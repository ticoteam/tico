"""A granted secret never leaves the runner as text (runner/redact.py): not in events or the reply, not in the
log, not in a file the turn left in the repository, and a commit that holds one is not pushed."""
import base64
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from urllib.parse import quote

import pytest

from runner import redact
from runner.hosts.fake import FakeHost
from runner.service import Runner
from runner.tests.test_runner_resilience import FakeClient, attempt

SECRET = "jira-token/ABC def+123=="
TAIL = "AtlassianApiToken12345"
BASIC = "ana@acme.example:" + TAIL


class Scrub(unittest.TestCase):
    def test_a_secret_is_replaced_in_the_forms_it_turns_up_in(self):
        r = redact.Redactor([SECRET, BASIC])
        b64 = base64.b64encode(BASIC.encode()).decode()
        text = f"a {SECRET} b {quote(SECRET, safe='')} c Basic {b64} d {TAIL} e"
        out = r.scrub_text(text)
        for leak in (SECRET, quote(SECRET, safe=""), b64, TAIL):
            self.assertNotIn(leak, out)
        self.assertEqual(out.count(redact.MASK), 4)
        self.assertIn("Basic " + redact.MASK, out)

    def test_json_keeps_its_shape_and_short_or_common_values_are_ignored(self):
        r = redact.Redactor([SECRET, "short", "localhost", "aaaaaaaaaaaa", ""])
        payload = {"kind": "tool", "n": 3, "ok": True, "items": [{"text": f"x {SECRET}"}, None, "short"]}
        self.assertEqual(r.scrub_json(payload),
                         {"kind": "tool", "n": 3, "ok": True, "items": [{"text": "x " + redact.MASK}, None, "short"]})
        self.assertEqual(r.scrub_text("short localhost aaaaaaaaaaaa"), "short localhost aaaaaaaaaaaa")
        self.assertIsNone(redact.for_turn({"PATH": "/usr/bin", "NAME": "x"}, ["short", "localhost"]))

    def test_an_internal_error_fails_closed(self):
        r = redact.Redactor([SECRET])

        class Broken:
            def sub(self, *_):
                raise RuntimeError("boom")
        r.pattern = Broken()
        self.assertEqual(r.scrub_text("has " + SECRET), redact.ERROR)


class Turn(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root / "emp-coo"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main")
        (self.repo / "state.md").write_text("start\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "init")

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args):
        env = {"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e.x",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e.x", "HOME": self.tmp.name, "GIT_CONFIG_NOSYSTEM": "1"}
        return subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True, text=True, env=env).stdout

    def run_turn(self, act, reply):
        """One turn on a fake host; `act()` is what the bot does in its checkout. Returns (posts, pushes, published, runner)."""
        client, pushes, published = FakeClient(), [], []
        host = FakeHost(replies=[reply])
        real_start = host.start_turn

        def start_turn(*a, **k):
            act()
            return real_start(*a, **k)
        host.start_turn = start_turn
        runner = Runner({"url": "https://runner.example", "token": "m", "projects_dir": str(self.root), "capacity": 1},
                        self.root / "state", host_factory=lambda att, env: host, client=client,
                        push=lambda path, env=None: pushes.append(str(path)) or (0, ""))
        runner.environment = lambda att: {"JIRA_BASIC_AUTH": SECRET, "PATH": "/usr/bin:/bin"}
        runner.vault_values["att-1"] = [SECRET]
        from unittest import mock
        with mock.patch("runner.files_publish.after_turn", lambda r, a, root, pushed=False, skip=(): published.append((pushed, list(skip)))), \
                mock.patch.object(Runner, "publish", lambda *a, **k: None):
            runner.execute(attempt(bot="coo"))
        return client, pushes, published

    def test_nothing_with_the_secret_is_posted_written_or_pushed(self):
        def act():
            (self.repo / "state.md").write_text(f"token is {SECRET}\n")
            (self.repo / "reports").mkdir()
            (self.repo / "reports" / "r.md").write_text(f"see {SECRET}")
            (self.repo / "blob.bin").write_bytes(b"\0\1" + SECRET.encode())
        client, pushes, published = self.run_turn(act, f"Connected with {SECRET}")
        wire = json.dumps(client.posts)
        self.assertNotIn(SECRET, wire)
        self.assertIn(redact.MASK, client.completion()["text"])
        self.assertIn("left out of the commit: blob.bin (contains a secret)", client.completion()["text"])
        self.assertNotIn(SECRET, (self.repo / "state.md").read_text())
        self.assertNotIn(SECRET, (self.repo / "reports" / "r.md").read_text())
        self.assertEqual(published, [(True, ["blob.bin"])])
        self.assertEqual(len(pushes), 1)

    def test_a_file_left_out_of_the_commit_is_reported_once(self):
        def act():
            (self.repo / "blob.bin").write_bytes(b"\0\1" + SECRET.encode())
        first, _, _ = self.run_turn(act, "first")
        second, _, published = self.run_turn(lambda: None, "second")
        self.assertIn("left out of the commit: blob.bin (contains a secret)", first.completion()["text"])
        self.assertEqual(second.completion()["text"], "second")
        self.assertEqual(published, [(True, ["blob.bin"])], "still kept out of what is published")

    @pytest.mark.slow
    def test_a_commit_that_holds_the_secret_is_not_pushed(self):
        def act():
            (self.repo / "state.md").write_text(f"token is {SECRET}\n")
            self.git("commit", "-q", "-am", "oops")
        client, pushes, published = self.run_turn(act, "done")
        self.assertEqual(pushes, [])
        self.assertIn("not pushed", client.completion()["text"])
        self.assertNotIn(SECRET, json.dumps(client.posts))

    def test_the_log_never_carries_a_running_turns_secret(self):
        r = redact.Redactor([SECRET])
        r.register("a1")
        try:
            self.assertNotIn(SECRET, redact.scrub_log("failed: " + SECRET))
        finally:
            redact.release("a1")
        self.assertIn(SECRET, redact.scrub_log("failed: " + SECRET))


if __name__ == "__main__":
    unittest.main()


def test_scrubbing_never_runs_checkout_programs_or_writes_through_symlinks(tmp_path, monkeypatch):
    from unittest import mock
    from runner import isolation
    repo = tmp_path / 'repo'
    repo.mkdir()
    def git(*args):
        return subprocess.run(['git', '-C', str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()
    git('init', '-b', 'main')
    git('config', 'user.name', 'Sam')
    git('config', 'user.email', 'sam@example.com')
    (repo / 'file').write_text('initial')
    (repo / '.gitattributes').write_text('file diff=unsafe\n')
    git('add', '.')
    git('commit', '-m', 'Initial')
    before = git('rev-parse', 'HEAD')
    (repo / 'file').write_text('changed')
    git('commit', '-am', 'Changed')
    program = tmp_path / 'program'
    marker = tmp_path / 'ran'
    program.write_text('#!/bin/sh\ntouch "' + str(marker) + '"\n')
    program.chmod(0o755)
    git('config', 'core.fsmonitor', str(program))
    git('config', 'diff.unsafe.textconv', str(program))
    git('config', 'diff.external', str(program))
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'secret').write_text(SECRET)
    (repo / 'link').symlink_to(outside, target_is_directory=True)
    monkeypatch.setenv('EXAMPLE_VAULT_SECRET', SECRET)
    with mock.patch.object(isolation, 'run', wraps=isolation.run) as calls:
        r = redact.Redactor([SECRET])
        r.scrub_tree(repo, before)
        r.scrub_files([repo / 'link' / 'secret'], repo)
        assert all('EXAMPLE_VAULT_SECRET' not in call.kwargs['env'] for call in calls.call_args_list)
        assert all(call.kwargs['env']['GIT_CONFIG_GLOBAL'] == __import__('os').devnull for call in calls.call_args_list)
        assert all('--no-optional-locks' in call.args[0] and 'core.fsmonitor=false' in call.args[0] for call in calls.call_args_list)
    assert not marker.exists()
    assert (outside / 'secret').read_text() == SECRET


def test_atomic_scrub_never_follows_a_swapped_symlink(tmp_path, monkeypatch):
    import os
    path = tmp_path / 'report'
    path.write_text(SECRET)
    outside = tmp_path / 'outside'
    outside.write_text('keep outside')
    replace = os.replace
    def swap(source, target, **kwargs):
        path.unlink()
        path.symlink_to(outside)
        return replace(source, target, **kwargs)
    monkeypatch.setattr(redact.os, 'replace', swap)
    rewritten, excluded = redact.Redactor([SECRET]).scrub_files([path], tmp_path)
    assert rewritten == [path] and excluded == []
    assert not path.is_symlink()
    assert SECRET not in path.read_text()
    assert outside.read_text() == 'keep outside'


# ---------------------------------------------------------------- git's own reason, safe to show
APP_TOKEN = "ghs_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"


@pytest.mark.parametrize("stderr,wanted", [
    ("Cloning into 'web'...\nremote: Repository not found.\n"
     f"fatal: repository 'https://x-access-token:{APP_TOKEN}@github.com/acme/web.git/' not found\n",
     ["remote: Repository not found.", "fatal: repository 'https://" + redact.MASK + "@github.com/acme/web.git/' not found"]),
    ("Cloning into 'web'...\nerror: unable to create file src/" + "a" * 40 + ".tsx: Filename too long\n"
     "fatal: unable to checkout working tree\nwarning: Clone succeeded, but checkout failed.\n"
     "You can inspect what was checked out with 'git status'\nand retry with the restore command\n",
     ["Filename too long", "fatal: unable to checkout working tree"]),
    ("error: RPC failed; HTTP 500 curl 22 The requested URL returned error: 500\nfatal: expected flush after ref listing\n",
     ["error: RPC failed; HTTP 500", "fatal: expected flush after ref listing"]),
    ("Downloading public/hero.mp4 (12 MB)\nError downloading object: public/hero.mp4 (abc123): Smudge error: "
     "batch response: This repository is over its data quota\nerror: external filter 'git-lfs filter-process' failed\n"
     "fatal: public/hero.mp4: smudge filter lfs failed\n", ["over its data quota", "smudge filter lfs failed"]),
    ("hint: Using 'master' as the name for the initial branch.\nfatal: could not read Username for "
     "'https://github.com': terminal prompts disabled\n", ["fatal: could not read Username"]),
])
def test_git_failure_keeps_gits_own_reason(stderr, wanted):
    text = redact.git_failure(stderr)
    for part in wanted:
        assert part in text
    assert "hint:" not in text and "Cloning into" not in text and "inspect what was checked out" not in text
    assert APP_TOKEN not in text and len(text) <= 300


def test_git_failure_masks_every_credential_shape_and_is_bounded():
    pat = "github_pat_11ABCDEFG0123456789_abcdefghijklmnopqrstuvwxyz0123456789"
    granted = "granted-secret-value-123"
    stderr = "\n".join([
        f"fatal: unable to access 'https://user:{granted}@example.com/x.git/': The requested URL returned error: 403",
        f"error: {APP_TOKEN} and ghp_{'z' * 36} and {pat} were refused",
        "error: > Authorization: Basic eC1hY2Nlc3MtdG9rZW46c2VjcmV0c2VjcmV0",
        "error: helper said password=hunter2hunter2 and token=abc123def456",
    ])
    text = redact.git_failure(stderr, secrets=[granted], limit=1000)
    for secret in (granted, APP_TOKEN, pat, "ghp_" + "z" * 36, "eC1hY2Nlc3MtdG9rZW46c2VjcmV0c2VjcmV0",
                   "hunter2hunter2", "abc123def456"):
        assert secret not in text
    first = redact.git_failure(stderr.splitlines()[0], secrets=[granted])
    assert granted not in first and "returned error: 403" in first
    long = redact.git_failure("fatal: " + "x" * 5000, limit=120)
    assert len(long) <= 120 and long.endswith("…")
    assert redact.git_failure("") == ""


def test_git_failure_masks_a_running_turns_secret():
    redactor = redact.Redactor(["turn-secret-abcdef"])
    redactor.register("attempt-git-failure")
    try:
        assert "turn-secret-abcdef" not in redact.git_failure("fatal: turn-secret-abcdef rejected")
    finally:
        redact.release("attempt-git-failure")
