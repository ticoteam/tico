"""What the bot list and a bot's page read about many bots at once: one query per kind of row for all
of them, then lookups, so a request costs about the same for 7 bots as for 90. Privacy checks that
are per task, job or message stay per row (backend/task_privacy.py); only the reading is shared."""

from . import task_privacy as privacy
from . import auto_tasks, pipeline_stats, read_cache
from .harnesses import is_external, resolve_harness
from .shared_bots import _json, source_of
from .store import H


def _by(c, sql, slugs, key="bot"):
    """{row[key]: row} for `sql` with `{}` standing for the `IN (...)` of `slugs`."""
    out = {}
    for part in H.chunks(slugs):
        for row in c.execute(sql.format(",".join("?" * len(part))), part):
            out.setdefault(row[key], row)
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
        self.assignments = _by(c, "SELECT a.bot,a.runner_id,a.generation,r.label,r.owner_id,r.last_seen_at,"
                                  "r.revoked_at FROM assignments a JOIN runners r ON r.id=a.runner_id "
                                  "WHERE a.bot IN ({})", read)
        self.draining = set(_by(c, "SELECT bot FROM bot_control WHERE draining=1 AND bot IN ({})", read))
        self.scopes = {bot: row["claim_task"] for bot, row in _by(
            c, "SELECT bot,claim_task FROM bot_control WHERE claim_task IS NOT NULL AND bot IN ({})", read).items()}
        self.statuses = _by(c, "SELECT * FROM bot_status WHERE bot IN ({})", read)
        self.status_inputs = privacy.status_inputs(c, list(self.statuses))
        # The queued number of hub pipeline: work that will run, not work fenced behind a run nobody reviewed.
        self.queued = {slug: q["queued"] for slug, q in pipeline_stats.job_queues(c, who, read).items()}
        self.next_run = H.next_run_tasks_by(c, read)
        self.notes = H.notes_waiting_counts(c, read, limit=500)
        tasks = {}
        for part in H.chunks(read):
            for task in H._rows(c.execute("SELECT * FROM tasks WHERE owner_id IN (" + ','.join('?' * len(part)) +
                                          ") AND status IN ('open','doing')", ['bot:' + slug for slug in part])):
                tasks.setdefault(H.actor_id(task['owner_id']), []).append(task)
        # Every task the rows below look at is read once, with its tags, so each privacy check and lookup of it reads none.
        H.prime_tasks(c, [t['id'] for ts in [*tasks.values(), *self.next_run.values()] for t in ts]
                      + list(self.scopes.values()))
        self.task_flow = auto_tasks.counts_many(c, {slug: tasks.get(slug, []) for slug in read}, who)
        self.goals = {}
        for part in H.chunks(slugs):
            for row in c.execute("SELECT bot,conversation_id FROM chat_goals WHERE status='active' AND bot IN "
                                 f"({','.join('?' * len(part))})", part):
                self.goals.setdefault(row["bot"], []).append(row["conversation_id"])

    def config(self, slug):
        """The bot's bot_config row, or None."""
        return self.configs.get(slug)

    def declared(self, slug):
        """shared_bots.declared, from the rows read."""
        row = self.configs.get(slug)
        return _json(row["config"]) if row else {}

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
