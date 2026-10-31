"""One planner per live action kind. A planner reads the payload, checks it against the books, the org chart and the
asker's view, and returns a Plan: the default gate it meets, its budget impact, and the write to run when it commits.
Planning writes nothing, so a dry run and an approval's re-check run the same code as the real thing."""
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from . import budgeting, posting
from .context import Ctx
from .core import budgets as B
from .core import dates, kinds as K
from .core import feeds as F
from .core import ledger as L
from .core import rights as R
from .core import validate as V
from .core import view as VW
from .core.errors import CONFLICT, NOT_FOUND, PERIOD_CLOSED, FinanceError, denied, invalid
from .core.org import SYSTEM
from .store import access as SA
from .store import ap as SAP
from .store import banking as SK
from .store import budgets as SB
from .store import ledger as SL
from .store import tico

Result = dict[str, object]


@dataclass
class Plan:
    gate: K.Gate
    summary: str
    apply: Callable[[str], Result]
    impact: Result = field(default_factory=lambda: Result())
    # Defaults that depend on when the action runs (a version's effective day, a reversal's date), fixed into the
    # canonical payload before an approval is asked, so what runs is exactly what was approved.
    resolved: Result = field(default_factory=lambda: Result())
    # What gate rules match on beyond kind and actor (amount, recipient, accounts, tags...), see finance/gating.py.
    facts: Result = field(default_factory=lambda: Result())


Planner = Callable[[Ctx, str, V.Payload], Plan]


# ------------------------------------------------------------------------------------------------ view helpers
def _account(ctx: Ctx, account_id: str) -> L.Account:
    account = ctx.accounts.get(account_id)
    if account is None:
        raise FinanceError(NOT_FOUND, "no such account")
    return account


def sees_scope(ctx: Ctx, who: str, account_id: str, team: str = "", teammate: str = "") -> bool:
    """Whether `who` sees all of a scope: a line (company-wide), a team on it, or a teammate on it."""
    view = ctx.view(who)
    if view.everything:
        return True
    account = ctx.accounts.get(account_id)
    if account is None:
        return False
    if teammate:
        return view.sees(account, {"team": team or ctx.org.team_of.get(teammate, ""), "teammate": teammate})
    if team:
        return view.covers_team(account, team, ctx.org.below(team))
    return view.company_wide(account)


def sees_budget(ctx: Ctx, who: str, b: B.Budget) -> bool:
    if b.catch_all:
        return ctx.view(who).company_expenses()
    return sees_scope(ctx, who, b.account_id, b.team, b.teammate)


def may_write(ctx: Ctx, who: str, account_id: str, tags: Mapping[str, str]) -> bool:
    """Whether `who` may write a line or scope with these tags on `account_id`. Reads take any slice that covers a line;
    a write checks each tag: the team inside the view on that account, and the teammate the writer or a member of a
    team the view covers. An untagged (unattributed) one needs company-wide view of the account."""
    view = ctx.view(who)
    if view.everything:
        return True
    account = ctx.accounts.get(account_id)
    if account is None or not view.sees_account(account):
        return False
    if account.type not in L.PNL_TYPES or view.company_wide(account):
        return view.sees(account, tags)
    team, mate = tags.get("team", ""), tags.get("teammate", "")
    if not team and not mate:
        return False
    teams = view.teams(account)
    mine = mate == who and any(s.whose == "own" and s.covers_account(account) for s in view.slices)
    if mate and not mine and ctx.org.team_of.get(mate, "") not in teams:
        return False
    return not team or team in teams or bool(mate) and team == ctx.org.team_of.get(mate, "")


def _need_line(ctx: Ctx, who: str, account_id: str, tags: Mapping[str, str], new: bool = False) -> None:
    if not may_write(ctx, who, account_id, tags):
        if new:
            raise denied("those tags are outside your view")
        raise FinanceError(NOT_FOUND, "no such line or account in your view")


def private_scope(ctx: Ctx, who: str, b: B.Budget) -> bool:
    """Whether a budget's spend takes in private lines `who` lacks the detail of: its numbers stay hidden from them."""
    private = ctx.private()
    if not private:
        return False
    detail = ctx.view(who).full_detail
    return any(a in private and a not in detail for a in budgeting.scope_accounts(ctx, b))


def shows_numbers(ctx: Ctx, who: str, b: B.Budget) -> bool:
    """Whether `who` may see a budget's remaining, spend and holds: in their view and over no private lines."""
    return sees_budget(ctx, who, b) and not private_scope(ctx, who, b)


def _budget_impact(ctx: Ctx, who: str, charges: list[tuple[L.Line, int]]) -> list[Result]:
    """For each budget the charges count against that `who` sees: remaining now and after. Budgets outside their view
    (a parent above a manager's teams) are left out; ones over private lines show no numbers."""
    at = dates.day(ctx.today)
    moved: dict[str, int] = {}
    for item, amount in charges:
        account = ctx.accounts.get(item.account_id)
        if account is None or account.type != "expense":
            continue
        b = budgeting.leaf(ctx, item.account_id, item.tags.get("team", ""), item.tags.get("teammate", ""),
                           item.contact_id)
        for bid in budgeting.chain_of(ctx, b.id) if b else []:
            moved[bid] = moved.get(bid, 0) + amount
    out: list[Result] = []
    for bid, amount in moved.items():
        b = ctx.budgets()[bid]
        if not sees_budget(ctx, who, b):
            continue
        if private_scope(ctx, who, b):
            out.append({"budget_id": bid, "name": b.name, "remaining": None, "remaining_after": None})
            continue
        s = budgeting.state(ctx, bid, at)
        out.append({"budget_id": bid, "name": b.name, "remaining": s.remaining,
                    "remaining_after": None if s.remaining is None else s.remaining - amount})
    return out


# ------------------------------------------------------------------------------------------------ books
def journal(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    if "reverses" in p:
        V.only(p, ("reverses", "date", "memo"))
        entry_id = V.text(p, "reverses", 80)
        row = SL.entry_row(ctx.c, entry_id)
        if row is None:
            raise FinanceError(NOT_FOUND, "no such entry")
        when, memo = V.opt_day(p, "date") or str(row["entry_date"]), V.opt_text(p, "memo") or ""
        lines = [line for _, line in SL.entry_lines(ctx.c, entry_id)]
        for item in lines:
            _need_line(ctx, who, item.account_id, item.tags)
        if row["status"] != "posted" or row["reverses_id"]:
            raise FinanceError(CONFLICT, "that entry is already reversed or is a reversal")
        # A reclassification of a reconciled row's spend is locked with that row's own entry (banking._reclass).
        moved = SK.get(ctx.c, "fin_feed_txns", F.reclass_of(str(row["source"]), str(row["source_id"])) or "-")
        if any(r["reconciliation_id"] for r in SK.rows_of_entry(ctx.c, entry_id)) or \
                (moved is not None and moved["reconciliation_id"]):
            raise FinanceError(CONFLICT, "that entry is in a reconciled statement; undo the reconciliation first")
        if str(row["source"]) in L.DOC_SOURCES and ":paid:" not in str(row["source_id"]):
            raise FinanceError(CONFLICT, "that entry belongs to a bill or invoice; void the document instead")
        total = sum(x.amount for x in lines if x.amount > 0)

        def undo(aid: str) -> Result:
            from .documents import entry_reversed
            out: Result = {"entry_id": posting.reverse(ctx, entry_id, day=when, memo=memo, by=who, action_id=aid),
                           "reverses": entry_id}
            entry_reversed(ctx, entry_id, who)       # a payment it recorded on a bill or invoice is owed again
            return out
        return Plan(K.default_gate(ctx.org, who, "journal"), f"Reverse entry {entry_id}", undo,
                    {"budgets": _budget_impact(ctx, who, [(x, -x.amount) for x in L.reversal(lines)])},
                    {"date": when}, {"amount": total})
    V.only(p, ("date", "memo", "source", "source_id", "lines", "basis"))
    e = L.entry(p, ctx.book)
    e = replace(e, lines=tuple(replace(x, tags=posting.with_team(ctx, x.tags)) for x in e.lines))
    L.check(e, ctx.book, ctx.accounts, SL.closed_on(ctx.c, ctx.book.id, e.entry_date))
    for item in e.lines:
        _need_line(ctx, who, item.account_id, item.tags, new=True)

    def apply(aid: str) -> Result:
        entry_id, line_ids = posting.post(ctx, e, by=who, action_id=aid)
        return {"entry_id": entry_id, "line_ids": line_ids}
    total = sum(x.amount for x in e.lines if x.amount > 0)
    return Plan(K.default_gate(ctx.org, who, "journal"),
                f"Post a {total / 100:.2f} {ctx.book.base_currency} entry on {e.entry_date}: {e.memo}"[:200], apply,
                {"budgets": _budget_impact(ctx, who, [(x, x.amount) for x in e.lines])},
                facts={"amount": total, "accounts": sorted({ctx.accounts[x.account_id].code for x in e.lines})})


def categorize(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """Add or replace tags on posted lines. Lines in a closed period are refused until it is reopened, so a closed
    month's scoped reports never change underneath. With `feed_id` it posts or ignores a bank or card row instead."""
    if "feed_id" in p:
        from .banking import categorize_feed
        return categorize_feed(ctx, who, p)
    V.only(p, ("lines", "mode"))
    mode = V.choice(p, "mode", ("add", "replace"), "add")
    wanted: dict[str, dict[str, str]] = {}
    for raw in V.items(p.get("lines"), "lines"):
        q = V.obj(raw, "a line")
        V.only(q, ("line_id", "tags"))
        wanted[V.text(q, "line_id", 80)] = L.tags(q.get("tags"))
    found = SL.lines(ctx.c, list(wanted))
    changes: list[tuple[object, dict[str, str], dict[str, str]]] = []
    charges: list[tuple[L.Line, int]] = []
    total = 0
    for lid, tags in wanted.items():
        if lid not in found:
            raise FinanceError(NOT_FOUND, "no such line in your view")
        row, line = found[lid]
        if row["book_id"] != ctx.book.id:
            raise FinanceError(NOT_FOUND, "no such line in your view")
        _need_line(ctx, who, line.account_id, line.tags)
        if SL.closed_on(ctx.c, ctx.book.id, str(row["entry_date"])):
            raise FinanceError(PERIOD_CLOSED, f"line {lid} is in a closed period; reopen it to change its tags")
        new = posting.with_team(ctx, {**line.tags, **tags} if mode == "add" else tags)
        _need_line(ctx, who, line.account_id, new, new=True)
        changes.append((row, dict(line.tags), new))
        charges += [(line, -line.amount), (replace(line, tags=new), line.amount)]
        total += abs(line.amount)

    def apply(aid: str) -> Result:
        from .documents import retagged
        for row, old, new in changes:
            SL.set_line_tags(ctx.c, row, old, new, who, ctx.at)
            retagged(ctx, row, old, new)        # the cash view's attribution follows the tags
        posting.touched(ctx, [c for c, _ in charges], who)
        return {"lines": len(changes)}
    return Plan(K.default_gate(ctx.org, who, "categorize"), f"Tag {len(changes)} line(s)", apply,
                {"budgets": _budget_impact(ctx, who, charges)}, facts={"amount": total})


def account_create(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    V.only(p, ("code", "name", "type", "subtype", "parent_id"))
    new = L.Account(id="", book_id=ctx.book.id, code=V.text(p, "code", 20), name=V.text(p, "name", 120),
                    type=V.choice(p, "type", L.ACCOUNT_TYPES), subtype=V.opt_text(p, "subtype", 40) or "",
                    parent_id=V.opt_text(p, "parent_id", 80) or "")
    L.check_account(new, ctx.accounts)

    def apply(aid: str) -> Result:
        made = replace(new, id=SL.new_id())
        SL.insert_account(ctx.c, made, who, ctx.at)
        ctx.changed()
        return {"account_id": made.id}
    return Plan(K.default_gate(ctx.org, who, "account_create"),
                f"Add account {new.code} {new.name} ({new.type})", apply)


def account_update(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    V.only(p, ("account_id", "code", "name", "subtype", "parent_id", "active"))
    old = _account(ctx, V.text(p, "account_id", 80))
    if not ctx.view(who).sees_account(old):
        raise FinanceError(NOT_FOUND, "no such account")
    parent = old.parent_id if "parent_id" not in p else V.opt_text(p, "parent_id", 80) or ""
    new = replace(old, code=V.opt_text(p, "code", 20) or old.code, name=V.opt_text(p, "name", 120) or old.name,
                  subtype=old.subtype if "subtype" not in p else V.opt_text(p, "subtype", 40) or "",
                  parent_id=parent, active=V.flag(p, "active", old.active))
    L.check_account(new, ctx.accounts)
    moved = new.parent_id != old.parent_id
    if moved:
        _may_move(ctx, who, old, new)

    def apply(aid: str) -> Result:
        SL.update_account(ctx.c, new, ctx.at)
        if moved:
            SA.bump_view(ctx.c, f"move {new.id}", ctx.at)
        ctx.changed()
        return {"account_id": new.id}
    return Plan(K.default_gate(ctx.org, who, "account_update"), f"Change account {old.code} {old.name}", apply)


def _may_move(ctx: Ctx, who: str, old: L.Account, new: L.Account) -> None:
    """Moving an account changes what line grants and budgets on its old and new parents take in, so it needs
    company-wide view of the account, everything under it, and both parent chains, and every budget must still fit."""
    view = ctx.view(who)
    touched = L.descendants(ctx.accounts, old.id) | set(L.ancestors(ctx.accounts, old.parent_id)) | \
        set(L.ancestors(ctx.accounts, new.parent_id))
    if not all(view.company_wide(ctx.accounts[a]) for a in touched):
        raise denied("moving an account needs company-wide view of it, the lines under it and where it goes")
    after = {**ctx.accounts, new.id: new}
    budgets = ctx.budgets()
    for b in budgets.values():
        if b.active:
            try:
                B.check(b, budgets, after, ctx.org)
            except FinanceError as e:
                raise invalid(f"moving it breaks budget {b.name}: {e.message}") from None


def _month(ctx: Ctx, p: V.Payload) -> tuple[str, str]:
    start = dates.month(V.month(p, "month"))
    return start.isoformat(), dates.month_end(start).isoformat()


def close_period(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    V.only(p, ("month",))
    start, end = _month(ctx, p)
    row = SL.period_by_start(ctx.c, ctx.book.id, start)
    if row is not None and row["status"] == "closed":
        raise FinanceError(CONFLICT, f"{start[:7]} is already closed")

    def apply(aid: str) -> Result:
        pid = SL.insert_period(ctx.c, ctx.book.id, start, end, who, ctx.at)
        SL.set_period_status(ctx.c, pid, "closed", who, ctx.at)
        return {"period_id": pid, "month": start[:7], "status": "closed"}
    return Plan(K.default_gate(ctx.org, who, "close_period"), f"Close {start[:7]}", apply)


def reopen_period(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    V.only(p, ("month",))
    start, _ = _month(ctx, p)
    row = SL.period_by_start(ctx.c, ctx.book.id, start)
    if row is None or row["status"] != "closed":
        raise FinanceError(CONFLICT, f"{start[:7]} is not closed")

    def apply(aid: str) -> Result:
        SL.set_period_status(ctx.c, str(row["id"]), "open", who, ctx.at)
        return {"period_id": str(row["id"]), "month": start[:7], "status": "open"}
    return Plan(K.default_gate(ctx.org, who, "reopen_period"), f"Reopen {start[:7]}", apply)


# ------------------------------------------------------------------------------------------------ budgets
def _effective(ctx: Ctx, b: B.Budget, raw: str | None) -> str:
    """A version starts on a period's first day (any day for a once budget)."""
    at = dates.day(raw or ctx.today)
    if b.period == "once":
        return at.isoformat()
    return B.period_of(b, at, ctx.book.fiscal_year_start, [])[0].isoformat()


def _fit(ctx: Ctx, who: str, b: B.Budget, effective: str, amount: int) -> None:
    """With `b` at `amount` from `effective`, children never pass their parent. The reason names amounts only when
    `who` may see every budget it sums."""
    budgets = ctx.budgets()
    near = [x for x in budgets.values() if x.active and x.id != b.id and
            (bool(b.id) and x.parent_id == b.id or bool(b.parent_id) and b.parent_id in (x.id, x.parent_id))]
    try:
        _fits(ctx, b, effective, amount)
    except FinanceError:
        if all(shows_numbers(ctx, who, x) for x in near):
            raise
        raise invalid("it does not fit: not enough left") from None


def _fits(ctx: Ctx, b: B.Budget, effective: str, amount: int) -> None:
    """b among its siblings under its parent, and b over its own children."""
    budgets = ctx.budgets()
    mine = SB.versions(ctx.c, [b.id])[b.id] if b.id else []
    mine.append(B.Version(budget_id=b.id or "new", version=10**9, effective_from=effective, amount=amount))
    if b.parent_id:
        parent = budgets[b.parent_id]
        sibling_ids = [x.id for x in budgets.values() if x.parent_id == parent.id and x.active and x.id != b.id]
        found = SB.versions(ctx.c, [parent.id, *sibling_ids])
        children: dict[str, list[B.Version]] = {sid: found[sid] for sid in sibling_ids}
        children[b.id or "new"] = mine
        every = [v for vs in children.values() for v in vs] + found[parent.id]
        B.check_fit(parent, found[parent.id], children, B.fit_points(dates.day(effective), every))
    kids = [x.id for x in budgets.values() if x.parent_id == b.id and x.active and b.id]
    if kids:
        found = SB.versions(ctx.c, kids)
        every = [v for vs in found.values() for v in vs] + mine
        B.check_fit(b, mine, found, B.fit_points(dates.day(effective), every))


def _kid_versions(ctx: Ctx, budget_id: str) -> dict[str, list[B.Version]]:
    """Each active child budget's versions."""
    kids = [x.id for x in ctx.budgets().values() if x.parent_id == budget_id and x.active]
    return SB.versions(ctx.c, kids) if kids else {}


def _cards_fit(ctx: Ctx, who: str, b: B.Budget, effective: str, amount: int) -> None:
    """A version may not set `b` below what its cards reserve plus its child budgets' amounts (§4.4), on every day a
    version changes from `effective` on (a child's later raise included). A card reserves the more of its allocation
    and what it has used in that period (holds and actuals). Names the cards to lower first where `who` may see them
    and the budget's numbers. Separate from `_fit`, which checks the children alone."""
    if not b.id or amount == 0:      # a pause (0) freezes its cards instead (cards.freeze_paused)
        return
    mine = SB.versions(ctx.c, [b.id])[b.id]
    mine.append(B.Version(budget_id=b.id, version=10**9, effective_from=effective, amount=amount))
    _reserve_fit(ctx, who, b, mine, _kid_versions(ctx, b.id), effective)


def _cards_fit_child(ctx: Ctx, who: str, child_id: str, parent: B.Budget, effective: str, amount: int) -> None:
    """A child's version may not push its parent's cards and child budgets past the parent's amount: `_cards_fit` on
    the parent, with this child's new version in the children's sum. A child's pause only lowers that sum."""
    if amount == 0:
        return
    key = child_id or "new"
    kids = {k: vs for k, vs in _kid_versions(ctx, parent.id).items() if k != child_id}
    own = SB.versions(ctx.c, [child_id])[child_id] if child_id else []
    own.append(B.Version(budget_id=key, version=10**9, effective_from=effective, amount=amount))
    kids[key] = own
    _reserve_fit(ctx, who, parent, SB.versions(ctx.c, [parent.id])[parent.id], kids, effective)


def _reactivate_fit(ctx: Ctx, who: str, b: B.Budget) -> None:
    """A budget that comes back active adds its current amount from today on, so it must fit as a version of today
    would: among its siblings (`_fit`), over its own cards and children, and within its parent's cards and children."""
    today = dates.day(ctx.today)
    current = B.amount_at(SB.versions(ctx.c, [b.id])[b.id], today)
    if not current:
        return
    _fit(ctx, who, b, today.isoformat(), current)
    _cards_fit(ctx, who, b, today.isoformat(), current)
    if b.parent_id:
        _cards_fit_child(ctx, who, b.id, ctx.budgets()[b.parent_id], today.isoformat(), current)


def _reserve_fit(ctx: Ctx, who: str, b: B.Budget, mine: list[B.Version], kids: dict[str, list[B.Version]],
                 effective: str) -> None:
    """On every day from `effective` that a version of `b` or of a child takes effect, `b`'s cards and its children's
    amounts must fit `mine`, b's amount. A day at 0 is skipped: a pause froze the cards, and `_fits` holds the
    children to 0 under it."""
    from .cards import sees as sees_card
    tree = budgeting.subtree(ctx, b.id)
    allocs = [row for row in SB.allocations(ctx.c, [b.id]) if row["target_kind"] == "card"]
    every = [v for vs in kids.values() for v in vs] + mine
    for at in B.fit_points(dates.day(effective), every):
        cap = B.amount_at(mine, at)
        if not cap:
            continue
        start, end = B.period_of(b, at, ctx.book.fiscal_year_start, mine)
        cards = _card_reserves(ctx, tree, allocs, start.isoformat(), end.isoformat())
        owed = sum(B.amount_at(vs, at) or 0 for vs in kids.values())
        total = sum(reserve for _, reserve in cards) + owed
        if not cards or total <= cap:
            continue
        if not shows_numbers(ctx, who, b) or not all(sees_card(ctx, who, card) for card, _ in cards):
            raise invalid("it does not fit: its cards reserve more than that")
        names = "; ".join(f"{card['nickname'] or card['holder']} ••{card['last4'] or '----'} "
                          f"reserves {reserve / 100:.2f}" for card, reserve in cards)
        raise invalid(f"it does not fit: its cards and child budgets reserve {total / 100:.2f}; "
                      f"lower these cards first: {names}")


def _card_reserves(ctx: Ctx, tree: list[str], allocs: list[Any], start: str, end: str) -> list[tuple[Any, int]]:
    """Each card allocated on a budget with what it reserves in the period: its allocation, or what it used if that is
    more (its holds and settled spend under the tree)."""
    out: list[tuple[Any, int]] = []
    for row in allocs:
        card = SK.get(ctx.c, "fin_cards", str(row["target_id"]))
        if card is None:
            continue
        used = SB.target_used(ctx.c, tree, start, end, f"card:{card['id']}", absorbed=True)
        reserve = max(int(row["amount"]), used)
        if reserve > 0:
            out.append((card, reserve))
    return out


def _need_budget_view(ctx: Ctx, who: str, b: B.Budget) -> None:
    """Changing a budget checks its scope like a write: each of its team and teammate inside the asker's view."""
    ok = ctx.view(who).company_expenses() if b.catch_all else \
        may_write(ctx, who, b.account_id, {"team": b.team, "teammate": b.teammate}) if b.team or b.teammate \
        else sees_scope(ctx, who, b.account_id)
    if not ok:
        raise denied("that budget's spend is outside your view")


def _budget_fields(p: V.Payload, base: B.Budget) -> B.Budget:
    return replace(base, name=V.opt_text(p, "name", 120) or base.name, owner=V.opt_text(p, "owner", 120) or base.owner,
                   warn_pct=V.integer(p, "warn_pct", 1, 100, base.warn_pct),
                   at_limit=V.choice(p, "at_limit", B.AT_LIMIT, base.at_limit),
                   rollover=V.choice(p, "rollover", B.ROLLOVER, base.rollover))


def budget_create(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    V.only(p, ("name", "period", "account_id", "catch_all", "team", "teammate", "vendor", "parent_id", "owner",
               "warn_pct", "at_limit", "rollover", "amount", "effective_from"))
    new = _budget_fields(p, B.Budget(
        id="", book_id=ctx.book.id, name=V.text(p, "name", 120), period=V.choice(p, "period", B.PERIODS, "month"),
        account_id=V.opt_text(p, "account_id", 80) or "", catch_all=V.flag(p, "catch_all"),
        team=V.opt_text(p, "team", 80) or "", teammate=V.opt_text(p, "teammate", 120) or "",
        vendor=SAP.vendor_resolved(ctx.c, ctx.book.id, V.opt_text(p, "vendor", 80) or ""),
        parent_id=V.opt_text(p, "parent_id", 80) or "", owner=who,
        created_at=ctx.at))
    B.check(new, ctx.budgets(), ctx.accounts, ctx.org)
    _need_budget_view(ctx, who, new)
    amount = V.opt_amount(p)
    if amount is not None and amount < 0:
        raise invalid("a budget amount is 0 or more")
    effective = _effective(ctx, new, V.opt_day(p, "effective_from"))
    if amount is not None:
        _fit(ctx, who, new, effective, amount)
        if new.parent_id:
            _cards_fit_child(ctx, who, "", ctx.budgets()[new.parent_id], effective, amount)

    def apply(aid: str) -> Result:
        made = replace(new, id=SL.new_id())
        SB.insert_budget(ctx.c, made, who, ctx.at)
        if amount is not None:
            SB.insert_version(ctx.c, made.id, effective, amount, ctx.book.base_currency, who, ctx.at)
        ctx.changed()
        return {"budget_id": made.id, "effective_from": effective if amount is not None else None}
    return Plan(K.default_gate(ctx.org, who, "budget_create"), f"Create budget {new.name}" +
                (f" at {amount / 100:.2f} {ctx.book.base_currency} per {new.period}" if amount is not None else ""),
                apply, {"effective_from": effective}, {"effective_from": effective} if amount is not None else {})


def _budget(ctx: Ctx, budget_id: str) -> B.Budget:
    b = ctx.budgets().get(budget_id)
    if b is None:
        raise FinanceError(NOT_FOUND, "no such budget")
    return b


def budget_update(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    V.only(p, ("budget_id", "name", "owner", "warn_pct", "at_limit", "rollover", "active", "amount", "effective_from"))
    old = _budget(ctx, V.text(p, "budget_id", 80))
    _need_budget_view(ctx, who, old)
    new = replace(_budget_fields(p, old), active=V.flag(p, "active", old.active))
    if new.active:
        B.check(new, ctx.budgets(), ctx.accounts, ctx.org)
    elif any(b.parent_id == old.id and b.active for b in ctx.budgets().values()):
        raise invalid("end the budgets under it first")
    amount = V.opt_amount(p)
    if amount is not None and amount < 0:
        raise invalid("a budget amount is 0 or more")
    effective = _effective(ctx, old, V.opt_day(p, "effective_from"))
    if amount == 0 and dates.day(effective) > dates.day(ctx.today):
        raise invalid("a pause takes effect now: leave effective_from out")
    reactivates = new.active and not old.active
    if amount is not None:
        if reactivates and dates.day(effective) > dates.day(ctx.today):
            raise invalid("a budget that comes back active takes its amount from today: leave effective_from out")
        _fit(ctx, who, old, effective, amount)
        _cards_fit(ctx, who, old, effective, amount)
        if old.parent_id:
            _cards_fit_child(ctx, who, old.id, ctx.budgets()[old.parent_id], effective, amount)
    elif reactivates:
        _reactivate_fit(ctx, who, old)

    def apply(aid: str) -> Result:
        SB.update_budget(ctx.c, new, ctx.at)
        version = SB.insert_version(ctx.c, old.id, effective, amount, ctx.book.base_currency, who, ctx.at) \
            if amount is not None else None
        ctx.changed()
        if amount == 0:
            from .cards import freeze_paused           # a pause freezes the cards on it and under it (§8.2)
            freeze_paused(ctx, old.id, aid, who)
        elif amount is not None:
            from .cards import unfreeze_with_room      # a raise that leaves room lifts the freezes under it
            unfreeze_with_room(ctx, old.id, aid)
        return {"budget_id": old.id, "version": version}
    return Plan(K.default_gate(ctx.org, who, "budget_update"), f"Change budget {old.name}" +
                (f" to {amount / 100:.2f} {ctx.book.base_currency} from {effective}" if amount is not None else ""),
                apply, {"effective_from": effective}, {"effective_from": effective} if amount is not None else {})


def allocation_set(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """Set aside part of a budget for one card, Gateway key or teammate. It must fit what the budget and every parent
    have left; 0 ends it. A card's allocation is on the card's own budget, by someone who sees the card."""
    V.only(p, ("budget_id", "target_kind", "target_id", "amount"))
    if p.get("target_kind") == "card":
        from .cards import check_lower, sees as sees_card
        card = SK.get(ctx.c, "fin_cards", V.text(p, "target_id", 120))
        if card is None or not sees_card(ctx, who, card):
            raise FinanceError(NOT_FOUND, "no such card")
        if str(card["budget_id"] or "") != V.text(p, "budget_id", 80):
            raise invalid("a card's allocation is on its own budget")
        if str(card["status"]) == "cancelled":
            raise FinanceError(CONFLICT, "that card is cancelled")
        check_lower(ctx, card, V.amount(p))
    return allocation_plan(ctx, who, p)


def allocation_plan(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """The allocation itself (card_create and card_update_limit come here with their own card checks). A card's lower
    allocation keeps the old reservation until Mercury confirms the lower limit (cards.limit_follows)."""
    V.only(p, ("budget_id", "target_kind", "target_id", "amount"))
    b = _budget(ctx, V.text(p, "budget_id", 80))
    _need_budget_view(ctx, who, b)
    kind, target = V.choice(p, "target_kind", B.TARGETS), V.text(p, "target_id", 120)
    amount = V.amount(p)
    if amount < 0:
        raise invalid("an allocation is 0 or more")
    if kind == "teammate" and target not in ctx.org.humans | ctx.org.bots:
        raise invalid("no such teammate")
    row = SB.allocation(ctx.c, b.id, kind, target)
    grow = amount - (int(row["amount"]) if row else 0)
    at = dates.day(ctx.today)
    if grow > 0:
        for one in B.chain(ctx.budgets(), b.id):
            s = budgeting.state(ctx, one.id, at)
            if s.remaining is not None and grow > s.remaining:
                if shows_numbers(ctx, who, one):
                    raise invalid(f"it does not fit: {one.name} has {s.remaining} left")
                raise invalid("it does not fit: not enough left")
    lowered = kind == "card" and grow < 0

    def apply(aid: str) -> Result:
        made = str(row["id"]) if lowered and row else SB.upsert_allocation(
            ctx.c, budget_id=b.id, target_kind=kind, target_id=target, amount=amount, currency=ctx.book.base_currency,
            by=who, at=ctx.at)
        if kind == "card":
            from .cards import limit_follows          # a card's provider limit is its allocation (§8.2)
            limit_follows(ctx, target, amount, aid, who, lowered)
        return {"allocation_id": made}
    return Plan(K.default_gate(ctx.org, who, "allocation_set"), f"Set aside {amount / 100:.2f} of {b.name} for "
                f"{kind} {target}", apply)


def projection_set(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    V.only(p, ("account_id", "month", "amount", "team", "teammate", "method"))
    account = _account(ctx, V.text(p, "account_id", 80))
    if account.type not in L.PNL_TYPES:
        raise invalid("projections are for P&L lines")
    team, mate = V.opt_text(p, "team", 80) or "", V.opt_text(p, "teammate", 120) or ""
    if team and team not in ctx.org.teams:
        raise invalid("no such team")
    if not (may_write(ctx, who, account.id, {"team": team, "teammate": mate}) if team or mate
            else sees_scope(ctx, who, account.id)):
        raise denied("that line is outside your view")
    month, amount = V.month(p, "month"), V.amount(p)
    method = V.choice(p, "method", B.METHODS, "manual")

    def apply(aid: str) -> Result:
        return {"projection_id": SB.upsert_projection(ctx.c, book_id=ctx.book.id, account_id=account.id, month=month,
                                                      team=team, teammate=mate, amount=amount,
                                                      currency=ctx.book.base_currency, method=method, by=who,
                                                      at=ctx.at)}
    return Plan(K.default_gate(ctx.org, who, "projection_set"), f"Project {account.name} for {month}", apply)


# ------------------------------------------------------------------------------------------------ access
def _until(p: V.Payload) -> str:
    value = V.opt_text(p, "expires_at", 40) or ""
    if value:
        dates.day(value[:10])
    return value


def view_grant(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    V.only(p, ("subject_kind", "subject", "what", "whose", "whose_team", "account_ids", "new_members", "expires_at"))
    kind = V.choice(p, "subject_kind", ("teammate", "team"), "teammate")
    subject = V.text(p, "subject", 120)
    ids = frozenset(V.texts(p, "account_ids")) if p.get("account_ids") else frozenset[str]()
    members = ctx.org.members(ctx.org.below(subject)) if kind == "team" and subject in ctx.org.teams \
        else frozenset[str]()
    g = VW.Grant(id="", subject_kind=kind, subject=subject, what=V.choice(p, "what", VW.WHAT),
                 whose=V.choice(p, "whose", VW.WHOSE, "company"), whose_team=V.opt_text(p, "whose_team", 80) or "",
                 account_ids=ids, new_members=V.flag(p, "new_members"), members=members, granted_by=who,
                 expires_at=_until(p))
    verdict = VW.check_grant(ctx.org, who, g, SA.defaults(ctx.c), ctx.accounts)
    gate = K.Gate("needs_approval", "manager", "default:bot-grant") if verdict == "approval" \
        else K.default_gate(ctx.org, who, "view_grant")

    def apply(aid: str) -> Result:
        made = replace(g, id=SL.new_id())
        SA.insert_grant(ctx.c, made, who, ctx.at)
        version = SA.bump_view(ctx.c, f"grant {made.id}", ctx.at)
        ctx.changed()
        return {"grant_id": made.id, "view_version": version}
    return Plan(gate, f"Let {kind} {subject} see {g.what} ({g.whose})", apply)


def view_revoke(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    V.only(p, ("grant_id",))
    g = SA.grant(ctx.c, V.text(p, "grant_id", 80))
    if g is None:
        raise FinanceError(NOT_FOUND, "no such grant")
    if not VW.may_revoke(ctx.org, who, g):
        raise denied("only whoever made a grant, or the owner, takes it back")
    if g.status not in ("active", "suspended"):
        raise FinanceError(CONFLICT, f"that grant is already {g.status}")

    def apply(aid: str) -> Result:
        SA.set_grant_status(ctx.c, g.id, "revoked", who, ctx.at)
        version = SA.bump_view(ctx.c, f"revoke {g.id}", ctx.at)
        ctx.changed()
        return {"grant_id": g.id, "view_version": version}
    return Plan(K.default_gate(ctx.org, who, "view_revoke"), f"Take back grant {g.id}", apply)


def private_lines_set(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    V.only(p, ("account_id", "private", "others_see", "full_detail"))
    account = _account(ctx, V.text(p, "account_id", 80))
    if account.type != "expense":
        raise invalid("private lines are expense lines (payroll by person)")
    private = V.flag(p, "private", True)
    others = V.choice(p, "others_see", VW.OTHERS_SEE, "totals")
    detail = V.texts(p, "full_detail") if p.get("full_detail") else []
    for actor in detail:
        if actor not in ctx.org.humans | ctx.org.bots:
            raise invalid("full_detail names teammates")

    def apply(aid: str) -> Result:
        if private:
            SA.set_private(ctx.c, account.id, others, detail, who, ctx.at)
        else:
            SA.clear_private(ctx.c, account.id)
        version = SA.bump_view(ctx.c, f"private {account.id}", ctx.at)
        ctx.changed()
        return {"account_id": account.id, "private": private, "view_version": version}
    return Plan(K.default_gate(ctx.org, who, "private_lines_set"),
                f"{'Make' if private else 'Stop making'} {account.name} private", apply)


def view_defaults_set(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """What each role sees by default (spec §7.5, the Access page's "Edit defaults"): managers, everyone else, and
    whether managers see their department's payroll total. Owner-only; grants a manager made that the new defaults no
    longer back are suspended."""
    V.only(p, ("manager", "member", "manager_payroll"))
    picks = {"manager": VW.MANAGER_POLICIES, "member": VW.MEMBER_POLICIES, "manager_payroll": VW.PAYROLL_POLICIES}
    wanted = {role: V.choice(p, role, options) for role, options in picks.items() if role in p}
    if not wanted:
        raise invalid("name at least one of manager, member, manager_payroll")
    old = SA.defaults(ctx.c)

    def apply(aid: str) -> Result:
        for role, policy in wanted.items():
            SA.set_default(ctx.c, role, policy, who, ctx.at)
        version = SA.bump_view(ctx.c, "defaults", ctx.at)
        ctx.changed()
        suspended = suspend_unstanding(ctx)
        now = SA.defaults(ctx.c)
        return {"defaults": {"manager": now.manager, "member": now.member, "manager_payroll": now.manager_payroll},
                "suspended_grants": suspended, "view_version": version}
    changes = ", ".join(f"{role} {getattr(old, role)} to {policy}" for role, policy in wanted.items())
    return Plan(K.ALLOW, f"Change Finance view defaults: {changes}", apply)


def right_grant(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    V.only(p, ("teammate", "right", "expires_at"))
    mate, right = V.text(p, "teammate", 120), V.choice(p, "right", R.RIGHTS)
    if mate not in ctx.org.humans | ctx.org.bots:
        raise invalid("no such teammate")
    if mate == who:
        raise denied("nobody grants rights to themselves")
    if right == R.ADMIN and who != ctx.org.owner:
        raise denied("only the owner makes a finance admin")
    until = _until(p) or None

    def apply(aid: str) -> Result:
        rid = SA.grant_right(ctx.c, mate, right, who, until, ctx.at)
        ctx.changed()
        return {"right_id": rid}
    return Plan(K.default_gate(ctx.org, who, "right_grant"), f"Give {mate} {right}", apply)


def right_revoke(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    V.only(p, ("teammate", "right"))
    mate, right = V.text(p, "teammate", 120), V.choice(p, "right", R.RIGHTS)
    if right not in SA.rights_of(ctx.c, mate, ctx.at):
        raise FinanceError(CONFLICT, f"{mate} does not hold {right}")
    if right == R.ADMIN and who != ctx.org.owner:
        raise denied("only the owner takes finance admin away")

    def apply(aid: str) -> Result:
        SA.revoke_right(ctx.c, mate, right, who, ctx.at)
        ctx.changed()
        return {"teammate": mate, "right": right}
    return Plan(K.default_gate(ctx.org, who, "right_revoke"), f"Take {right} from {mate}", apply)


def suspend_unstanding(ctx: Ctx) -> list[str]:
    """Write down what view resolution already does: grants their grantor could no longer make are suspended, with a
    view version bump and an audit row, so the owner can see them (spec §7.5)."""
    bad = VW.unstanding(ctx.org, SA.grants(ctx.c), SA.defaults(ctx.c), ctx.accounts)
    for gid in bad:
        SA.set_grant_status(ctx.c, gid, "suspended", SYSTEM, ctx.at)
        tico.audit(ctx.c, SYSTEM, "finance.grant_suspended", gid, {"reason": "exceeds its grantor's authority"})
    if bad:
        SA.bump_view(ctx.c, f"suspend {len(bad)}", ctx.at)
        ctx.changed()
    return bad


PLANNERS: dict[str, Planner] = {
    "journal": journal, "categorize": categorize, "account_create": account_create, "account_update": account_update,
    "close_period": close_period, "reopen_period": reopen_period, "budget_create": budget_create,
    "budget_update": budget_update, "allocation_set": allocation_set, "projection_set": projection_set,
    "view_grant": view_grant, "view_revoke": view_revoke, "private_lines_set": private_lines_set,
    "view_defaults_set": view_defaults_set,
    "right_grant": right_grant, "right_revoke": right_revoke}
assert set(PLANNERS) <= set(K.LIVE)          # the rest live in banking, cards, payments and gating (actions.py)

__all__ = ["Plan", "PLANNERS", "sees_scope", "sees_budget", "shows_numbers", "private_scope", "may_write",
           "suspend_unstanding", "SYSTEM"]
