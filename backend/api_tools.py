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
keys that existed before (the migration 20261105220550) are `computer` until a person moves them. How a key reaches a
run is never a problem: it puts no tool under Needs you (Tools marks a proxied tool with a lock instead).

A tool also holds Settings, its non-secret values (a host, a region, a project id), each delivered as a variable to
every run that holds a level on the tool (backend/tool_settings.py). Tools lists one row a vendor (`vendor` on a row,
`tool_services.vendor_of`: the tool's `service`, which a person may set, else its name, its keys' variables or its
address), and `GET /api/v2/tool-vendors/{vendor}` answers a vendor's page in one read: its rows, an API tool's with who
holds which level and whether it is an MCP server. Mailboxes, Tico's numbers and addresses and Tico's own tools have no vendor.

A check is one authenticated GET to the tool's `check_path` (its known service's when it has none:
tool_services.check_path_for) with each stored key: 2xx is Working, 403 Works (limited) (the vendor knows the key, the
path needs a scope it lacks), a 401 whose reply says the key lacks a permission for the path (`_missing_scope`) Works
(limited) too, only any other 401 Key rejected, anything else (a timeout, a refused address, a 500) Check failed.
An id stored as a key (`tool_settings.known_name`) is never sent as one, and a status never comes from a check that
would not run today (`_checked_today`). A proxied tool's real calls (`call_health`, over CALLS_WINDOW) outrank its
check: a 2xx makes it Working whatever the check said, and only REJECTED_AFTER 401s in a row, none 2xx since and none
whose reply says a permission is missing (`said_missing_scope`, stored as `MISSING_SCOPE` on the call), make it
Key rejected. The scheduler checks every API tool with a check path every CHECK_EVERY on a background thread, its keys
on the proxy or on computers alike (the server holds every value); "Check now" runs one at once. A check sends
the key only to the tool's own https address, pinned to the public IP it resolved to (backend/egress.py), and nothing
about a key but its tool and id is logged or stored. A tool with an MCP address (`mcp_url`, backend/mcp_connect.py) is a
remote MCP server: its check is MCP's initialize and tools/list instead, and it may sign in over OAuth rather than hold
a pasted key.

A key's end date (`expires_on`) is typed by a person, or read from the vendor on its check (`vendor_end_date`: GitHub
and Cloudflare say it on every check that works), and then marked `expires_source` 'vendor': the vendor's date replaces
a typed one and the next check keeps it current. From EXPIRING_DAYS before it the tool reads "Expires in N days", and
its Renew task opens (backend/reconnect_tasks.py).

Who may do what, the vault's rules: a credential administrator (`credentials.administrator`) manages every tool and
sees every row. Anyone else reads the page of a tool they hold a grant on or added a key to, sees only their own
access row and the grants they made, and only the calls they or a bot they may see made. A bot reads the tools it is
granted. The person or bot who added a key can remove it while they can see the tool. Values are revealed through the
vault.

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
from typing import NamedTuple

from fastapi import Query, Request
from pydantic import ConfigDict, Field, SecretStr

from . import mcp_connect
from . import tool_services as TS
from . import tool_settings
from . import access_requests
from . import tool_templates
from . import default_read
from . import reconnect_tasks
from . import tool_access as TA
from .credentials import CredentialWrite, administrator, effective_grant, may_remove, owned
from .models import AccessAudience, Contract
from .store import H, Problem, encode
from .api_contract import api_v2

log = logging.getLogger(__name__)

LEVELS = ("read", "read_write")
RANK = {"read": 1, "read_write": 2}
LEVEL_NAMES = {"read": "Read", "read_write": "Read and write"}
DELIVERIES = ("proxy", "computer")
CHECK_EVERY = timedelta(minutes=30)
CHECK_TIMEOUT = 10.0  # seconds for one check, connect to last byte
# Tests set this to an httpx transport: every check then goes through it, its address still checked and pinned.
TRANSPORT = None
CHECKS_SHOWN = 24  # the 24 squares on a tool's Check card
RECENT = 5  # calls on a tool's Overview
EXPIRING_DAYS = 14  # a key this close to its end date is under Needs you
RETENTION_DAYS = 90
WHO_MAX = 8  # avatars on one tool-index row; `who_total` counts all
INDEX_LIMIT_MAX = 500
INDEX_BYTES_MAX = 192 * 1024
ACTIVITY_MAX = 200
RESULTS = ("sent", "refused", "failed", "use")
CALLS_WINDOW = timedelta(hours=24)  # real calls this recent say whether a proxied tool works (`call_health`)
REJECTED_AFTER = 3  # 401s in a row on real calls, none 2xx since, before a tool reads Key rejected
LIMITED = "Works (limited)"  # a check that got 403: the vendor knows the key, this path needs a scope it lacks
SCOPE_READ = 2048  # bytes of a 401's reply read to tell a restricted key from a bad one (`said_missing_scope`)
# What a 401's reply (lowercased) says when the key is known and lacks a permission for the path: ElevenLabs'
# restricted keys answer missing_permissions, and RFC 6750 bearer challenges answer insufficient_scope.
SCOPE_SAYS = (
    "missing_permissions",
    "missing the permission",
    "missing permission",
    "insufficient_scope",
    "insufficient scope",
    "insufficient permission",
    "missing_scope",
    "missing scope",
)
# A vendor that says when a key ends, read on the key's check (`vendor_end_date`): GitHub sends this header on its API's
# replies to a personal token that has an end date, and Cloudflare's token verify answers `result.expires_on`. Only the
# day is kept (`credentials.expires_on`, `expires_source` 'vendor'); nothing else of the reply is.
GITHUB_ENDS = "github-authentication-token-expiration"
ENDS_READ = ("github", "cloudflare")  # services whose check reply is read for an end date
ENDS_IN_BODY = ("cloudflare",)  # of them, those whose end date is in the reply's body, not a header
ENDS_BODY = 64 * 1024  # bytes of a 2xx reply read for an end date in its body
ENDS_AT = re.compile(
    r"(\d{4}-\d{2}-\d{2})(?:[ T](\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?))?\s*(UTC|GMT|Z|[+-]\d{2}:?\d{2})?", re.IGNORECASE
)
# A tool_calls row's detail for a real call the vendor answered 401 with a missing scope (never the reply's text).
MISSING_SCOPE = "missing_scope"
CHECK_PATH = re.compile(r"^/[A-Za-z0-9._~!$&'()*+,;=:@%/?-]{0,299}$")
DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# connections.py's statuses, as the tones and words Tools shows them in.
CONNECTION_STATUS = {
    "on": ("ok", "On"),
    "off": ("idle", "Off"),
    "waiting": ("wait", "Waiting"),
    "paused": ("wait", "Paused"),
    "error": ("fail", "Error"),
}
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
    return c.execute(
        "SELECT * FROM credentials WHERE tool_id=? ORDER BY access_level, created_at, id", (tool_id,)
    ).fetchall()


def _held(c, tool_id, subject):
    """[(key row, level)] for each of the tool's keys `subject` holds an effective grant to; with none, the key a bot
    reads the tool with by default, at Read (backend/default_read.py)."""
    out = []
    keys = _keys(c, tool_id)
    for key in keys:
        grant = effective_grant(c, key["id"], subject)
        if grant is not None:
            level = "read" if key["access_level"] == "read" or grant["access_level"] == "read" else "read_write"
            out.append((key, level))
    if not out and str(subject).startswith("bot:"):
        default = default_read.keys_of(c, tool_id, subject)
        out = [(key, "read") for key in keys if key["id"] in default]
    return out


def _templated(c, tool_id):
    """Whether the tool has a multi-secret template (backend/tool_templates.py): one key per variable per level."""
    row = c.execute("SELECT * FROM tools WHERE id=?", (tool_id,)).fetchone()
    return row is not None and bool(_field(row, "auth_template"))


def computer_way(c, row, subject, tool=None):
    """How one key of a tool whose keys go to computers reaches `subject`'s run:
    - `key`: as it is;
    - `skip`: held at Read, and a read key of the same tool goes instead (the read key is preferred);
    - `proxy`: held at Read with only a Read and write key: the run gets a proxy token, and the proxy lets only reads
      through (backend/tool_proxy.py);
    - `unenforced`: as `key`, because nothing can hold it to reads: the tool has no API address the proxy can call.
    A key on no tool, or held at Read and write, is `key` (credentials.held_at_read puts the write-key policy of ads,
    payments and CRM tools, `proxy_policy`, ahead of this). `tool`: its row, when the caller has it."""
    if not row["tool_id"] or row["access_level"] == "read":
        return "key"
    held = _held(c, row["tool_id"], subject_of(c, subject))
    mine = [level for key, level in held if key["id"] == row["id"]]
    if not mine or mine[0] != "read":
        return "key"
    # A template's keys are each a variable of their own: only a read key of the same variable goes instead.
    same = _templated(c, row["tool_id"])
    if any(
        key["access_level"] == "read" and key["ciphertext"] is not None and (not same or key["env"] == row["env"])
        for key, _level in held
    ):
        return "skip"
    tool = tool if tool is not None else c.execute("SELECT * FROM tools WHERE id=?", (row["tool_id"],)).fetchone()
    from .tool_proxy import address_problem

    if tool is not None and not address_problem(tool["base_url"] or "", tool["name"] or tool["id"]):
        return "proxy"
    return "unenforced"


def proxy_policy(tool):
    """The write-key policy of an ads, payments or CRM tool (tool_services.GATED, by its service or its API's known
    host), ahead of the tool's own delivery: `direct` for Google Ads, whose calls are signed on the computer
    (tool_services.DIRECT), and for one the proxy cannot call, its write keys reaching runs as they are (Not covered);
    `proxy` for the rest, a write key reaching runs only through the proxy, where the Rules gate sees its writes, and
    never revealed to an agent. None for any other tool: its own delivery decides. Rotating a key a run had before is
    a person's, at the vendor (`python -m backend.proxy_keys` lists them)."""
    from .tool_proxy import address_problem

    vendor = TS.gated_vendor(tool["service"], tool["base_url"] or _field(tool, "mcp_url") or "")
    if not vendor:
        return None
    if vendor in TS.DIRECT or address_problem(tool["base_url"] or "", tool["name"] or tool["id"]):
        return "direct"
    return "proxy"


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
            raise Problem(
                "tool_access", f"{tool['name']} has no stored key. Add one in Tools > {tool['name']} > Keys", 403
            )
        raise Problem(
            "tool_access",
            f"{_label(c, subject)} has no access to {tool['name']}. Set a level in Tools > "
            f"{tool['name']} > Who can use it",
            403,
        )
    level = max((level for _key, level in held), key=RANK.get)
    if level == "read":
        held.sort(key=lambda pair: pair[0]["access_level"] != "read")  # the read key first
    else:
        held = [(key, lv) for key, lv in held if lv == "read_write"]
    return held[0][0], level


def keys_for(c, tool_id, subject):
    """([credential row], level) a call by `subject` through a tool with a template uses: one key per variable, at its
    level (at Read the variable's read key when there is a stored one, else its Read and write key held at Read).
    Raises key_for's 403s."""
    tool_row(c, tool_id)
    subject = subject_of(c, subject)
    held = [(key, level) for key, level in _held(c, tool_id, subject) if key["ciphertext"] is not None]
    if not held:
        key_for(c, tool_id, subject)  # raises the Problem that names the setting
        raise Problem("tool_access", "No stored key", 403)  # pragma: no cover - key_for raises first
    level = max((level for _key, level in held), key=lambda lv: RANK[lv])
    best = {}
    for key, lv in held:
        rank = (lv != level, key["access_level"] != ("read" if level == "read" else "read_write"), key["created_at"])
        if key["env"] not in best or rank < best[key["env"]][0]:
            best[key["env"]] = (rank, key)
    return [pair[1] for pair in best.values()], level


# ---- status


def _today():
    return datetime.now(timezone.utc).date()


def _days_left(on, today):
    try:
        return (date.fromisoformat(on) - today).days
    except (TypeError, ValueError):
        return None


def _check_text(ok, status_code, service=""):
    """A check's words: only a 401 (or what a known service answers a bad key with: tool_services.rejects) says the
    key is rejected. Another 403 answers a key the vendor knows, without the scope for this path (a restricted key):
    it works, limited."""
    if ok:
        return LIMITED if status_code == 403 else "Working"
    return "Key rejected" if status_code is not None and TS.rejects(service, status_code) else "Check failed"


def _limited(tool, row):
    """A check row that found the key accepted but a scope of this path missing: a 401 whose reply said so (stored as
    LIMITED), or a 403 from an API (not an MCP server's initialize) whose service does not answer a bad key with 403.
    Rows stored as failed before 403 meant this read the same."""
    if _field(tool, "mcp_url"):
        return False
    if row["ok"] and row["detail"] == LIMITED:
        return True
    return row["status_code"] == 403 and not TS.rejects(tool["service"], 403)


def _checked_today(tool, key):
    """Whether a check would send this key today: never an id (`tool_settings.known_name`), and only on a tool it can
    reach, an MCP server or an address with a check path (`check_path_of`). A check row of a key no check sends any more
    (an id, a tool whose address or path went) says nothing about the key now."""
    if tool_settings.known_name(key["env"]):
        return False
    return bool(_field(tool, "mcp_url")) or bool(tool["base_url"] and check_path_of(tool))


def call_health(c, tool_id, now=None):
    """What the tool's real calls through the proxy say, over the last CALLS_WINDOW: `working` when one got a 2xx,
    `rejected` when REJECTED_AFTER calls in a row got a 401 and none got a 2xx since; else None (nothing to say).
    A 401 whose reply says a permission is missing is the vendor knowing the key, so it is no rejection, and it ends
    the scan as a 2xx does: an older run of 401s from before the key was right says nothing about it now."""
    since = H.shift(now or H.now(), seconds=-int(CALLS_WINDOW.total_seconds()))
    calls = [
        (r[0], r[1] == MISSING_SCOPE)
        for r in c.execute(
            "SELECT status_code, detail FROM tool_calls WHERE tool_id=? AND result='sent' AND status_code IS NOT NULL "
            "AND created_at>=? ORDER BY created_at DESC, id DESC LIMIT 200",
            (tool_id, since),
        )
    ]
    run = 0
    for code, scoped in calls:  # newest first, up to the newest 2xx or scoped 401
        if 200 <= code < 300 or scoped:
            break
        run = run + 1 if code == 401 else 0
        if run >= REJECTED_AFTER:
            return "rejected"
    return "working" if any(200 <= code < 300 for code, _ in calls) else None


def state_of(c, tool, keys=None, now=None):
    """`status` for a tool row from the database: its keys, last checks and real calls."""
    keys = _keys(c, tool["id"]) if keys is None else keys
    return status(tool, keys, latest_checks(c, tool["id"]), calls=call_health(c, tool["id"], now))


def latest_checks(c, tool_id):
    """{credential id: its newest check row}."""
    out = {}
    for row in c.execute("SELECT * FROM tool_checks WHERE tool_id=? ORDER BY checked_at DESC LIMIT 50", (tool_id,)):
        out.setdefault(row["credential_id"], row)
    return out


def status(tool, keys, checks, today=None, calls=None):
    """{tone, text, needs_you} for an API tool. Worst first: no stored key; real calls rejected (`calls`, from
    `call_health`: REJECTED_AFTER 401s in a row); a refused sign-in; a failed check, unless a real call got a 2xx
    within CALLS_WINDOW (a check's path may need a scope the key's calls do not); an expired key; then a key expiring
    within EXPIRING_DAYS (Needs you, not broken); then Working once a real call worked or every stored key passed its
    last check (Works (limited) when a check got 403), else Not checked. Only the checks that would run today count
    (`_checked_today`): an id stored as a key, or a tool whose check path went, is never Key rejected from an old check.
    How its key reaches runs (`delivery`) is never one of them."""
    today = today or _today()
    stored = [k for k in keys if k["ciphertext"] is not None]
    if not stored:
        # An MCP tool that signs in (backend/mcp_connect.py) is waiting for its sign-in, not for a key.
        return {"tone": "fail", "text": "Not signed in" if _field(tool, "mcp_oauth") else "No key", "needs_you": True}
    if calls == "rejected":
        return {"tone": "fail", "text": "Key rejected", "needs_you": True}
    last = [checks.get(k["id"]) for k in stored if _checked_today(tool, k)]
    if _field(tool, "auth_template"):
        last = [r for r in last if r is not None] or [None]  # a template's keys are checked together, on one row
    failed = [r for r in last if r is not None and not r["ok"] and not _limited(tool, r)]
    if any(r["detail"] == "Sign in again" for r in failed):
        # A refused sign-in refresh is no check path's opinion: it stands, whatever calls worked before it.
        return {"tone": "fail", "text": "Sign in again", "needs_you": True}
    if failed and calls != "working":
        code = failed[0]["status_code"]
        return {"tone": "fail", "text": _check_text(False, code, tool["service"]), "needs_you": True}
    left = [d for d in (_days_left(k["expires_on"], today) for k in stored) if d is not None]
    if left and min(left) < 0:
        return {"tone": "fail", "text": "Key expired", "needs_you": True}
    if left and min(left) <= EXPIRING_DAYS:
        n = min(left)
        return {
            "tone": "wait",
            "text": "Expires today" if n == 0 else f"Expires in {n} day{'s' if n != 1 else ''}",
            "needs_you": True,
        }
    if calls == "working":
        return {"tone": "ok", "text": "Working", "needs_you": False}
    if last and all(r is not None for r in last):
        limited = any(_limited(tool, r) for r in last)
        return {"tone": "ok", "text": LIMITED if limited else "Working", "needs_you": False}
    return {"tone": "idle", "text": "Not checked", "needs_you": False}


# ---- who may see what


def _admin(c, who, admins):
    return administrator(c, who, admins)


def tools_reader(c, who):
    """The Tools page's own owner bot (backend/page_owners.py, never the General Manager standing in) in its own run: it
    reads every tool the company sees by default (Everyone), with who holds which level and its keys' names, never a
    value or a preview, so it can propose levels, find duplicates and explain a failing check. It changes nothing: every
    write here stays a credential administrator's."""
    if who.support or who.role != "bot" or who.via or who.agent:
        return False
    from . import page_owners

    return page_owners.owner(c, "tools", fallback=False) == who.actor


def may_read(c, who, tool_id, admins):
    """A credential administrator, anyone holding an effective grant on one of its keys, the person who added one, or the
    Tools page's owner bot (`tools_reader`)."""
    if who.support or who.role not in ("owner", "human", "bot"):
        return False
    if who.role in ("owner", "human") and _admin(c, who, admins):
        return True
    if tools_reader(c, who):
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
    return {
        "everyone": False,
        **{k: [str(v) for v in value[k] if v] if isinstance(value.get(k), list) else [] for k in AUDIENCE_LISTS},
    }


def _can_use(c, tool_id, actor):
    """An effective grant on one of its keys, or (a website tool) an own account `actor` holds or a shared one granted
    to a group `actor` is in or to All bots (backend/website_tools.py `_grant`)."""
    keys = _keys(c, tool_id)
    if any(_field(k, "holder") == actor or effective_grant(c, k["id"], actor) for k in keys):
        return True
    logins = [k for k in keys if k["kind"] == "login" and not _field(k, "holder")]
    if not logins:
        return False
    from . import website_tools

    return any(website_tools.can_use(c, actor, k) for k in logins)


def kind_of(tool):
    """`api` or `website` (backend/website_tools.py): a row from before the column is an API tool."""
    return _field(tool, "kind") or "api"


def visible(c, who, tool, admins, auth=None):
    """Whether `who` may know this tool exists: the one rule every route, list and the vault follow. `tool` is its row
    (or its id). Everyone (audience NULL) is `may_read`; Only listed is the owner, whoever the audience names, and
    whoever can use it."""
    if isinstance(tool, str):
        tool = c.execute("SELECT * FROM tools WHERE id=?", (tool,)).fetchone()
        if tool is None:
            return False
    level = audience_of(tool)
    if level is None and kind_of(tool) == "website":
        # A website tool for Everyone is known to every person and bot; its accounts stay with those who use them.
        return not getattr(who, "support", None) and getattr(who, "role", "") in ("owner", "human", "bot")
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
    except sqlite3.OperationalError as exc:  # a file before the tools migrations
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


def hidden_ids(c, who, admins, auth=None):
    """The event targets `who` never reads (backend/sql.py `events`, so history and POST /api/v2/sql): each tool they
    may not see, its keys, and the keys deleted from it (`tool.key_deleted` on the tool names them)."""
    tools = [t["id"] for t in private_tools(c) if not visible(c, who, t, admins, auth)]
    if not tools:
        return set()
    out = set(tools)
    for tid in tools:
        out.update(k["id"] for k in _keys(c, tid))
    gone = c.execute(
        "SELECT json_extract(detail,'$.credential') FROM events WHERE action='tool.key_deleted' AND target IN (%s)"
        % ",".join("?" * len(tools)),
        tools,
    )
    out.update(str(r[0]) for r in gone if r[0])
    return out


def key_hidden(c, who, credential_id, admins, auth=None):
    """Whether this credential is a key of a tool `who` may not see: the vault then answers as if it did not exist."""
    try:
        tool = c.execute(
            "SELECT t.* FROM credentials k JOIN tools t ON t.id=k.tool_id WHERE k.id=? AND t.audience IS NOT NULL",
            (str(credential_id or ""),),
        ).fetchone()
    except sqlite3.OperationalError as exc:
        if "no such" not in str(exc):
            raise
        return False
    return tool is not None and not visible(c, who, tool, admins, auth)


def _readable(c, who, tool_id, admins, auth=None, kind="api"):
    """The tool's row, or a 404 "No such tool" when it does not exist, is not of `kind` (an API tool's routes never
    reach a website tool, nor the other way round) or `who` may not see it."""
    tool = c.execute("SELECT * FROM tools WHERE id=?", (str(tool_id or ""),)).fetchone()
    if tool is None or kind_of(tool) != kind or not visible(c, who, tool, admins, auth):
        raise Problem("not_found", "No such tool", 404)
    return tool


def _initials(name):
    words = [w for w in re.split(r"[\s._@-]+", str(name or "")) if w]
    return "".join(w[0] for w in words[:2]).upper() or "?"


def _person(subject, name):
    kind = "bot" if subject.startswith("bot:") else "person"
    return {"kind": kind, "id": H.actor_id(subject), "name": name, "initials": _initials(name)}


def access_rows(c, who, tool_id, admins, auth=None, usage=True):
    """Who can use the tool: one row per subject with an effective grant, its level and (`usage`) last use. Anyone but
    an administrator (or the Tools page's owner bot, `tools_reader`) sees only their own row and the grants they made.
    A summary (a vendor's page) leaves the uses out: no `last_used_at`, nothing read from the call history."""
    from . import names

    admin = _admin(c, who, admins) or tools_reader(c, who)
    keys = {k["id"]: k for k in _keys(c, tool_id)}
    found = {}
    for key_id, key in keys.items():
        for grant in c.execute(
            "SELECT * FROM credential_grants WHERE credential_id=? AND revoked_at IS NULL", (key_id,)
        ):
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
    for row in (
        c.execute(f"SELECT actor, bot, used_at FROM credential_uses WHERE credential_id IN ({marks})", list(keys))
        if usage
        else ()
    ):
        subject = "bot:" + row["bot"] if row["bot"] else row["actor"]
        if row["used_at"] > used.get(subject, ""):
            used[subject] = row["used_at"]
    for row in (
        c.execute(
            "SELECT actor, bot, max(created_at) AS at FROM tool_calls WHERE tool_id=? GROUP BY actor, bot", (tool_id,)
        )
        if usage
        else ()
    ):
        subject = "bot:" + row["bot"] if row["bot"] else row["actor"]
        if (row["at"] or "") > used.get(subject, ""):
            used[subject] = row["at"]
    tool = c.execute("SELECT delivery FROM tools WHERE id=?", (tool_id,)).fetchone()
    on_computers = tool is not None and tool["delivery"] != "proxy"
    out = []
    for subject, level in found.items():
        name = label.get(subject) or H.actor_id(subject)
        row = {"subject": subject, **_person(subject, name), "level": level}
        if usage:
            row["last_used_at"] = used.get(subject)
        # Read on a computer tool is held to reads by the proxy when only a Read and write key holds it; with no API
        # address nothing can, and the row says so. A Read key of its own goes to the run as it is.
        if on_computers and level == "read" and subject.startswith("bot:"):
            ways = {computer_way(c, k, subject) for k in keys.values() if k["ciphertext"] is not None}
            if "unenforced" in ways:
                row["read_not_enforced"] = True
            if "proxy" in ways:
                row["proxied_read"] = True
        elif not on_computers and level == "read":
            row["proxied_read"] = True
        out.append(row)
    return sorted(out, key=lambda r: (r["kind"] != "person", r["name"].lower()))


def _finance_sources(c, key_ids):
    """The Finance connections (fin_connections: reading its status and key is allowed outside backend/finance) that
    read one of these keys and are not disconnected, oldest first; none on an install without Finance's tables."""
    ids = [str(k) for k in key_ids if k]
    if not ids:
        return []
    try:
        return c.execute(
            f"SELECT credential_ref, name, provider, last_sync_at FROM fin_connections WHERE credential_ref "
            f"IN ({','.join('?' * len(ids))}) AND status<>'disconnected' ORDER BY created_at, id",
            ids,
        ).fetchall()
    except sqlite3.OperationalError as exc:  # no Finance here
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
    return [
        {
            "subject": "finance",
            "kind": "finance",
            "id": "finance",
            "name": "Finance",
            "initials": "F",
            "level": level,
            "last_used_at": used,
            "fixed": True,
        }
    ]


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
    for gid in _login_groups(c, tool["id"]):
        # A website login granted to a group: each of its members can use it, and so can see the tool.
        add("group:" + gid, "group", str((groups.get(gid) or {}).get("name") or gid), "Can use it")
    for subject in sorted(grants, key=lambda s: (not s.startswith("human:"), (label.get(s) or s).lower())):
        add(
            subject,
            "bot" if subject.startswith("bot:") else "person",
            label.get(subject) or H.actor_id(subject),
            "Can use it",
        )
    if finance_rows(c, tool["id"]):
        add("finance", "finance", "Finance", "Can use it")
    return rows


def _login_groups(c, tool_id):
    """The groups a website tool's shared logins are granted to, oldest grant first."""
    from . import website_tools

    out = []
    for key in _keys(c, tool_id):
        if key["kind"] == "login":
            out += [gid for gid in website_tools._group_grants(c, key) if gid not in out]
    return out


def access_rows_all(c, tool_id):
    """[{subject, level}] for every person and bot with an effective grant on one of the tool's keys."""
    found = {}
    for key in _keys(c, tool_id):
        if key['kind'] == 'login':
            from . import website_tools

            if website_tools.all_bots(c, key):
                for bot in c.execute("SELECT id FROM bots WHERE state<>'archived'"):
                    found['bot:' + bot['id']] = 'read_write'
        for grant in c.execute(
            "SELECT * FROM credential_grants WHERE credential_id=? AND revoked_at IS NULL", (key["id"],)
        ):
            subject = grant["subject"]
            if subject.startswith(("bot:", "human:")) and effective_grant(c, key["id"], subject) is not None:
                level = "read" if key["access_level"] == "read" or grant["access_level"] == "read" else "read_write"
                if RANK[level] > RANK.get(found.get(subject), 0):
                    found[subject] = level
        holder = _field(key, "holder")
        if holder and str(holder).startswith(("bot:", "human:")):
            found[holder] = "read_write"  # a website tool's own account: its holder uses it
    return [{"subject": s, "level": lv} for s, lv in found.items()]


def _key_view(c, who, key, admin, admins):
    from .credentials import last_used, permitted

    shown = admin or permitted(c, who, key["id"], admins)
    return {
        "id": key["id"],
        "name": key["name"],
        "preview": key["preview"] if shown else "",
        "access_level": key["access_level"],
        "expires_on": key["expires_on"],
        "expires_source": key["expires_source"],
        "env": key["env"],
        "username": key["username"] if shown else "",
        "stored": key["ciphertext"] is not None,
        "created_by": key["created_by"] or key["updated_by"],
        "created_at": key["created_at"],
        "can_remove": may_remove(who, key["created_by"], manages=admin),
        **last_used(c, key["id"]),
    }


def _tool_view(tool):
    return {
        **{k: tool[k] for k in ("id", "name", "service", "base_url", "auth_scheme", "check_path", "delivery")},
        "vendor": TS.vendor_of(tool["service"], tool["name"], host=tool["base_url"] or _field(tool, "mcp_url") or ""),
        "private": _field(tool, "audience") is not None,
        "everyone_reads": bool(_field(tool, "everyone_reads")),
        # How its several keys and settings reach the vendor (backend/tool_templates.py); None for one key.
        "template": tool_templates.view(tool),
    }


def _call_view(row):
    return {
        "id": row["id"],
        "kind": "call",
        "actor": row["actor"],
        "bot": row["bot"],
        "access_level": row["access_level"],
        "method": row["method"],
        "path": row["path"],
        "status_code": row["status_code"],
        "result": row["result"],
        "detail": row["detail"],
        "duration_ms": row["duration_ms"],
        "at": row["created_at"],
    }


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
    checks = [
        {
            "ok": bool(r["ok"]),
            "status_code": r["status_code"],
            "detail": r["detail"],
            "checked_at": r["checked_at"],
            "credential_id": r["credential_id"],
        }
        for r in c.execute(
            "SELECT * FROM tool_checks WHERE tool_id=? ORDER BY checked_at DESC, id DESC LIMIT ?",
            (tool_id, CHECKS_SHOWN),
        )
    ]
    recent = []
    for row in c.execute(
        "SELECT * FROM tool_calls WHERE tool_id=? ORDER BY created_at DESC, id DESC LIMIT 200", (tool_id,)
    ):
        if _call_visible(c, who, auth, admin, row):
            recent.append(_call_view(row))
            if len(recent) == RECENT:
                break
    state = state_of(c, tool, keys)
    out = {
        "tool": _tool_view(tool),
        "keys": [_key_view(c, who, k, admin, admins) for k in keys],
        "settings": tool_settings.listing(c, tool_id),
        "access": access_rows(c, who, tool_id, admins, auth) + (finance_rows(c, tool_id) if admin else []),
        "checks": checks,
        "recent": recent,
        "status": {"tone": state["tone"], "text": state["text"]},
        "needs_you": state["needs_you"],
        "can_manage": admin,
        "private": _field(tool, "audience") is not None,
        "can_set_audience": False,
        "mcp": mcp_connect.view(c, tool, keys) if _field(tool, "mcp_url") else None,
        # Made from several tools of one vendor account; POST …/ungroup splits it back (backend/tool_templates.py).
        "grouped": tool_templates.latest_group(c, tool_id) is not None,
        # Every bot reads it with no grant while `on` and Read can be held to reads (backend/default_read.py).
        "everyone_reads": {
            "on": bool(_field(tool, "everyone_reads")),
            "enforceable": default_read.enforceable(c, tool_id),
        },
    }
    if TA.may_change_access(c, who, TA.tool(tool), action=TA.AUDIENCE, admins=admins, auth=auth):
        out.update(can_set_audience=True, audience=audience_of(tool), seen_by=seen_by(c, tool, auth))
    return out


def activity(c, who, tool_id, admins, auth, cursor="", limit=50, result="", since=""):
    """Calls through the proxy and, for a tool whose key goes to computers, the newest time each caller's run took a
    key (credential_uses keeps one row a caller), newest first. A proxied tool's uses are its calls, already listed, so
    its use rows are left out. `cursor` is the last row's `<at>|<id>`; `since` (an ISO time or 24h / 7d) leaves out
    older rows. A credential administrator and the Tools page's owner bot (`tools_reader`, which learns from how tools
    are used) see every caller's rows; anyone else their own and those of the bots they may see."""
    from .views import since_time

    proxied = _readable(c, who, tool_id, admins, auth)["delivery"] == "proxy"
    admin = _admin(c, who, admins) or tools_reader(c, who)
    floor = since_time(since) if since else None
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
        if floor:
            sql, args = sql + " AND created_at>=?", args + [floor]
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
                if floor and row["used_at"] < floor:
                    continue
                probe = {"actor": row["actor"], "bot": row["bot"]}
                if not _call_visible(c, who, auth, admin, probe):
                    continue
                rows.append(
                    {
                        "id": ident,
                        "kind": "use",
                        "actor": row["actor"],
                        "bot": row["bot"],
                        "access_level": keys[row["credential_id"]]["access_level"],
                        "credential_id": row["credential_id"],
                        "result": "use",
                        "at": row["used_at"],
                    }
                )
    rows.sort(key=lambda r: (r["at"], r["id"]), reverse=True)
    shown = rows[:limit]
    more = len(rows) > limit
    return {"rows": shown, "next": f"{shown[-1]['at']}|{shown[-1]['id']}" if more and shown else None}


# ---- checks


class Reply(NamedTuple):
    """A check's answer: the status code, whether a 401's reply says the key lacks a permission for the path, GitHub's
    token expiration header (GITHUB_ENDS, '' without one), and a 2xx reply's first ENDS_BODY bytes when they were asked
    for (`read_body`)."""

    status_code: int
    missing_scope: bool = False
    ends_header: str = ""
    body: bytes = b""


def said_missing_scope(challenge, body):
    """Whether a 401 says the key is known but lacks a permission or scope for this path (SCOPE_SAYS: a restricted key,
    not a bad one), from its WWW-Authenticate `challenge` and the first bytes of its reply `body`: only SCOPE_READ of
    them are read, and nothing of them is kept. The tool proxy (backend/tool_proxy.py) and the checks share it."""
    said = (challenge + " " + body[:SCOPE_READ].decode("utf-8", "replace")).lower()
    return any(phrase in said for phrase in SCOPE_SAYS)


def _missing_scope(response):
    """`said_missing_scope` for a check's reply: its first chunk is read, at most SCOPE_READ bytes of it."""
    return said_missing_scope(
        response.headers.get("www-authenticate", ""), next(response.iter_bytes(chunk_size=SCOPE_READ), b"")
    )


def _first_bytes(response, limit):
    out = b""
    for chunk in response.iter_bytes():
        out += chunk
        if len(out) >= limit:
            return out[:limit]
    return out


def send_check(url, headers, params, read_body=False):
    """One GET: a `Reply`. Tests replace it. Only to a public https address, pinned to the IP it resolved to, no
    redirects followed, no proxy from the environment, at most CHECK_TIMEOUT seconds. With `read_body`, a 2xx reply's
    first ENDS_BODY bytes come back too, for its end date (`vendor_end_date`)."""
    import httpx
    from . import egress

    parts = egress.check(url)
    address = egress.address_for(parts, budget=CHECK_TIMEOUT)
    pinned = httpx.URL(url, params=params or None).copy_with(host=address)
    host = parts.hostname + (f":{parts.port}" if parts.port else "")
    with httpx.Client(timeout=CHECK_TIMEOUT, follow_redirects=False, trust_env=False, transport=TRANSPORT) as client:
        with client.stream(
            "GET", pinned, headers={**headers, "Host": host}, extensions={"sni_hostname": parts.hostname}
        ) as response:
            code = response.status_code
            ends = response.headers.get(GITHUB_ENDS, "")
            body = _first_bytes(response, ENDS_BODY) if read_body and 200 <= code < 300 else b""
            return Reply(code, code == 401 and _missing_scope(response), ends, body)


def _utc_day(raw):
    """The UTC day of a vendor's end date ("2026-12-01 09:00:00 UTC", "2026-12-01T00:00:00Z", an offset), or None."""
    found = ENDS_AT.fullmatch(str(raw).strip())
    if not found:
        return None
    day, clock, zone = found.groups()
    try:
        at = datetime.fromisoformat(f"{day}T{clock or '00:00'}")
    except ValueError:
        return None
    if zone and zone.upper() not in ("UTC", "GMT", "Z"):
        sign = 1 if zone[0] == "+" else -1
        at -= sign * timedelta(hours=int(zone[1:3]), minutes=int(zone[-2:]))
    return at.date().isoformat()


def vendor_end_date(service, reply):
    """The key's end date its vendor sent with a check that worked: a day (YYYY-MM-DD), '' when the vendor says the key
    has none, or None when this vendor says nothing either way or what it sent does not read as a date."""
    if service not in ENDS_READ:
        return None
    raw = reply.ends_header
    if service in ENDS_IN_BODY:
        try:
            answer = json.loads(reply.body or b"null")
        except ValueError:
            return None
        result = answer.get("result") if isinstance(answer, dict) else None
        if not isinstance(result, dict):
            return None
        raw = result.get("expires_on") or ""
    return _utc_day(raw) if raw else ""


def _revision(c, key_id):
    row = c.execute("SELECT revision FROM credentials WHERE id=?", (key_id,)).fetchone()
    return row["revision"] if row else None


def _keep_end_date(c, key_id, ends, revision=None):
    """A check's word on its key's end date (`vendor_end_date`): a day replaces any date and marks it the vendor's; ''
    clears a date the vendor gave before and keeps one a person typed. Nothing changes when the key's value was
    replaced since the check read it (`revision`): the answer was about the old value."""
    row = c.execute("SELECT expires_on, expires_source, revision FROM credentials WHERE id=?", (key_id,)).fetchone()
    if row is None or (revision is not None and row["revision"] != revision):
        return
    if ends and (row["expires_on"], row["expires_source"]) != (ends, "vendor"):
        c.execute("UPDATE credentials SET expires_on=?, expires_source='vendor' WHERE id=?", (ends, key_id))
    elif not ends and row["expires_source"] == "vendor":
        c.execute("UPDATE credentials SET expires_on=NULL, expires_source=NULL WHERE id=?", (key_id,))


def _check_one(tool, key, secret, auth=None):
    """(ok, status_code, detail, end date) for one key, or for a template's keys together (`auth`: the headers and query
    its template built). The end date is `vendor_end_date`'s on a check that worked, else None (nothing known). An
    exception's text is never kept: a query-param key would be in it."""
    from . import egress

    service = TS.SERVICES.get(tool["service"]) or {}
    username = key["username"] or service.get("username") or ""
    try:
        headers, params = auth if auth is not None else TS.apply_auth(tool["auth_scheme"], secret, username)
    except ValueError:
        return False, None, "Check failed: unknown auth scheme", None
    headers = {
        "Accept": "application/json",
        **TS.check_headers(tool["service"]),
        **headers,
        "User-Agent": "Tico-tool-check",
    }
    url = tool["base_url"].rstrip("/") + check_path_of(tool)
    try:
        if tool["service"] in ENDS_IN_BODY and auth is None:
            reply = send_check(url, headers, params, read_body=True)
        else:
            reply = send_check(url, headers, params)
    except egress.Blocked:
        return False, None, "Check failed: address refused", None
    except Exception as exc:  # noqa: BLE001 - every network failure is one answer: the check failed
        return False, None, "Check failed: " + ("timed out" if "Timeout" in type(exc).__name__ else "no answer"), None
    code = reply.status_code
    if code == 401 and reply.missing_scope:  # a restricted key: known to the vendor, this path needs another permission
        return True, code, LIMITED, vendor_end_date(tool["service"], reply) if auth is None else None
    rejected = TS.rejects(tool["service"], code)
    ok = 200 <= code < 300 or (code == 403 and not rejected)  # a 403: the key is known, the path needs another scope
    text = _check_text(ok, code, tool["service"]) if ok or rejected else f"Check failed: {code}"
    # A template's keys go together: no one key's end date is known from their call.
    ends = vendor_end_date(tool["service"], reply) if ok and auth is None else None
    return ok, code, text, ends


def check_tool(store, vault, tool_id):
    """Check each stored key of the tool and record a tool_checks row for it; the rows written, worst first. A tool that
    reads Key rejected now gets its Reconnect task, and one that reads Working ends it (backend/reconnect_tasks.py)."""
    rows = _check_tool(store, vault, tool_id)
    reconnect_tasks.checked(store, tool_id)
    return rows


def _check_tool(store, vault, tool_id):
    with store.transaction() as c:  # decrypt may reseal an old value in place
        tool = tool_row(c, tool_id)
        if _field(tool, "mcp_url"):
            mcp = True
        elif not tool["base_url"] or not check_path_of(tool):
            raise Problem(
                "check",
                f"{tool['name']} has no check. Set its address and check path in Tools > {tool['name']} > Settings",
                409,
            )
        else:
            mcp = False
        spec = None if mcp else tool_templates.spec_of(tool)
        together = tool_templates.check_auth(c, vault, tool, spec) if spec and spec.get("ways") else None
        held = [k for k in _keys(c, tool_id) if k["ciphertext"] is not None]
        # An id stored as a key (TWILIO_ACCOUNT_SID, POSTHOG_PROJECT_ID) is no key to the vendor, whatever its value
        # looks like: sent as one it would read Key rejected. It is never decrypted for a check. The write-key policy
        # comes first: an ads, payments or CRM tool's write key is a key, whatever its name.
        protected = proxy_policy(tool) == "proxy"
        stored = (
            []
            if mcp or together
            else [
                (k, vault.cipher.decrypt(c, k))
                for k in held
                if (protected and k["access_level"] == "read_write") or not tool_settings.known_name(k["env"])
            ]
        )
        # The revision each value was checked at, read after decrypt (which may reseal it): an end date is kept only
        # while the key still holds that value (`_keep_end_date`), never on a value saved while the check was out.
        stored = [({**dict(k), "revision": _revision(c, k["id"])}, secret) for k, secret in stored]
    if mcp:  # initialize and tools/list, with a sign-in's access token when it signs in
        return mcp_connect.check(store, vault, tool_id)
    if together:  # a template's keys go on one call together, as the proxy sends them
        key, headers, params = together
        return _record_checks(store, tool_id, [(key, *_check_one(tool, key, "", (headers, params)))])
    if not stored:
        if held:
            raise Problem("check", f"{tool['name']} holds only ids, no key to check", 409)
        raise Problem("check", f"{tool['name']} has no stored key. Add one in Tools > {tool['name']} > Keys", 409)
    results = [(key, *_check_one(tool, key, secret)) for key, secret in stored]
    stored = None  # the values leave scope before anything is written
    return _record_checks(store, tool_id, results)


def _record_checks(store, tool_id, results):
    rows = []
    with store.transaction() as c:
        for key, ok, code, text, ends in results:
            row = {
                "id": H.new_id(),
                "tool_id": tool_id,
                "credential_id": key["id"],
                "ok": int(ok),
                "status_code": code,
                "detail": text,
                "checked_at": H.now(),
            }
            c.execute(
                "INSERT INTO tool_checks(id,tool_id,credential_id,ok,status_code,detail,checked_at) "
                "VALUES(:id,:tool_id,:credential_id,:ok,:status_code,:detail,:checked_at)",
                row,
            )
            rows.append(row | {"ok": ok})
            if ends is not None:
                _keep_end_date(c, key["id"], ends, key.get("revision"))
    rows.sort(key=lambda r: r["ok"])
    return rows


_checking = threading.Lock()


def check_path_of(tool):
    """The path a tool's check GETs: its own, else its known service's at that service's address
    (tool_services.check_path_for)."""
    return TS.check_path_for(tool["service"], tool["base_url"], tool["check_path"])


def due(c, now=None):
    """API tools with an address, a check path (their own or their known service's) and a stored key whose newest
    check is older than CHECK_EVERY, however their key reaches runs: the server holds every key's value, so a key on
    computers is checked as a proxied one is (same egress rules). A tool whose every stored key is named as an id
    (`tool_settings.known_name`) has nothing to check."""
    cutoff = H.shift(now or H.now(), seconds=-int(CHECK_EVERY.total_seconds()))
    out = []
    for tool in c.execute(
        "SELECT t.* FROM tools t WHERE t.kind='api' AND t.base_url<>'' "
        "AND EXISTS(SELECT 1 FROM credentials k WHERE k.tool_id=t.id AND k.ciphertext IS NOT NULL) "
        "AND coalesce((SELECT max(checked_at) FROM tool_checks k WHERE k.tool_id=t.id),'')<? ORDER BY t.id",
        (cutoff,),
    ):
        if not (tool["mcp_url"] or check_path_of(tool)):
            continue
        keys = c.execute(
            "SELECT env, access_level FROM credentials WHERE tool_id=? AND ciphertext IS NOT NULL", (tool["id"],)
        ).fetchall()
        # The write-key policy first: an ads, payments or CRM tool's write key is checked whatever its name says.
        protected = proxy_policy(tool) == "proxy"
        if (
            not tool["mcp_url"]
            and keys
            and all(tool_settings.known_name(k[0]) and not (protected and k[1] == "read_write") for k in keys)
        ):
            continue
        out.append(tool["id"])
    return out


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
    # Its first key (Read and write), stored with the tool in one write: a refused key makes no tool.
    key: SecretStr | None = Field(default=None, min_length=1, max_length=100_000)


class ToolUpdate(Contract):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=80)
    service: str | None = Field(default=None, max_length=60)
    base_url: str | None = Field(default=None, max_length=300)
    auth_scheme: str | None = Field(default=None, max_length=80)
    check_path: str | None = Field(default=None, max_length=300)
    delivery: str | None = Field(default=None, pattern=r"^(proxy|computer)$")
    # "Everyone can read" (backend/default_read.py): every bot reads the tool with no grant while it is on.
    everyone_reads: bool | None = None
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

    def __init__(self, body, omit_none=(), **extra):
        self.body, self.omit_none, self.extra = body, omit_none, extra

    def model_dump(self):
        out = self.body.model_dump() if self.body is not None else {}
        # A field a later version added, left out when not sent: a retry across that deploy fingerprints the same.
        for name in self.omit_none:
            if out.get(name) is None:
                out.pop(name, None)
        for name, value in list(out.items()):
            if isinstance(value, SecretStr):
                out[name] = value.get_secret_value()
        return out | self.extra


_slug = TS.slug


def _unique_id(c, base):
    """`base`, else `base-<random>` whenever any tool has `base`, seen or not: an id never says how many tools of that
    name exist, nor (by its shape) that a hidden one does."""
    tid = base
    while c.execute("SELECT 1 FROM tools WHERE id=?", (tid,)).fetchone() or _former(c, tid):
        tid = f"{base}-{H.new_id()[:8].lower()}"
    return tid


def _former(c, tid):
    """Whether a grouped tool's key still names `tid` as its old tool (its run address keeps it; an ungroup brings it
    back), or an outside reference still names it as its connection (TS.referenced), so no new tool takes that id."""
    if TS.referenced(c, tid):
        return True
    try:
        return c.execute("SELECT 1 FROM credentials WHERE former_tool_id=? LIMIT 1", (tid,)).fetchone() is not None
    except sqlite3.OperationalError:
        return False


def keep_sites(c, tool, base_url, sites):
    """409 `connection_sites` when a change would move a tool that outside references name (TS.referenced) to another
    endpoint (its base URL, scheme and host in any case, its path as written) or other hosts: its items' keys (Jira's
    `PROJ-7`, a CRM's customer 42) are its old endpoint's, so another one is a new tool."""
    from urllib.parse import urlsplit

    from . import item_sources as IS

    def endpoint(url):
        parts = urlsplit(str(url or "").strip())
        return (parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), parts.query)

    before = IS.hosts(_field(tool, "base_url"), _field(tool, "sites"))
    moved = endpoint(base_url) != endpoint(_field(tool, "base_url")) or IS.hosts(base_url, sites) != before
    if moved and TS.referenced(c, tool["id"]):
        raise Problem(
            "connection_sites",
            f"{tool['name']} names outside items of {', '.join(sorted(before)) or 'its site'}: add a new tool for "
            "another site",
            409,
        )


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


def create_tool(c, who, body, admins, vault=None):
    TA.require(c, who, TA.tool(), action=TA.CREATE, admins=admins)
    if body.key is not None and (TA.acting_agent(who) or getattr(who, "via", "")):
        # A value is a person's own to paste (plan P4): never an agent's, with anyone's rights or its own.
        raise Problem("secret_in_request", "A key goes in through a person's own paste in Tools, never an agent", 422)
    fields = _clean(body.model_dump(exclude={"key"}))
    # A service given is kept as given (a "stripe-adapter" is no Stripe); only none given is matched from the name. An
    # alias given (`close`) is stored as given, its catalog service (Close CRM) reading the catalog's address and check.
    service = fields["service"].strip().lower() if fields["service"] else TS.match(name=fields["name"])
    canonical = TS.canonical(service or "")
    TA.catalog_only(who, canonical, fields)
    known = TS.defaults(canonical)
    # A known service's check path belongs to its own address: a tool given another address gets none by default.
    own = not fields["base_url"] or fields["base_url"] == known.get("base_url")
    tool = {
        "id": _unique_id(c, _slug(fields["name"])),
        "name": fields["name"].strip(),
        "service": service or fields["service"] or "",
        "base_url": fields["base_url"] or known.get("base_url", ""),
        "auth_scheme": fields["auth_scheme"] or known.get("auth_scheme", "bearer"),
        "check_path": fields["check_path"] or (known.get("check_path", "") if own else ""),
        "created_by": who.actor,
        "created_at": H.now(),
    }
    tool["delivery"] = "proxy" if tool["base_url"] else "computer"
    tool["updated_at"] = tool["created_at"]
    c.execute(
        "INSERT INTO tools(id,name,service,base_url,auth_scheme,check_path,delivery,created_by,created_at,updated_at) "
        "VALUES(:id,:name,:service,:base_url,:auth_scheme,:check_path,:delivery,:created_by,:created_at,:updated_at)",
        tool,
    )
    H.event(c, who.actor, "tool.created", tool["id"], {"service": tool["service"], "delivery": tool["delivery"]})
    if body.key is not None:
        # Its own variable: another connection of the service (Stripe prod, Stripe sandbox) may hold the plain one.
        base = (
            TS.defaults(tool["service"]).get("env")
            or re.sub(r"[^A-Z0-9]+", "_", tool["id"].upper()).strip("_") + "_API_KEY"
        )
        base = "_" + base if base[0].isdigit() else base
        env, n = base, 1
        while c.execute("SELECT 1 FROM credentials WHERE env=? LIMIT 1", (env,)).fetchone():
            n += 1
            env = f"{base}_{n}"
        add_key(c, who, vault, tool["id"], KeyCreate(name="Key", value=body.key, env=env))
    return _tool_view(tool_row(c, tool["id"]))


def update_tool(c, who, tool_id, body, admins, auth=None):
    """A tool's settings, and its Who can see it (`audience`, which the owner may change too)."""
    asked = body.model_dump(exclude_none=True, exclude={"audience", "everyone_reads"})
    audience_set = "audience" in body.model_fields_set
    if asked or not audience_set or getattr(who, "role", "") != "owner":
        # Its service, address, check or delivery: on an ads, payments or CRM tool a person's own (tool_access Rule P).
        reach = bool(set(asked) & {"service", "base_url", "auth_scheme", "check_path", "delivery"})
        TA.require(c, who, TA.tool(tool_id=tool_id) if reach else TA.tool(), action=TA.EDIT, admins=admins, auth=auth)
    tool = dict(_readable(c, who, tool_id, admins, auth))
    if audience_set:
        set_audience(c, who, tool, body.audience.model_dump() if body.audience is not None else None, admins, auth)
        tool = dict(tool_row(c, tool_id))
    if body.everyone_reads is not None and body.everyone_reads != bool(tool["everyone_reads"]):
        c.execute(
            "UPDATE tools SET everyone_reads=?,updated_at=? WHERE id=?", (int(body.everyone_reads), H.now(), tool_id)
        )
        H.event(
            c,
            who.actor,
            "tool.everyone_reads_changed",
            tool_id,
            {"before": not body.everyone_reads, "after": body.everyone_reads},
        )
        tool = dict(tool_row(c, tool_id))
        if body.everyone_reads:  # every bot reads it now: requests for Read on it close themselves
            access_requests.settled(c, who, resource_type="tool", resource_id=tool_id, via="everyone-reads")
    fields = {k: v for k, v in _clean(asked).items()}
    merged = tool | fields
    if any(merged[k] != tool[k] for k in ("service", "base_url", "auth_scheme", "check_path", "delivery")):
        TA.person_only(c, who, TA.tool(merged))  # Rule P on what it would be too (moved onto Stripe's API)
    # What the tool reaches after the change, whole: an agent's change of any part leaves only the catalog's.
    if any(merged[k] != tool[k] for k in (*TA.ADDRESS_FIELDS, "service")):
        TA.catalog_only(who, merged["service"], {k: merged[k] for k in TA.ADDRESS_FIELDS})
    if merged["delivery"] == "proxy" and not merged["base_url"]:
        raise Problem("delivery", f"Set {tool['name']}'s address before moving it to the proxy", 422)
    if merged["delivery"] == "computer" and _field(tool, "mcp_oauth"):
        raise Problem("delivery", f"{tool['name']} signs in: its tokens stay with Tico, so it stays on the proxy", 422)
    if merged == tool:
        return _tool_view(tool_row(c, tool_id))
    keep_sites(c, tool, merged["base_url"], _field(tool, "sites"))
    c.execute(
        "UPDATE tools SET name=?,service=?,base_url=?,auth_scheme=?,check_path=?,delivery=?,updated_at=? WHERE id=?",
        (
            merged["name"].strip(),
            merged["service"],
            merged["base_url"],
            merged["auth_scheme"],
            merged["check_path"],
            merged["delivery"],
            H.now(),
            tool_id,
        ),
    )
    H.event(
        c,
        who.actor,
        "tool.updated",
        tool_id,
        {k: merged[k] for k in fields if k != "name"} | ({"name": merged["name"]} if "name" in fields else {}),
    )
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
    from .model_subscriptions import before_delete

    refuse_finance_key(c, [key_id])
    key = c.execute("SELECT * FROM credentials WHERE id=?", (key_id,)).fetchone()
    before_delete(c, who, key_id)
    grants = c.execute("DELETE FROM credential_grants WHERE credential_id=?", (key_id,)).rowcount
    c.execute("DELETE FROM credentials WHERE id=?", (key_id,))
    if key is not None:
        key_gone(c, who, key, grants)


def key_gone(c, who, key, grants):
    """The events a deleted key leaves, whichever route deleted it: `credential.deleted` on the key, with its last name
    and its tool (history names the key from it to whoever could see it), and `tool.key_deleted` on its tool."""
    tid = key["tool_id"] if "tool_id" in key.keys() else None
    tool = c.execute("SELECT id,audience FROM tools WHERE id=?", (tid,)).fetchone() if tid else None
    detail = {"name": key["name"], "env": key["env"], "grants": grants}
    if tool is not None:
        detail |= {"tool": tool["id"], "private": tool["audience"] is not None}
    H.event(c, who.actor, "credential.deleted", key["id"], detail)
    if tool is not None:
        H.event(c, who.actor, "tool.key_deleted", tool["id"], {"credential": key["id"], "name": key["name"]})


def set_audience(c, who, tool, value, admins, auth=None):
    """Who can see the tool: None (Everyone) or an audience (Only listed). The owner, or a credential administrator who
    can see it; one who is not the owner stays on the list they set, so they never lock themselves out. Event
    `tool.audience_changed` with the before and after."""
    from . import teammate_access

    TA.require(c, who, TA.tool(tool), action=TA.AUDIENCE, admins=admins, auth=auth)
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
    H.event(
        c,
        who.actor,
        "tool.audience_changed",
        tool["id"],
        {"before": before, "after": audience_of({"audience": stored})},
    )


def forget_audience_member(c, actor, field, ident):
    """A removed person, group or bot leaves every tool's audience (backend/pages.py's hook calls this): a teammate made
    later under the same id is never on a list by inheritance. A list left empty stays Only listed."""
    for tool in private_tools(c):
        before = audience_of(tool)
        if ident not in before[field]:
            continue
        after = {**before, field: [v for v in before[field] if v != ident]}
        c.execute(
            "UPDATE tools SET audience=?, updated_at=? WHERE id=?",
            (json.dumps(after, sort_keys=True, separators=(",", ":")), H.now(), tool["id"]),
        )
        H.event(c, actor, "tool.audience_changed", tool["id"], {"before": before, "after": after, "removed": ident})


def delete_tool(c, who, tool_id, admins, kind="api"):
    """The tool, its checks and calls, and its keys with their grants: a key belongs to its tool (a website tool's
    accounts too)."""
    TA.require(c, who, TA.tool(tool_id=tool_id), action=TA.DELETE, admins=admins)
    tool = _readable(c, who, tool_id, admins, kind=kind)
    keys = [k["id"] for k in _keys(c, tool_id)]
    refuse_finance_key(c, keys)  # before anything goes
    for key_id in keys:
        _drop_key(c, who, key_id)
    tool_settings.drop_tool(c, tool_id)
    c.execute("DELETE FROM tool_calls WHERE tool_id=?", (tool_id,))
    c.execute("DELETE FROM tool_checks WHERE tool_id=?", (tool_id,))
    c.execute("DELETE FROM tool_oauth_states WHERE tool_id=?", (tool_id,))
    c.execute("DELETE FROM tools WHERE id=?", (tool_id,))
    reconnect_tasks.gone(c, tool_id)
    from . import key_grants

    key_grants.tool_gone(c, tool_id)  # a key proposal naming it changes in its person's Needs you
    H.event(c, who.actor, "tool.deleted", tool_id, {"name": tool["name"], "keys": len(keys)})
    return {"deleted": True, "id": tool_id}


def _key_at(c, tool_id, level, other_than=None, env=None):
    """The tool's other key at `level`; on a tool with a template, at that level under `env` (one key per variable)."""
    if env is not None and _templated(c, tool_id):
        return c.execute(
            "SELECT * FROM credentials WHERE tool_id=? AND access_level=? AND env=? AND id<>?",
            (tool_id, level, env, other_than or ""),
        ).fetchone()
    return c.execute(
        "SELECT * FROM credentials WHERE tool_id=? AND access_level=? AND id<>?", (tool_id, level, other_than or "")
    ).fetchone()


def _taken(tool, level):
    return Problem(
        "key_level",
        f"{tool['name']} already has a {LEVEL_NAMES[level]} key. Replace its value in Tools > {tool['name']} > Keys",
        409,
    )


def add_key(c, who, vault, tool_id, body):
    """A new key for the tool at its level, stored by the vault under the tool's variable."""
    TA.require(c, who, TA.tool(tool_id=tool_id), action=TA.EDIT, admins=vault.admins, auth=vault.auth)
    tool = _readable(c, who, tool_id, vault.admins)
    if _key_at(c, tool_id, body.access_level, env=body.env or None):
        raise _taken(tool, body.access_level)
    _day(body.expires_on)
    sibling = c.execute("SELECT env FROM credentials WHERE tool_id=? AND env<>'' LIMIT 1", (tool_id,)).fetchone()
    env = (
        body.env
        or (sibling["env"] if sibling else "")
        or TS.defaults(tool["service"]).get("env")
        or re.sub(r"[^A-Z0-9]+", "_", tool["id"].upper()).strip("_") + "_API_KEY"
    )
    if env[0].isdigit():
        env = "_" + env
    brief = vault.write(
        c,
        who,
        CredentialWrite(
            name=body.name, username=body.username, kind="api_key", env=env, secret=body.value, source="tool:" + tool_id
        ),
    )
    c.execute(
        "UPDATE credentials SET tool_id=?, access_level=?, expires_on=?, expires_source=? WHERE id=?",
        (tool_id, body.access_level, body.expires_on or None, "person" if body.expires_on else None, brief["id"]),
    )
    H.event(c, who.actor, "tool.key_added", tool_id, {"credential": brief["id"], "access_level": body.access_level})
    access_requests.settled(c, who, resource_type="tool", resource_id=tool_id)  # a Read key may make Read default
    return _key_view(c, who, vault.row(c, brief["id"]), True, vault.admins)


def ensure_tool(c, who, credential_id, from_computer=False, value=None):
    """Put a key stored outside Tools (Settings' vault, a chat's credential card, a computer's file migration) on a
    tool, in the caller's transaction, so every tool key shows on Tools: the first tool of its service that has no Read
    and write key yet takes it as that key, else a new tool is made for it, on the proxy when its service's address is
    known and on computers otherwise. The same rule as the data migration (`TS.is_tool_key`). An Only listed tool never
    takes a key this way (whoever stored it may not see that tool: a key joins a private tool from its own page), nor
    counts toward the new tool's id or name. A key Tools stored itself (`tool:` source) already has its tool; anything
    else is left alone. `from_computer`: a key a bot already
    uses from its computer's files (the file migration) stays on computers, on a computer tool, like the data
    migration's: moving it to the proxy unasked would change a working bot. An id stored as a key (POSTHOG_PROJECT_ID,
    TWILIO_ACCOUNT_SID: `tool_settings.is_setting` on its name and `value`, or its name alone when the value is not at
    hand) never goes behind the proxy, nor on a tool with an address: it goes on a computer tool of its own with no
    address, so its value reaches runs as it is and no check sends it as a key. The tool id, or None."""
    row = c.execute("SELECT * FROM credentials WHERE id=?", (credential_id,)).fetchone()
    if not row or row["tool_id"] or str(row["source"] or "").startswith("tool:"):
        return None
    if row["source"] == "tools-chat":
        return None  # a key dropped in the Tools chat goes to the tool the person grants it to (backend/key_grants.py)
    if row["member_stored"] and str(row["created_by"] or "").startswith("bot:"):
        return None  # a bot's own key goes on a tool of its own (`own_tool`), never on a tool others use
    on_computers = c.execute(
        "SELECT 1 FROM model_subscriptions WHERE credential_id=? UNION SELECT 1 FROM credential_grants "
        "WHERE credential_id=? AND (subject='computers' OR subject LIKE 'computer:%')",
        (credential_id, credential_id),
    ).fetchone()
    if not TS.is_tool_key(
        row["kind"], row["env"], row["name"], row["source"], row["ciphertext"] is not None, bool(on_computers)
    ):
        return None
    service = TS.match(env=row["env"], name=row["name"])
    env = row["env"] or ""
    # A vendor account's other value (TWILIO_API_SECRET beside a Twilio tool's TWILIO_API_KEY) joins its tool.
    home = tool_templates.home_for(c, env, computer_only=from_computer)
    if home:
        c.execute("UPDATE credentials SET tool_id=?, access_level='read_write' WHERE id=?", (home, credential_id))
        H.event(c, who.actor, "tool.key_added", home, {"credential": credential_id, "access_level": "read_write"})
        return home
    # A write key of an ads, payments or CRM vendor stays a key on its tool, policy first: never an id's own tool.
    if not TS.protected(service or "") and (
        tool_settings.is_setting(env, value) if value is not None else tool_settings.known_name(env)
    ):
        tid = TS.insert_tool(c, service, row["name"], "computer", who.actor, H.now(), tid=_unique_id(c, TS.slug(env)))
        c.execute("UPDATE tools SET name=?, base_url='', check_path='' WHERE id=?", (env, tid))
        c.execute("UPDATE credentials SET tool_id=?, access_level='read_write' WHERE id=?", (tid, credential_id))
        H.event(c, who.actor, "tool.key_added", tid, {"credential": credential_id, "access_level": "read_write"})
        return tid
    free = (
        service
        and c.execute(
            "SELECT id FROM tools t WHERE service=? AND audience IS NULL AND kind='api' AND NOT EXISTS (SELECT 1 FROM credentials k WHERE k.tool_id=t.id"
            " AND k.access_level='read_write')"
            + (" AND delivery='computer'" if from_computer else "")
            + " ORDER BY created_at, id LIMIT 1",
            (service,),
        ).fetchone()
    )
    proxy = not from_computer and TS.defaults(service).get("base_url")
    if free:
        tid = free["id"]
    else:
        base = TS.slug(service or row["name"])
        made = _unique_id(c, base)
        first = (
            made == base or c.execute("SELECT 1 FROM tools WHERE id=? AND audience IS NULL", (base,)).fetchone() is None
        )
        tid = TS.insert_tool(
            c, service, row["name"], "proxy" if proxy else "computer", who.actor, H.now(), tid=made, first=first
        )
    c.execute("UPDATE credentials SET tool_id=?, access_level='read_write' WHERE id=?", (tid, credential_id))
    H.event(c, who.actor, "tool.key_added", tid, {"credential": credential_id, "access_level": "read_write"})
    return tid


def own_tool(c, who, credential_id, near="", level="read_write"):
    """Put a key a bot stored for itself (backend/credential_cards.py `store_for_bot`) on a new tool of its own, so it
    shows on Tools like any key and never changes a tool others use: under the vendor of the tool `near` names (one
    everybody may see) or else of its own variable and name, its keys to computers (the bot gets back the value it
    already held), and "Everyone can read" off, so no other bot reads with it until an administrator gives it to them.
    The tool id."""
    row = c.execute("SELECT * FROM credentials WHERE id=?", (credential_id,)).fetchone()
    if not row or row["tool_id"]:
        return row["tool_id"] if row else None
    beside = (
        c.execute(
            "SELECT * FROM tools WHERE id=? AND audience IS NULL AND COALESCE(kind,'api')='api'", (str(near),)
        ).fetchone()
        if near
        else None
    )
    service = (beside["service"] if beside is not None else "") or TS.match(env=row["env"], name=row["name"]) or ""
    bot = H.bot(c, H.actor_id(who.actor)) or {}
    name = (
        f"{beside['name']} ({bot.get('display_name') or bot.get('slug') or H.actor_id(who.actor)})"
        if beside
        else row["name"]
    )
    now = H.now()
    tid = TS.insert_tool(c, service, name, "computer", who.actor, now, tid=_unique_id(c, TS.slug(name)), first=False)
    if beside is not None:
        c.execute(
            "UPDATE tools SET base_url=?, auth_scheme=?, check_path=? WHERE id=?",
            (beside["base_url"], beside["auth_scheme"], beside["check_path"], tid),
        )
    c.execute("UPDATE tools SET everyone_reads=0 WHERE id=?", (tid,))
    c.execute("UPDATE credentials SET tool_id=?, access_level=? WHERE id=?", (tid, level, credential_id))
    H.event(c, who.actor, "tool.created", tid, {"service": service, "delivery": "computer", "own": True})
    H.event(c, who.actor, "tool.key_added", tid, {"credential": credential_id, "access_level": level})
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
    TA.require(c, who, TA.tool(tool_id=tool_id), action=TA.EDIT, admins=vault.admins, auth=vault.auth)
    tool = _readable(c, who, tool_id, vault.admins)
    key = _tool_key(c, tool_id, key_id)
    if (
        body.access_level
        and body.access_level != key["access_level"]
        and _key_at(c, tool_id, body.access_level, key_id, env=key["env"])
    ):
        raise _taken(tool, body.access_level)
    _day(body.expires_on)
    if body.value is not None or body.name is not None or body.username is not None:
        vault.write(
            c,
            who,
            CredentialWrite(
                name=body.name or key["name"],
                username=key["username"] if body.username is None else body.username,
                kind=key["kind"],
                env=key["env"],
                secret=body.value,
                source=key["source"],
                expected_revision=key["revision"],
            ),
            key_id,
        )
    level = body.access_level or key["access_level"]
    if body.expires_on is not None:  # typed by a person; the next check's vendor date, if any, still wins
        expires, source = body.expires_on or None, "person" if body.expires_on else None
    elif body.value is not None and key["expires_source"] == "vendor":
        expires, source = None, None  # the old value's end date: the next check reads the new value's
    else:
        expires, source = key["expires_on"], key["expires_source"]
    c.execute(
        "UPDATE credentials SET access_level=?, expires_on=?, expires_source=? WHERE id=?",
        (level, expires, source, key_id),
    )
    H.event(
        c,
        who.actor,
        "tool.key_updated",
        tool_id,
        {"credential": key_id, "access_level": level, "value_changed": body.value is not None},
    )
    if body.value is not None:
        reconnect_tasks.replaced(c, tool_id)  # its Reconnect task, if one is open, is done
    elif body.expires_on is not None:
        reconnect_tasks.redated(c, tool_id)  # a Renew task ends once no key's end date is near
    return _key_view(c, who, vault.row(c, key_id), True, vault.admins)


def delete_key(c, who, vault, tool_id, key_id):
    _readable(c, who, tool_id, vault.admins, vault.auth)
    key = _tool_key(c, tool_id, key_id)
    if not may_remove(who, key["created_by"], manages=TA.admin(c, who, vault.admins)):
        TA.require(c, who, TA.tool(tool_id=tool_id), action=TA.EDIT, admins=vault.admins, auth=vault.auth)
    TA.person_only(c, who, TA.tool(tool_id=tool_id))
    _drop_key(c, who, key["id"])
    reconnect_tasks.redated(c, tool_id)  # the key it renewed is gone
    return {"deleted": True, "id": key_id}


def _revoke(c, who, key_id, subject):
    for grant in c.execute(
        "SELECT id FROM credential_grants WHERE credential_id=? AND subject=? AND revoked_at IS NULL", (key_id, subject)
    ).fetchall():
        c.execute(
            "UPDATE credential_grants SET revoked_at=?, revoked_by=? WHERE id=?", (H.now(), who.actor, grant["id"])
        )
        H.event(c, who.actor, "credential.revoked", key_id, {"grant": grant["id"], "subject": subject})


def set_level(c, who, vault, tool_id, subject, level):
    """Give `subject` Read, Read and write or no access: grant the one key that level uses, at that level, and take the
    subject's grants on the tool's other keys away first (one value per variable per run). One rule
    (backend/tool_access.py): an administrator's, and through the Tools Owner never a person's own access."""
    TA.require(
        c,
        who,
        TA.tool(tool_id=tool_id),
        subject,
        TA.GRANT if level != "none" else TA.EDIT,
        admins=vault.admins,
        auth=vault.auth,
    )
    tool = _readable(c, who, tool_id, vault.admins)
    subject = subject_of(c, subject)
    if not subject.startswith(("bot:", "human:")):
        raise Problem("subject", "Choose a person or a bot", 422)
    deferred = access_requests.DEFERRED.set(True)  # the grant is made, then its level set: settled once both are
    try:
        _set_level(c, who, vault, tool, tool_id, subject, level)
    finally:
        access_requests.DEFERRED.reset(deferred)
    if subject.startswith("bot:") and level != "none":
        access_requests.settled(c, who, subject=subject, resource_type="tool", resource_id=tool_id)
    return {"subject": subject, "level": subject_level(c, tool_id, subject)}


def _set_level(c, who, vault, tool, tool_id, subject, level):
    keys = [k for k in _keys(c, tool_id) if k["ciphertext"] is not None]
    if _templated(c, tool_id) and level != "none":
        return _set_template_level(c, who, vault, tool, subject, level, keys)
    target = None
    if level == "read":
        target = next((k for k in keys if k["access_level"] == "read"), None) or next(
            (k for k in keys if k["access_level"] == "read_write"), None
        )
        if target is None:
            raise Problem(
                "tool_access", f"{tool['name']} has no stored key. Add one in Tools > {tool['name']} > Keys", 409
            )
    elif level == "read_write":
        target = next((k for k in keys if k["access_level"] == "read_write"), None)
        if target is None:
            raise Problem(
                "tool_access",
                f"{tool['name']} has no Read and write key. Add one in Tools > {tool['name']} > Keys",
                409,
            )
    for key in _keys(c, tool_id):
        if target is None or key["id"] != target["id"]:
            _revoke(c, who, key["id"], subject)
    if target is not None:
        grant = vault.grant(c, who, target["id"], subject)
        c.execute("UPDATE credential_grants SET access_level=? WHERE id=?", (level, grant["id"]))
    H.event(c, who.actor, "tool.access_set", tool_id, {"subject": subject, "level": level})


def _set_template_level(c, who, vault, tool, subject, level, keys):
    """`set_level` on a tool with a template: each variable's key for the level (at Read its read key, else its Read
    and write key at Read; at Read and write its Read and write key), the subject's grants on the tool's other keys
    taken away first."""
    targets = {}
    for key in keys:
        fits = key["access_level"] == "read_write" or level == "read"
        better = key["env"] not in targets or (level == "read" and key["access_level"] == "read")
        if fits and better:
            targets[key["env"]] = key
    if not targets:
        missing = "stored key" if level == "read" else "Read and write key"
        raise Problem("tool_access", f"{tool['name']} has no {missing}. Add one in Tools > {tool['name']} > Keys", 409)
    chosen = {k["id"] for k in targets.values()}
    for key in _keys(c, tool["id"]):
        if key["id"] not in chosen:
            _revoke(c, who, key["id"], subject)
    for key in targets.values():
        grant = vault.grant(c, who, key["id"], subject)
        c.execute("UPDATE credential_grants SET access_level=? WHERE id=?", (level, grant["id"]))
    H.event(c, who.actor, "tool.access_set", tool["id"], {"subject": subject, "level": level})
    return {"subject": subject, "level": subject_level(c, tool["id"], subject)}


# ---- tool-index


def _who_from_bots(bots, more):
    return [{"kind": "bot", "id": b["slug"], "name": b["name"], "initials": _initials(b["name"])} for b in bots], len(
        bots
    ) + more


def _index_row(id, kind, name, status_, href, *, detail="", way="", who=(), who_total=0, needs_you=None, **extra):
    who = list(who)
    return {
        "id": id,
        "kind": kind,
        "name": name,
        "detail": detail,
        "way": way,
        "status": {"tone": status_["tone"], "text": status_["text"]},
        "who": who[:WHO_MAX],
        "who_total": max(who_total, len(who)),
        "needs_you": status_["tone"] == "fail" if needs_you is None else bool(needs_you),
        "href": href,
        **extra,
    }


def _api_rows(c, who, admins, view, access=False):
    admin = _admin(c, who, admins)
    person = who.role in ("owner", "human") and not who.support
    out = []
    for tool in c.execute("SELECT * FROM tools WHERE kind='api' ORDER BY lower(name), id").fetchall():
        if not (admin and tool["audience"] is None) and not visible(c, who, tool, admins, view.auth):
            if person and tool["audience"] is None:
                # An Everyone tool a person holds nothing on: its name and id alone, so they can ask for an agent's
                # access to it. No service, vendor, address or delivery: nothing about how it is set up.
                out.append(
                    _index_row(
                        "api:" + tool["id"],
                        "api",
                        tool["name"],
                        {"tone": "idle", "text": ""},
                        "#/tools/api/" + tool["id"],
                        needs_you=False,
                        vendor="",
                        requestable=True,
                    )
                )
            continue
        keys = _keys(c, tool["id"])
        state = state_of(c, tool, keys)
        people = access_rows(c, who, tool["id"], admins, view.auth, usage=False)
        if who.role in ("owner", "human"):
            # A bot listed is one the reader may see (the same rule every list of bots follows).
            bots = {r["id"] for r in people if r["kind"] == "bot"}
            seen = view.auth.bot_accesses(c, who, sorted(bots)) if bots and not admin else {}
            people = [r for r in people if r["kind"] != "bot" or admin or (seen.get(r["id"]) or {}).get("see")]
        host = re.sub(r"^https://", "", tool["base_url"]).split("/")[0]
        out.append(
            _index_row(
                "api:" + tool["id"],
                "api",
                tool["name"],
                state,
                "#/tools/api/" + tool["id"],
                detail=host,
                way="Proxy" if tool["delivery"] == "proxy" else "On computers",
                who=[{k: r[k] for k in ("kind", "id", "name", "initials")} for r in people],
                who_total=len(people),
                needs_you=state["needs_you"],
                service=tool["service"],
                vendor=TS.vendor_of(
                    tool["service"],
                    tool["name"],
                    [k["env"] for k in keys if k["env"]],
                    tool["base_url"] or _field(tool, "mcp_url") or "",
                ),
                delivery=tool["delivery"],
                private=tool["audience"] is not None,
            )
        )
        if access:  # a vendor's page: who holds it at which level (no use times), and whether it is an MCP server
            out[-1].update(access=people, mcp=bool(_field(tool, "mcp_url")))
    return out


MAILBOX_STATE = {
    "connected": ("ok", "Connected"),
    "not_connected": ("fail", "Not connected"),
    "awaiting_person": ("wait", "Waiting for its person"),
    "stopped": ("idle", "Stopped"),
}


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
            for kind, gid in c.execute(
                "SELECT grantee_type, grantee_id FROM mailbox_accesses WHERE mailbox_id=? ORDER BY created_at",
                (box["id"],),
            ).fetchall():
                subject = ("human:" if kind == "person" else "bot:") + gid
                people.append(_person(subject, view.name(subject)))
            out.append(
                _index_row(
                    "mailbox:" + box["address"],
                    "mailbox",
                    box["address"],
                    {"tone": tone, "text": text},
                    "#/tools/mailbox/" + box["address"],
                    way="Google",
                    who=people,
                    who_total=len(people),
                    mailbox_id=box["id"],
                )
            )
    for _key, row in CN.mailboxes(view, None, INDEX_LIMIT_MAX):
        address = row["name"]
        if address in seen:
            continue
        tone, text = CONNECTION_STATUS.get(row["status"], ("idle", row["status"]))
        people, total = _who_from_bots(row["bots"], row["more_bots"])
        out.append(
            _index_row(
                "mailbox:" + address,
                "mailbox",
                address,
                {"tone": tone, "text": text},
                "#/tools/mailbox/" + address,
                detail=row["readers"],
                way="Mail agent",
                who=people,
                who_total=total,
            )
        )
    return out


def _connection_rows(view):
    """Tico addresses and numbers, mailing mail, Slack, GitHub, meeting importers, Listening and outgoing webhooks, from
    connections.py's sources, as Tools rows."""
    from . import connections as CN

    out = []
    for key, row in CN.inboxes(view, None, INDEX_LIMIT_MAX):
        number = row.get("channel") == "sms"
        tone, text = CONNECTION_STATUS.get(row["status"], ("idle", row["status"]))
        out.append(
            _index_row(
                ("number:" if number else "tico:") + key,
                "number" if number else "mailbox",
                row["name"],
                {"tone": tone, "text": text},
                "#/tools/number/" + key,
                detail=row["detail"],
                way="Tico",
                who_total=0,
            )
        )
    simple = (
        ("postal", CN.postal, "address", "#/tools/address/intake", "Tico"),
        ("slack", CN.slack, "slack", "#/tools/slack", "Slack app"),
        ("github", CN.github, "github", "#/tools/github", "GitHub App"),
        ("meetings", CN.meetings, "meetings", None, "Importer"),
        ("listening", CN.listening, "listening", None, "Tico"),
        ("webhook", CN.webhooks, "webhook", None, "Webhook"),
    )
    for _name, source, kind, href, way in simple:
        for key, row in source(view, None, INDEX_LIMIT_MAX):
            tone, text = CONNECTION_STATUS.get(row["status"], ("idle", row["status"]))
            people, total = _who_from_bots(row["bots"], row["more_bots"])
            out.append(
                _index_row(
                    kind + (":" + key if key else ""),
                    kind,
                    row["name"],
                    {"tone": tone, "text": text},
                    href or row["configure"],
                    detail=row["detail"],
                    way=way,
                    who=people,
                    who_total=total,
                )
            )
    return out


DECLARED = {
    "ready": ("ok", "Ready"),
    "problem": ("fail", "Problem"),
    "pending": ("wait", "Pending"),
    "unknown": ("idle", "Not checked"),
}


def _declared_rows(c, who, view, covered_services, covered_envs):
    """Tools bots declare in bot.yaml (backend/bot_tools.py) that have no tools row, for the bots the reader may read."""
    from . import bot_tools

    found = {}
    for bot in H.bots(c):
        if bot["state"] == "archived":
            continue
        if who.actor != "bot:" + bot["id"] and not (
            who.role in ("owner", "human") and view.auth.bot_access(c, who, bot["id"])["read"]
        ):
            continue
        try:
            tools = bot_tools.listing(c, view.settings, bot["id"])["tools"]
        except Exception as exc:  # noqa: BLE001 - one bot's broken bot.yaml leaves its rows out, never the list
            log.warning("tool-index: declared tools of %s unreadable: %s", bot["id"], type(exc).__name__)
            continue
        for tool in tools:
            service = str(tool.get("service") or "").lower()
            if (
                tool.get("id") in ("model", "repo")
                or not service
                or OWN_ROWS.match(service)
                or service in covered_services
                or (tool.get("env") and tool["env"] in covered_envs)
            ):
                continue
            item = found.setdefault(
                service, {"name": tool.get("name") or bot_tools.service_name(service), "bots": [], "statuses": set()}
            )
            item["bots"].append(bot["id"])
            item["statuses"].add(tool.get("status") or "unknown")
    out = []
    for service, item in sorted(found.items()):
        statuses = item["statuses"]
        state = next(s for s in ("problem", "pending", "ready", "unknown") if s in statuses or s == "unknown")
        tone, text = DECLARED[state]
        bots, more = view.bots(item["bots"])
        people, total = _who_from_bots(bots, more)
        out.append(
            _index_row(
                "declared:" + service,
                "declared",
                item["name"],
                {"tone": tone, "text": text},
                "#/tools/" + service,
                way="Declared",
                who=people,
                who_total=total,
                service=service,
            )
        )
    return out


def _website_rows(c, who, auth, admins):
    """Website tools the reader may see (backend/website_tools.py)."""
    from . import website_tools

    return website_tools.index_rows(c, who, auth, admins)


def index_rows(c, who, auth, settings, admins, access=False):
    """Every Tools row the reader may see, in list order: API tools, website tools, mail and numbers, the company's connections,
    declared tools."""
    from . import connections as CN

    view = CN.view(c, auth, who, settings)
    rows = _api_rows(c, who, admins, view, access)
    covered = {r["service"] for r in rows if r.get("service")}
    # A tool the reader may not see, or sees by name alone (`requestable`), covers nothing: its keys' variables are not
    # the reader's to know, and the list reads as if it did not exist.
    shown = {r["id"][len("api:") :] for r in rows if not r.get("requestable")}
    envs = {
        r[0]
        for r in c.execute("SELECT env, tool_id FROM credentials WHERE tool_id IS NOT NULL AND env<>''")
        if r[1] in shown
    }
    rows += _website_rows(c, who, auth, admins)
    rows += _mailbox_rows(c, who, view)
    rows += _connection_rows(view)
    rows += _declared_rows(c, who, view, covered, envs)
    for row in rows:
        if "vendor" not in row:
            row["vendor"] = (
                BUILT_IN_VENDORS.get(row["kind"], "")
                if row["kind"] != "declared"
                else TS.vendor_of(row.get("service", ""), row["name"])
            )
        if row["vendor"]:
            row["vendor_name"] = TS.vendor_name(row["vendor"])
    return rows


# The connections that are a vendor's: the GitHub App is GitHub's and Slack's app Slack's. Mailboxes, numbers, mailing
# mail, meeting importers, Listening and webhooks are no vendor's: each stays its own row.
BUILT_IN_VENDORS = {"github": "github", "slack": "slack"}


def vendor_page(c, who, auth, settings, admins, vendor):
    """`GET /api/v2/tool-vendors/{vendor}`: everything the reader may see of one vendor, as its Tools rows, each API
    tool's with who can use it at which level (at most WHO_MAX, `access_total` all of them) and whether it is an MCP
    server. Its keys, settings, checks and calls are its own page's (`detail`). A vendor with nothing the reader may see
    is a 404, as a tool is."""
    vendor = str(vendor or "").strip().lower()
    rows = [r for r in index_rows(c, who, auth, settings, admins, access=True) if vendor and r.get("vendor") == vendor]
    if not rows:
        raise Problem("not_found", "No such vendor", 404)
    out = []
    for row in rows[:INDEX_LIMIT_MAX]:
        if row["kind"] == "api":
            row = row | {"access": row["access"][:WHO_MAX], "access_total": len(row["access"])}
        out.append(row)
    return {"vendor": vendor, "name": TS.vendor_name(vendor), "rows": out}


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
    for item in rows[start : start + limit]:
        size = len(encode(item).encode())
        if shown and used + size > INDEX_BYTES_MAX:
            break
        shown.append(item)
        used += size
        end += 1
    counts = {
        "all": len(rows),
        "needs_you": sum(1 for r in rows if r["needs_you"]),
        "api": sum(1 for r in rows if r["kind"] in ("api", "declared")),
        "mail": sum(1 for r in rows if r["kind"] in ("mailbox", "number", "address")),
        "websites": sum(1 for r in rows if r["kind"] == "website"),
    }
    return {"rows": shown, "counts": counts, "next": str(end) if end < len(rows) else None}


# ---- routes


def install(app, store, auth, settings):
    """The routes. The vault is read from app.state at each call (a test swaps its cipher after start)."""
    vault = lambda: app.state.vault

    def change(request, body, authorize, fn):
        v = vault()
        return v.change(
            request.state.identity, request.url.path, request.headers.get("idempotency-key"), body, authorize, fn
        )

    def manager(c, w):
        TA.require(c, w, TA.tool(), action=TA.EDIT, admins=vault().admins, auth=auth)

    @app.get(
        "/api/v2/tool-index",
        **api_v2(
            "Tools",
            "listToolIndex",
            "API tools, mailboxes, Tico addresses and numbers, mailing mail, Slack, GitHub, meeting importers, Listening, outgoing webhooks and declared tools the caller may see, with status and who can use each. Follow next with cursor",
            "ToolIndex",
        ),
    )
    def tool_index(
        request: Request,
        limit: int = Query(default=INDEX_LIMIT_MAX, ge=1, le=INDEX_LIMIT_MAX),
        cursor: str = Query(default="", max_length=20),
    ):
        """Tools: every API tool, mailbox, Tico address and number, mailing mail, Slack, GitHub, meeting importer,
        Listening, outgoing webhook and declared tool the caller may see, with its status and who can use it."""
        with store.read_transaction() as c:
            return index_page(c, request.state.identity, auth, settings, vault().admins, limit, cursor)

    @app.get(
        "/api/v2/tool-vendors/{vendor}",
        **api_v2(
            "Tools",
            "getToolVendor",
            "One vendor's page: its rows on Tools (website logins, MCP servers, API tools, its connections), an API tool's with who can use it at which level (access, at most 8; access_total) and mcp, whether it is an MCP server. A tool's keys and settings are on GET /api/v2/api-tools/{id}",
            "ToolVendor",
        ),
    )
    def tool_vendor(request: Request, vendor: str):
        """One vendor's page on Tools: its website logins, MCP servers, API tools (who holds which level) and the
        connections that are its, as the reader may see them."""
        with store.read_transaction() as c:
            return vendor_page(c, request.state.identity, auth, settings, vault().admins, vendor)

    @app.get(
        "/api/v2/api-tools/{tool_id}",
        **api_v2(
            "Tools",
            "getApiTool",
            "One API tool: its keys (never a value), who can use it at which level, its last checks and calls; to whoever may change it, who can see it and why. A tool the caller may not see is a 404",
            "ApiTool",
        ),
    )
    def get_api_tool(request: Request, tool_id: str):
        with store.read_transaction() as c:
            return detail(c, request.state.identity, tool_id, vault().admins, auth)

    @app.post("/api/v2/api-tools")
    def create_api_tool(request: Request, body: ToolCreate):
        return change(
            request,
            _Receipt(body, omit_none=("key",)),
            manager,
            lambda c: create_tool(c, request.state.identity, body, vault().admins, vault()),
        )

    def setter(c, w):
        if w.role != "owner":  # the owner may change Who can see it (update_tool says what else)
            TA.require(c, w, TA.tool(), action=TA.EDIT, admins=vault().admins, auth=auth)

    @app.patch(
        "/api/v2/api-tools/{tool_id}",
        **api_v2(
            "Tools",
            "updateApiTool",
            "Change a tool's settings (credential administrators) or who can see it: audience null for Everyone, or an audience for Only listed (the owner, or a credential administrator who can see it, who then stays on the list)",
            "ApiToolSettings",
        ),
    )
    def update_api_tool(request: Request, tool_id: str, body: ToolUpdate):
        """A tool's settings and its Who can see it (`audience`: null for Everyone, or an audience for Only listed)."""
        return change(
            request, body, setter, lambda c: update_tool(c, request.state.identity, tool_id, body, vault().admins, auth)
        )

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
        return change(
            request, _Receipt(body), manager, lambda c: add_key(c, request.state.identity, vault(), tool_id, body)
        )

    @app.patch("/api/v2/api-tools/{tool_id}/keys/{key_id}")
    def update_api_tool_key(request: Request, tool_id: str, key_id: str, body: KeyUpdate):
        return change(
            request,
            _Receipt(body),
            manager,
            lambda c: update_key(c, request.state.identity, vault(), tool_id, key_id, body),
        )

    @app.delete("/api/v2/api-tools/{tool_id}/keys/{key_id}")
    def delete_api_tool_key(request: Request, tool_id: str, key_id: str):
        return plain(request, lambda c, who: delete_key(c, who, vault(), tool_id, key_id))

    @app.put("/api/v2/api-tools/{tool_id}/access/{subject}")
    def set_api_tool_access(request: Request, tool_id: str, subject: str, body: AccessSet):
        return change(
            request,
            _Receipt(body, subject=subject),
            manager,
            lambda c: set_level(c, request.state.identity, vault(), tool_id, subject, body.level),
        )

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
        return {
            "ok": worst["ok"],
            "status_code": worst["status_code"],
            "detail": worst["detail"],
            "checked_at": worst["checked_at"],
            "rows": [{k: r[k] for k in ("credential_id", "ok", "status_code", "detail", "checked_at")} for r in rows],
        }

    @app.get(
        "/api/v2/api-tools/{tool_id}/activity",
        **api_v2(
            "Tools",
            "listApiToolActivity",
            "Calls through the proxy and uses of its keys, newest first. Follow next with cursor",
            "ApiToolActivity",
        ),
    )
    def api_tool_activity(
        request: Request,
        tool_id: str,
        cursor: str = Query(default="", max_length=200),
        limit: int = Query(default=50, ge=1, le=ACTIVITY_MAX),
        result: str = Query(default="", max_length=20),
        since: str = Query(default="", max_length=40),
    ):
        with store.read_transaction() as c:
            return activity(c, request.state.identity, tool_id, vault().admins, auth, cursor, limit, result, since)
