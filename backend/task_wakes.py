# pyright: strict
"""A bot's wake about a task on the engine (docs/pipelines-v3.md, "Effects travel with the move").

A wake is a row in `messages` with a `wake_key`: `<task id>:<visit id>:<level>:<try>`. The key is unique, so writing the
same wake twice is one wake. It is written in the same transaction as the move that causes it. The body says what is
missing and the five ways a run may end. A bot has at most one wake queued per task: a second one carries the first's
words before its own, and the first is canceled, so one run starts and nothing said is lost. The wake remembers what
was folded into it (`refs.parts`), so taking one of them back (`cancel`) takes out its words alone.
"""

import json
from collections.abc import Collection
from typing import TYPE_CHECKING, Any, cast

from . import run_triggers
from . import task_requirements as R

if TYPE_CHECKING:
    import sqlite3

Row = dict[str, Any]
# run_triggers is untyped: its functions are Any here.
RT: Any = run_triggers

ENDINGS = (
    "Leave the task in one of five states before you stop:\n"
    "1. Done with the stage: what is missing is met, or you moved the task.\n"
    "2. Asking a person, on the task.\n"
    "3. Waiting on a task you filed.\n"
    "4. Snoozed, with a reason, to a date where there is one.\n"
    "5. Closed, with a reason.\n"
    "Anything else counts as stopped early and you are woken again."
)


def _h() -> Any:
    from . import hubdb

    return hubdb


def key(task_id: str, visit_id: str, level: str = "owner", try_n: int = 0) -> str:
    return f"{task_id}:{visit_id}:{level}:{try_n}"


def body(conn: "sqlite3.Connection", task: Row, lead: str = "") -> str:
    """What a bot reads when it is woken for a task: the stage, what is missing, and how its run may end."""
    stage: Row = task.get("stage") or {}
    pipeline: Row = task.get("pipeline") or {}
    lines = [lead.strip()] if lead.strip() else []
    lines.append(f"{task.get('title') or 'A task'} is in {stage.get('name', '?')} ({pipeline.get('name', '?')}).")
    missing = [s for s in R.standings(conn, task) if not s.met]
    if missing:
        lines.append("Missing to leave this stage:")
        for s in missing:
            waits = f" Waiting on {s.on}." if s.state == R.WAITING and s.on else ""
            lines.append(f"- {s.name}: {s.line}.{waits}" if s.line and s.line != s.name else f"- {s.name}.{waits}")
    lines.append(f"Version {task.get('version')}. Send it with every command on this task.")
    lines.append(ENDINGS)
    return "\n".join(lines)


# What a wake's refs hold that is the engine's or the run's own. A caller's refs never set one, and a folded wake never
# carries one into the wake it folds into.
ENGINE_REFS = (
    "task",
    "wake",
    "visit",
    "level",
    "try",
    "version",
    "quiet",
    "job",
    "trigger",
    "trigger_source",
    "source",
    "purpose",
    "parts",
)


def add(
    conn: "sqlite3.Connection",
    task: Row,
    target: str | None,
    visit: Row,
    text: str,
    *,
    level: str = "owner",
    try_n: int = 0,
    wake_key: str | None = None,
    quiet: bool = False,
    refs: Row | None = None,
    trigger: str | None = None,
    source: str | None = None,
) -> str | None:
    """Wake `target` about `task`, once per key. Returns the message id, or None when this wake was already written or
    there is nobody to tell. A wake still queued for `target` about this task is folded into this one: its words come
    first, what its caller attached comes along, and it is canceled once this one has queued its run. A quiet note
    starts no run and folds nothing. `refs` are a caller's own facts for the run it starts (an automation's run and
    step, the other work to reconcile with): any of them that is the engine's (`ENGINE_REFS`) is dropped, so `quiet`
    and `job` come only from the engine's own state. `trigger` and `source` say what the run is for, where the caller
    knows better than "work a task"."""
    H = _h()
    if not target:
        return None
    wake_key = wake_key or key(task["id"], visit["id"], level, try_n)
    if conn.execute("SELECT 1 FROM messages WHERE wake_key=?", (wake_key,)).fetchone():
        return None
    if not H._actor_exists(conn, target):
        return None
    if not H.task_private_readable(conn, target, task):
        return None  # the engine never adds a reader: who can see a private task does not change
    waiting: list[Any] = []
    carried: Row = {}
    parts: list[Row] = []
    if not quiet and H.is_bot(target):
        waiting = conn.execute(
            "SELECT j.id, m.body, m.refs, m.wake_key FROM messages m CROSS JOIN jobs j ON j.message_id = m.id "
            f"WHERE {_KEY} AND j.bot_id=? AND j.state='queued' ORDER BY m.created_at, m.rowid",
            (*_range(task["id"]), H.actor_id(target)),
        ).fetchall()
        for found in waiting:
            # What the folded wake's caller attached (a new task's automation run, the work to reconcile with) rides
            # on: the run that starts is still that work. The engine's own facts are this wake's.
            was: Row = H._json(found[2], {}) or {}
            trigger = trigger or (was.get("trigger") if was.get("trigger") != "task_turn" else None)
            source = source or was.get("trigger_source")
            carried.update({name: value for name, value in was.items() if name not in ENGINE_REFS})
            parts += _parts(was, found[3], found[1])
        if parts:
            parts.append({"key": wake_key, "text": text})
            text = _joined(parts)
    own: Row = {"task": task["id"], "wake": "task_turn", "visit": visit["id"], "level": level, "try": try_n}
    own["version"] = task.get("version")
    if quiet:
        own["quiet"] = True
    if parts:
        own["parts"] = parts  # what was folded into it, each with its key and its words (`cancel`)
    theirs: Row = {name: value for name, value in (refs or {}).items() if name not in ENGINE_REFS}
    if not theirs.get("wake_batch"):
        carried.pop("wake_batch", None)  # a wake that is not batchable is not held for what it folded (wake_presets)
    stamped: Row = RT.stamp({**carried, **theirs, **own}, trigger or "task_turn", source)
    message = H._write_message(
        conn,
        H.KEEPER,
        target,
        text,
        H.conversation(conn, task["conversation_id"]),
        "notice",
        stamped,
        None,
        None,
    )
    if not message:
        return None
    # `job` marks a wake that queued a run, so a wake whose job later went missing reads as lost (`lost`); one to a bot
    # that reads its own inbox never had a job.
    job = conn.execute("SELECT 1 FROM jobs WHERE message_id=?", (message["id"],)).fetchone() is not None
    if job:
        conn.execute(
            "UPDATE messages SET wake_key=?, refs=json_set(COALESCE(refs, '{}'), '$.job', json('true')) WHERE id=?",
            (wake_key, message["id"]),
        )
        for found in waiting:  # folded into this one
            conn.execute("UPDATE jobs SET state='canceled' WHERE id=? AND state='queued'", (found[0],))
    else:
        # It queued no run (a rule held it): the wakes it would have folded stay queued, so the bot is still woken.
        conn.execute("UPDATE messages SET wake_key=? WHERE id=?", (wake_key, message["id"]))
    return message["id"]


def ask(conn: "sqlite3.Connection", task: Row, requirement: Row | None, who: str | None = None) -> str | None:
    """Wake the bot an ask on this task names (an OK or an answer), once per ask and visit, keyed
    `<task>:<visit>:ask:<requirement>`: an ask added to the visit, or one the task's stage has (it holds for every
    visit of the stage). Only while that bot has a turn on the task (`waiting_on.turns`), so never while a person has
    it or it is snoozed. `who` is the bot the task waits on for it, when the requirement names nobody itself. A task
    entering a stage wakes each bot a stage's ask names beside the bot with the turn (task_engine._wake_turn); the
    never-stuck check calls this again for a bot that has a turn and was never woken for its ask: the ask it got while
    a person had the task reaches it once a person acts."""
    from . import waiting_on as W

    H = _h()
    visit: Row = task.get("visit") or {}
    if not requirement or requirement.get("withdrawn_at"):
        return None
    staged = requirement.get("stage_id")
    if (staged or requirement.get("visit_id")) != (task["stage"]["id"] if staged else visit.get("id")):
        return None
    bot = requirement.get("person_id") or who
    if not H.is_bot(bot) or task.get("status") != "open" or task.get("snooze") or bot not in W.turns(conn, task):
        return None
    line = requirement.get("line") or ""
    lead = (
        f"You are asked: {line}" if staged else f"{H.actor_name(conn, requirement.get('added_by_id'))} asks you: {line}"
    )
    return add(
        conn, task, bot, visit, body(conn, task, lead), wake_key=f"{task['id']}:{visit['id']}:ask:{requirement['id']}"
    )


# Why a bot cannot run, as the rest of a sentence about it: "Builder has no computer". The never-stuck check reads this
# before it wakes anyone, and `waiting_on` shows the task as Paused with the cause. Every cause is a read of saved state:
# nothing here asks a computer. The three in COMPANY_WIDE stop every bot they touch and are whole sentences.
STATE_CAUSES = {"paused": "is paused", "quarantined": "is quarantined", "planned": "has not started yet"}
REMOVED = "was removed"
NO_COMPUTER = "has no computer"
RUNS_FAILING = "keeps failing its runs"
LOGIN = "has a login to reconnect"
COMPANY_PAUSED = "The company is paused"
CLOUD_LOCKED = "Tico Cloud is locked"
OUT_OF_CREDIT = "Tico Cloud is out of credit"
COMPANY_WIDE = (COMPANY_PAUSED, CLOUD_LOCKED, OUT_OF_CREDIT)


def cause_line(conn: "sqlite3.Connection", actor: str | None, cause: str) -> str:
    """A cause from `cannot_run` as one sentence: "Builder has no computer", "Tico Cloud is locked"."""
    if cause in COMPANY_WIDE:
        return cause
    return f"{_h().actor_name(conn, actor)} {cause}"


def cannot_run(conn: "sqlite3.Connection", actor: str | None, seen: dict[str, str | None] | None = None) -> str | None:
    """Why this bot cannot run now, in a few words, or None when it can. A paused bot's tasks say Paused and carry on
    by themselves when it can run again; nobody is woken meanwhile. `seen` keeps each bot's answer for one pass."""
    H = _h()
    if not H.is_bot(actor):
        return None
    bot: str = H.actor_id(actor)
    if seen is not None and bot in seen:
        return seen[bot]
    cause = _cause(conn, bot)
    if seen is not None:
        seen[bot] = cause
    return cause


def _cause(conn: "sqlite3.Connection", bot: str) -> str | None:
    from . import bot_settings, cloud_access, company_pause, gateway_connection, harnesses, reconnects, run_backoff

    row = conn.execute("SELECT state FROM bots WHERE id=?", (bot,)).fetchone()
    if not row:
        return REMOVED
    state = row[0] or "active"
    if state != "active":
        return STATE_CAUSES.get(state, f"is {state}")
    if company_pause.record(conn) is not None:
        return COMPANY_PAUSED
    # These modules are untyped: what they return is Any here.
    cloud: Any = cloud_access
    config: Row = cast("Any", bot_settings).read(conn, bot) or {}
    access = cloud.access(conn)
    if cloud.locked(conn, access):
        return CLOUD_LOCKED
    if cloud.frozen(conn, access) and cast("Any", gateway_connection).is_tico(config.get("model")):
        return OUT_OF_CREDIT  # a bot on its own subscription or keys still runs
    kinds: Any = harnesses
    if kinds.resolve_harness(config) not in kinds.EXTERNAL_HARNESSES:
        # A computer that is offline is not a cause: a cloud computer sleeps until work is queued for it.
        placed = conn.execute(
            "SELECT r.revoked_at FROM assignments a JOIN runners r ON r.id = a.runner_id WHERE a.bot=?", (bot,)
        ).fetchone()
        if not placed or placed[0]:
            return NO_COMPUTER
    held: Row = cast("Any", run_backoff).get(conn, bot) or {}
    if held.get("held_at"):
        return RUNS_FAILING  # held until a person releases it (backend/run_backoff.py)
    if cast("Any", reconnects).bot_open(conn, bot, "login:"):
        return LOGIN  # its computer's sign-in was refused: a person reconnects it (backend/reconnects.py)
    return None


def live_bots(conn: "sqlite3.Connection", now: str | None = None) -> set[str]:
    """The bots (slugs) with a live run on any task: one read, on the index of active runs (one_active_attempt_per_lane).
    The never-stuck pass reads it once and hands it to `live_run`, which then reads nothing for any other bot. A pass
    starts no run, so the set holds for the whole pass; a run that ends during it only makes `live_run` read again."""
    H = _h()
    return {
        str(found[0])
        for found in conn.execute(
            "SELECT DISTINCT j.bot_id FROM attempts a JOIN jobs j ON j.attempt_id = a.id "
            "WHERE a.state IN ('leased','running') AND a.lease_until > ?",
            (now or H.now(),),
        ).fetchall()
    }


def live_run(
    conn: "sqlite3.Connection",
    task: Row,
    actor: str | None,
    now: str | None = None,
    live: Collection[str] | None = None,
) -> bool:
    """This bot has a run on this task whose computer still checks in. A run is live only while its lease is renewed
    (every 90 seconds at most): one that is held, cut off or lost is over. `live` is `live_bots` read at the same
    `now`: a bot not in it has no live run on any task. A run is on the task as `_ON_TASK` says: a run in the task's
    conversation counts only when it names no task and no other open task has that conversation, so a run on another
    task there never hides this one's lost wake."""
    H = _h()
    if not H.is_bot(actor):
        return False
    if live is not None and H.actor_id(actor) not in live:
        return False
    on_task = _on_task(conn, task)
    return bool(
        conn.execute(
            "SELECT 1 FROM (SELECT m.id FROM messages m WHERE "
            f"{_KEY} UNION SELECT id FROM messages WHERE conversation_id = ?) w "
            "CROSS JOIN jobs j ON j.message_id = w.id JOIN attempts a ON a.id = j.attempt_id "
            "JOIN messages m ON m.id = w.id "
            f"WHERE j.bot_id=? AND a.state IN ('leased', 'running') AND a.lease_until > ? AND {_ON_TASK} LIMIT 1",
            (
                *_range(task["id"]),
                on_task[-1] if on_task[-2] else "",  # the conversation's messages, only when it is this task's alone
                H.actor_id(actor),
                now or H.now(),
                *on_task,
            ),
        ).fetchone()
    )


# The runs (attempts `a`) and the message each ran on (`m`), and which of them are on one task: the attempt or its
# message names the task (the attempt's task, the wake's key, the message's task), or, when neither names any task, the
# message is in the task's conversation and no other open task has that conversation. Bound by `_on_task`.
_RUNS = (
    "FROM attempts a LEFT JOIN jobs j ON j.id = a.job_id LEFT JOIN messages m ON m.id = COALESCE(a.message_id, "
    "j.message_id) "
)
_ON_TASK = (
    "(a.task_id = ? OR (m.wake_key >= ? AND m.wake_key < ?) OR json_extract(m.refs, '$.task') = ? OR (? AND "
    "a.task_id IS NULL AND m.wake_key IS NULL AND json_extract(m.refs, '$.task') IS NULL AND m.conversation_id = ?))"
)


def _on_task(conn: "sqlite3.Connection", task: Row) -> tuple[object, ...]:
    """The values `_ON_TASK` takes for this task."""
    conversation = task.get("conversation_id") or ""
    sharing = conn.execute(
        "SELECT COUNT(*) FROM tasks WHERE conversation_id=? AND status='open' AND id<>?", (conversation, task["id"])
    ).fetchone()
    alone = bool(conversation) and not (sharing and sharing[0])
    return (task["id"], *_range(task["id"]), task["id"], 1 if alone else 0, conversation)


def last_run(conn: "sqlite3.Connection", task: Row, actor: str | None) -> Row | None:
    """This bot's newest run on this task that started: its `state` and when it ended (`finished_at`). None when it
    never ran for it. A lease that never started is no run."""
    H = _h()
    if not H.is_bot(actor):
        return None
    found = conn.execute(
        f"SELECT a.state, a.finished_at {_RUNS}WHERE a.bot_id=? AND a.started_at IS NOT NULL AND {_ON_TASK} "
        "ORDER BY a.rowid DESC LIMIT 1",
        (H.actor_id(actor), *_on_task(conn, task)),
    ).fetchone()
    return {"state": found[0], "finished_at": found[1]} if found else None


def _marks(task: Row) -> tuple[int, int]:
    """Where the task's visit last changed, by order: the newest attempts row and the newest messages row there were
    then (`visits.last_change_attempt`, `last_change_message`, written by triggers whenever `last_change_at` is). A run
    or a wake with a larger rowid came after the change, whatever the clock said when it was written."""
    visit: Row = task.get("visit") or {}
    return int(visit.get("last_change_attempt") or 0), int(visit.get("last_change_message") or 0)


def runs_since(conn: "sqlite3.Connection", task: Row, *, starting: bool = False, besides: str | None = None) -> int:
    """How many bot runs started on this task since its visit last changed, whoever ran: the count the never-stuck
    ladder holds to 7. Only a run that started counts (`started_at`): a lease that failed before it began ran nothing.
    With `starting`, a lease still live that has not started yet counts too: it is about to. "Since" is by order, not
    by the clock: a run whose attempt row came after the change. A run belongs to the change it was claimed after.
    `besides` leaves one run out: the one asking whether it may start."""
    started, args = "a.started_at IS NOT NULL", list[object]([_marks(task)[0]])
    if starting:
        started = "(a.started_at IS NOT NULL OR (a.state = 'leased' AND a.lease_until > ?))"
        args.append(_h().now())
    if besides:
        started += " AND a.id <> ?"
        args.append(besides)
    found = conn.execute(
        f"SELECT COUNT(*) {_RUNS}WHERE a.rowid > ? AND {started} AND {_ON_TASK}", (*args, *_on_task(conn, task))
    ).fetchone()
    return int(found[0]) if found else 0


# Bot runs one visit may start between two changes: the owner's start and 3 tries, then the manager bot's 3 tries
# (R1). Every run on the task counts, whoever it was for and whatever woke it.
RUNS = 7


def may_start(conn: "sqlite3.Connection", message: str | Row, attempt: str | None = None) -> bool:
    """Whether a queued run about a task may start now. The claim of a job calls it in its write transaction before it
    leases the run, and the start of that run calls it again (`attempt`: that run's own lease is not counted as one
    about to start). `message` is the job's message, by id (a job's id is its message's) or as a row.

    Every automatic start tied to a task on the engine is held: the engine's wakes, and any other message a person did
    not write (a scheduled occurrence's notice, a CI nudge, a task's news), retries included. Such a run may start only
    while the task is open, not snoozed and not at level person, while its bot has a turn on it (`waiting_on.turns`;
    Late's one look for the manager bot needs none), and while the visit has started fewer than 7 runs since it last
    changed, the leases about to start included. An engine wake must also be about the task's open visit. When the
    run is held its queued job is canceled, here. True for a person's own message (their words always get an answer),
    a quiet note, and anything about no task on the engine."""
    from . import task_engine as E

    H = _h()
    message_id = message if isinstance(message, str) else str(message.get("message_id") or message.get("id") or "")
    found = conn.execute(
        "SELECT m.wake_key, m.recipient_id, m.refs, m.sender_id, c.task_id FROM messages m "
        "LEFT JOIN conversations c ON c.id = m.conversation_id WHERE m.id=?",
        (message_id,),
    ).fetchone()
    if not found:
        return True
    refs: Row = H._json(found[2], {}) or {}
    if refs.get("quiet") or H.person_sent({"sender_id": found[3], "refs": refs}):
        return True
    parts = str(found[0] or "").split(":")
    task = E.task(conn, parts[0]) if found[0] else None
    if task is None:
        named = H.message_task_id({"refs": refs}, {"task_id": found[4]})
        task = E.task(conn, str(named)) if named else None
    if task is None:
        return True  # about no task on the engine: not the engine's to hold
    visit: Row = task.get("visit") or {}
    if refs.get("wake") == "task_turn" and refs.get("visit") != visit.get("id"):
        allowed = False
    else:
        kind = parts[2] if found[0] and task["id"] == parts[0] and len(parts) > 2 else ""
        allowed = _may_start(conn, task, str(found[1] or ""), kind, attempt)
    if allowed:
        return True
    conn.execute("UPDATE jobs SET state='canceled' WHERE message_id=? AND state='queued'", (message_id,))
    return False


def _may_start(conn: "sqlite3.Connection", task: Row, bot: str, kind: str, attempt: str | None) -> bool:
    from . import waiting_on as W

    if task["status"] != "open" or task.get("snooze") or not task.get("visit"):
        return False
    if (task["visit"].get("level") or "owner") == "person":
        return False
    pipe: Row = task.get("pipeline") or {}
    if not (kind == "late" and bot == pipe.get("manager_id")) and bot not in W.turns(conn, task):
        return False
    return runs_since(conn, task, starting=True, besides=attempt) < RUNS


# A job in one of these states may still start its run: its wake is not lost.
LIVE_JOBS = ("queued", "leased", "running", "input", "stalled")


def lost(conn: "sqlite3.Connection", task: Row, actor: str | None, *, whole_visit: bool = False) -> Row | None:
    """The wake about this task that `actor` was owed and never got: its newest one in this visit since the visit last
    changed (by order; with `whole_visit`, in the whole visit), when it queued a run and that run never started (its
    job is gone, or ended with no run of it started), and no run of this bot on the task started on a wake written
    after it. `id`, `body`, and `refs`: what woke it, which a wake written again keeps. None when nothing was lost."""
    H = _h()
    if not H.is_bot(actor):
        return None
    visit: Row = task.get("visit") or {}
    found = conn.execute(
        "SELECT m.id, m.body, m.rowid, json_extract(m.refs, '$.job'), "
        "(SELECT j.state FROM jobs j WHERE j.id = m.id), m.refs FROM messages m "
        f"WHERE {_KEY} AND m.rowid > ? AND +m.recipient_id=? "
        "AND COALESCE(json_extract(m.refs, '$.quiet'), 0) = 0 ORDER BY m.rowid DESC LIMIT 1",
        (*_range(f"{task['id']}:{visit.get('id')}"), 0 if whole_visit else _marks(task)[1], actor),
    ).fetchone()
    if not found or not found[3] or found[4] in LIVE_JOBS:
        return None
    taken = conn.execute(
        f"SELECT 1 {_RUNS}WHERE a.bot_id=? AND a.started_at IS NOT NULL AND (a.job_id=? OR ({_ON_TASK} "
        "AND m.rowid > ?)) LIMIT 1",
        (H.actor_id(actor), found[0], *_on_task(conn, task), found[2]),
    ).fetchone()
    if taken:
        return None
    return {"id": str(found[0]), "body": str(found[1] or ""), "refs": H._json(found[5], {}) or {}}


def inbox(conn: "sqlite3.Connection", task: Row, actor: str | None) -> Row | None:
    """The newest wake about this task in this visit since it last changed (by order), when `actor` is a bot that reads
    its own inbox (an outside agent: its wakes queue no job). `id`, and `read_at`: None while it has not read it. An
    intact wake it has not read is on its way, as a queued one is; once read, the bot took it. None for a bot whose
    wakes queue a run, or when nothing was written for it since the change."""
    from . import enqueue

    H = _h()
    if not H.is_bot(actor):
        return None
    visit: Row = task.get("visit") or {}
    reads_inbox: str = cast("Any", enqueue).external_bot_sql("m.recipient_id")
    found = conn.execute(
        f"SELECT m.id, m.read_at FROM messages m WHERE {_KEY} AND m.rowid > ? "
        "AND +m.recipient_id=? AND COALESCE(json_extract(m.refs, '$.quiet'), 0) = 0 "
        f"AND json_extract(m.refs, '$.job') IS NULL AND {reads_inbox} ORDER BY m.rowid DESC LIMIT 1",
        (*_range(f"{task['id']}:{visit.get('id')}"), _marks(task)[1], actor),
    ).fetchone()
    return {"id": str(found[0]), "read_at": found[1]} if found else None


def told_in_visit(conn: "sqlite3.Connection", task: Row, actor: str | None) -> str | None:
    """Whether this bot was told about the task's open visit: "told" when it got a wake that queued a run or sits in
    its own inbox, "lost" when its newest such wake never ran (the never-stuck check writes it again, once per level,
    try and change), None when it got none at all."""
    H = _h()
    if not H.is_bot(actor):
        return None
    visit: Row = task.get("visit") or {}
    found = conn.execute(
        f"SELECT 1 FROM messages m WHERE {_KEY} AND +m.recipient_id=? "
        "AND COALESCE(json_extract(m.refs, '$.quiet'), 0) = 0 LIMIT 1",
        (*_range(f"{task['id']}:{visit.get('id')}"), actor),
    ).fetchone()
    if not found:
        return None
    return "lost" if lost(conn, task, actor, whole_visit=True) else "told"


def queued(conn: "sqlite3.Connection", task_id: str, actor: str | None) -> bool:
    """A wake for this task is waiting for this bot: queued or just claimed, held ones included."""
    H = _h()
    if not H.is_bot(actor):
        return False
    return bool(
        conn.execute(
            "SELECT 1 FROM messages m CROSS JOIN jobs j ON j.message_id = m.id "
            f"WHERE {_KEY} AND j.bot_id=? AND j.state IN ('queued', 'leased') LIMIT 1",
            (*_range(task_id), H.actor_id(actor)),
        ).fetchone()
    )


def drop(conn: "sqlite3.Connection", task_id: str, keep: str | Collection[str] | None = None) -> int:
    """Cancel every wake still queued for this task, whoever it is for: the turn they were about is over. With `keep`
    (a bot, or several), those bots' wakes stay queued. A run that already took its wake is left alone. Returns how
    many."""
    H = _h()
    keeping = [keep] if isinstance(keep, str) else list(keep or ())
    kept = sorted({str(H.actor_id(bot)) for bot in keeping if H.is_bot(bot)})
    cursor = conn.execute(
        "UPDATE jobs SET state='canceled' WHERE state='queued' AND message_id IN "
        f"(SELECT id FROM messages m WHERE {_KEY})"
        + (f" AND bot_id NOT IN ({','.join('?' * len(kept))})" if kept else ""),
        (*_range(task_id), *kept),
    )
    return cursor.rowcount


def _parts(refs: Row, wake_key: object, body: object) -> list[Row]:
    """What one queued wake is made of: the wakes folded into it, or itself alone."""
    folded: list[Row] | None = refs.get("parts")
    return list(folded) if folded else [{"key": str(wake_key or ""), "text": str(body or "")}]


def _joined(parts: list[Row]) -> str:
    return "\n\n".join(str(part.get("text") or "") for part in parts)


def cancel(conn: "sqlite3.Connection", wake_key: str) -> int:
    """Take one wake back by its key, while no run has taken it yet. A wake that was folded into another is taken out
    of it alone: the wake that holds it keeps the rest of its words, and is canceled only when nothing is left. Returns
    how many jobs it canceled."""
    H = _h()
    rows = conn.execute(
        "SELECT j.id, m.id, m.wake_key, m.refs, m.body FROM messages m CROSS JOIN jobs j ON j.message_id = m.id "
        f"WHERE {_KEY} AND j.state='queued'",
        _range(wake_key.split(":", 1)[0]),
    ).fetchall()
    for job_id, message_id, own_key, raw, body in rows:
        refs: Row = H._json(raw, {}) or {}
        parts = _parts(refs, own_key, body)
        rest = [part for part in parts if part.get("key") != wake_key]
        if len(rest) == len(parts):
            continue
        if not rest:
            return conn.execute("UPDATE jobs SET state='canceled' WHERE id=? AND state='queued'", (job_id,)).rowcount
        refs["parts"] = rest
        conn.execute("UPDATE messages SET body=?, refs=? WHERE id=?", (_joined(rest), json.dumps(refs), message_id))
        return 0
    return 0


# A wake key's prefix read as a range on the wake-key index (`messages_wake_key`): every key that starts `<prefix>:`
# sorts at or after it and before `<prefix>;`. A LIKE cannot use the index, so each read scanned every job of the bot.
_KEY = "m.wake_key >= ? AND m.wake_key < ?"


def _range(prefix: str) -> tuple[str, str]:
    return f"{prefix}:", f"{prefix};"
