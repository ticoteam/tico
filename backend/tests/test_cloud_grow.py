"""Grow storage automatically (backend/cloud_machines.py): a running cloud machine whose disk is nearly full moves to the
next size with more disk, on by default, never past the largest size or the company's CPU cap; a size chosen while it
runs (grown or a person's) drains it, so its runner takes no new turn, and once no turn is out it stops and starts
again at that size. A turn is never cut. Live case: the shared machine ran out of disk (0.2 GB free), dropped, and 29
running turns expired. HQ is a fake client (backend/tests/test_cloud_machines.py) offering HQ's sizes with disk."""

import pytest

from backend import cloud_machines as CM, performance
from backend.store import H
from backend.tests import test_cloud_machines as TCM
from backend.tests.test_api import api, headers, post, ready  # noqa: F401
from backend.tests.test_cloud_machines import claim_on, cloud, enrolled, events, facts, machine_row, work  # noqa: F401

GB = 1024 ** 3
OFFERED = [{"name": name, "cpu": cpu, "memory_gb": cpu * 2, "disk_gb": disk, "usd_per_hour": 0.05 * cpu,
            "start_usd": 0.04, "idle_stop_s": 300}
           for name, cpu, disk in (("small", 2, 51), ("default", 4, 51), ("large", 8, 111), ("xlarge", 16, 251))]


def disk(free_gb, total_gb=51):
    return {"free_bytes": int(free_gb * GB), "total_bytes": int(total_gb * GB)}


# ----------------------------------------------------------------------------- the plan, pure
def test_a_nearly_full_disk_plans_the_next_size_with_more_disk_up_to_the_largest_and_the_cpu_cap():
    assert not CM.low_disk(disk(20)) and not CM.low_disk(None) and not CM.low_disk({"free_bytes": 0, "total_bytes": 0})
    assert CM.low_disk(disk(9)) and CM.low_disk(disk(0.2))
    assert CM.low_disk(disk(15, 111)) and not CM.low_disk(disk(40, 111))    # under 15% even with over 10 GB free
    running = machine_row(state="running")
    target, why, full = CM.grow_plan(running, disk(0.2), OFFERED, {}, 0)
    assert (target, why, full) == ("large", None, 100)
    # small has no more disk than default: it goes straight to the next size that has.
    assert CM.grow_plan(machine_row(state="running", size="small", machine_size="small"), disk(1), OFFERED, {}, 0)[0] \
        == "large"
    assert CM.grow_plan(machine_row(state="running", size="large", machine_size="large"), disk(5, 111), OFFERED, {},
                        0)[0] == "xlarge"
    # Room on the disk, or a size already waiting for the restart: nothing to plan.
    assert CM.grow_plan(running, disk(30), OFFERED, {}, 0) == (None, None, None)
    assert CM.grow_plan(machine_row(state="running", size="large"), disk(0.2), OFFERED, {}, 0) == (None, None, None)
    # The largest, past the CPU cap, or a size HQ already refused: no size, a reason.
    top = CM.grow_plan(machine_row(state="running", size="xlarge", machine_size="xlarge"), disk(2, 251), OFFERED, {}, 0)
    assert top[0] is None and top[1]
    capped = CM.grow_plan(running, disk(0.2), OFFERED, {"max_cpu": 16}, 12)
    assert capped[0] is None and capped[1]
    assert CM.grow_plan(running, disk(0.2), OFFERED, {"max_cpu": 16}, 8)[0] == "large"
    refused = CM.grow_plan(machine_row(state="running", grow_refused_size="large"), disk(0.2), OFFERED, {}, 0)
    assert refused[0] is None and refused[1]
    # HQ's sizes without disk: nothing to grow to.
    assert CM.bigger([{k: v for k, v in s.items() if k != "disk_gb"} for s in OFFERED], "default") is None


def test_grow_storage_is_on_unless_the_bot_or_the_company_turned_it_off():
    assert CM.grows(machine_row()) and CM.grows(machine_row(grow_storage=None), {"grow_storage": None})
    assert not CM.grows(machine_row(grow_storage=0))
    assert not CM.grows(machine_row(grow_storage=None), {"grow_storage": 0})        # the company's
    assert CM.grows(machine_row(grow_storage=1), {"grow_storage": 0})               # the bot's own choice wins


def test_a_running_machine_with_another_size_stops_only_once_no_turn_is_out_always_on_too():
    pending = machine_row(state="running", size="large", heartbeat_at=990, active_at=999)
    assert CM.resizing(pending) and not CM.resizing(machine_row(state="running"))
    assert not CM.resizing(machine_row(size="large"))                               # stopped: the next start sends it
    assert CM.decide(pending, facts(), 1000, 300) == ("stop", None)                 # not idle yet, but drained
    assert CM.decide({**pending, "always_on": 1}, facts(), 1000, 300) == ("stop", None)
    assert CM.decide(pending, facts(turns=True), 1000, 300) == (None, None)
    assert CM.decide(pending, facts(turns=True), 1100, 300) == ("heartbeat", None)  # kept on, never cut
    assert CM.decide(pending, facts(updating=True), 1000, 300) == (None, None)
    assert CM.decide({**machine_row(state="running", always_on=1, heartbeat_at=990)}, facts(), 1000, 300) \
        == (None, None)


# ----------------------------------------------------------------------------- the manager, with HQ's sizes
@pytest.fixture
def grow(cloud, monkeypatch):  # noqa: F811
    """`cloud` with HQ offering its real sizes (disk included) and caps; `grow.full(free_gb)` posts the runner's disk."""
    caps = {"machines": 10}
    monkeypatch.setattr(TCM, "SIZES", OFFERED)
    listed = cloud.hq.list
    monkeypatch.setattr(cloud.hq, "list", lambda: {**listed(), "caps": caps})
    cloud.caps = caps
    cloud.manager.synced = None
    cloud.tick()

    def full(free_gb, total_gb=51, rid=None):
        rid = rid or cloud.row()["runner_id"]
        at = int(cloud.at[0])
        with cloud.store.transaction() as c:
            c.execute("INSERT OR REPLACE INTO perf_points(runner_id,span,created_at,n,cpu,cpu_max,disk_free,disk_total) "
                      "VALUES(?,?,?,1,10,10,?,?)", (rid, performance.MINUTE, at, int(free_gb * GB), int(total_gb * GB)))
    cloud.full = full
    return cloud


def test_a_full_disk_grows_the_machine_once_its_turn_finishes_and_the_turn_is_never_cut(grow):
    computer = enrolled(grow)
    ready(grow.api, computer, ["ops"])
    attempt = claim_on(grow, computer, "ops")["attempt"]
    assert attempt and grow.row()["state"] == "running"
    grow.full(0.2)
    grow.hq.calls.clear()
    grow.tick(1)
    assert grow.row()["size"] == "large" and grow.row()["machine_size"] == "default"
    assert [e["action"] for e in events(grow, "cloud_machine.grew")] == ["cloud_machine.grew"]
    assert "stop" not in grow.hq.actions()
    # Draining: new work waits, and the turn out keeps it on however long it takes.
    work(grow, text="Another thing")
    held = claim_on(grow, computer, "ops")
    assert held["attempt"] is None and held["paused"] == CM.RESIZE_HELD
    for _ in range(5):
        grow.tick(120)
    assert "stop" not in grow.hq.actions()
    assert grow.row()["drain_started_at"]
    with grow.store.transaction() as c:
        c.execute("UPDATE attempts SET state='completed',finished_at=? WHERE id=?", (H.now(), attempt["id"]))
    grow.hq.calls.clear()
    grow.tick(1)
    assert grow.hq.actions() == ["stop"] and grow.row()["state"] == "stopped"
    assert grow.row()["drain_started_at"] is None
    grow.tick(1)                                                # the queued work starts it again, at the new size
    assert grow.hq.calls[-1] == ("start", "m1", "large")
    after = grow.row()
    assert after["machine_size"] == "large" and after["size"] == "large" and after["grow_note"].startswith("Grew to")
    # Started at the new size: the old disk reading no longer moves it.
    grow.hq.calls.clear()
    grow.tick(1)
    assert "stop" not in grow.hq.actions() and grow.row()["size"] == "large"


def test_grow_storage_off_keeps_the_size(grow):
    computer = enrolled(grow)
    ready(grow.api, computer, ["ops"])
    post(grow.api, "bots/ops/cloud-machine", {"grow_storage": False})
    grow.full(0.2)
    grow.tick(1)
    assert grow.row()["size"] == "default" and not events(grow, "cloud_machine.grew")
    assert claim_on(grow, computer, "ops").get("paused") is None
    # The company turning it off holds for every own machine that has not chosen.
    post(grow.api, "bots/ops/cloud-machine", {"grow_storage": True})
    post(grow.api, "cloud-machines/shared", {"grow_storage": False})
    assert grow.row()["grow_storage"] == 1
    grow.tick(1)
    assert grow.row()["size"] == "large"


def test_off_by_the_company_an_own_machine_that_never_chose_stays_at_its_size(grow):
    computer = enrolled(grow)
    ready(grow.api, computer, ["ops"])
    post(grow.api, "cloud-machines/shared", {"grow_storage": False})
    grow.full(0.2)
    grow.tick(1)
    assert grow.row()["size"] == "default"


def test_at_the_largest_size_it_is_a_problem_on_health_not_a_resize(grow):
    computer = enrolled(grow)
    ready(grow.api, computer, ["ops"])
    with grow.store.transaction() as c:
        CM.save(c, "ops", size="xlarge", machine_size="xlarge")
    grow.hq.machines["m1"]["size"] = "xlarge"
    grow.full(2, 251)
    grow.hq.calls.clear()
    grow.tick(1)
    grow.tick(1)
    row = grow.row()
    assert row["size"] == "xlarge" and row["problem"] == "disk_full" and row["grow_note"]
    assert len(events(grow, "cloud_machine.grow_blocked")) == 1
    assert "stop" not in grow.hq.actions() and claim_on(grow, computer, "ops").get("paused") is None
    with grow.store.read() as c:
        found = CM.health(c, grow.api.app.state.settings)
        assert found and found["status"] == "warn" and row["grow_note"] in found["summary"]
    grow.full(100, 251)                                         # space freed: the problem clears
    grow.tick(1)
    assert grow.row()["problem"] == ""


def test_over_the_cpu_cap_it_does_not_grow(grow):
    computer = enrolled(grow)
    ready(grow.api, computer, ["ops"])
    grow.caps["max_cpu"] = 4
    grow.manager.synced = None
    grow.tick()
    grow.full(0.2)
    grow.tick(1)
    assert grow.row()["size"] == "default" and grow.row()["problem"] == "disk_full"


def test_a_size_hq_refuses_at_the_restart_goes_back_and_is_not_grown_to_again(grow):
    computer = enrolled(grow)
    ready(grow.api, computer, ["ops"])
    with grow.store.transaction() as c:                        # the first chat is done: nothing out
        c.execute("UPDATE jobs SET state='canceled' WHERE bot_id='ops'")
    grow.full(0.2)
    grow.tick(1)
    assert grow.row()["size"] == "large"
    grow.tick(1)
    assert grow.row()["state"] == "stopped"
    grow.hq.fail["start"] = CM.Refused("cpu_cap", 409)
    work(grow, text="Another thing")
    grow.tick(1)
    row = grow.row()
    assert row["size"] == "default" and row["grow_refused_size"] == "large" and row["next_try_at"] is None
    grow.hq.calls.clear()
    grow.tick(1)                                               # starts again at its own size, not at the refused one
    assert grow.hq.calls[-1] == ("start", "m1", None)
    grow.full(0.2)
    grow.tick(1)
    assert grow.row()["size"] == "default" and grow.row()["problem"] == "disk_full"


def test_a_size_a_person_chooses_while_it_runs_waits_for_the_turn_then_restarts_at_it(grow):
    """The live shared machine said size large, ran at default and never idled: a running machine now applies it."""
    computer = enrolled(grow)
    ready(grow.api, computer, ["ops"])
    attempt = claim_on(grow, computer, "ops")["attempt"]
    post(grow.api, "bots/ops/cloud-machine", {"size": "large"})
    assert grow.row()["state"] == "running" and CM.resizing(grow.row())
    grow.hq.calls.clear()
    grow.tick(60)
    assert "stop" not in grow.hq.actions()
    with grow.store.transaction() as c:
        c.execute("UPDATE attempts SET state='completed',finished_at=? WHERE id=?", (H.now(), attempt["id"]))
        c.execute("UPDATE jobs SET state='canceled' WHERE bot_id='ops'")
    grow.tick(1)
    assert grow.hq.actions()[-1] == "stop"
    work(grow, text="Another thing")
    grow.tick(1)
    assert grow.hq.calls[-1] == ("start", "m1", "large")


def test_the_settings_default_to_growing_and_the_routes_take_the_switch(grow):
    listed = grow.api.get("/api/v2/cloud-machines", headers=headers()).json()
    assert listed["shared"]["grow_storage"] is True
    assert [s["disk_gb"] for s in listed["sizes"]] == [51, 51, 111, 251]
    assert post(grow.api, "cloud-machines/shared", {"grow_storage": False})["shared"]["grow_storage"] is False
    assert grow.api.get("/api/v2/cloud-machines", headers=headers()).json()["shared"]["grow_storage"] is False
    TCM.adopt(grow)
    answer = post(grow.api, "bots/ops/cloud-machine", {"grow_storage": True})["cloud_machine"]
    assert answer["grow_storage"] is True


def test_the_disk_read_is_the_runners_newest_minute_and_only_a_fresh_one(grow):
    computer = enrolled(grow)
    rid = computer["runner_id"]
    grow.full(0.5)
    with grow.store.read() as c:
        assert performance.latest_disks(c, [rid], grow.at[0])[rid]["free_bytes"] == int(0.5 * GB)
        assert performance.latest_disks(c, [rid], grow.at[0] + performance.DISK_FRESH_S + 60) == {}
