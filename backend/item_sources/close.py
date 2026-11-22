# pyright: strict
"""Close opportunities linked from open tasks, read through their own record integration."""

import json
import logging
import re
import sqlite3
import time
from typing import Any, NamedTuple, cast
from urllib.parse import urlsplit

from .. import item_metadata as IM
from .. import tool_services as TS
from ..tool_services import RECORD_INTEGRATION, SHARED_KEY
from . import Condition, Item, hosts, primary_task
from . import http
from .github import version_now

SOURCE, OPPORTUNITY = "close", "opportunity"
NAME = "Close"
ITEMS = {OPPORTUNITY: Item(OPPORTUNITY, "Opportunity")}
CONDITIONS: dict[str, Condition] = {}
API_HOST = "api.close.com"
KEY = r"oppo_[A-Za-z0-9]{1,80}"
LEAD = re.compile(r"lead_[A-Za-z0-9]{1,80}")
URL = re.compile(
    r"(?i:https://(?:api\.close\.com(?::443)?/api/v1|app\.close\.com(?::443)?))/opportunity/"
    rf"({KEY})/?(?:[?#]\S*)?"
)
FIELDS = "id,status_type,status_label,value,value_currency,value_period,lead_id"
REREAD_S = 300
RETRY_S = 300
MAX_RETRY_S = 3600
PER_CONNECTION = 10
PASS_BUDGET_S = 30.0
RATE_LIMITED = "Close answered 429"
HTTP = http.Pool(NAME)
TRANSPORT: Any = None  # tests: an httpx.MockTransport at the network edge
LOG = logging.getLogger("tico.close_source")


def match_key(item_type: str, text: str) -> str | None:
    return text if item_type == OPPORTUNITY and re.fullmatch(KEY, text) else None


def match_link(url: str) -> tuple[str, str] | None:
    found = URL.fullmatch(url.strip())
    return (OPPORTUNITY, found[1]) if found else None


def site(url: str) -> str | None:
    return API_HOST if match_link(url) else None


def url_of(key: str) -> str:
    return f"https://{API_HOST}/api/v1/opportunity/{key}/"


def _credentials(conn: sqlite3.Connection, connection_id: str | None = None) -> dict[str, sqlite3.Row]:
    """The selected shared Read key on each eligible Close tool."""
    one = "AND tl.id=?" if connection_id is not None else ""
    found: dict[str, sqlite3.Row] = {}
    for row in conn.execute(
        "SELECT k.*, tl.base_url, tl.sites FROM tools tl JOIN credentials k ON k.id = ("
        f"SELECT k.id FROM credentials k WHERE k.tool_id=tl.id AND {SHARED_KEY} AND k.access_level='read' "
        "ORDER BY k.created_at, k.id LIMIT 1) "
        f"WHERE {RECORD_INTEGRATION} AND tl.auth_scheme='basic_user' AND COALESCE(tl.auth_template, '')='' {one}",
        (connection_id,) if connection_id is not None else (),
    ):
        base_url, sites = row["base_url"], row["sites"]
        parts = urlsplit(str(base_url or ""))
        if (
            parts.scheme == "https"
            and parts.netloc.lower() in (API_HOST, f"{API_HOST}:443")
            and parts.path.rstrip("/") in ("", "/api/v1")
            and not (parts.query or parts.fragment)
            and hosts(base_url, sites) == {API_HOST}
        ):
            found[str(row["tool_id"])] = row
    return found


def integrations(conn: sqlite3.Connection, connection_id: str | None = None) -> set[str]:
    """One connection per eligible Close tool, even though they all use the same API host."""
    return set(_credentials(conn, connection_id))


class Reading(NamedTuple):
    item: tuple[str, str, str, str]
    version: int
    credential: tuple[str, int]
    fields: dict[str, Any] | None = None
    error: str = ""
    status: int | None = None
    retry_after: float | None = None
    due_seen: str = ""


def _revision(credential: sqlite3.Row) -> tuple[str, int]:
    return str(credential["id"]), int(credential["revision"])


def current(conn: sqlite3.Connection, reading: Reading) -> bool:
    """A response belongs to the key that was selected when the request went out."""
    connection_id = reading.item[3]
    selected = _credentials(conn, connection_id).get(connection_id)
    return selected is not None and _revision(selected) == reading.credential


def _read(
    store: Any,
    vault: Any,
    item: tuple[str, str, str, str],
    deadline: float,
    retry: str,
    due_seen: str,
) -> Reading | None:
    """A bounded GET with no write lock held over the network."""
    _, _, key, connection_id = item
    with store.transaction() as conn:
        if conn.execute(
            "SELECT 1 FROM item_metadata WHERE source=? AND item_type=? AND connection_id=? "
            "AND refused_at >= ? AND (item_key=? OR read_error=?) LIMIT 1",
            (SOURCE, OPPORTUNITY, connection_id, retry, key, RATE_LIMITED),
        ).fetchone():
            return None
        credential = _credentials(conn, connection_id).get(connection_id)
        if credential is None:
            return None
        read = Reading(item, version_now(), _revision(credential), due_seen=due_seen)
        try:
            key_value = str(vault.cipher.decrypt(conn, credential))
            services: Any = TS
            auth, _ = cast("tuple[dict[str, str], dict[str, str]]", services.apply_auth("basic_user", key_value))
        except Exception:  # noqa: BLE001 - a vault error must never expose the credential
            return read._replace(error="The connection's key could not be read")
    left = deadline - time.monotonic()
    if left <= 0:
        return None
    try:
        reply = HTTP.call(
            "GET",
            left,
            lambda ends: http.request(
                NAME,
                "GET",
                url_of(key),
                {**auth, "Accept": "application/json"},
                {"_fields": FIELDS},
                ends,
                transport=TRANSPORT,
            ),
        )
    except http.Busy:
        return None
    except Exception as exc:  # noqa: BLE001 - a transport error can contain request details
        timeout = " (timed out)" if "Timeout" in type(exc).__name__ else ""
        return read._replace(error="Close could not be reached" + timeout)
    read = read._replace(status=reply.status, retry_after=reply.retry_after)
    if not 200 <= reply.status < 300:
        return read._replace(error=f"Close answered {reply.status}")
    got = cast("dict[str, object]", reply.body) if isinstance(reply.body, dict) else {}
    status_type, lead, value = got.get("status_type"), got.get("lead_id"), got.get("value")
    if (
        got.get("id") != key
        or status_type not in ("active", "won", "lost")
        or not isinstance(lead, str)
        or not LEAD.fullmatch(lead)
        or (value is not None and type(value) is not int)
    ):
        return read._replace(error="Close's answer has no valid opportunity")
    label, currency, period = got.get("status_label"), got.get("value_currency"), got.get("value_period")
    return read._replace(
        fields={
            "state": "open" if status_type == "active" else "closed",
            "status_type": status_type,
            "status": label if isinstance(label, str) else status_type,
            "value": value,
            "value_currency": currency if isinstance(currency, str) else None,
            "value_period": period if period in ("one_time", "monthly", "annual") else None,
            "lead_id": lead,
            "connection_id": connection_id,
        },
    )


def _fresh(saved: IM.Row, cutoff: str) -> bool:
    fields: IM.Row = saved.get("fields") or {}
    return (
        str(saved.get("read_at") or "") >= cutoff
        and not saved.get("read_error")
        and not saved.get("gone_at")
        and fields.get("status_type") in ("active", "won", "lost")
    )


def prefetch(store: Any, vault: Any, item: tuple[str, str, str, str]) -> Reading | None:
    """Look up a lead only when a create could use it and there is no fresh successful read."""
    from .. import hubdb

    H: Any = hubdb
    at: str = H.now()
    with store.read() as conn:
        saved = IM.get(conn, *item) or {}
        if primary_task(conn, item) or _fresh(saved, H.shift(at, seconds=-REREAD_S)):
            return None
        if not conn.execute(
            "SELECT 1 FROM entity_external_refs WHERE system=? AND connection_id=? "
            "AND external_type='lead' AND status='active' LIMIT 1",
            (SOURCE, item[3]),
        ).fetchone():
            return None
    read = _read(
        store,
        vault,
        item,
        time.monotonic() + http.TIMEOUT_S,
        H.shift(at, seconds=-RETRY_S),
        str(saved.get("due_at") or ""),
    )
    if read and read.status == 429:
        # The connection must back off even if the task is never filed.
        with store.transaction(notify=True) as conn:
            save(conn, read)
        return None
    return read


def entity(
    conn: sqlite3.Connection, reader: str, item: tuple[str, str, str, str], reading: Reading | None
) -> str | None:
    from .. import hubdb
    from ..entities import store as ES

    H: Any = hubdb
    saved = IM.get(conn, *item) or {}
    if reading and reading.item == item and reading.version > saved.get("metadata_version", 0):
        fields = reading.fields or {}
    elif _fresh(saved, H.shift(H.now(), seconds=-REREAD_S)):
        fields = saved["fields"]
    else:
        return None
    return ES.verified_external_ref(
        conn, reader, SOURCE, item[3], "lead", str(fields.get("lead_id") or ""), company_only=True
    )


def save(conn: sqlite3.Connection, read: Reading) -> bool | None:
    """Save a current response and update the primary task's ask. True for success, False for a saved refusal,
    or None when the response is stale."""
    from .. import hubdb
    from .. import release_closeout

    H: Any = hubdb
    if not current(conn, read):
        return None
    if read.fields is not None:
        if IM.save_read(conn, read.item, read.fields, read.version, read.due_seen):
            release_closeout.watch_close(conn, read.item, str(read.fields["status_type"]))
            return True
        return None
    refused_at = None
    if read.status == 429 and read.retry_after is not None and read.retry_after > RETRY_S:
        # The normal retry query already adds RETRY_S to the refusal timestamp.
        refused_at = cast(str, H.shift(H.now(), seconds=min(read.retry_after, MAX_RETRY_S) - RETRY_S))
    if not IM.failed_read(conn, read.item, read.error, read.version, refused_at=refused_at):
        return None
    LOG.info("Reading Close opportunity %s on %s failed: %s", read.item[2], read.item[3], read.error)
    if read.status == 404:
        release_closeout.watch_close(conn, read.item, "gone")
    return False


def refresh(store: Any, vault: Any, now: str | None = None) -> int:
    """Read due opportunities of open tasks, with a bounded pass."""
    from .. import hubdb

    H: Any = hubdb
    at: str = now or H.now()
    cutoff, retry = H.shift(at, seconds=-REREAD_S), H.shift(at, seconds=-RETRY_S)
    with store.read() as conn:
        tools = integrations(conn)
        if not tools:
            return 0
        cooled = conn.execute(
            "SELECT DISTINCT connection_id FROM item_metadata WHERE source=? AND item_type=? "
            "AND read_error=? AND refused_at >= ?",
            (SOURCE, OPPORTUNITY, RATE_LIMITED, retry),
        )
        tools.difference_update(str(row[0]) for row in cooled)
        if not tools:
            return 0
        due = conn.execute(
            "SELECT item_key, connection_id, due FROM (SELECT l.item_key, l.connection_id, "
            "COALESCE(m.due_at, '') AS due, ROW_NUMBER() OVER (PARTITION BY l.connection_id "
            "ORDER BY m.due_at IS NULL, MAX(COALESCE(m.read_at, ''), COALESCE(m.refused_at, '')), l.item_key) AS n "
            "FROM task_links l JOIN tasks t ON t.id=l.task_id AND t.status='open' "
            "JOIN json_each(?) i ON i.value=l.connection_id "
            "LEFT JOIN item_metadata m ON m.source=l.source AND m.item_type=l.item_type "
            "AND m.item_key=l.item_key AND m.connection_id=l.connection_id "
            "WHERE l.source=? AND l.item_type=? AND (m.refused_at IS NULL OR m.refused_at < ?) "
            "AND (m.read_at IS NULL OR m.read_at < ? OR m.due_at IS NOT NULL) "
            "GROUP BY l.item_key, l.connection_id) WHERE n <= ? ORDER BY due='', n, connection_id",
            (json.dumps(sorted(tools)), SOURCE, OPPORTUNITY, retry, cutoff, PER_CONNECTION),
        ).fetchall()
    deadline = time.monotonic() + PASS_BUDGET_S
    changed = 0
    rate_limited: set[str] = set()
    for key, connection_id, due_seen in due:
        if time.monotonic() >= deadline or HTTP.busy():
            break
        if connection_id in rate_limited:
            continue
        read = _read(store, vault, (SOURCE, OPPORTUNITY, key, connection_id), deadline, retry, due_seen)
        if read is None:
            continue
        with store.transaction(notify=True) as conn:
            saved = save(conn, read)
        if saved is not None:
            changed += int(saved)
            if read.status == 429:
                rate_limited.add(connection_id)
    return changed
