"""Spend limits: an hourly, daily, weekly and monthly cap in estimated USD, for a bot, a group and the whole team
(docs/usage.md).

A bot's own limit wins; a bot with none follows the company default; the default is no limit. A pool is a cap on the
combined spend of several bots: `company` covers every bot, `group:<id>` the bots in that group and in the groups nested
under it. A bot takes no new job while any limit it is under is met, its own or a pool's. What counts against a limit is
the estimate of the runs the bots have finished in the period (backend/usage.py prices each run as it completes). Runs
on a ChatGPT or Claude sign-in are flat cost, so their API-equivalent counts only when the company opts in.

  80%   one notice to the bot's operator (the company owner when it has none, and always for a pool), once per period
        and limit
  100%  the bot takes no new job until the period turns over or the limit is raised: the job stays queued and the bot
        reads "Paused: over its hourly limit". A run already going finishes.

The hour is the UTC clock hour, the day the UTC day, the week starts Monday 00:00 UTC and the month is the UTC calendar
month.
"""

import json
from datetime import datetime, timedelta, timezone

from .store import H

KEY = "usage_limits"                       # the company default, in registry_metadata
WARN = 80
MAX_USD = 10_000_000
PERIODS = (("hourly", "hour"), ("daily", "day"), ("weekly", "week"), ("monthly", "month"))
KEYS = tuple(name + "_usd" for name, _ in PERIODS)
COMPANY = "company"                        # the pool every bot is in

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS usage_pools (scope TEXT PRIMARY KEY, hourly_usd REAL, daily_usd REAL, "
    "weekly_usd REAL, monthly_usd REAL, updated TEXT, updated_by TEXT)",
)


def ensure(c):
    """The hourly and weekly columns on a bot's own limits and the pools table; safe to run on any database."""
    from .hubdb import add_column
    for column in ("hourly_usd", "weekly_usd"):
        add_column(c, "usage_limits", column, "REAL")
    for statement in SCHEMA:
        c.execute(statement)


class Default(dict):
    """The company default ({hourly_usd, ..., count_subscription}), carrying what one pass over many bots reads only once:
    the pools, the groups and each period's spend per bot. Only the dict goes out in a response."""

    def __init__(self, value):
        super().__init__(value)
        self.memo = {}


def company(c):
    """The default for bots with no limit of their own: {hourly_usd, daily_usd, weekly_usd, monthly_usd, count_subscription}."""
    row = c.execute("SELECT value_json FROM registry_metadata WHERE key=?", (KEY,)).fetchone()
    try:
        stored = json.loads(row[0]) if row else {}
    except ValueError:
        stored = {}
    stored = stored if isinstance(stored, dict) else {}
    return Default({**{key: _amount(stored.get(key)) for key in KEYS}, "count_subscription": stored.get("count_subscription") is True})


def _amount(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, 6) if 0 < number <= MAX_USD else None


def _caps(row):
    return {key: (row[key] if row and key in row.keys() else None) for key in KEYS}


def own(c, bot):
    return _caps(c.execute("SELECT * FROM usage_limits WHERE bot=?", (bot,)).fetchone())


def effective(c, bot, default=None):
    """Each period's cap in USD or None, and where each came from: `bot`, `company` or None."""
    default = default if default is not None else company(c)
    mine = own(c, bot)
    out, source = {}, {}
    for name, _ in PERIODS:
        key = name + "_usd"
        out[key] = mine[key] if mine[key] is not None else default[key]
        source[name] = "bot" if mine[key] is not None else "company" if default[key] is not None else None
    return out, source


def only_lowers(c, bot, body):
    """Whether saving `body` as the limits of `bot` (empty: the company default) leaves no cap higher than it is now.
    No cap is the highest there is, and a body that is not amounts counts as raising."""
    body = body if isinstance(body, dict) else {}
    if bot:
        now, _ = effective(c, bot)
        default = company(c)
        new = {key: body.get(key) if body.get(key) is not None else default[key] for key in now}
    else:
        now = company(c)
        new = {key: body.get(key) for key in KEYS}
        if now["count_subscription"] and not body.get("count_subscription"):
            return False               # counting less spend is a higher limit
    for key in KEYS:
        value = _amount(new[key]) if new[key] is not None else None
        if new[key] is not None and value is None:
            return False
        if now[key] is not None and (value is None or value > now[key]):
            return False
    return True


# ------------------------------------------------------------------------------------------------ pools
def pools(c, default=None):
    """{scope: caps} for every pool with a cap set: `company` and `group:<id>`."""
    memo = default.memo if isinstance(default, Default) else {}
    if "pools" not in memo:
        found = {}
        for row in c.execute("SELECT * FROM usage_pools"):
            caps = _caps(row)
            if any(caps.values()):
                found[row["scope"]] = caps
        memo["pools"] = found
    return memo["pools"]


def pool(c, scope):
    return _caps(c.execute("SELECT * FROM usage_pools WHERE scope=?", (scope,)).fetchone())


def save_pool(c, scope, caps, actor):
    if not any(caps.values()):
        c.execute("DELETE FROM usage_pools WHERE scope=?", (scope,))
        return
    c.execute("INSERT INTO usage_pools(scope, hourly_usd, daily_usd, weekly_usd, monthly_usd, updated, updated_by) "
              "VALUES(?,?,?,?,?,?,?) ON CONFLICT(scope) DO UPDATE SET hourly_usd=excluded.hourly_usd, "
              "daily_usd=excluded.daily_usd, weekly_usd=excluded.weekly_usd, monthly_usd=excluded.monthly_usd, "
              "updated=excluded.updated, updated_by=excluded.updated_by",
              (scope, *(caps[key] for key in KEYS), H.now(), actor))


def groups(c, default=None):
    """The org groups ({id: {name, parent, ...}}) and {bot: [its group, that group's parent, ...]}."""
    memo = default.memo if isinstance(default, Default) else {}
    if "groups" not in memo:
        from . import people as P
        from . import views
        roster = views.roster(c)
        chains = {row["bot"]: P.group_chain(row["team"], roster) if row["team"] else []
                  for row in c.execute("SELECT bot, team FROM bot_config")}
        memo["groups"] = ((roster or {}).get("org_groups") or {}, chains)
    return memo["groups"]


def scopes_of(c, bot, default=None):
    """The pools `bot` is in that have a cap: its groups from the nearest up, then the company."""
    found = pools(c, default)
    if not found:
        return []
    _, chains = groups(c, default)
    return [scope for scope in ["group:" + g for g in chains.get(bot, [])] + [COMPANY] if scope in found]


def members(c, scope, default=None):
    """The bots a pool covers."""
    _, chains = groups(c, default)
    if scope == COMPANY:
        return set(chains)
    gid = scope.split(":", 1)[1]
    return {bot for bot, chain in chains.items() if gid in chain}


def label(c, scope, default=None):
    """How a pool is named to a person: "the team" or "the <name> group"."""
    if scope == COMPANY:
        return "the team"
    named, _ = groups(c, default)
    gid = scope.split(":", 1)[1]
    return f"the {(named.get(gid) or {}).get('name') or gid} group"


# ------------------------------------------------------------------------------------------------ spend
def bounds(at=None):
    """The current period's key and its [from, to) timestamps: {hourly: (key, from, to), daily: ..., weekly: ..., monthly: ...}."""
    at = at or datetime.now(timezone.utc)
    hour = at.replace(minute=0, second=0, microsecond=0)
    day = at.strftime("%Y-%m-%d")
    monday = (at - timedelta(days=at.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    year, week, _ = at.isocalendar()
    first = at.replace(day=1)
    following = first.replace(year=first.year + (first.month == 12), month=first.month % 12 + 1)
    return {"hourly": (hour.strftime("%Y-%m-%dT%H"), hour.strftime("%Y-%m-%dT%H:00:00"),
                       (hour + timedelta(hours=1)).strftime("%Y-%m-%dT%H:00:00")),
            "daily": (day, day + "T00:00:00", (at + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00")),
            "weekly": (f"{year}-W{week:02d}", monday.strftime("%Y-%m-%dT00:00:00"),
                       (monday + timedelta(days=7)).strftime("%Y-%m-%dT00:00:00")),
            "monthly": (at.strftime("%Y-%m"), first.strftime("%Y-%m-%dT00:00:00"), following.strftime("%Y-%m-%dT00:00:00"))}


def by_bot(c, start, end, subscription, default=None):
    """{bot: estimate} for the runs that started in [start, end), API-billed, and the subscription runs'
    API-equivalent too when the company counts them. One query serves every bot and pool in a pass."""
    memo = default.memo if isinstance(default, Default) else {}
    key = ("spent", start, end, bool(subscription))
    if key not in memo:
        from .usage_records import RUNS
        memo[key] = {row[0]: float(row[1] or 0) for row in c.execute(
            f"SELECT bot, coalesce(sum(est_cost_usd), 0) FROM {RUNS} WHERE started >= ? AND started < ? "
            "AND (? OR coalesce(billing, 'api') != 'subscription') GROUP BY bot", (start, end, 1 if subscription else 0))}
    return memo[key]


def spent(c, bot, start, end, subscription, default=None):
    """The estimate for the bot's runs that started in [start, end)."""
    return by_bot(c, start, end, subscription, default).get(bot, 0.0)


def pool_spent(c, scope, start, end, subscription, default=None):
    amounts = by_bot(c, start, end, subscription, default)
    if scope == COMPANY:
        return sum(amounts.values())
    return sum(amounts.get(bot, 0.0) for bot in members(c, scope, default))


def pool_state(c, scope, at=None, default=None):
    """A pool's caps, what its bots have spent in each period with a cap, and `blocked` (the period) when one is met."""
    default = default if default is not None else company(c)
    caps = pools(c, default).get(scope) or pool(c, scope)
    when = bounds(at)
    result = {**caps, "percent": 0, "blocked": None}
    for name, label_ in PERIODS:
        result[label_ + "_spent"] = None
        cap = caps[name + "_usd"]
        if not cap:
            continue
        _, start, end = when[name]
        amount = pool_spent(c, scope, start, end, default["count_subscription"], default)
        result[label_ + "_spent"] = round(amount, 6)
        result["percent"] = max(result["percent"], int(100 * amount / cap))
        if amount >= cap and not result["blocked"]:
            result["blocked"] = name
    return result


def state(c, bot, at=None, default=None):
    """Where the bot stands: its own limits and their source, what it has spent, the highest percentage of one of them
    reached, and `blocked` (the period) when a limit is met, its own or a pool's; `blocked_by` then says whose."""
    default = default if default is not None else company(c)
    limits, source = effective(c, bot, default)
    when = bounds(at)
    result = {**limits, "source": source, **{label_ + "_spent": None for _, label_ in PERIODS},
              "percent": 0, "blocked": None, "blocked_by": None}
    for name, label_ in PERIODS:
        cap = limits[name + "_usd"]
        if not cap:
            continue
        _, start, end = when[name]
        amount = spent(c, bot, start, end, default["count_subscription"], default)
        result[label_ + "_spent"] = round(amount, 6)
        result["percent"] = max(result["percent"], int(100 * amount / cap))
        if amount >= cap and not result["blocked"]:
            result["blocked"] = name
            result["blocked_by"] = {"scope": "bot", "period": name, "spent": round(amount, 6), "limit": cap}
    if not result["blocked"]:
        for scope in scopes_of(c, bot, default):
            found = pool_state(c, scope, at, default)
            if found["blocked"]:
                period = found["blocked"]
                label_ = dict(PERIODS)[period]
                result["blocked"] = period
                result["blocked_by"] = {"scope": scope, "name": label(c, scope, default), "period": period,
                                        "spent": found[label_ + "_spent"], "limit": found[period + "_usd"]}
                break
    return result


def blocked(c, bot, at=None, default=None):
    """The period (`hourly`, `daily`, `weekly` or `monthly`) when the bot may take no new job, else None."""
    return state(c, bot, at, default)["blocked"]


def why(met):
    """The words on a paused bot, from `over`'s answer (or a bare period, for the bot's own limit)."""
    met = met if isinstance(met, dict) else {"period": met, "scope": "bot"}
    if met.get("scope", "bot") == "bot":
        return f"Paused: over its {met['period']} limit"
    return f"Paused: {met['name']} is over its {met['period']} limit"


def over(c, bot, at=None, default=None):
    """The limit a bot is held by, for the pages and the hub tools: {period, spent, limit, until, scope, name}, else
    None. `scope` is `bot`, `company` or `group:<id>`; `name` names a pool; `until` is when the period turns over (UTC)."""
    found = state(c, bot, at, default)
    met = found["blocked_by"]
    if not met:
        return None
    return {**met, "until": bounds(at)[met["period"]][2] + "Z"}


def resets(period, until):
    """When a period turns over, as a person reads it."""
    if period == "hourly":
        return f"{until:%H}:00 UTC"
    if period == "daily":
        return "00:00 UTC"
    return f"{until:%b} {until.day} 00:00 UTC"


def warning(c, actor, at=None):
    """The words a sender gets when its message or task goes to a bot over a limit, else None. It is a warning
    beside the saved message or task, not a refusal: the work stays queued and runs once the limit allows."""
    if not str(actor or "").startswith("bot:"):
        return None
    bot = actor[4:]
    met = over(c, bot, at)
    if not met:
        return None
    name = (H.bot(c, bot) or {}).get("display_name") or bot
    reset = resets(met["period"], datetime.fromisoformat(met["until"][:-1]))
    if met["scope"] == "bot":
        return (f"{name} is over its {met['period']} limit (${met['spent']:,.2f} of ${met['limit']:,.2f}); "
                f"it starts again at {reset} or when a person raises the limit.")
    return (f"{name} is waiting: {met['name']} is over its {met['period']} limit; "
            f"it starts again at {reset} or when a person raises the limit.")


def overlay(c, row, default=None):
    """A bot's status row as the pages should read it: paused, why, and the limit it is held by, while a limit is met
    (unless it is running). A pool's amounts add up bots the reader may not see, so a pool's hold carries none."""
    if not row or row.get("state") in ("running", "quarantined"):
        return row
    met = over(c, row["bot"], default=default)
    if met and met["scope"] != "bot":
        met = {k: v for k, v in met.items() if k not in ("spent", "limit")}
    return {**row, "state": "paused", "focus": why(met), "limit": met} if met else row


def after_run(c, bot, owner_email="", at=None):
    """Called as a run completes: the one notice when a limit reaches 80% and another at 100%, for the bot's own limits
    and every pool it is in. A notice is sent once per bot or pool, period and limit, so raising a limit rearms it; a
    run that jumps past both sends only the higher."""
    default = company(c)
    limits, _ = effective(c, bot, default)
    checks = [(bot, limits, lambda amount_of: amount_of(bot))]
    for scope in scopes_of(c, bot, default):
        checks.append(("@" + scope, pools(c, default)[scope], lambda amount_of, s=scope: sum(amount_of(b) for b in members(c, s, default))))
    when = bounds(at)
    for who, caps, total in checks:
        for name, _ in PERIODS:
            cap = caps[name + "_usd"]
            if not cap:
                continue
            key, start, end = when[name]
            amounts = by_bot(c, start, end, default["count_subscription"], default)
            amount = total(lambda b: amounts.get(b, 0.0))
            level = 100 if amount >= cap else WARN if amount >= cap * WARN / 100 else 0
            if not level:
                continue
            period = f"{key}:{cap:g}"
            if c.execute("SELECT 1 FROM usage_alerts WHERE bot=? AND period=? AND level>=?", (who, period, level)).fetchone():
                continue
            for reached in (WARN, 100):
                if reached <= level:
                    c.execute("INSERT OR IGNORE INTO usage_alerts(bot, period, level, sent) VALUES(?,?,?,?)",
                              (who, period, reached, H.now()))
            if who == bot:
                notify(c, bot, level, name, amount, cap, owner_email)
            else:
                notify_pool(c, who[1:], level, name, amount, cap, owner_email, default)


def recipients(c, bot, owner_email=""):
    """The person to tell: the bot's operator, else the company owner."""
    row = c.execute("SELECT operator FROM bot_config WHERE bot=?", (bot,)).fetchone() if bot else None
    person = H.human(c, row["operator"]) if row and row["operator"] else None
    if not person and owner_email:
        owner = c.execute("SELECT id FROM humans WHERE lower(email)=?", (owner_email.lower(),)).fetchone()
        person = H.human(c, owner["id"]) if owner else None
    return person


def notify(c, bot, level, label_, amount, cap, owner_email=""):
    person = recipients(c, bot, owner_email)
    name = (H.bot(c, bot) or {}).get("display_name") or bot
    money = f"${amount:,.2f} of ${cap:,.2f}"
    text = (f"{name} is at {level}% of its {label_} limit ({money}, estimated)." if level < 100 else
            f"{name} reached its {label_} limit ({money}, estimated) and takes no new work until it resets or the limit is raised.")
    if person:
        H.say(c, H.KEEPER, H.human_actor(person["id"]), text, kind="notice")
    H.event(c, H.KEEPER, "usage.limit", H.bot_actor(bot), {"level": level, "period": label_, "spent": round(amount, 4), "limit": cap})


def notify_pool(c, scope, level, label_, amount, cap, owner_email="", default=None):
    """A pool's notice goes to the company owner: it holds many bots, so no one bot's operator owns it."""
    person = recipients(c, None, owner_email)
    name = label(c, scope, default)
    money = f"${amount:,.2f} of ${cap:,.2f}"
    text = (f"{name[0].upper() + name[1:]} is at {level}% of its {label_} limit ({money}, estimated)." if level < 100 else
            f"{name[0].upper() + name[1:]} reached its {label_} limit ({money}, estimated); its bots take no new work "
            "until it resets or the limit is raised.")
    if person:
        H.say(c, H.KEEPER, H.human_actor(person["id"]), text, kind="notice")
    H.event(c, H.KEEPER, "usage.limit", scope, {"level": level, "period": label_, "spent": round(amount, 4), "limit": cap})
