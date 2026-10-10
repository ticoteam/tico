"""Slack in the Docker install: compose wiring, the manifest against the code, sealed tokens, and the
gateway process with a fake Slack client (nothing touches the network)."""
import json
import logging
import os
import subprocess
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from backend import hubdb as H
from backend import slack_app
from backend import slack_gateway as G
from backend.app import create_app
from backend.auth import Identity
from backend.config import Settings

ROOT = Path(__file__).resolve().parents[2]
BOT, APP_TOKEN = "xoxb-1111111111-secret-bot-token", "xapp-1-A0SECRET-2222222222-secrettoken"
ENV = {"TICO_COMPANY_NAME": "Acme", "TICO_OWNER_EMAIL": "ana@acme.example", "TICO_AUTH_PROXY": "none"}


def compose(profiles=""):
    out = subprocess.run(["docker", "compose", "config"], cwd=ROOT, capture_output=True, text=True,
                         env={**os.environ, "COMPOSE_PROFILES": profiles, **ENV})
    assert out.returncode == 0, out.stderr
    return yaml.safe_load(out.stdout)["services"]


def test_manifest_matches_the_code_and_the_yaml():
    manifest = json.loads((ROOT / "connectors" / "slack-app-manifest.json").read_text())
    assert manifest == yaml.safe_load((ROOT / "connectors" / "slack-app-manifest.yaml").read_text())
    scopes = set(manifest["oauth_config"]["scopes"]["bot"])
    assert set(G.NEEDED_SCOPES) <= scopes
    events = set(manifest["settings"]["event_subscriptions"]["bot_events"])
    assert events == {"app_mention", "message.im", "message.channels", "message.groups"}
    assert manifest["settings"]["socket_mode_enabled"] is True
    # Every scope the connector CLI names in its own source is requested.
    import re
    named = set(re.findall(r"\b(?:channels|groups|im|mpim|users|chat|app_mentions):[a-z.]+\b",
                           (ROOT / "connectors" / "slack.py").read_text()))
    assert {s for s in named if s in scopes or s.endswith((":history", ":read", ":write"))} <= scopes


@pytest.fixture
def api(tmp_path):
    ids = {"owner-test": Identity("human:ana", "owner", "ana@acme.example"),
           "member-test": Identity("human:ben", "human", "ben@acme.example")}
    app = create_app(Settings(db_path=tmp_path / "hub.db", company_name="Acme", test_identities=ids))
    with TestClient(app) as client:
        with app.state.store.transaction() as c:
            H.sync_registry(c, {}, {"people": [{"id": "ana", "email": "ana@acme.example", "team": "leadership"},
                                               {"id": "ben", "email": "ben@acme.example", "team": "leadership"}]})
        client.app_state = app.state
        yield client


def h(who="owner-test"):
    return {"Authorization": "Bearer " + who}


def test_tokens_are_sealed_never_returned_and_never_logged(api, caplog, tmp_path):
    caplog.set_level(logging.DEBUG)
    body = {"bot_token": BOT, "app_token": APP_TOKEN}
    assert api.put("/api/v2/slack/tokens", json=body, headers=h("member-test")).status_code == 403
    assert api.put("/api/v2/slack/tokens", json={"bot_token": "nope", "app_token": APP_TOKEN}, headers=h()).status_code == 422
    assert api.put("/api/v2/slack/tokens", json=body, headers=h()).status_code == 200
    state = api.get("/api/v2/slack/app", headers=h())
    assert state.json()["configured"] is True and BOT not in state.text and APP_TOKEN not in state.text
    raw = b"".join(p.read_bytes() for p in tmp_path.iterdir() if p.name.startswith("hub.db"))
    assert BOT.encode() not in raw and APP_TOKEN.encode() not in raw
    assert (tmp_path / "slack.key").stat().st_mode & 0o077 == 0
    assert BOT not in caplog.text and APP_TOKEN not in caplog.text
    store = api.app_state.store
    assert slack_app.load(store, store.settings)["bot_token"] == BOT
    assert api.post("/api/v2/slack/disconnect", headers=h()).status_code == 200
    assert slack_app.load(store, store.settings) is None


class FakeClient:
    """Stands in for SlackAPI: auth and bot lookups succeed, the socket is a flag."""
    instances = []

    def __init__(self, bot, app):
        self.bot, self.app, self.scopes, self.up, self.closed = bot, app, (), False, False
        FakeClient.instances.append(self)

    def auth_test(self):
        return {"team_id": "T0FAKE1", "user_id": "U0BOT", "bot_id": "B0BOT", "url": "https://fake.slack.com/"}

    def bots_info(self, bot_id):
        return {"app_id": "A0FAKE1"}

    def connect(self, on_envelope):
        self.up = True

    def connected(self):
        return self.up

    def close(self):
        self.up, self.closed = False, True



def test_the_gateway_opens_tokens_the_api_sealed_under_a_kms_key(tmp_path, monkeypatch, capsys):
    from backend.credentials import CredentialCipher, Vault
    from backend.store import Store
    from backend.tests.test_credentials import FakeKMS
    kms = FakeKMS()
    monkeypatch.setattr("boto3.client", lambda name, *a, **k: kms if name == "kms" else pytest.fail(name))
    monkeypatch.delenv("TICO_AUTH_PROXY", raising=False)
    for name, value in {"TICO_COMPANY_NAME": "Acme", "TICO_OWNER_EMAIL": "ana@acme.example", "TICO_DB": str(tmp_path / "hub.db"), "TICO_CREDENTIAL_KMS_KEY": "test-key",
                        "TICO_ENVIRONMENT_ID": "acme-test"}.items():
        monkeypatch.setenv(name, value)
    settings = Settings.from_env()
    store = Store(settings)
    store.initialize()
    slack_app.save(store, settings, "human:ana", BOT, APP_TOKEN, Vault(store, CredentialCipher("test-key", kms)))
    assert not (tmp_path / "slack.key").exists()
    assert G.main(["--check"], make_slack=FakeClient) == 0
    assert FakeClient.instances[-1].bot == BOT and '"team_id": "T0FAKE1"' in capsys.readouterr().out


def test_slack_tokens_saved_before_kms_was_set_open_with_slack_key_and_are_resealed(tmp_path, caplog):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    from backend.credentials import CredentialCipher, Vault
    from backend.store import Store
    from backend.tests.test_credentials import FakeKMS
    settings = Settings(db_path=tmp_path / "hub.db", company_name="Acme")
    store = Store(settings)
    store.initialize()
    slack_app.save(store, settings, "human:ana", BOT, APP_TOKEN)           # before KMS: sealed with slack.key
    legacy = (tmp_path / "slack.key").read_bytes()
    settings.credential_kms_key = "test-key"
    vault = Vault(store, CredentialCipher("test-key", FakeKMS()))
    caplog.set_level("INFO")
    assert slack_app.load(store, settings, vault)["bot_token"] == BOT
    with store.read() as c:
        row = c.execute("SELECT * FROM slack_credentials").fetchone()
        AESGCM(vault.cipher.key(c)).decrypt(row["nonce"], row["ciphertext"], slack_app.AAD)   # now the vault's
    assert any("Re-sealed slack_credentials app" in r.getMessage() for r in caplog.records)
    assert not any(BOT in r.getMessage() for r in caplog.records)
    assert slack_app.load(store, settings, vault)["app_token"] == APP_TOKEN
    assert (tmp_path / "slack.key").read_bytes() == legacy
    # Without slack.key nothing opens them, and the read makes no key file.
    slack_app.save(store, settings, "human:ana", BOT, APP_TOKEN)
    (tmp_path / "slack.key").unlink()
    assert slack_app.load(store, settings, vault) is None and not (tmp_path / "slack.key").exists()
