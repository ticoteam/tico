"""Computer status follows live contact; retirement never removes a computer still in use."""

import json
from unittest.mock import Mock

import pytest

from backend import cloud_machines as CM, computers, runner_states
from backend.getting_started import ONLINE_SECONDS
from backend.scheduler import Scheduler
from backend.store import H
from backend.tests.test_api import api, assign, get, post, ready, runner  # noqa: F401
from backend.tests.test_cloud_machines import cloud, employee, enrolled, shared_enrolled  # noqa: F401


@pytest.mark.parametrize("shared", [False, True])
def test_live_contact_overrides_stale_cloud_state_in_ui_payloads(cloud, shared):  # noqa: F811
    if shared:
        shared_enrolled(cloud, "ops")
        computer = post(cloud.api, "runners/enroll", {"code": cloud.hq.calls[-1][3], "label": "boat-vm", "platform": "linux"})
    else:
        computer = enrolled(cloud)
    rid = computer["runner_id"]
    key = CM.SHARED if shared else "ops"
    assert cloud.row(key)["state"] == "running", "enrollment finishes the boot transition"
    ready(cloud.api, computer, ["ops"])
    with cloud.store.transaction() as c:
        # An old record, or a stale HQ listing, must not overrule a runner doing real work.
        CM.save(c, key, state="starting")
    shown = employee(cloud.api)["cloud_machine"]
    machine = next(m for m in get(cloud.api, "operations")["computers"] if m["id"] == rid)
    assert shown["state"] == machine["cloud_machine"]["state"] == "running"
    assert next(m for m in get(cloud.api, "computers")["computers"] if m["id"] == rid)["online"]
    if shared:
        assert get(cloud.api, "cloud-machines")["shared"]["state"] == "running"
    assert cloud.row(key)["state"] == "starting", "reading a payload does not change the record"
    ready(cloud.api, computer, ["ops"])
    assert cloud.row(key)["state"] == "running", "the next heartbeat repairs existing records"
    with cloud.store.transaction() as c:
        CM.save(c, key, state="starting")
        runner_states.save(c, rid, last_seen_at=H.shift(H.now(), seconds=-ONLINE_SECONDS - 1))
    assert employee(cloud.api)["cloud_machine"]["state"] == "starting", "an offline runner cannot prove a boot"
    with cloud.store.transaction() as c:
        CM.save(c, key, state="stopping")
    ready(cloud.api, computer, ["ops"])
    assert cloud.row(key)["state"] == "stopping", "a late heartbeat cannot cancel the scheduler's stop fence"
    assert employee(cloud.api)["cloud_machine"]["state"] == "running", "live contact still wins on screen"
    post(cloud.api, f"computers/{rid}/revoke", {})
    assert employee(cloud.api)["cloud_machine"]["state"] == "stopping", "revoked contact cannot prove the machine is live"


def test_hourly_sweep_retires_an_unused_offline_computer_and_it_can_enroll_again(api, monkeypatch):  # noqa: F811
    old = runner(api, label="Old box")
    current = runner(api, label="Current box")
    ready(api, current, [])
    rid = old["runner_id"]
    store = api.app.state.store
    now = H.now()
    with store.transaction() as c:
        runner_states.save(c, rid, last_seen_at=H.shift(now, seconds=-computers.UNASSIGNED_OFFLINE_RETIRE_SECONDS - 1))
        c.execute("INSERT INTO registry_metadata VALUES(?,?)", ("computer-repositories:" + rid, '{}'))
    sessions = Mock()
    monkeypatch.setattr(store, "screen_sessions", sessions)
    result = Scheduler(store, api.app.state.execution).tick(H.parse_ts(now))
    assert not result["failures"]
    assert rid not in {r["id"] for r in get(api, "computers")["computers"]}
    health = get(api, "health")
    assert {r["id"] for r in health["computers"]} == {current["runner_id"]}
    assert next(r for r in get(api, "operations")["computers"] if r["id"] == rid)["revoked_at"]
    post(api, "runners/heartbeat", {"version": "test", "platform": "test"}, token=old["token"], expected=401)
    with store.read() as c:
        assert not c.execute("SELECT 1 FROM registry_metadata WHERE key IN (?,?)",
                             ("computer-repositories:" + rid, "computer-enrolled-as:" + rid)).fetchone()
        events = list(c.execute("SELECT actor,detail FROM events WHERE action='runner.revoked' AND target=?", (rid,)))
        assert len(events) == 1 and events[0]["actor"] == H.KEEPER
        assert json.loads(events[0]["detail"])["reason"] == "No bots assigned and offline for over 24 hours"
    sessions.set_mode.assert_called_once_with(rid, "off", "computer removed")
    sessions.exit_close.assert_called_once_with(exit=rid, reason="computer removed")
    assert computers.sweep(store, now) == [], "retirement is idempotent"
    replacement = runner(api, label="Old box")
    ready(api, replacement, [])
    assert replacement["runner_id"] != rid
    assert replacement["runner_id"] in {r["id"] for r in get(api, "computers")["computers"]}


def test_retirement_preserves_assigned_cloud_recent_and_never_seen_computers(api):  # noqa: F811
    store = api.app.state.store
    now = H.now()
    cutoff = H.shift(now, seconds=-computers.UNASSIGNED_OFFLINE_RETIRE_SECONDS)
    boxes = {name: runner(api, label=name) for name in ("assigned", "cloud", "recent", "boundary", "new", "never-seen")}
    assign(api, boxes["assigned"], "ops")
    with store.transaction() as c:
        # Even an archived bot's assignment protects its computer.
        c.execute("UPDATE bots SET state='archived' WHERE id='ops'")
        for name, computer in boxes.items():
            rid = computer["runner_id"]
            c.execute("UPDATE runners SET created_at=? WHERE id=?", (H.shift(cutoff, hours=-1), rid))
            seen = H.shift(cutoff, seconds=-1)
            if name == "recent":
                seen = H.shift(cutoff, seconds=1)
            elif name == "boundary":
                seen = cutoff
            elif name in ("new", "never-seen"):
                seen = None
            runner_states.save(c, rid, last_seen_at=seen)
        c.execute("UPDATE runners SET created_at=? WHERE id=?", (now, boxes["new"]["runner_id"]))
        c.execute("INSERT INTO cloud_machines(bot,runner_id,state,updated_at) VALUES('cpo',?,'stopped',?)",
                  (boxes["cloud"]["runner_id"], now))
    assert computers.sweep(store, now) == [boxes["never-seen"]["runner_id"]]
    with store.read() as c:
        kept = {r["id"] for r in c.execute("SELECT id FROM runners WHERE revoked_at IS NULL")}
    assert kept == {computer["runner_id"] for name, computer in boxes.items() if name != "never-seen"}
    assert computers.sweep(store, H.shift(now, seconds=1)) == [boxes["boundary"]["runner_id"]]
    # A fresh heartbeat wins before the sweep, even when the old contact was long expired.
    ready(api, boxes["recent"], [])
    assert computers.sweep(store, H.shift(now, seconds=2)) == []
