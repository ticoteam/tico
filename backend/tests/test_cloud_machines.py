"""Bots' own cloud machines (backend/cloud_machines.py): on a Tico Cloud company a bot with no computer runs on its own
machine, made on first work with a one-time enrollment code and bound to that bot only; it is started for work, kept
with a heartbeat, stopped after its size's break-even idle time unless Always on, never started out of credit, and
retried with backoff when HQ refuses. A size change applies at the next start. Off cloud nothing changes. HQ is a fake
client and the clock is a fake one."""

import json
import time

import httpx
import pytest

from backend import cloud_access, cloud_machines as CM, placement
from backend.store import H
from backend.tests.test_api import api, assign, get, headers, post, runner  # noqa: F401
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
            if machine["key"] == key and machine["state"] != "deleted":
                return dict(machine)
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


def adopt(cloud, bot="ops"):
    with cloud.store.transaction() as c:
        assert placement.auto_place(c, cloud.api.app.state.execution, bot) is None
    return cloud.row(bot)


def work(cloud, bot="ops", text="Please summarize the current work."):
    return post(cloud.api, "chat/" + bot, {"text": text})


def enrolled(cloud, bot="ops"):
    """Create the bot's machine for its queued work and enroll its runner with the code HQ was handed."""
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
    return {"active": True, "turns": False, "activity": 0, "want": None, "frozen": False, "retry_now": False, **kw}


def machine_row(**kw):
    return {"bot": "ops", "mode": "cloud", "size": "default", "always_on": 0, "machine_id": "m1", "runner_id": "r1",
            "state": "stopped", "machine_size": "default", "active_at": None, "heartbeat_at": None, "next_try": None,
            "failures": 0, "problem": "", "problem_at": None, "wake_key": None, "woke_at": None, **kw}


def test_decide_wakes_for_work_and_creates_on_first_use():
    assert CM.decide(machine_row(), facts(), 1000, 300) == (None, None)
    assert CM.decide(machine_row(), facts(want="job:a"), 1000, 300) == ("start", None)
    assert CM.decide(machine_row(machine_id=None, state="none"), facts(want="job:a"), 1000, 300) == ("create", None)
    assert CM.decide(machine_row(state="deleted"), facts(want="move"), 1000, 300) == ("create", None)
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
    waiting = machine_row(next_try=1100, problem="out_of_credit")
    assert CM.decide(waiting, facts(want="job:a"), 1050, 300) == (None, None)
    assert CM.decide(waiting, facts(want="job:a", retry_now=True), 1050, 300) == ("start", None)
    assert CM.decide(waiting, facts(want="job:a"), 1100, 300) == ("start", None)
    # Woken once for this job and nothing ran: not again for it; new activity or new work wakes it.
    woke = machine_row(wake_key="job:a", woke_at=1000)
    assert CM.decide(woke, facts(want="job:a", activity=900), 2000, 300) == (None, "stuck")
    assert CM.decide(woke, facts(want="job:a", activity=1500), 2000, 300) == ("start", None)
    assert CM.decide(woke, facts(want="job:b", activity=900), 2000, 300) == ("start", None)


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
    assert get(api, "cloud-machines") == {"offered": False, "sizes": [], "caps": {}}
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
        c.execute("UPDATE jobs SET state='cancelled' WHERE bot='ops'")
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


def test_always_on_starts_with_no_work_and_never_idles(cloud):
    enrolled(cloud)
    with cloud.store.transaction() as c:
        c.execute("UPDATE jobs SET state='cancelled' WHERE bot='ops'")
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
        c.execute("UPDATE jobs SET state='cancelled' WHERE bot='ops'")

    def ran(seconds_ago, every):
        when = H.shift(H.now(), seconds=-seconds_ago + (cloud.at[0] - time.time()))
        with cloud.store.transaction() as c:
            c.execute("INSERT INTO watcher_runs(bot,name,runner_id,started,finished,exit_code,every) "
                      "VALUES('ops','mail',?,?,?,0,?) ON CONFLICT(bot,name) DO UPDATE SET finished=excluded.finished",
                      (r["runner_id"], when, when, every))
    for _ in range(4):                                          # every two minutes: never idle for 300 s
        cloud.at[0] += 120
        ran(0, 120)
        cloud.tick()
    assert "stop" not in cloud.hq.actions()
    cloud.tick(400)
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
        c.execute("UPDATE jobs SET state='cancelled' WHERE bot='ops'")
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
    delete = cloud.api.delete("/api/v2/bots/ops/cloud-machine", headers=headers())
    assert delete.status_code == 200, delete.text
    assert cloud.hq.calls[-1] == ("delete", "m1")
    assert cloud.row() == {**cloud.row(), "mode": "computer", "machine_id": None, "runner_id": None}
    # Choosing its own machine again makes a new one at its next work.
    post(cloud.api, "bots/ops/cloud-machine", {"mode": "cloud"})
    assert cloud.tick(10)[-1] == "create"


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


def test_sizes_and_choices_are_checked(cloud):
    adopt(cloud)
    cloud.tick()
    shown = get(cloud.api, "cloud-machines")
    assert shown["offered"] and [s["name"] for s in shown["sizes"]] == ["small", "default", "large", "xlarge"]
    post(cloud.api, "bots/ops/cloud-machine", {"size": "huge"}, expected=422)
    post(cloud.api, "bots/ops/cloud-machine", {"mode": "computer"}, expected=422)
    post(cloud.api, "bots/ops/cloud-machine", {"size": "large"}, token="cara-test", expected=403)


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
