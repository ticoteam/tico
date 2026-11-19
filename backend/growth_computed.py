# pyright: strict
"""Figures computed from customer facts (backend/growth_customers.py): new customers, retention, LTV, CAC and payback for
each cohort, cut by channel, segment, plan or all together, at weekly (Monday to Sunday) or monthly grain.

A cohort is the customer spells whose "became customer" date falls in the same period. For each one:

- `new_customers`: how many spells it holds.
- `kept_d30`, `kept_d90`, `kept_d180`: the share still customers on day N (no churned date, or one after became + N
  days). A window shows only once every customer in the cohort is at least N days old, that is from the period's last
  day + N days (`fills_on`); before then its status is `not_yet`, never 0%. Churn that means "no activity for some
  days" is only known that many days late, so a cohort whose customers are in a segment with `churn_days` waits that
  much longer: N + churn_days after the period's last day, taking the largest churn_days among the cohort's customers
  (a channel or all-together cohort mixing segments waits for the slowest; `churn_days` on the cohort says how long).
  A cohort under the team's minimum (`min_cohort`, default 30, never below 5; a segment's own in its segment cut)
  shows the count only, status `too_few`.
- `revenue_month`, `expected_months`, `ltv`: today's maths (growth.expected_months, revenue per month x margin) on the
  computed retention. Revenue per customer is the customer's own, else the typed figure for their channel's month, else
  their segment's, else the settings value; margin is the channel's month, else the segment's, else the settings. A
  cohort under the minimum shows `too_few`.
- `ltv_observed_d90`, `_d180`, `_d365` (when any customer carries a net revenue): the mean observed cumulative net
  revenue per customer at that age, over the cohort's customers at least that old who have the value. `not_yet` until
  one is that old (`fills_on`), `no_data` when none has the value, `too_few` under the minimum; `count` is the customers
  it averages and `denominator` the cohort. The modelled `ltv` stays beside it.
- `cac`, `payback_months`: from the month's typed spend and bot cost, by channel and all together. Unknown has no
  spend. Plan has no spend (`not_split`), and neither has segment for a team with no segments. With segments, a
  segment's `cac` is direct: the spend and bot cost of the channels whose spend is for it, over its new customers.
  `cac_loaded` adds the company-wide channels' spend (no segment; Unknown has none) times the segment's share of the
  period's new customers who have a segment, every customer counted whatever the filter. No segment has no spend.
  So the segments' loaded spend adds up to the All cohort's spend, less the spend of a segment's channels in a period
  the segment has no new customers (it has no cohort to carry it). Payback is on the direct CAC.
  Cut to a channel or plan, a segment's CAC and loaded CAC are `not_split`: its spend is not split to them.
  A weekly cohort's spend is its Monday-to-Sunday days of Finance's
  daily ad spend (backend/growth_finance.py weeks) while a live Finance ad spend source fills spend, with no bot cost,
  which is monthly. Otherwise, or where a channel's month spend is typed or another source's (who wins,
  backend/growth_sync.py: a sync never overwrites a typed actual), spend is monthly and the week says `spend_monthly`.
- `attribution` (when the customers carry it): how many customers each rung named the channel by (utm, code, ...).

With segments, a customer's segment is the one its label names by slug or name, any case (backend/growth_segments.py);
the segment cut shows the rest as `none` (No segment). Every answer then carries `health`: `no_segment`, the customers
whose label names no segment, and `mismatch`, the ones in a segment other than the one their channel's spend is for,
over every customer in the period whatever the filter.

Each figure is {value, status, origin: computed}, with `count` and `denominator` where it is a share. A cohort's
`sources` are the customers sources its customers were loaded from ("upload", or "source:<id>"). The trace lists
the customer keys behind a cohort, split into still customers and churned with the date, and the rule used; keys and
churn dates go only to the owner, admins and the people or bots in `growth_settings.key_readers`. Everyone else who
reads Growth gets the counts and the rule.

Figures are computed when read, from the stored facts and today's date in the team's time zone, so a load shows at once
and day 90 and day 180 fill in as cohorts age with nobody typing. A team's whole history (100,000 customers over three
years) computes in a few seconds.
"""

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from typing import Any, cast

from fastapi import Request

from . import brand as _brand_untyped
from . import growth as _growth_untyped
from . import growth_customers as GC
from . import growth_finance as GF
from . import growth_sync as GS
from . import hubdb as _hubdb_untyped
from .store import Problem

G: Any = _growth_untyped
H: Any = _hubdb_untyped
admin = cast("Any", _brand_untyped.admin)

WINDOWS = (30, 90, 180)
CUTS = ("channel", "segment", "plan", "all")
GRAINS = ("month", "week")
MIN_COHORT, MIN_COHORT_FLOOR = 30, 5
UNKNOWN = "unknown"  # growth.UNKNOWN: a customer whose channel is not one of the team's
NO_SEGMENT = "none"  # growth_segments.NO_SEGMENT: a customer whose label names none of the team's segments
NETS = (90, 180, 365)  # the ages observed net revenue is given at

Row = dict[str, Any]
Typed = dict[tuple[str | None, str], Row]  # (channel slug or None, YYYY-MM) -> the typed cohort row


@dataclass(slots=True)
class Fact:
    """One customer spell, as stored."""

    customer_key: str
    became: date
    churned: date | None = None
    channel: str | None = None
    segment: str | None = None
    plan: str | None = None
    revenue_month: float | None = None
    source: str = ""  # the growth_customers.source it was loaded under: "upload" or "source:<id>"
    net_d90: float | None = None
    net_d180: float | None = None
    net_d365: float | None = None
    attribution: str | None = None

    def net(self, days: int) -> float | None:
        return {90: self.net_d90, 180: self.net_d180, 365: self.net_d365}[days]


@dataclass(slots=True)
class Segments:
    """The team's segments for the cuts (backend/growth_segments.py); empty for a team with none, whose segment labels
    read as they are."""

    rows: dict[str, Row]  # slug -> growth_segments row, retired ones too: their customers still count under them
    labels: dict[str, str]  # a label as customers give it, lowercased (a slug or a name) -> the slug
    channels: dict[str, str | None]  # channel slug -> the slug of the segment its spend is for; None is company-wide
    counts: dict[tuple[str, str], int] | None = None  # (period, segment slug or none) -> every new customer, unfiltered
    filtered: bool = False  # the facts are cut to a channel or plan: a segment's spend cannot be split to them

    def of(self, f: Fact) -> Row | None:
        return self.rows.get(self.labels.get((f.segment or "").strip().lower(), ""))

    def slug(self, f: Fact) -> str:
        seg = self.of(f)
        return str(seg["slug"]) if seg else NO_SEGMENT


@dataclass(slots=True)
class Daily:
    """Finance's daily ad spend by week, for weekly CAC, while a live Finance ad spend source fills spend."""

    spend: dict[tuple[str | None, str], float]  # (channel slug or None for Unknown, the week's Monday) -> spend
    monthly: set[tuple[str, str]]  # (channel slug, YYYY-MM) whose spend is typed or another source's: the month wins
    bot_cost: bool = False  # a channel-month read has bot cost, which weekly CAC leaves out


@dataclass(slots=True)
class _Group:
    value: str | None
    period: str
    start: date
    end: date
    rows: list[tuple[Fact, str | None]]


def today(conf: Row) -> date:
    return datetime.now(GC.zone(conf.get("time_zone"))).date()


def maturity(facts: Iterable[Fact], segs: Segments) -> int:
    """The days a cohort's kept windows wait past their length: the longest churn delay of its customers' segments."""
    return max((int((segs.of(f) or {}).get("churn_days") or 0) for f in facts), default=0) if segs.rows else 0


def period(became: date, grain: str) -> tuple[str, date, date]:
    """(period key, first day, last day) of the cohort a date falls in. A week runs Monday to Sunday and is keyed by
    its Monday."""
    if grain == "week":
        start = became - timedelta(days=became.weekday())
        return start.isoformat(), start, start + timedelta(days=6)
    start = became.replace(day=1)
    nxt = (start + timedelta(days=32)).replace(day=1)
    return start.isoformat()[:7], start, nxt - timedelta(days=1)


def _pct(kept: int, total: int) -> float | None:
    return round(kept * 100 / total, 1) if total else None


def _figure(value: float | int | None, status: str = "ok", **more: Any) -> Row:
    return {"value": value if status == "ok" else None, "status": status, "origin": "computed", **more}


def _rule(cut: str, value: str | None, start: date, end: date, window: int | None = None) -> str:
    said = f"became customer {start.isoformat()} to {end.isoformat()}"
    if cut != "all":
        said += f", {cut} {value if value is not None else 'none'}"
    if window:
        said += f", still paying on day {window}"
    return said


def compute(
    facts: list[Fact],
    on: date,
    *,
    cut: str = "channel",
    grain: str = "month",
    channels: set[str] | None = None,
    typed: Typed | None = None,
    conf: Row | None = None,
    min_cohort: int = MIN_COHORT,
    trace: bool = False,
    keys: bool = False,
    daily: Daily | None = None,
    segs: Segments | None = None,
) -> list[Row]:
    """Every cohort of `facts` cut by `cut` at `grain`, as of the date `on`.

    channels: the Growth channel slugs; any other channel is Unknown. typed: the typed cohorts, for the month's spend,
    revenue and margin. trace: add each window's rule; keys: and the customer keys behind it. daily: Finance's daily
    spend by week, for weekly CAC (daily_spend); without it a week's CAC says spend is monthly. segs: the team's
    segments (`segments`), whose `counts` are every customer's when `facts` is filtered; from `facts` when not given."""
    conf, typed, channels = conf or {}, typed or {}, channels or set()
    segs = segs or Segments({}, {}, {})
    by_segment = cut == "segment" and bool(segs.rows)
    if by_segment and segs.counts is None:
        segs = replace(segs, counts=new_by_segment(facts, grain, segs))
    groups: dict[tuple[str | None, str], _Group] = {}
    for f in facts:
        slug = f.channel if f.channel in channels else None
        if by_segment:
            value = segs.slug(f)
        else:
            value = {"channel": slug or UNKNOWN, "segment": f.segment, "plan": f.plan, "all": "all"}[cut]
        key, start, end = period(f.became, grain)
        group = groups.get((value, key))
        if group is None:
            group = groups[(value, key)] = _Group(value, key, start, end, [])
        group.rows.append((f, slug))
    observed = any(f.net_d90 is not None or f.net_d180 is not None or f.net_d365 is not None for f in facts)
    out = [
        _cohort(g, on, cut, grain, typed, conf, min_cohort, trace, keys, daily, segs, observed) for g in groups.values()
    ]
    last = (UNKNOWN, NO_SEGMENT) if by_segment else (UNKNOWN,)
    out.sort(key=lambda r: (r["value"] in last, r["value"] is None, str(r["value"])))
    out.sort(key=lambda r: r["period"], reverse=True)
    return out


def new_by_segment(facts: list[Fact], grain: str, segs: Segments) -> dict[tuple[str, str], int]:
    """{(period, segment slug or none): new customers}: the shares company-wide spend is loaded by."""
    out: dict[tuple[str, str], int] = {}
    for f in facts:
        key = (period(f.became, grain)[0], segs.slug(f))
        out[key] = out.get(key, 0) + 1
    return out


def _first(*values: Any) -> Any:
    return next((v for v in values if v is not None), None)


def _money(
    rows: list[tuple[Fact, str | None]], typed: Typed, conf: Row, segs: Segments
) -> tuple[float | None, float | None]:
    """(revenue per customer-month, profit per customer-month) over the cohort's customers, each customer's revenue its
    own, else its channel's typed month, else its segment's, else the settings; margin its channel's typed month, else
    its segment's, else the settings."""
    revenues: list[float] = []
    profits: list[float] = []
    for f, slug in rows:
        cohort = typed.get((slug, f.became.isoformat()[:7])) or {}
        seg = (segs.of(f) if segs.rows else None) or {}
        revenue = _first(
            f.revenue_month, cohort.get("revenue_month"), seg.get("revenue_month"), conf.get("revenue_month")
        )
        margin = _first(cohort.get("margin_pct"), seg.get("margin_pct"), conf.get("margin_pct"))
        if revenue is not None:
            revenues.append(float(revenue))
            if margin is not None:
                profits.append(float(revenue) * float(margin) / 100)
    return _mean(revenues), _mean(profits)


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


def _spend(
    cut: str, value: str | None, grain: str, key: str, typed: Typed, daily: Daily | None = None
) -> tuple[float | None, str]:
    """(cost, status) for CAC: the month's typed spend plus bot cost for a channel, or every channel's for all; for a
    week, its days of Finance's daily spend (`daily`), unless a month it touches has spend that wins."""
    if cut in ("segment", "plan"):
        return None, "not_split"
    if grain == "week" and daily is None:
        return None, "spend_monthly"
    if cut == "channel" and value == UNKNOWN:
        return None, "no_spend"
    return _cost({str(value)} if cut == "channel" else None, grain, key, typed, daily)


def _cost(slugs: set[str] | None, grain: str, key: str, typed: Typed, daily: Daily | None) -> tuple[float | None, str]:
    """(cost, status) of these channels' spend and bot cost in the period (None: every channel but Unknown)."""
    if grain == "week":
        return _week_spend(slugs, key, daily) if daily is not None else (None, "spend_monthly")
    rows = [t for (slug, m), t in typed.items() if m == key and slug and (slugs is None or slug in slugs)]
    costs = [(t.get("spend"), t.get("bot_cost")) for t in rows]
    costs = [(s, b) for s, b in costs if s is not None or b is not None]
    if not costs:
        return None, "no_spend"
    return float(sum((s or 0) + (b or 0) for s, b in costs)), "ok"


def _segment_spend(
    value: str, grain: str, key: str, typed: Typed, daily: Daily | None, segs: Segments
) -> tuple[tuple[float | None, str], tuple[float | None, str]]:
    """((direct cost, status), (loaded cost, status)) of a segment's period (see the module's `cac`)."""
    if value == NO_SEGMENT:
        return (None, "no_spend"), (None, "no_spend")
    own = {ch for ch, seg in segs.channels.items() if seg == value}
    shared = {ch for ch, seg in segs.channels.items() if seg is None}
    direct, status = _cost(own, grain, key, typed, daily)
    company, company_status = _cost(shared, grain, key, typed, daily)
    counts = segs.counts or {}
    known = sum(n for (p, seg), n in counts.items() if p == key and seg != NO_SEGMENT)
    share = counts.get((key, value), 0) / known if known else 0.0
    if "spend_monthly" in (status, company_status):
        return (direct, status), (None, "spend_monthly")
    if direct is None and company is None:
        return (direct, status), (None, "no_spend")
    return (direct, status), ((direct or 0) + (company or 0) * share, "ok")  # rounded once, as the CAC


def _week_spend(slugs: set[str] | None, monday: str, daily: Daily) -> tuple[float | None, str]:
    """A week's spend for these channels, or (None) every channel's but Unknown's, as a month's all has it."""
    start = date.fromisoformat(monday)
    months = {start.isoformat()[:7], (start + timedelta(days=6)).isoformat()[:7]}
    if any(m in months and (slugs is None or s in slugs) for s, m in daily.monthly):
        return None, "spend_monthly"
    found = [v for (s, w), v in daily.spend.items() if w == monday and s and (slugs is None or s in slugs)]
    return (float(round(sum(found), 2)), "ok") if found else (None, "no_spend")


def daily_spend(c: sqlite3.Connection, typed: Typed, since: date, until: date) -> Daily | None:
    """Finance's daily spend by week over the weeks [since, until] touch, while a live Finance ad spend source fills
    spend; None without one, or while it cannot read Finance (its source shows Failing with the reason)."""
    source = H._one(
        c,
        "SELECT id, sync_map FROM gtm_sources WHERE sync_kind=? AND retired_at IS NULL ORDER BY created_at LIMIT 1",
        (GF.KIND,),
    )
    if not source:
        return None
    start, end = period(since, "week")[1], period(until, "week")[2]
    try:
        spend = GF.weeks(c, GS.sync_map(source), start.isoformat(), end.isoformat())
    except GF.Failing:
        return None
    ours = {
        str(r[0])
        for r in c.execute(
            "SELECT cohort_id FROM growth_origins WHERE field='spend' AND origin='synced' AND source_id=?",
            (source["id"],),
        )
    }
    first, last = start.isoformat()[:7], end.isoformat()[:7]
    shown = [(slug, m, row) for (slug, m), row in typed.items() if slug and first <= m <= last]
    return Daily(
        spend,
        {(slug, m) for slug, m, row in shown if row.get("spend") is not None and str(row["id"]) not in ours},
        any(row.get("bot_cost") for _, _, row in shown),
    )


def _trace(rows: list[tuple[Fact, str | None]], kept: list[Fact]) -> Row:
    """The keys behind a retention figure: still customers, and churned with the date. Keys only."""
    counted = {id(f) for f in kept}
    return {
        "still": sorted(f.customer_key for f, _ in rows if id(f) in counted),
        "churned": sorted(
            (
                {"key": f.customer_key, "churned": f.churned.isoformat() if f.churned else None}
                for f, _ in rows
                if id(f) not in counted
            ),
            key=lambda c: (str(c["churned"]), str(c["key"])),
        ),
    }


def _cohort(
    group: _Group,
    on: date,
    cut: str,
    grain: str,
    typed: Typed,
    conf: Row,
    min_cohort: int,
    trace: bool,
    keys: bool,
    daily: Daily | None,
    segs: Segments,
    observed: bool,
) -> Row:
    rows, start, end = group.rows, group.start, group.end
    n = len(rows)
    by_segment = cut == "segment" and bool(segs.rows)
    own = segs.rows.get(str(group.value)) if by_segment else None
    if own and own.get("min_cohort"):
        min_cohort = max(MIN_COHORT_FLOOR, int(own["min_cohort"]))
    few = n < min_cohort
    wait = maturity((f for f, _ in rows), segs)
    view: Row = {
        "cut": cut,
        "value": group.value,
        "grain": grain,
        "period": group.period,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "computed_from": n,
        "sources": sorted({f.source for f, _ in rows if f.source}),
        "too_few": few,
        "new_customers": _figure(n),
        "rule": _rule(cut, group.value, start, end),
    }
    if wait:
        view["churn_days"] = wait
    rates: dict[int, float | None] = {}
    for window in WINDOWS:
        fills = end + timedelta(days=window + wait)
        name = f"kept_d{window}"
        if on < fills:
            view[name] = _figure(None, "not_yet", count=None, denominator=n, fills_on=fills.isoformat())
            continue
        kept = [f for f, _ in rows if f.churned is None or f.churned > f.became + timedelta(days=window)]
        rate = _pct(len(kept), n)
        figure = _figure(rate, "too_few" if few else "ok", count=len(kept), denominator=n, fills_on=fills.isoformat())
        view[name] = figure
        if not few:
            rates[window] = rate
        if trace:
            figure["rule"] = _rule(cut, group.value, start, end, window)
            if keys:
                figure["customers"] = _trace(rows, kept)
    revenue, profit = _money(rows, typed, conf, segs)
    months: float | None = G.expected_months(rates.get(30), rates.get(90), rates.get(180)) if 30 in rates else None
    ltv = round(profit * months, 2) if profit is not None and months is not None else None
    view["revenue_month"] = _figure(revenue, ("too_few" if few else "ok") if revenue is not None else "missing")
    if few:
        view["expected_months"] = _figure(None, "too_few")
        view["ltv"] = _figure(None, "too_few")
    else:
        why = "not_yet" if 30 not in rates else "missing"
        view["expected_months"] = _figure(months, "ok" if months is not None else why)
        view["ltv"] = _figure(ltv, "ok" if ltv is not None else why if months is None else "missing")
    if observed:
        for days in NETS:
            view[f"ltv_observed_d{days}"] = _observed(rows, on, days, min_cohort)
    if by_segment and segs.filtered:
        cost, status = None, "not_split"
        view["cac_loaded"] = _figure(None, "not_split", denominator=n)
    elif by_segment:
        (cost, status), (loaded, loaded_status) = _segment_spend(
            str(group.value), grain, group.period, typed, daily, segs
        )
        view["cac_loaded"] = _figure(round(loaded / n, 2) if loaded is not None else None, loaded_status, denominator=n)
    else:
        cost, status = _spend(cut, group.value, grain, group.period, typed, daily)
    cac = round(cost / n, 2) if cost is not None and n else None
    view["cac"] = _figure(cac, status, denominator=n) if by_segment else _figure(cac, status)
    if cac is None:
        view["payback_months"] = _figure(None, status)
    else:
        payback = round(cac / profit, 2) if profit else None
        view["payback_months"] = _figure(payback, "ok" if payback is not None else "missing")
    rungs: dict[str, int] = {}
    for f, _ in rows:
        if f.attribution:
            rungs[f.attribution] = rungs.get(f.attribution, 0) + 1
    if rungs:
        view["attribution"] = dict(sorted(rungs.items()))
    return view


def _observed(rows: list[tuple[Fact, str | None]], on: date, days: int, least: int) -> Row:
    """The mean net revenue per customer at `days` days old, over the cohort's customers that old with the value."""
    n = len(rows)
    old = [f for f, _ in rows if on >= f.became + timedelta(days=days)]
    if not old:
        first = min(f.became for f, _ in rows) + timedelta(days=days)
        return _figure(None, "not_yet", count=None, denominator=n, fills_on=first.isoformat())
    values = [v for v in (f.net(days) for f in old) if v is not None]
    if not values:
        return _figure(None, "no_data", count=0, denominator=n)
    mean = round(sum(values) / len(values), 2)
    return _figure(mean, "too_few" if len(values) < least else "ok", count=len(values), denominator=n)


def health(facts: list[Fact], channels: set[str], segs: Segments) -> Row:
    """With segments: the customers whose label names none (`no_segment`), and the ones in a segment other than the one
    their channel's spend is for (`mismatch`; a company-wide channel matches every segment)."""
    no_segment = mismatch = 0
    for f in facts:
        seg = segs.of(f)
        if seg is None:
            no_segment += 1
            continue
        spend_for = segs.channels.get(f.channel or "") if f.channel in channels else None
        if spend_for and spend_for != seg["slug"]:
            mismatch += 1
    return {"no_segment": no_segment, "mismatch": mismatch}


# ----------------------------------------------------------------------------- reads
def min_cohort(conf: Row) -> int:
    value = conf.get("min_cohort")
    return max(MIN_COHORT_FLOOR, int(value)) if value else MIN_COHORT


def facts(c: sqlite3.Connection, since: date | None = None, until: date | None = None) -> list[Fact]:
    """The stored customer spells, every source, `became` from `since` to `until` (dates, inclusive)."""
    where: list[str] = []
    args: list[str] = []
    if since:
        where.append("became>=?")
        args.append(since.isoformat())
    if until:
        where.append("became<=?")
        args.append(until.isoformat())
    sql = (
        "SELECT customer_key, became, churned, channel, segment, plan, revenue_month, source, net_d90, net_d180, "
        "net_d365, attribution FROM growth_customers"
    )
    return [
        Fact(
            key,
            date.fromisoformat(became),
            date.fromisoformat(churned) if churned else None,
            ch,
            seg,
            plan,
            rev,
            src,
            d90,
            d180,
            d365,
            rung,
        )
        for key, became, churned, ch, seg, plan, rev, src, d90, d180, d365, rung in c.execute(
            sql + (" WHERE " + " AND ".join(where) if where else ""), args
        )
    ]


def segments(c: sqlite3.Connection) -> Segments:
    """The team's segments, their labels and which segment each channel's spend is for."""
    rows: list[Row] = H._rows(c.execute("SELECT * FROM growth_segments"))
    labels = {str(r["name"]).strip().lower(): str(r["slug"]) for r in rows}
    labels.update({str(r["slug"]): str(r["slug"]) for r in rows})  # a slug wins over another segment's name
    ids = {str(r["id"]): str(r["slug"]) for r in rows}
    channels = {
        str(slug): ids.get(str(sid)) if sid else None
        for slug, sid in c.execute("SELECT slug, segment_id FROM growth_channels")
    }
    return Segments({str(r["slug"]): r for r in rows}, labels, channels)


def has_facts(c: sqlite3.Connection) -> bool:
    return c.execute("SELECT 1 FROM growth_customers LIMIT 1").fetchone() is not None


def _slugs(c: sqlite3.Connection) -> dict[str, str]:
    return {r[0]: r[1] for r in c.execute("SELECT id, slug FROM growth_channels")}


def typed_cohorts(c: sqlite3.Connection, month: str | None = None) -> Typed:
    """The typed cohort rows, every channel's, for spend, revenue and margin."""
    slugs = _slugs(c)
    sql, args = "SELECT * FROM growth_cohorts", []
    if month:
        sql, args = sql + " WHERE month=?", [month]
    found: list[Row] = H._rows(c.execute(sql, args))
    return {(slugs.get(r["channel_id"]) if r["channel_id"] else None, r["month"]): r for r in found}


def months(c: sqlite3.Connection) -> list[str]:
    """The months with customer facts, newest first."""
    return [r[0] for r in c.execute("SELECT DISTINCT substr(became, 1, 7) FROM growth_customers ORDER BY 1 DESC")]


def by_channel_month(c: sqlite3.Connection, month: str | None = None) -> dict[tuple[str | None, str], Row]:
    """{(slug or None, YYYY-MM): the channel's computed month}, for growth.cohorts to lay beside the typed rows."""
    if not has_facts(c):
        return {}
    conf: Row = G.settings(c)
    since = until = None
    if month:
        since = date.fromisoformat(month + "-01")
        until = period(since, "month")[2]
    rows = compute(
        facts(c, since, until),
        today(conf),
        channels=set(_slugs(c).values()),
        typed=typed_cohorts(c, month),
        conf=conf,
        min_cohort=min_cohort(conf),
        segs=segments(c),
    )
    return {(None if r["value"] == UNKNOWN else r["value"], r["period"]): r for r in rows}


def may_see_keys(c: sqlite3.Connection, auth: Any, who: Any, conf: Row | None = None) -> bool:
    """The owner, an admin, or a person or bot the settings name in key_readers."""
    if admin(auth, who):
        return True
    settings: Row = conf or G.settings(c)
    return who.actor in (settings.get("key_readers") or [])


def computed(
    c: sqlite3.Connection,
    auth: Any,
    who: Any,
    cut: str = "channel",
    grain: str = "month",
    month: str | None = None,
    week: str | None = None,
    channel: str | None = None,
    segment: str | None = None,
    plan: str | None = None,
    customers: bool = False,
) -> Row:
    """The computed figures for a cut, a grain and an optional period and filter. `customers`: each window's rule and
    counts, and for a reader allowed to see them, the keys and churn dates. Cut by channel, monthly, a cohort with a
    typed actual that differs carries it in `typed` (growth.typed_beside), for the channel's owner to choose."""
    if cut not in CUTS:
        raise Problem("validation", f"by is {'|'.join(CUTS)}", 422)
    if grain not in GRAINS:
        raise Problem("validation", f"grain is {'|'.join(GRAINS)}", 422)
    if month and week:
        raise Problem("validation", "Give month or week, not both", 422)
    if customers and not (month or week):
        # The keys behind every cohort of a team's whole history is an unbounded answer: one period at a time.
        raise Problem("validation", "customers needs a month or a week", 422)
    conf: Row = G.settings(c)
    since = until = None
    try:
        if month:
            since = date.fromisoformat(month + "-01")
            until = period(since, "month")[2]
            if grain == "week":
                # Every week that touches the month, whole: a week cut at the month's edge would count part of its
                # cohort under a rule that names the whole week.
                since, until = period(since, "week")[1], period(until, "week")[2]
        if week:
            _, since, until = period(date.fromisoformat(week), "week")
            grain = "week"
    except ValueError:
        raise Problem("validation", "month is YYYY-MM and week is a date (YYYY-MM-DD)", 422) from None
    everyone = rows = facts(c, since, until)
    slugs = set(_slugs(c).values())
    segs = segments(c)
    if segs.rows and cut == "segment":
        segs.counts = new_by_segment(everyone, grain, segs)
        segs.filtered = bool(channel or plan)
    if channel:
        rows = [f for f in rows if (f.channel if f.channel in slugs else UNKNOWN) == channel]
    if segment and segs.rows:
        wanted = segs.labels.get(segment.strip().lower(), segment.strip().lower())
        rows = [f for f in rows if segs.slug(f) == wanted]
    elif segment:
        rows = [f for f in rows if f.segment == segment]
    if plan:
        rows = [f for f in rows if f.plan == plan]
    keys = customers and may_see_keys(c, auth, who, conf)
    typed = typed_cohorts(c)
    daily = None
    if grain == "week" and (cut in ("channel", "all") or (cut == "segment" and segs.rows)) and rows:
        daily = daily_spend(c, typed, min(f.became for f in rows), max(f.became for f in rows))
    cohorts = compute(
        rows,
        today(conf),
        cut=cut,
        grain=grain,
        channels=slugs,
        typed=typed,
        conf=conf,
        min_cohort=min_cohort(conf),
        trace=customers,
        keys=keys,
        daily=daily,
        segs=segs,
    )
    if cut == "channel" and grain == "month":
        # A typed actual that differs, beside its computed figure, and whether this reader may choose (growth.beside).
        G.typed_beside(c, cohorts, who.actor, bool(admin(auth, who)))
    note = {
        "segment": ("spend is not split by channel or plan within a segment" if segs.filtered else None)
        if segs.rows
        else "spend is not split by segment",
        "plan": "spend is not split by plan",
    }.get(cut)
    if grain == "week" and not note:
        statuses = {r["cac"]["status"] for r in cohorts}
        if daily is None or ("spend_monthly" in statuses and "ok" not in statuses):
            note = "spend is monthly"
        elif "spend_monthly" in statuses:
            note = "weeks touching a month with typed spend say monthly"
            if daily.bot_cost:  # the weeks that show a CAC still leave bot cost out
                note += "; agent cost is monthly"
        elif daily.bot_cost:
            note = "weekly CAC is daily ad spend: agent cost is monthly"
    out: Row = {
        "by": cut,
        "grain": grain,
        "month": month,
        "week": week,
        "as_of": today(conf).isoformat(),
        "min_cohort": min_cohort(conf),
        "computed_from": len(rows),
        "cohorts": cohorts,
        "cac_note": note,
        "keys_visible": keys,
        "loads": GC.loads(c, 5),
    }
    if segs.rows:
        out["health"] = health(everyone, slugs, segs)  # the team's, whatever the filter: a segment's read names it too
    return out


def install(app: Any, store: Any, auth: Any) -> None:
    @app.get("/api/v2/growth/computed")
    def growth_computed(  # pyright: ignore[reportUnusedFunction]
        request: Request,
        by: str = "channel",
        grain: str = "month",
        month: str | None = None,
        week: str | None = None,
        channel: str | None = None,
        segment: str | None = None,
        plan: str | None = None,
        customers: bool = False,
    ) -> Row:
        """Cohorts computed from customer facts, cut `by` channel, segment, plan or all, at `grain` week or month, each
        figure with its count, denominator, status and origin. `customers`: the rule behind each retention figure and,
        for the owner, admins and the key readers, the customer keys."""
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            return computed(c, auth, who, by, grain, month, week, channel, segment, plan, customers)
