"""The stale PR keeper re-enqueues a pull request the merge queue dropped for a flake, at most once per head, and
comments once naming the failure for the rest. No network: a fake GitHub serves trimmed real fixtures."""

import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess

import pytest

from scripts import stale_pr_keeper as keeper

FIXTURES = Path(__file__).parent / "stale_pr_keeper"
NOW = datetime(2026, 11, 8, 18, 0, tzinfo=timezone.utc)
# The tests and UI script the real logs name, read from them: a literal path to a test here would make pr_checks.py select
# this file for every change to that test (and its folder).
RECRUITING = re.search(r" FAILED (\S+) - ", (FIXTURES / "pytest-failed.log").read_text()).group(1)  # quarantined
CRASHED = re.search(r" FAILED (\S+) - worker", (FIXTURES / "worker-crashed.log").read_text()).group(1)
UI_SCRIPT = re.search(r"UI scripts failed: (\S+)", (FIXTURES / "browser-failed.log").read_text()).group(1)
# Made-up test ids live under a folder no checkout has.
OTHER = "made_up/test_x.py"
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


def comment_marker(head, queue, tests=QUARANTINE, flags="", failed=(RECRUITING, CRASHED)):
    """A comment's marker lines, written when `tests` were quarantined and naming `failed`."""
    key = keeper.quarantine_key(failed, tests)
    return (
        f"<!-- stale-pr-keeper:tests {json.dumps(list(failed))} -->\n"
        f"<!-- stale-pr-keeper:comment {head} queue={queue} quarantine={key}{flags} -->"
    )


def requeued_marker(head, queue, tests=QUARANTINE, failed=(RECRUITING, CRASHED)):
    return comment_marker(head, queue, tests, " requeued", failed)


class FakeGitHub:
    def __init__(
        self,
        comments=None,
        fail=(),
        can_enqueue=True,
        status="completed",
        stderr="GraphQL: Pull request is not mergeable ({})",
        limited=(),
    ):
        self.pages = json.loads((FIXTURES / "open-prs.json").read_text())
        # {number: [body, ...]}; comment ids are number * 1000 + position
        self.existing = {n: [(n * 1000 + i, b) for i, b in enumerate(bodies)] for n, bodies in (comments or {}).items()}
        self.fail = set(fail)
        self.can_enqueue = can_enqueue
        self.status = status
        self.stderr = stderr
        self.limited = set(limited)  # calls refused for the rate limit
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
        if call[0] in self.limited:
            raise keeper.RateLimited(1, ["gh"], stderr="HTTP 403: API rate limit exceeded")
        if call[0] in self.fail:
            raise subprocess.CalledProcessError(1, ["gh"], stderr=self.stderr.format(call[0]))

    def enqueue(self, pr):
        self._write("enqueue", pr.number, pr.head)

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


def test_a_crash_pytest_never_summarised_still_names_its_test():
    lines = [
        line for line in log_lines("worker-crashed.log") if "short test summary" not in line and "FAILED" not in line
    ]
    tests = keeper.read_log(lines)[0]
    assert tests == [(CRASHED, True), ("no pytest summary", False)]
    assert not keeper.flaky([finding(*tests)], set())  # what else failed is unknown


def test_a_failed_line_outside_the_summary_is_test_output_not_a_failure():
    lines = ["2026-11-08T20:00:00.0Z FAILED made_up/test_x.py::test_printed - from a test's own output\n"]
    assert keeper.read_log(lines) == ([], [])


def test_the_browser_suite_names_its_failed_ui_scripts():
    assert keeper.read_log(log_lines("browser-failed.log")) == ([], [UI_SCRIPT])


def summary(*lines, results="1 failed, 3 passed in 1.0s"):
    header = "=========================== short test summary info ============================"
    return [f"{line}\n" for line in (header, *lines, results)]


def test_a_test_that_only_quotes_a_worker_crash_is_not_a_crash():
    quote = f"worker 'gw3' crashed while running '{CRASHED}'"
    tests, _ = keeper.read_log(summary(f"FAILED {CRASHED} - AssertionError: {quote}"))
    assert tests == [(CRASHED, False)]
    other = f"{OTHER}::test_y"
    assert keeper.read_log(summary(f"FAILED {other} - {quote}"))[0] == [(other, False)]  # another test's crash
    assert keeper.read_log([f"E   AssertionError: {quote}\n"])[0] == []  # test output, not xdist's line


@pytest.mark.parametrize("test", [f"{OTHER}::test_y[can't]", f"{OTHER}::test_y[a\\b]"])
def test_a_worker_crash_names_its_test_as_repr_writes_it(test):
    line = f"FAILED {test} - worker 'gw1' crashed while running {test!r}"
    assert keeper.read_log(summary(line))[0] == [(test, True)]
    assert keeper.read_log([f"worker 'gw1' crashed while running {test!r}\n"])[0][0] == (test, True)


def test_a_log_that_ends_inside_the_summary_is_never_a_flake():
    lines = summary(f"FAILED {RECRUITING} - x")[:-1]  # no result line: the log was cut off
    tests, _ = keeper.read_log(lines)
    assert tests == [(RECRUITING, False), ("pytest's summary, cut off", False)]
    assert not keeper.flaky([finding(*tests)], QUARANTINE)


def test_a_parameter_id_holding_a_dash_is_read_whole():
    test = f"{OTHER}::test_split[a - b]"
    assert keeper.read_log(summary(f"FAILED {test} - AssertionError: x - y"))[0] == [(test, False)]
    assert keeper.read_log(summary(f"FAILED {test}"))[0] == [(test, False)]


def test_a_coloured_summary_still_reads():
    lines = [
        "\x1b[31m=========================== short test summary info ============================\x1b[0m\n",
        f"\x1b[31mFAILED\x1b[0m {RECRUITING} - AssertionError\n",
        "\x1b[31m1 failed\x1b[0m, 3 passed in 1.0s\n",
    ]
    assert keeper.read_log(lines)[0] == [(RECRUITING, False)]


def test_a_collection_error_in_the_summary_is_named_and_is_never_a_crash():
    tests, _ = keeper.read_log(
        summary(f"FAILED {CRASHED} - worker 'gw3' crashed while running '{CRASHED}'", f"ERROR {OTHER}")
    )
    assert tests == [(CRASHED, True), (OTHER, False)]
    assert not keeper.flaky([finding(*tests)], set())


def test_an_internalerror_is_named_and_is_never_a_crash():
    crash = f"FAILED {CRASHED} - worker 'gw3' crashed while running '{CRASHED}'"
    lines = [*summary(crash), "INTERNALERROR> Traceback\n", "INTERNALERROR> x\n"]
    tests, _ = keeper.read_log(lines)
    assert tests == [(CRASHED, True), ("pytest INTERNALERROR", False)]


def test_failures_the_summary_does_not_list_are_named():
    tests, _ = keeper.read_log(summary(f"FAILED {RECRUITING} - x", results="2 failed, 1 error, 3 passed in 1.0s"))
    assert tests == [(RECRUITING, False), ("2 failure(s) the summary does not list", False)]
    assert not keeper.flaky([finding(*tests)], QUARANTINE)


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


def attempt(jobs, number, **changes):
    jobs = copy.deepcopy(jobs)
    for job in jobs:
        job["run_attempt"] = number
        job.update(changes)
    return jobs


def test_a_failure_in_any_attempt_counts_though_a_later_rerun_passed():
    first = attempt(jobs_with("python-5"), 1)
    rerun = attempt([j for j in first if j["name"] == "python-5"], 2, conclusion="success", steps=[])
    assert [(job["name"], job["run_attempt"]) for job, _ in keeper.failed_jobs(first + rerun)] == [("python-5", 1)]


def test_a_partial_rerun_keeps_the_failures_it_did_not_rerun():
    # Attempt 1: a browser leg and python-5 failed. Attempt 2 reran python-5 only, which failed again.
    first = attempt(jobs_with("python-5"), 1)
    first += attempt([j for j in first if j["name"] == "python-5"], 1, name="browser-1", id=99)
    rerun = attempt([j for j in first if j["name"] == "python-5"], 2, id=100)
    failed = [(job["name"], job["run_attempt"]) for job, _ in keeper.failed_jobs(first + rerun)]
    assert failed == [("python-5", 1), ("browser-1", 1), ("python-5", 2)]


def test_a_rerun_that_failed_only_on_flakes_again_is_still_re_enqueued():
    gh = FakeGitHub()
    real = gh.jobs
    gh.jobs = lambda run_id: (
        attempt(real(run_id), 1) + [{**job, "id": f"{job['id']}-2"} for job in attempt(real(run_id), 2)]
    )
    run(gh)
    calls = written_to(gh, 2984)
    assert [c[0] for c in calls] == ["comment", "enqueue"]
    assert "[python-5 (attempt 2)]" in calls[0][2]


@pytest.mark.parametrize(
    ("job", "step", "requeued"),
    [
        ("caddy-linux-amd64", "Actual pinned Linux Caddy runtime and lifecycle", True),  # pytest alone
        ("lint", "Python lint", False),  # any failed step's log is read for names
    ],
)
def test_any_failed_step_names_its_tests_and_only_pytest_steps_can_be_flakes(job, step, requeued):
    gh = FakeGitHub()
    gh.jobs = lambda run_id: [{**j, "id": f"{run_id}:{j['id']}"} for j in jobs_with(job, failing_step=step)]
    run(gh)
    calls = written_to(gh, 2984)
    assert [c[0] for c in calls] == (["comment", "enqueue"] if requeued else ["comment"])
    assert f"`{RECRUITING}` (quarantined) in [{job}]" in calls[0][2]


def test_a_job_that_timed_out_after_a_failed_step_counts_both():
    (job,) = [j for j in jobs_with("python-5") if j["name"] == "python-5"]
    job["conclusion"] = "timed_out"
    assert [step for _, step in keeper.failed_jobs([job])] == ["Run selected tests", "timed out"]


def test_every_failed_step_of_a_job_counts():
    (job,) = [j for j in jobs_with("python-5") if j["name"] == "python-5"]
    job["steps"].append({"name": "Upload test results", "conclusion": "failure"})
    assert [step for _, step in keeper.failed_jobs([job])] == ["Run selected tests", "Upload test results"]
    gh = FakeGitHub()
    gh.jobs = lambda run_id: [{**job, "id": f"{run_id}:{job['id']}"}]
    run(gh)
    assert [c[0] for c in written_to(gh, 2984)] == ["comment"]  # a quarantined test, but the upload failed too
    assert sum(1 for c in gh.calls if c[0] == "log" and c[1].startswith(sha("1"))) == 1


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
        ([finding((f"{OTHER}::test_y", False))], False),
        ([finding((RECRUITING, False)), finding((f"{OTHER}::test_y", False))], False),
        ([finding()], False),  # a failed step that named no test
        ([finding((CRASHED, True), log_missing=True)], False),
        ([finding(step="Shell scripts (shellcheck, pinned)")], False),
        ([finding(scripts=[UI_SCRIPT])], False),
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
    for test in (RECRUITING, CRASHED):
        (tmp_path / test.split("::")[0]).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / test.split("::")[0]).write_text("")
    (folder / keeper.entry_name(RECRUITING)).write_text(json.dumps(entry(RECRUITING)))
    (folder / "misnamed.json").write_text(json.dumps(entry(CRASHED)))
    (folder / keeper.entry_name(CRASHED)).write_text(json.dumps(entry(CRASHED, runs=[])))  # no run url
    gone = "made_up/test_gone.py::test_x"
    (folder / keeper.entry_name(gone)).write_text(json.dumps(entry(gone)))  # its test file is gone
    (folder / "broken.json").write_text("{not json")
    assert keeper.quarantined(tmp_path) == {RECRUITING}
    assert keeper.quarantined(tmp_path / "missing") == set()


def test_entry_names_match_flake_quarantine():
    digest = hashlib.sha256(b"a/b.py::test_x[1-2]").hexdigest()[:10]
    assert keeper.entry_name("a/b.py::test_x[1-2]") == f"a-b.py-test_x-1-2-{digest}.json"


def test_the_quarantine_uses_flake_quarantine_load_when_the_checkout_has_it(tmp_path):
    script = tmp_path / keeper.QUARANTINE_SCRIPT
    script.parent.mkdir()
    script.write_text("def load(root):\n    return {'from/flake_quarantine.py::test_load': {'file': str(root)}}\n")
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
    assert requeued_marker(sha("a"), sha("1"), failed=(RECRUITING,)) in calls[0][2]
    assert f"`{RECRUITING}` (quarantined)" in calls[0][2]
    assert calls[1] == ("enqueue", 2984, sha("a"))  # expectedHeadOid: a newer push is not queued
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
    assert not keeper.markers([(1, call[2])])[0].requeued


def test_a_real_failure_gets_one_comment_naming_the_test_and_no_enqueue():
    gh = FakeGitHub()
    run(gh, tests=set())
    calls = written_to(gh, 3034)
    assert [c[0] for c in calls] == ["comment"]
    body = calls[0][2]
    assert f"`{RECRUITING}` in [python-8]" in body
    assert "a check failed" in body
    assert comment_marker(sha("c"), sha("3"), set(), failed=(RECRUITING,)) in body


def test_a_ui_script_failure_names_the_script():
    gh = FakeGitHub()
    run(gh)
    (call,) = written_to(gh, 3033)
    assert call[0] == "comment" and f"`{UI_SCRIPT}` (UI script) in [browser-1]" in call[2]


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


def test_a_test_quarantined_after_the_comment_gets_its_head_re_enqueued_in_the_same_comment():
    # #2966 was commented on before the recruiting test was quarantined.
    gh = FakeGitHub(comments={2966: [comment_marker(sha("c2"), sha("7a"), set())]})
    run(gh)
    assert [c[0] for c in written_to(gh, 2966)] == ["enqueue"]
    (edit,) = gh.writes("edit_comment")
    assert edit[1] == 2966000 and requeued_marker(sha("c2"), sha("7a"), failed=(RECRUITING,)) in edit[2]
    assert gh.calls.index(edit) < gh.calls.index(("enqueue", 2966, sha("c2")))  # the marker first


def test_a_refused_enqueue_puts_the_existing_comment_back():
    old = comment_marker(sha("c2"), sha("7a"), set())
    gh = FakeGitHub(comments={2966: [old]}, fail={"enqueue"})
    _, ok = run(gh)
    assert not ok
    assert [c[1:] for c in gh.writes("edit_comment") if c[1] == 2966000][-1] == (2966000, old)


def test_a_new_queue_run_of_the_same_head_updates_the_comment_instead_of_adding_one():
    gh = FakeGitHub(comments={2966: [comment_marker(sha("c2"), sha("55"), set())]})
    text, _ = run(gh, tests=set())
    assert written_to(gh, 2966) == []
    (edit,) = gh.writes("edit_comment")
    assert edit[1] == 2966000 and comment_marker(sha("c2"), sha("7a"), set(), failed=(RECRUITING,)) in edit[2]
    assert "#2966 Mail from the source: updated" in text


def test_a_head_dropped_again_after_its_requeue_is_diagnosed_in_the_same_comment_and_not_re_enqueued():
    # #2953 was re-enqueued after queue run 0; the queue dropped it again (8a), a crash again.
    gh = FakeGitHub(comments={2953: [requeued_marker(sha("d3"), sha("0"))]})
    run(gh)
    assert written_to(gh, 2953) == []
    (edit,) = gh.writes("edit_comment")
    assert edit[1] == 2953000 and "Already re-queued once for this commit" in edit[2]
    assert f"`{CRASHED}` (worker crashed)" in edit[2]
    (marker,) = keeper.markers([(1, edit[2])])
    assert marker.requeued and marker.queue == sha("8a")


def test_a_requeued_head_is_never_re_enqueued_again_when_the_quarantine_changes():
    gh = FakeGitHub(comments={2966: [requeued_marker(sha("c2"), sha("7a"), set())]})
    run(gh)
    assert written_to(gh, 2966) == []
    (edit,) = gh.writes("edit_comment")
    assert "Already re-queued once for this commit" in edit[2]


def test_without_a_queue_token_the_flake_comment_is_marked_deferred_and_requeued_once_a_token_is_added():
    first = FakeGitHub(can_enqueue=False)
    run(first)
    (call,) = written_to(first, 2984)
    assert keeper.markers([(1, call[2])])[0].deferred
    again = FakeGitHub(comments={2984: [call[2]]}, can_enqueue=False)
    run(again)
    assert written_to(again, 2984) == [] and again.writes("edit_comment") == []
    later = FakeGitHub(comments={2984: [call[2]]})
    run(later)
    assert [c[0] for c in written_to(later, 2984)] == ["enqueue"]
    (edit,) = later.writes("edit_comment")
    marker = keeper.markers([(1, edit[2])])[0]
    assert marker.requeued and not marker.deferred


def test_a_quarantine_change_that_touches_none_of_a_comments_tests_costs_no_reads():
    gh = FakeGitHub(comments={2966: [comment_marker(sha("c2"), sha("7a"))]})
    run(gh, tests=QUARANTINE | {"made_up/test_other.py::test_z"})
    assert written_to(gh, 2966) == [] and not any(c[0] == "edit_comment" and c[1] == 2966000 for c in gh.calls)
    assert ("queue_run", sha("7a")) not in gh.calls


def test_a_comment_naming_tests_with_an_html_comment_end_still_reads():
    test = f"{OTHER}::test_y[-->]"
    pr = keeper.parse_prs(json.loads((FIXTURES / "open-prs.json").read_text()))[0]
    body = keeper.comment_body(pr, [finding((test, False))], {test}, None)
    assert "-->]" not in body.split("<!-- stale-pr-keeper:tests")[1]
    (marker,) = keeper.markers([(1, body)])
    assert marker.tests == [test] and marker.quarantine == keeper.quarantine_key([test], {test})


def with_mergeable(gh, number, state):
    for page in gh.pages:
        for node in page["data"]["repository"]["pullRequests"]["nodes"]:
            if node["number"] == number:
                node["mergeable"] = state
    return gh


def test_a_conflicting_flake_is_re_enqueued_once_the_conflict_is_gone():
    first = with_mergeable(FakeGitHub(), 2984, "CONFLICTING")
    run(first)
    (call,) = written_to(first, 2984)
    assert "once it no longer conflicts with main" in call[2] and keeper.markers([(1, call[2])])[0].deferred
    still = with_mergeable(FakeGitHub(comments={2984: [call[2]]}), 2984, "CONFLICTING")
    run(still)
    assert written_to(still, 2984) == [] and still.writes("edit_comment") == []
    clean = FakeGitHub(comments={2984: [call[2]]})
    run(clean)
    assert [c[0] for c in written_to(clean, 2984)] == ["enqueue"]


def test_huge_test_ids_keep_the_comment_small_and_are_never_a_flake():
    many = [(f"{OTHER}::test_y[{i}{'p' * 1000}]", True) for i in range(200)]
    findings = [finding(*many, job=f"python-{n}") for n in range(1, 6)]
    pr = keeper.parse_prs(json.loads((FIXTURES / "open-prs.json").read_text()))[0]
    body = keeper.comment_body(pr, findings, set(), "https://x")
    assert len(body) < 40_000
    (marker,) = keeper.markers([(1, body)])
    assert 0 < len(marker.tests) < 200 and marker.tests == [t for t, _ in many[: len(marker.tests)]]
    assert not keeper.flaky(findings, set())  # every one a crash, but too many to be flakes


def with_reason(gh, number, reason):
    for page in gh.pages:
        for node in page["data"]["repository"]["pullRequests"]["nodes"]:
            if node["number"] == number:
                node["timelineItems"]["nodes"][-1]["reason"] = reason
    return gh


def test_a_timed_out_removal_names_its_failing_tests_and_is_never_re_enqueued():
    gh = with_reason(FakeGitHub(), 2984, "checks_timed_out")
    run(gh)
    (call,) = written_to(gh, 2984)
    assert call[0] == "comment" and "its checks timed out" in call[2]
    assert f"`{RECRUITING}` (quarantined) in [python-5]" in call[2]


def test_ids_holding_an_html_comment_end_stay_inside_the_budget():
    many = [(f"{OTHER}::test_y[{i}{'-->' * 160}]", False) for i in range(40)]
    findings = [finding(*many[n * 8 : n * 8 + 8], job=f"python-{n}") for n in range(5)]
    pr = keeper.parse_prs(json.loads((FIXTURES / "open-prs.json").read_text()))[0]
    body = keeper.comment_body(pr, findings, {t for t, _ in many}, "https://x")
    assert len(body) < 60_000
    assert not keeper.flaky(findings, {t for t, _ in many})  # quarantined, but not all fit the marker


def test_a_requeued_head_whose_comment_is_current_is_done():
    gh = FakeGitHub(comments={2953: [requeued_marker(sha("d3"), sha("8a"))]})
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
    assert "#2984 requeued: GraphQL: Pull request is not mergeable (enqueue)" in text
    assert ("delete_comment", 777) in gh.calls
    assert [c[0] for c in written_to(gh, 3060)] == ["comment", "enqueue"]  # later pull requests were still handled
    assert [c[0] for c in written_to(gh, 3033)] == ["comment"]


@pytest.mark.parametrize(
    "stderr",
    [
        "Post https://api.github.com/graphql: net/http: TLS handshake timeout",
        "unexpected end of JSON input",
        "",
        "GraphQL: Something went wrong while executing your query. (enqueuePullRequest)",
    ],
)
def test_an_enqueue_whose_outcome_is_unknown_keeps_its_marker(stderr):
    gh = FakeGitHub(fail={"enqueue"}, stderr=stderr)
    _, ok = run(gh)
    assert not ok and ("enqueue", 2984, sha("a")) in gh.calls
    assert not any(c[0] == "delete_comment" for c in gh.calls)


def test_a_rate_limited_enqueue_keeps_its_marker_and_stops_the_run():
    gh = FakeGitHub(limited={"enqueue"})
    text, ok = run(gh)
    assert not ok and not any(c[0] == "delete_comment" for c in gh.calls)
    assert written_to(gh, 3060) == [] and "#3060: not read (rate limit)" in text


def test_a_rate_limit_while_taking_a_refused_marker_back_stops_the_run():
    gh = FakeGitHub(fail={"enqueue"}, limited={"delete_comment"})
    text, ok = run(gh)
    assert not ok and "#2984 requeued: HTTP 403: API rate limit exceeded" in text
    assert written_to(gh, 3060) == [] and "#3060: not read (rate limit)" in text


def test_a_rate_limit_while_labelling_stops_the_run_before_the_comments_are_read():
    gh = FakeGitHub(limited={"add_label"})
    text, ok = run(gh)
    assert not ok and gh.writes("remove_label") == []
    assert not any(c[0] == "comments" for c in gh.calls)
    assert "#2984: not read (rate limit)" in text


def test_add_label_does_not_try_to_create_the_label_under_the_rate_limit(monkeypatch):
    calls = []

    def limited(*args, **_):
        calls.append(args)
        raise keeper.RateLimited(1, ["gh"], stderr="API rate limit exceeded")

    gh = keeper.GitHub("ticoteam/tico")
    monkeypatch.setattr(gh, "_gh", limited)
    with pytest.raises(keeper.RateLimited):
        gh.add_label(1, "needs-main-merge")
    assert len(calls) == 1


def test_the_rate_limit_stops_the_run_at_once(monkeypatch):
    gh = FakeGitHub()
    real = gh.read_job_log

    def limited(job_id):
        if job_id.startswith(sha("2")):
            raise keeper.RateLimited(1, ["gh"], stderr="HTTP 403: API rate limit exceeded")
        return real(job_id)

    monkeypatch.setattr(gh, "read_job_log", limited)
    text, ok = run(gh)
    assert not ok
    assert [c[0] for c in written_to(gh, 2984)] == ["comment", "enqueue"]  # before the limit
    for number in (3060, 3034, 3033, 2966, 2953):
        assert written_to(gh, number) == [], number
    assert "#3034: not read (rate limit)" in text


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
    assert prs[2984].node_id == "PR_kwDO2984"
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
    nodes = [{"databaseId": number * 1000 + i, "viewerDidAuthor": True, "body": b} for i, b in enumerate(bodies)]
    return {f"pr{number}": {"comments": {"pageInfo": info, "nodes": nodes}}}


def test_comments_page_back_until_the_heads_marker_is_found(monkeypatch):
    head = sha("a")
    replies = [
        {"data": {"repository": {**comments_page(1, ["chatter"] * 100, previous="c1"), **comments_page(2, ["x"])}}},
        {"data": {"repository": comments_page(1, ["chatter"] * 99 + [comment_marker(head, sha("1"))], "c2")}},
    ]
    seen = []

    def fake_run(args, **_):
        seen.append(args)
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps(replies[len(seen) - 1]))

    monkeypatch.setattr(keeper.subprocess, "run", fake_run)
    out = keeper.GitHub("ticoteam/tico").comments({1: head, 2: sha("b")})
    assert len(seen) == 2  # the second page had the marker: the third is never read
    assert 'before: "c1"' in seen[1][4]
    assert len(out[1]) == 200 and len(keeper.markers(out[1])) == 1
    assert out[2] == [(2000, "x")]


def test_only_the_keepers_own_comments_count(monkeypatch):
    head = sha("a")
    page = comments_page(1, [requeued_marker(head, sha("1")), "mine"])
    page["pr1"]["comments"]["nodes"][0]["viewerDidAuthor"] = False  # pasted by someone else
    monkeypatch.setattr(keeper.subprocess, "run", completed(json.dumps({"data": {"repository": page}})))
    assert keeper.GitHub("ticoteam/tico").comments({1: head}) == {1: [(1001, "mine")]}


def test_a_rate_limited_call_raises_rate_limited(monkeypatch):
    def fake_run(args, **_):
        raise subprocess.CalledProcessError(1, args, stderr="gh: API rate limit exceeded for installation (HTTP 403)")

    monkeypatch.setattr(keeper.subprocess, "run", fake_run)
    with pytest.raises(keeper.RateLimited):
        keeper.GitHub("ticoteam/tico").api("rate_limit")


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
    fake_gh("gh: Resource not accessible by integration (HTTP 403)", 1)  # a permission problem is not an expired log
    with pytest.raises(subprocess.CalledProcessError, match="returned non-zero"):
        gh.read_job_log(1)
    fake_gh("gh: API rate limit exceeded (HTTP 403)", 1)
    with pytest.raises(keeper.RateLimited):
        gh.read_job_log(1)
    fake_gh("", 0)
    assert gh.read_job_log(1) == ([], [])


def test_comment_bodies_go_through_stdin(monkeypatch):
    seen = []

    def fake_run(args, input=None, **_):
        seen.append((args, input))
        return subprocess.CompletedProcess(args, 0, stdout='{"id": 5}')

    monkeypatch.setattr(keeper.subprocess, "run", fake_run)
    gh = keeper.GitHub("ticoteam/tico")
    assert gh.comment(12, "x" * 300_000) == 5
    gh.edit_comment(5, "body")
    assert all("--input" in args and all(len(a) < 200 for a in args) for args, _ in seen)
    assert json.loads(seen[0][1])["body"] == "x" * 300_000 and json.loads(seen[1][1]) == {"body": "body"}


def test_enqueue_uses_the_queue_token_and_the_inspected_head(monkeypatch):
    seen = {}
    reply = {"data": {"enqueuePullRequest": {"mergeQueueEntry": {"id": "MQE_1"}}}}

    def fake_run(args, env=None, **_):
        seen.update(args=args, token=env["GH_TOKEN"])
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps(reply))

    monkeypatch.setattr(keeper.subprocess, "run", fake_run)
    monkeypatch.setenv("GH_TOKEN", "workflow-token")
    gh = keeper.GitHub("ticoteam/tico", queue_token="app-token")
    assert gh.can_enqueue and not keeper.GitHub("ticoteam/tico").can_enqueue
    pr = keeper.PR(12, "t", "u", False, "main", sha("a"), "MERGEABLE", False, [], None, "SUCCESS", node_id="PR_12")
    gh.enqueue(pr)
    assert seen["args"][:3] == ["gh", "api", "graphql"] and "enqueuePullRequest" in seen["args"][4]
    assert "id=PR_12" in seen["args"] and f"head={sha('a')}" in seen["args"]  # never a newer push
    assert seen["token"] == "app-token"
    reply = {"data": {"enqueuePullRequest": {"mergeQueueEntry": None}}}
    with pytest.raises(subprocess.CalledProcessError) as raised:
        gh.enqueue(pr)
    assert keeper.REFUSED.search(raised.value.stderr)  # nothing queued: the marker is taken back


@pytest.mark.parametrize(
    ("label_stderr", "calls", "raises"),
    [
        ("gh: Not Found (HTTP 404)", 4, False),  # no such label: created (never --force), then added
        (None, 2, True),  # the label exists: the first error stands
        ("HTTP 502: Bad Gateway", 2, True),
    ],
)
def test_add_label_creates_the_label_only_when_it_is_missing(monkeypatch, label_stderr, calls, raises):
    seen = []

    def fake_gh(*args, **_):
        seen.append(args)
        if len(seen) == 1:
            raise subprocess.CalledProcessError(1, ["gh"], stderr="HTTP 502: Bad Gateway")
        if len(seen) == 2 and label_stderr:
            raise subprocess.CalledProcessError(1, ["gh"], stderr=label_stderr)
        return ""

    gh = keeper.GitHub("ticoteam/tico")
    monkeypatch.setattr(gh, "_gh", fake_gh)
    if raises:
        with pytest.raises(subprocess.CalledProcessError):
            gh.add_label(1, "needs-main-merge")
    else:
        gh.add_label(1, "needs-main-merge")
    assert len(seen) == calls and not any("--force" in args for args in seen)


def test_the_workflow_is_hourly_and_on_demand_and_keeps_the_queue_token_apart():
    import yaml

    workflow = yaml.safe_load((keeper.ROOT / ".github/workflows/stale-pr-keeper.yml").read_text())
    triggers = workflow[True]  # PyYAML reads the bare `on` key as True
    assert "workflow_dispatch" in triggers
    assert triggers["schedule"][0]["cron"].split()[1:] == ["*", "*", "*", "*"]
    assert workflow["permissions"] == {
        "contents": "read",
        "pull-requests": "write",
        "issues": "write",
        "actions": "read",
        "checks": "read",
        "statuses": "read",
    }
    (job,) = workflow["jobs"].values()
    (step,) = [s for s in job["steps"] if "scripts/stale_pr_keeper.py" in s.get("run", "")]
    assert step["env"]["GH_TOKEN"] == "${{ github.token }}"
    assert step["env"]["QUEUE_TOKEN"] == "${{ secrets.STALE_PR_KEEPER_TOKEN }}"
