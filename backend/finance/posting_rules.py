"""Posting rules (spec §6.1–6.3): on a new feed row (a Tico card's too) and on a bill line with no account, the first
active rule by priority whose conditions hold says where it posts (an account or a split, with tags and a contact) or
that a person decides (`ask`). A row no rule covers, or one a rule asks about, raises finance.feed.needs_category: a
debit still posts to Other expenses (a Tico card's charge to its budget line) so the books stay whole; a credit waits.
Rules are written through gating's rule actions with `kind: posting`; their examples must pass before they are saved."""
from collections.abc import Mapping
from typing import Any, cast

from .context import Ctx
from .core import ap as AP
from .core import feeds as F
from .core import gates as G
from .core import validate as V
from .core.errors import CONFLICT, NOT_FOUND, FinanceError, denied, invalid
from .store import ap as SAP
from .store import banking as SK

CONTROL_SUBTYPES = ("payable", "receivable")


def rule_of(row: Any) -> AP.PostingRule:
    return AP.PostingRule(str(row["id"]), str(row["name"]), SK.loads(row["condition"]),
                          AP.parse_posting(SK.loads(row["outcome"])), int(row["priority"]))


def rules(ctx: Ctx) -> list[AP.PostingRule]:
    out: list[AP.PostingRule] = []
    for row in SAP.posting_rules(ctx.c):
        try:
            out.append(rule_of(row))
        except FinanceError:
            continue                        # a rule whose stored outcome no longer parses runs as no rule
    return out


def first_for(ctx: Ctx, facts: Mapping[str, object]) -> AP.PostingRule | None:
    return AP.first_match(rules(ctx), facts)


def feed_facts(row: Any, money: Any) -> dict[str, object]:
    """What a posting rule matches on for a feed row."""
    amount = int(row["amount"])
    return {"kind": "feed", "amount": abs(amount), "direction": "out" if amount < 0 else "in",
            "counterparty": str(row["counterparty"] or ""), "description": str(row["description"] or ""),
            "money_account_id": str(money["id"]), "money.kind": str(money["kind"]), "feed.kind": str(row["kind"] or "")}


def usable(ctx: Ctx, then: AP.Posting) -> bool:
    """Whether every account a rule posts to is still an active line it may post to."""
    ids = [s.account_id for s in then.split] or [then.account_id]
    return all(i in ctx.accounts and ctx.accounts[i].active and ctx.accounts[i].subtype not in CONTROL_SUBTYPES
               for i in ids)


def lines_for(ctx: Ctx, row: Any, rule: AP.PostingRule, base: Mapping[str, str] | None = None,
              keep: Mapping[str, str] | None = None) -> list[dict[str, object]]:
    """The non-money lines a rule gives a feed row (feed_line shape), signed against the row: the rule's tags over
    `base`, with `keep` (a card holder's teammate tag) over both."""
    from . import posting
    from .banking import feed_line
    amount = int(row["amount"])
    contact = rule.then.contact_id or str(row["counterparty"] or "")
    return [feed_line(account, -amount // abs(amount) * part,
                      posting.with_team(ctx, {**(base or {}), **tags, **(keep or {})}), contact)
            for account, part, tags in AP.split_amounts(abs(amount), rule.then)]


def card_lines(ctx: Ctx, row: Any, money: Any, account_id: str,
               tags: Mapping[str, str]) -> tuple[list[dict[str, object]], bool]:
    """The spend lines of a charge or refund on a card Tico issued, and whether a person should look. The first
    matching posting rule sets the account, tags and vendor; the holder's teammate tag stays, and the money line (the
    card liability, which card use is read from) is the caller's, so a rule never moves the charge off the card.
    No rule, or an `ask`, keeps the card's budget line; an `ask` also asks."""
    from .banking import feed_line
    rule = first_for(ctx, feed_facts(row, money))
    if rule is not None and rule.then.outcome == "post" and usable(ctx, rule.then):
        keep = {"teammate": tags["teammate"]} if tags.get("teammate") else {}
        return lines_for(ctx, row, rule, tags, keep), False
    return [feed_line(account_id, -int(row["amount"]), tags, str(row["counterparty"] or ""))], rule is not None


def post_feed(ctx: Ctx, row: Any, money: Any, t: F.FeedTxn, gl_id: str) -> bool:
    """Post a new settled row by the first matching rule, else by the counterparty's contact default line. False when
    nothing covers it (or a rule asks): the caller posts its fallback and raises needs_category."""
    from .banking import feed_line, mark_posted, post_row
    rule = first_for(ctx, feed_facts(row, money))
    fid = str(row["id"])
    if rule is not None and rule.then.outcome == "post" and usable(ctx, rule.then):
        entry = post_row(ctx, fid, t, lines_for(ctx, row, rule), gl_id, [])
        mark_posted(ctx, [fid], entry)
        return True
    if rule is not None:
        return False                        # ask, or a rule whose line was retired: a person decides
    contact = SAP.contact_named(ctx.c, ctx.book.id, t.counterparty) if t.counterparty else None
    default = str(contact["default_account_id"] or "") if contact is not None else ""
    account = ctx.accounts.get(default)
    if contact is None or account is None or not account.active or (t.amount < 0) != (account.type != "income"):
        return False
    entry = post_row(ctx, fid, t, [feed_line(account.id, -t.amount, {}, str(contact["id"]))], gl_id, [])
    mark_posted(ctx, [fid], entry)
    return True


# ------------------------------------------------------------------------------------------------ writing rules
def checked(ctx: Ctx, who: str, p: V.Payload, base: Mapping[str, object] | None = None) -> dict[str, object]:
    """A posting rule's fields, validated: its accounts active lines inside the writer's view (never accounts payable
    or receivable, which only documents move), and its examples passing."""
    from .handlers import may_write
    old = base or {}
    name = V.opt_text(p, "name", 120) or str(old.get("name") or "")
    if not name:
        raise invalid("a rule has a name")
    when = G.parse_when(p["when"]) if "when" in p else cast(dict[str, object], old.get("when") or {})
    then = AP.parse_posting(p["outcome"]) if "outcome" in p else AP.parse_posting(old.get("outcome") or {})
    if not when:
        raise invalid("a rule has a when")
    targets = (then.split or (AP.Split(then.account_id, AP.SHARE_WHOLE, {}),)) if then.outcome == "post" else ()
    for s in targets:
        account = ctx.accounts.get(s.account_id)
        if account is None or not account.active or account.subtype in CONTROL_SUBTYPES:
            raise FinanceError(NOT_FOUND, "no such account for a posting rule")
        if not may_write(ctx, who, account.id, {**then.tags, **s.tags}):
            raise denied("a rule's account or tags are outside your view")
    if then.contact_id and SAP.get(ctx.c, "fin_contacts", then.contact_id) is None:
        raise FinanceError(NOT_FOUND, "no such contact")
    examples = AP.parse_posting_examples(p["examples"]) if "examples" in p else \
        cast(list[dict[str, object]], old.get("examples") or [])
    if not examples:
        raise invalid("a rule needs at least one example")
    bad = AP.failing_posting_examples(when, then, examples)
    if bad:
        raise invalid("its examples do not pass: " + "; ".join(bad[:5]))
    kept = old.get("priority")              # 0 is a priority (first of all), not "none given"
    priority = V.integer(p, "priority", 0, 10_000) if p.get("priority") is not None else \
        int(cast(int, kept)) if kept is not None else AP.DEFAULT_PRIORITY
    outcome = AP.posting_json(then)
    words = V.opt_text(p, "words", 300) or str(old.get("words") or "") or G.words_for(when, outcome)
    external = V.opt_text(p, "external_rule_id", 120) if "external_rule_id" in p else old.get("external_rule_id")
    return {"name": name, "words": words, "when": when, "outcome": outcome, "examples": examples,
            "priority": priority, "external_rule_id": external or None}


def test(ctx: Ctx, who: str, rule: AP.PostingRule, since: str, most: int) -> dict[str, object]:
    """What a posting rule would have done to the feed rows since `since` the tester sees: each row it matches, with
    where it posted then and where the rule puts it."""
    from .tab import feed_scope
    money_ids, card_ids = feed_scope(ctx, ctx.view(who))
    seen_money, seen_cards = set(money_ids), set(card_ids)
    money = {str(m["id"]): m for m in SK.money_accounts(ctx.c, ctx.book.id)}
    changed: list[dict[str, object]] = []
    checked_n = 0
    for row in SK.feed_since(ctx.c, since, most):
        if str(row["money_account_id"]) not in seen_money and str(row["card_id"] or "") not in seen_cards:
            continue
        m = money.get(str(row["money_account_id"]))
        if m is None or int(row["pending"]):
            continue
        checked_n += 1
        if not G.matches(rule.when, feed_facts(row, m)):
            continue
        item: dict[str, object] = {"feed_id": str(row["id"]), "kind": "feed", "at": str(row["txn_date"]),
                                   "amount": int(row["amount"]), "counterparty": str(row["counterparty"]),
                                   "was": str(row["status"]), "would": rule.then.outcome}
        if rule.then.outcome == "post":
            item["account_ids"] = [s.account_id for s in rule.then.split] or [rule.then.account_id]
        changed.append(item)
    return {"checked": checked_n, "changed": changed[:500], "changed_count": len(changed), "since": since}


def need_kind(row: Any, kind: str) -> None:
    if str(row["kind"]) != kind:
        raise FinanceError(CONFLICT, f"that is a {row['kind']} rule")


__all__ = ["rules", "rule_of", "first_for", "feed_facts", "post_feed", "card_lines", "checked", "test", "lines_for"]
