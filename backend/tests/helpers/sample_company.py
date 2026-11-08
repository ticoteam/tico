"""Helpers that other tests share, moved out of test_sample_company.py so they import no test file."""

import uuid
import pytest
import yaml
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.config import Settings
from backend.store import H, encode
from backend import bot_settings as BS


OWNER_EMAIL = "owner@sample.example"


PEOPLE = {
    "default_user": "owner",
    "people": [
        {"id": "owner", "name": "Owner Person", "email": OWNER_EMAIL, "primary_for": ["*"]},
        {"id": "helper", "name": "Helper Person", "email": "helper@sample.example", "primary_for": []},
    ],
}


# What a fresh environment seeds (templates/environment-registry/employees.yaml): the assistant and BotOps, planned.
SEED = {"coo": {"name": "coo", "status": "planned"}, "botops": {"name": "botops", "status": "planned"}}


TOKEN = "local-owner-secret-token-0123456789"


def settings_for(tmp_path, **overrides):
    registry = tmp_path / "registry"
    registry.mkdir(exist_ok=True)
    (registry / "hub-access.yaml").write_text(yaml.safe_dump({"bot_admins": ["helper@sample.example"]}))
    token = tmp_path / "token"
    token.write_text(TOKEN)
    token.chmod(0o600)
    return Settings(
        **{
            "db_path": tmp_path / "hub.db",
            "registry_dir": registry,
            "local_owner_token_file": token,
            "owner_email": OWNER_EMAIL,
            "environment_id": "sample-1",
            "company_name": "",
            "app_name": "Tico",
            "assistant_name": "Assistant",
            "assistant_bot": "coo",
            "enabled_providers": (),
            **overrides,
        }
    )


@pytest.fixture
def environment(tmp_path):
    """A signed-in loopback environment on the shipped catalog, seeded like a fresh install."""
    clients = []

    def build(seed_roster=True, **overrides):
        client = TestClient(create_app(settings_for(tmp_path, **overrides)))
        client.__enter__()
        clients.append(client)
        if seed_roster:
            with client.app.state.store.transaction() as c:
                H.sync_registry(c, SEED, PEOPLE)
                c.execute(
                    "INSERT INTO registry_metadata VALUES('people',?) ON CONFLICT(key) DO NOTHING", (encode(PEOPLE),)
                )
                for slug, config in SEED.items():
                    c.execute('INSERT INTO bot_config(bot,owner_id,repo) VALUES(?,?,?)', (slug, 'owner', 'bot-' + slug))
                    BS.write(c, slug, config)
        return client

    yield build
    for client in clients:
        client.__exit__(None, None, None)


def signed_in():
    return {"Authorization": "Bearer " + TOKEN, "Idempotency-Key": str(uuid.uuid4())}


def machine(api):
    code = api.post("/api/v2/enrollments", json={"operator": "owner"}, headers=signed_in()).json()["code"]
    return api.post(
        "/api/v2/runners/enroll",
        json={"code": code, "label": "Server", "platform": "test"},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    ).json()


def rows(api, sql, args=()):
    with api.app.state.store.read() as c:
        return [dict(row) for row in c.execute(sql, args)]
