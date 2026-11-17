"""When the never-stuck ladder stops and the Quiet bot tasks rule cannot reach a manager, a person who can read the
task gets a ❓ on it from the bot that stalled (or BotOps for it), in their Needs you (backend/never_stuck.py
`ask_person`). Each case is one the review
of the change that took stopped tasks out of Needs you reproduced: nobody heard of the task before.

Synthetic rows only (backend/tests/helpers/engine.py), on a fake clock, through completed runs and real passes.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from backend import never_stuck as NS
from backend import task_engine as E
from backend import waiting_on as W
from backend.store import H
from backend.tests.helpers import engine as X

ANA = X.ANA
START = datetime(2030, 1, 7, 9, tzinfo=timezone.utc)


@pytest.fixture
def c(company, time_machine):
    time_machine.move_to(START, tick=False)
    with company.transaction() as conn:
        for bot in ("builder", "manager", "reviewer"):
            X.bot(conn, bot)
        yield conn


def _stop(c, time_machine, task_id, bot):
    """The bot (and the manager bot) stop early on every wake until the ladder stops; then seven quiet hours."""
    for minutes in (15, 15, 50, 2, 15, 50, 2):
        X.ran(c, task_id, bot)
        X.ran(c, task_id, "bot:manager")
        time_machine.shift(timedelta(minutes=minutes))
        NS.check(c)
    assert E.task(c, task_id)["visit"]["level"] == "person", "the ladder did not stop"
    time_machine.move_to(START + timedelta(hours=7))
    NS.check(c)


def _asked(c, person, task_id):
    """The ❓ on the task in the person's Needs you."""
    return [n for n in W.needs(c, person) if n["kind"] == "question" and n["task_id"] == task_id]


def _author(c, item):
    """Who wrote the question: a bot, never Tico (only bots mark a ❓)."""
    return c.execute("SELECT bot_id FROM question_marks WHERE id=?", (item["mark_id"],)).fetchone()[0]


@pytest.mark.parametrize("case", ["no_manager", "manager_owns_task", "private_manager_cannot_read"])
def test_a_stopped_task_no_manager_can_hear_of_asks_a_person(c, time_machine, case):
    manager = {"no_manager": None, "manager_owns_task": "bot:builder"}.get(case, "bot:manager")
    X.pipeline(c, manager=manager)
    task_id = X.new_task(c, ANA, "Write the launch note", assignee="bot:builder")["id"]
    if case == "private_manager_cannot_read":
        c.execute("UPDATE tasks SET private=1, readers=? WHERE id=?", (json.dumps([ANA, "bot:builder"]), task_id))
    _stop(c, time_machine, task_id, "bot:builder")
    [ask] = _asked(c, ANA, task_id)
    assert ask["line"] == "I'm stuck on To do: I stopped 3 times without finishing. Can you help or tell me what to do?"
    assert _author(c, ask) == "bot:builder"
    assert E.task(c, task_id)["visit"]["level"] == "person"  # asking her does not wake the bot again
    assert NS.rules_hold(c) == []


def test_a_stopped_task_whose_visit_was_already_late_asks_a_person(c, time_machine):
    X.pipeline(c, manager="bot:manager")
    c.execute("UPDATE stages SET expected_minutes=20 WHERE id='general-todo'")
    task_id = X.new_task(c, ANA, "Write the launch note", assignee="bot:builder")["id"]
    _stop(c, time_machine, task_id, "bot:builder")
    assert E.task(c, task_id)["visit"]["late_told_at"]
    assert len(_asked(c, ANA, task_id)) == 1


def test_a_persons_task_whose_bot_never_answered_asks_her_again(c, time_machine):
    X.pipeline(c, manager="bot:manager")
    row = X.new_task(c, ANA, "Write the launch note", assignee=ANA)
    X.old_question(c, row["id"], ANA, "bot:reviewer", "Please review")  # an older ask of a bot, which it never answers
    _stop(c, time_machine, row["id"], "bot:reviewer")
    [ask] = _asked(c, ANA, row["id"])
    assert (
        ask["line"].startswith("I'm stuck on To do: I could not answer your ask.") and _author(c, ask) == "bot:reviewer"
    )


def test_an_answer_starts_the_ladder_again(c, time_machine):
    X.pipeline(c)
    task_id = X.new_task(c, ANA, "Write the launch note", assignee="bot:builder")["id"]
    _stop(c, time_machine, task_id, "bot:builder")
    assert _asked(c, ANA, task_id)
    H.task_comment(c, ANA, task_id, "Try once more, smaller")  # her reply on the task's chat clears the ❓
    assert E.task(c, task_id)["visit"]["level"] == "owner" and not _asked(c, ANA, task_id)


def test_a_release_only_a_person_cuts_asks_them_after_a_week(company, time_machine):
    from backend.item_sources import github as F
    from backend.tests.helpers.fake_github import FakeGitHub
    from backend.tests.helpers.release_cut import REPO, to_release

    time_machine.move_to(START, tick=False)
    gh = FakeGitHub()
    gh.history[REPO] = [gh.sha()]
    gh.publish(REPO, "v0.9.0")
    task_id = to_release(company, gh, release={})
    time_machine.shift(timedelta(days=8))
    F.refresh(company, gh, H.now())
    with company.transaction() as c:
        c.execute("UPDATE stages SET expected_minutes=1440 WHERE id='eng-release'")
        NS.check(c)
        person = W.person_for(c, E.task(c, task_id))
        [ask] = _asked(c, person, task_id)
        assert "this waits for the next release, which only a person cuts" in ask["line"]
        assert _author(c, ask) == E.task(c, task_id)["owner_id"]  # the release's own bot asks


def test_a_bot_that_finishes_after_all_takes_tico_s_question_with_it(c, time_machine):
    X.pipeline(c)
    task_id = X.new_task(c, ANA, "Write the launch note", assignee="bot:builder")["id"]
    _stop(c, time_machine, task_id, "bot:builder")
    assert _asked(c, ANA, task_id)
    X.say_done(c, E.task(c, task_id))
    E.advance(c, task_id)
    assert E.task(c, task_id)["status"] == "done" and not _asked(c, ANA, task_id)


def test_botops_asks_for_a_stalled_bot_that_cannot_run(c):
    """A stopped task whose bot was paused since (the Quiet rule finds no manager for it): BotOps asks for it."""
    X.bot(c, "botops")
    X.pipeline(c)
    task_id = X.new_task(c, ANA, "Write the launch note", assignee="bot:builder")["id"]
    c.execute("UPDATE visits SET level='person' WHERE task_id=? AND left_at IS NULL", (task_id,))
    c.execute("UPDATE bots SET state='paused' WHERE id='builder'")
    NS.ask_person(c, E.task(c, task_id))
    [ask] = _asked(c, ANA, task_id)
    assert _author(c, ask) == "bot:botops" and ask["line"].startswith("builder is stuck on To do:")
