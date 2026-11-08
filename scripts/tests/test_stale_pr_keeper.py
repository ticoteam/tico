"""The stale PR keeper re-enqueues a pull request the merge queue dropped for a flake, at most once per head, and
comments once naming the failure for the rest. No network: a fake GitHub serves trimmed real fixtures."""

import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess

import pytest

from scripts import stale_pr_keeper as keeper

FIXTURES = Path(__file__).parent / "stale_pr_keeper"
NOW = datetime(2026, 11, 8, 18, 0, tzinfo=timezone.utc)
RECRUITING = (
    "backend/tests/test_recruiting.py::"
    "test_marketing_and_engineering_titles_draft_from_the_template_that_is_the_job_by_any_of_its_names"
)
CRASHED = "backend/tests/test_turn_fixer.py::test_dead_sweep_walks_live_runs_not_every_attempt"


def sha(c):
    return (c * 40)[:40]


def log_lines(name):
    return (FIXTURES / name).read_text().splitlines(keepends=True)


def jobs_with(failing_name, failing_step="Run selected tests"):
    """The real queue run's jobs (python-5 failed, the rest cancelled, `checks` failed), with the failed job renamed."""
    jobs = copy.deepcopy(json.loads((FIXTURES / "jobs-queue-run.json").read_text())["jobs"])
    for job in jobs:
        if job["name"] == "python-5":
            job["name"] = failing_name
            for step in job["steps"]:
                if step["conclusion"] == "failure":
                    step["name"] = failing_step
    return jobs


# Each queue commit's run: its failed job's name and log. The log is None when it expired.
RUNS = {
    sha("1"): ("python-5", "pytest-failed.log"),  # #2984: the quarantined recruiting test
    sha("2"): ("python-9", "worker-crashed.log"),  # #3060: an xdist worker crashed
    sha("3"): ("python-8", "pytest-failed.log"),  # #3034: the same test, not quarantined in that test
    sha("6a"): ("browser-1", "browser-failed.log"),  # #3033: a UI script
    sha("7a"): ("python-6", "pytest-failed.log"),  # #2966: already commented on its head
    sha("8a"): ("python-1", "worker-crashed.log"),  # #2953: crashed again after one re-enqueue
}


class FakeGitHub:
    def __init__(self, comments=None, fail=()):
        self.pages = json.loads((FIXTURES / "open-prs.json").read_text())
        self.comments = comments or {}
        self.fail = set(fail)
        self.calls = []

    def open_prs(self):
        self.calls.append(("open_prs",))
        return self.pages

    def comment_bodies(self, numbers):
        self.calls.append(("comment_bodies", tuple(numbers)))
        return {n: self.comments.get(n, []) for n in numbers}

    def queue_run(self, commit):
        self.calls.append(("queue_run", commit))
        if commit not in RUNS:
            return None
        return {"id": commit, "html_url": f"https://github.com/ticoteam/tico/actions/runs/{commit[:6]}"}

    def jobs(self, run_id):
        self.calls.append(("jobs", run_id))
        name, _ = RUNS[run_id]
        jobs = jobs_with(name)
        for job in jobs:
            job["id"] = f"{run_id}:{job['id']}"
        return jobs

    def read_job_log(self, job_id):
        self.calls.append(("log", job_id))
        _, log = RUNS[job_id.split(":")[0]]
        return None if log is None else keeper.read_log(log_lines(log))

    def _write(self, *call):
        self.calls.append(call)
        if call[0] in self.fail:
            raise subprocess.CalledProcessError(1, ["gh"], stderr=f"{call[0]} refused")

    def enqueue(self, number):
        self._write("enqueue", number)

    def comment(self, number, body):
        self._write("comment", number, body)

    def add_label(self, number, label):
        self._write("add_label", number, label)

    def remove_label(self, number, label):
        self._write("remove_label", number, label)

    def writes(self, kind=None):
        return [
            c
            for c in self.calls
            if c[0] in ("enqueue", "comment", "add_label", "remove_label") and (kind is None or c[0] == kind)
        ]


def written_to(gh, number):
    return [c for c in gh.writes() if c[1] == number]


# Log reading


def test_a_pytest_leg_names_its_failed_test_from_the_short_summary():
    tests, scripts, scenarios = keeper.read_log(log_lines("pytest-failed.log"))
    assert tests == [(RECRUITING, False)]
    assert scripts == [] and scenarios == []


def test_a_crashed_xdist_worker_is_marked_crashed():
    tests, _, _ = keeper.read_log(log_lines("worker-crashed.log"))
    assert tests == [(CRASHED, True)]


def test_a_crash_pytest_never_summarised_still_names_its_test():
    lines = [
        line for line in log_lines("worker-crashed.log") if "short test summary" not in line and "FAILED" not in line
    ]
    assert keeper.read_log(lines)[0] == [(CRASHED, True)]


def test_a_failed_line_outside_the_summary_is_test_output_not_a_failure():
    lines = ["2026-11-08T20:00:00.0Z FAILED backend/tests/test_x.py::test_printed - from a test's own output\n"]
    assert keeper.read_log(lines) == ([], [], [])


def test_the_browser_suite_names_its_failed_ui_scripts():
    assert keeper.read_log(log_lines("browser-failed.log")) == ([], ["tasks-page.cjs"], [])


def test_the_factory_sim_names_each_scenario_with_a_fail_line():
    assert keeper.read_log(log_lines("factory-sim-failed.log")) == ([], [], ["stranded_doing seed 1"])


def test_a_coloured_summary_still_reads():
    lines = [
        "\x1b[31m=========================== short test summary info ============================\x1b[0m\n",
        f"\x1b[31mFAILED\x1b[0m {RECRUITING} - AssertionError\n",
        "\x1b[31m1 failed\x1b[0m, 3 passed in 1.0s\n",
    ]
    assert keeper.read_log(lines)[0] == [(RECRUITING, False)]


# Jobs and verdicts


def test_the_failed_shard_is_found_by_its_step_though_the_job_reads_cancelled():
    failed = keeper.failed_jobs(jobs_with("python-5"))
    assert [(job["name"], step) for job, step in failed] == [("python-5", "Run selected tests")]


def test_a_job_that_timed_out_with_no_failed_step_still_counts():
    jobs = [
        {
            "name": "python-2",
            "conclusion": "timed_out",
            "steps": [{"name": "Run selected tests", "conclusion": "cancelled"}],
        }
    ]
    assert [step for _, step in keeper.failed_jobs(jobs)] == ["timed out"]


def finding(*tests, step="Run selected tests", **kw):
    return keeper.Finding(job="python-5", step=step, url="https://x", tests=list(tests), **kw)


@pytest.mark.parametrize(
    ("findings", "expected"),
    [
        ([finding((RECRUITING, False))], True),  # quarantined
        ([finding((CRASHED, True))], True),  # crashed worker
        ([finding((RECRUITING, False), (CRASHED, True))], True),
        ([finding((RECRUITING + "[a]", False))], True),  # a parameter of a quarantined test function
        ([finding(("backend/tests/test_x.py::test_y", False))], False),
        ([finding((RECRUITING, False)), finding(("backend/tests/test_x.py::test_y", False))], False),
        ([finding()], False),  # a failed step that named no test
        ([finding((CRASHED, True), log_missing=True)], False),
        ([finding(step="Shell scripts (shellcheck, pinned)")], False),
        ([finding(scripts=["tasks-page.cjs"])], False),
        ([], False),
    ],
)
def test_only_quarantined_tests_and_crashed_workers_are_flakes(findings, expected):
    assert keeper.flaky(findings, {RECRUITING}) is expected


def test_the_quarantine_reads_the_flake_quarantine_entry_files(tmp_path):
    folder = tmp_path / "ci" / "quarantine"
    folder.mkdir(parents=True)
    entry = {
        "test": RECRUITING,
        "first_seen": "2026-11-07",
        "commit": sha("a"),
        "runs": ["https://x"],
        "ticket": "",
        "reason": "failed and passed on the same commit",
    }
    (folder / "backend-tests-test_recruiting.py-abc.json").write_text(json.dumps(entry))
    (folder / "broken.json").write_text("{not json")
    (folder / "no-node-id.json").write_text(json.dumps({"test": "backend/tests/test_x.py"}))
    assert keeper.quarantined(tmp_path) == {RECRUITING}
    assert keeper.quarantined(tmp_path / "missing") == set()


# The whole run


def run(gh, tests=frozenset({RECRUITING}), dry_run=False):
    return keeper.keep(gh, set(tests), NOW, dry_run)


def test_a_quarantined_failure_is_re_enqueued_with_its_marker_first():
    gh = FakeGitHub()
    text, ok = run(gh)
    assert ok
    calls = written_to(gh, 2984)
    assert [c[0] for c in calls] == ["comment", "enqueue"]
    assert f"<!-- stale-pr-keeper:requeue {sha('a')} -->" in calls[0][2]
    assert f"`{RECRUITING}` (quarantined)" in calls[0][2]
    assert "#2984" in text.split("### Re-enqueued")[1].split("###")[0]


def test_a_crashed_worker_is_re_enqueued():
    gh = FakeGitHub()
    run(gh)
    calls = written_to(gh, 3060)
    assert [c[0] for c in calls] == ["comment", "enqueue"]
    assert "(worker crashed)" in calls[0][2]


def test_a_real_failure_gets_one_comment_naming_the_test_and_no_enqueue():
    gh = FakeGitHub()
    run(gh, tests=set())
    calls = written_to(gh, 3034)
    assert [c[0] for c in calls] == ["comment"]
    body = calls[0][2]
    assert f"`{RECRUITING}` in [python-8]" in body
    assert "a check failed" in body
    assert f"<!-- stale-pr-keeper:comment {sha('c')} -->" in body


def test_a_ui_script_failure_names_the_script():
    gh = FakeGitHub()
    run(gh)
    (call,) = written_to(gh, 3033)
    assert call[0] == "comment" and "`tasks-page.cjs` (UI script) in [browser-1]" in call[2]


def test_a_head_already_commented_on_costs_no_run_lookup():
    gh = FakeGitHub(comments={2966: [f"old\n<!-- stale-pr-keeper:comment {sha('c2')} -->"]})
    text, _ = run(gh)
    assert written_to(gh, 2966) == []
    assert ("queue_run", sha("7a")) not in gh.calls
    assert "#2966: already commented" in text


def test_a_comment_on_an_older_head_does_not_count():
    gh = FakeGitHub(comments={2966: [f"<!-- stale-pr-keeper:comment {sha('99')} -->"]})
    run(gh, tests=set())
    assert [c[0] for c in written_to(gh, 2966)] == ["comment"]


def test_a_second_flake_on_the_same_head_is_commented_not_re_enqueued():
    gh = FakeGitHub(comments={2953: [f"<!-- stale-pr-keeper:requeue {sha('d3')} -->"]})
    run(gh)
    (call,) = written_to(gh, 2953)
    assert call[0] == "comment"
    assert "Already re-queued once for this commit" in call[2]
    assert f"`{CRASHED}` (worker crashed)" in call[2]


def test_conflicting_red_prs_are_labelled_and_the_label_comes_off_once_clean():
    gh = FakeGitHub()
    run(gh)
    assert gh.writes("add_label") == [("add_label", 2991, "needs-main-merge")]
    assert gh.writes("remove_label") == [("remove_label", 3045, "needs-main-merge")]
    # 2962 conflicts but its checks are green: not "red only because it is behind main".


def test_nothing_merges_main_or_closes_a_pr():
    gh = FakeGitHub()
    run(gh)
    assert {c[0] for c in gh.writes()} <= {"enqueue", "comment", "add_label", "remove_label"}


def test_left_alone_a_manual_removal_a_conflict_a_moved_head_a_queued_a_draft_and_another_base():
    gh = FakeGitHub()
    text, _ = run(gh)
    for number in (2917, 2999, 3017, 3061, 2970):
        assert written_to(gh, number) == [], number
    assert [c for c in written_to(gh, 2991) if c[0] != "add_label"] == []
    assert "#2917: removed from the queue as manual" in text
    assert "#2999: pushed since the queue removed it" in text
    assert "#2991: removed for a merge conflict" in text
    (bodies_call,) = [c for c in gh.calls if c[0] == "comment_bodies"]
    assert set(bodies_call[1]) == {2984, 3060, 3034, 3033, 2966, 2953}


def test_the_summary_lists_prs_with_no_push_in_seven_days_oldest_first():
    gh = FakeGitHub()
    text, _ = run(gh)
    stale = text.split("### No push in 7 days")[1]
    assert stale.index("#3061") < stale.index("#3045")
    assert "| 19 | yes |" in stale and "| 9 |  |" in stale
    assert "#2984" not in stale


def test_a_dry_run_writes_nothing():
    gh = FakeGitHub()
    text, ok = run(gh, dry_run=True)
    assert ok and gh.writes() == []
    assert "(dry run)" in text


def test_a_failed_enqueue_is_reported_and_fails_the_run_after_the_rest():
    gh = FakeGitHub(fail={"enqueue"})
    text, ok = run(gh)
    assert not ok
    assert "#2984 requeued: enqueue refused" in text
    assert [c[0] for c in written_to(gh, 3060)] == ["comment", "enqueue"]  # later pull requests were still handled
    assert [c[0] for c in written_to(gh, 3033)] == ["comment"]


def test_a_queue_run_that_is_gone_is_still_commented_on(monkeypatch):
    monkeypatch.delitem(RUNS, sha("3"))
    gh = FakeGitHub()
    run(gh)
    (call,) = written_to(gh, 3034)
    assert "No queue run found for it." in call[2]


def test_an_expired_log_is_never_a_flake(monkeypatch):
    monkeypatch.setitem(RUNS, sha("2"), ("python-9", None))
    gh = FakeGitHub()
    run(gh)
    (call,) = written_to(gh, 3060)
    assert call[0] == "comment" and "log expired" in call[2]


def test_a_read_error_writes_nothing_for_that_pr_and_fails_the_run(monkeypatch):
    gh = FakeGitHub()
    real = gh.queue_run

    def failing_read(commit):
        if commit == sha("1"):
            raise subprocess.CalledProcessError(1, ["gh"], stderr="HTTP 502")
        return real(commit)

    monkeypatch.setattr(gh, "queue_run", failing_read)
    text, ok = run(gh)
    assert not ok and "#2984 reading its queue run: HTTP 502" in text
    assert written_to(gh, 2984) == []
    assert [c[0] for c in written_to(gh, 3060)] == ["comment", "enqueue"]


def test_parse_prs_reads_the_tested_head_from_the_queue_merge_commit():
    prs = {pr.number: pr for pr in keeper.parse_prs(json.loads((FIXTURES / "open-prs.json").read_text()))}
    assert prs[2999].queue_commit == sha("5") and prs[2999].tested_head == sha("9") and prs[2999].head == sha("e")
    assert prs[3017].removed_reason is None and prs[3017].in_queue
    assert prs[3061].checks is None and prs[3061].draft


def test_the_github_client_pages_open_prs_with_one_graphql_call_each(monkeypatch):
    pages = json.loads((FIXTURES / "open-prs.json").read_text())
    seen = []

    def fake_run(args, **_):
        seen.append(args)
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps(pages[len(seen) - 1]))

    monkeypatch.setattr(keeper.subprocess, "run", fake_run)
    assert keeper.GitHub("ticoteam/tico").open_prs() == pages
    assert len(seen) == 2
    assert "after=Y3Vyc29yOjk=" in seen[1] and not any(a.startswith("after=") for a in seen[0])


def test_the_workflow_is_hourly_and_on_demand_and_runs_the_script():
    import yaml

    workflow = yaml.safe_load((keeper.ROOT / ".github/workflows/stale-pr-keeper.yml").read_text())
    triggers = workflow[True]  # PyYAML reads the bare `on` key as True
    assert "workflow_dispatch" in triggers
    assert triggers["schedule"][0]["cron"].split()[1:] == ["*", "*", "*", "*"]
    (job,) = workflow["jobs"].values()
    assert any("scripts/stale_pr_keeper.py" in step.get("run", "") for step in job["steps"])
