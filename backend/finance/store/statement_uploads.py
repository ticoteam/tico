"""SQL for statement uploads (backend/finance/statement_upload.py): the feed rows and journal lines an upload is checked
against, the uploads (each found again by its file's hash, per money account), the manual statement rows an upload
makes, and the short-lived preview tokens. Callers pass plain values; no provider is called from here."""

import sqlite3
from collections.abc import Mapping
from typing import Any

from . import banking as SK

# The journal sources a person or an import made: a bank or card entry is matched through its feed row instead.
MANUAL_SOURCES = ("manual", "import")


def feed_rows_between(c: sqlite3.Connection, money_account_id: str, start: str, end: str) -> list[Any]:
    """Every feed row of a money account dated in a window, of any status (a row already in Tico is a duplicate)."""
    return c.execute(
        "SELECT * FROM fin_feed_txns WHERE money_account_id=? AND txn_date BETWEEN ? AND ? ORDER BY txn_date, id",
        (money_account_id, start, end),
    ).fetchall()


def journal_lines_between(c: sqlite3.Connection, book_id: str, account_id: str, start: str, end: str) -> list[Any]:
    """The live lines a person or an import posted to a money account's GL line in a window: a posted original that
    no reversal undid (a reversal and a reversed original are both left out)."""
    marks = ",".join("?" * len(MANUAL_SOURCES))
    return c.execute(
        "SELECT l.entry_date, l.amount, e.memo FROM fin_lines l JOIN fin_entries e ON e.id=l.entry_id "
        "WHERE e.book_id=? AND l.account_id=? AND l.entry_date BETWEEN ? AND ? AND e.status='posted' "
        f"AND e.reverses_id IS NULL AND e.source IN ({marks}) ORDER BY l.entry_date, l.id",
        (book_id, account_id, start, end, *MANUAL_SOURCES),
    ).fetchall()


def upload_by_hash(c: sqlite3.Connection, money_account_id: str, file_hash: str) -> Any:
    """The live upload of this file to this money account, if there is one."""
    return c.execute(
        "SELECT * FROM fin_statement_uploads WHERE money_account_id=? AND file_hash=? AND undone_at IS NULL "
        "ORDER BY created_at, id LIMIT 1",
        (money_account_id, file_hash),
    ).fetchone()


def upload_get(c: sqlite3.Connection, upload_id: str) -> Any:
    return c.execute("SELECT * FROM fin_statement_uploads WHERE id=?", (upload_id,)).fetchone()


def upload_rows(c: sqlite3.Connection, upload_id: str) -> list[Any]:
    """The feed rows an upload put in (the rows still in the feed)."""
    return c.execute("SELECT * FROM fin_feed_txns WHERE upload_id=? ORDER BY txn_date, id", (upload_id,)).fetchall()


def insert_upload(c: sqlite3.Connection, row: Mapping[str, object]) -> None:
    cols = sorted(row)
    c.execute(
        f"INSERT INTO fin_statement_uploads({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
        [row[k] for k in cols],
    )


def set_upload(c: sqlite3.Connection, upload_id: str, fields: Mapping[str, object]) -> None:
    cols = sorted(fields)
    c.execute(
        f"UPDATE fin_statement_uploads SET {','.join(k + '=?' for k in cols)} WHERE id=?",
        [*(fields[k] for k in cols), upload_id],
    )


def insert_feed(c: sqlite3.Connection, row: Mapping[str, object]) -> str:
    """One feed row, with an id made here (feed rows take their id as banking.ingest gives them)."""
    return SK.insert(c, "fin_feed_txns", row)


def delete_rows(c: sqlite3.Connection, upload_id: str) -> int:
    """Remove the feed rows an upload put in. Their entries are reversed by the caller first."""
    return c.execute("DELETE FROM fin_feed_txns WHERE upload_id=?", (upload_id,)).rowcount


def insert_statement(c: sqlite3.Connection, row: Mapping[str, object]) -> None:
    """A manual statement row (source 'manual'): the closing balance a file gave for its end day."""
    cols = sorted(row)
    c.execute(
        f"INSERT INTO fin_statements({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
        [row[k] for k in cols],
    )


def delete_statement(c: sqlite3.Connection, statement_id: str) -> None:
    c.execute("DELETE FROM fin_statements WHERE id=? AND source='manual'", (statement_id,))


def off_by(c: sqlite3.Connection, money_account_id: str) -> int | None:
    """The latest upload's tie-out gap, while it is nonzero and the account is not reconciled through that statement's
    end (a reconciliation through that day clears it). None otherwise."""
    up = c.execute(
        "SELECT off_by, end_date FROM fin_statement_uploads WHERE money_account_id=? AND undone_at IS NULL "
        "ORDER BY created_at DESC, id DESC LIMIT 1",
        (money_account_id,),
    ).fetchone()
    if up is None or not int(up["off_by"]):
        return None
    last = SK.last_reconciliation(c, money_account_id)
    if last is not None and str(last["statement_end"]) >= str(up["end_date"]):
        return None
    return int(up["off_by"])


def preview_put(c: sqlite3.Connection, row: Mapping[str, object]) -> None:
    cols = sorted(row)
    c.execute(
        f"INSERT INTO fin_statement_previews({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
        [row[k] for k in cols],
    )


def preview_get(c: sqlite3.Connection, token_hash: str) -> Any:
    return c.execute("SELECT * FROM fin_statement_previews WHERE token_hash=?", (token_hash,)).fetchone()


def preview_delete(c: sqlite3.Connection, token_hash: str) -> None:
    c.execute("DELETE FROM fin_statement_previews WHERE token_hash=?", (token_hash,))


def preview_purge(c: sqlite3.Connection, at: str) -> None:
    """Forget the previews that have expired by `at`."""
    c.execute("DELETE FROM fin_statement_previews WHERE expires_at<?", (at,))

