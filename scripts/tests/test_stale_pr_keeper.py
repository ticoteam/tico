"""The stale PR keeper re-enqueues a pull request the merge queue dropped for a flake, at most once per head, and
comments once naming the failure for the rest. No network: a fake GitHub serves trimmed real fixtures."""

import copy
from datetime import datetime, timezone
import hashlib
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
QUARANTINE = frozenset({RECRUITING})


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
    sha("3"): ("python-8", "pytest-failed.log"),  # #3034: the recruiting test
    sha("6a"): ("browser-1", "browser-failed.log"),  # #3033: a UI script
    sha("7a"): ("python-6", "pytest-failed.log"),  # #2966: the recruiting test
    sha("8a"): ("python-1", "worker-crashed.log"),  # #2953: an xdist worker crashed
}


def comment_marker(head, queue, tests=QUARANTINE):
    return f"<!-- stale-pr-keeper:comment {head} queue={queue} quarantine={keeper.quarantine_key(tests)} -->"


class FakeGitHub:
    def __init__(self, comments=None, fail=(), can_enqueue=True, status="completed"):
        self.pages = json.loads((FIXTURES / "open-prs.json").read_text())
        # {number: [body, ...]}; comment ids are number * 1000 + position
        self.existing = {n: [(n * 1000 + i, b) for i, b in enumerate(bodies)] for n, bodies in (comments or {}).items()}
        self.fail = set(fail)
        self.can_enqueue = can_enqueue
        self.status = status
        self.calls = []

    def open_prs(self):
        self.calls.append(("open_prs",))
        return self.pages

    def comments(self, heads):
        self.calls.append(("comments", tuple(heads)))
        return {n: self.existing.get(n, []) for n in heads}

    def queue_run(self, commit):
        self.calls.append(("queue_run", commit))
        if commit not in RUNS:
            return None
        return {
            "id": commit,
            "status": self.status,
            "html_url": f"https://github.com/ticoteam/tico/actions/runs/{commit[:6]}",
        }

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

    def enqueue(self, number, head):
        self._write("enqueue", number, head)

    def comment(self, number, body):
        self._write("comment", number, body)
        return 777

    def edit_comment(self, comment_id, body):
        self._write("edit_comment", comment_id, body)

    def delete_comment(self, comment_id):
        self._write("delete_comment", comment_id)

    def add_label(self, number, label):
        self._write("add_label", number, label)

    def remove_label(self, number, label):
        self._write("remove_label", number, label)

    def writes(self, kind=None):
        kinds = ("enqueue", "comment", "edit_comment", "delete_comment", "add_label", "remove_label")
        return [c for c in self.calls if c[0] in kinds and (kind is None or c[0] == kind)]


def written_to(gh, number):
    return [c for c in gh.writes() if c[0] in ("enqueue", "comment", "add_label", "remove_label") and c[1] == number]


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


def finding(*tests, job="python-5", step="Run selected tests", **kw):
    return keeper.Finding(job=job, step=step, url="https://x", tests=list(tests), **kw)


@pytest.mark.parametrize(
    ("findings", "expected"),
    [
        ([finding((RECRUITING, False))], True),  # quarantined
        ([finding((CRASHED, True))], True),  # crashed worker
        ([finding((CRASHED, True), job="contracts")], True),
        ([finding((RECRUITING, False), (CRASHED, True))], True),
        ([finding((RECRUITING + "[a]", False))], True),  # a parameter of a quarantined test function
        ([finding(("backend/tests/test_x.py::test_y", False))], False),
        ([finding((RECRUITING, False)), finding(("backend/tests/test_x.py::test_y", False))], False),
        ([finding()], False),  # a failed step that named no test
        ([finding((CRASHED, True), log_missing=True)], False),
        ([finding(step="Shell scripts (shellcheck, pinned)")], False),
        ([finding(scripts=["tasks-page.cjs"])], False),
        # conformance runs cargo tests in the same step: a Rust failure there leaves no pytest summary line
        ([finding((CRASHED, True), job="conformance")], False),
        ([finding((CRASHED, True), job="browser-1")], False),
        ([], False),
    ],
)
def test_only_quarantined_tests_and_crashed_workers_in_pytest_legs_are_flakes(findings, expected):
    assert keeper.flaky(findings, QUARANTINE) is expected


def entry(test, **overrides):
    data = {
        "test": test,
        "first_seen": "2026-11-07",
        "commit": sha("a"),
        "runs": ["https://github.com/ticoteam/tico/actions/runs/1"],
        "ticket": "",
        "reason": "failed and passed on the same commit",
    }
    return {**data, **overrides}


def test_the_quarantine_applies_flake_quarantines_rules_until_that_script_is_in_the_checkout(tmp_path):
    folder = tmp_path / "ci" / "quarantine"
    folder.mkdir(parents=True)
    (tmp_path / "backend/tests").mkdir(parents=True)
    (tmp_path / "backend/tests/test_recruiting.py").write_text("")
    (tmp_path / "backend/tests/test_turn_fixer.py").write_text("")
    (folder / keeper.entry_name(RECRUITING)).write_text(json.dumps(entry(RECRUITING)))
    (folder / "misnamed.json").write_text(json.dumps(entry(CRASHED)))
    (folder / keeper.entry_name(CRASHED)).write_text(json.dumps(entry(CRASHED, runs=[])))  # no run url
    gone = "backend/tests/test_gone.py::test_x"
    (folder / keeper.entry_name(gone)).write_text(json.dumps(entry(gone)))  # its test file is gone
    (folder / "broken.json").write_text("{not json")
    assert keeper.quarantined(tmp_path) == {RECRUITING}
    assert keeper.quarantined(tmp_path / "missing") == set()


def test_entry_names_match_flake_quarantine():
    digest = hashlib.sha256(b"a/b.py::test_x[1-2]").hexdigest()[:10]
    assert keeper.entry_name("a/b.py::test_x[1-2]") == f"a-b.py-test_x-1-2-{digest}.json"


def test_the_quarantine_uses_flake_quarantine_load_when_the_checkout_has_it(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/flake_quarantine.py").write_text(
        "def load(root):\n    return {'from/flake_quarantine.py::test_load': {'file': str(root)}}\n"
    )
    assert keeper.quarantined(tmp_path) == {"from/flake_quarantine.py::test_load"}


# The whole run


def run(gh, tests=QUARANTINE, dry_run=False):
    return keeper.keep(gh, set(tests), NOW, dry_run)


def test_a_quarantined_failure_is_re_enqueued_at_its_head_with_its_marker_first():
    gh = FakeGitHub()
    text, ok = run(gh)
    assert ok
    calls = written_to(gh, 2984)
    assert [c[0] for c in calls] == ["comment", "enqueue"]
    assert f"<!-- stale-pr-keeper:requeue {sha('a')} -->" in calls[0][2]
    assert f"`{RECRUITING}` (quarantined)" in calls[0][2]
    assert calls[1] == ("enqueue", 2984, sha("a"))  # gh pr merge --match-head-commit: a newer push is not queued
    assert "#2984" in text.split("### Re-enqueued")[1].split("###")[0]


def test_a_crashed_worker_is_re_enqueued():
    gh = FakeGitHub()
    run(gh)
    calls = written_to(gh, 3060)
    assert [c[0] for c in calls] == ["comment", "enqueue"]
    assert "(worker crashed)" in calls[0][2]


def test_without_a_queue_token_a_flake_is_commented_with_the_command_and_its_one_requeue_is_kept():
    gh = FakeGitHub(can_enqueue=False)
    run(gh)
    assert gh.writes("enqueue") == []
    (call,) = written_to(gh, 2984)
    assert "Only flakes failed. Re-enqueue it: `gh pr merge 2984`." in call[2]
    assert "stale-pr-keeper:requeue" not in call[2]


def test_a_real_failure_gets_one_comment_naming_the_test_and_no_enqueue():
    gh = FakeGitHub()
    run(gh, tests=set())
    calls = written_to(gh, 3034)
    assert [c[0] for c in calls] == ["comment"]
    body = calls[0][2]
    assert f"`{RECRUITING}` in [python-8]" in body
    assert "a check failed" in body
    assert comment_marker(sha("c"), sha("3"), set()) in body


def test_a_ui_script_failure_names_the_script():
    gh = FakeGitHub()
    run(gh)
    (call,) = written_to(gh, 3033)
    assert call[0] == "comment" and "`tasks-page.cjs` (UI script) in [browser-1]" in call[2]


def test_a_head_already_commented_on_costs_no_run_lookup():
    gh = FakeGitHub(comments={2966: [f"old\n{comment_marker(sha('c2'), sha('7a'))}"]})
    text, _ = run(gh)
    assert written_to(gh, 2966) == []
    assert ("queue_run", sha("7a")) not in gh.calls
    assert "#2966: already commented" in text


def test_a_comment_on_an_older_head_does_not_count():
    gh = FakeGitHub(comments={2966: [comment_marker(sha("99"), sha("7a"), set())]})
    run(gh, tests=set())
    assert [c[0] for c in written_to(gh, 2966)] == ["comment"]


def test_a_test_quarantined_after_the_comment_gets_its_head_re_enqueued():
    # #2966 was commented on before the recruiting test was quarantined.
    gh = FakeGitHub(comments={2966: [comment_marker(sha("c2"), sha("7a"), set())]})
    run(gh)
    assert [c[0] for c in written_to(gh, 2966)] == ["comment", "enqueue"]


def test_a_new_queue_run_of_the_same_head_updates_the_comment_instead_of_adding_one():
    gh = FakeGitHub(comments={2966: [comment_marker(sha("c2"), sha("55"), set())]})
    text, _ = run(gh, tests=set())
    assert written_to(gh, 2966) == []
    (edit,) = gh.writes("edit_comment")
    assert edit[1] == 2966000 and comment_marker(sha("c2"), sha("7a"), set()) in edit[2]
    assert "#2966 Mail from the source: updated" in text


def test_a_second_flake_on_the_same_head_is_commented_not_re_enqueued():
    gh = FakeGitHub(comments={2953: [f"<!-- stale-pr-keeper:requeue {sha('d3')} -->"]})
    run(gh)
    (call,) = written_to(gh, 2953)
    assert call[0] == "comment"
    assert "Already re-queued once for this commit" in call[2]
    assert f"`{CRASHED}` (worker crashed)" in call[2]


def test_a_requeued_and_commented_head_is_done():
    gh = FakeGitHub(
        comments={2953: [f"<!-- stale-pr-keeper:requeue {sha('d3')} -->", comment_marker(sha("d3"), sha("0"), set())]}
    )
    run(gh)
    assert written_to(gh, 2953) == [] and gh.writes("edit_comment") == []
    assert ("queue_run", sha("8a")) not in gh.calls


def test_a_queue_run_still_finishing_is_read_later():
    gh = FakeGitHub(status="in_progress")
    text, ok = run(gh)
    assert ok and written_to(gh, 2984) == [] and ("jobs", sha("1")) not in gh.calls
    assert "#2984: its queue run is still finishing" in text


def test_conflicting_red_prs_are_labelled_and_the_label_comes_off_once_clean():
    gh = FakeGitHub()
    run(gh)
    assert gh.writes("add_label") == [("add_label", 2991, "needs-main-merge")]
    assert gh.writes("remove_label") == [("remove_label", 3045, "needs-main-merge")]


@pytest.mark.parametrize(
    ("mergeable", "checks", "labels", "expected"),
    [
        ("CONFLICTING", "FAILURE", [], True),
        ("CONFLICTING", "ERROR", [], True),
        ("CONFLICTING", "FAILURE", ["needs-main-merge"], None),
        ("CONFLICTING", "SUCCESS", [], None),  # green: not red because of the conflict
        ("CONFLICTING", "PENDING", [], None),
        ("CONFLICTING", None, [], None),
        ("MERGEABLE", "FAILURE", [], None),  # red for its own reasons
        ("MERGEABLE", "FAILURE", ["needs-main-merge"], False),
        ("UNKNOWN", "FAILURE", ["needs-main-merge"], None),  # GitHub has not worked it out yet
    ],
)
def test_the_label_rule(mergeable, checks, labels, expected):
    pr = keeper.PR(1, "t", "u", False, "main", sha("a"), mergeable, False, labels, None, checks)
    assert keeper.needs_label(pr) is expected
    assert (
        keeper.needs_label(keeper.PR(1, "t", "u", True, "main", sha("a"), mergeable, False, labels, None, checks))
        is None
    )


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
    (comments_call,) = [c for c in gh.calls if c[0] == "comments"]
    assert set(comments_call[1]) == {2984, 3060, 3034, 3033, 2966, 2953}


def test_the_summary_lists_prs_with_no_push_in_seven_days_oldest_first():
    gh = FakeGitHub()
    text, _ = run(gh)
    stale = text.split("### No push in 7 days")[1]
    assert stale.index("#3061") < stale.index("#3045")
    assert "| 19 | yes |" in stale and "| 9 |  |" in stale
    assert "#2984" not in stale
    assert "#2975" not in stale  # an old commit, pushed yesterday (its first check suite)


def test_a_dry_run_writes_nothing():
    gh = FakeGitHub()
    text, ok = run(gh, dry_run=True)
    assert ok and gh.writes() == []
    assert "(dry run)" in text


def test_a_failed_enqueue_takes_its_marker_back_and_fails_the_run_after_the_rest():
    gh = FakeGitHub(fail={"enqueue"})
    text, ok = run(gh)
    assert not ok
    assert "#2984 requeued: enqueue refused" in text
    assert ("delete_comment", 777) in gh.calls
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
    real = gh.read_job_log

    def failing_log(job_id):
        if job_id.startswith(sha("1")):
            raise subprocess.CalledProcessError(1, ["gh"], stderr="HTTP 502: Bad Gateway")
        return real(job_id)

    monkeypatch.setattr(gh, "read_job_log", failing_log)
    text, ok = run(gh)
    assert not ok and "#2984 reading its queue run: HTTP 502" in text
    assert written_to(gh, 2984) == []
    assert [c[0] for c in written_to(gh, 3060)] == ["comment", "enqueue"]


def test_parse_prs_reads_the_tested_head_and_the_push_time():
    prs = {pr.number: pr for pr in keeper.parse_prs(json.loads((FIXTURES / "open-prs.json").read_text()))}
    assert prs[2999].queue_commit == sha("5") and prs[2999].tested_head == sha("9") and prs[2999].head == sha("e")
    assert prs[3017].removed_reason is None and prs[3017].in_queue
    assert prs[3061].checks is None and prs[3061].draft
    assert prs[2975].pushed == datetime(2026, 11, 7, 20, 0, tzinfo=timezone.utc)


# The gh client, with subprocess faked


def completed(stdout):
    return lambda args, **_: subprocess.CompletedProcess(args, 0, stdout=stdout)


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


def comments_page(number, bodies, previous=None):
    info = {"hasPreviousPage": previous is not None, "startCursor": previous}
    nodes = [{"databaseId": number * 1000 + i, "body": b} for i, b in enumerate(bodies)]
    return {f"pr{number}": {"comments": {"pageInfo": info, "nodes": nodes}}}


def test_comments_page_back_until_the_heads_marker_is_found(monkeypatch):
    head = sha("a")
    replies = [
        {"data": {"repository": {**comments_page(1, ["chatter"] * 100, previous="c1"), **comments_page(2, ["x"])}}},
        {
            "data": {
                "repository": comments_page(1, ["chatter"] * 99 + [f"<!-- stale-pr-keeper:requeue {head} -->"], "c2")
            }
        },
    ]
    seen = []

    def fake_run(args, **_):
        seen.append(args)
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps(replies[len(seen) - 1]))

    monkeypatch.setattr(keeper.subprocess, "run", fake_run)
    out = keeper.GitHub("ticoteam/tico").comments({1: head, 2: sha("b")})
    assert len(seen) == 2  # the second page had the marker: the third is never read
    assert 'before: "c1"' in seen[1][4]
    assert len(out[1]) == 200 and [m.kind for m in keeper.markers(out[1])] == ["requeue"]
    assert out[2] == [(2000, "x")]


def test_an_expired_log_reads_as_gone_and_any_other_error_raises(monkeypatch, tmp_path):
    def fake_gh(stderr, code):
        script = tmp_path / "gh"
        script.write_text(f"#!/bin/sh\necho 'FAILED a.py::t - x'\necho '{stderr}' >&2\nexit {code}\n")
        script.chmod(0o755)
        monkeypatch.setenv("PATH", f"{tmp_path}:/usr/bin:/bin")

    gh = keeper.GitHub("ticoteam/tico")
    fake_gh("gh: Not Found (HTTP 410)", 1)
    assert gh.read_job_log(1) is None
    fake_gh("HTTP 502: Bad Gateway", 1)
    with pytest.raises(subprocess.CalledProcessError, match="returned non-zero"):
        gh.read_job_log(1)
    fake_gh("", 0)
    assert gh.read_job_log(1) == ([], [], [])


def test_enqueue_uses_the_queue_token_and_the_inspected_head(monkeypatch):
    seen = {}

    def fake_run(args, env=None, **_):
        seen.update(args=args, token=env["GH_TOKEN"])
        return subprocess.CompletedProcess(args, 0, stdout="")

    monkeypatch.setattr(keeper.subprocess, "run", fake_run)
    monkeypatch.setenv("GH_TOKEN", "workflow-token")
    gh = keeper.GitHub("ticoteam/tico", queue_token="app-token")
    assert gh.can_enqueue and not keeper.GitHub("ticoteam/tico").can_enqueue
    gh.enqueue(12, sha("a"))
    assert seen["args"] == ["gh", "pr", "merge", "12", "--repo", "ticoteam/tico", "--match-head-commit", sha("a")]
    assert seen["token"] == "app-token"


def test_the_workflow_is_hourly_and_on_demand_and_keeps_the_queue_token_apart():
    import yaml

    workflow = yaml.safe_load((keeper.ROOT / ".github/workflows/stale-pr-keeper.yml").read_text())
    triggers = workflow[True]  # PyYAML reads the bare `on` key as True
    assert "workflow_dispatch" in triggers
    assert triggers["schedule"][0]["cron"].split()[1:] == ["*", "*", "*", "*"]
    assert workflow["permissions"]["contents"] == "read"
    (job,) = workflow["jobs"].values()
    (step,) = [s for s in job["steps"] if "scripts/stale_pr_keeper.py" in s.get("run", "")]
    assert step["env"]["GH_TOKEN"] == "${{ github.token }}"
    assert step["env"]["QUEUE_TOKEN"] == "${{ secrets.STALE_PR_KEEPER_TOKEN }}"
