"""Attribution: which teammates (people or bots) and teams a posted P&L line counts for, in shares.

A line's own tags say who it counts for. An attribution replaces that for a share of the line (core/attribution.py has
the split math: basis points, largest-remainder cents, the unattributed rest). Precedence for a line: a person's or
bot's override > a matching rule (store/attribution.py) > the line's tags.

Actions (all need finance.attribution, which the owner and finance admins have; none asks for approval, bots included):
- attribute {line_id, splits}: put shares on a line. splits [] clears the override.
- attribute_undo {line_id}: put back what the line had before its latest change (a new history row, never a delete).
- attribution_rule_set {id?, name, match, splits, enabled?, apply_from?}: a rule by vendor, P&L account or money
  account. apply_from also re-applies it to lines posted from that day on that no person or bot has overridden.
- attribution_rule_delete {id}: the rule's lines go back to what the other rules and their tags give; overrides stay.

Every change rewrites the rollups the line's parts roll into (accrual and cash), and budgets are checked for crossings.
A closed period is never changed (the same rule categorize keeps): a line in one keeps what it had until reopened.
Tico-side only: nothing here writes to a provider's books.
"""

from collections.abc import Iterable, Sequence
from dataclasses import replace
from typing import Any

from . import posting
from .context import Ctx
from .core import attribution as CA
from .core import kinds as K
from .core import ledger as L
from .core import validate as V
from .core.errors import CONFLICT, NOT_FOUND, PERIOD_CLOSED, FinanceError, invalid
from .handlers import Plan, Result, budget_impact, private_hidden
from .store import attribution as ATT
from .store import ledger as SL

MAX_TEXT = 80


def _splits(raw: object, ctx: Ctx) -> tuple[CA.Split, ...]:
    """The shares a request names, checked: each names a current teammate and/or a team, shares are whole basis points
    from 1 to 10000 and add up to at most 10000, at most MAX_PARTS of them. An empty list clears."""
    out: list[CA.Split] = []
    total = 0
    for item in V.items(raw, "splits", CA.MAX_PARTS):
        q = V.obj(item, "a share")
        V.only(q, ("teammate", "team", "share_bps"))
        mate = V.opt_text(q, "teammate", MAX_TEXT) or ""
        team = V.opt_text(q, "team", MAX_TEXT) or ""
        if not mate and not team:
            raise invalid("a share names a teammate or a team")
        if mate and mate not in ctx.org.humans and mate not in ctx.org.bots:
            raise invalid("no such teammate")
        if team and team not in ctx.org.teams:
            raise invalid("no such team")
        bps = V.integer(q, "share_bps", 1, CA.WHOLE)
        total += bps
        out.append(CA.Split(mate, team, bps))
    if total > CA.WHOLE:
        raise invalid("the shares add up to more than 100%")
    return tuple(out)


def visible(ctx: Ctx, who: str, line: L.Line) -> bool:
    """Whether `who` sees a line the way the transactions read does: its account, its tags, and no private line they
    lack the detail of."""
    account = ctx.accounts.get(line.account_id)
    if account is None:
        return False
    view = ctx.view(who)
    if not view.sees_account(account) or not view.sees(account, line.tags):
        return False
    return not private_hidden(ctx, who, line.account_id)


def _line(ctx: Ctx, who: str, line_id: str) -> tuple[Any, L.Line]:
    """A posted P&L line the actor sees and that no closed period holds. Anything else reads as not found."""
    found = SL.lines(ctx.c, [line_id])
    if line_id not in found:
        raise FinanceError(NOT_FOUND, "no such line in your view")
    row, line = found[line_id]
    if str(row["book_id"]) != ctx.book.id or not visible(ctx, who, line):
        raise FinanceError(NOT_FOUND, "no such line in your view")
    if ctx.accounts[line.account_id].type not in L.PNL_TYPES:
        raise invalid("only a P&L line takes attribution")
    if SL.closed_on(ctx.c, ctx.book.id, str(row["entry_date"])):
        raise FinanceError(PERIOD_CLOSED, f"line {line_id} is in a closed period; reopen it to change its attribution")
    return row, line


def _source_of(ctx: Ctx, who: str) -> str:
    return "bot" if who in ctx.org.bots else "person"


def _parts_lines(ctx: Ctx, line: L.Line, splits: Sequence[CA.Split]) -> list[L.Line]:
    """The line with each rollup part's tags, for the budgets it counts against."""
    return [replace(line, tags=dict(p.tags)) for p in CA.parts(line.tags, line.amount, splits, ctx.org.team_of)]


def _charges(ctx: Ctx, line: L.Line, before: Sequence[CA.Split], after: Sequence[CA.Split]) -> list[tuple[L.Line, int]]:
    """Each old part taken off its tags and each new part put on, signed as categorize signs a retag."""
    out: list[tuple[L.Line, int]] = []
    for p in CA.parts(line.tags, line.amount, before, ctx.org.team_of):
        out.append((replace(line, tags=dict(p.tags)), -p.amount))
    for p in CA.parts(line.tags, line.amount, after, ctx.org.team_of):
        out.append((replace(line, tags=dict(p.tags)), p.amount))
    return out


def _reroll(ctx: Ctx, who: str, row: Any, line: L.Line, before: Sequence[CA.Split], after: Sequence[CA.Split]) -> None:
    """Move a line's parts from its old splits to its new ones everywhere they roll: the accrual rollups, the cash view
    (its correction and each settlement's share), and the budgets they count against (crossings raised for `who`)."""
    from .documents import reattributed  # lazy: documents imports the handlers this module sits beside

    SL.reattribute(ctx.c, row, before, after, ctx.org.team_of)
    reattributed(ctx, row, before, after)
    posting.touched(ctx, [*_parts_lines(ctx, line, before), *_parts_lines(ctx, line, after)], who)


def _shown(line: L.Line, splits: Sequence[CA.Split], source: str, line_id: str) -> Result:
    shares = CA.shares_of(splits, line.amount)
    return {
        "line_id": line_id,
        "source": source if splits else "tags",
        "splits": [
            {"teammate": s.teammate, "team": s.team, "share_bps": s.share_bps, "amount": amount}
            for s, amount in zip(splits, shares, strict=False)
        ],
    }


def _set_line(ctx: Ctx, who: str, line_id: str, splits: Sequence[CA.Split], source: str, rule_id: str) -> bool:
    """Put an attribution in force on a line. False (nothing written) when the line is gone, in a closed period, or
    already has exactly this (a clear of a line with no attribution counts as this)."""
    found = SL.lines(ctx.c, [line_id])
    if line_id not in found:
        return False
    row, line = found[line_id]
    if SL.closed_on(ctx.c, ctx.book.id, str(row["entry_date"])):
        return False
    now_source, now_rule, now_splits = ATT.in_force_source(ctx.c, line_id)
    if (now_source, now_rule, now_splits) == (source, rule_id, tuple(splits)) or (not now_source and not splits):
        return False
    ATT.write(
        ctx.c,
        book_id=ctx.book.id,
        line_id=line_id,
        splits=splits,
        source=source,
        rule_id=rule_id,
        by=who,
        at=ctx.at,
    )
    _reroll(ctx, who, row, line, now_splits, splits)
    return True


def _refresh(ctx: Ctx, who: str, line_id: str, rules: Sequence[Any]) -> bool:
    """Bring a line in line with the book's rules: a person's or bot's override stays; otherwise the rule that now
    matches it (judged across every live rule, by precedence) is in force, or a rule's old effect is cleared."""
    if line_id in ATT.lines_owned_by_people(ctx.c, [line_id]):
        return False
    facts = ATT.facts_of_line(ctx.c, ctx.book.id, line_id)
    claim = ATT.claim_for(rules, facts) if facts is not None else None
    if claim is not None:
        return _set_line(ctx, who, line_id, claim.splits, "rule", claim.rule_id)
    source, rule_id, _ = ATT.in_force_source(ctx.c, line_id)
    if source == "rule":
        return _set_line(ctx, who, line_id, (), "rule", rule_id)
    return False


def _refresh_all(ctx: Ctx, who: str, line_ids: Iterable[str]) -> int:
    rules = ATT.rules(ctx.c, ctx.book.id)
    return sum(1 for line_id in dict.fromkeys(line_ids) if _refresh(ctx, who, line_id, rules))


# ------------------------------------------------------------------------------------------------ lines
def attribute(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    V.only(p, ("line_id", "splits"))
    line_id = V.text(p, "line_id", 80)
    if "splits" not in p:
        raise invalid("splits is required (an empty list clears the attribution)")
    splits = _splits(p.get("splits"), ctx)
    row, line = _line(ctx, who, line_id)
    before = ATT.in_force_splits(ctx.c, line_id)
    source = _source_of(ctx, who)

    def apply(aid: str) -> Result:
        now_source, _, now_splits = ATT.in_force_source(ctx.c, line_id)
        if (now_source, now_splits) == (source, splits) or (not now_source and not splits):
            return _shown(line, splits, source, line_id)
        ATT.write(
            ctx.c,
            book_id=ctx.book.id,
            line_id=line_id,
            splits=splits,
            source=source,
            rule_id="",
            by=who,
            at=ctx.at,
        )
        _reroll(ctx, who, row, line, ATT.in_force_splits(ctx.c, line_id), splits)
        return _shown(line, splits, source, line_id)

    what = f"a {abs(line.amount) / 100:.2f} line"
    summary = f"Attribute {what} to {len(splits)} share(s)" if splits else f"Clear the attribution of {what}"
    return Plan(
        K.ALLOW,
        summary,
        apply,
        {"budgets": budget_impact(ctx, who, _charges(ctx, line, before, splits))},
        facts={"amount": abs(line.amount)},
    )


def attribute_undo(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    V.only(p, ("line_id",))
    line_id = V.text(p, "line_id", 80)
    row, line = _line(ctx, who, line_id)
    prior = ATT.previous(ctx.c, line_id)
    if prior is None:
        raise FinanceError(CONFLICT, "nothing to undo on that line")
    before = ATT.in_force_splits(ctx.c, line_id)
    target = ATT.parse_splits(str(prior["splits_json"]))
    source = str(prior["source"])
    rule_id = str(prior["rule_id"] or "")

    def apply(aid: str) -> Result:
        ATT.write(
            ctx.c,
            book_id=ctx.book.id,
            line_id=line_id,
            splits=target,
            source=source,
            rule_id=rule_id,
            by=who,
            at=ctx.at,
        )
        _reroll(ctx, who, row, line, ATT.in_force_splits(ctx.c, line_id), target)
        return _shown(line, target, source, line_id)

    return Plan(
        K.ALLOW,
        "Undo the last attribution change on a line",
        apply,
        {"budgets": budget_impact(ctx, who, _charges(ctx, line, before, target))},
        facts={"amount": abs(line.amount)},
    )


# ------------------------------------------------------------------------------------------------ rules
def _target(ctx: Ctx, kind: str, match_id: str) -> None:
    """A rule's match must name something in the book: a vendor, a P&L account, or a money account (a non-P&L one)."""
    if kind == "vendor":
        row = ctx.c.execute("SELECT book_id FROM fin_contacts WHERE id=?", (match_id,)).fetchone()
        if row is None or str(row[0]) != ctx.book.id:
            raise invalid("no such vendor")
        return
    account = ctx.accounts.get(match_id)
    if account is None:
        raise invalid("no such account")
    if kind == "account" and account.type not in L.PNL_TYPES:
        raise invalid("an account rule names a P&L account")
    if kind == "money_account" and account.type in L.PNL_TYPES:
        raise invalid("a money account rule names a bank, card or payables account")


def attribution_rule_set(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    V.only(p, ("id", "name", "match", "splits", "enabled", "apply_from"))
    rule_id = V.opt_text(p, "id", 80) or ""
    name = V.text(p, "name", MAX_TEXT)
    match = V.obj(p.get("match"), "match")
    V.only(match, ("kind", "id"))
    kind = V.choice(match, "kind", ATT.MATCH_KINDS)
    match_id = V.text(match, "id", 200)
    _target(ctx, kind, match_id)
    if "splits" not in p:
        raise invalid("splits is required")
    splits = _splits(p.get("splits"), ctx)
    if not splits:
        raise invalid("a rule names its shares")
    enabled = V.flag(p, "enabled", True)
    apply_from = V.opt_day(p, "apply_from")
    existing = None
    if rule_id:
        existing = ATT.rule(ctx.c, rule_id)
        if existing is None or str(existing["book_id"]) != ctx.book.id or existing["deleted_at"] is not None:
            raise FinanceError(NOT_FOUND, "no such rule")
    taking = ATT.rule_taking(ctx.c, ctx.book.id, kind, match_id)
    if taking is not None and str(taking["id"]) != rule_id:
        raise FinanceError(CONFLICT, "a rule already names that vendor, account or card")
    new_id = rule_id or ATT.new_id()

    def apply(aid: str) -> Result:
        ATT.put_rule(
            ctx.c,
            rule_id=new_id,
            book_id=ctx.book.id,
            name=name,
            match_kind=kind,
            match_id=match_id,
            splits=splits,
            enabled=enabled,
            apply_from=apply_from,
            by=who,
            at=ctx.at,
            exists=existing is not None,
        )
        # The lines the rule put on before (its terms or being disabled may move them), and with apply_from the lines
        # posted from that day on that it now matches. Overrides by a person or bot are never touched.
        lines = ATT.lines_with_rule(ctx.c, new_id)
        if apply_from is not None and enabled:
            lines += ATT.candidate_lines(ctx.c, ctx.book.id, ATT.rule(ctx.c, new_id), apply_from)
        return {"id": new_id, "lines": _refresh_all(ctx, who, lines)}

    what = "Change" if existing is not None else "Add"
    return Plan(K.ALLOW, f"{what} the attribution rule {name}", apply)


def attribution_rule_delete(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    V.only(p, ("id",))
    rule_id = V.text(p, "id", 80)
    row = ATT.rule(ctx.c, rule_id)
    if row is None or str(row["book_id"]) != ctx.book.id or row["deleted_at"] is not None:
        raise FinanceError(NOT_FOUND, "no such rule")

    def apply(aid: str) -> Result:
        lines = ATT.lines_with_rule(ctx.c, rule_id)
        ATT.delete_rule(ctx.c, rule_id, ctx.at)
        return {"id": rule_id, "lines": _refresh_all(ctx, who, lines)}

    return Plan(K.ALLOW, f"Delete the attribution rule {row['name']}", apply)


PLANNERS: dict[str, Any] = {
    "attribute": attribute,
    "attribute_undo": attribute_undo,
    "attribution_rule_set": attribution_rule_set,
    "attribution_rule_delete": attribution_rule_delete,
}

__all__ = ["PLANNERS", "attribute", "attribute_undo", "attribution_rule_delete", "attribution_rule_set"]
