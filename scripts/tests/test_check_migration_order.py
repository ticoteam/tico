"""New migrations must run after every migration the current base branch can already have applied."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts import check_migration_order as guard


def paths(*names):
    return [f"backend/migrations/{name}" for name in names]


@pytest.mark.parametrize("number", [92, 96])
def test_an_addition_must_be_strictly_newer_than_the_current_base_tip(number):
    added = paths(f"{number:04d}_x.sql")
    collision = "; number also used by 0096_main.py" if number == 96 else ""
    assert guard.errors(added, added, paths("0096_main.py"), "main") == [
        f"Renumber {number:04d}_x.sql to 0097_x.sql: main already has 0096{collision}"]


def test_duplicate_additions_across_sql_and_python_get_distinct_free_numbers():
    added = paths("0097_a.sql", "0097_b.py", "0098_kept.sql")
    assert guard.errors(added, added, paths("0096_main.sql"), "main") == [
        "Renumber 0097_a.sql to 0099_a.sql: number also used by 0097_b.py",
        "Renumber 0097_b.py to 0100_b.py: number also used by 0097_a.sql"]


def test_an_addition_cannot_reuse_an_existing_heads_number():
    added = paths("0097_new.py")
    assert guard.errors(added, paths("0097_old.sql") + added, paths("0096_base.sql"), "main") == [
        "Renumber 0097_new.py to 0098_new.py: number also used by 0097_old.sql"]


def test_only_added_migrations_are_checked_and_gaps_and_other_files_are_allowed():
    base = paths("0001_old.sql", "0096_base.py")
    head = base + paths("0100_first.sql", "0102_second.py", "__init__.py", "renames.py", "0097_notes.txt")
    assert guard.errors(head[2:], head, base, "main") == []
    # Existing duplicates belong to the loader's check, not to an unrelated PR.
    assert guard.errors([], paths("0001_old.sql", "0001_duplicate.py"), base, "main") == []


@pytest.mark.slow
def test_git_diff_uses_the_merge_base_and_current_tip_and_checks_renamed_paths(tmp_path):
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path, text=True,
                                       env={**os.environ, "GIT_AUTHOR_NAME": "Acme", "GIT_AUTHOR_EMAIL": "dev@acme.example",
                                            "GIT_COMMITTER_NAME": "Acme", "GIT_COMMITTER_EMAIL": "dev@acme.example"})

    def commit():
        git("add", "-A")
        git("-c", "commit.gpgsign=false", "commit", "-qm", "fixture")

    directory = tmp_path / "backend/migrations"
    directory.mkdir(parents=True)
    (directory / "0001_old.sql").write_text("SELECT 1;\n")
    (directory / "0002_deleted.sql").write_text("SELECT 2;\n")
    (directory / "0091_renamed.sql").write_text("SELECT 91;\n")
    git("init", "-q", "-b", "main")
    commit()
    git("checkout", "-qb", "feature")
    (directory / "0001_old.sql").write_text("SELECT 3;\n")
    (directory / "0002_deleted.sql").unlink()
    (directory / "0091_renamed.sql").rename(directory / "0092_renamed.sql")
    commit()
    pr_head = git("rev-parse", "HEAD").strip()
    git("checkout", "-q", "main")
    (directory / "0096_main.py").write_text("def up(conn): pass\n")
    commit()
    # The PR's old base had only 0091. The current tip's 0096 is not in its head tree.
    assert guard.check("main", pr_head, root=tmp_path) == [
        "Renumber 0092_renamed.sql to 0097_renamed.sql: main already has 0096"]
    git("checkout", "-q", "feature")
    (directory / "0092_renamed.sql").rename(directory / "0097_renamed.sql")
    commit()
    assert guard.check("main", root=tmp_path) == []

    # Exercise the CLI's actual exit status from an isolated checkout, including a missing base ref.
    script = tmp_path / "scripts/check_migration_order.py"
    script.parent.mkdir()
    script.write_text(Path(guard.__file__).read_text())
    for head, base, status in [(pr_head, "main", 1), ("HEAD", "main", 0), ("HEAD", "missing-base", 2)]:
        result = subprocess.run([sys.executable, str(script), "--base", base, "--head", head],
                                cwd=tmp_path, capture_output=True, text=True)
        assert result.returncode == status, result.stdout + result.stderr
