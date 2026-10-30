"""Finance's one write path (spec §5). Every change is an action: checked against rights, view and the default gates,
then committed, sent to one person for approval, denied or failed, and recorded in `fin_actions` with its audit row
in the same transaction. Callers outside backend/finance write only through here.

Approvals are Tico approvals with no task, kind `finance` (`spend` is kept for money out, which phase 0 has none of;
`spend_limit` is never used, since approving one applies a usage-cap proposal). The payload carries the action id.
When a decision arrives, `approval_decided` verifies the approval is the one Finance asked for, re-checks rights, view
and budgets, consumes it, and commits, all in one transaction."""
import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, field

from . import budgeting, handlers, posting, setup as setup_mod
from .context import Ctx, load
from .core import kinds as K
from .core import org as O
from .core.errors import DENIED, INVALID, NOT_AVAILABLE, NOT_FOUND, CONFLICT, FinanceError
from .store import actions as SX
from .store import tico

# What part B and the HTTP routes call.
setup = setup_mod.setup
SetupResult = setup_mod.SetupResult
post_entry = posting.post_entry
HoldDecision = budgeting.HoldDecision
hold_place = budgeting.hold_place
hold_settle = budgeting.hold_settle
hold_release = budgeting.hold_release
holds_expire = budgeting.holds_expire
budget_state = budgeting.budget_state
budget_for = budgeting.budget_for
SYSTEM = O.SYSTEM
Principal = O.Principal

STATUSES = ("committed", "needs_approval", "denied", "failed", "expired", "withdrawn")
DENIAL_CODES = (DENIED, NOT_AVAILABLE)
SOURCE = "finance"                    # marks a Tico approval as Finance's, for the decide routes to route
MAX_KEY = 200
STALE_DAYS = 14                        # hubdb.APPROVAL_STALE_DAYS: a withdrawal after this long is an expiry


@dataclass(frozen=True)
class ActionResult:
    status: str
    kind: str
    action_id: str | None = None
    result: Mapping[str, object] = field(default_factory=lambda: dict[str, object]())
    code: str = ""
    reason: str = ""
    approval_id: str | None = None
    approver: str | None = None
    rule: str = ""
    dry_run: bool = False
    impact: Mapping[str, object] = field(default_factory=lambda: dict[str, object]())

    def as_dict(self) -> dict[str, object]:
        return {"status": self.status, "kind": self.kind, "action_id": self.action_id, "result": dict(self.result),
                "code": self.code, "reason": self.reason, "approval_id": self.approval_id, "approver": self.approver,
                "rule": self.rule, "dry_run": self.dry_run, "impact": dict(self.impact)}


def _canonical(payload: object) -> dict[str, object]:
    """The payload as stored and hashed: plain JSON, keys sorted."""
    if not isinstance(payload, Mapping):
        raise FinanceError(INVALID, "payload is an object")
    try:
        loaded: object = json.loads(json.dumps(payload, sort_keys=True))
    except (TypeError, ValueError):
        raise FinanceError(INVALID, "payload is plain JSON") from None
    assert isinstance(loaded, dict)
    return {str(k): v for k, v in loaded.items()}  # pyright: ignore[reportUnknownVariableType]


def _stored(row: Mapping[str, object]) -> ActionResult:
    result = json.loads(str(row["result_json"] or "{}"))
    return ActionResult(status=str(row["status"]), kind=str(row["kind"]), action_id=str(row["id"]),
                        result={k: v for k, v in result.items() if k != "what"}, code=str(row["code"] or ""),
                        reason=str(row["reason"] or ""), approval_id=str(row["approval_id"] or "") or None,
                        approver=str(row["approval_to"] or "") or None, rule=str(row["rule_id"] or ""))


def _approval_payload(action_id: str, kind: str, payload: Mapping[str, object], what: str) -> dict[str, object]:
    return {"source": SOURCE, "action_id": action_id, "kind": kind, "what": what, "payload": dict(payload)}


def _audit(c: sqlite3.Connection, p: O.Principal, action_id: str, kind: str, status: str, code: str = "",
           extra: Mapping[str, object] | None = None) -> None:
    tico.audit(c, p.actor, f"finance.{kind}", action_id,
               {"status": status, "code": code, "on_behalf_of": p.for_actor, "rule": p.rule, **(extra or {})})


def _savepoint(c: sqlite3.Connection, name: str) -> None:
    c.execute(f"SAVEPOINT {name}")


def _rollback(c: sqlite3.Connection, name: str) -> None:
    c.execute(f"ROLLBACK TO SAVEPOINT {name}")
    c.execute(f"RELEASE SAVEPOINT {name}")


def _release(c: sqlite3.Connection, name: str) -> None:
    c.execute(f"RELEASE SAVEPOINT {name}")


def _requester(c: sqlite3.Connection, who: str) -> str:
    """Who the Tico approval is asked by: the teammate, or the keeper for system work."""
    return who if who != O.SYSTEM and tico.known_actor(c, who) else "keeper"


def _plan(ctx: Ctx, p: O.Principal, kind: str, payload: Mapping[str, object]) -> tuple[K.Gate, handlers.Plan]:
    """The right check first (a missing right is a denial, or for budgets a request to the asker's manager), then the
    kind's own checks and default gate."""
    by_right = K.authority(ctx.org, p.who, kind, ctx.rights(p.who))
    if by_right is not None and by_right.outcome == "deny":
        raise FinanceError(DENIED, f"{kind} needs {by_right.rule.removeprefix('right:')}")
    plan = handlers.PLANNERS[kind](ctx, p.who, payload)
    return by_right or plan.gate, plan


def act(c: sqlite3.Connection, principal: O.Principal | str, kind: str, payload: Mapping[str, object],
        idempotency_key: str, dry_run: bool = False, at: str | None = None) -> ActionResult:
    """Request one Finance action. The same key returns the first result and does nothing again. A dry run writes
    nothing and says what would happen: the outcome, the rule, the approver and the budget impact."""
    p = O.principal(principal)
    key = (idempotency_key or "").strip()
    if not key or len(key) > MAX_KEY:
        return ActionResult(status="failed", kind=kind, code=INVALID, reason=f"an idempotency key is 1-{MAX_KEY} chars")
    try:
        body = _canonical(payload)
    except FinanceError as e:
        return ActionResult(status="failed", kind=kind, code=e.code, reason=e.message)
    if not dry_run:
        prior = SX.by_key(c, key)
        if prior is not None:
            if prior["kind"] != kind or json.loads(prior["payload_json"]) != body or prior["actor"] != p.actor:
                return ActionResult(status="failed", kind=kind, code=CONFLICT,
                                    reason="that idempotency key was used for a different request")
            return _stored(prior)
    action_id = tico.new_id()
    try:
        if kind not in K.ALL:
            raise FinanceError(INVALID, f"no Finance action {kind}")
        if kind not in K.LIVE:
            raise FinanceError(NOT_AVAILABLE, f"{kind} is not available yet")
        ctx = load(c, at)
    except FinanceError as e:
        return _finish_refused(c, p, kind, body, key, action_id, e, dry_run, at)
    _savepoint(c, "fin_act")
    try:
        gate, plan = _plan(ctx, p, kind, body)
        approver = ""
        if gate.outcome == "needs_approval":
            approver = O.approver(ctx.org, gate.who, p.who, ctx.approver_setting())
            if not approver:
                raise FinanceError(DENIED, "nobody can approve this: the install has no owner")
            if approver == p.who:
                approver = ""          # the one who would decide asked: nothing to ask
        would = "needs_approval" if approver else "committed"
        if dry_run:
            _rollback(c, "fin_act")
            return ActionResult(status=would, kind=kind, approver=approver or None, rule=gate.rule, dry_run=True,
                                impact={**plan.impact, "summary": plan.summary})
        if approver:
            what = f"{plan.summary} (Finance action {action_id})"
            SX.insert(c, action_id=action_id, kind=kind, payload=body, actor=p.actor, on_behalf_of=p.for_actor,
                      rule_id=gate.rule, key=key, rules=[{"rule": gate.rule, "outcome": gate.outcome}],
                      status="needs_approval", result={"what": what}, code="", reason="", by=p.actor, at=ctx.at)
            approval_id = _ask(ctx, action_id, kind, body, what, p.who, approver, gate.rule)
            _audit(c, p, action_id, kind, "needs_approval", extra={"approval_id": approval_id, "to": approver})
            _release(c, "fin_act")
            return ActionResult(status="needs_approval", kind=kind, action_id=action_id, approval_id=approval_id,
                                approver=approver, rule=gate.rule, impact=plan.impact)
        SX.insert(c, action_id=action_id, kind=kind, payload=body, actor=p.actor, on_behalf_of=p.for_actor,
                  rule_id=gate.rule, key=key, rules=[{"rule": gate.rule, "outcome": "allow"}], status="committed",
                  result={}, code="", reason="", by=p.actor, at=ctx.at)
        result = plan.apply(action_id)
        SX.finish(c, action_id, status="committed", result=result, code="", reason="", at=ctx.at)
        _audit(c, p, action_id, kind, "committed")
        _release(c, "fin_act")
        return ActionResult(status="committed", kind=kind, action_id=action_id, result=result, rule=gate.rule,
                            impact=plan.impact)
    except FinanceError as e:
        _rollback(c, "fin_act")
        return _finish_refused(c, p, kind, body, key, action_id, e, dry_run, at)
    except (sqlite3.IntegrityError, tico.Refused) as e:
        _rollback(c, "fin_act")
        return _finish_refused(c, p, kind, body, key, action_id, FinanceError("failed", str(e)[:300]), dry_run, at)
    except BaseException:
        _rollback(c, "fin_act")
        raise


def _finish_refused(c: sqlite3.Connection, p: O.Principal, kind: str, body: Mapping[str, object], key: str,
                    action_id: str, e: FinanceError, dry_run: bool, at: str | None) -> ActionResult:
    """A denial or failure is recorded under its key too, so a retry gets the same answer."""
    status = "denied" if e.code in DENIAL_CODES else "failed"
    if dry_run:
        return ActionResult(status=status, kind=kind, code=e.code, reason=e.message, dry_run=True)
    when = at or tico.now()
    SX.insert(c, action_id=action_id, kind=kind, payload=body, actor=p.actor, on_behalf_of=p.for_actor,
              rule_id=p.rule, key=key, rules=[], status=status, result={}, code=e.code, reason=e.message, by=p.actor,
              at=when)
    _audit(c, p, action_id, kind, status, e.code)
    return ActionResult(status=status, kind=kind, action_id=action_id, code=e.code, reason=e.message)


def _ask(ctx: Ctx, action_id: str, kind: str, body: Mapping[str, object], what: str, who: str, approver: str,
         rule: str) -> str:
    requester = _requester(ctx.c, who)
    payload = _approval_payload(action_id, kind, body, what)
    approval_id = tico.approval_request(ctx.c, requester, K.approval_kind(kind), payload, approver)
    SX.asked(ctx.c, action_id, approval_id=approval_id, approval_hash=tico.payload_hash(payload), requester=requester,
             to=approver, rule_id=rule, at=ctx.at)
    return approval_id


# ------------------------------------------------------------------------------------------------ decisions
def verify(row: Mapping[str, object], appr: Mapping[str, object]) -> str:
    """Whether an approved Tico approval is the one this action waits on, decided by the person it was sent to, for
    exactly the payload about to run. "" when it is; "payload" when the payload differs (ask again); else why not."""
    if appr.get("kind") != K.approval_kind(str(row["kind"])):
        return "it is a different kind of approval"
    if appr.get("requested_by") != row["approval_by"]:
        return "Finance did not ask for it"
    decided_by = str(appr.get("decided_by") or "")
    if not decided_by.startswith("human:") or decided_by != row["approval_to"]:
        return "it was decided by someone other than the person it was sent to"
    if appr.get("consumed_at"):
        return "it was already used"
    result = json.loads(str(row["result_json"] or "{}"))
    expected = tico.payload_hash(_approval_payload(str(row["id"]), str(row["kind"]),
                                                   json.loads(str(row["payload_json"])), str(result.get("what", ""))))
    if appr.get("payload_hash") != expected or row["approval_hash"] != expected:
        return "payload"
    return ""


def _close(ctx: Ctx, row: Mapping[str, object], status: str, code: str, reason: str) -> ActionResult:
    """End a waiting action: it releases any hold it reserved, and the key's result is final (ask again with a new
    key)."""
    action_id = str(row["id"])
    budgeting.release_for(ctx, "action", action_id)
    SX.finish(ctx.c, action_id, status=status, result={}, code=code, reason=reason, at=ctx.at)
    p = O.Principal(actor=str(row["actor"]), for_actor=str(row["on_behalf_of"] or ""))
    _audit(ctx.c, p, action_id, str(row["kind"]), status, code, {"approval_id": row["approval_id"]})
    return _stored(SX.by_id(ctx.c, action_id))


def approval_decided(c: sqlite3.Connection, approval_id: str, at: str | None = None) -> ActionResult | None:
    """Act on a decision about a Finance approval, in the caller's transaction: approve commits once (re-checked),
    decline denies, a withdrawal or expiry ends the action. None when the approval is not Finance's. Safe to call
    more than once."""
    row = SX.by_approval(c, approval_id)
    if row is None:
        return None
    if row["status"] != "needs_approval":
        return _stored(row)
    ctx = load(c, at)
    appr = tico.approval(c, approval_id)
    if appr is None:
        return _close(ctx, row, "withdrawn", "withdrawn", "its approval is gone")
    decision = appr.get("decision")
    if not decision:
        return _stored(row)
    if decision == "declined":
        return _close(ctx, row, "denied", "declined", f"declined by {appr.get('decided_by')}")
    if decision != "approved":
        stale = appr.get("decided_by") == "keeper" and str(appr.get("created") or "") <= \
            tico.shift(ctx.at, days=-STALE_DAYS)
        status = "expired" if stale else "withdrawn"
        return _close(ctx, row, status, status, "nobody decided in time; ask again" if stale else
                      "its approval was withdrawn; ask again")
    problem = verify(row, appr)
    p = O.Principal(actor=str(row["actor"]), for_actor=str(row["on_behalf_of"] or ""), rule=str(row["rule_id"] or ""))
    kind, body = str(row["kind"]), json.loads(str(row["payload_json"]))
    if problem == "payload":
        return _reask(ctx, row, p, kind, body)
    if problem:
        return _close(ctx, row, "denied", DENIED, f"the approval does not stand: {problem}")
    _savepoint(c, "fin_decide")
    try:
        _plan_gate, plan = _plan(ctx, p, kind, body)
        tico.approval_consume(c, str(row["approval_by"]), approval_id)
        result = plan.apply(str(row["id"]))
        SX.finish(c, str(row["id"]), status="committed", result=result, code="", reason="", at=ctx.at)
        _audit(c, p, str(row["id"]), kind, "committed", extra={"approval_id": approval_id})
        _release(c, "fin_decide")
        return _stored(SX.by_id(c, str(row["id"])))
    except FinanceError as e:
        _rollback(c, "fin_decide")
        return _close(ctx, row, "denied" if e.code in DENIAL_CODES else "failed", e.code, e.message)
    except (sqlite3.IntegrityError, tico.Refused) as e:
        _rollback(c, "fin_decide")
        return _close(ctx, row, "failed", "failed", str(e)[:300])
    except BaseException:
        _rollback(c, "fin_decide")
        raise


def _reask(ctx: Ctx, row: Mapping[str, object], p: O.Principal, kind: str, body: Mapping[str, object]) -> ActionResult:
    """The approved payload is not the one about to run: that approval is void for this action, and the right person
    is asked again for the real one."""
    try:
        gate, plan = _plan(ctx, p, kind, body)
    except FinanceError as e:
        return _close(ctx, row, "denied" if e.code in DENIAL_CODES else "failed", e.code, e.message)
    approver = O.approver(ctx.org, gate.who or "owner", p.who, ctx.approver_setting())
    what = f"{plan.summary} (Finance action {row['id']})"
    SX.finish(ctx.c, str(row["id"]), status="needs_approval", result={"what": what}, code="",
              reason="the approved payload did not match; asked again", at=ctx.at)
    approval_id = _ask(ctx, str(row["id"]), kind, body, what, p.who, approver, gate.rule or str(row["rule_id"]))
    _audit(ctx.c, p, str(row["id"]), kind, "needs_approval", "reask", {"void": row["approval_id"],
                                                                         "approval_id": approval_id})
    return _stored(SX.by_id(ctx.c, str(row["id"])))


def approvals_sweep(c: sqlite3.Connection, at: str | None = None, most: int = 200) -> list[ActionResult]:
    """Finish waiting actions whose approval was decided, withdrawn (backend/approval_expiry.py withdraws undecided ones
    after 14 days) or deleted: what a scheduler job runs, and what catches a decision no route handed over."""
    out: list[ActionResult] = []
    for approval_id in SX.settled_waits(c, most):
        done = approval_decided(c, approval_id, at)
        if done is not None:
            out.append(done)
    return out


def action(c: sqlite3.Connection, action_id: str) -> ActionResult:
    """An action's status by id: the one fin_* read allowed outside Finance besides connection status."""
    row = SX.by_id(c, action_id)
    if row is None:
        raise FinanceError(NOT_FOUND, "no such action")
    return _stored(row)
