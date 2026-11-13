"""What the screens read of a dependency (backend/task_depends.py, part 3 of "A blocked task shows as Blocked, never
Snoozed"): a board row says which tasks block it (`blocked_by`) and which it blocks (`blocks`), the task's relations
say since when, and a person who owns a blocker reads "Blocks #307, #308" in their Needs you. No list says Snoozed for
a blocked task."""

import pytest

from backend import task_depends as D
from backend import task_engine as E
from backend import task_reads
from backend import task_relations as TR
from backend import waiting_on as W
from backend.store import H
from backend.tests.helpers import engine as X

B = "bot:builder"


@pytest.fixture
def c(company):
    with company.transaction() as conn:
        yield conn


def build(c):
    X.bot(c, "builder")
    X.pipeline(c)  # General: To do, the assignee's
    return X.pipeline(
        c,
        "eng",
        "Engineering",
        stages=({"name": "Build", "owner": B, "requirements": [{"kind": "owner_done"}]},),
    )


def task(c, title, pipeline="eng", assignee=None):
    return X.new_task(c, X.ANA, title, pipeline, assignee=assignee)["id"]


def rows(c, *ids, visible_sql="1"):
    found = [dict(H.task(c, tid)) for tid in ids]
    return {row["id"]: row for row in task_reads.brief(c, found, visible_sql)}


def ref(c, task_id):
    return D.ref(E.task(c, task_id))


def test_a_board_row_names_the_tasks_that_block_it_and_those_it_blocks(c):
    build(c)
    blocker, blocked, other = task(c, "Part 1"), task(c, "Part 2"), task(c, "Part 3")
    TR.relate(c, X.ANA, blocked, blocker, "depends_on")
    read = rows(c, blocker, blocked, other)
    number = E.task(c, blocker)["number"]
    assert read[blocked].get("blocked_by", []) == [{"id": blocker, "number": number}]
    assert read[blocked].get("blocks", []) == []
    assert read[blocker].get("blocks", []) == [{"id": blocked, "number": E.task(c, blocked)["number"]}]
    assert read[blocker].get("blocked_by", []) == []
    # A row with no dependency carries neither field: a 500-row list stays inside its byte budget.
    assert "blocked_by" not in read[other] and "blocks" not in read[other]
    # Blocked, not asleep: its turn is the dependency, and it is not snoozed.
    assert read[blocked]["snoozed"] is False and read[blocked]["turn"]["state"] == W.TASKS
    # Once the blocker ends, neither row says so any more.
    E.move(c, blocker, E.DONE, X.ANA, "Finished")
    read = rows(c, blocker, blocked)
    assert (read[blocked].get("blocked_by", []), read[blocker].get("blocks", [])) == ([], [])


def test_a_reader_who_may_not_open_the_other_end_is_not_told_which_task_it_is(c):
    build(c)
    blocker, blocked = task(c, "Part 1"), task(c, "Part 2")
    TR.relate(c, X.ANA, blocked, blocker, "depends_on")
    hidden = f"id <> '{blocker}'"
    read = rows(c, blocked, visible_sql=hidden)
    assert read[blocked].get("blocked_by", []) == [{"id": None, "number": None}]  # still blocked: the mark stays
    read = rows(c, blocker, visible_sql=f"id <> '{blocked}'")
    assert read[blocker].get("blocks", []) == []


def test_a_dependency_says_since_when_from_both_ends(c):
    build(c)
    blocker, blocked = task(c, "Part 1"), task(c, "Part 2")
    TR.relate(c, X.ANA, blocked, blocker, "depends_on")
    since = c.execute("SELECT created_at FROM task_relations WHERE from_task=?", (blocked,)).fetchone()[0]
    both = TR.grouped(c, [blocked, blocker])
    assert [e["since"] for e in both[blocked]["depends_on"]] == [since]
    assert [e["since"] for e in both[blocker]["depends_on"]] == [since]


def test_a_person_who_owns_a_blocker_reads_blocks_in_needs_you(c):
    build(c)
    mine = task(c, "Write the copy", "general", assignee=X.ANA)
    first, second = task(c, "Part 2"), task(c, "Part 3")
    TR.relate(c, X.ANA, first, mine, "depends_on")
    entries = [e for e in W.needs(c, X.ANA) if e.get("task_id") == mine]
    assert [e["kind"] for e in entries] == ["task"]
    assert entries[0]["line"].endswith(f"Blocks {ref(c, first)}.")
    assert entries[0]["blocks"] == [ref(c, first)]
    TR.relate(c, X.ANA, second, mine, "depends_on")
    entries = [e for e in W.needs(c, X.ANA) if e.get("task_id") == mine]
    assert entries[0]["line"].endswith(f"Blocks {ref(c, first)}, {ref(c, second)}.")
    # The blocked tasks are not in anyone's list, and nothing in it says Snoozed.
    everyone = W.needs_by_person(c)
    listed = [e for items in everyone.values() for e in items]
    assert not any(e.get("task_id") in (first, second) for e in listed)
    assert not any("Snoozed" in (e.get("line") or "") for e in listed)
    assert everyone[X.ANA] == W.needs(c, X.ANA)


def test_a_person_blocker_that_is_not_their_turn_still_shows_blocks(c):
    """A bot set the blocker aside, so its own work is out of the owner's list; they still read that it holds others
    up."""
    build(c)
    mine = task(c, "Write the copy", "general", assignee=X.ANA)
    first = task(c, "Part 2")
    TR.relate(c, X.ANA, first, mine, "depends_on")
    row = E.task(c, mine)
    E.snooze(c, mine, B, "Vendor replies by Nov 14", "2026-11-14T00:00:00.000000Z", row["version"])
    entries = [e for e in W.needs(c, X.ANA) if e.get("task_id") == mine]
    assert [(e["kind"], e["line"], e["missing"]) for e in entries] == [("task", f"Blocks {ref(c, first)}.", [])]


def test_a_blocker_owned_by_a_bot_or_itself_blocked_lists_no_blocks(c):
    build(c)
    blocker, blocked = task(c, "Part 1"), task(c, "Part 2")
    TR.relate(c, X.ANA, blocked, blocker, "depends_on")
    assert not any("Blocks" in (e.get("line") or "") for e in W.needs(c, X.ANA))
    # A person's blocker that is itself blocked is not theirs to move: the end of the chain is.
    mine = task(c, "Write the copy", "general", assignee=X.ANA)
    TR.relate(c, X.ANA, task(c, "Part 3"), mine, "depends_on")
    TR.relate(c, X.ANA, mine, blocker, "depends_on")
    assert not any(e.get("task_id") == mine for e in W.needs(c, X.ANA))


def test_a_blocked_task_a_bot_snoozed_is_listed_as_blocked_never_snoozed(c):
    """A bot snoozes a task, then it is made to depend on another (or the other way round): it is blocked, so no list
    has it under Snoozed. The Snoozed archive (snoozed=only) leaves it out, the lists that hide snoozed tasks keep it,
    and its row is not snoozed, so every screen draws it as blocked."""
    build(c)
    blocker, blocked = task(c, "Part 1"), task(c, "Part 2")
    TR.relate(c, X.ANA, blocked, blocker, "depends_on")
    row = E.task(c, blocked)
    E.snooze(c, blocked, B, "Waiting for part 1 to merge", "2026-11-16T00:00:00.000000Z", row["version"])
    asleep = task(c, "Part 3")
    E.snooze(c, asleep, B, "Vendor replies by Nov 16", "2026-11-16T00:00:00.000000Z", E.task(c, asleep)["version"])

    def listed(snoozed):
        return {t["id"] for t in H.tasks(c, status="open", snoozed=snoozed)}

    assert blocked not in listed("only") and asleep in listed("only")
    assert blocked in listed("hide") and asleep not in listed("hide")
    read = rows(c, blocked, asleep)
    assert read[blocked]["snoozed"] is False and read[blocked]["blocked_by"]
    assert read[asleep]["snoozed"] is True
    # Its line is the dependency's, in the row and in the task's own read, never the snooze's why.
    turn = read[blocked]["turn"]
    assert (turn["state"], turn["line"]) == (W.TASKS, f"Blocked by {ref(c, blocker)} Part 1, in Build")
    assert [w.state for w in W.waiting_on(c, blocked)] == [W.TASKS]
    # Once the blocker ends, the snooze that is still open sets it aside again.
    E.move(c, blocker, E.DONE, X.ANA, "Finished")
    assert blocked in listed("only") and rows(c, blocked)[blocked]["snoozed"] is True
    assert W.waiting_on(c, blocked)[0].state == W.SNOOZED
