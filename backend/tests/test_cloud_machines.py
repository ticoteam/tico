"""Cloud machines (backend/cloud_machines.py): on a Tico Cloud company a bot with no computer runs on a cloud machine,
the company's shared one or, for a developer or the video producer, its own. A machine is made on first work with a
one-time enrollment code and bound to the bots set to run there only; it is started for work, kept with a heartbeat,
paused (stopped) after its Pause after minutes idle (30 unless a person sets another) unless Always on (the shared one
once every member is idle), never started out of credit, and retried with backoff when HQ refuses. A size change applies at the next start. A stopped
machine whose computer is behind this server's release is started once to update, then stops as usual. Off cloud
nothing changes. HQ is a fake client and the clock is a fake one."""

import json
import time
from types import SimpleNamespace

import httpx
import pytest

from backend import cloud_access, cloud_machines as CM, placement, readiness
from backend.store import H, Problem
from backend.tests.test_api import api, assign, get, headers, post, ready, runner  # noqa: F401
from backend.tests.test_cloud_access import signals
from backend.tests.test_credentials import setup
from backend.tests.test_model_subscriptions import OTHER, TOKEN, add, codex_file, company, give, on
from backend import model_subscriptions as MS
from backend.auth import Identity
from backend.names import SUPPORT_ACTOR

SIZES = [{"name": name, "cpu": cpu, "memory_gb": cpu * 2, "usd_per_hour": 0.05 * cpu, "start_usd": 0.04,
          "idle_stop_s": idle} for name, cpu, idle in (("small", 1, 600), ("default", 2, 300), ("large", 4, 180),
                                                      ("xlarge", 8, 120))]


class FakeHQ:
    """The machines routes as Python calls. `fail[action]` is a Refused to raise (once each), or a list of them."""

    def __init__(self):
        self.calls, self.fail, self.machines, self.n = [], {}, {}, 0
        self.clock = None               # a test's fake clock (an ISO time): start and stop then stamp as HQ does

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
        changes = {"state": "starting", "size": size or machine["size"], "starts_month": machine["starts_month"] + 1}
        if self.clock:
            changes["started_at"] = self.clock()
        return self._machine(machine_id, **changes)

    def stop(self, machine_id):
        self.calls.append(("stop", machine_id))
        self._check("stop")
        return self._machine(machine_id, state="stopped", stopped_at=self.clock() if self.clock else H.now())

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
    # These tests time a pause in seconds: 5 minutes (the least a person can set) keeps them at 300 s. The 30-minute
    # default is tested on its own, in test_the_default_pause_is_30_minutes_of_idle_and_not_before.
    monkeypatch.setattr(CM, "PAUSE_MINUTES", 5)

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
            "enroll_fails": 0, "pause_minutes": None, **kw}


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


def test_decide_idle_stop_after_the_pause_heartbeat_and_always_on():
    on = machine_row(state="running", active_at=1000, heartbeat_at=1000)
    assert CM.decide(on, facts(), 1059, 300) == (None, None)
    assert CM.decide(on, facts(), 1060, 300) == ("heartbeat", None)
    assert CM.decide(on, facts(), 1299, 300) == ("heartbeat", None)
    assert CM.decide(on, facts(), 1300, 300) == ("stop", None)            # exactly the pause
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


def test_the_pause_is_pause_minutes_or_30_and_only_whole_minutes_from_5_to_1440_are_stored():
    assert CM.PAUSE_MINUTES == 30
    assert CM.pause_after(machine_row()) == 1800
    assert CM.pause_after(machine_row(pause_minutes=5)) == 300
    assert CM.pause_after(machine_row(pause_minutes=1440)) == 86400
    assert CM.machine_view(None)["pause_minutes"] == 30
    for ok in (5, 30, 1440):
        assert CM.checked_pause(ok) == ok
    for bad in (None, 4, 1441, 0, -5, 2.5, "30", True):
        with pytest.raises(Problem):
            CM.checked_pause(bad)


def test_a_machine_pauses_after_its_pause_with_no_turn_out_and_always_on_never_does():
    on = machine_row(state="running", active_at=1000, heartbeat_at=1000)
    assert CM.decide(on, facts(), 2799, CM.pause_after(on)) == ("heartbeat", None)     # the default: 30 minutes
    assert CM.decide(on, facts(), 2800, CM.pause_after(on)) == ("stop", None)
    assert CM.decide(on, facts(turns=True), 9999, CM.pause_after(on)) == ("heartbeat", None)
    long = {**on, "pause_minutes": 45}
    assert CM.decide(long, facts(), 3699, CM.pause_after(long)) == ("heartbeat", None)
    assert CM.decide(long, facts(), 3700, CM.pause_after(long)) == ("stop", None)
    always = {**on, "always_on": 1}
    assert CM.decide(always, facts(), 9999, CM.pause_after(always)) == ("heartbeat", None)


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
        computer = dict(c.execute("SELECT * FROM runners_with_state WHERE id=?", (enrolled["runner_id"],)).fetchone())
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
    cloud.tick()                                                # learns the sizes: the pause is 300 s here
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


def test_the_default_pause_is_30_minutes_of_idle_and_not_before(cloud, monkeypatch):
    monkeypatch.setattr(CM, "PAUSE_MINUTES", 30)               # this test is of the real default, not the fixture's
    enrolled(cloud)
    cloud.hq.calls.clear()
    cloud.manager.synced = None
    cloud.tick()
    with cloud.store.transaction() as c:                        # the queued chat is not why it stays on
        c.execute("UPDATE jobs SET state='canceled' WHERE bot_id='ops'")
    cloud.at[0] = cloud.row()["active_at"] + 1799
    cloud.tick()
    assert "stop" not in cloud.hq.actions()
    cloud.tick(1)
    assert cloud.hq.actions()[-1] == "stop" and cloud.row()["state"] == "stopped"


def test_a_custom_pause_is_the_idle_time_the_machine_stops_after(cloud):
    enrolled(cloud)
    post(cloud.api, "bots/ops/cloud-machine", {"pause_minutes": 45})
    cloud.hq.calls.clear()
    cloud.manager.synced = None
    cloud.tick()
    with cloud.store.transaction() as c:                        # the queued chat is not why it stays on
        c.execute("UPDATE jobs SET state='canceled' WHERE bot_id='ops'")
    cloud.at[0] = cloud.row()["active_at"] + 2699
    cloud.tick()
    assert "stop" not in cloud.hq.actions()
    cloud.tick(1)
    assert cloud.hq.actions()[-1] == "stop" and cloud.row()["state"] == "stopped"


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
        # It boots for longer than its pause: never idle-stopped meanwhile.
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


def test_a_look_that_raises_is_named_on_its_row_until_the_next_create_goes_out(cloud, monkeypatch):
    adopt(cloud)
    work(cloud)
    real_mint = CM.mint

    def unowned(c, key, owner=None):
        raise Problem("operator", "Nobody on the roster to own this cloud machine's computer", 409)
    monkeypatch.setattr(CM, "mint", unowned)
    assert cloud.tick(10) == []                                 # the look raised before HQ was asked
    assert cloud.row()["problem"] == "error:operator" and cloud.row()["problem_at"]
    assert employee(cloud.api)["cloud_machine"]["problem"] == "Couldn't be made or started (operator): trying again"
    with cloud.store.read() as c:
        assert "couldn't be made or started (operator)" in CM.waiting(c, "ops")
    monkeypatch.setattr(CM, "mint", real_mint)
    assert cloud.tick(10) == ["create"] and cloud.row()["problem"] == ""


def test_a_look_that_raises_with_nothing_to_do_is_cleared_by_the_next_clean_look(cloud, monkeypatch):
    adopt(cloud)                                                # no work: no machine is wanted
    real_facts = CM.facts

    def broken(*args):
        raise RuntimeError("database is locked")
    monkeypatch.setattr(CM, "facts", broken)
    assert cloud.tick(10) == [] and cloud.row()["problem"] == "error:RuntimeError"
    monkeypatch.setattr(CM, "facts", real_facts)
    assert cloud.tick(10) == [] and cloud.row()["problem"] == ""


def test_a_create_waiting_on_a_refused_list_says_so_on_its_row_and_goes_out_once_listed(cloud):
    adopt(cloud)
    work(cloud)
    cloud.hq.fail["create"] = [CM.Refused("boat_error", 502)]   # refused once: the create after it waits on HQ's list
    assert cloud.tick(10) == ["create"] and cloud.row()["problem"] == "hq:boat_error"
    adopt(cloud, "cpo")
    work(cloud, "cpo")
    listed = cloud.hq.list

    def down():
        raise CM.Refused("unreachable", 0)
    cloud.hq.list = down
    cloud.hq.calls.clear()
    assert cloud.tick(CM.BACKOFF) == [] and cloud.row("cpo")["problem"] == "hq:unreachable"
    cloud.hq.list = listed
    assert cloud.tick(CM.BACKOFF_MAX) == ["create", "create"] and cloud.row("cpo")["problem"] == ""


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


def test_a_same_choice_reposted_keeps_the_start_refusal_and_its_wake_guard(cloud):
    enrolled(cloud)
    cloud.tick(400)
    work(cloud, text="More")
    cloud.hq.fail["start"] = [CM.Refused("starts_full", 429)]
    cloud.hq.calls.clear()
    assert cloud.tick(10) == ["start"] and cloud.row()["problem"] == "hq:starts_full" and cloud.row()["wake_key"]
    refused = cloud.row()
    # Re-posting the choice a bot already has (a script reading the view it returns) changes none of it.
    for body in ({"mode": "cloud"}, {"mode": "cloud", "placement": "own"}):
        post(cloud.api, "bots/ops/cloud-machine", body)
        assert employee(cloud.api)["cloud_machine"]["problem"] == "Didn't start (starts_full): trying again"
        after = cloud.row()
        assert (after["problem"], after["problem_at"], after["wake_key"], after["mode"], after["placement"]) == (
            refused["problem"], refused["problem_at"], refused["wake_key"], "cloud", "own")


def test_a_same_choice_reposted_does_not_wake_a_machine_whose_work_already_stuck(cloud):
    enrolled(cloud)
    cloud.tick(400)                                             # the chat was never claimed there: idle, stopped
    cloud.hq.calls.clear()
    assert cloud.tick(10) == [] and cloud.tick(600) == [] and cloud.row()["problem"] == "stuck"
    post(cloud.api, "bots/ops/cloud-machine", {"mode": "cloud"})
    cloud.hq.calls.clear()
    assert cloud.tick(10) == [] and cloud.tick(600) == [] and cloud.row()["problem"] == "stuck"


def test_a_real_change_of_placement_still_clears_the_refusal_and_its_wake_guard(cloud):
    enrolled(cloud)
    cloud.tick(400)
    work(cloud, text="More")
    cloud.hq.fail["start"] = [CM.Refused("starts_full", 429)]
    cloud.hq.calls.clear()
    assert cloud.tick(10) == ["start"] and cloud.row()["problem"] == "hq:starts_full" and cloud.row()["wake_key"]
    idle(cloud)
    post(cloud.api, "bots/ops/cloud-machine", {"placement": "shared"})
    assert cloud.row()["placement"] == "shared" and cloud.row()["problem"] == "" and cloud.row()["wake_key"] is None
    with cloud.store.transaction() as c:                        # a refusal and a wake guard on its row, moving back
        CM.save(c, "ops", problem="hq:starts_full", problem_at=H.now(), wake_key="job:old")
    post(cloud.api, "bots/ops/cloud-machine", {"placement": "own"})
    assert cloud.row()["placement"] == "own" and cloud.row()["problem"] == "" and cloud.row()["wake_key"] is None


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


def test_pause_after_takes_whole_minutes_from_5_to_1440_and_null_leaves_it(cloud):
    adopt(cloud)
    cloud.tick()
    for bad in (4, 1441, 2.5):
        post(cloud.api, "bots/ops/cloud-machine", {"pause_minutes": bad}, expected=422)
    assert post(cloud.api, "bots/ops/cloud-machine", {"pause_minutes": 1440})["cloud_machine"]["pause_minutes"] == 1440
    assert post(cloud.api, "bots/ops/cloud-machine", {"size": "large"})["cloud_machine"]["pause_minutes"] == 1440
    assert post(cloud.api, "bots/ops/cloud-machine", {"pause_minutes": None})["cloud_machine"]["pause_minutes"] == 1440
    assert post(cloud.api, "bots/ops/cloud-machine", {"pause_minutes": 30})["cloud_machine"]["pause_minutes"] == 30
    with cloud.store.read() as c:
        assert CM.row(c, "ops")["pause_minutes"] == 30


def test_the_shared_machines_pause_is_the_companys_and_checked(cloud):
    adopt(cloud, "ops", "shared")
    post(cloud.api, "cloud-machines/shared", {"pause_minutes": 45}, token="cara-test", expected=403)
    post(cloud.api, "cloud-machines/shared", {"pause_minutes": 4}, token="ben-test", expected=422)
    assert post(cloud.api, "cloud-machines/shared", {"pause_minutes": 45}, token="ben-test")["shared"]["pause_minutes"] == 45
    assert post(cloud.api, "cloud-machines/shared", {"always_on": True}, token="ben-test")["shared"]["pause_minutes"] == 45
    assert post(cloud.api, "cloud-machines/shared", {"pause_minutes": None}, token="ben-test")["shared"]["pause_minutes"] \
        == 45


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
        # coo is a Builder; ops is in Engineering on the team chart, which alone is no reason for its own machine;
        # finance was made from the video producer's template.
        c.execute("UPDATE registry_metadata SET value=json_set(value,'$.org_groups',json(?)) WHERE key='people'",
                  (json.dumps({"engineering": {"name": "Engineering", "parent": ""}}),))
        c.execute("UPDATE bot_config SET config=json_set(config,'$.team','engineering') WHERE bot='ops'")
        c.execute("UPDATE bots SET display_name='Builder (Claude)' WHERE id='coo'")
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


def test_only_a_builder_by_name_gets_its_own_machine_by_default(cloud):
    with cloud.store.transaction() as c:
        for name, want in (("Builder", "own"), ("builder (OpenAI)", "own"), ("Builder (Claude)", "own"),
                           ("Builders guild", "shared"), ("Reviewer 1", "shared"), ("Engineering Manager", "shared"),
                           ("", "shared"), (None, "shared")):
            c.execute("UPDATE bots SET display_name=? WHERE id='ops'", (name,))
            assert CM.default_placement(c, "ops") == want, name


def _config(c, bot, config):
    assert c.execute("SELECT 1 FROM bot_config WHERE bot=?", (bot,)).fetchone(), bot
    c.execute("UPDATE bot_config SET config=? WHERE bot=?", (json.dumps(config), bot))


def test_a_builder_template_a_copy_of_one_or_a_numbered_builder_gets_its_own_machine(cloud):
    with cloud.store.transaction() as c:
        c.execute("UPDATE bots SET display_name=? WHERE id='ops'", ("Ops",))
        _config(c, "ops", {})
        assert CM.default_placement(c, "ops") == "shared"
        _config(c, "ops", {"template": "builder"})
        assert CM.default_placement(c, "ops") == "own"
        _config(c, "ops", {"template": "video-producer"})
        assert CM.default_placement(c, "ops") == "own"
        # A copy is one step from its original: a copy of a Builder is heavy whatever it is called.
        _config(c, "coo", {"template": "builder"})
        _config(c, "ops", {"copied_from": {"bot": "coo", "sha": "0" * 40}})
        assert CM.default_placement(c, "ops") == "own"
        _config(c, "coo", {"template": "pr-reviewer"})
        assert CM.default_placement(c, "ops") == "shared"
        for name, want in (("Builder 2", "own"), ("builder (Claude)", "own"), ("BUILDER", "own"),
                           ("Builder 2 Ops", "shared"), ("Rebuilder", "shared")):
            c.execute("UPDATE bots SET display_name=? WHERE id='ops'", (name,))
            assert CM.default_placement(c, "ops") == want, name


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
        computer = dict(c.execute("SELECT * FROM runners_with_state WHERE id=?", (rid,)).fetchone())
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
    cloud.tick()                                                # learns the sizes: the pause is 300 s here
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


def test_the_shared_machine_pauses_after_the_companys_pause(cloud):
    shared_enrolled(cloud, "ops", "finance")
    post(cloud.api, "cloud-machines/shared", {"pause_minutes": 45})
    idle(cloud)
    cloud.manager.synced = None
    cloud.tick()                                                # learns the sizes
    start = cloud.row(CM.SHARED)["active_at"]
    cloud.at[0] = start + 2699
    cloud.tick()
    assert "stop" not in cloud.hq.actions()
    cloud.at[0] = start + 2700
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


def test_a_lasting_refusal_to_move_is_said_to_the_person_and_nothing_moves(cloud, monkeypatch):
    own = enrolled(cloud)                                       # ops on its own machine
    shared = shared_enrolled(cloud, "finance")                  # the shared machine has a computer
    idle(cloud)
    monkeypatch.setattr("backend.execution.stranded", lambda *args: "ticoteam/bot-ops is not on GitHub yet")
    refused = cloud.api.post("/api/v2/bots/ops/cloud-machine", headers=headers(), json={"placement": "shared"})
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "repository_unpublished", refused.text
    # Nothing half-applied: still its own machine, still on its own computer.
    assert cloud.row()["placement"] == "own" and cloud.row()["mode"] == "cloud"
    assert placed(cloud)["ops"] == own["runner_id"] and shared != own["runner_id"]


def report_repo(api, m, bot, **fields):
    """The computer's report on the bot's checkout (`published`: whether GitHub has its history, as the runner says)."""
    return post(api, "runners/heartbeat", {"version": "test", "platform": "test", "readiness": {
        "schema_version": 1, "runtimes": {}, "bots": {bot: {"ready": True, **fields}}}}, token=m["token"])


def test_a_move_to_a_cloud_machine_not_made_yet_is_refused_before_one_is_billed(cloud):
    m = runner(cloud.api)                                       # ops on an ordinary computer, its history only there
    assign(cloud.api, m, "ops")
    report_repo(cloud.api, m, "ops", published=False)
    refused = cloud.api.post("/api/v2/bots/ops/cloud-machine", headers=headers(),
                             json={"mode": "cloud", "placement": "own"})
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "repository_unpublished", refused.text
    assert "a new cloud machine could not fetch it" in refused.json()["error"]["detail"]
    assert not any(call[0] in ("create", "start") for call in cloud.hq.calls)     # no machine made, nothing billed
    assert (cloud.row() or {}).get("mode", "computer") == "computer"          # the choice was rolled back
    assert placed(cloud)["ops"] == m["runner_id"]


@pytest.mark.parametrize("fields", [{"published": True}, {}])
def test_a_move_to_a_cloud_machine_not_made_yet_is_allowed_when_the_history_is_on_github_or_unreported(cloud, fields):
    m = runner(cloud.api)
    assign(cloud.api, m, "ops")
    report_repo(cloud.api, m, "ops", **fields)
    change_as(cloud, "ops", mode="cloud", placement="own")
    assert cloud.row()["mode"] == "cloud" and cloud.row()["placement"] == "own"
    assert placed(cloud)["ops"] == m["runner_id"]               # it moves when its machine is made


def test_a_look_never_refuses_a_move_to_an_unmade_machine(cloud):
    m = runner(cloud.api)
    assign(cloud.api, m, "ops")
    report_repo(cloud.api, m, "ops", published=False)
    with cloud.store.transaction() as c:
        assert CM.bind(c, cloud.api.app.state.execution, "ops") is False      # no lasting choice: no raise


def test_a_turn_still_out_on_the_old_computer_is_asked_again_at_a_look(cloud):
    own = enrolled(cloud)                                       # ops on its own machine, with its work claimed there
    ready(cloud.api, own, ["ops"])
    assert leased(cloud, own["runner_id"]) == [] and claim_on(cloud, own, "ops")
    assert leased(cloud, own["runner_id"]) == ["ops"]
    shared = shared_enrolled(cloud, "finance")
    out = cloud.api.post("/api/v2/bots/ops/cloud-machine", headers=headers(), json={"placement": "shared"})
    assert out.status_code == 200, out.text
    assert cloud.row()["placement"] == "shared" and placed(cloud)["ops"] == own["runner_id"]
    with cloud.store.transaction() as c:                        # its turn ends; the next look moves it
        c.execute("UPDATE attempts SET lease_until=0 WHERE bot_id='ops'")
    cloud.tick()
    assert placed(cloud)["ops"] == shared


def test_a_size_or_always_on_change_on_a_bot_awaiting_its_move_is_saved_and_says_nothing(cloud, monkeypatch):
    own = enrolled(cloud)
    shared_enrolled(cloud, "finance")
    idle(cloud)
    monkeypatch.setattr("backend.execution.stranded", lambda *args: "ticoteam/bot-ops is not on GitHub yet")
    refused = cloud.api.post("/api/v2/bots/ops/cloud-machine", headers=headers(), json={"placement": "shared"})
    assert refused.status_code == 409, refused.text
    kept = cloud.api.post("/api/v2/bots/ops/cloud-machine", headers=headers(), json={"always_on": True})
    assert kept.status_code == 200, kept.text
    assert cloud.row()["always_on"] and cloud.row()["placement"] == "own"
    assert placed(cloud)["ops"] == own["runner_id"]


def test_a_removing_computer_keeps_the_choice_and_the_move_waits_for_a_look(cloud, monkeypatch):
    own = enrolled(cloud)
    shared = shared_enrolled(cloud, "finance")
    idle(cloud)
    def busy_removing(*args, **kwargs):
        raise Problem("removing", "ops is being removed from the computer it is on")
    execution = cloud.api.app.state.execution
    real_assign = execution.assign
    monkeypatch.setattr(execution, "assign", busy_removing)
    out = cloud.api.post("/api/v2/bots/ops/cloud-machine", headers=headers(), json={"placement": "shared"})
    assert out.status_code == 200, out.text
    assert cloud.row()["placement"] == "shared" and placed(cloud)["ops"] == own["runner_id"]
    monkeypatch.setattr(execution, "assign", real_assign)
    cloud.tick()
    assert placed(cloud)["ops"] == shared


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


# ----------------------------------------------------------------------------- a company plan on a new computer
def plan_company(cloud, *bots, saved=True):
    """A Tico Cloud company (no Gateway choice) with these bots running Claude, and a Claude plan saved unless not."""
    setup(cloud.api)
    company(cloud.api)
    for bot in bots:
        on(cloud.api, bot, "claude", "claude-sonnet-5-5")
    return add(cloud.api)["id"] if saved else None


def change_as(cloud, bot, token="ana-test", **body):
    """A person's choice for the bot's cloud machine, through the real route (the row's changed_by is that person)."""
    return post(cloud.api, f"bots/{bot}/cloud-machine", body, token=token)


def enroll_made(cloud, key):
    """The machine HQ was just asked to make for `key` enrolls with its code; returns the computer's enrollment answer."""
    assert cloud.tick()[-1] == "create" and cloud.hq.calls[-1][1] == key
    return post(cloud.api, "runners/enroll", {"code": cloud.hq.calls[-1][3], "label": "boat-vm", "platform": "linux"})


def own_computer(cloud, bot="ops", token="ana-test"):
    """The bot's own cloud machine made for its work and enrolled; returns the computer's enrollment answer."""
    change_as(cloud, bot, token, mode="cloud", placement="own")
    work(cloud, bot)
    return enroll_made(cloud, bot)


def shared_for(cloud, bot, token="ana-test"):
    """The bot on the shared cloud machine, which is made for its work and enrolled (the first member's)."""
    change_as(cloud, bot, token, mode="cloud", placement="shared")
    work(cloud, bot)
    return enroll_made(cloud, CM.SHARED)


def task_on(api, bot, model, harness):
    """The bot's task model: a model on `harness` (its runtime), beside its own model."""
    with api.app.state.store.transaction() as c:
        config = json.loads(c.execute("SELECT config FROM bot_config WHERE bot=?", (bot,)).fetchone()[0])
        config["task_model"] = {"model": model, "harness": harness, "runtime": harness}
        c.execute("UPDATE bot_config SET config=? WHERE bot=?", (json.dumps(config), bot))


def given(cloud, runner_id):
    with cloud.store.read() as c:
        return {r["runtime"]: r["subscription_id"] for r in c.execute(
            "SELECT runtime,subscription_id FROM computer_subscriptions WHERE runner_id=?", (runner_id,))}


def sign_in_report(cloud, computer, authenticated):
    """The computer's first report: Claude signed in as `authenticated` (a takes-subscriptions runner)."""
    return post(cloud.api, "runners/heartbeat", {
        "version": "test", "platform": "linux", "capabilities": [MS.CAPABILITY],
        "readiness": {"schema_version": 1, "runtimes": {"claude": {"installed": True, "authenticated": authenticated}},
                      "bots": {}}}, token=computer["token"])


def can_run(cloud, bot, runner_id):
    with cloud.store.read() as c:
        return readiness.can_run(c, bot, "claude", runner=runner_id)["can_run"]


def test_a_new_own_machine_takes_the_companys_plan_and_its_bot_is_ready(cloud):
    sid = plan_company(cloud, "ops")
    computer = own_computer(cloud)
    assert given(cloud, computer["runner_id"]) == {"claude": sid}
    assert [e["actor"] for e in events(cloud, "subscription.given")] == ["human:ana"]    # the person who chose it
    sign_in_report(cloud, computer, "missing")               # nobody signs in to a throwaway machine
    assert can_run(cloud, "ops", computer["runner_id"]) is True


def test_a_plan_already_on_the_shared_machine_is_preferred_to_a_newer_one(cloud):
    first = plan_company(cloud, "cpo", "ops")
    shared = shared_for(cloud, "cpo")["runner_id"]           # the shared machine's computer takes the only plan
    assert given(cloud, shared) == {"claude": first}
    add(cloud.api, secret=OTHER, name="Newer Max")           # newer, and no more used: still not the one
    computer = own_computer(cloud)
    assert given(cloud, computer["runner_id"]) == {"claude": first}


def test_a_computer_with_a_plan_or_a_sign_in_of_its_own_keeps_it(cloud):
    plan_company(cloud, "ops", "inbox", saved=False)
    signed = own_computer(cloud, "inbox")                    # made before any plan; its own login is signed in
    sign_in_report(cloud, signed, "ready")
    sid = add(cloud.api)["id"]
    with cloud.store.transaction() as c:
        CM.lend_plans(c, cloud.api.app.state.execution, "inbox", signed["runner_id"], "human:ana")
    assert given(cloud, signed["runner_id"]) == {}           # its own sign-in stays the one it runs on
    first = own_computer(cloud, "ops")                       # made now: the plan is given to it
    assert given(cloud, first["runner_id"]) == {"claude": sid}
    add(cloud.api, secret=OTHER, name="Newer Max")
    with cloud.store.transaction() as c:
        CM.lend_plans(c, cloud.api.app.state.execution, "ops", first["runner_id"], "human:ana")
    assert given(cloud, first["runner_id"]) == {"claude": sid}   # a plan it has is never replaced


def test_with_no_plan_that_fits_nothing_changes_and_the_bot_still_waits(cloud):
    plan_company(cloud, "ops", "inbox")
    with cloud.store.transaction() as c:
        c.execute("DELETE FROM model_subscriptions")         # no plan at all
    computer = own_computer(cloud, "ops")
    assert given(cloud, computer["runner_id"]) == {}
    sid = add(cloud.api, secret=OTHER, name="Broken Max")["id"]
    with cloud.store.transaction() as c:
        c.execute("UPDATE model_subscriptions SET status='needs_reconnect' WHERE id=?", (sid,))
    waiting = own_computer(cloud, "inbox")                   # its only plan needs reconnecting: passed over
    assert given(cloud, waiting["runner_id"]) == {}
    assert events(cloud, "subscription.given") == []
    sign_in_report(cloud, waiting, "missing")
    assert can_run(cloud, "inbox", waiting["runner_id"]) is not True


def test_a_bot_put_on_an_ordinary_computer_gets_no_plan(cloud):
    plan_company(cloud, "ops")
    mac = runner(cloud.api, label="Test Mac")
    assign(cloud.api, mac, "ops")
    assert given(cloud, mac["runner_id"]) == {}
    assert events(cloud, "subscription.given") == []


def test_a_member_moving_a_bot_gets_no_plan_on_a_computer_it_moves_to(cloud):
    plan_company(cloud, "cpo", "ops", saved=False)
    shared = shared_for(cloud, "cpo")["runner_id"]           # the owner's shared machine, with no plan yet
    add(cloud.api)
    with cloud.store.transaction() as c:
        c.execute("UPDATE bot_config SET owner_id='cara' WHERE bot='ops'")
    change_as(cloud, "ops", "cara-test", mode="cloud", placement="shared")
    assert placed(cloud)["ops"] == shared and given(cloud, shared) == {}
    change_as(cloud, "ops", "cara-test", mode="cloud", placement="own")  # a new machine, made and enrolled for her move
    work(cloud, "ops")
    computer = enroll_made(cloud, "ops")
    assert given(cloud, computer["runner_id"]) == {} and events(cloud, "subscription.given") == []


def test_an_admin_moving_a_bot_gets_the_plan_on_its_own_machine(cloud):
    sid = plan_company(cloud, "ops")
    computer = own_computer(cloud, "ops", token="ben-test")
    assert given(cloud, computer["runner_id"]) == {"claude": sid}
    assert [e["actor"] for e in events(cloud, "subscription.given")] == ["human:ben"]


def test_a_bot_that_chose_its_machine_gets_no_plan(cloud):
    plan_company(cloud, "ops")
    change_as(cloud, "ops", mode="cloud", placement="own")
    with cloud.store.transaction() as c:
        c.execute("UPDATE cloud_machines SET changed_by='bot:cpo' WHERE bot='ops'")
    work(cloud, "ops")
    assert given(cloud, enroll_made(cloud, "ops")["runner_id"]) == {}


def test_a_plan_an_owner_took_off_a_computer_is_not_brought_back_by_a_later_change(cloud):
    sid = plan_company(cloud, "cpo", "finance")
    shared = shared_for(cloud, "cpo")["runner_id"]
    assert given(cloud, shared) == {"claude": sid}
    give(cloud.api, {"runner_id": shared}, "claude", None)
    assert given(cloud, shared) == {}
    change_as(cloud, "cpo", size="large")                    # a size change on the bot already placed there
    change_as(cloud, "finance", mode="cloud", placement="shared")   # a second member joins the same computer
    assert placed(cloud)["finance"] == shared and given(cloud, shared) == {}


def test_a_claude_bot_with_a_codex_task_model_gets_the_codex_plan_too(cloud):
    sid = plan_company(cloud, "ops")
    csid = add(cloud.api, "codex_login", codex_file(), name="ChatGPT")["id"]
    task_on(cloud.api, "ops", "gpt-6.1-sol", "codex")
    computer = own_computer(cloud, "ops")
    assert given(cloud, computer["runner_id"]) == {"claude": sid, "codex": csid}


def generation(cloud, bot):
    with cloud.store.read() as c:
        return c.execute("SELECT generation FROM assignments WHERE bot=?", (bot,)).fetchone()["generation"]


def test_a_same_computer_reassignment_by_a_member_or_support_after_a_plan_is_removed_gives_nothing(cloud):
    sid = plan_company(cloud, "ops")
    computer = own_computer(cloud, "ops", token="ben-test")  # an admin chose the machine, and its plan was given
    assert given(cloud, computer["runner_id"]) == {"claude": sid}
    give(cloud.api, computer, "claude", None)
    with cloud.store.transaction() as c:
        c.execute("UPDATE bot_config SET owner_id='cara' WHERE bot='ops'")
    idle(cloud)
    assign(cloud.api, computer, "ops", generation(cloud, "ops"), operator="cara-test")
    assert given(cloud, computer["runner_id"]) == {}
    # Support's session is checked by the route; assign itself is what decides, so it is called with support's identity.
    before = generation(cloud, "ops")
    with cloud.store.transaction() as c:
        cloud.api.app.state.execution.assign(c, Identity(SUPPORT_ACTOR, "owner", support="full"), "ops",
                                             SimpleNamespace(runner_id=computer["runner_id"],
                                                             expected_generation=before))
    assert generation(cloud, "ops") == before + 1 and placed(cloud)["ops"] == computer["runner_id"]
    assert given(cloud, computer["runner_id"]) == {}


def test_a_member_moving_its_bot_off_and_back_through_the_assignment_route_gives_no_plan(cloud):
    plan_company(cloud, "ops")
    own = own_computer(cloud, "ops", token="ben-test")        # an admin chose the machine, and it had the plan
    give(cloud.api, own, "claude", None)
    other = runner(cloud.api, label="Office Mac")
    with cloud.store.transaction() as c:
        c.execute("UPDATE bot_config SET owner_id='cara' WHERE bot='ops'")
        c.execute("UPDATE runners SET accepts_member_bots=1 WHERE id=?", (other["runner_id"],))
    idle(cloud)
    assign(cloud.api, other, "ops", generation(cloud, "ops"), operator="cara-test")
    assert placed(cloud)["ops"] == other["runner_id"] and given(cloud, other["runner_id"]) == {}
    assign(cloud.api, own, "ops", generation(cloud, "ops"), operator="cara-test")
    assert placed(cloud)["ops"] == own["runner_id"] and given(cloud, own["runner_id"]) == {}


def test_only_an_admins_choice_of_the_machine_lends_a_direct_assignment_never_does(cloud):
    sid = plan_company(cloud, "ops")
    own = own_computer(cloud, "ops", token="ben-test")
    give(cloud.api, own, "claude", None)
    other = runner(cloud.api, label="Office Mac")
    idle(cloud)
    assign(cloud.api, other, "ops", generation(cloud, "ops"), operator="ben-test")
    assign(cloud.api, own, "ops", generation(cloud, "ops"), operator="ben-test")
    assert given(cloud, own["runner_id"]) == {}                 # assignment, even an admin's, gives no plan
    assign(cloud.api, other, "ops", generation(cloud, "ops"), operator="ben-test")
    chose = cloud.api.post("/api/v2/bots/ops/cloud-machine", headers=headers("ben-test"), json={"mode": "cloud"})
    assert chose.status_code == 200, chose.text
    assert given(cloud, own["runner_id"]) == {"claude": sid}
    assert events(cloud, "subscription.given")[-1]["actor"] == "human:ben"


def test_two_bots_chosen_before_a_new_shared_machine_enrolls_still_get_its_plan(cloud):
    sid = plan_company(cloud, "ops", "finance")
    for bot in ("ops", "finance"):
        change_as(cloud, bot, mode="cloud", placement="shared")
    work(cloud, "ops")
    assert cloud.tick()[-1] == "create" and cloud.hq.calls[-1][1] == CM.SHARED
    made = post(cloud.api, "runners/enroll", {"code": cloud.hq.calls[-1][3], "label": "boat-vm", "platform": "linux"})
    assert given(cloud, made["runner_id"]) == {"claude": sid}


def test_a_look_never_gives_a_plan_back_after_a_bots_assignment_goes(cloud):
    plan_company(cloud, "ops")
    own = own_computer(cloud, "ops", token="ben-test")
    give(cloud.api, own, "claude", None)
    idle(cloud)
    with cloud.store.transaction() as c:                        # archiving a bot drops its assignment, keeps its row
        c.execute("DELETE FROM assignments WHERE bot='ops'")
    cloud.tick()
    assert placed(cloud)["ops"] == own["runner_id"] and given(cloud, own["runner_id"]) == {}


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
    cloud.tick()                                                # learns the sizes: the pause is 300 s here
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
    """Nothing queued for the machine and its clock at its pause: it is stopped."""
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



def codex_heartbeat(cloud, computer):
    """The computer reports Claude and Codex installed and neither signed in: a throwaway machine nobody signs in to."""
    post(cloud.api, "runners/heartbeat", {
        "version": "test", "platform": "linux", "capabilities": [MS.CAPABILITY],
        "readiness": {"schema_version": 1, "runtimes": {
            "claude": {"installed": True, "authenticated": "missing"},
            "codex": {"installed": True, "authenticated": "missing"}}, "bots": {}}}, token=computer["token"])


def test_a_codex_bot_moved_onto_a_running_shared_machine_takes_the_chatgpt_plan(cloud):
    plan_company(cloud, "cpo")
    shared = shared_for(cloud, "cpo", token="ben-test")
    codex_heartbeat(cloud, shared)
    csid = add(cloud.api, "codex_login", codex_file(), name="ChatGPT")["id"]
    on(cloud.api, "ops", "codex", "gpt-6.1-sol")
    box = runner(cloud.api, label="Box")
    assign(cloud.api, box, "ops")
    idle(cloud)
    change_as(cloud, "ops", "ben-test", mode="cloud", placement="shared")
    assert placed(cloud)["ops"] == shared["runner_id"]
    assert given(cloud, shared["runner_id"]) == {"claude": given(cloud, shared["runner_id"])["claude"], "codex": csid}


def health_of(cloud):
    body = cloud.api.get("/api/v2/health", headers=headers("ana-test")).json()
    return {check["id"]: check for check in body["checks"]}


def issue_kinds(cloud):
    return {i["kind"] for i in cloud.api.get("/api/v2/health/issues", headers=headers("ana-test")).json()["issues"]}


def test_a_plan_on_the_computer_is_not_a_missing_sign_in_on_health(cloud):
    plan_company(cloud, "cpo")
    shared = shared_for(cloud, "cpo")
    codex_heartbeat(cloud, shared)
    on(cloud.api, "ops", "codex", "gpt-6.1-sol")
    change_as(cloud, "ops", mode="cloud", placement="shared")
    checks = health_of(cloud)
    assert "Codex" in checks["computer_signin"]["summary"] and "Claude" not in checks["computer_signin"]["summary"]
    assert checks["models"]["status"] == "ok"                # Claude signs in with the plan: the model is signed in
    shared_row = next(x for x in cloud.api.get("/api/v2/health", headers=headers("ana-test")).json()["computers"]
                      if x["id"] == shared["runner_id"])
    claude = next(r for r in shared_row["runtimes"] if r["name"] == "claude")
    assert claude["plan"] == "subscription" and claude["ready"] and not claude["signable"]
    from backend import getting_started
    with cloud.store.read() as c:
        online = getting_started._online_runners(c)
        assert getting_started._signed_in_runtime(online, {"claude"}, c) == "claude"
        assert getting_started._login_target(online, {"claude"}, c) is None
    add(cloud.api, "codex_login", codex_file(), name="ChatGPT")
    with cloud.store.transaction() as c:
        sid = c.execute("SELECT id FROM model_subscriptions WHERE kind='codex_login'").fetchone()[0]
    give(cloud.api, shared, "codex", sid)
    checks = health_of(cloud)
    assert "computer_signin" not in checks and "computer_logins" not in checks


def test_a_cloud_machine_stopped_when_idle_is_not_offline_on_health(cloud):
    plan_company(cloud, "cpo")
    shared = shared_for(cloud, "cpo")
    hour_ago = H.shift(H.now(), hours=-1)
    with cloud.store.transaction() as c:
        CM.save(c, CM.SHARED, state="stopped")
        c.execute("UPDATE runner_states SET last_seen_at=? WHERE runner_id=?", (hour_ago, shared["runner_id"]))
    work(cloud, "cpo")                                       # work waits for it: it starts for that, it is not silent
    checks = health_of(cloud)
    assert checks["computers"]["status"] == "ok" and "stopped until work comes" in checks["computers"]["summary"]
    assert not [k for k in checks if k.startswith("runner_load:")]
    assert "computer_offline" not in issue_kinds(cloud)
    with cloud.store.read() as c:
        from backend import health
        assert health.silent_computers(c) == []
    with cloud.store.transaction() as c:                     # a machine with a problem is offline as before
        CM.save(c, CM.SHARED, problem="out_of_credit")
    assert health_of(cloud)["computers"]["summary"] == "Every computer is offline."
    assert "computer_offline" in issue_kinds(cloud)


def test_a_list_that_breaks_outside_hq_still_lets_the_looks_run_and_says_why(cloud):
    adopt(cloud)
    work(cloud)
    cloud.hq.fail["create"] = [CM.Refused("boat_error", 502)]   # refused once: the next create waits on HQ's list
    assert cloud.tick(10) == ["create"]
    adopt(cloud, "cpo")
    work(cloud, "cpo")
    listed = cloud.hq.list

    def broken():
        raise ValueError("bad page")
    cloud.hq.list = broken
    cloud.hq.calls.clear()
    assert cloud.tick(CM.BACKOFF) == [] and cloud.row("cpo")["problem"] == "hq:ValueError"
    cloud.hq.list = listed
    assert "create" in cloud.tick(CM.BACKOFF_MAX) and cloud.row("cpo")["problem"] == ""


def test_a_failing_look_never_hides_a_named_problem_and_a_listed_outage_clears_without_a_create(cloud):
    adopt(cloud)
    with cloud.store.transaction() as c:
        CM.save(c, "ops", problem="out_of_credit", problem_at=H.now())
    cloud.manager.failed("ops", RuntimeError("blip"))
    assert cloud.row()["problem"] == "out_of_credit"            # its credit retry keys on it
    with cloud.store.transaction() as c:
        CM.save(c, "ops", problem="hq:unreachable", problem_at=H.now())   # said while a create waited on the list
    cloud.manager.synced = None                                 # (this server has no memory of the outage: a restart)
    assert cloud.tick(10) == [] and cloud.row()["problem"] == ""          # the list is back; no work, nothing made
