"""Diff tiers cannot hide SQL, executable changes or recently deleted work."""

import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from scripts import diff_facts

pytestmark = pytest.mark.slow  # real git
NOW = 1_800_000_000


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def write(repo, path, text):
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)


def commit(repo, monkeypatch, days=0):
    timestamp = f"{NOW - days * 86400} +0000"
    monkeypatch.setenv("GIT_AUTHOR_DATE", timestamp)
    monkeypatch.setenv("GIT_COMMITTER_DATE", timestamp)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "Change")
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    for key, value in {
        "HOME": str(tmp_path),
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


def change(repo, monkeypatch, path, before, after):
    write(repo, path, before)
    base = commit(repo, monkeypatch, days=30)
    write(repo, path, after)
    head = commit(repo, monkeypatch)
    return diff_facts.diff_facts(base, head, repo)


def test_copy_and_comments_are_low_and_line_numbers_are_exact(repo, monkeypatch):
    facts = change(
        repo,
        monkeypatch,
        "app.py",
        'MESSAGE = "Hello"\n# Explain\nCOUNT = 1\n',
        'MESSAGE = "Welcome"\n# Clarify\nCOUNT = 1\n',
    )
    assert facts["tier"] == "low"
    file = facts["files"][0]
    assert file["path"] == "app.py" and file["kind"] == "code"
    assert file["added"] == file["deleted"] == [1, 2]
    assert file["hunks"][0]["strings_only"] and file["hunks"][0]["copy_only"]
    assert not file["hunks"][0]["sql"]


def test_comment_next_to_unchanged_sql_is_copy(repo, monkeypatch):
    facts = change(
        repo,
        monkeypatch,
        "app.py",
        'db.execute("SELECT 1")  # Before\n',
        'db.execute("SELECT 1")  # After\n',
    )
    assert facts["tier"] == "low"
    assert facts["files"][0]["hunks"][0]["copy_only"]
    assert not facts["files"][0]["hunks"][0]["sql"]


@pytest.mark.parametrize(
    "before,after",
    [
        ('db.execute("pragma user_version = 1")\n', 'db.execute("pragma user_version = 2")\n'),
        ('db.executemany("pragma user_version = 1", rows)\n', 'db.executemany("pragma user_version = 2", rows)\n'),
        (
            'SCHEMA_SQL: str = (\n    "pragma user_version = 1"\n)\n',
            'SCHEMA_SQL: str = (\n    "pragma user_version = 2"\n)\n',
        ),
        ('query = "REPLACE INTO entries VALUES (1)"\n', 'query = "REPLACE INTO entries VALUES (2)"\n'),
        ('label = "é"; query = "SELECT 1"\n', 'label = "é"; query = "SELECT 2"\n'),
    ],
)
def test_sql_is_normal_even_when_only_literal_text_changes(repo, monkeypatch, before, after):
    facts = change(repo, monkeypatch, "app.py", before, after)
    assert facts["tier"] == "normal"
    hunk = facts["files"][0]["hunks"][0]
    assert hunk["strings_only"] and hunk["sql"] and not hunk["copy_only"]


def test_a_multiline_string_insertion_is_copy_but_sql_deletion_is_not(repo, monkeypatch):
    write(repo, "app.py", 'MESSAGE = """Hello\nworld\n"""\n\ndb.executescript("""\nSELECT 1;\nSELECT 2;\n""")\n')
    base = commit(repo, monkeypatch, days=30)
    write(repo, "app.py", 'MESSAGE = """Hello\nfriendly\nworld\n"""\n\ndb.executescript("""\nSELECT 1;\n""")\n')
    commit(repo, monkeypatch)
    facts = diff_facts.diff_facts(base, repo=repo)
    assert facts["tier"] == "normal"
    copy, sql = facts["files"][0]["hunks"]
    assert copy["added"] == [2] and copy["copy_only"]
    assert sql["deleted"] == [7] and sql["sql"] and not sql["copy_only"]


@pytest.mark.parametrize(
    "path,kind",
    [
        ("backend/migrations/20260101000000_example.sql", "migration"),
        (".github/workflows/check.yml", "workflow"),
        ("scripts/rules/example.yml", "rules"),
        ("scripts/gates/example.py", "gates"),
        ("scripts/check.sh", "gates"),
    ],
)
def test_sensitive_files_are_high_even_for_comments(repo, monkeypatch, path, kind):
    facts = change(repo, monkeypatch, path, "# Before\n", "# After\n")
    assert facts["tier"] == "high"
    assert facts["files"][0]["kind"] == kind


def test_other_code_and_interpolation_and_indentation_stay_normal(repo, monkeypatch):
    write(repo, "ui/app/example.ts", 'const message = "Hello";\n')
    write(repo, "app.py", 'if ready:\n    send(f"Hello {name}")\n    flush()\n')
    base = commit(repo, monkeypatch, days=30)
    write(repo, "ui/app/example.ts", 'const message = "Welcome";\n')
    write(repo, "app.py", 'if ready:\n    send(f"Hello {other}")\nflush()\n')
    commit(repo, monkeypatch)
    facts = diff_facts.diff_facts(base, repo=repo)
    assert facts["tier"] == "normal"
    assert all(not h["copy_only"] for f in facts["files"] for h in f["hunks"])


def test_docs_and_tests_are_low_even_with_code_changes(repo, monkeypatch):
    write(repo, "docs/example.md", "Before\n")
    write(repo, "scripts/tests/test_example.py", "assert 1\n")
    base = commit(repo, monkeypatch, days=30)
    write(repo, "docs/example.md", "After\n")
    write(repo, "scripts/tests/test_example.py", "assert 2\n")
    commit(repo, monkeypatch)
    facts = diff_facts.diff_facts(base, repo=repo)
    assert facts["tier"] == "low"
    assert {f["kind"] for f in facts["files"]} == {"docs", "test"}


def test_deleted_recent_uses_only_deleted_ranges_and_excludes_old_lines(repo, monkeypatch):
    write(repo, "app.py", "old = 1\nkeep = 2\n")
    commit(repo, monkeypatch, days=30)
    write(repo, "app.py", "old = 1\nkeep = 2\nrecent = 3\nalso_recent = 4\n")
    base = commit(repo, monkeypatch, days=2)
    write(repo, "app.py", "keep = 2\n")
    commit(repo, monkeypatch)
    calls = []
    real_git = diff_facts.git

    def recording_git(repo, *args):
        if args[0] == "blame":
            calls.append(args)
        return real_git(repo, *args)

    monkeypatch.setattr(diff_facts, "git", recording_git)
    found = diff_facts.deleted_recent(base, repo=repo, now=NOW)
    assert [(f["path"], f["line"], f["text"], f["age_days"]) for f in found] == [
        ("app.py", 3, "recent = 3", 2),
        ("app.py", 4, "also_recent = 4", 2),
    ]
    assert {f["commit"] for f in found} == {base}
    assert calls == [("blame", "--porcelain", "-L", "1,1", "-L", "3,4", base, "--", "app.py")]
    assert diff_facts.deleted_recent(base, days=1, repo=repo, now=NOW) == []


def test_cli_honors_head_and_handles_quoted_paths(repo, monkeypatch):
    path = 'app/quoted "name".py'
    write(repo, path, 'MESSAGE = "Hello"\n')
    base = commit(repo, monkeypatch, days=30)
    write(repo, path, 'MESSAGE = "Welcome"\n')
    head = commit(repo, monkeypatch)
    write(repo, path, "MESSAGE = 42\n")
    commit(repo, monkeypatch)
    (repo / "scripts").mkdir()
    script = repo / "scripts/diff_facts.py"
    shutil.copyfile(Path(diff_facts.__file__), script)
    result = subprocess.run(
        [sys.executable, str(script), "--base", base, "--head", head], check=True, capture_output=True, text=True
    )
    facts = json.loads(result.stdout)
    assert facts["base"] == base and facts["head"] == head and facts["tier"] == "low"
    assert facts["files"][0]["path"] == path


def test_moving_a_literal_into_a_statement_is_code(repo, monkeypatch):
    facts = change(repo, monkeypatch, "app.py", 'def f():\n    return\n    "ok"\n', 'def f():\n    return "ok"\n')
    assert facts["tier"] == "normal"
    assert not any(h["copy_only"] for h in facts["files"][0]["hunks"])


def test_augmented_sql_assignment_is_sql(repo, monkeypatch):
    before = 'QUERY_SQL = "SELECT 1 FROM t"\nQUERY_SQL += " WHERE active=1"\n'
    facts = change(repo, monkeypatch, "app.py", before, before.replace("active=1", "active=0"))
    hunk = facts["files"][0]["hunks"][0]
    assert facts["tier"] == "normal" and hunk["sql"] and not hunk["copy_only"]
