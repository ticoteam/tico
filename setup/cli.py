from __future__ import annotations

import argparse
import sys

from . import backup as bk, settings as st
from .ui import IO, MissingInput
from .runner_setup import run_runner
from . import aws as awsmod, hosted, support
from .wizard import Deps, destroy, doctor, run

COMMANDS = ("run", "doctor", "destroy", "runner", "backup-storage", "support", "owner-link")


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tico setup", description="Set up Tico on one Docker server, from a domain to a signed-in app.",
                                epilog="Secrets are never taken as flags: set TICO_OIDC_CLIENT_SECRET, CLOUDFLARE_API_TOKEN, "
                                       "CLOUDFLARE_TUNNEL_TOKEN, TICO_INBOUND_KEY or OPENAI_API_KEY (ANTHROPIC_/GEMINI_) "
                                       "in the environment. Optional server endpoint: TICO_INBOUND_URL.")
    p.add_argument("command", nargs="?", choices=COMMANDS, default="run",
                   help="run (default): set up the server; runner: add a Linux computer that runs bots; doctor: re-run the checks; "
                        "destroy: remove what setup created on AWS; backup-storage: from a laptop with AWS credentials, create the "
                        "backup bucket and its key for a server that has none (then install with --backup existing); support: "
                        "what scripts/install.sh runs when it fails, to offer sending the error to Tico support; owner-link: on a "
                        "server Tico hosts, a new owner sign-in link in place of the old one (--sign-out also ends the owner's sessions). "
                        "`cell ...`: many companies on one shared host (python3 -m setup cell --help; docs/cell.md)")
    p.add_argument("--dry-run", action="store_true", help="print the plan and change nothing")
    p.add_argument("--non-interactive", action="store_true", help="never prompt; missing values are errors")
    p.add_argument("--yes", "-y", action="store_true", help="do not ask for confirmation")
    p.add_argument("--target", choices=list(st.TARGETS))
    p.add_argument("--cloud", choices=["aws", "hetzner", "digitalocean"], help="create the server on this cloud (same as --target)")
    p.add_argument("--cloud-location", help="hetzner: location (default nbg1); digitalocean: region slug (default nyc3)")
    p.add_argument("--cloud-size", help="hetzner: server type (default cax11); digitalocean: size slug (default s-1vcpu-2gb)")
    p.add_argument("--cloud-ssh-key", help="name (hetzner) or id/fingerprint (digitalocean) of an SSH key already in your account")
    p.add_argument("--tico-version", help="release to install, such as v1.2.3 (cloud targets: default the latest release; local: what scripts/install.sh pins)")
    p.add_argument("--domain")
    p.add_argument("--dns-name", help="hosted AWS/Caddy: exact operational Route 53 hostname; defaults to --domain")
    p.add_argument("--dns-zone", help="hosted AWS/Caddy: authoritative zone of --dns-name; no public URL or certificate change")
    p.add_argument("--front-door", choices=list(st.FRONT_DOORS))
    p.add_argument("--domain-aliases", help="cloudflared: comma-separated full-app hosts; empty clears saved aliases")
    p.add_argument("--public-url", help="canonical HTTPS origin on --domain; empty clears the saved override")
    p.add_argument("--cors-origins", help="explicit comma-separated app origins; empty clears, never adds visual trust")
    p.add_argument("--hq-url", help="public HQ HTTPS origin; omitted keeps the saved/default endpoint")
    p.add_argument("--visual-origin", help="isolated HTTPS visual origin; empty clears, without acquiring DNS authority")
    p.add_argument("--visual-same-origin", choices=("0", "1"), help="explicitly retain or disable legacy same-origin visuals")
    p.add_argument("--caddy-visuals", choices=("0", "1"), help="explicit local Caddy visual serving; default off")
    p.add_argument("--reader-origin", help="the Company page's own HTTPS origin, such as https://page.acme.example, served by "
                   "the Caddy front door (docs/company-page.md); empty clears it")
    p.add_argument("--runner-hostname", dest="runner_host", metavar="HOST",
                   help="cloudflared: a hostname without Access where computers and outside agents connect (only /api/v2 and "
                        "/download are routed there); default with --auth cloudflare: runner.<domain>, or <name>-runner.<zone> "
                        "when the domain is a subdomain; 'none' for no runner hostname")
    p.add_argument("--ssh", dest="ssh_host", metavar="USER@HOST")
    p.add_argument("--ssh-port", type=int)
    p.add_argument("--ssh-identity")
    p.add_argument("--server-ip", help="public IPv4 the domain should point at (caddy; ssh and command targets)")
    p.add_argument("--aws-region")
    p.add_argument("--aws-profile")
    p.add_argument("--aws-instance-type", choices=["t4g.small", "t4g.medium", "t4g.large", "m8i-flex.xlarge", "m8i-flex.2xlarge"], help="server default t4g.small; runner default t4g.medium")
    p.add_argument("--server-computer", choices=["same", "none"], default="same", help="run: where bots run")
    p.add_argument("--aws-os", choices=["ubuntu", "al2023"])
    p.add_argument("--aws-permissions-boundary", metavar="ARN", help="AWS: the IAM policy ARN set as the permissions boundary of "
                   "the server's role when it is created, for an account that only lets setup make roles under one")
    p.add_argument("--plan-bucket", metavar="BUCKET", help="AWS, with --company-file: put the starter plan in this S3 bucket "
                   "at plans/<name>/<sha256> for the server to copy once at first boot, instead of in its 8 KB settings "
                   "parameter; deleted once the server is up (Tico's hosted servers)")
    p.add_argument("--plan-bucket-region", metavar="REGION", help="the --plan-bucket's region (default --aws-region)")
    p.add_argument("--name", help="AWS: the server's name: its resources are tico-<name>, tagged tico-setup-name=<name>, and its "
                   "settings are in /tico/<name>/env (default: derived from --domain); destroy: the tico-setup-name tag value")
    p.add_argument("--dns", action="store_true", help="destroy: also delete the domain's A and AAAA records in Route 53, only "
                   "while they still point at this server (needs --domain)")
    p.add_argument("--auth", choices=[*st.AUTHS, st.OWNER_LINK],
                   help="owner-link: a single-use sign-in link for the owner, only with --hosted")
    p.add_argument("--hosted", metavar="own|invited", help="a server Tico hosts: own (our team) or invited (an invited company); "
                   "writes TICO_HOSTED and signs the owner in with a sign-in link written to a private file, never printed")
    p.add_argument("--hosted-zone", metavar="ZONE", help="with --hosted: our DNS zone; the domain must be a name under it")
    p.add_argument("--preset", metavar="recommended", help="with --hosted: the recommended setup (sample company, subscriptions "
                   "plus Tico Gateway from TICO_GATEWAY_INVITE in the environment, Tico Support, a computer on the server)")
    p.add_argument("--sign-out", action="store_true", help="owner-link: also sign the owner out everywhere")
    p.add_argument("--tenant", help="Microsoft tenant id or domain")
    p.add_argument("--client-id")
    p.add_argument("--issuer", help="with --auth oidc: the provider's issuer URL (https); the client secret comes only "
                   "from TICO_OIDC_CLIENT_SECRET in the environment")
    p.add_argument("--signin-label", metavar="NAME", help="with --auth oidc: the name on the sign-in button, "
                   "\"Sign in with NAME\" (for example \"Tico account\"); without it, sign-in goes straight to the provider")
    p.add_argument("--allowed-domain", help="only this email domain may sign in")
    p.add_argument("--access-issuer")
    p.add_argument("--access-audience")
    p.add_argument("--company")
    p.add_argument("--company-file", metavar="PATH", help="start with the company YAML exported by the org-chart picker")
    p.add_argument("--owner-email")
    p.add_argument("--decisions-provider", choices=["openai", "anthropic", "gemini"],
                   help="the model provider whose API key the server uses for decisions (optional)")
    p.add_argument("--judge-provider", dest="decisions_provider", choices=["openai", "anthropic", "gemini"],
                   help=argparse.SUPPRESS)      # the old name of --decisions-provider
    p.add_argument("--server-url", help="runner: the Tico server to join (default https://<--domain>)")
    p.add_argument("--label", dest="runner_label", help="runner: the computer's name in Tico")
    p.add_argument("--runner-tag", default="latest", help="runner: the release (vX.Y.Z) whose install.sh and images the runner uses; default is the latest")
    p.add_argument("--rejoin", action="store_true", help="runner: replace an existing runner container on the box")
    p.add_argument("--screen", action=argparse.BooleanOptionalAction, default=True,
                   help="runner: offer the computer's screen to the web app (TICO_SCREEN=1, the default; --no-screen turns it off)")
    p.add_argument("--runner", action="store_true", help="doctor: also check the runners this machine set up")
    p.add_argument("--backup", choices=list(bk.CHOICES), help="where backups go: aws (create an S3 bucket), r2 (create an R2 bucket), "
                   "existing (--backup-url), local (this server only). Default: aws on the AWS target, else local")
    p.add_argument("--backup-url", help="existing bucket, as s3://bucket/prefix; keys come from LITESTREAM_ACCESS_KEY_ID and LITESTREAM_SECRET_ACCESS_KEY")
    p.add_argument("--backup-endpoint", help="S3-compatible endpoint (R2 and others; not needed for AWS)")
    p.add_argument("--backup-region")
    p.add_argument("--backup-key-id", help="existing bucket: the access key id (or set LITESTREAM_ACCESS_KEY_ID)")
    p.add_argument("--cf-account-id", help="R2: the Cloudflare account id, when the domain's zone is not on that account")
    p.add_argument("--no-updater", action="store_true", help="leave out the one-click updater (it holds the Docker socket)")
    p.add_argument("--compose-ref", default="main", help="git ref to download compose.yaml from when not run inside a checkout")
    p.add_argument("--dns-timeout", type=float, default=20, help="minutes to wait for DNS to go live (default 20)")
    p.add_argument("--skip-dns-wait", action="store_true")
    p.add_argument("--no-support", action="store_true", help="when a check fails, do not offer to send it to Tico support "
                   "(TICO_SUPPORT=off does the same)")
    p.add_argument("--error", help=argparse.SUPPRESS)       # support: the installer's error line
    p.add_argument("--step", type=int, default=0, help=argparse.SUPPRESS)     # support: the installer's exit code
    return p


def backup_storage(args, io: IO, deps: Deps) -> int:
    """A server should not hold credentials that can create buckets and IAM users, so this runs where they are."""
    if not args.domain or not args.aws_region:
        raise MissingInput("backup-storage needs --domain and --aws-region")
    _, iam, _, account = deps.aws_clients(args.aws_region, args.aws_profile)
    values = bk.create_s3(deps.aws_s3(args.aws_region, args.aws_profile), iam, domain=args.domain, region=args.aws_region,
                          account_id=account, name=awsmod.slug(args.domain), say=io.say)
    io.say("\nOn the server, before the installer (the key is shown once; keep it out of shell history and chat):")
    io.say(f"  export LITESTREAM_ACCESS_KEY_ID={values['LITESTREAM_ACCESS_KEY_ID']}")
    io.say(f"  export LITESTREAM_SECRET_ACCESS_KEY={values['LITESTREAM_SECRET_ACCESS_KEY']}")
    io.say(f"and answer the backup question with: existing, {values['TICO_BACKUP_URL']}, region {values['TICO_BACKUP_REGION']}")
    io.say(f"  (non-interactive: -- --backup existing --backup-url {values['TICO_BACKUP_URL']} --backup-region {values['TICO_BACKUP_REGION']})")
    return 0


def main(argv: list[str] | None = None, deps: Deps | None = None, io: IO | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["cell"]:
        # Many companies on one shared host (docs/cell.md): its own machine interface, one JSON object per command.
        from . import cell
        return cell.main(argv[1:])
    args = parser().parse_args(argv)
    if args.cloud:
        args.target = args.cloud
    io = io or IO(interactive=not args.non_interactive and sys.stdin.isatty())
    deps = deps or Deps()
    try:
        return {"run": run, "doctor": doctor, "destroy": destroy, "runner": run_runner,
                "backup-storage": backup_storage, "support": lambda a, i, _: support.from_installer(a, i),
                "owner-link": hosted.rotate}[args.command](args, io, deps)
    except KeyboardInterrupt:
        io.say("\nInterrupted. Re-run `tico setup`; it resumes.")
        return 130
    except MissingInput as e:
        io.say(str(e))
        return 2
