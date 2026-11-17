# pyright: strict
"""Why a bot, one of its queued runs, or its work on one task cannot start right now: the one answer every page reads.

Each hold is stored where it was written (docs: reports D1, "one holds system"); the `holds` table takes them one
kind at a time, written by `put` and ended by `release`:

| kind              | scope   | stored in                                              | ends                               |
|-------------------|---------|--------------------------------------------------------|------------------------------------|
| `paused`          | bot     | `bots.state='paused'`                                  | a person resumes it                |
| `quarantined`     | bot     | `holds`, kind `quarantine`, on the bot                 | the cooldown, or a person          |
| `company_paused`  | company | company_pause.KEY                                      | the owner or an admin resumes      |
| `cloud_locked`    | company | Tico Cloud access (cloud_access)                       | Tico HQ unlocks the company        |
| `run_hold`        | bot     | `run_backoff.held_at` (failed runs)                    | a try completes, or a release      |
| `budget`          | bot     | `holds`, kind `runaway` (runaway runs)                 | a release, or a raised budget      |
| `cooling_off`     | login   | the login hold (login_holds); the bot's status shows it | the reset, then one probe; Try now |
| `cant_start`      | task    | `task.cant_start` events with BotOps's open task       | BotOps closes its task             |
| `task_quarantine` | task    | `holds`, kind `quarantine`, on the task                | the cooldown, or a person          |
| `claim_held`      | job     | `jobs.hold_reason` (claim_holds)                       | its wait, a start, or a release    |
| `handed_back`     | job     | the run's expired attempts (execution)                 | its wait                           |
| `backoff`         | bot     | `run_backoff.wait_until`                               | its wait (seconds)                 |

`why` walks them in this order and answers the first that holds the work it is asked about, by the kind's name: the
claim's refusal reason, Health's reason key and the task card's cause all use the same names. A caller names the kinds
it shows (`kinds`): the claim reads the company and the bot's failed runs before its jobs (CLAIM_BOT) and the budget and
back-off for each job (CLAIM_JOB); a status row, a task card and a sender's warning show what stops the bot (STOPS); a
task card adds what holds the bot on that task (ON_TASK); a run's "Saved - waiting" label adds what holds that run.

A person's message passes a budget hold (it only stops work nobody asked for), and one a person typed since the last
failure passes a back-off wait (it goes with the next try), as before: a queued run's own message is judged by the
claim's rules (turn_budgets.refusal, run_backoff.job_refusal); `human` speaks only for new work, sent now. No other hold
lets it through. `words` is the sentence the pages show for a kind.
"""

import json
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from typing import Any, cast

from .store import H as _H

H: Any = _H
Row = Any  # a jobs or messages row: a dict, or a sqlite3.Row

PAUSED = "paused"
QUARANTINED = "quarantined"
COMPANY_PAUSED = "company_paused"
CLOUD_LOCKED = "cloud_locked"
RUN_HOLD = "run_hold"
BUDGET = "budget"
USAGE_LIMIT = "cooling_off"
CANT_START = "cant_start"
TASK_QUARANTINE = "task_quarantine"
CLAIM_HELD = "claim_held"
HANDED_BACK = "handed_back"
BACKOFF = "backoff"
ORDER = (
    PAUSED,
    QUARANTINED,
    COMPANY_PAUSED,
    CLOUD_LOCKED,
    RUN_HOLD,
    BUDGET,
    USAGE_LIMIT,
    CANT_START,
    TASK_QUARANTINE,
    CLAIM_HELD,
    HANDED_BACK,
    BACKOFF,
)

# What stops the bot as a whole until something changes: its status row, its task cards and a sender's warning.
STOPS = (PAUSED, QUARANTINED, COMPANY_PAUSED, CLOUD_LOCKED, RUN_HOLD, BUDGET, USAGE_LIMIT)
# What holds the bot on one task while it can run elsewhere: that task's card.
ON_TASK = (CANT_START, TASK_QUARANTINE, CLAIM_HELD)
# The claim, before it looks at a bot's jobs. Its query takes only active bots, and the login hold is judged with the
# computer it would run on (execution.login_refusal), so neither is read here.
CLAIM_BOT = (COMPANY_PAUSED, CLOUD_LOCKED, RUN_HOLD, BACKOFF)
# The claim, for each job of a bot it looks at.
CLAIM_JOB = (BUDGET, BACKOFF)
# What Tico stopped the bot for, as its status row shows it in place of its state, and the sender's warning names.
STOPPED = (RUN_HOLD, BUDGET)
# A run's "Saved - waiting" label: what Tico stopped the bot for, what holds this run, and a back-off wait.
SAVED = (*STOPPED, CLAIM_HELD, BACKOFF)

STATE_SQL = "SELECT state FROM bots WHERE id=?"

# ---------------------------------------------------------------------------------------------- the holds table
# A kind the `holds` table stores (its CHECK allows every kind; each moves into it in its own change).
RUNAWAY = "runaway"  # a bot over its run budget (turn_budgets): BUDGET to the pages and the claim
QUARANTINE = "quarantine"  # rule 8 (hubdb.quarantine, task_quarantine): QUARANTINED and TASK_QUARANTINE to the pages
OPEN_SQL = (
    "SELECT * FROM holds WHERE kind=? AND holdable_type=? AND holdable_id=? AND coalesce(bot_id,'')=? "
    "AND released_at IS NULL"
)


def _held_bot(holdable_type: str, holdable_id: str, bot_id: str | None) -> str | None:
    """The bot a hold holds: `bot_id` always names it, the holdable itself for a bot (holds_bot reads every open hold of
    a bot in one query)."""
    return bot_id or (holdable_id if holdable_type == "bot" else None)


def get(c: Any, kind: str, holdable_type: str, holdable_id: str, bot_id: str | None = None) -> dict[str, Any] | None:
    """The open hold of `kind` on the holdable, or None. Read once per snapshot (read_cache; `prime` reads many)."""
    row = _read(c, OPEN_SQL, (kind, holdable_type, holdable_id, _held_bot(holdable_type, holdable_id, bot_id) or ""))
    return dict(row) if row else None


def prime(c: Any, kind: str, holdable_type: str, holdable_ids: Collection[str]) -> None:
    """Read the open holds of `kind` on each of these holdables (a bot's own, or one naming no bot) in one statement,
    misses too, and keep them in the snapshot's read cache for `get`. Nothing outside a snapshot."""
    from . import read_cache

    RC: Any = read_cache
    if RC.cache(c) is None or not holdable_ids:
        return
    found = {
        str(row["holdable_id"]): row
        for row in c.execute(
            "SELECT * FROM holds WHERE kind=? AND holdable_type=? AND coalesce(bot_id,'')=(CASE holdable_type WHEN 'bot' "
            "THEN holdable_id ELSE '' END) AND released_at IS NULL AND holdable_id IN (SELECT value FROM json_each(?))",
            (kind, holdable_type, json.dumps(sorted(holdable_ids))),
        )
    }
    RC.prime(
        c,
        OPEN_SQL,
        {(kind, holdable_type, h, _held_bot(holdable_type, h, None) or ""): found.get(h) for h in holdable_ids},
    )


def put(
    c: Any,
    kind: str,
    holdable_type: str,
    holdable_id: str,
    *,
    actor: str,
    bot_id: str | None = None,
    reason: str = "",
    details: Mapping[str, Any] | None = None,
    streak: int = 0,
    until_at: str | None = None,
    upkeep_task_id: str | None = None,
) -> dict[str, Any]:
    """The one writer: open a hold of `kind` on the holdable, or update the one open already (holds_open). Answers
    the row. A bot's own hold names it as its bot_id."""
    now = str(H.now())
    found = c.execute(
        "INSERT INTO holds(id,kind,holdable_type,holdable_id,bot_id,reason,details,streak,until_at,upkeep_task_id,"
        "created_by_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(kind,holdable_type,holdable_id,coalesce(bot_id,'')) WHERE released_at IS NULL DO UPDATE SET "
        "reason=excluded.reason,details=excluded.details,streak=excluded.streak,until_at=excluded.until_at,"
        "upkeep_task_id=excluded.upkeep_task_id,updated_at=excluded.updated_at RETURNING *",
        (
            H.new_id(),
            kind,
            holdable_type,
            holdable_id,
            _held_bot(holdable_type, holdable_id, bot_id),
            reason,
            json.dumps(dict(details or {})),
            streak,
            until_at,
            upkeep_task_id,
            actor,
            now,
            now,
        ),
    ).fetchall()
    return dict(found[0])


def release(
    c: Any, kind: str, holdable_type: str, holdable_id: str, *, actor: str, how: str, bot_id: str | None = None
) -> dict[str, Any] | None:
    """End the open hold of `kind` on the holdable, kept as history: `how` is its released_how (person, botops, raised,
    expired, probe_passed, moot, superseded). Answers the row it ended, or None when none was open."""
    now = str(H.now())
    found = c.execute(
        "UPDATE holds SET released_at=?,released_by_id=?,released_how=?,updated_at=? WHERE kind=? AND holdable_type=? "
        "AND holdable_id=? AND coalesce(bot_id,'')=? AND released_at IS NULL RETURNING *",
        (now, actor, how, now, kind, holdable_type, holdable_id, _held_bot(holdable_type, holdable_id, bot_id) or ""),
    ).fetchall()
    return dict(found[0]) if found else None


def _task_sql() -> str:
    return str(H.MESSAGE_TASK_SQL)


def claim_held_sql(many: bool = False) -> str:
    """The bots (comma-joined) with a run on one task its claim could not start, held for its wait now
    (claim_holds.hold; held=1 ends at the wait, release_due), and no other queued run there that is not held: the
    claim takes that one (bot_claimable reads only held=0 jobs), so the bot is not held on the task. With `many`,
    (task, bots) for every such task. Few rows: jobs_held_queued holds only held queued jobs."""
    task = _task_sql()
    other = task.replace("m.", "m2.").replace("cv.", "cv2.")
    return (
        f"SELECT {task + ', ' if many else ''}group_concat(DISTINCT j.bot_id) FROM jobs j "
        "JOIN messages m ON m.id = j.message_id JOIN conversations cv ON cv.id = m.conversation_id "
        "WHERE j.held = 1 AND j.state = 'queued' AND j.hold_reason = ? "
        "AND NOT EXISTS (SELECT 1 FROM jobs j2 JOIN messages m2 ON m2.id = j2.message_id "
        "JOIN conversations cv2 ON cv2.id = m2.conversation_id "
        f"WHERE j2.bot_id = j.bot_id AND j2.state = 'queued' AND j2.held = 0 AND {other} = {task}) "
        f"{'GROUP BY 1' if many else f'AND {task} = ?'}"
    )


def task_quarantine_sql(many: bool = False) -> str:
    """The bots (comma-joined) a refusal-count quarantine holds on one task (task_quarantine.hold). With `many`, (task,
    bots) for every such task."""
    return (
        f"SELECT {'holdable_id, ' if many else ''}group_concat(DISTINCT bot_id) FROM holds WHERE kind = 'quarantine' "
        f"AND holdable_type = 'task' AND released_at IS NULL {'GROUP BY holdable_id' if many else 'AND holdable_id = ?'}"
    )


def _claim_reason() -> str:
    from . import claim_holds

    return str(cast(Any, claim_holds).REASON)


def _start_holds() -> Any:
    from . import start_holds

    return start_holds


def _task_reads() -> list[tuple[str, str, tuple[Any, ...]]]:
    """Each task-scoped kind with its one-task read and the arguments before the task's id."""
    SH = _start_holds()
    return [
        (CANT_START, str(SH.held_sql()), (H.KEEPER, SH.EVENT)),
        (TASK_QUARANTINE, task_quarantine_sql(), ()),
        (CLAIM_HELD, claim_held_sql(), (_claim_reason(),)),
    ]


def _many_sql(kind: str) -> tuple[str, tuple[Any, ...]]:
    SH = _start_holds()
    if kind == CANT_START:
        return str(SH.held_sql(many=True)), (H.KEEPER, SH.EVENT)
    if kind == TASK_QUARANTINE:
        return task_quarantine_sql(many=True), ()
    return claim_held_sql(many=True), (_claim_reason(),)


def _read(c: Any, sql: str, args: tuple[Any, ...]) -> Any:
    from . import read_cache

    return cast(Any, read_cache).one(c, sql, args)


def on_tasks(c: Any, task_ids: Collection[str] | None = None) -> dict[str, set[str]]:
    """{task: the bots a task-scoped hold (ON_TASK) keeps off it}, every task's with no `task_ids`. With `task_ids`, a
    snapshot's read cache is seeded with each task's answer (a pass over many tasks reads them here first): three
    statements in all."""
    from . import read_cache

    RC: Any = read_cache
    found: dict[str, set[str]] = {}
    seed = task_ids is not None and RC.cache(c) is not None
    for kind, one_sql, before in _task_reads():
        sql, args = _many_sql(kind)
        held = {str(task): str(bots or "") for task, bots in c.execute(sql, args).fetchall() if task}
        for task, bots in held.items():
            found.setdefault(task, set()).update(b for b in bots.split(",") if b)
        if seed:
            RC.prime(c, one_sql, {(*before, t): (held.get(t),) for t in task_ids or ()})
    return found if task_ids is None else {t: bots for t, bots in found.items() if t in task_ids}


@dataclass
class _Ask:
    """One question to `why`: the bot, what the work is, and what has been read for it so far."""

    c: Any
    bot: str
    message: Row | None
    job: Row | None
    task_id: str | None
    human: bool
    claim: bool
    loads: Any
    _state: str | None = None
    _backoff: str | None = field(default=None)
    _backoff_read: bool = False
    _message_read: bool = False

    def state(self) -> str:
        if self._state is None:
            row = _read(self.c, STATE_SQL, (self.bot,))
            self._state = str(row[0] or "active") if row else ""
        return self._state

    def backoff(self) -> str | None:
        """The bot's failed runs, read once: "run_hold" while it is held, "backoff" while it waits, else None. The
        claim asks run_backoff.refusal, which lets a held bot's one try through when it is due; to every page the bot
        stays held until that try completes."""
        if not self._backoff_read:
            from . import run_backoff

            RB: Any = run_backoff
            row: Any = self.loads.backoff_of(self.c, self.bot) if self.loads is not None else RB.get(self.c, self.bot)
            if self.claim:
                self._backoff = RB.refusal(self.c, self.bot, row)
            elif row.get("held_at"):
                self._backoff = RUN_HOLD
            else:
                self._backoff = BACKOFF if (row.get("wait_until") or "") > H.now() else None
            self._backoff_read = True
        return self._backoff

    def work(self) -> Row | None:
        """The work's message: the one given, else the queued `job`'s own, read when a check needs it (a job row with no
        message_id has none). None for new work: what is being sent now."""
        if self.message is None and not self._message_read:
            self._message_read = True
            mid = _col(self.job, "message_id") if self.job is not None else None
            self.message = H.message(self.c, mid) if mid else None
        return self.message

    def on_task(self, kind: str) -> bool:
        if not self.task_id:
            return False
        for each, sql, before in _task_reads():
            if each == kind:
                row = _read(self.c, sql, (*before, self.task_id))
                return bool(row and row[0] and self.bot in str(row[0]).split(","))
        return False


def _col(row: Row, name: str) -> Any:
    try:
        return row[name]
    except (KeyError, IndexError):
        return None


def _company_paused(a: _Ask) -> bool:
    from . import company_pause

    return (a.loads.company if a.loads is not None else company_pause.record(a.c)) is not None


def _cloud_locked(a: _Ask) -> bool:
    from . import cloud_access

    CA: Any = cloud_access
    return bool(CA.locked(a.c, a.loads.access if a.loads is not None else CA.access(a.c)))


def _budget(a: _Ask) -> bool:
    from . import turn_budgets

    TB: Any = turn_budgets
    if a.message is not None:
        return TB.refusal(a.c, a.bot, a.message) == BUDGET  # the claim's own rule for one job
    if not TB.get(a.c, a.bot).get("held_at"):
        return False
    message = a.work()  # a queued job's message, read only now that the bot is held
    if message is not None:
        return TB.refusal(a.c, a.bot, message) == BUDGET
    return not a.human  # new work: a person's message passes


# Any login hold at all (login_holds.PREFIX): without one no login keeps a bot waiting. One read per snapshot.
ANY_LOGIN_HOLD_SQL = "SELECT 1 FROM registry_metadata WHERE key >= 'login-hold:' AND key < 'login-hold;' LIMIT 1"


def _usage_limit(a: _Ask) -> bool:
    """The claim's own answer, execution.cooling_off: the hold on the login the bot's next turn would sign in with on
    the computer it is assigned to (its own login, a company subscription's, or the Gateway's), and no fallback ready.
    It is read from the login holds, not from the bot's Rate limited status: a bot sharing a login another bot met the
    limit on keeps its own status until it runs, yet the claim holds it. Each bot is judged once per snapshot, and not
    at all while no login is held. Readers have no Settings here: they only seed a company that has not saved its
    providers (saved at initialization) and give the Gateway's key, which gateway_connection.env_key reads from the
    environment without them."""
    if not _read(a.c, ANY_LOGIN_HOLD_SQL, ()):
        return False
    from . import execution, read_cache

    EX: Any = execution
    RC: Any = read_cache
    return bool(RC.value(a.c, ("holds.cooling_off", a.bot), lambda: bool(EX.cooling_off(a.c, None, a.bot)), True))


def _claim_held(a: _Ask) -> bool:
    if a.job is not None:
        job = a.job
        state = _col(job, "state") or "queued"
        return bool(_col(job, "held")) and state == "queued" and _col(job, "hold_reason") == _claim_reason()
    return a.on_task(CLAIM_HELD)


def _handed_back(a: _Ask) -> bool:
    if a.job is None:
        return False
    from . import execution

    until = cast(Any, execution).handed_back_until(a.c, a.job)
    return bool(until) and until > H.now()


def _backoff(a: _Ask) -> bool:
    if a.backoff() != BACKOFF:
        return False
    from . import run_backoff

    RB: Any = run_backoff
    if a.claim and a.message is None and a.job is None:
        return not RB.person_waiting(a.c, a.bot)  # the whole queue: a person's message waiting goes with the next try
    message = a.work()
    if message is not None:
        # The claim's own rule for one job: only a message a person typed since the failure goes with the next try.
        return RB.job_refusal(a.c, a.bot, message) == BACKOFF
    # New work, sent now: after any failure, so a person's own message goes with the next try.
    return not a.human


CHECKS: dict[str, Callable[[_Ask], bool]] = {
    PAUSED: lambda a: a.state() == "paused",
    QUARANTINED: lambda a: a.state() == "quarantined" or get(a.c, QUARANTINE, "bot", a.bot) is not None,
    COMPANY_PAUSED: _company_paused,
    CLOUD_LOCKED: _cloud_locked,
    RUN_HOLD: lambda a: a.backoff() == RUN_HOLD,
    BUDGET: _budget,
    USAGE_LIMIT: _usage_limit,
    CANT_START: lambda a: a.on_task(CANT_START),
    TASK_QUARANTINE: lambda a: a.on_task(TASK_QUARANTINE),
    CLAIM_HELD: _claim_held,
    HANDED_BACK: _handed_back,
    BACKOFF: _backoff,
}


def why(
    c: Any,
    bot: str,
    *,
    kinds: Collection[str] = ORDER,
    message: Row | None = None,
    job: Row | None = None,
    task_id: str | None = None,
    human: bool = False,
    claim: bool = False,
    loads: Any = None,
) -> str | None:
    """The first of `kinds`, in ORDER, that keeps `bot`'s work from starting now, or None.

    The work is a queued `job` (its row: held, hold_reason, attempt_id, message_id) and its `message`, when given (the
    job's own is read when a budget or back-off needs it); else new work, sent now, `human` saying it is a person's.
    `claim`: the claim asks, about the whole queue when no message or job is given: a held bot whose try is due is let
    through for it, and a back-off holds nothing while a person's message waits.
    `task_id` asks about the bot's work on that task too. `loads` is what the claim read for its bots (claim_loads),
    when it has it."""
    ask = _Ask(c, bot, message, job, task_id, human, claim, loads)
    for kind in ORDER:
        if kind in kinds and CHECKS[kind](ask):
            return kind
    return None


def words(c: Any, bot: str, kind: str, job: Row | None = None) -> str:
    """The sentence the pages show for `kind` holding `bot` (and `job`, for a run's own hold)."""
    if kind in (RUN_HOLD, BACKOFF):
        from . import run_backoff

        return str(cast(Any, run_backoff).words(c, bot))
    if kind == BUDGET:
        from . import turn_budgets

        return str(cast(Any, turn_budgets).words(c, bot))
    if kind == QUARANTINED:
        return str(H.quarantine_words(c, bot))
    if kind == CLAIM_HELD:
        until = str((_col(job, "hold_until") if job is not None else None) or "")
        at = f" at {until[11:16]} UTC" if len(until) >= 16 else ""
        return f"Tico couldn't start this run: an error happened while preparing it. It tries again{at}."
    if kind == COMPANY_PAUSED:
        from . import company_pause

        found = company_pause.record(c)
        return company_pause.waiting_words(found) if found else ""
    return ""


def overlay(c: Any, row: dict[str, Any] | None) -> dict[str, Any] | None:
    """A bot's status row as the pages read it while Tico stops the bot (STOPPED): paused, with the words. A running
    turn keeps its row, and so does a quarantined bot's. A short back-off after a failed run keeps the state and focus,
    and adds its wait in words as `backoff` for the pages to show beside them."""
    if not row or row.get("state") in ("running", "quarantined"):
        return row
    bot = str(row["bot"])
    kind = why(c, bot, kinds=(*STOPPED, BACKOFF))
    if kind == BACKOFF:
        return {**row, "backoff": words(c, bot, kind)}
    if kind:
        return {**row, "state": "paused", "focus": words(c, bot, kind), "stopped": kind}
    return row


def warning(c: Any, actor: str | None) -> str | None:
    """The words a sender gets when its message or task goes to a bot Tico stopped (STOPPED), else None. A note beside
    the saved message, not a refusal: the work stays queued and runs once the bot is released. A claim hold holds only
    its own run, so new work gets no warning for it; the run's own label names it (`queued_reason`)."""
    if not str(actor or "").startswith("bot:"):
        return None
    bot = str(actor)[4:]
    kind = why(c, bot, kinds=STOPPED)
    return f"{words(c, bot, kind)} What you sent is kept and starts when the bot runs again." if kind else None


def queued_reason(
    c: Any, bot: str, human: bool = False, job: Row | None = None, message: Row | None = None
) -> str | None:
    """The words after "Saved - " for work waiting on a stop, on its run's own hold, or on a back-off, else None. `job`
    is the queued run, when the label is its own, and `message` its message when read already: the budget and back-off
    then judge that message as the claim does. `human` that new work, with no job yet, is a person's message."""
    kind = why(c, bot, kinds=SAVED, job=job, message=message, human=human)
    return "waiting: " + words(c, bot, kind, job) if kind else None
