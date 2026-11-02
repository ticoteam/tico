"""Usage: what the bots' model runs cost, estimated from their token counts (`GET /api/v2/usage`).

Each finished run carries the tokens its runner counted, the model that ran it and, when the model
has a list price (providers.PRICES), an estimated cost in USD. A run on a ChatGPT or Claude sign-in
is `subscription`: it is billed to the plan, so its cost is what the same tokens would cost through
the API, shown apart and never added to the estimate of what was spent. Days are UTC.

Who sees what: the owner and the bot administrators see every bot; anyone else sees the bots they
run or own. A bot's own credentials see nothing here.
"""

import json
from datetime import date, datetime, timedelta, timezone

from fastapi import Request

from . import bot_access as A
from . import models as M
from . import providers, usage_limits, views
from .store import H, P, Problem
from .usage_records import RUNS
from .subscription_identity import usage_label

DIMENSIONS = {"harness": "coalesce(t.harness, '')", "model": "coalesce(t.model, '')",
              "effort": "coalesce(t.effort, '')", "subscription": "CASE WHEN t.billing='subscription' AND coalesce(t.profile, '') != '' "
              "THEN coalesce((SELECT 'subscription:' || json_array(a.runner_id, t.profile) FROM attempts a WHERE a.id=t.id), "
              "'legacy-profile:' || t.profile) ELSE '' END"}
GROUPS = ("bot", "day", "routine", *DIMENSIONS)
MAX_DAYS = 366
TOP_ROUTINES = 10

# A priced run whose estimate may be low: its model's long prompts cost more and the runner did not send every
# request's tokens (providers.estimate). A run recorded before that was marked is judged by its model alone.
LOW = ("coalesce(t.est_cost_low, lower(coalesce(t.model, '')) IN ("
       + ",".join(f"'{model}'" for model in sorted(providers.LONG_PROMPT)) + "))")

# Sums over `turns` shared by every grouping. A run with no token counts (a runtime that reports none,
# or one from before usage) still counts as a run.
SUMS = f"""count(DISTINCT t.id) AS runs,
  sum(coalesce(t.input_tokens, 0)) AS input_tokens,
  sum(coalesce(t.cached_tokens, 0)) AS cached_tokens,
  sum(coalesce(t.output_tokens, 0)) AS output_tokens,
  sum(CASE WHEN t.billing = 'subscription' OR t.usage_reason IS NOT NULL THEN 0 ELSE coalesce(t.est_cost_usd, 0) END) AS api_usd,
  sum(CASE WHEN t.billing = 'subscription' AND t.usage_reason IS NULL THEN coalesce(t.est_cost_usd, 0) ELSE 0 END) AS subscription_usd,
  count(DISTINCT CASE WHEN coalesce(t.billing, 'api') != 'subscription' AND t.est_cost_usd IS NOT NULL AND t.usage_reason IS NULL THEN t.id END) AS api_priced,
  count(DISTINCT CASE WHEN coalesce(t.billing, 'api') != 'subscription' AND (t.usage_reason IS NOT NULL OR (t.est_cost_usd IS NULL
            AND coalesce(t.input_tokens, 0) + coalesce(t.cached_tokens, 0) + coalesce(t.output_tokens, 0) > 0))
       THEN t.id END) AS api_unpriced,
  count(DISTINCT CASE WHEN t.est_cost_usd IS NOT NULL AND t.usage_reason IS NULL AND {LOW} THEN t.id END) AS low_priced"""
ROUTINE_JOIN = ("LEFT JOIN schedule_occurrences o ON o.task_id = t.task_id "
                "LEFT JOIN schedules s ON s.id = o.schedule_id")
EMPTY_DAY = {"runs": 0, "input_tokens": 0, "cached_tokens": 0, "output_tokens": 0, "est_cost_usd": 0.0,
             "subscription_equiv_usd": 0.0, "unpriced_runs": 0, "low_estimate_runs": 0}


def money(value):
    return round(float(value or 0), 6)


def figures(row):
    """The fields every row shares. `est_cost_usd` is null when API runs have no priced reading
    but have unpriced tokens or unreported usage, so the page shows a dash and not a false zero.
    `low_estimate_runs` counts the priced runs whose figure may be low (LOW)."""
    return {"runs": row["runs"], "input_tokens": row["input_tokens"], "cached_tokens": row["cached_tokens"],
            "output_tokens": row["output_tokens"],
            "est_cost_usd": None if not row["api_priced"] and row["api_unpriced"] else money(row["api_usd"]),
            "subscription_equiv_usd": money(row["subscription_usd"]),
            "unpriced_runs": row["api_unpriced"], "low_estimate_runs": row["low_priced"]}


def spend(item):
    """What ranks a row and sizes its bar: estimated spend plus the API-equivalent of subscription runs."""
    return (item["est_cost_usd"] or 0) + item["subscription_equiv_usd"]


def own_bots(c, auth, who):
    """The slugs `who` may see usage for: every bot for the owner and administrators, else the
    bots they run or own."""
    slugs = {row["slug"] for row in H.bots(c)}
    if auth.bot_admin(who):
        return slugs
    pid = H.actor_id(who.actor)
    mine = set()
    for row in c.execute("SELECT bot, operator, bot_owners FROM bot_config"):
        if row["operator"] == pid or pid in A.owner_ids(row["bot_owners"]):
            mine.add(row["bot"])
    return mine & slugs

def valid(value, name):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= usage_limits.MAX_USD:
        raise Problem("limit", f"{name} is an amount in USD above zero, or empty for none", 422)
    return round(float(value), 6)

def amounts(c, body):
    """The caps a save changes, by period; the caller lays them over what is stored.

    Only the fields the request sent: one left out keeps its stored value and an explicit null clears it. The
    30-minute window replaces the hour, so an `hourly_usd` becomes the half hour at the same rate (half of it, up to
    the next $0.10) and the hour is cleared."""
    caps = {key: valid(getattr(body, key, None), key) for key in usage_limits.KEYS if key in body.model_fields_set}
    if caps.get("hourly_usd") is not None:
        if caps.get("per_30m_usd") is not None:
            raise Problem("limit", "Send per_30m_usd or hourly_usd, not both", 422)
        caps["per_30m_usd"] = usage_limits.half(caps["hourly_usd"])
        caps["hourly_usd"] = None
    return caps

def led_bots(c, who):
    """The bots a group lead may set limits for: every bot in a group the caller leads, nested groups included. Empty
    when the caller leads no group."""
    from . import groups as G
    out = set()
    for gid in G.led_groups(c, who.actor):          # a lead who left leads nothing
        out |= usage_limits.members(c, "group:" + gid)
    return out

def parents_allow(c, gid, caps, named):
    """A group's limit may not exceed its parent group's, nor the one above that."""
    seen, parent = {gid}, (named.get(gid) or {}).get("parent")
    while parent and parent not in seen:
        seen.add(parent)
        above = usage_limits.pool(c, "group:" + parent)
        for key, value in caps.items():
            if value is not None and above[key] is not None and value > above[key]:
                raise Problem("limit", f"Over {usage_limits.label(c, 'group:' + parent)}: its limit is ${above[key]:g}, "
                              "so a group's limit can be lower, not higher", 422)
        parent = (named.get(parent) or {}).get("parent")


def _audit(c, who, obj, before, after, via):
    """A spend change as one audit row (CONTRACT §5, object `spend:<bot>` or `spend:pool:<scope>`)."""
    if before != after:
        from . import audit_log
        audit_log.write(c, who.actor, obj, before, after, via)


def _caps_of(row):
    return {key: row[key] for key in usage_limits.KEYS} if row and any(row[key] is not None for key in usage_limits.KEYS) \
        else None


def save_pool(c, auth, who, scope, body, via=None):
    """A limit on the combined spend of the whole team or a group; a field set to null removes it, a field left out keeps it. The owner and bot administrators;
    also the lead of the group or of one above it."""
    gid = scope.split(":", 1)[1] if scope != usage_limits.COMPANY else None
    if not auth.bot_admin(who):
        from . import groups as G
        if gid is None or gid not in G.led_groups(c, who.actor):
            raise Problem("forbidden", "Only the owner and administrators set team and group limits", 403)
    default = usage_limits.company(c)
    named = usage_limits.groups(c, default)[0]
    if gid is not None and gid not in named:
        raise Problem("not_found", "Group not found", 404)
    changed = amounts(c, body)
    if gid is not None:
        parents_allow(c, gid, changed, named)
    stored = usage_limits.pool(c, scope)
    before = _caps_of(stored)
    usage_limits.save_pool(c, scope, {**stored, **changed}, who.actor)
    H.event(c, who.actor, "usage.pool-set", scope, changed)
    _audit(c, who, "spend:pool:" + scope, before, _caps_of(usage_limits.pool(c, scope)), via)
    return {"scope": scope, "limit": usage_limits.pool_state(c, scope, default=usage_limits.company(c))}


def save_limit(c, auth, who, bot, body, via=None):
    """A bot's own limits; a field set to null follows the company default, a field left out keeps it. Its owners and administrators, and the lead of a group it
    is in. Returns (the saved caps, who may edit)."""
    led = led_bots(c, who)
    if who.role not in ("owner", "human") and not led:
        raise Problem("forbidden", "Usage is for people", 403)
    if not H.bot(c, bot) or (who.role != "bot" and not auth.visible_bot(c, who, bot)):
        raise Problem("not_found", "Bot not found", 404)
    allowed = own_bots(c, auth, who) | led
    if bot not in allowed:
        raise Problem("forbidden", "Limits are for a bot's owners and administrators", 403)
    wanted = amounts(c, body)
    default = usage_limits.company(c)
    if not auth.bot_admin(who):
        for key, value in wanted.items():
            if value is not None and default[key] is not None and value > default[key]:
                raise Problem("limit", f"The company default is ${default[key]:g}; a bot's limit can be lower, not higher", 403)
    stored = usage_limits.own(c, bot)
    before, saved = _caps_of(stored), {**stored, **wanted}
    marks = ",".join("?" * len(usage_limits.KEYS))
    c.execute(f"INSERT INTO usage_limits(bot, {', '.join(usage_limits.KEYS)}, updated_at, updated_by) VALUES(?,{marks},?,?) "
              "ON CONFLICT(bot) DO UPDATE SET " + ", ".join(f"{key}=excluded.{key}" for key in usage_limits.KEYS) +
              ", updated_at=excluded.updated_at, updated_by=excluded.updated_by",
              (bot, *(saved[key] for key in usage_limits.KEYS), H.now(), who.actor))
    H.event(c, who.actor, "usage.limit-set", H.bot_actor(bot), wanted)
    _audit(c, who, "spend:" + bot, before, _caps_of(usage_limits.own(c, bot)), via)
    return default, allowed


def install(app, store, auth, mutate, settings):
    def window(start, end):
        """(first day, last day) from the query; the default is the last seven days."""
        try:
            last = date.fromisoformat(end) if end else datetime.now(timezone.utc).date()
            first = date.fromisoformat(start) if start else last - timedelta(days=6)
        except ValueError:
            raise Problem("range", "from and to are dates, YYYY-MM-DD", 422)
        if first > last:
            raise Problem("range", "from is after to", 422)
        if (last - first).days >= MAX_DAYS:
            raise Problem("range", f"A range is at most {MAX_DAYS} days", 422)
        return first, last

    def query(c, key, join, slugs, first, last, filters=None):
        """Grouped sums for the runs that finished in the window on these bots."""
        if not slugs:
            return []
        marks = ",".join("?" * len(slugs))
        sql = (f"SELECT {key} AS k, {SUMS} FROM {RUNS} t {join} WHERE t.finished_at IS NOT NULL AND t.started_at >= ? "
               f"AND t.started_at < ? AND t.bot IN ({marks})")
        args = [first.isoformat() + "T00:00:00", (last + timedelta(days=1)).isoformat() + "T00:00:00", *sorted(slugs)]
        for dimension, value in (filters or {}).items():
            sql += f" AND {DIMENSIONS[dimension]} = ?"
            args.append("" if value == "__unknown__" else value)
        return c.execute(sql + " GROUP BY k", args).fetchall()

    def totals(rows):
        keys = ("runs", "input_tokens", "cached_tokens", "output_tokens", "api_usd", "subscription_usd",
                "api_priced", "api_unpriced", "low_priced")
        return figures({k: sum(r[k] or 0 for r in rows) for k in keys})

    def routine_row(c, row, names):
        """A routine's title and bot; runs that came from no routine are one `Other runs` row."""
        found = c.execute("SELECT bot, title FROM schedules WHERE id=?", (row["k"],)).fetchone() if row["k"] else None
        return {"routine": row["k"] or None,
                "title": (found["title"] if found else "") or ("Routine" if row["k"] else "Other runs"),
                "bot": found["bot"] if found else None,
                "name": names.get(found["bot"], found["bot"]) if found else None, **figures(row)}

    def limit_view(c, who, bot, default, allowed):
        """A bot's limits as its row shows them: the effective caps and where they came from, the period's spend
        against them, and whether the caller may change them. A hold by a pool names the pool, and its amounts only
        to someone who sees every bot."""
        found = usage_limits.state(c, bot, default=default)
        if found["blocked_by"] and found["blocked_by"]["scope"] != "bot" and not auth.bot_admin(who):
            found["blocked_by"] = {k: v for k, v in found["blocked_by"].items() if k not in ("spent", "limit")}
        mine = usage_limits.own(c, bot)
        # The capped pools it counts against, nearest group first: they follow its home group.
        pools = {"pools": usage_limits.scopes_of(c, bot, default)}
        return {**found, **{"own_" + key: mine[key] for key in usage_limits.KEYS}, "may_edit": bot in allowed, **pools,
                "spent": usage_limits.spent_now(c, bot=bot, default=default)}

    def pool_views(c, default, led=None):
        """Every group with what it and the whole team are capped at and have spent (the owner and administrators).
        With `led` (a lead who is not an administrator), only those groups, each `may_edit`, and no
        company pool."""
        named, _ = usage_limits.groups(c, default)
        groups = {gid: {"name": g.get("name") or gid, "parent": g.get("parent") or None,
                        **usage_limits.pool_state(c, "group:" + gid, default=default),
                        "spent": usage_limits.spent_now(c, scope="group:" + gid, default=default),
                        **({"may_edit": True} if led is not None else {})}
                  for gid, g in named.items() if led is None or gid in led}
        if led is not None:
            return {"company": None, "groups": groups}
        return {"company": {**usage_limits.pool_state(c, usage_limits.COMPANY, default=default),
                            "spent": usage_limits.spent_now(c, scope=usage_limits.COMPANY, default=default)},
                "groups": groups}

    @app.get("/api/v2/usage/limits")
    def limits(request: Request):
        """The company default, the limits of every bot the caller may see usage for (a lead's: also the bots in the
        groups it leads) and, for the owner and administrators, the team's and each group's combined limits; a lead gets
        the pools of what it leads, listed in `leads`. Each bot and pool row carries `spent`, what it has spent so far in
        each window whether capped or not, which Strategy › Budget adds up the org chart: one read for the whole tree."""
        who = request.state.identity
        auth.domain(who)
        if who.role not in ("owner", "human"):
            raise Problem("forbidden", "Usage is for people", 403)
        with store.read_transaction() as c:         # one snapshot, so each bot's rows are read once (read_cache)
            default = usage_limits.company(c)
            admin = bool(auth.bot_admin(who))
            from . import groups as G
            led = None if admin else G.led_groups(c, who.actor)
            allowed = own_bots(c, auth, who) | ((led_bots(c, who) & {row["slug"] for row in H.bots(c)}) if led else set())
            usage_limits.prime_own(c, allowed)
            pools = pool_views(c, default) if admin else pool_views(c, default, led) if led else None
            return {"default": default, "may_edit_default": admin, "pools": pools, "leads": sorted(led or ()),
                    "bots": {bot: limit_view(c, who, bot, default, allowed) for bot in sorted(allowed)}}

    @app.put("/api/v2/usage/limits")
    def set_default(request: Request, body: M.UsageDefault):
        """The default for bots with no limit of their own (owner and bot administrators)."""
        who = request.state.identity
        auth.domain(who)

        def work(c):
            if not auth.bot_admin(who):
                raise Problem("forbidden", "Only the owner and administrators set the company default", 403)
            stored = usage_limits.company(c)
            # Laid over the stored default (backend/usage.py `amounts`); the subscription switch left out keeps its
            # value too.
            counted = body.count_subscription if "count_subscription" in body.model_fields_set else stored["count_subscription"]
            value = {**{key: stored[key] for key in usage_limits.KEYS}, **amounts(c, body),
                     "count_subscription": bool(counted)}
            c.execute("INSERT INTO registry_metadata(key, value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                      (usage_limits.KEY, json.dumps(value)))
            H.event(c, who.actor, "usage.default", "company", value)
            return {"default": usage_limits.company(c)}
        return mutate(request, body, work)

    def set_pool(request, scope, body):
        """A limit on the combined spend of the whole team or a group; a field set to null removes it, a field left out keeps it."""
        who = request.state.identity
        auth.domain(who)
        return mutate(request, body, lambda c: save_pool(c, auth, who, scope, body))

    @app.put("/api/v2/usage/pools/company")
    def set_company_pool(request: Request, body: M.UsageLimit):
        """A limit on every bot's spend together: over it, no bot takes a new job."""
        return set_pool(request, usage_limits.COMPANY, body)

    @app.put("/api/v2/usage/pools/groups/{group}")
    def set_group_pool(request: Request, group: str, body: M.UsageLimit):
        """A limit on the spend of a group's bots together, nested groups included: over it, none of them takes a new job."""
        return set_pool(request, "group:" + group, body)

    @app.put("/api/v2/usage/limits/{bot}")
    def set_limit(request: Request, bot: str, body: M.UsageLimit):
        """A bot's own 30-minute, daily, weekly and monthly limit in estimated USD; a field set to null follows the company default, a field left out keeps it. A
        person who runs the bot but is not an administrator may not go above the company default. A group's lead may set
        it for every bot in the group."""
        who = request.state.identity
        auth.domain(who)

        def work(c):
            default, allowed = save_limit(c, auth, who, bot, body)
            return {"bot": bot, "limit": limit_view(c, who, bot, default, allowed)}
        return mutate(request, body, work)

    @app.get("/api/v2/usage")
    def usage(request: Request, group: str = "bot", department: str | None = None, bot: str | None = None):
        """Estimated model spend per bot, per day or per routine over `from`..`to` (UTC dates, default the last
        seven days); `bot` is one bot's daily series and top routines."""
        who = request.state.identity
        auth.domain(who)
        if who.role not in ("owner", "human"):
            raise Problem("forbidden", "Usage is for people", 403)
        first, last = window(request.query_params.get("from"), request.query_params.get("to"))
        if group not in GROUPS:
            raise Problem("group", "group must be one of: " + ", ".join(GROUPS), 422)
        filters = {key: request.query_params[key] for key in DIMENSIONS if request.query_params.get(key)}
        with store.read() as c:
            allowed = own_bots(c, auth, who)
            people, configs = views.roster(c), views.entries(c, settings.github_owner)
            depts = {slug: P.team_of(slug, configs, people) or "" for slug in allowed}
            names = {row["slug"]: row["display_name"] or row["slug"] for row in H.bots(c)}
            base = {"from": first.isoformat(), "to": last.isoformat(), "prices_as_of": providers.PRICES_AS_OF}
            if bot:
                if bot not in names or not auth.visible_bot(c, who, bot):
                    raise Problem("not_found", "Bot not found", 404)
                if bot not in allowed:
                    raise Problem("forbidden", "Usage is for a bot's owners and administrators", 403)
                daily = {r["k"]: r for r in query(c, "substr(t.started_at, 1, 10)", "", {bot}, first, last, filters)}
                series = []
                for offset in range((last - first).days + 1):
                    day = (first + timedelta(days=offset)).isoformat()
                    series.append({"day": day, **(figures(daily[day]) if day in daily else EMPTY_DAY)})
                routines = sorted((routine_row(c, r, names) for r in query(c, "coalesce(s.id, '')", ROUTINE_JOIN, {bot}, first, last, filters)),
                                  key=lambda r: (-spend(r), -r["runs"]))
                return {**base, "bot": bot, "name": names[bot], "department": depts.get(bot) or None,
                        "limit": limit_view(c, who, bot, usage_limits.company(c), allowed),
                        "totals": totals(query(c, "'all'", "", {bot}, first, last, filters)), "daily": series,
                        "routines": routines[:TOP_ROUTINES]}
            slugs = {s for s in allowed if not department or depts.get(s) == department}
            total = totals(query(c, "'all'", "", slugs, first, last, filters))
            if group == "bot":
                default = usage_limits.company(c)
                rows = [{"bot": r["k"], "name": names.get(r["k"], r["k"]), "department": depts.get(r["k"]) or None,
                         **figures(r), "limit": limit_view(c, who, r["k"], default, allowed)}
                        for r in query(c, "t.bot", "", slugs, first, last, filters)]
            elif group == "day":
                rows = [{"day": r["k"], **figures(r)} for r in query(c, "substr(t.started_at, 1, 10)", "", slugs, first, last, filters)]
            elif group in DIMENSIONS:
                rows = [{"value": r["k"] or None, "name": usage_label(c, r["k"] or "") if group == "subscription" else r["k"] or "Not recorded / not applicable", **figures(r)}
                        for r in query(c, DIMENSIONS[group], "", slugs, first, last, filters)]
            else:
                rows = [routine_row(c, r, names) for r in query(c, "coalesce(s.id, '')", ROUTINE_JOIN, slugs, first, last, filters)]
            whole = spend(total)
            for row in rows:
                row["share"] = round(spend(row) / whole, 4) if whole else 0.0
            if group == "day":
                rows.sort(key=lambda r: r["day"])
            else:
                rows.sort(key=lambda r: (-spend(r), -(r["input_tokens"] + r["cached_tokens"] + r["output_tokens"]), -r["runs"]))
            dimensions = {key: query(c, expression, "", slugs, first, last)
                          for key, expression in DIMENSIONS.items()}
            return {**base, "group": group, "department": department or None, "totals": total, "rows": rows,
                    "departments": sorted({d for d in depts.values() if d}),
                    "dimension_labels": {"subscription": {r["k"] or "__unknown__": usage_label(c, r["k"] or "")
                        for r in dimensions["subscription"]}},
                    "dimensions": {key: [r["k"] or "__unknown__" for r in values]
                                   for key, values in dimensions.items()}}
