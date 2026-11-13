"""Helpers that other tests share, moved out of test_ask_ingest.py so they import no test file."""

import pytest
from backend import ask_ingest as A
from backend import hubdb as H
from backend.tests.helpers.tasks_board import bot_token


class FakeModel:
    """Answers the set as told: clear/recommends as probabilities, kind, meets (the option whose line holds
    `meets`), covered (the option whose title holds `covered`) and urgency 0-3. A chore by default: a decision is
    refused outright (asked as a question instead), so the budget is read for a chore."""

    def __init__(self, clear=0.95, kind="chore", meets=None, urgency=1.0, recommends=0.1, covered=None):
        self.clear, self.kind, self.meets, self.urgency = clear, kind, meets, urgency
        self.recommends, self.covered = recommends, covered
        self.calls, self.fail, self.hold, self.before = [], None, None, None

    def __call__(self, state, questions, label=None):
        self.calls.append({"state": state, "questions": questions, "label": label})
        if self.before:  # a test changes the world between the judgment and the write
            self.before()
        if self.hold:
            self.hold.wait(5)
        if self.fail:
            raise self.fail

        def pick(qid, needle, default):
            options = questions.get(qid, {}).get("criteria") or {}
            return next((n for n, text in options.items() if needle and needle in str(text)), default)

        answers = {
            "clear": {"type": "noul", "noul": self.clear},
            "kind": {"type": "choice", "choice": self.kind, "confidence": 0.9},
            "urgency": {"type": "score", "score": self.urgency, "confidence": 0.9},
            "recommends": {"type": "noul", "noul": self.recommends},
        }
        if "meets" in questions:
            answers["meets"] = {"type": "choice", "choice": pick("meets", self.meets, "none"), "confidence": 0.9}
        if "covered" in questions:
            answers["covered"] = {"type": "choice", "choice": pick("covered", self.covered, "new"), "confidence": 0.9}
        return {"model": "jev-1.14", "usage": {"input_tokens": 400, "output_tokens": 0}, "ms": 120, "answers": answers}


@pytest.fixture
def gate(api):
    """Ben's checks On (budget 10, the default criteria), ops reporting to cpo, a fake model."""
    model = FakeModel()
    api.app.state.judge = model
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET reports_to='cpo' WHERE bot='ops'")
        A.save(
            c,
            "human:ben",
            "human:ben",
            {"mode": "on", "budget": 10, "chores_budget": 5, "criteria": A.DEFAULTS["criteria"]},
        )
    return api, model, bot_token(api, "ops")


def plate(api, n, person="human:ben"):
    """Put n open items on the person's plate, filed by a person (not gated)."""
    with api.app.state.store.transaction() as c:
        for i in range(n):
            H.task_create(c, "human:ana", f"Load item {i} for {person}", "", person, lint=False)


def items(api):
    with api.app.state.store.read() as c:
        return H._rows(c.execute("SELECT * FROM ask_ingest_items ORDER BY created_at"))


def calls(api):
    with api.app.state.store.read() as c:
        return H._rows(
            c.execute("SELECT * FROM events WHERE action='judge.call' AND target LIKE 'person-ask-ingest@%'")
        )
