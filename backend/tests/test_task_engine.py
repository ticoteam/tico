"""The one writer of a task's stage, owner and status (backend/task_engine.py, docs/pipelines-v3.md "How a task moves").

Synthetic rows only (backend/tests/helpers/engine.py): a General pipeline, a three-stage Build pipeline, the fictional
company's person and two bots.
"""

import sqlite3

import pytest

from backend import task_engine as E
from backend import task_requirements as R
from backend.store import H
from backend.tests.helpers import engine as X

ANA = X.ANA
BUILD = (
    {"name": "On deck"},
    {"name": "Build", "owner": "bot:builder", "task_limit": 1, "requirements": [{"kind": "owner_done"}]},
    {
        "name": "Review",
        "owner": "bot:reviewer",
        "requirements": [{"kind": "ok", "person": ANA, "line": "OK the build"}],
    },
)


@pytest.fixture
def c(api):
    with api.app.state.store.transaction() as conn:
        yield conn


@pytest.fixture
def general(c):
    X.bot(c, "builder")
    X.bot(c, "reviewer")
    X.bot(c, "manager")
    return X.pipeline(c, manager="bot:manager")


@pytest.fixture
def build(c, general):
    return X.pipeline(c, "eng", "Engineering", BUILD, manager="bot:manager")


def _refused(rule):
    return pytest.raises(H.Refused, match=rule)


def test_a_task_on_the_engine_changes_stage_owner_and_status_only_through_the_engine(c, general):
    row = X.new_task(c, ANA, "Write the launch note", assignee="bot:builder")
    for write in ("status='done'", "owner_id='bot:reviewer'", "stage_id='general-done'", "pipeline_id=NULL"):
        with pytest.raises(sqlite3.IntegrityError, match="only through move"):
            c.execute(f"UPDATE tasks SET {write} WHERE id=?", (row["id"],))
    c.execute("UPDATE tasks SET title='Write the launch note today' WHERE id=?", (row["id"],))
    old = H.task_create(c, ANA, "File the receipts", "Body.", ANA, lint=False)
    c.execute("UPDATE tasks SET status='doing' WHERE id=?", (old["id"],))  # not on the engine yet: untouched
    assert X.guard_closed(c)


def test_a_new_task_starts_in_the_first_stage_with_one_open_visit_and_one_wake(c, general):
    row = X.new_task(c, ANA, "Write the launch note", assignee="bot:builder")
    assert (row["stage_id"], row["status"], row["owner_id"]) == ("general-to-do", "open", "bot:builder")
    assert row["visit"]["entered_how"] == "new" and row["visit"]["left_at"] is None
    assert X.wakes(c, row["id"]) == [(f"{row['id']}:{row['visit']['id']}:owner:0", "bot:builder")]
    with _refused("task_changed"):
        E.enter(c, row["id"], "general", ANA)
    mine = X.new_task(c, ANA, "Call the bank", assignee=ANA)
    assert mine["owner_id"] == ANA and X.wakes(c, mine["id"]) == []  # a person's list is a read: nothing is sent


def test_a_task_with_no_assignee_wakes_the_manager_bot_to_pick_one(c, general):
    row = X.new_task(c, ANA, "Sort the shared inbox")
    assert row["owner_id"] is None
    assert [w[1] for w in X.wakes(c, row["id"])] == ["bot:manager"]


def test_forward_is_held_to_what_is_missing_and_moves_once_it_is_met(c, general):
    row = X.new_task(c, ANA, "Write the launch note", assignee="bot:builder")
    with _refused("task_not_met"):
        E.move(c, row["id"], E.NEXT, ANA)
    assert E.advance(c, row["id"]) is None
    X.say_done(c, row)
    done = E.advance(c, row["id"])
    assert (done["status"], done["stage_id"], done["version"]) == ("done", "general-done", row["version"] + 1)
    assert done["owner_id"] == "bot:builder"  # an ended task keeps its owner
    left = H._one(c, "SELECT * FROM visits WHERE id=?", (row["visit"]["id"],))
    assert (left["how"], left["by_id"]) == ("met", E.SYSTEM) and left["left_at"]
    assert done["visit"]["stage_id"] == "general-done" and done["done_at"]
    assert E.move(c, row["id"], E.DONE, ANA)["version"] == done["version"]  # already there: nothing happens


def test_push_past_needs_a_reason_and_is_recorded(c, general):
    row = X.new_task(c, ANA, "Write the launch note", assignee="bot:builder")
    moved = E.move(c, row["id"], E.DONE, "bot:builder", "The note went out by hand", seen_version=row["version"])
    assert moved["status"] == "done"
    left = H._one(c, "SELECT * FROM visits WHERE id=?", (row["visit"]["id"],))
    assert (left["how"], left["why"]) == ("pushed_past", "The note went out by hand")
    pushed = H._one(c, "SELECT * FROM answers WHERE visit_id=?", (row["visit"]["id"],))
    assert (pushed["answer"], pushed["by_id"]) == ("pushed_past", "bot:builder")
    assert any(key.endswith(":pushed_past") and who == "bot:manager" for key, who in X.wakes(c, row["id"]))


def test_a_switch_that_is_off_and_a_people_only_ok_refuse_a_push_past(c, general):
    stages = (
        {
            "name": "Sign",
            "owner": "bot:builder",
            "requirements": [{"kind": "ok", "person": ANA, "who_oks": "people_only"}],
        },
        {"name": "Ship", "owner": "bot:builder", "requirements": [{"kind": "owner_done", "can_push_past": 0}]},
    )
    X.pipeline(c, "legal", "Legal", stages)
    row = X.new_task(c, ANA, "Sign the lease", "legal")
    with _refused("task_needs_person"):
        E.move(c, row["id"], E.NEXT, "bot:builder", "Ana said yes in chat", seen_version=row["version"])
    assert H._one(c, "SELECT COUNT(*) AS n FROM answers")["n"] == 0  # a refused move writes nothing
    row = E.move(c, row["id"], E.NEXT, ANA, "I signed it on paper")
    assert row["stage"]["name"] == "Ship"
    with _refused("task_no_push_past"):
        E.move(c, row["id"], E.NEXT, ANA, "Ship it anyway")
    assert X.guard_closed(c)


def test_a_bot_sends_the_version_it_saw_and_an_old_one_is_refused(c, general):
    row = X.new_task(c, ANA, "Write the launch note", assignee="bot:builder")
    with _refused("task_changed"):
        E.move(c, row["id"], E.CLOSED, "bot:builder", "Not needed")
    E.reassign(c, row["id"], "bot:reviewer", ANA)
    with _refused("task_changed"):
        E.move(c, row["id"], E.CLOSED, "bot:builder", "Not needed", seen_version=row["version"])
    assert E.task(c, row["id"])["status"] == "open"


def test_forward_needs_room_back_and_close_never_do_and_the_oldest_takes_a_free_slot(c, build):
    first = X.new_task(c, ANA, "Build the importer", "eng")
    second = X.new_task(c, ANA, "Build the exporter", "eng")
    third = X.new_task(c, ANA, "Build the report", "eng")
    assert E.fill_open_slots(c) == [first["id"]]  # On deck needs nothing: the oldest moves when Build has room
    assert E.task(c, second["id"])["stage"]["name"] == "On deck"
    with _refused("task_stage_full"):
        E.move(c, second["id"], E.NEXT, ANA)
    with _refused("task_stage_full"):
        E.move(c, second["id"], E.NEXT, ANA, "It is urgent")  # a reason does not push past the limit
    built = E.task(c, first["id"])
    assert built["owner_id"] == "bot:builder"
    X.say_done(c, built)
    assert E.advance(c, first["id"])["stage"]["name"] == "Review"
    assert E.fill_open_slots(c) == [second["id"]]
    assert E.task(c, third["id"])["stage"]["name"] == "On deck"
    review = E.task(c, first["id"])
    with _refused("task_why"):
        E.move(c, first["id"], "Build", "bot:reviewer", seen_version=review["version"])
    back = E.move(c, first["id"], "Build", "bot:reviewer", "The importer drops rows", seen_version=review["version"])
    assert back["stage"]["name"] == "Build" and back["visit"]["entered_how"] == "back"  # one over the limit is allowed
    assert not E.has_room(c, back["stage"])
    assert E.move(c, third["id"], E.CLOSED, ANA)["status"] == "closed"  # a person need not say why


def test_skipping_a_stage_counts_its_requirements_as_missing(c, build):
    row = X.new_task(c, ANA, "Build the importer", "eng")
    with _refused("task_not_met"):
        E.move(c, row["id"], "Review", ANA)
    moved = E.move(c, row["id"], "Review", ANA, "Built by hand last week")
    assert moved["stage"]["name"] == "Review" and moved["visit"]["entered_how"] == "pushed_past"


def test_closing_a_task_closes_the_open_tasks_it_filed_and_nesting_stops_at_five(c, general):
    top = X.new_task(c, ANA, "Plan the launch", assignee="bot:builder")
    chain = [top]
    for level in range(E.MAX_DEPTH):
        chain.append(X.new_task(c, ANA, f"Step {level + 1}", assignee="bot:builder", parent=chain[-1]["id"]))
    with _refused("task_too_deep"):
        X.new_task(c, ANA, "Step 6", assignee="bot:builder", parent=chain[-1]["id"])
    finished = chain[2]
    X.say_done(c, finished)
    E.move(c, finished["id"], E.DONE, ANA, "Done early")  # its own child is still open: pushed past
    E.move(c, top["id"], E.CLOSED, ANA, "The launch is off")
    states = [E.task(c, t["id"])["status"] for t in chain]
    assert states == ["closed", "closed", "done", "closed", "closed", "closed"]
    assert X.guard_closed(c)


def test_a_task_waits_on_the_tasks_it_filed_and_moves_when_they_are_all_done(c, general):
    stages = ({"name": "In development", "owner": "bot:manager", "requirements": [{"kind": "linked_done"}]},)
    X.pipeline(c, "product", "Product", stages)
    parent = X.new_task(c, ANA, "Checkout flow", "product")
    assert [s.state for s in R.standings(c, parent)] == [R.NOT_MET]  # zero tasks filed is not met
    one = X.new_task(c, ANA, "Build the cart", assignee="bot:builder", parent=parent["id"])
    two = X.new_task(c, ANA, "Build the payment form", assignee="bot:builder", parent=parent["id"])
    standing = R.standings(c, E.task(c, parent["id"]))[0]
    assert (standing.state, standing.on) == (R.WAITING, one["id"])
    X.say_done(c, one)
    E.advance(c, one["id"])
    assert E.task(c, parent["id"])["status"] == "open"
    X.say_done(c, two)
    E.advance(c, two["id"])
    assert E.task(c, parent["id"])["status"] == "done"  # the last one ended Done: the parent left in the same step


def test_a_filed_task_that_was_deleted_counts_as_closed_not_done(c, general):
    stages = ({"name": "In development", "owner": "bot:manager", "requirements": [{"kind": "linked_done"}]},)
    X.pipeline(c, "product", "Product", stages)
    parent = X.new_task(c, ANA, "Checkout flow", "product")
    one = X.new_task(c, ANA, "Build the cart", assignee="bot:builder", parent=parent["id"])
    two = X.new_task(c, ANA, "Build the payment form", assignee="bot:builder", parent=parent["id"])
    X.say_done(c, one)
    E.advance(c, one["id"])
    c.execute("PRAGMA foreign_keys=OFF")
    c.execute("DELETE FROM tasks WHERE id=?", (two["id"],))
    standing = R.standings(c, E.task(c, parent["id"]))[0]
    assert (standing.state, standing.line) == (R.NOT_MET, "A deleted task was closed: It was deleted")
    assert E.advance(c, parent["id"]) is None


def test_a_filed_task_that_closes_keeps_the_parent_and_wakes_its_owner_once(c, general):
    stages = ({"name": "In development", "owner": "bot:manager", "requirements": [{"kind": "linked_done"}]},)
    X.pipeline(c, "product", "Product", stages)
    parent = X.new_task(c, ANA, "Checkout flow", "product")
    child = X.new_task(c, ANA, "Build the cart", assignee="bot:builder", parent=parent["id"])
    X.settled(c)
    E.move(c, child["id"], E.CLOSED, "bot:builder", "The cart is a duplicate", seen_version=child["version"])
    after = E.task(c, parent["id"])
    assert after["status"] == "open"
    standing = R.standings(c, after)[0]
    assert standing.state == R.NOT_MET and "The cart is a duplicate" in standing.line
    told = [key for key, who in X.wakes(c, parent["id"], "bot:manager") if ":filed:" in key]
    assert told == [f"{parent['id']}:{parent['visit']['id']}:filed:{child['id']}"]


def test_reopen_needs_a_note_and_goes_back_to_the_stage_the_task_left(c, build):
    row = X.new_task(c, ANA, "Build the importer", "eng")
    E.fill_open_slots(c)
    closed = E.move(c, row["id"], E.CLOSED, ANA)
    with _refused("task_ended"):
        E.move(c, row["id"], E.NEXT, ANA)
    with _refused("task_why"):
        E.move(c, row["id"], E.REOPEN, ANA)
    again = E.move(c, row["id"], E.REOPEN, ANA, "The customer asked again")
    assert (again["stage"]["name"], again["status"], again["owner_id"]) == ("Build", "open", "bot:builder")
    assert again["visit"]["entered_how"] == "reopened" and again["closed_at"] is None
    assert again["version"] == closed["version"] + 1


def test_reassign_is_a_move_it_resets_the_tries_and_drops_the_old_owners_wakes(c, general):
    row = X.new_task(c, ANA, "Write the launch note", assignee="bot:builder")
    c.execute("UPDATE visits SET tries=2, level='manager' WHERE id=?", (row["visit"]["id"],))
    moved = E.reassign(c, row["id"], "bot:reviewer", ANA, "Reviewer knows the product")
    assert (moved["owner_id"], moved["assignee_id"], moved["version"]) == (
        "bot:reviewer",
        "bot:reviewer",
        row["version"] + 1,
    )
    assert (moved["visit"]["id"], moved["visit"]["tries"], moved["visit"]["level"]) == (row["visit"]["id"], 0, "owner")
    states = dict(
        c.execute(
            "SELECT m.recipient_id, j.state FROM jobs j JOIN messages m ON m.id = j.message_id WHERE m.wake_key LIKE ?",
            (row["id"] + ":%",),
        ).fetchall()
    )
    assert states == {"bot:builder": "canceled", "bot:reviewer": "queued"}


def test_snooze_a_bot_says_why_and_its_fourth_in_one_stage_goes_to_the_manager(c, general):
    row = X.new_task(c, ANA, "Chase the vendor", assignee="bot:builder")
    seen = row["version"]  # a snooze is not a move: the version stays
    with _refused("task_changed"):
        E.snooze(c, row["id"], "bot:builder", "Vendor replies later", "2026-11-14T00:00:00.000000Z")
    with _refused("task_why"):
        E.snooze(c, row["id"], "bot:builder", until="2026-11-14T00:00:00.000000Z", seen_version=seen)
    for n in range(E.BOT_SNOOZES):
        out = E.snooze(c, row["id"], "bot:builder", f"Vendor replies later ({n})", "2026-11-14T00:00:00.000000Z", seen)
        assert out["snooze"]["why"].startswith("Vendor replies later")
        E.unsnooze(c, row["id"], ANA)
    out = E.snooze(c, row["id"], "bot:builder", "Still waiting", "2026-11-20T00:00:00.000000Z", seen)
    assert out["snooze"] is None and "manager" in out["note"]
    assert out["visit"]["level"] == "manager"
    assert E.snooze(c, row["id"], ANA)["snooze"]["until_at"] is None  # a person needs no reason and no date


def test_a_snoozed_task_holds_no_slot_and_ending_the_task_ends_the_snooze(c, build):
    first = X.new_task(c, ANA, "Build the importer", "eng")
    second = X.new_task(c, ANA, "Build the exporter", "eng")
    E.fill_open_slots(c)
    E.snooze(c, first["id"], ANA)
    assert E.fill_open_slots(c) == [second["id"]]  # the snoozed task's slot is free
    E.unsnooze(c, first["id"], ANA)
    assert not E.has_room(c, E.task(c, first["id"])["stage"])  # one over the limit until a task leaves
    E.snooze(c, first["id"], ANA)
    closed = E.move(c, first["id"], E.CLOSED, ANA)
    assert closed["snooze"] is None
    assert H._one(c, "SELECT COUNT(*) AS n FROM snoozes WHERE ended_at IS NULL")["n"] == 0


def test_a_snooze_ends_when_the_task_leaves_the_stage_it_was_set_aside_in(c, build):
    row = X.new_task(c, ANA, "Build the importer", "eng")
    E.move(c, row["id"], E.NEXT, ANA)
    E.fill_open_slots(c)
    E.snooze(c, row["id"], ANA)
    moved = E.move(c, row["id"], "Review", ANA, "Reviewed by hand")
    assert moved["snooze"] is None  # it would have kept Review's wake and slot away from the task
    back = E.move(c, row["id"], "Build", ANA)
    assert back["snooze"] is None and X.wakes(c, row["id"], "bot:builder")[-1][0].startswith(row["id"])


def test_a_snooze_cancels_every_wake_queued_for_the_task_whoever_it_was_for(c, general):
    row = X.new_task(c, ANA, "Write the launch note")  # no owner: the manager bot is woken to pick one
    E.reassign(c, row["id"], "bot:builder", ANA)
    mine = "message_id IN (SELECT id FROM messages WHERE wake_key LIKE ?)"
    c.execute(f"UPDATE jobs SET state='queued' WHERE {mine}", (row["id"] + ":%",))
    E.snooze(c, row["id"], ANA)
    states = c.execute(f"SELECT bot_id, state FROM jobs WHERE {mine} ORDER BY bot_id", (row["id"] + ":%",)).fetchall()
    assert [tuple(s) for s in states] == [("builder", "canceled"), ("manager", "canceled")]


def test_the_guard_is_open_for_one_task_only(c, general):
    first = X.new_task(c, ANA, "Write the launch note", assignee="bot:builder")
    second = X.new_task(c, ANA, "Write the changelog", assignee="bot:builder")
    c.execute(
        "CREATE TEMP TRIGGER rides_along AFTER UPDATE OF updated_at ON tasks WHEN NEW.id = '%s' "
        "BEGIN UPDATE tasks SET status='done' WHERE id = '%s'; END" % (first["id"], second["id"])
    )
    try:
        with pytest.raises(sqlite3.DatabaseError, match="only through move"):
            E.reassign(c, first["id"], "bot:reviewer", ANA)
    finally:
        c.execute("DROP TRIGGER rides_along")
    assert E.task(c, second["id"])["status"] == "open" and X.guard_closed(c)


def test_a_requirement_added_to_one_task_holds_it_until_answered_withdrawn_or_the_visit_ends(c, build):
    row = X.new_task(c, ANA, "Build the importer", "eng")
    E.fill_open_slots(c)
    row = E.task(c, row["id"])
    seen = row["version"]
    with _refused("task_changed"):
        E.add_requirement(c, row["id"], "bot:builder", "answer", ANA, "Retry how many times?")
    with _refused("task_requirement"):
        E.add_requirement(c, row["id"], "bot:builder", "owner_done", ANA, "Is it done?", seen_version=seen)
    first = E.add_requirement(
        c, row["id"], "bot:builder", "answer", ANA, "Retry how many times?", choices=["3", "5"], seen_version=seen
    )
    second = E.add_requirement(
        c, row["id"], "bot:builder", "answer", ANA, "Retry 3 or 5 times?", choices=["3", "5"], seen_version=seen
    )
    open_ones = R.on_visit(c, row["visit"]["id"])
    assert [r["id"] for r in open_ones] == [second["requirement_id"]]  # a bot's second replaces its first
    assert first["version"] == row["version"]  # asking is not a move
    mine = E.add_requirement(c, row["id"], ANA, "ok", "human:ana", "OK the retry plan")
    assert len(R.on_visit(c, row["visit"]["id"])) == 2  # a person has no such limit
    X.say_done(c, row)
    assert E.advance(c, row["id"]) is None  # the stage's own requirement is met: the task still waits
    X.answer(c, row, second["requirement_id"], "5", ANA)
    assert E.advance(c, row["id"]) is None
    moved = E.withdraw_requirement(c, mine["requirement_id"], ANA)
    assert moved["stage"]["name"] == "Review"
    asked = E.add_requirement(c, row["id"], ANA, "ok", ANA, "OK the importer's limits")
    E.move(c, row["id"], "Build", ANA)
    assert R.get(c, asked["requirement_id"])["withdrawn_at"]  # moving back ends it: nothing is left behind
