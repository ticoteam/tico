"""Ideas: say it once (Product task #209, Engineering #227): Product's Ideas is an ordinary stage. An idea that is not
snoozed moves on to Spec by itself when Spec has room, after Jev's duplicate check (one call per pass, two questions
an idea); a bot's own idea is filed snoozed, and only a person unsnoozes it.

Synthetic rows only (backend/tests/helpers/engine.py): the fictional company, where Ana owns it, and a few bots.
"""

import importlib
from datetime import datetime, timedelta, timezone

import pytest

from backend import idea_checks as IC
from backend import never_stuck as N
from backend import pipeline_settings as PS
from backend import task_engine as E
from backend import waiting_on as W
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.product_engineering_presets import _answers, _team

ANA = X.ANA
START = datetime(2026, 11, 12, 7, tzinfo=timezone.utc)


@pytest.fixture
def product(company, time_machine):
    time_machine.move_to(START, tick=False)
    with company.transaction() as c:
        _team(c)
        _answers(c, software="yes")
        PS.ensure_presets(c)
    return company


class Jev:
    """A fake Jev: each call's questions are kept; `says` maps an idea's title to (option title, confidence) for both
    of its questions, and every other idea is new."""

    def __init__(self, says=None, fail=False):
        self.calls = []
        self.says = says or {}
        self.fail = fail

    def __call__(self, state, questions, label):
        self.calls.append((state, questions, label))
        if self.fail:
            raise IC.J.JudgeError("unavailable", "down", retryable=True)
        answers = {}
        for n, idea in enumerate(state["ideas"]):
            title, sure = self.says.get(idea["title"], (None, 0.95))
            same = questions[f"same_{n}"]["criteria"]
            pick = next((name for name, text in same.items() if title and str(text).endswith(title)), None)
            answers[f"same_{n}"] = {"choice": pick or "new", "confidence": sure}
            answers[f"merge_{n}"] = {"choice": pick or "none", "confidence": sure}
        return {"answers": answers, "model": "fake-jev", "cost_usd": 0.0}


@pytest.fixture
def jev(monkeypatch):
    fake = Jev()
    monkeypatch.setattr(IC, "ENGINE", fake)
    return fake


def _file(store, by, title, **kw):
    with store.transaction() as c:
        return X.new_task(c, by, title, "product", **kw)


def _pass(store):
    """One beat: the duplicate pass, then the never-stuck pass."""
    checked = IC.run(store)
    with store.transaction() as c:
        X.settled(c)
        N.check(c)
    return checked


def _stage(store, row):
    with store.read() as c:
        return E.task(c, row["id"])["stage_id"]


def test_a_persons_idea_moves_into_spec_after_jevs_answer_with_no_room_notice(product, jev):
    idea = _file(product, ANA, "Say yes once")
    with product.transaction() as c:
        N.check(c)  # before Jev answered: it waits on Jev, nobody is woken
        row = E.task(c, idea["id"])
        assert row["stage_id"] == "product-ideas" and row.get("snooze") is None
        (wait,) = W.of(c, row)
        assert wait.state == W.JEV
    assert _pass(product) == 1
    assert _stage(product, idea) == "product-spec"
    with product.read() as c:
        assert c.execute("SELECT COUNT(*) FROM messages WHERE wake_key LIKE 'room:%'").fetchone()[0] == 0
        assert IC.state_of(c, idea["id"])["outcome"] == IC.NEW
    assert len(jev.calls) == 1


def test_with_spec_full_ideas_wait_and_go_in_oldest_first(product, jev):
    with product.transaction() as c:
        c.execute("UPDATE stages SET task_limit=1 WHERE id='product-spec'")
    first = _file(product, ANA, "First")
    second = _file(product, ANA, "Second")
    _pass(product)
    assert (_stage(product, first), _stage(product, second)) == ("product-spec", "product-ideas")
    with product.read() as c:
        (wait,) = W.of(c, E.task(c, second["id"]))
        assert (wait.state, wait.line) == (W.ROOM, "No room in Spec")
    with product.transaction() as c:
        E.move(c, first["id"], E.CLOSED, ANA, "Done elsewhere")
        N.check(c)
    assert _stage(product, second) == "product-spec"


def test_a_bots_own_idea_is_filed_snoozed_and_only_a_person_unsnoozes_it(product, jev):
    brief = _file(product, "bot:pm", "Runner heartbeat replies are half a megabyte")
    with product.read() as c:
        row = E.task(c, brief["id"])
        snooze = row["snooze"]
        assert snooze and snooze["until_at"] is None and snooze["filed"] == 1 and snooze["by_id"] == "bot:pm"
        assert W.of(c, row)[0].state == W.SNOOZED
    _pass(product)
    assert _stage(product, brief) == "product-ideas"  # snoozed: it does not move
    with product.transaction() as c:
        with pytest.raises(H.Refused) as refused:
            E.unsnooze(c, brief["id"], "bot:pm")
        assert refused.value.rule == "idea_filed_snoozed"
        with pytest.raises(H.Refused):
            row = E.task(c, brief["id"])
            E.snooze(c, brief["id"], "bot:pm", "Park it a week", "2026-11-19T07:00:00Z", row["version"])
    with product.transaction() as c:
        E.unsnooze(c, brief["id"], ANA)  # one click on the moon
        N.check(c)
    assert _stage(product, brief) == "product-spec"


def test_a_bots_filed_idea_it_owns_is_not_moved_on_as_its_own_wait(product, jev):
    """Given a bot's idea filed snoozed that the same bot owns, and Spec has room, when the passes run and its checks
    end its owner's own snoozes, then the snooze stays and the idea stays in Ideas: only a person ends a filed snooze."""
    brief = _file(product, "bot:pm", "Runner heartbeat replies are half a megabyte")
    with product.transaction() as c:
        assert E.task(c, brief["id"])["owner_id"] == "bot:pm"  # the preset gives Ideas to the Product Manager
        row = E.task(c, brief["id"])
        assert not E.own_snooze(row)
        assert not E.move_on_past_own_snooze(c, brief["id"], "checks passed")
    _pass(product)
    _pass(product)
    with product.read() as c:
        row = E.task(c, brief["id"])
        assert row["stage_id"] == "product-ideas" and row["snooze"] and row["snooze"]["filed"] == 1


def test_a_bots_snooze_of_its_own_choice_is_still_its_own_to_end(product, jev):
    idea = _file(product, ANA, "Export to CSV")
    with product.transaction() as c:
        E.snooze(c, idea["id"], "bot:pm", "Waits for the CSV library", None, E.task(c, idea["id"])["version"])
        assert E.unsnooze(c, idea["id"], "bot:pm").get("snooze") is None


def test_three_ideas_in_one_pass_are_one_call_with_six_questions(product, jev):
    for title in ("One", "Two", "Three"):
        _file(product, ANA, title)
    _pass(product)
    (state, questions, label) = jev.calls[0]
    assert len(jev.calls) == 1 and label == "idea-check@1"
    assert sorted(questions) == sorted(f"{q}_{n}" for q in ("same", "merge") for n in range(3))
    # Each idea is offered the others, never itself.
    assert not any(str(text).endswith("One") for text in questions["same_0"]["criteria"].values())
    assert any(str(text).endswith("Two") for text in questions["same_0"]["criteria"].values())
    with product.read() as c:
        (detail,) = c.execute("SELECT detail FROM events WHERE action='judge.call' ORDER BY rowid DESC").fetchone()
    assert '"questions": 6' in detail and "One" not in detail  # the audit keeps no idea's text


def _requesters(c, task_id):
    return [r[0] for r in c.execute("SELECT actor FROM task_requesters WHERE task_id=?", (task_id,))]


def test_a_sure_duplicate_is_closed_linked_and_its_person_added_to_the_original(product, jev):
    original = _file(product, ANA, "Gmail reads one message per round trip")
    _pass(product)
    bea = "human:ben"
    jev.says["Gmail reads each message on its own"] = ("Gmail reads one message per round trip", 0.9)
    dup = _file(product, bea, "Gmail reads each message on its own")
    _pass(product)
    with product.read() as c:
        row = H.task(c, dup["id"])
        assert row["status"] == "closed"
        relation = c.execute(
            "SELECT kind FROM task_relations WHERE from_task=? AND to_task=?", (dup["id"], original["id"])
        ).fetchone()
        assert relation and relation[0] == "duplicate_of"
        assert bea in _requesters(c, original["id"])
        said = [m["body"] for m in H.task_comments(c, original["id"])]
        assert any(text.startswith("Also asked by") for text in said)
        assert IC.state_of(c, dup["id"])["outcome"] == IC.DUPLICATE


def test_under_085_a_failed_call_or_no_answer_in_two_minutes_leaves_the_idea_to_go_on(product, jev, time_machine):
    jev.says["Close enough"] = ("Original", 0.84)
    _file(product, ANA, "Original")
    _pass(product)
    near = _file(product, ANA, "Close enough")
    _pass(product)
    assert _stage(product, near) == "product-spec"
    jev.fail = True
    failed = _file(product, ANA, "Jev is down")
    _pass(product)
    with product.read() as c:
        assert IC.state_of(c, failed["id"])["outcome"] == IC.FAILED
    assert _stage(product, failed) == "product-spec"
    # The step never ran: past two minutes the idea goes on without the check.
    late = _file(product, ANA, "Nobody checked")
    with product.transaction() as c:
        N.check(c)
    assert _stage(product, late) == "product-ideas"
    time_machine.move_to(START + timedelta(minutes=2, seconds=1), tick=False)
    with product.transaction() as c:
        N.check(c)
    assert _stage(product, late) == "product-spec"


def test_a_persons_idea_that_repeats_a_bots_idea_in_ideas_closes_the_bots(product, jev):
    brief = _file(product, "bot:pm", "Assistant carries out an agreed plan")
    _pass(product)
    jev.says["Say yes once"] = ("Assistant carries out an agreed plan", 0.95)
    mine = _file(product, ANA, "Say yes once")
    _pass(product)
    with product.read() as c:
        assert H.task(c, brief["id"])["status"] == "closed"
        assert H.task(c, mine["id"])["stage_id"] == "product-spec"
        assert IC.state_of(c, mine["id"])["outcome"] == IC.KEPT


def test_on_ice_is_gone_from_what_the_board_and_task_page_say(product):
    with product.read() as c:
        purpose = c.execute("SELECT purpose, by_hand FROM stages WHERE id='product-ideas'").fetchone()
    assert purpose[1] == 0 and "by hand" not in purpose[0]


def test_release_snoozes_bot_ideas_and_owes_one_check_for_each_idea(product, jev):
    with product.transaction() as c:
        c.execute("UPDATE stages SET by_hand=1, requests_move_on=1 WHERE id='product-ideas'")
        mine = X.new_task(c, ANA, "Asked by a person", "product")
        brief = X.new_task(c, "bot:pm", "A bot's brief", "product")
        c.execute("UPDATE snoozes SET ended_at=? WHERE task_id=?", (H.now(), brief["id"]))
        c.execute("DELETE FROM idea_checks")
        migration = importlib.import_module("backend.migrations.20261112190728_ideas_say_it_once")
        migration.up(c)
        migration.up(c)  # again: nothing more
        assert tuple(c.execute("SELECT by_hand, requests_move_on FROM stages WHERE id='product-ideas'").fetchone()) == (
            0,
            0,
        )
        assert E.task(c, brief["id"])["snooze"]["filed"] == 1
        assert E.task(c, mine["id"]).get("snooze") is None
        assert c.execute("SELECT COUNT(*) FROM idea_checks WHERE checked_at IS NULL").fetchone()[0] == 2
        assert (
            c.execute("SELECT COUNT(*) FROM snoozes WHERE task_id=? AND ended_at IS NULL", (brief["id"],)).fetchone()[0]
            == 1
        )
    assert IC.run(product) == 2 and len(jev.calls) == 1
