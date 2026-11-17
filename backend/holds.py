# pyright: strict
"""Why a bot, one of its queued runs, or its work on one task cannot start right now: the one answer every page reads.

Each hold is still stored where it was written (docs: reports D1, PR 1 of "one holds system"):

| kind              | scope   | stored in                                              | ends                               |
|-------------------|---------|--------------------------------------------------------|------------------------------------|
| `paused`          | bot     | `bots.state='paused'`                                  | a person resumes it                |
| `quarantined`     | bot     | `bots.state='quarantined'`                             | the cooldown, or a person          |
| `company_paused`  | company | company_pause.KEY                                      | the owner or an admin resumes      |
| `cloud_locked`    | company | Tico Cloud access (cloud_access)                       | Tico HQ unlocks the company        |
| `run_hold`        | bot     | `run_backoff.held_at` (failed runs)                    | a try completes, or a release      |
| `budget`          | bot     | `bot_budget.held_at` (runaway runs)                    | a release, or a raised budget      |
| `cooling_off`     | login   | the login hold (login_holds); the bot's status shows it | the reset, then one probe; Try now |
| `cant_start`      | task    | `task.cant_start` events with BotOps's open task       | BotOps closes its task             |
| `task_quarantine` | task    | `quarantine_holds`                                     | the cooldown, or a person          |
| `claim_held`      | job     | `jobs.hold_reason` (claim_holds)                       | its wait, a start, or a release    |
| `handed_back`     | job     | the run's expired attempts (execution)                 | its wait                           |
| `backoff`         | bot     | `run_backoff.wait_until`                               | its wait (seconds)                 |

`why` walks them in this order and answers the first that holds the work it is asked about, by the kind's name: the
claim's refusal reason, Health's reason key and the task card's cause all use the same names. A caller names the kinds
it shows (`kinds`): the claim reads the company and the bot's failed runs before its jobs (CLAIM_BOT) and the budget and
back-off for each job (CLAIM_JOB); a status row, a task card and a sender's warning show what stops the bot (STOPS); a
task card adds what holds the bot on that task (ON_TASK); a run's "Saved - waiting" label adds what holds that run.

A person's message passes a budget hold (it only stops work nobody asked for) and a back-off wait (it goes with the next
try), as before; no other hold lets it through. `words` is the sentence the pages show for a kind.
"""

from collections.abc import Callable, Collection
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
STATUS_SQL = "SELECT state FROM bot_status WHERE bot=?"


def _task_sql() -> str:
    return str(H.MESSAGE_TASK_SQL)


def claim_held_sql(many: bool = False) -> str:
    """The bots (comma-joined) with a run on one task its claim could not start, held for its wait now
    (claim_holds.hold; held=1 ends at the wait, release_due). With `many`, (task, bots) for every such task. Few rows:
    jobs_held_queued holds only held queued jobs."""
    task = _task_sql()
    return (
        f"SELECT {task + ', ' if many else ''}group_concat(DISTINCT j.bot_id) FROM jobs j "
        "JOIN messages m ON m.id = j.message_id JOIN conversations cv ON cv.id = m.conversation_id "
        f"WHERE j.held = 1 AND j.state = 'queued' AND j.hold_reason = ? {'GROUP BY 1' if many else f'AND {task} = ?'}"
    )


def task_quarantine_sql(many: bool = False) -> str:
    """The bots (comma-joined) a refusal-count quarantine holds on one task (task_quarantine.hold). With `many`, (task,
    bots) for every such task."""
    return (
        f"SELECT {'task_id, ' if many else ''}group_concat(DISTINCT bot_id) FROM quarantine_holds "
        f"WHERE released_at IS NULL AND {'task_id IS NOT NULL GROUP BY task_id' if many else 'task_id = ?'}"
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

    def by_person(self) -> bool:
        """Whether the work is a person's message, which a budget hold lets through (turn_budgets.refusal)."""
        if self.message is not None:
            return str(_col(self.message, "sender_id") or "").startswith("human:")
        return self.human

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

    return not a.by_person() and bool(cast(Any, turn_budgets).get(a.c, a.bot).get("held_at"))


def _usage_limit(a: _Ask) -> bool:
    # The bot's Rate limited status is the display of the claim's own answer (execution.cooling_off), kept in step
    # with the login holds by execution.relieve: a reader with no computer to judge the login on reads that.
    row = _read(a.c, STATUS_SQL, (a.bot,))
    return bool(row and row[0] == "limited")


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
    if a.message is not None:
        return RB.job_refusal(a.c, a.bot, a.message) == BACKOFF  # a person's message sent since the failure goes
    if a.claim:
        return not RB.person_waiting(a.c, a.bot)  # the whole queue: a person's message waiting goes with the next try
    return not a.human


CHECKS: dict[str, Callable[[_Ask], bool]] = {
    PAUSED: lambda a: a.state() == "paused",
    QUARANTINED: lambda a: a.state() == "quarantined",
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

    The work is a queued `job` (its row: held, hold_reason, attempt_id) and its `message`, when given; else new work,
    `human` saying it is a person's. `claim`: the claim asks, about the whole queue when no message is given: a held
    bot whose try is due is let through for it, and a back-off holds nothing while a person's message waits.
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


def queued_reason(c: Any, bot: str, human: bool = False, job: Row | None = None) -> str | None:
    """The words after "Saved - " for work waiting on a stop, on its run's own hold, or on a back-off, else None. `job`
    is the queued run, when the label is its own; `human` that the work is a person's message."""
    kind = why(c, bot, kinds=SAVED, job=job, human=human)
    return "waiting: " + words(c, bot, kind, job) if kind else None
