"""Find stuck: the Maintenance step that wakes bots about waiting work, with one grouped list per bot per run.

Plain code: no model is called and no task is filed. A run gathers every item a bot should look at, then wakes each chosen
bot once with one list. At most `max_bots` bots are woken a run (the step's "Most bots woken per run", MAX_BOTS by
default), the bots with the oldest items first; the rest roll over to the next run.

The kinds, in the order a list shows them:
- due: a task's follow-up time, set by a person or a bot, has come. Not listed on a task that waits on a person under any
  hold kind. The time is consumed once the list is delivered, so a time set again is listed again.
- missed: a snooze ended, or a deferred time passed, more than GRACE ago, and no turn or delivery on the task has started
  since. Listed once for that time.
- blocked: a bot's waiting task whose every hold is on a bot (task_holds.holds). Each bot a hold is on is woken about the
  task; the task's owner is not. A hold on a person, on nobody, on a private task, or on a bot that cannot read the task
  keeps the task off the list.
- unanswered: a bot's question to a person with no answer for two business days (still_needed). Delivered once; the question
  then goes back to its bot and off the person's Needs you.
- restate: a bot set a task waiting on a person without a plain ask. Its bot is asked once to restate it (restate_asks).
- stale: a bot-owned task that stale_review's candidate rule finds, while the team's stale_review rule is on. Chosen in code;
  no model judges it.
- quiet: today's rule (task_roundup): nothing has happened on the task for a day and nothing holds it.

Rules for every kind:
- Nothing goes to a person. A list is written to the bot's own notice room with Tico, where no person is a member.
- A task a person holds is never listed by due, missed, blocked, stale or quiet, under any hold kind. Two kinds are the
  bot's own correction and are folded in from still_needed and restate_asks, so they name a task waiting on a person once
  each: unanswered (the bot's question to a person, after two business days, then taken back) and restate (the bot's
  waiting note with no plain ask, once).
- A task is listed once per appointment. The key is (task, kind, appointment) in maintenance_listings, where the
  appointment is the follow-up time (due), the snooze or deferred time (missed), the task's updated_at with the blocked
  bot (blocked), or the task's updated_at (stale and quiet). A stale, quiet or blocked task is also not listed again to the
  same bot until it changes after its last listing, whichever of those kinds listed it.
- A bot is woken only through backend/maintenance_limits.py (allow, then record for each task listed).
"""

import sqlite3
from datetime import timedelta

from .. import hubdb as H
from .. import maintenance_limits as LIMITS
from .. import restate_asks, rules, run_triggers as RT, stale_review, still_needed, task_check_backs
from .. import task_holds as TH
from .. import task_roundup as TRU
from .. import task_snooze as TS
from ..batch_work import isolated

WAKE = "find_stuck"                  # the wake's name in its refs
TRIGGER = "task_roundup"             # run_triggers has no find-stuck kind yet: the list is the roundup's list
EVENT = "find_stuck.sent"
HEADING = TRU.HEADING
MAX_BOTS = 10                        # the default "Most bots woken per run"
MAX_LINES = 25                       # task lines in one list; the rest are counted and roll over
GRACE = timedelta(minutes=15)        # a snooze or deferred time must be this old before it is missed
LOOKBACK = timedelta(days=1)         # missed starts older than this are history, not a list
QUIET_HOURS = 24
ACTIVE = H.ACTIVE_STATUSES
# PR1 owns this table; it is created here, with the same shape, until PR1 lands.
LISTINGS = ("CREATE TABLE IF NOT EXISTS maintenance_listings("
            "task_id TEXT NOT NULL, kind TEXT NOT NULL, appointment TEXT NOT NULL, bot_id TEXT NOT NULL, "
            "listed_at TEXT NOT NULL, PRIMARY KEY(task_id, kind, appointment))")
KINDS = ("due", "missed", "blocked", "unanswered", "restate", "stale", "quiet")
RANK = {kind: n for n, kind in enumerate(KINDS)}
# The kinds a task is not listed again to the same bot for until it changes.
CHANGE_KINDS = ("blocked", "stale", "quiet")
DELIVERED_JOBS = ("input", "running", "uncertain", "completed")


def run(c, ctx):
    """One pass: gather the items, then wake each chosen bot once with its list. `ctx` is the Maintenance run's context
    ({now, settings, ...}). Returns the step result: status, reason, checked, found, woke, cost_usd and details."""
    ctx = ctx or {}
    at = ctx.get("now") or H.now()
    settings = ctx.get("settings") or {}
    max_bots = max(1, int(settings.get("max_bots") or MAX_BOTS))
    c.execute(LISTINGS)
    items, checked = _gather(c, at)
    by_bot = _by_bot(items)
    woken, listed = [], 0
    for slug in sorted(by_bot, key=lambda s: (_oldest(by_bot[s]), s)):
        if len(woken) >= max_bots:
            break
        count = _send(c, slug, by_bot[slug], at)
        if count:
            woken.append(slug)
            listed += count
    return {"status": "ran", "reason": "" if woken else "nothing to list", "checked": checked, "found": len(items),
            "woke": len(woken), "cost_usd": 0.0,
            "details": {"woken": woken, "listed": listed, "bots_waiting": len(by_bot) - len(woken)}}


# ----------------------------------------------------------------------------- gathering
def _gather(c, at):
    """Every new item the bots should look at, and how many task rows were looked at."""
    now = H.parse_ts(at)
    awake = {slug for slug in _active_bots(c) if rules.sweeps_on(c, slug)}
    items, checked = [], 0
    for slug in sorted(awake):
        rows = H._rows(c.execute("SELECT * FROM " + H.TASKS + " tasks WHERE owner_id=? AND status IN (" + TRU._marks()
                                 + ") ORDER BY updated_at", (H.bot_actor(slug), *ACTIVE)))
        for row in rows:
            checked += 1
            items += _own(c, slug, row, at, now)
    items += _missed(c, at, awake)
    items += _blocked(c, at, awake)
    items += _unanswered(c, at, awake)
    items += _restate(c, awake)
    items += _stale(c, at, awake)
    return items, checked


def _own(c, slug, row, at, now):
    """A bot's own task: its due follow-up, else its quiet line. Nothing for a task it cannot read, one snoozed, or one
    already being run for it."""
    actor = H.bot_actor(slug)
    if not H.task_private_readable(c, actor, row) or TS.snoozed(row, at) or TRU._running(c, slug, row):
        return []
    follow = TRU._follow_up(c, row)
    if follow and H.parse_ts(row["check_back_at"]) <= now:
        if _person_held(c, row):
            return []
        private = H.task_private(c, row)
        line = TRU._line(row, TRU._due_reason(follow, private), private)
        return _new_item(c, "due", slug, row["id"], row["check_back_at"], row["check_back_at"], line)
    if _person_held(c, row):
        return []
    updated = row["updated_at"] or ""
    last_turn = TRU._last_turn(c, row, slug, actor)
    quiet_since = TRU._later(updated, last_turn, TRU._latest(c, row))
    if H.parse_ts(quiet_since) > now - timedelta(hours=QUIET_HOURS) or TRU._holds(c, row, at):
        return []
    span = TRU._days(now - H.parse_ts(quiet_since))
    reason = (f"Waiting on nothing for {span}" if row["status"] == "waiting"
              else f"Quiet for {span}, nothing waiting on anyone")
    private = H.task_private(c, row)
    return _new_item(c, "quiet", slug, row["id"], updated, quiet_since, TRU._line(row, reason, private), updated=updated)


def _missed(c, at, awake):
    """Snoozes that ended, and deferred tasks whose time passed unfired, with no turn or delivery on the task since."""
    now = H.parse_ts(at)
    since = H.shift(at, seconds=-int(LOOKBACK.total_seconds()))
    before = H.shift(at, seconds=-int(GRACE.total_seconds()))
    out = []
    ended = H._rows(c.execute(
        f"SELECT e.created_at AS appointment, t.* FROM history_events e JOIN {H.TASKS} t ON t.id=e.subject_id "
        "WHERE e.subject_type='task' AND e.kind='snoozed_until' AND e.new IS NULL AND e.note='snooze over' "
        f"AND e.created_at>? AND e.created_at<=? AND t.status IN ({TRU._marks()}) ORDER BY e.created_at",
        (since, before, *ACTIVE)))
    deferred = H._rows(c.execute(
        f"SELECT d.bot AS deferred_bot, d.due_at AS appointment, t.* FROM deferrals d JOIN {H.TASKS} t ON t.id=d.item "
        "WHERE d.kind='task' AND d.settled_at IS NULL AND d.fired_job IS NULL AND d.due_at IS NOT NULL "
        f"AND d.due_at>? AND d.due_at<=? AND t.status IN ({TRU._marks()}) ORDER BY d.due_at",
        (since, before, *ACTIVE)))
    for row in ended + deferred:
        slug = row.get("deferred_bot") or (H.actor_id(row["owner_id"]) if H.is_bot(row["owner_id"]) else None)
        if slug not in awake or not H.task_private_readable(c, H.bot_actor(slug), row):
            continue
        if TRU._running(c, slug, row) or _received(c, row, row["appointment"]) or _person_held(c, row):
            continue
        private = H.task_private(c, row)
        when = _clock(row["appointment"])
        what = "Snooze ended" if "deferred_bot" not in row else "Deferred time passed"
        line = TRU._line(row, f"{what} at {when} and nothing has picked it up", private)
        out += _new_item(c, "missed", slug, row["id"], row["appointment"], row["appointment"], line)
    return out


def _blocked(c, at, awake):
    """Waiting tasks every one of whose holds is on a bot: each such bot is woken about the task, not the owner."""
    out = []
    for row in H._rows(c.execute("SELECT * FROM " + H.TASKS + " tasks WHERE status='waiting' ORDER BY updated_at")):
        if not H.is_bot(row["owner_id"]) or TS.snoozed(row, at):
            continue
        found = TH.holds(c, row)
        if not found or any(h["kind"] == "private" or not h["on"] or not H.is_bot(h["on"])
                            or not H.task_private_readable(c, h["on"], row) for h in found):
            continue
        woken = set()
        for hold in found:
            slug = H.actor_id(hold["on"])
            if slug in woken or slug not in awake:
                continue
            woken.add(slug)
            private = H.task_private(c, row)
            what = _line_text(hold["what"])
            line = TRU._line(row, "Waiting on you" + (f": {what}" if what else ""), private)
            out += _new_item(c, "blocked", slug, row["id"], f"{slug}|{row['updated_at']}", row["updated_at"] or "", line,
                             updated=row["updated_at"] or "")
    return out


def _unanswered(c, at, awake):
    """Bot questions to people, unanswered two business days, on the weekday morning batch (still_needed)."""
    if not still_needed.batch_due(at):
        return []
    out = []
    for asked, task in still_needed.crossed(c, at):
        slug = H.actor_id(asked["sender_id"]) if H.is_bot(asked["sender_id"]) else None
        if slug not in awake:
            continue
        private = bool(task) and H.task_private(c, task)
        key = task["id"] if task else asked["id"]
        line = still_needed._line(asked, None if private else task)
        out += _new_item(c, "unanswered", slug, key, asked["id"], asked["created_at"], line, asks=[(asked, task)])
    return out


def _restate(c, awake):
    """Bot tasks waiting on a person with no plain ask: the bot is asked once to restate it (restate_asks)."""
    out = []
    for row in restate_asks.unexplained(c):
        slug = H.actor_id(row["owner_id"])
        if slug not in awake:
            continue
        person = H.actor_id(row["waiting_on"])
        reason = (f"Waiting on {person} with no plain ask: restate it in one or two plain sentences, with "
                  f"hub task update {row['id']} --status waiting --on {person} --note \"<your ask>\"")
        line = TRU._line(row, reason, H.task_private(c, row))
        out += _new_item(c, "restate", slug, row["id"], row["updated_at"] or "", row["updated_at"] or "", line, row=row)
    return out


def _stale(c, at, awake):
    """Bot-owned tasks that stale_review's candidate rule finds, while the team's stale_review rule is on."""
    if not stale_review.enabled(c):
        return []
    out = []
    for row, why in stale_review.candidates(c, at):
        if not H.is_bot(row["owner_id"]):
            continue
        slug = H.actor_id(row["owner_id"])
        if slug not in awake or TRU._running(c, slug, row) or _person_held(c, row):
            continue
        line = TRU._line(row, f"Stale check: {why}. Update it, close it, or say why it is still open", False)
        updated = row["updated_at"] or ""
        out += _new_item(c, "stale", slug, row["id"], updated, updated, line, updated=updated)
    return out


# ----------------------------------------------------------------------------- what counts
def _person_held(c, row):
    """Whether a person holds the task: a hold on a person, on nobody, or on a task the reader cannot see."""
    return any(h["on"] is None or H.is_human(h["on"]) or h["kind"] == "private" for h in TH.holds(c, row))


def _received(c, row, since):
    """Whether a turn or a delivery on the task has started since `since` (a snooze's or deferred time)."""
    try:
        if c.execute("SELECT 1 FROM attempts WHERE task_id=? AND created_at>=? LIMIT 1", (row["id"], since)).fetchone():
            return True
        marks = ",".join("?" * len(DELIVERED_JOBS))
        return c.execute(f"SELECT 1 FROM jobs j JOIN messages m ON m.id=j.message_id WHERE m.conversation_id=? "
                         f"AND m.created_at>=? AND j.state IN ({marks}) LIMIT 1",
                         (row["conversation_id"], since, *DELIVERED_JOBS)).fetchone() is not None
    except sqlite3.OperationalError:    # a local hub has no attempts or jobs table
        return False


def _new_item(c, kind, slug, task, appointment, when, line, updated=None, **extra):
    """The item as a one-element list when it is new for its bot, else []. An item is new when its (task, kind,
    appointment) is not listed. A change kind is also not new when the bot was listed the task since it last changed."""
    if c.execute("SELECT 1 FROM maintenance_listings WHERE task_id=? AND kind=? AND appointment=?",
                 (task, kind, appointment)).fetchone():
        return []
    if updated is not None and kind in CHANGE_KINDS and c.execute(
            "SELECT 1 FROM maintenance_listings WHERE task_id=? AND bot_id=? AND listed_at>=? LIMIT 1",
            (task, slug, updated)).fetchone():
        return []
    return [{"kind": kind, "bot": slug, "task": task, "appointment": appointment, "when": when or "", "line": line,
             **extra}]


def _by_bot(items):
    """The items per bot, each bot's list in kind order then oldest first, one line per task (its first kind)."""
    grouped = {}
    for item in items:
        grouped.setdefault(item["bot"], []).append(item)
    out = {}
    for slug, rows in grouped.items():
        rows.sort(key=lambda item: (RANK[item["kind"]], item["when"], item["task"]))
        seen, kept = set(), []
        for item in rows:
            if item["task"] not in seen:
                seen.add(item["task"])
                kept.append(item)
        out[slug] = kept
    return out


def _oldest(items):
    return min(item["when"] for item in items)


# ----------------------------------------------------------------------------- sending
def _send(c, slug, items, at):
    """Wake `slug` once with its list. Returns how many items the list names, or 0 when nothing was delivered."""
    actor = _wake_actor(slug)
    allowed = [item for item in items if LIMITS.allow(c, slug, item["task"])]
    shown = allowed[:MAX_LINES]
    if not shown:
        return 0
    lines = [HEADING, *(item["line"] for item in shown)]
    if len(allowed) > len(shown):
        lines.append(f"- and {len(allowed) - len(shown)} more")
    with isolated(c, WAKE, slug):
        try:
            # no_mentions: a task's title may say "@ana", which is text in the list, not a mention.
            message = H._write_message(c, H.KEEPER, actor, "\n".join(lines), TRU._room(c, slug), "notice",
                                       RT.stamp({"wake": WAKE, "no_mentions": True}, TRIGGER), None, None)
        except H.Refused:
            return 0                    # not told this run: a later run may try again
        if not TRU._delivered(c, slug, message):
            return 0                    # no job was queued for the bot, so it was not woken: a later run may try again
        for item in shown:
            _record(c, slug, item, at)
        H.event(c, H.KEEPER, EVENT, slug, {"at": at, "items": len(shown),
                                           "kinds": sorted({item["kind"] for item in shown})})
        return len(shown)
    return 0


def _record(c, slug, item, at):
    """What a delivered line leaves behind: its listing, the limiter's count, and the one-time effect of its kind."""
    c.execute("INSERT OR IGNORE INTO maintenance_listings(task_id, kind, appointment, bot_id, listed_at) "
              "VALUES(?,?,?,?,?)", (item["task"], item["kind"], item["appointment"], slug, at))
    LIMITS.record(c, slug, item["task"])
    if item["kind"] == "due":
        task_check_backs.consume(c, item["task"], H.KEEPER)
    elif item["kind"] == "unanswered":
        still_needed.take_back(c, item["asks"])
        H._recount(c, H.bot_actor(slug))
    elif item["kind"] == "restate":
        restate_asks.mark_asked(c, item["row"])


def _wake_actor(slug):
    """The actor a list is written to. This step wakes bots and never a person: anything else is refused."""
    actor = H.bot_actor(slug)
    if not H.is_bot(actor) or H.is_human(actor):
        raise ValueError("find stuck wakes bots only")
    return actor


# ----------------------------------------------------------------------------- small helpers
def _active_bots(c):
    return [row[0] for row in c.execute("SELECT id FROM bots WHERE state='active' ORDER BY id").fetchall()]


def _clock(ts):
    return f"{H.parse_ts(ts):%H:%M} UTC" if ts else ""


def _line_text(text, limit=160):
    return " ".join(str(text or "").split())[:limit]
