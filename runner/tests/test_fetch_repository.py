"""A bot placed on a computer that has never held it gets its repository from GitHub, or a readiness problem
that names why not (never just "Missing bot repository"). No network: GitHub is a local bare repository."""
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from clients.tico import APIError
from runner import git_credentials, service
from runner.service import Runner

REPO = "Acme/emp-helper"
RUNTIMES = {"codex": {"installed": True, "authenticated": "ready", "detail": "", "models": [],
                      "version": "codex 1.0", "controls": []}}
GIT = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "bot@acme.example", "GIT_COMMITTER_NAME": "t",
       "GIT_COMMITTER_EMAIL": "bot@acme.example", "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "HOME": "/tmp"}


def git(*args):
    subprocess.run(["git", *args], check=True, capture_output=True, env=GIT)


class Cloud:
    """The server's token route: a token, or the refusal it gives for a repository that is not on GitHub."""

    def __init__(self, refusal=None):
        self.refusal, self.asked = refusal, 0

    def post(self, path, body=None, **_):
        assert path == "github/token"
        self.asked += 1
        if self.refusal:
            raise self.refusal
        return {"configured": True, "token": "ghs_fake", "repository": REPO}


def entry(repository=REPO, runner_id="r1", generation=2):
    return {"bot": "helper", "runner_id": runner_id, "state": "active", "generation": generation,
            "repository": repository, "config": {"runtime": "codex", "model": "gpt-6-sol"}}


class FetchRepository(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.projects = self.root / "workspace"
        self.projects.mkdir()
        seed = self.root / "seed"
        seed.mkdir()
        git("-C", str(seed), "init", "-q", "-b", "main")
        (seed / "AGENT.md").write_text("# Helper\n")
        git("-C", str(seed), "add", "-A")
        git("-C", str(seed), "commit", "-q", "-m", "first")
        self.remote = self.root / "remote.git"
        git("clone", "-q", "--bare", str(seed), str(self.remote))
        real = git_credentials.clone_repository
        patcher = mock.patch.object(git_credentials, "clone_repository",
                                    lambda path, repository, env=None, **kw: real(path, repository, env, url=str(self.remote)))
        patcher.start()
        self.addCleanup(patcher.stop)

    def runner(self, cloud):
        runner = Runner.__new__(Runner)
        runner.config = {"url": "https://acme.test", "token": "t", "runner_id": "r1", "projects_dir": str(self.projects)}
        runner.client, runner._names = cloud, None
        return runner

    def test_a_bot_assigned_here_with_no_checkout_gets_its_repository(self):
        cloud = Cloud()
        rows = {r["bot"]: r for r in self.runner(cloud).preflight([entry()], RUNTIMES)}
        self.assertEqual(rows["helper"]["problems"], [])
        self.assertTrue(rows["helper"]["repository_present"] and rows["helper"]["ready"])
        self.assertTrue((self.projects / "bot-helper" / "AGENT.md").is_file())
        self.assertEqual(cloud.asked, 1)
        self.assertIs(rows["helper"]["published"], True)          # the clone has its upstream: a move can follow

    def test_a_repository_that_is_not_on_github_is_named_not_called_missing(self):
        cloud = Cloud(APIError("github_repo_missing", f"The repository {REPO} does not exist yet on GitHub. "
                               "Create it with BotOps: `hub bot repo-create helper --empty`.", 409))
        runner = self.runner(cloud)
        first = runner.preflight([entry()], RUNTIMES)[0]
        self.assertFalse(first["ready"])
        self.assertIn(REPO, first["problems"][0])
        self.assertIn("does not exist yet on GitHub", first["problems"][0])
        self.assertNotEqual(first["problems"], ["Missing bot repository or AGENT.md"])
        # Every heartbeat is not another GitHub call: the answer stands until the retry time or a new assignment.
        runner.preflight([entry()], RUNTIMES)
        self.assertEqual(cloud.asked, 1)
        runner.preflight([entry(generation=3)], RUNTIMES)
        self.assertEqual(cloud.asked, 2)

    def test_an_older_server_without_token_purpose_keeps_the_no_app_path(self):
        cloud = Cloud()
        runner, assigned = self.runner(cloud), entry()
        with mock.patch.object(cloud, "post", side_effect=[
                APIError("validation", "Unexpected field: purpose", 422),
                {"configured": False}, {"configured": False}]) as older:
            self.assertIsNotNone(runner.github_access("helper", assigned)[0])
            self.assertIsNotNone(runner.github_access("helper", assigned)[0])
            self.assertEqual([call.args[1] for call in older.call_args_list],
                             [{"bot": "helper", "purpose": "git"}, {"bot": "helper"}, {"bot": "helper"}])

    def test_a_template_bot_with_history_on_github_is_cloned_not_set_up_afresh(self):
        real = git_credentials.remote_history
        with mock.patch.object(git_credentials, "remote_history",
                               lambda repository, env=None, **kw: real(repository, env, url=str(self.remote))), \
                mock.patch.object(Runner, "bootstrap_template", lambda *a: "release-notes"), \
                mock.patch("clients.catalog.materialize") as materialize:
            row = self.runner(Cloud()).preflight([entry()], RUNTIMES)[0]
        materialize.assert_not_called()
        self.assertTrue(row["ready"])
        self.assertEqual((self.projects / "bot-helper" / "AGENT.md").read_text(), "# Helper\n")

    def test_only_a_copy_with_unrelated_history_holds_turns_not_one_that_diverged(self):
        real = git_credentials.publish_history
        path = self.projects / "bot-helper"

        def local_copy(base):
            # A checkout with no upstream: `base` is a clone of GitHub's history (diverged) or None (a template copy).
            if base:
                git("clone", "-q", str(self.remote), str(path))
                git("-C", str(path), "remote", "remove", "origin")
            else:
                git("init", "-q", "-b", "main", str(path))
                (path / "AGENT.md").write_text("# Helper from the template\n")
            (path / "notes.md").write_text("unpushed work\n")
            git("-C", str(path), "add", "-A")
            git("-C", str(path), "commit", "-q", "-m", "local")

        for base, ready in ((self.remote, True), (None, False)):
            with self.subTest(diverged=bool(base)):
                local_copy(base)
                if base:     # GitHub moved on too, so the copy cannot fast-forward it
                    other = self.root / "other"
                    git("clone", "-q", str(self.remote), str(other))
                    (other / "x.md").write_text("x\n")
                    git("-C", str(other), "add", "-A")
                    git("-C", str(other), "commit", "-q", "-m", "remote")
                    git("-C", str(other), "push", "-q", "origin", "main")
                runner = self.runner(Cloud())
                runner.push_lock, runner.publish_notes = threading.Lock(), {}
                with mock.patch.object(git_credentials, "publish_history",
                                       lambda p, repository, env=None, **kw: real(p, repository, {**(env or {}), **GIT},
                                                                                   url=str(self.remote))):
                    row = runner.preflight([entry()], RUNTIMES)[0]
                self.assertEqual(row["ready"], ready, row["problems"])
                self.assertEqual(any("shares no history" in p for p in row["problems"]), not ready)
                self.assertEqual((path / "notes.md").read_text(), "unpushed work\n")     # never touched
                if not ready:
                    # A turn checks for itself, without waiting for the publish pass: a fresh runner refuses it.
                    fresh = self.runner(Cloud())
                    fresh.push_lock, fresh.publish_notes = threading.Lock(), {}
                    with mock.patch.object(git_credentials, "publish_history",
                                           lambda p, repository, env=None, **kw: real(p, repository, {**(env or {}), **GIT},
                                                                                       url=str(self.remote))):
                        with self.assertRaisesRegex(RuntimeError, "shares no history"):
                            fresh.prepare_history("helper", 2, path, {git_credentials.REPOSITORY_KEY: REPO})
                shutil.rmtree(path)

    def test_a_computer_only_fetches_what_is_assigned_to_it(self):
        cloud = Cloud()
        row = self.runner(cloud).preflight([entry(runner_id="another-mac")], RUNTIMES)[0]
        self.assertEqual(row["problems"], ["Missing bot repository or AGENT.md"])
        self.assertEqual(cloud.asked, 0)
        self.assertFalse((self.projects / "bot-helper").exists())

    def test_personal_github_clone_uses_safe_git_with_computer_login(self):
        target = self.projects / "bot-original"
        config = {"repo": "Acme/bot-original", "repo_url": "https://github.com/Acme/old-repo"}
        with mock.patch.object(service.isolation, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
            self.assertEqual(service.clone_shared(target, config), "")
        command = run.call_args.args[0]
        self.assertEqual(command[-4:], ["clone", "--quiet", "https://github.com/Acme/bot-original", str(target)])
        self.assertIn("--no-optional-locks", command)
        self.assertIn("core.fsmonitor=false", command)
        self.assertTrue(any(arg.startswith('core.hooksPath=') for arg in command))
        self.assertEqual(run.call_args.kwargs["env"]["GIT_TERMINAL_PROMPT"], "0")

    def test_personal_clone_never_overwrites_an_existing_folder(self):
        target = self.projects / "bot-original"
        target.mkdir()
        (target / "draft.md").write_text("unfinished")
        with mock.patch.object(service.isolation, "run") as run:
            self.assertIn("left as it is", service.clone_shared(target, {"repo": "Acme/bot-original"}))
        run.assert_not_called()
        self.assertEqual((target / "draft.md").read_text(), "unfinished")

    def test_a_failed_clone_names_gits_reason_and_leaves_nothing_behind(self):
        target = self.projects / "bot-helper"
        def fails(command, **kwargs):
            if "clone" not in command:
                return subprocess.CompletedProcess(command, 0, "", "")
            (target / ".git" / "objects").mkdir(parents=True)
            return subprocess.CompletedProcess(command, 128, "", "Cloning into 'bot-helper'...\nerror: RPC failed; HTTP 500 "
                                               "curl 22 The requested URL returned error: 500\nfatal: expected flush after ref listing\n")
        with mock.patch.object(git_credentials.isolation, "run", side_effect=fails):
            state, detail = git_credentials.clone_repository(target, REPO, {"GH_TOKEN": "ghs_" + "x" * 36})
        self.assertEqual(state, "failed")
        self.assertIn("RPC failed; HTTP 500", detail)
        self.assertIn("expected flush after ref listing", detail)
        self.assertFalse(target.exists())                      # the next try is a clone, not "already exists"

    def test_a_timed_out_clone_says_so_and_leaves_nothing_behind(self):
        target = self.projects / "bot-helper"
        def slow(command, **kwargs):
            if "clone" not in command:
                return subprocess.CompletedProcess(command, 0, "", "")
            (target / ".git").mkdir(parents=True)
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        with mock.patch.object(git_credentials.isolation, "run", side_effect=slow):
            self.assertEqual(git_credentials.clone_repository(target, REPO, {}, timeout=180),
                             ("failed", "git clone timed out after 180 seconds; a large repository or a slow network needs longer"))
        self.assertFalse(target.exists())
        with mock.patch.object(service.isolation, "run", side_effect=slow):
            self.assertIn("timed out after 120 seconds", service.clone_shared(target, {"repo": "Acme/bot-original"}))
        self.assertFalse(target.exists())

    def test_a_refused_token_keeps_gits_words(self):
        target = self.projects / "bot-helper"
        refused = subprocess.CompletedProcess([], 128, "", "remote: Permission to Acme/emp-helper.git denied to app.\n"
                                              "fatal: unable to access 'https://x-access-token:ghs_secretsecretsecretsecret@github.com/"
                                              "Acme/emp-helper.git/': The requested URL returned error: 403\n")
        with mock.patch.object(git_credentials.isolation, "run", return_value=refused):
            state, detail = git_credentials.clone_repository(target, REPO, {})
        self.assertTrue(detail.startswith("GitHub refused the bot's token: remote: Permission to Acme/emp-helper.git denied"))
        self.assertNotIn("ghs_secretsecret", detail)

if __name__ == "__main__":
    unittest.main()
