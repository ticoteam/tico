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
- `action`: run the row's `params.action` (close, move_step, set_wait, relate, note, assign, add_tag, remove_tag) as
  the keeper, with the rule id in the event payload, and write the message quietly with its note. An action that fails opens the message: it wakes the
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
matches one event (or `*`, every event that is neither always-wake nor awaited) and optional conditions (`condition`), and the
first match wins by scope, most specific first: task, bot, department, type, team, then the built-ins; inside a
scope by `position`. The always-wake classes are a floor no row lowers: a person is never made to wait by a rule.
A bot's profile plugs in at the bot scope: `profile_of` names it and PROFILE_RULES holds each profile's built-in
rows (a profile that is not listed adds none).

An event that is not a message to a bot (an incoming webhook, a release) has no task and no message to judge:
`judge_event` runs its rows instead (team, department and bot scopes), writes what they say, and never fails open: an
error or no match does nothing. See "Events without a task" below.

Fail open: anything the engine cannot read wakes, under `fail_open`. The hot path costs one indexed read of the
rules table for a message that no built-in always-wakes, and nothing more when the table is empty.
"""

import contextvars
import json
import logging
import sqlite3
from typing import NamedTuple, Optional

from . import event_catalog as CAT, hubdb as H, templating, tico_notices, task_trigger_rules as TTR

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
# What a code action can do, for `action` rows (system_routines' THEN names). The task actions need a task; the event
# actions (`wake_bot`, `send_webhook`) do not, so they also run from events without one (judge_event).
TASK_ACTIONS = ("close", "move_step", "set_wait", "relate", "note", "assign", "add_tag", "remove_tag")
TAG_ACTIONS = ("add_tag", "remove_tag")
EVENT_ACTIONS = ("wake_bot", "send_webhook", "create_task", "forward", "finance_freeze_cards", "finance_pause_budget",
                 "finance_act")
# The two that make something new from an event judged once (judge_event) refuse a message event: its ledger is what
# makes a repeated call do nothing.
ONCE_ACTIONS = ("create_task", "forward")
ACTIONS = TASK_ACTIONS + EVENT_ACTIONS
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
    "incident_news": "news about a task marked as an incident wakes the bot at once",
    "task_action": "a stale-check suggestion on a task it owns needs its action now",
    "task_blocker": "a blocker or refused move on a task it owns or asked for needs action now",
    "direct_review_request": "a review or sign-off asked of this bot needs action now",
    "builder_handoff": "a builder hand-off on its Dev ticket: a verdict to act on, a failed check, a merge conflict or "
                       "a merge",
}
# Structured task news that wakes at once when no row already wakes it or keeps it quiet.
URGENT_EVENTS = ("incident_news", "task_action", "task_blocker", "direct_review_request")
# wake_rules.always_wakes key -> (event, rule id)
_FLOOR = {"person": "person_message", "approval": "approval", "needs_action": "needs_action", "reply": "reply",
          "gate": "verdict", "handoff": "builder_handoff"}
# wake_rules.AWAITED key -> event
AWAITED_EVENTS = {
    "blocker_done": ("blocker_done", "a task this one waits on (blocked_by) finished"),
    "wake_once": ("pr_checks_done", "a pull request link armed with wake_once got its result"),
    "subtask": ("subtask_finished", "a subtask moved or finished"),
    "worktree": ("worktree", "the worktree the task waited for is ready"),
    "watcher": ("watcher", "something the bot watches moved or ended"),
    "resume": ("resume", "a deferred task's stored resume condition came true"),
    "milestone": ("milestone", "a pull request it waits for merged or a release it waits for was published"),
    "reconnected": ("reconnected", "the account or credential its reconnect card waited for works again"),
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
    "due_at": "a task's due date came",
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
    "bookkeeper_check": "the Bookkeeper's Books check found new Finance work",
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
    "exceptions": "the type manager's exceptions list has a new entry",
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
# What a Manager-profile bot hears of as a note though it wakes every other bot: a ticket's verdicts and builder
# hand-offs (wake_rules.always_wakes keys), its subtasks' and blockers' results (wake_rules.awaited keys) and a
# blocker or refused move on a ticket it asked for (urgent_rule's task_blocker). Its exceptions list carries what of
# those does not move on; a person, an approval, a needs-action flag, a reply it waits on, an incident, its own
# wake_once, resume, watcher, milestone and worktree waits still wake it.
MANAGER_NOTE = "profile_manager_note"
MANAGER_QUIET = frozenset({"gate", "handoff", "subtask", "blocker_done", "task_blocker"})
EVENTS = {**FLOOR_EVENTS, **WAKE_EVENTS, **NOTE_EVENTS, **{v[0]: v[1] for v in AWAITED_EVENTS.values()},
          **{"batch_" + k: v for k, v in BATCH_EVENTS.items()}}
# refs.wake spellings the writers use -> the event a rule names.
WAKE_ALIASES = {"stalled": "stall", "stuck_retry": "stall", "stuck_cause": "stall", "occurrence": "routine",
                "next-run": "next_run", "background-done": "background_done", "watcher-done": "watcher",
                "held-messages": "backlog", "step_status": "status_check"}
# `trigger` (an incoming webhook's id) and `type` (its extracted type, a trailing * matches a prefix) are read only for
# events without a task.
WHEN_KEYS = ("actor", "step", "role", "tag", "state", "trigger", "type")
EVENT_WHEN_KEYS = ("trigger", "type")
# The events without a task, and the only scopes a row for one can have (there is no task and no task type).
SOURCELESS = frozenset(CAT.sourceless())
EVENT_SCOPES = ("team", "department", "bot")
WHEN_ACTORS = ("human", "bot", "keeper")
WHEN_ROLES = ("owner", "requester", "qa", "release_manager", "other")

# A bot's profile (backend/profiles.py) plugs in at the bot scope. `profile_of` names it and PROFILE_RULES holds each
# profile's built-in rows, shaped like `rules` rows (a profile that is not listed adds none; `custom` has its own
# rows in the table). A row naming an event is tried at the bot scope after the bot's own rows; a `*` row is the
# profile's "everything else" and is tried last of all, after the team's rows, so a type or team rule that names an
# event (a release waking QA) still reaches a bot whose profile makes everything else a note.
PROFILES = ("doer", "reviewer", "lead", "manager", "responder", "monitor", "custom")
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
            "id": rule, "event": event, "then_outcome": then, "params": "{}", "words": words,
            "when": when or {}, "condition": json.dumps(when or {}, sort_keys=True)})

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
    # A manager works by exception: its schedule, a person, work given to it and its exceptions list wake it; a ticket's
    # steps, its subtasks' results, checks, verdicts and late or backlog notices wait for that run.
    add("manager", "profile_manager_assignment", "assignment", WAKE, "a task was given to it")
    add("manager", "profile_manager_request", "review_request", WAKE, "a sign-off was asked of it")
    add("manager", "profile_manager_exceptions", "exceptions", WAKE, "its exceptions list has a new entry")
    timers("manager", OWN_TIMERS + OWN_WORK)
    add("manager", MANAGER_NOTE, "*", NOTE, "anything else: ticket news waits for its next run")
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
        row = conn.execute("SELECT json_extract(config,'$.profile') FROM bot_config WHERE bot=?",
                           (H.actor_id(bot),)).fetchone()
    except sqlite3.OperationalError:
        return "doer"
    return row[0] if row and row[0] in PROFILES else "doer"


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
    by: Optional[str] = None            # task action's effective writer; event actions retain the row's creator
    notice_only: bool = False           # read urgent news without starting the work its dependency holds


PARKED_NOTICE = "parked_urgent_notice"


def notice_only(message):
    """Only the keeper's server-stamped urgent notices run without attaching their held task."""
    return bool(message and message.get("sender_id") == H.KEEPER and message.get("kind") == "notice"
                and (message.get("refs") or {}).get(PARKED_NOTICE) is True)


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
    if task and task.get("owner_id") == target and task.get("status") in WORKING:
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
    for event in URGENT_EVENTS:
        add(event, event, WAKE, FLOOR_EVENTS[event], "always", True)
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
 condition TEXT NOT NULL DEFAULT '{}', then_outcome TEXT NOT NULL, params TEXT NOT NULL DEFAULT '{}',
 enabled INTEGER NOT NULL DEFAULT 1, position INTEGER NOT NULL DEFAULT 0, note TEXT NOT NULL DEFAULT '',
 created_by TEXT NOT NULL, action_by TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1);
CREATE INDEX IF NOT EXISTS rules_event ON rules(event, enabled);
CREATE INDEX IF NOT EXISTS rules_scope ON rules(scope_kind, scope_id);
"""


def ensure_schema(c):
    """Keep creator attribution separate from the person responsible for a task action."""
    H.add_column(c, "rules", "action_by", "TEXT")
    # Only an unedited legacy rule is known to carry its creator's action. Revised legacy rules fail open on private
    # tasks until someone saves their action again, rather than borrowing an unknown editor's creator permissions.
    c.execute("UPDATE rules SET action_by=created_by WHERE action_by IS NULL AND revision=1")


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
    trigger = run_triggers.of_message({"refs": refs, "sender_id": actor})[0]
    if trigger in ("comment", "status_or_relation") or actor == H.KEEPER:
        label = wake_presets.classify({"refs": refs, "sender_id": actor, "body": body, "kind": kind})[0]
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
            gid = ((people.person(self.slug, roster) or {}).get("team") if H.is_human(self.target) else
                   people.team_of(self.slug, groups._configs(self.conn), roster))
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
            elif key in ("owner", "requester"):
                if task.get(key) not in wanted:
                    return False
            elif key == "type":
                names = {str(task.get("type_id") or "").lower()}
                found = self.conn.execute("SELECT name FROM task_types WHERE id=?", (task.get("type_id"),)).fetchone()
                if found:
                    names.add(str(found[0]).lower())
                if not names & {str(w).lower() for w in wanted}:
                    return False
            elif key == "role":
                mine = {"owner" if task.get("owner") == self.target else
                        "requester" if task.get("requester_id") == self.target else "other"}
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
    params = H._json(row["params"], {}) or {}
    if outcome == NOTE:
        return Decision(NOTE, row["id"], HOLD_ROW, params)
    if outcome == IGNORE:
        return Decision(IGNORE, row["id"], HOLD_IGNORED, params)
    if outcome == BATCH:
        # Only Tico's own notices can be held at enqueue (the wake_preset_hold trigger); a message from anyone
        # else wakes, with the rule still named, rather than risk a wake nobody sees.
        return Decision(BATCH if actor == H.KEEPER else WAKE, row["id"], None, params)
    if outcome in (ASK, ACTION):
        by = row["created_by"]
        if outcome == ACTION and params.get("action") in TASK_ACTIONS:
            by = dict(row).get("action_by") or (by if row["revision"] == 1 else None)
        return Decision(outcome, row["id"], None, params, by)
    return Decision(WAKE, row["id"], None, params)


def _table_decision(conn, event, actor, target, refs, conv, task_row=None):
    """The decision of the first table or profile row that matches, or None. One indexed read, nothing more when
    no row names this event and no profile has rows."""
    if event in FLOOR_EVENTS:
        return None
    try:
        rows = conn.execute("SELECT * FROM rules WHERE enabled=1 AND event IN (?, '*') "
                            "ORDER BY position, event='*', created_at, id", (event,)).fetchall()
    except sqlite3.OperationalError:            # a database from before the table: no rows
        rows = []
    from . import redesign_migration
    converted = redesign_migration.wake_rows(conn, event)   # rules the redesign moved into "What wakes" automations
    if converted:
        rows = sorted([*rows, *converted], key=lambda r: (r["position"], r["event"] == "*", r["created_at"], r["id"]))
    slug, profile = H.actor_id(target), []
    if PROFILE_RULES:
        profile = [{**r, "scope_kind": "bot", "scope_id": slug} for r in PROFILE_RULES.get(profile_of(conn, slug), ())
                   if r["event"] in (event, "*")]
    if event in AWAITED_NAMES or event in ("digest", "step_entered"):
        # What the bot waits on wakes it, a digest is held news let go, and a step move is news only for a bot a row
        # names: a `*` row (a lead's batch-everything, a note-everything) reaches none of them, only a row that names
        # the event.
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
            if ctx.in_scope(row) and ctx.matches(H._json(row["condition"], {}) or {}):
                return _row_decision(row, actor)
    for row in rest:                            # the profile's "everything else", after every scope that named an event
        if ctx.matches(H._json(row["condition"], {}) or {}):
            return _row_decision(row, actor)
    return None


def incident_task(conn, task):
    """The persisted marker, never a title match when judging news."""
    if not task:
        return False
    if "incident" in H.task_labels(task):
        return True
    task_type = conn.execute("SELECT name FROM task_types WHERE id=?", (task.get("type_id"),)).fetchone()
    return bool(task_type and task_type[0].strip().lower() in ("incident", "incidents"))


def urgent_rule(conn, actor, target, refs, conv, event):
    """Structured task news that wakes at once when no row already wakes it or keeps it quiet. A row that
    wakes keeps its own rule name; an explicit ignore or quiet marker keeps the silence. Own writes stay quiet."""
    from . import wake_rules
    if wake_rules.CAUSE.get() == target or actor == target:
        return None
    ctx = _Context(conn, actor, target, refs, conv, None)
    task = ctx.task
    if task:
        if incident_task(conn, task):
            return "incident_news"
        party = target in (task.get("owner_id"), task.get("requester_id"))
        if refs.get("stale_check") and target == task.get("owner_id"):
            return "task_action"
        if party and (event in ("blocker", "task_blocked") or refs.get("review_gate")):
            return "task_blocker"
    if event in ("review_request", "review_handback"):
        return "direct_review_request"
    return None


# ----------------------------------------------------------------------------- the decision
def judge(conn, actor, target, kind, refs, body, conv, in_reply_to=None, decided=None):
    """The decision for a message about to be written to `target`, or None when `target` is not a bot. Fail open.
    `decided` is a decision the writer made before this (a plain bot-to-bot message waiting under `bot_to_bot`)."""
    if not str(target).startswith("bot:"):
        return None
    try:
        found = _judge(conn, actor, target, kind, refs, body, conv, in_reply_to, decided)
        if found.outcome == WAKE and actor == H.KEEPER and kind == "notice":
            from . import wake_rules
            task = _Context(conn, actor, target, refs, conv, None).task
            if (task and not H.TS.snoozed(task) and wake_rules.parked(conn, task) == "blocked_by"
                    and urgent_rule(conn, actor, target, refs, conv, event_of(actor, kind, refs, body))):
                # This changes delivery only, after the decision and its silence controls. Preserve the rule id.
                found = found._replace(notice_only=True)
        return found
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
    if _quiet(conn, target, floor):
        return Decision(NOTE, MANAGER_NOTE, HOLD_ROW)
    if floor:
        return Decision(WAKE, floor_rule(floor, kind))
    event = event_of(actor, kind, refs, body)
    row = _table_decision(conn, event, actor, target, refs, conv)
    if row is None:
        row = _pr_decision(conn, event, target, refs, conv)
    if row is not None:
        if row.outcome in (WAKE, IGNORE):
            return row
        urgent = urgent_rule(conn, actor, target, refs, conv, event)
        if urgent and not _quiet(conn, target, urgent):
            return Decision(WAKE, urgent)
        return row
    key = wake_rules.awaited(refs)
    if _quiet(conn, target, key):
        return Decision(NOTE, MANAGER_NOTE, HOLD_ROW)
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
    if (held == "parked" and wake_rules.parked(
            conn, _Context(conn, actor, target, refs, conv, None).task) == "blocked_by"):
        urgent = urgent_rule(conn, actor, target, refs, conv, event)
        if urgent:
            return Decision(WAKE, urgent)
    if held:
        return Decision(NOTE, held, held)
    outcome, rule = _rule_for_event(event)
    if outcome == WAKE:
        return Decision(outcome, rule)
    urgent = urgent_rule(conn, actor, target, refs, conv, event)
    if urgent and not _quiet(conn, target, urgent):
        return Decision(WAKE, urgent)
    return Decision(outcome, rule, HOLD_ROW if outcome == NOTE else None)


def _quiet(conn, target, key):
    """Whether this floor, awaited or urgent key is only a note for the target (MANAGER_QUIET)."""
    return key in MANAGER_QUIET and profile_of(conn, H.actor_id(target)) == "manager"


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
    out.pop(PARKED_NOTICE, None)          # callers cannot supply a notice-only dispatch capability
    if decision.notice_only:
        out[PARKED_NOTICE] = True
    out.pop("wake_immediate", None)
    if decision.outcome == WAKE and decision.rule in (*URGENT_EVENTS, "needs_action", "ask"):
        # Urgent news and a question or flagged action for the bot skip every hold; the trigger reads this.
        out.pop("wake_batch", None)
        out["wake_immediate"] = True
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
        with TTR.from_action(decision.rule):
            if decision.outcome == ASK:
                _ask(conn, decision, row, actor, body)
            else:
                _act(conn, decision, row, {"actor": actor, "target": target, "kind": kind, "refs": refs or {},
                                           "body": body})
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
                    "AND json_extract(e.detail,'$.rule')=? AND json_extract(e.detail,'$.task')=? "
                    f"AND t.status IN ({','.join('?' * len(H.ACTIVE_STATUSES))}) LIMIT 1",
                    (ASKED, decision.rule, row["id"], *H.ACTIVE_STATUSES)).fetchone():
        return
    who = row["requester_id"] if H.is_human(row["requester_id"]) else None
    if not who and H.is_bot(row["owner_id"]):
        from . import usage_limits
        person = usage_limits.recipients(conn, H.actor_id(row["owner_id"]))
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


def _act(conn, decision, row, message=None):
    """Run `params.action` as the keeper: a task action on the message's task, an event action (`wake_bot`,
    `send_webhook`) with or without one. `message` is the message being judged, for the event actions' templates."""
    params = decision.params or {}
    action = params.get("action")
    run = EVENT_RUNNERS.get(action)
    if run:
        values = _message_values(conn, row, message or {})
        found = run(conn, decision.rule, decision.by, params, values, event=values["event"],
                    subject=row["id"] if row else decision.rule, revision=None, task=row, ctx={})
        if not found["ok"] and action == "wake_bot":
            raise ValueError(found["detail"])           # the bot was not told: open the message instead (fail open)
        H.event(conn, H.KEEPER, "rule.action", row["id"] if row else decision.rule,
                {"rule": decision.rule, "action": action, "ok": found["ok"], "detail": found["detail"]})
        return
    runner = TASK_RUNNERS.get(action)
    if not runner:
        raise ValueError(f"unknown action {action!r}")
    if not row:
        raise ValueError("an action rule needs a task")
    # The keeper passes the private-task check, so the rule's writer is held to it here: a rule never changes a
    # private task its writer could not change by hand. Failing opens the message, so the owner still hears of it.
    if H.task_private(conn, row) and decision.by != H.KEEPER \
            and (not decision.by or not H.task_private_readable(conn, decision.by, row)):
        raise ValueError("the rule's writer cannot read this private task")
    note = str(params.get("note") or params.get("text") or "")[:1000]
    said = f"Rule {decision.rule}: {note}" if note else f"Rule {decision.rule}"
    done = runner(conn, row["id"], params, said) or {}
    H.event(conn, H.KEEPER, "rule.action", row["id"], {"rule": decision.rule, "action": action, **done})


def _close(conn, tid, params, said):
    H.task_close(conn, H.KEEPER, tid, note=said)


def _move_step(conn, tid, params, said):
    H.task_update(conn, H.KEEPER, tid, step=str(params["step"]), note=said)


def _set_wait(conn, tid, params, said):
    if params.get("blocked_by"):
        from . import task_relations as TR
        TR.add_blocker(conn, H.KEEPER, tid, str(params["blocked_by"]), mover=True, note=said)
    else:
        H.task_update(conn, H.KEEPER, tid, status="waiting", waiting_on=str(params["waiting_on"]), note=said)


def _relate(conn, tid, params, said):
    from . import task_relations as TR
    TR.relate(conn, H.KEEPER, tid, str(params["task"]), kind=str(params.get("kind") or "related"), mover=True,
              note=said)


def _note(conn, tid, params, said):
    H.task_update(conn, H.KEEPER, tid, note=said)


def _assign(conn, tid, params, said):
    # The same write a person's reassignment makes, so the new owner is woken and the old one told as usual. The keeper
    # passes the private-task check, so it is made here: a rule never hands a private task to someone who may not read
    # it. Failing opens the message, so the current owner is still woken.
    owner, task = str(params["owner"]), H.task(conn, tid)
    if H.task_private(conn, task) and not H.task_private_readable(conn, owner, task):
        raise ValueError("the new owner cannot read this private task")
    H.task_update(conn, H.KEEPER, tid, owner=owner, note=said)
    return {"owner_id": owner}


def _retag(add):
    def run(conn, tid, params, said):
        have, named = H.task_labels(H.task(conn, tid)), list(params["tag"])
        keys = have + [k for k in named if k not in have] if add else [k for k in have if k not in named]
        if keys != have:                # a tag already on (or already off) writes nothing and mirrors no note
            H.task_update(conn, H.KEEPER, tid, labels=keys, note=said)
        return {"tag": named, "changed": keys != have}
    return run


TASK_RUNNERS = {"close": _close, "move_step": _move_step, "set_wait": _set_wait, "relate": _relate, "note": _note,
                "assign": _assign, "add_tag": _retag(True), "remove_tag": _retag(False)}


# ----------------------------------------------------------------------------- events without a task
# An incoming webhook, a release, a health finding: nothing to judge as a message to a bot and no task to hang a row on.
# `judge_event` reads the rows for the event itself (team, department and bot scopes) and does what they say. Unlike
# `judge` it never fails open: that exists to save a message to a bot, and here it would start a bot on a bug with an
# outsider's text. No match does nothing, and an error does nothing and is logged (`rule.event_error`).
EVENT_KEEP_DAYS = 30
EVENT_ERROR = "event_error"
# Bot rows first, then department, then team; inside a scope by position.
EVENT_PRECEDENCE = ("bot", "department", "team")
MAX_TEXT = 4096
BOT_STATES_OK = ("active", *H.HELD_STATES)   # a held bot keeps what it is told for when it is back


def _slug(value):
    value = str(value or "").strip()
    return value[4:] if value.startswith("bot:") else value


def event_is_sourceless(event):
    """Whether a rule's `event` names events without a task: a catalog name marked so, or a prefix pattern
    (`webhook.*`) that reaches one. A bare `*` stays what it is for messages."""
    if event in SOURCELESS:
        return True
    return isinstance(event, str) and event.endswith("*") and event != "*" and any(
        CAT.matches(event, name) for name in SOURCELESS)


def _event_when(when, ctx):
    """Whether a row's `trigger` and `type` hold for this call; each is a word or a list, `type` with a trailing *."""
    for key, want in when.items():
        wanted = want if isinstance(want, list) else [want]
        if key == "trigger":
            if str(ctx.get("trigger") or "") not in [str(w) for w in wanted]:
                return False
        elif key == "type":
            if not any(CAT.matches(str(w), str(ctx.get("type") or "")) for w in wanted):
                return False
        else:
            return False                    # a condition that needs a task never matches here
    return True


def _event_values(name, ctx):
    """What a row's templates read for an event without a task: the caller's context, the event, `trigger.id` and
    `trigger.name`, and the payload as `data` (and `payload`)."""
    trigger = ctx.get("trigger")
    if not isinstance(trigger, dict):
        trigger = {"id": str(trigger or ""), "name": str(ctx.get("trigger_name") or "")}
    data = ctx["payload"] if "payload" in ctx else ctx.get("data")
    return {**ctx, "event": name, "trigger": trigger, "data": data, "payload": data,
            "type": str(ctx.get("type") or ""), "content": str(ctx.get("content") or "")}


def _message_values(conn, task, message):
    """What a row's templates read when an event action runs on a message to a bot: the event, the task, who wrote
    the message and its text."""
    refs = message.get("refs") or {}
    event = event_of(message.get("actor"), message.get("kind"), refs, message.get("body")) if message else ""
    out = {"event": event, "text": str(message.get("body") or ""), "actor": str(message.get("actor") or ""),
           "to": H.actor_id(message.get("target") or "")}
    if task:
        out["task"] = {key: task.get(key) for key in ("id", "title", "status", "owner", "requester", "step_id")}
    return out


def _bot_room(conn, slug):
    """The room a bot's own work shows in: the shared room of a shared bot, else its operator's room with it, else a
    notice room of its own. The same choice rooms.work_room makes for a task, without the request's identity."""
    from . import rooms
    if rooms.thread_mode(conn, slug) == rooms.SHARED:
        found = conn.execute("SELECT id FROM conversations WHERE scope='shared' AND room_key=? AND closed_at IS NULL "
                             "ORDER BY COALESCE(last_message_at,created_at) DESC LIMIT 1", (slug,)).fetchone()
        if found:
            return H.conversation(conn, found["id"])
    stored = conn.execute("SELECT owner_id FROM bot_config WHERE bot=?", (slug,)).fetchone()
    if stored and stored["owner_id"] and H.human(conn, stored["owner_id"]):
        bot = H.bot(conn, slug) or {}
        name = bot.get("display_name") or bot.get("slug") or slug
        return rooms.personal_room(conn, H.human_actor(stored["owner_id"]), slug, "Chat with " + name)
    return H._pair_conversation(conn, H.KEEPER, H.bot_actor(slug), "notice", allow_held=True)


def _tell_bot(conn, rule_id, slug, text, refs, *, wake, task=None, room=None):
    """The keeper's notice to a bot, decided here: it wakes the bot (`wake`) or waits quietly as a note. With a task it
    goes in the task's room, and only to a bot that may read the task; else in `room` when given (an automation's
    note to its owner's Assistant), else the bot's own room. Returns (ok, detail)."""
    found = H.bot(conn, slug) if slug else None
    if not found:
        return False, f"No bot {slug}" if slug else "The rule names no bot"
    if found["state"] not in BOT_STATES_OK:
        return False, f"{found['slug']} is {found['state']}"
    target = H.bot_actor(slug)
    if task:
        if not H.task_private_readable(conn, target, task):
            return False, f"{found['slug']} may not read this task"
        conv = H.conversation(conn, task["conversation_id"])
        refs = {**refs, "task": task["id"]}
    else:
        conv = room or _bot_room(conn, slug)
    decided = Decision(WAKE, rule_id) if wake else Decision(NOTE, rule_id, HOLD_ROW)
    H._write_message(conn, H.KEEPER, target, text, conv, "notice", refs, None, None, decided=decided)
    return True, "woke it" if wake else "left it a note"


def _default_text(event, values):
    if event == "webhook.received":
        return f"{(values.get('trigger') or {}).get('name') or 'A webhook'}: {values.get('type') or 'a call'} arrived."
    return f"{event}: a rule sent you this."


def _wake_refs(event, rule_id, ctx):
    """What the wake carries for the run: the event, the rule, and which webhook call it was."""
    return {"wake": event, "rule_id": rule_id, **{k: ctx[k] for k in ("trigger", "type", "delivery", "via")
                                                  if ctx.get(k) and not isinstance(ctx[k], dict)}}


def _wake_text(params, values, ctx, default):
    """The rendered `params.text` (4 KB), then the body of the delivery when the call carries one, then, when that body
    was cut, one line saying how to read the rest."""
    text = templating.render(str(params.get("text") or ""), values, cap=MAX_TEXT).strip() or default
    parts = [text]
    if ctx.get("content"):
        parts.append(str(ctx["content"]))
    if ctx.get("cut") and ctx.get("trigger") and ctx.get("delivery") and not isinstance(ctx.get("trigger"), dict):
        parts.append(f"The rest: `hub trigger delivery {ctx['trigger']} {ctx['delivery']} --part 2`.")
    return "\n\n".join(parts)


def _run_wake_bot(conn, rule_id, by, params, values, *, event, subject, revision, task=None, ctx=None):
    ctx = ctx or {}
    slug = _slug(params.get("bot"))
    slug = (H.resolve_bot(conn, slug) or {}).get("id", slug)
    ok, detail = _tell_bot(conn, rule_id, slug, _wake_text(params, values, ctx, _default_text(event, values)),
                           _wake_refs(event, rule_id, ctx), wake=True, task=task)
    return {"outcome": "wake_bot", "target": slug, "ok": ok, "detail": detail}


def _run_send_webhook(conn, rule_id, by, params, values, *, event, subject, revision, task=None, ctx=None):
    hook, kind = str(params.get("webhook") or ""), str(params.get("type") or "")
    data = templating.render_json(params.get("data") or {}, values)
    return _send_out(conn, "send_webhook", by, hook, kind, subject, data, revision)


def _send_out(conn, outcome, by, hook, kind, subject, data, revision):
    result = {"outcome": outcome, "target": hook, "ok": False, "detail": ""}
    try:
        from . import webhooks
        publish, refused = webhooks.publish, webhooks.Refused
    except (ImportError, AttributeError):
        result["detail"] = "Outgoing webhooks are not available"
        return result
    try:
        queued = publish(conn, kind, str(subject), data if isinstance(data, dict) else {"data": data}, by or H.KEEPER,
                         only=hook, revision=revision)
    except refused as exc:
        result["detail"] = str(getattr(exc, "reason", "") or exc)[:300]
        return result
    return {**result, "ok": True, "detail": f"{queued} queued"}


def _run_forward(conn, rule_id, by, params, values, *, event, subject, revision, task=None, ctx=None):
    """`send_webhook` of the incoming call itself: its payload is `data` (an object as it is, anything else under
    `data`), its type `custom.forward.<trigger id>`, so a receiver knows which webhook it came through."""
    ctx = ctx or {}
    hook, trigger = str(params.get("webhook") or ""), str(ctx.get("trigger") or "")
    payload = ctx["payload"] if "payload" in ctx else ctx.get("data")
    return _send_out(conn, "forward", by, hook, f"custom.forward.{trigger}", subject,
                     payload if isinstance(payload, dict) else {"data": payload}, revision)


def _run_create_task(conn, rule_id, by, params, values, *, event, subject, revision, task=None, ctx=None):
    """A task for the bot `params.owner`, titled and told from the templates, filed in the rule maker's name. The body
    carries the call's content and the line that says how to read all of it, as a wake does."""
    ctx = ctx or {}
    slug = _slug(params.get("owner"))
    slug = (H.resolve_bot(conn, slug) or {}).get("id", slug)
    result = {"outcome": "create_task", "target": slug, "ok": False, "detail": ""}
    found = H.bot(conn, slug) if slug else None
    if not found:
        result["detail"] = f"No bot {slug}" if slug else "The rule names no bot"
        return result
    if found["state"] not in BOT_STATES_OK:
        result["detail"] = f"{found['slug']} is {found['state']}"
        return result
    title = " ".join(templating.render(str(params.get("title") or ""), values, cap=300).split()) \
        or _default_text(event, values)
    body = _wake_text({"text": params.get("body")}, values, ctx, _default_text(event, values))
    if event == "webhook.received":
        # A call from outside is screened for prompt injection first (backend/screening.py): long text waits for the
        # model after this write, short text gets the patterns' warning line now.
        from . import screening
        waits, body = screening.rule_task(conn, rule_id, by, slug, title, body, ctx)
        if waits:
            return {**result, "ok": True, "detail": waits}
    conn.execute("SAVEPOINT rule_task")
    try:
        options = {}
        if event in TTR.TRIGGERS and task:
            options = {"private": True if H.task_private(conn, task) else None,
                       "type": params.get("type"), "step": params.get("step")}
        row = H.task_create(conn, by or H.KEEPER, title[:300], body, H.bot_actor(slug), lint=False,
                            deduplicate=False, **options)
        if event in TTR.TRIGGERS and task:
            from . import task_relations as TR
            TR.relate(conn, H.KEEPER, row["id"], task["id"], mover=True,
                      note=f"Trigger: {event}. Action: create task. Rule {rule_id}")
        conn.execute("RELEASE rule_task")
    except Exception as exc:  # noqa: BLE001 - the rule's other rows still run
        conn.execute("ROLLBACK TO rule_task")
        conn.execute("RELEASE rule_task")
        result["detail"] = (str(getattr(exc, "message", "") or exc) or type(exc).__name__)[:300]
        return result
    return {**result, "ok": True, "detail": f"task {row['id']}"}


def _finance(action):
    """`finance_freeze_cards {budget_id?}`, `finance_pause_budget {budget_id?}`, `finance_act {kind, payload,
    dry_run?}`: a Finance action as the rule's maker (backend/finance/actions.watch), so its rights and gates apply.
    The payload's templates read the event; `budget_id` defaults to the event's budget."""
    def run(conn, rule_id, by, params, values, *, event, subject, revision, task=None, ctx=None):
        from .finance import actions as finance
        data = values.get("data") if isinstance(values.get("data"), dict) else {}
        params = {**params, "payload": templating.render_json(params.get("payload") or {}, values)}
        done = finance.watch(conn, rule_id, by or H.KEEPER, action, params, data,
                             f"rule-{rule_id}-{action}-{event}-{subject}"[:200])
        ok = done.status in ("committed", "needs_approval")
        detail = done.status if ok else f"{done.status}: {done.reason}"[:300]
        return {"outcome": action, "target": str(params.get("budget_id") or data.get("budget_id") or params.get("kind")
                                                 or ""), "ok": ok, "detail": detail}
    return run


# The event actions: `wake_bot {bot, text}`, `send_webhook {webhook, type, data}`, `create_task {owner, title, body}`,
# `forward {webhook}` and the three Finance ones. Each returns its result row.
EVENT_RUNNERS = {"wake_bot": _run_wake_bot, "send_webhook": _run_send_webhook, "create_task": _run_create_task,
                 "forward": _run_forward, "finance_freeze_cards": _finance("finance_freeze_cards"),
                 "finance_pause_budget": _finance("finance_pause_budget"), "finance_act": _finance("finance_act")}


def _event_target(row, params):
    """What a row acts on, so the first row for a target wins: the bot (scope bot, else params.bot) for ignore, note,
    wake and wake_bot; the endpoint for send_webhook; the rule's maker for ask. A bot of "" is every bot."""
    if row["then_outcome"] == ASK:
        return "ask", row["created_by"]
    if row["then_outcome"] == ACTION:
        action = params.get("action")
        if action in ("send_webhook", "forward"):
            return "webhook", str(params.get("webhook") or "")
        if action == "create_task":
            return "task", row["id"]
        if action == "wake_bot":
            return "bot", _slug(params.get("bot"))
        return "action", str(action)
    return "bot", row["scope_id"] if row["scope_kind"] == "bot" else _slug(params.get("bot"))


def _event_ask(conn, row, name, params, values, ctx):
    """One open Needs-you card for the rule's maker (a bot's maker is its operator), for as long as the last one is
    open. Returns (ok, detail)."""
    who = row["created_by"] if H.is_human(row["created_by"]) else None
    if not who and H.is_bot(row["created_by"]):
        from . import usage_limits
        person = usage_limits.recipients(conn, H.actor_id(row["created_by"]))
        who = H.human_actor(person["id"]) if person else None
    if not who:
        return False, "No person to ask"
    if conn.execute("SELECT 1 FROM events e JOIN tasks t ON t.id=e.target WHERE e.action=? "
                    "AND json_extract(e.detail,'$.rule')=? "
                    f"AND t.status IN ({','.join('?' * len(H.ACTIVE_STATUSES))}) LIMIT 1",
                    (ASKED, row["id"], *H.ACTIVE_STATUSES)).fetchone():
        return True, "already asked"
    text = templating.render(str(params.get("text") or ""), values, cap=1500).strip() or f"{name} happened."
    heading = " ".join(text.split())[:80]
    card = H.task_create(conn, H.KEEPER, f"A rule needs you: {heading}"[:300],
                         f"The rule {row['id']} asked for a person.\n\n{text}", who, lint=False, deduplicate=False)
    H.event(conn, H.KEEPER, ASKED, card["id"], {"rule": row["id"], "event": name})
    return True, "asked"


def _event_rows(conn, name):
    """The enabled rows that reach event `name`: its own, and prefix patterns that match it (`webhook.*`), never a
    bare `*`, at the scopes an event without a task has, in the order they fire."""
    found = conn.execute("SELECT * FROM rules WHERE enabled=1 AND scope_kind IN ('bot','department','team') "
                         "AND (event=? OR (event LIKE '%*' AND event<>'*'))", (name,)).fetchall()
    from . import redesign_migration
    found += [r for r in redesign_migration.wake_rows(conn) if r["scope_kind"] in ("bot", "department", "team")
              and (r["event"] == name or (r["event"].endswith("*") and r["event"] != "*"))]
    found = [r for r in found if CAT.matches(r["event"], name)]
    return sorted(found, key=lambda r: (EVENT_PRECEDENCE.index(r["scope_kind"]), r["position"], r["created_at"], r["id"]))


def judge_event(conn, event, ctx, *, actor="keeper"):
    """Do what the rules say for an event that is not a message to a bot (`webhook.received` and the other sourceless
    events in backend/event_catalog.py). Called inside the caller's write transaction.

    Every matching row fires, bot rows before department rows before team rows, then by position, at most one row per
    target: the first row for a bot (its scope, or `params.bot`) decides it, so a bot's `ignore` silences the team's
    wake for that bot; likewise one send per endpoint and one ask per maker. An `ignore` that names no bot ends the
    rows after it for every target. The same (event, ctx.trigger, ctx.delivery) twice does nothing the second time.

    Returns what was done, one `{rule_id, outcome, target, ok, detail}` per row that fired. Never raises: an error
    does nothing at all, is logged, and leaves a `rule.event_error` event."""
    ctx = ctx if isinstance(ctx, dict) else {}
    try:
        conn.execute("SAVEPOINT rule_event")
        try:
            done = _judge_event(conn, event, ctx, actor)
            conn.execute("RELEASE rule_event")
            return done
        except BaseException:
            conn.execute("ROLLBACK TO rule_event")
            conn.execute("RELEASE rule_event")
            raise
    except Exception as exc:  # noqa: BLE001 - nothing happens on an error: no fail-open for an outsider's event
        log.warning("Rules could not judge %s: %s", event, exc)
        try:
            H.event(conn, H.KEEPER, "rule.event_error", str(event)[:80], {
                "rule_id": EVENT_ERROR, "trigger": str(ctx.get("trigger") or "")[:80],
                "delivery": str(ctx.get("delivery") or "")[:80], "error": type(exc).__name__})
        except Exception:  # noqa: BLE001
            pass
        return []


def _judge_event(conn, event, ctx, actor):
    name = CAT.canonical(event) or str(event)
    if name not in SOURCELESS:
        return []
    rows = _event_rows(conn, name)
    if not rows:
        return []
    trigger, delivery = str(ctx.get("trigger") or ""), str(ctx.get("delivery") or ctx.get("subject") or "")
    if trigger or delivery:
        if not conn.execute("INSERT OR IGNORE INTO rule_events_seen(event,trigger,delivery,seen_at) VALUES(?,?,?,?)",
                            (name, trigger, delivery, H.now())).rowcount:
            return []
    values, seen, done, stopped = _event_values(name, ctx), set(), [], False
    for row in rows:
        if stopped or not _event_when(H._json(row["condition"], {}) or {}, ctx):
            continue
        params = H._json(row["params"], {}) or {}
        key = _event_target(row, params)
        if key[0] == "bot":
            key = ("bot", (H.resolve_bot(conn, key[1]) or {}).get("id", key[1]))
        if key in seen:
            continue
        seen.add(key)
        outcome = row["then_outcome"]
        if outcome == IGNORE:
            stopped = not key[1]                        # an ignore that names no bot ends the rows after it
            ok, detail = True, "ignored"
        elif outcome in (WAKE, NOTE):
            ok, detail = _tell_bot(conn, row["id"], key[1], _wake_text(params, values, ctx, _default_text(name, values)),
                                   _wake_refs(name, row["id"], ctx), wake=outcome == WAKE)
        elif outcome == ASK:
            ok, detail = _event_ask(conn, row, name, params, values, ctx)
        elif outcome == ACTION and params.get("action") in EVENT_RUNNERS:
            with TTR.from_action(row["id"]):
                found = EVENT_RUNNERS[params["action"]](
                    conn, row["id"], row["created_by"], params, values, event=name,
                    subject=delivery or name, revision=f"{row['id']}:{name}:{trigger}", ctx=ctx)   # one send per rule, event, trigger and call
            done.append({"rule_id": row["id"], "outcome": found["outcome"], "target": found["target"],
                         "ok": found["ok"], "detail": found["detail"]})
            continue
        else:
            ok, detail = False, "this rule needs a task"
        done.append({"rule_id": row["id"], "outcome": outcome, "target": key[1], "ok": ok, "detail": detail})
    for item in done:
        if item["outcome"] != IGNORE:
            H.event(conn, actor, "rule.event", item["rule_id"], {
                "event": name, "outcome": item["outcome"], "target": item["target"], "ok": item["ok"],
                "trigger": trigger, "delivery": delivery})
    return done


def prune_events(conn):
    """Forget the (event, trigger, delivery) triples older than EVENT_KEEP_DAYS (the hourly cleanup calls this)."""
    return conn.execute("DELETE FROM rule_events_seen WHERE seen_at<?",
                        (H.shift(H.now(), days=-EVENT_KEEP_DAYS),)).rowcount


# ----------------------------------------------------------------------------- step entry and the Dev tickets rows
# What a Dev ticket's hand-offs say, as editable type rows: (note, when, outcome). Seeded once per type (`seed_dev`);
# a person edits or deletes them in Settings, and a type that lost them is not given them again.
DEV_ROWS = (
    ("QA Fixes entered: wake the Builder who owns the ticket", {"step": "QA Fixes", "role": "owner"}, WAKE),
    ("Pending Release entered: a note to the Release Manager", {"step": "Pending Release", "role": "release_manager"}, NOTE),
    ("In QA entered: wake QA only", {"step": "In QA", "role": "qa"}, WAKE),
)
SEEDED = "rules.seeded"


def steps_renamed(conn, type_id, renamed):
    """A type's steps were renamed ({old name: new name}): its own rows that name a step by its old name name the new
    one, so a rule set on a step (QA wakes on In QA, ...) stays on that step. Quietly does nothing before the rules
    table exists."""
    lower = {str(k).lower(): v for k, v in renamed.items()}
    try:
        rows = conn.execute("SELECT id, condition FROM rules WHERE scope_kind='type' AND scope_id=?", (type_id,)).fetchall()
    except sqlite3.OperationalError:
        return
    from . import redesign_migration
    if H._has_table(conn, "automations"):
        redesign_migration.rename_steps(conn, type_id, lower)     # the rows moved into its What wakes automation
    for ident, raw in rows:
        when = H._json(raw, {}) or {}
        step = when.get("step") if isinstance(when, dict) else None
        if step is None:
            continue
        names = step if isinstance(step, list) else [step]
        moved = [lower.get(str(n).lower(), n) for n in names]
        if moved != names:
            when["step"] = moved if isinstance(step, list) else moved[0]
            conn.execute("UPDATE rules SET condition=?, updated_at=?, revision=revision+1 WHERE id=?",
                         (json.dumps(when, sort_keys=True), H.now(), ident))


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
                    "INSERT INTO rules(id,scope_kind,scope_id,event,condition,then_outcome,params,enabled,"
                    "position,note,created_by,created_at,updated_at,revision) VALUES(?,?,?,?,?,?,?,1,?,?,?,?,?,1)",
                    ("rule-" + H.new_id(), "type", ident, "step_entered", json.dumps(when, sort_keys=True), outcome,
                     "{}", position, note, H.KEEPER, at, at))
            H.event(conn, H.KEEPER, SEEDED, ident, {"rows": len(DEV_ROWS)})
            from . import redesign_migration
            redesign_migration.adopt(conn, "wake_rules", ("type", ident))   # after the cutover: its What wakes
    except sqlite3.OperationalError:
        return


STEP_NOISE_DROPPED = "rules.step_noise_dropped"


def drop_step_noise(conn):
    """Once per install: cancel the Release Manager's queued step-entry notices a profile catch-all held for its digest
    (before only a row naming `step_entered` could reach it, every task's step move did). Nothing else is touched: a
    note, another bot's jobs, a job already let go, a notice a team's own step_entered row batched. Quietly does
    nothing before the tables exist."""
    try:
        if conn.execute("SELECT 1 FROM events WHERE action=? LIMIT 1", (STEP_NOISE_DROPPED,)).fetchone():
            return 0
        flow = _release_flow()
        manager = flow.load(conn)["bot_id"] or flow.release_manager(conn)   # a paused one's jobs too
        dropped = conn.execute(
            "UPDATE jobs SET state='canceled',held=0 WHERE bot_id=? AND state='queued' AND held=1 AND message_id IN "
            "(SELECT id FROM messages WHERE json_extract(refs,'$.news')='step_entered' "
            "AND json_extract(refs,'$.wake_batch')='rule' AND json_extract(refs,'$.rule')='profile_lead_digest')",
            (manager,)).rowcount if manager else 0
        H.event(conn, H.KEEPER, STEP_NOISE_DROPPED, manager or "", {"jobs": dropped})
        return dropped
    except sqlite3.OperationalError:
        return 0


def step_entered(conn, actor, row, *, skip=()):
    """A task entered a step: the bots that have a rule for it hear it (a note or a wake, as the rule says); everyone
    else hears nothing. Incident tasks also notify their owner and requester without a configured step rule.
    Ordinary step moves need a row naming `step_entered`. The owner, the requester, the ticket's QA and the Release Manager are the ones a row can reach.
    Callers can skip recipients already notified by the event that moved the task."""
    try:
        incident = incident_task(conn, row)
        from . import redesign_migration
        # A row naming a step entry, kept or moved into a "What wakes" automation (a Dev tickets type's are adopted).
        if not incident and not conn.execute("SELECT 1 FROM rules WHERE enabled=1 AND event='step_entered' LIMIT 1").fetchone() \
                and not any(r["event"] == "step_entered" for r in redesign_migration.wake_rows(conn, "step_entered")):
            return
        step = conn.execute("SELECT name FROM task_steps WHERE id=?", (row.get("step_id"),)).fetchone()
        flow = _release_flow()
        qa, manager = flow.qa_owner(conn, row), flow.release_manager(conn)
        who = [row.get("owner_id"), row.get("requester_id"), H.bot_actor(qa) if qa else None,
               H.bot_actor(manager) if manager else None]
        conv = H.conversation(conn, row.get("conversation_id"))
        for target in dict.fromkeys(a for a in who if a and H.is_bot(a) and a != actor and a not in skip):
            refs = {"task": row["id"], "news": "step_entered"}
            if (incident and target in (row.get("owner_id"), row.get("requester_id"))) or \
                    _table_decision(conn, "step_entered", actor, target, refs, conv, row):
                H._wake(conn, row, target, f"{row['title']} entered {step[0] if step else 'a new step'}.",
                        {"news": "step_entered", **({"at_step": row["step_id"]} if row.get("step_id") else {})})
    except Exception:  # noqa: BLE001 - a step move never fails for the news about it
        return


# ----------------------------------------------------------------------------- reading
def view(row, conn=None):
    """A table row as the API shows it."""
    return {"id": row["id"], "scope_kind": row["scope_kind"], "scope_id": row["scope_id"], "event": row["event"],
            "when": H._json(row["condition"], {}) or {}, "then": row["then_outcome"],
            "params": H._json(row["params"], {}) or {}, "enabled": bool(row["enabled"]),
            "position": row["position"], "note": row["note"], "built_in": False,
            "protected": row["id"] in tico_notices.RULE_IDS,       # Tico Team's notice rules: editable, never deleted
            "created_by": row["created_by"], "action_by": row["action_by"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
            "revision": row["revision"],
            **(TTR.state(conn, row["id"]) if conn is not None and row["event"] in TTR.TRIGGERS else {})}


def install(app, store, auth):
    """The event catalog. Wake rules are edited as "What wakes" automations (backend/automations.py); this module only
    decides by them."""
    from fastapi import Request

    @app.get("/api/v2/event-types")
    def event_types(request: Request, group: str | None = None):
        """Every event Tico names, with a plain description and an example payload (`data`): what automations and
        outgoing webhooks read. Nothing secret."""
        auth.domain(request.state.identity)
        return {"items": CAT.listing(group)}
