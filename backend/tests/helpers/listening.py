"""Helpers the listening tests share (backend/listening.py)."""

import re

from backend.tests.helpers.api import post

NOTE = {"id": "tell", "type": "action", "do": "note", "args": {"text": "{{listening.text}}"}}
SEARCH = {"id": "s1", "kind": "search", "site": "reddit", "target": "standup tools"}
MARK = re.compile(r"\[(r\d+)(?::([\d.]+))?\]")


def listening(api, sources=(SEARCH,), rules=(), limits=({"count": 5, "per": "day"},), steps=(NOTE,), key=None, **extra):
    """A listening that is on, sending at 08:00 UTC."""
    trigger = {
        "type": "listening",
        "sources": list(sources),
        "rules": list(rules),
        "limits": list(limits),
        "at": "08:00",
        "tz": "UTC",
    }
    body = {"name": "Standups", "owner": "person:ana", "trigger": trigger, "steps": list(steps), **extra}
    return post(api, "automations", body, key=key)


def source_id(api, aid, key="s1"):
    with api.app.state.store.read() as c:
        return c.execute("SELECT id FROM listening_sources WHERE automation_id=? AND key=?", (aid, key)).fetchone()[0]


def item(n, text, **extra):
    return {"native_id": str(n), "url": f"https://acme.example/post/{n}", "author": "@host", "text": text, **extra}


def save(api, source, items, status="ok", error="", expected=200, key=None):
    body = {"status": status, "error": error, "items": items}
    return post(api, f"listening/sources/{source}/items", body, key=key, expected=expected)


class FakeJudge:
    """Answers a rule yes when the post's text names it: `[r0]` is 0.9 for the first rule, `[r1:0.8]` is 0.8 for the
    second. Every other rule is 0.05."""

    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def __call__(self, state, questions, label=None):
        self.calls.append(state["text"])
        if self.fail:
            raise RuntimeError("down")
        named = {qid: float(value or 0.9) for qid, value in MARK.findall(state["text"])}
        answers = {qid: {"type": "noul", "noul": named.get(qid, 0.05)} for qid in questions}
        return {"answers": answers, "ms": 1, "model": "fake-judge", "usage": {}}
