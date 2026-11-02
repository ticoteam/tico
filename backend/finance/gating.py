"""Gate rules in the action path (spec §6.3) and the actions that write them. `judge` runs inside `act` before commit
and again when an approval is consumed: every active rule that matches the action's facts runs and the strictest
wins. A deny refuses; needs_approval asks the highest named approver; a period limit that fits moves its counter in
the same transaction as the action. Each evaluation is one fin_rule_runs row."""
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import Any, cast

from .context import Ctx
from .core import dates
from .core import gates as G
from .core import kinds as K
from .core import validate as V
from .core.errors import CONFLICT, DENIED, NOT_FOUND, FinanceError
from .core.org import SYSTEM, Principal
from .handlers import Plan, Result
from .store import banking as SK

TEST_DAYS = 90
RULE_KINDS = ("gate", "posting")
TEST_MOST = 5000


class Denied(FinanceError):
    """A gate rule said no. It carries the evaluation so the refusal is logged after the action's rollback."""

    def __init__(self, verdict: G.Verdict, facts: Mapping[str, object], actor: str) -> None:
        super().__init__(DENIED, verdict.reason or "a finance rule refuses this")
        self.verdict, self.facts, self.actor = verdict, dict(facts), actor


@dataclass(frozen=True)
class Judged:
    gate: K.Gate
    plan: Plan


def rules(ctx: Ctx) -> list[G.Rule]:
    return [rule_from(r) for r in SK.rules(ctx.c)]


def rule_from(row: Any) -> G.Rule:
    return G.rule_of(str(row["id"]), str(row["name"]), SK.loads(row["when_json"]), SK.loads(row["outcome_json"]),
                     bool(row["active"]))


def facts(ctx: Ctx, who: str, kind: str, plan: Plan) -> dict[str, object]:
    """What rules match on: the kind, who asks (and their kind and team), the time, plus the planner's own facts."""
    return send_facts(ctx, who, kind, plan.facts)


def send_facts(ctx: Ctx, who: str, kind: str, planned: Mapping[str, object]) -> dict[str, object]:
    """The same keys `facts` gives an action at request time, rebuilt for the send: a send-time recheck judges the
    requester as they are now, so a rule that matched at request time (actor.owner included) matches again."""
    actor_kind = "system" if who == SYSTEM else "bot" if who in ctx.org.bots else "human"
    return {"kind": kind, "actor": who, "actor.kind": actor_kind, "actor.team": ctx.org.team_of.get(who, ""),
            "actor.owner": bool(ctx.org.owner) and who == ctx.org.owner,
            "now": ctx.at, "currency": ctx.book.base_currency, **planned}


def _used(ctx: Ctx) -> G.Used:
    return lambda rule, key, subject: SK.counter(ctx.c, rule.id, key, subject)[0]


def log(ctx: Ctx, verdict: G.Verdict, facts_: Mapping[str, object], action_id: str | None, by: str) -> None:
    SK.rule_run(ctx.c, rule_id=verdict.rule, action_id=action_id, subject=str(facts_.get("kind") or ""),
                inputs={k: v for k, v in facts_.items() if k != G.AGES},
                outcome={"outcome": verdict.outcome, "who": verdict.who, "reason": verdict.reason,
                         "stale": verdict.stale, "hits": [h.__dict__ for h in verdict.hits]}, by=by, at=ctx.at)


def logged_denial(ctx: Ctx, e: Denied, action_id: str) -> None:
    log(ctx, e.verdict, e.facts, action_id, e.actor)


def stricter(gate: K.Gate, verdict: G.Verdict) -> K.Gate:
    """The default gate (or right fallback) and the rules' verdict together: needs_approval from the higher person."""
    if verdict.outcome != "needs_approval":
        return gate
    if gate.outcome != "needs_approval":
        return K.Gate("needs_approval", verdict.who, verdict.rule)
    if G.higher(gate.who, verdict.who) == gate.who:
        return gate
    return K.Gate("needs_approval", verdict.who, verdict.rule)


def combine(right: K.Gate, planned: K.Gate, requester: str) -> K.Gate:
    """A missing right's gate and the planner's gate together. The higher approver decides, and the person the planner
    named (a parent budget's owner) goes with it, unless that person is the requester: nobody approves their own ask,
    so the right's gate (their manager) stands. A tie keeps the right's gate."""
    if planned.outcome != "needs_approval" or planned.who == right.who or planned.person == requester:
        return right
    return planned if G.higher(right.who, planned.who) == planned.who else right


def judge(ctx: Ctx, p: Principal, kind: str, gate: K.Gate, plan: Plan, action_id: str) -> Judged:
    """Run the gate rules on one planned action. Raises Denied; returns the combined gate and a plan whose apply also
    moves the period counters of the limits it fits under."""
    found = facts(ctx, p.who, kind, plan)
    verdict = G.evaluate(rules(ctx), found, dates.day(ctx.today), _used(ctx))
    if verdict.outcome == "deny":
        raise Denied(verdict, found, p.actor)
    log(ctx, verdict, found, action_id, p.actor)
    if not verdict.counts:
        return Judged(stricter(gate, verdict), plan)
    amount = found.get("amount")
    spend = amount if isinstance(amount, int) and not isinstance(amount, bool) else 0
    inner = plan.apply

    def apply(aid: str) -> Result:
        out = inner(aid)
        for hit in verdict.counts:
            SK.count(ctx.c, hit.rule, hit.period_key, hit.subject, spend, ctx.at)
        return out
    return Judged(stricter(gate, verdict), replace(plan, apply=apply))


# ------------------------------------------------------------------------------------------------ writing rules
def _gate(ctx: Ctx, who: str, kind: str) -> K.Gate:
    """A rule a bot writes or changes always needs a finance admin (§6.2); a person with finance.rules commits."""
    if who in ctx.org.bots:
        return K.Gate("needs_approval", "finance_admin", "default:bot-rule")
    return K.default_gate(ctx.org, who, kind)


def _checked(p: V.Payload, base: Mapping[str, object] | None = None) -> dict[str, object]:
    """A rule's fields, validated, with its examples passing (a rule that fails its own examples is not saved)."""
    old = base or {}
    name = V.opt_text(p, "name", 120) or str(old.get("name") or "")
    if not name:
        raise FinanceError("invalid", "a rule has a name")
    when = G.parse_when(p["when"]) if "when" in p else cast(dict[str, object], old.get("when") or {})
    outcome = G.parse_outcome(p["outcome"]) if "outcome" in p else cast(dict[str, object], old.get("outcome") or {})
    if not when or not outcome:
        raise FinanceError("invalid", "a rule has a when and an outcome")
    examples = G.parse_examples(p["examples"]) if "examples" in p else \
        cast(list[dict[str, object]], old.get("examples") or [])
    bad = G.failing_examples(G.rule_of("draft", name, when, outcome), examples, dates.day("2026-01-01"))
    if not examples:
        raise FinanceError("invalid", "a rule needs at least one example")
    if bad:
        raise FinanceError("invalid", "its examples do not pass: " + "; ".join(bad[:5]))
    words = V.opt_text(p, "words", 300) or str(old.get("words") or "") or G.words_for(when, outcome)
    external = V.opt_text(p, "external_rule_id", 120) if "external_rule_id" in p else old.get("external_rule_id")
    return {"name": name, "words": words, "when": when, "outcome": outcome, "examples": examples,
            "external_rule_id": external or None}


def _row(ctx: Ctx, p: V.Payload) -> Any:
    row = SK.get(ctx.c, "fin_rules", V.text(p, "rule_id", 80))
    if row is None or str(row["kind"]) not in RULE_KINDS:
        raise FinanceError(NOT_FOUND, "no such rule")
    return row


def _fixed(row: Any) -> None:
    """A fixed default (G.FIXED) is the code's: it can't be edited or turned off, whoever asks."""
    if str(row["id"]) in G.FIXED:
        raise FinanceError(DENIED, "the code keeps this rule: it can't be changed or turned off")


def _as_fields(row: Any) -> dict[str, object]:
    return {"name": str(row["name"]), "words": str(row["words"]), "when": SK.loads(row["when_json"]),
            "outcome": SK.loads(row["outcome_json"]),
            "examples": cast(list[dict[str, object]], SK.loads_list(row["examples_json"])),
            "external_rule_id": row["external_rule_id"], "priority": int(row["priority"])}


def _fields(ctx: Ctx, who: str, kind: str, p: V.Payload, base: Mapping[str, object] | None = None) -> dict[str, object]:
    if kind == "posting":
        from .posting_rules import checked
        return checked(ctx, who, p, base)
    if "priority" in p:
        raise FinanceError("invalid", "priority is for posting rules")
    return _checked(p, base)


def rule_create(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """A gate rule (before an action commits) or a posting rule (on a new feed row or bill line)."""
    V.only(p, ("kind", "name", "words", "when", "outcome", "examples", "priority", "external_rule_id"))
    kind = V.choice(p, "kind", RULE_KINDS, "gate")
    f = _fields(ctx, who, kind, p)

    def apply(aid: str) -> Result:
        rid = SK.insert(ctx.c, "fin_rules", {
            "kind": kind, "name": f["name"], "words": f["words"], "when_json": SK.dump(f["when"]),
            "outcome_json": SK.dump(f["outcome"]), "examples_json": SK.dump(f["examples"]),
            "external_rule_id": f["external_rule_id"], "owner": who, "active": 1, "created_at": ctx.at,
            "created_by": who, **({"priority": f["priority"]} if kind == "posting" else {})})
        return {"rule_id": rid, "kind": kind}
    return Plan(_gate(ctx, who, "rule_create"), f"Add the finance rule \"{f['name']}\": {f['words']}"[:300], apply,
                facts={"rule.kind": kind})


def rule_update(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    V.only(p, ("rule_id", "name", "words", "when", "outcome", "examples", "priority", "external_rule_id"))
    row = _row(ctx, p)
    _fixed(row)
    kind = str(row["kind"])
    f = _fields(ctx, who, kind, p, _as_fields(row))

    def apply(aid: str) -> Result:
        SK.update(ctx.c, "fin_rules", str(row["id"]), {
            "name": f["name"], "words": f["words"], "when_json": SK.dump(f["when"]),
            "outcome_json": SK.dump(f["outcome"]), "examples_json": SK.dump(f["examples"]),
            "external_rule_id": f["external_rule_id"], "updated_at": ctx.at,
            **({"priority": f["priority"]} if kind == "posting" else {})})
        return {"rule_id": str(row["id"])}
    return Plan(_gate(ctx, who, "rule_update"), f"Change the finance rule \"{f['name']}\": {f['words']}"[:300], apply)


def _toggle(on: bool) -> Any:
    def planner(ctx: Ctx, who: str, p: V.Payload) -> Plan:
        V.only(p, ("rule_id",))
        row = _row(ctx, p)
        _fixed(row)
        if bool(row["active"]) == on:
            raise FinanceError(CONFLICT, f"that rule is already {'on' if on else 'off'}")

        def apply(aid: str) -> Result:
            SK.update(ctx.c, "fin_rules", str(row["id"]), {"active": 1 if on else 0, "updated_at": ctx.at})
            return {"rule_id": str(row["id"]), "active": on}
        kind = "rule_enable" if on else "rule_disable"
        return Plan(_gate(ctx, who, kind), f"Turn {'on' if on else 'off'} the finance rule \"{row['name']}\"", apply)
    return planner


rule_enable = _toggle(True)
rule_disable = _toggle(False)


def rule_test(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """Run a draft (or saved) rule over the last 90 days of actions and feed rows the tester can see and say what it
    would have changed (a posting rule: the feed rows it would post, and where). Writes nothing but the action's own
    record."""
    V.only(p, ("rule_id", "when", "outcome", "kind"))
    since = (dates.day(ctx.today) - timedelta(days=TEST_DAYS)).isoformat()
    row = _row(ctx, p) if "rule_id" in p else None
    if (str(row["kind"]) if row is not None else V.choice(p, "kind", RULE_KINDS, "gate")) == "posting":
        from .core import ap as AP
        from .posting_rules import rule_of, test
        draft = rule_of(row) if row is not None else \
            AP.PostingRule("draft", "draft", G.parse_when(p.get("when")), AP.parse_posting(p.get("outcome")))
        found = test(ctx, who, draft, since, TEST_MOST)
        return Plan(K.ALLOW, f"Test a posting rule over {TEST_DAYS} days", lambda aid: found, {"test": found})
    if row is not None:
        rule = rule_from(row)
    else:
        rule = G.rule_of("draft", "draft", G.parse_when(p.get("when")), G.parse_outcome(p.get("outcome")))
    everything = ctx.view(who).everything  # whoever sees everything tests every actor's actions, not only their own
    day = dates.day(ctx.today)
    changed: list[Result] = []
    checked = 0
    for row in SK.actions_since(ctx.c, since, TEST_MOST):
        if not everything and str(row["actor"]) != who:
            continue
        body = {k: v for k, v in SK.loads(row["payload_json"]).items() if k not in K.SERVER_ONLY_KEYS}
        found: dict[str, object] = {"kind": str(row["kind"]), "actor": str(row["actor"]),
                                    "actor.kind": "bot" if str(row["actor"]) in ctx.org.bots else "human",
                                    **{k: v for k, v in body.items() if isinstance(v, (int, str, bool))}}
        checked += 1
        got = G.evaluate([replace(rule, active=True)], found, day).outcome
        if got != "allow":
            changed.append({"action_id": str(row["id"]), "kind": str(row["kind"]), "was": str(row["status"]),
                            "would": got, "at": str(row["created_at"])})
    from .tab import sees_money_account
    view = ctx.view(who)
    seen = {str(m["id"]) for m in SK.money_accounts(ctx.c, ctx.book.id) if sees_money_account(ctx, view, m)}
    for row in SK.feed_since(ctx.c, since, TEST_MOST):
        if str(row["money_account_id"]) not in seen:
            continue
        found = {"kind": "feed", "amount": abs(int(row["amount"])), "counterparty": str(row["counterparty"]),
                 "direction": "out" if int(row["amount"]) < 0 else "in"}
        checked += 1
        got = G.evaluate([replace(rule, active=True)], found, day).outcome
        if got != "allow":
            changed.append({"feed_id": str(row["id"]), "kind": "feed", "would": got, "at": str(row["txn_date"])})
    result: Result = {"checked": checked, "changed": changed[:500], "changed_count": len(changed), "since": since}
    return Plan(K.ALLOW, f"Test a finance rule over {TEST_DAYS} days", lambda aid: result, {"test": result})


PLANNERS = {"rule_create": rule_create, "rule_update": rule_update, "rule_enable": rule_enable,
            "rule_disable": rule_disable, "rule_test": rule_test}

__all__ = ["Denied", "judge", "logged_denial", "PLANNERS", "rules", "rule_from", "facts"]
