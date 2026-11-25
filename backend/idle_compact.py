# pyright: strict
"""Idle compaction: a big Claude session is compacted while its prompt cache is still warm, not cold at the next turn.

A Claude Code session on a Claude plan writes its prompt cache with the 1-hour lifetime (execution.cache_write_split).
The next real turn after that hour sends the whole context again, and a turn past its `compact_at` compacts it cold.
When the newest turn of a session of CONTEXT_MIN tokens or more has been idle WARM_AFTER to WARM_BEFORE
(backend/wake_presets.py, the warm window inside the hour), Tico sends that same session `/compact` instead, so the
summary is made from a warm cache and the next turn starts small (T3 Code's idle compaction, with its 200k floor).

- Once per idle spell: the compaction is a newer run on the session, so the turn it followed is no longer the newest; a
  failed one counts too. The next real turn starts the count again.
- Only the session that turn ran in, on the computer that holds it (`computer_session_key`, `session_computers`): the
  start checks both, and refuses a lease anywhere else.
- Never on an API key or another harness: those caches live 5 minutes (`billing`, `handed_runtime`). Never cold: the
  claim and the start check again that the turn ended less than LATEST seconds ago (the hour less T3's 2-minute lead).
- Never while the bot has newer work on the conversation or task (queued, held or going), a ❓ or a person's answer or
  OK the task waits on, an active goal, background commands its runner still watches, or a dream queued (a dream forks
  the session it finds: compacting first would hand it the summary). Never on a task that ended or is snoozed, after a
  Stop, on a fallback's, side or card slot's session, on a routine's or dream's own run, for a bot that keeps one thread
  for everything or keeps nothing, or for a bot that cannot run.
- Unseen: the notice has no seq (a keeper notice to a bot), so no timeline, unread count or live view shows it; its run
  posts nothing, is no run on its task (task_wakes._runs_of), takes in nothing queued behind it (execution.inputs),
  carries no notes or deferred deliveries, and a run that fails settles quietly (no retry, no failure count, no Needs
  you). It carries the message the turn answered (`refs.carries`), so the claim keys its session as that turn's
  (execution.thread_payload), and names no sender (`refs.from`), so it is never read as a person's words.

`sweep` runs from the release-policy tick at most once a minute and reads only the runs that ended in the window
(attempts_finished_state). Each skip is logged at debug, with ids only.
"""

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

from . import hubdb_core, run_triggers, wake_presets

if TYPE_CHECKING:
    import sqlite3

Row = dict[str, Any]
LOG = logging.getLogger("tico.idle_compact")

PURPOSE = "idle compact"  # the run's purpose (run_triggers.purpose_of reads it from the notice's refs)
WAKE = "idle_compact"  # the notice's refs.wake (task_wakes.may_start)
BODY = "/compact"
CONTEXT_MIN = 200_000  # T3 Code's floor: a smaller session costs little to send cold
_WP: Any = wake_presets  # untyped
_RT: Any = run_triggers  # untyped
WARM_AFTER: int = _WP.WARM_AFTER
WARM_BEFORE: int = _WP.WARM_BEFORE
LATEST = 3600 - 120  # the 1-hour cache less T3's 2-minute lead: later than this the compaction would read it cold
_LIVE = ("queued", "leased", "running", "input", "stalled")
_MARKS = ",".join("?" * len(_LIVE))
# A fallback's run or a side session (execution.complete `fallback`, a source's continuation): not the session the
# next real turn resumes.
_FALLBACK = (
    "(json_extract(a.result,'$.fallback') IS NOT NULL OR json_extract(a.result,'$.source_continuation') IS NOT NULL "
    "OR coalesce(a.session_why,'')='fallback_side')"
)
# The runs that ended in the window on a Claude plan with a big context (attempts_finished_state).
_DUE = (
    "SELECT a.id FROM attempts a WHERE a.state='completed' AND a.finished_at > ? AND a.finished_at <= ? "
    "AND coalesce(a.purpose,'')<>? AND a.billing='subscription' AND a.handed_runtime='claude' "
    "AND coalesce(a.prompt_tokens,0) >= ?"
)
_RUN = f"SELECT a.*, {_FALLBACK} AS side, j.slot AS slot FROM attempts a LEFT JOIN jobs j ON j.id=a.job_id WHERE a.id=?"
# Any other run on the session begun since this one (attempts_bot_created): newer work, or a compaction already sent.
_NEWER = (
    "SELECT 1 FROM attempts WHERE bot_id=? AND created_at>=? AND computer_session_key=? AND id NOT IN (?,?) LIMIT 1"
)
# The bot's work queued, held or going on the conversation or the task (jobs_queue), the compaction's own left out.
_BUSY = (
    "SELECT 1 FROM jobs j JOIN messages m ON m.id=j.message_id "
    f"WHERE j.bot_id=? AND j.state IN ({_MARKS}) AND j.message_id<>? AND (m.conversation_id=? "
    "OR json_extract(m.refs,'$.task')=? OR (m.wake_key >= ? AND m.wake_key < ?)) LIMIT 1"
)
_DREAM = f"SELECT 1 FROM jobs WHERE bot_id=? AND state IN ({_MARKS}) AND wake_class='dream' LIMIT 1"
# Background commands the run left for its runner to watch (runner-rs afterwatch), not yet reported ended.
_WATCHED = (
    "SELECT 1 FROM attempt_events WHERE attempt_id=? AND kind='diagnostic' "
    "AND coalesce(json_extract(payload,'$.background_wait.kept'),0)>0 "
    "AND NOT EXISTS (SELECT 1 FROM events WHERE action='attempt.background-done' AND target=?) LIMIT 1"
)
_ASKED = "SELECT 1 FROM question_marks WHERE cleared_at IS NULL AND bot_id=? AND (task_id=? OR room_id=?) LIMIT 1"
_GOAL = "SELECT 1 FROM chat_goals WHERE conversation_id=? AND bot=? AND status='active'"

_core: Any = hubdb_core
bot_actor: Callable[[str], str] = _core.bot_actor
_last_minute: dict[str, str] = {}  # database file -> the clock minute its last sweep ran in


def _h() -> Any:
    from . import hubdb

    return hubdb


def ours(message: Row | None) -> bool:
    """Whether a message is Tico's idle-compaction notice. Only the keeper's: refs a bot sends are not trusted."""
    message = message or {}
    refs: Any = message.get("refs")
    refs = refs if isinstance(refs, dict) else _h()._json(refs, {})
    return (
        message.get("sender_id") == _core.TICO_ACTOR
        and isinstance(refs, dict)
        and cast("Row", refs).get("wake") == WAKE
    )


def _task_why(conn: "sqlite3.Connection", task_id: str) -> str | None:
    """A task that ended, is snoozed, waits on a person, or waits on a person's answer or OK (waiting_on)."""
    from . import waiting_on

    W: Any = waiting_on
    waits: list[Any] = W.waiting_on(conn, task_id)
    if not waits:
        return "the task ended"
    if waits[0].state in (W.SNOOZED, W.PERSON, W.PAUSED):
        return f"the task is {waits[0].state}"
    if any(w.ask for w in waits):
        return "a person's answer or OK is pending"
    return None


def _task_goal(conn: "sqlite3.Connection", bot: str, task_id: str) -> bool:
    """Worked as a goal (execution.task_goal): the harness would carry on with it on any turn."""
    from . import execution

    task = _h().task(conn, task_id)
    return bool(task and cast("Any", execution).current_task_goal(conn, bot, task))


def source(conn: "sqlite3.Connection", attempt_id: str) -> tuple[Row, Row, Row] | None:
    """The run, the message it started from, and the message it answered (execution.reply_source), or None."""
    from . import execution

    found = conn.execute(_RUN, (attempt_id,)).fetchone()
    run: Row | None = dict(found) if found else None
    msg: Row | None = _h().message(conn, run["message_id"], include_deleted=True) if run and run["message_id"] else None
    answered: Row | None = cast("Any", execution).reply_source(conn, msg) if msg and not msg["deleted_at"] else None
    if not run or not msg or not answered or answered.get("deleted_at"):
        return None
    return run, msg, answered


def why_not(conn: "sqlite3.Connection", attempt_id: str, at: str, own: str = "", lease: str = "") -> str | None:
    """Why the session this run ended is not compacted now, in a few words, or None when it is. `own` is the
    compaction's notice and `lease` its run, which are not newer work. Reads only."""
    from . import company_pause, task_wakes, threads

    H = _h()
    found = source(conn, attempt_id)
    if not found:
        return "no message to answer"
    a, msg, _ = found
    bot, key, conv_id = str(a["bot_id"]), a["computer_session_key"], str(msg["conversation_id"])
    task_id = str(a["task_id"] or "")
    checks: list[tuple[Callable[[], object], str]] = [
        (lambda: a["state"] != "completed" or a["purpose"] == PURPOSE, "not a finished turn"),
        (lambda: a["billing"] != "subscription" or a["handed_runtime"] != "claude", "not a Claude plan sign-in"),
        (lambda: (a["prompt_tokens"] or 0) < CONTEXT_MIN, "a small context"),
        (lambda: a["stop_requested_at"], "stopped"),
        (lambda: a["side"] or a["slot"] or threads.note_wake(msg), "a fallback's, side, card slot's or notes session"),
        (lambda: (H._json(a["wake"], {}) or {}).get("release"), "a routine's or dream's run"),
        (lambda: not (key and a["thread_id"] and a["runner_id"]), "no saved session"),
        (lambda: str(a["finished_at"] or "") <= str(H.shift(at, seconds=-LATEST)), "its cache is cold"),
        (lambda: conn.execute(_NEWER, (bot, a["created_at"], key, attempt_id, lease)).fetchone(), "a newer run"),
        (lambda: _moved(conn, a), "the session moved to another computer"),
        (lambda: _no_session(conn, bot), "no session of its own"),
        (lambda: task_id and _task_why(conn, task_id), ""),
        (lambda: conn.execute(_ASKED, (bot, task_id or "\x00", _room(conn, conv_id))).fetchone(), "a ❓ is open"),
        (
            lambda: conn.execute(_GOAL, (conv_id, bot)).fetchone() or task_id and _task_goal(conn, bot, task_id),
            "a goal",
        ),
        (lambda: conn.execute(_BUSY, _busy(bot, own, conv_id, task_id)).fetchone(), "newer work queued or going"),
        (lambda: conn.execute(_WATCHED, (attempt_id, attempt_id)).fetchone(), "background commands still watched"),
        (lambda: conn.execute(_DREAM, (bot, *_LIVE)).fetchone(), "a dream is queued"),
        (lambda: cast("Any", company_pause).record(conn), "the company is paused"),
        (lambda: task_wakes.cannot_run(conn, bot_actor(bot)), "the bot cannot run"),
    ]
    for check, why in checks:
        said = check()
        if said:
            return why or str(said)
    return None


def _moved(conn: "sqlite3.Connection", a: Row) -> bool:
    """Another computer holds the session now (session_computers), or its newest saved run is not this one."""
    from . import execution

    E: Any = execution
    home = E.session_computer(conn, a["bot_id"], a["computer_session_key"])
    latest = E.latest_session_run(conn, a["bot_id"], a["computer_session_key"])
    return bool(home and home["runner_id"] != a["runner_id"] or not latest or latest["thread_id"] != a["thread_id"])


def _no_session(conn: "sqlite3.Connection", bot: str) -> bool:
    """A bot that keeps one thread for everything (`session: bot`) or keeps nothing (stateless)."""
    from . import bot_settings
    from .shared_bots import follow

    found = conn.execute(cast("Any", bot_settings).ONE_SQL, (bot,)).fetchone()
    config: Any = _h()._json(found[0], {}) if found else {}
    config = cast("Any", follow)(conn, bot, config if isinstance(config, dict) else {})
    return config.get("session") == "bot" or bool(_h().stateless(config))


def _room(conn: "sqlite3.Connection", conv_id: str) -> str:
    conv: Row = _h().conversation(conn, conv_id) or {}
    return str(conv.get("root_id") or conv_id)


def _busy(bot: str, own: str, conv_id: str, task_id: str) -> tuple[str, ...]:
    return (bot, *_LIVE, own, conv_id, task_id or "\x00", f"{task_id or chr(0)}:", f"{task_id or chr(0)};")


def send(conn: "sqlite3.Connection", attempt_id: str) -> str | None:
    """Write the `/compact` notice for the session this run ended, in its conversation, and queue its run. Returns its
    id, or None when no run was queued. Written as a thread's carrier is (threads._carrier), from the keeper to the
    bot: no seq, restricted, in the session of the agent it is for in a task's own thread."""
    from . import enqueue, execution, threads

    H = _h()
    found = source(conn, attempt_id)
    if not found:
        return None
    a, msg, answered = found
    conv: Row = H.conversation(conn, msg["conversation_id"]) or {}
    bot = bot_actor(str(a["bot_id"]))
    task = cast("Any", execution).work_task_id(msg, conv)
    refs: Row = _RT.stamp(
        {
            "command": True,
            "wake": WAKE,
            "carries": answered["id"],
            "after": attempt_id,
            **({"task": task} if task else {}),
        },
        "other",
        purpose=PURPOSE,
    )
    mid, created, dumped = str(H.new_id()), str(H.now()), str(H._dump(refs))
    legacy = conn.execute("SELECT legacy_audience FROM messages WHERE id=?", (answered["id"],)).fetchone()
    own = conv.get("kind") == "task" and conv.get("task_id") and not conv.get("parent_id")
    conn.execute(
        "INSERT INTO messages(id,conversation_id,sender_id,recipient_id,kind,body,refs,in_reply_to,created_at,"
        "origin_id,revision,restricted,legacy_audience,session_agent_id) VALUES(?,?,?,?,'notice',?,?,NULL,?,?,0,1,?,?)",
        (
            mid,
            msg["conversation_id"],
            _core.TICO_ACTOR,
            bot,
            BODY,
            dumped,
            created,
            cast("Any", threads).mint_origin(conn),
            legacy[0] if legacy else None,
            bot if own else None,
        ),
    )
    message = {"id": mid, "sender_id": _core.TICO_ACTOR, "recipient_id": bot, "refs": dumped, "created_at": created}
    if not cast("Any", enqueue).enqueue(conn, message):
        conn.execute("DELETE FROM messages WHERE id=?", (mid,))  # a row no run reads is no use to anyone
        return None
    return mid


def may_start(conn: "sqlite3.Connection", message_id: str, refs: Row, attempt: str | None = None) -> bool:
    """Whether a compaction about to be claimed (`attempt` None) or started is still wanted: everything `send` asked
    still holds at this moment, and a started one runs in the very session that turn ended, on its computer."""
    source_id = str(refs.get("after") or "")
    why = why_not(conn, source_id, str(_h().now()), own=message_id, lease=attempt or "")
    if not why and attempt:
        ran = conn.execute(
            "SELECT 1 FROM attempts n JOIN attempts a ON a.id=? WHERE n.id=? AND n.runner_id=a.runner_id "
            "AND n.computer_session_key=a.computer_session_key",
            (source_id, attempt),
        ).fetchone()
        why = None if ran else "another session or computer"
    if why:
        LOG.info("idle compact: not run: %s (attempt %s, notice %s)", why, source_id, message_id)
    return not why


def sweep(conn: "sqlite3.Connection", at: str | None = None, *, every_minute: bool = True) -> int:
    """Send `/compact` to each big Claude session whose newest turn has been idle WARM_AFTER to WARM_BEFORE. With
    `every_minute`, at most once per clock minute (the tick runs more often; the window is ten minutes wide). Returns
    how many it sent."""
    H = _h()
    at = str(at or H.now())
    if every_minute:
        listed = conn.execute("PRAGMA database_list").fetchone()
        where, minute = str(listed[2] if listed else ""), at[:16]
        if _last_minute.get(where) == minute:
            return 0
        _last_minute[where] = minute
    window = (H.shift(at, seconds=-WARM_BEFORE), H.shift(at, seconds=-WARM_AFTER), PURPOSE, CONTEXT_MIN)
    sent = 0
    for (attempt_id,) in conn.execute(_DUE, window).fetchall():
        why = why_not(conn, str(attempt_id), at)
        if why:
            # Debug: a session that is not compacted comes round once a minute while it is in the window.
            LOG.debug("idle compact: skipped: %s (attempt %s)", why, attempt_id)
            continue
        if send(conn, str(attempt_id)):
            sent += 1
            LOG.info("idle compact: sent (attempt %s)", attempt_id)
    return sent
