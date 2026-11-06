"""The diff coverage gate: the statements a change adds, the pragma rule, the gate's listing and the data round trip."""

import json
import os
from pathlib import Path
import subprocess

from coverage import CoverageData
import pytest

from scripts import diff_coverage as dc

DIFF = """diff --git a/backend/x.py b/backend/x.py
--- a/backend/x.py
+++ b/backend/x.py
@@ -3,0 +4,2 @@
+def f():
+    return 1
@@ -10 +12 @@
-old
+new
"""


@pytest.mark.parametrize(
    ("name", "measured"),
    [
        ("backend/app.py", True),
        ("clients/hubcli.py", True),
        ("backend/tests/test_app.py", False),
        ("backend/migrations/0001_init.py", False),
        ("backend/conftest.py", False),
        ("clients/tool_test.py", False),
        ("scripts/diff_coverage.py", False),
        ("backend/static/app.js", False),
    ],
)
def test_only_backend_and_client_source_is_measured(name, measured):
    assert dc.measured(name) is measured


def test_added_lines_are_new_side_numbers():
    assert dc.added_lines(DIFF) == {"backend/x.py": [4, 5, 12]}
    assert dc.added_text(DIFF) == {"backend/x.py": [(4, "def f():"), (5, "    return 1"), (12, "new")]}


def test_a_bare_pragma_is_refused_and_a_reasoned_one_passes():
    text = """+++ b/backend/x.py
@@ -0,0 +1,3 @@
+x = 1  # pragma: no cover
+y = 2  # pragma: no cover - the retry sleeps for real and no test can wait for it
+z = 3  # pragma: no cover – an en dash reason that is long enough here
+++ b/backend/tests/test_x.py
@@ -0,0 +1 @@
+w = 4  # pragma: no cover
"""
    assert dc.bare_pragmas(text) == [("backend/x.py", 1, "x = 1  # pragma: no cover")]


def test_plan_counts_only_statements_the_change_adds(tmp_path, monkeypatch):
    def git(*args):
        return subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@example.test", *args],
            cwd=tmp_path,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    git("init", "-q")
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "x.py").write_text("x = 1\n")
    git("add", "-A")
    git("commit", "-q", "-m", "base")
    base = git("rev-parse", "HEAD")
    (tmp_path / "backend" / "x.py").write_text(
        "x = 1\n"
        "def f():\n"  # 2: a statement
        '    """doc."""\n'  # 3: a docstring, not a statement
        "    # a comment\n"  # 4
        "    return 1\n"  # 5: a statement
        "    y = 2  # pragma: no cover - a reason of three words\n"  # 6: excluded
    )
    git("commit", "-qam", "head")
    head = git("rev-parse", "HEAD")
    monkeypatch.setattr(dc, "ROOT", tmp_path)
    assert dc.plan_coverage(base, head, tmp_path) == {"lines": {"backend/x.py": [2, 5]}}


def test_gate_lists_each_missed_statement_with_its_text(tmp_path):
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "x.py").write_text("a\nb\nc\ndef f():\n    return 1\n")
    plan = {"coverage": {"lines": {"backend/x.py": [4, 5]}}}
    results = tmp_path / "ci"
    results.mkdir()
    (results / "coverage-python-1.json").write_text(json.dumps({"backend/x.py": [4]}))

    passed, text = dc.report(plan, results, threshold=1.0, root=tmp_path)
    assert not passed
    assert "1 of 2 added statements ran (50.0%)" in text
    assert "  backend/x.py:5: return 1" in text

    assert dc.report(plan, results, threshold=0.5, root=tmp_path)[0]


def test_a_change_with_no_statements_passes_and_says_so():
    passed, text = dc.report({"coverage": {"lines": {}}}, Path("/nonexistent"))
    assert passed
    assert "adds no Python statement" in text


def test_extract_keeps_plan_lines_that_ran_under_the_checkout_path(tmp_path, monkeypatch):
    monkeypatch.setattr(dc, "ROOT", tmp_path)
    plan = {"coverage": {"lines": {"backend/x.py": [4, 5]}}}
    data = CoverageData(basename=str(tmp_path / "coverage.data"))
    data.add_lines({os.path.realpath(tmp_path / "backend" / "x.py"): [4, 9]})
    data.write()

    out = tmp_path / "ci-results" / "coverage-python-1.json"
    assert dc.extract(plan, tmp_path / "coverage.data", out) == {"backend/x.py": [4]}
    assert json.loads(out.read_text()) == {"backend/x.py": [4]}
    assert dc.load_results(out.parent)["backend/x.py"] == {4}
