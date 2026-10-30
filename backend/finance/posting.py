"""Posting to the books: the invariant checks, the period, the rollups and budget crossings, all in the caller's
transaction. Entries never change after this; a correction is a reversal."""
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import replace

from . import budgeting
from .context import Ctx, load
from .core import dates
from .core import ledger as L
from .core import validate as V
from .core.errors import CONFLICT, NOT_FOUND, PERIOD_CLOSED, FinanceError, invalid
from .core.org import SYSTEM
from .store import budgets as SB
from .store import ledger as SL


def ensure_period(ctx: Ctx, day: str) -> str:
    """The monthly period holding `day`, made open if the book has none there yet."""
    row = SL.period(ctx.c, ctx.book.id, day)
    if row is not None:
        return str(row["id"])
    start, end = dates.month_bounds(dates.day(day))
    return SL.insert_period(ctx.c, ctx.book.id, start.isoformat(), end.isoformat(), SYSTEM, ctx.at)


def with_team(ctx: Ctx, tags: Mapping[str, str]) -> dict[str, str]:
    """A line tagged with a teammate and no team counts in the team the teammate is in when it is tagged."""
    out = dict(tags)
    mate = out.get("teammate", "")
    if mate and not out.get("team") and ctx.org.team_of.get(mate):
        out["team"] = ctx.org.team_of[mate]
    return out


def post(ctx: Ctx, e: L.Entry, *, by: str, action_id: str = "", reverses_id: str = "",
         reversing: bool = False) -> tuple[str, list[str]]:
    """Check and post one entry. The entry id and its line ids."""
    e = replace(e, lines=tuple(replace(x, tags=with_team(ctx, x.tags)) for x in e.lines))
    closed = SL.closed_on(ctx.c, ctx.book.id, e.entry_date)
    L.check(e, ctx.book, ctx.accounts, closed, reversing)
    period_id = ensure_period(ctx, e.entry_date)
    entry_id = SL.new_id()
    line_ids = SL.insert_entry(ctx.c, e, entry_id=entry_id, period_id=period_id, action_id=action_id,
                               reverses_id=reverses_id, by=by, at=ctx.at)
    touched(ctx, e.lines, by)
    return entry_id, line_ids


def touched(ctx: Ctx, lines: Sequence[L.Line], by: str) -> None:
    """Raise budget crossings for the budgets these lines count against."""
    ids: list[str] = []
    for item in lines:
        account = ctx.accounts.get(item.account_id)
        if account is None or account.type != "expense":
            continue
        b = budgeting.leaf(ctx, item.account_id, item.tags.get("team", ""), item.tags.get("teammate", ""),
                           item.contact_id)
        if b is not None:
            ids += budgeting.chain_of(ctx, b.id)
    if ids:
        budgeting.crossings(ctx, ids, by)


def reverse(ctx: Ctx, entry_id: str, *, day: str | None, memo: str, by: str, action_id: str = "") -> str:
    """Post the entry that undoes `entry_id`, on `day` (default: the original's date). Works after the original's
    accounts are deactivated; refused into a closed period."""
    row = SL.entry_row(ctx.c, entry_id)
    if row is None or row["book_id"] != ctx.book.id:
        raise FinanceError(NOT_FOUND, "no such entry")
    if row["status"] != "posted":
        raise FinanceError(CONFLICT, "that entry is already reversed")
    if row["reverses_id"]:
        raise FinanceError(CONFLICT, "that entry is itself a reversal; post a new entry instead")
    lines = [line for _, line in SL.entry_lines(ctx.c, entry_id)]
    when = day or str(row["entry_date"])
    if SL.closed_on(ctx.c, ctx.book.id, when):
        raise FinanceError(PERIOD_CLOSED, f"the period holding {when} is closed; reverse on an open date")
    e = L.Entry(book_id=ctx.book.id, entry_date=when, memo=memo or f"Reverses {entry_id}", source=str(row["source"]),
                source_id=str(row["source_id"]), lines=L.reversal(lines))
    rid, _ = post(ctx, e, by=by, action_id=action_id, reverses_id=entry_id, reversing=True)
    if SL.mark_reversed(ctx.c, entry_id) != 1:
        raise FinanceError(CONFLICT, "that entry is already reversed")
    return rid


def system_entry(raw: object, ctx: Ctx) -> L.Entry:
    """An entry Tico itself posts (usage, imports, syncs): any source but `manual`, balanced lines."""
    p = V.obj(raw, "entry")
    V.only(p, ("date", "memo", "source", "source_id", "lines"))
    source = V.choice(p, "source", tuple(s for s in L.SOURCES if s != "manual"))
    lines = tuple(L.line(item, ctx.book.base_currency) for item in V.items(p.get("lines"), "lines", L.MAX_LINES))
    return L.Entry(book_id=ctx.book.id, entry_date=V.day(p, "date"), memo=V.opt_text(p, "memo") or "",
                   source=source, source_id=V.opt_text(p, "source_id", 200) or "", lines=lines)


def post_entry(c: sqlite3.Connection, entry: Mapping[str, object], *, by: str = SYSTEM,
               hold_ids: Sequence[str] = (), at: str | None = None) -> dict[str, object]:
    """Post an entry from Tico itself (Gateway and video usage, imports) and absorb the settled holds it stands for,
    so the spend counts once. A re-sent (source, source_id) returns the first entry and posts nothing."""
    ctx = load(c, at)
    e = system_entry(entry, ctx)
    if e.source_id:
        prior = SL.entry_by_source(c, ctx.book.id, e.source, e.source_id)
        if prior is not None:
            return {"entry_id": str(prior["id"]), "posted": False}
    entry_id, line_ids = post(ctx, e, by=by)
    for hid in hold_ids:
        row = SB.hold(c, hid)
        if row is None:
            raise invalid(f"no hold {hid}")
        if row["status"] == "held":
            # Once an entry is attached the posted lines count, not the hold, so its own amount is recorded.
            SB.settle(c, hid, int(row["amount"]), entry_id, ctx.at)
        elif row["status"] == "settled":
            SB.absorb(c, hid, entry_id)
    return {"entry_id": entry_id, "line_ids": line_ids, "posted": True}
