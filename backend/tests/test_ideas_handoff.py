"""Work handed over in Product's Ideas gets a turn (Product task #125, Engineering #173): a task that a bot other than
its owner files into Ideas, or moves back into it, is the owner's turn, due 24 hours after it entered, and Late wakes
the Product Manager once. An owner's own ideas, a person's request on its first visit and a task a person moved back
rest as before.

Synthetic rows only (backend/tests/helpers/engine.py): the fictional company, where Ana owns it, and a few bots.
"""

from datetime import datetime, timedelta, timezone

import pytest

from backend import never_stuck as N
from backend import pipeline_settings as PS
from backend import task_engine as E
from backend import task_wakes
from backend import waiting_on as W
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.product_engineering_presets import PPM, _answers, _team

ANA = X.ANA
LINE = "Move to Spec, or snooze to park it"


@pytest.fixture
def c(company):
    with company.transaction() as conn:
        yield conn


@pytest.fixture
def product(c, time_machine):
    time_machine.move_to(datetime(2026, 11, 12, 7, tzinfo=timezone.utc), tick=False)
    _team(c)
    _answers(c, software="yes")
    PS.ensure_presets(c)
    return X.bot(c, "bug-triage")


def _owner_wakes(c, row):
    """The owner's wakes about the task's open visit, beside C10's "Spec has room" (never_stuck._room)."""
    visit = E.task(c, row["id"])["visit"]["id"]
    return [
        key
        for key, who in X.wakes(c, row["id"], row["owner_id"])
        if key.split(":")[1] == visit and key.split(":")[2] != task_wakes.ROOM
    ]


def _manager_late_wakes(c, row):
    return [key for key, who in X.wakes(c, row["id"], H.bot_actor(PPM)) if key.endswith(":late")]


def _handed_back(c, triage):
    """A person's request that moved into Spec by itself, which Bug Triage moved back into Ideas."""
    bug = X.new_task(c, ANA, "Saved carts lose items", "product")
    X.settled(c)
    N.check(c)
    assert E.task(c, bug["id"])["stage_id"] == "product-spec"
    row = E.task(c, bug["id"])
    E.move(c, bug["id"], "product-ideas", triage, "Route it to its area's owner", seen_version=row["version"])
    return E.task(c, bug["id"])


def test_a_persons_request_a_bot_moved_back_into_ideas_is_its_owners_turn_due_in_24_hours(c, product):
    row = _handed_back(c, product)
    assert (row["stage_id"], row["visit"]["entered_how"]) == ("product-ideas", "back")
    N.check(c)
    N.check(c)
    (wait,) = W.of(c, E.task(c, row["id"]))
    entered = row["visit"]["entered_at"]
    assert (wait.state, wait.who, wait.line) == (W.BOT, row["owner_id"], LINE)
    assert (wait.since, wait.until) == (entered, H.shift(entered, seconds=24 * 3600))
    assert len(_owner_wakes(c, row)) == 1  # one wake when the turn starts, none from the sweeps
    assert E.task(c, row["id"])["stage_id"] == "product-ideas"  # the owner still chooses
    assert N.rules_hold(c) == []


def test_a_task_bug_triage_files_into_product_for_another_bot_is_that_bots_turn(c, product):
    row = X.new_task(c, product, "Export drops the last row", "product", assignee=H.bot_actor("pm"))
    assert row["owner_id"] != product and row["requester_id"] == product
    N.check(c)
    N.check(c)
    row = E.task(c, row["id"])
    assert row["stage_id"] == "product-ideas"
    (wait,) = W.of(c, row)
    assert (wait.state, wait.who, wait.line) == (W.BOT, row["owner_id"], LINE)
    assert wait.until == H.shift(row["visit"]["entered_at"], seconds=24 * 3600)
    assert len(_owner_wakes(c, row)) == 1
    assert N.rules_hold(c) == []


def test_a_hand_off_still_in_ideas_after_24_hours_is_late_and_wakes_the_product_manager_once(c, product, time_machine):
    row = _handed_back(c, product)
    X.settled(c)
    time_machine.shift(timedelta(hours=23, minutes=59))
    assert N.check(c)["late"] == 0
    assert _manager_late_wakes(c, row) == []
    time_machine.shift(timedelta(minutes=2))
    assert N.check(c)["late"] == 1
    assert E.task(c, row["id"])["visit"]["late_told_at"]
    assert len(_manager_late_wakes(c, row)) == 1
    assert N.check(c)["late"] == 0
    assert len(_manager_late_wakes(c, row)) == 1
    assert N.rules_hold(c) == []


@pytest.mark.parametrize("ends", ["spec", "snooze", "close"])
def test_moving_snoozing_or_closing_a_hand_off_ends_its_turn_and_no_late_follows(c, product, time_machine, ends):
    row = _handed_back(c, product)
    owner, version = row["owner_id"], row["version"]
    if ends == "spec":
        c.execute("UPDATE stages SET task_limit=NULL WHERE id='product-spec'")
        E.move(c, row["id"], "product-spec", owner, "Worth a spec", seen_version=version)
    elif ends == "snooze":
        E.snooze(c, row["id"], owner, "Park it until the pricing work lands", seen_version=version)
    else:
        E.move(c, row["id"], E.CLOSED, ANA, "Fixed elsewhere")
    after = E.task(c, row["id"])
    assert not [w for w in W.of(c, after) if w.state == W.BOT and w.line == LINE]
    X.settled(c)
    time_machine.shift(timedelta(hours=25))
    N.check(c)
    assert _manager_late_wakes(c, row) == []
    late = "SELECT 1 FROM visits WHERE task_id=? AND stage_id='product-ideas' AND late_told_at IS NOT NULL"
    assert not c.execute(late, (row["id"],)).fetchone()


def test_a_product_owners_own_brief_still_rests_with_no_turn_and_no_wake(c, product, time_machine):
    brief = X.new_task(c, H.bot_actor("pm"), "Bulk edit for price lists", "product")
    X.settled(c)
    N.check(c)
    (held,) = W.of(c, E.task(c, brief["id"]))
    assert (held.state, held.line) == (W.BY_HAND, "Leaves Ideas by hand")
    time_machine.shift(timedelta(hours=25))
    assert N.check(c)["late"] == 0
    assert X.wakes(c, brief["id"]) == [] or all(":room:" in key for key, _ in X.wakes(c, brief["id"]))
    assert not E.task(c, brief["id"])["visit"]["late_told_at"]


def test_a_task_a_person_moved_back_into_ideas_rests_with_no_turn(c, product):
    bug = X.new_task(c, ANA, "Saved carts lose items", "product")
    X.settled(c)
    N.check(c)
    E.move(c, bug["id"], "product-ideas", ANA, "Not this month")
    X.settled(c)
    N.check(c)
    row = E.task(c, bug["id"])
    (held,) = W.of(c, row)
    assert held.state == W.BY_HAND
    assert _owner_wakes(c, row) == []


def test_a_persons_request_still_moves_to_spec_by_itself_or_waits_for_room(c, product):
    c.execute("UPDATE stages SET task_limit=1 WHERE id='product-spec'")
    first = X.new_task(c, ANA, "Request 1", "product")
    second = X.new_task(c, ANA, "Request 2", "product")
    X.settled(c)
    N.check(c)
    rows = [E.task(c, each["id"]) for each in (first, second)]  # filed in the same second: either goes first
    assert sorted(row["stage_id"] for row in rows) == ["product-ideas", "product-spec"]
    waiting = next(row for row in rows if row["stage_id"] == "product-ideas")
    (wait,) = W.of(c, waiting)
    assert (wait.state, wait.line) == (W.ROOM, "No room in Spec")


def test_time_a_hand_off_spent_snoozed_moves_its_due_time_and_late(c, product, time_machine):
    row = _handed_back(c, product)
    entered = row["visit"]["entered_at"]
    E.snooze(c, row["id"], row["owner_id"], "Park it for two hours", seen_version=row["version"])
    time_machine.shift(timedelta(hours=2))
    E.unsnooze(c, row["id"], ANA)
    X.settled(c)
    (wait,) = W.of(c, E.task(c, row["id"]))
    assert wait.until == H.shift(entered, seconds=26 * 3600)
    time_machine.shift(timedelta(hours=22, minutes=30))  # 24 h 30 min since it entered
    assert N.check(c)["late"] == 0
    time_machine.shift(timedelta(hours=2))  # 26 h 30 min
    assert N.check(c)["late"] == 1
    assert len(_manager_late_wakes(c, row)) == 1
