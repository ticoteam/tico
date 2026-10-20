"""The run itself: gather answers, print the plan, then do each step and check it."""
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import socket
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import aws as awsmod, backup as bk, cloud as cloudmod, cloudinit, contract, dns as dnsmod, envfile, hosted, remote, route53, settings as st, signin, state, support, verify
from .cloudflare import DASHBOARD_STEPS, RUNNER_DASHBOARD_STEP, TOKEN_URL, Cloudflare, CloudflareError
from .settings import Asker, Settings
from .ui import IO, USAGE_NOTICE, MissingInput


@dataclass
class Deps:
    """Everything that touches the outside world, so tests replace it."""
    resolver: dnsmod.Resolver = field(default_factory=lambda: dnsmod.WireResolver("1.1.1.1"))
    public_resolvers: Callable[[], list] = dnsmod.default_resolvers
    cloudflare: Callable[[str], Cloudflare] = Cloudflare
    aws_clients: Callable[[str, str], tuple] = awsmod.make_clients
    aws_s3: Callable[[str, str], object] = awsmod.make_s3
    route53_client: Callable[[str], object] | None = None
    ssh_shell: Callable[..., remote.Shell] = remote.SSHShell
    local_shell: Callable[[], remote.Shell] = remote.LocalShell
    public_ip: Callable[[], str] = lambda: _public_ip()
    resolve_host: Callable[[str], str] = socket.gethostbyname
    sleep: Callable[[float], None] = time.sleep
    compose_local: Callable[[], bytes | None] = lambda: _local_compose()
    cloud_cli: Callable[[list[str]], cloudmod.Cli] = cloudmod.run_cli
    which: Callable[[str], str | None] = shutil.which
    latest_release: Callable[[], str] = lambda: _latest_release()


def _local_compose() -> bytes | None:
    p = Path(__file__).resolve().parent.parent / "compose.yaml"
    return p.read_bytes() if p.exists() else None


def _latest_release() -> str:
    try:
        with urllib.request.urlopen("https://api.github.com/repos/ticoteam/tico/releases/latest", timeout=15) as r:
            return str(json.loads(r.read()).get("tag_name", ""))
    except (OSError, ValueError):
        return ""


def _public_ip() -> str:
    """Only a suggestion the person confirms: behind NAT it is the router's address, not this server's."""
    try:
        with urllib.request.urlopen("https://api.ipify.org", timeout=10) as r:
            ip = r.read().decode().strip()
        return ip if re.fullmatch(r"(\d{1,3}\.){3}\d{1,3}", ip) else ""
    except OSError:
        return ""


def tunnel_id_from_token(token: str) -> str:
    """A tunnel token is base64 of {"a": account, "t": tunnel id, "s": secret}."""
    try:
        return json.loads(base64.b64decode(token + "=" * (-len(token) % 4)))["t"]
    except Exception:
        return ""


def host_of(ssh: str) -> str:
    return ssh.rsplit("@", 1)[-1]


# --- 1. answers ------------------------------------------------------------------------------------------

def gather(io: IO, args, s: Settings, dry: bool) -> Settings:
    a = Asker(io, args, s, dry)
    a.get("target", "Where should Tico run?", choices=st.TARGETS, default="aws")
    a.get("domain", "Domain to serve Tico at (for example tico.example.com)", validate=lambda v: bool(st.DOMAIN_RE.match(v.lower())))
    s.domain = s.domain.lower()
    a.get("front_door", "How should people reach it?", choices=st.FRONT_DOORS, default="caddy")
    if s.target == "ssh":
        a.get("ssh_host", "SSH destination (user@host)", flag="--ssh")
        s.ssh_port = int(args.ssh_port or s.ssh_port or 22)
        a.get("ssh_identity", "SSH key file (blank uses your agent / default keys)", required=False, flag="--ssh-identity")
    if s.target == "aws":
        a.get("aws_region", "AWS region", flag="--aws-region", default=os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1")
        a.get("aws_instance_type", "Instance size (t4g.small is enough for the server)", flag="--aws-instance-type",
              choices={k: k for k in awsmod.HOURLY}, default="t4g.small")
        a.get("aws_os", "Operating system", flag="--aws-os", choices={"ubuntu": "Ubuntu 24.04", "al2023": "Amazon Linux 2023"}, default="ubuntu")
        a.get("aws_profile", "AWS profile (blank for the default credentials)", flag="--aws-profile", required=False)
        s.aws_name = (getattr(args, "name", None) or s.aws_name or "").strip().lower()
        if s.aws_name and not awsmod.NAME_RE.fullmatch(s.aws_name):
            raise MissingInput("--name is 1 to 40 lowercase letters, digits and dashes, starting and ending with a letter or digit")
        s.aws_permissions_boundary = (getattr(args, "aws_permissions_boundary", None) or s.aws_permissions_boundary or "").strip()
        if s.aws_permissions_boundary and not awsmod.POLICY_ARN_RE.fullmatch(s.aws_permissions_boundary):
            raise MissingInput("--aws-permissions-boundary is an IAM policy ARN (arn:aws:iam::<account>:policy/<name>)")
    elif getattr(args, "name", None) or getattr(args, "aws_permissions_boundary", None):
        raise MissingInput("--name and --aws-permissions-boundary are for a server on AWS (--cloud aws)")
    if s.target in cloudmod.PROVIDERS:
        pv = cloudmod.PROVIDERS[s.target]
        a.get("cloud_location", f"{pv.label} location", flag="--cloud-location", choices=pv.locations, default=pv.default_location)
        a.get("cloud_size", "Server size (small and always on is enough)", flag="--cloud-size", choices=pv.sizes, default=pv.default_size)
        a.get("cloud_ssh_key", "SSH key name already in your account (blank: the account's only key, or none)", flag="--cloud-ssh-key", required=False)
    return s


def gather_identity(io: IO, args, s: Settings, dry: bool) -> None:
    a = Asker(io, args, s, dry)
    if s.auth != st.OWNER_LINK and not s.hosted:   # hosted.gather sets it for a server Tico hosts, never asked
        a.get("auth", "How should people sign in?", flag="--auth", choices=st.AUTHS, default="google")
    for flag in ("issuer", "signin_label"):
        if getattr(args, flag, None) and s.auth != "oidc":
            raise MissingInput(f"--{flag.replace('_', '-')} is for --auth oidc")
    if s.auth == st.OWNER_LINK:
        pass                            # the owner's sign-in link: no provider to set up
    elif s.auth == "cloudflare":
        if s.front_door != "cloudflared":
            raise MissingInput("Cloudflare Access needs --front-door cloudflared")
        a.get("access_issuer", "Access team URL (https://<team>.cloudflareaccess.com)")
        a.get("access_audience", "Access application audience tag")
    else:
        p = signin.provider(s.auth)
        uri = signin.redirect_uri(s.domain)
        need_client = not (getattr(args, "client_id", None) or s.client_id)
        if need_client and io.interactive and not dry:
            io.say(f"\nCreate a {p.label} OAuth client for Tico (about two minutes):")
            for i, step in enumerate(p.steps, 1):
                io.say(f"  {i}. {step}")
            io.say(f"\n  Redirect URI to paste, exactly:  {uri}\n")
            if p.console_url:
                io.open_url(p.console_url)
        elif not io.interactive or dry:
            io.say(f"Sign-in: create a {p.label} OAuth client" + (f" at {p.console_url}" if p.console_url else "")
                   + f"\n  with redirect URI exactly {uri}")
        if s.auth == "microsoft":
            a.get("tenant", "Directory (tenant) ID or domain")
        if s.auth == "oidc":
            a.get("issuer", "Issuer URL (https://..., the provider's OpenID Connect address)", validate=signin.valid_issuer)
            s.issuer = s.issuer.rstrip("/") if not s.issuer.startswith("<") else s.issuer
            a.get("signin_label", "Name on the sign-in button, \"Sign in with ...\" (blank: straight to the provider)",
                  required=False, flag="--signin-label", validate=lambda v: len(v) <= 40 and not any(c in v for c in "<>\n\r"))
        a.get("client_id", f"{p.label} client ID")
        a.get("client_secret", f"{p.label} client secret (hidden)", secret=True, env="TICO_OIDC_CLIENT_SECRET")
        a.get("allowed_domain", "Only allow sign-in from this email domain (blank for anyone on the roster)", required=False,
              flag="--allowed-domain")
    a.get("company", "Team/Company name", default="Sample Co" if s.preset else "Acme")
    a.get("owner_email", "Owner email (must be the account you will sign in with)", validate=lambda v: bool(st.EMAIL_RE.match(v)))
    a.get("decisions_provider", "Model key for the server's own decisions (openai, anthropic, gemini; blank to skip)", required=False,
          choices=None, flag="--decisions-provider")
    if s.decisions_provider:
        if s.decisions_provider not in contract.DECISIONS_KEYS:
            raise MissingInput("--decisions-provider must be openai, anthropic or gemini")
        a.get("decisions_key", f"{contract.DECISIONS_KEYS[s.decisions_provider]} (hidden)", secret=True, env=contract.DECISIONS_KEYS[s.decisions_provider])


def gather_runner_host(io: IO, args, s: Settings, zone: dnsmod.Zone | None, dry: bool) -> None:
    """A tunnel hostname that Access does not guard, so computers and outside agents connect with their tokens and no bypass.
    Asked for with Access in front; opt-in otherwise, since the domain itself already lets them in."""
    given = (getattr(args, "runner_host", None) or "").lower()
    if s.front_door != "cloudflared":
        if given and given != "none":
            raise MissingInput("--runner-hostname needs --front-door cloudflared: with Caddy, computers connect to the domain itself")
        s.runner_host = ""
        return
    if s.auth == "cloudflare" or given:
        default = "" if s.domain.startswith("<") else st.default_runner_host(s.domain, zone.name if zone else "")
        Asker(io, args, s, dry).get("runner_host", "Runner hostname, served without Access for computers and outside agents ('none' to skip)",
                                    flag="--runner-hostname", required=False, default=default,
                                    validate=lambda v: v.lower() == "none" or bool(st.DOMAIN_RE.match(v.lower())))
    s.runner_host = s.runner_host.lower()
    s.runner_given = bool(given) and given != "none"
    host = s.runner()
    if host == s.domain:
        raise MissingInput("--runner-hostname must differ from --domain")
    if host and zone and not host.endswith("." + zone.name) and host != zone.name:
        raise MissingInput(f"--runner-hostname must be in the domain's DNS zone, {zone.name}")


def gather_backup(io: IO, args, s: Settings, dry: bool) -> None:
    """Backups are never skipped silently: pick a bucket, or say out loud that copies stay on the server."""
    a = Asker(io, args, s, dry)
    default = "aws" if s.target == "aws" else "local"
    if s.backup_url and not s.backup:
        s.backup = "existing"
    a.get("backup", "Where should backups go? Tico copies its database and files there continuously.", choices=bk.CHOICES, default=default)
    if s.backup == "aws" and not s.aws_region:
        s.aws_region = os.environ.get("AWS_REGION") or "us-east-1"
    if s.backup == "r2" and not s.cf_token:
        if io.interactive and not dry:
            io.say(f"\nR2 needs a Cloudflare API token ({TOKEN_URL}), {bk.R2_TOKEN_HELP}.")
            s.cf_token = io.ask("Cloudflare API token (hidden)", secret=True)
        elif not dry:
            raise MissingInput("--backup r2 needs CLOUDFLARE_API_TOKEN in the environment")
    if s.backup == "existing":
        a.get("backup_url", "Bucket URL (s3://bucket/prefix)", validate=lambda v: v.startswith("s3://") and len(v) > 5)
        a.get("backup_endpoint", "S3-compatible endpoint (blank for AWS S3)", required=False)
        a.get("backup_region", "Region (blank for AWS default; auto for R2)", required=False)
        a.get("backup_key_id", "Access key id", env="LITESTREAM_ACCESS_KEY_ID")
        a.get("backup_secret", "Secret access key (hidden)", secret=True, env="LITESTREAM_SECRET_ACCESS_KEY")


def provision_backup(io: IO, s: Settings, deps: Deps, cf: Cloudflare | None) -> bool:
    """Creates the bucket and its credentials for `aws` and `r2`; False (after saying why) when that fails."""
    if s.backup not in ("aws", "r2") or s.backup_url:
        return True
    io.say("\nBackup storage")
    try:
        if s.backup == "aws":
            _, iam, _, account = deps.aws_clients(s.aws_region, s.aws_profile)
            values = bk.create_s3(deps.aws_s3(s.aws_region, s.aws_profile), iam, domain=s.domain, region=s.aws_region,
                                  account_id=account, name=s.aws_name or awsmod.slug(s.domain), say=io.say)
        else:
            account_id = s.cf_account_id
            if not account_id:
                zone = cf.find_zone(s.domain) if cf else None
                account_id = zone["account_id"] if zone else ""
            if not account_id:
                raise MissingInput("R2 needs the Cloudflare account id: pass --cf-account-id")
            values = bk.create_r2(cf, token=s.cf_token, account_id=account_id, domain=s.domain, say=io.say)
    except MissingInput:
        raise
    except Exception as e:
        io.say(f"\nCould not create the backup storage: {envfile.scrub(str(e), s.secrets())}\n"
               "Fix that and re-run `tico setup` (finished steps are skipped), or use `--backup local` to continue without off-server backups.\n"
               "On a server with no AWS credentials (the usual case), create the bucket from your laptop instead:\n"
               f"  python3 -m setup backup-storage --domain {s.domain} --aws-region {s.aws_region or '<region>'}\n"
               "then re-run here with the backup answer `existing`.")
        return False
    s.backup_url, s.backup_endpoint = values["TICO_BACKUP_URL"], values.get("TICO_BACKUP_ENDPOINT", "")
    s.backup_region, s.backup_key_id = values.get("TICO_BACKUP_REGION", ""), values["LITESTREAM_ACCESS_KEY_ID"]
    s.backup_secret = values["LITESTREAM_SECRET_ACCESS_KEY"]
    return True


# --- 2. plan ---------------------------------------------------------------------------------------------

def aws_plan(s: Settings) -> awsmod.AwsPlan:
    return awsmod.plan_aws(domain=s.domain, front_door=s.front_door, region=s.aws_region, instance_type=s.aws_instance_type,
                           os_family=s.aws_os, name=s.aws_name, boundary=s.aws_permissions_boundary)


def cloudname(s: Settings) -> str:
    return "tico-" + awsmod.slug(s.domain)


def plan_lines(s: Settings, zone: dnsmod.Zone | None, records: list[dnsmod.Record], cf_token: bool) -> list[str]:
    L = [f"Tico at https://{s.domain}: one server running the Tico server in Docker. Bots run on computers you add afterwards.", ""]
    n = 1

    def add(t: str) -> None:
        nonlocal n
        L.append(f"  {n}. {t}")
        n += 1

    if s.target == "aws":
        p = aws_plan(s)
        add("Create the AWS server:")
        L += [f"       - {x}" for x in p.lines()]
        L += [f"       ! {w}" for w in p.warnings]
    elif s.target in cloudmod.PROVIDERS:
        pv = cloudmod.PROVIDERS[s.target]
        add(f"Create the {pv.label} server with `{pv.cli}` (steps and the cloud-init file are printed instead if it is missing or signed out):")
        L += [f"       - {s.cloud_size} ({pv.sizes[s.cloud_size]}), Ubuntu 24.04, {s.cloud_location}" if s.cloud_size in pv.sizes
              else f"       - {s.cloud_size}, Ubuntu 24.04, {s.cloud_location}",
              f"       - cloud-init installs Docker, downloads compose.yaml for the pinned release, writes /opt/tico/.env (0600), "
              f"starts the stack; ufw closes everything but " + ("80/443 (and SSH when a key is used)" if s.front_door == "caddy" else "SSH (when a key is used)"),
              f"       - {pv.label} firewall {cloudname(s)}: inbound " + ("80, 443 (and 22 with an SSH key)" if s.front_door == "caddy" else "none (22 with an SSH key)"),
              *(["       - A public IP you keep across rebuilds (" + ("Primary IP" if pv.key == "hetzner" else "Reserved IP") + ")"] if s.front_door == "caddy" else []),
              f"       - Labelled ManagedBy=tico-setup, tico-setup-name={cloudname(s)}",
              f"       ! {pv.note}",
              "       ! The sign-in secret sits in the provider's user-data (readable from the server's own metadata service, blocked for containers) "
              "for as long as the server exists."]
    elif s.target == "local":
        add(f"On this server: install Docker if missing, write {contract.REMOTE_DIR}/compose.yaml and "
            f"{contract.REMOTE_DIR}/.env (0600), `docker compose up -d`")
    elif s.target == "ssh":
        add(f"Over SSH to {s.ssh_host}: install Docker if missing, write {contract.REMOTE_DIR}/compose.yaml and "
            f"{contract.REMOTE_DIR}/.env (0600), `docker compose up -d`")
    else:
        add("Write the .env here (0600) and one paste-able install command; you run it on the server")
    if s.front_door == "cloudflared":
        add("Cloudflare tunnel: " + ("create the tunnel, its hostname route and DNS record through the API" if cf_token
                                     else "you create it in the dashboard (exact steps shown) and paste the token"))
        if s.runner():
            L.append(f"       - runner hostname {s.runner()}: /api/v2 and /download only, without Access (TICO_RUNNER_URL)")
    if zone:
        add(f"DNS: {s.domain} is served by {zone.provider.name} ({', '.join(zone.nameservers[:2])}{', ...' if len(zone.nameservers) > 2 else ''})")
    else:
        add("DNS: could not find the domain's nameservers; check the domain is registered and delegated")
    for r in records:
        L.append(f"       {r.type} {r.name} -> {r.value}" + (" (proxied)" if r.proxied else ""))
    add("Wait until public resolvers (8.8.8.8, 1.1.1.1, 9.9.9.9) answer with those records, before anything asks for a certificate")
    if s.auth == st.OWNER_LINK:
        for line in hosted.plan_lines(s):
            add(line)
    elif s.auth == "cloudflare":
        add("Sign-in: Cloudflare Access (issuer and audience you gave)")
    else:
        add(f"Sign-in: {signin.provider(s.auth).label} OIDC" + (f" at {s.issuer}" if s.auth == "oidc" else "")
            + (f", button \"Sign in with {s.signin_label}\"" if s.auth == "oidc" and s.signin_label else "")
            + f"; OAuth client redirect URI must be exactly {signin.redirect_uri(s.domain)}")
        if s.hosted:
            for line in hosted.plan_lines(s):
                add(line)
    if s.backup == "aws":
        add("Backups: create the backup storage on AWS")
        L += [f"       - {x}" for x in bk.aws_plan_lines(s.domain, s.aws_region)]
    elif s.backup == "r2":
        add("Backups: create the backup storage on Cloudflare R2")
        L += [f"       - {x}" for x in bk.r2_plan_lines(s.domain)]
    elif s.backup == "existing":
        add(f"Backups: continuously to {s.backup_url}" + (f" at {s.backup_endpoint}" if s.backup_endpoint else ""))
    else:
        add("Backups: local only")
        L.append(f"       ! {bk.LOCAL_WARNING}")
    add(f"Team {s.company!r}, owner {s.owner_email}" + (f", decisions key {contract.DECISIONS_KEYS[s.decisions_provider]} (server only)" if s.decisions_provider else ""))
    add("Verify: HTTPS certificate, /healthz, sign-in redirect, server container up" + (", runner hostname" if s.runner() else ""))
    return L


# --- 3. DNS ----------------------------------------------------------------------------------------------

def ensure_dns(io: IO, s: Settings, deps: Deps, zone: dnsmod.Zone | None, records: list[dnsmod.Record], *,
               timeout: float, skip_wait: bool, cf: Cloudflare | None) -> bool:
    """Creates the records where it can, prints them where it cannot, then waits for public resolvers."""
    handled = False
    if zone and zone.provider.key == "cloudflare" and cf:
        try:
            z = cf.find_zone(s.domain)
            if z:
                for r in records:
                    if r.type == "CNAME":
                        res = cf.upsert_cname(z["id"], r.name, r.value, repoint=r.name != s.runner() or s.runner_given)
                    else:
                        res = cf.upsert_address(z["id"], r.type, r.name, r.value)
                    io.say(f"  Cloudflare DNS {r.type} {r.name}: {res}")
                handled = True
        except CloudflareError as e:
            io.say(f"  Could not write the Cloudflare record ({e}). Add it by hand instead.")
    elif zone and zone.provider.key == "route53":
        handled = _route53(io, s, deps, zone, records)
    if not handled and zone:
        if s.front_door == "cloudflared" and zone.provider.key == "cloudflare":
            io.say("  Adding the tunnel's Public Hostname in the dashboard creates the DNS record for you.")
        else:
            if s.front_door == "cloudflared":
                io.say("  Note: a tunnel needs the domain's DNS on Cloudflare (or a CNAME-setup plan). "
                       f"{zone.provider.name} serves it today.")
            for line in dnsmod.manual_instructions(records, zone):
                io.say("  " + line)
    elif not zone:
        io.say("  Could not find nameservers for the domain. Add these records at the provider that serves it:")
        for r in records:
            io.say(f"    {r.type} {r.name} {r.value}")
    if skip_wait:
        return True
    io.say("  Waiting for public DNS ...")
    return dnsmod.wait_until_live(s.domain, records, deps.public_resolvers(), timeout=timeout, say=io.say, sleep=deps.sleep)


def _route53(io: IO, s: Settings, deps: Deps, zone: dnsmod.Zone, records) -> bool:
    try:
        client = deps.route53_client(s.aws_profile) if deps.route53_client else _r53_client(s.aws_profile)
    except Exception:
        io.say("  Route 53 serves this domain but no AWS credentials are available here; add the records by hand.")
        return False
    try:
        zid, status = route53.find_zone(client, zone)
    except Exception as e:
        io.say(f"  Route 53 lookup failed ({type(e).__name__}); add the records by hand.")
        return False
    if status == "not-authoritative":
        io.say(f"  A Route 53 zone named {zone.name} exists in this account, but the internet is not using it (its nameservers "
               "differ from the domain's). Records there would have no effect; add them at the provider above.")
        return False
    if status == "missing":
        io.say("  This AWS account has no hosted zone for the domain; add the records by hand.")
        return False
    if not io.confirm(f"  Create the record in Route 53 zone {zone.name}?", True):
        return False
    route53.upsert(client, zid, records)
    io.say("  Route 53 records upserted.")
    return True


def _r53_client(profile: str):
    import boto3
    c = boto3.Session(profile_name=profile or None).client("route53")
    c.list_hosted_zones(MaxItems="1")
    return c


# --- 4. the run --------------------------------------------------------------------------------------------

def run(args, io: IO, deps: Deps) -> int:
    dry = args.dry_run
    domain_hint = (args.domain or "").lower()
    saved, saved_env = state.load(domain_hint) if domain_hint else ({}, "")
    s = st.from_saved(saved, saved_env) if saved else Settings()
    s.cf_token = os.environ.get("CLOUDFLARE_API_TOKEN", "")
    s.tunnel_token = os.environ.get("CLOUDFLARE_TUNNEL_TOKEN", "") or s.tunnel_token
    io.say("tico setup" + (" (dry run: nothing will be created or changed)" if dry else ""))
    io.say(USAGE_NOTICE + " Details: PRIVACY.md in the Tico repository.")
    if saved:
        io.say(f"Resuming from the last run for {domain_hint} (secrets kept in {state.home() / domain_hint}).")
    gather(io, args, s, dry)
    hosted.gather(args, s, dry)
    gather_identity(io, args, s, dry)
    gather_backup(io, args, s, dry)
    s.updater = not args.no_updater
    s.compose_ref = args.compose_ref

    io.say("\nLooking up who serves DNS for the domain ...")
    zone = dnsmod.detect_zone(s.domain, deps.resolver)
    if zone:
        io.say(f"  {zone.name}: {zone.provider.name}  ({', '.join(zone.nameservers)})")
    gather_runner_host(io, args, s, zone, dry)
    if s.target == "ssh" and s.front_door == "caddy":
        default_ip = ""
        try:
            default_ip = deps.resolve_host(host_of(s.ssh_host)) if s.ssh_host and not s.ssh_host.startswith("<") else ""
        except OSError:
            pass
        Asker(io, args, s, dry).get("server_ip", "Server's public IPv4 address (what the domain should point at)", flag="--server-ip", default=default_ip)
    if s.target == "command" and s.front_door == "caddy":
        Asker(io, args, s, dry).get("server_ip", "Server's public IPv4 address (what the domain should point at)", flag="--server-ip")
    if args.tico_version and s.target not in cloudmod.PROVIDERS:
        # local and AWS pin the images and fetch compose.yaml from the release, never `latest` or main
        s.tag = s.compose_ref = args.tico_version
    if s.target == "local":
        if s.front_door == "caddy":
            Asker(io, args, s, dry).get("server_ip", "This server's public IPv4 address (what the domain should point at)", flag="--server-ip",
                                        default="" if dry else deps.public_ip())
    if s.front_door == "cloudflared" and not s.cf_token and zone and zone.provider.key == "cloudflare" and io.interactive and not dry:
        io.say(f"\nA scoped Cloudflare API token lets me create the tunnel and DNS for you ({TOKEN_URL}):\n"
               "  permissions: Account > Cloudflare Tunnel > Edit, Zone > DNS > Edit (this zone only).")
        s.cf_token = io.ask("Cloudflare API token (hidden; blank to use the dashboard instead)", secret=True)

    tunnel_id = s.tunnel_id or tunnel_id_from_token(s.tunnel_token)
    ip = "<elastic-ip>" if s.target == "aws" else "<reserved-ip>" if s.target in cloudmod.PROVIDERS else s.server_ip
    records = dnsmod.plan_records(s.domain, s.front_door, ipv4=ip if s.front_door == "caddy" else "", tunnel_id=tunnel_id,
                                  runner_host=s.runner())

    io.say("\nPlan")
    for line in plan_lines(s, zone, records, bool(s.cf_token)):
        io.say(line)
    if dry:
        if s.backup in ("aws", "r2") and not s.backup_url:
            s.backup_url = "<bucket created when you run setup>"
        io.say("\nWhat would be written to .env (secrets masked):")
        io.say(envfile.redact_env(envfile.render(s.to_env())).rstrip())
        io.say("\nDry run: nothing was created, changed or saved.")
        return 0
    if not args.yes and not io.confirm("\nGo ahead?", True):
        io.say("Stopped; nothing was changed.")
        return 1

    cf = deps.cloudflare(s.cf_token) if s.cf_token else None
    if cf and s.runner() and not s.runner_given:
        runner_record_free(s, cf)
    if not provision_backup(io, s, deps, cf):
        return 1
    if s.front_door == "cloudflared":
        tunnel_id = _tunnel(io, args, s, deps, zone, cf) or tunnel_id
        records = dnsmod.plan_records(s.domain, s.front_door, tunnel_id=tunnel_id, runner_host=s.runner())

    if s.hosted and not s.owner_link_hash:
        # The first link (break-glass beside --auth oidc), made here; a re-run keeps it (the server only takes one when it
        # has none).
        s.owner_link_hash = hosted.new_link(s)
    env_text = envfile.render(s.to_env())
    state.save(s.domain, s.public(), env_text)
    scrub = lambda t: envfile.scrub(t, s.secrets())
    compose = deps.compose_local()
    url = contract.compose_url(s.compose_ref)
    runner: remote.Shell | None = None
    live = True
    timeout = args.dns_timeout * 60

    if s.target in ("ssh", "local"):
        if s.front_door == "caddy":
            io.say("\nDNS")
            live = ensure_dns(io, s, deps, zone, records, timeout=timeout, skip_wait=args.skip_dns_wait, cf=cf)
            if not live:
                io.say("Not starting Tico yet: Caddy would request its certificate before DNS is live. Re-run `tico setup` "
                       "when the records resolve; everything so far is kept.")
                return 2
        runner = deps.local_shell() if s.target == "local" else deps.ssh_shell(s.ssh_host, s.ssh_port, s.ssh_identity)
        io.say("\nServer")
        for step in remote.ssh_steps(runner, env_text=env_text, compose=compose, compose_url=url, say=io.say, scrub=scrub):
            io.say(f"  - {step.description}")
            try:
                step.run()
            except remote.StepFailed as e:
                io.say(f"\nFailed: {e}\nRe-run `tico setup` after fixing it; finished steps are skipped.")
                offer_support(io, args, s, f"tico setup ({s.target})", error=f"{step.description}: {e}")
                return 1
        if s.front_door == "cloudflared":
            io.say("\nDNS")
            live = ensure_dns(io, s, deps, zone, records, timeout=timeout, skip_wait=args.skip_dns_wait, cf=cf)
    elif s.target == "aws":
        runner = _aws(io, args, s, deps, zone, records, cf, env_text, compose, url, timeout)
        if runner is None:
            return 1
    elif s.target in cloudmod.PROVIDERS:
        res = _cloud(io, args, s, deps, zone, cf, env_text, timeout)
        if isinstance(res, int):
            return res
        runner, records = res
    else:
        return _command(io, args, s, deps, zone, records, cf, env_text, compose, url, timeout)

    return finish(io, s, deps, records, runner, args)


def runner_record_free(s: Settings, cf: Cloudflare) -> None:
    """The default runner hostname must not take over a record that serves something else. Checked before anything is
    created or written, so TICO_RUNNER_URL never names a host that is not this tunnel's; --runner-hostname may repoint."""
    z = cf.find_zone(s.domain)
    rec = cf.find_record(z["id"], s.runner()) if z else None
    if not rec:
        return
    tid = cf.find_tunnel(z["account_id"], f"tico-{awsmod.slug(s.domain)}")
    if rec["type"] != "CNAME" or not tid or rec["content"] != f"{tid}.cfargotunnel.com":
        raise MissingInput(f"{s.runner()}, the default runner hostname, already has a {rec['type']} record pointing at {rec['content']}. "
                           f"Setup does not take it over: pass --runner-hostname {s.runner()} to repoint it to this tunnel, "
                           "--runner-hostname <another name>, or --runner-hostname none.")


def _tunnel(io: IO, args, s: Settings, deps: Deps, zone, cf: Cloudflare | None) -> str:
    if cf:
        io.say("\nCloudflare tunnel")
        z = cf.find_zone(s.domain)
        if not z:
            raise SystemExit("That Cloudflare token cannot see a zone for this domain (needs Zone > DNS > Edit on it).")
        tid, token = cf.ensure_tunnel(z["account_id"], f"tico-{awsmod.slug(s.domain)}")
        cf.configure_tunnel(z["account_id"], tid, s.domain, s.runner())
        s.tunnel_token, s.tunnel_id = token, tid
        io.say(f"  tunnel {tid} ready, routing {s.domain} to http://server:8765"
               + (f", and {s.runner()} for /api/v2 and /download" if s.runner() else ""))
        return tid
    if not s.tunnel_token:
        io.say("\nCloudflare tunnel (dashboard steps)")
        steps = DASHBOARD_STEPS + ([RUNNER_DASHBOARD_STEP] if s.runner() else [])
        for i, t in enumerate(steps, 1):
            io.say(f"  {i}. {t.format(domain=s.domain, runner=s.runner(), path=contract.RUNNER_ROUTE_PATH)}")
        Asker(io, args, s, False).get("tunnel_token", "Tunnel token (hidden)", secret=True, env="CLOUDFLARE_TUNNEL_TOKEN")
    s.tunnel_id = tunnel_id_from_token(s.tunnel_token)
    return s.tunnel_id


def _aws(io, args, s, deps, zone, records, cf, env_text, compose, url, timeout) -> remote.Shell | None:
    plan = aws_plan(s)
    io.say("\nAWS")
    ec2, iam, ssm, account = deps.aws_clients(s.aws_region, s.aws_profile)
    d = awsmod.Deployer(plan, ec2, iam, ssm, account, say=io.say, sleep=deps.sleep)
    d.put_env(env_text)
    prep = d.prepare()
    ip = prep["public_ip"]
    if s.front_door == "caddy":
        s.server_ip = ip
        records = dnsmod.plan_records(s.domain, "caddy", ipv4=ip)
        io.say(f"\nDNS (Elastic IP {ip})")
        ensure_dns(io, s, deps, zone, records, timeout=0, skip_wait=True, cf=cf)
    script = remote.bootstrap_script(os_family=s.aws_os, domain=s.domain, front_door=s.front_door, compose=compose, compose_url=url,
                                     env_param=(d.param_name, s.aws_region), wait_for_ip=ip)
    iid, created = d.launch_or_reuse(prep, script)
    s.aws_instance_id = iid
    state.save(s.domain, {**s.public(), "aws_name": plan.name}, env_text)
    runner = remote.SSMShell(ssm, iid, deps.sleep)
    if s.front_door == "caddy" and not args.skip_dns_wait:
        io.say("\nWaiting for public DNS (the server holds back Caddy until this is true) ...")
        if not dnsmod.wait_until_live(s.domain, records, deps.public_resolvers(), timeout=timeout, say=io.say, sleep=deps.sleep):
            io.say("The server keeps waiting for DNS for about 20 minutes after boot; if you fix DNS later, run "
                   "`tico setup doctor` (or re-run `tico setup`).")
    elif s.front_door == "cloudflared":
        io.say("\nDNS")
        ensure_dns(io, s, deps, zone, records, timeout=timeout, skip_wait=args.skip_dns_wait, cf=cf)
    io.say("\nWaiting for the server to finish installing (Docker, image pull; a few minutes) ...")
    if not d.wait_ready():
        io.say("The server did not report ready in time. Log: /var/log/tico-setup.log (via SSM Session Manager).")
        return None
    if not created:
        io.say("  Existing server: refreshing .env and restarting.")
        res = runner.run(f"{contract.REMOTE_DIR}/pull-env.sh && cd {contract.REMOTE_DIR} && docker compose up -d")
        if res.returncode:
            io.say(envfile.scrub(res.stderr[-400:], s.secrets()))
            return None
    return runner


def _cloud(io: IO, args, s: Settings, deps: Deps, zone, cf, env_text: str, timeout: float):
    """Hetzner / DigitalOcean. Returns an exit code to stop with, or (shell, records) to continue to the checks."""
    pv = cloudmod.PROVIDERS[s.target]
    name = cloudname(s)
    version = args.tico_version or s.cloud_version or deps.latest_release()
    if not cloudinit.VERSION_RE.match(version or ""):
        raise MissingInput("Could not find the latest Tico release; pass --tico-version vX.Y.Z (see github.com/ticoteam/tico/releases)")
    s.cloud_version = version
    env = s.to_env()
    io.say(f"\n{pv.label}")
    say = io.say

    def manual(why: str) -> int:
        out = state.home() / s.domain / "user-data.yaml"
        state.write_private(out, cloudinit.server_user_data(env, version=version, allow_ssh=True))
        io.say(why)
        io.say(f"\nThe cloud-init file holds your secrets, so it is not printed: {out} (mode 0600). Delete it once the server is up.")
        for line in cloudmod.manual_steps(pv, name=name, size=s.cloud_size, location=s.cloud_location, front_door=s.front_door,
                                          user_data_path=str(out)):
            io.say("  " + line)
        io.say(f"  Then add the DNS record: {'A' if s.front_door == 'caddy' else 'the tunnel record'} {s.domain} -> the server's IPv4 address "
               "(the server waits up to 30 minutes for it before starting Caddy).")
        io.say(f"  Check it later:  python3 -m setup doctor --domain {s.domain}")
        return 0

    if not deps.which(pv.cli):
        return manual(f"{pv.cli} is not installed ({pv.install_url}), so here are the steps to do it yourself.")
    c = cloudmod.Cloud(pv, name, s.cloud_location, s.cloud_size, s.front_door, ssh_key=s.cloud_ssh_key, cli=deps.cloud_cli, say=say,
                       secrets=s.secrets())
    try:
        c.check_auth()
    except cloudmod.CloudError as e:
        manual(str(e))
        return 2
    keys = c.ssh_keys()
    if not keys:
        io.say("  No SSH key found in the account, so the server gets no SSH access (use the provider's console, or add a key and pass --cloud-ssh-key).")
    if s.front_door == "cloudflared":
        tunnel_id = s.tunnel_id or tunnel_id_from_token(s.tunnel_token)
        records = dnsmod.plan_records(s.domain, "cloudflared", tunnel_id=tunnel_id, runner_host=s.runner())
    try:
        made = c.create(lambda ip: cloudinit.server_user_data(env, version=version, server_ip=ip, allow_ssh=bool(keys)), bool(keys), keys)
    except cloudmod.CloudError as e:
        io.say(f"\nFailed: {e}\nRe-run `tico setup` after fixing it; what already exists is reused.")
        return 1
    s.server_ip = made.ip if s.front_door == "caddy" else ""
    state.save(s.domain, {**s.public(), "cloud_name": name}, env_text)
    if s.front_door == "caddy":
        records = dnsmod.plan_records(s.domain, "caddy", ipv4=made.ip)
        io.say(f"\nDNS (server IP {made.ip})")
    else:
        io.say("\nDNS")
    ok = ensure_dns(io, s, deps, zone, records, timeout=timeout, skip_wait=args.skip_dns_wait, cf=cf)
    if not ok:
        io.say("The server keeps waiting for DNS for up to 30 minutes after boot; when the record resolves it starts by itself. "
               f"Then run `python3 -m setup doctor --domain {s.domain}`.")
        return 2
    io.say("\nWaiting for the server to finish installing (Docker, image pull; a few minutes) ...")
    for _ in range(90):
        if verify.check_health(s.domain).ok:
            break
        deps.sleep(10)
    else:
        io.say(f"The server did not answer in time. Its log is /var/log/tico-setup.log on the server (ssh in, or use the provider's console).")
    return None, records


def _command(io, args, s, deps, zone, records, cf, env_text, compose, url, timeout) -> int:
    io.say("\nDNS")
    ensure_dns(io, s, deps, zone, records, timeout=timeout, skip_wait=args.skip_dns_wait or s.front_door == "cloudflared", cf=cf)
    script = remote.bootstrap_script(os_family="ubuntu", domain=s.domain, front_door=s.front_door, compose=compose, compose_url=url,
                                     env_b64=remote.env_b64(env_text), wait_for_ip=s.server_ip)
    out = state.home() / s.domain / "install-command.txt"
    state.write_private(out, remote.paste_command(script) + "\n")
    io.say(f"\nThe install command holds your secrets, so it is not printed. It is one line in {out} (mode 0600).")
    io.say(f"  Copy it:  pbcopy < {out}   (macOS)   or   xclip -selection clipboard < {out}   (Linux)")
    io.say("  Paste it in a shell on the server (Debian or Ubuntu, with sudo). It installs Docker, writes the files, "
           "waits for DNS, and starts Tico.")
    io.say(f"  Delete it afterwards:  rm {out}")
    io.say(f"  Then check from here:  python3 -m setup doctor --domain {s.domain}")
    if not s.backup_url:
        io.say(f"\nNote: {bk.LOCAL_WARNING}")
    return 0


# Caddy asks Let's Encrypt only once the stack is up, so the first checks can beat it by a minute or two.
HTTPS_WAIT_SECONDS = 180


def finish(io: IO, s: Settings, deps: Deps, records, runner: remote.Shell | None, args=None) -> int:
    io.say("\nChecking it works")
    checks = verify.run_all(domain=s.domain, provider=s.auth, client_id=s.client_id, issuer=s.issuer, front_door=s.front_door,
                            records=records, resolvers=deps.public_resolvers(), shell=runner, runner_host=s.runner(),
                            wait_https=HTTPS_WAIT_SECONDS, sleep=deps.sleep, say=io.say)
    show(io, checks)
    if all(c.ok for c in checks):
        computer = s.preset == "recommended" and hosted.add_server_computer(io, s, runner)
        if s.auth == st.OWNER_LINK:
            io.say(f"\nDone. The owner's sign-in link is in {hosted.link_file(s.domain)} (0600): send it to {s.owner_email}. "
                   "It works once, for a week; `python3 -m setup owner-link --domain " + s.domain + "` makes a new one.")
        elif s.hosted:
            io.say(f"\nDone. Open https://{s.domain} and press \"Sign in with {s.signin_label or 'your provider'}\" as "
                   f"{s.owner_email}, the owner.")
            io.say(f"Break-glass, for our team only (not for the ready email): the owner's sign-in link is in "
                   f"{hosted.link_file(s.domain)} (0600), for when the issuer is down.")
        else:
            io.say(f"\nDone. Open https://{s.domain} and sign in as {s.owner_email}.")
        if not s.backup_url:
            io.say(f"\nNote: {bk.LOCAL_WARNING}")
        if computer:
            io.say("The one step left is in the app: Settings > Computers > Server computer > Sign in, to Claude and to ChatGPT.")
        else:
            io.say("The in-app checklist continues from there. Next, add the computer that runs your bots: your Mac via "
                   "Settings > Computers > Add computer, or a Linux box with `python3 -m setup runner`.")
        return 0
    io.say("\nNot everything passed. Fix the hints above and run `python3 -m setup doctor --domain " + s.domain + "`.")
    offer_support(io, args, s, f"tico setup ({s.target})", checks)
    return 1


def offer_support(io: IO, args, s: Settings, what: str, checks=(), error: str = "") -> None:
    """Offer to send the failure to Tico support (support.py), with this run's domain, names and secrets kept out."""
    support.offer(io, what, checks, error, domains=support.domains_of(s.domain, s.owner_email, s.allowed_domain, s.runner(), s.ssh_host),
                  secrets=s.secrets(), names=[s.company], version=support.bundle_version(getattr(args, "tico_version", "") or s.tag),
                  args=args)


def show(io: IO, checks: list[verify.Check]) -> None:
    for c in checks:
        io.say(f"  [{'ok' if c.ok else 'FAIL'}] {c.name}: {c.detail}")
        if not c.ok and c.hint:
            io.say(f"         fix: {c.hint}")


def doctor(args, io: IO, deps: Deps) -> int:
    domain = (args.domain or "").lower()
    if not domain:
        known = state.known_domains()
        if len(known) != 1:
            raise MissingInput("Pass --domain" + (f" (known: {', '.join(known)})" if known else ""))
        domain = known[0]
    saved, env_text = state.load(domain)
    if not saved:
        raise MissingInput(f"No saved setup for {domain}; run `tico setup` first.")
    s = st.from_saved(saved, env_text)
    tunnel_id = s.tunnel_id or tunnel_id_from_token(s.tunnel_token)
    records = dnsmod.plan_records(domain, s.front_door, ipv4=s.server_ip, tunnel_id=tunnel_id, runner_host=s.runner())
    runner = None
    if s.target == "ssh":
        runner = deps.ssh_shell(s.ssh_host, s.ssh_port, s.ssh_identity)
    elif s.target == "aws" and s.aws_instance_id:
        _, _, ssm, _ = deps.aws_clients(s.aws_region, s.aws_profile)
        runner = remote.SSMShell(ssm, s.aws_instance_id, deps.sleep)
    io.say(f"tico setup doctor for {domain}")
    checks = verify.run_all(domain=domain, provider=s.auth, client_id=s.client_id, issuer=s.issuer, front_door=s.front_door,
                            records=records, resolvers=deps.public_resolvers(), shell=runner, runner_host=s.runner())
    show(io, checks)
    ok = all(c.ok for c in checks)
    if not ok:
        offer_support(io, args, s, "tico setup doctor", checks)
    if args.runner:
        from . import runner_setup
        ok = runner_setup.doctor_runners(io, deps, f"https://{domain}") and ok
    return 0 if ok else 1


def _destroy_hint(io: IO, saved: dict) -> int:
    """Not automated: deleting a server also deletes its data, so the commands are shown for the user to run."""
    n = saved.get("cloud_name") or "tico-" + awsmod.slug(saved.get("domain", ""))
    io.say(f"`tico setup destroy` does not delete {cloudmod.PROVIDERS[saved['target']].label} resources. Deleting the server deletes "
           "the tico-data volume with it: back it up first. To remove what setup created:")
    if saved["target"] == "hetzner":
        cmds = [f"hcloud server delete {n}", f"hcloud firewall delete {n}", f"hcloud primary-ip delete {n}"]
    else:
        cmds = [f"doctl compute droplet delete {n} --tag-name {n}   # asks first", f"doctl compute firewall delete <id from: doctl compute firewall list>",
                "doctl compute reserved-ip delete <ip from: doctl compute reserved-ip list>"]
    for c in cmds:
        io.say("  " + c)
    return 0


def destroy(args, io: IO, deps: Deps) -> int:
    saved, _ = state.load((args.domain or "").lower()) if args.domain else ({}, "")
    if saved.get("target") in cloudmod.PROVIDERS:
        return _destroy_hint(io, saved)
    name = args.name or saved.get("aws_name") or (awsmod.slug(args.domain) if args.domain else "")
    if not name:
        raise MissingInput("Pass --domain or --name")
    region = args.aws_region or saved.get("aws_region") or os.environ.get("AWS_REGION") or "us-east-1"
    ec2, iam, ssm, _ = deps.aws_clients(region, args.aws_profile or saved.get("aws_profile", ""))
    found = awsmod.discover(ec2, iam, ssm, name)
    if not found:
        io.say(f"Nothing tagged {awsmod.MANAGED_TAG[0]}={awsmod.MANAGED_TAG[1]}, {awsmod.NAME_TAG}={name} in {region}.")
        return 0
    io.say(f"Tagged {awsmod.NAME_TAG}={name} in {region}:")
    for kind, ids in found.items():
        for i in ids:
            io.say(f"  {kind}: {i}")
    records = None
    if getattr(args, "dns", False):
        records = _dns_to_delete(io, args, saved, deps, awsmod.addresses(ec2, name))
        if records is None:
            io.say("Nothing was deleted: run it again, or without --dns and remove the DNS record yourself.")
            return 1
    io.say("The server's data (the tico-data volume) is deleted with the instance. Back it up first if you need it.")
    io.say(f"The backup bucket and its IAM user (tico-backup-{name}) are left alone: they hold the copy you would restore from.")
    if args.dry_run:
        io.say("Dry run: nothing was deleted.")
        return 0
    if not args.yes and not (io.interactive and io.ask(f"Type {name} to delete these") == name):
        io.say("Not confirmed; nothing was deleted.")
        return 1
    if records and records[2]:
        client, zid, sets = records
        route53.delete(client, zid, sets)       # first: the name never points at an address AWS may give someone else
        io.say("  DNS records deleted.")
    awsmod.destroy(ec2, iam, ssm, found, say=io.say, sleep=deps.sleep, dns_done=records is not None)
    return 0


def _dns_to_delete(io: IO, args, saved: dict, deps: Deps, ours: set[str]):
    """destroy --dns: (client, zone id, the record sets to delete) for the domain's A and AAAA records that still point at
    this server; None when Route 53 can't be read, so nothing at all is deleted and a re-run tries again."""
    domain = (args.domain or "").lower()
    if not domain:
        raise MissingInput("--dns needs --domain: the name whose record points at the server")
    zone = dnsmod.detect_zone(domain, deps.resolver)
    if not zone or zone.provider.key != "route53":
        io.say(f"--dns deletes records in Route 53, and {domain} is not served from there "
               f"({zone.provider.name if zone else 'no nameservers found'}).")
        return None
    profile = args.aws_profile or saved.get("aws_profile", "")
    try:
        client = deps.route53_client(profile) if deps.route53_client else _r53_client(profile)
        zid, status = route53.find_zone(client, zone)
        if status != "authoritative":
            io.say(f"No Route 53 zone in this account serves {zone.name}.")
            return None
        sets, other = route53.pointing_at(client, zid, domain, ours)
    except Exception as e:
        io.say(f"Could not read Route 53 ({type(e).__name__}).")
        return None
    for rs in sets:
        io.say(f"  dns: {rs['Type']} {domain} -> {', '.join(v['Value'] for v in rs['ResourceRecords'])}")
    for rs in other:
        io.say(f"  Left alone: {rs['Type']} {domain} no longer points only at this server.")
    if not sets and not other:
        io.say(f"  No A or AAAA record for {domain} in Route 53.")
    return client, zid, sets
