"""Finance's short scheduler jobs (spec §7.3, §9), each one step inside the caller's transaction and none touching the
network: setting Finance up on an install that lacks it, draining `fin_events` into Tico's rules and automations with
the budget owner's notice, opening next month's period, and what the Gateway cap should be."""
import sqlite3
from collections.abc import Mapping
from typing import cast

from . import handlers
from .budgeting import LIMIT_EVENT, WARN_EVENT, actual, event_period, state, subtree
from .context import is_set_up, load
from .core import budgets as B
from .core import dates
from .posting import ensure_period
from .setup import AI, VIDEO, setup
from .store import budgets as SB
from .store import ledger as SL
from .store import tico
from .store import usage as SU

DRAIN_MOST = 200
PERIOD_WORDS = {"month": "month", "quarter": "quarter", "year": "year", "once": "budget"}
CAP_KEY = "gateway_cap"                 # what the Gateway cap worker last pushed, and when it may try again


def ensure_setup(c: sqlite3.Connection, owner_email: str = "", at: str | None = None) -> bool:
    """Set Finance up for the install owner (the stored owner record, else `owner_email`'s person). Idempotent; False
    when the install has no owner yet."""
    owner = tico.owner_person(c) or tico.person_by_email(c, owner_email)
    if not owner:
        return False
    setup(c, owner, at)
    return True


def _words(b: B.Budget, name: str, bots: bool, refuses: bool = True) -> str:
    """The owner's notice. No amounts: the words name the budget and what happens next (`refuses`: something stops
    new spend under it; nothing stops image making at a Video production cutoff yet)."""
    period = PERIOD_WORDS.get(b.period, b.period)
    if name == WARN_EVENT:
        return f"The {b.name} budget is at {b.warn_pct}% of this {period}'s amount."
    if b.at_limit == "decline":
        if bots:
            return (f"The {b.name} budget is used up for this {period}: the bots it covers take no new work until it "
                    f"is raised or the {period} ends.")
        if refuses:
            return (f"The {b.name} budget is used up for this {period}: new spend under it is refused until it is "
                    "raised.")
    if b.at_limit == "ask":
        return f"The {b.name} budget is used up for this {period}: new spend under it needs approval."
    return f"The {b.name} budget is used up for this {period}."


def drain(c: sqlite3.Connection, at: str | None = None, most: int = DRAIN_MOST) -> int:
    """Hand waiting `finance.*` events to rules (`judge_event`) and automations, ids only, so whoever acts reads the
    budget through their own view; and tell a budget's owner at warn and at cutoff. How many were drained."""
    rows = SU.undrained(c, most)
    if not rows:
        return 0
    ctx = load(c, at) if is_set_up(c) else None
    lines = {a.code: a.id for a in ctx.accounts.values() if a.code in (AI, VIDEO)} if ctx is not None else {}
    if any(str(r["name"]) == LIMIT_EVENT for r in rows):
        from . import gateway_sync
        tico.after_commit(c, gateway_sync.ring)
    for row in rows:
        name, subject, eid = str(row["name"]), str(row["subject"]), str(row["id"])
        tico.emit(c, name, subject, {"budget_id": subject, "event_id": eid,
                                        "period": event_period(str(row["period_key"]))})
        b = ctx.budgets().get(subject) if ctx is not None else None
        if ctx is not None and b is not None and name in (WARN_EVENT, LIMIT_EVENT):
            owner = b.owner if b.owner.startswith("human:") and handlers.sees_budget(ctx, b.owner, b) else ctx.org.owner
            if owner:
                c.execute("SAVEPOINT fin_notice")
                try:
                    tico.notice(c, owner, _words(b, name, b.account_id == lines.get(AI),
                                                 b.account_id != lines.get(VIDEO)))
                    c.execute("RELEASE fin_notice")
                except tico.Refused:
                    c.execute("ROLLBACK TO fin_notice")
                    c.execute("RELEASE fin_notice")
        SU.drained(c, eid, ctx.at if ctx is not None else tico.now())
    return len(rows)


def open_next_period(c: sqlite3.Connection, at: str | None = None) -> str | None:
    """Make next month's period ahead of its first posting. Its id, or None before Finance is set up."""
    if not is_set_up(c):
        return None
    ctx = load(c, at)
    return ensure_period(ctx, dates.add_months(dates.day(ctx.today).replace(day=1), 1).isoformat())


def company_ai_budget(c: sqlite3.Connection, at: str | None = None) -> tuple[str, B.State, int] | None:
    """The company-wide AI services budget (no team, teammate or vendor) when it has an amount: its at-limit action,
    its state now, and what is really spent against it (actuals and holds; money set aside in allocations and not used
    is not spent)."""
    if not is_set_up(c):
        return None
    ctx = load(c, at)
    ai = next((a.id for a in ctx.accounts.values() if a.code == AI), "")
    for b in ctx.budgets().values():
        if b.active and not b.parent_id and b.account_id == ai and not (b.team or b.teammate or b.vendor):
            st = state(ctx, b.id, dates.day(ctx.today))
            if st.amount is None:
                return None
            spent = actual(ctx, b, st.period_start, st.period_end) + \
                SB.held(c, subtree(ctx, b.id), st.period_start, st.period_end)
            return b.at_limit, st, spent
    return None


def cap_wanted(c: sqlite3.Connection, at: str | None = None) -> tuple[str, str]:
    """What the Gateway self cap should be now, as (`cut` | `clear`, the Gateway month `YYYY-MM`): `cut` while the
    company AI services budget is a `decline` budget whose spending (not its unused allocations) reached its amount.
    The month is the calendar month even for a quarter, year or one-time budget: the Gateway's spend resets monthly,
    so a cut made in October must be made again at November's spend."""
    found = company_ai_budget(c, at)
    month = (at or tico.now())[:7]
    if found is None:
        return "clear", month
    action, st, spent = found
    over = st.amount is not None and spent >= st.amount
    return ("cut" if action == "decline" and over else "clear"), month


def cap_state(c: sqlite3.Connection) -> dict[str, object]:
    stored = SL.setting(c, CAP_KEY)
    return dict(cast(Mapping[str, object], stored)) if isinstance(stored, Mapping) else {}


def cap_record(c: sqlite3.Connection, state: Mapping[str, object], at: str) -> None:
    SL.set_setting(c, CAP_KEY, dict(state), "keeper", at)
