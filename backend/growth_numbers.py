"""The Growth page's numbers: one read that tells the story of growth, for anyone signed in.

`GET /api/v2/growth/numbers?months=12` (1 to 24, default 12):
- `months`: the newest N months with cohort rows, oldest first.
- `series`: one per month, every channel together (growth.economics' total, with revenue and margin from
  growth.aggregate over the same rows). A figure not recorded is null, never 0.
- `channels`: each live channel in its sort order with the months it has cohort rows for, then Unknown when it has any.
  Each month carries the channel's own figures, revenue and margin included.
- `kpis`: the KPI tree by growth role, each with its last 24 readings as the sparkline plots them (one value per period,
  partials left out, oldest first). A KPI with no growth field is left out; `other` holds one with a stage or loop but
  no role.
- `markers`: the moves of marketing tasks that start a test or finish it, newest first, at most 200. Each move is one
  marker, so a task moved twice gives two. The window is the first month's start to now (the last twelve months when
  there are no cohorts).

The tasks, KPIs and channels are read as their own routes read them: a marker shows only a task the reader may see
(auth.task_sql, as task lists do). `may_edit` is who may record cohorts: an admin or a bot (growth.py's cohort rule).
"""
from datetime import datetime, timezone

from fastapi import Request

from . import growth as G
from . import hubdb as H
from . import kpis as K
from . import marketing_tasks as MT
from .brand import admin
from .store import Problem

MONTHS_DEFAULT, MONTHS_MIN, MONTHS_MAX = 12, 1, 24
MARKERS_MAX = 200
KPI_KEYS = ("id", "name", "unit", "latest", "latest_at", "target", "goal_id", "parent_kpi_id", "growth_role", "stage",
            "loop_name", "loop_step")
SERIES_KEYS = ("spend", "bot_cost", "new_customers", "cac", "kept_d30", "kept_d90", "kept_d180", "expected_months",
               "ltv", "ltv_cac", "payback_months", "quality", "revenue_month", "margin_pct")
# The marketing tasks' steps that start a test or finish it, by step id: (kind, outcome).
MOVES = {MT.step_id("testing"): ("started", None), **{MT.step_id(key): ("finished", key) for key in MT.TERMINAL}}


def _month(row, month):
    """A channel's or the total's figures for one month."""
    return {"month": month, **{key: row.get(key) for key in SERIES_KEYS}}


def _window(c, months):
    """The newest `months` months with cohort rows, oldest first."""
    return sorted(r[0] for r in c.execute("SELECT DISTINCT month FROM growth_cohorts ORDER BY month DESC LIMIT ?",
                                          (months,)))


def _since(window):
    """Where the markers' window starts: the first month's first day, else the first day of the last twelve months."""
    if window:
        return window[0] + "-01T00:00:00"
    now = datetime.now(timezone.utc)
    index = now.year * 12 + now.month - 12
    return f"{index // 12:04d}-{index % 12 + 1:02d}-01T00:00:00"


def _money(c, month, conf):
    """Revenue and margin for one month, per channel and in total, over the rows growth.economics adds up."""
    groups = {}
    rows = G.cohorts(c, month=month, limit=G.COHORTS_MAX)
    for row in rows:
        groups.setdefault(row["channel"] or G.UNKNOWN, []).append(row)
    return {slug: G.aggregate(group, conf) for slug, group in groups.items()}, G.aggregate(rows, conf)


def _with_money(row, money):
    return {**row, "revenue_month": money["revenue_month"], "margin_pct": money["margin_pct"]}


def _channels(c, window, by_month):
    live = H._rows(c.execute("SELECT slug, name, stage, owner_ref FROM growth_channels WHERE retired_at IS NULL "
                             "ORDER BY sort, name COLLATE NOCASE"))

    def series(slug):
        return [_month(by_month[m][slug], m) for m in window if slug in by_month[m]]
    out = [{**channel, "series": series(channel["slug"])} for channel in live]
    if any(G.UNKNOWN in by_month[m] for m in window):
        out.append({"slug": G.UNKNOWN, "name": "Unknown", "stage": None, "owner_ref": None,
                    "series": series(G.UNKNOWN)})
    return out


def _points(readings):
    """A KPI's series as kpis.spark plots it: one value per period (a later reading of a period replaces an earlier
    one), partial readings left out, oldest first, the last K.SPARK."""
    by_period = {}
    for r in readings:
        if r.get("quality") != "partial":
            by_period[r["period_end"]] = r
    return [{"at": period, "value": by_period[period]["value"]} for period in sorted(by_period)][-K.SPARK:]


def _kpis(c, hidden):
    tree = [k for k in G.kpis(c, hidden)["kpis"]
            if k["growth_role"] or k["stage"] or k["loop_name"] or k["loop_step"] is not None]
    readings = K.effective_many(c, [k["id"] for k in tree])
    views = [{**{key: k[key] for key in KPI_KEYS}, "series": _points(readings.get(k["id"], []))} for k in tree]
    return {"north_star": next((v for v in views if v["growth_role"] == "north_star"), None),
            "inputs": [v for v in views if v["growth_role"] == "input"],
            "guardrails": [v for v in views if v["growth_role"] == "guardrail"],
            "other": [v for v in views if not v["growth_role"]]}


def _markers(c, auth, who, since):
    visible = auth.task_sql(c, who)
    rows = H._rows(c.execute(
        "SELECT e.subject_id AS task_id, e.created_at, e.new, t.title, t.goal_id FROM history_events e "
        "JOIN tasks t ON t.id=e.subject_id "
        f"WHERE e.subject_type='task' AND e.kind='step' AND e.new IN ({','.join('?' * len(MOVES))}) AND t.type_id=? AND e.created_at>=? "
        f"AND t.id IN (SELECT id FROM tasks WHERE type_id=? AND {visible}) ORDER BY e.created_at DESC, e.id DESC LIMIT ?",
        [*MOVES, MT.TYPE_ID, since, MT.TYPE_ID, MARKERS_MAX]))
    tags = {}
    ids = list(dict.fromkeys(r["task_id"] for r in rows))
    if ids:
        for task_id, key in c.execute(
                "SELECT tt.task_id, tg.key FROM task_tags tt JOIN tags tg ON tg.id=tt.tag_id "
                f"WHERE tt.task_id IN ({','.join('?' * len(ids))}) AND tg.key LIKE 'channel:%' ORDER BY tg.key", ids):
            tags.setdefault(task_id, []).append(key[len("channel:"):])
    out = []
    for r in rows:
        kind, outcome = MOVES[r["new"]]
        out.append({"task_id": r["task_id"], "title": r["title"], "kind": kind, "outcome": outcome, "at": r["created_at"],
                    "channels": tags.get(r["task_id"], []), "goal_id": r["goal_id"]})
    return out


def numbers(c, auth, who, months, hidden):
    window = _window(c, months)
    conf = G.settings(c)
    totals, by_month = {}, {}
    for m in window:
        econ = G.economics(c, m)
        per, total = _money(c, m, conf)
        totals[m] = _with_money(econ["total"], total)
        by_month[m] = {row["channel"]: _with_money(row, per[row["channel"]]) for row in econ["rows"]}
    return {"months": window,
            "series": [_month(totals[m], m) for m in window],
            "channels": _channels(c, window, by_month),
            "kpis": _kpis(c, hidden),
            "markers": _markers(c, auth, who, _since(window)),
            "may_edit": who.role == "bot" or admin(auth, who)}


def install(app, store, auth):
    from .goal_routes import _hidden_owners

    @app.get("/api/v2/growth/numbers")
    def growth_numbers(request: Request, months: int = MONTHS_DEFAULT):
        """The Growth page's numbers: the months, every channel's and the total's CAC, LTV, retention, revenue and
        margin, the KPI tree with its last readings, and the marketing tests that started or finished. `months`: the
        newest 1 to 24 months."""
        who = request.state.identity
        auth.domain(who)
        if not MONTHS_MIN <= months <= MONTHS_MAX:
            raise Problem("validation", "months is between 1 and 24", 422)
        with store.read() as c:
            return numbers(c, auth, who, months, _hidden_owners(c, auth, who))
