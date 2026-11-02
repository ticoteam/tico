"""The one way a KPI or Finance value leaves the install.

`project` reads with the owner's Finance rights, because the owner is who decides, then keeps nothing but the fields of a
Stat. Runway's `cash` and `monthly_burn`, cash's `accounts`, a reading's evidence and notes, and every other field of a
Finance or KPI object stop at the helpers below: each reads the one number it needs by name and passes on only that.

Three things can keep a value back, in this order:
- The rule. No rule, or Hidden, means no stat.
- The delay clock. A period is eligible at its period end + N days, never its collected or created time, so a correction
  entered today for an old period shows at once and one for a period still inside its delay shows nothing.
- The derivation check. A stat is held, with the reason named to the owner, when what is shown recovers something the
  rules keep back. It works from the rules and the board, not the numbers, so it stays held on a day the numbers happen
  not to give it away. When it's unsure, it holds."""
import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal

from .. import hubdb as H
from .. import kpis as K
from ..store import Problem
from . import audiences as A
from . import disclosure as D
from . import settings as S

SOURCES = ("kpi", "finance_line", "finance_metric")
CHARTS = ("number", "line", "bars")
STATS_MAX = 200
LABEL_MAX = 80
DEFINITION_MAX = 200
SERIES_MAX = K.SPARK
FINANCE_MONTHS = 12
QUARTERLY_MONTHS = 24
QUARTER_DAYS = 92
# A metric's data class is fixed: cash can't become less sensitive by being filed under something else. Spend is the sum
# of the expense lines, so it answers to the lines' class.
METRIC_CLASS = {"revenue": "revenue", "spend": "pnl_line", "burn": "burn", "cash": "cash_runway",
                "runway_months": "cash_runway", "gross_margin": "gross_margin"}
METRIC_LABEL = {"revenue": "Revenue", "spend": "Spend", "burn": "Burn", "cash": "Cash", "runway_months": "Runway",
                "gross_margin": "Gross margin"}
AVAILABLE_MOST = 500
SAFE_UNIT = re.compile(r"[%$€£]|pp|[A-Za-z][A-Za-z /-]{0,19}")
SYMBOL = {"USD": "$", "EUR": "€", "GBP": "£"}

# Quantities the derivation check reasons about: what each is called and which cell rules it when no stat shows it.
QUANTITY = {"revenue": ("revenue", "revenue", ""), "growth": ("growth", "growth", ""),
            "gross_margin": ("gross margin", "gross_margin", ""), "burn": ("burn", "burn", ""),
            "cash": ("cash", "cash_runway", "cash"), "runway": ("runway", "cash_runway", "runway_months"),
            "spend": ("total spend", "pnl_line", "spend"),
            "cost_of_revenue": ("cost of revenue", "pnl_line", "cost_of_revenue")}
METRIC_QUANTITY = {"revenue": "revenue", "spend": "spend", "burn": "burn", "cash": "cash", "runway_months": "runway",
                   "gross_margin": "gross_margin"}
# Any two recover the third.
TRIPLES = (("cash", "runway", "burn"), ("gross_margin", "revenue", "cost_of_revenue"), ("burn", "revenue", "spend"))
# The level's differences between periods give the change, and an exact change sharpens a coarse level.
LEVELS = (("cash", "burn"), ("revenue", "growth"))
WORD_MONTHS = ("month", "months", "mo", "mos")


# ------------------------------------------------------------------------------------------------- time and shape
def _when(at):
    if at is None:
        at = H.now()
    if isinstance(at, datetime):
        return at if at.tzinfo else at.replace(tzinfo=timezone.utc)
    parsed = K._dt(K.utc(at))
    if not parsed:
        raise ValueError(f"not a time: {at!r}")
    return parsed


def _end_of(day):
    return datetime(day.year, day.month, day.day, 23, 59, 59, 999999, tzinfo=timezone.utc)


def _quarter(day):
    return (day.month - 1) // 3 + 1


def _quarter_end(day):
    last = _quarter(day) * 3
    return (date(day.year + (last == 12), last % 12 + 1, 1)) - timedelta(days=1)


def _month_label(day):
    return day.strftime("%b %Y")


def _day_label(day):
    return f"{day.strftime('%b')} {day.day}, {day.year}"


def _quarter_label(day):
    return f"Q{_quarter(day)} {day.year}"


@dataclass
class _Point:
    """A period's exact value. It goes no further than this module: _shape turns it into what may be shown."""
    end: datetime
    period: str
    value: float


def _number(value):
    """A whole number stays whole in JSON and floats don't show their binary tails."""
    d = Decimal(repr(float(value)))
    return int(d) if d == d.to_integral_value() else float(d)


def _singular(value, unit):
    """A plural unit drops its s at exactly one: "1 post", not "1 posts". "ss" words like "class" are not plurals."""
    if abs(value) == 1 and unit.endswith("s") and not unit.endswith("ss"):
        return unit[:-1]
    return unit


def _text(value, unit):
    text = K.amount(abs(value), _singular(value, unit))
    return "-" + text if value < 0 else text


def _step(values, r):
    """One grid for the whole series. A step that shrank with each value would keep small early periods finer than the
    rule says, and their differences with them."""
    if r.method == "nearest":
        return Decimal(r.n)
    largest = max((abs(v) for v in values), default=0)
    if not largest:
        return Decimal(1)
    return Decimal(10) ** (math.floor(math.log10(largest)) - (r.n or 2) + 1)


def _rounded(value, step):
    return float((Decimal(repr(float(value))) / step).quantize(Decimal(1), rounding=ROUND_HALF_UP) * step)


def _band(value, r):
    """The low and high of the band a value falls in, by size, sign kept apart so a band never straddles zero."""
    size = abs(Decimal(repr(float(value))))
    if r.method == "step":
        step = Decimal(r.n)
        low = (size / step).quantize(Decimal(1), rounding=ROUND_FLOOR) * step
        return float(low), float(low + step)
    power = math.floor(size.log10())
    lead = size / Decimal(10) ** power
    low, high = next((a, b) for a, b in ((1, 2), (2, 5), (5, 10)) if lead < b)
    scale = Decimal(10) ** power
    return float(low * scale), float(high * scale)


def _range_text(value, r, unit):
    if value == 0:
        return _text(0, unit)
    low, high = K.versus(*_band(value, r), unit)
    return f"{'-' if value < 0 else ''}{low}–{high}"


def _shape(points, rule, unit):
    """What a reader may get of each point: every value in the series takes the tile's form, or the sparkline would hand
    back what the tile rounded away. A Range has no number at all to subtract."""
    r = D.parse_rounding(rule["rounding"])
    if rule["mode"] == "rounded":
        step = _step([p.value for p in points], r)
        shaped = [(p, _number(_rounded(p.value, step))) for p in points]
        return [{"end": p.end, "period": p.period, "value": v, "display": "~" + _text(v, unit)} for p, v in shaped]
    if rule["mode"] == "range":
        return [{"end": p.end, "period": p.period, "value": None, "display": _range_text(p.value, r, unit)} for p in points]
    return [{"end": p.end, "period": p.period, "value": _number(p.value), "display": _text(p.value, unit)} for p in points]


def _exposes_exact(points, shaped):
    """Whether a Rounded series still gives exact values or exact differences: 100,000 then 200,000 rounds to itself,
    and 1,234 then 2,234 rounds to 1,000 and 2,000, a difference of exactly what it was."""
    if len(points) < 2:
        return False
    exact = [p.value for p in points]
    shown = [s["value"] for s in shaped]
    if all(math.isclose(a, b, abs_tol=1e-9) for a, b in zip(exact, shown, strict=True)):
        return True
    was = [b - a for a, b in zip(exact, exact[1:], strict=False)]
    now = [b - a for a, b in zip(shown, shown[1:], strict=False)]
    return any(was) and all(math.isclose(a, b, abs_tol=1e-9) for a, b in zip(was, now, strict=True))


# ------------------------------------------------------------------------------------------------- reading the values
def _safe_unit(unit):
    unit = str(unit or "").strip()
    return unit if SAFE_UNIT.fullmatch(unit) else ""


def _kpi_points(c, row, quarterly):
    """(points, unit, period days), or None when there's no KPI to read. Only the value and the business period of a
    reading go on. A replaced reading is dropped here by what the rows say, not by trusting a flag on the query."""
    record = K.kpi(c, row["ref_id"])
    if not record or record.get("archived_at"):
        return None
    rows = K.readings(c, row["ref_id"])
    replaced = {r.get("supersedes") for r in rows if r.get("supersedes")}
    live = [r for r in rows if not r.get("superseded_by") and r["id"] not in replaced and r.get("quality") != "partial"]
    newest = {}
    for r in sorted(live, key=lambda r: (r.get("collected_at") or "", r.get("created") or "")):
        ended = K._dt(r.get("period_end"))
        if ended:
            newest[ended] = float(r["value"])
    cadence = record.get("cadence")
    label = _month_label if cadence == "monthly" else _day_label
    points = [_Point(end, label(end.date()), value) for end, value in sorted(newest.items())]
    if quarterly:
        whole = {}
        for p in points:
            last = _quarter_end(p.end.date())
            whole[last] = _Point(_end_of(last), _quarter_label(last), p.value)
        points = [whole[k] for k in sorted(whole)]
    return points, _safe_unit(record.get("unit")), QUARTER_DAYS if quarterly else K.CADENCES.get(cadence, 7)


class _Books:
    """Finance read once per projection, as the owner, on the server. Callers get numbers by name; the rows and the
    metric objects never leave this class."""

    def __init__(self, c, at):
        self.c, self.at, self.iso = c, at, at.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
        self._pnl, self._metric = {}, {}
        self.viewer, self.ready = "", False
        self.accounts, self.private, self.unit, self.exponent = {}, {}, "", 2
        try:
            from ..finance import context as FC
            from ..finance.core import money
            from ..finance.store import access as SA
            ctx = FC.load(c, self.iso)
        except self._errors():
            return
        self.viewer = FC.owner(c)
        self.ready = bool(self.viewer)
        self.accounts, self.private = ctx.accounts, SA.private_lines(c)
        currency = ctx.book.base_currency
        self.unit, self.exponent = SYMBOL.get(currency, currency), money.EXPONENTS.get(currency, 2)

    @staticmethod
    def _errors():
        from ..finance.core.errors import FinanceError
        return FinanceError

    def _major(self, minor):
        """Whole currency units: cents on a tile would be false precision, and the exact cents are no one's business."""
        return float(round(Decimal(minor) / Decimal(10) ** self.exponent))

    def _read_pnl(self, basis, months):
        key = (basis, months)
        if key not in self._pnl:
            from ..finance import reads as FR
            last = date(self.at.year, self.at.month, 1) - timedelta(days=1)
            year, month = last.year, last.month - months + 1
            while month <= 0:
                year, month = year - 1, month + 12
            found = FR.pnl(self.c, self.viewer, date(year, month, 1).isoformat(), last.isoformat(), "month", basis, self.iso)
            self._pnl[key] = {"ends": [str(x["end"]) for x in found["columns"]], "income": list(found["income"]),
                              "expense": list(found["expense"]),
                              "rows": {str(r["account_id"]): (list(r["actual"] or []), str(r["section"]))
                                       for r in found["rows"]}}
        return self._pnl[key]

    def active_accounts(self):
        """P&L accounts with anything posted in the window: a part with nothing in it hides nothing."""
        read = self._read_pnl("accrual", FINANCE_MONTHS)
        return {a for a, (actual, _) in read["rows"].items() if any(actual)}

    def active_by_period(self, quarterly):
        """P&L accounts with anything posted in each disclosed period, in order: a month, or a whole quarter. A total is
        the sum of what is posted within its period, so the window's active accounts don't say what one period shows."""
        read = self._read_pnl("accrual", QUARTERLY_MONTHS if quarterly else FINANCE_MONTHS)
        return [{a for a, (actual, _) in read["rows"].items() if any(actual[i] for i in idx if i < len(actual))}
                for idx, _, _ in self._groups(read, quarterly)]

    def is_private(self, account_id):
        return account_id in self.private

    def _groups(self, read, quarterly):
        """[(column indexes, end day, label)]: each month, or each whole calendar quarter."""
        ends = [date.fromisoformat(e) for e in read["ends"]]
        if not quarterly:
            return [([i], e, _month_label(e)) for i, e in enumerate(ends)]
        quarters = {}
        for i, e in enumerate(ends):
            quarters.setdefault((e.year, _quarter(e)), []).append(i)
        return [(idx, ends[idx[-1]], _quarter_label(ends[idx[-1]])) for idx in quarters.values() if len(idx) == 3]

    def _flow(self, basis, quarterly, value):
        read = self._read_pnl(basis, QUARTERLY_MONTHS if quarterly else FINANCE_MONTHS)
        out = []
        for idx, end, label in self._groups(read, quarterly):
            number = value(read, idx)
            if number is not None:
                out.append(_Point(_end_of(end), label, number))
        return _without_leading_zeros(out)

    def line(self, account_id, quarterly):
        from ..finance.core.ledger import descendants
        under = descendants(self.accounts, account_id)

        def value(read, idx):
            return self._major(sum(sum(read["rows"].get(a, ([], ""))[0][i:i + 1] or [0]) for a in under for i in idx))
        return self._flow("accrual", quarterly, value), self.unit

    def _balance(self, name, end):
        """Only `value` of a metric: that dict also holds `accounts`, `cash` and `monthly_burn`, which stop here."""
        key = (name, end)
        if key not in self._metric:
            from ..finance import reads as FR
            found = FR.metric(self.c, self.viewer, name, at=_end_of(end).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z")
            self._metric[key] = found["value"]
        return self._metric[key]

    def bank_stale(self):
        """Whether the bank data behind a balance is out of date right now. The flag and nothing else."""
        from ..finance import reads as FR
        return bool(FR.metric(self.c, self.viewer, "cash", at=self.iso).get("stale"))

    def metric(self, name, quarterly):
        """(points, unit)."""
        if name in ("cash", "runway_months"):
            read = self._read_pnl("cash", QUARTERLY_MONTHS if quarterly else FINANCE_MONTHS)
            out = []
            for _, end, label in self._groups(read, quarterly):
                number = self._balance(name, end)
                if number is None:              # nothing burns: no runway to state, and never a zero
                    continue
                out.append(_Point(_end_of(end), label, self._major(number) if name == "cash" else float(number)))
            return _without_leading_zeros(out), self.unit if name == "cash" else "months"
        if name == "revenue":
            return self._flow("accrual", quarterly, lambda r, idx: self._major(sum(r["income"][i] for i in idx))), self.unit
        if name == "spend":
            return self._flow("accrual", quarterly, lambda r, idx: self._major(sum(r["expense"][i] for i in idx))), self.unit
        if name == "burn":
            return self._flow("cash", quarterly, lambda r, idx: self._major(
                sum(r["expense"][i] - r["income"][i] for i in idx))), self.unit

        def margin(read, idx):
            income = sum(read["income"][i] for i in idx)
            if income <= 0:
                return None
            cost = sum(actual[i] for actual, section in read["rows"].values() if section == "cost_of_revenue" and actual
                       for i in idx)
            return float(round(Decimal(income - cost) * 100 / Decimal(income), 1))
        return self._flow("accrual", quarterly, margin), "%"


def _without_leading_zeros(points):
    """Months before the books began aren't months of zero revenue: a missing number is never a zero."""
    for i, p in enumerate(points):
        if p.value:
            return points[i:]
    return []


# ------------------------------------------------------------------------------------------------- the rule for a stat
def _rule(rules, row):
    """The cell's own rule, then the KPI's or line's own row in the grid, then the class's. Specific beats general."""
    cls, ref = row["data_class"], row["ref_id"]
    own = {"kpi": "kpi", "finance_line": "pnl_line"}.get(row["source_kind"])
    found = rules.get((cls, ref)) or (rules.get((own, ref)) if own else None)
    return dict(found) if found and cls in D.DATA_CLASSES else D.lookup(rules, cls)


def _board(c):
    return H._rows(c.execute("SELECT * FROM page_stats ORDER BY position, created_at, id LIMIT ?", (STATS_MAX,)))


def _quantities(row):
    """Which of the check's quantities a stat is. A KPI filed under cash and runway could be either unless its unit says."""
    kind, ref, cls = row["source_kind"], row["ref_id"], row["data_class"]
    if kind == "finance_metric":
        return [METRIC_QUANTITY[ref]] if ref in METRIC_QUANTITY else []
    if kind == "finance_line":
        return ["line:" + ref]
    if cls in ("revenue", "growth", "gross_margin", "burn"):
        return [cls]
    if cls == "cash_runway":
        unit = _safe_unit((row.get("_unit") or "")).lower()
        if unit in WORD_MONTHS:
            return ["runway"]
        return ["cash"] if unit in ("$", "€", "£") else ["cash", "runway"]
    return []


# ------------------------------------------------------------------------------------------------- the derivation check
def _strictest(states):
    return min(states, key=lambda r: (D.RANK[r["mode"]], -r["lag_days"]))


def _gives(rule):
    """(exact?, lag) of what a shown value hands over."""
    return rule["mode"] in ("show", "delayed"), rule["lag_days"] if rule["mode"] == "delayed" else 0


def _recovers(target, inputs):
    """Whether these shown values, taken together, give the target away, given how it is kept back."""
    if target["mode"] == "show" or not inputs:
        return False
    if target["mode"] == "hidden":
        return True                                # even coarse inputs put a hidden value in plain view, roughly
    gives = [_gives(r) for r in inputs]
    if target["mode"] == "delayed":
        return max(lag for _, lag in gives) < target["lag_days"]    # known early, however roughly
    return all(exact for exact, _ in gives)        # coarse inputs say no more than the coarse value already does


class _Check:
    def __init__(self, c, rules, books, entries):
        self.c, self.rules, self.books = c, rules, books
        self.shown = {}                                 # quantity -> [(stat id, label, rule)], hidden rules left out
        self.held = {}
        for stat_id, label, rule, quantities in entries:
            for q in quantities:
                self.shown.setdefault(q, []).append((stat_id, label, rule))

    def state(self, q):
        """How q is kept back: the strictest of the stats showing it, else its own cell. A private Finance line is Hidden
        whatever the grid says."""
        if q.startswith("line:"):
            if self.books.is_private(q[5:]):
                return dict(D.HIDDEN)
            cell = D.lookup(self.rules, "pnl_line", q[5:])
        else:
            _, cls, ref = QUANTITY[q]
            cell = D.lookup(self.rules, cls, ref)
        rules = [r for _, _, r in self.shown.get(q, [])]
        return _strictest(rules) if rules else cell

    def best(self, q):
        """The most any stat gives of q."""
        return max((r for _, _, r in self.shown[q]), key=lambda r: (D.RANK[r["mode"]], -r["lag_days"]))

    def hold(self, quantities, reason):
        for q in quantities:
            for stat_id, _, _ in self.shown.get(q, []):
                self.held.setdefault(stat_id, reason)

    def _name(self, q):
        if q.startswith("line:"):
            return "a line"
        return QUANTITY[q][0]

    def _label(self, q):
        return self.shown[q][0][1]

    def same_number(self):
        """The same number on two stats is shown at the more open of their forms."""
        for q, entries in self.shown.items():
            strict = self.state(q)
            for stat_id, _, rule in entries:
                if (D.RANK[rule["mode"]], -rule["lag_days"]) > (D.RANK[strict["mode"]], -strict["lag_days"]):
                    self.held.setdefault(stat_id, f"Another stat keeps {self._name(q)} {strict['mode']}.")

    def triples(self):
        for triple in TRIPLES:
            for target in triple:
                a, b = [q for q in triple if q != target]
                if a in self.shown and b in self.shown and _recovers(self.state(target), [self.best(a), self.best(b)]):
                    self.hold((a, b), f"{self._label(a)} next to {self._label(b)} reveals {self._name(target)}, "
                                      f"which is {self.state(target)['mode']}.")

    def differences(self):
        for level, change in LEVELS:
            if level not in self.shown:
                continue
            if self.best(level)["mode"] != "range" and _recovers(self.state(change), [self.best(level)]):
                self.hold((level,), f"Differences between periods of {self._label(level)} reveal {self._name(change)}, "
                                    f"which is {self.state(change)['mode']}.")
            kept = self.state(level)
            if change in self.shown and kept["mode"] != "hidden" and _recovers(kept, [self.best(change)]):
                self.hold((change,), f"{self._label(change)} gives exact differences of {self._label(level)}, which "
                                     f"is {kept['mode']}.")

    def _private_alone_in_a_period(self, total, accounts):
        """Whether a period the total is shown for has a private account as its only active part, at the period lengths
        its shown stats use."""
        for quarterly in {D.parse_rounding(r["rounding"]).quarterly for _, _, r in self.shown[total]}:
            for active in self.books.active_by_period(quarterly):
                parts = active & accounts
                if len(parts) == 1 and self.books.is_private(next(iter(parts))):
                    return True
        return False

    def _sum(self, total, accounts, name):
        """A total and its parts, by account. A line stat covers everything under it in the chart. A total shown with no
        line beside it is the sum of its active accounts: when that is one private account, the total is its figure."""
        from ..finance.core.ledger import descendants
        lines = ["line:" + a for a in accounts if "line:" + a in self.shown and "line:" + a != total]
        if not lines:
            if total in self.shown and self._private_alone_in_a_period(total, accounts):
                self.hold((total,), f"{self._label(total)} is the only active line under {name} in a period shown, "
                                    f"which is private.")
            return
        covered = set().union(*(descendants(self.books.accounts, q[5:]) for q in lines))
        uncovered = [a for a in accounts if a not in covered and "line:" + a != total]
        kept = [self.state(q) for q in lines] + [self.state("line:" + a) for a in uncovered]
        secret = [r for r in kept if r["mode"] != "show"]
        if total in self.shown and secret:
            target = _strictest(secret)
            others = [self.best(q) for q in lines if self.state(q) != target]
            if _recovers(target, [self.best(total), *others]):
                self.hold((total, *lines), f"{self._label(total)} minus the lines shown reveals the other lines of "
                                           f"{name}, which are {target['mode']}.")
        if not uncovered and _recovers(self.state(total), [self.best(q) for q in lines]):
            self.hold(lines, f"The lines shown add up to {name}, which is {self.state(total)['mode']}.")

    def totals(self):
        """Exact total minus exact parts recovers what is left, even when several lines are left: their sum is news
        too. Parts come from what has been posted, so an unused account doesn't count as hidden. A total shown alone
        is checked too, against the accounts under it."""
        lines = [q for q in self.shown if q.startswith("line:")]
        totals = [q for q in ("revenue", "spend", "cost_of_revenue") if q in self.shown]
        if not self.books.ready or not (lines or totals):
            return
        from ..finance.core.ledger import descendants, pnl_section
        accounts = self.books.accounts
        try:
            active = self.books.active_accounts()
        except self.books._errors():
            self.hold(lines + totals, "The books couldn't be read to check what these lines reveal.")
            return
        income = {a for a in active if accounts[a].type == "income"}
        expense = {a for a in active if accounts[a].type == "expense"}
        cost = {a for a in expense if pnl_section(accounts, accounts[a]) == "cost_of_revenue"}
        self._sum("revenue", income, "revenue")
        self._sum("spend", expense, "total spend")
        self._sum("cost_of_revenue", cost, "cost of revenue")
        for q in lines:
            under = descendants(accounts, q[5:]) & active
            self._sum(q, under, "that line")

    def run(self):
        self.same_number()
        self.triples()
        self.differences()
        self.totals()
        return self.held


# ------------------------------------------------------------------------------------------------- building the board
def _stale(last_end, period_days, lag, at):
    """Out of date on the delay's clock: a Delayed stat isn't late for lagging by its own delay."""
    age = (at - timedelta(days=lag) - last_end).total_seconds() / 86400
    return age > period_days * (1 + K.GRACE)


def _read(c, books, row, quarterly):
    """(exact points, unit, period days, whether the bank data is stale), or None when it can't be read."""
    kind, ref = row["source_kind"], row["ref_id"]
    if kind == "kpi":
        found = _kpi_points(c, row, quarterly)
        return None if found is None else (*found, False)
    if not books.ready:
        return None
    period = QUARTER_DAYS if quarterly else K.CADENCES["monthly"]
    try:
        if kind == "finance_line":
            if ref not in books.accounts or books.accounts[ref].type not in ("income", "expense"):
                return None
            points, unit = books.line(ref, quarterly)
            return points, unit, period, False
        if ref not in METRIC_CLASS:
            return None
        points, unit = books.metric(ref, quarterly)
        return points, unit, period, ref in ("cash", "runway_months") and bool(points) and books.bank_stale()
    except books._errors():
        return None


def _build(c, audience_id, at):
    """Everything about the board for one audience. Exact values stay in `points`, which only this module reads."""
    at = _when(at)
    rules = D.all_rules(c, audience_id)
    books = _Books(c, at)
    entries, built, held, absent = [], [], {}, []
    for row in _board(c):
        rule = _rule(rules, row)
        if row["source_kind"] == "kpi":
            record = K.kpi(c, row["ref_id"])
            row = {**row, "_unit": (record or {}).get("unit")}
        if rule["mode"] == "hidden":
            absent.append((row, "Hidden"))
            continue
        if row["source_kind"] == "finance_line" and books.is_private(row["ref_id"]):
            held[row["id"]] = "This line is private in Finance."    # payroll: never on a page, whatever the grid says
            continue
        entries.append((row["id"], row.get("label") or "", rule, _quantities(row)))
        quarterly = D.parse_rounding(rule["rounding"]).quarterly
        found = _read(c, books, row, quarterly)
        if found is None:
            absent.append((row, "No data"))
            continue
        points, unit, period, bank_stale = found
        # The one place the delay clock runs. A Show rule has lag 0, so a period still under way isn't shown either.
        lag = rule["lag_days"] if rule["mode"] == "delayed" else 0
        points = [p for p in points if p.end + timedelta(days=lag) <= at][-SERIES_MAX:]
        if not points:
            absent.append((row, "Nothing eligible yet"))
            continue
        shaped = _shape(points, rule, unit)
        if rule["mode"] == "rounded" and _exposes_exact(points, shaped):
            held[row["id"]] = "Rounding leaves these values or their differences exact. Use a coarser rounding."
        built.append({"row": row, "rule": rule, "shaped": shaped,
                      "stale": bank_stale or _stale(points[-1].end, period, lag, at)})
    for stat_id, reason in _Check(c, rules, books, entries).run().items():
        held.setdefault(stat_id, reason)
    if audience_id != "public":
        for stat_id, reason in _with_public(c, at, books, rules, entries).items():
            held.setdefault(stat_id, reason)
    return built, held, absent


def _with_public(c, at, books, rules, entries):
    """A private board's readers also read the Public page, so what that page shows is known to them too. The two are
    checked together: each stat takes the more revealing of its two rules, and a stat the Public page already shows is
    not held here. Held results apply to this board only; the Public page's own check is unchanged."""
    row = c.execute("SELECT enabled FROM audiences WHERE kind='public'").fetchone()
    if not row or not row[0] or not board_approved(c, "public"):
        return {}
    pub_built, pub_held, _ = _build(c, "public", at)
    shown = {b["row"]["id"]: b for b in pub_built if b["row"]["id"] not in pub_held}
    if not shown:
        return {}
    merged = {stat_id: [label, rule, quantities] for stat_id, label, rule, quantities in entries}
    for stat_id, b in shown.items():
        if stat_id in merged:
            label, rule, quantities = merged[stat_id]
            if _revealing(b["rule"]) > _revealing(rule):
                rule = b["rule"]
            merged[stat_id] = [label, rule, quantities]
        else:
            merged[stat_id] = [b["row"].get("label") or "", b["rule"], _quantities(b["row"])]
    union = [(stat_id, label, rule, quantities) for stat_id, (label, rule, quantities) in merged.items()]
    private = {stat_id: rule for stat_id, _, rule, _ in entries}
    # Public showing a stat exempts it only at least as exactly as this board does: a coarser Public form doesn't cover
    # the finer private one.
    exempt = {stat_id for stat_id, b in shown.items()
              if stat_id not in private or _revealing(b["rule"]) >= _revealing(private[stat_id])}
    return {stat_id: reason for stat_id, reason in _Check(c, rules, books, union).run().items()
            if stat_id in private and stat_id not in exempt}


def _revealing(rule):
    """How exact a rule's form is, for comparing two forms of the same stat: a higher value is more revealing."""
    return D.RANK[rule["mode"]], -rule["lag_days"]


def _stat(row, shaped, stale):
    """A Stat, field by field. The keys here are the view model's; nothing else can get through."""
    last = shaped[-1]
    return {"id": row["id"], "label": row.get("label") or "", "definition": row.get("definition") or "",
            "chart": row["chart"], "period": last["period"], "as_of": last["end"].date().isoformat(),
            "display": last["display"], "stale": bool(stale),
            "series": [{"period": s["period"], "value": s["value"], "display": s["display"]} for s in shaped]}


def _held_list(c, held):
    labels = {r["id"]: r.get("label") or "" for r in _board(c)}
    return [{"id": i, "label": labels.get(i, ""), "reason": reason} for i, reason in held.items()]


def project(c, audience_id, at=None):
    """{"stats": [Stat], "held": [{"id", "label", "reason"}]}. Stats are those the rules allow and nothing recovers;
    `held` goes to the owner, never to a reader. Whether the board is approved is board_approved's question."""
    built, held, _ = _build(c, audience_id, at)
    return {"stats": [_stat(b["row"], b["shaped"], b["stale"]) for b in built if b["row"]["id"] not in held],
            "held": _held_list(c, held)}


def board(c, audience_id, at=None):
    """For the owner's grid: the projection, plus each stat that's absent and why."""
    built, held, absent = _build(c, audience_id, at)
    return {"stats": [_stat(b["row"], b["shaped"], b["stale"]) for b in built if b["row"]["id"] not in held],
            "held": _held_list(c, held),
            "absent": [{"id": row["id"], "label": row.get("label") or "", "reason": why} for row, why in absent],
            "approved": board_approved(c, audience_id)}


def facts(c, audience_id, period_start, period_end, at=None):
    """The filtered structured facts an edition for this period may use: each Stat cut to the period, and which classes
    this audience may read about at all. Held stats are left out entirely, labels included: the model's prompt and the
    leak check's allowed words both come from here, so a held label would otherwise pass as if it were shown."""
    start, end = _when(period_start), _when(K.utc(period_end, end=True))
    built, held, _ = _build(c, audience_id, at)
    stats = []
    for b in built:
        if b["row"]["id"] in held:
            continue
        inside = [s for s in b["shaped"] if start <= s["end"] <= end]
        if inside:
            stats.append(_stat(b["row"], inside, b["stale"]))
    rules = D.all_rules(c, audience_id)
    return {"period_start": str(period_start), "period_end": str(period_end), "stats": stats,
            "classes": {cls: D.lookup(rules, cls)["mode"] for cls in D.DATA_CLASSES}}


RECENT_PERIODS = 12


def unseen_values(c, audience_id, at=None):
    """The recent exact values of every board stat, private Finance lines included, that this audience does not see
    exactly in its own projection: Hidden, held, delayed, or rounded to something else. The leak check flags an edition's
    figure that equals one, even when a source says it. Only the numbers leave here: they never go into the model's
    prompt or a check's detail."""
    at = _when(at)
    built, held, _ = _build(c, audience_id, at)
    seen = {float(s["value"]) for b in built if b["row"]["id"] not in held for s in b["shaped"] if s["value"] is not None}
    rules = D.all_rules(c, audience_id)
    books = _Books(c, at)
    values = set()
    for row in _board(c):
        rule = _rule(rules, row)
        if row["source_kind"] == "kpi":
            row = {**row, "_unit": (K.kpi(c, row["ref_id"]) or {}).get("unit")}
        found = _read(c, books, row, D.parse_rounding(rule["rounding"]).quarterly)
        if found:
            values.update(float(p.value) for p in found[0][-RECENT_PERIODS:])
    return values - seen


# ------------------------------------------------------------------------------------------------- the board's approval
def board_terms(c, audience_id):
    """Each stat the board's approval covers, in the board's order: what it is and how this audience gets it. Hidden stats
    are not here. The approval's hash and the text an approver reads are both made from this list, so they can't differ."""
    rules = D.all_rules(c, audience_id)
    terms = []
    for row in _board(c):
        rule = _rule(rules, row)
        if rule["mode"] == "hidden":
            continue
        record = K.kpi(c, row["ref_id"]) if row["source_kind"] == "kpi" else None
        terms.append({"id": row["id"], "source": row["source_kind"], "ref": row["ref_id"], "class": row["data_class"],
                      "label": row.get("label") or "", "definition": row.get("definition") or "", "chart": row["chart"],
                      "unit": (record or {}).get("unit"), "version": (record or {}).get("definition_version"),
                      "mode": rule["mode"], "lag_days": rule["lag_days"], "rounding": rule["rounding"]})
    return terms


def board_config_hash(c, audience_id):
    """What an approval of the stats board covers: which stats this audience sees, in which form and order, with what
    words, how each is delayed or rounded, under which disclosure revision. Values aren't in it: they refresh daily with no
    one asked. A KPI's unit and definition version are, because a bot can change those and the number then means
    something else."""
    body = {"audience": audience_id, "revision": D.current_revision(c, audience_id), "stats": board_terms(c, audience_id)}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def _latest_approval(c, audience_id):
    return H._one(c, "SELECT * FROM stat_board_approvals WHERE audience_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                  (audience_id,))


def board_approved(c, audience_id):
    """Only the newest approval counts, and only for exactly this board. Track B writes the rows."""
    row = _latest_approval(c, audience_id)
    return bool(row) and row["config_hash"] == board_config_hash(c, audience_id)


def carry_board_approval(c, audience_id):
    """Keeps an approval standing through a change that only takes away, which applies at once and needs no new click.
    The caller checked board_approved before making the change."""
    row = _latest_approval(c, audience_id)
    if not row:
        return
    c.execute("INSERT INTO stat_board_approvals (id, audience_id, config_hash, disclosure_revision_id, approval_id, "
              "approved_by, created_at) VALUES (?,?,?,?,?,?,?)",
              (H.new_id(), audience_id, board_config_hash(c, audience_id), D.current_revision(c, audience_id),
               row["approval_id"], row.get("approved_by"), H.now()))


# ------------------------------------------------------------------------------------------------- the board's stats
def list_stats(c, limit=STATS_MAX, offset=0):
    return H._rows(c.execute("SELECT * FROM page_stats ORDER BY position, created_at, id LIMIT ? OFFSET ?", (limit, offset)))


def _words(value, limit, what):
    text = " ".join(str(value or "").split())
    if len(text) > limit:
        raise Problem(what, f"Keep the {what} under {limit} characters", 422)
    return text


def _source_defaults(c, source_kind, ref_id):
    """The data class and starting words. They're copied, not linked: a bot that renames its KPI can't change what a
    page says, and the board hash covers the copy."""
    if source_kind == "kpi":
        record = K.kpi(c, ref_id)
        if not record:
            raise Problem("ref_id", "No such KPI", 422)
        return "kpi", record.get("name") or "", (str(record.get("definition") or "").split("\n") or [""])[0]
    if source_kind == "finance_metric":
        if ref_id not in METRIC_CLASS:
            raise Problem("ref_id", f"A metric is {', '.join(METRIC_CLASS)}", 422)
        return METRIC_CLASS[ref_id], METRIC_LABEL[ref_id], ""
    if source_kind == "finance_line":
        row = H._one(c, "SELECT name, type FROM fin_accounts WHERE id=?", (ref_id,))
        if not row or row["type"] not in ("income", "expense"):
            raise Problem("ref_id", "A line is an income or expense account", 422)
        return "pnl_line", row["name"], ""
    raise Problem("source_kind", f"A source is {', '.join(SOURCES)}", 422)


def available(c):
    """What an owner can put on the board, by name and never with a value. Each kind is capped on its own, so a long list
    of KPIs can't crowd out Finance. A private Finance line is left out: it can never show, and its name is private too."""
    from ..finance.store import access as SA
    private = SA.private_lines(c)
    metrics = [{"source_kind": "finance_metric", "ref_id": ref, "label": label} for ref, label in METRIC_LABEL.items()]
    kpis = [{"source_kind": "kpi", "ref_id": r["id"], "label": r["name"]} for r in c.execute(
        "SELECT id, name FROM kpis WHERE archived_at IS NULL ORDER BY lower(name), id LIMIT ?", (AVAILABLE_MOST,))]
    lines = [{"source_kind": "finance_line", "ref_id": r["id"], "label": r["name"]} for r in c.execute(
        "SELECT id, name FROM fin_accounts WHERE type IN ('income','expense') AND active=1 ORDER BY code, id LIMIT ?",
        (AVAILABLE_MOST,)) if r["id"] not in private]
    return kpis + metrics + lines


def _approved_audiences(c):
    return [a["id"] for a in A.list_all(c) if board_approved(c, a["id"])]


def create_stat(c, actor, source_kind, ref_id, data_class=None, label=None, definition=None, chart="number", position=None):
    S.require_person(c, actor)
    ref = str(ref_id or "").strip()
    cls, default_label, default_definition = _source_defaults(c, source_kind, ref)
    # A KPI's owner says what it measures, so a person files it; Finance's own numbers have a fixed class.
    if source_kind == "kpi" and data_class:
        if data_class not in D.DATA_CLASSES:
            raise Problem("data_class", "Unknown data class", 422)
        cls = data_class
    if chart not in CHARTS:
        raise Problem("chart", f"A chart is {', '.join(CHARTS)}", 422)
    if c.execute("SELECT count(*) FROM page_stats").fetchone()[0] >= STATS_MAX:
        raise Problem("stats", f"A page holds {STATS_MAX} stats", 422)
    if c.execute("SELECT 1 FROM page_stats WHERE source_kind=? AND ref_id=?", (source_kind, ref)).fetchone():
        raise Problem("stats", "That stat is already on the page", 409)
    if position is None:
        position = c.execute("SELECT coalesce(max(position), -1) + 1 FROM page_stats").fetchone()[0]
    stat_id = H.new_id()
    c.execute("INSERT INTO page_stats (id, source_kind, ref_id, data_class, label, definition, chart, position, created_at) "
              "VALUES (?,?,?,?,?,?,?,?,?)",
              (stat_id, source_kind, ref, cls, _words(default_label if label is None else label, LABEL_MAX, "label"),
               _words((default_definition if definition is None else definition)[:DEFINITION_MAX], DEFINITION_MAX, "definition"),
               chart, int(position), H.now()))
    H.event(c, actor, "company_page.stat_add", stat_id, {"source": source_kind})
    S.rebuild_if_live(c, "stats changed")
    return H._one(c, "SELECT * FROM page_stats WHERE id=?", (stat_id,))


def update_stat(c, actor, stat_id, **fields):
    """Words, chart and order are what an approval covers, so changing any of them asks again."""
    S.require_person(c, actor)
    row = H._one(c, "SELECT * FROM page_stats WHERE id=?", (stat_id,))
    if not row:
        raise Problem("not_found", "No such stat", 404)
    values = {}
    for name, value in fields.items():
        if name == "label":
            values[name] = _words(value, LABEL_MAX, "label")
        elif name == "definition":
            values[name] = _words(value, DEFINITION_MAX, "definition")
        elif name == "chart":
            if value not in CHARTS:
                raise Problem("chart", f"A chart is {', '.join(CHARTS)}", 422)
            values[name] = value
        elif name == "position":
            values[name] = int(value)
        elif name == "data_class" and row["source_kind"] == "kpi":
            if value not in D.DATA_CLASSES:
                raise Problem("data_class", "Unknown data class", 422)
            values[name] = value
        else:
            raise Problem("fields", f"Can't change {name}", 422)
    if values:
        names = list(values)
        c.execute(f"UPDATE page_stats SET {', '.join(f'{n}=?' for n in names)} WHERE id=?", (*values.values(), stat_id))
        H.event(c, actor, "company_page.stat_update", stat_id, {"fields": sorted(names)})
        S.rebuild_if_live(c, "stats changed")
    return H._one(c, "SELECT * FROM page_stats WHERE id=?", (stat_id,))


def delete_stat(c, actor, stat_id):
    """Taking a stat off applies at once and asks no one: each board that was approved stays approved without it."""
    S.require_person(c, actor)
    approved = _approved_audiences(c)
    if not c.execute("DELETE FROM page_stats WHERE id=?", (stat_id,)).rowcount:
        raise Problem("not_found", "No such stat", 404)
    for audience_id in approved:
        carry_board_approval(c, audience_id)
    H.event(c, actor, "company_page.stat_delete", stat_id, {})
    S.rebuild_if_live(c, "stats changed")
    return {"deleted": stat_id}
