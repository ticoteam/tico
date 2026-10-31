"""SQL for money in and out (spec §4.3, §4.5, §4.6, §9): connections, money accounts, feed rows, statements and
reconciliations, cards, recipients, payments, the inbox and outbox, gate rules and their counters. Callers pass plain
values; no provider is called from here."""
import json
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, cast

from .ledger import new_id

TABLES = frozenset({"fin_connections", "fin_money_accounts", "fin_feed_txns", "fin_statements", "fin_reconciliations",
                    "fin_cards", "fin_card_auths", "fin_recipients", "fin_payments", "fin_inbox", "fin_outbox",
                    "fin_rules", "fin_rule_runs"})
LIVE_PAYMENT = ("queued", "awaiting_bank_approval", "submitted", "unknown")


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


# ------------------------------------------------------------------------------------------------ money accounts
def money_accounts(c: sqlite3.Connection, book_id: str) -> list[Any]:
    return c.execute("SELECT * FROM fin_money_accounts WHERE book_id=? ORDER BY kind, name, id", (book_id,)).fetchall()


def money_account_by_provider(c: sqlite3.Connection, connection_id: str, provider_account_id: str) -> Any:
    return c.execute("SELECT * FROM fin_money_accounts WHERE connection_id=? AND provider_account_id=?",
                     (connection_id, provider_account_id)).fetchone()


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
    """Posted (not pending, not ignored) rows on or before `through` that no done reconciliation cleared yet."""
    return c.execute("SELECT * FROM fin_feed_txns WHERE money_account_id=? AND txn_date<=? AND pending=0 AND "
                     "status<>'ignored' AND reconciliation_id IS NULL ORDER BY txn_date, id",
                     (money_account_id, through)).fetchall()


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


def payment_by_key(c: sqlite3.Connection, key: str) -> Any:
    return c.execute("SELECT * FROM fin_payments WHERE idempotency_key=?", (key,)).fetchone()


def payment_by_request(c: sqlite3.Connection, request_id: str) -> Any:
    return c.execute("SELECT * FROM fin_payments WHERE provider_request_id=?", (request_id,)).fetchone()


def payments_due(c: sqlite3.Connection, at: str, most: int) -> list[Any]:
    """Payments the worker owes a call: to send (queued), to resolve (unknown), or to poll (waiting on the bank)."""
    return c.execute("SELECT * FROM fin_payments WHERE status IN ('queued','unknown','awaiting_bank_approval',"
                     "'submitted') AND (next_try_at IS NULL OR next_try_at<=?) ORDER BY next_try_at, created_at LIMIT ?",
                     (at, most)).fetchall()


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


# ------------------------------------------------------------------------------------------------ inbox and outbox
def inbox_add(c: sqlite3.Connection, *, connection_id: str | None, provider: str, event_id: str,
              payload: Mapping[str, object], at: str) -> bool:
    """Keep a received event once: False when the same provider event was kept before (a replay)."""
    return c.execute("INSERT OR IGNORE INTO fin_inbox(id, connection_id, provider, provider_event_id, payload_json, "
                     "status, created_at, created_by) VALUES(?,?,?,?,?,'new',?,'system:finance')",
                     (new_id(), connection_id, provider, event_id, dump(payload), at)).rowcount == 1


def inbox_waiting(c: sqlite3.Connection, most: int) -> list[Any]:
    return c.execute("SELECT * FROM fin_inbox WHERE status='new' ORDER BY created_at, id LIMIT ?", (most,)).fetchall()


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


def outbox_due(c: sqlite3.Connection, at: str, most: int) -> list[Any]:
    return c.execute("SELECT * FROM fin_outbox WHERE status='pending' AND (next_try_at IS NULL OR next_try_at<=?) "
                     "ORDER BY created_at LIMIT ?", (at, most)).fetchall()


# ------------------------------------------------------------------------------------------------ rules
def rules(c: sqlite3.Connection, kind: str = "gate", active_only: bool = True) -> list[Any]:
    sql = "SELECT * FROM fin_rules WHERE kind=?" + (" AND active=1" if active_only else "")
    return c.execute(sql + " ORDER BY created_at, id", (kind,)).fetchall()


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
                     f"l.description, e.memo, e.source, e.source_id, e.status AS entry_status FROM fin_lines l "
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


def recipient_paid(c: sqlite3.Connection, recipient_id: str) -> bool:
    return c.execute("SELECT 1 FROM fin_payments WHERE recipient_id=? AND status='settled' LIMIT 1",
                     (recipient_id,)).fetchone() is not None
