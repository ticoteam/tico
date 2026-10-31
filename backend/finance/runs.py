"""Run holds (spec §9): a bot's claim reserves its likely cost on the AI services budget that covers it, in the claim's
own transaction, and the run's end settles that hold at the run's priced Gateway cost, or releases it when nothing was
Gateway-billed (own keys, sign-ins and subscriptions post nothing from usage). A settled hold counts until the daily
usage posting takes it in, so AI spend counts against the budget as it happens and never twice."""
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from . import budgeting
from .context import load
from .core.org import SYSTEM
from .setup import AI
from .store import budgets as SB
from .store import ledger as SL
from .store import tico
from .usage import cents, nano

FLOOR = 5                       # cents held for a bot with no Gateway-billed history
RECENT_RUNS = 20
RECENT_DAYS = 30
EXPIRES_HOURS = 12              # a run that never reports back frees its hold after this


@dataclass(frozen=True)
class RunHold:
    """What a claim's hold came to: `refused` when a `decline` budget at its limit says no (the claim then takes no
    run), with the budget's name for the paused reason."""
    refused: bool = False
    hold_id: str | None = None
    budget: str = ""


def source_id(job_id: str) -> str:
    return "job:" + job_id


def size(c: sqlite3.Connection, bot: str, at: str) -> int:
    """Cents to hold for one run of `bot`: its recent average Gateway-billed cost, never under FLOOR."""
    average = tico.bot_average_run(c, bot, tico.shift(at, days=-RECENT_DAYS), RECENT_RUNS)
    return max(FLOOR, cents(nano(average)) if average is not None else 0)


def hold(c: sqlite3.Connection, bot: str, job_id: str, at: str | None = None) -> RunHold:
    """Hold the run's likely cost at claim. When the full amount would pass a `decline` budget that still has money
    left, the hold takes what is left instead: only a budget at its limit refuses the run."""
    book = SL.book(c)
    if book is None:
        return RunHold()
    account = next((a.id for a in SL.accounts(c, book.id).values() if a.code == AI), None)
    live = [b.id for b in SB.budgets(c, book.id).values() if b.active]
    versions = SB.versions(c, live) if account is not None and live else {}
    if not any(versions.get(bid) for bid in live):
        return RunHold()            # no budget has an amount: nothing to hold against
    at = at or tico.now()
    scope = {"account_id": account, "teammate": "bot:" + bot}
    expires = _hours_after(at, EXPIRES_HOURS)
    amount = size(c, bot, at)
    decision = budgeting.hold_place(c, budget_scope=scope, amount=amount, currency=book.base_currency, source="run",
                                    source_id=source_id(job_id), expires_at=expires, at=at)
    if not decision.allowed and decision.action == "decline" and decision.limit_budget_id:
        left = budgeting.budget_state(c, decision.limit_budget_id, at).remaining
        if left is not None and 0 < left < amount:
            decision = budgeting.hold_place(c, budget_scope=scope, amount=left, currency=book.base_currency,
                                            source="run", source_id=source_id(job_id), expires_at=expires, at=at)
    if decision.allowed or decision.hold_id:
        return RunHold(hold_id=decision.hold_id)    # held, or a retried claim keeps the hold its job already has
    if decision.action != "decline":
        return RunHold()            # ask or hold_payment: a run is not a payment, so it goes ahead without a hold
    limit = SB.budgets(c, book.id).get(decision.limit_budget_id or "")
    return RunHold(refused=True, budget=limit.name if limit else "AI services")


def ended(c: sqlite3.Connection, job_id: str, turn_id: str, at: str | None = None) -> str:
    """Settle the job's live run hold at the run's priced Gateway cost, or release it when nothing was Gateway-billed.
    `settled`, `released` or `none`."""
    if SL.book(c) is None:
        return "none"
    row = SB.hold_for_source(c, "run", source_id(job_id))
    if row is None:
        return "none"
    ctx = load(c, at)
    cost = tico.run_gateway_cost(c, turn_id)
    if cost is None:
        budgeting.release(ctx, str(row["id"]))
        return "released"
    budgeting.settle(ctx, str(row["id"]), max(0, cents(nano(cost))), None, SYSTEM)
    return "settled"


def _hours_after(at: str, hours: int) -> str:
    moment = datetime.fromisoformat(at.replace("Z", "+00:00"))
    return (moment + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
