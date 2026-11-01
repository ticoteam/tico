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

from ..records import core as RC
from ..records import due_dates as RD
from ..records import reads as RR
from ..records import store as RS
from . import budgeting, limits, tab, tab_ap, tab_hooks, tab_money, usage
from . import due_dates as DD
from .tab_purchasing import estimate as estimate, estimates as estimates, order as order, orders as orders
from . import closing as CL
from . import context as _context
from .store import usage as SU
from .context import Ctx, load
from .core import ap as AP
from .core import budgets as B
from .core import rights as R
from .core import dates, money
from .core import projections as PJ
from .core import view as VW
from .core import watch as W
from .core.errors import FinanceError, NOT_FOUND, denied, invalid
from .core.ledger import PNL_TYPES, Account, descendants, pnl_section
from .tax import form_1099_csv as form_1099_csv
from .tax import form_1099_report as form_1099_report
from .tax import sales_tax_csv as sales_tax_csv
from .tax import sales_tax_report as sales_tax_report
from .handlers import named_reason, private_scope, sees_budget, sees_scope, shows_numbers
from .store import actions as SX
from .store import ap as SAP
from .store import access as SA
from .store import banking as SK
from .store import budgets as SB
from .store import ledger as SL
from .store import ap as SAP
from .store import catalog as SC
from .store import mirror as SM
from .store import raw_totals as SR
from .store import tico as ST

UNATTRIBUTED = "Unattributed"
# Reserved row keys. Every real key is stripped (V.opt_text, L.name_key, SAP.vendor_key), so none starts with a space:
# a vendor named Other or Unattributed is its own row, never one of these.
OTHER = " other"                  # the folded "Other · n" row
NONE = " unattributed"            # the group with no vendor, team or teammate (No vendor / Unattributed)
NO_TEAM = "no_team"                # a teammate's spend with no team on it: the No team group, not Unattributed
LOCATED = ("location", "class", "project", "location_class", "location_project", "class_project")
GROUPS = ("department", "team", "teammate", "bot", "person", "vendor", "account", *LOCATED)
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
    wide_start, _, wide_end = dates.quarters_around(a, z, fy, budgeting.horizon(s.ctx))
    wide = (wide_start, wide_end)
    out: list[VW.Cell] = []
    vendors: dict[str, str] = {}                     # a name a contact took counts as that contact (fin_vendor_aliases)
    for ids_, (p, q) in ((plain, (a, z)), (hidden, wide)):
        rows = SL.rollup_rows(s.ctx.c, s.ctx.book.id, p.isoformat(), q.isoformat(), ids_)
        if basis == "cash":
            rows = [*rows, *SAP.cash_rows(s.ctx.c, s.ctx.book.id, p.isoformat(), q.isoformat(), ids_)]
        for r in rows:
            account = s.ctx.accounts.get(r["account_id"])
            if account is None or not s.view.sees(account, {"team": r["team"], "teammate": r["teammate"]}):
                continue
            location, klass, project = _located(str(r["tagset"]))
            out.append(VW.Cell(account_id=r["account_id"], day=r["day"], team=r["team"], teammate=r["teammate"],
                               vendor=_vendor(s, vendors, str(r["vendor"])), amount=int(r["amount"]),
                               location=location, class_id=klass, project=project))
    return _without_settled(s, vendors, out, tab.settled_lines(s.ctx, s.view, a, z, plain, basis))


_Key = tuple[str, str, str, str, str, str, str, str]


def _located(tagset: str) -> tuple[str, str, str]:
    """A rollup row's location, class and project values, read from its tag set (core/ledger.tagset writes
    kind=value)."""
    got = dict(part.split("=", 1) for part in tagset.split("|") if "=" in part)
    return got.get("location", ""), got.get("class", ""), got.get("project", "")


def _cell_key(cell: VW.Cell) -> _Key:
    return (cell.account_id, cell.day, cell.team, cell.teammate, cell.vendor, cell.location, cell.class_id,
    cell.project)


def _without_settled(s: _Seen, vendors: dict[str, str], cells: list[VW.Cell], lines: list[Any]) -> list[VW.Cell]:
    """Takes the lines of settled private payroll entries out of the cells they sit in, before any total or breakdown is
    built, so their amounts reach no read: not the total, a by-vendor row, the P&L or a spend report. A cell that nets
    to nothing is dropped by suppression, and the cells marked linked make the totals say "excl. payroll"."""
    if not lines:
        return cells
    tags = SL.line_tags(s.ctx.c, [str(r["id"]) for r in lines])
    removed: dict[_Key, int] = {}
    for r in lines:
        account = s.ctx.accounts.get(r["account_id"])
        mine = tags.get(str(r["id"]), {})
        team, teammate = mine.get("team", ""), mine.get("teammate", "")
        if account is None or not s.view.sees(account, {"team": team, "teammate": teammate}):
            continue
        key: _Key = (str(r["account_id"]), str(r["entry_date"]), team, teammate,
                     _vendor(s, vendors, str(r["contact_id"] or "")), mine.get("location", ""),
                     mine.get("class", ""), mine.get("project", ""))
        removed[key] = removed.get(key, 0) + int(r["amount"])
    if not removed:
        return cells
    kept: list[VW.Cell] = []
    left: dict[_Key, int] = {}
    for cell in cells:
        key = _cell_key(cell)
        if key in removed:
            left[key] = left.get(key, 0) + cell.amount
        else:
            kept.append(cell)
    for key, amount in removed.items():
        account_id, day, team, teammate, vendor, location, klass, project = key
        kept.append(VW.Cell(account_id=account_id, day=day, team=team, teammate=teammate, vendor=vendor,
                            amount=left.get(key, 0) - amount, linked=True, location=location, class_id=klass,
                            project=project))
    return kept


def _vendor(s: _Seen, seen: dict[str, str], key: str) -> str:
    if key not in seen:
        seen[key] = SAP.vendor_resolved(s.ctx.c, s.ctx.book.id, key)
    return seen[key]


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
    return rows[:top_n] + [{"key": OTHER, "label": f"Other · {len(rest)}", "amount":
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
        shown.append({"key": OTHER, "label": f"Other · {len(rest)}", "type": type_, "account_id": None,
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
    s, snap = _closed(s, viewer, a, z)
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
    methods: dict[str, set[str]] = {}
    if buckets:
        stored: dict[tuple[str, str], list[tuple[str, str]]] = {}
        for r in SB.projections(c, s.ctx.book.id, buckets[0][0].isoformat()[:7], buckets[-1][1].isoformat()[:7]):
            account = s.ctx.accounts.get(r["account_id"])
            if account is None or account.id not in actual or \
                    (account.id in s.private and account.id not in s.view.full_detail) or \
                    not s.view.sees(account, {"team": r["team"], "teammate": r["teammate"]}) or \
                    (f.any and not f.keeps(str(r["team"] or ""), str(r["teammate"] or ""))):
                continue
            scope = (str(r["team"] or ""), str(r["teammate"] or ""))
            stored.setdefault((account.id, str(r["month"])), []).append(scope)
            methods.setdefault(account.id, set()).add(str(r["method"]))
            first = dates.month(r["month"])
            span = (first, dates.month_end(first))
            cols = projection.setdefault(account.id, [0] * len(buckets))
            for n, bucket in enumerate(buckets):
                share = dates.overlap(span, bucket)
                if share:
                    cols[n] += money.prorate(int(r["amount"]), share, dates.days(*span))
        # A month nobody projected gets the line's default from its posted history.
        for (account_id, month, team, teammate), (amount, method) in _defaults(
                s, pnl_ids, _months(buckets[0][0], buckets[-1][1]), basis, f, stored).items():
            if _covered(stored.get((account_id, month), []), team, teammate):
                continue
            first = dates.month(month)
            span = (first, dates.month_end(first))
            methods.setdefault(account_id, set()).add(method)
            cols = projection.setdefault(account_id, [0] * len(buckets))
            for n, bucket in enumerate(buckets):
                share = dates.overlap(span, bucket)
                if share:
                    cols[n] += money.prorate(amount, share, dates.days(*span))
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
                     "projection": None if hidden else projection.get(account_id),
                     "projection_method": None if hidden else _method(methods.get(account_id))})
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
                "excluded": sup.excluded, "view_version": s.view.version,
                "imported": tab.imported_months(c, s.ctx, s.view, a.isoformat()[:7], z.isoformat()[:7], basis),
                "snapshot": snap}
    if view == "expenses":
        out["teammates"] = _side_panel(s, spent, tops, versions, a, z, f)
    return out


def pnl_by(c: sqlite3.Connection, viewer: str, by: str, start: str | None = None, end: str | None = None, *,
           basis: str | None = None, top_n: int = 20, at: str | None = None) -> Row:
    """The P&L over a window split by location, class, project or a pair of them (`by`): income, expense and net per
    group.
    Lines with none of those tags form the Unattributed group. The totals are summed on their own, so the groups adding
    up to them is a check, not a restatement."""
    if by not in LOCATED:
        raise invalid(f"by is {'|'.join(LOCATED)}")
    if basis not in (None, "cash", "accrual"):
        raise invalid("basis is cash|accrual")
    s = _open(c, viewer, at)
    a, z = _window(start, end, s.ctx)
    s, snap = _closed(s, viewer, a, z)
    basis = basis or s.ctx.book.basis
    ids = [x.id for x in s.ctx.accounts.values() if x.type in PNL_TYPES and s.view.sees_account(x)]
    sup = _suppress(s, _cells(s, a, z, ids, basis), False, a, z)
    groups: dict[str, list[int]] = {}
    totals = [0, 0]                                    # income, expense
    for cell in sup.cells:
        account = s.ctx.accounts.get(cell.account_id)
        if account is None or account.type not in PNL_TYPES or cell.account_id in sup.hidden:
            continue
        income = account.type == "income"
        value = -cell.amount if income else cell.amount    # income is posted as credits
        groups.setdefault(_located_key(cell, by), [0, 0])[0 if income else 1] += value
        totals[0 if income else 1] += value
    named = _located_names(s.ctx.c, by)
    rows: list[Row] = [{"key": k or "unattributed", "label": _located_label(named, by, k), "income": v[0],
                        "expense": v[1], "net": v[0] - v[1]} for k, v in groups.items()]
    rows.sort(key=lambda r: (-abs(cast(int, r["net"])), str(r["label"])))
    top = max(1, min(int(top_n), MAX_TOP))
    if len(rows) > top:
        rest = rows[top:]
        rows = [*rows[:top], {"key": "other", "label": f"Other · {len(rest)}", "count": len(rest),
                              **{f: sum(cast(int, r[f]) for r in rest) for f in ("income", "expense", "net")}}]
    return {"by": by, "start": a.isoformat(), "end": z.isoformat(), "basis": basis, "rows": rows,
            "income": totals[0], "expense": totals[1], "net": totals[0] - totals[1], "excluded": sup.excluded,
            "snapshot": snap, "view_version": s.view.version}


def _closed(s: _Seen, viewer: str, a: date, z: date) -> tuple[_Seen, Row | None]:
    """A window inside one closed month reads through that month's snapshot tree: the accounts as they stood when it
    closed. Amounts still come from the posted rows the viewer may see. Also the month's snapshot marker."""
    if (a.year, a.month) != (z.year, z.month):
        return s, None
    month = a.isoformat()[:7]
    ctx = CL.frozen(s.ctx, month)
    if ctx is None:
        return s, None
    return _Seen(ctx=ctx, view=ctx.view(viewer), private=s.private), CL.marker(s.ctx.c, s.ctx.book.id, month)


def _segments(a: date, z: date) -> list[tuple[date, date]]:
    """The window cut at month ends, so each piece is one calendar month of it and reads the tree that month reads."""
    out: list[tuple[date, date]] = []
    first = a
    while first <= z:
        last = min(dates.month_end(first.replace(day=1)), z)
        out.append((first, last))
        first = last + timedelta(days=1)
    return out


def _spent(trees: Sequence[tuple[date, date, _Seen]], b: B.Budget, p: date, q: date) -> tuple[int, bool]:
    """A budget's spend over [p, q], each month of it counted on the account tree that month reads with and netted as
    that month's viewer reads it. Also whether a settled private line came out of the sum."""
    total = 0
    excluded = False
    for sa, sz, seen in trees:
        lo, hi = max(sa, p), min(sz, q)
        if lo <= hi:
            read = budgeting.Netted(seen.view)
            total += budgeting.actual(seen.ctx, b, lo.isoformat(), hi.isoformat(), read=read)
            excluded = excluded or read.excluded
    return total, excluded


def _covered(scopes: Sequence[tuple[str, str]], team: str, teammate: str) -> bool:
    """Whether a stored projection's scope covers a history scope: a scope with no team or teammate covers those under
    it (none at all covers the whole line), and a teammate's scope covers only that teammate."""
    return any(t in ("", team) and m in ("", teammate) for t, m in scopes)


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
def _located_key(cell: VW.Cell, by: str) -> str:
    """The group a cell falls in by location, class, project or a pair: its values joined with | ("x|" when only one is
    set), "" when it has none of them."""
    values = [{"location": cell.location, "class": cell.class_id, "project": cell.project}[kind] for kind in
    by.split("_")]
    return "|".join(values) if any(values) else ""


def _located_names(c: sqlite3.Connection, by: str) -> dict[str, dict[str, str]]:
    """Each location, class or project's name by id, for the kinds a group names (history keeps names of inactive
    ones)."""
    names: dict[str, dict[str, str]] = {}
    for kind in by.split("_"):
        names[kind] = RR.names(c, "locations") if kind == "location" else \
            RR.names(c, "projects") if kind == "project" else SC.class_names(c)
    return names


def _located_label(names: Mapping[str, Mapping[str, str]], by: str, key: str) -> str:
    if not key:
        return UNATTRIBUTED
    parts = key.split("|") if "_" in by else [key]
    return " · ".join(names[kind].get(value, value) if value else UNATTRIBUTED
                      for kind, value in zip(by.split("_"), parts, strict=True))


def _key(s: _Seen, cell: VW.Cell, by: str) -> str | None:
    """The group a cell falls in; "" is Unattributed (no team and no teammate); NO_TEAM is a teammate's spend with no
    team; None leaves it out (a person's line in a by-bot read)."""
    if by in LOCATED:
        return _located_key(cell, by)
    if by in ("department", "team"):
        if not cell.team:
            return NO_TEAM if cell.teammate else ""
        return s.ctx.org.department(cell.team) if by == "department" else cell.team
    if by == "teammate":
        return cell.teammate
    if by == "bot":
        return cell.teammate if cell.teammate.startswith("bot:") else None
    if by == "person":
        return cell.teammate if cell.teammate.startswith("human:") else None
    if by == "vendor":
        return cell.vendor
    return cell.account_id


def _labels(s: _Seen, keys: Iterable[str], by: str) -> dict[str, str]:
    """Each group's label; a vendor group's from tab_ap.vendor_labels (a contact's name, never its id)."""
    if by in LOCATED:
        named = _located_names(s.ctx.c, by)
        return {k: _located_label(named, by, k) for k in keys}
    names = tab_ap.vendor_labels(s.ctx, s.view, keys) if by == "vendor" else {}
    return {k: names.get(k, k) if by == "vendor" and k else _label(s, k, by) for k in keys}


def _label(s: _Seen, key: str, by: str) -> str:
    if not key:
        return UNATTRIBUTED if by != "vendor" else "No vendor"
    if by in ("department", "team"):
        if key == NO_TEAM:
            return "No team"
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
    labels = _labels(s, sums, by)
    rows: list[Row] = [{"key": k or NONE, "label": labels[k], "amount": v} for k, v in sums.items()]
    return {"by": by, "start": a.isoformat(), "end": z.isoformat(), "rows": _cap(rows, top_n),
            "total": sum(sums.values()), "excluded": sup.excluded, "hidden": _hidden_labels(s, sup.hidden),
            "view_version": s.view.version}


def expenses(c: sqlite3.Connection, viewer: str, start: str | None = None, end: str | None = None,
             by: str = "department", *, account_id: str | None = None, team: str | None = None,
             teammate: str | None = None, top_n: int = 20, at: str | None = None) -> Row:
    """Expenses grouped by department, team, teammate, bot, person, vendor, line, location, class, project or a pair of
    those (location_project and so on). Unattributed (no team or teammate) shows only to viewers who see company-wide
    expenses; a located group's Unattributed is the lines with none of those tags."""
    s = _open(c, viewer, at)
    a, z = _window(start, end, s.ctx)
    return _grouped(s, a, z, by, account_id, team, teammate, top_n)


def spend_by(c: sqlite3.Connection, viewer: str, by: str, start: str | None = None, end: str | None = None, *,
             account_id: str | None = None, team: str | None = None, top_n: int = 10, at: str | None = None) -> Row:
    """The top spenders on a line or across expenses: a ranking with one "Other · n" row."""
    s = _open(c, viewer, at)
    a, z = _window(start, end, s.ctx)
    return _grouped(s, a, z, by, account_id, team, None, top_n)


def revenue_by_item(c: sqlite3.Connection, viewer: str, start: str | None = None, end: str | None = None, *,
                    top_n: int = 10, at: str | None = None) -> Row:
    """Revenue by item: the posted invoices' lines in the window, summed by the item each names; a line that names none
    is Unattributed. Receivables only: a viewer without the receivables view is refused, as the aging report is. An
    invoice with a line on a private account this viewer lacks full detail for is left out whole."""
    ctx = load(c, at)
    view = ctx.view(viewer)
    if not tab_ap.sees_side(ctx, view, "invoice"):
        raise denied("receivables are not in your view")
    a, z = _window(start, end, ctx)
    cut = tab.private_cut(ctx, view)
    rows: list[Row] = []
    for r in SAP.invoice_item_revenue(c, ctx.book.id, a.isoformat(), z.isoformat(), cut=cut):
        key, label = (str(r["item_id"]), str(r["item_name"] or r["item_id"])) if r["item_id"] else (NONE, UNATTRIBUTED)
        rows.append({"key": key, "label": label, "amount": int(r["amount"] or 0)})
    return {"by": "item", "start": a.isoformat(), "end": z.isoformat(), "rows": _cap(rows, top_n),
            "total": sum(int(str(r["amount"])) for r in rows), "view_version": view.version}


# ------------------------------------------------------------------------------------------------ one line
_RowKey = tuple[str, str, str, str]        # a breakdown row's history: line, vendor, team, teammate


def _largest_remainder(amount: int, weights: Mapping[_RowKey, int]) -> dict[_RowKey, int]:
    """`amount` split over positive weights to the cent: each key's floor share, then the cents left one each to the
    largest remainders, ties to the smallest key (never the order the keys came in)."""
    total = sum(weights.values())
    parts = {k: amount * w // total for k, w in weights.items()}
    left = amount - sum(parts.values())                 # 0 <= left < len(weights): floors only fall short
    for k in sorted(weights, key=lambda k: (-(amount * weights[k] % total), k))[:left]:
        parts[k] += 1
    return parts


@dataclass(frozen=True)
class _Atom:
    """One piece of next month's projection: a saved projection's scope, or a default for a team and teammate (or the
    whole line, `whole`, when nothing is saved on it)."""
    account_id: str
    team: str
    teammate: str
    amount: int
    method: str
    whole: bool = False
    saved: bool = False

    def covers(self, cell: VW.Cell) -> bool:
        if cell.account_id != self.account_id:
            return False
        if self.whole:
            return True
        if self.saved:                      # a saved projection covers its scope (`_covered`)
            return _covered([(self.team, self.teammate)], cell.team or "", cell.teammate or "")
        return (cell.team or "", cell.teammate or "") == (self.team, self.teammate)


def _next_atoms(s: _Seen, ids: Sequence[str], f: _Filter | None) -> tuple[date, list[_Atom]] | None:
    """Next month and the pieces its projection adds up from (the saved projections the viewer sees within the filter,
    then a default for each scope none covers); None on a private line without its detail. The line's projection and
    its breakdown both sum these, so they agree to the cent."""
    nxt = dates.month_end(dates.day(s.ctx.today)) + timedelta(days=1)
    month = nxt.isoformat()[:7]
    if any(i in s.private and i not in s.view.full_detail for i in ids):
        return None
    atoms: list[_Atom] = []
    stored: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for r in SB.projections(s.ctx.c, s.ctx.book.id, month, month):
        account = s.ctx.accounts.get(r["account_id"])
        if account is None or account.id not in ids or \
                not s.view.sees(account, {"team": r["team"], "teammate": r["teammate"]}) or \
                (f is not None and f.any and not f.keeps(str(r["team"] or ""), str(r["teammate"] or ""))):
            continue
        team, mate = str(r["team"] or ""), str(r["teammate"] or "")
        stored.setdefault((account.id, month), []).append((team, mate))
        atoms.append(_Atom(account.id, team, mate, int(r["amount"]), str(r["method"]), saved=True))
    for (account_id, _, team, teammate), (amount, method) in _defaults(s, ids, [nxt], "accrual", f, stored).items():
        scopes = stored.get((account_id, month), [])
        if _covered(scopes, team, teammate):
            continue
        atoms.append(_Atom(account_id, team, teammate, amount, method, whole=not scopes))
    return nxt, atoms


def _next_month(s: _Seen, ids: Sequence[str], f: _Filter | None = None) -> Row:
    """The line's projection for the month after this one, as far as the viewer sees it and within the org filter
    (projections are by line, team and teammate; there are none by vendor). Each team and teammate with no stored
    projection for that month gets its own default from posted history. None on a private line without its detail."""
    got = _next_atoms(s, ids, f)
    if got is None:
        nxt = dates.month_end(dates.day(s.ctx.today)) + timedelta(days=1)
        return {"month": nxt.isoformat()[:7], "projection": None, "projection_method": None}
    nxt, atoms = got
    return {"month": nxt.isoformat()[:7], "projection": sum(a.amount for a in atoms) if atoms else None,
            "projection_method": _method({a.method for a in atoms})}


def _next_by(s: _Seen, ids: Sequence[str], f: _Filter, vendor: str | None) -> dict[str, dict[str, int]]:
    """Next month's projection for each vendor, team and teammate of a line (`by` -> key -> amount), split as the
    drill-down's rows are (`_key`, NONE for none). It shares out the same pieces as the line's projection
    (`_next_atoms`: saved projections, §4.4, and each line's own default method), so each column adds up to it: a
    piece goes over the vendor / team / teammate rows of the history it covers, weighted by each row's own default,
    equally when none has one, and to the piece's own team and teammate (Unattributed or No team) when it covers no
    history. Amounts are split with the remainder of the rounding on the largest share, never lost. `vendor` keeps
    that vendor's share. Empty on a private line without its detail."""
    got = _next_atoms(s, ids, f) if ids else None
    if got is None:
        return {}
    nxt, atoms = got
    today = dates.day(s.ctx.today)
    cur = today.replace(day=1)
    first = dates.add_months(cur, -PJ.TREND_MONTHS)
    recent_from = today - timedelta(days=PJ.RATE_DAYS - 1)
    days = dates.days(nxt, dates.month_end(nxt))
    lines = sorted({a.account_id for a in atoms})
    cells = _cells(s, first, today, lines, "accrual")
    if f.any:
        cells = [x for x in cells if f.keeps(x.team, x.teammate)]
    sup = _suppress(s, cells, f.narrow(s.ctx.org), first, today)
    shown = [x for x in sup.cells if x.account_id not in sup.hidden]
    monthly: dict[_RowKey, list[int]] = {}
    recent: dict[_RowKey, int] = {}
    for cell in shown:
        k = (cell.account_id, cell.vendor or "", cell.team or "", cell.teammate or "")
        amount = (-1 if s.ctx.accounts[cell.account_id].type == "income" else 1) * cell.amount
        day = dates.day(cell.day)
        monthly.setdefault(k, [0] * PJ.TREND_MONTHS)
        recent[k] = recent.get(k, 0) + (amount if day >= recent_from else 0)
        if day < cur:
            monthly[k][(day.year - first.year) * 12 + day.month - first.month] += amount
    def weight(k: _RowKey) -> int:
        account = s.ctx.accounts[k[0]]
        return PJ.estimate(PJ.default_method(account.type, account.subtype), monthly[k], recent[k], days, 2) or 0

    out: dict[str, dict[str, int]] = {"vendor": {}, "team": {}, "teammate": {}}
    for atom in atoms:
        mine = sorted({(x.account_id, x.vendor or "", x.team or "", x.teammate or "") for x in shown if atom.covers(x)})
        weights = {k: max(0, weight(k)) for k in mine}
        if not any(weights.values()):
            weights = dict.fromkeys(mine, 1)
        if not weights:
            weights = {(atom.account_id, "", atom.team, atom.teammate): 1}
        parts = _largest_remainder(atom.amount, weights)
        for (i, v, team, mate), amount in parts.items():
            if vendor is not None and v != vendor:
                continue
            cell = VW.Cell(account_id=i, day="", team=team, teammate=mate, vendor=v, amount=0)
            for by in out:
                key = _key(s, cell, by) or NONE
                out[by][key] = out[by].get(key, 0) + amount
    return out


def _next_labels(s: _Seen, keys: Iterable[str], by: str) -> dict[str, str]:
    """Each next_by key's label, as the breakdown rows have them (NONE is the group with none), so a row
    that only has a projection is named too."""
    raw = {k: "" if k == NONE else k for k in keys}
    labels = _labels(s, set(raw.values()), by)
    return {k: labels[r] for k, r in raw.items()}


def _months(a: date, z: date) -> list[date]:
    """The first day of each month from `a`'s month to `z`'s."""
    count = (z.year * 12 + z.month) - (a.year * 12 + a.month)
    return [dates.add_months(a, k) for k in range(count + 1)]


def _method(found: set[str] | None) -> str | None:
    """The one method behind a projection, or "mixed" when its months came from several."""
    if not found:
        return None
    return next(iter(found)) if len(found) == 1 else "mixed"


def _defaults(s: _Seen, ids: Iterable[str], months: Sequence[date], basis: str, f: _Filter | None,
              stored: Mapping[tuple[str, str], Sequence[tuple[str, str]]]
              ) -> dict[tuple[str, str, str, str], tuple[int, str]]:
    """The default projection per line, month (`YYYY-MM`), team and teammate, from the posted history this viewer sees,
    for the months that are this one or later. A line month with no stored projection gets one default for the whole
    line, its trend fitted to the line's total history: clamping each team's trend alone would count a falling team as
    nothing and overstate the line. Only where stored projections cover some of its scopes is the line split, each
    uncovered scope taking its own default, so a stored one leaves the others standing. A line with no history, or one
    this viewer sees no full detail of, gets none."""
    today = dates.day(s.ctx.today)
    cur = today.replace(day=1)
    targets = [m for m in months if m >= cur]
    lines = sorted(i for i in ids if i in s.ctx.accounts and not (i in s.private and i not in s.view.full_detail))
    if not targets or not lines:
        return {}
    first = dates.add_months(cur, -PJ.TREND_MONTHS)
    recent_from = today - timedelta(days=PJ.RATE_DAYS - 1)
    cells = _cells(s, first, today, lines, basis)
    if f is not None and f.any:
        cells = [x for x in cells if f.keeps(x.team, x.teammate)]
    sup = _suppress(s, cells, f is not None and f.narrow(s.ctx.org), first, today)
    scoped = [x for x in sup.cells if x.account_id not in sup.hidden]
    keys = sorted({(x.account_id, x.team or "", x.teammate or "") for x in scoped})
    names = sorted({k[0] for k in keys})
    monthly = {k: [0] * PJ.TREND_MONTHS for k in keys}
    recent = dict.fromkeys(keys, 0)
    line_monthly = {i: [0] * PJ.TREND_MONTHS for i in names}
    line_recent = dict.fromkeys(names, 0)
    for cell in scoped:
        key = (cell.account_id, cell.team or "", cell.teammate or "")
        account = s.ctx.accounts[cell.account_id]
        amount = (-1 if account.type == "income" else 1) * cell.amount
        day = dates.day(cell.day)
        if day >= recent_from:
            recent[key] += amount
            line_recent[cell.account_id] += amount
        if day < cur:
            index = (day.year - first.year) * 12 + day.month - first.month
            monthly[key][index] += amount
            line_monthly[cell.account_id][index] += amount
    out: dict[tuple[str, str, str, str], tuple[int, str]] = {}
    for i in names:
        account = s.ctx.accounts[i]
        method = PJ.default_method(account.type, account.subtype)
        for m in targets:
            month = m.isoformat()[:7]
            ahead = (m.year - cur.year) * 12 + m.month - cur.month + 1
            span_days = dates.days(m, dates.month_end(m))
            scopes = stored.get((i, month), [])
            if not scopes:
                value = PJ.estimate(method, line_monthly[i], line_recent[i], span_days, ahead)
                if value is not None:
                    out[(i, month, "", "")] = (value, method)
                continue
            for key in keys:
                if key[0] != i or _covered(scopes, key[1], key[2]):
                    continue
                value = PJ.estimate(method, monthly[key], recent[key], span_days, ahead)
                if value is not None:
                    out[(i, month, key[1], key[2])] = (value, method)
    return out


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
    filter it as the P&L's expenses view does. `next_month` is the line's projection (within the org filter), and
    `next_by` each vendor's, team's and teammate's default for that month.

    Private totals come only from whole cohorts: a vendor filter is always narrow (so a payroll cell is never rebuilt
    from a caller-picked subset and recovered by subtraction), and an org filter is narrow as the P&L's is."""
    s = _open(c, viewer, at)
    a, z = _window(start, end, s.ctx)
    account = s.ctx.accounts.get(account_id)
    if account is None or not s.view.sees_account(account):
        raise FinanceError(NOT_FOUND, "no such account")
    f = _org_filter(s, teams, teammates, who, unattributed)
    if vendor is not None:
        vendor = SAP.vendor_resolved(c, s.ctx.book.id, vendor)
    narrow = vendor is not None or f.narrow(s.ctx.org)
    # Each month counts the line and the lines under it on the tree it closed with (the live tree while it is open), and
    # is suppressed on that tree alone, as a read of that month is. A private month's cells come from its whole fiscal
    # quarters, so suppressing the window at once would count them once for each month that reads them.
    spans: list[tuple[str, str, _Seen, set[str]]] = []
    whole_cells: list[VW.Cell] = []
    split_cells: list[VW.Cell] = []
    whole_hidden: set[str] = set()
    hidden: set[str] = set()
    excluded = False
    for sa, sz in _segments(a, z):
        tree = _closed(s, viewer, sa, sz)[0]
        mine = set(descendants(tree.ctx.accounts, account_id))
        spans.append((sa.isoformat(), sz.isoformat(), tree, mine))
        cells = _cells(tree, sa, sz, sorted(mine))
        if vendor is not None:
            cells = [x for x in cells if x.vendor == vendor]
        if f.any:
            cells = [x for x in cells if f.keeps(x.team, x.teammate)]
        whole = _suppress(tree, cells, narrow, sa, sz)
        split = _suppress(tree, cells, True, sa, sz)
        whole_cells += whole.cells
        split_cells += split.cells
        whole_hidden |= whole.hidden
        hidden |= whole.hidden | split.hidden
        excluded = excluded or split.excluded
    ids = sorted({i for _, _, _, mine in spans for i in mine})
    now_ids = sorted(descendants(s.ctx.accounts, account_id))
    # A vendor drill-down shows its seats and renewal date (spec §12) only where the viewer sees that vendor's cells on
    # this line, or has the payables view; a contact of another book shows nothing.
    contact = SAP.get(c, "fin_contacts", vendor) if vendor else None
    sees_vendor = (account_id not in whole_hidden and bool(whole_cells)) or s.view.payables or s.view.everything
    if contact is not None and (str(contact["book_id"]) != s.ctx.book.id or not sees_vendor):
        contact = None
    next_by = _next_by(s, now_ids, f, vendor)
    out: Row = {"account": {"id": account.id, "code": account.code, "name": account.name, "type": account.type},
                "start": a.isoformat(), "end": z.isoformat(), "view_version": s.view.version,
                "total": None if account_id in whole_hidden else sum(x.amount for x in whole_cells),
                "excluded": excluded, "hidden": _hidden_labels(s, hidden),
                "vendor": vendor, "filter": {"teams": list(f.teams), "teammates": list(f.teammates), "who": f.who,
                                             "unattributed": f.unattributed},
                "next_month": _next_month(s, now_ids, f), "next_by": next_by,
                "next_labels": {by: _next_labels(s, keys, by) for by, keys in next_by.items()},
                "seats": contact["seats"] if contact is not None else None,
                "renewal_on": (contact["renewal_on"] or None) if contact is not None else None}
    for by in ("vendor", "team", "teammate"):
        sums: dict[str, int] = {}
        for cell in split_cells:
            key = _key(s, cell, by) or ""
            if key == "" and by != "vendor" and not s.view.company_wide(account):
                continue
            sums[key] = sums.get(key, 0) + cell.amount
        labels = _labels(s, sums, by)
        out["by_" + by] = _cap([{"key": k or NONE, "label": labels[k], "amount": v}
                                for k, v in sums.items()], top_n)
    entries: list[Row] = []
    more = False
    private = any(i in s.private and i not in s.view.full_detail for i in ids)
    if not private:
        most = max(1, min(int(limit), 500))
        cap = most * 4 + 1
        keys = None if vendor is None else SAP.vendor_keys(c, s.ctx.book.id, vendor) or [""]
        # Each month reads its own newest lines on its own tree and keeps only those that month's view takes, before the
        # cut: a newer line the viewer cannot see never pushes out an older one they can.
        visible: list[tuple[Any, dict[str, str]]] = []
        for lo, hi, tree, mine in spans:
            if mine:
                visible += _month_entries(c, s.ctx.book.id, tree, lo, hi, mine, f, keys, cap)
        visible.sort(key=lambda pair: str(pair[0]["line_id"]))
        visible.sort(key=lambda pair: str(pair[0]["entry_date"]), reverse=True)
        visible = visible[:cap]
        more = len(visible) > most
        seen: list[Row] = []
        for r, mark in visible:
            seen.append({"amount": int(r["amount"]), "card_id": r["card_id"], "money_account_id": r["money_account_id"],
                         "source": r["source"]})
            if len(entries) < most:
                entries.append({"line_id": r["line_id"], "entry_id": r["entry_id"], "date": r["entry_date"],
                                "account_id": r["account_id"], "amount": int(r["amount"]), "vendor": r["contact_id"],
                                "description": r["description"], "memo": r["memo"], "source": r["source"],
                                "status": str(r["entry_status"]), "reverses": r["reverses_id"] or None,
                                "tags": mark})
        shown = {e["line_id"] for e in entries}
        names = tab_ap.vendor_labels(s.ctx, s.view, [str(e["vendor"] or "") for e in entries])
        _, closed = tab.pays_labels(s.ctx, s.view, [r for r, _ in visible if r["line_id"] in shown])
        for e in entries:
            e["vendor_name"] = names.get(str(e["vendor"] or ""), "")
            if e["entry_id"] in closed:
                e["description"], e["memo"] = "", ""
        out["paid_by"] = _paid_by(s, seen)
    else:
        out["paid_by"] = None
    out["entries"] = entries
    out["more"] = more
    return out


_ENTRY_BATCH = 200


def _month_entries(c: sqlite3.Connection, book_id: str, tree: _Seen, lo: str, hi: str, mine: set[str], f: _Filter,
                   keys: list[str] | None, cap: int) -> list[tuple[Any, dict[str, str]]]:
    """The newest lines on `mine` in [lo, hi] that `tree`'s view takes and the filter keeps, up to `cap`, each with its
    tags. Read in batches, so a run of lines the view hides never cuts a visible one short."""
    out: list[tuple[Any, dict[str, str]]] = []
    offset = 0
    while len(out) < cap:
        batch = SL.entries_on(c, book_id, sorted(mine), lo, hi, _ENTRY_BATCH, keys, offset)
        tags = SL.line_tags(c, [r["line_id"] for r in batch]) if batch else {}
        # As Transactions: a line of a bank or card row standing for a private line never shows to a viewer without that
        # line's full detail, amount and all (judged on this month's own tree and view).
        stands = SK.feed_by_entry(c, sorted({str(r["entry_id"]) for r in batch}))
        origins = {str(r["line_id"]): tab.feed_origin(r) for r in batch}
        history = SK.feed_rows(c, sorted({o for o in origins.values() if o}))
        cut = tab.private_feed(tree.ctx, tree.view, [*stands.values(), *history.values()])
        for r in batch:
            mark = tags.get(r["line_id"], {})
            account = tree.ctx.accounts.get(r["account_id"])
            if account is None or not tree.view.sees(account, mark) or origins[str(r["line_id"])] in cut:
                continue
            if f.any and not f.keeps(str(mark.get("team") or ""), str(mark.get("teammate") or "")):
                continue
            out.append((r, mark))
            if len(out) == cap:
                break
        if len(batch) < _ENTRY_BATCH:
            break
        offset += _ENTRY_BATCH
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
            "period_start": st.period_start, "period_end": st.period_end,
            "excluded": st.excluded and not cut}


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
    rows: list[Row] = []
    netted = False
    for b in seen[:most]:
        st = budgeting.state(s.ctx, b.id, day, read=budgeting.Netted(s.view))
        state_row = _state_row(s, b, st)
        netted = netted or bool(state_row["excluded"])
        rows.append({**_budget_row(b), "state": state_row})
    return {"budgets": rows, "more": len(seen) > most, "on": day.isoformat(), "view_version": s.view.version,
            "excluded": netted}


def budget_vs_actual(c: sqlite3.Connection, viewer: str, start: str | None = None, end: str | None = None,
                     period: str = "month", *, limit: int = 200, at: str | None = None) -> Row:
    """Each visible budget's amount and actual spend per period bucket (a monthly budget prorated over days or
    weeks)."""
    s = _open(c, viewer, at)
    if period not in dates.BUCKETS:
        raise invalid(f"period is {'|'.join(dates.BUCKETS)}")
    a, z = _window(start, end, s.ctx)
    buckets = dates.buckets(period, a, z, s.ctx.book.fiscal_year_start)
    base = s
    s, snap = _closed(base, viewer, a, z)
    trees = [(sa, sz, _closed(base, viewer, sa, sz)[0]) for sa, sz in _segments(a, z)]
    # A budget is a candidate when the live tree or a month of the window lists it for the viewer, each read on its own
    # tree. It is listed only where the viewer sees it in every month of the window, each judged on that month's own
    # tree, as a read of that month alone judges it. A month whose tree lacks the budget's account has no spend to show.
    live = s.ctx.budgets()
    pool: dict[str, B.Budget] = {}
    for ctx in [s.ctx, *(x.ctx for _, _, x in trees)]:
        for b in ctx.budgets().values():
            if b.active and sees_budget(ctx, viewer, b):
                pool.setdefault(b.id, live.get(b.id, b))
    seen = [b for b in pool.values()
            if all(sees_budget(x.ctx, viewer, b) for _, _, x in trees if b.catch_all or b.account_id in x.ctx.accounts)]
    most = max(1, min(int(limit), 500))
    versions = SB.versions(c, [b.id for b in seen[:most]])
    rows: list[Row] = []
    for b in seen[:most]:
        # Judged on each month's own tree: a private line can sit under this budget in a closed month and not now.
        cut = any(private_scope(x.ctx, viewer, b) for _, _, x in trees)
        spent = None if cut else [_spent(trees, b, p, q) for p, q in buckets]
        rows.append({**_budget_row(b),
                     "budget": [B.in_window(b, versions[b.id], q, s.ctx.book.fiscal_year_start) for q in buckets],
                     "actual": None if spent is None else [amt for amt, _ in spent],
                     "excluded": spent is not None and any(out for _, out in spent)})
    return {"start": a.isoformat(), "end": z.isoformat(), "period": period,
            "columns": [{"start": p.isoformat(), "end": q.isoformat(), "label": dates.label(p, period)}
                        for p, q in buckets],
            "budgets": rows, "more": len(seen) > most, "view_version": s.view.version,
            "excluded": any(r["excluded"] for r in rows),
            "imported": tab.imported_months(c, s.ctx, s.view, a.isoformat()[:7], z.isoformat()[:7],
                                             s.ctx.book.basis),
            "snapshot": snap}


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
        row = _state_row(s, b, budgeting.state(s.ctx, b.id, today, read=budgeting.Netted(s.view)))
        if name == "budget_remaining":
            return {**base, "value": row["remaining"], "unit": "minor", "currency": currency,
                    "excluded": row["excluded"]}
        return {**base, "value": row["pct"], "unit": "percent", "excluded": row["excluded"]}
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


def _uncategorized(s: _Seen, a: date, z: date, most: int, skip: int = 0) -> tuple[list[Any], bool]:
    """Settled rows the viewer sees still waiting for a category, newest first, among the `most` after the first
    `skip` (counted before the private cut, so a cursor of raw positions never skips a row), and whether there are
    more."""
    money_ids, card_ids = tab.feed_scope(s.ctx, s.view)
    other = [x.id for x in s.ctx.accounts.values() if x.code == tab.OTHER_CODE]
    rows = SAP.uncategorized(s.ctx.c, money_ids, card_ids, other, a.isoformat(), z.isoformat(), skip + most + 1)
    window = rows[skip:skip + most]
    cut = tab.private_feed(s.ctx, s.view, window)
    kept = [r for r in window if str(r["id"]) not in cut]
    return kept, len(rows) > skip + most


# ------------------------------------------------------------------------------------------------ reports
REPORTS = ("pnl", "pnl_by", "balance_sheet", "cash_flow", "budget_vs_actual", "ap_aging", "ar_aging", "spend_by",
           "revenue_by_item")


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
        shown.append({"key": OTHER, "label": f"Other · {len(rest)}",
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
           by: str | None = None, at: str | None = None) -> Row:
    """One report (§7.1): pnl (accrual or cash), balance_sheet (as of `end`), cash_flow, budget_vs_actual, ap_aging,
    ar_aging, spend_by (`by` department|team|teammate|bot|person|vendor|account|location|class|project or a pair),
    pnl_by (`by` location|class|project or a pair) or revenue_by_item (posted invoices by the item each line names;
    receivables view). `compare=prior` adds the window before it. Payloads are capped: `top_n` rows and one
    "Other · n"."""
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
        if kind == "pnl_by":
            return pnl_by(c, viewer, by or "location", p.isoformat(), q.isoformat(), basis=basis, top_n=top, at=at)
        if kind == "cash_flow":
            return _cash_flow(c, viewer, p, q, period, at)
        if kind == "budget_vs_actual":
            return budget_vs_actual(c, viewer, p.isoformat(), q.isoformat(), period, limit=top, at=at)
        if kind == "revenue_by_item":
            return revenue_by_item(c, viewer, p.isoformat(), q.isoformat(), top_n=top, at=at)
        return spend_by(c, viewer, by or "vendor", p.isoformat(), q.isoformat(), top_n=top, at=at)
    out: Row = {"kind": kind, **one(a, z)}
    if compare == "prior":
        p, q = _prior(a, z)
        out["prior"] = one(p, q)
    return out


# ------------------------------------------------------------------------------------------------ search
SEARCH_KINDS = ("entry", "feed", "bill", "invoice", "contact", "rule", "card", "item", "location", "project", "po",
              "estimate", "equipment", "due_date")
RECORD_KINDS = {"item": "items", "location": "locations", "project": "projects", "equipment": "equipment"}
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


def _record_row(kind: str, r: Any, source: str, as_of: str) -> Row:
    """A record as a search result: its name is the label, and its own type keeps its own field. It names where it
    came from and the time it was read, so an answer built on it can cite both."""
    row: Row = {"kind": kind, "id": str(r["id"]), "label": str(r["name"]), "archived_at": r["archived_at"],
                "source": source, "as_of": as_of}
    if kind == "item":
        row["item_kind"] = str(r["kind"])
        row["sku"] = str(r["sku"])
    elif kind == "location":
        row["location_kind"] = str(r["kind"])
    elif kind == "equipment":
        row["equipment_kind"] = str(r["kind"])
    else:
        row["status"] = str(r["status"])
    return row


RECORD_TABLE = {"location": "locations", "item": "items", "project": "projects", "equipment": "equipment"}
RECORD_PLURAL = {"location": "locations", "item": "items", "project": "projects", "equipment": "equipment"}


ITEM_ACCOUNTS = ("income_account_id", "expense_account_id", "asset_account_id")


def _record_rows(c: sqlite3.Connection, ctx: Ctx, kind: str, rows: Sequence[Any]) -> list[Row]:
    """Records as search rows, each with its source and the read time. A new searchable record kind adds its table to
    RECORD_KINDS and its rows through here, so the rows keep the same shape."""
    sources = SM.record_providers(c, RECORD_KINDS[kind], [str(r["id"]) for r in rows])
    return [_record_row(kind, r, sources.get(str(r["id"]), "tico"), ctx.at) for r in rows]


def _teammate(ctx: Ctx, viewer: str) -> bool:
    """A teammate: a person on the roster, or a bot the company runs (org.bots holds only the bots not archived)."""
    return ctx.org.is_person(viewer) or viewer in ctx.org.bots


def _teammate_or_refuse(ctx: Ctx, viewer: str) -> None:
    """Records are for teammates whatever their Finance view: names, addresses, customers and sale prices read for all
    of them, while an item's accounts and the costs stay per view. A guest, who is not on the roster, and an unknown
    actor read none of it."""
    if not _teammate(ctx, viewer):
        raise denied("records are for teammates")


def _customer_names(ctx: Ctx, kind: str, rows: Sequence[Any]) -> dict[str, str]:
    """The name of each customer the rows name, by contact id. An item names none."""
    ids = sorted({str(r["customer_id"]) for r in rows if kind != "item" and r["customer_id"]})
    return {cid: str(row["name"]) for cid, row in SAP.contacts(ctx.c, ids).items()}


def _account_shown(view: VW.View, accounts: Mapping[str, Account], account_id: object) -> bool:
    """Whether a record's account may show: an unset one always (it reveals nothing), a set one when the viewer sees
    that account."""
    if not account_id:
        return True
    account = accounts.get(str(account_id))
    return account is not None and view.sees_account(account)


WARRANTY_WINDOW_DAYS = 90


@dataclass
class _Narrow:
    """The filters a record read hands records/reads.search: equal, by (on or before), between (from, to), and the ids
    kept (None keeps every row, an empty list none)."""
    equal: dict[str, str]
    by: dict[str, str]
    between: dict[str, tuple[str, str]]
    ids: list[str] | None


def _narrow(ctx: Ctx, kind: str, f: Mapping[str, object]) -> _Narrow:
    """The filters a record read takes. Equipment is kept by its kind, its customer, its location (an alias resolves to
    the location it names), a warranty that ends on or before a day or within the window, and a due date that is soon or
    overdue: those ids are worked out from the live due dates, so the SQL paging counts right. Others take no filter."""
    out = _Narrow({}, {}, {}, None)
    if kind != "equipment":
        return out
    for key in ("kind", "customer_id"):
        value = _filter(f, key)
        if value is not None:
            out.equal[key] = value
    if out.equal.get("kind") not in (None, *RC.EQUIPMENT_KINDS):
        raise invalid(f"equipment kind is {'|'.join(RC.EQUIPMENT_KINDS)}")
    location = _filter(f, "location_id")
    if location is not None:
        out.equal["location_id"] = RS.resolve(ctx.c, "locations", location)
    ends = _filter(f, "warranty_ends_by")
    if ends is not None:
        out.by["warranty_ends_on"] = dates.day(ends).isoformat()
    warranty = _filter(f, "warranty")
    if warranty is not None:
        if warranty != "ending":
            raise invalid("warranty is ending")
        today = DD.today_of(ctx)
        out.between["warranty_ends_on"] = (today.isoformat(),
                                           (today + timedelta(days=WARRANTY_WINDOW_DAYS)).isoformat())
    due = _filter(f, "due")
    if due is not None:
        if due not in ("soon", "overdue"):
            raise invalid("due is soon or overdue")
        out.ids = DD.equipment_due(ctx, due)
    return out


def _customer_out(customer_id: object, names: Mapping[str, str]) -> Row:
    """A record's customer by id and name, for every teammate: the customer's name is operational."""
    key = str(customer_id) if customer_id else None
    return {"customer_id": key, "customer_name": names.get(key) if key else None}


def _record_out(ctx: Ctx, kind: str, r: Any, view: VW.View, source: str, names: Mapping[str, str],
                due: Mapping[str, str] | None = None) -> Row:
    """A record as the records API shows it, to a teammate: an item's sale price and description whatever the viewer's
    Finance view; an item's accounts show to the viewers who see them. A location's, equipment's or project's customer
    shows by id and name. A location's or equipment's next due day comes from `due` (records/due_dates.next_due_for),
    null when none is open. Only teammates reach this (see _teammate_or_refuse)."""
    out: Row = {"id": str(r["id"]), "name": str(r["name"]), "archived_at": r["archived_at"], "source": source,
                "as_of": ctx.at}
    if kind == "location":
        out.update(kind=str(r["kind"]), parent_id=r["parent_id"], address=json.loads(r["address"] or "{}"),
                   next_due_on=(due or {}).get(str(r["id"])))
        out.update(_customer_out(r["customer_id"], names))
    elif kind == "equipment":
        out.update(kind=str(r["kind"]), location_id=r["location_id"], make=str(r["make"]), model=str(r["model"]),
                   serial=str(r["serial"]), installed_on=r["installed_on"], warranty_ends_on=r["warranty_ends_on"],
                   photo_file_id=r["photo_file_id"], next_due_on=(due or {}).get(str(r["id"])))
        out.update(_customer_out(r["customer_id"], names))
    elif kind == "item":
        out.update(sku=str(r["sku"]), kind=str(r["kind"]), unit=str(r["unit"]), currency=str(r["currency"]),
                   description=str(r["description"]), taxable=bool(r["taxable"]), price=r["price"])
        out.update({key: r[key] for key in ITEM_ACCOUNTS if _account_shown(view, ctx.accounts, r[key])})
    else:
        out.update(status=str(r["status"]))
        out.update(_customer_out(r["customer_id"], names))
    return out


def records(c: sqlite3.Connection, viewer: str, *, kind: str, q: str = "", archived: str = "hide",
            cursor: str | None = None, limit: int = 50, at: str | None = None,
            equal: Mapping[str, str] | None = None, filters: Mapping[str, object] | None = None) -> Row:
    """Locations, items, projects or equipment by name (an item also by sku, equipment by serial, make or model), past
    `cursor`. `archived` is hide, only or all. `equal` narrows by column (a location's kind or customer, an item's kind,
    a project's status or customer); equipment takes `filters`: kind, customer_id, location_id, warranty_ends_by,
    warranty (ending) and due (soon|overdue), see _narrow. A location or equipment row carries `next_due_on`, its earliest
    open due day, or null. A guest is refused; an item's accounts are cut to what the viewer sees."""
    ctx = load(c, at)
    view = ctx.view(viewer)
    _teammate_or_refuse(ctx, viewer)
    parts = tab.decode_cursor(cursor)
    after = (parts[0], parts[1]) if len(parts) == 2 else None
    active = {"hide": True, "only": False, "all": None}[archived]
    most = tab.page_size(limit)
    narrow = _narrow(ctx, kind, filters or {})
    rows = RR.search(c, RECORD_TABLE[kind], (q or "").strip()[:100], active=active, after=after, most=most + 1,
                     equal={**(equal or {}), **narrow.equal}, by=narrow.by, between=narrow.between, ids=narrow.ids)
    shown = rows[:most]
    nxt = tab.encode_cursor([str(shown[-1]["name"]), str(shown[-1]["id"])]) if len(rows) > most else None
    sources = SM.record_providers(c, RECORD_TABLE[kind], [str(r["id"]) for r in shown])
    names = _customer_names(ctx, kind, shown)
    due = RD.next_due_for(c, kind, [str(r["id"]) for r in shown]) if kind in ("location", "equipment") else {}
    return {RECORD_PLURAL[kind]: [_record_out(ctx, kind, r, view, sources.get(str(r["id"]), "tico"), names, due=due)
                                  for r in shown], "next_cursor": nxt}


def record(c: sqlite3.Connection, viewer: str, *, kind: str, record_id: str, at: str | None = None) -> Row:
    """One location, item, project or piece of equipment, archived or not, by its id or an id it was merged under."""
    ctx = load(c, at)
    view = ctx.view(viewer)
    _teammate_or_refuse(ctx, viewer)
    row = RR.get(c, RECORD_TABLE[kind], record_id)
    if row is None:
        raise FinanceError(NOT_FOUND, f"no such {kind}")
    source = SM.record_providers(c, RECORD_TABLE[kind], [str(row["id"])]).get(str(row["id"]), "tico")
    due = RD.next_due_for(c, "equipment", [str(row["id"])]) if kind == "equipment" else {}
    return {kind: _record_out(ctx, kind, row, view, source, _customer_names(ctx, kind, [row]), due=due)}


def customer_extras(c: sqlite3.Connection, viewer: str, customer_id: str, at: str | None = None) -> Row:
    """What a customer panel adds beyond its contact, locations, estimates and invoices: the equipment the customer has,
    by name with each piece's next due day, and the live due dates on the contact, its locations and that equipment,
    as the viewer may read them (finance/due_dates._can_read), the next due first."""
    ctx = load(c, at)
    view = ctx.view(viewer)
    _teammate_or_refuse(ctx, viewer)
    equipment = RR.equipment_for_customer(c, customer_id)
    locations = RR.at_customer(c, customer_id)
    ids = [str(r["id"]) for r in equipment]
    sources = SM.record_providers(c, "equipment", ids)
    names = _customer_names(ctx, "equipment", equipment)
    due = RD.next_due_for(c, "equipment", ids)
    return {
        "equipment": [_record_out(ctx, "equipment", r, view, sources.get(str(r["id"]), "tico"), names, due=due)
                      for r in equipment],
        "due_dates": DD.customer_due_dates(ctx, viewer, contact_id=customer_id,
                                           location_ids=[str(r["id"]) for r in locations], equipment_ids=ids),
    }


def customers(c: sqlite3.Connection, viewer: str, *, q: str = "", cursor: str | None = None, limit: int = 50,
              at: str | None = None) -> Row:
    """Active customers by name then id, for the picker: id and name only, to every teammate. `q` matches a name,
    and an email too for a viewer of receivables; `next_cursor` pages."""
    ctx = load(c, at)
    _teammate_or_refuse(ctx, viewer)
    parts = tab.decode_cursor(cursor)
    after = (parts[0], parts[1]) if len(parts) == 2 else None
    most = tab.page_size(limit)
    by_email = tab_ap.sees_side(ctx, ctx.view(viewer), "invoice")
    rows = SAP.contacts_page(c, book_id=ctx.book.id, kinds=("customer", "both"), q=(q or "").strip()[:100],
                             active=True, after=after, most=most + 1, by_email=by_email)
    shown = rows[:most]
    nxt = tab.encode_cursor([str(shown[-1]["name"]), str(shown[-1]["id"])]) if len(rows) > most else None
    return {"customers": [{"id": str(r["id"]), "name": str(r["name"])} for r in shown], "next_cursor": nxt}


def customer_panel(c: sqlite3.Connection, viewer: str, contact_id: str, at: str | None = None) -> Row:
    """A customer's panel: the contact, the active locations it is the customer of, its open estimates (draft,
    sent or accepted) and its unpaid invoices. A teammate without the receivables view reads the contact by id and name and its
    locations; its estimates and invoices are null. A missing contact, or one that is not a customer, is not_found."""
    ctx = load(c, at)
    view = ctx.view(viewer)
    _teammate_or_refuse(ctx, viewer)
    row = SAP.get(c, "fin_contacts", contact_id)
    if row is None or str(row["book_id"]) != ctx.book.id or str(row["kind"]) not in ("customer", "both"):
        raise FinanceError(NOT_FOUND, "no such customer")
    locations = RR.at_customer(c, contact_id)
    sources = SM.record_providers(c, "locations", [str(r["id"]) for r in locations])
    names = _customer_names(ctx, "location", locations)
    out: Row = {
        "contact": {"id": str(row["id"]), "name": str(row["name"])},
        "locations": [_record_out(ctx, "location", r, view, sources.get(str(r["id"]), "tico"), names)
                      for r in locations],
        "estimates": None,
        "invoices": None,
    }
    if tab_ap.sees_side(ctx, view, "invoice"):
        out["contact"] = tab_ap.contact_row(ctx, row, R.is_admin(ctx.org, viewer, ctx.rights(viewer)))
        out["invoices"] = tab_ap.invoices(c, viewer, status="unpaid", customer_id=contact_id, at=at)
        if R.has(ctx.org, viewer, R.AR, ctx.rights(viewer)):
            shown = [e for status in ("draft", "sent", "accepted")
                     for e in estimates(c, viewer, status=status, customer_id=contact_id, at=at)["estimates"]]
            out["estimates"] = sorted(shown, key=lambda e: (str(e["date"]), str(e["id"])), reverse=True)
    return {**out, **customer_extras(c, viewer, contact_id, at)}


def search(c: sqlite3.Connection, viewer: str, *, kind: str | None, q: str = "",
           filters: Mapping[str, object] | None = None, limit: int = 50, cursor: str | None = None,
           at: str | None = None) -> Row:
    """Search entries' lines, feed rows, bills, invoices, contacts, rules, cards (§7.1) and items, locations and
    projects
    the viewer may see (records carry no amounts). With a `kind` it pages (`next_cursor`); without one it returns the
    first few of each kind it can show. Filters per kind: entry/feed: account_id, money_account_id, status (feed),
    start, end; bill/invoice: status, contact_id, start, end, aging; contact: kind, active; rule: kind (gate|posting);
    card: status, vendor (the cards saved on that vendor, so `vendor` answers "which of my cards works at X");
    po/estimate: status, contact_id, and a search over number, memo and contact name;
    item/location/project: active (1 keeps only active records); equipment: kind (owned|customer), customer_id,
    location_id, warranty_ends_by (a day: warranties ending on or before it), warranty (ending: within the window),
    due (soon|overdue), active."""
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
            elif k == "po":
                listed = orders(c, viewer, status=_filter(f, "status"), vendor_id=_filter(f, "contact_id"),
                                q=text, cursor=cursor, limit=most, at=at)
                results += [cast(Row, {**r, "label": f"{cast(Mapping[str, object], r['contact'])['name']} "
                                                     f"{r['number']}"}) for r in listed["orders"]]
                nxt = cast(str | None, listed["next_cursor"])
            elif k == "estimate":
                listed = estimates(c, viewer, status=_filter(f, "status"), customer_id=_filter(f, "contact_id"),
                                   q=text, cursor=cursor, limit=most, at=at)
                results += [cast(Row, {**r, "label": f"{cast(Mapping[str, object], r['contact'])['name']} "
                                                     f"{r['number']}"}) for r in listed["estimates"]]
                nxt = cast(str | None, listed["next_cursor"])
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
            elif k == "card":
                found = tab.cards(c, viewer, status=_filter(f, "status"), vendor=_filter(f, "vendor"), q=text,
                                  cursor=cursor, limit=most, at=at)
                results += [{**r, "label": f"{r['nickname'] or r['holder']} ••{r['last4'] or ''}".strip()}
                            for r in cast(list[Row], found["cards"])]
                nxt = cast(str | None, found["next"])
            elif k == "due_date":
                # Due dates page by name; `active=0` names the archived ones, the default is the live ones.
                listed = DD.listing(c, viewer, q=text, archived="only" if _filter(f, "active") in ("0", "false")
                                    else "hide", cursor=cursor, limit=most, at=at)
                results += [cast(Row, {**r, "kind": "due_date", "label": r["name"]})
                            for r in cast(list[Row], listed["due_dates"])]
                nxt = cast(str | None, listed["next_cursor"])
            elif k in RECORD_KINDS:
                _teammate_or_refuse(ctx, viewer)
                parts = tab.decode_cursor(cursor)
                active = _filter(f, "active")
                narrow = _narrow(ctx, k, f)
                rows = RR.search(c, RECORD_KINDS[k], text, active=None if active is None else
                                 active in ("1", "true", "True"), after=(parts[0], parts[1]) if len(parts) == 2
                                 else None, most=most, equal=narrow.equal, by=narrow.by, between=narrow.between,
                                 ids=narrow.ids)
                results += _record_rows(c, ctx, k, rows)
                nxt = tab.encode_cursor([str(rows[-1]["name"]), str(rows[-1]["id"])]) \
                    if rows and len(rows) == most else None
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


def inbox(c: sqlite3.Connection, viewer: str, *, limit: int = 50, cursor: str | None = None, kind: str | None = None,
          at: str | None = None) -> Row:
    """The viewer's Finance work queue (§7.1): uncategorized rows, bills to enter (drafts, e.g. from the bills
    address), unmatched and missing receipts, bills due within a week, overdue invoices, budgets near their limit,
    failed syncs and months ready to close. Each item has its ids and a short label; `counts` covers every kind
    (`more` when a kind has over 200). With `kind`, that kind alone, paged from its own query past the 200 (to
    INBOX_DEEP), so a reader that follows `next_cursor` reaches every item."""
    s = _open(c, viewer, at)
    if kind is not None:
        return _inbox_one(s, kind, limit, cursor)
    items: list[Row] = []
    counts: dict[str, int] = {}
    more: list[str] = []
    for name in INBOX_KINDS:
        got = _inbox_kind(s, name, INBOX_EACH)
        if got is None:
            continue
        rows, over = got
        counts[name] = len(rows)
        if over:
            more.append(name)
        items.extend({"kind": name, **r} for r in rows)
    offset = int((tab.decode_cursor(cursor) or ["0"])[0] or 0)
    most = tab.page_size(limit)
    page = items[offset:offset + most]
    nxt = tab.encode_cursor([str(offset + most)]) if offset + most < len(items) else None
    return {"items": page, "counts": counts, "more": more, "next_cursor": nxt, "currency": s.ctx.book.base_currency,
            "view_version": s.view.version}


INBOX_DEEP = 5000           # items of one kind an inbox page reaches into (`kind`); past it, `more` says so


def _inbox_one(s: _Seen, kind: str, limit: int, cursor: str | None) -> Row:
    if kind not in INBOX_KINDS:
        raise invalid(f"kind is {'|'.join(INBOX_KINDS)}")
    # The cursor is a raw position in the kind's own query (before the view's cuts), so an item a viewer cannot see
    # never shifts the next page past one they can: a page may hold fewer than `limit`, and `next_cursor` goes on.
    offset = min(max(0, int((tab.decode_cursor(cursor) or ["0"])[0] or 0)), INBOX_DEEP)
    most = tab.page_size(limit)
    end = min(offset + most, INBOX_DEEP)
    page, over = _inbox_kind(s, kind, end - offset, offset) or ([], False)
    nxt = tab.encode_cursor([str(end)]) if over and end < INBOX_DEEP else None
    return {"items": [{"kind": kind, **r} for r in page], "counts": {kind: len(page)},
            "more": [kind] if over and nxt is None else [], "next_cursor": nxt,
            "currency": s.ctx.book.base_currency, "view_version": s.view.version}


def _inbox_kind(s: _Seen, kind: str, n: int, skip: int = 0) -> tuple[list[Row], bool] | None:
    """One kind of the inbox: the items the viewer sees among its `n` after the first `skip` (raw positions in its own
    query, before the view's cuts), and whether there are more; None when the viewer has no such kind (no view of that
    side, or not the right)."""
    c, ctx, view = s.ctx.c, s.ctx, s.view
    today = dates.day(ctx.today)
    held = ctx.rights(s.view.viewer)
    cut = set(tab.private_cut(ctx, view))
    if kind == "uncategorized":
        rows, over = _uncategorized(s, today - timedelta(days=RECEIPT_DAYS), today, n, skip)
        return [{"id": str(r["id"]), "feed_id": str(r["id"]), "date": str(r["txn_date"]),
                 "amount": int(r["amount"]), "entry_id": r["entry_id"], "status": str(r["status"]),
                 "label": f"{r['counterparty'] or r['description'] or 'Row'} · {_money(int(r['amount']))}"}
                for r in rows], over
    if kind == "bill_to_enter":
        if not tab_ap.sees_side(ctx, view, "bill"):
            return None
        raw = SAP.drafts(c, ctx.book.id, skip + n + 1)
        drafts, _ = tab_ap.keep_docs(c, "bill", raw[skip:skip + n], cut)
        return [{"id": str(r["id"]), "bill_id": str(r["id"]), "date": str(r["bill_date"]),
                 "amount": int(r["total"]), "origin": str(r["origin"]),
                 "label": f"{r['contact_name'] or 'Bill'} {r['number']} · {_money(int(r['total']))}"
                 .replace("  ", " ")} for r in drafts], len(raw) > skip + n
    if kind == "unmatched_receipt":
        if not R.has(ctx.org, s.view.viewer, R.BOOK, held):
            return None
        # A receipt with no subject yet is the company's to see only with the whole books; else the viewer's own.
        files = SAP.unmatched_files(c, ctx.book.id, skip + n + 1, "" if view.everything else s.view.viewer)
        return [{"id": str(r["id"]), "file_id": str(r["file_id"]), "date": str(r["created_at"])[:10],
                 "label": "Receipt with nothing to attach to"} for r in files[skip:skip + n]], len(files) > skip + n
    if kind == "missing_receipt":
        money_ids, card_ids = tab.feed_scope(ctx, view)
        card_money = [i for i in money_ids if i in {str(m["id"]) for m in SK.money_accounts(c, ctx.book.id)
                                                    if str(m["kind"]) == "card_program"}]
        since = (today - timedelta(days=RECEIPT_DAYS)).isoformat()
        raw = SAP.card_rows_without_receipt(c, card_money, card_ids, since, skip + n + 1)
        gone = tab.private_feed(ctx, view, raw[skip:skip + n])
        return [{"id": str(r["id"]), "feed_id": str(r["id"]), "date": str(r["txn_date"]),
                 "amount": int(r["amount"]),
                 "label": f"{r['counterparty'] or 'Card charge'} · {_money(int(r['amount']))}"}
                for r in raw[skip:skip + n] if str(r["id"]) not in gone], len(raw) > skip + n
    if kind in ("bill_due", "invoice_overdue"):
        side = "bill" if kind == "bill_due" else "invoice"
        if not tab_ap.sees_side(ctx, view, side):
            return None
        docs, _ = tab_ap.keep_docs(c, side, SAP.open_docs(c, side, ctx.book.id), cut)
        if side == "bill":
            docs = [r for r in docs if r["due_date"] and AP.days_late(r["due_date"], today) >= -7]
        else:
            docs = [r for r in docs if AP.days_late(r["due_date"], today) > 0]
        return [{"id": str(r["id"]), f"{side}_id": str(r["id"]), "date": r["due_date"],
                 "amount": tab_ap.remaining(side, r), "days_late": AP.days_late(r["due_date"], today),
                 "label": f"{r['contact_name'] or side.title()} · {_money(tab_ap.remaining(side, r))} "
                          f"due {r['due_date']}"} for r in docs[skip:skip + n]], len(docs) > skip + n
    if kind == "budget_near_limit":
        near: list[Row] = []
        for b in _visible(s, False):
            st = _state_row(s, b, budgeting.state(ctx, b.id, today, read=budgeting.Netted(view)))
            if st["crossed_warn"]:
                near.append({"id": b.id, "budget_id": b.id, "amount": st["remaining"], "pct": st["pct"],
                             "excluded": st["excluded"], "label": f"{b.name} · {st['pct'] or '?'}% used"})
        return near[skip:skip + n], len(near) > skip + n
    if kind == "sync_failed":
        if not (view.everything or view.sources or R.is_admin(ctx.org, s.view.viewer, held)):
            return None
        failed: list[Row] = [{"id": str(x["id"]), "connection_id": str(x["id"]), "date": str(x["updated_at"] or "")[:10],
                   "label": f"{x['name'] or x['provider']}: {str(x['last_error'] or 'sync failed')[:120]}"}
                  for x in SK.connections(c) if str(x["status"]) == "error"]
        return failed[skip:skip + n], len(failed) > skip + n
    if not R.has(ctx.org, s.view.viewer, R.BOOK, held):      # period_ready
        return None
    ready: list[Row] = [{"id": p, "month": p, "label": f"{p} is ready to close"} for p in _closable(c, ctx)
             if _checklist(s, p)["ready"]]
    return ready[skip:skip + n], len(ready) > skip + n


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


# ------------------------------------------------------------------------------------------------ watch
WATCH_HISTORY_DAYS = 120       # posted debits read for duplicate charges and price increases
WATCH_PAGES = 10              # pages of 200 read per check of the debits


def _month_windows(today: date, n: int = 4) -> list[tuple[str, str, str]]:
    """The last n calendar months as (label, first day, last day), oldest first; the newest ends on the day itself."""
    out: list[tuple[str, str, str]] = []
    for back in range(n - 1, -1, -1):
        year, month = today.year, today.month - back
        while month <= 0:
            month, year = month + 12, year - 1
        first = date(year, month, 1)
        nxt = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
        out.append((first.strftime("%Y-%m"), first.isoformat(), (nxt - timedelta(days=1)).isoformat()))
    return out


def _charges(c: sqlite3.Connection, viewer: str, at: str) -> list[W.Charge]:
    """Posted debits of the last WATCH_HISTORY_DAYS as the viewer's view shows them (feed rows, not pending)."""
    today = date.fromisoformat(at[:10])
    start = (today - timedelta(days=WATCH_HISTORY_DAYS)).isoformat()
    out: list[W.Charge] = []
    cursor: str | None = None
    for _ in range(WATCH_PAGES):
        try:
            page = transactions(c, viewer, start=start, end=today.isoformat(), limit=200, cursor=cursor)
        except FinanceError:
            break
        for row in cast(list[Row], page.get("rows") or []):
            payee = str(row.get("counterparty") or "").strip().lower()
            amount = int(cast(int, row.get("amount") or 0))
            if row.get("kind") != "feed" or row.get("pending") or row.get("status") == "ignored" or amount >= 0 \
                    or not payee:
                continue
            out.append(W.Charge(id=str(row["id"]), payee=payee, amount=-amount,
                                day=date.fromisoformat(str(row["date"])[:10])))
        cursor = cast(str | None, page.get("next"))
        if not cursor:
            break
    return out


def watch_findings(c: sqlite3.Connection, viewer: str, at: str) -> list[dict[str, Any]]:
    """Every Watch check's findings now: runway, unusual spend per payee over the last four months, and the posted
    debits read for duplicate charges and price increases. Every read goes through the viewer's view."""
    today = date.fromisoformat(at[:10])
    found: list[dict[str, Any]] = []
    figures: Row = {}
    try:
        figures = metric(c, viewer, "runway_months", at=at)
    except FinanceError:
        pass
    value = figures.get("value")
    found += W.runway(float(cast(float, value)) if value is not None else None,
                      cast(int | None, figures.get("cash")))
    months = _month_windows(today)
    series: dict[str, list[int]] = {}
    for index, (_, first, last) in enumerate(months):
        rows: list[Row] = []
        try:
            rows = cast(list[Row], spend_by(c, viewer, "vendor", first, last, top_n=500, at=at)["rows"])
        except FinanceError:
            pass
        for row in rows:
            payee = str(row.get("label") or "")
            if not payee or row.get("key") in ("", NONE):
                continue
            series.setdefault(payee, [0] * len(months))[index] += int(cast(int, row.get("amount") or 0))
    found += W.unusual_spend(series, months[-1][0])
    charges = _charges(c, viewer, at)
    found += W.duplicate_charges(charges) + W.price_increases(charges)
    return found


def new_posting_rules(c: sqlite3.Connection, since: str) -> list[Row]:
    """Posting rules made since a time, newest first: the Bookkeeper's weekly digest (rules are company-wide)."""
    from .store import banking as SK
    return [{"id": str(r["id"]), "name": str(r["name"]), "created_by": str(r["created_by"] or ""),
             "active": bool(r["active"])} for r in SK.posting_rules_since(c, since)]


def view_version(c: sqlite3.Connection) -> int:
    """The counter a cache of any read keys on: it moves on every grant, revoke and privacy change."""
    return SA.view_version(c)



# The Finance tab's other reads (finance/tab.py).
balance_sheet = tab.balance_sheet
cards = tab.cards
sources = tab.sources
gateway_connected = ST.gateway_connected       # whether Sources shows the Gateway with no entries yet
access = tab.access
view_as = tab.view_as
# Money out and controls (finance/tab_money.py).
payments = tab_money.payments
transfers = tab_money.transfers
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
contact = tab_ap.contact
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


def raw_imports(c: sqlite3.Connection, viewer: str, *, limit: int = 50, at: str | None = None) -> Row:
    """The history imports (spec §4.1), newest first: each file's basis, name, months and rows, and how many of its
    months still stand. Only a viewer who sees everything gets the list: a month's raw totals are company-wide."""
    s = _open(c, viewer, at)
    if not s.view.everything:
        return {"imports": [], "more": False}
    most = max(1, min(int(limit), 200))
    found = SR.batches(c, s.ctx.book.id, most + 1)
    shown = [{"batch_id": str(r["id"]), "basis": str(r["basis"]), "source_name": str(r["source_name"]),
              "months": int(r["periods"]), "live_months": int(r["live_months"]),
              "live": int(r["live_months"]) == int(r["periods"]), "rows": int(r["row_count"]),
              "created_at": str(r["created_at"]), "created_by": str(r["created_by"])} for r in found[:most]]
    return {"imports": shown, "more": len(found) > most}


def action_for(c: sqlite3.Connection, viewer: str, action_id: str, at: str | None = None) -> dict[str, object] | None:
    """An action's status for `viewer`, read at `at` (default now): None unless they asked for it, are the one to decide
    its approval, or their current view covers what it is about. Its reason (which may carry amounts) for the approver,
    and otherwise only while the viewer's view covers that; else its code alone. A refused figure is stored without its
    amount and rendered here per reader (named_reason)."""
    row = SX.by_id(c, action_id)
    if row is None:
        return None
    ctx = load(c, at)
    try:
        payload = cast(Mapping[str, object], json.loads(str(row["payload_json"] or "{}")))
    except ValueError:
        payload = dict[str, object]()
    try:
        result = cast(Mapping[str, Any], json.loads(str(row["result_json"] or "{}")))
    except ValueError:
        result = dict[str, Any]()
    covers = True if ctx.view(viewer).everything else _covers(ctx, viewer, payload)
    asked = viewer in {str(row["actor"]), str(row["on_behalf_of"] or "")}
    approver = bool(row["approval_to"]) and viewer == str(row["approval_to"])     # who decides it, not who asked
    if not asked and not covers and not approver:
        return None
    shown = covers is True or approver or (covers is None and asked)
    return {"action_id": str(row["id"]), "kind": str(row["kind"]), "status": str(row["status"]),
            "code": str(row["code"] or ""), "reason": named_reason(ctx, viewer, str(row["reason"] or ""), result, decider=approver) if shown else "",
            "approval_id": str(row["approval_id"] or "") or None, "approver": str(row["approval_to"] or "") or None,
            "rule": str(row["rule_id"] or "")}
