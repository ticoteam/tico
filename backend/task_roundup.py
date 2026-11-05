"""The task roundup (Settings > Routines, "Task roundup"): each run gives each active bot at most one wake, with one grouped
list of the tasks it needs to look at. Plain code: no model is called and no BotOps diagnostic is filed. It is the one
place Tico tells a bot about its tasks on a schedule: the per-task wakes of the stall watcher, the check-backs, the late
notices, the backlog warnings, the exceptions lines, the restate asks, the still-needed batch and the assigned-start
recovery are all lines here now.
Its schedule is a setting; the default is three times a day, at 6am, noon and 6pm company time.

A bot's list has these lines, in this order, MAX_LINES in all (the rest are counted), one line per task:
- a follow-up time that has come, set by a person or a bot (below);
- a question the bot asked a person that had no answer in two business days: it went back to the bot (backend/still_needed.py),
  listed once;
- an assignment the bot has not started an hour after it was given (backend/assigned_start.py): "Assigned, not started", listed
  once, its ledger row written when it is sent;
- a task the bot owns that waits on a person with no plain ask (H.lint_person_ask): asked to restate it, relisted after
  RELIST_DAYS until it is restated;
- a Dev ticket late at its step, on its manager's list (backend/step_age.py): "Late in <step>: N days", relisted after
  RELIST_DAYS;
- an engineering exception on a manager's list that reads the exceptions list (backend/pipeline_exceptions.py), relisted
  after RELIST_DAYS;
- a step's backlog over its limit (backend/step_backlog.py), on each bot it names, on the oldest ticket waiting there,
  relisted after the step's every_hours;
- a missed update, then a quiet task, on a task the bot owns (below).
A missed start is a task the bot owns, live and readable to it, whose snooze ended at its deadline, or whose deferred time
passed, within the last three days and more than 15 minutes before the run, with no turn on the task and no delivery to it since
that time: one line per such time (START). A task waiting on bots only is listed on the list of each bot it waits on, not its
owner's, once per update of the task: every hold on it is on a bot (backend/task_holds.py), none on a person, nobody or a private
task (BLOCKED). A task a person holds gets no missed start. These two are sweep lines: a bot with its sweeps off gets neither.
A task is named once on a list, whatever its reason.

A missed update is a task the bot owns, live (H.ACTIVE_STATUSES), readable to it, not snoozed, not already being run for it
(a queued, leased or running job in its conversation), where someone other than the bot and Tico did something on it since
the later of the bot's last turn on it and the last time it was listed. That is a message written or edited in its conversation;
a change of the task itself (status, owner, step, waiting_on, due, title, body or acceptance; a task's creation row is not
news, but a step or status set later on a task that had none is); a comment edited or deleted; a blocker taken off it; a
blocker that finished (taken off by Tico: "a blocker is done", or kept on a roadmap item); or a subtask that finished, by
someone other than the bot. A status change Tico made (a hub move) counts too. Tico's other bookkeeping (check-backs, rank,
checkpoints, its wake notices) never counts.

A task is quiet when nothing has changed on it for a day, and nothing holds it: it does not wait on someone (waiting_on), no
live blocker holds it, nothing is recorded to wait on (H.recorded_wait, which includes an unanswered ask), and no follow-up
time is still to come. A task left in `waiting` that waits on nothing is quiet like any other. A quiet task is listed when it
was never listed or was last listed 7 or more days ago.

A follow-up time (tasks.check_back_at) is set only by a person or a bot: its check_back_explicit history row names the
time. When it has come, the task is listed first on its owner bot's list as "Follow-up time set by <who> has come: <note>"
(the note trimmed), and the time is then cleared, so it is listed once. Tico sets no follow-up time of its own, and a
task a person owns is on no bot's list. A date a snooze left behind is not a follow-up: it holds nothing, and a snooze in
force hides the task (task_snooze.snoozed) until it ends.

A private task is listed as "a private task <id>", without its title, its follow-up's who and note, its step, or what changed:
only that a follow-up time has come, that it has an update, that it has gone quiet, or the bare fact of a line. A bot whose
profile has its sweeps off (rules.sweeps_on: a reviewer, lead, responder or monitor) is listed its unanswered questions and
its own follow-up times only: never an idle, late, exception, backlog, assigned or restate line. A manager and a doer get all
of them. A late ticket or an exception is listed only where its bot reads the task (H.bot_reads_task). Only bots are listed:
a person a line would name is never told by the roundup.

A bot is sent at most one list per run: registry_metadata `task_roundup:<slug>` holds the run's time it was last sent a
list, and a bot already sent one since this run's time gets nothing more, so the roundup works at any cadence. Each line's
registry key is written as the time its list was built, not the run's time, so an update that lands after the run's time but
before the build is not listed again. All are written only once the message is delivered: a bot run by a computer is
delivered when its job is queued, and an external inbox (no computer, no job) when the message is written. A message a rule
held is not counted as sent. The list is written to the bot's own notice room with Tico, where no person is a member, so the
roundup never notifies a person.

The scheduler selects the bots in one read transaction (discover), then sends each one in its own short transaction (send),
which rebuilds that bot's list from the live database before it writes anything."""

import sqlite3
from datetime import datetime, timedelta, timezone

from .batch_work import isolated
from .store import H

WAKE = "task_roundup"
EVENT = "task_roundup.sent"
SENT_KEY = "task_roundup:"             # registry_metadata: the run's time a bot was last sent a list
LISTED_KEY = "task_roundup:task:"      # registry_metadata: the run's time a task was last on a list
HEADING = "Here is a list of tasks on your plate. You may want to look into them."
MAX_LINES = 25                         # lines in one list; the rest are counted
TITLE_CHARS = 80
QUIET_HOURS = 24
RELIST_DAYS = 7
FOLLOW_UP_CHARS = 160                  # a follow-up's note on its list line, trimmed to this
ASK_CHARS = 120                        # a question's text on its line, trimmed to this
ASK_DAYS = 30                          # an unanswered question is looked for this far back
CREATED_WITHIN = 1                     # seconds: a status or step row this close to its task's creation is the creation
MISSED_START_GRACE = timedelta(minutes=15)    # a snooze or deferred time must have passed this long before a missed start
MISSED_START_LOOKBACK = timedelta(days=3)     # a missed start older than this is history, not a line
DELIVERED_JOBS = ("input", "running", "uncertain", "completed")     # a job that reached its bot: a turn on the task started
# The lines' order on a list (a group sorts its own lines; a missed update newest first).
DUE, ASKS, ASSIGNED, START, RESTATE, LATE, EXCEPTIONS, BACKLOG, BLOCKED, MISSED, QUIET = range(11)
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
    """Discover and send in the caller's transaction (tests). Returns {"woke": [slugs], "listed": lines named in them}."""
    woke, listed = [], 0
    for plan in discover(c, at):
        count = send(c, plan)
        if count:
            woke.append(plan["slug"])
            listed += count
    return {"woke": woke, "listed": listed}


def _candidates(c, at, shared):
    """The bots a list could name this run: each that owns a live task, and each a shared line names (a late ticket's
    manager, an exception's manager, a backlog's targets, a question's asker). Any other bot has no line to list."""
    from . import still_needed, step_age, step_backlog
    actors = {row[0] for row in c.execute(
        "SELECT DISTINCT owner_id FROM tasks WHERE status IN (" + _marks() + ")", tuple(H.ACTIVE_STATUSES))}
    actors |= {row[0] for row in c.execute(
        "SELECT DISTINCT target FROM events WHERE action=? AND created_at>? AND json_extract(detail, '$.message') IS NULL",
        (still_needed.ACTION, H.shift(at, days=-ASK_DAYS)))}
    actors |= {item["to"] for item in _shared(shared, "late", lambda: step_age.late_items(c, at))}
    actors |= {H.bot_actor(manager) for _, manager in _shared(shared, "types", lambda: _engineering_types(c))}
    actors |= {H.bot_actor(slug) for slug in _shared(shared, "blocked", lambda: _blocked_waits(c, at))}
    actors |= {who for warning in _shared(shared, "backlog", lambda: step_backlog.warnings(c)) for who in warning["targets"]}
    return {H.actor_id(actor) for actor in actors if actor}


def discover(c, at=None):
    """The bots to visit for the run at `at`: active bots with a line to list that were not sent a list in this run. Read
    only. send builds each list again from the live database."""
    from .hubdb import _has_table
    if not _has_table(c, "jobs"):       # a hub without jobs has no wakes to send: nothing is listed
        return []
    at = at or H.now()
    shared, plans = {}, []
    wanted = _candidates(c, at, shared)
    for bot in H._rows(c.execute("SELECT id FROM bots WHERE state='active' ORDER BY id")):
        slug = bot["id"]
        if slug not in wanted or _sent_before(c, slug, at):
            continue
        items = _items(c, slug, at, shared)
        if items:
            plans.append({"slug": slug, "at": at, "lines": len(items),
                          "oldest": min(item["sort"] for item in items)})
    return plans


def send(c, plan):
    """Send one bot its list in the caller's transaction. The list is rebuilt here from the live database (discover only chose
    the bot), and the bot is sent only if it was not already sent one this run. A bot with nothing to list gets no message.
    The run's time, each line's registry key, the consumed follow-ups and the events are written only once the message is
    delivered. Returns how many lines the list named, or 0 when nothing was sent."""
    return send_detail(c, plan)[0]


def send_detail(c, plan, skip_tasks=(), *, allowed=None):
    """send, and the ids of the tasks its shown lines named: (lines, [task ids]), or (0, []) when nothing was sent. The
    Maintenance limiter counts a delivered list once, with these ids (backend/maintenance_steps/find_stuck.py). A task in
    skip_tasks or refused by allowed(task_id) has its lines named nowhere; allowed runs before the list is capped."""
    from . import run_triggers as RT, task_check_backs
    slug, at = plan["slug"], plan["at"]
    if _sent_before(c, slug, at) or not _active(c, slug):
        return 0, []
    built = _later(at, _built_at())        # stamped before the read: an update racing it is listed again, never missed
    text, count, shown = _list(c, slug, at, {}, skip_tasks, allowed=allowed)
    if not text:
        return 0, []
    actor = H.bot_actor(slug)
    with isolated(c, WAKE, slug):
        try:
            # no_mentions: a task's title may say "@ana", which is text in the list, not a mention, so no person is delivered it.
            message = H._write_message(c, H.KEEPER, actor, text, _room(c, slug), "notice",
                                       RT.stamp({"wake": WAKE, "no_mentions": True}, WAKE), None, None)
        except H.Refused:
            return 0, []               # not told this run: a later run may try again
        if not _delivered(c, slug, message):
            return 0, []               # a rule held it, so no bot was woken: not sent, and a later run may try again
        _set(c, SENT_KEY + slug, at)
        for item in shown:
            _set(c, item["mark"], built)
            if item["also"]:
                _set(c, item["also"], built)
            if item["event"]:
                H.event(c, H.KEEPER, *item["event"])
            if item["due"]:
                task_check_backs.consume(c, item["tid"], H.KEEPER)
        H.event(c, H.KEEPER, EVENT, slug, {"at": at, "tasks": count,
                                           "follow_ups": sum(1 for item in shown if item["due"])})
        return count, _task_ids(shown)
    return 0, []


def line_tasks(c, plan):
    """The task ids the bot's list would name this run, before any per-task limit.
    Read only: it runs in the caller's transaction and writes nothing."""
    _, _, shown = _list(c, plan["slug"], plan["at"], {})
    return _task_ids(shown)


def _task_ids(shown):
    """The distinct task ids the shown lines name, in list order (a line that names no task names none)."""
    out = []
    for item in shown:
        if item["tid"] and item["tid"] not in out:
            out.append(item["tid"])
    return out


def _set(c, key, value):
    c.execute("INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def _mark(c, key):
    row = c.execute("SELECT value FROM registry_metadata WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def _sent_before(c, slug, at):
    """Whether this bot was already sent a list in the run at `at` (its last list is at or after that run's time)."""
    row = c.execute("SELECT value FROM registry_metadata WHERE key=?", (SENT_KEY + slug,)).fetchone()
    return bool(row) and H.parse_ts(row[0]) >= H.parse_ts(at)


def _active(c, slug):
    return c.execute("SELECT 1 FROM bots WHERE id=? AND state='active'", (slug,)).fetchone() is not None


def _listed(c, tid):
    return _mark(c, LISTED_KEY + tid)


def _stale(c, key, at, relist):
    """Whether a line keyed `key` was last sent `relist` before `at`, or never: it is on the list again then."""
    listed = _mark(c, key)
    return listed is None or H.parse_ts(listed) <= H.parse_ts(at) - relist


def _key(kind, slug, ref):
    """The registry key of one line kind for one bot: a task, an ask, an exception or a backlog it is listed for."""
    return f"{SENT_KEY}{kind}:{slug}:{ref}"


def _later(*times):
    """The latest of the times that are set, or "" when none is."""
    return max((t for t in times if t), default="")


def _built_at():
    """The time a list is built, stamped as the checkpoint of each line on it. Tests pin it (it is the wall clock otherwise)."""
    return H.now()


def _room(c, slug):
    """The bot's own notice room with Tico: the keeper and the bot are its only members, so a list reaches the bot and no
    person is told about a task. (rules._bot_room picks the operator's room or a shared room, where people are.)"""
    return H._pair_conversation(c, H.KEEPER, H.bot_actor(slug), "notice", allow_held=True)


def _shared(shared, name, make):
    """A read the whole run shares: made once, by the first bot that needs it."""
    if name not in shared:
        shared[name] = make()
    return shared[name]


def _item(tid, line, key, *, due=False, event=None, also=None):
    """One line on a list: the task it names (None when it names none), its text, the registry key it is recorded under
    once sent, whether it consumes a due follow-up, an event written with it (action, target, detail) or None, and the
    task's quiet-clock key (`also`) when the line lists the task as a whole (a missed start)."""
    return {"tid": tid, "line": line, "mark": key, "due": due, "event": event, "also": also}


def _list(c, slug, at, shared, skip_tasks=(), *, allowed=None):
    """The bot's list as one message's text, how many lines it has, and the items shown on it (each with the registry key it
    is recorded under once sent). (None, 0, []) when nothing needs a look. Task limits apply before the display cap."""
    ordered = _items(c, slug, at, shared, skip_tasks, allowed=allowed)
    if not ordered:
        return None, 0, []
    shown = ordered[:MAX_LINES]
    lines = [HEADING, *(item["line"] for item in shown)]
    if len(ordered) > len(shown):
        lines.append(f"- and {len(ordered) - len(shown)} more")
    return "\n".join(lines), len(ordered), shown


def _items(c, slug, at, shared, skip_tasks=(), *, allowed=None):
    """All eligible lines in list order, one per task, before the display cap."""
    from . import rules, task_snooze as TS
    actor = H.bot_actor(slug)
    now = H.parse_ts(at)
    cutoff = now - timedelta(hours=QUIET_HOURS)
    relist = now - timedelta(days=RELIST_DAYS)
    # A bot with sweeps off is listed its questions and its own follow-up times only (the module's docstring).
    sweeping = rules.sweeps_on(c, slug)
    groups = {group: [] for group in range(QUIET + 1)}
    rows = H._rows(c.execute(
        "SELECT * FROM " + H.TASKS + " tasks WHERE owner_id=? AND status IN (" + _marks() + ") ORDER BY updated_at",
                             (actor, *H.ACTIVE_STATUSES)))
    for row in rows:
        if not H.task_private_readable(c, actor, row) or TS.snoozed(row, at) or _running(c, slug, row):
            continue
        private = H.task_private(c, row)
        follow = follow_up(c, row)
        if follow and H.parse_ts(row["check_back_at"]) <= now:
            groups[DUE].append((row["check_back_at"], _item(row["id"], _line(row, _due_reason(follow, private), private),
                                                           LISTED_KEY + row["id"], due=True)))
            continue
        if not sweeping:
            continue
        person = _restate(c, row)
        if person and _stale(c, _key("restate", slug, row["id"]), at, timedelta(days=RELIST_DAYS)):
            groups[RESTATE].append((row["created_at"] or "", _item(row["id"], _line(
                row, _restate_reason(row, person, private), private), _key("restate", slug, row["id"]))))
        last_turn = _last_turn(c, row, slug, actor)
        listed = _listed(c, row["id"])
        news = _news(c, row, actor, _later(last_turn, listed))
        if news:
            count, when, what = news
            groups[MISSED].append((when, _item(row["id"], _line(row, _missed_reason(count, what, private), private),
                                               LISTED_KEY + row["id"])))
            continue
        quiet_since = _later(row["updated_at"], last_turn, _latest(c, row))
        if H.parse_ts(quiet_since) <= cutoff and not _holds(c, row, at) and (listed is None or H.parse_ts(listed) <= relist):
            span = _days(now - H.parse_ts(quiet_since))
            reason = (f"Waiting on nothing for {span}" if row["status"] == "waiting"
                      else f"Quiet for {span}, nothing waiting on anyone")
            groups[QUIET].append((quiet_since, _item(row["id"], _line(row, reason, private), LISTED_KEY + row["id"])))
    sweep_lines = (_assigned(c, slug, actor, at) + _missed_starts(c, slug, actor, at) + _late(c, slug, actor, at, shared)
                   + _exceptions(c, slug, actor, at, shared) + _backlogs(c, slug, actor, at, shared)
                   + _blocked_by(c, slug, actor, at, shared)) if sweeping else []
    for group, sort, item in _asks_back(c, slug, actor, at) + sweep_lines:
        groups[group].append((sort, item))
    ordered, seen = [], set()
    for group in range(QUIET + 1):
        for sort, item in sorted(groups[group], key=lambda found: found[0], reverse=group == MISSED):
            if item["tid"] is not None:
                if item["tid"] in seen:         # a task is named once on a list
                    continue
                seen.add(item["tid"])
                if item["tid"] in skip_tasks or (allowed is not None and not allowed(item["tid"])):
                    continue
            item["sort"] = _when(sort)
            ordered.append(item)
    return ordered


def _when(value):
    """A line's sort time as an aware datetime, the oldest possible when it has none, so lines of every kind compare."""
    found = H.parse_ts(str(value)) if value else None
    if found is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    return found if found.tzinfo else found.replace(tzinfo=timezone.utc)


def _missed_starts(c, slug, actor, at):
    """A snooze that ended at its deadline, or a deferred time that passed, within MISSED_START_LOOKBACK and more than
    MISSED_START_GRACE before the run, on a live task the bot owns (a deferral it made on a task it owns), with no turn on the
    task and no delivery to it since that time: one line per such time. A deferred task must still wait for its next run,
    with no resume condition holding it (H.next_run_waiting)."""
    from . import task_snooze as TS
    since = H.shift(at, seconds=-int(MISSED_START_LOOKBACK.total_seconds()))
    before = H.shift(at, seconds=-int(MISSED_START_GRACE.total_seconds()))
    marks = _marks()
    # A snooze's appointment is its deadline (the row's old value), not the sweep that wrote its end.
    ended = H._rows(c.execute(
        f"SELECT e.old AS appointment, t.* FROM history_events e JOIN {H.TASKS} t ON t.id=e.subject_id "
        "WHERE e.subject_type='task' AND e.kind='snoozed_until' AND e.new IS NULL AND e.note='snooze over' "
        f"AND e.old>? AND e.old<=? AND t.owner_id=? AND t.status IN ({marks}) ORDER BY e.old",
        (since, before, actor, *H.ACTIVE_STATUSES)))
    deferred = H._rows(c.execute(
        f"SELECT d.due_at AS appointment, t.* FROM deferrals d JOIN {H.TASKS} t ON t.id=d.item "
        "WHERE d.kind='task' AND d.bot=? AND t.owner_id=? AND d.settled_at IS NULL AND d.fired_job IS NULL "
        f"AND d.due_at IS NOT NULL AND d.due_at>? AND d.due_at<=? AND t.status IN ({marks}) ORDER BY d.due_at",
        (slug, actor, since, before, *H.ACTIVE_STATUSES)))
    external = _external(c, slug)     # an external inbox has no job: a message written to it is its delivery (_delivered)
    out = []
    for what, rows in (("Snooze ended", ended), ("Deferred time passed", deferred)):
        for row in rows:
            appointment = row["appointment"]
            key = _key("start", slug, f"{row['id']}:{appointment}")
            if (_mark(c, key) is not None or TS.snoozed(row, at) or not H.task_private_readable(c, actor, row)
                    or (what == "Deferred time passed" and not H.next_run_waiting(c, row))
                    or _turned_since(c, row, appointment, actor, external) or _running(c, slug, row) or _person_held(c, row)):
                continue
            reason = f"{what} at {_clock(appointment)} and nothing has picked it up"
            out.append((START, appointment, _item(row["id"], _line(row, reason, H.task_private(c, row)), key,
                                                  also=LISTED_KEY + row["id"])))
    return out


def _turned_since(c, row, since, actor, external):
    """Whether a turn on the task, or a delivery in its conversation, started since `since` (a snooze's or deferred time). An
    external inbox has no job, so a message written to it since then is its delivery (the _delivered rule)."""
    try:
        if c.execute("SELECT 1 FROM attempts WHERE task_id=? AND created_at>=? LIMIT 1", (row["id"], since)).fetchone():
            return True
        if external:
            return c.execute("SELECT 1 FROM messages WHERE conversation_id=? AND recipient_id=? AND created_at>=? "
                             "AND deleted_at IS NULL LIMIT 1", (row["conversation_id"], actor, since)).fetchone() is not None
        marks = ",".join("?" * len(DELIVERED_JOBS))
        return c.execute(f"SELECT 1 FROM jobs j JOIN messages m ON m.id=j.message_id WHERE m.conversation_id=? "
                         f"AND m.created_at>=? AND j.state IN ({marks}) LIMIT 1",
                         (row["conversation_id"], since, *DELIVERED_JOBS)).fetchone() is not None
    except sqlite3.OperationalError:    # a local hub has no attempts or jobs table
        return False


def _clock(ts):
    return H.parse_ts(ts).strftime("%H:%M") + " UTC"


def _person_held(c, row):
    """Whether a person holds the task: a hold on a person, on nobody, or on a task the reader cannot see (backend/task_holds.py)."""
    from . import task_holds as TH
    return any(h["on"] is None or H.is_human(h["on"]) or h["kind"] == "private" for h in TH.holds(c, row))


def _blocked_waits(c, at):
    """{bot id: [(task row, what)]}: each waiting task a bot owns, of an active bot, whose every hold is on a bot
    (backend/task_holds.py), under the bot it waits on. The owner and active-bot filters are in the query, so a person's
    task never reaches task_holds. Each recipient's mark suppresses only that recipient's line for this update.
    Read once per run (the shared reads of discover and of each send)."""
    from . import task_holds as TH, task_snooze as TS
    out = {}
    rows = H._rows(c.execute("SELECT tasks.* FROM " + H.TASKS + " tasks JOIN bots b ON tasks.owner_id='bot:'||b.id "
                             "WHERE tasks.status='waiting' AND tasks.owner_id LIKE 'bot:%' AND b.state='active' "
                             "ORDER BY tasks.updated_at"))
    for row in rows:
        if TS.snoozed(row, at):
            continue
        found = TH.holds(c, row)
        if not found or any(h["kind"] == "private" or not h["on"] or not H.is_bot(h["on"]) for h in found):
            continue
        whats = {}
        for hold in found:
            whats.setdefault(H.actor_id(hold["on"]), hold["what"])
        for slug, what in whats.items():
            if _mark(c, _key("blocked", slug, f"{row['id']}:{row['updated_at']}")) is None:
                out.setdefault(slug, []).append((row, what))
    return out


def _blocked_by(c, slug, actor, at, shared):
    """A waiting task that the bot's own work holds, on this bot's list (the bot it waits on), not its owner's: listed once per
    update of the task, since its key carries the task's updated_at. A task the bot cannot read (its privacy, or
    H.bot_reads_task), or one it is already running for, is named nowhere."""
    out = []
    for row, what in _shared(shared, "blocked", lambda: _blocked_waits(c, at)).get(slug, []):
        key = _key("blocked", slug, f"{row['id']}:{row['updated_at']}")
        if (_mark(c, key) is not None or not H.task_private_readable(c, actor, row) or not H.bot_reads_task(c, actor, row)
                or _running(c, slug, row)):
            continue
        private = H.task_private(c, row)
        reason = "Waiting on you" + (f": {_clip(what, ASK_CHARS)}" if what and not private else "")
        out.append((BLOCKED, row["updated_at"] or "", _item(row["id"], _line(row, reason, private), key)))
    return out


def _asks_back(c, slug, actor, at):
    """Questions this bot asked a person that went back unanswered (backend/still_needed.py): one line each, listed once.
    An event with a `message` was sent to the bot as a chat message before the roundup, so it is not listed again."""
    from . import still_needed
    found = c.execute("SELECT e.created_at, j.value FROM events e, json_each(e.detail, '$.asks') j "
                      "WHERE e.action=? AND e.target=? AND e.created_at>? AND json_extract(e.detail, '$.message') IS NULL "
                      "ORDER BY e.created_at, j.key",
                      (still_needed.ACTION, actor, H.shift(at, days=-ASK_DAYS))).fetchall()
    out = []
    for when, ask_id in found:
        key = _key("ask", slug, ask_id)
        ask = c.execute("SELECT id, body, recipient_id, refs, conversation_id FROM messages WHERE id=?", (ask_id,)).fetchone()
        if not ask or _mark(c, key) is not None:
            continue
        who = H.actor_name(c, ask["recipient_id"]) or H.actor_id(ask["recipient_id"])
        # The task the question is about, as the message names it (its refs, else its task conversation): a question
        # asked from a Home room names its task in refs only.
        tid = H.message_task_id({"refs": H._json(ask["refs"], {})}, H.conversation(c, ask["conversation_id"]))
        task = H.task(c, tid) if tid else None
        # A task the bot may not read by every rule (H.bot_reads_task) is named nowhere, as the late lines are.
        if task and not H.bot_reads_task(c, actor, task):
            continue
        if task and H.task_private(c, task):
            line = (f"- a private task {task['id'][:8]}: a question to a person had no answer in two business days; "
                    "ask again as a fresh question with a date, or drop it.")
            out.append((ASKS, when, _item(task["id"], line, key)))
            continue
        reason = (f'no answer in two business days to "{_clip(ask["body"], ASK_CHARS)}" ({who}): ask again as a fresh '
                  "question with a date, or drop it.")
        out.append((ASKS, when, _item(task["id"] if task else None,
                                      _line(task or {"id": ask["id"], "title": f"Question to {who}"}, reason, False), key)))
    return out


def _restate(c, row):
    """The person a waiting task of the bot's needs restated, or None: the note reads as no plain ask (H.lint_person_ask),
    and no open question to that person already holds it."""
    person = row.get("waiting_on") or ""
    if row["status"] != "waiting" or not person.startswith("human:") or not H.lint_person_ask(row.get("note")):
        return None
    if any(a["recipient_id"] == person for a in H.open_task_asks(c, row)):
        return None
    return person


def _restate_reason(row, person, private):
    """The restate line's reason. A private task says no person's name."""
    if private:
        return ("Waiting on a person with no plain ask. Restate it, at most "
                f"{H.LINT_ASK_WORDS} words, with: hub task update {row['id']} --status waiting --on <person> "
                "--note \"<your ask>\".")
    who = H.actor_id(person)
    return (f"Waiting on {who} with no plain ask. Restate it in one or two plain sentences, at most {H.LINT_ASK_WORDS} words: "
            f"what you need from {who} and what happens after. Set it with: hub task update {row['id']} --status waiting "
            f"--on {who} --note \"<your ask>\".")


def _assigned(c, slug, actor, at):
    """The bot's open assignments no run has started and no list has named (backend/assigned_start.py), one line each, listed
    once. The line writes the assignment's ledger row when it is sent, so the health check's "not started" still reads it."""
    from . import assigned_start, task_snooze as TS
    out = []
    for row, key, since, _ in assigned_start.unstarted(c, at, owner=actor):
        if not H.task_private_readable(c, actor, row) or TS.snoozed(row, at) or _running(c, slug, row):
            continue
        private = H.task_private(c, row)
        event = (assigned_start.EVENT, row["id"], {"assignment": key, "assigned_at": since, "rewoken_at": at, "refusal": None})
        out.append((ASSIGNED, since, _item(row["id"], _line(row, "Assigned, not started", private), LISTED_KEY + row["id"],
                                           event=event)))
    return out


def _late(c, slug, actor, at, shared):
    """Dev tickets late at their step, on their manager's list (backend/step_age.py), relisted after RELIST_DAYS."""
    from . import step_age
    out = []
    for item in _shared(shared, "late", lambda: step_age.late_items(c, at)):
        row = item["row"]
        # A ticket its manager cannot read by its parties' bot access (H.bot_reads_task) is named nowhere, not even as private.
        if (item["to"] != actor or not H.task_private_readable(c, actor, row) or not H.bot_reads_task(c, actor, row)
                or _running(c, slug, row)):
            continue
        key = _key("late", slug, row["id"])
        if not _stale(c, key, at, timedelta(days=RELIST_DAYS)):
            continue
        private = H.task_private(c, row)
        reason = (f"Late at its step: {_span(item['hours'])}" if private
                  else f"Late in {item['step']}: {_span(item['hours'])}")
        out.append((LATE, item["since"], _item(row["id"], _line(row, reason, private), key)))
    return out


def _exceptions(c, slug, actor, at, shared):
    """A manager's engineering exceptions (backend/pipeline_exceptions.py) on the list it reads; each is relisted after
    RELIST_DAYS while it stands. A manager that does not read the list gets none."""
    from . import pipeline_exceptions
    if not pipeline_exceptions.reads_list(c, slug):
        return []
    out = []
    for typ, manager in _shared(shared, "types", lambda: _engineering_types(c)):
        if manager != slug:
            continue
        for entry in pipeline_exceptions.build(c, typ, at=at, reader=actor):
            key = _key("exception", slug, entry["key"])
            if not _stale(c, key, at, timedelta(days=RELIST_DAYS)):
                continue
            since = str(entry["since"])[:16].replace("T", " ") + "Z"
            row = H.task(c, entry["ticket"]["id"]) if (entry.get("ticket") or {}).get("id") else None
            if row:
                private = H.task_private(c, row)
                reason = (f"{entry['kind']} exception, since {since}" if private
                          else f"{entry['cause']}, since {since}")
                line, tid = _line(row, reason, private), row["id"]
            else:
                subject = entry.get("bot") or entry.get("step") or entry["kind"]
                line, tid = f"- {subject}: {entry['cause']}, since {since}", None
            out.append((EXCEPTIONS, entry["since"], _item(tid, line, key)))
    return out


def _engineering_types(c):
    """(type, its manager's bot id) for each engineering type that has a manager."""
    from .roadmap_flow import ENGINEERING
    out = []
    for typ in H._rows(c.execute("SELECT * FROM task_types WHERE role=? AND manager IS NOT NULL ORDER BY id", (ENGINEERING,))):
        out.append((typ, (H.resolve_bot(c, typ["manager"]) or {}).get("id", typ["manager"])))
    return out


def _backlogs(c, slug, actor, at, shared):
    """A step over its backlog (backend/step_backlog.py), on each bot it names, on the oldest ticket waiting there. Relisted
    after the step's every_hours while it stays over. A snoozed oldest ticket lists nothing for its owner, as its wake did before."""
    from . import step_backlog, task_snooze as TS
    out = []
    for warning in _shared(shared, "backlog", lambda: step_backlog.warnings(c)):
        if actor not in warning["targets"]:
            continue
        first = warning["first"]
        key = _key("backlog", slug, warning["key"])
        snoozed_for_owner = actor == first.get("owner_id") and TS.snoozed(first, at)
        if (not H.task_private_readable(c, actor, first) or H.task_private(c, first) or snoozed_for_owner
                or not _stale(c, key, at, timedelta(hours=warning["every_hours"]))):
            continue
        event = (step_backlog.EVENT, first["id"], {"key": warning["key"], "step": warning["step"],
                                                   "owner": warning["owner"], "count": warning["count"], "told": [actor]})
        out.append((BACKLOG, first.get("created_at") or "", _item(first["id"], _line(first, warning["text"], False), key,
                                                                  event=event)))
    return out


def _clip(text, limit):
    return " ".join(str(text or "").split())[:limit]


def _span(hours):
    """How long a ticket has been at its step: hours under two days, then days."""
    if hours < 48:
        return f"{int(hours)} h"
    return f"{int(hours // 24)} days"


def follow_up(c, row):
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
    later = bool(follow_up(c, row) and H.parse_ts(row["check_back_at"]) > H.parse_ts(at))
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
