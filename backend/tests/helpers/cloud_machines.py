"""Helpers that other tests share, moved out of test_cloud_machines.py so they import no test file."""

import pytest
from backend import cloud_access, cloud_machines as CM, placement
from backend.store import H
from backend.tests.helpers.api import headers, post
from backend.tests.helpers.cloud_access import signals


SIZES = [
    {"name": name, "cpu": cpu, "memory_gb": cpu * 2, "usd_per_hour": 0.05 * cpu, "start_usd": 0.04, "idle_stop_s": idle}
    for name, cpu, idle in (("small", 1, 600), ("default", 2, 300), ("large", 4, 180), ("xlarge", 8, 120))
]


class FakeHQ:
    """The machines routes as Python calls. `fail[action]` is a Refused to raise (once each), or a list of them."""

    def __init__(self):
        self.calls, self.fail, self.machines, self.n = [], {}, {}, 0
        self.clock = None  # a test's fake clock (an ISO time): start and stop then stamp as HQ does
        self.sizes = SIZES  # what list() offers; a test marks one unavailable for the plan

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
        return {"machines": [dict(m) for m in self.machines.values()], "sizes": self.sizes, "caps": {"machines": 10}}

    def create(self, key, size, enroll_token, hub_url):
        self.calls.append(("create", key, size, enroll_token, hub_url))
        self._check("create")
        for machine in self.machines.values():
            if machine["key"] == key and machine["state"] not in ("deleted", "failed"):
                return dict(machine)
        for machine in self.machines.values():  # as HQ: the dead one is kept under a renamed key
            if machine["key"] == key:
                machine["key"] = f"{key}:superseded:{machine['id']}"
        self.n += 1
        mid = f"m{self.n}"
        self.machines[mid] = {
            "id": mid,
            "key": key,
            "size": size,
            "state": "starting",
            "started_at": H.now(),
            "stopped_at": None,
            "cost_month_usd": 0.0,
            "starts_month": 1,
            "last_error": "",
            "pending": None,
        }
        return dict(self.machines[mid])

    def start(self, machine_id, size=None):
        self.calls.append(("start", machine_id, size))
        self._check("start")
        machine = self.machines[machine_id]
        if machine["state"] in ("starting", "running"):
            return dict(machine)  # as HQ: a live machine is left as it is, never restarted
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
        if machine_id not in self.machines or self.machines[machine_id]["state"] == "deleted":
            raise CM.Refused("not_found", 404)
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
    at = [CM.epoch(H.now())]
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
    tick()  # HQ lists its sizes: the option is offered
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
    return {
        "active": True,
        "turns": False,
        "activity": 0,
        "want": None,
        "want_at": 0,
        "frozen": False,
        "retry_now": False,
        "update": None,
        "updating": False,
        **kw,
    }


def machine_row(**kw):
    return {
        "bot": "ops",
        "mode": "cloud",
        "size": "default",
        "always_on": 0,
        "machine_id": "m1",
        "runner_id": "r1",
        "state": "stopped",
        "machine_size": "default",
        "active_at": None,
        "heartbeat_at": None,
        "next_try_at": None,
        "failures": 0,
        "problem": "",
        "problem_at": None,
        "wake_key": None,
        "woke_at": None,
        "enroll_by": 0,
        "enroll_fails": 0,
        "pause_minutes": None,
        **kw,
    }


def shared_enrolled(cloud, *bots):
    """These bots on the shared machine, made for the first one's work and enrolled; returns its computer's id."""
    for bot in bots:
        adopt(cloud, bot, "shared")
    work(cloud, bots[0])
    assert cloud.tick()[-1] == "create" and cloud.hq.calls[-1][1] == CM.SHARED
    return post(cloud.api, "runners/enroll", {"code": cloud.hq.calls[-1][3], "label": "boat-vm", "platform": "linux"})[
        "runner_id"
    ]


def idle(cloud):
    with cloud.store.transaction() as c:
        c.execute("UPDATE jobs SET state='canceled'")


def claim_on(cloud, computer, bot):
    """A claim through the real route, as the machine's runner does it."""
    return post(cloud.api, "jobs/claim", {"bot": bot, "start_places": True}, token=computer["token"])


def stopped_when_idle(cloud, key="ops"):
    """Nothing queued for the machine and its clock at its pause: it is stopped."""
    idle(cloud)
    cloud.hq.calls.clear()
    cloud.at[0] = cloud.row(key)["active_at"] + 300
    cloud.tick()
    assert cloud.hq.actions() == ["stop"] and cloud.row(key)["state"] == "stopped"
    cloud.hq.calls.clear()
