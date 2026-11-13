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


def test_questions_too_big_for_one_call_split_the_group_and_any_count_fits():
    # Small states, big questions: two items' questions fit one call (J.MAX_CALL_CHARS), three do not.
    long = "w" * (J.MAX_INSTRUCTIONS - 100)
    big = {f"q{n}": {"type": "noul", "instructions": long} for n in range(J.MAX_CALL_CHARS * 2 // 5 // len(long))}
    assert DG.packs(items(5, big)) == [[0, 1], [2, 3], [4]]
    for run in DG.packs(items(5, big)):
        state, questions, _ = DG.grouped(items(len(run), big))
        J.validate(state, questions, "x")
    # No count of questions splits a group: only the call's size does.
    many = {f"q{n}": QUESTIONS["fit"] for n in range(30)}
    assert DG.packs(items(5, many)) == [[0, 1, 2, 3, 4]]


def test_the_grouped_call_names_each_item_in_its_questions_and_keeps_their_wording():
    state, questions, ids = DG.grouped(items(2))
    assert set(state["items"]) == {"item1", "item2"} and ids == [{"item1__fit": "fit"}, {"item2__fit": "fit"}]
    text = questions["item2__fit"]["instructions"]
    assert text.startswith("About item2 only") and text.endswith(QUESTIONS["fit"]["instructions"])
    J.validate(state, questions, "x")


class Hub:
    """The hub's decisions route as the real client reads it (`J.through_hub`): one item's answers left out of a
    grouped call, every answer present otherwise."""

    def __init__(self, drop):
        self.drop, self.bodies = drop, []

    def post(self, path, body):
        self.bodies.append(body)
        state, questions = body["state"], body["questions"]
        parts = DG.split(state, questions)
        if parts is None:
            return {"model": "jev", "answers": {q: {"noul": state["score"]} for q in questions}}
        answers = {
            key: {"noul": one["score"]} for _, one, mine in parts if one["n"] != self.drop for key in mine.values()
        }
        return {"model": "jev", "answers": answers}


def test_through_the_real_client_one_missing_answer_asks_only_that_item_again():
    """AC-9 with the real transport, which checks the whole answer map: within a grouped call it keeps the answers that
    are there, so the nine answered items keep theirs and only the tenth is asked again."""
    hub = Hub(drop=3)
    client = J.through_hub(hub)

    def metered(state, questions, label=None):  # the server's meter adds what each call cost
        return {**client(state, questions, label), "cost_usd": 0.1 if DG.split(state, questions) else 0.01}

    replies, _ = DG.ask(metered, items(10), "x")
    assert len(hub.bodies) == 2 and hub.bodies[1]["state"]["n"] == 3
    assert nouls(replies) == [n / 100 for n in range(10)]
    assert replies[3]["cost_usd"] == pytest.approx(0.01 + 0.01), "its share of the grouped call and its own call"
    assert sum(r["cost_usd"] for r in replies) == pytest.approx(0.1 + 0.01)


def test_outside_a_grouped_call_the_client_still_refuses_a_missing_answer():
    with pytest.raises(J.JudgeError) as refused:
        J.check_answers({}, QUESTIONS)
    assert refused.value.code == "shape"
