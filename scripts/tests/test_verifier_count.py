"""Verification counts and hard refusals read committed trees, including stale overwrite regressions."""

from collections import Counter
import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts import freeze_guard
from scripts.gates import verifier_count

pytestmark = pytest.mark.slow  # real git
NOW = 1_800_000_000


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def write(repo, path, text):
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)


def commit(repo, monkeypatch, message="Change", days=0):
    timestamp = f"{NOW - days * 86400} +0000"
    monkeypatch.setenv("GIT_AUTHOR_DATE", timestamp)
    monkeypatch.setenv("GIT_COMMITTER_DATE", timestamp)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", message)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    for key, value in {
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Acme Dev",
        "GIT_AUTHOR_EMAIL": "dev@acme.example",
        "GIT_COMMITTER_NAME": "Acme Dev",
        "GIT_COMMITTER_EMAIL": "dev@acme.example",
    }.items():
        monkeypatch.setenv(key, value)
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    return path


def check(repo, base, head="HEAD"):
    return verifier_count.check(base, head, repo, now=NOW)


def kinds(result):
    return Counter(f["kind"] for f in result["facts"])


def hard(result):
    return [f for f in result["facts"] if f["hard"]]


def stale_tree(repo, monkeypatch):
    # PR #4008 (7db1e81ae3) introduced the start-failures migration and rewrote the holds readers.
    # An old copy written on top of newer work loses that work even though git can merge the commit.
    older = "def holds_for_run(run):\n    return load(run)\n"
    write(repo, "backend/holds.py", older)
    commit(repo, monkeypatch, days=30)
    write(
        repo,
        "backend/holds.py",
        older + "\ndef close_claim(run):\n    claim = read_claim(run)\n    close_item(claim)\n",
    )
    newer = commit(repo, monkeypatch, "Close the claim item (#4008)", days=2)
    write(repo, "backend/holds.py", older)
    write(repo, "backend/unrelated.py", "ENABLED = True\n")
    return newer


def test_pr_4008_stale_tree_overwrites_newer_work(repo, monkeypatch):
    base = stale_tree(repo, monkeypatch)
    commit(repo, monkeypatch, "Write an older tree over the newer work")
    result = check(repo, base)
    assert kinds(result) == {"stale_revert": 1}
    assert hard(result)[0]["commit"] == base
    assert hard(result)[0]["path"] == "backend/holds.py"
    assert result["count"] == 0
    monkeypatch.setattr(verifier_count, "ROOT", repo)
    monkeypatch.setattr(verifier_count.diff_facts, "ROOT", repo)
    monkeypatch.setattr(verifier_count, "check", lambda base: result)
    assert verifier_count.main(["--base", base]) == 1


def test_v0616_edited_migration_and_deletion_fail_but_new_files_pass(repo, monkeypatch):
    # 68ed7d3971 added a pin limit to a migration v0.6.15 had applied; v0.6.16 could not start.
    path = "backend/migrations/20261117023225_company_floor_widget.py"
    original = "def up(conn):\n    conn.execute('SELECT place FROM pin_places')\n"
    write(repo, path, original)
    base = commit(repo, monkeypatch, "Ship the Floor migration", days=30)
    git(repo, "tag", "v0.6.15")
    write(repo, path, "PIN_LIMIT = 60\n\n" + original.replace("pin_places'", "pin_places LIMIT 60'"))
    commit(repo, monkeypatch, "Edit the applied migration")
    git(repo, "tag", "v0.6.16")
    result = check(repo, base)
    assert kinds(result) == {"edited_migration": 1}
    assert "new migration" in hard(result)[0]["reason"]
    (repo / path).unlink()
    commit(repo, monkeypatch, "Delete the applied migration")
    assert kinds(check(repo, base)) == {"edited_migration": 1}
    write(repo, path, original)
    write(repo, "backend/migrations/20261118000000_floor_limit.py", "def up(conn):\n    return None\n")
    commit(repo, monkeypatch, "Restore and add a new migration")
    assert hard(check(repo, base)) == []


def test_each_counted_kind_once_and_working_tree_is_ignored(repo, monkeypatch, tmp_path):
    write(
        repo,
        "backend/tests/test_holds.py",
        "def test_removed():\n    assert True\n\ndef test_kept():\n    assert True\n",
    )
    write(repo, "baselines/ruff/backend/holds.py.json", '{"backend/holds.py": {"F401": 1}}\n')
    write(repo, "scripts/check.sh", "exit 0\n")
    base = commit(repo, monkeypatch, days=30)
    write(
        repo, "backend/tests/test_holds.py", "@pytest.mark.skip(reason='waiting')\ndef test_kept():\n    assert True\n"
    )
    write(repo, "backend/holds.py", "import os  # noqa: F401\n")
    write(repo, "baselines/ruff/backend/holds.py.json", '{"backend/holds.py": {"F401": 2}}\n')
    write(repo, "ci/quarantine/holds.json", '{"test": "backend/tests/test_holds.py::test_kept"}\n')
    write(repo, "scripts/check.sh", "exit 1\n")
    commit(repo, monkeypatch)
    write(repo, "backend/tests/test_holds.py", "")
    result = check(repo, base)
    assert kinds(result) == dict.fromkeys(
        ["deleted_test", "new_skip", "quarantine_entry", "new_suppression", "raised_baseline", "verifier_edit"], 1
    )
    assert result["count"] == 6 and not hard(result)
    monkeypatch.setattr(verifier_count, "check", lambda base: result)
    output, summary = tmp_path / "facts.json", tmp_path / "summary.md"
    summary.write_text("Earlier summary\n")
    monkeypatch.setenv("VERIFIER_COUNT_JSON", str(output))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert verifier_count.main(["--base", base]) == 0
    assert json.loads(output.read_text()) == result
    assert summary.read_text().startswith("Earlier summary\n")
    assert "verifier count: 6" in summary.read_text()


@pytest.mark.parametrize("token", ["sha", "#4008", "https://github.com/ticoteam/tico/pull/4008"])
def test_revert_ok_names_one_commit_and_counts_it(repo, monkeypatch, token):
    base = stale_tree(repo, monkeypatch)
    token = base[:12] if token == "sha" else token
    commit(repo, monkeypatch, f"Revert intentionally\n\nRevert-Ok: {token}")
    result = check(repo, base)
    assert kinds(result) == {"allowed_revert": 1}
    assert result["count"] == 1 and not hard(result)


def test_wrong_revert_trailer_does_not_clear_a_finding(repo, monkeypatch):
    base = stale_tree(repo, monkeypatch)
    commit(repo, monkeypatch, "Revert\n\nRevert-Ok: #4009")
    assert kinds(check(repo, base)) == {"stale_revert": 1}


def test_revert_ok_pr_names_the_merge_of_a_blame_commit(repo, monkeypatch):
    base = stale_tree(repo, monkeypatch)
    original = git(repo, "rev-parse", f"{base}^")
    tree = git(repo, "rev-parse", f"{base}^{{tree}}")
    # A normal merge names the PR; the branch commit that blame identifies has no PR number.
    branch = git(repo, "commit-tree", tree, "-p", original, "-m", "Close the claim item")
    merge = git(repo, "commit-tree", tree, "-p", original, "-p", branch, "-m", "Merge pull request #4008 from acme/fix")
    git(repo, "update-ref", "refs/heads/main", merge)
    commit(repo, monkeypatch, "Revert intentionally\n\nRevert-Ok: #4008")
    result = check(repo, merge)
    assert kinds(result) == {"allowed_revert": 1}
    assert result["count"] == 1 and not hard(result)


def test_one_whole_hunk_of_a_larger_recent_commit_is_a_stale_revert(repo, monkeypatch):
    write(repo, "backend/holds.py", "value = 1\n")
    commit(repo, monkeypatch, days=30)
    write(repo, "backend/holds.py", "value = 2\n")
    write(repo, "backend/other.py", "\n".join(f"step_{i} = {i}" for i in range(20)))
    base = commit(repo, monkeypatch, days=2)
    write(repo, "backend/holds.py", "value = 1\n")
    commit(repo, monkeypatch)
    assert kinds(check(repo, base)) == {"stale_revert": 1}


def test_losing_most_recent_additions_is_a_stale_revert(repo, monkeypatch):
    write(repo, "backend/holds.py", "existing = 1\n")
    commit(repo, monkeypatch, days=30)
    recent = [f"recent_{i} = {i}\n" for i in range(7)]
    write(repo, "backend/holds.py", "existing = 1\n" + "".join(recent))
    base = commit(repo, monkeypatch, days=2)
    write(repo, "backend/holds.py", "existing = 1\n" + recent[-1])
    commit(repo, monkeypatch)
    assert kinds(check(repo, base)) == {"stale_revert": 1}


@pytest.mark.parametrize(
    "path,before,after",
    [
        (
            "backend/tests/test_holds.py",
            "def test_keep():\n    assert True\n\ndef test_lost():\n    assert False\n",
            "def test_keep():\n    assert True\n",
        ),
        ("runner/src/lib.rs", "#[test]\nfn lost() { assert!(false); }\n", "fn lost() {}\n"),
        (
            "ui/tests/holds.test.ts",
            "test('lost', () => expect(false));\nit('keep', () => {});\n",
            "it('keep', () => {});\n",
        ),
    ],
)
def test_removed_tests_in_each_language(repo, monkeypatch, path, before, after):
    write(repo, path, before)
    base = commit(repo, monkeypatch, days=30)
    write(repo, path, after)
    commit(repo, monkeypatch)
    assert kinds(check(repo, base)) == {"deleted_test": 1}


def test_deleted_test_file_counts_once_and_changed_body_is_not_a_deletion(repo, monkeypatch):
    path = "backend/tests/test_holds.py"
    write(repo, path, "def test_one():\n    assert True\n\ndef test_two():\n    assert True\n")
    base = commit(repo, monkeypatch, days=30)
    write(repo, path, "def test_one():\n    assert 1\n\ndef test_two():\n    assert 2\n")
    commit(repo, monkeypatch)
    assert not check(repo, base)["facts"]
    (repo / path).unlink()
    commit(repo, monkeypatch)
    assert kinds(check(repo, base)) == {"deleted_test": 1}


def test_all_skip_and_suppression_spellings(repo, monkeypatch):
    write(repo, "README.md", "Example\n")
    base = commit(repo, monkeypatch, days=30)
    skips = [
        "@pytest.mark.skip",
        "@pytest.mark.skipif(True)",
        "@pytest.mark.xfail",
        "pytest.skip('reason')",
        "@unittest.skip('reason')",
        "@unittest.skipIf(True, 'reason')",
        "#[ignore]",
        "test.skip('name', () => {})",
        "it.skip('name', () => {})",
        "test.only('name', () => {})",
        "it.only('name', () => {})",
    ]
    suppressions = [
        "value = 1 # noqa",
        "value = 2 # type: ignore",
        "value = 3 # pyright: ignore",
        "#[allow(dead_code)]",
        "// eslint-disable",
        "// @ts-ignore",
        "// @ts-expect-error",
        "// biome-ignore lint/rule: reason",
    ]
    write(repo, "examples.txt", "\n".join(skips + suppressions) + "\n")
    commit(repo, monkeypatch)
    assert kinds(check(repo, base)) == {"new_skip": len(skips), "new_suppression": len(suppressions)}


def test_baseline_shrink_and_range_moves_are_free_but_per_rule_growth_counts(repo, monkeypatch):
    before = {
        "baselines/ruff/backend/a.py.json": {"backend/a.py": {"F401": 3, "F821": 1}},
        "baselines/pyright/backend/b.py.json": {
            "files": {"./backend/b.py": [{"code": "type", "range": {"lineCount": 1}}]}
        },
        "ui/baselines/strict/app/a.ts.json": {"count": 4},
        "ruff-baseline-counts.json": {"backend/c.py": {"F401": 3}},
        ".basedpyright/baseline.json": {"files": {"./backend/c.py": [{"code": "type"}, {"code": "type"}]}},
    }
    for path, data in before.items():
        write(repo, path, json.dumps(data))
    base = commit(repo, monkeypatch, days=30)
    for path, data in {
        "baselines/ruff/backend/a.py.json": {"backend/a.py": {"F401": 1, "F821": 2}},
        "baselines/pyright/backend/b.py.json": {
            "files": {"./backend/b.py": [{"code": "type", "range": {"lineCount": 8}}]}
        },
        "ui/baselines/strict/app/a.ts.json": {"count": 3},
        "ruff-baseline-counts.json": {"backend/c.py": {"F401": 2}},
        ".basedpyright/baseline.json": {"files": {"./backend/c.py": [{"code": "type"}]}},
    }.items():
        write(repo, path, json.dumps(data))
    commit(repo, monkeypatch)
    result = check(repo, base)
    assert kinds(result) == {"raised_baseline": 1}
    assert result["facts"][0]["path"] == "baselines/ruff/backend/a.py.json"


def test_comments_spacing_old_lines_and_own_commits_are_not_stale_reverts(repo, monkeypatch):
    write(repo, "backend/holds.py", "old = 1\n")
    old = commit(repo, monkeypatch, days=30)
    write(repo, "backend/holds.py", "old = 1\n# A recent explanation\nvalue = 2\n")
    base = commit(repo, monkeypatch, days=2)
    write(repo, "backend/holds.py", "old=1\nvalue=2\n")
    commit(repo, monkeypatch)
    assert not hard(check(repo, base))
    write(repo, "backend/holds.py", "")
    commit(repo, monkeypatch)
    assert not hard(check(repo, old))


def test_pyproject_only_counts_verifier_sections(repo, monkeypatch):
    write(repo, "pyproject.toml", '[project]\nname = "acme"\n[tool.ruff]\nline-length = 120\n')
    base = commit(repo, monkeypatch, days=30)
    write(repo, "pyproject.toml", '[project]\nname = "acme-tools"\n[tool.ruff]\nline-length = 120\n')
    commit(repo, monkeypatch)
    assert not check(repo, base)["facts"]
    write(repo, "pyproject.toml", '[project]\nname = "acme-tools"\n[tool.ruff]\nline-length = 140\n')
    commit(repo, monkeypatch)
    assert kinds(check(repo, base)) == {"verifier_edit": 1}


def test_refactor_of_a_larger_block_is_not_a_stale_tree(repo, monkeypatch):
    write(repo, "backend/holds.py", "def old_reader():\n    return load_old()\n")
    commit(repo, monkeypatch, days=30)
    write(repo, "backend/holds.py", "def old_reader():\n    require_access()\n    return load_old()\n")
    base = commit(repo, monkeypatch, days=2)
    write(repo, "backend/holds.py", "def new_reader():\n    return load_with_access()\n")
    commit(repo, monkeypatch)
    assert not hard(check(repo, base))


def test_revert_ok_in_message_body_is_not_a_trailer(repo, monkeypatch):
    base = stale_tree(repo, monkeypatch)
    commit(repo, monkeypatch, f"Revert\n\nRevert-Ok: {base[:12]}\n\nThis is body text, not a trailer.")
    assert kinds(check(repo, base)) == {"stale_revert": 1}


def test_removing_one_of_several_recent_routes_is_not_a_whole_revert(repo, monkeypatch):
    write(repo, "backend/routes.py", "\n".join(f"route_{i} = 'old/{i}'" for i in range(4)))
    commit(repo, monkeypatch, days=30)
    routes = [f"route_{i} = 'new/{i}'" for i in range(4)]
    write(repo, "backend/routes.py", "\n".join(routes))
    base = commit(repo, monkeypatch, days=2)
    write(repo, "backend/routes.py", "\n".join(routes[1:]))
    commit(repo, monkeypatch)
    assert not hard(check(repo, base))


def test_blame_following_an_older_path_does_not_crash(repo, monkeypatch):
    write(repo, "README.md", "Example\n")
    commit(repo, monkeypatch, days=30)
    write(repo, "backend/first.py", "def read_value():\n    return load_value()\n")
    commit(repo, monkeypatch, days=2)
    git(repo, "mv", "backend/first.py", "backend/second.py")
    base = commit(repo, monkeypatch, days=1)
    write(repo, "backend/second.py", "")
    commit(repo, monkeypatch)
    assert not hard(check(repo, base))


def test_feature_removal_with_a_small_inverse_does_not_revert_the_whole_file(repo, monkeypatch):
    original = "FIRST = 1\nSECOND = 1\n\nexisting_a = 1\nexisting_b = 1\n"
    write(repo, "backend/holds.py", original)
    commit(repo, monkeypatch, days=30)
    feature = "".join(f"feature_{i} = {i}\n" for i in range(7))
    write(
        repo,
        "backend/holds.py",
        original.replace("FIRST = 1", "FIRST = 2").replace("SECOND = 1", "SECOND = 2") + feature,
    )
    base = commit(repo, monkeypatch, days=2)
    write(
        repo,
        "backend/holds.py",
        original.replace("SECOND = 1", "SECOND = 2")
        .replace("existing_a = 1", "existing_a = 2")
        .replace("existing_b = 1", "existing_b = 2"),
    )
    commit(repo, monkeypatch)
    assert not hard(check(repo, base))


def test_queue_revert_only_is_offline_and_checks_only_merged_change_and_last_release(repo, monkeypatch):
    write(repo, "backend/holds.py", "value = 1\n")
    commit(repo, monkeypatch, days=30)
    write(repo, "backend/older.py", "legacy_value = 1\n")
    commit(repo, monkeypatch, days=30)
    git(repo, "tag", "v1.0.0")
    write(repo, "backend/holds.py", "value = 2\n")
    shipped = commit(repo, monkeypatch, "Fix holds (#4008)", days=2)
    git(repo, "tag", "v1.0.1")
    # An unrelated unreviewed change after the release is outside the queue's merged diff.
    write(repo, "backend/other.py", "value = 3\n")
    base = commit(repo, monkeypatch)
    write(repo, "backend/holds.py", "value = 1\n")
    head = commit(repo, monkeypatch)
    script = Path(freeze_guard.__file__)
    args = [sys.executable, str(script), "--repo", str(repo), "--revert-only", "--base", base, "--head", head, "--json"]
    monkeypatch.setenv("PATH", str(Path(subprocess.check_output(["which", "git"], text=True).strip()).parent))
    done = subprocess.run(args, capture_output=True, text=True)
    assert done.returncode == 1, done.stderr
    result = json.loads(done.stdout)
    assert [(f["kind"], f["reverts"]) for f in result["findings"]] == [("NET REVERT", shipped)]
    # Permitting the named revert never needs a GitHub review lookup.
    write(repo, "backend/other.py", "value = 4\n")
    allowed = commit(repo, monkeypatch, f"Intentional revert\n\nRevert-Ok: {shipped[:12]}")
    result = freeze_guard.check_revert_only(str(repo), base, allowed)
    assert result["findings"] == []
    # The preceding release is outside the bounded queue window.
    git(repo, "tag", "v1.0.2")
    (repo / "backend/older.py").unlink()
    newest = commit(repo, monkeypatch)
    assert freeze_guard.check_revert_only(str(repo), allowed, newest)["findings"] == []
