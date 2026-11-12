"""A webhook worker that outlived its claim (stalled past STALE, so recovery handed the send back and another worker
claimed it) cannot settle, release or post the send the newer claim owns (state-machines audit SM-04 B).
backend/webhook_delivery.py claim/deliver/record over backend/outbox.py: each claim stamps a fresh token, recovery
clears it, and release and settlement require it. The automation half is test_automation_reclaimed_worker.py."""

import pytest

from backend import task_trigger_rules
from backend import webhook_delivery as D
from backend.store import H
from backend.tests.helpers.tasks_board import api  # noqa: F401
from backend.tests.helpers.webhooks import make as make_hook, net, post, sends  # noqa: F401


def two_claims(api):
    """A claims a send; it stalls past STALE, recovery hands the send back and B claims it. Returns (store, the send row
    as A's claim read it, B's claim and its row, the time B claimed at)."""
    hook = make_hook(api, events=["custom.*"])
    post(api, f"webhooks/{hook['id']}/send", {"type": "custom.acme"})
    store = api.app.state.store
    a = D.claim(store, H.now())
    with store.read() as c:
        row_a = dict(c.execute("SELECT * FROM webhook_sends WHERE id=?", (a.id,)).fetchone())
    future = H.shift(H.now(), seconds=D.STALE + 1)
    assert D.recover(store, future) == 1
    b = D.claim(store, future)
    assert b.id == a.id and b.token != a.token
    with store.read() as c:
        row_b = dict(c.execute("SELECT * FROM webhook_sends WHERE id=?", (b.id,)).fetchone())
    return store, hook, row_a, b, row_b, future


@pytest.mark.parametrize("old_first", [True, False], ids=["old-503-then-new-200", "new-200-then-old-503"])
def test_an_old_webhook_failure_after_reclaim_cannot_reset_the_send_or_cause_a_resend(api, net, monkeypatch, old_first):
    store, hook, row_a, b, row_b, future = two_claims(api)
    results = []
    monkeypatch.setattr(task_trigger_rules, "webhook_result", lambda c, event, status: results.append(status))
    answers = [(row_a, 503), (row_b, 200)] if old_first else [(row_b, 200), (row_a, 503)]
    won = [D.record(store, row, 0, status, "", "", False, 1) for row, status in answers]
    assert won == ([False, True] if old_first else [True, False])
    send = sends(api, hook["id"])[0]
    assert (send["state"], send["attempts"], send["last_status"], send["claim_token"]) == ("delivered", 1, 200, None)
    with store.read() as c:
        logged = [r[0] for r in c.execute("SELECT status FROM webhook_attempts WHERE send_id=? ORDER BY n", (b.id,))]
        failing = c.execute("SELECT failing_since FROM webhooks WHERE id=?", (hook["id"],)).fetchone()[0]
    assert sorted(logged) == [200, 503]  # the late answer is history
    assert failing is None  # and changed neither the endpoint's health
    assert results == [200]  # nor a task rule
    # Nothing is due: the delivered send is not sent again.
    assert not D.step(store, api.app.state.vault, transport=net.transport, now=H.shift(future, days=2))
    assert net.seen == []


def test_a_webhook_worker_whose_claim_lapsed_posts_nothing_and_the_new_claim_delivers_with_the_same_id(api, net):
    store, hook, row_a, b, row_b, future = two_claims(api)
    vault = api.app.state.vault
    D.deliver(store, vault, D.Claimed(row_a["id"], row_a["claim_token"]), transport=net.transport)
    assert net.seen == [] and sends(api, hook["id"])[0]["state"] == "sending"
    D.deliver(store, vault, b, transport=net.transport)
    assert [r.headers["webhook-id"] for r in net.seen] == [b.id]  # the receiver's id never changes
    assert sends(api, hook["id"])[0]["state"] == "delivered"


def test_an_old_webhook_release_cannot_hand_back_the_new_claim(api):
    store, hook, row_a, b, row_b, future = two_claims(api)
    with store.transaction() as c:
        assert D.O.release(c, D.TABLE, b.id, row_a["claim_token"]) is False
    assert sends(api, hook["id"])[0]["state"] == "sending"
    with store.transaction() as c:
        assert D.O.release(c, D.TABLE, b.id, b.token) is True
    send = sends(api, hook["id"])[0]
    assert (send["state"], send["claim_token"]) == ("ready", None)
