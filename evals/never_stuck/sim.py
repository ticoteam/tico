"""The fault rig's moving parts: a fake computer that runs the bots' wakes, fake bots and fake people, the faults, the
checks after every step, and one seeded run (`run_seed`).

A step: the clock moves on, the computer ends, renews or cuts off its runs and takes the wakes queued for bots that can
run, the people act on their Needs you, the faults strike, and one pass of the real never-stuck check runs. Then the
seven rules are checked (`never_stuck.rules_hold`), every open task must wait on something, and no task may have used
more than 7 bot runs without a change before a person was asked.

At the end the faults stop and the bots only stop early. The clock runs on until nothing is due, and every task must be
Done, Closed, Snoozed, in a by-hand stage or a person's turn, or wait only on room or on tasks whose own chain ends in
one of those."""

import random
import sqlite3
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from . import world as Wd
from .world import ANA, BEN, BOTS, CARA, MANAGER, PEOPLE, REVIEWER, WRITER, Crash, Refusal, World

# What a fake bot does in one run.
FINISH, STOP, COMMENT, MENTION, CRASH, ASK, SNOOZE_UNTIL, SNOOZE_ICE, FILE, HANG = BEHAVIOURS = (
    "finish",
    "stop early",
    "comment and stop",
    "mention and stop",
    "crash",
    "ask a person",
    "snooze until",
    "snooze, no end",
    "file a sub-task",
    "never answer",
)
# What the manager bot may also do with a task it was handed.
REASSIGN, CLOSE, BACK = "reassign", "close", "move back"
# What a fake person does with an item in their Needs you.
SILENT = "stay silent"
LEASE_S = 90  # a run's lease, renewed while its computer checks in
RUN_CAP_S = 3600  # a run is cut off at 60 minutes
MAX_RUNS = 7  # bot runs one task may use without a change before a person is asked (docs/pipelines-v3.md)


# Engine bugs the rig has found and that are still open, by name (see README.md), each with a test that tells its
# failures apart. A failure that matches one is still a failure: the name only groups a sweep's failures so a new bug
# stands out. None is open: every failure is new.
KNOWN_BUGS: dict[str, str] = {}
MATCHES: dict[str, Callable[[str], bool]] = {}


def known_bug(what: str) -> str | None:
    """The known engine bug a failure matches, or None for a new one."""
    return next((name for name, test in MATCHES.items() if name in KNOWN_BUGS and test(what)), None)


class RigFailure(AssertionError):
    """A rule broke. Carries the seed and step so the run can be replayed, and the known bug it matches, if any."""

    def __init__(self, seed: int, step: int, what: str, lines: list[str] | None = None) -> None:
        super().__init__(f"seed {seed} step {step}: {what}")
        self.seed, self.step, self.what = seed, step, what
        self.bug = known_bug(what)
        self.lines = lines or []  # the last steps before it, for the report


@dataclass
class Run:
    """One run of a bot on one wake."""

    attempt: str
    job: str
    bot: str
    task: str | None
    started: str
    ends: str  # when the bot stops (or crashes)
    behaviour: str
    ending: str  # completed, failed, interrupted or expired
    version: int | None = None
    visit: str | None = None
    lease: str = ""
    leased: bool = False  # taken, not yet started: its start asks the engine again
    length: int = 0


@dataclass
class Stats:
    steps: int = 0
    runs: int = 0
    behaviours: dict[str, int] = field(default_factory=dict)
    faults: dict[str, int] = field(default_factory=dict)
    people: dict[str, int] = field(default_factory=dict)
    refusals: dict[str, int] = field(default_factory=dict)
    tasks: int = 0
    ended: dict[str, int] = field(default_factory=dict)

    def add(self, table: dict[str, int], key: str) -> None:
        table[key] = table.get(key, 0) + 1


def _h() -> Any:
    from backend.store import H

    return H


# ----------------------------------------------------------------------------- reads
def task_of_job(c: sqlite3.Connection, job_id: str) -> str | None:
    """The task a job's message is about: the start of its wake key, or the task whose conversation it is in."""
    found = c.execute(
        "SELECT m.wake_key, m.conversation_id FROM jobs j JOIN messages m ON m.id = j.message_id WHERE j.id=?",
        (job_id,),
    ).fetchone()
    if not found:
        return None
    if found[0]:
        return str(found[0]).split(":", 1)[0]
    row = c.execute(
        "SELECT id FROM tasks WHERE conversation_id=? AND pipeline_id IS NOT NULL LIMIT 1", (found[1],)
    ).fetchone()
    return str(row[0]) if row else None


def wake_kind(c: sqlite3.Connection, job_id: str) -> str:
    """What a job's wake is: owner, manager, ask, unsnooze, again, or a reassign (vN), from its wake key."""
    found = c.execute(
        "SELECT m.wake_key FROM jobs j JOIN messages m ON m.id = j.message_id WHERE j.id=?", (job_id,)
    ).fetchone()
    parts = str(found[0] if found and found[0] else "").split(":")
    if len(parts) < 3:
        return "message"
    kind = parts[2]
    return f"{kind} {parts[3]}" if kind in ("owner", "manager") and len(parts) > 3 else kind


def fingerprint(c: sqlite3.Connection) -> dict[str, tuple[Any, ...]]:
    """For each task on the engine, what counts as a change (docs/pipelines-v3.md, C4, as R1 settles it): its visit,
    status and owner (a move, a reassign), the answers, added requirements and who each is asked of (a person giving an
    ask to someone else is a person acting, so a change) and filed tasks of its open visit. Not a
    snooze or its end (setting a task aside earns no runs), and not the task's version (a field edit raises it). Read
    by the rig itself, not through the engine's own progress mark. The last part says a person has it now (the ladder
    or Late got to a person)."""
    rows = c.execute(
        "SELECT t.id, v.id, t.status, t.owner_id, "
        "(SELECT COUNT(*) FROM answers a WHERE a.visit_id = v.id), "
        "(SELECT COUNT(*) FROM answers a WHERE a.visit_id = v.id AND a.undone_at IS NOT NULL), "
        "(SELECT COUNT(*) FROM requirements r WHERE r.visit_id = v.id), "
        "(SELECT COUNT(*) FROM requirements r WHERE r.visit_id = v.id AND r.withdrawn_at IS NOT NULL), "
        "(SELECT group_concat(r.id || '=' || COALESCE(r.person_id, ''), ',') FROM (SELECT id, person_id FROM "
        "requirements WHERE visit_id = v.id ORDER BY id) r), "
        "(SELECT COUNT(*) FROM links l WHERE l.visit_id = v.id), "
        "(SELECT COUNT(*) FROM links l LEFT JOIN tasks k ON k.id = l.to_task_id "
        " WHERE l.visit_id = v.id AND COALESCE(k.status, 'gone') <> 'open'), "
        "v.level = 'person' "
        "FROM tasks t LEFT JOIN visits v ON v.task_id = t.id AND v.left_at IS NULL WHERE t.pipeline_id IS NOT NULL"
    ).fetchall()
    return {str(r[0]): tuple(r[1:]) for r in rows}


def db_print(c: sqlite3.Connection) -> tuple[Any, ...]:
    """Everything a write could leave half done, to tell a rolled-back write from a partial one."""
    parts: list[Any] = []
    for sql in (
        "SELECT id, stage_id, status, owner_id, assignee_id, version FROM tasks WHERE pipeline_id IS NOT NULL ORDER BY id",
        "SELECT COUNT(*), COUNT(left_at), COALESCE(MAX(tries), 0) FROM visits",
        "SELECT COUNT(*) FROM answers",
        "SELECT COUNT(*) FROM requirements",
        "SELECT COUNT(*) FROM links",
        "SELECT COUNT(*), COUNT(ended_at) FROM snoozes",
        "SELECT COUNT(*) FROM messages",
        "SELECT state, COUNT(*) FROM jobs GROUP BY state ORDER BY state",
        "SELECT COUNT(*) FROM history_events",
        "SELECT open FROM task_move_guards",
    ):
        parts.append(tuple(tuple(r) for r in c.execute(sql).fetchall()))
    return tuple(parts)


def open_ids(c: sqlite3.Connection) -> list[str]:
    return [
        str(r[0])
        for r in c.execute(
            "SELECT id FROM tasks WHERE pipeline_id IS NOT NULL AND status='open' ORDER BY id"
        ).fetchall()
    ]


# ----------------------------------------------------------------------------- the computer
class Computer:
    """Runs the bots' wakes the way a runner does: a job is taken (leased), its run starts on the next step if the
    engine still lets it (execution.held_before_start: the task may have changed since the claim), holds a lease it
    renews every step, and ends completed, failed, interrupted (cut off) or expired (the lease lapsed). `choose(run)`
    picks the bot's behaviour for each run; the bot acts when the run ends, with the task version it read when it
    started."""

    def __init__(self, sim: "Sim") -> None:
        self.sim = sim
        self.runs: dict[str, Run] = {}
        self.started: list[Run] = []
        self.ended: list[Run] = []

    @property
    def w(self) -> World:
        return self.sim.world

    def busy(self, bot: str) -> bool:
        """A bot has one run at a time (one_active_attempt_per_lane)."""
        return any(r.bot == bot for r in self.runs.values())

    def step(self) -> None:
        now = self.w.now()
        for run in sorted(self.runs.values(), key=lambda r: (r.started, r.attempt)):
            if run.leased and run.lease > now:
                self._begin(run)
            elif run.lease <= now:
                self._finish(run, "expired", run.lease)  # nothing renewed it: a clock jump, or a crash
            elif run.ends <= now:
                if run.ending == "expired":
                    continue  # it crashed: nothing renews the lease, which lapses on its own
                if run.ending == "completed":
                    self.sim.bot_acts(run)
                self._finish(run, run.ending, self.w.now())
            else:
                self._renew(run)
        self._claim()

    def _renew(self, run: Run) -> None:
        H = _h()
        run.lease = H.shift(self.w.now(), seconds=LEASE_S)
        lease = run.lease

        def renew(c: sqlite3.Connection) -> None:
            c.execute("UPDATE attempts SET lease_until=? WHERE id=?", (lease, run.attempt))

        self.sim.write(renew)

    def _finish(self, run: Run, state: str, at: str) -> None:
        def end(c: sqlite3.Connection) -> None:
            c.execute("UPDATE attempts SET state=?, finished_at=? WHERE id=?", (state, at, run.attempt))
            # A lease that lapsed before its run started goes back in the queue (execution.settle_unstarted).
            job = "completed" if state == "completed" else "queued" if run.leased else "uncertain"
            c.execute("UPDATE jobs SET state=? WHERE id=?", (job, run.job))

        self.sim.write(end)
        self.runs.pop(run.attempt, None)
        self.ended.append(run)
        self.sim.log(f"{run.bot} run on {self.sim.name(run.task)} ended {state} ({run.behaviour})")

    def _claim(self) -> None:
        from backend import task_wakes

        def queued(c: sqlite3.Connection) -> list[tuple[str, str]]:
            return [
                (str(r[0]), str(r[1]))
                for r in c.execute(
                    "SELECT j.id, j.bot_id FROM jobs j WHERE j.state='queued' ORDER BY j.created_at, j.id"
                ).fetchall()
            ]

        self.sim.note_changes()
        for job_id, bot_id in self.w.read(queued):
            bot = "bot:" + bot_id
            if self.w.read(lambda c, bot=bot: task_wakes.cannot_run(c, bot)):
                continue
            task = self.w.read(lambda c, job_id=job_id: task_of_job(c, job_id))
            if task is None:
                self.sim.write(
                    lambda c, job_id=job_id: c.execute("UPDATE jobs SET state='completed' WHERE id=?", (job_id,))
                )
                continue
            if self.busy(bot):
                continue  # the bot's next run takes this wake
            # As the claim of a job does (execution.start_claimed): the engine may hold the run and cancel its wake.
            if not self.sim.write(lambda c, job_id=job_id: task_wakes.may_start(c, job_id)):
                self.sim.log(f"{bot}'s wake on {self.sim.name(task)} may not start a run: canceled")
                continue
            self._start(job_id, bot, task)

    def _start(self, job_id: str, bot: str, task: str) -> None:
        """The claim: the run is leased, not started (execution.Execution.start_claimed)."""
        H = _h()
        sim = self.sim
        row = self.w.task_row(task)
        behaviour = sim.choose(bot, row)
        rng = sim.rng
        length = rng.choice((20, 45, 90, 240, 600, 1500))
        ending = "completed"
        if behaviour == CRASH:
            ending = rng.choice(("failed", "interrupted", "expired"))
        if behaviour == HANG:
            length, ending = RUN_CAP_S, "interrupted"  # the run is cut off at the 60-minute limit
        now = self.w.now()
        attempt = H.new_id()
        run = Run(
            attempt=attempt,
            job=job_id,
            bot=bot,
            task=task,
            started=now,
            ends=H.shift(now, seconds=length),
            behaviour=behaviour,
            ending=ending,
            lease=H.shift(now, seconds=LEASE_S),
            leased=True,
            length=length,
        )

        def lease(c: sqlite3.Connection) -> None:
            message = c.execute("SELECT message_id FROM jobs WHERE id=?", (job_id,)).fetchone()[0]
            c.execute(
                "INSERT INTO attempts (id, job_id, bot_id, runner_id, generation, token_hash, state, lease_until, "
                "created_at, message_id, task_id) VALUES (?,?,?,'r1',1,?,'leased',?,?,?,?)",
                (attempt, job_id, bot.split(":", 1)[1], attempt, run.lease, now, message, task),
            )
            c.execute("UPDATE jobs SET state='leased', attempt_id=? WHERE id=?", (attempt, job_id))

        sim.write(lease)
        self.runs[attempt] = run

    def _begin(self, run: Run) -> None:
        """The start of a leased run (execution.Execution.started): refused when the engine holds it now, and then the
        run is skipped and its job canceled; else it starts and the bot reads the task."""
        from backend import task_wakes

        H = _h()
        sim = self.sim
        now = self.w.now()
        held = sim.write(lambda c: not task_wakes.may_start(c, run.job, attempt=run.attempt))
        if held:

            def skip(c: sqlite3.Connection) -> None:
                c.execute("UPDATE attempts SET state='interrupted', finished_at=? WHERE id=?", (now, run.attempt))
                c.execute("UPDATE jobs SET state='canceled' WHERE id=?", (run.job,))

            sim.write(skip)
            self.runs.pop(run.attempt, None)
            sim.log(f"{run.bot}'s run on {sim.name(run.task)} may not start any more: skipped")
            return
        row = self.w.task_row(run.task) if run.task else None
        run.leased = False
        run.started = now
        run.ends = H.shift(now, seconds=run.length)
        run.version = int(row["version"]) if row else None
        run.visit = (row.get("visit") or {}).get("id") if row else None
        run.lease = H.shift(now, seconds=LEASE_S)

        def start(c: sqlite3.Connection) -> None:
            c.execute(
                "UPDATE attempts SET state='running', started_at=?, lease_until=? WHERE id=?",
                (now, run.lease, run.attempt),
            )
            task_wakes.started(c, run.attempt)
            c.execute("UPDATE jobs SET state='running' WHERE id=?", (run.job,))

        sim.write(start)
        self.started.append(run)
        sim.stats.runs += 1
        sim.stats.add(sim.stats.behaviours, run.behaviour)
        sim.counted_run(run, row)
        sim.log(f"{run.bot} starts a run on {sim.name(run.task)}: will {run.behaviour}")


# ----------------------------------------------------------------------------- one seeded run
class Sim:
    """A world with its computer, bots, people and faults, checked after every step."""

    def __init__(self, world: World, seed: int, *, faults: bool = True, explain: bool = False) -> None:
        self.world = world
        self.seed = seed
        self.rng = random.Random(seed * 7919 + 1)
        self.faults_on = faults
        self.explain = explain
        self.step_n = 0
        self.lines: list[str] = []
        self.stats = Stats()
        self.computer = Computer(self)
        self.titles: dict[str, str] = {}
        self.marks: dict[str, tuple[Any, ...]] = {}
        self.unchanged_runs: dict[str, int] = {}
        self.counted: dict[str, list[str]] = {}  # the runs in the count: bot and the wake it ran on
        self.most_runs = 0
        self.held: list[tuple[str, str, Callable[[], None]]] = []  # (until, what, restore): faults to put right
        self.draining = False
        self.reassigned: dict[str, tuple[str, int]] = {}  # task -> (old owner, version it was taken at)
        self.crash_next: int | None = (
            None  # the next API write (or never-stuck pass) crashes after this many statements
        )
        self.crash_in_check = False
        # Each bot's habits, drawn from the seed: how likely it is to do each thing in a run.
        self.habits: dict[str, list[float]] = {}
        for bot in BOTS:
            weights = [self.rng.random() for _ in BEHAVIOURS]
            weights[0] *= 3  # bots finish more often than anything else
            self.habits[bot] = weights
        self.person_rate = {p: self.rng.choice((0.02, 0.05, 0.1, 0.2)) for p in PEOPLE}
        self.fault_rate = self.rng.choice((0.01, 0.03, 0.06))

    # ------------------------------------------------------------------ plumbing
    def log(self, text: str) -> None:
        line = f"[{self.step_n:4d} {self.world.now()[5:19]}] {text}"
        self.lines.append(line)
        if self.explain:
            print(line)

    def name(self, task: str | None) -> str:
        return self.titles.get(task or "", (task or "?")[:8])

    def fail(self, what: str) -> None:
        raise RigFailure(self.seed, self.step_n, what, self.lines[-40:])

    def write(self, fn: Callable[[sqlite3.Connection], Any]) -> Any:
        """A write the computer makes: a crash here is retried once the server is back, as a runner retries."""
        for _ in range(3):
            try:
                with self.world.tx() as c:
                    return fn(c)
            except Crash as crash:
                self.crashed(crash, None)
        self.fail("a write kept crashing")

    def act(self, actor: str, what: str, fn: Callable[[sqlite3.Connection], Any], task: str | None = None) -> Any:
        """An API call by a bot or a person. A crash part way must leave nothing behind. Returns the result, a Refusal,
        or None after a crash."""
        before = self.world.read(db_print)
        if self.crash_next is not None and not self.crash_in_check:
            Wd.METER.arm(self.crash_next)
            self.crash_next = None
        try:
            out = self.world.act(actor, fn)
        except Crash as crash:
            Wd.METER.disarm()
            self.crashed(crash, before)
            self.log(f"{actor} {what} on {self.name(task)}: the server crashed part way")
            return None
        except Exception as error:  # noqa: BLE001 - anything but a refusal is a bug the run reports
            Wd.METER.disarm()
            self.fail(f"{actor} {what} on {self.name(task)} raised {error!r}\n{traceback.format_exc()}")
        Wd.METER.disarm()
        if isinstance(out, Refusal):
            self.stats.add(self.stats.refusals, out.rule)
            self.log(f"{actor} {what} on {self.name(task)}: refused {out.rule}: {out.detail}")
        else:
            self.log(f"{actor} {what} on {self.name(task)}")
        return out

    def crashed(self, crash: Crash, before: tuple[Any, ...] | None) -> None:
        self.world.restart()
        self.stats.add(self.stats.faults, "crash")
        if before is not None and self.world.read(db_print) != before:
            self.fail(f"a crash left a write half done: {crash}")

    # ------------------------------------------------------------------ setup
    def populate(self) -> None:
        rng = self.rng
        count = rng.randint(4, 9)
        for i in range(count):
            pipeline = rng.choice((Wd.GENERAL, Wd.GENERAL, Wd.WORK))
            assignee = rng.choice((Wd.BUILDER, WRITER, WRITER, Wd.BUILDER, CARA))
            if pipeline == Wd.WORK:
                assignee = rng.choice((Wd.BUILDER, WRITER))
            by = rng.choice(PEOPLE)
            title = f"Task {i + 1}"
            tid = self.world.new_task(by, title, pipeline, assignee=assignee)
            if isinstance(tid, Refusal):
                self.fail(f"could not make {title}: {tid}")
            self.titles[str(tid)] = title
            self.log(f"{by} files {title} in {pipeline} for {assignee}")
        self.stats.tasks = count

    # ------------------------------------------------------------------ run counting
    def counted_run(self, run: Run, row: dict[str, Any] | None) -> None:
        if not row or row["status"] != "open" or not run.task:
            return
        if (row.get("visit") or {}).get("level") == "person":
            return  # a person is asked already
        n = self.unchanged_runs.get(run.task, 0) + 1
        self.unchanged_runs[run.task] = n
        if n == 1:
            self.counted[run.task] = []
        self.counted.setdefault(run.task, []).append(
            f"{run.bot.split(':', 1)[1]} ({self.world.read(lambda c: wake_kind(c, run.job))})"
        )
        self.most_runs = max(self.most_runs, n)
        if n > MAX_RUNS:
            runs = ", ".join(self.counted[run.task])
            self.fail(f"{self.name(run.task)} used {n} bot runs without a change before a person was asked: {runs}")

    def note_changes(self) -> None:
        marks = self.world.read(fingerprint)
        for task, mark in marks.items():
            if self.marks.get(task) != mark or mark[-1]:
                self.unchanged_runs[task] = 0  # it changed, or a person has it: the count starts again
        self.marks = marks

    # ------------------------------------------------------------------ bots
    def choose(self, bot: str, row: dict[str, Any] | None) -> str:
        if self.draining:
            return STOP
        weights = self.habits[bot]
        return self.rng.choices(BEHAVIOURS, weights)[0]

    def bot_acts(self, run: Run) -> None:
        """The bot's run ends: it does what it chose, against the version it read when the run started."""
        from backend import answers as A
        from backend import task_engine as E
        from backend import task_requirements as R
        from backend import waiting_on as W

        H = _h()
        w, rng, bot, task, seen = self.world, self.rng, run.bot, run.task, run.version
        if not task:
            return
        row = w.task_row(task)
        if not row or row["status"] != "open":
            return
        behaviour = run.behaviour
        taken = self.reassigned.get(task)
        if taken and taken[0] == bot and seen is not None and seen <= taken[1]:
            # The task was reassigned while this bot ran. Its next command is refused as a change since it looked.
            rid = self.owner_done_id(row) or ""
            out = self.act(
                bot,
                "says done after a reassign",
                lambda c: A.answer(c, task, rid, bot, "done", seen_version=seen),
                task,
            )
            if out is not None and not (isinstance(out, Refusal) and out.rule == "task_changed"):
                self.fail(f"{bot} wrote to {self.name(task)} after it was reassigned away: {out!r}")
            return
        if behaviour == STOP:
            return
        if behaviour == COMMENT:
            self.act(bot, "comments", lambda c: H.task_comment(c, bot, task, "Working on it."), task)
            return
        if behaviour == MENTION:
            who = rng.choice(("ben", "cara"))
            self.act(bot, f"mentions @{who}", lambda c: H.task_comment(c, bot, task, f"@{who} can you look?"), task)
            return
        if behaviour == SNOOZE_UNTIL:
            until = H.shift(w.now(), seconds=rng.choice((600, 3600, 6 * 3600, 2 * 86400)))
            self.act(bot, "snoozes", lambda c: E.snooze(c, task, bot, "Waiting on the vendor", until, seen), task)
            return
        if behaviour == SNOOZE_ICE:
            self.act(bot, "puts it on ice", lambda c: E.snooze(c, task, bot, "Parked until spring", None, seen), task)
            return
        if behaviour == ASK:
            kind = rng.choice(("ok", "answer"))
            person = rng.choice((ANA, BEN, CARA, REVIEWER))
            line = "Is this the right plan?" if kind == "answer" else "OK to go ahead?"
            self.act(
                bot,
                f"asks {person} for an {kind}",
                lambda c: E.add_requirement(c, task, bot, kind, person, line, seen_version=seen),
                task,
            )
            return
        if behaviour == FILE:
            self.file_under(bot, task)
            return
        # FINISH: meet what is asked of this bot, or as the manager decide what to do with the task.
        waits = w.read(lambda c: W.of(c, row))
        mine = [x for x in waits if x.who == bot and x.state in (W.BOT, W.PAUSED)]
        level = (row.get("visit") or {}).get("level") or "owner"
        if bot == MANAGER and level == "manager" and rng.random() < 0.6:
            self.manager_decides(row, seen)
            return
        for wait in mine:
            requirement = w.read(lambda c, wait=wait: R.get(c, wait.requirement_id)) if wait.requirement_id else None
            kind = (requirement or {}).get("kind", "owner_done")
            rid = (requirement or {}).get("id") or self.owner_done_id(row)
            if kind == "owner_done" and rid:
                self.act(
                    bot, "says done", lambda c, rid=rid: A.answer(c, task, rid, bot, "done", seen_version=seen), task
                )
            elif kind == "ok" and rid:
                if rng.random() < 0.25:
                    self.act(
                        bot,
                        "sends it back",
                        lambda c, rid=rid: A.answer(c, task, rid, bot, "no", "Fix the intro", seen_version=seen),
                        task,
                    )
                else:
                    self.act(
                        bot, "OKs it", lambda c, rid=rid: A.answer(c, task, rid, bot, "ok", seen_version=seen), task
                    )
            elif kind == "answer" and rid:
                self.act(bot, "answers", lambda c, rid=rid: A.answer(c, task, rid, bot, "Yes", seen_version=seen), task)
            elif kind == "linked_done":
                if rng.random() < 0.2:
                    self.act(
                        bot,
                        "pushes past",
                        lambda c: E.move(c, task, E.NEXT, bot, "Done another way", seen),
                        task,
                    )
                else:
                    self.file_under(bot, task)
            return

    def owner_done_id(self, row: dict[str, Any]) -> str | None:
        found = self.world.read(
            lambda c: c.execute(
                "SELECT id FROM requirements WHERE stage_id=? AND kind='owner_done' AND withdrawn_at IS NULL",
                (row["stage_id"],),
            ).fetchone()
        )
        return str(found[0]) if found else None

    def file_under(self, bot: str, task: str) -> None:
        title = f"{self.name(task)}.{sum(1 for t in self.titles if t) + 1}"
        assignee = self.rng.choice((WRITER, Wd.BUILDER))
        tid = self.world.new_task(bot, title, Wd.GENERAL, assignee=assignee, parent=task)
        if isinstance(tid, Refusal):
            self.stats.add(self.stats.refusals, tid.rule)
            self.log(f"{bot} files a sub-task under {self.name(task)}: refused {tid.rule}")
            return
        self.titles[str(tid)] = title
        self.log(f"{bot} files {title} under {self.name(task)} for {assignee}")

    def manager_decides(self, row: dict[str, Any], seen: int | None) -> None:
        from backend import answers as A
        from backend import task_engine as E

        task, rng = row["id"], self.rng
        choice = rng.choice((REASSIGN, CLOSE, BACK, FINISH, SNOOZE_UNTIL))
        if choice == REASSIGN:
            to = rng.choice((Wd.BUILDER, WRITER, CARA))
            self.act(
                MANAGER, f"reassigns to {to}", lambda c: E.reassign(c, task, to, MANAGER, "Fresh eyes", seen), task
            )
        elif choice == CLOSE:
            self.act(MANAGER, "closes it", lambda c: E.move(c, task, E.CLOSED, MANAGER, "Not needed now", seen), task)
        elif choice == BACK:
            stages = [s for s in self.world.read(lambda c: E.stages(c, row["pipeline_id"])) if s["kind"] == "work"]
            at = next((i for i, s in enumerate(stages) if s["id"] == row["stage_id"]), 0)
            if at == 0:
                return
            back = stages[at - 1]["id"]
            self.act(MANAGER, "moves it back", lambda c: E.move(c, task, back, MANAGER, "Needs more work", seen), task)
        elif choice == SNOOZE_UNTIL:
            until = _h().shift(self.world.now(), seconds=4 * 3600)
            self.act(MANAGER, "snoozes it", lambda c: E.snooze(c, task, MANAGER, "Later today", until, seen), task)
        else:
            rid = self.owner_done_id(row)
            if rid:
                self.act(
                    MANAGER, "says done", lambda c: A.answer(c, task, rid, MANAGER, "done", seen_version=seen), task
                )

    # ------------------------------------------------------------------ people
    def people_act(self) -> None:
        from backend import waiting_on as W

        if self.draining:
            return
        for person in PEOPLE:
            if self.rng.random() >= self.person_rate[person]:
                continue
            items = self.world.read(lambda c, person=person: W.needs(c, person))
            if person == ANA:
                items = items + [{"kind": "by_hand", "task_id": t} for t in self.by_hand_tasks()]
            if not items:
                continue
            item = self.rng.choice(items)
            self.person_does(person, item)

    def by_hand_tasks(self) -> list[str]:
        return self.world.read(
            lambda c: [
                str(r[0])
                for r in c.execute(
                    "SELECT t.id FROM tasks t JOIN stages s ON s.id = t.stage_id WHERE t.status='open' "
                    "AND s.by_hand=1 AND t.pipeline_id IS NOT NULL ORDER BY t.id"
                ).fetchall()
            ]
        )

    def person_does(self, person: str, item: dict[str, Any]) -> None:
        from backend import answers as A
        from backend import task_engine as E
        from backend import task_requirements as R

        rng, kind = self.rng, item["kind"]
        if kind == "paused":
            self.stats.add(self.stats.people, "fixes a paused bot")
            self.restore_all(person)
            return
        task = str(item["task_id"])
        row = self.world.task_row(task)
        if not row or row["status"] != "open":
            return
        version = row["version"]
        if kind == "by_hand":
            rid = self.owner_done_id(row)
            self.stats.add(self.stats.people, "moves a task on by hand")
            if rid:
                self.act(person, "says done", lambda c: A.answer(c, task, rid, person, "done"), task)
            self.act(person, "moves it on", lambda c: E.move(c, task, E.NEXT, person, None, version), task)
            return
        if kind == "ask":
            rid = str(item["requirement_id"])
            requirement = self.world.read(lambda c: R.get(c, rid)) or {}
            choice = rng.choice(("answer", "answer", "no", "give", "push past", "thumbs down", SILENT))
            self.stats.add(self.stats.people, f"ask: {choice}")
            if choice == SILENT:
                return
            if choice == "give":
                other = rng.choice([p for p in PEOPLE if p != person])
                self.act(person, f"gives it to {other}", lambda c: A.give_to(c, task, rid, person, other), task)
            elif choice == "push past":
                self.act(person, "pushes past", lambda c: E.move(c, task, E.NEXT, person, "Go ahead", version), task)
            elif choice == "thumbs down":
                self.act(
                    person, "gives a thumbs down", lambda c: A.thumbs_down(c, task, rid, person, "Not useful"), task
                )
            elif choice == "no" and requirement.get("kind") == "ok":
                self.act(
                    person, "sends it back", lambda c: A.answer(c, task, rid, person, "no", "Change the plan"), task
                )
            else:
                value = "ok" if requirement.get("kind") == "ok" else "Yes, go"
                self.act(person, f"answers {value}", lambda c: A.answer(c, task, rid, person, value), task)
            return
        # The task is theirs: their own, or the ladder or Late brought it to them.
        choice = rng.choice(("try again", "done", "reassign", "close", "snooze", "push past", SILENT, SILENT))
        self.stats.add(self.stats.people, f"task: {choice}")
        if choice == SILENT:
            return
        if choice == "try again":
            self.act(person, "says try again", lambda c: E.try_again(c, task, person), task)
        elif choice == "done":
            rid = self.owner_done_id(row)
            if rid:
                self.act(person, "says done", lambda c: A.answer(c, task, rid, person, "done"), task)
        elif choice == "reassign":
            to = rng.choice((Wd.BUILDER, WRITER, person))
            self.act(person, f"reassigns to {to}", lambda c: E.reassign(c, task, to, person, None, version), task)
        elif choice == "close":
            self.act(person, "closes it", lambda c: E.move(c, task, E.CLOSED, person, None, version), task)
        elif choice == "snooze":
            until = _h().shift(self.world.now(), seconds=86400)
            self.act(person, "snoozes it", lambda c: E.snooze(c, task, person, None, until, version), task)
        else:
            self.act(person, "pushes past", lambda c: E.move(c, task, E.NEXT, person, "Good enough", version), task)

    # ------------------------------------------------------------------ faults
    def faults(self) -> None:
        H = _h()
        now = self.world.now()
        for until, what, restore in list(self.held):
            if until <= now:
                self.held.remove((until, what, restore))
                restore()
                self.log(f"fault over: {what}")
        if not self.faults_on or self.draining:
            return
        rng = self.rng
        if rng.random() >= self.fault_rate * 6:
            return
        fault = rng.choice(
            (
                "crash",
                "vanish",
                "pause",
                "quarantine",
                "credit",
                "computer",
                "movers",
                "jump",
                "back",
                "reassign",
                "delete",
            )
        )
        self.stats.add(self.stats.faults, fault)
        if fault == "crash":
            self.crash_next = rng.randint(0, 80)
            self.crash_in_check = rng.random() < 0.3
            where = "never-stuck pass" if self.crash_in_check else "write"
            self.log(f"fault: the next {where} crashes after {self.crash_next} statements")
        elif fault == "vanish":
            self.vanish()
        elif fault in ("pause", "quarantine", "credit", "computer"):
            self.stop_bot(fault, H.shift(now, seconds=rng.choice((300, 1800, 3 * 3600, 8 * 3600))))
        elif fault == "movers":
            self.two_movers()
        elif fault == "jump":
            hours = rng.choice((1, 3, 8, 26))
            self.world.shift(hours * 3600)
            self.log(f"fault: the clock jumps {hours} hours ahead")
        elif fault == "back":
            seconds = rng.randint(1, 90)
            self.world.shift(-seconds)
            self.log(f"fault: the clock goes back {seconds} seconds")
        elif fault == "reassign":
            self.reassign_running()
        elif fault == "delete":
            self.delete_linked()

    def vanish(self) -> None:
        found = self.world.read(
            lambda c: c.execute(
                "SELECT j.id, m.wake_key FROM jobs j JOIN messages m ON m.id = j.message_id "
                "WHERE j.state='queued' AND m.wake_key IS NOT NULL ORDER BY j.id"
            ).fetchall()
        )
        if not found:
            return
        job, key = self.rng.choice(found)
        self.write(lambda c: c.execute("DELETE FROM jobs WHERE id=?", (job,)))
        self.log(f"fault: wake {key} vanished")

    def stop_bot(self, how: str, until: str) -> None:
        H = _h()
        bot = self.rng.choice(BOTS)
        slug = bot.split(":", 1)[1]
        if how in ("pause", "quarantine"):
            state = "paused" if how == "pause" else "quarantined"
            self.write(lambda c: c.execute("UPDATE bots SET state=? WHERE id=?", (state, slug)))
            what = f"{bot} is {state}"

            def restore() -> None:
                self.write(lambda c: c.execute("UPDATE bots SET state='active' WHERE id=?", (slug,)))

        elif how == "credit":
            value = '{"access": "frozen", "reason": "out_of_credit"}'
            self.write(
                lambda c: c.execute(
                    "INSERT OR REPLACE INTO registry_metadata(key, value) VALUES('cloud-access', ?)", (value,)
                )
            )
            what = "Tico Cloud is out of credit"

            def restore() -> None:
                self.write(lambda c: c.execute("DELETE FROM registry_metadata WHERE key='cloud-access'"))

        else:
            self.write(lambda c: c.execute("UPDATE runners SET revoked_at=? WHERE id='r1'", (H.now(),)))
            what = "the computer was removed"

            def restore() -> None:
                self.write(lambda c: c.execute("UPDATE runners SET revoked_at=NULL WHERE id='r1'"))

        self.held.append((until, what, restore))
        self.log(f"fault: {what} until {until[5:19]}")

    def restore_all(self, by: str = "the rig") -> None:
        for _until, what, restore in list(self.held):
            restore()
            self.log(f"{by} puts right: {what}")
        self.held.clear()

    def two_movers(self) -> None:
        """A person and a bot move the same task from the same version. The second must be refused."""
        from backend import task_engine as E

        ids = self.world.read(open_ids)
        if not ids:
            return
        task = self.rng.choice(ids)
        row = self.world.task_row(task)
        if not row or row["stage"].get("by_hand"):
            return
        version = row["version"]
        work = [s for s in self.world.read(lambda c: E.stages(c, row["pipeline_id"])) if s["kind"] == "work"]
        at = next((i for i, s in enumerate(work) if s["id"] == row["stage_id"]), 0)
        if at > 0:
            back = work[at - 1]["id"]
            first = self.act(ANA, "moves it back", lambda c: E.move(c, task, back, ANA, None, version), task)
        else:
            to = WRITER if row.get("owner_id") != WRITER else Wd.BUILDER
            first = self.act(ANA, f"reassigns to {to}", lambda c: E.reassign(c, task, to, ANA, None, version), task)
        if first is None or isinstance(first, Refusal):
            return
        bot = row.get("owner_id") if str(row.get("owner_id") or "").startswith("bot:") else WRITER
        second = self.act(bot, "closes it too", lambda c: E.move(c, task, E.CLOSED, bot, "Duplicate", version), task)
        if not (isinstance(second, Refusal) and second.rule == "task_changed"):
            self.fail(f"two movers: the second move from version {version} was not refused as changed: {second!r}")

    def reassign_running(self) -> None:
        from backend import task_engine as E
        from backend import task_wakes

        runs = [r for r in self.computer.runs.values() if r.task and r.ending == "completed" and not r.leased]
        if not runs:
            return
        run = self.rng.choice(sorted(runs, key=lambda r: r.attempt))
        task = run.task or ""
        row = self.world.task_row(task)
        if not row or row["status"] != "open" or row.get("owner_id") != run.bot:
            return
        to = WRITER if run.bot != WRITER else Wd.BUILDER
        out = self.act(ANA, f"reassigns to {to} while {run.bot} runs", lambda c: E.reassign(c, task, to, ANA), task)
        if out is None or isinstance(out, Refusal):
            return
        self.reassigned[task] = (run.bot, int(row["version"]))
        if self.world.read(lambda c: task_wakes.queued(c, task, run.bot)):
            self.fail(f"a reassign left {run.bot}'s wake for {self.name(task)} queued")

    def delete_linked(self) -> None:
        from backend import task_delete

        found = self.world.read(
            lambda c: c.execute(
                "SELECT l.to_task_id FROM links l JOIN tasks t ON t.id = l.to_task_id ORDER BY l.created_at, l.id"
            ).fetchall()
        )
        if not found:
            return
        task = str(self.rng.choice(found)[0])
        self.act(
            ANA, "deletes a filed task", lambda c: task_delete.delete_tasks(c, [task], apply=True, keep_work=True), task
        )

    # ------------------------------------------------------------------ the step
    def step(self, seconds: float) -> None:
        from backend import never_stuck as NS

        self.step_n += 1
        self.stats.steps += 1
        self.world.shift(seconds)
        self.computer.step()
        self.people_act()
        self.faults()
        self.write(Wd.settle_stopped)
        self.note_changes()
        failures: list[tuple[str, Exception]] = []
        before = None
        if self.crash_next is not None and self.crash_in_check:
            before = self.world.read(db_print)
            Wd.METER.arm(self.crash_next)
            self.crash_next, self.crash_in_check = None, False
        try:
            with self.world.tx() as c:
                done = NS.check(c, failures=failures)
            Wd.METER.disarm()
        except Crash as crash:
            Wd.METER.disarm()
            self.crashed(crash, before)
            self.log("the never-stuck pass crashed part way and rolled back")
            done = {}
        if failures:
            task, error = failures[0]
            self.fail(f"the never-stuck check raised on {self.name(task)}: {error!r}")
        said = {k: n for k, n in done.items() if n}
        if said:
            self.log(f"never-stuck check: {said}")
        self.verify()
        self.note_changes()

    def verify(self) -> None:
        from backend import never_stuck as NS
        from backend import waiting_on as W

        broken = self.world.read(NS.rules_hold)
        if broken:
            self.fail("rules broken: " + "; ".join(broken))
        for task in self.world.read(open_ids):
            if not self.world.read(lambda c, task=task: W.waiting_on(c, task)):
                self.fail(f"{self.name(task)} is open and waits on nothing")

    # ------------------------------------------------------------------ the end
    def drain(self, limit_days: float = 6.0) -> None:
        """Stop the faults, put every bot right, and let the clock run until nothing more is due."""
        H = _h()
        self.draining = True
        Wd.METER.disarm()
        self.restore_all()
        start = self.world.at()
        self.step(10)  # one pass with every bot put right: what fell due while a fault held it is seen now
        for _ in range(4000):
            if (self.world.at() - start).total_seconds() > limit_days * 86400:
                break
            wait = self.next_due()
            if wait is None:
                break
            self.step(wait)
        self.final_check()
        del H

    def next_due(self) -> float | None:
        """Seconds to the next thing that could happen, at least 10 and at most an hour. None when nothing is due."""
        H = _h()
        if self.computer.runs:
            return 30
        now = self.world.now()
        if self.world.read(lambda c: c.execute("SELECT 1 FROM jobs WHERE state='queued' LIMIT 1").fetchone()):
            return 10
        times: list[str] = []

        def due(c: sqlite3.Connection) -> None:
            for r in c.execute(
                "SELECT v.next_try_at, v.entered_at, s.expected_minutes, v.late_person_at FROM visits v "
                "JOIN tasks t ON t.id = v.task_id JOIN stages s ON s.id = v.stage_id "
                "WHERE v.left_at IS NULL AND t.status='open' AND t.pipeline_id IS NOT NULL"
            ).fetchall():
                if r[0]:
                    times.append(str(r[0]))
                if r[2] and not r[3]:
                    times.append(H.shift(r[1], seconds=int(r[2]) * 60 * 3 + 1))
            for r in c.execute("SELECT until_at FROM snoozes WHERE ended_at IS NULL AND until_at IS NOT NULL"):
                times.append(str(r[0]))
            for r in c.execute("SELECT MAX(finished_at) FROM attempts"):
                if r[0]:
                    times.append(H.shift(r[0], seconds=3601))

        self.world.read(due)
        # Due now counts: a snooze that ended this pass leaves its task due at once, for the next pass (R1).
        later = sorted(t for t in times if t >= now)
        if not later:
            return None
        gap = (Wd.parse(later[0]) - Wd.parse(now)).total_seconds()
        return min(max(gap, 10.0), 3600.0)

    def final_check(self) -> None:
        """Every task is Done, Closed, Snoozed, in a by-hand stage or a person's turn, or waits only on room or tasks
        whose own chain ends in one of those."""
        from backend import waiting_on as W

        def settled(task: str, seen: set[str]) -> str | None:
            row = self.world.task_row(task)
            if not row:
                return None  # deleted
            if row["status"] != "open":
                return None
            if task in seen:
                return f"{self.name(task)} waits on itself"
            seen = seen | {task}
            waits = self.world.read(lambda c: W.of(c, row))
            states = {x.state for x in waits}
            if states & {W.SNOOZED, W.BY_HAND, W.PERSON, W.PAUSED}:
                return None
            if W.BOT in states:
                return f"{self.name(task)} is left waiting on a bot: {[(x.who, x.line) for x in waits]}"
            for x in waits:
                if x.state == W.TASKS and x.who:
                    problem = settled(x.who, seen)
                    if problem:
                        return f"{self.name(task)} waits on a task that never ends: {problem}"
                elif x.state == W.ROOM:
                    if x.line.startswith("Moving to"):
                        return f"{self.name(task)} is ready and has room but did not move"
                    for held in self.world.read(lambda c, x=x: _in_stage(c, x.who)):
                        problem = settled(held, seen)
                        if problem:
                            return f"{self.name(task)} waits for room held by a task that never ends: {problem}"
                elif x.state not in (W.TASKS, W.ROOM):
                    return f"{self.name(task)} waits on {x.state}"
            return None

        for task in self.world.read(open_ids):
            problem = settled(task, set())
            if problem:
                self.fail("at the end: " + problem)
        for row in self.world.read(
            lambda c: c.execute(
                "SELECT status, COUNT(*) FROM tasks WHERE pipeline_id IS NOT NULL GROUP BY status"
            ).fetchall()
        ):
            self.stats.ended[str(row[0])] = int(row[1])


def _in_stage(c: sqlite3.Connection, stage_id: str | None) -> list[str]:
    return [
        str(r[0])
        for r in c.execute(
            "SELECT id FROM tasks WHERE stage_id=? AND status='open' AND pipeline_id IS NOT NULL ORDER BY id",
            (stage_id,),
        ).fetchall()
    ]


def run_seed(seed: int, *, explain: bool = False, steps: int | None = None) -> dict[str, Any]:
    """One seeded run: a few tasks, a stretch of steps with faults, then the drain and the end check. Returns a report.
    Raises RigFailure with the seed and step on the first broken rule."""
    with World(seed) as world:
        sim = Sim(world, seed, explain=explain)
        try:
            sim.populate()
            sim.note_changes()
            count = steps if steps is not None else sim.rng.randint(150, 400)
            for _ in range(count):
                sim.step(sim.rng.choice((10, 10, 20, 30, 60)))
            sim.drain()
        except RigFailure:
            raise
        finally:
            Wd.METER.disarm()
        return {
            "seed": seed,
            "steps": sim.stats.steps,
            "runs": sim.stats.runs,
            "most_unchanged_runs": sim.most_runs,
            "tasks": len(sim.titles),
            "behaviours": sim.stats.behaviours,
            "faults": sim.stats.faults,
            "people": sim.stats.people,
            "refusals": sim.stats.refusals,
            "ended": sim.stats.ended,
            "restarts": world.restarts,
        }
