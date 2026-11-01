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

A Finance budget that refuses at its limit (`decline`) on a bot's AI services path is one more limit: at its cutoff
every bot it covers is held the same way and reads "Paused: AI services budget reached" (scope `budget`, no amounts,
since who reads a paused bot may not see the budget). Video production is not one: a run need not make images, so its
cutoff refuses new images instead. Caps and budgets both stay; whichever is met first holds.

The window is the UTC half hour (`per_30m`): it replaces the hour for every new limit, and an hour limit set before
stays in force. The day is the UTC day, the week starts
Monday 00:00 UTC and the month is the UTC calendar month. Over a window the bot takes no new turn and resumes when the
window ends.
"""

import json
import logging
import math
from datetime import datetime, timedelta, timezone

from .store import H

KEY = "usage_limits"                       # the company default, in registry_metadata
WARN = 80
MAX_USD = 10_000_000
PERIODS = (("per_30m", "30m"), ("hourly", "hour"), ("daily", "day"), ("weekly", "week"), ("monthly", "month"))
KEYS = tuple(name + "_usd" for name, _ in PERIODS)
COMPANY = "company"                        # the pool every bot is in
# How a period reads in a sentence (the key is what the data calls it).
WORDS = {"per_30m": "30-minute", "hourly": "hourly", "daily": "daily", "weekly": "weekly", "monthly": "monthly"}

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS usage_pools (scope TEXT PRIMARY KEY, hourly_usd REAL, daily_usd REAL, "
    "weekly_usd REAL, monthly_usd REAL, updated_at TEXT, updated_by TEXT)",
)


def ensure(c):
    """The 30-minute, hourly and weekly columns on a bot's own limits and the pools table; safe to run on any database."""
    from .hubdb import add_column
    for column in ("hourly_usd", "weekly_usd", "per_30m_usd"):
        add_column(c, "usage_limits", column, "REAL")
    for statement in SCHEMA:
        c.execute(statement)
    add_column(c, "usage_pools", "per_30m_usd", "REAL")


def half(value):
    """An hour's limit as the half hour that replaces it: half of it, rounded up to the next $0.10. The sustained rate is
    the same, in the window that replaces the hour."""
    return round(math.ceil(round(float(value) / 2 * 10, 6)) / 10, 6)


def periods(c):
    """The windows that apply: every one, the 30-minute one and the hour (kept for limits set before it) included."""
    return PERIODS


def shown(c, caps):
    """A caps dict with every window's key; one out of force would show as None."""
    live = {name + "_usd" for name, _ in periods(c)}
    return {key: (caps[key] if key in live else None) for key in KEYS}


class Default(dict):
    """The company default ({hourly_usd, ..., count_subscription}), carrying what one pass over many bots reads only once:
    the pools, the groups and each period's spend per bot. Only the dict goes out in a response."""

    def __init__(self, value):
        super().__init__(value)
        self.memo = {}


def company(c):
    """The default for bots with no limit of their own: {hourly_usd, daily_usd, weekly_usd, monthly_usd, count_subscription}.
    Subscription runs count unless the team turned that off."""
    from .read_cache import metadata
    row = metadata(c, KEY)
    try:
        stored = json.loads(row[0]) if row else {}
    except ValueError:
        stored = {}
    stored = stored if isinstance(stored, dict) else {}
    return Default({**{key: _amount(stored.get(key)) for key in KEYS}, "count_subscription": stored.get("count_subscription") is not False})


def _amount(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, 6) if 0 < number <= MAX_USD else None


def _caps(row):
    return {key: (row[key] if row and key in row.keys() else None) for key in KEYS}


OWN_SQL = "SELECT * FROM usage_limits WHERE bot=?"


def own(c, bot):
    from .read_cache import one
    return _caps(one(c, OWN_SQL, (bot,)))


def prime_own(c, bots):
    """Every bot's own limits read in one statement, for a page that asks for many bots in one snapshot."""
    from .read_cache import prime
    found = {row["bot"]: row for row in c.execute("SELECT * FROM usage_limits")}
    prime(c, OWN_SQL, {(bot,): found.get(bot) for bot in bots})


def effective(c, bot, default=None):
    """Each period's cap in USD or None, and where each came from: `bot`, `company` or None."""
    default = default if default is not None else company(c)
    mine = own(c, bot)
    out, source = {}, {}
    live = {name for name, _ in periods(c)}
    for name, _ in PERIODS:
        key = name + "_usd"
        if name not in live:
            out[key], source[name] = None, None
            continue
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


def pool_only_lowers(c, scope, body):
    """`only_lowers` for a team or group pool: no cap is the highest there is."""
    body = body if isinstance(body, dict) else {}
    now = pool(c, scope)
    for key in KEYS:
        value = _amount(body.get(key)) if body.get(key) is not None else None
        if body.get(key) is not None and value is None:
            return False
        if value is None and now[key] is not None or value is not None and now[key] is not None and value > now[key]:
            return False
    return True


# ------------------------------------------------------------------------------------------------ pools
def pools(c, default=None):
    """{scope: caps} for every pool with a cap set: `company` and `group:<id>`."""
    memo = default.memo if isinstance(default, Default) else {}
    if "pools" not in memo:
        from .read_cache import rows
        found = {}
        for row in rows(c, "SELECT * FROM usage_pools"):
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
    marks = ",".join("?" * len(KEYS))
    c.execute(f"INSERT INTO usage_pools(scope, {', '.join(KEYS)}, updated_at, updated_by) VALUES(?,{marks},?,?) "
              "ON CONFLICT(scope) DO UPDATE SET " + ", ".join(f"{key}=excluded.{key}" for key in KEYS) +
              ", updated_at=excluded.updated_at, updated_by=excluded.updated_by",
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
    """The bots a pool covers. Kept for the pass when `default` carries a memo: a page of 200 bots asks once a pool."""
    memo = default.memo if isinstance(default, Default) else {}
    key = ("members", scope)
    if key not in memo:
        _, chains = groups(c, default)
        gid = scope.split(":", 1)[1] if scope != COMPANY else None
        memo[key] = frozenset(chains) if gid is None else frozenset(bot for bot, chain in chains.items() if gid in chain)
    return memo[key]


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
    half_hour = at.replace(minute=30 if at.minute >= 30 else 0, second=0, microsecond=0)
    day = at.strftime("%Y-%m-%d")
    monday = (at - timedelta(days=at.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    year, week, _ = at.isocalendar()
    first = at.replace(day=1)
    following = first.replace(year=first.year + (first.month == 12), month=first.month % 12 + 1)
    return {"per_30m": (half_hour.strftime("%Y-%m-%dT%H:%M"), half_hour.strftime("%Y-%m-%dT%H:%M:00"),
                        (half_hour + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:00")),
            "hourly": (hour.strftime("%Y-%m-%dT%H"), hour.strftime("%Y-%m-%dT%H:00:00"),
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
            f"SELECT bot, coalesce(sum(est_cost_usd), 0) FROM {RUNS} WHERE started_at >= ? AND started_at < ? "
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
    """A pool's caps, what its bots have spent in each period with a cap, and `blocked` (the period) when one is met.
    Read now (`at` None), it is kept for the pass: every bot in the pool asks for it."""
    default = default if default is not None else company(c)
    memo = default.memo if at is None and isinstance(default, Default) else None
    key = ("pool_state", scope)
    if memo is not None and key in memo:
        return dict(memo[key])
    result = _pool_state(c, scope, at, default)
    if memo is not None:
        memo[key] = dict(result)
    return result


def _pool_state(c, scope, at, default):
    caps = shown(c, pools(c, default).get(scope) or pool(c, scope))
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


def spent_now(c, bot=None, scope=None, at=None, default=None):
    """What a bot, or a pool's bots together, has spent so far in each window in force, capped or not:
    {"30m": usd, "hour": usd, "day": usd, ...}. Budget adds these up the org chart."""
    default = default if default is not None else company(c)
    when, subscription = bounds(at), default["count_subscription"]
    out = {}
    for name, label_ in periods(c):
        _, start, end = when[name]
        amount = pool_spent(c, scope, start, end, subscription, default) if scope else \
            spent(c, bot, start, end, subscription, default)
        out[label_] = round(amount, 6)
    return out


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
    if not result["blocked"]:
        cut = budget_cutoff(c, bot, at, default)
        if cut:
            result["blocked"] = cut["period"]
            result["blocked_by"] = cut
    return result


def budget_cutoff(c, bot, at=None, default=None):
    """The Finance budget at its cutoff that holds the bot: {scope: budget, name, period, budget_id, until}, else None.
    One pass over many bots reads the budgets once (the memo on `default`). A fault in Finance holds no bot."""
    memo = default.memo if at is None and isinstance(default, Default) else None
    try:
        if memo is not None and "finance_cutoffs" in memo:
            cuts = memo["finance_cutoffs"]
        else:
            from .finance import reads as finance_reads
            cuts = finance_reads.cutoffs(c, at.strftime("%Y-%m-%dT%H:%M:%S.%fZ") if at else None)
            if memo is not None:
                memo["finance_cutoffs"] = cuts
        cut = cuts.of(bot)
    except Exception:
        logging.getLogger(__name__).exception("Reading Finance budget cutoffs failed")
        return None
    if cut is None:
        return None
    return {"scope": "budget", "name": cut.name, "period": cut.period, "budget_id": cut.budget_id, "until": cut.until}


def blocked(c, bot, at=None, default=None):
    """The period (`hourly`, `daily`, `weekly` or `monthly`) when the bot may take no new job, else None."""
    return state(c, bot, at, default)["blocked"]


def why(met):
    """The words on a paused bot, from `over`'s answer (or a bare period, for the bot's own limit)."""
    met = met if isinstance(met, dict) else {"period": met, "scope": "bot"}
    word = WORDS.get(met["period"], met["period"])
    if met.get("scope") == "budget":
        return f"Paused: {met['name']} budget reached"
    if met.get("scope", "bot") == "bot":
        return f"Paused: over its {word} limit"
    return f"Paused: {met['name']} is over its {word} limit"


def over(c, bot, at=None, default=None):
    """The limit a bot is held by, for the pages and the hub tools: {period, spent, limit, until, scope, name}, else
    None. `scope` is `bot`, `company`, `group:<id>` or `budget` (a Finance budget at its cutoff, with no amounts);
    `name` names a pool or budget; `until` is when the period turns over (UTC; "" for a one-time budget)."""
    found = state(c, bot, at, default)
    met = found["blocked_by"]
    if not met:
        return None
    if met["scope"] == "budget":
        return dict(met)
    return {**met, "until": bounds(at)[met["period"]][2] + "Z"}


def resets(period, until):
    """When a period turns over, as a person reads it."""
    if period == "per_30m":
        return f"{until:%H:%M} UTC"
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
    found = H.bot(c, bot) or {}
    name = found.get("display_name") or found.get("slug") or bot
    if met["scope"] == "budget" and not met["until"]:
        return f"{name} is waiting: the {met['name']} budget is used up; it starts again when a person raises it."
    reset = resets(met["period"], datetime.fromisoformat(met["until"][:-1]))
    if met["scope"] == "budget":
        return (f"{name} is waiting: the {met['name']} budget is used up for this period; it starts again on "
                f"{reset} or when a person raises the budget.")
    if met["scope"] == "bot":
        return (f"{name} is over its {WORDS.get(met['period'], met['period'])} limit (${met['spent']:,.2f} of ${met['limit']:,.2f}); "
                f"it starts again at {reset} or when a person raises the limit.")
    return (f"{name} is waiting: {met['name']} is over its {WORDS.get(met['period'], met['period'])} limit; "
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
        checks.append(("@" + scope, shown(c, pools(c, default)[scope]), lambda amount_of, s=scope: sum(amount_of(b) for b in members(c, s, default))))
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
    row = c.execute("SELECT owner_id FROM bot_config WHERE bot=?", (bot,)).fetchone() if bot else None
    person = H.human(c, row["owner_id"]) if row and row["owner_id"] else None
    if not person and owner_email:
        owner = c.execute("SELECT id FROM humans WHERE lower(email)=?", (owner_email.lower(),)).fetchone()
        person = H.human(c, owner["id"]) if owner else None
    return person


def notify(c, bot, level, label_, amount, cap, owner_email=""):
    person = recipients(c, bot, owner_email)
    found = H.bot(c, bot) or {}
    name = found.get("display_name") or found.get("slug") or bot
    money = f"${amount:,.2f} of ${cap:,.2f}"
    word = WORDS.get(label_, label_)
    text = (f"{name} is at {level}% of its {word} limit ({money}, estimated)." if level < 100 else
            f"{name} reached its {word} limit ({money}, estimated) and takes no new work until it resets or the limit is raised.")
    if person:
        H.say(c, H.KEEPER, H.human_actor(person["id"]), text, kind="notice")
    H.event(c, H.KEEPER, "usage.limit", H.bot_actor(bot), {"level": level, "period": label_, "spent": round(amount, 4), "limit": cap})


def notify_pool(c, scope, level, label_, amount, cap, owner_email="", default=None):
    """A pool's notice goes to the company owner: it holds many bots, so no one bot's operator owns it."""
    person = recipients(c, None, owner_email)
    name = label(c, scope, default)
    money = f"${amount:,.2f} of ${cap:,.2f}"
    word = WORDS.get(label_, label_)
    text = (f"{name[0].upper() + name[1:]} is at {level}% of its {word} limit ({money}, estimated)." if level < 100 else
            f"{name[0].upper() + name[1:]} reached its {word} limit ({money}, estimated); its bots take no new work "
            "until it resets or the limit is raised.")
    if person:
        H.say(c, H.KEEPER, H.human_actor(person["id"]), text, kind="notice")
    H.event(c, H.KEEPER, "usage.limit", scope, {"level": level, "period": label_, "spent": round(amount, 4), "limit": cap})
