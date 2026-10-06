"""The runner supervises its side jobs: start when wanted, stop when not, restart with backoff."""
import subprocess
from pathlib import Path


from runner import sidejobs
from runner.sidejobs import SideJobs

ROOT = Path(__file__).resolve().parents[2]


class Proc:
    def __init__(self):
        self.code, self.signals = None, []

    def poll(self):
        return self.code

    def terminate(self):
        self.signals.append("term")
        self.code = -15

    def kill(self):
        self.signals.append("kill")
        self.code = -9

    def wait(self, timeout=None):
        if self.code is None:
            raise subprocess.TimeoutExpired("job", timeout)
        return self.code


class Hub:
    def __init__(self, sources=None):
        self.sources, self.posts = sources or [], []

    def get(self, path):
        assert path == "runners/importers"
        return {"importers": [{"source": s} for s in self.sources]}

    def post(self, path, body):
        self.posts.append((path, body))


class Rig:
    def __init__(self, hub=None, jobs=None):
        self.now, self.spawned, self.hub = 0.0, [], hub or Hub()
        self.jobs = jobs or {"importers": (sidejobs.importers_wanted, sidejobs.report_importers)}
        self.side = SideJobs({"url": "u", "token": "t", "projects_dir": "/none"}, "/x/runner.json",
                             client=self.hub, jobs=self.jobs, spawn=self.spawn, clock=lambda: self.now)

    def spawn(self, name):
        proc = Proc()
        self.spawned.append((name, proc))
        return proc


def test_starts_only_when_the_hub_assigns_it_here():
    rig = Rig(Hub([]))
    rig.side.tick()
    assert rig.spawned == []                       # another computer runs the importers
    rig.hub.sources = ["zoom"]
    rig.side.tick()
    rig.side.tick()
    assert [n for n, _ in rig.spawned] == ["importers"]     # started once, not once per pass


def test_stops_cleanly_when_unassigned():
    rig = Rig(Hub(["zoom"]))
    rig.side.tick()
    rig.hub.sources = []
    rig.side.tick()
    assert rig.spawned[0][1].signals == ["term"]
    assert rig.side.children == {}
    rig.side.tick()
    assert len(rig.spawned) == 1


def test_a_crash_restarts_with_growing_backoff_and_reports_it():
    rig = Rig(Hub(["zoom"]))
    rig.side.tick()
    delays = []
    for _ in range(4):
        rig.spawned[-1][1].code = 1
        rig.now += 1
        before = rig.now
        rig.side.tick()                            # sees the exit; too early to restart
        delays.append(rig.side.retry_at["importers"] - before)
        rig.now = rig.side.retry_at["importers"]
        rig.side.tick()
    assert delays == [10, 20, 40, 80]
    assert len(rig.spawned) == 5
    assert rig.hub.posts[0][0] == "imports/sources/zoom/status" and rig.hub.posts[0][1]["state"] == "error"


def test_stop_terminates_every_child():
    rig = Rig(Hub(["zoom"]))
    rig.side.tick()
    rig.side.stopping.set()
    rig.side.run()
    assert rig.spawned[0][1].signals == ["term"]


def test_a_stubborn_child_is_killed(monkeypatch):
    class Stubborn(Proc):
        def terminate(self):
            self.signals.append("term")
    rig = Rig(Hub(["zoom"]))
    rig.spawn = lambda name: rig.spawned.append((name, Stubborn())) or rig.spawned[-1][1]
    rig.side.spawn = rig.spawn
    monkeypatch.setattr(sidejobs, "STOP_GRACE", 0)
    rig.side.tick()
    rig.side.halt("importers")
    assert rig.spawned[0][1].signals == ["term", "kill"]


class Connectors:
    """A hub that may or may not name this computer's operator a processing operator."""
    def __init__(self, assigned=(), status=0):
        self.assigned, self.status = list(assigned), status

    def get(self, path):
        assert path == "runners/connectors"
        if self.status:
            from clients.tico import APIError
            raise APIError("not_found", "no such route", status=self.status)
        return {"connectors": self.assigned}

    def post(self, path, body):
        pass


def connectors_rig(tmp_path, hub):
    rig = Rig(hub, jobs={"connectors": (sidejobs.connectors_wanted, sidejobs.report_connectors)})
    rig.side.config["projects_dir"] = str(tmp_path)
    (tmp_path / "secrets").mkdir(exist_ok=True)
    return rig
