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
    out = subprocess.run(
        ["docker", "compose", "config"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={**os.environ, "COMPOSE_PROFILES": profiles, **ENV},
    )
    assert out.returncode == 0, out.stderr
    return yaml.safe_load(out.stdout)["services"]


def test_manifest_matches_the_code_and_the_yaml():
    manifest = json.loads((ROOT / "connectors" / "slack-app-manifest.json").read_text())
    assert manifest == yaml.safe_load((ROOT / "connectors" / "slack-app-manifest.yaml").read_text())
    scopes = set(manifest["oauth_config"]["scopes"]["bot"])
    assert set(G.NEEDED_SCOPES) <= scopes
    events = set(manifest["settings"]["event_subscriptions"]["bot_events"])
    assert events == {"app_mention", "message.im", "message.channels", "message.groups", "link_shared"}
    assert manifest["settings"]["socket_mode_enabled"] is True
    # Every scope the connector CLI names in its own source is requested.
    import re

    named = set(
        re.findall(
            r"\b(?:channels|groups|im|mpim|users|chat|app_mentions|files):[a-z.]+\b",
            (ROOT / "connectors" / "slack.py").read_text(),
        )
    )
    assert {s for s in named if s in scopes or s.endswith((":history", ":read", ":write"))} <= scopes


def test_the_manifest_asks_for_previews_only_where_slack_can_register_a_host(tmp_path):
    text = (ROOT / "connectors" / "slack-app-manifest.json").read_text()
    public = slack_app.with_hosts(
        json.loads(text), Settings(db_path=tmp_path / "hub.db", public_url="https://tico.acme.example")
    )
    assert public["features"]["unfurl_domains"] == ["tico.acme.example"]
    assert {"links:read", "links:write"} <= set(public["oauth_config"]["scopes"]["bot"])
    # A loopback install cannot register a host: no preview scopes, event or domains are asked for.
    loopback = slack_app.with_hosts(
        json.loads(text), Settings(db_path=tmp_path / "hub.db", public_url="http://127.0.0.1:8765")
    )
    assert "unfurl_domains" not in loopback["features"]
    assert not {"links:read", "links:write"} & set(loopback["oauth_config"]["scopes"]["bot"])
    assert "link_shared" not in loopback["settings"]["event_subscriptions"]["bot_events"]


@pytest.fixture
def api(tmp_path):
    ids = {
        "owner-test": Identity("human:ana", "owner", "ana@acme.example"),
        "member-test": Identity("human:ben", "human", "ben@acme.example"),
    }
    app = create_app(Settings(db_path=tmp_path / "hub.db", company_name="Acme", test_identities=ids))
    with TestClient(app) as client:
        with app.state.store.transaction() as c:
            H.sync_registry(
                c,
                {},
                {
                    "people": [
                        {"id": "ana", "email": "ana@acme.example", "team": "leadership"},
                        {"id": "ben", "email": "ben@acme.example", "team": "leadership"},
                    ]
                },
            )
        client.app_state = app.state
        yield client


def h(who="owner-test"):
    return {"Authorization": "Bearer " + who}


def test_tokens_are_sealed_never_returned_and_never_logged(api, caplog, tmp_path):
    caplog.set_level(logging.DEBUG)
    body = {"bot_token": BOT, "app_token": APP_TOKEN}
    assert api.put("/api/v2/slack/tokens", json=body, headers=h("member-test")).status_code == 403
    assert (
        api.put("/api/v2/slack/tokens", json={"bot_token": "nope", "app_token": APP_TOKEN}, headers=h()).status_code
        == 422
    )
    assert api.put("/api/v2/slack/tokens", json=body, headers=h()).status_code == 200
    state = api.get("/api/v2/slack/app", headers=h())
    assert state.json()["configured"] is True and BOT not in state.text and APP_TOKEN not in state.text
    raw = b"".join(p.read_bytes() for p in tmp_path.iterdir() if p.name.startswith("hub.db"))
    assert BOT.encode() not in raw and APP_TOKEN.encode() not in raw
    assert not (tmp_path / "slack.key").exists()  # sealed with the vault's key, which its backup covers
    assert BOT not in caplog.text and APP_TOKEN not in caplog.text
    store = api.app_state.store
    assert slack_app.load(store)["bot_token"] == BOT
    # Vault rows the server uses: never listed, granted or revealed.
    listed = api.get("/api/v2/credentials", headers=h())
    assert listed.status_code == 200 and slack_app.BOT_ID not in listed.text
    revealed = api.post(f"/api/v2/credentials/{slack_app.BOT_ID}/reveal", headers=h())
    assert revealed.status_code in (403, 404) and BOT not in revealed.text
    assert api.post("/api/v2/slack/disconnect", headers=h()).status_code == 200
    assert slack_app.load(store) is None and api.get("/api/v2/slack/app", headers=h()).json()["configured"] is False


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
    from backend.tests.helpers.credentials import FakeKMS

    kms = FakeKMS()
    monkeypatch.setattr("boto3.client", lambda name, *a, **k: kms if name == "kms" else pytest.fail(name))
    monkeypatch.delenv("TICO_AUTH_PROXY", raising=False)
    for name, value in {
        "TICO_COMPANY_NAME": "Acme",
        "TICO_OWNER_EMAIL": "ana@acme.example",
        "TICO_DB": str(tmp_path / "hub.db"),
        "TICO_CREDENTIAL_KMS_KEY": "test-key",
        "TICO_ENVIRONMENT_ID": "acme-test",
    }.items():
        monkeypatch.setenv(name, value)
    settings = Settings.from_env()
    store = Store(settings)
    store.initialize()
    slack_app.save(store, "human:ana", BOT, APP_TOKEN, Vault(store, CredentialCipher("test-key", kms)))
    assert not (tmp_path / "slack.key").exists()
    assert G.main(["--check"], make_slack=FakeClient) == 0
    assert FakeClient.instances[-1].bot == BOT and '"team_id": "T0FAKE1"' in capsys.readouterr().out


MOVE = "20261117075941_slack_tokens_into_vault"


def _older_server(tmp_path, key=None):
    """A database as an older server left it, this move not run yet (as in any order: every newer migration has): the
    tokens sealed in its own `slack_credentials` table with `key` (a slack.key file beside it when None)."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    from backend.store import Store

    store = Store(Settings(db_path=tmp_path / "hub.db", company_name="Acme"))
    store.initialize()
    if key is None:
        key = os.urandom(32)
        (tmp_path / "slack.key").write_bytes(key)
    elif callable(key):
        key = key(store)
    nonce = os.urandom(12)
    sealed = AESGCM(key).encrypt(
        nonce, json.dumps({"bot_token": BOT, "app_token": APP_TOKEN}).encode(), slack_app.OLD_AAD
    )
    with store.transaction() as c:
        c.execute(
            "CREATE TABLE slack_credentials(id TEXT PRIMARY KEY, ciphertext BLOB NOT NULL, nonce BLOB NOT NULL, "
            "team_id TEXT NOT NULL DEFAULT '', app_id TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, "
            "created_by TEXT NOT NULL)"
        )
        c.execute(
            "INSERT INTO slack_credentials VALUES('app',?,?,'T0OLD','A0OLD','2026-11-01T00:00:00Z','human:ana')",
            (sealed, nonce),
        )
        c.execute("DELETE FROM schema_migrations WHERE version=?", (MOVE,))
    return store


def _start(store):
    import sqlite3
    from contextlib import closing

    from backend import migrations

    with closing(sqlite3.connect(store.settings.db_path, isolation_level=None)) as conn:
        migrations.apply(conn)
        return conn.execute("SELECT 1 FROM sqlite_master WHERE name='slack_credentials'").fetchone() is not None


def test_an_older_servers_slack_key_tokens_move_into_the_vault_once(tmp_path, caplog):
    store = _older_server(tmp_path)
    caplog.set_level("INFO")
    assert _start(store) is False  # moved, and the table dropped
    (tmp_path / "slack.key").unlink()  # never read again
    stored = slack_app.load(store)
    assert (stored["bot_token"], stored["app_token"]) == (BOT, APP_TOKEN)
    assert (stored["team_id"], stored["app_id"], stored["created_at"]) == ("T0OLD", "A0OLD", "2026-11-01T00:00:00Z")
    raw = b"".join(p.read_bytes() for p in tmp_path.iterdir() if p.name.startswith("hub.db"))
    assert BOT.encode() not in raw and APP_TOKEN.encode() not in raw
    assert BOT not in caplog.text and not (tmp_path / "slack.key").exists()
    assert _start(store) is False and slack_app.load(store)["bot_token"] == BOT  # recorded: it does not run again


def test_without_slack_key_the_row_is_left_logged_and_the_start_goes_on(tmp_path, caplog):
    store = _older_server(tmp_path)
    (tmp_path / "slack.key").unlink()
    caplog.set_level("INFO")
    assert _start(store) is True
    assert "category=cannot_open id=slack_credentials:app detail=FileNotFoundError" in caplog.text
    assert slack_app.load(store) is None
    with store.read() as c:
        assert slack_app.status(c)["configured"] is True  # still there: Disconnect forgets it
    slack_app.forget(store)
    with store.read() as c:
        assert slack_app.status(c)["configured"] is False


def test_kms_sealed_tokens_move_and_wait_for_kms_when_it_is_unreachable(tmp_path, monkeypatch, caplog):
    from backend.credentials import CredentialCipher, Vault
    from backend.tests.helpers.credentials import FakeKMS

    class Down:
        def decrypt(self, **kwargs):
            raise RuntimeError("KMS unreachable")

    kms, reach = FakeKMS(), {"kms": None}
    monkeypatch.setenv("TICO_CREDENTIAL_KMS_KEY", "test-key")
    monkeypatch.setattr("boto3.client", lambda name, *a, **k: reach["kms"] if name == "kms" else pytest.fail(name))

    def vault_key(store):
        with store.transaction() as c:
            return Vault(store, CredentialCipher("test-key", kms)).cipher.key(c)

    store = _older_server(tmp_path, vault_key)
    reach["kms"] = Down()
    caplog.set_level("INFO")
    assert _start(store) is True  # the start does not fail
    assert "category=vault_unavailable id=slack_credentials:app detail=Problem" in caplog.text
    assert slack_app.load(store, Vault(store, CredentialCipher("test-key", Down()))) is None
    # KMS is back: the gateway's next read opens the row with the vault's key and moves it.
    stored = slack_app.load(store, Vault(store, CredentialCipher("test-key", kms)))
    assert (stored["bot_token"], stored["team_id"]) == (BOT, "T0OLD")
    assert "Moved the Slack tokens from slack_credentials into the vault" in caplog.text and BOT not in caplog.text
    with store.read() as c:
        assert c.execute("SELECT count(*) FROM slack_credentials").fetchone()[0] == 0
    # And a start with KMS reachable moves one at once.
    (tmp_path / "next").mkdir()
    other = _older_server(tmp_path / "next", vault_key)
    reach["kms"] = kms
    assert _start(other) is False
    assert slack_app.load(other, Vault(other, CredentialCipher("test-key", kms)))["app_token"] == APP_TOKEN


def test_the_gateway_reads_tokens_from_the_secret_arn_and_nowhere_else(monkeypatch):
    from backend import config

    fetched = []

    class SecretsManager:
        def get_secret_value(self, SecretId):
            fetched.append(SecretId)
            secret = {"bot_token": BOT, "app_token": APP_TOKEN, "team_id": "T0ARN", "app_id": "A0ARN"}
            return {"SecretString": json.dumps(secret)}

    arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:tico/slack"
    monkeypatch.setattr(
        "boto3.client", lambda name, **k: SecretsManager() if name == "secretsmanager" else pytest.fail(name)
    )
    real_path = config.Path

    def guarded_path(value, *args):
        assert str(value) != "/etc/tico/slack", "the gateway must not read /etc/tico/slack"
        return real_path(value, *args)

    monkeypatch.setattr(config, "Path", guarded_path)
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-from-env")  # ignored: the environment pair is gone
    monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-from-env")
    monkeypatch.setenv("TICO_SLACK_SECRET_ARN", arn)
    monkeypatch.setenv("SLACK_TEAM_ID", "T0PINNED")
    pinned = {"bot_token": BOT, "app_token": APP_TOKEN, "team_id": "T0PINNED", "app_id": "A0ARN"}
    assert config.slack_credentials() == pinned
    assert fetched == [arn]
    monkeypatch.delenv("TICO_SLACK_SECRET_ARN")
    empty = {"bot_token": "", "app_token": "", "team_id": "T0PINNED", "app_id": ""}
    assert config.slack_credentials() == empty
    assert fetched == [arn]

    class Refused:
        def get_secret_value(self, SecretId):
            raise PermissionError("no access")  # the gateway logs the error's type, never a value

    monkeypatch.setattr("boto3.client", lambda name, **k: Refused())
    monkeypatch.setenv("TICO_SLACK_SECRET_ARN", arn)
    assert config.slack_credentials() == empty


def test_the_gateway_with_no_tokens_says_where_to_paste_them(tmp_path, monkeypatch, caplog):
    for name, value in {
        "TICO_COMPANY_NAME": "Acme",
        "TICO_OWNER_EMAIL": "ana@acme.example",
        "TICO_DB": str(tmp_path / "hub.db"),
        "TICO_ENVIRONMENT_ID": "acme-test",
        "TICO_SLACK_GATEWAY_ENABLED": "1",
        "SLACK_BOT_TOKEN": "xoxb-from-env",  # not read by the gateway
    }.items():
        monkeypatch.setenv(name, value)
    for name in ("TICO_AUTH_PROXY", "TICO_SLACK_SECRET_ARN", "TICO_SLACK_WAIT"):
        monkeypatch.delenv(name, raising=False)
    caplog.set_level("ERROR")
    clients = len(FakeClient.instances)
    assert G.main([], make_slack=FakeClient) == 1
    assert len(FakeClient.instances) == clients  # no Slack client was built from the environment pair
    assert "No Slack tokens: paste them in Tools > Slack or set TICO_SLACK_SECRET_ARN" in caplog.text
    # Waiting (TICO_SLACK_WAIT=1, as compose runs it), it says the same and checks again until stopped.
    monkeypatch.setenv("TICO_SLACK_WAIT", "1")
    monkeypatch.setattr(G, "_sleep", lambda stopping, seconds: False)
    caplog.set_level("INFO")
    assert G.main([], make_slack=FakeClient) == 0
    assert "No Slack tokens yet; paste them in Tools > Slack" in caplog.text
