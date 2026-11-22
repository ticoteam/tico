# pyright: strict
"""Jira as a source (backend/item_sources): issue links, and each linked issue's state read through its connection.

A link to a Jira issue is stored typed (source jira, item type issue, key `PROJ-123`) with the connection it came
through, so a task can be found by its issue. The key is the issue key as Jira shows it (upper case). Jira keys are
unique within one site, so an issue is always one connection's: the tool whose base URL or sites are on the link's host
(`site`, item_sources.outside_item). A link on a site no connection serves stays a plain link. No requirement asks
anything of an issue (CONDITIONS is empty). An issue moved to another project gets a new key; a task linking the old key
keeps it (a second task filed for the new key shows as a duplicate to merge).

Metadata (docs/dev/pipelines-v3.md "Outside items"): one `item_metadata` row per issue and connection. `fields` hold
`state` (open, or closed when Jira's status category is done), `status` (its name), `category` (Jira's status category
key: new, indeterminate or done), `summary`, `updated` (Jira's own stamp) and `connection_id`. An issue can always be
reopened, so it is never final. Whoever sees a task sees its links' state.

Reads use only a company-wide connection's own key: a tool whose audience is Everyone (api_tools.audience_of None), its
read key when it has one, else its read and write key, sent as a GET. A private tool's issues are never read. How a read
comes about:
- Push: a Jira issue event to a trigger bound to the connection (backend/triggers.py) marks that connection's linked
  copy of the issue due (`webhook`). The payload only names the issue; a payload that does not read as one is ignored.
- The pass (the heartbeat's `jira_items` step, `refresh`): issues an open task links whose metadata is missing, marked
  due, or older than REREAD_S (a slow backstop), at most PER_CONNECTION per connection and PASS_BUDGET_S of reading a
  pass, so Jira never holds the other outside polls for long.
A read that saves clears the due mark only when it is the mark the read began under: a webhook's mark made while the
read was out survives for the next pass. A read Jira refuses keeps the metadata as it was, with `read_error` and
`refused_at`, and is tried again after RETRY_S. The key never leaves the read: it is not logged, stored or put in an
error.
"""

import json
import logging
import re
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import quote, urlsplit

from . import Condition, Item, hosts

if TYPE_CHECKING:
    import sqlite3

Row = dict[str, Any]
Send = Callable[[str, dict[str, str], dict[str, str]], tuple[int, object]]

SOURCE, ISSUE = "jira", "issue"
NAME = "Jira"
ITEMS = {ISSUE: Item(ISSUE, "Issue")}
CONDITIONS: dict[str, Condition] = {}
REREAD_S = 3600  # the backstop: an issue an open task links is read again when its metadata is older than this
RETRY_S = 300  # a read Jira refused, or that could not be made, is tried again after this
PER_CONNECTION = 10  # reads of one connection's issues in one pass
PASS_BUDGET_S = 30.0  # no read starts after a pass has spent this long reading
TIMEOUT_S = 10.0
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


def ref(key: str, connection_id: str) -> str:
    """An issue as item_sources.parse_ref reads it: `jira:issue:PROJ-12@<tool id>`."""
    return f"{SOURCE}:{ISSUE}:{key}@{connection_id}"


# ----------------------------------------------------------------------------- push: a webhook names an issue


def webhook(conn: "sqlite3.Connection", connection_id: str, payload: object) -> int:
    """A call to a trigger bound to `connection_id`: when it is a Jira issue event (`webhookEvent` jira:issue_*, with
    `issue.key`), that connection's linked copy of the issue is marked due. How many were marked (0 or 1); anything that
    is not such an event marks nothing."""
    from .. import item_metadata as IM

    body = cast("dict[str, object]", payload) if isinstance(payload, dict) else {}
    issue = body.get("issue")
    event = body.get("webhookEvent")
    if not isinstance(event, str) or not event.startswith("jira:issue_") or not isinstance(issue, dict):
        return 0
    named = cast("dict[str, object]", issue).get("key")
    key = match_key(ISSUE, named) if isinstance(named, str) else None
    if not key or not IM.linked(conn, SOURCE, ISSUE, key, connection_id):
        return 0
    IM.mark_due(conn, SOURCE, ISSUE, key, connection_id)
    return 1


# ----------------------------------------------------------------------------- the pass: reads through the connection


def due_reads(conn: "sqlite3.Connection", now: str, per_connection: int = PER_CONNECTION) -> list[tuple[str, str, str]]:
    """(key, connection, due mark) of each issue to read now, longest unread first, at most `per_connection` of each
    company-wide connection: one an open task links whose metadata is missing, never read, marked due or older than
    REREAD_S, unless a read of it failed within RETRY_S. The due mark ('' for none) is what the read began under."""
    H = _h()
    cutoff, retry = H.shift(now, seconds=-REREAD_S), H.shift(now, seconds=-RETRY_S)
    found = conn.execute(
        "SELECT key, connection_id, due FROM (SELECT MIN(l.item_key) AS key, l.connection_id, "
        "COALESCE(MAX(m.due_at), '') AS due, ROW_NUMBER() OVER (PARTITION BY l.connection_id "
        "ORDER BY MIN(COALESCE(m.read_at, '')), MIN(l.item_key)) AS n FROM task_links l "
        "JOIN tasks t ON t.id = l.task_id AND t.status = 'open' "
        "JOIN tools tl ON tl.id = l.connection_id AND tl.audience IS NULL "
        "LEFT JOIN item_metadata m ON m.source = l.source AND m.item_type = l.item_type AND m.item_key = l.item_key "
        "AND m.connection_id = l.connection_id "
        "WHERE l.source=? AND l.item_type=? AND l.connection_id <> '' "
        "AND (m.refused_at IS NULL OR m.refused_at < ?) "
        "AND (m.id IS NULL OR m.read_at IS NULL OR m.read_at < ? OR m.due_at IS NOT NULL) "
        "GROUP BY l.item_key, l.connection_id) WHERE n <= ? ORDER BY n, connection_id",
        (SOURCE, ISSUE, retry, cutoff, per_connection),
    ).fetchall()
    return [(str(key), str(connection_id), str(due)) for key, connection_id, due in found]


class Unreadable(Exception):
    """A read that cannot be made or that Jira refused; its words go in `read_error`, never a key."""


def request(
    conn: "sqlite3.Connection", vault: Any, key: str, connection_id: str
) -> tuple[str, dict[str, str], dict[str, str]]:
    """(url, headers, query) of the read of one issue with the connection's own key: its read key, else its read and
    write key. Unreadable when the connection is gone, private, has no https API address or stored key, or uses a key
    template. Runs in a write transaction: decrypting may reseal the key in place."""
    from .. import api_tools as _api_tools
    from .. import tool_services as _tool_services

    AT: Any = _api_tools
    TS: Any = _tool_services
    tool = conn.execute("SELECT * FROM tools WHERE id=?", (connection_id,)).fetchone()
    if tool is None:
        raise Unreadable("The connection is gone")
    if AT.audience_of(tool) is not None:
        raise Unreadable(f"{tool['name']} is not company-wide: Tico reads only an Everyone connection's issues")
    base = str(tool["base_url"] or "").rstrip("/")
    if not base.startswith("https://"):
        raise Unreadable(f"{tool['name']} has no https API address")
    if "auth_template" in tool.keys() and tool["auth_template"]:
        raise Unreadable(f"{tool['name']} uses a key template; Tico reads Jira with one key")
    credential = conn.execute(
        "SELECT * FROM credentials WHERE tool_id=? AND ciphertext IS NOT NULL "
        "ORDER BY access_level <> 'read', created_at, id LIMIT 1",
        (connection_id,),
    ).fetchone()
    if credential is None:
        raise Unreadable(f"{tool['name']} has no stored key")
    secret = str(vault.cipher.decrypt(conn, credential))
    service = cast("dict[str, Any]", TS.SERVICES.get(tool["service"]) or {})
    username = str(credential["username"] or service.get("username") or "")
    try:
        auth = cast("tuple[dict[str, str], dict[str, str]]", TS.apply_auth(tool["auth_scheme"], secret, username))
    except ValueError:
        raise Unreadable(f"{tool['name']} has an auth scheme Tico does not know") from None
    headers, params = auth
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


def save_issue(
    conn: "sqlite3.Connection", key: str, connection_id: str, fields: Row, version: int, due_seen: str = ""
) -> bool:
    """Save what a read saw of an issue at `version` (newest wins, item_metadata.save) and clear a failed read's marks.
    The due mark is cleared only when it is still `due_seen`, the one the read began under: a newer mark stays. Whether
    it was saved."""
    from .. import item_metadata as IM

    scoped = "source=? AND item_type=? AND item_key=? AND connection_id=?"
    item = (SOURCE, ISSUE, key, connection_id)
    found = conn.execute(f"SELECT due_at FROM item_metadata WHERE {scoped}", item).fetchone()
    due_now = str(found[0] or "") if found else ""
    title = fields.get("summary") or None
    if not IM.save(conn, SOURCE, ISSUE, key, fields, version, title=title, connection_id=connection_id):
        return False
    after = conn.execute(f"SELECT due_at FROM item_metadata WHERE {scoped}", item).fetchone()[0]
    # A tie at the same version keeps or sets its own mark (item_metadata.save); else a mark newer than the read's stays.
    kept = after or (due_now if due_now and due_now != due_seen else None)
    conn.execute(f"UPDATE item_metadata SET read_error=NULL, refused_at=NULL, due_at=? WHERE {scoped}", (kept, *item))
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


def refresh(
    store: Any,
    vault: Any,
    now: str | None = None,
    sender: Send | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    """The pass: each of `due_reads` read through its connection with no transaction held, then saved with the version
    taken before its read; no read starts once the pass has spent PASS_BUDGET_S. Returns how many issues' metadata
    changed."""
    from .github import version_now

    H = _h()
    at: str = now or H.now()
    call = sender or send
    with store.read() as conn:
        due = due_reads(conn, at)
    changed, started = 0, clock()
    for key, connection_id, due_seen in due:
        if clock() - started > PASS_BUDGET_S:
            break
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
                    conn, key, connection_id, "Jira's answer has no status" if 200 <= status < 300 else _refused(status)
                )
            elif save_issue(conn, key, connection_id, fields, version, due_seen):
                changed += 1
    return changed
