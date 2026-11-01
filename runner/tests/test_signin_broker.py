"""Synthetic credentials only: no provider call, production home or Hub database."""
import base64
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "runner-rs/src/hosts/signin_broker.py"
spec = importlib.util.spec_from_file_location("signin_broker", SOURCE)
broker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(broker)


def token(seconds=7200, label="access"):
    payload = json.dumps({"exp": time.time() + seconds, "label": label}).encode()
    return "synthetic." + base64.urlsafe_b64encode(payload).decode().rstrip("=") + ".fixture"


@pytest.fixture
def login(tmp_path, monkeypatch):
    # Clear the entire inherited turn environment, including live scoped credentials.
    for key in list(os.environ):
        monkeypatch.delenv(key)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    return tmp_path


def codex_auth(root, access=None, refresh="synthetic-refresh-never-granted", account="acme"):
    home = root / ".codex"
    home.mkdir(exist_ok=True)
    path = home / "auth.json"
    path.write_text(json.dumps({"auth_mode": "chatgpt", "tokens": {
        "access_token": access or token(), "refresh_token": refresh, "account_id": account,
        "id_token": "synthetic-identity-never-granted"}}))
    path.chmod(0o600)
    return path


def test_access_only_does_not_export_refresh_or_identity_token(login):
    path = codex_auth(login)
    original = path.read_bytes()
    result = broker.grant("codex-external", 60)
    assert set(result) == {"accessToken", "chatgptAccountId"}
    assert result["chatgptAccountId"] == "acme"
    assert "synthetic-refresh-never-granted" not in json.dumps(result)
    assert "synthetic-identity-never-granted" not in json.dumps(result)
    assert path.read_bytes() == original


def test_a_duplicate_refresh_request_reuses_the_first_rotation(login, monkeypatch):
    stale = token(-1)
    codex_auth(login, stale)
    calls = []
    fresh = token(label="rotated")

    def refresh():
        calls.append(True)
        codex_auth(login, fresh, "rotated-refresh-never-granted")

    monkeypatch.setattr(broker, "codex_refresh", refresh)
    first = broker.grant("codex-external", 60, "acme", stale)
    second = broker.grant("codex-external", 60, "acme", stale)
    assert calls == [True]
    assert first == second == {"accessToken": fresh, "chatgptAccountId": "acme"}


def test_account_switch_does_not_refresh_or_grant_the_new_account(login, monkeypatch):
    codex_auth(login, account="different")
    monkeypatch.setattr(broker, "codex_refresh", lambda: pytest.fail("wrong account refreshed"))
    with pytest.raises(ValueError, match="account changed"):
        broker.grant("codex-external", 60, "original", "old-access")


def test_account_change_during_refresh_is_denied(login, monkeypatch):
    codex_auth(login, token(-1))
    monkeypatch.setattr(broker, "codex_refresh", lambda: codex_auth(login, account="different"))
    with pytest.raises(ValueError, match="account changed during refresh"):
        broker.grant("codex-external", 60, "acme")


@pytest.mark.parametrize("kind", ["symlink", "ancestor", "hardlink", "fifo", "oversized"])
def test_malicious_login_files_are_refused_without_following_or_blocking(login, kind):
    target = login / "plain.json"
    target.write_text("{}")
    target.chmod(0o600)
    path = login / "candidate.json"
    if kind == "symlink":
        path.symlink_to(target)
    elif kind == "ancestor":
        (login / "linked").symlink_to(login, target_is_directory=True)
        path = login / "linked/plain.json"
    elif kind == "hardlink":
        os.link(target, path)
    elif kind == "fifo":
        os.mkfifo(path)
    else:
        path.write_bytes(b"x" * (broker.LIMIT + 1))
        path.chmod(0o600)
    # Run adversarial reads out of process: a missing NONBLOCK would otherwise hang the whole suite.
    code = "import importlib.util; s=importlib.util.spec_from_file_location('b',%r); b=importlib.util.module_from_spec(s); s.loader.exec_module(b); b.read_json(%r)" % (str(SOURCE), str(path))
    done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, timeout=3)
    assert done.returncode != 0
    assert target.read_text() == "{}"


def test_claude_refreshes_before_handing_out_an_access_token(login, monkeypatch):
    folder = login / ".claude"
    folder.mkdir()
    path = folder / ".credentials.json"

    def save(expires):
        path.write_text(json.dumps({"claudeAiOauth": {"accessToken": "synthetic-access",
            "refreshToken": "synthetic-refresh", "expiresAt": expires,
            "scopes": ["user:inference", "user:profile"]}}))
        path.chmod(0o600)

    save(0)
    seen = []

    def refresh(argv, **kwargs):
        assert argv == ["claude", "auth", "login"]
        assert kwargs["env"]["CLAUDE_CODE_OAUTH_REFRESH_TOKEN"] == "synthetic-refresh"
        assert kwargs["stdin"] == subprocess.DEVNULL
        seen.append(True)
        save((time.time() + 7200) * 1000)

    monkeypatch.setattr(subprocess, "run", refresh)
    result = broker.grant("claude-oauth", 3660)
    assert result["accessToken"] == "synthetic-access"
    assert set(result) == {"accessToken", "expiresAt"}
    assert result["expiresAt"] > time.time() + 3660
    assert seen == [True]


def test_claude_refuses_a_token_that_cannot_cover_the_turn(login, monkeypatch):
    folder = login / ".claude"
    folder.mkdir()
    (folder / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {
        "accessToken": "synthetic-expired", "refreshToken": "synthetic-refresh", "expiresAt": 0, "scopes": []}}))
    (folder / ".credentials.json").chmod(0o600)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: None)
    with pytest.raises(ValueError, match="lifetime is too short"):
        broker.grant("claude-oauth", 3660)


def test_failed_helper_prints_no_credentials_or_provider_errors(login):
    codex_auth(login, "not-a-jwt-SYNTHETIC-SECRET")
    done = subprocess.run([sys.executable, "-I", str(SOURCE)], input='{"kind":"codex-external","minimum":60}',
                          capture_output=True, text=True, timeout=3)
    assert done.returncode == 1 and done.stdout == done.stderr == ""


def test_stored_codex_api_key_needs_no_subscription_refresh(login, monkeypatch):
    home = login / ".codex"
    home.mkdir()
    (home / "auth.json").write_text(json.dumps({"OPENAI_API_KEY": "synthetic-api-key"}))
    (home / "auth.json").chmod(0o600)
    monkeypatch.setattr(broker, "codex_refresh", lambda: pytest.fail("API keys must not refresh"))
    assert broker.grant("codex-external", 60) == {"apiKey": "synthetic-api-key"}


def state_module():
    source = SOURCE.with_name("turn_state.py")
    spec = importlib.util.spec_from_file_location("turn_state", source)
    state = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(state)
    return state


def test_conversation_state_round_trip_never_copies_sign_in_or_configuration(login):
    state = state_module()
    original, saved, restored = [login / name for name in ("original", "saved", "restored")]
    for folder in (original, saved, restored):
        folder.mkdir()
    (original / "sessions").mkdir()
    (original / "sessions/turn.jsonl").write_text("synthetic conversation\n")
    for name in ("auth.json", ".credentials.json", "config.toml", "settings.json"):
        (original / name).write_text("synthetic-private-login-never-copied")
    args = dict(source_uid=os.geteuid(), target_uid=os.geteuid(), gid=os.getegid())
    state.copy(original, saved, **args)
    state.copy(saved, restored, **args)
    assert (restored / "sessions/turn.jsonl").read_text() == "synthetic conversation\n"
    assert sorted(p.name for p in restored.iterdir()) == ["sessions"]
    assert (restored / "sessions/turn.jsonl").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo", "foreign", "oversized"])
def test_provider_snapshot_refuses_unsafe_or_oversized_inodes(login, kind, monkeypatch):
    state = state_module()
    source, destination = login / "source", login / "destination"
    source.mkdir()
    destination.mkdir()
    (source / "sessions").mkdir()
    item = source / "sessions/entry"
    outside = login / "outside"
    outside.write_text("never changed")
    uid = os.geteuid()
    if kind == "symlink":
        item.symlink_to(outside)
    elif kind == "hardlink":
        os.link(outside, item)
    elif kind == "fifo":
        os.mkfifo(item)
    else:
        item.write_text("large enough")
        if kind == "foreign":
            uid += 1
        else:
            monkeypatch.setattr(state, "MAX_FILE", 1)
    with pytest.raises((ValueError, OSError)):
        state.copy(source, destination, uid, os.geteuid(), os.getegid())
    assert outside.read_text() == "never changed"


def test_broker_refuses_credentials_readable_by_other_users(login):
    path = codex_auth(login)
    path.chmod(0o644)
    with pytest.raises(ValueError, match="owned plain file"):
        broker.grant("codex-external", 60)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux broker lifetime binding")
def test_helper_refuses_to_refresh_after_its_supervisor_has_gone(login):
    path = codex_auth(login)
    before = path.read_bytes()
    request = json.dumps({"kind": "codex-external", "minimum": 60, "parent": -1})
    done = subprocess.run([sys.executable, "-I", str(SOURCE)], input=request, capture_output=True, text=True, timeout=3)
    assert done.returncode != 0 and done.stdout == done.stderr == ""
    assert path.read_bytes() == before
