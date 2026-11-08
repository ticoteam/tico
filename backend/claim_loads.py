"""What a runner's claim reads for each of its bots, loaded once for all of them in fixed set queries and passed down
explicitly. The claim (execution.candidate) reads it once per pass, before its per-bot checks; Health reads it once per
page (health._prime_claims). Both run the same checks with it (execution._claim_refusal). A lookup the object does not
hold, a bot it was not loaded for, is asked of the database the way the check always asked it, so a caller without the
object keeps the same answers."""
import json


def _dict(raw):
    try:
        value = json.loads(raw) if raw else {}
    except (ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


class Loads:
    """The company's values read once (`record`, `plain`, `access`, `given`), and each bot's rows keyed by the bot.
    `runners` is {id: runners row}; `holds` is every login hold ({key: stored value}), so a missing key is no hold."""

    def __init__(self, record, plain, access, given, runners, status, waits, limits, backoff, exits, held, holds):
        self.record = record        # providers.load(c, settings): the company's choice, the environment's seed included
        self.plain = plain          # providers.load(c): the same without the seed (for_claim reads it so); None if unread
        self.access = access        # cloud_access.access(c)
        self.given = given          # model_subscriptions._given(c): {(runner_id, runtime): subscription}
        self.runners = runners
        self.status = status        # {bot: bot_status row as a dict, {} when none}
        self.waits = waits          # {bot: the Gateway's wait, a dict}
        self.limits = limits        # {bot: own usage limits, as usage_limits.own returns them}
        self.backoff = backoff      # {bot: run_backoff row as a dict, {} when none}
        self.exits = exits          # {bot: browser exit's computer or None}; None when not read
        self.held = held            # {bot: turns it holds on every computer}
        self.holds = holds          # {login-hold key: stored value}

    def runner_of(self, c, runner_id, live=False):
        """The runners row, or None; `live` leaves out a revoked computer (as the capability reads do)."""
        if runner_id in self.runners:
            row = self.runners[runner_id]
        else:
            from .read_cache import one
            row = one(c, "SELECT * FROM runners WHERE id=?", (runner_id,))
        return None if live and row is not None and row["revoked_at"] is not None else row

    def plain_record(self, c):
        if self.plain is None:
            from . import providers
            self.plain = providers.load(c)
        return self.plain

    def status_of(self, c, bot):
        if bot in self.status:
            return self.status[bot]
        from .hubdb import status
        return status(c, bot) or {}

    def wait_of(self, c, bot):
        if bot in self.waits:
            return self.waits[bot]
        from .repositories import metadata
        from .execution import GATEWAY_WAIT
        return metadata(c, GATEWAY_WAIT + bot)

    def limit_of(self, c, bot):
        if bot in self.limits:
            return self.limits[bot]
        from . import usage_limits
        return usage_limits.own(c, bot)

    def backoff_of(self, c, bot):
        if bot in self.backoff:
            return self.backoff[bot]
        from . import run_backoff
        return run_backoff.get(c, bot)

    def exit_of(self, c, bot):
        if self.exits is not None and bot in self.exits:
            return self.exits[bot]
        from . import browser_exit
        return browser_exit.bot_exit(c, bot)

    def held_of(self, c, bot):
        if bot in self.held:
            return self.held[bot]
        from . import start_places
        return start_places.bot_held(c, bot)

    def hold_of(self, key):
        """The login hold stored under `key`, as a dict ({} when none)."""
        return _dict(self.holds.get(key))


def read(c, settings, runners, bots, *, exits=True):
    """The Loads for `bots` (their ids) and `runners` ({id: runners row}), in fixed set queries: the company's values
    once, then each bot's rows in chunks of 500 bots (one statement while there are fewer). `exits`: whether the
    browser exits are read (a computer that does not advertise browser_exit_v1 needs them)."""
    from . import providers, cloud_access, usage_limits, run_backoff, browser_exit, start_places
    from . import model_subscriptions
    from .hubdb import chunks
    from .execution import GATEWAY_WAIT
    bots = list(dict.fromkeys(bots))
    record = providers.load(c, settings)
    given = model_subscriptions._given(c)
    plain = providers.load(c) if given else None
    access = cloud_access.access(c)
    status = {bot: {} for bot in bots}
    waits = {bot: {} for bot in bots}
    for part in chunks(bots):
        marks = ",".join("?" * len(part))
        for row in c.execute(f"SELECT * FROM bot_status WHERE bot IN ({marks})", part):
            status[row["bot"]] = dict(row)
        for row in c.execute("SELECT key,value FROM registry_metadata WHERE key IN ("
                             + ",".join("?" * len(part)) + ")", [GATEWAY_WAIT + bot for bot in part]):
            waits[row["key"][len(GATEWAY_WAIT):]] = _dict(row["value"])
    holds = {row["key"]: row["value"] for row in c.execute(
        "SELECT key,value FROM registry_metadata WHERE key>=? AND key<?", (_hold_prefix(), _hold_prefix()[:-1] + ";"))}
    return Loads(record, plain, access, given, dict(runners), status, waits,
                 usage_limits.own_many(c, bots), run_backoff.get_many(c, bots),
                 browser_exit.exits_of(c, bots) if exits else None, start_places.held_counts(c, bots), holds)


def _hold_prefix():
    from .login_holds import PREFIX
    return PREFIX
