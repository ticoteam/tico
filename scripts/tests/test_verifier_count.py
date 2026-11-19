"""Verification facts read committed trees, including stale overwrite regressions, without blocking."""

from collections import Counter
import json
import subprocess

import pytest

from scripts import check_migration_order
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
    assert result["facts"][0]["commit"] == base
    assert result["facts"][0]["path"] == "backend/holds.py"
    assert result["count"] == 1
    monkeypatch.setattr(verifier_count, "check", lambda base: result)
    assert verifier_count.main(["--base", base]) == 0


def test_v0616_edited_migration_is_refused_only_by_migration_guard(repo, monkeypatch):
    # 68ed7d3971 added a pin limit to a migration v0.6.15 had applied; v0.6.16 could not start.
    path = "backend/migrations/20261117023225_company_floor_widget.py"
    original = "def up(conn):\n    conn.execute('SELECT place FROM pin_places')\n"
    write(repo, path, original)
    base = commit(repo, monkeypatch, "Ship the Floor migration", days=30)
    git(repo, "tag", "v0.6.15")
    write(repo, path, "PIN_LIMIT = 60\n\n" + original.replace("pin_places'", "pin_places LIMIT 60'"))
    commit(repo, monkeypatch, "Edit the applied migration")
    assert "new migration" in check_migration_order.check(base, root=repo)[0]
    result = check(repo, base)
    assert result["facts"] == []
    monkeypatch.setattr(verifier_count, "check", lambda base: result)
    assert verifier_count.main(["--base", base]) == 0
    write(repo, path, original)
    write(repo, "backend/migrations/20261118000000_floor_limit.py", "def up(conn):\n    return None\n")
    commit(repo, monkeypatch, "Restore and add a new migration")
    assert check_migration_order.check(base, root=repo) == []


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
    write(repo, "scripts/check.sh", "exit 1\n")
    commit(repo, monkeypatch)
    write(repo, "backend/tests/test_holds.py", "")
    result = check(repo, base)
    assert kinds(result) == dict.fromkeys(
        ["deleted_test", "new_skip", "new_suppression", "raised_baseline", "verifier_edit"], 1
    )
    assert result["count"] == 5
    monkeypatch.setattr(verifier_count, "check", lambda base: result)
    output, summary = tmp_path / "facts.json", tmp_path / "summary.md"
    summary.write_text("Earlier summary\n")
    monkeypatch.setenv("VERIFIER_COUNT_JSON", str(output))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert verifier_count.main(["--base", base]) == 0
    assert json.loads(output.read_text()) == result
    assert summary.read_text().startswith("Earlier summary\n")
    assert "verifier count: 5" in summary.read_text()


@pytest.mark.parametrize("token", ["sha", "#4008"])
def test_revert_ok_names_one_commit_and_counts_it(repo, monkeypatch, token):
    base = stale_tree(repo, monkeypatch)
    token = base[:12] if token == "sha" else token
    commit(repo, monkeypatch, f"Revert intentionally\n\nRevert-Ok: {token} restore the previous behavior")
    result = check(repo, base)
    assert kinds(result) == {"named_revert": 1}
    assert result["count"] == 1


def test_wrong_revert_trailer_does_not_clear_a_finding(repo, monkeypatch):
    base = stale_tree(repo, monkeypatch)
    commit(repo, monkeypatch, "Revert\n\nRevert-Ok: #4009 restore the previous behavior")
    assert kinds(check(repo, base)) == {"stale_revert": 1}


def test_revert_ok_pr_names_the_merge_of_a_blame_commit(repo, monkeypatch):
    base = stale_tree(repo, monkeypatch)
    original = git(repo, "rev-parse", f"{base}^")
    tree = git(repo, "rev-parse", f"{base}^{{tree}}")
    # A normal merge names the PR; the branch commit that blame identifies has no PR number.
    branch = git(repo, "commit-tree", tree, "-p", original, "-m", "Close the claim item")
    tip = git(repo, "commit-tree", tree, "-p", branch, "-m", "Finish the branch")
    merge = git(repo, "commit-tree", tree, "-p", original, "-p", tip, "-m", "Merge pull request #4008 from acme/fix")
    git(repo, "update-ref", "refs/heads/main", merge)
    git(repo, "tag", "v1.0.0", original)
    git(repo, "tag", "v1.0.1", merge)
    commit(repo, monkeypatch, "Restore the earlier tree")
    for token in [merge[:7], branch[:12], tip, "#4008", original[:12]]:
        head = git(
            repo, "commit-tree", "HEAD^{tree}", "-p", merge, "-m", f"Revert\n\nRevert-Ok: {token} restore behavior"
        )
        result = check(repo, merge, head)
        if token == original[:12]:
            assert kinds(result) == {"stale_revert": 1}
        else:
            assert kinds(result) == {"named_revert": 1}
            assert result["count"] == 1


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


def test_baseline_edits_count_and_per_rule_growth_is_its_own_fact(repo, monkeypatch):
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
    assert [(f["kind"], f["path"]) for f in result["facts"]] == [
        ("raised_baseline", "baselines/ruff/backend/a.py.json")
    ]


def test_comments_spacing_old_lines_and_own_commits_are_not_stale_reverts(repo, monkeypatch):
    write(repo, "backend/holds.py", "old = 1\n")
    old = commit(repo, monkeypatch, days=30)
    write(repo, "backend/holds.py", "old = 1\n# A recent explanation\nvalue = 2\n")
    base = commit(repo, monkeypatch, days=2)
    write(repo, "backend/holds.py", "old=1\nvalue=2\n")
    commit(repo, monkeypatch)
    assert not check(repo, base)["facts"]
    write(repo, "backend/holds.py", "")
    commit(repo, monkeypatch)
    assert not check(repo, old)["facts"]


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
    assert not check(repo, base)["facts"]


def test_revert_ok_in_message_body_is_not_a_trailer(repo, monkeypatch):
    base = stale_tree(repo, monkeypatch)
    commit(repo, monkeypatch, f"Revert\n\nRevert-Ok: {base[:12]} restore behavior\n\nThis is body text, not a trailer.")
    assert kinds(check(repo, base)) == {"stale_revert": 1}


def test_removing_one_of_several_recent_routes_is_not_a_whole_revert(repo, monkeypatch):
    write(repo, "backend/routes.py", "\n".join(f"route_{i} = 'old/{i}'" for i in range(4)))
    commit(repo, monkeypatch, days=30)
    routes = [f"route_{i} = 'new/{i}'" for i in range(4)]
    write(repo, "backend/routes.py", "\n".join(routes))
    base = commit(repo, monkeypatch, days=2)
    write(repo, "backend/routes.py", "\n".join(routes[1:]))
    commit(repo, monkeypatch)
    assert not check(repo, base)["facts"]


def test_blame_following_an_older_path_does_not_crash(repo, monkeypatch):
    write(repo, "README.md", "Example\n")
    commit(repo, monkeypatch, days=30)
    write(repo, "backend/first.py", "def read_value():\n    return load_value()\n")
    commit(repo, monkeypatch, days=2)
    git(repo, "mv", "backend/first.py", "backend/second.py")
    base = commit(repo, monkeypatch, days=1)
    write(repo, "backend/second.py", "")
    commit(repo, monkeypatch)
    assert not check(repo, base)["facts"]


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
    assert not check(repo, base)["facts"]


def test_pr_4038_behind_main_reports_only_the_branch_changes(repo, monkeypatch):
    write(repo, "backend/holds.py", "LIMIT = 1\n")
    write(repo, "README.md", "Example\n")
    fork = commit(repo, monkeypatch, days=30)
    write(repo, "README.md", "Updated example\n")
    head = commit(repo, monkeypatch, "Update the example")
    write(repo, "README.md", "Example\n")
    write(repo, "backend/holds.py", "LIMIT = 10\n")
    write(repo, "backend/other.py", "ENABLED = True\n")
    write(repo, "backend/migrations/20261117000000_new.py", "def up(conn):\n    return None\n")
    git(repo, "add", "-A")
    tree = git(repo, "write-tree")
    base = git(repo, "commit-tree", tree, "-p", fork, "-m", "Main advances after the branch point")
    result = check(repo, base, head)
    assert result["base"] == fork
    assert result["facts"] == []


def test_migration_maintenance_and_restoring_shipped_bytes_are_allowed(repo, monkeypatch):
    path = "backend/migrations/20261117000000_floor.py"
    shipped = "def up(conn):\n    conn.execute('SELECT 1')\n"
    write(repo, path, shipped)
    write(repo, "backend/migrations/__init__.py", "LOADER = 1\n")
    write(repo, "backend/migrations/ddl_allowlist.txt", "3\n")
    write(repo, "backend/migrations/notes.txt", "Migration notes\n")
    commit(repo, monkeypatch, days=30)
    git(repo, "tag", "v0.6.14")
    write(repo, path, shipped.replace("SELECT 1", "SELECT 0"))
    commit(repo, monkeypatch, "Ship a different version", days=20)
    git(repo, "tag", "v0.6.15")
    write(repo, path, shipped.replace("SELECT 1", "SELECT 2"))
    base = commit(repo, monkeypatch, "Edit an applied migration", days=2)
    write(repo, path, shipped)
    write(repo, "backend/migrations/__init__.py", "LOADER = 2\n")
    write(repo, "backend/migrations/ddl_allowlist.txt", "2\n")
    (repo / "backend/migrations/notes.txt").unlink()
    commit(repo, monkeypatch, "Restore shipped bytes and maintain the loader")
    assert check(repo, base)["facts"] == []
    assert check_migration_order.check(base, root=repo) == []
    write(repo, path, shipped.replace("SELECT 1", "SELECT 3"))
    commit(repo, monkeypatch, "Different bytes are still frozen")
    assert "new migration" in check_migration_order.check(base, root=repo)[0]


def test_replacing_a_recent_addition_is_evolution_but_deleting_it_is_a_revert(repo, monkeypatch):
    write(repo, "backend/config.py", "ENABLED = True\n")
    commit(repo, monkeypatch, days=30)
    write(repo, "backend/config.py", "ENABLED = True\nLIMIT = 10\n")
    base = commit(repo, monkeypatch, days=2)
    write(repo, "backend/config.py", "ENABLED = True\nLIMIT = 20\n")
    commit(repo, monkeypatch)
    assert check(repo, base)["facts"] == []
    write(repo, "backend/config.py", "ENABLED = True\n")
    commit(repo, monkeypatch)
    assert kinds(check(repo, base)) == {"stale_revert": 1}


def test_replacing_recent_lines_must_restore_the_preimage(repo, monkeypatch):
    write(repo, "backend/config.py", "LIMIT = 1\n")
    commit(repo, monkeypatch, days=30)
    write(repo, "backend/config.py", "LIMIT = 10\n")
    base = commit(repo, monkeypatch, days=2)
    write(repo, "backend/config.py", "LIMIT = 20\n")
    commit(repo, monkeypatch)
    assert check(repo, base)["facts"] == []
    write(repo, "backend/config.py", "")
    commit(repo, monkeypatch)
    assert check(repo, base)["facts"] == []
    write(repo, "backend/config.py", "LIMIT = 1\n")
    commit(repo, monkeypatch)
    assert kinds(check(repo, base)) == {"stale_revert": 1}


def test_revert_trailers_require_a_target_and_reason_and_allow_several(repo, monkeypatch):
    base = stale_tree(repo, monkeypatch)
    commit(repo, monkeypatch, "Restore the earlier tree")
    invalid = [base[:12], f"{base[:6]} too short", "4008 bare number", "https://github.com/acme/tico/pull/4008 link"]
    for token in invalid:
        head = git(repo, "commit-tree", "HEAD^{tree}", "-p", base, "-m", f"Revert\n\nRevert-Ok: {token}")
        assert kinds(check(repo, base, head)) == {"stale_revert": 1}
    head = git(
        repo,
        "commit-tree",
        "HEAD^{tree}",
        "-p",
        base,
        "-m",
        f"Revert\n\nRevert-Ok: #4009 unrelated\nRevert-Ok: {base[:7]} restore behavior",
    )
    assert kinds(check(repo, base, head)) == {"named_revert": 1}


def test_verifier_scripts_and_configuration_all_count(repo, monkeypatch):
    write(repo, "README.md", "Example\n")
    base = commit(repo, monkeypatch, days=30)
    paths = [
        "scripts/gates/example.py",
        "scripts/ci/example.py",
        "scripts/check.sh",
        "scripts/check_migration_order.py",
        "scripts/lint.sh",
        "scripts/lint_baseline.py",
        "scripts/freeze_guard.py",
        "scripts/example_guard_check.py",
        "scripts/diff_facts.py",
        "scripts/pr_checks.py",
        "scripts/gates.sh",
        ".github/workflows/example.yml",
        "ruff.toml",
        "pytest.ini",
        "pyrightconfig.strict.json",
        "biome.json",
        "biome.jsonc",
        "scripts/rules/example.yml",
        "backend/tests/conftest.py",
    ]
    for path in paths:
        write(repo, path, "changed = 1\n")
    write(repo, "scripts/product.py", "changed = 1\n")
    commit(repo, monkeypatch)
    result = check(repo, base)
    assert kinds(result) == {"verifier_edit": len(paths)}
    assert {fact["path"] for fact in result["facts"]} == set(paths)


def test_deleted_rust_async_and_parameterized_tests_count_once_per_function(repo, monkeypatch):
    path = "runner-rs/src/client.rs"
    attributes = [
        "#[test]",
        "#[tokio::test]",
        '#[tokio::test(flavor = "multi_thread")]',
        '#[tokio::test(\n    flavor = "multi_thread",\n)]',
        "#[rstest]\n#[case(1)]\n#[case(2)]",
        "#[test_case(1)]\n#[test_case(2)]",
    ]
    write(repo, path, "\n".join(f"{attr}\nasync fn test_{i}() {{}}" for i, attr in enumerate(attributes)))
    base = commit(repo, monkeypatch, days=30)
    write(repo, path, "fn production() {}\n")
    commit(repo, monkeypatch)
    result = check(repo, base)
    assert kinds(result) == {"deleted_test": len(attributes)}
    assert {fact["reason"] for fact in result["facts"]} == {f"removed test test_{i}" for i in range(len(attributes))}
