"""Helpers that other tests share, moved out of test_updater_runner.py so they import no test file."""

import importlib.util
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[3] / "docker/updater.py"


def load(monkeypatch, mode, tmp_path):
    for key, value in {
        "TICO_UPDATER_MODE": mode,
        "TICO_PROJECT_DIR": str(tmp_path),
        "TICO_COMPOSE_FILE": "runner.compose.yaml" if mode == "runner" else "",
        "TICO_UPDATER_TOKEN_FILE": str(tmp_path / "token"),
        "TICO_UPDATER_SELF": "never",
        "TICO_UPDATER_BUNDLE": "never",
    }.items():
        monkeypatch.setenv(key, value)
    spec = importlib.util.spec_from_file_location("tico_updater_" + (mode or "server"), SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class NeverSettled(BaseException):
    """settle() catches every Exception and tries again, so a fake daemon stops a settle that would hold forever."""


class Moby:
    """A Docker daemon with containers, for what a killed client leaves behind. `up` resolves the runner's container
    and asks the daemon to stop it; once accepted, that stop runs to its end even if the client is killed (Moby
    stops with a context that ignores cancellation). `hang(argv, tag, n)` names the calls whose client is killed
    after the daemon accepted; their stop lands `lag` calls later. `n` counts the calls so far."""

    def __init__(self, module, monkeypatch, hang, lag=3, healthy_tags=("v0.1.0",)):
        import subprocess

        self.module, self.hang, self.lag, self.healthy_tags = module, hang, lag, healthy_tags
        self.expired, self.calls, self.n = module.Stalled, [], 0
        self.containers = {"c1": {"tag": "v0.1.0", "running": True}}  # what the update starts from
        self.pending = []  # [container id, calls until it lands]: accepted stops nobody waited for
        self.idle_recreates = 0  # answered recreates that change nothing (a daemon that has not caught up)
        self.stopped = []  # containers an accepted stop stopped after the client was gone, with the status then
        self.ended = []  # (state, accepted stops still aimed at a container that exists) at each outcome
        self.restores = {}  # one-off id -> calls until its restore replaces the database (then it ends, --rm)
        self.restored = []  # (live service containers, status) when a restore replaced the database
        monkeypatch.setattr(module, "docker_run", self.run)
        monkeypatch.setattr(module, "pinned_services", lambda version: [])
        monkeypatch.setattr(module, "check_switched", lambda version, release: None)
        monkeypatch.setattr(module, "POLL_SECONDS", 0)
        monkeypatch.setattr(module, "SETTLE_RETRY_SECONDS", 0)
        monkeypatch.setattr(module, "HEALTH_SECONDS", 1)
        set_status = module.set_status

        def watched(**fields):
            set_status(**fields)
            if fields.get("state") in ("healthy", "rolled_back", "failed"):
                self.ended.append((fields["state"], [c for c, _ in self.pending if c in self.containers]))

        monkeypatch.setattr(module, "set_status", watched)
        save_status, self.saves = module.save_status, 0

        def counted():
            self.saves += 1
            if self.saves > 500:
                raise NeverSettled(module.status["message"])
            save_status()

        monkeypatch.setattr(module, "save_status", counted)

    def live(self):
        return [c for c, v in self.containers.items() if v["running"] and not v.get("oneoff")]

    def land(self):
        for cid in list(self.restores):
            self.restores[cid] -= 1
            if self.restores[cid] <= 0 and self.containers.get(cid, {}).get("running"):
                del self.restores[cid], self.containers[cid]
                self.restored.append((self.live(), self.module.status["state"]))
        for item in list(self.pending):
            item[1] -= 1
            if item[1] <= 0:
                self.pending.remove(item)
                if item[0] in self.containers and self.containers[item[0]]["running"]:
                    self.containers[item[0]]["running"] = False
                    self.stopped.append((item[0], self.module.status["state"]))

    def run(self, argv, env=None, timeout=None, **kw):
        assert timeout, argv  # nothing the worker runs may hang it
        self.n += 1
        self.land()
        tag = (env or {}).get("TICO_TAG")
        self.calls.append((argv, tag, self.module.status["state"]))
        args = argv[argv.index("--project-directory") + 2 :] if "--project-directory" in argv else argv[1:]
        out = ""
        if args[:1] == ["run"]:  # a --rm one-off: the snapshot restore
            cid = "r%d" % self.n
            self.containers[cid] = {"tag": tag, "running": True, "oneoff": True}
            self.restores[cid] = self.lag
            if self.hang(argv, tag, self.n):
                raise self.expired(argv, timeout)  # the client is killed; the container is not
            del self.restores[cid], self.containers[cid]
            self.restored.append((self.live(), self.module.status["state"]))
        elif args[:1] == ["stop"]:
            for c in self.live():
                self.containers[c]["running"] = False
        elif argv[:2] == ["docker", "rm"]:
            if self.containers.get(argv[2], {}).get("running"):
                return type("R", (), {"returncode": 1, "stdout": "", "stderr": "cannot remove a running container"})()
            self.containers.pop(argv[2], None)
        elif args[:1] == ["up"]:
            current = [c for c in self.containers if not self.containers[c].get("oneoff")]
            if "--force-recreate" not in args and any(
                self.containers[c]["tag"] == tag and self.containers[c]["running"] for c in current
            ):
                pass  # up to date: nothing to recreate
            else:
                if "--force-recreate" in args and self.idle_recreates:
                    self.idle_recreates -= 1
                    return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
                accepted = [[c, self.lag] for c in current]
                if self.hang(argv, tag, self.n):
                    self.pending += accepted  # the client is killed; the daemon is not
                    raise self.expired(argv, timeout)
                for c in current:  # an answered recreate: stopped and removed
                    del self.containers[c]
                self.pending = [p for p in self.pending if p[0] in self.containers]
                self.containers["c%d" % (self.n + 100)] = {"tag": tag, "running": True}
        elif args[:1] == ["ps"]:
            out = "".join(
                c + "\n" for c, v in self.containers.items() if "-a" in args or (v["running"] and not v.get("oneoff"))
            )
        elif argv[:2] == ["docker", "inspect"] and "{{.State.Status}}" in argv[3]:
            out = "".join(
                "%s %s %s\n"
                % (c, v.get("state") or ("running" if v["running"] else "exited"), "True" if v.get("oneoff") else "")
                for c, v in self.containers.items()
                if c in argv[4:]
            )
        elif argv[:2] == ["docker", "inspect"] and "{{.State.Health.Status}}" in argv:
            box = self.containers.get(argv[-1])
            out = "healthy\n" if box and box["running"] and box["tag"] in self.healthy_tags else "unhealthy\n"
        elif argv[:2] == ["docker", "inspect"]:
            tag = self.containers[argv[-1]]["tag"]
            out = "sha256:%s ghcr.io/ticoteam/tico-runner:%s\n" % (tag, tag)
        return type("R", (), {"returncode": 0, "stdout": out, "stderr": ""})()
