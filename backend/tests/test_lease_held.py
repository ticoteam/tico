"""A slow server must not expire a turn whose runner is still renewing it (Dev 612).

On v0.4.14 the write lock was held for up to a minute: renewals reached the server but waited longer than the
90-second lease, and the expiry sweep ended 49 live turns "Computer lost or heartbeat stopped"."""

from backend.store import H, Problem
from backend.tests.test_api import api, claim, expire, headers, post, setup_attempt  # noqa: F401


def lock_timeout(*args, **kwargs):
    raise Problem("unavailable", "The database is busy", 503, retryable=True)


def state(api, aid):
    with api.app.state.store.read() as c:
        return c.execute("SELECT state,lease_until FROM attempts WHERE id=?", (aid,)).fetchone()


def test_a_renewal_that_reached_a_busy_server_keeps_the_turn(api, monkeypatch):
    r, _, a = setup_attempt(api)
    post(api, f"attempts/{a['id']}/started", {"thread_id": "slow-server"}, r["token"])
    execution = api.app.state.execution
    # The renewal arrives, then times out waiting for the write lock: nothing it would have written lands.
    with monkeypatch.context() as m:
        m.setattr(execution, "renew", lock_timeout)
        assert api.post(f"/api/v2/attempts/{a['id']}/renew", json={},
                        headers=headers(r["token"])).status_code == 503
    expire(api, a["id"])
    assert claim(api, r) is None
    row = state(api, a["id"])
    assert row["state"] == "running" and row["lease_until"] > H.now()
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM events WHERE action='attempt.lease-held' AND target=?",
                         (a["id"],)).fetchone()[0] == 1
        assert c.execute("SELECT state FROM jobs WHERE id=?", (a["job_id"],)).fetchone()[0] == "running"


def test_an_event_batch_that_reached_a_busy_server_keeps_the_turn(api, monkeypatch):
    r, _, a = setup_attempt(api)
    post(api, f"attempts/{a['id']}/started", {"thread_id": "slow-events"}, r["token"])
    with monkeypatch.context() as m:
        m.setattr(api.app.state.execution, "events", lock_timeout)
        assert api.post(f"/api/v2/attempts/{a['id']}/events", json={"events": [{"seq": 1, "kind": "status", "payload": {"note": "still working"}}]},
                        headers=headers(r["token"])).status_code == 503
    expire(api, a["id"])
    assert claim(api, r) is None
    assert state(api, a["id"])["state"] == "running"


def test_a_renewal_that_waited_past_the_lease_is_still_accepted(api, monkeypatch):
    """The renewal reached the server while the lease was live and got the lock only after it lapsed. Judged by
    when it arrived, it renews; judged by when it ran, it was refused and the runner stopped a live turn."""
    import time
    r, _, a = setup_attempt(api)
    post(api, f"attempts/{a['id']}/started", {"thread_id": "late-lock"}, r["token"])
    execution, store = api.app.state.execution, api.app.state.store
    original = execution.renew

    def after_the_lock_wait(c, who, aid, **kwargs):
        if not kwargs.get("if_fresh"):
            c.execute("UPDATE attempts SET lease_until=? WHERE id=?", (H.now(), aid))
            time.sleep(0.01)
        return original(c, who, aid, **kwargs)

    monkeypatch.setattr(execution, "renew", after_the_lock_wait)
    with store.transaction() as c:          # stale enough that the read-only fast path does not answer it
        c.execute("UPDATE attempts SET lease_until=? WHERE id=?", (H.shift(H.now(), seconds=10), a["id"]))
    renewed = post(api, f"attempts/{a['id']}/renew", {}, r["token"])
    assert renewed["lease_until"] > H.now() and "stop" not in renewed
    assert state(api, a["id"])["state"] == "running"
    assert a["id"] not in execution.calls                # it landed


def test_a_landed_renewal_does_not_hold_a_later_lapse(api):
    r, _, a = setup_attempt(api)
    post(api, f"attempts/{a['id']}/started", {"thread_id": "landed"}, r["token"])
    post(api, f"attempts/{a['id']}/renew", {}, r["token"])
    assert a["id"] not in api.app.state.execution.calls
    expire(api, a["id"])
    assert claim(api, r) is None
    assert state(api, a["id"])["state"] == "expired"


def test_a_runner_that_stopped_calling_still_loses_the_lease(api, monkeypatch):
    r, _, a = setup_attempt(api)
    post(api, f"attempts/{a['id']}/started", {"thread_id": "gone"}, r["token"])
    execution = api.app.state.execution
    with monkeypatch.context() as m:
        m.setattr(execution, "renew", lock_timeout)
        assert api.post(f"/api/v2/attempts/{a['id']}/renew", json={},
                        headers=headers(r["token"])).status_code == 503
    # Its last call is more than a lease old: the runner is gone, and the turn expires as before.
    old = H.shift(H.now(), seconds=-api.app.state.store.settings.lease_seconds - 1)
    execution.calls[a["id"]] = {call: (runner_id, old) for call, (runner_id, _) in execution.calls[a["id"]].items()}
    expire(api, a["id"])
    assert claim(api, r) is None
    assert state(api, a["id"])["state"] == "expired"


def test_only_the_attempts_own_runner_keeps_it(api):
    r, _, a = setup_attempt(api)
    post(api, f"attempts/{a['id']}/started", {"thread_id": "other-runner"}, r["token"])
    api.app.state.execution.calls[a["id"]] = {object(): ("another-runner", H.now())}
    expire(api, a["id"])
    assert claim(api, r) is None
    assert state(api, a["id"])["state"] == "expired"
