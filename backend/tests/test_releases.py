"""The running version, the update notice and the owner's "Update now"."""

import json
from pathlib import Path

import httpx
import pytest

from backend import releases
from backend.tests.helpers.onboarding import as_person, environment, signed_in  # noqa: F401

RELEASE = {
    "tag_name": "v0.2.0",
    "html_url": "https://github.com/ticoteam/tico/releases/tag/v0.2.0",
    "published_at": "2026-10-20T10:00:00Z",
    "name": "Tico 0.2.0",
}


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


def test_a_server_on_a_newer_prerelease_is_not_shown_an_older_release_as_the_latest():
    """Tico Team ran 0.6.0-rc.8 and Health said the latest was 0.5.216: the check reads the newest stable release."""
    checker = releases.Checker()
    checker.checked = checker.clock()  # fresh: no background read
    checker.release = {**RELEASE, "tag": "v0.5.216", "url": "u", "name": "n"}
    seen = checker.view("0.6.0-rc.8")
    assert (seen["latest"], seen["available"], seen["url"]) == ("", False, "")
    assert checker.view("0.5.200")["latest"] == "0.5.216" and checker.view("0.5.200")["available"]
    assert checker.view("0.5.300")["latest"] == ""  # nor is it to a stable build ahead of it


def test_a_release_read_before_the_running_version_caught_up_is_read_again_soon(monkeypatch):
    """Health said latest 0.6.78 for an hour while 0.6.79 was published and running: the check had read just before
    0.6.79's caches caught up and kept that answer for TTL. Behind the running version, it reads again after BEHIND,
    unconditionally, and past an HQ answer that is behind too; an older release is never shown as the latest."""
    monkeypatch.setenv("TICO_VERSION", "0.6.79")
    monkeypatch.setattr(releases.Checker, "enabled", staticmethod(lambda: True))
    now = [1000.0]
    checker = releases.Checker(clock=lambda: now[0])
    checker.release = {**RELEASE, "tag": "v0.6.78", "url": "u78", "name": "n"}
    checker.etag = "e78"
    checker.checked = now[0]
    started = []
    monkeypatch.setattr(releases.Checker, "refresh", lambda self, force=False: started.append(force))
    monkeypatch.setattr(
        releases.threading, "Thread", lambda target, **kw: type("T", (), {"start": lambda s: target()})()
    )
    seen = checker.view("0.6.79")
    assert (seen["latest"], seen["available"], seen["url"]) == ("", False, "")
    assert started == []  # fresh: not read again at once
    now[0] += releases.BEHIND + 1
    checker.view("0.6.79")
    assert started == [False]  # read again well before TTL
    assert checker.view("0.6.80-rc.1")["latest"] == ""  # a prerelease ahead shows none
    assert not checker._behind("0.6.80-rc.1")  # and is not read again every BEHIND


def test_a_read_behind_the_running_version_skips_the_conditional_read_and_a_behind_hq(monkeypatch):
    monkeypatch.setenv("TICO_VERSION", "0.6.79")
    checker = releases.Checker()
    checker.release = {**RELEASE, "tag": "v0.6.78", "url": "u78", "name": "n"}
    checker.etag = "e78"
    asked = []

    def github(request):
        asked.append((request.url.host, request.headers.get("if-none-match"), request.headers.get("cache-control")))
        if request.url.host != "api.github.com":
            return httpx.Response(200, json={**RELEASE, "tag_name": "v0.6.78"})  # HQ still behind
        return httpx.Response(200, json={**RELEASE, "tag_name": "v0.6.79", "html_url": "u79"})

    network(monkeypatch, github)
    checker.census = type("C", (), {"query": lambda self, v: {"v": v}})()
    checker.refresh()
    assert asked[-1] == ("api.github.com", None, "no-cache")  # past HQ, and no conditional read of the stale answer
    assert [a[0] for a in asked][-1] == "api.github.com" and len(asked) == 2  # HQ first, then GitHub
    seen = checker.view("0.6.79")
    assert (seen["latest"], seen["url"], seen["available"]) == ("0.6.79", "u79", False)
    assert seen["known_issues_checked"]  # HQ's messages and known issues are kept beside GitHub's release


def test_update_is_owner_only(environment):
    api = environment()
    person = as_person(api, "riley")
    assert api.post("/api/v2/system/update", json={"version": "0.2.0"}, headers=person).status_code == 403
    assert api.get("/api/v2/system/update", headers=person).status_code == 403


def test_update_is_forwarded_to_the_updater_with_the_token(environment, monkeypatch):
    seen = []

    def updater(request):
        seen.append(
            (
                request.method,
                request.url.path,
                request.headers["authorization"],
                json.loads(request.content) if request.content else None,
            )
        )
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
        "configured": True,
        "state": "pulling",
        "from": "0.1.0",
        "to": "0.2.0",
        "message": "",
        "snapshot": "",
        "restored": False,
    }
    assert got["running"] == "0.1.9"  # the server's own record, beside the updater's view


def test_a_server_tico_updates_offers_no_update_now(environment, monkeypatch):
    """TICO_UPDATES=tico (Tico updates this server from outside): no Update now, and the app says why."""
    monkeypatch.setenv("TICO_UPDATES", "tico")
    monkeypatch.setenv("TICO_UPDATER_URL", "http://updater:9000")  # even an updater left in .env is not called
    network(monkeypatch, lambda request: pytest.fail("the updater was called"))
    releases.CHECKER.release = {"tag": "v0.2.0", "url": RELEASE["html_url"], "published_at": "", "name": ""}
    releases.CHECKER.checked = releases.CHECKER.clock()
    assert releases.notice()["managed"] is True and releases.notice()["available"] is True
    api = environment()
    r = api.post("/api/v2/system/update", json={"version": "v0.2.0"}, headers=signed_in())
    assert r.status_code == 409 and r.json()["error"]["code"] == "managed_update"
    assert "command" not in r.json()["error"]
    assert api.get("/api/v2/system/update", headers=signed_in()).json()["managed"] is True
    checks = api.get("/api/v2/health", headers=signed_in()).json()["checks"]
    version = next(c for c in checks if c["id"] == "version")
    assert version["status"] == "info" and version["summary"].endswith("Tico updates this server.")
    assert [f["label"] for f in version["fixes"]] == ["What is new"]
    monkeypatch.delenv("TICO_UPDATES")
    assert releases.notice()["managed"] is False
