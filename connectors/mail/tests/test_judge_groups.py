"""The bots' mail triage asks Jev about several messages in one call (backend/decide_groups.py, task #239): the
listing's triage and the rules' sets, each message answered from its own answers only, a missing answer asked again
alone, and a failed call failing each message as its own failed call did."""

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake  # noqa: E402,F401  (puts the hub on sys.path)

from backend import decide_groups as DG  # noqa: E402
from clients import judge as J  # noqa: E402
from connectors.mail import judge as jd  # noqa: E402

BOX = "ana@acme.example"
MAILS = [
    {"id": f"m{n}", "from": f"sender{n}@acme.example", "subject": f"Subject {n}", "snippet": f"Hello {n}", "to": []}
    for n in range(10)
]


def answer(state, question):
    """One question about one message's state, fixed by both: an item grouped or alone gets the same answer."""
    seed = hashlib.sha256(json.dumps([state, question], sort_keys=True, default=str).encode()).digest()[0] / 255
    if question["type"] == "choice":
        options = list(question["criteria"])
        return {"choice": options[int(seed * 1000) % len(options)], "confidence": round(seed, 3)}
    if question["type"] == "score":
        return {"score": float(int(seed * 1000) % len(question["criteria"])), "confidence": round(seed, 3)}
    return {"noul": round(seed, 3)}


class Jev:
    """Counts calls (and each grouped call's items), answers each message from its own state and questions as if asked
    alone (the item's instructions put back), leaves out the answers of any subject in `drop` from a grouped call,
    and fails every grouped call with `down` when set."""

    def __init__(self, drop=(), down=None):
        self.calls, self.drop, self.down = [], set(drop), down

    def __call__(self, state, questions, label=None):
        J.validate(state, questions, label)
        held = DG.split(state, questions)
        self.calls.append({"items": len(held) if held else 1, "label": label})
        if held is None:
            return {"model": J.MODEL, "usage": {}, "ms": 1, "answers": self.own(state, questions)}
        if self.down:
            raise self.down
        answers = {}
        for name, one, ids in held:
            if one["subject"] in self.drop:
                continue
            asked = {qid: questions[key] for qid, key in ids.items()}
            lead = len(f"About {name} only (state.items.{name}): ")
            mine = self.own(one, {qid: {**q, "instructions": q["instructions"][lead:]} for qid, q in asked.items()})
            answers.update({ids[qid]: a for qid, a in mine.items()})
        return {"model": J.MODEL, "usage": {}, "ms": 1, "answers": answers}

    @staticmethod
    def own(state, questions):
        return J.check_answers({qid: answer(state, q) for qid, q in questions.items()}, questions)


def triaged(jev, group):
    jd.GROUP = group
    msgs = [dict(m) for m in MAILS]
    stats = jd.triage(msgs, jev, BOX)
    return msgs, stats


@pytest.fixture(autouse=True)
def group_back():
    yield
    jd.GROUP = 1


def test_ten_messages_are_triaged_in_at_most_two_calls_each_as_alone():
    """AC-8 for the bots' mail triage: ten small messages, at most two calls, each message's judgment the one it gets
    alone."""
    alone, _ = triaged(Jev(), 1)
    jev = Jev()
    grouped, stats = triaged(jev, DG.MOST)
    assert len(jev.calls) <= 2 and sum(c["items"] for c in jev.calls) == 10
    assert [m["judgment"] for m in grouped] == [m["judgment"] for m in alone]
    assert stats["count"] == 10 and stats["errors"] == 0


def test_a_message_missing_from_a_grouped_answer_is_asked_again_alone():
    """AC-9: its own judgment, from its own call; never a neighbour's."""
    alone, _ = triaged(Jev(), 1)
    jev = Jev(drop={"Subject 2"})
    grouped, _ = triaged(jev, DG.MOST)
    assert sorted(c["items"] for c in jev.calls) == [1, 2, 8]  # the two groups (in parallel), then Subject 2 alone
    assert [m["judgment"] for m in grouped] == [m["judgment"] for m in alone]


def test_a_failed_grouped_call_fails_each_message_as_its_own_call_did():
    """AC-10: every message of the failed call shows the call's error, and none is asked again."""
    jev = Jev(down=J.JudgeError("unavailable", "down", retryable=True))
    grouped, stats = triaged(jev, DG.MOST)
    assert all(c["items"] > 1 for c in jev.calls) and len(jev.calls) <= 2
    assert {m["judgment"].get("error") for m in grouped} == {"unavailable"} and stats["errors"] == 10


def test_the_rules_sets_ask_several_messages_per_call_each_message_with_all_its_sets():
    """AC-8 for the rules' sets: ten messages, both sets each, at most two calls, every set's answers as alone."""
    sets = ["mail-triage", "reply-intent"]
    jd.GROUP = 1
    alone = [dict(m) for m in MAILS]
    jd.judge_sets(alone, sets, Jev(), BOX)
    jd.GROUP = DG.MOST
    jev = Jev()
    grouped = [dict(m) for m in MAILS]
    stats = jd.judge_sets(grouped, sets, jev, BOX)
    assert len(jev.calls) <= 2 and {c["label"] for c in jev.calls} == {"mail-triage@1+reply-intent@1"}
    assert [m["judgments"] for m in grouped] == [m["judgments"] for m in alone]
    assert {sid: (s["count"], s["errors"]) for sid, s in stats.items()} == {sid: (10, 0) for sid in sets}
