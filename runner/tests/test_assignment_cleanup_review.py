"""Independent remote-preservation check before removing an assignment clone."""
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from runner.service import Runner
from runner.tests.test_shared_bots import COPY, git


@pytest.mark.parametrize("stash_tree", ["product", pytest.param("learning", marks=pytest.mark.slow)])
def test_cleanup_preserves_stashed_assignment_work(stash_tree):
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        origin, source = root / "origin.git", root / "source"
        git(root, "init", "--bare", "--initial-branch=main", str(origin))
        git(root, "clone", str(origin), str(source))
        git(source, "config", "user.name", "Synthetic reviewer")
        git(source, "config", "user.email", "review@example.invalid")
        (source / "AGENT.md").write_text("Synthetic instructions\n")
        git(source, "add", "AGENT.md")
        git(source, "commit", "-m", "synthetic base")
        git(source, "push", "-u", "origin", "main")
        git(source, "remote", "set-head", "origin", "main")
        config = {**COPY, "assignment_branch": True, "assignment_id": "stash-assignment",
                  "assignment_task_id": "stash-task", "assignment_key": "stash", "generation": 1,
                  "repo": source.as_uri()}
        bot = "backend-architect-work-stash-g1"
        client = mock.Mock()
        client.post.return_value = {"configured": False}
        runner = Runner({"url": "https://runner.example.invalid", "token": "synthetic",
                         "projects_dir": str(root / "projects"),
                         "repos": {"backend-architect": str(source)}},
                        root / "state", host_factory=lambda a, e: None, client=client)
        product = runner.local_path(bot, config)
        assert runner.fetch_repository(bot, {"bot": bot, "config": config, "repository": "",
                                            "generation": 1}, product) == ""
        learning = runner.assignment_learning_path(bot)
        tree = product if stash_tree == "product" else learning
        (tree / "AGENT.md").write_text("Stashed work must survive\n")
        git(tree, "stash", "push", "-m", "synthetic unreviewed work")
        stash = git(tree, "rev-parse", "refs/stash").stdout.strip()
        request = {"id": "stash-assignment", "assignment_id": "stash-assignment", "bot": bot,
                   "source_bot": "backend-architect", "task_id": "stash-task", "generation": 1,
                   "revision": 4, "config": config}
        result, detail = runner.cleanup_assignment_trees(request)
        assert result == "blocked", (result, detail)
        assert product.is_dir() and learning.is_dir() and runner.assignment_marker(bot).is_file()
        assert git(tree, "rev-parse", "refs/stash").stdout.strip() == stash
        assert "Stashed work must survive" in git(tree, "show", "refs/stash:AGENT.md").stdout


@pytest.mark.slow
def test_deleted_remote_branch_is_not_proof_of_preserved_assignment_commits():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        origin, source = root / "origin.git", root / "source"
        git(root, "init", "--bare", "--initial-branch=main", str(origin))
        git(root, "clone", str(origin), str(source))
        git(source, "config", "user.name", "Synthetic reviewer")
        git(source, "config", "user.email", "review@example.invalid")
        (source / "AGENT.md").write_text("Synthetic instructions\n")
        git(source, "add", "AGENT.md")
        git(source, "commit", "-m", "synthetic base")
        git(source, "push", "-u", "origin", "main")
        git(source, "remote", "set-head", "origin", "main")
        config = {**COPY, "assignment_branch": True, "assignment_id": "review-assignment",
                  "assignment_task_id": "review-task", "assignment_key": "review", "generation": 1,
                  "repo": source.as_uri()}
        bot = "backend-architect-work-review-g1"
        client = mock.Mock()
        client.post.return_value = {"configured": False}
        runner = Runner({"url": "https://runner.example.invalid", "token": "synthetic",
                         "projects_dir": str(root / "projects"),
                         "repos": {"backend-architect": str(source)}},
                        root / "state", host_factory=lambda a, e: None, client=client)
        path = runner.local_path(bot, config)
        assert runner.fetch_repository(bot, {"bot": bot, "config": config, "repository": "",
                                            "generation": 1}, path) == ""
        (path / "only-assignment.md").write_text("Commit must remain accessible\n")
        git(path, "add", "only-assignment.md")
        git(path, "commit", "-m", "unmerged assignment work")
        head = git(path, "rev-parse", "HEAD").stdout.strip()
        git(path, "push", "origin", "HEAD:refs/heads/temporary-review")
        git(path, "fetch", "origin")
        git(source, "branch", "-D", "temporary-review")
        # Git fetch without prune leaves this stale tracking reference in the assignment clone.
        assert git(path, "rev-parse", "refs/remotes/origin/temporary-review").stdout.strip() == head
        request = {"id": "review-assignment", "assignment_id": "review-assignment", "bot": bot,
                   "source_bot": "backend-architect", "task_id": "review-task", "generation": 1,
                   "revision": 4, "config": config}
        result, detail = runner.cleanup_assignment_trees(request)
        assert result == "blocked" and path.is_dir(), (result, detail, "assignment clone was removed")
        assert git(path, "rev-parse", "HEAD").stdout.strip() == head
