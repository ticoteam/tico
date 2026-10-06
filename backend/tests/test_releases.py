"""The running version, the update notice and the owner's "Update now"."""

import json
from pathlib import Path

import httpx
import pytest

from backend import releases
from backend.tests.test_onboarding import as_person, environment, signed_in  # noqa: F401

RELEASE = {"tag_name": "v0.2.0", "html_url": "https://github.com/ticoteam/tico/releases/tag/v0.2.0",
           "published_at": "2026-10-20T10:00:00Z", "name": "Tico 0.2.0"}


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(releases, "CHECKER", releases.Checker())
    monkeypatch.setattr(releases, "TRANSPORT", None)
    monkeypatch.delenv("TICO_UPDATER_URL", raising=False)
    monkeypatch.delenv("TICO_UPDATER_TOKEN", raising=False)
    monkeypatch.setenv("TICO_UPDATE_CHECK", "on")
    monkeypatch.setenv("TICO_VERSION", "0.1.0")


def network(monkeypatch, handler):
    monkeypatch.setattr(releases, "TRANSPORT", httpx.MockTransport(handler))


def test_semver_comparison():
    assert releases.newer("0.2.0", "0.1.0") and releases.newer("v1.0.0", "0.9.9")
    assert releases.newer("0.10.0", "0.9.0")
    assert not releases.newer("0.1.0", "0.1.0") and not releases.newer("0.1.0", "0.2.0")
    assert releases.newer("1.0.0", "1.0.0-rc.1") and not releases.newer("1.0.0-rc.1", "1.0.0")
    assert releases.newer("1.0.0-rc.10", "1.0.0-rc.2")
    assert not releases.newer("0.2.0", "dev") and not releases.newer("latest", "0.1.0")


def test_update_is_owner_only(environment):
    api = environment()
    person = as_person(api, "riley")
    assert api.post("/api/v2/system/update", json={"version": "0.2.0"}, headers=person).status_code == 403
    assert api.get("/api/v2/system/update", headers=person).status_code == 403


def test_update_is_forwarded_to_the_updater_with_the_token(environment, monkeypatch):
    seen = []

    def updater(request):
        seen.append((request.method, request.url.path, request.headers["authorization"],
                     json.loads(request.content) if request.content else None))
        return httpx.Response(200, json={"state": "pulling", "from": "0.1.0", "to": "0.2.0", "message": ""})
    network(monkeypatch, updater)
    monkeypatch.setenv("TICO_UPDATER_URL", "http://updater:9000/")
    monkeypatch.setenv("TICO_UPDATER_TOKEN", "s3cret")
    monkeypatch.setenv("TICO_VERSION", "v0.1.9")
    api = environment()
    r = api.post("/api/v2/system/update", json={"version": "v0.2.0"}, headers=signed_in())
    assert r.status_code == 200 and r.json()["state"] == "pulling"
    # The release this server runs goes along, so the updater never has to guess "from".
    assert seen[0] == ("POST", "/update", "Bearer s3cret", {"version": "0.2.0", "from": "0.1.9"})
    assert seen[1][:3] == ("GET", "/status", "Bearer s3cret")
    got = api.get("/api/v2/system/update", headers=signed_in()).json()
    assert {k: got[k] for k in ("configured", "state", "from", "to", "message", "snapshot", "restored")} == {
        "configured": True, "state": "pulling", "from": "0.1.0", "to": "0.2.0", "message": "", "snapshot": "",
        "restored": False}
    assert got["running"] == "0.1.9"                       # the server's own record, beside the updater's view


def test_docker_updater_speaks_the_servers_contract(environment, monkeypatch, tmp_path):
    """The real docker/updater.py behind the real client: version spelling, token, status shape."""
    import importlib.util
    import threading
    from http.server import ThreadingHTTPServer
    spec = importlib.util.spec_from_file_location("tico_updater", Path(__file__).resolve().parents[2] / "docker/updater.py")
    updater = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(updater)
    (tmp_path / "token").write_text("s3cret\n")
    started = []
    monkeypatch.setattr(updater, "TOKEN_FILE", str(tmp_path / "token"))
    monkeypatch.setattr(updater, "update", lambda version, running="": started.append((version, running)))
    # What the previous update left in the status: a new one must not show any of it.
    updater.status.update(state="healthy", **{"from": "v0.1.0", "to": "v0.1.9"}, message="old", snapshot="s", restored=True)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), updater.Handler)
    threading.Thread(target=lambda: httpd.serve_forever(0.02), daemon=True).start()
    try:
        monkeypatch.setenv("TICO_UPDATER_URL", "http://127.0.0.1:%d" % httpd.server_address[1])
        monkeypatch.setenv("TICO_UPDATER_TOKEN", "s3cret")
        monkeypatch.setenv("TICO_VERSION", "0.1.9")
        api = environment()
        r = api.post("/api/v2/system/update", json={"version": "0.2.0"}, headers=signed_in())
        assert r.status_code == 200 and r.json()["configured"] is True and r.json()["state"] == "pulling"
        assert r.json()["to"] == "v0.2.0"
        # "from" is the release the server runs, not the previous update's; nothing else is carried over either.
        assert (r.json()["from"], r.json()["message"], r.json()["snapshot"], r.json()["restored"]) == ("v0.1.9", "", "", False)
        monkeypatch.setenv("TICO_UPDATER_TOKEN", "wrong")
        assert api.get("/api/v2/system/update", headers=signed_in()).status_code == 502
    finally:
        httpd.shutdown()
    import time
    deadline = time.time() + 2
    while not started and time.time() < deadline:
        time.sleep(0.01)
    assert started == [("v0.2.0", "v0.1.9")]

