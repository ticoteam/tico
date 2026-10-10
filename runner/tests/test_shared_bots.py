"""Branches sync lessons without losing local commits or provider isolation."""
import json
import os
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

import pytest

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

    @pytest.mark.slow
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
    @pytest.mark.slow
    def test_assignment_instances_get_registration_bound_local_branches_and_never_repair_missing_work(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            origin, source = root / "origin.git", root / "source"
            git(root, "init", "--bare", "--initial-branch=main", str(origin))
            git(root, "clone", str(origin), str(source))
            git(source, "config", "user.name", "Test bot")
            git(source, "config", "user.email", "bot@acme.example")
            (source / "AGENT.md").write_text("rules\n")
            (source / "memory").mkdir()
            (source / "memory" / "learnings.md").write_text("Shared trunk lessons\n")
            git(source, "add", "AGENT.md", "memory/learnings.md")
            git(source, "commit", "-q", "-m", "rules")
            git(source, "push", "-q", "-u", "origin", "main")
            # Assignment learning follows the explicitly advertised role trunk;
            # do not infer `main` merely because this synthetic repo has it.
            git(source, "remote", "set-head", "origin", "main")
            config = {**COPY, "assignment_branch": True, "assignment_id": "assignment-id-1",
                      "assignment_task_id": "task-id-1", "assignment_key": "feature-1", "generation": 1,
                      "repo": source.as_uri()}
            bot = "backend-architect-work-abc123-g1"
            runner = Runner({"url": "https://runner.acme.example", "token": "m", "projects_dir": str(root / "projects"),
                            "repos": {"backend-architect": str(source)}},
                            root / "state", host_factory=lambda a, e: None, client=mock.Mock())
            # Synthetic fixture uses a local bare remote and no GitHub App installation.
            runner.client.post.return_value = {"configured": False}
            path = runner.local_path(bot, config)
            self.assertEqual(path, root / "projects" / "assignments" / bot)
            self.assertNotEqual(path, runner.local_path("backend-architect", {"shared": True}))
            self.assertFalse(service.is_shared(config))
            entry = {"bot": bot, "config": config, "repository": "", "generation": 1}
            self.assertEqual(runner.fetch_repository(bot, entry, path), "")
            self.assertEqual(git(path, "branch", "--show-current").stdout.strip(), "assignment/" + bot)
            marker = runner.assignment_marker(bot)
            self.assertTrue(marker.is_file())
            marker_data = json.loads(marker.read_text())
            source_common = git(source, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()
            assignment_common = git(path, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()
            self.assertEqual(marker_data["git_common_dir"], str(Path(assignment_common).resolve()))
            self.assertNotEqual(Path(source_common).resolve(), Path(assignment_common).resolve())
            learning_path = runner.assignment_learning_path(bot)
            self.assertEqual(learning_path, root / "projects" / "assignment-learning" / bot)
            self.assertEqual(git(learning_path, "branch", "--show-current").stdout.strip(), "assignment-learning/" + bot)
            self.assertEqual(git(learning_path, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip(), source_common)
            marker_data = json.loads(marker.read_text())
            self.assertEqual(marker_data["learning_trunk_ref"], "refs/remotes/origin/main")

            # Two assignment drafts stay in separate worktrees. Only the persistent role publishes
            # reviewed lessons to origin/main; simultaneous checkpoint refreshes serialize fetches,
            # retain both diverged drafts, and fast-forward a clean learning tree.
            second = "backend-architect-work-def456-g1"
            second_config = {**config, "assignment_id": "assignment-id-2", "assignment_task_id": "task-id-2",
                             "assignment_key": "feature-2"}
            second_entry = {"bot": second, "config": second_config, "repository": "", "generation": 1}
            second_path = runner.local_path(second, second_config)
            self.assertEqual(runner.fetch_repository(second, second_entry, second_path), "")
            second_learning = runner.assignment_learning_path(second)
            self.assertNotEqual(learning_path, second_learning)
            self.assertEqual(git(second_learning, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip(), source_common)

            third = "backend-architect-work-ghi789-g1"
            third_config = {**config, "assignment_id": "assignment-id-3", "assignment_task_id": "task-id-3",
                            "assignment_key": "feature-3"}
            third_entry = {"bot": third, "config": third_config, "repository": "", "generation": 1}
            third_path = runner.local_path(third, third_config)
            self.assertEqual(runner.fetch_repository(third, third_entry, third_path), "")
            third_learning = runner.assignment_learning_path(third)
            self.assertEqual(git(third_learning, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip(), source_common)

            (learning_path / "memory" / "learnings.md").write_text("Shared trunk lessons\nReviewed lesson A\n")
            git(learning_path, "add", "memory/learnings.md")
            git(learning_path, "commit", "-q", "-m", "reviewed lesson A")
            (second_learning / "memory" / "learnings.md").write_text("Shared trunk lessons\nReviewed lesson B\n")
            git(second_learning, "add", "memory/learnings.md")
            git(second_learning, "commit", "-q", "-m", "reviewed lesson B")

            # A human-reviewed handoff is added and published by the persistent source role, never
            # by either assignment branch. A second reviewed publication extends that same trunk.
            (source / "memory" / "learnings.md").write_text("Shared trunk lessons\nHuman-reviewed lesson A\n")
            git(source, "add", "memory/learnings.md")
            git(source, "commit", "-q", "-m", "publish reviewed lesson A")
            git(source, "push", "-q", "origin", "main")
            (source / "memory" / "learnings.md").write_text("Shared trunk lessons\nHuman-reviewed lesson A\nHuman-reviewed lesson B\n")
            git(source, "add", "memory/learnings.md")
            git(source, "commit", "-q", "-m", "publish reviewed lesson B")
            git(source, "push", "-q", "origin", "main")
            published = git(source, "rev-parse", "HEAD").stdout.strip()
            first_draft_head = git(learning_path, "rev-parse", "HEAD").stdout.strip()
            second_draft_head = git(second_learning, "rev-parse", "HEAD").stdout.strip()
            with ThreadPoolExecutor(max_workers=2) as pool:
                conflicts = list(pool.map(lambda pair: runner.assignment_learning_refresh(*pair),
                                          ((bot, config, 2), (second, second_config, 2))))
            self.assertTrue(all("diverged" in conflict for conflict in conflicts))
            self.assertEqual(git(learning_path, "rev-parse", "HEAD").stdout.strip(), first_draft_head)
            self.assertEqual(git(second_learning, "rev-parse", "HEAD").stdout.strip(), second_draft_head)
            self.assertEqual(runner.assignment_learning_refresh(third, third_config, 2), "")
            self.assertEqual(git(third_learning, "rev-parse", "HEAD").stdout.strip(), published)
            self.assertEqual(git(source, "rev-parse", "refs/remotes/origin/main").stdout.strip(), published)
            draft = second_learning / "memory" / "draft.md"
            draft.write_text("keep this uncommitted reviewed lesson draft\n")
            dirty_problem = runner.assignment_learning_refresh(second, second_config, 3)
            self.assertIn("draft changes", dirty_problem)
            self.assertEqual(draft.read_text(), "keep this uncommitted reviewed lesson draft\n")
            self.assertEqual(git(second_learning, "rev-parse", "HEAD").stdout.strip(), second_draft_head)

            # Cleanup is a separate explicit request. Dirty task work and learning drafts are retained;
            # an already-published, clean assignment can be removed without touching the source trunk.
            first_request = {"id": "assignment-id-1", "assignment_id": "assignment-id-1", "bot": bot,
                             "source_bot": "backend-architect", "task_id": "task-id-1", "generation": 1,
                             "revision": 4, "config": config}
            exclude = path / ".git" / "info" / "exclude"
            exclude_before = exclude.read_text() if exclude.exists() else ""
            exclude.write_text(exclude_before + "\nignored-task-work.md\n")
            ignored_draft = path / "ignored-task-work.md"
            ignored_draft.write_text("keep ignored local task work\n")
            result, detail = runner.cleanup_assignment_trees(first_request)
            self.assertEqual(result, "blocked")
            self.assertIn("untracked, or ignored", detail)
            self.assertEqual(ignored_draft.read_text(), "keep ignored local task work\n")
            ignored_draft.unlink()
            exclude.write_text(exclude_before)
            task_draft = path / "untracked-task-work.md"
            task_draft.write_text("keep this untracked task work\n")
            task_head_before = git(path, "rev-parse", "HEAD").stdout.strip()
            result, detail = runner.cleanup_assignment_trees(first_request)
            self.assertEqual(result, "blocked")
            self.assertIn("untracked, or ignored", detail)
            self.assertEqual(task_draft.read_text(), "keep this untracked task work\n")
            task_draft.unlink()
            committed_draft = path / "committed-task-work.md"
            committed_draft.write_text("keep this unpreserved local commit\n")
            git(path, "add", "committed-task-work.md")
            git(path, "commit", "-q", "-m", "unpreserved assignment commit")
            task_head_before = git(path, "rev-parse", "HEAD").stdout.strip()
            result, detail = runner.cleanup_assignment_trees(first_request)
            self.assertEqual(result, "blocked")
            self.assertIn("commits not preserved", detail)
            self.assertTrue(path.is_dir() and learning_path.is_dir() and marker.is_file())
            self.assertEqual(git(path, "rev-parse", "HEAD").stdout.strip(), task_head_before)

            second_request = {"id": "assignment-id-2", "assignment_id": "assignment-id-2", "bot": second,
                              "source_bot": "backend-architect", "task_id": "task-id-2", "generation": 1,
                              "revision": 4, "config": second_config}
            result, detail = runner.cleanup_assignment_trees(second_request)
            self.assertEqual(result, "blocked")
            self.assertIn("changed, untracked, or ignored", detail)
            self.assertTrue(second_learning.is_dir() and second_path.is_dir())

            third_request = {"id": "assignment-id-3", "assignment_id": "assignment-id-3", "bot": third,
                             "source_bot": "backend-architect", "task_id": "task-id-3", "generation": 1,
                             "revision": 4, "config": third_config}
            git(third_path, "remote", "set-url", "origin", (root / "different-origin.git").as_uri())
            result, detail = runner.cleanup_assignment_trees(third_request)
            self.assertEqual(result, "blocked")
            self.assertIn("origin is ambiguous or differs", detail)
            self.assertTrue(third_path.is_dir() and third_learning.is_dir())
            git(third_path, "remote", "set-url", "origin", source.as_uri())

            # Cleanup must not treat preservation refs as complete when origin has
            # alternate fetch destinations or a separate push destination.
            git(third_path, "config", "--add", "remote.origin.fetch",
                "refs/heads/main:refs/remotes/other/main")
            result, detail = runner.cleanup_assignment_trees(third_request)
            self.assertEqual(result, "blocked")
            self.assertIn("fetch refspec is ambiguous", detail)
            self.assertTrue(third_path.is_dir() and third_learning.is_dir())
            git(third_path, "config", "--unset-all", "remote.origin.fetch")
            git(third_path, "config", "--add", "remote.origin.fetch",
                "+refs/heads/*:refs/remotes/origin/*")

            git(third_path, "config", "--add", "remote.origin.pushurl", "")
            result, detail = runner.cleanup_assignment_trees(third_request)
            self.assertEqual(result, "blocked")
            self.assertIn("separate or ambiguous push URL", detail)
            self.assertTrue(third_path.is_dir() and third_learning.is_dir())
            git(third_path, "config", "--unset-all", "remote.origin.pushurl")

            git(third_path, "config", "--add", "remote.origin.pushurl", (root / "push-only.git").as_uri())
            result, detail = runner.cleanup_assignment_trees(third_request)
            self.assertEqual(result, "blocked")
            self.assertIn("separate or ambiguous push URL", detail)
            self.assertTrue(third_path.is_dir() and third_learning.is_dir())
            git(third_path, "config", "--unset-all", "remote.origin.pushurl")

            git(third_path, "remote", "set-url", "--add", "origin", (root / "other-origin.git").as_uri())
            result, detail = runner.cleanup_assignment_trees(third_request)
            self.assertEqual(result, "blocked")
            self.assertIn("origin is ambiguous or differs", detail)
            self.assertTrue(third_path.is_dir() and third_learning.is_dir())
            git(third_path, "remote", "set-url", "--delete", "origin", (root / "other-origin.git").as_uri())

            result, detail = runner.cleanup_assignment_trees(third_request)
            self.assertEqual(result, "complete", detail)
            self.assertFalse(third_path.exists())
            self.assertFalse(third_learning.exists())
            self.assertFalse(runner.assignment_marker(third).exists())
            self.assertTrue(source.is_dir() and (source / "AGENT.md").is_file())

            self.assertTrue(runner.client.post.call_args_list)
            self.assertTrue(all(call.args == ("github/token", {"bot": "backend-architect", "purpose": "git"})
                                for call in runner.client.post.call_args_list))

            self.assertEqual(runner.assignment_checkout_problem(bot, config, path), "")
            (path / "work-in-progress.txt").write_text("keep this untracked file\n")
            self.assertEqual(runner.assignment_checkout_problem(bot, config, path), "")
            (path / "progress.md").write_text("committed assignment progress\n")
            git(path, "add", "progress.md")
            git(path, "commit", "-q", "-m", "save assignment progress")
            committed_head = git(path, "rev-parse", "HEAD").stdout.strip()
            (path / "AGENT.md").unlink()
            problem = runner.fetch_repository(bot, entry, path)
            self.assertIn("Tracked files are missing", problem)
            self.assertFalse((path / "AGENT.md").exists())
            self.assertEqual(git(path, "rev-parse", "HEAD").stdout.strip(), committed_head)
            self.assertEqual((path / "work-in-progress.txt").read_text(), "keep this untracked file\n")
            changed = {**config, "assignment_id": "different-registration"}
            self.assertIn("different registration", runner.assignment_checkout_problem(bot, changed, path))


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
