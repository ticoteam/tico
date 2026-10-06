"""One backend process serves one company, named and signed in by configuration alone.

Nothing here reads Acme's registry: the environment is Acme, its app is Atlas, and its owner
is Morgan, so a value that leaked back into the code would fail rather than pass by accident.
"""

import uuid

import pytest
import yaml
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.config import Settings
from backend.store import H, encode

COMPANY = {"environment_id": "acme-7", "company_name": "Acme", "app_name": "Atlas",
           "assistant_name": "Morgan", "assistant_bot": "coo", "github_owner": "AcmeCorp"}
OWNER_EMAIL = "Morgan@ACME.example"
PEOPLE = {"default_user": "morgan", "people": [
    {"id": "morgan", "name": "Morgan Reed", "email": "Morgan@acme.example", "primary_for": ["*"]},
    {"id": "riley", "name": "Riley Quinn", "email": "riley@acme.example", "primary_for": ["support"]}]}
BOTS = {"coo": {"name": "coo", "runtime": "fake", "status": "active"},
        "support": {"name": "support", "runtime": "fake", "status": "active"}}
REPOS = {"coo": "emp-coo", "support": "OtherOrg/support-bot"}
TOKEN = "local-owner-secret-token-0123456789"


def local_token_file(tmp_path, token=TOKEN):
    path = tmp_path / "local-token"
    path.write_text(token)
    path.chmod(0o600)
    return path


@pytest.fixture
def environment(tmp_path):
    """Build one configured environment per call; each gets its own database and registry."""
    clients = []

    def build(roster=PEOPLE, **overrides):
        registry = tmp_path / ("registry-%d" % len(clients))
        registry.mkdir()
        (registry / "hub-access.yaml").write_text(yaml.safe_dump({"allowed": []}))
        settings = Settings(db_path=tmp_path / ("hub-%d.db" % len(clients)), registry_dir=registry,
                            **{"owner_email": OWNER_EMAIL, **COMPANY, **overrides})
        client = TestClient(create_app(settings))
        client.__enter__()
        clients.append(client)
        with client.app.state.store.transaction() as c:
            H.sync_registry(c, BOTS, roster)
            c.execute("INSERT INTO registry_metadata VALUES('people',?)", (encode(roster),))
            for slug, config in BOTS.items():
                c.execute("INSERT INTO bot_config(bot,config_json,team,operator,repo) VALUES(?,?,?,?,?)",
                          (slug, encode(config), None, "riley" if slug == "support" else "morgan",
                           REPOS[slug]))
        return client

    yield build
    for client in clients:
        client.__exit__(None, None, None)


def signed_in(token=TOKEN):
    return {"Authorization": "Bearer " + token, "Idempotency-Key": str(uuid.uuid4())}


def test_local_owner_sign_in_names_the_owner_stays_on_origin_and_refuses_a_world_readable_token(environment, tmp_path):
    api = environment(local_owner_token_file=local_token_file(tmp_path))
    me = api.get("/api/me", headers=signed_in()).json()
    assert (me["id"], me["role"], me["owner_id"]) == ("morgan", "owner", "morgan")
    # The sign-in link sets a host-only, HttpOnly cookie and never redirects off this origin.
    response = api.get("/api/v2/local-signin", params={"token": TOKEN, "next": "//evil.example/steal"},
                       follow_redirects=False)
    assert response.status_code == 302 and response.headers["location"] == "/"
    cookie = response.headers["set-cookie"].lower()
    assert cookie.startswith(api.app.state.auth.local_cookie() + "=") and "httponly" in cookie and "domain" not in cookie
    assert api.get("/api/v2/me").json()["role"] == "owner"
    api.cookies.clear()
    (tmp_path / "local-token").chmod(0o644)
    refused = api.get("/api/v2/me", headers=signed_in())
    assert refused.status_code == 500 and "chmod 600" in refused.json()["error"]["detail"]


def test_a_local_install_takes_writes_from_127_0_0_1_and_localhost_on_its_own_port(tmp_path):
    local = Settings(db_path=tmp_path / "hub.db", public_url="http://127.0.0.1:8765",
                     local_owner_token_file=local_token_file(tmp_path))
    for origin in ("http://127.0.0.1:8765", "http://localhost:8765"):
        assert local.allows_origin(origin)
    for origin in ("http://localhost:9000", "http://127.0.0.1.evil.example:8765"):
        assert not local.allows_origin(origin)


def test_local_signin_refuses_to_start_on_a_public_address(tmp_path):
    with pytest.raises(RuntimeError, match="loopback"):
        Settings(db_path=tmp_path / "hub.db", public_url="https://atlas.acme.example",
                 local_owner_token_file=local_token_file(tmp_path))


def test_no_sign_in_is_fine_on_loopback_and_refused_on_a_public_address(monkeypatch, tmp_path):
    monkeypatch.setenv("TICO_DB", str(tmp_path / "hub.db"))
    monkeypatch.delenv("TICO_AUTH_PROXY", raising=False)
    monkeypatch.delenv("TICO_ACCESS_ISSUER", raising=False)
    monkeypatch.delenv("TICO_PUBLIC_URL", raising=False)
    monkeypatch.setenv("TICO_PORT", "8877")
    monkeypatch.setenv("TICO_OWNER_NAME", "Ana")
    settings = Settings.from_env()
    assert settings.environment()["owner_name"] == "Ana"
    assert settings.loopback and settings.public_url == settings.runner_url == "http://127.0.0.1:8877"
    assert settings.allows_origin("http://127.0.0.1:8877")
    monkeypatch.setenv("TICO_PUBLIC_URL", "http://localhost:8877")
    assert Settings.from_env().public_url == "http://localhost:8877"
    monkeypatch.setenv("TICO_PUBLIC_URL", "https://tico.acme.example")
    with pytest.raises(RuntimeError, match="needs sign-in"):
        Settings.from_env()
