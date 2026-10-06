"""A turn that cannot read the runner's registration still gets its own bot's GitHub token, and only that."""
import io
import os
import shutil
import tempfile
from unittest import mock

import pytest

from runner import credential_socket as C, git_credentials as G, isolation


@pytest.fixture
def channel():
    directory = tempfile.mkdtemp(dir="/tmp")     # short: a Unix socket path has a length limit
    minted = []

    def mint(bot):
        minted.append(bot)
        return f"ghs_{bot}"
    server = C.Server(os.path.join(directory, "cred.sock"), mint).start()
    server.minted = minted
    yield server
    server.stop()
    shutil.rmtree(directory, ignore_errors=True)


def test_an_attempt_gets_a_token_for_its_own_bot_only(channel):
    channel.register("attempt-a", "alpha")
    channel.register("attempt-b", "beta")
    assert C.request(channel.path, "attempt-a") == "ghs_alpha"
    assert C.request(channel.path, "attempt-b") == "ghs_beta"
    assert channel.minted == ["alpha", "beta"]      # the request cannot name a bot: there is no field for it


def test_unknown_or_finished_attempts_get_nothing(channel):
    channel.register("attempt-a", "alpha")
    for token in ("", "guess", "attempt-a "):
        with pytest.raises(ValueError):
            C.request(channel.path, token)
    channel.unregister("attempt-a")
    with pytest.raises(ValueError):
        C.request(channel.path, "attempt-a")
    assert channel.minted == []


def test_the_helper_uses_the_socket_and_never_the_registration(channel, monkeypatch, capsys):
    channel.register("attempt-a", "alpha")
    monkeypatch.setenv("HUB_TOKEN", "attempt-a")
    monkeypatch.setattr("sys.stdin", io.StringIO("protocol=https\nhost=github.com\n\n"))
    G.main(["--socket", channel.path, "--bot", "alpha"])           # no --config: the file is not readable to a turn
    assert capsys.readouterr().out == "username=x-access-token\npassword=ghs_alpha\n"


def test_bot_code_is_wrapped_only_when_told_to_and_only_as_another_user(monkeypatch):
    monkeypatch.delenv(isolation.UID_ENV, raising=False)
    assert isolation.wrap(["git", "status"], {"cwd": "x"}) == (["git", "status"], {"cwd": "x"})
    monkeypatch.setenv(isolation.UID_ENV, "10003")
    monkeypatch.setenv(isolation.GID_ENV, "10002")
    with mock.patch("os.geteuid", return_value=10003):       # already the bot user: nothing to drop to
        assert isolation.identity() is None
    with mock.patch("os.geteuid", return_value=10002):
        argv, kwargs = isolation.wrap(["codex", "app-server"], {})
    assert argv[:1] == [isolation.SETPRIV] and argv[-2:] == ["codex", "app-server"]
    assert "--reuid=10003" in argv and "--regid=10002" in argv and "--ambient-caps=-all" in argv
    assert kwargs == {"umask": 0o002}


def test_socket_preserves_repository_selection_and_attempt_identity(monkeypatch, capsys):
    grants = {"configured": True, "token": "write-token", "tokens": [
        {"token": "write-token", "repositories": ["Acme/product"]},
        {"token": "read-token", "repositories": ["Acme/docs"]}]}
    calls = []
    class Hub:
        def post(self, path, body):
            calls.append((path, body))
            return grants
    directory = tempfile.mkdtemp(dir="/tmp")
    monkeypatch.setattr(isolation, "enabled", lambda: True)
    server = C.serve(Hub(), os.path.join(directory, "cred.sock"))
    try:
        server.register("attempt-a", "alpha")
        assert C.request(server.path, "attempt-a", repository="Acme/docs") == "read-token"
        assert C.request(server.path, "attempt-a", repository="Acme/product") == "write-token"
        assert calls == [("github/token", {"bot": "alpha"})] * 2
        monkeypatch.setenv("HUB_TOKEN", "attempt-a")
        monkeypatch.setattr("sys.stdin", io.StringIO("protocol=https\nhost=github.com\npath=Acme/docs.git\n\n"))
        G.main(["--socket", server.path, "--bot", "another-bot"])
        assert capsys.readouterr().out == "username=x-access-token\npassword=read-token\n"
        assert calls[-1] == ("github/token", {"bot": "alpha"})
        with pytest.raises(ValueError):
            C.request(server.path, "attempt-a", repository="Acme/unknown")
        grants.pop("tokens")
        assert C.request(server.path, "attempt-a", repository="Acme/docs") == "write-token"
        server.unregister("attempt-a")
        with pytest.raises(ValueError):
            C.request(server.path, "attempt-a", repository="Acme/docs")
    finally:
        server.stop()
        shutil.rmtree(directory)


def test_refreshed_tokens_join_attempt_redactor_and_are_released(channel):
    from runner.redact import Redactor, MASK
    channel.register('attempt-a', 'alpha')
    # Publishing can ask for a token before the turn redactor has been built.
    first = C.request(channel.path, 'attempt-a')
    redactor = Redactor(['start-of-turn-secret'])
    channel.set_redactor('attempt-a', redactor)
    assert redactor.scrub_text(first) == MASK
    channel.mint = lambda bot: 'refreshed-read-secret'
    fresh = C.request(channel.path, 'attempt-a')
    assert redactor.scrub_text(fresh) == MASK
    channel.unregister('attempt-a')
    assert not channel.redactors and not channel.issued


def test_mirror_refresh_requires_live_attempt_and_its_repository(channel):
    refresh = mock.Mock(return_value={'refreshed': True})
    channel.refresh = refresh
    channel.mint = lambda bot, repository=None: 'synthetic-repo-token' if bot == 'alpha' and repository == 'org/product' else None
    channel.register('attempt-a', 'alpha')
    assert C.request_refresh(channel.path, 'attempt-a', 'org/product') == {'refreshed': True}
    refresh.assert_called_once_with('org/product', 'synthetic-repo-token')
    for token, repo in [('attempt-a', 'org/other'), ('unknown', 'org/product')]:
        with pytest.raises(ValueError):
            C.request_refresh(channel.path, token, repo)
    channel.unregister('attempt-a')
    with pytest.raises(ValueError):
        C.request_refresh(channel.path, 'attempt-a', 'org/product')
    assert refresh.call_count == 1
