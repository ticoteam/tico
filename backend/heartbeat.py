# pyright: strict
"""The Heartbeat (docs/heartbeat.md): one short beat, every 10 s by default, that runs in order the steps that must happen
within seconds but have no push event to start them:
  - due: time passing (a deferred run, an hour gone by);
  - recovery: crashes nothing announces (a dead turn, a silent run, a lost wake);
  - poll: outside systems that do not push (GitHub, HQ paging).

Each step is a block that ran in the server's 10-second loop (backend/app.py `schedule_loop`, backend/scheduler.py
`Scheduler.tick`) and recorded its own System job row. Phase 1 moved them here as they were: same code, cadence, gates
and order. A step's `run(ctx)` returns (words, quiet); raising is its failure, kept to that step: the others still run,
and the failure goes where it went before (the pass's `failures`, `telemetry.capture`).

What a beat did is stored small (migration 20261106094503_heartbeat): each beat writes the one `heartbeat_states` row
(also the server's liveness, `liveness`); a `heartbeat_beats` row only for a beat where a step did something or failed
(repeated failures fold); a step's `heartbeat_steps` row only when it did something, failed, changed its result or is
timed from its last run, else every 5 minutes, its counts kept in memory between (`_Memory`). The Heartbeat is one System job (system_routines.JOBS `heartbeat`): one run per beat,
quiet repeats folded. Its screen is ui/app/automations-heartbeat.ts (GET/PUT /api/v2/heartbeat)."""

import json
import logging
import threading
import time
import weakref
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, cast

from . import scheduler as S
from . import system_routines as SR
from .store import H, Problem

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Callable, Generator, Sequence
    from contextlib import AbstractContextManager

    from fastapi import FastAPI

    from .auth import Auth, Identity
    from .config import Settings
    from .execution import Execution
    from .github_app import GitHubApp
    from .observability import Observability
    from .store import Store

LOG = logging.getLogger("tico.heartbeat")

INTERVAL_KEY = "heartbeat:interval_s"  # registry_metadata
INTERVAL_DEFAULT, INTERVAL_MIN, INTERVAL_MAX = 10, 5, 60
POLL_FLOOR = timedelta(seconds=10)  # a shorter beat never polls an outside system more often than before
REST_FLOOR = 10.0  # seconds: the rest of the loop, likewise (backend/app.py)
WINDOW = timedelta(hours=12)  # the day counts: this window and the one before it
KEEP = timedelta(days=7)  # working beats kept this long...
KEEP_BEATS = 5000  # ...and at most this many
PRUNE_EVERY = timedelta(hours=1)
FLUSH_EVERY = timedelta(minutes=5)  # quiet steps' counts and last runs, kept in memory between writes
ALIVE_MIN = 60  # seconds: the server counts as running while it beat this recently...
ALIVE_SLACK = 60  # ...or within one interval and this much more, whichever is longer
CARRY_MAX = 50  # failures of skipped rest passes carried to the next one, at most
PAGE = 20
KINDS = (("due", "Due times"), ("recovery", "Recovery"), ("poll", "Outside polls"))
KIND_WORDS = dict(KINDS)


class Failed(Exception):
    """A step's failure in its own words (a block that set `run.error` without raising): recorded, not captured."""


@dataclass(frozen=True)
class Step:
    key: str  # its System job key before the Heartbeat (system_routines history keeps the old rows)
    name: str
    kind: str  # "due", "recovery" or "poll"
    every: timedelta | None  # None: every beat; else at most this often (today's cadence)
    # run(ctx) -> (words, quiet); words None: the step records its own outcome later
    run: "Callable[[Context], tuple[str | None, bool]]"  # Context is defined below the dataclass
    failure: str | None = None  # the {name: kind} entry it adds to the pass's failures when it raises, as before
    capture: str | None = None  # the telemetry name a raise went to before; None: it never went, or captures its own
    lasting: bool = False  # timed from its last recorded start, so a restart keeps its hour
    control: str | None = None  # its switch and preset (system_routines.CONTROLS)
    loop: bool = False  # one of the server loop's own (needs the app): never in a bare Scheduler.tick

    @property
    def floor(self) -> timedelta | None:
        """How often it may run at most: `every`, and a poll never more often than POLL_FLOOR."""
        if self.kind == "poll":
            return max(self.every or POLL_FLOOR, POLL_FLOOR)
        return self.every


class Context:
    """One pass of the server's loop: what a step reads, the pass's failures and write-lock holds (the scheduler's
    service_health row reports them, the rest of Scheduler.tick included), and `locked`, one short write per step."""

    store: "Store"
    execution: "Execution"
    github_app: "GitHubApp | None"
    failures: list[dict[str, str]]
    holds: list[tuple[float, float, str]]

    def __init__(
        self,
        scheduler: S.Scheduler,
        at: datetime | None = None,
        app: "FastAPI | None" = None,
        telemetry: "Observability | None" = None,
    ) -> None:
        self.scheduler = scheduler
        self.store, self.execution, self.github_app = scheduler.store, scheduler.execution, scheduler.github_app
        self.app, self.telemetry = app, telemetry
        self.at = at or datetime.now(timezone.utc)
        self.failures, self.holds = [], []
        self.started = time.monotonic()

    @property
    def settings(self) -> "Settings":
        return self.store.settings

    @property
    def auth(self) -> "Auth":
        return self.execution.auth

    @contextmanager
    def locked(self, step: str) -> "Generator[sqlite3.Connection]":
        # One short write transaction per step and per reminder batch. The pass used to be one transaction, and on
        # a hub with many tasks every request waited 20-30 s behind it (TK-RCZJXEWA). Each step is whole on its
        # own; reminders keep their dedupe rows (task_reminders), checked under the same lock that writes them.
        # The hold is timed from BEGIN to commit; the wait for the lock is kept apart, since it measures other
        # writers, not this pass.
        asked, begun = time.monotonic(), None
        try:
            with cast("AbstractContextManager[sqlite3.Connection]", self.store.transaction()) as c:
                begun = time.monotonic()
                yield c
        finally:
            if begun is not None:
                self.holds.append((time.monotonic() - begun, begun - asked, step))


# ----------------------------------------------------------------------------- the server loop's own steps
# These ran beside Scheduler.tick in backend/app.py `schedule_loop`; moved here as they were.
def github_sync(ctx: Context) -> tuple[str, bool]:
    github_app = ctx.app.state.github_app
    from .github import (
        flush_wakes,
        reconcile_worktree_prs,
        refresh_deployed_tasks,
        refresh_conflicts,
        refresh_pending_checks,
        refresh_queue_removals,
    )

    with ctx.store.transaction() as c:
        flush_wakes(c)
    refresh_deployed_tasks(github_app)
    # Checks pending with no word from GitHub are read afresh: a suite that never starts sends none (Dev 646).
    refresh_pending_checks(github_app)
    # A merge-queue removal that was not a merge is read afresh before its owner is woken (github.refresh_queue_removals).
    refresh_queue_removals(github_app)
    # An open pull request's conflict with a moved base sends no event: read the unknown and stale ones (github.refresh_conflicts).
    refresh_conflicts(github_app)
    # A worktree is released by its pull requests' state, so a missed webhook must heal unread.
    reconcile_worktree_prs(github_app)
    return "Done", True


def personal_alerts(ctx: Context) -> tuple[None, bool]:
    # The pass runs on its own thread and records its outcome there (backend/personal_alerts.py `run`). Not started
    # (the last pass still runs, or a demo): no words, so the step's last result stays what the last pass said.
    ctx.app.state.personal_alerts.tick()
    return None, True


def repository_waits(ctx: Context) -> tuple[str, bool]:
    github_app = ctx.app.state.github_app
    from .repositories import daily

    daily(github_app)
    # Each bot's own repository, made without anyone asking where the App may create them (backend/bot_repo_maker.py).
    from . import bot_repo_maker

    bot_repo_maker.run(github_app)
    # A task waiting for room for a worktree whose freeing event was lost (backend/worktree_waits.py).
    from . import worktree_waits

    worktree_waits.tick(ctx.store, ctx.settings.github_owner)
    # A bot left in setup for a day: its person hears once (backend/setup_stuck.py).
    from . import setup_stuck

    setup_stuck.tick(ctx.store)
    return "Done", True


def entity_links(ctx: Context) -> tuple[str | None, bool]:
    # The Directory's linker (backend/entities/linker.py) reads the texts written since its cursors and writes their
    # mentions in one short transaction of its own, so no text's write waits for it.
    from .entities import linker

    done = linker.run_pass(ctx.store, lambda: ctx.locked("entity_links"))
    if not done["texts"]:
        return None, True
    return f"Linked {done['texts']} texts", False


def call_sweeps(ctx: Context) -> tuple[str, bool]:
    # Live-meeting windows a lost routing thread (a restart) had not routed (backend/live_meetings.py), and
    # calls kept to their threads: people and bots whose access ended, and a LiveKit that restarted.
    failed = None
    for name, sweep in (
        ("live_meeting_sweep", ctx.app.state.live_meeting_routing.sweep),
        ("calls_sweep", ctx.app.state.calls.sweep),
    ):
        try:
            sweep()
        except Exception as exc:  # noqa: BLE001 - a step's own failure is captured and the beat goes on
            if ctx.telemetry is not None:
                ctx.telemetry.capture(name, exc)
            failed = failed or exc
    if failed:
        raise failed
    return "Done", True


def _engine_github(ctx: Context) -> Any:
    """The engine's GitHub (backend/github.py `AppGitHub`) through the company's GitHub App, or None without one."""
    app: Any = ctx.github_app
    if app is None or app.row() is None:
        return None
    from .github import AppGitHub

    return AppGitHub(ctx.github_app)


def item_metadata(ctx: Context) -> tuple[str, bool]:
    # The 5-minute re-read of the items open tasks wait on, GitHub's PRs and releases (backend/item_sources/github.py
    # `refresh`): a webhook GitHub never sent, or one Tico missed, still reaches the task.
    github = _engine_github(ctx)
    if github is None:
        return "No GitHub app", True
    from .item_sources import github as GS

    changed = GS.refresh(ctx.store, github)
    return (f"{changed} items changed", False) if changed else ("Nothing due", True)


def source_actions(ctx: Context) -> tuple[str, bool]:
    # The owed source actions, the Merge stage's queue and dequeue calls (backend/source_actions.py `run`).
    github = _engine_github(ctx)
    if github is None:
        return "No GitHub app", True
    from . import source_actions as SA

    done = SA.run(ctx.store, {"github": github})
    return (f"{done} merge queue calls", False) if done else ("Nothing due", True)


def jev_verdicts(ctx: Context) -> tuple[str | None, bool]:
    # Jev's verdicts on pages attached to page requirements (backend/page_verdicts.py): a few a beat, each model call
    # made with no transaction held, each verdict recorded in its own short write.
    from . import page_verdicts

    at = ctx.at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    given = page_verdicts.run(ctx.store, at)
    if not given:
        return None, True
    return f"Jev gave {given} verdicts", False


def idea_checks(ctx: Context) -> tuple[str | None, bool]:
    # Jev's duplicate check on ideas filed into Product's Ideas (backend/idea_checks.py): one call for up to 20 owed
    # ideas, made with no transaction held, the outcomes recorded in one short write.
    from . import idea_checks as IC

    at = ctx.at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    checked = IC.run(ctx.store, at)
    if not checked:
        return None, True
    return f"Jev checked {checked} ideas", False


# ----------------------------------------------------------------------------- the steps, in the order they run
MINUTE, FIVE_MINUTES, TEN_MINUTES, HOUR = (
    timedelta(minutes=1),
    timedelta(minutes=5),
    timedelta(minutes=10),
    timedelta(hours=1),
)
STEPS = [
    Step("github_sync", "GitHub sync", "poll", None, github_sync, capture="github_wakes", loop=True),
    Step("personal_alerts", "Personal alerts", "poll", None, personal_alerts, capture="scheduler", loop=True),
    Step("stall_marks", "Silent run check", "recovery", MINUTE, S.step_stall_marks, failure="stall_marks"),
    Step("turn_fixer", "Turn fixer", "recovery", None, S.step_turn_fixer, failure="turn_fixer"),
    Step(
        "run_housekeeping",
        "Run housekeeping",
        "recovery",
        None,
        S.step_run_housekeeping,
        failure="run_housekeeping",
        capture="scheduler",
    ),
    Step(
        "ship_deployed",
        "Shipped merges",
        "recovery",
        None,
        S.step_ship_deployed,
        failure="ship_deployed",
        capture="scheduler",
    ),
    Step(
        "never_stuck",
        "Never-stuck check",
        "recovery",
        None,
        S.step_never_stuck,
        failure="never_stuck",
        capture="scheduler",
    ),
    Step("deferred", "Deferred runs", "due", None, S.step_deferred, failure="deferred", capture="scheduler"),
    Step(
        "queue_check", "Queue check", "recovery", None, S.step_queue_check, failure="queue_check", capture="scheduler"
    ),
    # CI build time: the repository's recent workflow runs recorded and priced into Finance (backend/ci_runs.py).
    Step(
        "ci_sweep", "CI build time", "recovery", FIVE_MINUTES, S.step_ci_sweep, failure="ci_sweep", capture="scheduler"
    ),
    Step("updates", "Updates", "due", None, S.step_updates, failure="updates"),
    Step("company_page", "Company page editions", "due", None, S.step_company_page, failure="company_page"),
    Step("thread_digest", "Email digest", "due", HOUR, S.step_thread_digest, failure="helper", lasting=True),
    Step(
        "thread_bot_digest", "Bot thread digest", "due", HOUR, S.step_thread_bot_digest, failure="helper", lasting=True
    ),
    Step("thread_loop_guard", "Paused bot replies", "due", None, S.step_thread_loop_guard, failure="thread_loop_guard"),
    Step("go_live_expiry", "Go-live expiry", "due", TEN_MINUTES, S.step_go_live_expiry, failure="go_live"),
    Step(
        "drop_unreadable",
        "Unreadable queued work",
        "recovery",
        MINUTE,
        S.step_drop_unreadable,
        failure="drop_unreadable",
        capture="scheduler",
    ),
    Step(
        "repository_waits", "Repository and setup waits", "poll", None, repository_waits, capture="scheduler", loop=True
    ),
    Step(
        "entity_links",
        "Contacts links",
        "recovery",
        None,
        entity_links,
        failure="entity_links",
        capture="entity_links",
    ),
    # It asks LiveKit, so it is timed as the polls are: never more often than every 10 s.
    Step("call_sweeps", "Calls and live meetings", "recovery", POLL_FLOOR, call_sweeps, loop=True),
    Step(
        "item_metadata",
        "PR and release metadata",
        "poll",
        MINUTE,
        item_metadata,
        failure="item_metadata",
        capture="scheduler",
    ),
    Step("source_actions", "Merge queue", "due", None, source_actions, failure="source_actions", capture="scheduler"),
    Step("jev_verdicts", "Jev's page verdicts", "due", None, jev_verdicts, failure="jev_verdicts", capture="scheduler"),
    Step("idea_checks", "Jev's idea check", "due", None, idea_checks, failure="idea_checks", capture="scheduler"),
]
BY_KEY = {step.key: step for step in STEPS}
CONTROLLED = {step.control: step for step in STEPS if step.control}


# ----------------------------------------------------------------------------- the interval
def clamp(value: Any) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return INTERVAL_DEFAULT
    return min(max(n, INTERVAL_MIN), INTERVAL_MAX)


def interval_of(c: "sqlite3.Connection") -> int:
    row = c.execute("SELECT value FROM registry_metadata WHERE key=?", (INTERVAL_KEY,)).fetchone()
    return clamp(row[0]) if row else INTERVAL_DEFAULT


def interval(store: "Store") -> int:
    """Seconds between beats, read each pass (no restart needed); the default when it cannot be read."""
    try:
        with store.read() as c:
            return interval_of(c)
    except Exception:  # noqa: BLE001 - a busy or old database beats at the default
        return INTERVAL_DEFAULT


def save_interval(c: "sqlite3.Connection", actor: str, value: Any) -> int:
    seconds, before = clamp(value), interval_of(c)
    c.execute(
        "INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (INTERVAL_KEY, str(seconds)),
    )
    if seconds != before:
        H.event(c, actor, "heartbeat.updated", "heartbeat", {"before": before, "after": seconds})
    return seconds


# ----------------------------------------------------------------------------- one beat
def _due(
    ctx: Context,
    step: Step,
    at: datetime,
    cfgs: dict[str, dict[str, Any]] | None,
    lasts: dict[str, datetime | None] | None,
) -> bool:
    """Whether `step` runs this beat. Marked first, as the blocks were, so a failing step retries on its interval."""
    if step.control:
        # Its switch and preset, as Scheduler.due times the controlled jobs.
        return cfgs is not None and ctx.scheduler.due(step.control, at, cfgs, lasts)
    marks: dict[str, datetime]
    every, marks = step.floor, ctx.scheduler.step_marks
    if every is None:
        return True
    mark = marks.get(step.key)
    if step.lasting:
        if lasts is None:
            return False  # as the fixed-cadence jobs waited when their last runs could not be read
        mark = max([t for t in (mark, lasts.get(step.key)) if t], default=None)
    if mark is not None and at - mark < every:
        return False
    marks[step.key] = at
    return True


@dataclass
class Ran:
    key: str
    ms: int
    words: str | None
    quiet: bool
    error: str | None = None

    @property
    def did(self) -> bool:
        return self.error is None and bool(self.words) and not self.quiet


def beat(ctx: Context) -> dict[str, list[str]]:
    """Run the steps due now in order, each timed and on its own (one raising never stops the next), then store what
    the beat did. Returns {"ran", "did", "failed"} (step keys)."""
    ran: list[Ran]
    at, ran = ctx.at, []
    with SR.recording(ctx.store, "heartbeat", ctx.failures) as run:
        timed = [s.key for s in STEPS if s.lasting or s.control]
        cfgs: dict[str, dict[str, Any]] | None
        lasts: dict[str, datetime | None] | None
        try:
            with ctx.store.read() as c:
                cfgs, lasts = SR.config(c), SR.last_started(c, timed)
        except Exception as exc:  # noqa: BLE001 - the timing reads fail alone; untimed steps still run
            cfgs = lasts = None  # only those steps wait for the next beat
            ctx.failures.append({"system_routines": type(exc).__name__})
        for step in STEPS:
            if step.loop and ctx.app is None:
                continue
            if not _due(ctx, step, at, cfgs, lasts):
                continue
            begun = time.monotonic()
            words, quiet, error = None, True, None
            try:
                words, quiet = step.run(ctx) or (None, True)
            except Failed as exc:
                error = str(exc)
            except Exception as exc:  # noqa: BLE001 - one failing step must not stop the others
                error = SR.failure_words(exc)
                if step.failure:
                    ctx.failures.append({step.failure: type(exc).__name__})
                if step.capture and ctx.telemetry is not None:
                    ctx.telemetry.capture(step.capture, exc)
                LOG.error("Heartbeat step %s failed: %s", step.key, type(exc).__name__)
            ran.append(Ran(step.key, round((time.monotonic() - begun) * 1000), words, bool(quiet), error))
        record(ctx.store, at, ran, round((time.monotonic() - ctx.started) * 1000))
        failed = [r for r in ran if r.error]
        worked = [r for r in ran if r.did]
        if failed:
            run.error = "Failed: " + ", ".join(BY_KEY[r.key].name for r in failed)
        else:
            run.did(
                SR.count_words(len(worked), "step did something", "steps did something") if worked else "All quiet",
                quiet=not worked,
            )
    return {
        "ran": [r.key for r in ran],
        "did": [r.key for r in ran if r.did],
        "failed": [r.key for r in ran if r.error],
    }


def _roll(row: dict[str, Any], at: datetime, fields: tuple[str, ...], window_field: str = "window_started_at") -> None:
    """Move a row's day counts on: a window older than WINDOW becomes the previous one (or is dropped when older
    than two), and a new window starts now."""
    started: datetime | None = H.parse_ts(row.get(window_field))
    if started is not None and at - started < WINDOW:
        return
    for field in fields:
        row["previous_" + field] = row.get(field) or 0 if started is not None and at - started < 2 * WINDOW else 0
        row[field] = 0
    row[window_field] = S.stamp(at)


STEP_COUNTS = ("runs_count", "did_count", "failed_count")
STEP_COLUMNS = (
    "key",
    "last_run_at",
    "last_ms",
    "last_result",
    "last_did_at",
    "last_did",
    "last_error",
    "last_error_at",
    "window_started_at",
    *STEP_COUNTS,
    *("previous_" + f for f in STEP_COUNTS),
    "created_at",
    "updated_at",
)


class _Memory:
    """One store's step numbers not written yet. A quiet beat writes only `heartbeat_states`; each step that ran is
    noted here (its run counted, its last run, time and result) and written with the next write that happens anyway
    (a step did something or failed, its result changed, a timed step ran) or after FLUSH_EVERY. The API runs in the
    same process and shows these over the stored rows (`merged_steps`)."""

    lock: threading.Lock
    pending: dict[str, dict[str, Any]]
    stored: dict[str, str | None] | None
    flushed: datetime | None

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.pending = {}  # key -> {"runs_count", "did_count", "failed_count": n, "set": {column: value}}
        self.stored = None  # key -> last_result as written; read once
        self.flushed = None  # when the noted numbers were last written


_MEMORIES: "weakref.WeakKeyDictionary[Store, _Memory]" = weakref.WeakKeyDictionary()
_MEMORIES_LOCK = threading.Lock()


def _memory(store: "Store") -> _Memory:
    with _MEMORIES_LOCK:
        mem = _MEMORIES.get(store)
        if mem is None:
            mem = _MEMORIES[store] = _Memory()
        return mem


def _note(mem: _Memory, r: Ran, at: datetime, counted: bool = True) -> None:
    """Note one step's outcome in memory (the caller holds mem.lock). An uncounted outcome (one that lands after its
    beat, which counted the run) still moves the last run time and its time taken to when it finished."""
    now = S.stamp(at)
    p = mem.pending.setdefault(r.key, {**{f: 0 for f in STEP_COUNTS}, "set": {}})
    s = p["set"]
    if counted:
        p["runs_count"] += 1
    s["last_run_at"], s["last_ms"] = now, r.ms
    if r.error is not None or r.words is not None:
        s["last_result"] = r.error or r.words
    if r.did:
        s["last_did_at"], s["last_did"] = now, r.words
        p["did_count"] += 1
    if r.error:
        s["last_error_at"], s["last_error"] = now, r.error
        p["failed_count"] += 1


def _merged(old: dict[str, Any] | None, p: dict[str, Any] | None, at: datetime, key: str) -> dict[str, Any]:
    """A step's row with what memory noted since it was written laid over it, its day windows moved on to `at`."""
    row = (
        dict(old)
        if old
        else {
            "key": key,
            "created_at": S.stamp(at),
            **{f: 0 for f in STEP_COUNTS},
            **{"previous_" + f: 0 for f in STEP_COUNTS},
        }
    )
    _roll(row, at, STEP_COUNTS)
    if p:
        for f in STEP_COUNTS:
            row[f] = int(row.get(f) or 0) + p[f]
        row.update(p["set"])
    return row


def _write_steps(c: "sqlite3.Connection", taken: dict[str, dict[str, Any]], at: datetime) -> None:
    marks = ",".join("?" * len(taken))
    old = {r["key"]: dict(r) for r in c.execute(f"SELECT * FROM heartbeat_steps WHERE key IN ({marks})", tuple(taken))}
    for key, p in taken.items():
        row = _merged(old.get(key), p, at, key)
        row["updated_at"] = S.stamp(at)
        c.execute(
            f"INSERT INTO heartbeat_steps({','.join(STEP_COLUMNS)}) VALUES({','.join('?' * len(STEP_COLUMNS))}) "
            "ON CONFLICT(key) DO UPDATE SET "
            + ",".join(f"{col}=excluded.{col}" for col in STEP_COLUMNS[1:] if col != "created_at"),
            [row.get(col) for col in STEP_COLUMNS],
        )


def _give_back(mem: _Memory, taken: dict[str, dict[str, Any]]) -> None:
    """A write that failed: what it took goes back into memory, under anything noted since (the caller holds mem.lock)."""
    for key, p in taken.items():
        cur = mem.pending.get(key)
        if cur is None:
            mem.pending[key] = p
            continue
        for f in STEP_COUNTS:
            cur[f] += p[f]
        for col, value in p["set"].items():
            cur["set"].setdefault(col, value)


def merged_steps(c: "sqlite3.Connection", store: "Store", at: datetime | None = None) -> dict[str, dict[str, Any]]:
    """Every stored step row with what memory noted since laid over it ({key: row}), as the screen shows them."""
    at = at or datetime.now(timezone.utc)
    rows = {r["key"]: dict(r) for r in c.execute("SELECT * FROM heartbeat_steps")}
    mem = _memory(store)
    with mem.lock:
        pending = {k: {**p, "set": dict(p["set"])} for k, p in mem.pending.items()}
    return {key: _merged(rows.get(key), pending.get(key), at, key) for key in {*rows, *pending}}


def _timed(key: str) -> bool:
    """A step timed from its last recorded run (the digests): its row is written each run, hourly at most,
    so a restart keeps its hour (system_routines.last_started reads heartbeat_steps.last_run_at)."""
    step = BY_KEY.get(key)
    return bool(step and (step.lasting or step.control))


def _signature(lines: list[dict[str, Any]]) -> list[tuple[Any, Any]]:
    return sorted((line.get("key"), line.get("error")) for line in lines)


def _beat_row(c: "sqlite3.Connection", at: datetime, lines: "Sequence[Ran]", total_ms: int) -> None:
    """A working beat's row. A beat where only steps failed, with the same steps and errors as the newest row, folds
    into that row (its repeat count and last time) so one broken step does not push the useful beats out."""
    steps = [{"key": r.key, "text": r.words if r.did else None, "ms": r.ms, "error": r.error} for r in lines]
    if steps and all(r.error for r in lines):
        last = c.execute(
            "SELECT id, steps, did_count FROM heartbeat_beats ORDER BY started_at DESC, id DESC LIMIT 1"
        ).fetchone()
        try:
            same = (
                bool(last)
                and not last["did_count"]
                and _signature(json.loads(last["steps"] or "[]")) == _signature(steps)
            )
        except ValueError:
            same = False
        if same:
            c.execute(
                "UPDATE heartbeat_beats SET repeat_count=repeat_count+1, last_repeated_at=? WHERE id=?",
                (S.stamp(at), last["id"]),
            )
            return
    c.execute(
        "INSERT INTO heartbeat_beats(id,started_at,total_ms,did_count,failed_count,steps,created_at) "
        "VALUES(?,?,?,?,?,?,?)",
        (
            H.new_id(),
            S.stamp(at),
            total_ms,
            sum(r.did for r in lines),
            sum(r.error is not None for r in lines),
            json.dumps(steps),
            H.now(),
        ),
    )


def _state_write(c: "sqlite3.Connection", at: datetime, working: bool) -> None:
    now = S.stamp(at)
    state: dict[str, Any]
    state = c.execute("SELECT * FROM heartbeat_states WHERE id=1").fetchone()
    state = (
        dict(state)
        if state
        else {"id": 1, "beats_count": 0, "previous_beats_count": 0, "quiet_beats_count": 0, "created_at": now}
    )
    _roll(state, at, ("beats_count",))
    state["beats_count"] += 1
    state["last_beat_at"] = now
    if working:
        state["quiet_since_at"] = None
    else:
        state["quiet_beats_count"] += 1
        state["quiet_since_at"] = state.get("quiet_since_at") or now
    pruned: datetime | None = H.parse_ts(state.get("last_pruned_at"))
    if pruned is None or at - pruned >= PRUNE_EVERY:
        prune(c, at)
        state["last_pruned_at"] = now
    state["updated_at"] = now
    cols = (
        "id",
        "last_beat_at",
        "window_started_at",
        "beats_count",
        "previous_beats_count",
        "quiet_beats_count",
        "quiet_since_at",
        "last_pruned_at",
        "created_at",
        "updated_at",
    )
    c.execute(
        f"INSERT INTO heartbeat_states({','.join(cols)}) VALUES({','.join('?' * len(cols))}) "
        "ON CONFLICT(id) DO UPDATE SET " + ",".join(f"{col}=excluded.{col}" for col in cols[1:] if col != "created_at"),
        [state.get(col) for col in cols],
    )


def _commit(
    store: "Store",
    at: datetime,
    urgent: bool,
    lines: "Sequence[Ran]" = (),
    total_ms: int = 0,
    beat: bool = False,
) -> None:
    """One write transaction: the noted step numbers when something is written anyway (`urgent`, a working beat's row),
    a result changed or FLUSH_EVERY passed; the beat's row; and for a beat, `heartbeat_states`."""
    taken: dict[str, dict[str, Any]]
    mem, taken = _memory(store), {}
    try:
        with cast("AbstractContextManager[sqlite3.Connection]", store.transaction()) as c:
            with mem.lock:
                if mem.stored is None:
                    mem.stored = {k: v for k, v in c.execute("SELECT key, last_result FROM heartbeat_steps")}
                changed = any(
                    "last_result" in p["set"] and p["set"]["last_result"] != mem.stored.get(k)
                    for k, p in mem.pending.items()
                )
                if urgent or lines or changed or mem.flushed is None or abs(at - mem.flushed) >= FLUSH_EVERY:
                    taken, mem.pending = mem.pending, {}
            if taken:
                _write_steps(c, taken, at)
            if lines:
                _beat_row(c, at, lines, total_ms)
            if beat:
                _state_write(c, at, bool(lines))
    except BaseException:
        with mem.lock:
            _give_back(mem, taken)
        raise
    if taken:
        with mem.lock:
            mem.flushed = at
            mem.stored.update({k: p["set"]["last_result"] for k, p in taken.items() if "last_result" in p["set"]})


def record(store: "Store", at: datetime, ran: list[Ran], total_ms: int) -> None:
    """Store one beat in one small transaction: `heartbeat_states` always; the beat's row when a step did something or
    failed (else only counted); each step's row only with a write that happens anyway, a changed result or every
    FLUSH_EVERY (`_Memory`); and once an hour the beats past KEEP or KEEP_BEATS pruned."""
    mem = _memory(store)
    with mem.lock:
        for r in ran:
            _note(mem, r, at)
    working = [r for r in ran if r.did or r.error]
    _commit(store, at, any(_timed(r.key) for r in ran), working, total_ms, beat=True)


def prune(c: "sqlite3.Connection", at: datetime) -> None:
    c.execute("DELETE FROM heartbeat_beats WHERE coalesce(last_repeated_at, started_at)<?", (S.stamp(at - KEEP),))
    c.execute(
        "DELETE FROM heartbeat_beats WHERE id IN (SELECT id FROM heartbeat_beats ORDER BY started_at DESC, id DESC "
        "LIMIT -1 OFFSET ?)",
        (KEEP_BEATS,),
    )


def record_late(
    store: "Store",
    key: str,
    words: str | None,
    quiet: bool,
    error: str | None = None,
    ms: int = 0,
    counted: bool = False,
) -> None:
    """A step's outcome that lands after its beat (Personal alerts' own thread) or outside one (Run now). A quiet
    outcome is noted in memory (its last result, written with the next write) unless `counted` (a run that times the
    step's next one); one that did something or failed is written at once, with a beat row of its own. Never raises."""
    r, at = Ran(key, ms, words, bool(quiet), error), datetime.now(timezone.utc)
    mem = _memory(store)
    with mem.lock:
        _note(mem, r, at, counted=counted)
    if not (r.did or r.error or counted):
        return
    try:
        _commit(store, at, True, [r] if r.did or r.error else (), ms)
    except Exception as exc:  # noqa: BLE001 - recording never fails the work
        LOG.warning("Could not record the %s step: %s", key, type(exc).__name__)


# ----------------------------------------------------------------------------- liveness
def liveness(c: "sqlite3.Connection", now: datetime | None = None) -> tuple[str | None, float | None, bool]:
    """(last stamp, age in seconds, alive) of the server's loop: the fresher of the scheduler's pass
    (service_health 'scheduler', written by the rest of the pass, at most every 10 s) and the last beat
    (heartbeat_states, every beat). Alive while that is under max(ALIVE_MIN, interval + ALIVE_SLACK) seconds old, so a
    long interval does not read as stopped between beats."""
    now = now or datetime.now(timezone.utc)
    # One statement whatever is there, so a status page reads a fixed number of them (test_status_reads.py).
    tick, beat, saved = c.execute(
        "SELECT (SELECT last_succeeded_at FROM service_health WHERE service='scheduler'),"
        " (SELECT last_beat_at FROM heartbeat_states WHERE id=1),"
        " (SELECT value FROM registry_metadata WHERE key=?)",
        (INTERVAL_KEY,),
    ).fetchone()
    parsed: list[tuple[datetime, str]] = [(H.parse_ts(s), s) for s in (tick, beat) if s and H.parse_ts(s)]
    if not parsed:
        return None, None, False
    last, stamp = max(parsed)
    age = max(0.0, (now - last).total_seconds())
    try:
        seconds = clamp(saved) if saved is not None else INTERVAL_DEFAULT
    except Exception:  # noqa: BLE001 - an unreadable setting beats at the default
        seconds = INTERVAL_DEFAULT
    return stamp, age, age < max(ALIVE_MIN, seconds + ALIVE_SLACK)


class Rest:
    """When the rest of the server's pass runs (at most every REST_FLOOR seconds, however short the beat), and the
    failures of the beats whose pass it skipped, carried into the next one so they still reach service_health."""

    floor: float
    clock: "Callable[[], float]"
    at: float | None
    carried: list[dict[str, str]]

    def __init__(self, floor: float = REST_FLOOR, clock: "Callable[[], float]" = time.monotonic) -> None:
        self.floor, self.clock, self.at, self.carried = floor, clock, None, []

    def due(self, ctx: Context) -> bool:
        if self.at is not None and self.clock() - self.at < self.floor:
            self.carried = (self.carried + list(ctx.failures))[-CARRY_MAX:]
            return False
        self.at = self.clock()
        ctx.failures[:0] = self.carried
        self.carried = []
        return True


def run_now(store: "Store", auth: "Auth", key: str) -> None:
    """Run now (Settings > Routines) of a step a person controls: its block once, its outcome on its row. A failure is
    recorded, not raised."""
    step = CONTROLLED[key]
    from .scheduler import Scheduler

    class _Execution:  # a step reads only `auth` from the execution
        auth: "Auth"
        pass

    execution = _Execution()
    execution.auth = auth
    ctx = Context(Scheduler(store, execution))
    begun = time.monotonic()
    words, quiet, error = None, True, None
    try:
        words, quiet = step.run(ctx)
    except Failed as exc:
        error = str(exc)
    except Exception as exc:  # noqa: BLE001 - Run now records the failure on the step row
        error = SR.failure_words(exc)
    record_late(store, step.key, words, quiet, error, round((time.monotonic() - begun) * 1000), counted=True)


# ----------------------------------------------------------------------------- the read
def _day(row: dict[str, Any], at: datetime, field: str) -> int:
    """A count over about the last day: this window and, while it is under a day old, the one before."""
    started: datetime | None = H.parse_ts(row.get("window_started_at"))
    if started is None or at - started >= 2 * WINDOW:
        return 0
    if at - started >= WINDOW:
        return int(row.get(field) or 0)
    return int(row.get(field) or 0) + int(row.get("previous_" + field) or 0)


def every_words(step: Step, cfg: dict[str, Any] | None = None) -> str:
    if step.control and cfg is not None:
        return SR.schedule_words(step.control, cfg) if cfg.get("on") else "Off"
    every = step.floor
    if every is None:
        return "Every beat"
    seconds = int(every.total_seconds())
    if seconds % 3600 == 0:
        return "Hourly" if seconds == 3600 else f"Every {seconds // 3600} hours"
    if seconds % 60 == 0:
        return "Every minute" if seconds == 60 else f"Every {seconds // 60} minutes"
    return f"Every {seconds} s"


def _beat_view(row: dict[str, Any]) -> dict[str, Any]:
    steps: list[dict[str, Any]]
    try:
        steps = json.loads(row["steps"] or "[]")
    except ValueError:
        steps = []
    for line in steps:
        line["name"] = BY_KEY[line["key"]].name if line.get("key") in BY_KEY else line.get("key")
    return {
        "id": row["id"],
        "started_at": row["started_at"],
        "total_ms": row["total_ms"],
        "did": row["did_count"],
        "failed": row["failed_count"],
        "steps": steps,
        "repeats": int(row["repeat_count"] or 1),
        "last_repeated_at": row["last_repeated_at"],
    }


def beats(
    c: "sqlite3.Connection", limit: int = PAGE, before: str | None = None
) -> tuple[list[dict[str, Any]], str | None]:
    """Beats that did something or failed, newest first; `next` is the `before` of the page after."""
    limit = max(1, min(int(limit or PAGE), 100))
    at = None
    if before:
        started, sep, rid = str(before).rpartition("|")
        if not sep or not started or not rid:
            raise Problem("invalid", "before is the `next` of the page before", 422)
        at = (started, rid)
    rows = c.execute(
        "SELECT * FROM heartbeat_beats"
        + (" WHERE started_at<? OR (started_at=? AND id<?)" if at else "")
        + " ORDER BY started_at DESC, id DESC LIMIT ?",
        (*((at[0], at[0], at[1]) if at else ()), limit + 1),
    ).fetchall()
    shown = rows[:limit]
    nxt = f"{shown[-1]['started_at']}|{shown[-1]['id']}" if len(rows) > limit and shown else None
    return [_beat_view(r) for r in shown], nxt


def _step_view(
    step: Step,
    row: dict[str, Any],
    cfg: dict[str, Any] | None,
    at: datetime,
    auth: "Auth | None",
    who: "Identity | None",
) -> dict[str, Any]:
    return {
        "key": step.key,
        "name": step.name,
        "kind": step.kind,
        "kind_name": KIND_WORDS[step.kind],
        "every_s": int(step.floor.total_seconds()) if step.floor else None,
        "every": every_words(step, cfg),
        "switch": bool(step.control),
        "on": cfg["on"] if cfg else True,
        "can_change": bool(step.control and who is not None and SR.can_change(auth, who, step.control)),
        "last_run_at": row.get("last_run_at"),
        "last_ms": row.get("last_ms"),
        "last_result": row.get("last_result"),
        "last_did_at": row.get("last_did_at"),
        "last_did": row.get("last_did"),
        "last_error": row.get("last_error"),
        "last_error_at": row.get("last_error_at"),
        # Its latest run failed: the error is current, not one from before a run that went well.
        "failing": bool(row.get("last_error")) and (row.get("last_error_at") or "") >= (row.get("last_run_at") or ""),
        "runs_24h": _day(row, at, "runs_count"),
        "did_24h": _day(row, at, "did_count"),
        "failed_24h": _day(row, at, "failed_count"),
    }


def step_row(c: "sqlite3.Connection", key: str, auth: "Auth | None", who: "Identity | None") -> dict[str, Any]:
    """One step's row as GET /api/v2/heartbeat lists it (the answer to its switch, PUT /api/v2/system-routines/{key})."""
    step = CONTROLLED.get(key) or BY_KEY[key]
    row = c.execute("SELECT * FROM heartbeat_steps WHERE key=?", (step.key,)).fetchone()
    cfg: dict[str, Any] | None = SR.config(c).get(step.control) if step.control else None
    return _step_view(step, dict(row) if row else {}, cfg, datetime.now(timezone.utc), auth, who)


def listing(
    c: "sqlite3.Connection",
    auth: "Auth",
    who: "Identity",
    limit: int = PAGE,
    before: str | None = None,
    store: "Store | None" = None,
) -> dict[str, Any]:
    """What GET /api/v2/heartbeat returns: the interval, the last beat and the day's count, each step in order with its
    kind, cadence, switch and numbers (with `store`, what this process noted since the rows were written laid over
    them), and a page of the beats that did something."""
    at = datetime.now(timezone.utc)
    rows: dict[str, dict[str, Any]] = (
        merged_steps(c, store, at)
        if store is not None
        else {r["key"]: dict(r) for r in c.execute("SELECT * FROM heartbeat_steps")}
    )
    state: dict[str, Any]
    state = c.execute("SELECT * FROM heartbeat_states WHERE id=1").fetchone()
    state = dict(state) if state else {}
    cfgs: dict[str, dict[str, Any]] = SR.config(c)
    steps = [
        _step_view(step, rows.get(step.key, {}), cfgs.get(step.control) if step.control else None, at, auth, who)
        for step in STEPS
    ]
    page, nxt = beats(c, limit, before)
    return {
        "interval_s": interval_of(c),
        "interval_min": INTERVAL_MIN,
        "interval_max": INTERVAL_MAX,
        "last_beat_at": state.get("last_beat_at"),
        "beats_24h": _day(state, at, "beats_count"),
        "quiet_beats": int(state.get("quiet_beats_count") or 0),
        "quiet_since_at": state.get("quiet_since_at"),
        "kinds": [{"key": k, "name": n} for k, n in KINDS],
        "steps": steps,
        "beats": page,
        "next": nxt,
        "can_change": can_change(auth, who),
    }


def may_change(auth: "Auth", who: "Identity") -> None:
    """An owner or an admin changes the interval, as Maintenance's settings (backend/maintenance.py). Raises a 403."""
    auth.domain(who)
    if getattr(who, "support", None) == "app" or not auth.bot_admin(who):
        raise Problem("forbidden", "Only an owner or an admin changes the Heartbeat", 403)


def can_change(auth: "Auth", who: "Identity") -> bool:
    try:
        may_change(auth, who)
        return True
    except Problem:
        return False


def install(app: "FastAPI", store: "Store", auth: "Auth", mutate: "Callable[..., Any]") -> None:
    from fastapi import Request

    from . import models as M

    @app.get("/api/v2/heartbeat")
    def heartbeat(request: Request, limit: int = PAGE, before: str | None = None):
        """The Heartbeat: its interval, the last beat, beats in the last day and quiet beats, each step in order with
        its kind, cadence, switch and numbers, and the beats that did something, newest first (`next` is `before` for
        the page after). Any signed-in person reads it."""
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            return listing(c, auth, who, limit, before, store=store)

    @app.put("/api/v2/heartbeat")
    def heartbeat_save(request: Request, body: M.HeartbeatUpdate):
        """Set the seconds between beats (5 to 60). An owner or an admin."""
        who = request.state.identity
        may_change(auth, who)

        def work(c: "sqlite3.Connection") -> None:
            save_interval(c, who.actor, body.interval_s)

        mutate(request, body, work)
        with store.read() as c:
            return listing(c, auth, who, store=store)
