"""What a runner's claim reads for each of its bots, loaded once for all of them in fixed set queries and passed down
explicitly. The claim (execution.candidate) reads it once per pass, before its per-bot checks; Health reads it once per
page (health._prime_claims). Both run the same checks with it (execution._claim_refusal). A lookup the object does not
hold, a bot it was not loaded for, is asked of the database the way the check always asked it, so a caller without the
object keeps the same answers."""


class Loads:
    """The company's values read once (`record`, `plain`, `access`, `given`), and each bot's rows keyed by the bot.
    `runners` is {id: runners row}; `holds` is every open hold, by (kind, holdable_type, holdable_id, bot_id or ''),
    so a missing key is no hold."""

    def __init__(self, record, plain, access, given, runners, limits, backoff, held, holds, company=None):
        self.record = record  # providers.load(c, settings): the company's choice, the environment's seed included
        self.plain = plain  # providers.load(c): the same without the seed (for_claim reads it so); None if unread
        self.access = access  # cloud_access.access(c)
        self.given = given  # model_subscriptions._given(c): {(runner_id, runtime): subscription}
        self.runners = runners
        self.limits = limits  # {bot: own usage limits, as usage_limits.own returns them}
        self.backoff = backoff  # {bot: run_backoff row as a dict, {} when none}
        self.held = held  # {bot: turns it holds on every computer}
        self.holds = holds  # {(kind, holdable_type, holdable_id, bot_id or ''): open holds row}
        self.company = company  # company_pause.record(c): the company's pause on record, None while it runs

    def runner_of(self, c, runner_id, live=False):
        """The runners row, or None; `live` leaves out a revoked computer (as the capability reads do)."""
        if runner_id in self.runners:
            row = self.runners[runner_id]
        else:
            from .read_cache import one

            row = one(c, "SELECT * FROM runners_with_state WHERE id=?", (runner_id,))
        return None if live and row is not None and row["revoked_at"] is not None else row

    def plain_record(self, c):
        if self.plain is None:
            from . import providers

            self.plain = providers.load(c)
        return self.plain

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

    def held_of(self, c, bot):
        if bot in self.held:
            return self.held[bot]
        from . import start_places

        return start_places.bot_held(c, bot)


def read(c, settings, runners, bots):
    """The Loads for `bots` (their ids) and `runners` ({id: runners row}), in fixed set queries: the company's values
    once, then each bot's rows in chunks of 500 bots (one statement while there are fewer)."""
    from . import providers, cloud_access, usage_limits, run_backoff, start_places
    from . import model_subscriptions, company_pause

    bots = list(dict.fromkeys(bots))
    record = providers.load(c, settings)
    given = model_subscriptions._given(c)
    plain = providers.load(c) if given else None
    access = cloud_access.access(c)
    return Loads(
        record,
        plain,
        access,
        given,
        dict(runners),
        usage_limits.own_many(c, bots),
        run_backoff.get_many(c, bots),
        start_places.held_counts(c, bots),
        open_holds(c),
        company_pause.record(c),
    )


def open_holds(c):
    """Every open hold of every kind (backend/holds.py), by (kind, holdable_type, holdable_id, bot_id or '')."""
    return {
        (row["kind"], row["holdable_type"], row["holdable_id"], row["bot_id"] or ""): dict(row)
        for row in c.execute("SELECT * FROM holds WHERE released_at IS NULL")
    }
