"""Durable delivery, runner enrollment, and transactionally fenced execution attempts."""

import json
import re
import secrets
import sqlite3
import time
from datetime import timezone

from clients import gateway, sessions

from . import app_delivery, check_models, gateway_connection, inbox_isolation, login_holds, models_preset, providers, quiet_work, routines, run_triggers, runner_versions, screen, usage_limits, wake_presets
from . import task_privacy as privacy
from .auth import Identity
from .batch_work import isolated
from .harnesses import reports_tool_calls
from .statuses import PARKED_SQL
from .store import H, P, Problem, bot_readiness, digest, encode, message_page, readiness_document

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


def bot_turns(capacity):
    """How many of a computer's turns one bot may hold at once: half of them, at least one. A bot with many
    task sessions would otherwise fill every turn and leave other bots' work (a review, a release) queued."""
    return max(1, int(capacity or 1) // 2)


def turn_share(c, runner_id, capacity):
    """The turns one bot may hold on this computer: its share (bot_turns) when another active bot is assigned there,
    every turn when it is alone, since then no one else's work can be waiting for them."""
    bots = c.execute("SELECT count(*) FROM assignments a JOIN bots b ON b.slug=a.bot "
                     "WHERE a.runner_id=? AND b.state='active'", (runner_id,)).fetchone()[0]
    return bot_turns(capacity) if bots > 1 else int(capacity or 1)


def turn_limit(share, capacity, others_waiting):
    """The turns one bot may hold now: its share while another bot's work waits for this computer; otherwise every
    turn but one, kept free so new work from another bot starts at once. Turns left idle help no one."""
    return share if others_waiting else max(share, int(capacity or 1) - 1)


def turns_here(c, runner_id):
    """{bot: turns it holds on this computer now}."""
    return {r[0]: r[1] for r in c.execute("SELECT bot,count(*) FROM attempts WHERE runner_id=? "
                                          "AND state IN ('leased','running') GROUP BY bot", (runner_id,))}


# The busy lists each runner sent with its latest claim: {runner id: (bots, lanes, monotonic time)}. Only the runner
# knows a lapsed lease whose worker still runs, or a bot whose worktrees it is maintaining: Health reads them here
# (bot_refusal). In memory: after a server restart they are empty until each runner's next claim, a second later.
CLAIMED_BUSY = {}
CLAIMED_FRESH = 120         # seconds a runner's busy lists are believed; a runner claims several times a second


def note_claim(runner_id, body):
    """Keep the busy lists of this claim for Health."""
    CLAIMED_BUSY[runner_id] = (frozenset(body.busy_bots or ()), frozenset(body.busy_lanes or ()), time.monotonic())


def claimed_busy(runner_id):
    """(bots, {(bot, lane)}) the runner said were busy on its latest claim; empty when it has not claimed lately."""
    seen = CLAIMED_BUSY.get(runner_id)
    if not seen or time.monotonic() - seen[2] > CLAIMED_FRESH:
        return frozenset(), frozenset()
    return seen[0], frozenset(tuple(entry.split("|", 1)) for entry in seen[1] if "|" in entry)


def lanes_on(runner):
    return LANES in set(H._json(runner["capabilities_json"], []) or []) if runner else False


def roots_on(runner):
    return ROOTS in set(H._json(runner["capabilities_json"], []) or []) if runner else False


def task_goals_on(runner):
    return TASK_GOALS in set(H._json(runner["capabilities_json"], []) or []) if runner else False


def session_task_id(c, bot, config, task_id, roots):
    """The task a turn on `task_id` keys its session and lane on: its session root for a runner that reads
    `session_task_id`, the task itself for any other."""
    return H.session_root(c, bot, task_id, config) if roots and task_id else task_id


def job_lane(c, bot, config, message, conversation, lanes, roots=False):
    """The lane a queued message would run in (clients/sessions.py), `main` for a runner without lanes.
    `config` is the bot's followed config, the one its runner keys sessions on. A subtask's turn runs in
    its session root's lane, so it and its parent's turns take turns in one session."""
    if not lanes:
        return sessions.MAIN_LANE
    return sessions.lane(config, bot, session_task_id(c, bot, config, H.message_task_id(message, conversation), roots),
                         conversation["id"] if conversation else None)


def bump_bot_session_epoch(c, bot):
    """Every turn of `bot` carries this as `bot_session_epoch`; a runner drops the bot's shared thread when it moves."""
    c.execute("INSERT INTO bot_session_epochs VALUES(?,1,?) ON CONFLICT(bot) DO UPDATE SET "
              "epoch=epoch+1,updated=excluded.updated", (bot, H.now()))


# The room messages a claim hands its turn (the runner forwards only what arrived since the session last answered).
HISTORY_PAGE = 50


def lane_history(c, bot, config, history, conversation, lane, roots=False):
    """The part of a room's page a turn in `lane` reads: the messages whose own lane (job_lane) is that
    lane, so the main session never reads task traffic and a task's session never reads the main chat or
    another task. The main chat's quiet task lines (`refs.task_started`, `refs.task_report`) are the main
    session's: how it hears that a task started and where it got to."""
    memo = {}

    def lane_of(message):
        refs = message.get("refs") or {}
        if refs.get("task_report") or refs.get("task_started"):
            return sessions.MAIN_LANE
        task_id = H.message_task_id(message, conversation)
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


def settle_status(c, bot, state, focus, claimed=None, task_id=None):
    """The status a finished or lapsed turn leaves its bot in. While another of its turns is still out,
    the bot is still at work, so an idle or crashed turn does not mark the whole bot idle or crashed;
    the held run keeps its own item in Health. A usage limit is the subscription's, so one lane's limit
    holds for the whole bot until its cooldown passes, even when a previously claimed turn starts or
    restores its lease afterwards. A turn claimed after it (a person's message, Try now) that settles
    shows the limit has lifted. `claimed` is when the turn was leased, None when unknown."""
    if state in ("idle", "crashed", "running"):
        status = H.status(c, bot) or {}
        if (status.get("state") == "limited" and status.get("since")
                and status["since"] > H.shift(H.now(), seconds=-limit_cooldown(c, bot))
                and (state == "running" or claimed is None or claimed < status["since"])):
            return
    if state in ("idle", "crashed"):
        if c.execute("SELECT 1 FROM attempts WHERE bot=? AND state IN ('leased','running') LIMIT 1",
                     (bot,)).fetchone():
            # No count and no task: the other turns may be on private tasks this status's readers cannot see.
            H.status_set(c, H.KEEPER, bot, state="running", focus="Working on other tasks", task_id="")
            return
    H.status_set(c, H.KEEPER, bot, state=state, focus=focus,
                 **({"task_id": task_id} if task_id is not None else {}))


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


def _readable_held(c, who, auth, bot):
    """The bot's runs saved for later that `who` may read, oldest first, each with its task when `who` may read that
    under the same rule as reading it (`auth.task_sql`), else None. A run on a task `who` may not read is left out."""
    fence = auth.task_sql(c, who)
    for row in c.execute("SELECT m.*,j.attempt_id FROM jobs j JOIN messages m ON m.id=j.message_id "
                         "WHERE j.bot=? AND j.state='uncertain' ORDER BY j.created", (bot,)):
        if not (privacy.message_readable(c, privacy.actor(who), row)
                and (not row["attempt_id"] or privacy.attempt_readable(c, privacy.actor(who), row["attempt_id"]))):
            continue
        message = H.message(c, row["id"])  # parsed refs: a comment or notice names its task there
        task_id = H.message_task_id(message, H.conversation(c, message["conversation_id"]))
        task = H.task(c, task_id) if task_id else None
        readable = bool(task and H.task_private_readable(c, privacy.actor(who), task)
                        and c.execute(f"SELECT 1 FROM tasks WHERE id=? AND {fence}", (task["id"],)).fetchone())
        if task and not readable:
            yield row, None, False
        else:
            yield row, task, True


def held_runs(c, who, auth, bot, limit=3):
    """The tasks of a bot's stopped runs held for review that `who` may read, oldest first: title and owner.
    A task counts only under the same rule as reading it (`auth.task_sql`), so a company task involving a bot
    the reader may not read stays unnamed."""
    held = []
    for _, task, _ in _readable_held(c, who, auth, bot):
        if task:
            held.append({"title": task["title"], "owner": task["owner"], "owner_name": owner_name(c, task["owner"])})
        if len(held) >= limit:
            break
    return held


def isolation_refused(c, who, auth, bot):
    """Whether one of the bot's runs saved for later that `who` may read, task and all, is a Tico turn its runner
    refused for want of isolation (clients/gateway.py NOT_ISOLATED): held until a person moves the bot or changes
    its model."""
    return any(seen and row["attempt_id"]
               and any(gateway.run_code(error) == gateway.NOT_ISOLATED for error in limit_errors(c, row["attempt_id"]))
               for row, _, seen in _readable_held(c, who, auth, bot))


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


def limit_errors(c, attempt_id):
    """The runtime's latest error texts on an attempt, newest first."""
    return [str(json.loads(row["payload_json"]).get("error") or "") for row in c.execute(
        "SELECT payload_json FROM attempt_events WHERE attempt_id=? AND kind='error' ORDER BY seq DESC LIMIT 8",
        (attempt_id,))]


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
    return bool(attempt_id) and any(BILLING_LIMIT_RE.search(error) for error in limit_errors(c, attempt_id))


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
    if any(gateway.rate_limited(error) for error in errors):
        # Its address block lasts the Gateway's Retry-After, and every early retry with a refused key extends it.
        return reported or max(cooldown, gateway.RETRY_AFTER)
    if reported:
        return reported
    for error in errors:
        match = re.search(r"usage window resets at (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)", error, re.I)
        reset = H.parse_ts(match[1]) if match else None
        if reset:
            cooldown = max(cooldown, int((reset - finished).total_seconds()) + 1)
    return cooldown


def release_limits(c):
    """Lift each usage limit whose hold has passed: the bot shows it is back, and the status change tells
    its computer to ask for the queued work now rather than at its next backup poll."""
    for row in c.execute("SELECT bot,since FROM bot_status WHERE state='limited'").fetchall():
        if row["since"] and row["since"] > H.shift(H.now(), seconds=-limit_cooldown(c, row["bot"])):
            continue
        settle_status(c, row["bot"], "idle", "Usage limit lifted; queued work resumes")
        H.event(c, H.KEEPER, "limit.released", row["bot"], {"since": row["since"]})


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
    (a person's message is worth one try)."""
    from .repositories import metadata
    status = H.status(c, bot) or {}
    wait = metadata(c, GATEWAY_WAIT + bot)
    if wait.get("until") and wait["until"] > H.now() and not person_wrote_since(c, bot, wait.get("since") or ""):
        return True
    return bool(status.get("state") == "limited" and status.get("since")
                and status["since"] > H.shift(H.now(), seconds=-limit_cooldown(c, bot))
                and not person_wrote_since(c, bot, status["since"]))


GATEWAY_WAIT = "gateway-unavailable:"


def clear_limits(c, settings, actor, bot):
    """Try now: lift the usage-limit cooldown for the bot and for every bot limited on the same
    runtime on the same computer, since they share the subscription a person has just renewed.
    Returns how many bots it cleared."""
    def runtime(slug):
        row = c.execute("SELECT config_json FROM bot_config WHERE bot=?", (slug,)).fetchone()
        return providers.bot_choice(c, settings, json.loads(row[0]) if row and row[0] else {})[0]
    mine, place = runtime(bot), c.execute("SELECT runner_id FROM assignments WHERE bot=?", (bot,)).fetchone()
    bots = [bot]
    if place and place["runner_id"] and mine:
        bots += [r[0] for r in c.execute("SELECT s.bot FROM bot_status s JOIN assignments a ON a.bot=s.bot "
                                         "WHERE s.state='limited' AND a.runner_id=? AND s.bot<>? ORDER BY s.bot",
                                         (place["runner_id"], bot)) if runtime(r[0]) == mine]
    for slug in bots:
        row = c.execute("SELECT config_json FROM bot_config WHERE bot=?", (slug,)).fetchone()
        if place and place["runner_id"]:
            # The plan behind the login was just renewed: its hold goes too, for every bot on it.
            login_holds.clear(c, *login_key(c, settings, place["runner_id"], slug,
                                            json.loads(row[0]) if row and row[0] else {}))
        if (H.status(c, slug) or {}).get("state") == "limited":
            H.status_set(c, actor, slug, state="idle", focus="", reason="Try now")
        H.event(c, actor, "limit.cleared", slug, {"from": bot, "runtime": mine})
    return len(bots)


def own_login_hold(c, settings, bot):
    """The hold on the bot's own login on its computer (backend/login_holds.py), or {}: it can stand while the bot is
    not `limited`, its work going on on a fallback."""
    place = c.execute("SELECT runner_id FROM assignments WHERE bot=?", (bot,)).fetchone()
    row = c.execute("SELECT config_json FROM bot_config WHERE bot=?", (bot,)).fetchone()
    if not place or not place["runner_id"] or not row:
        return {}
    return login_holds.get(c, *login_key(c, settings, place["runner_id"], bot, json.loads(row[0] or "{}")))


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


def claim_refusal(c, settings, runner, bot, config_json, draining, ready, held_bots=(), subscription_context=None,
                  default=None):
    """Why `runner`'s claim passes over all of `bot`'s queued work before it looks at any one job: (reason, None), or
    (None, config) when it may look at the bot's jobs. The claim (Execution.candidate) and Health (bot_refusal) use
    this one rule, so Health never calls work the claim holds on purpose a Tico bug. `held_bots`: bots the runner
    says are busy (a lane runner sends only the bots whose worktrees it is maintaining) and, for a runner without
    lanes, every bot with a running turn. `ready` is the computer's readiness document."""
    from .repositories import metadata
    from .subscriptions import context, covers, effective, refusal
    if draining:
        return "draining", None
    if bot in held_bots:
        return ("maintaining" if lanes_on(runner) else "lane_held"), None
    try:
        config = json.loads(config_json)
    except (ValueError, TypeError):
        config = None
    if not isinstance(config, dict):
        return "config", None
    profile = effective(c, bot, subscription_context if subscription_context is not None else context(c))[0]
    if profile:
        if not metadata(c, "computer-profiles:" + runner["id"]).get("reported"):
            return "profile", None
        runtime = providers.bot_choice(c, settings, config)[0]
        blocked = refusal(c, runner["id"], bot)
        if not covers({**config, "runtime": runtime}) or (blocked.get("profile") == profile
                and blocked.get("primary_runtime") == runtime
                and blocked.get("primary_harness") == config.get("harness")):
            return "profile", None
        report = c.execute("SELECT runtimes_json FROM computer_profiles WHERE runner_id=? AND profile=?",
                           (runner["id"], profile)).fetchone()
        if not report or json.loads(report[0] or "{}").get(runtime, {}).get("signed_in") is False:
            return "profile", None
    record = providers.load(c, settings)
    filled = providers.fill(record, config)
    if gateway_connection.is_tico(filled.get("model")) and not models_preset.isolated(ready):
        # A Tico model runs only where bots run as their own user (runner-rs/src/gateway_relay.rs): elsewhere the
        # runner would refuse the turn, so the work waits here instead of failing there.
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
        return ("cooling_off", None) if cooling_off(c, bot) else (None, config)
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
    if (cooling_off(c, bot) and not primary_hold(c, settings, runner["id"], bot, config, ctx=subscription_context)
            or login_refusal(c, settings, runner["id"], bot, config, ctx=subscription_context)):
        return "cooling_off", None
    return None, config


def login_refusal(c, settings, runner_id, bot, config, job_id=None, ctx=None):
    """Whether a hold on the bot's own login keeps it waiting (backend/login_holds.py): before the hold ends, no bot
    on the login starts a turn; after it, only the probe job does until its turn ends. A bot with a fallback that is
    ready here and not held itself does not wait: its turn runs there (primary_hold marks the job so the runner skips
    the held login). Without `job_id` it answers for the bot: a probe of its own still lets it through, for
    job_refusal's per-job answer."""
    why = primary_hold(c, settings, runner_id, bot, config, job_id, ctx)
    return None if why and fallback_choice(c, settings, runner_id, bot, config, job_id, ctx) else why


def primary_hold(c, settings, runner_id, bot, config, job_id=None, ctx=None):
    """Why the bot's own login takes no turn now ("held" or "probing"), or None."""
    _, profile, runtime = login_key(c, settings, runner_id, bot, config, ctx)
    why = login_wait(c, runner_id, profile, runtime, bot, job_id)
    if why == "held" and person_wrote_since(c, bot, login_holds.get(c, runner_id, profile, runtime).get("since") or ""):
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
    from .harnesses import runtime_of
    from .subscriptions import effective
    named = config.get("fallback") if isinstance(config.get("fallback"), dict) else {}
    if named.get("off"):
        return None
    _, own, primary = login_key(c, settings, runner_id, bot, config, ctx)
    if bot in login_holds.get(c, runner_id, own, primary).get("stuck", []):
        return None
    row = c.execute("SELECT readiness_json FROM runners WHERE id=?", (runner_id,)).fetchone()
    ready = readiness_document(row["readiness_json"]) if row else {}
    profile = effective(c, bot, ctx)[0] or ""
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
            if not models_preset.isolated(ready):
                continue
            return "", choice["runtime"]
        signed = (ready.get("runtimes") or {}).get(choice["runtime"]) or {}
        if profile:
            signed = (signed.get("profiles") or {}).get(profile) or {}
        if signed.get("installed") is not True or signed.get("authenticated") != "ready":
            continue
        login = login_holds.canonical(c, runner_id, profile, choice["runtime"])
        why = login_wait(c, runner_id, login, choice["runtime"], bot, job_id)
        if why == "held":
            continue
        return None if why else (login, choice["runtime"])
    return None


def login_key(c, settings, runner_id, bot, config, ctx=None):
    """(runner, login, runtime) of the bot's own login, as holds key it (login_holds.canonical)."""
    from .subscriptions import effective
    runtime = providers.bot_choice(c, settings, config)[0]
    return runner_id, login_holds.canonical(c, runner_id, effective(c, bot, ctx)[0] or "", runtime), runtime


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


def live_tasks(c):
    """{(bot, task)} with a turn out now. A later wake for that bot about that task waits for the turn even when its
    lane is another one: a task moved under another of the bot's tasks mid-turn keys a new lane (session_root), and
    one task never has two turns of the same bot at once."""
    return {(r[0], r[1]) for r in c.execute("SELECT a.bot,t.task_id FROM attempts a JOIN turns t ON t.id=a.id "
                                             "WHERE a.state IN ('leased','running') AND t.task_id IS NOT NULL")}


def job_refusal(c, job, followed, ready, busy, lanes, roots, live=frozenset()):
    """Why the claim passes over one queued job of a bot claim_refusal let through, or None when it hands it out.
    `followed` is the bot's followed config; `busy` the taken (bot, lane) pairs; `live` the (bot, task) pairs with a
    turn out (live_tasks)."""
    message = H.message(c, job["message_id"])
    if not privacy.message_readable(c, "bot:" + job["bot"], message):
        return "unreadable"
    conversation = H.conversation(c, message["conversation_id"])
    task_id = H.message_task_id(message, conversation)
    wanted = check_models.of(c, task_id, job["bot"])
    if not check_models.runs_here(ready, wanted) or (wanted and check_models.held(c, task_id)):
        return "check_model"
    if wanted and not message["from_actor"].startswith("human:") and check_models.dropped(c, task_id):
        return "check_model"                # hold_unrunnable cancels it on the scheduler's next tick
    config = check_models.apply(followed, wanted)
    if ((job["bot"], job_lane(c, job["bot"], config, message, conversation, lanes, roots)) in busy
            or (job["bot"], task_id) in live):
        return "lane_held"
    return None


def bot_refusal(c, settings, runner_id, bot):
    """Why `runner_id`'s next claim would give none of `bot`'s queued work, by the claim's own rules (claim_refusal,
    the interrupted-work fence, job_refusal) and the busy lists the runner sent with its latest claim; None when it
    could take one. For Health, about a bot with no leased or running attempt: the reason of its oldest held job."""
    return bot_claimable(c, settings, runner_id, bot)[0]


def bot_claimable(c, settings, runner_id, bot):
    """(reason, first, person) for `bot`'s queued work on `runner_id`'s next claim: `reason` as bot_refusal says it;
    otherwise None with `first`, when the job the claim would hand out next was queued, and `person`, True when a
    person sent it. The scan keeps the claim's own order (Execution.candidate: a redirect, then a person's message, then
    the longest waiting), so Health ages the job the claim would take: an old held job beside a young free one is
    neither held nor slow, and neither is an old free job behind a young chat the claim takes first."""
    from .shared_bots import follow
    runner = c.execute("SELECT * FROM runners WHERE id=?", (runner_id,)).fetchone()
    row = c.execute(f"SELECT bc.config_json,{DRAINING_SQL.replace('a.bot', 'bc.bot')} draining FROM bot_config bc "
                    "WHERE bc.bot=?", (bot,)).fetchone()
    if not runner:
        return None, None, None
    if not row:
        return "config", None, None            # the claim reads only bots with a configuration
    held_bots, held_lanes = claimed_busy(runner_id)
    ready = readiness_document(runner["readiness_json"])
    why, config = claim_refusal(c, settings, runner, bot, row["config_json"], row["draining"], ready, held_bots)
    if why:
        return why, None, None
    lanes, roots = lanes_on(runner), roots_on(runner)
    busy = {(r["bot"], r["lane"]) for r in c.execute(
        "SELECT bot,lane FROM attempts WHERE state IN ('leased','running')")} | held_lanes
    # A lapsed lease whose worker still runs counts against the bot's turns here, as in the claim; only a person's
    # message is never held behind them.
    mine = sum(1 for b, _ in held_lanes if b == bot)
    at_share = mine and mine >= turn_limit(turn_share(c, runner_id, runner["capacity"]), runner["capacity"], False)
    followed, refused = follow(c, bot, config), None     # refused: (created, reason) of the oldest held job
    for job in c.execute(f"SELECT j.*,m.from_actor,{unfenced_sql()} free FROM jobs j JOIN messages m ON m.id=j.message_id "
                         "JOIN conversations cv ON cv.id=m.conversation_id WHERE j.bot=? AND j.state='queued' AND j.held=0 "
                         "ORDER BY j.redirect DESC,CASE WHEN m.from_actor LIKE 'human:%' THEN 0 ELSE 1 END,j.created,j.id",
                         (bot,)).fetchall():
        person = str(job["from_actor"]).startswith("human:")
        if not job["free"]:
            why = "uncertain"
        elif at_share and not person:
            why = "lane_held"
        else:
            why = job_refusal(c, job, followed, ready, busy, lanes, roots) or (
                "cooling_off" if login_refusal(c, settings, runner_id, bot, followed, job["id"]) else None)
        if why is None:
            return None, job["created"], person
        if refused is None or job["created"] < refused[0]:
            refused = (job["created"], why)
    return (refused[1] if refused else None), None, None


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
            if not row or row["expires"] <= H.now():
                raise Problem("enrollment", "Enrollment code is invalid or expired", 401)
            # Deterministic credential makes a lost enrollment response safely retryable.
            token = digest("tico-runner:" + body.code)
            if row["consumed_at"]:
                runner = c.execute("SELECT * FROM runners WHERE id=? AND revoked_at IS NULL",
                                   (row["runner_id"],)).fetchone()
                if not runner or runner["label"] != body.label:
                    raise Problem("enrollment", "Enrollment code was already used", 409)
                return {"runner_id": runner["id"], "token": token, "operator": row["operator"]}
            rid = H.new_id()
            # A new computer takes members' bots, so a team gets going without asking; an admin closes it per
            # computer (Settings > Computers). Its Screen starts at Control when it first offers one
            # (backend/screen.py).
            # Computers that existed before these defaults keep what they had.
            c.execute("INSERT INTO runners(id,label,operator,token_hash,created,platform,accepts_member_bots,"
                      "screen_default_pending) VALUES(?,?,?,?,?,?,1,1)",
                      (rid, body.label, row["operator"], digest(token), H.now(), body.platform))
            c.execute("UPDATE enrollments SET consumed_at=?,runner_id=? WHERE code_hash=?",
                      (H.now(), rid, digest(body.code)))
            H.event(c, "human:" + row["operator"], "runner.enrolled", rid)
            if self.runner_enrolled:
                self.runner_enrolled(c, rid, row["operator"])
            return {"runner_id": rid, "token": token, "operator": row["operator"]}

    def heartbeat(self, c, who, body):
        row = self.runner(c, who)
        now = H.now()
        awake_since = self.waking(row, now)
        self.served_at = now
        offered_before = screen.available(row)
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
        c.execute("UPDATE runners SET last_seen=?,awake_since=?,version=?,platform=coalesce(nullif(platform,''),?),"
                  "capacity=?,readiness_json=?,capabilities_json=?,readiness_at=? WHERE id=?",
                  (now, awake_since, body.version, body.platform, body.capacity,
                   encode(readiness), encode(sorted(set(body.capabilities))), now, who.runner_id))
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
        worktree_actions = worktrees.heartbeat(c, who, body.worktrees, readiness.get('worktrees', False), self.store.settings.github_owner)
        # A worktree whose task neither this computer nor its owning bot here can read is left out and counted, so one
        # such task cannot fail the whole readiness report (task_privacy.require_payload refuses the response).
        sendable = [a for a in worktree_actions if privacy.worktree_action_readable(c, who, a)]
        skipped = len(worktree_actions) - len(sendable)
        # A Restart a person pressed goes to the runner once; it restarts when no turn is running.
        restart = c.execute("SELECT restart_requested,performance FROM runners WHERE id=?", (who.runner_id,)).fetchone()
        if restart and restart["restart_requested"]:
            c.execute("UPDATE runners SET restart_requested=NULL WHERE id=?", (who.runner_id,))
        return {"server_time": H.now(), "assignments": self.assigned(c, who), "runtime_credential_source": True, "subscription_accounts": True, "worktree_record_stamp": True, "worktree_actions": sendable,
                # Whether the runner samples its performance (backend/performance.py).
                "performance": bool(restart) and restart["performance"] != "off",
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
        from .subscriptions import effective, context
        subscription_context = context(c)
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
            result.append({**dict(row), 'computer_label': runner['label'], 'profile': effective(c, row['bot'], subscription_context)[0],
                           'config': app_delivery.job_config(providers.fill(company, follow(
                               c, row['bot'], json.loads(row['config_json']), configs.get))),
                           'repository': bot_repository(c, self.store.settings, row['bot'], configs.get, app),
                           'mail_agent': bool(P.inbox_person(row['bot'], people))})
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
        H.event(c, who.actor, "bot.assigned", bot, {"runner": body.runner_id, "generation": generation + 1})
        from . import built_on
        built_on.forget(c, bot)                   # placed: its computer reports the checkout from now on
        if old and old["runner_id"] != body.runner_id:
            forget_report(c, old["runner_id"], bot)
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

    def expire(self, c):
        now = H.now()
        rows = c.execute("SELECT * FROM attempts WHERE state IN ('leased','running') AND lease_until<=?",
                         (now,)).fetchall()
        if rows and self.stalled(now):
            # The same grace as after a restart: one more lease period, then the usual rule.
            until = H.shift(now, seconds=self.store.settings.lease_seconds)
            for row in rows:
                with isolated(c, "expire", row["id"]):
                    c.execute("UPDATE attempts SET lease_until=? WHERE id=?", (until, row["id"]))
                    H.event(c, H.KEEPER, "attempt.lease-grace", row["id"],
                            {"bot": row["bot"], "lapsed": row["lease_until"], "lease_until": until, "served_at": self.served_at})
            return
        for row in rows:
            with isolated(c, "expire", row["id"]):
                state = "queued" if row["state"] == "leased" else "uncertain"
                c.execute("UPDATE attempts SET state='expired',finished=? WHERE id=?", (H.now(), row["id"]))
                gateway_connection.forget(c, row["id"])
                if row["state"] == "leased":
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
                c.execute("UPDATE jobs SET state=? WHERE attempt_id=? AND state IN ('leased','running','input')", (state, row["id"]))
                H.turn_finish(c, H.KEEPER, row["id"], exit_code="failed" if state == "failed" else "interrupted",
                              summary=reason if state == "failed" else "Runner lease expired")
                if H.bot(c, row["bot"]):
                    settle_status(c, row["bot"], "crashed",
                                  reason if state == "failed" else "One run stopped; saved for later",
                                  claimed=row["created"])
                H.event(c, H.KEEPER, "attempt.expired", row["id"], {"job_state": state})

    @staticmethod
    def mark_rejected(c, runner_id, runtime, reason, profile=None):
        """Record the refusal now; the runner's next heartbeat repeats it and later lifts it."""
        record = c.execute("SELECT readiness_json FROM runners WHERE id=?", (runner_id,)).fetchone()
        ready = readiness_document(record[0] if record else None)
        row = (ready.get("runtimes") or {}).get(runtime)
        if not isinstance(row, dict):
            return
        if profile:
            row = {}
        row.update(authenticated="rejected", rejected_at=H.now(), rejected_reason=reason[:300],
                   detail=("Sign-in rejected: " + reason)[:500])
        for bot in ready.get("bots", {}).values():
            if isinstance(bot, dict) and bot.get("runtime") == runtime and bot.get("profile", "") == (profile or ""):
                bot["sign_in"] = "rejected"
                bot["ready"] = False
                bot["problems"] = [row["detail"]]
        if profile:
            report = c.execute("SELECT runtimes_json FROM computer_profiles WHERE runner_id=? AND profile=?",
                               (runner_id, profile)).fetchone()
            if report:
                runtimes = json.loads(report[0] or "{}")
                runtimes[runtime] = {"signed_in": False}
                c.execute("UPDATE computer_profiles SET runtimes_json=?,updated=? WHERE runner_id=? AND profile=?",
                          (json.dumps(runtimes), H.now(), runner_id, profile))
        c.execute("UPDATE runners SET readiness_json=? WHERE id=?", (json.dumps(ready), runner_id))

    def candidate(self, c, who, body, runner):
        """The queued job this runner would be given next, or None. Reads only."""
        note_claim(runner["id"], body)
        lanes, roots = lanes_on(runner), roots_on(runner)
        # Taken lanes, each "bot|lane": the server's active attempts and the runner's own busy list. A runner
        # without lanes runs one turn per bot, so any active attempt takes the whole bot.
        busy = {(r["bot"], r["lane"]) for r in c.execute(
            "SELECT bot,lane FROM attempts WHERE state IN ('leased','running')")}
        busy_bots = {bot for bot, _ in busy} if not lanes else set()
        mine = {tuple(entry.split("|", 1)) for entry in (body.busy_lanes or ()) if "|" in entry}
        busy |= mine
        # This computer's turns each bot holds: the server's leases, or the runner's own live workers when it
        # counts more (a lapsed lease whose worker still runs). A bot at its share waits for one of them to
        # finish, except for a person's own message, which is never held behind the bot's other work.
        held = turns_here(c, runner["id"])
        for bot in {bot for bot, _ in mine}:
            held[bot] = max(held.get(bot, 0), sum(1 for b, _ in mine if b == bot))
        share = turn_share(c, runner["id"], runner["capacity"])
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
        for row in c.execute(f"SELECT a.bot,bc.config_json,{DRAINING_SQL} draining FROM assignments a "
                             "JOIN bots b ON b.slug=a.bot JOIN bot_config bc ON bc.bot=a.bot "
                             "WHERE a.runner_id=? AND b.state='active' AND (? IS NULL OR a.bot=?) "
                             "AND EXISTS(SELECT 1 FROM jobs j WHERE j.bot=a.bot AND j.state='queued' AND j.held=0)",
                             (who.runner_id, body.bot, body.bot)).fetchall():
            bot = row["bot"]
            why, config = claim_refusal(c, self.store.settings, runner, bot, row["config_json"], row["draining"], ready,
                                        held_bots, subscription_context, default)
            if why:
                continue
            bots.append(bot)
            followed[bot] = follow(c, bot, config)
        if not bots:
            return None
        marks = ",".join("?" for _ in bots)
        live = live_tasks(c)
        limit = turn_limit(share, runner["capacity"], any(held.get(bot, 0) < share for bot in bots))
        full = [bot for bot in bots if held.get(bot, 0) >= limit]
        # Readiness, spend and cooldown are checked once per bot, never once per queued job.
        # Keep human chat priority and interrupted-task/conversation fencing in the query.
        candidates = c.execute(
            "SELECT j.*,a.generation FROM jobs j JOIN assignments a ON a.bot=j.bot "
            "JOIN messages m ON m.id=j.message_id JOIN conversations cv ON cv.id=m.conversation_id "
            # A job its wake preset holds waits for its release (backend/wake_presets.py tick).
            f"WHERE j.state='queued' AND j.held=0 AND j.bot IN ({marks}) AND a.runner_id=? "
            f"AND (m.from_actor LIKE 'human:%' OR j.bot NOT IN ({','.join('?' for _ in full)})) "
            "AND (m.from_actor LIKE 'human:%' OR NOT EXISTS(SELECT 1 FROM bot_config pc "
            "WHERE pc.bot=j.bot AND pc.onboarding_state IN " + PARKED_SQL + ")) "
            f"AND {unfenced_sql()} "
            # A redirect first (it replaces a turn just stopped); then a person's chat; then the bot holding the
            # fewest turns here, so a freed turn goes to another bot's waiting job before the busy bot's next one;
            # then the longest waiting.
            "ORDER BY j.redirect DESC,CASE WHEN m.from_actor LIKE 'human:%' THEN 0 ELSE 1 END,"
            f"CASE j.bot {' '.join('WHEN ? THEN ?' for _ in bots)} ELSE 0 END,j.created,j.id",
            (*bots, who.runner_id, *full, *(v for bot in bots for v in (bot, held.get(bot, 0)))))
        for row in candidates:
            if job_refusal(c, row, followed[row["bot"]], ready, busy, lanes, roots, live) is None and not login_refusal(
                    c, self.store.settings, runner["id"], row["bot"], followed[row["bot"]], row["id"], subscription_context):
                return row
        return None

    def idle_claim(self, c, who, body, key=None, selected=None):
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
        answer = self._idle_claim(c, who, body, selected)
        if answer is not None:
            self.served_at = H.now()        # answering a runner is what `stalled` measures
        return answer

    def _idle_claim(self, c, who, body, selected=None):
        runner = self.runner(c, who)
        if not runner:
            return None
        now = H.now()
        if c.execute("SELECT 1 FROM attempts WHERE state IN ('leased','running') AND lease_until<=? LIMIT 1",
                     (now,)).fetchone():
            return None
        awake_since = self.waking(runner, now)
        if (awake_since != runner["awake_since"] or not runner["last_seen"]
                or runner["last_seen"] <= H.shift(now, seconds=-CONTACT_EVERY)):
            return None
        # From this computer's bots to their queued jobs (jobs_queue): left to itself SQLite starts from
        # every keeper notice ever sent, tens of milliseconds on each of a runner's frequent claims.
        if c.execute("SELECT 1 FROM assignments x CROSS JOIN jobs j CROSS JOIN messages m "
                     "WHERE x.runner_id=? AND j.bot=x.bot AND j.state='queued' AND m.id=j.message_id "
                     "AND m.from_actor=? AND m.kind='notice' AND m.body LIKE 'New task from %' LIMIT 1",
                     (who.runner_id, H.KEEPER)).fetchone():
            return None
        why = runner_versions.paused(c, who.runner_id)
        if why:
            return {"attempt": None, "paused": why}
        count = c.execute("SELECT count(*) FROM attempts WHERE runner_id=? AND state IN ('leased','running')",
                          (who.runner_id,)).fetchone()[0]
        if count >= runner["capacity"] or not self.awake(awake_since, now):
            return {"attempt": None}
        row = self.candidate(c, who, body, runner)
        if row is not None and selected is not None:
            selected.append(row)
        return None if row else {"attempt": None}

    def stamp(self, c, who, runner, now):
        """Record contact: `last_seen`, at most every CONTACT_EVERY, and a new waking period after a silence."""
        awake_since = self.waking(runner, now)
        if awake_since != runner["awake_since"]:
            H.event(c, H.KEEPER, "runner.waking", who.runner_id,
                    {"last_seen": runner["last_seen"], "awake_since": awake_since})
        if awake_since != runner["awake_since"] or not runner["last_seen"] \
                or runner["last_seen"] <= H.shift(now, seconds=-CONTACT_EVERY):
            c.execute("UPDATE runners SET last_seen=?,awake_since=? WHERE id=?",
                      (now, awake_since, who.runner_id))
        return awake_since

    def contact(self, c, who):
        """A runner's open event stream is contact, as a claim or a heartbeat is: its keepalives keep
        `last_seen` (Health, waking periods) current while the runner sends little else, and keep
        `served_at` current so a lapsed lease is not mistaken for this server's stall. Problem 403
        for a revoked computer."""
        runner = self.runner(c, who)
        if not runner:
            raise Problem("forbidden", "This computer is no longer registered", 403)
        now = H.now()
        self.served_at = now
        self.stamp(c, who, runner, now)

    def claim(self, c, who, body, selected=None):
        runner = self.runner(c, who)
        changes = c.total_changes
        self.expire(c)
        if c.total_changes != changes:
            selected = None
        now = H.now()
        self.served_at = now
        # A claim is contact too, and on a machine waking for a few seconds it is the first
        # contact: the heartbeat behind it is up to 15 s away. Recording the waking period here
        # is what keeps a brief wake from taking work on a stale one.
        # A claim is contact, and on a machine waking for seconds it is the only contact: its
        # heartbeat runs on a fifteen-second timer that a wake this short never reaches. Until
        # this was recorded, the next wake compared itself against a heartbeat from before the
        # sleep, read the gap as one long stretch of presence, and took the work.
        awake_since = self.stamp(c, who, runner, now)
        # An initial task notice can sit in the queue while another turn finishes
        # that task. Suppress only this obsolete wake, never a later human follow-up.
        obsolete = c.execute("SELECT j.id,j.message_id,t.id AS task_id FROM assignments x "
            "CROSS JOIN jobs j CROSS JOIN messages m "
            f"JOIN conversations cv ON cv.id=m.conversation_id JOIN tasks t ON t.id={H.MESSAGE_TASK_SQL} "
            "WHERE x.runner_id=? AND j.bot=x.bot AND j.state='queued' AND m.id=j.message_id "
            "AND m.from_actor=? AND m.kind='notice' "
            "AND m.body LIKE 'New task from %' AND t.status IN ('done','closed') "
            "AND m.to_actor=t.owner AND NOT EXISTS (SELECT 1 FROM job_recovery r WHERE r.job_id=j.id "
            "AND r.attempt_id=j.attempt_id AND r.decision='resume')", (who.runner_id,H.KEEPER)).fetchall()
        for old in obsolete:
            selected = None
            c.execute("UPDATE jobs SET state='cancelled' WHERE id=?", (old['id'],))
            c.execute("UPDATE messages SET delivered_at=coalesce(delivered_at,?) WHERE id=?", (H.now(),old['message_id']))
            H.event(c,H.KEEPER,'job.suppress',old['id'],{'reason':'Task completed before initial notice delivery','task_id':old['task_id']})
        why = runner_versions.paused(c, who.runner_id)
        if why:
            return {"attempt": None, "paused": why}
        count = c.execute("SELECT count(*) FROM attempts WHERE runner_id=? AND state IN ('leased','running')",
                          (who.runner_id,)).fetchone()[0]
        if count >= runner["capacity"]:
            return {"attempt": None}
        # A machine that has only just come back cannot be trusted with a 90 s lease yet.
        # Its work keeps its place in the queue and goes out once the machine is really up.
        if not self.awake(awake_since, now):
            return {"attempt": None}
        row = selected if selected is not None else self.candidate(c, who, body, runner)
        if not row:
            return {"attempt": None}
        msg = H.message(c, row["message_id"])
        if not privacy.message_readable(c, "bot:" + row["bot"], msg):
            c.execute("UPDATE jobs SET state='cancelled' WHERE id=?", (row["id"],))
            H.event(c, H.KEEPER, "job.suppress", row["id"], {"reason": "unreadable"})
            return {"attempt": None}
        live_ref = (msg.get("refs") or {}).get("live_meeting")
        if (isinstance(live_ref, dict) and msg.get("from_actor") == H.KEEPER
                and msg.get("to_actor") == "bot:" + row["bot"]):
            from .live_meetings import claim_runner_turn
            meeting_claim = claim_runner_turn(c, live_ref.get("meeting_id"), live_ref.get("turn_id"),
                                              row["bot"], auth=self.auth)
            if meeting_claim["status"] != "claimed":
                return {"attempt": None}
        conv = H.conversation(c, msg["conversation_id"])
        task = H.task(c, H.message_task_id(msg, conv)) if H.message_task_id(msg, conv) else None
        if task and task["owner"] == "bot:" + row["bot"] and row["bot"] != self.store.settings.assistant_bot:
            # A bot's task filed before task rooms moves to its own room now; this turn's message stays put.
            # The Assistant's stay in the person's own room, where its turns act for that person.
            task = H.give_task_room(c, task)
        from .shared_bots import follow
        followed = follow(c, row["bot"], json.loads(
            c.execute("SELECT config_json FROM bot_config WHERE bot=?", (row["bot"],)).fetchone()[0]))
        # A check subtask's turns run on its own model, in its own session (backend/check_models.py).
        check = check_models.of(c, task["id"], row["bot"]) if task else None
        followed = check_models.apply(followed, check)
        roots, lanes = roots_on(runner), lanes_on(runner)
        lane = job_lane(c, row["bot"], followed, msg, conv, lanes, roots)
        token = secrets.token_urlsafe(32)
        until = H.shift(H.now(), seconds=self.store.settings.lease_seconds)
        woke, source = run_triggers.of_message(msg)
        turn = H.turn_start(c, H.KEEPER, row["bot"], trigger="task" if task else msg["kind"],
                            message_id=msg["id"], task_id=task["id"] if task else None,
                            trigger_kind=woke, trigger_source=source)
        aid = turn["id"]
        c.execute("INSERT INTO attempts(id,job_id,bot,runner_id,generation,token_hash,state,lease_until,created,lane) "
                  "VALUES(?,?,?,?,?,?,'leased',?,?,?)",
                  (aid, row["id"], row["bot"], who.runner_id, row["generation"], digest(token), until, H.now(), lane))
        c.execute("UPDATE jobs SET state='leased',attempt_id=? WHERE id=?", (aid, row["id"]))
        # What woke this turn and the wake preset in force, for its run record (backend/wake_presets.py).
        job = c.execute("SELECT wake_preset,wake_class,hold_until FROM jobs WHERE id=?", (row["id"],)).fetchone()
        c.execute("UPDATE attempts SET wake_json=? WHERE id=?", (json.dumps(wake_presets.record(c, job, msg)), aid))
        c.execute("INSERT OR IGNORE INTO attempt_conversations VALUES(?,?)", (aid, conv["id"]))
        held = None
        if check:
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
        config = models_preset.with_gateway_fallback(record, here, models_preset.main_config(record, here, config))
        # A bot delivered through a desktop app: who it is in the app, beside `app_delivery` (backend/app_delivery.py).
        config = app_delivery.job_config(config)
        if held:
            config = {**config, "primary_held": held.get("until") or H.now()}
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
        # Next-run tasks ride along with whatever woke the bot: one run, one prompt, each task
        # still its own task to mark done. Only a runner that puts them in its prompt gets them.
        # Any lane carries them: a bot whose turns are all tasks and routines never has a main-lane
        # turn. The claim holds the write lock and `carried_by` marks each one taken, so two lanes
        # never carry the same task.
        carried = []
        # A check runs on another model for one subtask: the bot's other work waits for its own turns.
        if getattr(body, "next_run", False) and not check:
            if row["bot"] == "librarian":
                from .docs import refresh_generated_docs
                refresh_generated_docs(c)  # Also covers a Librarian enabled after the upgrade.
            for item in H.next_run_tasks(c, row["bot"], exclude=task["id"] if task else None):
                if not H.task_private_readable(c, bot_who.actor, item):
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
        if getattr(body, "next_run", False) and not check:
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
        from .views import roster
        people = roster(c)
        inbox = P.inbox_person(row["bot"], people)
        parked = c.execute("SELECT onboarding_state FROM bot_config WHERE bot=?", (row["bot"],)).fetchone()
        from .chat_goals import current
        from .subscriptions import effective
        from . import worktrees
        # A runner from before task goals would treat a task room's goal as running without ever setting it.
        chat_goal = current(c, conv["id"]) if not (conv.get("kind") == "task" and not task_goals_on(runner)) else None
        if chat_goal and chat_goal["bot"] != row["bot"]:
            # Another bot's turn in the room (the requester answering) works without the owner's goal.
            chat_goal = None
        return {"attempt": {"chat_goal": chat_goal, "routine": routine, "id": aid,
                            "profile": effective(c, row["bot"])[0],
                            "computer_label": runner["label"],
                            # A starter bot's chat while it is `needs_setup` is its setup (runner prompt).
                            "onboarding": (parked["onboarding_state"] if parked else "") or "",
                            # The mailboxes an inbox bot's turn may ask its runner for mail access to: the one it declares
                            # (its person's email when it declares none), then the people below them.
                            "mailboxes": routines.token_mailboxes(c, row["bot"], people) if inbox else [], "next_run": carried, "notes": notes, "job_id": row["id"], "bot": row["bot"], "lane": lane,
                            "credential_vault": c.execute("SELECT 1 FROM credential_grants WHERE subject=? AND revoked IS NULL LIMIT 1",
                                                          ("bot:" + row["bot"],)).fetchone() is not None,
                            # A run on a Tico model reaches the Gateway through its runner, which alone is handed the key
                            # (runner-gateway-credentials; runner-rs/src/gateway_relay.rs).
                            "gateway": gateway_connection.wanted(config),
                            "generation": row["generation"], "lease_until": until,
                            "lease_seconds": self.store.settings.lease_seconds, "token": token,
                            "message": msg, "conversation": conv, "task": task,
                            # The task's set-up worktrees on this computer, added to its session (runner-rs/src/sessions.rs).
                            "worktrees": worktrees.attached(c, task["id"], who.runner_id, row["bot"]) if task else [],
                            "history": history, "config": config, "principal": principal,
                            # Bumped by a session clear or reset (views.py clear_session, app.py reset_bot_sessions):
                            # a runner that sees it change starts a fresh provider thread (runner-rs/src/execute.rs).
                            "session_epoch": epoch["epoch"] if epoch else 0,
                            # Moved by any clear or reset of this bot: the runner starts the bot's shared thread anew.
                            "bot_session_epoch": bot_epoch[0] if bot_epoch else 0}}

    def attempt(self, c, who, aid, active=True, final=False):
        """`final` is a runner handing in the result of a turn it has finished. That result
        exists nowhere else, so a lapsed lease must not be grounds for refusing it: ownership
        still decides, the clock does not."""
        self.runner(c, who)
        self.served_at = H.now()
        row = c.execute("SELECT a.*,x.runner_id AS assigned_runner,x.generation AS assigned_generation "
                        "FROM attempts a JOIN assignments x ON x.bot=a.bot WHERE a.id=?", (aid,)).fetchone()
        if not row or row["runner_id"] != who.runner_id:
            raise Problem("forbidden", "This execution attempt is not yours", 403)
        if not privacy.attempt_readable(c, "bot:" + row["bot"], aid):
            raise Problem("stale_lease", "Task access expired; stop this attempt", 409)
        current = self.current_owner(c, who, row)
        if active and current and row["state"] == "expired":
            row = self.restore(c, row) or row
        if final and row["state"] == "expired":
            return row
        if active and (row["state"] not in ("leased", "running") or row["lease_until"] <= H.now() or not current):
            raise Problem("stale_lease", "Execution ownership expired; stop this attempt", 409)
        return row

    @staticmethod
    def current_owner(c, who, row):
        """Whether this runner still owns this bot's work: the assignment names it, at the same
        generation, and the bot is active. A transfer to another Mac ends ownership, and with it
        any say over the job."""
        return bool(row["assigned_runner"] == who.runner_id and row["assigned_generation"] == row["generation"]
                    and H.bot(c, row["bot"])["state"] == "active")

    def restore(self, c, row):
        """A running attempt the server let lapse comes back when its runner reports in within
        STALL_MAX, as long as nothing else touched the job: it is still uncertain and on this
        attempt, no review decided it, and no other attempt runs in its lane. The turn on the
        Mac never stopped, so its result is worth more than a review of a phantom interruption."""
        now = H.now()
        job = c.execute("SELECT state,message_id FROM jobs WHERE id=? AND attempt_id=?", (row["job_id"], row["id"])).fetchone()
        if (not row["finished"] or row["finished"] <= H.shift(now, seconds=-STALL_MAX)
                or not job or job["state"] != "uncertain"
                or c.execute("SELECT 1 FROM job_recovery WHERE job_id=? AND attempt_id=?", (row["job_id"], row["id"])).fetchone()
                or c.execute("SELECT 1 FROM attempts WHERE bot=? AND lane=? AND state IN ('leased','running')",
                             (row["bot"], row["lane"])).fetchone()):
            return None
        until = H.shift(now, seconds=self.store.settings.lease_seconds)
        c.execute("UPDATE attempts SET state='running',finished=NULL,lease_until=? WHERE id=?", (until, row["id"]))
        c.execute("UPDATE jobs SET state='running' WHERE id=?", (row["job_id"],))
        c.execute("UPDATE turns SET finished=NULL,exit=NULL,summary=NULL WHERE id=?", (row["id"],))
        current = H.message(c, job["message_id"])
        settle_status(c, row["bot"], "running", "Responding to queued work",
                      claimed=row["created"], task_id=H.message_task_id(current) or "")
        H.event(c, H.KEEPER, "attempt.lease-restored", row["id"],
                {"bot": row["bot"], "job_id": row["job_id"], "lapsed": row["finished"], "lease_until": until})
        return c.execute("SELECT a.*,x.runner_id AS assigned_runner,x.generation AS assigned_generation "
                         "FROM attempts a JOIN assignments x ON x.bot=a.bot WHERE a.id=?", (row["id"],)).fetchone()

    def settleable(self, c, who, row):
        """Whether a result that arrives after the lease lapsed may still settle its job. The
        test is the one `restore` uses without the clock: this runner still owns the bot, the job
        is still uncertain and still on this attempt, no review has decided it, and no other
        attempt is running in its lane. A review — a person's or `auto_reconcile`'s — always
        writes job_recovery and moves the job off `uncertain`, so a decision already taken wins
        and a late result can never undo one. Elapsed time is what hands a job from the runner to
        the sweep; it is not what makes settling safe, and the longest turns are exactly the ones
        most likely to outlive a lease."""
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
        if body.outcome != "completed" or not self.current_owner(c, who, row):
            return False
        job = c.execute("SELECT state FROM jobs WHERE id=? AND attempt_id=?", (row["job_id"], row["id"])).fetchone()
        return bool(job and job["state"] in ("cancelled", "queued"))

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

    def file_result(self, c, row, body):
        """A result whose job this runner no longer speaks for: a review decided it, another
        attempt owns the work now, or the bot moved to another Mac. The run is recorded against
        its own attempt so the text, the tokens and the outcome survive — a reviewer can now read
        what the run actually produced — and the job is left exactly as its owner left it. The
        attempt stays `expired`, which is the true account of its lease and keeps late output
        archivable against it."""
        c.execute("UPDATE attempts SET final_text=?,result_json=? WHERE id=?",
                  (body.text, encode(body.model_dump()), row["id"]))
        H.turn_finish(c, H.KEEPER, row["id"], exit_code=body.outcome, tokens_in=body.tokens_in,
                      tokens_out=body.tokens_out, summary=body.text[:2000], usage=self.run_usage(c, row, body))
        H.event(c, H.KEEPER, "attempt.late-result", row["id"],
                {"bot": row["bot"], "job_id": row["job_id"], "outcome": body.outcome})
        return {"attempt_id": row["id"], "outcome": body.outcome, "message": None, "filed": True}

    def renew(self, c, who, aid):
        row = self.attempt(c, who, aid)
        until = H.shift(H.now(), seconds=self.store.settings.lease_seconds)
        c.execute("UPDATE attempts SET lease_until=? WHERE id=?", (until, aid))
        # The runner renews every few seconds, so a person's Stop rides on the answer it already reads.
        return {"lease_until": until, "lease_seconds": self.store.settings.lease_seconds,
                **({"stop": True} if row["stop_requested"] else {})}

    def stop(self, c, who, bot, attempt_id=None, redirect=None):
        """A person who manages the bot asks its running turn to stop. The runner interrupts the turn
        when it next renews the lease, and `complete` settles it as stopped rather than held for review.

        With `redirect`, the caller's own message still queued in a conversation that turn works in is
        claimed first once it ends, so the next turn starts with it in the same session (Stop and redirect,
        for a harness that cannot take input mid-turn)."""
        if not H.bot(c, bot):
            raise Problem("not_found", "Unknown bot", 404)
        if not (self.auth.operator(c, who, bot) or self.auth.bot_manager(c, who, bot)):
            raise Problem("forbidden", "Only a person who manages this bot can stop it", 403)
        rows = c.execute("SELECT id,stop_requested FROM attempts WHERE bot=? AND state IN ('leased','running') "
                         "AND (? IS NULL OR id=?) ORDER BY created", (bot, attempt_id, attempt_id)).fetchall()
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

    def started(self, c, who, aid, body):
        row = self.attempt(c, who, aid)
        if row["state"] != "leased":
            raise Problem("already_started", "This attempt was already started", 409)
        c.execute("UPDATE attempts SET state='running',started=?,thread_id=? WHERE id=?",
                  (H.now(), body.thread_id, aid))
        c.execute("UPDATE turns SET thread_id=? WHERE id=?", (body.thread_id, aid))
        c.execute("UPDATE jobs SET state='running' WHERE id=?", (row["job_id"],))
        from .shared_bots import follow
        config = follow(c, row["bot"], json.loads(c.execute("SELECT config_json FROM bot_config WHERE bot=?", (row["bot"],)).fetchone()[0]))
        runtime, model = providers.bot_choice(c, self.store.settings, config)
        c.execute("INSERT INTO bot_sessions(bot,runtime,model,thread_id,runner_id,tokens_in,updated) "
                  "VALUES(?,?,?,?,?,0,?) ON CONFLICT(bot,runtime,model) DO UPDATE SET "
                  "thread_id=excluded.thread_id,runner_id=excluded.runner_id,updated=excluded.updated",
                  (row["bot"], runtime, model, body.thread_id, who.runner_id, H.now()))
        msg = c.execute("SELECT message_id FROM jobs WHERE id=?", (row["job_id"],)).fetchone()[0]
        H.task_session_started(c, row["bot"], row["lane"], msg, body.thread_id, runtime)
        H.mark_delivered(c, H.KEEPER, msg)
        settle_status(c, row["bot"], "running", "Responding to queued work",
                      claimed=row["created"], task_id=H.message_task_id(H.message(c, msg)) or "")
        return {"started": True}

    def inputs(self, c, who, aid):
        """Add eligible queued work to a running turn when its runtime can steer.

        A follow-up in the turn's own conversation belongs to that turn. Across conversations,
        only a waited bot question may interrupt it because that has its own explicit answer
        channel. The runner calls this endpoint only for hosts that advertise steering support.
        Re-deliver until acknowledged; a crash remains uncertain, not a new turn.
        """
        attempt = self.attempt(c, who, aid)
        if attempt["state"] != "running":
            return {"messages": []}
        # A comment its author took back mid-turn (H.task_comment_delete) still started the turn: read its row,
        # whose words are already gone, so the turn keeps polling. Its follow-ups are read the normal way below.
        origin = H.message(c, c.execute("SELECT message_id FROM jobs WHERE id=?", (attempt["job_id"],)).fetchone()[0],
                           include_deleted=True)
        origin_task = H.message_task_id(origin, H.conversation(c, origin["conversation_id"]))
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
        for row in c.execute("SELECT m.* FROM jobs j JOIN messages m ON m.id=j.message_id "
                             "WHERE j.bot=? AND j.state='queued' AND coalesce(json_extract(m.refs_json,'$.command'),0)!=1 "
                             # A redirect replaces the turn being stopped, so it starts the next one instead.
                             "AND j.redirect=0 AND (m.conversation_id=("
                             "SELECT active_message.conversation_id FROM attempts active_attempt "
                             "JOIN jobs active_job ON active_job.id=active_attempt.job_id "
                             "JOIN messages active_message ON active_message.id=active_job.message_id "
                             "WHERE active_attempt.id=?) OR m.conversation_id=? OR (m.kind='ask' AND m.wait_s>0 "
                             "AND m.from_actor LIKE 'bot:%')) ORDER BY m.rowid",
                             (attempt["bot"], aid, task_room)).fetchall():
            # Ten at a time, counted after the filters below: older work queued for other lanes must
            # never hide this lane's follow-ups.
            if taken >= 10:
                break
            if not privacy.message_readable(c, "bot:" + attempt["bot"], row):
                continue
            incoming_conversation = H.conversation(c, row["conversation_id"])
            incoming_task = H.message_task_id(H.message(c, row["id"]), incoming_conversation)
            if incoming_task and incoming_task != origin_task and row["kind"] == "notice":
                continue                    # a new task gets its own run, even in the same bot room
            if lanes and job_lane(c, attempt["bot"], followed, H.message(c, row["id"]), incoming_conversation,
                                  lanes, roots) != attempt["lane"]:
                continue                    # another lane's work is that lane's turn, never folded into this one
            if any(tid != origin_task and H.task_private(c, H.task(c, tid))
                   for tid in privacy.message_tasks(c, row, include_run=False)):
                # Folding another task's private message into this run would make the run private
                # too, shutting out whoever started it. It waits queued and gets its own run.
                continue
            if attempt["bot"] == H.FLEET_MAINTAINER:
                refs = json.loads(row["refs_json"] or "{}")
                origin = c.execute("SELECT j.message_id,m.from_actor FROM jobs j JOIN messages m ON m.id=j.message_id "
                                   "WHERE j.id=?", (attempt["job_id"],)).fetchone()
                origin_message = H.message(c, origin["message_id"], include_deleted=True)
                task_id = H.message_task_id(origin_message)
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
            if prior and prior["attempt_id"] != aid:
                # The earlier run did read this message, so it keeps the message's tasks for
                # privacy (attempt_tasks reads this event) once the row is no longer its own.
                privacy.record_moved_input(c, prior["attempt_id"], aid, row)
            c.execute("INSERT INTO attempt_inputs VALUES(?,?,NULL) ON CONFLICT(message_id) DO UPDATE SET "
                      "acked_at=CASE WHEN attempt_id=excluded.attempt_id THEN acked_at END,"
                      "attempt_id=excluded.attempt_id", (aid, row["id"]))
            c.execute("INSERT OR IGNORE INTO attempt_conversations VALUES(?,?)", (aid, row["conversation_id"]))
            c.execute("UPDATE jobs SET state='input',attempt_id=? WHERE message_id=?", (aid, row["id"]))
            taken += 1
        ids = c.execute("SELECT message_id FROM attempt_inputs WHERE attempt_id=? AND acked_at IS NULL", (aid,)).fetchall()
        # `attempt_id` lets the response check read these as the bot, as for the turn itself:
        # each message was just checked readable by the bot, and a private task's message is
        # never readable by the computer's own identity.
        return {"attempt_id": aid, "messages": [m for row in ids if (m := H.message(c, row[0]))
                             and privacy.message_readable(c, "bot:" + attempt["bot"], m)]}

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
        for event in body.events:
            payload = encode(event.payload)
            existing = c.execute("SELECT kind,payload_json FROM attempt_events WHERE attempt_id=? AND seq=?",
                                 (aid, event.seq)).fetchone()
            if existing:
                if existing["kind"] != event.kind or existing["payload_json"] != payload:
                    raise Problem("event_conflict", "An event sequence was reused with different content", 409)
                continue
            if event.seq != seq + 1:
                raise Problem("event_gap", f"Expected event {seq + 1}", 409)
            if row["state"] in ("completed", "failed", "interrupted"):
                raise Problem("terminal", "Cannot append new output to a completed attempt", 409)
            c.execute("INSERT INTO attempt_events(attempt_id,seq,kind,payload_json,created) VALUES(?,?,?,?,?)",
                      (aid, event.seq, event.kind, payload, H.now()))
            if event.kind == "goal" and row["lease_until"] > H.now() and row["state"] != "expired":
                from .chat_goals import report
                report(c, row, event.payload)
            seq = event.seq
        c.execute("UPDATE attempts SET last_seq=? WHERE id=?", (seq, aid))
        return {"ack_seq": seq, "historical": row["lease_until"] <= H.now() or row["state"] == "expired"}

    def complete(self, c, who, aid, body):
        row = self.attempt(c, who, aid, final=True)
        # `attempt` has already given `restore` its chance. What is left is a finished turn whose
        # lease lapsed under it: settle the job with it when the job is still waiting, and file it
        # against the attempt when it is not. Refusing it was how a run that opened three pull
        # requests came back as a phantom interruption.
        late = row["state"] == "expired"
        # The one turn that ends before it starts: a Tico model on a runner that can't run one (clients/gateway.py
        # NOT_ISOLATED). Settled now, so the bot says why once rather than its lease lapsing into a retry.
        # Likewise a turn sent away from a held login (`primary_held`) that found no fallback ready after all: nothing
        # ran, and it goes back to wait out the hold (backend/login_holds.py after_turn).
        unstarted = (row["state"] == "leased" and body.outcome == "failed"
                     and (any(gateway.run_code(error) == gateway.NOT_ISOLATED for error in limit_errors(c, aid))
                          or body.primary_held and body.limited and not body.fallback))
        if not late and row["state"] != "running" and not unstarted:
            raise Problem("not_started", "Acknowledge start before completing work", 409)
        if row["last_seq"] != body.last_seq:
            raise Problem("event_gap", "Upload all events before completing the attempt", 409)
        if late and not self.settleable(c, who, row) and not self.settles_late(c, who, row, body):
            return self.file_result(c, row, body)
        if late:
            H.event(c, H.KEEPER, "attempt.late-settled", aid, {"bot": row["bot"], "job_id": row["job_id"],
                                                                "outcome": body.outcome})
        self.release_undelivered(c, aid, body.undelivered)
        # A message its author took back during the turn (H.task_comment_delete) still started it: read its row.
        msg = H.message(c, c.execute("SELECT message_id FROM jobs WHERE id=?", (row["job_id"],)).fetchone()[0],
                        include_deleted=True)
        conv = H.conversation(c, msg["conversation_id"])
        actor = "bot:" + row["bot"]
        reply = None
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
            task = H.task(c, H.message_task_id(msg, conv)) if H.message_task_id(msg, conv) else None
            # A bot delivered through a desktop app: the computer may have reported the reply from the app first.
            same = same or app_delivery.echoes(c, conv["id"], actor, text, any_source=False)
            if not already and not same and (msg["kind"] in ("say", "ask", "steer") or task):
                target = msg["from_actor"]
                replyable = not str(target).startswith("bot:") or msg["kind"] == "ask"
                if target == H.KEEPER and task:
                    humans = [p for p in conv.get("participants") or [] if str(p).startswith("human:")]
                    target = ((conv.get("owner_actor") if conv.get("scope") == "personal" else None)
                              or (humans[0] if humans else None) or task["requester"])
                    # A task in the bot's chat room always delivers, and so does one filed from a chat into
                    # its own room (H.task_from_chat). Any other dedicated task thread waits until the owner
                    # has marked the work done, or, for a finished turn that came in after its lease lapsed,
                    # until the task was closed under it: no later turn will answer that room.
                    if conv.get("kind") == "chat" or (conv.get("task_id") == task["id"] and H.task_from_chat(c, task["id"])):
                        replyable = target not in (H.KEEPER, actor)
                    else:
                        replyable = actor == task["owner"] and (
                            task["status"] == "done" or (late and task["status"] == "closed"))
                if target not in (H.KEEPER, actor) and replyable:
                    refs = {"turn_id": aid, **({"task": task["id"]} if task else {}),
                            **({"warnings": notices} if notices and msg["kind"] != "ask" else {})}
                    try:
                        privacy.require_destination(c, Identity(actor, "bot", attempt_id=aid), target, conv["id"], refs, msg)
                    except Problem as refused:
                        if refused.code != "privacy":
                            raise
                        # The run is done; only its reply may not go here. The result stays on the
                        # run, which only the private task's own parties can read, and the person
                        # who expected the reply is told, with nothing from the task named.
                        if H.is_human(target):
                            H._write_message(c, H.KEEPER, target, f"{row['bot']} finished, but its reply drew on "
                                             "a private task you can't read, so it was not posted here.",
                                             conv, "notice", {"withheld": {"bot": row["bot"]}}, None, None)
                    else:
                        reply = H.answer(c, actor, msg["id"], body.text) if msg["kind"] == "ask" else H.say(
                            c, actor, target, text, conversation_id=conv["id"],
                            in_reply_to=msg["id"], refs=refs)
        if reply:
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
        # A turn on a check subtask ran on the check's model, not the bot's (backend/check_models.py).
        # Decided by what the claim handed out: a check handed to another bot mid-turn still ran on the check's model.
        check_task, check = check_models.handed_out(c, aid)
        c.execute("UPDATE attempts SET state=?,finished=?,final_text=?,result_json=? WHERE id=?",
                  (body.outcome, H.now(), body.text,
                   encode({**body.model_dump(), **({"check": check["runtime"]} if check else {})}), aid))
        # A usage-limited turn did nothing and had no effects, so the job goes back in the
        # queue however often it happens; the bot cools off (claim() honours the window) and
        # a third limit in a row waits longer and tells a person in Needs attention. Only
        # interrupted or failed work, which may have acted, is held for review.
        # A turn a person stopped is over by their choice: nothing to retry and nothing to review.
        stopped = bool(row["stop_requested"]) and body.outcome != "completed"
        limited = body.outcome != "completed" and body.limited and not stopped
        # Same reasoning, different cause: the runner reports `retryable` when the runtime was
        # refused before the turn began because it could not renew its own sign-in, and only
        # when that turn produced no message and used no tool. Nothing happened, so requeue it
        # rather than blocking the bot behind an uncertain job until a person clears it.
        retryable = body.outcome != "completed" and body.retryable and not limited and not stopped
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
        if body.outcome == "failed" and not limited and not retryable and not stopped:
            kinds = {r[0] for r in c.execute("SELECT DISTINCT kind FROM attempt_events WHERE attempt_id=?", (aid,))}
            tries = c.execute("SELECT count(*) FROM attempts WHERE job_id=?", (row["job_id"],)).fetchone()[0]
            quick = (row["started"] or row["created"]) >= H.shift(H.now(), seconds=-NO_EFFECT_WITHIN)
            silent = (kinds <= SILENT_KINDS and (quick or tico_code == gateway.UNAVAILABLE) and tries < NO_EFFECT_TRIES
                      and tico_code not in gateway.FINAL)
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
        requeue = not rejected_acted if rejected else limited or retryable or silent
        c.execute("UPDATE jobs SET state=? WHERE id=?",
                  ("completed" if body.outcome == "completed" else "cancelled" if stopped
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
                                 requeued=requeue)
        turn_task = H.message_task_id(msg, conv)
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
            task_id = H.message_task_id(message, H.conversation(c, message["conversation_id"]))
            deferred = False
            if task_id and task_id != turn_task:
                task = H.task(c, task_id)
                outcome = c.execute("SELECT 1 FROM task_events WHERE task_id=? AND actor=? AND ts>=? "
                                    "AND (field='note' OR (field='status' AND new IN ('done','closed','declined'))) LIMIT 1",
                                    (task_id, actor, row["started"] or row["created"])).fetchone()
                deferred = bool(task and task["status"] in H.ACTIVE_STATUSES and not outcome)
            state = ("queued" if deferred else "completed" if body.outcome == "completed" and answered else
                     "cancelled" if stopped else "queued" if requeue else "uncertain")
            c.execute("UPDATE jobs SET state=?,attempt_id=CASE WHEN ? THEN NULL ELSE attempt_id END WHERE message_id=?",
                      (state, deferred, incoming["message_id"]))
        if body.outcome == "completed":
            # Notices that queued in this turn's conversation before it was claimed were in the
            # room page it was given, so the turn has read them. Leaving their jobs queued costs
            # one more run each to read the same thing again: a bot could accumulate
            # dozens of Slack digests and hourly occurrences stacked this way. A bot's chat
            # room carries notices for many tasks, so only the ones about this turn's own task
            # (or, for a turn with no task, the ones about none) count as read.
            turn_task = H.message_task_id(msg, conv)
            for extra in c.execute("SELECT j.id,j.message_id FROM jobs j JOIN messages m ON m.id=j.message_id "
                                   "WHERE j.bot=? AND j.state='queued' AND m.kind='notice' "
                                   "AND m.conversation_id=? AND m.created<=? AND j.id<>?",
                                   (row["bot"], conv["id"], row["created"], row["job_id"])).fetchall():
                if H.message_task_id(H.message(c, extra["message_id"]), conv) != turn_task:
                    continue
                c.execute("UPDATE jobs SET state='completed',attempt_id=? WHERE id=?", (aid, extra["id"]))
                H.mark_delivered(c, H.KEEPER, extra["message_id"])
                H.event(c, H.KEEPER, "job.coalesce", extra["id"], {"attempt_id": aid, "job_id": row["job_id"]})
        if body.outcome == "completed":
            # Each next-run task this run carried and left: back to the next run, unless it was worked or deferred.
            quiet_work.settle(c, aid, row["bot"], row["started"] or row["created"])
        # The runner's notices (a file left out of the commit) are shown beside the reply, not as the turn's summary.
        H.turn_finish(c, H.KEEPER, aid, exit_code=body.outcome, tokens_in=body.tokens_in,
                      tokens_out=body.tokens_out, summary=split_runner_notices(body.text)[0][:2000],
                      usage=self.run_usage(c, row, body))
        # The checkpoint goes on the task whose session the turn ran in: a subtask's turn runs in its session
        # root's lane (`task:<root>`, set at claim), so it checkpoints that root, never the subtask, even if
        # the subtask moved during the turn. A lane naming a task this bot does not own checkpoints nothing.
        turn_task_id = H.message_task_id(msg, conv)
        lane_task = row["lane"][len("task:"):] if turn_task_id and str(row["lane"] or "").startswith("task:") else ""
        if lane_task and lane_task != turn_task_id:
            root = H.task(c, lane_task)
            checkpoint_task = root if root and root["owner"] == "bot:" + row["bot"] else None
        else:
            checkpoint_task = H.task(c, turn_task_id) if turn_task_id else None
        if checkpoint_task:
            bot_config = c.execute("SELECT config_json FROM bot_config WHERE bot=?", (row["bot"],)).fetchone()
            own = sessions.session_key(json.loads(bot_config[0]) if bot_config else {}, row["bot"],
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
        if body.outcome == "completed" and body.text and checkpoint_task:
            # Where the task stood after this turn: a later turn that cannot resume this session
            # starts from it (runner prompt). It is not the task's activity, so `updated` stays.
            c.execute("UPDATE tasks SET checkpoint=?,checkpoint_at=?,checkpoint_by=? WHERE id=?",
                      (split_runner_notices(body.text)[0].strip()[:CHECKPOINT_CHARS], H.now(), actor,
                       checkpoint_task["id"]))
        usage_limits.after_run(c, row["bot"], self.store.settings.owner_email)
        config = c.execute("SELECT config_json FROM bot_config WHERE bot=?", (row["bot"],)).fetchone()
        runtime, model = providers.bot_choice(c, self.store.settings, json.loads(config[0]) if config else {})
        if check:
            runtime, model = check["runtime"], check["model"]
        if row["thread_id"] and body.tokens_in is not None:
            c.execute("UPDATE bot_sessions SET tokens_in=?,updated=? WHERE bot=? AND runtime=? AND model=? AND thread_id=?",
                      (int(body.tokens_in), H.now(), row["bot"], runtime, model, row["thread_id"]))
        login_hold = {}
        # The Tico AI Gateway's cap and address block are the company key's, not a provider login's: they keep their
        # own waits (reset_cooldown).
        gateway_wait = tico_code == gateway.RATE_LIMITED or any(gateway.cap_reached(e) for e in limit_errors(c, aid))
        if not check and not stopped and not gateway_wait and (limited or body.outcome == "completed"):
            from .subscriptions import effective
            login_hold = login_holds.after_turn(c, row["runner_id"], row["bot"], effective(c, row["bot"])[0] or "",
                                                runtime, body, aid, H.now())
        if rejected:
            self.mark_rejected(c, row["runner_id"], body.auth_rejected.runtime, body.auth_rejected.reason, body.profile_used)
            H.event(c, H.KEEPER, "attempt.auth_rejected", aid,
                    {"bot": row["bot"], "runtime": body.auth_rejected.runtime, "reason": body.auth_rejected.reason})
        if body.fallback:
            # The runner ran this turn on the bot's fallback harness: countable here, and
            # named in Runs, so a primary that keeps failing is noticed. `primary_runtime` is what the bot is
            # set to and did not do the work; `model` is what did, when the runner counted it.
            ran = (body.usage.segments or [body.usage])[-1] if body.usage else None
            H.event(c, H.KEEPER, "attempt.fallback", aid,
                    {"bot": row["bot"], "job_id": row["job_id"], "primary_runtime": runtime, "fallback": body.fallback,
                     "model": (ran.model or None) if ran else None,
                     "outcome": body.outcome, **({"primary_limited": True} if body.primary_limited else {})})
        if stopped:
            person = row["stop_requested"]
            name = (H.human(c, H.actor_id(person)) or {}).get("name") or H.actor_id(person)
            settle_status(c, row["bot"], "idle", "", claimed=row["created"])
            # Quiet: the bot reads it with the room next time; it does not start a run of its own.
            H.say(c, H.KEEPER, actor, f"Stopped by {name}", conversation_id=conv["id"], kind="notice",
                  refs={"quiet": True, "stopped_by": person, "turn_id": aid})
            H.event(c, H.KEEPER, "attempt.stopped", aid, {"bot": row["bot"], "job_id": row["job_id"], "by": person})
            # A worktree this turn was waiting for is not wanted any more (backend/worktree_waits.py).
            from .worktree_waits import cancel_for_attempt
            cancel_for_attempt(c, aid, f"Stopped by {name}")
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
            settle_status(c, row["bot"], "idle", f"{runtime} usage limit on a check at {hit[11:16]}; the check "
                          f"retries after {retry[:10]} {retry[11:16]} UTC", claimed=row["created"])
            H.event(c, H.KEEPER, "attempt.check_limited", aid,
                    {"bot": row["bot"], "job_id": row["job_id"], "task_id": check_task, "runtime": runtime,
                     "retry_after": retry})
        elif limited:
            streak = limit_streak(c, row["bot"])
            hit = H.now()
            retry = login_hold.get("until") or H.shift(hit, seconds=limit_cooldown(c, row["bot"]))
            kind = "API quota or billing limit" if billing_limit(c, attempt_id=aid) else "usage limit"
            if any(gateway.cap_reached(error) for error in limit_errors(c, aid)):
                # The Tico AI Gateway's monthly cap for the company's key (clients/gateway.py): said as it is.
                focus = (f"{gateway.CAP_MESSAGE} at {hit[11:16]}: the gateway key has used this month's cap; "
                         f"retrying after {retry[:10]} {retry[11:16]} UTC")
            elif tico_code == gateway.RATE_LIMITED:
                focus = (f"Tico AI Gateway rate limited at {hit[11:16]} after too many refused keys from this "
                         f"address; retrying after {retry[:10]} {retry[11:16]} UTC")
            elif login_hold:
                # The hold on the login (backend/login_holds.py): that login's provider, its own words and its streak,
                # so a fallback's quota is not reported as the bot's own runtime's.
                said = (f"{login_hold['runtime']} " + ("API quota or billing limit"
                        if BILLING_LIMIT_RE.search(login_hold.get("error") or "") else "usage limit")
                        + (f" on the {login_hold['profile']} login" if login_hold.get("profile") else "")
                        + f" at {hit[11:16]}")
                if login_hold.get("reset"):
                    focus = (f"{said}; resumes at {retry[11:16]} UTC"
                             + ("" if retry[:10] == hit[:10] else f" on {retry[:10]}") + " when it resets")
                else:
                    focus = (said + (", third in a row" if login_hold["streak"] >= LIMIT_STRIKES else "")
                             + f"; retrying after {retry[:10]} {retry[11:16]} UTC")
            elif body.limit_resets_at and provider_reset(last_limit(c, row["bot"]), H.parse_ts(hit)):
                focus = (f"{runtime} {kind} at {hit[11:16]}; resumes at {retry[11:16]} UTC"
                         + ("" if retry[:10] == hit[:10] else f" on {retry[:10]}") + " when it resets")
            else:
                focus = (f"{runtime} {kind} at {hit[11:16]}" + (", third in a row" if streak >= LIMIT_STRIKES else "")
                         + f"; retrying after {retry[:10]} {retry[11:16]} UTC")
            H.status_set(c, H.KEEPER, row["bot"], state="limited", focus=focus)
            # Every limit restarts the cooldown; status_set keeps `since` while the state stays the same.
            c.execute("UPDATE bot_status SET since=? WHERE bot=?", (hit, row["bot"]))
            H.event(c, H.KEEPER, "attempt.limited", aid,
                    {"bot": row["bot"], "job_id": row["job_id"], "runtime": runtime, "streak": streak,
                     "retry_after": retry, "provider_reset": bool(body.limit_resets_at)})
        elif rejected and any(gateway.key_refused(error) for error in limit_errors(c, aid)):
            settle_status(c, row["bot"], "idle" if requeue else "crashed",
                          "The Tico AI Gateway key is not working; save a new one in Settings > AI providers",
                          claimed=row["created"])
        elif rejected:
            settle_status(c, row["bot"], "idle" if requeue else "crashed",
                          f"{body.auth_rejected.runtime} sign-in was rejected; " +
                          ("sign in on this computer to continue" if requeue else "this run is saved for review"),
                          claimed=row["created"])
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
                          claimed=row["created"])
        return {"attempt_id": aid, "outcome": body.outcome, "message": reply}

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
        for row in c.execute("SELECT * FROM jobs WHERE bot=? AND state='uncertain' ORDER BY created", (bot,)):
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
            calls = recorded_tools(c, row['attempt_id'])
            acted = sorted({call['tool'] for call in calls if call['tool'] not in READ_ONLY_TOOLS})
            if acted:
                reason = (f"The run stopped after using a tool that can change things ({', '.join(acted)}). Tico "
                          "cannot tell whether it finished, so retrying automatically could repeat the action.")
            elif calls:
                reason = "The run stopped after only reading, so trying it again repeats nothing."
            else:
                reason = ("The run stopped before it returned an answer. Its runner did not record "
                          "enough detail for Tico to prove that retrying automatically is safe.")
            jobs.append({'id':row['id'], 'attempt_id':row['attempt_id'], 'readable':readable,
                         'task':{'id':task['id'],'title':task['title'],'status':task['status'],'owner':task['owner'],
                                 'owner_name':owner_name(c, task['owner'])} if task and readable else None,
                         'request':message['body'] if readable else '',
                         'attempt':(dict(attempt) if readable else
                                    {**dict(attempt), 'final_text': ''}) if attempt else None,
                         'output':output if readable else [], 'tools':calls if readable else [], 'reason':reason})
        return {'jobs':jobs}

    def reconcile_job(self, c, who, job_id, body):
        row = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row or not self.reviewer(c, who, row['bot']):
            raise Problem("forbidden", "Only this bot's operator or BotOps can review interrupted work", 403)
        if (not body.acknowledge_uncertain_effects or row['state'] != 'uncertain'
                or row['attempt_id'] != body.attempt_id):
            raise Problem("uncertain", "This run changed or its external effects have not been reviewed", 409)
        # Only a run in the job's own lane can race the decision; the bot's other tasks run on.
        if c.execute("SELECT 1 FROM attempts WHERE bot=? AND state IN ('leased','running') AND lane="
                     "coalesce((SELECT lane FROM attempts WHERE id=?),'main')", (row['bot'], row['attempt_id'])).fetchone():
            raise Problem("active_attempt", "Wait for the active run to finish before reconciling", 409)
        note = body.note.strip()
        if len(note) < 20:
            raise Problem("review_note", "Describe completed work and what should happen next", 422)
        return self._decide(c, row, body.attempt_id, body.decision, note, who.actor)

    def _decide(self, c, row, attempt_id, decision, note, actor, *, action='job.reconcile', shown=None):
        """Record one decision on an uncertain job: resume queues it again, dismiss releases it. `shown` is the
        line people read when the note itself is written to the bot."""
        job_id = row['id']
        state = 'queued' if decision == 'resume' else 'cancelled'
        c.execute("INSERT INTO job_recovery VALUES(?,?,?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET "
                  "attempt_id=excluded.attempt_id,decision=excluded.decision,note=excluded.note,"
                  "actor=excluded.actor,created=excluded.created",
                  (job_id,attempt_id,decision,note,actor,H.now()))
        if decision == 'dismiss':
            c.execute("UPDATE messages SET delivered_at=coalesce(delivered_at,?) WHERE id=?", (H.now(),row['message_id']))
        c.execute("UPDATE jobs SET state=? WHERE id=?", (state,job_id))
        H.event(c, actor, action, job_id, {'attempt_id':attempt_id, 'decision':decision, 'note':note,
                                           **({'shown':shown} if shown else {})})
        if not c.execute("SELECT 1 FROM jobs WHERE bot=? AND state='uncertain'", (row['bot'],)).fetchone():
            settle_status(c, row['bot'], 'idle', 'Stopped run reviewed')
        return {'job_id':job_id, 'state':state}

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

    def drop_unreadable(self, c):
        """Cancel every queued job whose message its bot may no longer read: a private task it was taken off, say.

        The claim passes over such a job for good (job_refusal's "unreadable"), so it would sit queued forever,
        never claimed or expired, and age Health's queue_idle. The bot never read the message, so nothing it was
        owed is lost; the message itself stays where it is. Held jobs too: a wake preset's release or a drain's end
        would only hand the claim the same unreadable message. Runs from the scheduler, once a minute."""
        dropped = []
        # From the bots to their queued jobs (jobs_queue), not from every job ever run.
        for row in c.execute("SELECT j.id,j.bot,j.message_id FROM bots b CROSS JOIN jobs j "
                             "WHERE j.bot=b.slug AND j.state='queued' ORDER BY j.created,j.id").fetchall():
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

    def auto_reconcile(self, c):
        """Settle the interrupted work nobody needs to look at, so a person is only asked about
        a run that actually did something.

        Runs from the scheduler's tick, after `expire`. Two decisions are safe without a person:

        - **Dismiss** when the task the delivery was for is already done or closed. There is
          nothing left to deliver, and rows that held bots were often this.
        - **Resume** when the attempt recorded no tool call other than a read (READ_ONLY_TOOLS) and
          consumed no approval: it read, it thought, it was cut off. Running it again repeats
          nothing. This needs a host that reports its tool calls (`reports_tools`); a run from a
          host that does not is silent either way, so it is held like one that acted.

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
        decided = []
        rows = c.execute("SELECT j.*, a.started, a.finished, a.state AS attempt_state, "
                         "coalesce(a.finished, j.created) <= ? AS aged "
                         "FROM jobs j LEFT JOIN attempts a ON a.id=j.attempt_id "
                         "WHERE j.state='uncertain' AND (coalesce(a.finished, j.created) <= ? "
                         "OR a.state='interrupted') "
                         "AND NOT EXISTS(SELECT 1 FROM attempts t WHERE t.bot=j.bot AND t.state IN ('leased','running') "
                         "AND t.lane=coalesce(a.lane,'main')) "
                         "ORDER BY j.created", (cutoff, cutoff)).fetchall()
        for row in rows:
            with isolated(c, "auto_reconcile", row["id"]):
                held = row['attempt_id'] and c.execute("SELECT 1 FROM events WHERE action='job.held' AND target=?",
                                                       (row['attempt_id'],)).fetchone()
                calls, acted, safe = [], [], False
                if not held:
                    config = c.execute("SELECT config_json FROM bot_config WHERE bot=?", (row['bot'],)).fetchone()
                    declared = json.loads(config[0]) if config else {}
                    calls = recorded_tools(c, row['attempt_id'])
                    acted = sorted({call['tool'] for call in calls if call['tool'] not in READ_ONLY_TOOLS})
                    window = (row['started'] or row['created'], row['finished'] or H.now())
                    consumed = c.execute("SELECT count(*) FROM approvals WHERE requested_by=? AND consumed_at IS NOT NULL "
                                         "AND consumed_at BETWEEN ? AND ?", ('bot:' + row['bot'], *window)).fetchone()[0]
                    safe = reports_tools(c, row['attempt_id'], declared) and not acted and not consumed
                # A message folded into another job's run counts that run as one of its own starts.
                started = c.execute("SELECT count(*) FROM attempts WHERE started IS NOT NULL AND (job_id=? OR id IN "
                                    "(SELECT attempt_id FROM attempt_inputs WHERE message_id=?))",
                                    (row['id'], row['message_id'])).fetchone()[0]
                if not row['aged'] and not (safe and started <= 1):
                    continue  # the usual wait: a person may look first
                msg = H.message(c, row['message_id'])
                conv = H.conversation(c, msg['conversation_id']) if msg else None
                task_id = H.message_task_id(msg, conv) if msg else None
                task = H.task(c, task_id) if task_id else None
                if not privacy.message_readable(c, 'bot:' + row['bot'], msg):
                    decided.append(self._decide(c, row, row['attempt_id'], 'dismiss',
                                               'Task access was revoked.', H.KEEPER, action='job.auto_reconcile'))
                    continue
                if task and task['status'] in ('done', 'closed'):
                    note = (f"Dismissed automatically: the task \"{task['title']}\" was already {task['status']} "
                            "when the interrupted delivery was reviewed, so there was nothing left to deliver.")
                    decided.append(self._decide(c, row, row['attempt_id'], 'dismiss', note, H.KEEPER,
                                                action='job.auto_reconcile'))
                    continue
                # Only runs that started count: a claim that lapsed before its run began (a computer offline, the
                # hub unreachable) says nothing about the request itself.
                if started > 1:
                    # Stopped twice the same way: not bad luck. Let it go and hand the cause to BotOps.
                    note = ("Dismissed automatically: this request stopped partway twice, so it is not retried "
                            "again. BotOps is looking into why.")
                    decision = self._decide(c, row, row['attempt_id'], 'dismiss', note, H.KEEPER,
                                            action='job.auto_reconcile')
                    if privacy.message_readable(c, 'bot:' + H.FLEET_MAINTAINER, msg):
                        self._tell_maintainer(c, row, msg)
                    decided.append(decision)
                    continue
                if not safe:
                    # Held: running it again could repeat an outside action.
                    if not held and row['attempt_id']:
                        H.event(c, H.KEEPER, 'job.held', row['attempt_id'], {'job_id': row['id'], 'tools': acted})
                        if privacy.message_readable(c, 'bot:' + H.FLEET_MAINTAINER, msg):
                            self._hand_to_maintainer(c, row, msg, task, acted)
                    continue
                if (row['attempt_state'] == 'expired' and row['started'] and row['finished']
                        and row['finished'] > H.shift(H.now(), seconds=-RESULT_GRACE)):
                    # Its lease lapsed but its runner never said the turn stopped: the finished turn may still come
                    # in (`settles_late`), and running it again now would answer the request twice.
                    continue
                reads = sorted({call['tool'] for call in calls})
                did = f"only read ({', '.join(reads)})" if reads else "recorded no tool call"
                note = (f"Resumed automatically: the interrupted run {did} and consumed no approval before it "
                        "stopped, so running it again repeats nothing. If this conversation already holds your "
                        "earlier partial work, check what already happened, then continue from there.")
                decided.append(self._decide(c, row, row['attempt_id'], 'resume', note, H.KEEPER,
                                            action='job.auto_reconcile'))
        return decided

    def _hand_to_maintainer(self, c, row, msg, task, acted):
        """One open BotOps task per bot to review its held runs: BotOps, not a person, recovers a stopped run."""
        maintainer = H.FLEET_MAINTAINER
        if row['bot'] == maintainer or not H.bot(c, maintainer):
            return
        title = f"Review {row['bot']}'s stopped runs held for a check"
        if c.execute("SELECT 1 FROM tasks WHERE title=? AND status IN ('open','doing','waiting','review','ready')",
                     (title,)).fetchone():
            return
        did = f"used {', '.join(acted)}" if acted else "ran on a host that does not report its tool calls"
        about = f" on the task \"{task['title']}\" (owner {owner_name(c, task['owner'])})" if task else ""
        # The request's words stay out: this task is company-wide, and the review shows them only to readers of
        # the conversation they came from.
        try:
            H.task_create(c, H.KEEPER, title,
                          f"A run of {row['bot']}{about} (job {row['id']}) stopped after it {did}, so Tico holds it "
                          "rather than repeat a possible outside action.\n"
                          f"Read GET bots/{row['bot']}/execution-review (the request, its tool calls and saved output), check what "
                          "already happened, then resume or dismiss each held run with POST jobs/<id>/reconcile.",
                          H.bot_actor(maintainer))
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
