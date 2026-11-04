"""The daily task check (Settings > Routines, "Daily task check"): once a day each active bot gets at most one wake, with one
grouped list of its tasks that have an update it may have missed or have gone quiet. Plain code: no model is called and no
BotOps diagnostic is filed. It replaces the per-task wakes of the stall watcher and of the check-backs, which woke a bot one
task at a time.

A task is on its bot's list when it is the bot's, live (H.ACTIVE_STATUSES), readable to it, not snoozed, not already being
run for it (a queued, leased or running job in its conversation), and either:
- a missed update: someone other than the bot and Tico did something on it since the later of the bot's last turn on it and
  the last time it was listed. That is a message written or edited in its conversation; a change of the task itself (status,
  owner, step, waiting_on, due, title, body or acceptance; a task's creation row is not news); a comment edited or deleted;
  a blocker taken off it (a blocker that finished is taken off by Tico: "a blocker is done"); or a subtask that finished,
  by someone other than the bot. A status change Tico made (a hub move) counts too. Tico's other bookkeeping (check-backs,
  rank, checkpoints, its wake notices) never counts. Or
- gone quiet: nothing has changed on it for a day, and nothing holds it: it does not wait on someone (waiting_on), no live
  blocker holds it, and nothing is recorded to wait on (H.recorded_wait, which includes an unanswered ask). A task left in
  `waiting` that waits on nothing is quiet like any other. A quiet task is listed when it was never listed or was last
  listed 7 or more days ago.

A private task is listed as "a private task <id>", without its title. A bot whose profile has its stall sweeps off
(rules.sweeps_on) gets no list.

Each bot's list is sent at most once per UTC day (registry_metadata `daily_task_check:<slug>`), and each task's last listing
is kept (`daily_task_check:task:<id>`). Both are written only once the message is queued, so a message a rule held is not
counted as sent. The job's first run waits for its first 14:00 UTC (backend/scheduler.py, Scheduler.due).

The scheduler reads every bot's list in one read transaction (discover), then sends each one in its own short transaction
that rechecks the day (send)."""

import sqlite3
from datetime import timedelta

from .batch_work import isolated
from .store import H

WAKE = "daily_task_check"
EVENT = "daily_task_check.sent"
DAY_KEY = "daily_task_check:"           # registry_metadata: the UTC day a bot was last sent its list
LISTED_KEY = "daily_task_check:task:"   # registry_metadata: when a task was last on a list
MAX_LINES = 25                          # task lines in one list; the rest are counted
TITLE_CHARS = 80
QUIET_HOURS = 24
RELIST_DAYS = 7
# The changes to a task itself. Anything else a history row records (check-backs, rank, checkpoints) is bookkeeping.
TASK_KINDS = ("status", "owner", "step", "waiting_on", "due", "title", "body", "acceptance")
_TASK_KINDS_SQL = ",".join(f"'{kind}'" for kind in TASK_KINDS)
# The quiet clock: a change of the task itself by anyone. Args: the task.
_LATEST_HIST = f"subject_type='task' AND subject_id=? AND kind IN ({_TASK_KINDS_SQL})"
# Args: the task, the base, the bot, Tico. A creation row (a status or step with no old value) is not news; Tico's own
# moves are news only when they are status moves.
_MISSED_HIST = (f"subject_type='task' AND subject_id=? AND kind IN ({_TASK_KINDS_SQL},'comment') "
                "AND NOT (kind IN ('status','step') AND old IS NULL) AND created_at>? AND actor_id IS NOT NULL "
                "AND actor_id<>? AND (actor_id<>? OR kind='status')")
# A blocker taken off the task: the relation removal writes a blocked_by row with no new value. Args: the task, the base, the bot.
_UNBLOCKED = ("subject_type='task' AND subject_id=? AND kind='blocked_by' AND new IS NULL AND created_at>? "
              "AND actor_id IS NOT NULL AND actor_id<>?")
# A message's news time is when it was written or last edited. Args: the conversation, the bot, Tico, the base, the base.
_WHEN = "max(created_at, coalesce(edited_at, created_at))"
_FROM_OTHERS = ("deleted_at IS NULL AND sender_id IS NOT NULL AND sender_id<>? AND sender_id<>? "
                "AND (created_at>? OR edited_at>?)")


def run(c, at=None):
    """Discover and send in the caller's transaction (tests). Returns {"woke": [slugs], "listed": tasks named in them}."""
    woke, listed = [], 0
    for plan in discover(c, at):
        if send(c, plan):
            woke.append(plan["slug"])
            listed += plan["count"]
    return {"woke": woke, "listed": listed}


def discover(c, at=None):
    """The lists due at `at`, read without writing: one plan per active bot not yet sent today that has something to list."""
    from . import rules
    from .hubdb import _has_table
    if not _has_table(c, "jobs"):       # a hub without jobs has no wakes to send: nothing is listed
        return []
    at = at or H.now()
    day = H.parse_ts(at).date().isoformat()
    plans = []
    for bot in H._rows(c.execute("SELECT id FROM bots WHERE state='active' ORDER BY id")):
        slug = bot["id"]
        if _sent_day(c, slug) == day or not rules.sweeps_on(c, slug):
            continue
        text, count, shown = _list(c, slug, at)
        if text:
            plans.append({"slug": slug, "day": day, "at": at, "text": text, "count": count, "shown": shown})
    return plans


def send(c, plan):
    """Send one bot its list (from discover) in the caller's transaction. It rechecks the day first, and writes the day, the
    tasks' listed times and the event only once the message is queued. Returns whether the list was sent."""
    from . import rules, run_triggers as RT
    slug, day = plan["slug"], plan["day"]
    if _sent_day(c, slug) == day:
        return False
    actor = H.bot_actor(slug)
    with isolated(c, WAKE, slug):
        try:
            message = H._write_message(c, H.KEEPER, actor, plan["text"], rules._bot_room(c, slug), "notice",
                                       RT.stamp({"wake": WAKE}, WAKE), None, None)
        except H.Refused:
            return False        # not told today: a later run may try again
        if not _queued(c, message["id"]):
            return False        # a rule held it, so no bot was woken: not sent, and a later run may try again
        c.execute("INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                  (DAY_KEY + slug, day))
        for tid in plan["shown"]:
            c.execute("INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                      (LISTED_KEY + tid, plan["at"]))
        H.event(c, H.KEEPER, EVENT, slug, {"day": day, "tasks": plan["count"]})
        return True


def _sent_day(c, slug):
    row = c.execute("SELECT value FROM registry_metadata WHERE key=?", (DAY_KEY + slug,)).fetchone()
    return row[0] if row else None


def _listed(c, tid):
    row = c.execute("SELECT value FROM registry_metadata WHERE key=?", (LISTED_KEY + tid,)).fetchone()
    return row[0] if row else None


def _later(*times):
    """The latest of the times that are set, or "" when none is."""
    return max((t for t in times if t), default="")


def _list(c, slug, at):
    """The bot's list as one message's text, how many tasks it names, and their ids; (None, 0, []) when nothing needs a look."""
    from . import task_snooze as TS
    actor = H.bot_actor(slug)
    now = H.parse_ts(at)
    cutoff = now - timedelta(hours=QUIET_HOURS)
    relist = now - timedelta(days=RELIST_DAYS)
    missed, quiet = [], []
    rows = H._rows(c.execute("SELECT * FROM tasks WHERE owner_id=? AND status IN (" + _marks() + ") ORDER BY updated_at",
                             (actor, *H.ACTIVE_STATUSES)))
    for row in rows:
        if not H.task_private_readable(c, actor, row) or TS.snoozed(row, at) or _running(c, slug, row):
            continue
        last_turn = _last_turn(c, row, slug, actor)
        listed = _listed(c, row["id"])
        news = _news(c, row, actor, _later(last_turn, listed))
        if news:
            count, when, what = news
            reason = f"Update you may have missed: {what} ({count} since you last worked on it)"
            missed.append((when, row["id"], _line(c, row, reason)))
            continue
        quiet_since = _later(row["updated_at"], last_turn, _latest(c, row))
        if H.parse_ts(quiet_since) <= cutoff and not _holds(c, row, at) and (listed is None or H.parse_ts(listed) <= relist):
            span = _days(now - H.parse_ts(quiet_since))
            reason = (f"Waiting on nothing for {span}" if row["status"] == "waiting"
                      else f"Quiet for {span}, nothing waiting on anyone")
            quiet.append((quiet_since, row["id"], _line(c, row, reason)))
    if not missed and not quiet:
        return None, 0, []
    ordered = [(tid, line) for _, tid, line in sorted(missed, reverse=True)] + [(tid, line) for _, tid, line in sorted(quiet)]
    shown = ordered[:MAX_LINES]
    total = len(ordered)
    lines = [f"Daily task check: {total} of your tasks need a look. Work each one, or set it waiting on whoever must act.",
             *(line for _, line in shown)]
    if total > len(shown):
        lines.append(f"- and {total - len(shown)} more")
    return "\n".join(lines), total, [tid for tid, _ in shown]


def _marks():
    return ",".join("?" for _ in H.ACTIVE_STATUSES)


def _line(c, row, reason):
    if H.task_private(c, row):
        return f"- a private task {row['id'][:8]}: {reason}"
    title = " ".join((row["title"] or "").split())[:TITLE_CHARS] or "Untitled"
    return f"- {row['id'][:8]} {title}: {reason}"


def _days(delta):
    days = int(delta.total_seconds() // 86400)
    return f"{days} day{'' if days == 1 else 's'}"


def _holds(c, row, at):
    """Whether something holds the task: a person it waits on, a live blocker, or a wait recorded for it (an unanswered
    ask included)."""
    from . import task_relations as TR
    return bool(row.get("waiting_on")) or TR.live_blocker(c, row["id"], H.ACTIVE_STATUSES) is not None \
        or H.recorded_wait(c, dict(row), at) is not None


def _last_turn(c, row, slug, actor):
    """When the bot last worked on the task: its latest turn on it, or a message or history row it wrote on it."""
    return c.execute(
        "SELECT max(x) FROM (SELECT max(started_at) AS x FROM turns WHERE bot=? AND task_id=? "
        "UNION ALL SELECT max(created_at) FROM messages WHERE conversation_id=? AND sender_id=? AND deleted_at IS NULL "
        "UNION ALL SELECT max(created_at) FROM history_events WHERE subject_type='task' AND subject_id=? AND actor_id=?)",
        (slug, row["id"], row["conversation_id"], actor, row["id"], actor)).fetchone()[0]


def _news(c, row, actor, base):
    """(count, latest time, what the latest was) of what others did on the task since `base`, or None."""
    tid, conv = row["id"], row["conversation_id"]
    msgs, msg_when = c.execute(f"SELECT count(*), max({_WHEN}) FROM messages WHERE conversation_id=? AND {_FROM_OTHERS}",
                               (conv, actor, H.KEEPER, base, base)).fetchone()
    hist = c.execute(f"SELECT count(*) FROM history_events WHERE {_MISSED_HIST}",
                     (tid, base, actor, H.KEEPER)).fetchone()[0]
    gone = H._rows(c.execute(f"SELECT created_at, actor_id FROM history_events WHERE {_UNBLOCKED} "
                             "ORDER BY created_at DESC", (tid, base, actor)))
    kids = H._rows(c.execute(
        "SELECT t.status, h.created_at AS at FROM task_relations r JOIN tasks t ON t.id=r.from_task "
        "JOIN history_events h ON h.subject_type='task' AND h.subject_id=t.id AND h.kind='status' "
        f"AND h.new NOT IN ({_marks()}) AND h.created_at>? AND h.actor_id<>? "
        f"WHERE r.to_task=? AND r.kind='parent' AND t.status NOT IN ({_marks()}) ORDER BY h.created_at DESC",
        (*H.ACTIVE_STATUSES, base, actor, tid, *H.ACTIVE_STATUSES)))
    count = (msgs or 0) + hist + len(gone) + len(kids)
    if not count:
        return None
    options = []
    if msgs:
        sender = c.execute(f"SELECT sender_id FROM messages WHERE conversation_id=? AND {_FROM_OTHERS} "
                           f"ORDER BY {_WHEN} DESC LIMIT 1", (conv, actor, H.KEEPER, base, base)).fetchone()[0]
        options.append((msg_when, f"a message from {_who(c, sender)}"))
    if hist:
        latest = c.execute(f"SELECT created_at, kind, actor_id FROM history_events WHERE {_MISSED_HIST} "
                           "ORDER BY created_at DESC LIMIT 1", (tid, base, actor, H.KEEPER)).fetchone()
        label = "a comment" if latest["kind"] == "comment" else latest["kind"]
        options.append((latest["created_at"], f"{label} changed by {_who(c, latest['actor_id'])}"))
    if gone:
        taken = gone[0]
        what = ("a blocker is done" if taken["actor_id"] == H.KEEPER
                else f"a blocker was taken off by {_who(c, taken['actor_id'])}")
        options.append((taken["created_at"], what))
    if kids:
        options.append((kids[0]["at"], f"a subtask is {kids[0]['status']}"))
    when, what = max(options)
    return count, when, what


def _latest(c, row):
    """The newest thing that resets the quiet clock: a message written or edited by anyone but Tico, or a change of the task
    itself (TASK_KINDS) by anyone, Tico's status moves included. Tico's bookkeeping does not reset it."""
    msg = c.execute(f"SELECT max({_WHEN}) FROM messages WHERE conversation_id=? AND deleted_at IS NULL "
                    "AND sender_id IS NOT NULL AND sender_id<>?", (row["conversation_id"], H.KEEPER)).fetchone()[0]
    hist = c.execute(f"SELECT max(created_at) FROM history_events WHERE {_LATEST_HIST}", (row["id"],)).fetchone()[0]
    return _later(msg, hist) or None


def _who(c, actor):
    if not actor or actor == H.KEEPER:
        return "Tico"
    return H.actor_name(c, actor) or actor


def _running(c, slug, row):
    """Whether a job for this bot in the task's conversation is queued, leased or running: it is being woken already."""
    try:
        return c.execute("SELECT 1 FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.bot_id=? "
                         "AND j.state IN ('queued','leased','running') AND m.conversation_id=? LIMIT 1",
                         (slug, row["conversation_id"])).fetchone() is not None
    except sqlite3.OperationalError:    # a local hub has no jobs table
        return False


def _queued(c, message_id):
    """Whether the message started a job, so the bot is actually woken (backend/enqueue.py)."""
    return c.execute("SELECT 1 FROM jobs WHERE message_id=? LIMIT 1", (message_id,)).fetchone() is not None
