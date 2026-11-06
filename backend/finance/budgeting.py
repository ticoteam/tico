"""Budget state and holds over the store (spec §4.4). Every function takes the caller's open connection: a hold is one
small step inside the caller's transaction, so two holds racing for the last dollars serialize on the writer lock and
the second sees the first.

Live ad spend (the Google Ads and Meta Ads imports, ad_import.py). The platforms report spend daily; what they charge
posts days or weeks later, by card, ACH, bill or order, and is the expense of record in the books and the P&L, which
this never touches. A budget tracks a bot's usage, so it moves with the daily spend and never counts both. An ad budget
is one whose vendor is an ad platform (budget_platform: the contact the import makes, "Google Ads" or "Meta", or a
tight descriptor such as GOOGLE *ADS or FACEBK *; store/ads.py). A read (`live=True`: the budgets list, the budget
metrics, budget vs actual) takes each calendar month of an ad budget's window on its own:
- a month the platform's import covers (import_covers: on, its last run OK, reading from that month's start, its last
  success after the month or, for one still running, within STALE) and whose scope it covers (an assigned ad account in
  scope, all in the book's currency) reads the platform's daily spend for those accounts in place of every books line
  of the budget's vendor in that month and every hold at that platform (card, payment, bill and order alike). Closed
  or open, and whenever the charge posts: a December 2 charge for November's spend comes out of December, which the
  platform's December figure stands for;
- any other month reads the books, as before. Nothing comes out unless the platform's figure goes in, so a stale import,
  one to reconnect or one turned off never makes a charge vanish.
`spent_from` says which: books, platform or mixed. An ad account is in a budget's scope when its import's books line is
under the budget's line, the budget's teammate is the account's bot (or the budget names none), and its team holds that
bot (or it names none). An ad budget only warns (warn_only): decide, a card's cutoff, a payment's hold and a pause never
stop anything because of it; ad_warnings.py tells the spending bot and no one else. `pace_end` projects the period's
spend at its rate up to the platform's as_of (month and quarter budgets, once a whole day has passed)."""
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
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
    out of the sum, as they do from the P&L. `live`: an ad budget's covered months take its platform's daily spend in
    place of its posted charges (the module docstring)."""
    vendors = SAP.vendor_keys(ctx.c, ctx.book.id, b.vendor)
    total = _posted(ctx, b, start, end, teammate, read, vendors)
    if not live:
        return total
    lv = live_ads(ctx, b, start, end, teammate, read)
    return total - lv.books_cut + lv.platform_spend


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
    `live` counts an ad budget's platform spend in its covered months (the module docstring) and says where the spend
    came from (`spent_from`) and how fresh the platforms' figures are (`as_of`)."""
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
    spent += lv.platform_spend - lv.books_cut
    holds = max(holds - lv.holds_cut, 0)     # a cut hold the viewer's private netting already took out stays out once
    st = B.state(b, period, _amount(ctx, b, versions, period, at, read), spent, holds, reserve)
    st = replace(st, pace_end=pace_end(b, period, lv.as_of or ctx.at, st.spent))
    if live:
        st = replace(st, spent_from=lv.spent_from, as_of=lv.as_of)
    return st if read is None else replace(st, excluded=read.excluded)


def pace_end(b: B.Budget, period: tuple[date, date], ref: str, spent: int) -> int | None:
    """Where the period's spend ends at its rate so far: spent over the time from the period's start (00:00 UTC) to
    `ref` (the platforms' as_of for an ad budget read live, else now), in fractional days, times the days in the
    period, half up. None for a period other than a month or quarter, and before a whole day has passed."""
    if b.period not in ("month", "quarter"):
        return None
    whole = ((period[1] - period[0]).days + 1) * DAY_SECONDS
    begun = datetime.combine(period[0], datetime.min.time(), tzinfo=timezone.utc)
    elapsed = min(int((_instant(ref) - begun).total_seconds()), whole)
    if elapsed < DAY_SECONDS:
        return None
    return (2 * spent * whole + elapsed) // (2 * elapsed)


def _instant(ts: str) -> datetime:
    when = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return when if when.tzinfo is not None else when.replace(tzinfo=timezone.utc)


# ------------------------------------------------------------------------------------------------ live ad spend
DAY_SECONDS = 86_400
STALE = timedelta(hours=12)        # an import whose last success is older stands for no month still running


@dataclass(frozen=True)
class LiveAds:
    """What a live read changes in an ad budget's window, over the months its platform's import covers: the books lines
    taken out (`books_cut`), the holds at the platform taken out (`holds_cut`) and the platform's daily spend put in
    (`platform_spend`). `live_months` / `books_months`: how many months of the window read the platform / the books;
    `platform`: the budget's platform; `as_of`: the import's last success, when any month reads it."""
    books_cut: int = 0
    holds_cut: int = 0
    platform_spend: int = 0
    live_months: int = 0
    books_months: int = 0
    platform: str = ""
    as_of: str | None = None

    @property
    def spent_from(self) -> str:
        if not self.live_months:
            return "books"
        return "mixed" if self.books_months else "platform"


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
    """The ad platform a vendor key is ("" when none): store/ads.vendor_platform, once per key per call."""
    known: dict[str, str] = _memo(ctx, "ads:vendors", dict[str, str])
    if key not in known:
        known[key] = SAD.vendor_platform(ctx.c, ctx.book.id, key)
    return known[key]


def budget_platform(ctx: Ctx, b: B.Budget) -> str:
    """The ad platform a budget's vendor is ("" when it names no vendor or another one). Such a budget is an ad budget:
    it only warns (warn_only), whatever an import does."""
    if not b.vendor or b.catch_all:
        return ""
    keys = SAP.vendor_keys(ctx.c, ctx.book.id, b.vendor) or [b.vendor]
    return next((p for k in keys if (p := ad_platform(ctx, k))), "")


def warn_only(ctx: Ctx, b: B.Budget) -> B.Budget:
    """The budget as enforcement reads it: an ad budget's at-limit is warn, so it never declines, holds a payment,
    asks or freezes a card (handlers refuse any other; a data fix set the old ones)."""
    return replace(b, at_limit="warn") if b.at_limit != "warn" and budget_platform(ctx, b) else b


def import_covers(ctx: Ctx, imp: SAD.AdImport, lo: str, hi: str) -> bool:
    """Whether an import's spend stands for the days [lo, hi]: it is on, its last run went through, it reads from lo or
    earlier, and its last success came after hi (a month that is over) or within STALE (one still running)."""
    if not imp.enabled or imp.status != "ok" or not imp.last_import_at or imp.history_from > lo:
        return False
    last = _instant(imp.last_import_at)
    if last.date().isoformat() > hi:
        return True
    return hi >= ctx.today and _instant(ctx.at) - last <= STALE


def _months(start: str, end: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    first, last = dates.day(start), dates.day(end)
    while first <= last:
        z = min(dates.month_end(first.replace(day=1)), last)
        out.append((first.isoformat(), z.isoformat()))
        first = date.fromordinal(z.toordinal() + 1)
    return out


def ad_covers(ctx: Ctx, b: B.Budget, s: SAD.LiveSpend, accounts: frozenset[str], teammate: str = "") -> bool:
    """Whether an ad account's spend counts in a budget (scope `accounts`): its platform the budget's, its books line in
    scope, its bot the budget's teammate and on its team (when the budget names them). An account with no bot counts
    only in a budget that names neither."""
    if s.platform != budget_platform(ctx, b) or s.books_account_id not in accounts:
        return False
    bot = "bot:" + s.bot_slug if s.bot_slug else ""
    mate = teammate or b.teammate
    if mate and mate != bot:
        return False
    return not b.team or (bool(bot) and ctx.org.team_of.get(bot, "") in ctx.org.below(b.team))


def _replaced(ctx: Ctx, rows: Sequence[SAD.LiveSpend]) -> bool:
    """Whether the platform's spend stands in for the books: an assigned account in scope, and none in another
    currency than the book's (its spend could not be added, so its charges must stay)."""
    return any(r.bot_slug for r in rows) and all(r.currency == ctx.book.base_currency for r in rows)


def _month_spend(ctx: Ctx, lo: str, hi: str) -> list[SAD.LiveSpend]:
    key = f"ads:spend:{lo}:{hi}"
    if key not in ctx.memo:
        ctx.memo[key] = SAD.live_spend(ctx.c, lo, hi)
    found: list[SAD.LiveSpend] = ctx.memo[key]
    return found


def hold_platform(ctx: Ctx, h: SAD.HeldFor) -> str:
    """The ad platform a hold is for ("" when none): its vendor contact, else its merchant or payee as posted."""
    if h.contact_id and ad_platform(ctx, h.contact_id):
        return ad_platform(ctx, h.contact_id)
    return ad_platform(ctx, SAP.vendor_key(ctx.c, ctx.book.id, h.text)) if h.text else ""


def live_ads(ctx: Ctx, b: B.Budget, start: str, end: str, teammate: str = "", read: Netted | None = None,
             tree: Sequence[str] = ()) -> LiveAds:
    """What a live read changes in an ad budget's window [start, end], month by month (the module docstring). `tree`:
    the budgets whose holds count in it (state passes the subtree; none for `actual`)."""
    platform = budget_platform(ctx, b)
    if not platform:
        return NO_ADS
    imp = ad_imports(ctx).get(platform)
    accounts = frozenset(scope_accounts(ctx, b))
    keys = SAP.vendor_keys(ctx.c, ctx.book.id, b.vendor) or [b.vendor]
    last = min(dates.day(end), horizon(ctx)).isoformat()
    books_cut = holds_cut = spend = live = books = 0
    for lo, hi in _months(start, last):
        rows = [r for r in _month_spend(ctx, lo, hi) if ad_covers(ctx, b, r, accounts, teammate)]
        if imp is None or not import_covers(ctx, imp, lo, hi) or not _replaced(ctx, rows):
            books += 1          # never cut without the platform's figure to put in its place
            continue
        live += 1
        spend += sum(r.amount for r in rows)
        books_cut += _posted(ctx, b, lo, hi, teammate, read, keys)
        if tree:
            holds_cut += sum(h.amount for h in SAD.holds_for(ctx.c, tree, lo, hi) if hold_platform(ctx, h) == platform)
    return LiveAds(books_cut=books_cut, holds_cut=holds_cut, platform_spend=spend, live_months=live,
                   books_months=books, platform=platform, as_of=imp.last_import_at if live and imp else None)


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
    chain = [(warn_only(ctx, b), state(ctx, b.id, at, target, spent_on)) for b in B.chain(ctx.budgets(), leaf_id)]
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
