"""Tico support's scheduled update of a hosted server (backend/hosted_updates.py): only a hosted install asks HQ,
nothing happens until support sets a target, the update waits for no bot turn to run (at most IDLE_WAIT_S), runs once
through the standard updater, and its outcome goes back to HQ without the update being tried again. HQ and the
updater are fakes."""
import io
import json
import time
import urllib.error

import httpx
import pytest

from backend import execution, hosted_updates as HU, releases
from backend.credentials import Vault
from backend.tests.test_api import api, headers  # noqa: F401

KEY = "tgw-synthetic-fixture-key-0042"
URL = "https://gateway.acme.example/v1/gateway"
TARGET = {"version": "0.4.11", "request_id": "hu_" + "a" * 16}
T0 = 1_800_000_000.0


class Answer(io.BytesIO):
    status = 200


class FakeHQ:
    """`POST <Gateway URL>/servers/update`: keeps each body and answers `target`, as a stuck HQ would (it never
    clears it), so the server alone keeps an update from repeating."""
    def __init__(self):
        self.bodies, self.target, self.status = [], None, 200

    def __call__(self, request, timeout):
        assert request.full_url == URL + "/servers/update" and request.get_header("Authorization") == "Bearer " + KEY
        self.bodies.append(json.loads(request.data))
        if self.status != 200:
            raise urllib.error.HTTPError(request.full_url, self.status, "refused", {}, io.BytesIO(b"{}"))
        return Answer(json.dumps({"target": self.target}).encode())


class FakeUpdater:
    def __init__(self):
        self.started, self.state, self.fail = [], {"state": "idle"}, False

    def __call__(self, request):
        if request.method == "POST":
            if self.fail:
                return httpx.Response(500, json={})
            self.started.append(json.loads(request.content)["version"])
            self.state = {"state": "pulling", "from": "0.4.9", "to": self.started[-1]}
        return httpx.Response(200, json=self.state)


@pytest.fixture
def hosted(api, monkeypatch):  # noqa: F811
    store = api.app.state.store
    hq, updater = FakeHQ(), FakeUpdater()
    monkeypatch.setattr(store.settings, "hosted", "own")
    monkeypatch.setattr(HU, "OPENER", hq)
    monkeypatch.setattr(HU, "tick_later", lambda store: None)      # the scheduler's own tick stays out of the way
    monkeypatch.setattr(releases, "TRANSPORT", httpx.MockTransport(updater))
    monkeypatch.setenv("TICO_GATEWAY_KEY", KEY)
    monkeypatch.setenv("TICO_GATEWAY_URL", URL)
    monkeypatch.setenv("TICO_UPDATER_URL", "http://updater:9000")
    monkeypatch.setenv("TICO_UPDATER_TOKEN", "s3cret")
    monkeypatch.setenv("TICO_UPDATE_CHECK", "off")
    monkeypatch.setenv("TICO_VERSION", "0.4.9")
    monkeypatch.setattr(execution, "CLAIMED_BUSY", {})
    vault = Vault(store)
    return type("Hosted", (), {"store": store, "hq": hq, "updater": updater,
                               "tick": lambda self, at: HU.tick(store, vault, at)})()


def events(store, action):
    with store.read() as c:
        return [(r["actor"], json.loads(r["detail"] or "{}"))
                for r in c.execute("SELECT * FROM events WHERE action=? ORDER BY rowid", (action,))]


def test_only_a_hosted_server_asks_and_nothing_happens_until_support_sets_a_target(api, hosted, monkeypatch):  # noqa: F811
    monkeypatch.setattr(hosted.store.settings, "hosted", "")
    assert hosted.tick(T0) == "" and hosted.hq.bodies == []                       # a self-run server never asks
    monkeypatch.setattr(hosted.store.settings, "hosted", "own")
    hosted.tick(T0)
    assert hosted.hq.bodies == [{"version": "0.4.9"}]
    hosted.tick(T0 + 60)
    assert len(hosted.hq.bodies) == 1                                              # HQ is asked every 15 minutes
    hosted.tick(T0 + HU.CHECK_S)
    assert len(hosted.hq.bodies) == 2 and hosted.updater.started == []
    assert api.get("/api/v2/config", headers=headers()).json()["update"]["scheduled"] == ""
    hosted.hq.status = 404                                                         # HQ: not a hosted server
    hosted.tick(T0 + 2 * HU.CHECK_S)
    assert hosted.updater.started == []


def test_a_server_tico_updates_from_outside_never_asks_hq(hosted, monkeypatch):
    """TICO_UPDATES=tico: Tico updates it from outside, and it has no updater for a target to go through."""
    monkeypatch.setenv("TICO_UPDATES", "tico")
    hosted.hq.target = TARGET
    assert hosted.tick(T0) == "" and hosted.hq.bodies == [] and hosted.updater.started == []


def test_a_cell_company_takes_its_target_through_its_own_updater(hosted, monkeypatch):
    """A cell company (TICO_CELL=1) has the cell updater at TICO_UPDATER_URL, so support's target reaches it too."""
    monkeypatch.setenv("TICO_CELL", "1")
    monkeypatch.setenv("TICO_UPDATER_URL", "http://10.208.0.1:8090")
    hosted.hq.target = TARGET
    hosted.tick(T0)
    assert hosted.hq.bodies and hosted.updater.started


def test_a_target_waits_for_no_bot_turn_at_most_the_idle_wait_then_updates_once(api, hosted, monkeypatch):  # noqa: F811
    hosted.hq.target = TARGET
    execution.CLAIMED_BUSY["mac"] = (frozenset({"coo"}), frozenset(), time.monotonic())   # a bot turn runs
    assert hosted.tick(T0) == "waiting" and hosted.updater.started == []
    assert api.get("/api/v2/config", headers=headers()).json()["update"]["scheduled"] == "0.4.11"
    health = api.get("/api/v2/health", headers=headers()).json()
    version = next(c for c in health["checks"] if c["id"] == "version")
    assert version["summary"] == "Tico will update this server to v0.4.11 when idle."
    assert hosted.tick(T0 + 60) == "waiting" and hosted.updater.started == []
    execution.CLAIMED_BUSY["mac"] = (frozenset({"coo"}), frozenset(), time.monotonic())
    assert hosted.tick(T0 + HU.IDLE_WAIT_S) == "updating"                         # busy all along: the wait ends
    assert hosted.updater.started == ["0.4.11"]
    assert events(hosted.store, "system.update.started") == [("system:hosting", {"from": "0.4.9", "to": "0.4.11"})]

    # The updater's restart: the new release finishes the record and tells HQ at the next minute's check-in.
    monkeypatch.setenv("TICO_VERSION", "0.4.11")
    hosted.updater.state = {"state": "healthy", "from": "0.4.9", "to": "v0.4.11"}
    assert hosted.tick(T0 + HU.IDLE_WAIT_S + 60) == "done"
    hosted.tick(T0 + HU.IDLE_WAIT_S + 120)
    assert hosted.hq.bodies[-1] == {"version": "0.4.11", "done": {
        "request_id": TARGET["request_id"], "outcome": "updated", "message": ""}}
    assert [d["outcome"] for _, d in events(hosted.store, "system.update.finished")] == ["updated"]
    # Once on the target, the same target (or a new request for it) never updates again.
    hosted.hq.target = {**TARGET, "request_id": "hu_" + "b" * 16}
    for at in range(1, 4):
        hosted.tick(T0 + HU.IDLE_WAIT_S + at * HU.CHECK_S)
    assert hosted.updater.started == ["0.4.11"]
    assert hosted.hq.bodies[-1]["done"] == {"request_id": "hu_" + "b" * 16, "outcome": "updated", "message": ""}
    assert api.get("/api/v2/config", headers=headers()).json()["update"]["scheduled"] == ""


def test_a_failed_or_rolled_back_update_is_reported_and_never_tried_again(api, hosted):  # noqa: F811
    hosted.hq.target = TARGET
    assert hosted.tick(T0) == "updating" and hosted.updater.started == ["0.4.11"]   # idle: at once
    hosted.updater.state = {"state": "rolled_back", "from": "0.4.9", "to": "v0.4.11", "message": "Health check failed"}
    assert hosted.tick(T0 + 60) == "done"
    hosted.tick(T0 + 120)
    assert hosted.hq.bodies[-1]["done"] == {"request_id": TARGET["request_id"], "outcome": "rolled_back",
                                            "message": "Health check failed"}
    told = len(hosted.hq.bodies)
    for at in range(1, 5):                     # HQ still answering the same target: told once, never started again
        hosted.tick(T0 + 120 + at * HU.CHECK_S)
    assert hosted.updater.started == ["0.4.11"] and len(hosted.hq.bodies) == told + 4
    assert all("done" not in body for body in hosted.hq.bodies[told:])
    assert [d["outcome"] for _, d in events(hosted.store, "system.update.finished")] == ["rolled_back"]

    # Support sets it again (a new request): an updater that refuses the start is a failure, told once.
    hosted.hq.target = {**TARGET, "request_id": "hu_" + "c" * 16}
    hosted.updater.fail = True
    assert hosted.tick(T0 + 10 * HU.CHECK_S) == "done"
    hosted.tick(T0 + 10 * HU.CHECK_S + 60)
    assert hosted.hq.bodies[-1]["done"]["outcome"] == "failed"
    hosted.tick(T0 + 11 * HU.CHECK_S)
    assert hosted.updater.started == ["0.4.11"]
    assert [d["outcome"] for _, d in events(hosted.store, "system.update.finished")] == ["rolled_back", "failed"]


def test_a_target_taken_back_before_it_starts_is_dropped_and_hq_answers_are_checked(api, hosted):  # noqa: F811
    hosted.hq.target = {"version": "latest", "request_id": TARGET["request_id"]}   # not a release: ignored
    hosted.tick(T0)
    execution.CLAIMED_BUSY["mac"] = (frozenset({"coo"}), frozenset(), time.monotonic())
    hosted.hq.target = TARGET
    assert hosted.tick(T0 + HU.CHECK_S) == "waiting"
    hosted.hq.target = None
    assert hosted.tick(T0 + 2 * HU.CHECK_S) == "done"
    assert hosted.tick(T0 + HU.IDLE_WAIT_S + HU.CHECK_S) == "done" and hosted.updater.started == []
    assert len(events(hosted.store, "system.update.unscheduled")) == 1
