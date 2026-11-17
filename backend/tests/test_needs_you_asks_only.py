"""Needs you holds only what someone asks of the person: an open question to them, or an OK they must give. A task they
own with nothing asked of them lives in Tasks, and a task the never-stuck ladder could take no further stays with its
bot, where the quiet-task rule tells the pipeline's manager (docs/pipelines-v3.md, "Needs you").

Synthetic rows only (backend/tests/helpers/engine.py).
"""

import pytest

from backend import hubdb as H
from backend import never_stuck as NS
from backend import stage_rule_timers as T
from backend import task_engine as E
from backend import waiting_on as W
from backend.tests.helpers import engine as X
from backend.tests.helpers.api import get, post

ANA, BEN = X.ANA, "human:ben"


@pytest.fixture
def c(company):
    with company.transaction() as conn:
        X.bot(conn, "builder")
        X.bot(conn, "manager")
        yield conn
        # Each person's own read is the list every person's pass gives them, and the rules hold.
        every = W.needs_by_person(conn)
        for person in sorted({ANA, BEN, *every}):
            assert W.needs(conn, person) == every.get(person, []), person
        broken = [b for b in NS.rules_hold(conn) if b.startswith("3:") and not b.held]
        assert not broken, broken


def test_a_task_a_person_owns_with_nothing_asked_is_not_in_their_needs_you(c):
    X.pipeline(c)
    X.new_task(c, ANA, "Renew the lease", assignee=ANA)
    X.new_task(c, BEN, "Call the bank for Ana", assignee=ANA)
    assert W.needs(c, ANA) == []
    assert ANA not in W.needs_by_person(c)


def test_an_open_question_and_an_ok_asked_of_a_person_are_in_their_needs_you(c):
    X.pipeline(c, manager="bot:manager")
    mine = X.new_task(c, ANA, "Renew the lease", assignee=ANA)
    asked = X.new_task(c, ANA, "Write the launch note", assignee="bot:builder")
    E.add_requirement(c, asked["id"], "bot:builder", "answer", ANA, "Which date?", seen_version=asked["version"])
    # One open ask per bot on a task: the OK is another task's.
    ok = X.new_task(c, ANA, "Write the press note", assignee="bot:builder")
    E.add_requirement(c, ok["id"], "bot:builder", "ok", ANA, "OK the headline", seen_version=ok["version"])
    # A question on her own task is hers to answer, though the task's own work is not listed.
    E.add_requirement(c, mine["id"], "bot:builder", "answer", ANA, "Which landlord?", seen_version=mine["version"])
    listed = W.needs(c, ANA)
    assert sorted((n["kind"], n["task_id"], n["line"]) for n in listed) == sorted(
        [
            ("ask", asked["id"], "Which date?"),
            ("ask", ok["id"], "OK the headline"),
            ("ask", mine["id"], "Which landlord?"),
        ]
    )


def test_a_task_the_ladder_could_take_no_further_stays_with_its_bot_and_the_quiet_rule_tells_the_manager(c):
    X.pipeline(c, manager="bot:manager")
    stuck = X.new_task(c, ANA, "Fix the footer", assignee="bot:builder")
    c.execute("UPDATE visits SET level='person', person_id=? WHERE id=?", (ANA, stuck["visit"]["id"]))
    row = E.task(c, stuck["id"])
    waits = W.of(c, row)
    assert [w.state for w in waits] == [W.BOT] and waits[0].who == "bot:builder", waits
    assert W.holder(c, row) == "bot:builder"
    assert W.turn(c, row, waits) is None  # the ladder wakes nobody more
    assert all(W.needs(c, person) == [] for person in (ANA, BEN))
    # Six hours quiet: the built-in Quiet bot tasks rule matches it, and the manager is told in its batched wake.
    rule = next(r for r in T.rules(c) if r["id"] == "sr_quiet_bot_tasks")
    assert T._matches(c, rule, {"id": stuck["id"]})


def test_a_change_takes_a_task_the_late_ladder_rested_back_to_its_owner(c):
    X.pipeline(c, manager="bot:manager")
    late = X.new_task(c, ANA, "Fix the footer", assignee="bot:builder")
    c.execute(
        "UPDATE visits SET level='person', person_id=?, late_told_at=?, late_person_at=? WHERE id=?",
        (ANA, H.now(), H.now(), late["visit"]["id"]),
    )
    E.changed(c, late["id"], "bot:builder")
    visit = E.task(c, late["id"])["visit"]
    assert (visit["level"], visit["person_id"]) == ("owner", None)


def test_the_count_is_the_lists_length_and_holds_no_owned_task(api):
    with api.app.state.store.transaction() as c:
        H.task_create(c, "bot:finance", "Give finance the payroll connector", "x", "human:ana")
        asked = H.task_create(c, "human:ana", "Run the ads", "x", "bot:finance", lint=False)
        E.add_requirement(
            c, asked["id"], "bot:finance", "answer", "human:ana", "Which budget?", seen_version=asked["version"]
        )
    post(api, "tasks", {"title": "Renew the domain", "body": "x", "owner": "human:ana"})
    listed = get(api, "needs-you")
    assert [(i["kind"], i["task_id"]) for i in listed["items"]] == [("ask", asked["id"])]
    assert get(api, "needs-you?count=1")["count"] == listed["count"] == len(listed["items"]) == 1
    assert get(api, "needs-you/summary")["by_kind"] == {"ask": 1}
