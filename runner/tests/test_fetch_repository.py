"""A bot placed on a computer that has never held it gets its repository from GitHub, or a readiness problem
that names why not (never just "Missing bot repository"). No network: GitHub is a local bare repository."""
import subprocess
import tempfile
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

if __name__ == "__main__":
    unittest.main()
