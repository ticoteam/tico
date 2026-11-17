"""Code a bot runs drops to the bot user only when the runner is told to separate them (runner_support/isolation.py),
the user a turn's credential socket answers (runner-rs/src/gitcred.rs serves it and holds its own tests)."""

from unittest import mock

from runner_support import isolation


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
