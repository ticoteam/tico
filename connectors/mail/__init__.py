"""Shared bits for the mail connector: paths, errors, YAML, time.

Everything here is pure and importable without the Google client libraries, so the rest of
the package (and the tests) can be exercised offline.
"""

import json, os, re, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:                                     # pragma: no cover - py<3.9
    ZoneInfo = None
try:
    import yaml
except ImportError:                                     # pragma: no cover
    yaml = None

HUB = Path(__file__).resolve().parents[2]               # .../tico


def locations(env=None, hub=HUB):
    """(projects, runtime) directories. A Mac keeps them beside the checkout; a Linux runner has no
    such layout, so TICO_PROJECTS_DIR and TICO_MAIL_RUNTIME_DIR name them (docs/mail.md)."""
    env = os.environ if env is None else env
    projects = Path(env.get("TICO_PROJECTS_DIR") or hub.parent).expanduser()
    runtime = Path(env.get("TICO_MAIL_RUNTIME_DIR") or projects / "runtime" / "mail").expanduser()
    return projects, runtime


PROJECTS, RUNTIME = locations()                         # .../tico-work, .../runtime/mail
DB_PATH = RUNTIME / "mail.db"
AUDIT_PATH = RUNTIME / "audit.jsonl"
# An environment keeps its own registry; TICO_REGISTRY_DIR names it (backend/config.py does the same).
REGISTRY = Path(os.environ.get("TICO_REGISTRY_DIR") or HUB / "registry")
RULES_FILE = REGISTRY / "mail-rules.yaml"
DEFAULT_KEY = PROJECTS / "secrets" / "google-sa.json"
DEFAULT_TZ = "America/Los_Angeles"
MAX_BODY = 32 * 1024                                    # characters, per docs/mail-service.md
TRUNCATED = "\n\n[... truncated at %d characters ...]" % MAX_BODY
INTERNAL_DOMAIN = "acme.example"

JSON_ERRORS = False                                     # set by --json


class Failure(Exception):
    """Something went wrong (missing key, network, Google said no). Exit 1."""
    def __init__(self, msg, hint="", status=None):
        super().__init__(msg)
        self.msg, self.hint, self.status = msg, hint, status


class Refused(Failure):
    """A hub policy refused the action. Exit 2."""


def report(e, kind):
    if JSON_ERRORS:
        print(json.dumps({"ok": False, "kind": kind, "error": e.msg, "hint": e.hint}, indent=2))
    else:
        print(f"{kind}: {e.msg}", file=sys.stderr)
        if e.hint:
            print(f"  {e.hint}", file=sys.stderr)


# ---------------------------------------------------------------- a bot's files, new names and old
# The connectors run on their own, so this repeats clients/manifest.py: bot.yaml (older employee.yaml), the manifest's
# `tools:` (older `access:`), the folder bot-<slug> (older emp-<slug>) and HUB_BOT (older HUB_EMPLOYEE).

def repo_dir(projects, slug):
    """The bot's folder under the projects directory: for this run's own bot the folder the runner names (HUB_BOT_FOLDER:
    a bot made with a UUID id is not in bot-<id>, and a rename keeps its folder), else bot-<slug> if it exists, else
    emp-<slug> if that does, else bot-<slug>."""
    own = os.environ.get("HUB_BOT_FOLDER") or ""
    if own and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", own) and str(slug).lower() == hub_bot().lower():
        return Path(projects) / own
    for prefix in ("bot-", "emp-"):
        if (Path(projects) / (prefix + slug)).exists():
            return Path(projects) / (prefix + slug)
    return Path(projects) / ("bot-" + slug)


def bot_folders(projects):
    """[(slug, folder)] for every bot-* / emp-* folder, by slug, bot-<slug> ahead of emp-<slug>."""
    found = {}
    for prefix in ("emp-", "bot-"):
        for d in Path(projects).glob(prefix + "*"):
            found[d.name[len(prefix):]] = d
    return sorted(found.items())


def manifest_file(folder):
    """bot.yaml if it is there, else employee.yaml if that is, else bot.yaml."""
    for name in ("bot.yaml", "employee.yaml"):
        if (Path(folder) / name).is_file():
            return Path(folder) / name
    return Path(folder) / "bot.yaml"


def declared_tools(manifest):
    """The manifest's `tools:` list, else the older `access:`."""
    m = manifest or {}
    return (m["tools"] if "tools" in m else m.get("access")) or []


def hub_bot(env=None):
    """The bot this run is, from HUB_BOT or the older HUB_EMPLOYEE (the runner sets both)."""
    env = os.environ if env is None else env
    return (env.get("HUB_BOT") or env.get("HUB_EMPLOYEE") or "").strip()


# ---------------------------------------------------------------- yaml

def load_yaml(path, what):
    if yaml is None:                                    # pragma: no cover
        raise Failure("PyYAML is missing",
                      "scripts/mail.sh builds a venv with it: run the command through that.")
    p = Path(path)
    if not p.exists():
        raise Refused(f"{what} not found at {p}")
    try:
        return yaml.safe_load(p.read_text()) or {}
    except Exception as e:
        raise Failure(f"{what} at {p} is not valid YAML: {e}")


# ---------------------------------------------------------------- time (pure)

REL_RE = re.compile(r"^(\d+)\s*(m|min|mins|minutes|h|hr|hrs|hours|d|day|days|w|week|weeks)$")
REL_UNITS = {"m": 60, "min": 60, "mins": 60, "minutes": 60,
             "h": 3600, "hr": 3600, "hrs": 3600, "hours": 3600,
             "d": 86400, "day": 86400, "days": 86400,
             "w": 604800, "week": 604800, "weeks": 604800}


def zone(name=DEFAULT_TZ):
    if ZoneInfo is None:                                # pragma: no cover
        return timezone.utc
    try:
        return ZoneInfo(name or DEFAULT_TZ)
    except Exception:
        raise Failure(f"unknown timezone {name!r}", "Use an IANA name like America/Los_Angeles.")


def duration(text):
    """'7d' | '90m' -> timedelta. Used by --since and by the older_than rule condition."""
    m = REL_RE.match(str(text or "").strip().lower())
    if not m:
        raise Failure(f"cannot read the duration {text!r}", "Use 24h, 90m, 7d, 2w.")
    return timedelta(seconds=int(m.group(1)) * REL_UNITS[m.group(2)])


def parse_since(text, now=None, tz_name=DEFAULT_TZ):
    """'24h' | '7d' | 'today' | 'yesterday' | ISO -> aware datetime in tz."""
    tz = zone(tz_name)
    now = (now or datetime.now(tz)).astimezone(tz)
    s = str(text or "").strip().lower()
    if not s:
        raise Failure("empty time value")
    if s == "now":
        return now
    if s in ("today", "yesterday"):
        d = now.date() - (timedelta(days=1) if s == "yesterday" else timedelta(0))
        return datetime(d.year, d.month, d.day, tzinfo=tz)
    if REL_RE.match(s):
        return now - duration(s)
    try:
        dt = datetime.fromisoformat(str(text).strip().replace("Z", "+00:00"))
    except ValueError:
        raise Failure(f"cannot read time {text!r}",
                      "Use an ISO date/time (2026-09-01, 2026-09-01T09:00), a relative window "
                      "(24h, 7d), 'today', or 'yesterday'.")
    return dt.replace(tzinfo=tz) if dt.tzinfo is None else dt.astimezone(tz)


def now_utc():
    return datetime.now(timezone.utc)


def stamp(dt=None):
    return (dt or now_utc()).astimezone(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- owner

def owner_handle():
    """The owner's roster handle: the identity that holds every verb (`--as <handle>`).

    `TICO_OWNER_HANDLE` names it outright. Otherwise it is the roster person whose email is
    `TICO_OWNER_EMAIL` (or `owner:` in registry/hub-access.yaml), else that email's local part,
    else the literal `owner`.
    """
    named = (os.environ.get("TICO_OWNER_HANDLE") or "").strip().lower()
    if named:
        return named
    email = (os.environ.get("TICO_OWNER_EMAIL") or "").strip().lower()
    try:
        if not email:
            email = str((yaml.safe_load((REGISTRY / "hub-access.yaml").read_text()) or {}).get("owner")
                        or "").strip().lower()
        if email:
            people = (yaml.safe_load((REGISTRY / "people.yaml").read_text()) or {}).get("people") or []
            for person in people:
                if isinstance(person, dict) and str(person.get("email") or "").strip().lower() == email \
                        and person.get("id"):
                    return str(person["id"]).strip().lower()
            return email.split("@")[0]
    except Exception:                                   # missing files or PyYAML: fall through
        pass
    return "owner"
