"""Directory sync: Google and Graph fixtures, the safety rules, the audit trail and the routes."""
import json

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from backend import directory as D
from backend import directory_sources as S
from backend import people as P
from backend.tests.test_onboarding import OWNER_EMAIL, environment, signed_in  # noqa: F401
from backend.tests.test_people_access import person_headers

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
SERVICE_ACCOUNT = {"client_email": "tico@proj.iam.gserviceaccount.com", "private_key": KEY,
                   "token_uri": "https://oauth2.googleapis.com/token", "type": "service_account"}


def guser(email, name, **more):
    return {"id": "g-" + email.split("@")[0], "primaryEmail": email, "name": {"fullName": name},
            "orgUnitPath": "/", **more}


ENTRA = {"tenant": "tenant-1", "client_id": "app-1", "client_secret": "s3cret"}


def test_a_failed_listing_raises_instead_of_looking_like_an_empty_directory(monkeypatch):
    monkeypatch.setattr(S, "TRANSPORT", httpx.MockTransport(lambda r: httpx.Response(
        403, json={"error": {"message": "Insufficient privileges"}}) if r.url.host != "login.microsoftonline.com"
        else httpx.Response(200, json={"access_token": "t"})))
    with pytest.raises(Exception, match="Insufficient privileges"):
        S.entra_fetch(ENTRA, {})


# ------------------------------------------------------------------------------------ the engine
def person(pid, email, **more):
    return P._person({"id": pid, "name": pid.title(), "email": email, **more})


def rec(email, active=True, **more):
    return {"email": email, "name": more.pop("name", email.split("@")[0].title()), "title": "", "manager": "",
            "active": active, "external_id": "", **more}


def test_engine_only_leaves_people_its_feed_created_and_never_the_owner():
    roster = {"people": [person("owner", "owner@x.io"), person("hand", "hand@x.io"),
                         person("g", "g@x.io", directory="google"), person("e", "e@x.io", directory="entra"),
                         person("g2", "g2@x.io", directory="google")]}
    found = D.plan(roster, [rec("owner@x.io", active=False), rec("hand@x.io", active=False),
                            rec("e@x.io", active=False)], "google", "owner@x.io", True)
    # g and g2 are absent from the directory; owner, hand and the entra person are not ours to remove.
    assert sorted(x["email"] for x in found["leaves"]) == ["g2@x.io", "g@x.io"]
    assert found["protected"] == []
    roster["people"][0]["directory"] = "google"
    found = D.plan(roster, [rec("owner@x.io", active=False)], "google", "owner@x.io", True)
    assert "owner@x.io" not in [x["email"] for x in found["leaves"]]
    assert [x["email"] for x in found["protected"]] == ["owner@x.io"]


# ------------------------------------------------------------------------------------- the routes
def events(api, action):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute("SELECT * FROM events WHERE action=? ORDER BY ts, id", (action,))]


def put_config(api, revision=0, **more):
    body = {"source": "google", "filter": {}, "expected_revision": revision,
            "credentials": {"service_account_json": json.dumps(SERVICE_ACCOUNT), "admin_email": "admin@acme.example"},
            **more}
    r = api.put("/api/v2/directory", json=body, headers=signed_in())
    assert r.status_code == 200, r.text
    return r.json()["revision"]


def sync(api, **body):
    r = api.post("/api/v2/directory/sync", json=body, headers=signed_in())
    assert r.status_code == 200, r.text
    return r.json()


def roster_of(api):
    with api.app.state.store.read() as c:
        from backend.views import roster
        return {p["email"]: p for p in roster(c)["people"]}


@pytest.fixture
def google_api(environment, monkeypatch):
    users = {"users": [
        guser("ana@acme.example", "Ana Rivera"),
        guser("dev@acme.example", "Dev Patel", organizations=[{"title": "Engineer"}],
              relations=[{"type": "manager", "value": "ana@acme.example"}]),
        guser(OWNER_EMAIL, "Morgan Reed")]}
    state = {"users": users}

    def handler(request):
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "g-token"})
        if request.url.path.endswith("/users"):
            return httpx.Response(200, json=state["users"])
        if "/photos/thumbnail" in request.url.path:
            return httpx.Response(200, json={"photoData": "_-8", "mimeType": "image/png"})
        return httpx.Response(404, json={})
    monkeypatch.setattr(S, "TRANSPORT", httpx.MockTransport(handler))
    api = environment()
    state["api"] = api
    return api, state


def test_first_sync_previews_then_needs_the_owner_to_confirm_that_exact_plan(google_api):
    api, _ = google_api
    put_config(api)
    r = api.post("/api/v2/directory/preview", json={}, headers=signed_in())
    preview = r.json()
    assert preview["counts"]["adds"] == 2 and preview["needs_confirmation"]["first"] is True
    assert "ana@acme.example" not in roster_of(api)                          # a preview writes nothing
    held = sync(api)
    assert held["applied"] is False and "ana@acme.example" not in roster_of(api)
    wrong = sync(api, confirm=True, plan_hash="0" * 24)
    assert wrong["applied"] is False and wrong["stale"] is True
    done = sync(api, confirm=True, plan_hash=preview["plan"]["hash"])
    assert done["applied"] is True and done["done"]["adds"] == 2
    people = roster_of(api)
    assert people["dev@acme.example"]["title"] == "Engineer"
    assert people["dev@acme.example"]["reports_to"] == people["ana@acme.example"]["id"]
    assert people["dev@acme.example"]["directory"] == "google"
    assert len(events(api, "directory.person_added")) == 2
    synced = events(api, "directory.synced")
    assert len(synced) == 1 and json.loads(synced[0]["detail_json"])["applied"]["adds"] == 2
    assert sync(api)["applied"] is True                                       # confirmed once, then routine
    assert api.get("/api/v2/directory", headers=signed_in()).json()["confirmed"] is True


def confirmed_sync(api):
    preview = api.post("/api/v2/directory/preview", json={}, headers=signed_in()).json()
    return sync(api, confirm=True, plan_hash=preview["plan"]["hash"])


def test_suspended_people_are_marked_left_and_their_tokens_revoked_not_deleted(google_api):
    api, state = google_api
    put_config(api)
    confirmed_sync(api)
    dev = roster_of(api)["dev@acme.example"]
    headers = person_headers(api, dev["id"])
    assert api.get("/api/me", headers=headers).status_code == 200
    state["users"]["users"][1]["suspended"] = True
    out = sync(api)
    assert out["applied"] is True and out["done"]["leaves"] == 1
    after = roster_of(api)["dev@acme.example"]
    assert after["hidden"] is True and after["directory_left"] is True
    with api.app.state.store.read() as c:
        assert c.execute("SELECT COUNT(*) FROM human_tokens WHERE human=? AND revoked_at IS NULL", (dev["id"],)).fetchone()[0] == 0
    assert api.get("/api/me", headers=headers).status_code in (401, 403)
    left = events(api, "directory.person_left")
    assert json.loads(left[0]["detail_json"])["reason"] == "disabled in the directory"
    state["users"]["users"][1]["suspended"] = False
    assert sync(api)["done"]["restores"] == 1
    assert roster_of(api)["dev@acme.example"]["hidden"] is False


def test_credentials_are_encrypted_and_never_returned(google_api):
    api, _ = google_api
    put_config(api)
    with api.app.state.store.read() as c:
        row = c.execute("SELECT * FROM directory_credentials WHERE source='google'").fetchone()
    assert b"PRIVATE KEY" not in row["ciphertext"] and b"tico@proj" not in row["ciphertext"]
    view = api.get("/api/v2/directory", headers=signed_in())
    assert "PRIVATE KEY" not in view.text
    assert view.json()["credentials"]["google"] == {"configured": True, "hint": "tico@proj.iam.gserviceaccount.com as admin@acme.example"}
    config = events(api, "directory.configured")
    assert config and "PRIVATE KEY" not in config[0]["detail_json"]


def test_only_the_owner_manages_directory_sync_and_stale_revisions_are_refused(google_api):
    api, _ = google_api
    riley = person_headers(api, "riley")
    assert api.get("/api/v2/directory", headers=riley).status_code == 403
    assert api.post("/api/v2/directory/sync", json={}, headers=riley).status_code == 403
    put_config(api)
    r = api.put("/api/v2/directory", json={"source": "", "expected_revision": 0}, headers=signed_in())
    assert r.status_code == 409

