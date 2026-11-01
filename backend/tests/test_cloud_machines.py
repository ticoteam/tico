"""Cloud machines (backend/cloud_machines.py): on a Tico Cloud company a bot with no computer runs on a cloud machine,
the company's shared one or, for a developer or the video producer, its own. A machine is made on first work with a
one-time enrollment code and bound to the bots set to run there only; it is started for work, kept with a heartbeat,
stopped after its size's break-even idle time unless Always on (the shared one once every member is idle), never
started out of credit, and retried with backoff when HQ refuses. A size change applies at the next start. A stopped
machine whose computer is behind this server's release is started once to update, then stops as usual. Off cloud
nothing changes. HQ is a fake client and the clock is a fake one."""

import json
import time

import httpx
import pytest

from backend import cloud_access, cloud_machines as CM, placement
from backend.store import H
from backend.tests.test_api import api, assign, get, headers, post, ready, runner  # noqa: F401
from backend.tests.test_cloud_access import signals

SIZES = [{"name": name, "cpu": cpu, "memory_gb": cpu * 2, "usd_per_hour": 0.05 * cpu, "start_usd": 0.04,
          "idle_stop_s": idle} for name, cpu, idle in (("small", 1, 600), ("default", 2, 300), ("large", 4, 180),
                                                      ("xlarge", 8, 120))]


class FakeHQ:
    """The machines routes as Python calls. `fail[action]` is a Refused to raise (once each), or a list of them."""

    def __init__(self):
        self.calls, self.fail, self.machines, self.n = [], {}, {}, 0

    def _check(self, action):
        failing = self.fail.get(action)
        if isinstance(failing, list) and failing:
            raise failing.pop(0)
        if isinstance(failing, CM.Refused):
            raise failing

    def _machine(self, mid, **changes):
        self.machines[mid].update(changes)
        return dict(self.machines[mid])

    def list(self):
        self.calls.append(("list",))
        return {"machines": [dict(m) for m in self.machines.values()], "sizes": SIZES, "caps": {"machines": 10}}

    def create(self, key, size, enroll_token, hub_url):
        self.calls.append(("create", key, size, enroll_token, hub_url))
        self._check("create")
        for machine in self.machines.values():
            if machine["key"] == key and machine["state"] not in ("deleted", "failed"):
                return dict(machine)
        for machine in self.machines.values():                  # as HQ: the dead one is kept under a renamed key
            if machine["key"] == key:
                machine["key"] = f"{key}:superseded:{machine['id']}"
        self.n += 1
        mid = f"m{self.n}"
        self.machines[mid] = {"id": mid, "key": key, "size": size, "state": "starting", "started_at": H.now(),
                              "stopped_at": None, "cost_month_usd": 0.0, "starts_month": 1, "last_error": ""}
        return dict(self.machines[mid])

    def start(self, machine_id, size=None):
        self.calls.append(("start", machine_id, size))
        self._check("start")
        machine = self.machines[machine_id]
        return self._machine(machine_id, state="starting", size=size or machine["size"],
                             starts_month=machine["starts_month"] + 1)

    def stop(self, machine_id):
        self.calls.append(("stop", machine_id))
        self._check("stop")
        return self._machine(machine_id, state="stopped", stopped_at=H.now())

    def heartbeat(self, machine_id):
        self.calls.append(("heartbeat", machine_id))
        self._check("heartbeat")
        return 300

    def delete(self, machine_id):
        self.calls.append(("delete", machine_id))
        self._check("delete")
        self.machines[machine_id]["state"] = "deleted"

    def actions(self):
        return [call[0] for call in self.calls if call[0] != "list"]


@pytest.fixture
def cloud(api, monkeypatch):  # noqa: F811
    """A Tico Cloud company, open, with a fake HQ and a fake clock (`cloud.at[0]`, seconds)."""
    store = api.app.state.store
    cloud_access.apply(store, cloud_access.parse(signals("open")))
    fake = FakeHQ()
    manager = api.app.state.cloud_machines
    monkeypatch.setattr(manager, "client", fake)
    # The finished setup's bots go on cloud machines when HQ first lists sizes (test_a_setup_finished_before_...);
    # here each test places its own.
    monkeypatch.setattr(manager, "on_offered", None)
    at = [time.time()]
    monkeypatch.setattr(CM, "now", lambda: at[0])

    class Cloud:
        pass
    out = Cloud()
    out.api, out.store, out.hq, out.manager, out.at = api, store, fake, manager, at

    def tick(seconds=0):
        at[0] += seconds
        manager.tick()
        return fake.actions()
    out.tick = tick
    tick()                                                      # HQ lists its sizes: the option is offered
    with store.read() as c:
        assert CM.offered(c, store.settings)

    def row(bot="ops"):
        with store.read() as c:
            return CM.row(c, bot)
    out.row = row
    return out


def adopt(cloud, bot="ops", place="own"):
    """The bot on a cloud machine; `place` own (most tests here are of one bot's own machine) or shared."""
    with cloud.store.transaction() as c:
        assert placement.auto_place(c, cloud.api.app.state.execution, bot) is None
        CM.save(c, bot, placement=place)
        CM.ensure_shared(c)
    return cloud.row(bot)


def work(cloud, bot="ops", text="Please summarize the current work."):
    return post(cloud.api, "chat/" + bot, {"text": text})


def enrolled(cloud, bot="ops"):
    """Create the bot's own machine for its queued work and enroll its runner with the code HQ was handed."""
    adopt(cloud, bot)
    work(cloud, bot)
    assert cloud.tick()[-1] == "create"
    code = cloud.hq.calls[-1][3]
    return post(cloud.api, "runners/enroll", {"code": code, "label": "boat-vm", "platform": "linux"})


def employee(api, bot="ops"):  # noqa: F811
    return next(e for e in api.get("/api/employees", headers=headers()).json() if e["name"] == bot)


def events(cloud, action):
    with cloud.store.read() as c:
        return [dict(r) for r in c.execute("SELECT * FROM events WHERE action=?", (action,))]


# ----------------------------------------------------------------------------- the policy, pure
def facts(**kw):
    return {"active": True, "turns": False, "activity": 0, "want": None, "want_at": 0, "frozen": False,
            "retry_now": False, "update": None, "updating": False, **kw}


def machine_row(**kw):
    return {"bot": "ops", "mode": "cloud", "size": "default", "always_on": 0, "machine_id": "m1", "runner_id": "r1",
            "state": "stopped", "machine_size": "default", "active_at": None, "heartbeat_at": None, "next_try_at": None,
            "failures": 0, "problem": "", "problem_at": None, "wake_key": None, "woke_at": None, "enroll_by": 0,
            "enroll_fails": 0, **kw}


def test_decide_wakes_for_work_and_creates_on_first_use():
    assert CM.decide(machine_row(), facts(), 1000, 300) == (None, None)
    assert CM.decide(machine_row(), facts(want="job:a"), 1000, 300) == ("start", None)
    assert CM.decide(machine_row(machine_id=None, state="none"), facts(want="job:a"), 1000, 300) == ("create", None)
    assert CM.decide(machine_row(state="deleted"), facts(want="move:ops"), 1000, 300) == ("create", None)
    assert CM.decide(machine_row(always_on=1), facts(), 1000, 300) == ("start", None)
    # Not active, or moved to another computer: a machine that is on stops, one that is off stays off.
    assert CM.decide(machine_row(state="running"), facts(active=False, want="job:a"), 1000, 300) == ("stop", None)
    assert CM.decide(machine_row(), facts(active=False, want="job:a"), 1000, 300) == (None, None)
    # A drain lets the turn out finish on it first.
    assert CM.decide(machine_row(state="running"), facts(active=False, turns=True), 1000, 300) == ("heartbeat", None)


def test_decide_idle_stop_at_break_even_heartbeat_and_always_on():
    on = machine_row(state="running", active_at=1000, heartbeat_at=1000)
    assert CM.decide(on, facts(), 1059, 300) == (None, None)
    assert CM.decide(on, facts(), 1060, 300) == ("heartbeat", None)
    assert CM.decide(on, facts(), 1299, 300) == ("heartbeat", None)
    assert CM.decide(on, facts(), 1300, 300) == ("stop", None)            # exactly the size's break-even
    # Activity since the start moves the idle clock; a turn out never stops it; Always on never idles.
    assert CM.decide(on, facts(activity=1200), 1300, 300) == ("heartbeat", None)
    assert CM.decide(on, facts(activity=1200), 1500, 300) == ("stop", None)
    assert CM.decide(on, facts(turns=True), 9999, 300) == ("heartbeat", None)
    assert CM.decide({**on, "always_on": 1}, facts(), 9999, 300) == ("heartbeat", None)


def test_decide_frozen_backoff_and_the_stuck_guard():
    assert CM.decide(machine_row(), facts(want="job:a", frozen=True), 1000, 300) == (None, "out_of_credit")
    assert CM.decide(machine_row(state="running"), facts(frozen=True), 1000, 300) == ("stop", "out_of_credit")
    waiting = machine_row(next_try_at=1100, problem="out_of_credit")
    assert CM.decide(waiting, facts(want="job:a"), 1050, 300) == (None, None)
    assert CM.decide(waiting, facts(want="job:a", retry_now=True), 1050, 300) == ("start", None)
    assert CM.decide(waiting, facts(want="job:a"), 1100, 300) == ("start", None)
    # Woken once for this job and nothing ran: not again for it; new activity or new work wakes it.
    woke = machine_row(wake_key="job:a", woke_at=1000)
    assert CM.decide(woke, facts(want="job:a", activity=900), 2000, 300) == (None, "stuck")
    assert CM.decide(woke, facts(want="job:a", activity=1500), 2000, 300) == ("start", None)
    assert CM.decide(woke, facts(want="job:b", activity=900), 2000, 300) == ("start", None)


def test_decide_counts_a_pending_want_as_activity():
    """Work queued after the machine went quiet, that its runner has not claimed yet, holds the idle stop: stopping now
    would only pay to start it again. Work its runner never takes still stops it, idle_s after it was queued."""
    on = machine_row(state="running", active_at=1000, heartbeat_at=1000)
    assert CM.decide(on, facts(want="job:b", want_at=1290), 1300, 300) == ("heartbeat", None)
    assert CM.decide(on, facts(want="job:b", want_at=1290), 1589, 300) == ("heartbeat", None)
    assert CM.decide(on, facts(want="job:b", want_at=1290), 1590, 300) == ("stop", None)


def test_decide_gives_a_machine_being_made_until_its_code_expires_then_makes_it_again():
    booting = machine_row(state="running", runner_id=None, active_at=1000, heartbeat_at=1000, enroll_by=1960)
    # Booting: never idle-stopped, even with nothing to do, while its code can still enroll it.
    assert CM.decide(booting, facts(), 1959, 300) == ("heartbeat", None)
    assert CM.decide({**booting, "heartbeat_at": 1950}, facts(), 1959, 300) == (None, None)
    # Its code expired unused: deleted, whatever its state, and made again for the next want.
    assert CM.decide(booting, facts(), 1960, 300) == ("delete", None)
    assert CM.decide({**booting, "state": "stopped"}, facts(want="job:a"), 1960, 300) == ("delete", None)
    assert CM.decide({**booting, "state": "deleted"}, facts(want="job:a"), 1960, 300) == ("create", None)
    # Enrolled (0) or never made (None): no deadline.
    assert CM.decide({**booting, "enroll_by": 0}, facts(), 1960, 300) == ("stop", None)
    # A refusal's wait holds the delete too; out of credit stops it first.
    assert CM.decide({**booting, "next_try_at": 2000}, facts(), 1960, 300) == (None, None)
    assert CM.decide(booting, facts(frozen=True), 1960, 300) == ("stop", "out_of_credit")
    # Machines made in a row that never enrolled: nothing more is made until a person looks.
    gone = machine_row(machine_id=None, runner_id=None, state="deleted", enroll_by=None)
    assert CM.decide({**gone, "enroll_fails": CM.ENROLL_TRIES - 1}, facts(want="job:a"), 2000, 300) == ("create", None)
    assert CM.decide({**gone, "enroll_fails": CM.ENROLL_TRIES}, facts(want="job:a"), 2000, 300) == (None, "no_enroll")
    assert CM.decide({**gone, "enroll_fails": CM.ENROLL_TRIES}, facts(), 2000, 300) == (None, None)


def test_decide_makes_a_failed_machine_again_and_never_starts_it():
    # HQ marked it failed: its create never got a computer. A want makes a new one, as for a deleted one.
    failed = machine_row(state="failed", enroll_by=None)
    assert CM.decide(failed, facts(want="job:a"), 2000, 300) == ("create", None)
    assert CM.decide({**failed, "state": "deleted"}, facts(want="job:a"), 2000, 300) == ("create", None)
    assert CM.decide(failed, facts(), 2000, 300) == (None, None)
    # Its code's deadline passing is not a delete at HQ: it is made again.
    assert CM.decide({**failed, "enroll_by": 1960}, facts(want="job:a"), 1960, 300) == ("create", None)
    # Its last machines never enrolled: nothing more is made until a person looks.
    assert CM.decide({**failed, "enroll_fails": CM.ENROLL_TRIES}, facts(want="job:a"), 2000, 300) == (
        None, "no_enroll")


def test_decide_wakes_a_stopped_machine_once_per_server_release_to_update():
    behind = facts(update="update:0.5.21")
    assert CM.decide(machine_row(), behind, 1000, 300) == ("start", None)
    assert CM.decide(machine_row(), facts(update="update:0.5.21", frozen=True), 1000, 300) == (None, "out_of_credit")
    # Woken for this release and its runner never reported it: not again for it, and nothing is flagged stuck.
    woke = machine_row(wake_key="update:0.5.21", woke_at=1000)
    assert CM.decide(woke, behind, 2000, 300) == (None, None)
    assert CM.decide(woke, facts(update="update:0.5.22"), 2000, 300) == ("start", None)
    assert CM.decide(woke, facts(want="job:a"), 2000, 300) == ("start", None)
    # Work comes first: an update only stands in when there is none, or its wake is spent.
    assert CM.wake_for(machine_row(), facts(want="job:a", update="update:0.5.21")) == ("job:a", False)


def test_decide_lets_an_update_stand_in_for_work_whose_wake_is_spent_once():
    # Woken for a job its old runner could not take: the server's newer release wakes it once to update, and the
    # wake names the job, so neither wakes it again while nothing runs.
    stuck = machine_row(wake_key="job:a", woke_at=1000)
    behind = facts(want="job:a", update="update:0.5.21")
    assert CM.decide(stuck, facts(want="job:a"), 2000, 300) == (None, "stuck")
    assert CM.wake_for(stuck, behind) == ("update:0.5.21 job:a", False)
    assert CM.decide(stuck, behind, 2000, 300) == ("start", None)
    spent = machine_row(wake_key="update:0.5.21 job:a", woke_at=2000)
    assert CM.decide(spent, behind, 3000, 300) == (None, "stuck")
    assert CM.decide(spent, facts(want="job:a"), 3000, 300) == (None, "stuck")
    assert CM.decide(spent, facts(want="job:b", update="update:0.5.21"), 3000, 300) == ("start", None)
    assert CM.decide(spent, facts(want="job:a", update="update:0.5.22"), 3000, 300) == ("start", None)
    assert CM.decide(spent, behind | {"activity": 2500}, 3000, 300) == ("start", None)


def test_decide_holds_the_idle_stop_while_its_runner_is_updating():
    on = machine_row(state="running", active_at=1000, heartbeat_at=1000)
    assert CM.decide(on, facts(), 1300, 300) == ("stop", None)
    assert CM.decide(on, facts(updating=True), 1300, 300) == ("heartbeat", None)
    assert CM.decide(on, facts(updating=True), 2000, 300) == ("heartbeat", None)


# ----------------------------------------------------------------------------- the manager, against a fake HQ
def test_off_cloud_nothing_changes(api):  # noqa: F811
    store, fake = api.app.state.store, FakeHQ()
    manager = CM.Manager(store, store.settings, api.app.state.execution, client=fake)
    r = runner(api)
    with store.transaction() as c:
        assert placement.auto_place(c, api.app.state.execution, "ops")["runner_id"] == r["runner_id"]
        assert CM.row(c, "ops") is None
    manager.tick()
    assert fake.calls == []
    assert get(api, "cloud-machines") == {"offered": False, "sizes": [], "caps": {}, "shared": None}
    assert employee(api)["cloud_machine"] is None
    post(api, "bots/ops/cloud-machine", {"size": "large"}, expected=409)


def test_first_work_creates_the_machine_and_its_runner_is_that_bots_only(cloud):
    assert adopt(cloud)["mode"] == "cloud"
    assert cloud.tick() == []                                   # no work, no machine
    work(cloud)
    assert cloud.tick() == ["create"]
    _, key, size, code, hub = cloud.hq.calls[-1]
    assert (key, size, hub) == ("ops", "default", cloud.store.settings.runner_url) and len(code) > 30
    assert cloud.row()["state"] == "starting" and cloud.row()["machine_id"] == "m1"
    # The machine enrolls with that code: a computer named after the bot, closed to others, the bot assigned to it.
    enrolled = post(cloud.api, "runners/enroll", {"code": code, "label": "boat-vm", "platform": "linux"})
    with cloud.store.read() as c:
        computer = dict(c.execute("SELECT * FROM runners WHERE id=?", (enrolled["runner_id"],)).fetchone())
        placed = c.execute("SELECT runner_id FROM assignments WHERE bot='ops'").fetchone()["runner_id"]
        others = placement.candidates(c, cloud.api.app.state.auth, "cpo")
    assert computer["label"] == "ops (cloud)" and not computer["accepts_member_bots"]
    assert placed == enrolled["runner_id"] and cloud.row()["runner_id"] == enrolled["runner_id"]
    assert enrolled["runner_id"] not in {r["id"] for r in others}
    # A retried enroll (lost answer) gets the same computer back, whatever label the machine sends.
    again = post(cloud.api, "runners/enroll", {"code": code, "label": "other", "platform": "linux"})
    assert again["runner_id"] == enrolled["runner_id"]
    computers = {m["id"]: m for m in get(cloud.api, "operations")["computers"]}
    assert computers[enrolled["runner_id"]]["cloud_machine"]["bot"] == "ops"
    shown = employee(cloud.api)["cloud_machine"]
    assert shown["mode"] == "cloud" and shown["state"] == "starting"


def test_heartbeat_then_idle_stop_at_break_even_then_wake_with_a_resize(cloud):
    enrolled(cloud)
    cloud.hq.calls.clear()
    cloud.manager.synced = None
    cloud.tick()                                                # learns the sizes: default stops after 300 s idle
    assert cloud.hq.actions() == []
    assert cloud.tick(60) == ["heartbeat"]
    assert cloud.tick(10) == ["heartbeat"]                      # once a minute, not every look
    with cloud.store.transaction() as c:                        # the queued chat is not why it stays on
        c.execute("UPDATE jobs SET state='canceled' WHERE bot_id='ops'")
    cloud.hq.calls.clear()
    cloud.at[0] = cloud.row()["active_at"] + 299
    cloud.tick()
    assert cloud.hq.actions() == ["heartbeat"]
    cloud.tick(1)
    assert cloud.hq.actions() == ["heartbeat", "stop"] and cloud.row()["state"] == "stopped"
    assert cloud.tick(600) == ["heartbeat", "stop"]             # idle and stopped: nothing
    # A size chosen now waits for the next start, which sends it.
    post(cloud.api, "bots/ops/cloud-machine", {"size": "large"})
    assert cloud.row()["size"] == "large" and cloud.row()["machine_size"] == "default"
    work(cloud, text="Another thing")
    cloud.hq.calls.clear()
    assert cloud.tick(10) == ["start"]
    assert cloud.hq.calls[-1] == ("start", "m1", "large") and cloud.row()["machine_size"] == "large"
    cloud.hq.calls.clear()
    cloud.tick(10)
    assert cloud.hq.actions() == []                              # running now: no second start


def test_work_its_machine_never_takes_does_not_start_it_again_but_new_work_does(cloud):
    enrolled(cloud)
    cloud.tick(400)                                             # the chat was never claimed there: idle, stopped
    cloud.hq.calls.clear()
    assert cloud.tick(10) == [] and cloud.tick(600) == [] and cloud.row()["problem"] == "stuck"
    work(cloud, text="Another thing")
    assert cloud.tick(10) == ["start"] and cloud.row()["problem"] == ""


def test_work_queued_as_it_goes_quiet_keeps_it_on_for_the_runner_to_take(cloud):
    enrolled(cloud)
    with cloud.store.transaction() as c:
        c.execute("UPDATE jobs SET state='canceled' WHERE bot_id='ops'")
    start = cloud.row()["active_at"]
    cloud.at[0] = start + 290
    work(cloud, text="Another thing")                           # queued (real clock) just before the idle stop
    queued = cloud.at[0]
    with cloud.store.transaction() as c:                        # the hub's clock is the fake one's
        c.execute("UPDATE jobs SET created_at=? WHERE bot_id='ops' AND state='queued'", (stamp(cloud),))
    cloud.hq.calls.clear()
    cloud.at[0] = start + 300
    cloud.tick()
    cloud.at[0] = queued + 299
    cloud.tick()
    assert "stop" not in cloud.hq.actions() and cloud.row()["problem"] == ""
    cloud.at[0] = queued + 300                                  # never claimed: it stops after all
    cloud.tick()
    assert cloud.hq.actions()[-1] == "stop"


def test_a_machine_that_never_enrolls_is_made_again_with_a_fresh_code_three_times_at_most(cloud):
    adopt(cloud)
    work(cloud)
    deadline = CM.ENROLL_MINUTES * 60 + CM.ENROLL_GRACE
    codes = []
    for attempt in range(CM.ENROLL_TRIES):
        cloud.hq.calls.clear()
        assert cloud.tick(10) == ["create"]
        codes.append(cloud.hq.calls[-1][3])
        made = cloud.row()["machine_id"]
        assert cloud.row()["enroll_by"] == cloud.at[0] + deadline
        # It boots for longer than its size's idle time: never idle-stopped meanwhile.
        cloud.tick(deadline - 1)
        assert "stop" not in cloud.hq.actions() and "delete" not in cloud.hq.actions()
        cloud.tick(1)
        assert cloud.hq.actions()[-1] == "delete" and ("delete", made) in cloud.hq.calls
        assert cloud.row()["state"] == "deleted" and cloud.row()["enroll_fails"] == attempt + 1
    assert len(set(codes)) == CM.ENROLL_TRIES
    assert [e["target"] for e in events(cloud, "cloud_machine.deleted")] == ["ops"] * CM.ENROLL_TRIES
    # Three in a row never enrolled: nothing more is made for this work or new work, and people see why.
    cloud.hq.calls.clear()
    work(cloud, text="Another thing")
    assert cloud.tick(10) == [] and cloud.tick(600) == [] and cloud.row()["problem"] == "no_enroll"
    with cloud.store.read() as c:
        assert CM.waiting(c, "ops") == "its cloud machine never connected to this server"
        assert "ops" in CM.health(c, cloud.store.settings)["summary"]
    # A person's change tries again; this one enrolls, and the count starts over.
    post(cloud.api, "bots/ops/cloud-machine", {"size": "large"})
    assert cloud.row()["problem"] == "" and cloud.tick(10) == ["create"]
    post(cloud.api, "runners/enroll", {"code": cloud.hq.calls[-1][3], "label": "boat-vm", "platform": "linux"})
    assert cloud.row()["enroll_by"] == 0 and cloud.row()["enroll_fails"] == 0 and cloud.row()["runner_id"]


def test_the_new_machine_waits_for_hq_to_finish_deleting_the_one_that_never_enrolled(cloud):
    adopt(cloud)
    work(cloud)
    assert cloud.tick(10) == ["create"]
    pending = []
    cloud.hq.delete = lambda machine_id: (cloud.hq.calls.append(("delete", machine_id)), pending.append(machine_id))
    cloud.tick(CM.ENROLL_MINUTES * 60 + CM.ENROLL_GRACE)
    assert cloud.hq.actions()[-1] == "delete" and pending == ["m1"]
    # HQ keeps m1 live until boat confirms: a create answers with it, which is not taken for the new machine, and a
    # list does not bring it back.
    cloud.hq.calls.clear()
    cloud.manager.synced = None
    assert cloud.tick(10) == ["create"]
    assert cloud.row()["machine_id"] == "m1" and cloud.row()["state"] == "deleted" and not cloud.row()["enroll_by"]
    assert cloud.tick(5) == ["create"]                          # waits out BACKOFF
    cloud.hq.machines["m1"]["state"] = "deleted"                # boat confirmed
    cloud.manager.synced = None
    assert cloud.tick(10) == ["create", "create"]
    assert cloud.row()["machine_id"] == "m2" and cloud.row()["enroll_by"] == cloud.at[0] + (
        CM.ENROLL_MINUTES * 60 + CM.ENROLL_GRACE)


def test_a_create_whose_answer_was_lost_is_not_taken_for_a_machine_that_never_enrolled(cloud):
    adopt(cloud)
    work(cloud)
    made = cloud.hq.create

    def lost(*args):
        made(*args)                                             # HQ made it; the answer never came back
        raise CM.Refused("unreachable")
    cloud.hq.create = lost
    assert cloud.tick(10) == ["create"] and cloud.row()["machine_id"] is None
    first = cloud.hq.calls[-1][3]
    del cloud.hq.create
    post(cloud.api, "runners/enroll", {"code": first, "label": "boat-vm", "platform": "linux"})
    assert cloud.row()["enroll_by"] == 0
    # HQ's list is read before any retry and gives the machine back: it is enrolled, so no deadline and no second create.
    assert cloud.tick(15) == ["create"]
    assert cloud.row()["machine_id"] == "m1" and cloud.row()["enroll_by"] == 0 and cloud.row()["problem"] == ""
    assert cloud.row()["active_at"]                             # its idle time counts from now, not from the old work
    cloud.tick(CM.ENROLL_MINUTES * 60 + CM.ENROLL_GRACE + 10)
    assert "delete" not in cloud.hq.actions()


def test_a_machine_hq_marked_failed_is_made_again_not_started(cloud):
    adopt(cloud)
    work(cloud)
    made = cloud.hq.create

    def failed(*args):
        made(*args)                                             # HQ made it; boat never gave it a computer
        cloud.hq.machines["m1"]["state"] = "failed"
        raise CM.Refused("boat_error", 502)
    cloud.hq.create = failed
    assert cloud.tick(10) == ["create"] and cloud.row()["machine_id"] is None
    del cloud.hq.create
    # HQ's list says it failed: the row keeps its id and state, and the next want makes a new one, never a start.
    cloud.manager.synced = None
    cloud.manager.sync(cloud.hq, cloud.at[0])
    assert cloud.row()["machine_id"] == "m1" and cloud.row()["state"] == "failed"
    assert cloud.row()["enroll_fails"] == 1                     # counted once, not at every list
    cloud.manager.synced = None
    cloud.manager.sync(cloud.hq, cloud.at[0])
    assert cloud.row()["enroll_fails"] == 1
    cloud.hq.calls.clear()
    assert cloud.tick(20) == ["create"]
    assert cloud.hq.calls[-1][0] == "create" and cloud.row()["machine_id"] == "m2"
    assert cloud.row()["state"] == "starting" and cloud.row()["problem"] == ""
    post(cloud.api, "runners/enroll", {"code": cloud.hq.calls[-1][3], "label": "boat-vm", "platform": "linux"})
    assert cloud.row()["enroll_by"] == 0 and cloud.row()["runner_id"] and cloud.row()["enroll_fails"] == 0


def test_a_boat_that_keeps_failing_stops_being_asked(cloud):
    adopt(cloud)
    work(cloud)
    made = cloud.hq.create

    def failed(*args):
        machine = made(*args)
        cloud.hq.machines[machine["id"]]["state"] = "failed"
        raise CM.Refused("boat_error", 502)
    cloud.hq.create = failed
    for _ in range(40):                                          # ordinary looks, 10 s apart: HQ is listed before each retry
        cloud.tick(10)
    assert cloud.hq.actions().count("create") == CM.ENROLL_TRIES     # then it waits for a person
    assert cloud.row()["problem"] == "no_enroll" and cloud.row()["enroll_fails"] == CM.ENROLL_TRIES


def test_an_hq_outage_never_uses_up_the_tries(cloud):
    adopt(cloud)
    work(cloud)
    made = cloud.hq.create

    def unreachable(*args):
        raise CM.Refused("unreachable", 0)
    cloud.hq.create = unreachable
    for _ in range(CM.ENROLL_TRIES + 2):
        cloud.tick(CM.BACKOFF_MAX)
    assert cloud.row()["enroll_fails"] == 0 and cloud.row()["problem"] != "no_enroll"
    cloud.hq.create = made
    cloud.hq.calls.clear()
    assert cloud.tick(CM.BACKOFF_MAX) == ["create"] and cloud.row()["machine_id"]


def test_a_made_machine_boat_then_fails_is_made_again_for_the_same_work(cloud):
    adopt(cloud)
    work(cloud)
    assert cloud.tick(10) == ["create"] and cloud.row()["wake_key"]   # woken for this work
    first = cloud.row()["machine_id"]
    cloud.hq.machines[first]["state"] = "failed"                       # boat never gave it a computer
    cloud.manager.synced = None
    cloud.manager.sync(cloud.hq, cloud.at[0])
    assert cloud.row()["state"] == "failed" and cloud.row()["enroll_fails"] == 1 and not cloud.row()["wake_key"]
    cloud.hq.calls.clear()
    assert cloud.tick(20) == ["create"] and cloud.row()["machine_id"] != first


def test_a_failure_already_counted_is_never_counted_again(cloud):
    adopt(cloud)
    work(cloud)
    assert cloud.tick(10) == ["create"]
    mine = cloud.row()["machine_id"]
    # Even if HQ listed an old failed machine beside this row's own deleted one under the same key, the row keeps its own.
    cloud.hq.machines["old"] = dict(cloud.hq.machines[mine], id="old", state="failed")
    cloud.hq.machines[mine]["state"] = "deleted"
    with cloud.store.transaction() as c:
        CM.save(c, cloud.row()["bot"], state="deleted", enroll_fails=2)
    cloud.manager.synced = None
    cloud.manager.sync(cloud.hq, cloud.at[0])
    assert cloud.row()["machine_id"] == mine and cloud.row()["enroll_fails"] == 2


def test_a_row_failed_before_this_release_is_not_stuck_on_its_old_wake(cloud):
    adopt(cloud)
    work(cloud)
    assert cloud.tick(10) == ["create"]
    first = cloud.row()["machine_id"]
    cloud.hq.machines[first]["state"] = "failed"
    with cloud.store.transaction() as c:                         # as the old sync left it: failed, wake guard kept
        CM.save(c, cloud.row()["bot"], state="failed", enroll_by=None)
    assert cloud.row()["wake_key"]
    cloud.hq.calls.clear()
    assert cloud.tick(20) == ["create"] and cloud.row()["machine_id"] != first


def test_no_create_follows_a_refused_one_until_hq_is_listed(cloud):
    adopt(cloud)
    work(cloud)
    made, listed = cloud.hq.create, cloud.hq.list

    def failed(*args):
        machine = made(*args)
        cloud.hq.machines[machine["id"]]["state"] = "failed"
        raise CM.Refused("boat_error", 502)

    def down():
        raise CM.Refused("unreachable", 0)
    cloud.hq.create, cloud.hq.list = failed, down
    for _ in range(12):
        cloud.tick(CM.BACKOFF_MAX)
    assert cloud.hq.actions().count("create") == 1             # HQ's list never answered: never asked again
    cloud.hq.list = listed
    for _ in range(40):
        cloud.tick(CM.BACKOFF_MAX)
    assert cloud.hq.actions().count("create") == CM.ENROLL_TRIES


def test_a_create_hq_answers_as_failed_counts_too(cloud):
    adopt(cloud)
    work(cloud)
    made = cloud.hq.create

    def failed(*args):
        machine = made(*args)
        cloud.hq.machines[machine["id"]]["state"] = "failed"
        return dict(cloud.hq.machines[machine["id"]])
    cloud.hq.create = failed
    for _ in range(40):
        cloud.tick(CM.BACKOFF_MAX)
    assert cloud.hq.actions().count("create") == CM.ENROLL_TRIES and cloud.row()["problem"] == "no_enroll"


def test_always_on_starts_with_no_work_and_never_idles(cloud):
    enrolled(cloud)
    with cloud.store.transaction() as c:
        c.execute("UPDATE jobs SET state='canceled' WHERE bot_id='ops'")
    cloud.tick(400)
    assert cloud.row()["state"] == "stopped"
    post(cloud.api, "bots/ops/cloud-machine", {"always_on": True})
    cloud.hq.calls.clear()
    assert cloud.tick(10) == ["start"]
    for _ in range(5):
        cloud.tick(600)
    assert set(cloud.hq.actions()[1:]) == {"heartbeat"}


def test_a_frequent_watcher_keeps_it_on_and_a_due_one_wakes_it(cloud):
    r = enrolled(cloud)
    with cloud.store.transaction() as c:
        c.execute("UPDATE jobs SET state='canceled' WHERE bot_id='ops'")

    def ran(seconds_ago, every):
        when = H.shift(H.now(), seconds=-seconds_ago + (cloud.at[0] - time.time()))
        with cloud.store.transaction() as c:
            c.execute("INSERT INTO watcher_runs(bot,name,runner_id,started_at,finished_at,exit_code,every) "
                      "VALUES('ops','mail',?,?,?,0,?) ON CONFLICT(bot,name) DO UPDATE SET finished_at=excluded.finished_at",
                      (r["runner_id"], when, when, every))
    for _ in range(4):                                          # every two minutes: never idle for 300 s
        cloud.at[0] += 120
        ran(0, 120)
        cloud.tick()
    assert "stop" not in cloud.hq.actions()
    # A run that falls due and is never taken counts from when it fell due (120 s after the last), like queued work.
    cloud.tick(419)
    assert cloud.hq.actions()[-1] == "heartbeat"
    cloud.tick(1)
    assert cloud.hq.actions()[-1] == "stop"
    # Its next run is due and the machine is off: it starts for it.
    cloud.hq.calls.clear()
    ran(3600, 1800)
    assert cloud.tick(10) == ["start"]


def test_out_of_credit_starts_nothing_until_open_again(cloud):
    enrolled(cloud)
    cloud.tick(400)
    assert cloud.row()["state"] == "stopped"
    cloud_access.apply(cloud.store, cloud_access.parse(signals("frozen", "out_of_credit")))
    work(cloud, text="More")
    cloud.hq.calls.clear()
    assert cloud.tick(10) == [] and cloud.row()["problem"] == "out_of_credit"
    with cloud.store.read() as c:
        assert CM.waiting(c, "ops").startswith("out of credit")
    # HQ says 402 even though this server thought it open: the same, asked again only once HQ says open.
    cloud_access.apply(cloud.store, cloud_access.parse(signals("open")))
    cloud.hq.fail["start"] = [CM.Refused("frozen", 402)]
    assert cloud.tick(10) == ["start"] and cloud.row()["problem"] == "out_of_credit"
    assert cloud.tick(10) == ["start"]                          # waits out FROZEN_RETRY
    cloud_access.apply(cloud.store, cloud_access.parse(signals("open")))
    assert cloud.tick(10) == ["start", "start"] and cloud.row()["problem"] == ""


def test_hq_errors_back_off_with_one_event_and_a_wait_note(cloud):
    enrolled(cloud)
    cloud.tick(400)
    work(cloud, text="More")
    down = CM.Refused("unreachable")
    cloud.hq.fail["start"] = [down, down, down]
    cloud.hq.calls.clear()
    assert cloud.tick(10) == ["start"]
    assert cloud.row()["problem"] == "hq:unreachable" and cloud.row()["failures"] == 1
    with cloud.store.read() as c:
        assert "didn't start" in CM.waiting(c, "ops")
    assert cloud.tick(10) == ["start"]                          # inside 15 s: no call
    assert cloud.tick(6) == ["start", "start"]                  # 15 s, then 30 s
    assert cloud.tick(20) == ["start", "start"]
    assert cloud.tick(11) == ["start", "start", "start"]
    assert len(events(cloud, "cloud_machine.refused")) == 1     # one per streak, not per retry
    assert cloud.tick(60) == ["start"] * 4
    assert cloud.row()["failures"] == 0 and cloud.row()["problem"] == ""


def test_a_resume_right_after_a_stop_retries_soon_without_counting_a_failure(cloud):
    enrolled(cloud)
    cloud.tick(400)
    work(cloud, text="More")
    cloud.hq.fail["start"] = [CM.Refused("sandbox_stopping", 409)]
    cloud.hq.calls.clear()
    assert cloud.tick(10) == ["start"]
    assert cloud.row()["failures"] == 0 and cloud.row()["problem"] == ""
    assert cloud.tick(5) == ["start", "start"] and cloud.row()["state"] == "starting"


def test_a_machine_hq_no_longer_has_is_made_again_and_replaces_the_old_computer(cloud):
    first = enrolled(cloud)
    cloud.tick(400)
    work(cloud, text="More")
    cloud.hq.fail["start"] = [CM.Refused("not_found", 404)]
    cloud.hq.machines["m1"]["state"] = "deleted"
    cloud.hq.calls.clear()
    assert cloud.tick(10) == ["start"] and cloud.row()["machine_id"] is None
    assert cloud.tick(10) == ["start", "create"]
    code = cloud.hq.calls[-1][3]
    second = post(cloud.api, "runners/enroll", {"code": code, "label": "boat-vm", "platform": "linux"})
    with cloud.store.read() as c:
        old = c.execute("SELECT revoked_at FROM runners WHERE id=?", (first["runner_id"],)).fetchone()
        placed = c.execute("SELECT runner_id FROM assignments WHERE bot='ops'").fetchone()["runner_id"]
    assert old["revoked_at"] and placed == second["runner_id"]


def test_moving_to_another_computer_stops_it_and_the_machine_can_then_be_deleted(cloud):
    enrolled(cloud)
    other = runner(cloud.api, label="Office Mac")
    with cloud.store.transaction() as c:
        c.execute("UPDATE jobs SET state='canceled' WHERE bot_id='ops'")
        generation = c.execute("SELECT generation FROM assignments WHERE bot='ops'").fetchone()["generation"]
    post(cloud.api, "bots/ops/cloud-machine", {"size": "small"}, expected=200)
    delete = cloud.api.delete("/api/v2/bots/ops/cloud-machine", headers=headers())
    assert delete.status_code == 409                            # still runs there
    assign(cloud.api, other, "ops", generation)
    assert cloud.row()["mode"] == "computer"
    cloud.hq.calls.clear()
    assert cloud.tick(10) == ["stop"]
    work(cloud, text="More")
    assert cloud.tick(10) == ["stop"]                           # its work goes to the other computer
    pending = []                                                # HQ keeps m1 live until boat confirms
    cloud.hq.delete = lambda machine_id: (cloud.hq.calls.append(("delete", machine_id)), pending.append(machine_id))
    delete = cloud.api.delete("/api/v2/bots/ops/cloud-machine", headers=headers())
    assert delete.status_code == 200, delete.text
    assert pending == ["m1"]
    assert cloud.row() == {**cloud.row(), "mode": "computer", "placement": "own", "machine_id": "m1",
                           "state": "deleted", "runner_id": None}
    assert employee(cloud.api)["cloud_machine"]["state"] == "deleted"
    assert cloud.api.delete("/api/v2/bots/ops/cloud-machine", headers=headers()).status_code == 404
    assert pending == ["m1"]
    # Choosing its own machine again makes a new one at its next work: not the doomed m1 HQ still answers with.
    post(cloud.api, "bots/ops/cloud-machine", {"mode": "cloud"})
    cloud.hq.calls.clear()
    assert cloud.tick(10) == ["create"] and cloud.row()["machine_id"] == "m1" and cloud.row()["state"] == "deleted"
    cloud.hq.machines["m1"]["state"] = "deleted"                # boat confirmed
    assert cloud.tick(CM.BACKOFF) == ["create", "create"] and cloud.row()["machine_id"] == "m2"


def test_nothing_defaults_to_a_machine_before_hq_lists_sizes(api, monkeypatch):  # noqa: F811
    store = api.app.state.store
    cloud_access.apply(store, cloud_access.parse(signals("open")))
    fake = FakeHQ()
    fake.list = lambda: (_ for _ in ()).throw(CM.Refused("not_found", 404))
    monkeypatch.setattr(api.app.state.cloud_machines, "client", fake)
    r = runner(api)
    api.app.state.cloud_machines.tick()
    with store.transaction() as c:
        assert not CM.offered(c, store.settings)
        assert placement.auto_place(c, api.app.state.execution, "ops")["runner_id"] == r["runner_id"]
    assert get(api, "cloud-machines")["offered"] is False and employee(api)["cloud_machine"] is None


def test_hq_saying_machines_are_not_configured_hides_the_option_again(cloud):
    cloud.tick(CM.SYNC)                                         # the list the fixture made: offered
    with cloud.store.read() as c:
        assert CM.offered(c, cloud.store.settings)
    cloud.hq.list = lambda: (_ for _ in ()).throw(CM.Refused("not_configured", 503))
    cloud.tick(CM.SYNC)
    with cloud.store.read() as c:
        assert not CM.offered(c, cloud.store.settings)
    assert get(cloud.api, "cloud-machines")["offered"] is False
    del cloud.hq.list                                           # HQ answers again: the sizes come back
    cloud.tick(CM.SYNC)
    with cloud.store.read() as c:
        assert CM.offered(c, cloud.store.settings)


def test_sizes_and_choices_are_checked(cloud):
    adopt(cloud)
    cloud.tick()
    shown = get(cloud.api, "cloud-machines")
    assert shown["offered"] and [s["name"] for s in shown["sizes"]] == ["small", "default", "large", "xlarge"]
    post(cloud.api, "bots/ops/cloud-machine", {"size": "huge"}, expected=422)
    post(cloud.api, "bots/ops/cloud-machine", {"mode": "computer"}, expected=422)
    post(cloud.api, "bots/ops/cloud-machine", {"size": "large"}, token="cara-test", expected=403)


# ----------------------------------------------------------------------------- the shared machine
def stamp(cloud, ago=0):
    """The fake clock's now (less `ago` seconds) as the hub's ISO time."""
    return H.shift(H.now(), seconds=cloud.at[0] - time.time() - ago)


def placed(cloud):
    with cloud.store.read() as c:
        return {r["bot"]: r["runner_id"] for r in c.execute("SELECT bot,runner_id FROM assignments")}


def shared_enrolled(cloud, *bots):
    """These bots on the shared machine, made for the first one's work and enrolled; returns its computer's id."""
    for bot in bots:
        adopt(cloud, bot, "shared")
    work(cloud, bots[0])
    assert cloud.tick()[-1] == "create" and cloud.hq.calls[-1][1] == CM.SHARED
    return post(cloud.api, "runners/enroll", {"code": cloud.hq.calls[-1][3], "label": "boat-vm",
                                              "platform": "linux"})["runner_id"]


def idle(cloud):
    with cloud.store.transaction() as c:
        c.execute("UPDATE jobs SET state='canceled'")


def test_placement_defaults_by_role_and_a_person_switches_it(cloud):
    execution = cloud.api.app.state.execution
    with cloud.store.transaction() as c:
        # coo is in Engineering on the team chart; finance was made from the video producer's template.
        c.execute("UPDATE registry_metadata SET value=json_set(value,'$.org_groups',json(?)) WHERE key='people'",
                  (json.dumps({"engineering": {"name": "Engineering", "parent": ""}}),))
        c.execute("UPDATE bot_config SET config=json_set(config,'$.team','engineering') WHERE bot='coo'")
        c.execute("UPDATE bot_config SET config=json_set(config,'$.template','video-producer') "
                  "WHERE bot='finance'")
        for bot in ("ops", "coo", "finance"):
            assert placement.auto_place(c, execution, bot) is None
    assert [cloud.row(bot)["placement"] for bot in ("ops", "coo", "finance")] == ["shared", "own", "own"]
    assert employee(cloud.api, "ops")["cloud_machine"]["placement"] == "shared"
    post(cloud.api, "bots/coo/cloud-machine", {"placement": "shared"})
    post(cloud.api, "bots/ops/cloud-machine", {"placement": "own"})
    assert cloud.row("coo")["placement"] == "shared" and cloud.row("ops")["placement"] == "own"
    post(cloud.api, "bots/ops/cloud-machine", {"placement": "big"}, expected=422)
    post(cloud.api, "bots/ops/cloud-machine", {"placement": "shared"}, token="cara-test", expected=403)
    with cloud.store.read() as c:
        assert CM.members(c) == ["coo"]


def test_one_shared_machine_wakes_for_any_member_and_hosts_them_all(cloud):
    adopt(cloud, "ops", "shared")
    adopt(cloud, "finance", "shared")
    assert cloud.tick() == []                                   # no work, no machine
    work(cloud, "finance")
    assert cloud.tick() == ["create"]
    _, key, size, code, _ = cloud.hq.calls[-1]
    assert (key, size) == (CM.SHARED, "default") and cloud.row(CM.SHARED)["state"] == "starting"
    assert cloud.row("ops")["machine_id"] is None and cloud.row("finance")["machine_id"] is None
    # Its computer is the company's, open to members' bots, and every member is assigned to it.
    rid = post(cloud.api, "runners/enroll", {"code": code, "label": "boat-vm", "platform": "linux"})["runner_id"]
    with cloud.store.read() as c:
        computer = dict(c.execute("SELECT * FROM runners WHERE id=?", (rid,)).fetchone())
        offered = {r["id"] for r in placement.candidates(c, cloud.api.app.state.auth, "cpo")}
    assert computer["label"] == "Shared cloud machine" and computer["accepts_member_bots"] and computer["owner_id"] == "ana"
    assert placed(cloud) == {"ops": rid, "finance": rid} and rid not in offered
    assert {m["id"]: m for m in get(cloud.api, "operations")["computers"]}[rid]["cloud_machine"] == {
        "bot": None, "shared": True, "size": "default", "state": "starting"}
    assert employee(cloud.api, "ops")["cloud_machine"] == {**employee(cloud.api, "ops")["cloud_machine"],
                                                           "mode": "cloud", "placement": "shared", "state": "starting"}
    assert get(cloud.api, "cloud-machines")["shared"]["bots"] == ["finance", "ops"]
    # A bot joining while it is up is assigned there at the next look; nothing is started or made for it.
    adopt(cloud, "coo", "shared")
    cloud.hq.calls.clear()
    assert cloud.tick(10) == [] and placed(cloud)["coo"] == rid
    # Idle, it stops; another member's new work starts it again.
    idle(cloud)
    cloud.tick(400)
    assert cloud.hq.actions()[-1] == "stop" and cloud.row(CM.SHARED)["state"] == "stopped"
    cloud.hq.calls.clear()
    work(cloud, "ops")
    assert cloud.tick(10) == ["start"]


def test_the_shared_machine_idles_only_once_every_member_is_idle(cloud):
    rid = shared_enrolled(cloud, "ops", "finance")
    idle(cloud)
    cloud.manager.synced = None
    cloud.tick()                                                # learns the sizes: default stops after 300 s idle
    start = cloud.row(CM.SHARED)["active_at"]
    cloud.at[0] = start + 200
    with cloud.store.transaction() as c:                        # finance's watcher ran there; ops did nothing
        c.execute("INSERT INTO watcher_runs(bot,name,runner_id,started_at,finished_at,exit_code,every) "
                  "VALUES('finance','mail',?,?,?,0,3600)", (rid, stamp(cloud), stamp(cloud)))
    cloud.hq.calls.clear()
    cloud.at[0] = start + 300
    cloud.tick()
    cloud.at[0] = start + 499
    cloud.tick()
    assert "stop" not in cloud.hq.actions()
    cloud.at[0] = start + 500
    cloud.tick()
    assert cloud.hq.actions()[-1] == "stop"


def test_switching_own_and_shared_moves_the_bot_and_stops_the_machine_it_left(cloud):
    own = enrolled(cloud)["runner_id"]                          # ops on its own machine m1
    idle(cloud)
    post(cloud.api, "bots/ops/cloud-machine", {"placement": "shared"})
    cloud.hq.calls.clear()
    # The shared machine is made for the bot waiting to move there; its own, with no bot of its own left, stops.
    assert sorted(cloud.tick(10)) == ["create", "stop"]
    assert ("stop", "m1") in cloud.hq.calls
    code = next(call[3] for call in cloud.hq.calls if call[0] == "create")
    rid = post(cloud.api, "runners/enroll", {"code": code, "label": "boat-vm", "platform": "linux"})["runner_id"]
    assert placed(cloud)["ops"] == rid and cloud.row()["mode"] == "cloud" and cloud.row()["placement"] == "shared"
    # Its own machine can go now that it runs elsewhere.
    delete = cloud.api.delete("/api/v2/bots/ops/cloud-machine", headers=headers())
    assert delete.status_code == 200, delete.text
    assert cloud.row()["state"] == "deleted" and cloud.row()["runner_id"] is None and cloud.row()["placement"] == "shared"
    with cloud.store.read() as c:
        assert c.execute("SELECT revoked_at FROM runners WHERE id=?", (own,)).fetchone()["revoked_at"]
    # Back to its own: a new one is made for the move, and the shared one, with no member left, stops.
    post(cloud.api, "bots/ops/cloud-machine", {"placement": "own"})
    cloud.hq.calls.clear()
    assert sorted(cloud.tick(10)) == ["create", "stop"]
    create = next(call for call in cloud.hq.calls if call[0] == "create")
    assert create[1] == "ops" and ("stop", cloud.row(CM.SHARED)["machine_id"]) in cloud.hq.calls
    again = post(cloud.api, "runners/enroll", {"code": create[3], "label": "boat-vm", "platform": "linux"})
    assert placed(cloud)["ops"] == again["runner_id"] and cloud.row()["placement"] == "own"


def test_a_member_moved_to_a_computer_leaves_and_the_last_one_out_stops_it(cloud):
    shared_enrolled(cloud, "ops", "finance")
    other = runner(cloud.api, label="Office Mac")
    idle(cloud)
    generations = {}
    with cloud.store.read() as c:
        generations = {r["bot"]: r["generation"] for r in c.execute("SELECT bot,generation FROM assignments")}
    assign(cloud.api, other, "ops", generations["ops"])
    assert cloud.row()["mode"] == "computer" and cloud.row()["placement"] == "shared"
    cloud.hq.calls.clear()
    assert "stop" not in cloud.tick(10)                         # finance is still on it, and not idle yet
    assert get(cloud.api, "cloud-machines")["shared"]["bots"] == ["finance"]
    assign(cloud.api, other, "finance", generations["finance"])
    assert cloud.tick(10)[-1] == "stop"
    work(cloud, "ops")
    assert cloud.tick(10).count("start") == 0                   # its work goes to the other computer


def test_the_shared_machines_size_and_always_on_are_the_companys(cloud):
    adopt(cloud, "ops", "shared")
    post(cloud.api, "cloud-machines/shared", {"size": "large"}, token="cara-test", expected=403)
    post(cloud.api, "cloud-machines/shared", {"size": "huge"}, expected=422)
    assert post(cloud.api, "cloud-machines/shared", {"size": "large"}, token="ben-test")["shared"]["size"] == "large"
    # A refusal on its first start is every member's wait note and Health's.
    post(cloud.api, "cloud-machines/shared", {"always_on": True})
    cloud.hq.fail["create"] = [CM.Refused("unreachable")]
    assert cloud.tick(10) == ["create"] and cloud.hq.calls[-1][1:3] == (CM.SHARED, "large")
    with cloud.store.read() as c:
        assert "didn't start" in CM.waiting(c, "ops") and "ops" in CM.health(c, cloud.store.settings)["summary"]
    assert cloud.tick(20) == ["create", "create"] and cloud.row(CM.SHARED)["state"] == "starting"
    for _ in range(3):
        cloud.tick(600)
    assert "stop" not in cloud.hq.actions()                     # Always on never idles


def test_another_bots_own_machine_is_never_a_computer_to_assign_and_the_shared_one_is(cloud):
    own = enrolled(cloud)                                       # ops's own machine
    with cloud.store.transaction() as c:
        CM.ensure_shared(c)
        CM.save(c, CM.SHARED, machine_id="m9", state="running")
        code = CM.mint(c, CM.SHARED, "ana")
    shared = post(cloud.api, "runners/enroll", {"code": code, "label": "boat-vm", "platform": "linux"})
    # An owner (who passes every other check) still cannot put finance on ops's own machine.
    refused = cloud.api.post("/api/v2/bots/finance/assignment", headers=headers(),
                             json={"runner_id": own["runner_id"], "expected_generation": 0})
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "cloud_machine", refused.text
    with cloud.store.read() as c:
        assert not c.execute("SELECT 1 FROM assignments WHERE bot='finance'").fetchone()
    # The shared machine's computer takes any bot: choosing it is choosing the shared cloud machine.
    assign(cloud.api, shared, "finance")
    assert cloud.row("finance")["mode"] == "cloud" and cloud.row("finance")["placement"] == "shared"
    with cloud.store.read() as c:
        assert CM.members(c) == ["finance"]
    # Its own bot still goes back on it.
    with cloud.store.read() as c:
        generation = c.execute("SELECT generation FROM assignments WHERE bot='ops'").fetchone()["generation"]
    assign(cloud.api, shared, "ops", generation)
    assert cloud.row()["placement"] == "shared"
    assign(cloud.api, own, "ops", generation + 1)
    assert cloud.row()["placement"] == "own"


def test_a_setup_finished_before_hq_listed_sizes_goes_on_cloud_machines_when_it_does(api, monkeypatch):  # noqa: F811
    store = api.app.state.store
    cloud_access.apply(store, cloud_access.parse(signals("open")))
    fake = FakeHQ()
    listing = fake.list
    fake.list = lambda: (_ for _ in ()).throw(CM.Refused("unreachable"))
    manager = api.app.state.cloud_machines
    monkeypatch.setattr(manager, "client", fake)
    with store.transaction() as c:                              # a bootstrap bot setup left planned, with no computer
        c.execute("UPDATE bots SET state='planned' WHERE slug='coo'")
        c.execute("UPDATE bot_config SET config=json_set(config,'$.materialize',json('true')) "
                  "WHERE bot='coo'")
        c.execute("UPDATE bots SET state='archived' WHERE slug='doc-updater'")
    manager.tick()
    with store.read() as c:
        assert not CM.offered(c, store.settings) and CM.rows(c) == {}
    fake.list = listing
    manager.synced = None
    manager.tick()                                              # HQ answers: the finished setup is placed now
    with store.read() as c:
        adopted = set(CM.rows(c)) - {CM.SHARED}
        state = c.execute("SELECT state FROM bots WHERE slug='coo'").fetchone()["state"]
    assert adopted == {"coo", "ops", "cpo", "product-design", "finance", "inbox"} and state == "active"
    assert employee(api)["cloud_machine"]["mode"] == "cloud"
    # Later syncs leave a person's choices alone: a bot moved to a computer stays there.
    r = runner(api)
    assign(api, r, "ops")
    manager.tick()
    manager.synced = None
    manager.tick()
    with store.read() as c:
        assert CM.row(c, "ops")["mode"] == "computer"


# ----------------------------------------------------------------------------- the HTTP client
def test_the_client_speaks_the_contract(monkeypatch):
    seen = []

    def answer(request):
        seen.append((request.method, str(request.url), json.loads(request.content or b"null"),
                     request.headers["authorization"]))
        path = request.url.path
        if path.endswith("/m1/start"):
            return httpx.Response(402, json={"error": "frozen"})
        if path.endswith("/m2/start"):
            return httpx.Response(409, json={"error": {"code": "sandbox_stopping", "message": "x"}})
        if path.endswith("/heartbeat"):
            return httpx.Response(200, json={"ttl_s": 300})
        if path.endswith("/m3/stop"):
            return httpx.Response(401, json={"error": "key_revoked"})
        if request.method == "GET":
            return httpx.Response(200, json={"machines": [{"id": "m1", "key": "ops", "size": "default",
                                                           "state": "running", "cost_month_usd": 0.42,
                                                           "starts_month": 3}, {"id": "odd"}],
                                             "sizes": SIZES + [{"name": "bad", "idle_stop_s": 1.5}], "caps": {"n": 3}})
        return httpx.Response(200, json={"id": "m9", "key": "ops", "size": "small", "state": "starting"})
    monkeypatch.setattr(CM, "TRANSPORT", httpx.MockTransport(answer))
    hq = CM.HQ("tgw-synthetic-fixture-key-0042", "https://gateway.acme.example/v1/gateway")
    listed = hq.list()
    assert [m["id"] for m in listed["machines"]] == ["m1"] and listed["machines"][0]["cost_month_usd"] == 0.42
    assert len(listed["sizes"]) == 4 and listed["caps"] == {"n": 3}
    assert hq.create("ops", "small", "code", "https://hub.acme.example")["id"] == "m9"
    assert seen[-1][:3] == ("POST", "https://gateway.acme.example/v1/gateway/machines",
                            {"key": "ops", "size": "small", "enroll_token": "code", "hub_url": "https://hub.acme.example"})
    assert seen[-1][3] == "Bearer tgw-synthetic-fixture-key-0042"
    with pytest.raises(CM.Refused) as refused:
        hq.start("m1")
    assert (refused.value.status, refused.value.code) == (402, "frozen") and seen[-1][2] == {}
    with pytest.raises(CM.Refused) as refused:
        hq.start("m2", "large")
    assert (refused.value.status, refused.value.code) == (409, "sandbox_stopping") and seen[-1][2] == {"size": "large"}
    assert hq.heartbeat("m1") == 300
    with pytest.raises(CM.Refused) as refused:
        hq.stop("m3")
    assert refused.value.code == "key_refused"


# ----------------------------------------------------------------------------- the stop's fence against the claim
def leased(cloud, rid):
    with cloud.store.read() as c:
        return [r["bot_id"] for r in c.execute("SELECT bot_id FROM attempts WHERE runner_id=? AND state IN "
                                            "('leased','running')", (rid,))]


def claim_on(cloud, computer, bot):
    """A claim through the real route, as the machine's runner does it."""
    return post(cloud.api, "jobs/claim", {"bot": bot, "start_places": True}, token=computer["token"])


def going_idle(cloud, key, computer, bots):
    """The machine's runner ready for its bots, nothing queued, and the clock at its idle stop: `decide` says stop."""
    ready(cloud.api, computer, bots)
    idle(cloud)
    cloud.manager.synced = None
    cloud.tick()                                                # learns the sizes: default stops after 300 s idle
    cloud.at[0] = cloud.row(key)["active_at"] + 300
    cloud.hq.calls.clear()


def test_a_claim_after_the_stop_is_reserved_gets_nothing_from_any_member_until_it_starts_again(cloud, monkeypatch):
    """The reviewer's probe, stop first: decide idle, reserve, then a member's claim (the real route) before HQ's
    answer. The claim is refused with a pause, so no turn is leased when the stop goes out; after the stop, HQ's
    stopped keeps refusing it; once started again its runner takes the work."""
    rid = shared_enrolled(cloud, "ops", "finance")
    # The same code again is the same runner (a boot that lost the answer): here, its token.
    computer = post(cloud.api, "runners/enroll", {"code": next(c[3] for c in cloud.hq.calls if c[0] == "create"),
                                                  "label": "boat-vm", "platform": "linux"})
    assert computer["runner_id"] == rid
    going_idle(cloud, CM.SHARED, computer, ["ops", "finance"])
    answers, stop = [], cloud.hq.stop

    def stopping(machine_id):
        work(cloud, "finance")                                  # new work, and its runner asks for it at once
        answers.append(claim_on(cloud, computer, "finance"))
        assert leased(cloud, rid) == []                         # nothing is out when HQ is told to stop
        return stop(machine_id)
    monkeypatch.setattr(cloud.hq, "stop", stopping)
    cloud.tick()
    assert cloud.hq.actions() == ["stop"] and len(answers) == 1
    assert answers[0]["attempt"] is None and "stopping" in answers[0]["paused"]
    assert cloud.row(CM.SHARED)["state"] == "stopped" and cloud.row(CM.SHARED)["stop_until"] is None
    assert claim_on(cloud, computer, "finance")["attempt"] is None      # stopped: still nothing
    monkeypatch.setattr(cloud.hq, "stop", stop)
    cloud.hq.calls.clear()
    assert cloud.tick(10) == ["start"]                          # finance's new work wakes it
    assert claim_on(cloud, computer, "finance")["attempt"]["bot"] == "finance"


def test_a_claim_between_decide_and_the_stop_wins_and_no_stop_is_sent(cloud, monkeypatch):
    """The reviewer's probe, claim first: decide idle on its snapshot, then work is queued and leased through the real
    claim before the stop is reserved. The reservation sees the turn out and sends nothing; the machine is kept."""
    computer = enrolled(cloud)
    going_idle(cloud, "ops", computer, ["ops"])
    decide, raced = CM.decide, []

    def racing(current, found, at, idle_s):
        action = decide(current, found, at, idle_s)
        if action[0] == "stop" and not raced:                   # between the snapshot's decide and the stop
            raced.append(True)
            work(cloud)
            raced.append(claim_on(cloud, computer, "ops"))
        return action
    monkeypatch.setattr(CM, "decide", racing)
    cloud.tick()
    assert raced[1]["attempt"]["bot"] == "ops" and leased(cloud, computer["runner_id"]) == ["ops"]
    assert "stop" not in cloud.hq.actions() and cloud.row()["stop_until"] is None
    monkeypatch.setattr(CM, "decide", decide)
    assert "stop" not in cloud.tick(600)                        # a turn out never stops it


def test_a_reservation_whose_server_died_lapses_and_its_work_goes_out(cloud):
    computer = enrolled(cloud)
    going_idle(cloud, "ops", computer, ["ops"])
    with cloud.store.transaction() as c:                        # reserved, then the process died before HQ was asked
        assert CM.reserve(c, cloud.store.settings, "ops", "stop", cloud.row()["machine_id"])
    work(cloud)
    assert claim_on(cloud, computer, "ops")["attempt"] is None
    cloud.at[0] += CM.STOP_HOLD
    assert claim_on(cloud, computer, "ops")["attempt"]["bot"] == "ops"
    # The next look keeps it on for that turn rather than stopping it under the turn.
    assert "stop" not in cloud.tick(600)


def test_hq_refusing_the_stop_lets_the_claim_go_on_at_once_and_backs_off(cloud):
    computer = enrolled(cloud)
    going_idle(cloud, "ops", computer, ["ops"])
    cloud.hq.fail["stop"] = CM.Refused("forbidden", 403)       # HQ said it did not stop it
    cloud.tick()
    assert cloud.hq.actions() == ["stop"] and cloud.row()["stop_until"] is None
    assert cloud.row()["failures"] == 1 and cloud.row()["next_try_at"] == cloud.at[0] + CM.BACKOFF
    work(cloud)
    assert claim_on(cloud, computer, "ops")["attempt"]["bot"] == "ops"


def test_a_computer_that_is_no_cloud_machine_is_never_held(api):  # noqa: F811
    computer = runner(api)
    with api.app.state.store.read() as c:
        assert CM.held(c, computer["runner_id"]) == ""


@pytest.mark.parametrize("refused", [CM.Refused("unreachable"), CM.Refused("bad_answer"), CM.Refused("http_502", 502),
                                     CM.Refused("sandbox_stopping", 409)])
def test_a_stop_whose_outcome_is_unknown_keeps_the_fence_until_hqs_list_says(cloud, refused):
    """A timeout, an answer this server could not read, HQ's own failure or "already stopping" do not say the
    machine is still up: the reservation holds on from now, and the next look asks HQ's list. Stopping there, the
    state holds the claim; running there, the reservation lapses and the claim goes on."""
    computer = enrolled(cloud)
    going_idle(cloud, "ops", computer, ["ops"])
    cloud.hq.fail["stop"] = refused
    stop = cloud.hq.stop

    def slow(machine_id):
        cloud.at[0] += 30                                       # the call took a while before it gave up
        return stop(machine_id)
    cloud.hq.stop = slow
    cloud.tick()
    assert cloud.hq.actions() == ["stop"] and cloud.row()["stop_until"] == cloud.at[0] + CM.STOP_HOLD
    work(cloud)
    assert claim_on(cloud, computer, "ops")["attempt"] is None
    assert cloud.manager.synced is None                         # the next look lists first
    cloud.hq.machines["m1"]["state"] = "stopping"               # HQ did stop it
    cloud.hq.fail["start"] = CM.Refused("sandbox_stopping", 409)        # as HQ answers a resume meanwhile
    cloud.hq.calls.clear()
    cloud.tick(1)
    assert cloud.hq.calls[0] == ("list",) and cloud.row()["state"] == "stopping"
    cloud.at[0] += CM.STOP_HOLD
    assert claim_on(cloud, computer, "ops")["attempt"] is None  # past the reservation, HQ's stopping holds it
    cloud.hq.machines["m1"]["state"] = "running"                # had it not, its list says running
    cloud.manager.sync(cloud.hq, cloud.at[0])
    assert claim_on(cloud, computer, "ops")["attempt"]["bot"] == "ops"


def test_a_slow_look_still_reserves_the_stop_for_its_full_hold(cloud, monkeypatch):
    """The look's clock is read before HQ's list and the other machines' calls: if they take longer than STOP_HOLD,
    a reservation timed from it would be over before it was written. It is timed from the clock under the lock."""
    computer = enrolled(cloud)
    going_idle(cloud, "ops", computer, ["ops"])
    listed, stop, answers = cloud.hq.list, cloud.hq.stop, []

    def slow_list():
        cloud.at[0] += CM.STOP_HOLD + 60                        # HQ's list, or the machines before this one, took long
        return listed()

    def stopping(machine_id):
        work(cloud, text="Another thing")
        answers.append(claim_on(cloud, computer, "ops"))
        assert leased(cloud, computer["runner_id"]) == []
        return stop(machine_id)
    monkeypatch.setattr(cloud.hq, "list", slow_list)
    monkeypatch.setattr(cloud.hq, "stop", stopping)
    cloud.manager.synced = None
    cloud.tick()
    assert cloud.hq.actions() == ["stop"] and answers[0]["attempt"] is None


# ----------------------------------------------------------------------------- waking a computer behind the server
def reported(cloud, computer, release="", update=None, target=""):
    """The computer's heartbeat as its runner sends it: its release (none when blank) and how its self-update stands."""
    body = {"version": "test", "platform": "test", "release": release, "kind": "linux"}
    if update:
        body["update"] = {"state": update, "target": target}
    return post(cloud.api, "runners/heartbeat", body, token=computer["token"])


def stopped_when_idle(cloud, key="ops"):
    """Nothing queued for the machine and its clock at the break-even idle time: it is stopped."""
    idle(cloud)
    cloud.hq.calls.clear()
    cloud.at[0] = cloud.row(key)["active_at"] + 300
    cloud.tick()
    assert cloud.hq.actions() == ["stop"] and cloud.row(key)["state"] == "stopped"
    cloud.hq.calls.clear()


def draining(cloud, bot, value):
    with cloud.store.transaction() as c:
        c.execute("INSERT INTO bot_control(bot,draining) VALUES(?,?) ON CONFLICT(bot) DO UPDATE SET "
                  "draining=excluded.draining", (bot, value))


def test_a_stopped_machine_behind_its_server_starts_once_to_update_then_idles(cloud, monkeypatch):
    monkeypatch.setenv("TICO_VERSION", "0.5.21")
    computer = enrolled(cloud)
    reported(cloud, computer, "0.5.20")
    stopped_when_idle(cloud)
    assert cloud.tick(10) == ["start"] and cloud.hq.calls[-1] == ("start", "m1", None)
    assert cloud.row()["wake_key"] == "update:0.5.21"
    cloud.hq.calls.clear()
    cloud.at[0] = cloud.row()["active_at"] + 300                # woken for the update, and its runner never reports it
    cloud.tick()
    assert cloud.hq.actions() == ["stop"]
    cloud.hq.calls.clear()
    assert cloud.tick(10) == [] and cloud.tick(600) == [] and cloud.row()["problem"] == ""
    monkeypatch.setenv("TICO_VERSION", "0.5.22")                # a new server release is a new wake
    assert cloud.tick(10) == ["start"]


@pytest.mark.parametrize("server, release", [("0.5.21", "0.5.21"), ("0.5.21", "0.5.22"), ("0.5.21", "dev"),
                                             ("0.5.21", ""), ("", "0.5.20")])
def test_a_stopped_machine_is_not_woken_for_a_release_its_computer_is_not_behind(cloud, monkeypatch, server, release):
    """The same release, a newer one, a dev or blank one, and a development server: nothing wakes."""
    monkeypatch.setenv("TICO_VERSION", server)
    computer = enrolled(cloud)
    reported(cloud, computer, release)
    stopped_when_idle(cloud)
    assert cloud.tick(600) == [] and cloud.row()["state"] == "stopped" and cloud.row()["problem"] == ""


@pytest.mark.parametrize("update", ["pinned", "blocked"])
def test_a_computer_that_will_not_follow_the_server_is_not_woken_to_update(cloud, monkeypatch, update):
    monkeypatch.setenv("TICO_VERSION", "0.5.21")
    computer = enrolled(cloud)
    reported(cloud, computer, "0.5.20", update=update)
    stopped_when_idle(cloud)
    assert cloud.tick(600) == [] and cloud.row()["state"] == "stopped"


def test_a_blocked_update_wakes_nothing_for_its_release_but_once_for_a_newer_one(cloud, monkeypatch):
    """A block (say, changed files in the checkout) can clear by itself; the release after it gets its one wake."""
    monkeypatch.setenv("TICO_VERSION", "0.5.23")
    computer = enrolled(cloud)
    reported(cloud, computer, "0.5.21", update="blocked", target="0.5.23")
    stopped_when_idle(cloud)
    assert cloud.tick(600) == [] and cloud.row()["state"] == "stopped" and cloud.row()["problem"] == ""
    monkeypatch.setenv("TICO_VERSION", "0.5.30")
    assert cloud.tick(10) == ["start"] and cloud.row()["wake_key"] == "update:0.5.30"


@pytest.mark.parametrize("update", ["failed", "rolled_back"])
def test_an_update_that_failed_for_this_release_wakes_nothing_until_a_newer_one(cloud, monkeypatch, update):
    """Its runner waits hours before it tries the same release again: a wake now would spend this release's one."""
    monkeypatch.setenv("TICO_VERSION", "0.5.21")
    computer = enrolled(cloud)
    reported(cloud, computer, "0.5.20", update=update, target="0.5.21")
    stopped_when_idle(cloud)
    assert cloud.tick(600) == [] and cloud.row()["state"] == "stopped" and cloud.row()["problem"] == ""
    monkeypatch.setenv("TICO_VERSION", "0.5.22")
    assert cloud.tick(10) == ["start"] and cloud.row()["wake_key"] == "update:0.5.22"


def test_out_of_credit_starts_nothing_to_update_until_open_again(cloud, monkeypatch):
    monkeypatch.setenv("TICO_VERSION", "0.5.21")
    computer = enrolled(cloud)
    reported(cloud, computer, "0.5.20")
    stopped_when_idle(cloud)
    cloud_access.apply(cloud.store, cloud_access.parse(signals("frozen", "out_of_credit")))
    assert cloud.tick(10) == [] and cloud.row()["problem"] == "out_of_credit"
    cloud_access.apply(cloud.store, cloud_access.parse(signals("open")))
    assert cloud.tick(10) == ["start"]


def test_a_shared_machine_behind_its_server_wakes_for_an_active_member_only(cloud, monkeypatch):
    monkeypatch.setenv("TICO_VERSION", "0.5.21")
    for bot in ("ops", "finance"):
        adopt(cloud, bot, "shared")
    work(cloud, "ops")
    assert cloud.tick() == ["create"]
    computer = post(cloud.api, "runners/enroll", {"code": cloud.hq.calls[-1][3], "label": "boat-vm",
                                                  "platform": "linux"})
    reported(cloud, computer, "0.5.20")
    draining(cloud, "finance", 1)                               # paused: ops alone is active
    stopped_when_idle(cloud, CM.SHARED)
    assert cloud.tick(10) == ["start"]
    cloud.hq.calls.clear()
    draining(cloud, "ops", 1)                                   # now no member is active: it stops and stays stopped
    cloud.tick(10)
    assert cloud.hq.actions() == ["stop"]
    cloud.hq.calls.clear()
    assert cloud.tick(600) == [] and cloud.row(CM.SHARED)["state"] == "stopped"


def test_a_runner_still_updating_keeps_its_machine_up_past_the_idle_time(cloud, monkeypatch):
    monkeypatch.setenv("TICO_VERSION", "0.5.21")
    computer = enrolled(cloud)
    idle(cloud)
    reported(cloud, computer, "0.5.20", update="updating")
    cloud.hq.calls.clear()
    cloud.at[0] = cloud.row()["active_at"] + 300
    cloud.tick()
    assert cloud.hq.actions() == ["heartbeat"]                  # its update is under way: not stopped mid-update
    cloud.at[0] = cloud.row()["active_at"] + CM.UPDATE_STALE + 60   # no report since: a dead runner's last word
    cloud.tick()
    assert cloud.hq.actions()[-1] == "stop"


def test_decide_holds_every_stop_but_out_of_credit_while_its_runner_is_updating():
    """Paused, drained, moved or no bot left: the machine stops, but not under an update still under way."""
    on = machine_row(state="running", active_at=1000, heartbeat_at=1000)
    assert CM.decide(on, facts(active=False), 1100, 300) == ("stop", None)
    assert CM.decide(on, facts(active=False, updating=True), 1100, 300) == ("heartbeat", None)
    assert CM.decide({**on, "heartbeat_at": 1090}, facts(active=False, updating=True), 1100, 300) == (None, None)
    # Out of credit HQ stops it itself: holding it would only spend credit there is none of.
    assert CM.decide(on, facts(frozen=True, updating=True), 1100, 300) == ("stop", "out_of_credit")


def test_a_drained_bot_does_not_get_its_machine_stopped_mid_update(cloud, monkeypatch):
    monkeypatch.setenv("TICO_VERSION", "0.5.21")
    computer = enrolled(cloud)
    idle(cloud)
    reported(cloud, computer, "0.5.20", update="updating")
    draining(cloud, "ops", 1)
    cloud.hq.calls.clear()
    cloud.tick(60)
    assert cloud.hq.actions() == ["heartbeat"]                  # drained, but its runner is mid-update
    cloud.at[0] += CM.UPDATE_STALE                              # no report since: a dead runner's last word
    cloud.tick()
    assert cloud.hq.actions()[-1] == "stop"


def ran_since(cloud, release, at):
    """This server first ran `release` at `at` (epoch seconds), as releases.record_start writes it at startup."""
    from datetime import datetime, timezone
    since = datetime.fromtimestamp(at, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    with cloud.store.transaction() as c:
        c.execute("INSERT OR REPLACE INTO registry_metadata VALUES(?,?)",
                  ("release-history", json.dumps([{"version": "0.5.20", "since": "2026-01-01T00:00:00Z"},
                                                  {"version": release, "since": since}])))


def test_update_wakes_are_spread_per_computer_and_release_and_never_reshuffled():
    offsets = {CM.spread(f"r{i}", "0.5.21") for i in range(200)}
    assert all(0 <= o < CM.UPDATE_SPREAD_S for o in offsets) and len(offsets) > 100
    assert CM.spread("r1", "0.5.21") == CM.spread("r1", "0.5.21")              # a restart moves nothing
    assert any(CM.spread(f"r{i}", "0.5.21") != CM.spread(f"r{i}", "0.5.22") for i in range(5))


def test_an_update_wake_waits_for_its_spread_after_the_release_and_work_does_not(cloud, monkeypatch):
    monkeypatch.setenv("TICO_VERSION", "0.5.21")
    computer = enrolled(cloud)
    reported(cloud, computer, "0.5.20")
    stopped_when_idle(cloud)
    cloud.at[0] = float(int(cloud.at[0]))
    offset = CM.spread(computer["runner_id"], "0.5.21")
    ran_since(cloud, "0.5.21", cloud.at[0] - offset + 30)      # its update wake is due 30 seconds from now
    assert cloud.tick(10) == [] and cloud.tick(10) == []
    assert cloud.tick(15) == ["start"] and cloud.row()["wake_key"] == "update:0.5.21"
    # Work during the spread wakes it at once, as ever; its runner updates once up (it follows this server).
    stopped_when_idle(cloud)
    ran_since(cloud, "0.5.22", cloud.at[0] - CM.spread(computer["runner_id"], "0.5.22") + 300)
    monkeypatch.setenv("TICO_VERSION", "0.5.22")
    assert cloud.tick(10) == []
    work(cloud)
    assert cloud.tick(10) == ["start"] and cloud.row()["wake_key"].startswith("job:")


def test_owners_and_admins_delete_a_broken_shared_machine_and_the_next_work_makes_a_new_one(cloud):
    old = shared_enrolled(cloud, "ops", "finance")
    post(cloud.api, "cloud-machines/shared", {"size": "large"}, token="ben-test")
    machine = cloud.row(CM.SHARED)["machine_id"]
    url = "/api/v2/cloud-machines/shared"
    assert cloud.api.delete(url, headers=headers("cara-test")).status_code == 403
    cloud.hq.fail["delete"] = [CM.Refused("unreachable")]
    assert cloud.api.delete(url, headers=headers("ben-test")).status_code == 502
    assert cloud.row(CM.SHARED)["machine_id"] == machine                # HQ first: nothing changed, try again
    pending = []                                                        # HQ keeps it live until boat confirms
    cloud.hq.delete = lambda machine_id: (cloud.hq.calls.append(("delete", machine_id)), pending.append(machine_id))
    delete = cloud.api.delete(url, headers=headers("ben-test"))
    assert delete.status_code == 200, delete.text
    assert pending == [machine]
    shared = cloud.row(CM.SHARED)
    assert (shared["machine_id"], shared["runner_id"], shared["state"], shared["size"]) == (
        machine, None, "deleted", "large")
    with cloud.store.read() as c:
        assert c.execute("SELECT revoked_at FROM runners WHERE id=?", (old,)).fetchone()["revoked_at"]
    view = get(cloud.api, "cloud-machines")["shared"]
    assert view["bots"] == ["finance", "ops"] and view["state"] == "deleted" and view["runner_id"] is None
    assert cloud.api.delete(url, headers=headers("ben-test")).status_code == 404   # nothing left: HQ not asked
    assert pending == [machine]
    # A member's next work asks for a fresh one: HQ answers with the doomed one until boat confirms, which is no new
    # machine; then it makes one at the company's size, and its members go onto it.
    cloud.hq.calls.clear()
    work(cloud, "finance")
    assert cloud.tick(10) == ["create"] and cloud.row(CM.SHARED)["machine_id"] == machine
    assert cloud.row(CM.SHARED)["state"] == "deleted" and not cloud.row(CM.SHARED)["enroll_by"]
    cloud.hq.machines[machine]["state"] = "deleted"                     # boat confirmed
    cloud.hq.calls.clear()
    assert cloud.tick(CM.BACKOFF) == ["create"] and cloud.hq.calls[-1][1:3] == (CM.SHARED, "large")
    assert cloud.row(CM.SHARED)["machine_id"] not in (None, machine)
    new = post(cloud.api, "runners/enroll", {"code": cloud.hq.calls[-1][3], "label": "boat-vm",
                                             "platform": "linux"})["runner_id"]
    assert placed(cloud)["ops"] == new and placed(cloud)["finance"] == new

def test_decide_stops_a_paused_machine_out_of_credit_whatever_its_runner_does():
    on = machine_row(state="running", active_at=1000, heartbeat_at=1000)
    for busy in ({"updating": True}, {"turns": True}, {}):
        assert CM.decide(on, facts(active=False, frozen=True, **busy), 1100, 300) == ("stop", "out_of_credit")
    assert CM.decide(machine_row(), facts(active=False, frozen=True), 1100, 300) == (None, "out_of_credit")
