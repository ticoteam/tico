"""Tico sees which Tico Recorder each person runs (backend/recorder_installs.py), through the real routes."""

import httpx
import pytest

from backend import hubdb as H, recorder_installs as R, releases
from backend.tests.test_api import api, headers  # noqa: F401  (the api fixture)
from backend.tests.test_openapi_v2 import conforms

MAC = "a" * 32
LAPTOP = "b" * 32


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """Nothing reaches GitHub: the feed answers what a test says, and unasked the check is off."""
    monkeypatch.setattr(R, "LATEST", R.Latest())
    monkeypatch.setattr(R, "TRANSPORT", httpx.MockTransport(lambda request: httpx.Response(503)))
    monkeypatch.setattr(releases, "TRANSPORT", httpx.MockTransport(lambda request: httpx.Response(503)))


def feed(monkeypatch, version):
    """The updater feed says `version` now; the cache is filled the way the background fetch fills it."""
    monkeypatch.setenv("TICO_UPDATE_CHECK", "on")
    asked = []

    def answer(request):
        asked.append(str(request.url))
        return httpx.Response(200, json={"version": version, "platforms": {}})
    monkeypatch.setattr(R, "TRANSPORT", httpx.MockTransport(answer))
    R.LATEST.refresh()
    return asked


def recorder(token="ben-test", version="0.2.0", install=MAC):
    return {**headers(token), "X-Tico-Recorder-Version": version, "X-Tico-Recorder-Install": install}


def rows(api, person="ben"):
    with api.app.state.store.read() as c:
        return {r["install_id"]: dict(r) for r in c.execute(
            "SELECT * FROM recorder_installs WHERE person_id=?", (person,))}


def mine(api, token="ben-test"):
    r = api.get("/api/v2/meetings/recorder", headers=headers(token))
    assert r.status_code == 200, r.text
    return r.json()


def test_calls_write_once_per_install_until_the_version_changes_or_ten_minutes_pass(api):
    seen = api.app.state.recorder_seen
    clock = [1000.0]
    seen.clock = lambda: clock[0]
    assert api.get("/api/v2/meetings/settings", headers=recorder()).status_code == 200
    first = rows(api)[MAC]
    assert first["version"] == "0.2.0" and first["created_at"] == first["last_seen_at"]
    # A Recorder polling every few seconds writes nothing more.
    clock[0] += 590
    for _ in range(3):
        assert api.get("/api/v2/meetings/settings", headers=recorder()).status_code == 200
    assert rows(api)[MAC]["last_seen_at"] == first["last_seen_at"]
    # A new version is written at once, and another copy of the same person is its own row.
    api.get("/api/v2/meetings/settings", headers=recorder(version="0.3.1"))
    api.get("/api/v2/meetings/settings", headers=recorder(install=LAPTOP))
    now = rows(api)
    assert now[MAC]["version"] == "0.3.1" and now[MAC]["created_at"] == first["created_at"]
    assert now[LAPTOP]["version"] == "0.2.0"
    # Ten minutes later the same version is noted again.
    before = now[MAC]["last_seen_at"]
    clock[0] += 601
    api.get("/api/v2/meetings/settings", headers=recorder(version="0.3.1"))
    assert rows(api)[MAC]["last_seen_at"] > before


def test_only_well_formed_recorder_headers_from_a_person_are_kept(api):
    for version, install in (("0.2.0", "not-an-id"), ("latest", MAC), ("0.2.0\nX: y", MAC), ("", MAC), ("0.2.0", "")):
        api.get("/api/v2/meetings/settings", headers=recorder(version=version, install=install))
    assert rows(api) == {}
    # Plain browser calls carry neither header and write nothing.
    assert api.get("/api/v2/meetings/settings", headers=headers("ben-test")).status_code == 200
    assert rows(api) == {}
    # A person's install id is theirs alone: the same id from someone else is a separate row.
    api.get("/api/v2/meetings/settings", headers=recorder(token="cara-test"))
    assert rows(api) == {} and set(rows(api, "cara")) == {MAC}


def test_the_newest_version_comes_from_the_updater_feed_and_a_failed_fetch_says_nothing(api, monkeypatch):
    # Update checks off: nothing is fetched and nothing is offered.
    api.get("/api/v2/meetings/settings", headers=recorder())
    assert mine(api)["latest"] == "" and mine(api)["update_available"] is False
    asked = feed(monkeypatch, "0.3.0")
    assert asked == [R.FEED_URL]
    assert R.LATEST.version == "0.3.0"
    for answer in (lambda request: httpx.Response(500), lambda request: httpx.Response(200, text="<html>"),
                   lambda request: httpx.Response(200, json={"version": "nightly"}),
                   lambda request: (_ for _ in ()).throw(httpx.ConnectError("offline"))):
        monkeypatch.setattr(R, "TRANSPORT", httpx.MockTransport(answer))
        R.LATEST.refresh()
        # The last good answer stays, the next try waits, and the page still loads.
        assert R.LATEST.version == "0.3.0" and R.LATEST.retry_at > R.LATEST.checked
        assert mine(api)["latest"] == "0.3.0"
    # The cache holds for an hour: reads in between never fetch.
    monkeypatch.setattr(R, "TRANSPORT", httpx.MockTransport(lambda request: pytest.fail("fetched again")))
    R.LATEST.retry_at = 0.0
    assert mine(api)["update_available"] is True


def test_a_person_sees_the_download_until_a_copy_is_seen_and_the_update_line_only_when_behind(api, monkeypatch):
    feed(monkeypatch, "0.3.0")
    nothing = mine(api)
    assert nothing == {"version": "", "last_seen_at": None, "recent": False, "latest": "0.3.0",
                       "update_available": False, "download_url": R.DOWNLOAD_URL}
    api.get("/api/v2/meetings/settings", headers=recorder(version="0.2.9"))
    behind = mine(api)
    assert behind["recent"] and behind["version"] == "0.2.9" and behind["update_available"]
    document = api.get("/api/v2/openapi.json", headers=headers()).json()
    schema = document["paths"]["/api/v2/meetings/recorder"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert conforms(behind, schema, document) is None
    # The copy used last decides: a laptop on the newest one ends the line, though the old Mac is still listed.
    api.get("/api/v2/meetings/settings", headers=recorder(version="0.3.0", install=LAPTOP))
    assert mine(api)["update_available"] is False and mine(api)["version"] == "0.3.0"
    # Someone else's Recorder is not yours.
    assert mine(api, "cara-test")["version"] == ""
    # A copy not seen for over a month brings the download back.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE recorder_installs SET last_seen_at=? WHERE person_id='ben'", (H.shift(H.now(), days=-31),))
    assert mine(api)["recent"] is False and mine(api)["version"] == "0.3.0"


def test_owners_and_admins_see_each_persons_recorder_version_in_people(api, monkeypatch):
    feed(monkeypatch, "0.3.0")
    api.get("/api/v2/meetings/settings", headers=recorder(version="0.2.0"))
    api.get("/api/v2/meetings/settings", headers=recorder(token="cara-test", version="0.3.0"))
    view = api.get("/api/v2/access", headers=headers("ana-test")).json()
    assert view["recorders"]["ben"]["version"] == "0.2.0" and view["recorders"]["ben"]["update_available"] is True
    assert view["recorders"]["cara"]["update_available"] is False and "ana" not in view["recorders"]
    # A member does not read the roster, Recorder versions included.
    assert api.get("/api/v2/access", headers=headers("cara-test")).status_code == 403


def test_a_person_keeps_at_most_twenty_copies(api):
    seen = api.app.state.recorder_seen
    for n in range(R.KEEP + 3):
        seen.note("ben", f"{n:032x}", "0.2.0")
    kept = rows(api)
    assert len(kept) == R.KEEP and f"{R.KEEP + 2:032x}" in kept and f"{0:032x}" not in kept
    # Someone not on the roster is never written.
    seen.note("nobody", MAC, "0.2.0")
    assert rows(api, "nobody") == {}
