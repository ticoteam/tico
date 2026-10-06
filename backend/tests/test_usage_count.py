"""The anonymous usage count (PRIVACY.md): what is sent, and every way it is not."""

import sqlite3
from types import SimpleNamespace

import httpx
import pytest

from backend import census, releases
from backend.store import H
from backend.tests.test_onboarding import as_person, environment, signed_in  # noqa: F401

RELEASE = {"tag_name": "v0.2.0", "html_url": "https://github.com/ticoteam/tico/releases/tag/v0.2.0",
           "published_at": "2026-10-20T10:00:00Z", "name": "Tico 0.2.0"}
ALLOWED = {"install_id", "version", "active_people", "active_bots"}


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for name in ("TICO_TELEMETRY", "DO_NOT_TRACK", "TICO_TELEMETRY_DEBUG", "TICO_RELEASES_URL", "TICO_HQ_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TICO_UPDATE_CHECK", "on")
    monkeypatch.setenv("TICO_VERSION", "0.2.13")


def counting(api):
    """The install, with its owner shown the notice (nothing is counted before that)."""
    api.post("/api/v2/system/usage-count/notice", json={"state": "shown"}, headers=signed_in())
    return api.app.state.census


def complete_a_bot_turn(api, when):
    with sqlite3.connect(str(api.app.state.store.settings.db_path)) as c:
        c.execute("INSERT INTO attempts(id,job_id,bot,runner_id,generation,token_hash,state,lease_until,created,finished) "
                  "VALUES('a1','j1','coo','r1',1,'h','completed',?,?,?)", (when, when, when))


def wire(monkeypatch, api, hq=True):
    """Every request the release check makes, with HQ answering (or not) and GitHub always answering."""
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.host == "updates.tico.team" and not hq:
            raise httpx.ConnectTimeout("down")
        return httpx.Response(200, json=RELEASE)
    monkeypatch.setattr(releases, "TRANSPORT", httpx.MockTransport(handler))
    checker = releases.Checker()
    checker.bind(api.app.state.census)
    return checker, seen


def test_the_payload_is_exactly_the_allowed_fields(environment):
    api = environment()
    c = counting(api)
    with api.app.state.store.read() as read:
        first = c.payload(read, "0.2.13")
    assert set(first) == ALLOWED
    # The owner's own request just now is a person using the app; no bot has finished a turn.
    assert first["active_people"] is True and first["active_bots"] is False
    assert len(first["install_id"]) == 36 and first["version"] == "0.2.13"
    complete_a_bot_turn(api, H.now())
    with api.app.state.store.read() as read:
        second = c.payload(read, "0.2.13")
    assert set(second) == ALLOWED and second["active_people"] and second["active_bots"]
    assert second["install_id"] == first["install_id"]
    # Older than a week is not "active", and a token or an Assistant acting for a person is not a sign-in.
    old = H.shift(H.now(), days=-8)
    with sqlite3.connect(str(api.app.state.store.settings.db_path)) as raw:
        raw.execute("UPDATE attempts SET finished=?", (old,))
        raw.execute("UPDATE registry_metadata SET value_json=json_set(value_json,'$.last_person',?) WHERE key=?",
                    (old, census.KEY))
    with api.app.state.store.read() as read:
        assert not c.payload(read, "0.2.13")["active_bots"] and not c.payload(read, "0.2.13")["active_people"]
    assert not c.person_due(SimpleNamespace(role="human", via="assistant", via_token=False))
    assert not c.person_due(SimpleNamespace(role="human", via="", via_token=True))


@pytest.mark.parametrize("how", ["TICO_TELEMETRY", "toggle"])
def test_when_off_the_check_goes_to_github_with_no_id(environment, monkeypatch, how):
    api = environment()
    counting(api)
    if how == "TICO_TELEMETRY":
        monkeypatch.setenv("TICO_TELEMETRY", "off")
    elif how == "toggle":
        assert api.put("/api/v2/system/usage-count", json={"enabled": False}, headers=signed_in()).status_code == 200
    checker, seen = wire(monkeypatch, api)
    checker.refresh()
    assert [r.url.host for r in seen] == ["api.github.com"]
    assert not seen[0].url.query and "install" not in str(seen[0].headers)
    assert checker.view("0.2.13")["latest"] == "0.2.0"     # the update check itself still works


def test_when_on_hq_gets_the_four_fields_and_a_dead_hq_falls_back_silently(environment, monkeypatch):
    api = environment()
    counting(api)
    checker, seen = wire(monkeypatch, api)
    checker.refresh()
    assert [r.url.host for r in seen] == ["updates.tico.team"] and seen[0].url.path == "/v1/latest"
    assert set(seen[0].url.params) == ALLOWED
    assert seen[0].url.params["active_people"] == "true" and seen[0].url.params["active_bots"] == "false"
    assert checker.view("0.2.13")["latest"] == "0.2.0"
    checker, seen = wire(monkeypatch, api, hq=False)
    checker.refresh()
    assert [r.url.host for r in seen] == ["updates.tico.team", "api.github.com"]
    assert checker.view("0.2.13")["latest"] == "0.2.0"


def test_the_owner_alone_sees_and_changes_it_and_can_reset_the_id(environment):
    api = environment()
    person = as_person(api, "riley")
    assert api.get("/api/v2/config", headers=signed_in()).json()["usage_count_notice"] is True
    assert api.get("/api/v2/config", headers=person).json()["usage_count_notice"] is False
    for call in (lambda h: api.get("/api/v2/system/usage-count", headers=h),
                 lambda h: api.put("/api/v2/system/usage-count", json={"enabled": False}, headers=h),
                 lambda h: api.post("/api/v2/system/usage-count/reset", headers=h)):
        assert call(person).status_code == 403
    c = counting(api)
    with api.app.state.store.read() as read:
        before = c.payload(read, "0.2.13")["install_id"]
    after = api.post("/api/v2/system/usage-count/reset", headers=signed_in()).json()["install_id"]
    assert after != before and len(after) == 36
