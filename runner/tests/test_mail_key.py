"""The company's Google key stays with the supervisor; an inbox bot's turn gets a token for its own mailboxes only."""
import os
import shutil
import stat
import tempfile
from unittest import mock

import pytest

from runner import credential_socket as C, isolation, mail_key
from runner.connectors import mail_secret_path


@pytest.fixture
def channel():
    directory = tempfile.mkdtemp(dir="/tmp")
    minted = []

    def mail(service, mailbox):
        minted.append((service, mailbox))
        return {"token": f"ya29.{mailbox}", "expiry": "2026-01-01T00:00:00"}
    server = C.Server(os.path.join(directory, "cred.sock"), lambda bot: "ghs", mail).start()
    server.minted = minted
    yield server
    server.stop()
    shutil.rmtree(directory, ignore_errors=True)


def test_only_the_inbox_bots_turn_gets_mail_and_only_for_its_mailboxes(channel):
    channel.register("attempt-inbox", "ana-inbox", ["ana@acme.example", "ben@acme.example"])
    channel.register("attempt-other", "helper")                    # a bot the hub named no mailbox for
    assert C.request_mail(channel.path, "attempt-inbox", "gmail", "Ana@Acme.example")["token"] == "ya29.ana@acme.example"
    for attempt, mailbox in (("attempt-other", "ana@acme.example"), ("attempt-inbox", "cara@acme.example"),
                             ("guess", "ana@acme.example")):
        with pytest.raises(ValueError):
            C.request_mail(channel.path, attempt, "gmail", mailbox)
    with pytest.raises(ValueError):
        C.request_mail(channel.path, "attempt-inbox", "drive", "ana@acme.example")     # no other service
    assert channel.minted == [("gmail", "ana@acme.example")]
    channel.unregister("attempt-inbox")
    with pytest.raises(ValueError):
        C.request_mail(channel.path, "attempt-inbox", "gmail", "ana@acme.example")


def test_the_key_moves_out_of_the_bots_reach_when_isolated(tmp_path, monkeypatch):
    monkeypatch.delenv("GOOGLE_SA_KEY", raising=False)
    home = tmp_path / "home"
    secrets = home / "workspace" / "secrets"
    secrets.mkdir(parents=True)
    (secrets / "google-sa.json").write_text('{"type": "service_account"}')
    os.chmod(secrets / "google-sa.json", 0o600)
    config = {"projects_dir": str(home / "workspace"), "state_dir": str(home / "state-r1")}
    bot_uid = os.getuid() + 1                     # any bot user other than the supervisor running this test
    monkeypatch.setattr(isolation, "identity", lambda: (bot_uid, 10002))
    assert mail_key.status(config) == "exposed"
    assert mail_key.protect(config) is True
    moved = home / "state-r1" / "google-sa.json"
    assert moved.read_text() == '{"type": "service_account"}' and not (secrets / "google-sa.json").exists()
    # Closed to the bot user: no group or other bits on the file or its directory, and the mail
    # job now reads it there.
    assert stat.S_IMODE(moved.stat().st_mode) == 0o600 and stat.S_IMODE(moved.parent.stat().st_mode) == 0o700
    assert moved.stat().st_uid == os.getuid() != bot_uid
    assert mail_secret_path(config) == moved and mail_key.status(config) == "protected"
    assert mail_key.protect(config) is False


def test_without_isolation_the_key_stays_and_is_reported_readable(tmp_path, monkeypatch):
    monkeypatch.delenv("GOOGLE_SA_KEY", raising=False)
    monkeypatch.delenv(isolation.UID_ENV, raising=False)
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets" / "google-sa.json").write_text("{}")
    config = {"projects_dir": str(tmp_path)}
    assert mail_key.protect(config) is False and mail_key.status(config) == "exposed"
    assert mail_secret_path(config) == tmp_path / "secrets" / "google-sa.json"


def test_the_mail_cli_asks_the_supervisor_only_inside_an_isolated_turn(channel, monkeypatch):
    from connectors.mail import auth
    channel.register("attempt-inbox", "ana-inbox", ["ana@acme.example"])
    monkeypatch.delenv(auth.SOCKET_ENV, raising=False)
    assert auth.supervisor_token("ana@acme.example", [auth.GMAIL_SCOPE]) is None        # Mac: the key file is used
    monkeypatch.setenv(auth.SOCKET_ENV, channel.path)
    monkeypatch.setenv("HUB_TOKEN", "attempt-inbox")
    assert auth.supervisor_token("ana@acme.example", [auth.GMAIL_SCOPE])["token"] == "ya29.ana@acme.example"
    with pytest.raises(auth.Failure):
        auth.supervisor_token("cara@acme.example", [auth.GMAIL_SCOPE])


def test_the_computer_holds_the_key_only_when_isolated_with_the_key_and_the_connectors_job(tmp_path, monkeypatch):
    monkeypatch.delenv("GOOGLE_SA_KEY", raising=False)
    monkeypatch.setenv("TICO_SIDE_JOBS", "1")
    config = {"projects_dir": str(tmp_path / "workspace"), "state_dir": str(tmp_path / "state-r1")}
    monkeypatch.setattr(isolation, "identity", lambda: (10003, 10002))
    assert mail_key.held_by_computer(config) is False                       # no key on this computer
    (tmp_path / "state-r1").mkdir()
    (tmp_path / "state-r1" / "google-sa.json").write_text("{}")
    assert mail_key.held_by_computer(config) is True
    monkeypatch.setenv("TICO_SIDE_JOBS", "0")
    assert mail_key.held_by_computer(config) is False                       # nothing runs the connectors job
    monkeypatch.setenv("TICO_SIDE_JOBS", "1")
    monkeypatch.setattr(isolation, "identity", lambda: None)
    assert mail_key.held_by_computer(config) is False                       # a bot reads the key itself there


def test_the_connectors_job_writes_group_writable_files_only_in_the_two_user_layout(monkeypatch):
    from runner.connectors import mail_umask
    monkeypatch.setattr(isolation, "identity", lambda: (10003, 10002))
    assert mail_umask() == {"umask": 0o002}
    monkeypatch.setattr(isolation, "identity", lambda: None)
    assert mail_umask() == {}


def previous_registration(tmp_path):
    import json
    config = {"runner_id": "new", "owner_id": "ana", "url": "https://tico.example.com", "environment": "team"}
    stale = tmp_path / "runner.json.stale"
    stale.write_text(json.dumps({**config, "runner_id": "old"}))
    stale.chmod(0o600)
    old = tmp_path / "state-old"
    old.mkdir(mode=0o711)
    key = old / mail_key.FILE
    key.write_text('{"type": "service_account"}')
    key.chmod(0o600)
    return config, key

def test_reenrollment_carries_the_protected_key_once_with_permissions(tmp_path):
    config, key = previous_registration(tmp_path)
    assert mail_key.carry_protected(config, tmp_path / "runner.json")
    new = tmp_path / "state-new" / mail_key.FILE
    assert new.read_bytes() == key.read_bytes()
    assert stat.S_IMODE(new.stat().st_mode) == stat.S_IMODE(key.stat().st_mode) == 0o600
    assert stat.S_IMODE(new.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE((new.parent / ".mail-key-carried").stat().st_mode) == 0o600
    new.write_text("existing")
    assert not mail_key.carry_protected(config, tmp_path / "runner.json")
    assert new.read_text() == "existing" and key.is_file()
    assert list(new.parent.glob(".mail-key-*")) == [new.parent / ".mail-key-carried"]


@pytest.mark.parametrize("missing", ["key"])
def test_missing_previous_mail_key_is_quiet_and_not_retried(tmp_path, monkeypatch, missing):
    config, key = previous_registration(tmp_path)
    if missing == "key":
        key.unlink()
    elif missing == "state-directory":
        shutil.rmtree(key.parent)
    else:
        (tmp_path / "runner.json.stale").unlink()
    warnings = []
    monkeypatch.setattr(mail_key, "log", warnings.append)
    assert not mail_key.carry_protected(config, tmp_path / "runner.json")
    assert warnings == []
    assert not (tmp_path / "state-new" / mail_key.FILE).exists()
    if missing != "stale-registration":
        key.parent.mkdir(mode=0o711, exist_ok=True)
        key.write_text("later-key")
        key.chmod(0o600)
        assert not mail_key.carry_protected(config, tmp_path / "runner.json")
        assert not (tmp_path / "state-new" / mail_key.FILE).exists()


def test_existing_registration_key_is_not_restored_after_deletion(tmp_path):
    config, key = previous_registration(tmp_path)
    new = tmp_path / "state-new" / mail_key.FILE
    new.parent.mkdir(mode=0o700)
    new.write_text("operator-key")
    new.chmod(0o600)
    assert not mail_key.carry_protected(config, tmp_path / "runner.json")
    assert new.read_text() == "operator-key"
    new.unlink()
    assert not mail_key.carry_protected(config, tmp_path / "runner.json")
    assert not new.exists() and key.exists()


def test_failed_carryover_can_retry_without_leaving_temporary_keys(tmp_path, monkeypatch):
    import errno

    config, key = previous_registration(tmp_path)
    warnings = []
    monkeypatch.setattr(mail_key, "log", warnings.append)
    with mock.patch.object(mail_key.os, "link", side_effect=OSError(errno.ENOSPC, "No space left")):
        assert not mail_key.carry_protected(config, tmp_path / "runner.json")
    new = tmp_path / "state-new" / mail_key.FILE
    assert warnings and "free space and permissions" in warnings[0]
    # Only the grants' marker: the old registration had no grants file, which is recorded once.
    assert [p.name for p in new.parent.iterdir()] == [".google-oauth-carried"] and key.is_file()
    assert mail_key.carry_protected(config, tmp_path / "runner.json")
    assert new.read_bytes() == key.read_bytes()


@pytest.mark.parametrize("change", ["operator", "file-symlink", "owner"])
def test_reenrollment_never_imports_another_operators_key(tmp_path, monkeypatch, change):
    config, key = previous_registration(tmp_path)
    if change in ("operator", "url", "environment"):
        config[change] = "different"
    elif change == "directory-symlink":
        moved = tmp_path / "other"
        key.parent.rename(moved)
        key.parent.symlink_to(moved, target_is_directory=True)
    elif change == "file-symlink":
        moved = tmp_path / "other-key"
        key.rename(moved)
        key.symlink_to(moved)
    elif change == "owner":
        monkeypatch.setattr(os, "getuid", lambda: key.stat().st_uid + 1)
    elif change == "volume":
        from pathlib import Path
        real = Path.lstat
        def different_volume(path):
            result = real(path)
            if path.name == "state-old":
                values = list(result)
                values[2] += 1
                return os.stat_result(values)
            return result
        monkeypatch.setattr(Path, "lstat", different_volume)
    assert not mail_key.carry_protected(config, tmp_path / "runner.json")
    assert not (tmp_path / "state-new" / mail_key.FILE).exists()


def test_connected_account_grants_alone_give_mail_access_and_stay_out_of_the_bots_reach(tmp_path, monkeypatch):
    monkeypatch.delenv("GOOGLE_SA_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_GRANTS", raising=False)
    monkeypatch.setenv("TICO_SIDE_JOBS", "1")
    home = tmp_path / "home"
    secrets = home / "workspace" / "secrets"
    secrets.mkdir(parents=True)
    (secrets / mail_key.GRANTS).write_text('{"grants": {}}')
    os.chmod(secrets / mail_key.GRANTS, 0o600)
    config = {"projects_dir": str(home / "workspace"), "state_dir": str(home / "state-r1")}
    monkeypatch.setattr(isolation, "identity", lambda: (os.getuid() + 1, 10002))
    assert mail_key.status(config) == "exposed"
    assert mail_key.protect(config) is True
    moved = home / "state-r1" / mail_key.GRANTS
    assert moved.read_text() == '{"grants": {}}' and stat.S_IMODE(moved.stat().st_mode) == 0o600
    assert not (secrets / mail_key.GRANTS).exists() and not (home / "state-r1" / mail_key.FILE).exists()
    assert mail_key.status(config) == "protected" and mail_key.held_by_computer(config)
    from runner.connectors import mail_credential_present, mail_grants_path
    assert mail_grants_path(config) == moved and mail_credential_present(config)


def test_a_connected_accounts_scopes_reach_the_turn_with_its_token():
    def mail(service, mailbox):
        return {"token": "ya29.t", "expiry": "", "scopes": ["https://www.googleapis.com/auth/gmail.readonly"]}
    directory = tempfile.mkdtemp(dir="/tmp")     # short: a Unix socket path has a length limit
    server = C.Server(os.path.join(directory, "cred.sock"), lambda bot: "ghs", mail).start()
    try:
        server.register("attempt", "inbox", ["ben@acme.example"])
        reply = C.request_mail(server.path, "attempt", "gmail", "ben@acme.example")
        assert reply["scopes"] == ["https://www.googleapis.com/auth/gmail.readonly"]
        with pytest.raises(ValueError):            # the grant is bound to its own mailbox only
            C.request_mail(server.path, "attempt", "gmail", "ana@acme.example")
    finally:
        server.stop()
        shutil.rmtree(directory, ignore_errors=True)


def test_a_second_grant_moved_from_the_workspace_keeps_the_first(tmp_path, monkeypatch):
    monkeypatch.delenv("GOOGLE_SA_KEY", raising=False)
    home = tmp_path / "home"
    secrets = home / "workspace" / "secrets"
    secrets.mkdir(parents=True)
    config = {"projects_dir": str(home / "workspace"), "state_dir": str(home / "state-r1")}
    monkeypatch.setattr(isolation, "identity", lambda: (os.getuid() + 1, 10002))
    for mailbox in ("ben@x.example", "ana@x.example"):
        (secrets / mail_key.GRANTS).write_text('{"grants": {"%s": {"refresh_token": "t-%s"}}}' % (mailbox, mailbox))
        os.chmod(secrets / mail_key.GRANTS, 0o600)
        assert mail_key.protect(config) is True
    import json
    kept = json.loads((home / "state-r1" / mail_key.GRANTS).read_text())["grants"]
    assert sorted(kept) == ["ana@x.example", "ben@x.example"]


def test_a_corrupt_workspace_grants_file_leaves_the_protected_one_alone(tmp_path, monkeypatch):
    monkeypatch.delenv("GOOGLE_SA_KEY", raising=False)
    home = tmp_path / "home"
    secrets = home / "workspace" / "secrets"
    secrets.mkdir(parents=True)
    config = {"projects_dir": str(home / "workspace"), "state_dir": str(home / "state-r1")}
    monkeypatch.setattr(isolation, "identity", lambda: (os.getuid() + 1, 10002))
    (secrets / mail_key.GRANTS).write_text('{"grants": {"ben@x.example": {"refresh_token": "t-ben"}}}')
    os.chmod(secrets / mail_key.GRANTS, 0o600)
    assert mail_key.protect(config) is True
    protected = (home / "state-r1" / mail_key.GRANTS).read_text()
    for broken in ("{not json", "[]"):
        (secrets / mail_key.GRANTS).write_text(broken)
        os.chmod(secrets / mail_key.GRANTS, 0o600)
        mail_key.protect(config)                                    # logs, never raises
        assert (home / "state-r1" / mail_key.GRANTS).read_text() == protected
        assert (secrets / mail_key.GRANTS).is_file()                # left for a person to look at


def test_the_supervisor_s_mint_reaches_the_server_that_refreshes_connect_google_never_with_the_hub_token(
        tmp_path, monkeypatch):
    script = tmp_path / "mail.sh"
    script.write_text('#!/bin/sh\n[ -z "$HUB_TOKEN" ] || exit 9\n'
                      'printf \'{"token": "ya29.t", "expiry": "", "url": "%s", "auth": "%s"}\' '
                      '"$TICO_GOOGLE_BROKER_URL" "$TICO_GOOGLE_BROKER_AUTH"\n')
    script.chmod(0o755)
    monkeypatch.setenv("HUB_TOKEN", "hub-token")
    monkeypatch.setenv("TICO_GOOGLE_BROKER_AUTH", "inherited")       # never one the runner did not set itself
    config = {"url": "https://team.acme.example", "token": "runner-token", "state_dir": str(tmp_path)}
    minted = mail_key.minter(config, script)("gmail", "ana@acme.example")
    assert (minted["url"], minted["auth"]) == ("https://team.acme.example", "runner-token")
    minted = mail_key.minter({"state_dir": str(tmp_path)}, script)("gmail", "ana@acme.example")
    assert (minted["url"], minted["auth"]) == ("", "")
