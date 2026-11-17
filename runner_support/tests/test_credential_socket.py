"""A turn that cannot read the runner's registration still gets its own bot's GitHub token, and only that: the client
side of the supervisor's credential socket (runner-rs/src/gitcred.rs serves it and holds its own tests)."""

from unittest import mock

import pytest

from runner_support import credential_socket as C, isolation
from runner_support.tests.helpers.supervisor import Supervisor


@pytest.fixture(autouse=True)
def no_turn_tokens(monkeypatch):
    """Inside a bot's turn TICO_GITHUB_TOKENS is set, and the helper would answer from it instead of the socket."""
    monkeypatch.delenv("TICO_GITHUB_TOKENS", raising=False)


@pytest.fixture
def channel():
    tokens = {"attempt-a": "ghs_alpha"}

    def answer(asked):
        if asked.get("refresh"):
            return {"refreshed": True} if asked["token"] in tokens else {"error": "mirror refresh unavailable"}
        token = tokens.get(asked.get("token"))
        return {"token": token} if token else {"error": "unknown attempt"}

    server = Supervisor(answer)
    yield server
    server.close()


def test_the_client_refreshes_a_mirror_and_raises_on_a_refusal(channel):
    assert C.request_refresh(channel.path, "attempt-a", "org/product") == {"refreshed": True}
    assert channel.asked[-1] == {"token": "attempt-a", "repository": "org/product", "refresh": True}
    with pytest.raises(ValueError, match="mirror refresh unavailable"):
        C.request_refresh(channel.path, "guess", "org/product")


def test_bot_code_is_wrapped_only_when_told_to_and_only_as_another_user(monkeypatch):
    monkeypatch.delenv(isolation.UID_ENV, raising=False)
    assert isolation.wrap(["git", "status"], {"cwd": "x"}) == (["git", "status"], {"cwd": "x"})
    monkeypatch.setenv(isolation.UID_ENV, "10003")
    monkeypatch.setenv(isolation.GID_ENV, "10002")
    with mock.patch("os.geteuid", return_value=10003):  # already the bot user: nothing to drop to
        assert isolation.identity() is None
    with mock.patch("os.geteuid", return_value=10002):
        argv, kwargs = isolation.wrap(["codex", "app-server"], {})
    assert argv[:1] == [isolation.SETPRIV] and argv[-2:] == ["codex", "app-server"]
    assert "--reuid=10003" in argv and "--regid=10002" in argv and "--ambient-caps=-all" in argv
    assert kwargs == {"umask": 0o002}
