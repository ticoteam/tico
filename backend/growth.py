"""Growth: the numbers that tell the story of growth. The channels the company acquires customers through, each month's
cohorts per channel (spend, bot cost, new customers, the share kept at 30, 90 and 180 days) and the unit economics
they add up to, and the sources each number's truth lives in.

- Channels (`growth_channels`): a performance or brand track and a stage (idea, testing, scaling, paused), an owner, a
  monthly budget, the source its numbers come from, and an optional guardrail (a KPI that must stay at or under a max).
  A channel's marketing work is the open tasks labelled `channel:<slug>` (backend/marketing_tasks.py).
- Sources (`gtm_sources`): which system is the truth for what, and how a bot reaches it. Two live sources for the
  same thing are a conflict the page shows.
- Cohorts (`growth_cohorts`): one row per channel per month (no channel is Unknown). `economics` turns them into CAC,
  LTV and payback.
- The KPI tree is four columns on `kpis` (parent, growth role, funnel stage, loop): at most one North Star.

Who may: everyone signed in reads. The owner and admins (people) write channels, sources and settings; the bot that
owns a channel may change its why, stage and budget; any bot or admin records cohorts.
"""
import json
import math

from fastapi import Request

from . import hubdb as H
from . import models as M
from .brand import admin
from .store import Problem

UNKNOWN = "unknown"
LIFETIME_CAP = 60          # months: a retention curve that never decays still counts at most five years
COHORTS_PAGE, COHORTS_MAX = 500, 2000
CHANNEL_TASKS_MAX = 200
CHANNEL_FIELDS = ("id", "slug", "name", "track", "stage", "owner_ref", "budget_month", "source_id", "why",
                  "guardrail_kpi_id", "guardrail_max", "sort", "retired_at")
SOURCE_FIELDS = ("id", "truth_for", "system", "reach_kind", "reach_ref", "may", "owner_ref", "retired_at")
OWNER_PATCH = {"why", "stage", "budget_month"}       # what the bot that owns a channel may change
ROLES = ("north_star", "input", "guardrail")
STAGES = ("reach", "acquire", "activate", "retain", "refer")
KPI_FIELDS = ("parent_kpi_id", "growth_role", "stage", "loop_name", "loop_step")


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
            "margin_pct": row.get("margin_pct")}


def channel_view(row):
    return {k: row.get(k) for k in CHANNEL_FIELDS}


def channels(c, include_retired=False):
    sql = "SELECT * FROM growth_channels" + ("" if include_retired else " WHERE retired_at IS NULL")
    return [channel_view(r) for r in H._rows(c.execute(sql + " ORDER BY sort, name COLLATE NOCASE"))]


def channel(c, slug):
    return H._one(c, "SELECT * FROM growth_channels WHERE slug=?", (slug,))


def channel_tasks(c, auth, who, slug, limit=CHANNEL_TASKS_MAX):
    """The open tasks labelled `channel:<slug>` the reader may see, the most recently updated first."""
    key = H._labels([f"channel:{slug}"])[0]
    rows = H._rows(c.execute(
        "SELECT t.id, t.title, t.owner_id, t.status, t.updated_at FROM tasks t "
        "WHERE t.status IN ('open','doing','waiting','review','ready') "
        f"AND t.id IN (SELECT id FROM tasks WHERE {auth.task_sql(c, who)}) "
        "AND t.id IN (SELECT tt.task_id FROM task_tags tt JOIN tags tg ON tg.id=tt.tag_id WHERE tg.key=?) "
        "ORDER BY t.updated_at DESC LIMIT ?", (key, limit)))
    return [{"task_id": r["id"], "title": r["title"], "assignee": r["owner"], "status": r["status"],
             "updated_at": r["updated_at"]} for r in rows]


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


def _cohort_view(row, slugs):
    return {"id": row["id"], "channel": slugs.get(row["channel_id"]) if row["channel_id"] else None,
            "month": row["month"],
            **{k: row[k] for k in ("spend", "bot_cost", "new_customers", "kept_d30", "kept_d90", "kept_d180",
                                   "revenue_month", "margin_pct", "quality")},
            "sources": json.loads(row["sources"] or "{}"), "updated_by": row["updated_by"],
            "updated_at": row["updated_at"]}


def _slugs(c):
    return {r[0]: r[1] for r in c.execute("SELECT id, slug FROM growth_channels")}


def cohorts(c, channel_slug=None, month=None, limit=COHORTS_PAGE):
    where, args = [], []
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
                             + " ORDER BY month DESC, channel_id IS NULL, channel_id LIMIT ?", args + [limit]))
    return [_cohort_view(r, slugs) for r in rows]


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
        kpi = H._one(c, "SELECT name, owner_id FROM kpis WHERE id=?", (ch["guardrail_kpi_id"],)) or {}
        if kpi.get("owner_id") in hidden:
            continue
        newest = _latest_value(c, ch["guardrail_kpi_id"])
        value = newest["value"] if newest else None
        out.append({"channel": ch["slug"], "kpi_id": ch["guardrail_kpi_id"], "kpi_name": kpi.get("name"),
                    "value": value, "max": ch["guardrail_max"], "breached": value is not None and value > ch["guardrail_max"]})
    return out


def economics(c, month=None, hidden=()):
    """One month by channel: each channel's row for it, the Unknown row beside them, the total and the guardrails.
    The latest month with cohorts by default."""
    months = [r[0] for r in c.execute("SELECT DISTINCT month FROM growth_cohorts ORDER BY month DESC")]
    month = month or (months[0] if months else None)
    conf = settings(c)
    by_slug = {r["slug"]: r for r in H._rows(c.execute("SELECT * FROM growth_channels"))}
    rows, inputs = [], []
    for cohort in (cohorts(c, month=month, limit=COHORTS_MAX) if month else []):
        inputs.append(cohort)
        slug = cohort["channel"]
        ch = by_slug.get(slug) if slug else None
        rows.append({"channel": slug or UNKNOWN, "name": ch["name"] if ch else "Unknown",
                     "track": ch["track"] if ch else None, **economics_row(cohort, conf)})
    blank = {k: None for k in ("spend", "bot_cost", "new_customers", "cac", "kept_d30", "kept_d90", "kept_d180",
                               "expected_months", "ltv", "ltv_cac", "payback_months", "quality")}
    if not rows:
        # A month with no numbers yet still lists every active channel and Unknown, each blank, for the first entry.
        rows = [{"channel": ch["slug"], "name": ch["name"], "track": ch["track"], **blank, "sources": {}}
                for ch in by_slug.values() if not ch.get("retired_at")]
        rows.append({"channel": UNKNOWN, "name": "Unknown", "track": None, **blank, "sources": {}})
    rows.sort(key=lambda r: (r["channel"] == UNKNOWN, (by_slug.get(r["channel"]) or {}).get("sort", 0), r["name"]))
    total = economics_total(inputs, conf)
    if not inputs:
        total["quality"] = None
    return {"month": month, "months": months, "settings": conf, "rows": rows, "total": total,
            "guardrails": guardrails(c, hidden)}


def kpis(c, hidden=(), may_edit=lambda record: False):
    rows = H._rows(c.execute("SELECT * FROM kpis WHERE archived_at IS NULL ORDER BY created_at"))
    rows = [r for r in rows if r.get("owner_id") not in hidden]
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
    c.execute(f"INSERT INTO growth_channels ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", list(row.values()))
    H.event(c, actor, "growth.channel.create", row["id"], {"slug": body.slug})
    return channel_view(channel(c, body.slug))


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
    changed = {k: v for k, v in fields.items() if row.get(k) != v}
    if changed:
        c.execute(f"UPDATE growth_channels SET {', '.join(k + '=?' for k in changed)} WHERE id=?",
                  list(changed.values()) + [row["id"]])
        H.event(c, actor, "growth.channel.update", row["id"], {"slug": slug, **changed})
    return channel_view(channel(c, slug))


def channel_retire(c, actor, slug):
    H._writer(c, actor)
    row = channel(c, slug)
    if not row:
        raise Problem("not_found", f"No channel {slug}", 404)
    if not row["retired_at"]:
        c.execute("UPDATE growth_channels SET retired_at=? WHERE id=?", (H.now(), row["id"]))
        H.event(c, actor, "growth.channel.retire", row["id"], {"slug": slug})
    return channel_view(channel(c, slug))


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


def cohort_set(c, actor, body):
    H._writer(c, actor)
    slug = body.channel if body.channel not in (None, "", UNKNOWN) else None
    channel_id = None
    if slug:
        found = channel(c, slug)
        if not found:
            raise Problem("not_found", f"No channel {slug}", 404)
        channel_id = found["id"]
    row = H._one(c, "SELECT * FROM growth_cohorts WHERE coalesce(channel_id,'')=? AND month=?",
                 (channel_id or "", body.month))
    fields = {k: getattr(body, k) for k in body.model_fields_set if k not in ("channel", "month", "sources")}
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
        fields.update(id=cid, channel_id=channel_id, month=body.month)
        c.execute(f"INSERT INTO growth_cohorts ({', '.join(fields)}) VALUES ({', '.join('?' * len(fields))})",
                  list(fields.values()))
    H.event(c, actor, "growth.cohort.set", cid, {"channel": slug or UNKNOWN, "month": body.month})
    return _cohort_view(H._one(c, "SELECT * FROM growth_cohorts WHERE id=?", (cid,)), _slugs(c))


def settings_update(c, actor, fields):
    H._writer(c, actor)
    c.execute("INSERT OR IGNORE INTO growth_settings (id) VALUES (1)")
    if "unit" in fields and fields["unit"] is None:
        raise Problem("validation", "unit cannot be cleared", 422)
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
def install(app, store, auth, mutate):
    from .goal_routes import _hidden_owners, kpi_editable

    def reader(who):
        auth.domain(who)

    def editor(who, what):
        auth.domain(who)
        if not admin(auth, who):
            raise Problem("forbidden", f"Only the owner or an admin {what}", 403)

    @app.get("/api/v2/growth/channels")
    def growth_channels(request: Request, include_retired: bool = False):
        """The channels, by sort then name. `may_edit`: the caller adds and changes any channel (owner or admin)."""
        who = request.state.identity
        reader(who)
        with store.read() as c:
            return {"channels": channels(c, include_retired), "may_edit": admin(auth, who)}

    @app.get("/api/v2/growth/channels/{slug}")
    def growth_channel(request: Request, slug: str):
        """One channel, retired or not, with its open marketing tasks (`tasks`: labelled channel:<slug>, the ones the
        reader may see)."""
        who = request.state.identity
        reader(who)
        with store.read() as c:
            row = channel(c, slug)
            if not row:
                raise Problem("not_found", f"No channel {slug}", 404)
            return {**channel_view(row), "tasks": channel_tasks(c, auth, who, slug)}

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
        """Owner or admin: retire a channel; its cohorts stay."""
        who = request.state.identity

        def work(c):
            editor(who, "retires a channel")
            return channel_retire(c, who.actor, slug)
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

    @app.get("/api/v2/growth/cohorts")
    def growth_cohorts(request: Request, channel: str | None = None, month: str | None = None,
                       limit: int = COHORTS_PAGE):
        """Monthly cohorts, newest month first; `channel=unknown` is the Unknown row."""
        reader(request.state.identity)
        with store.read() as c:
            return {"cohorts": cohorts(c, channel, month, limit)}

    @app.put("/api/v2/growth/cohorts")
    def growth_cohort_set(request: Request, body: M.GrowthCohortSet):
        """Owner, admin or any bot: one channel's month, created or updated; only the fields sent change."""
        who = request.state.identity

        def work(c):
            auth.domain(who)
            if who.role != "bot" and not admin(auth, who):
                raise Problem("forbidden", "The owner, an admin or a bot records a cohort", 403)
            return cohort_set(c, who.actor, body)
        return mutate(request, body, work)

    @app.get("/api/v2/growth/economics")
    def growth_economics(request: Request, month: str | None = None):
        """CAC, LTV and payback per channel for one month (the latest by default), the total and the guardrails."""
        who = request.state.identity
        reader(who)
        with store.read() as c:
            return economics(c, month, _hidden_owners(c, auth, who))

    @app.get("/api/v2/growth/settings")
    def growth_settings(request: Request):
        """The unit counted, the default revenue and margin. `may_edit`: the caller changes them (owner or admin)."""
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
