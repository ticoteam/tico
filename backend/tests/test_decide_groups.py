"""The shared way to ask Decide about many small items (backend/decide_groups.py): items share calls, each gets only
its own answers, a missing answer is asked again alone and a failed call is each item's failure."""

import pytest

from backend import decide_groups as DG
from clients import judge as J

QUESTIONS = {"fit": {"type": "noul", "instructions": "Is this post about standups?"}}


class Counting:
    """A fake Decide that counts calls and answers each item from its own `score` field. `drop` names items (by their
    `n`) whose answers it leaves out of a grouped call; `fail` raises on every grouped call; `swap` answers each grouped
    item with its neighbour's score, so a reader that crossed names would show it."""

    def __init__(self, drop=(), fail=None, swap=False):
        self.calls, self.drop, self.fail, self.swap = [], set(drop), fail, swap

    def __call__(self, state, questions, label=None):
        parts = DG.split(state, questions)
        self.calls.append([one["n"] for _, one, _ in parts] if parts else [state["n"]])
        if parts is None:
            return {"answers": {q: {"noul": state["score"]} for q in questions}, "model": "fake", "cost_usd": 0.01}
        if self.fail:
            raise self.fail
        answers = {}
        for _, one, mine in parts:
            if one["n"] in self.drop:
                continue
            for _, key in mine.items():
                answers[key] = {"noul": one["score"]}
        return {"answers": answers, "model": "fake", "cost_usd": 0.04}


def items(count, questions=QUESTIONS):
    return [({"n": n, "score": round(n / 100, 2), "text": f"post {n}"}, questions) for n in range(count)]


def nouls(replies):
    return [r["answers"]["fit"]["noul"] for r in replies]


def test_ten_items_go_in_one_call_and_each_gets_its_own_answer():
    fake = Counting()
    replies, calls = DG.ask(fake, items(10), "listening.x")
    assert fake.calls == [list(range(10))] and len(calls) == 1
    assert nouls(replies) == [n / 100 for n in range(10)]
    assert all(r["grouped"] == 10 and r["cost_usd"] == pytest.approx(0.004) for r in replies)
    assert calls[0].items == list(range(10)) and [a["fit"]["noul"] for a in calls[0].answers] == nouls(replies)


def test_more_than_ten_items_take_the_fewest_calls_of_ten_at_most():
    fake = Counting()
    replies, _ = DG.ask(fake, items(23), "x")
    assert [len(c) for c in fake.calls] == [10, 10, 3] and nouls(replies) == [n / 100 for n in range(23)]


def test_a_pass_kept_at_one_item_per_call_sends_each_item_as_before():
    fake = Counting()
    sent = []
    DG.ask(lambda s, q, label=None: sent.append((s, q)) or fake(s, q, label), items(3), "x", size=1)
    assert fake.calls == [[0], [1], [2]] and sent == items(3)


def test_an_item_whose_answer_is_missing_is_asked_again_alone_and_never_gets_another_items_answer():
    """AC-9."""
    fake = Counting(drop={4})
    replies, calls = DG.ask(fake, items(10), "x")
    assert fake.calls == [list(range(10)), [4]]
    assert nouls(replies) == [n / 100 for n in range(10)]
    assert replies[4].get("grouped") is None
    assert [c.items for c in calls] == [[0, 1, 2, 3, 5, 6, 7, 8, 9], [4]]


def test_a_grouped_answer_under_another_items_name_is_not_taken():
    """An answer is read under its own item's names only: a model that answered item2's questions for item1 leaves
    item1 unanswered, so it is asked alone."""

    def crossed(state, questions, label=None):
        parts = DG.split(state, questions)
        if parts is None:
            return {"answers": {q: {"noul": state["score"]} for q in questions}}
        _, one, mine = parts[1]
        return {"answers": {mine["fit"]: {"noul": one["score"]}, "item1__other": {"noul": 1.0}}}

    replies, calls = DG.ask(crossed, items(2), "x")
    assert nouls(replies) == [0.0, 0.01] and [c.items for c in calls] == [[1], [0]]


def test_an_unreadable_grouped_answer_asks_each_item_alone():
    fake = Counting(fail=J.JudgeError("shape", "no answer"))
    replies, calls = DG.ask(fake, items(3), "x")
    assert fake.calls == [[0, 1, 2], [0], [1], [2]] and nouls(replies) == [0.0, 0.01, 0.02]
    assert calls[0].error is not None and calls[0].items == [0, 1, 2]


@pytest.mark.parametrize("code", ["unavailable", "spend_cap"])
def test_a_failed_grouped_call_is_each_items_failure(code):
    """AC-10: a timeout or the spend cap fails each item of the call as its own call failing would, and no item is
    asked again here; the pass decides (a try, a wait, a person)."""
    error = J.JudgeError(code, "down")
    fake = Counting(fail=error)
    replies, calls = DG.ask(fake, items(4), "x")
    assert fake.calls == [[0, 1, 2, 3]] and replies == [error] * 4
    assert len(calls) == 1 and calls[0].error is error and calls[0].items == [0, 1, 2, 3]


def test_an_item_the_contract_refuses_alone_is_refused_and_the_rest_are_asked():
    bad = ({"n": 99}, {})  # no questions
    fake = Counting()
    replies, calls = DG.ask(fake, [*items(2), bad], "x")
    assert isinstance(replies[2], J.JudgeError) and replies[2].code == "invalid"
    assert fake.calls == [[0, 1]] and [c.sent for c in calls] == [False, True]


def test_items_too_big_to_share_a_call_go_alone():
    big = "w" * (J.MAX_STATE_CHARS // 2)
    fake = Counting()
    many = [({"n": n, "score": 0.5, "text": big}, QUESTIONS) for n in range(3)]
    DG.ask(fake, many, "x")
    assert fake.calls == [[0], [1], [2]]


def test_questions_over_the_clients_cap_split_the_group(monkeypatch):
    monkeypatch.setattr(J, "MAX_QUESTIONS", 5)
    two = {"a": QUESTIONS["fit"], "b": QUESTIONS["fit"]}
    assert DG.packs(items(5, two)) == [[0, 1], [2, 3], [4]]
    monkeypatch.delattr(J, "MAX_QUESTIONS")
    assert DG.packs(items(5, two)) == [[0, 1, 2, 3, 4]]


def test_the_grouped_call_names_each_item_in_its_questions_and_keeps_their_wording():
    state, questions, ids = DG.grouped(items(2))
    assert set(state["items"]) == {"item1", "item2"} and ids == [{"item1__fit": "fit"}, {"item2__fit": "fit"}]
    text = questions["item2__fit"]["instructions"]
    assert text.startswith("About item2 only") and text.endswith(QUESTIONS["fit"]["instructions"])
    J.validate(state, questions, "x")
