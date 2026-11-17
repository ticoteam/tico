"""The company's Google key stays with the supervisor; an inbox bot's turn gets a token for its own mailboxes only."""

import os
import shutil
import stat
from unittest import mock

import pytest

from runner_support import mail_key
from runner_support.tests.helpers.supervisor import Supervisor


@pytest.fixture
def channel():
    """The supervisor's socket (runner-rs/src/gitcred.rs) for an inbox bot's attempt that may open ana@acme.example."""

    def answer(asked):
        mail = asked.get("mail") or {}
        if asked.get("token") != "attempt-inbox" or mail.get("mailbox") != "ana@acme.example":
            return {"error": "not this bot's mailbox", "mailboxes": ["ana@acme.example"]}
        return {"token": f"ya29.{mail['mailbox']}", "expiry": "2026-01-01T00:00:00"}

    server = Supervisor(answer)
    yield server
    server.close()


def test_the_mail_cli_asks_the_supervisor_only_inside_an_isolated_turn(channel, monkeypatch):
    from connectors.mail import auth

    monkeypatch.delenv(auth.SOCKET_ENV, raising=False)
    assert auth.supervisor_token("ana@acme.example", [auth.GMAIL_SCOPE]) is None  # Mac: the key file is used
    monkeypatch.setenv(auth.SOCKET_ENV, channel.path)
    monkeypatch.setenv("HUB_TOKEN", "attempt-inbox")
    assert auth.supervisor_token("ana@acme.example", [auth.GMAIL_SCOPE])["token"] == "ya29.ana@acme.example"
    with pytest.raises(auth.Failure):
        auth.supervisor_token("cara@acme.example", [auth.GMAIL_SCOPE])


def previous_registration(tmp_path):
    import json

    config = {"runner_id": "new", "operator": "ana", "url": "https://tico.example.com", "environment": "team"}
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
