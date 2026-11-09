"""SQL for receipts (backend/finance/receipts.py): the fin_receipts rows, the bank and card rows a receipt can match,
and the Library text a receipt is read from. Every write is an explicit column list; no logic lives here."""

import json
import sqlite3
from collections.abc import Sequence
from typing import Any, cast

Row = dict[str, Any]
OPEN = ("unmatched", "suggested", "rejected")  # receipts a match may still be found for


def _marks(values: Sequence[object]) -> str:
    return ",".join("?" * len(values))


def _rows(cur: sqlite3.Cursor) -> list[Row]:
    return [dict(r) for r in cur.fetchall()]


def get(c: sqlite3.Connection, receipt_id: str) -> Row | None:
    row = c.execute("SELECT * FROM fin_receipts WHERE id=?", (receipt_id,)).fetchone()
    return None if row is None else dict(row)


def by_file(c: sqlite3.Connection, book_id: str, file_id: str) -> Row | None:
    row = c.execute("SELECT * FROM fin_receipts WHERE book_id=? AND file_id=?", (book_id, file_id)).fetchone()
    return None if row is None else dict(row)


def insert(c: sqlite3.Connection, row: Row) -> None:
    cols = sorted(row)
    c.execute(f"INSERT INTO fin_receipts({','.join(cols)}) VALUES({','.join('?' * len(cols))})", [row[k] for k in cols])


def update(c: sqlite3.Connection, receipt_id: str, fields: Row) -> None:
    if not fields:
        return
    cols = sorted(fields)
    c.execute(
        f"UPDATE fin_receipts SET {','.join(k + '=?' for k in cols)} WHERE id=?",
        [fields[k] for k in cols] + [receipt_id],
    )


def page(c: sqlite3.Connection, book_id: str, statuses: Sequence[str], after: Sequence[str], limit: int) -> list[Row]:
    """Receipts of the book, newest first (created_at, id), after a cursor position, with a status filter."""
    where = ["book_id=?"]
    args: list[object] = [book_id]
    if statuses:
        where.append(f"status IN ({_marks(statuses)})")
        args += list(statuses)
    if len(after) == 2:
        where.append("(created_at < ? OR (created_at = ? AND id < ?))")
        args += [after[0], after[0], after[1]]
    return _rows(
        c.execute(
            f"SELECT * FROM fin_receipts WHERE {' AND '.join(where)} ORDER BY created_at DESC, id DESC LIMIT ?",
            (*args, limit),
        )
    )


def open_near(c: sqlite3.Connection, book_id: str, first: str, last: str) -> list[Row]:
    """Open receipts whose read date falls in [first, last] (ISO days): the ones a feed row dated in that span could
    match."""
    return _rows(
        c.execute(
            f"SELECT * FROM fin_receipts WHERE book_id=? AND status IN ({_marks(OPEN)}) AND read_date BETWEEN ? AND ?",
            (book_id, *OPEN, first, last),
        )
    )


def open_read(c: sqlite3.Connection, book_id: str, since: str) -> list[Row]:
    """Every open receipt with a read date from `since` on (the hourly pass settles these)."""
    return _rows(
        c.execute(
            f"SELECT * FROM fin_receipts WHERE book_id=? AND status IN ({_marks(OPEN)}) AND read_date >= ?",
            (book_id, *OPEN, since),
        )
    )


def unread(c: sqlite3.Connection, book_id: str, most: int) -> list[Row]:
    return _rows(
        c.execute(
            "SELECT * FROM fin_receipts WHERE book_id=? AND status='unread' ORDER BY created_at, id LIMIT ?",
            (book_id, most),
        )
    )


def holding(c: sqlite3.Connection, book_id: str, feed_ids: Sequence[str]) -> list[Row]:
    """The receipts suggested on one of these feed rows (a feed row never holds two suggestions)."""
    if not feed_ids:
        return []
    return _rows(
        c.execute(
            f"SELECT * FROM fin_receipts WHERE book_id=? AND status='suggested' AND match_feed_id IN ({_marks(feed_ids)})",
            (book_id, *feed_ids),
        )
    )


def suggested(c: sqlite3.Connection, book_id: str, most: int) -> list[Row]:
    return _rows(
        c.execute(
            "SELECT * FROM fin_receipts WHERE book_id=? AND status='suggested' ORDER BY created_at DESC, id DESC LIMIT ?",
            (book_id, most),
        )
    )


def unmatched_files(c: sqlite3.Connection, book_id: str) -> list[Row]:
    """The unmatched receipt uploads (fin_attachments subject `unmatched`) with no receipt row yet."""
    return _rows(
        c.execute(
            "SELECT a.file_id, a.created_by, a.created_at FROM fin_attachments a WHERE a.book_id=? AND "
            "a.subject_kind='unmatched' AND NOT EXISTS (SELECT 1 FROM fin_receipts r WHERE r.book_id=a.book_id "
            "AND r.file_id=a.file_id) ORDER BY a.created_at, a.id LIMIT 200",
            (book_id,),
        )
    )


def candidates(c: sqlite3.Connection, book_id: str, first: str, last: str) -> list[Row]:
    """The bank and card rows a receipt may match in [first, last]: settled debits (not pending, not ignored or covered)
    with no file attached yet."""
    return _rows(
        c.execute(
            "SELECT f.id, f.txn_date, f.amount, f.counterparty, f.description, f.vendor_key, f.card_id "
            "FROM fin_feed_txns f JOIN fin_money_accounts m ON m.id=f.money_account_id "
            "WHERE m.book_id=? AND f.txn_date BETWEEN ? AND ? AND f.amount<0 AND f.pending=0 "
            "AND f.status IN ('new','matched','posted') AND NOT EXISTS (SELECT 1 FROM fin_attachments a "
            "WHERE a.subject_kind='feed' AND a.subject_id=f.id) ORDER BY f.id",
            (book_id, first, last),
        )
    )


def file_titles(c: sqlite3.Connection, file_ids: Sequence[str]) -> dict[str, str]:
    """The Library titles of these files (a file that was deleted or is gone has none)."""
    if not file_ids:
        return {}
    try:
        return {
            str(r[0]): str(r[1])
            for r in c.execute(f"SELECT id, title FROM bot_files WHERE id IN ({_marks(file_ids)})", tuple(file_ids))
        }
    except sqlite3.OperationalError:  # no Library on this install
        return {}


def library_text(c: sqlite3.Connection, file_id: str) -> tuple[str, str] | None:
    """The title and the indexed text of a Library file's current version, or None when the index does not hold it yet
    (PDFs and images have no text; the index fills after the file is published). Reads the index, not the bytes."""
    try:
        row = c.execute(
            "SELECT f.title, t.text FROM bot_files f JOIN file_text_state s ON s.file_id=f.id "
            "AND s.version=f.current_version JOIN file_text t ON t.rowid=s.doc "
            "WHERE f.id=? AND f.state<>'deleted'",
            (file_id,),
        ).fetchone()
    except sqlite3.OperationalError:  # no Library, or SQLite without FTS5: no text to read
        return None
    return None if row is None else (str(row[0]), str(row[1]))


def library_file_ok(c: sqlite3.Connection, file_id: str) -> bool:
    """Whether the Library still holds this file (not deleted)."""
    try:
        return c.execute("SELECT 1 FROM bot_files WHERE id=? AND state<>'deleted'", (file_id,)).fetchone() is not None
    except sqlite3.OperationalError:
        return False


def rejected(row: Row) -> list[str]:
    """The feed rows a receipt was refused for (a JSON list on the row)."""
    try:
        got: object = json.loads(str(row.get("rejected_feed_ids") or "[]"))
    except (TypeError, ValueError):
        return []
    if not isinstance(got, list):
        return []
    return [str(x) for x in cast("list[object]", got)]
