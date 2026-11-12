"""What the bot list and a bot's page read about many bots at once: one query per kind of row for all
of them, then lookups, so a request costs about the same for 7 bots as for 90. Privacy checks that
are per task, job or message stay per row (backend/task_privacy.py); only the reading is shared.

A bot's queue counts live here too (`job_queues`): the one count of a bot's waiting work, on its page and in the bot
list. Health splits the same queue over every job (`health.queue_counts`)."""

from . import task_privacy as privacy
from . import read_cache
from . import providers
from .harnesses import is_external, resolve_harness
from .shared_bots import _json, source_of
from .store import H

DEAD_IDS = 20  # dead job ids listed per bot


def _by(c, sql, slugs, key="bot"):
    """{row[key]: row} for `sql` with `{}` standing for the `IN (...)` of `slugs`."""
    out = {}
    for part in H.chunks(slugs):
        for row in c.execute(sql.format(",".join("?" * len(part))), part):
            out.setdefault(row[key], row)
    return out


def hours(since, now):
    a, b = H.parse_ts(since), H.parse_ts(now)
    return round((b - a).total_seconds() / 3600, 1) if a and b else None


def job_queues(c, who, bots, now=None):
    """{bot: {queued, held, quarantine_held, dead, uncertain, oldest_queued_hours, dead_ids}} for readable jobs: the one
    count of a bot's waiting work, on its page and in the bot list, so the two never disagree. A job behind a run
    nobody reviewed (`execution.unfenced_sql`) is `dead`, a job its wake preset holds is `held`, and a refusal-count
    hold is `quarantine_held`; only the rest is `queued`."""
    privacy.snapshot(c)
    actor = privacy.actor(who)
    rows = queue_job_rows(c, bots)
    # One privacy prime for every queued/uncertain message (and its attempt), not message_readable per row: the
    # status reads rebuild these counts on every poll.
    shown = {m["id"] for m in privacy.readable_messages(c, actor, [{"id": row["id"]} for row in rows])}
    privacy.prime_attempts(c, [row["job_attempt"] for row in rows if row["id"] in shown and row["job_attempt"]])
    memo = {}

    def keep(row):
        return row["id"] in shown and (
            not row["job_attempt"] or privacy.attempt_readable(c, actor, row["job_attempt"], memo)
        )

    out = tally_queues(bots, rows, now, keep)
    for q in out.values():
        del q["oldest_queued_at"], q["oldest_dead_at"], q["oldest_quarantine_at"]
    return out


def queue_job_rows(c, bots):
    """Every queued or uncertain job of these bots, oldest first, with whether the claim may hand it out (`free`) and
    whether a refusal-count hold keeps it (`quarantine_free`)."""
    from .execution import queued_task_sql, unfenced_sql
    from .task_quarantine import free_sql

    rows = []
    for part in H.chunks(list(bots)):
        marks = ",".join("?" * len(part))
        rows.extend(
            c.execute(
                f"SELECT m.*, j.id AS job_id, j.bot_id AS job_bot, j.state AS job_state, j.created_at AS job_created, "
                f"j.held AS job_held, j.attempt_id AS job_attempt, {unfenced_sql()} AS free, "
                f"{free_sql(queued_task_sql())} AS quarantine_free FROM jobs j "
                "JOIN messages m ON m.id=j.message_id JOIN conversations cv ON cv.id=m.conversation_id "
                f"WHERE j.state IN ('queued','uncertain') AND j.bot_id IN ({marks}) ORDER BY j.created_at, j.id",
                part,
            )
        )
    return rows


def tally_queues(bots, rows, now, keep):
    """Split `rows` (queue_job_rows) per bot into queued, held, quarantine_held, dead and uncertain, with the oldest
    queued, dead and quarantine-held job's time. `keep(row)` leaves out a row the reader may not see."""
    now = now or H.now()
    out = {
        b: {
            "queued": 0,
            "held": 0,
            "quarantine_held": 0,
            "dead": 0,
            "uncertain": 0,
            "oldest_quarantine_at": None,
            "oldest_queued_hours": None,
            "oldest_queued_at": None,
            "oldest_dead_at": None,
            "dead_ids": [],
        }
        for b in bots
    }
    stopped = {b: set() for b in bots}
    for row in rows:
        if keep and not keep(row):
            continue
        q = out[row["job_bot"]]
        if row["job_state"] == "uncertain":
            # Counted per stopped run: requests folded into one run share its attempt and are one run to a person.
            key = row["job_attempt"] or row["job_id"]
            if key not in stopped[row["job_bot"]]:
                stopped[row["job_bot"]].add(key)
                q["uncertain"] += 1
        elif not row["quarantine_free"]:
            q["quarantine_held"] += 1
            q["oldest_quarantine_at"] = q["oldest_quarantine_at"] or row["job_created"]
        elif not row["free"]:
            q["dead"] += 1
            q["oldest_dead_at"] = q["oldest_dead_at"] or row["job_created"]
            if len(q["dead_ids"]) < DEAD_IDS:
                q["dead_ids"].append(row["job_id"])
        elif row["job_held"]:
            q["held"] += 1
        else:
            q["queued"] += 1
            q["oldest_queued_at"] = q["oldest_queued_at"] or row["job_created"]
    for q in out.values():
        q["oldest_queued_hours"] = hours(q["oldest_queued_at"], now)
    return out


def _live_runs(c, slugs):
    """{bot: (task ids, conversation ids)} that a run of the bot is working now: its lease is still renewed
    (task_wakes.live_run, for many bots in one read)."""
    out = {slug: (set(), set()) for slug in slugs}
    now = H.now()
    for part in H.chunks(slugs):
        for bot, wake_key, conversation in c.execute(
            "SELECT j.bot_id, m.wake_key, m.conversation_id FROM jobs j JOIN attempts a ON a.id = j.attempt_id "
            "JOIN messages m ON m.id = j.message_id WHERE a.state IN ('leased','running') AND a.lease_until > ? "
            f"AND j.bot_id IN ({','.join('?' * len(part))})",
            (now, *part),
        ):
            tasks, conversations = out[bot]
            if wake_key:
                tasks.add(str(wake_key).split(":", 1)[0])
            if conversation:
                conversations.add(conversation)
    return out


def task_flow(c, tasks_by_bot, who=None):
    """{bot: {"going", "waiting"}} over each bot's open tasks the reader may see: `going` has a live run, `waiting` is
    the bot's turn with nothing running (it is woken by the never-stuck check). A task that waits on a person,
    another task or room counts in neither."""
    every = [row for tasks in tasks_by_bot.values() for row in tasks]
    waits = H.task_waits(c, [row["id"] for row in every]) if every else {}
    live = _live_runs(c, list(tasks_by_bot))
    out = {}
    for bot, tasks in tasks_by_bot.items():
        going = waiting = 0
        running, rooms = live.get(bot, (set(), set()))
        for row in tasks:
            if who is not None and not privacy.task_readable(c, who, row):
                continue
            if row["id"] in running or (row.get("conversation_id") and row["conversation_id"] in rooms):
                going += 1
                continue
            first = (waits.get(row["id"]) or [None])[0]
            if first is not None and first.state == "bot" and first.who == "bot:" + bot:
                waiting += 1
        out[bot] = {"going": going, "waiting": waiting}
    return out


class BotRows:
    def __init__(self, c, who, slugs, readable):
        """`slugs`: the bots shown; `readable`: those the caller may read, whose activity is read too."""
        privacy.snapshot(c)
        slugs = list(dict.fromkeys(slugs))
        read = [slug for slug in slugs if slug in readable]
        self.configs = {r["bot"]: r for r in read_cache.configs(c, slugs)}
        # The originals of shared bots, which a branch follows.
        sources = {source_of(_json(row["config"])) for row in self.configs.values()} - {""} - set(self.configs)
        self.configs.update({r["bot"]: r for r in read_cache.configs(c, sources)})
        self.humans = {row["id"]: row for row in H.humans(c)}
        self.assignments = _by(
            c,
            "SELECT a.bot,a.runner_id,a.generation,r.label,r.owner_id,r.last_seen_at,"
            "r.revoked_at FROM assignments a JOIN runners_with_state r ON r.id=a.runner_id "
            "WHERE a.bot IN ({})",
            read,
        )
        self.draining = set(_by(c, "SELECT bot FROM bot_control WHERE draining=1 AND bot IN ({})", read))
        self.scopes = {
            bot: row["claim_task"]
            for bot, row in _by(
                c, "SELECT bot,claim_task FROM bot_control WHERE claim_task IS NOT NULL AND bot IN ({})", read
            ).items()
        }
        self.statuses = _by(c, "SELECT * FROM bot_status WHERE bot IN ({})", read)
        self.status_inputs = privacy.status_inputs(c, list(self.statuses))
        # Work that will run, not work fenced behind a run nobody reviewed.
        self.queued = {slug: q["queued"] for slug, q in job_queues(c, who, read).items()}
        self.notes = H.notes_waiting_counts(c, read, limit=500)
        tasks = {}
        for part in H.chunks(read):
            for task in H._rows(
                c.execute(
                    "SELECT * FROM tasks WHERE owner_id IN (" + ",".join("?" * len(part)) + ") AND status='open'",
                    ["bot:" + slug for slug in part],
                )
            ):
                tasks.setdefault(H.actor_id(task["owner_id"]), []).append(task)
        # Every task the rows below look at is read once, with its tags, so each privacy check and lookup of it reads none.
        H.prime_tasks(c, [t["id"] for ts in tasks.values() for t in ts] + list(self.scopes.values()))
        self.task_flow = task_flow(c, {slug: tasks.get(slug, []) for slug in read}, who)
        self.goals = {}
        for part in H.chunks(slugs):
            for row in c.execute(
                "SELECT bot,conversation_id FROM chat_goals WHERE status='active' AND bot IN "
                f"({','.join('?' * len(part))})",
                part,
            ):
                self.goals.setdefault(row["bot"], []).append(row["conversation_id"])

    def config(self, slug):
        """The bot's bot_config row, or None."""
        return self.configs.get(slug)

    def declared(self, slug):
        """shared_bots.declared, from the rows read."""
        row = self.configs.get(slug)
        return _json(row["config"]) if row else {}

    def model(self, c, settings, declared):
        """The model a bot with config `declared` runs on (providers.bot_choice), the company's choice read once."""
        if not hasattr(self, "_providers"):
            self._providers = providers.load(c, settings)
        return providers.bot_choice(c, settings, declared, self._providers)[1]

    def human(self, pid):
        """H.human, from the rows read."""
        return self.humans.get(H.actor_id(pid))

    def external_harness(self, slug):
        """agents.external_harness, from the rows read."""
        row = self.configs.get(slug)
        if not row:
            return None
        config = H._json(row["config"], {}) or {}
        return resolve_harness(config) if is_external(config) else None
