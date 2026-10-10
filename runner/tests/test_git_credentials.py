"""The turn's GitHub App token reaches git and gh through the environment only, and git asks for a fresh one each time."""
import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from runner import git_credentials as G

import pytest


class Hub:
    def __init__(self, answer=None, error=None):
        self.answer, self.error, self.calls = answer, error, []

    def post(self, path, body):
        self.calls.append((path, body))
        if self.error:
            raise self.error
        return self.answer


class HubServer:
    """A loopback stand-in for POST /api/v2/github/token that mints a different token per request."""

    def __init__(self):
        outer = self
        outer.count, outer.seen = 0, []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                outer.seen.append((self.path, self.headers.get("Authorization"), json.loads(self.rfile.read(length))))
                outer.count += 1
                body = json.dumps({"configured": True, "token": f"ghs_fresh{outer.count}"}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass
        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=lambda: self.server.serve_forever(0.02), daemon=True).start()

    def close(self):
        self.server.shutdown()


def fill(env, tmp_path):
    return subprocess.run(["git", "credential", "fill"], input="protocol=https\nhost=github.com\n\n", env=env,
                          capture_output=True, text=True, timeout=30, cwd=tmp_path).stdout


def test_git_gets_a_fresh_token_on_every_credential_request_and_nothing_is_written(tmp_path):
    hub = HubServer()
    try:
        config = tmp_path / "config" / "runner.json"
        config.parent.mkdir()
        config.write_text(json.dumps({"url": hub.url, "token": "runner-secret"}))
        home = tmp_path / "home"
        home.mkdir()
        env = {"PATH": "/usr/bin:/bin", "HOME": str(home), "GIT_CONFIG_NOSYSTEM": "1"}
        assert G.apply(env, Hub({"configured": True, "token": "ghs_start"}), "cpo", config) is True
        assert env["GH_TOKEN"] == "ghs_start"           # gh reads this one; only git refreshes
        assert "ghs_start" not in env["GIT_CONFIG_VALUE_1"] and "runner-secret" not in "".join(env.values())
        before = sorted(p.name for p in tmp_path.rglob("*"))
        first, second = fill(env, tmp_path), fill(env, tmp_path)
        assert "username=x-access-token" in first and "password=ghs_fresh1" in first
        assert "password=ghs_fresh2" in second
        assert hub.seen[0] == ("/api/v2/github/token", "Bearer runner-secret", {"bot": "cpo", "purpose": "git"})
        assert sorted(p.name for p in tmp_path.rglob("*")) == before
        # An unrelated host gets nothing, and neither does a store or erase.
        other = subprocess.run(["git", "credential", "fill"], input="protocol=https\nhost=example.com\n\n",
                               env={**env, "GIT_ASKPASS": "true"}, capture_output=True, text=True, timeout=30, cwd=tmp_path)
        assert "ghs_" not in other.stdout and hub.count == 2
    finally:
        hub.close()


def test_mixed_grants_select_the_remote_token_without_persisting_it(tmp_path, monkeypatch):
    grants = {"configured": True, "token": "write-token", "tokens": [
        {"token": "write-token", "repositories": ["Acme/product"], "access": "write"},
        {"token": "read-token", "repositories": ["Acme/docs"], "access": "read"}]}
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "GIT_CONFIG_NOSYSTEM": "1"}
    assert G.apply(env, Hub(grants), "alpha")
    for repo, expected in (("Acme/product.git", "write-token"), ("acme/docs.git", "read-token")):
        result = subprocess.run(["git", "credential", "fill"],
                                input=f"protocol=https\nhost=github.com\npath={repo}\n\n",
                                env=env, cwd=tmp_path, capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr
        assert f"password={expected}" in result.stdout
    assert list(tmp_path.iterdir()) == []
    assert env["GIT_CONFIG_VALUE_2"] == "true"
    assert G.select_token(grants, "Acme/unknown") == ""
    assert G.select_token({"configured": True, "token": "old-token"}, "Acme/docs") == "old-token"


def test_fresh_groups_and_outage_fallback_keep_repository_selection(tmp_path, monkeypatch):
    grants = {"configured": True, "token": "write-token", "tokens": [
        {"token": "write-token", "repositories": ["Acme/product"]},
        {"token": "read-token", "repositories": ["Acme/docs"]}]}
    config = tmp_path / "runner.json"
    config.write_text(json.dumps({"url": "https://example.com", "token": "registration"}))
    hub = Hub(grants)
    monkeypatch.setattr("clients.tico.Client", lambda *a, **kw: hub)
    assert G.credential(config, "alpha", repository="Acme/docs") == "read-token"
    assert G.credential(config, "alpha", repository="Acme/product") == "write-token"
    assert hub.calls == [("github/token", {"bot": "alpha", "purpose": "git"})] * 2
    monkeypatch.setenv(G.TOKENS_KEY, json.dumps(grants["tokens"]))
    monkeypatch.setenv("GH_TOKEN", "write-token")
    hub.error = OSError()
    assert G.credential(config, "alpha", repository="Acme/docs") == "read-token"
    assert G.credential(config, "alpha", repository="Acme/unknown") == ""
    monkeypatch.delenv(G.TOKENS_KEY)
    hub.answer, hub.error = {"configured": True, "token": "old-token"}, None
    assert G.credential(config, "alpha", repository="Acme/docs") == "old-token"


def test_a_held_token_is_reused_until_near_expiry_instead_of_asking_per_git_command(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    def at(minutes):
        return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
    grants = {"configured": True, "token": "write-token", "expires_at": at(60), "tokens": [
        {"token": "write-token", "repositories": ["Acme/product"], "expires_at": at(60)},
        {"token": "read-token", "repositories": ["Acme/docs"], "expires_at": at(60)}]}
    hub = Hub(grants)
    # The supervisor holds the hub's answer: one request for many git commands, per purpose.
    for _ in range(5):
        assert G.select_token(G.hub_token(hub, {"bot": "alpha", "purpose": "git"}), "Acme/docs") == "read-token"
    assert G.hub_token(hub, {"bot": "beta", "purpose": "git"}) is grants
    assert len(hub.calls) == 2
    # Under the turn's 45 minutes, a turn-start ask goes back to the hub; errors are never held.
    grants["expires_at"] = grants["tokens"][0]["expires_at"] = at(30)
    G.hub_token(hub, {"bot": "gamma"})
    G.hub_token(hub, {"bot": "gamma"})
    assert len(hub.calls) == 4
    hub.error = OSError()
    with pytest.raises(OSError):
        G.hub_token(hub, {"bot": "delta", "purpose": "git"})
    # The credential helper reuses the turn's own token while it has ten minutes left, and asks once it has not.
    config = tmp_path / "runner.json"
    config.write_text(json.dumps({"url": "https://example.com", "token": "registration"}))
    fresh = Hub({"configured": True, "token": "new-token"})
    monkeypatch.setattr("clients.tico.Client", lambda *a, **kw: fresh)
    monkeypatch.setenv(G.TOKENS_KEY, json.dumps([{**grants["tokens"][1], "expires_at": at(30)}]))
    assert G.credential(config, "alpha", repository="Acme/docs") == "read-token" and not fresh.calls
    assert G.credential(config, "alpha", repository="Acme/other") == "new-token" and len(fresh.calls) == 1
    monkeypatch.setenv(G.TOKENS_KEY, json.dumps([{**grants["tokens"][1], "expires_at": at(9)}]))
    assert G.credential(config, "alpha", repository="Acme/docs") == "new-token" and len(fresh.calls) == 2


def test_gh_wrapper_uses_the_read_token_for_explicit_and_local_repositories(tmp_path, monkeypatch):
    import os
    import shutil
    gh = tmp_path / "gh"
    gh.write_text("#!/bin/sh\nprintf '%s' \"$GH_TOKEN\"\n")
    gh.chmod(0o755)
    env = {"PATH": str(tmp_path) + os.pathsep + os.environ["PATH"], "HOME": str(tmp_path)}
    grants = {"configured": True, "token": "write-token", "tokens": [
        {"token": "write-token", "repositories": ["Acme/product"]},
        {"token": "read-token", "repositories": ["Acme/docs"]}]}
    assert G.apply(env, Hub(grants), "alpha")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "remote", "add", "origin", "https://github.com/Acme/docs.git"], check=True)
    for args, expected in ((["repo", "view"], "read-token"),
                           (["repo", "view", "-R", "Acme/product"], "write-token")):
        result = subprocess.run([shutil.which("gh", path=env["PATH"]), *args], env=env, cwd=tmp_path,
                                capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr
        assert result.stdout == expected
    assert "token" not in (tmp_path / ".git" / "config").read_text()


def test_gh_placeholders_and_text_urls_select_checkout_origin(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    subprocess.run(['git', 'init', '-q'], check=True)
    subprocess.run(['git', 'remote', 'add', 'origin', 'https://github.com/Acme/docs.git'], check=True)
    for args in (['api', 'repos/{owner}/{repo}/pulls'],
                 ['pr', 'comment', '5', '--body', 'https://github.com/Acme/product/pull/3']):
        assert G.gh_repository(args, {}) == 'Acme/docs'
    for flag in ('--body', '-f'):
        assert G.gh_repository(['issue', 'close', '5', flag, 'https://github.com/Acme/product/issues/1'], {}) == 'Acme/docs'
        assert G.gh_repository(['issue', 'close', '5', flag, '--repo=Acme/product'], {}) == 'Acme/docs'
        assert G.gh_repository(['issue', 'close', '5', flag + '=https://github.com/Acme/product/issues/1'], {}) == 'Acme/docs'
    assert G.gh_repository(['pr', 'view', 'https://github.com/Acme/product/pull/3'], {}) == 'Acme/product'


def test_each_granted_token_is_redacted_after_apply():
    from runner import redact
    grants = {'configured': True, 'token': 'ghs_write_secret', 'tokens': [
        {'token': 'ghs_write_secret', 'repositories': ['Acme/product']},
        {'token': 'ghs_read_secret', 'repositories': ['Acme/docs']}]}
    env = {}
    assert G.apply(env, Hub(grants), 'alpha')
    redactor = redact.for_turn(env)
    assert redactor.scrub_text('ghs_write_secret ghs_read_secret') == redact.MASK + ' ' + redact.MASK
    assert redact.for_turn({'GH_TOKEN': 'ghs_clone_secret'}).scrub_text('ghs_clone_secret') == redact.MASK


def test_app_token_failure_blocks_machine_helpers_and_askpass(tmp_path):
    from clients.tico import APIError
    marker = tmp_path / 'machine-used'
    machine = tmp_path / '.gitconfig'
    machine.write_text('[credential "https://github.com"]\nhelper = "!echo machine >> ' + str(marker) + '; echo username=machine; echo password=machine"\n')
    askpass = tmp_path / 'askpass'
    askpass.write_text('#!/bin/sh\necho askpass >> ' + str(marker) + '\necho machine\n')
    askpass.chmod(0o755)
    for hub in (Hub(error=APIError('github_token', 'App token failed', 409)),):
        env = {'PATH': '/usr/bin:/bin', 'HOME': str(tmp_path), 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_ASKPASS': str(askpass)}
        assert not G.apply(env, hub, 'alpha')
        result = subprocess.run(['git', 'credential', 'fill'], input='protocol=https\nhost=github.com\n\n',
                                env=env, cwd=tmp_path, capture_output=True, text=True, timeout=10)
        assert result.returncode != 0
        assert 'GitHub App token unavailable' in result.stderr
        assert not marker.exists()
    hub = Hub({'configured': False})
    assert not G.apply({}, hub, 'alpha')
    hub.error = APIError('github_token', 'App was connected since the previous turn', 409)
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(tmp_path), 'GIT_CONFIG_NOSYSTEM': '1'}
    assert not G.apply(env, hub, 'alpha')
    assert 'password=machine' not in fill(env, tmp_path)
    assert not marker.exists()
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(tmp_path), 'GIT_CONFIG_NOSYSTEM': '1'}
    assert not G.apply(env, Hub({'configured': False}), 'alpha')
    assert 'password=machine' in fill(env, tmp_path)
    assert marker.exists()


def test_app_token_wins_in_the_org_and_a_stored_gh_token_serves_only_other_owners(tmp_path):
    """A stale GH_TOKEN credential granted to the bot never shadows the App inside the connected organization."""
    import os
    import shutil
    from runner import redact
    gh = tmp_path / "gh"
    gh.write_text("#!/bin/sh\nprintf '%s' \"$GH_TOKEN\"\n")
    gh.chmod(0o755)
    env = {"PATH": str(tmp_path) + os.pathsep + os.environ["PATH"], "HOME": str(tmp_path), "GH_TOKEN": "ghp_stale"}
    grants = {"configured": True, "token": "write-token", "repository": "Acme/bot-alpha", "tokens": [
        {"token": "write-token", "repositories": ["Acme/bot-alpha", "Acme/product"]}]}
    assert G.apply(env, Hub(grants), "alpha")
    assert env["GH_TOKEN"] == env["GITHUB_TOKEN"] == "write-token"
    assert env[G.TURN_PATH_KEY] == env["PATH"]           # what a login shell puts back (docker/profile-turn.sh)
    run = lambda *args: subprocess.run([shutil.which("gh", path=env["PATH"]), *args], env=env, cwd=tmp_path,
                                       capture_output=True, text=True, timeout=10)
    assert run("pr", "view", "-R", "Acme/product").stdout == "write-token"
    assert run("pr", "view", "-R", "Other/tool").stdout == "ghp_stale"
    refused = run("pr", "view", "-R", "Acme/elsewhere")  # in the org but not granted: never the stored token
    assert refused.returncode == 1 and "ghp_stale" not in refused.stdout + refused.stderr
    assert redact.for_turn(env).scrub_text("ghp_stale write-token") == redact.MASK + " " + redact.MASK


@pytest.mark.slow
def test_mac_login_shells_keep_the_wrapper_and_refresh_after_the_starting_token_expires(tmp_path, monkeypatch):
    import os
    import shlex
    import shutil

    bash, zsh = shutil.which("bash"), shutil.which("zsh")
    if not bash or not zsh:
        pytest.skip("Bash and zsh are needed for the Mac login-shell regression")
    brew = tmp_path / "homebrew"
    brew.mkdir()
    gh = brew / "gh"
    gh.write_text("#!/bin/sh\nprintf '%s\\n' \"$GH_TOKEN\"\n")
    gh.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    zdotdir = tmp_path / "user zsh files"
    zdotdir.mkdir()
    reset_path = f"export PATH={shlex.quote(str(brew))}:$PATH\n"
    bash_env = home / "bash-env"
    bash_env.write_text(reset_path + "export STARTUP_ENV=kept\n")
    for name in (".zshenv", ".zprofile", ".zshrc", ".zlogin"):
        (zdotdir / name).write_text(reset_path + "export STARTUP_ENV=kept\n")
    logout = tmp_path / "logout"
    (zdotdir / ".zlogout").write_text(f"printf x >> {shlex.quote(str(logout))}\n")
    original_files = {p: p.read_bytes() for p in (bash_env, *zdotdir.iterdir())}
    hub = HubServer()
    try:
        config = tmp_path / "runner.json"
        config.write_text(json.dumps({"url": hub.url, "token": "synthetic-registration"}))
        monkeypatch.setattr(G.sys, "platform", "darwin")
        for shell, flags in ((bash, ["--noprofile", "-lc"]), (zsh, ["-d", "-lc"]), (zsh, ["-d", "-lic"])):
            env = {"PATH": str(brew) + os.pathsep + os.defpath, "HOME": str(home),
                   "BASH_ENV": str(bash_env), "ZDOTDIR": str(zdotdir)}
            grants = {"configured": True, "token": "expired-start-token", "tokens": [
                {"token": "expired-start-token", "repositories": ["Acme/product"]}]}
            assert G.apply(env, Hub(grants), "alpha", config)
            inner = "command -v gh; gh repo view -R Acme/product"
            command = inner + "; " + shlex.join([shell, *flags, inner]) + "; printf '%s\\n' \"$STARTUP_ENV\""
            before = hub.count
            result = subprocess.run([shell, *flags, command], env=env, cwd=home,
                                    capture_output=True, text=True, timeout=15)
            wrapper = str(G.ROOT / "runner" / "credential_bin" / "gh")
            assert result.returncode == 0, result.stderr
            assert result.stdout.splitlines() == [wrapper, f"ghs_fresh{before + 1}",
                                                   wrapper, f"ghs_fresh{before + 2}", "kept"]
            assert hub.count == before + 2
        assert logout.read_text()
        assert {p: p.read_bytes() for p in original_files} == original_files
    finally:
        hub.close()
