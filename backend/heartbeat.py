"""The Heartbeat (docs/heartbeat.md): one short beat, every 10 s by default, that runs in order the steps that must happen
within seconds but have no push event to start them:
  - due: time passing (a due date, a snooze's end, an hour gone by);
  - recovery: crashes nothing announces (a dead turn, a silent run, a lost wake);
  - poll: outside systems that do not push (GitHub, HQ paging).

Each step is a block that ran in the server's 10-second loop (backend/app.py `schedule_loop`, backend/scheduler.py
`Scheduler.tick`) and recorded its own System job row. Phase 1 moved them here as they were: same code, cadence, gates
and order. A step's `run(ctx)` returns (words, quiet); raising is its failure, kept to that step: the others still run,
and the failure goes where it went before (the pass's `failures`, `telemetry.capture`).

What a beat did is stored small (migration 20261106094503_heartbeat): one `heartbeat_steps` row per step, written in the
beat's one transaction; a `heartbeat_beats` row only for a beat where a step did something or failed; quiet beats
counted on `heartbeat_states`. The Heartbeat is one System job (system_routines.JOBS `heartbeat`): one run per beat,
quiet repeats folded. Its screen is ui/app/automations-heartbeat.js (GET/PUT /api/v2/heartbeat)."""
import json
import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from . import scheduler as S
from . import system_routines as SR
from .store import H, Problem

LOG = logging.getLogger("tico.heartbeat")

INTERVAL_KEY = "heartbeat:interval_s"       # registry_metadata
INTERVAL_DEFAULT, INTERVAL_MIN, INTERVAL_MAX = 10, 5, 60
POLL_FLOOR = timedelta(seconds=10)          # a shorter beat never polls an outside system more often than before
REST_FLOOR = 10.0                           # seconds: the rest of the loop, likewise (backend/app.py)
WINDOW = timedelta(hours=12)                # the day counts: this window and the one before it
KEEP = timedelta(days=7)                    # working beats kept this long...
KEEP_BEATS = 5000                           # ...and at most this many
PRUNE_EVERY = timedelta(hours=1)
PAGE = 20
KINDS = (("due", "Due times"), ("recovery", "Recovery"), ("poll", "Outside polls"))
KIND_WORDS = dict(KINDS)


class Failed(Exception):
    """A step's failure in its own words (a block that set `run.error` without raising): recorded, not captured."""


@dataclass(frozen=True)
class Step:
    key: str                    # its System job key before the Heartbeat (system_routines history keeps the old rows)
    name: str
    kind: str                   # "due", "recovery" or "poll"
    every: timedelta | None     # None: every beat; else at most this often (today's cadence)
    run: object                 # run(ctx) -> (words, quiet); words None: the step records its own outcome later
    failure: str | None = None  # the {name: kind} entry it adds to the pass's failures when it raises, as before
    capture: str | None = None  # the telemetry name a raise went to before; None: it never went, or captures its own
    lasting: bool = False       # timed from its last recorded start, so a restart keeps its hour
    control: str | None = None  # its switch and preset (system_routines.CONTROLS)
    loop: bool = False          # one of the server loop's own (needs the app): never in a bare Scheduler.tick

    @property
    def floor(self):
        """How often it may run at most: `every`, and a poll never more often than POLL_FLOOR."""
        if self.kind == "poll":
            return max(self.every or POLL_FLOOR, POLL_FLOOR)
        return self.every


class Context:
    """One pass of the server's loop: what a step reads, the pass's failures and write-lock holds (the scheduler's
    service_health row reports them, the rest of Scheduler.tick included), and `locked`, one short write per step."""

    def __init__(self, scheduler, at=None, app=None, telemetry=None):
        self.scheduler = scheduler
        self.store, self.execution, self.github_app = scheduler.store, scheduler.execution, scheduler.github_app
        self.app, self.telemetry = app, telemetry
        self.at = at or datetime.now(timezone.utc)
        self.failures, self.holds = [], []
        self.started = time.monotonic()

    @property
    def settings(self):
        return self.store.settings

    @property
    def auth(self):
        return self.execution.auth

    @contextmanager
    def locked(self, step):
        # One short write transaction per step and per reminder batch. The pass used to be one transaction, and on
        # a hub with many tasks every request waited 20-30 s behind it (TK-RCZJXEWA). Each step is whole on its
        # own; reminders keep their dedupe rows (task_reminders), checked under the same lock that writes them.
        # The hold is timed from BEGIN to commit; the wait for the lock is kept apart, since it measures other
        # writers, not this pass.
        asked, begun = time.monotonic(), None
        try:
            with self.store.transaction() as c:
                begun = time.monotonic()
                yield c
        finally:
            if begun is not None:
                self.holds.append((time.monotonic() - begun, begun - asked, step))


# ----------------------------------------------------------------------------- the server loop's own steps
# These ran beside Scheduler.tick in backend/app.py `schedule_loop`; moved here as they were.
def github_sync(ctx):
    github_app = ctx.app.state.github_app
    from .github import flush_wakes, reconcile_worktree_prs, refresh_deployed_tasks, refresh_pending_checks
    with ctx.store.transaction() as c:
        flush_wakes(c)
    refresh_deployed_tasks(github_app)
    # Checks pending with no word from GitHub are read afresh: a suite that never starts sends none (Dev 646).
    refresh_pending_checks(github_app)
    # A worktree is released by its pull requests' state, so a missed webhook must heal unread.
    reconcile_worktree_prs(github_app)
    # A merge or release a task waits for whose webhook never came is asked of GitHub (Dev 256).
    from . import milestone_waits
    milestone_waits.recover(github_app)
    # A review move refused for pending checks or a missing receipt, retried once that clears (Dev 534 C).
    from . import move_retry
    move_retry.tick(github_app)
    # A gate hold whose current head has every hosted check passing clears on a fresh GitHub read.
    from . import task_blocked
    task_blocked.fresh(github_app)
    return "Done", True


def personal_alerts(ctx):
    # The pass runs on its own thread and records its outcome there (backend/personal_alerts.py `run`).
    started = ctx.app.state.personal_alerts.tick()
    return (None if started else "Nothing to send"), True


def repository_waits(ctx):
    github_app = ctx.app.state.github_app
    from .repositories import daily
    daily(github_app)
    # A bot waiting for its GitHub repository finishes once a person has made it or granted the permission.
    from . import repo_waits
    repo_waits.tick(github_app)
    # A task waiting for room for a worktree whose freeing event was lost (backend/worktree_waits.py).
    from . import worktree_waits
    worktree_waits.tick(ctx.store, ctx.settings.github_owner)
    # A bot left in setup for a day: its person hears once (backend/setup_stuck.py).
    from . import setup_stuck
    setup_stuck.tick(ctx.store)
    return "Done", True


def call_sweeps(ctx):
    # Live-meeting windows a lost routing thread (a restart) had not routed (backend/live_meetings.py), and
    # calls kept to their threads: people and bots whose access ended, and a LiveKit that restarted.
    failed = None
    for name, sweep in (("live_meeting_sweep", ctx.app.state.live_meeting_routing.sweep),
                        ("calls_sweep", ctx.app.state.calls.sweep)):
        try:
            sweep()
        except Exception as exc:  # noqa: BLE001 - a step's own failure is captured and the beat goes on
            if ctx.telemetry is not None:
                ctx.telemetry.capture(name, exc)
            failed = failed or exc
    if failed:
        raise failed
    return "Done", True


# ----------------------------------------------------------------------------- the steps, in the order they run
MINUTE, TEN_MINUTES, HOUR = timedelta(minutes=1), timedelta(minutes=10), timedelta(hours=1)
STEPS = [
    Step("github_sync", "GitHub sync", "poll", None, github_sync, capture="github_wakes", loop=True),
    Step("personal_alerts", "Personal alerts", "poll", None, personal_alerts, capture="scheduler", loop=True),
    Step("stall_marks", "Silent run check", "recovery", MINUTE, S.step_stall_marks, failure="stall_marks"),
    Step("turn_fixer", "Turn fixer", "recovery", None, S.step_turn_fixer, failure="turn_fixer"),
    Step("run_housekeeping", "Run housekeeping", "recovery", None, S.step_run_housekeeping,
         failure="run_housekeeping", capture="scheduler"),
    Step("ship_deployed", "Shipped merges", "recovery", None, S.step_ship_deployed,
         failure="ship_deployed", capture="scheduler"),
    Step("task_loops", "Task loop alerts", "recovery", None, S.step_task_loops, failure="task_loops", capture="scheduler"),
    Step("snooze_ends", "Snooze ends", "due", None, S.step_snooze_ends, failure="snooze_ends", capture="scheduler"),
    Step("queue_check", "Queue check", "recovery", None, S.step_queue_check, failure="queue_check", capture="scheduler"),
    Step("milestone_waits", "Merge and release waits", "recovery", None, S.step_milestone_waits,
         failure="milestone_waits", capture="scheduler"),
    Step("due_reminders", "Due reminders", "due", None, S.step_due_reminders,
         failure="due_reminders", capture="scheduler"),
    Step("updates", "Updates", "due", None, S.step_updates, failure="updates"),
    Step("company_page", "Company page editions", "due", None, S.step_company_page, failure="company_page"),
    Step("ask_deadlines", "Ask go-ahead", "due", HOUR, S.step_ask_deadlines, failure="ask_deadlines",
         lasting=True, control="ask_deadlines"),
    Step("thread_digest", "Email digest", "due", HOUR, S.step_thread_digest, failure="helper", lasting=True),
    Step("thread_bot_digest", "Bot thread digest", "due", HOUR, S.step_thread_bot_digest, failure="helper", lasting=True),
    Step("thread_loop_guard", "Paused bot replies", "due", None, S.step_thread_loop_guard, failure="thread_loop_guard"),
    Step("go_live_expiry", "Go-live expiry", "due", TEN_MINUTES, S.step_go_live_expiry, failure="go_live"),
    Step("stall_checks", "Stall checks", "recovery", MINUTE, S.step_stall_checks,
         failure="stall_checks", capture="scheduler"),
    Step("approval_expiry", "Approval expiry", "due", HOUR, S.step_approval_expiry, failure="approval_expiry"),
    Step("repository_waits", "Repository and setup waits", "poll", None, repository_waits, capture="scheduler", loop=True),
    Step("call_sweeps", "Calls and live meetings", "recovery", None, call_sweeps, loop=True),
]
BY_KEY = {step.key: step for step in STEPS}
CONTROLLED = {step.control: step for step in STEPS if step.control}


# ----------------------------------------------------------------------------- the interval
def clamp(value):
    try:
        n = int(value)
    except (TypeError, ValueError):
        return INTERVAL_DEFAULT
    return min(max(n, INTERVAL_MIN), INTERVAL_MAX)


def interval_of(c):
    row = c.execute("SELECT value FROM registry_metadata WHERE key=?", (INTERVAL_KEY,)).fetchone()
    return clamp(row[0]) if row else INTERVAL_DEFAULT


def interval(store):
    """Seconds between beats, read each pass (no restart needed); the default when it cannot be read."""
    try:
        with store.read() as c:
            return interval_of(c)
    except Exception:  # noqa: BLE001 - a busy or old database beats at the default
        return INTERVAL_DEFAULT


def save_interval(c, actor, value):
    seconds, before = clamp(value), interval_of(c)
    c.execute("INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
              (INTERVAL_KEY, str(seconds)))
    if seconds != before:
        H.event(c, actor, "heartbeat.updated", "heartbeat", {"before": before, "after": seconds})
    return seconds


# ----------------------------------------------------------------------------- one beat
def _due(ctx, step, at, cfgs, lasts):
    """Whether `step` runs this beat. Marked first, as the blocks were, so a failing step retries on its interval."""
    if step.control:
        # Its switch and preset, as Scheduler.due times the controlled jobs.
        return cfgs is not None and ctx.scheduler.due(step.control, at, cfgs, lasts)
    every, marks = step.floor, ctx.scheduler.step_marks
    if every is None:
        return True
    mark = marks.get(step.key)
    if step.lasting:
        if lasts is None:
            return False                       # as the fixed-cadence jobs waited when their last runs could not be read
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
    def did(self):
        return self.error is None and bool(self.words) and not self.quiet


def beat(ctx):
    """Run the steps due now in order, each timed and on its own (one raising never stops the next), then store what
    the beat did. Returns {"ran", "did", "failed"} (step keys)."""
    at, ran = ctx.at, []
    with SR.recording(ctx.store, "heartbeat", ctx.failures) as run:
        timed = [s.key for s in STEPS if s.lasting or s.control]
        try:
            with ctx.store.read() as c:
                cfgs, lasts = SR.config(c), SR.last_started(c, timed)
        except Exception as exc:  # noqa: BLE001 - the timing reads fail alone; untimed steps still run
            cfgs = lasts = None                # only those steps wait for the next beat
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
            run.did(SR.count_words(len(worked), "step did something", "steps did something") if worked else "All quiet",
                    quiet=not worked)
    return {"ran": [r.key for r in ran], "did": [r.key for r in ran if r.did], "failed": [r.key for r in ran if r.error]}


def _roll(row, at, fields, window_field="window_started_at"):
    """Move a row's day counts on: a window older than WINDOW becomes the previous one (or is dropped when older
    than two), and a new window starts now."""
    started = H.parse_ts(row.get(window_field))
    if started is not None and at - started < WINDOW:
        return
    for field in fields:
        row["previous_" + field] = row.get(field) or 0 if started is not None and at - started < 2 * WINDOW else 0
        row[field] = 0
    row[window_field] = S.stamp(at)


STEP_COUNTS = ("runs_count", "did_count", "failed_count")
STEP_COLUMNS = ("key", "last_run_at", "last_ms", "last_result", "last_did_at", "last_did", "last_error", "last_error_at",
                "window_started_at", *STEP_COUNTS, *("previous_" + f for f in STEP_COUNTS), "created_at", "updated_at")


def _step_write(c, old, r, at, counted=True):
    now = S.stamp(at)
    row = dict(old) if old else {"key": r.key, "created_at": now, **{f: 0 for f in STEP_COUNTS},
                                 **{"previous_" + f: 0 for f in STEP_COUNTS}}
    _roll(row, at, STEP_COUNTS)
    if counted:
        row["runs_count"] += 1
        row["last_run_at"], row["last_ms"] = now, r.ms
    if r.error is not None or r.words is not None:
        row["last_result"] = r.error or r.words
    if r.did:
        row["last_did_at"], row["last_did"] = now, r.words
        row["did_count"] += 1
    if r.error:
        row["last_error_at"], row["last_error"] = now, r.error
        row["failed_count"] += 1
    row["updated_at"] = now
    values = [row.get(col) for col in STEP_COLUMNS]
    c.execute(f"INSERT INTO heartbeat_steps({','.join(STEP_COLUMNS)}) VALUES({','.join('?' * len(STEP_COLUMNS))}) "
              "ON CONFLICT(key) DO UPDATE SET " + ",".join(f"{col}=excluded.{col}" for col in STEP_COLUMNS[1:]
                                                           if col != "created_at"), values)


def _beat_row(c, at, lines, total_ms):
    steps = [{"key": r.key, "text": r.words if r.did else None, "ms": r.ms, "error": r.error} for r in lines]
    c.execute("INSERT INTO heartbeat_beats(id,started_at,total_ms,did_count,failed_count,steps,created_at) "
              "VALUES(?,?,?,?,?,?,?)", (H.new_id(), S.stamp(at), total_ms, sum(r.did for r in lines),
                                        sum(r.error is not None for r in lines), json.dumps(steps), H.now()))


def record(store, at, ran, total_ms):
    """Store one beat in one transaction: each step that ran on its row, the beat's row when a step did something or
    failed (else only counted), and once an hour the beats past KEEP or KEEP_BEATS pruned."""
    now = S.stamp(at)
    working = [r for r in ran if r.did or r.error]
    with store.transaction() as c:
        rows = {row["key"]: dict(row) for row in c.execute("SELECT * FROM heartbeat_steps")}
        for r in ran:
            _step_write(c, rows.get(r.key), r, at)
        if working:
            _beat_row(c, at, working, total_ms)
        state = c.execute("SELECT * FROM heartbeat_states WHERE id=1").fetchone()
        state = dict(state) if state else {"id": 1, "beats_count": 0, "previous_beats_count": 0, "quiet_beats_count": 0,
                                           "created_at": now}
        _roll(state, at, ("beats_count",))
        state["beats_count"] += 1
        state["last_beat_at"] = now
        if working:
            state["quiet_since_at"] = None
        else:
            state["quiet_beats_count"] += 1
            state["quiet_since_at"] = state.get("quiet_since_at") or now
        pruned = H.parse_ts(state.get("last_pruned_at"))
        if pruned is None or at - pruned >= PRUNE_EVERY:
            prune(c, at)
            state["last_pruned_at"] = now
        state["updated_at"] = now
        cols = ("id", "last_beat_at", "window_started_at", "beats_count", "previous_beats_count", "quiet_beats_count",
                "quiet_since_at", "last_pruned_at", "created_at", "updated_at")
        c.execute(f"INSERT INTO heartbeat_states({','.join(cols)}) VALUES({','.join('?' * len(cols))}) "
                  "ON CONFLICT(id) DO UPDATE SET " + ",".join(f"{col}=excluded.{col}" for col in cols[1:] if col != "created_at"),
                  [state.get(col) for col in cols])


def prune(c, at):
    c.execute("DELETE FROM heartbeat_beats WHERE started_at<?", (S.stamp(at - KEEP),))
    c.execute("DELETE FROM heartbeat_beats WHERE id IN (SELECT id FROM heartbeat_beats ORDER BY started_at DESC, id DESC "
              "LIMIT -1 OFFSET ?)", (KEEP_BEATS,))


def record_late(store, key, words, quiet, error=None, ms=0, counted=False):
    """A step's outcome that lands after its beat (Personal alerts' own thread) or outside one (Run now): its row, and
    a beat row of its own when it did something or failed. A quiet outcome writes nothing unless `counted` (a run that
    times the step's next one). Never raises."""
    r = Ran(key, ms, words, bool(quiet), error)
    if not (r.did or r.error or counted):
        return
    at = datetime.now(timezone.utc)
    try:
        with store.transaction() as c:
            old = c.execute("SELECT * FROM heartbeat_steps WHERE key=?", (key,)).fetchone()
            _step_write(c, dict(old) if old else None, r, at, counted=counted)
            if r.did or r.error:
                _beat_row(c, at, [r], ms)
    except Exception as exc:  # noqa: BLE001 - recording never fails the work
        LOG.warning("Could not record the %s step: %s", key, type(exc).__name__)


def run_now(store, auth, key):
    """Run now (Settings > Routines) of a step a person controls: its block once, its outcome on its row. A failure is
    recorded, not raised."""
    step = CONTROLLED[key]
    from .scheduler import Scheduler

    class _Execution:                          # a step reads only `auth` from the execution
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
def _day(row, at, field):
    """A count over about the last day: this window and, while it is under a day old, the one before."""
    started = H.parse_ts(row.get("window_started_at"))
    if started is None or at - started >= 2 * WINDOW:
        return 0
    if at - started >= WINDOW:
        return int(row.get(field) or 0)
    return int(row.get(field) or 0) + int(row.get("previous_" + field) or 0)


def every_words(step, cfg=None):
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


def _beat_view(row):
    try:
        steps = json.loads(row["steps"] or "[]")
    except ValueError:
        steps = []
    for line in steps:
        line["name"] = BY_KEY[line["key"]].name if line.get("key") in BY_KEY else line.get("key")
    return {"id": row["id"], "started_at": row["started_at"], "total_ms": row["total_ms"], "did": row["did_count"],
            "failed": row["failed_count"], "steps": steps}


def beats(c, limit=PAGE, before=None):
    """Beats that did something or failed, newest first; `next` is the `before` of the page after."""
    limit = max(1, min(int(limit or PAGE), 100))
    at = None
    if before:
        started, sep, rid = str(before).rpartition("|")
        if not sep or not started or not rid:
            raise Problem("invalid", "before is the `next` of the page before", 422)
        at = (started, rid)
    rows = c.execute("SELECT * FROM heartbeat_beats" + (" WHERE started_at<? OR (started_at=? AND id<?)" if at else "")
                     + " ORDER BY started_at DESC, id DESC LIMIT ?",
                     (*((at[0], at[0], at[1]) if at else ()), limit + 1)).fetchall()
    shown = rows[:limit]
    nxt = f"{shown[-1]['started_at']}|{shown[-1]['id']}" if len(rows) > limit and shown else None
    return [_beat_view(r) for r in shown], nxt


def _step_view(step, row, cfg, at, auth, who):
    return {"key": step.key, "name": step.name, "kind": step.kind, "kind_name": KIND_WORDS[step.kind],
            "every_s": int(step.floor.total_seconds()) if step.floor else None, "every": every_words(step, cfg),
            "switch": bool(step.control), "on": cfg["on"] if cfg else True,
            "can_change": bool(step.control and who is not None and SR.can_change(auth, who, step.control)),
            "last_run_at": row.get("last_run_at"), "last_ms": row.get("last_ms"), "last_result": row.get("last_result"),
            "last_did_at": row.get("last_did_at"), "last_did": row.get("last_did"),
            "last_error": row.get("last_error"), "last_error_at": row.get("last_error_at"),
            "runs_24h": _day(row, at, "runs_count"), "did_24h": _day(row, at, "did_count"),
            "failed_24h": _day(row, at, "failed_count")}


def step_row(c, key, auth, who):
    """One step's row as GET /api/v2/heartbeat lists it (the answer to its switch, PUT /api/v2/system-routines/{key})."""
    step = CONTROLLED.get(key) or BY_KEY[key]
    row = c.execute("SELECT * FROM heartbeat_steps WHERE key=?", (step.key,)).fetchone()
    cfg = SR.config(c).get(step.control) if step.control else None
    return _step_view(step, dict(row) if row else {}, cfg, datetime.now(timezone.utc), auth, who)


def listing(c, auth, who, limit=PAGE, before=None):
    """What GET /api/v2/heartbeat returns: the interval, the last beat and the day's count, each step in order with its
    kind, cadence, switch and numbers, and a page of the beats that did something."""
    at = datetime.now(timezone.utc)
    rows = {r["key"]: dict(r) for r in c.execute("SELECT * FROM heartbeat_steps")}
    state = c.execute("SELECT * FROM heartbeat_states WHERE id=1").fetchone()
    state = dict(state) if state else {}
    cfgs = SR.config(c)
    steps = [_step_view(step, rows.get(step.key, {}), cfgs.get(step.control) if step.control else None, at, auth, who)
             for step in STEPS]
    page, nxt = beats(c, limit, before)
    return {"interval_s": interval_of(c), "interval_min": INTERVAL_MIN, "interval_max": INTERVAL_MAX,
            "last_beat_at": state.get("last_beat_at"), "beats_24h": _day(state, at, "beats_count"),
            "quiet_beats": int(state.get("quiet_beats_count") or 0), "quiet_since_at": state.get("quiet_since_at"),
            "kinds": [{"key": k, "name": n} for k, n in KINDS], "steps": steps, "beats": page, "next": nxt,
            "can_change": can_change(auth, who)}


def may_change(auth, who):
    """An owner or an admin changes the interval, as Maintenance's settings (backend/maintenance.py). Raises a 403."""
    auth.domain(who)
    if getattr(who, "support", None) == "app" or not auth.bot_admin(who):
        raise Problem("forbidden", "Only an owner or an admin changes the Heartbeat", 403)


def can_change(auth, who):
    try:
        may_change(auth, who)
        return True
    except Problem:
        return False


def install(app, store, auth, mutate):
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
            return listing(c, auth, who, limit, before)

    @app.put("/api/v2/heartbeat")
    def heartbeat_save(request: Request, body: M.HeartbeatUpdate):
        """Set the seconds between beats (5 to 60). An owner or an admin."""
        who = request.state.identity
        may_change(auth, who)

        def work(c):
            save_interval(c, who.actor, body.interval_s)
        mutate(request, body, work)
        with store.read() as c:
            return listing(c, auth, who)
