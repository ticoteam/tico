"""Budgets and holds (spec §4.4): trees that start at a P&L line and split by team or teammate, then by vendor.

- A charge counts against the deepest budget that matches it and every parent of it (most specific wins); the
  catch-all takes expense lines no tree covers. Overlapping siblings are rejected.
- Children share the parent's period and together never pass it.
- Remaining = amount - posted actuals - active holds - unused allocations (§4.4). It refuses new holds and new
  allocations. A hold reserves in the whole chain at once.
- Spend = posted actuals + active holds. Warn and cutoff read spend, not remaining: an unused allocation keeps capacity
  set aside for its target, but it is not spent, so a card whose limit is its whole budget runs until the money is spent.
- Amounts live in versions with an effective-from day; a budget with no version has no amount and never cuts off.
"""
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from . import dates, money
from .errors import INVALID, Named, invalid, named_refusal
from .ledger import PNL_TYPES, Account, ancestors, descendants
from .org import Org

PERIODS = ("month", "quarter", "year", "once")
AT_LIMIT = ("warn", "ask", "decline", "hold_payment")
STRICTNESS = {"warn": 0, "ask": 1, "hold_payment": 2, "decline": 3}
ROLLOVER = ("none", "unused")
TARGETS = ("card", "gateway_key", "teammate")
METHODS = ("trend", "run_rate", "recurring", "fixed", "manual")
HOLD_STATES = ("held", "settled", "released")
ONCE_END = date(9999, 12, 31)


@dataclass(frozen=True)
class Budget:
    id: str
    book_id: str
    name: str
    period: str = "month"
    account_id: str = ""
    catch_all: bool = False
    team: str = ""
    teammate: str = ""
    vendor: str = ""
    parent_id: str = ""
    owner: str = ""
    warn_pct: int = 80
    at_limit: str = "warn"
    rollover: str = "none"
    active: bool = True
    created_at: str = ""


@dataclass(frozen=True)
class Version:
    budget_id: str
    version: int
    effective_from: str
    amount: int


@dataclass(frozen=True)
class Charge:
    """What a spend is for: the P&L account and, when known, the team, teammate and vendor (a contact id)."""
    account_id: str
    team: str = ""
    teammate: str = ""
    vendor: str = ""


@dataclass(frozen=True)
class State:
    budget_id: str
    period_start: str
    period_end: str
    amount: int | None
    actual: int
    held: int                       # active holds plus what unused allocations reserve (remaining counts both)
    spent: int                      # actual + active holds: what warn and cutoff read (never the reserves)
    remaining: int | None
    pct: Decimal | None
    crossed_warn: bool              # spend reached the warn level
    at_limit: bool                  # spend reached the amount (the cutoff)
    excluded: bool = False          # a budget read netted out settled private lines (marked excluded)


@dataclass(frozen=True)
class Decision:
    """What a hold request meets: `allowed`, the strictest at-limit `action` among budgets it would pass ("ok" when it
    passes none), the budget that set it, and every budget it brings to or past its warn level."""
    allowed: bool
    action: str
    limit_budget: str
    warn_budgets: tuple[str, ...]


# ------------------------------------------------------------------------------------------------ periods and amounts
def amount_at(versions: Iterable[Version], at: date) -> int | None:
    """The amount in force on `at`: the latest version effective on or before it."""
    best: Version | None = None
    for v in versions:
        if dates.day(v.effective_from) <= at and (best is None or (v.effective_from, v.version) >
                                                   (best.effective_from, best.version)):
            best = v
    return best.amount if best else None


def period_of(budget: Budget, at: date, fiscal_start: int, versions: Sequence[Version]) -> tuple[date, date]:
    """The budget period holding `at`. A `once` budget runs from its first version (or its creation) with no end."""
    if budget.period == "month":
        return dates.month_bounds(at)
    if budget.period == "quarter":
        return dates.fiscal_bounds(at, 3, fiscal_start)
    if budget.period == "year":
        return dates.fiscal_bounds(at, 12, fiscal_start)
    starts = [dates.day(v.effective_from) for v in versions] or \
        ([dates.day(budget.created_at)] if budget.created_at else [at])
    return min(starts), ONCE_END


def previous(budget: Budget, start: date, fiscal_start: int, versions: Sequence[Version]) -> tuple[date, date]:
    return period_of(budget, start.fromordinal(start.toordinal() - 1), fiscal_start, versions)


def carry(history: Sequence[tuple[int | None, int]]) -> int:
    """What an `unused` rollover brings into the current period from `history` (oldest first, each (amount, spent)):
    each period's unused amount adds to the next, and overspending eats into nothing below zero."""
    carried = 0
    for amount, spent in history:
        if amount is None:
            carried = 0
            continue
        carried = max(0, amount + carried - spent)
    return carried


def in_window(budget: Budget, versions: Sequence[Version], window: tuple[date, date], fiscal_start: int) -> int | None:
    """The budget's amount for a report bucket: each budget period's amount prorated by the days it shares with the
    window (a day or a week of a monthly budget), summed. None for `once` budgets and budgets with no version."""
    if budget.period == "once" or not versions:
        return None
    total, seen, at = 0, False, window[0]
    while at <= window[1]:
        start, end = period_of(budget, at, fiscal_start, versions)
        amount = amount_at(versions, start)
        if amount is not None:
            seen = True
            share = dates.overlap((start, end), window)
            total += money.prorate(amount, share, dates.days(start, end))
        at = end.fromordinal(end.toordinal() + 1)
    return total if seen else None


def state(budget: Budget, period: tuple[date, date], amount: int | None, actual: int, holds: int,
          reserved: int = 0) -> State:
    """A budget's figures. `holds` are its active holds (and settled ones no entry took yet); `reserved` is what unused
    allocations keep set aside in its subtree. Remaining counts both, since it refuses new holds and allocations. Warn
    and cutoff count only spend (actuals and holds): a reserve is capacity for its target, not money out."""
    held = holds + reserved
    used = actual + held
    spent = actual + holds
    remaining = None if amount is None else amount - used
    return State(budget_id=budget.id, period_start=period[0].isoformat(), period_end=period[1].isoformat(),
                 amount=amount, actual=actual, held=held, spent=spent, remaining=remaining,
                 pct=money.percent(used, amount),
                 crossed_warn=amount is not None and amount > 0 and spent * 100 >= amount * budget.warn_pct,
                 at_limit=amount is not None and spent >= amount)


def decide(chain: Sequence[tuple[Budget, State]], amount: int, past_reserves: bool = False) -> Decision:
    """A hold of `amount` against a budget and every parent at once. A hold the budget has no remaining room for (reserves
    count, §4.4) passes into its at-limit action; the strictest one wins, and only `warn` lets the hold through. The warn
    level is read on spend, as the crossing event is, so an unused allocation never warns a budget by itself. A refund
    (negative amount) always goes through. `past_reserves` fits the hold against spend alone: a run claim is work, not a
    request for budget, so what unused allocations set aside does not refuse it (only a budget at its limit does)."""
    action, limit = "ok", ""
    warns: list[str] = []
    for budget, s in chain:
        if s.amount is None:
            continue
        room = s.amount - s.spent if past_reserves else s.remaining
        if amount > 0 and room is not None and amount > room and \
                (action == "ok" or STRICTNESS[budget.at_limit] > STRICTNESS[action]):
            action, limit = budget.at_limit, budget.id
        if amount > 0 and s.amount > 0 and (s.spent + amount) * 100 >= s.amount * budget.warn_pct:
            warns.append(budget.id)
    return Decision(allowed=action in ("ok", "warn"), action=action, limit_budget=limit, warn_budgets=tuple(warns))


# ------------------------------------------------------------------------------------------------ the tree
def chain(budgets: Mapping[str, Budget], leaf: str) -> list[Budget]:
    """`leaf` and its parents, nearest first."""
    out: list[Budget] = []
    at = leaf
    while at in budgets and all(b.id != at for b in out):
        out.append(budgets[at])
        at = budgets[at].parent_id
    return out


def matches(budget: Budget, charge: Charge, accounts: Mapping[str, Account], org: Org) -> bool:
    if budget.catch_all or not budget.active:
        return False
    return budget.account_id in ancestors(accounts, charge.account_id) and \
        (not budget.team or charge.team in org.below(budget.team)) and \
        (not budget.teammate or charge.teammate == budget.teammate) and \
        (not budget.vendor or charge.vendor == budget.vendor)


def covered(budgets: Mapping[str, Budget], accounts: Mapping[str, Account]) -> set[str]:
    """Every account some tree starts at or below: the catch-all takes the other expense accounts."""
    out: set[str] = set()
    for b in budgets.values():
        if b.active and not b.catch_all and not b.parent_id:
            out |= descendants(accounts, b.account_id)
    return out


def catch_all_accounts(budgets: Mapping[str, Budget], accounts: Mapping[str, Account]) -> set[str]:
    taken = covered(budgets, accounts)
    return {a.id for a in accounts.values() if a.type == "expense" and a.id not in taken}


def find(budgets: Mapping[str, Budget], charge: Charge, accounts: Mapping[str, Account], org: Org) -> Budget | None:
    """The budget a charge belongs to: the deepest matching one, else the catch-all for an uncovered expense line."""
    best: Budget | None = None
    best_depth = -1
    for b in budgets.values():
        if matches(b, charge, accounts, org):
            depth = len(chain(budgets, b.id))
            if depth > best_depth:
                best, best_depth = b, depth
    if best is not None:
        return best
    account = accounts.get(charge.account_id)
    if account is None or account.type != "expense" or charge.account_id in covered(budgets, accounts):
        return None
    return next((b for b in budgets.values() if b.catch_all and b.active), None)


def _teams_meet(org: Org, a: str, b: str) -> bool:
    return not a or not b or a in org.below(b) or b in org.below(a)


def overlaps(a: Budget, b: Budget, accounts: Mapping[str, Account], org: Org) -> bool:
    """Whether one charge could count against both `a` and `b`."""
    if a.catch_all or b.catch_all:
        return a.catch_all and b.catch_all
    if a.account_id not in ancestors(accounts, b.account_id) and b.account_id not in ancestors(accounts, a.account_id):
        return False
    if not _teams_meet(org, a.team, b.team):
        return False
    if a.teammate and b.teammate and a.teammate != b.teammate:
        return False
    for one, other in ((a, b), (b, a)):
        if one.teammate and not one.team and other.team and not other.teammate and \
                org.team_of.get(one.teammate, "") not in org.below(other.team):
            return False
    return not (a.vendor and b.vendor and a.vendor != b.vendor)


def clash(budgets: Mapping[str, Budget], accounts: Mapping[str, Account], org: Org) -> tuple[Budget, Budget] | None:
    """Two active sibling budgets one charge could count against (what renaming a vendor to another's name makes)."""
    live = [b for b in budgets.values() if b.active and not b.catch_all]
    for i, a in enumerate(live):
        for b in live[i + 1:]:
            if a.parent_id == b.parent_id and a.book_id == b.book_id and overlaps(a, b, accounts, org):
                return a, b
    return None


def _unseen(_budget: Budget) -> bool:
    return False


def check(new: Budget, budgets: Mapping[str, Budget], accounts: Mapping[str, Account], org: Org,
          seen: Callable[[Budget], bool] = _unseen) -> None:
    """A new or changed budget's place in its tree. Raises `invalid` for anything that would let a charge count twice
    or a child sit outside its parent. `seen` says which budgets the asker may see: a sibling is named in the refusal
    only where it is seen, else the refusal says another budget."""
    if new.period not in PERIODS:
        raise invalid(f"a budget period is {'|'.join(PERIODS)}")
    if new.at_limit not in AT_LIMIT:
        raise invalid(f"at_limit is {'|'.join(AT_LIMIT)}")
    if new.rollover not in ROLLOVER:
        raise invalid(f"rollover is {'|'.join(ROLLOVER)}")
    if not 1 <= new.warn_pct <= 100:
        raise invalid("warn_pct is 1 to 100")
    if new.rollover == "unused" and new.period == "once":
        raise invalid("a once budget has nothing to roll over")
    if new.team and new.team not in org.teams:
        raise invalid("no such team")
    if new.teammate and new.teammate not in org.humans | org.bots:
        raise invalid("no such teammate")
    others = {k: v for k, v in budgets.items() if k != new.id and v.active}
    if new.catch_all:
        if new.account_id or new.team or new.teammate or new.vendor or new.parent_id:
            raise invalid("the catch-all budget has no line, team, teammate, vendor or parent")
        if any(b.catch_all and b.book_id == new.book_id for b in others.values()):
            raise invalid("there is already a catch-all budget")
        return
    account = accounts.get(new.account_id)
    if account is None or account.book_id != new.book_id or account.type not in PNL_TYPES:
        raise invalid("a budget hangs off a P&L line of this book")
    if not new.parent_id:
        if new.team or new.teammate or new.vendor:
            raise invalid("a budget tree starts at a line; split it by team, teammate or vendor under that")
    else:
        parent = budgets.get(new.parent_id)
        if parent is None or not parent.active or parent.catch_all or parent.book_id != new.book_id:
            raise invalid("no such parent budget")
        if new.id in {b.id for b in chain(budgets, parent.id)}:
            raise invalid("a budget cannot sit under itself")
        if parent.period != new.period:
            raise invalid("a child budget has its parent's period")
        if new.account_id not in descendants(accounts, parent.account_id):
            raise invalid("a child budget is on its parent's line or a line under it")
        if parent.team and new.team not in org.below(parent.team):
            raise invalid("a child budget is inside its parent's team")
        if parent.teammate and new.teammate != parent.teammate:
            raise invalid("a child budget is inside its parent's teammate")
        if parent.vendor and new.vendor != parent.vendor:
            raise invalid("a child budget is inside its parent's vendor")
        if parent.vendor and (new.team != parent.team or new.teammate != parent.teammate):
            raise invalid("split by team or teammate before vendor")
        if (new.account_id, new.team, new.teammate, new.vendor) == \
                (parent.account_id, parent.team, parent.teammate, parent.vendor):
            raise invalid("a child budget is narrower than its parent")
    for sibling in others.values():
        if sibling.parent_id == new.parent_id and not sibling.catch_all and sibling.book_id == new.book_id and \
                overlaps(new, sibling, accounts, org):
            named = Named((sibling.id,), "it overlaps budget {0}", "it overlaps another budget")
            raise named_refusal(INVALID, named, [sibling.name], seen(sibling))


def fit_points(change_from: date, versions: Iterable[Version]) -> list[date]:
    """The days on which a change from `change_from` on could make children pass their parent: that day and every
    later version change."""
    points = {change_from} | {dates.day(v.effective_from) for v in versions if dates.day(v.effective_from) > change_from}
    return sorted(points)


def check_fit(parent: Budget, parent_versions: Sequence[Version], children: Mapping[str, Sequence[Version]],
              points: Iterable[date]) -> None:
    """Children's amounts together never pass the parent's on any of `points`. A parent with no amount bounds
    nothing."""
    for at in points:
        cap = amount_at(parent_versions, at)
        if cap is None:
            continue
        total = sum(amount_at(v, at) or 0 for v in children.values())
        if total > cap:
            named = Named((parent.id,), f"its children would total {total}, more than {{0}}'s {cap}",
                          "it does not fit: not enough left", numbers=True, on=at.isoformat())
            # The caller re-raises this only where the asker may see the parent's numbers (handlers._fit).
            raise named_refusal(INVALID, named, [parent.name], shown=True)
