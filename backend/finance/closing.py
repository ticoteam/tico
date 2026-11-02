"""The close snapshot (spec 4.1, 10): what a month looked like when Tico closed it. A closed month reads through the
account tree it closed with, so later moves and renames do not change it. The snapshot keeps the month's totals too,
so a later change to a closed month (a restatement, §10) is flagged on it. Amounts still come from the posted rows
the viewer may see; the snapshot never bypasses suppression."""
import json
import sqlite3
from collections.abc import Iterable
from dataclasses import asdict, replace
from typing import Any

from .context import Ctx
from .core import dates
from .core.ledger import Account
from .store import ap as SAP
from .store import banking as SK
from .store import ledger as SL


def take(ctx: Ctx, period_id: str, month: str, start: str, end: str, by: str) -> None:
    """Store a closed month's P&L totals (accrual, and cash as the cash view counts them), its balances at the end and
    the account tree as it stands now."""
    c, book = ctx.c, ctx.book.id
    ids = sorted(ctx.accounts)
    accrual = _sums(SL.rollup_rows(c, book, start, end))
    cash = _sums([*SL.rollup_rows(c, book, start, end), *SAP.cash_rows(c, book, start, end, ids)])
    tree = [asdict(a) for a in ctx.accounts.values()]
    SL.insert_snapshot(c, book, period_id, month, json.dumps(tree, sort_keys=True),
                       json.dumps({"accrual": accrual, "cash": cash}, sort_keys=True),
                       json.dumps(SK.balances(c, book, end, ids), sort_keys=True), by, ctx.at)


def frozen(ctx: Ctx, month: str) -> Ctx | None:
    """The context a closed month reads with: the account tree it closed with. An account made since keeps its live
    record. None when the month is open or has no snapshot (it closed before the snapshot existed)."""
    if month not in SL.closed_months(ctx.c, ctx.book.id):
        return None
    row = SL.snapshot(ctx.c, ctx.book.id, month)
    if row is None:
        return None
    tree = {str(x["id"]): Account(**x) for x in json.loads(str(row["accounts_json"]))}
    return replace(ctx, accounts={**ctx.accounts, **tree}, _views={})


def marker(c: sqlite3.Connection, book_id: str, month: str) -> dict[str, Any] | None:
    """A closed month's snapshot as reads show it: how many restatements it has taken. None without a snapshot."""
    if month not in SL.closed_months(c, book_id):
        return None
    row = SL.snapshot(c, book_id, month)
    if row is None:
        return None
    return {"month": month, "restatements": len(json.loads(str(row["restated_json"])))}


def restate(c: sqlite3.Connection, book_id: str, month: str) -> int:
    """How many accounts of a closed month now total differently from its snapshot (spec 10). Reports only: it writes
    no flag. The edit that moved the totals flags the snapshot once, where it posts (mirror._restate), so a check that
    runs alongside that edit cannot count it twice. A month that still matches returns 0."""
    row = SL.snapshot(c, book_id, month)
    if row is None:
        return 0
    first, last = dates.month_bounds(dates.day(f"{month}-01"))
    was_doc: dict[str, Any] = json.loads(str(row["pnl_json"]))
    was = {str(k): int(v) for k, v in was_doc["accrual"].items()}
    now = _sums(SL.rollup_rows(c, book_id, first.isoformat(), last.isoformat()))
    return len({k for k in set(was) | set(now) if was.get(k, 0) != now.get(k, 0)})


def flag(c: sqlite3.Connection, book_id: str, month: str, by: str, at: str, change: str) -> int:
    """Flag a closed month on its snapshot because an edit landed in it (spec 10: a mirrored record edited inside a
    closed month). The flag keeps when it happened and what kind of change it was, never the entries or amounts, so a
    private line's detail stays private. Returns the flags the snapshot holds; 0 without a snapshot."""
    row = SL.snapshot(c, book_id, month)
    if row is None:
        return 0
    return _append(c, row, {"at": at, "by": by, "change": change})


def _append(c: sqlite3.Connection, row: Any, entry: dict[str, Any]) -> int:
    restated: list[Any] = json.loads(str(row["restated_json"]))
    restated.append(entry)
    SL.set_restated(c, str(row["id"]), json.dumps(restated, sort_keys=True))
    return len(restated)


def _sums(rows: Iterable[Any]) -> dict[str, int]:
    """Each account's total over these rows, without the zero ones."""
    out: dict[str, int] = {}
    for r in rows:
        key = str(r["account_id"])
        out[key] = out.get(key, 0) + int(r["amount"])
    return {k: v for k, v in out.items() if v}
