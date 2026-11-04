"""The daily task check (Settings > Routines, "Daily task check"): once a day each active bot gets at most one wake, with one
grouped list of its tasks that have an update it may have missed or have gone quiet. Plain code: no model is called and no
BotOps diagnostic is filed. It replaces the per-task wakes of the stall watcher (hubdb.wake_stalled) and the waiting
checks' unstrand (hubdb.sweep_stranded), which woke a bot about one task at a time.

A task is on its bot's list when it is the bot's, live (H.ACTIVE_STATUSES), readable to it, not snoozed, not already
being run for it (a queued, leased or running job in its conversation), and either:
- a missed update: something by anyone else landed on it after the bot last worked on it. That is a message in its
  conversation, a change in its history (a creation row is not news), or a subtask or blocker that left active status.
  The bot last worked on it at its latest turn on the task, or at a message or history row it wrote on it; a bot that
  never did counts from the start. Or
- gone quiet: nothing at all has happened on it for a day, its status is not `waiting`, no live blocker holds it, and
  nothing is recorded to wait on (H.recorded_wait, which includes an unanswered ask).

A bot whose profile has its stall sweeps off (rules.sweeps_on) gets no list, as the stall watcher left it alone.
The day a bot was sent its list is kept in registry_metadata (`daily_task_check:<slug>`), so a second run that UTC day
sends nothing."""

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
    from . import task_relations as TR, task_snooze as TS
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
        if (row["status"] != "waiting" and H.parse_ts(quiet_since) <= cutoff
                and TR.live_blocker(c, row["id"], H.ACTIVE_STATUSES) is None
                and H.recorded_wait(c, dict(row), at) is None):
            days = int((now - H.parse_ts(quiet_since)).total_seconds() // 86400)
            reason = f"Quiet for {days} day{'' if days == 1 else 's'}, nothing waiting on anyone"
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


def _last_turn(c, row, slug, actor):
    """When the bot last worked on the task: its latest turn on it, or a message or history row it wrote on it."""
    return c.execute(
        "SELECT max(x) FROM (SELECT max(started_at) AS x FROM turns WHERE bot=? AND task_id=? "
        "UNION ALL SELECT max(created_at) FROM messages WHERE conversation_id=? AND sender_id=? AND deleted_at IS NULL "
        "UNION ALL SELECT max(created_at) FROM history_events WHERE subject_type='task' AND subject_id=? AND actor_id=?)",
        (slug, row["id"], row["conversation_id"], actor, row["id"], actor)).fetchone()[0]


def _news(c, row, actor, base):
    """(count, latest time, what the latest was) of what anyone else did on the task since `base`, or None."""
    tid, conv = row["id"], row["conversation_id"]
    msgs = c.execute("SELECT count(*), max(created_at) FROM messages WHERE conversation_id=? AND coalesce(sender_id,'')<>? "
                     "AND deleted_at IS NULL AND created_at>?", (conv, actor, base)).fetchone()
    hist = c.execute("SELECT count(*) FROM history_events WHERE subject_type='task' AND subject_id=? "
                     "AND coalesce(actor_id,'')<>? AND old IS NOT NULL AND created_at>?", (tid, actor, base)).fetchone()[0]
    related = H._rows(c.execute(
        "SELECT t.title, t.status, t.updated_at, r.kind FROM task_relations r JOIN tasks t ON t.id=r.from_task "
        f"WHERE r.to_task=? AND r.kind IN ('parent','blocks') AND t.status NOT IN ({_marks()}) AND t.updated_at>? "
        "ORDER BY t.updated_at DESC", (tid, *H.ACTIVE_STATUSES, base)))
    count = (msgs[0] or 0) + hist + len(related)
    if not count:
        return None
    options = []
    if msgs[0]:
        sender = c.execute("SELECT sender_id FROM messages WHERE conversation_id=? AND coalesce(sender_id,'')<>? "
                           "AND deleted_at IS NULL AND created_at>? ORDER BY created_at DESC LIMIT 1",
                           (conv, actor, base)).fetchone()[0]
        options.append((msgs[1], f"a message from {_who(c, sender)}"))
    if hist:
        latest = c.execute("SELECT created_at, kind, actor_id FROM history_events WHERE subject_type='task' AND subject_id=? "
                           "AND coalesce(actor_id,'')<>? AND old IS NOT NULL AND created_at>? "
                           "ORDER BY created_at DESC LIMIT 1", (tid, actor, base)).fetchone()
        options.append((latest["created_at"], f"{latest['kind']} changed by {_who(c, latest['actor_id'])}"))
    if related:
        top = related[0]
        options.append((top["updated_at"], f"a {'subtask' if top['kind'] == 'parent' else 'blocker'} is {top['status']}"))
    when, what = max(options)
    return count, when, what


def _latest(c, row):
    """The newest message in the task's conversation or history row on the task, of anyone."""
    msg = c.execute("SELECT max(created_at) FROM messages WHERE conversation_id=? AND deleted_at IS NULL",
                    (row["conversation_id"],)).fetchone()[0]
    hist = c.execute("SELECT max(created_at) FROM history_events WHERE subject_type='task' AND subject_id=?",
                     (row["id"],)).fetchone()[0]
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
