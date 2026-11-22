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
key: new, indeterminate or done), `updated` (Jira's own stamp) and `connection_id`; no summary is fetched. An issue can
always be reopened, so it is never final.

Background reads go only through a record integration (`integration`): an API tool for everyone that an administrator
marked as holding records (`tools.record_owner`), not one a bot keeps for itself, with a shared company API key (no
sign-in, no holder): its read key when it has one, else its read and write key, sent as a GET. Nothing else is read.
The pass (the heartbeat's `jira_items` step, after the merge queue's, `refresh`) reads issues marked due first, then
those an open task links whose metadata is missing or older than REREAD_S (every 5 minutes), at most PER_CONNECTION per
connection and PASS_BUDGET_S of reading a pass. Each request has one deadline (TIMEOUT_S, or what is left of the pass
when it is sent) over the name's lookup, connecting, TLS and the answer, and at most MAX_IN_FLIGHT are out at once.
A read that saves clears the due mark only when it is the mark the read began under: a mark made while the read was
out survives for the next pass. A read Jira refuses keeps the metadata as it was, with `read_error` and `refused_at`,
and is tried again after RETRY_S. The key never leaves the read: it is not logged, stored or put in an error.
After each read of an issue a task was filed for (saved, or Jira's 404), backend/release_closeout.py `watch` asks the
task's owner once when the issue was withdrawn, closed or deleted outside Tico, and takes the ask back when it is
active again.

Writes (a transition, a comment) go only through the outbound gate's Jira sender (backend/outbound_jira.py), through the
same record integrations with a shared read and write key (`connect` with `write`, `writable`) and the same bounded
`call`.
"""

import json
import logging
import re
import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, NamedTuple, cast
from urllib.parse import quote, urlsplit

from . import Condition, Item, hosts

if TYPE_CHECKING:
    import sqlite3

Row = dict[str, Any]
Send = Callable[[str, dict[str, str], dict[str, str], float], tuple[int, object]]

SOURCE, ISSUE = "jira", "issue"
NAME = "Jira"
ITEMS = {ISSUE: Item(ISSUE, "Issue")}
CONDITIONS: dict[str, Condition] = {}
REREAD_S = 300  # an issue an open task links is read again when its metadata is older than this
RETRY_S = 300  # a read Jira refused, or that could not be made, is tried again after this
PER_CONNECTION = 10  # reads of one connection's issues in one pass
PASS_BUDGET_S = 30.0  # no read starts after a pass has spent this long reading
TIMEOUT_S = 10.0
MAX_IN_FLIGHT = 2  # reads out at once, a timed-out one counted until its thread ends
_in_flight = [0]
_in_flight_lock = threading.Lock()
MAX_BODY = 512 * 1024
FIELDS = "status,updated"
CATEGORIES = ("new", "indeterminate", "done")  # Jira's status categories
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


SITE = re.compile(r"^[a-z0-9-]+\.atlassian\.net$")  # a Jira Cloud site, as ISSUE_URL reads one


def jira_site(base_url: object, sites: object) -> str | None:
    """The one Jira Cloud site a tool is on: its base URL and every site on one `<site>.atlassian.net` host, else None.
    The one endpoint check behind a primary link (item_sources.primary_of) and the background reads, both through
    `integrations`."""
    on = hosts(base_url, sites)
    host = next(iter(on)) if len(on) == 1 else ""
    return host if SITE.match(host) else None


def ref(key: str, connection_id: str) -> str:
    """An issue as item_sources.parse_ref reads it: `jira:issue:PROJ-12@<tool id>`."""
    return f"{SOURCE}:{ISSUE}:{key}@{connection_id}"


# ----------------------------------------------------------------------------- the pass: reads through the connection

# A record integration Tico reads in the background: an API tool for everyone that an administrator marked as holding
# records (`record_owner`), never one a bot keeps for itself (its keys go to computers, it made it), and its key a
# shared company API key (no sign-in, no holder). Nothing else is read without someone asking.
INTEGRATION = (
    "tl.audience IS NULL AND COALESCE(tl.kind, 'api') = 'api' AND tl.record_owner <> '' "
    "AND tl.delivery <> 'computer' AND tl.created_by NOT LIKE 'bot:%'"
)
SHARED_KEY = "k.ciphertext IS NOT NULL AND k.kind <> 'login' AND COALESCE(k.holder, '') = ''"
WRITE_KEY = "AND k.access_level = 'read_write'"  # a write goes only with a read and write key


def integrations(conn: "sqlite3.Connection", connection_id: str | None = None) -> dict[str, str]:
    """{tool id: Jira site} of the record integrations on one Jira site (`jira_site`) with a shared key, or of the one
    `connection_id` names: the tools Tico reads issues through, and a primary link may name (item_sources.primary_of)."""
    one = "AND tl.id=? " if connection_id is not None else ""
    found: dict[str, str] = {}
    for tool_id, base_url, sites in conn.execute(
        f"SELECT DISTINCT tl.id, tl.base_url, tl.sites FROM tools tl JOIN credentials k ON k.tool_id = tl.id "
        f"WHERE {INTEGRATION} AND {SHARED_KEY} {one}",
        (connection_id,) if connection_id is not None else (),
    ).fetchall():
        host = jira_site(base_url, sites)
        if host:
            found[str(tool_id)] = host
    return found


def integration(conn: "sqlite3.Connection", connection_id: str) -> bool:
    """Whether a tool is one of `integrations` (that one tool read alone)."""
    return bool(integrations(conn, connection_id))


def due_reads(conn: "sqlite3.Connection", now: str, per_connection: int = PER_CONNECTION) -> list[tuple[str, str, str]]:
    """(key, connection, due mark) of each issue to read now, at most `per_connection` of each record integration
    (`integration`): one an open task links whose metadata is missing, never read, marked due or older than REREAD_S,
    unless a read of it failed within RETRY_S. Marked ones first, across connections, then the longest unread. The due
    mark ('' for none) is what the read began under."""
    H = _h()
    cutoff, retry = H.shift(now, seconds=-REREAD_S), H.shift(now, seconds=-RETRY_S)
    found = conn.execute(
        "SELECT key, connection_id, due FROM (SELECT MIN(l.item_key) AS key, l.connection_id, "
        "COALESCE(MAX(m.due_at), '') AS due, ROW_NUMBER() OVER (PARTITION BY l.connection_id "
        "ORDER BY MAX(m.due_at) IS NULL, MIN(COALESCE(m.read_at, '')), MIN(l.item_key)) AS n FROM task_links l "
        "JOIN tasks t ON t.id = l.task_id AND t.status = 'open' "
        "JOIN json_each(?) i ON i.value = l.connection_id "
        "LEFT JOIN item_metadata m ON m.source = l.source AND m.item_type = l.item_type AND m.item_key = l.item_key "
        "AND m.connection_id = l.connection_id "
        "WHERE l.source=? AND l.item_type=? AND l.connection_id <> '' "
        "AND (m.refused_at IS NULL OR m.refused_at < ?) "
        "AND (m.id IS NULL OR m.read_at IS NULL OR m.read_at < ? OR m.due_at IS NOT NULL) "
        "GROUP BY l.item_key, l.connection_id) WHERE n <= ? ORDER BY due = '', n, connection_id",
        (json.dumps(sorted(integrations(conn))), SOURCE, ISSUE, retry, cutoff, per_connection),
    ).fetchall()
    return [(str(key), str(connection_id), str(due)) for key, connection_id, due in found]


class Unreadable(Exception):
    """A read that cannot be made or that Jira refused; its words go in `read_error`, never a key."""


def connect(
    conn: "sqlite3.Connection", vault: Any, connection_id: str, *, write: bool = False, agent: str = "Tico-item-read"
) -> tuple[str, dict[str, str], dict[str, str]]:
    """(API address, headers, query) of a call through the connection's shared key (`integration`): for a read its read
    key, else its read and write key; for a write (`write`) only a read and write key. Unreadable when the connection is
    gone or no longer a record integration with such a key, has no https API address, or uses a key template. Runs in a
    write transaction: decrypting may reseal the key in place."""
    from .. import tool_services as _tool_services

    TS: Any = _tool_services
    tool = conn.execute("SELECT * FROM tools tl WHERE tl.id=?", (connection_id,)).fetchone()
    if tool is None:
        raise Unreadable("The connection is gone")
    if not integration(conn, connection_id):
        raise Unreadable(f"{tool['name']} is not a record integration on one Jira site with a shared company key")
    base = str(tool["base_url"] or "").rstrip("/")
    if not base.startswith("https://"):
        raise Unreadable(f"{tool['name']} has no https API address")
    if "auth_template" in tool.keys() and tool["auth_template"]:
        raise Unreadable(f"{tool['name']} uses a key template; Tico reads Jira with one key")
    credential = key_of(conn, connection_id, write=write)
    if credential is None:
        kind = "read and write key" if write else "key"
        raise Unreadable(f"{tool['name']} is not a record integration with a shared company {kind}")
    secret = str(vault.cipher.decrypt(conn, credential))
    service = cast("dict[str, Any]", TS.SERVICES.get(tool["service"]) or {})
    username = str(credential["username"] or service.get("username") or "")
    try:
        auth = cast("tuple[dict[str, str], dict[str, str]]", TS.apply_auth(tool["auth_scheme"], secret, username))
    except ValueError:
        raise Unreadable(f"{tool['name']} has an auth scheme Tico does not know") from None
    headers, params = auth
    return base, {**headers, "Accept": "application/json", "User-Agent": agent}, params


def key_of(conn: "sqlite3.Connection", connection_id: str, *, write: bool = False) -> Any:
    """The shared key `connect` uses on a record integration: for a read its read key first, else its read and write
    key; for a write (`write`) only a read and write key, the oldest. None when it has none."""
    return conn.execute(
        f"SELECT k.* FROM credentials k JOIN tools tl ON tl.id = k.tool_id WHERE tl.id=? AND {INTEGRATION} "
        f"AND {SHARED_KEY} {WRITE_KEY if write else ''} ORDER BY k.access_level <> 'read', k.created_at, k.id LIMIT 1",
        (connection_id,),
    ).fetchone()


def writable(conn: "sqlite3.Connection", connection_id: str) -> bool:
    """Whether a record integration (`integration`) holds a shared read and write key: what a write needs."""
    return integration(conn, connection_id) and key_of(conn, connection_id, write=True) is not None


def request(
    conn: "sqlite3.Connection", vault: Any, key: str, connection_id: str
) -> tuple[str, dict[str, str], dict[str, str]]:
    """(url, headers, query) of the read of one issue with the connection's shared key (`connect`)."""
    base, headers, params = connect(conn, vault, connection_id)
    return f"{base}/rest/api/3/issue/{quote(key)}", headers, {**params, "fields": FIELDS}


class Busy(Exception):
    """MAX_IN_FLIGHT reads are still out (one that timed out stays counted until its thread ends): no read starts."""


def busy() -> bool:
    with _in_flight_lock:
        return _in_flight[0] >= MAX_IN_FLIGHT


class Reply(NamedTuple):
    """Jira's answer to one call: its status, the JSON answer (None when it is not JSON or was cut off), the seconds its
    Retry-After asks for, and whether the answer was longer than MAX_BODY (`cut`: its body is then None)."""

    status: int
    body: object
    retry_after: float | None = None
    cut: bool = False


def send(url: str, headers: dict[str, str], params: dict[str, str], seconds: float = TIMEOUT_S) -> tuple[int, object]:
    """One GET (`call`): (status, the JSON answer or None)."""
    reply = call("GET", url, headers, params, seconds)
    return reply.status, reply.body


def call(
    method: str,
    url: str,
    headers: dict[str, str],
    params: dict[str, str],
    seconds: float = TIMEOUT_S,
    body: object = None,
    most: int | None = None,
) -> Reply:
    """One call (`_call`; a write sends `body` as JSON, and `most` bytes of the answer are read, MAX_BODY by default)
    with one deadline, `seconds` (at most TIMEOUT_S), over all of
    it: the name's lookup, connecting, TLS and the answer. It runs on a thread of its own that ends itself at the
    deadline (`_call` closes its client), and the caller stops waiting then too (`_wait`); the thread counts against
    MAX_IN_FLIGHT until it ends. Raises when the call failed or ran out of time, Busy (before anything is sent) when
    MAX_IN_FLIGHT calls are out."""
    wait = min(seconds, TIMEOUT_S)
    ends = time.monotonic() + wait
    answers: list[Reply] = []
    errors: list[BaseException] = []

    def run() -> None:
        try:
            answers.append(_call(method, url, headers, params, ends, body, most))
        except BaseException as error:  # noqa: BLE001 - handed to the caller, which raises it
            errors.append(error)
        finally:
            with _in_flight_lock:
                _in_flight[0] -= 1

    with _in_flight_lock:
        if _in_flight[0] >= MAX_IN_FLIGHT:
            raise Busy()
        _in_flight[0] += 1
    worker = threading.Thread(target=run, name=f"jira-{method.lower()}", daemon=True)
    worker.start()
    _wait(worker, wait)
    if answers:
        return answers[0]
    if errors:
        raise errors[0]
    raise TimeoutError(f"Jira took longer than {wait:g} seconds to answer")


def _wait(worker: threading.Thread, seconds: float) -> None:
    """Wait for a call's thread at most `seconds` (tests stand in for the clock here)."""
    worker.join(seconds)


def _retry_after(value: str | None) -> float | None:
    """Retry-After in seconds (a number of seconds or an HTTP date), None when absent or unreadable."""
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        from email.utils import parsedate_to_datetime

        return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
    except (TypeError, ValueError):
        return None


def _call(
    method: str,
    url: str,
    headers: dict[str, str],
    params: dict[str, str],
    ends: float,
    body: object = None,
    most: int | None = None,
) -> Reply:
    """One call to a public https address, pinned to the IP it resolved to, no redirects, no proxy from the environment,
    at most `most` bytes (MAX_BODY) read, all of it before `ends` (time.monotonic): as egress.post bounds its calls, a timer closes the
    client at the deadline, which ends a read blocked on a socket, and the answer stops being read past it."""
    import httpx

    from .. import egress as _egress

    egress: Any = _egress
    late = TimeoutError("Jira took too long to answer")
    limit = MAX_BODY if most is None else most
    parts = egress.check(url)
    address = egress.address_for(parts, budget=max(ends - time.monotonic(), 0.001))
    pinned = httpx.URL(url, params=params or None).copy_with(host=address)
    host = str(parts.hostname) + (f":{parts.port}" if parts.port else "")
    sent = {**headers, "Host": host}
    content = None
    if body is not None:
        content = json.dumps(body).encode()
        sent["Content-Type"] = "application/json"
    with httpx.Client(timeout=TIMEOUT_S, follow_redirects=False, trust_env=False, transport=TRANSPORT) as client:
        timer = threading.Timer(max(ends - time.monotonic(), 0.001), client.close)
        timer.daemon = True
        timer.start()
        try:
            with client.stream(
                method, pinned, headers=sent, content=content, extensions={"sni_hostname": parts.hostname}
            ) as response:
                got = b""
                for chunk in response.iter_bytes():
                    got += chunk
                    if len(got) > limit or time.monotonic() > ends:
                        break
                if time.monotonic() > ends:
                    raise late
                cut = len(got) > limit
                try:
                    answer: object = None if cut else json.loads(got)
                except ValueError:
                    answer = None
                return Reply(response.status_code, answer, _retry_after(response.headers.get("retry-after")), cut)
        except Exception as error:
            if time.monotonic() > ends and error is not late:
                raise late from error
            raise
        finally:
            timer.cancel()


def fields_of(answer: object, connection_id: str) -> Row | None:
    """The metadata fields of Jira's answer to an issue read, or None when it is not one: `fields.status` an object with
    a name and `statusCategory.key` one of Jira's three, and `fields.updated` text."""
    got = cast("dict[str, object]", answer).get("fields") if isinstance(answer, dict) else None
    status = cast("dict[str, object]", got).get("status") if isinstance(got, dict) else None
    category = cast("dict[str, object]", status).get("statusCategory") if isinstance(status, dict) else None
    key = cast("dict[str, object]", category).get("key") if isinstance(category, dict) else None
    name = cast("dict[str, object]", status).get("name") if isinstance(status, dict) else None
    updated = cast("dict[str, object]", got).get("updated") if isinstance(got, dict) else None
    if key not in CATEGORIES or not isinstance(name, str) or not isinstance(updated, str):
        return None
    return {
        "state": "closed" if key == "done" else "open",
        "status": name,
        "category": key,
        "updated": updated,
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
    if not IM.save(conn, SOURCE, ISSUE, key, fields, version, connection_id=connection_id):
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
    from .. import release_closeout as RC
    from .github import version_now

    H = _h()
    at: str = now or H.now()
    call = sender or send
    with store.read() as conn:
        due = due_reads(conn, at)
    changed, started = 0, clock()
    for key, connection_id, due_seen in due:
        left = PASS_BUDGET_S - (clock() - started)
        if left <= 0 or busy():  # out of time, or reads still out: the rest wait for the next pass
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
        left = PASS_BUDGET_S - (clock() - started)  # again, just before it is sent: preparing it took time
        if left <= 0:
            break
        try:
            status, answer = call(url, headers, params, left)
        except Busy:
            break
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
                if status == 404:
                    RC.watch(conn, key, connection_id)
            elif save_issue(conn, key, connection_id, fields, version, due_seen):
                changed += 1
                RC.watch(conn, key, connection_id)
    return changed
