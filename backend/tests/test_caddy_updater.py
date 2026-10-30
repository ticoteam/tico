"""The updater replaces only the installed Caddy front door, including rollback to an old direct proxy, and moves the
tunnel onto its hold only once the hold answers."""
import json
from pathlib import Path
import subprocess

import pytest

from backend.tests.test_updater_runner import load


class FrontDoorDocker:
    """A daemon with service identities and an accepted stop that can outlive its Compose client."""

    def __init__(self, updater, monkeypatch, project, *, selected=True, failure=""):
        self.updater, self.project, self.failure = updater, project, failure
        self.calls, self.pending, self.outcomes, self.next_id = [], set(), [], 0
        self.caddy_images = {"legacy"}  # A changed target bundle's image is not present yet.
        self.containers = {"server-old": (updater.SERVICE, "legacy")}
        if selected:
            self.containers["caddy-old"] = ("caddy", "legacy")
        self.health_answers = iter([False, True] if failure == "server" else [True, True, True])
        monkeypatch.setattr(updater, "docker_run", self.run)
        monkeypatch.setattr(updater, "healthy", lambda seconds: next(self.health_answers))
        monkeypatch.setattr(updater, "running_image", lambda: ("sha256:old", "v0.1.0"))
        monkeypatch.setattr(updater, "pinned_services", lambda version: [])
        monkeypatch.setattr(updater, "check_switched", lambda version, release: None)
        monkeypatch.setattr(updater, "SETTLE_RETRY_SECONDS", 0)
        original = updater.set_status

        def watched(**fields):
            original(**fields)
            if fields.get("state") in {"healthy", "rolled_back", "failed"}:
                self.outcomes.append((fields["state"], self.pending & self.containers.keys()))
        monkeypatch.setattr(updater, "set_status", watched)

    def result(self, argv, out="", code=0):
        return subprocess.CompletedProcess(argv, code, out, "fictional front-door failure" if code else "")

    def run(self, argv, env=None, **kwargs):
        assert kwargs.get("timeout"), argv
        tag = (env or {}).get("TICO_TAG")
        bundle = (self.project / "compose.yaml").read_text()
        self.calls.append((argv, tag, bundle, self.updater.status["state"]))
        if argv[:2] == ["docker", "compose"]:
            args = argv[argv.index("--project-directory") + 2:]
            if args == ["config", "--services"]:
                # Even if config lists inactive profiles, an absent Caddy must never be explicitly started.
                return self.result(argv, "server\ncaddy\ncloudflared\nupdater\n")
            if args[0] == "ps":
                service = args[-1]
                return self.result(argv, "\n".join(cid for cid, (name, _) in self.containers.items() if name == service))
            if args[0] == "pull" and args[-1] == "caddy":
                if self.failure == "pull":
                    return self.result(argv, code=1)
                self.caddy_images.add(bundle.strip())
            if args[0] == "up":
                service = args[-1]
                if service not in {self.updater.SERVICE, "caddy"}:
                    return self.result(argv)
                if service == "caddy" and bundle.strip() not in self.caddy_images:
                    return self.result(argv, code=1)
                old = {cid for cid, (name, _) in self.containers.items() if name == service}
                if service == "caddy" and tag == "v0.2.0" and self.failure == "uncertain":
                    self.pending.update(old)   # the old stop may still land after this client has gone
                    self.failure = ""
                    raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
                for cid in old:
                    del self.containers[cid]
                self.pending.difference_update(old)   # no accepted stop can reach the recreated container
                self.next_id += 1
                self.containers[service + str(self.next_id)] = (service, bundle.strip())
            return self.result(argv)
        if argv[:2] == ["docker", "inspect"]:
            if "--format" in argv:
                return self.result(argv, "\n".join(cid + " running False" for cid in argv[4:] if cid in self.containers))
            cid = argv[2]
            service, bundle = self.containers[cid]
            state = {"Running": True}
            command = ["caddy", "reverse-proxy", "--from", "acme.example", "--to", "server:8765"]
            if service == "caddy" and bundle == "supervised":
                state["Health"] = {"Status": "unhealthy" if self.failure == "front_door" else "healthy"}
                command = ["sh", "/tunnel/caddy-supervisor.sh"]
            return self.result(argv, json.dumps([{"State": state, "Config": {"Cmd": command}}]))
        return self.result(argv)

    def caddy_ups(self):
        return [call for call in self.calls if call[0][:2] == ["docker", "compose"]
                and "up" in call[0] and call[0][-1] == "caddy"]


def installed(monkeypatch, tmp_path, *, mode="server", selected=True, failure=""):
    updater = load(monkeypatch, mode, tmp_path)
    (tmp_path / "compose.yaml").write_text("legacy\n")
    (tmp_path / ".env").write_text("TICO_TAG=v0.1.0\n")
    if mode == "server":
        monkeypatch.setattr(updater, "BUNDLE", "always")
        monkeypatch.setattr(updater, "release_of", lambda version: version)

        def bundle(release, into):
            path = Path(into) / "compose.yaml"
            path.write_text("supervised\n")
            return {"compose.yaml": str(path)}
        monkeypatch.setattr(updater, "fetch_bundle", bundle)
    docker = FrontDoorDocker(updater, monkeypatch, tmp_path, selected=selected, failure=failure)
    return updater, docker


def test_update_waits_for_a_fresh_healthy_front_door_before_its_final_outcome(monkeypatch, tmp_path):
    updater, docker = installed(monkeypatch, tmp_path)
    updater.update("v0.2.0")
    assert updater.status["state"] == "healthy" and updater.status["front_door"] == "healthy"
    caddy, = docker.caddy_ups()   # the later generic services loop must not recreate it again
    argv, tag, bundle, state = caddy
    assert tag == "v0.2.0" and bundle == "supervised\n" and state == "restarting"
    assert "--force-recreate" in argv and "--wait" in argv and "--wait-timeout" in argv and "never" in argv
    first_server = next(i for i, (a, _, _, _) in enumerate(docker.calls) if "up" in a and a[-1] == "server")
    assert first_server < docker.calls.index(caddy)
    assert docker.outcomes == [("healthy", set())]


@pytest.mark.parametrize("failure", ["server", "front_door"])
def test_rollback_recreates_the_restored_prefeature_direct_proxy(monkeypatch, tmp_path, failure):
    updater, docker = installed(monkeypatch, tmp_path, failure=failure)
    updater.update("v0.2.0")
    assert updater.status["state"] == "rolled_back" and updater.status["restored"]
    assert updater.status["front_door"] == "legacy_running"  # no asset-readiness claim for an old direct command
    argv, tag, bundle, _ = docker.caddy_ups()[-1]
    assert tag == "v0.1.0" and bundle == "legacy\n" and "--force-recreate" in argv
    assert (tmp_path / "compose.yaml").read_text() == "legacy\n"
    assert docker.outcomes == [("rolled_back", set())]


@pytest.mark.parametrize("first_settled_health", [True, False])
def test_uncertain_front_door_holds_until_the_restored_recreate_is_confirmed(monkeypatch, tmp_path, first_settled_health):
    updater, docker = installed(monkeypatch, tmp_path, failure="uncertain")
    # Initial target and ordinary rollback health pass. A failed settlement check
    # must keep the Caddy debt until a later healthy server and fresh front door.
    docker.health_answers = iter([True, True, first_settled_health, True])
    updater.update("v0.2.0")
    assert updater.status["state"] == "rolled_back" and updater.status["front_door"] == "legacy_running"
    first, restored = docker.caddy_ups()
    assert first[1:] == ("v0.2.0", "supervised\n", "restarting")
    # No blind Caddy mutation in the ordinary rollback while the original operation's outcome is unknown.
    assert restored[1:] == ("v0.1.0", "legacy\n", "uncertain")
    assert "--force-recreate" in restored[0] and not docker.pending
    settled_servers = [call for call in docker.calls if "up" in call[0] and call[0][-1] == "server"
                       and call[3] == "uncertain"]
    assert len(settled_servers) == (1 if first_settled_health else 2)
    assert docker.outcomes == [("rolled_back", set())]
    saved = json.loads((tmp_path / updater.STATUS_FILE).read_text())
    assert saved["settle_caddy"] is False and saved["settle_tag"] == ""


@pytest.mark.parametrize("failure", ["", "pull"])
def test_changed_target_caddy_image_is_staged_before_switch_and_a_refused_pull_never_starts_target(
        monkeypatch, tmp_path, failure):
    updater, docker = installed(monkeypatch, tmp_path, failure=failure)
    assert "supervised" not in docker.caddy_images
    updater.update("v0.2.0")
    pulls = [call for call in docker.calls if call[0][:2] == ["docker", "compose"]
             and call[0][-2:] == ["pull", "caddy"]]
    pull, = pulls
    assert pull[1:3] == ("v0.2.0", "supervised\n")
    target_ups = [call for call in docker.calls if call[0][:2] == ["docker", "compose"]
                  and "up" in call[0] and call[1] == "v0.2.0"]
    if not failure:
        assert updater.status["state"] == "healthy" and "supervised" in docker.caddy_images
        assert target_ups and docker.calls.index(pull) < docker.calls.index(target_ups[0])
    else:
        assert not target_ups and updater.status["state"] == "rolled_back"
        assert not updater.status["snapshot"] and not updater.status["restored"]
        assert (tmp_path / "compose.yaml").read_text() == "legacy\n"
        assert all(call[1] == "v0.1.0" for call in docker.caddy_ups())
    # The old image is already local; rollback/settle must not pull it.
    assert len(pulls) == 1


@pytest.mark.parametrize("mode", ["server", "runner"])
def test_no_existing_caddy_or_a_runner_never_starts_one(monkeypatch, tmp_path, mode):
    updater, docker = installed(monkeypatch, tmp_path, mode=mode, selected=False)
    updater.update("v0.2.0")
    assert updater.status["state"] == "healthy" and not docker.caddy_ups()
    assert updater.status["front_door"] == ""
    assert not any(call[0][-2:] == ["pull", "caddy"] for call in docker.calls)


class TunnelDocker:
    """An install with a tunnel from before the hold: cloudflared runs the old bundle's direct route. The target bundle
    adds the hold, and cloudflared recreated from it routes everything through that hold."""

    def __init__(self, updater, monkeypatch, project, failure=""):
        self.updater, self.project, self.failure = updater, project, failure
        self.events, self.hold, self.cloudflared, self.servers = [], {}, "direct", ["server0"]
        self.tunnel_id, self.pending = "cf0", set()   # cloudflared's container, and any stop still on its way to one
        monkeypatch.setattr(updater, "docker_run", self.run)
        monkeypatch.setattr(updater, "healthy", lambda seconds: True)
        monkeypatch.setattr(updater, "running_image", lambda: ("sha256:old", "v0.1.0"))
        monkeypatch.setattr(updater, "pinned_services", lambda version: [])
        monkeypatch.setattr(updater, "check_switched", lambda version, release: None)
        monkeypatch.setattr(updater, "HEALTH_SECONDS", 0)
        monkeypatch.setattr(updater, "POLL_SECONDS", 0)
        monkeypatch.setattr(updater, "SETTLE_RETRY_SECONDS", 0)

    def result(self, argv, out="", code=0):
        return subprocess.CompletedProcess(argv, code, out, "fictional hold failure" if code else "")

    def run(self, argv, env=None, **kwargs):
        assert kwargs.get("timeout"), argv
        tag, bundle = (env or {}).get("TICO_TAG"), (self.project / "compose.yaml").read_text().strip()
        if argv[:2] == ["docker", "inspect"]:
            if "--format" in argv:
                return self.result(argv, "\n".join(cid + " " + self.hold.get(cid, "running") + " False" for cid in argv[4:]))
            return self.result(argv, json.dumps([{"State": {"Running": self.hold.get(argv[2]) == "running"}}]))
        if argv[:2] != ["docker", "compose"]:
            return self.result(argv)
        args = argv[argv.index("--project-directory") + 2:]
        if args == ["config", "--services"]:
            return self.result(argv, "server\n" + ("hold\n" if bundle == "tunnel" else "") + "cloudflared\nupdater\n")
        if args[:3] == ["ps", "-a", "-q"]:
            found = {"hold": list(self.hold), "cloudflared": [self.tunnel_id]}.get(args[-1], self.servers)
            return self.result(argv, "\n".join(found))
        if args[0] == "up" and args[-1] == self.updater.SERVICE:
            self.servers = ["server%d" % (int(self.servers[0][6:]) + 1)]
        if args[0] == "pull" and "hold" in args:
            self.events.append(("pull", tag))
            return self.result(argv, code=1 if self.failure == "pull" else 0)
        if args[0] == "up" and args[-1] == "hold":
            self.events.append(("up hold", tag))
            if self.failure == "start":
                return self.result(argv, code=1)
            self.hold = {"hold1": "exited" if self.failure == "crash" else "running"}
        if args[0] == "exec" and args[2] == "hold":
            answers = self.hold.get("hold1") == "running"
            self.events.append(("hold answered" if answers else "hold silent", "-"))
            return self.result(argv, code=0 if answers else 1)
        if args[0] == "up" and args[-1] == "cloudflared":
            self.events.append(("up cloudflared", tag))
            if self.failure == "cut" and tag == "v0.2.0":   # the client never heard back; the old one's stop may land
                self.pending.add(self.tunnel_id)
                raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
            if "--force-recreate" in args or bundle != self.cloudflared:   # else Compose keeps the matching container
                self.tunnel_id = "cf%d" % (int(self.tunnel_id[2:]) + 1)
            self.cloudflared = bundle   # Compose recreates it only when its definition changed; same bundle, same route
        return self.result(argv)


def tunnel_install(monkeypatch, tmp_path, failure=""):
    updater = load(monkeypatch, "server", tmp_path)
    (tmp_path / "compose.yaml").write_text("direct\n")
    (tmp_path / ".env").write_text("TICO_TAG=v0.1.0\n")
    monkeypatch.setattr(updater, "BUNDLE", "always")
    monkeypatch.setattr(updater, "release_of", lambda version: version)

    def bundle(release, into):
        path = Path(into) / "compose.yaml"
        path.write_text("tunnel\n")
        return {"compose.yaml": str(path)}
    monkeypatch.setattr(updater, "fetch_bundle", bundle)
    return updater, TunnelDocker(updater, monkeypatch, tmp_path, failure=failure)


def test_cloudflared_moves_onto_the_hold_only_after_the_hold_answers(monkeypatch, tmp_path):
    updater, docker = tunnel_install(monkeypatch, tmp_path)
    updater.update("v0.2.0")
    assert updater.status["state"] == "healthy" and docker.cloudflared == "tunnel"
    assert docker.events == [("pull", "v0.2.0"), ("up hold", "v0.2.0"), ("hold answered", "-"),
                             ("up cloudflared", "v0.2.0")]


@pytest.mark.parametrize("failure", ["pull", "start", "crash"])
def test_a_hold_that_does_not_come_up_leaves_cloudflared_on_its_direct_route_and_fails_the_update(
        monkeypatch, tmp_path, failure):
    updater, docker = tunnel_install(monkeypatch, tmp_path, failure=failure)
    updater.update("v0.2.0")
    assert docker.cloudflared == "direct"   # never recreated onto the route to a hold that is not there
    assert ("up cloudflared", "v0.2.0") not in docker.events
    assert updater.status["state"] == "rolled_back" and "v0.1.0" in updater.status["message"]
    assert (tmp_path / "compose.yaml").read_text() == "direct\n"   # a later `docker compose up` keeps the direct route
    if failure != "pull":
        assert "hold" in updater.status["message"]


def test_a_cut_cloudflared_recreate_is_settled_back_onto_the_restored_route(monkeypatch, tmp_path):
    updater, docker = tunnel_install(monkeypatch, tmp_path, failure="cut")
    updater.update("v0.2.0")
    assert updater.status["state"] == "rolled_back"
    assert docker.cloudflared == "direct" and docker.tunnel_id not in docker.pending   # a fresh one, not the stopping one
    assert docker.events[-1] == ("up cloudflared", "v0.1.0")
