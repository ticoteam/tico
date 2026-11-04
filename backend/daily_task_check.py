"""The daily task check (Settings > Routines, "Daily task check"): once a day each active bot gets at most one wake, with one
grouped list of its tasks that have an update it may have missed or have gone quiet. Plain code: no model is called and no
BotOps diagnostic is filed. It replaces the per-task wakes of the stall watcher (hubdb.wake_stalled) and the waiting
checks' unstrand (hubdb.sweep_stranded), which woke a bot about one task at a time.

A task is on its bot's list when it is the bot's, live (H.ACTIVE_STATUSES), readable to it, not snoozed, not already
being run for it (a queued, leased or running job in its conversation), and either:
- a missed update: someone other than the bot and Tico did something on it after the bot last worked on it. That is a
  message in its conversation, a change of its own (status, owner, step, waiting_on, due_at, title, body or acceptance,
  by someone other than the bot; a creation row is not news), or a subtask or blocker that left active status. A status
  change Tico made (a hub move) counts too. Tico's other bookkeeping (check-backs, rank, checkpoints, its wake notices)
  never counts. The bot last worked on it at its latest turn on the task, or at a message or history row it wrote on
  it; a bot that never did counts from the start. Or
- gone quiet: nothing has changed on it for a day (a message from anyone but Tico, or one of the changes above by
  anyone), and nothing holds it: it does not wait on someone (waiting_on), no live blocker holds it, and nothing is
  recorded to wait on (H.recorded_wait, which includes an unanswered ask). A task left in `waiting` that waits on
  nothing is quiet like any other.

A bot whose profile has its stall sweeps off (rules.sweeps_on) gets no list, as the stall watcher left it alone.
The day a bot was sent its list is kept in registry_metadata (`daily_task_check:<slug>`), so a second run that UTC day
sends nothing. The job's first run waits for its first 14:00 UTC (backend/scheduler.py, Scheduler.due)."""

import sqlite3
from datetime import timedelta

from .batch_work import isolated
from .store import H

WAKE = "daily_task_check"
EVENT = "daily_task_check.sent"
DAY_KEY = "daily_task_check:"   # registry_metadata: the UTC day a bot was last sent its list
MAX_LINES = 25                  # task lines in one list; the rest are counted
TITLE_CHARS = 80
QUIET_HOURS = 24
# The changes to a task itself. Anything else a history row records (check-backs, rank, checkpoints) is bookkeeping.
NEWS_KINDS = ("status", "owner", "step", "waiting_on", "due_at", "title", "body", "acceptance")
_KINDS = "subject_type='task' AND subject_id=? AND kind IN (" + ",".join(f"'{kind}'" for kind in NEWS_KINDS) + ")"
# Args: the task, the bot's last turn, the bot, Tico. A status change Tico made still counts.
_MISSED = (_KINDS + " AND old IS NOT NULL AND created_at>? AND actor_id IS NOT NULL AND actor_id<>? "
           "AND (actor_id<>? OR kind='status')")
# Args: the bot, Tico. Messages with no sender are not from anyone, so they are not news either.
_FROM_OTHERS = "sender_id IS NOT NULL AND sender_id<>? AND sender_id<>?"


def run(c, at):
    """Send each active bot its list, at most once per UTC day. Returns {"woke": [slugs], "listed": tasks named in them}."""
    from . import rules
    at = at or H.now()
    day = H.parse_ts(at).date().isoformat()
    woke, listed = [], 0
    for bot in H._rows(c.execute("SELECT id FROM bots WHERE state='active' ORDER BY id")):
        slug = bot["id"]
        sent = c.execute("SELECT value FROM registry_metadata WHERE key=?", (DAY_KEY + slug,)).fetchone()
        if (sent and sent[0] == day) or not rules.sweeps_on(c, slug):
            continue
        text, count = _list(c, slug, at)
        if not text:
            continue
        with isolated(c, WAKE, slug):
            try:
                _send(c, slug, text, day, count)
            except H.Refused:
                continue        # not told today: the next run may try again
        woke.append(slug)
        listed += count
    return {"woke": woke, "listed": listed}


def _list(c, slug, at):
    """The bot's list as one message's text, and how many tasks it names; (None, 0) when nothing needs a look."""
    from . import task_snooze as TS
    actor = H.bot_actor(slug)
    now = H.parse_ts(at)
    cutoff = now - timedelta(hours=QUIET_HOURS)
    missed, quiet = [], []
    rows = H._rows(c.execute("SELECT * FROM tasks WHERE owner_id=? AND status IN (" + _marks() + ") ORDER BY updated_at",
                             (actor, *H.ACTIVE_STATUSES)))
    for row in rows:
        if not H.task_private_readable(c, actor, row) or TS.snoozed(row, at) or _running(c, slug, row):
            continue
        base = _last_turn(c, row, slug, actor)
        news = _news(c, row, actor, base or "")
        if news:
            count, when, what = news
            reason = f"Update you may have missed: {what} ({count} since you last worked on it)"
            missed.append((when, _line(row, reason)))
            continue
        quiet_since = max(x for x in (row["updated_at"], base, _latest(c, row)) if x)
        if H.parse_ts(quiet_since) <= cutoff and not _holds(c, row, at):
            span = _days(now - H.parse_ts(quiet_since))
            reason = (f"Waiting on nothing for {span}" if row["status"] == "waiting"
                      else f"Quiet for {span}, nothing waiting on anyone")
            quiet.append((quiet_since, _line(row, reason)))
    if not missed and not quiet:
        return None, 0
    ordered = [line for _, line in sorted(missed, reverse=True)] + [line for _, line in sorted(quiet)]
    shown = ordered[:MAX_LINES]
    total = len(ordered)
    lines = [f"Daily task check: {total} of your tasks need a look. Work each one, or set it waiting on whoever must act.",
             *shown]
    if total > len(shown):
        lines.append(f"- and {total - len(shown)} more")
    return "\n".join(lines), total


def _marks():
    return ",".join("?" for _ in H.ACTIVE_STATUSES)


def _line(row, reason):
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
    msgs = c.execute("SELECT count(*), max(created_at) FROM messages WHERE conversation_id=? AND deleted_at IS NULL "
                     f"AND {_FROM_OTHERS} AND created_at>?", (conv, actor, H.KEEPER, base)).fetchone()
    hist = c.execute(f"SELECT count(*) FROM history_events WHERE {_MISSED}", (tid, base, actor, H.KEEPER)).fetchone()[0]
    related = H._rows(c.execute(
        "SELECT t.title, t.status, t.updated_at, r.kind FROM task_relations r JOIN tasks t ON t.id=r.from_task "
        f"WHERE r.to_task=? AND r.kind IN ('parent','blocks') AND t.status NOT IN ({_marks()}) AND t.updated_at>? "
        "ORDER BY t.updated_at DESC", (tid, *H.ACTIVE_STATUSES, base)))
    count = (msgs[0] or 0) + hist + len(related)
    if not count:
        return None
    options = []
    if msgs[0]:
        sender = c.execute("SELECT sender_id FROM messages WHERE conversation_id=? AND deleted_at IS NULL "
                           f"AND {_FROM_OTHERS} AND created_at>? ORDER BY created_at DESC LIMIT 1",
                           (conv, actor, H.KEEPER, base)).fetchone()[0]
        options.append((msgs[1], f"a message from {_who(c, sender)}"))
    if hist:
        latest = c.execute(f"SELECT created_at, kind, actor_id FROM history_events WHERE {_MISSED} "
                           "ORDER BY created_at DESC LIMIT 1", (tid, base, actor, H.KEEPER)).fetchone()
        options.append((latest["created_at"], f"{latest['kind']} changed by {_who(c, latest['actor_id'])}"))
    if related:
        top = related[0]
        options.append((top["updated_at"], f"a {'subtask' if top['kind'] == 'parent' else 'blocker'} is {top['status']}"))
    when, what = max(options)
    return count, when, what


def _latest(c, row):
    """The newest thing that resets the quiet clock: a message from anyone but Tico, or a change of the task itself (see
    NEWS_KINDS) by anyone, Tico's status moves included. Tico's bookkeeping does not reset it."""
    msg = c.execute("SELECT max(created_at) FROM messages WHERE conversation_id=? AND deleted_at IS NULL "
                    "AND sender_id IS NOT NULL AND sender_id<>?", (row["conversation_id"], H.KEEPER)).fetchone()[0]
    hist = c.execute(f"SELECT max(created_at) FROM history_events WHERE {_KINDS}", (row["id"],)).fetchone()[0]
    return max(x for x in (msg, hist) if x) if (msg or hist) else None


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


def _send(c, slug, text, day, count):
    from . import rules, run_triggers as RT
    actor = H.bot_actor(slug)
    H._write_message(c, H.KEEPER, actor, text, rules._bot_room(c, slug), "notice",
                     RT.stamp({"wake": WAKE}, WAKE), None, None)
    c.execute("INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
              (DAY_KEY + slug, day))
    H.event(c, H.KEEPER, EVENT, slug, {"day": day, "tasks": count})
