"""The full validation's trail: a test failing now that passed on the previous full run names the pull requests merged
in between whose plans left it out, in the summary and on one issue; nothing else is reported as new."""

import json
from pathlib import Path

import pytest

from scripts import full_validation as fv

HEAD, PREV = "b" * 40, "a" * 40
ENV = {"GITHUB_SHA": HEAD, "GITHUB_RUN_ID": "50", "GITHUB_REPOSITORY": "o/r", "GITHUB_SERVER_URL": "https://gh"}
PLAN = {
    "matrix": {
        "include": [
            {"suite": "python-1", "files": ["backend/tests/test_a.py"]},
            {"suite": "python-2", "files": ["backend/tests/test_b.py"]},
            {"suite": "browser-1", "files": ["chat", "tasks"]},
        ]
    }
}


def junit(path, cases):
    body = "".join(
        f'<testcase classname="{c}" name="{n}" file="{c.replace(".", "/")}.py">{inner}</testcase>'
        for c, n, inner in cases
    )
    path.write_text(f'<testsuites><testsuite name="pytest">{body}</testsuite></testsuites>')


def results(tmp_path, chat_passes=True):
    folder = tmp_path / "ci-results"
    folder.mkdir()
    junit(
        folder / "python-1.xml",
        [
            ("backend.tests.test_a", "test_ok", ""),
            ("backend.tests.test_a", "test_broke", '<failure message="x"/>'),
            ("backend.tests.test_a", "test_new", "<error/>"),
            ("backend.tests.test_a", "test_skip", "<skipped/>"),
        ],
    )
    (folder / "browser-1.json").write_text(json.dumps({"tasks": 3.0, **({"chat": 2.0} if chat_passes else {})}))
    return folder  # python-2 has no results: its job did not finish


class FakeGitHub:
    def __init__(self, previous, plans):
        self._previous, self._plans, self.issues = previous, plans, []

    def previous(self, run_id):
        assert run_id == "50"
        return self._previous

    def plans_of(self, pr, sha):
        return self._plans.get(sha, [])

    def file_issue(self, body):
        self.issues.append(body)
        return "https://gh/o/r/issues/9"


def test_outcomes_read_junit_and_browser_timings_and_name_suites_without_results(tmp_path):
    failed, passed, missing = fv.outcomes(PLAN, results(tmp_path, chat_passes=False))
    assert failed == {
        "backend/tests/test_a.py::test_broke",
        "backend/tests/test_a.py::test_new",
        "ui/tests/chat.cjs",
    }
    assert passed == {"backend/tests/test_a.py::test_ok", "ui/tests/tasks.cjs"}
    assert missing == ["python-2"]


def test_a_class_test_keeps_its_class_in_the_id():
    case = fv.ET.fromstring(
        '<testcase classname="backend.tests.test_a.TestX" name="test_y" file="backend/tests/test_a.py"/>'
    )
    assert fv.case_id(case) == "backend/tests/test_a.py::TestX::test_y"


def test_a_plan_includes_a_selected_file_or_script_and_a_full_plan_includes_everything():
    plan = {"python": ["backend/tests/test_a.py"], "browser": ["chat"]}
    assert fv.included(plan, "backend/tests/test_a.py::test_broke")
    assert not fv.included(plan, "backend/tests/test_b.py::test_x")
    assert fv.included(plan, "ui/tests/chat.cjs") and not fv.included(plan, "ui/tests/tasks.cjs")
    assert fv.included({"python": None, "browser": None}, "ui/tests/tasks.cjs")


def test_merges_come_from_queue_merges_and_squash_subjects_oldest_first():
    log = f"{'3' * 40}\tFix a typo\n{'2' * 40}\tShip it (#12)\n{'1' * 40}\tMerge pull request #11 from o/branch\n"
    assert [(pr, subject) for _, pr, subject in fv.merged_prs(log)] == [
        (11, "Merge pull request #11 from o/branch"),
        (12, "Ship it (#12)"),
        (None, "Fix a typo"),
    ]


def test_a_new_failure_names_the_prs_that_skipped_it_and_files_one_issue(tmp_path):
    previous = {"sha": PREV, "url": "https://gh/run/49", "passed": ["backend/tests/test_a.py::test_broke"]}
    skipped, ran, unread = "1" * 40, "2" * 40, "3" * 40
    plans = {
        skipped: [{"python": ["backend/tests/test_b.py"], "browser": []}, {"python": [], "browser": []}],
        ran: [{"python": ["backend/tests/test_b.py"], "browser": []}, {"python": ["backend/tests/test_a.py"]}],
    }
    gh = FakeGitHub(previous, plans)
    log = f"{unread}\tdirect push\n{ran}\tMerge pull request #8 from o/x\n{skipped}\tMerge pull request #7 from o/y\n"
    seen = []

    def git_log(base, head):
        seen.append((base, head))
        return log

    record = tmp_path / "record.json"
    text = fv.report(_plan(tmp_path), results(tmp_path), record, gh, ENV, git_log)
    assert seen == [(PREV, HEAD)]
    # test_new failed but did not pass before (a new test or one already red): only test_broke is new.
    assert f"`backend/tests/test_a.py::test_broke` (passed at {PREV[:12]}): merged without running it: #7" in text
    assert "#8" not in text.split("test_broke")[1].split("\n")[0]
    assert f"{unread[:12]} (no pull request)" in text
    assert "No results from python-2" in text and "Issue: https://gh/o/r/issues/9" in text
    [issue] = gh.issues
    assert "`backend/tests/test_a.py::test_broke`" in issue and "merged without running it: #7" in issue
    assert f"commit range: {PREV[:12]}..{HEAD[:12]}" in issue
    saved = json.loads(record.read_text())
    assert saved["sha"] == HEAD and saved["passed"]["backend/tests/test_a.py::test_ok"] == HEAD
    assert "backend/tests/test_a.py::test_broke" in saved["failed"]


def test_no_new_failure_or_no_previous_run_files_no_issue(tmp_path):
    previous = {"sha": PREV, "passed": ["backend/tests/test_a.py::test_ok"]}
    gh = FakeGitHub(previous, {})
    text = fv.report(_plan(tmp_path), results(tmp_path), tmp_path / "r.json", gh, ENV, lambda b, h: "")
    assert "No test fails now that passed on the previous full run." in text and not gh.issues
    (tmp_path / "ci-results").rename(tmp_path / "old")
    first = FakeGitHub(None, {})
    text = fv.report(_plan(tmp_path), results(tmp_path), tmp_path / "r.json", first, ENV, lambda b, h: "")
    assert "nothing is traced" in text and not first.issues


def _plan(tmp_path):
    path = tmp_path / "ci-plan.json"
    path.write_text(json.dumps(PLAN))
    return Path(path)


def test_a_slow_test_a_plans_job_runs_counts_as_included():
    plan = {
        "python": ["backend/tests/test_a.py"],
        "matrix": {"include": [{"suite": "contracts", "files": ["e2e/tests/test_contract.py"]}]},
    }
    assert fv.included(plan, "e2e/tests/test_contract.py::test_x")
    assert not fv.included(plan, "e2e/tests/test_roles.py::test_x")


def test_a_failed_report_leaves_no_record_so_the_next_run_compares_with_the_older_one(tmp_path):
    previous = {"sha": PREV, "passed": ["backend/tests/test_a.py::test_broke"]}

    class Down(FakeGitHub):
        def file_issue(self, body):
            raise RuntimeError("GitHub API error")

    record = tmp_path / "record.json"
    with pytest.raises(RuntimeError):
        fv.report(_plan(tmp_path), results(tmp_path), record, Down(previous, {}), ENV, lambda b, h: "")
    assert not record.exists()


def test_a_test_the_run_did_not_reach_keeps_the_commit_where_it_last_passed(tmp_path):
    # python-2 (test_b) left no results: test_b keeps PREV, so a later failure is new and its range starts at PREV.
    older = "c" * 40
    previous = {
        "sha": PREV,
        "passed": {
            "backend/tests/test_b.py::test_later": older,
            "backend/tests/test_a.py::test_broke": PREV,
            "backend/tests/test_gone.py::test_x": PREV,
        },
    }
    record = tmp_path / "record.json"
    fv.report(_plan(tmp_path), results(tmp_path), record, FakeGitHub(previous, {}), ENV, lambda b, h: "")
    saved = json.loads(record.read_text())
    assert saved["sha"] == HEAD
    assert saved["passed"]["backend/tests/test_b.py::test_later"] == older
    assert saved["passed"]["backend/tests/test_a.py::test_ok"] == HEAD
    assert "backend/tests/test_a.py::test_broke" not in saved["passed"]  # failing now
    assert "backend/tests/test_gone.py::test_x" not in saved["passed"]  # its file is no longer planned

    # The next run fails test_b: its range starts at the commit where it last passed, not at this run's.
    later = {**ENV, "GITHUB_SHA": "d" * 40, "GITHUB_RUN_ID": "50"}
    (tmp_path / "ci-results").rename(tmp_path / "first")
    results(tmp_path)
    junit(tmp_path / "ci-results" / "python-2.xml", [("backend.tests.test_b", "test_later", "<failure/>")])
    seen, gh = [], FakeGitHub(saved, {})
    text = fv.report(_plan(tmp_path), tmp_path / "ci-results", record, gh, later, lambda b, h: seen.append(b) or "")
    assert sorted(seen) == [older] and f"`backend/tests/test_b.py::test_later` (passed at {older[:12]})" in text


def test_a_missing_artifact_is_none_but_a_failed_download_raises(monkeypatch):
    gh = fv.GitHub("o/r")
    listed = {"artifacts": []}
    monkeypatch.setattr(gh, "api", lambda path: listed)
    assert gh.download(1, fv.RECORD) is None
    listed = {"artifacts": [{"name": fv.RECORD, "expired": True}]}
    assert gh.download(1, fv.RECORD) is None

    def down(*args, **kwargs):
        raise fv.subprocess.CalledProcessError(1, args[0])

    listed = {"artifacts": [{"name": fv.RECORD, "expired": False}]}
    monkeypatch.setattr(fv.subprocess, "run", down)
    with pytest.raises(fv.subprocess.CalledProcessError):
        gh.download(1, fv.RECORD)
