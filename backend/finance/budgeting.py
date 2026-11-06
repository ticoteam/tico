"""Budget state and holds over the store (spec §4.4). Every function takes the caller's open connection: a hold is one
small step inside the caller's transaction, so two holds racing for the last dollars serialize on the writer lock and
the second sees the first.

Live ad spend (the Google Ads and Meta Ads imports, ad_import.py). The platforms report spend daily; the card charge
for it posts days or weeks later and is the real expense in the books. A budget must move with the daily spend during
the month and must never count both. So a read (`live=True`: the budgets list, the budget metrics, budget vs actual)
takes each calendar month of its window on its own:
- a closed month (closing.py) reads the books alone, as before;
- an open month reads the books less the posted lines whose vendor is an ad platform with an enabled import (vendor
  keys sorted by name: "GOOGLE *ADS", "FACEBK *", "META PLATFORMS", the "Google Ads" / "Meta" contacts the import
  makes), less the card authorization holds at those merchants, plus the platform's daily spend for the ad accounts in
  the budget's scope. A platform's lines come out only of budgets whose scope holds the books line its import books
  to, so a charge filed elsewhere is never lost from that line.
An ad account is in a budget's scope when its import's books line is under the budget's line, the budget's teammate is
the account's bot (or the budget names none), its team holds that bot (or it names none), and its vendor, if any, is
the platform's. Enforcement (decide, place, crossings, a decline budget's card freeze) still reads the books alone, so
platform figures never block or refuse anything: ad budgets warn only (ad_warnings.py tells the spending bot). The
ledger is never touched. `pace_end` projects the period's spend at today's daily rate (month and quarter budgets, from
the period's second day)."""
import re
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
from .core.errors import CONFLICT, FinanceError, NOT_FOUND, invalid
from .core.ledger import descendants, vendor_text
from .core.org import SYSTEM
from .store import access as SA
from .store import ads as SAD
from .store import ap as SAP
from .store import budgets as SB
from .store import ledger as SL

HOLD_SOURCES = ("run", "video", "card", "payment", "bill", "request", "action", "manual", "purchase_order")
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


def actual(ctx: Ctx, b: B.Budget, start: str, end: str, teammate: str = "", read: Netted | None = None,
           live: bool = False) -> int:
    """Posted spend in the budget's scope in the window: its line and the lines under it, cut to its team (and the teams
    under it), teammate and vendor. A read passes its `Netted`, and the settled private payroll lines in that scope come
    out of the sum, as they do from the P&L. `live`: open months take the ad platforms' daily spend in place of their
    posted charges (the module docstring)."""
    vendors = SAP.vendor_keys(ctx.c, ctx.book.id, b.vendor)
    total = _posted(ctx, b, start, end, teammate, read, vendors)
    if not live:
        return total
    lv = live_ads(ctx, b, start, end, teammate, read)
    return total - lv.books_cut + lv.platform


def _posted(ctx: Ctx, b: B.Budget, start: str, end: str, teammate: str, read: Netted | None,
            vendors: Sequence[str]) -> int:
    """`actual` over these vendor keys (none: any vendor)."""
    teams = sorted(ctx.org.below(b.team)) if b.team else None
    accounts = scope_accounts(ctx, b)
    teammate = teammate or b.teammate
    total = SL.rollup_sum(ctx.c, ctx.book.id, start, end, accounts, teams=teams, teammate=teammate, vendors=vendors)
    if read is None:
        return total
    return total - _cut_in(ctx, read, start, end, accounts, teams, teammate, vendors)


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


def _cut_in(ctx: Ctx, read: Netted, start: str, end: str, accounts: Sequence[str], teams: Sequence[str] | None,
            teammate: str, vendors: Sequence[str]) -> int:
    """What a read takes out of a rollup's scope: the private money its viewer can't see, whatever its origin (spec
    §7.5). Settled feed postings on ordinary accounts come out line by line (`_settled_in`); lines on a private account
    (a journal entry, a bill or a feed row) are cut the way the P&L cuts them (`_private_in`)."""
    from . import tab
    hidden = set(tab.private_cut(ctx, read.view))
    return (_settled_in(ctx, read, start, end, [a for a in accounts if a not in hidden], teams, teammate, vendors)
            + _private_in(ctx, read, start, end, [a for a in accounts if a in hidden], teams, teammate, vendors))


def horizon(ctx: Ctx) -> date:
    """The latest day a read reaches: the later of today and the book's last posting. An open-ended window (a once budget
    runs to 9999-12-31) is held to it, so no read builds a date past the book."""
    today = dates.day(ctx.today)
    last = SL.last_day(ctx.c, ctx.book.id)
    return max(today, dates.day(last)) if last else today


def _private_in(ctx: Ctx, read: Netted, start: str, end: str, accounts: Sequence[str], teams: Sequence[str] | None,
                teammate: str, vendors: Sequence[str]) -> int:
    """The lines on these private accounts, in the rollup's scope and the window, that the viewer's figure leaves out.
    They go through the one suppression function (view.suppress) over the whole fiscal quarters around the window, as the
    P&L reads them: a pay run or quarter the viewer may see stays in, and the rest comes out. A narrower read (a team, a
    teammate or a vendor) shows none of it. `excluded` is set when a line in the window is not shown to the viewer."""
    if not accounts or (teams is not None and not teams):
        return 0
    fy = ctx.book.fiscal_year_start
    a = dates.day(start)
    wide_start, z, wide_end = dates.quarters_around(a, dates.day(end), fy, horizon(ctx))
    wide = (wide_start, wide_end)
    cells: list[VW.Cell] = []
    total = 0
    unseen = False
    for r in SL.rollup_rows(ctx.c, ctx.book.id, wide[0].isoformat(), wide[1].isoformat(), accounts):
        team, mate, vendor = str(r["team"]), str(r["teammate"]), str(r["vendor"])
        if (teams is not None and team not in teams) or (teammate and mate != teammate) or \
                (vendors and vendor not in vendors):
            continue
        day, amount = str(r["day"]), int(r["amount"])
        inside = a.isoformat() <= day <= z.isoformat()
        account = ctx.accounts.get(str(r["account_id"]))
        seen = account is not None and read.view.sees(account, {"team": team, "teammate": mate})
        unseen = unseen or (inside and not seen)
        if seen:
            cells.append(VW.Cell(account_id=str(r["account_id"]), day=day, team=team, teammate=mate, vendor=vendor,
                                 amount=amount))
        if inside:
            total += amount
    shown = VW.suppress(read.view, ctx.org, SA.private_lines(ctx.c), ctx.accounts, cells,
                        teams is not None or bool(teammate) or bool(vendors), window=(a, z),
                        closed=SL.closed_months(ctx.c, ctx.book.id), fiscal_start=fy)
    kept = sum(cell.amount for cell in shown.cells)
    if unseen or kept != total:
        read.excluded = True
    return total - kept


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


def period_window(ctx: Ctx, budget_id: str, on: str = "") -> tuple[str, str]:
    """The budget's period on the day `on` (today when none), as ISO days."""
    b = ctx.budgets()[budget_id]
    start, end = _period(ctx, b, dates.day(on or ctx.today), SB.versions(ctx.c, [b.id])[b.id])
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


def _precedes(x: B.Budget, y: B.Budget) -> bool:
    """Whether budget x takes posted lines that x and y could both carry, before y: an active budget before an ended one,
    then the newer. A replacement takes the spend its ended predecessor used to carry."""
    return (x.active, x.created_at, x.id) > (y.active, y.created_at, y.id)


def _covers(scope: tuple[frozenset[str], frozenset[str] | None, frozenset[str] | None], account: str, team: str,
            vendor: str) -> bool:
    """Whether an allocation's scope (accounts, teams, vendor keys; None: any) takes a posted line on this account, team
    and vendor: the same cut `actual` makes."""
    accounts, teams, vendors = scope
    return account in accounts and (teams is None or team in teams) and (vendors is None or vendor in vendors)


def _owned(ctx: Ctx, group: Sequence[str], tid: str, start: str, end: str, read: Netted | None) -> dict[str, int]:
    """One teammate's posted lines in the window, each counted once (spec 4.4). A line belongs to the nearest allocation in
    `group` whose scope takes it (none of its own sub-allocations takes it too), and among equally near ones to the one
    that takes precedence (`_precedes`): an active budget before an ended one, then the newer. So two allocations whose
    scopes overlap draw each line once, and an allocation's use is the lines assigned to it. Returns each allocation's own
    lines, its children's not included."""
    budgets = ctx.budgets()
    scopes: dict[str, tuple[frozenset[str], frozenset[str] | None, frozenset[str] | None]] = {}
    for bid in group:
        b = budgets[bid]
        keys = SAP.vendor_keys(ctx.c, ctx.book.id, b.vendor)
        scopes[bid] = (frozenset(scope_accounts(ctx, b)), frozenset(ctx.org.below(b.team)) if b.team else None,
                       frozenset(keys) if keys else None)
    owned = dict.fromkeys(group, 0)
    accounts: list[str] = sorted({a for bid in group for a in scopes[bid][0]})
    if not accounts:
        return owned
    below = {bid: frozenset(subtree(ctx, bid)[1:]) for bid in group}
    for row in SL.rollup_lines(ctx.c, ctx.book.id, start, end, accounts, tid):
        account, team, vendor = str(row["account_id"]), str(row["team"]), str(row["vendor"])
        covering = [bid for bid in group if _covers(scopes[bid], account, team, vendor)]
        if not covering:
            continue
        amount = int(row["amount"])
        if read is not None:
            # Netted under this group's own vendor key only: each hidden line sits in one group, so it comes out once.
            amount -= _cut_in(ctx, read, start, end, [account], [team], tid, [vendor])
        near = [x for x in covering if not any(y in below[x] for y in covering)]
        owner = near[0]
        for x in near[1:]:
            if _precedes(budgets[x], budgets[owner]):
                owner = x
        owned[owner] += amount
    return owned


def _reserve_detail(ctx: Ctx, budget_ids: Sequence[str], start: str, end: str, read: Netted | None = None,
                    overlay: tuple[str, str, str, int] | None = None, posted: bool = False
                    ) -> list[tuple[str, str, int, int, int]]:
    """Each allocation on these budgets as (budget id, target, reserve, own, spent). `own` is what its target used under it
    and not under a nearer allocation for the target: its holds (settled ones as the target counts them) and, for a
    teammate, the posted lines `_owned` assigns to it. `reserve` is its amount less `own`, never below zero. `spent` is the
    posted part of `own` (what was spent, not held); it is worked out only when `posted` is asked for. Every allocation is
    read, since a line's owner can sit outside `budget_ids`. `overlay` (budget id, target kind, target id, amount) reads one
    allocation as it would stand: it replaces the stored row or adds one, and 0 is no row (reserve_growth)."""
    budgets = ctx.budgets()
    allocs = [(str(r["budget_id"]), str(r["target_kind"]), str(r["target_id"]), int(r["amount"]))
              for r in SB.allocations(ctx.c, list(budgets))]
    if overlay is not None:
        allocs = [r for r in allocs if r[:3] != overlay[:3]] + ([overlay] if overlay[3] > 0 else [])
    by_target: dict[str, list[tuple[str, int]]] = {}
    for bid, kind, tid, amount in allocs:
        # An ended budget's allocations still reserve against its parents: its cards keep their Mercury limits until they
        # are lowered or canceled (spec 4.4). Only a new set-aside on an ended budget is refused (allocation_plan).
        if bid in budgets:
            by_target.setdefault(f"{kind}:{tid}", []).append((bid, amount))
    wanted = set(budget_ids)
    out: list[tuple[str, str, int, int, int]] = []
    for target, rows in by_target.items():
        if not any(bid in wanted for bid, _ in rows):
            continue
        kind, _, tid = target.partition(":")
        on = [bid for bid, _ in rows]
        absorbed = kind != "teammate"
        owned = _owned(ctx, on, tid, start, end, read) if kind == "teammate" else {}
        used: dict[str, int] = {}
        held: dict[str, int] = {}

        def usage(bid: str, used: dict[str, int] = used, owned: dict[str, int] = owned, target: str = target,
                  absorbed: bool = absorbed) -> int:
            if bid not in used:
                tree = subtree(ctx, bid)
                total = SB.target_used(ctx.c, tree, start, end, target, absorbed=absorbed)
                if read is not None:
                    total -= settled_used(ctx, read, tree, start, end, target, absorbed=absorbed)
                used[bid] = total + sum(owned.get(x, 0) for x in tree)
            return used[bid]

        def holds(bid: str, held: dict[str, int] = held, target: str = target) -> int:
            if bid not in held:
                held[bid] = SB.held(ctx.c, subtree(ctx, bid), start, end, target)
            return held[bid]
        for bid, amount in rows:
            if bid not in wanted:
                continue
            under = [x for x in subtree(ctx, bid)[1:] if x in on]
            nearest = [x for x in under if not any(y != x and x in subtree(ctx, y) for y in under)]
            own = usage(bid) - sum(usage(x) for x in nearest)
            spent = own - (holds(bid) - sum(holds(x) for x in nearest)) if posted else 0
            out.append((bid, target, max(0, amount - own), own, spent))
    return out


def _reserve(ctx: Ctx, budget_ids: Sequence[str], start: str, end: str, read: Netted | None = None,
             overlay: tuple[str, str, str, int] | None = None) -> list[tuple[str, str, int]]:
    """What allocations on these budgets still reserve, as (budget id, target, amount): each one's amount less what its
    target used under it, never below zero (`_reserve_detail`)."""
    return [(bid, target, reserve) for bid, target, reserve, _, _ in
            _reserve_detail(ctx, budget_ids, start, end, read, overlay)]


def retained(ctx: Ctx, budget_id: str, start: str, end: str) -> int:
    """What an ended budget still holds against its parents in the window (spec 4.4): the holds on it and its children
    with no target, and each allocation in its subtree at the larger of its amount and what its target used (holds and
    posted spend). The allocations keep their whole amount against the parent until they are lowered or canceled, as a
    live child's amount counts, so a parent cannot be lowered under them."""
    tree = subtree(ctx, budget_id)
    return SB.held(ctx.c, tree, start, end) + sum(reserve + spent for _, _, reserve, _, spent
                                                  in _reserve_detail(ctx, tree, start, end, posted=True))


def reserve_growth(ctx: Ctx, budget_id: str, allocation_on: str, target_kind: str, target_id: str, amount: int,
                   at: date) -> int:
    """What setting a target's allocation on `allocation_on` to `amount` adds to `budget_id`'s reserve on `at`: the reserve
    over the budget's whole subtree in its period as it would stand, less as it stands. A target's use already counts
    against the budget, and an allocation draws down only the use no nearer allocation holds, so the part of the amount
    that use already covers is not counted again."""
    start, end = period_window(ctx, budget_id, at.isoformat())
    tree = subtree(ctx, budget_id)
    before = sum(v for _, _, v in _reserve(ctx, tree, start, end))
    after = sum(v for _, _, v in _reserve(ctx, tree, start, end,
                                          overlay=(allocation_on, target_kind, target_id, amount)))
    return after - before


def state(ctx: Ctx, budget_id: str, at: date, target: str = "", path: Sequence[str] = (),
          read: Netted | None = None, live: bool = False) -> B.State:
    """A budget now: amount, actual, held (active holds plus the reserves unused allocations keep in its whole subtree),
    remaining (which refuses new holds and allocations), spent (actuals and holds: what warn and cutoff read) and percent.
    With a `target` and the budget `path` a spend sits on, that target's own unused allocations on that path are not
    counted against it (it may spend what was set aside for it there); its allocations on other branches still are.
    A read passes its `Netted`: the actual, the holds and what depends on them (rollover, reserves) leave out the settled
    private payroll the viewer can't see, and the State says so (`excluded`). Enforcement passes none. A read passing
    `live` counts the ad platforms' daily spend in open months (the module docstring) and says where the spend came
    from (`spent_from`) and how fresh the platforms' figures are (`as_of`)."""
    b = ctx.budgets().get(budget_id)
    if b is None:
        raise FinanceError(NOT_FOUND, "no such budget")
    versions = SB.versions(ctx.c, [b.id])[b.id]
    period = _period(ctx, b, at, versions)
    start, end = period[0].isoformat(), period[1].isoformat()
    tree = subtree(ctx, b.id)
    mine = set(path)
    reserve = sum(v for bid, t, v in _reserve(ctx, tree, start, end, read)
                  if not (bid in mine and target and t == target))
    holds = _held(ctx, tree, start, end, read)
    spent = actual(ctx, b, start, end, read=read)
    lv = live_ads(ctx, b, start, end, "", read, tree) if live else NO_ADS
    spent += lv.platform - lv.books_cut
    holds -= lv.holds_cut
    st = B.state(b, period, _amount(ctx, b, versions, period, at, read), spent, holds, reserve)
    st = replace(st, pace_end=pace_end(b, period, at, st.spent))
    if live:
        books = st.spent - lv.platform
        source = "books" if not lv.covered or not lv.open else \
            "platform" if not lv.closed and books == 0 else "mixed"
        st = replace(st, spent_from=source, as_of=lv.as_of if lv.covered and lv.open else None)
    return st if read is None else replace(st, excluded=read.excluded)


def pace_end(b: B.Budget, period: tuple[date, date], at: date, spent: int) -> int | None:
    """Where the period's spend ends at today's rate: spent / days so far (today included) × days in the period, half
    up. None for a period other than a month or quarter, and on its first day (one day is no rate)."""
    if b.period not in ("month", "quarter"):
        return None
    days = (period[1] - period[0]).days + 1
    elapsed = min((at - period[0]).days + 1, days)
    if elapsed < 2:
        return None
    return (2 * spent * days + elapsed) // (2 * elapsed)


# ------------------------------------------------------------------------------------------------ live ad spend
# A vendor's name (a posted key, its label, or a contact's name and aliases) that is an ad platform's charge.
AD_VENDORS = {
    "google_ads": re.compile(r"\bgoogle\s*\*?\s*ads|\badwords\b", re.IGNORECASE),
    "meta_ads": re.compile(r"\bfacebk\b|\bfacebook\b|\bmeta\s*platforms\b|\bmeta\s*ads\b|^\s*meta\s*$|\bfb\s*\*?\s*ads?\b",
                           re.IGNORECASE),
}


@dataclass(frozen=True)
class LiveAds:
    """What a live read changes in a budget's window: the posted platform charges taken out (`books_cut`), the
    platform card holds taken out (`holds_cut`) and the platforms' daily spend put in (`platform`), all in open months.
    `covered`: an ad account counts in the budget; `open` / `closed`: the window has an open / a closed month;
    `platforms`: the platforms counting in it; `as_of`: the oldest last import among them."""
    books_cut: int = 0
    holds_cut: int = 0
    platform: int = 0
    covered: bool = False
    open: bool = False
    closed: bool = False
    platforms: frozenset[str] = frozenset()
    as_of: str | None = None


NO_ADS = LiveAds()


def _memo(ctx: Ctx, key: str, make: Any) -> Any:
    if key not in ctx.memo:
        ctx.memo[key] = make()
    return ctx.memo[key]


def ad_imports(ctx: Ctx) -> dict[str, SAD.AdImport]:
    """{platform: its enabled import}, read once per call."""
    found: dict[str, SAD.AdImport] = _memo(ctx, "ads:imports", lambda: SAD.enabled_platforms(ctx.c))
    return found


def ad_platform(ctx: Ctx, key: str) -> str:
    """The ad platform a vendor key is a charge from ("" when none): a contact by its name and every alias, a plain
    name key by itself and the text it was posted under."""
    known: dict[str, str] = _memo(ctx, "ads:vendors", dict[str, str])
    if key in known:
        return known[key]
    texts = [key]
    if SAP.is_contact(ctx.c, key):
        row = ctx.c.execute("SELECT name FROM fin_contacts WHERE id=?", (key,)).fetchone()
        texts = [str(row[0]) if row else "", *SAP.aliases_of(ctx.c, key)]
    else:
        texts += list(SL.vendor_label_texts(ctx.c, ctx.book.id, [key]).values())
    known[key] = next((p for p, rx in AD_VENDORS.items() if any(rx.search(t) for t in texts if t)), "")
    return known[key]


def budget_platform(ctx: Ctx, b: B.Budget) -> str:
    """The platform a budget's vendor is, when that platform has an enabled import; else ""."""
    if not b.vendor:
        return ""
    on = ad_imports(ctx)
    for key in SAP.vendor_keys(ctx.c, ctx.book.id, b.vendor) or [b.vendor]:
        p = ad_platform(ctx, key)
        if p in on:
            return p
    return ""


def _months(start: str, end: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    first, last = dates.day(start), dates.day(end)
    while first <= last:
        z = min(dates.month_end(first.replace(day=1)), last)
        out.append((first.isoformat(), z.isoformat()))
        first = date.fromordinal(z.toordinal() + 1)
    return out


def ad_covers(ctx: Ctx, b: B.Budget, s: SAD.LiveSpend, accounts: frozenset[str], teammate: str = "") -> bool:
    """Whether an ad account's spend counts in a budget (scope `accounts`): its books line in scope, its bot the budget's
    teammate and on its team (when the budget names them), the platform the budget's vendor (when it names one). Spend
    in another currency than the book's is left out."""
    if s.currency != ctx.book.base_currency or s.books_account_id not in accounts:
        return False
    bot = "bot:" + s.bot_slug if s.bot_slug else ""
    mate = teammate or b.teammate
    if mate and mate != bot:
        return False
    if b.team and (not bot or ctx.org.team_of.get(bot, "") not in ctx.org.below(b.team)):
        return False
    return not b.vendor or budget_platform(ctx, b) == s.platform


def _month_spend(ctx: Ctx, lo: str, hi: str) -> list[SAD.LiveSpend]:
    key = f"ads:spend:{lo}:{hi}"
    if key not in ctx.memo:
        ctx.memo[key] = SAD.live_spend(ctx.c, lo, hi)
    found: list[SAD.LiveSpend] = ctx.memo[key]
    return found


def _month_vendors(ctx: Ctx, lo: str, hi: str) -> list[str]:
    key = f"ads:keys:{lo}:{hi}"
    if key not in ctx.memo:
        ctx.memo[key] = SAD.rollup_vendors(ctx.c, ctx.book.id, lo, hi)
    found: list[str] = ctx.memo[key]
    return found


def live_ads(ctx: Ctx, b: B.Budget, start: str, end: str, teammate: str = "", read: Netted | None = None,
             tree: Sequence[str] = ()) -> LiveAds:
    """What a live read changes in the budget's window [start, end], month by month (the module docstring). `tree`:
    the budgets whose card holds count in it (state passes the subtree; none for `actual`)."""
    on = ad_imports(ctx)
    if not on:
        return NO_ADS
    closed_months: frozenset[str] = _memo(ctx, "ads:closed", lambda: SL.closed_months(ctx.c, ctx.book.id))
    accounts = frozenset(scope_accounts(ctx, b))
    # Only a platform whose import books into this budget's lines gives up its posted charges here.
    mine = {p for p, imp in on.items() if imp.account_id in accounts}
    if b.vendor:
        mine &= {budget_platform(ctx, b)}
    budget_keys = set(SAP.vendor_keys(ctx.c, ctx.book.id, b.vendor)) if b.vendor else None
    last = min(dates.day(end), horizon(ctx)).isoformat()
    books_cut = holds_cut = platform = 0
    covered = is_open = closed = False
    counting: set[str] = set()
    for lo, hi in _months(start, last):
        if lo[:7] in closed_months:
            closed = True
            continue
        is_open = True
        spend = _month_spend(ctx, lo, hi)
        for row in spend:
            if ad_covers(ctx, b, row, accounts, teammate):
                covered = True
                counting.add(row.platform)
                platform += row.amount
        if not mine:
            continue
        keys = [k for k in _month_vendors(ctx, lo, hi)
                if ad_platform(ctx, k) in mine and (budget_keys is None or k in budget_keys)]
        if keys:
            books_cut += _posted(ctx, b, lo, hi, teammate, read, keys)
        if tree:
            holds_cut += sum(amount for amount, merchant in SAD.card_holds(ctx.c, tree, lo, hi)
                             if any(AD_VENDORS[p].search(merchant) for p in mine))
    stamps = [on[p].last_import_at for p in counting]
    as_of = min((x for x in stamps if x), default=None) if all(stamps) else None
    return LiveAds(books_cut=books_cut, holds_cut=holds_cut, platform=platform, covered=covered, open=is_open,
                   closed=closed, platforms=frozenset(counting), as_of=as_of)


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
    """A `finance.budget.warn` / `.cutoff` event the first time a budget's spend (actuals and holds, not what its unused
    allocations reserve) reaches its warn level or its amount, once per period and version (a raise arms it again), for
    Tico's triggers to drain. A budget of 0 is at its cutoff at once: it allows no spending."""
    at = dates.day(ctx.today)
    for bid in dict.fromkeys(budget_ids):
        b = ctx.budgets().get(bid)
        if b is None or not b.active:
            continue
        s = state(ctx, bid, at)
        if not (s.crossed_warn or s.at_limit):
            continue
        version = max((v.version for v in SB.versions(ctx.c, [bid])[bid]), default=0)
        facts: dict[str, object] = {"budget_id": bid, "name": b.name, "amount": s.amount, "used": s.spent,
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


def decide(ctx: Ctx, leaf_id: str, amount: int, target: str = "", past_reserves: bool = False) -> B.Decision:
    """What a new spend of `amount` meets across its budget and every parent, on the budgets the spend sits on (the chain
    of `leaf_id`). `past_reserves` checks against spend, not remaining (see core budgets decide)."""
    at = dates.day(ctx.today)
    spent_on = chain_of(ctx, leaf_id)
    chain = [(b, state(ctx, b.id, at, target, spent_on)) for b in B.chain(ctx.budgets(), leaf_id)]
    return B.decide(chain, amount, past_reserves)


def would_place(ctx: Ctx, *, budget_scope: str | Mapping[str, object], amount: int,
                target: str = "") -> B.Decision | None:
    """What a hold of `amount` would meet, with nothing written: None when no budget covers the spend."""
    target = target or scope_target(budget_scope)
    b = _scope(ctx, budget_scope)
    return None if b is None else decide(ctx, b.id, amount, target)


def scope_target(budget_scope: str | Mapping[str, object] | None) -> str:
    """The hold target a spend takes from its budget scope: the teammate the scope names, if it names one."""
    if isinstance(budget_scope, Mapping) and budget_scope.get("teammate"):
        return f"teammate:{budget_scope['teammate']}"
    return ""


def place(ctx: Ctx, *, budget_scope: str | Mapping[str, object], amount: int, currency: str, source: str,
          source_id: str, expires_at: str | None, target: str = "", by: str = SYSTEM,
          force: bool = False, moved_from: tuple[str, str] | None = None,
          past_reserves: bool = False) -> HoldDecision:
    """A hold for a spend. `force` holds it past an ask or hold_payment limit, for a spend someone approved; decline
    still refuses. `moved_from` names a hold the spend takes over part of (an order line a bill bills): that hold
    shrinks by the amount and the new hold takes it in the same step, without a fit check. `past_reserves` fits it
    against spend alone, so unused allocations do not refuse it (a run claim)."""
    money.minor(amount)
    if amount <= 0:
        raise invalid("a hold is for a positive amount")
    if money.currency(currency) != ctx.book.base_currency:
        raise invalid(f"holds are in the book's currency ({ctx.book.base_currency})")
    if source not in HOLD_SOURCES:
        raise invalid(f"a hold source is {'|'.join(HOLD_SOURCES)}")
    if target and target.partition(":")[0] not in TARGET_KINDS:
        raise invalid(f"a hold target is <{'|'.join(TARGET_KINDS)}>:<id>")
    target = target or scope_target(budget_scope)
    if source_id:
        prior = SB.hold_for_source(ctx.c, source, source_id)
        if prior is not None:
            return _again(ctx, prior, amount, target, by, force)
    b = _scope(ctx, budget_scope)
    if b is None:
        return HoldDecision(allowed=True, action="ok", reason="no budget covers this spend")
    if moved_from is not None:
        moved = _move(ctx, b, moved_from, amount, source, source_id, expires_at, by)
        if moved is not None:
            return moved
    verdict = decide(ctx, b.id, amount, target, past_reserves)
    if not verdict.allowed and not (force and verdict.action in ("ask", "hold_payment")):
        crossings(ctx, [verdict.limit_budget], by)
        limit = ctx.budgets()[verdict.limit_budget]
        return HoldDecision(allowed=False, action=verdict.action, budget_id=b.id, limit_budget_id=limit.id,
                            warn_budget_ids=verdict.warn_budgets, reason=f"over the {limit.name} budget")
    hid = SB.insert_hold(ctx.c, budget_id=b.id, amount=amount, currency=ctx.book.base_currency, source=source,
                         source_id=source_id, target=target, held_on=ctx.today, expires_at=expires_at, by=by, at=ctx.at)
    crossings(ctx, chain_of(ctx, b.id), by)
    left = state(ctx, b.id, dates.day(ctx.today), target, chain_of(ctx, b.id)).remaining
    return HoldDecision(allowed=True, action=verdict.action, hold_id=hid, budget_id=b.id,
                        limit_budget_id=verdict.limit_budget or None, warn_budget_ids=verdict.warn_budgets,
                        remaining=left)


def _move(ctx: Ctx, b: B.Budget, moved_from: tuple[str, str], amount: int, source: str, source_id: str,
          expires_at: str | None, by: str) -> HoldDecision | None:
    """A spend that takes over `amount` of another hold on the same budget: that hold shrinks (or is released when
    it empties) and the new hold takes the amount, in one step. None when the hold is not there, is on another budget,
    or is smaller than the amount, so the spend is fit-checked like any other."""
    src = SB.hold_for_source(ctx.c, *moved_from)
    if src is None or str(src["budget_id"]) != b.id or int(src["amount"]) < amount:
        return None
    left = int(src["amount"]) - amount
    if left:
        SB.resize_hold(ctx.c, str(src["id"]), left)
    else:
        SB.release(ctx.c, str(src["id"]), ctx.at)
    hid = SB.insert_hold(ctx.c, budget_id=b.id, amount=amount, currency=ctx.book.base_currency, source=source,
                         source_id=source_id, target=str(src["target"] or ""), held_on=ctx.today,
                         expires_at=expires_at, by=by, at=ctx.at)
    crossings(ctx, chain_of(ctx, b.id), by)
    return HoldDecision(allowed=True, action="ok", hold_id=hid, budget_id=b.id,
                        remaining=state(ctx, b.id, dates.day(ctx.today), "", chain_of(ctx, b.id)).remaining,
                        reason=f"moved {amount} from {moved_from[0]} {moved_from[1]}")


def _again(ctx: Ctx, prior: Mapping[str, object], amount: int, target: str, by: str,
           force: bool = False) -> HoldDecision:
    """The same (source, source_id) asked again. The same amount returns the live hold; a smaller one shrinks it; a
    larger one grows it only if the increase passes a fresh budget check (or is forced past an ask or hold_payment
    limit, as a new hold is), else the hold stays as it was and the decision says so."""
    hid, bid, was = str(prior["id"]), str(prior["budget_id"]), int(str(prior["amount"]))
    target = target or str(prior["target"] or "")
    path = chain_of(ctx, bid)
    if amount == was:
        return HoldDecision(allowed=True, action="ok", hold_id=hid, budget_id=bid, reason="already held")
    if amount > was:
        verdict = decide(ctx, bid, amount - was, target)
        if not verdict.allowed and not (force and verdict.action in ("ask", "hold_payment")):
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


def held_amount(ctx: Ctx, source: str, source_id: str) -> int:
    """What the hold for a source holds now: 0 when it holds nothing."""
    row = SB.hold_for_source(ctx.c, source, source_id)
    return 0 if row is None else int(row["amount"])


def transfer(ctx: Ctx, *, amount: int, currency: str, source: str, source_id: str, expires_at: str | None,
             by: str = SYSTEM, budget_scope: str | Mapping[str, object] | None = None,
             moved_from: tuple[str, str] | None = None, target: str | None = None) -> None:
    """An amount of commitment going back to the hold it was taken from: a bill voided or billed for less gives its
    order line its room back. With `moved_from`, the amount comes off that hold (it is released when it empties) and
    goes onto the hold for `source` and `source_id`, on the same budget. Without it, the amount goes onto that hold
    with nothing taken from another, since the spend it stood for has been reversed in the books. Neither is
    fit-checked: the room was the commitment's own already, and a refusal on the way back could leave it with
    no hold. The hold keeps the target of the hold the amount came from, or the teammate `budget_scope` names
    when there is none, unless `target` says otherwise."""
    money.minor(amount)
    if amount <= 0:
        return
    if money.currency(currency) != ctx.book.base_currency:
        raise invalid(f"holds are in the book's currency ({ctx.book.base_currency})")
    src = None
    if moved_from is not None:
        src = SB.hold_for_source(ctx.c, *moved_from)
        if src is None or int(src["amount"]) < amount:
            raise FinanceError(CONFLICT, "the hold this moves from does not hold that much")
        bid = str(src["budget_id"])
        target = str(src["target"] or "") if target is None else target
    else:
        b = None if budget_scope is None else _scope(ctx, budget_scope)
        if b is None:
            return
        bid = b.id
        target = scope_target(budget_scope) if target is None else target
    if src is not None:
        left = int(src["amount"]) - amount
        if left:
            SB.resize_hold(ctx.c, str(src["id"]), left)
        else:
            SB.release(ctx.c, str(src["id"]), ctx.at)
    prior = SB.hold_for_source(ctx.c, source, source_id)
    if prior is None:
        SB.insert_hold(ctx.c, budget_id=bid, amount=amount, currency=ctx.book.base_currency, source=source,
                       source_id=source_id, target=target, held_on=ctx.today, expires_at=expires_at, by=by,
                       at=ctx.at)
    elif str(prior["budget_id"]) == bid:
        SB.resize_hold(ctx.c, str(prior["id"]), int(prior["amount"]) + amount)
    else:
        raise FinanceError(CONFLICT, "that hold is on another budget")
    crossings(ctx, chain_of(ctx, bid), by)


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
               by: str = SYSTEM, past_reserves: bool = False) -> HoldDecision:
    """Reserve `amount` against the budget a spend belongs to (a budget id, or {account_id, team, teammate, vendor})
    and every parent at once. A retry with the same (source, source_id) returns the live hold. Before Finance is set up
    nothing limits spend. `past_reserves`: fit against spend, not remaining (see place)."""
    if not is_set_up(c):
        return HoldDecision(allowed=True, action="ok", reason="Finance is not set up")
    return place(load(c, at), budget_scope=budget_scope, amount=amount, currency=currency, source=source,
                 source_id=source_id, expires_at=expires_at, target=target, by=by, past_reserves=past_reserves)


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
