"""Independent timestamped migrations can merge in either order; new legacy names and collisions cannot."""
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts import check_migration_order as guard

NOW = datetime(2026, 11, 4, 23, 59, 59, tzinfo=timezone.utc)


def paths(*names):
    return [f"backend/migrations/{name}" for name in names]


def errors(added, head, base):
    return guard.errors(added, head, base, "main", now=NOW)


def test_a_new_legacy_name_gets_an_exact_timestamped_rename():
    added = paths("0094_x.py")
    assert errors(added, added, paths("0096_main.py")) == [
        "backend/migrations/0094_x.py: new migrations use YYYYMMDDHHMMSS UTC timestamps. Rename with: "
        "git mv backend/migrations/0094_x.py backend/migrations/20261104235959_x.py"]


def test_timestamp_additions_can_sort_before_the_base_tips_newest():
    added = paths("20261103000000_first.sql", "20261104235958_second.py")
    base = paths("0001_old.sql", "0096_base.py", "20261105000000_newer.py")
    head = base + added + paths("__init__.py", "renames.py", "0097_notes.txt")
    assert errors(added + head[-3:], head, base) == []
    # Existing legacy names (and even existing duplicates) do not make an unrelated PR fail.
    assert errors([], paths("0001_old.sql", "0001_duplicate.py"), base) == []


def test_duplicate_additions_across_sql_and_python_get_distinct_free_timestamps():
    added = paths("20261104235958_a.sql", "20261104235958_b.py", "20261104235959_kept.sql")
    assert errors(added, added, paths("20261105000000_base.py")) == [
        "backend/migrations/20261104235958_a.sql: version also used by 20261104235958_b.py. Rename with: "
        "git mv backend/migrations/20261104235958_a.sql backend/migrations/20261105000001_a.sql",
        "backend/migrations/20261104235958_b.py: version also used by 20261104235958_a.sql. Rename with: "
        "git mv backend/migrations/20261104235958_b.py backend/migrations/20261105000002_b.py"]


def test_an_addition_cannot_reuse_a_heads_or_base_tips_timestamp():
    added = paths("20261104235958_new.py")
    for head, base in [(paths("20261104235958_old.sql") + added, []),
                       (added, paths("20261104235958_old.sql"))]:
        assert errors(added, head, base) == [
            "backend/migrations/20261104235958_new.py: version also used by 20261104235958_old.sql. Rename with: "
            "git mv backend/migrations/20261104235958_new.py backend/migrations/20261104235959_new.py"]
    # Independently adding the exact same filename on both branches also collides.
    assert "main already contains backend/migrations/20261104235958_new.py" in errors(added, added, added)[0]


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
    legacy_head = git("rev-parse", "HEAD").strip()
    (directory / "0092_renamed.sql").rename(directory / "20261104000000_renamed.sql")
    commit()
    colliding_head = git("rev-parse", "HEAD").strip()
    git("checkout", "-q", "main")
    (directory / "0096_main.py").write_text("def up(conn): pass\n")
    (directory / "20261104000000_main.py").write_text("def up(conn): pass\n")
    (directory / "20261106000000_newer.sql").write_text("SELECT 6;\n")
    commit()
    # Both a legacy rename and a timestamp collision fail against the advanced tip, absent from the PR tree.
    assert "git mv backend/migrations/0092_renamed.sql backend/migrations/" in guard.check(
        "main", legacy_head, root=tmp_path)[0]
    assert "version also used by 20261104000000_main.py" in guard.check("main", colliding_head, root=tmp_path)[0]
    git("checkout", "-q", "feature")
    (directory / "20261104000000_renamed.sql").rename(directory / "20261105000000_renamed.sql")
    commit()
    assert guard.check("main", root=tmp_path) == []
    git("-c", "commit.gpgsign=false", "merge", "--no-edit", "main")
    # Legacy files inherited by merging main are allowed; the timestamp still sorts before main's newest.
    assert guard.check("main", root=tmp_path) == []

    # Exercise actual CLI exit statuses from an isolated checkout, including a missing base ref.
    script = tmp_path / "scripts/check_migration_order.py"
    script.parent.mkdir()
    script.write_text(Path(guard.__file__).read_text())
    for head, base, status in [(legacy_head, "main", 1), (colliding_head, "main", 1),
                               ("HEAD", "main", 0), ("HEAD", "missing-base", 2)]:
        result = subprocess.run([sys.executable, str(script), "--base", base, "--head", head],
                                cwd=tmp_path, capture_output=True, text=True)
        assert result.returncode == status, result.stdout + result.stderr
