"""Helpers that other tests share, moved out of test_org_rule_check.py so they import no test file."""

import json
import pytest
from backend.tests.helpers.api import headers, post
from backend.tests.helpers.org_rules import org, rule


COLOR = "Never tell anyone Acme's favorite color."


NEEDLE = "favorite color is"


class Judge:
    """Answers rule-check@1: a rule is broken when the output holds that rule's needle. It notes whether the store's
    write lock was held when it was asked, which must never happen."""

    def __init__(self, store, needles=None):
        self.store, self.needles = store, dict(needles or {COLOR: NEEDLE})
        self.calls, self.locked, self.fail, self.hold = [], [], None, None

    def __call__(self, state, questions, label=None, level=None):
        self.locked.append(bool(self.store.writer.queue))
        self.calls.append({"state": state, "questions": questions, "label": label, "level": level})
        if self.hold:
            self.hold.wait(5)
        if self.fail:
            raise self.fail
        text = (state["output"] + " " + json.dumps(state.get("context") or {})).lower()
        answers = {}
        for rule_row in state["rules"]:
            broken = any(key in rule_row["text"] and needle.lower() in text for key, needle in self.needles.items())
            answers[rule_row["id"]] = {"type": "noul", "noul": 0.95 if broken else 0.05}
        return {"model": "jev-1.14", "usage": {"input_tokens": 300, "output_tokens": 0}, "ms": 40, "answers": answers}


@pytest.fixture
def ruled(api):
    org(api)
    judge = Judge(api.app.state.store)
    api.app.state.judge = judge
    return api, judge


def color_rule(api, if_broken="block", **more):
    return rule(api, {"kind": "internal", "text": COLOR, "who": {"bots": ["ops"]}, "if_broken": if_broken, **more})


def say(api, text, to="ana", token="ops-test", **more):
    r = api.post("/api/v2/messages", json={"to": to, "text": text, **more}, headers=headers(token))
    assert r.status_code == 200, r.text
    return r.json()


def sql(api, query, args=()):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute(query, args)]


def events(api, action):
    return sql(api, "SELECT actor, target, detail FROM events WHERE action=? ORDER BY rowid", (action,))


def bodies(api, text):
    return sql(api, "SELECT id FROM messages WHERE body LIKE ?", (f"%{text}%",))


def hold_row(api, hold_id):
    return sql(api, "SELECT * FROM org_rule_holds WHERE id=?", (hold_id,))[0]


def chat(api, text, to="finance", token="ops-test"):
    r = api.post(f"/api/v2/chat/{to}", json={"text": text}, headers=headers(token))
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture(autouse=True)
def not_a_friday(monkeypatch):
    """Autouse wherever it is imported. A Friday's update must be a week in review (updates.kind_for); these tests post daily ones, so pin a Tuesday."""
    from backend import updates as _updates

    monkeypatch.setattr(_updates, "today", lambda at=None: "2026-09-29")


def task_for_ops(api, title="Write the launch note"):
    return post(api, "tasks", {"owner": "ops", "title": title, "body": "Draft it."})


def hand_over(api):
    """An automation of ops that assigns its run's task to Ana."""
    step = {"id": "a", "type": "action", "do": "assign", "args": {"owner": "ana"}}
    return post(
        api,
        "automations",
        {"name": "Hand", "owner": "bot:ops", "trigger": {"type": "event", "name": "x.y"}, "steps": [step]},
    )["id"]


LAUNCH = "Never share the launch date."
