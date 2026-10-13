"""Company environments on one Mac: `python -m clients.environments create|list|show|remove`.

An environment is a directory under `~/.config/tico/environments/<slug>` (override the root with
`TICO_ENVIRONMENTS_DIR`) holding everything that is one company's rather than the product's: its
permanent id and display names, its runner registration and state, its subscription profiles,
and — when the server runs on this Mac — its database, blobs, seed registry, and logs.

    environment.json   identity and names; the id never changes, the names may
    server.env         local only: the backend process's configuration
    local-owner.token  local only: 0600, the owner's loopback session key
    hub.sqlite blobs/ registry/ logs/    local only: this company's data
    runner.json state-<id>/ profiles/    the runner registration, its state, its subscriptions

The legacy setup (`~/.config/tico/runner.json`, no environment directory) keeps working and
appears in `list` as the legacy default. `scripts/tico -e <slug>` is the operator's entry point;
this module is the part worth testing, so it holds no launchd or process management.
"""

import argparse
import json
import os
import re
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend import providers as PROVIDERS  # noqa: E402  the one provider -> runtime -> model map

TEMPLATE_DIR = ROOT / "templates/environment-registry"
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
FIRST_PORT = 8770
LEGACY_CONFIG = ".config/tico/runner.json"
# What `remove` refuses to delete silently: this company's data, its provider logins, and the
# registration the server still knows about. Logs are not on the list.
DATA_NAMES = ("hub.sqlite", "blobs", "profiles", "runner.json")


def root_dir():
    return Path(os.environ.get("TICO_ENVIRONMENTS_DIR") or Path.home() / ".config/tico/environments").expanduser()


def validate(slug):
    if not SLUG_RE.fullmatch(slug or ""):
        raise ValueError("An environment slug is lowercase letters, digits, and single hyphens")
    return slug


def path(slug):
    return root_dir() / validate(slug)


def new_id():
    """A permanent, opaque id. Names are for people; this is what owns resources."""
    return secrets.token_hex(8)


def load(slug):
    return json.loads((path(slug) / "environment.json").read_text())


def save(environment):
    directory = path(environment["slug"])
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    (directory / "environment.json").write_text(json.dumps(environment, indent=2) + "\n")
    return directory


def environments():
    """Every environment on this machine, by slug."""
    found = []
    for manifest in sorted(root_dir().glob("*/environment.json")):
        try:
            row = json.loads(manifest.read_text())
        except (OSError, ValueError):
            continue
        # A manifest without a slug was not written by `env create` (an older or hand-made one): not an environment.
        if isinstance(row, dict) and row.get("slug"):
            found.append(row)
    return found


def legacy():
    """The registration made before environments existed, if this Mac still has one."""
    config = Path.home() / LEGACY_CONFIG
    return {"slug": "default", "name": "default (legacy)", "config": str(config)} if config.exists() else None


def free_port(start=None):
    """The first loopback port from `start` that nothing else holds or is already promised."""
    start = FIRST_PORT if start is None else start
    promised = {int(row["port"]) for row in environments() if row.get("port")}
    for port in range(start, start + 100):
        if port in promised:
            continue
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
        return port
    raise RuntimeError("No free loopback port between " + str(start) + " and " + str(start + 100))


def provider_choice(providers, default_runtime="", default_model=""):
    """(enabled, runtime, model) from the flags, validated by the backend's own rules, or a
    ValueError that says what to pass. There is no default provider: nothing is assumed."""
    try:
        enabled = PROVIDERS.normalize_enabled(providers)
        if not enabled:
            raise ValueError(
                "--providers is required: name the AI providers this company uses, e.g. "
                "--providers anthropic,openai (choose from " + ", ".join(PROVIDERS.PROVIDER_BY_ID) + "). "
                "Tico assumes no vendor; --default-runtime and --default-model pick the default.")
        runtime, model = PROVIDERS.complete_choice(enabled, default_runtime, default_model)
    except PROVIDERS.ProviderError as exc:
        raise ValueError(exc.detail) from exc
    return enabled, runtime, model


def server_values(environment, directory, owner_email, github_owner=""):
    """The backend process's configuration for a local server, as KEY=VALUE pairs."""
    values = {"TICO_DB": str(directory / "hub.sqlite"),
              "TICO_BLOB_DIR": str(directory / "blobs"),
              "TICO_REGISTRY_DIR": str(directory / "registry"),
              "TICO_PUBLIC_URL": environment["url"],
              "TICO_RUNNER_URL": environment["url"],
              "TICO_ENVIRONMENT_ID": environment["id"],
              "TICO_COMPANY_NAME": environment["company_name"],
              "TICO_APP_NAME": environment["app_name"],
              "TICO_ASSISTANT_NAME": environment["assistant_name"],
              "TICO_OWNER_EMAIL": owner_email,
              "TICO_LOCAL_OWNER_TOKEN_FILE": str(directory / "local-owner.token"),
              "TICO_SCHEDULER": "1"}
    if github_owner:
        values["TICO_GITHUB_OWNER"] = github_owner
    provider = environment.get("providers") or {}
    if provider.get("enabled"):
        values["TICO_ENABLED_PROVIDERS"] = ",".join(provider["enabled"])
        values["TICO_DEFAULT_RUNTIME"] = provider["runtime"]
        values["TICO_DEFAULT_MODEL"] = provider["model"]
    return values


def write_server_env(directory, values):
    """`KEY=VALUE` lines the launchd job and `hub` read. No secret is stored here: the owner
    token lives in its own 0600 file, which this names."""
    for key, value in values.items():
        if "\n" in value:
            raise ValueError("Environment values are single lines: " + key)
    target = directory / "server.env"
    target.write_text("".join(f"{key}={value}\n" for key, value in values.items()))
    target.chmod(0o600)
    return target


def write_owner_token(directory):
    """32 url-safe random bytes, readable only by the operator's account."""
    target = directory / "local-owner.token"
    fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(secrets.token_urlsafe(32))
    return target


def seed_registry(directory, owner_name, owner_email, environment):
    """Copy the fictional seed roster in, with this company's owner and names substituted."""
    registry = directory / "registry"
    registry.mkdir(mode=0o700, parents=True, exist_ok=True)
    fields = {"owner_name": owner_name, "owner_email": owner_email,
              "owner_id": re.sub(r"[^a-z0-9-]", "-", owner_email.split("@")[0].lower()) or "owner",
              "company_name": environment["company_name"], "app_name": environment["app_name"],
              "assistant_name": environment["assistant_name"]}
    for template in sorted(TEMPLATE_DIR.glob("*.yaml")):
        text = template.read_text()
        for key, value in fields.items():
            text = text.replace("{{" + key + "}}", value)
        (registry / template.name).write_text(text)
    return registry


def seeded_bots(registry):
    """The bot slugs this environment's seed registry asks for."""
    document = yaml.safe_load((Path(registry) / "employees.yaml").read_text()) or {}
    return sorted(str(row["name"]) for row in document.get("employees") or [])


def seed_database(directory, workspace, values, python=sys.executable):
    """Create and seed this environment's database with `backend.manage initialize`.

    The registry directory reaches the backend through the environment, which is how the running
    server reads it too. A backend that ignores `TICO_REGISTRY_DIR` seeds the product checkout's
    own roster instead, which would put another company's bots in this database: that database is
    deleted rather than handed over, because it is exactly what an environment must never hold.
    """
    database = Path(values["TICO_DB"])
    result = subprocess.run([python, "-m", "backend.manage", "initialize", str(database),
                             "--projects", str(workspace)], cwd=ROOT, capture_output=True,
                            text=True, env={**os.environ, **values})
    if result.returncode:
        raise RuntimeError("Seeding the database failed: " + (result.stderr.strip().splitlines() or [""])[-1])
    try:
        with sqlite3.connect("file:" + str(database) + "?mode=ro", uri=True) as db:
            bots = sorted(row[0] for row in db.execute("SELECT bot FROM bot_config"))
    except sqlite3.Error:
        bots = []
    expected = seeded_bots(values["TICO_REGISTRY_DIR"])
    if bots != expected:
        for suffix in ("", "-wal", "-shm"):
            Path(str(database) + suffix).unlink(missing_ok=True)
        raise RuntimeError(
            f"The seed produced {', '.join(bots) or 'no bots'} instead of {', '.join(expected)}: this "
            f"backend does not read TICO_REGISTRY_DIR yet, so it used {ROOT / 'registry'}. The database "
            f"has been deleted; the rest of the environment is in place. Seed it with "
            f"`python -m backend.manage initialize {database} --projects {workspace}` once the backend "
            f"reads the registry directory.")
    return bots


def create(slug, *, company, app="", assistant="", owner_email, owner_name="", url="",
           workspace="", github_owner="", skip_seed=False, providers="", default_runtime="",
           default_model=""):
    """Write a new environment. `url` empty means a local server on a free loopback port.

    `providers` is required: the choice is stored in environment.json and, for a local server,
    seeded into its database on first boot through TICO_ENABLED_PROVIDERS / TICO_DEFAULT_*.
    """
    enabled, runtime, model = provider_choice(providers, default_runtime, default_model)
    directory = path(slug)
    if directory.exists():
        raise ValueError("That environment already exists: " + str(directory))
    app = app or company
    assistant = assistant or app
    environment = {"id": new_id(), "slug": slug, "company_name": company, "app_name": app,
                   "assistant_name": assistant, "url": url.rstrip("/"),
                   "server": "remote" if url else "local",
                   "providers": {"enabled": enabled, "runtime": runtime, "model": model},
                   "workspace": str(Path(workspace).expanduser() if workspace
                                    else Path.home() / "Companies" / company),
                   "created": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    if not url:
        environment["port"] = free_port()
        environment["url"] = f"http://127.0.0.1:{environment['port']}"
    directory = save(environment)
    directory.chmod(0o700)
    report = {"environment": environment, "directory": str(directory)}
    Path(environment["workspace"]).mkdir(mode=0o700, parents=True, exist_ok=True)
    if environment["server"] == "local":
        for name in ("blobs", "logs"):
            (directory / name).mkdir(mode=0o700, exist_ok=True)
        write_owner_token(directory)
        seed_registry(directory, owner_name or owner_email, owner_email, environment)
        values = server_values(environment, directory, owner_email, github_owner)
        write_server_env(directory, values)
        report["bots"] = ("not seeded" if skip_seed
                          else seed_database(directory, environment["workspace"], values))
    return report


def remove(slug, delete_data=False):
    """Forget an environment. Its workspace and bot repositories are never touched here."""
    directory = path(slug)
    if not directory.is_dir():
        raise ValueError("No such environment: " + slug)
    workspace = load(slug)["workspace"]
    data = [name for name in DATA_NAMES if (directory / name).exists()]
    data += [item.name for item in directory.glob("state-*")]
    if data and not delete_data:
        raise ValueError("This environment still holds " + ", ".join(sorted(data))
                         + ". Stop its services, take a backup, then repeat with --delete-data."
                         " Its workspace at " + workspace + " is never deleted either way.")
    shutil.rmtree(directory)
    return {"removed": str(directory), "workspace_kept": workspace}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m clients.environments", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("create")
    p.add_argument("slug")
    p.add_argument("--company", required=True)
    p.add_argument("--app", default="", help="Application name; defaults to the company name")
    p.add_argument("--assistant", default="", help="Assistant name; defaults to the app name")
    p.add_argument("--owner-email", required=True)
    p.add_argument("--owner-name", default="")
    where = p.add_mutually_exclusive_group(required=True)
    where.add_argument("--local", action="store_true", help="Run the server on this Mac")
    where.add_argument("--url", default="", help="An already hosted server, https://...")
    p.add_argument("--workspace", default="", help="Bot checkouts and secrets; default ~/Companies/<Company>")
    p.add_argument("--github-owner", default="", help="GitHub organization for this company's bot repositories")
    p.add_argument("--providers", default="",
                   help="Required. Comma-separated AI providers this company uses: "
                        + ", ".join(PROVIDERS.PROVIDER_BY_ID))
    p.add_argument("--default-runtime", default="", help="Runtime bots use by default; must belong to an enabled provider")
    p.add_argument("--default-model", default="", help="Model bots use by default; defaults to the recommended model")
    p.add_argument("--skip-seed", action="store_true", help="Create the files but do not initialize the database")
    sub.add_parser("list")
    p = sub.add_parser("show")
    p.add_argument("slug")
    p = sub.add_parser("remove")
    p.add_argument("slug")
    p.add_argument("--delete-data", action="store_true", help="Also delete this company's database and blobs")
    args = parser.parse_args(argv)
    if args.command == "list":
        for row in environments():
            print(f"{row['slug']:<16} {row.get('company_name', '')} - {row.get('url', '')} ({row.get('server', '')})")
        row = legacy()
        if row:
            print(f"{row['slug']:<16} {row['name']} - {row['config']}")
        return 0
    if args.command == "show":
        try:
            environment = load(args.slug)
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
        directory = path(args.slug)
        print(json.dumps({**environment, "directory": str(directory),
                          "files": sorted(item.name for item in directory.iterdir())}, indent=2))
        return 0
    try:
        if args.command == "create":
            if not args.providers and sys.stdin.isatty():
                print("Which AI providers does this company use? (" + ", ".join(PROVIDERS.PROVIDER_BY_ID) + ")")
                args.providers = input("providers (comma-separated): ").strip()
                if args.providers and not args.default_runtime and not args.default_model:
                    args.default_model = input("default model (blank for the recommended one): ").strip()
            if args.url and not args.url.startswith(("http://", "https://")):
                parser.error("--url is the server's address, https://...")
            report = create(args.slug, company=args.company, app=args.app, assistant=args.assistant,
                            owner_email=args.owner_email, owner_name=args.owner_name, url=args.url,
                            workspace=args.workspace, github_owner=args.github_owner,
                            skip_seed=args.skip_seed, providers=args.providers,
                            default_runtime=args.default_runtime, default_model=args.default_model)
        else:
            report = remove(args.slug, args.delete_data)
    except (ValueError, OSError, RuntimeError) as exc:
        parser.error(str(exc))
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
