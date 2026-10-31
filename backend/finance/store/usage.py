"""SQL for Gateway usage and Finance events: the meter snapshots the worker fetches, what usage postings have put on
the books per (month, line), and draining `fin_events`."""
import json
import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any

from .ledger import new_id


# ------------------------------------------------------------------------------------------------ meter snapshots
def add_snapshot(c: sqlite3.Connection, *, month: str, as_of: str, ai_nano: int, video_nano: int,
                 rows: Sequence[Mapping[str, object]], by: str, at: str) -> str | None:
    """Keep one fetch of the meter. None when that (month, as_of) is already kept: the meter has not moved."""
    sid = new_id()
    made = c.execute("INSERT INTO fin_usage_snapshots(id, month, as_of, ai_nano, video_nano, rows_json, created_at, "
                     "created_by) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(month, as_of) DO NOTHING",
                     (sid, month, as_of, ai_nano, video_nano, json.dumps([dict(r) for r in rows], sort_keys=True),
                      at, by)).rowcount
    return sid if made else None


def unposted(c: sqlite3.Connection, most: int = 24) -> list[Any]:
    """The newest unposted snapshot of each month (a newer one carries everything an older one did)."""
    return c.execute("SELECT s.* FROM fin_usage_snapshots s WHERE s.posted_at IS NULL AND s.as_of = (SELECT max(as_of) "
                     "FROM fin_usage_snapshots t WHERE t.month = s.month) ORDER BY s.month LIMIT ?", (most,)).fetchall()


def mark_posted(c: sqlite3.Connection, month: str, through: str, at: str) -> None:
    """Every snapshot of `month` up to `through` is done: the newest one's posting covered them."""
    c.execute("UPDATE fin_usage_snapshots SET posted_at=? WHERE month=? AND as_of<=? AND posted_at IS NULL",
              (at, month, through))


def last_posted(c: sqlite3.Connection, month: str) -> Any:
    return c.execute("SELECT * FROM fin_usage_snapshots WHERE month=? AND posted_at IS NOT NULL ORDER BY as_of DESC "
                     "LIMIT 1", (month,)).fetchone()


def latest(c: sqlite3.Connection, month: str) -> Any:
    return c.execute("SELECT * FROM fin_usage_snapshots WHERE month=? ORDER BY as_of DESC LIMIT 1", (month,)).fetchone()


def posted_on(c: sqlite3.Connection, book_id: str, account_id: str, prefix: str) -> int:
    """The net amount usage entries whose source id starts `prefix` put on one account, reversals included."""
    row = c.execute("SELECT coalesce(sum(l.amount), 0) FROM fin_lines l JOIN fin_entries e ON e.id = l.entry_id "
                    "WHERE e.book_id=? AND e.source='usage' AND substr(e.source_id, 1, ?) = ? AND l.account_id=?",
                    (book_id, len(prefix), prefix, account_id)).fetchone()
    return int(row[0])


def run_holds_settled(c: sqlite3.Connection, through: str, most: int = 2000) -> list[str]:
    """Run holds settled at or before `through` that no entry has taken in yet."""
    return [str(r[0]) for r in c.execute("SELECT id FROM fin_holds WHERE source='run' AND status='settled' "
                                         "AND entry_id IS NULL AND settled_at<=? ORDER BY settled_at LIMIT ?",
                                         (through, most))]


# ------------------------------------------------------------------------------------------------ events
def undrained(c: sqlite3.Connection, most: int) -> list[Any]:
    return c.execute("SELECT id, name, subject, period_key FROM fin_events WHERE drained_at IS NULL "
                     "ORDER BY created_at, id LIMIT ?", (most,)).fetchall()


def drained(c: sqlite3.Connection, event_id: str, at: str) -> None:
    c.execute("UPDATE fin_events SET drained_at=? WHERE id=? AND drained_at IS NULL", (at, event_id))
