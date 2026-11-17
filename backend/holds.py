# pyright: strict
"""Why a bot, one of its queued runs, or its work on one task cannot start right now: the one answer every page reads.

Each hold is stored where it was written (docs: reports D1, "one holds system"); the `holds` table takes them one
kind at a time, written by `put` and ended by `release`:

| kind              | scope   | stored in                                              | ends                               |
|-------------------|---------|--------------------------------------------------------|------------------------------------|
| `paused`          | bot     | `bots.state='paused'`, and its `paused` hold           | a person resumes it                |
| `quarantined`     | bot     | `bots.state='quarantined'`, and its `quarantine` hold  | the cooldown, or a person          |
| `company_paused`  | company | `holds`, kind `paused` on the company (company_pause)  | the owner or an admin resumes      |
| `cloud_locked`    | company | Tico Cloud access (cloud_access)                       | Tico HQ unlocks the company        |
| `run_hold`        | bot     | `holds`, kind `failing`, held: no until_at             | a try completes, or a release      |
| `budget`          | bot     | `holds`, kind `runaway` (runaway runs)                 | a release, or a raised budget      |
| `cooling_off`     | login   | `holds`, kind `usage_limit` (login_holds)              | the reset, then one probe; Try now |
| `cant_start`      | task    | `holds`, kind `cant_start` on the task, BotOps's task  | BotOps closes its task             |
| `task_quarantine` | task    | `holds`, kind `quarantine`, on the task                | the cooldown, or a person          |
| `claim_held`      | job     | `holds`, kind `claim_error` on the job, while it waits | its wait, a start, or a release    |
| `handed_back`     | job     | `holds`, kind `cant_start` on the job, while it waits  | its wait                           |
| `backoff`         | bot     | `holds`, kind `failing`: its until_at                  | its wait (seconds)                 |

`why` walks them in this order and answers the first that holds the work it is asked about, by the kind's name: the
claim's refusal reason, Health's reason key and the task card's cause all use the same names. A caller names the kinds
it shows (`kinds`): the claim reads the company and the bot's failed runs before its jobs (CLAIM_BOT) and the budget and
back-off for each job (CLAIM_JOB); a status row, a task card and a sender's warning show what stops the bot (STOPS); a
task card adds what holds the bot on that task (ON_TASK); a run's "Saved - waiting" label adds what holds that run.

A person's message passes a budget hold (it only stops work nobody asked for), and one a person typed since the last
failure passes a back-off wait (it goes with the next try), as before: a queued run's own message is judged by the
claim's rules (turn_budgets.refusal, run_failures.typed_since); `human` speaks only for new work, sent now. No other hold
lets it through. `words` is the sentence the pages show for a kind.
"""

import json
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from typing import Any, cast

from .prompt_cache import CACHE_WARM_S
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
# RUNAWAY on an automation is the run guard's hold (automations._guard): it holds the automation's runs, never a bot.
QUARANTINE = "quarantine"  # rule 8 (hubdb.quarantine, task_quarantine): QUARANTINED and TASK_QUARANTINE to the pages
FAILING = "failing"  # a bot whose runs fail at once (run_failures): BACKOFF while it waits, RUN_HOLD once held
LOGIN_LIMIT = "usage_limit"  # a login over its provider's usage limit (login_holds): USAGE_LIMIT to the pages
# A queued run whose claim raised while preparing it, and a bot whose claim check raised (bot_stops.hold_job,
# check_failed): CLAIM_HELD to the pages while the run waits; the check's hold only passes the bot over.
CLAIM_ERROR = "claim_error"
# CANT_START is a kind too: on a job its computer handed back, or took and went silent on, before starting it
# (execution.wait_unstarted): HANDED_BACK while it waits; on a task once SAME in a row could not start (BotOps's task).
# PAUSED is a kind too: a bot's own pause (on the bot) and the company's (holdable_id '') (company_pause).
# A bot-wide hold of these kinds is also bots.state, the switch every reader of a bot checks: its open hold sets it (not
# on an archived or planned bot, and a pause never over a quarantine). Its release sets the state of the first kind here
# still open on the bot, so a quarantine wins over a pause, and active only when none is.
STATE_KINDS = {QUARANTINE: "quarantined", PAUSED: "paused"}


def _switch(c: Any, kind: str, holdable_type: str, holdable_id: str, held: bool) -> None:
    state = STATE_KINDS.get(kind)
    if state is None or holdable_type != "bot":
        return
    if held:
        c.execute("UPDATE bots SET state=? WHERE id=? AND state IN ('active','paused')", (state, holdable_id))
        return
    # Only a bot still in the released kind's state: a pause released under a quarantine leaves it quarantined.
    still = " ".join(
        "WHEN EXISTS(SELECT 1 FROM holds INDEXED BY holds_open WHERE kind=? AND holdable_type='bot' AND holdable_id=? "
        "AND released_at IS NULL) THEN ?"
        for _ in STATE_KINDS
    )
    c.execute(
        f"UPDATE bots SET state=CASE {still} ELSE 'active' END WHERE id=? AND state=?",
        (*(v for k, st in STATE_KINDS.items() for v in (k, holdable_id, st)), holdable_id, state),
    )


# Pinned to the partial indexes of open holds: released holds stay as history, and stats taken while the table was small
# plan a scan of all of it (the test_holds_*_plan tests).
OPEN_SQL = (
    "SELECT * FROM holds INDEXED BY holds_open WHERE kind=? AND holdable_type=? AND holdable_id=? "
    "AND coalesce(bot_id,'')=? AND released_at IS NULL"
)


@dataclass(frozen=True)
class Backoff:
    """How a kind waits after failures in a row: `first` seconds after the first, doubling for each more up to `cap`.
    After `held_after` in a row (never, when 0) it is held, and one try (its probe) is let through `probe_first` later,
    the gap doubling for each try that fails, up to `cap`."""

    first: int
    cap: int
    held_after: int = 0
    probe_first: int = 0


KINDS: dict[str, Backoff] = {
    FAILING: Backoff(first=15, cap=8 * 3600, held_after=5, probe_first=30 * 60),
    # Never longer than CACHE_WARM_S, so a retry finds the bot's session still cached.
    CLAIM_ERROR: Backoff(first=60, cap=CACHE_WARM_S),
    # A computer's claim polls four times a second: ten hand-backs take about half an hour, not seconds.
    CANT_START: Backoff(first=15, cap=300),
}
CLAIM_CHECK = Backoff(first=60, cap=300)  # a bot's claim check that raised (claim_error on the bot)
LIVE_RUN_SQL = "SELECT 1 FROM attempts WHERE bot_id=? AND state IN ('leased','running') LIMIT 1"


def wait(kind: str | Backoff, streak: int) -> int:
    """The one backoff: seconds a hold of `kind` waits after `streak` failures in a row. Before it is held, the wait
    before the next run; once held, the gap before its next try."""
    k = KINDS[kind] if isinstance(kind, str) else kind
    if not k.held_after or streak < k.held_after:
        return min(k.first * 2 ** max(streak - 1, 0), k.cap)
    return min(k.probe_first * 2 ** (streak - k.held_after), k.cap)


def probe_due(c: Any, hold: Mapping[str, Any], now: str | None = None) -> bool:
    """Whether a held hold's one try may start now: its probe_at has come, and no run of its bot is out."""
    at = str(hold.get("probe_at") or "")
    return bool(at) and at <= (now or str(H.now())) and not c.execute(LIVE_RUN_SQL, (hold["bot_id"],)).fetchone()


def take_probe(c: Any, hold: Mapping[str, Any], job: str) -> bool:
    """The one probe: called by the claim, in its write transaction, with the job it is about to hand out while `hold`
    holds it. True when the try is due and `job` takes it: the next one is pushed out at once, as if this one failed,
    so a second claim in the same window, another lane or computer, and a try whose runner vanished all wait the
    longer gap."""
    if not probe_due(c, hold):
        return False
    now = str(H.now())
    after = H.shift(now, seconds=wait(str(hold["kind"]), int(hold["streak"]) + 1))
    c.execute("UPDATE holds SET probe_at=?,probe_job_id=?,updated_at=? WHERE id=?", (after, job, now, hold["id"]))
    return True


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
    probe_at: str | None = None,
    upkeep_task_id: str | None = None,
    probe_job_id: str | None = None,
) -> dict[str, Any]:
    """The one writer: open a hold of `kind` on the holdable, or update the one open already (holds_open). Answers
    the row. A bot's own hold names it as its bot_id. `probe_job_id` is the one job let try after `until_at`, taken at
    `probe_at`."""
    now = str(H.now())
    found = c.execute(
        "INSERT INTO holds(id,kind,holdable_type,holdable_id,bot_id,reason,details,streak,until_at,probe_at,"
        "upkeep_task_id,probe_job_id,created_by_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(kind,holdable_type,holdable_id,coalesce(bot_id,'')) WHERE released_at IS NULL DO UPDATE SET "
        "reason=excluded.reason,details=excluded.details,streak=excluded.streak,until_at=excluded.until_at,"
        "probe_at=excluded.probe_at,upkeep_task_id=excluded.upkeep_task_id,probe_job_id=excluded.probe_job_id,"
        "updated_at=excluded.updated_at RETURNING *",
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
            probe_at,
            upkeep_task_id,
            probe_job_id,
            actor,
            now,
            now,
        ),
    ).fetchall()
    _switch(c, kind, holdable_type, holdable_id, True)
    return dict(found[0])


def release(
    c: Any, kind: str, holdable_type: str, holdable_id: str, *, actor: str, how: str, bot_id: str | None = None
) -> dict[str, Any] | None:
    """End the open hold of `kind` on the holdable, kept as history: `how` is its released_how (person, botops, raised,
    expired, probe_passed, moot, superseded). Answers the row it ended, or None when none was open."""
    now = str(H.now())
    found = c.execute(
        "UPDATE holds INDEXED BY holds_open SET released_at=?,released_by_id=?,released_how=?,updated_at=? WHERE kind=? "
        "AND holdable_type=? AND holdable_id=? AND coalesce(bot_id,'')=? AND released_at IS NULL RETURNING *",
        (now, actor, how, now, kind, holdable_type, holdable_id, _held_bot(holdable_type, holdable_id, bot_id) or ""),
    ).fetchall()
    if found:
        _switch(c, kind, holdable_type, holdable_id, False)
    return dict(found[0]) if found else None


def _task_sql() -> str:
    return str(H.MESSAGE_TASK_SQL)


def claim_held_sql(many: bool = False) -> str:
    """The bots with a queued run on one task whose claim raised (an open `claim_error` hold on the job), and no other
    queued run there free of one: the claim takes that one, so the bot is not held on the task. Comma-joined, each
    "bot until_at": the bot is held while that wait is ahead (`_held_bots`), so the read takes no time and a snapshot
    can seed it. With `many`, (task, bots) for every such task. Few rows: only the open claim_error holds are read."""
    task = _task_sql()
    other = task.replace("m.", "m2.").replace("cv.", "cv2.")
    return (
        f"SELECT {task + ', ' if many else ''}group_concat(DISTINCT j.bot_id || ' ' || coalesce(h.until_at, '')) "
        "FROM holds h INDEXED BY holds_open "
        "JOIN jobs j ON j.id = h.holdable_id JOIN messages m ON m.id = j.message_id "
        "JOIN conversations cv ON cv.id = m.conversation_id "
        "WHERE h.kind = 'claim_error' AND h.holdable_type = 'job' AND h.released_at IS NULL AND j.state = 'queued' "
        "AND NOT EXISTS (SELECT 1 FROM jobs j2 JOIN messages m2 ON m2.id = j2.message_id "
        "JOIN conversations cv2 ON cv2.id = m2.conversation_id "
        f"WHERE j2.bot_id = j.bot_id AND j2.state = 'queued' AND j2.held = 0 AND {other} = {task} "
        "AND NOT EXISTS (SELECT 1 FROM holds h2 INDEXED BY holds_open WHERE h2.kind = 'claim_error' "
        "AND h2.holdable_type = 'job' AND h2.holdable_id = j2.id AND h2.released_at IS NULL)) "
        f"{'GROUP BY 1' if many else f'AND {task} = ?'}"
    )


def cant_start_sql(many: bool = False) -> str:
    """The bots (comma-joined) whose runs on one task cannot start while BotOps's task about it is open (a `cant_start`
    hold on the task, bot_stops.cant_start). With `many`, (task, bots) for every such task. Few rows: only the
    open cant_start holds are read."""
    return (
        f"SELECT {'h.holdable_id, ' if many else ''}group_concat(DISTINCT h.bot_id) FROM holds h INDEXED BY holds_open "
        "JOIN tasks r ON r.id = h.upkeep_task_id AND r.status = 'open' "
        "WHERE h.kind = 'cant_start' AND h.holdable_type = 'task' AND h.released_at IS NULL "
        f"{'GROUP BY h.holdable_id' if many else 'AND h.holdable_id = ?'}"
    )


def task_quarantine_sql(many: bool = False) -> str:
    """The bots (comma-joined) a refusal-count quarantine holds on one task (task_quarantine.hold). With `many`, (task,
    bots) for every such task."""
    return (
        f"SELECT {'holdable_id, ' if many else ''}group_concat(DISTINCT bot_id) FROM holds WHERE kind = 'quarantine' "
        f"AND holdable_type = 'task' AND released_at IS NULL {'GROUP BY holdable_id' if many else 'AND holdable_id = ?'}"
    )


def _task_reads() -> list[tuple[str, str]]:
    """Each task-scoped kind with its one-task read."""
    return [(CANT_START, cant_start_sql()), (TASK_QUARANTINE, task_quarantine_sql()), (CLAIM_HELD, claim_held_sql())]


def _many_sql(kind: str) -> str:
    if kind == CANT_START:
        return cant_start_sql(many=True)
    if kind == TASK_QUARANTINE:
        return task_quarantine_sql(many=True)
    return claim_held_sql(many=True)


def _held_bots(kind: str, bots: str, now: str) -> set[str]:
    """The bots a task read (`_task_reads`) names: for CLAIM_HELD only those whose wait is still ahead of `now`."""
    found: set[str] = set()
    for each in bots.split(","):
        bot, _, until = each.partition(" ")
        if bot and (kind != CLAIM_HELD or until > now):
            found.add(bot)
    return found


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
    now = str(H.now())
    for kind, one_sql in _task_reads():
        held = {str(task): str(bots or "") for task, bots in c.execute(_many_sql(kind)).fetchall() if task}
        for task, bots in held.items():
            if names := _held_bots(kind, bots, now):
                found.setdefault(task, set()).update(names)
        if seed:
            RC.prime(c, one_sql, {(t,): (held.get(t),) for t in task_ids or ()})
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
        """The bot's failed runs (its `failing` hold), read once: "run_hold" while it is held, "backoff" while it waits,
        else None. The claim lets a held bot's one try through when it is due (probe_due); to every page the bot stays
        held until that try completes."""
        if not self._backoff_read:
            from . import run_failures

            hold: dict[str, Any] = cast(Any, run_failures).get(self.c, self.bot, self.loads)
            if hold and hold["until_at"] is None:
                self._backoff = None if self.claim and probe_due(self.c, hold) else RUN_HOLD
            elif hold and str(hold["until_at"]) > str(H.now()):
                self._backoff = BACKOFF
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
        for each, sql in _task_reads():
            if each == kind:
                row = _read(self.c, sql, (self.task_id,))
                return bool(row and row[0] and self.bot in _held_bots(kind, str(row[0]), str(H.now())))
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


# Any login hold at all (LOGIN_LIMIT): without one no login keeps a bot waiting. One read per snapshot.
ANY_LOGIN_HOLD_SQL = (
    "SELECT 1 FROM holds INDEXED BY holds_open WHERE kind='usage_limit' AND released_at IS NULL LIMIT 1"
)


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


# Every open hold on a queued job (claim_error, cant_start): few at any time, read once per snapshot.
JOB_HOLDS_SQL = (
    "SELECT * FROM holds INDEXED BY holds_open WHERE kind IN ('claim_error','cant_start') AND holdable_type='job' "
    "AND released_at IS NULL"
)


def job_hold(c: Any, kind: str, job: Row, loads: Any = None) -> dict[str, Any] | None:
    """The open hold of `kind` on the queued `job` (a jobs row), or None: from the claim's loads when it has them, else
    from every job's open holds, read once per snapshot."""
    key = (kind, "job", str(_col(job, "id")), str(_col(job, "bot_id") or ""))
    if loads is not None:
        return loads.holds.get(key)
    from . import read_cache

    def build() -> dict[tuple[str, str, str, str], dict[str, Any]]:
        return {
            (str(r["kind"]), "job", str(r["holdable_id"]), str(r["bot_id"] or "")): dict(r)
            for r in c.execute(JOB_HOLDS_SQL)
        }

    found: dict[tuple[str, str, str, str], dict[str, Any]] = cast(Any, read_cache).value(c, "holds.jobs", build, True)
    return found.get(key)


def waiting(hold: Mapping[str, Any] | None, now: str | None = None) -> bool:
    """Whether `hold` waits now: its until_at is still ahead."""
    return bool(hold) and str((hold or {}).get("until_at") or "") > (now or str(H.now()))


def _claim_held(a: _Ask) -> bool:
    if a.job is not None:
        return (_col(a.job, "state") or "queued") == "queued" and waiting(job_hold(a.c, CLAIM_ERROR, a.job, a.loads))
    return a.on_task(CLAIM_HELD)


def _handed_back(a: _Ask) -> bool:
    return a.job is not None and waiting(job_hold(a.c, CANT_START, a.job, a.loads))


def _backoff(a: _Ask) -> bool:
    if a.backoff() != BACKOFF:
        return False
    from . import run_failures

    RF: Any = run_failures
    if a.claim and a.message is None and a.job is None:
        return not RF.person_waiting(a.c, a.bot)  # the whole queue: a person's message waiting goes with the next try
    message = a.work()
    if message is not None:
        # The claim's own rule for one job: only a message a person typed since the failure goes with the next try.
        return not RF.typed_since(a.c, a.bot, message)
    # New work, sent now: after any failure, so a person's own message goes with the next try.
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

    The work is a queued `job` (its row: id, bot_id, state, attempt_id, message_id) and its `message`, when given (the
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
        from . import run_failures

        return str(cast(Any, run_failures).words(c, bot))
    if kind == BUDGET:
        from . import turn_budgets

        return str(cast(Any, turn_budgets).words(c, bot))
    if kind == QUARANTINED:
        return str(H.quarantine_words(c, bot))
    if kind == CLAIM_HELD:
        until = str(((job_hold(c, CLAIM_ERROR, job) or {}) if job is not None else {}).get("until_at") or "")
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
