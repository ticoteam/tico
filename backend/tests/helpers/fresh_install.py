"""A fresh install for tests: the seeded registry, the booted hub, the signed-in environment and the enroll
helpers."""

import shutil
import uuid
from pathlib import Path

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
# What a fresh environment seeds (templates/environment-registry/employees.yaml): the assistant and Agent Ops, planned.
SEED = {
    "assistant": {"name": "assistant", "status": "planned"},
    "botops": {"name": "botops", "slug": "agent-ops", "status": "planned"},
}
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
            "assistant_bot": "assistant",
            "enabled_providers": (),
            **overrides,
        }
    )


@pytest.fixture(scope="module")
def booted(tmp_path_factory) -> Path:
    """One initialized, seeded hub for the module: a default environment's first build copies it (the boot is the slow part)."""
    root = tmp_path_factory.mktemp("sample-boot")
    with TestClient(create_app(settings_for(root))) as client:
        _seed(client)
    return root


def _seed(client):
    with client.app.state.store.transaction() as c:
        H.sync_registry(c, SEED, PEOPLE)
        c.execute("INSERT INTO registry_metadata VALUES('people',?) ON CONFLICT(key) DO NOTHING", (encode(PEOPLE),))
        for slug, config in SEED.items():
            repo = 'bot-' + config.get('slug', slug)  # the registry's repo: bot-<slug>
            c.execute('INSERT INTO bot_config(bot,owner_id,repo) VALUES(?,?,?)', (slug, 'owner', repo))
            BS.write(c, slug, config)


@pytest.fixture
def environment(tmp_path, booted):
    """A signed-in loopback environment on the shipped catalog, seeded like a fresh install.

    The first build of a test with the default settings starts from a copy of the module's booted hub, which holds
    the same seed; every other build opens the same database in tmp_path as before (restarts share it)."""
    clients = []

    def build(seed_roster=True, **overrides):
        if not clients and seed_roster and not overrides:
            shutil.copytree(booted, tmp_path, dirs_exist_ok=True)
            client = TestClient(create_app(settings_for(tmp_path)))
            client.__enter__()
            clients.append(client)
            return client
        client = TestClient(create_app(settings_for(tmp_path, **overrides)))
        client.__enter__()
        clients.append(client)
        if seed_roster:
            _seed(client)
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
