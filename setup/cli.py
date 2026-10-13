from __future__ import annotations

import argparse
import sys

from . import backup as bk, settings as st
from .ui import IO, MissingInput
from .runner_setup import run_runner
from . import aws as awsmod
from .wizard import Deps, destroy, doctor, run

COMMANDS = ("run", "doctor", "destroy", "runner", "backup-storage")


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tico setup", description="Set up Tico on one Docker server, from a domain to a signed-in app.",
                                epilog="Secrets are never taken as flags: set TICO_OIDC_CLIENT_SECRET, CLOUDFLARE_API_TOKEN, "
                                       "CLOUDFLARE_TUNNEL_TOKEN or OPENAI_API_KEY (ANTHROPIC_/GEMINI_) in the environment.")
    p.add_argument("command", nargs="?", choices=COMMANDS, default="run",
                   help="run (default): set up the server; runner: add a Linux computer that runs bots; doctor: re-run the checks; "
                        "destroy: remove what setup created on AWS; backup-storage: from a laptop with AWS credentials, create the "
                        "backup bucket and its key for a server that has none (then install with --backup existing)")
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
    p.add_argument("--front-door", choices=list(st.FRONT_DOORS))
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
    p.add_argument("--aws-instance-type", choices=["t4g.small", "t4g.medium", "t4g.large"], help="server default t4g.small; runner default t4g.medium")
    p.add_argument("--aws-os", choices=["ubuntu", "al2023"])
    p.add_argument("--name", help="destroy: the tico-setup-name tag value (default: derived from --domain)")
    p.add_argument("--auth", choices=list(st.AUTHS))
    p.add_argument("--tenant", help="Microsoft tenant id or domain")
    p.add_argument("--client-id")
    p.add_argument("--allowed-domain", help="only this email domain may sign in")
    p.add_argument("--access-issuer")
    p.add_argument("--access-audience")
    p.add_argument("--company")
    p.add_argument("--owner-email")
    p.add_argument("--decisions-provider", choices=["openai", "anthropic", "gemini"],
                   help="the model provider whose API key the server uses for decisions (optional)")
    p.add_argument("--judge-provider", dest="decisions_provider", choices=["openai", "anthropic", "gemini"],
                   help=argparse.SUPPRESS)      # the old name of --decisions-provider
    p.add_argument("--server-url", help="runner: the Tico server to join (default https://<--domain>)")
    p.add_argument("--label", dest="runner_label", help="runner: the computer's name in Tico")
    p.add_argument("--runner-tag", default="latest", help="runner: the release (vX.Y.Z) whose install.sh and images the runner uses; default is the latest")
    p.add_argument("--rejoin", action="store_true", help="runner: replace an existing runner container on the box")
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
    args = parser().parse_args(argv)
    if args.cloud:
        args.target = args.cloud
    io = io or IO(interactive=not args.non_interactive and sys.stdin.isatty())
    deps = deps or Deps()
    try:
        return {"run": run, "doctor": doctor, "destroy": destroy, "runner": run_runner,
                "backup-storage": backup_storage}[args.command](args, io, deps)
    except KeyboardInterrupt:
        io.say("\nInterrupted. Re-run `tico setup`; it resumes.")
        return 130
    except MissingInput as e:
        io.say(str(e))
        return 2
