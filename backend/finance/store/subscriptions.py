"""SQL for subscriptions (backend/finance/subscriptions.py): the fin_subscriptions rows. Callers pass plain values and
check rights and views themselves."""

import json
import sqlite3
from collections.abc import Mapping
from typing import Any

from .ledger import new_id

Row = Any


def book_rows(c: sqlite3.Connection, book_id: str, status: str | None = None) -> list[Row]:
    """The book's subscriptions, by vendor then source (a status narrows them)."""
    if status is None:
        return c.execute(
            "SELECT * FROM fin_subscriptions WHERE book_id=? ORDER BY vendor_key, source_key", (book_id,)
        ).fetchall()
    return c.execute(
        "SELECT * FROM fin_subscriptions WHERE book_id=? AND status=? ORDER BY vendor_key, source_key",
        (book_id, status),
    ).fetchall()


def get(c: sqlite3.Connection, row_id: str) -> Row:
    return c.execute("SELECT * FROM fin_subscriptions WHERE id=?", (row_id,)).fetchone()


def by_key(c: sqlite3.Connection, book_id: str, vendor_key: str, source_key: str) -> Row:
    return c.execute(
        "SELECT * FROM fin_subscriptions WHERE book_id=? AND vendor_key=? AND source_key=?",
        (book_id, vendor_key, source_key),
    ).fetchone()


def insert(c: sqlite3.Connection, book_id: str, row: Mapping[str, object]) -> str:
    """Insert one subscription and return its id."""
    values: dict[str, object] = {"id": new_id(), "book_id": book_id, **row}
    cols = sorted(values)
    c.execute(
        f"INSERT INTO fin_subscriptions({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
        [values[k] for k in cols],
    )
    return str(values["id"])


def update(c: sqlite3.Connection, row_id: str, fields: Mapping[str, object]) -> None:
    if not fields:
        return
    cols = sorted(fields)
    c.execute(
        f"UPDATE fin_subscriptions SET {','.join(f'{k}=?' for k in cols)} WHERE id=?",
        [fields[k] for k in cols] + [row_id],
    )


def prior_json(fields: Mapping[str, object]) -> str:
    return json.dumps(dict(fields), sort_keys=True)


def prior_of(row: Row) -> dict[str, Any] | None:
    """The fields before the latest confirmation, or None when there is nothing to undo."""
    raw = row["prior"]
    return json.loads(str(raw)) if raw else None


def vendor_label(c: sqlite3.Connection, book_id: str, vendor_key: str) -> str:
    """The text a vendor key with no contact was last posted under (display only; "" when none)."""
    row = c.execute(
        "SELECT label FROM fin_vendor_labels WHERE book_id=? AND name_key=?", (book_id, vendor_key)
    ).fetchone()
    return str(row[0]) if row is not None else ""


def card_name(c: sqlite3.Connection, card_id: str) -> str:
    """A card's nickname, else its last four digits, as a person reads it ("" when there is no such card)."""
    row = c.execute("SELECT nickname, last4 FROM fin_cards WHERE id=?", (card_id,)).fetchone()
    if row is None:
        return ""
    if row[0]:
        return str(row[0])
    return f"Card ending {row[1]}" if row[1] else "Card"
