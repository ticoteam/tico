"""Credentials shared between bots work by default (docs/credential-vault.md): the vault needs no KMS key, BotOps grants and
revokes as the administrator who asked, a bot's own secrets file can be moved into the vault by its computer, and a granted
credential reaches the grantee's runs as its variable, and only the grantee's.

Ana owns the company, Ben is an admin, Cara is a member (the base fixture).
"""
import json
import os

import pytest

from backend.credentials import CredentialCipher
from backend.tests.test_api import api, assign, claim, get, post, ready, runner  # noqa: F401  (fixture)
from backend.tests.test_botops_parity import act, everything
from backend.tests.test_credentials import FakeKMS
from backend.tests.test_mcp import call as mcp
from backend.tests.test_member_bots import botops, finish, turn  # noqa: F401  (fixture)
from backend.tests.test_runner import live  # noqa: F401  (fixture)

JIRA = "jira-basic-auth-synthetic-fixture-0123456789"


def local(api):
    """The default: no KMS key is set, so the vault's data key is the file beside the database."""
    settings = api.app.state.store.settings
    assert not settings.credential_kms_key
    settings.credential_admins = ("ana@acme.example", "ben@acme.example")
    return settings.db_path.parent / "credential.key"


def jira(api, **kwargs):
    return post(api, "credentials", {"name": "Jira", "env": "JIRA_BASIC_AUTH", "secret": JIRA, **kwargs})


def test_watchers_receive_only_live_grants_for_active_bots_on_their_computer(api):
    local(api)
    computer, other = runner(api), runner(api)
    assign(api, computer, "ops")
    row = jira(api)
    grant = post(api, f"credentials/{row['id']}/grants", {"subject": "bot:ops"})
    path = "runner-watcher-credentials?bot=ops"
    values = get(api, path, computer["token"])["credentials"]
    assert values == [{"id": row["id"], "env": "JIRA_BASIC_AUTH", "kind": "api_key", "value": JIRA}]
    assert get(api, path, other["token"], expected=403)["error"]["code"] == "forbidden"
    assert get(api, path, "ana-test", expected=403)["error"]["code"] == "forbidden"
    with api.app.state.store.read() as c:
        event = c.execute("SELECT actor,detail_json FROM events WHERE action='credential.sent_to_computer'").fetchone()
        assert event["actor"] == "runner:" + computer["runner_id"] and '"use": "watcher"' in event["detail_json"]
        assert JIRA not in event["detail_json"]
    post(api, f"credentials/{row['id']}/grants/{grant['id']}/revoke", {})
    assert get(api, path, computer["token"])["credentials"] == []
    post(api, f"credentials/{row['id']}/grants", {"subject": "bot:finance"})
    assert get(api, path, computer["token"])["credentials"] == []
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET state='quarantined' WHERE slug='ops'")
    assert get(api, path, computer["token"], expected=403)["error"]["code"] == "forbidden"


# ------------------------------------------------------------------ a vault with no KMS key
def test_the_vault_works_with_a_local_key_and_reads_it_back_after_a_restart(api):
    path = local(api)
    assert get(api, "credentials")["configured"] is True and get(api, "credentials")["key_storage"] == "local"
    row = jira(api)
    assert row["stored"] and row["preview"] == "jir…789"
    assert path.stat().st_mode & 0o777 == 0o600 and len(path.read_bytes()) == 32
    with api.app.state.store.read() as c:
        marker = c.execute("SELECT kms_key,wrapped_key FROM credential_keys WHERE id='v1'").fetchone()
        assert marker["kms_key"] == "local" and path.read_bytes() not in bytes(marker["wrapped_key"])
        assert JIRA.encode() not in bytes(c.execute("SELECT ciphertext FROM credentials").fetchone()[0])
    # A restart is a new cipher over the same database and file.
    api.app.state.vault.cipher = CredentialCipher("", key_file=path)
    assert post(api, f"credentials/{row['id']}/reveal", {})["value"] == JIRA
    assert path.read_bytes() == path.read_bytes() and len(path.read_bytes()) == 32
    # The key is never generated a second time: without the file, or with another one, nothing decrypts (and nothing is lost).
    kept = path.read_bytes()
    path.unlink()
    api.app.state.vault.cipher = CredentialCipher("", key_file=path)
    failed = post(api, f"credentials/{row['id']}/reveal", {}, expected=503)
    assert "key file" in failed["error"]["detail"] and not path.exists()
    path.write_bytes(os.urandom(32))
    api.app.state.vault.cipher = CredentialCipher("", key_file=path)
    assert "does not belong" in post(api, f"credentials/{row['id']}/reveal", {}, expected=503)["error"]["detail"]
    path.write_bytes(kept)
    api.app.state.vault.cipher = CredentialCipher("", key_file=path)
    assert post(api, f"credentials/{row['id']}/reveal", {})["value"] == JIRA


class KMSWithEncrypt(FakeKMS):
    def encrypt(self, **kwargs):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        assert kwargs["EncryptionContext"] == {"application": "tico-credentials"}
        nonce = os.urandom(12)
        return {"CiphertextBlob": nonce + AESGCM(self.key).encrypt(nonce, kwargs["Plaintext"], b"kms")}


def test_setting_a_kms_key_later_keeps_what_was_stored_and_a_kms_install_is_untouched(api):
    path = local(api)
    row = jira(api)
    kms = KMSWithEncrypt()
    api.app.state.vault.cipher = CredentialCipher("alias/tico", kms, key_file=path)
    assert post(api, f"credentials/{row['id']}/reveal", {})["value"] == JIRA
    with api.app.state.store.read() as c:
        assert c.execute("SELECT kms_key FROM credential_keys").fetchone()[0] == "alias/tico"
    # From here the key file is not needed: KMS alone decrypts.
    path.unlink()
    api.app.state.vault.cipher = CredentialCipher("alias/tico", kms, key_file=path)
    assert post(api, f"credentials/{row['id']}/reveal", {})["value"] == JIRA
    assert not path.exists()
    # An install that began with KMS never reads or makes a key file; taking the KMS key away is refused, not a new key.
    api.app.state.vault.cipher = CredentialCipher("", key_file=path)
    assert "AWS KMS" in post(api, f"credentials/{row['id']}/reveal", {}, expected=503)["error"]["detail"] and not path.exists()


# ------------------------------------------------------------------ BotOps grants and revokes as the requester
def test_botops_grants_a_bot_at_once_for_an_admin_and_refuses_a_member_without_a_card(api, botops):
    local(api)
    row = jira(api)
    ana = turn(api, botops, person="ana-test", text="Grant ops the Jira credential")
    done = act(api, ana, "POST", f"credentials/{row['id']}/grants", {"subject": "bot:ops"})
    assert done.status_code == 200, done.text
    assert "needs_confirm" not in done.json() and done.json()["credential"] == "Jira" and done.json()["env"] == "JIRA_BASIC_AUTH"
    with api.app.state.store.read() as c:
        event = c.execute("SELECT actor,detail_json FROM events WHERE action='credential.granted'").fetchone()
        assert event["actor"] == "human:ana" and '"via": "botops"' in event["detail_json"]       # hers, via BotOps
        assert c.execute("SELECT count(*) FROM assistant_actions").fetchone()[0] == 0
    assert act(api, ana, "POST", f"credentials/{row['id']}/grants", {"subject": "bot:ops"}).json()["id"] == done.json()["id"]
    # Grants use the administrator requester’s full rights, including grants to humans.
    card = act(api, ana, "POST", f"credentials/{row['id']}/grants", {"subject": "human:cara"}).json()
    assert "needs_confirm" not in card and card["subject"] == "human:cara"
    finish(api, botops, ana)
    # A member is refused, in words that say who to ask, and no card is left behind.
    cara = turn(api, botops, person="cara-test", text="Grant ops the Jira credential")
    refused = act(api, cara, "POST", f"credentials/{row['id']}/grants", {"subject": "bot:finance"})
    assert refused.status_code == 403 and "credential administrator" in refused.json()["error"]["detail"]
    assert "Ana" in refused.json()["error"]["detail"] or "ana@acme.example" in refused.json()["error"]["detail"]
    assert act(api, cara, "POST", f"credentials/{row['id']}/grants/{done.json()['id']}/revoke", {}).status_code == 403
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM credential_grants WHERE subject='bot:finance'").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM assistant_actions WHERE summary LIKE '%finance%'").fetchone()[0] == 0
    finish(api, botops, cara)
    # The revoke is direct for an admin.
    ben = turn(api, botops, person="ben-test", text="Take Jira away from ops")
    assert act(api, ben, "POST", f"credentials/{row['id']}/grants/{done.json()['id']}/revoke", {}).status_code == 200
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM credential_grants WHERE subject='bot:ops' AND revoked IS NULL").fetchone()[0] == 0
    finish(api, botops, ben)
    # A member who added a credential shares it with their own bot through BotOps, at once, and nobody else's.
    settings = api.app.state.store.settings
    settings.credential_admins = ("ana@acme.example", "cara@acme.example")
    mine = post(api, "credentials", {"name": "Sentry", "env": "SENTRY_TOKEN", "secret": JIRA}, "cara-test")["id"]
    settings.credential_admins = ("ana@acme.example",)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET operator='cara' WHERE bot='finance'")
    cara = turn(api, botops, person="cara-test", text="Give finance my Sentry token")
    given = act(api, cara, "POST", f"credentials/{mine}/grants", {"subject": "bot:finance"})
    assert given.status_code == 200 and "needs_confirm" not in given.json(), given.text
    assert act(api, cara, "POST", f"credentials/{mine}/grants", {"subject": "bot:ops"}).status_code == 403
    assert act(api, cara, "POST", f"credentials/{mine}/grants/{given.json()['id']}/revoke", {}).status_code == 200


# ------------------------------------------------------------------ hub credential grant | revoke | import


# ------------------------------------------------------------------ moving a bot's own secrets file into the vault
def test_a_bots_own_secret_moves_into_the_vault_over_its_computer_and_is_then_shared_and_reaches_only_the_grantee(
        api, botops, live, tmp_path, capsys):
    from runner.service import Runner
    local(api)
    machine = runner(api, label="Jira Mac")
    assign(api, machine, "finance")
    assign(api, machine, "ops")
    ready(api, machine, ["finance", "ops"])
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    (secrets / "finance.env").write_text(f"# Jira\nJIRA_BASIC_AUTH={JIRA}\nOTHER_KEY=not-asked-for-synthetic\n")
    (secrets / "ops.env").write_text("OPS_ONLY_KEY=ops-own-synthetic\n")
    (secrets / "_shared.env").write_text("SHARED_KEY=shared-synthetic\n")
    service = Runner({"url": live, "token": machine["token"], "projects_dir": str(tmp_path)}, tmp_path / "state",
                     host_factory=lambda attempt, env: None)
    try:
        # Only a credential administrator asks, and never for a name the computer or Tico itself uses.
        post(api, "credential-imports", {"env": "JIRA_BASIC_AUTH", "bot": "finance"}, "cara-test", expected=403)
        post(api, "credential-imports", {"env": "HUB_TOKEN", "bot": "finance"}, "ana-test", expected=422)
        post(api, "credential-imports", {"env": "JIRA_BASIC_AUTH", "bot": "doc-updater"}, "ana-test", expected=409)        # on no computer
        token = post(api, "me/tokens", {"label": "QA credential admin"}, "ben-test")["token"]
        err, requested = mcp(api, "hub_credential_import", {"env": "JIRA_BASIC_AUTH", "from_bot": "finance", "wait": 0}, token=token)
        assert not err and requested["state"] == "waiting" and JIRA not in json.dumps(requested)
        asked = post(api, "credential-imports", {"env": "JIRA_BASIC_AUTH", "bot": "finance"}, token)
        assert asked["state"] == "requested" and JIRA not in json.dumps(asked)
        assert post(api, "credential-imports", {"env": "JIRA_BASIC_AUTH", "bot": "finance"}, "ben-test")["id"] == asked["id"]
        assert get(api, "runner-credential-imports", machine["token"])["imports"] == [{"id": asked["id"], "bot": "finance", "env": "JIRA_BASIC_AUTH"}]
        other = runner(api, label="Another Mac")
        assert get(api, "runner-credential-imports", other["token"])["imports"] == []       # the bot is not on that computer
        post(api, f"runner-credential-imports/{asked['id']}/report", {"error": "x"}, other["token"], expected=404)
        service.poll_credential_imports()
        done = get(api, f"credential-imports/{asked['id']}", "ben-test")
        assert done["state"] == "done" and done["credential_id"], done
        get(api, f"credential-imports/{asked['id']}", "cara-test", expected=404)
        # Stored encrypted for that bot alone, the file untouched, and the value nowhere it should not be.
        assert JIRA not in everything(api) and JIRA not in json.dumps(get(api, "credentials"))
        out = capsys.readouterr()
        assert JIRA not in out.out + out.err
        assert f"JIRA_BASIC_AUTH={JIRA}" in (secrets / "finance.env").read_text()
        listing = get(api, "credentials")["credentials"]
        assert [(c["name"], c["env"], [g["subject"] for g in c["grants"]]) for c in listing] == [("JIRA_BASIC_AUTH", "JIRA_BASIC_AUTH", ["bot:finance"])]
        assert post(api, f"credentials/{done['credential_id']}/reveal", {})["value"] == JIRA
        # A name that is not in that bot's own file (another bot's, or _shared.env's) is not found there.
        for env in ("OPS_ONLY_KEY", "SHARED_KEY"):
            missing = post(api, "credential-imports", {"env": env, "bot": "finance"}, "ana-test")
            service._polled.pop("imports", None)
            service.poll_credential_imports()
            failed = get(api, f"credential-imports/{missing['id']}", "ana-test")
            assert failed["state"] == "failed" and "secrets file" in failed["message"], failed
        # Now BotOps, as Ana, gives it to ops; ops's run has it as its variable and finance's own file is what it was.
        ana = turn(api, botops, person="ana-test", text="Grant ops the Jira credential finance has")
        err, granted = mcp(api, "hub_credential_grant", {"credential": "JIRA_BASIC_AUTH", "to_bot": "ops"}, token=ana["token"])
        assert not err, granted
        finish(api, botops, ana)
        post(api, "chat/ops", {"text": "Check Jira"})
        ops_run = claim(api, machine, "ops")
        assert ops_run["credential_vault"] is True
        env = service.environment(ops_run)
        assert env["JIRA_BASIC_AUTH"] == JIRA and "OPS_ONLY_KEY" not in env and "OTHER_KEY" not in env
        assert service.vault_values[ops_run["id"]] == [JIRA]
        # Only grants reach a run; old files are no longer a fallback.
        assert "OPS_ONLY_KEY" not in service.credential_environment("ops", {})
        assert "JIRA_BASIC_AUTH" not in service.credential_environment("ops", {})
        assert "JIRA_BASIC_AUTH" not in service.credential_environment("doc-updater", {})
        assert "JIRA_BASIC_AUTH" not in service.credential_environment("finance", {})
    finally:
        service.pool.shutdown()


@pytest.mark.slow
def test_the_import_tool_asks_as_the_requester_and_waits_for_the_computer(api, botops, live, tmp_path):
    from runner.service import Runner
    local(api)
    machine = runner(api, label="Jira Mac")
    assign(api, machine, "finance")
    ready(api, machine, ["finance"])
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets" / "finance.env").write_text(f"JIRA_BASIC_AUTH={JIRA}\n")
    service = Runner({"url": live, "token": machine["token"], "projects_dir": str(tmp_path)}, tmp_path / "state",
                     host_factory=lambda attempt, env: None)
    try:
        ana = turn(api, botops, person="ana-test", text="Move finance's Jira credential into Credentials")
        err, waiting = mcp(api, "hub_credential_import", {"env": "JIRA_BASIC_AUTH", "from_bot": "finance", "wait": 0}, token=ana["token"])
        assert not err and waiting["state"] == "waiting" and JIRA not in json.dumps(waiting), waiting
        service.poll_credential_imports()
        listing = get(api, "credentials")["credentials"]
        assert [g["subject"] for g in listing[0]["grants"]] == ["bot:finance"]
        with api.app.state.store.read() as c:
            event = c.execute("SELECT actor,detail_json FROM events WHERE action='credential.import_requested'").fetchone()
            assert event["actor"] == "human:ana" and '"via": "botops"' in event["detail_json"]
        # A variable that is not in the file is a plain failure the tool reports, not a wait.
        (tmp_path / "secrets" / "finance.env").write_text("SOMETHING_ELSE=x\n")
        service._polled.pop("imports", None)
        err, still = mcp(api, "hub_credential_import", {"env": "MISSING_KEY", "from_bot": "finance", "wait": 0}, token=ana["token"])
        assert not err and still["state"] == "waiting"
        service.poll_credential_imports()
        with api.app.state.store.read() as c:
            failed = c.execute("SELECT state,message FROM credential_imports WHERE env='MISSING_KEY'").fetchone()
        assert failed["state"] == "failed" and "not in that bot's secrets file" in failed["message"]
        err, again = mcp(api, "hub_credential_import", {"env": "MISSING_KEY", "from_bot": "finance", "wait": 0}, token=ana["token"])
        assert not err and again["state"] == "waiting"            # asking again is a new request, not a stale answer
        assert JIRA not in everything(api)
    finally:
        service.pool.shutdown()


# ------------------------------------------------------------------ readiness counts a vault grant
