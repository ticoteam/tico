# pyright: strict
"""Jira as a source (backend/item_sources): issue links, and each linked issue's state read through its connection.

A link to a Jira issue is stored typed (source jira, item type issue, key `PROJ-123`) with the connection it came
through, so a task can be found by its issue. The key is the issue key as Jira shows it (upper case). Jira keys are
unique within one site, so an issue is always one connection's: the tool whose base URL or sites are on the link's host
(`site`, item_sources.outside_item). A link on a site no connection serves stays a plain link. No requirement asks
anything of an issue (CONDITIONS is empty).

Metadata (docs/dev/pipelines-v3.md "Outside items"): one `item_metadata` row per issue and connection. `fields` hold
`state` (open, or closed when Jira's status category is done), `status` (its name), `category` (Jira's status category
key: new, indeterminate or done), `summary`, `updated` (Jira's own stamp) and `connection_id`. An issue can always be
reopened, so it is never final. How it comes in:
- Push: a Jira webhook to any trigger (backend/triggers.py `_route` hands it to `webhook`) names an issue; each linked
  copy of it on a connection serving the webhook's site is marked due. The payload is not trusted for the state: the
  next pass reads Jira.
- The pass (`refresh`, the heartbeat's item metadata step): an issue an open task links whose metadata is missing,
  marked due, or older than REREAD_S (a slow backstop) is read, at most REREAD_BATCH a pass.
A read uses the connection's own key, chosen for someone allowed to use the tool (api_tools.key_for): the owner of a
task holding the issue's primary link first, then the owners of the other open tasks linking it, then whoever linked it.
With nobody allowed, or Jira refusing, the row keeps what it had with `read_error` and `refused_at`, and it is tried again
after RETRY_S. The key never leaves the read: it is not logged, stored or put in an error.

A primary link's issue turning closed asks the task's owner once whether to settle the task or keep it; reopening takes
that ask back (backend/item_intake.py `closed_outside`). A secondary link's state is only shown.
"""

import json
import logging
import re
from collections.abc import Callable
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import quote, urlsplit

from . import Condition, Item, hosts, serving

if TYPE_CHECKING:
    import sqlite3

Row = dict[str, Any]
Send = Callable[[str, dict[str, str], dict[str, str]], tuple[int, object]]

SOURCE, ISSUE = "jira", "issue"
NAME = "Jira"
ITEMS = {ISSUE: Item(ISSUE, "Issue")}
CONDITIONS: dict[str, Condition] = {}
REREAD_S = 3600  # the backstop: an issue an open task links is read again when its metadata is older than this
RETRY_S = 300  # a read nobody may make, or Jira refused, is tried again after this
REREAD_BATCH = 50
TIMEOUT_S = 20.0
MAX_BODY = 512 * 1024
FIELDS = "summary,status,updated"
TRANSPORT: Any = None  # tests: an httpx.MockTransport at the network edge
LOG = logging.getLogger("tico.jira_source")

KEY = re.compile(r"^([A-Za-z][A-Za-z0-9_]*-[1-9][0-9]*)$")
# Jira Cloud's issue page: https://<site>.atlassian.net/browse/PROJ-123, and the board view's ?selectedIssue=PROJ-123.
ISSUE_URL = re.compile(
    r"^(?i:https://[a-z0-9-]+\.atlassian\.net)/(?:browse/([A-Za-z][A-Za-z0-9_]*-[1-9][0-9]*)(?:[/?#]\S*)?$"
    r"|\S*[?&]selectedIssue=([A-Za-z][A-Za-z0-9_]*-[1-9][0-9]*)(?:[&#]\S*)?$)"
)
_OFFSET = re.compile(r"([+-]\d{2})(\d{2})$")


def _h() -> Any:
    from .. import hubdb

    return hubdb


def match_key(item_type: str, text: str) -> str | None:
    """An issue key (`proj-123` or `PROJ-123`) as Jira spells it, `PROJ-123`; None for anything else."""
    found = KEY.match(text.strip()) if item_type == ISSUE else None
    return found.group(1).upper() if found else None


def match_link(url: str) -> tuple[str, str] | None:
    """(item type, item key) of a Jira Cloud issue link, or None."""
    found = ISSUE_URL.match(url.strip())
    if not found:
        return None
    return ISSUE, (found.group(1) or found.group(2)).upper()


def site(url: str) -> str | None:
    """The Jira site a link is on: its host, lower case."""
    return (urlsplit(url.strip()).hostname or "").lower() or None


def item_url(conn: "sqlite3.Connection", item_type: str, key: str, connection_id: str) -> str:
    """The issue's page on the one site its connection serves (item_sources.serving): what an intake links. ValueError,
    saying why, when the connection is no tool or is on more than one host."""
    if item_type != ISSUE:
        raise ValueError(f"Jira has no item type {item_type}")
    tool = conn.execute("SELECT base_url, sites FROM tools WHERE id=?", (connection_id,)).fetchone()
    if tool is None:
        raise ValueError(f"No connection {connection_id}: a connection is a tool's id")
    on = hosts(tool[0], tool[1])
    if len(on) != 1:
        raise ValueError(f"{connection_id} is not on one Jira site: its base URL and sites must name a single host")
    return f"https://{next(iter(on))}/browse/{key}"


def stamp_ms(text: object) -> int:
    """Jira's `updated` (2026-11-21T10:00:00.000+0000) or any ISO-8601 time with a zone, in ms; 0 when it is none."""
    raw = _OFFSET.sub(r"\1:\2", str(text or "").strip().replace("Z", "+00:00"))
    try:
        at = datetime.fromisoformat(raw)
    except ValueError:
        return 0
    if at.tzinfo is None:
        return 0
    return int(at.astimezone(timezone.utc).timestamp() * 1000)


def ref(key: str, connection_id: str) -> str:
    """An issue as item_sources.parse_ref reads it: `jira:issue:PROJ-12@<tool id>`."""
    return f"{SOURCE}:{ISSUE}:{key}@{connection_id}"


# ----------------------------------------------------------------------------- push: a webhook names an issue


def webhook(conn: "sqlite3.Connection", payload: object) -> list[Row]:
    """A trigger's call that is a Jira issue event (`webhookEvent` jira:issue_*, with `issue.key` and `issue.self`):
    each copy of that issue a task links, on a connection serving the site `issue.self` is on, is marked due, so the
    next pass reads it through its connection. One result per copy marked (trigger results); [] for anything else."""
    from .. import item_metadata as IM

    if not isinstance(payload, dict):
        return []
    body = cast("dict[str, object]", payload)
    issue = body.get("issue")
    if not str(body.get("webhookEvent") or "").startswith("jira:issue_") or not isinstance(issue, dict):
        return []
    found = cast("dict[str, object]", issue)
    key = match_key(ISSUE, str(found.get("key") or ""))
    host = site(str(found.get("self") or ""))
    if not key or not host:
        return []
    out: list[Row] = []
    for connection_id in serving(conn, host):
        if not IM.linked(conn, SOURCE, ISSUE, key, connection_id):
            continue
        IM.mark_due(conn, SOURCE, ISSUE, key, connection_id)
        out.append({"outcome": "item", "target": ref(key, connection_id), "ok": True, "detail": "read again"})
    return out


# ----------------------------------------------------------------------------- the pass: reads through the connection


def due_reads(conn: "sqlite3.Connection", now: str, limit: int = REREAD_BATCH) -> list[tuple[str, str]]:
    """(key, connection) of each issue to read now, longest unread first, at most `limit`: one an open task links whose
    metadata is missing, never read, marked due or older than REREAD_S, or one marked due whatever links it, unless a
    read of it failed within RETRY_S."""
    H = _h()
    cutoff, retry = H.shift(now, seconds=-REREAD_S), H.shift(now, seconds=-RETRY_S)
    found = conn.execute(
        "SELECT MIN(l.item_key), l.connection_id, MIN(COALESCE(m.read_at, '')) AS seen FROM task_links l "
        "JOIN tasks t ON t.id = l.task_id "
        "LEFT JOIN item_metadata m ON m.source = l.source AND m.item_type = l.item_type AND m.item_key = l.item_key "
        "AND m.connection_id = l.connection_id "
        "WHERE l.source=? AND l.item_type=? AND l.connection_id <> '' "
        "AND (m.refused_at IS NULL OR m.refused_at < ?) "
        "AND (t.status = 'open' AND (m.id IS NULL OR m.read_at IS NULL OR m.read_at < ? OR m.due_at IS NOT NULL) "
        "OR m.due_at IS NOT NULL) "
        "GROUP BY l.item_key, l.connection_id ORDER BY seen, MIN(l.item_key) LIMIT ?",
        (SOURCE, ISSUE, retry, cutoff, limit),
    ).fetchall()
    return [(str(key), str(connection_id)) for key, connection_id, _ in found]


def readers(conn: "sqlite3.Connection", key: str, connection_id: str) -> list[str]:
    """Who an issue is read for, in order: the owner of the task holding its primary link, the owners of the other open
    tasks linking it, then whoever linked it."""
    out: list[str] = []
    for (owner,) in conn.execute(
        "SELECT t.owner_id FROM task_links l JOIN tasks t ON t.id = l.task_id "
        "WHERE l.source=? AND l.item_type=? AND l.item_key=? AND l.connection_id=? "
        "ORDER BY l.is_primary DESC, t.status = 'open' DESC, l.created_at",
        (SOURCE, ISSUE, key, connection_id),
    ).fetchall():
        out.append(str(owner or ""))
    for (added_by,) in conn.execute(
        "SELECT l.added_by FROM task_links l WHERE l.source=? AND l.item_type=? AND l.item_key=? AND l.connection_id=? "
        "ORDER BY l.is_primary DESC, l.created_at",
        (SOURCE, ISSUE, key, connection_id),
    ).fetchall():
        out.append(str(added_by or ""))
    return [who for who in dict.fromkeys(out) if who]


class Unreadable(Exception):
    """A read that cannot be made or that Jira refused; its words go in `read_error`, never a key."""


def request(
    conn: "sqlite3.Connection", vault: Any, key: str, connection_id: str
) -> tuple[str, dict[str, str], dict[str, str]]:
    """(url, headers, query) of the read of one issue through its connection, with the key of the first of `readers`
    allowed to use the tool (api_tools.key_for). Unreadable when the connection has no API address, uses a key template,
    or nobody on its tasks may use it. Runs in a write transaction: decrypting may reseal the key in place."""
    from .. import api_tools as _api_tools
    from .. import tool_services as _tool_services
    from ..store import Problem

    AT: Any = _api_tools
    TS: Any = _tool_services
    tool = conn.execute("SELECT * FROM tools WHERE id=?", (connection_id,)).fetchone()
    if tool is None:
        raise Unreadable("The connection is gone")
    base = str(tool["base_url"] or "").rstrip("/")
    if not base.startswith("https://"):
        raise Unreadable(f"{tool['name']} has no https API address")
    if "auth_template" in tool.keys() and tool["auth_template"]:
        raise Unreadable(f"{tool['name']} uses a key template; Tico reads Jira with one key")
    credential = None
    for subject in readers(conn, key, connection_id):
        try:
            credential = AT.key_for(conn, connection_id, subject)[0]
            break
        except Problem:
            continue
    if credential is None:
        raise Unreadable(f"Nobody on the tasks linking {key} may use {tool['name']}")
    secret = str(vault.cipher.decrypt(conn, credential))
    service = cast("dict[str, Any]", TS.SERVICES.get(tool["service"]) or {})
    username = str(credential["username"] or service.get("username") or "")
    try:
        headers, params = cast(
            "tuple[dict[str, str], dict[str, str]]", TS.apply_auth(tool["auth_scheme"], secret, username)
        )
    except ValueError:
        raise Unreadable(f"{tool['name']} has an auth scheme Tico does not know") from None
    url = f"{base}/rest/api/3/issue/{quote(key)}"
    return url, {**headers, "Accept": "application/json", "User-Agent": "Tico-item-read"}, {**params, "fields": FIELDS}


def send(url: str, headers: dict[str, str], params: dict[str, str]) -> tuple[int, object]:
    """One GET to a public https address, pinned to the IP it resolved to, no redirects, no proxy from the environment:
    (status, the JSON answer or None). Raises when the call could not be made."""
    import httpx

    from .. import egress as _egress

    egress: Any = _egress
    parts = egress.check(url)
    address = egress.address_for(parts, budget=TIMEOUT_S)
    pinned = httpx.URL(url, params=params or None).copy_with(host=address)
    host = str(parts.hostname) + (f":{parts.port}" if parts.port else "")
    with httpx.Client(timeout=TIMEOUT_S, follow_redirects=False, trust_env=False, transport=TRANSPORT) as client:
        with client.stream(
            "GET", pinned, headers={**headers, "Host": host}, extensions={"sni_hostname": parts.hostname}
        ) as response:
            body = b""
            for chunk in response.iter_bytes():
                body += chunk
                if len(body) > MAX_BODY:
                    break
            try:
                answer: object = json.loads(body)
            except ValueError:
                answer = None
            return response.status_code, answer


def fields_of(answer: object, connection_id: str) -> Row | None:
    """The metadata fields of Jira's answer to an issue read, or None when it is not one."""
    if not isinstance(answer, dict):
        return None
    got = cast("dict[str, object]", cast("dict[str, object]", answer).get("fields") or {})
    status = cast("dict[str, object]", got.get("status") or {}) if isinstance(got.get("status"), dict) else {}
    category = status.get("statusCategory")
    category_key = str(cast("dict[str, object]", category).get("key") or "") if isinstance(category, dict) else ""
    if not status:
        return None
    return {
        "state": "closed" if category_key == "done" else "open",
        "status": str(status.get("name") or ""),
        "category": category_key,
        "summary": str(got.get("summary") or ""),
        "updated": str(got.get("updated") or ""),
        "connection_id": connection_id,
    }


def save_issue(conn: "sqlite3.Connection", key: str, connection_id: str, fields: Row, version: int) -> bool:
    """Save what a read saw of an issue at `version` (newest wins, item_metadata.save), keeping what Tico noted beside it
    (an intake's `intake_updated`), and clear a failed read's marks. A primary link's issue that turned closed asks
    its task's owner once; one that turned open again takes the ask back. Whether it was saved."""
    from .. import item_intake
    from .. import item_metadata as IM

    old = IM.get(conn, SOURCE, ISSUE, key, connection_id)
    kept = {name: value for name, value in (old or {}).get("fields", {}).items() if name == "intake_updated"}
    merged = {**fields, **kept}
    if not IM.save(
        conn, SOURCE, ISSUE, key, merged, version, title=fields.get("summary") or None, connection_id=connection_id
    ):
        return False
    conn.execute(
        "UPDATE item_metadata SET read_error=NULL, refused_at=NULL WHERE source=? AND item_type=? AND item_key=? "
        "AND connection_id=?",
        (SOURCE, ISSUE, key, connection_id),
    )
    was = str((old or {}).get("fields", {}).get("state") or "")
    now = str(fields.get("state") or "")
    words = f"{NAME} says {fields.get('status') or now}"
    if now == "closed" and was != "closed":
        item_intake.closed_outside(conn, SOURCE, ISSUE, key, connection_id, words)
    elif now == "open" and was == "closed":
        item_intake.reopened_outside(conn, SOURCE, ISSUE, key, connection_id, words)
    return True


def failed(conn: "sqlite3.Connection", key: str, connection_id: str, why: str) -> None:
    """A read that could not be made or that Jira refused: the metadata stays as it was, the reason is kept, and the
    issue is tried again after RETRY_S."""
    H = _h()
    now = H.now()
    found = conn.execute(
        "UPDATE item_metadata SET read_error=?, refused_at=?, updated_at=? WHERE source=? AND item_type=? "
        "AND item_key=? AND connection_id=?",
        (why[:300], now, now, SOURCE, ISSUE, key, connection_id),
    )
    if found.rowcount == 0:
        conn.execute(
            "INSERT INTO item_metadata (id, source, item_type, item_key, connection_id, fields, metadata_version, "
            "read_error, refused_at, created_at, updated_at) VALUES (?,?,?,?,?,'{}',0,?,?,?,?)",
            (H.new_id(), SOURCE, ISSUE, key, connection_id, why[:300], now, now, now),
        )
    LOG.info("Reading %s failed: %s", ref(key, connection_id), why)


def _refused(status: int) -> str:
    if status in (401, 403):
        return f"Jira refused the key ({status})"
    if status == 404:
        return "Jira has no such issue, or the key cannot see it (404)"
    return f"Jira answered {status}"


def refresh(store: Any, vault: Any, now: str | None = None, sender: Send | None = None) -> int:
    """The pass: each of `due_reads` read through its connection with no transaction held, then saved with the version
    taken before its read. Returns how many issues' metadata changed."""
    from .github import version_now

    H = _h()
    at: str = now or H.now()
    call = sender or send
    with store.read() as conn:
        due = due_reads(conn, at)
    changed = 0
    for key, connection_id in due:
        try:
            with store.transaction() as conn:
                url, headers, params = request(conn, vault, key, connection_id)
        except Exception as exc:  # noqa: BLE001 - one issue that cannot be read leaves the others to their reads
            why = str(exc) if isinstance(exc, Unreadable) else "The connection's key could not be read"
            with store.transaction() as conn:
                failed(conn, key, connection_id, why)
            continue
        version = version_now()
        try:
            status, answer = call(url, headers, params)
        except Exception as exc:  # noqa: BLE001 - Jira down or the address refused: the metadata waits for a retry
            why = "Jira could not be reached" + (" (timed out)" if "Timeout" in type(exc).__name__ else "")
            with store.transaction() as conn:
                failed(conn, key, connection_id, why)
            continue
        fields = fields_of(answer, connection_id) if 200 <= status < 300 else None
        with store.transaction() as conn:
            if fields is None:
                failed(
                    conn,
                    key,
                    connection_id,
                    _refused(status) if not 200 <= status < 300 else "Jira's answer has no status",
                )
            elif save_issue(conn, key, connection_id, fields, version):
                changed += 1
    return changed
