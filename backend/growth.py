"""Growth: the numbers that tell the story of growth. The channels the company acquires customers through, each month's
cohorts per channel (spend, bot cost, new customers, the share kept at 30, 90 and 180 days) and the unit economics
they add up to, and the sources each number's truth lives in.

- Channels (`growth_channels`): a performance or brand track and a stage (idea, testing, scaling, paused), an owner, a
  monthly budget, the source its numbers come from, and an optional guardrail (a KPI that must stay at or under a max).
  A channel's marketing work is the open tasks labelled `channel:<slug>` (backend/marketing_tasks.py).
- Sources (`gtm_sources`): which system is the truth for what, and how a bot reaches it. Two live sources for the
  same thing are a conflict the page shows. A source with a sync kind fills numbers itself, and each number records
  where it came from (backend/growth_sync.py).
- Cohorts (`growth_cohorts`): one row per channel per month (no channel is Unknown). `economics` turns them into CAC,
  LTV and payback.
- The KPI tree is four columns on `kpis` (parent, growth role, funnel stage, loop): at most one North Star.

Who may: everyone signed in reads. The owner and admins (people) write channels, sources and settings; the bot that
owns a channel may change its why, stage and budget; any bot or admin records cohorts.
"""

import json
import math

from fastapi import Request

from . import growth_finance as GF
from . import growth_sync as GS
from . import hubdb as H
from . import models as M
from .brand import admin
from .store import Problem

UNKNOWN = "unknown"
LIFETIME_CAP = 60  # months: a retention curve that never decays still counts at most five years
COHORTS_PAGE, COHORTS_MAX = 500, 2000
CHANNEL_TASKS_MAX = 200
CHANNEL_FIELDS = (
    "id",
    "slug",
    "name",
    "track",
    "stage",
    "owner_ref",
    "budget_month",
    "source_id",
    "why",
    "guardrail_kpi_id",
    "guardrail_max",
    "sort",
    "retired_at",
)
SOURCE_FIELDS = ("id", "truth_for", "system", "reach_kind", "reach_ref", "may", "owner_ref", "retired_at")
OWNER_PATCH = {"why", "stage", "budget_month"}  # what the bot that owns a channel may change
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
        tail = r1 * (1 - decay**left) / -math.log(decay)
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
    return {
        "spend": spend,
        "bot_cost": bot_cost,
        "new_customers": new,
        "cac": cac,
        "kept_d30": cohort.get("kept_d30"),
        "kept_d90": cohort.get("kept_d90"),
        "kept_d180": cohort.get("kept_d180"),
        "expected_months": months,
        "ltv": ltv,
        "ltv_cac": round(ltv / cac, 2) if ltv is not None and cac else None,
        "payback_months": round(cac / per_month, 2) if cac is not None and per_month else None,
        "quality": cohort.get("quality") or "actual",
        "sources": cohort.get("sources") or {},
        "origins": cohort.get("origins") or {},
        **{k: cohort[k] for k in BESIDE if k in cohort},
    }


# What `beside` adds to a channel's month and its economics carry: each figure's origin, and with customer facts how many
# customers it was computed from, the windows not yet due or too few, their dates, counts and a differing typed actual.
BESIDE = ("computed_or_typed", "computed_from", "status", "fills_on", "counts", "typed")


def economics_total(cohorts, settings):
    """Every channel together (see `aggregate`)."""
    total = aggregate(cohorts, settings)
    return {
        **economics_row({**total, "channel": "all"}, settings),
        "computed_or_typed": None,
        "channel": None,
        "name": "All channels",
        "track": None,
    }


def aggregate(cohorts, settings):
    """Cohorts as one: spend, bot cost and customers summed; retention, revenue and profit per customer-month
    (revenue x margin, each cohort's own) weighted by new customers. The margin shown is that profit over that revenue,
    so a high-revenue low-margin cohort never lends its revenue to another's margin. Unknown rows add no spend."""

    def value(c, key, fallback):
        return c.get(key) if c.get(key) is not None else fallback

    def profit(c):
        revenue, margin = (
            value(c, "revenue_month", settings.get("revenue_month")),
            value(c, "margin_pct", settings.get("margin_pct")),
        )
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
    total = {
        "spend": summed("spend", paid),
        "bot_cost": summed("bot_cost", paid),
        "new_customers": summed("new_customers", cohorts),
        "kept_d30": weighted("kept_d30"),
        "kept_d90": weighted("kept_d90"),
        "kept_d180": weighted("kept_d180"),
        "revenue_month": weighted("revenue_month", settings.get("revenue_month")),
        "profit_month": weighted(profit),
        "quality": "estimate" if any(c.get("quality") == "estimate" for c in cohorts) else "actual",
        "sources": {},
        "origins": {},
    }
    if total["new_customers"] is not None:
        total["new_customers"] = int(total["new_customers"])
    revenue, per_month = total["revenue_month"], total["profit_month"]
    total["margin_pct"] = round(per_month / revenue * 100, 2) if revenue and per_month is not None else None
    return total


# ----------------------------------------------------------------------------- reads
def settings(c):
    row = H._one(c, "SELECT * FROM growth_settings WHERE id=1") or {}
    return {
        "unit": row.get("unit") or "customers",
        "revenue_month": row.get("revenue_month"),
        "margin_pct": row.get("margin_pct"),
        "min_cohort": row.get("min_cohort") or 30,
        "time_zone": row.get("time_zone") or "UTC",
        "key_readers": json.loads(row.get("key_readers") or "[]"),
    }


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
    rows = H._rows(
        c.execute(
            "SELECT t.id, t.title, t.owner_id, t.status, t.updated_at FROM tasks t "
            "WHERE t.status='open' "
            f"AND t.id IN (SELECT id FROM tasks WHERE {auth.task_sql(c, who)}) "
            "AND t.id IN (SELECT tt.task_id FROM task_tags tt JOIN tags tg ON tg.id=tt.tag_id WHERE tg.key=?) "
            "ORDER BY t.updated_at DESC LIMIT ?",
            (key, limit),
        )
    )
    return [
        {
            "task_id": r["id"],
            "title": r["title"],
            "assignee": r["owner_id"],
            "status": r["status"],
            "updated_at": r["updated_at"],
        }
        for r in rows
    ]


def source_view(row):
    """A source and, when it syncs, its kind, what it fills, its mapping and health (backend/growth_sync.py)."""
    return {**{k: row.get(k) for k in SOURCE_FIELDS}, **GS.view(row)}


def sources(c, include_retired=False):
    rows = H._rows(
        c.execute(
            f"SELECT {GS.listed(c)} FROM gtm_sources"
            + ("" if include_retired else " WHERE retired_at IS NULL")
            + " ORDER BY truth_for COLLATE NOCASE, system COLLATE NOCASE"
        )
    )
    live = {}
    for r in rows:
        if not r["retired_at"]:
            live.setdefault(r["truth_for"].strip().lower(), []).append(r["truth_for"])
    return {
        "sources": [source_view(r) for r in rows],
        "conflicts": sorted(names[0] for names in live.values() if len(names) > 1),
    }


def _cohort_view(row, slugs, origins=None):
    return {
        "id": row["id"],
        "channel": slugs.get(row["channel_id"]) if row["channel_id"] else None,
        "month": row["month"],
        **{
            k: row[k]
            for k in (
                "spend",
                "bot_cost",
                "new_customers",
                "kept_d30",
                "kept_d90",
                "kept_d180",
                "revenue_month",
                "margin_pct",
                "quality",
            )
        },
        "sources": json.loads(row["sources"] or "{}"),
        "origins": (origins or {}).get(row["id"], {}),
        "updated_by": row["updated_by"],
        "updated_at": row["updated_at"],
    }


def _slugs(c):
    return {r[0]: r[1] for r in c.execute("SELECT id, slug FROM growth_channels")}


def cohort_months(c, until=None):
    """Every month with a typed cohort row or customer facts, newest first; `until` (YYYY-MM) leaves out later ones."""
    from . import growth_computed as GX

    found = {r[0] for r in c.execute("SELECT DISTINCT month FROM growth_cohorts")} | set(GX.months(c))
    return sorted((m for m in found if not until or m <= until), reverse=True)


# A typed figure customer facts can compute. The rest (spend, bot cost, revenue, margin) stay typed.
COMPUTED_FIELDS = ("new_customers", "kept_d30", "kept_d90", "kept_d180")
TYPED_FIELDS = (
    "spend",
    "bot_cost",
    "new_customers",
    "kept_d30",
    "kept_d90",
    "kept_d180",
    "revenue_month",
    "margin_pct",
)


def beside(row, computed, slug, month):
    """A channel's month with its computed figures laid over the typed ones (#287's rule: computed wins over a typed
    estimate; a typed actual that differs is kept in `typed`, beside it, for the channel's owner to choose). Every
    figure's `computed_or_typed` says which (beside #304's `origins`: typed or which source); a computed window that has not come due, or a cohort under the minimum,
    is null with its `status` (not_yet, too_few) and `fills_on` date. No customer facts: the typed row as it was."""
    view = (
        dict(row)
        if row
        else {
            "id": None,
            "channel": slug,
            "month": month,
            **{k: None for k in TYPED_FIELDS},
            "quality": "actual",
            "sources": {},
            "origins": {},
            "updated_by": None,
            "updated_at": None,
        }
    )
    view["computed_or_typed"] = {k: "typed" for k in TYPED_FIELDS if view.get(k) is not None}
    if not computed:
        return view
    actual = bool(row) and (row.get("quality") or "actual") == "actual"
    typed, status, fills, counts = {}, {}, {}, {}
    for field in COMPUTED_FIELDS:
        figure, old = computed[field], view.get(field)
        if actual and old is not None and (figure["status"] != "ok" or old != figure["value"]):
            typed[field] = old
        view[field] = figure["value"]
        view["computed_or_typed"][field] = "computed"
        if figure["status"] != "ok":
            status[field] = figure["status"]
        if figure.get("fills_on"):
            fills[field] = figure["fills_on"]
        if "denominator" in figure:
            counts[field] = {"count": figure["count"], "denominator": figure["denominator"]}
    view.update(computed_from=computed["computed_from"], status=status, fills_on=fills, counts=counts, typed=typed)
    return view


def cohorts(c, channel_slug=None, month=None, limit=COHORTS_PAGE):
    """Monthly cohorts by channel, newest month first, each with its computed figures beside the typed ones
    (`beside`). A channel's month with customer facts and no typed row is listed too."""
    from . import growth_computed as GX

    typed = _typed_cohorts(c, channel_slug, month, limit)
    computed = GX.by_channel_month(c, month)
    if channel_slug:
        computed = {k: v for k, v in computed.items() if (k[0] or UNKNOWN) == channel_slug}
    out, seen = [], set()
    for row in typed:
        key = (row["channel"], row["month"])
        seen.add(key)
        out.append(beside(row, computed.get(key), *key))
    out += [beside(None, figures, *key) for key, figures in computed.items() if key not in seen]
    out.sort(key=lambda r: (r["channel"] is None, r["channel"] or ""))
    out.sort(key=lambda r: r["month"], reverse=True)
    return out[: max(1, min(int(limit or COHORTS_PAGE), COHORTS_MAX))]


def _typed_cohorts(c, channel_slug=None, month=None, limit=COHORTS_PAGE):
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
    rows = H._rows(
        c.execute(
            "SELECT * FROM growth_cohorts"
            + (" WHERE " + " AND ".join(where) if where else "")
            + " ORDER BY month DESC, channel_id IS NULL, channel_id LIMIT ?",
            args + [limit],
        )
    )
    origins = GS.origins_view(c, [r["id"] for r in rows])
    return [_cohort_view(r, slugs, origins) for r in rows]


def _latest_value(c, kpi_id):
    from . import kpis as K

    newest = K.latest(K.readings(c, kpi_id, effective=True)) if kpi_id else None
    return newest


def guardrails(c, hidden=()):
    """Channels with a guardrail KPI and its latest value. `hidden`: owners whose KPIs the caller may not read
    (goal_routes._hidden_owners), as GET /growth/kpis; their guardrails are left out."""
    out = []
    for ch in H._rows(
        c.execute(
            "SELECT * FROM growth_channels WHERE retired_at IS NULL AND guardrail_kpi_id IS NOT NULL "
            "AND guardrail_max IS NOT NULL ORDER BY sort, name"
        )
    ):
        kpi = H._one(c, "SELECT name, owner_id FROM kpis WHERE id=?", (ch["guardrail_kpi_id"],)) or {}
        if kpi.get("owner_id") in hidden:
            continue
        newest = _latest_value(c, ch["guardrail_kpi_id"])
        value = newest["value"] if newest else None
        out.append(
            {
                "channel": ch["slug"],
                "kpi_id": ch["guardrail_kpi_id"],
                "kpi_name": kpi.get("name"),
                "value": value,
                "max": ch["guardrail_max"],
                "breached": value is not None and value > ch["guardrail_max"],
            }
        )
    return out


def economics(c, month=None, hidden=()):
    """One month by channel: each channel's row for it, the Unknown row beside them, the total and the guardrails.
    The latest month with cohorts by default."""
    months = cohort_months(c)
    month = month or (months[0] if months else None)
    conf = settings(c)
    by_slug = {r["slug"]: r for r in H._rows(c.execute("SELECT * FROM growth_channels"))}
    rows, inputs = [], []
    for cohort in cohorts(c, month=month, limit=COHORTS_MAX) if month else []:
        inputs.append(cohort)
        slug = cohort["channel"]
        ch = by_slug.get(slug) if slug else None
        rows.append(
            {
                "channel": slug or UNKNOWN,
                "name": ch["name"] if ch else "Unknown",
                "track": ch["track"] if ch else None,
                **economics_row(cohort, conf),
            }
        )
    blank = {
        k: None
        for k in (
            "spend",
            "bot_cost",
            "new_customers",
            "cac",
            "kept_d30",
            "kept_d90",
            "kept_d180",
            "expected_months",
            "ltv",
            "ltv_cac",
            "payback_months",
            "quality",
        )
    }
    if not rows:
        # A month with no numbers yet still lists every active channel and Unknown, each blank, for the first entry.
        rows = [
            {"channel": ch["slug"], "name": ch["name"], "track": ch["track"], **blank, "sources": {}, "origins": {}}
            for ch in by_slug.values()
            if not ch.get("retired_at")
        ]
        rows.append({"channel": UNKNOWN, "name": "Unknown", "track": None, **blank, "sources": {}, "origins": {}})
    rows.sort(key=lambda r: (r["channel"] == UNKNOWN, (by_slug.get(r["channel"]) or {}).get("sort", 0), r["name"]))
    total = economics_total(inputs, conf)
    if not inputs:
        total["quality"] = None
    return {
        "month": month,
        "months": months,
        "settings": conf,
        "rows": rows,
        "total": total,
        "guardrails": guardrails(c, hidden),
    }


def kpis(c, hidden=(), may_edit=lambda record: False):
    rows = H._rows(c.execute("SELECT * FROM kpis WHERE archived_at IS NULL ORDER BY created_at"))
    rows = [r for r in rows if r.get("owner_id") not in hidden]
    from . import kpis as K

    readings = K.effective_many(c, [r["id"] for r in rows])
    out = []
    for r in rows:
        newest = K.latest(readings.get(r["id"], []))
        link = (
            H._one(
                c,
                "SELECT goal_id, target FROM goal_kpis WHERE kpi_id=? ORDER BY target IS NULL, created_at LIMIT 1",
                (r["id"],),
            )
            or {}
        )
        out.append(
            {
                "id": r["id"],
                "name": r["name"],
                "unit": r.get("unit") or "",
                "latest": newest["value"] if newest else None,
                "latest_at": (newest.get("period_end") or newest.get("collected_at")) if newest else None,
                "target": link.get("target"),
                "goal_id": link.get("goal_id"),
                "may_edit": bool(may_edit(r)),
                **{k: r.get(k) for k in KPI_FIELDS},
            }
        )
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
    row = {
        "id": H.new_id(),
        "slug": body.slug,
        "name": body.name,
        "track": body.track,
        "stage": body.stage,
        "owner_ref": _owner_ref(c, body.owner_ref),
        "budget_month": body.budget_month,
        "source_id": _source_ref(c, body.source_id),
        "why": body.why,
        "guardrail_kpi_id": _kpi_ref(c, body.guardrail_kpi_id, "guardrail_kpi_id"),
        "guardrail_max": body.guardrail_max,
        "sort": body.sort,
        "created_at": H.now(),
    }
    c.execute(
        f"INSERT INTO growth_channels ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", list(row.values())
    )
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
        c.execute(
            f"UPDATE growth_channels SET {', '.join(k + '=?' for k in changed)} WHERE id=?",
            list(changed.values()) + [row["id"]],
        )
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


def _sync_fields(c, fields, sid, kind, catalog=None, before=None):
    """A synced source's mapping, checked (backend/growth_sync.py check_map), as the JSON column holds it. A database
    query source's bot must be a live bot and its query a named query of that database that needs no parameter; it
    keeps the daily automation it had (`before`, the map it held)."""
    mapping = fields.get("sync_map")
    if hasattr(mapping, "model_dump"):
        mapping = mapping.model_dump(exclude_none=True)
    if kind == "db_query" and before and isinstance(mapping, dict):
        mapping = {**{k: before[k] for k in GS.DB_KEYS if before.get(k)}, **mapping}  # a remap keeps what it runs
    out = GS.check_map(c, kind, mapping, sid)
    if kind == "db_query":
        found = H.resolve_bot(c, out["bot"])
        if not found or found.get("state") == "archived":
            raise Problem("validation", f"No bot {out['bot']} to run the query", 422)
        out["bot"] = found["id"]
        _check_query(catalog, out["database"], out["query"])
        had = (before or {}).get("automation")
        if had:
            out["automation"] = had
    return json.dumps(out, sort_keys=True)


def _check_query(catalog, database, query_id):
    """A daily run passes no parameters, so the named query must exist and need none without a default."""
    if catalog is None:
        return
    try:
        page = catalog.resolve(database)
    except Problem:
        raise Problem(
            "validation",
            f"No database {database}: add its page and named queries to the team's config (docs/databases.md)",
            422,
        ) from None
    query = next((q for q in page.get("queries") or [] if q.get("id") == query_id), None)
    if query is None:
        raise Problem(
            "validation",
            f"No query {query_id} in the {database} catalog (integrations/queries/{database}.yaml); "
            f"`tico tool query-search {database} <term>` lists them",
            422,
        )
    needs = [p["name"] for p in query.get("params") or [] if p.get("required") and "default" not in p]
    if needs:
        raise Problem(
            "validation",
            f"Query {query_id} needs {', '.join(needs)}; a daily run passes none, so give each a default",
            422,
        )


DB_RUN_VIA = "growth source"
# Fixed words and the source's id only: nothing a person types reaches the bot's turn. The command reads the source's
# query and database itself.
DB_RUN_PROMPT = (
    "Run `tico growth source run {sid}` and reply with the line it prints. It runs this Growth source's named query on "
    "this computer and posts its rows, or the reason the query failed. Do nothing else."
)


def _start_db_run(c, who, row):
    """`sync` on a database query source: its query runs on its bot's computer, so start its daily run now. The bot's
    turn posts the result, which `tico growth source list` then shows. Starting it is running the bot's automation, so
    it takes the rule POST /api/v2/automations/{id}/run takes (automations.may_change); the source's bot may always
    start its own run."""
    from . import automations as AU

    mapping = json.loads(row.get("sync_map") or "{}")
    found = AU._row(c, mapping.get("automation") or "")
    if found and who.actor != GS.run_bot(row):
        AU._require(c, who, AU.flow_of(found), "start the daily run of " + AU.flow_of(found)["owner"])
    if not found:
        raise Problem(
            "conflict", f"{row['system']} has no daily run: connect it again (tico growth source update)", 409
        )
    run = AU.queue(c, found, {"manual": {"by": who.actor}}, "manual", manual=True, chain=(found["id"],))
    if not run:
        raise Problem("on_hold", f"{row['system']}'s daily run is on hold: release or drop its held runs first", 409)
    return {
        "run": None,
        "source": row["id"],
        "started": run,
        "ok": None,
        "text": f"Started: {mapping['query']} runs on {mapping['database']} now; "
        "tico growth source list shows the result.",
    }


def _schedule(c, actor, sid, had=None, who=None):
    """Keep a database query source's daily run in step with it: a schedule automation whose one step is a turn by the
    bot granted the database. Made when it connects, changed when its bot or query changes, removed when it is retired
    or stops being a database query source (`had`: the automation its map named before the change). Making or changing
    it follows the automation rule for its bot (`who`, automations.may_change), as POST /api/v2/automations does: an
    admin not above the bot may not schedule its turn through a source."""
    from . import automation_flow as F
    from . import automations as AU

    row = H._one(c, "SELECT * FROM gtm_sources WHERE id=?", (sid,))
    if not row:
        return
    mapping = json.loads(row.get("sync_map") or "{}")
    aid = mapping.get("automation") or had
    existing = AU._row(c, aid) if aid else None
    if row.get("sync_kind") != "db_query" or row.get("retired_at"):
        if existing:
            AU.remove(c, actor, existing, via=DB_RUN_VIA)
        return
    bot = mapping["bot"]
    flow = F.check(
        {
            "name": f"Growth: sync {row['system']}"[:120],
            "description": f"Runs {mapping['query']} on {mapping['database']} daily and posts its rows to Growth.",
            "owner": "bot:" + bot,
            "enabled": True,
            "trigger": {"type": "schedule", "cron": GS.DB_CRON, "tz": "UTC"},
            "steps": [
                {
                    "id": "sync",
                    "type": "bot_turn",
                    "bot": "bot:" + bot,
                    "prompt": DB_RUN_PROMPT.format(sid=sid),
                }
            ],
        }
    )
    if existing:
        before = AU.flow_of(existing)
        if {k: before.get(k) for k in flow} != flow:
            if who is not None:
                AU._require(c, who, flow, "schedule a daily run for " + flow["owner"])
            AU._save(c, actor, aid, flow, before, via=DB_RUN_VIA)
        return
    if who is not None:
        AU._require(c, who, flow, "schedule a daily run for " + flow["owner"])
    aid = "auto_" + H.new_id()
    AU._save(c, actor, aid, flow, None, via=DB_RUN_VIA)
    mapping["automation"] = aid
    c.execute("UPDATE gtm_sources SET sync_map=? WHERE id=?", (json.dumps(mapping, sort_keys=True), sid))


def source_create(c, actor, body, catalog=None, who=None):
    H._writer(c, actor)
    # A file comes from a person; Finance ad spend through Finance's own import and its stored keys.
    reach = body.reach_kind or {"file": "person", GF.KIND: "credential"}.get(body.sync_kind or "")
    if not reach:
        raise Problem("validation", "reach_kind is mcp, credential, url or person", 422)
    sid = H.new_id()
    row = {
        "id": sid,
        "truth_for": body.truth_for,
        "system": body.system,
        "reach_kind": reach,
        "reach_ref": body.reach_ref,
        "may": body.may,
        "owner_ref": _owner_ref(c, body.owner_ref),
        "created_at": H.now(),
    }
    if body.sync_kind == GF.KIND:
        GF.may_connect(c, actor)
    if body.sync_kind:
        row.update(
            sync_kind=body.sync_kind,
            sync_map=_sync_fields(c, {"sync_map": body.sync_map}, sid, body.sync_kind, catalog),
            refresh_hours=body.refresh_hours,
        )
    c.execute(f"INSERT INTO gtm_sources ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", list(row.values()))
    H.event(
        c,
        actor,
        "growth.source.create",
        row["id"],
        {"truth_for": body.truth_for, "system": body.system, "sync_kind": body.sync_kind},
    )
    _schedule(c, actor, sid, who=who)
    return source_view(H._one(c, "SELECT * FROM gtm_sources WHERE id=?", (row["id"],)))


def source_update(c, actor, sid, fields, retire=False, catalog=None, who=None):
    H._writer(c, actor)
    row = H._one(c, "SELECT * FROM gtm_sources WHERE id=?", (sid,))
    if not row:
        raise Problem("not_found", f"No source {sid}", 404)
    if "owner_ref" in fields:
        fields["owner_ref"] = _owner_ref(c, fields["owner_ref"])
    for required in ("truth_for", "system", "reach_kind", "reach_ref", "may"):
        if required in fields and fields[required] is None:
            raise Problem("validation", f"{required} cannot be cleared", 422)
    if "sync_map" in fields or "sync_kind" in fields:
        kind = fields.get("sync_kind", row.get("sync_kind"))
        if kind == GF.KIND:
            GF.may_connect(c, actor)
        mapping = fields.get("sync_map") if "sync_map" in fields else json.loads(row.get("sync_map") or "{}")
        before = json.loads(row.get("sync_map") or "{}")
        fields["sync_map"] = _sync_fields(c, {"sync_map": mapping}, sid, kind, catalog, before) if kind else "{}"
    if retire and not row["retired_at"]:
        fields["retired_at"] = H.now()
    changed = {k: v for k, v in fields.items() if row.get(k) != v}
    if changed:
        c.execute(
            f"UPDATE gtm_sources SET {', '.join(k + '=?' for k in changed)} WHERE id=?", list(changed.values()) + [sid]
        )
        logged = dict(changed)
        if "sync_map" in logged and (changed.get("sync_kind") or row.get("sync_kind")) == GF.KIND:
            # The event log is read more widely than Finance: it says how many campaigns are mapped, never which.
            logged["sync_map"] = json.dumps(GF.public_map(json.loads(logged["sync_map"] or "{}")), sort_keys=True)
        H.event(c, actor, "growth.source." + ("retire" if retire else "update"), sid, logged)
        _schedule(c, actor, sid, json.loads(row.get("sync_map") or "{}").get("automation"), who)
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
    row = H._one(
        c, "SELECT * FROM growth_cohorts WHERE coalesce(channel_id,'')=? AND month=?", (channel_id or "", body.month)
    )
    fields = {k: getattr(body, k) for k in body.model_fields_set if k not in ("channel", "month", "sources")}
    if "quality" in fields and fields["quality"] is None:
        fields["quality"] = "actual"
    GS.refuse_typed(c, row, [k for k in fields if k in GS.FIELDS], slug or UNKNOWN, body.month)
    sources = dict(json.loads(row["sources"] or "{}")) if row else {}
    if body.sources is not None:
        for key, text in body.sources.items():
            if text:
                sources[key] = text
            else:
                sources.pop(key, None)
    fields.update(sources=json.dumps(sources, sort_keys=True), updated_by=actor, updated_at=H.now())
    if row:
        c.execute(
            f"UPDATE growth_cohorts SET {', '.join(k + '=?' for k in fields)} WHERE id=?",
            list(fields.values()) + [row["id"]],
        )
        cid = row["id"]
    else:
        cid = H.new_id()
        fields.update(id=cid, channel_id=channel_id, month=body.month)
        c.execute(
            f"INSERT INTO growth_cohorts ({', '.join(fields)}) VALUES ({', '.join('?' * len(fields))})",
            list(fields.values()),
        )
    if "quality" in fields:
        c.execute("UPDATE growth_origins SET quality=? WHERE cohort_id=? AND origin='typed'", (fields["quality"], cid))
    saved = H._one(c, "SELECT * FROM growth_cohorts WHERE id=?", (cid,)) or {}
    GS.record_typed(
        c, cid, {k: v for k, v in fields.items() if k in GS.FIELDS}, saved.get("quality") or "actual", actor
    )
    H.event(c, actor, "growth.cohort.set", cid, {"channel": slug or UNKNOWN, "month": body.month})
    return _cohort_view(saved, _slugs(c), GS.origins_view(c, [cid]))


def settings_update(c, actor, fields):
    H._writer(c, actor)
    c.execute("INSERT OR IGNORE INTO growth_settings (id) VALUES (1)")
    if "unit" in fields and fields["unit"] is None:
        raise Problem("validation", "unit cannot be cleared", 422)
    if fields.get("time_zone"):
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(fields["time_zone"])
        except (ZoneInfoNotFoundError, ValueError):
            raise Problem("validation", "time_zone is an IANA zone, such as Europe/London", 422) from None
    if "key_readers" in fields:
        # Who besides the owner and admins sees customer keys in a figure's trace: people and bots, by any name.
        readers = {str(_owner_ref(c, ref)) for ref in fields["key_readers"] or [] if ref}
        fields["key_readers"] = json.dumps(sorted(readers))
    if fields:
        c.execute(f"UPDATE growth_settings SET {', '.join(k + '=?' for k in fields)} WHERE id=1", list(fields.values()))
        H.event(c, actor, "growth.settings", "settings", fields)
    return settings(c)


def kpi_fields(c, actor, kpi_id, fields):
    """The KPI tree columns (`tico kpi update`): parent, growth role, funnel stage and loop. One North Star at a time:
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
    from . import growth_setup as SETUP
    from .goal_routes import _hidden_owners, kpi_editable

    def answer(who, out, seen=False):
        """A read's answer with what the caller may do and Growth's setup (backend/growth_setup.py)."""
        return SETUP.attach(store, auth, who, out, seen=seen)

    def wrote(c, who, result):
        """After a write: the setup steps it finished get their first done date, and the answer says what is next,
        so the four steps chain without a read between them."""
        SETUP.record(c, who.actor)
        return {**result, "setup": SETUP.block(c, auth, who)}

    from . import growth_computed, growth_customers

    growth_customers.install(app, store, auth, mutate)  # customer facts in: load, delete
    growth_computed.install(app, store, auth)  # cohorts computed from them

    def reader(who):
        auth.domain(who)

    def editor(who, what):
        auth.domain(who)
        if not admin(auth, who):
            raise Problem("forbidden", f"Only the owner or an admin {what}", 403)

    @app.get("/api/v2/growth/channels")
    def growth_channels(request: Request, include_retired: bool = False):
        """The channels, by sort then name. `may_edit`: the caller adds and changes any channel (owner or admin);
        `you_may` says what the caller may do per action and `setup` what is left to set up (backend/growth_setup.py)."""
        who = request.state.identity
        reader(who)
        with store.read() as c:
            out = {"channels": channels(c, include_retired), "may_edit": admin(auth, who)}
        return answer(who, out)

    @app.get("/api/v2/growth/channels/{slug}")
    def growth_channel(request: Request, slug: str):
        """One channel, retired or not, with its open marketing tasks (`tasks`: labelled channel:<slug>, the ones the
        reader may see), what the caller may do and what is left to set up."""
        who = request.state.identity
        reader(who)
        with store.read() as c:
            row = channel(c, slug)
            if not row:
                raise Problem("not_found", f"No channel {slug}", 404)
            out = {**channel_view(row), "tasks": channel_tasks(c, auth, who, slug)}
        return answer(who, out, seen=True)

    @app.post("/api/v2/growth/channels")
    def growth_channel_add(request: Request, body: M.GrowthChannelCreate):
        """Owner or admin: a new channel."""
        who = request.state.identity

        def work(c):
            editor(who, "adds a channel")
            return wrote(c, who, channel_create(c, who.actor, body))

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
                    raise Problem(
                        "forbidden", "Only the owner, an admin or the bot that owns the channel changes it", 403
                    )
                if set(fields) - OWNER_PATCH:
                    raise Problem(
                        "forbidden",
                        "The bot that owns a channel changes its why, stage and budget_month; "
                        "an admin changes the rest",
                        403,
                    )
            return wrote(c, who, channel_update(c, who.actor, slug, fields))

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
        changes sources (owner or admin); `you_may` per action, `setup` what is left to set up."""
        who = request.state.identity
        reader(who)
        with store.read() as c:
            out = {**sources(c, include_retired), "may_edit": admin(auth, who)}
        return answer(who, out)

    @app.post("/api/v2/growth/sources")
    def growth_source_add(request: Request, body: M.GrowthSourceCreate):
        """Owner or admin: a new source of truth."""
        who = request.state.identity

        def work(c):
            editor(who, "adds a source")
            return wrote(
                c, who, source_create(c, who.actor, body, getattr(request.app.state, "integrations", None), who)
            )

        return mutate(request, body, work)

    @app.patch("/api/v2/growth/sources/{sid}")
    def growth_source_update(request: Request, sid: str, body: M.GrowthSourceUpdate):
        """Owner or admin: change a source."""
        who = request.state.identity

        def work(c):
            editor(who, "changes a source")
            return wrote(
                c,
                who,
                source_update(
                    c,
                    who.actor,
                    sid,
                    {k: getattr(body, k) for k in body.model_fields_set},
                    catalog=getattr(request.app.state, "integrations", None),
                    who=who,
                ),
            )

        return mutate(request, body, work)

    @app.post("/api/v2/growth/sources/{sid}/retire")
    def growth_source_retire(request: Request, sid: str, body: M.Empty):
        """Owner or admin: retire a source."""
        who = request.state.identity

        def work(c):
            editor(who, "retires a source")
            return source_update(c, who.actor, sid, {}, retire=True)

        return mutate(request, body, work)

    @app.post("/api/v2/growth/sources/{sid}/rows")
    def growth_source_rows(request: Request, sid: str, body: M.GrowthSourceRows):
        """Owner, admin or the source's own owner: one run of a synced source from a CSV, rows, or the error its
        fetch ended with. A run that cannot write answers ok false with the reason; nothing it wrote changes."""
        who = request.state.identity

        def work(c):
            auth.domain(who)
            row = GS.may_run(c, sid, who.actor, admin(auth, who))
            sent = [k for k in ("csv", "rows", "error") if getattr(body, k) is not None]
            if len(sent) != 1:
                raise Problem("validation", "Send one of csv, rows or error", 422)
            return GS.run(c, who.actor, row, rows=body.rows, csv_text=body.csv, error=body.error)

        return mutate(request, body, work)

    @app.post("/api/v2/growth/sources/{sid}/sync")
    def growth_source_sync(request: Request, sid: str, body: M.Empty):
        """Owner, admin or the source's own owner: run a synced source now. A file source runs its last file again
        (after a remap, say)."""
        who = request.state.identity

        def work(c):
            auth.domain(who)
            row = GS.may_run(c, sid, who.actor, admin(auth, who))
            if row["sync_kind"] == "file":
                if not row.get("last_payload"):
                    raise Problem(
                        "conflict", f"{row['system']} has no file yet: send one with tico growth source push", 409
                    )
                return GS.run(c, who.actor, row, csv_text=row["last_payload"])
            if row["sync_kind"] == "db_query":
                return _start_db_run(c, who, row)
            if row["sync_kind"] == GF.KIND:
                return GF.sync(c, who.actor, row)
            raise Problem("conflict", f"{row['system']} cannot be run from here yet", 409)

        return mutate(request, body, work)

    @app.get("/api/v2/growth/cohorts")
    def growth_cohorts(
        request: Request, channel: str | None = None, month: str | None = None, limit: int = COHORTS_PAGE
    ):
        """Monthly cohorts, newest month first; `channel=unknown` is the Unknown row. `you_may` and `setup` as the other
        Growth reads."""
        who = request.state.identity
        reader(who)
        with store.read() as c:
            out = {"cohorts": cohorts(c, channel, month, limit)}
        return answer(who, out)

    @app.put("/api/v2/growth/cohorts")
    def growth_cohort_set(request: Request, body: M.GrowthCohortSet):
        """Owner, admin or any bot: one channel's month, created or updated; only the fields sent change."""
        who = request.state.identity

        def work(c):
            auth.domain(who)
            if who.role != "bot" and not admin(auth, who):
                raise Problem("forbidden", "The owner, an admin or a bot records a cohort", 403)
            return wrote(c, who, cohort_set(c, who.actor, body))

        return mutate(request, body, work)

    @app.post("/api/v2/growth/cohorts/conflicts")
    def growth_cohort_conflict(request: Request, body: M.GrowthConflictResolve):
        """The source's owner or an admin: a number someone typed as actual that a synced source disagrees with. Keep
        the typed number (the source's value is not raised again) or use the source's (it then belongs to it)."""
        who = request.state.identity

        def work(c):
            auth.domain(who)
            slug = body.channel if body.channel not in (None, "", UNKNOWN) else None
            channel_id = ""
            if slug:
                found = channel(c, slug)
                if not found:
                    raise Problem("not_found", f"No channel {slug}", 404)
                channel_id = found["id"]
            row = H._one(
                c, "SELECT id FROM growth_cohorts WHERE coalesce(channel_id,'')=? AND month=?", (channel_id, body.month)
            )
            if not row:
                raise Problem("not_found", f"No numbers for {slug or UNKNOWN} in {body.month}", 404)
            return GS.resolve(c, who.actor, admin(auth, who), row["id"], body.field, body.keep)

        return mutate(request, body, work)

    @app.get("/api/v2/growth/economics")
    def growth_economics(request: Request, month: str | None = None):
        """CAC, LTV and payback per channel for one month (the latest by default), the total and the guardrails;
        `you_may` and `setup` as the other Growth reads."""
        who = request.state.identity
        reader(who)
        with store.read() as c:
            out = economics(c, month, _hidden_owners(c, auth, who))
        return answer(who, out, seen=True)

    @app.get("/api/v2/growth/settings")
    def growth_settings(request: Request):
        """The unit counted, the default revenue and margin. `may_edit`: the caller changes them (owner or admin);
        `you_may` per action, `setup` what is left to set up."""
        who = request.state.identity
        reader(who)
        with store.read() as c:
            out = {**settings(c), "may_edit": admin(auth, who)}
        return answer(who, out)

    @app.put("/api/v2/growth/settings")
    def growth_settings_update(request: Request, body: M.GrowthSettingsUpdate):
        """Owner or admin: only the fields sent change."""
        who = request.state.identity

        def work(c):
            editor(who, "changes the growth settings")
            return wrote(c, who, settings_update(c, who.actor, {k: getattr(body, k) for k in body.model_fields_set}))

        return mutate(request, body, work)

    @app.get("/api/v2/growth/kpis")
    def growth_kpis(request: Request):
        """Every KPI the caller may read with its latest value, target and place in the KPI tree; `you_may` and `setup`
        as the other Growth reads."""
        who = request.state.identity
        reader(who)
        with store.read() as c:
            out = kpis(c, _hidden_owners(c, auth, who), lambda record: kpi_editable(c, who, record))
        return answer(who, out)

    @app.get("/api/v2/growth/setup")
    def growth_setup(request: Request):
        """Growth's first run: the four steps (settings, a channel, its source, its first month), when each was first
        done, the next one with its CLI command and MCP tool and whether the caller may do it (else who may), what the
        caller may do per action, and when the team first opened Growth and first saw its numbers."""
        who = request.state.identity
        reader(who)
        answer(who, {})  # the first open, when it is one
        with store.read() as c:
            return SETUP.show(c, auth, who)

    @app.post("/api/v2/growth/setup/shown")
    def growth_setup_shown(request: Request, body: M.Empty):
        """A person's Get started panel said "you're set up" (ui/app/growth-start.ts): it is said once per team, so the
        panel does not come back. Before setup is done it records nothing. Answers as GET /api/v2/growth/setup."""
        who = request.state.identity

        def work(c):
            reader(who)
            if who.role == "bot":
                raise Problem(
                    "forbidden", "The Get started panel is a person's; a bot reads GET /api/v2/growth/setup", 403
                )
            SETUP.shown(c)
            return SETUP.show(c, auth, who)

        return mutate(request, body, work)
