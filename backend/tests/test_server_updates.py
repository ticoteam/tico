"""The server update fence, rollback recovery and the update-key boundary."""
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
