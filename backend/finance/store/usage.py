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
    """Keep one fetch of the meter. A fetch with the same (month, as_of) and a higher total replaces the kept row under
    a new id (so its posting is a new source id) and makes it unposted again. None when nothing changed."""
    sid = new_id()
    made = c.execute("INSERT INTO fin_usage_snapshots(id, month, as_of, ai_nano, video_nano, rows, created_at, "
                     "created_by) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(month, as_of) DO UPDATE SET id=excluded.id, "
                     "ai_nano=excluded.ai_nano, video_nano=excluded.video_nano, rows=excluded.rows, "
                     "created_at=excluded.created_at, created_by=excluded.created_by, posted_at=NULL, ignored_at=NULL "
                     "WHERE excluded.ai_nano + excluded.video_nano > "
                     "fin_usage_snapshots.ai_nano + fin_usage_snapshots.video_nano",
                     (sid, month, as_of, ai_nano, video_nano, json.dumps([dict(r) for r in rows], sort_keys=True),
                      at, by)).rowcount
    return sid if made else None


def unposted(c: sqlite3.Connection, most: int = 24) -> list[Any]:
    """Per month, the unposted snapshot fetched last (by created_at): it carries everything an earlier fetch did."""
    return c.execute("SELECT s.* FROM fin_usage_snapshots s WHERE s.id = (SELECT t.id FROM fin_usage_snapshots t "
                     "WHERE t.month = s.month AND t.posted_at IS NULL AND t.ignored_at IS NULL "
                     "ORDER BY t.created_at DESC, t.as_of DESC, t.id DESC LIMIT 1) ORDER BY s.month LIMIT ?",
                     (most,)).fetchall()


def mark_posted(c: sqlite3.Connection, month: str, through: str, at: str) -> None:
    """Every snapshot of `month` up to `through` is done: the newest one's posting covered them."""
    c.execute("UPDATE fin_usage_snapshots SET posted_at=? WHERE month=? AND as_of<=? AND posted_at IS NULL "
              "AND ignored_at IS NULL", (at, month, through))


def ignore(c: sqlite3.Connection, snapshot_id: str, at: str) -> None:
    """A snapshot older than what is already posted: kept, never posted, never taken as the month's total."""
    c.execute("UPDATE fin_usage_snapshots SET ignored_at=? WHERE id=? AND posted_at IS NULL", (at, snapshot_id))


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


def last_entry(c: sqlite3.Connection, book_id: str, prefix: str) -> str | None:
    """The newest usage entry whose source id starts `prefix`."""
    row = c.execute("SELECT id FROM fin_entries WHERE book_id=? AND source='usage' AND substr(source_id, 1, ?) = ? "
                    "ORDER BY created_at DESC, id DESC LIMIT 1", (book_id, len(prefix), prefix)).fetchone()
    return str(row[0]) if row else None


def posted_by_teammate(c: sqlite3.Connection, book_id: str, account_id: str, prefix: str) -> dict[str, int]:
    """{teammate: cents} usage entries whose source id starts `prefix` put on each teammate on one account."""
    rows = c.execute("SELECT t.value, coalesce(sum(l.amount), 0) FROM fin_lines l JOIN fin_entries e ON e.id = l.entry_id "
                     "JOIN fin_line_tags t ON t.line_id = l.id AND t.kind = 'teammate' WHERE e.book_id=? "
                     "AND e.source='usage' AND substr(e.source_id, 1, ?) = ? AND l.account_id=? GROUP BY t.value",
                     (book_id, len(prefix), prefix, account_id)).fetchall()
    return {str(r[0]): int(r[1]) for r in rows}


def release_settled(c: sqlite3.Connection, hold_id: str, at: str) -> int:
    """A settled run hold no posting will ever stand for (its month's meter billed nothing on its line) frees up."""
    return c.execute("UPDATE fin_holds SET status='released', released_at=? WHERE id=? AND status='settled' "
                     "AND entry_id IS NULL", (at, hold_id)).rowcount


def extend_hold(c: sqlite3.Connection, hold_id: str, expires_at: str) -> int:
    return c.execute("UPDATE fin_holds SET expires_at=? WHERE id=? AND status='held'", (expires_at, hold_id)).rowcount


def released_run_hold(c: sqlite3.Connection, source_id: str) -> Any:
    """The run hold for `source_id` that expiry released, newest first."""
    return c.execute("SELECT * FROM fin_holds WHERE source='run' AND source_id=? AND status='released' "
                     "ORDER BY released_at DESC, id DESC LIMIT 1", (source_id,)).fetchone()


def resettle(c: sqlite3.Connection, hold_id: str, amount: int, at: str) -> int:
    """A released run hold settles after all: the run reported back after its hold expired."""
    return c.execute("UPDATE fin_holds SET status='settled', settled_amount=?, settled_at=?, released_at=NULL "
                     "WHERE id=? AND status='released'", (amount, at, hold_id)).rowcount


# ------------------------------------------------------------------------------------------------ events
def undrained(c: sqlite3.Connection, most: int) -> list[Any]:
    return c.execute("SELECT id, name, subject, period_key, payload FROM fin_events WHERE drained_at IS NULL "
                     "ORDER BY created_at, id LIMIT ?", (most,)).fetchall()


def drained(c: sqlite3.Connection, event_id: str, at: str) -> None:
    c.execute("UPDATE fin_events SET drained_at=? WHERE id=? AND drained_at IS NULL", (at, event_id))


def drain_all(c: sqlite3.Connection, at: str) -> int:
    """Mark every undrained event drained without acting on it (a dev copy's scrub)."""
    return c.execute("UPDATE fin_events SET drained_at=? WHERE drained_at IS NULL", (at,)).rowcount
