"""Helpers that other tests share, moved out of test_thread_attention.py so they import no test file."""

from backend import threads
from backend import hubdb as H


def members_thread(api, members=("human:ana",), title="Launch"):
    with api.app.state.store.transaction() as c:
        return threads.create(c, "human:cara", title=title, audience="members", members=list(members))["id"]


def say(api, thread_id, text, *, actor="human:cara", **kw):
    with api.app.state.store.transaction() as c:
        return threads.post(c, actor, thread_id, text, **kw)


def hold(api, reviewer="human:ana", author="bot:finance", operation="reply", state="held"):
    """A held write as org_rule_check stores it (its verdict and payload are not read by the list)."""
    with api.app.state.store.transaction() as c:
        hid = H.new_id()
        c.execute(
            "INSERT INTO org_rule_holds(id, operation, payload, author, destinations, rule_ids, "
            "verdict, nonce, state, reviewer, task_id, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (hid, operation, "{}", author, "[]", "[]", "{}", "nonce", state, reviewer, None, H.now()),
        )
    return hid
