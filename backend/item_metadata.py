# pyright: strict
"""Item metadata: Tico's saved copy of what a source said of one item (docs/pipelines-v3.md "Sources and items").

Requirements read saved metadata only; nothing here calls a source. One `item_metadata` row per item (source, item
type, item key). A task reaches its items through its `task_links` rows that name one (`source`, `item_type`,
`item_key`). The source's module (backend/item_sources) says what `fields` hold and reads them.

- Newest wins. `metadata_version` is when the read or webhook that saw the metadata began, in ms. A save with an older
  version than the row's changes nothing, so a slow read can't undo a webhook that came after it began. Two saves at
  the same version that say different things cannot be ordered (two webhooks GitHub stamped in the same second may
  arrive either way round; two server processes may read in the same ms, though one process never repeats a version):
  the second is saved and the item is marked due in the same write, so the next pass reads the source and the saved
  metadata ends at what it says.
- `read_at` is the last word from the source. `due_at` asks for a full read on the next pass whatever the item's age
  (`mark_due`); a save at a newer version clears it, one at the same version keeps it. `final_at`: the item can never
  change again (a merged PR), so it is not read again. An item that can still change (a closed PR can be reopened) is
  never final.

Every function takes the caller's open write transaction and never commits, except `refresh`.
"""

import json
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import sqlite3

Row = dict[str, Any]
LOG = logging.getLogger("tico.item_metadata")
REREAD_S = 300  # an item an open task waits on is read again when its metadata is older than this
REREAD_BATCH = 50  # at most this many items per pass and source


def _h() -> Any:
    from . import hubdb

    return hubdb


def _rows(conn: "sqlite3.Connection", sql: str, args: tuple[Any, ...]) -> list[Row]:
    cursor = conn.execute(sql, args)
    names = [d[0] for d in cursor.description]
    return [dict(zip(names, found, strict=True)) for found in cursor.fetchall()]


def _loaded(row: Row) -> Row:
    try:
        row["fields"] = json.loads(row.get("fields") or "{}") or {}
    except ValueError:
        row["fields"] = {}
    return row


def get(conn: "sqlite3.Connection", source: str, item_type: str, key: str) -> Row | None:
    """One item's saved metadata (an `item_metadata` row with `fields` loaded), or None when Tico has none yet."""
    found = _rows(
        conn,
        "SELECT * FROM item_metadata WHERE source=? AND item_type=? AND item_key=?",
        (source, item_type, key),
    )
    return _loaded(found[0]) if found else None


def save(
    conn: "sqlite3.Connection",
    source: str,
    item_type: str,
    key: str,
    fields: Row,
    version: int,
    *,
    final: bool = False,
    title: str | None = None,
) -> bool:
    """Save what the source said of an item, as seen by a read or webhook that began at `version`. `fields` replaces the
    saved fields (the source merges first). False, and nothing written, when the saved metadata is newer. Saved at the
    same version as different fields, an item that is not final is also marked due: which of the two is newer is
    unknown, so the next pass reads the source (`due_reads`)."""
    H = _h()
    now = H.now()
    old = get(conn, source, item_type, key)
    if old and int(old["metadata_version"]) > version:
        return False
    text = json.dumps(fields)
    same = not final and bool(old) and int(old["metadata_version"]) == version
    tied = same and old is not None and old["fields"] != json.loads(text)
    if tied:
        LOG.info("equal_version_conflict %s %s %s at %s: read again on the next pass", source, item_type, key, version)
    values = (title, text, version, now, (old or {}).get("final_at") or now if final else None, now)
    if old:
        conn.execute(
            "UPDATE item_metadata SET title=COALESCE(?, title), fields=?, metadata_version=?, read_at=?, final_at=?, "
            "updated_at=?, due_at=? WHERE id=?",
            # A later save at the same version that agrees with the tied one settles nothing: the due mark stays.
            (*values, now if tied else old["due_at"] if same else None, old["id"]),
        )
    else:
        conn.execute(
            "INSERT INTO item_metadata (title, fields, metadata_version, read_at, final_at, updated_at, id, source, "
            "item_type, item_key, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (*values, H.new_id(), source, item_type, key, now),
        )
    return True


def mark_due(conn: "sqlite3.Connection", source: str, item_type: str, key: str) -> None:
    """Ask for a full read of an item on the next pass, whatever its age (it is not final)."""
    conn.execute(
        "UPDATE item_metadata SET due_at=? WHERE source=? AND item_type=? AND item_key=? AND final_at IS NULL",
        (_h().now(), source, item_type, key),
    )


def linked(conn: "sqlite3.Connection", source: str, item_type: str, key: str) -> bool:
    """Whether any task links this item."""
    return (
        conn.execute(
            "SELECT 1 FROM task_links WHERE source=? AND item_type=? AND item_key=? LIMIT 1", (source, item_type, key)
        ).fetchone()
        is not None
    )


def of_task(conn: "sqlite3.Connection", task_id: str, source: str, item_type: str) -> list[Row]:
    """The items of one type linked to a task, oldest link first: its `task_links` rows, each with `metadata` (`get`)
    or None."""
    links = _rows(
        conn,
        "SELECT * FROM task_links WHERE task_id=? AND source=? AND item_type=? ORDER BY created_at, id",
        (task_id, source, item_type),
    )
    return [{**link, "metadata": get(conn, source, item_type, str(link["item_key"]))} for link in links]


def tasks_of(conn: "sqlite3.Connection", source: str, item_type: str, key: str) -> list[str]:
    """The open tasks on the engine that link this item, oldest link first."""
    return [
        str(found[0])
        for found in conn.execute(
            "SELECT l.task_id FROM task_links l JOIN tasks t ON t.id = l.task_id WHERE l.source=? AND l.item_type=? "
            "AND l.item_key=? AND t.status='open' AND t.pipeline_id IS NOT NULL "
            "GROUP BY l.task_id ORDER BY MIN(l.created_at), l.task_id",
            (source, item_type, key),
        ).fetchall()
    ]


def nudge(conn: "sqlite3.Connection", task_ids: list[str]) -> None:
    """Metadata came in: move each task on if it now has all it needs (`task_engine.advance`). Each in a savepoint, so
    one that cannot move leaves the others and the saved metadata as they are; the never-stuck check tries it again."""
    from . import task_engine

    for task_id in task_ids:
        conn.execute("SAVEPOINT nudge")
        try:
            task_engine.advance(conn, task_id)
        except Exception as exc:  # noqa: BLE001 - the metadata stands; the next never-stuck pass moves the task
            conn.execute("ROLLBACK TO nudge")
            LOG.warning("Moving %s on after new item metadata failed: %s", task_id, exc)
        conn.execute("RELEASE nudge")


# The open tasks on the engine, with their open visit, that wait on a source: their stage acts on it when entered, or a
# requirement of the stage or visit is an item of it, or a page is for the newest version of the linked items.
_WAITING_TASKS = (
    "SELECT t.id FROM tasks t JOIN visits v ON v.task_id = t.id AND v.left_at IS NULL "
    "JOIN stages s ON s.id = t.stage_id WHERE t.status='open' AND t.pipeline_id IS NOT NULL AND ("
    "COALESCE(json_extract(CASE WHEN json_valid(s.on_enter) THEN s.on_enter END, '$.act'), '') LIKE ? || '.%' "
    "OR EXISTS (SELECT 1 FROM requirements r WHERE (r.stage_id = t.stage_id OR r.visit_id = v.id) "
    "AND r.withdrawn_at IS NULL AND (r.kind = 'item' AND r.source = ? "
    "OR r.kind = 'page' AND COALESCE(json_extract(CASE WHEN json_valid(r.settings) THEN r.settings END, "
    "'$.at_head'), 0))))"
)


def due_reads(
    conn: "sqlite3.Connection", source: str, item_type: str, now: str, limit: int = REREAD_BATCH
) -> list[str]:
    """The keys of the items an open task waits on whose metadata is missing or older than REREAD_S, oldest read first,
    at most `limit`. A final item is not read again. An item marked due (`mark_due`) is read once more whatever links
    it: a task closed while the source changed the item anyway so leaves no metadata stale for ever."""
    cutoff = _h().shift(now, seconds=-REREAD_S)
    found = conn.execute(
        "SELECT MIN(l.item_key), MIN(CASE WHEN m.due_at IS NOT NULL THEN '' ELSE COALESCE(m.read_at, '') END) AS seen "
        f"FROM task_links l LEFT JOIN ({_WAITING_TASKS}) w ON w.id = l.task_id "
        "LEFT JOIN item_metadata m ON m.source = l.source AND m.item_type = l.item_type AND m.item_key = l.item_key "
        "WHERE l.source=? AND l.item_type=? AND l.item_key IS NOT NULL AND l.item_key <> '' "
        "AND (w.id IS NOT NULL AND (m.id IS NULL OR m.final_at IS NULL AND (m.read_at < ? OR m.due_at IS NOT NULL)) "
        "OR m.final_at IS NULL AND m.due_at IS NOT NULL) "
        "GROUP BY lower(l.item_key) ORDER BY seen, lower(l.item_key) LIMIT ?",
        (source, source, source, item_type, cutoff, limit),
    ).fetchall()
    return [str(key) for key, _ in found]
