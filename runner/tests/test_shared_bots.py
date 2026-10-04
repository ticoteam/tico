"""Branches sync lessons without losing local commits or provider isolation."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from runner import service
from runner.hosts import base
from runner.service import Runner
from runner.tests.test_hosts import ClaudeProcess, make_claude

ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "bot@acme.example", "GIT_COMMITTER_NAME": "t",
       "GIT_COMMITTER_EMAIL": "bot@acme.example", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}
COPY = {"shared_from": "backend-architect", "repo": "bot-backend-architect", "runtime": "claude"}


def git(path, *args):
    return subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True, env=ENV)


class TwoCopies(unittest.TestCase):
    """GitHub is a bare origin; Ana's and Sam's machines each have a clone of it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.origin, self.ana, self.sam = root / "origin.git", root / "ana", root / "sam"
        git(root, "init", "-q", "--bare", "-b", "main", str(self.origin))
        git(root, "clone", "-q", str(self.origin), str(self.ana))
        git(self.ana, "config", "user.name", "Test bot")
        git(self.ana, "config", "user.email", "bot@acme.example")
        git(self.ana, "checkout", "-q", "-b", "main")
        self.write(self.ana, "AGENT.md", "rules\n")
        git(self.ana, "push", "-q", "-u", "origin", "main")
        git(root, "clone", "-q", str(self.origin), str(self.sam))
        # Maintenance discards ambient author variables, just as on a fresh computer.
        git(self.sam, "config", "user.name", "Test bot")
        git(self.sam, "config", "user.email", "bot@acme.example")
        self.env_patch = mock.patch.dict(os.environ, {k: ENV[k] for k in ENV if k.startswith("GIT_")})
        self.env_patch.start()

    def tearDown(self):
        self.env_patch.stop()
        self.tmp.cleanup()

    def write(self, path, name, text):
        (path / name).parent.mkdir(parents=True, exist_ok=True)
        (path / name).write_text(text)
        git(path, "add", name)
        git(path, "commit", "-q", "-m", name)

    def test_a_turn_starts_from_what_the_other_copy_pushed_with_its_own_commits_on_top(self):
        self.write(self.ana, "memory/learnings.md", "ana's lesson\n")
        git(self.ana, "push", "-q")
        self.write(self.sam, "memory/decisions.md", "sam's unpushed lesson\n")
        self.assertEqual(service.sync_shared(self.sam), "")
        self.assertEqual((self.sam / "memory/learnings.md").read_text(), "ana's lesson\n")
        self.assertEqual((self.sam / "memory/decisions.md").read_text(), "sam's unpushed lesson\n")

    def test_a_conflict_or_leftovers_are_the_bots_to_fix_and_nothing_is_lost(self):
        self.write(self.ana, "memory/learnings.md", "ana's lesson\n")
        git(self.ana, "push", "-q")
        self.write(self.sam, "memory/learnings.md", "sam's lesson\n")
        problem = service.sync_shared(self.sam)
        self.assertIn("conflict", problem)
        self.assertEqual((self.sam / "memory/learnings.md").read_text(), "sam's lesson\n")
        self.assertFalse((self.sam / ".git" / "rebase-merge").exists())
        (self.sam / "scratch.md").write_text("half done")
        self.assertIn("uncommitted", service.sync_shared(self.sam))
        prompt = Runner.shared_lines("backend-architect-sam", COPY, problem)
        self.assertIn("Before anything else", prompt[-1])

    def test_a_push_another_copy_beat_is_rebased_and_pushed_again(self):
        self.write(self.ana, "memory/learnings.md", "ana's lesson\n")
        git(self.ana, "push", "-q")
        self.write(self.sam, "memory/decisions.md", "sam's lesson\n")
        runner = Runner({"url": "https://runner.acme.example", "token": "m", "projects_dir": self.tmp.name},
                        Path(self.tmp.name) / "state", host_factory=lambda a, e: None, client=mock.Mock())
        self.assertFalse(runner.push(self.sam))
        self.assertTrue(runner.push(self.sam, shared=True))
        self.assertEqual(git(self.sam, "rev-parse", "HEAD").stdout, git(self.origin, "rev-parse", "main").stdout)


class Copy(unittest.TestCase):
    def test_a_copy_works_in_the_shared_repositorys_checkout(self):
        runner = Runner({"url": "https://runner.acme.example", "token": "m", "projects_dir": "/w"}, tempfile.mkdtemp(),
                        host_factory=lambda a, e: None, client=mock.Mock())
        self.assertEqual(runner.local_path("backend-architect-sam", COPY), Path("/w/bot-backend-architect"))
        self.assertEqual(runner.local_path("backend-architect-sam"), Path("/w/bot-backend-architect"))
        self.assertEqual(runner.local_path("backend-architect", {"shared": True}), Path("/w/bot-backend-architect"))

    def test_a_copy_is_told_it_is_one_and_what_to_remember(self):
        lines = " ".join(Runner.shared_lines("backend-architect-sam", COPY))
        self.assertIn("a branch of `backend-architect`", lines)
        self.assertIn("never who asked", lines)
        self.assertNotIn("Before anything else", lines)

    def test_a_copy_keeps_its_own_repositorys_paths_in_its_reply(self):
        text = "See bot-backend-architect/memory/learnings.md and emp-legal/x.md"
        scrubbed = service.scrub_reply(text, "backend-architect-sam", own="bot-backend-architect")
        self.assertIn("bot-backend-architect/memory/learnings.md", scrubbed)
        self.assertNotIn("emp-legal/", scrubbed)


class CleanClaude(unittest.TestCase):
    def test_a_shared_bot_runs_without_the_operators_own_claude_setup(self):
        with tempfile.TemporaryDirectory() as repo:
            Path(repo, ".mcp.json").write_text(json.dumps({"mcpServers": {"docs": {"command": "docs-server"}}}))
            host = make_claude()
            settings = base.settings(repo, env={"HUB_EMPLOYEE": "cpo", "HUB_TOKEN": "t0k",
                                                "HUB_API_URL": "https://acme.example", "PATH": "/bin"}, shared=True)
            host.start_turn(host.start_thread("cpo", settings), "hello")
            proc = ClaudeProcess.instances[-1]
            argv = proc.argv
            self.assertEqual(argv[argv.index("--setting-sources") + 1], "project,local")
            self.assertIn("--strict-mcp-config", argv)
            servers = json.loads(argv[argv.index("--mcp-config") + 1])["mcpServers"]
            self.assertEqual(sorted(servers), ["docs", "hub"])
            self.assertEqual(proc.kwargs["env"]["CLAUDE_CODE_DISABLE_AUTO_MEMORY"], "1")
            host.stop()

    def test_any_other_bot_keeps_its_operators_setup(self):
        host = make_claude()
        settings = base.settings("/tmp/emp-cpo", env={"HUB_EMPLOYEE": "cpo", "PATH": "/bin"})
        host.start_turn(host.start_thread("cpo", settings), "hello")
        argv = ClaudeProcess.instances[-1].argv
        self.assertNotIn("--setting-sources", argv)
        self.assertNotIn("CLAUDE_CODE_DISABLE_AUTO_MEMORY", ClaudeProcess.instances[-1].kwargs["env"])
        host.stop()


class Sessions(unittest.TestCase):
    def test_botops_defaults_to_task_and_conversation_isolation(self):
        from runner.state import session_key
        first = {'bot': 'botops', 'task': {'id': 'mail-repair'}, 'conversation': {'id': 'room'}}
        other = {**first, 'task': {'id': 'kpi-repair'}}
        self.assertEqual(session_key({}, first), 'task:mail-repair')
        self.assertEqual(session_key({}, first), session_key({}, dict(first)))
        self.assertNotEqual(session_key({}, first), session_key({}, other))
        self.assertEqual(session_key({}, {'bot': 'botops', 'conversation': {'id': 'ana'}}), 'conversation:ana')
        self.assertNotEqual(session_key({}, {'bot': 'botops', 'conversation': {'id': 'ana'}}),
                            session_key({}, {'bot': 'botops', 'conversation': {'id': 'ben'}}))
        self.assertEqual(session_key({'session': 'bot'}, first), 'bot')

    def test_task_sessions_resume_the_same_task_and_chat_without_changing_existing_bots(self):
        from runner.state import session_key
        attempt = {'task': {'id': 'review-a'}, 'conversation': {'id': 'room-a'}}
        self.assertEqual(session_key({}, attempt), 'bot')
        self.assertEqual(session_key({'session': 'task'}, attempt), 'task:review-a')
        self.assertEqual(session_key({'session': 'task'}, {'conversation': {'id': 'room-a'}}), 'conversation:room-a')
