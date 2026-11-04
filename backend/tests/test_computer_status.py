"""Computer status follows live contact; retirement never removes a computer still in use."""

import json
from unittest.mock import Mock

import pytest

from backend import cloud_machines as CM, computers, runner_states
from backend import bot_settings as BS
from backend.getting_started import ONLINE_SECONDS
from backend.scheduler import Scheduler
from backend.store import H
from backend.tests.test_api import api, assign, get, post, ready, runner  # noqa: F401
from backend.tests.test_cloud_machines import cloud, employee, enrolled, shared_enrolled  # noqa: F401
from backend.tests.test_credentials import setup
from backend.tests.test_model_subscriptions import add, give


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
    assert employee(cloud.api)["cloud_machine"]["state"] == "stopping", "live contact cannot cancel a stop on screen"
    post(cloud.api, f"computers/{rid}/revoke", {})
    assert employee(cloud.api)["cloud_machine"]["state"] == "stopping", "revoked contact cannot prove the machine is live"


def replaced_computer(api, label="Acme Mac"):
    old = runner(api, label=label)
    current = runner(api, label=label)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runners SET created_at=? WHERE id=?", (H.shift(H.now(), hours=-96), old["runner_id"]))
        runner_states.save(c, old["runner_id"], last_seen_at=H.shift(H.now(), hours=-72))
    return old, current


def test_hourly_sweep_retires_a_replaced_offline_computer_and_it_can_enroll_again(api, monkeypatch):  # noqa: F811
    old, current = replaced_computer(api)
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
        assert json.loads(events[0]["detail"])["reason"] == (
            f"No bots assigned and offline for over 24 hours; replaced by Acme Mac ({current['runner_id']})")
    sessions.set_mode.assert_called_once_with(rid, "off", "computer removed")
    sessions.exit_close.assert_called_once_with(exit=rid, reason="computer removed")
    assert computers.sweep(store, now) == [], "retirement is idempotent"
    replacement = runner(api, label="Acme Mac")
    ready(api, replacement, [])
    assert replacement["runner_id"] != rid
    assert replacement["runner_id"] in {r["id"] for r in get(api, "computers")["computers"]}


def test_retirement_preserves_assigned_cloud_recent_and_never_seen_computers(api):  # noqa: F811
    store = api.app.state.store
    now = H.now()
    cutoff = H.shift(now, seconds=-computers.UNASSIGNED_OFFLINE_RETIRE_SECONDS)
    boxes = {name: runner(api, label=name) for name in (
        "assigned", "cloud", "delivery", "recent", "boundary", "new", "never-seen")}
    replacements = {name: runner(api, label=name) for name in boxes}
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
        BS.write(c, "cpo", {"app_delivery": {"computer": boxes["delivery"]["runner_id"]}})
    assert computers.sweep(store, now) == [boxes["never-seen"]["runner_id"]]
    with store.read() as c:
        kept = {r["id"] for r in c.execute("SELECT id FROM runners WHERE revoked_at IS NULL")}
    assert kept == ({computer["runner_id"] for name, computer in boxes.items() if name != "never-seen"}
                    | {computer["runner_id"] for computer in replacements.values()})
    assert computers.sweep(store, H.shift(now, seconds=1)) == [boxes["boundary"]["runner_id"]]
    # A fresh heartbeat wins before the sweep, even when the old contact was long expired.
    ready(api, boxes["recent"], [])
    assert computers.sweep(store, H.shift(now, seconds=2)) == []


def test_an_unreplaced_computer_is_kept_after_days_offline_without_bots(api):  # noqa: F811
    old = runner(api, label="Weekend Mac")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runners SET created_at=? WHERE id=?", (H.shift(H.now(), hours=-96), old["runner_id"]))
        runner_states.save(c, old["runner_id"], last_seen_at=H.shift(H.now(), hours=-72))
    assert computers.sweep(api.app.state.store, H.now()) == []
    assert old["runner_id"] in {r["id"] for r in get(api, "computers")["computers"]}


def test_replacement_requires_same_owner_name_platform_newer_enrollment_and_later_contact(api):  # noqa: F811
    old, stale = replaced_computer(api)
    other_owner = runner(api, operator="ben", label="Acme Mac")
    other_name = runner(api, label="Other Mac")
    other_platform = runner(api, label="Acme Mac")
    revoked = runner(api, label="Acme Mac")
    earlier = runner(api, label="Acme Mac")
    ready(api, earlier, [])
    store = api.app.state.store
    with store.transaction() as c:
        c.execute("UPDATE runners SET platform='linux' WHERE id=?", (other_platform["runner_id"],))
        c.execute("UPDATE runners SET revoked_at=? WHERE id=?", (H.now(), revoked["runner_id"]))
        c.execute("UPDATE runners SET created_at=? WHERE id=?", (H.shift(H.now(), hours=-120), earlier["runner_id"]))
        c.execute("UPDATE runners SET created_at=? WHERE id=?", (H.shift(H.now(), hours=-80), stale["runner_id"]))
        runner_states.save(c, stale["runner_id"], last_seen_at=H.shift(H.now(), hours=-75))
    assert computers.sweep(store, H.now()) == []
    # A newer enrollment that predates the old computer's last contact becomes a replacement when it checks in again.
    ready(api, stale, [])
    assert computers.sweep(store, H.now()) == [old["runner_id"]]
    assert {other_owner["runner_id"], other_name["runner_id"]} <= {
        r["id"] for r in get(api, "computers")["computers"]}


def test_retirement_keeps_a_sharing_browser_exit_until_sharing_is_off(api):  # noqa: F811
    old, _ = replaced_computer(api)
    store = api.app.state.store
    with store.transaction() as c:
        c.execute("INSERT INTO browser_exits(runner_id,sharing) VALUES(?,1)", (old["runner_id"],))
    assert computers.sweep(store, H.now()) == []
    with store.transaction() as c:
        c.execute("UPDATE browser_exits SET sharing=0 WHERE runner_id=?", (old["runner_id"],))
    assert computers.sweep(store, H.now()) == [old["runner_id"]]


def test_retirement_keeps_a_computer_holding_a_company_subscription(api):  # noqa: F811
    setup(api)
    old, _ = replaced_computer(api)
    give(api, old, "claude", add(api)["id"])
    assert computers.sweep(api.app.state.store, H.now()) == []
    assert old["runner_id"] in {r["id"] for r in get(api, "computers")["computers"]}


@pytest.mark.parametrize("login", ["profile", "relay", "reported", "key"])
def test_retirement_keeps_stored_or_reported_logins(api, login):  # noqa: F811
    old, _ = replaced_computer(api)
    store, rid = api.app.state.store, old["runner_id"]
    with store.transaction() as c:
        if login == "profile":
            c.execute("INSERT INTO computer_profiles(runner_id,profile,runtimes) VALUES(?,'work',?)",
                      (rid, json.dumps({"claude": {"signed_in": True}})))
        elif login == "relay":
            c.execute("INSERT INTO model_logins(id,runner_id,runtime,state,created_at,updated_at,expires_at,requested_by) "
                      "VALUES('login',?,'claude','signed_in',?,?,?,'human:ana')", (rid, H.now(), H.now(), H.now()))
        elif login == "key":
            c.execute("INSERT INTO credentials(id,name,kind,created_at,updated_at,updated_by) "
                      "VALUES('key','Computer key','api_key',?,?,'human:ana')", (H.now(), H.now()))
            c.execute("INSERT INTO computer_keys(credential_id,runner_id,runtime,state,created_at,created_by) "
                      "VALUES('key',?,'claude','active',?,'human:ana')", (rid, H.now()))
        else:
            runner_states.save(c, rid, readiness=json.dumps({"schema_version": 1, "runtimes": {
                "claude": {"installed": True, "authenticated": "ready"}}}))
    assert computers.sweep(store, H.now()) == []
