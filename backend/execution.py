"""Durable delivery, runner enrollment, and transactionally fenced execution attempts."""

import functools
import json
import re
import secrets
import sqlite3
import threading
from contextlib import contextmanager
import time
from datetime import timezone

from clients import gateway, sessions

from . import auto_tasks, start_places, org_turns, app_delivery, check_models, learned, rooms, rules as wake_rule_engine, updates_chat, gateway_connection, inbox_isolation, login_holds, models_preset, providers, quiet_work, routines, run_triggers, runner_versions, screen, task_models, usage_limits, wake_presets, wake_rules
from . import task_privacy as privacy
from . import browser_exit, claim_holds, cloud_access, cloud_machines, model_subscriptions, quarantine_stops, reconnects, run_backoff, turn_budgets
from .auth import Identity
from .batch_work import isolated, report, shared_check, shared_scope, storage_error
from .harnesses import bot_keyed, reports_tool_calls
from .statuses import PARKED_SQL
from .verdict_moves import HANDOFF_FIRST_SQL
from .store import H, P, Problem, bot_readiness, digest, encode, message_page, readiness_document
from .config import Settings

# The wait after a usage limit whose provider gave no reset time; a reported reset is used instead.
LIMIT_COOLDOWN = 1800        # seconds a bot waits after its runtime reported a usage limit
LIMIT_COOLDOWN_LONG = 7200   # after the third limit in a row: the subscription window is spent, not busy
LIMIT_RESET_MAX = 8 * 86400  # a reported reset further off than a weekly window is not believed
LIMIT_STRIKES = 3
# An API key's own quota or billing refusal: the fix is the key's plan and billing, not waiting out a subscription window.
BILLING_LIMIT_RE = re.compile(r"quota exceeded\. check your plan|exceeded your current quota|insufficient[_ ]quota", re.I)
# How long a server that stopped answering runners is forgiven: leases that lapse meanwhile
# get grace instead of expiring, and a runner reporting in afterwards gets its attempt back.
# A backup can starve a single small API worker for minutes: 90 s leases lapsed and healthy running turns were marked interrupted for review.
STALL_MAX = 900
# How long the sweep waits for a run whose lease lapsed (its runner never said it stopped) before running it again. On
# Oct 17-18 eight finished turns reached the hub 15 to 76 minutes after their lease lapsed, while their runner was
# stalled between the model's last word and the hand-in; one sweep resumed at 15 minutes and the request ran twice.
# 90 minutes covers the slowest seen with room, and outlasts a whole turn (max_run_minutes defaults to 60).
RESULT_GRACE = 90 * 60
# After this long with no decision a held run is a person's: Health and its operator's Needs you name it (Dev 543).
HELD_UNREVIEWED = 2 * 3600
# A failed turn whose only events are these produced nothing and touched nothing.
SILENT_KINDS = {"error", "diagnostic", "status"}
NO_EFFECT_TRIES = 3
PRE_START_TRIES = 10        # lapsed leases before start: a broken computer must not retry forever
# A run that found the Tico AI Gateway unavailable goes back in the queue (at most NO_EFFECT_TRIES - 1 times), and the
# bot first waits this long, doubled for each try: an outage is not asked again every few seconds.
GATEWAY_BACKOFF = 60
NO_EFFECT_WITHIN = 120       # seconds from start; a longer silent run may have used unreported tools
CHECKPOINT_CHARS = 1500      # a task's checkpoint: the last turn's reply, enough to pick the work back up
# A deploy drains every bot while it waits for runs to finish (at most 30 minutes) and then
# activates. A drain it left behind for longer than this is a leak, not a deploy in progress.
DEPLOY_DRAIN_MAX = 3600
# A machine that wakes for a few seconds — a macOS dark wake, a laptop on battery checking
# mail — reports in once and is gone before it can finish anything. A silence longer than
# AWAKE_GAP ends a waking period; AWAKE_SETTLE of unbroken contact starts a new one. Work
# waits in the queue meanwhile, which is what an owner expects a sleeping machine to do.
AWAKE_GAP = 60
AWAKE_SETTLE = 120
# A claim arrives four times a second; contact is recorded at most this often so the rule costs
# one write a minute on an idle machine rather than four a second.
CONTACT_EVERY = 10
CLAIM_MAINTENANCE = 8       # a polling request never cleans up the whole install's backlog
CLAIM_PICKS = 5            # candidates one claim may drop under its lock (no longer eligible) before it answers none
CLAIM_HOLDS = 3            # jobs one claim may hold, for a check or a start that raised, before it picks no more


class ClaimFaults:
    """What one claim could not check or start, kept for the whole claim. `jobs`: (job id, error name) pairs to hold.
    `bots`: the bots whose bot-wide check raised, with the error name; the claim passes each over for the rest of its
    call, so its error is logged once and none of its runs is held. `holds`: the jobs the claim has held, which
    CLAIM_HOLDS bounds, so one wide failure cannot hold a whole backlog in one claim. `clean`: the bots whose check
    the backfill read without error, so a back-off left from an earlier failure ends."""

    def __init__(self):
        self.jobs, self.bots, self.holds = [], {}, 0
        self.new_bots = []          # (bot, exception) whose failure Health has not recorded yet
        self.clean = set()

    def __bool__(self):
        return bool(self.jobs or self.new_bots)

    def full(self):
        return self.holds >= CLAIM_HOLDS

    def job(self, job_id, error):
        self.jobs.append((job_id, error))
        self.holds += 1

    def bot(self, bot, exc):
        if bot not in self.bots:
            self.bots[bot] = type(exc).__name__
            self.new_bots.append((bot, exc))


# A runner that advertises this runs a bot's task-keyed turns in their own lanes, side by side;
# for any other runner every turn is in the main lane, one at a time per bot. `task_lanes_v1` was
# advertised by unreleased main-built runners whose session key treats an unset `session` as the
# bot thread, so the task-session default would give their lanes one shared provider thread: it
# is still accepted but means no lanes, and those runners run serially.
LANES = "task_lanes_v2"

# A runner that advertises this keys a task turn's session and worktree on the claimed task's
# `session_task_id` (H.session_root: a subtask continues its parent's session). Any other runner keys
# on the task itself, so for it the server keeps every task, subtasks included, its own lane and session.
ROOTS = "session_roots_v1"

# A runner that advertises this runs a task's goal as the harness's native goal (chat_goals.task_goal_row):
# it sets it in the task's session beside the turn's prompt. Any other runner is handed no task-room goal.
TASK_GOALS = "task_goals_v1"

# A runner that advertises this continues the thread a claim names in `continue_thread` (capacity_continuation). Any
# other runner ignores the field and would start the job on a fresh thread, so it is never handed such a job.
CAPACITY_CONTINUE = "capacity_continue_v1"

# A runner that advertises this runs a private live meeting's turns in that meeting's own checkout, read-only on GitHub
# (runner-rs/src/sessions.rs, private lanes). Any other runner would run them in the bot's checkout and push what they
# wrote, so it is never handed one: the turn waits for an updated runner (live_meetings `meeting.bot_turn_waiting`).
PRIVATE_LANES = "private_lanes_v1"
SOURCE_CONTINUE = "source_continue_v1"


def positive_effect(c, aid, body):
    """Evidence the turn did real work even when its event stream looks empty (sparse host telemetry).

    Stream-kind silence alone used to requeue such a run as `attempt.no_effect_retry` despite recorded hub writes,
    usage, partial text, pushes or consumed approvals. Those mean a blind retry is unsafe; hold for review instead.
    """
    if (getattr(body, "text", None) or "").strip():
        return True
    if getattr(body, "pushes", None):
        return True
    # Output only: input tokens prove the prompt was sent, which a provider bills before erroring too.
    if getattr(body, "tokens_out", None):
        return True
    usage = getattr(body, "usage", None)
    if usage is not None:
        parts = [usage, *list(getattr(usage, "segments", None) or [])]
        if any(getattr(part, "output_tokens", 0) for part in parts):
            return True
    if c.execute("SELECT 1 FROM turns WHERE id=? AND coalesce(writes,0)>0", (aid,)).fetchone():
        return True
    row = c.execute("SELECT bot,coalesce(started,created) began FROM attempts WHERE id=?", (aid,)).fetchone()
    if row and c.execute(
            "SELECT 1 FROM approvals WHERE requested_by=? AND consumed_at IS NOT NULL AND consumed_at>=? LIMIT 1",
            ("bot:" + row["bot"], row["began"])).fetchone():
        return True
    return False


def only_a_notice(c, aid, row, body):
    """Whether a stopped turn did nothing but report why it stopped, so nothing of it needs its session continued.
    Claude Code writes a limit or a refused request as an assistant message of its own, the same line as the turn's
    error, so "a message came back" is no evidence of work. Missing token counts are never proof here (a host may not
    report them). The proof is what the host promised and streamed: it reports every tool call and none came, no text
    streamed, each message is word for word a line the turn ended on, and no write, push, output or used approval shows.
    An older runner or a harness that makes no such promise never passes."""
    stopped_on = {error.strip() for error in limit_errors(c, aid) if error.strip()}
    for event in c.execute("SELECT kind,payload_json FROM attempt_events WHERE attempt_id=? ORDER BY seq", (aid,)):
        payload = json.loads(event["payload_json"] or "{}") or {}
        if event["kind"] == "tokens":
            usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
            if payload.get("output") or usage.get("output"):
                return False
        elif event["kind"] == "message":
            if str(payload.get("text") or "").strip() not in stopped_on:
                return False
        elif event["kind"] not in SILENT_KINDS:
            return False
    text = split_runner_notices(body.text or "")[0].strip()
    if (text and text not in stopped_on) or body.yielded or positive_effect(c, aid, body.model_copy(update={"text": ""})):
        return False
    job = c.execute("SELECT * FROM jobs WHERE id=?", (row["job_id"],)).fetchone()
    facts = retry_facts(c, job, row["started"], H.now())
    return bool(facts["reported"] and not facts["calls"] and not facts["consumed"] and not facts["fallback_unknown"])


def turns_here(c, runner_id):
    """{bot: turns it holds on this computer now}."""
    return start_places.held(c, runner_id)


# The busy lists each runner sent with its latest claim: {runner id: (bots, lanes, monotonic time)}. Only the runner
# knows a lapsed lease whose worker still runs, or a bot whose worktrees it is maintaining: Health reads them here
# (bot_refusal). In memory: after a server restart they are empty until each runner's next claim, a second later.
CLAIMED_BUSY = {}
CLAIMED_FRESH = 120         # seconds a runner's busy lists are believed; a runner claims several times a second
# An idle runner whose event stream is up claims again only on a work event or after its backup poll (BACKUP_POLL_S,
# 300 s, in runner-rs/src/events.rs), so the company's stop-level waiter order believes its lists that long and a
# minute more: a computer still online and waiting is not mistaken for one that stopped claiming.
CLAIMED_WAITING_FRESH = 360


def note_claim(runner_id, body):
    """Keep the busy lists of every claim, before any early answer: Health and the company's stop-level waiter
    order read them, and a claim answered early (at the stop level, say) still says what the runner is running."""
    CLAIMED_BUSY[runner_id] = (frozenset(body.busy_bots or ()), frozenset(body.busy_lanes or ()), time.monotonic())


def claiming(runner_id, fresh=CLAIMED_FRESH):
    """Whether the runner has claimed lately, so its busy lists are known. One that has stopped claiming (its own
    places are full, perhaps with a worker whose lease lapsed) takes nothing, however free the server thinks it is."""
    seen = CLAIMED_BUSY.get(runner_id)
    return bool(seen) and time.monotonic() - seen[2] <= fresh


def claimed_busy(runner_id, fresh=CLAIMED_FRESH):
    """(bots, {(bot, lane)}) the runner said were busy on its latest claim; empty when it has not claimed lately."""
    seen = CLAIMED_BUSY.get(runner_id)
    if not seen or time.monotonic() - seen[2] > fresh:
        return frozenset(), frozenset()
    return seen[0], frozenset(tuple(entry.split("|", 1)) for entry in seen[1] if "|" in entry)


@functools.lru_cache(maxsize=256)
def _capability_set(capabilities_json):
    """Parse a runner's capabilities_json once; claim/Health ask several flags per computer."""
    return frozenset(H._json(capabilities_json, []) or [])


def lanes_on(runner):
    return bool(runner) and LANES in _capability_set(runner["capabilities_json"])


def private_lanes_on(runner):
    return bool(runner) and PRIVATE_LANES in _capability_set(runner["capabilities_json"])


def private_job(c, job):
    """Whether a queued job is a private live meeting's turn (private_lane): only a private_lanes_on runner takes it."""
    message = H.message(c, job["message_id"])
    return bool(message and private_lane(c, H.conversation(c, message["conversation_id"])))


def roots_on(runner):
    return bool(runner) and ROOTS in _capability_set(runner["capabilities_json"])


def task_goals_on(runner):
    return bool(runner) and TASK_GOALS in _capability_set(runner["capabilities_json"])


def capacity_continue_on(runner):
    return bool(runner) and CAPACITY_CONTINUE in _capability_set(runner["capabilities_json"])


def session_task_id(c, bot, config, task_id, roots):
    """The task a turn on `task_id` keys its session and lane on: its session root for a runner that reads
    `session_task_id`, the task itself for any other."""
    return H.session_root(c, bot, task_id, config) if roots and task_id else task_id


def reply_target(message, conversation, task):
    """Whom a turn's reply goes to: the message's sender, or for the keeper's wake on a task, the personal room's
    owner, else its first person, else the task's requester. The completion posts to it; the org rule check
    (backend/org_rule_check.py) checks the reply against it before the completion's write."""
    target = message["from_actor"]
    if target == H.KEEPER and task:
        humans = [p for p in conversation.get("participants") or [] if str(p).startswith("human:")]
        target = ((conversation.get("owner_actor") if conversation.get("scope") == "personal" else None)
                  or (humans[0] if humans else None) or task["requester"])
    return target


def work_task_id(message, conversation=None):
    """The work attached to a turn; parked urgent notices retain their task refs only as news.

    Privacy, operator scope and interrupted-work SQL continue to use the message's actual task.
    """
    return None if wake_rule_engine.notice_only(message) else H.message_task_id(message, conversation)


def job_lane(c, bot, config, message, conversation, lanes, roots=False):
    """The lane a queued message would run in (clients/sessions.py), `main` for a runner without lanes.
    `config` is the bot's followed config, the one its runner keys sessions on. A subtask's turn runs in
    its session root's lane, so it and its parent's turns take turns in one session."""
    if not lanes:
        return sessions.MAIN_LANE
    if private := private_lane(c, conversation):
        return private
    return sessions.lane(config, bot, session_task_id(c, bot, config, work_task_id(message, conversation), roots),
                         conversation["id"] if conversation else None)


def private_lane(c, conversation):
    """A private live meeting's room runs in its own lane (clients/sessions.py PRIVATE_LANE), else None."""
    from .live_privacy import private_room
    if conversation and private_room(c, conversation.get("room_key")):
        return sessions.private_lane(conversation["id"])
    return None


def bump_bot_session_epoch(c, bot):
    """Every turn of `bot` carries this as `bot_session_epoch`; a runner drops the bot's shared thread when it moves."""
    c.execute("INSERT INTO bot_session_epochs VALUES(?,1,?) ON CONFLICT(bot) DO UPDATE SET "
              "epoch=epoch+1,updated=excluded.updated", (bot, H.now()))


def model_changed_since(c, bot, since):
    """Whether a person moved the bot to another model in Settings after `since`, a turn's lease. Only for a turn
    leased before its claim recorded the model it handed out (ran_another_model)."""
    return c.execute("SELECT 1 FROM bot_transitions WHERE bot=? AND kind='model' AND state='applied' AND applied_at>? "
                     "LIMIT 1", (bot, since)).fetchone() is not None


def ran_another_model(c, settings, attempt):
    """Whether the bot is now set to another runtime or model than the one its claim handed this turn. The turn runs
    on what it was handed, so what it meets (a usage limit, a provider thread) is that model's, not the bot's now
    (Dev 334: a rate-limited bot can be moved off the spent model while a turn is out). An effort change, or a move
    away and back, leaves the turn on the bot's own model, and its limit holds (Dev 353). A turn on a task model or
    a check model ran on that one, so the bot's own model moving says nothing about it."""
    if task_models.off_bot_model(c, attempt["id"]):
        return False
    from .shared_bots import follow
    row = c.execute("SELECT config_json FROM bot_config WHERE bot=?", (attempt["bot"],)).fetchone()
    now = providers.bot_choice(c, settings, follow(c, attempt["bot"], json.loads(row[0]) if row and row[0] else {}))
    handed = c.execute("SELECT handed_runtime,handed_model FROM attempts WHERE id=?", (attempt["id"],)).fetchone()
    if not handed or handed["handed_runtime"] is None:
        return model_changed_since(c, attempt["bot"], attempt["created"])
    return (handed["handed_runtime"], handed["handed_model"]) != tuple(now)


def model_choice(c, settings, bot):
    """The (runtime, model) the bot's own turns are handed now."""
    row = c.execute("SELECT config_json FROM bot_config WHERE bot=?", (bot,)).fetchone()
    return tuple(providers.bot_choice(c, settings, json.loads(row[0]) if row and row[0] else {}))


def model_change_hold(c, settings, bot):
    """Why the model a limited bot was just moved to waits too, or "": its login on the bot's computer is held
    (backend/login_holds.py), or the bot's last limit was the Tico AI Gateway's and the new model is a Tico model on
    the same company key. Only then does the bot stay Rate limited after the move (Dev 353)."""
    hold = own_login_hold(c, settings, bot)
    if (hold.get("until") or "") > H.now():
        return "login"
    model = model_choice(c, settings, bot)[1]
    last = last_limit(c, bot)
    if last and gateway_connection.is_tico(model) and any(
            gateway.cap_reached(e) or gateway.out_of_credit(e) or gateway.run_code(e) == gateway.RATE_LIMITED
            for e in limit_errors(c, last["id"])):
        return "gateway"
    return ""


def lift_after_model_change(c, settings, actor, bot, was):
    """A person moved the bot from `was` (model_choice before the change) to another model: a usage limit that was the
    old model's lifts, and the new model starts on the queued work now. A turn still out on the old model keeps the bot
    at work (settle_status). When the new model waits on the same spent login or Gateway key (model_change_hold), the
    bot stays Rate limited and says so. An effort-only change leaves the bot on the spent model: nothing lifts."""
    status = H.status(c, bot) or {}
    if status.get("state") != "limited" or model_choice(c, settings, bot) == tuple(was):
        return
    why = model_change_hold(c, settings, bot)
    if why:
        shared = "the same login" if why == "login" else "the same Tico AI Gateway key"
        H.status_set(c, actor, bot, state="limited",
                     focus=f"Model changed; the new model uses {shared}, which is still at its usage limit",
                     reason="Model changed")
        return
    out = c.execute("SELECT 1 FROM attempts WHERE bot=? AND state IN ('leased','running') LIMIT 1", (bot,)).fetchone()
    H.status_set(c, actor, bot, state="running" if out else "idle",
                 focus="Working on other tasks" if out else "Model changed; queued work resumes",
                 reason="Model changed")


def lift_after_connection_change(c, settings, actor, bot):
    """The bot's connection changed (it moved to another computer, so another login; a computer's login was switched
    to another account): a usage limit it showed was the previous login's. Only a hold on the login it runs on now
    counts (backend/login_holds.py): with none the bot is free and its queued work starts now; with one it stays
    Rate limited and names that hold's end. A turn still out keeps the bot at work (settle_status)."""
    if (H.status(c, bot) or {}).get("state") != "limited":
        return
    why = model_change_hold(c, settings, bot)
    if why == "login" and fallback_takes(c, settings, bot):
        why = ""        # the named or default fallback runs the turn now (fallback_choice), so the held login does not
    if why:
        until = own_login_hold(c, settings, bot).get("until") or ""
        if why == "login" and until:
            # The bot stays held on its own login until that login's hold ends (a replaced plan's later deadline is no
            # longer the one): the claim releases these at this time (wake_presets.release_quota_resets).
            c.execute("UPDATE jobs SET hold_until=? WHERE bot=? AND state='queued' AND held=1 "
                      "AND wake_class='quota-reset' AND hold_reason IS NULL", (until, bot))
        H.status_set(c, actor, bot, state="limited",
                     focus=("Connection changed; the new login is at its usage limit"
                            + (f" until {until[11:16]} UTC" if why == "login" and until else "")
                            if why == "login" else
                            "Connection changed; the Tico AI Gateway key is still at its usage limit"),
                     reason="Connection changed")
        return
    c.execute("UPDATE jobs SET held=0,hold_until=NULL WHERE bot=? AND state='queued' AND wake_class='quota-reset' "
              "AND hold_reason IS NULL", (bot,))
    out = c.execute("SELECT 1 FROM attempts WHERE bot=? AND state IN ('leased','running') LIMIT 1", (bot,)).fetchone()
    H.status_set(c, actor, bot, state="running" if out else "idle",
                 focus="Working on other tasks" if out else "Connection changed; queued work resumes",
                 reason="Connection changed")


def fallback_takes(c, settings, bot):
    """Whether the bot's named or default fallback takes its turn on its computer now: the same answer the claim gives
    (fallback_choice), on the config a claim runs (shared_bots.follow)."""
    from .shared_bots import follow
    place = c.execute("SELECT runner_id FROM assignments WHERE bot=?", (bot,)).fetchone()
    if not place or not place["runner_id"]:
        return False
    row = c.execute("SELECT config_json FROM bot_config WHERE bot=?", (bot,)).fetchone()
    config = follow(c, bot, json.loads(row[0]) if row and row[0] else {})
    return fallback_choice(c, settings, place["runner_id"], bot, config) is not None


def lift_limited_on_computer(c, settings, actor, runner_id, runtime):
    """Re-judge each Rate limited bot on this computer that runs `runtime`, as its primary or its named fallback
    (lift_after_connection_change). Its config is the one a claim runs (shared_bots.follow). Call it after the
    computer's sign-in for `runtime` has changed in the database: the re-judge reads the login the bot would use now."""
    from .shared_bots import follow
    record = providers.load(c, settings)
    for row in c.execute("SELECT s.bot FROM bot_status s JOIN assignments a ON a.bot=s.bot "
                         "WHERE s.state='limited' AND a.runner_id=? ORDER BY s.bot", (runner_id,)).fetchall():
        config = c.execute("SELECT config_json FROM bot_config WHERE bot=?", (row[0],)).fetchone()
        filled = providers.fill(record, follow(c, row[0], json.loads(config[0]) if config and config[0] else {}))
        if runtime == filled.get("runtime") or runtime in model_subscriptions.turn_runtimes(filled):
            lift_after_connection_change(c, settings, actor, row[0])


def lift_after_login_switch(c, settings, actor, runner_id, runtime):
    """A computer's login for `runtime` was switched to another account or key: the hold on it was the old account's.
    It ends as Try now does (login_holds.try_now: one probe turn tests the new account), and every bot that was
    Rate limited on it is freed."""
    for key in {k for k in (login_holds.canonical(c, runner_id, "", runtime), "") if k is not None}:
        login_holds.try_now(c, runner_id, key, runtime)
    lift_limited_on_computer(c, settings, actor, runner_id, runtime)


# The room messages a claim hands its turn (the runner forwards only what arrived since the session last answered).
HISTORY_PAGE = 50


def lane_history(c, bot, config, history, conversation, lane, roots=False):
    """The part of a room's page a turn in `lane` reads: the messages whose own lane (job_lane) is that
    lane, so the main session never reads task traffic and a task's session never reads the main chat or
    another task. The main chat's quiet task lines (`refs.task_started`, `refs.task_report`) are the main
    session's: how it hears that a task started and where it got to."""
    if private := private_lane(c, conversation):
        return list(history) if lane == private else []
    memo = {}

    def lane_of(message):
        refs = message.get("refs") or {}
        if refs.get("task_report") or refs.get("task_started"):
            return sessions.MAIN_LANE
        task_id = work_task_id(message, conversation)
        if task_id not in memo:
            memo[task_id] = sessions.lane(config, bot, session_task_id(c, bot, config, task_id, roots),
                                          conversation["id"] if conversation else None)
        return memo[task_id]
    return [message for message in history if lane_of(message) == lane]


def task_goal(c, bot, config, task):
    """The goal a bot's task turn carries, or None. `task_goal: on` in the bot's config gives every task it
    owns one; a `goal:on` or `goal:off` label on the task overrides it. A person's task never has one."""
    if not task or task.get("owner") != "bot:" + bot:
        return None
    labels = set(H.task_labels(task))
    if not ("goal:on" in labels or (config or {}).get("task_goal") == "on" and "goal:off" not in labels):
        return None
    criteria = [str(item).strip() for item in (H._json(task.get("acceptance_json"), []) or []) if str(item).strip()]
    objective = task["title"] + ("\nAcceptance criteria:\n" + "\n".join("- " + item for item in criteria) if criteria else "")
    return {"objective": objective[:4000]}


def current_task_goal(c, bot, task):
    """task_goal with the bot's config as its turns read it (shared_bots.follow)."""
    from .shared_bots import follow
    found = c.execute("SELECT config_json FROM bot_config WHERE bot=?", (bot,)).fetchone()
    return task_goal(c, bot, follow(c, bot, json.loads(found[0] or "{}")) if found else {}, task)


def stopper(c, person):
    """The name of the person who pressed Stop, as the "Stopped by" line shows it."""
    return (H.human(c, H.actor_id(person)) or {}).get("name") or H.actor_id(person)


def settle_status(c, bot, state, focus, claimed=None, task_id=None, stop=None):
    """The status a finished or lapsed turn leaves its bot in. While another of its turns is still out,
    the bot is still at work, so an idle or crashed turn does not mark the whole bot idle or crashed;
    the held run keeps its own item in Health. A usage limit is the subscription's, so one lane's limit
    holds for the whole bot until its cooldown passes, even when a previously claimed turn starts or
    restores its lease afterwards. A turn claimed after it (a person's message, Try now) that settles
    shows the limit has lifted. `claimed` is when the turn was leased, None when unknown. `stop` is the run that
    ended without finishing, which the status line then explains (`last_stop`); any other settled status forgets it."""
    if state in ("idle", "crashed", "running"):
        status = H.status(c, bot) or {}
        if (status.get("state") == "limited" and status.get("since")
                and limit_until(c, bot, status["since"]) > H.now()
                and (state == "running" or claimed is None or claimed < status["since"])):
            return
    if state in ("idle", "crashed"):
        if c.execute("SELECT 1 FROM attempts WHERE bot=? AND state IN ('leased','running') LIMIT 1",
                     (bot,)).fetchone():
            # No count and no task: the other turns may be on private tasks this status's readers cannot see.
            H.status_set(c, H.KEEPER, bot, state="running", focus="Working on other tasks", task_id="")
            return
    if stop:
        from .repositories import save_metadata
        save_metadata(c, LAST_STOP + bot, {"attempt_id": stop, "at": H.now()})
    else:
        c.execute("DELETE FROM registry_metadata WHERE key=?", (LAST_STOP + bot,))
    H.status_set(c, H.KEEPER, bot, state=state, focus=focus,
                 **({"task_id": task_id} if task_id is not None else {}))


# The run a bot's status line explains (last_stop) until the bot's next run settles.
LAST_STOP = "last-stop:"
# What last_stop reads of that run, its job and a crashed bot's held run; events.prime_status_lines reads them for
# many bots at once.
STOP_ATTEMPT_SQL = "SELECT id,job_id,state,started,finished,final_text,stop_requested FROM attempts WHERE id=?"
STOP_JOB_SQL = "SELECT state FROM jobs WHERE id=?"
# Of jobs held at the same instant the last written wins (rowid), so the batched read picks the same one.
HELD_SQL = "SELECT id,attempt_id FROM jobs WHERE bot=? AND state='uncertain' ORDER BY created DESC,rowid DESC LIMIT 1"
HELD_RUN_SQL = ("SELECT id,attempt_id FROM jobs WHERE bot=? AND state='uncertain' AND attempt_id=? "
                "ORDER BY created DESC,rowid DESC LIMIT 1")
HELD_ATTEMPT_SQL = "SELECT state,started,finished FROM attempts WHERE id=?"
NOT_STARTED = "Last run didn't start in time. Nothing was lost; nothing is needed."


def last_stop(c, bot, status):
    """Why a bot's last run ended without finishing, for its page header: one plain line, whether a person must act
    (`needs_person`, shown red) or not (`soft`, shown grey), the one action that exists (`review`: retry or dismiss a
    run that stopped partway) and the reason in plain words. None when its last run finished, or when the run that
    did not start is no longer waiting (its work was covered elsewhere, or it was withdrawn): nothing to say."""
    state = (status or {}).get("state")
    if state not in ("idle", "crashed"):
        return None
    from .repositories import metadata
    from .read_cache import one
    mark = metadata(c, LAST_STOP + bot)
    attempt = one(c, STOP_ATTEMPT_SQL, (mark["attempt_id"],)) if mark.get("attempt_id") else None
    at = (attempt["finished"] if attempt else None) or status.get("since")
    # An older held task must not replace the run that produced the current status.
    held = ((one(c, HELD_RUN_SQL, (bot, mark["attempt_id"])) if mark.get("attempt_id") else one(c, HELD_SQL, (bot,)))
            if state == "crashed" else None)
    if held:
        run = one(c, HELD_ATTEMPT_SQL, (held["attempt_id"],)) if held["attempt_id"] else None
        unstarted = run is not None and not run["started"]
        focus = (status.get("focus") or "").rstrip(".")
        reason = ("Its computer refused the run before it started." if unstarted else
                  "Its computer stopped answering while the run was going, so the run's lease ran out."
                  if run and run["state"] == "expired" else
                  "The run ended with an error before it finished." if run and run["state"] == "failed" else
                  "The run was interrupted before it finished.")
        if focus:
            reason += " " + focus + "."
        reason += (" Nothing ran. Review the saved run after fixing the cause." if unstarted else
                   " Tico kept it rather than run it again, in case it had already changed something.")
        return {"kind": "never_started" if unstarted else "stopped", "tone": "alert", "needs_person": True,
                "action": "review", "at": (run["finished"] if run else None) or at,
                "attempt_id": held["attempt_id"], "reason": reason,
                "text": ("Last run never started. Check the reason, then retry it or dismiss it." if unstarted else
                         "Last run stopped partway. Retry it or dismiss it.")}
    if attempt and not attempt["started"] and attempt["state"] == "failed":
        # PRE_START_TRIES lapses: the reason names the computer and what to do about it (expire()).
        return {"kind": "never_started", "tone": "alert", "needs_person": True, "action": None, "at": at,
                "attempt_id": attempt["id"], "reason": f"Its computer took this run {PRE_START_TRIES} times and never "
                "started it.", "text": (attempt["final_text"] or "Last run couldn't start on its computer").rstrip(".") + "."}
    if attempt and not attempt["started"] and attempt["state"] == "expired":
        job = one(c, STOP_JOB_SQL, (attempt["job_id"],))
        if not job or job["state"] != "queued":        # taken again, or no longer waiting: nothing to explain
            return None
        returned = (attempt["final_text"] or "").startswith(HANDED_BACK)
        if attempt["stop_requested"]:
            return {"kind": "not_started", "tone": "soft", "needs_person": False, "action": None, "at": at,
                    "attempt_id": attempt["id"], "text": "Last run was stopped before it started. Nothing ran.",
                    "reason": "It was stopped before its computer started it, so nothing ran."}
        return {"kind": "not_started", "tone": "soft", "needs_person": False, "action": None, "at": at,
                "attempt_id": attempt["id"], "text": NOT_STARTED,
                "reason": (f"{attempt['final_text']} " if returned else
                           "Its computer took the run but didn't start it before its lease ran out. ")
                + "It is back in the queue and runs again shortly."}
    if state == "crashed":
        focus = (status.get("focus") or "").rstrip(".")
        return {"kind": "stopped", "tone": "alert", "needs_person": True, "action": None, "at": at,
                "attempt_id": attempt["id"] if attempt else None, "reason": (focus + ".") if focus else "",
                "text": "Last run stopped" + (f": {focus}." if focus else ".")
                + " Ask a person who manages this bot to check its Computer before sending a new message."}
    return None


HANDED_BACK = "Its computer handed the run back before starting it"


def queued_task_sql():
    """Match message_task_id's string/list handling before testing interrupted work."""
    refs = []
    # The Unicode whitespace stripped by Python's _task_ref, including older saved refs.
    whitespace = "char(9,10,11,12,13,28,29,30,31,32,133,160,5760,8192,8193,8194,8195,8196,8197,8198,8199,8200,8201,8202,8232,8233,8239,8287,12288)"
    for path in ("$.task", "$.task_id"):
        value = f"json_extract(m.refs_json,'{path}')"
        refs.append(f"CASE WHEN json_type(m.refs_json,'{path}')='text' THEN nullif(trim({value},{whitespace}),'') "
                    f"WHEN json_type(m.refs_json,'{path}')='array' THEN (SELECT trim(value,{whitespace}) "
                    f"FROM json_each(m.refs_json,'{path}') WHERE type='text' AND trim(value,{whitespace})<>'' "
                    "ORDER BY key LIMIT 1) END")
    return "coalesce(" + ",".join(refs) + f",nullif(trim(cv.task_id,{whitespace}),''))"


def reports_tools(c, attempt_id, config):
    """True when an attempt's silence about tools proves it called none: its harness always reports tool calls,
    or the run itself said it does (a `status` event with `tool_events`, sent by Claude and Codex runners that
    report each call; an older runner of the same harness does not, and its silence proves nothing). The latest
    such event counts, ahead of the harness: a turn that falls back to another host sends `tool_events: false`
    when it hops."""
    row = c.execute("SELECT json_extract(payload_json,'$.tool_events') AS said FROM attempt_events "
                    "WHERE attempt_id=? AND kind='status' AND json_extract(payload_json,'$.tool_events') IS NOT NULL "
                    "ORDER BY seq DESC LIMIT 1", (attempt_id,)).fetchone()
    if row:
        return bool(row['said'])
    return reports_tool_calls(config, config.get('runtime'))



# Match operations Tico can name, rather than treating every shell or file edit as an outside action.
OUTSIDE_ACTIONS = tuple((label, re.compile(pattern, re.I)) for label, pattern in (
    ('merge pull request', r"\bgh\s+pr\s+merge\b"),
    ('merge pull request', r"\bgh\s+api\b[^\n]*\bmerg(e|ed|ing)\b"),
    ('publish release', r"\bgh\s+release\s+create\b"),
    ('publish package', r"\bnpm\s+publish\b"),
    ('publish package', r"\bcargo\s+publish\b"),
    ('publish package', r"\btwine\s+upload\b"),
    ('publish package', r"\bdocker\s+(?:image\s+)?push\b"),
    ('consume approval', r"\b(?:hub\s+)?approval\s+consume\b"),
    ('send email', r"\b(?:hub\s+)?(?:mail|email)\s+send\b"),
    ('send email', r"\bsendmail\b"),
    ('send webhook', r"\b(?:hub\s+)?webhook\s+send\b"),
    ('make payment', r"\bstripe\s+(?:payments|payment_intents)\s+(?:create|confirm)\b"),
))
# Runner tool names that name the same operations, including the hub MCP tools with their runner prefix
# (`mcp__hub__hub_webhook_send` arrives here as `hub_webhook_send`). Internal hub writes (tasks, messages,
# comments) stay out on purpose: they are reversible teamwork, not outside effects.
OUTSIDE_TOOLS = {'send_email': 'send email', 'send_mail': 'send email', 'merge_pull_request': 'merge pull request',
                 'publish_release': 'publish release', 'make_payment': 'make payment',
                 'hub_release_now': 'publish release', 'hub_webhook_send': 'send webhook',
                 'hub_webhook_retry': 'send webhook', 'hub_webhook_recover': 'send webhook',
                 'hub_support_send': 'send support report'}


OUTSIDE_LABELS = frozenset(label for label, _ in OUTSIDE_ACTIONS) | frozenset(OUTSIDE_TOOLS.values())
# Older computers stored only the program. Do not mistake absent arguments for a harmless invocation.
LEGACY_OUTSIDE_PROGRAMS = {
    'gh': 'possible pull request merge or release (older computer omitted command details)',
    'npm': 'possible package publish (older computer omitted command details)',
    'cargo': 'possible package publish (older computer omitted command details)',
    'twine': 'possible package publish (older computer omitted command details)',
    'docker': 'possible image publish (older computer omitted command details)',
    'hub': 'possible approval, publish or send (older computer omitted command details)',
    'stripe': 'possible payment (older computer omitted command details)',
    'sendmail': 'send email',
}


def restart_reported(c, attempt_id):
    """Only the computer's terminal restart report relaxes generic tool uncertainty; a lost lease does not."""
    row = c.execute("SELECT state,result_json FROM attempts WHERE id=?", (attempt_id,)).fetchone()
    result = json.loads(row['result_json'] or '{}') if row else {}
    return bool(row and row['state'] in ('interrupted', 'expired') and result.get('outcome') == 'interrupted'
                and result.get('text') == 'Runner restarted during execution')


# Tico's update, or any restart of the hub, cuts the runs live then, mostly with no report from their computer, so each
# lapses. Updates must not wait for runs, so each such run is flagged at the cut (`flag_cut`): when an update starts,
# and when the hub starts for the runs its last process still held. A flagged run that ends without an answer within
# the lease plus UPDATE_MARGIN_S of the hub coming back (the update's snapshot, image switch and health check take
# minutes, docs/updates.md) was cut, and resumes on the next sweep with no Needs you (auto_reconcile). A run the flags
# miss (one begun just after the update, before the updater restarted its runner) is judged by the same window.
UPDATE_MARGIN_S = 15 * 60
UPDATE_CUT = 'attempt.update_cut'
UPDATE_RESUMES = 3          # update cuts one request resumes from; the next one ends it quietly
UPDATE_STALE_S = 24 * 3600  # an older cut is dismissed, not resumed: its work is stale by now


def flag_cut(c, cause, update=None):
    """Flag every live run (leased or running) as cut by `cause`: 'update' as Tico's update starts (`update` its
    target), 'restart' as the hub starts on the runs its last process held. How many it flagged."""
    rows = c.execute("SELECT id,bot,job_id FROM attempts WHERE state IN ('leased','running')").fetchall()
    for row in rows:
        H.event(c, H.KEEPER, UPDATE_CUT, row['id'], {'bot': row['bot'], 'job_id': row['job_id'], 'cause': cause,
                                                     **({'update': update} if update else {})})
    return len(rows)


def cut_by_update(c, attempt_id):
    """How Tico's update or a hub restart cut this run, {'cause': 'update' or 'restart', 'at': when}, else None. Only a
    lapsed or interrupted run counts: never one that reported its own end, a Stop (a person's or a quarantine's) or a
    usage limit, which keep their own rules. The updater restarts the runners after it reports its finish, so an
    unflagged run that began just after the update counts too. A flag counts only for a run already started when it
    was written: a lease claimed before the cut and started after it ran on the new hub, so its lapse is an ordinary
    one."""
    row = (c.execute("SELECT state,started,finished,stop_requested,result_json FROM attempts WHERE id=?",
                     (attempt_id,)).fetchone() if attempt_id else None)
    if (not row or row['state'] not in ('interrupted', 'expired') or not row['started'] or not row['finished']
            or row['stop_requested']):
        return None
    result = json.loads(row['result_json'] or '{}') or {}
    if result.get('limited') or result.get('limit_source'):
        return None
    window = Settings.lease_seconds + UPDATE_MARGIN_S
    flagged = c.execute("SELECT ts,detail_json FROM events WHERE action=? AND target=? ORDER BY ts",
                        (UPDATE_CUT, attempt_id)).fetchall()
    flags = [f for f in flagged if row['started'] <= f['ts']]
    if flagged and not flags:
        return None
    if flags:
        # The hub is back at its start (the last flag) or once the update finished, whichever is later.
        ended = c.execute("SELECT ts FROM events WHERE action='system.update.finished' AND ts>=? ORDER BY ts LIMIT 1",
                          (flags[-1]['ts'],)).fetchone()
        base = ended['ts'] if ended and ended['ts'] <= row['finished'] else flags[-1]['ts']
        if row['finished'] <= H.shift(base, seconds=window):
            update = any((json.loads(f['detail_json'] or '{}') or {}).get('cause') == 'update' for f in flags)
            return {'cause': 'update' if update else 'restart', 'at': flags[0]['ts']}
    began = c.execute("SELECT ts FROM events WHERE action='system.update.started' AND ts<=? ORDER BY ts DESC LIMIT 1",
                      (row['finished'],)).fetchone()
    if not began:
        return None
    ended = c.execute("SELECT ts FROM events WHERE action='system.update.finished' AND ts>=? ORDER BY ts LIMIT 1",
                      (began['ts'],)).fetchone()
    base = ended['ts'] if ended and ended['ts'] <= row['finished'] else began['ts']
    return {'cause': 'update', 'at': row['finished']} if row['finished'] <= H.shift(base, seconds=window) else None


def restart_cause(facts):
    """How a review card names the restart that ended a run: Tico's own update, or the computer's report of one."""
    return "The computer reported a restart" + (" during Tico's update" if facts['update'] else "")


def run_limit_minutes(c, attempt_id):
    """Recognize the runner's terminal run-budget cut, never a provider quota or Gateway limit."""
    row = c.execute("SELECT * FROM attempts WHERE id=?", (attempt_id,)).fetchone()
    if not row or row['state'] not in ('interrupted', 'expired'):
        return None
    result = json.loads(row['result_json'] or '{}')
    if result.get('outcome') != 'interrupted':
        return None
    from .run_diagnostics import reason
    match = re.fullmatch(r'Timeout: max_run_minutes \(([0-9]+(?:\.[0-9]+)?) minutes\)',
                         reason(c, row, 'interrupted'))
    return match[1] if match and float(match[1]) > 0 else None


def run_limit_cuts(c, job):
    """Count consecutive started attempts of this job, including folded inputs, up to the hold cap."""
    cuts = 0
    rows = c.execute("SELECT id FROM attempts WHERE started IS NOT NULL AND (job_id=? OR id IN "
                     "(SELECT attempt_id FROM attempt_inputs WHERE message_id=?)) "
                     "ORDER BY started DESC,created DESC,rowid DESC LIMIT 2", (job['id'], job['message_id']))
    for row in rows:
        if run_limit_minutes(c, row['id']) is None:
            break
        cuts += 1
    return cuts


def outside_actions(c, job, started, finished):
    actions = set()
    # Tool output may mention a command without executing it. Prefer the invocation's start summary and
    # keep only operation labels: arguments and output must never enter widely visible hold reasons.
    evidence = {}
    for row in c.execute("SELECT seq,payload_json FROM attempt_events WHERE attempt_id=? AND kind='tool' ORDER BY seq",
                         (job['attempt_id'],)):
        call = json.loads(row['payload_json'] or '{}')
        key = call.get('item_id') or f"seq:{row['seq']}"
        first = evidence.get(key)
        is_start = call.get('status', call.get('state')) in ('started', 'running', 'in_progress')
        if first is None or is_start:
            evidence[key] = call
    for call in evidence.values():
        name = str(call.get('tool') or call.get('name') or '').split('__')[-1]
        if name in OUTSIDE_TOOLS:
            actions.add(OUTSIDE_TOOLS[name])
        labels = call.get('outside_actions')
        classified = isinstance(labels, list) and all(isinstance(label, str) and label in OUTSIDE_LABELS
                                                     for label in labels)
        if classified:
            # Only canonical labels may reach review reasons; never trust arbitrary event text as a label.
            actions.update(labels)
        summary = str(call.get('summary') or '')
        if not classified and name in ('Bash', 'bash', 'shell', 'exec_command'):
            program = summary.strip().rsplit('/', 1)[-1].lower()
            if program in LEGACY_OUTSIDE_PROGRAMS:
                actions.add(LEGACY_OUTSIDE_PROGRAMS[program])
        # Compatibility with older command-bearing events. Output never overrides invocation evidence.
        actions.update(label for label, pattern in OUTSIDE_ACTIONS if pattern.search(summary))
    return sorted(actions)


def retry_facts(c, job, started, finished):
    """What a held run did, judged once for both the sweep that retries a safe one by itself (`auto_reconcile`) and
    the review card that recommends a person's choice (`review_jobs`), so the two can never disagree: its recorded
    calls, the ones that can change things, approvals it used up, whether its host's silence proves anything, how
    many times it has started, and named outside actions that can hold a computer-reported restart."""
    config = c.execute("SELECT config_json FROM bot_config WHERE bot=?", (job['bot'],)).fetchone()
    declared = json.loads(config[0]) if config else {}
    calls = recorded_tools(c, job['attempt_id'])
    acted = sorted({call['tool'] for call in calls if call['tool'] not in READ_ONLY_TOOLS})
    window = (started or job['created'], finished or H.now())
    # New consumes name their attempt. Older receipts can use the originating message only when
    # no competing attempt overlapped its consumption; another lane's approval never holds this run.
    approvals = c.execute("SELECT kind FROM approvals WHERE requested_by=? "
                          "AND consumed_at IS NOT NULL "
                          "AND (EXISTS(SELECT 1 FROM events e WHERE e.action='approval.consume' AND e.target=approvals.id "
                          "AND json_extract(e.detail_json,'$.attempt_id')=?) OR (NOT EXISTS "
                          "(SELECT 1 FROM events e WHERE e.action='approval.consume' AND e.target=approvals.id "
                          "AND json_extract(e.detail_json,'$.attempt_id') IS NOT NULL) AND consumed_at BETWEEN ? AND ? AND message_id=? AND NOT EXISTS "
                          "(SELECT 1 FROM attempts other JOIN jobs oj ON oj.id=other.job_id "
                          "WHERE oj.message_id=approvals.message_id AND other.id<>? "
                          "AND other.started<=approvals.consumed_at AND coalesce(other.finished,?)>=approvals.consumed_at)))",
                          ('bot:' + job['bot'], job['attempt_id'], *window, job['message_id'], job['attempt_id'], H.now())).fetchall()
    consumed = len(approvals)
    reported = reports_tools(c, job['attempt_id'], declared)
    # A message folded into another job's run counts that run as one of its own starts.
    starts = c.execute("SELECT count(*) FROM attempts WHERE started IS NOT NULL AND (job_id=? OR id IN "
                       "(SELECT attempt_id FROM attempt_inputs WHERE message_id=?))",
                       (job['id'], job['message_id'])).fetchone()[0]
    timeout = run_limit_minutes(c, job['attempt_id'])
    # A run its own time limit cut was not cut by the update, whenever it ended.
    update = None if timeout else cut_by_update(c, job['attempt_id'])
    restart = restart_reported(c, job['attempt_id'])
    attempt = c.execute('SELECT state,thread_id,runner_id FROM attempts WHERE id=?', (job['attempt_id'],)).fetchone()
    computer = c.execute('SELECT capabilities_json FROM runners WHERE id=?', (attempt['runner_id'],)).fetchone() if attempt else None
    # Only a negotiated strict continuation may bypass the legacy late-result grace. Unknown tools and
    # an already-final answer still need their original safety/settlement path.
    final = c.execute("SELECT 1 FROM attempt_events WHERE attempt_id=? AND kind='message' "
                      "AND json_extract(payload_json,'$.final')=1 LIMIT 1", (job['attempt_id'],)).fetchone()
    lease_lost = bool(attempt and attempt['state'] == 'expired' and started and attempt['thread_id']
                      and capacity_continue_on(computer) and reported and not acted and not final)
    outside = outside_actions(c, job, started, finished) if restart or lease_lost or timeout or update else []
    result = c.execute("SELECT result_json FROM attempts WHERE id=?", (job['attempt_id'],)).fetchone()
    result = json.loads(result[0] or '{}') if result else {}
    pending_source = result.get('source_pending') and not result.get('source_running')
    lease_lost = lease_lost and not pending_source
    source_bound = bool(result.get('source_running') and result.get('source_continuation')
                        and result['source_continuation'].get('thread_id') == attempt['thread_id'])
    fallback_unknown = pending_source or (result.get('outcome') == 'interrupted' and result.get('primary_held')
                       and result.get('fallback') and not result.get('source_pause') and not source_bound)
    return {'calls': calls, 'acted': acted, 'consumed': consumed, 'reported': reported, 'starts': starts,
            'restart': restart, 'update': update, 'lease_lost': lease_lost, 'timeout': timeout,
            'limit_cuts': run_limit_cuts(c, job) if timeout else 0,
            'outside': outside, 'approval_kinds': sorted({a['kind'] for a in approvals}),
            # Explicit restart/time-limit policy holds only named irreversible effects or consumed approvals. A silent
            # host alone is not such an effect; the continuation must check prior work before proceeding.
            'fallback_unknown': bool(fallback_unknown),
            'safe': not fallback_unknown and not consumed and (not outside if restart or timeout or lease_lost else reported and not acted)}

# Tool calls that only read: a run that made nothing else repeats nothing when it runs again. Claude's and Codex's
# own names. A subagent's launch is listed because its own calls are reported and judged one by one. A shell command
# never counts, whatever its program: `cat` can be piped into anything.
READ_ONLY_TOOLS = frozenset({
    "Read", "Grep", "Glob", "LS", "NotebookRead", "WebFetch", "WebSearch", "TodoWrite", "ToolSearch",
    "Agent", "Task", "web_search", "view_image", "spawnAgent", "subagent",
})


def recorded_tools(c, attempt_id):
    """Each tool call an attempt reported, in order, with its latest status: what a person reviewing the stopped
    run sees, and what decides whether it may run again on its own."""
    calls = {}
    for row in c.execute("SELECT seq,payload_json FROM attempt_events WHERE attempt_id=? AND kind='tool' ORDER BY seq",
                         (attempt_id,)):
        p = json.loads(row["payload_json"] or "{}") or {}
        key = p.get("item_id") or f"seq:{row['seq']}"
        call = calls.setdefault(key, {"tool": str(p.get("tool") or p.get("name") or "tool"), "summary": "",
                                      "status": "", "nested": bool(p.get("parent_id"))})
        call["summary"] = str(p.get("summary") or call["summary"])[:120]
        call["status"] = str(p.get("status") or p.get("state") or call["status"])
    return list(calls.values())


def owner_name(c, actor):
    """The name a person knows a task's owner by: the bot's display name or the person's name."""
    if not actor:
        return ""
    row = (H.bot(c, H.actor_id(actor)) if H.is_bot(actor) else H.human(c, H.actor_id(actor))) or {}
    return row.get("display_name") or row.get("name") or H.actor_id(actor)


def quiet_source_wait(c, job, facts=None, *, started_counts=None, attempt_times=None):
    """A proven safe second computer loss needs source recovery, not a person's effects review.

    `started_counts` / `attempt_times` are optional preloads from `_readable_held` so a status/operations
    pass does not SELECT count/attempt once per uncertain job.
    """
    if not job['attempt_id']:
        return False
    started = (started_counts or {}).get(job['id'])
    if started is None:
        started = c.execute(
            "SELECT count(*) FROM attempts WHERE job_id=? AND started IS NOT NULL", (job['id'],)).fetchone()[0]
    if started < 2:
        return False
    attempt = (attempt_times or {}).get(job['attempt_id'])
    if attempt is None:
        attempt = c.execute('SELECT started,finished FROM attempts WHERE id=?', (job['attempt_id'],)).fetchone()
    facts = facts or retry_facts(c, job, attempt['started'], attempt['finished'])
    return facts['safe'] and facts['lease_lost']


def _held_key(who, actor, bot):
    # task_sql's fence depends on the role as well as the actor.
    return ("readable_held", actor, who.role, bot)


def _readable_held(c, who, auth, bot):
    """The bot's runs saved for later that `who` may read, oldest first, each with its task when `who` may read that
    under the same rule as reading it (`auth.task_sql`), else None. A run on a task `who` may not read comes with
    task None and readable False, so it is counted without its title.

    Operations / status call held_runs, isolation_refused and stopped_rows for the same bot; cache the list for the
    read snapshot and batch message/attempt privacy once instead of message_readable per uncertain job per caller.
    `prime_readable_held` fills it for every uncertain bot at once.
    """
    from . import read_cache
    actor = privacy.actor(who)
    values = read_cache.cache(c)
    key = _held_key(who, actor, bot)
    if values is not None and key in values:
        return iter(values[key])
    out = _readable_held_for(c, who, auth, [bot])[bot]
    if values is not None:
        values[key] = out
    return iter(out)


def prime_readable_held(c, who, auth, bots):
    """`_readable_held` for many bots in one set of reads, kept for this snapshot (a status or operations page
    checks every stopped run of every bot): the queries scale with the runs, not with the bots."""
    from . import read_cache
    values = read_cache.cache(c)
    if values is None:
        return
    actor = privacy.actor(who)
    todo = [bot for bot in dict.fromkeys(bots) if _held_key(who, actor, bot) not in values]
    if not todo:
        return
    found = _readable_held_for(c, who, auth, todo)
    chosen = _tico_chosen_for(c, who, todo)
    for bot in todo:
        values[_held_key(who, actor, bot)] = found[bot]
        values[("tico_chosen", actor, bot)] = chosen[bot]


def _readable_held_for(c, who, auth, bots):
    """{bot: [(row, task or None, readable)]} for the stopped runs of `bots`, each bot's list oldest first."""
    actor = privacy.actor(who)
    fence = auth.task_sql(c, who)
    marks = ",".join("?" * len(bots))
    # A run Tico itself chose for (an update cut waiting for its lane, auto_reconcile) is nobody's to review.
    rows = list(c.execute(
        "SELECT m.*,j.attempt_id,j.id AS job_id,j.bot AS held_bot FROM jobs j JOIN messages m ON m.id=j.message_id "
        f"WHERE j.bot IN ({marks}) AND j.state='uncertain' AND NOT EXISTS(SELECT 1 FROM job_recovery r "
        "WHERE r.job_id=j.id AND r.attempt_id=j.attempt_id AND r.actor=?) ORDER BY j.created", [*bots, H.KEEPER]))
    shown = {m["id"] for m in privacy.readable_messages(c, actor, rows)}
    privacy.prime_attempts(c, [r["attempt_id"] for r in rows if r["id"] in shown and r["attempt_id"]])
    kept = [r for r in rows if r["id"] in shown
            and (not r["attempt_id"] or privacy.attempt_readable(c, actor, r["attempt_id"]))]
    job_ids = [r["job_id"] for r in kept]
    jobs = {}
    for part in H.chunks(job_ids):
        marks = ",".join("?" * len(part))
        jobs.update({j["id"]: j for j in c.execute(f"SELECT * FROM jobs WHERE id IN ({marks})", part)})
    # A job with no started attempt counts 0 here, so quiet_source_wait never falls back to its own SELECT.
    started_counts, attempt_times = dict.fromkeys(job_ids, 0), {}
    if job_ids:
        for part in H.chunks(job_ids):
            marks = ",".join("?" * len(part))
            for r in c.execute(
                    f"SELECT job_id, count(*) n FROM attempts WHERE job_id IN ({marks}) "
                    "AND started IS NOT NULL GROUP BY job_id", part):
                started_counts[r["job_id"]] = r["n"]
        aids = [r["attempt_id"] for r in kept if r["attempt_id"]]
        for part in H.chunks(aids):
            marks = ",".join("?" * len(part))
            for a in c.execute(f"SELECT id, started, finished FROM attempts WHERE id IN ({marks})", part):
                attempt_times[a["id"]] = a
    out = {bot: [] for bot in bots}
    for row in kept:
        job = jobs.get(row["job_id"])
        if not job or quiet_source_wait(c, job, started_counts=started_counts, attempt_times=attempt_times):
            continue
        message = H.message(c, row["id"])  # parsed refs: a comment or notice names its task there
        task_id = H.message_task_id(message, H.conversation(c, message["conversation_id"]))
        task = H.task(c, task_id) if task_id else None
        readable = bool(task and H.task_private_readable(c, actor, task)
                        and c.execute(f"SELECT 1 FROM tasks WHERE id=? AND {fence}", (task["id"],)).fetchone())
        if task and not readable:
            out[row["held_bot"]].append((row, None, False))
        else:
            out[row["held_bot"]].append((row, task, True))
    return out


def tico_chosen(c, who, bot):
    """How many of the bot's runs saved for later that `who` may read wait only for Tico's own choice to apply (an
    update cut behind live work in its lane, auto_reconcile): nobody's to review, so no line counts them. Read for
    every bot at once by prime_readable_held, so a status page pays no statement per bot for it."""
    from . import read_cache
    values, key = read_cache.cache(c), ("tico_chosen", privacy.actor(who), bot)
    if values is not None and key in values:
        return values[key]
    return _tico_chosen_for(c, who, [bot])[bot]


def _tico_chosen_for(c, who, bots):
    actor, out = privacy.actor(who), dict.fromkeys(bots, 0)
    rows = []
    for part in H.chunks(list(bots)):
        rows.extend(c.execute("SELECT m.*,j.attempt_id,j.bot AS held_bot FROM jobs j JOIN messages m ON m.id=j.message_id "
                              "JOIN job_recovery r ON r.job_id=j.id AND r.attempt_id=j.attempt_id "
                              f"WHERE j.bot IN ({','.join('?' * len(part))}) AND j.state='uncertain' AND r.actor=?",
                              (*part, H.KEEPER)))
    shown = {m["id"] for m in privacy.readable_messages(c, actor, rows)} if rows else set()
    for r in rows:
        if r["id"] in shown and (not r["attempt_id"] or privacy.attempt_readable(c, actor, r["attempt_id"])):
            out[r["held_bot"]] += 1
    return out


def held_runs(c, who, auth, bot, limit=3):
    """The tasks of a bot's stopped runs held for review that `who` may read, oldest first: title and owner.
    A task counts only under the same rule as reading it (`auth.task_sql`), so a company task involving a bot
    the reader may not read stays unnamed."""
    held, seen = [], set()
    for row, task, _ in _readable_held(c, who, auth, bot):
        # A request folded into a run shares the run's row: one title per run, however many jobs it settles.
        key = row["attempt_id"] or row["job_id"]
        if key in seen:
            continue
        seen.add(key)
        if task:
            held.append({"title": task["title"], "owner": task["owner"], "owner_name": owner_name(c, task["owner"])})
        if len(held) >= limit:
            break
    return held


# Why a held run stopped, in the words of one Needs you row (stopped_rows). Only a person's Stop and a plain stop are
# not `routine`: an interruption (a restart, update or drain), a usage limit, a busy AI service or a computer that
# stopped answering is the computer's or the provider's, so the person is told it is not a bug (Chris, Oct 26).
STOP_WHY = {"update": "Stopped during Tico's update", "interrupted": "Interrupted on its computer",
            "quota": "Usage limit", "capacity": "AI service busy", "timeout": "Computer stopped answering",
            "stopped": "Stopped by a person", "partway": "Stopped partway"}
ROUTINE_STOPS = frozenset({"update", "interrupted", "quota", "capacity", "timeout"})


def stop_cause(attempt, update=False):
    """One STOP_WHY key for a held run's attempt, from what the run recorded, never from the bot's own text: a Billing
    task's answer that says "quota" is not a usage limit. `update` is cut_by_update's answer for the run."""
    if not attempt:
        return "partway"
    if attempt["stop_requested"]:
        return "stopped"
    try:
        result = json.loads(attempt["result_json"] or "{}")
    except (TypeError, ValueError):
        result = {}
    result = result if isinstance(result, dict) else {}
    source = result.get("limit_source") if isinstance(result.get("limit_source"), dict) else {}
    if source.get("kind") == "capacity":
        return "capacity"
    # `primary_limited` alone means the run fell back to another model and carried on, so its state says why it stopped.
    if source or result.get("limited"):
        return "quota"
    if update:
        return "update"
    # A runner reports `interrupted` when its computer restarts or drains under a run, or the harness ends the turn
    # early, so the row says interrupted, not restarted; a lapsed lease is a computer that stopped reporting (asleep,
    # offline or too busy).
    return {"interrupted": "interrupted", "expired": "timeout"}.get(attempt["state"], "partway")


def stopped_rows(c, who, auth, bot, limit=5):
    """The bot's stopped runs that `who` may read, one row per run (a message folded into a run rides on its row),
    oldest first, for Needs you: (rows, runs). A row has the task title ("A chat request" outside a task, None for
    a task `who` may not read), why it stopped, whether that is `routine`, whether Try again is the safe pick
    (`retry`, by the same rule as the review card, retry_facts), when it stopped, and the jobs a decision settles.
    A choice saved while the bot's lane was busy (reconcile_job, deferred) keeps its job held: that job carries it as
    `decided`, and a row whose every job has one carries it too ("mixed" when they differ), so a refreshed card shows the outcome instead of
    offering a second choice that would be refused. `runs` counts every readable run; rows stop at `limit`."""
    held = list(_readable_held(c, who, auth, bot))
    job_ids = [row["job_id"] for row, _, _ in held]
    jobs, attempts, recoveries = {}, {}, {}
    for part in H.chunks(job_ids):
        marks = ",".join("?" * len(part))
        jobs.update({j["id"]: j for j in c.execute(f"SELECT * FROM jobs WHERE id IN ({marks})", part)})
        for r in c.execute(
                f"SELECT job_id, attempt_id, decision FROM job_recovery WHERE job_id IN ({marks})", part):
            recoveries[(r["job_id"], r["attempt_id"])] = r["decision"]
    aids = [row["attempt_id"] for row, _, _ in held if row["attempt_id"]]
    for part in H.chunks(aids):
        marks = ",".join("?" * len(part))
        for a in c.execute(
                f"SELECT id, state, result_json, stop_requested, started, finished FROM attempts "
                f"WHERE id IN ({marks})", part):
            attempts[a["id"]] = a
    rows, by_run = [], {}

    def judged(job, attempt):
        # Each request folded into a run is judged on its own facts (its starts, the approvals its message used), and
        # the run's line is only as safe as its least safe request, since one choice settles them all.
        facts = retry_facts(c, job, attempt["started"] if attempt else None, attempt["finished"] if attempt else None)
        return facts, {"retry": bool(facts["safe"] and facts["starts"] <= 1)}

    def handle(job):
        key = (job["id"], job["attempt_id"])
        return {"id": job["id"], "attempt_id": job["attempt_id"],
                **({"decided": recoveries[key]} if key in recoveries else {})}

    for row, task, seen in held:
        key = row["attempt_id"] or row["id"]
        job = jobs.get(row["job_id"])
        if not job:
            continue
        if key in by_run:
            if by_run[key] is not None:
                by_run[key]["jobs"].append(handle(job))
                _, flags = judged(job, attempts.get(row["attempt_id"]) if row["attempt_id"] else None)
                by_run[key].update({k: by_run[key][k] and v for k, v in flags.items()})
            continue
        if len(rows) >= limit:
            by_run[key] = None
            continue
        attempt = attempts.get(row["attempt_id"]) if row["attempt_id"] else None
        facts, flags = judged(job, attempt)
        cause = stop_cause(attempt, update=facts["update"])
        by_run[key] = {"title": task["title"] if task else "A chat request" if seen else None,
                       "cause": cause, "why": STOP_WHY[cause], "routine": cause in ROUTINE_STOPS, **flags,
                       "at": attempt["finished"] if attempt else None,
                       "jobs": [handle(job)]}
        rows.append(by_run[key])
    for row in rows:
        # The Details card decides each folded request on its own, so they can differ.
        saved = {job.get("decided") for job in row["jobs"]}
        row["decided"] = None if None in saved else saved.pop() if len(saved) == 1 else "mixed"
    return rows, len(by_run)


def isolation_refused(c, who, auth, bot):
    """Whether one of the bot's runs saved for later that `who` may read, task and all, is a Tico turn its runner
    refused for want of isolation (clients/gateway.py NOT_ISOLATED): held until a person moves the bot or changes
    its model."""
    return any(seen and row["attempt_id"]
               and any(gateway.run_code(error) == gateway.NOT_ISOLATED for error in limit_errors(c, row["attempt_id"]))
               for row, _, seen in _readable_held(c, who, auth, bot))


def held_back(c, who, auth, bot, limit=3):
    """The bot's queued work the claim's interrupted-work fence (unfenced_sql) passes over, as `who` may read it:
    (count, titles of up to `limit` tasks it is on). A saved recovery choice is already a review, so its lane wait
    must not be turned into another choice prompt; count jobs only when an unresolved stopped run still blocks them."""
    actor = privacy.actor(who)
    fence, count, titles = auth.task_sql(c, who), 0, []
    task_sql = queued_task_sql()
    held_task_sql = task_sql.replace("m.", "um.").replace("cv.", "uc.")
    rows = list(c.execute(
        f"SELECT m.*,j.attempt_id FROM jobs j JOIN messages m ON m.id=j.message_id "
        "JOIN conversations cv ON cv.id=m.conversation_id "
        f"WHERE j.bot=? AND j.state='queued' AND j.held=0 AND NOT {unfenced_sql()} "
        "AND EXISTS(SELECT 1 FROM jobs u JOIN messages um ON um.id=u.message_id "
        "JOIN conversations uc ON uc.id=um.conversation_id WHERE u.bot=j.bot "
        "AND u.state='uncertain' AND NOT EXISTS(SELECT 1 FROM job_recovery r "
        "WHERE r.job_id=u.id AND r.attempt_id=u.attempt_id) AND (("
        f"{held_task_sql} IS NOT NULL AND {held_task_sql}={task_sql}) OR "
        f"({held_task_sql} IS NULL AND cv.kind='chat' AND um.conversation_id=m.conversation_id))) "
        "ORDER BY j.created", (bot,)))
    shown = {m["id"] for m in privacy.readable_messages(c, actor, rows)}
    privacy.prime_attempts(c, [r["attempt_id"] for r in rows if r["id"] in shown and r["attempt_id"]])
    for row in rows:
        if row["id"] not in shown:
            continue
        if row["attempt_id"] and not privacy.attempt_readable(c, actor, row["attempt_id"]):
            continue
        count += 1
        message = H.message(c, row["id"])
        task_id = H.message_task_id(message, H.conversation(c, message["conversation_id"]))
        task = H.task(c, task_id) if task_id else None
        if (task and task["title"] not in titles and len(titles) < limit
                and H.task_private_readable(c, actor, task)
                and c.execute(f"SELECT 1 FROM tasks WHERE id=? AND {fence}", (task["id"],)).fetchone()):
            titles.append(task["title"])
    return count, titles


def unreviewed(c, who, bot):
    """The bot's held runs `who` may read that have waited HELD_UNREVIEWED or longer with no saved decision:
    (count, whole hours the oldest has waited). Counted per run, not per request: a run that settles several folded
    requests is one stopped run. A run waits from when it stopped (the job's creation when it never started); a saved
    decision that waits for its lane is a review already."""
    actor = privacy.actor(who)
    cutoff = H.shift(H.now(), seconds=-HELD_UNREVIEWED)
    count, oldest, seen = 0, None, set()
    rows = list(c.execute(
        "SELECT m.*,j.attempt_id,coalesce(a.finished,j.created) AS since FROM jobs j "
        "JOIN messages m ON m.id=j.message_id LEFT JOIN attempts a ON a.id=j.attempt_id "
        "WHERE j.bot=? AND j.state='uncertain' AND coalesce(a.finished,j.created)<=? "
        "AND NOT EXISTS(SELECT 1 FROM job_recovery r WHERE r.job_id=j.id AND r.attempt_id=j.attempt_id) "
        "ORDER BY since", (bot, cutoff)))
    shown = {m["id"] for m in privacy.readable_messages(c, actor, rows)}
    privacy.prime_attempts(c, [r["attempt_id"] for r in rows if r["id"] in shown and r["attempt_id"]])
    for row in rows:
        if row["id"] not in shown:
            continue
        if row["attempt_id"] and not privacy.attempt_readable(c, actor, row["attempt_id"]):
            continue
        key = row["attempt_id"] or row["id"]
        if key in seen:
            continue
        seen.add(key)
        count += 1
        oldest = oldest or row["since"]
    hours = int((H.parse_ts(H.now()) - H.parse_ts(oldest)).total_seconds() // 3600) if oldest else 0
    return count, hours


def left_by_keeper(c, who, bot):
    """How many of the bot's runs saved for later that `who` may read rode on a stopped run whose own request the
    keeper already settled (auto_reconcile): it decided that one and could not decide these safely, so nobody else
    was going to look at them."""
    actor = privacy.actor(who)
    rows = list(c.execute(
        "SELECT m.*,j.attempt_id FROM jobs j JOIN messages m ON m.id=j.message_id "
        "JOIN attempts a ON a.id=j.attempt_id "
        "JOIN job_recovery r ON r.job_id=a.job_id AND r.attempt_id=a.id "
        "WHERE j.bot=? AND j.state='uncertain' AND j.id<>a.job_id AND r.actor=? "
        # One Tico chose for itself too (an update cut behind live work) waits for nobody.
        "AND NOT EXISTS(SELECT 1 FROM job_recovery o WHERE o.job_id=j.id AND o.attempt_id=j.attempt_id AND o.actor=?)",
        (bot, H.KEEPER, H.KEEPER)))
    # One privacy prime (status/operations used to call message_readable per row here).
    shown = {m["id"] for m in privacy.readable_messages(c, actor, rows)}
    privacy.prime_attempts(c, [r["attempt_id"] for r in rows if r["id"] in shown and r["attempt_id"]])
    # Counted per run: requests folded into one stopped run share its attempt.
    runs = {r["attempt_id"] for r in rows if r["id"] in shown and privacy.attempt_readable(c, actor, r["attempt_id"])}
    return len(runs)


def limit_streak(c, bot):
    """How many of the bot's latest finished attempts in a row ended on a usage limit (capped
    at LIMIT_STRIKES; a lapsed lease or any other outcome ends the run)."""
    streak = 0
    for row in c.execute("SELECT result_json FROM attempts WHERE bot=? AND state IN ('completed','failed','interrupted') "
                         "AND json_extract(result_json,'$.check') IS NULL ORDER BY created DESC LIMIT ?",
                         (bot, LIMIT_STRIKES)):
        if not (row[0] and json.loads(row[0]).get("limited")):
            break
        streak += 1
    return streak


# What runner-rs report_failure tags a private meeting's turn with when no copy of the bot's repository could be made
# for it (sessions::PRIVATE_COPY_FAILED): no retry can help.
PRIVATE_COPY_FAILURE = "private_checkout"


def private_copy_failed(c, attempt_id):
    return c.execute("SELECT 1 FROM attempt_events WHERE attempt_id=? AND kind='diagnostic' "
                     "AND json_extract(payload_json,'$.code')=? LIMIT 1", (attempt_id, PRIVATE_COPY_FAILURE)).fetchone() is not None


def limit_errors(c, attempt_id):
    """The runtime's latest error texts on an attempt, newest first."""
    return [str(json.loads(row["payload_json"]).get("error") or "") for row in c.execute(
        "SELECT payload_json FROM attempt_events WHERE attempt_id=? AND kind='error' ORDER BY seq DESC LIMIT 8",
        (attempt_id,))]


# Said to the turn that continues one the model's capacity stopped (capacity_continuation): its thread holds what it did.
CONTINUE_NOTE = ("\n\nTico: the model was at capacity and stopped your previous turn on this request partway through. "
                 "This turn continues that same session once the model is free again: carry on from where it stopped, "
                 "and do not repeat anything it already did.")


def capacity_continuation(c, job_id):
    """The thread a job continues after the model's capacity stopped one of its turns once it had acted (complete
    `continues`), or None. The job continues it once: a second such stop is held for review, and none of its later
    turns starts a fresh thread or moves to a fallback, which could repeat what the stopped turn did."""
    row = c.execute("SELECT thread_id FROM attempts WHERE job_id=? AND json_extract(result_json,'$.capacity_continue')=1 "
                    "ORDER BY created DESC LIMIT 1", (job_id,)).fetchone()
    return row[0] if row else None


SOURCE_CONTINUATION_SQL = ("SELECT result_json FROM attempts WHERE job_id=? "
                           "AND json_extract(result_json,'$.source_continue')=1 ORDER BY created DESC,rowid DESC LIMIT 1")


def source_continuation(c, job_id):
    """The actual quota- or capacity-stopped fallback, bound to its job instead of the primary session."""
    from .read_cache import one
    row = one(c, SOURCE_CONTINUATION_SQL, (job_id,))
    return json.loads(row[0]).get('source_continuation') if row else None


def continuation_thread(c, job_id):
    """Require the saved provider session for capacity, quota or safe lease recovery."""
    source = source_continuation(c, job_id)
    if source:
        return source["thread_id"]
    capacity = capacity_continuation(c, job_id)
    if capacity:
        return capacity
    row = c.execute("SELECT thread_id FROM attempts WHERE job_id=? "
                    "AND (json_extract(result_json,'$.lease_continue')=1 OR json_extract(result_json,'$.quota_continue')=1) "
                    "ORDER BY created DESC LIMIT 1",
                    (job_id,)).fetchone()
    return row[0] if row else None


def last_limit(c, bot):
    """The bot's most recent attempt that ended on a usage limit."""
    # Another lane begun before the limit can finish afterwards without lifting the bot's hold.
    return c.execute("SELECT id,finished,json_extract(result_json,'$.limit_resets_at') AS resets_at "
                     "FROM attempts WHERE bot=? AND state IN ('failed','interrupted') "
                     "AND finished IS NOT NULL AND json_extract(result_json,'$.limited')=1 "
                     "AND json_extract(result_json,'$.check') IS NULL "
                     "ORDER BY finished DESC LIMIT 1", (bot,)).fetchone()


def billing_limit(c, bot=None, attempt_id=None):
    """Whether the limit (the bot's last, or this attempt's) was an API key's quota or billing."""
    if attempt_id is None:
        last = last_limit(c, bot)
        attempt_id = last["id"] if last else None
    # The Gateway's out-of-credit refusal says insufficient_quota too, but it is the company's credit, not a key's plan.
    return bool(attempt_id) and any(BILLING_LIMIT_RE.search(error) and not gateway.out_of_credit(error)
                                    for error in limit_errors(c, attempt_id))


def provider_reset(last, finished):
    """Seconds from the limit to the reset its runner reported from the provider's own fields, or None."""
    reset = H.parse_ts(last["resets_at"])
    if not reset or not reset.tzinfo:
        return None
    wait = int((reset - finished).total_seconds()) + 1
    return wait if 0 < wait <= LIMIT_RESET_MAX else None


def limit_cooldown(c, bot):
    """Seconds held after the last limit: until the provider's reported reset when there is one, else
    the default wait, stretched to a reset the error text names."""
    cooldown = LIMIT_COOLDOWN_LONG if limit_streak(c, bot) >= LIMIT_STRIKES else LIMIT_COOLDOWN
    last = last_limit(c, bot)
    if not last:
        return cooldown
    hold = login_holds.for_attempt(c, last["id"])
    if hold and (finished := H.parse_ts(last["finished"])) and (until := H.parse_ts(hold.get("until"))):
        # The login's hold (backend/login_holds.py) already chose the wait: the provider's reset, or its backoff.
        return max(int((until - finished).total_seconds()), 1)
    return reset_cooldown(c, last["id"], last["finished"], cooldown, last["resets_at"])


def reset_cooldown(c, attempt_id, finished, cooldown, resets_at=None):
    """`cooldown`, or until the reset the runner reported from the provider, or longer to reach a reset the
    attempt's limit error names."""
    finished = H.parse_ts(finished)
    if not finished:
        return cooldown
    errors = limit_errors(c, attempt_id)
    if any(gateway.cap_reached(error) for error in errors):
        # The Tico AI Gateway's cap is the key's for the UTC month: no retry before the next one, whatever a reported
        # reset or Retry-After said.
        return max(cooldown, int((gateway.month_after(finished) - finished).total_seconds()) + 1)
    reported = provider_reset({"resets_at": resets_at}, finished)
    if any(gateway.out_of_credit(error) for error in errors):
        # The company is out of credit: the Gateway lets the next call through as soon as credit lands, so the bot
        # asks again after its Retry-After (a minute), never a login's 30-minute hold.
        return reported or gateway.OUT_OF_CREDIT_WAIT
    if errors and all(login_holds.capacity(error) for error in errors):
        # The provider was busy for the moment, not out of quota: a short retry (backend/login_holds.py CAPACITY_WAIT),
        # never a reset the runner read from the plan's usage window.
        return login_holds.CAPACITY_WAIT
    if any(gateway.rate_limited(error) for error in errors):
        # Retry-After covers both the key's minute window and an address block; older runners keep the safe default.
        return reported or max(cooldown, gateway.RETRY_AFTER)
    if reported:
        return reported
    for error in errors:
        match = re.search(r"usage window resets at (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)", error, re.I)
        reset = H.parse_ts(match[1]) if match else None
        if reset:
            cooldown = max(cooldown, int((reset - finished).total_seconds()) + 1)
    return cooldown


LOGIN_CLASS_LABELS = {"release-blocker": "release", "critical-path": "review"}


def finished_before_start(c, row):
    """Whether a turn that has not started (it waits for its shared login: runner-rs/src/login_turns.rs) is for a task
    that was marked done or closed after the message that started it: it is skipped rather than run. A person's own
    message is never skipped: their words still get an answer."""
    if row["state"] != "leased":
        return False
    msg = H.message(c, c.execute("SELECT message_id FROM jobs WHERE id=?", (row["job_id"],)).fetchone()[0],
                    include_deleted=True)
    if msg and H.is_human(msg.get("from_actor")):
        return False
    task_id = H.message_task_id(msg, H.conversation(c, msg["conversation_id"])) if msg else None
    task = H.task(c, task_id) if task_id else None
    ended = task and task["status"] in ("done", "closed") and (task.get("closed_at") or task.get("done_at"))
    return bool(ended and ended >= msg["created"])


def login_class(c, task, msg, routine):
    """The class a turn waits in for a shared login that serves one turn at a time: "release" for a task labelled
    release-blocker, "review" for one labelled critical-path and for a turn a person's own message started, "other"
    for the rest. A routine's or a timed wake's turn is "other" whatever its labels, so standing watches never jump the
    queue. The labels count on the task or on the work it serves (its parents, the tasks it blocks)."""
    msg = msg or {}
    if routine or (not H.is_human(msg.get("from_actor")) and (msg.get("refs") or {}).get("wake")):
        return "other"
    labels = set(H.task_labels(task))
    if task:
        # Tags are set when a task is made, so a step subtask filed without one takes its class from the work it
        # serves: its parents, the tasks it blocks, and the same for the task whose session it continues.
        from .task_relations import ancestors, blocked_ids
        ids = set()
        for one in {task["id"], task.get("session_task_id") or task["id"]}:
            ids |= {one, *ancestors(c, one), *blocked_ids(c, one)}
        ids.discard(task["id"])
        if ids:
            marks = ",".join("?" * len(ids))
            labels |= {r[0] for r in c.execute(f"SELECT tags.key FROM task_tags JOIN tags ON tags.id=task_tags.tag_id "
                                               f"WHERE task_tags.task_id IN ({marks})", tuple(ids))}
    for label, cls in LOGIN_CLASS_LABELS.items():
        if label in labels:
            return cls
    return "review" if H.is_human(msg.get("from_actor")) else "other"


def limit_until(c, bot, since):
    """An absolute provider deadline wins over a later status refresh on another lane."""
    last = last_limit(c, bot)
    hold = login_holds.for_attempt(c, last['id']) if last else {}
    return hold.get('until') or H.shift(since, seconds=limit_cooldown(c, bot))


def release_limits(c):
    """Lift each usage limit whose hold has passed: the bot shows it is back, and the status change tells
    its computer to ask for the queued work now rather than at its next backup poll."""
    for row in c.execute("SELECT bot,since FROM bot_status WHERE state='limited'").fetchall():
        if row["since"] and limit_until(c, row["bot"], row["since"]) > H.now():
            continue
        settle_status(c, row["bot"], "idle", "Usage limit lifted; queued work resumes")
        H.event(c, H.KEEPER, "limit.released", row["bot"], {"since": row["since"]})


def hop_failed(c, attempt_id, result, began, ended):
    """Whether a turn ended on a usage limit its fallback could not take over: the bot's own runtime hit its limit
    (`primary_limited`), the runner moved the turn to a fallback, and the fallback failed before it did anything
    (after the hop only errors, notes and status, and soon: Claude and Codex never report their tool calls, so
    silence counts only with speed, as for `silent`). Nothing ran on either, so it is the limit's to wait out, not a
    stopped run for a person: on Oct 21 27 of Reviewer 2's turns hopped to a fallback that could not start and each
    was held until a person dismissed it."""
    field = login_holds.field
    if not (field(result, "primary_limited") and field(result, "fallback") and not field(result, "limited")
            and field(result, "outcome") == "failed" and field(result, "auth_rejected") is None):
        return False
    window = (began, ended)
    began, ended = H.parse_ts(began), H.parse_ts(ended)
    if not began or not ended or (ended - began).total_seconds() > NO_EFFECT_WITHIN:
        return False
    # A harness that never reports its tool calls can still have used an approval a person gave: running the turn
    # again would repeat that action, so such a turn is a person's to review, as in auto_reconcile.
    bot = (c.execute("SELECT bot FROM attempts WHERE id=?", (attempt_id,)).fetchone() or [None])[0]
    if c.execute("SELECT 1 FROM approvals WHERE requested_by=? AND consumed_at IS NOT NULL AND consumed_at BETWEEN ? AND ? "
                 "LIMIT 1", ("bot:" + str(bot), *window)).fetchone():
        return False
    hop, after = None, set()
    for row in c.execute("SELECT seq,kind,payload_json FROM attempt_events WHERE attempt_id=? ORDER BY seq",
                         (attempt_id,)):
        if hop is not None:
            after.add(row["kind"])
        elif row["kind"] == "diagnostic" and ((p := json.loads(row["payload_json"] or "{}") or {}).get("fallback")
                                               or login_holds.HOP_RE.search(str(p.get("text") or ""))):
            hop = row["seq"]
        elif row["kind"] == "tool":
            return False
    return hop is not None and after <= SILENT_KINDS


def empty_fallback_interruption(c, settings, row, result):
    """An acknowledged terminal interruption with complete telemetry, before any output or effects."""
    field = login_holds.field
    hold = own_login_hold(c, settings, row['bot'])
    if not (hold.get('reset') and field(result, 'outcome') == 'interrupted' and field(result, 'primary_held')
            and field(result, 'fallback') and not field(result, 'text')
            and (hold.get('until') or '') > H.now()):
        return False
    job = c.execute("SELECT * FROM jobs WHERE id=?", (row['job_id'],)).fetchone()
    facts = retry_facts(c, job, row['started'], H.now())
    if facts['consumed'] or facts['calls'] or not facts['reported'] or field(result, 'pushes'):
        return False
    # Full streamed output is uploaded before complete(). A host's explicit end event establishes that
    # the stream ended; a dropped connection, lost lease or timeout cannot supply this receipt.
    terminal = False
    for event in c.execute("SELECT kind,payload_json FROM attempt_events WHERE attempt_id=? ORDER BY seq", (row['id'],)):
        payload = json.loads(event['payload_json'] or '{}')
        if event['kind'] in ('delta', 'message', 'tool'):
            return False
        if event['kind'] == 'tokens':
            output = payload.get('output', (payload.get('usage') or {}).get('output'))
            if output != 0:
                return False
        if event['kind'] == 'diagnostic' and payload.get('interruption_complete') is True:
            terminal = True
    usage = field(result, 'usage')
    spent = [usage, *(field(usage, 'segments') or [])] if usage else []
    return terminal and not (field(result, 'tokens_out') or 0) and not any(
        field(part, 'output_tokens') for part in spent)


def hold_job_until_reset(c, row, hold, reason):
    """Keep an established empty retry visibly held until its known reset, outside wake batching."""
    until = hold.get('until') or ''
    if not hold.get('reset') or until <= H.now():
        return
    c.execute("UPDATE jobs SET held=1,hold_until=?,wake_class='quota-reset' WHERE id=? AND state='queued'",
              (until, row['job_id']))
    # A claim that ran nowhere inherits the existing deadline, not another default cooldown.
    c.execute("UPDATE attempts SET result_json=json_set(result_json,'$.limit_resets_at',?) WHERE id=?",
              (until, row['id']))
    H.event(c, H.KEEPER, 'job.reset_wait', row['job_id'],
            {'attempt_id': row['id'], 'owner': H.KEEPER, 'until': until, 'reason': reason})
    return hold


def fallback_mark(config):
    """The fallback a bot names, as a hold remembers it failed (login_holds.after_turn `hopped`): naming another
    one, or none, lets the bot try again."""
    return json.dumps((config or {}).get("fallback") if isinstance(config, dict) else None, sort_keys=True)


def limit_lifted(c, settings, bot, attempt_id, finished):
    """Why a run a usage limit stopped (hop_failed) may run again now, or None: the bot was moved to another model
    since ("model"), its own limit is over ("reset": no cooldown, no hold on its login, and no reset named in the
    limit's notice still ahead), or a fallback it names is ready here and has not failed in this hold ("fallback")."""
    if model_changed_since(c, bot, finished):
        return "model"
    now = H.now()
    hold = own_login_hold(c, settings, bot)
    named = [login_holds.reset_from_text(error, finished) for error in limit_errors(c, attempt_id)]
    if (not cooling_off(c, bot) and (hold.get("until") or "") <= now
            and not any(at and at > H.parse_ts(now) for at in named)):
        return "reset"
    row = c.execute("SELECT config_json FROM bot_config WHERE bot=?", (bot,)).fetchone()
    config = json.loads(row[0] or "{}") if row else {}
    place = c.execute("SELECT runner_id FROM assignments WHERE bot=?", (bot,)).fetchone()
    if (isinstance(config.get("fallback"), dict) and config["fallback"].get("harness") and place and place["runner_id"]
            and fallback_choice(c, settings, place["runner_id"], bot, config)):
        return "fallback"
    return None


def person_wrote_since(c, bot, since):
    """Whether a person's message to the bot is queued from after its latest usage limit. A person
    who writes to a limited bot has often just renewed the plan, so the bot tries once now; a
    run that hits the limit again finishes after the message and the cooldown holds."""
    last = c.execute("SELECT max(finished) FROM attempts WHERE bot=?", (bot,)).fetchone()[0]
    return c.execute("SELECT 1 FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.bot=? AND j.state='queued' "
                     "AND m.from_actor LIKE 'human:%' AND m.kind<>'notice' AND m.created>? LIMIT 1",
                     (bot, max(since, last or ""))).fetchone() is not None


def cooling_off(c, bot):
    """Whether a usage limit, or the wait after the Tico AI Gateway was unavailable, still holds the bot: its status is
    `limited` and the cooldown has not passed (or the Gateway's wait has not), and no person has written to it since
    (a person's message is worth one try). Asked once a bot in a snapshot: Health asks for a limited bot's twice."""
    from .read_cache import value
    return value(c, ("cooling_off", bot), lambda: _cooling_off(c, bot))


def _cooling_off(c, bot):
    from .repositories import metadata
    status = H.status(c, bot) or {}
    wait = metadata(c, GATEWAY_WAIT + bot)
    if wait.get("until") and wait["until"] > H.now() and not person_wrote_since(c, bot, wait.get("since") or ""):
        return True
    return bool(status.get("state") == "limited" and status.get("since")
                and limit_until(c, bot, status["since"]) > H.now()
                and not person_wrote_since(c, bot, status["since"]))


GATEWAY_WAIT = "gateway-unavailable:"


def clear_limits(c, settings, actor, bot):
    """Try now: lift the usage-limit cooldown for the bot and for every bot limited on the same
    runtime on the same computer, since they share the subscription a person has just renewed.
    Returns how many bots it cleared."""
    from .shared_bots import follow

    def config(slug):
        row = c.execute("SELECT config_json FROM bot_config WHERE bot=?", (slug,)).fetchone()
        return follow(c, slug, json.loads(row[0]) if row and row[0] else {})

    def runtime(slug):
        return providers.bot_choice(c, settings, config(slug))[0]
    mine, place = runtime(bot), c.execute("SELECT runner_id FROM assignments WHERE bot=?", (bot,)).fetchone()
    bots = [bot]
    if place and place["runner_id"] and mine:
        bots += [r[0] for r in c.execute("SELECT s.bot FROM bot_status s JOIN assignments a ON a.bot=s.bot "
                                         "WHERE s.state='limited' AND a.runner_id=? AND s.bot<>? ORDER BY s.bot",
                                         (place["runner_id"], bot)) if runtime(r[0]) == mine]
    for slug in bots:
        if place and place["runner_id"]:
            # The plan behind the login was just renewed: its hold ends for every bot on it, but only one queued job
            # tries it until that turn's result (login_holds.try_now), so a plan still spent sees one turn, not a burst.
            login_holds.try_now(c, *login_key(c, settings, place["runner_id"], slug, config(slug)))
        if (H.status(c, slug) or {}).get("state") == "limited":
            H.status_set(c, actor, slug, state="idle", focus="", reason="Try now")
        c.execute("UPDATE jobs SET held=0,hold_until=NULL WHERE bot=? AND state='queued' "
                  "AND wake_class='quota-reset' AND hold_reason IS NULL", (slug,))
        H.event(c, actor, "limit.cleared", slug, {"from": bot, "runtime": mine})
    return len(bots)


def own_login_hold(c, settings, bot):
    """The hold on the bot's own login on its computer (backend/login_holds.py), or {}: it can stand while the bot is
    not `limited`, its work going on on a fallback. The bot's config is the one a claim runs (shared_bots.follow)."""
    from .shared_bots import follow
    place = c.execute("SELECT runner_id FROM assignments WHERE bot=?", (bot,)).fetchone()
    row = c.execute("SELECT config_json FROM bot_config WHERE bot=?", (bot,)).fetchone()
    if not place or not place["runner_id"] or not row:
        return {}
    config = follow(c, bot, json.loads(row[0] or "{}"))
    return login_holds.get(c, *login_key(c, settings, place["runner_id"], bot, config))


def _github_app(c):
    try:
        return c.execute("SELECT org FROM github_app WHERE id='app'").fetchone()
    except sqlite3.OperationalError:
        return None


def bot_repository(c, settings, bot, rows=None, app=None):
    """`owner/name` of a bot's GitHub repository, resolved the way the token route resolves it, or "".
    The runner clones and publishes this one (runner-rs/src/fetchrepo.rs). `rows(slug)` (its
    bot_config row with config_json and repo) and `app` ((the github_app row,)) when read already."""
    from .github_app import repo_of
    from .shared_bots import _json, declared, source_of
    if rows is None:
        config = declared(c, bot)
    else:
        found = rows(bot)
        config = _json(found["config_json"]) if found else {}
    bot = source_of(config) or bot
    config = rows(bot) if rows is not None else c.execute("SELECT repo FROM bot_config WHERE bot=?", (bot,)).fetchone()
    app = app[0] if app is not None else _github_app(c)
    return repo_of(config["repo"] if config else "", (app["org"] if app else "") or settings.github_owner) or ""


def _reported(c, runner_id, bot):
    row = c.execute("SELECT label,readiness_json FROM runners WHERE id=?", (runner_id,)).fetchone()
    report = (readiness_document(row["readiness_json"]).get("bots") or {}).get(bot) if row else None
    return (row["label"] if row else "a computer"), report if isinstance(report, dict) else {}


def stranded(c, settings, bot, source_id, target_id):
    """Why moving `bot` from one computer to another would leave it without its repository, or "".

    The destination gets it by cloning from GitHub. That fails when the destination holds no copy and the
    computer being left reports a checkout GitHub does not have (`published` false): its history exists
    only there. A runner that does not report `published` is given the benefit of the doubt."""
    _, there = _reported(c, target_id, bot)
    if there.get("repository_present") is True:
        return ""
    source, here = _reported(c, source_id, bot)
    if here.get("published") is not False:
        return ""
    target, _ = _reported(c, target_id, bot)
    repository = bot_repository(c, settings, bot) or "its repository"
    return (f"{bot}'s history exists only on {source}: {repository} is not on GitHub yet, so {target} could not fetch "
            f"it and the bot would stop running. Create it as an empty private repository on GitHub "
            f"(`hub bot repo-create {bot} --empty`, or by hand); {source} then publishes it, and the bot can move")


def forget_report(c, runner_id, bot):
    """Drop a computer's stored report on a bot it no longer hosts, so a move never leaves the old computer
    claiming the bot is ready there until that computer's next heartbeat says what it holds now."""
    row = c.execute("SELECT readiness_json FROM runners WHERE id=?", (runner_id,)).fetchone()
    if not row:
        return
    document = readiness_document(row["readiness_json"])
    if bot in (document.get("bots") or {}):
        del document["bots"][bot]
        c.execute("UPDATE runners SET readiness_json=? WHERE id=?", (encode(document), runner_id))


# The runner appends these after the bot's own words when it holds back a commit (runner-rs/src/execute.rs).
# They are the runner's, not the bot's, so chat shows them as a warning beside the reply.
RUNNER_NOTICE = re.compile(r"(not pushed: a commit made this turn contains a secret"
                           r"|left out of the commit: .+ \((?:contains a secret|too large to commit)\))")


def split_runner_notices(text):
    """The reply without the runner's trailing notices, and those notices. A reply that is only
    notices keeps them as its text so the person still hears about them."""
    parts, notices = text.split("\n\n"), []
    while len(parts) > 1 and RUNNER_NOTICE.fullmatch(parts[-1].strip()):
        notices.insert(0, parts.pop().strip())
    return "\n\n".join(parts), notices


def checkpoint_safe(c, aid, task):
    """Whether this run's reply may be kept on `task` as its checkpoint: only when the run took in no
    other private task. Stricter than a posted reply's rule: a checkpoint keeps no record of what it
    drew on, so another task's private details in it would leak once `task` is published or
    reassigned. When it is not safe the task's earlier checkpoint stays as it was: it predates this
    run, so it holds nothing from these other tasks, and a stale starting point beats none."""
    return not any(other and H.task_private(c, other)
                   for other in (H.task(c, tid) for tid in privacy.attempt_tasks(c, aid) if tid != task["id"]))


# A drained bot (a deploy finishing its running turns, or a person's pause) is given no new work.
DRAINING_SQL = "EXISTS(SELECT 1 FROM bot_control ctl WHERE ctl.bot=a.bot AND ctl.draining=1)"


# What a Finance hook may raise that must not hold a bot's work: its own refusals and a bug in it.
FINANCE_FAULTS = (ValueError, LookupError, TypeError, ArithmeticError, AttributeError)


def finance_run_hold(c, bot, job_id):
    """Hold the run's likely Gateway cost on the Finance budget that covers the bot, in the claim's transaction
    (backend/finance/runs.py). True when a budget at its cutoff refuses the run. A fault in Finance holds nothing and
    refuses nothing; a storage error fails the claim as any other does."""
    from .finance import actions as finance
    c.execute("SAVEPOINT finance_hold")
    try:
        refused = finance.run_hold(c, bot, job_id).refused
    except (*FINANCE_FAULTS, finance.FinanceError) as exc:
        c.execute("ROLLBACK TO finance_hold")
        c.execute("RELEASE finance_hold")
        report("finance_hold", job_id, exc)
        return False
    c.execute("RELEASE finance_hold")
    return refused


def finance_run_ended(c, job_id, turn_id):
    """Settle the job's run hold at the run's priced Gateway cost, or release it when nothing was Gateway-billed."""
    from .finance import actions as finance
    c.execute("SAVEPOINT finance_settle")
    try:
        finance.run_ended(c, job_id, turn_id)
    except (*FINANCE_FAULTS, finance.FinanceError) as exc:
        c.execute("ROLLBACK TO finance_settle")
        c.execute("RELEASE finance_settle")
        report("finance_settle", turn_id, exc)
        return
    c.execute("RELEASE finance_settle")


def claim_refusal(c, settings, runner, bot, config_json, draining, ready, held_bots=(), subscription_context=None,
                  default=None):
    """Why `runner`'s claim passes over all of `bot`'s queued work, or (None, config) when it may look at the bot's
    jobs. A bot at its own "Turns at once" holds its work on every computer ("bot_limit") once nothing else holds it."""
    why, config = _claim_refusal(c, settings, runner, bot, config_json, draining, ready, held_bots,
                                 subscription_context, default)
    if why is None and (limit := start_places.bot_limit(config)) is not None and start_places.bot_held(c, bot) >= limit:
        return "bot_limit", None
    return why, config


def _claim_refusal(c, settings, runner, bot, config_json, draining, ready, held_bots=(), subscription_context=None,
                   default=None):
    """Why `runner`'s claim passes over all of `bot`'s queued work before it looks at any one job: (reason, None), or
    (None, config) when it may look at the bot's jobs. The claim (Execution.candidate) and Health (bot_refusal) use
    this one rule, so Health never calls work the claim holds on purpose a Tico bug. `held_bots`: bots the runner
    says are busy (a lane runner sends only the bots whose worktrees it is maintaining) and, for a runner without
    lanes, every bot with a running turn. `ready` is the computer's readiness document."""
    if draining:
        return "draining", None
    if cloud_access.locked(c):
        return "cloud_locked", None         # a locked Tico Cloud company runs no bot (backend/cloud_access.py)
    if bot in held_bots:
        return ("maintaining" if lanes_on(runner) else "lane_held"), None
    if (why := run_backoff.refusal(c, bot)) and not (why == "backoff" and run_backoff.person_waiting(c, bot)):
        return why, None            # waiting out failed runs, or held until a person releases it (backend/run_backoff.py)
    try:
        config = json.loads(config_json)
    except (ValueError, TypeError):
        config = None
    if not isinstance(config, dict):
        return "config", None
    from .subscriptions import login_upgrade
    if login_upgrade(runner, ready, bot=bot):
        return "login_upgrade", None
    if browser_exit.CAPABILITY not in _capability_set(runner["capabilities_json"]) and browser_exit.bot_exit(c, bot):
        return "browser_exit_update", None
    record = providers.load(c, settings)
    filled = providers.fill(record, config)
    # A runtime this computer's subscription signs in needs no sign-in here (backend/model_subscriptions.py).
    ready = model_subscriptions.covered(ready, model_subscriptions.covered_runtimes(c, runner, filled))
    if cloud_access.frozen(c) and (gateway_connection.is_tico(filled.get("model"))
                                   or models_preset.waiting(record, ready, filled)):
        # A Tico Cloud company out of credit: no new turn that would spend it. A bot on its own subscription or keys
        # still runs; this one starts again once credit lands, with no restart.
        return "out_of_credit", None
    if gateway_connection.is_tico(filled.get("model")) and not models_preset.runs_gateway(ready):
        # A Tico model runs only where bots run as their own user, or on a cloud machine (runner-rs/src/gateway_relay.rs
        # Separation): elsewhere the runner would refuse the turn, so the work waits here instead of failing there.
        return ("not_isolated" if ready.get("isolated") is False else "isolation_unknown"), None
    check = bot_readiness(ready, bot)
    if models_preset.waiting(record, ready, filled):
        # Its subscription is not signed in on this computer yet: the turn runs on the Gateway instead
        # (backend/models_preset.py), so the missing sign-in is the one problem that doesn't hold it. Its checkout,
        # its settings and the Gateway's own runtime still have to be ready.
        if bot not in (ready.get("bots") or {}):
            return "readiness_unknown", None
        if (check.get("repository_present") is not True or check.get("configuration_valid") is not True
                or ((ready.get("runtimes") or {}).get(models_preset.GATEWAY_RUNTIME) or {}).get("installed") is not True):
            return "not_ready", None
        if usage_limits.blocked(c, bot, default=usage_limits.company(c) if default is None else default):
            return "limit", None
        return ("cooling_off", None) if cooling_off_here(c, settings, runner, bot, config) else (None, config)
    if check.get("ready") is not True:
        reported = check.get("ready") is False and bot in (ready.get("bots") or {})
        return ("not_ready" if reported else "readiness_unknown"), None
    if ((ready.get("runtimes", {}).get(check.get("runtime")) or {}).get("authenticated") == "rejected"
            and not check.get("profile") and check.get("sign_in") != "ready"):
        return "sign_in_rejected", None
    if usage_limits.blocked(c, bot, default=usage_limits.company(c) if default is None else default):
        return "limit", None
    # While the bot's own login is held, that hold decides (login_refusal), so a ready fallback can take the turn;
    # otherwise the bot's own cooldown does, as for a limit its fallback met.
    if (cooling_off_here(c, settings, runner, bot, config, subscription_context)
            and not primary_hold(c, settings, runner["id"], bot, config, ctx=subscription_context)
            or login_refusal(c, settings, runner["id"], bot, config, ctx=subscription_context)):
        # Already-started fallback work continues its own login even if new fallbacks have since been disabled.
        pending = c.execute("SELECT id FROM jobs WHERE bot=? AND state='queued' AND held=0", (bot,))
        if not any(source_continuation(c, j['id']) and not login_refusal(c, settings, runner['id'], bot, config, j['id'])
                   for j in pending):
            return "cooling_off", None
    return None, config


def cooling_off_here(c, settings, runner, bot, config, ctx=None):
    """cooling_off for a turn of `bot` on `runner`, judged on the login that turn would sign in with. A limit met on a
    company subscription holds that subscription (backend/login_holds.py `sub:<id>`), which the computer then passes
    over (model_subscriptions.in_effect): the turn runs on the computer's own login or the Gateway instead, so the
    subscription's cooldown does not keep it waiting. That login's own hold, if any, still decides (login_refusal)."""
    if not cooling_off(c, bot):
        return False
    last = last_limit(c, bot)
    held = str((login_holds.for_attempt(c, last["id"]) if last else {}).get("profile") or "")
    if held.startswith(login_holds.SUBSCRIPTION) and runner:
        return login_key(c, settings, runner["id"], bot, config, ctx)[1] == held
    return True


def login_refusal(c, settings, runner_id, bot, config, job_id=None, ctx=None):
    """Whether a hold on the bot's own login keeps it waiting (backend/login_holds.py): before the hold ends, no bot
    on the login starts a turn; after it, only the probe job does until its turn ends. A bot with a fallback that is
    ready here and not held itself does not wait: its turn runs there (primary_hold marks the job so the runner skips
    the held login). Without `job_id` it answers for the bot: a probe of its own still lets it through, for
    job_refusal's per-job answer."""
    source = source_continuation(c, job_id) if job_id else None
    if source:
        return login_wait(c, runner_id, login_holds.canonical(c, runner_id, source["profile"], source["runtime"]),
                          source["runtime"], bot, job_id)
    why = primary_hold(c, settings, runner_id, bot, config, job_id, ctx)
    if why and job_id and continuation_thread(c, job_id):
        return why          # only its own thread, on its own login, continues it
    return None if why and fallback_choice(c, settings, runner_id, bot, config, job_id, ctx) else why


def primary_hold(c, settings, runner_id, bot, config, job_id=None, ctx=None):
    """Why the bot's own login takes no turn now ("held" or "probing"), or None."""
    _, profile, runtime = login_key(c, settings, runner_id, bot, config, ctx)
    why = login_wait(c, runner_id, profile, runtime, bot, job_id)
    hold = login_holds.get(c, runner_id, profile, runtime)
    if why == "held" and not hold.get("reset") and person_wrote_since(c, bot, hold.get("since") or ""):
        return None         # a person who writes has often just renewed the plan: one try (cooling_off)
    return why


def login_wait(c, runner_id, profile, runtime, bot, job_id=None):
    """login_holds.holding for one of `bot`'s jobs; without `job_id`, for the bot: a probe of its own still lets
    it through, for the per-job answer."""
    why = login_holds.holding(c, runner_id, profile, runtime, job_id)
    if why == "probing" and job_id is None:
        probe = login_holds.get(c, runner_id, profile, runtime).get("probe_job")
        if c.execute("SELECT 1 FROM jobs WHERE id=? AND bot=?", (probe, bot)).fetchone():
            return None
    return why


def fallback_choice(c, settings, runner_id, bot, config, job_id=None, ctx=None):
    """Where a turn of `bot` held off its own login runs on this computer, as (profile, runtime), or None to wait. It
    is the runner's own choice (runner-rs/src/fallback.rs `configured`, `default_choice`): the fallback the bot names,
    else the first of the company's providers on another runtime that the computer reports signed in and no hold
    keeps off (the runner skips those too, from the holds its config carries). That login's hold having just ended,
    the turn is its one probe, so with another job's probe out on it the bot waits rather than the runner picking it
    anyway. The Gateway counts when the turn would be handed it (models_preset.with_gateway_fallback). A fallback
    turned off, none, or one the runner already found not ready in this hold window (`stuck`) means waiting."""
    from .harnesses import harness_caps, runtime_of
    named = config.get("fallback") if isinstance(config.get("fallback"), dict) else {}
    if named.get("off"):
        return None
    _, own, primary = login_key(c, settings, runner_id, bot, config, ctx)
    # The runner ignores a named model on this same spent login and continues through the company list.
    # Match it here, so the claim admits that next provider, or holds the job when there is none.
    if (named.get("harness") and named.get("model") and runtime_of(named["harness"]) == primary
            and harness_caps(config.get("harness"), primary).get("api_keys") != "bot"
            and harness_caps(named["harness"]).get("api_keys") != "bot"):
        named = {}
    hold = login_holds.get(c, runner_id, own, primary)
    if bot in hold.get("stuck", []) or (hold.get("failed_fallback") or {}).get(bot) == fallback_mark(config):
        return None
    row = c.execute("SELECT readiness_json FROM runners WHERE id=?", (runner_id,)).fetchone()
    ready = readiness_document(row["readiness_json"]) if row else {}
    if named.get("harness"):
        if not named.get("model"):
            return None
        rows = [{"runtime": runtime_of(named["harness"]), "model": named["model"]}]
    else:
        record = providers.load(c, settings)
        gateway = "gateway_fallback" in models_preset.with_gateway_fallback(record, ready, providers.fill(record, config))
        rows = [r for r in providers.default_fallbacks(record) if r["runtime"] != primary
                and (gateway or not providers.gateway_model(r["model"]))]
    for choice in rows:
        if providers.gateway_model(choice["model"]):
            # Signs in with the turn's relay: no login of its own to hold, but only where bots run as their own user
            # (a company row is listed only then; one the bot names is checked here).
            if not models_preset.runs_gateway(ready):
                continue
            return "", choice["runtime"]
        signed = (ready.get("runtimes") or {}).get(choice["runtime"]) or {}
        if signed.get("installed") is not True or signed.get("authenticated") != "ready":
            continue
        login = login_holds.canonical(c, runner_id, "", choice["runtime"])
        why = login_wait(c, runner_id, login, choice["runtime"], bot, job_id)
        if why == "held":
            continue
        return None if why else (login, choice["runtime"])
    return None


def login_key(c, settings, runner_id, bot, config, ctx=None):
    """(runner, login, runtime) of the bot's own login, as holds key it (login_holds.canonical)."""
    runtime = providers.bot_choice(c, settings, config)[0]
    return runner_id, login_holds.canonical(c, runner_id, "", runtime), runtime


def unfenced_sql():
    """SQL over a queued job's message `m` and conversation `cv`: true unless the bot's interrupted work holds it.
    Unconfirmed effects hold the interrupted task and conversation until review; other tasks and routines of the same
    bot continue, and a person's chat outside any task stays claimable so an interrupted routine cannot silence its
    owner."""
    task_sql = queued_task_sql()
    return (f"((m.from_actor LIKE 'human:%' AND cv.kind='chat' AND {task_sql} IS NULL) "
            "OR NOT EXISTS(SELECT 1 FROM jobs u JOIN messages um ON um.id=u.message_id "
            "JOIN conversations uc ON uc.id=um.conversation_id WHERE u.bot=j.bot AND u.state='uncertain' "
            f"AND (({task_sql} IS NOT NULL AND "
            + H.MESSAGE_TASK_SQL.replace("m.", "um.").replace("cv.", "uc.") + f"={task_sql}) "
            f"OR ({task_sql} IS NULL AND cv.kind='chat' AND um.conversation_id=m.conversation_id))))")


def scoped_sql():
    """SQL over a queued job `j`, its message `m` and conversation `cv`: true unless an operator limited the bot's
    claims to one task (bot_control.claim_task) and this job is about neither that task nor a direct subtask of it.
    Work outside the scope stays queued, untouched, until the scope is released; the scope only narrows what the
    other checks (pause, drain, readiness, the interrupted-work fence, privacy) already allow."""
    task_sql = queued_task_sql()
    return ("NOT EXISTS(SELECT 1 FROM bot_control sc WHERE sc.bot=j.bot AND sc.claim_task IS NOT NULL "
            f"AND coalesce({task_sql},'')<>sc.claim_task AND NOT EXISTS(SELECT 1 FROM task_relations sr "
            f"WHERE sr.from_task={task_sql} AND sr.to_task=sc.claim_task AND sr.kind='parent'))")


def claim_scope(c, bot):
    """The one task `bot`'s claims are limited to, or None (scoped_sql)."""
    row = c.execute("SELECT claim_task FROM bot_control WHERE bot=?", (bot,)).fetchone()
    return row[0] if row else None


def in_scope(c, scope, task_id):
    """Whether `task_id` is `scope` or a direct subtask of it: what a scoped bot may still be given."""
    return bool(task_id) and (task_id == scope or bool(c.execute(
        "SELECT 1 FROM task_relations WHERE from_task=? AND to_task=? AND kind='parent'", (task_id, scope)).fetchone()))


def live_tasks(c):
    """{(bot, task)} with a turn out now. A later wake for that bot about that task waits for the turn even when its
    lane is another one: a task moved under another of the bot's tasks mid-turn keys a new lane (session_root), and
    one task never has two turns of the same bot at once.

    Cached for the read snapshot: Health `_waiting` / `queued_reasons` and claim paths asked once per slow bot.
    Frozen: callers share it.
    """
    from . import read_cache
    values = read_cache.cache(c)
    key = "execution.live_tasks"
    if values is not None and key in values:
        return values[key]
    out = frozenset((r[0], r[1]) for r in c.execute(
        "SELECT a.bot,t.task_id FROM attempts a JOIN turns t ON t.id=a.id "
        "WHERE a.state IN ('leased','running') AND t.task_id IS NOT NULL"))
    if values is not None:
        values[key] = out
    return out


def live_lanes(c):
    """{(bot, lane)} with a leased or running attempt. Claim fences and Health `_waiting` ask once per bot; reuse the
    set for the current read snapshot instead of re-SELECTing and rebuilding it each time. Frozen: callers share it."""
    from . import read_cache
    values = read_cache.cache(c)
    key = "execution.live_lanes"
    if values is not None and key in values:
        return values[key]
    out = frozenset((r[0], r[1]) for r in c.execute(
        "SELECT bot,lane FROM attempts WHERE state IN ('leased','running')"))
    if values is not None:
        values[key] = out
    return out


FOLD_TEXT = 4000           # characters of each one given in the prompt; the bot reads the rest in its room
PRIME_QUEUED = 50          # queued messages a claim or an input poll reads together before checking them one by one


def job_refusal(c, job, followed, ready, busy, lanes, roots, live=frozenset(), *, suspect=None, runner=None):
    """Why the claim passes over one queued job of a bot claim_refusal let through, or None when it hands it out.
    `followed` is the bot's followed config; `busy` the taken (bot, lane) pairs; `live` the (bot, task) pairs with a
    turn out (live_tasks); `runner` the computer's row, whose company subscription signs a check's runtime in."""
    message = H.message(c, job["message_id"])
    if not privacy.message_readable(c, "bot:" + job["bot"], message):
        return "unreadable"
    # The bot's budget and back-off are the bot's own state: an error reading them is shared_check's, and the whole
    # bot is passed over. The checks below depend on this job, so an error in them holds this job alone.
    with shared_check():
        if why := turn_budgets.refusal(c, job["bot"], message) or run_backoff.job_refusal(c, job["bot"], message):
            return why              # over its run budget or waiting out failed runs: except for a person's message
    conversation = H.conversation(c, message["conversation_id"])
    if wake_rule_engine.notice_only(message):
        notice_task = H.task(c, H.message_task_id(message, conversation) or "")
        if notice_task and (H.TS.snoozed(notice_task) or auto_tasks.put_back_held(c, notice_task)):
            return "task_wait"          # a silence control applied after enqueue still wins
    task_id = work_task_id(message, conversation)
    task = H.task(c, task_id) if task_id else None
    refs = message.get("refs") or {}
    if task and task['owner'] == 'bot:' + job['bot'] and auto_tasks.put_back_held(c, task) and not refs.get('run_now'):
        return 'task_wait'
    if task and task['owner'] == 'bot:' + job['bot'] and message['from_actor'] == H.KEEPER and not refs.get("run_now"):
        # A subtask's result (hubdb._deliver_subtask_result) is what the task waits for: its other open subtasks
        # do not hold it back, though a snooze, a person, an approval or a worktree still does.
        if why := auto_tasks.refusal(c, task, followed, woken=True, result=bool(refs.get("subtask")), message=message, suspect=suspect):
            return why
    wanted = check_models.of(c, task_id, job["bot"])
    if wanted and runner is not None:
        # A check runtime this computer's subscription signs in needs no sign-in here (backend/model_subscriptions.py).
        ready = model_subscriptions.covered(ready, model_subscriptions.covered_runtimes(
            c, runner, check_models.apply(followed, wanted)))
    if not check_models.runs_here(ready, wanted) or (wanted and check_models.held(c, task_id)):
        return "check_model"
    if wanted and not message["from_actor"].startswith("human:") and check_models.dropped(c, task_id):
        return "check_model"                # hold_unrunnable cancels it on the scheduler's next tick
    config = check_models.apply(followed, wanted)
    if ((job["bot"], job_lane(c, job["bot"], config, message, conversation, lanes, roots)) in busy
            or (job["bot"], task_id) in live or filer_live(c, message)):
        return "lane_held"
    if (until := handed_back_until(c, job)) and until > H.now():
        return "handed_back"
    return None


def obsolete_notices_sql():
    """FROM and WHERE of the keeper notices a claim cancels, for one runner (?), the keeper (?) and now (?): any queued
    on a task already finished, unless a person's own act caused it (H._write_message: it still owes that person its
    answer), a status check of a quiet task that someone other than Tico has acted on since (it is no longer quiet), a step's
    late warning for a task that has since left that step, news about a step (`at_step`: a verdict's move, a step
    entered) once the task is at another step, unless a person's act caused it,
    a pull request's checks result for a head no open pull request on the task is at any more (a newer push supersedes it),
    the review gate's comment on a refused move once the ticket has changed step since (Tico made the move, or someone
    moved it elsewhere), and a self-filed task's "New task" notice while the task is parked and its filing turn has ended.
    A turn that started and yielded (a drain's time limit, a shared login's hold cap) is never one of them: it is the rest
    of a turn already under way, often the one that finished the task, and cancelling it left the bot's status on "queued
    to continue" with nothing queued (Dev 684). The
    idle claim asks the same question: when it counted notices the claim keeps, every claim from that computer went
    on to the write lock for nothing. It goes from this computer's bots to their queued jobs (jobs_queue): left to
    itself SQLite starts from every keeper notice ever sent, tens of milliseconds on each of a runner's claims."""
    # A held job waits: a wake preset's or a quota reset's until its release, a claim's until its wait ends. The pass
    # takes it once it is free, and the claim then closes the item its hold opened (claim_holds.clear).
    return ("FROM assignments x CROSS JOIN jobs j CROSS JOIN messages m "
            f"JOIN conversations cv ON cv.id=m.conversation_id JOIN tasks t ON t.id={H.MESSAGE_TASK_SQL} "
            f"WHERE x.runner_id=? AND j.bot=x.bot AND j.state='queued' AND {H.unheld_sql()} AND m.id=j.message_id "
            "AND m.from_actor=? AND (m.kind='say' AND json_extract(m.refs_json,'$.review_gate') IS NOT NULL "
            "AND EXISTS (SELECT 1 FROM task_events e WHERE e.task_id=t.id AND e.ts>m.created AND e.field='step') "
            "OR m.kind='notice' "
            "AND ((t.status IN ('done','closed','declined') "
            "AND coalesce(json_extract(m.refs_json,'$.caused_by'),'') NOT LIKE 'human:%') "
            "OR (m.body LIKE 'New task from %' AND m.to_actor=t.owner "
            "AND json_extract(m.refs_json,'$.filed_in') IS NOT NULL "
            "AND (t.status IN ('waiting','blocked') OR t.snoozed_until>?) "
            "AND NOT EXISTS (SELECT 1 FROM attempts fa WHERE fa.id=json_extract(m.refs_json,'$.filed_in') "
            "AND fa.state IN ('leased','running'))) "
            "OR ((json_extract(m.refs_json,'$.wake') IN ('status_check','stalled','step_status') "
            "OR json_extract(m.refs_json,'$.rule')='status_check') AND EXISTS (SELECT 1 FROM task_events "
            "e WHERE e.task_id=t.id AND e.ts>m.created AND e.actor<>m.from_actor)) "
            "OR (json_extract(m.refs_json,'$.step_late') AND EXISTS (SELECT 1 FROM task_events e WHERE e.task_id=t.id "
            "AND e.ts>m.created AND e.field='step')) "
            "OR (json_extract(m.refs_json,'$.at_step') IS NOT NULL AND json_extract(m.refs_json,'$.at_step')<>"
            "coalesce(t.step_id,'') AND coalesce(json_extract(m.refs_json,'$.caused_by'),'') NOT LIKE 'human:%') "
            "OR (json_extract(m.refs_json,'$.wake')='pr_checks_done' AND json_extract(m.refs_json,'$.pr_head') IS NOT NULL "
            "AND EXISTS (SELECT 1 FROM task_links l WHERE l.task_id=t.id AND l.kind='pr' AND coalesce(l.state,'')<>'closed' "
            "AND coalesce(json_extract(l.detail_json,'$.head_sha'),'')<>'') "
            "AND NOT EXISTS (SELECT 1 FROM task_links l WHERE l.task_id=t.id AND l.kind='pr' "
            "AND lower(json_extract(l.detail_json,'$.head_sha'))=lower(json_extract(m.refs_json,'$.pr_head')))))) "
            "AND NOT EXISTS (SELECT 1 FROM job_recovery r WHERE r.job_id=j.id "
            "AND r.attempt_id=j.attempt_id AND r.decision='resume') "
            "AND NOT EXISTS (SELECT 1 FROM attempts ya WHERE ya.id=j.attempt_id AND ya.started IS NOT NULL "
            "AND ya.state='interrupted' AND json_extract(ya.result_json,'$.yielded'))")


HAND_BACK_WAIT = 15          # seconds before a handed-back job is handed out again, doubled for each hand-back
HAND_BACK_MAX = 300


def handed_back_until(c, job):
    """When a job its computer last handed back before starting (Execution.hand_back) may be claimed again, or None.
    A runner claims every quarter second, so without this wait a step that fails fast before every start would spend
    all PRE_START_TRIES in seconds and fail a run over a short hiccup; with it, ten hand-backs take about half an
    hour, as ten lapsed leases used to take about fifteen minutes."""
    last = (c.execute("SELECT state,started,finished,final_text FROM attempts WHERE id=?", (job["attempt_id"],)).fetchone()
            if job["attempt_id"] else None)
    if not last or last["started"] or last["state"] != "expired" or not (last["final_text"] or "").startswith(HANDED_BACK):
        return None
    count = c.execute("SELECT count(*) FROM attempts WHERE job_id=? AND started IS NULL AND state='expired' "
                      "AND substr(final_text,1,?)=?", (job["id"], len(HANDED_BACK), HANDED_BACK)).fetchone()[0]
    return H.shift(last["finished"], seconds=min(HAND_BACK_WAIT * 2 ** max(count - 1, 0), HAND_BACK_MAX))


def quiet_recheck(c, aid, body, task, msg):
    """Whether turn `aid` only looked again at `task`, finished (done or closed) before the turn started, and changed
    nothing Tico counts: no hub write with its token and no push reported with it. Never when a person is owed the
    answer: a task finished during the turn (its answer is the last word in its room), a wake on the always-wake
    list (an approval decided, an answer, a steer, Tico's notice of a person's own act: wake_rules.ALWAYS_WAKE), or a
    person's message, or a notice of their act, folded into the turn (Execution.inputs)."""
    finished = min([t for t in (task.get("done_at"), task.get("closed_at")) if t] or [""])
    row = c.execute("SELECT coalesce(t.writes,0),a.started FROM turns t JOIN attempts a ON a.id=t.id WHERE t.id=?",
                    (aid,)).fetchone()
    if not (task["status"] in ("done", "closed") and bool(finished) and bool(row) and bool(row[1])
            and finished < row[1] and not row[0] and not body.pushes):
        return False
    from . import wake_rules
    refs = msg.get("refs") or {}
    if wake_rules.always_wakes(c, msg["from_actor"], msg["to_actor"], msg["kind"], refs, msg["body"],
                               cause=str(refs.get(wake_rules.CAUSED_BY) or ""), in_reply_to=msg.get("in_reply_to")):
        return False
    return c.execute("SELECT 1 FROM attempt_inputs i JOIN messages m ON m.id=i.message_id WHERE i.attempt_id=? "
                     "AND m.deleted_at IS NULL AND (m.from_actor LIKE 'human:%' "
                     f"OR json_extract(m.refs_json,'$.{wake_rules.CAUSED_BY}') LIKE 'human:%') LIMIT 1",
                     (aid,)).fetchone() is None


# How long a task a bot filed for itself waits for the turn that filed it. Long enough for that turn to finish or
# drop it (on Oct 22 that took 9 s); short enough that work it meant to run beside itself is not held behind a long
# build or a login wait.
FILER_HOLD_S = 300


def filer_live(c, message):
    """Whether this is the first notice of a task its bot filed for itself in a turn still out (refs.filed_in,
    H.task_create), filed under FILER_HOLD_S ago. It starts the task's turn once that turn ends or the hold passes; a
    task the filing turn finished by then is cancelled by the claim's obsolete-notice check instead, so the bot never
    runs twice on one thought (Dev 359)."""
    filed_in = (message.get("refs") or {}).get("filed_in")
    return (bool(filed_in) and message["from_actor"] == H.KEEPER
            and message["created"] > H.shift(H.now(), seconds=-FILER_HOLD_S)
            and c.execute("SELECT 1 FROM attempts WHERE id=? AND state IN ('leased','running')",
                          (filed_in,)).fetchone() is not None)


def bot_refusal(c, settings, runner_id, bot):
    """Why `runner_id`'s next claim would give none of `bot`'s queued work, by the claim's own rules (claim_refusal,
    the interrupted-work fence, job_refusal) and the busy lists the runner sent with its latest claim; None when it
    could take one. For Health, about a bot with no leased or running attempt: the reason of its oldest held job."""
    return bot_claimable(c, settings, runner_id, bot)[0]


RUNNER_SQL = "SELECT * FROM runners WHERE id=?"
CLAIM_CONFIG_SQL = (f"SELECT bc.config_json,{DRAINING_SQL.replace('a.bot', 'bc.bot')} draining "
                    "FROM bot_config bc WHERE bc.bot=?")


def queued_jobs_sql(bots=None):
    """A bot's queued jobs in the order its claim takes them, each with whether the interrupted-work fence and a claim
    scope let it through. With `bots`, the same rows for all of them at once (prime_claimables)."""
    return (f"SELECT j.*,m.from_actor,{unfenced_sql()} free,{scoped_sql()} in_scope FROM jobs j "
            "JOIN messages m ON m.id=j.message_id JOIN conversations cv ON cv.id=m.conversation_id "
            f"WHERE j.bot{'=?' if bots is None else ' IN (' + ','.join('?' * len(bots)) + ')'} "
            "AND j.state='queued' AND j.held=0 "
            "ORDER BY j.redirect DESC,CASE WHEN m.from_actor LIKE 'human:%' THEN 0 ELSE 1 END,"
            f"{HANDOFF_FIRST_SQL},j.created,j.id")


def prime_claimables(c, bots, first=3):
    """What bot_claimable reads for each of these bots with queued work, one table at a time: the computer's row, the
    bot's configuration, its queued jobs and (for the first few of each) their messages, rooms and tasks. `first`
    None reads every queued job's message: Health walks the whole queue (`queued_reasons`), and the first few are
    only enough for the claim's next job. Outside a snapshot nothing is kept."""
    from . import readiness
    from .read_cache import cache, prime, prime_rows
    if cache(c) is None:
        return
    for part in H.chunks(sorted(set(bots))):
        marks = ",".join("?" * len(part))
        found = {r["bot"]: r for r in c.execute(
            f"SELECT bc.bot,bc.config_json,{DRAINING_SQL.replace('a.bot', 'bc.bot')} draining FROM bot_config bc "
            f"WHERE bc.bot IN ({marks})", part)}
        prime(c, CLAIM_CONFIG_SQL, {(s,): found.get(s) for s in part})
        prime(c, readiness.CONFIG_SQL, {(s,): found.get(s) for s in part})
        jobs = {s: [] for s in part}
        for row in c.execute(queued_jobs_sql(part), part):
            jobs[row["bot"]].append(row)
        prime_rows(c, queued_jobs_sql(), {(s,): queue for s, queue in jobs.items()})
        privacy.snapshot(c)
        waiting = [job["message_id"] for queue in jobs.values()
                   for job in (queue if first is None else queue[:first])]
        privacy.prime_queued(c, H.prime_messages(c, waiting).values())
        if first is None:
            prime_job_checks(c, [job for queue in jobs.values() for job in queue])


def prime_job_checks(c, jobs):
    """What job_refusal reads for each of these queued jobs besides its message and task (prime_queued), one query
    a table for all of them: the job's fallback continuation (login_refusal), its task's queue return and step
    (auto_tasks.prime_checks) and check model (check_models.prime). A batch row carries its key after the columns
    the one-row read selects, which are read by position or name only."""
    from .read_cache import cache, prime
    if cache(c) is None or not jobs:
        return
    ids = [job["id"] for job in jobs]
    found = {}
    for part in H.chunks(ids):
        for row in c.execute("SELECT result_json,job_id FROM attempts WHERE job_id IN (%s) "
                             "AND json_extract(result_json,'$.source_continue')=1 ORDER BY created DESC,rowid DESC"
                             % ",".join("?" * len(part)), part):
            found.setdefault(row["job_id"], row)
    prime(c, SOURCE_CONTINUATION_SQL, {(job_id,): found.get(job_id) for job_id in ids})
    tasks, pairs = [], set()
    for job in jobs:
        message = H.message(c, job["message_id"])
        if not message:
            continue
        task_id = work_task_id(message, H.conversation(c, message["conversation_id"]))
        task = H.task(c, task_id) if task_id else None
        tasks.append(task)
        pairs.add((task_id, job["bot"]))
    auto_tasks.prime_checks(c, tasks)
    check_models.prime(c, pairs)


def bot_claimable(c, settings, runner_id, bot, default=None, oldest=False, with_job=False, *, suspect=None):
    """(reason, first, person) for `bot`'s queued work on `runner_id`'s next claim: `reason` as bot_refusal says it;
    otherwise None with `first`, when the job the claim would hand out next was queued, and `person`, True when a
    person sent it. The scan keeps the claim's own order (Execution.candidate: a redirect, then a person's message, then
    a builder hand-off, then the longest waiting), so Health ages the job the claim would take: an old held job beside a young free one is
    neither held nor slow, and neither is an old free job behind a young chat the claim takes first.
    `oldest` diagnoses the oldest job outside a held lane; `with_job` also returns its ID for
    observation aging and message-level authorization. `suspect`: as queued_reasons takes it."""
    def result(reason, first=None, person=None, job=None):
        values = (reason, first, person, job)
        return values if with_job else values[:3]

    # Diagnostics skip a held lane to explain the oldest job that could use another lane.
    from .read_cache import one, rows
    from .shared_bots import follow
    runner = one(c, RUNNER_SQL, (runner_id,))
    row = one(c, CLAIM_CONFIG_SQL, (bot,))
    if not runner:
        return result(None)
    if not row:
        return result("config")            # the claim reads only bots with a configuration
    held_bots, held_lanes = claimed_busy(runner_id)
    ready = readiness_document(runner["readiness_json"])
    why, config = claim_refusal(c, settings, runner, bot, row["config_json"], row["draining"], ready, held_bots,
                                default=default)
    if why:
        if oldest:
            first = min(rows(c, queued_jobs_sql(), (bot,)), key=lambda j: (j["created"], j["id"]), default=None)
            return result(why, first["created"] if first else None, job=first["id"] if first else None)
        return result(why)
    lanes, roots = lanes_on(runner), roots_on(runner)
    busy = set(live_lanes(c)) | held_lanes
    followed, refused = follow(c, bot, config), None     # refused: (created, reason) of the oldest held job
    queue = rows(c, queued_jobs_sql(), (bot,))
    for job in sorted(queue, key=lambda j: (j["created"], j["id"])) if oldest else queue:
        person = str(job["from_actor"]).startswith("human:")
        if not job["in_scope"]:
            why = "scoped"
        elif not job["free"]:
            why = "uncertain"
        elif not private_lanes_on(runner) and private_job(c, job):
            why = "private_update"
        else:
            why = job_refusal(c, job, followed, ready, busy, lanes, roots, suspect=suspect, runner=runner) or (
                "cooling_off" if login_refusal(c, settings, runner_id, bot, followed, job["id"]) else None)
        if why is None or (oldest and why != "lane_held"):
            return result(why, job["created"], person, job["id"])
        if refused is None or job["created"] < refused[0]:
            refused = (job["created"], why, person, job["id"])
    return result(refused[1], refused[0] if oldest else None, refused[2], refused[3]) if refused else result(None)


def _queue_suspects(c, queue):
    """The queued jobs' tasks that auto_tasks.waiting might hold, read as one set (auto_tasks.maybe_waiting)."""
    tasks = []
    for job in queue:
        message = H.message(c, job["message_id"])
        if not message:
            continue
        task_id = work_task_id(message, H.conversation(c, message["conversation_id"]))
        task = H.task(c, task_id) if task_id else None
        if task:
            tasks.append(task)
    suspect = auto_tasks.maybe_waiting(c, tasks)
    auto_tasks.prime_waits(c, [task for task in tasks if task["id"] in suspect])     # each is asked `waiting` next
    return suspect


def queued_reasons(c, settings, runner_id, bot, default=None, *, oldest=False, suspect=None):
    """Each queued job with its own claim reason; common bot fences are read once. `suspect`: the suspects of every
    queue the caller reads at once (_queue_suspects), so a page of many bots reads them once, not a bot at a time."""
    from .read_cache import one, rows
    from .shared_bots import follow
    runner = one(c, RUNNER_SQL, (runner_id,))
    row = one(c, CLAIM_CONFIG_SQL, (bot,))
    if not runner:
        return
    held_bots, held_lanes = claimed_busy(runner_id)
    ready = readiness_document(runner["readiness_json"])
    why, config = (claim_refusal(c, settings, runner, bot, row["config_json"], row["draining"], ready, held_bots,
                                 default=default) if row else ("config", {}))
    queue = rows(c, queued_jobs_sql(), (bot,))
    if oldest:
        queue = sorted(queue, key=lambda j: (j["created"], j["id"]))
    if why:
        for job in queue:
            yield job, why
        return
    lanes, roots = lanes_on(runner), roots_on(runner)
    busy = set(live_lanes(c)) | held_lanes
    followed = follow(c, bot, config)
    live = live_tasks(c)
    suspect = _queue_suspects(c, queue) if suspect is None else suspect
    for job in queue:
        if not job["in_scope"]:
            why = "scoped"
        elif not job["free"]:
            why = "uncertain"
        elif not private_lanes_on(runner) and private_job(c, job):
            why = "private_update"
        else:
            why = job_refusal(c, job, followed, ready, busy, lanes, roots, live, suspect=suspect, runner=runner) or (
                "cooling_off" if login_refusal(c, settings, runner_id, bot, followed, job["id"]) else None)
        yield job, why


class Execution:
    def __init__(self, store, auth):
        self.store, self.auth = store, auth
        self.booted = H.now()
        # Set by create_app: onboarding gives a newly enrolled machine the bots nobody placed.
        self.runner_enrolled = None
        # Set by create_app: a readiness report finishes the go-lives that were waiting for this computer to get a
        # bot's repository (backend/app.py finish_pending_go_live).
        self.readiness_reported = None
        self.served_at = None   # when this process last answered a runner (heartbeat, claim, attempt call)
        # Bot -> (when its claim check may run again, failed checks in a row), for a bot whose check raised. Kept in
        # memory: a restart runs each check once more, which costs one traceback.
        self.check_backoff = {}
        self.stall_lock = threading.Lock()
        self.stall_graces = []
        self.stall_seen = None
        # Attempt id -> {call: (runner id, arrived)}: its runner's renewals and event batches that reached this
        # process and have not landed, either still waiting for the write lock or failed on a busy database.
        self.calls = {}

    def refuse_in_rehearsal(self, what="Runners"):
        """A rehearsal (TICO_REHEARSAL=1) is a copy of real data: a computer that connected would run bots against its
        queue, so no runner enrols, reports in or takes work there (docs/install.md, "Rehearse a migration")."""
        if getattr(self.store.settings, "rehearsal", False):
            raise Problem("rehearsal", what + " are off in a rehearsal: this server is a copy of real data and runs "
                          "no bots", 409)

    def runner(self, c, who):
        self.refuse_in_rehearsal()
        if who.role != "runner":
            raise Problem("identity", "A registered runner is required", 403)
        return c.execute("SELECT * FROM runners WHERE id=? AND revoked_at IS NULL",
                         (who.runner_id,)).fetchone()

    @staticmethod
    def waking(row, now):
        """When the machine's current stretch of unbroken contact began, given its last one.

        `last_seen` is contact of any kind, a heartbeat or a claim, so a silence between two
        wakes is visible whichever call happens to arrive first. NULL is a machine that has
        been here all along: one enrolling now, and every machine already running when this
        column arrived. Only a real silence starts a new stretch.
        """
        if row["last_seen"] and row["last_seen"] <= H.shift(now, seconds=-AWAKE_GAP):
            return now
        return row["awake_since"]

    def readable(self, c, who, conv):
        """Whether this person may read the conversation, not whether they may act on it.

        A leftover keeper-and-bot thread has no person in it, so there is no one's privacy
        to protect and the operator check is the whole authority. A conversation a person
        is in stays theirs.
        """
        if not any(str(p).startswith("human:") for p in conv["participants"]):
            return True
        try:
            self.auth.conversation(c, who, conv["id"])
            return True
        except Problem:
            return False

    @staticmethod
    def awake(awake_since, now):
        """Whether a machine has been reporting in long enough to be given work."""
        return not awake_since or awake_since <= H.shift(now, seconds=-AWAKE_SETTLE)

    def issue_enrollment(self, c, who, body):
        self.refuse_in_rehearsal("Enrolments")
        if who.role not in ("owner", "human"):
            raise Problem("identity", "A person must enroll a machine", 403)
        operator = body.operator or H.actor_id(who.actor)
        if not self.auth.bot_admin(who) and operator != H.actor_id(who.actor):
            raise Problem("forbidden", "You can enroll only your own machines", 403)
        if not H.human(c, operator):
            raise Problem("not_found", "Operator is not on the roster", 404)
        code = secrets.token_urlsafe(32)
        expires = H.shift(H.now(), minutes=15)
        c.execute("INSERT INTO enrollments(code_hash,operator,expires) VALUES(?,?,?)",
                  (digest(code), operator, expires))
        H.event(c, who.actor, "runner.enrollment", operator)
        return {"code": code, "expires": expires, "operator": operator}

    def enroll(self, body):
        self.refuse_in_rehearsal("Enrolments")
        with self.store.transaction() as c:
            row = c.execute("SELECT * FROM enrollments WHERE code_hash=?", (digest(body.code),)).fetchone()
            hosted = c.execute("SELECT label FROM hosting_enrollments WHERE code_hash=?", (digest(body.code),)).fetchone()
            if hosted and hosted['label'] != body.label:
                raise Problem("enrollment", "Enrollment is for another computer", 403)
            if not row or row["expires"] <= H.now():
                raise Problem("enrollment", "Enrollment code is invalid or expired", 401)
            # Deterministic credential makes a lost enrollment response safely retryable.
            token = digest("tico-runner:" + body.code)
            # A code minted for a cloud machine (backend/cloud_machines.py): the computer is named for it whatever label
            # the machine sends, and given to the bots set to run there only: a bot's own is closed to other bots, the
            # shared one takes members' bots.
            from . import cloud_machines
            cloud_bot = cloud_machines.enrolling(c, digest(body.code))
            label = cloud_machines.label_for(c, cloud_bot) if cloud_bot else body.label
            if row["consumed_at"]:
                runner = c.execute("SELECT * FROM runners WHERE id=? AND revoked_at IS NULL",
                                   (row["runner_id"],)).fetchone()
                if not runner or (runner["label"] != body.label and not cloud_bot):
                    raise Problem("enrollment", "Enrollment code was already used", 409)
                return {"runner_id": runner["id"], "token": token, "operator": row["operator"]}
            rid = H.new_id()
            # A new computer takes members' bots, so a team gets going without asking; an admin closes it per
            # computer (Settings > Computers). Its Screen starts at Control when it first offers one
            # (backend/screen.py).
            # Computers that existed before these defaults keep what they had.
            c.execute("INSERT INTO runners(id,label,operator,token_hash,created,platform,accepts_member_bots,"
                      "screen_default_pending) VALUES(?,?,?,?,?,?,?,1)",
                      (rid, label, row["operator"], digest(token), H.now(), body.platform,
                       int(cloud_machines.opens(cloud_bot)) if cloud_bot else 1))
            c.execute("UPDATE enrollments SET consumed_at=?,runner_id=? WHERE code_hash=?",
                      (H.now(), rid, digest(body.code)))
            H.event(c, "human:" + row["operator"], "runner.enrolled", rid)
            if cloud_bot:
                cloud_machines.enrolled(c, self, rid, cloud_bot)    # not onboarding's: only its own bots go on it
            elif self.runner_enrolled:
                self.runner_enrolled(c, rid, row["operator"])
            return {"runner_id": rid, "token": token, "operator": row["operator"]}

    def heartbeat(self, c, who, body, *, live_tasks=None):
        row = self.runner(c, who)
        now = H.now()
        awake_since = self.waking(row, now)
        self.record_contact(now)
        offered_before = screen.available(row)
        stored = (row["version"], row["platform"], row["capacity"], row["readiness_json"], row["capabilities_json"])
        del row
        from .subscriptions import record
        record(c, who.runner_id, body.profiles, body.readiness)
        readiness = readiness_document(body.readiness)
        from .repositories import save_metadata, metadata
        key = 'computer-repositories:' + who.runner_id
        previous = metadata(c, key)
        report = {'repositories': [r.model_dump() for r in body.repositories]} if body.repositories is not None else previous or {'repositories': 'unknown'}
        if report != previous:
            save_metadata(c, key, report)
        # Store what the runner actually reported: optional fields a runner left unset,
        # such as the per-profile rows older runners cannot produce, never enter the record.
        # Routines are the hub's own rows now; what a runner before 0.5.4 read from a
        # repository manifest is accepted and dropped.
        for row in readiness.get('bots', {}).values():
            for key in ('schedules', 'schedule_error', 'routine_revisions', 'routine_preparation_error'):
                row.pop(key, None)
            if not row.get('profile'):
                row.pop('profile', None)
            if row.get('sign_in') == 'unknown':
                row.pop('sign_in', None)
            if row.get('published') is None:
                row.pop('published', None)
            if not row.get('tools'):
                row.pop('tools', None)
            if not row.get('mail_request'):
                row.pop('mail_request', None)
        from .subscriptions import weekly_snapshot
        for row in readiness.get('runtimes', {}).values():
            if not row.get('profiles'):
                row.pop('profiles', None)
            if row.get('authenticated') != 'rejected':
                row.pop('rejected_at', None), row.pop('rejected_reason', None)
            if not row.get('credential_source'):
                row.pop('credential_source', None)
            # The own login's usage windows, checked as a named profile's are: a reset beyond the window is not its.
            for window in ('weekly', 'five_hour'):
                reading = weekly_snapshot(row.get(window), window)
                if reading:
                    row[window] = reading
                else:
                    row.pop(window, None)
        if readiness.get('worktrees') is None:
            readiness.pop('worktrees', None)
        if not readiness.get('harnesses'):
            readiness.pop('harnesses', None)
        if not readiness.get('mail_key'):
            readiness.pop('mail_key', None)
        if not readiness.get('shared_env'):
            readiness.pop('shared_env', None)
        # The platform named at enrollment stands: backend/sql.py decides on it (a heartbeat
        # from a stolen credential must not turn a shared server into a personal Mac).
        # Most beats repeat the last report: then only the contact times move (readiness_at too: the page tells a
        # report from one before a change by it).
        reported = (body.version, stored[1] or body.platform, body.capacity, encode(readiness),
                    encode(sorted(set(body.capabilities))))
        if reported == stored:
            c.execute("UPDATE runners SET last_seen=?,awake_since=?,readiness_at=? WHERE id=?",
                      (now, awake_since, now, who.runner_id))
        else:
            c.execute("UPDATE runners SET last_seen=?,awake_since=?,version=?,platform=coalesce(nullif(platform,''),?),"
                      "capacity=?,readiness_json=?,capabilities_json=?,readiness_at=? WHERE id=?",
                      (now, awake_since, body.version, body.platform, body.capacity, reported[3], reported[4], now,
                       who.runner_id))
            # A login it now reports signed in ends the reconnect cards that waited on it (backend/reconnects.py).
            reconnects.login_report(c, who.runner_id, readiness)
        if not offered_before and screen.CAPABILITY in body.capabilities:
            screen.first_offer(c, who.runner_id)
        from . import bot_tools
        bot_tools.reconcile(c, {bot: row.get('tools') for bot, row in readiness.get('bots', {}).items()})
        if self.readiness_reported:
            self.readiness_reported(c, who.runner_id, readiness)
        if body.release:
            runner_versions.record(c, who.runner_id, body)
        # A restart under way is kept until the heartbeat that no longer carries it: the restarted runner's first. A time
        # ahead of the server's clock reads as now.
        if body.restart:
            since = H.parse_ts(body.restart.since)
            since = since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ") if since and since.tzinfo else now
            c.execute("UPDATE runners SET restart_since=? WHERE id=? AND restart_since IS NULL", (min(since, now), who.runner_id))
        else:
            c.execute("UPDATE runners SET restart_since=NULL WHERE id=? AND restart_since IS NOT NULL", (who.runner_id,))
        if body.checkout:
            # How long it has been behind is the server's to remember: a runner checks every ten minutes.
            checkout = body.checkout.model_dump()
            before = H._json((c.execute("SELECT checkout_json FROM runners WHERE id=?", (who.runner_id,)).fetchone()
                              or {"checkout_json": None})["checkout_json"], {}) or {}
            if checkout["behind"]:
                checkout["behind_since"] = before.get("behind_since") if before.get("behind") else now
            c.execute("UPDATE runners SET checkout_json=? WHERE id=?", (encode(checkout), who.runner_id))
        if body.mail_agent_instructions:
            from .views import roster
            people = roster(c)
            for bot, content in body.mail_agent_instructions.items():
                if not P.inbox_person(bot, people) or len(content) > 100_000:
                    continue
                assigned = c.execute("SELECT 1 FROM assignments WHERE bot=? AND runner_id=?",
                                     (bot, who.runner_id)).fetchone()
                if assigned:
                    c.execute("INSERT INTO mail_agent_instructions(bot,content,runner_id,updated) "
                              "VALUES(?,?,?,?) ON CONFLICT(bot) DO UPDATE SET "
                              "content=excluded.content,runner_id=excluded.runner_id,"
                              "updated=excluded.updated WHERE "
                              "mail_agent_instructions.content<>excluded.content OR "
                              "mail_agent_instructions.runner_id<>excluded.runner_id",
                              (bot, content, who.runner_id, now))
        for bot, content in body.agent_instructions.items():
            if len(content) > 100_000:
                continue
            assigned = c.execute("SELECT 1 FROM assignments WHERE bot=? AND runner_id=?",
                                 (bot, who.runner_id)).fetchone()
            if assigned:
                c.execute("INSERT INTO bot_agent_instructions(bot,content,runner_id,updated) "
                          "VALUES(?,?,?,?) ON CONFLICT(bot) DO UPDATE SET "
                          "content=excluded.content,runner_id=excluded.runner_id,"
                          "updated=excluded.updated WHERE "
                          "bot_agent_instructions.content<>excluded.content OR "
                          "bot_agent_instructions.runner_id<>excluded.runner_id",
                          (bot, content, who.runner_id, now))
        from . import worktrees
        worktree_actions = worktrees.heartbeat(c, who, body.worktrees, readiness.get('worktrees', False),
                                               self.store.settings.github_owner, live_tasks=live_tasks)
        # A worktree whose task neither this computer nor its owning bot here can read is left out and counted, so one
        # such task cannot fail the whole readiness report (task_privacy.require_payload refuses the response).
        sendable = privacy.readable_worktree_actions(c, who, worktree_actions)
        skipped = len(worktree_actions) - len(sendable)
        # A Restart a person pressed goes to the runner once; it restarts when no turn is running.
        restart = c.execute("SELECT restart_requested,performance,turns_at_once FROM runners WHERE id=?",
                            (who.runner_id,)).fetchone()
        if restart and restart["restart_requested"]:
            c.execute("UPDATE runners SET restart_requested=NULL WHERE id=?", (who.runner_id,))
        # The assignments are read after the commit (app.py heartbeat route), off the write lock.
        return {"server_time": H.now(), "start_places": True, "runtime_credential_source": True, "subscription_accounts": True, "worktree_record_stamp": True, "worktree_actions": sendable,
                # Whether the runner samples its performance (backend/performance.py).
                "performance": bool(restart) and restart["performance"] != "off",
                # Whether this computer's owner shares its internet with bots: the runner holds its exit link open
                # (backend/browser_exit.py).
                "browser_exit": browser_exit.sharing(c, who.runner_id),
                # The bots here with an exit set: the runner stops the proxies of the rest.
                "browser_exit_bots": browser_exit.bots_on(c, who.runner_id),
                # The owner's "Turns at once" (null: automatic). Only a runner with turns_at_once_v1 takes a raised one.
                "turns_at_once": restart["turns_at_once"] if restart else None,
                **({"worktree_actions_skipped": skipped} if skipped else {}),
                **({"restart": True} if restart and restart["restart_requested"] else {})}

    def assigned(self, c, who):
        runner = self.runner(c, who)
        # The environment owner hosts any bot; every other operator hosts only their own.
        owner = self.auth.owner_id(c)
        rows = c.execute("SELECT a.*,b.state,bc.config_json,bc.operator,bc.onboarding_state FROM assignments a "
                         "JOIN bots b ON b.slug=a.bot JOIN bot_config bc ON bc.bot=a.bot "
                         "WHERE a.runner_id=? ORDER BY a.bot", (who.runner_id,)).fetchall()
        result = []
        from .views import roster
        people = roster(c)
        # The runner gets a concrete runtime and model; a bot that names none runs on the
        # company default, so changing the default moves it without editing the bot.
        company = providers.load(c, self.store.settings)
        from .shared_bots import _json, follow, source_of
        # Every hosted bot's configuration and its original's, read once (shared_bots.follow, bot_repository).
        configs = {}

        def load(slugs):
            for part in H.chunks(slugs):
                configs.update((r["bot"], r) for r in c.execute(
                    "SELECT bot,config_json,repo,created_by FROM bot_config WHERE bot IN (%s)" % ",".join("?" * len(part)), part))
        load([row["bot"] for row in rows])
        load(sorted({source_of(_json(r["config_json"])) for r in configs.values()} - {""} - set(configs)))
        app, creators = (_github_app(c),), {}
        for row in rows:
            takes = runner['accepts_member_bots'] and self.auth.member_bot(c, row['bot'], configs.get(row['bot']), creators)
            if row['operator'] != runner['operator'] and runner['operator'] != owner and not takes:
                continue
            config = app_delivery.job_config(providers.fill(company, follow(
                c, row['bot'], json.loads(row['config_json']), configs.get)))
            result.append({**dict(row), 'computer_label': runner['label'], 'computer_logins_v1': True,
                           'config': config,
                           'repository': bot_repository(c, self.store.settings, row['bot'], configs.get, app),
                           'mail_agent': bool(P.inbox_person(row['bot'], people)),
                           # The runtimes its own or its team's subscription signs in: no sign-in needed here for them.
                           **model_subscriptions.assignment_fields(c, runner, config)})
        return result

    def assign(self, c, who, bot, body):
        from .shared_bots import check_runner
        check_runner(c, bot, body.runner_id)
        if not (self.auth.operator(c, who, bot) or self.auth.bot_manager(c, who, bot)):
            raise Problem("forbidden", "You do not manage this bot's machines", 403)
        from .agents import external_harness
        if external_harness(c, bot):
            raise Problem("harness", "This bot is run by an external agent; it has no computer to "
                          "assign. Give it an agent credential in Settings instead", 422)
        runner = c.execute("SELECT * FROM runners WHERE id=? AND revoked_at IS NULL",
                           (body.runner_id,)).fetchone()
        if not runner:
            raise Problem("not_found", "Computer is not registered", 404)
        operator = c.execute("SELECT operator FROM bot_config WHERE bot=?", (bot,)).fetchone()[0]
        member_bot = self.auth.member_bot(c, bot)
        if (member_bot and not runner["accepts_member_bots"] and runner["operator"] != operator
                and not self.auth.bot_admin(who)):
            # A member's bot goes on its own operator's computer, or on one an admin has opened to members' bots:
            # never on another member's.
            raise Problem("computer_closed", "That computer does not take bots members create. Ask an admin to place "
                          "this bot, or to let the computer accept members' bots (Settings > Computers)", 409)
        # A computer hosts its operator's bots and the owner's; a member's bot may also go on a computer an
        # admin has opened to members' bots, and an admin may put a member's bot on any computer.
        if (runner["operator"] not in (operator, self.auth.owner_id(c))
                and not (member_bot and (runner["accepts_member_bots"] or self.auth.bot_admin(who)))):
            raise Problem("forbidden", "This machine's operator is not authorized to host this bot", 403)
        from . import cloud_machines
        cloud_machines.check_host(c, bot, body.runner_id)
        old = c.execute("SELECT * FROM assignments WHERE bot=?", (bot,)).fetchone()
        generation = old["generation"] if old else 0
        if body.expected_generation != generation:
            raise Problem("version_conflict", "Assignment changed; refresh before transferring", 409)
        active = c.execute("SELECT * FROM attempts WHERE bot=? AND state IN ('leased','running') "
                           "AND lease_until>?", (bot, H.now())).fetchone()
        if active:
            raise Problem("busy", "Drain the current run or wait for its lease to expire before transfer", 409)
        if not old or old["runner_id"] != body.runner_id:
            from .views import roster
            inbox_isolation.check(c, bot, body.runner_id, roster(c))
            if old and (blocked := stranded(c, self.store.settings, bot, old["runner_id"], body.runner_id)):
                raise Problem("repository_unpublished", blocked, 409)
        self.expire(c)
        c.execute("INSERT INTO assignments VALUES(?,?,?,?,?) ON CONFLICT(bot) DO UPDATE SET "
                  "runner_id=excluded.runner_id,generation=excluded.generation,updated=excluded.updated,"
                  "updated_by=excluded.updated_by",
                  (bot, body.runner_id, generation + 1, H.now(), who.actor))
        if not old or old["runner_id"] != body.runner_id:
            from .model_login import hold_removals
            hold_removals(c, self.store.settings, {bot})
            # Another computer is another login: the limit the bot showed was the old one's.
            lift_after_connection_change(c, self.store.settings, who.actor, bot)
        H.event(c, who.actor, "bot.assigned", bot, {"runner": body.runner_id, "generation": generation + 1})
        from . import built_on
        built_on.forget(c, bot)                   # placed: its computer reports the checkout from now on
        if old and old["runner_id"] != body.runner_id:
            forget_report(c, old["runner_id"], bot)
        cloud_machines.placed(c, bot, body.runner_id, who.actor)
        return dict(c.execute("SELECT * FROM assignments WHERE bot=?", (bot,)).fetchone())

    def grace_leases(self):
        """After a restart, leases that lapsed while the API was away get one more lease
        period: the runner may well still be working, and a truly gone one expires exactly
        as before, one period later."""
        with self.store.transaction() as c:
            until = H.shift(H.now(), seconds=self.store.settings.lease_seconds)
            rows = c.execute("SELECT id,bot,lease_until FROM attempts WHERE state IN ('leased','running') AND lease_until<?",
                             (H.now(),)).fetchall()
            for row in rows:
                with isolated(c, "grace_leases", row["id"]):
                    c.execute("UPDATE attempts SET lease_until=? WHERE id=?", (until, row["id"]))
                    H.event(c, H.KEEPER, "attempt.lease-grace", row["id"],
                            {"bot": row["bot"], "lapsed": row["lease_until"], "lease_until": until})
            return len(rows)

    def stalled(self, now):
        """True when this server has answered no runner for more than half a lease but less
        than STALL_MAX: a lease lapsing in that window is the server's doing, not the runner's,
        which is still renewing. A longer silence is a runner that is gone."""
        if not self.served_at:
            return False
        gap = (H.parse_ts(now) - H.parse_ts(self.served_at)).total_seconds()
        return self.store.settings.lease_seconds / 2 < gap <= STALL_MAX

    def stall_recovery(self, now):
        """Keep the original cutoff and deadline while bounded sweeps drain a stalled fleet.

        Contact may resume between chunks. An attempt that already received lease grace
        cannot receive stall grace again, and an undrained cohort expires at its fixed deadline.
        """
        with self.stall_lock:
            self.stall_graces = [g for g in self.stall_graces if g[2] > now]
            if self.stalled(now) and self.served_at != self.stall_seen:
                self.stall_seen = self.served_at
                self.stall_graces.append((self.served_at, now,
                                          H.shift(now, seconds=self.store.settings.lease_seconds)))
            return tuple(self.stall_graces)

    def record_contact(self, now):
        with self.stall_lock:
            self.served_at = now

    @contextmanager
    def calling(self, who, aid):
        """A runner's renewal or event batch for this attempt, from the moment it reached the server; yields that
        time. On a slow server the call can wait for the write lock longer than the lease itself: v0.4.14 held it
        for up to a minute and ended live turns "Computer lost" (Dev 612). Until the call lands, `expire` keeps the
        lease, and `renew` judges the lease by the call's arrival. A refusal (not the runner's, or a lease really
        lost) forgets it at once; a busy database's failure stays until the lease would have run out anyway."""
        arrived = H.now()
        if who.role != "runner" or not who.runner_id:
            yield arrived
            return
        call = object()
        with self.stall_lock:
            if sum(map(len, self.calls.values())) >= 4096:
                cutoff = H.shift(arrived, seconds=-self.store.settings.lease_seconds)
                for other in list(self.calls):
                    kept = {t: x for t, x in self.calls[other].items() if x[1] > cutoff}
                    if kept:
                        self.calls[other] = kept
                    else:
                        del self.calls[other]
            self.calls.setdefault(aid, {})[call] = (who.runner_id, arrived)
        try:
            yield arrived
        except Problem as e:
            if not e.retryable:
                self._landed(aid, call)
            raise
        else:
            self._landed(aid, call)

    def _landed(self, aid, call):
        with self.stall_lock:
            waiting = self.calls.get(aid, {})
            waiting.pop(call, None)
            if not waiting:
                self.calls.pop(aid, None)

    def still_calling(self, row, now):
        """When the latest call from this attempt's own runner that has not landed arrived, if within one lease."""
        cutoff = H.shift(now, seconds=-self.store.settings.lease_seconds)
        with self.stall_lock:
            times = [at for runner_id, at in self.calls.get(row["id"], {}).values()
                     if runner_id == row["runner_id"] and at > cutoff]
        return max(times) if times else None

    def expire(self, c, *, attempt_ids=None, limit=None):
        now = H.now()
        overdue = set(start_places.overdue(c))
        # The existing indexed event is committed atomically with its extension, so rollback
        # cannot consume grace and a later contact cohort cannot extend the same attempt twice.
        from .run_diagnostics import CAP_EVENT
        rows = c.execute("SELECT a.*,EXISTS(SELECT 1 FROM events e WHERE e.target=a.id "
                         "AND e.action='attempt.lease-grace') AS already_graced,"
                         "EXISTS(SELECT 1 FROM events e WHERE e.target=a.id AND e.action=?) AS capped "
                         "FROM attempts a WHERE state IN ('leased','running') AND (lease_until<=?" +
                         (" OR id IN (" + ','.join('?' * len(overdue)) + ")" if overdue else "") + ")" +
                         (" AND id IN (" + ','.join('?' * len(attempt_ids)) + ")" if attempt_ids else "") +
                         " ORDER BY lease_until,id" + (" LIMIT ?" if limit is not None else ""),
                         (CAP_EVENT, now, *overdue, *(attempt_ids or ()), *((limit,) if limit is not None else ()))).fetchall()
        # Contact alone does not enroll future work in a grace cohort: expiration must
        # first observe overdue leases, as it did before maintenance was chunked.
        graces = self.stall_recovery(now) if any(row["id"] not in overdue for row in rows) else ()
        for row in rows:
            with isolated(c, "expire", row["id"]):
                # Its runner's call arrived and waits for the lock: the lease lapsed on our side, not the runner's.
                # A run the server capped is not extended by a call in flight or by stall grace (run_diagnostics.cap).
                heard = None if row["id"] in overdue or row["capped"] else self.still_calling(row, now)
                if heard:
                    until = H.shift(heard, seconds=self.store.settings.lease_seconds)
                    c.execute("UPDATE attempts SET lease_until=? WHERE id=?", (until, row["id"]))
                    H.event(c, H.KEEPER, "attempt.lease-held", row["id"],
                            {"bot": row["bot"], "lapsed": row["lease_until"], "heard_at": heard, "lease_until": until})
                    continue
                grace = next((g for g in graces if row["lease_until"] <= g[1]), None)
                if grace and not row["already_graced"] and row["id"] not in overdue and not row["capped"]:
                    served_at, _, until = grace
                    c.execute("UPDATE attempts SET lease_until=? WHERE id=?", (until, row["id"]))
                    H.event(c, H.KEEPER, "attempt.lease-grace", row["id"],
                            {"bot": row["bot"], "lapsed": row["lease_until"], "lease_until": until,
                             "served_at": served_at})
                    continue
                # A start that reported a tool call or a push did run something: like a running turn, it may have
                # pushed, sent or paid, so it is held for a check (auto_reconcile), never put back in line blindly.
                acted = row["state"] == "leased" and c.execute(
                    "SELECT 1 FROM attempt_events WHERE attempt_id=? AND kind='tool' UNION ALL "
                    "SELECT 1 FROM turns WHERE id=? AND writes>0 LIMIT 1", (row["id"], row["id"])).fetchone()
                retry_state = state = "queued" if row["state"] == "leased" and not acted else "uncertain"
                jobs = c.execute("SELECT j.id,j.message_id,t.status AS task_status FROM jobs j "
                                 "JOIN messages m ON m.id=j.message_id JOIN conversations cv ON cv.id=m.conversation_id "
                                 f"LEFT JOIN tasks t ON t.id={H.MESSAGE_TASK_SQL} "
                                 "WHERE j.attempt_id=? AND j.state IN ('leased','running','input')", (row["id"],)).fetchall()
                # A lost lease may retry open work, but cannot undo Stop or resurrect a finished task.
                # Inputs folded into a running turn have their own jobs: Stop applies only to its original request.
                # A quarantine's Stop is not the person's choice to drop the request: a run safe to repeat goes back in
                # the queue to run when the quarantine lifts, as it does when the runner hears that Stop (Dev 573). Any
                # other run is held for review like a lost one: running it again could repeat an outside action.
                benched = quarantine_stops.stopped_by(c, row)
                held_back = bool(benched) and not quarantine_stops.requeues(c, row)
                if benched:
                    retry_state = state = "uncertain" if held_back else "queued"
                cancelled = {job["id"] for job in jobs if (row["stop_requested"] and job["id"] == row["job_id"] and not benched)
                             or job["task_status"] in ("done", "closed", "declined")}
                if row["job_id"] in cancelled:
                    state = "cancelled"
                c.execute("UPDATE attempts SET state='expired',finished=? WHERE id=?", (H.now(), row["id"]))
                from .performance import stamp_attempt
                stamp_attempt(c, row["id"], row["runner_id"], "end")
                gateway_connection.forget(c, row["id"])
                if row["state"] == "leased" and state == "queued" and not benched:
                    tries = c.execute("SELECT count(*) FROM attempts WHERE job_id=? AND started IS NULL "
                                      "AND state='expired' AND finished>=?", (row["job_id"], self.booted)).fetchone()[0]
                    if tries >= PRE_START_TRIES:
                        computer = c.execute("SELECT label FROM runners WHERE id=?", (row["runner_id"],)).fetchone()
                        label = (computer["label"] if computer else None) or "its Computer"
                        bot = H.bot(c, row["bot"])
                        name = (self.store.settings.assistant_name if row["bot"] == self.store.settings.assistant_bot
                                else (bot["display_name"] if bot else None)) or row["bot"]
                        version = c.execute("SELECT release FROM runner_versions WHERE runner_id=?",
                                            (row["runner_id"],)).fetchone()
                        remedy = ("update its Tico" if version and runner_versions.incompatible(version["release"])
                                  else "check that Computer")
                        reason = f"Your {name} couldn't start on {label}; {remedy}"
                        state = "failed"
                        c.execute("UPDATE attempts SET state='failed',final_text=? WHERE id=?", (reason, row["id"]))
                        job = c.execute("SELECT message_id FROM jobs WHERE id=? AND attempt_id=? "
                                        "AND state='leased'", (row["job_id"], row["id"])).fetchone()
                        if job:
                            msg = H.message(c, job["message_id"])
                            if H.is_human(msg["from_actor"]):
                                H.say(c, "bot:" + row["bot"] if bot else H.KEEPER, msg["from_actor"], reason, kind="notice",
                                      conversation_id=msg["conversation_id"], in_reply_to=msg["id"],
                                      refs={"turn_id": row["id"]})
                for job in jobs:
                    job_state = "cancelled" if job["id"] in cancelled else retry_state if state == "cancelled" else state
                    c.execute("UPDATE jobs SET state=? WHERE id=?", (job_state, job["id"]))
                H.turn_finish(c, H.KEEPER, row["id"], exit_code="failed" if state == "failed" else "interrupted",
                              summary=reason if state == "failed" else "Runner lease expired", usage_reason="no_result")
                if row["stop_requested"]:
                    job = c.execute("SELECT m.conversation_id FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.id=?",
                                    (row["job_id"],)).fetchone()
                    if job:
                        self.stopped(c, row, job["conversation_id"], held=held_back)
                elif state == "cancelled":
                    settle_status(c, row["bot"], "idle", "", claimed=row["created"])
                elif row["state"] == "leased" and state == "queued":
                    settle_status(c, row["bot"], "idle", "Last run didn't start; it is back in the queue",
                                  claimed=row["created"], stop=row["id"])
                elif state == 'uncertain' and jobs and retry_facts(c, {
                        **dict(next((j for j in jobs if j['id'] == row['job_id']), jobs[0])),
                        'bot': row['bot'], 'attempt_id': row['id'], 'created': row['created']
                }, row['started'], H.now())['safe']:
                    settle_status(c, row['bot'], 'idle', 'Resuming after a lost lease; waiting for its computer',
                                  claimed=row['created'], stop=row['id'])
                elif H.bot(c, row["bot"]):
                    settle_status(c, row["bot"], "crashed",
                                  reason if state == "failed" else "One run stopped; saved for later",
                                  claimed=row["created"], stop=row["id"])
                H.event(c, H.KEEPER, "attempt.expired", row["id"], {"job_state": state})

    def settle_unstarted(self, c, row, summary, event="attempt.expired"):
        """A run its computer took and never started, its attempt already `expired`: its lease lapsed, or its
        computer handed it back (hand_back). Nothing ran, so nothing is held for review and the bot is not crashed:
        the job waits in the queue again and the bot's status line says so softly (last_stop). After PRE_START_TRIES
        such runs since this server started the job fails instead, with a reason that names the computer, told to
        the person who asked: a broken computer must not take it forever."""
        state, reason = "queued", ""
        tries = c.execute("SELECT count(*) FROM attempts WHERE job_id=? AND started IS NULL "
                          "AND state='expired' AND finished>=?", (row["job_id"], self.booted)).fetchone()[0]
        bot = H.bot(c, row["bot"])
        if tries >= PRE_START_TRIES:
            computer = c.execute("SELECT label FROM runners WHERE id=?", (row["runner_id"],)).fetchone()
            label = (computer["label"] if computer else None) or "its Computer"
            name = (self.store.settings.assistant_name if row["bot"] == self.store.settings.assistant_bot
                    else (bot["display_name"] if bot else None)) or row["bot"]
            version = c.execute("SELECT release FROM runner_versions WHERE runner_id=?",
                                (row["runner_id"],)).fetchone()
            remedy = ("update its Tico" if version and runner_versions.incompatible(version["release"])
                      else "check that Computer")
            reason = f"Your {name} couldn't start on {label}; {remedy}"
            state = "failed"
            c.execute("UPDATE attempts SET state='failed',final_text=? WHERE id=?", (reason, row["id"]))
            job = c.execute("SELECT message_id FROM jobs WHERE id=? AND attempt_id=? "
                            "AND state='leased'", (row["job_id"], row["id"])).fetchone()
            if job:
                msg = H.message(c, job["message_id"])
                if H.is_human(msg["from_actor"]):
                    H.say(c, "bot:" + row["bot"] if bot else H.KEEPER, msg["from_actor"], reason, kind="notice",
                          conversation_id=msg["conversation_id"], in_reply_to=msg["id"],
                          refs={"turn_id": row["id"]})
        c.execute("UPDATE jobs SET state=? WHERE attempt_id=? AND state IN ('leased','running','input')", (state, row["id"]))
        H.turn_finish(c, H.KEEPER, row["id"], exit_code="failed" if state == "failed" else "interrupted",
                      summary=reason if state == "failed" else summary)
        if bot:
            settle_status(c, row["bot"], "crashed" if state == "failed" else "idle",
                          reason if state == "failed" else "Last run didn't start; it is back in the queue",
                          claimed=row["created"], stop=row["id"])
        H.event(c, H.KEEPER, event, row["id"], {"bot": row["bot"], "job_id": row["job_id"], "job_state": state})
        return state

    def hand_back(self, c, row, body):
        """A run its computer gave up on before starting it (it could not set the run up or reach Tico, or a person
        stopped it first) comes back now, not 90 seconds later when its lease lapses: on Oct 22 BotOps's runner lost
        Tico for half a minute before a start, its report was refused as not started, and the lapse showed BotOps as
        crashed though nothing had run."""
        self.release_undelivered(c, row["id"], body.undelivered)
        now = H.now()
        c.execute("UPDATE attempts SET state='expired',finished=?,lease_until=?,final_text=? WHERE id=?",
                  (now, now, HANDED_BACK + ".", row["id"]))
        gateway_connection.forget(c, row["id"])
        state = self.settle_unstarted(c, row, HANDED_BACK, event="attempt.returned")
        return {"attempt_id": row["id"], "outcome": body.outcome, "job_state": state, "message": None}

    @staticmethod
    def mark_rejected(c, runner_id, runtime, reason, profile=None, bot=""):
        """Record the refusal now; the runner's next heartbeat repeats it and later lifts it. On a bot-keyed runtime with
        no profile (pi, the Gemini CLI) the refused key is `bot`'s own, so only that bot is marked and the runtime's row
        is left as the runner reports it."""
        record = c.execute("SELECT readiness_json FROM runners WHERE id=?", (runner_id,)).fetchone()
        ready = readiness_document(record[0] if record else None)
        row = (ready.get("runtimes") or {}).get(runtime)
        if not isinstance(row, dict):
            return
        own = bool(bot) and not profile and bot_keyed(runtime)
        if profile or own:
            row = {}
        row.update(authenticated="rejected", rejected_at=H.now(), rejected_reason=reason[:300],
                   detail=("Sign-in rejected: " + reason)[:500])
        for name, entry in ready.get("bots", {}).items():
            if not isinstance(entry, dict) or entry.get("runtime") != runtime:
                continue
            if own:
                if name != bot:
                    continue
            elif entry.get("profile", "") != (profile or ""):
                continue
            entry["sign_in"] = "rejected"
            entry["ready"] = False
            entry["problems"] = [row["detail"]]
        if profile:
            report = c.execute("SELECT runtimes_json FROM computer_profiles WHERE runner_id=? AND profile=?",
                               (runner_id, profile)).fetchone()
            if report:
                runtimes = json.loads(report[0] or "{}")
                runtimes[runtime] = {"signed_in": False}
                c.execute("UPDATE computer_profiles SET runtimes_json=?,updated=? WHERE runner_id=? AND profile=?",
                          (json.dumps(runtimes), H.now(), runner_id, profile))
        c.execute("UPDATE runners SET readiness_json=? WHERE id=?", (json.dumps(ready), runner_id))

    def candidate(self, c, who, body, runner, *, job_id=None, people_only=False, failed=None, skip=()):
        """The queued job this runner would be given next, or None. Reads only. `people_only`: only a person's own
        message (start_places.from_person), on a computer that holds every other start. `failed`: the claim's
        ClaimFaults, which takes each job whose checks raise and moves on to the next candidate, passes over the bot of
        a bot-wide check that raises, and stops picking once CLAIM_HOLDS jobs are held; without it the error
        propagates. `skip`: the job ids a claim has already dropped, which are passed over."""
        lanes, roots, continues = lanes_on(runner), roots_on(runner), capacity_continue_on(runner)
        private_lanes = private_lanes_on(runner)
        # Taken lanes, each "bot|lane": the server's active attempts and the runner's own busy list. A runner
        # without lanes runs one turn per bot, so any active attempt takes the whole bot.
        busy = set(live_lanes(c))
        busy_bots = {bot for bot, _ in busy} if not lanes else set()
        mine = {tuple(entry.split("|", 1)) for entry in (body.busy_lanes or ()) if "|" in entry}
        busy |= mine
        # Prefer the bot holding the fewest real places; waiting workers still fence their lanes,
        # but neither occupy a place nor impose a per-bot cap.
        held = turns_here(c, runner["id"])
        followed = {}
        ready = readiness_document(runner["readiness_json"])
        # Unconfirmed effects hold the interrupted conversation and task until review. Other
        # tasks and routines for the same bot can continue. A person's unrelated chat also
        # remains claimable so an interrupted routine cannot silence its owner.
        default = usage_limits.company(c)
        from .subscriptions import context
        from .shared_bots import follow
        subscription_context = context(c)
        held_bots = set(body.busy_bots or ()) | busy_bots
        bots = []
        # A selected-job recheck must not classify unrelated queued bots while
        # holding the writer. Resolve its current bot, state and hold here.
        queued = ("a.bot=(SELECT bot FROM jobs WHERE id=? AND state='queued' AND held=0)" if job_id is not None else
                  "EXISTS(SELECT 1 FROM jobs j WHERE j.bot=a.bot AND j.state='queued' AND j.held=0)")
        for row in c.execute(f"SELECT a.bot,bc.config_json,{DRAINING_SQL} draining FROM assignments a "
                             "JOIN bots b ON b.slug=a.bot JOIN bot_config bc ON bc.bot=a.bot "
                             "WHERE a.runner_id=? AND b.state='active' AND (? IS NULL OR a.bot=?) "
                             f"AND {queued}",
                             (who.runner_id, body.bot, body.bot, *((job_id,) if job_id is not None else ()))).fetchall():
            bot = row["bot"]
            if failed is not None and bot in failed.bots:
                continue            # its check raised earlier in this claim: passed over for the rest of it
            if self.check_waiting(bot):
                continue            # its check raised and its back-off has not ended: the check does not run yet
            try:
                with shared_check():
                    why, config = claim_refusal(c, self.store.settings, runner, bot, row["config_json"],
                                                row["draining"], ready, held_bots, subscription_context, default)
                    if why:
                        continue
                    followed_bot = follow(c, bot, config)
            except Exception as exc:
                # A check of one bot (its back-off or budget) that raises is that bot's alone: the claim passes it over.
                if failed is None or storage_error(exc) or shared_scope(exc) == "team":
                    raise
                failed.bot(bot, exc)
                continue
            bots.append(bot)
            followed[bot] = followed_bot
        if not bots:
            return None
        marks = ",".join("?" for _ in bots)
        live = live_tasks(c)
        # Readiness, spend and cooldown are checked once per bot, never once per queued job.
        # Keep human chat priority and interrupted-task/conversation fencing in the query.
        candidates = c.execute(
            "SELECT j.*,a.generation FROM jobs j JOIN assignments a ON a.bot=j.bot "
            "JOIN messages m ON m.id=j.message_id JOIN conversations cv ON cv.id=m.conversation_id "
            f"LEFT JOIN tasks qt ON qt.id={H.MESSAGE_TASK_SQL} "
            # A job its wake preset holds waits for its release (backend/wake_presets.py tick).
            f"WHERE j.state='queued' AND j.held=0 AND j.bot IN ({marks}) AND a.runner_id=? "
            # An onboarding bot takes a person's message and its manager's, nothing else (backend/lifecycle.py).
            "AND (m.from_actor LIKE 'human:%' OR NOT EXISTS(SELECT 1 FROM bot_config pc "
            "WHERE pc.bot=j.bot AND pc.onboarding_state IN " + PARKED_SQL + " "
            "AND m.from_actor<>'bot:'||coalesce(pc.reports_to,''))) "
            f"AND {unfenced_sql()} AND {scoped_sql()} " + ("AND j.id=? " if job_id is not None else "") +
            ("AND m.from_actor LIKE 'human:%' " if people_only else "") +
            # A redirect first (it replaces a turn just stopped); then a person's chat; then the bot holding the
            # fewest turns here, so a freed turn goes to another bot's waiting job before the busy bot's next one;
            # then a builder hand-off (backend/verdict_moves.py); then task rank, then the longest waiting.
            "ORDER BY j.redirect DESC,CASE WHEN m.from_actor LIKE 'human:%' THEN 0 ELSE 1 END,"
            f"CASE j.bot {' '.join('WHEN ? THEN ?' for _ in bots)} ELSE 0 END,{HANDOFF_FIRST_SQL},"
            "(qt.rank IS NULL),qt.rank,j.created,j.id",
            (*bots, who.runner_id, *((job_id,) if job_id is not None else ()),
             *(v for bot in bots for v in (bot, held.get(bot, 0)))))
        candidates = candidates.fetchall()
        for i, row in enumerate(candidates):
            if failed is not None and failed.full():
                break               # CLAIM_HOLDS jobs are held by this claim: it picks no more
            if row["id"] in skip or (failed is not None and row["bot"] in failed.bots):
                continue
            if i % PRIME_QUEUED == 0 and len(candidates) > 1:
                # A backlog the claim passes over (lane held, check model, budget) is checked again on every claim, a
                # few a second per computer: the next jobs' messages, rooms and tasks are read together, not one by one.
                privacy.snapshot(c)
                part = candidates[i:i + PRIME_QUEUED]
                privacy.prime_queued(c, H.prime_messages(c, [job["message_id"] for job in part]).values())
            try:
                if source_continuation(c, row["id"]) and (
                        SOURCE_CONTINUE not in _capability_set(runner["capabilities_json"])):
                    continue        # old strict-primary runners cannot route a saved fallback
                if not continues and continuation_thread(c, row["id"]):
                    continue        # left queued for a runner that continues its thread: this one would start it afresh
                if not private_lanes and private_job(c, row):
                    continue        # a private meeting's turn waits for a runner that keeps it out of the bot's checkout
                why = job_refusal(c, row, followed[row["bot"]], ready, busy, lanes, roots, live, runner=runner)
                if why != "unreadable":
                    # The bot's checks ran for this job without raising: the back-off their last failure started ends.
                    self.check_backoff.pop(row["bot"], None)
                if why is None:
                    # The job's own login hold: an error reading it holds this job alone. The bot's login as a whole is
                    # checked once per bot, in claim_refusal, where an error passes the bot over.
                    waiting = login_refusal(c, self.store.settings, runner["id"], row["bot"], followed[row["bot"]],
                                            row["id"], subscription_context)
                    if not waiting:
                        return row
            except Exception as exc:
                # Storage errors and a team's check fail the claim. A bot's check is not this job's: the claim passes
                # the bot over, and its other runs wait for the check. This job's own error is logged once, and the
                # claim holds the job, so no other claim evaluates it again.
                scope = shared_scope(exc)
                if failed is None or storage_error(exc) or scope == "team":
                    raise
                if scope:
                    failed.bot(row["bot"], exc)
                    continue
                report("claim", row["id"], exc)
                failed.job(row["id"], type(exc).__name__)
        return None

    def pick(self, c, who, body, runner, now, *, people_only=False, failed, passed):
        """The next candidate a claim may hand out, or None. A job whose checks raise is held, and an obsolete notice
        (obsolete_notice) is passed over, as the claim's own selection passes it over. A backfill or a retry after a
        failed start picks this way too, so it never hands out what the selection would drop. At most CLAIM_PICKS
        are passed over, then none, as the selection loop bounds it."""
        for _ in range(CLAIM_PICKS):
            row = self.candidate(c, who, body, runner, people_only=people_only, failed=failed, skip=passed)
            self.hold_failed(c, failed)
            if row is None or not self.obsolete_notice(c, who, row["id"], now):
                return row
            passed.append(row["id"])
        return None

    @staticmethod
    def held_answer(c, runner_id):
        hold = start_places.computer_hold(c, runner_id)
        if hold:
            return {"attempt": None, "waiting": hold[1]}
        return {"attempt": None}

    def held_starts(self, c, runner_id):
        """(people_only, answer) for a computer that holds new starts: overloaded, not reporting, memory high, or with turns already waiting
        to start there. Only a person's own message is handed out then (start_places.from_person); with none queued,
        `answer` is the claim's answer at once. Capacity still applies to that message."""
        if not start_places.computer_hold(c, runner_id) and not start_places.ready_waiting(c, runner_id):
            return False, None
        return True, (None if start_places.person_queued(c, runner_id) else self.held_answer(c, runner_id))

    def company_waiter(self, c, who, body, runner, failed=None):
        """Oldest eligible stop-level waiter across computers; unavailable lanes do not block others.
        Another computer's waiter is eligible only while that computer claims: its claimed busy lists are the only
        record of lanes the attempts table does not show, and an empty list from a silent computer is not "free"."""
        from .models import Claim
        from .performance import load_status
        from .read_cache import one
        for queued in c.execute("SELECT w.job_id,a.runner_id FROM org_turn_waits w JOIN jobs j ON j.id=w.job_id "
                                 "JOIN assignments a ON a.bot=j.bot WHERE j.state='queued' ORDER BY w.created,w.job_id"):
            rid = queued['runner_id']
            remote = rid != runner['id']
            # Share RUNNER_SQL with bot_claimable for the snapshot; skip revoked computers in Python.
            found = one(c, RUNNER_SQL, (rid,)) if remote else runner
            target = found if found and not found["revoked_at"] else None
            if (not target or (remote and not claiming(rid, CLAIMED_WAITING_FRESH))
                    or not target['last_seen'] or target['last_seen'] < H.shift(H.now(), seconds=-90)
                    or not self.awake(target['awake_since'], H.now()) or runner_versions.paused(c, rid)
                    or cloud_machines.held(c, rid)
                    or load_status(c, rid)['overloaded'] or start_places.ready_waiting(c, rid)
                    or sum(turns_here(c, rid).values()) >= start_places.turn_limit(target)):
                continue
            busy_bots, busy_lanes = claimed_busy(rid, CLAIMED_WAITING_FRESH)
            scope = Claim(busy_bots=list(busy_bots), busy_lanes=['|'.join(x) for x in busy_lanes]) if remote else body
            identity = Identity('runner:' + rid, 'runner', runner_id=rid) if remote else who
            if found := self.candidate(c, identity, scope, target, job_id=queued['job_id'], failed=failed):
                return rid, found
        return None

    def idle_claim(self, c, who, body, key=None, selected=None, failed=None):
        """The answer to a claim that changes nothing, from a read-only connection, or None when
        the claim has something to write or to hand out and must run in the write transaction.

        Runners ask every fraction of a second, so an idle fleet would otherwise take the
        database's one write lock several times a second for nothing. Everything `claim` writes
        before it looks for work is checked here: a lapsed lease to expire, a contact stamp that
        is due, an obsolete notice to suppress. Any of them, or a job to hand out, sends the claim
        on to `claim`, which repeats every check under the lock."""
        # A retry of a claim that took a job is answered from the record of it, not from here.
        if not key or c.execute("SELECT 1 FROM idempotency WHERE actor=? AND operation='/api/v2/jobs/claim' AND key=?",
                                (who.actor, key)).fetchone():
            return None
        answer = self._idle_claim(c, who, body, selected, failed)
        if answer is not None:
            self.record_contact(H.now())
        return answer

    def _idle_claim(self, c, who, body, selected=None, failed=None):
        runner = self.runner(c, who)
        if not runner:
            return None
        note_claim(runner["id"], body)
        now = H.now()
        if self.maintenance_due(c, who):
            return None  # the writer expires lapsed leases and releases due reset holds before selecting work
        awake_since = self.waking(runner, now)
        if (awake_since != runner["awake_since"] or not runner["last_seen"]
                or runner["last_seen"] <= H.shift(now, seconds=-CONTACT_EVERY)):
            return None
        if c.execute("SELECT 1 " + obsolete_notices_sql() + " LIMIT 1", (who.runner_id, H.KEEPER, now)).fetchone():
            return None
        company = org_turns.admission(c)
        if org_turns.needs_observation(c, company):
            return None
        if company['at_stop']:
            # Record new jobs as waiters under the writer lock; an unchanged idle fleet stays read-only.
            if c.execute("SELECT 1 FROM jobs j WHERE j.state='queued' AND NOT EXISTS "
                         "(SELECT 1 FROM org_turn_waits w WHERE w.job_id=j.id) LIMIT 1").fetchone():
                return None
            return {'attempt': None, 'waiting': org_turns.REASON}
        if cloud_access.locked(c):
            return {"attempt": None, "paused": cloud_access.LOCKED}
        if c.execute('SELECT 1 FROM org_turn_waits LIMIT 1').fetchone():
            return None
        why = runner_versions.paused(c, who.runner_id) or cloud_machines.held(c, who.runner_id)
        if why:
            return {"attempt": None, "paused": why}
        people_only, held = self.held_starts(c, who.runner_id)
        if held:
            return held
        count = sum(turns_here(c, who.runner_id).values())
        if count >= start_places.turn_limit(runner) or not self.awake(awake_since, now):
            return {"attempt": None}
        row = self.candidate(c, who, body, runner, people_only=people_only, failed=failed)
        if failed:
            return None         # a job that raised is held by the claim, under the writer lock, before any answer
        if row is None and people_only:
            return self.held_answer(c, who.runner_id)
        if row is None and (auto_tasks.missing(c, self.store.settings, runner, body, failed, self.check_waiting)
                            or failed or failed is not None and self.check_recovered(c, failed.clean)):
            return None         # a bot whose check raised, or passes again, is recorded under the writer lock
        if row is not None and selected is not None:
            selected.append(row)
        return None if row else {"attempt": None}

    def stamp(self, c, who, runner, now):
        """Record contact: `last_seen`, at most every CONTACT_EVERY, and a new waking period after a silence."""
        awake_since = self.waking(runner, now)
        if awake_since != runner["awake_since"]:
            H.event(c, H.KEEPER, "runner.waking", who.runner_id,
                    {"last_seen": runner["last_seen"], "awake_since": awake_since})
        if self.stamp_due(runner, now):
            c.execute("UPDATE runners SET last_seen=?,awake_since=? WHERE id=?",
                      (now, awake_since, who.runner_id))
        return awake_since

    def stamp_due(self, runner, now):
        return (self.waking(runner, now) != runner["awake_since"] or not runner["last_seen"]
                or runner["last_seen"] <= H.shift(now, seconds=-CONTACT_EVERY))

    def contact(self, c, who, *, check=False):
        """A runner's open event stream is contact, as a claim or a heartbeat is: its keepalives keep
        `last_seen` (Health, waking periods) current while the runner sends little else, and keep
        `served_at` current so a lapsed lease is not mistaken for this server's stall. Problem 403
        for a revoked computer. With `check`, on a read connection: whether a write is due, writing nothing."""
        runner = self.runner(c, who)
        if not runner:
            raise Problem("forbidden", "This computer is no longer registered", 403)
        now = H.now()
        self.record_contact(now)
        if check:
            return self.stamp_due(runner, now)
        self.stamp(c, who, runner, now)

    def maintenance_due(self, c, who):
        """Whether `maintain` has anything to do for this claim: a start place or lease that lapsed, a quota reset hold
        on one of this computer's bots that is due, or a held run whose wait has ended. Read-only, so an idle poll
        stays off the lock."""
        now = H.now()
        return bool(start_places.overdue(c) or c.execute(
            "SELECT 1 FROM jobs j JOIN assignments a ON a.bot=j.bot WHERE a.runner_id=? "
            "AND j.state='queued' AND j.held=1 AND j.wake_class='quota-reset' "
            "AND j.hold_until<=? LIMIT 1", (who.runner_id, now)).fetchone() or c.execute(
            "SELECT 1 FROM jobs j JOIN assignments a ON a.bot=j.bot WHERE a.runner_id=? "
            "AND j.state='queued' AND j.held=1 AND j.hold_reason=? AND j.hold_until<=? LIMIT 1",
            (who.runner_id, claim_holds.REASON, now)).fetchone() or c.execute(
            "SELECT 1 FROM attempts WHERE state IN ('leased','running') AND lease_until<=? LIMIT 1",
            (now,)).fetchone())

    def maintain(self, c):
        """What a claim cleans up before it looks for work: due quota resets released, held runs whose wait has ended
        let go, and a bounded number of lapsed leases expired, their work back in the queue."""
        wake_presets.release_quota_resets(c)
        claim_holds.release_due(c)
        self.expire(c, limit=CLAIM_MAINTENANCE)

    def claim(self, c, who, body, selected=None, *, prepared=False, maintained=False, failed=None):
        """`maintained`: the claim route ran `maintain` in its own write before its read snapshot chose `selected`,
        so a job that expiry requeued was there to choose, and this write rechecks only that job.
        `failed`: the read snapshot's ClaimFaults (see candidate): the jobs and bots that raised there. They are held
        first; the claim then chooses under the lock as usual."""
        failed = ClaimFaults() if failed is None else failed
        self.hold_failed(c, failed)
        runner = self.runner(c, who)
        if runner:
            note_claim(runner["id"], body)
        if not maintained:
            changes = c.total_changes
            self.maintain(c)
            if c.total_changes != changes:
                # Expiry can put a lapsed job back in the queue after the read snapshot chose:
                # choose afresh under this lock so it goes out now, not one poll later.
                selected, prepared = None, False
        now = H.now()
        self.record_contact(now)
        # A claim is contact too, and on a machine waking for a few seconds it is the first
        # contact: the heartbeat behind it is up to 15 s away. Recording the waking period here
        # is what keeps a brief wake from taking work on a stale one.
        # A claim is contact, and on a machine waking for seconds it is the only contact: its
        # heartbeat runs on a fifteen-second timer that a wake this short never reaches. Until
        # this was recorded, the next wake compared itself against a heartbeat from before the
        # sleep, read the gap as one long stretch of presence, and took the work.
        awake_since = self.stamp(c, who, runner, now)
        # Any keeper notice can sit queued after its task finishes. Suppress obsolete automatic
        # wakes of every kind, while preserving people's messages and explicitly resumed jobs.
        # A keeper notice of a person's own act still owes that person its answer (H._write_message).
        # A self-filed task left parked when its filing turn ended waits for its actual trigger instead.
        obsolete = c.execute("SELECT j.id,j.bot,j.attempt_id,j.message_id,t.id AS task_id " + obsolete_notices_sql() +
                             " ORDER BY j.created,j.id LIMIT ?", (who.runner_id, H.KEEPER, now, CLAIM_MAINTENANCE)).fetchall()
        for old in obsolete:
            if not prepared:
                selected = None
            c.execute("UPDATE jobs SET state='cancelled' WHERE id=?", (old['id'],))
            claim_holds.clear(c, old['id'], "Its run is no longer needed.")
            c.execute("UPDATE messages SET delivered_at=coalesce(delivered_at,?) WHERE id=?", (H.now(),old['message_id']))
            from .repositories import metadata
            if (old["attempt_id"] and metadata(c, LAST_STOP + old["bot"]).get("attempt_id") == old["attempt_id"]
                    and (H.status(c, old["bot"]) or {}).get("state") == "idle"):
                # Clearing the status also publishes the new header to already-open pages.
                settle_status(c, old["bot"], "idle", "")
            H.event(c,H.KEEPER,'job.suppress',old['id'],{'reason':'Task finished or acted on before keeper notice delivery','task_id':old['task_id']})
        company = org_turns.observe(c, self.store.settings, detailed=False)
        if company['at_stop']:
            return {'attempt': None, 'waiting': org_turns.REASON}
        if cloud_access.locked(c):
            return {"attempt": None, "paused": cloud_access.LOCKED}
        # A cloud machine reserved for a stop takes no new turn: checked under this write lock, which the stop's
        # reservation also takes, so a claim either finds it or has its turn out before it (cloud_machines.reserve).
        why = runner_versions.paused(c, who.runner_id) or cloud_machines.held(c, who.runner_id)
        if why:
            return {"attempt": None, "paused": why}
        people_only, held = self.held_starts(c, who.runner_id)
        if held:
            return held
        if people_only:
            selected, prepared = None, False     # chosen before the hold, perhaps not a person's
        count = sum(turns_here(c, who.runner_id).values())
        if count >= start_places.turn_limit(runner):
            return {"attempt": None}
        # A machine that has only just come back cannot be trusted with a 90 s lease yet.
        # Its work keeps its place in the queue and goes out once the machine is really up.
        if not self.awake(awake_since, now):
            return {"attempt": None}
        if oldest := self.company_waiter(c, who, body, runner, failed=failed):
            if oldest[0] != who.runner_id:
                return {'attempt': None, 'waiting': org_turns.REASON}
            selected, prepared = oldest[1], True
        # Selection scans the queue on a read connection. Recheck only that job's current eligibility under the writer
        # lock. A selected job that is no longer eligible here (the obsolete-notice loop above cancelled it, or its
        # recheck refuses it) is dropped, and the claim takes the next candidate in this same call, passing over the
        # jobs it dropped. A claim drops at most CLAIM_PICKS, so it never loops; then it answers none, as before.
        passed = []
        for _ in range(CLAIM_PICKS):
            if prepared:
                row = (self.candidate(c, who, body, runner, job_id=selected["id"], failed=failed)
                       if selected is not None else None)
            else:
                row = (selected if selected is not None else
                       self.candidate(c, who, body, runner, people_only=people_only, failed=failed, skip=passed))
            raised = bool(failed)
            self.hold_failed(c, failed)
            if row is None and raised:
                # The job checked here was held just now: the next candidate is taken in this same claim.
                row = self.candidate(c, who, body, runner, people_only=people_only, failed=failed, skip=passed)
                self.hold_failed(c, failed)
            if row is not None and self.obsolete_notice(c, who, row["id"], now):
                # The obsolete-notice page above cancels CLAIM_MAINTENANCE at a time, and an obsolete notice is never
                # handed out: the claim passes over this one and takes the next.
                passed.append(row["id"])
                row, selected, prepared = None, None, False
                continue
            if row is not None or selected is None:
                break
            passed.append(selected["id"])
            selected, prepared = None, False
        if row is None and people_only:
            return self.held_answer(c, who.runner_id)
        # Backfill a free turn after queued conversations and awaited results keep their place. A claim that passed
        # over a dropped pick leaves backfill to the next poll: its scan under the writer lock is not repeated in a
        # busy moment.
        if (row is None and selected is None and not passed
                and (pending := auto_tasks.missing(c, self.store.settings, runner, body, failed, self.check_waiting))):
            auto_tasks.enqueue(c, pending)
            row = self.pick(c, who, body, runner, now, failed=failed, passed=passed)
        self.hold_failed(c, failed)
        if not row:
            return {"attempt": None}
        # The job's preparation runs in a savepoint of its own. An error rolls back its writes: a check of one bot
        # passes that bot over, and a job's own error holds the job. The claim takes the next candidate until
        # CLAIM_HOLDS jobs are held. A team's check and a storage error fail the claim, as they always did.
        while True:
            c.execute("SAVEPOINT claim_start")
            try:
                answer = self.start_claimed(c, who, body, runner, row)
            except Exception as exc:
                scope = shared_scope(exc)
                if not c.in_transaction or storage_error(exc) or scope == "team":
                    raise
                c.execute("ROLLBACK TO claim_start")
                c.execute("RELEASE claim_start")
                if scope:
                    failed.bot(row["bot"], exc)
                else:
                    report("claim", row["id"], exc)
                    failed.job(row["id"], type(exc).__name__)
                self.hold_failed(c, failed)
                if failed.full():
                    return {"attempt": None}
                row = self.pick(c, who, body, runner, now, people_only=people_only, failed=failed, passed=passed)
                if not row:
                    return {"attempt": None}
                continue
            c.execute("RELEASE claim_start")
            if answer.get("attempt"):
                # The run is handed out: a retry that starts clears its hold and its bot's Health finding.
                claim_holds.started(c, row["id"])
                self.check_backoff.pop(row["bot"], None)
            return answer

    def start_claimed(self, c, who, body, runner, row):
        """The preparation of the job a claim hands out: its turn, attempt, inputs and hand-out. The claim runs it in a
        savepoint of its own (claim), so an error here rolls back only this job's writes."""
        msg = H.message(c, row["message_id"])
        if not privacy.message_readable(c, "bot:" + row["bot"], msg):
            c.execute("UPDATE jobs SET state='cancelled' WHERE id=?", (row["id"],))
            H.event(c, H.KEEPER, "job.suppress", row["id"], {"reason": "unreadable"})
            return {"attempt": None}
        # The bot's budget and back-off: an error in them passes the bot over, and holds no run.
        with shared_check():
            if turn_budgets.enforce(c, self.store.settings, row, msg):
                return {"attempt": None}    # the bot is over its run budget: held now, its queue kept
            if run_backoff.probe(c, row["bot"]):
                return {"attempt": None}    # held for failed runs and its next try is not due, or another took it
        if finance_run_hold(c, row["bot"], row["id"]):
            return {"attempt": None}        # a Finance budget at its cutoff refuses the run (usage_limits holds the bot)
        live_ref = (msg.get("refs") or {}).get("live_meeting")
        if (isinstance(live_ref, dict) and msg.get("from_actor") == H.KEEPER
                and msg.get("to_actor") == "bot:" + row["bot"]):
            from .live_meetings import claim_runner_turn
            meeting_claim = claim_runner_turn(c, live_ref.get("meeting_id"), live_ref.get("turn_id"),
                                              row["bot"], auth=self.auth)
            if meeting_claim["status"] != "claimed":
                return {"attempt": None}
        conv = H.conversation(c, msg["conversation_id"])
        task = H.task(c, work_task_id(msg, conv)) if work_task_id(msg, conv) else None
        if task and task["owner"] == "bot:" + row["bot"] and row["bot"] != self.store.settings.assistant_bot:
            # A bot's task filed before task rooms moves to its own room now; this turn's message stays put.
            # The Assistant's stay in the person's own room, where its turns act for that person.
            task = H.give_task_room(c, task)
        from .shared_bots import follow
        followed = follow(c, row["bot"], json.loads(
            c.execute("SELECT config_json FROM bot_config WHERE bot=?", (row["bot"],)).fetchone()[0]))
        if isinstance(live_ref, dict):
            # A live-meeting reply runs on its meeting's model (backend/live_meetings.py turn_config), not the bot's.
            from .live_meetings import turn_config
            followed = turn_config(c, self.store.settings, followed, row["bot"], live_ref.get("meeting_id"),
                                   who.runner_id, row["id"], live_ref.get("turn_id"))
        # A check subtask's turns run on its own model, in its own session (backend/check_models.py).
        check = check_models.of(c, task["id"], row["bot"]) if task else None
        released_notice = bool(task and auto_tasks.notice_releases(c, task, msg))
        if (task and task['owner'] == 'bot:' + row['bot']
                and not auto_tasks.refusal(c, task, followed, woken=True, message=msg)
                and not (msg.get("refs") or {}).get("run_now")):
            from . import automations   # the claim starts the turn: its task's change acts for the turn
            with automations.acting_for((msg.get("refs") or {}).get("automation_chain") or ()):
                task = auto_tasks.started(c, task, message=msg)
        roots, lanes = roots_on(runner), lanes_on(runner)
        # A turn in a task's own session runs on the bot's task model when it has one, or on its own model when that
        # cannot run here; a check keeps its check model (backend/task_models.py).
        followed, model_record = task_models.pick(
            c, self.store.settings, row["bot"], followed, task["id"] if task else None,
            session_task_id(c, row["bot"], followed, task["id"], roots) if task else None, conv["id"],
            who.runner_id, row["id"], check)
        followed = check_models.apply(followed, check)
        # What really runs this turn: the task's or check's model, or the bot's own after a fallback. A change to
        # the bot's model while the turn is out is told from an effort change by these (ran_another_model).
        handed = providers.bot_choice(c, self.store.settings, followed)
        lane = job_lane(c, row["bot"], followed, msg, conv, lanes, roots)
        token = secrets.token_urlsafe(32)
        until = H.shift(H.now(), seconds=self.store.settings.lease_seconds)
        woke, source = run_triggers.of_message(msg)
        turn = H.turn_start(c, H.KEEPER, row["bot"], trigger="task" if task else msg["kind"],
                            message_id=msg["id"], task_id=task["id"] if task else None,
                            trigger_kind=woke, trigger_source=source,
                            purpose=run_triggers.purpose_of(msg, row["bot"], woke),
                            rule_id=wake_rule_engine.claimed_rule(msg, c.execute(
                                "SELECT wake_class FROM jobs WHERE id=?", (row["id"],)).fetchone()))
        aid = turn["id"]
        c.execute("INSERT INTO attempts(id,job_id,bot,runner_id,generation,token_hash,state,lease_until,created,lane) "
                  "VALUES(?,?,?,?,?,?,'leased',?,?,?)",
                  (aid, row["id"], row["bot"], who.runner_id, row["generation"], digest(token), until, H.now(), lane))
        if body.start_places:
            start_places.claim(c, aid)
        c.execute('DELETE FROM org_turn_waits WHERE job_id=?', (row['id'],))
        from . import run_diagnostics
        run_diagnostics.touch(c, aid, "leased")
        c.execute("UPDATE jobs SET state='leased',attempt_id=? WHERE id=?", (aid, row["id"]))
        org_turns.observe(c, self.store.settings, detailed=False)
        # What woke this turn, the wake preset and the bot's profile, for its run record (backend/wake_presets.py).
        job = c.execute("SELECT wake_preset,wake_class,hold_until FROM jobs WHERE id=?", (row["id"],)).fetchone()
        c.execute("UPDATE attempts SET wake_json=?,handed_runtime=?,handed_model=? WHERE id=?",
                  (json.dumps(wake_presets.record(c, job, msg, row["bot"])), *handed, aid))
        c.execute("INSERT OR IGNORE INTO attempt_conversations VALUES(?,?)", (aid, conv["id"]))
        task_models.handed(c, aid, task["id"] if task else None, model_record)
        held = None
        source = source_continuation(c, row["id"])
        if source:
            login_holds.take_probe(c, who.runner_id, login_holds.canonical(c, who.runner_id, source["profile"], source["runtime"]), source["runtime"], row["id"])
        elif check:
            check_models.handed(c, aid, task["id"], check)
        elif primary_hold(c, self.store.settings, who.runner_id, row["bot"], followed, row["id"]):
            # Its login is held and the claim let it through for its fallback (login_refusal): the runner starts the
            # turn there and never tries the held login. A fallback login whose own hold just ended gets this job as
            # its one probe.
            held = login_holds.get(c, *login_key(c, self.store.settings, who.runner_id, row["bot"], followed))
            moved = fallback_choice(c, self.store.settings, who.runner_id, row["bot"], followed, row["id"])
            if moved:
                login_holds.take_probe(c, who.runner_id, *moved, row["id"])
        else:
            # After a hold on the bot's login ends, the first job to start on it is the window's one probe.
            login_holds.take_probe(c, *login_key(c, self.store.settings, who.runner_id, row["bot"], followed), row["id"])
        # A bot that names no runtime or model runs on the company default, resolved here the way
        # runners/assignments resolves it: the runner starts exactly what this says.
        record = providers.load(c, self.store.settings)
        config = providers.fill(record, followed)
        # The company's models choice: the Gateway while the bot's subscription is not signed in here, and the
        # Gateway as its last fallback, on a computer that can run it (backend/models_preset.py).
        here = readiness_document(runner["readiness_json"])
        # The computer's subscription for this runtime signs the turn in, so a missing or
        # refused sign-in here does not move it to the Gateway (backend/model_subscriptions.py).
        subscribed = model_subscriptions.for_claim(c, runner, config)
        here = model_subscriptions.covered(here, [x["runtime"] for x in subscribed])
        # Its department's tier (Medium by default) is looked up only when a turn would use it.
        tier = lambda: models_preset.model_for_bot(c, self.store.settings, row["bot"], followed)
        config = models_preset.with_gateway_fallback(record, here, models_preset.main_config(record, here, config, tier), tier)
        config = models_preset.with_gateway_inputs(config)
        # A bot delivered through a desktop app: who it is in the app, beside `app_delivery` (backend/app_delivery.py).
        config = app_delivery.job_config(config)
        if held:
            config = {**config, "primary_held": held.get("until") or H.now()}
        # The thread's last run failed on a prompt too large to send: this one compacts its session before it runs, once
        # (backend/run_backoff.py). An older runner ignores it, fails the same way, and the next run starts anew.
        if run_backoff.take_compact(c, row["bot"], conv["id"], aid):
            config = {**config, "compact_first": True}
        # A stateless bot (Counsel): its runner starts each task fresh, reads and keeps no memory (hubdb.stateless).
        if H.stateless(followed):
            config = {**config, "stateless": True}
        gateway_connection.hand_out(c, aid, config)
        # The room's recent page. The runner forwards only what arrived since the bot last
        # answered; the bot reads further back itself with `hub conversation show` when it wants to. The
        # hub keeps no pointer to the bot's session and rebuilds nothing on its behalf.
        epoch = c.execute("SELECT epoch,updated FROM session_epochs WHERE conversation_id=?", (conv["id"],)).fetchone()
        bot_epoch = c.execute("SELECT epoch FROM bot_session_epochs WHERE bot=?", (row["bot"],)).fetchone()
        bot_who = Identity("bot:" + row["bot"], "bot", attempt_id=aid)
        history = privacy.page(c, bot_who, conv["id"], limit=HISTORY_PAGE * (2 if lanes else 1),
                               since=epoch["updated"] if epoch else None)["messages"]
        if lanes:
            history = lane_history(c, row["bot"], followed, history, conv, lane, roots)[-HISTORY_PAGE:]
        principal = (conv.get("owner_actor") if conv.get("scope") == "personal"
                     else msg["from_actor"] if str(msg["from_actor"]).startswith("human:") else None)
        routine = None
        if task:
            # The routine this task came from, so the bot knows the text is a standing
            # instruction and not a person's ask. What it says is already the task body.
            saved = c.execute('SELECT s.id,s.title FROM schedule_occurrences o JOIN schedules s ON s.id=o.schedule_id '
                              'WHERE o.task_id=?', (task['id'],)).fetchone()
            if saved:
                routine = dict(saved)
            # Its related tasks the bot may open (kind, direction, title, status); one it may not
            # open is left out without a trace.
            def readable(other):
                try:
                    self.auth.task_row(c, bot_who, other)
                    return True
                except Problem:
                    return False
            from .task_relations import for_prompt
            task = {**task, "relations": for_prompt(c, task["id"], lambda other: bool(other) and readable(other))}
            if check:
                task["check"] = {k: check[k] for k in ("runtime", "model", "role")}
            goal = task_goal(c, row["bot"], followed, task)
            if goal:
                # Worked as a goal: the runner tells the bot to keep at it until the objective holds.
                task["goal"] = goal
            if roots:
                # The task whose session and worktree this turn continues (H.session_root); the runner keys on it.
                task["session_task_id"] = session_task_id(c, row["bot"], followed, task["id"], roots)
                root = H.task(c, task["session_task_id"]) if task["session_task_id"] != task["id"] else None
                if root and readable(root):
                    # The checkpoint lives on the root: a fresh session starts from where the root stood.
                    kept = root.get("checkpoint") if not root.get("checkpoint_blocked") else None
                    task.update({"checkpoint": kept, "checkpoint_at": root.get("checkpoint_at") if kept else None,
                                 "checkpoint_by": root.get("checkpoint_by") if kept else None})
            own_room = conv and conv.get("kind") == "task" and conv.get("task_id") == task["id"]
            from .chat_goals import clear_task_goal, task_goal_row
            if own_room and not goal and task.get("owner") == "bot:" + row["bot"]:
                # goal:off, or the bot's task_goal turned off: the goal Tico set for the task ends. Only the
                # owner's turn decides that: task_goal is None for any other bot, such as the requester answering
                # in the room, and its turn says nothing about the owner's goal.
                clear_task_goal(c, conv)
            if (goal and own_room and lane != sessions.MAIN_LANE and task_goals_on(runner)
                    and task.get("session_task_id", task["id"]) == task["id"]):
                # Its own session runs it as the harness's native goal; the runner sets it once per session.
                native = task_goal_row(c, self.auth, conv, row["bot"], task, goal["objective"])
                if native:
                    task["goal"] = {**goal, "goal_id": native["id"]}
        recovery = c.execute("SELECT * FROM job_recovery WHERE job_id=? AND attempt_id=? AND decision='resume'",
                             (row['id'],row['attempt_id'])).fetchone()
        if recovery:
            msg = {**msg, 'body':msg['body'] + '\n\nOperator recovery instructions (' + recovery['actor'] + '):\n' + recovery['note']}
        if task and (recovery or released_notice):
            msg = self.fold_behind(c, row, aid, msg, task["id"], followed, lanes, roots, lane)
        # The model's capacity stopped this job's turn after it acted: the runner continues that same thread, or holds
        # the run for review when it no longer has it (runner-rs/src/execute.rs CONTINUE_LOST).
        continue_thread = continuation_thread(c, row['id'])
        if continue_thread:
            msg = {**msg, 'body': msg['body'] + (CONTINUE_NOTE if capacity_continuation(c, row['id']) else
                   '\n\nTico: continue the same saved session after the source interruption. Check prior work and finish only what remains.')}
        # Next-run tasks ride along with whatever woke the bot: one run, one prompt, each task
        # still its own task to mark done. Only a runner that puts them in its prompt gets them.
        # Any lane carries them: a bot whose turns are all tasks and routines never has a main-lane
        # turn. The claim holds the write lock and `carried_by` marks each one taken, so two lanes
        # never carry the same task.
        carried = []
        # A bot limited to one task (scoped_sql) carries only that task's work: its other next-run tasks and waiting
        # notes stay where they are for a run after the scope is released.
        scope = claim_scope(c, row["bot"])
        # A check runs on another model for one subtask: the bot's other work waits for its own turns.
        if getattr(body, "next_run", False) and not check:
            if row["bot"] == "librarian":
                from .docs import refresh_generated_docs
                refresh_generated_docs(c)  # Also covers a Librarian enabled after the upgrade.
            for item in H.next_run_tasks(c, row["bot"], exclude=task["id"] if task else None):
                if not H.task_private_readable(c, bot_who.actor, item) or scope and not in_scope(c, scope, item["id"]):
                    continue
                if routine and H.task_private(c, item):
                    # A routine runs in company scope: a private task carried in would make the whole run
                    # private, and its shared outputs (the docs map, a report) refused. It gets a run of its
                    # own instead, since a bot that only runs routines may never have another.
                    c.execute("UPDATE tasks SET next_run=0, updated=? WHERE id=?", (H.now(), item["id"]))
                    H._task_event(c, item["id"], H.KEEPER, "next_run", "1", "0", "a routine left it out: its own run")
                    H._wake(c, item, item["owner"], f"Next-run task: {item['title']}", refs={"wake": "next-run"},
                            trigger="schedule")
                    continue
                c.execute("UPDATE tasks SET carried_by=? WHERE id=?", (aid, item["id"]))
                # How to say "not now" with a time to come back, rather than in the reply's words (quiet_work).
                carried.append({**{k: item.get(k) for k in ("id", "title", "body", "requester", "created")},
                                "if_not_now": quiet_work.HINT.format(id=item["id"])})
            if carried:
                H.event(c, H.KEEPER, "task.next-run.carried", aid,
                        {"bot": row["bot"], "tasks": [item["id"] for item in carried]})
        # Quiet notes ride the same way: every note waiting for this bot, each with when it was
        # sent, so the run can tell yesterday's from this morning's.
        notes = []
        if getattr(body, "next_run", False) and not check and not scope:
            from . import bot_to_bot
            waiting = H.notes_waiting(c, row["bot"])
            for item in waiting:
                c.execute("UPDATE notes SET carried_by=? WHERE id=?", (aid, item["id"]))
            # A bot's message that waited for this run is read now; a backlog past one run's notes gets a run of its own.
            for item in bot_to_bot.carried(c, waiting, H.now()):
                notes.append({"id": item["id"], "from": item["from_actor"], "sent": item["created"],
                              "text": item["body"], **({"message": item["message_id"]} if item.get("message_id") else {})})
            bot_to_bot.continue_backlog(c, row["bot"])
            if notes:
                H.event(c, H.KEEPER, "note.carried", aid, {"bot": row["bot"], "notes": [n["id"] for n in notes]})
        # What was deferred to this turn (backend/delivery.py): the session's comments and messages, written out in the
        # prompt since the history page may not reach them, and the deferred tasks and notes above. Their deadlines are
        # met. A deferred task this turn works as its own is met too, though not counted: it is the turn's task.
        from . import delivery
        own = [task["id"]] if task and task.get("id") else []
        deferred = delivery.summary(
            delivery.carried(c, row["bot"], aid, [conv["id"], (task or {}).get("conversation_id")],
                             [item["id"] for item in carried] + own, [item["id"] for item in notes],
                             job=row["id"], current=msg["id"]),
            tasks=len(carried), notes=len(notes))
        from .views import roster
        people = roster(c)
        inbox = P.inbox_person(row["bot"], people)
        subscriptions = model_subscriptions.hand_out(c, aid, subscribed)
        parked = c.execute("SELECT onboarding_state FROM bot_config WHERE bot=?", (row["bot"],)).fetchone()
        from .chat_goals import current
        from . import worktrees
        # A runner from before task goals would treat a task room's goal as running without ever setting it.
        chat_goal = current(c, conv["id"]) if not (conv.get("kind") == "task" and not task_goals_on(runner)) else None
        if chat_goal and chat_goal["bot"] != row["bot"]:
            # Another bot's turn in the room (the requester answering) works without the owner's goal.
            chat_goal = None
        return {"attempt": {"chat_goal": chat_goal, "routine": routine, "id": aid, "deferred": deferred,
                            # Which waiting turn takes a shared one-turn login first (runner-rs/src/login_turns.rs).
                            "login_class": login_class(c, task, msg, routine),
                            "recovery": {"resume_session": restart_reported(c, row["attempt_id"]) or bool(cut_by_update(c, row["attempt_id"]))
                                         or bool(run_limit_minutes(c, row["attempt_id"])) or bool(c.execute(
                                "SELECT 1 FROM attempts WHERE id=? AND state='expired' AND thread_id IS NOT NULL",
                                (row["attempt_id"],)).fetchone())} if recovery else None,
                            "computer_label": runner["label"], "computer_logins_v1": True,
                            # The computer the bot's browser reaches the web through, or None (backend/browser_exit.py).
                            "browser_exit": browser_exit.for_turn(c, row["bot"]),
                            # A starter bot's chat while it is `needs_setup` is its setup (runner prompt).
                            "onboarding": (parked["onboarding_state"] if parked else "") or "",
                            # The mailboxes an inbox bot's turn may ask its runner for mail access to: the one it declares
                            # (its person's email when it declares none), then the people below them.
                            "mailboxes": routines.token_mailboxes(c, row["bot"], people) if inbox else [], "next_run": carried, "notes": notes, "job_id": row["id"], "bot": row["bot"], "lane": lane,
                            "credential_vault": bool(subscriptions) or c.execute(
                                "SELECT 1 FROM credential_grants WHERE subject=? AND revoked IS NULL LIMIT 1",
                                ("bot:" + row["bot"],)).fetchone() is not None,
                            # The company subscriptions this turn signs in with: ids and kinds, never values, which the run
                            # reads from credential-runtime (backend/model_subscriptions.py).
                            **({"subscriptions": subscriptions} if subscriptions else {}),
                            # A run on a Tico model reaches the Gateway through its runner, which alone is handed the key
                            # (runner-gateway-credentials; runner-rs/src/gateway_relay.rs).
                            "gateway": gateway_connection.wanted(config),
                            "generation": row["generation"], "lease_until": until,
                            **({"start_reservation_seconds": start_places.START_SECONDS} if body.start_places else {}),
                            "lease_seconds": self.store.settings.lease_seconds, "token": token,
                            "message": msg, "conversation": conv, "task": task,
                            **({"continue_thread": continue_thread} if continue_thread else {}),
                            **({"source_continuation": source} if source else {}),
                            # The task's set-up worktrees on this computer, added to its session (runner-rs/src/sessions.rs).
                            "worktrees": worktrees.attached(c, task["id"], who.runner_id, row["bot"]) if task else [],
                            "history": history, "principal": principal,
                            # What the person's chat bot has learned, read now: a deleted entry is gone from this turn
                            # (backend/learned.py; rendered by runner-rs/src/prompt.rs). Outside their own chat only
                            # `strategy`, the team's published strategy, which every turn carries (backend/strategy.py).
                            "learned": learned.for_turn(c, self.store.settings, conv, row["bot"]),
                            # Asked from the Updates chat: the recent updates that person may read and open, read now as
                            # them (backend/updates_chat.py). None for every other turn.
                            "updates": updates_chat.for_turn(c, self.auth, self.store.settings, conv, row["bot"], msg, aid),
                            # The owner's earlier Strategy Bot chat is a session of its own (backend/rooms.py).
                            "config": rooms.session_config(conv, row["bot"], config),
                            # Bumped by a session clear or reset (views.py clear_session, app.py reset_bot_sessions):
                            # a runner that sees it change starts a fresh provider thread (runner-rs/src/execute.rs).
                            "session_epoch": epoch["epoch"] if epoch else 0,
                            # Moved by any clear or reset of this bot: the runner starts the bot's shared thread anew.
                            "bot_session_epoch": bot_epoch[0] if bot_epoch else 0}}

    @staticmethod
    def obsolete_notice(c, who, job_id, now):
        """Whether a queued job is an obsolete keeper notice (obsolete_notices_sql), which a claim cancels and never
        hands out."""
        return c.execute("SELECT 1 FROM (SELECT j.id AS id " + obsolete_notices_sql() + ") o WHERE o.id=? LIMIT 1",
                         (who.runner_id, H.KEEPER, now, job_id)).fetchone() is not None

    def check_waiting(self, bot):
        """Whether the bot's claim check raised and its back-off has not ended: the claim passes the bot over without
        running the check. hold_failed sets the back-off."""
        wait = self.check_backoff.get(bot)
        return wait is not None and H.now() < wait[0]

    def check_recovered(self, c, bots):
        """Whether any of `bots`, whose checks just passed, still has a back-off or a Health finding to clear. The
        finding is read, not inferred from the back-off, which a restart forgets."""
        if bots & self.check_backoff.keys():
            return True
        marks = ",".join("?" * len(bots))
        return bool(bots) and c.execute(f"SELECT 1 FROM service_health WHERE service IN ({marks}) LIMIT 1",
                                        [claim_holds.CHECK + bot for bot in sorted(bots)]).fetchone() is not None

    def hold_failed(self, c, failed):
        """Record what the claim's ClaimFaults found, and clear it: each job it could not evaluate or start is held, and
        each bot whose check raised is logged once, named in Health (claim_holds.check_failed) and passed over until
        its back-off ends (check_waiting). A bot whose check passes again loses its back-off and its Health finding."""
        for bot in failed.clean - failed.bots.keys():
            self.check_backoff.pop(bot, None)
            claim_holds.check_passed(c, bot)
        failed.clean.clear()
        while failed.jobs:
            job_id, error = failed.jobs.pop(0)
            claim_holds.hold(c, self.store.settings, job_id, error)
        while failed.new_bots:
            bot, exc = failed.new_bots.pop(0)
            tries = self.check_backoff.get(bot, (None, 0))[1] + 1
            self.check_backoff[bot] = (H.shift(H.now(), seconds=claim_holds.check_delay(tries)), tries)
            claim_holds.check_failed(c, bot, exc)

    def attempt(self, c, who, aid, active=True, final=False, arrived=None):
        """`final` is a runner handing in the result of a turn it has finished. That result
        exists nowhere else, so a lapsed lease must not be grounds for refusing it: ownership
        still decides, the clock does not. `arrived` is when a renewal reached the server: a
        lease that was live then lapsed only while the renewal waited for the lock."""
        self.runner(c, who)
        self.record_contact(H.now())
        def read():
            return c.execute("SELECT a.*,x.runner_id AS assigned_runner,x.generation AS assigned_generation "
                             "FROM attempts a JOIN assignments x ON x.bot=a.bot WHERE a.id=?", (aid,)).fetchone()
        row = read()
        if not row or row["runner_id"] != who.runner_id:
            raise Problem("forbidden", "This execution attempt is not yours", 403)
        if final and row["state"] == "running" and row["lease_until"] <= H.now():
            # A started turn's lease lapsed and no sweep has caught it yet: lapse it now, as the sweep would have, so
            # its result is settled or filed below like any late one rather than refused with the lease. A turn that
            # never started has no result worth that: the sweep requeues or fails it as before.
            self.expire(c, attempt_ids=[aid])
            row = read()
        if not privacy.attempt_readable(c, "bot:" + row["bot"], aid):
            raise Problem("stale_lease", "Task access expired; stop this attempt", 409)
        if (row["state"] in ("leased", "running") and row["lease_until"] > H.now()
                and row["assigned_runner"] == who.runner_id and row["assigned_generation"] == row["generation"]
                and H.bot(c, row["bot"])["state"] == "quarantined"):
            # A quarantine stops this turn the way a person's Stop does, rather than refusing its runner as if the
            # computer were lost (Dev 573): the renewal answers stop, and the turn ends with the quarantine's reason.
            row = quarantine_stops.stop(c, row)
        current = self.current_owner(c, who, row)
        if active and current and row["state"] == "expired":
            row = self.restore(c, row) or row
        if final and row["state"] == "expired":
            return row
        place = c.execute("SELECT start_until FROM start_places WHERE attempt_id=?", (aid,)).fetchone()
        if active and row["state"] == "leased" and place and place[0] and place[0] <= H.now():
            raise Problem("stale_lease", "Start reservation expired; this work returns to the queue", 409)
        if active and (row["state"] not in ("leased", "running") or row["lease_until"] <= (arrived or H.now())
                       or not current):
            raise Problem("stale_lease", "Execution ownership expired; stop this attempt", 409)
        return row

    @staticmethod
    def current_owner(c, who, row):
        """Whether this runner still owns this bot's work: the assignment names it, at the same
        generation, and the bot is active. A transfer to another Mac ends ownership, and with it
        any say over the job. A quarantined bot's runner still speaks for a turn with a Stop on it, so the turn
        ends stopped with its reason rather than lapsing (Dev 573)."""
        state = H.bot(c, row["bot"])["state"]
        return bool(row["assigned_runner"] == who.runner_id and row["assigned_generation"] == row["generation"]
                    and (state == "active" or state == "quarantined" and row["stop_requested"]))

    def restore(self, c, row):
        """A running attempt the server let lapse comes back when its runner reports in within
        STALL_MAX, as long as nothing else touched the job: it is still uncertain and on this
        attempt, no review decided it, and no other attempt runs in its lane. The turn on the
        Mac never stopped, so its result is worth more than a review of a phantom interruption. Neither the computer's
        capacity nor the bot's Turns at once is checked: refusing would not stop the turn on the Mac, only discard
        its result, so a turn claimed while this one lapsed briefly runs beside it."""
        from .run_diagnostics import CAP_EVENT
        now = H.now()
        job = c.execute("SELECT state,message_id FROM jobs WHERE id=? AND attempt_id=?", (row["job_id"], row["id"])).fetchone()
        if (not row["finished"] or row["finished"] <= H.shift(now, seconds=-STALL_MAX)
                or not job or job["state"] != "uncertain"
                # A Stop recorded after the lease lapsed stands over whatever the turn reports next: nothing brings
                # it back, and `complete` files its result.
                or row["stop_requested"]
                # The server ended this run past its limit (run_diagnostics.cap): a renewal does not bring it back.
                or c.execute("SELECT 1 FROM events WHERE target=? AND action=? LIMIT 1", (row["id"], CAP_EVENT)).fetchone()
                or c.execute("SELECT 1 FROM job_recovery WHERE job_id=? AND attempt_id=?", (row["job_id"], row["id"])).fetchone()
                or c.execute("SELECT 1 FROM attempts WHERE bot=? AND lane=? AND state IN ('leased','running')",
                             (row["bot"], row["lane"])).fetchone()):
            return None
        until = H.shift(now, seconds=self.store.settings.lease_seconds)
        c.execute("UPDATE attempts SET state='running',finished=NULL,lease_until=? WHERE id=?", (until, row["id"]))
        from . import run_diagnostics
        run_diagnostics.restored(c, row["id"])
        c.execute("UPDATE jobs SET state='running' WHERE id=?", (row["job_id"],))
        c.execute("UPDATE turns SET finished=NULL,exit=NULL,summary=NULL WHERE id=?", (row["id"],))
        current = H.message(c, job["message_id"])
        settle_status(c, row["bot"], "running", "Responding to queued work",
                      claimed=row["created"], task_id=work_task_id(current) or "")
        H.event(c, H.KEEPER, "attempt.lease-restored", row["id"],
                {"bot": row["bot"], "job_id": row["job_id"], "lapsed": row["finished"], "lease_until": until})
        return c.execute("SELECT a.*,x.runner_id AS assigned_runner,x.generation AS assigned_generation "
                         "FROM attempts a JOIN assignments x ON x.bot=a.bot WHERE a.id=?", (row["id"],)).fetchone()

    def settleable(self, c, who, row):
        """Whether a result that arrives after the lease lapsed may still settle its job. The
        test is the one `restore` uses without the clock: this runner still owns the bot, the job
        is still uncertain and still on this attempt, no review has decided it, and no other
        attempt is running in its lane, and no person pressed Stop on it: a Stop its runner never heard, because the
        computer slept through the lease, still stands over the answer the model went on to finish. A review — a person's or `auto_reconcile`'s — always
        writes job_recovery, so a decision already taken wins even while it waits for an idle lane
        with the job still `uncertain`, and a late result can never undo one. Elapsed time is what hands a job from the runner to
        the sweep; it is not what makes settling safe, and the longest turns are exactly the ones
        most likely to outlive a lease."""
        return bool(not row["stop_requested"] and self.holds(c, who, row))

    def holds(self, c, who, row):
        """`settleable` before the Stop: this runner still speaks for the job its lapsed attempt left uncertain."""
        job = c.execute("SELECT state FROM jobs WHERE id=? AND attempt_id=?",
                        (row["job_id"], row["id"])).fetchone()
        return bool(self.current_owner(c, who, row)
                    and job and job["state"] == "uncertain"
                    and not c.execute("SELECT 1 FROM job_recovery WHERE job_id=? AND attempt_id=?",
                                      (row["job_id"], row["id"])).fetchone()
                    and not c.execute("SELECT 1 FROM attempts WHERE bot=? AND lane=? AND state IN ('leased','running')",
                                      (row["bot"], row["lane"])).fetchone())

    def settles_late(self, c, who, row, body):
        """Whether a finished turn whose job the sweep or a person let go meanwhile still settles it. A completed
        result is the answer the dismissal assumed would never come: the job was cancelled (the task was closed, or
        nothing was left to deliver) or queued to run again, and no later attempt has taken it. It settles the job
        completed and its answer is posted where the request was made, a closed task's own room included, and the
        queued rerun never happens. Any other outcome, or a job another attempt (`attempt_id`) or another computer now
        owns, is only filed (`file_result`)."""
        if body.outcome != "completed" or not self.current_owner(c, who, row) or row["stop_requested"]:
            return False
        job = c.execute("SELECT state FROM jobs WHERE id=? AND attempt_id=?", (row["job_id"], row["id"])).fetchone()
        return bool(job and job["state"] in ("cancelled", "queued"))

    @staticmethod
    def no_usage_reason(body):
        """Why a result with no token counts records 0 (hubdb.USAGE_REASONS). The runner's one-number `tokens_in`
        is not used for the run's usage: it is the session's size (bot_sessions), a resumed thread's whole total."""
        return "usage_limited" if body.limited else "usage_unreported"

    def run_usage(self, c, row, body):
        """The turn's usage columns from the tokens its runner counted, or None when it counted none.
        The model is the runner's choice for each primary/fallback portion. Unknown values stay
        unknown; the cost is the list-price estimate, empty for a model with no price, and `est_cost_low` says
        it may be low: a model whose long prompts cost more, without every request's counts (providers.estimate)."""
        used = body.usage
        if not used:
            return None
        def part(item):
            model, runtime = item.model, item.runtime
            catalog = providers.MODEL_BY_ID.get(model) or {}
            provider = catalog.get("provider") or providers.runtime_provider(runtime or catalog.get("runtime"))
            cost, low = providers.estimate(model, item.input_tokens, item.cached_tokens, item.output_tokens, item.requests)
            return {"input_tokens": item.input_tokens, "cached_tokens": item.cached_tokens,
                    "output_tokens": item.output_tokens, "model": model or None, "provider": provider or None,
                    "est_cost_usd": cost, "est_cost_low": int(low),
                    "billing": item.billing, "runtime": runtime or None, "harness": item.harness or None,
                    "effort": item.effort or None, "profile": item.profile_used or None,
                    **(gateway(model) if gateway_connection.is_tico(model) else {})}

        def gateway(model):
            # Ledger-ready: which gateway key and install a Gateway segment was spent under; never the key itself. The
            # key is the one its runner was handed (gateway_connection.run_credentials_for), not the one saved now.
            from .census import install_id_in
            return {"gateway_key_id": row["gateway_key_id"] or None, "install_id": install_id_in(c)}
        items = [item for item in (used.segments or [used])
                 if item.input_tokens or item.cached_tokens or item.output_tokens]
        segments = [part(item) for item in items]
        if not segments:
            return None
        result = dict(segments[-1])
        for key in ("input_tokens", "cached_tokens", "output_tokens"):
            result[key] = sum(p[key] for p in segments)
        # Legacy readers of turns see API spend only for a mixed-billing run. Usage and limits
        # consume the immutable segments and keep the subscription equivalent separate.
        billed = [p for p in segments if p["billing"] != "subscription"] or segments
        result["est_cost_usd"] = (sum(p["est_cost_usd"] for p in billed)
                                  if all(p["est_cost_usd"] is not None for p in billed) else None)
        result["billing"] = "subscription" if all(p["billing"] == "subscription" for p in segments) else "api"
        for key in ("model", "provider", "runtime", "harness", "effort", "profile"):
            if len({p[key] for p in segments}) > 1:
                result[key] = None
        result["segments"] = segments
        # The session's context size: the newest of the bot's own requests' prompt, when the last portion's runner
        # marked them (a subagent's request is not the session's). Unknown otherwise.
        own = [r for r in items[-1].requests or () if len(r) > 3 and r[3] == 1]
        result["prompt_tokens"] = own[-1][0] + own[-1][1] if own else None
        return result

    def stamp_reply(self, c, reply, row, aid, msg):
        """Mark a reply Tico posts for a run as that run's: counted as its write, with what it answered."""
        # A reply (to a person, or an answer to a bot's ask) is the run acting, though Tico posts it for
        # the run (backend/run_triggers.py). Only a newly written one: a reply already sent counts once.
        c.execute("UPDATE turns SET writes=coalesce(writes,0)+1 WHERE id=?", (aid,))
        # Which run wrote this and which messages it took in (the one it was started for and
        # what was folded into it), kept in the reply's refs for the frontends to show.
        handled = [msg["id"]] + [r[0] for r in c.execute(
            "SELECT i.message_id FROM attempt_inputs i JOIN messages m ON m.id=i.message_id "
            "WHERE i.attempt_id=? AND m.conversation_id=? AND m.deleted_at IS NULL ORDER BY m.rowid",
            (aid, msg["conversation_id"]))]
        refs = {**(H.message(c, reply["id"]).get("refs") or {}),
                "run": {"job_id": row["job_id"], "attempt_id": aid}, "answers": handled}
        c.execute("UPDATE messages SET refs_json=? WHERE id=?", (encode(refs), reply["id"]))
        H.held_queue_recheck(c, reply["id"])

    def file_result(self, c, row, body, held=False):
        """A result whose job this runner no longer speaks for: a review decided it, another
        attempt owns the work now, or the bot moved to another Mac. The run is recorded against
        its own attempt so the text, the tokens and the outcome survive — a reviewer can now read
        what the run actually produced — and the job is left exactly as its owner left it. The
        attempt stays `expired`, which is the true account of its lease and keeps late output
        archivable against it. `held`: the org rule check held its reply (forget_late_reply), and `body`
        carries only the note."""
        result = {**body.model_dump(exclude={"load_snapshot"}), **({"held_reply": True} if held else {})}
        prior = json.loads(row['result_json'] or '{}')
        # A late report cannot erase the saved-session decision that already requeued this job.
        for key in ('lease_continue', 'quota_continue', 'capacity_continue', 'source_continue', 'source_running', 'source_pending'):
            if key in prior:
                result[key] = prior[key]
        if prior.get('source_continuation') and (prior.get('source_running') or prior.get('source_continue')):
            result['source_continuation'] = prior['source_continuation']
        c.execute("UPDATE attempts SET final_text=?,result_json=? WHERE id=?",
                  (body.text, encode(result), row["id"]))
        from .performance import stamp_attempt
        stamp_attempt(c, row["id"], row["runner_id"], "end", body.load_snapshot)
        run_triggers.pushed(c, row["id"], row["runner_id"], body.pushes)
        run_triggers.rotation(c, row["id"], body.rotated)
        run_triggers.session(c, row["id"], body.session)
        H.turn_finish(c, H.KEEPER, row["id"], exit_code=body.outcome, summary=body.text[:2000],
                      usage=self.run_usage(c, row, body), usage_reason=self.no_usage_reason(body))
        H.event(c, H.KEEPER, "attempt.late-result", row["id"],
                {"bot": row["bot"], "job_id": row["job_id"], "outcome": body.outcome})
        return {"attempt_id": row["id"], "outcome": body.outcome, "message": None, "filed": True}

    @staticmethod
    def stopped(c, row, conversation_id, held=False):
        """A turn a person stopped is over: the bot is idle, told quietly who stopped it, and gives up its worktree wait.
        A turn a quarantine stopped says why, and that its run is back in the queue or, `held`, held for review
        (`quarantine_stops.requeues`)."""
        person = row["stop_requested"]
        benched = quarantine_stops.stopped_by(c, row)
        then = quarantine_stops.then(c, row, held)
        said = f"{benched}. {then}" if benched else f"Stopped by {stopper(c, person)}"
        settle_status(c, row["bot"], "idle", "", claimed=row["created"])
        # Quiet: the bot reads it with the room next time; it does not start a run of its own. A quarantined bot's
        # full held queue withholds only this line.
        H.unless_queue_full(c, lambda: H.say(c, H.KEEPER, "bot:" + row["bot"], said, conversation_id=conversation_id,
                                             kind="notice", refs={"quiet": True, "stopped_by": person,
                                                                  "turn_id": row["id"]}))
        reason = "restart" if benched and quarantine_stops.restarted(c, row) else "quarantine"
        H.event(c, H.KEEPER, "attempt.stopped", row["id"], {"bot": row["bot"], "job_id": row["job_id"], "by": person,
                                                            **({reason: benched, "requeued": not held}
                                                               if benched else {})})
        # A worktree this turn was waiting for is not wanted any more (backend/worktree_waits.py).
        from .worktree_waits import cancel_for_attempt
        cancel_for_attempt(c, row["id"], benched or f"Stopped by {stopper(c, person)}")

    def renew(self, c, who, aid, *, if_fresh=False, arrived=None):
        if if_fresh:
            lease = c.execute("SELECT lease_until FROM attempts WHERE id=?", (aid,)).fetchone()
            if not lease or lease[0] < H.shift(H.now(), seconds=max(20, self.store.settings.lease_seconds * 2 / 3)):
                return None
        row = self.attempt(c, who, aid, active=not if_fresh, arrived=arrived)
        now = H.now()
        seconds = self.store.settings.lease_seconds
        if if_fresh:
            remaining = (H.parse_ts(row["lease_until"]) - H.parse_ts(now)).total_seconds()
            if (row["state"] not in ("leased", "running") or not self.current_owner(c, who, row)
                    or remaining < max(20, seconds * 2 / 3)):
                return None
            diagnostic = c.execute("SELECT heartbeat_at,last_event_at,stalled_at FROM attempt_diagnostics "
                                   "WHERE attempt_id=?", (aid,)).fetchone()
            # Coalesce diagnostics with real lease writes; a long lease still records life at least each minute.
            last_seen = (max(row["created"], diagnostic["heartbeat_at"] or "", diagnostic["last_event_at"] or "")
                         if diagnostic else row["created"])
            if (diagnostic and diagnostic["stalled_at"]) or last_seen <= H.shift(now, seconds=-60):
                return None
            until, seconds = row["lease_until"], int(remaining)
        else:
            from . import run_diagnostics
            run_diagnostics.touch(c, aid)
            until = H.shift(now, seconds=seconds)
            c.execute("UPDATE attempts SET lease_until=? WHERE id=?", (until, aid))
        # The runner renews every few seconds, so a person's Stop rides on the answer it already reads. A turn whose
        # task ended meanwhile is stopped while it waits before starting; one preparing or under way runs on.
        from .login_waits import wait
        benched = quarantine_stops.stopped_by(c, row)
        return {"lease_until": until, "lease_seconds": seconds,
                **({"stop": True} if row["stop_requested"] or finished_before_start(c, row) and
                   (wait(c, aid) or start_places.is_waiting(c, aid)) else {}),
                **({"stop_reason": benched} if benched else {})}

    def stop(self, c, who, bot, attempt_id=None, redirect=None):
        """A person who manages the bot asks its running turn to stop. The runner interrupts the turn
        when it next renews the lease, and `complete` settles it as stopped rather than held for review.
        A turn whose lease already lapsed can be stopped by its id too: its late answer is then filed, not
        posted, and its job ends stopped rather than resumed.

        With `redirect`, the caller's own message still queued in a conversation that turn works in is
        claimed first once it ends, so the next turn starts with it in the same session (Stop and redirect,
        for a harness that cannot take input mid-turn)."""
        if not H.bot(c, bot):
            raise Problem("not_found", "Unknown bot", 404)
        if not (self.auth.operator(c, who, bot) or self.auth.bot_manager(c, who, bot)):
            raise Problem("forbidden", "Only a person who manages this bot can stop it", 403)
        rows = c.execute("SELECT id,stop_requested FROM attempts WHERE bot=? AND state IN ('leased','running') "
                         "AND (? IS NULL OR id=?) ORDER BY created", (bot, attempt_id, attempt_id)).fetchall()
        if not rows and attempt_id:
            # A turn whose lease lapsed while its computer slept may still hand in the answer the model went on to
            # finish (`settleable`, `settles_late`). A Stop on that turn, by its id, is recorded all the same, so the
            # answer is filed on its run rather than posted, while its job is still on it, uncertain or let go. A
            # job already queued to run again is not this turn's to stop; a Stop without an id only finds a live
            # turn, never an older lapsed one. A run the sweep holds for review is left to that review, which asks
            # what the run did (`reconcile_job`); once it has been let go, a Stop still files its late answer.
            rows = c.execute("SELECT a.id,a.stop_requested,j.state AS job_state FROM attempts a JOIN jobs j "
                             "ON j.id=a.job_id AND j.attempt_id=a.id WHERE a.bot=? AND a.id=? AND a.state='expired' "
                             "AND j.state IN ('uncertain','cancelled')", (bot, attempt_id)).fetchall()
            if rows and rows[0]["job_state"] == "uncertain" and c.execute(
                    "SELECT 1 FROM events WHERE action='job.held' AND target=?", (attempt_id,)).fetchone():
                raise Problem("held", "This run is held for review: resume or dismiss it there", 409)
        if not rows:
            raise Problem("not_running", "This bot is not running that turn", 409)
        if len(rows) > 1:
            # A bot running several tasks at once: stopping one of them takes its attempt id.
            raise Problem("ambiguous", "This bot is running more than one turn; say which to stop", 409,
                          extra={"attempts": [r["id"] for r in rows]})
        row = rows[0]
        if redirect:
            job = c.execute("SELECT j.id,j.state,m.from_actor FROM jobs j JOIN messages m ON m.id=j.message_id "
                            "WHERE j.message_id=? AND j.bot=? AND (m.conversation_id=(SELECT om.conversation_id "
                            "FROM jobs oj JOIN messages om ON om.id=oj.message_id JOIN attempts oa ON oa.job_id=oj.id "
                            "WHERE oa.id=?) OR m.conversation_id IN (SELECT conversation_id FROM attempt_conversations "
                            "WHERE attempt_id=?))", (redirect, bot, row["id"], row["id"])).fetchone()
            if not job or job["from_actor"] != who.actor:
                raise Problem("not_found", "Redirect with your own message to this turn's conversation", 404)
            if job["state"] != "queued":
                raise Problem("not_queued", "That message is already being answered", 409)
            c.execute("UPDATE jobs SET redirect=1 WHERE id=?", (job["id"],))
        if not row["stop_requested"]:
            c.execute("UPDATE attempts SET stop_requested=? WHERE id=?", (who.actor, row["id"]))
            H.event(c, who.actor, "attempt.stop", row["id"], {"bot": bot, **({"redirect": redirect} if redirect else {})})
        return {"attempt_id": row["id"], "stopping": True, **({"redirect": redirect} if redirect else {})}

    def start_place(self, c, who, aid, body):
        row = self.attempt(c, who, aid)
        if row["state"] != "leased":
            raise Problem("already_started", "This attempt was already started", 409)
        if body.waiting:
            start_places.waiting(c, aid, body.waiting)
        # Check before reserve clears the wait, including when the last renewal preceded task completion.
        if start_places.is_waiting(c, aid) and finished_before_start(c, row):
            return {"reserved": False, "stop": True}
        if body.waiting:
            return {"reserved": False, **({"stop": True} if row["stop_requested"] else {})}
        return start_places.reserve(c, row, self.store.settings)

    def started(self, c, who, aid, body):
        row = self.attempt(c, who, aid)
        if row["state"] != "leased":
            raise Problem("already_started", "This attempt was already started", 409)
        # Older workers did not negotiate a short start; their in-flight delivery lease stays valid.
        if start_places.is_waiting(c, aid) and finished_before_start(c, row):
            raise Problem("start_wait", "Task finished while this turn was waiting", 409)
        reserved = start_places.reserve(c, row, self.store.settings) if start_places.managed(c, aid) else {"reserved": not row["stop_requested"]}
        if not reserved["reserved"]:
            raise Problem("start_wait", reserved.get("waiting", "Start reservation expired or stopped"), 409)
        c.execute("UPDATE attempts SET state='running',started=? WHERE id=?", (H.now(), aid))
        from .performance import stamp_attempt
        stamp_attempt(c, aid, row["runner_id"], "start", body.load_snapshot)
        from . import run_diagnostics
        run_diagnostics.touch(c, aid, "started")
        c.execute("UPDATE jobs SET state='running' WHERE id=?", (row["job_id"],))
        msg = self.record_thread(c, who, row, body.thread_id)
        H.mark_delivered(c, H.KEEPER, msg)
        settle_status(c, row["bot"], "running", "Responding to queued work",
                      claimed=row["created"], task_id=work_task_id(H.message(c, msg)) or "")
        return {"started": True}

    def record_thread(self, c, who, row, thread_id):
        """The provider thread a running attempt is on: the run's, the bot's session under the model it ran, and the
        task's primary session. Returns the attempt's message id."""
        source = source_continuation(c, row['job_id'])
        if source:
            if source['thread_id'] != thread_id:
                raise Problem('wrong_thread', 'This job must continue its saved provider session', 409)
            self.record_source_thread(c, row, source)
            return c.execute('SELECT message_id FROM jobs WHERE id=?', (row['job_id'],)).fetchone()[0]
        c.execute("UPDATE attempts SET thread_id=? WHERE id=?", (thread_id, row["id"]))
        c.execute("UPDATE turns SET thread_id=? WHERE id=?", (thread_id, row["id"]))
        from .shared_bots import follow
        config = follow(c, row["bot"], json.loads(c.execute("SELECT config_json FROM bot_config WHERE bot=?", (row["bot"],)).fetchone()[0]))
        runtime, model = providers.bot_choice(c, self.store.settings, config)
        if ran := task_models.handed_out(c, row["id"]):
            # Its thread is the task model's, kept under that model, never as the bot's own session.
            runtime, model = ran["runtime"], ran["model"]
        if not ran and ran_another_model(c, self.store.settings, row):
            # A turn leased before a model change started the old model's thread: it is not the new model's.
            runtime = row["handed_runtime"] or runtime
        else:
            c.execute("INSERT INTO bot_sessions(bot,runtime,model,thread_id,runner_id,tokens_in,updated) "
                      "VALUES(?,?,?,?,?,0,?) ON CONFLICT(bot,runtime,model) DO UPDATE SET "
                      "thread_id=excluded.thread_id,runner_id=excluded.runner_id,updated=excluded.updated",
                      (row["bot"], runtime, model, thread_id, who.runner_id, H.now()))
        msg = c.execute("SELECT message_id FROM jobs WHERE id=?", (row["job_id"],)).fetchone()[0]
        H.task_session_started(c, row["bot"], row["lane"], msg, thread_id, runtime)
        return msg

    @staticmethod
    def fold_behind(c, row, aid, msg, task_id, followed, lanes, roots, lane):
        """A resumed run takes the work that queued behind it on its task while it was held (verdicts, subtask
        results, comments: the claim's fence, unfenced_sql, kept them back), so no separate turn waits after it to
        read them again (Dev 543). Each joins this turn as an input given in its prompt, already acknowledged, and its
        job settles with the turn (complete); a lane's other work, a command and a redirect keep their own turns."""
        rows = c.execute("SELECT m.*,j.id AS job_id FROM jobs j JOIN messages m ON m.id=j.message_id "
                         "JOIN conversations cv ON cv.id=m.conversation_id "
                         "WHERE j.bot=? AND j.state='queued' AND j.held=0 AND j.redirect=0 AND j.id<>? "
                         "AND m.deleted_at IS NULL AND coalesce(json_extract(m.refs_json,'$.command'),0)!=1 "
                         f"AND {queued_task_sql()}=? ORDER BY m.rowid",
                         (row["bot"], row["id"], task_id)).fetchall()
        lines, folded = [], []
        for queued in rows:
            if not privacy.message_readable(c, "bot:" + row["bot"], queued):
                continue
            message = H.message(c, queued["id"])
            if lanes and job_lane(c, row["bot"], followed, message, H.conversation(c, queued["conversation_id"]),
                                  lanes, roots) != lane:
                continue
            prior = c.execute("SELECT i.attempt_id,a.state FROM attempt_inputs i JOIN attempts a ON a.id=i.attempt_id "
                              "WHERE i.message_id=?", (queued["id"],)).fetchone()
            if prior and prior["state"] in ("leased", "running"):
                continue
            if prior and prior["attempt_id"] != aid:
                # As in inputs(): the run that already read this message keeps its tasks for privacy.
                privacy.record_moved_input(c, prior["attempt_id"], aid, queued)
            # An update then an insert, never an upsert: SQLite runs an upsert's triggers under its own conflict rule,
            # so the read index's INSERT OR IGNORE fails on a row the move's event just marked, and every claim does.
            c.execute("UPDATE attempt_inputs SET attempt_id=?,acked_at=? WHERE message_id=?", (aid, H.now(), queued["id"]))
            c.execute("INSERT OR IGNORE INTO attempt_inputs VALUES(?,?,?)", (aid, queued["id"], H.now()))
            c.execute("INSERT OR IGNORE INTO attempt_conversations VALUES(?,?)", (aid, queued["conversation_id"]))
            c.execute("UPDATE jobs SET state='input',attempt_id=? WHERE id=?", (aid, queued["job_id"]))
            H.mark_delivered(c, H.KEEPER, queued["id"])
            text = str(queued["body"] or "")
            lines.append(f"- From {queued['from_actor']} at {str(queued['created'])[11:16]} UTC:\n"
                         + (text if len(text) <= FOLD_TEXT else text[:FOLD_TEXT] + "\u2026"))
            folded.append(queued["job_id"])
        if not folded:
            return msg
        H.event(c, H.KEEPER, "job.fold_behind", aid, {"job_id": row["id"], "jobs": folded})
        return {**msg, "body": msg["body"] + "\n\nQueued on this task while this run was held, now part of this "
                "turn (no separate run follows for them):\n" + "\n".join(lines)}

    @staticmethod
    def record_source_thread(c, row, source):
        # A fallback belongs to this job, not the primary bot/task session recorded at start.
        c.execute("UPDATE attempts SET thread_id=?,result_json=json_set(coalesce(result_json,'{}'),"
                  "'$.source_running',json('true'),'$.source_pending',json('false'),"
                  "'$.source_continuation',json(?)) WHERE id=?", (source['thread_id'], encode(source), row['id']))
        c.execute('UPDATE turns SET thread_id=? WHERE id=?', (source['thread_id'], row['id']))

    def thread(self, c, who, aid, body):
        """A confirmed running provider session, persisted before work can need a lease continuation."""
        row = self.attempt(c, who, aid)
        if row["state"] != "running":
            raise Problem("not_running", "This attempt is not running", 409)
        if body.source_pending or body.source_continuation:
            computer = c.execute('SELECT capabilities_json FROM runners WHERE id=?', (row['runner_id'],)).fetchone()
            if SOURCE_CONTINUE not in json.loads(computer[0] or '[]'):
                raise Problem('capability', 'Saved fallback sessions require source continuation support', 409)
            if body.source_pending:
                # The primary's thread is no proof of where the fallback is. Fence recovery before launching it.
                c.execute("UPDATE attempts SET result_json=json_set(coalesce(result_json,'{}'),"
                          "'$.source_pending',json('true'),'$.source_running',json('false'),"
                          "'$.source_continuation',json('null')) WHERE id=?", (aid,))
            else:
                source = body.source_continuation.model_dump()
                if source['thread_id'] != body.thread_id:
                    raise Problem('wrong_thread', 'The saved provider session must match this thread', 409)
                self.record_source_thread(c, row, source)
        else:
            self.record_thread(c, who, row, body.thread_id)
        return {"thread_id": body.thread_id}

    @staticmethod
    def input_reply(c, attempt):
        aid = attempt["id"]
        rows = c.execute("SELECT m.* FROM attempt_inputs i JOIN messages m ON m.id=i.message_id "
                         "WHERE i.attempt_id=? AND i.acked_at IS NULL AND m.deleted_at IS NULL", (aid,)).fetchall()
        return {"attempt_id": aid, "messages": [H.message_view(c, row) for row in rows
                if privacy.message_readable(c, "bot:" + attempt["bot"], row, current=True)]}

    def idle_inputs(self, c, who, aid):
        """A poll with no eligible queued input, without restoring or changing an attempt."""
        return self.inputs(c, who, aid, if_idle=True)

    def inputs(self, c, who, aid, *, if_idle=False):
        """Add eligible queued work to a running turn when its runtime can steer.

        A follow-up in the turn's own conversation belongs to that turn. Across conversations,
        only a waited bot question may interrupt it because that has its own explicit answer
        channel. The runner calls this endpoint only for hosts that advertise steering support.
        Re-deliver until acknowledged; a crash remains uncertain, not a new turn.
        """
        attempt = self.attempt(c, who, aid, active=not if_idle)
        if if_idle and (attempt["state"] != "running" or attempt["lease_until"] <= H.now()
                        or not self.current_owner(c, who, attempt)):
            return None
        if attempt["state"] != "running":
            return {"messages": []}
        # A comment its author took back mid-turn (H.task_comment_delete) still started the turn: read its row,
        # whose words are already gone, so the turn keeps polling. Its follow-ups are read the normal way below.
        origin = H.message(c, c.execute("SELECT message_id FROM jobs WHERE id=?", (attempt["job_id"],)).fetchone()[0],
                           include_deleted=True)
        origin_task = work_task_id(origin, H.conversation(c, origin["conversation_id"]))
        origin_notice = wake_rule_engine.notice_only(origin)
        capabilities = c.execute("SELECT capabilities_json FROM runners WHERE id=?", (attempt["runner_id"],)).fetchone()
        lanes, roots = lanes_on(capabilities), roots_on(capabilities)
        if lanes:
            from .shared_bots import follow
            followed = follow(c, attempt["bot"], json.loads(
                c.execute("SELECT config_json FROM bot_config WHERE bot=?", (attempt["bot"],)).fetchone()[0]))
        # A task turn whose message stayed in the chat the task moved out of (H.give_task_room) also takes
        # follow-ups from the task's own room; the lane and privacy checks below apply to both.
        task_room = ((H.task(c, origin_task) or {}).get("conversation_id")
                     if origin_task and str(dict(attempt).get("lane") or "").startswith("task:") else None)
        taken = 0
        candidates = c.execute("SELECT m.*,j.id AS job_id FROM jobs j JOIN messages m ON m.id=j.message_id "
                             "JOIN conversations cv ON cv.id=m.conversation_id "
                             "WHERE j.bot=? AND j.state='queued' AND coalesce(json_extract(m.refs_json,'$.command'),0)!=1 "
                             # Queued, not steered: it goes in right after the turn (backend/delivery.py).
                             "AND coalesce(json_extract(m.refs_json,'$.delivery'),'')<>'queue' "
                             # A run the claim holds is not taken into a running turn until its wait ends, as the
                             # claim would not start it either.
                             "AND (j.held=0 OR (j.hold_reason=? AND j.hold_until<=?)) "
                             # A bot limited to one task takes nothing else into a running turn either.
                             f"AND {scoped_sql()} "
                             # A redirect replaces the turn being stopped, so it starts the next one instead.
                             "AND j.redirect=0 AND (m.conversation_id=("
                             "SELECT active_message.conversation_id FROM attempts active_attempt "
                             "JOIN jobs active_job ON active_job.id=active_attempt.job_id "
                             "JOIN messages active_message ON active_message.id=active_job.message_id "
                             "WHERE active_attempt.id=?) OR m.conversation_id=? OR (m.kind='ask' AND m.wait_s>0 "
                             "AND m.from_actor LIKE 'bot:%')) ORDER BY m.rowid",
                             (attempt["bot"], claim_holds.REASON, H.now(), aid, task_room)).fetchall()
        read_at = c.total_changes
        for i, row in enumerate(candidates):
            # Ten at a time, counted after the filters below: older work queued for other lanes must
            # never hide this lane's follow-ups.
            if taken >= 10:
                break
            if origin_notice and row["kind"] != "notice":
                continue                    # a notice result cannot answer a separate question; give it its own turn
            if i % PRIME_QUEUED == 0 and len(candidates) > 1 and c.total_changes == read_at:
                # Every poll of a running turn checks the whole backlog queued for its bot: a dozen reads per
                # message, a few times a second, unless the next ones are read together. Rows read before a write
                # here are never used to seed the cache.
                privacy.prime_queued(c, candidates[i:i + PRIME_QUEUED])
            if not privacy.message_readable(c, "bot:" + attempt["bot"], row):
                continue
            incoming_conversation = H.conversation(c, row["conversation_id"])
            incoming_task = work_task_id(H.message(c, row["id"]), incoming_conversation)
            if incoming_task and incoming_task != origin_task and row["kind"] == "notice":
                continue                    # a new task gets its own run, even in the same bot room
            if lanes and job_lane(c, attempt["bot"], followed, H.message(c, row["id"]), incoming_conversation,
                                  lanes, roots) != attempt["lane"]:
                continue                    # another lane's work is that lane's turn, never folded into this one
            if privacy.private_tasks(c, set(privacy.message_tasks(c, row, include_run=False)) - {origin_task}):
                # Folding another task's private message into this run would make the run private
                # too, shutting out whoever started it. It waits queued and gets its own run.
                continue
            if attempt["bot"] == H.FLEET_MAINTAINER:
                refs = json.loads(row["refs_json"] or "{}")
                origin = c.execute("SELECT j.message_id,m.from_actor FROM jobs j JOIN messages m ON m.id=j.message_id "
                                   "WHERE j.id=?", (attempt["job_id"],)).fetchone()
                origin_message = H.message(c, origin["message_id"], include_deleted=True)
                task_id = work_task_id(origin_message)
                task = H.task(c, task_id) if task_id else None
                requester = task["requester"] if task else origin["from_actor"]
                cancelled = bool(task and task["status"] == "closed" and refs.get("task") == task_id)
                # The person's next message in the chat this run answers is the same ask, carried on ("add it to
                # engineering"): it joins the run rather than queueing behind it.
                same_chat = (not task and row["conversation_id"] == origin_message["conversation_id"]
                             and row["kind"] == "say" and not refs.get("task"))
                related = (same_chat or row["in_reply_to"] == origin["message_id"] or refs.get("turn_id") == aid
                           or (task and task.get("request_id") and row["in_reply_to"] == task["request_id"])
                           or row["kind"] in ("answer", "steer") or refs.get("credential_saved"))
                if (str(row["from_actor"]).startswith("human:") and (row["from_actor"] != requester or not related)
                        or row["kind"] == "notice" and not cancelled):
                    continue
            # A message keeps one attempt_inputs row (message_id is the key). Its job being queued
            # again means a finished run handed it back on purpose (a requeued or deferred turn, a
            # resumed review), so that old row moves to this run, unacknowledged, and is delivered
            # here once. A row still held by another live run is left alone, and one already on
            # this run keeps its acknowledgement so it is not delivered twice.
            prior = c.execute("SELECT i.attempt_id,a.state FROM attempt_inputs i "
                              "LEFT JOIN attempts a ON a.id=i.attempt_id WHERE i.message_id=?",
                              (row["id"],)).fetchone()
            if prior and prior["attempt_id"] != aid and prior["state"] in ("leased", "running"):
                continue
            if if_idle:
                # This poll would take an input: that is the write transaction's to do. Until then a poll whose
                # queued work all waits for other runs or lanes answers here, without the write lock.
                return None
            if prior and prior["attempt_id"] != aid:
                # The earlier run did read this message, so it keeps the message's tasks for
                # privacy (attempt_tasks reads this event) once the row is no longer its own.
                privacy.record_moved_input(c, prior["attempt_id"], aid, row)
            # Not an upsert, as in fold_behind: its triggers would fail on the read index row the move just marked.
            c.execute("UPDATE attempt_inputs SET acked_at=CASE WHEN attempt_id=? THEN acked_at END,attempt_id=? "
                      "WHERE message_id=?", (aid, aid, row["id"]))
            c.execute("INSERT OR IGNORE INTO attempt_inputs VALUES(?,?,NULL)", (aid, row["id"]))
            c.execute("INSERT OR IGNORE INTO attempt_conversations VALUES(?,?)", (aid, row["conversation_id"]))
            claim_holds.clear(c, row["job_id"], "Its held run went into a running turn.")
            c.execute("UPDATE jobs SET state='input',attempt_id=? WHERE message_id=?", (aid, row["id"]))
            taken += 1
        # `attempt_id` lets the response check read these as the bot, as for the turn itself:
        # each message was just checked readable by the bot, and a private task's message is
        # never readable by the computer's own identity.
        return self.input_reply(c, attempt)

    def release_undelivered(self, c, aid, ids):
        """Put inputs the runner says it never gave the runtime back in the queue, before anything below
        settles this run's inputs with its outcome (a goal control's "completed" would mark them answered,
        a failure "uncertain"). Only this run's own unacknowledged inputs: an acknowledged one did reach
        the runtime. Its input row goes too, so the reply does not list it as answered and the next poll
        or claim treats it as new."""
        for mid in dict.fromkeys(ids or ()):
            if not c.execute("SELECT 1 FROM attempt_inputs WHERE attempt_id=? AND message_id=? AND acked_at IS NULL",
                             (aid, mid)).fetchone():
                continue
            c.execute("DELETE FROM attempt_inputs WHERE attempt_id=? AND message_id=?", (aid, mid))
            if c.execute("SELECT 1 FROM messages WHERE id=? AND deleted_at IS NOT NULL", (mid,)).fetchone():
                # Taken back before the runtime saw it: nothing is left to run, as H.task_comment_delete
                # cancels a queued one.
                c.execute("UPDATE jobs SET state='cancelled',attempt_id=NULL WHERE message_id=? AND attempt_id=?",
                          (mid, aid))
                continue
            c.execute("UPDATE jobs SET state='queued',attempt_id=NULL WHERE message_id=? AND attempt_id=?", (mid, aid))
            H.event(c, H.KEEPER, "job.input_returned", aid, {"message_id": mid})

    def acknowledge_input(self, c, who, aid, mid):
        self.attempt(c, who, aid)
        row = c.execute("SELECT * FROM attempt_inputs WHERE attempt_id=? AND message_id=?", (aid, mid)).fetchone()
        if not row:
            raise Problem("not_found", "This input is not assigned to this execution", 404)
        c.execute("UPDATE attempt_inputs SET acked_at=coalesce(acked_at,?) WHERE attempt_id=? AND message_id=?", (H.now(), aid, mid))
        H.mark_delivered(c, H.KEEPER, mid)
        return {"acknowledged": True}

    def events(self, c, who, aid, body):
        # Late output is archived with its historical attempt, never applied to current work.
        row = self.attempt(c, who, aid, active=False)
        seq = row["last_seq"]
        from .login_waits import stored
        # Fetch only the sequences in this bounded request, not the attempt's output history.
        existing_events = {r["seq"]: (r["kind"], r["payload_json"]) for r in c.execute(
            "SELECT seq,kind,payload_json FROM attempt_events WHERE attempt_id=? AND seq IN (" +
            ",".join("?" * len(body.events)) + ")", (aid, *(event.seq for event in body.events)))}
        inserts, goals = [], []
        last_event = None
        now = H.now()
        for event in body.events:
            clean = stored(event.kind, event.payload)
            payload = encode(clean)
            existing = existing_events.get(event.seq)
            if existing:
                if existing != (event.kind, payload):
                    raise Problem("event_conflict", "An event sequence was reused with different content", 409)
                continue
            if event.seq != seq + 1:
                raise Problem("event_gap", f"Expected event {seq + 1}", 409)
            if row["state"] in ("completed", "failed", "interrupted"):
                raise Problem("terminal", "Cannot append new output to a completed attempt", 409)
            existing_events[event.seq] = (event.kind, payload)
            inserts.append((aid, event.seq, event.kind, payload, now))
            last_event = (event.kind, clean)
            if event.kind == "goal" and row["lease_until"] > now and row["state"] != "expired":
                goals.append(event.payload)
            seq = event.seq
        c.executemany("INSERT INTO attempt_events(attempt_id,seq,kind,payload_json,created) VALUES(?,?,?,?,?)", inserts)
        # Managed workers report waits through start-place. Their delayed status stream cannot revoke a grant.
        # Legacy workers use the same latest-event predicate here as on restart, including clears and opt-outs.
        if row["state"] == "leased" and row["lease_until"] > now and not start_places.managed(c, aid):
            from .login_waits import wait
            pending = wait(c, aid)
            if pending:
                start_places.waiting(c, aid, "shared login" if pending.get("login") else "pre-start requirements")
            else:
                c.execute("UPDATE start_places SET wait_reason='',waiting_since=NULL WHERE attempt_id=? AND managed=0", (aid,))
        if last_event is not None:
            from . import run_diagnostics
            run_diagnostics.touch(c, aid, *last_event)
        for payload in goals:
            from .chat_goals import report
            report(c, row, payload)
        c.execute("UPDATE attempts SET last_seq=? WHERE id=?", (seq, aid))
        return {"ack_seq": seq, "historical": row["lease_until"] <= H.now() or row["state"] == "expired"}

    def background_done(self, c, who, aid, body):
        """The commands a turn left running when it ended on its wait have all ended: one wake for the bot, in the
        turn's task and conversation, with each command's exit and the end of its output. A second report of the same
        turn changes nothing."""
        from . import run_triggers
        from .watchers import defang, live
        row = self.attempt(c, who, aid, active=False)
        if c.execute("SELECT 1 FROM events WHERE action='attempt.background-done' AND target=?", (aid,)).fetchone():
            return {"woke": False}
        job = c.execute("SELECT message_id FROM jobs WHERE id=?", (row["job_id"],)).fetchone()
        origin = H.message(c, job["message_id"]) if job else None
        task = H.task(c, work_task_id(origin) or "") if origin else None
        lines = []
        for cmd in body.commands:
            ended = ("killed: ran over the run's time limit" if cmd.killed
                     else "exit code unknown" if cmd.exit is None else f"exit {cmd.exit}")
            what = cmd.description or "A background command"
            if cmd.ci_watch:
                what += " (CI watch: " + ", ".join(f"{k} {v}" for k, v in cmd.ci_watch.items()) + ")"
            tail = cmd.tail.strip()
            lines.append(f"- {what}: {ended}" + (f"\n  Last output:\n```\n{tail}\n```" if tail else ""))
        text = ("The background commands your earlier turn left running have ended. The runner kept them when that "
                "turn ended on its wait (Claude may have said they did not finish; they did).\n" + "\n".join(lines))
        woke = bool(origin) and (task is None or live(task))
        if woke:
            refs = {"task": task["id"]} if task else {}
            H.say(c, H.KEEPER, "bot:" + row["bot"], defang(text, c), kind="notice",
                  conversation_id=task["conversation_id"] if task else origin["conversation_id"],
                  refs=run_triggers.stamp({**refs, "wake": "background-done"}, "background_done", aid))
        H.event(c, H.KEEPER, "attempt.background-done", aid,
                {"bot": row["bot"], "task": task["id"] if task else None, "woke": woke,
                 "commands": [{"exit": cmd.exit, "killed": cmd.killed} for cmd in body.commands]})
        return {"woke": woke}

    @staticmethod
    def forget_held_reply(c, row, aid, conversation_id, note):
        """A reply the org rule check held is kept nowhere but its hold: the run's live text and raw capture keep only
        the note (a later capture upload is refused: `held_reply`), and the provider thread that wrote it is never
        resumed (the run forgets it, the bot's session row goes, the conversation's and the bot's shared thread's
        epochs move, so the next turn starts fresh)."""
        c.execute("DELETE FROM attempt_events WHERE attempt_id=? AND kind='delta'", (aid,))
        c.execute("UPDATE attempt_events SET payload_json=? WHERE attempt_id=? AND kind='message'",
                  (encode({"text": note}), aid))
        c.execute("DELETE FROM attempt_captures WHERE attempt_id=?", (aid,))
        c.execute("DELETE FROM deltas WHERE turn_id=?", (aid,))
        thread = c.execute("SELECT thread_id FROM attempts WHERE id=?", (aid,)).fetchone()[0]
        c.execute("UPDATE attempts SET thread_id=NULL WHERE id=?", (aid,))
        c.execute("UPDATE turns SET thread_id=NULL WHERE id=?", (aid,))
        if thread:
            c.execute("DELETE FROM bot_sessions WHERE bot=? AND thread_id=?", (row["bot"], thread))
        c.execute("INSERT INTO session_epochs VALUES(?,1,?) ON CONFLICT(conversation_id) DO UPDATE SET "
                  "epoch=epoch+1,updated=excluded.updated", (conversation_id, H.now()))
        bump_bot_session_epoch(c, row["bot"])
        H.event(c, H.KEEPER, "reply.held", aid, {"bot": row["bot"]})

    def forget_late_reply(self, c, row, rule_check):
        """A late result's reply that the org rule check would hold (a Block or Escalate rule it breaks) is filed as its
        held note only: no later turn posts it, so no hold is opened, and like a held completion the run keeps none of
        its text, live stream or thread (forget_held_reply). Returns the note, or None when nothing holds it."""
        from . import org_rule_check
        if not org_rule_check.holds(rule_check):
            return None
        broken = [r for r in rule_check["rules"] if r["id"] in rule_check["broken"] and r["if_broken"] != "warn"]
        note = org_rule_check.held_note(c, "bot:" + row["bot"], broken[0])
        found = c.execute("SELECT m.conversation_id FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.id=?",
                          (row["job_id"],)).fetchone()
        self.forget_held_reply(c, row, row["id"], found[0] if found else None, note)
        return note

    def complete(self, c, who, aid, body, rule_check=None):
        """`rule_check`: the org rule check of the reply (backend/org_rule_check.py), made before this write."""
        row = self.attempt(c, who, aid, final=True)
        # A turn a quarantine stopped ends stopped whatever it reports: a quarantined bot can post no answer, so one
        # finished in the seconds before its runner heard stays on the run. Its job goes back in the queue with the
        # rest of the turn's work, to run when the quarantine lifts, only when running it again repeats nothing
        # (`quarantine_stops.requeues`); otherwise the whole turn's work is held for review, where the kept answer
        # shows what the run already finished (Dev 573).
        benched = quarantine_stops.stopped_by(c, row)
        # A turn that finished before its runner heard a restart's Stop (its computer's subscription changed) stays a
        # finished turn: nothing to run again.
        if benched and body.outcome == "completed" and not quarantine_stops.restarted(c, row):
            body = body.model_copy(update={"outcome": "interrupted"})
        # `attempt` has already given `restore` its chance. What is left is a finished turn whose
        # lease lapsed under it: settle the job with it when the job is still waiting, and file it
        # against the attempt when it is not. Refusing it was how a run that opened three pull
        # requests came back as a phantom interruption.
        late = row["state"] == "expired"
        # The one turn that ends before it starts: a Tico model on a runner that can't run one (clients/gateway.py
        # NOT_ISOLATED). Settled now, so the bot says why once rather than its lease lapsing into a retry.
        # Likewise a turn sent away from a held login (`primary_held`) that found no fallback ready after all: nothing
        # ran, and it goes back to wait out the hold (backend/login_holds.py after_turn).
        # And a turn stopped while it waited for its shared login: older runners report failed, newer ones interrupted.
        # Either settles the Stop. A task done or closed meanwhile (`skipped`) also ends its waiting turn.
        skipped = body.outcome == "interrupted" and not row["stop_requested"] and finished_before_start(c, row)
        # A private meeting's turn its computer could make no copy of the repository for (runner-rs
        # sessions::PRIVATE_COPY_FAILED) never started, and would fail alike on every retry: it ends here, and the
        # meeting says why (live_meetings `meeting.bot_turn_failed`), never handed back to run again.
        no_copy = body.outcome != "completed" and not row["stop_requested"] and private_copy_failed(c, aid)
        unstarted = (row["state"] == "leased" and no_copy
                     or row["state"] == "leased" and body.outcome == "failed"
                     and (any(gateway.run_code(error) == gateway.NOT_ISOLATED for error in limit_errors(c, aid))
                          or body.primary_held and body.limited and not body.fallback)
                     or row["state"] == "leased" and body.outcome in ("failed", "interrupted") and bool(row["stop_requested"])
                     or row["state"] == "leased" and skipped
                     or row["state"] == "leased" and body.outcome == "interrupted" and body.yielded)
        # The runner reports a pre-start bailout as interrupted. A failed completion still requires
        # a start or one of the explicit exceptions above; it is not an unused lease being returned.
        # A planned update yield settles below, without counting as a failed preparation attempt.
        returned = (row["state"] == "leased" and not unstarted and body.outcome == "interrupted"
                    and not row["stop_requested"] and not skipped)
        if not late and row["state"] != "running" and not unstarted and not returned:
            raise Problem("not_started", "Acknowledge start before completing work", 409)
        if row["last_seq"] != body.last_seq:
            raise Problem("event_gap", "Upload all events before completing the attempt", 409)
        if returned:
            return self.hand_back(c, row, body)
        if late and not self.settleable(c, who, row) and not self.settles_late(c, who, row, body):
            held_late = self.forget_late_reply(c, row, rule_check)
            if held_late:
                body = body.model_copy(update={"text": held_late})
            filed = self.file_result(c, row, body, held=bool(held_late))
            if row["stop_requested"] and not benched and self.holds(c, who, row):
                # The Stop stands over the answer, and over a second run of the request too: the job ends as a
                # stopped one would, rather than waiting uncertain for `auto_reconcile` to resume it. Inputs the runtime
                # never received go back in the queue, as they do for a Stop the runner heard.
                self.release_undelivered(c, aid, body.undelivered)
                c.execute("UPDATE jobs SET state='cancelled' WHERE id=?", (row["job_id"],))
                msg = H.message(c, c.execute("SELECT message_id FROM jobs WHERE id=?", (row["job_id"],)).fetchone()[0],
                                include_deleted=True)
                self.stopped(c, row, msg["conversation_id"])
            return filed
        if late:
            H.event(c, H.KEEPER, "attempt.late-settled", aid, {"bot": row["bot"], "job_id": row["job_id"],
                                                                "outcome": body.outcome})
        self.release_undelivered(c, aid, body.undelivered)
        # A message its author took back during the turn (H.task_comment_delete) still started it: read its row.
        msg = H.message(c, c.execute("SELECT message_id FROM jobs WHERE id=?", (row["job_id"],)).fetchone()[0],
                        include_deleted=True)
        conv = H.conversation(c, msg["conversation_id"])
        actor = "bot:" + row["bot"]
        reply, held = None, False
        # Nothing is posted under a comment taken back mid-turn: the reply would stand where it was and could
        # restate it. The turn's own result is still recorded below.
        if body.text and body.outcome == "completed" and msg["deleted_at"] is None:
            text, notices = split_runner_notices(body.text)
            already = c.execute("SELECT id FROM messages WHERE from_actor=? AND in_reply_to=?",
                                (actor, msg["id"])).fetchone()
            # Tool sends need not name in_reply_to. Their authenticated mutation receipt,
            # unlike caller-supplied refs or matching historical text, proves this attempt sent it.
            same = c.execute(
                "SELECT m.id FROM idempotency i JOIN messages m "
                "ON m.id=coalesce(json_extract(i.response_json,'$.id'),json_extract(i.response_json,'$.message.id')) "
                "WHERE i.actor=? AND i.operation IN (?,?) "
                "AND m.conversation_id=? AND m.from_actor=? AND m.body=?",
                (actor + ":" + aid, "/api/v2/messages", "/api/v2/conversations/" + conv["id"] + "/messages",
                 conv["id"], actor, text)).fetchone()
            task = H.task(c, work_task_id(msg, conv)) if work_task_id(msg, conv) else None
            # A bot delivered through a desktop app: the computer may have reported the reply from the app first.
            same = same or app_delivery.echoes(c, conv["id"], actor, text, any_source=False)
            # A reply the turn already sent (a tool message) is not posted again; the rule check's verdict on the final
            # text still applies (held, the run keeps only the note), so `posting` decides only the post.
            posting = not already and not same
            if msg["kind"] in ("say", "ask", "steer") or task:
                target = msg["from_actor"]
                replyable = not str(target).startswith("bot:") or msg["kind"] == "ask"
                if target == H.KEEPER and task:
                    target = reply_target(msg, conv, task)
                    # A task in the bot's chat room always delivers, and so does one filed from a chat into
                    # its own room (H.task_from_chat). Any other dedicated task thread waits until the owner
                    # has marked the work done, or, for a finished turn that came in after its lease lapsed,
                    # until the task was closed under it: no later turn will answer that room.
                    if conv.get("kind") == "chat" or (conv.get("task_id") == task["id"] and H.task_from_chat(c, task["id"])):
                        replyable = target not in (H.KEEPER, actor)
                    else:
                        replyable = actor == task["owner"] and (
                            task["status"] == "done" or (late and task["status"] == "closed"))
                    # Woken by Tico on a task finished before it started, a turn that wrote nothing has no news: the
                    # person would only read "nothing changed" again. Its text stays on the run (Dev 359).
                    if posting and replyable and quiet_recheck(c, aid, body, task, msg):
                        replyable = False
                        H.event(c, H.KEEPER, "reply.withheld", aid, {"bot": row["bot"], "task": task["id"],
                                                                     "to": target, "reason": "no_change"})
                # A reply the turn already sent is not posted again, but the person waiting still hears of a hold.
                reaches = replyable
                replyable = replyable and posting
                if target not in (H.KEEPER, actor):
                    refs = {"turn_id": aid, **({"task": task["id"]} if task else {}),
                            **({"warnings": notices} if notices and msg["kind"] != "ask" else {})}
                    refused = False
                    try:
                        if replyable:
                            privacy.require_destination(c, Identity(actor, "bot", attempt_id=aid), target, conv["id"],
                                                        refs, msg)
                    except Problem as refusal:
                        if refusal.code != "privacy":
                            raise
                        refused = True
                        # The run is done; only its reply may not go here. The result stays on the
                        # run, which only the private task's own parties can read, and the person
                        # who expected the reply is told, with nothing from the task named.
                        if H.is_human(target):
                            H._write_message(c, H.KEEPER, target, f"{row['bot']} finished, but its reply drew on "
                                             "a private task you can't read, so it was not posted here.",
                                             conv, "notice", {"withheld": {"bot": row["bot"]}}, None, None)
                    # The org rule check's verdict (backend/org_rule_check.py), whether the reply is posted now or
                    # waits on the run until Done: held, the run keeps only the note, never the reply.
                    rule_gate, asked_text = None, body.text
                    if rule_check and not refused:
                        from . import org_rule_check
                        checked = rule_check if target in rule_check["recipients"] else \
                            {**rule_check, "outcome": "unchecked", "error": "target_changed"}
                        rule_gate = org_rule_check.apply(c, checked, text, {"attempt": aid, "message": msg["id"],
                                                                           "text": body.text}, tell=True)
                    if rule_gate and rule_gate.get("held"):
                        held = True
                        note = org_rule_check.held_note(c, actor, rule_gate["rule"])
                        if H.is_human(target) and reaches:
                            H._write_message(c, H.KEEPER, target, note, conv, "notice",
                                             {"org_rule": {"hold": rule_gate["hold_id"], "bot": row["bot"]},
                                              **({"task": task["id"]} if task else {})}, None, None)
                        body = body.model_copy(update={"text": note})
                        self.forget_held_reply(c, row, aid, conv["id"], note)
                        replyable = False
                    elif rule_gate and rule_gate.get("warn"):
                        text = org_rule_check.warned_text(rule_gate, text)
                        body = body.model_copy(update={"text": org_rule_check.warned_text(rule_gate, body.text)})
                    if replyable and not refused:
                        def post_reply():
                            if msg["kind"] == "ask":
                                # The pick is resolved on the reply's own words; a warning goes on what the asker
                                # reads (task_review.reply_answer).
                                from .task_review import reply_answer
                                from . import org_rule_check
                                sent = reply_answer(c, self.auth, Identity(actor, "bot", attempt_id=aid), msg,
                                                    asked_text, org_rule_check.warn_line(rule_gate))
                            else:
                                sent = H.say(c, actor, target, text, conversation_id=conv["id"], in_reply_to=msg["id"],
                                             refs=refs)
                            if sent:
                                self.stamp_reply(c, sent, row, aid, msg)
                            return sent
                        # A paused or quarantined bot's full held queue withholds only this answer: the run is
                        # still done, its text kept on the attempt, so the runner's completion is not refused.
                        reply, full = H.unless_queue_full(c, post_reply)
                        if full:
                            H.event(c, H.KEEPER, "reply.withheld", aid, {
                                "bot": row["bot"], "to": target, "reason": "queue_full",
                                **({"task": task["id"]} if task else {})})
            if task and task["owner"] == actor and not held:
                # Record the owner's final verdict even when the reply is withheld until Done: from the text as
                # delivered, after the rule check, never from a held reply.
                H.record_verdict(c, actor, task, text=text)
        # A turn on a check subtask ran on the check's model, not the bot's (backend/check_models.py).
        # Decided by what the claim handed out: a check handed to another bot mid-turn still ran on the check's model.
        check_task, check = check_models.handed_out(c, aid)
        # Quota is a terminal provider pause even when it happens halfway through fallback work. Reuse the
        # restart safety judgement: local work may continue, while consumed approvals and outside actions stay held.
        failure = None
        source_pause = False
        quota_acted = False
        notice_only = False
        if body.outcome in ('failed', 'interrupted') and not row['stop_requested']:
            failure = run_backoff.classify(c, aid, body)
            errors = limit_errors(c, aid)
            notice_only = only_a_notice(c, aid, row, body)
            if notice_only and body.continues and errors and all(login_holds.capacity(e) for e in errors):
                # The runner took the busy notice for work and kept the thread; the turn never ran, so it just waits.
                body = body.model_copy(update={'limited': True, 'continues': False})
            if body.limited and not any(gateway.run_code(e) for e in errors) and not (errors and all(login_holds.capacity(e) for e in errors)):
                job = c.execute('SELECT * FROM jobs WHERE id=?', (row['job_id'],)).fetchone()
                facts = retry_facts(c, job, row['started'], H.now())
                quota_acted = bool(facts['consumed'] or outside_actions(c, job, row['started'], H.now()))
                source_pause = not quota_acted
                if source_pause:
                    worked = not notice_only and c.execute("SELECT 1 FROM attempt_events WHERE attempt_id=? AND kind IN ('tool','message','delta') LIMIT 1", (aid,)).fetchone()
                    body = body.model_copy(update={'outcome': 'interrupted' if worked else body.outcome, 'retryable': True})
        c.execute("UPDATE attempts SET state=?,finished=?,final_text=?,result_json=? WHERE id=?",
                  (body.outcome, H.now(), body.text,
                   encode({**body.model_dump(exclude={"load_snapshot"}), **({'source_pause': 'quota'} if source_pause else {}),
                           **({"check": check["runtime"]} if check else {}),
                           # A held reply's marker: its capture is never taken again (attempt_capture).
                           **({"held_reply": True} if held else {})}), aid))
        quota_continue = False
        if source_pause and not body.fallback and row['thread_id'] and worked:
            computer = c.execute('SELECT capabilities_json FROM runners WHERE id=?', (row['runner_id'],)).fetchone()
            quota_continue = capacity_continue_on(computer)
            if quota_continue:
                c.execute("UPDATE attempts SET result_json=json_set(result_json,'$.quota_continue',json('true')) WHERE id=?", (aid,))
        if body.outcome == "completed":
            H.notes_settle(c, aid)        # read for good; a failed or interrupted run gives its notes back
        from .performance import stamp_attempt
        stamp_attempt(c, aid, row["runner_id"], "end", body.load_snapshot)
        # A usage-limited turn did nothing and had no effects, so the job goes back in the
        # queue however often it happens; the bot cools off (claim() honours the window) and
        # a third limit in a row waits longer and tells a person in Needs attention. Only
        # interrupted or failed work, which may have acted, is held for review.
        # A turn a person stopped is over by their choice: nothing to retry and nothing to review.
        stopped = bool(row["stop_requested"]) and body.outcome != "completed" or skipped
        # A quarantine's Stop is no choice to drop the work: what it held back waits for review, not cancelled.
        held_back = bool(benched) and not quarantine_stops.requeues(c, row)
        # A provider limit inside any error is the usage limit's, whatever the runner flagged; a prompt too large to
        # send starts the next run in a fresh session (backend/run_backoff.py).
        if body.outcome == "failed" and not stopped:
            H.hook(c, "bot.run_failed", row["bot"], lambda: {"bot": row["bot"], "attempt_id": aid,
                                                              "error_class": failure if isinstance(failure, str) else None},
                   H.KEEPER, revision=aid)
        limited = body.outcome != "completed" and body.limited and not stopped
        # A model at capacity (backend/login_holds.py capacity) is a busy moment, not the login's quota: kept with the
        # run, so Health and a check's next model never count it as quota. Only a refusal before the bot did anything
        # is a turn that never ran; after a message or a tool it may have acted, and a replay could repeat that, so it
        # is saved for review like any other failed run, whatever an older runner reported.
        errors = limit_errors(c, aid)
        busy = (body.outcome != "completed" and not stopped and bool(errors)
                and all(login_holds.capacity(error) for error in errors))
        busy_acted = busy and not notice_only and c.execute("SELECT 1 FROM attempt_events WHERE attempt_id=? "
                                                            "AND kind IN ('message','tool') LIMIT 1", (aid,)).fetchone() is not None
        # Unless the runner kept its thread (`continues`): then the job goes back in the queue, and after the capacity
        # hold its next turn continues that thread, which knows what it did. Once only: a continuation the model's
        # capacity stops again is held for review, even before it acts (capacity_continuation).
        continuing = continuation_thread(c, row["job_id"])
        repeated_capacity = busy and bool(continuing)
        continues = busy_acted and body.continues and bool(row["thread_id"]) and not continuing
        if busy and (body.limited or continues):
            c.execute("UPDATE attempts SET result_json=json_set(result_json,'$.limit_source',json(?)) WHERE id=?",
                      (encode({"kind": login_holds.CAPACITY_KIND, "notice": errors[0][:300]}), aid))
        if continues:
            # Recorded as the capacity limit it is, so the bot's cooldown (last_limit) holds the job for the wait.
            c.execute("UPDATE attempts SET result_json=json_set(result_json,'$.limited',json('true'),"
                      "'$.capacity_continue',json('true')) WHERE id=?", (aid,))
        source = body.source_continuation
        if (source_pause or continues) and source and body.fallback and source.harness == body.fallback and not notice_only:
            computer = c.execute('SELECT capabilities_json FROM runners WHERE id=?', (row['runner_id'],)).fetchone()
            if computer and SOURCE_CONTINUE in json.loads(computer[0] or '[]'):
                c.execute("UPDATE attempts SET result_json=json_set(result_json,'$.source_continue',json('true')) WHERE id=?", (aid,))
        limited = limited and not busy_acted or continues
        if repeated_capacity:
            limited = False
            c.execute("UPDATE attempts SET result_json=json_set(result_json,'$.limited',json('false')) WHERE id=?", (aid,))
        # A limit its fallback could not take over is the limit's too (hop_failed): queued again to wait for the reset,
        # never held for a person, and recorded as a limit so the cooldown and the login's hold read it.
        hopped = (not limited and not stopped and not check
                  and hop_failed(c, aid, body, row["started"] or row["created"], H.now()))
        if hopped:
            limited = True
            c.execute("UPDATE attempts SET result_json=json_set(result_json,'$.limited',json('true'),"
                      "'$.fallback_failed',json('true')) WHERE id=?", (aid,))
        # Same reasoning, different cause: the runner reports `retryable` when the runtime was
        # refused before the turn began because it could not renew its own sign-in, and only
        # when that turn produced no message and used no tool. Nothing happened, so requeue it
        # rather than blocking the bot behind an uncertain job until a person clears it.
        retryable = body.outcome != "completed" and body.retryable and not limited and not stopped
        yielded = body.outcome == "interrupted" and body.yielded and not stopped
        # The general case of the same thing: a turn that failed having streamed nothing at all,
        # no text, no tool, no token count, only the runtime's own error. When a runtime
        # answered every turn of several bots with "Internal error" at the first event, the
        # jobs it left `uncertain` held those bots until a person cleared each one. Nothing ran,
        # so it goes back in the queue; a job that fails this way NO_EFFECT_TRIES times is not
        # unlucky but broken, and waits for a person as before.
        # Codex and Claude never report their tool calls, so for them silence is only evidence
        # together with speed: the Grok failures came back within a second or two of starting.
        # A run whose Tico model (its own, or the fallback it names) the Tico AI Gateway refused ends with the Gateway's
        # code (clients/gateway.py): asking again gets the same answer for a refusal of the request as sent, and an
        # unavailable model is worth a retry.
        tico_code, tico_error = "", ""
        if body.outcome == "failed" and not stopped and body.auth_rejected is None:
            tico_error = next((error for error in limit_errors(c, aid) if gateway.run_code(error)), "")
            if tico_error and gateway_connection.ran_tico(gateway_connection.handed(c, aid), body):
                tico_code = gateway.run_code(tico_error)
        gateway_connection.forget(c, aid)
        silent = False
        # A continuation already acted in its earlier turn: a quiet missing-session failure is held, never retried.
        if body.outcome == "failed" and not limited and not retryable and not stopped and not continuing:
            kinds = {r[0] for r in c.execute("SELECT DISTINCT kind FROM attempt_events WHERE attempt_id=?", (aid,))}
            tries = c.execute("SELECT count(*) FROM attempts WHERE job_id=?", (row["job_id"],)).fetchone()[0]
            quick = (row["started"] or row["created"]) >= H.shift(H.now(), seconds=-NO_EFFECT_WITHIN)
            silent = ((kinds <= SILENT_KINDS or notice_only) and (quick or tico_code == gateway.UNAVAILABLE)
                      and tries < NO_EFFECT_TRIES and tico_code not in gateway.FINAL)
            if silent and not notice_only and positive_effect(c, aid, body):
                silent = False
        # A refused key or sign-in blocks this profile until its credential changes.
        # Only runs with no evidence of actions may wait queued rather than held for review.
        rejected = body.outcome == "failed" and body.auth_rejected is not None and not stopped
        if retryable and body.subscription_unavailable:
            from .repositories import save_metadata
            config = c.execute("SELECT config_json FROM bot_config WHERE bot=?", (row['bot'],)).fetchone()
            config = json.loads(config[0]) if config else {}
            primary_runtime = providers.bot_choice(c, self.store.settings, config)[0]
            save_metadata(c, f"subscription-unavailable:{row['bot']}",
                          {**body.subscription_unavailable.model_dump(), "runner_id": row["runner_id"],
                           "primary_runtime": primary_runtime, "primary_harness": config.get("harness")})
        # A refused sign-in invalidates readiness even mid-turn. Model output or tools
        # mean the run may have acted; signing in cannot make replay safe.
        rejected_acted = rejected and c.execute(
            "SELECT 1 FROM attempt_events WHERE attempt_id=? AND kind IN ('delta','message','tool','tokens') LIMIT 1",
            (aid,)).fetchone() is not None
        if rejected:
            silent = False
        # What the bot says about a run the Gateway refused, in its words rather than as a crash.
        refused = gateway.run_status(tico_code, tico_error) if tico_code and not (limited or retryable or rejected) else ""
        reset_wait = (not stopped and not check and empty_fallback_interruption(c, self.store.settings, row, body))
        if reset_wait:
            # Do not try this empty fallback again in the same primary hold window.
            hold = own_login_hold(c, self.store.settings, row['bot'])
            config_row = c.execute("SELECT config_json FROM bot_config WHERE bot=?", (row['bot'],)).fetchone()
            hold.setdefault('failed_fallback', {})[row['bot']] = fallback_mark(json.loads(config_row[0] or '{}'))
            login_holds._save(c, hold)
        timeout_cut = run_limit_minutes(c, aid)
        requeue = not rejected_acted if rejected else limited or retryable or silent or yielded or reset_wait
        if timeout_cut:
            requeue = False  # every run-budget cut goes through the same effect and consecutive-cut judgement
        if quota_acted or (body.outcome == 'interrupted' and body.primary_held and body.fallback and not (reset_wait or source_pause)):
            requeue = False  # a yield or limit flag cannot turn unknown fallback effects into a safe retry
        if repeated_capacity:
            requeue = False
        no_copy = no_copy and not stopped    # (above) ended, not queued again nor left for auto_reconcile to resume
        if no_copy:
            requeue = False
        c.execute("UPDATE jobs SET state=? WHERE id=?",
                  ("completed" if body.outcome == "completed" else "uncertain" if held_back else "queued" if benched
                   else "cancelled" if stopped or no_copy
                   else "queued" if requeue else "uncertain", row["job_id"]))
        if tico_code == gateway.NOT_ISOLATED and not requeue:
            # Held as it is: never started, so `auto_reconcile` would resume it onto the same runner each sweep. It
            # waits for a person to move the bot or change its model, then retry or dismiss it.
            H.event(c, H.KEEPER, "job.held", aid, {"job_id": row["job_id"], "tools": [], "reason": tico_code})
        live_ref = (msg.get("refs") or {}).get("live_meeting")
        if (isinstance(live_ref, dict) and msg.get("from_actor") == H.KEEPER
                and msg.get("to_actor") == "bot:" + row["bot"]):
            from .live_meetings import complete_runner_turn
            complete_runner_turn(c, live_ref.get("meeting_id"), live_ref.get("turn_id"), row["bot"],
                                 body.text if body.outcome == "completed" else "", body.outcome,
                                 requeued=requeue, attempt_id=aid,
                                 failure=PRIVATE_COPY_FAILURE if no_copy else None, runner_id=row["runner_id"])
        turn_task = work_task_id(msg, conv)
        for incoming in c.execute("SELECT i.message_id,m.kind FROM attempt_inputs i "
                                  "JOIN messages m ON m.id=i.message_id WHERE i.attempt_id=?", (aid,)).fetchall():
            message = H.message(c, incoming["message_id"], include_deleted=True)
            if message["deleted_at"]:
                # Taken back mid-turn: nothing is left to answer, defer or hold for review, so its job ends with
                # the turn. A deleted comment's queued job is cancelled the same way (H.task_comment_delete).
                c.execute("UPDATE jobs SET state=? WHERE message_id=?",
                          ("completed" if body.outcome == "completed" else "cancelled", incoming["message_id"]))
                continue
            answered = incoming["kind"] != "ask" or bool(H.answers_to(c, [incoming["message_id"]]))
            task_id = work_task_id(message, H.conversation(c, message["conversation_id"]))
            deferred = False
            if task_id and task_id != turn_task:
                task = H.task(c, task_id)
                outcome = c.execute("SELECT 1 FROM task_events WHERE task_id=? AND actor=? AND ts>=? "
                                    "AND (field='note' OR (field='status' AND new IN ('done','closed','declined'))) LIMIT 1",
                                    (task_id, actor, row["started"] or row["created"])).fetchone()
                deferred = bool(task and task["status"] in H.ACTIVE_STATUSES and not outcome)
            state = ("queued" if deferred else "completed" if body.outcome == "completed" and answered else
                     "uncertain" if held_back else "queued" if benched else "cancelled" if stopped
                     else "queued" if requeue else "uncertain")
            c.execute("UPDATE jobs SET state=?,attempt_id=CASE WHEN ? THEN NULL ELSE attempt_id END WHERE message_id=?",
                      (state, deferred, incoming["message_id"]))
        if body.outcome == "completed":
            # Notices that queued in this turn's conversation before it was claimed were in the
            # room page it was given, so the turn has read them. Leaving their jobs queued costs
            # one more run each to read the same thing again: a bot could accumulate
            # dozens of Slack digests and hourly occurrences stacked this way. A bot's chat
            # room carries notices for many tasks, so only the ones about this turn's own task
            # (or, for a turn with no task, the ones about none) count as read.
            turn_task = work_task_id(msg, conv)
            for extra in c.execute("SELECT j.id,j.message_id FROM jobs j JOIN messages m ON m.id=j.message_id "
                                   "WHERE j.bot=? AND j.state='queued' AND m.kind='notice' "
                                   "AND m.conversation_id=? AND m.created<=? AND j.id<>?",
                                   (row["bot"], conv["id"], row["created"], row["job_id"])).fetchall():
                if work_task_id(H.message(c, extra["message_id"]), conv) != turn_task:
                    continue
                c.execute("UPDATE jobs SET state='completed',attempt_id=? WHERE id=?", (aid, extra["id"]))
                H.mark_delivered(c, H.KEEPER, extra["message_id"])
                H.event(c, H.KEEPER, "job.coalesce", extra["id"], {"attempt_id": aid, "job_id": row["job_id"]})
        if body.outcome == "completed":
            # Each next-run task this run carried and left: back to the next run, unless it was worked or deferred.
            quiet_work.settle(c, aid, row["bot"], row["started"] or row["created"])
        # A push from the task's worktrees is the run acting too, though only its runner saw it.
        run_triggers.pushed(c, aid, row["runner_id"], body.pushes)
        run_triggers.rotation(c, aid, body.rotated)
        run_triggers.session(c, aid, body.session)
        # The runner's notices (a file left out of the commit) are shown beside the reply, not as the turn's summary.
        H.turn_finish(c, H.KEEPER, aid, exit_code=body.outcome, summary=split_runner_notices(body.text)[0][:2000],
                      usage=self.run_usage(c, row, body), usage_reason=self.no_usage_reason(body))
        from . import automations
        automations.turn_done(c, row, body.outcome, body.text)   # an automation that started this turn is charged it
        from . import counsel
        # Counsel's completed turn on a document's read task finishes that task.
        counsel.turn_done(c, row, "stopped" if stopped else body.outcome, work_task_id(msg, conv), body.text)
        # The checkpoint goes on the task whose session the turn ran in: a subtask's turn runs in its session
        # root's lane (`task:<root>`, set at claim), so it checkpoints that root, never the subtask, even if
        # the subtask moved during the turn. A lane naming a task this bot does not own checkpoints nothing.
        turn_task_id = work_task_id(msg, conv)
        lane_task = row["lane"][len("task:"):] if turn_task_id and str(row["lane"] or "").startswith("task:") else ""
        if lane_task and lane_task != turn_task_id:
            root = H.task(c, lane_task)
            checkpoint_task = root if root and root["owner"] == "bot:" + row["bot"] else None
        else:
            checkpoint_task = H.task(c, turn_task_id) if turn_task_id else None
        if checkpoint_task:
            bot_config = c.execute("SELECT config_json FROM bot_config WHERE bot=?", (row["bot"],)).fetchone()
            # The followed config, as the turn's runner keyed it (a branch's session keys come from its original).
            from .shared_bots import follow
            followed_config = follow(c, row["bot"], json.loads(bot_config[0]) if bot_config else {})
            own = sessions.session_key(followed_config, row["bot"],
                                       checkpoint_task["id"], conv["id"]) == "task:" + checkpoint_task["id"]
            # Only a turn in the task's own session leaves a checkpoint: a shared bot thread carries
            # other tasks. And a session that once took in another private task may carry it in every
            # later turn, even one that does not, so that task never gets a checkpoint again (a private
            # subtask worked in its root's session counts as another task here).
            if not own or checkpoint_task.get("checkpoint_blocked"):
                checkpoint_task = None
            elif not checkpoint_safe(c, aid, checkpoint_task):
                c.execute("UPDATE tasks SET checkpoint_blocked=1 WHERE id=?", (checkpoint_task["id"],))
                checkpoint_task = None
        # A turn that only said why it stopped leaves the task where it stood: its line is no place to pick the work up
        # from, and a checkpoint would have the same-state rule hold the stall checks.
        if (body.outcome == "completed" or yielded or source_pause) and not notice_only and body.text and checkpoint_task:
            # Where the task stood after this turn: a later turn that cannot resume this session
            # starts from it (runner prompt). It is not the task's activity, so `updated` stays.
            started = (c.execute("SELECT started FROM turns WHERE id=?", (aid,)).fetchone() or [None])[0]
            c.execute("UPDATE tasks SET checkpoint=?,checkpoint_at=?,checkpoint_by=?,checkpoint_state=? WHERE id=?",
                      (split_runner_notices(body.text)[0].strip()[:CHECKPOINT_CHARS], H.now(), actor,
                       wake_rules.settled_state(c, checkpoint_task, row["bot"], started, aid) if started else None,
                       checkpoint_task["id"]))
        usage_limits.after_run(c, row["bot"], self.store.settings.owner_email)
        finance_run_ended(c, row["job_id"], aid)
        config = c.execute("SELECT config_json FROM bot_config WHERE bot=?", (row["bot"],)).fetchone()
        runtime, model = providers.bot_choice(c, self.store.settings, json.loads(config[0]) if config else {})
        # A turn on the bot's task model ran on that model (backend/task_models.py): its usage and limit are that login's.
        ran = None if check else task_models.handed_out(c, aid)
        if check:
            runtime, model = check["runtime"], check["model"]
        elif ran:
            runtime, model = ran["runtime"], ran["model"]
        # The bot was moved to another model while this turn ran on the old one: its limit is not the new model's login.
        # A move to another model on the same runtime keeps the login, so the turn's limit is the login's either way.
        # A task-model turn did not run on the bot's own model, so a move of that model says nothing about it.
        superseded = (not check and not ran and ran_another_model(c, self.store.settings, row)
                      and row["handed_runtime"] != runtime)
        if row["thread_id"] and body.tokens_in is not None:
            c.execute("UPDATE bot_sessions SET tokens_in=?,updated=? WHERE bot=? AND runtime=? AND model=? AND thread_id=?",
                      (int(body.tokens_in), H.now(), row["bot"], runtime, model, row["thread_id"]))
        login_hold = {}
        # The Tico AI Gateway's cap and address block are the company key's, not a provider login's: they keep their
        # own waits (reset_cooldown).
        gateway_wait = tico_code == gateway.RATE_LIMITED or any(gateway.cap_reached(e) for e in limit_errors(c, aid))
        if not check and not superseded and not stopped and not gateway_wait and (limited or body.outcome == "completed"):
            login_hold = login_holds.after_turn(c, row["runner_id"], row["bot"], "",
                                                runtime, body, aid, H.now(),
                                                hopped=fallback_mark(json.loads(config[0] or "{}") if config else {})
                                                if hopped else None)
        elif check and not stopped and not gateway_wait and (limited or body.outcome == "completed"):
            # A check's own schedule stays its own (check_models.hold below), but a company subscription it signed in
            # with is one login on every computer given it: its limit holds it there too, and a check that worked on it
            # clears it.
            login_holds.after_turn(c, row["runner_id"], row["bot"], "", runtime, body, aid, H.now(),
                                   subscriptions_only=True)
        # A nightly memory tidy pass never waits for a reset or retries: its limit ends it as skipped (below).
        memory_pass_turn = bool(check and check.get("memory_pass"))
        if not memory_pass_turn and (reset_wait or unstarted and body.primary_held and body.limited and not body.fallback):
            login_hold = hold_job_until_reset(c, row, own_login_hold(c, self.store.settings, row['bot']),
                                'No fallback could take this claim; waiting for the primary login reset.') or login_hold
        if not memory_pass_turn and source_pause and login_hold and (quota_continue or body.fallback or not fallback_choice(
                c, self.store.settings, row['runner_id'], row['bot'], json.loads(config[0] or '{}') if config else {})):
            hold_job_until_reset(c, row, login_hold, 'Provider quota paused this turn; continue its saved session at reset.')
        # A turn signed in with a company subscription: a refusal is that subscription's, never the computer's login.
        subscription = model_subscriptions.turn_rejected(
            c, self.store.settings, aid, row["bot"], body.auth_rejected.runtime, body.auth_rejected.reason,
            turn_task_id) if rejected else None
        if rejected:
            if not subscription:
                self.mark_rejected(c, row["runner_id"], body.auth_rejected.runtime, body.auth_rejected.reason,
                                   body.profile_used, row["bot"])
            H.event(c, H.KEEPER, "attempt.auth_rejected", aid,
                    {"bot": row["bot"], "runtime": body.auth_rejected.runtime, "reason": body.auth_rejected.reason,
                     **({"subscription": subscription["id"]} if subscription else {})})
            if not subscription:
                # The task it ran for waits on a card for whoever signs the computer in (backend/reconnects.py).
                reconnects.login_refused(c, self.store.settings, row["bot"], row["runner_id"],
                                         body.auth_rejected.runtime, turn_task_id, body.profile_used or "")
        elif body.outcome == "completed" and not check and not body.fallback:
            # A run that completed on the bot's own login (not a fallback's) shows that profile's login works, or
            # the subscription it was handed.
            if model_subscriptions.handed(c, aid, runtime):
                model_subscriptions.turn_worked(c, aid, runtime)
            else:
                reconnects.login_works(c, row["runner_id"], [(runtime, body.profile_used or "")])
        if body.fallback:
            # The runner ran this turn on the bot's fallback harness: countable here, and
            # named in Runs, so a primary that keeps failing is noticed. `primary_runtime` is what the bot is
            # set to and did not do the work; `model` is what did, when the runner counted it.
            fallback_ran = (body.usage.segments or [body.usage])[-1] if body.usage else None
            H.event(c, H.KEEPER, "attempt.fallback", aid,
                    {"bot": row["bot"], "job_id": row["job_id"], "primary_runtime": runtime, "fallback": body.fallback,
                     "model": (fallback_ran.model or None) if fallback_ran else None,
                     "outcome": body.outcome, **({"primary_limited": True} if body.primary_limited else {})})
        what = login_holds.CAPACITY_KIND if busy else "usage limit"
        if skipped:
            settle_status(c, row["bot"], "idle", "", claimed=row["created"])
            H.event(c, H.KEEPER, "attempt.skipped", aid, {"bot": row["bot"], "job_id": row["job_id"]})
        elif stopped:
            self.stopped(c, row, conv["id"], held=held_back)
        elif memory_pass_turn and body.limited and body.outcome != "completed":
            # A nightly memory tidy pass never moves to another model and never waits for a reset: its named model
            # cannot run, so the pass ends skipped with the reason in the run log (spec criterion 22). Any limit does
            # it, even one after work the turn already did (`limited` is cleared then).
            check_models.skip_pass(c, check_task, check)
            settle_status(c, row["bot"], "idle", f"Nightly memory tidy skipped: {runtime} is out of quota on this "
                          f"computer, so its named model could not run", claimed=row["created"])
            H.event(c, H.KEEPER, "attempt.check_skipped", aid,
                    {"bot": row["bot"], "job_id": row["job_id"], "task_id": check_task, "runtime": runtime,
                     "reason": check_models.PASS_SKIPPED})
        elif limited and check and not body.profile_used and (moved := check_models.move_on(
                c, self.store.settings, row["bot"], check_task, check, row["runner_id"])):
            # The computer's default login for the check's model is already out of quota (Health's
            # default_login_limited): retrying it every half hour only fails again, so the check's next turn runs
            # on its next check model at once, and goes back to its first when that login works again.
            hit = H.now()
            settle_status(c, row["bot"], "idle", f"{runtime} is out of quota on this computer's default login; the "
                          f"check moved to {moved['runtime']} ({moved['model']}) at {hit[11:16]}",
                          claimed=row["created"])
            H.event(c, H.KEEPER, "attempt.check_moved", aid,
                    {"bot": row["bot"], "job_id": row["job_id"], "task_id": check_task, "runtime": runtime,
                     "to_runtime": moved["runtime"], "to_model": moved["model"]})
        elif limited and check:
            # A check has no fallback, and its model's limit is not the bot's: hold the check alone, and leave
            # the bot's own work, its status and its Health finding to its own provider.
            hit = H.now()
            retry = H.shift(hit, seconds=reset_cooldown(c, aid, hit, LIMIT_COOLDOWN, body.limit_resets_at))
            check_models.hold(c, check_task, runtime, retry)
            settle_status(c, row["bot"], "idle", f"{runtime} {what} on a check at {hit[11:16]}; the check "
                          f"retries after {retry[:10]} {retry[11:16]} UTC", claimed=row["created"])
            H.event(c, H.KEEPER, "attempt.check_limited", aid,
                    {"bot": row["bot"], "job_id": row["job_id"], "task_id": check_task, "runtime": runtime,
                     "retry_after": retry})
        elif limited and ran and not gateway_wait:
            # The task model's limit is not the bot's: its login is held (after_turn above), the bot's chat, routines
            # and other work go on on its own model, and its next task turns run there until the hold ends.
            hit = H.now()
            retry = login_hold.get("until") or H.shift(hit, seconds=reset_cooldown(c, aid, hit, LIMIT_COOLDOWN,
                                                                                    body.limit_resets_at))
            settle_status(c, row["bot"], "idle", f"{runtime} {what} on the bot's task model at {hit[11:16]}; its "
                          f"tasks run on its own model until {retry[:10]} {retry[11:16]} UTC", claimed=row["created"])
            H.event(c, H.KEEPER, "attempt.task_model_limited", aid,
                    {"bot": row["bot"], "job_id": row["job_id"], "runtime": runtime, "retry_after": retry})
        elif limited and superseded:
            # Its work goes back in the queue and runs on the model the bot was moved to. When that model waits on
            # the same spent login or key, the bot stays Rate limited as the move left it (lift_after_model_change).
            if not model_change_hold(c, self.store.settings, row["bot"]):
                settle_status(c, row["bot"], "idle", f"The previous model hit its usage limit at {H.now()[11:16]}; "
                              "queued work runs on the new model", claimed=row["created"])
            H.event(c, H.KEEPER, "attempt.limited_superseded", aid,
                    {"bot": row["bot"], "job_id": row["job_id"], "runtime": row["handed_runtime"] or runtime,
                     "to_runtime": runtime})
        elif limited:
            streak = limit_streak(c, row["bot"])
            hit = H.now()
            retry = login_hold.get("until") or H.shift(hit, seconds=limit_cooldown(c, row["bot"]))
            src = {}
            if any(gateway.cap_reached(error) for error in limit_errors(c, aid)):
                # The Tico AI Gateway's monthly cap for the company's key (clients/gateway.py): said as it is.
                focus = (f"{gateway.CAP_MESSAGE} at {hit[11:16]}: the gateway key has used this month's cap; "
                         f"retrying after {retry[:10]} {retry[11:16]} UTC")
            elif tico_code == gateway.RATE_LIMITED:
                focus = (f"Tico AI Gateway rate limited at {hit[11:16]}; "
                         f"retrying after {retry[:10]} {retry[11:16]} UTC")
            elif (gateway.out_of_credit(login_hold.get("error")) if login_hold
                  else any(gateway.out_of_credit(error) for error in limit_errors(c, aid))):
                # The turn ended on the company's credit, not a login's limit (login_holds.after_turn): its minute.
                focus = (f"{gateway.PAUSED_MESSAGE} at {hit[11:16]}; trying Tico models again at {retry[11:16]} UTC, "
                         "or as soon as credit is added")
            else:
                # Whose limit it was, from the turn's own notes (backend/login_holds.py source): primary or fallback,
                # which provider and login, and an API key's quota told apart from a plan's usage window. A fallback's
                # quota is never reported as the bot's own runtime's (Dev 279).
                profile = ""
                own = login_holds.get(c, row["runner_id"], login_holds.canonical(c, row["runner_id"], profile, runtime),
                                      runtime)
                src = login_holds.source(c, aid, runtime, body, profile, row["runner_id"],
                                         held=own if body.primary_held else None)
                ran = (body.usage.segments or [body.usage])[-1] if body.usage else None
                if ran is not None:
                    # The model that returned the limit, for the run list (login_holds.failure_line).
                    # Only a segment of the source that returned it: a fallback that fails at once leaves the primary's
                    # segment last, and its model is not the fallback's.
                    if ran.model and (not body.usage.segments or ran.runtime == src.get("runtime")):
                        src["model"] = ran.model
                # Kept with the run: its log and Health name this source after the bot's settings or the login's hold
                # change.
                c.execute("UPDATE attempts SET result_json=json_set(result_json,'$.limit_source',json(?)) WHERE id=?",
                          (encode(src), aid))
                primary_until = ""
                if src["role"] == "fallback" and src.get("primary") in ("limited", "held"):
                    if (own.get("until") or "") > hit:
                        # Its own hold and its own retry time: the fallback's never moves it.
                        primary_until = (f"{own['until'][11:16]} UTC"
                                         + ("" if own["until"][:10] == hit[:10] else f" on {own['until'][:10]}"))
                said = login_holds.label(src, at=hit[11:16], primary_until=primary_until)
                if login_hold.get("reset") or (not login_hold and body.limit_resets_at
                                                and provider_reset(last_limit(c, row["bot"]), H.parse_ts(hit))):
                    focus = (f"{said}; resumes at {retry[11:16]} UTC"
                             + ("" if retry[:10] == hit[:10] else f" on {retry[:10]}") + " when it resets")
                else:
                    third = (login_hold["streak"] if login_hold else streak) >= LIMIT_STRIKES
                    focus = said + (", third in a row" if third else "") + f"; retrying after {retry[:10]} {retry[11:16]} UTC"
            H.status_set(c, H.KEEPER, row["bot"], state="limited", focus=focus)
            # Every limit restarts the cooldown; status_set keeps `since` while the state stays the same.
            c.execute("UPDATE bot_status SET since=? WHERE bot=?", (hit, row["bot"]))
            H.event(c, H.KEEPER, "attempt.limited", aid,
                    {"bot": row["bot"], "job_id": row["job_id"], "runtime": runtime, "streak": streak,
                     "retry_after": retry, "provider_reset": bool(body.limit_resets_at),
                     **({"fallback_failed": body.fallback} if hopped else {}),
                     **({"source": {k: v for k, v in src.items() if k != "notice"}} if src else {})})
        elif busy_acted or repeated_capacity:
            when = "again on its continued thread" if repeated_capacity else "after the run had acted"
            settle_status(c, row["bot"], "crashed", f"{runtime} {login_holds.CAPACITY_KIND} at {H.now()[11:16]} "
                          f"{when}; this run is saved for review", claimed=row["created"], stop=aid)
        elif rejected and any(gateway.key_refused(error) for error in limit_errors(c, aid)):
            settle_status(c, row["bot"], "idle" if requeue else "crashed",
                          "The Tico AI Gateway key is not working; save a new one in Settings > Computers",
                          claimed=row["created"], stop=aid if not requeue else None)
        elif rejected:
            settle_status(c, row["bot"], "idle" if requeue else "crashed",
                          f"{body.auth_rejected.runtime} sign-in was rejected; " +
                          (f"reconnect {subscription['name']} in Settings > Models" if subscription and requeue
                           else "sign in on this computer to continue" if requeue else "this run is saved for review"),
                          claimed=row["created"], stop=aid if not requeue else None)
        elif silent and tico_code == gateway.UNAVAILABLE:
            from .repositories import save_metadata
            hit = H.now()
            tries = c.execute("SELECT count(*) FROM attempts WHERE job_id=?", (row["job_id"],)).fetchone()[0]
            retry = H.shift(hit, seconds=GATEWAY_BACKOFF * 2 ** max(tries - 1, 0))
            save_metadata(c, GATEWAY_WAIT + row["bot"], {"since": hit, "until": retry})
            settle_status(c, row["bot"], "idle", f"{refused} ({hit[11:16]} UTC); the run is back in the queue and "
                          f"retries after {retry[11:16]} UTC (try {tries} of {NO_EFFECT_TRIES})",
                          claimed=row["created"])
            H.event(c, H.KEEPER, "attempt.no_effect_retry", aid,
                    {"bot": row["bot"], "job_id": row["job_id"], "runtime": runtime, "retry_after": retry})
        elif silent:
            settle_status(c, row["bot"], "idle",
                          f"{refused} ({H.now()[11:16]} UTC); the run is back in the queue" if refused else
                          f"{runtime} failed before the run began at {H.now()[11:16]} UTC; the run is back in the queue",
                          claimed=row["created"])
            H.event(c, H.KEEPER, "attempt.no_effect_retry", aid,
                    {"bot": row["bot"], "job_id": row["job_id"], "runtime": runtime})
        elif reset_wait:
            settle_status(c, row['bot'], 'limited',
                          f"Fallback ended without output; queued work resumes at {hold['until'][11:16]} UTC",
                          claimed=row['created'])
        elif yielded:
            # Paused at its hold cap so another turn could use the shared login: it is back in the queue and
            # continues next, so the bot is idle with the pause line, not crashed until the continuation starts.
            from .login_waits import paused
            settle_status(c, row["bot"], "idle", paused(c, aid, "interrupted", '{"yielded": true}'),
                          claimed=row["created"])
        elif retryable:
            # Not a crash and not a limit: the box's sign-in lapsed. Say so plainly, leave the
            # bot claimable, and let the requeued job run again as soon as it is handed out.
            settle_status(c, row["bot"], "idle",
                          f"{runtime} sign-in could not be renewed at {H.now()[11:16]} UTC; the run is back in the queue",
                          claimed=row["created"])
            H.event(c, H.KEEPER, "attempt.sign_in_retry", aid,
                    {"bot": row["bot"], "job_id": row["job_id"], "runtime": runtime})
        else:
            # Only this run's outcome decides. A run held from earlier keeps its own "saved for
            # later" item in Health until reviewed; counting it here turned every clean run after
            # it into a crash, so a working bot looked broken.
            healthy = body.outcome == "completed"
            settle_status(c, row["bot"], "idle" if healthy else "crashed",
                          "" if healthy else f"{refused}. This run is saved for later" if refused
                          else "One run stopped; saved for later",
                          claimed=row["created"], stop=aid if not healthy else None)
        # A Gateway outage keeps its own doubling wait (GATEWAY_WAIT) and is Tico's, not the bot's: it never holds a bot.
        outage = silent and tico_code == gateway.UNAVAILABLE
        self.account_failure(c, row, body, aid, conv, failure,
                             counts=not (stopped or limited or busy or yielded or outage))
        if not stopped and timeout_cut:
            self.auto_reconcile(c, attempt_id=aid)
        return {"attempt_id": aid, "outcome": body.outcome, "message": reply}

    def account_failure(self, c, row, body, aid, conv, failure, counts):
        """What a finished run means for the bot's next ones (backend/run_backoff.py): a run that completed starts the
        count over; a failure within a minute of starting makes the bot wait, and holds it after five in a row; a
        prompt too large to send makes the next run compact its session first, and only a second one in a row starts a fresh
        session. A Stop, a usage limit, a busy model, a
        yield and a Gateway outage (`counts` False) are none of that."""
        too_long = body.outcome == "failed" and counts and failure == run_backoff.PROMPT_TOO_LONG
        if not too_long:
            run_backoff.compact_settle(c, aid, body.outcome == "completed")
        if body.outcome == "completed":
            run_backoff.reset(c, row["bot"])
            return
        if body.outcome != "failed" or not counts:
            return
        if too_long:
            run_backoff.too_long(c, self.store.settings, row, conv["id"], aid)
        run_backoff.failed(c, self.store.settings, row, body, aid)

    def retry(self, c, who, job_id, body):
        row = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row or not self.auth.operator(c, who, row["bot"]):
            raise Problem("forbidden", "Only this bot's operator can retry uncertain work", 403)
        if not body.acknowledge_uncertain_effects or row["state"] != "uncertain":
            raise Problem("uncertain", "Reconcile any external effects before retrying", 409)
        c.execute("UPDATE jobs SET state='queued' WHERE id=?", (job_id,))
        H.event(c, who.actor, "job.retry", job_id)
        return {"job_id": job_id, "state": "queued"}


    def reviewer(self, c, who, bot):
        """The bot's operator, or BotOps. A stopped run is recovered by BotOps,
        not by a person filling in a review form; BotOps' note and decision are audited like theirs."""
        return who.actor == "bot:botops" or self.auth.operator(c, who, bot)

    def review_jobs(self, c, who, bot):
        if not self.reviewer(c, who, bot):
            raise Problem("forbidden", "Only this bot's operator can review interrupted work", 403)
        jobs = []
        for row in c.execute("SELECT j.*, r.decision AS pending_decision FROM jobs j "
                             "LEFT JOIN job_recovery r ON r.job_id=j.id AND r.attempt_id=j.attempt_id "
                             "WHERE j.bot=? AND j.state='uncertain' ORDER BY j.created", (bot,)):
            message = H.message(c, row['message_id'])
            if (not privacy.message_readable(c, privacy.actor(who), message)
                    or row['attempt_id'] and not privacy.attempt_readable(c, privacy.actor(who), row['attempt_id'])):
                continue
            conv = H.conversation(c, message['conversation_id'])
            # An operator must be able to unblock their own bot whatever it was doing. That is
            # not a way into a person's conversation: one of those is reconcilable but arrives
            # without its words.
            readable = self.readable(c, who, conv)
            task = H.task(c, H.message_task_id(message, conv)) if H.message_task_id(message, conv) else None
            attempt = c.execute("SELECT id,state,created,started,finished,final_text FROM attempts WHERE id=?",
                                (row['attempt_id'],)).fetchone()
            events = c.execute("SELECT seq,kind,payload_json FROM attempt_events WHERE attempt_id=? "
                               "ORDER BY seq DESC LIMIT 50", (row['attempt_id'],)).fetchall()
            output = [{'seq': e['seq'], 'kind': e['kind'],
                       'text': str(json.loads(e['payload_json']).get('text', ''))[:4000]}
                      for e in reversed(events)]
            facts = retry_facts(c, row, attempt['started'] if attempt else None, attempt['finished'] if attempt else None)
            if quiet_source_wait(c, row, facts):
                # A safe second computer loss waits for the source, not a person's effects decision.
                continue
            calls, acted = facts['calls'], facts['acted']
            # `stopped` is what the review card leads with (ui/app/settings.js executionReview), by the same rule the
            # sweep retries by: only a run `auto_reconcile` would call safe, on its first start, is 'read'.
            if facts['fallback_unknown']:
                stopped = 'unknown'
                reason = 'BotOps must review this interrupted fallback: its output or effects cannot be ruled out.'
            elif facts['timeout']:
                named = ', '.join([*facts['outside'],
                                   *(['consumed approval (' + ', '.join(facts['approval_kinds']) + ')'] if facts['consumed'] else [])])
                stopped = 'changed' if named else 'twice' if facts['limit_cuts'] >= 2 else 'timeout'
                reason = ('The job was cut by the run time limit twice in a row; review it before continuing.'
                          if facts['limit_cuts'] >= 2 else 'The run reached its time limit.')
                if named:
                    reason += f' It recorded {named}; check this outside action before resuming.'
            elif facts['restart'] and (facts['outside'] or facts['consumed']):
                stopped = 'changed'
                named = ', '.join([*facts['outside'],
                                   *(['consumed approval (' + ', '.join(facts['approval_kinds']) + ')'] if facts['consumed'] else [])])
                reason = (f"{restart_cause(facts)} after the run recorded {named}; check this outside action before resuming.")
            elif facts['restart'] and facts['starts'] <= 1 and not facts['acted']:
                stopped = 'read'
                reason = f"{restart_cause(facts)}; no consumed approval or named irreversible outside action holds this run."
            elif facts['restart'] and facts['starts'] <= 1:
                # Safe to resume, but the run did reach tools that can change things: the card must
                # never say it only read (ui/app/settings.js renders each bucket's own words).
                stopped = 'restarted'
                reason = (f"{restart_cause(facts)}; no consumed approval or named irreversible outside "
                          "action holds this run, but it had started tools that can change things, so check what "
                          "already happened before running it again.")
            elif facts['restart'] and facts['starts'] > 1:
                stopped = 'twice'
                reason = "The run stopped the same way more than once, so Tico won't run it again."
            elif acted:
                stopped = 'changed'
                reason = (f"The run stopped after using a tool that can change things ({', '.join(acted)}). Tico "
                          "cannot tell whether it finished, so retrying automatically could repeat the action.")
            elif facts['consumed']:
                stopped = 'changed'
                reason = ("The run stopped after using an approval. Tico cannot tell whether the approved action "
                          "finished, so retrying automatically could repeat it.")
            elif not facts['reported']:
                stopped = 'unknown'
                reason = ("The run stopped before it returned an answer. Its runner did not record "
                          "enough detail for Tico to prove that retrying automatically is safe.")
            elif facts['starts'] > 1:
                stopped = 'twice'
                reason = "The run stopped the same way more than once, so Tico won't run it again."
            else:
                stopped = 'read'
                reason = ("The run stopped after only reading, so trying it again repeats nothing." if calls else
                          "The run stopped before it used any tool, so trying it again repeats nothing.")
            wait = c.execute("SELECT action,detail_json FROM events WHERE target=? AND action IN "
                             "('job.recovery_wait','job.recovery_ready','job.held') ORDER BY rowid DESC LIMIT 1",
                             (row['attempt_id'],)).fetchone()
            if wait:
                owner = 'BotOps' if wait['action'] == 'job.held' else 'Tico'
                reason += ' ' + owner + ' owns recovery. ' + json.loads(wait['detail_json']).get('reason', '')
            if readable and (limit := login_holds.fallback_reset_notice(c, row['attempt_id'])):
                reason += f" Provider limit: {limit['notice']}. Quota reset ~{limit['reset'][11:16]} UTC"
                reason += f" on {limit['reset'][:10]}."
            jobs.append({'id':row['id'], 'attempt_id':row['attempt_id'], 'readable':readable,
                         'task':{'id':task['id'],'title':task['title'],'status':task['status'],'owner':task['owner'],
                                 'owner_name':owner_name(c, task['owner'])} if task and readable else None,
                         'request':message['body'] if readable else '',
                         'attempt':(dict(attempt) if readable else
                                    {**dict(attempt), 'final_text': ''}) if attempt else None,
                         'output':output if readable else [], 'tools':calls if readable else [], 'reason':reason,
                         # `acted` names tools only, as `reason` always has, so a reader of none of the run's words
                         # still sees why it is held.
                         'stopped':stopped, 'acted':acted,
                         'pending_decision': {'decision': row['pending_decision']} if row['pending_decision'] else None})
        return {'jobs':jobs}

    def reconcile_job(self, c, who, job_id, body):
        row = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row or not self.reviewer(c, who, row['bot']):
            raise Problem("forbidden", "Only this bot's operator or BotOps can review interrupted work", 403)
        if (not body.acknowledge_uncertain_effects or row['state'] != 'uncertain'
                or row['attempt_id'] != body.attempt_id):
            raise Problem("uncertain", "This run changed or its external effects have not been reviewed", 409)
        # A deferred choice keeps the job uncertain; state alone cannot fence another open dialog. Tico's own saved
        # choice for an update cut gives way to a person's.
        if c.execute("SELECT 1 FROM job_recovery WHERE job_id=? AND attempt_id=? AND actor<>?",
                     (job_id, body.attempt_id, H.KEEPER)).fetchone():
            raise Problem("uncertain", "A choice is already saved for this run. Refresh to see it.", 409)
        note = body.note.strip()
        if len(note) < 20:
            raise Problem("review_note", "Describe completed work and what should happen next", 422)
        # Save the operator's choice now, but keep the hold until the lane is idle. The recovery record also
        # fences a late answer from the stopped run while the scheduler waits to apply the choice.
        busy = c.execute("SELECT 1 FROM attempts WHERE bot=? AND state IN ('leased','running') AND lane="
                         "coalesce((SELECT lane FROM attempts WHERE id=?),'main')",
                         (row['bot'], row['attempt_id'])).fetchone()
        result = self._decide(c, row, body.attempt_id, body.decision, note, who.actor, deferred=bool(busy))
        return result

    def _finish_recovery_review(self, c, bot):
        """The review owns saving choices, not waiting for their lanes to become idle."""
        # Reviewing ends when the choices are durably saved, even if their lane is still busy.
        if not c.execute("SELECT 1 FROM jobs j WHERE j.bot=? AND j.state='uncertain' AND NOT EXISTS "
                         "(SELECT 1 FROM job_recovery r WHERE r.job_id=j.id AND r.attempt_id=j.attempt_id)",
                         (bot,)).fetchone():
            for task in c.execute("SELECT id FROM tasks WHERE owner=? AND title=? "
                                  "AND status IN ('open','doing','waiting','review','ready')",
                                  (H.bot_actor(H.FLEET_MAINTAINER), f"Review {bot}'s stopped runs held for a check")).fetchall():
                H.task_update(c, H.KEEPER, task['id'], status='done', quiet=True,
                              note='Recovery decisions saved. Tico applies deferred choices when the lane frees.')

    def _decide(self, c, row, attempt_id, decision, note, actor, *, action='job.reconcile', shown=None, deferred=False, saved=False,
                cause=None):
        """Record one decision on an uncertain job: resume queues it again, dismiss releases it. `shown` is the
        line people read when the note itself is written to the bot. A deferred choice stays held until its lane is idle.
        `cause` names what stopped the run in the event ('update': the update cuts UPDATE_RESUMES counts)."""
        job_id = row['id']
        state = 'uncertain' if deferred else 'queued' if decision == 'resume' else 'cancelled'
        if not saved:
            # A deferred choice applied once its lane is idle keeps the time it was made: that is when it was decided.
            c.execute("INSERT INTO job_recovery VALUES(?,?,?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET "
                      "created=CASE WHEN job_recovery.attempt_id=excluded.attempt_id AND "
                      "job_recovery.decision=excluded.decision AND job_recovery.actor=excluded.actor "
                      "THEN job_recovery.created ELSE excluded.created END,"
                      "attempt_id=excluded.attempt_id,decision=excluded.decision,note=excluded.note,"
                      "actor=excluded.actor",
                      (job_id,attempt_id,decision,note,actor,H.now()))
        if decision == 'dismiss' and not deferred:
            c.execute("UPDATE messages SET delivered_at=coalesce(delivered_at,?) WHERE id=?", (H.now(),row['message_id']))
        c.execute("UPDATE jobs SET state=? WHERE id=?", (state,job_id))
        H.event(c, actor, action, job_id, {'attempt_id':attempt_id, 'decision':decision, 'note':note,
                                           **({'deferred': True} if deferred else {}),
                                           **({'cause': cause} if cause else {}),
                                           **({'shown':shown} if shown else {})})
        self._finish_recovery_review(c, row['bot'])
        if not c.execute("SELECT 1 FROM jobs WHERE bot=? AND state='uncertain'", (row['bot'],)).fetchone():
            settle_status(c, row['bot'], 'idle', 'Stopped run reviewed')
        return {'job_id':job_id, 'state':state, **({'deferred': True} if deferred else {})}

    def hold_unrunnable(self, c):
        """Keep one queued notice per conversation for a bot no machine runs.

        A bot with no assignment never claims anything, so every routine occurrence and digest
        for it only lengthens a queue: a bot created before its
        repository existed can have dozens of hourly jobs a day later. The newest stays
        queued for the machine that eventually takes it on, and the older ones are cancelled;
        their messages stay unread in the bot's inbox and conversation, so nothing is lost.
        """
        newest, held = {}, []
        for row in c.execute(
                # From the unassigned bots to their queued jobs (jobs_queue), not from every message.
                "SELECT j.id,j.bot,j.message_id FROM bots b CROSS JOIN jobs j CROSS JOIN messages m "
                "WHERE NOT EXISTS(SELECT 1 FROM assignments a WHERE a.bot=b.slug) "
                "AND j.bot=b.slug AND j.state='queued' AND m.id=j.message_id AND m.kind='notice' "
                "ORDER BY j.created DESC, j.id DESC").fetchall():
            with isolated(c, "hold_unrunnable", row["id"]):
                # A chat room carries notices for many tasks: one kept per task, not per room.
                msg = H.message(c, row["message_id"])
                key = (row["bot"], msg["conversation_id"],
                       H.message_task_id(msg, H.conversation(c, msg["conversation_id"])))
                if key in newest:
                    held.append(row["id"])
                else:
                    newest[key] = row["id"]
        for job_id in held:
            with isolated(c, "hold_unrunnable", job_id):
                c.execute("UPDATE jobs SET state='cancelled' WHERE id=?", (job_id,))
                H.event(c, H.KEEPER, "job.suppress", job_id,
                        {"reason": "Bot has no machine; a newer notice about the same task stays queued"})
        # An archived bot never runs again: its queued jobs only sit there. The
        # messages stay in its conversations; the job is let go.
        for row in c.execute("SELECT j.id FROM jobs j JOIN bots b ON b.slug=j.bot "
                             "WHERE j.state='queued' AND b.state='archived'").fetchall():
            with isolated(c, "hold_unrunnable", row["id"]):
                c.execute("UPDATE jobs SET state='cancelled' WHERE id=?", (row["id"],))
                H.event(c, H.KEEPER, "job.suppress", row["id"], {"reason": "Bot is archived"})
                held.append(row["id"])
        # A check its model's usage limit held, whose task is done, closed or declined, would go on retrying after
        # every reset and run once on a finished task: its owner's queued turns on it are let go. A person's message
        # stays queued, and so does a check handed to another bot: it is that bot's ordinary task (H.check_model).
        # From the held checks (check_models.hold) to their owner's queue (jobs_queue).
        for row in c.execute("SELECT j.id,t.id AS task_id,t.status FROM registry_metadata r "
                             "JOIN tasks t ON t.id=substr(r.key,15) "
                             "JOIN task_check_models k ON k.task_id=t.id AND k.created_by=t.owner "
                             "CROSS JOIN jobs j CROSS JOIN messages m JOIN conversations cv ON cv.id=m.conversation_id "
                             "WHERE r.key>='check-limited:' AND r.key<'check-limited;' "
                             f"AND t.status NOT IN ({','.join('?' * len(H.ACTIVE_STATUSES))}) "
                             "AND t.owner LIKE 'bot:%' AND j.bot=substr(t.owner,5) AND j.state='queued' "
                             "AND m.id=j.message_id AND m.from_actor NOT LIKE 'human:%' "
                             f"AND {H.MESSAGE_TASK_SQL}=t.id", H.ACTIVE_STATUSES).fetchall():
            with isolated(c, "hold_unrunnable", row["id"]):
                c.execute("UPDATE jobs SET state='cancelled' WHERE id=?", (row["id"],))
                H.event(c, H.KEEPER, "job.suppress", row["id"],
                        {"reason": f"Check task is {row['status']}: its turn held by a usage limit is let go",
                         "task_id": row["task_id"]})
                held.append(row["id"])
        return held

    def drop_unreadable(self, c, job_ids=None):
        """Cancel every queued job whose message its bot may no longer read: a private task it was taken off, say.

        The claim passes over such a job for good (job_refusal's "unreadable"), so it would sit queued forever,
        never claimed or expired, and age Health's queue_idle. The bot never read the message, so nothing it was
        owed is lost; the message itself stays where it is. Held jobs too: a wake preset's release or a drain's end
        would only hand the claim the same unreadable message. Runs from the scheduler, once a minute."""
        dropped = []
        # From the bots to their queued jobs (jobs_queue), not from every job ever run.
        for row in c.execute("SELECT j.id,j.bot,j.message_id FROM bots b CROSS JOIN jobs j "
                             "WHERE j.bot=b.slug AND j.state='queued'" +
                             (" AND j.id IN (" + ",".join("?" * len(job_ids)) + ")" if job_ids else "") +
                             " ORDER BY j.created,j.id", job_ids or ()).fetchall():
            with isolated(c, "drop_unreadable", row["id"]):
                # job_refusal's own check, with its arguments.
                if privacy.message_readable(c, "bot:" + row["bot"], H.message(c, row["message_id"])):
                    continue
                c.execute("UPDATE jobs SET state='cancelled' WHERE id=? AND state='queued'", (row["id"],))
                H.event(c, H.KEEPER, "job.suppress", row["id"], {"reason": "unreadable"})
                dropped.append(row["id"])
        return dropped

    def release_limits(self, c):
        """Runs from the scheduler's tick: lift each usage limit whose hold has passed."""
        release_limits(c)

    def release_deploy_drains(self, c):
        """Undrain the bots a deploy drained and never gave back.

        Runs from the scheduler's tick. A release's migration can bump several
        bots' config revision mid-deploy, the deploy's restore read that as a person's change and
        kept them drained, and they claimed nothing for eleven hours. A bot is released when its
        last drain was the deploy's, more than DEPLOY_DRAIN_MAX ago, and no person has drained or
        paused it since. A deploy's drain record that old is dropped too, so a retry of that
        release drains afresh instead of reusing a list this sweep has already undone.
        """
        now = H.now()
        cutoff, horizon = H.shift(now, seconds=-DEPLOY_DRAIN_MAX), H.shift(now, days=-7)
        released = []
        for row in c.execute("SELECT b.slug FROM bots b JOIN bot_control ctl ON ctl.bot=b.slug "
                             "WHERE ctl.draining=1 AND b.state='active' ORDER BY b.slug").fetchall():
            with isolated(c, "release_deploy_drains", row["slug"]):
                drained = c.execute("SELECT ts, detail_json FROM events WHERE actor='system:deploy' AND action='bot.drain' "
                                    "AND target=? ORDER BY ts DESC LIMIT 1", (row['slug'],)).fetchone()
                if not drained or drained['ts'] > cutoff or drained['ts'] < horizon:
                    continue
                if c.execute("SELECT 1 FROM events WHERE actor>='human:' AND actor<'human;' "
                             "AND action IN ('bot.drain','bot.pause','bot.resume') AND target=? AND ts>=? LIMIT 1",
                             (row['slug'], drained['ts'])).fetchone():
                    continue
                c.execute("UPDATE bot_control SET draining=0 WHERE bot=?", (row['slug'],))
                c.execute("UPDATE bot_config SET revision=revision+1 WHERE bot=?", (row['slug'],))
                release = (json.loads(drained['detail_json'] or '{}') or {}).get('release')
                H.event(c, H.KEEPER, 'operator.deployment-undrain', row['slug'],
                        {'release': release, 'reason': 'stale deploy drain', 'drained_at': drained['ts']})
                released.append(row['slug'])
        for key, value in c.execute("SELECT key, value_json FROM registry_metadata "
                                    "WHERE key LIKE 'deployment-drain:%'").fetchall():
            with isolated(c, "release_deploy_drains", key):
                stamps = [r.get('at') for r in json.loads(value or '[]') if isinstance(r, dict)]
                if stamps and all(stamps) and max(stamps) <= cutoff:
                    c.execute("DELETE FROM registry_metadata WHERE key=?", (key,))
        return released

    def auto_reconcile(self, c, *, attempt_id=None):
        """Settle the interrupted work nobody needs to look at, so a person is only asked about
        a run that actually did something.

        Runs from the scheduler's tick, after `expire`. Two decisions are safe without a person:

        - **Dismiss** when the task the delivery was for is already done or closed. There is
          nothing left to deliver, and rows that held bots were often this.
        - **Resume** when the attempt recorded no tool call other than a read (READ_ONLY_TOOLS) and
          consumed no approval: it read, it thought, it was cut off. Running it again repeats
          nothing. This needs a host that reports its tool calls (`reports_tools`); a run from a
          host that does not is silent either way, so it is held like one that acted.

        A reported max_run_minutes cut uses this same recovery path immediately. It allows one continuation,
        carries the task's hand-off note, and holds a second consecutive cut for review instead of dismissing it.
        A completed attempt breaks the consecutive-cut streak.

        A computer-reported restart resumes on the next sweep even after a shell or local edit, unless a consumed
        approval or a named merge, publish, send or payment requires review. Its note gives the interruption time
        and asks it to check existing effects first. Lost leases without that report retain the conservative rule.

        A run Tico's update or a hub restart cut (cut_by_update) resumes on the next sweep whatever it used, a named
        outside action or a consumed approval included (its note names those to verify first): nobody is asked, and
        BotOps gets no review. A stale cut or a request cut UPDATE_RESUMES times already ends quietly (_update_choice).

        Any other run, one that used a tool that can change things or whose host cannot say what it
        did, stays held for its operator or BotOps to review (`review_jobs`), because the hub cannot
        see what a tool did on the Mac and running it again could repeat an outside action. BotOps is
        given the review once per hold (`_hand_to_maintainer`), so a held run never waits unseen; the
        hold is recorded (`job.held`) and the run is not re-judged on later ticks, only checked for a
        task that has since finished. The runner's own `restore` gets the first STALL_MAX to reclaim
        a run that is still alive; this sweep only looks after that. A run its runner reported
        interrupted (it restarted) is not alive, so a run that is safe to repeat resumes at once; when
        the same computer picks the job up again it continues the same provider session.

        A run whose lease lapsed without its runner saying it stopped is resumed only after RESULT_GRACE: its
        runner may be stalled after the turn finished, and that result settles the job when it comes.

        Resuming is offered once. A job whose second attempt is also uncertain is not a turn
        that was unlucky, it is one that keeps ending the same way, and running it a third time
        is how a bot once opened the same request eighteen times.
        """
        cutoff = H.shift(H.now(), seconds=-STALL_MAX)
        decided, limit_said = [], {}
        task_sql = queued_task_sql()
        # Judged while the bot works on anything else (an always-busy bot never had a free lane, so its stopped runs and
        # the work fenced behind them waited forever, Dev 488); it waits only for a live run that may be doing the same
        # work: one that took this job or folded in its message, one on the same task (a chat with no task: the same
        # conversation), or one in the same session lane, including a shared main lane.
        rows = c.execute("SELECT j.*, a.started, a.finished, a.state AS attempt_state, a.result_json, "
                         "a.stop_requested, a.job_id AS attempt_job, "
                         "r.decision AS pending_decision, r.note AS review_note, r.actor AS reviewer, "
                         "coalesce(a.finished, j.created) <= ? AS aged, "
                         "(SELECT t.id FROM attempts t LEFT JOIN turns tt ON tt.id=t.id "
                         "WHERE t.bot=j.bot AND t.state IN ('leased','running') AND (t.job_id=j.id "
                         "OR t.id IN (SELECT attempt_id FROM attempt_inputs WHERE message_id=j.message_id) "
                         f"OR tt.task_id={task_sql} "
                         f"OR ({task_sql} IS NULL AND EXISTS(SELECT 1 FROM attempt_conversations ac "
                         "WHERE ac.attempt_id=t.id AND ac.conversation_id=m.conversation_id)) "
                         "OR t.lane=a.lane) LIMIT 1) AS blocking_attempt "
                         "FROM jobs j LEFT JOIN attempts a ON a.id=j.attempt_id "
                         "LEFT JOIN job_recovery r ON r.job_id=j.id AND r.attempt_id=j.attempt_id "
                         "LEFT JOIN messages m ON m.id=j.message_id LEFT JOIN conversations cv ON cv.id=m.conversation_id "
                         "WHERE j.state='uncertain' AND j.attempt_id IS NOT NULL "
                         "AND (? IS NULL OR j.attempt_id=?) "
                         "ORDER BY j.created", (cutoff, attempt_id, attempt_id)).fetchall()
        for row in rows:
            with isolated(c, "auto_reconcile", row["id"]):
                prior = c.execute("SELECT action,detail_json FROM events WHERE target=? AND action IN "
                                  "('job.recovery_wait','job.recovery_ready') AND json_extract(detail_json,'$.job_id')=? ORDER BY rowid DESC LIMIT 1",
                                  (row['attempt_id'], row['id'])).fetchone()
                if row['blocking_attempt']:
                    detail = {'job_id': row['id'], 'owner': H.KEEPER, 'blocking_attempt': row['blocking_attempt'],
                              'reason': 'Tico is waiting for live work on the same task or session lane and re-evaluates when it ends.'}
                    if not prior or prior['action'] != 'job.recovery_wait' or json.loads(prior['detail_json']) != detail:
                        H.event(c, H.KEEPER, 'job.recovery_wait', row['attempt_id'], detail)
                    if not row['pending_decision'] and cut_by_update(c, row['attempt_id']):
                        # Tico's own choice for an update cut, saved now so nobody is asked meanwhile; once the lane
                        # frees, the sweep judges the run again rather than applying it blindly.
                        facts = retry_facts(c, row, row['started'], row['finished'])
                        if facts['update']:
                            decided.append(self._decide(c, row, row['attempt_id'], *self._update_choice(c, row, facts),
                                                        H.KEEPER, action='job.auto_reconcile', deferred=True, cause='update'))
                    continue
                freed = bool(prior and prior['action'] == 'job.recovery_wait')
                if freed:
                    H.event(c, H.KEEPER, 'job.recovery_ready', row['attempt_id'],
                            {'job_id': row['id'], 'owner': H.KEEPER,
                             'reason': 'Live task or session lane is free; Tico is re-evaluating recovery.'})
                if row['pending_decision'] and row['reviewer'] == H.KEEPER:
                    # Tico's own saved choice is only a placeholder while the lane was busy: the rules below judge the
                    # run afresh, so a Stop, a quarantine, a finished task or a day's wait since it was saved counts.
                    c.execute("DELETE FROM job_recovery WHERE job_id=? AND actor=?", (row['id'], H.KEEPER))
                elif row['pending_decision']:
                    decided.append(self._decide(c, row, row['attempt_id'], row['pending_decision'],
                                                row['review_note'], row['reviewer'], saved=True))
                    continue
                held = row['attempt_id'] and c.execute("SELECT detail_json FROM events WHERE action='job.held' "
                                                       "AND target=?", (row['attempt_id'],)).fetchone()
                calls, acted, safe = [], [], False
                facts = retry_facts(c, row, row['started'], row['finished'])
                if not held or facts['restart'] or facts['lease_lost'] or facts['timeout']:
                    calls, acted, safe = facts['calls'], facts['acted'], facts['safe']
                started = facts['starts']
                if (not row['aged'] and not freed and not facts['restart'] and not facts['timeout'] and not facts['update']
                        and not (safe and started <= 1)):
                    continue  # the usual wait: a person may look first
                msg = H.message(c, row['message_id'])
                conv = H.conversation(c, msg['conversation_id']) if msg else None
                task_id = work_task_id(msg, conv) if msg else None
                task = H.task(c, task_id) if task_id else None
                if not privacy.message_readable(c, 'bot:' + row['bot'], msg):
                    decided.append(self._decide(c, row, row['attempt_id'], 'dismiss',
                                               'Task access was revoked.', H.KEEPER, action='job.auto_reconcile'))
                    continue
                # A quarantine's Stop is not a person's: its run is judged like any other lost run below.
                if (row['stop_requested'] and row['id'] == row['attempt_job'] and not quarantine_stops.stopped_by(
                        c, {'stop_requested': row['stop_requested'], 'id': row['attempt_id']})):
                    # A person pressed Stop and the computer never reported back: the request is over by their
                    # choice, as it would be had the runner heard it, so it is not resumed. A late answer is only
                    # filed. A message folded into the turn is not what they stopped and is judged as before.
                    decided.append(self._decide(c, row, row['attempt_id'], 'dismiss',
                                                f"Stopped by {stopper(c, row['stop_requested'])}; not resumed.",
                                                H.KEEPER, action='job.auto_reconcile'))
                    continue
                delivery_task = task
                if wake_rule_engine.notice_only(msg):
                    delivery_task = H.task(c, H.message_task_id(msg, conv) or "")
                if delivery_task and delivery_task['status'] in ('done', 'closed'):
                    note = (f"Dismissed automatically: the task \"{delivery_task['title']}\" was already {delivery_task['status']} "
                            "when the interrupted delivery was reviewed, so there was nothing left to deliver.")
                    decided.append(self._decide(c, row, row['attempt_id'], 'dismiss', note, H.KEEPER,
                                                action='job.auto_reconcile'))
                    continue
                if row['attempt_id'] and hop_failed(c, row['attempt_id'], json.loads(row['result_json'] or '{}'),
                                                    row['started'], row['finished']):
                    # Stopped on a usage limit before it did anything (held before Tico read such a turn as the limit's):
                    # never BotOps' to review. All of them go back together, oldest first in the queue: the claim holds
                    # a task's queued work while any run on it is still held (unfenced_sql).
                    said = limit_said.setdefault(row['bot'], {'resume': 0, 'dismiss': 0})
                    if self._covered(c, row, task_id):
                        note = ("Dismissed automatically: this run stopped on a usage limit, and a later run of this bot "
                                "on the same task has finished since, so its work is covered.")
                        decided.append(self._decide(c, row, row['attempt_id'], 'dismiss', note, H.KEEPER,
                                                    action='job.auto_reconcile'))
                        said['dismiss'] += 1
                        continue
                    why = limit_lifted(c, self.store.settings, row['bot'], row['attempt_id'], row['finished'])
                    if why:
                        because = {"model": "the bot has moved to another model",
                                   "fallback": "the fallback it names is ready",
                                   "reset": "the limit has reset"}[why]
                        note = (f"Resumed automatically: this run stopped on a usage limit before it did anything, and "
                                f"{because}, so it runs again.")
                        decided.append(self._decide(c, row, row['attempt_id'], 'resume', note, H.KEEPER,
                                                    action='job.auto_reconcile'))
                        said['resume'] += 1
                    continue
                if facts['update']:
                    # Cut by Tico's update or a hub restart: resumed like a computer-reported restart, never held for a
                    # person or BotOps, whatever it used (_update_choice).
                    decided.append(self._decide(c, row, row['attempt_id'], *self._update_choice(c, row, facts),
                                                H.KEEPER, action='job.auto_reconcile', cause='update'))
                    continue
                # Only runs that started count: a claim that lapsed before its run began (a computer offline, the
                # hub unreachable) says nothing about the request itself.
                if started > 1 and facts['lease_lost'] and safe:
                    if not held:
                        H.event(c, H.KEEPER, 'job.held', row['attempt_id'],
                                {'job_id': row['id'], 'tools': [], 'reason': 'Repeated lease loss; waiting for the computer.'})
                    settle_status(c, row['bot'], 'idle', 'Resuming is held after a second lost lease; waiting for the computer')
                    continue
                if started > 1 and not facts['timeout']:
                    # Stopped twice the same way: not bad luck. Let it go and hand the cause to BotOps.
                    note = ("Dismissed automatically: this request stopped partway twice, so it is not retried "
                            "again. BotOps is looking into why.")
                    decision = self._decide(c, row, row['attempt_id'], 'dismiss', note, H.KEEPER,
                                            action='job.auto_reconcile')
                    if privacy.message_readable(c, 'bot:' + H.FLEET_MAINTAINER, msg):
                        self._tell_maintainer(c, row, msg)
                    decided.append(decision)
                    continue
                limit_cap = facts['timeout'] and facts['limit_cuts'] >= 2
                if not safe or limit_cap:
                    why = ('cut by the run time limit twice in a row' if limit_cap else
                           'Review required before repeating possible effects.')
                    # Held: running it again could repeat an outside action. While it stays held with no decision,
                    # BotOps has an open task for it: one closed or done is reopened on a later sweep.
                    if not held and row['attempt_id']:
                        H.event(c, H.KEEPER, 'job.held', row['attempt_id'], {'job_id': row['id'], 'tools': acted,
                                   'outside_actions': facts['outside'], 'consumed_approval': bool(facts['consumed']),
                                   'owner': H.bot_actor(H.FLEET_MAINTAINER),
                                   'reason': why})
                    elif held:
                        acted = (json.loads(held[0] or '{}') or {}).get('tools') or []
                    if row['attempt_id']:
                        # The review names what makes the run unsafe to repeat: each outside action, and any
                        # approval it consumed, before the tools it merely used.
                        named = [*facts['outside'], *(['consumed approval (' + ', '.join(facts['approval_kinds']) + ')']
                                                      if facts['consumed'] else [])]
                        reviewer = privacy.actor(Identity('bot:' + H.FLEET_MAINTAINER, 'bot'))
                        can_review = (privacy.message_readable(c, reviewer, msg)
                                      and privacy.attempt_readable(c, reviewer, row['attempt_id']))
                        if can_review:
                            self._hand_to_maintainer(c, row, msg, task, named or acted, again=bool(held),
                                                     reason=('was ' + why + (f"; recorded {', '.join(named)}" if named else '')) if limit_cap else None)
                        else:
                            self._hand_to_operator(c, row, msg, again=bool(held))
                    continue
                if (not (facts['restart'] or facts['lease_lost'] or facts['timeout']) and row['attempt_state'] == 'expired' and row['started'] and row['finished']
                        and row['finished'] > H.shift(H.now(), seconds=-RESULT_GRACE)):
                    # Its lease lapsed but its runner never said the turn stopped: the finished turn may still come
                    # in (`settles_late`), and running it again now would answer the request twice.
                    continue
                reads = sorted({call['tool'] for call in calls})
                did = f"only read ({', '.join(reads)})" if reads else "recorded no tool call"
                note = (f"Resumed automatically: the interrupted run {did} and consumed no approval before it "
                        "stopped, so running it again repeats nothing. If this conversation already holds your "
                        "earlier partial work, check what already happened, then continue from there.")
                if facts['lease_lost']:
                    c.execute("UPDATE attempts SET result_json=json_set(coalesce(result_json,'{}'),'$.lease_continue',json('true')) WHERE id=?",
                              (row['attempt_id'],))
                    c.execute("UPDATE attempts SET result_json=json_set(result_json,'$.source_continue',json('true')) "
                              "WHERE id=? AND json_extract(result_json,'$.source_running')=1", (row['attempt_id'],))
                if facts['restart'] or facts['lease_lost']:
                    # The cut time is the attempt's finished (report) time: the closest Tico has to when
                    # the computer was cut off.
                    at = (row['finished'] or H.now())[11:16]
                    note = (f"You were interrupted at {at} UTC by a computer restart or lost lease. "
                            "Check what already happened first, then continue in this provider session. "
                            "No consumed approval or named irreversible outside action was recorded. " +
                            (note if not acted else ''))
                if facts['timeout']:
                    line = f"Reached its {facts['timeout']}-minute run limit; continuing"
                    note = (line + '. Check what already happened first, then continue in this provider session. '
                            'No consumed approval or named irreversible outside action was recorded.')
                    if task and task.get('note'):
                        note += '\n\nHand-off note on the task:\n' + task['note']
                    if task:
                        H.task_comment(c, H.KEEPER, task['id'], line, wake=False)
                decided.append(self._decide(c, row, row['attempt_id'], 'resume', note, H.KEEPER,
                                            action='job.auto_reconcile'))
        for bot, said in limit_said.items():
            with isolated(c, "auto_reconcile", bot):
                self._say_settled(c, bot, said)
        return decided

    @staticmethod
    def _update_choice(c, row, facts):
        """(decision, note) for a run Tico's update or a hub restart cut (facts['update']): resume on the same computer
        and provider session, even after a shell, a local edit, a named outside action or a consumed approval, with a
        note that names any such action to verify first. A cut more than UPDATE_STALE_S old is dismissed as stale, and
        a request already resumed from UPDATE_RESUMES cuts ends; both quietly, in the recovery record only."""
        cut = facts['update']
        by = "Tico's update" if cut['cause'] == 'update' else "a Tico restart"
        if (row['finished'] or row['created']) <= H.shift(H.now(), seconds=-UPDATE_STALE_S):
            return 'dismiss', (f"Dismissed quietly: stopped by {by} at {cut['at'][:16].replace('T', ' ')} UTC, more than a "
                               "day ago, so its stale work is not resumed.")
        resumed = c.execute("SELECT count(*) FROM events WHERE action='job.auto_reconcile' AND target=? "
                            "AND json_extract(detail_json,'$.cause')='update' "
                            "AND json_extract(detail_json,'$.decision')='resume' "
                            "AND json_extract(detail_json,'$.deferred') IS NULL", (row['id'],)).fetchone()[0]
        if resumed >= UPDATE_RESUMES:
            return 'dismiss', f"Ended quietly: cut by {by} again after {resumed} resumes, so it is not resumed again."
        named = [*facts['outside'], *(['consumed approval (' + ', '.join(facts['approval_kinds']) + ')']
                                      if facts['consumed'] else [])]
        return 'resume', (f"You were interrupted at {cut['at'][11:16]} UTC by {by}. Check what already happened first, "
                          "then continue in this provider session. " +
                          (f"Before it stopped, this run recorded {', '.join(named)}: verify whether that already took "
                           "effect before doing it again." if named else
                           "No consumed approval or named irreversible outside action was recorded."))

    @staticmethod
    def _covered(c, row, task_id):
        """Whether a turn of the bot on the same task finished after this run stopped: it worked the task with this
        request in its room, so running the request again would do that twice. A chat outside any task never counts:
        a later turn there may have answered something else, so that run resumes instead."""
        return bool(task_id) and c.execute(
            "SELECT 1 FROM attempts a JOIN turns t ON t.id=a.id WHERE a.bot=? AND t.task_id=? AND a.state='completed' "
            "AND a.started>? LIMIT 1", (row['bot'], task_id, row['finished'] or row['created'])).fetchone() is not None

    @staticmethod
    def _say_settled(c, bot, said):
        """One plain line in the bot's activity for the runs a usage limit held that the sweep settled. It names no
        task: the bot's status is read more widely than its private tasks."""
        parts = []
        if said['resume']:
            parts.append(f"resumed {said['resume']} run{'s' if said['resume'] > 1 else ''} held by a usage limit")
        if said['dismiss']:
            parts.append(f"dismissed {said['dismiss']} held run{'s' if said['dismiss'] > 1 else ''} "
                         "a later run already covered")
        if not parts:
            return
        line = "Tico " + " and ".join(parts)
        status = H.status(c, bot) or {}
        if status.get("state") == "limited" and status.get("focus"):
            line = f"{status['focus'].split('. Tico ')[0]}. {line}"
        H.status_set(c, H.KEEPER, bot, focus=line, reason="Held runs settled")
        H.event(c, H.KEEPER, "limit.held_settled", bot, dict(said))

    def _hand_to_maintainer(self, c, row, msg, task, acted, again=False, *, reason=None):
        """One open BotOps task per bot to review its held runs: BotOps, not a person, recovers a stopped run.
        `again`: the run was held on an earlier sweep. Reopen its review if it was closed while work remained."""
        maintainer = H.FLEET_MAINTAINER
        if row['bot'] == maintainer or not H.bot(c, maintainer):
            return
        title = H.HELD_REVIEW_TITLE.format(bot=row['bot'])
        did = reason or (f"used {', '.join(acted)}" if acted else "ran on a host that does not report its tool calls")
        about = f" on the task \"{task['title']}\" (owner {owner_name(c, task['owner'])})" if task else ""
        review = c.execute("SELECT id,body,status FROM tasks WHERE title=? AND owner=? "
                           "ORDER BY (status IN ('open','doing','waiting','review','ready')) DESC,created DESC "
                           "LIMIT 1", (title, H.bot_actor(maintainer))).fetchone()
        if review:
            if review['status'] in ('done', 'closed', 'declined'):
                try:
                    H.task_update(c, H.KEEPER, review['id'], status='open', quiet=True)
                    H.task_comment(c, H.KEEPER, review['id'],
                                   "Reopened: stopped runs are still held with no recovery decision. "
                                   "Check each held run before closing this review.")
                except H.Refused:
                    return
            # The open review names every run it covers: one held after it was filed gets a comment naming its job,
            # once, so later holds cannot disappear behind an earlier review.
            if row['id'] in str(review['body'] or '') or c.execute(
                    "SELECT 1 FROM events WHERE action='job.held_named' AND target=? "
                    "AND json_extract(detail_json,'$.task')=?", (row['id'], review['id'])).fetchone():
                return
            try:
                H.task_comment(c, H.KEEPER, review['id'],
                               f"Another run of {row['bot']}{about} (job {row['id']}) stopped after it {did}, so Tico "
                               "holds it too. Review it with the others: check what already happened, then resume or "
                               f"dismiss it with POST jobs/{row['id']}/reconcile.")
            except H.Refused:
                return
            H.event(c, H.KEEPER, 'job.held_named', row['id'], {'task': review['id'], 'bot': row['bot']})
            return
        held = c.execute("SELECT count(*) FROM jobs j WHERE j.bot=? AND j.state='uncertain' AND NOT EXISTS("
                         "SELECT 1 FROM job_recovery r WHERE r.job_id=j.id AND r.attempt_id=j.attempt_id)",
                         (row['bot'],)).fetchone()[0]
        still = (f"\nThe last review task for these runs was closed while {held} of them "
                 f"{'was' if held == 1 else 'were'} still held with no decision, so Tico opened this one."
                 if again else "")
        # The request's words stay out: this task is company-wide, and the review shows them only to readers of
        # the conversation they came from.
        try:
            H.task_create(c, H.KEEPER, title,
                          f"A run of {row['bot']}{about} (job {row['id']}) stopped after it {did}, so Tico holds it "
                          f"rather than repeat a possible outside action.{still}\n"
                          f"Read GET bots/{row['bot']}/execution-review (the request, its tool calls and saved output), check what "
                          "already happened, then resume or dismiss each held run with POST jobs/<id>/reconcile. "
                          "The review is done once all choices are saved; Tico applies deferred choices when the lane frees.",
                          H.bot_actor(maintainer))
        except H.Refused:
            pass

    def _hand_to_operator(self, c, row, msg, again=False):
        """BotOps cannot read every held request. Keep an open review task with its authorized operator when the
        request or attempt is private to them; an access failure is not a recovery decision (Dev 543)."""
        bot = H.bot(c, row['bot']) or {}
        config = c.execute("SELECT operator FROM bot_config WHERE bot=?", (row['bot'],)).fetchone()
        operator = (config['operator'] if config else None) or self.auth.owner_id(c)
        owner = 'human:' + operator if operator else None
        if not owner or not H._actor_exists(c, owner):
            return
        display_name = bot.get('display_name') or row['bot']
        title = H.HELD_OPERATOR_REVIEW_TITLE.format(display_name=display_name)
        tasks = c.execute("SELECT id,body FROM tasks WHERE title=? AND owner=? AND status IN "
                          "('open','doing','waiting','review','ready') ORDER BY created", (title, owner)).fetchall()
        boundary = ("Tico cannot show BotOps this interrupted run because only the bot's owner or operator can inspect "
                    "interrupted-run details. BotOps has made no recovery decision. Review the run through the bot's "
                    "interrupted-run review and choose only after checking what already happened.")
        named = H.HELD_OPERATOR_REVIEW_BOT.format(slug=row['bot'])
        if tasks:
            existing = tasks[0]
            missing = [part for part in (boundary, named) if part not in str(existing['body'] or '')]
            if missing:
                body = "\n\n".join([str(existing['body'] or '').rstrip(), *missing]).strip()
                try:
                    H.task_update(c, H.KEEPER, existing['id'], body=body, quiet=True)
                except H.Refused:
                    pass
            return
        job_line = f" Held job: {row['id']}." if row['id'] else ""
        if again:
            job_line += " The earlier review task was closed while the run remained unresolved."
        try:
            H.task_create(c, H.KEEPER, title, boundary + job_line + "\n\n" + named, owner, private=True)
        except H.Refused:
            pass

    def _tell_maintainer(self, c, row, msg):
        """One task for BotOps per bot and day when a request keeps stopping."""
        maintainer = H.FLEET_MAINTAINER
        if row['bot'] == maintainer or not H.bot(c, maintainer):
            return
        title = f"Find why {row['bot']}'s runs keep stopping"
        if c.execute("SELECT 1 FROM tasks WHERE title=? AND status IN ('open','doing','waiting','review','ready')",
                     (title,)).fetchone():
            return
        # The request's words stay out, as in _hand_to_maintainer: its attempts hold them behind the usual reads.
        try:
            H.task_create(c, H.KEEPER, title,
                          f"Job {row['id']} stopped partway twice and was dismissed.\n"
                          f"Read its attempts (`hub sql`), fix the cause, and send the request again if it still matters.",
                          H.bot_actor(maintainer))
        except H.Refused:
            pass
