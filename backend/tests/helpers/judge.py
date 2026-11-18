"""Helpers that other tests share, moved out of test_judge.py so they import no test file."""

import json


QUESTIONS = {
    "bucket": {
        "type": "choice",
        "instructions": "Where does it go?",
        "criteria": {"archive": "noise", "reply": "answer it"},
    },
    "is_ask": {"type": "noul", "instructions": "Is someone asking for something?"},
}


class FakeJudge:
    def __init__(self):
        self.calls, self.fail = [], None
        self.choices = {}  # question name -> the option answered when a call asks that question

    def __call__(self, state, questions, label=None):
        self.calls.append({"state": state, "questions": questions, "label": label})
        if self.fail:
            raise self.fail
        answers = {
            "bucket": {
                "type": "choice",
                "choice": "reply",
                "confidence": 0.8,
                "probabilities": {"archive": 0.2, "reply": 0.8},
            },
            "is_ask": {"type": "noul", "noul": 0.9},
        }
        for name, option in self.choices.items():
            if name in questions:
                answers[name] = {
                    "type": "choice",
                    "choice": option,
                    "confidence": 0.9,
                    "probabilities": {key: float(key == option) for key in questions[name]["criteria"]},
                }
        return {
            "model": "judge-1.13.0",
            "usage": {"input_tokens": 90, "output_tokens": 0},
            "ms": 180,
            "answers": answers,
        }


def fake(api, **choices):
    """The fake decision model as the server's; each keyword is a question and the option it answers with."""
    engine = FakeJudge()
    engine.choices = choices
    api.app.state.judge = engine
    return engine


def events(api, actor):
    with api.app.state.store.read() as c:
        return [
            (r["target"], json.loads(r["detail"]))
            for r in c.execute(
                "SELECT target, detail FROM events WHERE action='judge.call' AND actor=? ORDER BY created_at", (actor,)
            )
        ]
