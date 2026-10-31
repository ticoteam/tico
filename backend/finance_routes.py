"""Finance over HTTP (API v2, spec §5 and §8): budgets, the P&L, expenses and one line's detail, each cut to the caller's
own view; requesting a Finance action (or a dry run of one); and reading an action's status. The Finance tab and the
hub tools call these.

A write goes through `mutate`, and its Idempotency-Key is the action's idempotency key too. `store.mutate` replays a
saved answer without running the call again, so the saved answer is only the action's id, kind and status: the first
call also carries the result it just made, and a retry (the saved answer, or once it has lapsed Finance's own replay of
the action) reads the rest afresh through the caller's current view: a reason that names amounts shows only while that
view still covers them."""
from typing import Any, Literal

from fastapi import Query, Request
from pydantic import Field

from .finance import actions as finance
from .finance import reads as finance_reads
from .models import ID, Contract
from .store import Problem

STATUS = {"not_found": 404, "denied": 403, "invalid": 422, "conflict": 409, "not_available": 409,
          "period_closed": 409, "unbalanced": 422}
Group = Literal["department", "team", "teammate", "bot", "person", "vendor", "account"]


class FinanceActionRequest(Contract):
    """One Finance action: its kind (`budget.create`, `entry.post`, ...), its payload, and whether only to say what would
    happen."""
    kind: str = Field(min_length=1, max_length=80)
    payload: dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = False


def problem(e):
    return Problem(e.code, e.message, STATUS.get(e.code, 400))


def install(app, store, auth, mutate):
    def viewer(request):
        who = request.state.identity
        auth.domain(who)
        return who

    def read(fn):
        try:
            with store.read() as c:
                return fn(c)
        except finance.FinanceError as e:
            raise problem(e) from None

    @app.get("/api/v2/finance/budgets")
    def finance_budgets(request: Request, include_inactive: bool = False, limit: int = Query(200, ge=1, le=500)):
        """The budgets the caller sees, each with where it stands today (amount, actual, held, remaining)."""
        who = viewer(request)
        return read(lambda c: finance_reads.budgets(c, who.actor, include_inactive=include_inactive, limit=limit))

    @app.get("/api/v2/finance/pnl")
    def finance_pnl(request: Request, start: str | None = None, end: str | None = None,
                    period: Literal["month", "quarter", "year"] = "month",
                    basis: Literal["cash", "accrual"] = "cash"):
        """The P&L the caller sees: income and expense lines by period, with budget and projection beside each actual."""
        who = viewer(request)
        return read(lambda c: finance_reads.pnl(c, who.actor, start, end, period, basis))

    @app.get("/api/v2/finance/expenses")
    def finance_expenses(request: Request, start: str | None = None, end: str | None = None,
                         by: Group = "department", account_id: str | None = None, team: str | None = None,
                         teammate: str | None = None, top_n: int = Query(20, ge=1, le=200)):
        """Expenses the caller sees, grouped by department, team, teammate, bot, person, vendor or line."""
        who = viewer(request)
        return read(lambda c: finance_reads.expenses(c, who.actor, start, end, by, account_id=account_id, team=team,
                                                     teammate=teammate, top_n=top_n))

    @app.get("/api/v2/finance/lines/{account_id}")
    def finance_line(request: Request, account_id: str, start: str | None = None, end: str | None = None,
                     limit: int = Query(50, ge=1, le=200), top_n: int = Query(10, ge=1, le=200)):
        """One line's spend by vendor, team and teammate, and its entries, as far as the caller sees."""
        who = viewer(request)
        return read(lambda c: finance_reads.line_detail(c, who.actor, account_id, start, end, limit=limit,
                                                        top_n=top_n))

    @app.post("/api/v2/finance/actions")
    def finance_act(request: Request, body: FinanceActionRequest):
        """Request a Finance action: committed, sent to one person for approval, denied or failed. The Idempotency-Key
        is the action's key, so a retry never acts twice. `dry_run` writes nothing and says what would happen."""
        who = viewer(request)
        key = (request.headers.get("idempotency-key") or "").strip()
        if body.dry_run:
            try:
                with store.transaction() as c:
                    return finance.act(c, who.actor, body.kind, body.payload, key or "dry-run", dry_run=True).as_dict()
            except finance.FinanceError as e:
                raise problem(e) from None
        first = {}

        def work(c):
            done = finance.act(c, who.actor, body.kind, body.payload, key)
            first["result"] = done
            return {"action_id": done.action_id, "kind": done.kind, "status": done.status}
        saved = mutate(request, body, work)
        done = first.get("result")
        if done is not None and not done.replayed and done.action_id == saved.get("action_id"):
            return {**saved, "code": done.code, "reason": done.reason, "approval_id": done.approval_id,
                    "approver": done.approver, "rule": done.rule, "result": dict(done.result),
                    "impact": dict(done.impact)}
        if not saved.get("action_id"):
            return saved
        with store.read() as c:
            now = finance_reads.action_for(c, who.actor, saved["action_id"])
        return {**saved, **now} if now else saved

    @app.get("/api/v2/finance/actions/{action_id}")
    def finance_action(request: Request, action_id: ID):
        """An action's status, for whoever asked for it, the person named to decide its approval, or whoever's current
        view covers what it is about. Its reason shows to the approver, and to others only while their view covers it."""
        who = viewer(request)
        with store.read() as c:
            found = finance_reads.action_for(c, who.actor, action_id)
        if found is None:
            raise Problem("not_found", "No such Finance action", 404)
        return found
