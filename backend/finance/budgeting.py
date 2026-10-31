"""Budget state and holds over the store (spec §4.4). Every function takes the caller's open connection: a hold is one
small step inside the caller's transaction, so two holds racing for the last dollars serialize on the writer lock and
the second sees the first."""
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from typing import Any

from .context import Ctx, is_set_up, load
from .core import budgets as B
from .core import dates, money
from .core import validate as V
from .core import view as VW
from .core.errors import FinanceError, NOT_FOUND, invalid
from .core.ledger import descendants, vendor_text
from .core.org import SYSTEM
from .store import ap as SAP
from .store import budgets as SB
from .store import ledger as SL

HOLD_SOURCES = ("run", "video", "card", "payment", "bill", "request", "action", "manual")
TARGET_KINDS = B.TARGETS
WARN_EVENT, LIMIT_EVENT = "finance.budget.warn", "finance.budget.cutoff"


@dataclass(frozen=True)
class HoldDecision:
    """What a hold request got. `allowed` with a `hold_id` (None when no budget covers the spend, which nothing
    limits); else the strictest at-limit `action` (ask | decline | hold_payment) and the budget that set it."""
    allowed: bool
    action: str
    hold_id: str | None = None
    budget_id: str | None = None
    limit_budget_id: str | None = None
    warn_budget_ids: tuple[str, ...] = ()
    remaining: int | None = None
    reason: str = ""


# ------------------------------------------------------------------------------------------------ scope and state
def subtree(ctx: Ctx, budget_id: str) -> list[str]:
    """`budget_id` and every budget under it: holds sit on the deepest budget and reserve in every parent."""
    kids: dict[str, list[str]] = {}
    for b in ctx.budgets().values():
        if b.parent_id:
            kids.setdefault(b.parent_id, []).append(b.id)
    out, todo = [budget_id], [budget_id]
    while todo:
        for child in kids.get(todo.pop(), []):
            if child not in out:
                out.append(child)
                todo.append(child)
    return out


def scope_accounts(ctx: Ctx, b: B.Budget) -> list[str]:
    if b.catch_all:
        return sorted(B.catch_all_accounts(ctx.budgets(), ctx.accounts))
    return sorted(descendants(ctx.accounts, b.account_id))


@dataclass
class Netted:
    """A budget read: its viewer, and whether the figures it gives net out settled private lines (tab.settled_lines,
    the P&L's rule, and the holds and card lines of the same feed rows). `excluded` is set once something is netted; the
    read then marks itself excluded. Enforcement reads with no Netted and takes the true figures."""
    view: VW.View
    excluded: bool = False


def actual(ctx: Ctx, b: B.Budget, start: str, end: str, teammate: str = "", read: Netted | None = None) -> int:
    """Posted spend in the budget's scope in the window: its line and the lines under it, cut to its team (and the teams
    under it), teammate and vendor. A read passes its `Netted`, and the settled private payroll lines in that scope come
    out of the sum, as they do from the P&L."""
    teams = sorted(ctx.org.below(b.team)) if b.team else None
    accounts = scope_accounts(ctx, b)
    teammate = teammate or b.teammate
    vendors = SAP.vendor_keys(ctx.c, ctx.book.id, b.vendor)
    total = SL.rollup_sum(ctx.c, ctx.book.id, start, end, accounts, teams=teams, teammate=teammate, vendors=vendors)
    if read is None:
        return total
    return total - _settled_in(ctx, read, start, end, accounts, teams, teammate, vendors)


def _settled_in(ctx: Ctx, read: Netted, start: str, end: str, accounts: Sequence[str], teams: Sequence[str] | None,
                teammate: str, vendors: Sequence[str]) -> int:
    """What the settled private payroll lines in a rollup's scope add up to: the lines tab.settled_lines finds, cut to
    the rollup's own filters (team, teammate, vendor), so a read takes out exactly what the rollup counted."""
    from . import tab       # tab reads budgets too (card rows), so it is imported here rather than at the top
    if teams is not None and not teams:
        return 0
    lines = tab.settled_lines(ctx, read.view, dates.day(start), dates.day(end), accounts, "accrual")
    tags = SL.line_tags(ctx.c, [str(r["id"]) for r in lines])
    total = 0
    for r in lines:
        mine = tags.get(str(r["id"]), {})
        if teams is not None and mine.get("team", "") not in teams:
            continue
        if teammate and mine.get("teammate", "") != teammate:
            continue
        if vendors and str(r["contact_id"] or "") not in vendors:
            continue
        total += int(r["amount"])
        read.excluded = True
    return total


def _held(ctx: Ctx, budget_ids: Sequence[str], start: str, end: str, read: Netted | None) -> int:
    """What holds in the window reserve on these budgets outside an allocation's own (SB.held), less the settled private
    ones a read's viewer can't see."""
    total = SB.held(ctx.c, budget_ids, start, end)
    return total if read is None else total - _private_holds(ctx, read, budget_ids, start, end)


def _private_holds(ctx: Ctx, read: Netted, budget_ids: Sequence[str], start: str, end: str, target: str = "",
                   linked: bool = False) -> int:
    """The settled holds in the window (for `target`, any when empty) that stand for spend a private feed row made: the
    row a hold was placed for (a card's authorization), or the feed row of the entry the hold was posted to. A hold no
    entry took in always counts; one whose entry is posted only when `linked`."""
    from . import tab
    if not budget_ids or not tab.private_cut(ctx, read.view):
        return 0
    mine = [r for r in SB.settled_holds(ctx.c, budget_ids, start, end, target)
            if not r["entry_id"] or (linked and str(r["entry_status"]) == "posted")]
    origin = {str(r["id"]): _hold_origin(r) for r in mine}
    cut = tab.private_origins(ctx, read.view, list(origin.values()))
    took = [r for r in mine if origin[str(r["id"])] in cut]
    if took:
        read.excluded = True
    return sum(int(r["settled_amount"] or 0) for r in took)


def _hold_origin(r: Any) -> str:
    """The feed row a settled hold's spend came from: its entry's (when posted to one), else its own source."""
    from . import tab
    if r["entry_id"]:
        return tab.feed_origin({"source": r["entry_source"] or "", "source_id": r["entry_source_id"] or ""})
    return tab.feed_origin({"source": r["source"], "source_id": r["source_id"]})


def settled_used(ctx: Ctx, read: Netted, budget_ids: Sequence[str], start: str, end: str, target: str,
                  absorbed: bool) -> int:
    """The private part of what a target used on these budgets in the window (SB.target_used), which a read takes out.
    A card's use is its money lines of private feed rows (SB.card_used) plus its settled holds no entry took in. Any
    other target's use is its settled holds (with `absorbed`, those whose entry is posted too). A teammate's posted
    lines come out through actual."""
    from . import tab
    if not tab.private_cut(ctx, read.view):
        return 0
    if not target.startswith("card:"):
        return _private_holds(ctx, read, budget_ids, start, end, target, linked=absorbed)
    money = SB.card_money(ctx.c, budget_ids, target.partition(":")[2], start, end)
    cut = tab.private_origins(ctx, read.view, [str(r["feed_id"]) for r in money])
    took = [r for r in money if str(r["feed_id"]) in cut]
    if took:
        read.excluded = True
    # A card's use is the negated sum of its money lines (SB.card_used).
    return _private_holds(ctx, read, budget_ids, start, end, target) - sum(int(r["amount"]) for r in took)


def period_window(ctx: Ctx, budget_id: str) -> tuple[str, str]:
    """The budget's period today, as ISO days."""
    b = ctx.budgets()[budget_id]
    start, end = _period(ctx, b, dates.day(ctx.today), SB.versions(ctx.c, [b.id])[b.id])
    return start.isoformat(), end.isoformat()


def _period(ctx: Ctx, b: B.Budget, at: date, versions: Sequence[B.Version]) -> tuple[date, date]:
    return B.period_of(b, at, ctx.book.fiscal_year_start, versions)


def _amount(ctx: Ctx, b: B.Budget, versions: Sequence[B.Version], period: tuple[date, date], at: date,
            read: Netted | None = None) -> int | None:
    """The amount for the period: the version in force at its start (on `at` for a once budget), plus what an `unused`
    rollover carries in from every earlier period since the budget's first version."""
    base = B.amount_at(versions, at if b.period == "once" else period[0])
    if base is None or b.rollover != "unused" or not versions:
        return base
    fy = ctx.book.fiscal_year_start
    first = min(dates.day(v.effective_from) for v in versions)
    history: list[tuple[int | None, int]] = []
    start, end = B.period_of(b, first, fy, versions)
    tree = subtree(ctx, b.id)
    while start < period[0]:
        a, z = start.isoformat(), end.isoformat()
        history.append((B.amount_at(versions, start), actual(ctx, b, a, z, read=read) + _held(ctx, tree, a, z, read)))
        start, end = B.period_of(b, end.fromordinal(end.toordinal() + 1), fy, versions)
    return base + B.carry(history)


def _reserve(ctx: Ctx, budget_ids: Sequence[str], start: str, end: str, read: Netted | None = None
             ) -> list[tuple[str, str, int]]:
    """What allocations on these budgets still reserve, as (budget id, target, amount): each one's amount less what its
    target used under it, never below zero. A target's use (its holds, settled holds, for a teammate their posted
    lines, for a card its entries in the ledger) draws down only the nearest allocation for that target above it,
    never every one up the tree."""
    budgets = ctx.budgets()
    by_target: dict[str, list[tuple[str, int]]] = {}
    for row in SB.allocations(ctx.c, budget_ids):
        b = budgets.get(row["budget_id"])
        if b is not None and b.active:
            by_target.setdefault(f"{row['target_kind']}:{row['target_id']}", []).append((b.id, int(row["amount"])))
    out: list[tuple[str, str, int]] = []
    for target, rows in by_target.items():
        kind, _, tid = target.partition(":")
        on = {bid for bid, _ in rows}
        used: dict[str, int] = {}

        def usage(bid: str, used: dict[str, int] = used, target: str = target, kind: str = kind, tid: str = tid) -> int:
            if bid not in used:
                tree = subtree(ctx, bid)
                total = SB.target_used(ctx.c, tree, start, end, target, absorbed=kind != "teammate")
                if read is not None:
                    total -= settled_used(ctx, read, tree, start, end, target, absorbed=kind != "teammate")
                used[bid] = total + (actual(ctx, budgets[bid], start, end, teammate=tid, read=read)
                                     if kind == "teammate" else 0)
            return used[bid]
        for bid, amount in rows:
            under = [x for x in subtree(ctx, bid)[1:] if x in on]
            nearest = [x for x in under if not any(y != x and x in subtree(ctx, y) for y in under)]
            out.append((bid, target, max(0, amount - usage(bid) + sum(usage(x) for x in nearest))))
    return out


def state(ctx: Ctx, budget_id: str, at: date, target: str = "", path: Sequence[str] = (),
          read: Netted | None = None) -> B.State:
    """A budget now: amount, actual, held (holds and allocations' reserves in its whole subtree), remaining, percent.
    With a `target` and the budget `path` a spend sits on, that target's own unused allocations on that path are not
    counted against it (it may spend what was set aside for it there); its allocations on other branches still are.
    A read passes its `Netted`: the actual, the held and what depends on them (rollover, reserves) leave out the
    settled private payroll the viewer can't see, and the State says so (`excluded`). Enforcement passes none."""
    b = ctx.budgets().get(budget_id)
    if b is None:
        raise FinanceError(NOT_FOUND, "no such budget")
    versions = SB.versions(ctx.c, [b.id])[b.id]
    period = _period(ctx, b, at, versions)
    start, end = period[0].isoformat(), period[1].isoformat()
    tree = subtree(ctx, b.id)
    mine = set(path)
    reserve = sum(v for bid, t, v in _reserve(ctx, tree, start, end, read)
                  if not (target and t == target and bid in mine))
    held = _held(ctx, tree, start, end, read) + reserve
    st = B.state(b, period, _amount(ctx, b, versions, period, at, read), actual(ctx, b, start, end, read=read), held)
    return st if read is None else replace(st, excluded=read.excluded)


def leaf(ctx: Ctx, account_id: str, team: str = "", teammate: str = "", vendor: str = "") -> B.Budget | None:
    """The budget a spend counts against (most specific wins; the catch-all takes uncovered expense lines). A teammate
    with no team named counts in the team they are in."""
    team = team or (ctx.org.team_of.get(teammate, "") if teammate else "")
    if vendor and any(b.vendor for b in ctx.budgets().values()):
        vendor = SAP.vendor_resolved(ctx.c, ctx.book.id, vendor)    # a name a vendor contact goes (or went) by
    return B.find(ctx.budgets(), B.Charge(account_id=account_id, team=team, teammate=teammate, vendor=vendor),
                  ctx.accounts, ctx.org)


def event_key(period_start: str, version: int) -> str:
    """An event's period key: the period and the budget's newest version, so a change of amount arms both again."""
    return f"{period_start}/v{version}"


def event_period(key: str) -> str:
    return key.partition("/")[0]


def crossings(ctx: Ctx, budget_ids: Sequence[str], by: str = SYSTEM) -> None:
    """A `finance.budget.warn` / `.cutoff` event the first time a budget reaches its warn level or its amount, once per
    period and version (a raise arms it again), for Tico's triggers to drain. A budget of 0 is at its cutoff at once:
    it allows no spending."""
    at = dates.day(ctx.today)
    for bid in dict.fromkeys(budget_ids):
        b = ctx.budgets().get(bid)
        if b is None or not b.active:
            continue
        s = state(ctx, bid, at)
        if not (s.crossed_warn or s.at_limit):
            continue
        version = max((v.version for v in SB.versions(ctx.c, [bid])[bid]), default=0)
        facts: dict[str, object] = {"budget_id": bid, "name": b.name, "amount": s.amount, "used": s.actual + s.held,
                                    "remaining": s.remaining, "pct": str(s.pct) if s.pct is not None else None}
        if s.crossed_warn:
            SB.event(ctx.c, WARN_EVENT, bid, event_key(s.period_start, version), facts, by, ctx.at)
        if s.at_limit and s.amount is not None:
            SB.event(ctx.c, LIMIT_EVENT, bid, event_key(s.period_start, version), facts, by, ctx.at)


def chain_of(ctx: Ctx, budget_id: str) -> list[str]:
    return [b.id for b in B.chain(ctx.budgets(), budget_id)]


# ------------------------------------------------------------------------------------------------ holds (part B's API)
def _scope(ctx: Ctx, budget_scope: str | Mapping[str, object]) -> B.Budget | None:
    if isinstance(budget_scope, str):
        b = ctx.budgets().get(budget_scope)
        if b is None or not b.active:
            raise FinanceError(NOT_FOUND, "no such budget")
        return b
    p = V.obj(budget_scope, "budget_scope")
    V.only(p, ("account_id", "team", "teammate", "vendor"))
    return leaf(ctx, V.text(p, "account_id", 80), V.opt_text(p, "team", 80) or "", V.opt_text(p, "teammate", 120) or "",
                vendor_text(p, "vendor"))


def decide(ctx: Ctx, leaf_id: str, amount: int, target: str = "") -> B.Decision:
    """What a new spend of `amount` meets across its budget and every parent."""
    at = dates.day(ctx.today)
    path = chain_of(ctx, leaf_id)
    chain = [(b, state(ctx, b.id, at, target, path)) for b in B.chain(ctx.budgets(), leaf_id)]
    return B.decide(chain, amount)


def place(ctx: Ctx, *, budget_scope: str | Mapping[str, object], amount: int, currency: str, source: str,
          source_id: str, expires_at: str | None, target: str = "", by: str = SYSTEM) -> HoldDecision:
    money.minor(amount)
    if amount <= 0:
        raise invalid("a hold is for a positive amount")
    if money.currency(currency) != ctx.book.base_currency:
        raise invalid(f"holds are in the book's currency ({ctx.book.base_currency})")
    if source not in HOLD_SOURCES:
        raise invalid(f"a hold source is {'|'.join(HOLD_SOURCES)}")
    if target and target.partition(":")[0] not in TARGET_KINDS:
        raise invalid(f"a hold target is <{'|'.join(TARGET_KINDS)}>:<id>")
    if not target and isinstance(budget_scope, Mapping) and budget_scope.get("teammate"):
        target = f"teammate:{budget_scope['teammate']}"
    if source_id:
        prior = SB.hold_for_source(ctx.c, source, source_id)
        if prior is not None:
            return _again(ctx, prior, amount, target, by)
    b = _scope(ctx, budget_scope)
    if b is None:
        return HoldDecision(allowed=True, action="ok", reason="no budget covers this spend")
    verdict = decide(ctx, b.id, amount, target)
    if not verdict.allowed:
        crossings(ctx, [verdict.limit_budget], by)
        limit = ctx.budgets()[verdict.limit_budget]
        return HoldDecision(allowed=False, action=verdict.action, budget_id=b.id, limit_budget_id=limit.id,
                            warn_budget_ids=verdict.warn_budgets, reason=f"over the {limit.name} budget")
    hid = SB.insert_hold(ctx.c, budget_id=b.id, amount=amount, currency=ctx.book.base_currency, source=source,
                         source_id=source_id, target=target, held_on=ctx.today, expires_at=expires_at, by=by, at=ctx.at)
    crossings(ctx, chain_of(ctx, b.id), by)
    left = state(ctx, b.id, dates.day(ctx.today), target, chain_of(ctx, b.id)).remaining
    return HoldDecision(allowed=True, action=verdict.action, hold_id=hid, budget_id=b.id,
                        warn_budget_ids=verdict.warn_budgets, remaining=left)


def _again(ctx: Ctx, prior: Mapping[str, object], amount: int, target: str, by: str) -> HoldDecision:
    """The same (source, source_id) asked again. The same amount returns the live hold; a smaller one shrinks it; a
    larger one grows it only if the increase passes a fresh budget check, else the hold stays as it was and the
    decision says so."""
    hid, bid, was = str(prior["id"]), str(prior["budget_id"]), int(str(prior["amount"]))
    target = target or str(prior["target"] or "")
    path = chain_of(ctx, bid)
    if amount == was:
        return HoldDecision(allowed=True, action="ok", hold_id=hid, budget_id=bid, reason="already held")
    if amount > was:
        verdict = decide(ctx, bid, amount - was, target)
        if not verdict.allowed:
            crossings(ctx, [verdict.limit_budget], by)
            limit = ctx.budgets()[verdict.limit_budget]
            return HoldDecision(allowed=False, action=verdict.action, hold_id=hid, budget_id=bid,
                                limit_budget_id=limit.id, warn_budget_ids=verdict.warn_budgets,
                                reason=f"kept the hold at {was}: {amount} is over the {limit.name} budget")
    SB.resize_hold(ctx.c, hid, amount)
    crossings(ctx, path, by)
    return HoldDecision(allowed=True, action="ok", hold_id=hid, budget_id=bid,
                        remaining=state(ctx, bid, dates.day(ctx.today), target, path).remaining,
                        reason=f"resized the hold from {was} to {amount}")


def settle(ctx: Ctx, hold_id: str, actual_amount: int, entry_id: str | None, by: str = SYSTEM) -> bool:
    """Settle at whatever was really spent: more than the hold or the budget included (a provider already charged it,
    so remaining may go negative and shows as over). A settled hold the entry arrives for later is absorbed."""
    money.minor(actual_amount)
    row = SB.hold(ctx.c, hold_id)
    if row is None:
        raise FinanceError(NOT_FOUND, "no such hold")
    if entry_id is not None and SL.entry_row(ctx.c, entry_id) is None:
        raise FinanceError(NOT_FOUND, "no such entry")
    if row["status"] == "held":
        SB.settle(ctx.c, hold_id, actual_amount, entry_id, ctx.at)
    elif row["status"] == "settled" and entry_id is not None and row["entry_id"] is None:
        SB.absorb(ctx.c, hold_id, entry_id)
    else:
        return False
    crossings(ctx, chain_of(ctx, str(row["budget_id"])), by)
    return True


def release(ctx: Ctx, hold_id: str) -> bool:
    return SB.release(ctx.c, hold_id, ctx.at) > 0


# ------------------------------------------------------------------------------------------------ connection-level API
def hold_place(c: sqlite3.Connection, *, budget_scope: str | Mapping[str, object], amount: int, currency: str,
               source: str, source_id: str, expires_at: str | None, target: str = "", at: str | None = None,
               by: str = SYSTEM) -> HoldDecision:
    """Reserve `amount` against the budget a spend belongs to (a budget id, or {account_id, team, teammate, vendor})
    and every parent at once. A retry with the same (source, source_id) returns the live hold. Before Finance is set up
    nothing limits spend."""
    if not is_set_up(c):
        return HoldDecision(allowed=True, action="ok", reason="Finance is not set up")
    return place(load(c, at), budget_scope=budget_scope, amount=amount, currency=currency, source=source,
                 source_id=source_id, expires_at=expires_at, target=target, by=by)


def hold_settle(c: sqlite3.Connection, hold_id: str, actual_amount: int, entry_id: str | None = None,
                at: str | None = None) -> bool:
    return settle(load(c, at), hold_id, actual_amount, entry_id)


def hold_release(c: sqlite3.Connection, hold_id: str, at: str | None = None) -> bool:
    return release(load(c, at), hold_id)


def holds_expire(c: sqlite3.Connection, at: str, most: int = 500) -> int:
    """Release holds past their expiry (a run that never reported back). How many."""
    if not is_set_up(c):
        return 0
    ctx = load(c, at)
    return sum(1 for hid in SB.expired(c, at, most) if release(ctx, hid))


def budget_state(c: sqlite3.Connection, budget_id: str, at: str | None = None) -> B.State:
    ctx = load(c, at)
    return state(ctx, budget_id, dates.day(ctx.today))


def budget_for(c: sqlite3.Connection, *, account_id: str, team: str = "", teammate: str = "", vendor: str = "",
               at: str | None = None) -> str | None:
    """The budget id a spend on (account, team, teammate, vendor) counts against, or None."""
    if not is_set_up(c):
        return None
    found = leaf(load(c, at), account_id, team, teammate, vendor)
    return found.id if found else None


def release_for(ctx: Ctx, source: str, source_id: str) -> int:
    return sum(1 for hid in SB.holds_for_source(ctx.c, source, source_id) if release(ctx, hid))


__all__ = ["HoldDecision", "hold_place", "hold_settle", "hold_release", "holds_expire", "budget_state", "budget_for"]
