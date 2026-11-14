# pyright: strict
"""Keep active tasks warm (token efficiency, item 9): "Keep warm for up to N hours" on a pipeline, and on a stage.

A bot's session on a task (its own provider thread, `task:<id>`, clients/sessions.py) is cached for an hour after its
last model request. A Builder whose task waits on a review for longer comes back cold and writes its whole context to
the cache again. While a task is in an active stage, Tico sends the session one short turn (PING) when it has been idle
for WARM_AFTER to WARM_BEFORE (backend/wake_presets.py, the warm window inside the hour): the turn reads the cache, which
starts its hour again. A 180k session costs about 18k input-equivalent an hour this way, against about 360k to write it
cold, so it pays when the session is used again within about 20 hours.

- The setting: `pipelines.keep_warm_hours` (default 8, 0 = off) and `stage_keep_warm.hours` for a stage of its own
  (backend/pipeline_settings.py). A stage that is not a work stage, or leaves by hand, is never active.
- It stops when the task leaves the active stages (closed, done, snoozed with no end, a stage with 0 hours), when N
  hours have passed since the bot's last real run on the task, when that run or the newest one ran on a fallback model
  (its session is not the one the next run resumes), or when the bot's next run on the task is already queued or
  running.
- Only for a bot that has the task's current stage or owns a stage still ahead of it (`_ahead`): the Builder waiting
  through a review, not a reviewer whose stage is behind the task.
- Never for a bot that keeps one thread for everything (`session: bot`) or keeps nothing (stateless), a task worked as a
  goal (the harness would go on with it), or a task whose conversation is not its own.
- Its notice is written as a thread's carrier is (no seq): no timeline, unread count or room history shows it. The
  runner sends it as its one line, with `--max-turns 1` on Claude Code, and keeps its records of the session as they
  were (runner-rs prompt::keep_warm), so the next real turn is sent what it would have been.
- A keep-warm turn is marked: trigger `keep_warm`, purpose `keep warm` (run_triggers.KEEP_WARM). It is not a run on its
  task for the never-stuck ladder or the run cap (task_wakes), is never counted as acting or as doing nothing
  (run_triggers.SETTLE_SQL), and its claim checks again that it is still wanted (`may_start`).

`sweep` runs from the release-policy tick (wake_presets.tick) at most once a minute, reads only attempts from the last
few hours, and writes one notice per session that is due. Every skip a session reaches the window with is logged as
"skipped: <why>", with ids only.
"""

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from . import hubdb_core, run_triggers, wake_presets

if TYPE_CHECKING:
    import sqlite3

Row = dict[str, Any]
LOG = logging.getLogger("tico.keep_warm")

# The one line a keep-warm turn says. It asks for nothing: no tools, a one-word answer.
PING = "Keep-warm turn: nothing has changed and there is nothing to do. Use no tools; reply with one word: ok."
WAKE = "keep_warm"  # the notice's refs.wake (task_wakes.may_start, runner-rs prompt::keep_warm)
# run_triggers and wake_presets are untyped: what this reads of them gets its type here.
_RT: Any = run_triggers
_WP: Any = wake_presets
KEEP_WARM: str = _RT.KEEP_WARM  # the run's purpose
WARM_AFTER: int = _WP.WARM_AFTER
WARM_BEFORE: int = _WP.WARM_BEFORE
# A run lasts at most an hour: a session whose last request was in the warm window had a run created within this.
LOOK_BACK_S = WARM_BEFORE + 2 * 3600
# What makes a run a fallback's: the harness that ran it instead (execution.complete `fallback`), or a side session.
_FALLBACK = "(json_extract(a.result,'$.fallback') IS NOT NULL OR coalesce(a.session_why,'')='fallback_side')"
_TOKENS = "coalesce(a.input_tokens,0)+coalesce(a.cached_tokens,0)+coalesce(a.cache_write_tokens,0)+coalesce(a.output_tokens,0)"
# Each bot's session on each task whose last model request ended in the window: (bot, task, when).
_DUE = (
    "SELECT a.bot_id, a.task_id, max(a.finished_at) AS last FROM attempts a "
    f"WHERE a.created_at >= ? AND a.task_id IS NOT NULL AND a.finished_at IS NOT NULL AND {_TOKENS} > 0 "
    "GROUP BY a.bot_id, a.task_id HAVING last > ? AND last <= ?"
)
_LIVE = ("queued", "leased", "running", "input", "stalled")

_core: Any = hubdb_core
bot_actor: Callable[[str], str] = _core.bot_actor
_last_minute: dict[str, str] = {}  # database file -> the clock minute its last sweep ran in


def _TH(module: object) -> Any:  # an untyped module, read as Any
    return module


def _h() -> Any:
    from . import hubdb

    return hubdb


def hours(conn: "sqlite3.Connection", row: Row) -> int:
    """The task's "Keep warm for up to N hours" where it is now: its stage's own, else its pipeline's. 0 when its stage
    is not active: not a work stage, left by hand, or the task is not open."""
    stage: Row = row.get("stage") or {}
    pipeline: Row = row.get("pipeline") or {}
    if row.get("status") != "open" or stage.get("kind") != "work" or stage.get("by_hand") or not row.get("visit"):
        return 0
    own = conn.execute("SELECT hours FROM stage_keep_warm WHERE stage_id=?", (stage.get("id"),)).fetchone()
    found = own[0] if own else pipeline.get("keep_warm_hours", 8)
    return int(found or 0)


def _snoozed_for_good(conn: "sqlite3.Connection", task_id: str) -> bool:
    """Set aside with no end ("on ice"): nobody is coming back to it soon."""
    return bool(
        conn.execute(
            "SELECT 1 FROM snoozes WHERE task_id=? AND ended_at IS NULL AND until_at IS NULL LIMIT 1", (task_id,)
        ).fetchone()
    )


def _ahead(conn: "sqlite3.Connection", row: Row, bot: str) -> bool:
    """Whether the bot has the task's current stage (its owner or the bot holding it) or owns a work stage still ahead
    in the pipeline: by name, as the task's assignee on an "assignee" stage, or on the list of a "One of several" stage.
    Only such a session is likely to be used again (the Builder through a review); a reviewer whose stage is behind
    the task is not."""
    from . import stage_pick, task_engine

    pick: Any = stage_pick
    actor = bot_actor(bot)
    visit: Row = row.get("visit") or {}
    if actor in (row.get("owner_id"), visit.get("holder_id")):
        return True
    stages = task_engine.stages(conn, str(row.get("pipeline_id")))
    current: Row = row.get("stage") or {}
    here = next((n for n, s in enumerate(stages) if s["id"] == current.get("id")), None)
    for stage in stages[here + 1 :] if here is not None else []:
        if stage.get("kind") != "work" or stage.get("retired_at"):
            continue
        owner = stage.get("owner")
        if owner == actor or (owner == "assignee" and row.get("assignee_id") == actor):
            return True
        listed = pick.config(stage)
        if listed and (actor in listed.bots or bot in listed.bots):
            return True
    return False


def _own_room(conn: "sqlite3.Connection", row: Row) -> bool:
    found = conn.execute("SELECT kind, task_id FROM conversations WHERE id=?", (row.get("conversation_id"),)).fetchone()
    return bool(found) and found[0] == "task" and found[1] == row["id"]


def _config(conn: "sqlite3.Connection", bot: str) -> Row:
    from . import bot_settings

    found = conn.execute(bot_settings.ONE_SQL, (bot,)).fetchone()
    config: Row = _h()._json(found[0], {}) if found else {}
    return config if isinstance(config, dict) else {}  # pyright: ignore[reportUnnecessaryIsInstance]


def _goal(conn: "sqlite3.Connection", row: Row, bot: str, config: Row) -> bool:
    """Worked as a goal (execution.task_goal): the harness would carry on with it on any turn."""
    if row.get("owner_id") != bot_actor(bot):
        return False
    H = _h()
    labels = set(H.task_labels(H.task(conn, row["id"])))
    return "goal:on" in labels or (config.get("task_goal") == "on" and "goal:off" not in labels)


def _busy(conn: "sqlite3.Connection", bot: str, row: Row) -> bool:
    """The bot has a run on the task queued or going: the next real one, or a keep-warm turn already sent."""
    marks = ",".join("?" * len(_LIVE))
    return bool(
        conn.execute(
            "SELECT 1 FROM jobs j JOIN messages m ON m.id=j.message_id "
            f"WHERE j.bot_id=? AND j.state IN ({marks}) AND (json_extract(m.refs,'$.task')=? "
            "OR m.conversation_id=? OR (m.wake_key >= ? AND m.wake_key < ?)) LIMIT 1",
            (bot, *_LIVE, row["id"], row.get("conversation_id") or "", f"{row['id']}:", f"{row['id']};"),
        ).fetchone()
    )


def why_not(conn: "sqlite3.Connection", bot: str, task_id: str, at: str) -> str | None:
    """Why this bot's session on this task is not kept warm now, in a few words, or None when it is. Reads the task,
    the bot and the bot's runs on the task; writes nothing."""
    from . import task_engine, task_wakes

    H = _h()
    row: Row | None = task_engine.task(conn, task_id)
    if not row:
        return "no such task"
    n = hours(conn, row)
    if n <= 0:
        return "not in an active stage" if row.get("status") == "open" else "task ended"
    if _snoozed_for_good(conn, task_id):
        return "snoozed with no end"
    if not _ahead(conn, row, bot):
        return "its stage is behind the task"
    if not _own_room(conn, row):
        return "not in its own conversation"
    config = _config(conn, bot)
    if config.get("session") == "bot" or H.stateless(config):
        return "no task session"
    if _goal(conn, row, bot, config):
        return "worked as a goal"
    real = conn.execute(
        f"SELECT a.finished_at, {_FALLBACK} FROM attempts a WHERE a.bot_id=? AND a.task_id=? "
        "AND a.started_at IS NOT NULL AND coalesce(a.purpose,'')<>? ORDER BY a.rowid DESC LIMIT 1",
        (bot, task_id, KEEP_WARM),
    ).fetchone()
    if not real or not real[0]:
        return "no real run on the task"
    if str(real[0]) < str(H.shift(at, hours=-n)):
        return f"{n} h since its last work"
    newest = conn.execute(
        f"SELECT {_FALLBACK} FROM attempts a WHERE a.bot_id=? AND a.task_id=? AND a.started_at IS NOT NULL "
        "ORDER BY a.rowid DESC LIMIT 1",
        (bot, task_id),
    ).fetchone()
    if real[1] or (newest and newest[0]):
        return "on a fallback model"
    if _busy(conn, bot, row):
        return "a run is queued or going"
    if task_wakes.cannot_run(conn, bot_actor(bot)):
        return "the bot cannot run"
    return None


def may_start(conn: "sqlite3.Connection", task: Row, bot: str, message_id: str) -> bool:
    """Whether a keep-warm turn about to be claimed is still wanted: the task is still in an active stage, not set
    aside for good, and no other run of the bot on it is queued (the real one warms the session itself). Its other
    reasons were checked when it was sent, a moment ago."""
    if hours(conn, task) <= 0 or _snoozed_for_good(conn, str(task["id"])):
        return False
    other = conn.execute(
        "SELECT 1 FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.bot_id=? AND j.state='queued' "
        "AND m.id<>? AND (json_extract(m.refs,'$.task')=? OR (m.wake_key >= ? AND m.wake_key < ?)) LIMIT 1",
        (bot, message_id, task["id"], f"{task['id']}:", f"{task['id']};"),
    ).fetchone()
    return not other


def send(conn: "sqlite3.Connection", bot: str, task_id: str) -> str | None:
    """Write the keep-warm notice for this bot's session on this task, in the task's conversation, and queue its run.
    Returns its id, or None when no run was queued. Like a thread's carrier (threads._carrier) it has no seq, so no
    timeline, unread count, live view or later turn's room history shows it, and nothing else is told: nobody reads
    it but the bot's claim. A carrier escapes the insert trigger's seq by `refs.carries`, which would make its run
    answer the carried message; this one takes back what the trigger stamped (the seq, and the room's counters)."""
    from . import enqueue, threads

    H = _h()
    row: Row | None = H.task(conn, task_id)
    if not row:
        return None
    refs: Row = _RT.stamp({"task": task_id, "wake": WAKE}, "keep_warm", purpose=KEEP_WARM)
    mid, created, dumped = str(H.new_id()), str(H.now()), str(H._dump(refs))
    counters = conn.execute(
        "SELECT last_seq, last_unread_seq FROM conversations WHERE id=?", (row["conversation_id"],)
    ).fetchone()
    conn.execute(
        "INSERT INTO messages(id,conversation_id,sender_id,recipient_id,kind,body,refs,in_reply_to,created_at,"
        "origin_id,revision,restricted) VALUES(?,?,?,?,'notice',?,?,NULL,?,?,0,1)",
        (mid, row["conversation_id"], H.KEEPER, bot_actor(bot), PING, dumped, created, _TH(threads).mint_origin(conn)),
    )
    conn.execute("UPDATE messages SET seq=NULL WHERE id=?", (mid,))
    if counters:
        conn.execute(
            "UPDATE conversations SET last_seq=?, last_unread_seq=? WHERE id=?",
            (counters[0], counters[1], row["conversation_id"]),
        )
    message = {"id": mid, "sender_id": H.KEEPER, "recipient_id": bot_actor(bot), "refs": dumped, "created_at": created}
    if not _TH(enqueue).enqueue(conn, message):
        conn.execute("DELETE FROM messages WHERE id=?", (mid,))  # a row no run reads is no use to anyone
        return None
    return mid


def sweep(conn: "sqlite3.Connection", at: str | None = None, *, every_minute: bool = True) -> int:
    """Send a keep-warm turn to each task session that is due: idle from WARM_AFTER to WARM_BEFORE, on a task in an
    active stage, within its hours. With `every_minute`, at most once per clock minute (the tick runs more often; the
    window is ten minutes wide). Returns how many it sent."""
    H = _h()
    at = str(at or H.now())
    if every_minute:
        listed = conn.execute("PRAGMA database_list").fetchone()
        where, minute = str(listed[2] if listed else ""), str(at)[:16]
        if _last_minute.get(where) == minute:
            return 0
        _last_minute[where] = minute
    due = conn.execute(
        _DUE,
        (
            H.shift(at, seconds=-LOOK_BACK_S),
            H.shift(at, seconds=-WARM_BEFORE),
            H.shift(at, seconds=-WARM_AFTER),
        ),
    ).fetchall()
    sent = 0
    for bot, task_id, _last in due:
        why = why_not(conn, str(bot), str(task_id), at)
        if why:
            # Debug: a session that is not kept comes round once a minute while it is in the window.
            LOG.debug("skipped: %s (keep warm, task %s, bot %s)", why, task_id, bot)
            continue
        if send(conn, str(bot), str(task_id)):
            sent += 1
            LOG.info("keep warm: sent (task %s, bot %s)", task_id, bot)
    return sent
