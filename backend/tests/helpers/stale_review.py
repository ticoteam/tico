"""Helpers that other tests share, moved out of test_stale_review.py so they import no test file."""

import pytest
from backend import hubdb as H
from backend import team_rules
from clients import judge as J


class FakeDecide:
    """Answers `verdict` as told, as the Gateway would; `fail` raises instead."""

    def __init__(self, verdict="update", confidence=0.9, provider=J.GATEWAY_PROVIDER):
        self.verdict, self.confidence, self.provider = verdict, confidence, provider
        self.calls, self.fail = [], None

    def __call__(self, state, questions, label=None):
        self.calls.append({"state": state, "questions": questions, "label": label})
        if self.fail:
            raise self.fail
        return {
            "model": J.GATEWAY_MODEL,
            "provider": self.provider,
            "usage": {"input_tokens": 300},
            "ms": 90,
            "cost_usd": 0.00002,
            "answers": {"verdict": {"type": "choice", "choice": self.verdict, "confidence": self.confidence}},
        }


@pytest.fixture
def stale(api):
    """The switch on, and a task maker that backdates a task so it is not quiet by accident."""
    store = api.app.state.store
    with store.transaction() as c:
        team_rules.save(c, "human:ana", {"stale_review": True})

    def task(title, owner="bot:ops", status="review", requester="human:ana", days_ago=1, pr=None):
        with store.transaction() as c:
            row = H.task_create(c, requester, "Placeholder " + H.new_id(), "x", owner)
            when = H.shift(H.now(), days=-days_ago)
            c.execute(
                "UPDATE tasks SET title=?,status=?,updated_at=?,created_at=? WHERE id=?",
                (title, status, when, when, row["id"]),
            )
            if pr:
                c.execute(
                    "INSERT INTO task_links(id,task_id,kind,url,title,state,added_by,created_at,pr_merged_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        H.new_id(),
                        row["id"],
                        "pr",
                        "https://github.com/acme/site/pull/12",
                        "PR",
                        pr,
                        "bot:ops",
                        when,
                        "2026-10-16T10:00:00Z",
                    ),
                )
        return row["id"]

    return api, store, task
