# pyright: strict
"""Growth core: three primitives (cohort rows, costs, a segment's value kind) and the one read every Growth number comes
from (docs/growth.md, "The core").

Cohort rows (`growth_cohort_rows`). One row is the people in one cohort cell, month grain: the `source` that pushed it,
the `anchor` (`signup`: people who signed up that month; `customer`: people who reached the segment's customer step that
month), `period` (YYYY-MM), `segment` and `channel` slugs ('' is No segment and Unknown), `campaign` ('' is none; at
most 10 per source and channel, the rest `other`), `as_of` (the date the counts were true), `entered`, and per step or
window a count with its eligible count: `activated`, `paid`, `kept_d30`, `kept_d90`, `kept_d180` (kept by the segment's
own definition), and `net_d90_sum`, `net_d180_sum`, `net_d365_sum` (summed net revenue at that age) with the people
that old. Counts are whole numbers, `x <= x_eligible <= entered`; blank is not supplied (NULL), never 0. A push replaces
every row of its source for the periods it covers: cohorts restate as they age.

Customer facts (backend/growth_customers.py) fold into the same shape on read (`fold`, never stored): customer-anchor
rows under their own source, kept by the segment's `churn_days` and net revenue by age, eligible as
backend/growth_computed.py has it (a window counts once the whole month is old enough).

Costs (`growth_costs`): source, period, channel ('' is company-wide), campaign, `kind` (`money`, `promo_cash`,
`promo_credits` at real cost at redemption, `bot`, `hours` in money at the company's rate), `purpose` (`acquisition` or
`retention`), amount and quality. A push replaces its source's rows for the periods it covers. The typed spend and bot
cost of `growth_cohorts` (Finance's ad spend syncs there too) read as `money` and `bot` acquisition costs, source `typed`.

The read (`read`): sums over cohort rows plus costs for a scope (`all`, `mine`: the channels the caller owns,
`segment:<slug>`, `channel:<slug>`) and a month. Each channel carries its owner, monthly budget and cost over budget;
`metrics` lists Growth's KPIs (a channel's input or guardrail KPI, or the Growth Manager's) with their latest reading.
Headline CAC is money and promotions for acquisition over new customers; `with_effort` adds bot cost and hours; with no
money cost CAC is null with status `no_spend`, never 0. A segment's CAC is its own channels' costs; `cac_loaded` adds
company-wide costs by its share of the month's new customers who have a segment (as backend/growth_computed.py). Rates
come from the latest cohort whose eligible count reaches the minimum, and say which cohort. A `retention` segment reads
a `value` block (kept at day 30 and cost per retained customer) in place of LTV, LTV:CAC and payback.

Who may: everyone signed in reads; the owner, admins and the Growth Manager push (backend/growth_setup.py editor).
"""

import csv
import io
import math
import re
import sqlite3
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, NoReturn, cast

from fastapi import Request

from . import growth as _growth_untyped
from . import growth_computed as GX
from . import growth_setup as _setup_untyped
from . import hubdb as _hubdb_untyped
from . import kpis as _kpis_untyped
from . import models as M
from . import page_owners
from .store import Problem

G: Any = _growth_untyped
H: Any = _hubdb_untyped
K: Any = _kpis_untyped
may_edit = cast("Callable[[Any, Any, Any], bool]", _setup_untyped.editor)
Row = dict[str, Any]

ANCHORS = ("signup", "customer")
STEPS = ("activated", "paid", "kept_d30", "kept_d90", "kept_d180")  # each with its `_eligible`
NETS = ("net_d90", "net_d180", "net_d365")  # each as `_sum` with its `_eligible`
WINDOWS = (30, 90, 180)
AGES = (90, 180, 365)
KINDS = ("money", "promo_cash", "promo_credits", "bot", "hours")
MONEY = ("money", "promo_cash", "promo_credits")  # what headline CAC is
EFFORT = ("bot", "hours")  # what `with_effort` adds
PURPOSES = ("acquisition", "retention")
QUALITIES = ("actual", "estimate")
DETAILS = ("campaigns", "retention", "series")
ROW_COLUMNS = (
    "anchor",
    "period",
    "segment",
    "channel",
    "campaign",
    "as_of",
    "entered",
    *(f"{s}{e}" for s in STEPS for e in ("", "_eligible")),
    *(f"{n}{e}" for n in NETS for e in ("_sum", "_eligible")),
)
COST_COLUMNS = ("period", "channel", "campaign", "kind", "purpose", "amount", "quality")
ROW_REQUIRED = ("anchor", "period", "as_of", "entered")
COST_REQUIRED = ("period", "kind", "amount")
ROW_KEY = ("anchor", "period", "segment", "channel", "campaign")
COST_KEY = ("period", "channel", "campaign", "kind", "purpose")
TYPED = "typed"  # the source growth_cohorts' typed spend and bot cost read under
CAMPAIGNS_MAX, OTHER = 10, "other"
LABEL_MAX = 40
ROWS_MAX = 20_000
ROW_NUMBERS_SHOWN = 3
PROBLEMS_SHOWN = 3
COUNT_FLOOR = 5  # a count's verdict needs this many
FLAT = 0.10  # within ±10% of the trailing 3-month average is flat
STALE_DAYS = 45
SERIES_MONTHS, RETENTION_COHORTS = 12, 6
METRICS_MAX = 20
MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ----------------------------------------------------------------------------- months
def shift(month: str, back: int) -> str:
    """The month `back` months before `month` (YYYY-MM)."""
    n = int(month[:4]) * 12 + int(month[5:7]) - 1 - back
    return f"{n // 12:04d}-{n % 12 + 1:02d}"


def last_full_month(on: date) -> str:
    return (on.replace(day=1) - timedelta(days=1)).isoformat()[:7]


# ----------------------------------------------------------------------------- customer facts, folded
def fold(facts: Iterable[GX.Fact], segs: GX.Segments, channels: set[str], on: date, as_of: dict[str, str]) -> list[Row]:
    """Customer facts as customer-anchor cohort rows, one per (source, month, segment, channel), as of `on`. Pure.

    A kept window counts once the whole month is that old plus the segment's churn_days (growth_computed's eligibility):
    until then it is 0 of 0 eligible. Net revenue at an age sums the customers that old who carry it; a window no fact
    carries anywhere is not supplied."""
    cells: dict[tuple[str, str, str, str], list[GX.Fact]] = {}
    for f in facts:
        seg = segs.slug(f)
        key = (
            f.source,
            f.became.isoformat()[:7],
            "" if seg == GX.NO_SEGMENT else seg,
            f.channel if f.channel in channels else "",
        )
        cells.setdefault(key, []).append(f)
    carried = {days: any(f.net(days) is not None for group in cells.values() for f in group) for days in AGES}
    out: list[Row] = []
    for (source, month, segment, channel), group in cells.items():
        end = GX.period(group[0].became, "month")[2]
        wait = int((segs.rows.get(segment) or {}).get("churn_days") or 0)
        row: Row = {
            **{c: None for c in ROW_COLUMNS},
            "source": source,
            "anchor": "customer",
            "period": month,
            "segment": segment,
            "channel": channel,
            "campaign": "",
            "as_of": as_of.get(source) or on.isoformat(),
            "entered": len(group),
        }
        for window in WINDOWS:
            old = on >= end + timedelta(days=window + wait)
            kept = sum(1 for f in group if f.churned is None or f.churned > f.became + timedelta(days=window))
            row[f"kept_d{window}"], row[f"kept_d{window}_eligible"] = (kept, len(group)) if old else (0, 0)
        for days in AGES:
            if not carried[days]:
                continue
            values = [
                v for f in group if on >= f.became + timedelta(days=days) for v in (f.net(days),) if v is not None
            ]
            row[f"net_d{days}_sum"], row[f"net_d{days}_eligible"] = round(sum(values), 2), len(values)
        out.append(row)
    return out


# ----------------------------------------------------------------------------- pushes
def refuse(rule: str, detail: str, **more: Any) -> NoReturn:
    """A refusal that names its rule; `detail` names columns and row numbers, never a value."""
    raise Problem("validation", detail, 422, extra={"rule": rule, **more})


def _rows_said(rows: list[int]) -> str:
    shown = ", ".join(str(n) for n in rows[:ROW_NUMBERS_SHOWN])
    more = len(rows) - ROW_NUMBERS_SHOWN
    return ("row " if len(rows) == 1 else "rows ") + shown + (f" and {more} more" if more > 0 else "")


def _header(name: Any) -> str:
    return re.sub(r"[\s\-]+", "_", str(name or "").strip().lower())


def table(body: M.GrowthCorePush) -> tuple[list[str], list[list[Any]], int]:
    """(headers, rows, the first row's number) from CSV text (row 1 is the header) or rows as objects (row 1 is the
    first object)."""
    if body.csv is not None:
        try:
            found = [r for r in csv.reader(io.StringIO(body.csv.lstrip("﻿"))) if any(cell.strip() for cell in r)]
        except csv.Error:
            refuse("csv", "Nothing pushed: that is not a CSV Growth can read; check the quoting.")
        if not found:
            refuse("empty", "Nothing pushed: the CSV has no header.")
        return [_header(h) for h in found[0]], [list[Any](r) for r in found[1:]], 2
    objects = body.rows or []
    headers = list(dict.fromkeys(k for r in objects for k in r))
    return [_header(h) for h in headers], [[r.get(h) for h in headers] for r in objects], 1


def _columns(headers: list[str], allowed: Sequence[str], required: Sequence[str]) -> None:
    refused = [h[:LABEL_MAX] or "(blank)" for i, h in enumerate(headers) if h not in allowed or h in headers[:i]]
    if refused:
        refuse(
            "columns",
            f"Nothing pushed. The columns are: {', '.join(allowed)}. Remove these and push again: {', '.join(refused)}.",
        )
    missing = [c for c in required if c not in headers]
    if missing:
        refuse("columns", f"Nothing pushed. These columns are required: {', '.join(missing)}.")


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else "" if value is None else str(value)


def _count(value: Any) -> int | None:
    """A whole number of zero or more; None for blank; ValueError otherwise."""
    if _text(value) == "":
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0 or number != int(number):
        raise ValueError
    return int(number)


def _amount(value: Any) -> float | None:
    if _text(value) == "":
        return None
    number = float(value)
    if not math.isfinite(number):
        raise ValueError
    return number


def _lookup(c: sqlite3.Connection, table_name: str) -> dict[str, str]:
    """Each slug and name, lowercased, to its slug (retired ones too: a past month may carry one)."""
    found: dict[str, str] = {}
    for slug, name in c.execute(f"SELECT slug, name FROM {table_name}"):
        found.setdefault(str(name).strip().lower(), str(slug))
    found.update({str(slug): str(slug) for slug, _ in c.execute(f"SELECT slug, name FROM {table_name}")})
    return found


class _Problems:
    """Problems by (rule, column), each with its row numbers; reported together, values never."""

    SAID = {
        "blank": "is blank",
        "anchor": "is signup or customer",
        "month": "is not a month like 2026-10",
        "date": "is not a date like 2026-11-18",
        "count": "is not a whole number of zero or more",
        "number": "is not a number",
        "amount": "is not an amount of zero or more",
        "segment": "names no segment (tico growth segment list; blank is No segment)",
        "channel": "names no channel (tico growth channel list; blank is Unknown or company-wide)",
        "long_label": f"is longer than {LABEL_MAX} characters",
        "pair": "is given without its eligible count, or the other way round",
        "over": "is more than its eligible count, or the eligible count is more than entered",
        "kind": "is " + ", ".join(KINDS),
        "purpose": "is acquisition or retention",
        "quality": "is actual or estimate",
        "duplicate": "repeats a row's key",
    }

    def __init__(self) -> None:
        self.found: dict[tuple[str, str], list[int]] = {}

    def add(self, rule: str, column: str, n: int) -> None:
        self.found.setdefault((rule, column), []).append(n)

    def check(self) -> None:
        if not self.found:
            return
        listed = sorted(self.found.items(), key=lambda item: item[1][0])
        said = [f"{column} {self.SAID[rule]} ({_rows_said(rows)})" for (rule, column), rows in listed[:PROBLEMS_SHOWN]]
        more = len(listed) - PROBLEMS_SHOWN
        refuse(
            listed[0][0][0],
            "Nothing pushed: " + "; ".join(said) + (f"; and {more} more problems." if more > 0 else "."),
            problems=[{"rule": r, "column": col, "rows": rows[:ROW_NUMBERS_SHOWN]} for (r, col), rows in listed],
        )


def _labels(
    cells: dict[str, Any], n: int, bad: _Problems, channels: dict[str, str], segments: dict[str, str] | None
) -> dict[str, str]:
    """segment (when `segments`), channel and campaign of one row, as stored."""
    out: dict[str, str] = {}
    said = _text(cells.get("channel")).lower()
    if said in ("", GX.UNKNOWN):
        out["channel"] = ""
    elif said in channels:
        out["channel"] = channels[said]
    else:
        bad.add("channel", "channel", n)
    if segments is not None:
        said = _text(cells.get("segment")).lower()
        if said in ("", GX.NO_SEGMENT):
            out["segment"] = ""
        elif said in segments:
            out["segment"] = segments[said]
        else:
            bad.add("segment", "segment", n)
    campaign = _text(cells.get("campaign"))
    if len(campaign) > LABEL_MAX:
        bad.add("long_label", "campaign", n)
    out["campaign"] = campaign
    return out


def _month(cells: dict[str, Any], n: int, bad: _Problems) -> str:
    said = _text(cells.get("period"))
    if not said:
        bad.add("blank", "period", n)
    elif not MONTH.match(said):
        bad.add("month", "period", n)
    return said


def check_rows(c: sqlite3.Connection, headers: list[str], rows: list[list[Any]], first: int) -> list[Row]:
    """A cohort rows push, checked: the rows as stored, less source and who. Refuses the whole push on any rule."""
    _columns(headers, ROW_COLUMNS, ROW_REQUIRED)
    if not rows:
        refuse("empty", "Nothing pushed: there are no rows.")
    if len(rows) > ROWS_MAX:
        refuse("too_many", f"Nothing pushed: a push holds at most {ROWS_MAX:,} rows.")
    segments, channels = _lookup(c, "growth_segments"), _lookup(c, "growth_channels")
    bad = _Problems()
    out: list[Row] = []
    for n, raw in enumerate(rows, start=first):
        cells = {h: raw[i] if i < len(raw) else None for i, h in enumerate(headers)}
        row: Row = {col: None for col in ROW_COLUMNS}
        anchor = _text(cells.get("anchor")).lower()
        if anchor not in ANCHORS:
            bad.add("blank" if not anchor else "anchor", "anchor", n)
        row["anchor"] = anchor
        row["period"] = _month(cells, n, bad)
        row.update(_labels(cells, n, bad, channels, segments))
        as_of = _text(cells.get("as_of"))
        try:
            if not DAY.match(as_of):
                raise ValueError
            date.fromisoformat(as_of)
        except ValueError:
            bad.add("blank" if not as_of else "date", "as_of", n)
        row["as_of"] = as_of
        for col in (
            "entered",
            *(f"{s}{e}" for s in STEPS for e in ("", "_eligible")),
            *(f"{x}_eligible" for x in NETS),
        ):
            try:
                row[col] = _count(cells.get(col))
            except (TypeError, ValueError):
                bad.add("count", col, n)
        if row["entered"] is None and ("count", "entered") not in bad.found:
            bad.add("blank", "entered", n)
        for net in NETS:
            try:
                row[f"{net}_sum"] = _amount(cells.get(f"{net}_sum"))
            except (TypeError, ValueError):
                bad.add("number", f"{net}_sum", n)
        entered = row["entered"] if row["entered"] is not None else 0
        for x, eligible in [(s, f"{s}_eligible") for s in STEPS] + [(f"{x}_sum", f"{x}_eligible") for x in NETS]:
            if (row[x] is None) != (row[eligible] is None):
                bad.add("pair", x, n)
            elif row[eligible] is not None and (
                row[eligible] > entered or (not x.endswith("_sum") and row[x] > row[eligible])
            ):
                bad.add("over", x, n)
        out.append(row)
    _unique(out, ROW_KEY, first, bad)
    bad.check()
    return _fold_campaigns(out, ROW_KEY, "entered", _merge_rows)


def check_costs(c: sqlite3.Connection, headers: list[str], rows: list[list[Any]], first: int) -> list[Row]:
    """A costs push, checked: the rows as stored, less source and who. Refuses the whole push on any rule."""
    _columns(headers, COST_COLUMNS, COST_REQUIRED)
    if not rows:
        refuse("empty", "Nothing pushed: there are no rows.")
    if len(rows) > ROWS_MAX:
        refuse("too_many", f"Nothing pushed: a push holds at most {ROWS_MAX:,} rows.")
    channels = _lookup(c, "growth_channels")
    bad = _Problems()
    out: list[Row] = []
    for n, raw in enumerate(rows, start=first):
        cells = {h: raw[i] if i < len(raw) else None for i, h in enumerate(headers)}
        row: Row = {"period": _month(cells, n, bad), **_labels(cells, n, bad, channels, None)}
        for col, allowed, default in (
            ("kind", KINDS, ""),
            ("purpose", PURPOSES, "acquisition"),
            ("quality", QUALITIES, "actual"),
        ):
            said = _text(cells.get(col)).lower() or default
            if said not in allowed:
                bad.add("blank" if not said else col, col, n)
            row[col] = said
        try:
            amount = _amount(cells.get("amount"))
            if amount is None:
                bad.add("blank", "amount", n)
            elif amount < 0:
                raise ValueError
            row["amount"] = amount
        except (TypeError, ValueError):
            bad.add("amount", "amount", n)
        out.append(row)
    _unique(out, COST_KEY, first, bad)
    bad.check()
    return _fold_campaigns(out, COST_KEY, "amount", _merge_costs)


def _unique(rows: list[Row], key: Sequence[str], first: int, bad: _Problems) -> None:
    seen: set[tuple[Any, ...]] = set()
    for n, row in enumerate(rows, start=first):
        k = tuple(row.get(col) for col in key)
        if k in seen:
            bad.add("duplicate", "row", n)
        seen.add(k)


def _plus(a: Any, b: Any) -> Any:
    return b if a is None else a if b is None else a + b


def _merge_rows(a: Row, b: Row) -> Row:
    out = {k: _plus(a[k], b[k]) for k in ROW_COLUMNS if k not in (*ROW_KEY, "as_of")}
    return {**a, **out, "as_of": max(a["as_of"], b["as_of"])}


def _merge_costs(a: Row, b: Row) -> Row:
    return {
        **a,
        "amount": a["amount"] + b["amount"],
        "quality": "estimate" if "estimate" in (a["quality"], b["quality"]) else "actual",
    }


def _fold_campaigns(rows: list[Row], key: Sequence[str], weight: str, merge: Callable[[Row, Row], Row]) -> list[Row]:
    """At most CAMPAIGNS_MAX campaigns per channel: the biggest by `weight` keep their name, the rest become `other`."""
    sizes: dict[str, dict[str, float]] = {}
    for r in rows:
        if r["campaign"] not in ("", OTHER):
            per = sizes.setdefault(r["channel"], {})
            per[r["campaign"]] = per.get(r["campaign"], 0) + float(r[weight] or 0)
    kept = {ch: set(sorted(per, key=lambda name: (-per[name], name))[:CAMPAIGNS_MAX]) for ch, per in sizes.items()}
    out: dict[tuple[Any, ...], Row] = {}
    for r in rows:
        if r["campaign"] not in ("", OTHER) and r["campaign"] not in kept[r["channel"]]:
            r = {**r, "campaign": OTHER}
        k = tuple(r[col] for col in key)
        out[k] = merge(out[k], r) if k in out else r
    return list(out.values())


def _replace(
    c: sqlite3.Connection, table_name: str, source: str, actor: str, rows: list[Row], columns: Sequence[str]
) -> Row:
    """Every row of `source` for the periods the push covers, replaced by the push."""
    periods = sorted({str(r["period"]) for r in rows})
    marks = ",".join("?" * len(periods))
    replaced = c.execute(
        f"DELETE FROM {table_name} WHERE source=? AND period IN ({marks})", [source, *periods]
    ).rowcount
    at: str = H.now()
    names = ["source", *columns, "pushed_by", "pushed_at"]
    c.executemany(
        f"INSERT INTO {table_name} ({', '.join(names)}) VALUES ({', '.join('?' * len(names))})",
        [[source, *(r[col] for col in columns), actor, at] for r in rows],
    )
    return {"source": source, "rows": len(rows), "periods": periods, "replaced": replaced, "pushed_at": at}


def push_rows(c: sqlite3.Connection, actor: str, body: M.GrowthCorePush) -> Row:
    if c.execute("SELECT 1 FROM growth_customers WHERE source=? LIMIT 1", (body.source,)).fetchone():
        refuse("source", "Nothing pushed: customer facts load under that source, and fold into its rows; use another.")
    rows = check_rows(c, *table(body))
    out = _replace(c, "growth_cohort_rows", body.source, actor, rows, ROW_COLUMNS)
    H.event(c, actor, "growth.rows.push", body.source, {k: out[k] for k in ("rows", "periods", "replaced")})
    return out


def push_costs(c: sqlite3.Connection, actor: str, body: M.GrowthCorePush) -> Row:
    if body.source == TYPED:
        refuse("source", f"Nothing pushed: {TYPED} is the source typed spend reads under; use another.")
    rows = check_costs(c, *table(body))
    out = _replace(c, "growth_costs", body.source, actor, rows, COST_COLUMNS)
    H.event(c, actor, "growth.costs.push", body.source, {k: out[k] for k in ("rows", "periods", "replaced")})
    return out


# ----------------------------------------------------------------------------- the read
@dataclass(slots=True)
class Data:
    """Everything the read sums: cohort rows (pushed and folded), costs (pushed and typed), and the team's setup."""

    rows: list[Row]
    costs: list[Row]
    segs: GX.Segments
    conf: Row
    on: date
    slugs: set[str]  # the channels' slugs
    lines: dict[str, Row]  # channel slug -> its owner_ref, budget_month and retired_at


def load(c: sqlite3.Connection) -> Data:
    conf: Row = G.settings(c)
    on = GX.today(conf)
    segs = GX.segments(c)
    lines: dict[str, Row] = {
        str(r["slug"]): r
        for r in H._rows(c.execute("SELECT slug, owner_ref, budget_month, retired_at FROM growth_channels"))
    }
    slugs = set(lines)
    rows: list[Row] = H._rows(c.execute("SELECT * FROM growth_cohort_rows"))
    if GX.has_facts(c):
        loaded = {
            str(s): str(at)[:10]
            for s, at in c.execute("SELECT source, max(loaded_at) FROM growth_customer_loads GROUP BY source")
        }
        rows += fold(GX.facts(c), segs, slugs, on, loaded)
    costs: list[Row] = H._rows(c.execute("SELECT * FROM growth_costs"))
    for (slug, month), typed in GX.typed_cohorts(c).items():
        for field, kind in (("spend", "money"), ("bot_cost", "bot")):
            if slug and typed.get(field) is not None:
                costs.append(
                    {
                        "source": TYPED,
                        "period": month,
                        "channel": slug,
                        "campaign": "",
                        "kind": kind,
                        "purpose": "acquisition",
                        "amount": float(typed[field]),
                        "quality": typed.get("quality") or "actual",
                    }
                )
    return Data(rows, costs, segs, conf, on, slugs, lines)


def verdict(value: float | None, average: float | None, n: int, least: int) -> str | None:
    """up, down or flat (within ±10%) against the trailing 3-month average; too_few under `least`."""
    if n < least:
        return "too_few"
    if value is None or average is None:
        return None
    if average == 0:
        return "up" if value > 0 else "flat"
    change = (value - average) / average
    return "up" if change > FLAT else "down" if change < -FLAT else "flat"


class Scope:
    """One scope's rows and costs, and the sums the read is made of."""

    def __init__(self, data: Data, kind: str, slug: str, owned: set[str] | None = None) -> None:
        self.data, self.kind, self.slug, self.owned = data, kind, slug, owned or set[str]()
        segs = data.segs
        if kind == "segment":
            self.rows = [r for r in data.rows if r["segment"] == slug]
            own = {ch for ch, seg in segs.channels.items() if seg == slug} if slug else set[str]()
            self.costs = [k for k in data.costs if k["channel"] in own]
            self.shared = (
                [k for k in data.costs if not k["channel"] or segs.channels.get(k["channel"]) is None] if slug else []
            )
        elif kind == "channel":
            self.rows = [r for r in data.rows if r["channel"] == slug]
            self.costs = [k for k in data.costs if slug and k["channel"] == slug]
            self.shared = []
        elif kind == "mine":
            self.rows = [r for r in data.rows if r["channel"] in self.owned]
            self.costs = [k for k in data.costs if k["channel"] in self.owned]
            self.shared = []
        else:
            self.rows, self.costs, self.shared = data.rows, data.costs, []
        self.by_month: dict[tuple[str, str], list[Row]] = {}
        for r in self.rows:
            self.by_month.setdefault((r["anchor"], r["period"]), []).append(r)
        self.first = {a: min((r["period"] for r in self.rows if r["anchor"] == a), default=None) for a in ANCHORS}
        seg = segs.rows.get(slug) if kind == "segment" else None
        self.least = (
            max(GX.MIN_COHORT_FLOOR, int(seg["min_cohort"]))
            if seg and seg.get("min_cohort")
            else GX.min_cohort(data.conf)
        )

    def at(self, anchor: str, month: str) -> list[Row]:
        return self.by_month.get((anchor, month), [])

    def entered(self, anchor: str, month: str) -> int | None:
        """The people who entered that month; None when the scope has no rows of this anchor at all."""
        if self.first[anchor] is None:
            return None
        return sum(int(r["entered"]) for r in self.at(anchor, month))

    def cost(
        self, month: str, kinds: Sequence[str], purpose: str = "acquisition", costs: list[Row] | None = None
    ) -> float | None:
        found = [
            float(k["amount"])
            for k in (self.costs if costs is None else costs)
            if k["period"] == month and k["kind"] in kinds and k["purpose"] == purpose
        ]
        return round(sum(found), 2) if found else None

    def share(self, month: str) -> float:
        """The segment's share of the month's new customers who have a segment (every channel)."""
        counts: dict[str, int] = {}
        for r in self.data.rows:
            if r["anchor"] == "customer" and r["period"] == month and r["segment"]:
                counts[r["segment"]] = counts.get(r["segment"], 0) + int(r["entered"])
        known = sum(counts.values())
        return counts.get(self.slug, 0) / known if known else 0.0

    def loaded(self, month: str) -> float | None:
        """A segment's own money cost plus company-wide money cost by its share of new customers."""
        direct, company = self.cost(month, MONEY), self.cost(month, MONEY, costs=self.shared)
        if direct is None and company is None:
            return None
        return (direct or 0) + (company or 0) * self.share(month)

    def pair(self, anchor: str, month: str, x: str, eligible: str) -> tuple[float, int] | None:
        found = [(r[x], r[eligible]) for r in self.at(anchor, month) if r[x] is not None and r[eligible] is not None]
        if not found:
            return None
        return sum(float(a) for a, _ in found), sum(int(b) for _, b in found)

    def supplied(self, anchor: str, column: str) -> bool:
        return any(r[column] is not None for r in self.rows if r["anchor"] == anchor)

    def rate(self, anchor: str, x: str, eligible: str, upto: str, money: bool = False) -> Row:
        """From the latest cohort up to `upto` whose eligible count reaches the minimum, and which cohort it is."""
        if not self.supplied(anchor, eligible):
            return {"value": None, "status": "not_supplied"}
        young: Row | None = None
        for month in sorted(
            {r["period"] for r in self.rows if r["anchor"] == anchor and r["period"] <= upto}, reverse=True
        ):
            found = self.pair(anchor, month, x, eligible)
            if not found or not found[1]:
                continue
            total, n = found
            if n >= self.least:
                value = round(total / n, 2) if money else round(total / n, 3)
                return {"value": value, "n": n, "cohort": month, "status": "ok"}
            young = young or {"value": None, "n": n, "cohort": month, "status": "too_few"}
        return young or {"value": None, "status": "not_yet"}

    def average(self, fn: Callable[[str], float | None], month: str) -> float | None:
        values = [v for v in (fn(shift(month, k)) for k in (1, 2, 3)) if v is not None]
        return round(sum(values) / len(values), 1) if values else None

    def cac(self, month: str) -> float | None:
        cost, new = self.cost(month, MONEY), self.entered("customer", month)
        return round(cost / new, 2) if cost is not None and new else None


def _count_figure(scope: Scope, anchor: str, month: str) -> Row:
    value = scope.entered(anchor, month)
    if value is None:
        return {"value": None, "status": "not_supplied"}
    first = scope.first[anchor] or month

    def prior(m: str) -> float | None:
        return float(scope.entered(anchor, m) or 0) if m >= first else None

    average = scope.average(prior, month)
    return {
        "value": value,
        "vs_3mo": average,
        "verdict": verdict(value, average, int(max(value, average or 0)), COUNT_FLOOR),
    }


def _kept(scope: Scope, month: str) -> Row:
    """Kept at the first window the scope supplies, of d90, d30 and d180."""
    for window in (90, 30, 180):
        if scope.supplied("customer", f"kept_d{window}_eligible"):
            return {
                "window": f"d{window}",
                **scope.rate("customer", f"kept_d{window}", f"kept_d{window}_eligible", month),
            }
    return {"value": None, "status": "not_supplied"}


def _ltv(scope: Scope, month: str) -> Row:
    """Observed net revenue per customer at the longest age with a cohort big enough, else the first supplied."""
    figures = [
        (days, scope.rate("customer", f"net_d{days}_sum", f"net_d{days}_eligible", month, money=True)) for days in AGES
    ]
    for days, figure in reversed(figures):
        if figure["status"] == "ok":
            return {"value": figure["value"], "window": f"d{days}", **{k: v for k, v in figure.items() if k != "value"}}
    for days, figure in figures:
        if figure["status"] != "not_supplied":
            return {"value": None, "window": f"d{days}", **{k: v for k, v in figure.items() if k != "value"}}
    return {"value": None, "status": "not_supplied"}


def headline(scope: Scope, month: str, value_kind: str) -> Row:
    new = scope.entered("customer", month)
    money, effort = scope.cost(month, MONEY), scope.cost(month, EFFORT)
    out: Row = {
        "signups": _count_figure(scope, "signup", month),
        "activated_rate": scope.rate("signup", "activated", "activated_eligible", month),
        "paid_rate": scope.rate("signup", "paid", "paid_eligible", month),
        "new_customers": _count_figure(scope, "customer", month),
    }
    cost: Row = {"value": money}
    if money is None:
        cost["status"] = "no_spend"
    if effort is not None:
        cost["with_effort"] = round((money or 0) + effort, 2)
    retention = scope.cost(month, MONEY, "retention")
    if retention is not None:
        cost["retention"] = retention  # never in CAC
    out["cost"] = cost
    n = new or 0
    cac: Row = {"value": scope.cac(month), "n": n}
    if money is None:
        cac["status"] = "no_spend"
    elif not n:
        cac["status"] = "no_customers"
    else:
        cac.update(verdict=verdict(cac["value"], scope.average(scope.cac, month), n, scope.least), status="ok")
    if effort is not None and n:
        cac["with_effort"] = round(((money or 0) + effort) / n, 2)
    out["cac"] = cac
    if scope.kind == "segment" and scope.slug:
        loaded = scope.loaded(month)
        out["cac_loaded"] = (
            {"value": round(loaded / n, 2)}
            if loaded is not None and n
            else {"value": None, "status": "no_spend" if loaded is None else "no_customers"}
        )
    out["kept"] = _kept(scope, month)
    if value_kind == "retention":
        out["value"] = _value(scope, month)
        return out
    ltv = out["ltv"] = _ltv(scope, month)
    if cac["value"] is None:
        out["ltv_cac"] = {"value": None, "status": cac["status"]}
        out["payback_months"] = {"value": None, "status": cac["status"]}
    elif ltv["value"] is None:
        out["ltv_cac"] = out["payback_months"] = {"value": None, "status": ltv["status"]}
    else:
        out["ltv_cac"] = {"value": round(ltv["value"] / cac["value"], 1)}
        monthly = ltv["value"] / (
            int(ltv["window"][1:]) / 30
        )  # flat net revenue per month over the window: an estimate
        out["payback_months"] = {
            "value": round(cac["value"] / monthly, 1) if monthly > 0 else None,
            "status": "estimate",
        }
    return out


def _value(scope: Scope, month: str) -> Row:
    """A retention segment's worth: kept at day 30, and its cohort month's acquisition cost per customer kept."""
    kept = scope.rate("customer", "kept_d30", "kept_d30_eligible", month)
    out: Row = {"kept_d30": kept}
    if kept["status"] != "ok":
        out["cost_per_retained"] = {"value": None, "status": kept["status"]}
        return out
    cohort = str(kept["cohort"])
    found = scope.pair("customer", cohort, "kept_d30", "kept_d30_eligible")
    retained = int(found[0]) if found else 0
    money = scope.loaded(cohort) if scope.kind == "segment" and scope.slug else scope.cost(cohort, MONEY)
    effort = scope.cost(cohort, EFFORT)
    figure: Row = {
        "value": round(money / retained, 2) if money is not None and retained else None,
        "cohort": cohort,
        "n": retained,
    }
    if money is None:
        figure["status"] = "no_spend"
    if effort is not None and retained:
        figure["with_effort"] = round(((money or 0) + effort) / retained, 2)
    out["cost_per_retained"] = figure
    return out


def channels(scope: Scope, month: str) -> list[Row]:
    """Each channel's new customers, cost and CAC in the month, with its owner and monthly budget (cost over budget as
    backend/growth_line.py has it); Unknown last. Scoped to mine, every live channel the caller owns."""
    data = scope.data
    new: dict[str, int] = {}
    for r in scope.at("customer", month):
        new[r["channel"]] = new.get(r["channel"], 0) + int(r["entered"])
    spent = {k["channel"] for k in scope.costs if k["period"] == month and k["channel"]}
    total_new = sum(new.values())
    total_cost = scope.cost(month, MONEY) or 0
    out: list[Row] = []
    owned = {ch for ch in scope.owned if not data.lines[ch].get("retired_at")}
    for slug in sorted(set(new) | spent | owned):
        n = new.get(slug, 0)
        row: Row = {"slug": slug or GX.UNKNOWN, "new": n, "new_share": round(n / total_new, 2) if total_new else None}
        if not slug:
            out.append(row)
            continue
        line = data.lines.get(slug) or {}
        budget = line.get("budget_month")
        row.update(owner=line.get("owner_ref"), budget=budget, over_budget=None)
        if scope.kind == "segment" and data.segs.channels.get(slug) != scope.slug:
            # company-wide or another segment's spend: not split to this segment
            spends = any(k["channel"] == slug and k["period"] == month and k["kind"] in MONEY for k in data.costs)
            row.update(cost=None, cac=None, status="not_split" if spends else "no_spend")
            out.append(row)
            continue
        own = [k for k in scope.costs if k["channel"] == slug]
        money, effort = scope.cost(month, MONEY, costs=own), scope.cost(month, EFFORT, costs=own)

        def cac_of(m: str, own: list[Row] = own, slug: str = slug) -> float | None:
            cost = scope.cost(m, MONEY, costs=own)
            people = sum(int(r["entered"]) for r in scope.at("customer", m) if r["channel"] == slug)
            return round(cost / people, 2) if cost is not None and people else None

        row["cost"] = money
        over = round(money - float(budget), 2) if money is not None and budget is not None else None
        row["over_budget"] = over if over is not None and over > 0 else None
        row["cac"] = round(money / n, 2) if money is not None and n else None
        if money is None:
            row["status"] = "no_spend"
        elif not n:
            row["status"] = "no_customers"
        else:
            row["cost_share"] = round(money / total_cost, 2) if total_cost else None
            row["verdict"] = verdict(row["cac"], scope.average(cac_of, month), n, scope.least)
        if effort is not None and n:
            row["with_effort"] = round(((money or 0) + effort) / n, 2)
        out.append(row)
    out.sort(key=lambda r: (r["slug"] == GX.UNKNOWN, -int(r["new"]), str(r["slug"])))
    return out


def health(scope: Scope, month: str) -> Row:
    data = scope.data
    unknown = sum(int(r["entered"]) for r in scope.at("customer", month) if not r["channel"])
    total = scope.entered("customer", month) or 0
    out: Row = {"unknown_share": round(unknown / total, 2) if total else None}
    if data.segs.rows:
        no_segment = mismatch = 0
        for r in data.rows:
            if r["anchor"] != "customer" or r["period"] != month:
                continue
            if not r["segment"]:
                no_segment += int(r["entered"])
                continue
            spend_for = data.segs.channels.get(r["channel"]) if r["channel"] else None
            if spend_for and spend_for != r["segment"]:
                mismatch += int(r["entered"])
        out.update(no_segment=no_segment, mismatch=mismatch)
    newest: dict[str, str] = {}
    for r in data.rows:
        newest[r["source"]] = max(newest.get(r["source"], ""), str(r["as_of"]))
    line = (data.on - timedelta(days=STALE_DAYS)).isoformat()
    out.update(reconciled="not_reconciled", stale_sources=sorted(s for s, at in newest.items() if at < line))
    return out


def series(scope: Scope, month: str, window: str | None) -> list[Row]:
    out: list[Row] = []
    for k in range(SERIES_MONTHS - 1, -1, -1):
        m = shift(month, k)
        kept = scope.pair("customer", m, f"kept_{window}", f"kept_{window}_eligible") if window else None
        out.append(
            {
                "period": m,
                "signups": scope.entered("signup", m),
                "new_customers": scope.entered("customer", m),
                "cost": scope.cost(m, MONEY),
                "cac": scope.cac(m),
                "kept": round(kept[0] / kept[1], 3) if kept and kept[1] >= scope.least else None,
            }
        )
    return out


def retention(scope: Scope, month: str) -> list[Row]:
    cohorts = sorted(
        {r["period"] for r in scope.rows if r["anchor"] == "customer" and r["period"] <= month}, reverse=True
    )
    out: list[Row] = []
    for m in cohorts[:RETENTION_COHORTS]:
        row: Row = {"cohort": m, "entered": scope.entered("customer", m)}
        for window in WINDOWS:
            found = scope.pair("customer", m, f"kept_d{window}", f"kept_d{window}_eligible")
            if found:
                row[f"kept_d{window}"] = {"value": round(found[0] / found[1], 3) if found[1] else None, "n": found[1]}
        out.append(row)
    return out


def campaigns(scope: Scope, month: str) -> dict[str, list[Row]]:
    found: dict[str, dict[str, Row]] = {}
    for r in scope.at("customer", month):
        cell = found.setdefault(r["channel"] or GX.UNKNOWN, {}).setdefault(
            r["campaign"] or "none", {"new": 0, "cost": None}
        )
        cell["new"] += int(r["entered"])
    for k in scope.costs:
        if k["period"] == month and k["kind"] in MONEY and k["purpose"] == "acquisition":
            cell = found.setdefault(k["channel"] or GX.UNKNOWN, {}).setdefault(
                k["campaign"] or "none", {"new": 0, "cost": None}
            )
            cell["cost"] = round((cell["cost"] or 0) + float(k["amount"]), 2)
    out: dict[str, list[Row]] = {}
    for channel, cells in sorted(found.items()):
        if set(cells) == {"none"}:
            continue
        out[channel] = [
            {
                "campaign": name,
                **cell,
                "cac": round(cell["cost"] / cell["new"], 2) if cell["cost"] is not None and cell["new"] else None,
            }
            for name, cell in sorted(cells.items(), key=lambda item: (-int(item[1]["new"]), item[0]))
        ]
    return out


def metrics(c: sqlite3.Connection, scope: Scope, month: str, hidden: Iterable[str] = ()) -> list[Row]:
    """The KPIs that belong to Growth: linked to a channel (its input or guardrail KPI) or owned by the Growth Manager,
    leaving out those of owners the caller may not read. Scoped to a segment, its channels' KPIs and the manager's; to a
    channel (or mine), only those channels' KPIs. Each
    with its latest reading up to the month's end and the reading before it (`vs_prior`)."""
    linked: dict[str, str] = {}
    for slug, *ids in c.execute(
        "SELECT slug, input_kpi_id, guardrail_kpi_id FROM growth_channels WHERE retired_at IS NULL ORDER BY sort, slug"
    ):
        for kpi_id in ids:
            if kpi_id:
                linked.setdefault(str(kpi_id), str(slug))
    if scope.kind == "channel":
        linked = {k: ch for k, ch in linked.items() if ch == scope.slug}
    elif scope.kind == "mine":
        linked = {k: ch for k, ch in linked.items() if ch in scope.owned}
    elif scope.kind == "segment":
        linked = {k: ch for k, ch in linked.items() if scope.data.segs.channels.get(ch) == scope.slug}
    manager = page_owners.owner(c, "growth", fallback=False) if scope.kind not in ("channel", "mine") else None
    marks = ",".join("?" * len(linked)) or "''"
    found: list[Row] = H._rows(
        c.execute(
            f"SELECT id, name, owner_id FROM kpis WHERE archived_at IS NULL AND (id IN ({marks}) OR owner_id=?) "
            "ORDER BY created_at",
            [*linked, manager or ""],
        )
    )
    blocked, end = set(hidden), GX.period(date.fromisoformat(month + "-01"), "month")[2].isoformat()
    out: list[Row] = []
    for kpi in [k for k in found if k["owner_id"] not in blocked][:METRICS_MAX]:
        upto = [
            r
            for r in K.readings(c, kpi["id"], effective=True)
            if str(r.get("period_end") or r["measured_at"])[:10] <= end
        ]
        newest = K.latest(upto)
        prior = K.latest([r for r in upto if r is not newest]) if newest else None
        out.append(
            {
                "id": kpi["id"],
                "name": kpi["name"],
                "owner": kpi["owner_id"],
                "channel": linked.get(str(kpi["id"])),
                "value": newest["value"] if newest else None,
                "period_end": str(newest.get("period_end") or newest["measured_at"])[:10] if newest else None,
                "estimate": bool(newest and newest.get("quality") == "estimate"),
                "vs_prior": prior["value"] if prior else None,
            }
        )
    return out


def _scope(c: sqlite3.Connection, data: Data, said: str) -> tuple[str, str, Row | None]:
    """(kind, slug, the segment row) of a scope as said: all, mine, segment:<slug or name>, channel:<slug or name>."""
    kind, _, value = said.strip().partition(":")
    value = value.strip().lower()
    if kind in ("all", "mine") and not value:
        return kind, "", None
    if kind == "segment" and value:
        if value == GX.NO_SEGMENT:
            return "segment", "", None
        slug = data.segs.labels.get(value)
        if not slug:
            raise Problem("not_found", "No such segment: tico growth segment list names them", 404)
        return "segment", slug, data.segs.rows[slug]
    if kind == "channel" and value:
        if value == GX.UNKNOWN:
            return "channel", "", None
        slug = _lookup(c, "growth_channels").get(value)
        if not slug:
            raise Problem("not_found", "No such channel: tico growth channel list names them", 404)
        return "channel", slug, None
    raise Problem("validation", "scope is all, mine, segment:<slug> or channel:<slug>", 422)


def read(
    c: sqlite3.Connection,
    scope: str = "all",
    period: str | None = None,
    detail: str = "",
    actor: str | None = None,
    hidden: Iterable[str] = (),
) -> Row:
    """The Growth read: one scope's month, summed from cohort rows and costs (see the module). `mine` is the channels
    `actor` owns: none is an empty read, not an error."""
    wanted = [d.strip() for d in (detail or "").split(",") if d.strip()]
    if any(d not in DETAILS for d in wanted):
        raise Problem("validation", f"detail is a comma list of {', '.join(DETAILS)}", 422)
    if period is not None and not MONTH.match(period):
        raise Problem("validation", "period is a month like 2026-10", 422)
    data = load(c)
    month = period or last_full_month(data.on)
    kind, slug, segment = _scope(c, data, scope or "all")
    owned = (
        {ch for ch, line in data.lines.items() if actor and line.get("owner_ref") == actor} if kind == "mine" else None
    )
    seen = Scope(data, kind, slug, owned)
    if segment:
        value_kind = str(segment.get("value_kind") or "revenue")
    else:
        live = [s for s in data.segs.rows.values() if not s.get("retired_at")]
        value_kind = "retention" if live and all(s.get("value_kind") == "retention" for s in live) else "revenue"
    out: Row = {
        "scope": f"{kind}:{slug or (GX.NO_SEGMENT if kind == 'segment' else GX.UNKNOWN)}"
        if kind not in ("all", "mine")
        else kind
    }
    out.update(period=month, as_of=data.on.isoformat())
    if kind == "segment":
        out["segment"] = {
            "slug": slug or GX.NO_SEGMENT,
            "name": segment["name"] if segment else "No segment",
            "rule": segment.get("customer_rule") if segment else "",
            "value_kind": value_kind,
        }
    top = out["headline"] = headline(seen, month, value_kind)
    out["channels"] = channels(seen, month)
    out["health"] = health(seen, month)
    out["metrics"] = metrics(c, seen, month, hidden)
    if "campaigns" in wanted:
        out["campaigns"] = campaigns(seen, month)
    if "retention" in wanted:
        out["retention"] = retention(seen, month)
    if "series" in wanted:
        out["series"] = series(seen, month, top["kept"].get("window"))
    out["more"] = [d for d in DETAILS if d not in wanted]
    return out


def install(app: Any, store: Any, auth: Any, mutate: Callable[..., Any]) -> None:
    def editor(c: sqlite3.Connection, who: Any, what: str) -> None:
        auth.domain(who)
        if not may_edit(auth, who, c):
            raise Problem("forbidden", f"Only the owner, an admin or the Growth Manager {what}", 403)

    @app.get("/api/v2/growth/read")
    def growth_read(request: Request, scope: str = "all", period: str | None = None, detail: str = "") -> Row:  # pyright: ignore[reportUnusedFunction]
        """The Growth read: a scope's month (all, mine: the channels the caller owns, segment:<slug>, channel:<slug>;
        default the last full month) summed from cohort rows and costs: headline, channels with owner and budget, and
        health, and Growth's KPIs (`metrics`); `detail` adds campaigns, retention, series."""
        from . import goal_routes

        routes: Any = goal_routes

        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            return read(c, scope, period, detail, who.actor, cast("set[str]", routes._hidden_owners(c, auth, who)))

    @app.post("/api/v2/growth/rows")
    def growth_rows_push(request: Request, body: M.GrowthCorePush) -> Any:  # pyright: ignore[reportUnusedFunction]
        """Owner, admin or the Growth Manager: a source's cohort rows, as CSV text or rows. They replace the source's
        rows for the months they cover; nothing is pushed when any rule refuses, naming the column and row."""
        who = request.state.identity

        def work(c: sqlite3.Connection) -> Row:
            editor(c, who, "pushes cohort rows")
            return push_rows(c, who.actor, body)

        return mutate(request, body, work)

    @app.post("/api/v2/growth/costs")
    def growth_costs_push(request: Request, body: M.GrowthCorePush) -> Any:  # pyright: ignore[reportUnusedFunction]
        """Owner, admin or the Growth Manager: a source's costs, as CSV text or rows. They replace the source's costs
        for the months they cover; nothing is pushed when any rule refuses, naming the column and row."""
        who = request.state.identity

        def work(c: sqlite3.Connection) -> Row:
            editor(c, who, "pushes costs")
            return push_costs(c, who.actor, body)

        return mutate(request, body, work)
