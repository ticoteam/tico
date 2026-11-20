"""What a runner's claim reads for each of its bots, loaded once for all of them in fixed set queries and passed down
explicitly. The claim (execution.candidate) reads it once per pass, before its per-bot checks; Health reads it once per
page (health._prime_claims). Both run the same checks with it (execution._claim_refusal). A lookup the object does not
hold, a bot it was not loaded for, is asked of the database the way the check always asked it, so a caller without the
object keeps the same answers.

A claim's recheck of the one job it selected (execution.candidate with `job_id`, under the write lock) reads `narrow`:
the open holds of that job's bot and the holds of no bot, and that computer's grants, not every open hold and every
computer's grants. Both are `Part`s: the same answers for those keys, the whole read for any other."""

from collections.abc import Iterator, Mapping


class Loads:
    """The company's values read once (`record`, `plain`, `access`, `given`), and each bot's rows keyed by the bot.
    `runners` is {id: runners row}; `holds` is every open hold, by (kind, holdable_type, holdable_id, bot_id or ''),
    so a missing key is no hold."""

    def __init__(self, record, plain, access, given, runners, limits, failing, held, holds, company=None):
        self.record = record  # providers.load(c, settings): the company's choice, the environment's seed included
        self.plain = plain  # providers.load(c): the same without the seed (for_claim reads it so); None if unread
        self.access = access  # cloud_access.access(c)
        self.given = given  # model_subscriptions._given(c): {(runner_id, runtime): subscription}
        self.runners = runners
        self.limits = limits  # {bot: own usage limits, as usage_limits.own returns them}
        self.failing = failing  # {bot: its open `failing` hold, None when none} (run_failures.get reads it)
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

    def held_of(self, c, bot):
        if bot in self.held:
            return self.held[bot]
        from . import start_places

        return start_places.bot_held(c, bot)


class Part(Mapping):
    """A mapping read for some of its keys: `rows` holds every entry whose key is `inside(key)`, exactly as the whole
    mapping has it. Any other key, an iteration or a length reads the whole (`whole()`), once. Its truth is the
    whole's: `rows`, else `any_()` (whether the whole has an entry), else the whole read."""

    def __init__(self, rows, inside, whole, any_=None):
        self.rows, self.inside, self._read, self._any = rows, inside, whole, any_
        self._whole = None

    def whole(self):
        if self._whole is None:
            self._whole = self._read()
        return self._whole

    def __getitem__(self, key):
        return self.rows[key] if self.inside(key) else self.whole()[key]

    def get(self, key, default=None):
        return self.rows.get(key, default) if self.inside(key) else self.whole().get(key, default)

    def __contains__(self, key):
        return key in self.rows if self.inside(key) else key in self.whole()

    def __iter__(self) -> Iterator:
        return iter(self.whole())

    def __len__(self):
        return len(self.whole())

    def __bool__(self):
        if self.rows:
            return True
        if self._whole is None and self._any is not None:
            return bool(self._any())
        return bool(self.whole())


def read(c, settings, runners, bots, narrow=False):
    """The Loads for `bots` (their ids) and `runners` ({id: runners row}), in fixed set queries: the company's values
    once, then each bot's rows in chunks of 500 bots (one statement while there are fewer). `narrow`: the holds and
    grants of these bots and computers only (`Part`), for a check of one job."""
    from . import providers, cloud_access, usage_limits, start_places
    from . import model_subscriptions, company_pause

    bots = list(dict.fromkeys(bots))
    record = providers.load(c, settings)
    given = model_subscriptions.given_of(c, list(runners)) if narrow else model_subscriptions._given(c)
    plain = providers.load(c) if given else None
    access = cloud_access.access(c)
    holds = open_holds_of(c, bots) if narrow else open_holds(c)
    return Loads(
        record,
        plain,
        access,
        given,
        dict(runners),
        usage_limits.own_many(c, bots),
        {bot: holds.get(("failing", "bot", bot, bot)) for bot in bots},  # its open `failing` hold (run_failures)
        start_places.held_counts(c, bots),
        holds,
        company_pause.record(c),
    )


def open_holds(c):
    """Every open hold of every kind (backend/holds.py), by (kind, holdable_type, holdable_id, bot_id or ''). Read
    through holds_bot, the partial index of open holds only: the table keeps every released hold as history, and stale
    stats can plan a scan of all of it (test_holds_usage_limit_plan)."""
    return {
        (row["kind"], row["holdable_type"], row["holdable_id"], row["bot_id"] or ""): dict(row)
        for row in c.execute("SELECT * FROM holds INDEXED BY holds_bot WHERE released_at IS NULL")
    }


def open_holds_of(c, bots):
    """open_holds for the keys of these bots and of no bot (bot_id NULL or ''), the only keys a claim's check of one
    job asks (holds.job_hold, login_holds, the claim's own claim_error keys): read together through holds_bot, so the
    answers are the snapshot's, as one read of every open hold gives them. Any other key reads every open hold."""
    names = sorted({str(bot) for bot in bots})
    rows = {
        (row["kind"], row["holdable_type"], row["holdable_id"], row["bot_id"] or ""): dict(row)
        for row in c.execute(
            "SELECT * FROM holds INDEXED BY holds_bot WHERE released_at IS NULL "
            f"AND bot_id IN ({','.join('?' * (len(names) + 1))}) UNION ALL "
            "SELECT * FROM holds INDEXED BY holds_bot WHERE released_at IS NULL AND bot_id IS NULL",
            [*names, ""],
        )
    }
    inside = set(names) | {""}
    return Part(rows, lambda key: isinstance(key, tuple) and len(key) == 4 and key[3] in inside, lambda: open_holds(c))
