"""Gate rules (spec §6.2, §6.3), with no I/O. A rule is plain words, a `when` condition in Tico's `when_json` shape
(every key must match, a list means any of) with gt/gte/lt/lte/not/matches for money and text, an outcome, and
examples that must pass before it is saved. Every matching rule runs and the strictest wins:
deny > needs_approval > limit > allow; the lowest limit binds; one approval from the highest named approver."""
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any, cast

from . import validate as V
from .errors import invalid

OUTCOMES = ("allow", "deny", "needs_approval", "limit")
RANK = {"allow": 0, "limit": 1, "needs_approval": 2, "deny": 3}
WHO = ("manager", "finance_admin", "owner")          # lowest to highest
# Who decides, lowest to highest. A parent budget's owner (budget_owner) is not a rule's choice, but a parent raise
# asks them, and that outranks the manager a missing right sends to (§4.4).
RANK_WHO = ("manager", "budget_owner", "finance_admin", "owner")
OPS = ("gt", "gte", "lt", "lte", "not", "matches")
PERIODS = ("action", "day", "week", "month")
SUBJECTS = ("company", "actor", "recipient")
STALE_MINUTES = 15                                    # money out reads nothing older than this (§6.3)
MAX_KEYS, MAX_EXAMPLES, MAX_PATTERN = 20, 20, 200
KEY = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)*$")
# Inputs whose source can go stale, and how old each is, live in facts["_ages"] (minutes; None when unknown).
AGES = "_ages"

Facts = Mapping[str, object]


@dataclass(frozen=True)
class Rule:
    id: str
    name: str
    when: Mapping[str, object]
    outcome: str
    who: str = ""
    reason: str = ""
    limit: int | None = None
    per: str = "action"
    subject: str = "company"
    stale_minutes: int = STALE_MINUTES
    active: bool = True


@dataclass(frozen=True)
class Hit:
    """One rule's say on one action: its outcome, why, and for a limit the counter it moves."""
    rule: str
    outcome: str
    who: str = ""
    reason: str = ""
    stale: bool = False
    period_key: str = ""
    subject: str = ""


@dataclass(frozen=True)
class Verdict:
    outcome: str
    who: str = ""
    rule: str = ""
    reason: str = ""
    stale: bool = False
    hits: tuple[Hit, ...] = ()
    counts: tuple[Hit, ...] = field(default=())      # limits that fit: their counters move when the action commits


# ------------------------------------------------------------------------------------------------ parsing
def parse_when(raw: object) -> dict[str, object]:
    when = V.obj(raw, "when")
    if not when:
        raise invalid("a rule's when names at least one input")
    if len(when) > MAX_KEYS:
        raise invalid(f"a rule's when has at most {MAX_KEYS} inputs")
    for key, cond in when.items():
        if not KEY.match(key) or key.startswith("_"):
            raise invalid(f"{key!r} is not an input name")
        _check_cond(key, cond)
    return dict(when)


def _check_cond(key: str, cond: object) -> None:
    if isinstance(cond, list):
        items = cast(list[object], cond)
        if not items or len(items) > 50 or any(isinstance(x, (dict, list)) for x in items):
            raise invalid(f"{key}: a list holds 1-50 plain values")
        return
    if isinstance(cond, dict):
        ops = cast(dict[str, object], cond)
        if not ops or set(ops) - set(OPS):
            raise invalid(f"{key}: operators are {'|'.join(OPS)}")
        for op, value in ops.items():
            if op in ("gt", "gte", "lt", "lte") and (isinstance(value, bool) or not isinstance(value, int)):
                raise invalid(f"{key}.{op} compares whole numbers (cents)")
            if op == "matches":
                if not isinstance(value, str) or not value or len(value) > MAX_PATTERN:
                    raise invalid(f"{key}.matches is a pattern of 1-{MAX_PATTERN} chars")
                try:
                    re.compile(value)
                except re.error:
                    raise invalid(f"{key}.matches is not a valid pattern") from None
            if op == "not" and isinstance(value, dict):
                raise invalid(f"{key}.not takes a value or a list")
        return
    if cond is not None and not isinstance(cond, (str, int, bool)):
        raise invalid(f"{key}: a condition is a value, a list or operators")


def parse_outcome(raw: object) -> dict[str, object]:
    """{outcome, who?, reason?, limit?, per?, subject?, stale_minutes?}."""
    p = V.obj(raw, "outcome")
    V.only(p, ("outcome", "who", "reason", "limit", "per", "subject", "stale_minutes"))
    out: dict[str, object] = {"outcome": V.choice(p, "outcome", OUTCOMES)}
    if out["outcome"] == "needs_approval":
        out["who"] = V.choice(p, "who", WHO, "finance_admin")
    if out["outcome"] == "limit":
        limit = p.get("limit")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise invalid("a limit is 0 or more cents")
        out["limit"], out["per"] = limit, V.choice(p, "per", PERIODS, "action")
        out["subject"] = V.choice(p, "subject", SUBJECTS, "company")
    reason = V.opt_text(p, "reason", 300)
    if reason:
        out["reason"] = reason
    if "stale_minutes" in p:
        minutes = p.get("stale_minutes")
        if isinstance(minutes, bool) or not isinstance(minutes, int) or not 1 <= minutes <= 1440:
            raise invalid("stale_minutes is 1-1440")
        out["stale_minutes"] = minutes
    return out


def rule_of(rid: str, name: str, when: Mapping[str, object], outcome: Mapping[str, object],
            active: bool = True) -> Rule:
    limit = outcome.get("limit")
    stale = outcome.get("stale_minutes")
    return Rule(id=rid, name=name, when=when, outcome=str(outcome.get("outcome") or "allow"),
                who=str(outcome.get("who") or ""), reason=str(outcome.get("reason") or ""),
                limit=limit if isinstance(limit, int) else None, per=str(outcome.get("per") or "action"),
                subject=str(outcome.get("subject") or "company"),
                stale_minutes=stale if isinstance(stale, int) else STALE_MINUTES, active=active)


# ------------------------------------------------------------------------------------------------ matching
def _one(cond: object, value: object) -> bool:
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(_one(cond, v) for v in cast(Sequence[object], value))
    if isinstance(cond, list):
        return any(_scalar(x, value) for x in cast(list[object], cond))
    if isinstance(cond, dict):
        return all(_op(op, arg, value) for op, arg in cast(dict[str, object], cond).items())
    return _scalar(cond, value)


def _scalar(want: object, value: object) -> bool:
    if isinstance(want, bool) or isinstance(value, bool):
        return isinstance(want, bool) and isinstance(value, bool) and want == value
    return want == value


def _op(op: str, arg: object, value: object) -> bool:
    if op == "not":
        return not (any(_scalar(x, value) for x in cast(list[object], arg)) if isinstance(arg, list)
                    else _scalar(arg, value))
    if op == "matches":
        return isinstance(value, str) and re.search(str(arg), value, re.IGNORECASE) is not None
    if isinstance(value, bool) or not isinstance(value, int) or isinstance(arg, bool) or not isinstance(arg, int):
        return False
    return {"gt": value > arg, "gte": value >= arg, "lt": value < arg, "lte": value <= arg}[op]


def matches(when: Mapping[str, object], facts: Facts) -> bool:
    """Every key must match. A missing input matches only `not`; a list-valued input matches if any item does."""
    for key, cond in when.items():
        if key not in facts:
            if isinstance(cond, dict) and set(cast(dict[str, object], cond)) == {"not"}:
                continue
            return False
        if not _one(cond, facts[key]):
            return False
    return True


def stale_keys(rule: Rule, facts: Facts) -> set[str]:
    """The inputs the rule reads whose source is older than the rule tolerates (or of unknown age)."""
    ages = cast(Mapping[str, Any], facts.get(AGES) or {})
    out: set[str] = set()
    for key in rule.when:
        root = key.split(".", 1)[0]
        if root in ages:
            age = ages[root]
            if age is None or int(age) > rule.stale_minutes:
                out.add(key)
    return out


def stale(rule: Rule, facts: Facts) -> bool:
    """Whether the rule reads an input whose source is older than the rule tolerates (or of unknown age)."""
    return bool(stale_keys(rule, facts))


def period_key(per: str, day: date) -> str:
    if per == "day":
        return day.isoformat()
    if per == "week":
        iso = day.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"
    if per == "month":
        return day.isoformat()[:7]
    return ""


def subject_of(rule: Rule, facts: Facts) -> str:
    if rule.subject == "actor":
        return str(facts.get("actor") or "")
    if rule.subject == "recipient":
        return str(facts.get("recipient.id") or "")
    return "company"


Used = Callable[[Rule, str, str], int]


def evaluate(rules: Sequence[Rule], facts: Facts, day: date, used: Used | None = None) -> Verdict:
    """Run every active rule that matches. A rule over stale inputs asks rather than allow or limit (it still denies
    when it would), and a stale input never dismisses a rule: its freshness is checked before its value is relied on.
    A limit compares this action's amount plus what its period counter already holds."""
    amount = facts.get("amount")
    spend = amount if isinstance(amount, int) and not isinstance(amount, bool) else 0
    hits: list[Hit] = []
    counts: list[Hit] = []
    for r in rules:
        if not r.active:
            continue
        old = stale_keys(r, facts)
        full = matches(r.when, facts)
        if old:
            # A stale value can neither dismiss a rule nor satisfy an allow: when the rest of the condition holds,
            # the rule asks (a deny that matches in full still denies).
            if not matches({k: v for k, v in r.when.items() if k not in old}, facts) or \
                    (r.outcome == "allow" and not full):
                continue
            if r.outcome == "deny" and full:
                hits.append(Hit(r.id, "deny", r.who, r.reason or r.name))
                continue
            hits.append(Hit(r.id, "needs_approval", r.who if r.outcome == "needs_approval" and r.who else
                            "finance_admin", f"{r.name}: its data is stale", stale=True))
            continue
        if not full:
            continue
        if r.outcome == "limit":
            key, subject = period_key(r.per, day), subject_of(r, facts)
            before = used(r, key, subject) if used is not None and r.per != "action" else 0
            limit = r.limit or 0
            if before + spend > limit:
                hits.append(Hit(r.id, "deny", reason=r.reason or f"{r.name}: over its limit of {limit / 100:.2f}"
                                + (f" per {r.per}" if r.per != "action" else "")))
            else:
                hit = Hit(r.id, "limit", reason=r.reason or r.name, period_key=key, subject=subject)
                hits.append(hit)
                if r.per != "action":
                    counts.append(hit)
            continue
        hits.append(Hit(r.id, r.outcome, r.who, r.reason or r.name))
    return strictest(hits, counts)


def strictest(hits: Sequence[Hit], counts: Sequence[Hit] = ()) -> Verdict:
    if not hits:
        return Verdict("allow")
    top = max(RANK[h.outcome] for h in hits)
    winners = [h for h in hits if RANK[h.outcome] == top]
    if top == RANK["needs_approval"]:
        best = max(winners, key=lambda h: WHO.index(h.who) if h.who in WHO else 0)
        return Verdict("needs_approval", best.who or "finance_admin", best.rule, best.reason,
                       any(h.stale for h in winners), tuple(hits), tuple(counts))
    first = winners[0]
    return Verdict(first.outcome, "", first.rule, first.reason, False, tuple(hits),
                   tuple(counts) if top < RANK["deny"] else ())


def higher(a: str, b: str) -> str:
    """The higher of two approvers (owner > finance_admin > budget_owner > manager)."""
    return max(a, b, key=lambda w: RANK_WHO.index(w) if w in RANK_WHO else -1)


# ------------------------------------------------------------------------------------------------ examples
def parse_examples(raw: object) -> list[dict[str, object]]:
    items = V.items(raw, "examples", MAX_EXAMPLES)
    if not items:
        raise invalid("a rule needs at least one example")
    out: list[dict[str, object]] = []
    for item in items:
        p = V.obj(item, "an example")
        V.only(p, ("input", "expect"))
        expect = V.choice(p, "expect", OUTCOMES)
        out.append({"input": V.obj(p.get("input"), "an example's input"), "expect": expect})
    return out


def failing_examples(rule: Rule, examples: Sequence[Mapping[str, object]], day: date) -> list[str]:
    """Examples whose expected outcome this rule alone does not give (no match means allow)."""
    bad: list[str] = []
    for i, ex in enumerate(examples, 1):
        facts = cast(Mapping[str, object], ex.get("input") or {})
        got = evaluate([rule], facts, day).outcome
        if got != ex.get("expect"):
            bad.append(f"example {i} gives {got}, not {ex.get('expect')}")
    return bad


def words_for(when: Mapping[str, object], outcome: Mapping[str, object]) -> str:
    """Plain words for a rule when its writer gave none."""
    parts: list[str] = []
    for key, cond in when.items():
        if isinstance(cond, dict):
            names = {"gt": "over", "gte": "at least", "lt": "under", "lte": "at most", "not": "not", "matches": "like"}
            parts += [f"{key} {names[op]} {arg}" for op, arg in cast(dict[str, object], cond).items()]
        elif isinstance(cond, list):
            parts.append(f"{key} is any of {', '.join(str(x) for x in cast(list[object], cond))}")
        else:
            parts.append(f"{key} is {cond}")
    then = str(outcome.get("outcome"))
    if then == "needs_approval":
        then = f"ask the {str(outcome.get('who') or 'finance_admin').replace('_', ' ')}"
    elif then == "limit":
        then = f"limit to {int(cast(int, outcome.get('limit') or 0)) / 100:.2f} per {outcome.get('per') or 'action'}"
    return f"When {' and '.join(parts)}: {then}."[:300]


# ------------------------------------------------------------------------------------------------ defaults
# §6.4 rules 1, 2, 3 and 5, seeded at setup (every start, so existing installs get new ones once) and editable like any
# rule; a seeded rule a person changed or turned off stays as they left it. Rule 6 is seeded too, and fixed: nobody can
# edit or turn it off (FIXED), and kinds.default_gate enforces it whatever the row says. (id, name, words, when, outcome,
# examples)
DEFAULTS: tuple[tuple[str, str, str, dict[str, object], dict[str, object], list[dict[str, object]]], ...] = (
    ("rule:new-recipient", "New recipient", "Paying a recipient (or changed payment details) for the first time asks "
     "a finance admin.", {"kind": "payment_create", "recipient.known": False},
     {"outcome": "needs_approval", "who": "finance_admin"},
     [{"input": {"kind": "payment_create", "recipient.known": False, "amount": 5000}, "expect": "needs_approval"},
      {"input": {"kind": "payment_create", "recipient.known": True, "amount": 5000}, "expect": "allow"}]),
    ("rule:payment-over-1000", "Payments over $1,000", "A payment over $1,000 asks the owner.",
     {"kind": "payment_create", "amount": {"gt": 100000}}, {"outcome": "needs_approval", "who": "owner"},
     [{"input": {"kind": "payment_create", "amount": 100001}, "expect": "needs_approval"},
      {"input": {"kind": "payment_create", "amount": 100000}, "expect": "allow"}]),
    ("rule:po-over-1000", "Purchase orders over $1,000", "A purchase order over $1,000 asks the owner.",
     {"kind": "po_approve", "amount": {"gt": 100000}}, {"outcome": "needs_approval", "who": "owner"},
     [{"input": {"kind": "po_approve", "amount": 100001}, "expect": "needs_approval"},
      {"input": {"kind": "po_approve", "amount": 100000}, "expect": "allow"}]),
    ("rule:bot-card-standing", "Standing bot cards", "A standing card for a bot asks a finance admin; single-use cards "
     "up to $200 inside the bot's budget do not.",
     {"kind": "card_create", "holder.kind": "bot", "card.kind": {"not": "single_use"}},
     {"outcome": "needs_approval", "who": "finance_admin"},
     [{"input": {"kind": "card_create", "holder.kind": "bot", "card.kind": "person", "amount": 5000},
       "expect": "needs_approval"},
      {"input": {"kind": "card_create", "holder.kind": "bot", "card.kind": "single_use", "amount": 5000},
       "expect": "allow"},
      {"input": {"kind": "card_create", "holder.kind": "human", "card.kind": "person", "amount": 5000},
       "expect": "allow"}]),
    ("rule:bot-card-single-use-over-200", "Single-use bot cards over $200", "A single-use card for a bot over $200 "
     "asks a finance admin.",
     {"kind": "card_create", "holder.kind": "bot", "card.kind": "single_use", "amount": {"gt": 20000}},
     {"outcome": "needs_approval", "who": "finance_admin"},
     [{"input": {"kind": "card_create", "holder.kind": "bot", "card.kind": "single_use", "amount": 20001},
       "expect": "needs_approval"},
      {"input": {"kind": "card_create", "holder.kind": "bot", "card.kind": "single_use", "amount": 20000},
       "expect": "allow"}]),
    ("rule:bot-books-over-5000", "Bot bookkeeping over $5,000", "A bot categorizing or posting more than $5,000 in "
     "one action asks a finance admin.",
     {"kind": ["categorize", "journal"], "actor.kind": "bot", "amount": {"gt": 500000}},
     {"outcome": "needs_approval", "who": "finance_admin"},
     [{"input": {"kind": "categorize", "actor.kind": "bot", "amount": 500001}, "expect": "needs_approval"},
      {"input": {"kind": "journal", "actor.kind": "bot", "amount": 500000}, "expect": "allow"},
      {"input": {"kind": "categorize", "actor.kind": "human", "amount": 900000}, "expect": "allow"}]),
    ("rule:bot-new-account", "Bot adds an account", "A bot adding a line to the chart of accounts asks a finance "
     "admin.", {"kind": "account_create", "actor.kind": "bot"}, {"outcome": "needs_approval", "who": "finance_admin"},
     [{"input": {"kind": "account_create", "actor.kind": "bot"}, "expect": "needs_approval"},
      {"input": {"kind": "account_create", "actor.kind": "human"}, "expect": "allow"}]),
    ("rule:close-reopen-owner", "Closing or reopening a period", "Closing or reopening a period asks the owner, unless "
     "the owner does it.", {"kind": ["close_period", "reopen_period"], "actor.kind": {"not": "system"},
                            "actor.owner": False},
     {"outcome": "needs_approval", "who": "owner"},
     [{"input": {"kind": "close_period", "actor.kind": "human", "actor.owner": False}, "expect": "needs_approval"},
      {"input": {"kind": "reopen_period", "actor.kind": "bot", "actor.owner": False}, "expect": "needs_approval"},
      {"input": {"kind": "close_period", "actor.kind": "human", "actor.owner": True}, "expect": "allow"},
      {"input": {"kind": "close_period", "actor.kind": "system", "actor.owner": False}, "expect": "allow"}]),
)

# §6.4 rule 4 (budget at its limit with decline denies new holds and freezes the budget's cards) is an event reaction,
# not an action gate, and fin_rules holds only gate and posting rows. Finance shows it read-only beside the rules; the
# code enforces it: budgeting denies holds at a decline budget, and jobs.drain freezes its cards on the cutoff event.
# (id, name, words, where)
FLOORS: tuple[tuple[str, str, str, str], ...] = (
    ("floor:budget-cutoff-freeze", "Budget at limit", "A budget at its limit with decline denies new holds and freezes "
     "its cards.", "finance.budget.cutoff with at limit decline"),
)
# Rules nobody can change or turn off: the code keeps them (kinds.default_gate). Editing one is refused.
FIXED = ("rule:close-reopen-owner",)
