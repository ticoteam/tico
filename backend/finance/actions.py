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
from dataclasses import dataclass, field, replace
from typing import Any, cast

from . import banking, budgeting, cards, catalog, documents, gateway_sync, gating, handlers, jobs, mirror, payments
from . import purchasing
from . import posting
from . import raw_totals, runs
from . import setup as setup_mod
from . import rights, sync, tax, usage
from .context import Ctx, load
from .core import kinds as K
from .core import org as O
from .core import validate as V
from .core.errors import CONFLICT, DENIED, INVALID, NOT_AVAILABLE, NOT_FOUND, FinanceError, Named
from .store import actions as SX
from .store import budgets as SB
from .store import usage as SU
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

# Every live kind's planner: phase 0's in handlers, then banking, cards, payments, rules, and payables/receivables.
PLANNERS: dict[str, handlers.Planner] = {
    **handlers.PLANNERS, "connection_create": banking.connection_create, "reconcile": banking.reconcile,
    "reconcile_undo": banking.reconcile_undo,
    "connection_sync": banking.connection_sync, "connection_disconnect": banking.connection_disconnect,
    "connection_map": banking.connection_map, **cards.PLANNERS, **payments.PLANNERS, **gating.PLANNERS,
    **documents.PLANNERS, **mirror.PLANNERS, **tax.PLANNERS, **raw_totals.PLANNERS, **catalog.PLANNERS,
    **purchasing.PLANNERS}
assert set(PLANNERS) == set(K.LIVE)

STATUSES = ("committed", "needs_approval", "denied", "failed", "expired", "withdrawn")
DENIAL_CODES = (DENIED, NOT_AVAILABLE)
SOURCE = "finance"                    # marks a Tico approval as Finance's, for the decide routes to route
MAX_KEY = 200
CARD_NUMBER_REFUSED = V.CARD_NUMBER_REFUSED
STALE_DAYS = 14                        # hubdb.APPROVAL_STALE_DAYS: a withdrawal after this long is an expiry
# Payload fields a planner fills in when the asker leaves them out (Plan.resolved): a retry without them is the same
# request.
RESOLVED_KEYS = frozenset({"effective_from", "date", "raise_check", "recipient_version"})


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
    replayed: bool = False              # the key's earlier action, returned again: nothing was done now

    def as_dict(self) -> dict[str, object]:
        return {"status": self.status, "kind": self.kind, "action_id": self.action_id, "result": dict(self.result),
                "code": self.code, "reason": self.reason, "approval_id": self.approval_id, "approver": self.approver,
                "rule": self.rule, "dry_run": self.dry_run, "impact": dict(self.impact)}


def _canonical(payload: object) -> dict[str, object]:
    """The payload as stored and hashed: plain JSON, keys sorted, its top-level ids (`*_id`, `obligation`) trimmed as
    the planners read them, so a stored id always matches the record it names (a bill's waiting asks are found by
    it)."""
    if not isinstance(payload, Mapping):
        raise FinanceError(INVALID, "payload is an object")
    try:
        loaded: object = json.loads(json.dumps(payload, sort_keys=True))
    except (TypeError, ValueError):
        raise FinanceError(INVALID, "payload is plain JSON") from None
    return _trim_ids(cast(dict[str, object], loaded))


def _trim_ids(payload: Mapping[str, object]) -> dict[str, object]:
    return {k: v.strip() if isinstance(v, str) and (k.endswith("_id") or k == "obligation") else v
            for k, v in payload.items()}


def _replayed_reason(c: sqlite3.Connection, p: O.Principal, row: Mapping[str, object], at: str | None) -> str:
    """The reason a replay returns: a refusal's names come back only for a caller who may see each budget it named
    (spec §7.5); otherwise the fallback its record kept."""
    fallback = str(row["reason"] or "")
    result = cast(dict[str, Any], json.loads(str(row["result_json"] or "{}")))
    if "named" not in result:
        return fallback
    try:
        ctx = load(c, at)
    except FinanceError:
        return fallback
    return handlers.named_reason(ctx, p.who, fallback, result)


def _stored(row: Mapping[str, object], full: bool = False) -> ActionResult:
    """An action as recorded. A replay or a status lookup carries the id, status, code and reason only (`full` is for
    the call that just committed it): finance data is read fresh through finance/reads with the caller's current view,
    so a retry after a revoked grant returns nothing they may no longer see (spec §5)."""
    result = cast(dict[str, object], json.loads(str(row["result_json"] or "{}"))) if full else dict[str, object]()
    return ActionResult(status=str(row["status"]), kind=str(row["kind"]), action_id=str(row["id"]),
                        result={k: v for k, v in result.items() if k != "what"}, code=str(row["code"] or ""),
                        reason=str(row["reason"] or ""), approval_id=str(row["approval_id"] or "") or None,
                        approver=str(row["approval_to"] or "") or None, rule=str(row["rule_id"] or ""))


def _approval_payload(action_id: str, kind: str, payload: Mapping[str, object], what: str) -> dict[str, object]:
    shown = {k: v for k, v in payload.items() if k not in K.SERVER_ONLY_KEYS}
    out: dict[str, object] = {"source": SOURCE, "action_id": action_id, "kind": kind, "what": what, "payload": shown}
    if kind in K.SPEND:
        # A Tico spend approval names the amount and the account it comes from (its rule 7 refuses one without).
        out["amount"] = payload.get("amount")
        out["account"] = str(payload.get("money_account_id") or payload.get("account_id")
                             or payload.get("from_account") or "bank")
    return out


def _audit(c: sqlite3.Connection, p: O.Principal, action_id: str, kind: str, status: str, code: str = "",
           extra: Mapping[str, object] | None = None) -> None:
    tico.audit(c, p.actor, f"finance.{kind}", action_id,
               {"status": status, "code": code, "on_behalf_of": p.for_actor, "rule": p.rule, **(extra or {})})


def _hooks(c: sqlite3.Connection) -> list[object] | None:
    return cast(list[object] | None, getattr(c, "after_commit", None))


def _savepoint(c: sqlite3.Connection, name: str) -> int:
    """Open the savepoint; returns how many after-commit callbacks were queued before it."""
    c.execute(f"SAVEPOINT {name}")
    hooks = _hooks(c)
    return len(hooks) if hooks is not None else 0


def _rollback(c: sqlite3.Connection, name: str, mark: int) -> None:
    """Undo to the savepoint, with the after-commit callbacks queued since (a ring for an undone card change)."""
    c.execute(f"ROLLBACK TO SAVEPOINT {name}")
    c.execute(f"RELEASE SAVEPOINT {name}")
    hooks = _hooks(c)
    if hooks is not None:
        del hooks[mark:]


def _release(c: sqlite3.Connection, name: str) -> None:
    c.execute(f"RELEASE SAVEPOINT {name}")


def _requester(c: sqlite3.Connection, who: str) -> str:
    """Who the Tico approval is asked by: the teammate, or the keeper for system work."""
    return who if who != O.SYSTEM and tico.known_actor(c, who) else "keeper"


def _same_request(prior: Mapping[str, object], body: Mapping[str, object]) -> bool:
    """Whether a stored payload is this request: equal, or equal but for defaults the planner filled in. Ids compare
    trimmed, so a request stored before ids were trimmed still replays."""
    prior = _trim_ids(prior)
    if prior == body:
        return True
    extra = set(prior) - set(body)
    return extra <= RESOLVED_KEYS | {"details_ref"} and all(prior.get(k) == v for k, v in body.items())


def _approver(ctx: Ctx, gate: K.Gate, who: str) -> str:
    """The one person the gate sends this to now, or "" when it needs nobody (it allows, or its approver asked)."""
    if gate.outcome != "needs_approval":
        return ""
    approver = O.approver(ctx.org, gate.who, who, ctx.approver_setting(), gate.person)
    if approver == who and who != ctx.org.owner:
        # A rule can make a gate stricter than the right's, and the person it names may be the requester (a finance
        # approver asking). Their own approval is never permission: the install owner decides instead (spec §6.3).
        approver = ctx.org.owner
    if not approver:
        raise FinanceError(DENIED, "nobody can approve this: the install has no owner")
    return "" if approver == who else approver


def _decider(ctx: Ctx, row: Mapping[str, object], gate: K.Gate, who: str) -> str:
    """Who decides an approved action now (_approver). A request asked of the requester's manager (default:budget-request)
    keeps that route here: while deciding, figure_route is "" (the true figures), so the planned gate may no longer name
    the manager. The manager must still be the person the request was sent to, or it is asked again (spec 6.3)."""
    if str(row["rule_id"] or "") == handlers.REQUEST_GATE.rule:
        gate = gating.combine(handlers.REQUEST_GATE, gate, who)
    return _approver(ctx, gate, who)


def _plan(ctx: Ctx, p: O.Principal, kind: str, payload: Mapping[str, object],
          action_id: str) -> tuple[K.Gate, handlers.Plan]:
    """The right check first (a missing right is a denial, or for budgets a request to the asker's manager), then the
    kind's own checks and default gate, then the gate rules (§6.3), which run again whenever this does: at consume."""
    if ctx.book.mode == "mirror" and kind in mirror.REFUSED:
        raise FinanceError(NOT_AVAILABLE, f"{kind} is not available while the books are a provider's (mirror mode)")
    by_right = K.authority(ctx.org, p.who, kind, ctx.rights(p.who))
    if by_right is not None and by_right.outcome == "deny":
        raise FinanceError(DENIED, f"{kind} needs {by_right.rule.removeprefix('right:')}")
    # A mirrored book sends its documents to the provider first: the mirror's planner queues the write.
    planner = mirror.DOC_PLANNERS.get(kind) if ctx.book.mode == "mirror" else None
    plan = (planner or PLANNERS[kind])(ctx, p.who, payload)
    gate = by_right or plan.gate
    if by_right is not None:
        gate = gating.combine(by_right, plan.gate, p.who)
    judged = gating.judge(ctx, p, kind, gate, plan, action_id)
    return judged.gate, judged.plan


def act(c: sqlite3.Connection, principal: O.Principal | str, kind: str, payload: Mapping[str, object],
        idempotency_key: str, dry_run: bool = False, at: str | None = None) -> ActionResult:
    """Request one Finance action. The same key returns the first result and does nothing again. A dry run writes
    nothing and says what would happen: the outcome, the rule, the approver and the budget impact."""
    p = O.principal(principal)
    key = (idempotency_key or "").strip()
    if not key or len(key) > MAX_KEY:
        return ActionResult(status="failed", kind=kind, code=INVALID, reason=f"an idempotency key is 1-{MAX_KEY} chars")
    try:
        body, sealed = payments.strip_details(c, kind, _canonical(payload))
    except FinanceError as e:
        return ActionResult(status="failed", kind=kind, code=e.code, reason=e.message)
    except tico.Refused as e:
        return ActionResult(status="failed", kind=kind, code=NOT_AVAILABLE, reason=str(e)[:300])
    if V.has_card_number(body):
        return ActionResult(status="failed", kind=kind, code=INVALID, reason=CARD_NUMBER_REFUSED)
    if not dry_run:
        prior = SX.by_key(c, key)
        if prior is not None:
            if prior["kind"] != kind or not _same_request(json.loads(prior["payload_json"]), body) or \
                    prior["actor"] != p.actor:
                return ActionResult(status="failed", kind=kind, code=CONFLICT,
                                    reason="that idempotency key was used for a different request")
            return replace(_stored(prior), reason=_replayed_reason(c, p, prior, at), replayed=True)
    action_id = tico.new_id()
    try:
        if kind not in K.ALL:
            raise FinanceError(INVALID, f"no Finance action {kind}")
        if kind not in K.LIVE:
            raise FinanceError(NOT_AVAILABLE, f"{kind} is not available yet")
        ctx = load(c, at)
    except FinanceError as e:
        return _finish_refused(c, p, kind, body, key, action_id, e, dry_run, at)
    if not dry_run:
        handlers.suspend_unstanding(ctx)
    mark = _savepoint(c, "fin_act")
    try:
        if sealed and not dry_run:
            body = payments.seal_details(c, body, sealed, p.actor, ctx.at)
        gate, plan = _plan(ctx, p, kind, body, action_id)
        approver = _approver(ctx, gate, p.who)
        body = {**body, **plan.resolved}
        would = "needs_approval" if approver else "committed"
        if dry_run:
            _rollback(c, "fin_act", mark)
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
        _rollback(c, "fin_act", mark)
        if isinstance(e, gating.Denied) and not dry_run:
            gating.logged_denial(ctx, e, action_id)
        return _finish_refused(c, p, kind, body, key, action_id, e, dry_run, at)
    except (sqlite3.IntegrityError, tico.Refused) as e:
        _rollback(c, "fin_act", mark)
        return _finish_refused(c, p, kind, body, key, action_id, FinanceError("failed", str(e)[:300]), dry_run, at)
    except BaseException:
        _rollback(c, "fin_act", mark)
        raise


def carries_card_number(payload: Mapping[str, object]) -> bool:
    """Whether a payload holds a card number anywhere in it: a route then keeps no receipt of the payload as sent."""
    return V.has_card_number(payload)


def receipt_payload(c: sqlite3.Connection, kind: str, payload: Mapping[str, object]) -> dict[str, object]:
    """The payload as a route's idempotency receipt may hash it: bank account and routing numbers become a keyed
    fingerprint and the last 4, and a card number is never kept (the receipt says it was refused), so no receipt holds
    (or a plain hash of) a number."""
    try:
        body = payments.strip_details(c, kind, _canonical(payload))[0]
    except (FinanceError, tico.Refused):
        body = {k: v for k, v in payload.items() if k not in payments.BANK_FIELDS}
    return {"refused": CARD_NUMBER_REFUSED} if V.has_card_number(body) else body


def _recorded(named: Named | None) -> dict[str, object]:
    """What a refusal keeps of the budgets and cards it names: their ids and the sentence, never a name."""
    if named is None:
        return {}
    return {"named": {"budgets": list(named.budgets), "cards": list(named.cards), "template": named.template,
                      "numbers": named.numbers, "plain": named.plain, "on": named.on}}


def _finish_refused(c: sqlite3.Connection, p: O.Principal, kind: str, body: Mapping[str, object], key: str,
                    action_id: str, e: FinanceError, dry_run: bool, at: str | None) -> ActionResult:
    """A denial or failure is recorded under its key too, so a retry gets the same answer. Bank details it sealed
    were rolled back with its savepoint, so the stored payload does not name them."""
    status = "denied" if e.code in DENIAL_CODES else "failed"
    body = {k: v for k, v in body.items() if k not in payments.REFS}
    if dry_run:
        return ActionResult(status=status, kind=kind, code=e.code, reason=e.message, dry_run=True)
    when = at or tico.now()
    SX.insert(c, action_id=action_id, kind=kind, payload=body, actor=p.actor, on_behalf_of=p.for_actor,
              rule_id=p.rule, key=key, rules=[], status=status, result=_recorded(e.named), code=e.code,
              reason=e.named.fallback if e.named else e.message, by=p.actor, at=when)
    _audit(c, p, action_id, kind, status, e.code)
    return ActionResult(status=status, kind=kind, action_id=action_id, code=e.code, reason=e.message)


def _ask(ctx: Ctx, action_id: str, kind: str, body: Mapping[str, object], what: str, who: str, approver: str,
         rule: str) -> str:
    requester = _requester(ctx.c, who)
    payload = _approval_payload(action_id, kind, body, what)
    approval_id = tico.approval_request(ctx.c, requester, K.approval_kind(kind), payload, approver)
    SX.asked(ctx.c, action_id, approval_id=approval_id, approval_hash=tico.payload_hash(payload), requester=requester,
             to=approver, rule_id=rule, payload=body, at=ctx.at)
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


def _close(ctx: Ctx, row: Mapping[str, object], status: str, code: str, reason: str,
           named: Named | None = None) -> ActionResult:
    """End a waiting action: it releases any hold it reserved, and the key's result is final (ask again with a new
    key). A refusal that named budgets keeps its fallback and record, as a refused call does (`named`)."""
    action_id = str(row["id"])
    text = named.fallback if named else reason
    budgeting.release_for(ctx, "action", action_id)
    payments.drop_details(ctx.c, json.loads(str(row["payload_json"] or "{}")))
    SX.finish(ctx.c, action_id, status=status, result=_recorded(named), code=code, reason=text, at=ctx.at)
    if str(row["kind"]) == "payment_create":     # a scheduled bill that asked for it is open again, and says why
        documents.payment_ask_ended(ctx, action_id, json.loads(str(row["payload_json"] or "{}")), status, text)
    p = O.Principal(actor=str(row["actor"]), for_actor=str(row["on_behalf_of"] or ""))
    _audit(ctx.c, p, action_id, str(row["kind"]), status, code, {"approval_id": row["approval_id"]})
    done = _stored(SX.by_id(ctx.c, action_id))
    return replace(done, reason=reason) if named else done     # the caller who just refused it gets the sentence


def withdraw_bill_asks(ctx: Ctx, bill_id: str, keep: str, why: str) -> list[str]:
    """End every waiting ask to pay this bill (its scheduled payment's payment_create, an earlier bill_schedule)
    other than `keep`: its approval is withdrawn and it can never run. The ids ended."""
    out: list[str] = []
    for row in SX.waiting(ctx.c, ("payment_create", "bill_schedule")):
        body = json.loads(str(row["payload_json"] or "{}"))
        mine = str(body.get("obligation") or "").strip() == f"bill:{bill_id}" if row["kind"] == "payment_create" \
            else str(body.get("bill_id") or "").strip() == bill_id  # trimmed as the planners read them
        if not mine or str(row["id"]) == keep:
            continue
        if row["approval_id"]:
            tico.approval_withdraw(ctx.c, str(row["approval_id"]), why)
        _close(ctx, row, "withdrawn", "withdrawn", why)
        out.append(str(row["id"]))
    return out


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
    handlers.suspend_unstanding(ctx)
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
    mark = _savepoint(c, "fin_decide")
    try:
        ctx.deciding = True
        gate, plan = _plan(ctx, p, kind, body, str(row["id"]))
        now_to = _decider(ctx, row, gate, p.who)
        if payments.moved(body, plan.resolved) or (now_to and (now_to != row["approval_to"] or _drifted(body, plan))):
            # The person who decides this changed since it was asked (a new manager, a finance approver named), or the
            # planner would now fix a value differently from the approved one (a parent raise that moved). When nobody
            # needs to decide it any more, it runs below with the plan as it now stands.
            _rollback(c, "fin_decide", mark)
            return _reask(ctx, row, p, kind, body)
        tico.approval_consume(c, str(row["approval_by"]), approval_id)
        result = plan.apply(str(row["id"]))
        SX.finish(c, str(row["id"]), status="committed", result=result, code="", reason="", at=ctx.at)
        _audit(c, p, str(row["id"]), kind, "committed", extra={"approval_id": approval_id})
        _release(c, "fin_decide")
        return _stored(SX.by_id(c, str(row["id"])), full=True)
    except FinanceError as e:
        _rollback(c, "fin_decide", mark)
        if isinstance(e, gating.Denied):
            gating.logged_denial(ctx, e, str(row["id"]))
        return _close(ctx, row, "denied" if e.code in DENIAL_CODES else "failed", e.code, e.message, e.named)
    except (sqlite3.IntegrityError, tico.Refused) as e:
        _rollback(c, "fin_decide", mark)
        return _close(ctx, row, "failed", "failed", str(e)[:300])
    except BaseException:
        _rollback(c, "fin_decide", mark)
        raise


def _drifted(body: Mapping[str, object], plan: handlers.Plan) -> bool:
    """Whether the planner would now fix a value differently from the one asked (Plan.resolved): what was approved
    is then not what would run."""
    return any(body.get(k) != v for k, v in plan.resolved.items())


def _reask(ctx: Ctx, row: Mapping[str, object], p: O.Principal, kind: str, body: Mapping[str, object]) -> ActionResult:
    """The approved payload is not the one about to run, so that approval is void. The change is planned again and asked
    again for the payload it would now run, of the person who decides it now. Never the requester."""
    action_id = str(row["id"])
    ctx.deciding = True                       # the recheck runs on the true figures (figure_route)
    try:
        gate, plan = _plan(ctx, p, kind, body, action_id)
        approver = _decider(ctx, row, gate, p.who)
    except FinanceError as e:
        return _close(ctx, row, "denied" if e.code in DENIAL_CODES else "failed", e.code, e.message, e.named)
    if payments.moved(body, plan.resolved):
        approver = payments.admin_person(ctx)           # new payment details are a finance admin's to approve (§6.4)
    if not approver:
        return _close(ctx, row, "failed", DENIED, "nobody else decides this now; ask again")
    fresh = {**body, **plan.resolved}
    what = f"{plan.summary} (Finance action {action_id})"
    SX.finish(ctx.c, action_id, status="needs_approval", result={"what": what}, code="",
              reason="the approved payload did not match; asked again", at=ctx.at)
    approval_id = _ask(ctx, action_id, kind, fresh, what, p.who, approver, gate.rule or str(row["rule_id"]))
    _audit(ctx.c, p, action_id, kind, "needs_approval", "reask", {"void": row["approval_id"],
                                                                  "approval_id": approval_id, "to": approver})
    return _stored(SX.by_id(ctx.c, action_id))


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


def help(kind: str | None = None) -> dict[str, object]:
    """Plain-words docs for one action kind (`finance_act kind=help`), or every kind in one line each: what it does,
    its payload fields, the right it needs and whether it is live yet."""
    def need(k: str) -> str:
        n = K.NEEDS.get(k)
        if k in K.OWNER_ONLY:
            return "owner"
        if k in K.ADMIN_ONLY:
            return "finance.admin"
        return " or ".join(n) if isinstance(n, tuple) else (n or "")
    if kind is None:
        return {"kinds": [{"kind": k, "words": K.WORDS[k], "live": k in K.LIVE} for k in K.ALL]}
    if kind not in K.ALL:
        raise FinanceError(INVALID, f"unknown action kind {kind}")
    return {"kind": kind, "words": K.WORDS[kind], "right": need(kind), "live": kind in K.LIVE,
            "approval_kind": K.approval_kind(kind),
            "fields": [{"name": n.rstrip("?"), "type": t, "required": not n.endswith("?"), "words": w}
                       for n, t, w in K.SHAPES[kind]]}


# What the server, the scheduler and the run hooks call (part B).
ensure_setup = jobs.ensure_setup
drain_events = jobs.drain
open_next_period = jobs.open_next_period
watch_documents = jobs.watch
grant_defaults = rights.grant_defaults


def set_bills_email(c: sqlite3.Connection, address: str | None, by: str = O.SYSTEM, at: str | None = None) -> None:
    """Record the install's bills address (reads.bills_email and GET /finance/bills show it); None clears it."""
    from .store import ledger as SL
    from .tab_ap import BILLS_EMAIL_KEY
    value = (address or "").strip().lower()
    if value and ("@" not in value or len(value) > 200):
        raise FinanceError(INVALID, "a bills address is an email address")
    SL.set_setting(c, BILLS_EMAIL_KEY, value or None, by, at or tico.now())
RunHold = runs.RunHold
run_hold = runs.hold
run_ended = runs.ended
expire_holds = runs.expire
post_usage = usage.post
usage_snapshot = usage.snapshot
gateway_start = gateway_sync.start
gateway_ensure = gateway_sync.ensure
gateway_stop = gateway_sync.stop
banking_start = sync.start
use_vault = tico.use_vault
BANK_FIELDS = payments.BANK_FIELDS
banking_ensure = sync.ensure
banking_stop = sync.stop
banking_ring = sync.ring
banking_step = sync.step
mercury_probe = sync.probe_credential
stripe_delivery = sync.stripe_delivery
stripe_probe = sync.stripe_probe


def receive(c: sqlite3.Connection, payload: dict[str, object]) -> bool:
    """A kept call to a trigger whose consumer is Finance (spec §3.4): Mercury's event, kept once by its id. Mercury is
    the only bank feed so far, so every Finance call is one of its deliveries."""
    return sync.delivery(c, None, payload)


# ------------------------------------------------------------------------------------------------ watch rules
WATCH_ACTIONS = ("finance_freeze_cards", "finance_pause_budget", "finance_act")


def watch(c: sqlite3.Connection, rule_id: str, by: str, action: str, params: Mapping[str, object],
          data: Mapping[str, object], key: str, at: str | None = None) -> ActionResult:
    """A Tico rule's Finance action (spec §6.1 watch rules), run as an ordinary action by the rule's maker under that
    rule, so it gets exactly the maker's rights and gates. A keeper-made rule acts as Finance itself (system work,
    which may only freeze cards). `budget_id` defaults to the event's own budget. One key per rule and event: the
    same event never acts twice."""
    who = SYSTEM if by in ("", "keeper") else by
    p = O.Principal(actor=who, rule=rule_id)
    budget = str(params.get("budget_id") or data.get("budget_id") or "")
    if action == "finance_freeze_cards":
        return act(c, p, "card_freeze", {"budget_id": budget, "reason": f"finance rule {rule_id}"}, key, at=at)
    if action == "finance_pause_budget":
        return act(c, p, "budget_update", {"budget_id": budget, "amount": 0, "at_limit": "decline"}, key, at=at)
    if action == "finance_act":
        payload = params.get("payload")
        return act(c, p, str(params.get("kind") or ""), cast(Mapping[str, object], payload)
                   if isinstance(payload, Mapping) else {}, key, dry_run=params.get("dry_run") is True, at=at)
    return ActionResult(status="failed", kind=action, code=INVALID, reason=f"no Finance watch action {action}")


def note_view_as(c: sqlite3.Connection, owner: str, target: str) -> None:
    """"View as" is audited (spec §7.5): the owner reads Finance exactly as `target` sees it."""
    tico.audit(c, owner, "finance.view_as", target, {})


# ------------------------------------------------------------------------------------------------ dev copies
def scrub_dev_copy(c: sqlite3.Connection, at: str) -> dict[str, int]:
    """Make a dev copy's Finance tables inert (backend/devcopy.py empties the rest): an action waiting on approval
    would commit when someone approves it in the copy, so it goes; an undrained budget event would reach rules and the
    owner, so it is marked drained without acting; allocations lose their link to the emptied holds, not themselves.
    {table: rows changed}."""
    return {"fin_actions": SX.drop_waiting(c), "fin_events": SU.drain_all(c, at),
            "fin_allocations": SB.unlink_allocation_holds(c)}
