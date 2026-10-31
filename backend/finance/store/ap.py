"""SQL for payables and receivables (spec §4.1, §4.2): contacts, bills, invoices and their lines, attachments,
settlement links and the cash view's rollups. Callers pass plain values and check rights and views themselves."""
import json
import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any

from .banking import LIVE_PAYMENT, like
from .ledger import new_id, tagset

TABLES = frozenset({"fin_contacts", "fin_bills", "fin_bill_lines", "fin_invoices", "fin_invoice_lines",
                    "fin_attachments", "fin_settlements"})
DOCS = {"bill": ("fin_bills", "fin_bill_lines", "bill_id", "bill_date", "vendor_id"),
        "invoice": ("fin_invoices", "fin_invoice_lines", "invoice_id", "invoice_date", "customer_id")}


def insert(c: sqlite3.Connection, table: str, row: Mapping[str, object]) -> str:
    """Insert one row (an id is made when the row has none) and return its id."""
    assert table in TABLES
    values = {"id": new_id(), **row}
    cols = sorted(values)
    c.execute(f"INSERT INTO {table}({','.join(cols)}) VALUES({','.join('?' * len(cols))})", [values[k] for k in cols])
    return str(values["id"])


def update(c: sqlite3.Connection, table: str, row_id: str, fields: Mapping[str, object], where: str = "",
           args: Sequence[object] = ()) -> int:
    """Set `fields` on one row (only when `where` also holds). How many rows changed."""
    assert table in TABLES
    if not fields:
        return 0
    cols = sorted(fields)
    sql = f"UPDATE {table} SET {','.join(k + '=?' for k in cols)} WHERE id=?" + (f" AND ({where})" if where else "")
    return c.execute(sql, [*(fields[k] for k in cols), row_id, *args]).rowcount


def get(c: sqlite3.Connection, table: str, row_id: str) -> Any:
    assert table in TABLES
    return c.execute(f"SELECT * FROM {table} WHERE id=?", (row_id,)).fetchone()


def _marks(values: Sequence[object]) -> str:
    return ",".join("?" * len(values))


# ------------------------------------------------------------------------------------------------ documents
def lines(c: sqlite3.Connection, kind: str, doc_ids: Sequence[str]) -> dict[str, list[Any]]:
    """Each document's lines, in the order they were written."""
    if not doc_ids:
        return {}
    _, table, key, _, _ = DOCS[kind]
    out: dict[str, list[Any]] = {i: [] for i in doc_ids}
    sql = f"SELECT * FROM {table} WHERE {key} IN ({_marks(doc_ids)}) ORDER BY line_no, id"
    for r in c.execute(sql, list(doc_ids)):
        out[str(r[key])].append(r)
    return out


def set_line_tags(c: sqlite3.Connection, kind: str, line_id: str, tags: Mapping[str, str]) -> None:
    """A document line's tags, after its posted line was retagged."""
    _, table, _, _, _ = DOCS[kind]
    c.execute(f"UPDATE {table} SET tags_json=? WHERE id=?", (json.dumps(dict(tags), sort_keys=True), line_id))


def replace_lines(c: sqlite3.Connection, kind: str, doc_id: str, rows: Sequence[Mapping[str, object]]) -> list[str]:
    _, table, key, _, _ = DOCS[kind]
    c.execute(f"DELETE FROM {table} WHERE {key}=?", (doc_id,))
    return [insert(c, table, {key: doc_id, "line_no": n, **r}) for n, r in enumerate(rows)]


def docs_page(c: sqlite3.Connection, kind: str, *, book_id: str, statuses: Sequence[str] = (), contact_id: str = "",
              q: str = "", start: str = "", end: str = "", before: tuple[str, str] | None = None,
              most: int = 200) -> list[Any]:
    """Bills or invoices newest first by date (then id), with the contact's name as `contact_name`."""
    table, _, _, day, contact = DOCS[kind]
    sql = (f"SELECT d.*, k.name AS contact_name FROM {table} d LEFT JOIN fin_contacts k ON k.id=d.{contact} "
           "WHERE d.book_id=?")
    args: list[object] = [book_id]
    if statuses:
        sql += f" AND d.status IN ({_marks(statuses)})"
        args += list(statuses)
    if contact_id:
        sql += f" AND d.{contact}=?"
        args.append(contact_id)
    if start:
        sql += f" AND d.{day}>=?"
        args.append(start)
    if end:
        sql += f" AND d.{day}<=?"
        args.append(end)
    if q:
        sql += " AND (d.number LIKE ? ESCAPE '\\' OR d.memo LIKE ? ESCAPE '\\' OR k.name LIKE ? ESCAPE '\\')"
        args += [like(q)] * 3
    if before:
        sql += f" AND (d.{day}<? OR (d.{day}=? AND d.id<?))"
        args += [before[0], before[0], before[1]]
    return c.execute(sql + f" ORDER BY d.{day} DESC, d.id DESC LIMIT ?", [*args, most]).fetchall()


def doc_with_contact(c: sqlite3.Connection, kind: str, doc_id: str) -> Any:
    table, _, _, _, contact = DOCS[kind]
    return c.execute(f"SELECT d.*, k.name AS contact_name FROM {table} d LEFT JOIN fin_contacts k ON k.id=d.{contact} "
                     "WHERE d.id=?", (doc_id,)).fetchone()


def doc_actions(c: sqlite3.Connection, kind: str, doc_id: str, most: int) -> list[Any]:
    """The latest actions naming this document (its approvals among them)."""
    named = like(f'"{kind}_id": "{doc_id}"')
    return c.execute("SELECT * FROM fin_actions WHERE kind LIKE ? ESCAPE '\\' AND (payload_json LIKE ? ESCAPE '\\' "
                     "OR result_json LIKE ? ESCAPE '\\') ORDER BY created_at DESC LIMIT ?",
                     (f"{kind}\\_%", named, named, most)).fetchall()


def open_docs(c: sqlite3.Connection, kind: str, book_id: str, most: int = 5000) -> list[Any]:
    """Open bills or sent invoices (what is still owed), oldest due first, with `contact_name`."""
    table, _, _, _, contact = DOCS[kind]
    statuses = ("open", "scheduled") if kind == "bill" else ("sent", "partial")
    return c.execute(f"SELECT d.*, k.name AS contact_name FROM {table} d LEFT JOIN fin_contacts k ON k.id=d.{contact} "
                     f"WHERE d.book_id=? AND d.status IN ({_marks(statuses)}) "
                     "ORDER BY COALESCE(d.due_date, '9999-12-31'), d.id LIMIT ?", (book_id, *statuses, most)).fetchall()


def drafts(c: sqlite3.Connection, book_id: str, most: int) -> list[Any]:
    return c.execute("SELECT d.*, k.name AS contact_name FROM fin_bills d LEFT JOIN fin_contacts k ON k.id=d.vendor_id "
                     "WHERE d.book_id=? AND d.status='draft' ORDER BY d.created_at, d.id LIMIT ?",
                     (book_id, most)).fetchall()


def scheduled_due(c: sqlite3.Connection, book_id: str, day: str, most: int = 200) -> list[Any]:
    """Scheduled bills whose payment day has come."""
    return c.execute("SELECT * FROM fin_bills WHERE book_id=? AND status='scheduled' AND pay_on<=? ORDER BY pay_on, id "
                     "LIMIT ?", (book_id, day, most)).fetchall()


def number_taken(c: sqlite3.Connection, kind: str, contact_id: str, number: str, not_id: str = "") -> bool:
    """Whether a live document from this contact already has this number (a bill emailed twice)."""
    table, _, _, _, contact = DOCS[kind]
    return c.execute(f"SELECT 1 FROM {table} WHERE {contact}=? AND number=? AND status!='void' AND id!=? LIMIT 1",
                     (contact_id, number, not_id)).fetchone() is not None


# ------------------------------------------------------------------------------------------------ contacts
def contacts_page(c: sqlite3.Connection, *, book_id: str, kinds: Sequence[str] = (), q: str = "",
                  active: bool | None = True, after: tuple[str, str] | None = None, most: int = 200) -> list[Any]:
    """Contacts by name (then id)."""
    sql = "SELECT * FROM fin_contacts WHERE book_id=?"
    args: list[object] = [book_id]
    if kinds:
        sql += f" AND kind IN ({_marks(kinds)})"
        args += list(kinds)
    if active is not None:
        sql += " AND active=?"
        args.append(1 if active else 0)
    if q:
        sql += " AND (name LIKE ? ESCAPE '\\' OR emails_json LIKE ? ESCAPE '\\')"
        args += [like(q)] * 2
    if after:
        sql += " AND (name>? OR (name=? AND id>?))"
        args += [after[0], after[0], after[1]]
    return c.execute(sql + " ORDER BY name, id LIMIT ?", [*args, most]).fetchall()


def contacts(c: sqlite3.Connection, ids: Sequence[str]) -> dict[str, Any]:
    if not ids:
        return {}
    return {str(r["id"]): r for r in c.execute(f"SELECT * FROM fin_contacts WHERE id IN ({_marks(ids)})", list(ids))}


def contact_named(c: sqlite3.Connection, book_id: str, name: str) -> Any:
    return c.execute("SELECT * FROM fin_contacts WHERE book_id=? AND lower(name)=lower(?) AND active=1 LIMIT 1",
                     (book_id, name)).fetchone()


def owed(c: sqlite3.Connection, kind: str, contact_ids: Sequence[str]) -> dict[str, int]:
    """What each contact's open documents still owe."""
    if not contact_ids:
        return {}
    table, _, _, _, contact = DOCS[kind]
    paid = "amount_paid" if kind == "bill" else "amount_paid + written_off"
    statuses = ("open", "scheduled") if kind == "bill" else ("sent", "partial")
    return {str(r[0]): int(r[1]) for r in c.execute(
        f"SELECT {contact}, SUM(total - {paid}) FROM {table} WHERE {contact} IN ({_marks(contact_ids)}) "
        f"AND status IN ({_marks(statuses)}) GROUP BY {contact}", (*contact_ids, *statuses))}


def open_for(c: sqlite3.Connection, kind: str, contact_ids: Sequence[str], most: int) -> list[Any]:
    """The open documents of these contacts (what `owed` sums), for a caller that cuts them to a viewer first."""
    if not contact_ids:
        return []
    table, _, _, _, contact = DOCS[kind]
    statuses = ("open", "scheduled") if kind == "bill" else ("sent", "partial")
    return c.execute(f"SELECT * FROM {table} WHERE {contact} IN ({_marks(contact_ids)}) AND status IN "
                     f"({_marks(statuses)}) ORDER BY id LIMIT ?", (*contact_ids, *statuses, most)).fetchall()


# ------------------------------------------------------------------------------------------------ settlements
def settlements_to(c: sqlite3.Connection, kind: str, doc_id: str, live: bool = True, most: int = -1) -> list[Any]:
    sql = "SELECT * FROM fin_settlements WHERE to_kind=? AND to_id=?" + (" AND voided_at IS NULL" if live else "")
    return c.execute(sql + " ORDER BY settled_on, created_at, id LIMIT ?", (kind, doc_id, most)).fetchall()


def settlement_count(c: sqlite3.Connection, kind: str, doc_id: str) -> int:
    return int(c.execute("SELECT count(*) FROM fin_settlements WHERE to_kind=? AND to_id=?", (kind, doc_id)
                         ).fetchone()[0])


def settlements_from(c: sqlite3.Connection, kind: str, from_id: str) -> list[Any]:
    return c.execute("SELECT * FROM fin_settlements WHERE from_kind=? AND from_id=? AND voided_at IS NULL "
                     "ORDER BY created_at", (kind, from_id)).fetchall()


def expected(c: sqlite3.Connection, kind: str, money_account_id: str, amount: int, start: str, end: str,
             most: int = 20) -> list[Any]:
    """Payments marked from a connected money account still waiting for their bank row, of this amount (0: any) in a
    window, with the document's `contact_id` and `contact_name`."""
    table, _, _, _, contact = DOCS[kind]
    return c.execute(f"SELECT s.*, d.{contact} AS contact_id, k.name AS contact_name FROM fin_settlements s "
                     f"JOIN {table} d ON d.id=s.to_id LEFT JOIN fin_contacts k ON k.id=d.{contact} "
                     "WHERE s.from_kind='expected' AND s.to_kind=? AND s.from_id=? AND (?=0 OR s.amount=?) AND "
                     "s.settled_on BETWEEN ? AND ? AND s.voided_at IS NULL ORDER BY s.created_at, s.id LIMIT ?",
                     (kind, money_account_id, amount, amount, start, end, most)).fetchall()


def settlements_by_entry(c: sqlite3.Connection, entry_id: str) -> list[Any]:
    return c.execute("SELECT * FROM fin_settlements WHERE entry_id=? AND voided_at IS NULL ORDER BY created_at",
                     (entry_id,)).fetchall()


def settled(c: sqlite3.Connection, kind: str, doc_id: str) -> int:
    row = c.execute("SELECT COALESCE(SUM(amount), 0) FROM fin_settlements WHERE to_kind=? AND to_id=? AND "
                    "voided_at IS NULL", (kind, doc_id)).fetchone()
    return int(row[0])


# ------------------------------------------------------------------------------------------------ attachments
def attachments(c: sqlite3.Connection, kind: str, subject_ids: Sequence[str]) -> dict[str, list[Any]]:
    if not subject_ids:
        return {}
    out: dict[str, list[Any]] = {i: [] for i in subject_ids}
    for r in c.execute(f"SELECT * FROM fin_attachments WHERE subject_kind=? AND subject_id IN ({_marks(subject_ids)}) "
                       "ORDER BY created_at, id", (kind, *subject_ids)):
        out[str(r["subject_id"])].append(r)
    return out


def attachment_counts(c: sqlite3.Connection, kind: str, subject_ids: Sequence[str]) -> dict[str, int]:
    if not subject_ids:
        return {}
    return {str(r[0]): int(r[1]) for r in c.execute(
        f"SELECT subject_id, count(*) FROM fin_attachments WHERE subject_kind=? AND subject_id IN "
        f"({_marks(subject_ids)}) GROUP BY subject_id", (kind, *subject_ids))}


def attachments_first(c: sqlite3.Connection, kind: str, subject_id: str, most: int) -> list[Any]:
    return c.execute("SELECT * FROM fin_attachments WHERE subject_kind=? AND subject_id=? ORDER BY created_at, id "
                     "LIMIT ?", (kind, subject_id, most)).fetchall()


def attach(c: sqlite3.Connection, book_id: str, kind: str, subject_id: str, file_ids: Sequence[str], by: str,
           at: str) -> list[str]:
    """Attach files not already on the subject. The new attachment ids."""
    have = {str(r["file_id"]) for r in attachments(c, kind, [subject_id])[subject_id]}
    return [insert(c, "fin_attachments", {"book_id": book_id, "subject_kind": kind, "subject_id": subject_id,
                                          "file_id": f, "created_at": at, "created_by": by})
            for f in file_ids if f not in have]


def unmatched_files(c: sqlite3.Connection, book_id: str, most: int, by: str = "") -> list[Any]:
    """Receipts uploaded with nothing to attach to yet (subject `unmatched`); with `by`, only that teammate's."""
    return c.execute("SELECT * FROM fin_attachments WHERE book_id=? AND subject_kind='unmatched' AND "
                     "(?='' OR created_by=?) ORDER BY created_at DESC, id LIMIT ?", (book_id, by, by, most)).fetchall()


def card_rows_without_receipt(c: sqlite3.Connection, money_ids: Sequence[str], card_ids: Sequence[str], since: str,
                              most: int) -> list[Any]:
    """Posted card charges (on these card programs, or these cards) since `since` with no receipt, newest first."""
    scopes: list[str] = []
    args: list[object] = []
    if money_ids:
        scopes.append(f"f.money_account_id IN ({_marks(money_ids)})")
        args += list(money_ids)
    if card_ids:
        scopes.append(f"f.card_id IN ({_marks(card_ids)})")
        args += list(card_ids)
    if not scopes:
        return []
    return c.execute(
        f"SELECT f.* FROM fin_feed_txns f JOIN fin_money_accounts m ON m.id=f.money_account_id WHERE "
        f"({' OR '.join(scopes)}) AND m.kind='card_program' AND f.txn_date>=? AND f.amount<0 AND f.status='posted' "
        "AND f.pending=0 AND NOT EXISTS (SELECT 1 FROM fin_attachments a WHERE a.subject_kind='feed' AND "
        "a.subject_id=f.id) ORDER BY f.txn_date DESC, f.id DESC LIMIT ?", (*args, since, most)).fetchall()


def uncategorized(c: sqlite3.Connection, money_ids: Sequence[str], card_ids: Sequence[str], other_ids: Sequence[str],
                  start: str, end: str, most: int) -> list[Any]:
    """Settled feed rows still waiting for a category, newest first: new ones, and ones posted to Other expenses (in
    the window, on a live entry)."""
    scopes: list[str] = []
    args: list[object] = []
    if money_ids:
        scopes.append(f"f.money_account_id IN ({_marks(money_ids)})")
        args += list(money_ids)
    if card_ids:
        scopes.append(f"f.card_id IN ({_marks(card_ids)})")
        args += list(card_ids)
    if not scopes:
        return []
    other = (f"OR (f.status='posted' AND EXISTS (SELECT 1 FROM fin_lines l JOIN fin_entries e ON e.id=l.entry_id "
             f"WHERE l.entry_id=f.entry_id AND l.account_id IN ({_marks(other_ids)}) AND e.status='posted'))"
             if other_ids else "")
    return c.execute(f"SELECT f.* FROM fin_feed_txns f WHERE ({' OR '.join(scopes)}) AND f.pending=0 "
                     f"AND f.txn_date>=? AND f.txn_date<=? AND (f.status='new' {other}) "
                     "ORDER BY f.txn_date DESC, f.id DESC LIMIT ?",
                     (*args, start, end, *other_ids, most)).fetchall()


# ------------------------------------------------------------------------------------------------ cash view
def cash_roll(c: sqlite3.Connection, book_id: str, day: str, account_id: str, tags: Mapping[str, str], vendor: str,
              amount: int) -> None:
    """Move the cash view's correction for one line: what the accrual rollups hold minus what cash recognized."""
    if not amount:
        return
    c.execute("INSERT INTO fin_cash_rollups(book_id, day, account_id, tagset, team, teammate, vendor, amount) "
              "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(book_id, day, account_id, tagset) DO UPDATE SET "
              "amount=fin_cash_rollups.amount+excluded.amount",
              (book_id, day, account_id, tagset(tags, vendor), tags.get("team", ""), tags.get("teammate", ""), vendor,
               amount))


def cash_rows(c: sqlite3.Connection, book_id: str, start: str, end: str, account_ids: Sequence[str]) -> list[Any]:
    if not account_ids:
        return []
    return c.execute("SELECT day, account_id, tagset, team, teammate, vendor, amount FROM fin_cash_rollups "
                     f"WHERE book_id=? AND day>=? AND day<=? AND amount!=0 AND account_id IN ({_marks(account_ids)})",
                     (book_id, start, end, *account_ids)).fetchall()


# ------------------------------------------------------------------------------------------------ posting rules
def posting_rules(c: sqlite3.Connection) -> list[Any]:
    """Active posting rules, first to run first."""
    return c.execute("SELECT * FROM fin_rules WHERE kind='posting' AND active=1 ORDER BY priority, id").fetchall()


def settled_from(c: sqlite3.Connection, from_kind: str, from_id: str) -> int:
    """How much of a payment or feed row is applied to documents already."""
    row = c.execute("SELECT COALESCE(SUM(amount), 0) FROM fin_settlements WHERE from_kind=? AND from_id=? AND "
                    "voided_at IS NULL", (from_kind, from_id)).fetchone()
    return int(row[0])


def open_owing(c: sqlite3.Connection, kind: str, book_id: str, left: int, most: int = 20) -> list[Any]:
    """Open documents that still owe exactly `left`, with `contact_name` (what a new feed row may be paying). A
    scheduled bill counts (the vendor may pull it before its day); one with a live Finance payment does not (that
    payment's own row settles it)."""
    table, _, _, _, contact = DOCS[kind]
    paid = "amount_paid" if kind == "bill" else "amount_paid + written_off"
    statuses = ("open", "scheduled") if kind == "bill" else ("sent", "partial")
    live = ("AND NOT EXISTS (SELECT 1 FROM fin_payments p WHERE p.obligation='bill:' || d.id AND p.status IN "
            f"({_marks(LIVE_PAYMENT)}))") if kind == "bill" else ""
    args = (book_id, *statuses, left, *(LIVE_PAYMENT if kind == "bill" else ()), most)
    return c.execute(f"SELECT d.*, k.name AS contact_name FROM {table} d LEFT JOIN fin_contacts k ON k.id=d.{contact} "
                     f"WHERE d.book_id=? AND d.status IN ({_marks(statuses)}) AND d.total - {paid} = ? {live} "
                     "ORDER BY d.id LIMIT ?", args).fetchall()


def drop_unmatched(c: sqlite3.Connection, book_id: str, file_ids: Sequence[str]) -> int:
    """Files filed to a subject leave the unmatched receipts."""
    if not file_ids:
        return 0
    return c.execute(f"DELETE FROM fin_attachments WHERE book_id=? AND subject_kind='unmatched' AND file_id IN "
                     f"({_marks(file_ids)})", (book_id, *file_ids)).rowcount


def doc_count(c: sqlite3.Connection, kind: str, book_id: str) -> int:
    table = DOCS[kind][0]
    return int(c.execute(f"SELECT COUNT(*) FROM {table} WHERE book_id=?", (book_id,)).fetchone()[0])
