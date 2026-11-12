"""The 52 scenarios (docs/pipelines-v3.md, "Scenarios the rig and the spot checks must pass"), as named, numbered
cases on the rig's world, plus the engine bugs the seeded runs found.

Each scenario builds what it needs on a fresh World (the real migrations and engine, the fake clock, seeded ids), does
what the scenario says through the engine's own functions, and checks the outcome. After every check pass the rig's
per-step checks run as in a seeded run: the seven rules hold and every open task waits on something.

- SCENARIOS: the ones this branch can express. Each passes, or is in FAILING.
- FAILING: scenario number -> the engine bug that makes it fail. The test suite runs these as strict expected failures,
  so a fix shows up as an unexpected pass.
- LATER: scenario number -> (phase, why it cannot run on this branch yet).
- BUGS: extra cases for bugs the seeded runs found; each fails today and names its bug (README.md).
- CHECKS: extra cases that pass and cover a brief point no scenario does (the clock going back).

`numbers_covered()` is SCENARIOS plus LATER; it must be exactly 1 to 52."""

import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from . import world as Wd
from .world import ANA, BEN, BUILDER, GENERAL, MANAGER, REVIEWER, WORK, WRITER, Crash, Refusal, World


class ScenarioFailed(AssertionError):
    """A scenario's check did not hold."""


def expect(ok: object, what: str) -> None:
    if not ok:
        raise ScenarioFailed(what)


def _h() -> Any:
    from backend.store import H

    return H


# ----------------------------------------------------------------------------- the scenario's hands
class Rig:
    """What a scenario does to its world: make tasks, act as a person or a bot, run a bot's wakes, move the clock, and
    run the never-stuck check with the rig's per-step checks after it."""

    def __init__(self, world: World) -> None:
        self.w = world
        self.passes = 0

    # -------------------------------------------------------------- reads
    def row(self, task: str) -> dict[str, Any]:
        found = self.w.task_row(task)
        expect(found, f"task {task} is missing")
        return found or {}

    def visit(self, task: str) -> dict[str, Any]:
        return self.row(task).get("visit") or {}

    def waits(self, task: str) -> list[tuple[str, str | None]]:
        from backend import waiting_on as W

        return [(x.state, x.who) for x in self.w.read(lambda c: W.waiting_on(c, task))]

    def wait_lines(self, task: str) -> list[str]:
        from backend import waiting_on as W

        return [x.line for x in self.w.read(lambda c: W.waiting_on(c, task))]

    def needs(self, person: str) -> list[dict[str, Any]]:
        from backend import waiting_on as W

        return self.w.read(lambda c: W.needs(c, person))

    def needs_you(self, person: str) -> list[dict[str, Any]]:
        """The person's Needs you as `GET /api/v2/needs-you` gives it (views.needs_items): `needs` plus every @ of them by
        a person they have not replied to (C28), only what they may read."""
        from backend import views
        from backend.auth import Auth, Identity

        auth = Auth(self.w.store)
        who = Identity(person, "human")
        return self.w.read(lambda c: views.needs_items(c, auth, who, count_only=True))

    def needs_tasks(self, person: str) -> list[tuple[str, str | None]]:
        return [(str(n["kind"]), n.get("task_id")) for n in self.needs(person)]

    def wakes(self, task: str, bot: str | None = None) -> list[tuple[str, str, str]]:
        """(wake key after the task and visit, recipient, first line of the body), oldest first."""
        rows = self.w.read(
            lambda c: c.execute(
                "SELECT wake_key, recipient_id, body FROM messages WHERE wake_key LIKE ? ORDER BY created_at, rowid",
                (task + ":%",),
            ).fetchall()
        )
        out = [(str(k).split(":", 2)[2], str(r), str(b or "").split("\n")[0]) for k, r, b in rows]
        return [x for x in out if bot is None or x[1] == bot]

    def queued(self, task: str, bot: str | None = None) -> int:
        """Jobs queued for a bot's wakes about this task."""
        return int(
            self.w.read(
                lambda c: c.execute(
                    "SELECT COUNT(*) FROM jobs j JOIN messages m ON m.id = j.message_id WHERE j.state='queued' "
                    "AND m.wake_key LIKE ? AND (? IS NULL OR m.recipient_id = ?)",
                    (task + ":%", bot, bot),
                ).fetchone()[0]
            )
        )

    def events(self, task: str) -> list[dict[str, Any]]:
        rows = self.w.read(
            lambda c: c.execute(
                "SELECT kind, old, new, note, data FROM history_events WHERE subject_type='task' AND subject_id=? "
                "ORDER BY rowid",
                (task,),
            ).fetchall()
        )
        return [
            {"kind": k, "old": o, "new": n, "note": note, "data": json.loads(d) if d else {}}
            for k, o, n, note, d in rows
        ]

    def stage_of(self, task: str) -> str:
        return str(self.row(task)["stage"]["name"])

    def requirement(self, task: str, kind: str) -> str:
        """The id of the task's current stage requirement of `kind`."""
        stage = self.row(task)["stage_id"]
        found = self.w.read(
            lambda c: c.execute(
                "SELECT id FROM requirements WHERE stage_id=? AND kind=? AND withdrawn_at IS NULL ORDER BY position",
                (stage, kind),
            ).fetchone()
        )
        expect(found, f"no {kind} requirement on {stage}")
        return str(found[0])

    def bot_state(self, bot: str) -> str:
        found = self.w.read(
            lambda c: c.execute("SELECT state FROM bots WHERE id=?", (bot.split(":", 1)[1],)).fetchone()
        )
        return str(found[0]) if found else "missing"

    # -------------------------------------------------------------- writes
    def act(self, actor: str, fn: Callable[[sqlite3.Connection], Any]) -> Any:
        return self.w.act(actor, fn)

    def ok(self, actor: str, fn: Callable[[sqlite3.Connection], Any], what: str) -> Any:
        """A write that must be taken."""
        out = self.w.act(actor, fn)
        expect(not isinstance(out, Refusal), f"{what}: refused {out!r}")
        return out

    def refused(self, actor: str, fn: Callable[[sqlite3.Connection], Any], rule: str, what: str) -> Refusal:
        """A write that must be refused with `rule`. Returns the refusal."""
        out = self.w.act(actor, fn)
        expect(isinstance(out, Refusal), f"{what}: it was taken")
        expect(out.rule == rule, f"{what}: refused {out.rule}, not {rule}: {out.detail}")
        return out

    def raw(self, fn: Callable[[sqlite3.Connection], Any]) -> Any:
        with self.w.tx() as c:
            return fn(c)

    def task(
        self, by: str, title: str, pipeline: str = GENERAL, assignee: str | None = None, parent: str | None = None
    ) -> str:
        tid = self.w.new_task(by, title, pipeline, assignee=assignee, parent=parent)
        expect(not isinstance(tid, Refusal), f"could not make {title}: {tid!r}")
        return str(tid)

    def pipeline(self, record: dict[str, Any]) -> dict[str, Any]:
        from backend import pipeline_settings as PS
        from backend import task_engine as E

        return self.ok(E.SYSTEM, lambda c: PS.save(c, E.SYSTEM, record), f"save {record.get('name')}")

    def to_deck(self, task: str) -> None:
        """Ana takes a Work task out of Ideas, by hand."""
        from backend import answers as A
        from backend import task_engine as E

        rid = self.requirement(task, "owner_done")
        self.ok(ANA, lambda c: A.answer(c, task, rid, ANA, "done"), "Ana says the idea is ready")
        self.ok(ANA, lambda c: E.move(c, task, E.NEXT, ANA), "Ana moves the idea on")

    def say_done(self, task: str, by: str) -> Any:
        from backend import answers as A

        row = self.row(task)
        rid = self.requirement(task, "owner_done")
        return self.act(by, lambda c: A.answer(c, task, rid, by, "done", seen_version=row["version"]))

    # -------------------------------------------------------------- runs
    def start(self, task: str, bot: str) -> list[str]:
        """The bot's computer takes its queued wakes about this task: each gets a run that is live (leased, renewed)."""
        H = _h()
        now = self.w.now()
        lease = H.shift(now, seconds=90)

        def take(c: sqlite3.Connection) -> list[str]:
            rows = c.execute(
                "SELECT j.id, j.message_id FROM jobs j JOIN messages m ON m.id = j.message_id "
                "WHERE j.state='queued' AND j.bot_id=? AND m.wake_key LIKE ? ORDER BY j.created_at, j.id",
                (bot.split(":", 1)[1], task + ":%"),
            ).fetchall()
            out: list[str] = []
            for job, message in rows[:1]:  # one run at a time per bot
                run = H.new_id()
                c.execute(
                    "INSERT INTO attempts (id, job_id, bot_id, runner_id, generation, token_hash, state, lease_until, "
                    "created_at, started_at, message_id, task_id) VALUES (?,?,?,'r1',1,?,'running',?,?,?,?,?)",
                    (run, job, bot.split(":", 1)[1], run, lease, now, now, message, task),
                )
                c.execute("UPDATE jobs SET state='running', attempt_id=? WHERE id=?", (run, job))
                out.append(str(run))
            return out

        return self.raw(take)

    def end(self, runs: list[str], state: str = "completed") -> None:
        now = self.w.now()

        def finish(c: sqlite3.Connection) -> None:
            for run in runs:
                c.execute("UPDATE attempts SET state=?, finished_at=? WHERE id=?", (state, now, run))
                job = "completed" if state == "completed" else "uncertain"
                c.execute("UPDATE jobs SET state=? WHERE attempt_id=?", (job, run))

        self.raw(finish)

    def ran(self, task: str, bot: str, state: str = "completed", during: Callable[[], None] | None = None) -> int:
        """The bot's next queued wake about this task has its run, which ends now in `state`. `during` is what the bot
        does in the run. Returns how many runs (0 or 1)."""
        runs = self.start(task, bot)
        if runs and during:
            during()
        self.end(runs, state)
        return len(runs)

    def runs(self, task: str) -> int:
        return int(
            self.w.read(lambda c: c.execute("SELECT COUNT(*) FROM attempts WHERE task_id=?", (task,)).fetchone()[0])
        )

    # -------------------------------------------------------------- the clock and the check
    def check(self) -> dict[str, int]:
        """One pass of the real never-stuck check, then the rig's per-step checks."""
        from backend import never_stuck as NS
        from backend import waiting_on as W

        failures: list[tuple[str, Exception]] = []
        self.raw(Wd.settle_stopped)
        with self.w.tx() as c:
            done = NS.check(c, failures=failures)
        self.passes += 1
        expect(not failures, f"the never-stuck check raised: {failures[:1]!r}")
        broken = self.w.read(NS.rules_hold)
        expect(not broken, "rules broken: " + "; ".join(broken))
        for task in self.w.read(_open_ids):
            expect(self.w.read(lambda c, task=task: W.waiting_on(c, task)), f"open task {task} waits on nothing")
        return {k: n for k, n in done.items() if n}

    def later(self, seconds: float = 0, minutes: float = 0, hours: float = 0, days: float = 0) -> dict[str, int]:
        self.w.shift(seconds + minutes * 60 + hours * 3600 + days * 86400)
        return self.check()

    def heartbeat(self, seconds: float) -> dict[str, int]:
        """The Heartbeat for `seconds`: a pass every 10 seconds. Returns what all the passes did, summed."""
        total: dict[str, int] = {}
        for _ in range(int(seconds // 10)):
            for k, n in self.later(10).items():
                total[k] = total.get(k, 0) + n
        return total

    def ladder(
        self, task: str, during: Callable[[str], None] | None = None, hours: float = 12
    ) -> list[tuple[str, float, str]]:
        """Every bot run on this task ends at once with nothing changed, until a person has it. Returns each run as
        (bot, seconds since the run before it ended, wake), and checks a pass every 10 seconds around each due time."""
        H = _h()
        out: list[tuple[str, float, str]] = []
        last = Wd.parse(self.w.now())
        limit = last.timestamp() + hours * 3600
        while Wd.parse(self.w.now()).timestamp() < limit:
            if (self.visit(task).get("level") or "owner") == "person":
                return out
            ran = False
            for bot in Wd.BOTS:
                if self.queued(task, bot):
                    kind = self.wakes(task, bot)[-1][0]
                    now = Wd.parse(self.w.now())
                    self.ran(task, bot, during=(lambda b=bot: during(b)) if during else None)
                    out.append((bot, (now - last).total_seconds(), kind))
                    last = now
                    ran = True
            if ran:
                self.check()
                continue
            due = self.visit(task).get("next_try_at")
            if due and due > self.w.now():
                gap = (Wd.parse(due) - Wd.parse(self.w.now())).total_seconds()
                if gap > 10:
                    self.w.shift(gap - 10)
                    self.check()  # one pass just before it is due: nothing goes out early
            self.later(10)
        del H
        return out


def _open_ids(c: sqlite3.Connection) -> list[str]:
    return [
        str(r[0])
        for r in c.execute(
            "SELECT id FROM tasks WHERE pipeline_id IS NOT NULL AND status='open' ORDER BY id"
        ).fetchall()
    ]


# ----------------------------------------------------------------------------- the registry
@dataclass(frozen=True)
class Scenario:
    number: int
    name: str
    run: Callable[[Rig], None]


SCENARIOS: dict[int, Scenario] = {}
BUGS: dict[str, Scenario] = {}
CHECKS: dict[str, Scenario] = {}

# Scenarios that fail on this branch because of an engine bug, by the bug's name (README.md).
FAILING: dict[int, str] = {}

# Scenarios this branch cannot express yet: (phase, why).
LATER: dict[int, tuple[str, str]] = {
    14: ("2", "pages and their OK (send back, version 2) arrive with Product"),
    19: ("2 and 3", "only the General preset exists; Product and Engineering presets arrive with their phases"),
    20: ("2", "waking the Product Owner when Spec empties (C10) arrives with Product"),
    22: ("2", "factories and the Done count arrive with Product"),
    26: ("2", "on-enter filing into a pipeline arrives with Product (pipeline_settings refuses it until then)"),
    27: ("2", "Jev and the production version check arrive with Product"),
    28: ("3", "GitHub: commits and review state"),
    29: ("3", "GitHub: PRs and Merge"),
    30: ("3", "GitHub: the merge queue"),
    31: ("3", "GitHub: the merge queue"),
    33: ("3", "GitHub: releases"),
    34: ("3", "GitHub: Checks pass"),
    35: ("3", "GitHub: commits during a run"),
    40: ("3", "Checks pass is a GitHub requirement"),
    44: ("3", "Checks pass is a GitHub requirement"),
    49: ("1 (validation) / later (build doc)", "no rule check on asks is built; the build doc puts it after phase 1"),
    50: ("3", "GitHub: a closed task's PR"),
    51: ("3", "GitHub: commits after an OK"),
}


def scenario(number: int, name: str) -> Callable[[Callable[[Rig], None]], Callable[[Rig], None]]:
    def add(fn: Callable[[Rig], None]) -> Callable[[Rig], None]:
        assert number not in SCENARIOS, number
        SCENARIOS[number] = Scenario(number, name, fn)
        return fn

    return add


def bug(name: str, what: str) -> Callable[[Callable[[Rig], None]], Callable[[Rig], None]]:
    def add(fn: Callable[[Rig], None]) -> Callable[[Rig], None]:
        BUGS[name] = Scenario(0, what, fn)
        return fn

    return add


def extra(name: str, what: str) -> Callable[[Callable[[Rig], None]], Callable[[Rig], None]]:
    def add(fn: Callable[[Rig], None]) -> Callable[[Rig], None]:
        CHECKS[name] = Scenario(0, what, fn)
        return fn

    return add


# Scenarios a Product and Engineering case passes for (software_scenarios.CASES), by the case's name.
ELSEWHERE: dict[int, str] = {
    32: "webhooks-out-of-order",
}


def numbers_covered() -> list[int]:
    return sorted([*SCENARIOS, *LATER, *ELSEWHERE])


@contextmanager
def rig(seed: int = 1) -> Iterator[Rig]:
    with World(seed) as world:
        yield Rig(world)


def run(case: Scenario, seed: int = 1) -> None:
    with rig(seed) as r:
        case.run(r)


# ----------------------------------------------------------------------------- the ladder
LADDER_GAPS = [120, 900, 3600]


def _check_ladder(r: Rig, task: str, runs: list[tuple[str, float, str]], owner: str) -> None:
    """Scenario 1's shape: one start and 3 tries for the owner, 3 for the manager bot, then one person's list."""
    expect(len(runs) == 7, f"{len(runs)} runs, not 7: {runs}")
    expect([b for b, _, _ in runs] == [owner] * 4 + [MANAGER] * 3, f"who ran: {runs}")
    gaps = [g for _, g, _ in runs[1:4]]
    expect(
        all(want <= got <= want + 10 for want, got in zip(LADDER_GAPS, gaps, strict=True)),
        f"the owner was woken {gaps} seconds after each stop, not 2 min, 15 min and 1 h",
    )
    expect(r.visit(task).get("level") == "person", "the ladder did not end at a person")
    person = r.visit(task).get("person_id")
    expect(person and ("task", task) in r.needs_tasks(person), f"the task is not in {person}'s Needs you")
    expect(r.runs(task) == 7, f"{r.runs(task)} runs in all")
    before = r.runs(task)
    r.later(hours=6)
    expect(r.queued(task) == 0 and r.runs(task) == before, "a bot was woken after a person had it")


@scenario(1, "A bot stops early with nothing changed")
def s01(r: Rig) -> None:
    task = r.task(ANA, "Write the launch note", assignee=BUILDER)
    _check_ladder(r, task, r.ladder(task), BUILDER)


@scenario(2, "A bot comments 'working on it' and stops")
def s02(r: Rig) -> None:
    H = _h()
    task = r.task(ANA, "Write the launch note", assignee=BUILDER)

    def comment(bot: str) -> None:
        r.ok(bot, lambda c: H.task_comment(c, bot, task, "Working on it."), "the bot comments")

    _check_ladder(r, task, r.ladder(task, comment), BUILDER)


@scenario(42, "A bot @s a person in the conversation and stops")
def s42(r: Rig) -> None:
    H = _h()
    task = r.task(ANA, "Write the launch note", assignee=BUILDER)
    turns: list[list[tuple[str, str | None]]] = []

    def mention(bot: str) -> None:
        r.ok(bot, lambda c: H.task_comment(c, bot, task, "@ben can you look at the intro?"), "the bot @s Ben")
        turns.append(r.waits(task))

    runs = r.ladder(task, mention)
    expect(turns and turns[0] == [("bot", BUILDER)], f"after the @ the task waits on {turns[:1]}")
    expect(not any(n.get("task_id") == task for n in r.needs(BEN)), "a bot's @ put the task in Ben's Needs you")
    _check_ladder(r, task, runs, BUILDER)


@scenario(3, "A wake is lost")
def s03(r: Rig) -> None:
    task = r.task(ANA, "Order the stickers", assignee=BUILDER)
    expect(r.queued(task, BUILDER) == 1, "the first wake was not queued")
    r.raw(
        lambda c: c.execute(
            "DELETE FROM jobs WHERE message_id IN (SELECT id FROM messages WHERE wake_key LIKE ?)", (task + ":%",)
        )
    )
    passes = 0
    while passes < 30:
        r.later(10)
        passes += 1
        expect(r.queued(task, BUILDER) <= 1, "two wakes queued for one turn")
        if r.queued(task, BUILDER):
            break
    back = r.wakes(task, BUILDER)[-1]
    expect(passes == 1, f"the wake came back {10 * passes} seconds later, not on the next pass, as {back[2]!r}")
    expect(r.visit(task).get("tries") == 0, "the lost wake was counted as a try")


@scenario(4, "A crash at any point inside a move")
def s04(r: Rig) -> None:
    from backend import answers as A
    from backend import task_engine as E

    from .sim import db_print

    tasks = [r.task(ANA, f"Draft {n}", WORK, assignee=WRITER) for n in (1, 2)]
    for t in tasks:
        r.to_deck(t)
    r.check()
    task = tasks[0]
    expect(r.stage_of(task) == "Draft", f"the task is in {r.stage_of(task)}")
    rid = r.requirement(task, "owner_done")
    version = r.row(task)["version"]
    crashes = 0
    for n in range(2000):
        before = r.w.read(db_print)
        Wd.METER.arm(n)
        try:
            out = r.act(WRITER, lambda c: A.answer(c, task, rid, WRITER, "done", seen_version=version))
        except Crash:
            crashes += 1
            r.w.restart()
            expect(r.w.read(db_print) == before, f"a crash before statement {n + 1} left part of the move")
            expect(r.stage_of(task) == "Draft", "a crash moved the task part way")
            continue
        finally:
            Wd.METER.disarm()
        expect(not isinstance(out, Refusal), f"the move was refused: {out!r}")
        break
    expect(crashes > 20, f"only {crashes} crash points: the move is not being crashed")
    expect(r.stage_of(task) == "Build", f"after the crashes the task is in {r.stage_of(task)}")
    moves = [e for e in r.events(task) if e["kind"] == "move" and e["new"] == "work-build"]
    expect(len(moves) == 1, f"{len(moves)} moves into Build")
    visits = r.w.read(
        lambda c: c.execute("SELECT COUNT(*) FROM visits WHERE task_id=? AND left_at IS NULL", (task,)).fetchone()[0]
    )
    expect(visits == 1, f"{visits} open visits")
    entry = [w for w in r.wakes(task) if "in Build" in w[2]]
    expect(len(entry) == 1 and entry[0][1] == WRITER, f"wakes for the new visit: {entry}")
    r.check()
    del E


@scenario(5, "Two clients move the same task")
def s05(r: Rig) -> None:
    from backend import task_engine as E

    task = r.task(ANA, "Plan the offsite", WORK, assignee=WRITER)
    r.to_deck(task)
    r.check()
    version = r.row(task)["version"]
    r.ok(ANA, lambda c: E.move(c, task, "On deck", ANA, "Not yet", version), "Ana moves it back")
    out = r.refused(
        WRITER, lambda c: E.move(c, task, E.CLOSED, WRITER, "Duplicate", version), "task_changed", "the second move"
    )
    expect("changed since you looked" in out.detail, f"the refusal says {out.detail!r}")
    expect(r.row(task)["status"] == "open", "the second move was applied")


@scenario(6, "A direct database write to a task's stage")
def s06(r: Rig) -> None:
    task = r.task(ANA, "Plan the offsite", WORK, assignee=WRITER)
    try:
        r.raw(lambda c: c.execute("UPDATE tasks SET stage_id='work-ship' WHERE id=?", (task,)))
    except sqlite3.DatabaseError as error:
        expect("only through move()" in str(error), f"refused with {error}")
    else:
        raise ScenarioFailed("the direct write was taken")
    expect(r.stage_of(task) == "Ideas", "the stage changed")


@scenario(7, "A bot with 6 tasks runs out of credit")
def s07(r: Rig) -> None:
    tasks = [r.task(ANA, f"Chore {n}", assignee=BUILDER) for n in range(6)]
    other = r.task(ANA, "Writer's chore", assignee=WRITER)
    r.raw(
        lambda c: c.execute(
            "INSERT OR REPLACE INTO registry_metadata(key, value) VALUES('cloud-access', ?)",
            ('{"access": "frozen", "reason": "out_of_credit"}',),
        )
    )
    r.check()
    for t in tasks:
        expect(r.waits(t) == [("paused", BUILDER)], f"a task waits on {r.waits(t)}")
        expect("out of credit" in r.wait_lines(t)[0], f"the line is {r.wait_lines(t)}")
    expect(r.waits(other) == [("bot", WRITER)], "a bot on its own subscription stopped too")
    rows = [n for p in Wd.PEOPLE for n in r.needs(p) if n["kind"] == "paused"]
    expect(len(rows) == 1 and sorted(rows[0]["tasks"]) == sorted(tasks), f"paused rows: {rows}")
    queued = sum(r.queued(t) for t in tasks)
    for t in tasks:
        r.end(r.start(t, BUILDER))
    r.later(hours=3)
    expect(sum(r.queued(t) for t in tasks) == 0, f"a bot out of credit was woken ({queued} queued before)")
    r.raw(lambda c: c.execute("DELETE FROM registry_metadata WHERE key='cloud-access'"))
    r.later(minutes=5)
    expect(all(r.waits(t) == [("bot", BUILDER)] for t in tasks), "the tasks did not carry on")
    expect(sum(r.queued(t) for t in tasks) >= 1, "nothing woke the bot when it could run again")


@scenario(8, "A person answers while the bot is paused")
def s08(r: Rig) -> None:
    from backend import answers as A
    from backend import task_engine as E

    task = r.task(ANA, "Choose a venue", assignee=BUILDER)
    r.ran(task, BUILDER)
    version = r.row(task)["version"]
    out = r.ok(
        BUILDER,
        lambda c: E.add_requirement(c, task, BUILDER, "answer", BEN, "Which city?", seen_version=version),
        "the bot asks Ben",
    )
    rid = out["requirement_id"]
    r.raw(lambda c: c.execute("UPDATE bots SET state='paused' WHERE id='builder'"))
    r.ok(BEN, lambda c: A.answer(c, task, rid, BEN, "Lisbon"), "Ben answers")
    expect(r.queued(task, BUILDER) == 1, "the answer was not queued for the bot")
    r.later(hours=2)
    expect(r.waits(task) == [("paused", BUILDER)], f"while paused the task waits on {r.waits(task)}")
    expect(r.queued(task, BUILDER) == 1, "the answer's wake was dropped while the bot was paused")
    r.raw(lambda c: c.execute("UPDATE bots SET state='active' WHERE id='builder'"))
    r.check()
    wakes = r.wakes(task, BUILDER)
    expect(any("Lisbon" in w[2] for w in wakes), f"the bot's wakes: {wakes}")
    expect(r.ran(task, BUILDER) == 1, "the answer was not delivered when the bot could run")


@scenario(9, "11 refusals about tasks for one bot")
def s09(r: Rig) -> None:
    from backend import task_engine as E

    tasks = [r.task(ANA, f"Draft {n}", WORK, assignee=WRITER) for n in range(4)]
    for t in tasks:
        r.to_deck(t)
    r.check()
    full = [t for t in tasks if r.stage_of(t) == "Draft"]
    waiting = [t for t in tasks if r.stage_of(t) == "On deck"]
    expect(len(full) == 2 and len(waiting) == 2, "Draft did not fill to its limit")
    rules: list[str] = []
    for n in range(11):
        t = full[n % 2]
        version = r.row(t)["version"]
        tries: list[Callable[[sqlite3.Connection], Any]] = [
            lambda c, t=t, version=version: E.move(c, t, E.NEXT, WRITER, None, version),  # not met
            lambda c, t=t, version=version: E.move(c, t, E.NEXT, WRITER, "Go", version - 1),  # changed since it looked
            lambda c, t=t, version=version: E.move(
                c, t, "Ship", WRITER, "Skip ahead", version
            ),  # past a people-only OK
            lambda c: E.move(c, waiting[0], "Draft", WRITER, "Make room", r.row(waiting[0])["version"]),  # no room
        ]
        out = r.act(WRITER, tries[n % len(tries)])
        expect(isinstance(out, Refusal), f"write {n} was taken")
        rules.append(out.rule)
    expect({"task_not_met", "task_changed", "task_needs_person", "task_stage_full"} <= set(rules), f"refusals: {rules}")
    kept = r.w.read(lambda c: c.execute("SELECT COUNT(*) FROM refusals WHERE actor=?", (WRITER,)).fetchone()[0])
    expect(kept == 11, f"{kept} refusals were recorded, not 11")  # recorded as the server records them
    expect(r.bot_state(WRITER) == "active", f"after 11 refusals the bot is {r.bot_state(WRITER)}")
    from backend import task_wakes

    expect(r.w.read(lambda c: task_wakes.cannot_run(c, WRITER)) is None, "the bot cannot run after its refusals")


@scenario(10, "A bot snoozes with no end date")
def s10(r: Rig) -> None:
    from backend import task_engine as E

    task = r.task(ANA, "Plant the bulbs", assignee=BUILDER)
    r.ran(task, BUILDER)
    version = r.row(task)["version"]
    r.ok(BUILDER, lambda c: E.snooze(c, task, BUILDER, "Parked until spring", None, version), "the bot snoozes")
    expect(r.waits(task)[0] == ("snoozed", BUILDER), f"waits {r.waits(task)}")
    from backend import waiting_on as W

    head = r.w.read(lambda c: W.waiting_on(c, task))[0]
    expect(head.until is None and head.line == "Parked until spring", f"not On ice: {head}")  # On ice: no end date
    told = [w for w in r.wakes(task, MANAGER) if w[0].startswith("on_ice")]
    expect(len(told) == 1, f"the manager bot was told {len(told)} times")
    expect(r.queued(task, MANAGER) == 0, "telling the manager bot started a run")
    r.later(days=10)
    expect(len([w for w in r.wakes(task, MANAGER) if w[0].startswith("on_ice")]) == 1, "told again")
    expect(r.queued(task) == 0, "something woke for a task on ice")


@scenario(11, "A snoozed task in a full stage")
def s11(r: Rig) -> None:
    from backend import task_engine as E

    tasks = []
    for n in range(4):
        t = r.task(ANA, f"Draft {n}", WORK, assignee=WRITER)
        r.to_deck(t)
        r.later(30)
        tasks.append(t)
    expect([r.stage_of(t) for t in tasks] == ["Draft", "Draft", "On deck", "On deck"], "Draft did not fill in order")
    expect(r.waits(tasks[2])[0][0] == "room", f"the third waits on {r.waits(tasks[2])}")
    version = r.row(tasks[0])["version"]
    r.ok(
        WRITER,
        lambda c: E.snooze(
            c, tasks[0], WRITER, "Waiting on the printer", r.w.now()[:4] + "-12-31T00:00:00.000000Z", version
        ),
        "the writer snoozes one",
    )
    r.check()
    expect(r.stage_of(tasks[2]) == "Draft", "the oldest waiting task did not take the slot")
    expect(r.stage_of(tasks[3]) == "On deck", "the newest waiting task jumped the queue")


@scenario(12, "A bot tries to close a person's request")
def s12(r: Rig) -> None:
    from backend import task_engine as E

    task = r.task(BEN, "Book the flights", assignee=BUILDER)
    version = r.row(task)["version"]
    out = r.act(BUILDER, lambda c: E.move(c, task, E.CLOSED, BUILDER, "Not possible", version))
    expect(isinstance(out, Refusal), "a bot closed a person's request (C8)")
    expect("ask" in out.detail.lower(), f"the refusal does not say to ask: {out.detail}")


@scenario(13, "A held action with no task: a General task is filed for the person")
def s13(r: Rig) -> None:
    from backend import task_asks as K

    raise_ = {"usd": 40, "what": "Launch ads budget"}
    asked = r.ok(
        WRITER,
        lambda c: K.ask_ok(
            c,
            WRITER,
            ANA,
            "Raise the launch ads budget by $40?",
            shows=raise_,
            subject="budget_raise:launch-ads",
            content=raise_,
        ),
        "the writer asks Ana, an owner, for a budget raise",
    )
    task, rid = str(asked["task_id"]), str(asked["requirement_id"])
    row = r.row(task)
    expect(row["pipeline_id"] == GENERAL, f"the filed task is in {row['pipeline_id']}, not General (C11)")
    expect(row["owner_id"] == WRITER, f"the filed task is owned by {row['owner_id']}, not the bot that asked")
    expect((ANA in {who for _, who in r.waits(task)}), f"the task does not wait on Ana: {r.waits(task)}")
    expect(any(t == task for _, t in r.needs_tasks(ANA)), "the ask is not in Ana's Needs you")
    # The bot never OKs its own ask, and asking again with the same content is the same ask.
    r.refused(
        WRITER,
        lambda c: K.give_ok(c, WRITER, rid, K.content_hash(raise_), own_click=True),
        "identity",
        "the writer OKs its own budget raise",
    )
    again = r.ok(
        WRITER,
        lambda c: K.ask_ok(
            c,
            WRITER,
            ANA,
            "Raise the launch ads budget by $40?",
            shows=raise_,
            subject="budget_raise:launch-ads",
            content=raise_,
        ),
        "the writer asks again",
    )
    expect(again["task_id"] == task and again["requirement_id"] == rid, f"asking again filed another: {again}")
    r.check()


REVIEW_RECORD: dict[str, Any] = {
    "name": "Reviews",
    "goal": "Drafts reviewed by a bot.",
    "prefix": "R",
    "manager_id": MANAGER,
    "stages": [
        {"name": "Write", "owner": "assignee", "requirements": [{"kind": "owner_done"}]},
        {
            "name": "Review 1",
            "owner": "assignee",
            "requirements": [{"kind": "ok", "person_id": REVIEWER, "line": "Reviewer checks it"}],
        },
        {"name": "Publish", "owner": "assignee", "requirements": [{"kind": "owner_done"}]},
    ],
}


@scenario(15, "A bot pushes a task past Review 1 with a reason")
def s15(r: Rig) -> None:
    from backend import task_engine as E

    pid = r.pipeline(json.loads(json.dumps(REVIEW_RECORD)))["id"]
    task = r.task(ANA, "Blog post", pid, assignee=WRITER)
    expect(r.say_done(task, WRITER) is not None, "the writer could not say done")
    r.check()
    expect(r.stage_of(task) == "Review 1", f"the task is in {r.stage_of(task)}")
    version = r.row(task)["version"]
    r.refused(
        WRITER, lambda c: E.move(c, task, E.NEXT, WRITER, None, version), "task_not_met", "a push past with no reason"
    )
    r.ok(
        WRITER,
        lambda c: E.move(c, task, E.NEXT, WRITER, "The reviewer is away and this is a typo fix", version),
        "the push past",
    )
    expect(r.stage_of(task) == "Publish", "it did not move")
    moves = [e for e in r.events(task) if e["kind"] == "move" and e["data"].get("move") == "pushed_past"]
    expect(len(moves) == 1 and moves[0]["note"].startswith("The reviewer is away"), f"history: {moves}")
    told = [w for w in r.wakes(task, MANAGER) if w[0] == "pushed_past"]
    expect(len(told) == 1 and "Pushed past" in told[0][2], f"the manager bot was told: {told}")
    expect(r.queued(task, MANAGER) == 0, "telling the manager bot started a run")


@scenario(16, "A bot tries to push past a people-only OK")
def s16(r: Rig) -> None:
    from backend import answers as A
    from backend import task_engine as E

    task = r.task(ANA, "Spring catalogue", WORK, assignee=WRITER)
    r.to_deck(task)
    r.check()
    r.say_done(task, WRITER)
    version = r.row(task)["version"]
    r.ok(WRITER, lambda c: E.move(c, task, E.NEXT, WRITER, "Done another way", version), "push past Linked work done")
    expect(r.stage_of(task) == "Review", f"the task is in {r.stage_of(task)}")
    rid = r.requirement(task, "ok")
    r.ok(
        REVIEWER,
        lambda c: A.answer(c, task, rid, REVIEWER, "ok", seen_version=r.row(task)["version"]),
        "the reviewer OKs",
    )
    version = r.row(task)["version"]
    out = r.refused(
        WRITER,
        lambda c: E.move(c, task, E.NEXT, WRITER, "Ben is away", version),
        "task_needs_person",
        "the bot's push past",
    )
    expect("needs a person" in out.detail, out.detail)
    expect(r.stage_of(task) == "Review", "it moved")
    r.ok(ANA, lambda c: E.move(c, task, E.NEXT, ANA, "Ben is away, I checked it"), "a person pushes past")
    expect(r.stage_of(task) == "Ship", "a person could not push past")


@scenario(17, "Anyone tries to push past a requirement nobody may push past (stand-in for PR merged)")
def s17(r: Rig) -> None:
    from backend import task_engine as E

    task = r.task(ANA, "Ship the catalogue", WORK, assignee=WRITER)
    r.to_deck(task)
    r.check()
    r.ok(ANA, lambda c: E.move(c, task, "Ship", ANA, "Already reviewed"), "Ana drags it to Ship")
    for who in (WRITER, ANA):
        version = r.row(task)["version"]
        out = r.refused(
            who,
            lambda c, who=who, version=version: E.move(c, task, E.DONE, who, "Shipped by hand", version),
            "task_no_push_past",
            f"{who} pushes past",
        )
        expect("Move back or close" in out.detail, f"the refusal does not offer Move back and Close: {out.detail}")
    version = r.row(task)["version"]
    r.ok(
        WRITER,
        lambda c: E.move(c, task, "Review", WRITER, "Needs another look", version),
        "Move back is offered and works",
    )
    r.ok(ANA, lambda c: E.move(c, task, E.CLOSED, ANA, "Dropped"), "Close is offered and works")


@scenario(18, "A task is dragged two stages ahead")
def s18(r: Rig) -> None:
    from backend import task_engine as E

    task = r.task(ANA, "Rewrite the guide", WORK, assignee=WRITER)
    r.to_deck(task)
    r.check()
    expect(r.stage_of(task) == "Draft", r.stage_of(task))
    r.ok(ANA, lambda c: E.move(c, task, "Review", ANA, "Already written elsewhere"), "Ana drags it to Review")
    move = [e for e in r.events(task) if e["kind"] == "move"][-1]
    pushed = [p["name"] for p in move["data"].get("pushed_past", [])]
    expect(len(pushed) == 2, f"pushed past {pushed}, not Draft's and Build's requirements")
    expect(move["note"] == "Already written elsewhere", f"the reason is {move['note']!r}")
    answers = r.w.read(lambda c: c.execute("SELECT answer, notes FROM answers WHERE answer='pushed_past'").fetchall())
    expect(answers and all(n == "Already written elsewhere" for _, n in answers), f"answers: {answers}")


@scenario(21, "Close a task with its stage's requirement unmet")
def s21(r: Rig) -> None:
    from backend import task_engine as E

    # A bot asked for it: a bot may close it (C8 keeps a person's request for a person to close).
    task = r.task(MANAGER, "Spec the export", WORK, assignee=WRITER)
    r.to_deck(task)
    r.check()
    version = r.row(task)["version"]
    r.refused(
        WRITER, lambda c: E.move(c, task, E.CLOSED, WRITER, None, version), "task_why", "a bot closes with no reason"
    )
    r.ok(WRITER, lambda c: E.move(c, task, E.CLOSED, WRITER, "Covered by the import work", version), "the close")
    row = r.row(task)
    expect(row["status"] == "closed", f"status {row['status']}")
    close = [e for e in r.events(task) if e["kind"] == "move"][-1]
    expect(close["note"] == "Covered by the import work", f"the reason is {close['note']!r}")


def _filed(r: Rig) -> tuple[str, str]:
    """A Work task in Build that filed one General task for the Builder."""
    task = r.task(ANA, "Export feature", WORK, assignee=WRITER)
    r.to_deck(task)
    r.check()
    r.say_done(task, WRITER)
    r.check()
    expect(r.stage_of(task) == "Build", f"the task is in {r.stage_of(task)}")
    sub = r.task(WRITER, "Build the export", GENERAL, assignee=BUILDER, parent=task)
    expect(r.waits(task) == [("tasks", sub)], f"the task waits on {r.waits(task)}")
    return task, sub


@scenario(23, "Close the task filed by a task in Build")
def s23(r: Rig) -> None:
    from backend import task_engine as E

    task, sub = _filed(r)
    r.ran(task, WRITER)
    version = r.row(sub)["version"]
    r.ok(
        BUILDER,
        lambda c: E.move(c, sub, E.CLOSED, BUILDER, "The vendor's API is gone", version),
        "the Builder closes it",
    )
    r.check()
    expect(r.stage_of(task) == "Build", "the task moved on although its filed task was closed")
    expect(r.waits(task) == [("bot", WRITER)], f"it is not the owner's turn: {r.waits(task)}")
    told = [w for w in r.wakes(task, WRITER) if w[0].startswith("filed:")]
    expect(len(told) == 1 and "Closed" in told[0][2], f"the owner was told: {told}")
    lines = " ".join(r.wait_lines(task))
    expect("closed" in lines.lower(), f"the task does not show the filed task closed: {lines}")


@scenario(24, "Close a task that has an open filed task")
def s24(r: Rig) -> None:
    from backend import task_engine as E

    task, sub = _filed(r)
    version = r.row(task)["version"]
    r.ok(ANA, lambda c: E.move(c, task, E.CLOSED, ANA, "Customer cancelled", version), "close the task")
    row = r.row(sub)
    expect(row["status"] == "closed", f"the filed task is {row['status']}")
    note = [e for e in r.events(sub) if e["kind"] == "move"][-1]["note"]
    expect(note and "Export feature" in note, f"the filed task's note is {note!r}")
    expect(r.queued(sub) == 0, "a wake is still queued for the closed filed task")


@scenario(25, "Save a pipeline where A files into B and B into A")
def s25(r: Rig) -> None:
    from backend import pipeline_settings as PS
    from backend import task_engine as E

    def record(name: str, into: str | None) -> dict[str, Any]:
        stage: dict[str, Any] = {"name": "Hand off", "owner": "assignee", "requirements": [{"kind": "linked_done"}]}
        if into:
            stage["on_enter"] = {"file_into": into}
        return {
            "id": name.lower(),
            "name": name,
            "goal": f"{name} work.",
            "prefix": name[0],
            "manager_id": MANAGER,
            "stages": [stage],
        }

    r.pipeline(record("Alpha", None))
    r.pipeline(record("Beta", "alpha"))
    looped = record("Alpha", "beta")
    r.refused(E.SYSTEM, lambda c: PS.save(c, E.SYSTEM, looped), "task_settings", "the loop")
    problems = r.w.read(lambda c: PS.check(c, looped))
    expect(any("would loop" in p for p in problems), f"the loop is not named: {problems}")


@scenario(36, "A run is held or cut off")
def s36(r: Rig) -> None:
    task = r.task(ANA, "Count the stock", assignee=BUILDER)
    runs = r.start(task, BUILDER)
    expect(runs, "the first wake had no run")
    r.later(60)
    expect(r.queued(task, BUILDER) == 0 and r.wakes(task, BUILDER)[-1][0] == "owner:0", "woken while its run was live")
    r.later(30)  # nothing renewed the lease: the server expires the run (execution.expire), though nothing reported it
    from backend import task_wakes

    expect(not r.w.read(lambda c: task_wakes.live_run(c, r.row(task), BUILDER)), "a lapsed run still counts as on it")
    r.end(runs, "expired")
    r.heartbeat(130)
    wakes = r.wakes(task, BUILDER)
    expect(len(wakes) == 2 and "cut off" in wakes[-1][2], f"the bot's wakes: {wakes}")
    held = r.start(task, BUILDER)
    r.end(held, "interrupted")  # held at the 60-minute limit, or stopped by its computer
    r.heartbeat(900)
    wakes = r.wakes(task, BUILDER)
    expect(len(wakes) == 3 and "cut off" in wakes[-1][2], f"after a held run: {wakes}")


@scenario(37, "A bot's fourth snooze of a task in one stage")
def s37(r: Rig) -> None:
    from backend import task_engine as E

    task = r.task(ANA, "Renew the domain", assignee=BUILDER)
    for n in range(3):
        version = r.row(task)["version"]
        until = _h().shift(r.w.now(), seconds=600)
        r.ok(
            BUILDER,
            lambda c, n=n, until=until, version=version: E.snooze(
                c, task, BUILDER, f"Registrar is down ({n + 1})", until, version
            ),
            f"snooze {n + 1}",
        )
        r.later(minutes=11)
        expect(not r.row(task).get("snooze"), "the snooze did not end")
    version = r.row(task)["version"]
    out = r.ok(
        BUILDER,
        lambda c: E.snooze(c, task, BUILDER, "Still down", _h().shift(r.w.now(), seconds=600), version),
        "snooze 4",
    )
    expect(not r.row(task).get("snooze"), "the fourth snooze was taken")
    expect("manager" in str(out.get("note", "")).lower(), f"the note says {out.get('note')!r}")
    expect(r.visit(task).get("level") == "manager", f"level {r.visit(task).get('level')}")
    r.later(10)
    expect(r.queued(task, MANAGER) == 1, "the manager bot was not woken")


@scenario(38, "A sub-task six levels deep")
def s38(r: Rig) -> None:
    parent = r.task(ANA, "Level 0", assignee=BUILDER)
    for level in range(1, 6):
        parent = r.task(BUILDER, f"Level {level}", assignee=BUILDER, parent=parent)
    out = r.w.new_task(BUILDER, "Level 6", GENERAL, assignee=BUILDER, parent=parent)
    expect(isinstance(out, Refusal) and out.rule == "task_too_deep", f"the sixth level: {out!r}")


@scenario(39, "A task is reassigned while the old owner is running")
def s39(r: Rig) -> None:
    from backend import answers as A
    from backend import task_engine as E

    task = r.task(ANA, "Fix the invoice", assignee=BUILDER)
    r.ran(task, BUILDER)
    seen = r.row(task)["version"]
    out = r.ok(
        BUILDER,
        lambda c: E.add_requirement(c, task, BUILDER, "answer", BEN, "Which client?", seen_version=seen),
        "the bot asks",
    )
    runs = r.start(task, BUILDER) or r.raw(lambda c: [])
    r.later(minutes=3)
    runs = r.start(task, BUILDER)
    r.ok(BEN, lambda c: A.answer(c, task, out["requirement_id"], BEN, "Acme"), "Ben answers while the bot runs")
    expect(r.queued(task, BUILDER) >= 1, "nothing was queued for the bot")
    seen = r.row(task)["version"]
    r.ok(ANA, lambda c: E.reassign(c, task, WRITER, ANA, "Writer knows the client"), "Ana reassigns")
    expect(r.queued(task, BUILDER) == 0, "the old owner's queued wake was not dropped")
    rid = r.requirement(task, "owner_done")
    r.refused(
        BUILDER,
        lambda c: A.answer(c, task, rid, BUILDER, "done", seen_version=seen),
        "task_changed",
        "the old owner's next command",
    )
    r.end(runs)
    expect(r.queued(task, WRITER) == 1, "the new owner was not woken")


@scenario(41, "A task with an open ask moves back")
def s41(r: Rig) -> None:
    from backend import task_engine as E

    task = r.task(ANA, "Choose the font", WORK, assignee=WRITER)
    r.to_deck(task)
    r.check()
    version = r.row(task)["version"]
    r.ok(
        WRITER,
        lambda c: E.add_requirement(c, task, WRITER, "ok", BEN, "OK to use Inter?", seen_version=version),
        "the ask",
    )
    expect(any(n["kind"] == "ask" and n["task_id"] == task for n in r.needs(BEN)), "the ask is not in Ben's list")
    version = r.row(task)["version"]
    r.ok(WRITER, lambda c: E.move(c, task, "On deck", WRITER, "Brief changed", version), "move back")
    expect(not any(n.get("task_id") == task for n in r.needs(BEN)), "the ask stayed in Ben's list")


@scenario(43, "A person @s another person on a task")
def s43(r: Rig) -> None:
    H = _h()
    task = r.task(ANA, "Choose the venue", assignee=BUILDER)
    r.ok(ANA, lambda c: H.task_comment(c, ANA, task, "@ben can you check the dates?"), "Ana @s Ben")
    expect(r.waits(task)[0] == ("bot", BUILDER), f"the task's turn changed: {r.waits(task)}")
    expect(
        any(n["kind"] == "mention" and n.get("task_id") == task for n in r.needs_you(BEN)),
        "the @ is not in Ben's Needs you (C28)",
    )


@scenario(45, "A person adds an OK from someone to one task")
def s45(r: Rig) -> None:
    from backend import task_engine as E

    task = r.task(ANA, "Price the bundle", WORK, assignee=WRITER)
    r.to_deck(task)
    r.check()
    r.ok(ANA, lambda c: E.add_requirement(c, task, ANA, "ok", BEN, "OK from Ben"), "Ana adds OK from Ben")
    expect([n["kind"] for n in r.needs(BEN) if n.get("task_id") == task] == ["ask"], "it is not in Ben's list once")
    r.say_done(task, WRITER)
    r.later(minutes=5)
    expect(r.stage_of(task) == "Draft", "the task left its stage without Ben's OK")
    r.ok(ANA, lambda c: E.move(c, task, E.NEXT, ANA, "Ben is on leave"), "Ana pushes past")
    expect(not any(n.get("task_id") == task for n in r.needs(BEN)), "the OK stayed in Ben's list after the task left")


@scenario(46, "A bot adds a second question to a task")
def s46(r: Rig) -> None:
    from backend import task_engine as E

    task = r.task(ANA, "Plan the menu", assignee=BUILDER)
    for line in ("Vegetarian?", "How many guests?"):
        version = r.row(task)["version"]
        r.ok(
            BUILDER,
            lambda c, line=line, version=version: E.add_requirement(
                c, task, BUILDER, "answer", BEN, line, seen_version=version
            ),
            line,
        )
    asks = [n for n in r.needs(BEN) if n.get("task_id") == task]
    expect(len(asks) == 1 and "How many guests?" in str(asks[0]["line"]), f"Ben's list: {asks}")
    open_ = r.w.read(
        lambda c: c.execute(
            "SELECT COUNT(*) FROM requirements WHERE added_by_id=? AND withdrawn_at IS NULL AND visit_id IS NOT NULL",
            (BUILDER,),
        ).fetchone()[0]
    )
    expect(open_ == 1, f"{open_} open asks from one bot")


def _ask(r: Rig, task: str, line: str = "OK to send?") -> str:
    from backend import task_engine as E

    version = r.row(task)["version"]
    out = r.ok(WRITER, lambda c: E.add_requirement(c, task, WRITER, "ok", BEN, line, seen_version=version), "the ask")
    return str(out["requirement_id"])


@scenario(47, "A thumbs down with Send back on an ask")
def s47(r: Rig) -> None:
    from backend import answers as A

    task = r.task(ANA, "Email the members", assignee=WRITER)
    rid = _ask(r, task)
    r.ok(BEN, lambda c: A.thumbs_down(c, task, rid, BEN, "Just send it, no need to ask"), "the thumbs down")
    expect(not any(n.get("task_id") == task for n in r.needs(BEN)), "the ask stayed in Ben's list")
    told = [w for w in r.wakes(task, WRITER) if w[0].startswith("thumbs_down")]
    expect(len(told) == 1 and "Just send it, no need to ask" in told[0][2], f"the bot got: {told}")


@scenario(48, "A third thumbs down on one bot's asks in 30 days (the count; the sheet is a screen)")
def s48(r: Rig) -> None:
    from backend import answers as A

    counts = []
    for n in range(4):
        task = r.task(ANA, f"Mailing {n}", assignee=WRITER)
        rid = _ask(r, task)
        r.ok(
            BEN,
            lambda c, task=task, rid=rid: A.thumbs_down(c, task, rid, BEN, "Don't ask about mailings"),
            f"thumbs down {n}",
        )
        counts.append(r.w.read(lambda c: A.thumbs_down_count(c, WRITER)))
        if n == 0:
            r.w.shift(31 * 86400)  # the first is more than 30 days old when the others come
    expect(counts == [1, 1, 2, 3], f"counts {counts}")


@scenario(52, "A linked task is deleted")
def s52(r: Rig) -> None:
    from backend import task_delete

    task, sub = _filed(r)
    r.ok(ANA, lambda c: task_delete.delete_tasks(c, [sub], apply=True, keep_work=True), "delete the filed task")
    expect(not r.w.task_row(sub), "the filed task is still there")
    r.check()
    expect(("tasks", sub) not in r.waits(task), f"the task still waits on the deleted task: {r.waits(task)}")
    expect(r.waits(task) == [("bot", WRITER)], f"the task waits on {r.waits(task)}")


# ----------------------------------------------------------------------------- bugs the seeded runs found


@extra("runs-outside-ladder", "A snooze ends while another bot is asked: still at most 7 runs without a change")
def c_runs_outside_ladder(r: Rig) -> None:
    from backend import task_engine as E

    task = r.task(ANA, "Approve the copy", assignee=WRITER)
    r.ran(task, WRITER)
    version = r.row(task)["version"]
    r.ok(
        WRITER,
        lambda c: E.add_requirement(c, task, WRITER, "answer", REVIEWER, "Is the copy right?", seen_version=version),
        "ask the reviewer",
    )
    r.ran(task, REVIEWER)  # the reviewer's ask wake: it stops without answering
    version = r.row(task)["version"]
    r.ok(
        WRITER,
        lambda c: E.snooze(c, task, WRITER, "Waiting on legal", _h().shift(r.w.now(), seconds=600), version),
        "snooze",
    )
    r.later(minutes=11)  # the snooze ends. Neither it nor its end is a change (R1): the reviewer's run above counts.
    runs = r.ladder(task)
    expect(len(runs) <= 6, f"{len(runs)} bot runs after the reviewer's one, without a change: {runs}")
    expect(all(bot != WRITER for bot, _, _ in runs), f"the writer, who asked and waits, was woken: {runs}")


@extra("file-into", "A stage that files into General files one linked task on enter, once per visit")
def c_file_into(r: Rig) -> None:
    from backend import task_engine as E

    r.pipeline(
        {
            "id": "handoff",
            "name": "Handoff",
            "goal": "Work handed to General.",
            "prefix": "H",
            "manager_id": MANAGER,
            "stages": [
                {"name": "Plan", "owner": "assignee", "requirements": [{"kind": "owner_done"}]},
                {
                    "name": "Hand off",
                    "owner": "assignee",
                    "on_enter": {"file_into": GENERAL},
                    "requirements": [{"kind": "linked_done"}],
                },
            ],
        }
    )
    task = r.task(ANA, "Hand it over", "handoff", assignee=WRITER)
    version = r.row(task)["version"]
    try:
        moved = r.act(WRITER, lambda c: E.move(c, task, E.NEXT, WRITER, "Planned in the call", version))
    except NotImplementedError as error:
        raise ScenarioFailed(f"the move raised {error!r} instead of being refused or done") from error
    expect(not isinstance(moved, Refusal), f"refused: {moved!r}")
    expect(r.stage_of(task) == "Hand off", f"the task is in {r.stage_of(task)}")
    filed = r.w.read(
        lambda c: c.execute(
            "SELECT t.pipeline_id, t.title FROM links l JOIN tasks t ON t.id = l.to_task_id WHERE l.from_task_id=? "
            "AND l.kind='handoff'",
            (task,),
        ).fetchall()
    )
    expect([tuple(f) for f in filed] == [(GENERAL, "Hand it over")], f"filed {[tuple(f) for f in filed]}")
    r.check()


# ----------------------------------------------------------------------------- extra checks
@extra("clock-back", "The clock goes back a little: nothing breaks and the ladder still ends at a person")
def c_clock_back(r: Rig) -> None:
    task = r.task(ANA, "Water the plants", assignee=BUILDER)
    for back in (5, 30, 90):
        r.ran(task, BUILDER)
        r.later(minutes=3)
        r.w.shift(-back)
        r.check()
    runs = r.ladder(task, hours=24)
    expect(r.visit(task).get("level") == "person", f"after the clock went back the ladder did not end: {runs}")
    expect(r.runs(task) <= 7, f"{r.runs(task)} runs")


@extra("held-run", "A run held for a check pauses its task, says so on Needs you, and the task carries on after")
def c_held_run(r: Rig) -> None:
    H = _h()
    task = r.task(ANA, "Write the launch note", assignee=BUILDER)
    r.ran(task, BUILDER, "expired")  # it may have acted: its job is uncertain
    found = r.w.read(lambda c: c.execute("SELECT id, job_id FROM attempts WHERE task_id=?", (task,)).fetchone())
    attempt, job = str(found[0]), str(found[1])
    r.raw(lambda c: H.event(c, H.KEEPER, "job.held", attempt, {"job_id": job, "tools": ["Bash"]}))
    r.later(hours=2)
    expect(r.waits(task) == [("paused", BUILDER)], f"the held task waits on {r.waits(task)}")
    expect(r.queued(task, BUILDER) == 0, "a wake was written that the bot cannot take")
    paused = [n for n in r.needs(BEN) if n["kind"] == "paused"]  # the builder's owner
    expect([n["tasks"] for n in paused] == [[task]], f"Needs you has {paused}")
    r.raw(lambda c: c.execute("UPDATE jobs SET state='canceled' WHERE id=?", (job,)))  # dismissed
    r.later(seconds=10)
    expect(r.waits(task) == [("bot", BUILDER)] and r.queued(task, BUILDER) == 1, f"after: {r.waits(task)}")


@extra("deleted-snoozed-task", "A snoozed task is deleted: its snooze ends with it and the check passes it by")
def c_deleted_snoozed(r: Rig) -> None:
    from backend import task_delete
    from backend import task_engine as E

    task, sub = _filed(r)
    version = r.row(sub)["version"]
    until = _h().shift(r.w.now(), seconds=600)
    r.ok(BUILDER, lambda c: E.snooze(c, sub, BUILDER, "Waiting on the vendor", until, version), "snooze the filed task")
    r.ok(ANA, lambda c: task_delete.delete_tasks(c, [sub], apply=True, keep_work=True), "delete it")
    left = r.w.read(
        lambda c: c.execute("SELECT COUNT(*) FROM snoozes WHERE task_id=? AND ended_at IS NULL", (sub,)).fetchone()[0]
    )
    expect(left == 0, "the deleted task's snooze is still open")
    r.later(minutes=11)  # the snooze's date passes: nothing raises
    expect(r.waits(task) == [("bot", WRITER)], f"the task that filed it waits on {r.waits(task)}")


@extra("crash-in-check", "A crash at any point inside a never-stuck pass rolls the whole pass back")
def c_crash_in_check(r: Rig) -> None:
    from backend import never_stuck as NS

    from .sim import db_print

    tasks = []
    for n in range(3):
        t = r.task(ANA, f"Draft {n}", WORK, assignee=WRITER)
        r.to_deck(t)
        r.w.shift(1)  # the order they wait in
        tasks.append(t)
    r.task(ANA, "Chore", assignee=BUILDER)
    crashes = 0
    for n in range(2000):
        before = r.w.read(db_print)
        Wd.METER.arm(n)
        try:
            with r.w.tx() as c:
                done = NS.check(c)
        except Crash:
            crashes += 1
            r.w.restart()
            expect(r.w.read(db_print) == before, f"a crash before statement {n + 1} of the pass left part of it")
            continue
        finally:
            Wd.METER.disarm()
        expect(done.get("moved"), f"the pass did nothing: {done}")
        break
    expect(crashes > 20, f"only {crashes} crash points")
    expect([r.stage_of(t) for t in tasks] == ["Draft", "Draft", "On deck"], "the pass did not fill Draft")
