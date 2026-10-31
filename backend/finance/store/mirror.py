"""SQL for the mirror (spec §10): external refs (which provider record each local row is), restatements, a connection's
backfill and change-feed positions, the contacts a provider's party is, and the bank rows an entry can match. Callers
pass plain values; no provider is called from here."""
import json
import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any, cast

from .ledger import new_id


def ref(c: sqlite3.Connection, cid: str, local_table: str, external_id: str) -> Any:
    return c.execute("SELECT * FROM fin_external_refs WHERE connection_id=? AND local_table=? AND external_id=?",
                     (cid, local_table, external_id)).fetchone()


def ref_for_local(c: sqlite3.Connection, cid: str, local_table: str, local_id: str) -> Any:
    return c.execute("SELECT * FROM fin_external_refs WHERE connection_id=? AND local_table=? AND local_id=? "
                     "ORDER BY created_at LIMIT 1", (cid, local_table, local_id)).fetchone()


def ref_insert(c: sqlite3.Connection, *, cid: str, local_table: str, local_id: str, kind: str, external_id: str,
               revision: str, status: str, at: str, by: str) -> None:
    c.execute("INSERT INTO fin_external_refs(id, connection_id, local_table, local_id, external_id, kind, revision, "
              "status, created_at, created_by, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
              (new_id(), cid, local_table, local_id, external_id, kind, revision, status, at, by, at))


def ref_set(c: sqlite3.Connection, ref_id: str, *, local_id: str, revision: str, status: str, at: str) -> None:
    c.execute("UPDATE fin_external_refs SET local_id=?, revision=?, status=?, updated_at=? WHERE id=?",
              (local_id, revision, status, at, ref_id))


def restatement_insert(c: sqlite3.Connection, *, book_id: str, period_id: str, day: str, change: str,
                       entry_id: str | None, reversal_id: str | None, cid: str | None, external_id: str, at: str,
                       by: str) -> None:
    c.execute("INSERT INTO fin_restatements(id, book_id, period_id, day, change, entry_id, reversal_id, connection_id, "
              "external_id, created_at, created_by) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
              (new_id(), book_id, period_id, day, change, entry_id, reversal_id, cid, external_id, at, by))


def restatements(c: sqlite3.Connection, book_id: str, most: int) -> list[Any]:
    """The newest restatements of closed months, for the close pages."""
    return c.execute("SELECT r.*, p.start_date AS period_start FROM fin_restatements r JOIN fin_periods p ON "
                     "p.id=r.period_id WHERE r.book_id=? ORDER BY r.created_at DESC, r.id LIMIT ?",
                     (book_id, most)).fetchall()


def settlements_of(c: sqlite3.Connection, entry_id: str) -> list[Any]:
    """The settlements a payment's entry made that have not been undone."""
    return c.execute("SELECT * FROM fin_settlements WHERE entry_id=? AND voided_at IS NULL ORDER BY created_at, id",
                     (entry_id,)).fetchall()


def as_map(value: object) -> dict[str, object]:
    """A JSON object as a dict; any other value reads as empty."""
    return cast(dict[str, object], value) if isinstance(value, dict) else {}


def _settings(c: sqlite3.Connection, cid: str) -> dict[str, object]:
    """A connection's settings_json as a dict; missing or unreadable settings read as empty."""
    row = c.execute("SELECT settings_json FROM fin_connections WHERE id=?", (cid,)).fetchone()
    try:
        value: object = json.loads(row[0]) if row and row[0] else {}
    except ValueError:
        return {}
    return as_map(value)


def mirror_state(c: sqlite3.Connection, cid: str) -> dict[str, object]:
    """A connection's mirror positions (its backfill and change-feed read), kept under `mirror` in its settings."""
    return as_map(_settings(c, cid).get("mirror"))


def set_mirror_state(c: sqlite3.Connection, cid: str, fields: Mapping[str, object]) -> None:
    settings = _settings(c, cid)
    settings["mirror"] = {**mirror_state(c, cid), **fields}
    c.execute("UPDATE fin_connections SET settings_json=? WHERE id=?", (json.dumps(settings, sort_keys=True), cid))


def candidates(c: sqlite3.Connection, book_id: str, gl_account_id: str, amount: int, start: str,
               end: str) -> list[tuple[str, str, int]]:
    """Live entries (not reversals, not reversed, not already a bank row's) with a line on the bank's account for this
    amount in a window: (entry id, entry date, line amount)."""
    rows = c.execute(
        "SELECT e.id, e.entry_date, l.amount FROM fin_lines l JOIN fin_entries e ON e.id=l.entry_id "
        "WHERE e.book_id=? AND l.account_id=? AND l.amount=? AND e.entry_date BETWEEN ? AND ? AND e.status='posted' "
        "AND e.reverses_id IS NULL AND NOT EXISTS (SELECT 1 FROM fin_feed_txns f WHERE f.entry_id=e.id) "
        "ORDER BY e.entry_date, e.id LIMIT 200", (book_id, gl_account_id, amount, start, end)).fetchall()
    return [(str(r[0]), str(r[1]), int(r[2])) for r in rows]


def unmatched_rows(c: sqlite3.Connection, book_id: str, most: int) -> list[Any]:
    """Bank rows still new and not linked: each with its money account's ledger account as gl_id."""
    return c.execute("SELECT f.*, m.account_id AS gl_id FROM fin_feed_txns f JOIN fin_money_accounts m ON "
                     "m.id=f.money_account_id WHERE m.book_id=? AND f.status='new' AND f.pending=0 AND "
                     "f.entry_id IS NULL ORDER BY f.txn_date, f.id LIMIT ?", (book_id, most)).fetchall()


def link_feed(c: sqlite3.Connection, old_entry: str, new_entry: str) -> None:
    """Bank rows that pointed at an entry now point at its replacement."""
    c.execute("UPDATE fin_feed_txns SET entry_id=? WHERE entry_id=?", (new_entry, old_entry))


def unlink_feed(c: sqlite3.Connection, entry_id: str, at: str) -> None:
    """Bank rows that pointed at an entry the provider deleted wait again for a match or a categorization."""
    c.execute("UPDATE fin_feed_txns SET status='new', entry_id=NULL, updated_at=? WHERE entry_id=? AND "
              "status IN ('matched','posted')", (at, entry_id))


def writes_for_feed(c: sqlite3.Connection, feed_id: str) -> list[Any]:
    """Writes out for a bank row not yet confirmed: pending (or claimed for a send), or unknown, which may have landed at
    the provider and so is never sent again."""
    return c.execute("SELECT * FROM fin_outbox WHERE (status='pending' OR (status='failed' AND unknown=1)) "
                     "AND json_extract(payload_json, '$.feed_id')=?", (feed_id,)).fetchall()


def outbox_counts(c: sqlite3.Connection, cid: str) -> dict[str, int]:
    """A connection's writes out by state: pending, failed (refused by the provider), unknown (it cannot tell whether
    the write landed), withheld (held back in rehearsal)."""
    out = {"pending": 0, "failed": 0, "unknown": 0, "withheld": 0}
    for status, unknown, n in c.execute("SELECT status, unknown, COUNT(*) FROM fin_outbox WHERE connection_id=? "
                                        "GROUP BY status, unknown", (cid,)).fetchall():
        if status == "failed" and unknown:
            out["unknown"] += int(n)
        elif status in out:
            out[str(status)] += int(n)
    return out


def cancel_feed_writes(c: sqlite3.Connection, feed_id: str, why: str, at: str) -> None:
    """Withdraw the writes out for a bank row that a provider entry now is (or that was ignored)."""
    c.execute("UPDATE fin_outbox SET status='failed', last_error=?, claimed_at=NULL, updated_at=? WHERE status='pending' "
              "AND claimed_at IS NULL AND json_extract(payload_json, '$.feed_id')=?", (why, at, feed_id))


def writes_waiting(c: sqlite3.Connection, cid: str) -> int:
    """Writes for a connection not yet confirmed or given up on."""
    return int(c.execute("SELECT COUNT(*) FROM fin_outbox WHERE connection_id=? AND status IN ('pending','withheld')",
                         (cid,)).fetchone()[0])


def contact(c: sqlite3.Connection, contact_id: str) -> Any:
    return c.execute("SELECT * FROM fin_contacts WHERE id=?", (contact_id,)).fetchone()


def contact_by_name(c: sqlite3.Connection, book_id: str, name: str) -> Any:
    """The oldest contact of this name: a provider party with no ref yet takes it over rather than doubling it."""
    return c.execute("SELECT * FROM fin_contacts WHERE book_id=? AND name=? COLLATE NOCASE ORDER BY created_at, id "
                     "LIMIT 1", (book_id, name)).fetchone()


def contact_insert(c: sqlite3.Connection, *, book_id: str, contact_id: str, name: str, kind: str,
                   emails: Sequence[str], active: bool, at: str, by: str) -> None:
    c.execute("INSERT INTO fin_contacts(id, book_id, name, kind, emails_json, active, created_at, created_by, updated_at) "
              "VALUES(?,?,?,?,?,?,?,?,?)", (contact_id, book_id, name, kind, json.dumps(list(emails)), int(active), at,
                                           by, at))


def contact_update(c: sqlite3.Connection, contact_id: str, *, name: str, kind: str, emails: Sequence[str],
                   active: bool, at: str) -> None:
    c.execute("UPDATE fin_contacts SET name=?, kind=?, emails_json=?, active=?, updated_at=? WHERE id=?",
              (name, kind, json.dumps(list(emails)), int(active), at, contact_id))


def trial_balances(c: sqlite3.Connection, book_id: str) -> dict[str, int]:
    """The sum of every line on each account (the books' balance per account, base currency minor units)."""
    return {str(r[0]): int(r[1]) for r in c.execute(
        "SELECT account_id, SUM(amount) FROM fin_lines WHERE book_id=? GROUP BY account_id", (book_id,))}


def entry_count(c: sqlite3.Connection, book_id: str) -> int:
    return int(c.execute("SELECT COUNT(*) FROM fin_entries WHERE book_id=?", (book_id,)).fetchone()[0])


def contact_count(c: sqlite3.Connection, book_id: str) -> int:
    return int(c.execute("SELECT COUNT(*) FROM fin_contacts WHERE book_id=?", (book_id,)).fetchone()[0])


def export_entries(c: sqlite3.Connection, book_id: str, after: tuple[str, str] | None, until: str,
                   most: int) -> list[Any]:
    """Entries made up to `until` (the switch to mirror), in the order they were made, after a (created_at, id)
    position: a page of an export. Later entries are written out as they are made."""
    if after is None:
        return c.execute("SELECT * FROM fin_entries WHERE book_id=? AND created_at<=? ORDER BY created_at, id LIMIT ?",
                         (book_id, until, most)).fetchall()
    return c.execute("SELECT * FROM fin_entries WHERE book_id=? AND created_at<=? AND (created_at, id) > (?, ?) "
                     "ORDER BY created_at, id LIMIT ?", (book_id, until, after[0], after[1], most)).fetchall()


def contacts(c: sqlite3.Connection, book_id: str) -> list[Any]:
    return c.execute("SELECT * FROM fin_contacts WHERE book_id=? ORDER BY created_at, id", (book_id,)).fetchall()


def contact_deactivate(c: sqlite3.Connection, contact_id: str, at: str) -> None:
    c.execute("UPDATE fin_contacts SET active=0, updated_at=? WHERE id=?", (at, contact_id))


def synced(c: sqlite3.Connection, cid: str, at: str, error: str) -> None:
    """A pass over a connection's books ended: when, and whether it went through."""
    if error:
        c.execute("UPDATE fin_connections SET last_sync_at=?, last_error=? WHERE id=?", (at, error[:300], cid))
    else:
        c.execute("UPDATE fin_connections SET last_sync_at=?, last_ok_at=?, last_error=NULL WHERE id=?", (at, at, cid))


def last_ok(c: sqlite3.Connection, cid: str) -> str | None:
    row = c.execute("SELECT last_ok_at FROM fin_connections WHERE id=?", (cid,)).fetchone()
    return str(row[0]) if row and row[0] else None


def disconnect(c: sqlite3.Connection, cid: str, at: str) -> None:
    c.execute("UPDATE fin_connections SET status='disconnected', updated_at=? WHERE id=?", (at, cid))


def books_settings(c: sqlite3.Connection, cid: str) -> dict[str, object]:
    """A books connection's own settings: its company, its realm or tenant, and the credential ids of its tokens (the
    secrets stay in the vault; the token expiry times are kept here)."""
    return as_map(_settings(c, cid).get("books"))


def set_books_settings(c: sqlite3.Connection, cid: str, fields: Mapping[str, object]) -> None:
    settings = _settings(c, cid)
    settings["books"] = {**books_settings(c, cid), **fields}
    c.execute("UPDATE fin_connections SET settings_json=? WHERE id=?", (json.dumps(settings, sort_keys=True), cid))


def books_connections(c: sqlite3.Connection, provider: str | None = None) -> list[tuple[str, str, str]]:
    """(id, provider, company id) for each books connection that is not disconnected, optionally of one provider."""
    sql = "SELECT id, provider, settings_json, status FROM fin_connections WHERE purpose='books' AND status!='disconnected'"
    args: list[object] = []
    if provider:
        sql += " AND provider=?"
        args.append(provider)
    out: list[tuple[str, str, str]] = []
    for row in c.execute(sql + " ORDER BY created_at, id", args).fetchall():
        books = as_map(_settings(c, str(row[0])).get("books"))
        out.append((str(row[0]), str(row[1]), str(books.get("company_id") or "")))
    return out


def books_connection_insert(c: sqlite3.Connection, *, cid: str, provider: str, name: str, by: str, at: str) -> None:
    c.execute("INSERT INTO fin_connections(id, provider, purpose, status, created_at, created_by, updated_at, name) "
              "VALUES(?,?,'books','active',?,?,?,?)", (cid, provider, at, by, at, name))


def set_connection_status(c: sqlite3.Connection, cid: str, status: str, error: str, at: str) -> None:
    """A connection's status and why: `reconnect` stops the worker from calling the provider until a person reconnects."""
    c.execute("UPDATE fin_connections SET status=?, last_error=?, updated_at=? WHERE id=?", (status, error, at, cid))


def books_status(c: sqlite3.Connection, cid: str) -> str:
    row = c.execute("SELECT status FROM fin_connections WHERE id=?", (cid,)).fetchone()
    return str(row[0]) if row else ""


def pending_writes(c: sqlite3.Connection, cid: str) -> list[Any]:
    """This connection's writes not answered, oldest first: one of them may already be at the provider (an unknown one
    too, until the provider's record shows up)."""
    return c.execute("SELECT * FROM fin_outbox WHERE connection_id=? AND (status='pending' OR (status='failed' AND "
                     "unknown=1)) ORDER BY created_at, id", (cid,)).fetchall()


def withhold_writes(c: sqlite3.Connection, cid: str, why: str, at: str) -> int:
    """A rehearsal copy's writes to the provider wait as `withheld`: visible in the outbox, never sent. Returns how many."""
    cur = c.execute("UPDATE fin_outbox SET status='withheld', last_error=?, updated_at=? "
                    "WHERE connection_id=? AND status='pending' AND claimed_at IS NULL", (why[:300], at, cid))
    return int(cur.rowcount)


def connection_owner(c: sqlite3.Connection, cid: str) -> str:
    row = c.execute("SELECT created_by FROM fin_connections WHERE id=?", (cid,)).fetchone()
    return str(row[0]) if row else ""
