# pyright: strict
"""Figures computed from customer facts (backend/growth_customers.py): new customers, retention, LTV, CAC and payback for
each cohort, cut by channel, segment, plan or all together, at weekly (Monday to Sunday) or monthly grain.

A cohort is the customer spells whose "became customer" date falls in the same period. For each one:

- `new_customers`: how many spells it holds.
- `kept_d30`, `kept_d90`, `kept_d180`: the share still customers on day N (no churned date, or one after became + N
  days). A window shows only once every customer in the cohort is at least N days old, that is from the period's last
  day + N days (`fills_on`); before then its status is `not_yet`, never 0%. A cohort under the team's minimum
  (`min_cohort`, default 30, never below 5) shows the count only, status `too_few`.
- `revenue_month`, `expected_months`, `ltv`: today's maths (growth.expected_months, revenue per month x margin) on the
  computed retention. Revenue per customer is the customer's own, else the typed figure for their channel's month, else
  the settings value; margin is the channel's month, else the settings. A cohort under the minimum shows `too_few`.
- `cac`, `payback_months`: by channel (and all together) only, from the month's typed spend and bot cost. Spend has no
  segment or plan (`not_split`); typed spend is monthly, so a weekly cohort says `spend_monthly`; Unknown has no spend.

Each figure is {value, status, origin: computed}, with `count` and `denominator` where it is a share. The trace lists
the customer keys behind a cohort, split into still customers and churned with the date, and the rule used; keys and
churn dates go only to the owner, admins and the people or bots in `growth_settings.key_readers`. Everyone else who
reads Growth gets the counts and the rule.

Figures are computed when read, from the stored facts and today's date in the team's time zone, so a load shows at once
and day 90 and day 180 fill in as cohorts age with nobody typing. A team's whole history (100,000 customers over three
years) computes in a few seconds.
"""

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, cast

from fastapi import Request

from . import brand as _brand_untyped
from . import growth as _growth_untyped
from . import growth_customers as GC
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


@dataclass(slots=True)
class _Group:
    value: str | None
    period: str
    start: date
    end: date
    rows: list[tuple[Fact, str | None]]


def today(conf: Row) -> date:
    return datetime.now(GC.zone(conf.get("time_zone"))).date()


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
) -> list[Row]:
    """Every cohort of `facts` cut by `cut` at `grain`, as of the date `on`.

    channels: the Growth channel slugs; any other channel is Unknown. typed: the typed cohorts, for the month's spend,
    revenue and margin. trace: add each window's rule; keys: and the customer keys behind it."""
    conf, typed, channels = conf or {}, typed or {}, channels or set()
    groups: dict[tuple[str | None, str], _Group] = {}
    for f in facts:
        slug = f.channel if f.channel in channels else None
        value = {"channel": slug or UNKNOWN, "segment": f.segment, "plan": f.plan, "all": "all"}[cut]
        key, start, end = period(f.became, grain)
        group = groups.get((value, key))
        if group is None:
            group = groups[(value, key)] = _Group(value, key, start, end, [])
        group.rows.append((f, slug))
    out = [_cohort(g, on, cut, grain, typed, conf, min_cohort, trace, keys) for g in groups.values()]
    out.sort(key=lambda r: (r["value"] == UNKNOWN, r["value"] is None, str(r["value"])))
    out.sort(key=lambda r: r["period"], reverse=True)
    return out


def _money(rows: list[tuple[Fact, str | None]], typed: Typed, conf: Row) -> tuple[float | None, float | None]:
    """(revenue per customer-month, profit per customer-month) over the cohort's customers, each customer's revenue its
    own, else its channel's typed month, else the settings; margin its channel's typed month, else the settings."""
    revenues: list[float] = []
    profits: list[float] = []
    for f, slug in rows:
        cohort = typed.get((slug, f.became.isoformat()[:7])) or {}
        revenue = f.revenue_month
        if revenue is None:
            revenue = cohort.get("revenue_month")
            if revenue is None:
                revenue = conf.get("revenue_month")
        margin = cohort.get("margin_pct")
        if margin is None:
            margin = conf.get("margin_pct")
        if revenue is not None:
            revenues.append(float(revenue))
            if margin is not None:
                profits.append(float(revenue) * float(margin) / 100)
    return _mean(revenues), _mean(profits)


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


def _spend(cut: str, value: str | None, grain: str, key: str, typed: Typed) -> tuple[float | None, str]:
    """(cost, status) for CAC: the month's typed spend plus bot cost for a channel, or every channel's for all."""
    if cut in ("segment", "plan"):
        return None, "not_split"
    if grain == "week":
        return None, "spend_monthly"
    if cut == "channel" and value == UNKNOWN:
        return None, "no_spend"
    if cut == "channel":
        rows = [typed[(value, key)]] if (value, key) in typed else []
    else:
        rows = [t for (slug, m), t in typed.items() if m == key and slug]
    costs = [(t.get("spend"), t.get("bot_cost")) for t in rows]
    costs = [(s, b) for s, b in costs if s is not None or b is not None]
    if not costs:
        return None, "no_spend"
    return float(sum((s or 0) + (b or 0) for s, b in costs)), "ok"


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
) -> Row:
    rows, start, end = group.rows, group.start, group.end
    n = len(rows)
    few = n < min_cohort
    view: Row = {
        "cut": cut,
        "value": group.value,
        "grain": grain,
        "period": group.period,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "computed_from": n,
        "too_few": few,
        "new_customers": _figure(n),
        "rule": _rule(cut, group.value, start, end),
    }
    rates: dict[int, float | None] = {}
    for window in WINDOWS:
        fills = end + timedelta(days=window)
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
    revenue, profit = _money(rows, typed, conf)
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
    cost, status = _spend(cut, group.value, grain, group.period, typed)
    cac = round(cost / n, 2) if cost is not None and n else None
    view["cac"] = _figure(cac, status)
    if cac is None:
        view["payback_months"] = _figure(None, status)
    else:
        payback = round(cac / profit, 2) if profit else None
        view["payback_months"] = _figure(payback, "ok" if payback is not None else "missing")
    return view


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
    sql = "SELECT customer_key, became, churned, channel, segment, plan, revenue_month FROM growth_customers"
    return [
        Fact(key, date.fromisoformat(became), date.fromisoformat(churned) if churned else None, ch, seg, plan, rev)
        for key, became, churned, ch, seg, plan, rev in c.execute(
            sql + (" WHERE " + " AND ".join(where) if where else ""), args
        )
    ]


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
    counts, and for a reader allowed to see them, the keys and churn dates."""
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
        if week:
            _, since, until = period(date.fromisoformat(week), "week")
            grain = "week"
    except ValueError:
        raise Problem("validation", "month is YYYY-MM and week is a date (YYYY-MM-DD)", 422) from None
    rows = facts(c, since, until)
    slugs = set(_slugs(c).values())
    if channel:
        rows = [f for f in rows if (f.channel if f.channel in slugs else UNKNOWN) == channel]
    if segment:
        rows = [f for f in rows if f.segment == segment]
    if plan:
        rows = [f for f in rows if f.plan == plan]
    keys = customers and may_see_keys(c, auth, who, conf)
    cohorts = compute(
        rows,
        today(conf),
        cut=cut,
        grain=grain,
        channels=slugs,
        typed=typed_cohorts(c),
        conf=conf,
        min_cohort=min_cohort(conf),
        trace=customers,
        keys=keys,
    )
    note = {"segment": "spend is not split by segment", "plan": "spend is not split by plan"}.get(cut)
    return {
        "by": cut,
        "grain": grain,
        "as_of": today(conf).isoformat(),
        "min_cohort": min_cohort(conf),
        "computed_from": len(rows),
        "cohorts": cohorts,
        "cac_note": note or ("spend is monthly" if grain == "week" else None),
        "keys_visible": keys,
        "loads": GC.loads(c, 5),
    }


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
