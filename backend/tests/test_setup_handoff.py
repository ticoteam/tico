"""The setup handoff: the owner's coding agent trades a one-time secret from the installer for a personal token of the
owner, once, before the owner has signed in, and the secret is stored as a hash only."""

import logging
import secrets

import pytest

from fastapi.testclient import TestClient

from backend import onboarding as O, setup_handoff
from backend.app import create_app
from backend.config import Settings
from backend.store import H, digest
from backend.tests.helpers.api import api_settings, get, headers, post  # noqa: F401  (api: the fixture)
from backend.tests.helpers.mcp import call, rpc

OWNER, ADMIN = "ana-test", "ben-test"
PATH = "/api/v2/setup/handoff"


@pytest.fixture
def handoff(api):  # noqa: F811
    """The api fixture's server, given a handoff the way the installer's hash would on first start."""
    settings = api.app.state.auth.settings
    secret = secrets.token_urlsafe(32)
    settings.setup_handoff_hash = digest(secret)
    setup_handoff.place_first(settings, api.app.state.store)

    def trade(value=None, **kw):
        return api.post(PATH, json={"secret": secret if value is None else value}, **kw)

    api.secret, api.trade, api.settings = secret, trade, settings
    return api


def rows(api):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute("SELECT * FROM setup_handoffs")]


def test_the_secret_is_traded_once_for_the_owners_token_before_the_owner_has_signed_in(handoff):
    # No sign-in of any kind: the secret is the only credential, and nobody has opened a session.
    with handoff.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM oidc_sessions").fetchone()[0] == 0
    r = handoff.trade()
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    body = r.json()
    assert set(body) == {"token", "token_id", "label", "expires_at", "mcp_url", "team_url"}
    assert body["token"].startswith("tico_pt_") and len(body["token"]) >= 48
    assert body["label"] == f"Setup agent · {H.now()[:10]}"
    assert body["mcp_url"] == handoff.settings.runner_url + "/api/v2/mcp"
    assert body["team_url"] == handoff.settings.public_url
    assert H.shift(H.now(), days=89, hours=23) < body["expires_at"] <= H.shift(H.now(), days=90)
    # The token is the owner: the same rights a signed-in owner has, on the web API and through MCP.
    who = handoff.get("/api/v2/me", headers=headers(body["token"])).json()
    assert who["role"] == "owner" and who["email"] == "ana@acme.example" and who["actor"] == "human:ana"
    assert rpc(handoff, "tools/list", token=body["token"])["result"]["tools"]
    # It is listed with the owner's other API tokens, named for what it is, and revocable like them.
    listed = get(handoff, "me/tokens", token=OWNER)["tokens"]
    assert [(t["id"], t["label"]) for t in listed] == [(body["token_id"], body["label"])]
    post(handoff, "me/tokens/" + body["token_id"] + "/revoke", {}, token=OWNER)
    assert handoff.get("/api/v2/me", headers=headers(body["token"])).status_code == 401


def test_a_second_trade_a_wrong_secret_and_a_malformed_one_are_all_refused_the_same_way(handoff):
    assert handoff.trade().status_code == 200
    again = handoff.trade()
    assert again.status_code == 410 and again.json()["error"]["code"] == "gone"
    wrong = handoff.trade(secrets.token_urlsafe(32))
    short = handoff.trade("short")
    empty = handoff.post(PATH, json={})
    assert {r.status_code for r in (wrong, short, empty)} == {410}
    assert {r.text for r in (again, wrong, short, empty)} == {again.text}
    assert handoff.secret not in again.text
    # Only the one token was ever made.
    with handoff.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM human_tokens").fetchone()[0] == 1
    assert handoff.post(PATH, json={"secret": handoff.secret, "more": 1}).status_code == 422


def test_it_lasts_a_week_from_the_first_start_and_not_longer(handoff):
    [row] = rows(handoff)
    created = row["created_at"]
    assert H.shift(created, days=7) == row["expires_at"]
    assert view(handoff)["state"] == "waiting"
    with handoff.app.state.store.transaction() as c:
        c.execute("UPDATE setup_handoffs SET expires_at=?", (H.shift(H.now(), seconds=-1),))
    assert view(handoff)["state"] == "expired"
    assert handoff.trade().status_code == 410
    with handoff.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM human_tokens").fetchone()[0] == 0


def view(api):
    return get(api, "setup/handoff", token=OWNER)


def test_the_owner_turns_it_off_before_it_is_used_and_it_is_refused(handoff):
    assert view(handoff)["state"] == "waiting"
    off = post(handoff, "setup/handoff/off", {}, token=OWNER)
    assert off["state"] == "off" and off["token_revoked"] is False
    assert handoff.trade().status_code == 410
    assert view(handoff)["state"] == "off"
    # A restart (the same hash in the environment) does not bring it back.
    setup_handoff.place_first(handoff.settings, handoff.app.state.store)
    assert handoff.trade().status_code == 410 and len(rows(handoff)) == 1


def test_turning_it_off_after_use_also_stops_the_agents_token(handoff):
    made = handoff.trade().json()
    status = view(handoff)
    assert status["state"] == "used" and status["token_id"] == made["token_id"] and status["used_at"]
    # The agent's token reads the status but cannot turn the handoff off: a token revokes no token.
    assert handoff.get(PATH, headers=headers(made["token"])).json()["state"] == "used"
    r = handoff.post(PATH + "/off", json={}, headers=headers(made["token"]))
    assert r.status_code == 403 and "signed-in browser" in r.text
    assert handoff.get("/api/v2/me", headers=headers(made["token"])).status_code == 200
    off = post(handoff, "setup/handoff/off", {}, token=OWNER)
    assert off["state"] == "off" and off["token_revoked"] is True
    assert handoff.get("/api/v2/me", headers=headers(made["token"])).status_code == 401
    # Again changes nothing and is not an error.
    assert post(handoff, "setup/handoff/off", {}, token=OWNER)["token_revoked"] is False


def test_only_the_owner_sees_or_turns_off_the_handoff(handoff):
    for method, path in (("get", PATH), ("post", PATH + "/off")):
        kw = {"json": {}} if method == "post" else {}
        assert getattr(handoff, method)(path, headers=headers(ADMIN), **kw).status_code == 403
        assert getattr(handoff, method)(path, **kw).status_code == 401
    assert view(handoff)["state"] == "waiting"


def test_a_server_without_a_handoff_answers_not_found_and_status_says_none(api):  # noqa: F811
    assert api.post(PATH, json={"secret": secrets.token_urlsafe(32)}).status_code == 404
    assert get(api, "setup/handoff", token=OWNER) == {"state": "none"}


def test_wrong_tries_per_address_are_limited_even_for_the_right_secret(handoff):
    for _ in range(10):
        assert handoff.trade(secrets.token_urlsafe(32)).status_code == 410
    blocked = handoff.trade()
    assert blocked.status_code == 429 and blocked.json()["error"]["code"] == "rate_limited"
    with handoff.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM human_tokens").fetchone()[0] == 0


def test_only_the_hash_is_stored_and_the_secret_is_never_logged_or_written_to_the_audit_trail(handoff, caplog):
    caplog.set_level(logging.DEBUG)
    made = handoff.trade().json()
    handoff.trade()  # the refused retry
    with handoff.app.state.store.read() as c:
        stored = " ".join(" ".join(str(v) for v in tuple(r)) for r in c.execute("SELECT * FROM setup_handoffs"))
        tokens = " ".join(" ".join(str(v) for v in tuple(r)) for r in c.execute("SELECT * FROM human_tokens"))
        events = [
            dict(r) for r in c.execute("SELECT actor,action,target,detail FROM events WHERE action LIKE 'setup.%'")
        ]
    assert handoff.secret not in stored and digest(handoff.secret) in stored
    assert made["token"] not in tokens + stored
    assert [e["action"] for e in events] == ["setup.handoff"] and events[0]["actor"] == "human:ana"
    assert handoff.secret not in str(events) and made["token"] not in str(events)
    assert handoff.secret not in caplog.text and made["token"] not in caplog.text


def test_an_owner_with_no_person_row_yet_gets_it_the_way_a_first_sign_in_would_make_it(tmp_path):
    """A bare server: nobody has signed in and the roster is empty, so the owner has no person row."""
    settings = api_settings(tmp_path)
    secret = secrets.token_urlsafe(32)
    settings.setup_handoff_hash = digest(secret)
    app = create_app(settings)
    with TestClient(app) as client:
        setup_handoff.place_first(settings, app.state.store)
        with app.state.store.read() as c:
            assert not c.execute("SELECT 1 FROM humans WHERE lower(email)=?", ("ana@acme.example",)).fetchone()
        made = client.post(PATH, json={"secret": secret}).json()
        with app.state.store.read() as c:
            person = c.execute("SELECT id FROM humans WHERE lower(email)=?", ("ana@acme.example",)).fetchone()
            held = c.execute("SELECT human FROM human_tokens WHERE id=?", (made["token_id"],)).fetchone()
        assert person and held["human"] == person["id"]
        who = client.get("/api/v2/me", headers=headers(made["token"])).json()
        assert who["role"] == "owner" and who["email"] == "ana@acme.example" and who["actor"] == "human:" + person["id"]


def test_the_first_start_places_it_once_and_a_second_hash_does_not_replace_a_live_one(handoff):
    other = digest(secrets.token_urlsafe(32))
    handoff.settings.setup_handoff_hash = other
    setup_handoff.place_first(handoff.settings, handoff.app.state.store)
    assert [r["token_hash"] for r in rows(handoff)] == [digest(handoff.secret)]
    assert handoff.trade().status_code == 200


def test_the_server_refuses_to_start_with_a_hash_it_cannot_use(tmp_path):
    good = digest("x" * 40)
    base = dict(db_path=tmp_path / "hub.db", public_url="https://acme.example.com", owner_email="ana@acme.example")
    assert Settings(**base, setup_handoff_hash=good).setup_handoff_hash == good
    with pytest.raises(RuntimeError, match="64-character sha256"):
        Settings(**base, setup_handoff_hash="not-a-hash")
    with pytest.raises(RuntimeError, match="https"):
        Settings(**{**base, "public_url": "http://acme.example.com"}, setup_handoff_hash=good)
    with pytest.raises(RuntimeError, match="https"):
        Settings(**{**base, "public_url": "https://localhost:8765"}, setup_handoff_hash=good)
    with pytest.raises(RuntimeError, match="TICO_OWNER_EMAIL"):
        Settings(**{**base, "owner_email": ""}, setup_handoff_hash=good)


def test_the_environment_hash_is_read_from_tico_setup_handoff_hash(monkeypatch, tmp_path):
    good = digest("y" * 40)
    monkeypatch.setenv("TICO_SETUP_HANDOFF_HASH", good.upper())
    monkeypatch.setenv("TICO_PUBLIC_URL", "https://acme.example.com")
    monkeypatch.setenv("TICO_OWNER_EMAIL", "ana@acme.example")
    monkeypatch.setenv("TICO_DB", str(tmp_path / "hub.db"))
    # A reachable server needs a sign-in; the owner link is the simplest the environment can name.
    monkeypatch.setenv("TICO_AUTH_PROXY", "owner-link")
    monkeypatch.setenv("TICO_HOSTED", "own")
    monkeypatch.setenv("TICO_OWNER_LINK_HASH", digest("z" * 40))
    assert Settings.from_env().setup_handoff_hash == good


@pytest.fixture
def bare(tmp_path):
    """A brand-new server with a handoff: nobody has signed in, the welcome wizard is untouched and the roster is empty."""
    settings = api_settings(tmp_path)
    secret = secrets.token_urlsafe(32)
    settings.setup_handoff_hash = digest(secret)
    app = create_app(settings)
    with TestClient(app) as client:
        setup_handoff.place_first(settings, app.state.store)
        client.token = client.post(PATH, json={"secret": secret}).json()["token"]
        yield client


def test_the_agent_finishes_setup_through_the_token_with_the_hub_setup_tools(bare):
    token = bare.token
    names = {t["name"] for t in rpc(bare, "tools/list", token=token)["result"]["tools"]}
    assert {"tico_setup_show", "tico_setup_complete", "tico_setup_invite", "tico_api"} <= names
    # The company file from setup names Dee (Settings > People > From your setup).
    with bare.app.state.store.transaction() as c:
        record = O.load(c)
        record["company_import"] = {
            "people": [{"id": "dee", "name": "Dee", "title": "Designer", "seats": [], "person": None}]
        }
        bare.app.state.onboarding._store(c, record, "human:ana")

    err, show = call(bare, "tico_setup_show", {}, token=token)
    assert not err and show["completed"] is None and show["needed"] is True
    assert [(p["id"], p["person"]) for p in show["company_import"]["people"]] == [("dee", None)]
    assert show["getting_started"]["items"]

    # An unknown key is a plain not found. The person from the file goes on the roster with the email the agent gives.
    err, refused = call(bare, "tico_setup_invite", {"person_key": "nobody", "email": "x@acme.example"}, token=token)
    assert err and "not_found" in str(refused)
    err, invited = call(bare, "tico_setup_invite", {"person_key": "dee", "email": "dee@acme.example"}, token=token)
    # This test install signs in another way, so no invitation email can go: the agent is told so, plainly.
    assert err and "invite_unavailable" in str(invited)
    with bare.app.state.store.read() as c:
        assert c.execute("SELECT 1 FROM humans WHERE lower(email)='dee@acme.example'").fetchone()

    # The wizard's picks are saved through the generic door, then finished by the tool; finishing twice keeps the first.
    err, done = call(bare, "tico_setup_complete", {}, token=token)
    assert not err and done["completed"] and done["needed"] is False, done
    first = done["completed"]
    err, again = call(bare, "tico_setup_complete", {}, token=token)
    assert not err and again["completed"] == first
    err, show = call(bare, "tico_setup_show", {}, token=token)
    assert not err and show["completed"] == first and show["needed"] is False


def test_an_admin_reads_setup_through_the_tools_but_only_the_owner_changes_it(handoff):
    admin = [t["name"] for t in rpc(handoff, "tools/list", token=ADMIN)["result"]["tools"]]
    assert "tico_setup_show" in admin and "tico_setup_complete" not in admin and "tico_setup_invite" not in admin
    err, show = call(handoff, "tico_setup_show", {}, token=ADMIN)
    assert not err and "completed" in show
