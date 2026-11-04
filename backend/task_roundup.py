"""The task roundup (Settings > Routines, "Task roundup"): each run gives each active bot at most one wake, with one grouped
list of its tasks that have an update it may have missed, have gone quiet, or have a follow-up time that has come. Plain
code: no model is called and no BotOps
diagnostic is filed. It replaces the per-task wakes of the stall watcher and of the check-backs, which woke a bot one task
at a time. Its schedule is a setting; the default is three times a day, at 6am, noon and 6pm company time.

A task is on its bot's list when it is the bot's, live (H.ACTIVE_STATUSES), readable to it, not snoozed, not already being
run for it (a queued, leased or running job in its conversation), and either:
- a missed update: someone other than the bot and Tico did something on it since the later of the bot's last turn on it and
  the last time it was listed. That is a message written or edited in its conversation; a change of the task itself (status,
  owner, step, waiting_on, due, title, body or acceptance; a task's creation row is not news, but a step or status set later
  on a task that had none is); a comment edited or deleted; a blocker taken off it; a blocker that finished (taken off by
  Tico: "a blocker is done", or kept on a roadmap item); or a subtask that finished, by someone other than the bot. A status
  change Tico made (a hub move) counts too. Tico's other bookkeeping (check-backs, rank, checkpoints, its wake notices) never
  counts. Or
- gone quiet: nothing has changed on it for a day, and nothing holds it: it does not wait on someone (waiting_on), no live
  blocker holds it, nothing is recorded to wait on (H.recorded_wait, which includes an unanswered ask), and no follow-up
  time is still to come. A task left in
  `waiting` that waits on nothing is quiet like any other. A quiet task is listed when it was never listed or was last
  listed 7 or more days ago.

A follow-up time (tasks.check_back_at) is set only by a person or a bot: its check_back_explicit history row names the
time. When it has come, the task is listed first on its owner bot's list as "Follow-up time set by <who> has come: <note>"
(the note trimmed), and the time is then cleared, so it is listed once. Tico sets no follow-up time of its own, and a
task a person owns is on no bot's list. A date a snooze left behind is not a follow-up: it holds nothing, and a snooze in
force hides the task (task_snooze.snoozed) until it ends.

A private task is listed as "a private task <id>", without its title, its follow-up's who and note, or what changed: only
that a follow-up time has come, that it has an update, or that it has gone quiet. A bot whose profile has its stall sweeps off (rules.sweeps_on) gets no
list.

A bot is sent at most one list per run: registry_metadata `task_roundup:<slug>` holds the run's time it was last sent a
list, and a bot already sent one since this run's time gets nothing more, so the roundup works at any cadence. Each task's
last listing is kept (`task_roundup:task:<id>`) as the time its list was built, not the run's time, so an update that lands
after the run's time but before the build is not listed again. Both are written only once the message is delivered: a bot
run by a computer is delivered when its job is queued, and an external inbox (no computer, no job) when the message is
written. A message a rule held is not counted as sent. The list is written to the bot's own notice room with Tico, where
no person is a member, so the roundup never notifies a person.

The scheduler selects the bots in one read transaction (discover), then sends each one in its own short transaction (send),
which rebuilds that bot's list from the live database before it writes anything."""

import sqlite3
from datetime import timedelta

from .batch_work import isolated
from .store import H

WAKE = "task_roundup"
EVENT = "task_roundup.sent"
SENT_KEY = "task_roundup:"             # registry_metadata: the run's time a bot was last sent a list
LISTED_KEY = "task_roundup:task:"      # registry_metadata: the run's time a task was last on a list
HEADING = "Here is a list of tasks on your plate. You may want to look into them."
MAX_LINES = 25                         # task lines in one list; the rest are counted
TITLE_CHARS = 80
QUIET_HOURS = 24
RELIST_DAYS = 7
FOLLOW_UP_CHARS = 160             # a follow-up's note on its list line, trimmed to this
CREATED_WITHIN = 1                     # seconds: a status or step row this close to its task's creation is the creation
# The changes to a task itself. Anything else a history row records (check-backs, rank, checkpoints) is bookkeeping.
TASK_KINDS = ("status", "owner", "step", "waiting_on", "due", "title", "body", "acceptance")
_TASK_KINDS_SQL = ",".join(f"'{kind}'" for kind in TASK_KINDS)
# The quiet clock: a change of the task itself by anyone. Args: the task.
_LATEST_HIST = f"subject_type='task' AND subject_id=? AND kind IN ({_TASK_KINDS_SQL})"
# Args: the task, the creation horizon, the base, the bot, Tico. A creation row (a status or step with no old value, written
# with the task) is not news; Tico's own moves are news only when they are status moves.
_MISSED_HIST = (f"subject_type='task' AND subject_id=? AND kind IN ({_TASK_KINDS_SQL},'comment') "
                "AND NOT (kind IN ('status','step') AND old IS NULL AND created_at<=?) AND created_at>? "
                "AND actor_id IS NOT NULL AND actor_id<>? AND (actor_id<>? OR kind='status')")
# A message's news time is when it was written or last edited. Args: the conversation, the bot, Tico, the base, the base.
_WHEN = "max(created_at, coalesce(edited_at, created_at))"
_FROM_OTHERS = ("deleted_at IS NULL AND sender_id IS NOT NULL AND sender_id<>? AND sender_id<>? "
                "AND (created_at>? OR edited_at>?)")


def run(c, at=None):
    """Discover and send in the caller's transaction (tests). Returns {"woke": [slugs], "listed": tasks named in them}."""
    woke, listed = [], 0
    for plan in discover(c, at):
        count = send(c, plan)
        if count:
            woke.append(plan["slug"])
            listed += count
    return {"woke": woke, "listed": listed}


def discover(c, at=None):
    """The bots to visit for the run at `at`: active bots with something to list that were not sent a list in this run. Read
    only. send builds each list again from the live database."""
    from . import rules
    from .hubdb import _has_table
    if not _has_table(c, "jobs"):       # a hub without jobs has no wakes to send: nothing is listed
        return []
    at = at or H.now()
    plans = []
    for bot in H._rows(c.execute("SELECT id FROM bots WHERE state='active' ORDER BY id")):
        slug = bot["id"]
        if _sent_before(c, slug, at) or not _owns_live(c, slug) or not rules.sweeps_on(c, slug):
            continue                    # no live task of its own: no list, and no per-task query for it
        if _list(c, slug, at)[0]:
            plans.append({"slug": slug, "at": at})
    return plans


def send(c, plan):
    """Send one bot its list in the caller's transaction. The list is rebuilt here from the live database (discover only chose
    the bot), and the bot is sent only if it was not already sent one this run. A bot with nothing to list gets no message.
    The run's time, the tasks' listed times, the consumed follow-ups and the event are written only once the message is
    delivered. Returns how many tasks the list named, or 0 when nothing was sent."""
    from . import rules, run_triggers as RT, task_check_backs
    slug, at = plan["slug"], plan["at"]
    if _sent_before(c, slug, at) or not _active(c, slug) or not _owns_live(c, slug) or not rules.sweeps_on(c, slug):
        return 0
    built = _later(at, _built_at())        # stamped before the read: an update racing it is listed again, never missed
    text, count, shown, due = _list(c, slug, at)
    if not text:
        return 0
    actor = H.bot_actor(slug)
    with isolated(c, WAKE, slug):
        try:
            # no_mentions: a task's title may say "@ana", which is text in the list, not a mention, so no person is delivered it.
            message = H._write_message(c, H.KEEPER, actor, text, _room(c, slug), "notice",
                                       RT.stamp({"wake": WAKE, "no_mentions": True}, WAKE), None, None)
        except H.Refused:
            return 0                    # not told this run: a later run may try again
        if not _delivered(c, slug, message):
            return 0                    # a rule held it, so no bot was woken: not sent, and a later run may try again
        c.execute("INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                  (SENT_KEY + slug, at))
        for tid in shown:
            c.execute("INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                      (LISTED_KEY + tid, built))
        for tid in due:
            task_check_backs.consume(c, tid, H.KEEPER)
        H.event(c, H.KEEPER, EVENT, slug, {"at": at, "tasks": count, "follow_ups": len(due)})
        return count
    return 0


def _sent_before(c, slug, at):
    """Whether this bot was already sent a list in the run at `at` (its last list is at or after that run's time)."""
    row = c.execute("SELECT value FROM registry_metadata WHERE key=?", (SENT_KEY + slug,)).fetchone()
    return bool(row) and H.parse_ts(row[0]) >= H.parse_ts(at)


def _active(c, slug):
    return c.execute("SELECT 1 FROM bots WHERE id=? AND state='active'", (slug,)).fetchone() is not None


def _listed(c, tid):
    row = c.execute("SELECT value FROM registry_metadata WHERE key=?", (LISTED_KEY + tid,)).fetchone()
    return row[0] if row else None


def _later(*times):
    """The latest of the times that are set, or "" when none is."""
    return max((t for t in times if t), default="")


def _built_at():
    """The time a list is built, stamped as the checkpoint of each task on it. Tests pin it (it is the wall clock otherwise)."""
    return H.now()


def _room(c, slug):
    """The bot's own notice room with Tico: the keeper and the bot are its only members, so a list reaches the bot and no
    person is told about a task. (rules._bot_room picks the operator's room or a shared room, where people are.)"""
    return H._pair_conversation(c, H.KEEPER, H.bot_actor(slug), "notice", allow_held=True)


def _list(c, slug, at):
    """The bot's list as one message's text, how many tasks it names, the ids it names, and the ids of its follow-ups among
    them (cleared once it is sent). A follow-up whose time has come goes first. (None, 0, [], []) when nothing needs a look."""
    from . import task_snooze as TS
    actor = H.bot_actor(slug)
    now = H.parse_ts(at)
    cutoff = now - timedelta(hours=QUIET_HOURS)
    relist = now - timedelta(days=RELIST_DAYS)
    due, missed, quiet = [], [], []
    rows = H._rows(c.execute("SELECT * FROM " + H.TASKS + " tasks WHERE owner_id=? AND status IN (" + _marks() + ") ORDER BY updated_at",
                             (actor, *H.ACTIVE_STATUSES)))
    for row in rows:
        if not H.task_private_readable(c, actor, row) or TS.snoozed(row, at) or _running(c, slug, row):
            continue
        private = H.task_private(c, row)
        follow = _follow_up(c, row)
        if follow and H.parse_ts(row["check_back_at"]) <= now:
            due.append((row["check_back_at"], row["id"], _line(row, _due_reason(follow, private), private)))
            continue
        last_turn = _last_turn(c, row, slug, actor)
        listed = _listed(c, row["id"])
        news = _news(c, row, actor, _later(last_turn, listed))
        if news:
            count, when, what = news
            missed.append((when, row["id"], _line(row, _missed_reason(count, what, private), private)))
            continue
        quiet_since = _later(row["updated_at"], last_turn, _latest(c, row))
        if H.parse_ts(quiet_since) <= cutoff and not _holds(c, row, at) and (listed is None or H.parse_ts(listed) <= relist):
            span = _days(now - H.parse_ts(quiet_since))
            reason = (f"Waiting on nothing for {span}" if row["status"] == "waiting"
                      else f"Quiet for {span}, nothing waiting on anyone")
            quiet.append((quiet_since, row["id"], _line(row, reason, private)))
    if not due and not missed and not quiet:
        return None, 0, [], []
    ordered = ([(tid, line) for _, tid, line in sorted(due)]
               + [(tid, line) for _, tid, line in sorted(missed, reverse=True)]
               + [(tid, line) for _, tid, line in sorted(quiet)])
    shown = ordered[:MAX_LINES]
    total = len(ordered)
    lines = [HEADING, *(line for _, line in shown)]
    if total > len(shown):
        lines.append(f"- and {total - len(shown)} more")
    due_ids = {tid for _, tid, _ in due}
    shown_ids = [tid for tid, _ in shown]
    return "\n".join(lines), total, shown_ids, [tid for tid in shown_ids if tid in due_ids]


def _owns_live(c, slug):
    """Whether the bot owns any live task: the cheap first look, before any per-task query. A bot with none has no list."""
    return c.execute(f"SELECT 1 FROM tasks WHERE owner_id=? AND status IN ({_marks()}) LIMIT 1",
                     (H.bot_actor(slug), *H.ACTIVE_STATUSES)).fetchone() is not None


def _follow_up(c, row):
    """(who set it, its note trimmed) when the task's follow-up time was set by a person or a bot (a check_back_explicit row
    names that time), else None: a time nobody set is not a follow-up."""
    if not row.get("check_back_at"):
        return None
    found = c.execute("SELECT actor_id FROM history_events WHERE subject_type='task' AND subject_id=? "
                      "AND kind='check_back_explicit' AND new=? ORDER BY created_at DESC LIMIT 1",
                      (row["id"], row["check_back_at"])).fetchone()
    if not found:
        return None
    note = " ".join((row.get("check_back_note") or "").split())[:FOLLOW_UP_CHARS]
    return _who(c, found[0]), note


def _due_reason(follow, private):
    """A due follow-up's reason: who set it and its note. A private task says only that the time has come."""
    if private:
        return "Follow-up time has come."
    who, note = follow
    return f"Follow-up time set by {who} has come" + (f": {note}" if note else ".")


def _missed_reason(count, what, private):
    """A missed update's reason: what changed and by whom, and how many. A private task gets the count only."""
    detail = "" if private else f": {what}"
    return f"Update you may have missed{detail} ({count} since you last worked on it)"


def _marks():
    return ",".join("?" for _ in H.ACTIVE_STATUSES)


def _line(row, reason, private):
    """One task's line. A private task is named by its id alone, so its `reason` must already be private-safe."""
    if private:
        return f"- a private task {row['id'][:8]}: {reason}"
    title = " ".join((row["title"] or "").split())[:TITLE_CHARS] or "Untitled"
    return f"- {row['id'][:8]} {title}: {reason}"


def _days(delta):
    days = int(delta.total_seconds() // 86400)
    return f"{days} day{'' if days == 1 else 's'}"


def _holds(c, row, at):
    """Whether something holds the task off the quiet list: a person it waits on, an explicit follow-up time still to come
    (a date nobody set, or one a snooze left behind, holds nothing), a live blocker, or a wait recorded for it (an unanswered
    ask included). A snooze in force is not here: _list skips the task while task_snooze.snoozed."""
    from . import task_relations as TR
    later = bool(_follow_up(c, row) and H.parse_ts(row["check_back_at"]) > H.parse_ts(at))
    return (bool(row.get("waiting_on")) or later
            or TR.live_blocker(c, row["id"], H.ACTIVE_STATUSES) is not None
            or H.recorded_wait(c, dict(row), at) is not None)


def _last_turn(c, row, slug, actor):
    """When the bot last worked on the task: its latest turn on it, or a message or history row it wrote on it."""
    return c.execute(
        "SELECT max(x) FROM (SELECT max(created_at) AS x FROM attempts WHERE bot_id=? AND task_id=? "
        "AND (job_id IS NULL OR trigger IS NOT NULL) "
        "UNION ALL SELECT max(created_at) FROM messages WHERE conversation_id=? AND sender_id=? AND deleted_at IS NULL "
        "UNION ALL SELECT max(created_at) FROM history_events WHERE subject_type='task' AND subject_id=? AND actor_id=?)",
        (slug, row["id"], row["conversation_id"], actor, row["id"], actor)).fetchone()[0]


def _readable(c, actor, rows):
    """The related tasks (rows with their id, owner, requester and private flag) that the bot may read."""
    return [row for row in rows if H.task_private_readable(c, actor, row)]


def _news(c, row, actor, base):
    """(count, latest time, what the latest was) of what others did on the task since `base`, or None."""
    tid, conv = row["id"], row["conversation_id"]
    horizon = H.shift(row["created_at"], seconds=CREATED_WITHIN)
    msgs, msg_when = c.execute(f"SELECT count(*), max({_WHEN}) FROM messages WHERE conversation_id=? AND {_FROM_OTHERS}",
                               (conv, actor, H.KEEPER, base, base)).fetchone()
    hist = c.execute(f"SELECT count(*) FROM history_events WHERE {_MISSED_HIST}",
                     (tid, horizon, base, actor, H.KEEPER)).fetchone()[0]
    # A blocker taken off the task: a blocked_by row with no new value and the blocker's id as its old value. It is news
    # only when the bot can read that blocker; a blocker that no longer exists has no row here, so it is not news.
    gone = _readable(c, actor, H._rows(c.execute(
        "SELECT h.created_at, h.actor_id, t.id, t.owner_id, t.requester_id, t.private FROM history_events h "
        "JOIN tasks t ON t.id=h.old WHERE h.subject_type='task' AND h.subject_id=? AND h.kind='blocked_by' "
        "AND h.new IS NULL AND h.created_at>? AND h.actor_id IS NOT NULL AND h.actor_id<>? ORDER BY h.created_at DESC",
        (tid, base, actor))))
    # A blocker that finished and whose relation is still there (a roadmap item keeps its finished Dev tickets). A related
    # task the bot cannot read is neither counted nor described: its finish is not news on this list.
    retired = _readable(c, actor, H._rows(c.execute(
        "SELECT t.id, t.owner_id, t.requester_id, t.private, t.status, h.created_at AS at FROM task_relations r "
        "JOIN tasks t ON t.id=r.from_task JOIN history_events h ON h.subject_type='task' AND h.subject_id=t.id "
        f"AND h.kind='status' AND h.new NOT IN ({_marks()}) AND h.created_at>? AND h.actor_id<>? "
        f"WHERE r.to_task=? AND r.kind='blocks' AND t.status NOT IN ({_marks()}) ORDER BY h.created_at DESC",
        (*H.ACTIVE_STATUSES, base, actor, tid, *H.ACTIVE_STATUSES))))
    kids = _readable(c, actor, H._rows(c.execute(
        "SELECT t.id, t.owner_id, t.requester_id, t.private, t.status, h.created_at AS at FROM task_relations r "
        "JOIN tasks t ON t.id=r.from_task JOIN history_events h ON h.subject_type='task' AND h.subject_id=t.id "
        f"AND h.kind='status' AND h.new NOT IN ({_marks()}) AND h.created_at>? AND h.actor_id<>? "
        f"WHERE r.to_task=? AND r.kind='parent' AND t.status NOT IN ({_marks()}) ORDER BY h.created_at DESC",
        (*H.ACTIVE_STATUSES, base, actor, tid, *H.ACTIVE_STATUSES))))
    count = (msgs or 0) + hist + len(gone) + len(retired) + len(kids)
    if not count:
        return None
    options = []
    if msgs:
        sender = c.execute(f"SELECT sender_id FROM messages WHERE conversation_id=? AND {_FROM_OTHERS} "
                           f"ORDER BY {_WHEN} DESC LIMIT 1", (conv, actor, H.KEEPER, base, base)).fetchone()[0]
        options.append((msg_when, f"a message from {_who(c, sender)}"))
    if hist:
        latest = c.execute(f"SELECT created_at, kind, actor_id FROM history_events WHERE {_MISSED_HIST} "
                           "ORDER BY created_at DESC LIMIT 1", (tid, horizon, base, actor, H.KEEPER)).fetchone()
        label = "a comment" if latest["kind"] == "comment" else latest["kind"]
        options.append((latest["created_at"], f"{label} changed by {_who(c, latest['actor_id'])}"))
    if gone:
        taken = gone[0]
        what = ("a blocker is done" if taken["actor_id"] == H.KEEPER
                else f"a blocker was taken off by {_who(c, taken['actor_id'])}")
        options.append((taken["created_at"], what))
    if retired:
        options.append((retired[0]["at"], f"a blocker is {retired[0]['status']}"))
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


def _delivered(c, slug, message):
    """Whether the written message reached its bot. A bot run by a computer is woken only by the job its message started
    (backend/enqueue.py). An external inbox (hermes and the other external harnesses, with no computer) gets no job: it reads
    its inbox, so a message written there is delivered."""
    if _external(c, slug):
        return True
    return _queued(c, message["id"])


def _external(c, slug):
    from .enqueue import external_bot_sql
    try:
        return c.execute(f"SELECT 1 WHERE {external_bot_sql('?')}", (H.bot_actor(slug),)).fetchone() is not None
    except sqlite3.OperationalError:    # a hub without its bot config or runner tables
        return False


def _queued(c, message_id):
    """Whether the message started a job, so the bot is actually woken (backend/enqueue.py)."""
    return c.execute("SELECT 1 FROM jobs WHERE message_id=? LIMIT 1", (message_id,)).fetchone() is not None
