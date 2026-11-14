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


# The names below are not files in this checkout: a real path here would make the dependency map select this test for
# every change to that file (scripts/pr_checks.py reads path literals as reads or runs).
@pytest.mark.parametrize(
    ("name", "measured"),
    [
        ("backend/no_such_module.py", True),
        ("clients/no_such_cli.py", True),
        ("backend/tests/test_no_such.py", False),
        ("backend/migrations/0001_no_such.py", False),
        ("backend/no_such/conftest.py", False),
        ("clients/no_such_test.py", False),
        ("scripts/no_such_script.py", False),
        ("backend/static/no_such.js", False),
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
    source = tmp_path / "backend/x.py"
    source.parent.mkdir()
    source.write_text("x = 1\n")
    git("add", "-A")
    git("commit", "-q", "-m", "base")
    base = git("rev-parse", "HEAD")
    source.write_text(
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

    # A base that moved on after the branch point (a pull request's base) counts only the branch's own lines.
    git("checkout", "-q", "-b", "later", base)
    other = tmp_path / "backend/y.py"
    other.write_text("z = 3\n")
    git("add", "-A")
    git("commit", "-q", "-m", "main moves on")
    later = git("rev-parse", "HEAD")
    git("checkout", "-q", head)  # the plan reads statements from the checkout, which is the head
    assert dc.plan_coverage(later, head, tmp_path) == {"lines": {"backend/x.py": [2, 5]}}
    assert dc.plan_coverage(later, head, tmp_path, changed=["backend/x.py"]) == {"lines": {"backend/x.py": [2, 5]}}
    with pytest.raises(subprocess.CalledProcessError):
        dc.plan_coverage("no-such-ref", head, tmp_path, changed=[])


def test_gate_lists_each_missed_statement_with_its_text(tmp_path):
    source = tmp_path / "backend/x.py"
    source.parent.mkdir()
    source.write_text("a\nb\nc\ndef f():\n    return 1\n")
    plan = {"coverage": {"lines": {"backend/x.py": [4, 5]}}}
    results = tmp_path / "gate-output"
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


def test_a_plan_that_could_not_read_the_diff_fails_the_gate():
    passed, text = dc.report({"coverage": {"lines": {}, "error": "bad revision"}}, Path("/nonexistent"))
    assert not passed
    assert "bad revision" in text


def test_extract_keeps_plan_lines_that_ran_under_the_checkout_path(tmp_path, monkeypatch):
    monkeypatch.setattr(dc, "ROOT", tmp_path)
    source = tmp_path / "backend/x.py"
    source.parent.mkdir()
    # 1 def, 2 i = 0, 3-5 one while statement over three lines, 6 i += 1, 7 return.
    source.write_text("def f(xs):\n    i = 0\n    while (\n        i < len(xs)\n    ):\n        i += 1\n    return i\n")
    plan = {"coverage": {"lines": {"backend/x.py": [2, 3, 6, 7]}}}
    data = CoverageData(basename=str(tmp_path / "coverage.data"))
    # The condition records its continuation line (4), which coverage.py's analysis maps to the statement's line 3.
    data.add_lines({os.path.realpath(source): [1, 2, 4, 7]})
    data.write()

    out = tmp_path / "ci-results" / "coverage-python-1.json"
    assert dc.extract(plan, tmp_path / "coverage.data", out) == {"backend/x.py": [2, 3, 7]}
    assert json.loads(out.read_text()) == {"backend/x.py": [2, 3, 7]}
    assert dc.load_results(out.parent)["backend/x.py"] == {2, 3, 7}


def test_a_measured_job_keeps_the_environment_its_job_sets(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from scripts import pr_checks as checks

    (tmp_path / "test_no_such_sweep.py").write_text("")
    plan = {
        "matrix": {
            "include": [{"suite": "contracts", "files": ["test_no_such_sweep.py"], "env": {"NO_SUCH_SWEEP": "1"}}]
        },
        "coverage": {"lines": {"backend/no_such_module.py": [1]}},
    }
    target = tmp_path / "plan.json"
    target.write_text(json.dumps(plan))
    envs = []
    monkeypatch.setattr(dc, "extract", lambda *a, **kw: {})
    monkeypatch.setattr(
        checks.subprocess,
        "run",
        lambda command, **kw: envs.append((command, kw.get("env"))) or SimpleNamespace(returncode=1),
    )
    assert checks.run(target, "contracts", root=tmp_path) == 1
    ((command, env),) = envs
    assert "--cov=backend" in command and env["NO_SUCH_SWEEP"] == "1"
    assert env["COVERAGE_FILE"].endswith("coverage-contracts.data") and env["COVERAGE_CORE"] == "sysmon"
