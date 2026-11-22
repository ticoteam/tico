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
from . import jira as JS
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


def integrations(conn: sqlite3.Connection, connection_id: str | None = None) -> set[str]:
    """Close's API uses one host for every account. Each qualifying tool is a separate connection."""
    one = "AND tl.id=?" if connection_id is not None else ""
    found: set[str] = set()
    for tool_id, base_url, sites in conn.execute(
        f"SELECT tl.id, tl.base_url, tl.sites FROM tools tl WHERE {RECORD_INTEGRATION} "
        "AND tl.auth_scheme='basic_user' AND COALESCE(tl.auth_template, '')='' "
        f"AND EXISTS (SELECT 1 FROM credentials k WHERE k.tool_id=tl.id AND {SHARED_KEY}) {one}",
        (connection_id,) if connection_id is not None else (),
    ):
        parts = urlsplit(str(base_url or ""))
        if (
            parts.scheme == "https"
            and parts.netloc.lower() in (API_HOST, f"{API_HOST}:443")
            and parts.path.rstrip("/") in ("", "/api/v1")
            and not (parts.query or parts.fragment)
            and hosts(base_url, sites) == {API_HOST}
        ):
            found.add(str(tool_id))
    return found


class Reading(NamedTuple):
    item: tuple[str, str, str, str]
    version: int
    fields: dict[str, Any] | None
    error: str = ""


def _read(store: Any, vault: Any, key: str, connection_id: str, deadline: float) -> Reading | None:
    """A bounded GET with no write lock held over the network. None when the connection or read slot is gone."""
    item = (SOURCE, OPPORTUNITY, key, connection_id)
    version = version_now()
    try:
        with store.transaction() as conn:
            if not integrations(conn, connection_id):
                return None
            credential = JS.key_of(conn, connection_id)  # shared Read first, then shared Read and write
            if credential is None:
                return None
            key_value = str(vault.cipher.decrypt(conn, credential))
            services: Any = TS
            auth, _ = cast("tuple[dict[str, str], dict[str, str]]", services.apply_auth("basic_user", key_value))
    except Exception:  # noqa: BLE001 - a vault error must never expose the credential
        return Reading(item, version, None, "The connection's key could not be read")
    left = deadline - time.monotonic()
    if left <= 0:
        return None
    try:
        status, answer = JS.send(url_of(key), {**auth, "Accept": "application/json"}, {"_fields": FIELDS}, left)
    except JS.Busy:
        return None
    except Exception:  # noqa: BLE001 - a transport error can contain request details
        return Reading(item, version, None, "Close could not be reached")
    if not 200 <= status < 300:
        return Reading(item, version, None, f"Close answered {status}")
    got = cast("dict[str, object]", answer) if isinstance(answer, dict) else {}
    status_type, lead, value = got.get("status_type"), got.get("lead_id"), got.get("value")
    if (
        got.get("id") != key
        or status_type not in ("active", "won", "lost")
        or not isinstance(lead, str)
        or not LEAD.fullmatch(lead)
        or (value is not None and type(value) is not int)
    ):
        return Reading(item, version, None, "Close's answer has no valid opportunity")
    label, currency, period = got.get("status_label"), got.get("value_currency"), got.get("value_period")
    return Reading(
        item,
        version,
        {
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


def prefetch(store: Any, vault: Any, item: tuple[str, str, str, str]) -> Reading | None:
    """An initial lead lookup when this connection has recorded leads and no successful poll has supplied one."""
    connection_id = item[3]
    with store.read() as conn:
        if primary_task(conn, item) or (IM.get(conn, *item) or {}).get("read_at"):
            return None
        if not conn.execute(
            "SELECT 1 FROM entity_external_refs WHERE system=? AND connection_id=? "
            "AND external_type='lead' AND status='active' LIMIT 1",
            (SOURCE, connection_id),
        ).fetchone():
            return None
    reading = _read(store, vault, item[2], connection_id, time.monotonic() + JS.TIMEOUT_S)
    return reading if reading and reading.fields else None


def entity(
    conn: sqlite3.Connection, reader: str, item: tuple[str, str, str, str], reading: Reading | None
) -> str | None:
    from ..entities import store as ES

    saved = IM.get(conn, *item) or {}
    fields: dict[str, Any] = saved.get("fields") or {}
    if reading and reading.item == item and reading.fields and reading.version > saved.get("metadata_version", 0):
        fields = reading.fields
    return ES.verified_external_ref(conn, reader, SOURCE, item[3], "lead", str(fields.get("lead_id") or ""))


def refresh(store: Any, vault: Any, now: str | None = None) -> int:
    """Read due opportunities of open tasks, with Jira's cadence and bounded pass."""
    from .. import release_closeout
    from .. import hubdb

    H: Any = hubdb

    at: str = now or H.now()
    cutoff, retry = H.shift(at, seconds=-JS.REREAD_S), H.shift(at, seconds=-JS.RETRY_S)
    with store.read() as conn:
        due = conn.execute(
            "SELECT item_key, connection_id, due FROM (SELECT l.item_key, l.connection_id, "
            "COALESCE(m.due_at, '') AS due, ROW_NUMBER() OVER (PARTITION BY l.connection_id "
            "ORDER BY m.due_at IS NULL, COALESCE(m.read_at, ''), l.item_key) AS n FROM task_links l "
            "JOIN tasks t ON t.id=l.task_id AND t.status='open' "
            "JOIN json_each(?) i ON i.value=l.connection_id "
            "LEFT JOIN item_metadata m ON m.source=l.source AND m.item_type=l.item_type "
            "AND m.item_key=l.item_key AND m.connection_id=l.connection_id "
            "WHERE l.source=? AND l.item_type=? AND (m.refused_at IS NULL OR m.refused_at < ?) "
            "AND (m.read_at IS NULL OR m.read_at < ? OR m.due_at IS NOT NULL) "
            "GROUP BY l.item_key, l.connection_id) WHERE n <= ? ORDER BY due='', n, connection_id",
            (json.dumps(sorted(integrations(conn))), SOURCE, OPPORTUNITY, retry, cutoff, JS.PER_CONNECTION),
        ).fetchall()
    deadline = time.monotonic() + JS.PASS_BUDGET_S
    changed = 0
    for key, connection_id, due_seen in due:
        if time.monotonic() >= deadline or JS.busy():
            break
        read = _read(store, vault, str(key), str(connection_id), deadline)
        if read is None:
            continue
        with store.transaction(notify=True) as conn:
            if not integrations(conn, str(connection_id)):
                continue
            if read.fields is None:
                IM.failed_read(conn, read.item, read.error)
                LOG.info("Reading Close opportunity %s on %s failed: %s", key, connection_id, read.error)
            elif IM.save_read(conn, read.item, read.fields, read.version, str(due_seen)):
                changed += 1
                release_closeout.watch_close(conn, read.item, str(read.fields["status_type"]))
    return changed
