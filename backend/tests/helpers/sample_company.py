"""Shared set-up for the sample company tests: the seeded registry, the booted hub, the signed-in environment
and the enroll helpers. Tests import these names; the tests themselves stay in test_sample_company.py."""

import re
import shutil
import uuid
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from backend import onboarding as O
from backend import sample_company
from backend.app import create_app
from backend.auth import Identity
from backend.company_file import from_yaml
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
SEED = {"assistant": {"name": "assistant", "status": "planned"}, "botops": {"name": "botops", "status": "planned"}}
TOKEN = "local-owner-secret-token-0123456789"
ROLE_BOTS = {
    "head-of-product": "claude",
    "feature-owner": "codex",
    "support-agent": "codex",
    "content-marketer": "codex",
}


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
            c.execute('INSERT INTO bot_config(bot,owner_id,repo) VALUES(?,?,?)', (slug, 'owner', 'bot-' + slug))
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


def start(api):
    return api.post("/api/v2/setup/sample", json={}, headers=signed_in())


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


def bot_ids(api):
    """Bot slug to bot id: a bot the sample makes has a generated id, and the tables name bots by it."""
    return {row["slug"]: row["id"] for row in rows(api, "SELECT id,slug FROM bots")}
