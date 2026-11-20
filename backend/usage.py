"""Usage: what the bots' model runs cost, estimated from their token counts (`GET /api/v2/usage`).

Each finished run carries the tokens its runner counted, the model that ran it and, when the model
has a list price (providers.PRICES), an estimated cost in USD. A run on a ChatGPT or Claude sign-in
is `subscription`: it is billed to the plan, so its cost is what the same tokens would cost through
the API, shown apart and never added to the estimate of what was spent. Days and hours are UTC; `from`
and `to` are whole days or exact UTC times.

Who sees what: the owner and the bot administrators see every bot; anyone else sees the bots they
run or own. A bot's own credentials see nothing here.
"""

import json
import re
from datetime import date, datetime, time, timedelta, timezone

from fastapi import Query, Request

from . import bot_access as A
from . import models as M
from . import providers, queue_purge, usage_limits, views
from .store import H, P, Problem
from .usage_records import RUNS
from .subscription_identity import usage_label
from .api_contract import api_v2

# A subscription run is its computer's subscription; a run with no lease (from before leases) names only its profile.
DIMENSIONS = {
    "harness": "coalesce(t.harness, '')",
    "model": "coalesce(t.model, '')",
    "effort": "coalesce(t.effort, '')",
    "subscription": "CASE WHEN t.billing='subscription' AND coalesce(t.profile, '') != '' "
    "THEN coalesce((SELECT 'subscription:' || json_array(a.runner_id, t.profile) FROM attempts a WHERE a.id=t.id AND a.runner_id IS NOT NULL), "
    "'legacy-profile:' || t.profile) ELSE '' END",
}
# A run's task by its title, so a routine's runs add up under one row; a private task is named only as one, a task the
# reader cannot open now (moved to a bot whose activity they may not read) only as that, and a run with no task (a chat,
# a check) is "". `visible` is auth.task_sql for the reader; its bare columns are the task's.
HIDDEN_TASK = "A task you can't open"


def task_key(visible):
    return (
        f"coalesce((SELECT CASE WHEN tasks.private=1 THEN 'A private task' WHEN NOT ({visible}) THEN "
        f"'{HIDDEN_TASK.replace(chr(39), chr(39) * 2)}' ELSE tasks.title END FROM tasks WHERE tasks.id=t.task_id), '')"
    )


GROUPS = ("bot", "day", "hour", "routine", "task", *DIMENSIONS)
MAX_DAYS = 366
# group=hour lists every hour of its window, so the window is at most a week (168 rows).
MAX_HOUR_DAYS = 7
TOP_ROUTINES = 10
TOP_TASKS = 25
# The opened bot's detail lists its top tasks; the rest are one count and one sum.
DETAIL_TASKS = 10

# A priced run whose estimate may be low: its model's long prompts cost more and the runner did not send every
# request's tokens (providers.estimate). A run recorded before that was marked is judged by its model alone.
LOW = (
    "coalesce(t.est_cost_low, lower(coalesce(t.model, '')) IN ("
    + ",".join(f"'{model}'" for model in sorted(providers.LONG_PROMPT))
    + "))"
)

# Sums over the runs (usage_records.RUNS) shared by every grouping. A run with no token counts (a runtime that reports none,
# or one from before usage) still counts as a run.
SUMS = f"""count(DISTINCT t.id) AS runs,
  sum(coalesce(t.input_tokens, 0)) AS input_tokens,
  sum(coalesce(t.cached_tokens, 0)) AS cached_tokens,
  sum(coalesce(t.cache_write_tokens, 0)) AS cache_write_tokens,
  sum(coalesce(t.output_tokens, 0)) AS output_tokens,
  sum(CASE WHEN t.billing = 'subscription' OR t.usage_reason IS NOT NULL THEN 0 ELSE coalesce(t.est_cost_usd, 0) END) AS api_usd,
  sum(CASE WHEN t.billing = 'subscription' AND t.usage_reason IS NULL THEN coalesce(t.est_cost_usd, 0) ELSE 0 END) AS subscription_usd,
  count(DISTINCT CASE WHEN coalesce(t.billing, 'api') != 'subscription' AND t.est_cost_usd IS NOT NULL AND t.usage_reason IS NULL THEN t.id END) AS api_priced,
  count(DISTINCT CASE WHEN coalesce(t.billing, 'api') != 'subscription' AND (t.usage_reason IS NOT NULL OR (t.est_cost_usd IS NULL
            AND coalesce(t.input_tokens, 0) + coalesce(t.cached_tokens, 0) + coalesce(t.cache_write_tokens, 0)
              + coalesce(t.output_tokens, 0) > 0))
       THEN t.id END) AS api_unpriced,
  count(DISTINCT CASE WHEN t.est_cost_usd IS NOT NULL AND t.usage_reason IS NULL AND {LOW} THEN t.id END) AS low_priced"""
ROUTINE_JOIN = "LEFT JOIN schedule_occurrences o ON o.task_id = t.task_id LEFT JOIN schedules s ON s.id = o.schedule_id"
EMPTY_DAY = {
    "runs": 0,
    "input_tokens": 0,
    "cached_tokens": 0,
    "cache_write_tokens": 0,
    "output_tokens": 0,
    "est_cost_usd": 0.0,
    "subscription_equiv_usd": 0.0,
    "unpriced_runs": 0,
    "low_estimate_runs": 0,
}


def money(value):
    return round(float(value or 0), 6)


def figures(row):
    """The fields every row shares. `est_cost_usd` is null when API runs have no priced reading
    but have unpriced tokens or unreported usage, so the page shows a dash and not a false zero.
    `low_estimate_runs` counts the priced runs whose figure may be low (LOW)."""
    return {
        "runs": row["runs"],
        "input_tokens": row["input_tokens"],
        "cached_tokens": row["cached_tokens"],
        "cache_write_tokens": row["cache_write_tokens"],
        "output_tokens": row["output_tokens"],
        "est_cost_usd": None if not row["api_priced"] and row["api_unpriced"] else money(row["api_usd"]),
        "subscription_equiv_usd": money(row["subscription_usd"]),
        "unpriced_runs": row["api_unpriced"],
        "low_estimate_runs": row["low_priced"],
    }


def tokens(item):
    return item["input_tokens"] + item["cached_tokens"] + item["cache_write_tokens"] + item["output_tokens"]


def raw(item):
    """A shown row back as the sums `figures` reads, so rows can be added up again (the detail's rest of its tasks)."""
    cost = item["est_cost_usd"]
    return {
        **{k: item[k] for k in ("runs", "input_tokens", "cached_tokens", "cache_write_tokens", "output_tokens")},
        "api_usd": cost or 0,
        "subscription_usd": item["subscription_equiv_usd"],
        "api_priced": 0 if cost is None else 1,
        "api_unpriced": item["unpriced_runs"],
        "low_priced": item["low_estimate_runs"],
    }


def spend(item):
    """What ranks a row and sizes its bar: estimated spend plus the API-equivalent of subscription runs."""
    return (item["est_cost_usd"] or 0) + item["subscription_equiv_usd"]


def own_bots(c, auth, who):
    """The bot ids `who` may see usage for: every bot for the owner and administrators, else the
    bots they run or own."""
    slugs = {row["id"] for row in H.bots(c)}
    if auth.bot_admin(who):
        return slugs
    pid = H.actor_id(who.actor)
    mine = set()
    for row in c.execute("SELECT bot, owner_id, bot_owners FROM bot_config"):
        if row["owner_id"] == pid or pid in A.owner_ids(row["bot_owners"]):
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
    for gid in G.led_groups(c, who.actor):  # a lead who left leads nothing
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
                raise Problem(
                    "limit",
                    f"Over {usage_limits.label(c, 'group:' + parent)}: its limit is ${above[key]:g}, "
                    "so a group's limit can be lower, not higher",
                    422,
                )
        parent = (named.get(parent) or {}).get("parent")


def _audit(c, who, obj, before, after, via):
    """A spend change as one audit row (CONTRACT §5, object `spend:<bot>` or `spend:pool:<scope>`)."""
    if before != after:
        from . import audit_log

        audit_log.write(c, who.actor, obj, before, after, via)


def _caps_of(row):
    return (
        {key: row[key] for key in usage_limits.KEYS}
        if row and any(row[key] is not None for key in usage_limits.KEYS)
        else None
    )


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
                raise Problem(
                    "limit", f"The company default is ${default[key]:g}; a bot's limit can be lower, not higher", 403
                )
    stored = usage_limits.own(c, bot)
    before, saved = _caps_of(stored), {**stored, **wanted}
    usage_limits.save_own(c, bot, saved, who.actor)
    H.event(c, who.actor, "usage.limit-set", H.bot_actor(bot), wanted)
    _audit(c, who, "spend:" + bot, before, _caps_of(usage_limits.own(c, bot)), via)
    return default, allowed


def instant(value, end=False):
    """A window bound as a UTC datetime: a date is its day's start, or as the end the next day's start (`to` is the
    last day, included); a datetime is that instant (as the end, excluded)."""
    if isinstance(value, datetime):
        return value
    return datetime.combine(value + timedelta(days=1 if end else 0), time(), timezone.utc)


def stamp(moment):
    """A bound in the stored timestamps' form (hubdb.now), which compare as text. A whole second has no fraction, so
    a day's bounds read as they always have (YYYY-MM-DDT00:00:00)."""
    moment = moment.astimezone(timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%S" + (".%f" if moment.microsecond else ""))


def shown(value):
    """A bound as the answer echoes it: a date as YYYY-MM-DD, a time as ISO-8601 UTC with Z."""
    if not isinstance(value, datetime):
        return value.isoformat()
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="microseconds" if value.microsecond else "seconds")
        .replace("+00:00", "Z")
    )


def bound(text):
    """`from` or `to` as sent: a date (YYYY-MM-DD) or an ISO-8601 time, read as UTC when it names no offset."""
    if len(text) == 10:
        return date.fromisoformat(text)
    # A `+` offset sent unescaped in a query string arrives as a space.
    moment = datetime.fromisoformat(re.sub(r" (\d\d(:?\d\d)?)$", r"+\1", text))
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment.astimezone(timezone.utc)


def hours(first, last, now):
    """Each UTC hour the window touches, up to the current one: 'YYYY-MM-DDTHH', as substr(started_at, 1, 13) reads."""
    at, end = instant(first).replace(minute=0, second=0, microsecond=0), min(instant(last, end=True), now)
    out = []
    while at < end:
        out.append(at.strftime("%Y-%m-%dT%H"))
        at += timedelta(hours=1)
    return out


def query(c, key, join, slugs, first, last, filters=None):
    """Grouped sums for the runs that started in the window on these bots and have finished. `first` and `last` are
    dates (whole days, `last` included) or UTC datetimes (`last` excluded). Module level so the demo's billing sample
    (backend/demo_billing.py) sums the same runs the usage page does."""
    if not slugs:
        return []
    where, args = runs_in(slugs, first, last, filters)
    return c.execute(f"SELECT {key} AS k, {SUMS} FROM {RUNS} t {join} WHERE {where} GROUP BY k", args).fetchall()


def runs_in(slugs, first, last, filters=None):
    """The WHERE clause over RUNS (as `t`) and its arguments: the finished runs that started in the window on these bots,
    with these dimension filters."""
    marks = ",".join("?" * len(slugs))
    sql = f"t.finished_at IS NOT NULL AND t.started_at >= ? AND t.started_at < ? AND t.bot IN ({marks})"
    args = [stamp(instant(first)), stamp(instant(last, end=True)), *sorted(slugs)]
    for dimension, value in (filters or {}).items():
        sql += f" AND {DIMENSIONS[dimension]} = ?"
        args.append("" if value == "__unknown__" else value)
    return sql, args


def dimension_values(c, slugs, first, last):
    """{dimension: its values among the window's runs on these bots}, each list in the order `query` groups them (text
    by its bytes, which is Python's order for str), from one read: the filter menus need the values, not their sums."""
    if not slugs:
        return {key: [] for key in DIMENSIONS}
    where, args = runs_in(slugs, first, last)
    columns = ", ".join(f"json_group_array(DISTINCT {expression})" for expression in DIMENSIONS.values())
    row = c.execute(f"SELECT {columns} FROM {RUNS} t WHERE {where}", args).fetchone()
    return {key: sorted(json.loads(values)) for key, values in zip(DIMENSIONS, row, strict=True)}


def install(app, store, auth, mutate, settings):
    def window(start, end, group):
        """(first, last) from the query: dates for whole UTC days, or UTC datetimes for an exact window from `from`
        up to (not including) `to`; one of each works too. The default is the last seven days."""
        try:
            last = bound(end) if end else datetime.now(timezone.utc).date()
            if start:
                first = bound(start)
            else:
                first = (last.date() if isinstance(last, datetime) else last) - timedelta(days=6)
        except ValueError:
            raise Problem(
                "range", "from and to are dates (YYYY-MM-DD) or UTC times (YYYY-MM-DDTHH:MM:SSZ)", 422
            ) from None
        if isinstance(first, datetime) or isinstance(last, datetime):
            if instant(first) >= instant(last, end=True):
                raise Problem("range", "from must be before to", 422)
        elif first > last:
            raise Problem("range", "from is after to", 422)
        span = instant(last, end=True) - instant(first)
        if span > timedelta(days=MAX_DAYS):
            raise Problem("range", f"A range is at most {MAX_DAYS} days", 422)
        if group == "hour" and span > timedelta(days=MAX_HOUR_DAYS):
            raise Problem(
                "range",
                f"group=hour covers at most {MAX_HOUR_DAYS} days ({MAX_HOUR_DAYS * 24} hours): narrow from..to",
                422,
            )
        return first, last

    def days(first, last):
        """Each UTC day the window touches, first to last, for a bot's daily series."""
        start = instant(first).date()
        end = (instant(last, end=True) - timedelta(microseconds=1)).date()
        return [(start + timedelta(days=offset)).isoformat() for offset in range((end - start).days + 1)]

    def totals(rows):
        keys = (
            "runs",
            "input_tokens",
            "cached_tokens",
            "cache_write_tokens",
            "output_tokens",
            "api_usd",
            "subscription_usd",
            "api_priced",
            "api_unpriced",
            "low_priced",
        )
        return figures({k: sum(r[k] or 0 for r in rows) for k in keys})

    def routine_row(c, row, names):
        """A routine's title and bot; runs that came from no routine are one `Other runs` row."""
        found = c.execute("SELECT bot, title FROM schedules WHERE id=?", (row["k"],)).fetchone() if row["k"] else None
        return {
            "routine": row["k"] or None,
            "title": (found["title"] if found else "") or ("Routine" if row["k"] else "Other runs"),
            "bot": found["bot"] if found else None,
            "name": names.get(found["bot"], found["bot"]) if found else None,
            **figures(row),
        }

    def limit_view(c, who, bot, default, allowed):
        """A bot's limits as its row shows them: the effective caps and where they came from, the period's spend
        against them, and whether the caller may change them. A hold by a pool names the pool, and its amounts only
        to someone who sees every bot."""
        spent = usage_limits.spent_now(c, bot=bot, default=default)  # first: one read of every window's spend
        found = usage_limits.state(c, bot, default=default)
        if found["blocked_by"] and found["blocked_by"]["scope"] != "bot" and not auth.bot_admin(who):
            found["blocked_by"] = {k: v for k, v in found["blocked_by"].items() if k not in ("spent", "limit")}
        mine = usage_limits.own(c, bot)
        # The capped pools it counts against, nearest group first: they follow its home group.
        pools = {"pools": usage_limits.scopes_of(c, bot, default)}
        return {
            **found,
            **{"own_" + key: mine[key] for key in usage_limits.KEYS},
            "may_edit": bot in allowed,
            **pools,
            "spent": spent,
        }

    def pool_views(c, default, led=None):
        """Every group with what it and the whole team are capped at and have spent (the owner and administrators).
        With `led` (a lead who is not an administrator), only those groups, each `may_edit`, and no
        company pool."""
        named, _ = usage_limits.groups(c, default)
        groups = {
            gid: {
                "name": g.get("name") or gid,
                "parent": g.get("parent") or None,
                **usage_limits.pool_state(c, "group:" + gid, default=default),
                "spent": usage_limits.spent_now(c, scope="group:" + gid, default=default),
                **({"may_edit": True} if led is not None else {}),
            }
            for gid, g in named.items()
            if led is None or gid in led
        }
        if led is not None:
            return {"company": None, "groups": groups}
        return {
            "company": {
                **usage_limits.pool_state(c, usage_limits.COMPANY, default=default),
                "spent": usage_limits.spent_now(c, scope=usage_limits.COMPANY, default=default),
            },
            "groups": groups,
        }

    def usage_bots(c, auth, who):
        """The bots whose spend the caller sees: their own, plus a group lead's led bots, as /usage/limits lists them."""
        allowed = own_bots(c, auth, who)
        if auth.bot_admin(who):
            return allowed
        return allowed | (led_bots(c, who) & {row["id"] for row in H.bots(c)})

    @app.get(
        "/api/v2/usage/limits",
        **api_v2(
            "Usage",
            "getUsageLimits",
            "The team default limit and each bot's hourly, daily, weekly and monthly limits with this period's spend, for the bots the caller may see usage for; for the owner and bot administrators also the team's and each group's combined limits (a lead who is not an administrator gets the groups it leads and those below)",
            "UsageLimits",
        ),
    )
    def limits(request: Request):
        """The company default, the limits of every bot the caller may see usage for (a lead's: also the bots in the
        groups it leads) and, for the owner and administrators, the team's and each group's combined limits; a lead gets
        the pools of what it leads, listed in `leads`. Each bot and pool row carries `spent`, what it has spent so far in
        each window whether capped or not, which Strategy › Budget adds up the org chart: one read for the whole tree."""
        who = request.state.identity
        auth.domain(who)
        if who.role not in ("owner", "human"):
            raise Problem("forbidden", "Usage is for people", 403)
        with store.read_transaction() as c:  # one snapshot, so each bot's rows are read once (read_cache)
            default = usage_limits.company(c)
            admin = bool(auth.bot_admin(who))
            from . import groups as G

            led = None if admin else G.led_groups(c, who.actor)
            allowed = own_bots(c, auth, who) | ((led_bots(c, who) & {row["id"] for row in H.bots(c)}) if led else set())
            pools = pool_views(c, default) if admin else pool_views(c, default, led) if led else None
            return {
                "default": default,
                "may_edit_default": admin,
                "pools": pools,
                "leads": sorted(led or ()),
                "bots": {bot: limit_view(c, who, bot, default, allowed) for bot in sorted(allowed)},
            }

    @app.put(
        "/api/v2/usage/limits",
        **api_v2(
            "Usage",
            "setUsageDefault",
            "The default limit for bots with none of their own, in estimated USD (empty is no limit), and whether subscription runs count toward every limit (owner and bot administrators)",
            "UsageDefaultView",
        ),
    )
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
            counted = (
                body.count_subscription
                if "count_subscription" in body.model_fields_set
                else stored["count_subscription"]
            )
            value = {
                **{key: stored[key] for key in usage_limits.KEYS},
                **amounts(c, body),
                "count_subscription": bool(counted),
            }
            usage_limits.save_default(c, value, value["count_subscription"], who.actor)
            H.event(c, who.actor, "usage.default", "company", value)
            return {"default": usage_limits.company(c)}

        return mutate(request, body, work)

    def set_pool(request, scope, body):
        """A limit on the combined spend of the whole team or a group; a field set to null removes it, a field left out keeps it."""
        who = request.state.identity
        auth.domain(who)
        return mutate(request, body, lambda c: save_pool(c, auth, who, scope, body))

    @app.put(
        "/api/v2/usage/pools/company",
        **api_v2(
            "Usage",
            "setTeamUsageLimit",
            "A limit on every bot's estimated spend together, per hour, day, week and month; null removes a field, one left out stays. Over it, no bot takes a new job (owner and bot administrators)",
            "UsagePoolLimit",
        ),
    )
    def set_company_pool(request: Request, body: M.UsageLimit):
        """A limit on every bot's spend together: over it, no bot takes a new job."""
        return set_pool(request, usage_limits.COMPANY, body)

    @app.put(
        "/api/v2/usage/pools/groups/{group}",
        **api_v2(
            "Usage",
            "setGroupUsageLimit",
            "A limit on the estimated spend of a group's bots together, nested groups included; null removes a field, one left out stays. Over it, none of them takes a new job (owner and bot administrators; also the lead of the group or one above it, within the parent group's limit)",
            "UsagePoolLimit",
        ),
    )
    def set_group_pool(request: Request, group: str, body: M.UsageLimit):
        """A limit on the spend of a group's bots together, nested groups included: over it, none of them takes a new job."""
        return set_pool(request, "group:" + group, body)

    @app.post(
        "/api/v2/usage/queues/purge",
        **api_v2(
            "Usage",
            "purgeAutomaticQueue",
            "Cancel queued automatic wakes and waits for the whole team (scope company) or one group and the groups under it (scope group:<id>). Tasks stay on their current step. Messages from people, and Tico's notices of a person's own act, stay queued. A run that has already started finishes (owner and bot administrators)",
            "QueuePurgeResult",
        ),
    )
    def purge_queue(request: Request, body: M.QueuePurge):
        """Cancel queued automatic wakes and waits for the team or one group. Tasks stay. Messages from people stay
        queued. A run that has already started finishes."""
        who = request.state.identity
        auth.domain(who)

        def work(c):
            if not auth.bot_admin(who):
                raise Problem("forbidden", "Only the owner and administrators clear the automatic queue", 403)
            scope = (body.scope or usage_limits.COMPANY).strip()
            bots = queue_purge.bots_for(c, scope)
            result = queue_purge.apply(c, bots)
            H.event(c, who.actor, "usage.queue-purged", scope, result)
            return {"scope": scope, **result}

        return mutate(request, body, work)

    @app.put(
        "/api/v2/usage/limits/{bot}",
        **api_v2(
            "Usage",
            "setBotUsageLimit",
            "A bot's own 30-minute, daily, weekly and monthly limit in estimated USD; empty follows the team default. Owner and administrators, the person who runs the bot within the team default, and a group lead for the bots in the group and those below. A bot over a limit takes no new job until the period turns over or the limit is raised",
            "BotUsageLimit",
        ),
    )
    def set_limit(request: Request, bot: str, body: M.UsageLimit):
        """A bot's own 30-minute, daily, weekly and monthly limit in estimated USD; a field set to null follows the company default, a field left out keeps it. A
        person who runs the bot but is not an administrator may not go above the company default. A group's lead may set
        it for every bot in the group."""
        who = request.state.identity
        auth.domain(who)

        def work(c):
            ref = (H.resolve_bot(c, bot) or {}).get("id", bot)
            default, allowed = save_limit(c, auth, who, ref, body)
            return {"bot": ref, "limit": limit_view(c, who, ref, default, allowed)}

        return mutate(request, body, work)

    @app.get(
        "/api/v2/usage",
        **api_v2(
            "Usage",
            "getUsage",
            "Estimated spend over from..to (UTC dates, or UTC times for an exact window; default the last 7 days), grouped by bot (default), day, hour (at most 7 days), routine, task (the top 25), harness, model, effort or subscription, filterable by those run dimensions, department (__unknown__ selects missing values) and bots (slugs, comma separated). The owner and bot administrators see every bot; anyone else sees the bots they run or own. ?bot=<bot id or slug> is that bot's daily series, top routines, top 10 tasks (the rest summed) and models instead",
            "Usage",
            parameters={
                "from": "Start of the window: a UTC day (YYYY-MM-DD, from its start) or an ISO-8601 time "
                "(2026-11-18T17:00:00Z, included). Default: 6 days before to",
                "to": "End of the window: a UTC day (YYYY-MM-DD, that whole day included) or an ISO-8601 time "
                "(2026-11-18T20:00:00Z, excluded). Default: today. A time with no offset is UTC. At most 366 days, "
                "7 for group=hour",
                "group": "How rows are grouped: bot (default), day, hour (each UTC hour of the window up to now, empty "
                "ones as zeros), routine, task, harness, model, effort or subscription",
            },
        ),
    )
    def usage(
        request: Request,
        group: str = "bot",
        department: str | None = None,
        bot: str | None = None,
        start: str | None = Query(None, alias="from"),
        end: str | None = Query(None, alias="to"),
    ):
        """Estimated model spend per bot, per day, per hour, per routine, per task (its top TOP_TASKS by spend) or per
        dimension over `from`..`to` (UTC dates, or UTC times for an exact window; default the last seven days); `bots`
        (slugs, comma separated) narrows it to those bots; `bot` is one bot's daily series and top routines."""
        who = request.state.identity
        auth.domain(who)
        if who.role not in ("owner", "human"):
            raise Problem("forbidden", "Usage is for people", 403)
        if group not in GROUPS:
            raise Problem("group", "group must be one of: " + ", ".join(GROUPS), 422)
        first, last = window(start, end, None if bot else group)
        filters = {key: request.query_params[key] for key in DIMENSIONS if request.query_params.get(key)}
        # One snapshot: the budget nodes, roster and spend each bot's limit reads are read once for every row.
        with store.read_transaction() as c:
            allowed = usage_bots(c, auth, who)
            people, configs = views.roster(c), views.entries(c, settings.github_owner)
            depts = {slug: P.team_of(slug, configs, people) or "" for slug in allowed}
            names = {row["id"]: row["display_name"] or row["slug"] for row in H.bots(c)}
            base = {"from": shown(first), "to": shown(last), "prices_as_of": providers.PRICES_AS_OF}
            if bot:
                bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
                if bot not in names or not auth.visible_bot(c, who, bot):
                    raise Problem("not_found", "Bot not found", 404)
                if bot not in allowed:
                    raise Problem("forbidden", "Usage is for a bot's owners and administrators", 403)
                daily = {r["k"]: r for r in query(c, "substr(t.started_at, 1, 10)", "", {bot}, first, last, filters)}
                series = [
                    {"day": day, **(figures(daily[day]) if day in daily else EMPTY_DAY)} for day in days(first, last)
                ]
                routines = sorted(
                    (
                        routine_row(c, r, names)
                        for r in query(c, "coalesce(s.id, '')", ROUTINE_JOIN, {bot}, first, last, filters)
                    ),
                    key=lambda r: (-spend(r), -r["runs"]),
                )
                total = totals(query(c, "'all'", "", {bot}, first, last, filters))
                whole = spend(total)

                def ranked(rows):
                    for row in rows:
                        row["share"] = round(spend(row) / whole, 4) if whole else 0.0
                    return sorted(rows, key=lambda r: (-spend(r), -tokens(r), -r["runs"], r["name"]))

                tasks = ranked(
                    [
                        {"value": r["k"] or None, "name": r["k"] or "Chats and other runs", **figures(r)}
                        for r in query(c, task_key(auth.task_sql(c, who)), "", {bot}, first, last, filters)
                    ]
                )
                # Runs with no task keep their own row: the rest line counts tasks only.
                named = [t for t in tasks if t["value"]]
                rest = named[DETAIL_TASKS:]
                tasks = [t for t in tasks if t not in rest]
                rest_figures = totals([raw(r) for r in rest]) if rest else dict(EMPTY_DAY)
                models = ranked(
                    [
                        {"value": r["k"] or None, "name": r["k"] or "Not recorded / not applicable", **figures(r)}
                        for r in query(c, DIMENSIONS["model"], "", {bot}, first, last, filters)
                    ]
                )
                return {
                    **base,
                    "bot": bot,
                    "name": names[bot],
                    "department": depts.get(bot) or None,
                    "limit": limit_view(c, who, bot, usage_limits.company(c), allowed),
                    "totals": total,
                    "daily": series,
                    "routines": routines[:TOP_ROUTINES],
                    "tasks": tasks,
                    "tasks_more": {"count": len(rest), **rest_figures},
                    "models": models,
                }
            only = {
                (H.resolve_bot(c, s) or {}).get("id", s)
                for s in (s.strip() for s in (request.query_params.get("bots") or "").split(","))
                if s
            }
            slugs = {s for s in allowed if (not department or depts.get(s) == department) and (not only or s in only)}
            total = totals(query(c, "'all'", "", slugs, first, last, filters))
            if group == "bot":
                default = usage_limits.company(c)
                rows = [
                    {
                        "bot": r["k"],
                        "name": names.get(r["k"], r["k"]),
                        "department": depts.get(r["k"]) or None,
                        **figures(r),
                        "limit": limit_view(c, who, r["k"], default, allowed),
                    }
                    for r in query(c, "t.bot", "", slugs, first, last, filters)
                ]
            elif group == "day":
                rows = [
                    {"day": r["k"], **figures(r)}
                    for r in query(c, "substr(t.started_at, 1, 10)", "", slugs, first, last, filters)
                ]
            elif group == "hour":
                hourly = {r["k"]: r for r in query(c, "substr(t.started_at, 1, 13)", "", slugs, first, last, filters)}
                rows = [
                    {"hour": hour + ":00:00Z", **(figures(hourly[hour]) if hour in hourly else EMPTY_DAY)}
                    for hour in sorted(set(hours(first, last, datetime.now(timezone.utc))) | set(hourly))
                ]
            elif group == "task":
                rows = [
                    {"value": r["k"] or None, "name": r["k"] or "Chats and other runs", **figures(r)}
                    for r in query(c, task_key(auth.task_sql(c, who)), "", slugs, first, last, filters)
                ]
            elif group in DIMENSIONS:
                rows = [
                    {
                        "value": r["k"] or None,
                        "name": usage_label(c, r["k"] or "")
                        if group == "subscription"
                        else r["k"] or "Not recorded / not applicable",
                        **figures(r),
                    }
                    for r in query(c, DIMENSIONS[group], "", slugs, first, last, filters)
                ]
            else:
                rows = [
                    routine_row(c, r, names)
                    for r in query(c, "coalesce(s.id, '')", ROUTINE_JOIN, slugs, first, last, filters)
                ]
            whole = spend(total)
            for row in rows:
                row["share"] = round(spend(row) / whole, 4) if whole else 0.0
            if group in ("day", "hour"):
                rows.sort(key=lambda r: r[group])
            else:
                rows.sort(
                    key=lambda r: (
                        -spend(r),
                        -(r["input_tokens"] + r["cached_tokens"] + r["cache_write_tokens"] + r["output_tokens"]),
                        -r["runs"],
                    )
                )
            if group == "task":
                rows = rows[:TOP_TASKS]
            dimensions = dimension_values(c, slugs, first, last)
            return {
                **base,
                "group": group,
                "department": department or None,
                "totals": total,
                "rows": rows,
                "departments": sorted({d for d in depts.values() if d}),
                "dimension_labels": {
                    "subscription": {k or "__unknown__": usage_label(c, k) for k in dimensions["subscription"]}
                },
                "dimensions": {key: [k or "__unknown__" for k in values] for key, values in dimensions.items()},
            }
