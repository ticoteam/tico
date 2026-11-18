"""What the screens read of a dependency (backend/task_depends.py, part 3 of "A blocked task shows as Blocked, never
Snoozed"): a board row says which tasks block it (`blocked_by`) and which it blocks (`blocks`), the task's relations
say since when, and a blocker a person owns is in Tasks, not their Needs you (nothing is asked of them). No list says
Snoozed for a blocked task."""

import pytest

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


def test_a_board_row_names_the_tasks_that_block_it_and_those_it_blocks(c):
    build(c)
    blocker, blocked, other = task(c, "Part 1"), task(c, "Part 2"), task(c, "Part 3")
    TR.relate(c, X.ANA, blocked, blocker, "depends_on")
    read = rows(c, blocker, blocked, other)
    number = E.task(c, blocker)["number"]
    assert read[blocked].get("blocked_by", []) == [{"id": blocker, "number": number, "label": None, "title": "Part 1"}]
    assert read[blocked].get("blocks", []) == []
    assert read[blocker].get("blocks", []) == [{"id": blocked, "number": E.task(c, blocked)["number"], "label": None}]
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
    hidden_row = {"id": None, "number": None, "label": None, "title": None}
    assert read[blocked].get("blocked_by", []) == [hidden_row]  # still blocked: the mark stays, nothing names it
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


def test_a_blocker_a_person_owns_is_in_nobodys_needs_you_and_neither_are_the_tasks_it_blocks(c):
    """Its row says what it blocks (`blocks`); Needs you holds only what is asked of a person."""
    build(c)
    mine = task(c, "Write the copy", "general", assignee=X.ANA)
    first, second = task(c, "Part 2"), task(c, "Part 3")
    TR.relate(c, X.ANA, first, mine, "depends_on")
    TR.relate(c, X.ANA, second, mine, "depends_on")
    assert rows(c, mine)[mine]["blocks"]
    everyone = W.needs_by_person(c)
    listed = [e for items in everyone.values() for e in items]
    assert not any(e.get("task_id") in (mine, first, second) for e in listed)
    assert not any("Snoozed" in (e.get("line") or "") or "Blocks" in (e.get("line") or "") for e in listed)


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
    assert (turn["state"], turn["line"]) == (W.TASKS, "Blocked by Part 1, in Build")
    assert [w.state for w in W.waiting_on(c, blocked)] == [W.TASKS]
    # Once the blocker ends, the snooze that is still open sets it aside again.
    E.move(c, blocker, E.DONE, X.ANA, "Finished")
    assert blocked in listed("only") and rows(c, blocked)[blocked]["snoozed"] is True
    assert W.waiting_on(c, blocked)[0].state == W.SNOOZED


def test_a_blocker_is_named_by_its_label_or_by_its_title_never_by_its_id(c):
    """A person reads "Blocked by O561 Write the launch post" or, for a task with no number, its title alone: a raw id
    ("7a0009f1") says nothing to them. The row carries the label and title for the card's link to the blocker."""
    build(c)
    c.execute("UPDATE pipelines SET prefix='O' WHERE id='eng'")
    numbered = task(c, "Write the launch post")
    plain = task(c, "Call the printer", "general", assignee=X.ANA)
    blocked = task(c, "Ship the launch")
    TR.relate(c, X.ANA, blocked, numbered, "depends_on")
    TR.relate(c, X.ANA, blocked, plain, "depends_on")
    label = f"O{E.task(c, numbered)['number']}"
    assert [w.line for w in W.waiting_on(c, blocked)] == [
        f"Blocked by {label} Write the launch post, in Build",
        "Blocked by Call the printer, in To do",
    ]
    assert not any(plain[:8] in w.line for w in W.waiting_on(c, blocked))
    read = rows(c, blocked, numbered)
    assert [(d["label"], d["title"]) for d in read[blocked]["blocked_by"]] == [
        (label, "Write the launch post"),
        (None, "Call the printer"),
    ]
    assert read[numbered]["blocks"] == [
        {"id": blocked, "number": E.task(c, blocked)["number"], "label": f"O{E.task(c, blocked)['number']}"}
    ]


def test_a_page_of_blocked_rows_reads_the_same_statements_however_many_blockers(c):
    """A board page's dependency lines ("Blocked by #304 ..., in Build") and relations ("#304 · Build") are read for
    the whole page at once: on the General board each blocker cost two more statements per row before."""
    build(c)

    def statements(n):
        blocked = [task(c, f"Waits {n}-{i}") for i in range(n)]
        for i, tid in enumerate(blocked):
            TR.relate(c, X.ANA, tid, task(c, f"Blocker {n}-{i}"), "depends_on")
        found, seen = [dict(H.task(c, tid)) for tid in blocked], []
        c.set_trace_callback(seen.append)
        try:
            read = {row["id"]: row for row in task_reads.brief(c, found, "1")}
            relations = TR.grouped(c, blocked)
        finally:
            c.set_trace_callback(None)
        for tid in blocked:
            assert read[tid]["turn"]["line"].startswith("Blocked by ") and read[tid]["turn"]["line"].endswith(
                ", in Build"
            )
            (dep,) = relations[tid]["depends_on"]
            assert dep["stage"] == "Build" and dep["number"] == E.task(c, dep["id"])["number"]
        return len(seen)

    assert statements(8) == statements(2)
