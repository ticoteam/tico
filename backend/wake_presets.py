"""Per-bot wake presets: how soon a bot's nonblocking notices and its enabled sweeps start a turn.

A bot's `config.wake_preset` is `token_saver` or `performance`. Existing bots without a preset,
including the former `current` setting, use Performance. New bots start as Doers.

Under a preset, two kinds of job wait at enqueue time (the `wake_preset_hold` trigger on jobs, so every
event source is held the same way): a nonblocking notice the server marks with `refs.wake_batch`
(BATCH_KINDS: a finished request, review comments, a failing check nobody armed a wait for) and a sweep
(a routine's scheduled run, a stall or status check). Only the keeper writes those refs, so nobody can make
an immediate wake wait by sending text that looks like one. Other news (a comment, a task's status, step or relation
moving, any notice no rule names) starts no job at all: the rules engine writes it as a note (backend/rules.py,
`default_note`), so nothing here holds it. A held batch never holds anything else back: a person's message starts
its turn at once.
The scheduler plans each held job's release (`tick`) and lets it go when it is due.

Under a preset a job whose message repeats another of the bot's in the same conversation (same sender, kind, text,
refs and reply) is dropped as a duplicate, so a duplicated event wakes the bot once: while the first is queued or
being worked, and for DUPLICATE_SECONDS after its turn ended. A notice a batch carried (its newest, or its digest) is
live while its carrier is, so a redelivery after the batch is let go still matches. A message from a person or a bot
repeats only when nothing else was said in between, so the same words sent again after an answer are a new request.
A preset grants nothing: it is one key in the bot's configuration and the hold reads nothing else.

A bot with a profile (backend/profiles.py) runs on the profile's numbers instead of its preset's: a window, a scope, a
sweep gap and, for a Lead, the hour of its daily digest. The preset kept on the bot's configuration and its jobs is then
only the label older readers and the hold trigger use (Token saver when the window is 30 minutes or more).

The values below are starting points to evaluate, not measured optima. TICO_WAKE_PRESETS overrides them
per install without a code change, e.g. `{"token_saver": {"batch_seconds": 900}}`.
"""

import json
import os
import statistics

from . import hubdb as H

# A run's tokens: the measured columns, or the legacy pair for a run finished before they were written.
RUN_TOKENS = ("CASE WHEN t.input_tokens IS NOT NULL THEN t.input_tokens+coalesce(t.cached_tokens,0)+coalesce(t.output_tokens,0) "
              "ELSE coalesce(t.tokens_in,0)+coalesce(t.tokens_out,0) END")

CURRENT = "current"
TOKEN_SAVER = "token_saver"
PERFORMANCE = "performance"
NEW_BOT_PRESET = PERFORMANCE      # a new bot is a Doer (backend/profiles.py), 15 min on the old label
# batch_seconds: how long a nonblocking notice waits. batch_scope "bot" spaces the bot's batches at least
# batch_seconds apart (at most one batched wake per window); "conversation" only coalesces notices for one
# task inside the window. sweep_seconds: the least time between two runs of the same enabled sweep.
PRESETS = {
    TOKEN_SAVER: {"label": "Token saver", "batch_seconds": 30 * 60, "batch_scope": "bot",
                  "sweep_seconds": 2 * 3600},
    PERFORMANCE: {"label": "Performance", "batch_seconds": 60, "batch_scope": "conversation",
                  "sweep_seconds": 15 * 60},
}
CHOICES = (CURRENT, TOKEN_SAVER, PERFORMANCE)  # Legacy runs remain identifiable in historical measures.
LABELS = {CURRENT: "Immediate (legacy)", TOKEN_SAVER: "Token saver", PERFORMANCE: "Performance"}
# An immediate wake not picked up within this long counts as a missed action in `measures`.
MISSED_AFTER_SECONDS = 5 * 60
# How long after a turn ends an identical event still counts as the same one (a retried webhook, a double send).
DUPLICATE_SECONDS = 2 * 60
# Nonblocking notices the keeper marks with refs.wake_batch.
BATCH_KINDS = ("completion", "review", "ci", "rule")
# Jobs held as `notice` by an earlier build's news hold, let go with the batch above.
NOTICE = "notice"
DIGEST_ROOMS = 40        # rooms a digest names; it counts the rest
DIGEST_IDS = 200         # job ids its event keeps
HELD_CLASSES = (*BATCH_KINDS, NOTICE)
# Sweeps: refs.wake on a keeper notice, or refs.sweep (a routine's scheduled run, by schedule id).
SWEEP_WAKES = ("stalled", "status_check", "occurrence")
IMMEDIATE = ("A person's message or request", "An assignment", "A direct question",
             "An awaited dependency resolving (a blocker done, an awaited review, a release)",
             "An actionable blocker", "A review, QA or Security request")
_BATCH_SQL = ("json_extract(m.refs,'$.wake_batch') IN (" + ",".join(f"'{k}'" for k in BATCH_KINDS) + ")")
_SWEEP_SQL = ("(json_extract(m.refs,'$.wake') IN (" + ",".join(f"'{k}'" for k in SWEEP_WAKES) + ") "
              "OR json_extract(m.refs,'$.sweep') IS NOT NULL)")
_PRESET_SQL = "(SELECT wake_preset FROM jobs WHERE id=NEW.id) IN ('" + TOKEN_SAVER + "','" + PERFORMANCE + "')"
# The same event as an earlier job's message: same room, sender, kind, text, refs and reply. Only the message's own
# fields: a job's hold flag changes when its batch is let go.
_SAME_EVENT = ("om.from_actor IS m.from_actor AND om.kind IS m.kind AND om.body=m.body "
               "AND om.refs IS m.refs AND om.in_reply_to IS m.in_reply_to")
# A keeper notice repeats any earlier one in its room; a person's or a bot's message repeats only the room's message
# just before it (nothing said in between). Two lookups rather than one OR, so each seeks: the keeper's walks only its
# room's earlier messages, and the other reads that one previous message by rowid, found once per insert.
_EARLIER = (f"m.from_actor='{H.KEEPER}' AND om.conversation_id=m.conversation_id AND om.rowid<m.rowid",
            "om.rowid=(SELECT max(x.rowid) FROM messages x WHERE x.conversation_id=m.conversation_id AND x.rowid<m.rowid)")
_DUPLICATE = " OR ".join(
    f"""EXISTS(SELECT 1 FROM messages m JOIN messages om ON {earlier}
   JOIN jobs o ON o.message_id=om.id AND o.bot=NEW.bot AND o.id<>NEW.id
   AND (o.state<>'cancelled' OR o.wake_carrier IS NOT NULL)
   LEFT JOIN jobs cj ON cj.id=o.wake_carrier LEFT JOIN attempts a ON a.id=coalesce(cj.attempt_id,o.attempt_id)
   WHERE m.id=NEW.message_id AND {_SAME_EVENT} AND (coalesce(cj.state,o.state) IN ('queued','leased','running','input')
   OR a.finished_at>=strftime('%Y-%m-%dT%H:%M:%fZ',m.created_at,'-{DUPLICATE_SECONDS} seconds')))""" for earlier in _EARLIER)
HOLD_TRIGGER = f"""CREATE TRIGGER wake_preset_hold AFTER INSERT ON jobs WHEN NEW.state='queued' BEGIN
 UPDATE jobs SET wake_preset=CASE WHEN (SELECT json_extract(config,'$.wake_preset') FROM bot_config
   WHERE bot=NEW.bot)='{TOKEN_SAVER}' THEN '{TOKEN_SAVER}' ELSE '{PERFORMANCE}' END WHERE id=NEW.id;
 UPDATE jobs SET held=1,wake_class=(SELECT CASE WHEN {_BATCH_SQL} THEN json_extract(m.refs,'$.wake_batch')
   ELSE 'sweep' END FROM messages m WHERE m.id=NEW.message_id)
   WHERE id=NEW.id AND {_PRESET_SQL} AND EXISTS(SELECT 1 FROM messages m WHERE m.id=NEW.message_id
   AND m.from_actor='{H.KEEPER}' AND coalesce(json_extract(m.refs,'$.wake_immediate'),0)<>1
   AND ({_BATCH_SQL} OR {_SWEEP_SQL}));
 UPDATE jobs SET state='cancelled',held=0,wake_class='duplicate' WHERE id=NEW.id AND {_PRESET_SQL}
   AND ({_DUPLICATE});
END"""


def ensure(c):
    """The hold's columns and trigger. Unversioned like the other job columns: an older release never reads them,
    and the trigger is rebuilt whenever its text changes."""
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
    H.add_column(c, "attempts", "wake", "TEXT")
    c.execute("CREATE INDEX IF NOT EXISTS jobs_held ON jobs(bot,hold_until) WHERE held=1")
    # The few held jobs still queued, in claim order: what tick reads every pass under the write lock (HELD_*_SQL).
    c.execute("CREATE INDEX IF NOT EXISTS jobs_held_queued ON jobs(created_at, id) WHERE held=1 AND state='queued'")
    # The duplicate check seeks a room's messages before the new one by rowid (the index's implicit suffix), not a walk
    # through every earlier message from the same sender in every room.
    c.execute("CREATE INDEX IF NOT EXISTS messages_room_rowid ON messages(conversation_id)")
    stored = c.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name='wake_preset_hold'").fetchone()
    if not stored or stored[0] != HOLD_TRIGGER:
        c.execute("DROP TRIGGER IF EXISTS wake_preset_hold")
        c.execute(HOLD_TRIGGER)

    c.execute("UPDATE bot_config SET config=json_set(config,'$.wake_preset',?),revision=revision+1 "
              "WHERE json_extract(config,'$.wake_preset') IS NULL "
              "OR json_extract(config,'$.wake_preset')=?", (PERFORMANCE, CURRENT))


def values():
    """PRESETS with this install's TICO_WAKE_PRESETS overrides: whole seconds from 0 to a week, and a known scope."""
    out = {name: dict(spec) for name, spec in PRESETS.items()}
    try:
        given = json.loads(os.environ.get("TICO_WAKE_PRESETS") or "{}")
    except ValueError:
        given = {}
    for name, spec in (given.items() if isinstance(given, dict) else ()):
        if name not in out or not isinstance(spec, dict):
            continue
        for key in ("batch_seconds", "sweep_seconds"):
            if isinstance(spec.get(key), int) and not isinstance(spec.get(key), bool) and 0 <= spec[key] <= 7 * 86400:
                out[name][key] = spec[key]
        if spec.get("batch_scope") in ("bot", "conversation"):
            out[name]["batch_scope"] = spec["batch_scope"]
    return out


def preset_of(config):
    """Normalize older configurations to Performance; Token saver is always an explicit choice."""
    name = (config or {}).get("wake_preset")
    return name if name in PRESETS else PERFORMANCE


def effective(config):
    """What the bot's settings and the API show: the preset and the values it runs on. A profile's own numbers
    stand over the preset's, which then only names the closest of the two."""
    from . import profiles
    name = preset_of(config)
    spec = dict(values().get(name))
    own = profiles.numbers(config)
    if own:
        spec.update(own)
    batch = _duration(spec["batch_seconds"])
    summary = (f"{spec['label']}: finished requests and a person's review comments wait up to {batch}"
               + (" (at most one such wake per " + batch + ")" if spec["batch_scope"] == "bot" else "")
               + f"; an enabled routine or stall check runs at most every {_duration(spec['sweep_seconds'])}. "
               "People, assignments, questions, awaited results, blockers and review requests wake it at once.")
    if own and own.get("digest_hour") is not None:
        summary = (f"The rest of the news arrives once a day, at {own['digest_hour']:02d}:00 team time. "
                   "People, escalations and approvals wake it at once.")
    return {"preset": name, "label": spec["label"], "chosen": True, "batch_seconds": spec["batch_seconds"],
            "batch_scope": spec["batch_scope"], "sweep_seconds": spec["sweep_seconds"],
            "digest_hour": spec.get("digest_hour"), "profile": profiles.name_of(config), "immediate": list(IMMEDIATE),
            "summary": summary}


def numbers_for(c, bot, label, cache=None):
    """The window, scope, sweep gap and digest hour a held job of this bot is planned on: its profile's when it has
    one, else the preset its job carries (None when that is unknown)."""
    from . import profiles
    if cache is not None and bot in cache:
        own = cache[bot]
    else:
        row = c.execute("SELECT config FROM bot_config WHERE bot=?", (bot,)).fetchone()
        own = profiles.numbers(H._json(row[0], {}) if row else {})
        if cache is not None:
            cache[bot] = own
    return {**values()[label], **own} if own and label in PRESETS else (own or values().get(label))


def _duration(seconds):
    if seconds % 3600 == 0 and seconds >= 3600:
        return f"{seconds // 3600} h"
    if seconds % 60 == 0 and seconds >= 60:
        return f"{seconds // 60} min"
    return f"{seconds} s"


def sweep_key(refs):
    return str(refs.get("sweep") or refs.get("task") or "")


def release_quota_resets(c, at=None):
    """A quota retry keeps its job and becomes claimable at the reset even between scheduler ticks."""
    c.execute("UPDATE jobs SET held=0 WHERE held=1 AND state='queued' AND wake_class='quota-reset' "
              "AND hold_reason IS NULL AND hold_until<=?", (at or H.now(),))


# Read on every scheduler tick under the write lock. INDEXED BY jobs_held_queued: the planner otherwise scans
# messages and seeks each job, because the stale sqlite_stat1 row for messages (16k rows when analysed, 72k
# now) makes that look cheapest. 40-44 ms a tick on TIDY v0.5.1, 480 ticks an hour.
HELD_NEW_SQL = ("SELECT j.id,j.bot,j.wake_preset,j.wake_class,j.created_at,m.conversation_id,m.refs "
                "FROM jobs j INDEXED BY jobs_held_queued JOIN messages m ON m.id=j.message_id "
                "WHERE j.held=1 AND j.hold_until IS NULL AND j.hold_reason IS NULL AND j.state='queued' "
                "ORDER BY j.created_at,j.id")
HELD_DUE_SQL = ("SELECT j.id,j.bot,j.wake_preset,j.wake_class,j.message_id,m.conversation_id,m.refs "
                "FROM jobs j INDEXED BY jobs_held_queued JOIN messages m ON m.id=j.message_id "
                "WHERE j.held=1 AND j.state='queued' AND j.hold_until IS NOT NULL AND j.hold_until<=? "
                "AND j.hold_reason IS NULL ORDER BY j.created_at,j.id")


def tick(c, at=None):
    """Plan each newly held job's release, then let go of the ones that are due. Runs in the scheduler's write
    transaction every tick; a release is an UPDATE of `held`, which tells the bot's computer there is work."""
    at = at or H.now()
    cache = {}
    for job in c.execute(HELD_NEW_SQL).fetchall():
        values_of = numbers_for(c, job["bot"], job["wake_preset"], cache)
        if not values_of:
            c.execute("UPDATE jobs SET held=0 WHERE id=?", (job["id"],))
            continue
        if job["wake_class"] == "sweep":
            due = _sweep_slot(c, job, values_of["sweep_seconds"])
            if due is None:
                c.execute("UPDATE jobs SET state='cancelled',held=0 WHERE id=?", (job["id"],))
                H.event(c, H.KEEPER, "job.wake_merged", job["id"], {"bot": job["bot"]})
                continue
        else:
            due = _batch_slot(c, job, values_of)
        c.execute("UPDATE jobs SET hold_until=? WHERE id=?", (due, job["id"]))
    groups = {}
    # A run a claim holds (backend/claim_holds.py) has a wait of its own, and only its claim releases it.
    for job in c.execute(HELD_DUE_SQL, (at,)).fetchall():
        if job["wake_class"] in ("sweep", "quota-reset"):
            # A quota retry is the same job, not news to batch, fold into another request or turn into a digest.
            c.execute("UPDATE jobs SET held=0 WHERE id=?", (job["id"],))
            continue
        scope = (numbers_for(c, job["bot"], job["wake_preset"], cache) or {}).get("batch_scope")
        groups.setdefault((job["bot"], job["conversation_id"] if scope == "conversation" else ""), []).append(job)
    for (bot, _), jobs in groups.items():
        _release(c, bot, jobs)


def _sweep_slot(c, job, gap):
    """When this run of a sweep may start: now, or `gap` after the same sweep's last run started (its first claim,
    which a busy computer can put well after its planned release). None when the same sweep is still queued, held or
    let go, since this run adds nothing that one will not do."""
    key, since = sweep_key(H._json(job["refs"], {}) or {}), H.shift(job["created_at"], seconds=-gap)
    last = None
    for row in c.execute("SELECT j.state,j.hold_until,m.refs,"
                         "(SELECT min(a.created_at) FROM attempts a WHERE a.job_id=j.id) AS started_at "
                         "FROM jobs j JOIN messages m ON m.id=j.message_id "
                         "WHERE j.bot=? AND j.wake_class='sweep' AND j.id<>? AND j.state<>'cancelled' "
                         "AND (j.state IN ('queued','leased','running','input') OR j.hold_until>? "
                         "OR EXISTS(SELECT 1 FROM attempts a WHERE a.job_id=j.id AND a.created_at>?))",
                         (job["bot"], job["id"], since, since)):
        if sweep_key(H._json(row["refs"], {}) or {}) != key:
            continue
        if row["state"] == "queued":
            return None
        last = max(last or "", row["started_at"] or row["hold_until"])
    return max(job["created_at"], H.shift(last, seconds=gap)) if last else job["created_at"]


def _batch_slot(c, job, values_of):
    """When a nonblocking notice may wake the bot: with the batch already waiting (the bot's, or this conversation's
    under a per-conversation scope), else a window after it arrived, and under a per-bot scope never sooner than a
    window after the bot's last batch went out."""
    gap, per_bot = values_of["batch_seconds"], values_of["batch_scope"] == "bot"
    hour = values_of.get("digest_hour")
    kinds = ",".join(f"'{k}'" for k in HELD_CLASSES)
    waiting = c.execute("SELECT min(j.hold_until) FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.bot=? "
                        f"AND j.held=1 AND j.state='queued' AND j.hold_until IS NOT NULL AND j.wake_class IN ({kinds}) "
                        "AND j.hold_reason IS NULL AND (? OR m.conversation_id=?)",
                        (job["bot"], per_bot, job["conversation_id"])).fetchone()[0]
    if waiting:
        return waiting
    if hour is not None:
        return digest_time(job["created_at"], hour)
    due = H.shift(job["created_at"], seconds=gap)
    if per_bot:
        last = c.execute(f"SELECT max(hold_until) FROM jobs WHERE bot=? AND wake_class IN ({kinds},'digest') "
                         "AND held=0 AND hold_until>? AND hold_reason IS NULL",
                         (job["bot"], H.shift(job["created_at"], seconds=-gap))).fetchone()[0]
        if last:
            due = max(due, H.shift(last, seconds=gap))
    return due


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
    rooms = {(j["conversation_id"], (H._json(j["refs"], {}) or {}).get("task")) for j in jobs}
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
        task = H.task(c, (H._json(held[-1]["refs"], {}) or {}).get("task") or "")
        what = classify(newest)[1] + (f" and {len(held) - 1} more" if len(held) > 1 else "")
        where = (f"a private task ({task['id']})" if task and H.task_private(c, task)
                 else f"{task['title']} ({task['id']})" if task
                 else str(newest.get("body") or "").splitlines()[0][:200] if newest.get("body") else "a notice")
        lines.append(f"- {what}: {where}; read them with `hub conversation show {conversation}`"
                     + (f" (the oldest is from {first.get('created_at')}: page back with --before to reach it)"
                        if len(held) > 1 else ""))
    if len(rooms) > DIGEST_ROOMS:
        lines.append(f"- and updates in {len(rooms) - DIGEST_ROOMS} more rooms; your open tasks list them")
    from . import profiles
    row = c.execute("SELECT config FROM bot_config WHERE bot=?", (bot,)).fetchone()
    mine = profiles.name_of(H._json(row[0], {}) if row else {})
    held_by = (f"your {profiles.SPEC[mine]['label']} profile" if mine
               else f"your {LABELS.get(jobs[0]['wake_preset'], '')} wake preset")
    sent = H.say(c, H.KEEPER, "bot:" + bot, f"{len(jobs)} updates held by {held_by}:\n"
                 + "\n".join(lines) + "\nOpen each task that needs you; the rest is news.", kind="notice",
                 refs={"wake_digest": len(jobs)})
    held_until = max(c.execute("SELECT hold_until FROM jobs WHERE id=?", (j["id"],)).fetchone()[0] or H.now() for j in jobs)
    c.execute("UPDATE jobs SET wake_class='digest',wake_preset=?,hold_until=? WHERE message_id=?",
              (jobs[0]["wake_preset"], held_until, (sent or {}).get("id")))
    digest = c.execute("SELECT id FROM jobs WHERE message_id=?", ((sent or {}).get("id"),)).fetchone()
    for job in jobs:
        c.execute("UPDATE jobs SET state='cancelled',held=0,wake_carrier=? WHERE id=?",
                  (digest[0] if digest else None, job["id"]))
    H.event(c, H.KEEPER, "job.wake_digest", bot, {"jobs": [j["id"] for j in jobs[:DIGEST_IDS]], "count": len(jobs)})


def _on_one_page(c, jobs):
    """Whether the room page of the batch's newest turn still shows its oldest notice: at most half a page of
    messages from it on, and none of it before the room's session epoch (which the page starts from)."""
    from .execution import HISTORY_PAGE
    oldest = c.execute("SELECT rowid,conversation_id,created_at FROM messages WHERE id=?", (jobs[0]["message_id"],)).fetchone()
    epoch = c.execute("SELECT updated_at FROM session_epochs WHERE conversation_id=?", (oldest["conversation_id"],)).fetchone()
    if epoch and epoch["updated_at"] and oldest["created_at"] < epoch["updated_at"]:
        return False
    after = c.execute("SELECT count(*) FROM messages WHERE conversation_id=? AND rowid>=?",
                      (oldest["conversation_id"], oldest["rowid"])).fetchone()[0]
    return after <= HISTORY_PAGE // 2


def replan(c, bot):
    """A preset change: the bot's held jobs are planned again on the new values."""
    config = c.execute("SELECT config FROM bot_config WHERE bot=?", (bot,)).fetchone()
    name = preset_of(H._json(config[0], {}) if config else {})
    c.execute("UPDATE jobs SET hold_until=NULL,wake_preset=? WHERE bot=? AND held=1 AND state='queued' "
              "AND coalesce(wake_class,'')<>'quota-reset' AND hold_reason IS NULL", (name, bot))


def classify(message):
    """Why a message woke its bot, for the run record: (class, words). The hold never reads this; it reads only the
    keeper's refs, so these labels are a description, not a permission."""
    refs = message.get("refs") or H._json(message.get("refs"), {}) or {}
    sender, body, kind = str(message.get("from_actor") or ""), str(message.get("body") or ""), message.get("kind")
    if sender.startswith("human:"):
        return ("question" if kind in ("ask", "question") else "person"), "A person's message"
    if sender != H.KEEPER:
        return ("question" if kind in ("ask", "question") else "message"), f"A message from {H.actor_id(sender)}"
    if refs.get("wake_digest"):
        return "batched", "Batched updates"
    if refs.get("wake_batch") in BATCH_KINDS:
        return "batched", {"completion": "A finished request", "review": "Review comments",
                           "ci": "A check result", "rule": "A batched update"}[refs["wake_batch"]]
    wake = refs.get("wake")
    if wake in ("stalled", "status_check") or refs.get("sweep") or wake == "occurrence":
        return "sweep", ("A scheduled routine run" if refs.get("sweep") or wake == "occurrence"
                         else "A stall check" if wake == "stalled" else "A status check")
    if body.startswith(("New task from", "Open:", "Next-run task:")) or refs.get("auto_start"):
        return "assignment", "An assignment"
    if body.startswith(("Unblocked:", "All subtasks done")) or refs.get("subtask") or refs.get("worktree_wait") \
            or wake in ("watcher", "watcher-done", "resume") or refs.get("approval") or refs.get("milestone") \
            or refs.get("reconnected"):
        return "dependency", "An awaited result"
    if body.startswith(("Stuck:", "Your task for")) or refs.get("setup_held") or wake == "stuck_cause":
        return "blocker", "A blocker"
    if body.startswith("Ready for review:"):
        return "gate", "A review request"
    if refs.get("run_now"):
        return "person", "Run now"
    if wake in ("due", "snooze", "next-run", "update", "restate_ask", "deferred"):
        return "scheduled", {"due": "A due date", "snooze": "A snooze ending", "update": "An update request",
                             "restate_ask": "An unanswered ask", "deferred": "A defer deadline"}.get(wake, "A deferred task")
    return "notice", "A Tico notice"


def record(c, job, message, bot=None):
    """The wake a turn starts from, kept on its attempt: what woke it, the preset in force, the bot's profile then
    (backend/profiles.py; its settings and the run show the profile, the preset stays for older clients) and how long
    it waited."""
    cls, words = classify(message)
    if job["wake_class"] in (*HELD_CLASSES, "digest", "sweep"):
        cls = "sweep" if job["wake_class"] == "sweep" else "batched"
        words = "A scheduled sweep" if cls == "sweep" and words == "A Tico notice" else words
    name = job["wake_preset"] or CURRENT
    out = {"class": cls, "reason": words, "preset": name, "preset_label": LABELS.get(name, LABELS[CURRENT]),
           "event_at": message.get("created_at")}
    if bot:
        from . import profiles
        out["profile"] = profiles.of_bot(c, bot)
        out["profile_label"] = profiles.SPEC[out["profile"]]["label"]
    if job["hold_until"]:
        out["held_until"] = job["hold_until"]
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
    """Per preset, over jobs queued in [since, until): turns and tokens per accepted ticket (a task that ended done),
    pickup latency from the event to the turn that acts on it, the time those tickets spent blocked or waiting, and
    missed actions (an immediate wake not picked up within MISSED_AFTER_SECONDS). Bounded by `limit` jobs.

    A notice a batch carried (`wake_carrier`: the batch's newest, or its digest) is picked up when its carrier is, so
    its wait runs from its own event; the carrier's own message is not an event and adds no wait. A carrier's turns
    and tokens are split evenly over the tickets it carried, so each is counted once."""
    until = until or H.now()
    rows = c.execute(
        "SELECT j.id,j.state,j.wake_preset,j.wake_class,j.wake_carrier,m.created_at AS event_at,m.refs FROM jobs j "
        "JOIN messages m ON m.id=j.message_id "
        "WHERE j.created_at>=? AND j.created_at<? AND j.wake_preset IS NOT NULL ORDER BY j.created_at LIMIT ?",
        (since, until, limit)).fetchall()
    runs = _runs(c, {r["id"] for r in rows} | {r["wake_carrier"] for r in rows if r["wake_carrier"]})
    carried = {}
    for row in rows:
        if row["wake_carrier"]:
            task = (H._json(row["refs"], {}) or {}).get("task")
            carried.setdefault(row["wake_carrier"], set()).update([task] if task else [])
    out = {}
    for name in CHOICES:
        out[name] = {"preset": name, "label": LABELS[name], "jobs": 0, "turns": 0, "tokens": 0,
                     "accepted_tickets": 0, "turns_per_ticket": None, "tokens_per_ticket": None,
                     "pickup_seconds": {"immediate_p50": None, "immediate_p90": None, "held_p50": None,
                                        "held_p90": None},
                     "blocked_seconds_per_ticket": None, "missed_actions": 0, "held": 0, "duplicates": 0}
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
        if not held and wait is not None and wait > MISSED_AFTER_SECONDS and row["state"] != "cancelled":
            bucket["missed_actions"] += 1
    for name, bucket in out.items():
        accepted = [tid for tid in tickets[name] if (H.task(c, tid) or {}).get("status") in ("done", "closed")
                    and (H.task(c, tid) or {}).get("done_at")]
        bucket["accepted_tickets"] = len(accepted)
        if accepted:
            bucket["turns_per_ticket"] = round(sum(tickets[name][t][0] for t in accepted) / len(accepted), 2)
            bucket["tokens_per_ticket"] = round(sum(tickets[name][t][1] for t in accepted) / len(accepted))
            bucket["blocked_seconds_per_ticket"] = round(statistics.mean(_blocked(c, t) for t in accepted))
        for kind in ("immediate", "held"):
            bucket["pickup_seconds"][kind + "_p50"] = _pct(pickups[name][kind], 0.5)
            bucket["pickup_seconds"][kind + "_p90"] = _pct(pickups[name][kind], 0.9)
    return {"since": since, "until": until, "missed_after_seconds": MISSED_AFTER_SECONDS,
            "presets": [out[name] for name in CHOICES]}


def _runs(c, job_ids):
    """Each job's first claim, and its turns' count, tokens and task."""
    out, ids = {}, sorted(i for i in job_ids if i)
    for n in range(0, len(ids), 500):
        part = ids[n:n + 500]
        for row in c.execute("SELECT a.job_id,min(a.created_at) AS claimed,count(t.id) AS turns,"
                             f"coalesce(sum({RUN_TOKENS}),0) AS tokens,"
                             "max(t.task_id) AS task FROM attempts a LEFT JOIN turns t ON t.id=a.id "
                             f"WHERE a.job_id IN ({','.join('?' * len(part))}) GROUP BY a.job_id", part):
            out[row["job_id"]] = dict(row)
    return out


def _blocked(c, task_id):
    """Seconds the task spent blocked or waiting, from its status history."""
    total, entered = 0.0, None
    for row in c.execute("SELECT ts,new FROM task_events WHERE task_id=? AND field='status' ORDER BY ts,rowid",
                         (task_id,)):
        if row["new"] in ("blocked", "waiting"):
            entered = entered or row["ts"]
        elif entered:
            total += _seconds(entered, row["ts"]) or 0
            entered = None
    return total
