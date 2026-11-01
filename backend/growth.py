"""Growth: the channels the company acquires customers through, where each number's truth lives, the ideas it tests,
monthly cohorts per channel and the unit economics they add up to.

- Channels (`growth_channels`): a performance or brand track and a stage (idea, testing, scaling, paused), an owner, a
  monthly budget, the source its numbers come from, and an optional guardrail (a KPI that must stay at or under a max).
- Sources (`gtm_sources`): which system is the truth for what, and how a bot reaches it. Two live sources for the
  same thing are a conflict the page shows.
- Ideas are tasks of the built-in Idea type with one `growth_ideas` row: hypothesis, prediction, the bet and KPI they
  serve, ICE scores, the test (dates, change, stop rule) and the verdict. Creating one goes through the ordinary task
  path (app.task_create), so history, notices and idempotency are a task's. A terminal verdict closes the task and
  emits `growth.verdict`.
- Segments (`growth_segments`): who the company sells to. Each is a market `segment` entity (its name and place live in
  the market graph) with a status (serving, testing, next, dropped), a motion, a buyer, a deal size and a why. Serving
  and next mirror Where we play on the Strategy page (strategy_market.set_status keeps both in step). Channels and
  ideas name the segments they reach.
- Cohorts (`growth_cohorts`): one row per channel per month (no channel is Unknown), optionally per segment, with
  spend, bot cost, new customers and the share kept at 30, 90 and 180 days. `economics` turns them into CAC, LTV and
  payback: for one segment its rows, else every row, a channel's segments added together.
- The KPI tree is four columns on `kpis` (parent, growth role, funnel stage, loop): at most one North Star.

Who may: everyone signed in reads. The owner and admins (people) write channels, sources, segments and settings; the
bot that owns a channel may change its why, stage and budget; any bot may add a testing segment and change a segment's
why, buyer and deal size; any bot or admin records cohorts. Anyone files an idea; its
assignee, its creator or an admin edits and scores it; its assignee or an admin starts its test and gives the verdict.
"""
import json
import math
from datetime import datetime, timedelta, timezone

from fastapi import Request

from . import hubdb as H
from . import market as MK
from . import market_spend as MS
from . import models as M
from . import strategy_market as SM
from .brand_gtm import admin
from .store import Problem

IDEA_TYPE = "idea"
IDEA_TYPE_NAMES = ("Idea", "Growth idea")
# The Idea type's steps, in board order: (growth status or task status, step name, task status). The idea's growth
# status moves its task to the step of the same key; a hand close lands on Closed, ahead of the verdict steps.
IDEA_STEPS = (("idea", "Idea", "open"), ("testing", "Testing", "doing"), ("paused", "Paused", "waiting"),
              ("review", "Review", "review"), ("ready", "Ready", "ready"), ("done", "Done", "done"),
              ("closed", "Closed", "closed"), ("won", "Won", "closed"), ("lost", "Lost", "closed"),
              ("inconclusive", "Inconclusive", "closed"), ("dropped", "Dropped", "closed"))
# The first two keep the ids an earlier seed gave them.
STEP_IDS = {"idea": f"{IDEA_TYPE}-open", "testing": f"{IDEA_TYPE}-doing"}


def step_id(key):
    return STEP_IDS.get(key, f"{IDEA_TYPE}-{key}")
TERMINAL = ("won", "lost", "inconclusive", "dropped")
ROLES = ("north_star", "input", "guardrail")
STAGES = ("reach", "acquire", "activate", "retain", "refer")
KPI_FIELDS = ("parent_kpi_id", "growth_role", "stage", "loop_name", "loop_step")
UNKNOWN = "unknown"
LIFETIME_CAP = 60          # months: a retention curve that never decays still counts at most five years
IDEAS_PAGE, IDEAS_MAX = 200, 500
COHORTS_PAGE, COHORTS_MAX = 500, 2000
VERDICTS_DAYS = 14
DUE_DAYS = 7
CHANNEL_FIELDS = ("id", "slug", "name", "track", "stage", "owner_ref", "budget_month", "source_id", "why",
                  "guardrail_kpi_id", "guardrail_max", "sort", "retired_at")
SOURCE_FIELDS = ("id", "truth_for", "system", "reach_kind", "reach_ref", "may", "owner_ref", "retired_at")
OWNER_PATCH = {"why", "stage", "budget_month"}       # what the bot that owns a channel may change
SEGMENT_FIELDS = ("id", "segment_id", "status", "motion", "who_buys", "deal_size", "why", "owner_ref", "sort")
SEGMENT_ORDER = ("serving", "testing", "next", "dropped")
BOT_SEGMENT_PATCH = {"why", "who_buys", "deal_size"}  # what any bot may change on a segment
SEGMENTS_MAX = 200
# Segments in the page's order (growth_segments as s, its market entity as e): by status, then sort, then name.
_SEGMENT_SORT = ("CASE s.status " + " ".join(f"WHEN '{st}' THEN {i}" for i, st in enumerate(SEGMENT_ORDER))
                 + " END, s.sort, coalesce(e.name, s.segment_id) COLLATE NOCASE, s.id")


def ensure(c):
    """The built-in Idea task type and its steps: its own id, named Idea unless a team's own type already has that
    name. A step missing from an older seed is added; one a team renamed keeps its name."""
    if not H._one(c, "SELECT id FROM task_types WHERE id=?", (IDEA_TYPE,)):
        taken = {r[0].lower() for r in c.execute("SELECT name FROM task_types")}
        name = next((n for n in IDEA_TYPE_NAMES if n.lower() not in taken), None)
        if not name:
            return None
        now = H.now()
        c.execute("INSERT INTO task_types (id, name, created_at, updated_at) VALUES (?,?,?,?)", (IDEA_TYPE, name, now, now))
    names = {r[0].lower() for r in c.execute("SELECT name FROM task_steps WHERE type_id=?", (IDEA_TYPE,))}
    have = {r[0] for r in c.execute("SELECT id FROM task_steps WHERE type_id=?", (IDEA_TYPE,))}
    for position, (key, name, status) in enumerate(IDEA_STEPS):
        sid = step_id(key)
        if sid in have:
            # Only a step that moved is written: an UPDATE that matches nothing is no change, and a changed row bumps the
            # roster clock (backend/roster_clock.py), which every start would otherwise do for every step.
            c.execute("UPDATE task_steps SET position=? WHERE id=? AND position IS NOT ?", (position, sid, position))
        elif name.lower() not in names:
            c.execute("INSERT INTO task_steps (id, type_id, name, position, status) VALUES (?,?,?,?,?)",
                      (sid, IDEA_TYPE, name, position, status))
    return IDEA_TYPE


def _today():
    return datetime.now(timezone.utc).date().isoformat()


def _num(value):
    return None if value is None else float(value)


# ----------------------------------------------------------------------------- economics (pure)
def expected_months(d30, d90=None, d180=None, cap=LIFETIME_CAP):
    """Expected customer lifetime in months from the share kept at 30, 90 and 180 days (percent).

    The retention curve runs through (0, 100%) and each known point; the area under it to the last point is the
    trapezoid sum, and past it the last known monthly decay carries on geometrically to `cap`. None without d30."""
    if d30 is None:
        return None
    points = [(0.0, 1.0)] + [(t, max(0.0, float(v)) / 100) for t, v in ((1, d30), (3, d90), (6, d180)) if v is not None]
    area = sum((t1 - t0) * (r0 + r1) / 2 for (t0, r0), (t1, r1) in zip(points, points[1:], strict=False))
    (t0, r0), (t1, r1) = points[-2], points[-1]
    left = cap - t1
    if r1 <= 0 or left <= 0:
        return round(min(area, cap), 2)
    decay = (r1 / r0) ** (1 / (t1 - t0)) if r0 > 0 else 0.0
    if decay >= 1:
        tail = r1 * left
    elif decay <= 0:
        tail = 0.0
    else:
        tail = r1 * (1 - decay ** left) / -math.log(decay)
    return round(min(area + tail, cap), 2)


def economics_row(cohort, settings):
    """One channel's month: CAC, expected months, LTV, LTV:CAC and payback. Unknown has no spend, so no CAC or LTV."""
    revenue = cohort.get("revenue_month") if cohort.get("revenue_month") is not None else settings.get("revenue_month")
    margin = cohort.get("margin_pct") if cohort.get("margin_pct") is not None else settings.get("margin_pct")
    unknown = cohort.get("channel") is None
    spend = None if unknown else cohort.get("spend")
    bot_cost = None if unknown else cohort.get("bot_cost")
    new = cohort.get("new_customers")
    cost = (spend or 0) + (bot_cost or 0)
    cac = round(cost / new, 2) if not unknown and new and (spend is not None or bot_cost is not None) else None
    months = expected_months(cohort.get("kept_d30"), cohort.get("kept_d90"), cohort.get("kept_d180"))
    per_month = cohort.get("profit_month")
    if per_month is None:
        per_month = revenue * margin / 100 if revenue is not None and margin is not None else None
    ltv = round(per_month * months, 2) if per_month is not None and months is not None and not unknown else None
    return {"spend": spend, "bot_cost": bot_cost, "new_customers": new, "cac": cac,
            "kept_d30": cohort.get("kept_d30"), "kept_d90": cohort.get("kept_d90"), "kept_d180": cohort.get("kept_d180"),
            "expected_months": months, "ltv": ltv,
            "ltv_cac": round(ltv / cac, 2) if ltv is not None and cac else None,
            "payback_months": round(cac / per_month, 2) if cac is not None and per_month else None,
            "quality": cohort.get("quality") or "actual", "sources": cohort.get("sources") or {}}


def economics_total(cohorts, settings):
    """Every channel together (see `aggregate`)."""
    total = aggregate(cohorts, settings)
    return {**economics_row({**total, "channel": "all"}, settings), "channel": None, "name": "All channels",
            "track": None}


def aggregate(cohorts, settings):
    """Cohorts as one: spend, bot cost and customers summed; retention, revenue and profit per customer-month
    (revenue x margin, each cohort's own) weighted by new customers. The margin shown is that profit over that revenue,
    so a high-revenue low-margin cohort never lends its revenue to another's margin. Unknown rows add no spend."""
    def value(c, key, fallback):
        return c.get(key) if c.get(key) is not None else fallback

    def profit(c):
        revenue, margin = value(c, "revenue_month", settings.get("revenue_month")), value(c, "margin_pct", settings.get("margin_pct"))
        return revenue * margin / 100 if revenue is not None and margin is not None else None

    def weighted(key, fallback=None):
        pairs = [((key(c) if callable(key) else value(c, key, fallback)), c.get("new_customers") or 0) for c in cohorts]
        pairs = [(v, w) for v, w in pairs if v is not None and w]
        weight = sum(w for _, w in pairs)
        return round(sum(v * w for v, w in pairs) / weight, 2) if weight else None

    def summed(key, rows):
        values = [c[key] for c in rows if c.get(key) is not None]
        return round(sum(values), 2) if values else None

    paid = [c for c in cohorts if c.get("channel") is not None]
    total = {"spend": summed("spend", paid), "bot_cost": summed("bot_cost", paid),
             "new_customers": summed("new_customers", cohorts), "kept_d30": weighted("kept_d30"),
             "kept_d90": weighted("kept_d90"), "kept_d180": weighted("kept_d180"),
             "revenue_month": weighted("revenue_month", settings.get("revenue_month")), "profit_month": weighted(profit),
             "quality": "estimate" if any(c.get("quality") == "estimate" for c in cohorts) else "actual", "sources": {}}
    if total["new_customers"] is not None:
        total["new_customers"] = int(total["new_customers"])
    revenue, per_month = total["revenue_month"], total["profit_month"]
    total["margin_pct"] = round(per_month / revenue * 100, 2) if revenue and per_month is not None else None
    return total


# ----------------------------------------------------------------------------- reads
def settings(c):
    row = H._one(c, "SELECT * FROM growth_settings WHERE id=1") or {}
    return {"unit": row.get("unit") or "customers", "revenue_month": row.get("revenue_month"),
            "margin_pct": row.get("margin_pct"), "verdict_win_pct": row.get("verdict_win_pct", 60.0),
            "framework": row.get("framework") or ""}


def channel_view(row, segments=()):
    return {**{k: row.get(k) for k in CHANNEL_FIELDS}, "segments": list(segments)}


def _segments_of(c, table, key, ids):
    """{channel or idea id: [growth segment id]} from a join table, in the segments' order."""
    if not ids:
        return {}
    out = {}
    for r in c.execute(f"SELECT j.{key}, j.segment_row_id FROM {table} j JOIN growth_segments s ON s.id=j.segment_row_id "
                       f"LEFT JOIN market_entities e ON e.id=s.segment_id WHERE j.{key} IN ({','.join('?' * len(ids))}) "
                       f"ORDER BY {_SEGMENT_SORT}", list(ids)):
        out.setdefault(r[0], []).append(r[1])
    return out


def channel_full(c, row):
    return channel_view(row, _segments_of(c, "growth_channel_segments", "channel_id", [row["id"]]).get(row["id"], []))


def channels(c, include_retired=False):
    sql = "SELECT * FROM growth_channels" + ("" if include_retired else " WHERE retired_at IS NULL")
    rows = H._rows(c.execute(sql + " ORDER BY sort, name COLLATE NOCASE"))
    segs = _segments_of(c, "growth_channel_segments", "channel_id", [r["id"] for r in rows])
    return [channel_view(r, segs.get(r["id"], [])) for r in rows]


def channel(c, slug):
    return H._one(c, "SELECT * FROM growth_channels WHERE slug=?", (slug,))


def source_view(row):
    return {k: row.get(k) for k in SOURCE_FIELDS}


def sources(c, include_retired=False):
    rows = H._rows(c.execute("SELECT * FROM gtm_sources" + ("" if include_retired else " WHERE retired_at IS NULL")
                             + " ORDER BY truth_for COLLATE NOCASE, system COLLATE NOCASE"))
    live = {}
    for r in rows:
        if not r["retired_at"]:
            live.setdefault(r["truth_for"].strip().lower(), []).append(r["truth_for"])
    return {"sources": [source_view(r) for r in rows],
            "conflicts": sorted(names[0] for names in live.values() if len(names) > 1)}


def _visible(auth, c, who):
    return f"g.task_id IN (SELECT id FROM tasks WHERE {auth.task_sql(c, who)})"


IDEA_SQL = ("SELECT g.*, t.title, t.status AS task_status, t.owner AS assignee, t.requester AS created_by, "
            "t.created_at AS task_created FROM growth_ideas g JOIN tasks t ON t.id=g.task_id")


def _ice(row):
    scores = [row.get(k) for k in ("ice_i", "ice_c", "ice_e")]
    return round(sum(scores) / 3, 1) if all(s is not None for s in scores) else None


def _bet_titles(c, who, bet_ids):
    """Bet statements as the reader may see them: the owner reads live bets, everyone else the published version's,
    so an unpublished bet's words never reach a member or a bot."""
    if not bet_ids:
        return {}
    from . import goal_routes, strategy
    if who is not None and goal_routes.company_editor(who):
        return {r[0]: r[1] for r in c.execute(
            f"SELECT id, statement FROM strategy_bets WHERE id IN ({','.join('?' * len(bet_ids))})", list(bet_ids))}
    wanted = set(bet_ids)
    return {b["id"]: b.get("statement") for b in ((strategy.current(c) or {}).get("bets") or []) if b.get("id") in wanted}


def idea_views(c, rows, who=None, hidden=()):
    """Ideas as the API shows them, with channels, bet titles, relations and subtask counts read in a few queries."""
    if not rows:
        return []
    ids = [r["task_id"] for r in rows]
    marks = ",".join("?" * len(ids))
    slugs = {}
    for r in c.execute(f"SELECT ic.task_id, ch.slug FROM growth_idea_channels ic JOIN growth_channels ch "
                       f"ON ch.id=ic.channel_id WHERE ic.task_id IN ({marks}) ORDER BY ch.sort, ch.name", ids):
        slugs.setdefault(r[0], []).append(r[1])
    follow, depends = {}, {}
    for r in c.execute(f"SELECT from_task, to_task, kind FROM task_relations WHERE kind IN ('follow_up','blocks') "
                       f"AND ((kind='follow_up' AND from_task IN ({marks})) OR (kind='blocks' AND to_task IN ({marks}))) "
                       "ORDER BY created_at", ids + ids):
        if r[2] == "follow_up":
            follow.setdefault(r[0], r[1])
        else:
            depends.setdefault(r[1], []).append(r[0])
    subtasks = {r[0]: {"total": r[1], "done": r[2] or 0} for r in c.execute(
        f"SELECT rel.to_task, count(*), sum(t.status IN ('done','closed')) FROM task_relations rel "
        f"JOIN tasks t ON t.id=rel.from_task WHERE rel.kind='parent' AND rel.to_task IN ({marks}) GROUP BY rel.to_task",
        ids)}
    segs = _segments_of(c, "growth_idea_segments", "task_id", ids)
    bets = _bet_titles(c, who, sorted({r["bet_id"] for r in rows if r.get("bet_id")}))
    kpi_marks = sorted({r[k] for r in rows for k in ("kpi_id", "judged_kpi_id") if r[k]})
    kpi_names = {r["id"]: r["name"] for r in H._rows(c.execute(
        f"SELECT id, name, owner FROM kpis WHERE id IN ({','.join('?' * len(kpi_marks))})", kpi_marks))
        if r["owner"] not in hidden} if kpi_marks else {}
    out = []
    for r in rows:
        tid = r["task_id"]
        out.append({
            "task_id": tid, "title": r["title"], "task_status": r["task_status"], "assignee": r["assignee"],
            "created_by": r["created_by"], "created_at": r["task_created"] or r["created_at"],
            "updated_at": r["updated_at"], "hypothesis": r["hypothesis"], "prediction": r["prediction"],
            "bet_id": r["bet_id"], "bet_title": bets.get(r["bet_id"]), "kpi_id": r["kpi_id"],
            "kpi_name": kpi_names.get(r["kpi_id"]), "judged_kpi_id": r["judged_kpi_id"],
            "judged_kpi_name": kpi_names.get(r["judged_kpi_id"]), "channels": slugs.get(tid, []),
            "segments": segs.get(tid, []),
            "ice_i": r["ice_i"], "ice_c": r["ice_c"], "ice_e": r["ice_e"], "ice": _ice(r), "status": r["status"],
            **{k: r[k] for k in ("source_kind", "source_ref", "test_from", "test_to", "change", "stop_if", "min_events",
                                 "audience", "external_ref", "result", "learning", "bet_effect", "confidence",
                                 "verdict_at", "verdict_by")},
            "follow_up_of": follow.get(tid), "depends_on": depends.get(tid, []),
            "subtasks": subtasks.get(tid, {"total": 0, "done": 0})})
    return out


def ideas(c, auth, who, status=None, bet_id=None, channel_slug=None, kpi_id=None, q=None, limit=IDEAS_PAGE,
          segment=None):
    where, args = [_visible(auth, c, who)], []
    if segment:
        found = segment_row(c, segment)
        if not found:
            return []
        where.append("g.task_id IN (SELECT task_id FROM growth_idea_segments WHERE segment_row_id=?)")
        args.append(found["id"])
    if status:
        where.append("g.status=?")
        args.append(status)
    if bet_id:
        where.append("g.bet_id=?")
        args.append(bet_id)
    if kpi_id:
        where.append("(g.kpi_id=? OR g.judged_kpi_id=?)")
        args += [kpi_id, kpi_id]
    if channel_slug:
        where.append("g.task_id IN (SELECT ic.task_id FROM growth_idea_channels ic JOIN growth_channels ch "
                     "ON ch.id=ic.channel_id WHERE ch.slug=?)")
        args.append(channel_slug)
    if q:
        where.append("(t.title LIKE ? ESCAPE '\\' OR g.hypothesis LIKE ? ESCAPE '\\')")
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        args += [like, like]
    limit = max(1, min(int(limit or IDEAS_PAGE), IDEAS_MAX))
    rows = H._rows(c.execute(IDEA_SQL + " WHERE " + " AND ".join(where)
                             + " ORDER BY t.created_at DESC, g.task_id LIMIT ?", args + [limit]))
    return idea_views(c, rows, who, _hidden(c, auth, who))


def _hidden(c, auth, who):
    from . import goal_routes
    return goal_routes._hidden_owners(c, auth, who)


def idea(c, auth, who, task_id):
    resolved = auth.resolve_task(c, who, task_id)
    rows = H._rows(c.execute(IDEA_SQL + " WHERE g.task_id=? AND " + _visible(auth, c, who), (resolved,)))
    if not rows:
        raise Problem("not_found", "No such idea", 404)
    return idea_views(c, rows, who, _hidden(c, auth, who))[0]


def _cohort_view(row, slugs):
    return {"id": row["id"], "channel": slugs.get(row["channel_id"]) if row["channel_id"] else None,
            "segment_id": row.get("segment_id"), "month": row["month"],
            **{k: row[k] for k in ("spend", "bot_cost", "new_customers", "kept_d30", "kept_d90", "kept_d180",
                                   "revenue_month", "margin_pct", "quality")},
            "sources": json.loads(row["sources"] or "{}"), "updated_by": row["updated_by"],
            "updated_at": row["updated_at"]}


def _slugs(c):
    return {r[0]: r[1] for r in c.execute("SELECT id, slug FROM growth_channels")}


def cohorts(c, channel_slug=None, month=None, limit=COHORTS_PAGE, segment=None):
    where, args = [], []
    if segment:
        found = segment_row(c, segment)
        if not found:
            return []
        where.append("segment_id=?")
        args.append(found["id"])
    if channel_slug:
        if channel_slug == UNKNOWN:
            where.append("channel_id IS NULL")
        else:
            found = channel(c, channel_slug)
            if not found:
                return []
            where.append("channel_id=?")
            args.append(found["id"])
    if month:
        where.append("month=?")
        args.append(month)
    limit = max(1, min(int(limit or COHORTS_PAGE), COHORTS_MAX))
    slugs = _slugs(c)
    rows = H._rows(c.execute("SELECT * FROM growth_cohorts" + (" WHERE " + " AND ".join(where) if where else "")
                             + " ORDER BY month DESC, channel_id IS NULL, channel_id, segment_id IS NOT NULL, segment_id "
                             "LIMIT ?", args + [limit]))
    return [_cohort_view(r, slugs) for r in rows]


def segment_row(c, ref):
    """A growth segment by its own id or its market segment's id."""
    if not ref:
        return None
    return H._one(c, "SELECT * FROM growth_segments WHERE id=? OR segment_id=? ORDER BY id=? DESC LIMIT 1",
                  (ref, ref, ref))


def segments(c, auth=None, who=None):
    """Every segment, serving first, then testing, next and dropped, each by sort then name: its market name, the
    active channels that reach it, how many ideas the reader may see target it, and the new customers its cohorts
    counted in the latest month any cohort has."""
    rows = H._rows(c.execute(
        f"SELECT s.*, e.name FROM growth_segments s LEFT JOIN market_entities e ON e.id=s.segment_id "
        f"ORDER BY {_SEGMENT_SORT} LIMIT ?", (SEGMENTS_MAX,)))
    chips = {}
    for r in c.execute("SELECT cs.segment_row_id, ch.slug FROM growth_channel_segments cs JOIN growth_channels ch "
                       "ON ch.id=cs.channel_id WHERE ch.retired_at IS NULL ORDER BY ch.sort, ch.name COLLATE NOCASE"):
        chips.setdefault(r[0], []).append(r[1])
    visible = _visible(auth, c, who) if auth is not None else "1"
    counts = {r[0]: r[1] for r in c.execute(
        f"SELECT s.segment_row_id, count(*) FROM growth_idea_segments s JOIN growth_ideas g ON g.task_id=s.task_id "
        f"WHERE {visible} GROUP BY s.segment_row_id")}
    month = (H._one(c, "SELECT max(month) AS m FROM growth_cohorts") or {}).get("m")
    new = {r[0]: r[1] for r in c.execute(
        "SELECT segment_id, sum(new_customers) FROM growth_cohorts WHERE month=? AND segment_id IS NOT NULL "
        "GROUP BY segment_id", (month,))} if month else {}
    return [{**{k: r.get(k) for k in SEGMENT_FIELDS}, "name": r.get("name") or r["segment_id"],
             "channels": chips.get(r["id"], []), "ideas": counts.get(r["id"], 0),
             "new_customers_last_month": new.get(r["id"])} for r in rows]


def segment(c, ref, auth=None, who=None):
    row = segment_row(c, ref)
    if not row:
        raise Problem("not_found", f"No segment {ref}", 404)
    return next(s for s in segments(c, auth, who) if s["id"] == row["id"])


def _latest_value(c, kpi_id):
    from . import kpis as K
    newest = K.latest(K.readings(c, kpi_id, effective=True)) if kpi_id else None
    return newest


def guardrails(c, hidden=()):
    """Channels with a guardrail KPI and its latest value. `hidden`: owners whose KPIs the caller may not read
    (goal_routes._hidden_owners), as GET /growth/kpis; their guardrails are left out."""
    out = []
    for ch in H._rows(c.execute("SELECT * FROM growth_channels WHERE retired_at IS NULL AND guardrail_kpi_id IS NOT NULL "
                                "AND guardrail_max IS NOT NULL ORDER BY sort, name")):
        kpi = H._one(c, "SELECT name, owner FROM kpis WHERE id=?", (ch["guardrail_kpi_id"],)) or {}
        if kpi.get("owner") in hidden:
            continue
        newest = _latest_value(c, ch["guardrail_kpi_id"])
        value = newest["value"] if newest else None
        out.append({"channel": ch["slug"], "kpi_id": ch["guardrail_kpi_id"], "kpi_name": kpi.get("name"),
                    "value": value, "max": ch["guardrail_max"], "breached": value is not None and value > ch["guardrail_max"]})
    return out


def economics(c, month=None, hidden=(), segment=None):
    """One month by channel. With `segment` (a growth segment id or its market segment id) only that segment's rows.
    Without, one row a channel: its row with no segment, which is the channel's whole month when it has one (the
    segment rows beside it are a breakdown), else its segment rows added together."""
    seg = None
    if segment:
        seg = segment_row(c, segment)
        if not seg:
            raise Problem("not_found", f"No segment {segment}", 404)
    months = [r[0] for r in c.execute("SELECT DISTINCT month FROM growth_cohorts" + (" WHERE segment_id=?" if seg else "")
                                      + " ORDER BY month DESC", (seg["id"],) if seg else ())]
    month = month or (months[0] if months else None)
    conf = settings(c)
    by_slug = {r["slug"]: r for r in H._rows(c.execute("SELECT * FROM growth_channels"))}
    rows, inputs, groups = [], [], {}
    for cohort in (cohorts(c, month=month, limit=COHORTS_MAX, segment=seg["id"] if seg else None) if month else []):
        groups.setdefault(cohort["channel"], []).append(cohort)
    for slug, group in groups.items():
        whole = [g for g in group if not g.get("segment_id")]
        group = whole or group
        inputs += group
        ch = by_slug.get(slug) if slug else None
        if len(group) > 1:
            sources = {}
            for g in group:
                sources.update(g.get("sources") or {})
            cohort = {**aggregate(group, conf), "channel": slug, "sources": sources}
        else:
            cohort = group[0]
        rows.append({"channel": slug or UNKNOWN, "name": ch["name"] if ch else "Unknown",
                     "track": ch["track"] if ch else None, **economics_row(cohort, conf)})
    blank = {k: None for k in ("spend", "bot_cost", "new_customers", "cac", "kept_d30", "kept_d90", "kept_d180",
                               "expected_months", "ltv", "ltv_cac", "payback_months", "quality")}
    if seg:
        # One segment's view keeps its own channels and Unknown, each blank until it has numbers for the month.
        linked = {r[0] for r in c.execute("SELECT channel_id FROM growth_channel_segments WHERE segment_row_id=?",
                                          (seg["id"],))}
        shown = {r["channel"] for r in rows}
        rows += [{"channel": ch["slug"], "name": ch["name"], "track": ch["track"], **blank, "sources": {}}
                 for ch in by_slug.values() if ch["id"] in linked and not ch.get("retired_at") and ch["slug"] not in shown]
        if UNKNOWN not in shown:
            rows.append({"channel": UNKNOWN, "name": "Unknown", "track": None, **blank, "sources": {}})
    elif not rows:
        # A month with no numbers yet still lists every active channel and Unknown, each blank, for the first entry.
        rows = [{"channel": ch["slug"], "name": ch["name"], "track": ch["track"], **blank, "sources": {}}
                for ch in by_slug.values() if not ch.get("retired_at")]
        rows.append({"channel": UNKNOWN, "name": "Unknown", "track": None, **blank, "sources": {}})
    rows.sort(key=lambda r: (r["channel"] == UNKNOWN, (by_slug.get(r["channel"]) or {}).get("sort", 0), r["name"]))
    total = economics_total(inputs, conf)
    if not inputs:
        total["quality"] = None
    return {"month": month, "months": months, "segment": seg["id"] if seg else None, "settings": conf, "rows": rows,
            "total": total, "guardrails": guardrails(c, hidden)}


def kpis(c, hidden=(), may_edit=lambda record: False):
    rows = H._rows(c.execute("SELECT * FROM kpis WHERE archived_at IS NULL ORDER BY created_at"))
    rows = [r for r in rows if r.get("owner") not in hidden]
    from . import kpis as K
    readings = K.effective_many(c, [r["id"] for r in rows])
    out = []
    for r in rows:
        newest = K.latest(readings.get(r["id"], []))
        link = H._one(c, "SELECT goal_id, target FROM goal_kpis WHERE kpi_id=? ORDER BY target IS NULL, created_at LIMIT 1",
                      (r["id"],)) or {}
        out.append({"id": r["id"], "name": r["name"], "unit": r.get("unit") or "",
                    "latest": newest["value"] if newest else None,
                    "latest_at": (newest.get("period_end") or newest.get("collected_at")) if newest else None,
                    "target": link.get("target"), "goal_id": link.get("goal_id"),
                    "may_edit": bool(may_edit(r)), **{k: r.get(k) for k in KPI_FIELDS}})
    return {"kpis": out}


def evidence(c, auth, who):
    rows = H._rows(c.execute("SELECT g.task_id, g.bet_id, g.bet_effect, g.confidence FROM growth_ideas g "
                             "WHERE g.bet_id IS NOT NULL AND g.verdict_at IS NOT NULL AND " + _visible(auth, c, who)
                             + " ORDER BY g.verdict_at"))
    by_bet = {}
    for r in rows:
        item = by_bet.setdefault(r["bet_id"], {"bet_id": r["bet_id"], "title": None,
                                               "supports": {"low": 0, "medium": 0, "high": 0},
                                               "weakens": {"low": 0, "medium": 0, "high": 0}, "none": 0, "ideas": []})
        item["ideas"].append(r["task_id"])
        if r["bet_effect"] in ("supports", "weakens"):
            item[r["bet_effect"]][r["confidence"] or "low"] += 1
        elif r["bet_effect"] == "none":
            item["none"] += 1
    for bid, title in _bet_titles(c, who, list(by_bet)).items():
        by_bet[bid]["title"] = title
    return {"bets": list(by_bet.values())}


def working(c, auth, who, limit=200):
    """Open tasks under an idea, or labelled `channel:<slug>`."""
    visible = auth.task_sql(c, who)
    rows = H._rows(c.execute(
        "SELECT t.id, t.title, t.owner, t.status, t.updated_at, rel.to_task AS idea_task_id, it.title AS idea_title, "
        "(SELECT substr(tg.key, 9) FROM task_tags tt JOIN tags tg ON tg.id=tt.tag_id WHERE tt.task_id=t.id "
        " AND tg.key LIKE 'channel:%' LIMIT 1) AS label_channel "
        "FROM tasks t LEFT JOIN task_relations rel ON rel.from_task=t.id AND rel.kind='parent' "
        "AND rel.to_task IN (SELECT task_id FROM growth_ideas) "
        "LEFT JOIN tasks it ON it.id=rel.to_task "
        f"WHERE t.status IN ('open','doing','waiting','review','ready') AND t.id IN (SELECT id FROM tasks WHERE {visible}) "
        "AND (rel.to_task IS NOT NULL OR EXISTS (SELECT 1 FROM task_tags tt JOIN tags tg ON tg.id=tt.tag_id "
        "WHERE tt.task_id=t.id AND tg.key LIKE 'channel:%')) ORDER BY t.updated_at DESC LIMIT ?", (limit,)))
    parents = sorted({r["idea_task_id"] for r in rows if r["idea_task_id"]})
    shown = set()
    if parents:
        shown = {r[0] for r in c.execute(f"SELECT id FROM tasks WHERE id IN ({','.join('?' * len(parents))}) "
                                         f"AND ({visible})", parents)}
    for r in rows:
        # A visible subtask of a private idea: the idea's id, title and channel stay with the idea.
        if r["idea_task_id"] and r["idea_task_id"] not in shown:
            r["idea_task_id"] = r["idea_title"] = None
    first_channel = {}
    ids = [r["idea_task_id"] for r in rows if r["idea_task_id"]]
    if ids:
        for r in c.execute(f"SELECT ic.task_id, ch.slug FROM growth_idea_channels ic JOIN growth_channels ch ON "
                           f"ch.id=ic.channel_id WHERE ic.task_id IN ({','.join('?' * len(ids))}) ORDER BY ch.sort", ids):
            first_channel.setdefault(r[0], r[1])
    return {"items": [{"task_id": r["id"], "title": r["title"], "idea_task_id": r["idea_task_id"],
                       "idea_title": r["idea_title"],
                       "channel": r["label_channel"] or first_channel.get(r["idea_task_id"]),
                       "assignee": r["owner"], "status": r["status"], "updated_at": r["updated_at"]} for r in rows]}


def week(c, auth, who, hidden=()):
    # Due: overdue, or ending within the coming week, so the week's plan sees it before the day it ends.
    due_by = (datetime.now(timezone.utc) + timedelta(days=DUE_DAYS)).strftime("%Y-%m-%d")
    since = (datetime.now(timezone.utc) - timedelta(days=VERDICTS_DAYS)).strftime("%Y-%m-%dT%H:%M:%S")
    tree = [k for k in kpis(c, hidden)["kpis"] if k["growth_role"]]
    readings = [{"kpi_id": k["id"], "name": k["name"], "unit": k["unit"], "growth_role": k["growth_role"],
                 "value": k["latest"], "at": k["latest_at"], "target": k["target"]} for k in tree]
    testing = ideas(c, auth, who, status="testing", limit=IDEAS_MAX)
    open_ideas = ideas(c, auth, who, status="idea", limit=IDEAS_MAX)
    top = sorted((i for i in open_ideas if i["ice"] is not None), key=lambda i: -i["ice"])[:10]
    recent = [i for i in ideas(c, auth, who, limit=IDEAS_MAX) if i["verdict_at"] and i["verdict_at"] >= since]
    recent.sort(key=lambda i: i["verdict_at"], reverse=True)
    return {"readings": readings,
            "tests_due": [i for i in testing if i["test_to"] and i["test_to"][:10] <= due_by],
            "top_ideas": top, "recent_verdicts": recent, "guardrails": [g for g in guardrails(c, hidden) if g["breached"]]}


# ----------------------------------------------------------------------------- writes
def _owner_ref(c, value):
    if value in (None, ""):
        return None
    actor = H.resolve_actor(c, value)
    if not actor or actor == H.KEEPER:
        raise Problem("not_found", f"No person or bot {value}", 404)
    return actor


def _kpi_ref(c, value, field):
    if value in (None, ""):
        return None
    if not H._one(c, "SELECT id FROM kpis WHERE id=?", (value,)):
        raise Problem("not_found", f"{field}: no KPI {value}", 404)
    return value


def _source_ref(c, value):
    if value in (None, ""):
        return None
    if not H._one(c, "SELECT id FROM gtm_sources WHERE id=?", (value,)):
        raise Problem("not_found", f"No source {value}", 404)
    return value


def channel_create(c, actor, body):
    H._writer(c, actor)
    if channel(c, body.slug):
        raise Problem("conflict", f"There is already a channel {body.slug}", 409)
    row = {"id": H.new_id(), "slug": body.slug, "name": body.name, "track": body.track, "stage": body.stage,
           "owner_ref": _owner_ref(c, body.owner_ref), "budget_month": body.budget_month,
           "source_id": _source_ref(c, body.source_id), "why": body.why,
           "guardrail_kpi_id": _kpi_ref(c, body.guardrail_kpi_id, "guardrail_kpi_id"),
           "guardrail_max": body.guardrail_max, "sort": body.sort, "created_at": H.now()}
    seg_ids = _segment_ids(c, body.segments)
    c.execute(f"INSERT INTO growth_channels ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", list(row.values()))
    _link(c, "growth_channel_segments", "channel_id", row["id"], seg_ids)
    H.event(c, actor, "growth.channel.create", row["id"], {"slug": body.slug})
    return channel_full(c, channel(c, body.slug))


def channel_update(c, actor, slug, fields):
    H._writer(c, actor)
    row = channel(c, slug)
    if not row:
        raise Problem("not_found", f"No channel {slug}", 404)
    if "owner_ref" in fields:
        fields["owner_ref"] = _owner_ref(c, fields["owner_ref"])
    if "source_id" in fields:
        fields["source_id"] = _source_ref(c, fields["source_id"])
    if "guardrail_kpi_id" in fields:
        fields["guardrail_kpi_id"] = _kpi_ref(c, fields["guardrail_kpi_id"], "guardrail_kpi_id")
    for required in ("name", "track", "stage", "why", "sort"):
        if required in fields and fields[required] is None:
            raise Problem("validation", f"{required} cannot be cleared", 422)
    if "segments" in fields:
        seg_ids = _segment_ids(c, fields.pop("segments") or [])
        before = _segments_of(c, "growth_channel_segments", "channel_id", [row["id"]]).get(row["id"], [])
        if sorted(before) != sorted(seg_ids):
            _link(c, "growth_channel_segments", "channel_id", row["id"], seg_ids)
            H.event(c, actor, "growth.channel.update", row["id"], {"slug": slug, "segments": seg_ids})
    changed = {k: v for k, v in fields.items() if row.get(k) != v}
    if changed:
        c.execute(f"UPDATE growth_channels SET {', '.join(k + '=?' for k in changed)} WHERE id=?",
                  list(changed.values()) + [row["id"]])
        H.event(c, actor, "growth.channel.update", row["id"], {"slug": slug, **changed})
    return channel_full(c, channel(c, slug))


def channel_retire(c, actor, slug):
    H._writer(c, actor)
    row = channel(c, slug)
    if not row:
        raise Problem("not_found", f"No channel {slug}", 404)
    if not row["retired_at"]:
        c.execute("UPDATE growth_channels SET retired_at=? WHERE id=?", (H.now(), row["id"]))
        H.event(c, actor, "growth.channel.retire", row["id"], {"slug": slug})
    return channel_full(c, channel(c, slug))


def source_create(c, actor, body):
    H._writer(c, actor)
    row = {"id": H.new_id(), "truth_for": body.truth_for, "system": body.system, "reach_kind": body.reach_kind,
           "reach_ref": body.reach_ref, "may": body.may, "owner_ref": _owner_ref(c, body.owner_ref),
           "created_at": H.now()}
    c.execute(f"INSERT INTO gtm_sources ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", list(row.values()))
    H.event(c, actor, "growth.source.create", row["id"], {"truth_for": body.truth_for, "system": body.system})
    return source_view(H._one(c, "SELECT * FROM gtm_sources WHERE id=?", (row["id"],)))


def source_update(c, actor, sid, fields, retire=False):
    H._writer(c, actor)
    row = H._one(c, "SELECT * FROM gtm_sources WHERE id=?", (sid,))
    if not row:
        raise Problem("not_found", f"No source {sid}", 404)
    if "owner_ref" in fields:
        fields["owner_ref"] = _owner_ref(c, fields["owner_ref"])
    for required in ("truth_for", "system", "reach_kind", "reach_ref", "may"):
        if required in fields and fields[required] is None:
            raise Problem("validation", f"{required} cannot be cleared", 422)
    if retire and not row["retired_at"]:
        fields["retired_at"] = H.now()
    changed = {k: v for k, v in fields.items() if row.get(k) != v}
    if changed:
        c.execute(f"UPDATE gtm_sources SET {', '.join(k + '=?' for k in changed)} WHERE id=?", list(changed.values()) + [sid])
        H.event(c, actor, "growth.source." + ("retire" if retire else "update"), sid, changed)
    return source_view(H._one(c, "SELECT * FROM gtm_sources WHERE id=?", (sid,)))


def _channel_ids(c, slugs):
    out = []
    for slug in dict.fromkeys(slugs or []):
        row = channel(c, slug)
        if not row:
            raise Problem("not_found", f"No channel {slug}", 404)
        out.append(row["id"])
    return out


def _segment_ids(c, refs):
    """Growth segment ids for growth segment or market segment ids; an unknown one is a 404."""
    out = []
    for ref in dict.fromkeys(refs or []):
        row = segment_row(c, ref)
        if not row:
            raise Problem("not_found", f"No segment {ref}", 404)
        out.append(row["id"])
    return list(dict.fromkeys(out))


def _link(c, table, key, owner, seg_ids):
    c.execute(f"DELETE FROM {table} WHERE {key}=?", (owner,))
    c.executemany(f"INSERT OR IGNORE INTO {table} ({key}, segment_row_id) VALUES (?,?)", [(owner, s) for s in seg_ids])


def _market_segment(c, actor, name, company=""):
    """The market segment by that name or alias, else a new one under the market's root (defining the market as
    "<company> market" first, as the Strategy page's Add a segment does). A removed one by that name comes back."""
    name = " ".join(str(name or "").split())
    found = [r for r in MK._resolve_existing(c, name, [], {}) if r["type"] == "segment" and r["status"] == "active"]
    exact = [r for r in found if r["name"].lower() == name.lower()]
    if exact or found:
        return (exact or found)[0]["id"]
    held = MK.entity(c, "segment/" + MK.slug(name))
    if held and held["type"] == "segment" and held["status"] == "active":
        return held["id"]
    if not (MS.definition(c) or {}).get("root"):
        MS.set_definition(c, actor, {"root_name": (company or "Our company") + " market"})
    return MS.add_segment(c, actor, name)["id"]


def segment_create(c, actor, body, company="", bot=False):
    """A growth segment for a market segment named or given by id. A bot adds only a testing one, and never over a
    segment the market graph already marks Us or Next."""
    H._writer(c, actor)
    if bool(body.name) == bool(body.segment_id):
        raise Problem("validation", "Give a name or a segment_id", 422)
    if body.segment_id:
        sid = SM._segment(c, body.segment_id)["id"]
    else:
        sid = _market_segment(c, actor, body.name, company)
    if SM.growth_row(c, sid):
        raise Problem("conflict", f"{sid} is already a growth segment", 409, extra={"segment_id": sid})
    if bot and SM.placed(c, sid):
        raise Problem("forbidden", f"{sid} is already Us or Next on the Strategy page; an admin sets its status", 403)
    row = SM.set_status(c, actor, sid, body.status, company, growth=True)
    fields = {"motion": body.motion, "who_buys": body.who_buys or None, "deal_size": body.deal_size or None,
              "why": body.why, "owner_ref": _owner_ref(c, body.owner_ref), "sort": body.sort}
    c.execute(f"UPDATE growth_segments SET {', '.join(k + '=?' for k in fields)} WHERE id=?",
              list(fields.values()) + [row["id"]])
    return row["id"]


def segment_update(c, actor, ref, fields, company=""):
    H._writer(c, actor)
    row = segment_row(c, ref)
    if not row:
        raise Problem("not_found", f"No segment {ref}", 404)
    for required in ("status", "why", "sort"):
        if required in fields and fields[required] is None:
            raise Problem("validation", f"{required} cannot be cleared", 422)
    if "owner_ref" in fields:
        fields["owner_ref"] = _owner_ref(c, fields["owner_ref"])
    for key in ("who_buys", "deal_size"):
        if key in fields and not fields[key]:
            fields[key] = None
    name = fields.pop("name", None)
    if name and name != (MK.entity(c, row["segment_id"]) or {}).get("name"):
        MK.update_entity(c, actor, row["segment_id"], name=name, evidence_ids=SM._evidence(c, actor, growth=True))
        H.event(c, actor, "growth.segment.update", row["id"], {"name": name})
    status = fields.pop("status", None)
    if status:
        SM.set_status(c, actor, row["segment_id"], status, company, growth=True)
    changed = {k: v for k, v in fields.items() if row.get(k) != v}
    if changed:
        changed.update(updated_at=H.now(), updated_by=actor)
        c.execute(f"UPDATE growth_segments SET {', '.join(k + '=?' for k in changed)} WHERE id=?",
                  list(changed.values()) + [row["id"]])
        H.event(c, actor, "growth.segment.update", row["id"],
                {k: v for k, v in changed.items() if k not in ("updated_at", "updated_by")})
    return row["id"]


def _bet_ref(c, value):
    if value in (None, ""):
        return None
    if not H._one(c, "SELECT id FROM strategy_bets WHERE id=?", (value,)):
        raise Problem("not_found", f"No strategy bet {value}", 404)
    return value


def idea_create(c, auth, who, body, task_create):
    """A task of the Idea type and its growth row, in the caller's write. Filed for the assignee, else the caller."""
    type_id = ensure(c)
    if not type_id:
        raise Problem("conflict", "The team's own task types already use the names Idea and Growth idea", 409)
    bet_id, kpi_id = _bet_ref(c, body.bet_id), _kpi_ref(c, body.kpi_id, "kpi_id")
    channel_ids = _channel_ids(c, body.channels)
    seg_ids = _segment_ids(c, body.segments)
    relations = [M.TaskRelationRef(task=body.follow_up_of, kind="follow_up")] if body.follow_up_of else []
    task_body = body.body or body.hypothesis or body.title
    made = task_create(c, who, M.TaskCreate(title=body.title, body=task_body, owner=body.assignee or who.actor,
                                            type=type_id, private=False, relations=relations), lint=False)
    task = made.get("task") if isinstance(made, dict) else None
    if not task:
        raise Problem("held", "The idea was not filed: " + json.dumps(made)[:300], 409)
    task_id = task["id"]
    existing = H._one(c, "SELECT task_id FROM growth_ideas WHERE task_id=?", (task_id,))
    if existing:
        return task_id          # the same open task again (task_create deduplicates by title): that idea
    if (H.task(c, task_id) or {}).get("type_id") != type_id:
        raise Problem("conflict", f"An open task already has the title \"{body.title}\"; give the idea its own title", 409)
    now = H.now()
    c.execute("INSERT INTO growth_ideas (task_id, hypothesis, prediction, bet_id, kpi_id, ice_i, ice_c, ice_e, status, "
              "source_kind, source_ref, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,'idea',?,?,?,?)",
              (task_id, body.hypothesis, body.prediction, bet_id, kpi_id, body.ice_i, body.ice_c, body.ice_e,
               body.source_kind, body.source_ref, now, now))
    c.executemany("INSERT OR IGNORE INTO growth_idea_channels (task_id, channel_id) VALUES (?,?)",
                  [(task_id, cid) for cid in channel_ids])
    _link(c, "growth_idea_segments", "task_id", task_id, seg_ids)
    H.event(c, who.actor, "growth.idea.create", task_id, {"title": body.title})
    return task_id


def _idea_row(c, auth, who, task_id, need):
    """The idea and whether the caller may act on it: `edit` (admin, assignee, creator) or `run` (admin, assignee)."""
    resolved = auth.resolve_task(c, who, task_id)
    rows = H._rows(c.execute(IDEA_SQL + " WHERE g.task_id=? AND " + _visible(auth, c, who), (resolved,)))
    if not rows:
        raise Problem("not_found", "No such idea", 404)
    row = rows[0]
    allowed = admin(auth, who) or who.actor == row["assignee"] or (need == "edit" and who.actor == row["created_by"])
    if not allowed:
        whom = "its assignee, its creator or an admin" if need == "edit" else "its assignee or an admin"
        raise Problem("forbidden", f"Only {whom} changes this idea", 403)
    return row


def _set_idea(c, actor, task_id, fields, action):
    if fields:
        fields["updated_at"] = H.now()
        c.execute(f"UPDATE growth_ideas SET {', '.join(k + '=?' for k in fields)} WHERE task_id=?",
                  list(fields.values()) + [task_id])
        H.event(c, actor, action, task_id, {k: v for k, v in fields.items() if k != "updated_at"})


def idea_update(c, auth, who, task_id, body):
    row = _idea_row(c, auth, who, task_id, "edit")
    fields = {k: getattr(body, k) for k in body.model_fields_set}
    if "title" in fields:
        title = fields.pop("title")
        if title is None:
            raise Problem("validation", "An idea keeps its title", 422)
        if title != row["title"]:
            c.execute("UPDATE tasks SET title=?, updated_at=? WHERE id=?", (title, H.now(), row["task_id"]))
            H._task_event(c, row["task_id"], who.actor, "title", row["title"], title, "")
    if "channels" in fields:
        ids = _channel_ids(c, fields.pop("channels") or [])
        c.execute("DELETE FROM growth_idea_channels WHERE task_id=?", (row["task_id"],))
        c.executemany("INSERT INTO growth_idea_channels (task_id, channel_id) VALUES (?,?)",
                      [(row["task_id"], cid) for cid in ids])
    if "segments" in fields:
        seg_ids = _segment_ids(c, fields.pop("segments") or [])
        _link(c, "growth_idea_segments", "task_id", row["task_id"], seg_ids)
        H.event(c, who.actor, "growth.idea.update", row["task_id"], {"segments": seg_ids})
    if "bet_id" in fields:
        fields["bet_id"] = _bet_ref(c, fields["bet_id"])
    for key in ("kpi_id", "judged_kpi_id"):
        if key in fields:
            fields[key] = _kpi_ref(c, fields[key], key)
    for key in ("hypothesis", "prediction"):
        if key in fields and fields[key] is None:
            fields[key] = ""
    _set_idea(c, who.actor, row["task_id"], fields, "growth.idea.update")
    return row["task_id"]


def idea_score(c, auth, who, task_id, body):
    row = _idea_row(c, auth, who, task_id, "edit")
    _set_idea(c, who.actor, row["task_id"], {"ice_i": body.i, "ice_c": body.c, "ice_e": body.e}, "growth.idea.score")
    return row["task_id"]


def idea_start(c, auth, who, task_id, body):
    row = _idea_row(c, auth, who, task_id, "run")
    if row["status"] in TERMINAL:
        raise Problem("conflict", f"This idea already has its verdict ({row['status']}); file a follow-up to test again",
                      409)
    test_from = body.test_from or _today()
    if body.test_to < test_from:
        raise Problem("validation", "test_to comes after test_from", 422)
    fields = {"status": "testing", "test_from": test_from, "test_to": body.test_to, "change": body.change,
              "stop_if": body.stop_if}
    for key in ("min_events", "audience"):
        if getattr(body, key) is not None:
            fields[key] = getattr(body, key)
    if body.judged_kpi_id:
        fields["judged_kpi_id"] = _kpi_ref(c, body.judged_kpi_id, "judged_kpi_id")
    _set_idea(c, who.actor, row["task_id"], fields, "growth.idea.start")
    _step(c, who.actor, row["task_id"], "testing")
    return row["task_id"]


def _as_caller_or_tico(c, write):
    """Run a task write as the caller, or as Tico when the task rules leave it to someone else: the growth route has
    already checked that the caller may run this idea."""
    c.execute("SAVEPOINT growth_idea_task")
    try:
        write(None)
        c.execute("RELEASE growth_idea_task")
    except H.Refused:
        c.execute("ROLLBACK TO growth_idea_task")
        c.execute("RELEASE growth_idea_task")
        write(H.KEEPER)


def _step(c, actor, task_id, key, note=""):
    """Move the idea's task to the step its growth status names; a step the team deleted leaves the task where it is."""
    sid = step_id(key)
    row = H._one(c, "SELECT type_id, step_id FROM tasks WHERE id=?", (task_id,)) or {}
    if row.get("type_id") != IDEA_TYPE or row.get("step_id") == sid or not H._one(
            c, "SELECT 1 FROM task_steps WHERE id=?", (sid,)):
        return
    _as_caller_or_tico(c, lambda keeper: H.task_update(c, keeper or actor, task_id, step=sid, note=note or None,
                                                       quiet=True))


def _close(c, actor, task_id, note, key):
    """Close the idea's task on its verdict's step."""
    have = H._one(c, "SELECT 1 FROM task_steps WHERE id=?", (step_id(key),))
    row = H._one(c, "SELECT type_id FROM tasks WHERE id=?", (task_id,)) or {}
    sid = step_id(key) if have and row.get("type_id") == IDEA_TYPE else None
    _as_caller_or_tico(c, lambda keeper: H.task_close(c, keeper or actor, task_id, note=note, quiet=True, step=sid))


def idea_verdict(c, auth, who, task_id, body):
    row = _idea_row(c, auth, who, task_id, "run")
    fields = {"status": body.status}
    for key in ("result", "learning", "bet_effect", "confidence"):
        if getattr(body, key) is not None:
            fields[key] = getattr(body, key)
    if body.status != "paused":
        fields.update(verdict_at=H.now(), verdict_by=who.actor)
    _set_idea(c, who.actor, row["task_id"], fields, "growth.idea.verdict")
    if body.status == "paused":
        _step(c, who.actor, row["task_id"], "paused", "Test paused")
    if body.status in TERMINAL:
        task = H.task(c, row["task_id"]) or {}
        if task.get("status") != "declined":
            # A task already closed by hand still moves to the verdict's step.
            note = f"Verdict: {body.status}" + (f". {body.result}" if body.result else "")
            _close(c, who.actor, row["task_id"], note[:1000], body.status)
        H.hook(c, "growth.verdict", row["task_id"],
               lambda: {"task_id": row["task_id"], "title": row["title"], "status": body.status,
                        "verdict_by": who.actor}, who.actor, revision=0)
    return row["task_id"]


def cohort_set(c, actor, body):
    H._writer(c, actor)
    slug = body.channel if body.channel not in (None, "", UNKNOWN) else None
    channel_id = None
    if slug:
        found = channel(c, slug)
        if not found:
            raise Problem("not_found", f"No channel {slug}", 404)
        channel_id = found["id"]
    segment_id = _segment_ids(c, [body.segment_id])[0] if body.segment_id else None
    row = H._one(c, "SELECT * FROM growth_cohorts WHERE coalesce(channel_id,'')=? AND month=? AND coalesce(segment_id,'')=?",
                 (channel_id or "", body.month, segment_id or ""))
    fields = {k: getattr(body, k) for k in body.model_fields_set if k not in ("channel", "month", "sources", "segment_id")}
    if "quality" in fields and fields["quality"] is None:
        fields["quality"] = "actual"
    sources = dict(json.loads(row["sources"] or "{}")) if row else {}
    if body.sources is not None:
        for key, text in body.sources.items():
            if text:
                sources[key] = text
            else:
                sources.pop(key, None)
    fields.update(sources=json.dumps(sources, sort_keys=True), updated_by=actor, updated_at=H.now())
    if row:
        c.execute(f"UPDATE growth_cohorts SET {', '.join(k + '=?' for k in fields)} WHERE id=?",
                  list(fields.values()) + [row["id"]])
        cid = row["id"]
    else:
        cid = H.new_id()
        fields.update(id=cid, channel_id=channel_id, month=body.month, segment_id=segment_id)
        c.execute(f"INSERT INTO growth_cohorts ({', '.join(fields)}) VALUES ({', '.join('?' * len(fields))})",
                  list(fields.values()))
    H.event(c, actor, "growth.cohort.set", cid, {"channel": slug or UNKNOWN, "month": body.month,
                                                  **({"segment_id": segment_id} if segment_id else {})})
    return _cohort_view(H._one(c, "SELECT * FROM growth_cohorts WHERE id=?", (cid,)), _slugs(c))


def settings_update(c, actor, fields):
    H._writer(c, actor)
    c.execute("INSERT OR IGNORE INTO growth_settings (id) VALUES (1)")
    for required in ("unit", "verdict_win_pct", "framework"):
        if required in fields and fields[required] is None:
            raise Problem("validation", f"{required} cannot be cleared", 422)
    if fields:
        c.execute(f"UPDATE growth_settings SET {', '.join(k + '=?' for k in fields)} WHERE id=1", list(fields.values()))
        H.event(c, actor, "growth.settings", "settings", fields)
    return settings(c)


def kpi_fields(c, actor, kpi_id, fields):
    """The KPI tree columns (`hub kpi update`): parent, growth role, funnel stage and loop. One North Star at a time:
    naming a new one moves the role from the old."""
    if not fields:
        return
    if fields.get("growth_role") not in (None,) + ROLES:
        raise Problem("validation", f"growth_role is {'|'.join(ROLES)} or null", 422)
    if fields.get("stage") not in (None,) + STAGES:
        raise Problem("validation", f"stage is {'|'.join(STAGES)} or null", 422)
    parent = fields.get("parent_kpi_id")
    if parent:
        if parent == kpi_id:
            raise Problem("validation", "A KPI cannot be its own parent", 422)
        _kpi_ref(c, parent, "parent_kpi_id")
        seen, at = {kpi_id}, parent
        while at:
            if at in seen:
                raise Problem("validation", "That parent would make a loop in the KPI tree", 422)
            seen.add(at)
            at = (H._one(c, "SELECT parent_kpi_id FROM kpis WHERE id=?", (at,)) or {}).get("parent_kpi_id")
    if fields.get("growth_role") == "north_star":
        for old in c.execute("SELECT id FROM kpis WHERE growth_role='north_star' AND id<>?", (kpi_id,)).fetchall():
            c.execute("UPDATE kpis SET growth_role=NULL WHERE id=?", (old[0],))
            H.event(c, actor, "kpi.update", old[0], {"growth_role": None})
    c.execute(f"UPDATE kpis SET {', '.join(k + '=?' for k in fields)} WHERE id=?", list(fields.values()) + [kpi_id])
    H.event(c, actor, "kpi.update", kpi_id, fields)


# ----------------------------------------------------------------------------- routes
def install(app, store, auth, mutate, task_create):
    from .goal_routes import _hidden_owners, kpi_editable

    def reader(who):
        auth.domain(who)

    def editor(who, what):
        auth.domain(who)
        if not admin(auth, who):
            raise Problem("forbidden", f"Only the owner or an admin {what}", 403)

    def company():
        return str(getattr(store.settings, "company_name", "") or "").strip()

    @app.get("/api/v2/growth/channels")
    def growth_channels(request: Request, include_retired: bool = False):
        """The channels, by sort then name. `may_edit`: the caller adds and changes any channel (owner or admin)."""
        who = request.state.identity
        reader(who)
        with store.read() as c:
            return {"channels": channels(c, include_retired), "may_edit": admin(auth, who)}

    @app.get("/api/v2/growth/channels/{slug}")
    def growth_channel(request: Request, slug: str):
        """One channel, retired or not."""
        reader(request.state.identity)
        with store.read() as c:
            row = channel(c, slug)
            if not row:
                raise Problem("not_found", f"No channel {slug}", 404)
            return channel_full(c, row)

    @app.post("/api/v2/growth/channels")
    def growth_channel_add(request: Request, body: M.GrowthChannelCreate):
        """Owner or admin: a new channel."""
        who = request.state.identity

        def work(c):
            editor(who, "adds a channel")
            return channel_create(c, who.actor, body)
        return mutate(request, body, work)

    @app.patch("/api/v2/growth/channels/{slug}")
    def growth_channel_update(request: Request, slug: str, body: M.GrowthChannelUpdate):
        """Owner or admin: any field. The bot that owns the channel: its why, stage and budget."""
        who = request.state.identity

        def work(c):
            auth.domain(who)
            fields = {k: getattr(body, k) for k in body.model_fields_set}
            if not admin(auth, who):
                row = channel(c, slug)
                if not row:
                    raise Problem("not_found", f"No channel {slug}", 404)
                if who.role != "bot" or row["owner_ref"] != who.actor:
                    raise Problem("forbidden", "Only the owner, an admin or the bot that owns the channel changes it", 403)
                if set(fields) - OWNER_PATCH:
                    raise Problem("forbidden", "The bot that owns a channel changes its why, stage and budget_month; "
                                  "an admin changes the rest", 403)
            return channel_update(c, who.actor, slug, fields)
        return mutate(request, body, work)

    @app.post("/api/v2/growth/channels/{slug}/retire")
    def growth_channel_retire(request: Request, slug: str, body: M.Empty):
        """Owner or admin: retire a channel; its cohorts and ideas stay."""
        who = request.state.identity

        def work(c):
            editor(who, "retires a channel")
            return channel_retire(c, who.actor, slug)
        return mutate(request, body, work)

    @app.get("/api/v2/growth/segments")
    def growth_segments(request: Request):
        """Who the company sells to: serving, testing, next, then dropped. `may_edit`: the caller sets any field and
        status (owner or admin); a bot adds testing segments and changes why, who_buys and deal_size."""
        who = request.state.identity
        reader(who)
        with store.read() as c:
            return {"segments": segments(c, auth, who), "may_edit": admin(auth, who)}

    @app.post("/api/v2/growth/segments")
    def growth_segment_add(request: Request, body: M.GrowthSegmentCreate):
        """Owner or admin: a segment by market segment name (found by name or alias, else made) or id, any status.
        A bot: a testing one."""
        who = request.state.identity

        def work(c):
            auth.domain(who)
            if not admin(auth, who):
                if who.role != "bot":
                    raise Problem("forbidden", "Only the owner, an admin or a bot adds a segment", 403)
                if body.status != "testing":
                    raise Problem("forbidden", "A bot adds a segment to test (status testing); an admin sets serving, "
                                  "next and dropped", 403)
            rid = segment_create(c, who.actor, body, company(), bot=not admin(auth, who))
            return {"segment": segment(c, rid, auth, who)}
        return mutate(request, body, work)

    @app.patch("/api/v2/growth/segments/{sid:path}")
    def growth_segment_update(request: Request, sid: str, body: M.GrowthSegmentUpdate):
        """Owner or admin: any field, status included (serving and next move Where we play on the Strategy page). Any
        bot: why, who_buys and deal_size. `sid` is the growth segment id or its market segment id."""
        who = request.state.identity

        def work(c):
            auth.domain(who)
            fields = {k: getattr(body, k) for k in body.model_fields_set}
            if not admin(auth, who):
                if who.role != "bot":
                    raise Problem("forbidden", "Only the owner, an admin or a bot changes a segment", 403)
                if set(fields) - BOT_SEGMENT_PATCH:
                    raise Problem("forbidden", "A bot changes a segment's why, who_buys and deal_size; an admin "
                                  "changes the rest", 403)
            rid = segment_update(c, who.actor, sid, fields, company())
            return {"segment": segment(c, rid, auth, who)}
        return mutate(request, body, work)

    @app.get("/api/v2/growth/sources")
    def growth_sources(request: Request, include_retired: bool = False):
        """Where each number's truth lives, and the things two live sources both claim. `may_edit`: the caller
        changes sources (owner or admin)."""
        who = request.state.identity
        reader(who)
        with store.read() as c:
            return {**sources(c, include_retired), "may_edit": admin(auth, who)}

    @app.post("/api/v2/growth/sources")
    def growth_source_add(request: Request, body: M.GrowthSourceCreate):
        """Owner or admin: a new source of truth."""
        who = request.state.identity

        def work(c):
            editor(who, "adds a source")
            return source_create(c, who.actor, body)
        return mutate(request, body, work)

    @app.patch("/api/v2/growth/sources/{sid}")
    def growth_source_update(request: Request, sid: str, body: M.GrowthSourceUpdate):
        """Owner or admin: change a source."""
        who = request.state.identity

        def work(c):
            editor(who, "changes a source")
            return source_update(c, who.actor, sid, {k: getattr(body, k) for k in body.model_fields_set})
        return mutate(request, body, work)

    @app.post("/api/v2/growth/sources/{sid}/retire")
    def growth_source_retire(request: Request, sid: str, body: M.Empty):
        """Owner or admin: retire a source."""
        who = request.state.identity

        def work(c):
            editor(who, "retires a source")
            return source_update(c, who.actor, sid, {}, retire=True)
        return mutate(request, body, work)

    @app.get("/api/v2/growth/ideas")
    def growth_ideas(request: Request, status: str | None = None, bet_id: str | None = None,
                     channel: str | None = None, kpi_id: str | None = None, q: str | None = None,
                     limit: int = IDEAS_PAGE, segment: str | None = None):
        """Ideas the caller may read, newest first, at most `limit` (500). `segment`: those that target it."""
        who = request.state.identity
        reader(who)
        with store.read() as c:
            return {"ideas": ideas(c, auth, who, status, bet_id, channel, kpi_id, q, limit, segment)}

    @app.post("/api/v2/growth/ideas")
    def growth_idea_create(request: Request, body: M.GrowthIdeaCreate):
        """Anyone: a new idea, a task of the Idea type. `follow_up_of` relates it to the idea it follows."""
        who = request.state.identity

        def work(c):
            reader(who)
            return {"idea": idea(c, auth, who, idea_create(c, auth, who, body, task_create))}
        return mutate(request, body, work)

    @app.get("/api/v2/growth/ideas/{task_id}")
    def growth_idea(request: Request, task_id: str):
        """One idea; 404 for a task that is not an idea."""
        who = request.state.identity
        reader(who)
        with store.read() as c:
            return {"idea": idea(c, auth, who, task_id)}

    def idea_write(path, model, fn, doc):
        def route(request: Request, task_id: str, body: model):
            who = request.state.identity

            def work(c):
                reader(who)
                return {"idea": idea(c, auth, who, fn(c, auth, who, task_id, body))}
            return mutate(request, body, work)
        route.__name__ = "growth_idea_" + (path.strip("/") or "update")
        route.__doc__ = doc
        return route

    app.patch("/api/v2/growth/ideas/{task_id}")(idea_write(
        "", M.GrowthIdeaUpdate, idea_update, "Assignee, creator or admin: change the idea; only the fields sent."))
    app.post("/api/v2/growth/ideas/{task_id}/score")(idea_write(
        "/score", M.GrowthIdeaScore, idea_score, "Assignee, creator or admin: impact, confidence and ease, 1 to 10."))
    app.post("/api/v2/growth/ideas/{task_id}/start")(idea_write(
        "/start", M.GrowthIdeaStart, idea_start, "Assignee or admin: start the test with its dates, change and stop rule."))
    app.post("/api/v2/growth/ideas/{task_id}/verdict")(idea_write(
        "/verdict", M.GrowthIdeaVerdict, idea_verdict,
        "Assignee or admin: the verdict. Won, lost, inconclusive and dropped close the task; paused does not."))

    @app.get("/api/v2/growth/cohorts")
    def growth_cohorts(request: Request, channel: str | None = None, month: str | None = None,
                       limit: int = COHORTS_PAGE, segment: str | None = None):
        """Monthly cohorts, newest month first; `channel=unknown` is the Unknown row, `segment` one segment's rows."""
        reader(request.state.identity)
        with store.read() as c:
            return {"cohorts": cohorts(c, channel, month, limit, segment)}

    @app.put("/api/v2/growth/cohorts")
    def growth_cohort_set(request: Request, body: M.GrowthCohortSet):
        """Owner, admin or any bot: one channel's month (or one segment's share of it), created or updated; only the
        fields sent change."""
        who = request.state.identity

        def work(c):
            auth.domain(who)
            if who.role != "bot" and not admin(auth, who):
                raise Problem("forbidden", "The owner, an admin or a bot records a cohort", 403)
            return cohort_set(c, who.actor, body)
        return mutate(request, body, work)

    @app.get("/api/v2/growth/economics")
    def growth_economics(request: Request, month: str | None = None, segment: str | None = None):
        """CAC, LTV and payback per channel for one month (the latest by default), the total and the guardrails.
        `segment`: only that segment's cohorts."""
        who = request.state.identity
        reader(who)
        with store.read() as c:
            return economics(c, month, _hidden_owners(c, auth, who), segment)

    @app.get("/api/v2/growth/settings")
    def growth_settings(request: Request):
        """The unit counted, the default revenue and margin, the verdict win rate and the framework. `may_edit`: the
        caller changes them (owner or admin)."""
        who = request.state.identity
        reader(who)
        with store.read() as c:
            return {**settings(c), "may_edit": admin(auth, who)}

    @app.put("/api/v2/growth/settings")
    def growth_settings_update(request: Request, body: M.GrowthSettingsUpdate):
        """Owner or admin: only the fields sent change."""
        who = request.state.identity

        def work(c):
            editor(who, "changes the growth settings")
            return settings_update(c, who.actor, {k: getattr(body, k) for k in body.model_fields_set})
        return mutate(request, body, work)

    @app.get("/api/v2/growth/kpis")
    def growth_kpis(request: Request):
        """Every KPI the caller may read with its latest value, target and place in the KPI tree."""
        who = request.state.identity
        reader(who)
        with store.read() as c:
            return kpis(c, _hidden_owners(c, auth, who), lambda record: kpi_editable(c, who, record))

    @app.get("/api/v2/growth/evidence")
    def growth_evidence(request: Request):
        """Per strategy bet, how many verdicts support or weaken it, by confidence."""
        who = request.state.identity
        reader(who)
        with store.read() as c:
            return evidence(c, auth, who)

    @app.get("/api/v2/growth/working")
    def growth_working(request: Request):
        """Open tasks under an idea or labelled channel:<slug>."""
        who = request.state.identity
        reader(who)
        with store.read() as c:
            return working(c, auth, who)

    @app.get("/api/v2/growth/week")
    def growth_week(request: Request):
        """The week: KPI tree readings, tests due, the top ideas, recent verdicts and breached guardrails."""
        who = request.state.identity
        reader(who)
        with store.read() as c:
            return week(c, auth, who, _hidden_owners(c, auth, who))
