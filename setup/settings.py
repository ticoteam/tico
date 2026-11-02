"""The answers, and where each comes from: flag, environment (secrets), the last run, then a prompt."""
from __future__ import annotations

import hashlib
import ipaddress
import os
import re
import urllib.parse
from dataclasses import asdict, dataclass, field, fields

from . import contract, envfile
from .ui import IO, MissingInput

DOMAIN_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

TARGETS = {"local": "this server (you are running this on it; what scripts/install.sh uses)",
           "ssh": "a server I already have (any Linux box I can SSH into)",
           "aws": "create one on AWS (EC2, Docker and Tico installed for me)",
           "hetzner": "create one on Hetzner Cloud (needs the hcloud CLI signed in; otherwise prints the steps)",
           "digitalocean": "create one on DigitalOcean (needs the doctl CLI signed in; otherwise prints the steps)",
           "command": "print a one-line install command to paste on any server"}
FRONT_DOORS = {"caddy": "Caddy: automatic HTTPS, opens ports 80/443, DNS points at the server",
               "cloudflared": "Cloudflare Tunnel: no open ports, needs the domain on Cloudflare"}
AUTHS = {"google": "Google sign-in (OIDC)", "microsoft": "Microsoft sign-in (OIDC)",
         "cloudflare": "Cloudflare Access (needs the tunnel)",
         "oidc": "Another OpenID Connect provider (its issuer URL, client ID and secret)"}
# Only for a server Tico hosts (--hosted), so never offered in the question above: the owner signs in with a single-use
# link our team sends (backend/owner_link.py).
OWNER_LINK = "owner-link"
HOSTED = ("own", "invited")
PRESETS = ("recommended",)
# The Gateway invite our operator gives the preset (Dev 210 enrolls with it); checked whole, never shown.
INVITE_RE = re.compile(r"[A-Za-z0-9_-]{8,200}")

# Not persisted to state.json: they live in the private .env copy or come from the environment.
SECRET_FIELDS = ("client_secret", "decisions_key", "cf_token", "tunnel_token", "backup_secret", "gateway_invite",
                 "inbound_key", "inbound_url", "visual_secret", "company_file_data")
# This run's only, never saved: a re-run without the flag must not inherit permission to repoint a record.
TRANSIENT_FIELDS = ("runner_given",)
# Only these named settings migrate from the private env; never copy ambient or arbitrary keys.
RUNTIME_ENV = {
    "domain_aliases": "TICO_DOMAIN_ALIASES", "public_url": "TICO_PUBLIC_URL", "cors_origins": "TICO_CORS_ORIGINS",
    "hq_url": "TICO_HQ_URL", "visual_origin": "TICO_VISUAL_ORIGIN", "visual_same_origin": "TICO_VISUAL_SAME_ORIGIN",
    "caddy_visuals": "TICO_CADDY_VISUALS", "runner_url": "TICO_RUNNER_URL", "gateway_url": "TICO_GATEWAY_URL",
    "trusted_proxies": "TICO_TRUSTED_PROXIES", "client_ip_header": "TICO_CLIENT_IP_HEADER",
    "edge_subnet": "TICO_EDGE_SUBNET", "edge_dynamic_range": "TICO_EDGE_DYNAMIC_RANGE",
    "caddy_address": "TICO_CADDY_ADDRESS", "cloudflared_address": "TICO_CLOUDFLARED_ADDRESS",
    "reader_origin": "TICO_READER_ORIGIN",
}
RUNTIME_FLAGS = ("domain_aliases", "public_url", "cors_origins", "hq_url", "visual_origin", "visual_same_origin", "caddy_visuals",
                 "reader_origin")


@dataclass
class Settings:
    target: str = ""
    domain: str = ""
    dns_name: str = ""             # optional operational record, separate from the public domain
    dns_zone: str = ""             # exact authoritative Route 53 zone for dns_name
    front_door: str = "caddy"
    updater: bool = True
    company: str = ""
    company_file_data: str = field(default="", repr=False)
    owner_email: str = ""
    auth: str = "google"
    tenant: str = ""
    client_id: str = ""
    client_secret: str = ""
    issuer: str = ""               # --auth oidc: the provider's issuer URL
    signin_label: str = ""         # --auth oidc: the provider's name on the sign-in button ("Sign in with <label>")
    allowed_domain: str = ""
    access_issuer: str = ""
    access_audience: str = ""
    decisions_provider: str = ""
    decisions_key: str = ""
    backup: str = ""
    backup_url: str = ""
    backup_endpoint: str = ""
    backup_region: str = ""
    backup_key_id: str = ""
    backup_secret: str = ""
    cf_account_id: str = ""
    cf_token: str = ""
    tunnel_token: str = ""
    tunnel_id: str = ""
    ssh_host: str = ""
    ssh_port: int = 22
    ssh_identity: str = ""
    server_ip: str = ""
    aws_region: str = "us-east-1"
    aws_profile: str = ""
    aws_instance_type: str = "t4g.small"
    aws_os: str = "ubuntu"
    aws_instance_id: str = ""
    aws_name: str = ""             # --name: the resources are tico-<name>; blank derives it from the domain
    aws_permissions_boundary: str = ""
    plan_bucket: str = ""          # --plan-bucket: the starter plan goes to the server by this S3 bucket, not in the env
    plan_bucket_region: str = ""
    plan_bucket_managed: bool = False  # setup's private, expiring bucket; never configure a caller-supplied bucket
    cloud_location: str = ""
    cloud_size: str = ""
    cloud_ssh_key: str = ""
    cloud_version: str = ""
    server_url: str = ""
    runner_label: str = ""
    runner_host: str = ""   # cloudflared only: a hostname without Access for computers and outside agents; "none" declines the default
    runner_given: bool = False   # --runner-hostname named it on this run, so its DNS record may be repointed
    runner_tag: str = "latest"
    tag: str = "latest"
    compose_ref: str = "main"
    # A server Tico hosts: own (our team) or invited (an invited company), set from our operator's --hosted, never asked.
    hosted: str = ""
    hosted_zone: str = ""          # our DNS zone; the domain must be under it
    owner_link_hash: str = ""      # sha256 of the owner's first sign-in link; the link itself is only in a 0600 file here
    preset: str = ""               # recommended: the sample company, subscriptions plus Gateway, Support
    support: str = ""              # with the preset: on, or off with --no-support
    gateway_invite: str = ""
    inbound_key: str = ""          # existing company Inbox credential; setup never issues one
    inbound_url: str = ""          # optional server endpoint; blank keeps the server's HQ default

    domain_aliases: str = ""
    public_url: str = ""
    cors_origins: str = ""
    hq_url: str = ""
    visual_origin: str = ""
    visual_same_origin: str = ""
    caddy_visuals: str = ""
    reader_origin: str = ""       # the Company page's own HTTPS origin (docs/company-page.md); Caddy serves it
    runner_url: str = ""          # passive endpoint preservation is not DNS authority
    gateway_url: str = ""
    visual_secret: str = ""       # private saved env only, never a flag or public state
    trusted_proxies: str = ""
    client_ip_header: str = ""
    edge_subnet: str = ""
    edge_dynamic_range: str = ""
    caddy_address: str = ""
    cloudflared_address: str = ""

    def private_env(self, env_text: str) -> str:
        """The .env this computer keeps. With --plan-bucket the server's env names only the plan's key, so the copy here
        keeps the plan itself too, for a re-run without --company-file."""
        if self.company_file_data and (self.plan_bucket or self.target in ("ssh", "local")):
            return env_text + envfile.render({"TICO_COMPANY_FILE_B64": self.company_file_data}).split("\n", 1)[1]
        return env_text

    def public(self) -> dict:
        validate_runtime(self)
        return {k: v for k, v in asdict(self).items() if k not in SECRET_FIELDS + TRANSIENT_FIELDS}

    def runner(self) -> str:
        return "" if self.front_door != "cloudflared" or self.runner_host in ("", "none") else self.runner_host

    def effective_runner_url(self) -> str:
        return self.runner_url or (f"https://{self.runner()}" if self.runner() else "")

    def effective_runner_host(self) -> str:
        value = self.effective_runner_url()
        if not value or self.front_door != "cloudflared":
            return ""
        host = endpoint(value, "TICO_RUNNER_URL").hostname or ""
        # Match the local tunnel helper: IP/loopback and the canonical app add no separate route.
        return host if plain_hostname(host) and host != self.domain.lower() else ""

    def dns(self) -> str:
        return self.dns_name or self.domain

    def plan_sha256(self) -> str:
        return hashlib.sha256(self.company_file_data.encode()).hexdigest()

    def plan_key(self) -> str:
        """plans/<name>/<sha256>: the server's role may read only plans/<its own name>/*."""
        from .aws import slug
        return f"plans/{self.aws_name or slug(self.domain)}/{self.plan_sha256()}"

    def to_env(self) -> dict[str, str]:
        validate_runtime(self)
        # Keep the alias too: a pinned older server can use the generated settings.
        e = {"TICO_TEAM_NAME": self.company, "TICO_COMPANY_NAME": self.company, "TICO_OWNER_EMAIL": self.owner_email, "TICO_DOMAIN": self.domain,
             "TICO_PORT": os.environ.get("TICO_PORT", "8765"),
             "COMPOSE_PROFILES": contract.profiles(self.front_door, self.updater),
             "TICO_TAG": self.tag if self.tag != "latest" else "", "TICO_UPDATER_URL": contract.UPDATER_URL if self.updater else ""}
        if self.front_door == "cloudflared":
            e["CLOUDFLARE_TUNNEL_TOKEN"] = self.tunnel_token
        if self.target in ("aws", "ssh", "local"):
            # Absolute, for compose.yaml's starter plan mount: the updater's Compose resolves ./ against its own /project.
            e["TICO_COMPANY_FILE_DIR"] = f"{contract.REMOTE_DIR}/{contract.COMPANY_FILE_DIR}"
        e.update({key: getattr(self, name) for name, key in RUNTIME_ENV.items() if getattr(self, name)})
        if self.effective_runner_url():
            e["TICO_RUNNER_URL"] = self.effective_runner_url()
        if self.visual_secret:
            e["TICO_VISUAL_SECRET"] = self.visual_secret
        if self.hosted:
            e["TICO_HOSTED"] = self.hosted
        if self.auth == OWNER_LINK:
            e.update(TICO_AUTH_PROXY=OWNER_LINK, TICO_OWNER_LINK_HASH=self.owner_link_hash)
        elif self.auth == "cloudflare":
            e.update(TICO_AUTH_PROXY="cloudflare", TICO_ACCESS_ISSUER=self.access_issuer, TICO_ACCESS_AUDIENCE=self.access_audience)
        else:
            e.update(TICO_AUTH_PROXY="oidc",
                     TICO_OIDC_ISSUER=self.issuer if self.auth == "oidc" else contract.issuer(self.auth, self.tenant),
                     TICO_OIDC_CLIENT_ID=self.client_id, TICO_OIDC_CLIENT_SECRET=self.client_secret,
                     TICO_OIDC_LABEL=self.signin_label if self.auth == "oidc" else "",
                     **{contract.KEY_ALLOWED_DOMAIN: self.allowed_domain})
            if self.hosted:
                # A server Tico hosts keeps the owner link beside it, for when the issuer is down (backend/owner_link.py).
                e["TICO_OWNER_LINK_HASH"] = self.owner_link_hash
        if self.preset == "recommended":
            # The same choices as scripts/install.sh --recommended, read on the server's first start.
            e.update(TICO_INSTALL_PRESET="recommended", TICO_SAMPLE_COMPANY="on", TICO_MODELS_PRESET="subscriptions-gateway",
                     TICO_SUPPORT=self.support or "on", TICO_GATEWAY_INVITE=self.gateway_invite)
        if self.company_file_data and self.plan_bucket:
            # Only where it is and its sha256: the server copies the plan to a file at first boot (remote.py pull-env.sh).
            e.update(TICO_COMPANY_FILE_S3=f"s3://{self.plan_bucket}/{self.plan_key()}",
                     TICO_COMPANY_FILE_S3_REGION=self.plan_bucket_region or self.aws_region,
                     TICO_COMPANY_FILE_SHA256=self.plan_sha256(),
                     TICO_COMPANY_FILE=f"{contract.COMPANY_FILE_MOUNT}/{contract.COMPANY_FILE_NAME}", TICO_SAMPLE_COMPANY="off")
        elif self.company_file_data and self.target in ("ssh", "local"):
            e.update(TICO_COMPANY_FILE=f"{contract.COMPANY_FILE_MOUNT}/{contract.COMPANY_FILE_NAME}",
                     TICO_COMPANY_FILE_SHA256=self.plan_sha256(), TICO_SAMPLE_COMPANY="off")
        elif self.company_file_data:
            e.update(TICO_COMPANY_FILE_B64=self.company_file_data, TICO_SAMPLE_COMPANY="off")
        if self.inbound_key:
            e["TICO_INBOUND_KEY"] = self.inbound_key
        if self.inbound_url:
            e["TICO_INBOUND_URL"] = self.inbound_url
        if self.decisions_key and self.decisions_provider:
            e[contract.DECISIONS_KEYS[self.decisions_provider]] = self.decisions_key
        if self.backup_url:
            e.update(TICO_BACKUP_URL=self.backup_url, TICO_BACKUP_ENDPOINT=self.backup_endpoint, TICO_BACKUP_REGION=self.backup_region,
                     LITESTREAM_ACCESS_KEY_ID=self.backup_key_id, LITESTREAM_SECRET_ACCESS_KEY=self.backup_secret)
        return e

    def secrets(self) -> list[str]:
        return [v for v in (self.client_secret, self.decisions_key, self.cf_token, self.tunnel_token, self.backup_secret,
                            self.gateway_invite, self.inbound_key, self.inbound_url, self.visual_secret, self.company_file_data) if v]


def default_runner_host(domain: str, zone: str = "") -> str:
    """runner.<domain> at a zone's apex; beside the domain (tico-runner.example.com for tico.example.com) under it, because
    Cloudflare's universal certificate covers the zone and one label below it, not two."""
    if zone and domain != zone and domain.endswith("." + zone):
        label, parent = domain.split(".", 1)
        return f"{label}-runner.{parent}"
    return f"runner.{domain}"


def from_saved(saved: dict, env_text: str) -> Settings:
    known = {f.name for f in fields(Settings)}
    saved = {"decisions_provider" if k == "judge_provider" else k: v for k, v in saved.items()}   # state.json from before 0.2.4
    s = Settings(**{k: v for k, v in saved.items() if k in known and k not in TRANSIENT_FIELDS})
    e = envfile.parse(env_text)
    s.client_secret = e.get("TICO_OIDC_CLIENT_SECRET", "")
    s.tunnel_token = e.get("CLOUDFLARE_TUNNEL_TOKEN", "")
    s.gateway_invite = e.get("TICO_GATEWAY_INVITE", "")
    s.inbound_key = e.get("TICO_INBOUND_KEY", "")
    s.inbound_url = e.get("TICO_INBOUND_URL", "")
    s.visual_secret = e.get("TICO_VISUAL_SECRET", "")
    s.company_file_data = e.get("TICO_COMPANY_FILE_B64", "")
    for name, key in RUNTIME_ENV.items():
        # Empty public values are durable clears, even when an older private copy still has a value.
        if name not in saved:
            setattr(s, name, e.get(key, ""))
    s.backup_url, s.backup_endpoint, s.backup_region = (e.get(k, "") for k in ("TICO_BACKUP_URL", "TICO_BACKUP_ENDPOINT", "TICO_BACKUP_REGION"))
    s.backup_key_id, s.backup_secret = e.get("LITESTREAM_ACCESS_KEY_ID", ""), e.get("LITESTREAM_SECRET_ACCESS_KEY", "")
    for prov, key in contract.DECISIONS_KEYS.items():
        if e.get(key):
            s.decisions_provider, s.decisions_key = prov, e[key]
    return s


def plain_hostname(host: str) -> bool:
    return bool(host and host.isascii() and len(host) <= 253 and "." in host
                and re.search("[a-z]", host) and all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", p)
                                                       for p in host.split(".")))


def endpoint(value: str, key: str, *, origin=False, https=False):
    """Reject secret-bearing/ambiguous URLs without reflecting their former private value."""
    def refuse():
        raise MissingInput(f"{key}: use a plain {'HTTPS origin' if https else 'HTTP/HTTPS URL'} without credentials, query, fragment or controls; correct or clear the saved setting")
    if (not isinstance(value, str) or not value or not value.isascii()
            or any(ord(c) <= 32 or ord(c) == 127 for c in value) or "\\" in value
            or "?" in value or "#" in value):
        refuse()
    try:
        parsed = urllib.parse.urlsplit(value)
        host, port = parsed.hostname or "", parsed.port
        authority = "[" + host + "]" if ":" in host else host
        if port is not None:
            authority += ":" + str(port)
        if (parsed.scheme not in (("https",) if https else ("http", "https")) or not host
                or len(host) > 253 or "%" in host or parsed.username is not None or parsed.password is not None or port == 0
                or parsed.netloc.lower() != authority or origin and parsed.path not in ("", "/")):
            refuse()
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if all(c in "0123456789." for c in host):
                refuse()
            if not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", part) for part in host.split(".")):
                refuse()
    except ValueError:
        refuse()
    return parsed


def aliases(value: str, canonical: str, runner_host="", visual_host="", reader="") -> list[str]:
    if not isinstance(value, str) or not value.isascii() or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise MissingInput("TICO_DOMAIN_ALIASES: use comma-separated plain DNS hostnames")
    if not value:
        return []
    result, seen = [], {canonical.lower()}
    for part in value.split(","):
        host = part.strip(" ").lower()
        if not plain_hostname(host):
            raise MissingInput("TICO_DOMAIN_ALIASES: use plain DNS hostnames without empty entries")
        if host in (runner_host, visual_host, reader):
            raise MissingInput("TICO_DOMAIN_ALIASES: app aliases must differ from restricted runner, visual and reader hosts")
        if host not in seen:
            result.append(host); seen.add(host)
    return result


def visual_host(value: str, canonical: str, runner_host="") -> str:
    if not value:
        return ""
    host = endpoint(value, "TICO_VISUAL_ORIGIN", origin=True, https=True).hostname
    if not plain_hostname(host) or host in (canonical.lower(), runner_host):
        raise MissingInput("TICO_VISUAL_ORIGIN: use a separate DNS origin, distinct from app and runner")
    return host


def reader_host(value: str, canonical: str, *taken: str) -> str:
    """The Company page's own origin (docs/company-page.md): a plain DNS host of its own on the standard HTTPS port,
    never the app's, the runner's, the visual origin's or an alias. Caddy serves it, so the tunnel is not an option."""
    if not value:
        return ""
    parsed = endpoint(value, "TICO_READER_ORIGIN", origin=True, https=True)
    host = parsed.hostname or ""
    if not plain_hostname(host) or host in (canonical.lower(), *(t for t in taken if t)) or parsed.port not in (None, 443):
        raise MissingInput("TICO_READER_ORIGIN: use a separate DNS origin on the standard HTTPS port, distinct from the app, "
                           "runner, visual and alias hosts")
    return host


def validate_runtime(s: Settings, *, final=True, new_public=False, new_hq=False, runner_override=False) -> None:
    for name, key in RUNTIME_ENV.items():
        value = getattr(s, name)
        if not isinstance(value, str) or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise MissingInput(f"{key}: saved setting contains invalid characters; correct or clear it")
    for name in ("visual_same_origin", "caddy_visuals"):
        if getattr(s, name) not in ("", "0", "1"):
            raise MissingInput(f"{RUNTIME_ENV[name]}: expected 0 or 1")
    if s.public_url:
        public = endpoint(s.public_url, "TICO_PUBLIC_URL", origin=True, https=new_public)
        try:
            loopback = ipaddress.ip_address(public.hostname).is_loopback
        except ValueError:
            loopback = public.hostname == "localhost"
        legacy_local = not new_public and public.scheme == "http" and loopback
        canonical = f"https://{s.domain.lower()}"
        # The backend strips a root slash but otherwise uses these bytes in its registered callback.
        if not legacy_local and s.public_url not in (canonical, canonical + "/"):
            raise MissingInput("TICO_PUBLIC_URL: use exact https://<public domain> (optional root slash) to preserve the registered callback; correct or clear incompatible saved spelling")
    if s.hq_url:
        endpoint(s.hq_url, "TICO_HQ_URL", origin=True, https=new_hq)
    if s.gateway_url:
        endpoint(s.gateway_url, "TICO_GATEWAY_URL")
    if s.runner_url and not runner_override:
        runner = endpoint(s.runner_url, "TICO_RUNNER_URL")
        if s.domain_aliases and (not plain_hostname(runner.hostname or "") or runner.path not in ("", "/")):
            raise MissingInput("TICO_DOMAIN_ALIASES: needs a plain runner origin before app aliases can be routed")
        if s.caddy_visuals == "1" and runner.path not in ("", "/"):
            raise MissingInput("TICO_CADDY_VISUALS: needs a root runner URL")
    origins = []
    if s.cors_origins:
        for value in s.cors_origins.split(","):
            parsed = endpoint(value.strip(" "), "TICO_CORS_ORIGINS", origin=True)
            if (parsed.path or parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1")
                    or ":" in parsed.hostname and parsed.hostname != "::1"):
                raise MissingInput("TICO_CORS_ORIGINS: use exact origins without paths; HTTP is only for loopback")
            origins.append(value.strip(" ").lower())
    if s.visual_origin:
        parsed = endpoint(s.visual_origin, "TICO_VISUAL_ORIGIN", origin=True, https=True)
        if not plain_hostname(parsed.hostname or ""):
            raise MissingInput("TICO_VISUAL_ORIGIN: use a plain DNS origin")
    if s.reader_origin:
        # The tunnel routes only the app's hosts; the Company page's reader origin is served by the Caddy front door.
        if s.front_door != "caddy":
            raise MissingInput("TICO_READER_ORIGIN needs --front-door caddy: the tunnel does not route the Company page")
        parsed = endpoint(s.reader_origin, "TICO_READER_ORIGIN", origin=True, https=True)
        if not plain_hostname(parsed.hostname or ""):
            raise MissingInput("TICO_READER_ORIGIN: use a plain DNS origin")
    if s.domain_aliases:
        aliases(s.domain_aliases, s.domain)
    if not final:
        return
    runner_host = s.effective_runner_host()
    collision_runner = endpoint(s.runner_url, "TICO_RUNNER_URL").hostname if s.runner_url else runner_host
    visual = visual_host(s.visual_origin, s.domain, collision_runner)
    reader = reader_host(s.reader_origin, s.domain, collision_runner, visual)
    hosts = aliases(s.domain_aliases, s.domain, runner_host, visual, reader)
    if s.domain_aliases and s.front_door != "cloudflared":
        raise MissingInput("TICO_DOMAIN_ALIASES needs --front-door cloudflared")
    if any(f"https://{host}" not in origins for host in hosts):
        raise MissingInput("TICO_CORS_ORIGINS: explicitly list each app alias origin before routing it")
    if s.caddy_visuals == "1":
        if s.front_door != "caddy" or not visual or s.visual_same_origin == "1":
            raise MissingInput("TICO_CADDY_VISUALS=1 needs Caddy and a separate visual origin with same-origin rendering disabled")
        if endpoint(s.visual_origin, "TICO_VISUAL_ORIGIN", origin=True, https=True).port not in (None, 443):
            raise MissingInput("TICO_CADDY_VISUALS=1 requires the standard HTTPS visual port")


def gather_runtime(args, s: Settings, *, final=False) -> None:
    runner_given = getattr(args, "runner_host", None)
    if runner_given is not None:
        host = runner_given.lower()
        if host == "none":
            s.runner_url = ""
        elif DOMAIN_RE.fullmatch(host):
            s.runner_url = f"https://{host}"
    for name in RUNTIME_FLAGS:
        value = getattr(args, name, None)
        if value is not None:
            setattr(s, name, value)
    validate_runtime(s, final=final, new_public=getattr(args, "public_url", None) is not None,
                     new_hq=getattr(args, "hq_url", None) is not None,
                     runner_override=not final and getattr(args, "runner_host", None) is not None)
    if final:
        visual = visual_host(s.visual_origin, s.domain, s.effective_runner_host())
        s.domain_aliases = ",".join(aliases(s.domain_aliases, s.domain, s.effective_runner_host(), visual,
                                          reader_host(s.reader_origin, s.domain, s.effective_runner_host(), visual)))


class Asker:
    """Resolves one field. `dry` fills gaps with visible placeholders so a plan can always be shown."""

    def __init__(self, io: IO, args, base: Settings, dry: bool):
        self.io, self.args, self.s, self.dry = io, args, base, dry

    def get(self, field: str, question: str, *, flag: str = "", default: str = "", choices: dict | None = None,
            secret: bool = False, required: bool = True, env: str = "", validate=None) -> str:
        flag = flag or "--" + field.replace("_", "-")
        given = getattr(self.args, field, None)
        if given in (None, "") and env:
            given = os.environ.get(env)
        current = getattr(self.s, field)
        if given not in (None, ""):
            val = str(given)
        elif secret and current:
            val = current
        elif not self.io.interactive or self.dry:
            val = str(current or default or "")
            if not val and required:
                if self.dry:
                    val = f"<{field}>"
                else:
                    raise MissingInput(f"Missing {flag}" + (f" (or set {env})" if env else ""))
        else:
            if choices:
                self.io.say(question)
                keys = list(choices)
                for i, k in enumerate(keys, 1):
                    self.io.say(f"  {i}) {choices[k]}")
                d = str(current or default or keys[0])
                raw = self.io.ask("Choose", str(keys.index(d) + 1) if d in keys else "1")
                val = keys[int(raw) - 1] if raw.isdigit() and 0 < int(raw) <= len(keys) else raw
            else:
                val = self.io.ask(question, str(current or default or ""), secret=secret)
            if not val and required:
                raise MissingInput(f"{question} is required")
        if choices is not None and val not in choices and not val.startswith("<"):
            raise MissingInput(f"{flag} must be one of: {', '.join(choices)}")
        if validate and val and not val.startswith("<") and not validate(val):
            raise MissingInput(f"{flag}: {val!r} is not valid")
        setattr(self.s, field, val)
        return val
