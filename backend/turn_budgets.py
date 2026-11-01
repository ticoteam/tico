"""Turn budgets: a bot that starts too many runs is held (quiet task flow, section 8).

Every bot has a budget of runs per hour and per day, `turns_per_hour` (default 300) and `turns_per_day` (default 2500).
The team sets the defaults (Settings API, owner and admins); a bot's owner, operator or BotOps may override either on
the bot, and a bot with no override of its own follows the team. 0 is no limit for that window; `null`/"default" on a
bot clears its override.

The claim counts the bot's runs (attempts created) in the last hour and day before it hands out work nobody asked for.
At the limit the bot is held: its queued work is kept, none of it starts, and what every page says is

    Tico stopped <bot>: <n> runs in the last hour. This looks like a runaway loop, not normal work. Check its last runs,
    then release it or raise its budget.

(`in the last day` for the day's). A person's own message is never refused by the budget, and still counts. The bot's
operator gets one Needs-you item and Health a finding. One release clears the hold (`bot_stops.release`, POST
/api/v2/bots/{bot}/release); the count starts over from the release, so a bot released while still over the limit
is not held again by the runs that held it. Raising the budget above the count releases it too.
"""

from pydantic import Field

from .batch_work import shared_check
from .models import Contract
from .store import H, Problem

MAX = 100_000
KEYS = ("turns_per_hour", "turns_per_day")
DEFAULTS = {"turns_per_hour": 300, "turns_per_day": 2500}
WINDOWS = (("hour", "turns_per_hour", 3600, "hour"), ("day", "turns_per_day", 86400, "day"))
TEAM_KEY = "turn-budget-defaults"

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS bot_budget(bot TEXT PRIMARY KEY, held_at TEXT, window TEXT, runs INTEGER, budget INTEGER, "
    "task_id TEXT, released_at TEXT, released_by TEXT)",)


def ensure(c):
    for statement in SCHEMA:
        c.execute(statement)


class BotBudget(Contract):
    """Fields left out stay as they are; null clears the bot's own value so it follows the team; 0 is no limit."""
    turns_per_hour: int | None = Field(default=None, ge=0, le=MAX)
    turns_per_day: int | None = Field(default=None, ge=0, le=MAX)
    expected_revision: int = Field(ge=1)


class TeamBudgets(Contract):
    """The team defaults for bots with no budget of their own. Fields left out stay as they are; 0 is no limit."""
    turns_per_hour: int | None = Field(default=None, ge=0, le=MAX)
    turns_per_day: int | None = Field(default=None, ge=0, le=MAX)


class ReleaseBot(Contract):
    """Release a bot from every hold Tico put on it (failed runs, run budget, queued runs it could not start)."""


# ---------------------------------------------------------------------------------------------------- the numbers
def _whole(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= MAX else None


def team(c):
    """The team defaults: what the owner or an admin saved, over DEFAULTS."""
    from .repositories import metadata
    stored = metadata(c, TEAM_KEY)
    return {key: (_whole(stored.get(key)) if _whole(stored.get(key)) is not None else DEFAULTS[key]) for key in KEYS}


def own(declared):
    """The bot's own values from its config, None for a key it does not set."""
    declared = declared if isinstance(declared, dict) else {}
    return {key: _whole(declared.get(key)) for key in KEYS}


def limits(c, declared):
    """The limits in force for a bot with this config: its own value, else the team's. 0 is no limit."""
    mine, shared = own(declared), team(c)
    return {key: mine[key] if mine[key] is not None else shared[key] for key in KEYS}


def counts(c, bot, floor="", now=None):
    """The bot's runs in the last hour and the last day, counted from `floor` at the earliest (a release)."""
    now = now or H.now()
    out = {}
    for window, _, seconds, _ in WINDOWS:
        since = max(H.shift(now, seconds=-seconds), floor or "")
        out[window] = c.execute("SELECT count(*) FROM attempts WHERE bot_id=? AND created_at>?", (bot, since)).fetchone()[0]
    return out


# ---------------------------------------------------------------------------------------------------- the hold
GET_SQL = "SELECT * FROM bot_budget WHERE bot=?"


def get(c, bot):
    """The bot's row, read once per snapshot (read_cache): a status page primes every bot's (events.prime_status_lines)."""
    from .read_cache import one
    row = one(c, GET_SQL, (bot,))
    return dict(row) if row else {}


def held(c):
    """Every bot held on its budget: the rows Health lists."""
    return [dict(r) for r in c.execute("SELECT * FROM bot_budget WHERE held_at IS NOT NULL ORDER BY held_at")]


def refusal(c, bot, message):
    """Why the claim passes over one queued job for the bot's budget: "budget" while the bot is held, for any job but a
    person's message, else None. Read only (execution.job_refusal)."""
    if str(message.get("sender_id") or "").startswith("human:"):
        return None
    return "budget" if get(c, bot).get("held_at") else None


def enforce(c, settings, job, message, now=None):
    """Called by the claim, in its write transaction, with the job it is about to hand out: True when it must pass the
    job over because the bot is over its budget. The first time it records the hold, files the operator's Needs-you
    item and tells the events; after that `refusal` keeps the other jobs from being chosen. A person's message is
    never refused."""
    if str(message.get("sender_id") or "").startswith("human:"):
        return False
    bot = job["bot"]
    state = get(c, bot)
    if state.get("held_at"):
        return True
    row = c.execute("SELECT config FROM bot_config WHERE bot=?", (bot,)).fetchone()
    declared = _declared(row["config"] if row else None)
    # The team's defaults are read here: one failure there is every bot's, not this one's.
    with shared_check("team"):
        cap = limits(c, declared)
    now = now or H.now()
    seen = counts(c, bot, state.get("released_at") or "", now)
    for window, key, _, label in WINDOWS:
        if cap[key] and seen[window] >= cap[key]:
            _hold(c, settings, bot, window, seen[window], cap[key], now)
            return True
    return False


def _declared(raw):
    import json
    try:
        value = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _hold(c, settings, bot, window, runs, budget, now):
    from . import bot_stops
    name = bot_stops.name_of(c, bot)
    task_id = bot_stops.raise_item(
        c, settings, bot, f"{name} stopped: {runs} runs in the last {window}",
        f"{_sentence(name, runs, window)}\n\nIts budget is {budget} runs per {window}. Check its last runs in Runs. "
        f"Then release {name} (Settings > Bots > {name}, or `hub bot release {bot}`) or raise its budget "
        f"(`hub bot budget {bot} --{window} N`). Everything queued for it is kept and starts after the release.")
    c.execute("INSERT INTO bot_budget(bot,held_at,window,runs,budget,task_id) VALUES(?,?,?,?,?,?) "
              "ON CONFLICT(bot) DO UPDATE SET held_at=excluded.held_at,window=excluded.window,runs=excluded.runs,"
              "budget=excluded.budget,task_id=excluded.task_id", (bot, now, window, runs, budget, task_id))
    H.event(c, H.KEEPER, "bot.budget_held", bot, {"window": window, "runs": runs, "budget": budget, "task": task_id})
    H.hook(c, "bot.held", bot, lambda: {"bot": bot, "reason": "budget", "window": window, "runs": runs, "budget": budget,
                                        "task": task_id}, H.KEEPER, revision=now)
    # One notice each to BotOps and Health when the guard trips (CONTRACT §1 run guard); the hold itself is the same.
    words = (f"{name} is held: {runs} runs in the last {window} against a budget of {budget}. It takes no new turns "
             "until a person releases it.")
    for slug in ("botops", "health-monitor"):
        if H.bot(c, slug):
            H.say(c, H.KEEPER, H.bot_actor(slug), words, kind="notice", refs={"held": bot})


def _sentence(name, runs, window):
    return (f"Tico stopped {name}: {runs} runs in the last {window}. This looks like a runaway loop, not normal work. "
            "Check its last runs, then release it or raise its budget.")


def words(c, bot):
    """What every page says about a budget hold."""
    from . import bot_stops
    state = get(c, bot)
    if not state.get("held_at"):
        return ""
    return _sentence(bot_stops.name_of(c, bot), state.get("runs"), state.get("window") or "hour")


def release(c, actor, bot):
    """Lift the hold; the count starts over from now. Answers whether the bot was held."""
    state = get(c, bot)
    if not state.get("held_at"):
        return False
    from . import bot_stops
    c.execute("UPDATE bot_budget SET held_at=NULL,task_id=NULL,released_at=?,released_by=? WHERE bot=?",
              (H.now(), actor, bot))
    bot_stops.close_item(c, state.get("task_id"), "Released.")
    H.event(c, actor, "bot.budget_released", bot, {"window": state.get("window"), "runs": state.get("runs")})
    H.hook(c, "bot.released", bot, lambda: {"bot": bot, "reason": "budget", "released_by": actor}, actor, revision=H.now())
    return True


# ---------------------------------------------------------------------------------------------------- the settings
def view(c, bot, config):
    """The bot's budget as the API and the tools show it: in force, its own, the team's, the runs so far and the holds."""
    from . import run_backoff
    declared = _declared(config["config"])
    state, failing = get(c, bot), run_backoff.get(c, bot)
    now = H.now()
    return {"bot": bot, **limits(c, declared), "own": own(declared), "team": team(c),
            "runs": counts(c, bot, state.get("released_at") or "", now),
            "held": ({"window": state["window"], "runs": state["runs"], "budget": state["budget"],
                      "since": state["held_at"], "words": words(c, bot)} if state.get("held_at") else None),
            "run_hold": ({"fails": failing["fails"], "error_class": failing["error_class"], "since": failing["held_at"],
                          "next_try": run_backoff.probe_at(failing),
                          "words": run_backoff.words(c, bot)} if failing.get("held_at") else None),
            "revision": config["revision"]}


def set_bot(admin, c, who, bot, body, undo_change_id=None):
    """Set the bot's own budget (SettingsAdmin's pattern: manager check, revision check, audit row, undo)."""
    admin._manager(c, who, bot, botops=True)
    config = admin._config(c, bot)
    if config["revision"] != body.expected_revision:
        raise Problem("version_conflict", "Bot configuration changed; refresh before saving", 409)
    from .store import encode
    declared = _declared(config["config"])
    before = own(declared)
    after = {key: (getattr(body, key) if key in body.model_fields_set else before[key]) for key in KEYS}
    for key in KEYS:
        if after[key] is None:
            declared.pop(key, None)
        else:
            declared[key] = after[key]
    c.execute("UPDATE bot_config SET config=?,revision=revision+1 WHERE bot=?", (encode(declared), bot))
    admin.record(c, who.actor, bot, "turn_budget", before, after)
    if undo_change_id:
        c.execute("UPDATE settings_changes SET undone_by=?,undone_at=? WHERE id=? AND undone_at IS NULL",
                  (who.actor, H.now(), undo_change_id))
    H.event(c, who.actor, "bot.turn_budget_changed", bot, {"before": before, "after": after})
    freed = _release_if_room(c, who.actor, bot, declared)
    return {**view(c, bot, {"config": encode(declared), "revision": config["revision"] + 1}), "released": freed}


def _release_if_room(c, actor, bot, declared):
    """A budget raised above the runs that held the bot (or turned off) lets it go: the person has answered the hold."""
    state = get(c, bot)
    if not state.get("held_at"):
        return False
    cap = limits(c, declared)[next(key for window, key, _, _ in WINDOWS if window == state["window"])]
    runs = counts(c, bot, state.get("released_at") or "")[state["window"]]
    return release(c, actor, bot) if not cap or runs < cap else False


def set_team(c, actor, body):
    """Save the team defaults (owner and admins; the route checks). Answers the defaults now in force."""
    from .repositories import metadata, save_metadata
    stored = metadata(c, TEAM_KEY)
    before = team(c)
    for key in KEYS:
        if key in body.model_fields_set:
            if getattr(body, key) is None:
                stored.pop(key, None)
            else:
                stored[key] = getattr(body, key)
    save_metadata(c, TEAM_KEY, stored)
    after = team(c)
    freed = []
    if after != before:
        H.event(c, actor, "settings.turn_budgets", "team", {"before": before, "after": after})
        # A held bot that follows the team's budget is let go when the new one leaves it room, as its own would.
        for row in c.execute("SELECT b.bot, g.config FROM bot_budget b JOIN bot_config g ON g.bot=b.bot "
                             "WHERE b.held_at IS NOT NULL").fetchall():
            if _release_if_room(c, actor, row["bot"], _declared(row["config"])):
                freed.append(row["bot"])
    return {**after, "defaults": DEFAULTS, "released": freed}


# ---------------------------------------------------------------------------------------------------- the routes
def install(app, store, auth, mutate, settings_admin):
    from fastapi import Request

    def seeing(c, who, bot):
        auth.domain(who)
        if not H.bot(c, bot):
            raise Problem("not_found", "Bot not found", 404)
        auth.require_see(c, who, bot)

    @app.get("/api/v2/bots/{bot}/budget")
    def bot_budget(request: Request, bot: str):
        """A bot's run budget: in force, its own and the team's, its runs so far and any hold. For the people who
        manage it."""
        who = request.state.identity
        with store.read() as c:
            bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
            seeing(c, who, bot)
            settings_admin._manager(c, who, bot, botops=True)
            return view(c, bot, settings_admin._config(c, bot))

    @app.post("/api/v2/bots/{bot}/budget")
    def bot_budget_set(request: Request, bot: str, body: BotBudget):
        """Set the bot's own runs per hour and per day; null follows the team, 0 is no limit. Raising it above the
        runs that held the bot releases it."""
        who = request.state.identity

        def work(c):
            nonlocal bot
            bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
            seeing(c, who, bot)
            return set_bot(settings_admin, c, who, bot, body)
        return mutate(request, body, work)

    @app.post("/api/v2/bots/{bot}/release")
    def bot_release(request: Request, bot: str, body: ReleaseBot):
        """Release a bot Tico stopped, for failed runs or for too many runs. Its queued work starts again."""
        who = request.state.identity

        def work(c):
            nonlocal bot
            bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
            seeing(c, who, bot)
            settings_admin._manager(c, who, bot, botops=True)
            from . import bot_stops
            return {"bot": bot, "released": bot_stops.release(c, who.actor, bot)}
        return mutate(request, body, work)

    def admin_only(who, what):
        auth.domain(who)
        if not auth.bot_admin(who):
            raise Problem("forbidden", "Only an owner or an admin " + what, 403)

    @app.get("/api/v2/settings/turn-budgets")
    def team_budgets(request: Request):
        """The team defaults for each bot's runs per hour and per day."""
        admin_only(request.state.identity, "sees the team's run budgets")
        with store.read() as c:
            return {**team(c), "defaults": DEFAULTS}

    @app.post("/api/v2/settings/turn-budgets")
    def team_budgets_set(request: Request, body: TeamBudgets):
        """Set the team defaults; bots with no budget of their own follow them. 0 is no limit."""
        if not body.model_fields_set & set(KEYS):
            raise Problem("validation", "Give turns_per_hour or turns_per_day", 422)

        def permitted(c):
            auth.sync_access(c)
            who = request.state.identity
            current = auth.identity_for_actor(c, who.actor) if who.role in ("owner", "human") and not who.support else who
            admin_only(current, "changes the team's run budgets")
        return mutate(request, body, lambda c: set_team(c, request.state.identity.actor, body), check=permitted)
