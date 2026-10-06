"""A company's own frontend on another origin: CORS, the sign-in redirect allowlist, and the
bearer session it earns through the OIDC flow (docs/custom-frontend.md)."""

import base64
import hashlib
import json
from urllib.parse import parse_qs, urlparse

import pytest
import yaml
from fastapi.testclient import TestClient

from backend import cors
from backend.app import create_app
from backend.auth import Identity
from backend.config import Settings
from backend.store import H, encode
from backend.tests.test_api import headers
from backend.tests.test_oidc import provider, signin  # noqa: F401  (fixtures)

APP = "http://localhost:5173"
PROD = "https://app.acme.example"
EVIL = "https://evil.example"


@pytest.fixture
def api(tmp_path):
    """The api fixture of test_api, with a frontend allowed to call it. Fixtures that ask for `api` get this one."""
    registry = tmp_path / "hub-registry"
    registry.mkdir()
    (registry / "hub-access.yaml").write_text(yaml.safe_dump({"owner": "ana@acme.example", "bot_admins": ["ben@acme.example"]}))
    app = create_app(Settings(db_path=tmp_path / "hub.db", registry_dir=registry, cors_origins=APP + ", " + PROD, test_identities={
        "ana-test": Identity("human:ana", "owner", "ana@acme.example"),
        "ben-test": Identity("human:ben", "human", "ben@acme.example")}))
    with TestClient(app) as client:
        with app.state.store.transaction() as c:
            H.sync_registry(c, {"ops": {"name": "ops", "runtime": "fake", "status": "active"}},
                            {"people": [{"id": p, "email": p + "@acme.example"} for p in ("ana", "ben")]})
            c.execute("INSERT INTO registry_metadata VALUES('people',?)", (encode({"people": [
                {"id": "ana", "email": "ana@acme.example", "primary_for": ["*"]},
                {"id": "ben", "email": "ben@acme.example"}]}),))
            c.execute("INSERT INTO bot_config(bot,config_json,operator) VALUES('ops',?,'ana')",
                      (encode({"name": "ops", "runtime": "fake", "status": "active"}),))
            c.execute("INSERT INTO registry_metadata VALUES('onboarding',?)", (encode({"completed": "2026-01-01T00:00:00Z"}),))
        yield client


def challenge_for(verifier):
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


VERIFIER = "v" * 64
CHALLENGE = challenge_for(VERIFIER)


# ---- CORS -------------------------------------------------------------------------------

def test_cors_answers_allowed_origins_only(api):
    r = api.get("/api/v2/me", headers={**headers(), "Origin": APP})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == APP
    assert r.headers["access-control-allow-credentials"] == "true"
    for origin in (EVIL, "https://sub.app.acme.example"):
        r = api.get("/api/v2/me", headers={**headers(), "Origin": origin})
        assert not [k for k in r.headers if k.lower().startswith("access-control-")], origin
    ask = {"Origin": EVIL, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization"}
    bad = api.options("/api/v2/tasks", headers=ask)
    assert bad.status_code in (400, 401) and not [k for k in bad.headers if k.lower().startswith("access-control-")]
    refused = api.post("/api/v2/chat/ops", json={"text": "hello"}, headers={**headers(), "Origin": EVIL})
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "origin"


@pytest.mark.parametrize("value", ["*", "https://app.acme.example/x"])
def test_the_allowlist_takes_exact_origins_only(value):
    with pytest.raises(RuntimeError, match="TICO_CORS_ORIGINS"):
        cors.parse(value)


# ---- sign-in for a frontend on another origin -----------------------------------------------

def start(signin, target=APP + "/", challenge=CHALLENGE, **query):
    return signin.api.get("/auth/login", params={"next": target, **({"code_challenge": challenge} if challenge else {}), **query},
                          follow_redirects=False)


def sign_in(signin, target=APP + "/", claims=None):
    began = start(signin, target)
    assert began.status_code == 302, began.text
    code, state = signin.fake.approve(began.headers["location"], **(claims or {}))
    return signin.api.get("/auth/callback", params={"code": code, "state": state}, follow_redirects=False)


def tico_code(response):
    assert response.status_code == 302, response.text
    location = urlparse(response.headers["location"])
    return location, parse_qs(location.fragment)["tico_code"][0]


def exchange(signin, code, verifier=VERIFIER, origin=APP):
    return signin.api.post("/auth/token", json={"code": code, "code_verifier": verifier},
                           headers={"Origin": origin} if origin else {})


def test_a_frontend_signs_in_and_gets_a_code_then_a_bearer_session(signin):
    done = sign_in(signin, APP + "/app/?tab=tasks")
    location, code = tico_code(done)
    assert (location.scheme, location.netloc, location.path, location.query) == ("http", "localhost:5173", "/app/", "tab=tasks")
    assert "tico_session" not in done.headers.get("set-cookie", "") and signin.sessions() == []      # no cookie, no session yet
    assert me_status(signin, {}) == 401

    made = exchange(signin, code)
    assert made.status_code == 200, made.text
    assert made.headers["access-control-allow-origin"] == APP and made.headers["cache-control"].startswith("no-store")
    token = made.json()
    assert token["token_type"] == "Bearer" and token["access_token"].startswith("tico_st_") and token["person"] == "ben"
    assert token["expires_in"] == signin.settings.session_absolute_seconds
    bearer = {"Authorization": "Bearer " + token["access_token"], "Origin": APP}
    who = signin.api.get("/api/v2/me", headers=bearer)
    assert who.status_code == 200 and who.json()["actor"] == "human:ben" and who.json()["role"] == "human"
    assert who.headers["access-control-allow-credentials"] == "true"
    # Only a hash is kept, and the session answers as a cookie session would.
    assert token["access_token"][len("tico_st_"):] not in json.dumps([dict(r) for r in signin.sessions()])
    signin.api.cookies.clear()
    assert signin.api.get("/api/v2/needs-you", headers=bearer).status_code == 200

    out = signin.api.post("/auth/token/revoke", headers=bearer)
    assert out.status_code == 200 and out.json() == {"revoked": True}
    assert signin.api.get("/api/v2/me", headers=bearer).status_code == 401
    assert signin.sessions() == []


def me_status(signin, extra):
    return signin.api.get("/api/v2/me", headers=extra).status_code


def test_the_code_is_single_use_and_bound_to_the_verifier_and_the_origin(signin):
    _, code = tico_code(sign_in(signin))
    assert exchange(signin, code, verifier="w" * 64).status_code == 400          # wrong verifier burns the code
    assert exchange(signin, code).status_code == 400
    _, code = tico_code(sign_in(signin))
    assert exchange(signin, code, origin=EVIL).status_code == 400
    assert signin.sessions() == []


@pytest.mark.parametrize("target", [EVIL + "/", "https://app.acme.example@evil.example/"])
def test_an_open_redirect_is_refused(signin, target):
    began = start(signin, target)
    assert began.status_code == 400 and "location" not in began.headers and "set-cookie" not in began.headers
    assert signin.fake.codes == {}


def test_a_frontend_must_send_a_pkce_challenge(signin):
    assert start(signin, challenge="short").status_code == 400
    assert start(signin, PROD + "/").status_code == 302
