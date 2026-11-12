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
    assert checker.view("0.5.300")["latest"] == "0.5.216"  # a stable build ahead of it still shows it


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
