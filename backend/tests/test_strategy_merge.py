"""The Strategy Bot's merged asks (backend/strategy_merge.py): several bots' questions to one person become one item;
the person's answer to it is their answer to each, and an original that changed since is back as it was. On a store
with no app."""

import pytest

from backend import strategy_bot, strategy_merge as SM, strategy_suggest
from backend.store import H
from backend.tests.helpers import engine as X

ANA = "human:ana"


@pytest.fixture
def c(company, monkeypatch):
    monkeypatch.setattr(strategy_suggest, "suggests", lambda conn: True)
    with company.transaction() as conn:
        for slug in ("sales", "support", strategy_bot.SLUG):
            X.bot(conn, slug)
        yield conn


def _asked(c, bot, title):
    task = H.task_create(c, ANA, title, "Plan it.", bot, lint=False)
    asked = H.task_ask(c, bot, task["id"], ANA, "Which region goes first?", seen_version=task["version"])
    return task["id"], asked["requirement_id"]


def test_one_answer_to_the_merged_item_answers_every_original(c):
    one, ask_one = _asked(c, "bot:sales", "Plan the sales launch")
    two, ask_two = _asked(c, "bot:support", "Plan the support launch")
    made = SM.merge(c, SM.STRATEGY_BOT, ANA, "Which region goes first?", "Which region goes first?", [one, two])
    item = made["task"]["id"]
    assert SM.hidden(c, ANA) == {ask_one, ask_two}
    asked = H.task_view(c, item, ANA)["to_leave"][-1]
    H.task_answer(c, ANA, item, asked["requirement_id"], "Europe")  # passes it on (answers.answer calls on_answer)
    assert SM.on_answer(c, ANA, item, "Europe") is None  # nothing is left to pass on
    for task, ask in ((one, ask_one), (two, ask_two)):
        found = next(r for r in H.task_view(c, task, ANA)["to_leave"] if r["requirement_id"] == ask)
        assert (found["met"], found["answer"]["value"], found["answer"]["by"]) == (True, "Europe", ANA)
    assert H.task(c, item)["status"] == "done" and SM.hidden(c, ANA) == set()


def test_only_bots_open_questions_to_the_person_merge_and_one_withdrawn_since_is_not_answered(c):
    one, _ = _asked(c, "bot:sales", "Plan the sales launch")
    plain = H.task_create(c, ANA, "Write the notes", "Write them.", "bot:support", lint=False)
    with pytest.raises(H.Refused, match="no question"):
        SM.merge(c, SM.STRATEGY_BOT, ANA, "Region?", "Which region goes first?", [one, plain["id"]])
    two, ask_two = _asked(c, "bot:support", "Plan the support launch")
    item = SM.merge(c, SM.STRATEGY_BOT, ANA, "Region?", "Which region goes first?", [one, two])["task"]["id"]
    H.task_withdraw_ask(c, "bot:support", ask_two, "Decided it myself.")
    assert SM.on_answer(c, ANA, item, "Europe").startswith("Answered; passed to 1 bot(s). 1 could not be")
    assert [o["state"] for o in SM.originals(c, item)] == ["answered", "released"]


def test_a_persons_comment_on_the_merged_item_is_only_a_comment(c):
    """The answer is what passes on (answers.answer calls on_answer); a comment on the Strategy Bot's item is written
    like any other, and nothing calls the removed on_reply."""
    one, ask_one = _asked(c, "bot:sales", "Plan the sales launch")
    two, _ = _asked(c, "bot:support", "Plan the support launch")
    item = SM.merge(c, SM.STRATEGY_BOT, ANA, "Which region goes first?", "Which region goes first?", [one, two])
    H.task_comment(c, ANA, item["task"]["id"], "Thinking about it.")
    found = next(r for r in H.task_view(c, one, ANA)["to_leave"] if r["requirement_id"] == ask_one)
    assert found["met"] is False
