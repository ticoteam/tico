"""A task that depends on another one ("Blocked by #304", backend/task_depends.py). Nov 13: 10 of 13 snoozed Engineering
tasks were really waiting on another task (#304, #311, #264, the fix for a red main) and slept to a guessed date, several
past the moment their blocker cleared. A dependency is a relation with no date: the task shows Blocked by the task, it is
nobody's turn, holds no slot, is never Late, and its owner is woken once, the moment its last open dependency ends."""

import sqlite3
from importlib import import_module

import pytest

from backend import never_stuck as NS
from backend import task_depends as D
from backend import task_engine as E
from backend import task_relations as TR
from backend import waiting_on as W
from backend.store import H
from backend.tests.helpers import engine as X

B = "bot:builder"


@pytest.fixture
def c(company):
    with company.transaction() as conn:
        yield conn


def build(c, limit=None, minutes=None):
    """Build, owned by the Builder bot, left when the owner says done."""
    X.bot(c, "builder")
    return X.pipeline(
        c,
        "eng",
        "Engineering",
        stages=(
            {
                "name": "Build",
                "owner": B,
                "task_limit": limit,
                "expected_minutes": minutes,
                "requirements": [{"kind": "owner_done"}],
            },
            {"name": "Review", "owner": B, "requirements": [{"kind": "owner_done"}]},
        ),
    )


def task(c, title):
    return X.new_task(c, X.ANA, title, "eng")["id"]


def ref(c, task_id):
    return D.ref(E.task(c, task_id))


def bodies(c, task_id, what=D.WAKE):
    """The bodies of the wakes the engine sent about the task for an ended dependency."""
    return [
        row[0]
        for row in c.execute(
            "SELECT body FROM messages WHERE wake_key LIKE ? ORDER BY created_at, id", (f"{task_id}:%:{what}:%",)
        )
    ]


def ended(c, task_id, to=E.DONE):
    E.move(c, task_id, to, X.ANA, "Finished" if to == E.DONE else "Not needed")


def test_a_task_that_depends_on_another_is_blocked_by_it_and_nobodys_turn(c):
    build(c)
    blocker, blocked = task(c, "Growth synced sources part 1"), task(c, "Growth synced sources part 2")
    entered = X.wakes(c, blocked, B)  # Build's, on entering
    assert TR.relate(c, X.ANA, blocked, blocker, "depends_on")
    waits = W.waiting_on(c, blocked)
    assert [(w.state, w.who, w.line) for w in waits] == [
        (W.TASKS, blocker, f"Blocked by {ref(c, blocker)} Growth synced sources part 1, in Build")
    ]
    assert D.is_wait(waits[0].requirement_id)
    assert W.turn(c, E.task(c, blocked)) is None and W.turns(c, E.task(c, blocked)) == set()
    # Read from both ends: it depends on #304, and #304 blocks it.
    both = TR.grouped(c, [blocked, blocker])
    assert [(e["id"], e["direction"]) for e in both[blocked]["depends_on"]] == [(blocker, "out")]
    assert [(e["id"], e["direction"]) for e in both[blocker]["depends_on"]] == [(blocked, "in")]
    assert TR.readback(c, blocked, blocker, "depends_on")[1].startswith(f"{ref(c, blocker)} ")
    assert " blocks " in TR.readback(c, blocked, blocker, "depends_on")[1]
    # A reader who may not open the blocker is not told its title.
    hidden = W.unnamed(waits[0], lambda _task_id: False)
    assert (hidden.who, hidden.line) == (None, D.UNNAMED)
    # The never-stuck pass does not wake the Builder for it.
    NS.check(c, later(60))
    assert X.wakes(c, blocked, B) == entered


def test_the_owner_is_woken_once_when_the_last_open_dependency_is_done(c):
    build(c)
    first, second, blocked = task(c, "Part 1"), task(c, "Fix main"), task(c, "Part 2")
    TR.relate(c, X.ANA, blocked, first, "depends_on")
    TR.relate(c, X.ANA, blocked, second, "depends_on")
    entered = X.wakes(c, blocked, B)
    ended(c, first)
    # Still blocked by the fix: nobody is woken.
    assert [w.line for w in W.waiting_on(c, blocked)] == [f"Blocked by {ref(c, second)} Fix main, in Build"]
    assert bodies(c, blocked) == [] and X.wakes(c, blocked, B) == entered
    ended(c, second)
    (body,) = bodies(c, blocked)
    assert f"{ref(c, second)} is done" in body
    waits = W.waiting_on(c, blocked)
    assert not any(D.is_wait(w.requirement_id) for w in waits) and W.turn(c, E.task(c, blocked)) == B
    # Once: the never-stuck pass and a second end change nothing more.
    NS.check(c)
    assert len(bodies(c, blocked)) == 1


def test_a_dependency_closed_without_doing_it_wakes_the_owner_to_re_plan(c):
    build(c)
    blocker, blocked = task(c, "Part 1"), task(c, "Part 2")
    TR.relate(c, X.ANA, blocked, blocker, "depends_on")
    X.settled(c)
    ended(c, blocker, E.CLOSED)
    (body,) = bodies(c, blocked)
    assert f"{ref(c, blocker)} was closed without doing it" in body


def test_a_blocked_task_whose_work_is_finished_waits_and_moves_on_when_unblocked(c):
    """The Builder said done while still blocked: the task does not move on until the blocker ends; then it moves on by
    itself, as a task does when what it filed ends."""
    build(c)
    blocker, blocked = task(c, "Part 1"), task(c, "Part 2")
    TR.relate(c, X.ANA, blocked, blocker, "depends_on")
    X.say_done(c, E.task(c, blocked))
    assert E.advance(c, blocked) is None
    NS.check(c)
    assert E.task(c, blocked)["stage"]["name"] == "Build"
    ended(c, blocker)
    assert E.task(c, blocked)["stage"]["name"] == "Review"


def test_a_loop_a_dependency_on_an_ended_task_and_on_itself_are_refused(c):
    build(c)
    a, b, d = task(c, "A"), task(c, "B"), task(c, "D")
    TR.relate(c, X.ANA, a, b, "depends_on")
    TR.relate(c, X.ANA, b, d, "depends_on")
    with pytest.raises(H.Refused) as loop:
        TR.relate(c, X.ANA, d, a, "depends_on")
    assert loop.value.rule == "kind" and "loop" in loop.value.detail
    with pytest.raises(H.Refused):
        TR.relate(c, X.ANA, a, a, "depends_on")
    done = task(c, "Done already")
    ended(c, done)
    with pytest.raises(H.Refused) as gone:
        TR.relate(c, X.ANA, a, done, "depends_on")
    assert gone.value.rule == "task_ended"
    # Taking it off: the task is no longer blocked by it.
    assert TR.relate(c, X.ANA, a, b, "depends_on", remove=True)
    assert not any(D.is_wait(w.requirement_id) for w in W.waiting_on(c, a))


def later(minutes):
    return H.shift(H.now(), minutes=minutes)


def test_a_blocked_task_holds_no_slot_is_never_late_and_is_not_handed_on(c):
    build(c, limit=2, minutes=60)
    blocker, blocked = task(c, "Part 1"), task(c, "Part 2")
    stage = E.task(c, blocker)["stage_id"]
    assert not E.has_room(c, E.stage(c, stage))  # both hold Build's two slots
    TR.relate(c, X.ANA, blocked, blocker, "depends_on")
    assert E.has_room(c, E.stage(c, stage)) and W.slots(c, stage).held == 1
    third = task(c, "Part 3")
    assert E.task(c, third)["stage_id"] == stage  # took the slot the blocked task gave up
    entered = X.wakes(c, blocked)
    NS.check(c, later(180))
    NS.check(c, later(300))
    assert "late" not in X.events(c, blocked) and X.wakes(c, blocked) == entered
    assert (E.task(c, blocked)["visit"].get("level") or "owner") == "owner"


def test_a_blocked_task_leaves_its_person_owners_needs_you(c):
    build(c)
    X.pipeline(c)  # General: To do, the assignee's
    blocker = task(c, "Part 1")
    mine = X.new_task(c, X.ANA, "Write the copy", assignee=X.ANA)["id"]
    assert any(item.get("task_id") == mine for item in W.needs(c, X.ANA))
    TR.relate(c, X.ANA, mine, blocker, "depends_on")
    assert not any(item.get("task_id") == mine for item in W.needs(c, X.ANA))


def test_blocked_time_does_not_count_toward_late():
    """A task blocked for five and a half hours of its six in Build (two dependencies that overlap) spent half an hour
    there: each blocked moment is counted once."""
    spans = [("2026-11-13T00:00:00Z", "2026-11-13T05:00:00Z"), ("2026-11-13T04:00:00Z", "2026-11-13T05:30:00Z")]
    assert NS._covered(spans, "2026-11-13T00:00:00Z", "2026-11-13T06:00:00Z") == pytest.approx(5.5 * 3600)


def test_the_migration_lets_an_old_table_take_depends_on_and_keeps_its_rows():
    migration = import_module("backend.migrations.20261113060000_task_relations_depends_on")
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE tasks(id TEXT PRIMARY KEY)")
    conn.executemany("INSERT INTO tasks VALUES(?)", [("a",), ("b",)])
    conn.executescript(TR.SCHEMA.replace("'after','depends_on'", "'after','blocks'"))
    conn.execute("INSERT INTO task_relations VALUES('a','b','after','human:ana','2026-11-12T00:00:00Z')")
    conn.execute("INSERT INTO task_relations VALUES('b','a','blocks','human:ana','2026-11-12T00:00:01Z')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO task_relations VALUES('b','a','depends_on','human:ana','2026-11-12T00:00:02Z')")
    migration.up(conn)
    migration.up(conn)  # once only
    conn.execute("INSERT INTO task_relations VALUES('b','a','depends_on','human:ana','2026-11-13T00:00:00Z')")
    assert conn.execute("SELECT from_task, to_task, kind FROM task_relations ORDER BY rowid").fetchall() == [
        ("a", "b", "after"),
        ("b", "a", "depends_on"),
    ]
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO task_relations VALUES('a','b','blocks','human:ana','2026-11-13T00:00:01Z')")
    indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL")}
    assert indexes == {"task_relations_to", "task_relations_from", "task_relations_one_parent"}
