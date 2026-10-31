"""Finance's reads (spec §8): the P&L, expenses, a line's detail, budgets and single metrics. Every read resolves the
viewer's view first, cuts the numbers to it, runs them through the one suppression function, and only then sums, so
every derived number (totals, percents, "Other") is built from what the viewer may see. Callers outside backend/finance
read fin_* only through here."""
import json
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, cast

from . import budgeting, limits, tab, tab_ap, tab_hooks, tab_money, usage
from . import context as _context
from .store import usage as SU
from .context import Ctx, load
from .core import ap as AP
from .core import budgets as B
from .core import rights as R
from .core import dates, money
from .core import view as VW
from .core.errors import FinanceError, NOT_FOUND, denied, invalid
from .core.ledger import PNL_TYPES, descendants, pnl_section
from .handlers import private_scope, sees_budget, sees_scope, shows_numbers
from .store import actions as SX
from .store import access as SA
from .store import banking as SK
from .store import budgets as SB
from .store import ledger as SL
from .store import ap as SAP

UNATTRIBUTED = "Unattributed"
GROUPS = ("department", "team", "teammate", "bot", "person", "vendor", "account")
MAX_TOP = 200
Row = dict[str, object]


@dataclass
class _Seen:
    ctx: Ctx
    view: VW.View
    private: dict[str, VW.PrivateLine]


def _open(c: sqlite3.Connection, viewer: str, at: str | None) -> _Seen:
    ctx = load(c, at)
    return _Seen(ctx=ctx, view=ctx.view(viewer), private=SA.private_lines(c))


def _window(start: str | None, end: str | None, ctx: Ctx) -> tuple[date, date]:
    if start is None and end is None:
        return dates.month_bounds(dates.day(ctx.today))
    a, z = dates.day(start or end), dates.day(end or start)
    if z < a:
        raise invalid("end is before start")
    return a, z


def _cells(s: _Seen, a: date, z: date, account_ids: Iterable[str] | None = None, basis: str = "accrual"
           ) -> list[VW.Cell]:
    """The rollup totals this viewer's view takes in, before suppression: the window, and for private accounts the
    whole fiscal quarters around it, since their cells are whole pay runs and quarters chosen without the window. The
    cash basis adds the cash view's corrections (bills and invoices count when and as far as they are paid)."""
    ids = sorted(account_ids) if account_ids is not None else sorted(s.ctx.accounts)
    hidden = [i for i in ids if i in s.private and i not in s.view.full_detail]
    plain = [i for i in ids if i not in hidden]
    fy = s.ctx.book.fiscal_year_start
    wide = (dates.fiscal_bounds(a, 3, fy)[0], dates.fiscal_bounds(z, 3, fy)[1])
    out: list[VW.Cell] = []
    for ids_, (p, q) in ((plain, (a, z)), (hidden, wide)):
        rows = SL.rollup_rows(s.ctx.c, s.ctx.book.id, p.isoformat(), q.isoformat(), ids_)
        if basis == "cash":
            rows = [*rows, *SAP.cash_rows(s.ctx.c, s.ctx.book.id, p.isoformat(), q.isoformat(), ids_)]
        for r in rows:
            account = s.ctx.accounts.get(r["account_id"])
            if account is None or not s.view.sees(account, {"team": r["team"], "teammate": r["teammate"]}):
                continue
            out.append(VW.Cell(account_id=r["account_id"], day=r["day"], team=r["team"], teammate=r["teammate"],
                               vendor=r["vendor"], amount=int(r["amount"])))
    return out


def _suppress(s: _Seen, cells: list[VW.Cell], narrow: bool, a: date, z: date) -> VW.Suppressed:
    return VW.suppress(s.view, s.ctx.org, s.private, s.ctx.accounts, cells, narrow, window=(a, z),
                       closed=SL.closed_months(s.ctx.c, s.ctx.book.id), fiscal_start=s.ctx.book.fiscal_year_start)


def _cap(rows: list[Row], top_n: int) -> list[Row]:
    """The largest `top_n` rows and one "Other · n" row for the rest."""
    top_n = max(1, min(int(top_n), MAX_TOP))
    rows.sort(key=lambda r: (-abs(int(str(r["amount"]))), str(r["label"])))
    if len(rows) <= top_n:
        return rows
    rest = rows[top_n:]
    return rows[:top_n] + [{"key": "other", "label": f"Other · {len(rest)}", "amount":
                            sum(int(str(r["amount"])) for r in rest), "count": len(rest)}]


def _fold_pnl(out: Row, top_n: int) -> Row:
    """A P&L capped for a payload: the `top_n` largest lines (by total, in chart order) and, per type, one "Other · n"
    row carrying the rest's per-period actuals and total. Columns, totals and net stay whole."""
    rows = cast(list[Row], out["rows"])
    if len(rows) <= top_n:
        return out
    size = len(cast(list[object], out["columns"]))
    ranked = sorted(range(len(rows)), key=lambda i: -abs(cast(int, rows[i]["total"] or 0)))
    keep = set(ranked[:top_n])
    shown = [r for i, r in enumerate(rows) if i in keep]
    for type_ in ("income", "expense"):
        rest = [r for i, r in enumerate(rows) if i not in keep and r["type"] == type_]
        if not rest:
            continue
        actual = [0] * size
        for r in rest:
            for n, v in enumerate(cast(list[int], r["actual"] or [])):
                actual[n] += v
        shown.append({"key": "other", "label": f"Other · {len(rest)}", "type": type_, "account_id": None,
                      "section": None, "private": False, "actual": actual, "total": sum(actual), "budget": None,
                      "projection": None, "count": len(rest)})
    return {**out, "rows": shown, "rows_total": len(rows)}


def _hidden_labels(s: _Seen, hidden: Iterable[str]) -> list[Row]:
    return [{"account_id": a, "label": VW.PRIVATE_LABEL} for a in sorted(hidden)]


def _bucket_index(buckets: list[tuple[date, date]], day: str) -> int:
    d = dates.day(day)
    for i, (a, z) in enumerate(buckets):
        if a <= d <= z:
            return i
    return -1


# ------------------------------------------------------------------------------------------------ P&L
def _top_budgets(s: _Seen) -> list[B.Budget]:
    """The budgets this viewer sees whose parent they do not: one budget figure per scope, never double counted."""
    budgets = s.ctx.budgets()
    seen = {b.id for b in budgets.values() if b.active and not b.catch_all and sees_budget(s.ctx, s.view.viewer, b)}
    return [budgets[i] for i in seen if budgets[i].parent_id not in seen]


PNL_VIEWS = ("company", "expenses")


WHO = ("both", "bots", "people")


@dataclass(frozen=True)
class _Filter:
    """The expenses view's org-chart filter: teams (with the teams under them) and teammates picked together, bots,
    people or both, and Unattributed (lines with no team or teammate)."""
    teams: tuple[str, ...] = ()
    teammates: tuple[str, ...] = ()
    who: str = "both"
    unattributed: bool = False
    below: frozenset[str] | None = None

    @property
    def any(self) -> bool:
        return bool(self.teams or self.teammates or self.unattributed or self.who != "both")

    def _kind(self, mate: str) -> bool:
        return self.who == "both" or mate.startswith("bot:" if self.who == "bots" else "human:")

    def keeps(self, team: str, mate: str) -> bool:
        if not team and not mate and self.unattributed:
            return True
        picked = bool(self.teams or self.teammates or self.unattributed)
        hit = not picked or mate in self.teammates or (self.below is not None and team in self.below)
        return hit and (self.who == "both" or (bool(mate) and self._kind(mate)))

    def narrow(self, org: Any) -> bool:
        """Whether the filter is narrower than whole departments (a teammate, bots or people, a team below a
        department): private payroll shows none of its cells then (§7.5)."""
        return bool(self.teammates) or self.who != "both" or any(org.department(t) != t for t in self.teams)

    def keeps_budget(self, b: B.Budget) -> bool:
        in_teams = self.below is not None and b.team in self.below
        if b.teammate:
            picked = bool(self.teams or self.teammates or self.unattributed)
            return self._kind(b.teammate) and (not picked or b.teammate in self.teammates or in_teams)
        return self.who == "both" and in_teams


def _org_filter(s: _Seen, teams: Iterable[str], teammates: Iterable[str], who: str, unattributed: bool) -> _Filter:
    picked_teams = tuple(dict.fromkeys(t for t in teams if t))
    picked_mates = tuple(dict.fromkeys(m for m in teammates if m))
    if who not in WHO:
        raise invalid(f"who is {'|'.join(WHO)}")
    for t in picked_teams:
        if t not in s.ctx.org.teams:
            raise FinanceError(NOT_FOUND, "no such team")
    if unattributed and not s.view.company_expenses():
        raise denied("Unattributed is for viewers who see company-wide expenses")
    below = frozenset[str]().union(*(s.ctx.org.below(t) for t in picked_teams)) if picked_teams else None
    return _Filter(picked_teams, picked_mates, who, unattributed, below)


def _filter_budgets(s: _Seen, f: _Filter) -> list[B.Budget]:
    """The budgets the filter shows: theirs, one per scope, never a parent and its child both."""
    if not f.any:
        return _top_budgets(s)
    budgets = s.ctx.budgets()
    picked = {b.id for b in budgets.values() if b.active and not b.catch_all and sees_budget(s.ctx, s.view.viewer, b)
              and f.keeps_budget(b)}
    return [budgets[i] for i in picked if budgets[i].parent_id not in picked]


def _column_state(p: date, q: date, today: date) -> str:
    return "past" if q < today else ("future" if p > today else "current")


def pnl(c: sqlite3.Connection, viewer: str, start: str | None = None, end: str | None = None, period: str = "month",
        basis: str = "cash", at: str | None = None, *, view: str = "company", team: str | None = None,
        teammate: str | None = None, budget_as_of: str | None = None, teams: Iterable[str] = (),
        teammates: Iterable[str] = (), who: str = "both", unattributed: bool = False) -> Row:
    """Income and expense lines by period, with budget and projection beside each actual. Income is posted as credits
    (negative); it shows as positive revenue, so net = income - expense. `view=expenses` keeps cost of revenue and
    operating expenses only, filtered to a team (and the teams under it) or a teammate, hides lines with no spend in
    the filter, and lists the filter's teammates with spent vs budget. Columns say whether they are past, current or
    projected; `budget_as_of` compares to the budgets as they stood then. `teams` and `teammates` pick several at once
    (with `team` and `teammate`), `who` keeps bots, people or both, and `unattributed` adds lines with no team or
    teammate (for viewers who see company-wide expenses). Each row says its section (revenue, cost of revenue,
    operating, other). Accrual counts bills and invoices when entered or sent; cash counts their lines on the
    dates and in the proportions they were paid, and leaves out accrual-only journals (card spend counts when
    charged on both)."""
    s = _open(c, viewer, at)
    if basis not in ("cash", "accrual"):
        raise invalid("basis is cash|accrual")
    if period not in dates.BUCKETS:
        raise invalid(f"period is {'|'.join(dates.BUCKETS)}")
    if view not in PNL_VIEWS:
        raise invalid(f"view is {'|'.join(PNL_VIEWS)}")
    f = _org_filter(s, [*([team] if team else []), *teams], [*([teammate] if teammate else []), *teammates], who,
                    unattributed)
    if f.any and view != "expenses":
        raise invalid("a team or teammate filter is for the expenses view")
    made_by = ""
    if budget_as_of:
        made_by = budget_as_of if "T" in budget_as_of else dates.day(budget_as_of).isoformat() + "T23:59:59.999999Z"
    a, z = _window(start, end, s.ctx)
    fy = s.ctx.book.fiscal_year_start
    today = dates.day(s.ctx.today)
    buckets = dates.buckets(period, a, z, fy)
    types = ("expense",) if view == "expenses" else PNL_TYPES
    pnl_ids = [x.id for x in s.ctx.accounts.values() if x.type in types and s.view.sees_account(x)]
    cells = _cells(s, a, z, pnl_ids, basis)
    if f.any:
        cells = [x for x in cells if f.keeps(x.team, x.teammate)]
    sup = _suppress(s, cells, f.narrow(s.ctx.org), a, z)
    actual: dict[str, list[int]] = {i: [0] * len(buckets) for i in pnl_ids}
    spent: dict[str, int] = {}
    for cell in sup.cells:
        if cell.account_id in sup.hidden:
            continue
        n = _bucket_index(buckets, cell.day)
        if n >= 0 and cell.account_id in actual:
            sign = -1 if s.ctx.accounts[cell.account_id].type == "income" else 1
            actual[cell.account_id][n] += sign * cell.amount
            if cell.teammate:
                spent[cell.teammate] = spent.get(cell.teammate, 0) + cell.amount
    budget: dict[str, list[int | None]] = {}
    tops = _filter_budgets(s, f)
    versions = SB.versions(c, [b.id for b in tops], made_by)
    for b in tops:
        cols = [B.in_window(b, versions[b.id], bucket, fy) for bucket in buckets]
        row = budget.setdefault(b.account_id, [None] * len(buckets))
        budget[b.account_id] = [x if y is None else (y if x is None else x + y) for x, y in zip(row, cols, strict=False)]
    projection: dict[str, list[int]] = {}
    if buckets:
        for r in SB.projections(c, s.ctx.book.id, buckets[0][0].isoformat()[:7], buckets[-1][1].isoformat()[:7]):
            account = s.ctx.accounts.get(r["account_id"])
            if account is None or account.id not in actual or \
                    (account.id in s.private and account.id not in s.view.full_detail) or \
                    not s.view.sees(account, {"team": r["team"], "teammate": r["teammate"]}) or \
                    (f.any and not f.keeps(str(r["team"] or ""), str(r["teammate"] or ""))):
                continue
            first = dates.month(r["month"])
            span = (first, dates.month_end(first))
            cols = projection.setdefault(account.id, [0] * len(buckets))
            for n, bucket in enumerate(buckets):
                share = dates.overlap(span, bucket)
                if share:
                    cols[n] += money.prorate(int(r["amount"]), share, dates.days(*span))
    rows: list[Row] = []
    totals = {"income": [0] * len(buckets), "expense": [0] * len(buckets)}
    for account_id in pnl_ids:
        x = s.ctx.accounts[account_id]
        hidden = account_id in sup.hidden
        if not hidden:
            for n, v in enumerate(actual[account_id]):
                totals[x.type][n] += v
        rows.append({"account_id": x.id, "code": x.code, "name": VW.PRIVATE_LABEL if hidden else x.name,
                     "type": x.type, "section": pnl_section(s.ctx.accounts, x), "parent_id": x.parent_id,
                     "private": hidden,
                     "actual": None if hidden else actual[account_id],
                     "total": None if hidden else sum(actual[account_id]),
                     # "Payroll (private)" with no amount (§7.5): its budget is withheld too, since a narrow
                     # filter's payroll budget can be one person's planned pay.
                     "budget": None if hidden else budget.get(account_id),
                     "projection": None if hidden else projection.get(account_id)})
    if view == "expenses":
        rows = _with_spend(s, rows)
    out: Row = {"start": a.isoformat(), "end": z.isoformat(), "period": period, "basis": basis, "view": view,
                "team": team, "teammate": teammate, "budget_as_of": budget_as_of,
                "filter": {"teams": list(f.teams), "teammates": list(f.teammates), "who": f.who,
                           "unattributed": f.unattributed},
                "columns": [{"start": p.isoformat(), "end": q.isoformat(), "label": dates.label(p, period),
                             "state": _column_state(p, q, today)} for p, q in buckets],
                "rows": rows, "income": totals["income"], "expense": totals["expense"],
                "net": [i - e for i, e in zip(totals["income"], totals["expense"], strict=False)],
                "excluded": sup.excluded, "view_version": s.view.version}
    if view == "expenses":
        out["teammates"] = _side_panel(s, spent, tops, versions, a, z, f)
    return out


def _with_spend(s: _Seen, rows: list[Row]) -> list[Row]:
    """Rows with spend in the filter, a budget or a projection, and the lines above them."""
    keep: set[str] = set()
    for r in rows:
        if r["private"] or any(r.get(k) and any(cast(list[object], r[k])) for k in ("actual", "budget", "projection")):
            at = str(r["account_id"])
            while at and at not in keep:
                keep.add(at)
                parent = s.ctx.accounts.get(at)
                at = parent.parent_id if parent else ""
    return [r for r in rows if r["account_id"] in keep]


def _side_panel(s: _Seen, spent: Mapping[str, int], tops: list[B.Budget], versions: Mapping[str, list[B.Version]],
                a: date, z: date, f: _Filter) -> list[Row]:
    """The filter's teammates with what they spent and the sum of their own line budgets in the window."""
    fy = s.ctx.book.fiscal_year_start
    budgets: dict[str, int] = {}
    for b in tops:
        if b.teammate:
            amount = B.in_window(b, versions.get(b.id, []), (a, z), fy)
            if amount is not None:
                budgets[b.teammate] = budgets.get(b.teammate, 0) + amount
    people = set(spent) | set(budgets) | set(f.teammates)
    if f.below is not None:
        people |= {m for m in s.ctx.org.members(f.below)}
    people = {m for m in people if m in spent or m in f.teammates or f.keeps(s.ctx.org.team_of.get(m, ""), m)}
    rows: list[Row] = [{"teammate": m, "kind": "bot" if m in s.ctx.org.bots else "person", "spent": spent.get(m, 0),
                        "budget": budgets.get(m)} for m in sorted(people)]
    return rows[:MAX_TOP]


# ------------------------------------------------------------------------------------------------ expenses
def _key(s: _Seen, cell: VW.Cell, by: str) -> str | None:
    """The group a cell falls in; "" is Unattributed; None leaves it out (a person's line in a by-bot read)."""
    if by == "department":
        return s.ctx.org.department(cell.team) if cell.team else ""
    if by == "team":
        return cell.team
    if by == "teammate":
        return cell.teammate
    if by == "bot":
        return cell.teammate if cell.teammate.startswith("bot:") else None
    if by == "person":
        return cell.teammate if cell.teammate.startswith("human:") else None
    if by == "vendor":
        return cell.vendor
    return cell.account_id


def _label(s: _Seen, key: str, by: str) -> str:
    if not key:
        return UNATTRIBUTED if by != "vendor" else "No vendor"
    if by in ("department", "team"):
        team = s.ctx.org.teams.get(key)
        return team.name if team else key
    if by == "account":
        account = s.ctx.accounts.get(key)
        return account.name if account else key
    return key


def _grouped(s: _Seen, a: date, z: date, by: str, account_id: str | None, team: str | None,
             teammate: str | None, top_n: int) -> Row:
    if by not in GROUPS:
        raise invalid(f"by is {'|'.join(GROUPS)}")
    if account_id:
        root = s.ctx.accounts.get(account_id)
        if root is None or not s.view.sees_account(root):
            raise FinanceError(NOT_FOUND, "no such account")
        ids = sorted(descendants(s.ctx.accounts, account_id))
    else:
        ids = sorted(x.id for x in s.ctx.accounts.values() if x.type == "expense")
    cells = _cells(s, a, z, ids)
    below = s.ctx.org.below(team) if team else None
    if below is not None:
        cells = [x for x in cells if x.team in below]
    if teammate:
        cells = [x for x in cells if x.teammate == teammate]
    department_wide = not teammate and (not team or s.ctx.org.department(team) == team)
    narrow = by not in ("department", "account") or not department_wide
    sup = _suppress(s, cells, narrow, a, z)
    sums: dict[str, int] = {}
    for cell in sup.cells:
        key = _key(s, cell, by)
        if key is None or (key == "" and by in ("department", "team", "teammate") and
                           not s.view.company_expenses()):
            continue
        sums[key] = sums.get(key, 0) + cell.amount
    rows: list[Row] = [{"key": k or "unattributed", "label": _label(s, k, by), "amount": v} for k, v in sums.items()]
    return {"by": by, "start": a.isoformat(), "end": z.isoformat(), "rows": _cap(rows, top_n),
            "total": sum(sums.values()), "excluded": sup.excluded, "hidden": _hidden_labels(s, sup.hidden),
            "view_version": s.view.version}


def expenses(c: sqlite3.Connection, viewer: str, start: str | None = None, end: str | None = None,
             by: str = "department", *, account_id: str | None = None, team: str | None = None,
             teammate: str | None = None, top_n: int = 20, at: str | None = None) -> Row:
    """Expenses grouped by department, team, teammate, bot, person, vendor or line. Unattributed (no team or teammate)
    shows only to viewers who see company-wide expenses."""
    s = _open(c, viewer, at)
    a, z = _window(start, end, s.ctx)
    return _grouped(s, a, z, by, account_id, team, teammate, top_n)


def spend_by(c: sqlite3.Connection, viewer: str, by: str, start: str | None = None, end: str | None = None, *,
             account_id: str | None = None, team: str | None = None, top_n: int = 10, at: str | None = None) -> Row:
    """The top spenders on a line or across expenses: a ranking with one "Other · n" row."""
    s = _open(c, viewer, at)
    a, z = _window(start, end, s.ctx)
    return _grouped(s, a, z, by, account_id, team, None, top_n)


# ------------------------------------------------------------------------------------------------ one line
def _next_month(s: _Seen, ids: Sequence[str], f: _Filter | None = None) -> Row:
    """The line's projection for the month after this one, as far as the viewer sees it and within the org filter
    (projections are by line, team and teammate; there are none by vendor). None on a private line without its
    detail."""
    nxt = dates.month_end(dates.day(s.ctx.today)) + timedelta(days=1)
    month = nxt.isoformat()[:7]
    if any(i in s.private and i not in s.view.full_detail for i in ids):
        return {"month": month, "projection": None}
    total, found = 0, False
    for r in SB.projections(s.ctx.c, s.ctx.book.id, month, month):
        account = s.ctx.accounts.get(r["account_id"])
        if account is None or account.id not in ids or \
                not s.view.sees(account, {"team": r["team"], "teammate": r["teammate"]}) or \
                (f is not None and f.any and not f.keeps(str(r["team"] or ""), str(r["teammate"] or ""))):
            continue
        total, found = total + int(r["amount"]), True
    return {"month": month, "projection": total if found else None}


def _paid_by(s: _Seen, rows: Sequence[Row]) -> list[Row]:
    """How the shown lines were paid: by card (its nickname, last 4 only), from a bank account, or by an entry with no
    feed row (a journal, an import, Tico's own usage)."""
    cards: dict[str, Any] = {}
    money: dict[str, Any] = {}
    sums: dict[tuple[str, str], list[int]] = {}
    for r in rows:
        card_id, money_id = str(r.get("card_id") or ""), str(r.get("money_account_id") or "")
        if card_id:
            card = cards.get(card_id) or SK.get(s.ctx.c, "fin_cards", card_id)
            cards[card_id] = card
            label = (str(card["nickname"]) or f"Card ••{card['last4'] or ''}") if card is not None else "Card"
            key = ("card", label)
        elif money_id:
            m = money.get(money_id) or SK.get(s.ctx.c, "fin_money_accounts", money_id)
            money[money_id] = m
            key = ("bank", f"{m['name']}{' ••' + str(m['last4']) if m is not None and m['last4'] else ''}"
                   if m is not None else "Bank")
        else:
            key = ("entry", str(r.get("source") or "entry"))
        acc = sums.setdefault(key, [0, 0])
        acc[0] += int(str(r["amount"]))
        acc[1] += 1
    out: list[Row] = [{"kind": k, "name": n, "amount": v[0], "count": v[1]} for (k, n), v in sums.items()]
    out.sort(key=lambda r: (-abs(int(str(r["amount"]))), str(r["name"])))
    return out


def line_detail(c: sqlite3.Connection, viewer: str, account_id: str, start: str | None = None,
                end: str | None = None, *, limit: int = 50, top_n: int = 10, at: str | None = None,
                vendor: str | None = None, teams: Iterable[str] = (), teammates: Iterable[str] = (),
                who: str = "both", unattributed: bool = False) -> Row:
    """One line's spend by vendor, team and teammate, and its entries. With `vendor` ("" for lines with none) every
    breakdown, the entries and how they were paid are that vendor's; `teams`, `teammates`, `who` and `unattributed`
    filter it as the P&L's expenses view does. `next_month` is the line's projection (within the org filter).

    Private totals come only from whole cohorts: a vendor filter is always narrow (so a payroll cell is never rebuilt
    from a caller-picked subset and recovered by subtraction), and an org filter is narrow as the P&L's is."""
    s = _open(c, viewer, at)
    a, z = _window(start, end, s.ctx)
    account = s.ctx.accounts.get(account_id)
    if account is None or not s.view.sees_account(account):
        raise FinanceError(NOT_FOUND, "no such account")
    f = _org_filter(s, teams, teammates, who, unattributed)
    ids = sorted(descendants(s.ctx.accounts, account_id))
    cells = _cells(s, a, z, ids)
    if vendor is not None:
        cells = [x for x in cells if x.vendor == vendor]
    if f.any:
        cells = [x for x in cells if f.keeps(x.team, x.teammate)]
    whole = _suppress(s, cells, vendor is not None or f.narrow(s.ctx.org), a, z)
    split = _suppress(s, cells, True, a, z)
    out: Row = {"account": {"id": account.id, "code": account.code, "name": account.name, "type": account.type},
                "start": a.isoformat(), "end": z.isoformat(), "view_version": s.view.version,
                "total": None if account_id in whole.hidden else sum(x.amount for x in whole.cells),
                "excluded": split.excluded, "hidden": _hidden_labels(s, split.hidden | whole.hidden),
                "vendor": vendor, "filter": {"teams": list(f.teams), "teammates": list(f.teammates), "who": f.who,
                                             "unattributed": f.unattributed},
                "next_month": _next_month(s, ids, f)}
    for by in ("vendor", "team", "teammate"):
        sums: dict[str, int] = {}
        for cell in split.cells:
            key = _key(s, cell, by) or ""
            if key == "" and by != "vendor" and not s.view.company_wide(account):
                continue
            sums[key] = sums.get(key, 0) + cell.amount
        out["by_" + by] = _cap([{"key": k or "unattributed", "label": _label(s, k, by), "amount": v}
                                for k, v in sums.items()], top_n)
    entries: list[Row] = []
    more = False
    private = any(i in s.private and i not in s.view.full_detail for i in ids)
    if not private:
        most = max(1, min(int(limit), 500))
        rows = SL.entries_on(c, s.ctx.book.id, ids, a.isoformat(), z.isoformat(), most * 4 + 1, vendor)
        tags = SL.line_tags(c, [r["line_id"] for r in rows])
        seen: list[Row] = []
        for r in rows:
            line_account = s.ctx.accounts[r["account_id"]]
            mark = tags.get(r["line_id"], {})
            if not s.view.sees(line_account, mark) or \
                    (f.any and not f.keeps(str(mark.get("team") or ""), str(mark.get("teammate") or ""))):
                continue
            seen.append({"amount": int(r["amount"]), "card_id": r["card_id"], "money_account_id": r["money_account_id"],
                         "source": r["source"]})
            if len(entries) == most:
                more = True
                continue
            entries.append({"line_id": r["line_id"], "entry_id": r["entry_id"], "date": r["entry_date"],
                            "account_id": r["account_id"], "amount": int(r["amount"]), "vendor": r["contact_id"],
                            "description": r["description"], "memo": r["memo"], "source": r["source"],
                            "tags": tags.get(r["line_id"], {})})
        more = more or len(rows) > most * 4
        out["paid_by"] = _paid_by(s, seen)
    else:
        out["paid_by"] = None
    out["entries"] = entries
    out["more"] = more
    return out


# ------------------------------------------------------------------------------------------------ budgets
def _private_scope(s: _Seen, b: B.Budget) -> bool:
    """A budget over private lines shows its amount but not its spend to a viewer without their detail."""
    return private_scope(s.ctx, s.view.viewer, b)


def _state_row(s: _Seen, b: B.Budget, st: B.State) -> Row:
    cut = _private_scope(s, b)
    return {"amount": st.amount, "actual": None if cut else st.actual, "held": None if cut else st.held,
            "remaining": None if cut else st.remaining, "pct": None if cut or st.pct is None else str(st.pct),
            "crossed_warn": None if cut else st.crossed_warn, "at_limit": None if cut else st.at_limit,
            "period_start": st.period_start, "period_end": st.period_end}


def _budget_row(b: B.Budget) -> Row:
    return {"id": b.id, "name": b.name, "period": b.period, "account_id": b.account_id or None,
            "catch_all": b.catch_all, "team": b.team, "teammate": b.teammate, "vendor": b.vendor,
            "parent_id": b.parent_id or None, "owner": b.owner, "warn_pct": b.warn_pct, "at_limit": b.at_limit,
            "rollover": b.rollover, "active": b.active}


def _visible(s: _Seen, include_inactive: bool) -> list[B.Budget]:
    return [b for b in s.ctx.budgets().values() if (b.active or include_inactive) and
            sees_budget(s.ctx, s.view.viewer, b)]


def budgets(c: sqlite3.Connection, viewer: str, *, include_inactive: bool = False, limit: int = 200,
            at: str | None = None, on: str | None = None) -> Row:
    """Every budget the viewer sees, with where it stands today, or in the period holding the day `on` (past or
    future): the amount of the version in effect then, and that period's spend and holds."""
    s = _open(c, viewer, at)
    day = dates.day(on) if on else dates.day(s.ctx.today)
    seen = _visible(s, include_inactive)
    most = max(1, min(int(limit), 500))
    rows = [{**_budget_row(b), "state": _state_row(s, b, budgeting.state(s.ctx, b.id, day))} for b in seen[:most]]
    return {"budgets": rows, "more": len(seen) > most, "on": day.isoformat(), "view_version": s.view.version}


def budget_vs_actual(c: sqlite3.Connection, viewer: str, start: str | None = None, end: str | None = None,
                     period: str = "month", *, limit: int = 200, at: str | None = None) -> Row:
    """Each visible budget's amount and actual spend per period bucket (a monthly budget prorated over days or
    weeks)."""
    s = _open(c, viewer, at)
    if period not in dates.BUCKETS:
        raise invalid(f"period is {'|'.join(dates.BUCKETS)}")
    a, z = _window(start, end, s.ctx)
    buckets = dates.buckets(period, a, z, s.ctx.book.fiscal_year_start)
    seen = _visible(s, False)
    most = max(1, min(int(limit), 500))
    versions = SB.versions(c, [b.id for b in seen[:most]])
    rows: list[Row] = []
    for b in seen[:most]:
        cut = _private_scope(s, b)
        rows.append({**_budget_row(b),
                     "budget": [B.in_window(b, versions[b.id], q, s.ctx.book.fiscal_year_start) for q in buckets],
                     "actual": None if cut else [budgeting.actual(s.ctx, b, p.isoformat(), q.isoformat())
                                                 for p, q in buckets]})
    return {"start": a.isoformat(), "end": z.isoformat(), "period": period,
            "columns": [{"start": p.isoformat(), "end": q.isoformat(), "label": dates.label(p, period)}
                        for p, q in buckets],
            "budgets": rows, "more": len(seen) > most, "view_version": s.view.version}


METRICS = ("cash", "burn", "runway_months", "revenue", "spend", "budget_remaining", "budget_pct_used", "ap_due",
           "ar_overdue", "uncategorized_count", "unreconciled_days")
STALE_HOURS = 24            # bank data older than this marks a cash metric stale
RUNWAY_MONTHS = 3           # runway divides cash by the average burn of this many full months


def _data_as_of(s: _Seen, money_ids: Sequence[str]) -> tuple[str | None, bool]:
    """When the bank and card data these numbers rest on was last synced (the oldest of the connections behind the
    viewer's money accounts), and whether that is stale: older than a day, never synced, or a connection in error."""
    rows = [r for r in SK.money_accounts(s.ctx.c, s.ctx.book.id) if str(r["id"]) in set(money_ids)]
    conns = {str(r["connection_id"]) for r in rows if r["connection_id"]}
    found = [x for x in SK.connections(s.ctx.c) if str(x["id"]) in conns]
    if not found:
        return None, False
    synced = [str(x["last_sync_at"] or x["last_ok_at"] or "") for x in found]
    oldest = min(synced)
    if not oldest or any(str(x["status"]) == "error" for x in found):
        return oldest or None, True
    age = dates.day(s.ctx.today) - dates.day(oldest[:10])
    return oldest, age > timedelta(hours=STALE_HOURS)


def _cash(s: _Seen) -> tuple[int, list[Row], list[str]]:
    """Cash on the books in the money accounts that hold cash (bank, Stripe) the viewer sees, total and per account."""
    rows = [r for r in SK.money_accounts(s.ctx.c, s.ctx.book.id)
            if r["holds_cash"] and r["account_id"] and tab.sees_money_account(s.ctx, s.view, r)]
    sums = SK.balances(s.ctx.c, s.ctx.book.id, s.ctx.today, sorted({str(r["account_id"]) for r in rows}))
    per: list[Row] = [{"money_account_id": str(r["id"]), "name": str(r["name"]), "balance": sums.get(str(r["account_id"]), 0),
            "provider_balance": r["balance"], "balance_as_of": r["balance_as_of"]} for r in rows]
    by_gl = {str(r["account_id"]) for r in rows}
    return sum(sums.get(g, 0) for g in by_gl), per, [str(r["id"]) for r in rows]


def _net_burn(c: sqlite3.Connection, viewer: str, a: date, z: date, at: str | None) -> int:
    """Cash burn over a window: cash-basis expenses minus cash-basis income the viewer sees (financing, being on the
    balance sheet, never counts)."""
    found = pnl(c, viewer, a.isoformat(), z.isoformat(), "year", "cash", at)
    return sum(cast(list[int], found["expense"])) - sum(cast(list[int], found["income"]))


def _full_months(today: date, n: int) -> tuple[date, date]:
    first = date(today.year, today.month, 1)
    end = first - timedelta(days=1)
    y, m = first.year, first.month - n
    while m <= 0:
        y, m = y - 1, m + 12
    return date(y, m, 1), end


def metric(c: sqlite3.Connection, viewer: str, name: str, *, budget_id: str | None = None, start: str | None = None,
           end: str | None = None, account_id: str | None = None, team: str | None = None,
           teammate: str | None = None, days: int | None = None, at: str | None = None) -> Row:
    """One number for a card, a goal or a bot (§7.2), cut to the viewer like every read, with `as_of` (when it was
    worked out) and, for numbers resting on bank data, `data_as_of` and `stale`. cash: the books' balance of the cash
    accounts the viewer sees (per account in `accounts`); burn: cash out minus cash in this month (or the window);
    runway_months: cash over the average burn of the last 3 full months (null when nothing burns); revenue, spend: the
    window (this month by default); budget_remaining / budget_pct_used: one budget; ap_due: open bills due within
    `days` (default 7, overdue included); ar_overdue: invoices at least `days` (default 1) late;
    uncategorized_count: settled rows waiting for a category (last 90 days); unreconciled_days: days since the
    oldest reconciliation of the money accounts the viewer sees."""
    if name not in METRICS:
        raise invalid(f"a metric is {'|'.join(METRICS)}")
    s = _open(c, viewer, at)
    currency = s.ctx.book.base_currency
    base: Row = {"name": name, "as_of": s.ctx.at, "view_version": s.view.version}
    today = dates.day(s.ctx.today)
    if name in ("budget_remaining", "budget_pct_used"):
        b = s.ctx.budgets().get(budget_id or "")
        if b is None or not sees_budget(s.ctx, viewer, b):
            raise FinanceError(NOT_FOUND, "no such budget")
        row = _state_row(s, b, budgeting.state(s.ctx, b.id, today))
        if name == "budget_remaining":
            return {**base, "value": row["remaining"], "unit": "minor", "currency": currency}
        return {**base, "value": row["pct"], "unit": "percent"}
    if name in ("cash", "runway_months"):
        if not (s.view.everything or s.view.balance_sheet or s.view.bs_lines):
            raise denied("cash is on the balance sheet, the owner's unless granted")
        total, per, money_ids = _cash(s)
        data_as_of, stale = _data_as_of(s, money_ids)
        base.update({"data_as_of": data_as_of, "stale": stale})
        if name == "cash":
            return {**base, "value": total, "unit": "minor", "currency": currency, "accounts": per}
        a, z = _full_months(today, RUNWAY_MONTHS)
        burn = _net_burn(c, viewer, a, z, at)
        monthly = Decimal(burn) / RUNWAY_MONTHS
        value = None if monthly <= 0 else str((Decimal(total) / monthly).quantize(Decimal("0.1")))
        return {**base, "value": value, "unit": "months", "cash": total, "monthly_burn": int(monthly),
                "start": a.isoformat(), "end": z.isoformat()}
    if name in ("ap_due", "ar_overdue"):
        kind = "bill" if name == "ap_due" else "invoice"
        if not tab_ap.sees_side(s.ctx, s.view, kind):
            raise denied(f"{'payables' if kind == 'bill' else 'receivables'} are not in your view")
        n = (7 if kind == "bill" else 1) if days is None else max(0, min(int(days), 3650))
        rows, _ = tab_ap.keep_docs(c, kind, SAP.open_docs(c, kind, s.ctx.book.id), set(tab.private_cut(s.ctx, s.view)))
        if kind == "bill":
            picked = [r for r in rows if r["due_date"] and -AP.days_late(r["due_date"], today) <= n]
        else:
            picked = [r for r in rows if AP.days_late(r["due_date"], today) >= max(n, 1)]
        return {**base, "value": sum(tab_ap.remaining(kind, r) for r in picked), "count": len(picked), "days": n,
                "unit": "minor", "currency": currency}
    if name == "uncategorized_count":
        rows, more = _uncategorized(s, today - timedelta(days=90), today, tab.SCAN)
        money_ids, _ = tab.feed_scope(s.ctx, s.view)
        data_as_of, stale = _data_as_of(s, money_ids)
        return {**base, "value": len(rows), "more": more, "unit": "count", "data_as_of": data_as_of, "stale": stale}
    if name == "unreconciled_days":
        money_ids, _ = tab.feed_scope(s.ctx, s.view)
        worst: int | None = None
        for m in money_ids:
            through = SK.locked_through(c, m)
            first = SK.first_feed_day(c, m)
            if not first:
                continue
            gap = max(0, (today - dates.day(through)).days) if through else (today - dates.day(first)).days
            worst = gap if worst is None else max(worst, gap)
        data_as_of, stale = _data_as_of(s, money_ids)
        return {**base, "value": worst or 0, "unit": "days", "data_as_of": data_as_of, "stale": stale}
    a, z = _window(start, end, s.ctx)
    if name == "burn":
        return {**base, "value": _net_burn(c, viewer, a, z, at), "unit": "minor", "currency": currency,
                "start": a.isoformat(), "end": z.isoformat()}
    if name == "revenue":
        found = pnl(c, viewer, a.isoformat(), z.isoformat(), "year", "accrual", at)
        return {**base, "value": sum(cast(list[int], found["income"])), "unit": "minor", "currency": currency,
                "start": a.isoformat(), "end": z.isoformat(), "excluded": found["excluded"]}
    found = _grouped(s, a, z, "account", account_id, team, teammate, MAX_TOP)
    return {**base, "value": found["total"], "unit": "minor", "currency": currency, "start": a.isoformat(),
            "end": z.isoformat(), "excluded": found["excluded"]}


def _uncategorized(s: _Seen, a: date, z: date, most: int) -> tuple[list[Any], bool]:
    """Settled rows the viewer sees still waiting for a category, newest first, and whether there are more."""
    money_ids, card_ids = tab.feed_scope(s.ctx, s.view)
    other = [x.id for x in s.ctx.accounts.values() if x.code == tab.OTHER_CODE]
    rows = SAP.uncategorized(s.ctx.c, money_ids, card_ids, other, a.isoformat(), z.isoformat(), most + 1)
    cut = tab.private_feed(s.ctx, s.view, rows)
    kept = [r for r in rows[:most] if str(r["id"]) not in cut]
    return kept, len(rows) > most


# ------------------------------------------------------------------------------------------------ reports
REPORTS = ("pnl", "balance_sheet", "cash_flow", "budget_vs_actual", "ap_aging", "ar_aging", "spend_by")


def _prior(a: date, z: date) -> tuple[date, date]:
    span = (z - a).days + 1
    return a - timedelta(days=span), a - timedelta(days=1)


def _aging_report(c: sqlite3.Connection, viewer: str, kind: str, top_n: int, at: str | None) -> Row:
    ctx = load(c, at)
    view = ctx.view(viewer)
    if not tab_ap.sees_side(ctx, view, kind):
        raise denied(f"{'payables' if kind == 'bill' else 'receivables'} are not in your view")
    rows, _ = tab_ap.keep_docs(c, kind, SAP.open_docs(c, kind, ctx.book.id), set(tab.private_cut(ctx, view)))
    today = dates.day(ctx.today)
    by: dict[str, Row] = {}
    totals: dict[str, int] = dict.fromkeys(AP.AGING, 0)
    for r in rows:
        left = tab_ap.remaining(kind, r)
        bucket = AP.aging(r["due_date"], today)
        key = str(r[f"{tab_ap.CONTACT_OF[kind]}_id"] or "")
        row = by.setdefault(key, {"key": key, "label": str(r["contact_name"] or "No contact"),
                                  **dict.fromkeys(AP.AGING, 0), "total": 0})
        row[bucket] = cast(int, row[bucket]) + left
        row["total"] = cast(int, row["total"]) + left
        totals[bucket] += left
    ranked = sorted(by.values(), key=lambda x: -cast(int, x["total"]))
    shown = ranked[:top_n]
    if len(ranked) > top_n:
        rest = ranked[top_n:]
        shown.append({"key": "other", "label": f"Other · {len(rest)}",
                      **{b: sum(cast(int, x[b]) for x in rest) for b in AP.AGING},
                      "total": sum(cast(int, x["total"]) for x in rest)})
    return {"as_of": today.isoformat(), "buckets": list(AP.AGING), "rows": shown, "totals": totals,
            "total": sum(totals.values()), "currency": ctx.book.base_currency, "view_version": view.version}


def _cash_flow(c: sqlite3.Connection, viewer: str, a: date, z: date, period: str, at: str | None) -> Row:
    """Cash at the start, its net change per period and cash at the end, over the cash accounts the viewer sees."""
    s = _open(c, viewer, at)
    if not (s.view.everything or s.view.balance_sheet or s.view.bs_lines):
        raise denied("cash flow is on the balance sheet, the owner's unless granted")
    if period not in dates.BUCKETS:
        raise invalid(f"period is {'|'.join(dates.BUCKETS)}")
    rows = [r for r in SK.money_accounts(c, s.ctx.book.id)
            if r["holds_cash"] and r["account_id"] and tab.sees_money_account(s.ctx, s.view, r)]
    gls = sorted({str(r["account_id"]) for r in rows})
    buckets = dates.buckets(period, a, z, s.ctx.book.fiscal_year_start)
    opening = sum(SK.balances(c, s.ctx.book.id, (a - timedelta(days=1)).isoformat(), gls).values())
    net = [0] * len(buckets)
    for r in SL.rollup_rows(c, s.ctx.book.id, a.isoformat(), z.isoformat(), gls):
        n = _bucket_index(buckets, str(r["day"]))
        if n >= 0:
            net[n] += int(r["amount"])
    closing: list[int] = []
    run = opening
    for x in net:
        run += x
        closing.append(run)
    return {"start": a.isoformat(), "end": z.isoformat(), "period": period, "opening": opening, "net": net,
            "closing": closing, "columns": [{"start": p.isoformat(), "end": q.isoformat(), "label": dates.label(p, period)}
                                            for p, q in buckets],
            "accounts": [{"money_account_id": str(r["id"]), "name": str(r["name"])} for r in rows],
            "currency": s.ctx.book.base_currency, "view_version": s.view.version}


def report(c: sqlite3.Connection, viewer: str, kind: str, *, start: str | None = None, end: str | None = None,
           period: str = "month", basis: str | None = None, compare: str | None = None, top_n: int = 20,
           by: str = "vendor", at: str | None = None) -> Row:
    """One report (§7.1): pnl (accrual or cash), balance_sheet (as of `end`), cash_flow, budget_vs_actual, ap_aging,
    ar_aging or spend_by (`by` department|team|teammate|bot|person|vendor|account). `compare=prior` adds the window
    before it. Payloads are capped: `top_n` rows and one "Other · n"."""
    if kind not in REPORTS:
        raise invalid(f"a report is {'|'.join(REPORTS)}")
    if compare not in (None, "", "prior"):
        raise invalid("compare is prior")
    top = max(1, min(int(top_n), MAX_TOP))
    if kind == "balance_sheet":
        return {"kind": kind, **tab.balance_sheet(c, viewer, as_of=end, at=at)}
    if kind in ("ap_aging", "ar_aging"):
        return {"kind": kind, **_aging_report(c, viewer, "bill" if kind == "ap_aging" else "invoice", top, at)}
    ctx = load(c, at)
    a, z = _window(start, end, ctx)

    def one(p: date, q: date) -> Row:
        if kind == "pnl":
            return _fold_pnl(pnl(c, viewer, p.isoformat(), q.isoformat(), period, basis or ctx.book.basis, at), top)
        if kind == "cash_flow":
            return _cash_flow(c, viewer, p, q, period, at)
        if kind == "budget_vs_actual":
            return budget_vs_actual(c, viewer, p.isoformat(), q.isoformat(), period, limit=top, at=at)
        return spend_by(c, viewer, by, p.isoformat(), q.isoformat(), top_n=top, at=at)
    out: Row = {"kind": kind, **one(a, z)}
    if compare == "prior":
        p, q = _prior(a, z)
        out["prior"] = one(p, q)
    return out


# ------------------------------------------------------------------------------------------------ search
SEARCH_KINDS = ("entry", "feed", "bill", "invoice", "contact", "rule")
SEARCH_EACH = 10            # results per kind when no kind is named


def _filter(filters: Mapping[str, object], key: str) -> str | None:
    value = filters.get(key)
    if value is None or value == "":
        return None
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise invalid(f"filter {key} is text")
    return str(value)


def _search_rules(ctx: Ctx, viewer: str, q: str, filters: Mapping[str, object], most: int,
                  after: str) -> tuple[list[Row], str | None]:
    if not R.has(ctx.org, viewer, R.RULES, ctx.rights(viewer)):
        return [], None
    rule_kind = _filter(filters, "kind")
    rows = [r for r in (SK.rules(ctx.c, rule_kind, active_only=False) if rule_kind else
                        [*SK.rules(ctx.c, "gate", active_only=False), *SK.rules(ctx.c, "posting", active_only=False)])
            if (not q or q.lower() in f"{r['name']} {r['words']}".lower()) and str(r["id"]) > after]
    rows.sort(key=lambda r: str(r["id"]))
    page = rows[:most]
    return [{"kind": "rule", "id": str(r["id"]), "label": str(r["name"]), "rule_kind": str(r["kind"]),
             "words": str(r["words"]), "active": bool(r["active"]),
             "priority": int(r["priority"]) if str(r["kind"]) == "posting" else None} for r in page], \
        (str(page[-1]["id"]) if len(rows) > most else None)


def search(c: sqlite3.Connection, viewer: str, *, kind: str | None, q: str = "",
           filters: Mapping[str, object] | None = None, limit: int = 50, cursor: str | None = None,
           at: str | None = None) -> Row:
    """Search entries' lines, feed rows, bills, invoices, contacts and rules (§7.1) the viewer may see. With a `kind`
    it pages (`next_cursor`); without one it returns the first few of each kind it can show. Filters per kind:
    entry/feed: account_id, money_account_id, status (feed), start, end; bill/invoice: status, contact_id, start, end,
    aging; contact: kind, active; rule: kind (gate|posting)."""
    if kind is not None and kind not in SEARCH_KINDS:
        raise invalid(f"kind is {'|'.join(SEARCH_KINDS)}")
    if cursor and kind is None:
        raise invalid("a cursor pages one kind; name it")
    f = filters or {}
    text = (q or "").strip()[:100]
    most = tab.page_size(limit) if kind else min(tab.page_size(limit), SEARCH_EACH)
    ctx = load(c, at)
    view = ctx.view(viewer)
    results: list[Row] = []
    nxt: str | None = None
    for k in [kind] if kind else SEARCH_KINDS:
        try:
            if k in ("entry", "feed"):
                found = tab.transactions(c, viewer, source=k, q=text, cursor=cursor, limit=most, at=at,
                                         money_account_id=_filter(f, "money_account_id"), status=_filter(f, "status"),
                                         account_id=_filter(f, "account_id"), start=_filter(f, "start"),
                                         end=_filter(f, "end"))
                rows = cast(list[Row], found["rows"])
                results += [{**r, "label": str(r.get("counterparty") or r.get("description") or r.get("account") or "")}
                            for r in rows]
                nxt = cast(str | None, found["next"])
            elif k in ("bill", "invoice"):
                if not tab_ap.sees_side(ctx, view, k):
                    continue
                found = tab_ap.documents(c, viewer, k, status=_filter(f, "status"),
                                         contact_id=_filter(f, "contact_id"), q=text, start=_filter(f, "start"),
                                         end=_filter(f, "end"), aging=_filter(f, "aging"), cursor=cursor, limit=most,
                                         at=at)
                for r in cast(list[Row], found[f"{k}s"]):
                    who = cast(Mapping[str, object], r[tab_ap.CONTACT_OF[k]])
                    results.append({**r, "label": f"{who.get('name') or ''} {r['number']}".strip()})
                nxt = cast(str | None, found["next_cursor"])
            elif k == "contact":
                active = _filter(f, "active")
                found = tab_ap.contacts(c, viewer, kind=_filter(f, "kind"), q=text, cursor=cursor, limit=most, at=at,
                                        active=None if active is None else active in ("1", "true", "True"))
                results += [{**r, "kind": "contact", "contact_kind": r["kind"], "label": r["name"]}
                            for r in cast(list[Row], found["contacts"])]
                nxt = cast(str | None, found["next_cursor"])
            else:
                rows, after = _search_rules(ctx, viewer, text, f, most, (tab.decode_cursor(cursor) or [""])[0])
                results += rows
                nxt = tab.encode_cursor([after]) if after else None
        except FinanceError as e:
            if kind:        # named: say why; across kinds: show what the viewer may see
                raise
            del e
    return {"results": results, "next_cursor": nxt if kind else None, "currency": ctx.book.base_currency,
            "view_version": view.version}


# ------------------------------------------------------------------------------------------------ inbox
INBOX_KINDS = ("uncategorized", "bill_to_enter", "unmatched_receipt", "missing_receipt", "bill_due",
               "invoice_overdue", "budget_near_limit", "sync_failed", "period_ready")
INBOX_EACH = 200            # items one kind contributes before its count says "more"
RECEIPT_DAYS = 90


def _money(amount: int) -> str:
    return f"{abs(amount) / 100:,.2f}"


def inbox(c: sqlite3.Connection, viewer: str, *, limit: int = 50, cursor: str | None = None,
          at: str | None = None) -> Row:
    """The viewer's Finance work queue (§7.1): uncategorized rows, bills to enter (drafts, e.g. from the bills
    address), unmatched and missing receipts, bills due within a week, overdue invoices, budgets near their limit,
    failed syncs and months ready to close. Each item has its ids and a short label; `counts` covers every kind
    (`more` when a kind has over 200)."""
    s = _open(c, viewer, at)
    ctx, view = s.ctx, s.view
    today = dates.day(ctx.today)
    held = ctx.rights(viewer)
    items: list[Row] = []
    counts: dict[str, int] = {}
    more: list[str] = []

    def add(kind: str, rows: list[Row], over: bool = False) -> None:
        counts[kind] = len(rows)
        if over:
            more.append(kind)
        items.extend({"kind": kind, **r} for r in rows)

    rows, over = _uncategorized(s, today - timedelta(days=RECEIPT_DAYS), today, INBOX_EACH)
    add("uncategorized", [{"id": str(r["id"]), "feed_id": str(r["id"]), "date": str(r["txn_date"]),
                           "amount": int(r["amount"]), "entry_id": r["entry_id"], "status": str(r["status"]),
                           "label": f"{r['counterparty'] or r['description'] or 'Row'} · {_money(int(r['amount']))}"}
                          for r in rows], over)
    cut = set(tab.private_cut(ctx, view))
    if tab_ap.sees_side(ctx, view, "bill"):
        drafts, _ = tab_ap.keep_docs(c, "bill", SAP.drafts(c, ctx.book.id, INBOX_EACH + 1), cut)
        add("bill_to_enter", [{"id": str(r["id"]), "bill_id": str(r["id"]), "date": str(r["bill_date"]),
                               "amount": int(r["total"]), "origin": str(r["origin"]),
                               "label": f"{r['contact_name'] or 'Bill'} {r['number']} · {_money(int(r['total']))}"
                               .replace("  ", " ")} for r in drafts[:INBOX_EACH]], len(drafts) > INBOX_EACH)
    if R.has(ctx.org, viewer, R.BOOK, held):
        # A receipt with no subject yet is the company's to see only with the whole books; else the viewer's own.
        files = SAP.unmatched_files(c, ctx.book.id, INBOX_EACH + 1, "" if view.everything else viewer)
        add("unmatched_receipt", [{"id": str(r["id"]), "file_id": str(r["file_id"]), "date": str(r["created_at"])[:10],
                                   "label": "Receipt with nothing to attach to"} for r in files[:INBOX_EACH]],
            len(files) > INBOX_EACH)
    money_ids, card_ids = tab.feed_scope(ctx, view)
    card_money = [i for i in money_ids if i in {str(m["id"]) for m in SK.money_accounts(c, ctx.book.id)
                                                if str(m["kind"]) == "card_program"}]
    since = (today - timedelta(days=RECEIPT_DAYS)).isoformat()
    bare = SAP.card_rows_without_receipt(c, card_money, card_ids, since, INBOX_EACH + 1)
    gone = tab.private_feed(ctx, view, bare)
    bare = [r for r in bare if str(r["id"]) not in gone]
    add("missing_receipt", [{"id": str(r["id"]), "feed_id": str(r["id"]), "date": str(r["txn_date"]),
                             "amount": int(r["amount"]),
                             "label": f"{r['counterparty'] or 'Card charge'} · {_money(int(r['amount']))}"}
                            for r in bare[:INBOX_EACH]], len(bare) > INBOX_EACH)
    for kind, name in (("bill", "bill_due"), ("invoice", "invoice_overdue")):
        if not tab_ap.sees_side(ctx, view, kind):
            continue
        docs, _ = tab_ap.keep_docs(c, kind, SAP.open_docs(c, kind, ctx.book.id), cut)
        if kind == "bill":
            docs = [r for r in docs if r["due_date"] and AP.days_late(r["due_date"], today) >= -7]
        else:
            docs = [r for r in docs if AP.days_late(r["due_date"], today) > 0]
        add(name, [{"id": str(r["id"]), f"{kind}_id": str(r["id"]), "date": r["due_date"],
                    "amount": tab_ap.remaining(kind, r), "days_late": AP.days_late(r["due_date"], today),
                    "label": f"{r['contact_name'] or kind.title()} · {_money(tab_ap.remaining(kind, r))} "
                             f"due {r['due_date']}"} for r in docs[:INBOX_EACH]], len(docs) > INBOX_EACH)
    near: list[Row] = []
    for b in _visible(s, False):
        st = _state_row(s, b, budgeting.state(ctx, b.id, today))
        if st["crossed_warn"]:
            near.append({"id": b.id, "budget_id": b.id, "amount": st["remaining"], "pct": st["pct"],
                         "label": f"{b.name} · {st['pct'] or '?'}% used"})
    add("budget_near_limit", near[:INBOX_EACH], len(near) > INBOX_EACH)
    if view.everything or view.sources or R.is_admin(ctx.org, viewer, held):
        add("sync_failed", [{"id": str(x["id"]), "connection_id": str(x["id"]), "date": str(x["updated_at"] or "")[:10],
                             "label": f"{x['name'] or x['provider']}: {str(x['last_error'] or 'sync failed')[:120]}"}
                            for x in SK.connections(c) if str(x["status"]) == "error"])
    if R.has(ctx.org, viewer, R.BOOK, held):
        ready = [p for p in _closable(c, ctx) if _checklist(s, p)["ready"]]
        add("period_ready", [{"id": p, "month": p, "label": f"{p} is ready to close"} for p in ready])
    offset = int((tab.decode_cursor(cursor) or ["0"])[0] or 0)
    most = tab.page_size(limit)
    page = items[offset:offset + most]
    nxt = tab.encode_cursor([str(offset + most)]) if offset + most < len(items) else None
    return {"items": page, "counts": counts, "more": more, "next_cursor": nxt, "currency": ctx.book.base_currency,
            "view_version": view.version}


# ------------------------------------------------------------------------------------------------ close
def _closable(c: sqlite3.Connection, ctx: Ctx) -> list[str]:
    """Open months that have ended, oldest first (at most a year back)."""
    today = dates.day(ctx.today)
    out: list[str] = []
    for r in SL.open_periods_before(c, ctx.book.id, today.isoformat(), 12):
        out.append(str(r["start_date"])[:7])
    return out


def _checklist(s: _Seen, month: str) -> Row:
    """What stands between a month and its close, for whoever does the books."""
    ctx = s.ctx
    first = dates.month(month)
    last = dates.month_end(first)
    period = SL.period(ctx.c, ctx.book.id, first.isoformat())
    status = str(period["status"]) if period is not None else "open"
    money = [m for m in SK.money_accounts(ctx.c, ctx.book.id)
             if str(m["status"]) == "active" and tab.sees_money_account(ctx, s.view, m)]
    live = [m for m in money if (SK.first_feed_day(ctx.c, str(m["id"])) or "9999") <= last.isoformat()]
    behind = [{"money_account_id": str(m["id"]), "name": str(m["name"]),
               "locked_through": SK.locked_through(ctx.c, str(m["id"])) or None} for m in live
              if (SK.locked_through(ctx.c, str(m["id"])) or "") < last.isoformat()]
    rows, more = _uncategorized(s, first, last, INBOX_EACH)
    accrual = ctx.book.basis == "accrual"
    checks: list[Row] = [
        {"key": "reconciled", "ok": not behind, "count": len(behind), "accounts": behind,
         "label": "Every money account reconciled through month end"},
        {"key": "uncategorized", "ok": not rows, "count": len(rows), "more": more,
         "label": "Nothing left in Other expenses or uncategorized"}]
    # Company-wide counts only for whoever sees them: unmatched receipts with the whole books, draft bills with
    # payables (and only the drafts the viewer sees whole).
    if s.view.everything:
        receipts = SAP.unmatched_files(ctx.c, ctx.book.id, INBOX_EACH + 1)
        checks.append({"key": "unmatched_receipts", "ok": not receipts, "count": min(len(receipts), INBOX_EACH),
                       "label": "Receipts matched"})
    if tab_ap.sees_side(ctx, s.view, "bill"):
        dated = [r for r in SAP.drafts(ctx.c, ctx.book.id, 5000) if str(r["bill_date"]) <= last.isoformat()]
        kept, _ = tab_ap.keep_docs(ctx.c, "bill", dated, set(tab.private_cut(ctx, s.view)))
        checks.append({"key": "draft_bills", "ok": not kept, "count": len(kept),
                       "label": "Bills dated this month entered"})
    notes: list[Row] = [
        {"key": "accruals", "ok": True, "label": "Review accruals and prepaids" if accrual else
         "Cash basis: no accruals to review", "note": True},
        {"key": "budget_vs_actual", "ok": True, "label": "Write the budget vs actual note", "note": True}]
    checks += notes
    # A month no money account had a feed for has nothing to reconcile, so it is never "ready" on its own.
    ready = status == "open" and last < dates.day(ctx.today) and bool(live) and not behind and not rows
    return {"month": month, "start": first.isoformat(), "end": last.isoformat(), "status": status, "ready": ready,
            "checks": checks}


def close_checklist(c: sqlite3.Connection, viewer: str, period: str, at: str | None = None) -> Row:
    """A month's close checklist (§10): reconciled through month end, nothing uncategorized, receipts matched, draft
    bills entered, and notes for accruals and budget vs actual. `ready` is what finance.period.ready fires on (the
    first two, on an open month that has ended). For teammates with finance.book, cut to their view: only the money
    accounts they see, and the receipt and draft-bill checks only when they see those."""
    s = _open(c, viewer, at)
    if not R.has(s.ctx.org, viewer, R.BOOK, s.ctx.rights(viewer)):
        raise denied("the close is for teammates who do the books")
    return {**_checklist(s, dates.month(period).isoformat()[:7]), "view_version": s.view.version}


def ready_months(c: sqlite3.Connection, at: str | None = None) -> list[str]:
    """The ended open months whose close checklist is ready, read as the owner (system work: finance.period.ready)."""
    ctx = load(c, at)
    if not ctx.org.owner:
        return []
    s = _Seen(ctx=ctx, view=ctx.view(ctx.org.owner), private=SA.private_lines(c))
    return [p for p in _closable(c, ctx) if _checklist(s, p)["ready"]]


def company_cash(c: sqlite3.Connection, at: str | None = None) -> int | None:
    """The books' cash across every cash account (system work: finance.cash.below); None with no cash account."""
    ctx = load(c, at)
    if not ctx.org.owner:
        return None
    total, per, _ = _cash(_Seen(ctx=ctx, view=ctx.view(ctx.org.owner), private=SA.private_lines(c)))
    return total if per else None


def transactions(c: sqlite3.Connection, viewer: str, *, teams: Iterable[str] = (), teammates: Iterable[str] = (),
                 who: str = "both", unattributed: bool = False, at: str | None = None, **kw: Any) -> Row:
    """finance/tab.transactions, cut by the expenses view's org filter as the P&L and a line's detail are: a line
    shows when its team and teammate pass, a feed row when a line of its entry it shows does (one with no entry has
    no team or teammate). A private line without full detail never shows a row at all, so no private total is built
    from the filtered set."""
    s = _open(c, viewer, at)
    f = _org_filter(s, teams, teammates, who, unattributed)
    return tab.transactions(c, viewer, at=at, org=f.keeps if f.any else None, **kw)


def view_version(c: sqlite3.Connection) -> int:
    """The counter a cache of any read keys on: it moves on every grant, revoke and privacy change."""
    return SA.view_version(c)



# The Finance tab's other reads (finance/tab.py).
balance_sheet = tab.balance_sheet
cards = tab.cards
sources = tab.sources
access = tab.access
view_as = tab.view_as
# Money out and controls (finance/tab_money.py).
payments = tab_money.payments
recipients = tab_money.recipients
reconciliations = tab_money.reconciliations
statements = tab_money.statements
rules = tab_money.rules
may_connect = tab_money.may_connect
provider_users = tab_money.provider_users
# Payables and receivables (finance/tab_ap.py).
bills = tab_ap.bills
invoices = tab_ap.invoices
contacts = tab_ap.contacts
bill = tab_ap.bill
invoice = tab_ap.invoice
chart = tab_ap.chart
pay_from = tab_ap.pay_from
feed_expected = tab_ap.feed_expected
bills_email = tab_ap.bills_email
# Finance's webhook triggers (finance/tab_hooks.py).
hook_admin = tab_hooks.hook_admin
webhook_deliveries = tab_hooks.webhook_deliveries


# What usage limits and the server read (part B).
Cutoff = limits.Cutoff
Cutoffs = limits.Cutoffs
cutoffs = limits.cutoffs
video_cutoff = limits.video_cutoff
usage_reconcile = usage.reconcile
is_set_up = _context.is_set_up


def files_in_books(c: sqlite3.Connection, file_ids: Sequence[str]) -> set[str]:
    """Which of these Library files Finance holds: attached to a bill, invoice, row or entry, or kept as an unmatched
    receipt. Keeper code uses it to close intake work whose file has since been filed."""
    return SAP.held_files(c, [str(f) for f in file_ids if f][:500])


def events_waiting(c: sqlite3.Connection) -> bool:
    """Whether a `finance.*` event waits to be handed to rules and automations."""
    return bool(SU.undrained(c, 1))


def _covers(ctx: Ctx, viewer: str, payload: Mapping[str, object]) -> bool | None:
    """Whether the viewer's current view covers every budget and line an action's payload names, with the numbers, and
    every budget above each budget it names (a refusal can quote any of them); None when it names none."""
    seen: list[bool] = []
    budgets = ctx.budgets()
    for key in ("budget_id", "parent_id"):
        bid = payload.get(key)
        if isinstance(bid, str) and bid:
            path = B.chain(budgets, bid) if bid in budgets else []   # a reason may name any budget above it
            seen.append(bool(path) and all(shows_numbers(ctx, viewer, b) for b in path))
    scopes: list[Mapping[str, object]] = [payload] if isinstance(payload.get("account_id"), str) else []
    raw = payload.get("lines")
    for item in cast(list[object], raw) if isinstance(raw, list) else []:
        if isinstance(item, Mapping):
            scopes.append(cast(Mapping[str, object], item))
    for one in scopes:
        tags = one.get("tags")
        tagged = cast(Mapping[str, object], tags) if isinstance(tags, Mapping) else one
        seen.append(sees_scope(ctx, viewer, str(one.get("account_id") or ""), str(tagged.get("team") or ""),
                               str(tagged.get("teammate") or "")))
    return all(seen) if seen else None


def action_for(c: sqlite3.Connection, viewer: str, action_id: str) -> dict[str, object] | None:
    """An action's status for `viewer`, read now: None unless they asked for it, are the one to decide its approval, or
    their current view covers what it is about. Its reason (which may carry amounts) for the approver, and otherwise
    only while the viewer's view covers that; else its code alone."""
    row = SX.by_id(c, action_id)
    if row is None:
        return None
    ctx = load(c)
    try:
        payload = cast(Mapping[str, object], json.loads(str(row["payload_json"] or "{}")))
    except ValueError:
        payload = dict[str, object]()
    covers = True if ctx.view(viewer).everything else _covers(ctx, viewer, payload)
    asked = viewer in {str(row["actor"]), str(row["on_behalf_of"] or "")}
    approver = bool(row["approval_to"]) and viewer == str(row["approval_to"])     # who decides it, not who asked
    if not asked and not covers and not approver:
        return None
    shown = covers is True or approver or (covers is None and asked)
    return {"action_id": str(row["id"]), "kind": str(row["kind"]), "status": str(row["status"]),
            "code": str(row["code"] or ""), "reason": str(row["reason"] or "") if shown else "",
            "approval_id": str(row["approval_id"] or "") or None, "approver": str(row["approval_to"] or "") or None,
            "rule": str(row["rule_id"] or "")}
