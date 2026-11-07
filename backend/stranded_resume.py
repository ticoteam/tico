# pyright: strict
"""Restart stranded bot work: a bot's task left in progress with nothing to move it gets one resume wake.

A bot works a task in `doing`. When a run ends with the task still `doing`, and the bot neither finished it, handed it
on nor recorded what it waits on, nothing ran it again: the free-turn start (backend/auto_tasks.py) starts only `open`
work, and a bot whose profile does not pull its own work (a reviewer) never starts it at all.

Every SWEEP_MINUTES the scheduler calls `sweep`. A task is stranded when:
- it is `doing`, owned by an active bot that is not paused, held or draining, whose computer was seen in the last few
  minutes (check_pools.online), and which has no resume of its own queued or going;
- the bot reads it (H.bot_reads_task), and it is not a roadmap item;
- no job for it is queued, held, leased, running, waiting for input or uncertain, and no run is live on it;
- nothing is recorded to wait on: no hold (a move to Blocked, a snooze, a task quarantine), no follow-up time, deferral
  or stored resume to come, no blocker, open subtask, approval, unanswered ask, worktree, milestone or pull request
  wait (auto_tasks.waiting, H.recorded_wait). A due date ahead is not a wait for work already in progress;
- nothing changed on it for QUIET_MINUTES after its last run ended.

Such a task gets one wake for its owner on the task (`refs.wake` "resume", `refs.stranded` true) through the ordinary
wake path, so the claim's fences (turn limits, budgets, back-off, lanes, the build limit) all still apply. As an awaited
result it wakes a bot whatever its profile says about other news, a reviewer included. Each wake is a `resumed` row in
the task's history. One sweep wakes at most SWEEP_CAP tasks, oldest first, at most one per bot and PER_COMPUTER per
computer, so a backlog drains over hours without crowding one computer.

Progress on a task is a status, step or owner change by anyone; a note, comment or other change its owner bot made; a
message the bot wrote in its room (other than its reply to a resume); or a subtask filed under it. After
NO_PROGRESS_LIMIT resumes in a row with no progress the task is not resumed again until something moves it: a
`resume_stopped` row ("No progress after 3 runs") names the bot that hears of it, and the task roundup
(backend/task_roundup.py) lists it once on that bot's list: the owner's manager on the org chart when that is an active
bot that reads the task, else the owner itself. A person is never told. A task resumed DAY_LIMIT times in 24 hours
waits for the next day, whatever it did.
"""

import json
import sqlite3
from typing import Any, cast

from . import bot_settings as BS
from . import hubdb

# hubdb is untyped: each of its functions is partially unknown, and a cast cannot clear that on the attribute.
H: Any = hubdb

SWEEP_MINUTES = 10
QUIET_MINUTES = 15
SWEEP_CAP = 25
PER_COMPUTER = 5
NO_PROGRESS_LIMIT = 3
DAY_LIMIT = 6
RESUMED = "resumed"  # task history kind: one resume wake
STOPPED = "resume_stopped"  # task history kind: resumes stopped; `new` names the bot told
STOP_NOTE = f"No progress after {NO_PROGRESS_LIMIT} runs"
STOP_LOOKBACK_DAYS = 30  # a stop older than this is history, not a roundup line
LIVE_JOBS = ("queued", "leased", "running", "input", "uncertain")
# What a run writes on its own task that is bookkeeping, not progress.
BOOKKEEPING = ("lint", "number", "reader", "private", "check_model", "rank", "session", "run_model", RESUMED, STOPPED)
_MARKS = ",".join("?" * len(LIVE_JOBS))
_BOOKKEEPING = ",".join("?" * len(BOOKKEEPING))


def sweep(store: Any, at: str | None = None) -> dict[str, int]:
    """One pass: chosen in a read, each task then woken or stopped in its own short write, checked again there.
    Returns {"resumed": n, "stopped": n}."""
    from . import batch_work as _batch_work

    batch_work: Any = _batch_work

    at = cast(str, at or H.now())
    with store.read_transaction() as c:
        resume, stop = plan(c, at)
    done: dict[str, int] = {"resumed": 0, "stopped": 0}
    for task_id in stop + resume:
        with store.transaction() as c:
            with batch_work.isolated(c, "stranded_resume", task_id):
                what = act(c, task_id, at)
                if what:
                    done[what] += 1
    return done


def run(c: sqlite3.Connection, at: str | None = None) -> dict[str, int]:
    """`sweep` in the caller's transaction (tests)."""
    at = cast(str, at or H.now())
    resume, stop = plan(c, at)
    done: dict[str, int] = {"resumed": 0, "stopped": 0}
    for task_id in stop + resume:
        what = act(c, task_id, at)
        if what:
            done[what] += 1
    return done


def plan(c: sqlite3.Connection, at: str) -> tuple[list[str], list[str]]:
    """([task ids to resume], [task ids to stop resuming]), oldest first, within the sweep's limits. Reads only."""
    rows = cast(
        list[dict[str, Any]],
        H._rows(
            c.execute(
                f"SELECT tasks.* FROM {H.TASKS} tasks JOIN bots b ON tasks.owner_id='bot:'||b.id "
                "WHERE tasks.status='doing' AND b.state='active' AND tasks.updated_at<=? "
                "ORDER BY tasks.updated_at, tasks.id",
                (cast(str, H.shift(at, minutes=-QUIET_MINUTES)),),
            )
        ),
    )
    ready: dict[str, bool] = {}
    taken: set[str] = set()
    per_computer: dict[str | None, int] = {}
    resume: list[str] = []
    stop: list[str] = []
    for row in rows:
        if len(resume) >= SWEEP_CAP:
            break
        slug = cast(str, H.actor_id(row["owner_id"]))
        if slug in taken:
            continue
        if slug not in ready:
            ready[slug] = _bot_ready(c, slug)
        if not ready[slug]:
            continue
        found = state(c, row, at)
        if found is None:
            continue
        if found["stop"]:
            stop.append(row["id"])
            continue
        computer = _computer(c, slug)
        if per_computer.get(computer, 0) >= PER_COMPUTER:
            continue
        per_computer[computer] = per_computer.get(computer, 0) + 1
        taken.add(slug)
        resume.append(row["id"])
    return resume, stop


def act(c: sqlite3.Connection, task_id: str, at: str) -> str | None:
    """Resume or stop resuming one task, if it still calls for it: "resumed", "stopped" or None."""
    row = cast(dict[str, Any] | None, H.task(c, task_id))
    if row is None:
        return None
    found = state(c, row, at)
    if found is None:
        return None
    if found["stop"]:
        told = hears(c, row)
        H._task_event(c, task_id, H.KEEPER, STOPPED, None, told, STOP_NOTE)
        _nudge(c, row, told)
        return "stopped"
    if not _bot_ready(c, cast(str, H.actor_id(row["owner_id"]))):
        return None
    n = found["since"] + 1
    body = (
        f"Resume: {row['title']}\nThis task is still in progress, but no run is going on it and nothing is recorded "
        "to wait on. Carry on with it: finish it, hand it on, or record what it waits on (a subtask, a blocker, a "
        f"question, or hub task later). Automatic resume {n} of {NO_PROGRESS_LIMIT}: after {NO_PROGRESS_LIMIT} with "
        "no progress, Tico stops and lists it for your manager."
    )
    try:
        sent = cast(
            bool, H._wake(c, row, row["owner_id"], body, {"wake": "resume", "stranded": True}, trigger="stall_check")
        )
    except H.Refused:
        return None
    if not sent:
        return None
    H._task_event(c, task_id, H.KEEPER, RESUMED, None, str(n), f"Automatic resume {n} of {NO_PROGRESS_LIMIT}")
    return "resumed"


def _nudge(c: sqlite3.Connection, row: dict[str, Any], told: str) -> None:
    """A stop that reaches a manager gives it one ticket to act on at once: the task, the builder that ran it
    NO_PROGRESS_LIMIT times with no progress, and that it may reassign the task once, to someone other than that builder.
    A stop that reaches the builder itself (no manager reads the task) makes no ticket."""
    owner = row["owner_id"]
    if not told or told == owner or not cast(bool, H.is_bot(told)):
        return
    slug = cast(str, H.actor_id(owner))
    label = f"Ticket {row['number']}" if row["number"] else row["title"]
    body = (
        f"{row['title']}\n\nBuilder {slug} ran this task {NO_PROGRESS_LIMIT} times and it made no progress on any of them, "
        "so Tico has stopped resuming it. You may reassign it once, to a builder other than "
        f"{slug}, or take it yourself. Tico resumes it again only when something moves it."
    )
    try:
        H.task_create(
            c,
            H.KEEPER,
            f"{label}: builder {slug} made no progress in {NO_PROGRESS_LIMIT} runs"[:150],
            body,
            told,
            deduplicate=False,
            lint=False,
        )
    except H.Refused:
        return


def state(c: sqlite3.Connection, row: dict[str, Any], at: str) -> dict[str, Any] | None:
    """None when the task is not stranded now, else {"stop": whether resumes stop here, "since": resumes in a row with
    no progress}."""
    if not stranded(c, row, at):
        return None
    tid = row["id"]
    progress = _progress(c, row)
    resumes = [
        r[0]
        for r in c.execute(
            "SELECT created_at FROM history_events WHERE subject_type='task' AND subject_id=? AND kind=? "
            "ORDER BY created_at",
            (tid, RESUMED),
        )
    ]
    last_run = c.execute(
        "SELECT max(coalesce(settled_at, finished_at, created_at)) FROM attempts WHERE bot_id=? AND task_id=?",
        (cast(str, H.actor_id(row["owner_id"])), tid),
    ).fetchone()[0]
    others = c.execute(
        "SELECT max(created_at) FROM messages WHERE conversation_id=? AND deleted_at IS NULL "
        "AND sender_id IS NOT NULL AND sender_id NOT IN (?,?)",
        (row["conversation_id"], H.KEEPER, row["owner_id"]),
    ).fetchone()[0]
    quiet_since: str = max(
        t
        for t in (row["updated_at"], progress, last_run, others, resumes[-1] if resumes else None, "")
        if t is not None
    )
    if quiet_since > cast(str, H.shift(at, minutes=-QUIET_MINUTES)):
        return None
    since = sum(1 for r in resumes if r > progress)
    if since >= NO_PROGRESS_LIMIT:
        if c.execute(
            "SELECT 1 FROM history_events WHERE subject_type='task' AND subject_id=? AND kind=? AND created_at>? LIMIT 1",
            (tid, STOPPED, progress),
        ).fetchone():
            return None  # already stopped, and listed
        return {"stop": True, "since": since}
    if sum(1 for r in resumes if r >= cast(str, H.shift(at, hours=-24))) >= DAY_LIMIT:
        return None
    return {"stop": False, "since": since}


def stranded(c: sqlite3.Connection, row: dict[str, Any] | None, at: str) -> bool:
    """Whether this task is in progress with no run and nothing recorded to wait on (the module's docstring), its
    quiet time and its resumes aside."""
    from . import auto_tasks as _auto_tasks
    from . import task_holds as _task_holds

    auto_tasks: Any = _auto_tasks
    task_holds: Any = _task_holds

    if not row or row.get("status") != "doing" or not cast(bool, H.is_bot(row.get("owner_id"))):
        return False
    row = cast(dict[str, Any] | None, task_holds.with_holds(c, row))
    if row is None:
        return False
    if row.get("blocked_since") or row.get("snoozed_until") and row["snoozed_until"] > at:
        return False
    if (
        row.get("next_run")
        or row.get("resume")
        or row.get("carried_by")
        and cast(bool, auto_tasks.carrier_held(c, row))
    ):
        return False
    if row.get("check_back_at") and row["check_back_at"] > at:
        return False
    if (cast(dict[str, Any] | None, H.type_get(c, row.get("type_id"))) or {}).get("role") == "product-roadmap":
        return False
    owner = row["owner_id"]
    if not cast(bool, H.bot_reads_task(c, owner, row)):
        return False
    if _live(c, row):
        return False
    if c.execute(
        "SELECT 1 FROM quarantine_holds WHERE bot_id=? AND task_id=? AND released_at IS NULL LIMIT 1",
        (cast(str, H.actor_id(owner)), row["id"]),
    ).fetchone():
        return False
    if cast(bool, auto_tasks.waiting(c, row)):
        return False
    # A due date ahead says when to look again at work not begun; work in progress does not wait for it.
    return H.recorded_wait(c, {**row, "due_at": None}, at) is None


def handles(c: sqlite3.Connection, row: dict[str, Any] | None) -> bool:
    """Whether the sweep has this task in hand: a bot's task in progress that it has not stopped resuming. The task
    roundup's quiet line and Health's stuck-task check leave such a task to it."""
    if not row or row.get("status") != "doing" or not cast(bool, H.is_bot(row.get("owner_id"))):
        return False
    progress = _progress(c, row)
    since = c.execute(
        "SELECT count(*) FROM history_events WHERE subject_type='task' AND subject_id=? AND kind=? AND created_at>?",
        (row["id"], RESUMED, progress),
    ).fetchone()[0]
    return since < NO_PROGRESS_LIMIT


def hears(c: sqlite3.Connection, row: dict[str, Any]) -> str:
    """Who hears that resumes stopped: the owner's manager on the org chart when that is another active bot that reads
    the task, else the owner itself. Never a person."""
    owner = row["owner_id"]
    found = c.execute(
        f"SELECT bc.reports_to,{BS.CONFIG} AS config FROM bot_config bc {BS.JOIN} WHERE bc.bot=?",
        (cast(str, H.actor_id(owner)),),
    ).fetchone()
    declared: dict[str, Any] = json.loads(found[1]) if found and found[1] else {}
    target: str = cast(str, (found[0] if found else "") or declared.get("reports_to") or "")
    target = target if not target or cast(str | None, H.actor_kind(target)) else cast(str, H.bot_actor(target))
    if (
        target
        and cast(bool, H.is_bot(target))
        and target != owner
        and (cast(dict[str, Any] | None, H.bot(c, cast(str, H.actor_id(target)))) or {}).get("state") == "active"
        and cast(bool, H.bot_reads_task(c, target, row))
    ):
        return target
    return owner


def stops_for(c: sqlite3.Connection, actor: str, at: str) -> list[tuple[Any, Any, dict[str, Any]]]:
    """The stops `actor` hears of that still stand: [(history row id, when, task row)], oldest first. A stop stands while
    its task is still in progress with the same owner and nothing moved it since."""
    out: list[tuple[Any, Any, dict[str, Any]]] = []
    for event_id, when, task_id in c.execute(
        "SELECT id, created_at, subject_id FROM history_events WHERE subject_type='task' AND kind=? AND created_at>? "
        "AND new=? ORDER BY created_at",
        (STOPPED, cast(str, H.shift(at, days=-STOP_LOOKBACK_DAYS)), actor),
    ).fetchall():
        row = cast(dict[str, Any] | None, H.task(c, task_id))
        if row and row["status"] == "doing" and cast(bool, H.is_bot(row["owner_id"])) and _progress(c, row) < when:
            out.append((event_id, when, row))
    return out


def told(c: sqlite3.Connection, at: str) -> set[str]:
    """The actors told of a stop in the lookback, for the roundup's candidates."""
    return {
        r[0]
        for r in c.execute(
            "SELECT DISTINCT new FROM history_events WHERE subject_type='task' AND kind=? AND created_at>?",
            (STOPPED, cast(str, H.shift(at, days=-STOP_LOOKBACK_DAYS))),
        )
        if r[0]
    }


def _progress(c: sqlite3.Connection, row: dict[str, Any]) -> str:
    """When the task last moved on (the module's docstring), or ""."""
    tid, owner = row["id"], row["owner_id"]
    hist = c.execute(
        "SELECT max(created_at) FROM history_events WHERE subject_type='task' AND subject_id=? AND "
        f"(kind IN ('status','step','owner') OR actor_id=? AND kind NOT IN ({_BOOKKEEPING}))",
        (tid, owner, *BOOKKEEPING),
    ).fetchone()[0]
    said = c.execute(
        "SELECT max(m.created_at) FROM messages m WHERE m.conversation_id=? AND m.sender_id=? AND m.deleted_at IS NULL "
        "AND NOT EXISTS (SELECT 1 FROM messages n WHERE n.id=m.in_reply_to AND n.sender_id=? "
        "AND json_extract(n.refs,'$.stranded')=1)",
        (row["conversation_id"], owner, H.KEEPER),
    ).fetchone()[0]
    filed = c.execute(
        "SELECT max(created_at) FROM task_relations WHERE to_task=? AND kind='parent'", (tid,)
    ).fetchone()[0]
    return max((t for t in (hist, said, filed) if t), default="")


def _live(c: sqlite3.Connection, row: dict[str, Any]) -> bool:
    """Whether a job for the task is owed or a run on it is live."""
    slug = cast(str, H.actor_id(row["owner_id"]))
    if c.execute(
        "SELECT 1 FROM jobs j JOIN messages m ON m.id=j.message_id JOIN conversations cv ON cv.id=m.conversation_id "
        f"WHERE j.bot_id=? AND j.state IN ({_MARKS}) AND {H.MESSAGE_TASK_SQL}=? LIMIT 1",
        (slug, *LIVE_JOBS, row["id"]),
    ).fetchone():
        return True
    return (
        c.execute(
            "SELECT 1 FROM attempts WHERE bot_id=? AND task_id=? AND state IN ('leased','running') LIMIT 1",
            (slug, row["id"]),
        ).fetchone()
        is not None
    )


def _bot_ready(c: sqlite3.Connection, slug: str) -> bool:
    """Whether the bot can take a resume now: online and not held (check_pools.online), not draining, and with no resume
    of its own still queued or going. Its other work does not hold a resume back: the wake waits in line behind that
    work, so a bot that is never idle still gets its resumes (one at a time)."""
    from . import check_pools as _check_pools

    check_pools: Any = _check_pools

    if not cast(bool, check_pools.online(c, slug)):
        return False
    if c.execute("SELECT 1 FROM bot_control WHERE bot=? AND draining=1", (slug,)).fetchone():
        return False
    return (
        c.execute(
            "SELECT 1 FROM jobs j JOIN messages m ON m.id=j.message_id "
            f"WHERE j.bot_id=? AND j.state IN ('queued','leased','running') AND {cast(str, H.unheld_sql())} "
            "AND json_extract(m.refs,'$.wake')='resume' AND json_extract(m.refs,'$.stranded')=1 LIMIT 1",
            (slug,),
        ).fetchone()
        is None
    )


def _computer(c: sqlite3.Connection, slug: str) -> str | None:
    found = c.execute("SELECT runner_id FROM assignments WHERE bot=?", (slug,)).fetchone()
    return found[0] if found else None
