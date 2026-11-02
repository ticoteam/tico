"""SQL for the books: settings, books, accounts, periods, entries, lines, line tags and daily rollups.

Rollups (`fin_rollups`, day x account x tag set) change in the same transaction as every posting and every tag change,
so reports read them and never scan lines."""
import json
import sqlite3
import uuid
from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal
from typing import Any

from ..core.ledger import Account, Book, Entry, Line, tagset


def new_id() -> str:
    """Core's id format."""
    return str(uuid.uuid4())


# ------------------------------------------------------------------------------------------------ settings
def setting(c: sqlite3.Connection, key: str) -> Any:
    row = c.execute("SELECT value FROM fin_settings WHERE key=?", (key,)).fetchone()
    try:
        return json.loads(row[0]) if row else None
    except (TypeError, ValueError):
        return None


def set_setting(c: sqlite3.Connection, key: str, value: object, by: str, at: str) -> None:
    c.execute("INSERT INTO fin_settings(key, value, created_at, created_by, updated_at, updated_by) "
              "VALUES(?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
              "updated_at=excluded.updated_at, updated_by=excluded.updated_by",
              (key, json.dumps(value, sort_keys=True), at, by, at, by))


# ------------------------------------------------------------------------------------------------ books and accounts
def book(c: sqlite3.Connection) -> Book | None:
    """The one book (v1)."""
    row = c.execute("SELECT * FROM fin_books ORDER BY created_at, id LIMIT 1").fetchone()
    if row is None:
        return None
    return Book(id=row["id"], name=row["name"], base_currency=row["base_currency"], basis=row["basis"],
                fiscal_year_start=int(row["fiscal_year_start"]), mode=row["mode"],
                mirror_connection_id=row["mirror_connection_id"] or "")


def insert_book(c: sqlite3.Connection, b: Book, by: str, at: str) -> None:
    c.execute("INSERT INTO fin_books(id, name, base_currency, basis, fiscal_year_start, mode, mirror_connection_id, "
              "created_at, created_by) VALUES(?,?,?,?,?,?,?,?,?)",
              (b.id, b.name, b.base_currency, b.basis, b.fiscal_year_start, b.mode, b.mirror_connection_id or None,
               at, by))


def set_book_mode(c: sqlite3.Connection, book_id: str, mode: str, connection_id: str) -> None:
    """Switch a book between native and mirror (and which connection's books it copies)."""
    c.execute("UPDATE fin_books SET mode=?, mirror_connection_id=? WHERE id=?", (mode, connection_id or None, book_id))


def _account(row: Any) -> Account:
    return Account(id=row["id"], book_id=row["book_id"], code=row["code"], name=row["name"], type=row["type"],
                   subtype=row["subtype"] or "", parent_id=row["parent_id"] or "", active=bool(row["active"]))


def accounts(c: sqlite3.Connection, book_id: str) -> dict[str, Account]:
    return {r["id"]: _account(r) for r in c.execute("SELECT * FROM fin_accounts WHERE book_id=? ORDER BY code",
                                                     (book_id,))}


def insert_account(c: sqlite3.Connection, a: Account, by: str, at: str) -> None:
    c.execute("INSERT INTO fin_accounts(id, book_id, code, name, type, subtype, parent_id, active, created_at, "
              "created_by, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
              (a.id, a.book_id, a.code, a.name, a.type, a.subtype, a.parent_id or None, int(a.active), at, by, at))


def update_account(c: sqlite3.Connection, a: Account, at: str) -> None:
    c.execute("UPDATE fin_accounts SET code=?, name=?, subtype=?, parent_id=?, active=?, updated_at=? WHERE id=?",
              (a.code, a.name, a.subtype, a.parent_id or None, int(a.active), at, a.id))


def account_has_lines(c: sqlite3.Connection, account_id: str) -> bool:
    return c.execute("SELECT 1 FROM fin_lines WHERE account_id=? LIMIT 1", (account_id,)).fetchone() is not None


# ------------------------------------------------------------------------------------------------ periods
def open_periods_before(c: sqlite3.Connection, book_id: str, day: str, most: int) -> list[Any]:
    """Open periods that ended before `day`, oldest first (the latest `most`)."""
    rows = c.execute("SELECT * FROM fin_periods WHERE book_id=? AND status='open' AND end_date<? "
                     "ORDER BY end_date DESC LIMIT ?", (book_id, day, most)).fetchall()
    return list(reversed(rows))


def period(c: sqlite3.Connection, book_id: str, day: str) -> Any:
    return c.execute("SELECT * FROM fin_periods WHERE book_id=? AND start_date<=? AND end_date>=?",
                     (book_id, day, day)).fetchone()


def period_by_start(c: sqlite3.Connection, book_id: str, start: str) -> Any:
    return c.execute("SELECT * FROM fin_periods WHERE book_id=? AND start_date=?", (book_id, start)).fetchone()


def insert_period(c: sqlite3.Connection, book_id: str, start: str, end: str, by: str, at: str) -> str:
    pid = new_id()
    c.execute("INSERT INTO fin_periods(id, book_id, start_date, end_date, status, created_at, created_by) "
              "VALUES(?,?,?,?,'open',?,?) ON CONFLICT(book_id, start_date) DO NOTHING",
              (pid, book_id, start, end, at, by))
    row = period_by_start(c, book_id, start)
    return str(row["id"])


def set_period_status(c: sqlite3.Connection, period_id: str, status: str, by: str, at: str) -> None:
    c.execute("UPDATE fin_periods SET status=?, closed_at=?, closed_by=? WHERE id=?",
              (status, at if status == "closed" else None, by if status == "closed" else None, period_id))


def closed_on(c: sqlite3.Connection, book_id: str, day: str) -> bool:
    return c.execute("SELECT 1 FROM fin_periods WHERE book_id=? AND status='closed' AND start_date<=? AND end_date>=?",
                     (book_id, day, day)).fetchone() is not None


def closed_months(c: sqlite3.Connection, book_id: str) -> frozenset[str]:
    """Every closed monthly period, as YYYY-MM: payroll cells show only for runs in them."""
    return frozenset(str(r[0])[:7] for r in c.execute("SELECT start_date FROM fin_periods WHERE book_id=? AND "
                                                       "status='closed'", (book_id,)))


def closed_through(c: sqlite3.Connection, book_id: str) -> str | None:
    """The last day of the latest closed period (a reopened one no longer counts)."""
    found = c.execute("SELECT max(end_date) FROM fin_periods WHERE book_id=? AND status='closed'",
                      (book_id,)).fetchone()[0]
    return str(found) if found else None


# ------------------------------------------------------------------------------------------------ vendor labels
def label_vendor(c: sqlite3.Connection, book_id: str, name_key: str, label: str, day: str, *,
                 weak: bool = False) -> None:
    """Remember the text a vendor name key was posted under; an older entry date never replaces a newer one, and a
    `weak` label (the key itself) only fills a missing one."""
    keep = "DO NOTHING" if weak else ("DO UPDATE SET label=excluded.label, seen_on=excluded.seen_on "
                                      "WHERE excluded.seen_on>=fin_vendor_labels.seen_on")
    c.execute(f"INSERT INTO fin_vendor_labels(book_id, name_key, label, seen_on) VALUES(?,?,?,?) "
              f"ON CONFLICT(book_id, name_key) {keep}", (book_id, name_key, label, day))


def vendor_label_texts(c: sqlite3.Connection, book_id: str, keys: Sequence[str]) -> dict[str, str]:
    if not keys:
        return {}
    marks = ",".join("?" * len(keys))
    return {str(r[0]): str(r[1]) for r in c.execute(
        f"SELECT name_key, label FROM fin_vendor_labels WHERE book_id=? AND name_key IN ({marks})",
        (book_id, *keys))}


# ------------------------------------------------------------------------------------------------ entries and lines
def insert_entry(c: sqlite3.Connection, e: Entry, *, entry_id: str, period_id: str, action_id: str,
                 reverses_id: str, by: str, at: str) -> list[str]:
    """The entry, its lines, their tags and the rollups they move. The ids of the lines, in order."""
    c.execute("INSERT INTO fin_entries(id, book_id, entry_date, memo, source, source_id, status, reverses_id, "
              "period_id, action_id, basis, created_at, created_by) VALUES(?,?,?,?,?,?,'posted',?,?,?,?,?,?)",
              (entry_id, e.book_id, e.entry_date, e.memo, e.source, e.source_id, reverses_id or None, period_id,
               action_id or None, e.basis, at, by))
    ids: list[str] = []
    for n, item in enumerate(e.lines):
        lid = new_id()
        ids.append(lid)
        c.execute("INSERT INTO fin_lines(id, entry_id, book_id, line_no, entry_date, account_id, amount, currency, "
                  "original_amount, fx_rate, contact_id, description, role, external_line, created_at, created_by) "
                  "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (lid, entry_id, e.book_id, n, e.entry_date, item.account_id, item.amount, item.currency,
                   item.original_amount, str(item.fx_rate), item.contact_id, item.description, item.role,
                   item.external_line, at, by))
        for kind, value in item.tags.items():
            c.execute("INSERT INTO fin_line_tags(line_id, kind, value, created_at, created_by) VALUES(?,?,?,?,?)",
                      (lid, kind, value, at, by))
        roll(c, e.book_id, e.entry_date, item.account_id, item.tags, item.contact_id, item.amount, 1)
    return ids


def roll(c: sqlite3.Connection, book_id: str, day: str, account_id: str, tags: Mapping[str, str], vendor: str,
         amount: int, count: int) -> None:
    """Add `amount` and `count` lines to one rollup row (an upsert both SQLite and Postgres take)."""
    c.execute("INSERT INTO fin_rollups(book_id, day, account_id, tagset, team, teammate, vendor, amount, line_count) "
              "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(book_id, day, account_id, tagset) DO UPDATE SET "
              "amount=fin_rollups.amount+excluded.amount, line_count=fin_rollups.line_count+excluded.line_count",
              (book_id, day, account_id, tagset(tags, vendor), tags.get("team", ""), tags.get("teammate", ""),
               vendor, amount, count))


def mark_reversed(c: sqlite3.Connection, entry_id: str) -> int:
    return c.execute("UPDATE fin_entries SET status='reversed' WHERE id=? AND status='posted'", (entry_id,)).rowcount


def entry_row(c: sqlite3.Connection, entry_id: str) -> Any:
    return c.execute("SELECT * FROM fin_entries WHERE id=?", (entry_id,)).fetchone()


def entry_by_source(c: sqlite3.Connection, book_id: str, source: str, source_id: str) -> Any:
    """The original entry a source already posted (not a reversal of it), so a re-sent posting posts once."""
    return c.execute("SELECT * FROM fin_entries WHERE book_id=? AND source=? AND source_id=? AND reverses_id IS NULL "
                     "ORDER BY created_at LIMIT 1", (book_id, source, source_id)).fetchone()


def posted_by_source_prefix(c: sqlite3.Connection, book_id: str, source: str, prefix: str) -> list[Any]:
    """Live original entries from a source whose id is `prefix` or starts with `prefix:`."""
    return c.execute("SELECT * FROM fin_entries WHERE book_id=? AND source=? AND (source_id=? OR (source_id>? AND "
                     "source_id<?)) AND status='posted' AND reverses_id IS NULL ORDER BY created_at",
                     (book_id, source, prefix, prefix + ":", prefix + ";")).fetchall()


def line_tags(c: sqlite3.Connection, line_ids: Sequence[str]) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {lid: {} for lid in line_ids}
    for chunk in _chunks(line_ids):
        marks = ",".join("?" * len(chunk))
        for r in c.execute(f"SELECT line_id, kind, value FROM fin_line_tags WHERE line_id IN ({marks})", chunk):
            out[r["line_id"]][r["kind"]] = r["value"]
    return out


def lines(c: sqlite3.Connection, line_ids: Sequence[str]) -> dict[str, tuple[Any, Line]]:
    """{line id: (row, Line)} with each line's current tags."""
    tags = line_tags(c, line_ids)
    out: dict[str, tuple[Any, Line]] = {}
    for chunk in _chunks(line_ids):
        marks = ",".join("?" * len(chunk))
        for r in c.execute(f"SELECT * FROM fin_lines WHERE id IN ({marks})", chunk):
            out[r["id"]] = (r, _line(r, tags.get(r["id"], {})))
    return out


def entry_lines(c: sqlite3.Connection, entry_id: str) -> list[tuple[Any, Line]]:
    rows = c.execute("SELECT * FROM fin_lines WHERE entry_id=? ORDER BY line_no", (entry_id,)).fetchall()
    tags = line_tags(c, [r["id"] for r in rows])
    return [(r, _line(r, tags.get(r["id"], {}))) for r in rows]


def bank_lines(c: sqlite3.Connection, book_id: str, start: str, end: str, account_ids: Sequence[str]) -> list[Any]:
    """The lines in a window on these accounts of bank and card entries, reversals included (they are all in the
    rollups), with the entry's source and source id (the feed row it was posted for) and its basis."""
    if not account_ids:
        return []
    accounts = ",".join("?" * len(account_ids))
    return c.execute("SELECT l.id, l.account_id, l.entry_date, l.contact_id, l.amount, e.source, e.source_id, "
                     "e.basis FROM fin_lines l JOIN fin_entries e ON e.id=l.entry_id WHERE l.book_id=? AND "
                     "l.entry_date>=? AND l.entry_date<=? AND e.source IN ('bank','card') AND "
                     f"l.account_id IN ({accounts})", (book_id, start, end, *account_ids)).fetchall()


def entries_touching(c: sqlite3.Connection, entry_ids: Sequence[str], account_ids: Sequence[str]) -> set[str]:
    """Which of these entries have a line on any of these accounts."""
    if not entry_ids or not account_ids:
        return set()
    out: set[str] = set()
    accounts = ",".join("?" * len(account_ids))
    for chunk in _chunks(list(entry_ids)):
        marks = ",".join("?" * len(chunk))
        out |= {str(r[0]) for r in c.execute(
            f"SELECT DISTINCT entry_id FROM fin_lines WHERE entry_id IN ({marks}) AND account_id IN ({accounts})",
            [*chunk, *account_ids])}
    return out


def posted_total(c: sqlite3.Connection, entry_ids: Sequence[str], account_id: str) -> int:
    """What these entries, where still posted (not reversed), moved on one account."""
    total = 0
    for chunk in _chunks(list(dict.fromkeys(entry_ids))):
        marks = ",".join("?" * len(chunk))
        total += int(c.execute(f"SELECT COALESCE(SUM(l.amount), 0) FROM fin_lines l JOIN fin_entries e ON "
                               f"e.id=l.entry_id WHERE l.entry_id IN ({marks}) AND l.account_id=? AND "
                               f"e.status='posted'", [*chunk, account_id]).fetchone()[0])
    return total


def _line(r: Any, tags: Mapping[str, str]) -> Line:
    return Line(account_id=r["account_id"], amount=int(r["amount"]), currency=r["currency"],
                original_amount=int(r["original_amount"]), fx_rate=Decimal(r["fx_rate"]), contact_id=r["contact_id"],
                description=r["description"], tags=dict(tags), role=str(r["role"] or ""),
                external_line=str(r["external_line"] or ""))


def set_line_tags(c: sqlite3.Connection, row: Any, old: Mapping[str, str], new: Mapping[str, str], by: str,
                  at: str) -> None:
    """Replace a posted line's tags and move its amount between rollup rows. The line itself never changes."""
    if dict(old) == dict(new):
        return
    c.execute("DELETE FROM fin_line_tags WHERE line_id=?", (row["id"],))
    for kind, value in new.items():
        c.execute("INSERT INTO fin_line_tags(line_id, kind, value, created_at, created_by) VALUES(?,?,?,?,?)",
                  (row["id"], kind, value, at, by))
    amount = int(row["amount"])
    roll(c, row["book_id"], row["entry_date"], row["account_id"], old, row["contact_id"], -amount, -1)
    roll(c, row["book_id"], row["entry_date"], row["account_id"], new, row["contact_id"], amount, 1)
    c.execute("DELETE FROM fin_rollups WHERE book_id=? AND day=? AND account_id=? AND tagset=? AND line_count=0 "
              "AND amount=0", (row["book_id"], row["entry_date"], row["account_id"], tagset(old, row["contact_id"])))


def _chunks(values: Sequence[str], size: int = 400) -> Iterable[list[str]]:
    for i in range(0, len(values), size):
        yield list(values[i:i + size])


# ------------------------------------------------------------------------------------------------ rollup reads
def last_day(c: sqlite3.Connection, book_id: str) -> str | None:
    """The last day any posting falls on in the book, or None when nothing is posted."""
    row = c.execute("SELECT MAX(day) FROM fin_rollups WHERE book_id=?", (book_id,)).fetchone()
    return str(row[0]) if row and row[0] else None


def rollup_rows(c: sqlite3.Connection, book_id: str, start: str, end: str,
                account_ids: Iterable[str] | None = None) -> list[Any]:
    """Every rollup row in the window (on `account_ids` when given): what reports and budget actuals sum."""
    sql = ("SELECT day, account_id, tagset, team, teammate, vendor, amount, line_count FROM fin_rollups "
           "WHERE book_id=? AND day>=? AND day<=?")
    args: list[object] = [book_id, start, end]
    ids = sorted(account_ids) if account_ids is not None else None
    if ids is not None:
        if not ids:
            return []
        sql += f" AND account_id IN ({','.join('?' * len(ids))})"
        args += ids
    return c.execute(sql, args).fetchall()


def rollup_sum(c: sqlite3.Connection, book_id: str, start: str, end: str, account_ids: Sequence[str], *,
               teams: Sequence[str] | None = None, teammate: str = "", vendors: Sequence[str] = ()) -> int:
    """The posted total on `account_ids` in the window, cut to `teams` (None: any), a teammate and a vendor (any of
    the keys its lines carry, store/ap.vendor_keys, compared exactly)."""
    if not account_ids or teams is not None and not teams:
        return 0
    sql = (f"SELECT COALESCE(SUM(amount), 0) FROM fin_rollups WHERE book_id=? AND day>=? AND day<=? "
           f"AND account_id IN ({','.join('?' * len(account_ids))})")
    args: list[object] = [book_id, start, end, *account_ids]
    if teams is not None:
        sql += f" AND team IN ({','.join('?' * len(teams))})"
        args += list(teams)
    if teammate:
        sql += " AND teammate=?"
        args.append(teammate)
    if vendors:
        sql += f" AND vendor IN ({','.join('?' * len(vendors))})"
        args += list(vendors)
    return int(c.execute(sql, args).fetchone()[0])


def rollup_lines(c: sqlite3.Connection, book_id: str, start: str, end: str, account_ids: Sequence[str],
                 teammate: str) -> list[Any]:
    """One teammate's posted total per (account, team, vendor) in the window, on `account_ids`: the grain a budget's
    allocation takes its lines by (budgeting._owned). Same filters as rollup_sum, so the parts add up to its total."""
    if not account_ids:
        return []
    return c.execute(f"SELECT account_id, team, vendor, COALESCE(SUM(amount), 0) AS amount FROM fin_rollups "
                     f"WHERE book_id=? AND day>=? AND day<=? AND teammate=? "
                     f"AND account_id IN ({','.join('?' * len(account_ids))}) GROUP BY account_id, team, vendor",
                     [book_id, start, end, teammate, *account_ids]).fetchall()


def entries_on(c: sqlite3.Connection, book_id: str, account_ids: Sequence[str], start: str, end: str,
               most: int, vendors: Sequence[str] | None = None, offset: int = 0) -> list[Any]:
    """Lines on `account_ids` in the window with their entry, newest first, `most` of them after the first `offset`: a
    drill-down's entries (one vendor's when `vendors`, its keys, is given; [""] is lines with none). Each says the card
    or money account its feed row came through."""
    if not account_ids:
        return []
    marks = ",".join("?" * len(account_ids))
    cut, args = ("", []) if vendors is None else (f" AND l.contact_id IN ({','.join('?' * len(vendors))})",
                                                  list(vendors))
    return c.execute(f"SELECT l.id AS line_id, l.entry_id, l.entry_date, l.account_id, l.amount, l.contact_id, "
                     f"l.description, e.memo, e.source, e.source_id, e.status AS entry_status, e.reverses_id, "
                     f"(SELECT f.card_id FROM fin_feed_txns f WHERE f.entry_id=l.entry_id "
                     f"AND f.card_id IS NOT NULL LIMIT 1) AS card_id, (SELECT f.money_account_id FROM fin_feed_txns f "
                     f"WHERE f.entry_id=l.entry_id LIMIT 1) AS money_account_id "
                     f"FROM fin_lines l JOIN fin_entries e ON e.id=l.entry_id "
                     f"WHERE l.book_id=? AND l.account_id IN ({marks}) AND l.entry_date>=? AND l.entry_date<=?{cut} "
                     f"ORDER BY l.entry_date DESC, l.id LIMIT ? OFFSET ?",
                     (book_id, *account_ids, start, end, *args, most, offset)).fetchall()


def insert_snapshot(c: sqlite3.Connection, book_id: str, period_id: str, month: str, accounts_json: str,
                    pnl_json: str, balances_json: str, by: str, at: str) -> None:
    """A closed month's snapshot. Closing a month again replaces its totals and keeps the restatements it took."""
    c.execute("INSERT INTO fin_close_snapshots(id, book_id, period_id, month, accounts, pnl, balances, "
              "restated, created_at, created_by) VALUES(?,?,?,?,?,?,?,'[]',?,?) ON CONFLICT(period_id) DO UPDATE "
              "SET accounts=excluded.accounts, pnl=excluded.pnl, "
              "balances=excluded.balances, created_at=excluded.created_at, created_by=excluded.created_by",
              (new_id(), book_id, period_id, month, accounts_json, pnl_json, balances_json, at, by))


def snapshot(c: sqlite3.Connection, book_id: str, month: str) -> Any:
    return c.execute("SELECT * FROM fin_close_snapshots WHERE book_id=? AND month=?", (book_id, month)).fetchone()


def set_restated(c: sqlite3.Connection, snapshot_id: str, restated_json: str) -> None:
    c.execute("UPDATE fin_close_snapshots SET restated=? WHERE id=?", (restated_json, snapshot_id))
