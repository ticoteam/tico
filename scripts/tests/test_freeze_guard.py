"""The freeze guard finds net reverts of shipped work and merged pull requests whose final head nobody approved."""
import subprocess

import pytest

from scripts import freeze_guard

pytestmark = pytest.mark.slow   # real git

FEATURE = [f"def resume_step_{i}(tree):\n    return tree.resume({i})\n" for i in range(4)]


@pytest.fixture
def repo(tmp_path, monkeypatch):
    for key, value in {"HOME": str(tmp_path), "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
                       "GIT_AUTHOR_NAME": "Acme Dev", "GIT_AUTHOR_EMAIL": "dev@acme.example",
                       "GIT_COMMITTER_NAME": "Acme Dev", "GIT_COMMITTER_EMAIL": "dev@acme.example"}.items():
        monkeypatch.setenv(key, value)
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    write(path, "app/worktrees.py", "import os\n")
    write(path, "app/tests/test_worktrees.py", "".join(f"def test_step_{i}():\n    assert step({i})\n" for i in range(6)))
    write(path, "CHANGELOG.md", "# Changelog\n")
    commit(path, "Start")
    return path


def git(path, *args):
    return subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True).stdout.strip()


def write(path, name, text):
    (path / name).parent.mkdir(parents=True, exist_ok=True)
    (path / name).write_text(text)


def commit(path, message):
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", message)
    return git(path, "rev-parse", "HEAD")


def merge_pr(path, branch, number):
    git(path, "checkout", "-q", "main")
    git(path, "merge", "-q", "--no-ff", branch, "-m", f"Merge pull request #{number} from acme/{branch}")
    return git(path, "rev-parse", "HEAD")


def pr(path, number, changes):
    branch = f"pr-{number}"
    git(path, "checkout", "-q", "-b", branch, "main")
    for name, text in changes.items():
        write(path, name, text)
    head = commit(path, f"Change {number}")
    merge_pr(path, branch, number)
    return head


def kinds(result):
    return sorted((f["kind"], f["pr"], f.get("reverts_pr")) for f in result["findings"])


def test_a_change_that_undoes_a_shipped_pr_is_named_and_so_is_its_reapply(repo):
    shipped = "import os\n" + "".join(FEATURE)
    pr(repo, 1, {"app/worktrees.py": shipped})
    git(repo, "tag", "v1.0.0")
    # A stale tree: an unrelated change committed over a checkout from before PR 1.
    pr(repo, 2, {"app/worktrees.py": "import os\n", "app/updates.py": "def slides():\n    return 5\n"})
    # Cutting tests is not a revert.
    pr(repo, 3, {"app/tests/test_worktrees.py": "\n"})
    receipts = [{"pr": n, "head": git(repo, "rev-parse", f"main~{3 - n}^2"), "source": f"ticket {n}"} for n in (2, 3)]

    result = freeze_guard.check(str(repo), "main", "v1.0.0", receipts, fetch=False)
    assert kinds(result) == [("NET REVERT", 2, 1)]
    assert "PR #2" in result["findings"][0]["text"] and "PR #1" in result["findings"][0]["text"]
    assert "shipped in v1.0.0" in result["findings"][0]["text"]

    # Putting the reverted work back undoes what the next release shipped: the re-apply is named too.
    git(repo, "tag", "v1.1.0")
    head = pr(repo, 4, {"app/worktrees.py": shipped})
    result = freeze_guard.check(str(repo), "main", None, [{"pr": 4, "head": head}], fetch=False)   # newest tag
    assert kinds(result) == [("NET REVERT", 4, 2)]


class FakeGitHub:
    def __init__(self, heads, approvals):
        self.heads, self.approved = heads, approvals

    def head(self, number):
        return self.heads[number]

    def approvals(self, number):
        return self.approved.get(number, [])


def test_each_merged_pr_needs_an_approval_of_its_final_head(repo):
    git(repo, "tag", "v1.0.0")
    # PR 10: reviewed, then changed after the review and merged at the unreviewed head.
    git(repo, "checkout", "-q", "-b", "pr-10", "main")
    write(repo, "app/a.py", "A = 1\n")
    reviewed_10 = commit(repo, "A")
    write(repo, "app/a.py", "A = 2\n")
    commit(repo, "A after review")
    merge_pr(repo, "pr-10", 10)
    # PR 11: reviewed, then only brought up to date with main: the same change, so the approval stands.
    git(repo, "checkout", "-q", "-b", "pr-11", "main~1")
    write(repo, "app/b.py", "B = 1\n")
    reviewed_11 = commit(repo, "B")
    git(repo, "merge", "-q", "--no-ff", "main", "-m", "Merge main")
    merge_pr(repo, "pr-11", 11)
    # PR 12: the receipt names the final head.
    head_12 = pr(repo, 12, {"app/c.py": "C = 1\n"})
    # PRs 13 and 14 are squash merges: their heads and reviews are on GitHub.
    write(repo, "app/d.py", "D = 1\n")
    commit(repo, "D (#13)")
    write(repo, "app/e.py", "E = 1\n")
    commit(repo, "E (#14)")
    # A direct push needs a receipt; release notes don't.
    write(repo, "app/f.py", "F = 1\n")
    direct = commit(repo, "Hotfix straight on main")
    write(repo, "CHANGELOG.md", "# Changelog\n## 1.1.0\n")
    commit(repo, "Release 1.1.0")

    receipts = [{"pr": 10, "head": reviewed_10, "source": "ticket 10"}, {"pr": 11, "head": reviewed_11[:8]},
                {"pr": 12, "head": head_12[:7]}]
    github = FakeGitHub({13: "d" * 40, 14: "e" * 40}, {13: ["d" * 40], 14: ["f" * 40]})
    result = freeze_guard.check(str(repo), "main", "v1.0.0", receipts, github, fetch=False)
    assert kinds(result) == [("NO PR", None, None), ("UNREVIEWED", 10, None), ("UNREVIEWED", 14, None)]
    texts = {f["pr"]: f["text"] for f in result["findings"]}
    assert git(repo, "rev-parse", "pr-10")[:8] in texts[10] and reviewed_10[:8] in texts[10]
    assert direct[:8] in texts[None]

    result = freeze_guard.check(str(repo), "main", "v1.0.0", receipts + [{"commit": direct}], github, fetch=False)
    assert kinds(result) == [("UNREVIEWED", 10, None), ("UNREVIEWED", 14, None)]
