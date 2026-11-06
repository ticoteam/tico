"""API tools: a service bots call with a key (a `tools` row), its keys, who may use it at which level, its checks and
its calls; and Tools (`GET /api/v2/tool-index`), every way the company reaches the outside world in one list.

A tool holds at most one key per level: a `read` key and a `read_write` key, each a vault credential
(backend/credentials.py) with `tool_id` and `access_level`. Who may use a tool is the vault's grants on its keys, each
with its own `access_level`; a subject's level on a tool is the highest over its effective grants, a grant on a read
key counting as Read whatever the grant says. Setting a level grants the one key that level uses and takes the
subject's grants on the others away, so a run is never sent two values for the tool's variable. The proxy (track B,
backend/tool_proxy.py) asks `key_for` which key a caller's call uses, and `subject_level` what the caller may do.

A tool's `delivery` says how its key reaches a run: `proxy` (the server adds the key to each call, the key never
leaves it) or `computer` (the value goes to the run's environment, as every key did before Tools). Tools made from the
keys that existed before (the migration 20261105220550) are `computer` until a person moves them, and show "Key on
computers" under Needs you.

A check is one authenticated GET to the tool's `check_path` with each stored key: 2xx is Working, 401/403 Key rejected,
anything else (a timeout, a refused address, a 500) Check failed. A 401 is never "reachable". The scheduler checks every
proxied tool with a check path every CHECK_EVERY on a background thread; "Check now" runs one at once. A check sends
the key only to the tool's own https address, pinned to the public IP it resolved to (backend/egress.py), and nothing
about a key but its tool and id is logged or stored.

Who may do what, the vault's rules: a credential administrator (`credentials.administrator`) manages every tool and
sees every row. Anyone else reads the page of a tool they hold a grant on or added a key to, sees only their own
access row and the grants they made, and only the calls they or a bot they may see made. A bot reads the tools it is
granted. Key values appear in no response here; the vault's reveal stays the only door to one.

Who can see it (`tools.audience`, `visible`): NULL is Everyone, the rules above. A list (the audience shape pages use,
backend/teammate_access.py: people, groups with the groups nested in them, bots) is Only listed: the tool is then seen by
the company owner, whoever the list names and whoever can use it (an effective grant on one of its keys), and by nobody
else, not another credential administrator, not Tico support, not the person who once added a key. A tool someone may
not see is a 404 "No such tool" on every route here, its keys are left out of the vault's lists and refused by its
reveal, grant, change and delete as if they did not exist (backend/credentials.py `key_hidden`), and it lists nowhere:
not on Tools, not in a bot's tools, not by a name an Add would collide with. The owner, or a credential administrator who
can see the tool, changes it (`set_audience`, event `tool.audience_changed`); a non-owner who narrows it stays on the
list. The proxy follows grants alone, so it keeps working for whoever can use the tool.

A Finance connection (`fin_connections.credential_ref`, backend/finance) that reads one of a tool's keys shows under
Who can use it as Finance, read-only: it goes when the source is disconnected. Finance is not on the audience. While
it reads a key, deleting that key or its tool (or the vault's credential) is a 409 `key_in_use`; replacing its value
is fine.

tool-index rows other than API tools come from backend/connections.py's gatherers with their visibility already
applied (mailboxes from backend/mailboxes.py, whose rows say who may use each): Tico addresses and numbers, mailing
mail, Slack, GitHub, meeting importers, Listening and outgoing webhooks. Incoming webhooks and triggers are not tools:
they live in Automations. A tool a bot's bot.yaml declares with no tools row of its own is a `declared` row. tool_calls
and tool_checks older than RETENTION_DAYS go with the storage trim (`sweep`).
"""

import json
import logging
import re
import sqlite3
import threading
from datetime import date, datetime, timedelta, timezone

from fastapi import Query, Request
from pydantic import ConfigDict, Field, SecretStr

from . import tool_services as TS
from .credentials import CredentialWrite, administrator, effective_grant, owned, require_admin
from .models import AccessAudience, Contract
from .store import H, Problem, encode

log = logging.getLogger(__name__)

LEVELS = ("read", "read_write")
RANK = {"read": 1, "read_write": 2}
LEVEL_NAMES = {"read": "Read", "read_write": "Read and write"}
DELIVERIES = ("proxy", "computer")
CHECK_EVERY = timedelta(minutes=30)
CHECK_TIMEOUT = 10.0               # seconds for one check, connect to last byte
CHECKS_SHOWN = 24                  # the 24 squares on a tool's Check card
RECENT = 5                         # calls on a tool's Overview
EXPIRING_DAYS = 14                 # a key this close to its end date is under Needs you
RETENTION_DAYS = 90
WHO_MAX = 8                        # avatars on one tool-index row; `who_total` counts all
INDEX_LIMIT_MAX = 500
INDEX_BYTES_MAX = 192 * 1024
ACTIVITY_MAX = 200
RESULTS = ("sent", "refused", "failed", "use")
CHECK_PATH = re.compile(r"^/[A-Za-z0-9._~!$&'()*+,;=:@%/?-]{0,299}$")
DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# connections.py's statuses, as the tones and words Tools shows them in.
CONNECTION_STATUS = {"on": ("ok", "On"), "off": ("idle", "Off"), "waiting": ("wait", "Waiting"),
                     "paused": ("wait", "Paused"), "error": ("fail", "Error")}
# Declared tools Tools already lists under their own rows: Slack, GitHub, and Google's mail and calendar.
OWN_ROWS = re.compile(r"^(slack|github|github-app|gmail|google-.*)$")


# ---- levels and keys (the proxy's two helpers first: track B imports them by these names)

def subject_of(c, subject):
    """`bot:<id>` for a bot named by id or slug, else the subject as given (`human:<id>`)."""
    subject = str(subject or "")
    if subject.startswith("bot:"):
        return "bot:" + (H.resolve_bot(c, subject) or {}).get("id", H.actor_id(subject))
    return subject


def _keys(c, tool_id):
    return c.execute("SELECT * FROM credentials WHERE tool_id=? ORDER BY access_level, created_at, id",
                     (tool_id,)).fetchall()


def _held(c, tool_id, subject):
    """[(key row, level)] for each of the tool's keys `subject` holds an effective grant to."""
    out = []
    for key in _keys(c, tool_id):
        grant = effective_grant(c, key["id"], subject)
        if grant is not None:
            level = "read" if key["access_level"] == "read" or grant["access_level"] == "read" else "read_write"
            out.append((key, level))
    return out


def subject_level(c, tool_id, subject):
    """`read`, `read_write` or None: the highest level `subject` holds on the tool over its effective grants."""
    levels = [level for _key, level in _held(c, tool_id, subject_of(c, subject))]
    return max(levels, key=RANK.get) if levels else None


def tool_row(c, tool_id):
    row = c.execute("SELECT * FROM tools WHERE id=?", (str(tool_id or ""),)).fetchone()
    if not row:
        raise Problem("not_found", "No such tool", 404)
    return row


def _label(c, subject):
    from . import names
    return names.lookup(c, {subject}).get(subject) or H.actor_id(subject)


def key_for(c, tool_id, subject):
    """(credential row, level) a call by `subject` through the tool uses: at Read the read key when there is a stored
    one, else the Read and write key held at Read (the proxy then lets only GET, HEAD and OPTIONS through); at Read and
    write the Read and write key. Raises a 403 Problem `tool_access` whose detail names the setting that would allow
    it."""
    tool = tool_row(c, tool_id)
    subject = subject_of(c, subject)
    held = [(key, level) for key, level in _held(c, tool_id, subject) if key["ciphertext"] is not None]
    if not held:
        if _held(c, tool_id, subject):
            raise Problem("tool_access", f"{tool['name']} has no stored key. Add one in Tools > {tool['name']} > Keys", 403)
        raise Problem("tool_access", f"{_label(c, subject)} has no access to {tool['name']}. Set a level in Tools > "
                      f"{tool['name']} > Who can use it", 403)
    level = max((level for _key, level in held), key=RANK.get)
    if level == "read":
        held.sort(key=lambda pair: pair[0]["access_level"] != "read")      # the read key first
    else:
        held = [(key, lv) for key, lv in held if lv == "read_write"]
    return held[0][0], level


# ---- status

def _today():
    return datetime.now(timezone.utc).date()


def _days_left(on, today):
    try:
        return (date.fromisoformat(on) - today).days
    except (TypeError, ValueError):
        return None


def _check_text(ok, status_code):
    return "Working" if ok else "Key rejected" if status_code in (401, 403) else "Check failed"


def latest_checks(c, tool_id):
    """{credential id: its newest check row}."""
    out = {}
    for row in c.execute("SELECT * FROM tool_checks WHERE tool_id=? ORDER BY checked_at DESC LIMIT 50", (tool_id,)):
        out.setdefault(row["credential_id"], row)
    return out


def status(tool, keys, checks, today=None):
    """{tone, text, needs_you} for an API tool. Worst first: no stored key, a failed check, an expired key; then the
    key still on computers and a key expiring within EXPIRING_DAYS (Needs you, not broken); then Working once every
    stored key passed its last check, else Not checked."""
    today = today or _today()
    stored = [k for k in keys if k["ciphertext"] is not None]
    if not stored:
        return {"tone": "fail", "text": "No key", "needs_you": True}
    last = [checks.get(k["id"]) for k in stored]
    failed = [r for r in last if r is not None and not r["ok"]]
    if failed:
        return {"tone": "fail", "text": _check_text(False, failed[0]["status_code"]), "needs_you": True}
    left = [d for d in (_days_left(k["expires_on"], today) for k in stored) if d is not None]
    if left and min(left) < 0:
        return {"tone": "fail", "text": "Key expired", "needs_you": True}
    if tool["delivery"] == "computer":
        return {"tone": "wait", "text": "Key on computers", "needs_you": True}
    if left and min(left) <= EXPIRING_DAYS:
        n = min(left)
        return {"tone": "wait", "text": "Expires today" if n == 0 else f"Expires in {n} day{'s' if n != 1 else ''}",
                "needs_you": True}
    if all(r is not None for r in last):
        return {"tone": "ok", "text": "Working", "needs_you": False}
    return {"tone": "idle", "text": "Not checked", "needs_you": False}


# ---- who may see what

def _admin(c, who, admins):
    return administrator(c, who, admins)


def may_read(c, who, tool_id, admins):
    """A credential administrator, anyone holding an effective grant on one of its keys, or the person who added one."""
    if who.support or who.role not in ("owner", "human", "bot"):
        return False
    if who.role in ("owner", "human") and _admin(c, who, admins):
        return True
    keys = [k["id"] for k in _keys(c, tool_id)]
    if any(effective_grant(c, k, who.actor) for k in keys):
        return True
    return bool(set(keys) & owned(c, who))


AUDIENCE_LISTS = ("people", "teams", "bots")


def _field(row, name):
    try:
        return row[name]
    except (IndexError, KeyError):
        return None


def audience_of(tool):
    """A tool's Only listed audience as one level ({everyone: False, people, teams, bots}), or None for Everyone. A stored
    value that cannot be read lists nobody: a private tool never opens up by accident."""
    raw = _field(tool, "audience")
    if raw is None:
        return None
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        value = None
    value = value if isinstance(value, dict) else {}
    return {"everyone": False, **{k: [str(v) for v in value[k] if v] if isinstance(value.get(k), list) else []
                                  for k in AUDIENCE_LISTS}}


def _can_use(c, tool_id, actor):
    return any(effective_grant(c, k["id"], actor) for k in _keys(c, tool_id))


def visible(c, who, tool, admins, auth=None):
    """Whether `who` may know this tool exists: the one rule every route, list and the vault follow. `tool` is its row
    (or its id). Everyone (audience NULL) is `may_read`; Only listed is the owner, whoever the audience names, and
    whoever can use it."""
    if isinstance(tool, str):
        tool = c.execute("SELECT * FROM tools WHERE id=?", (tool,)).fetchone()
        if tool is None:
            return False
    level = audience_of(tool)
    if level is None:
        return may_read(c, who, tool["id"], admins)
    role = getattr(who, "role", "")
    if getattr(who, "support", None) or role not in ("owner", "human", "bot"):
        return False
    if role == "owner":
        return True
    from . import teammate_access
    if teammate_access.in_audience(c, who, level, auth):
        return True
    return _can_use(c, tool["id"], who.actor)


def private_tools(c):
    """Every Only listed tool's row."""
    try:
        return c.execute("SELECT * FROM tools WHERE audience IS NOT NULL").fetchall()
    except sqlite3.OperationalError as exc:      # a file before the tools migrations
        if "no such" not in str(exc):
            raise
        return []


def hidden_keys(c, who, admins, auth=None):
    """The ids of the keys (vault credentials) whose tool `who` may not see: a list leaves them out."""
    out = set()
    for tool in private_tools(c):
        if not visible(c, who, tool, admins, auth):
            out.update(k["id"] for k in _keys(c, tool["id"]))
    return out


def key_hidden(c, who, credential_id, admins, auth=None):
    """Whether this credential is a key of a tool `who` may not see: the vault then answers as if it did not exist."""
    try:
        tool = c.execute("SELECT t.* FROM credentials k JOIN tools t ON t.id=k.tool_id WHERE k.id=? "
                         "AND t.audience IS NOT NULL", (str(credential_id or ""),)).fetchone()
    except sqlite3.OperationalError as exc:
        if "no such" not in str(exc):
            raise
        return False
    return tool is not None and not visible(c, who, tool, admins, auth)


def _readable(c, who, tool_id, admins, auth=None):
    """The tool's row, or a 404 "No such tool" when it does not exist or `who` may not see it."""
    tool = c.execute("SELECT * FROM tools WHERE id=?", (str(tool_id or ""),)).fetchone()
    if tool is None or not visible(c, who, tool, admins, auth):
        raise Problem("not_found", "No such tool", 404)
    return tool


def may_set_audience(c, who, tool, admins, auth=None):
    """The owner, or a credential administrator who can see the tool now."""
    if getattr(who, "role", "") == "owner" and not getattr(who, "support", None):
        return True
    return _admin(c, who, admins) and visible(c, who, tool, admins, auth)


def _initials(name):
    words = [w for w in re.split(r"[\s._@-]+", str(name or "")) if w]
    return "".join(w[0] for w in words[:2]).upper() or "?"


def _person(subject, name):
    kind = "bot" if subject.startswith("bot:") else "person"
    return {"kind": kind, "id": H.actor_id(subject), "name": name, "initials": _initials(name)}


def access_rows(c, who, tool_id, admins, auth=None):
    """Who can use the tool: one row per subject with an effective grant, its level and last use. Anyone but an
    administrator sees only their own row and the grants they made."""
    from . import names
    admin = _admin(c, who, admins)
    keys = {k["id"]: k for k in _keys(c, tool_id)}
    found = {}
    for key_id, key in keys.items():
        for grant in c.execute("SELECT * FROM credential_grants WHERE credential_id=? AND revoked_at IS NULL", (key_id,)):
            subject = grant["subject"]
            if not subject.startswith(("bot:", "human:")) or effective_grant(c, key_id, subject) is None:
                continue
            if not (admin or subject == who.actor or grant["granted_by"] == who.actor):
                continue
            level = "read" if key["access_level"] == "read" or grant["access_level"] == "read" else "read_write"
            if RANK[level] > RANK.get(found.get(subject), 0):
                found[subject] = level
    if not found:
        return []
    label = names.lookup(c, set(found))
    used = {}
    marks = ",".join("?" * len(keys))
    for row in c.execute(f"SELECT actor, bot, used_at FROM credential_uses WHERE credential_id IN ({marks})", list(keys)):
        subject = "bot:" + row["bot"] if row["bot"] else row["actor"]
        if row["used_at"] > used.get(subject, ""):
            used[subject] = row["used_at"]
    for row in c.execute("SELECT actor, bot, max(created_at) AS at FROM tool_calls WHERE tool_id=? GROUP BY actor, bot",
                         (tool_id,)):
        subject = "bot:" + row["bot"] if row["bot"] else row["actor"]
        if (row["at"] or "") > used.get(subject, ""):
            used[subject] = row["at"]
    out = []
    for subject, level in found.items():
        name = label.get(subject) or H.actor_id(subject)
        out.append({"subject": subject, **_person(subject, name), "level": level, "last_used_at": used.get(subject)})
    return sorted(out, key=lambda r: (r["kind"] != "person", r["name"].lower()))


def _finance_sources(c, key_ids):
    """The Finance connections (fin_connections: reading its status and key is allowed outside backend/finance) that
    read one of these keys and are not disconnected, oldest first; none on an install without Finance's tables."""
    ids = [str(k) for k in key_ids if k]
    if not ids:
        return []
    try:
        return c.execute(f"SELECT credential_ref, name, provider, last_sync_at FROM fin_connections WHERE credential_ref "
                         f"IN ({','.join('?' * len(ids))}) AND status<>'disconnected' ORDER BY created_at, id",
                         ids).fetchall()
    except sqlite3.OperationalError as exc:      # no Finance here
        if "no such table" not in str(exc):
            raise
        return []


def finance_rows(c, tool_id):
    """Who can use it, Finance's part: one row while a Finance connection (fin_connections, on installs with Finance)
    reads one of the tool's keys, at Read and write when that is the Read and write key, last used at its last sync.
    Read-only here: it goes when the source is disconnected."""
    keys = {k["id"]: k for k in _keys(c, tool_id)}
    level, used = None, None
    for row in _finance_sources(c, keys):
        lv = "read_write" if keys[row["credential_ref"]]["access_level"] == "read_write" else "read"
        level = lv if RANK[lv] > RANK.get(level, 0) else level
        if (row["last_sync_at"] or "") > (used or ""):
            used = row["last_sync_at"]
    if level is None:
        return []
    return [{"subject": "finance", "kind": "finance", "id": "finance", "name": "Finance", "initials": "F", "level": level,
             "last_used_at": used, "fixed": True}]


def seen_by(c, tool, auth=None):
    """Who can see an Only listed tool, and why, for whoever may change it: the owner ("Owner"), each audience entry
    ("Listed"), then each person, bot and Finance that can use it ("Can use it"). Empty for Everyone."""
    level = audience_of(tool)
    if level is None:
        return []
    from . import names, views
    rows, seen = [], set()

    def add(subject, kind, name, why):
        if subject in seen:
            return
        seen.add(subject)
        rows.append({"subject": subject, "kind": kind, "id": subject.split(":", 1)[-1], "name": name, "why": why})
    owner = auth.owner_id(c) if auth is not None else ""
    listed = {"human:" + p for p in level["people"]} | {"bot:" + b for b in level["bots"]}
    grants = {r["subject"] for r in access_rows_all(c, tool["id"])}
    label = names.lookup(c, ({"human:" + owner} if owner else set()) | listed | grants)
    if owner:
        add("human:" + owner, "person", label.get("human:" + owner) or owner, "Owner")
    groups = views.roster(c, shared=True).get("org_groups") or {}
    for pid in level["people"]:
        add("human:" + pid, "person", label.get("human:" + pid) or pid, "Listed")
    for gid in level["teams"]:
        add("group:" + gid, "group", str((groups.get(gid) or {}).get("name") or gid), "Listed")
    for slug in level["bots"]:
        add("bot:" + slug, "bot", label.get("bot:" + slug) or slug, "Listed")
    for subject in sorted(grants, key=lambda s: (not s.startswith("human:"), (label.get(s) or s).lower())):
        add(subject, "bot" if subject.startswith("bot:") else "person", label.get(subject) or H.actor_id(subject),
            "Can use it")
    if finance_rows(c, tool["id"]):
        add("finance", "finance", "Finance", "Can use it")
    return rows


def access_rows_all(c, tool_id):
    """[{subject, level}] for every person and bot with an effective grant on one of the tool's keys."""
    found = {}
    for key in _keys(c, tool_id):
        for grant in c.execute("SELECT * FROM credential_grants WHERE credential_id=? AND revoked_at IS NULL", (key["id"],)):
            subject = grant["subject"]
            if subject.startswith(("bot:", "human:")) and effective_grant(c, key["id"], subject) is not None:
                level = "read" if key["access_level"] == "read" or grant["access_level"] == "read" else "read_write"
                if RANK[level] > RANK.get(found.get(subject), 0):
                    found[subject] = level
    return [{"subject": s, "level": lv} for s, lv in found.items()]


def _key_view(c, who, key, admin, admins):
    from .credentials import last_used, permitted
    shown = admin or permitted(c, who, key["id"], admins)
    return {"id": key["id"], "name": key["name"], "preview": key["preview"] if shown else "",
            "access_level": key["access_level"], "expires_on": key["expires_on"], "env": key["env"],
            "username": key["username"] if shown else "", "stored": key["ciphertext"] is not None,
            "created_by": key["created_by"] or key["updated_by"], "created_at": key["created_at"], **last_used(c, key["id"])}


def _tool_view(tool):
    return {**{k: tool[k] for k in ("id", "name", "service", "base_url", "auth_scheme", "check_path", "delivery")},
            "private": _field(tool, "audience") is not None}


def _call_view(row):
    return {"id": row["id"], "kind": "call", "actor": row["actor"], "bot": row["bot"], "access_level": row["access_level"],
            "method": row["method"], "path": row["path"], "status_code": row["status_code"], "result": row["result"],
            "detail": row["detail"], "duration_ms": row["duration_ms"], "at": row["created_at"]}


def _call_visible(c, who, auth, admin, row):
    if admin or row["actor"] == who.actor or (row["bot"] and who.actor == "bot:" + row["bot"]):
        return True
    if not row["bot"] or auth is None or who.role not in ("owner", "human"):
        return False
    return bool((auth.bot_accesses(c, who, [row["bot"]]).get(row["bot"]) or {}).get("see"))


def detail(c, who, tool_id, admins, auth=None):
    """`GET /api-tools/{id}`: the tool, its keys (never a value), who can use it, its last checks and calls."""
    tool = _readable(c, who, tool_id, admins, auth)
    admin = _admin(c, who, admins)
    keys = _keys(c, tool_id)
    checks = [{"ok": bool(r["ok"]), "status_code": r["status_code"], "detail": r["detail"], "checked_at": r["checked_at"],
               "credential_id": r["credential_id"]}
              for r in c.execute("SELECT * FROM tool_checks WHERE tool_id=? ORDER BY checked_at DESC, id DESC LIMIT ?",
                                 (tool_id, CHECKS_SHOWN))]
    recent = []
    for row in c.execute("SELECT * FROM tool_calls WHERE tool_id=? ORDER BY created_at DESC, id DESC LIMIT 200", (tool_id,)):
        if _call_visible(c, who, auth, admin, row):
            recent.append(_call_view(row))
            if len(recent) == RECENT:
                break
    state = status(tool, keys, latest_checks(c, tool_id))
    out = {"tool": _tool_view(tool), "keys": [_key_view(c, who, k, admin, admins) for k in keys],
           "access": access_rows(c, who, tool_id, admins, auth) + (finance_rows(c, tool_id) if admin else []),
           "checks": checks, "recent": recent, "status": {"tone": state["tone"], "text": state["text"]},
           "needs_you": state["needs_you"], "can_manage": admin, "private": _field(tool, "audience") is not None,
           "can_set_audience": False}
    if may_set_audience(c, who, tool, admins, auth):
        out.update(can_set_audience=True, audience=audience_of(tool), seen_by=seen_by(c, tool, auth))
    return out


def activity(c, who, tool_id, admins, auth, cursor="", limit=50, result=""):
    """Calls through the proxy and, for a tool whose key goes to computers, the newest time each caller's run took a
    key (credential_uses keeps one row a caller), newest first. A proxied tool's uses are its calls, already listed, so
    its use rows are left out. `cursor` is the last row's `<at>|<id>`."""
    proxied = _readable(c, who, tool_id, admins, auth)["delivery"] == "proxy"
    admin = _admin(c, who, admins)
    at, _, last = str(cursor or "").partition("|")
    if cursor and not last:
        raise Problem("invalid_request", "cursor is the `next` of the page before", 422)
    if result and result not in RESULTS:
        raise Problem("invalid_request", "result is one of " + ", ".join(RESULTS), 422)
    limit = max(1, min(int(limit or 50), ACTIVITY_MAX))
    rows = []
    if result != "use":
        sql, args = "SELECT * FROM tool_calls WHERE tool_id=?", [tool_id]
        if result:
            sql, args = sql + " AND result=?", args + [result]
        if cursor:
            sql, args = sql + " AND (created_at<? OR (created_at=? AND id<?))", args + [at, at, last]
        for row in c.execute(sql + " ORDER BY created_at DESC, id DESC LIMIT ?", (*args, ACTIVITY_MAX * 2)):
            if _call_visible(c, who, auth, admin, row):
                rows.append(_call_view(row))
    if result in ("", "use") and not proxied:
        keys = {k["id"]: k for k in _keys(c, tool_id)}
        if keys:
            marks = ",".join("?" * len(keys))
            for row in c.execute(f"SELECT * FROM credential_uses WHERE credential_id IN ({marks})", list(keys)):
                ident = f"use:{row['credential_id']}:{row['actor']}:{row['bot']}"
                if cursor and (row["used_at"], ident) >= (at, last):
                    continue
                probe = {"actor": row["actor"], "bot": row["bot"]}
                if not _call_visible(c, who, auth, admin, probe):
                    continue
                rows.append({"id": ident, "kind": "use", "actor": row["actor"], "bot": row["bot"],
                             "access_level": keys[row["credential_id"]]["access_level"], "credential_id": row["credential_id"],
                             "result": "use", "at": row["used_at"]})
    rows.sort(key=lambda r: (r["at"], r["id"]), reverse=True)
    shown = rows[:limit]
    more = len(rows) > limit
    return {"rows": shown, "next": f"{shown[-1]['at']}|{shown[-1]['id']}" if more and shown else None}


# ---- checks

def send_check(url, headers, params):
    """One GET; its status code. Tests replace it. Only to a public https address, pinned to the IP it resolved to,
    no redirects followed, no proxy from the environment, at most CHECK_TIMEOUT seconds."""
    import httpx
    from . import egress
    parts = egress.check(url)
    address = egress.address_for(parts, budget=CHECK_TIMEOUT)
    pinned = httpx.URL(url, params=params or None).copy_with(host=address)
    host = parts.hostname + (f":{parts.port}" if parts.port else "")
    with httpx.Client(timeout=CHECK_TIMEOUT, follow_redirects=False, trust_env=False) as client:
        with client.stream("GET", pinned, headers={**headers, "Host": host},
                           extensions={"sni_hostname": parts.hostname}) as response:
            return response.status_code


def _check_one(tool, key, secret):
    """(ok, status_code, detail) for one key. An exception's text is never kept: a query-param key would be in it."""
    from . import egress
    service = TS.SERVICES.get(tool["service"]) or {}
    username = key["username"] or service.get("username") or ""
    try:
        headers, params = TS.apply_auth(tool["auth_scheme"], secret, username)
    except ValueError:
        return False, None, "Check failed: unknown auth scheme"
    headers = {"Accept": "application/json", **TS.check_headers(tool["service"]), **headers,
               "User-Agent": "Tico-tool-check"}
    url = tool["base_url"].rstrip("/") + tool["check_path"]
    try:
        code = send_check(url, headers, params)
    except egress.Blocked:
        return False, None, "Check failed: address refused"
    except Exception as exc:  # noqa: BLE001 - every network failure is one answer: the check failed
        return False, None, "Check failed: " + ("timed out" if "Timeout" in type(exc).__name__ else "no answer")
    ok = 200 <= code < 300
    return ok, code, _check_text(ok, code) if ok or code in (401, 403) else f"Check failed: {code}"


def check_tool(store, vault, tool_id):
    """Check each stored key of the tool and record a tool_checks row for it; the rows written, worst first."""
    with store.transaction() as c:                 # decrypt may reseal an old value in place
        tool = tool_row(c, tool_id)
        if not tool["base_url"] or not tool["check_path"]:
            raise Problem("check", f"{tool['name']} has no check. Set its address and check path in Tools > "
                          f"{tool['name']} > Settings", 409)
        stored = [(k, vault.cipher.decrypt(c, k)) for k in _keys(c, tool_id) if k["ciphertext"] is not None]
    if not stored:
        raise Problem("check", f"{tool['name']} has no stored key. Add one in Tools > {tool['name']} > Keys", 409)
    results = [(key, *_check_one(tool, key, secret)) for key, secret in stored]
    stored = None                                  # the values leave scope before anything is written
    rows = []
    with store.transaction() as c:
        for key, ok, code, text in results:
            row = {"id": H.new_id(), "tool_id": tool_id, "credential_id": key["id"], "ok": int(ok),
                   "status_code": code, "detail": text, "checked_at": H.now()}
            c.execute("INSERT INTO tool_checks(id,tool_id,credential_id,ok,status_code,detail,checked_at) "
                      "VALUES(:id,:tool_id,:credential_id,:ok,:status_code,:detail,:checked_at)", row)
            rows.append(row | {"ok": ok})
    rows.sort(key=lambda r: r["ok"])
    return rows


_checking = threading.Lock()


def due(c, now=None):
    """Proxied tools with an address, a check path and a stored key whose newest check is older than CHECK_EVERY."""
    cutoff = H.shift(now or H.now(), seconds=-int(CHECK_EVERY.total_seconds()))
    return [r[0] for r in c.execute(
        "SELECT t.id FROM tools t WHERE t.delivery='proxy' AND t.base_url<>'' AND t.check_path<>'' "
        "AND EXISTS(SELECT 1 FROM credentials k WHERE k.tool_id=t.id AND k.ciphertext IS NOT NULL) "
        "AND coalesce((SELECT max(checked_at) FROM tool_checks k WHERE k.tool_id=t.id),'')<? ORDER BY t.id", (cutoff,))]


def check_soon(store, settings=None, vault=None):
    """The due checks on a background thread (the scheduler calls this every few minutes; a tick never waits on a
    vendor). One sweep at a time; a demo install checks nothing. Returns the thread, or None."""
    settings = settings or store.settings
    if getattr(settings, "demo", False):
        return None
    with store.read() as c:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='tools'").fetchone():
            return None
        ids = due(c)
    if not ids or not _checking.acquire(blocking=False):
        return None
    if vault is None:
        from .credentials import Vault
        vault = Vault(store)

    def run():
        try:
            for tool_id in ids:
                try:
                    check_tool(store, vault, tool_id)
                except Exception as exc:  # noqa: BLE001 - one tool's failure never stops the others
                    log.warning("tool check %s failed: %s", tool_id, type(exc).__name__)
        finally:
            _checking.release()
    thread = threading.Thread(target=run, name="tool-checks", daemon=True)
    thread.start()
    return thread


def sweep(store, now=None):
    """The storage trim: tool calls and checks older than RETENTION_DAYS."""
    cutoff = H.shift(now or H.now(), days=-RETENTION_DAYS)
    with store.transaction() as c:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='tools'").fetchone():
            return {"tool_calls": 0, "tool_checks": 0}
        calls = c.execute("DELETE FROM tool_calls WHERE created_at<?", (cutoff,)).rowcount
        checks = c.execute("DELETE FROM tool_checks WHERE checked_at<?", (cutoff,)).rowcount
    return {"tool_calls": calls, "tool_checks": checks}


# ---- changes

class ToolCreate(Contract):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    service: str = Field(default="", max_length=60)
    base_url: str = Field(default="", max_length=300)
    auth_scheme: str = Field(default="", max_length=80)
    check_path: str = Field(default="", max_length=300)


class ToolUpdate(Contract):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=80)
    service: str | None = Field(default=None, max_length=60)
    base_url: str | None = Field(default=None, max_length=300)
    auth_scheme: str | None = Field(default=None, max_length=80)
    check_path: str | None = Field(default=None, max_length=300)
    delivery: str | None = Field(default=None, pattern=r"^(proxy|computer)$")
    # Who can see it: null is Everyone, an audience Only listed (`set_audience`). Left out, it stays as it is.
    audience: AccessAudience | None = None


class KeyCreate(Contract):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    name: str = Field(min_length=1, max_length=150)
    value: SecretStr = Field(min_length=1, max_length=100_000)
    username: str = Field(default="", max_length=250)
    access_level: str = Field(default="read_write", pattern=r"^(read|read_write)$")
    expires_on: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    env: str = Field(default="", max_length=100, pattern=r"^$|^[A-Z_][A-Z0-9_]*$")


class KeyUpdate(Contract):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    name: str | None = Field(default=None, min_length=1, max_length=150)
    value: SecretStr | None = Field(default=None, min_length=1, max_length=100_000)
    username: str | None = Field(default=None, max_length=250)
    access_level: str | None = Field(default=None, pattern=r"^(read|read_write)$")
    expires_on: str | None = Field(default=None, pattern=r"^$|^\d{4}-\d{2}-\d{2}$")


class AccessSet(Contract):
    model_config = ConfigDict(extra="forbid")
    level: str = Field(pattern=r"^(read|read_write|none)$")


class _Receipt:
    """What `Vault.change` fingerprints for a body with a key value: the value itself (keyed HMAC, never stored)."""

    def __init__(self, body, **extra):
        self.body, self.extra = body, extra

    def model_dump(self):
        out = self.body.model_dump() if self.body is not None else {}
        for name, value in list(out.items()):
            if isinstance(value, SecretStr):
                out[name] = value.get_secret_value()
        return out | self.extra


_slug = TS.slug


def _unique_id(c, base, who=None, admins=()):
    """`base`, else `base-2`, `base-3`...; an Only listed tool `who` may not see is never counted out loud: past one,
    the id takes a random suffix instead, so a new tool's id does not say a hidden one of that name exists."""
    tid, n = base, 1
    while (row := c.execute("SELECT * FROM tools WHERE id=?", (tid,)).fetchone()) is not None:
        if who is not None and row["audience"] is not None and not visible(c, who, row, admins):
            while c.execute("SELECT 1 FROM tools WHERE id=?", (tid := f"{base}-{H.new_id()[:8].lower()}",)).fetchone():
                pass
            return tid
        n += 1
        tid = f"{base}-{n}"
    return tid


def _clean(fields):
    """Validated tool fields: an https base URL with no credentials, query or fragment; a known auth scheme; a check
    path that is a path."""
    from . import egress
    from urllib.parse import urlsplit
    out = dict(fields)
    if out.get("base_url"):
        url = out["base_url"].strip().rstrip("/")
        try:
            egress.check(url)
        except egress.Blocked as exc:
            raise Problem("base_url", f"Address: {exc}", 422) from None
        parts = urlsplit(url)
        if parts.query or parts.fragment:
            raise Problem("base_url", "Address: no query or fragment", 422)
        out["base_url"] = url
    if out.get("auth_scheme") and not TS.AUTH_SCHEME.match(out["auth_scheme"]):
        raise Problem("auth_scheme", "Auth is bearer, basic_user, basic_password, header:<Name> or query:<param>", 422)
    if out.get("check_path") and not CHECK_PATH.match(out["check_path"]):
        raise Problem("check_path", "Check path starts with / and is a path on the tool's address", 422)
    if "service" in out and out["service"] is not None:
        out["service"] = out["service"].strip().lower()
    return out


def create_tool(c, who, body, admins):
    require_admin(c, who, admins)
    fields = _clean(body.model_dump())
    service = TS.match(service=fields["service"], name=fields["name"])
    known = TS.defaults(service)
    # A known service's check path belongs to its own address: a tool given another address gets none by default.
    own = not fields["base_url"] or fields["base_url"] == known.get("base_url")
    tool = {"id": _unique_id(c, _slug(fields["name"]), who, admins), "name": fields["name"].strip(),
            "service": service or fields["service"] or "",
            "base_url": fields["base_url"] or known.get("base_url", ""),
            "auth_scheme": fields["auth_scheme"] or known.get("auth_scheme", "bearer"),
            "check_path": fields["check_path"] or (known.get("check_path", "") if own else ""),
            "created_by": who.actor, "created_at": H.now()}
    tool["delivery"] = "proxy" if tool["base_url"] else "computer"
    tool["updated_at"] = tool["created_at"]
    c.execute("INSERT INTO tools(id,name,service,base_url,auth_scheme,check_path,delivery,created_by,created_at,updated_at) "
              "VALUES(:id,:name,:service,:base_url,:auth_scheme,:check_path,:delivery,:created_by,:created_at,:updated_at)",
              tool)
    H.event(c, who.actor, "tool.created", tool["id"], {"service": tool["service"], "delivery": tool["delivery"]})
    return _tool_view(tool_row(c, tool["id"]))


def update_tool(c, who, tool_id, body, admins, auth=None):
    """A tool's settings, and its Who can see it (`audience`, which the owner may change too)."""
    asked = body.model_dump(exclude_none=True, exclude={"audience"})
    audience_set = "audience" in body.model_fields_set
    if asked or not audience_set or getattr(who, "role", "") != "owner":
        require_admin(c, who, admins)
    tool = dict(_readable(c, who, tool_id, admins, auth))
    if audience_set:
        set_audience(c, who, tool, body.audience.model_dump() if body.audience is not None else None, admins, auth)
        tool = dict(tool_row(c, tool_id))
    fields = {k: v for k, v in _clean(asked).items()}
    merged = tool | fields
    if merged["delivery"] == "proxy" and not merged["base_url"]:
        raise Problem("delivery", f"Set {tool['name']}'s address before moving it to the proxy", 422)
    if merged == tool:
        return _tool_view(tool_row(c, tool_id))
    c.execute("UPDATE tools SET name=?,service=?,base_url=?,auth_scheme=?,check_path=?,delivery=?,updated_at=? WHERE id=?",
              (merged["name"].strip(), merged["service"], merged["base_url"], merged["auth_scheme"], merged["check_path"],
               merged["delivery"], H.now(), tool_id))
    H.event(c, who.actor, "tool.updated", tool_id, {k: merged[k] for k in fields if k != "name"} | (
        {"name": merged["name"]} if "name" in fields else {}))
    return _tool_view(tool_row(c, tool_id))


def finance_user(c, key_ids):
    """The name of a Finance connection (fin_connections, on installs with Finance) that still reads one of these keys:
    any status but disconnected. None when none does."""
    rows = _finance_sources(c, key_ids)
    return (rows[0]["name"] or rows[0]["provider"] or "it") if rows else None


def refuse_finance_key(c, key_ids):
    """A 409 `key_in_use` while Finance reads one of these keys: deleting it would break that source. Replacing a key's
    value stays allowed."""
    name = finance_user(c, key_ids)
    if name is not None:
        raise Problem("key_in_use", f"Finance uses this key. Disconnect {name} in Finance › Sources first.", 409)


def _drop_key(c, who, key_id):
    from .computer_keys import before_delete
    refuse_finance_key(c, [key_id])
    before_delete(c, who, key_id)
    c.execute("DELETE FROM credential_grants WHERE credential_id=?", (key_id,))
    c.execute("DELETE FROM credentials WHERE id=?", (key_id,))


def set_audience(c, who, tool, value, admins, auth=None):
    """Who can see the tool: None (Everyone) or an audience (Only listed). The owner, or a credential administrator who
    can see it; one who is not the owner stays on the list they set, so they never lock themselves out. Event
    `tool.audience_changed` with the before and after."""
    from . import teammate_access
    if not may_set_audience(c, who, tool, admins, auth):
        raise Problem("forbidden", "Only the owner, or a credential administrator who can see it, changes who can see "
                      f"{tool['name']}", 403)
    stored = None
    if value is not None:
        level = teammate_access.parse_audience(c, value)
        if not level["everyone"]:
            level = {"everyone": False, **{k: list(level[k]) for k in AUDIENCE_LISTS}}
            if getattr(who, "role", "") != "owner" and not teammate_access.in_audience(c, who, level, auth):
                level["people"].append(H.actor_id(who.actor))
            stored = json.dumps(level, sort_keys=True, separators=(",", ":"))
    if stored == _field(tool, "audience"):
        return
    before = audience_of(tool)
    c.execute("UPDATE tools SET audience=?, updated_at=? WHERE id=?", (stored, H.now(), tool["id"]))
    H.event(c, who.actor, "tool.audience_changed", tool["id"],
            {"before": before, "after": audience_of({"audience": stored})})


def forget_audience_member(c, actor, field, ident):
    """A removed person, group or bot leaves every tool's audience (backend/pages.py's hook calls this): a teammate made
    later under the same id is never on a list by inheritance. A list left empty stays Only listed."""
    for tool in private_tools(c):
        before = audience_of(tool)
        if ident not in before[field]:
            continue
        after = {**before, field: [v for v in before[field] if v != ident]}
        c.execute("UPDATE tools SET audience=?, updated_at=? WHERE id=?",
                  (json.dumps(after, sort_keys=True, separators=(",", ":")), H.now(), tool["id"]))
        H.event(c, actor, "tool.audience_changed", tool["id"], {"before": before, "after": after, "removed": ident})


def delete_tool(c, who, tool_id, admins):
    """The tool, its checks and calls, and its keys with their grants: a key belongs to its tool."""
    require_admin(c, who, admins)
    tool = _readable(c, who, tool_id, admins)
    keys = [k["id"] for k in _keys(c, tool_id)]
    refuse_finance_key(c, keys)                       # before anything goes
    for key_id in keys:
        _drop_key(c, who, key_id)
    c.execute("DELETE FROM tool_calls WHERE tool_id=?", (tool_id,))
    c.execute("DELETE FROM tool_checks WHERE tool_id=?", (tool_id,))
    c.execute("DELETE FROM tools WHERE id=?", (tool_id,))
    H.event(c, who.actor, "tool.deleted", tool_id, {"name": tool["name"], "keys": len(keys)})
    return {"deleted": True, "id": tool_id}


def _key_at(c, tool_id, level, other_than=None):
    return c.execute("SELECT * FROM credentials WHERE tool_id=? AND access_level=? AND id<>?",
                     (tool_id, level, other_than or "")).fetchone()


def _taken(tool, level):
    return Problem("key_level", f"{tool['name']} already has a {LEVEL_NAMES[level]} key. Replace its value in Tools > "
                   f"{tool['name']} > Keys", 409)


def add_key(c, who, vault, tool_id, body):
    """A new key for the tool at its level, stored by the vault under the tool's variable."""
    require_admin(c, who, vault.admins)
    tool = _readable(c, who, tool_id, vault.admins)
    if _key_at(c, tool_id, body.access_level):
        raise _taken(tool, body.access_level)
    _day(body.expires_on)
    sibling = c.execute("SELECT env FROM credentials WHERE tool_id=? AND env<>'' LIMIT 1", (tool_id,)).fetchone()
    env = body.env or (sibling["env"] if sibling else "") or TS.defaults(tool["service"]).get("env") or \
        re.sub(r"[^A-Z0-9]+", "_", tool["id"].upper()).strip("_") + "_API_KEY"
    if env[0].isdigit():
        env = "_" + env
    brief = vault.write(c, who, CredentialWrite(name=body.name, username=body.username, kind="api_key", env=env,
                                                secret=body.value, source="tool:" + tool_id))
    c.execute("UPDATE credentials SET tool_id=?, access_level=?, expires_on=? WHERE id=?",
              (tool_id, body.access_level, body.expires_on or None, brief["id"]))
    H.event(c, who.actor, "tool.key_added", tool_id, {"credential": brief["id"], "access_level": body.access_level})
    return _key_view(c, who, vault.row(c, brief["id"]), True, vault.admins)


def ensure_tool(c, who, credential_id, from_computer=False):
    """Put a key stored outside Tools (Settings' vault, a chat's credential card, a computer's file migration) on a
    tool, in the caller's transaction, so every tool key shows on Tools: the first tool of its service that has no Read
    and write key yet takes it as that key, else a new tool is made for it, on the proxy when its service's address is
    known and on computers otherwise. The same rule as the data migration (`TS.is_tool_key`). An Only listed tool never
    takes a key this way (whoever stored it may not see that tool: a key joins a private tool from its own page), nor
    counts toward the new tool's id or name. A key Tools stored itself (`tool:` source) already has its tool; anything
    else is left alone. `from_computer`: a key a bot already
    uses from its computer's files (the file migration) stays on computers, on a computer tool, like the data
    migration's: moving it to the proxy unasked would change a working bot. The tool id, or None."""
    row = c.execute("SELECT * FROM credentials WHERE id=?", (credential_id,)).fetchone()
    if not row or row["tool_id"] or str(row["source"] or "").startswith("tool:"):
        return None
    on_computers = c.execute(
        "SELECT 1 FROM computer_keys WHERE credential_id=? UNION SELECT 1 FROM credential_grants WHERE credential_id=?"
        " AND (subject='computers' OR subject LIKE 'computer:%')", (credential_id, credential_id)).fetchone()
    if not TS.is_tool_key(row["kind"], row["env"], row["name"], row["source"], row["ciphertext"] is not None,
                          bool(on_computers)):
        return None
    service = TS.match(env=row["env"], name=row["name"])
    free = service and c.execute(
        "SELECT id FROM tools t WHERE service=? AND audience IS NULL AND NOT EXISTS (SELECT 1 FROM credentials k WHERE k.tool_id=t.id"
        " AND k.access_level='read_write')" + (" AND delivery='computer'" if from_computer else "") +
        " ORDER BY created_at, id LIMIT 1", (service,)).fetchone()
    proxy = not from_computer and TS.defaults(service).get("base_url")
    if free:
        tid = free["id"]
    else:
        base = TS.slug(service or row["name"])
        made = _unique_id(c, base, who)
        first = made == base or c.execute("SELECT 1 FROM tools WHERE id=? AND audience IS NULL", (base,)).fetchone() is None
        tid = TS.insert_tool(c, service, row["name"], "proxy" if proxy else "computer", who.actor, H.now(), tid=made,
                             first=first)
    c.execute("UPDATE credentials SET tool_id=?, access_level='read_write' WHERE id=?", (tid, credential_id))
    H.event(c, who.actor, "tool.key_added", tid, {"credential": credential_id, "access_level": "read_write"})
    return tid


def _day(value):
    if value and _days_left(value, _today()) is None:
        raise Problem("expires_on", "End date is a real day, YYYY-MM-DD", 422)


def _tool_key(c, tool_id, key_id):
    key = c.execute("SELECT * FROM credentials WHERE id=? AND tool_id=?", (key_id, tool_id)).fetchone()
    if not key:
        raise Problem("not_found", "No such key on this tool", 404)
    return key


def update_key(c, who, vault, tool_id, key_id, body):
    require_admin(c, who, vault.admins)
    tool = _readable(c, who, tool_id, vault.admins)
    key = _tool_key(c, tool_id, key_id)
    if body.access_level and body.access_level != key["access_level"] and _key_at(c, tool_id, body.access_level, key_id):
        raise _taken(tool, body.access_level)
    _day(body.expires_on)
    if body.value is not None or body.name is not None or body.username is not None:
        vault.write(c, who, CredentialWrite(name=body.name or key["name"], username=key["username"] if body.username is None
                                            else body.username, kind=key["kind"], env=key["env"], secret=body.value,
                                            source=key["source"], expected_revision=key["revision"]), key_id)
    level = body.access_level or key["access_level"]
    expires = key["expires_on"] if body.expires_on is None else (body.expires_on or None)
    c.execute("UPDATE credentials SET access_level=?, expires_on=? WHERE id=?", (level, expires, key_id))
    H.event(c, who.actor, "tool.key_updated", tool_id, {"credential": key_id, "access_level": level,
                                                         "value_changed": body.value is not None})
    return _key_view(c, who, vault.row(c, key_id), True, vault.admins)


def delete_key(c, who, vault, tool_id, key_id):
    require_admin(c, who, vault.admins)
    _readable(c, who, tool_id, vault.admins)
    key = _tool_key(c, tool_id, key_id)
    _drop_key(c, who, key_id)
    H.event(c, who.actor, "tool.key_deleted", tool_id, {"credential": key_id, "name": key["name"]})
    return {"deleted": True, "id": key_id}


def _revoke(c, who, key_id, subject):
    for grant in c.execute("SELECT id FROM credential_grants WHERE credential_id=? AND subject=? AND revoked_at IS NULL",
                           (key_id, subject)).fetchall():
        c.execute("UPDATE credential_grants SET revoked_at=?, revoked_by=? WHERE id=?", (H.now(), who.actor, grant["id"]))
        H.event(c, who.actor, "credential.revoked", key_id, {"grant": grant["id"], "subject": subject})


def set_level(c, who, vault, tool_id, subject, level):
    """Give `subject` Read, Read and write or no access: grant the one key that level uses, at that level, and take the
    subject's grants on the tool's other keys away first (one value per variable per run)."""
    require_admin(c, who, vault.admins)
    tool = _readable(c, who, tool_id, vault.admins)
    subject = subject_of(c, subject)
    if not subject.startswith(("bot:", "human:")):
        raise Problem("subject", "Choose a person or a bot", 422)
    keys = [k for k in _keys(c, tool_id) if k["ciphertext"] is not None]
    target = None
    if level == "read":
        target = next((k for k in keys if k["access_level"] == "read"), None) or \
            next((k for k in keys if k["access_level"] == "read_write"), None)
        if target is None:
            raise Problem("tool_access", f"{tool['name']} has no stored key. Add one in Tools > {tool['name']} > Keys", 409)
    elif level == "read_write":
        target = next((k for k in keys if k["access_level"] == "read_write"), None)
        if target is None:
            raise Problem("tool_access", f"{tool['name']} has no Read and write key. Add one in Tools > {tool['name']} > "
                          "Keys", 409)
    for key in _keys(c, tool_id):
        if target is None or key["id"] != target["id"]:
            _revoke(c, who, key["id"], subject)
    if target is not None:
        grant = vault.grant(c, who, target["id"], subject)
        c.execute("UPDATE credential_grants SET access_level=? WHERE id=?", (level, grant["id"]))
    H.event(c, who.actor, "tool.access_set", tool_id, {"subject": subject, "level": level})
    return {"subject": subject, "level": subject_level(c, tool_id, subject)}


# ---- tool-index

def _who_from_bots(bots, more):
    return [{"kind": "bot", "id": b["slug"], "name": b["name"], "initials": _initials(b["name"])} for b in bots], \
        len(bots) + more


def _index_row(id, kind, name, status_, href, *, detail="", way="", who=(), who_total=0, needs_you=None, **extra):
    who = list(who)
    return {"id": id, "kind": kind, "name": name, "detail": detail, "way": way,
            "status": {"tone": status_["tone"], "text": status_["text"]}, "who": who[:WHO_MAX],
            "who_total": max(who_total, len(who)),
            "needs_you": status_["tone"] == "fail" if needs_you is None else bool(needs_you), "href": href, **extra}


def _api_rows(c, who, admins, view):
    admin = _admin(c, who, admins)
    out = []
    for tool in c.execute("SELECT * FROM tools ORDER BY lower(name), id").fetchall():
        if not (admin and tool["audience"] is None) and not visible(c, who, tool, admins, view.auth):
            continue
        keys = _keys(c, tool["id"])
        state = status(tool, keys, latest_checks(c, tool["id"]))
        people = access_rows(c, who, tool["id"], admins, view.auth)
        if who.role in ("owner", "human"):
            # A bot listed is one the reader may see (the same rule every list of bots follows).
            bots = {r["id"] for r in people if r["kind"] == "bot"}
            seen = view.auth.bot_accesses(c, who, sorted(bots)) if bots and not admin else {}
            people = [r for r in people if r["kind"] != "bot" or admin or (seen.get(r["id"]) or {}).get("see")]
        host = re.sub(r"^https://", "", tool["base_url"]).split("/")[0]
        out.append(_index_row("api:" + tool["id"], "api", tool["name"], state, "#/tools/api/" + tool["id"],
                              detail=host, way="Proxy" if tool["delivery"] == "proxy" else "On computers",
                              who=[{k: r[k] for k in ("kind", "id", "name", "initials")} for r in people],
                              who_total=len(people), needs_you=state["needs_you"], service=tool["service"],
                              private=tool["audience"] is not None))
    return out


MAILBOX_STATE = {"connected": ("ok", "Connected"), "not_connected": ("fail", "Not connected"),
                 "awaiting_person": ("wait", "Waiting for its person"), "stopped": ("idle", "Stopped")}


def _mailbox_rows(c, who, view):
    """Google mailboxes (backend/mailboxes.py) the reader may use or manage, then the mail bots' mailboxes not among
    them (connections.py)."""
    from . import connections as CN
    from . import mailboxes as MB
    out, seen = [], set()
    if CN.table(c, "mailboxes") and who.role in ("owner", "human"):
        page = MB.listing(c, who)
        boxes = page["mailboxes"] if page["admin"] else [b for b in [page["mine"], *page["shared"]] if b]
        for box in boxes:
            seen.add(box["address"])
            tone, text = MAILBOX_STATE.get(box["state"], ("idle", box["state"]))
            if box["state"] == "connected" and box.get("sync"):
                tone, text = "fail", "Sign in again" if box["sync"] == "signin" else "Sync failing"
            people = []
            if box.get("person_id"):
                people.append(_person("human:" + box["person_id"], box.get("person_name") or box["person_id"]))
            for kind, gid in c.execute("SELECT grantee_type, grantee_id FROM mailbox_accesses WHERE mailbox_id=? "
                                       "ORDER BY created_at", (box["id"],)).fetchall():
                subject = ("human:" if kind == "person" else "bot:") + gid
                people.append(_person(subject, view.name(subject)))
            out.append(_index_row("mailbox:" + box["address"], "mailbox", box["address"], {"tone": tone, "text": text},
                                  "#/tools/mailbox/" + box["address"], way="Google", who=people,
                                  who_total=len(people), mailbox_id=box["id"]))
    for _key, row in CN.mailboxes(view, None, INDEX_LIMIT_MAX):
        address = row["name"]
        if address in seen:
            continue
        tone, text = CONNECTION_STATUS.get(row["status"], ("idle", row["status"]))
        people, total = _who_from_bots(row["bots"], row["more_bots"])
        out.append(_index_row("mailbox:" + address, "mailbox", address, {"tone": tone, "text": text},
                              "#/tools/mailbox/" + address, detail=row["readers"], way="Mail bot", who=people,
                              who_total=total))
    return out


def _connection_rows(view):
    """Tico addresses and numbers, mailing mail, Slack, GitHub, meeting importers, Listening and outgoing webhooks, from
    connections.py's sources, as Tools rows."""
    from . import connections as CN
    out = []
    for key, row in CN.inboxes(view, None, INDEX_LIMIT_MAX):
        number = row.get("channel") == "sms"
        tone, text = CONNECTION_STATUS.get(row["status"], ("idle", row["status"]))
        out.append(_index_row(("number:" if number else "tico:") + key, "number" if number else "mailbox", row["name"],
                              {"tone": tone, "text": text}, "#/tools/number/" + key, detail=row["detail"],
                              way="Tico", who_total=0))
    simple = (("postal", CN.postal, "address", "#/tools/address/intake", "Tico"),
              ("slack", CN.slack, "slack", "#/tools/slack", "Slack app"),
              ("github", CN.github, "github", "#/tools/github", "GitHub App"),
              ("meetings", CN.meetings, "meetings", None, "Importer"),
              ("listening", CN.listening, "listening", None, "Tico"),
              ("webhook", CN.webhooks, "webhook", None, "Webhook"))
    for _name, source, kind, href, way in simple:
        for key, row in source(view, None, INDEX_LIMIT_MAX):
            tone, text = CONNECTION_STATUS.get(row["status"], ("idle", row["status"]))
            people, total = _who_from_bots(row["bots"], row["more_bots"])
            out.append(_index_row(kind + (":" + key if key else ""), kind, row["name"], {"tone": tone, "text": text},
                                  href or row["configure"], detail=row["detail"], way=way, who=people, who_total=total))
    return out


DECLARED = {"ready": ("ok", "Ready"), "problem": ("fail", "Problem"), "pending": ("wait", "Pending"),
            "unknown": ("idle", "Not checked")}


def _declared_rows(c, who, view, covered_services, covered_envs):
    """Tools bots declare in bot.yaml (backend/bot_tools.py) that have no tools row, for the bots the reader may read."""
    from . import bot_tools
    found = {}
    for bot in H.bots(c):
        if bot["state"] == "archived":
            continue
        if who.actor != "bot:" + bot["id"] and not (who.role in ("owner", "human")
                                                     and view.auth.bot_access(c, who, bot["id"])["read"]):
            continue
        try:
            tools = bot_tools.listing(c, view.settings, bot["id"])["tools"]
        except Exception as exc:  # noqa: BLE001 - one bot's broken bot.yaml leaves its rows out, never the list
            log.warning("tool-index: declared tools of %s unreadable: %s", bot["id"], type(exc).__name__)
            continue
        for tool in tools:
            service = str(tool.get("service") or "").lower()
            if tool.get("id") in ("model", "repo") or not service or OWN_ROWS.match(service) \
                    or service in covered_services or (tool.get("env") and tool["env"] in covered_envs):
                continue
            item = found.setdefault(service, {"name": tool.get("name") or bot_tools.service_name(service), "bots": [],
                                              "statuses": set()})
            item["bots"].append(bot["id"])
            item["statuses"].add(tool.get("status") or "unknown")
    out = []
    for service, item in sorted(found.items()):
        statuses = item["statuses"]
        state = next(s for s in ("problem", "pending", "ready", "unknown") if s in statuses or s == "unknown")
        tone, text = DECLARED[state]
        bots, more = view.bots(item["bots"])
        people, total = _who_from_bots(bots, more)
        out.append(_index_row("declared:" + service, "declared", item["name"], {"tone": tone, "text": text},
                              "#/tools/" + service, way="Declared", who=people, who_total=total, service=service))
    return out


def index_rows(c, who, auth, settings, admins):
    """Every Tools row the reader may see, in list order: API tools, mail and numbers, the company's connections,
    declared tools."""
    from . import connections as CN
    view = CN.view(c, auth, who, settings)
    rows = _api_rows(c, who, admins, view)
    covered = {r["service"] for r in rows if r.get("service")}
    # A tool the reader may not see covers nothing: the list reads as if it did not exist.
    shown = {r["id"][len("api:"):] for r in rows}
    envs = {r[0] for r in c.execute("SELECT env, tool_id FROM credentials WHERE tool_id IS NOT NULL AND env<>''")
            if r[1] in shown}
    rows += _mailbox_rows(c, who, view)
    rows += _connection_rows(view)
    rows += _declared_rows(c, who, view, covered, envs)
    return rows


def index_page(c, who, auth, settings, admins, limit=INDEX_LIMIT_MAX, cursor=""):
    rows = index_rows(c, who, auth, settings, admins)
    try:
        start = int(cursor or 0)
    except ValueError:
        raise Problem("invalid_request", "cursor is the `next` of the page before", 422) from None
    if start < 0:
        raise Problem("invalid_request", "cursor is the `next` of the page before", 422)
    limit = max(1, min(limit, INDEX_LIMIT_MAX))
    shown, used, end = [], 0, start
    for item in rows[start:start + limit]:
        size = len(encode(item).encode())
        if shown and used + size > INDEX_BYTES_MAX:
            break
        shown.append(item)
        used += size
        end += 1
    counts = {"all": len(rows), "needs_you": sum(1 for r in rows if r["needs_you"]),
              "api": sum(1 for r in rows if r["kind"] in ("api", "declared")),
              "mail": sum(1 for r in rows if r["kind"] in ("mailbox", "number", "address")), "websites": 0}
    return {"rows": shown, "counts": counts, "next": str(end) if end < len(rows) else None}


# ---- routes

def install(app, store, auth, settings):
    """The routes. The vault is read from app.state at each call (a test swaps its cipher after start)."""
    vault = lambda: app.state.vault  # noqa: E731

    def change(request, body, authorize, fn):
        v = vault()
        return v.change(request.state.identity, request.url.path, request.headers.get("idempotency-key"), body,
                        authorize, fn)

    def manager(c, w):
        require_admin(c, w, vault().admins)

    @app.get("/api/v2/tool-index")
    def tool_index(request: Request, limit: int = Query(default=INDEX_LIMIT_MAX, ge=1, le=INDEX_LIMIT_MAX),
                   cursor: str = Query(default="", max_length=20)):
        """Tools: every API tool, mailbox, Tico address and number, mailing mail, Slack, GitHub, meeting importer,
        Listening, outgoing webhook and declared tool the caller may see, with its status and who can use it."""
        with store.read_transaction() as c:
            return index_page(c, request.state.identity, auth, settings, vault().admins, limit, cursor)

    @app.get("/api/v2/api-tools/{tool_id}")
    def get_api_tool(request: Request, tool_id: str):
        with store.read_transaction() as c:
            return detail(c, request.state.identity, tool_id, vault().admins, auth)

    @app.post("/api/v2/api-tools")
    def create_api_tool(request: Request, body: ToolCreate):
        return change(request, body, manager, lambda c: create_tool(c, request.state.identity, body, vault().admins))

    def setter(c, w):
        if w.role != "owner":                       # the owner may change Who can see it (update_tool says what else)
            require_admin(c, w, vault().admins)

    @app.patch("/api/v2/api-tools/{tool_id}")
    def update_api_tool(request: Request, tool_id: str, body: ToolUpdate):
        """A tool's settings and its Who can see it (`audience`: null for Everyone, or an audience for Only listed)."""
        return change(request, body, setter,
                      lambda c: update_tool(c, request.state.identity, tool_id, body, vault().admins, auth))

    def plain(request, fn):
        """A delete, like the vault's: no receipt (a repeat finds nothing and says so)."""
        from .auth import validate_identity
        with store.transaction() as c:
            validate_identity(c, request.state.identity)
            return fn(c, request.state.identity)

    @app.delete("/api/v2/api-tools/{tool_id}")
    def delete_api_tool(request: Request, tool_id: str):
        return plain(request, lambda c, who: delete_tool(c, who, tool_id, vault().admins))

    @app.post("/api/v2/api-tools/{tool_id}/keys")
    def add_api_tool_key(request: Request, tool_id: str, body: KeyCreate):
        return change(request, _Receipt(body), manager,
                      lambda c: add_key(c, request.state.identity, vault(), tool_id, body))

    @app.patch("/api/v2/api-tools/{tool_id}/keys/{key_id}")
    def update_api_tool_key(request: Request, tool_id: str, key_id: str, body: KeyUpdate):
        return change(request, _Receipt(body), manager,
                      lambda c: update_key(c, request.state.identity, vault(), tool_id, key_id, body))

    @app.delete("/api/v2/api-tools/{tool_id}/keys/{key_id}")
    def delete_api_tool_key(request: Request, tool_id: str, key_id: str):
        return plain(request, lambda c, who: delete_key(c, who, vault(), tool_id, key_id))

    @app.put("/api/v2/api-tools/{tool_id}/access/{subject}")
    def set_api_tool_access(request: Request, tool_id: str, subject: str, body: AccessSet):
        return change(request, _Receipt(body, subject=subject), manager,
                      lambda c: set_level(c, request.state.identity, vault(), tool_id, subject, body.level))

    @app.post("/api/v2/api-tools/{tool_id}/check")
    def check_api_tool(request: Request, tool_id: str):
        """Check now: one authenticated GET per stored key, by anyone who may read the tool's page (a check only
        reads). The worst result, and each key's."""
        who = request.state.identity
        with store.read() as c:
            from .auth import validate_identity
            validate_identity(c, who)
            _readable(c, who, tool_id, vault().admins)
        rows = check_tool(store, vault(), tool_id)
        worst = rows[0]
        return {"ok": worst["ok"], "status_code": worst["status_code"], "detail": worst["detail"],
                "checked_at": worst["checked_at"],
                "rows": [{k: r[k] for k in ("credential_id", "ok", "status_code", "detail", "checked_at")} for r in rows]}

    @app.get("/api/v2/api-tools/{tool_id}/activity")
    def api_tool_activity(request: Request, tool_id: str, cursor: str = Query(default="", max_length=200),
                          limit: int = Query(default=50, ge=1, le=ACTIVITY_MAX),
                          result: str = Query(default="", max_length=20)):
        with store.read_transaction() as c:
            return activity(c, request.state.identity, tool_id, vault().admins, auth, cursor, limit, result)
