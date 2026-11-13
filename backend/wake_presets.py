"""One release policy for every bot run that can wait, on the job hold (Product #248, spec v4, items 8-11).

A queued job that can wait carries four values (`jobs.release`, JSON, see `policy`):

- `due`: the earliest time it may start (null: when it was queued).
- `wait`: how long after Due it may wait, in seconds (0: it starts at Due).
- `warm` (Prefers warm): released the first time after Due that the bot has been idle WARM_AFTER (no turn running, its
  last model request that long ago), and before WARM_BEFORE, while the 1-hour cache is still warm. Several jobs of one
  bot released by the same window run one after another, oldest Due first (`release_after` chains them).
- `deadline` (at Due + wait): `run` starts it cold, as before; `skip` drops it and records it as skipped.
- `session`: `fork` asks the runner to fork the bot's latest session (execution.py hands `fork_thread`), `fresh` (or
  null) starts a new one.

What is held is decided at enqueue time by the `wake_preset_hold` trigger on jobs, so every source is held the same
way, and only from Tico's own notices (sender H.KEEPER): nobody can make a wake wait by sending text that looks like
one. Two kinds wait:

- a nonblocking notice the server marks with `refs.wake_batch` (BATCH_KINDS: a finished request, review comments, a
  failing check nobody armed a wait for): one built-in wait of NOTICE_SECONDS, Prefers warm off, Run cold. Notices for
  one conversation that arrive inside the wait are let go together (one turn, or one digest). A Lead's notices wait for
  its daily digest hour instead (backend/profiles.py), one digest for the bot.
- a job whose notice carries `refs.release` (a policy): a scheduled routine's run (`automations.release_of`) and, in
  wave 2b, a dream. Its class is `routine` unless `refs.release_kind` names another (RELEASE_KINDS). The repeat guard
  applies to routines: no second run while one of the same routine is queued or running, and none sooner than
  REPEAT_SECONDS after the last one started.

Never held: a task's turn in a pipeline (`task_turn`, backend/task_wakes.py) and a person's message start at once, and
a held job never holds anything else back. Other news (a comment, a task moving, any notice no rule names) starts no job
at all (backend/rules.py `default_note`).

One release check, `tick`, runs in the scheduler's write transaction every tick: it plans each newly held job's Due
(`hold_until`) and lets go of what is due. A release is an UPDATE of `held`, which tells the bot's computer there is
work. The wake presets (Token saver, Performance) and the profiles' wake numbers that this replaces are gone: past runs
keep the preset they recorded (`jobs.wake_preset`, `measures`), and nothing new writes it.

A job whose message repeats another of the bot's in the same conversation (same sender, kind, text, refs and reply) is
dropped as a duplicate, so a duplicated event wakes the bot once: while the first is queued or being worked, and for
DUPLICATE_SECONDS after its turn ended. A notice a batch carried (its newest, or its digest) is live while its carrier
is, so a redelivery after the batch is let go still matches. A message from a person or a bot repeats only when nothing
else was said in between, so the same words sent again after an answer are a new request.
"""

import json

from . import hubdb as H
from . import bot_settings as BS

# A run's tokens: its input (uncached, cached and written to the cache) and output, as its usage records them.
RUN_TOKENS = "coalesce(a.input_tokens,0)+coalesce(a.cached_tokens,0)+coalesce(a.cache_write_tokens,0)+coalesce(a.output_tokens,0)"

# The labels past runs recorded (`jobs.wake_preset`, `attempts.wake.preset`): history only, never written now.
CURRENT = "current"
TOKEN_SAVER = "token_saver"
PERFORMANCE = "performance"
CHOICES = (CURRENT, TOKEN_SAVER, PERFORMANCE)
LABELS = {CURRENT: "Immediate (legacy)", TOKEN_SAVER: "Token saver", PERFORMANCE: "Performance"}

# The built-in waits (spec v4): a nonblocking notice waits 5 minutes; a routine's next run starts at least 15 minutes
# after its last one started.
NOTICE_SECONDS = 5 * 60
REPEAT_SECONDS = 15 * 60
# The warm window: idle at least this long and less than that, inside the 1-hour prompt cache.
WARM_AFTER = 45 * 60
WARM_BEFORE = 55 * 60
MAX_WAIT = 14 * 86400  # the longest wait a policy keeps (a weekly routine's is 7 days)
RUN, SKIP = "run", "skip"
FRESH, FORK = "fresh", "fork"
ROUTINE = "routine"
RELEASE_KINDS = (ROUTINE, "dream")
# An immediate wake not picked up within this long counts as a missed action in `measures`.
MISSED_AFTER_SECONDS = 5 * 60
# How long after a turn ends an identical event still counts as the same one (a retried webhook, a double send).
DUPLICATE_SECONDS = 2 * 60
# Nonblocking notices the keeper marks with refs.wake_batch.
BATCH_KINDS = ("completion", "review", "ci", "rule")
# Jobs held as `notice` by an earlier build's news hold, let go with the batch above.
NOTICE = "notice"
DIGEST_ROOMS = 40  # rooms a digest names; it counts the rest
DIGEST_IDS = 200  # job ids its event keeps
HELD_CLASSES = (*BATCH_KINDS, NOTICE)
# The classes the policy releases: its kinds, and `sweep`, a routine run an earlier build held.
POLICY_CLASSES = (*RELEASE_KINDS, "sweep")
IMMEDIATE = (
    "A person's message or request",
    "An assignment",
    "A direct question",
    "A task to work",
    "An awaited result (a sub-task finished, a worktree ready, an account reconnected)",
    "An actionable blocker",
)
_BATCH_SQL = "json_extract(m.refs,'$.wake_batch') IN (" + ",".join(f"'{k}'" for k in BATCH_KINDS) + ")"
_POLICY_SQL = "json_type(m.refs,'$.release')='object'"
_KIND_SQL = (
    "CASE WHEN json_extract(m.refs,'$.release_kind') IN ("
    + ",".join(f"'{k}'" for k in RELEASE_KINDS)
    + f") THEN json_extract(m.refs,'$.release_kind') ELSE '{ROUTINE}' END"
)
# The same event as an earlier job's message: same room, sender, kind, text, refs and reply. Only the message's own
# fields: a job's hold flag changes when its batch is let go.
_SAME_EVENT = (
    "om.sender_id IS m.sender_id AND om.kind IS m.kind AND om.body=m.body "
    "AND om.refs IS m.refs AND om.in_reply_to IS m.in_reply_to"
)
# A keeper notice repeats any earlier one in its room; a person's or a bot's message repeats only the room's message
# just before it (nothing said in between). Two lookups rather than one OR, so each seeks: the keeper's walks only its
# room's earlier messages, and the other reads that one previous message by rowid, found once per insert.
_EARLIER = (
    f"m.sender_id='{H.KEEPER}' AND om.conversation_id=m.conversation_id AND om.rowid<m.rowid",
    "om.rowid=(SELECT max(x.rowid) FROM messages x WHERE x.conversation_id=m.conversation_id AND x.rowid<m.rowid)",
)
_DUPLICATE = " OR ".join(
    f"""EXISTS(SELECT 1 FROM messages m JOIN messages om ON {earlier}
   JOIN jobs o ON o.message_id=om.id AND o.bot_id=NEW.bot_id AND o.id<>NEW.id
   AND (o.state<>'canceled' OR o.wake_carrier IS NOT NULL)
   LEFT JOIN jobs cj ON cj.id=o.wake_carrier LEFT JOIN attempts a ON a.id=coalesce(cj.attempt_id,o.attempt_id)
   WHERE m.id=NEW.message_id AND {_SAME_EVENT} AND (coalesce(cj.state,o.state) IN ('queued','leased','running','input')
   OR a.finished_at>=strftime('%Y-%m-%dT%H:%M:%fZ',m.created_at,'-{DUPLICATE_SECONDS} seconds')))"""
    for earlier in _EARLIER
)
# The hold reads only the job's own notice: nothing per bot (no preset, no profile number) decides what waits.
HOLD_TRIGGER = f"""CREATE TRIGGER wake_preset_hold AFTER INSERT ON jobs WHEN NEW.state='queued' BEGIN
 UPDATE jobs SET held=1,
   wake_class=(SELECT CASE WHEN {_BATCH_SQL} THEN json_extract(m.refs,'$.wake_batch') ELSE {_KIND_SQL} END
     FROM messages m WHERE m.id=NEW.message_id),
   release=(SELECT CASE WHEN {_BATCH_SQL} THEN NULL ELSE json_extract(m.refs,'$.release') END
     FROM messages m WHERE m.id=NEW.message_id)
   WHERE id=NEW.id AND EXISTS(SELECT 1 FROM messages m WHERE m.id=NEW.message_id
   AND m.sender_id='{H.KEEPER}' AND coalesce(json_extract(m.refs,'$.wake_immediate'),0)<>1
   AND ({_BATCH_SQL} OR {_POLICY_SQL}));
 UPDATE jobs SET state='canceled',held=0,wake_class='duplicate' WHERE id=NEW.id AND ({_DUPLICATE});
END"""
# The profile options and settings the presets wrote, which the migration in `ensure` removes.
_RETIRED_OPTIONS = ("batch_seconds", "batch_scope", "sweep_seconds")


def ensure(c):
    """The hold's columns and trigger, and the move off the wake presets. Unversioned like the other job columns: an
    older release never reads them, and the trigger is rebuilt whenever its text changes."""
    H.add_column(c, "jobs", "wake_preset", "TEXT")
    H.add_column(c, "jobs", "wake_class", "TEXT")
    H.add_column(c, "jobs", "held", "INTEGER NOT NULL DEFAULT 0")
    H.add_column(c, "jobs", "hold_until", "TEXT")
    # Why a claim holds a job it could not start, its Needs-you item, and how many tries in a row failed
    # (backend/claim_holds.py). Unversioned like the others: an older release never reads them.
    H.add_column(c, "jobs", "hold_reason", "TEXT")
    H.add_column(c, "jobs", "hold_task_id", "TEXT")
    H.add_column(c, "jobs", "hold_streak", "INTEGER NOT NULL DEFAULT 0")
    # The job whose turn carried this one's notice (a batch's newest, or its digest), for `measures`.
    H.add_column(c, "jobs", "wake_carrier", "TEXT")
    # The release policy's own columns (`release`, `release_after`, `released`) come from migration
    # 20261113061438_job_release_policy, and from store.SCHEMA on a new file.
    c.execute("CREATE INDEX IF NOT EXISTS jobs_held ON jobs(bot_id,hold_until) WHERE held=1")
    # The few held jobs still queued, in claim order: what tick reads every pass under the write lock (HELD_*_SQL).
    c.execute("CREATE INDEX IF NOT EXISTS jobs_held_queued ON jobs(created_at, id) WHERE held=1 AND state='queued'")
    # The duplicate check seeks a room's messages before the new one by rowid (the index's implicit suffix), not a walk
    # through every earlier message from the same sender in every room.
    c.execute("CREATE INDEX IF NOT EXISTS messages_room_rowid ON messages(conversation_id)")
    stored = c.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name='wake_preset_hold'").fetchone()
    if not stored or stored[0] != HOLD_TRIGGER:
        c.execute("DROP TRIGGER IF EXISTS wake_preset_hold")
        c.execute(HOLD_TRIGGER)
    retire_presets(c)


def retire_presets(c):
    """The migration off the wake presets (spec v4): every bot's `wake_preset` and its profile's wake numbers (notice
    window, batch scope, sweep gap) go, and each held job is planned again on the policy. Touches only bots that still
    carry one, so it runs once per bot however often the server starts. Returns how many bots it changed."""
    paths = ", ".join(f"'$.profile_options.{k}'" for k in _RETIRED_OPTIONS)
    carrying = "wake_preset IS NOT NULL OR json_type(extra,'$.wake_preset') IS NOT NULL OR " + " OR ".join(
        f"json_type(extra,'$.profile_options.{k}') IS NOT NULL" for k in _RETIRED_OPTIONS
    )
    bots = [r[0] for r in c.execute(f"SELECT bot_id FROM bot_settings WHERE {carrying}").fetchall()]
    if not bots:
        return 0
    marks = ",".join("?" * len(bots))
    c.execute(
        f"UPDATE bot_settings SET wake_preset=NULL,extra=json_remove(coalesce(extra,'{{}}'),'$.wake_preset',{paths}),"
        f"updated_at=? WHERE bot_id IN ({marks})",
        (H.now(), *bots),
    )
    c.execute(f"UPDATE bot_config SET revision=revision+1 WHERE bot IN ({marks})", bots)
    for bot in bots:
        replan(c, bot)
    return len(bots)


def policy(due=None, wait=0, warm=False, deadline=RUN, session=None):
    """A release policy for `refs.release`: Due (an ISO time, or None for when the job is queued), how long it may wait
    after Due in seconds, Prefers warm, what happens at the deadline (`run` or `skip`) and the session (`fresh`, `fork`
    or None). Refuses values it would not keep (ValueError)."""
    if due is not None and not H.parse_ts(due):
        raise ValueError("due is an ISO time or None")
    if isinstance(wait, bool) or not isinstance(wait, int) or not 0 <= wait <= MAX_WAIT:
        raise ValueError(f"wait is whole seconds from 0 to {MAX_WAIT}")
    if deadline not in (RUN, SKIP):
        raise ValueError("deadline is run or skip")
    if session not in (None, FRESH, FORK):
        raise ValueError("session is fresh, fork or None")
    return {"due": due, "wait": wait, "warm": bool(warm), "deadline": deadline, "session": session}


def policy_of(raw):
    """A job's stored policy, read leniently: an unreadable value is a job that starts at Due."""
    given = H._json(raw, {}) if isinstance(raw, str) else raw
    given = given if isinstance(given, dict) else {}
    wait = given.get("wait")
    return {
        "due": given.get("due") if isinstance(given.get("due"), str) else None,
        "wait": wait if isinstance(wait, int) and not isinstance(wait, bool) and 0 <= wait <= MAX_WAIT else 0,
        "warm": given.get("warm") is True,
        "deadline": SKIP if given.get("deadline") == SKIP else RUN,
        "session": given.get("session") if given.get("session") in (FRESH, FORK) else None,
    }


def waits(config):
    """What the bot's settings and the API show about waiting: the built-in waits, a Lead's digest hour, and what wakes
    it at once. Nothing here is a per-bot choice."""
    from . import profiles

    hour = profiles.digest_hour(config)
    summary = (
        f"Finished requests, review comments and check results wait {_duration(NOTICE_SECONDS)}, one wake per "
        f"conversation; a routine runs at least {_duration(REPEAT_SECONDS)} after its last run started, and waits for a "
        "warm session as its own settings say. People, assignments, questions, awaited results and blockers wake it "
        "at once."
    )
    if hour is not None:
        summary = (
            f"The rest of the news arrives once a day, at {hour:02d}:00 team time. "
            "People, escalations and asks wake it at once."
        )
    return {
        "notice_seconds": NOTICE_SECONDS,
        "repeat_seconds": REPEAT_SECONDS,
        "warm_window": [WARM_AFTER, WARM_BEFORE],
        "digest_hour": hour,
        "profile": profiles.name_of(config),
        "immediate": list(IMMEDIATE),
        "summary": summary,
    }


def _digest_hour(c, bot, cache):
    """The hour of the bot's daily digest (a Lead's), or None: its notices then wait NOTICE_SECONDS."""
    from . import profiles

    if bot not in cache:
        row = c.execute(BS.ONE_SQL, (bot,)).fetchone()
        cache[bot] = profiles.digest_hour(H._json(row[0], {}) if row else {})
    return cache[bot]


def _duration(seconds):
    if seconds % 3600 == 0 and seconds >= 3600:
        return f"{seconds // 3600} h"
    if seconds % 60 == 0 and seconds >= 60:
        return f"{seconds // 60} min"
    return f"{seconds} s"


def sweep_key(refs):
    """Which routine a run is, for the repeat guard: its automation, else (an earlier build's) sweep or task."""
    return str(refs.get("automation") or refs.get("sweep") or refs.get("task") or "")


def release_quota_resets(c, at=None):
    """A quota retry keeps its job and becomes claimable at the reset even between scheduler ticks."""
    c.execute(
        "UPDATE jobs SET held=0 WHERE held=1 AND state='queued' AND wake_class='quota-reset' "
        "AND hold_reason IS NULL AND hold_until<=?",
        (at or H.now(),),
    )


# Read on every scheduler tick under the write lock. INDEXED BY jobs_held_queued: the planner otherwise scans
# messages and seeks each job, because the stale sqlite_stat1 row for messages (16k rows when analysed, 72k
# now) makes that look cheapest. 40-44 ms a tick on TIDY v0.5.1, 480 ticks an hour.
HELD_NEW_SQL = (
    "SELECT j.id,j.bot_id,j.wake_class,j.created_at,j.release,m.conversation_id,m.refs "
    "FROM jobs j INDEXED BY jobs_held_queued JOIN messages m ON m.id=j.message_id "
    "WHERE j.held=1 AND j.hold_until IS NULL AND j.hold_reason IS NULL AND j.state='queued' "
    "ORDER BY j.created_at,j.id"
)
HELD_DUE_SQL = (
    "SELECT j.id,j.bot_id,j.wake_class,j.message_id,j.created_at,j.hold_until,j.release,j.release_after,j.released,"
    "m.conversation_id,m.refs "
    "FROM jobs j INDEXED BY jobs_held_queued JOIN messages m ON m.id=j.message_id "
    "WHERE j.held=1 AND j.state='queued' AND j.hold_until IS NOT NULL AND j.hold_until<=? "
    "AND j.hold_reason IS NULL ORDER BY j.created_at,j.id"
)


def tick(c, at=None):
    """The one release check. Plan each newly held job's Due, then let go of what is due: a notice batch at its Due, a
    policy job at Due when it does not prefer warm, in the bot's warm window when it does, and at its deadline cold or
    skipped. Runs in the scheduler's write transaction every tick."""
    at = at or H.now()
    hours = {}
    for job in c.execute(HELD_NEW_SQL).fetchall():
        if job["wake_class"] in POLICY_CLASSES:
            given = policy_of(job["release"])
            due = _repeat_slot(c, job, given["due"] or job["created_at"])
            if due is None:
                c.execute("UPDATE jobs SET state='canceled',held=0 WHERE id=?", (job["id"],))
                H.event(c, H.KEEPER, "job.wake_merged", job["id"], {"bot": job["bot_id"]})
                # Its routine task goes with it: nothing else wakes it, and an open one would be worked as a second run.
                _close_task(c, job, "Merged: this routine's previous run is still waiting or running.")
                continue
            c.execute(
                "UPDATE jobs SET hold_until=?,release=? WHERE id=?",
                (due, json.dumps({**given, "due": due}), job["id"]),
            )
        else:
            c.execute(
                "UPDATE jobs SET hold_until=? WHERE id=?",
                (_batch_slot(c, job, _digest_hour(c, job["bot_id"], hours)), job["id"]),
            )
    groups, waiting = {}, {}
    # A run a claim holds (backend/claim_holds.py) has a wait of its own, and only its claim releases it.
    for job in c.execute(HELD_DUE_SQL, (at,)).fetchall():
        if job["wake_class"] == "quota-reset":
            # A quota retry is the same job, not news to batch, fold into another request or turn into a digest.
            c.execute("UPDATE jobs SET held=0 WHERE id=?", (job["id"],))
        elif job["wake_class"] in POLICY_CLASSES:
            waiting.setdefault(job["bot_id"], []).append(job)
        else:
            per_bot = _digest_hour(c, job["bot_id"], hours) is not None
            groups.setdefault((job["bot_id"], "" if per_bot else job["conversation_id"]), []).append(job)
    for (bot, _), jobs in groups.items():
        _release(c, bot, jobs)
    for bot, jobs in waiting.items():
        _release_policy(c, bot, jobs, at)


def _repeat_slot(c, job, due):
    """The routine repeat guard: when this run may start, at Due or REPEAT_SECONDS after the same routine's last run
    started (its first claim). None when a run of the same routine is still queued or running: this one adds nothing
    that one will not do. Other kinds (a dream) start at Due."""
    if job["wake_class"] not in (ROUTINE, "sweep"):
        return due
    key = sweep_key(H._json(job["refs"], {}) or {})
    if not key:
        return due
    since = H.shift(due, seconds=-REPEAT_SECONDS)
    last = None
    for row in c.execute(
        "SELECT j.state,m.refs,(SELECT min(a.created_at) FROM attempts a WHERE a.job_id=j.id) AS started_at "
        "FROM jobs j JOIN messages m ON m.id=j.message_id "
        "WHERE j.bot_id=? AND j.wake_class IN ('routine','sweep') AND j.id<>? AND j.state<>'canceled' "
        "AND (j.state IN ('queued','leased','running','input') "
        "OR EXISTS(SELECT 1 FROM attempts a WHERE a.job_id=j.id AND a.created_at>?))",
        (job["bot_id"], job["id"], since),
    ):
        if sweep_key(H._json(row["refs"], {}) or {}) != key:
            continue
        if row["state"] in ("queued", "leased", "running", "input"):
            return None
        if row["started_at"]:
            last = max(last or "", row["started_at"])
    return max(due, H.shift(last, seconds=REPEAT_SECONDS)) if last else due


def idle_seconds(c, bot, at):
    """How long the bot has been idle at `at`: None while a turn of it is running, or when it never ran (no warm cache
    to wait for). Its last model request is read as its last turn's end."""
    if c.execute("SELECT 1 FROM attempts WHERE bot_id=? AND state IN ('leased','running') LIMIT 1", (bot,)).fetchone():
        return None
    # Only a turn that reached the model warmed the cache: one that failed before any request did not.
    last = c.execute(
        "SELECT max(finished_at) FROM attempts WHERE bot_id=? AND coalesce(input_tokens,0)+coalesce(cached_tokens,0)"
        "+coalesce(cache_write_tokens,0)+coalesce(output_tokens,0)>0",
        (bot,),
    ).fetchone()[0]
    return _seconds(last, at) if last else None


def _release_policy(c, bot, jobs, at):
    """Let go of the bot's due policy jobs: a job chained after another once that one is over; a job that does not
    prefer warm now; jobs that do, together, when the bot is in its warm window (one after another, oldest Due first);
    and at the deadline, cold or skipped."""
    order = sorted(jobs, key=lambda j: (j["hold_until"], j["created_at"], j["id"]))
    free = []
    for job in order:
        if not job["release_after"]:
            free.append(job)
            continue
        before = c.execute("SELECT state FROM jobs WHERE id=?", (job["release_after"],)).fetchone()
        if not before or before[0] not in ("queued", "leased", "running", "input"):
            _let_go(c, job, at, warm=True)
        elif at >= H.shift(job["hold_until"], seconds=policy_of(job["release"])["wait"]):
            # The one before it never ended: its own deadline still holds, as if no window had come.
            if policy_of(job["release"])["deadline"] == SKIP:
                _skip(c, job, at)
            else:
                _let_go(c, job, at, warm=False)
    if not free:
        return
    idle = idle_seconds(c, bot, at)
    window = idle is not None and WARM_AFTER <= idle < WARM_BEFORE
    chain = []
    for job in free:
        given = policy_of(job["release"])
        if not given["warm"]:
            _let_go(c, job, at, warm=window)
        elif window:
            chain.append(job)
        elif at >= H.shift(job["hold_until"], seconds=given["wait"]):
            if given["deadline"] == SKIP:
                _skip(c, job, at)
            else:
                _let_go(c, job, at, warm=False)
    previous = None
    for job in chain:
        if previous is None:
            _let_go(c, job, at, warm=True)
        else:
            # Next once the one before is over; the window's cache stays warm while they run.
            c.execute("UPDATE jobs SET release_after=? WHERE id=?", (previous, job["id"]))
        previous = job["id"]


def _waited(job, at):
    return int(_seconds(job["hold_until"], at) or 0)


def _let_go(c, job, at, warm):
    """Release a policy job, recording how: warm or cold, and how long it waited after Due."""
    c.execute(
        "UPDATE jobs SET held=0,released=? WHERE id=?",
        (json.dumps({"warm": bool(warm), "waited": _waited(job, at), "at": at}), job["id"]),
    )


def _skip(c, job, at):
    """At the deadline with Skip: the job does not run and is recorded as skipped with its wait. A routine's task is
    closed with it, so the routine's next run is not absorbed into it."""
    waited = _waited(job, at)
    c.execute(
        "UPDATE jobs SET state='canceled',held=0,released=? WHERE id=?",
        (json.dumps({"skipped": True, "warm": False, "waited": waited, "at": at}), job["id"]),
    )
    refs = H._json(job["refs"], {}) or {}
    H.event(
        c,
        H.KEEPER,
        "job.release_skipped",
        job["id"],
        {"bot": job["bot_id"], "class": job["wake_class"], "waited": waited, "task": refs.get("task")},
    )
    _close_task(
        c,
        job,
        "Skipped: the bot found no warm session before this run's deadline, and the routine skips at the deadline.",
    )


def _close_task(c, job, why):
    """Close the open task a dropped run was filed as (a routine run's own task), so nothing works it later. Only a task
    the run made itself, i.e. one whose first job is this one: a run that names an existing task (a person's, a
    pipeline's) never closes it."""
    refs = H._json(job["refs"], {}) or {}
    task = H.task(c, H._task_ref(refs.get("task")) or "") if refs.get("task") else None
    if not task or task.get("status") != "open":
        return
    first = c.execute(
        "SELECT j.id FROM jobs j JOIN messages m ON m.id=j.message_id WHERE m.conversation_id=? "
        "AND j.bot_id=? ORDER BY j.rowid LIMIT 1",
        (task["conversation_id"], job["bot_id"]),
    ).fetchone()
    if first and first[0] == job["id"]:
        H.attempt(c, H.task_close, H.KEEPER, task["id"], why)


def replan(c, bot):
    """Plan the bot's held jobs again from when they were queued (the migration off the presets)."""
    c.execute(
        "UPDATE jobs SET hold_until=NULL,release_after=NULL WHERE bot_id=? AND held=1 AND state='queued' "
        "AND coalesce(wake_class,'')<>'quota-reset' AND hold_reason IS NULL",
        (bot,),
    )


def fork_thread(c, job_id, bot, runner_id):
    """The session a released job with session `fork` continues from: the thread of the bot's latest finished turn on
    this computer (a fork reads it from there) on a task that is not private, or None. A private task's session and a
    chat's are never forked into a routine's run, whose task others may read; the runner keeps those apart too."""
    row = c.execute("SELECT release FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not row or policy_of(row[0])["session"] != FORK:
        return None
    found = c.execute(
        "SELECT a.thread_id FROM attempts a JOIN tasks t ON t.id=a.task_id WHERE a.bot_id=? AND a.runner_id=? "
        "AND a.thread_id IS NOT NULL AND a.finished_at IS NOT NULL AND coalesce(a.job_id,'')<>? "
        "AND coalesce(t.private,0)=0 ORDER BY a.finished_at DESC LIMIT 1",
        (bot, runner_id, job_id),
    ).fetchone()
    return found[0] if found else None


def fresh_session(c, job_id):
    """Whether a job the release policy let go must start a session of its own (session `fresh`, the default) rather
    than resume its conversation's: the runner then starts a new one and leaves the conversation's session as it was."""
    row = c.execute("SELECT wake_class,release,released FROM jobs WHERE id=?", (job_id,)).fetchone()
    return bool(
        row and row["wake_class"] in POLICY_CLASSES and row["released"] and policy_of(row["release"])["session"] != FORK
    )


def reported_session(kind, why):
    """What the run record's fork/fresh says once the runner reports how the session began: `fork`, `fresh` or
    `resumed` (a job that asked for one and resumed its conversation's instead, e.g. on an older runner)."""
    if kind == "new":
        return FORK if why == "forked" else FRESH
    return "resumed"


def _batch_slot(c, job, hour):
    """When a nonblocking notice may wake the bot: with the batch already waiting for its conversation (for a Lead, the
    bot's digest), else NOTICE_SECONDS after it arrived, or at a Lead's next digest hour."""
    per_bot = hour is not None
    kinds = ",".join(f"'{k}'" for k in HELD_CLASSES)
    waiting = c.execute(
        "SELECT min(j.hold_until) FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.bot_id=? "
        f"AND j.held=1 AND j.state='queued' AND j.hold_until IS NOT NULL AND j.wake_class IN ({kinds}) "
        "AND j.hold_reason IS NULL AND (? OR m.conversation_id=?)",
        (job["bot_id"], per_bot, job["conversation_id"]),
    ).fetchone()[0]
    if waiting:
        return waiting
    if per_bot:
        return digest_time(job["created_at"], hour)
    return H.shift(job["created_at"], seconds=NOTICE_SECONDS)


def digest_time(created, hour):
    """The next `hour`:00 in the team's time zone after `created_at` (a Lead's daily digest), as a UTC timestamp."""
    from datetime import timedelta, timezone
    from .updates import ZONE

    at = (H.parse_ts(created) or H.parse_ts(H.now())).astimezone(ZONE)
    due = at.replace(hour=hour, minute=0, second=0, microsecond=0)
    if due <= at:
        due = (due.replace(tzinfo=None) + timedelta(days=1)).replace(tzinfo=ZONE)
    return due.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _release(c, bot, jobs):
    """Let a due batch go as one wake. Notices about one task in one room that all fit in the room page the turn is
    given (execution.HISTORY_PAGE, with room to spare for what arrives before the claim): the newest starts the turn
    and the page carries the others. Otherwise one digest in the bot's notice room names each room, how many notices
    wait there and how to read them; their own messages stay unread where they are."""
    rooms = {(j["conversation_id"], H._task_ref((H._json(j["refs"], {}) or {}).get("task"))) for j in jobs}
    if len(rooms) == 1 and _on_one_page(c, jobs):
        *earlier, last = jobs
        c.execute("UPDATE jobs SET held=0 WHERE id=?", (last["id"],))
        for job in earlier:
            c.execute("UPDATE jobs SET state='completed',held=0,wake_carrier=? WHERE id=?", (last["id"], job["id"]))
            H.mark_delivered(c, H.KEEPER, job["message_id"])
        return
    groups = {}
    for job in jobs:
        groups.setdefault(job["conversation_id"], []).append(job)
    lines = []
    # A Lead's day of news is one digest: it names at most DIGEST_ROOMS rooms, newest first, and counts the rest.
    place = {job["id"]: n for n, job in enumerate(jobs)}
    rooms = sorted(groups.items(), key=lambda kv: place[kv[1][-1]["id"]], reverse=True)
    for conversation, held in rooms[:DIGEST_ROOMS]:
        first, newest = H.message(c, held[0]["message_id"]) or {}, H.message(c, held[-1]["message_id"]) or {}
        task = H.task(c, H._task_ref((H._json(held[-1]["refs"], {}) or {}).get("task")) or "")
        what = classify(newest)[1] + (f" and {len(held) - 1} more" if len(held) > 1 else "")
        where = (
            f"a private task ({task['id']})"
            if task and H.task_private(c, task)
            else f"{task['title']} ({task['id']})"
            if task
            else str(newest.get("body") or "").splitlines()[0][:200]
            if newest.get("body")
            else "a notice"
        )
        lines.append(
            f"- {what}: {where}; read them with `tico conversation show {conversation}`"
            + (
                f" (the oldest is from {first.get('created_at')}: page back with --before to reach it)"
                if len(held) > 1
                else ""
            )
        )
    if len(rooms) > DIGEST_ROOMS:
        lines.append(f"- and updates in {len(rooms) - DIGEST_ROOMS} more rooms; your open tasks list them")
    from . import profiles

    row = c.execute(BS.ONE_SQL, (bot,)).fetchone()
    digest = profiles.digest_hour(H._json(row[0], {}) if row else {}) is not None
    held_by = "for your daily digest" if digest else f"for the {_duration(NOTICE_SECONDS)} notice wait"
    sent = H.say(
        c,
        H.KEEPER,
        "bot:" + bot,
        f"{len(jobs)} updates held {held_by}:\n"
        + "\n".join(lines)
        + "\nOpen each task that needs you; the rest is news.",
        kind="notice",
        refs={"wake_digest": len(jobs)},
    )
    held_until = max(
        c.execute("SELECT hold_until FROM jobs WHERE id=?", (j["id"],)).fetchone()[0] or H.now() for j in jobs
    )
    c.execute(
        "UPDATE jobs SET wake_class='digest',hold_until=? WHERE message_id=?",
        (held_until, (sent or {}).get("id")),
    )
    digest = c.execute("SELECT id FROM jobs WHERE message_id=?", ((sent or {}).get("id"),)).fetchone()
    for job in jobs:
        c.execute(
            "UPDATE jobs SET state='canceled',held=0,wake_carrier=? WHERE id=?",
            (digest[0] if digest else None, job["id"]),
        )
    H.event(c, H.KEEPER, "job.wake_digest", bot, {"jobs": [j["id"] for j in jobs[:DIGEST_IDS]], "count": len(jobs)})


def _on_one_page(c, jobs):
    """Whether the room page of the batch's newest turn still shows its oldest notice: at most half a page of
    messages from it on, and none of it before the room's session epoch (which the page starts from)."""
    from .execution import HISTORY_PAGE

    oldest = c.execute(
        "SELECT rowid,conversation_id,created_at FROM messages WHERE id=?", (jobs[0]["message_id"],)
    ).fetchone()
    epoch = c.execute(
        "SELECT updated_at FROM session_epochs WHERE conversation_id=?", (oldest["conversation_id"],)
    ).fetchone()
    if epoch and epoch["updated_at"] and oldest["created_at"] < epoch["updated_at"]:
        return False
    after = c.execute(
        "SELECT count(*) FROM messages WHERE conversation_id=? AND rowid>=?",
        (oldest["conversation_id"], oldest["rowid"]),
    ).fetchone()[0]
    return after <= HISTORY_PAGE // 2


def classify(message):
    """Why a message woke its bot, for the run record: (class, words). The hold never reads this; it reads only the
    keeper's refs, so these labels are a description, not a permission."""
    refs = H._json(message.get("refs"), {}) or {}
    sender, body, kind = str(message.get("sender_id") or ""), str(message.get("body") or ""), message.get("kind")
    if sender.startswith("human:"):
        return ("question" if kind in ("ask", "question") else "person"), "A person's message"
    if sender != H.KEEPER:
        return ("question" if kind in ("ask", "question") else "message"), f"A message from {H.actor_id(sender)}"
    if refs.get("wake_digest"):
        return "batched", "Batched updates"
    if refs.get("wake_batch") in BATCH_KINDS:
        return "batched", {
            "completion": "A finished request",
            "review": "Review comments",
            "ci": "A check result",
            "rule": "A batched update",
        }[refs["wake_batch"]]
    wake = refs.get("wake")
    if wake == "task_turn":
        return "assignment", "A task to work"
    if refs.get("sweep") or wake == "occurrence":
        return "sweep", "A scheduled routine run"
    if body.startswith(("New task from", "Open:")) or refs.get("auto_start"):
        return "assignment", "An assignment"
    if (
        body.startswith("All subtasks done")
        or refs.get("subtask")
        or refs.get("worktree_wait")
        or wake in ("watcher", "watcher-done")
        or refs.get("reconnected")
    ):
        return "dependency", "An awaited result"
    if body.startswith("Your task for") or refs.get("setup_held"):
        return "blocker", "A blocker"
    if refs.get("run_now"):
        return "person", "Run now"
    if wake in ("due", "snooze", "update"):
        return "scheduled", {"due": "A due date", "snooze": "A snooze ending", "update": "An update request"}[wake]
    return "notice", "A Tico notice"


def record(c, job, message, bot=None, forked=False):
    """The wake a turn starts from, kept on its attempt: what woke it, the bot's profile then (backend/profiles.py),
    until when it was held, and for a job the policy released (spec item 11, the run record): warm or cold, fork or
    fresh, and how long it waited after Due. The run's cache read and cache write tokens are its own columns
    (attempts.cached_tokens, attempts.cache_write_tokens). No preset is recorded: past runs keep theirs."""
    cls, words = classify(message)
    held_class = job["wake_class"]
    if held_class in (*HELD_CLASSES, "digest"):
        cls = "batched"
    elif held_class in POLICY_CLASSES:
        cls = "sweep" if held_class in (ROUTINE, "sweep") else held_class
        words = {"dream": "A dream"}.get(held_class, "A scheduled routine run")
    out = {"class": cls, "reason": words, "event_at": message.get("created_at")}
    if bot:
        from . import profiles

        out["profile"] = profiles.of_bot(c, bot)
        out["profile_label"] = profiles.SPEC[out["profile"]]["label"]
    if job["hold_until"]:
        out["held_until"] = job["hold_until"]
    released = H._json(job["released"], None) if job["released"] else None
    if held_class in POLICY_CLASSES and isinstance(released, dict):
        given = policy_of(job["release"])
        out["release"] = {
            "warm": released.get("warm") is True,
            "session": FORK if forked else FRESH,
            "waited_seconds": released.get("waited") or 0,
            "due": given["due"],
            "wait_seconds": given["wait"],
            "deadline": given["deadline"],
        }
    return out


def _seconds(start, end):
    a, b = H.parse_ts(start), H.parse_ts(end)
    return max(0.0, (b - a).total_seconds()) if a and b else None


def _pct(values_, q):
    if not values_:
        return None
    ordered = sorted(values_)
    return round(ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))], 1)


def measures(c, since, until=None, limit=20000):
    """Per preset, over jobs queued in [since, until): turns and tokens per accepted task (a task that ended done),
    pickup latency from the event to the turn that acts on it, and missed actions (an immediate wake not picked up within MISSED_AFTER_SECONDS). Bounded by `limit` jobs.

    A notice a batch carried (`wake_carrier`: the batch's newest, or its digest) is picked up when its carrier is, so
    its wait runs from its own event; the carrier's own message is not an event and adds no wait. A carrier's turns
    and tokens are split evenly over the tickets it carried, so each is counted once."""
    until = until or H.now()
    rows = c.execute(
        "SELECT j.id,j.state,j.wake_preset,j.wake_class,j.wake_carrier,m.created_at AS event_at,m.refs FROM jobs j "
        "JOIN messages m ON m.id=j.message_id "
        "WHERE j.created_at>=? AND j.created_at<? AND j.wake_preset IS NOT NULL ORDER BY j.created_at LIMIT ?",
        (since, until, limit),
    ).fetchall()
    runs = _runs(c, {r["id"] for r in rows} | {r["wake_carrier"] for r in rows if r["wake_carrier"]})
    carried = {}
    for row in rows:
        if row["wake_carrier"]:
            task = H._task_ref((H._json(row["refs"], {}) or {}).get("task"))
            carried.setdefault(row["wake_carrier"], set()).update([task] if task else [])
    out = {}
    for name in CHOICES:
        out[name] = {
            "preset": name,
            "label": LABELS[name],
            "jobs": 0,
            "turns": 0,
            "tokens": 0,
            "accepted_tickets": 0,
            "turns_per_ticket": None,
            "tokens_per_ticket": None,
            "pickup_seconds": {"immediate_p50": None, "immediate_p90": None, "held_p50": None, "held_p90": None},
            "missed_actions": 0,
            "held": 0,
            "duplicates": 0,
        }
    pickups, tickets = {n: {"immediate": [], "held": []} for n in CHOICES}, {n: {} for n in CHOICES}
    for row in rows:
        name = row["wake_preset"] if row["wake_preset"] in out else CURRENT
        bucket = out[name]
        bucket["jobs"] += 1
        held = row["wake_class"] in (*HELD_CLASSES, "digest", "sweep")
        bucket["held"] += 1 if held else 0
        if row["wake_class"] == "duplicate":
            bucket["duplicates"] += 1
            continue
        if row["wake_carrier"]:
            claimed = (runs.get(row["wake_carrier"]) or {}).get("claimed")
            if claimed and _seconds(row["event_at"], claimed) is not None:
                pickups[name]["held"].append(_seconds(row["event_at"], claimed))
            continue
        run = runs.get(row["id"]) or {}
        wait = _seconds(row["event_at"], run.get("claimed") or (until if row["state"] == "queued" else None))
        if run.get("claimed"):
            bucket["turns"] += run["turns"]
            bucket["tokens"] += run["tokens"]
            shared = sorted((carried.get(row["id"]) or set()) | ({run["task"]} if run["task"] else set()))
            for task in shared:
                seen = tickets[name].setdefault(task, [0, 0])
                seen[0], seen[1] = seen[0] + run["turns"] / len(shared), seen[1] + run["tokens"] / len(shared)
            if wait is not None and row["wake_class"] != "digest":
                pickups[name]["held" if held else "immediate"].append(wait)
        if not held and wait is not None and wait > MISSED_AFTER_SECONDS and row["state"] != "canceled":
            bucket["missed_actions"] += 1
    for name, bucket in out.items():
        accepted = [
            tid
            for tid in tickets[name]
            if (H.task(c, tid) or {}).get("status") in ("done", "closed") and (H.task(c, tid) or {}).get("done_at")
        ]
        bucket["accepted_tickets"] = len(accepted)
        if accepted:
            bucket["turns_per_ticket"] = round(sum(tickets[name][t][0] for t in accepted) / len(accepted), 2)
            bucket["tokens_per_ticket"] = round(sum(tickets[name][t][1] for t in accepted) / len(accepted))
        for kind in ("immediate", "held"):
            bucket["pickup_seconds"][kind + "_p50"] = _pct(pickups[name][kind], 0.5)
            bucket["pickup_seconds"][kind + "_p90"] = _pct(pickups[name][kind], 0.9)
    return {
        "since": since,
        "until": until,
        "missed_after_seconds": MISSED_AFTER_SECONDS,
        "presets": [out[name] for name in CHOICES],
    }


def _runs(c, job_ids):
    """Each job's first claim, and its turns' count, tokens and task."""
    out, ids = {}, sorted(i for i in job_ids if i)
    for n in range(0, len(ids), 500):
        part = ids[n : n + 500]
        for row in c.execute(
            "SELECT a.job_id,min(a.created_at) AS claimed,count(a.trigger) AS turns,"
            f"coalesce(sum({RUN_TOKENS}),0) AS tokens,"
            "max(a.task_id) AS task FROM attempts a "
            f"WHERE a.job_id IN ({','.join('?' * len(part))}) GROUP BY a.job_id",
            part,
        ):
            out[row["job_id"]] = dict(row)
    return out
