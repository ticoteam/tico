"""Per-bot wake presets: how soon a bot's nonblocking notices and its enabled sweeps start a turn.

A bot's `config_json.wake_preset` is `token_saver`, `performance` or absent. Absent is "current
behaviour": every job is claimable the moment it is queued, as before presets existed, and a bot made
before them keeps it until someone picks one. A new bot starts on NEW_BOT_PRESET.

Under a preset, two kinds of job wait at enqueue time (the `wake_preset_hold` trigger on jobs, so every
event source is held the same way): a nonblocking notice the server marks with `refs.wake_batch`
(BATCH_KINDS: a finished request, review comments, a failing check nobody armed a wait for) and a sweep
(a routine's scheduled run, a stall or status check). Only the keeper writes those refs, so nobody can make
an immediate wake wait by sending text that looks like one. Everything else — a person's message, an
assignment, a question, an awaited dependency resolving, a blocker, a review or QA request — is never held.
The scheduler plans each held job's release (`tick`) and lets it go when it is due.

Under a preset a queued job that repeats one still queued in the same conversation (same sender, kind and
text) is dropped as a duplicate, so a duplicated event wakes the bot once. A preset grants nothing: it is one
key in the bot's configuration and the hold reads nothing else.

The values below are starting points to evaluate, not measured optima. TICO_WAKE_PRESETS overrides them
per install without a code change, e.g. `{"token_saver": {"batch_seconds": 900}}`.
"""

import json
import os
import statistics

from . import hubdb as H

CURRENT = "current"
TOKEN_SAVER = "token_saver"
PERFORMANCE = "performance"
NEW_BOT_PRESET = TOKEN_SAVER
# batch_seconds: how long a nonblocking notice waits. batch_scope "bot" spaces the bot's batches at least
# batch_seconds apart (at most one batched wake per window); "conversation" only coalesces notices for one
# task inside the window. sweep_seconds: the least time between two runs of the same enabled sweep.
PRESETS = {
    TOKEN_SAVER: {"label": "Token saver", "batch_seconds": 30 * 60, "batch_scope": "bot",
                  "sweep_seconds": 2 * 3600},
    PERFORMANCE: {"label": "Performance", "batch_seconds": 60, "batch_scope": "conversation",
                  "sweep_seconds": 15 * 60},
}
CHOICES = (CURRENT, TOKEN_SAVER, PERFORMANCE)
LABELS = {CURRENT: "Current behaviour", TOKEN_SAVER: "Token saver", PERFORMANCE: "Performance"}
# An immediate wake not picked up within this long counts as a missed action in `measures`.
MISSED_AFTER_SECONDS = 5 * 60
# Nonblocking notices the keeper marks with refs.wake_batch.
BATCH_KINDS = ("completion", "review", "ci")
# Sweeps: refs.wake on a keeper notice, or refs.sweep (a routine's scheduled run, by schedule id).
SWEEP_WAKES = ("stalled", "status_check", "occurrence")
IMMEDIATE = ("A person's message or request", "An assignment", "A direct question",
             "An awaited dependency resolving (a blocker done, an awaited review, a release)",
             "An actionable blocker", "A review, QA or Security request")
_BATCH_SQL = ("json_extract(m.refs_json,'$.wake_batch') IN (" + ",".join(f"'{k}'" for k in BATCH_KINDS) + ")")
_SWEEP_SQL = ("(json_extract(m.refs_json,'$.wake') IN (" + ",".join(f"'{k}'" for k in SWEEP_WAKES) + ") "
              "OR json_extract(m.refs_json,'$.sweep') IS NOT NULL)")
_PRESET_SQL = "(SELECT wake_preset FROM jobs WHERE id=NEW.id) IN ('" + TOKEN_SAVER + "','" + PERFORMANCE + "')"
HOLD_TRIGGER = f"""CREATE TRIGGER wake_preset_hold AFTER INSERT ON jobs WHEN NEW.state='queued' BEGIN
 UPDATE jobs SET wake_preset=coalesce((SELECT json_extract(config_json,'$.wake_preset') FROM bot_config
   WHERE bot=NEW.bot),'{CURRENT}') WHERE id=NEW.id;
 UPDATE jobs SET held=1,wake_class=(SELECT CASE WHEN {_BATCH_SQL} THEN json_extract(m.refs_json,'$.wake_batch')
   ELSE 'sweep' END FROM messages m WHERE m.id=NEW.message_id)
   WHERE id=NEW.id AND {_PRESET_SQL} AND EXISTS(SELECT 1 FROM messages m WHERE m.id=NEW.message_id
   AND m.from_actor='{H.KEEPER}' AND ({_BATCH_SQL} OR {_SWEEP_SQL}));
 UPDATE jobs SET state='cancelled',held=0,wake_class='duplicate' WHERE id=NEW.id AND {_PRESET_SQL}
   AND EXISTS(SELECT 1 FROM messages m JOIN jobs o ON o.bot=NEW.bot AND o.state='queued' AND o.id<>NEW.id
   JOIN messages om ON om.id=o.message_id WHERE m.id=NEW.message_id AND om.conversation_id=m.conversation_id
   AND om.from_actor IS m.from_actor AND om.kind IS m.kind AND om.body=m.body
   AND o.held=(SELECT held FROM jobs WHERE id=NEW.id));
END"""


def ensure(c):
    """The hold's columns and trigger. Unversioned like the other job columns: an older release never reads them,
    and the trigger is rebuilt whenever its text changes."""
    H.add_column(c, "jobs", "wake_preset", "TEXT")
    H.add_column(c, "jobs", "wake_class", "TEXT")
    H.add_column(c, "jobs", "held", "INTEGER NOT NULL DEFAULT 0")
    H.add_column(c, "jobs", "hold_until", "TEXT")
    H.add_column(c, "attempts", "wake_json", "TEXT")
    c.execute("CREATE INDEX IF NOT EXISTS jobs_held ON jobs(bot,hold_until) WHERE held=1")
    stored = c.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name='wake_preset_hold'").fetchone()
    if not stored or stored[0] != HOLD_TRIGGER:
        c.execute("DROP TRIGGER IF EXISTS wake_preset_hold")
        c.execute(HOLD_TRIGGER)


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
    """The preset a bot's configuration names; anything else is current behaviour."""
    name = (config or {}).get("wake_preset")
    return name if name in PRESETS else CURRENT


def effective(config):
    """What the bot's settings and the API show: the preset and the values it runs on."""
    name = preset_of(config)
    spec = values().get(name)
    if not spec:
        return {"preset": CURRENT, "label": LABELS[CURRENT], "chosen": False,
                "batch_seconds": None, "batch_scope": None, "sweep_seconds": None, "immediate": list(IMMEDIATE),
                "summary": "Current behaviour: every wake starts a turn as soon as it arrives, as before presets."}
    batch = _duration(spec["batch_seconds"])
    summary = (f"{spec['label']}: finished requests, review comments and check results wait up to {batch}"
               + (" (at most one such wake per " + batch + ")" if spec["batch_scope"] == "bot" else "")
               + f"; an enabled routine or stall check runs at most every {_duration(spec['sweep_seconds'])}. "
               "People, assignments, questions, awaited results, blockers and review requests wake it at once.")
    return {"preset": name, "label": spec["label"], "chosen": True, "batch_seconds": spec["batch_seconds"],
            "batch_scope": spec["batch_scope"], "sweep_seconds": spec["sweep_seconds"], "immediate": list(IMMEDIATE),
            "summary": summary}


def _duration(seconds):
    if seconds % 3600 == 0 and seconds >= 3600:
        return f"{seconds // 3600} h"
    if seconds % 60 == 0 and seconds >= 60:
        return f"{seconds // 60} min"
    return f"{seconds} s"


def sweep_key(refs):
    return str(refs.get("sweep") or refs.get("task") or "")


def tick(c, at=None):
    """Plan each newly held job's release, then let go of the ones that are due. Runs in the scheduler's write
    transaction every tick; a release is an UPDATE of `held`, which tells the bot's computer there is work."""
    at = at or H.now()
    spec = values()
    for job in c.execute("SELECT j.id,j.bot,j.wake_preset,j.wake_class,j.created,m.conversation_id,m.refs_json "
                         "FROM jobs j JOIN messages m ON m.id=j.message_id "
                         "WHERE j.held=1 AND j.hold_until IS NULL AND j.state='queued' ORDER BY j.created,j.id").fetchall():
        values_of = spec.get(job["wake_preset"])
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
    for job in c.execute("SELECT j.id,j.bot,j.wake_preset,j.wake_class,j.message_id,m.conversation_id,m.refs_json "
                         "FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.held=1 AND j.state='queued' "
                         "AND j.hold_until IS NOT NULL AND j.hold_until<=? ORDER BY j.created,j.id", (at,)).fetchall():
        if job["wake_class"] == "sweep":
            c.execute("UPDATE jobs SET held=0 WHERE id=?", (job["id"],))
            continue
        scope = (spec.get(job["wake_preset"]) or {}).get("batch_scope")
        groups.setdefault((job["bot"], job["conversation_id"] if scope == "conversation" else ""), []).append(job)
    for (bot, _), jobs in groups.items():
        _release(c, bot, jobs)


def _sweep_slot(c, job, gap):
    """When this run of a sweep may start: now, or `gap` after the same sweep's last run. None when the same sweep
    already waits, since this run adds nothing that one will not do."""
    key = sweep_key(H._json(job["refs_json"], {}) or {})
    last = None
    for row in c.execute("SELECT j.state,j.held,j.hold_until,m.refs_json FROM jobs j JOIN messages m ON m.id=j.message_id "
                         "WHERE j.bot=? AND j.wake_class='sweep' AND j.id<>? AND j.hold_until>?",
                         (job["bot"], job["id"], H.shift(job["created"], seconds=-gap))):
        if sweep_key(H._json(row["refs_json"], {}) or {}) != key:
            continue
        if row["state"] == "queued" and row["held"]:
            return None
        last = max(last or "", row["hold_until"])
    return max(job["created"], H.shift(last, seconds=gap)) if last else job["created"]


def _batch_slot(c, job, values_of):
    """When a nonblocking notice may wake the bot: with the batch already waiting (the bot's, or this conversation's
    under a per-conversation scope), else a window after it arrived, and under a per-bot scope never sooner than a
    window after the bot's last batch went out."""
    gap, per_bot = values_of["batch_seconds"], values_of["batch_scope"] == "bot"
    kinds = ",".join(f"'{k}'" for k in BATCH_KINDS)
    waiting = c.execute("SELECT min(j.hold_until) FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.bot=? "
                        f"AND j.held=1 AND j.state='queued' AND j.hold_until IS NOT NULL AND j.wake_class IN ({kinds}) "
                        "AND (? OR m.conversation_id=?)", (job["bot"], per_bot, job["conversation_id"])).fetchone()[0]
    if waiting:
        return waiting
    due = H.shift(job["created"], seconds=gap)
    if per_bot:
        last = c.execute(f"SELECT max(hold_until) FROM jobs WHERE bot=? AND wake_class IN ({kinds},'digest') "
                         "AND held=0 AND hold_until>?", (job["bot"], H.shift(job["created"], seconds=-gap))).fetchone()[0]
        if last:
            due = max(due, H.shift(last, seconds=gap))
    return due


def _release(c, bot, jobs):
    """Let a due batch go as one wake. Notices about one task in one room: the newest starts the turn, and the room
    page it is given carries the others, so they are read with it. Notices across tasks: one digest in the bot's
    notice room names each, and the turn opens whichever it needs; their own messages stay unread where they are."""
    rooms = {(j["conversation_id"], (H._json(j["refs_json"], {}) or {}).get("task")) for j in jobs}
    if len(rooms) == 1:
        *earlier, last = jobs
        c.execute("UPDATE jobs SET held=0 WHERE id=?", (last["id"],))
        for job in earlier:
            c.execute("UPDATE jobs SET state='completed',held=0 WHERE id=?", (job["id"],))
            H.mark_delivered(c, H.KEEPER, job["message_id"])
        return
    lines = []
    for job in jobs:
        message = H.message(c, job["message_id"]) or {}
        task = H.task(c, (H._json(job["refs_json"], {}) or {}).get("task") or "")
        what = classify(message)[1]
        lines.append(f"- {what}: " + (f"a private task ({task['id']})" if task and H.task_private(c, task)
                                      else f"{task['title']} ({task['id']})" if task
                                      else str(message.get("body") or "").splitlines()[0][:200]))
    label = LABELS.get(jobs[0]["wake_preset"], "")
    sent = H.say(c, H.KEEPER, "bot:" + bot, f"{len(jobs)} updates held by your {label} wake preset:\n"
                 + "\n".join(lines) + "\nOpen each task that needs you; the rest is news.", kind="notice",
                 refs={"wake_digest": [j["id"] for j in jobs]})
    held_until = max(c.execute("SELECT hold_until FROM jobs WHERE id=?", (j["id"],)).fetchone()[0] for j in jobs)
    c.execute("UPDATE jobs SET wake_class='digest',wake_preset=?,hold_until=? WHERE message_id=?",
              (jobs[0]["wake_preset"], held_until, (sent or {}).get("id")))
    for job in jobs:
        c.execute("UPDATE jobs SET state='cancelled',held=0 WHERE id=?", (job["id"],))
    H.event(c, H.KEEPER, "job.wake_digest", bot, {"jobs": [j["id"] for j in jobs]})


def replan(c, bot):
    """A preset change: the bot's held jobs are planned again on the new values, or released for current behaviour."""
    config = c.execute("SELECT config_json FROM bot_config WHERE bot=?", (bot,)).fetchone()
    name = preset_of(H._json(config[0], {}) if config else {})
    if name == CURRENT:
        c.execute("UPDATE jobs SET held=0,wake_preset=? WHERE bot=? AND held=1 AND state='queued'", (name, bot))
    else:
        c.execute("UPDATE jobs SET hold_until=NULL,wake_preset=? WHERE bot=? AND held=1 AND state='queued'", (name, bot))


def classify(message):
    """Why a message woke its bot, for the run record: (class, words). The hold never reads this; it reads only the
    keeper's refs, so these labels are a description, not a permission."""
    refs = message.get("refs") or H._json(message.get("refs_json"), {}) or {}
    sender, body, kind = str(message.get("from_actor") or ""), str(message.get("body") or ""), message.get("kind")
    if sender.startswith("human:"):
        return ("question" if kind in ("ask", "question") else "person"), "A person's message"
    if sender != H.KEEPER:
        return ("question" if kind in ("ask", "question") else "message"), f"A message from {H.actor_id(sender)}"
    if refs.get("wake_digest"):
        return "batched", "Batched updates"
    if refs.get("wake_batch") in BATCH_KINDS:
        return "batched", {"completion": "A finished request", "review": "Review comments",
                           "ci": "A check result"}[refs["wake_batch"]]
    wake = refs.get("wake")
    if wake in ("stalled", "status_check") or refs.get("sweep") or wake == "occurrence":
        return "sweep", ("A scheduled routine run" if refs.get("sweep") or wake == "occurrence"
                         else "A stall check" if wake == "stalled" else "A status check")
    if body.startswith(("New task from", "Open:", "Next-run task:")):
        return "assignment", "An assignment"
    if body.startswith(("Unblocked:", "All subtasks done")) or refs.get("subtask") or refs.get("worktree_wait") \
            or wake in ("watcher", "watcher-done") or refs.get("approval"):
        return "dependency", "An awaited result"
    if body.startswith(("Stuck:", "Your task for")) or refs.get("setup_held") or wake == "stuck_cause":
        return "blocker", "A blocker"
    if body.startswith("Ready for review:"):
        return "gate", "A review request"
    if refs.get("run_now"):
        return "person", "Run now"
    if wake in ("due", "snooze", "next-run", "update", "restate_ask"):
        return "scheduled", {"due": "A due date", "snooze": "A snooze ending", "update": "An update request",
                             "restate_ask": "An unanswered ask"}.get(wake, "A next-run task")
    return "notice", "A Tico notice"


def record(c, job, message):
    """The wake a turn starts from, kept on its attempt: what woke it, the preset in force and how long it waited."""
    cls, words = classify(message)
    if job["wake_class"] in (*BATCH_KINDS, "digest", "sweep"):
        cls = "sweep" if job["wake_class"] == "sweep" else "batched"
        words = "A scheduled sweep" if cls == "sweep" and words == "A Tico notice" else words
    name = job["wake_preset"] or CURRENT
    out = {"class": cls, "reason": words, "preset": name, "preset_label": LABELS.get(name, LABELS[CURRENT]),
           "event_at": message.get("created")}
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
    missed actions (an immediate wake not picked up within MISSED_AFTER_SECONDS). Bounded by `limit` jobs."""
    until = until or H.now()
    rows = c.execute(
        "SELECT j.id,j.state,j.wake_preset,j.wake_class,j.created,m.created AS event_at,a.created AS claimed,"
        "t.task_id,t.tokens_in,t.tokens_out FROM jobs j "
        "JOIN messages m ON m.id=j.message_id LEFT JOIN attempts a ON a.job_id=j.id LEFT JOIN turns t ON t.id=a.id "
        "WHERE j.created>=? AND j.created<? AND j.wake_preset IS NOT NULL ORDER BY j.created LIMIT ?",
        (since, until, limit)).fetchall()
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
        held = row["wake_class"] in (*BATCH_KINDS, "digest", "sweep")
        bucket["held"] += 1 if held else 0
        if row["wake_class"] == "duplicate":
            bucket["duplicates"] += 1
            continue
        wait = _seconds(row["event_at"], row["claimed"] or (until if row["state"] == "queued" else None))
        if row["claimed"]:
            bucket["turns"] += 1
            tokens = (row["tokens_in"] or 0) + (row["tokens_out"] or 0)
            bucket["tokens"] += tokens
            if row["task_id"]:
                seen = tickets[name].setdefault(row["task_id"], [0, 0])
                seen[0], seen[1] = seen[0] + 1, seen[1] + tokens
            if wait is not None:
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
