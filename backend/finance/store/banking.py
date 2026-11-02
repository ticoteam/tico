"""SQL for money in and out (spec §4.3, §4.5, §4.6, §9): connections, money accounts, feed rows, statements and
reconciliations, cards, recipients, payments, the inbox and outbox, gate rules and their counters. Callers pass plain
values; no provider is called from here."""
import json
import sqlite3
from datetime import date, timedelta
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, cast

from .ledger import new_id

TABLES = frozenset({"fin_connections", "fin_money_accounts", "fin_feed_txns", "fin_statements", "fin_reconciliations",
                    "fin_cards", "fin_card_auths", "fin_card_saves", "fin_recipients", "fin_payments", "fin_inbox",
                    "fin_outbox",
                    "fin_rules", "fin_rule_runs", "fin_transfers"})
LIVE_PAYMENT = ("queued", "sending", "awaiting_bank_approval", "submitted", "unknown")


def dump(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def loads(raw: object) -> dict[str, Any]:
    try:
        value = json.loads(str(raw or "{}"))
    except ValueError:
        return {}
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


def loads_list(raw: object) -> list[Any]:
    try:
        value = json.loads(str(raw or "[]"))
    except ValueError:
        return []
    return cast(list[Any], value) if isinstance(value, list) else []


def insert(c: sqlite3.Connection, table: str, row: Mapping[str, object]) -> str:
    """Insert one row (an id is made when the row has none) and return its id."""
    assert table in TABLES
    values = {"id": new_id(), **row}
    cols = sorted(values)
    c.execute(f"INSERT INTO {table}({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
              [values[k] for k in cols])
    return str(values["id"])


def update(c: sqlite3.Connection, table: str, row_id: str, fields: Mapping[str, object],
           where: str = "", args: Sequence[object] = ()) -> int:
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


# ------------------------------------------------------------------------------------------------ connections
def connections(c: sqlite3.Connection, provider: str | None = None) -> list[Any]:
    sql, args = "SELECT * FROM fin_connections", []
    if provider:
        sql, args = sql + " WHERE provider=?", [provider]
    return c.execute(sql + " ORDER BY created_at, id", args).fetchall()


def connections_due(c: sqlite3.Connection, at: str) -> list[Any]:
    """Connections whose next sync is due."""
    return c.execute("SELECT * FROM fin_connections WHERE status IN ('active','error') AND "
                     "(next_sync_at IS NULL OR next_sync_at<=?) ORDER BY next_sync_at", (at,)).fetchall()


def connection_busy(c: sqlite3.Connection, connection_id: str) -> tuple[int, int]:
    """Payments still in flight from this connection's accounts, and outbox calls not yet made or confirmed."""
    paying = c.execute(f"SELECT count(*) FROM fin_payments WHERE status IN ({_marks(LIVE_PAYMENT)}) AND money_account_id "
                       "IN (SELECT id FROM fin_money_accounts WHERE connection_id=?)",
                       (*LIVE_PAYMENT, connection_id)).fetchone()[0]
    calls = c.execute("SELECT count(*) FROM fin_outbox WHERE connection_id=? AND status='pending'",
                      (connection_id,)).fetchone()[0]
    return int(paying), int(calls)


def money_posted(c: sqlite3.Connection, money_id: str) -> int:
    """Feed rows of a money account already in the books."""
    return int(c.execute("SELECT count(*) FROM fin_feed_txns WHERE money_account_id=? AND entry_id IS NOT NULL",
                         (money_id,)).fetchone()[0])


def live_payments(c: sqlite3.Connection, book_id: str) -> list[Any]:
    """Payments still on their way out (what a viewer's due-out totals are summed from, after their view)."""
    return c.execute("SELECT id, money_account_id, account_id, tags_json, amount, obligation FROM fin_payments "
                     f"WHERE book_id=? AND status IN ({_marks(LIVE_PAYMENT)})", (book_id, *LIVE_PAYMENT)).fetchall()


def set_provider_users(c: sqlite3.Connection, connection_id: str, users: Sequence[Mapping[str, object]],
                       at: str) -> None:
    """The provider's users as just read with this connection's token, replacing the last read."""
    c.execute("DELETE FROM fin_provider_users WHERE connection_id=?", (connection_id,))
    for u in users:
        c.execute("INSERT OR REPLACE INTO fin_provider_users(connection_id, provider_user_id, name, email, role, "
                  "updated_at) VALUES(?,?,?,?,?,?)", (connection_id, str(u.get("id") or ""), str(u.get("name") or ""),
                                                      str(u.get("email") or ""), str(u.get("role") or ""), at))


def provider_users(c: sqlite3.Connection) -> list[Any]:
    """Users of every connected (not disconnected) provider account, by name."""
    return c.execute("SELECT u.* FROM fin_provider_users u JOIN fin_connections k ON k.id=u.connection_id "
                     "WHERE k.status<>'disconnected' AND u.provider_user_id<>'' ORDER BY u.name, u.provider_user_id"
                     ).fetchall()


# ------------------------------------------------------------------------------------------------ money accounts
def money_accounts(c: sqlite3.Connection, book_id: str) -> list[Any]:
    return c.execute("SELECT * FROM fin_money_accounts WHERE book_id=? ORDER BY kind, name, id", (book_id,)).fetchall()


def money_account_by_provider(c: sqlite3.Connection, connection_id: str, provider_account_id: str) -> Any:
    return c.execute("SELECT * FROM fin_money_accounts WHERE connection_id=? AND provider_account_id=?",
                     (connection_id, provider_account_id)).fetchone()


def money_account_by_provider_id(c: sqlite3.Connection, book_id: str, provider: str, provider_account_id: str) -> Any:
    """The money account for one provider account, whichever connection holds it now."""
    if not provider_account_id:
        return None
    return c.execute("SELECT * FROM fin_money_accounts WHERE book_id=? AND provider=? AND provider_account_id=? "
                     "ORDER BY created_at LIMIT 1", (book_id, provider, provider_account_id)).fetchone()


def card_counts(c: sqlite3.Connection) -> dict[str, dict[str, int]]:
    """Cards per money account by status: what Sources shows instead of one row per card."""
    out: dict[str, dict[str, int]] = {}
    for r in c.execute("SELECT money_account_id, status, count(*) FROM fin_cards GROUP BY money_account_id, status"):
        out.setdefault(str(r[0] or ""), {})[str(r[1])] = int(r[2])
    return out


def feed_counts(c: sqlite3.Connection) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for r in c.execute("SELECT money_account_id, status, count(*) FROM fin_feed_txns GROUP BY money_account_id, status"):
        out.setdefault(str(r[0]), {})[str(r[1])] = int(r[2])
    return out


# ------------------------------------------------------------------------------------------------ feed rows
def feed_row(c: sqlite3.Connection, money_account_id: str, provider_id: str) -> Any:
    return c.execute("SELECT * FROM fin_feed_txns WHERE money_account_id=? AND provider_id=?",
                     (money_account_id, provider_id)).fetchone()


def feed_by_entry(c: sqlite3.Connection, entry_ids: Sequence[str]) -> dict[str, Any]:
    if not entry_ids:
        return {}
    return {str(r["entry_id"]): r for r in c.execute(
        f"SELECT * FROM fin_feed_txns WHERE entry_id IN ({_marks(entry_ids)})", list(entry_ids))}


def feed_by_request(c: sqlite3.Connection, request_id: str) -> Any:
    return c.execute("SELECT * FROM fin_feed_txns WHERE request_id=? ORDER BY created_at LIMIT 1",
                     (request_id,)).fetchone()


def feed_page(c: sqlite3.Connection, *, before: tuple[str, str] | None, most: int, money_account_ids: Sequence[str],
              status: str = "", start: str = "", end: str = "", q: str = "", card_ids: Sequence[str] | None = None,
              entry_ids: Sequence[str] | None = None) -> list[Any]:
    """Feed rows newest first, keyset-paged on (txn_date, id), on these money accounts (or cards, or entries)."""
    where: list[str] = ["1=1"]
    args: list[object] = []
    scopes: list[str] = []
    if money_account_ids:
        scopes.append(f"money_account_id IN ({_marks(money_account_ids)})")
        args += list(money_account_ids)
    if card_ids:
        scopes.append(f"card_id IN ({_marks(card_ids)})")
        args += list(card_ids)
    if entry_ids:
        scopes.append(f"entry_id IN ({_marks(entry_ids)})")
        args += list(entry_ids)
    if not scopes:
        return []
    where.append("(" + " OR ".join(scopes) + ")")
    if status == "pending":
        where.append("pending=1")
    elif status:
        where.append("status=?")
        args.append(status)
    if start:
        where.append("txn_date>=?")
        args.append(start)
    if end:
        where.append("txn_date<=?")
        args.append(end)
    if q:
        where.append("(counterparty LIKE ? ESCAPE '\\' OR description LIKE ? ESCAPE '\\')")
        args += [like(q)] * 2
    if before:
        where.append("(txn_date<? OR (txn_date=? AND id<?))")
        args += [before[0], before[0], before[1]]
    return c.execute(f"SELECT * FROM fin_feed_txns WHERE {' AND '.join(where)} ORDER BY txn_date DESC, id DESC LIMIT ?",
                     [*args, most]).fetchall()


def like(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def feed_for_match(c: sqlite3.Connection, money_account_id: str, amount: int, start: str, end: str) -> list[Any]:
    """Unposted rows on a money account with this amount in a date window: a transfer's other side."""
    return c.execute("SELECT * FROM fin_feed_txns WHERE money_account_id=? AND amount=? AND txn_date BETWEEN ? AND ? "
                     "AND status='new' AND pending=0 ORDER BY txn_date, id", (money_account_id, amount, start, end)
                     ).fetchall()


def transfer_candidates(c: sqlite3.Connection, money_account_ids: Sequence[str], amount: int, start: str,
                        end: str) -> list[Any]:
    """Settled, unreconciled rows on these money accounts with this amount in a window: a transfer's other side."""
    if not money_account_ids:
        return []
    return c.execute(f"SELECT * FROM fin_feed_txns WHERE money_account_id IN ({_marks(money_account_ids)}) AND "
                     "amount=? AND txn_date BETWEEN ? AND ? AND status IN ('new','posted') AND pending=0 AND "
                     "reconciliation_id IS NULL ORDER BY txn_date, id", [*money_account_ids, amount, start, end]
                     ).fetchall()


def rows_of_entry(c: sqlite3.Connection, entry_id: str) -> list[Any]:
    return c.execute("SELECT * FROM fin_feed_txns WHERE entry_id=? ORDER BY id", (entry_id,)).fetchall()


def feed_window(c: sqlite3.Connection, money_account_id: str, through: str) -> list[Any]:
    """Settled rows on or before `through` that are in the books (posted or matched to an entry) and that no done
    reconciliation cleared yet. A `new` row is not in the books, so it can't be cleared: it stays outstanding. A covered
    row (spec §4.1) is history the raw totals carry, so it counts without an entry."""
    return c.execute("SELECT * FROM fin_feed_txns WHERE money_account_id=? AND txn_date<=? AND pending=0 AND "
                     "status NOT IN ('ignored','new') AND (entry_id IS NOT NULL OR status='covered') AND "
                     "reconciliation_id IS NULL ORDER BY txn_date, id",
                     (money_account_id, through)).fetchall()


def open_feed_rows(c: sqlite3.Connection, book_id: str) -> list[Any]:
    """A book's settled rows that no entry holds (new or covered): what a raw-totals month can cover or uncover."""
    return c.execute("SELECT f.* FROM fin_feed_txns f JOIN fin_money_accounts m ON m.id=f.money_account_id WHERE "
                     "m.book_id=? AND f.status IN ('new','covered') AND f.pending=0 AND f.entry_id IS NULL "
                     "ORDER BY f.txn_date, f.id", (book_id,)).fetchall()


def row_effects(c: sqlite3.Connection, book_id: str, money_account_id: str, gl_account_id: str,
                through: str) -> list[Any]:
    """This money account's rows no done reconciliation cleared, each with what its entries moved on the account's GL
    line: `through`, every entry ever posted for it (originals, their reversals, replacements, a shared transfer)
    dated through that day; `live`, its current posted entry, whatever its date. A Stripe row in a day's summary shares
    its entry with the day's other rows, so it counts its own amount (its net) rather than the whole entry's line. A row
    that left its summary keeps that summary's amount through the days before the summary was reversed (`summary_entry`),
    since the closed period it was booked in still holds it."""
    summary = "json_extract(f.raw_json, '$.summary')=1"
    left = "json_extract(f.raw_json, '$.summary_entry')"
    left_share = (f"CASE WHEN {left} IS NOT NULL AND EXISTS (SELECT 1 FROM fin_entries e WHERE e.id={left} AND "
                  f"e.entry_date<=:through) AND NOT EXISTS (SELECT 1 FROM fin_entries r WHERE r.reverses_id={left} AND "
                  "r.status='posted' AND r.entry_date<=:through) THEN f.amount ELSE 0 END")
    return c.execute(
        f"SELECT f.id, (CASE WHEN {summary} THEN CASE WHEN EXISTS (SELECT 1 FROM fin_entries e WHERE e.id=f.entry_id "
        "AND e.status='posted' AND e.entry_date<=:through) THEN f.amount ELSE 0 END ELSE "
        "COALESCE((SELECT SUM(l.amount) FROM fin_entries e JOIN fin_lines l ON l.entry_id=e.id AND "
        "l.account_id=:gl WHERE e.book_id=:book AND e.source IN ('bank','card') AND e.entry_date<=:through AND "
        "(e.source_id=f.id OR substr(e.source_id, 1, length(f.id) + 1)=f.id || ':' OR (e.source_id LIKE 'transfer:%' "
        "AND instr(e.source_id, f.id)>0))), 0) END) + "
        f"{left_share} AS through_amount, "
        f"CASE WHEN {summary} THEN CASE WHEN EXISTS (SELECT 1 FROM fin_entries e WHERE e.id=f.entry_id "
        "AND e.status='posted') THEN f.amount ELSE 0 END ELSE "
        "COALESCE((SELECT SUM(l.amount) FROM fin_lines l JOIN fin_entries e ON e.id=l.entry_id AND e.status='posted' "
        "WHERE l.entry_id=f.entry_id AND l.account_id=:gl), 0) END AS live_amount "
        "FROM fin_feed_txns f WHERE f.money_account_id=:money AND f.reconciliation_id IS NULL",
        {"gl": gl_account_id, "book": book_id, "through": through, "money": money_account_id}).fetchall()


def settled_total(c: sqlite3.Connection, money_account_id: str) -> tuple[int, str]:
    """What the provider's current balance holds of this account's feed: the total of its settled rows (a cancelled,
    failed, blocked or reversed one moved nothing) and the earliest such row's day ("" with none)."""
    row = c.execute("SELECT COALESCE(SUM(amount), 0), COALESCE(MIN(txn_date), '') FROM fin_feed_txns WHERE "
                    "money_account_id=? AND pending=0 AND COALESCE(provider_status, '') NOT IN "
                    "('cancelled','failed','blocked','reversed')", (money_account_id,)).fetchone()
    return int(row[0]), str(row[1])


def opening_entry(c: sqlite3.Connection, book_id: str, money_account_id: str) -> Any:
    """This money account's live opening-balance entry (source sync, source id opening:<id>[:<when>]), if any."""
    return c.execute("SELECT * FROM fin_entries WHERE book_id=? AND source='sync' AND status='posted' AND "
                     "reverses_id IS NULL AND (source_id=? OR source_id LIKE ?) ORDER BY created_at DESC, id DESC "
                     "LIMIT 1", (book_id, f"opening:{money_account_id}", f"opening:{money_account_id}:%")).fetchone()


def feed_outstanding(c: sqlite3.Connection, money_account_id: str, through: str) -> tuple[int, int]:
    """Settled rows on or before `through` the books don't have yet (`new`): how many, and their total."""
    row = c.execute("SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM fin_feed_txns WHERE money_account_id=? AND "
                    "txn_date<=? AND pending=0 AND status='new' AND reconciliation_id IS NULL",
                    (money_account_id, through)).fetchone()
    return int(row[0]), int(row[1])


def feed_rows(c: sqlite3.Connection, ids: Sequence[str]) -> dict[str, Any]:
    if not ids:
        return {}
    return {str(r["id"]): r for r in c.execute(f"SELECT * FROM fin_feed_txns WHERE id IN ({_marks(ids)})", list(ids))}


def feed_since(c: sqlite3.Connection, since: str, most: int) -> list[Any]:
    return c.execute("SELECT * FROM fin_feed_txns WHERE txn_date>=? ORDER BY txn_date DESC, id DESC LIMIT ?",
                     (since, most)).fetchall()


def cleared_total(c: sqlite3.Connection, money_account_id: str) -> int:
    """The sum of every row a done reconciliation cleared: where the next one starts."""
    return int(c.execute("SELECT COALESCE(SUM(amount),0) FROM fin_feed_txns WHERE money_account_id=? AND "
                         "reconciliation_id IN (SELECT id FROM fin_reconciliations WHERE status='done' AND "
                         "undone_at IS NULL)", (money_account_id,)).fetchone()[0])


def set_cleared(c: sqlite3.Connection, ids: Sequence[str], reconciliation_id: str | None, at: str) -> int:
    if not ids:
        return 0
    return c.execute(f"UPDATE fin_feed_txns SET reconciliation_id=?, updated_at=? WHERE id IN ({_marks(ids)})",
                     [reconciliation_id, at, *ids]).rowcount


# ------------------------------------------------------------------------------------------------ reconciliations
def reconciliations(c: sqlite3.Connection, money_account_ids: Sequence[str], most: int) -> list[Any]:
    if not money_account_ids:
        return []
    return c.execute(f"SELECT * FROM fin_reconciliations WHERE money_account_id IN ({_marks(money_account_ids)}) "
                     "ORDER BY statement_end DESC, created_at DESC LIMIT ?", [*money_account_ids, most]).fetchall()


def last_reconciliation(c: sqlite3.Connection, money_account_id: str) -> Any:
    return c.execute("SELECT * FROM fin_reconciliations WHERE money_account_id=? AND status='done' AND undone_at IS NULL "
                     "ORDER BY statement_end DESC LIMIT 1", (money_account_id,)).fetchone()


def first_feed_day(c: sqlite3.Connection, money_account_id: str) -> str:
    """The date of a money account's first settled feed row ("" when it has none)."""
    row = c.execute("SELECT MIN(txn_date) FROM fin_feed_txns WHERE money_account_id=? AND pending=0",
                    (money_account_id,)).fetchone()
    return str(row[0] or "") if row else ""


def locked_through(c: sqlite3.Connection, money_account_id: str) -> str:
    row = last_reconciliation(c, money_account_id)
    return str(row["statement_end"]) if row else ""


def statement(c: sqlite3.Connection, money_account_id: str, end_date: str) -> Any:
    return c.execute("SELECT * FROM fin_statements WHERE money_account_id=? AND end_date=?",
                     (money_account_id, end_date)).fetchone()


def upsert_statement(c: sqlite3.Connection, *, money_account_id: str, provider_id: str, start: str, end: str,
                     starting: int | None, ending: int, currency: str, source: str, by: str, at: str) -> str:
    row = statement(c, money_account_id, end)
    if row is not None:
        c.execute("UPDATE fin_statements SET ending_balance=?, starting_balance=?, start_date=?, provider_id=? "
                  "WHERE id=?", (ending, starting, start, provider_id, row["id"]))
        return str(row["id"])
    return _new_statement(c, money_account_id, provider_id, start, end, starting, ending, currency, source, by, at)


def _new_statement(c: sqlite3.Connection, money_account_id: str, provider_id: str, start: str, end: str,
                   starting: int | None, ending: int, currency: str, source: str, by: str, at: str) -> str:
    sid = new_id()
    c.execute("INSERT INTO fin_statements(id, money_account_id, provider_id, start_date, end_date, starting_balance, "
              "ending_balance, currency, source, created_at, created_by) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
              (sid, money_account_id, provider_id, start, end, starting, ending, currency, source, at, by))
    return sid


def statements_for(c: sqlite3.Connection, money_account_ids: Sequence[str], most: int) -> list[Any]:
    if not money_account_ids:
        return []
    return c.execute(f"SELECT * FROM fin_statements WHERE money_account_id IN ({_marks(money_account_ids)}) "
                     "ORDER BY end_date DESC LIMIT ?", [*money_account_ids, most]).fetchall()


# ------------------------------------------------------------------------------------------------ cards
def cards(c: sqlite3.Connection, *, status: str = "", holder: str = "", before: str = "", most: int = 200) -> list[Any]:
    where: list[str] = ["1=1"]
    args: list[object] = []
    if status:
        where.append("status=?")
        args.append(status)
    if holder:
        where.append("holder=?")
        args.append(holder)
    if before:
        where.append("id>?")
        args.append(before)
    return c.execute(f"SELECT * FROM fin_cards WHERE {' AND '.join(where)} ORDER BY id LIMIT ?", [*args, most]).fetchall()


def card_by_provider(c: sqlite3.Connection, provider: str, provider_card_id: str) -> Any:
    return c.execute("SELECT * FROM fin_cards WHERE provider=? AND provider_card_id=?",
                     (provider, provider_card_id)).fetchone()


def cards_on_budgets(c: sqlite3.Connection, budget_ids: Sequence[str]) -> list[Any]:
    if not budget_ids:
        return []
    return c.execute(f"SELECT * FROM fin_cards WHERE budget_id IN ({_marks(budget_ids)}) AND status='active'",
                     list(budget_ids)).fetchall()


def budget_frozen_cards(c: sqlite3.Connection, budget_ids: Sequence[str] | None) -> list[Any]:
    """Cards a budget's cutoff froze and no person has claimed since, on these budgets (every budget when None)."""
    if budget_ids is not None and not budget_ids:
        return []
    scope = "" if budget_ids is None else f" AND budget_id IN ({_marks(budget_ids)})"
    sql = "SELECT * FROM fin_cards WHERE status='frozen' AND freeze_by='budget'" + scope
    return c.execute(sql + " ORDER BY created_at, id", [] if budget_ids is None else list(budget_ids)).fetchall()


def card_save_rows(c: sqlite3.Connection, card_id: str, vendor_keys: Sequence[str]) -> list[Any]:
    """A card's saved-card records at any of a vendor's keys (its contact id and the names it went by), active or
    removed, oldest first."""
    if not vendor_keys:
        return []
    return c.execute(f"SELECT * FROM fin_card_saves WHERE card_id=? AND vendor_key IN ({_marks(vendor_keys)}) "
                     "ORDER BY saved_at, id", [card_id, *vendor_keys]).fetchall()


def card_saves(c: sqlite3.Connection, card_ids: Sequence[str]) -> list[Any]:
    """The vendor accounts these cards are saved on now, oldest first."""
    if not card_ids:
        return []
    return c.execute(f"SELECT * FROM fin_card_saves WHERE active=1 AND card_id IN ({_marks(card_ids)}) "
                     "ORDER BY saved_at, id", list(card_ids)).fetchall()


def cards_saved_at(c: sqlite3.Connection, book_id: str, vendor_keys: Sequence[str]) -> set[str]:
    """The ids of the cards saved now on any of these vendor keys (a vendor's keys: its contact id and names)."""
    if not vendor_keys:
        return set()
    rows = c.execute(f"SELECT card_id FROM fin_card_saves WHERE book_id=? AND active=1 AND vendor_key IN "
                     f"({_marks(vendor_keys)})", [book_id, *vendor_keys]).fetchall()
    return {str(r[0]) for r in rows}


def cards_unsynced(c: sqlite3.Connection, most: int) -> list[Any]:
    return c.execute("SELECT * FROM fin_cards WHERE sync_status='pending' ORDER BY updated_at LIMIT ?",
                     (most,)).fetchall()


# ------------------------------------------------------------------------------------------------ recipients, payments
def recipient_current(c: sqlite3.Connection, recipient_id: str) -> Any:
    return c.execute("SELECT * FROM fin_recipients WHERE id=?", (recipient_id,)).fetchone()


def recipients(c: sqlite3.Connection, most: int) -> list[Any]:
    return c.execute("SELECT * FROM fin_recipients WHERE status='active' ORDER BY name, id LIMIT ?", (most,)).fetchall()


def live_payment(c: sqlite3.Connection, obligation: str) -> Any:
    return c.execute(f"SELECT * FROM fin_payments WHERE obligation=? AND status IN ({_marks(LIVE_PAYMENT)})",
                     (obligation, *LIVE_PAYMENT)).fetchone()


def last_payments(c: sqlite3.Connection, obligations: Sequence[str]) -> dict[str, Any]:
    """Each obligation's latest payment, live or ended (what a bill shows of its payment)."""
    if not obligations:
        return {}
    out: dict[str, Any] = {}
    for r in c.execute("SELECT obligation, id, status, amount, last_error, created_at FROM fin_payments "
                       f"WHERE obligation IN ({_marks(obligations)}) ORDER BY created_at, id", list(obligations)):
        out[str(r["obligation"])] = r
    return out


def payment_by_key(c: sqlite3.Connection, key: str) -> Any:
    return c.execute("SELECT * FROM fin_payments WHERE idempotency_key=?", (key,)).fetchone()


def outbox_by_key(c: sqlite3.Connection, key: str) -> Any:
    """The outbox write under this idempotency key (a transfer's is `transfer-<id>`)."""
    return c.execute("SELECT * FROM fin_outbox WHERE idempotency_key=?", (key,)).fetchone()


TRANSFER_WINDOW_DAYS = 5     # a transfer's two legs post on its submit day or within this many days after it


def _window(submitted_at: object) -> tuple[str, str]:
    """The first and last day a leg of a transfer submitted at `submitted_at` may be dated."""
    day = date.fromisoformat(str(submitted_at)[:10])
    return day.isoformat(), (day + timedelta(days=TRANSFER_WINDOW_DAYS)).isoformat()


def _leg(c: sqlite3.Connection, feed: str) -> Any:
    return c.execute("SELECT id, money_account_id, amount, txn_date, provider_id, request_id FROM fin_feed_txns "
                     "WHERE id=?", (feed,)).fetchone()


def settle_pair(c: sqlite3.Connection, out_feed: str, in_feed: str, at: str) -> str | None:
    """Link the two posted legs of a move to the Finance transfer they are: one between the same accounts for the same
    amount that still waits on them (submitted, or unknown: its legs may be what made it real), with both legs dated
    from its submit day through TRANSFER_WINDOW_DAYS on, so an older move between the same accounts never settles it.
    A transfer whose Mercury id a leg carries wins; else the oldest that fits. Its id, or None (a move made outside
    Finance)."""
    out, inn = _leg(c, out_feed), _leg(c, in_feed)
    if out is None or inn is None or int(out["amount"]) >= 0 or int(inn["amount"]) != -int(out["amount"]):
        return None
    ids = {str(x) for x in (out["provider_id"], out["request_id"], inn["provider_id"], inn["request_id"]) if x}
    best = None
    for t in c.execute("SELECT id, provider_txn_id, submitted_at FROM fin_transfers WHERE status IN ('submitted',"
                       "'unknown') AND from_money_account_id=? AND to_money_account_id=? AND amount=? AND "
                       "submitted_at IS NOT NULL ORDER BY created_at, id",
                       (str(out["money_account_id"]), str(inn["money_account_id"]), -int(out["amount"]))).fetchall():
        first, last = _window(t["submitted_at"])
        if not all(first <= str(leg["txn_date"])[:10] <= last for leg in (out, inn)):
            continue
        if t["provider_txn_id"] and str(t["provider_txn_id"]) in ids:
            best = t
            break
        if best is None:
            best = t
    if best is None:
        return None
    _settle_transfer(c, str(best["id"]), out_feed, in_feed, at)
    return str(best["id"])


def settle_legs(c: sqlite3.Connection, transfer_id: str, at: str, kinds: Sequence[str]) -> bool:
    """Link a transfer to its legs when both are already posted and not yet linked (the feed came first), with the same
    dates and preference as settle_pair."""
    t = c.execute("SELECT from_money_account_id, to_money_account_id, amount, status, submitted_at, provider_txn_id "
                  "FROM fin_transfers WHERE id=?", (transfer_id,)).fetchone()
    if t is None or str(t["status"]) not in ("submitted", "unknown") or t["submitted_at"] is None:
        return False
    first, last = _window(t["submitted_at"])
    amount = int(t["amount"])
    pid = str(t["provider_txn_id"] or "")

    def leg(money: object, value: int) -> str | None:
        found = c.execute(
            f"SELECT f.id FROM fin_feed_txns f WHERE f.money_account_id=? AND f.amount=? AND f.kind IN ({_marks(kinds)}) "
            "AND f.status='posted' AND substr(f.txn_date,1,10)>=? AND substr(f.txn_date,1,10)<=? AND NOT EXISTS "
            "(SELECT 1 FROM fin_transfers x WHERE x.from_feed_id=f.id OR x.to_feed_id=f.id) "
            "ORDER BY CASE WHEN f.provider_id=? OR f.request_id=? THEN 0 ELSE 1 END, f.txn_date, f.id LIMIT 1",
            [str(money), value, *kinds, first, last, pid, pid]).fetchone()
        return str(found[0]) if found else None
    out = leg(t["from_money_account_id"], -amount)
    inn = leg(t["to_money_account_id"], amount)
    if out is None or inn is None:
        return False
    _settle_transfer(c, transfer_id, out, inn, at)
    return True


def unlink_transfers(c: sqlite3.Connection, feed_id: str, at: str) -> None:
    """A leg went back to waiting (its entry was reversed): a transfer it settled waits for both legs again."""
    c.execute("UPDATE fin_transfers SET status='submitted', from_feed_id=NULL, to_feed_id=NULL, settled_at=NULL, "
              "updated_at=? WHERE status='posted' AND (from_feed_id=? OR to_feed_id=?)", (at, feed_id, feed_id))


def _settle_transfer(c: sqlite3.Connection, transfer_id: str, out_feed: str, in_feed: str, at: str) -> None:
    update(c, "fin_transfers", transfer_id, {"status": "posted", "from_feed_id": out_feed, "to_feed_id": in_feed,
                                             "settled_at": at, "updated_at": at})


def payment_by_request(c: sqlite3.Connection, request_id: str) -> Any:
    return c.execute("SELECT * FROM fin_payments WHERE provider_request_id=?", (request_id,)).fetchone()


def direct_payment(c: sqlite3.Connection, transaction_id: str) -> Any:
    """The direct send whose Mercury transaction is this bank row (a direct send has no approval request)."""
    return c.execute("SELECT * FROM fin_payments WHERE mode='direct' AND provider_request_id=?",
                     (transaction_id,)).fetchone()


def payments_by_txn(c: sqlite3.Connection, feed_ids: Sequence[str]) -> list[Any]:
    """Payments whose bank row is one of these (provider_txn_id: set when it settled, by request or by its note)."""
    if not feed_ids:
        return []
    return c.execute(f"SELECT * FROM fin_payments WHERE provider_txn_id IN ({_marks(feed_ids)})",
                     tuple(feed_ids)).fetchall()


def payment_debit(c: sqlite3.Connection, money_account_id: str, request_id: str) -> Any:
    """The settled debit a payment request became, if a sync already took it in."""
    return c.execute("SELECT * FROM fin_feed_txns WHERE money_account_id=? AND request_id=? AND pending=0 AND amount<0 "
                     "ORDER BY created_at LIMIT 1", (money_account_id, request_id)).fetchone()


def payments_unlinked(c: sqlite3.Connection, money_account_id: str, amount: int) -> list[Any]:
    """Live payments from this account for this amount whose Mercury request is not known yet (a lost send answer)."""
    return c.execute("SELECT * FROM fin_payments WHERE money_account_id=? AND amount=? AND COALESCE(provider_request_id, '')='' "
                     "AND status IN ('unknown','sending') ORDER BY created_at", (money_account_id, amount)).fetchall()


def unknown_transfers(c: sqlite3.Connection, connection_id: str) -> list[Any]:
    """Transfers whose call may have landed (unknown), from that connection's accounts: looked for by their key."""
    return c.execute("SELECT * FROM fin_transfers WHERE status='unknown' AND from_money_account_id IN "
                     "(SELECT id FROM fin_money_accounts WHERE connection_id=?) ORDER BY created_at",
                     (connection_id,)).fetchall()


def payments_due(c: sqlite3.Connection, at: str, most: int, connection_id: str | None = None) -> list[Any]:
    """Payments the worker owes a call: to send (queued), to resolve (unknown, or `sending` left by a crash), or to
    poll (waiting on the bank); with `connection_id`, only those paid from that connection's accounts."""
    scope = " AND money_account_id IN (SELECT id FROM fin_money_accounts WHERE connection_id=?)" if connection_id \
        else ""
    return c.execute("SELECT * FROM fin_payments WHERE status IN ('queued','sending','unknown','awaiting_bank_approval',"
                     f"'submitted') AND (next_try_at IS NULL OR next_try_at<=?){scope} ORDER BY next_try_at, "
                     "created_at LIMIT ?", (at, *([connection_id] if connection_id else []), most)).fetchall()


def payments_poll_now(c: sqlite3.Connection, connection_id: str, at: str) -> int:
    """Make this connection's payments waiting on the bank due for a poll now (Sync now): a decision made in Mercury
    lands on the sync, not on the next poll. How many."""
    return c.execute("UPDATE fin_payments SET next_try_at=? WHERE status IN ('awaiting_bank_approval','submitted') AND "
                     "provider_request_id IS NOT NULL AND provider_request_id<>'' AND (next_try_at IS NULL OR "
                     "next_try_at>?) AND money_account_id IN (SELECT id FROM fin_money_accounts WHERE connection_id=?)",
                     (at, at, connection_id)).rowcount


def payment_claim(c: sqlite3.Connection, payment_id: str, at: str, stuck_at: str) -> bool:
    """Take a queued payment for one send: only one worker wins, and a cancel can no longer take it back. If nothing
    records an answer by `stuck_at`, the worker finds it again as cut off."""
    return c.execute("UPDATE fin_payments SET status='sending', updated_at=?, next_try_at=? WHERE id=? AND "
                     "status='queued'", (at, stuck_at, payment_id)).rowcount == 1


def payments(c: sqlite3.Connection, *, status: str = "", before: str = "", most: int = 100) -> list[Any]:
    where: list[str] = ["1=1"]
    args: list[object] = []
    if status:
        where.append("status=?")
        args.append(status)
    if before:
        where.append("created_at<?")
        args.append(before)
    return c.execute(f"SELECT * FROM fin_payments WHERE {' AND '.join(where)} ORDER BY created_at DESC LIMIT ?",
                     [*args, most]).fetchall()


def transfers(c: sqlite3.Connection, money_account_ids: Sequence[str], *, before: str = "", most: int = 100) -> list[Any]:
    """Internal transfers between two of these money accounts, newest first."""
    if not money_account_ids:
        return []
    marks = ",".join("?" * len(money_account_ids))
    where = f"from_money_account_id IN ({marks}) AND to_money_account_id IN ({marks})"
    args: list[object] = [*money_account_ids, *money_account_ids]
    if before:
        where += " AND created_at<?"
        args.append(before)
    return c.execute(f"SELECT * FROM fin_transfers WHERE {where} ORDER BY created_at DESC, id DESC LIMIT ?",
                     [*args, most]).fetchall()


# ------------------------------------------------------------------------------------------------ inbox and outbox
def inbox_add(c: sqlite3.Connection, *, connection_id: str | None, provider: str, event_id: str,
              payload: Mapping[str, object], at: str) -> bool:
    """Keep a received event once: False when the same provider event was kept before (a replay)."""
    return c.execute("INSERT OR IGNORE INTO fin_inbox(id, connection_id, provider, provider_event_id, payload_json, "
                     "status, created_at, created_by) VALUES(?,?,?,?,?,'new',?,'system:finance')",
                     (new_id(), connection_id, provider, event_id, dump(payload), at)).rowcount == 1


def inbox_waiting(c: sqlite3.Connection, most: int, connection_id: str | None = None, at: str | None = None
                  ) -> list[Any]:
    """Events to take in: with `connection_id`, that connection's own and those no connection was named for that it
    has not tried yet; due now when `at` is given."""
    where, args = ["status='new'"], list[object]()
    if connection_id:
        where.append("(connection_id=? OR (connection_id IS NULL AND NOT EXISTS (SELECT 1 FROM json_each(tried_json) "
                     "WHERE value=?)))")
        args += [connection_id, connection_id]
    if at:
        where.append("(next_try_at IS NULL OR next_try_at<=?)")
        args.append(at)
    return c.execute(f"SELECT * FROM fin_inbox WHERE {' AND '.join(where)} ORDER BY created_at, id LIMIT ?",
                     (*args, most)).fetchall()


def inbox_later(c: sqlite3.Connection, inbox_id: str, *, attempts: int, next_try_at: str | None,
                tried: Sequence[str]) -> None:
    c.execute("UPDATE fin_inbox SET attempts=?, next_try_at=?, tried_json=? WHERE id=?",
              (attempts, next_try_at, dump(list(tried)), inbox_id))


def inbox_done(c: sqlite3.Connection, inbox_id: str, status: str, at: str) -> None:
    c.execute("UPDATE fin_inbox SET status=?, processed_at=? WHERE id=?", (status, at, inbox_id))


def outbox_add(c: sqlite3.Connection, *, connection_id: str | None, action_id: str, op: str,
               payload: Mapping[str, object], key: str, by: str, at: str) -> str:
    """One write owed to a provider under one idempotency key: the same key is never queued twice."""
    row = c.execute("SELECT id FROM fin_outbox WHERE idempotency_key=?", (key,)).fetchone()
    if row is not None:
        return str(row[0])
    return insert(c, "fin_outbox", {"connection_id": connection_id, "action_id": action_id, "op": op,
                                    "payload_json": dump(payload), "idempotency_key": key, "attempts": 0,
                                    "status": "pending", "created_at": at, "created_by": by, "updated_at": at})


# A record's update waits behind an older update of the same record that is still pending (out at the provider, or to be
# tried again): the updates of one record reach the provider in the order they were made, so a retry never sends a
# change back over a later one.
RECORD_UPDATE_OPS = ("item.update", "location.update")
IN_ORDER = ("NOT (op IN ('item.update','location.update') AND EXISTS (SELECT 1 FROM fin_outbox o WHERE "
            "o.status='pending' AND o.op=fin_outbox.op AND o.rowid<fin_outbox.rowid AND "
            "json_extract(o.payload_json, '$.local_id')=json_extract(fin_outbox.payload_json, '$.local_id')))")


def outbox_due(c: sqlite3.Connection, at: str, most: int, connection_id: str | None = None) -> list[Any]:
    """Unclaimed writes due now (with `connection_id`, that connection's only), oldest first."""
    scope = " AND connection_id=?" if connection_id else ""
    return c.execute("SELECT * FROM fin_outbox WHERE status='pending' AND claimed_at IS NULL AND (next_try_at IS NULL "
                     f"OR next_try_at<=?) AND {IN_ORDER}{scope} ORDER BY created_at LIMIT ?",
                     (at, *([connection_id] if connection_id else []), most)).fetchall()


def record_write_open(c: sqlite3.Connection, op: str, local_id: str) -> bool:
    """Whether a shared record's write is still to reach the provider or may have reached it: held, or unknown. The
    write names its record by the local id in its payload."""
    for row in c.execute("SELECT payload_json FROM fin_outbox WHERE op=? AND "
                         "NOT (status='sent' OR (status='failed' AND unknown=0)) AND instr(payload_json, ?) > 0",
                         (op, local_id)):
        if loads(row[0]).get("local_id") == local_id:
            return True
    return False


def record_write_pending(c: sqlite3.Connection, op: str, local_id: str) -> Any:
    """The newest write of this op for a shared record that is still pending (waiting, claimed, or to be tried again),
    or None. A change made to the record joins that write while it still waits to be claimed."""
    for row in c.execute("SELECT * FROM fin_outbox WHERE op=? AND status='pending' AND instr(payload_json, ?) > 0 "
                         "ORDER BY rowid DESC", (op, local_id)):
        if loads(row["payload_json"]).get("local_id") == local_id:
            return row
    return None


def outbox_amend(c: sqlite3.Connection, outbox_id: str, payload: Mapping[str, object], at: str) -> bool:
    """Replace a write's payload while it has never been attempted and waits to be claimed, so a change that joins it goes
    out with it. False (and nothing changed) once it is claimed, attempted, sent, or refused: a write the provider may
    have received keeps its content under its idempotency key, and a later change queues a write of its own behind it."""
    return c.execute("UPDATE fin_outbox SET payload_json=?, updated_at=? WHERE id=? AND status='pending' AND "
                     "claimed_at IS NULL AND attempts=0", (dump(payload), at, outbox_id)).rowcount == 1


def outbox_repoint(c: sqlite3.Connection, local_id: str, record_id: str) -> None:
    """Name a record by the row it is an alias of in every write not yet sent, so a write queued under the alias sends
    what a write queued under the row would."""
    for row in c.execute("SELECT id, payload_json FROM fin_outbox WHERE status!='sent' AND instr(payload_json, ?) > 0",
                         (local_id,)).fetchall():
        body = loads(row[1])
        fixed = _named(body, local_id, record_id)
        if fixed != body:
            c.execute("UPDATE fin_outbox SET payload_json=? WHERE id=?", (dump(fixed), row[0]))


def _named(value: Any, old: str, new: str) -> Any:
    """The value with each string equal to `old` replaced by `new`, in any nested object or list."""
    if isinstance(value, str):
        return new if value == old else value
    if isinstance(value, dict):
        return {k: _named(v, old, new) for k, v in cast(dict[str, Any], value).items()}
    if isinstance(value, list):
        return [_named(v, old, new) for v in cast(list[Any], value)]
    return value


def outbox_claim(c: sqlite3.Connection, outbox_id: str, at: str) -> bool:
    """Take one write for one call: only one worker wins it. A card's writes go one at a time across workers: a write
    for a card whose other write is at Mercury right now waits until that one is recorded. A record's update is taken
    only once the older updates of its record are recorded (IN_ORDER)."""
    return c.execute(
        "UPDATE fin_outbox SET claimed_at=?, updated_at=? WHERE id=? AND status='pending' AND claimed_at IS NULL AND "
        f"{IN_ORDER} AND (json_extract(payload_json, '$.card_id') IS NULL OR NOT EXISTS "
        "(SELECT 1 FROM fin_outbox o WHERE "
        "o.status='pending' AND o.claimed_at IS NOT NULL AND o.id<>fin_outbox.id AND "
        "json_extract(o.payload_json, '$.card_id')=json_extract(fin_outbox.payload_json, '$.card_id')))",
        (at, at, outbox_id)).rowcount == 1


def card_sync_waiting(c: sqlite3.Connection, card_id: str) -> bool:
    """A card_sync for this card not yet claimed: it will read the card's latest state when sent."""
    return c.execute("SELECT 1 FROM fin_outbox WHERE op='card_sync' AND status='pending' AND claimed_at IS NULL AND "
                     "json_extract(payload_json, '$.card_id')=? LIMIT 1", (card_id,)).fetchone() is not None


def card_create_claimed(c: sqlite3.Connection, card_id: str) -> bool:
    """This card's creation is at Mercury right now (claimed, not recorded)."""
    return c.execute("SELECT 1 FROM fin_outbox WHERE op='card_create' AND status='pending' AND claimed_at IS NOT NULL "
                     "AND json_extract(payload_json, '$.card_id')=? LIMIT 1", (card_id,)).fetchone() is not None


def card_create_waiting(c: sqlite3.Connection, card_id: str) -> bool:
    """This card's creation is still owed (queued or at Mercury now)."""
    return c.execute("SELECT 1 FROM fin_outbox WHERE op='card_create' AND status='pending' AND "
                     "json_extract(payload_json, '$.card_id')=? LIMIT 1", (card_id,)).fetchone() is not None


def cards_uncertain(c: sqlite3.Connection, connection_id: str, looked_before: str) -> list[Any]:
    """Cards whose creation was cut off at Mercury (it may or may not have issued them), on this connection, that no
    one has looked for in Mercury's card list since `looked_before`."""
    return c.execute("SELECT k.* FROM fin_cards k JOIN fin_money_accounts m ON m.id=k.money_account_id WHERE "
                     "k.provider_status='uncertain' AND m.connection_id=? AND (k.synced_at IS NULL OR "
                     "k.synced_at<?) ORDER BY k.created_at", (connection_id, looked_before)).fetchall()


def card_create_payload(c: sqlite3.Connection, card_id: str) -> dict[str, Any]:
    """What this card's creation asked Mercury for (its user and nickname)."""
    row = c.execute("SELECT payload_json FROM fin_outbox WHERE op='card_create' AND "
                    "json_extract(payload_json, '$.card_id')=? ORDER BY created_at DESC LIMIT 1", (card_id,)).fetchone()
    return loads(row[0]) if row is not None else {}


def open_writes(c: sqlite3.Connection, connection_id: str, ops: Sequence[str]) -> list[Any]:
    """Writes of these kinds the provider does not hold yet: pending ones, and failed ones it could not rule out. A
    write it refused gives its amount back."""
    return c.execute(f"SELECT * FROM fin_outbox WHERE connection_id=? AND op IN ({_marks(ops)}) AND "
                     "(status='pending' OR (status='failed' AND unknown=1)) ORDER BY created_at, id",
                     (connection_id, *ops)).fetchall()


def outbox_stuck(c: sqlite3.Connection, before: str, connection_id: str) -> list[Any]:
    """Writes claimed before `before` and never recorded: the worker stopped mid-call."""
    return c.execute("SELECT * FROM fin_outbox WHERE status='pending' AND claimed_at IS NOT NULL AND claimed_at<? AND "
                     "connection_id=? ORDER BY created_at", (before, connection_id)).fetchall()


# ------------------------------------------------------------------------------------------------ rules
def rules(c: sqlite3.Connection, kind: str = "gate", active_only: bool = True) -> list[Any]:
    sql = "SELECT * FROM fin_rules WHERE kind=?" + (" AND active=1" if active_only else "")
    return c.execute(sql + " ORDER BY created_at, id", (kind,)).fetchall()


def posting_rules_since(c: sqlite3.Connection, since: str) -> list[Any]:
    """Posting rules made since a time, newest first (the Bookkeeper's weekly digest)."""
    return c.execute("SELECT * FROM fin_rules WHERE kind='posting' AND created_at>=? ORDER BY created_at DESC, id",
                     (since,)).fetchall()


def rules_for_external(c: sqlite3.Connection, external_rule_id: str) -> list[Any]:
    return c.execute("SELECT * FROM fin_rules WHERE external_rule_id=? ORDER BY created_at, id",
                     (external_rule_id,)).fetchall()


def counter(c: sqlite3.Connection, rule_id: str, period_key: str, subject: str) -> tuple[int, int]:
    row = c.execute("SELECT amount, count FROM fin_rule_counters WHERE rule_id=? AND period_key=? AND subject=?",
                    (rule_id, period_key, subject)).fetchone()
    return (int(row[0]), int(row[1])) if row else (0, 0)


def count(c: sqlite3.Connection, rule_id: str, period_key: str, subject: str, amount: int, at: str) -> None:
    """Add one use to a rule's period counter, in the caller's transaction (the writer lock serializes racers)."""
    c.execute("INSERT INTO fin_rule_counters(rule_id, period_key, subject, amount, count, updated_at) "
              "VALUES(?,?,?,?,1,?) ON CONFLICT(rule_id, period_key, subject) DO UPDATE SET "
              "amount=amount+excluded.amount, count=count+1, updated_at=excluded.updated_at",
              (rule_id, period_key, subject, amount, at))


def uncount(c: sqlite3.Connection, rule_id: str, period_key: str, subject: str, amount: int, at: str) -> None:
    """Take back one use `count` added (a payment that reserved it never went), never below 0."""
    c.execute("UPDATE fin_rule_counters SET amount=MAX(0, amount-?), count=MAX(0, count-1), updated_at=? WHERE "
              "rule_id=? AND period_key=? AND subject=?", (amount, at, rule_id, period_key, subject))


def rule_run(c: sqlite3.Connection, *, rule_id: str, action_id: str | None, subject: str,
             inputs: Mapping[str, object], outcome: Mapping[str, object], by: str, at: str) -> None:
    c.execute("INSERT INTO fin_rule_runs(id, rule_id, action_id, subject, inputs_json, outcome_json, created_at, "
              "created_by) VALUES(?,?,?,?,?,?,?,?)",
              (new_id(), rule_id, action_id, subject, dump(inputs), dump(outcome), at, by))


def actions_since(c: sqlite3.Connection, since: str, most: int) -> list[Any]:
    return c.execute("SELECT * FROM fin_actions WHERE created_at>=? ORDER BY created_at DESC LIMIT ?",
                     (since, most)).fetchall()


# ------------------------------------------------------------------------------------------------ books helpers
def balances(c: sqlite3.Connection, book_id: str, through: str, account_ids: Iterable[str]) -> dict[str, int]:
    """Each account's posted balance (debit +) through a day, from the rollups."""
    ids = sorted(account_ids)
    if not ids:
        return {}
    return {str(r[0]): int(r[1]) for r in c.execute(
        f"SELECT account_id, COALESCE(SUM(amount),0) FROM fin_rollups WHERE book_id=? AND day<=? AND "
        f"account_id IN ({_marks(ids)}) GROUP BY account_id", [book_id, through, *ids])}


def sources_by_account(c: sqlite3.Connection, book_id: str, through: str, account_ids: Iterable[str]
                       ) -> dict[str, list[str]]:
    """Which kinds of entry (bank, card, usage, manual...) make up each account's balance."""
    ids = sorted(account_ids)
    if not ids:
        return {}
    out: dict[str, list[str]] = {}
    for r in c.execute(f"SELECT l.account_id, e.source FROM fin_lines l JOIN fin_entries e ON e.id=l.entry_id "
                       f"WHERE l.book_id=? AND l.entry_date<=? AND l.account_id IN ({_marks(ids)}) "
                       f"GROUP BY l.account_id, e.source", [book_id, through, *ids]):
        out.setdefault(str(r[0]), []).append(str(r[1]))
    return out


def entry_counts(c: sqlite3.Connection, book_id: str) -> dict[str, int]:
    return {str(r[0]): int(r[1]) for r in c.execute(
        "SELECT source, count(*) FROM fin_entries WHERE book_id=? GROUP BY source", (book_id,))}


def document_counts(c: sqlite3.Connection, book_id: str) -> dict[str, int]:
    bills = int(c.execute("SELECT count(*) FROM fin_bills WHERE book_id=?", (book_id,)).fetchone()[0])
    invoices = int(c.execute("SELECT count(*) FROM fin_invoices WHERE book_id=?", (book_id,)).fetchone()[0])
    return {"bills": bills, "invoices": invoices}


def lines_page(c: sqlite3.Connection, book_id: str, *, account_ids: Sequence[str], before: tuple[str, str] | None,
               most: int, start: str = "", end: str = "", q: str = "", sources: Sequence[str] = ()) -> list[Any]:
    """Posted lines newest first, keyset-paged on (entry_date, line id), with their entry's memo and source."""
    if not account_ids:
        return []
    where = ["l.book_id=?", f"l.account_id IN ({_marks(account_ids)})"]
    args: list[object] = [book_id, *account_ids]
    if sources:
        where.append(f"e.source IN ({_marks(sources)})")
        args += list(sources)
    if start:
        where.append("l.entry_date>=?")
        args.append(start)
    if end:
        where.append("l.entry_date<=?")
        args.append(end)
    if q:
        where.append("(l.contact_id LIKE ? ESCAPE '\\' OR l.description LIKE ? ESCAPE '\\' OR e.memo LIKE ? ESCAPE '\\')")
        args += [like(q)] * 3
    if before:
        where.append("(l.entry_date<? OR (l.entry_date=? AND l.id<?))")
        args += [before[0], before[0], before[1]]
    return c.execute(f"SELECT l.id AS line_id, l.entry_id, l.entry_date, l.account_id, l.amount, l.contact_id, "
                     f"l.description, e.memo, e.source, e.source_id, e.status AS entry_status, e.reverses_id FROM fin_lines l "
                     f"JOIN fin_entries e ON e.id=l.entry_id WHERE {' AND '.join(where)} "
                     f"ORDER BY l.entry_date DESC, l.id DESC LIMIT ?", [*args, most]).fetchall()


def seed_rule(c: sqlite3.Connection, *, rule_id: str, name: str, words: str, when: Mapping[str, object],
              outcome: Mapping[str, object], examples: Sequence[Mapping[str, object]], by: str, at: str) -> bool:
    """A default gate rule, once: a rule with this id that exists (even disabled or edited) is left alone."""
    return c.execute("INSERT OR IGNORE INTO fin_rules(id, kind, name, words, when_json, outcome_json, examples_json, "
                     "owner, active, created_at, created_by) VALUES(?, 'gate', ?, ?, ?, ?, ?, ?, 1, ?, ?)",
                     (rule_id, name, words, dump(when), dump(outcome), dump(list(examples)), by, at, by)).rowcount == 1


def rule_runs(c: sqlite3.Connection, rule_id: str, most: int) -> list[Any]:
    return c.execute("SELECT * FROM fin_rule_runs WHERE rule_id=? ORDER BY created_at DESC, id DESC LIMIT ?",
                     (rule_id, most)).fetchall()


def last_rule_run(c: sqlite3.Connection, action_id: str) -> Any:
    """The latest gate evaluation logged for one action (what its counters moved by when it committed)."""
    if not action_id:
        return None
    return c.execute("SELECT * FROM fin_rule_runs WHERE action_id=? ORDER BY created_at DESC, id DESC LIMIT 1",
                     (action_id,)).fetchone()


def recipient_paid(c: sqlite3.Connection, recipient_id: str) -> bool:
    return c.execute("SELECT 1 FROM fin_payments WHERE recipient_id=? AND status='settled' LIMIT 1",
                     (recipient_id,)).fetchone() is not None


# ------------------------------------------------------------------------------------------------ Stripe
def feed_total(c: sqlite3.Connection, money_account_id: str) -> int:
    """What a money account's feed rows add up to: a Stripe balance's nets, every row taken in so far."""
    row = c.execute("SELECT COALESCE(SUM(amount), 0) FROM fin_feed_txns WHERE money_account_id=?",
                    (money_account_id,)).fetchone()
    return int(row[0])


def stripe_source(c: sqlite3.Connection, money_account_id: str, kinds: Sequence[str], link: str) -> Any:
    """The Stripe row of these kinds whose link is this id (a sale, or a payout), the first one taken in."""
    return c.execute(f"SELECT * FROM fin_feed_txns WHERE money_account_id=? AND kind IN ({_marks(kinds)}) AND "
                     "json_extract(raw_json, '$.link')=? ORDER BY txn_date, id LIMIT 1",
                     (money_account_id, *kinds, link)).fetchone()


def stripe_refunded(c: sqlite3.Connection, money_account_id: str, kinds: Sequence[str], link: str) -> int:
    """What the posted refunds, disputes and returned refunds of one charge add up to so far (a refund is positive)."""
    return int(c.execute(f"SELECT COALESCE(SUM(-json_extract(raw_json, '$.amount')), 0) FROM fin_feed_txns WHERE "
                         f"money_account_id=? AND kind IN ({_marks(kinds)}) AND json_extract(raw_json, '$.link')=? AND "
                         "status='posted' AND entry_id IS NOT NULL",
                         (money_account_id, *kinds, link)).fetchone()[0])


def stripe_given(c: sqlite3.Connection, money_account_id: str, kinds: Sequence[str], link: str) -> dict[str, int]:
    """What the posted refunds, disputes and returned refunds of one charge gave back to each settlement (settlement id
    to cents; a restore is negative). Each refund keeps its shares in raw_json.gave."""
    rows = c.execute("SELECT j.key, COALESCE(SUM(j.value), 0) FROM fin_feed_txns f, json_each(f.raw_json, '$.gave') j "
                     f"WHERE f.money_account_id=? AND f.kind IN ({_marks(kinds)}) AND "
                     "json_extract(f.raw_json, '$.link')=? AND f.status='posted' AND f.entry_id IS NOT NULL "
                     "GROUP BY j.key", (money_account_id, *kinds, link)).fetchall()
    return {str(key): int(total) for key, total in rows}


def stripe_restored(c: sqlite3.Connection, money_account_id: str, kinds: Sequence[str], source: str) -> bool:
    """Whether a posted restore (a refund failure, or a dispute won back) already names this Stripe source id."""
    return c.execute(f"SELECT 1 FROM fin_feed_txns WHERE money_account_id=? AND kind IN ({_marks(kinds)}) AND "
                     "json_extract(raw_json, '$.source')=? AND json_extract(raw_json, '$.amount')>0 AND "
                     "status='posted' AND entry_id IS NOT NULL LIMIT 1", (money_account_id, *kinds, source)).fetchone() \
        is not None


def stripe_reversal(c: sqlite3.Connection, money_account_id: str, kinds: Sequence[str], source: str) -> Any:
    """The posted money-out row (a refund or a dispute) that a refund failure names by its Stripe source id, or None."""
    return c.execute(f"SELECT * FROM fin_feed_txns WHERE money_account_id=? AND kind IN ({_marks(kinds)}) AND "
                     "json_extract(raw_json, '$.source')=? AND json_extract(raw_json, '$.amount')<0 AND "
                     "status='posted' AND entry_id IS NOT NULL ORDER BY json_extract(raw_json, '$.created') LIMIT 1",
                     (money_account_id, *kinds, source)).fetchone()


def stripe_summary_rows(c: sqlite3.Connection, money_account_id: str) -> list[Any]:
    """Stripe rows marked for their day's summary entry that have not posted yet."""
    return c.execute("SELECT * FROM fin_feed_txns WHERE money_account_id=? AND status='new' AND entry_id IS NULL AND "
                     "json_extract(raw_json, '$.summary')=1 ORDER BY txn_date, id", (money_account_id,)).fetchall()


def stripe_waiting(c: sqlite3.Connection, money_account_id: str, kinds: Sequence[str], most: int) -> list[Any]:
    """Stripe rows of these kinds still waiting for the sale or payout they are about, oldest first."""
    return c.execute(f"SELECT * FROM fin_feed_txns WHERE money_account_id=? AND kind IN ({_marks(kinds)}) AND "
                     "status='new' AND entry_id IS NULL ORDER BY txn_date, id LIMIT ?",
                     (money_account_id, *kinds, most)).fetchall()


# A posted entry that is the bank side of a Stripe payout's clearing: its source id is `stripe:clear:<feed row id>`,
# and `fresh_source` may add `:<when>` after it. `f` is the feed row of the payout.
_CLEARED = ("e.source='bank' AND e.status='posted' AND (e.source_id = 'stripe:clear:' || f.id OR "
            "substr(e.source_id, 1, length('stripe:clear:') + length(f.id) + 1) = 'stripe:clear:' || f.id || ':')")


def stripe_cleared(c: sqlite3.Connection, payout_id: str) -> bool:
    """Whether a Stripe payout's transfer to the bank has posted (a deposit cleared it)."""
    return c.execute(f"SELECT 1 FROM fin_feed_txns f JOIN fin_money_accounts m ON m.id=f.money_account_id JOIN "
                     f"fin_entries e ON e.book_id=m.book_id WHERE f.id=? AND {_CLEARED} LIMIT 1",
                     (payout_id,)).fetchone() is not None


def stripe_transit(c: sqlite3.Connection, book_id: str, *, start: str, end: str, amount: int | None, most: int
                   ) -> list[Any]:
    """Stripe payouts still in transit: posted, not returned, and not cleared by a bank deposit. `amount` is the
    payout's own (negative) amount when given."""
    return c.execute(
        "SELECT f.* FROM fin_feed_txns f JOIN fin_money_accounts m ON m.id=f.money_account_id "
        "WHERE m.book_id=? AND m.kind='stripe' AND f.kind='payout' AND f.status='posted' AND "
        "f.txn_date BETWEEN ? AND ? AND (? IS NULL OR f.amount=?) AND NOT EXISTS (SELECT 1 FROM fin_feed_txns g "
        "WHERE g.money_account_id=f.money_account_id AND g.kind IN ('payout_failure', 'payout_cancel') AND "
        "json_extract(g.raw_json, '$.link')=json_extract(f.raw_json, '$.link')) AND NOT EXISTS (SELECT 1 FROM "
        f"fin_entries e WHERE e.book_id=m.book_id AND {_CLEARED}) ORDER BY f.txn_date, f.id LIMIT ?",
        (book_id, start, end, amount, amount, most)).fetchall()


def stripe_deposits(c: sqlite3.Connection, book_id: str, *, start: str, end: str, amount: int | None, most: int
                    ) -> list[Any]:
    """Bank deposits that look like Stripe's (STRIPE in the counterparty or description) and wait for their payout:
    new, settled, and not a card's or a request's."""
    return c.execute(
        "SELECT f.* FROM fin_feed_txns f JOIN fin_money_accounts m ON m.id=f.money_account_id "
        "WHERE m.book_id=? AND m.kind='bank' AND f.status='new' AND f.pending=0 AND f.entry_id IS NULL AND "
        "f.card_id IS NULL AND f.request_id IS NULL AND f.amount>0 AND f.txn_date BETWEEN ? AND ? AND "
        "(? IS NULL OR f.amount=?) AND (UPPER(f.counterparty) LIKE '%STRIPE%' OR UPPER(f.description) LIKE '%STRIPE%') "
        "ORDER BY f.txn_date, f.id LIMIT ?", (book_id, start, end, amount, amount, most)).fetchall()
