"""Wake rules: one engine decides what a message to a bot does, and names the rule that decided it.

A message to a bot starts a run (store.QUEUE_TRIGGER queues a job) unless it is quiet. `judge` is asked once for every
message H._write_message is about to write to a bot, and answers with an outcome and the id of the rule behind it:

- `wake`: queue a run now.
- `note`: write the message quietly and leave a quiet note linked to it, which the bot's next run carries
  (wake_rules.hold, the same note bot_to_bot.hold leaves).
- `batch`: queue it held, to be let go with the bot's other nonblocking notices (wake_presets).
- `ignore`: write it quietly with no note: the room history is all that carries it.
- `ask`: write the message quietly with its note, and raise one Needs-you item for the task's human naming the rule
  (`carry_out`, once per task and rule).
- `action`: run the row's `params.action` (close, move_step, set_wait, relate, note) as the keeper, with the rule id in
  the event payload, and write the message quietly with its note. An action that fails opens the message: it wakes the
  bot under `fail_open`, so work is never lost to a rule.

The message is stamped `refs.rule` with the rule id, whatever the outcome, and a run copies it to `turns.rule_id`
at claim, so every run joins to the rule that woke it (`rule_weekly` in hub sql).

Built-in rules are defined here, in code, with stable ids. They reproduce what the wake filters did before there
were rules: the ALWAYS_WAKE classes (a person's message, an ask, an approval, a needs-action flag, a reply, a gate
verdict) and AWAITED results wake; a bot's own write (`own_write`), news about a parked task (`parked`) and a stall
check on a task that stands as at the last checkpoint (`same_state`) wait; a plain bot-to-bot message waits
(`bot_to_bot`); a finished request and a person's review comments are batched (`batch_*`); one check finishing is
ignored (`pr_check`), every check on a pull request head finished (`pr_checks_done`) wakes the ticket's owner at a
working step and is a note otherwise (`pr_checks_note`), and a review that is not a person's is a note
(`pr_review_note`: the PR_RULES block); anything else
wakes under the rule named for its event (`assignment`, `stall`, `routine`, ...); an event no rule names is a note
under `default_note` (the next run carries it), and the bot's own write is ignored. The logic of
the filters stays in backend/wake_rules.py and backend/bot_to_bot.py; this module orders them and names them.

Table rows (the `rules` table, `scope_kind` team, type, department, bot or task) override the built-ins. A row
matches one event (or `*`, every event that is neither always-wake nor awaited) and optional conditions (`when_json`), and the
first match wins by scope, most specific first: task, bot, department, type, team, then the built-ins; inside a
scope by `position`. The always-wake classes are a floor no row lowers: a person is never made to wait by a rule.
A bot's profile plugs in at the bot scope: `profile_of` names it and PROFILE_RULES holds each profile's built-in
rows (a profile that is not listed adds none).

Fail open: anything the engine cannot read wakes, under `fail_open`. The hot path costs one indexed read of the
rules table for a message that no built-in always-wakes, and nothing more when the table is empty.
"""

import contextvars
import json
import logging
import sqlite3
from typing import NamedTuple, Optional

from . import hubdb as H

log = logging.getLogger(__name__)

IGNORE, NOTE, BATCH, WAKE, ASK, ACTION = "ignore", "note", "batch", "wake", "ask", "action"
OUTCOMES = (IGNORE, NOTE, BATCH, WAKE, ASK, ACTION)
SCOPES = ("builtin", "team", "type", "department", "bot", "task")
# Most specific first: the first scope with a matching row decides.
PRECEDENCE = ("task", "bot", "department", "type", "team")

FAIL_OPEN = "fail_open"
DEFAULT_NOTE = "default_note"
QUIET = "caller_quiet"
NO_RUN = "no_run"
BOT_TO_BOT = "bot_to_bot"
# What a code action can do, for `action` rows (system_routines' THEN names). Stored and checked now; run later.
ACTIONS = ("close", "move_step", "set_wait", "relate", "note")
MAX_ROWS = 500
MAX_LIST_BYTES = 256 * 1024
HOLD_ROW, HOLD_IGNORED = "rule", "ignored"

# What a message is, as a rule names it. The always-wake events cannot be lowered by a row.
FLOOR_EVENTS = {
    "person_message": "a person's message or comment, or a notice of a person's own act",
    "ask": "an ask or a steer: a question to the bot",
    "answer": "the answer to an ask the bot made",
    "approval": "an approval decided, withdrawn or asked about, or a structured question",
    "needs_action": "a message its sender flagged needs-action",
    "reply": "a reply to the bot's own ask, to a message it flagged needs-action, or to one it waits on",
    "verdict": "a factory gate verdict on the bot's ticket: Review 1, Review 2, QA or Security",
}
# wake_rules.always_wakes key -> (event, rule id)
_FLOOR = {"person": "person_message", "approval": "approval", "needs_action": "needs_action", "reply": "reply",
          "gate": "verdict"}
# wake_rules.AWAITED key -> event
AWAITED_EVENTS = {
    "blocker_done": ("blocker_done", "a task this one waits on (blocked_by) finished"),
    "wake_once": ("pr_checks_done", "a pull request link armed with wake_once got its result"),
    "subtask": ("subtask_finished", "a subtask moved or finished"),
    "worktree": ("worktree", "the worktree the task waited for is ready"),
    "watcher": ("watcher", "something the bot watches moved or ended"),
    "resume": ("resume", "a quiet task's stored resume condition came true"),
    "milestone": ("milestone", "a pull request it waits for merged or a release it waits for was published"),
}
# Events that wake under a rule of the same name. Another builder's new event names a `refs.wake` and lands here.
WAKE_EVENTS = {
    "assignment": "a task is assigned, reassigned or handed back to the bot",
    "blocker": "a setup or run blocker needs the bot",
    "review_request": "a review or QA child was created for the bot",
    "pr_checks": "a pull request's checks finished",
    "stall": "a task has gone quiet and nothing is set to move it",
    "status_check": "a quiet task's status check",
    "stall_check": "a stall or status check",
    "routine": "a routine ran",
    "schedule": "a scheduled run",
    "due": "a task's due date came",
    "snooze": "a task's snooze ended",
    "next_run": "a next-run task is next",
    "update": "an update was requested",
    "restate_ask": "an ask nobody answered was restated",
    "still_needed": "an ask is asked about again",
    "auto_start": "an assigned task starts on a free turn",
    "background_done": "background work the bot waited for finished",
    "release_published": "a release was published",
    "release_now": "a person asked for a release now",
    "task_manager_report": "the Task Manager's report for a department",
    "pr_checks_done": "every check on a pull request head finished",
    "pr_review": "a GitHub review or review comment on a pull request, not from a person",
    "subtask_finished": "a subtask finished",
    "blocker_done": "a task this one waits on finished",
    "task_done": "a task the bot asked for or owns was finished or closed",
    "task_declined": "a task the bot asked for was declined",
    "task_blocked": "a task the bot asked for now waits on someone else",
    "review_handback": "a task the bot asked for was handed back for its review",
    "backlog": "updates held for the bot while it was away",
    "feed": "a Slack channel's messages were handed to the bot to read",
    "digest": "the bot's held updates were let go as one digest",
}
# Events that wait as a note when no rule says otherwise: the next run carries them. A row may wake on any of them.
NOTE_EVENTS = {
    "comment": "a comment or message about a task or in a chat",
    "status_or_relation": "a task's status, step or relation moved",
    "ready_for_review": "a Dev ticket the bot asked for reached a review step",
    "step_entered": "a task entered a step of its type",
}
BATCH_EVENTS = {"completion": "a finished request", "review": "review comments", "ci": "a failing check",
                "rule": "a notice a rule batched"}
AWAITED_NAMES = frozenset(v[0] for v in AWAITED_EVENTS.values())
EVENTS = {**FLOOR_EVENTS, **WAKE_EVENTS, **NOTE_EVENTS, **{v[0]: v[1] for v in AWAITED_EVENTS.values()},
          **{"batch_" + k: v for k, v in BATCH_EVENTS.items()}}
# refs.wake spellings the writers use -> the event a rule names.
WAKE_ALIASES = {"stalled": "stall", "stuck_retry": "stall", "stuck_cause": "stall", "occurrence": "routine",
                "next-run": "next_run", "background-done": "background_done", "watcher-done": "watcher",
                "held-messages": "backlog"}
WHEN_KEYS = ("actor", "step", "role", "tag", "state")
WHEN_ACTORS = ("human", "bot", "keeper")
WHEN_ROLES = ("owner", "requester", "qa", "release_manager", "other")

# A bot's profile (backend/profiles.py) plugs in at the bot scope. `profile_of` names it and PROFILE_RULES holds each
# profile's built-in rows, shaped like `rules` rows (a profile that is not listed adds none; `custom` has its own
# rows in the table). A row naming an event is tried at the bot scope after the bot's own rows; a `*` row is the
# profile's "everything else" and is tried last of all, after the team's rows, so a type or team rule that names an
# event (a release waking QA) still reaches a bot whose profile makes everything else a note.
PROFILES = ("doer", "reviewer", "lead", "responder", "monitor", "custom")
# Time-based events a bot set for itself (its routines, due dates, snoozes): a bot that works to a clock is woken by
# them whatever its profile says about news.
OWN_TIMERS = (("routine", "its routine ran"), ("schedule", "a scheduled run came"), ("due", "a task's due date came"),
              ("snooze", "a task's snooze ended"))
# Work the bot started or was queued itself: its background work finished, a next-run task came up.
OWN_WORK = (("background_done", "background work it waited for finished"), ("next_run", "its next-run task is next"))


def _profile_rules():
    rows = {}

    def add(profile, rule, event, then, words, when=None):
        rows.setdefault(profile, []).append({
            "id": rule, "event": event, "then_outcome": then, "params_json": "{}", "words": words,
            "when": when or {}, "when_json": json.dumps(when or {}, sort_keys=True)})

    def timers(profile, which=OWN_TIMERS):
        for event, words in which:
            add(profile, f"profile_{profile}_{event}", event, WAKE, words)

    add("reviewer", "profile_reviewer_assignment", "assignment", WAKE, "a review or QA request was filed for it")
    add("reviewer", "profile_reviewer_request", "review_request", WAKE, "a review, QA or Security request")
    add("reviewer", "profile_reviewer_handback", "review_handback", WAKE, "a task it asked for came back for its review")
    add("reviewer", "profile_reviewer_release", "release_published", WAKE, "a ticket it checks was released: live tests now")
    timers("reviewer", OWN_TIMERS[:2] + OWN_WORK)
    add("reviewer", "profile_reviewer_note", "*", NOTE, "anything else")
    # A bot finishing it is batched news for everyone else (batch_completion); a lead hears it now.
    for event, words in (("task_done", "finished"), ("batch_completion", "finished by a bot"),
                         ("task_declined", "declined"), ("task_blocked", "blocked")):
        add("lead", f"profile_lead_{event}", event, WAKE, f"a ticket it asked for was {words}", {"role": ["requester"]})
    add("lead", "profile_lead_handback", "review_handback", WAKE, "a ticket it asked for came back for its review")
    add("lead", "profile_lead_release_now", "release_now", WAKE, "someone asked for a release now")
    add("lead", "profile_lead_blocker", "blocker", WAKE, "a blocker needs it")
    # A lead takes tasks: one handed to it (an escalation, a release, a report) is work now, not digest news.
    add("lead", "profile_lead_assignment", "assignment", WAKE, "a task was given to it")
    add("lead", "profile_lead_request", "review_request", WAKE, "a review or sign-off was asked of it")
    timers("lead", OWN_TIMERS + OWN_WORK)
    add("lead", "profile_lead_digest", "*", BATCH, "anything else Tico reports: held for the daily digest",
        {"actor": ["keeper"]})
    add("lead", "profile_lead_note", "*", NOTE, "anything else a bot says")
    add("responder", "profile_responder_note", "*", NOTE, "anything but a person")
    add("monitor", "profile_monitor_assignment", "assignment", WAKE, "its watcher filed a task",
        {"actor": ["keeper"]})
    timers("monitor", OWN_TIMERS + OWN_WORK)
    add("monitor", "profile_monitor_note", "*", NOTE, "anything else")
    return {name: tuple(found) for name, found in rows.items()}


PROFILE_RULES = _profile_rules()
PROFILE_RULE_IDS = frozenset(r["id"] for found in PROFILE_RULES.values() for r in found)


def profile_of(conn, bot):
    """The profile a bot runs under: its `bot_config.profile`, or `doer` for a bot with none (a database from before
    profiles, a bot seeded since the last start)."""
    try:
        row = conn.execute("SELECT json_extract(config_json,'$.profile') FROM bot_config WHERE bot=?",
                           (H.actor_id(bot),)).fetchone()
    except sqlite3.OperationalError:
        return "doer"
    return row[0] if row and row[0] in PROFILES else "doer"


def profile_listing(conn, slug):
    """A bot's profile rows as the Rules page lists them: built-in, read-only, tagged with the profile."""
    name = profile_of(conn, slug)
    return [{"id": r["id"], "scope_kind": "bot", "scope_id": slug, "event": r["event"], "when": r["when"],
             "then": r["then_outcome"], "params": {}, "enabled": True, "position": n, "built_in": True,
             "protected": False, "group": "profile", "words": r["words"], "profile": name, "can_change": False}
            for n, r in enumerate(PROFILE_RULES.get(name, ()))]


def sweeps_on(conn, bot):
    """Whether stall and status checks (the sweeps) may wake this bot's tasks: False for a reviewer, lead, responder
    and monitor, and for a custom profile that turned them off. Fails open: a bot whose profile cannot be read is
    swept."""
    try:
        from . import profiles
        return profiles.sweeps_on(conn, H.actor_id(bot))
    except Exception:  # noqa: BLE001
        return True


class Decision(NamedTuple):
    outcome: str
    rule: str
    reason: Optional[str] = None        # the held_by mark when the message is written quiet here
    params: Optional[dict] = None


# Pull request checks (backend/github.py): the webhook writes no message for one check finishing (`pr_check`), and one
# `pr_checks_done` per head when every check on it has finished. It wakes the owner of the ticket the pull request is
# linked to while the ticket is at a working step (WORKING), or when the link was armed with `wake_once` (the same
# event: the arming is what the bot asked to be told); anywhere else it is a note. A review or comment on the pull
# request that is not from a person is a note; a person's keeps the batched review wake. Rows in the table override
# these as they do any built-in.
WORKING = ("open", "doing")
PR_RULES = (
    ("pr_check", "pr_check", IGNORE, "one check on a pull request finished: nothing is written until all of them have"),
    ("pr_checks_done", "pr_checks_done", WAKE,
     "every check on a pull request head finished, on a ticket the bot owns at a working step or a link it armed"),
    ("pr_checks_note", "pr_checks_done", NOTE,
     "every check on a pull request head finished, on a ticket not at a working step or not the bot's"),
    ("pr_review_note", "pr_review", NOTE, "a review or review comment on a pull request that is not from a person"),
)


def _pr_decision(conn, event, target, refs, conv):
    """The built-in decision for a pull request message, or None when it is not one. Reads the task only for a head's
    result that no `wake_once` arming asked for."""
    if refs.get("wake") == "pr_review":
        return Decision(NOTE, "pr_review_note", HOLD_ROW)
    if event != "pr_checks_done" or not refs.get("pr_head"):
        return None
    if refs.get("pr_armed"):
        return Decision(WAKE, "awaited_wake_once")
    tid = H.message_task_id({"refs": refs}, conv)
    task = H.task(conn, tid) if tid else None
    if task and task.get("owner") == target and task.get("status") in WORKING:
        return Decision(WAKE, "pr_checks_done")
    return Decision(NOTE, "pr_checks_note", HOLD_ROW)


def _builtins():
    out = []

    def add(rule, event, then, when, group, protected=False):
        out.append({"id": rule, "scope_kind": "builtin", "scope_id": "", "event": event, "when": {}, "then": then,
                    "params": {}, "enabled": True, "position": len(out), "built_in": True, "protected": protected,
                    "group": group, "words": when})

    for key, event in _FLOOR.items():
        add(event, event, WAKE, FLOOR_EVENTS[event], "always", True)
    add("ask", "ask", WAKE, FLOOR_EVENTS["ask"], "always", True)
    add("answer", "answer", WAKE, FLOOR_EVENTS["answer"], "always", True)
    for key, (event, words) in AWAITED_EVENTS.items():
        add("awaited_" + key, event, WAKE, words, "awaited")
    add("own_write", "status_or_relation", IGNORE, "the bot's own write caused it", "filter")
    add("parked", "status_or_relation", NOTE, "news about a task the bot owns that waits on something", "filter")
    add("same_state", "stall_check", IGNORE, "a stall or status check on a task that stands as at the last checkpoint",
        "filter")
    add(BOT_TO_BOT, "comment", NOTE, "a plain message from another bot", "filter")
    for kind, words in BATCH_EVENTS.items():
        add("batch_" + kind, "batch_" + kind, BATCH, words, "batch")
    add("duplicate_drop", "*", IGNORE, "the same event as one already queued or just worked", "batch")
    for event, words in WAKE_EVENTS.items():
        if event not in ("digest", "pr_review", "pr_checks_done"):
            add(event, event, WAKE, words, "wake")
    for rule, event, then, words in PR_RULES:
        add(rule, event, then, words, "pr_checks")
    add("batch_digest", "digest", WAKE, WAKE_EVENTS["digest"], "batch")
    for event, words in NOTE_EVENTS.items():
        add(event, event, NOTE, words, "note")
    add(DEFAULT_NOTE, "*", NOTE, "anything no other rule names: the next run carries it", "note")
    add(FAIL_OPEN, "*", WAKE, "the engine could not read the message", "wake")
    add(QUIET, "*", IGNORE, "the writer already made it quiet", "system")
    add(NO_RUN, "*", IGNORE, "no run can start from it: live output, a message to itself, an external agent", "system")
    return out


BUILTINS = _builtins()
BUILTIN_IDS = frozenset(r["id"] for r in BUILTINS)


# ----------------------------------------------------------------------------- schema
SCHEMA = """
CREATE TABLE IF NOT EXISTS rules(
 id TEXT PRIMARY KEY, scope_kind TEXT NOT NULL, scope_id TEXT NOT NULL DEFAULT '', event TEXT NOT NULL,
 when_json TEXT NOT NULL DEFAULT '{}', then_outcome TEXT NOT NULL, params_json TEXT NOT NULL DEFAULT '{}',
 enabled INTEGER NOT NULL DEFAULT 1, position INTEGER NOT NULL DEFAULT 0, note TEXT NOT NULL DEFAULT '',
 created_by TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1);
CREATE INDEX IF NOT EXISTS rules_event ON rules(event, enabled);
CREATE INDEX IF NOT EXISTS rules_scope ON rules(scope_kind, scope_id);
"""


# ----------------------------------------------------------------------------- naming a message's event
def floor_rule(key, kind):
    """The built-in rule an always-wake key names: an ask, a steer and an answer are told apart by the message's kind."""
    if key == "ask":
        return "answer" if kind == "answer" else "ask"
    return _FLOOR.get(key, "person_message")


def event_of(actor, kind, refs, body, floor=None):
    """The event a message is, in the words a rule uses. The writers stamp it: `refs.wake` names the event, the
    trigger stamp (run_triggers) the kind of wake, and Tico's own notices are told apart by their words
    (wake_presets.classify). Pure: reads no table."""
    from . import run_triggers, wake_presets, wake_rules
    if floor:
        return floor_rule(floor, kind)
    if refs.get("wake_digest"):
        return "digest"
    batch = refs.get("wake_batch")
    if batch in BATCH_EVENTS:
        return "batch_" + batch
    key = wake_rules.awaited(refs)
    if key:
        return AWAITED_EVENTS[key][0]
    wake = refs.get("wake")
    if isinstance(wake, str):
        wake = WAKE_ALIASES.get(wake, wake)
        if wake in EVENTS:
            return wake
    news = refs.get("news")
    if isinstance(news, str) and news in EVENTS:
        return news
    if refs.get("sweep"):
        return "routine"
    if refs.get("auto_start"):
        return "auto_start"
    trigger = run_triggers.of_message({"refs": refs, "from_actor": actor})[0]
    if trigger in ("comment", "status_or_relation") or actor == H.KEEPER:
        label = wake_presets.classify({"refs": refs, "from_actor": actor, "body": body, "kind": kind})[0]
        if label == "assignment":
            return "assignment"
        if label == "blocker":
            return "blocker"
        if label == "gate":
            return "ready_for_review"
    if trigger == "other":
        return "other"
    return {"stall_check": "stall_check", "schedule": "schedule"}.get(trigger, trigger)


def _rule_for_event(event):
    """The built-in rule an event with no table row falls to: its own name when the event wakes, else a note."""
    return (WAKE, event) if event in WAKE_EVENTS else (NOTE, event if event in NOTE_EVENTS else DEFAULT_NOTE)


# ----------------------------------------------------------------------------- the table
def _release_flow():
    from . import release_flow
    return release_flow


class _Context:
    """What a row's scope and conditions read, loaded only when a row asks."""

    def __init__(self, conn, actor, target, refs, conv, task_row):
        self.conn, self.actor, self.target, self.refs, self.conv = conn, actor, target, refs, conv
        self._task = task_row
        self._loaded = task_row is not None
        self._chain = None

    @property
    def task(self):
        if not self._loaded:
            self._loaded = True
            tid = H.message_task_id({"refs": self.refs}, self.conv)
            self._task = H.task(self.conn, tid) if tid else None
        return self._task

    @property
    def slug(self):
        return H.actor_id(self.target)

    def departments(self):
        """The groups the target bot is in, its own and every one above it. Read once, and only when a department
        row is in play."""
        if self._chain is None:
            from . import groups, people
            roster = groups._roster(self.conn)
            gid = people.team_of(self.slug, groups._configs(self.conn), roster)
            self._chain = set(people.group_chain(gid, roster))
        return self._chain

    def in_scope(self, row):
        kind, ident = row["scope_kind"], row["scope_id"]
        if kind == "team":
            return True
        if kind == "bot":
            return ident == self.slug
        if kind == "task":
            return bool(self.task) and self.task["id"] == ident
        if kind == "type":
            return bool(self.task) and self.task.get("type_id") == ident
        if kind == "department":
            return ident in self.departments()
        return False

    def matches(self, when):
        for key, want in when.items():
            wanted = want if isinstance(want, list) else [want]
            if key == "actor":
                mine = "keeper" if self.actor == H.KEEPER else "human" if H.is_human(self.actor) else "bot"
                if mine not in wanted:
                    return False
                continue
            task = self.task
            if not task:
                return False
            if key == "state":
                if task.get("status") not in wanted:
                    return False
            elif key == "role":
                mine = {"owner" if task.get("owner") == self.target else
                        "requester" if task.get("requester") == self.target else "other"}
                # QA and the Release Manager are found, never configured per ticket (release_flow): a bot holds
                # that role here when it is the one the ticket's QA or the team's release setting names.
                if "qa" in wanted and self.slug == _release_flow().qa_owner(self.conn, task):
                    mine.add("qa")
                if "release_manager" in wanted and self.slug == _release_flow().release_manager(self.conn):
                    mine.add("release_manager")
                if not mine & set(wanted):
                    return False
            elif key == "step":
                names = {str(task.get("step_id") or "").lower()}
                found = self.conn.execute("SELECT name FROM task_steps WHERE id=?", (task.get("step_id"),)).fetchone()
                if found:
                    names.add(str(found[0]).lower())
                if not names & {str(w).lower() for w in wanted}:
                    return False
            elif key == "tag":
                for tag in wanted:
                    if not self.conn.execute(
                            "SELECT 1 FROM task_tags tt JOIN tags g ON g.id=tt.tag_id WHERE tt.task_id=? "
                            "AND lower(g.key)=? LIMIT 1", (task["id"], str(tag).lower())).fetchone():
                        return False
            else:
                return False            # a condition this release does not know never matches
        return True


def _row_decision(row, actor):
    outcome = row["then_outcome"]
    params = H._json(row["params_json"], {}) or {}
    if outcome == NOTE:
        return Decision(NOTE, row["id"], HOLD_ROW, params)
    if outcome == IGNORE:
        return Decision(IGNORE, row["id"], HOLD_IGNORED, params)
    if outcome == BATCH:
        # Only Tico's own notices can be held at enqueue (the wake_preset_hold trigger); a message from anyone
        # else wakes, with the rule still named, rather than risk a wake nobody sees.
        return Decision(BATCH if actor == H.KEEPER else WAKE, row["id"], None, params)
    if outcome in (ASK, ACTION):
        return Decision(outcome, row["id"], None, params)
    return Decision(WAKE, row["id"], None, params)


def _table_decision(conn, event, actor, target, refs, conv, task_row=None):
    """The decision of the first table or profile row that matches, or None. One indexed read, nothing more when
    no row names this event and no profile has rows."""
    if event in FLOOR_EVENTS:
        return None
    try:
        rows = conn.execute("SELECT * FROM rules WHERE enabled=1 AND event IN (?, '*') "
                            "ORDER BY position, event='*', created, id", (event,)).fetchall()
    except sqlite3.OperationalError:            # a database from before the table: no rows
        rows = []
    slug, profile = H.actor_id(target), []
    if PROFILE_RULES:
        profile = [{**r, "scope_kind": "bot", "scope_id": slug} for r in PROFILE_RULES.get(profile_of(conn, slug), ())
                   if r["event"] in (event, "*")]
    if event in AWAITED_NAMES or event == "digest":
        # What the bot waits on wakes it, and a digest is held news let go: a `*` row (a lead's batch-everything, a
        # note-everything) does not reach either, only a row that names the event.
        rows = [r for r in rows if r["event"] != "*"]
        profile = [r for r in profile if r["event"] != "*"]
    if not rows and not profile:
        return None
    from . import wake_rules
    if wake_rules.CAUSE.get() == target:
        # The bot's own write is no news to it: a profile row never wakes it for one (the built-in filter decides).
        profile = [r for r in profile if r["then_outcome"] in (NOTE, IGNORE)]
    ctx = _Context(conn, actor, target, refs, conv, task_row)
    named, rest = [r for r in profile if r["event"] != "*"], [r for r in profile if r["event"] == "*"]
    for scope in PRECEDENCE:
        found = [r for r in rows if r["scope_kind"] == scope]
        if scope == "bot":
            found += named
        for row in found:
            if ctx.in_scope(row) and ctx.matches(H._json(row["when_json"], {}) or {}):
                return _row_decision(row, actor)
    for row in rest:                            # the profile's "everything else", after every scope that named an event
        if ctx.matches(H._json(row["when_json"], {}) or {}):
            return _row_decision(row, actor)
    return None


# ----------------------------------------------------------------------------- the decision
def judge(conn, actor, target, kind, refs, body, conv, in_reply_to=None, decided=None):
    """The decision for a message about to be written to `target`, or None when `target` is not a bot. Fail open.
    `decided` is a decision the writer made before this (a plain bot-to-bot message waiting under `bot_to_bot`)."""
    if not str(target).startswith("bot:"):
        return None
    try:
        return _judge(conn, actor, target, kind, refs, body, conv, in_reply_to, decided)
    except Exception:  # noqa: BLE001 - fail open: a message the engine cannot read starts its run as before
        return Decision(WAKE, FAIL_OPEN)


def _judge(conn, actor, target, kind, refs, body, conv, in_reply_to, decided=None):
    from . import bot_to_bot, wake_rules
    refs = refs if refs is not None else {}
    if not isinstance(refs, dict):
        return Decision(WAKE, FAIL_OPEN)
    # The built-in filters (own write, parked, same state) stay in wake_rules.gate, asked first: it costs what it
    # always did, and a table row may override what it says.
    held = wake_rules.gate(conn, actor, target, kind, refs, body, conv, in_reply_to)
    if decided is not None:
        return decided
    if refs.get("quiet"):
        if refs.get(bot_to_bot.MARK) == bot_to_bot.RULE:
            return Decision(NOTE, BOT_TO_BOT)           # bot_to_bot.hold leaves the note
        return Decision(IGNORE, QUIET)
    if not wake_rules._would_queue(conn, actor, target, refs):
        return Decision(IGNORE, NO_RUN)
    cause = wake_rules.CAUSE.get()
    floor = wake_rules.always_wakes(conn, actor, target, kind, refs, body, cause, in_reply_to)
    if floor:
        return Decision(WAKE, floor_rule(floor, kind))
    event = event_of(actor, kind, refs, body)
    row = _table_decision(conn, event, actor, target, refs, conv)
    if row:
        return row
    row = _pr_decision(conn, event, target, refs, conv)
    if row:
        return row
    key = wake_rules.awaited(refs)
    if key:
        return Decision(WAKE, "awaited_" + key)
    if refs.get("wake_digest"):
        return Decision(WAKE, "batch_digest")
    if refs.get("wake_batch") in BATCH_EVENTS and actor == H.KEEPER:
        return Decision(BATCH, "batch_" + refs["wake_batch"])
    if held == "same_state":
        return Decision(IGNORE, "same_state", "same_state")
    if held == "own_write":
        return Decision(IGNORE, held, HOLD_IGNORED)    # the bot's own run already knows what it wrote
    if held:
        return Decision(NOTE, held, held)
    outcome, rule = _rule_for_event(event)
    return Decision(outcome, rule, HOLD_ROW if outcome == NOTE else None)


def decide(conn, actor, target, kind, refs, body, conv, in_reply_to=None):
    """(outcome, rule id) for a message to a bot, or None when the target is not one."""
    found = judge(conn, actor, target, kind, refs, body, conv, in_reply_to)
    return (found.outcome, found.rule) if found else None


def bot_to_bot_decision(conn, actor, target, kind, refs, body, conv, in_reply_to=None):
    """What a plain bot-to-bot message does, when the built-in lets it wait (bot_to_bot.can_wait): `bot_to_bot`, or
    the row that overrides it. None when it does not wait, or a rule wakes it (the engine then judges it again in
    H._write_message, and finds the same row)."""
    from . import bot_to_bot
    try:
        if not bot_to_bot.can_wait(conn, actor, target, kind, refs, body, in_reply_to):
            return None
        row = _table_decision(conn, event_of(actor, kind, refs, body), actor, target, refs, conv)
        if row is None:
            return Decision(NOTE, BOT_TO_BOT)
        return row if row.outcome in (NOTE, IGNORE) else None
    except Exception:  # noqa: BLE001 - fail open
        return None


def stamp(refs, decision):
    """The message's refs with the rule that decided it. A row's `batch` stamps the hold the jobs trigger reads
    a rule that lets a Tico notice through takes off a batch mark the writer put on it."""
    out = {**(refs or {}), "rule": decision.rule}
    if decision.outcome == BATCH and not out.get("wake_batch"):
        out["wake_batch"] = "rule"
    if decision.outcome != BATCH and decision.rule not in BUILTIN_IDS and out.get("wake_batch") in BATCH_EVENTS:
        out.pop("wake_batch", None)
    return out


# Which rule a held job's class names (wake_presets.HOLD_TRIGGER sets `jobs.wake_class`).
CLASS_RULES = {"completion": "batch_completion", "review": "batch_review", "ci": "batch_ci", "rule": "batch_rule",
               "digest": "batch_digest", "duplicate": "duplicate_drop"}


def claimed_rule(msg, job=None):
    """The rule id a claimed job's run records: the message's stamp, else the rule its hold class names (a message
    from before rules, or one a writer inserted without the gate), else None."""
    stamped = (msg.get("refs") or {}).get("rule")
    if isinstance(stamped, str) and stamped:
        return stamped[:80]
    return CLASS_RULES.get((job["wake_class"] if job is not None else None))


# ----------------------------------------------------------------------------- ask and action
# While a rule's action runs, what it writes is judged as news: an action never starts another action or ask.
_CARRYING = contextvars.ContextVar("rule_carrying", default=False)
ASKED = "rule.ask"


def carry_out(conn, decision, actor, target, kind, refs, body, conv):
    """What `ask` and `action` decisions do, before the message is written; the decision to write it under.

    `ask`: one Needs-you item for the task's human, then the message waits as a note. `action`: the row's action runs
    as the keeper, then the message waits as a note; an action that fails opens the message instead (`fail_open`), so a
    rule never costs the bot work it was meant to do. Anything else is returned as it is."""
    if decision.outcome not in (ASK, ACTION):
        return decision
    if _CARRYING.get():
        return Decision(NOTE, decision.rule, HOLD_ROW, decision.params)
    token = _CARRYING.set(True)
    conn.execute("SAVEPOINT rule_carry")
    try:
        tid = H.message_task_id({"refs": refs or {}}, conv)
        row = H.task(conn, tid) if tid else None
        if decision.outcome == ASK:
            _ask(conn, decision, row, actor, body)
        else:
            _act(conn, decision, row)
        conn.execute("RELEASE rule_carry")
        return Decision(NOTE, decision.rule, HOLD_ROW, decision.params)
    except Exception as exc:  # noqa: BLE001 - fail open: a rule that cannot run wakes the bot as before
        conn.execute("ROLLBACK TO rule_carry")
        conn.execute("RELEASE rule_carry")
        log.warning("Rule %s could not %s: %s", decision.rule, decision.outcome, exc)
        return Decision(WAKE, FAIL_OPEN)
    finally:
        _CARRYING.reset(token)


def _ask(conn, decision, row, actor, body):
    """One open Needs-you item per task and rule, for the task's requester when that is a person, else the owner bot's
    operator. Raises when nobody can be asked, so the message wakes the bot instead of waiting for no one."""
    if not row:
        raise ValueError("an ask rule needs a task")
    if conn.execute("SELECT 1 FROM events e JOIN tasks t ON t.id=e.target WHERE e.action=? "
                    "AND json_extract(e.detail_json,'$.rule')=? AND json_extract(e.detail_json,'$.task')=? "
                    f"AND t.status IN ({','.join('?' * len(H.ACTIVE_STATUSES))}) LIMIT 1",
                    (ASKED, decision.rule, row["id"], *H.ACTIVE_STATUSES)).fetchone():
        return
    who = row["requester"] if H.is_human(row["requester"]) else None
    if not who and H.is_bot(row["owner"]):
        from . import usage_limits
        person = usage_limits.recipients(conn, H.actor_id(row["owner"]))
        who = H.human_actor(person["id"]) if person else None
    if not who:
        raise ValueError("no person to ask")
    private = H.task_private(conn, row)
    if private and not H.task_private_readable(conn, who, row):
        raise ValueError("no person who may read the task")     # the bot is woken instead of naming it to someone else
    sender = "Tico" if actor == H.KEEPER else H.actor_id(actor)
    text = str(body or "").strip()[:1500]
    # The card carries the task's title and the message, so it is as private as the task it is about.
    card = H.task_create(conn, H.KEEPER, f"A rule needs you: {row['title']}"[:300],
                         f"The rule {decision.rule} asked for a person on {row['title']} ({row['id']}).\n\n"
                         f"From {sender}: {text}", who, lint=False, deduplicate=False, private=True if private else None)
    H.event(conn, H.KEEPER, ASKED, card["id"], {"rule": decision.rule, "task": row["id"]})


def _act(conn, decision, row):
    """Run `params.action` on the task as the keeper, through the same hubdb calls a person's click makes."""
    if not row:
        raise ValueError("an action rule needs a task")
    params = decision.params or {}
    action, tid = params.get("action"), row["id"]
    note = str(params.get("note") or params.get("text") or "")[:1000]
    said = f"Rule {decision.rule}: {note}" if note else f"Rule {decision.rule}"
    if action == "close":
        H.task_close(conn, H.KEEPER, tid, note=said)
    elif action == "move_step":
        H.task_update(conn, H.KEEPER, tid, step=str(params["step"]), note=said)
    elif action == "set_wait":
        if params.get("blocked_by"):
            from . import task_relations as TR
            TR.add_blocker(conn, H.KEEPER, tid, str(params["blocked_by"]), mover=True, note=said)
        else:
            H.task_update(conn, H.KEEPER, tid, status="waiting", waiting_on=str(params["waiting_on"]), note=said)
    elif action == "relate":
        from . import task_relations as TR
        TR.relate(conn, H.KEEPER, tid, str(params["task"]), kind=str(params.get("kind") or "related"), mover=True,
                  note=said)
    elif action == "note":
        H.task_update(conn, H.KEEPER, tid, note=said)
    else:
        raise ValueError(f"unknown action {action!r}")
    H.event(conn, H.KEEPER, "rule.action", tid, {"rule": decision.rule, "action": action})


# ----------------------------------------------------------------------------- step entry and the Dev tickets rows
# What a Dev ticket's hand-offs say, as editable type rows: (note, when, outcome). Seeded once per type (`seed_dev`);
# a person edits or deletes them in Settings, and a type that lost them is not given them again.
DEV_ROWS = (
    ("QA Fixes entered: wake the Builder who owns the ticket", {"step": "QA Fixes", "role": "owner"}, WAKE),
    ("Pending Release entered: a note to the Release Manager", {"step": "Pending Release", "role": "release_manager"}, NOTE),
    ("In QA entered: wake QA only", {"step": "In QA", "role": "qa"}, WAKE),
)
SEEDED = "rules.seeded"


def seed_dev(conn):
    """Give every Dev tickets type its hand-off rows once. A type that already has them recorded keeps what a person
    made of them. Quietly does nothing before the rules table or the events table exists."""
    from .roadmap_flow import _dev
    try:
        types = [r for r in conn.execute("SELECT id,name FROM task_types").fetchall() if _dev(r)]
        for ident, _name in types:
            if conn.execute("SELECT 1 FROM events WHERE action=? AND target=? LIMIT 1", (SEEDED, ident)).fetchone():
                continue
            at = H.now()
            for position, (note, when, outcome) in enumerate(DEV_ROWS, 1):
                conn.execute(
                    "INSERT INTO rules(id,scope_kind,scope_id,event,when_json,then_outcome,params_json,enabled,"
                    "position,note,created_by,created,updated,revision) VALUES(?,?,?,?,?,?,?,1,?,?,?,?,?,1)",
                    ("rule-" + H.new_id(), "type", ident, "step_entered", json.dumps(when, sort_keys=True), outcome,
                     "{}", position, note, H.KEEPER, at, at))
            H.event(conn, H.KEEPER, SEEDED, ident, {"rows": len(DEV_ROWS)})
    except sqlite3.OperationalError:
        return


def step_entered(conn, actor, row):
    """A task entered a step: the bots that have a rule for it hear it (a note or a wake, as the rule says); everyone
    else hears nothing. Only rows that name `step_entered` start this, so a team without them writes no message on a
    step move. The owner, the requester, the ticket's QA and the Release Manager are the ones a row can reach."""
    try:
        if not conn.execute("SELECT 1 FROM rules WHERE enabled=1 AND event='step_entered' LIMIT 1").fetchone():
            return
        step = conn.execute("SELECT name FROM task_steps WHERE id=?", (row.get("step_id"),)).fetchone()
        flow = _release_flow()
        qa, manager = flow.qa_owner(conn, row), flow.release_manager(conn)
        who = [row.get("owner"), row.get("requester"), H.bot_actor(qa) if qa else None,
               H.bot_actor(manager) if manager else None]
        conv = H.conversation(conn, row.get("conversation_id"))
        for target in dict.fromkeys(a for a in who if a and H.is_bot(a) and a != actor):
            refs = {"task": row["id"], "news": "step_entered"}
            if _table_decision(conn, "step_entered", actor, target, refs, conv, row):
                H._wake(conn, row, target, f"{row['title']} entered {step[0] if step else 'a new step'}.",
                        {"news": "step_entered"})
    except Exception:  # noqa: BLE001 - a step move never fails for the news about it
        return


# ----------------------------------------------------------------------------- reading
def usage(conn, actor, days=7):
    """{rule id: {runs, no_op}} over the last `days`, on the runs this actor may count: a run on a private task the
    actor is not a party to is left out of the figures."""
    since = H.shift(H.now(), days=-days)
    out = {}
    for row in conn.execute(
            "SELECT t.rule_id AS rule, count(*) AS runs, sum(coalesce(t.did_nothing,0)=1) AS idle FROM turns t "
            "WHERE t.started>=? AND t.rule_id IS NOT NULL AND (t.task_id IS NULL OR EXISTS ("
            "SELECT 1 FROM tasks k WHERE k.id=t.task_id AND (k.private=0 OR k.owner=? OR k.requester=?))) "
            "GROUP BY t.rule_id", (since, actor, actor)):
        out[row["rule"]] = {"runs": row["runs"], "no_op": row["idle"] or 0}
    return out


def _with_usage(item, counts):
    seen = counts.get(item["id"]) or {"runs": 0, "no_op": 0}
    return {**item, "runs_7d": seen["runs"], "no_op_7d": seen["no_op"],
            "no_op_rate_7d": round(seen["no_op"] / seen["runs"], 3) if seen["runs"] else None}


def view(row):
    """A table row as the API shows it."""
    return {"id": row["id"], "scope_kind": row["scope_kind"], "scope_id": row["scope_id"], "event": row["event"],
            "when": H._json(row["when_json"], {}) or {}, "then": row["then_outcome"],
            "params": H._json(row["params_json"], {}) or {}, "enabled": bool(row["enabled"]),
            "position": row["position"], "note": row["note"], "built_in": False, "protected": False,
            "created_by": row["created_by"], "created": row["created"], "updated": row["updated"],
            "revision": row["revision"]}


def validate(c, settings, scope_kind, scope_id, event, then, when, params):
    """The normalized (scope_id, when, params) of a row, or a 422 saying which part is wrong."""
    from .store import Problem
    if scope_kind not in SCOPES[1:]:
        raise Problem("invalid", f"scope_kind is one of {', '.join(SCOPES[1:])}", 422)
    scope_id = str(scope_id or "")
    if scope_kind == "team":
        scope_id = ""
    elif not scope_id:
        raise Problem("invalid", f"a {scope_kind} rule needs a scope_id", 422)
    elif scope_kind == "type" and not H.type_get(c, scope_id):
        raise Problem("not_found", "No such task type", 404)
    elif scope_kind == "bot" and not H.bot(c, scope_id):
        raise Problem("not_found", "No such bot", 404)
    elif scope_kind == "task" and not H.task(c, scope_id):
        raise Problem("not_found", "No such task", 404)
    elif scope_kind == "department":
        from . import task_rules
        if scope_id not in {d["id"] for d in task_rules.departments(settings)}:
            raise Problem("invalid", "Choose a department from the org chart's department list", 422)
    if event != "*" and event not in EVENTS:
        raise Problem("invalid", "event is * or one of: " + ", ".join(EVENTS), 422)
    if then not in OUTCOMES:
        raise Problem("invalid", f"then is one of {', '.join(OUTCOMES)}", 422)
    if event in FLOOR_EVENTS and then in (IGNORE, NOTE, BATCH):
        raise Problem("invalid", f"{event} always wakes the bot: a rule cannot make it wait", 422)
    when = when if when is not None else {}
    if not isinstance(when, dict) or set(when) - set(WHEN_KEYS):
        raise Problem("invalid", f"when holds only {', '.join(WHEN_KEYS)}", 422)
    if len(json.dumps(when)) > 1000:            # every row is read per message and listed in pages: keep each small
        raise Problem("invalid", "when is a few short words per key", 422)
    for key, want in when.items():
        wanted = want if isinstance(want, list) else [want]
        if not wanted or not all(isinstance(w, str) and w and len(w) <= 80 for w in wanted):
            raise Problem("invalid", f"when.{key} is a word or a list of words", 422)
        if key == "actor" and set(wanted) - set(WHEN_ACTORS):
            raise Problem("invalid", f"when.actor is {', '.join(WHEN_ACTORS)}", 422)
        if key == "role" and set(wanted) - set(WHEN_ROLES):
            raise Problem("invalid", f"when.role is {', '.join(WHEN_ROLES)}", 422)
        if key == "state" and set(wanted) - set(H.TASK_STATUSES if hasattr(H, "TASK_STATUSES") else wanted):
            raise Problem("invalid", "when.state is a task status", 422)
    params = params if params is not None else {}
    if not isinstance(params, dict) or len(json.dumps(params)) > 2000:
        raise Problem("invalid", "params is a small object", 422)
    if then == ACTION and params.get("action") not in ACTIONS:
        raise Problem("invalid", f"an action rule names params.action: {', '.join(ACTIONS)}", 422)
    return scope_id, when, params


def can_change(c, settings, auth, who, scope_kind, scope_id):
    """Who edits a row: team rows the owner and admins; type rows task movers; department rows movers and that
    department's members; bot rows the bot's owners, operator and BotOps; task rows the task's movers and parties."""
    if not who or not auth or who.support or who.role not in ("owner", "human", "bot"):
        return False
    admin = who.role == "owner" or auth.bot_admin(who)
    if scope_kind == "team":
        return admin
    mover = admin or (who.role == "human" and H.can_move(c, who.actor))
    if scope_kind == "type":
        return mover
    if scope_kind == "department":
        from . import task_rules
        return mover or who.role == "human" and task_rules.can_change(c, settings, auth, who, scope_id)
    if scope_kind == "bot":
        return bool(auth.bot_manager(c, who, scope_id) or auth.botops_manages(c, who, scope_id))
    if scope_kind == "task":
        row = H.task(c, scope_id)
        return bool(row and H.task_private_readable(c, who.actor, row)
                    and (mover or who.actor in (row.get("owner"), row.get("requester"))))
    return False


def _require(c, settings, auth, who, scope_kind, scope_id):
    from .store import Problem
    auth.domain(who)
    if not can_change(c, settings, auth, who, scope_kind, scope_id):
        raise Problem("forbidden", {
            "team": "Only the owner or an admin changes team wake rules",
            "type": "Only task movers change a task type's wake rules",
            "department": "Only task movers and the people in this department change its wake rules",
            "bot": "Only a bot's owners, its operator or BotOps change its wake rules",
            "task": "Only the task's movers and parties change its wake rules"}[scope_kind], 403)


def _action_writer(c, auth, who, then, params):
    """An action row runs as Tico (the keeper), so only someone who could make that change on any task writes one:
    the owner, an admin or a person who moves tasks; and a task it names must be one they may read."""
    from .store import Problem
    if then != ACTION:
        return
    if not (who.role == "owner" or auth.bot_admin(who) or who.role == "human" and H.can_move(c, who.actor)):
        raise Problem("forbidden", "An action rule acts as Tico: only the owner, an admin or a task mover writes one",
                      403)
    for key in ("task", "blocked_by"):
        named = H.task(c, str(params.get(key) or "")) if params.get(key) else None
        if params.get(key) and not (named and H.task_private_readable(c, who.actor, named)):
            raise Problem("not_found", f"No task {params[key]}", 404)


def _visible(c, who, row):
    """Whether the viewer may see a table row: every team, type, department and bot row; a task's rows when they
    can read the task."""
    kind, ident = row["scope_kind"], row["scope_id"]
    if kind == "task":
        task = H.task(c, ident)
        return bool(task and H.task_private_readable(c, who.actor, task))
    return True


def listing(c, settings, auth, who, scope_kind=None, scope_id=None, event=None, limit=200, offset=0):
    """Built-ins first (on the first page of an unfiltered or built-in listing), then the table rows the viewer may
    see, each with its 7-day run count and no-op rate and whether the viewer may change it. Paged and byte-capped:
    `more` says another page waits, at `next_offset`."""
    limit = max(1, min(int(limit or 200), MAX_ROWS))
    counts = usage(c, who.actor)
    items = []
    if scope_kind in (None, "builtin") and not offset and not scope_id:
        items = [_with_usage({**r, "can_change": False}, counts) for r in BUILTINS if event in (None, r["event"])]
    more = False
    if scope_kind == "bot" and scope_id and not offset:
        items += [_with_usage(r, counts) for r in profile_listing(c, scope_id) if event in (None, r["event"])]
    if scope_kind != "builtin":
        sql, args = "SELECT * FROM rules WHERE 1=1", []
        for column, value in (("scope_kind", scope_kind), ("scope_id", scope_id), ("event", event)):
            if value:
                sql += f" AND {column}=?"
                args.append(value)
        sql += " ORDER BY position, created, id LIMIT ? OFFSET ?"
        rows = c.execute(sql, (*args, limit + 1, offset)).fetchall()
        more = len(rows) > limit
        spent = len(json.dumps(items))
        for n, row in enumerate(rows[:limit]):
            if spent > MAX_LIST_BYTES:          # byte-capped as well as paged: the next page starts at this row
                more, next_offset = True, offset + n
                break
            if _visible(c, who, row):
                item = _with_usage({**view(row), "can_change": can_change(
                    c, settings, auth, who, row["scope_kind"], row["scope_id"])}, counts)
                items.append(item)
                spent += len(json.dumps(item))
        else:
            next_offset = offset + min(len(rows), limit)
    out = {"rules": items, "events": list(EVENTS), "outcomes": list(OUTCOMES), "scopes": list(SCOPES), "more": more}
    if more:
        out["next_offset"] = next_offset
    return out


def get(c, rule_id):
    from .store import Problem
    row = c.execute("SELECT * FROM rules WHERE id=?", (rule_id,)).fetchone()
    if not row:
        if rule_id in BUILTIN_IDS or rule_id in PROFILE_RULE_IDS:
            raise Problem("forbidden", "Built-in rules are read-only; override one with a scoped rule", 403)
        raise Problem("not_found", "No such rule", 404)
    return row


def create(c, settings, auth, who, body):
    from .store import Problem
    scope_id, when, params = validate(c, settings, body.scope_kind, body.scope_id, body.event, body.then,
                                      body.when, body.params)
    _require(c, settings, auth, who, body.scope_kind, scope_id)
    _action_writer(c, auth, who, body.then, params)
    if c.execute("SELECT count(*) FROM rules").fetchone()[0] >= MAX_ROWS:
        raise Problem("conflict", "The rule list is full; edit or delete a rule", 409)
    position = body.position
    if position is None:
        position = (c.execute("SELECT max(position) FROM rules WHERE scope_kind=? AND scope_id=?",
                              (body.scope_kind, scope_id)).fetchone()[0] or 0) + 1
    rule_id, at = "rule-" + H.new_id(), H.now()
    c.execute("INSERT INTO rules(id,scope_kind,scope_id,event,when_json,then_outcome,params_json,enabled,position,"
              "note,created_by,created,updated,revision) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
              (rule_id, body.scope_kind, scope_id, body.event, json.dumps(when, sort_keys=True), body.then,
               json.dumps(params, sort_keys=True), int(body.on is not False), position, body.note or "",
               who.actor, at, at))
    H.event(c, who.actor, "rule.created", rule_id, {"after": view(get(c, rule_id))})
    return {"rule": view(get(c, rule_id))}


def update(c, settings, auth, who, rule_id, body):
    from .store import Problem
    before = get(c, rule_id)
    _require(c, settings, auth, who, before["scope_kind"], before["scope_id"])
    if before["revision"] != body.expected_revision:
        raise Problem("version_conflict", "The rule changed; refresh before saving", 409)
    event = body.event if body.event is not None else before["event"]
    then = body.then if body.then is not None else before["then_outcome"]
    when = body.when if body.when is not None else H._json(before["when_json"], {})
    params = body.params if body.params is not None else H._json(before["params_json"], {})
    validate(c, settings, before["scope_kind"], before["scope_id"], event, then, when, params)
    if then == ACTION and (body.on is True or any(v is not None for v in (body.then, body.event, body.when, body.params))):
        _action_writer(c, auth, who, then, params)       # turning one off, moving or deleting it needs only the scope
    c.execute("UPDATE rules SET event=?,when_json=?,then_outcome=?,params_json=?,enabled=?,position=?,note=?,"
              "updated=?,revision=revision+1 WHERE id=?",
              (event, json.dumps(when, sort_keys=True), then, json.dumps(params, sort_keys=True),
               int(body.on) if body.on is not None else before["enabled"],
               body.position if body.position is not None else before["position"],
               body.note if body.note is not None else before["note"], H.now(), rule_id))
    H.event(c, who.actor, "rule.updated", rule_id, {"before": view(before), "after": view(get(c, rule_id))})
    return {"rule": view(get(c, rule_id))}


def delete(c, settings, auth, who, rule_id, expected_revision=None):
    from .store import Problem
    before = get(c, rule_id)
    _require(c, settings, auth, who, before["scope_kind"], before["scope_id"])
    if expected_revision is not None and before["revision"] != expected_revision:
        raise Problem("version_conflict", "The rule changed; refresh before deleting", 409)
    c.execute("DELETE FROM rules WHERE id=?", (rule_id,))
    H.event(c, who.actor, "rule.deleted", rule_id, {"before": view(before)})
    return {"deleted": rule_id}


def install(app, store, auth, mutate):
    from fastapi import Request
    from . import models as M
    from .store import Problem

    @app.get("/api/v2/rules")
    def list_rules(request: Request, scope_kind: str | None = None, scope_id: str | None = None,
                   event: str | None = None, limit: int = 200, offset: int = 0):
        """Every wake rule the viewer may see: the built-ins (read-only) and the table rows, each with its 7-day run
        count and no-op rate. Paged: `limit` table rows from `offset`."""
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            return listing(c, store.settings, auth, who, scope_kind, scope_id, event, limit, max(0, offset))

    @app.get("/api/v2/rules/{rule_id}")
    def read_rule(rule_id: str, request: Request):
        """One rule (a built-in or a table row) with its 7-day figures."""
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            found = next((r for r in BUILTINS if r["id"] == rule_id), None)
            counts = usage(c, who.actor)
            if found:
                return {"rule": _with_usage({**found, "can_change": False}, counts)}
            row = get(c, rule_id)
            if not _visible(c, who, row):
                raise Problem("not_found", "No such rule", 404)
            return {"rule": _with_usage({**view(row), "can_change": can_change(
                c, store.settings, auth, who, row["scope_kind"], row["scope_id"])}, counts)}

    @app.post("/api/v2/rules")
    def create_rule(request: Request, body: M.RuleCreate):
        who = request.state.identity
        return mutate(request, body, lambda c: create(c, store.settings, auth, who, body))

    @app.post("/api/v2/rules/{rule_id}")
    def update_rule(rule_id: str, request: Request, body: M.RuleUpdate):
        who = request.state.identity
        return mutate(request, body, lambda c: update(c, store.settings, auth, who, rule_id, body))

    @app.post("/api/v2/rules/{rule_id}/delete")
    def delete_rule(rule_id: str, request: Request, body: M.RuleDelete):
        who = request.state.identity
        return mutate(request, body, lambda c: delete(c, store.settings, auth, who, rule_id, body.expected_revision))
