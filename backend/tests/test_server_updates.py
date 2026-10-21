"""The server update fence, rollback recovery and the update-key boundary."""
import json

import pytest

from backend import execution, releases, server_updates as updates
from backend.tests.test_api import api, claim, headers, post, setup_attempt  # noqa: F401


@pytest.fixture
def updater(monkeypatch):
    execution.CLAIMED_BUSY.clear()
    monkeypatch.setenv("TICO_VERSION", "0.1.0")
    monkeypatch.setenv("TICO_UPDATER_URL", "http://updater")
    state = {"state": "idle", "to": ""}
    calls, latest = [], ["0.2.0"]
    monkeypatch.setattr(releases, "notice", lambda: {
        "available": releases.newer(latest[0], releases.version()), "latest": latest[0]})
    monkeypatch.setattr(releases, "status", lambda: {"configured": True, **state})

    def start(target):
        calls.append(target)
        state.update(state="pulling", to=target)
        return state.copy()

    monkeypatch.setattr(releases, "start", start)
    yield state, calls, latest
    execution.CLAIMED_BUSY.clear()


def test_hosted_update_preserves_turns_fences_claims_and_survives_the_switch(api, updater, monkeypatch):
    machine, _, attempt = setup_attempt(api)
    store = api.app.state.store
    store.settings.hosted = "invited"
    post(api, "chat/ops", {"text": "Do this after the first turn."})
    updates.tick(store)
    assert updater[1] == []
    assert claim(api, machine) is None
    with store.read() as c:
        assert updates.holding(c)
        assert c.execute("SELECT state FROM attempts WHERE id=?", (attempt["id"],)).fetchone()[0] == "leased"
        assert c.execute("SELECT count(*) FROM jobs WHERE state='queued'").fetchone()[0] == 1
    post(api, "attempts/" + attempt["id"] + "/started", {"thread_id": "existing-turn"}, token=machine["token"])
    updates.tick(store)
    assert updater[1] == []
    post(api, "attempts/" + attempt["id"] + "/complete",
         {"outcome": "completed", "text": "Done", "last_seq": 0}, token=machine["token"])
    post(api, "jobs/claim", {"busy_bots": ["ops"]}, token=machine["token"])
    updates.tick(store)
    assert updater[1] == []                         # the computer still reports a worker finishing
    post(api, "jobs/claim", {}, token=machine["token"])
    updates.tick(store)
    assert updater[1] == ["0.2.0"]
    assert claim(api, machine) is None
    # A matching new server still holds claims through the health/rollback
    # window, including a sidecar whose status was temporarily lost.
    with store.read() as c:
        assert updates.load(c)["target"] == "0.2.0"
    monkeypatch.setenv("TICO_VERSION", "0.2.0")
    updates.tick(store)
    assert claim(api, machine) is None
    updater[0].update(state="idle", to="")
    updates.tick(store)
    assert claim(api, machine) is None
    updater[0].update(state="healthy", to="0.2.0")
    updates.tick(store)
    assert claim(api, machine) is not None


def test_rollback_releases_claims_and_waits_for_a_different_release(api, updater):
    store = api.app.state.store
    store.settings.hosted = "invited"
    updates.tick(store)
    updater[0].update(state="rolled_back", to="0.2.0")
    updates.tick(store)
    for _ in range(2):
        updates.tick(store)
    assert updater[1] == ["0.2.0"]
    with store.read() as c:
        assert not updates.holding(c)
        assert updates.view(c, store.settings)["failed_version"] == "0.2.0"
    with store.transaction() as c:
        updates.request(c, store.settings, "0.2.0", "human:ana")
    updates.tick(store)
    updates.tick(store)
    assert updater[1] == ["0.2.0", "0.2.0"]       # one explicit retry, never an automatic loop
    updater[0].update(state="rolled_back", to="0.2.0")
    updates.tick(store)
    updater[2][0] = "0.3.0"
    updates.tick(store)
    assert updater[1] == ["0.2.0", "0.2.0", "0.3.0"]


def test_busy_wait_releases_after_60_minutes_and_requeues_six_hours_later(api, updater, monkeypatch):
    store = api.app.state.store
    store.settings.hosted = "invited"
    clock = [updates.H.now()]
    monkeypatch.setattr(updates.H, "now", lambda: clock[0])
    monkeypatch.setattr(updates, "busy", lambda c: True)
    queued = clock[0]
    updates.tick(store)
    clock[0] = updates.H.shift(queued, minutes=59)
    updates.tick(store)
    with store.read() as c:
        assert updates.holding(c) and updates.load(c)["queued_at"] == queued
    clock[0] = updates.H.shift(queued, minutes=60)
    updates.tick(store)
    with store.read() as c:
        policy = updates.load(c)
        assert not updates.holding(c) and policy["state"] == "idle"
        assert policy["failed_version"] == "" and "retry" in policy["error"]
        retry = policy["retry_at"]
        assert retry == updates.H.shift(clock[0], hours=6)
    clock[0] = updates.H.shift(retry, seconds=-1)
    with store.transaction() as c:
        updates.request(c, store.settings, "0.2.0", "human:ana")  # staff polling cannot restart the hold early
    updates.tick(store)
    with store.read() as c:
        assert not updates.holding(c)
    assert updater[1] == []
    clock[0] = retry
    monkeypatch.setattr(updates, "busy", lambda c: False)
    updates.tick(store)
    with store.read() as c:
        assert updates.load(c)["queued_at"] == retry
    assert updater[1] == ["0.2.0"]


def test_updater_health_deadline_is_45_minutes_despite_errors_and_lost_status(api, updater, monkeypatch):
    store = api.app.state.store
    store.settings.hosted = "invited"
    clock = [updates.H.now()]
    monkeypatch.setattr(updates.H, "now", lambda: clock[0])
    started = clock[0]

    def unanswered():
        raise updates.Problem("updater_unavailable", "No answer", 502)

    monkeypatch.setattr(releases, "status", unanswered)
    updates.tick(store)                              # starts the clock even before an update was sent
    with store.read() as c:
        assert not updates.load(c).get("attempted")
        assert updates.load(c)["started_at"] == started
    clock[0] = updates.H.shift(started, minutes=15)
    monkeypatch.setattr(releases, "status", lambda: updater[0].copy())
    updates.tick(store)                              # a later successful start cannot reset it
    assert updater[1] == ["0.2.0"]
    for minute, state in ((30, "pulling"), (35, "restarting"), (40, "uncertain"), (44, "idle")):
        clock[0] = updates.H.shift(started, minutes=minute)
        updater[0].update(state=state, to="0.2.0" if state != "idle" else "")
        if state == "idle":
            monkeypatch.setenv("TICO_VERSION", "0.2.0")  # image changed but status was lost
        updates.tick(store)
        with store.read() as c:
            assert updates.holding(c) and updates.load(c)["started_at"] == started
    clock[0] = updates.H.shift(started, minutes=45)
    updates.tick(store)
    with store.read() as c:
        assert not updates.holding(c) and updates.load(c)["failed_version"] == "0.2.0"
        events = [json.loads(row[0]) for row in c.execute(
            "SELECT detail_json FROM events WHERE action='system.update.finished'")]
        assert [e["outcome"] for e in events] == ["timed_out"]
        assert events[0]["from"] == "0.1.0" and events[0]["to"] == "0.2.0"
    monkeypatch.setenv("TICO_VERSION", "0.1.0")
    updates.tick(store)
    assert updater[1] == ["0.2.0"]                   # the same release cannot start another hold
    updater[2][0] = "0.3.0"
    monkeypatch.setattr(releases, "status", unanswered)
    updates.tick(store)                              # a later release is still followed
    clock[0] = updates.H.shift(clock[0], minutes=45)
    updates.tick(store)                              # no answer ever, and no update-pending marker
    with store.read() as c:
        assert not updates.holding(c) and updates.load(c)["failed_version"] == "0.3.0"
        assert c.execute("SELECT count(*) FROM events WHERE action='system.update.finished'").fetchone()[0] == 2


def test_only_owner_or_update_key_controls_policy_and_off_cancels_a_wait(api, updater):
    store = api.app.state.store
    with store.read() as c:
        assert not updates.view(c, store.settings)["enabled"]
    updates.tick(store)
    assert updater[1] == []                         # self-hosted stays manual
    key = post(api, "service-keys", {"label": "Hosting updates", "scope": "update"})["key"]
    tasks = post(api, "service-keys", {"label": "Task filing"})["key"]
    path = "/api/v2/system/update"
    for token in ("ben-test", tasks):
        assert api.put(path, json={"enabled": True}, headers=headers(token)).status_code == 403
    assert api.put(path, json={"enabled": True}, headers=headers(key)).json()["enabled"]
    assert api.put(path, json={"enabled": False}, headers=headers(key)).status_code == 200
    invalid = api.post(path, json={"version": "not-a-version", "when_idle": True, "automatic": True}, headers=headers(key))
    assert invalid.status_code == 422
    assert api.get(path, headers=headers(key)).json()["automatic"]["enabled"] is False
    queued = api.post(path, json={"version": "0.2.0", "when_idle": True, "automatic": True}, headers=headers(key))
    assert queued.status_code == 200 and queued.json()["state"] == "waiting" and queued.json()["enabled"] is True
    assert api.get("/api/v2/tasks", headers=headers(key)).status_code == 403
    assert api.put(path, json={"enabled": False}, headers=headers(key)).status_code == 200
    with store.read() as c:
        assert not updates.holding(c)
        store.settings.hosted = "invited"
        assert not updates.view(c, store.settings)["enabled"]  # saved off beats the hosted default
