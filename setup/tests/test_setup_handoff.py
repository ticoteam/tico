"""TICO_SETUP_HANDOFF: the one-time secret HQ gives the installer for the owner's coding agent. It comes from the
environment only, the server gets its sha256 (TICO_SETUP_HANDOFF_HASH), and the secret reaches no output, command line,
saved setting or private copy of the .env."""

import hashlib
import json

import pytest

from setup import contract, envfile, state, verify
from setup.tests.fakes import FakeShell
from setup.tests.helpers.hosted import DOMAIN, HOSTED, env_sent, go, over_ssh
from setup.tests.helpers.hosted_oidc import ISSUER, OIDC

SECRET = "handoff-SECRET_0123456789abcdefghijklmnopqrstuv"
ARGV = ["--target", "ssh", "--ssh", "root@203.0.113.7", "--yes", "--hosted", "invited", "--company", "Acme", *HOSTED]


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(verify, "run_all", lambda **kw: [verify.Check("stub", True, "ok")])
    monkeypatch.delenv("TICO_GATEWAY_INVITE", raising=False)
    monkeypatch.delenv("TICO_SETUP_HANDOFF", raising=False)
    monkeypatch.setenv("TICO_OIDC_CLIENT_SECRET", "client-SECRET-0123456789abcdef")


def test_the_server_gets_only_the_secrets_hash_and_the_secret_is_never_shown_or_saved(monkeypatch):
    monkeypatch.setenv("TICO_SETUP_HANDOFF", SECRET)
    shell = FakeShell()
    code, out = go([*ARGV, *OIDC], deps=over_ssh(shell))
    assert code == 0, out
    assert env_sent(shell)["TICO_SETUP_HANDOFF_HASH"] == hashlib.sha256(SECRET.encode()).hexdigest()
    cmds = "\n".join(shell.cmds())
    assert SECRET not in out and SECRET not in cmds
    # Not in the settings this computer keeps, nor in its copy of the .env; only the hash, which is no secret.
    saved, saved_env = state.load(DOMAIN)
    assert SECRET not in json.dumps(saved) + saved_env
    assert saved["setup_handoff_hash"] == hashlib.sha256(SECRET.encode()).hexdigest()
    assert envfile.parse(saved_env)["TICO_SETUP_HANDOFF_HASH"] == saved["setup_handoff_hash"]
    assert "TICO_SETUP_HANDOFF_HASH" in contract.ENV_ORDER and "TICO_SETUP_HANDOFF" not in contract.ENV_ORDER
    # The plan says what it does without showing the secret.
    assert "Setup handoff: the server keeps only the hash" in out


def test_without_the_variable_no_hash_is_written():
    shell = FakeShell()
    code, out = go([*ARGV, *OIDC], deps=over_ssh(shell))
    assert code == 0, out
    assert "TICO_SETUP_HANDOFF_HASH" not in env_sent(shell) and "Setup handoff" not in out
    assert state.load(DOMAIN)[0]["setup_handoff_hash"] == ""


def test_a_rerun_without_the_variable_keeps_the_first_hash_and_a_new_secret_replaces_it(monkeypatch):
    monkeypatch.setenv("TICO_SETUP_HANDOFF", SECRET)
    assert go([*ARGV, *OIDC], deps=over_ssh(FakeShell()))[0] == 0
    monkeypatch.delenv("TICO_SETUP_HANDOFF")
    again = FakeShell()
    assert go(ARGV, deps=over_ssh(again))[0] == 0
    first = hashlib.sha256(SECRET.encode()).hexdigest()
    assert env_sent(again)["TICO_SETUP_HANDOFF_HASH"] == first
    other = "other-SECRET_0123456789abcdefghijklmnopqrstuvw"
    monkeypatch.setenv("TICO_SETUP_HANDOFF", other)
    newer = FakeShell()
    assert go(ARGV, deps=over_ssh(newer))[0] == 0
    assert env_sent(newer)["TICO_SETUP_HANDOFF_HASH"] == hashlib.sha256(other.encode()).hexdigest()


@pytest.mark.parametrize("bad", ["short", "has spaces in it 0123456789abcdefghijklmnopqrstuvwxyz", "x" * 129, "é" * 40])
def test_a_secret_the_server_would_not_accept_stops_the_run_without_showing_it(monkeypatch, bad):
    monkeypatch.setenv("TICO_SETUP_HANDOFF", bad)
    shell = FakeShell()
    code, out = go([*ARGV, *OIDC], deps=over_ssh(shell))
    assert code != 0 and "TICO_SETUP_HANDOFF is not a setup handoff secret" in out and bad not in out
    assert not shell.calls
