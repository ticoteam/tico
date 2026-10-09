"""The answers, and where each comes from: flag, environment (secrets), the last run, then a prompt."""
from __future__ import annotations

import os
import re
import urllib.parse
from dataclasses import asdict, dataclass, fields

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
         "cloudflare": "Cloudflare Access (needs the tunnel)"}

# Not persisted to state.json: they live in the private .env copy or come from the environment.
SECRET_FIELDS = ("client_secret", "decisions_key", "cf_token", "tunnel_token", "backup_secret")
# This run's only, never saved: a re-run without the flag must not inherit permission to repoint a record.
TRANSIENT_FIELDS = ("runner_given",)


@dataclass
class Settings:
    target: str = ""
    domain: str = ""
    front_door: str = "caddy"
    updater: bool = True
    company: str = ""
    owner_email: str = ""
    auth: str = "google"
    tenant: str = ""
    client_id: str = ""
    client_secret: str = ""
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

    def public(self) -> dict:
        return {k: v for k, v in asdict(self).items() if k not in SECRET_FIELDS + TRANSIENT_FIELDS}

    def runner(self) -> str:
        return "" if self.front_door != "cloudflared" or self.runner_host in ("", "none") else self.runner_host

    def to_env(self) -> dict[str, str]:
        # Keep the alias too: a pinned older server can use the generated settings.
        e = {"TICO_TEAM_NAME": self.company, "TICO_COMPANY_NAME": self.company, "TICO_OWNER_EMAIL": self.owner_email, "TICO_DOMAIN": self.domain,
             "TICO_PORT": os.environ.get("TICO_PORT", "8765"),
             "COMPOSE_PROFILES": contract.profiles(self.front_door, self.updater),
             "TICO_TAG": self.tag if self.tag != "latest" else "", "TICO_UPDATER_URL": contract.UPDATER_URL if self.updater else ""}
        if self.front_door == "cloudflared":
            e["CLOUDFLARE_TUNNEL_TOKEN"] = self.tunnel_token
            if self.runner():
                e["TICO_RUNNER_URL"] = f"https://{self.runner()}"
        if self.auth == "cloudflare":
            e.update(TICO_AUTH_PROXY="cloudflare", TICO_ACCESS_ISSUER=self.access_issuer, TICO_ACCESS_AUDIENCE=self.access_audience)
        else:
            e.update(TICO_AUTH_PROXY="oidc", TICO_OIDC_ISSUER=contract.issuer(self.auth, self.tenant),
                     TICO_OIDC_CLIENT_ID=self.client_id, TICO_OIDC_CLIENT_SECRET=self.client_secret,
                     **{contract.KEY_ALLOWED_DOMAIN: self.allowed_domain})
        if self.decisions_key and self.decisions_provider:
            e[contract.DECISIONS_KEYS[self.decisions_provider]] = self.decisions_key
        if self.backup_url:
            e.update(TICO_BACKUP_URL=self.backup_url, TICO_BACKUP_ENDPOINT=self.backup_endpoint, TICO_BACKUP_REGION=self.backup_region,
                     LITESTREAM_ACCESS_KEY_ID=self.backup_key_id, LITESTREAM_SECRET_ACCESS_KEY=self.backup_secret)
        return e

    def secrets(self) -> list[str]:
        return [v for v in (self.client_secret, self.decisions_key, self.cf_token, self.tunnel_token, self.backup_secret) if v]


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
    runner = urllib.parse.urlsplit(e.get("TICO_RUNNER_URL", "")).hostname or ""
    if not s.runner_host and runner and runner != s.domain:
        s.runner_host = runner
    s.backup_url, s.backup_endpoint, s.backup_region = (e.get(k, "") for k in ("TICO_BACKUP_URL", "TICO_BACKUP_ENDPOINT", "TICO_BACKUP_REGION"))
    s.backup_key_id, s.backup_secret = e.get("LITESTREAM_ACCESS_KEY_ID", ""), e.get("LITESTREAM_SECRET_ACCESS_KEY", "")
    for prov, key in contract.DECISIONS_KEYS.items():
        if e.get(key):
            s.decisions_provider, s.decisions_key = prov, e[key]
    return s


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
