"""A test that fails and passes on the same commit is flaky. The daily detector lists each one with its commit and runs
and goes red, so it is fixed at its root cause the same day or deleted. Nothing quarantines a test."""

import io
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
import zipfile

import pytest
import yaml

from scripts import flake_detect as detector

ROOT = Path(__file__).resolve().parents[2]
REPO = "ticoteam/tico"


def log(*lines, commit="b" * 40):
    stamp = "2026-11-07T07:11:20.9819248Z "
    head = [
        "﻿" + stamp + "Current runner version: '2.338.2'",
        stamp + "[command]/usr/bin/git log -1 --format=%H",
        stamp + commit,
    ]
    return "\n".join(head + [stamp + line for line in lines]) + "\n"


def test_failed_tests_are_read_from_the_pytest_summary_of_a_job_log():
    text = log(
        "=========================== short test summary info ============================",
        "\x1b[31mFAILED\x1b[0m fixture_tests/test_a.py::test_one - AssertionError: assert 1 == 2",
        "FAILED fixture_tests/test_b.py::test_two[case a-1] - KeyError: 'x'",
        "ERROR fixture_tests/test_c.py::test_three - RuntimeError: fixture",
        "ERROR fixture_tests/test_d.py - ImportError: no module",  # a collection error names no test
        "FAILED fixture_tests/test_e.py::TestKind::test_four",
        "FAILED fixture_tests/test_f.py::test_five[foo - bar] - AssertionError: x[1] - y",
        "FAILED fixture_tests/test_g.py::test_six[a] - assert b[0] == 1",
        "some FAILED text that is not a summary line",
        "\x1b[31m===== 4 failed, 10 passed in 3.2s =====\x1b[0m",
    )
    assert detector.failed_tests(text) == [
        "fixture_tests/test_a.py::test_one",
        "fixture_tests/test_b.py::test_two[case a-1]",
        "fixture_tests/test_c.py::test_three",
        "fixture_tests/test_e.py::TestKind::test_four",
        "fixture_tests/test_f.py::test_five[foo - bar]",
        "fixture_tests/test_g.py::test_six[a]",
    ]
    assert detector.tested_commit(text) == "b" * 40
    assert detector.tested_commit("no checkout here\n") is None


def observation(key, passed=(), failed=(), crowded=(), url="https://github.com/r/actions/runs/1/attempts/1", run=1):
    """`passed`: the node ids a passing leg's JUnit report shows as passed; `failed`: tests that failed in its leg python-1."""
    legs = []
    if passed:
        legs.append(
            {"id": f"{url}:ok", "name": "python-2", "conclusion": "success", "url": url + "/ok", "passed": set(passed)}
        )
    if failed:
        legs.append({"id": f"{url}:bad", "name": "python-1", "conclusion": "failure", "url": url + "/job"})
    return {
        "key": key,
        "run": run,
        "plan": None,
        "url": url,
        "created": "2026-11-07",
        "legs": legs,
        "failed": {test: {"url": url + "/job", "leg": "python-1"} for test in failed},
        "crowded": list(crowded),
    }


def test_a_test_that_fails_and_passes_on_the_same_commit_is_a_flip():
    observed = [
        observation("c1", failed=["a.py::test_flaky", "a.py::test_broken"], url="u1"),
        observation("c1", passed=["a.py::test_flaky"], url="u2"),  # the same test passed in another attempt
        observation("c2", failed=["a.py::test_broken"], url="u3"),  # failed only: a real failure
        observation("c2", failed=["a.py::test_broken"], url="u4"),
        observation("c3", failed=["a.py::test_branch"], url="u5"),
        observation("c4", passed=["a.py::test_flaky"], url="u6"),  # a pass on another commit proves nothing
        observation("c5", failed=["b.py::test_alone"], url="u7"),
        observation("c5", passed=["c.py::test_x"], failed=["d.py::test_other"], url="u8"),  # b.py never passed again
        observation("c6", failed=["e.py::test_new"], url="u9"),
        observation("c6", passed=["e.py::test_new"], failed=["f.py::test_new"], url="u10"),
    ]
    found = detector.flips(observed)
    # a.py::test_broken failed twice and never passed; a passing sibling in a.py proves nothing about it.
    assert set(found) == {"a.py::test_flaky", "e.py::test_new"}
    assert found["a.py::test_flaky"] == {
        "commit": "c1",
        "failed": ["u1/job"],
        "passed": ["u2"],
        "first_seen": "2026-11-07",
    }


def test_a_job_with_many_failures_is_a_breakage_not_flakes():
    many = [f"a.py::test_{n}" for n in range(detector.MAX_FAILURES_PER_JOB + 1)]
    observations = [observation("c1", failed=many), observation("c1", passed=[many[0]], url="u2")]
    assert detector.flips(observations)  # flips() trusts its input: the cap is applied when logs are read
    gh = FakeGitHub(
        runs=[run(1, attempts=2)],
        jobs={
            (1, 1): [job(11, "python-1", "failure")],
            (1, 2): [job(21, "python-1", "success")],
        },
        logs={11: log(*[f"FAILED {test} - boom" for test in many])},
    )
    found = detector.observations(gh, REPO, gh.runs)
    assert found[0]["failed"] == {} and found[0]["crowded"] == ["https://github.com/job/11"]


def run(ident, attempts=1, event="merge_group", sha="c" * 40, conclusion="failure", pr_head=None):
    return {
        "id": ident,
        "run_attempt": attempts,
        "event": event,
        "head_sha": pr_head or sha,
        "conclusion": conclusion,
        "created_at": "2026-11-07T07:00:00Z",
        "html_url": f"https://github.com/{REPO}/actions/runs/{ident}",
    }


def job(ident, name, conclusion):
    return {"id": ident, "name": name, "conclusion": conclusion, "html_url": f"https://github.com/job/{ident}"}


def flips_of(gh):
    return detector.flips(detector.observations(gh, REPO, gh.runs))


def junit_for(*cases):
    """A JUnit report like pytest's: each case is (node id, None to pass, "skipped", or "failed")."""
    body = ""
    for nodeid, outcome in cases:
        path, _, name = nodeid.partition("::")
        child = {None: "", "skipped": "<skipped/>", "failed": "<failure/>"}[outcome]
        body += f'<testcase classname="{path[:-3].replace("/", ".")}" name="{name}">{child}</testcase>'
    return f'<?xml version="1.0"?><testsuites><testsuite name="pytest">{body}</testsuite></testsuites>'


class FakeGitHub:
    """`xml` maps a job id to its JUnit report text; a job with no entry has no report."""

    def __init__(self, runs, jobs, logs, xml=None):
        self.runs, self.jobs, self.logs, self.xml, self.calls = runs, jobs, logs, xml or {}, []

    def api(self, path):
        self.calls.append(path)
        parts = path.split("?")[0].split("/")
        run_id, attempt = int(parts[parts.index("runs") + 1]), int(parts[parts.index("attempts") + 1])
        return {"jobs": self.jobs.get((run_id, attempt), [])}

    def log(self, job_id):
        self.calls.append(f"log {job_id}")
        return self.logs.get(job_id)

    def results(self, run_id, leg):
        self.calls.append(f"results {leg['id']}")
        text = self.xml.get(leg["id"])
        return None if text is None else detector.passed_tests(text)


def artifact_zip(files):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return data.getvalue()


def test_a_queue_retry_artifact_lists_its_pytest_and_browser_flakes_with_commit_and_run(monkeypatch):
    flaky = "scripts/tests/test_flake_detector.py::test_a_node_id_comes_from_its_junit_classname_and_name"
    commit = "c" * 40
    retry_run = run(7, conclusion="success")
    archive = artifact_zip(
        {
            "flaky.json": json.dumps(
                [
                    {"suite": "python-1", "tests": [flaky], "commit": commit},
                    {"suite": "browser-1", "tests": ["chat.cts"], "commit": commit},
                ]
            )
        }
    )
    artifact = {
        "expired": False,
        "created_at": "2026-11-07T08:00:00Z",
        "workflow_run": {"id": retry_run["id"]},
        "archive_download_url": "https://api.github.com/artifacts/7/zip",
    }
    listing = f"repos/{REPO}/actions/artifacts?name=queue-flaky&per_page=100"
    calls = []

    def fake(command, **kwargs):
        calls.append(command)
        path = command[-1]
        if path == listing:
            old = {**artifact, "created_at": "2026-11-06T08:00:00Z", "archive_download_url": "old"}
            expired = {**artifact, "expired": True, "archive_download_url": "expired"}
            pages = [{"artifacts": [old, expired]}, {"artifacts": [artifact]}]
            return SimpleNamespace(returncode=0, stdout=json.dumps(pages))
        if "/workflows/" in path:
            runs = [retry_run] if "created=2026-11-07" in path else []
            return SimpleNamespace(returncode=0, stdout=json.dumps({"workflow_runs": runs}))
        assert path == artifact["archive_download_url"]
        return SimpleNamespace(returncode=0, stdout=archive)

    monkeypatch.setattr(detector.subprocess, "run", fake)
    report, found = detector.detect(detector.GitHub(REPO), REPO, days=1, today="2026-11-08")
    assert [command for command in calls if command[-1] == listing] == [["gh", "api", "--paginate", "--slurp", listing]]
    assert [command[-1] for command in calls if "/workflows/" not in command[-1]] == [
        listing,
        artifact["archive_download_url"],
    ]
    url = retry_run["html_url"]
    assert f"- `{flaky}` on {commit[:10]}: failed in {url}; passed in {url}" in report.splitlines()
    assert f"- `chat.cts` (browser script) on {commit[:10]}: failed in {url}; passed in {url}" in report.splitlines()
    assert "2 flaky test(s)" in report and "root cause today, or delete it" in report
    assert found[flaky] == {"commit": commit, "failed": [url], "passed": [url], "first_seen": "2026-11-07"}


def test_a_leg_artifact_counts_only_the_first_junit_report_as_ordinary_pass_evidence(monkeypatch):
    flaky, steady = "fixture_tests/test_x.py::test_flaky", "fixture_tests/test_x.py::test_steady"
    archive = artifact_zip(
        {
            "python-1.xml": junit_for((flaky, "failed"), (steady, None)),
            "python-1-retry.xml": junit_for((flaky, None)),
        }
    )
    gh = detector.GitHub(REPO)
    artifact = {"created_at": "2026-11-07T07:01:00Z", "archive_download_url": "https://api.github.com/artifacts/8/zip"}
    monkeypatch.setattr(gh, "api", lambda path: {"artifacts": [artifact]})
    monkeypatch.setattr(
        detector.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=archive)
    )
    leg = {"name": "python-1", "started_at": "2026-11-07T07:00:00Z", "completed_at": "2026-11-07T07:02:00Z"}
    assert gh.results(7, leg) == {steady}


def test_a_rerun_attempt_that_passes_flips_the_test_its_first_attempt_failed():
    flaky = "fixture_tests/test_flaky_fixture_c.py::test_the_runner_lists_exactly_the_command_paths"
    gh = FakeGitHub(
        runs=[run(1, attempts=2)],
        jobs={
            # "Re-run failed jobs": the plan job carries over (one id), so python-1 ran the same tests again.
            (1, 1): [job(10, "plan", "success"), job(11, "python-1", "failure"), job(12, "python-2", "cancelled")],
            (1, 2): [job(10, "plan", "success"), job(21, "python-1", "success"), job(22, "python-2", "success")],
        },
        logs={11: log(f"FAILED {flaky} - AssertionError")},
        xml={21: junit_for((flaky, None)), 22: junit_for()},
    )
    found = flips_of(gh)
    assert list(found) == [flaky] and found[flaky]["commit"] == "c" * 40
    assert found[flaky]["passed"] == [f"https://github.com/{REPO}/actions/runs/1/attempts/2"]


class UnreadableGitHub(FakeGitHub):
    """A FakeGitHub whose jobs listing fails, with gh's message, for the (run, attempt) pairs in `broken`."""

    def __init__(self, *args, broken, **kwargs):
        super().__init__(*args, **kwargs)
        self.broken = broken

    def api(self, path):
        parts = path.split("?")[0].split("/")
        key = (int(parts[parts.index("runs") + 1]), int(parts[parts.index("attempts") + 1]))
        if key in self.broken:
            self.calls.append(path)
            raise detector.GhError(path, self.broken[key])
        return super().api(path)


def test_one_unreadable_attempt_is_skipped_with_a_warning_and_a_systemic_failure_still_fails(monkeypatch, capsys):
    """Nov 17: one run's jobs listing failed and the uncaught error killed the daily detector, so no flake was reported
    after it. One unreadable attempt is now skipped and named; auth or rate limit, or every attempt failing, still fails."""
    flaky = "scripts/tests/test_flake_detector.py::test_a_node_id_comes_from_its_junit_classname_and_name"
    runs = [run(1, attempts=2), run(2, attempts=2, sha="d" * 40)]
    jobs = {
        (1, 1): [job(10, "plan", "success"), job(11, "python-1", "failure")],
        (1, 2): [job(10, "plan", "success"), job(21, "python-1", "success")],
    }
    kwargs = {"logs": {11: log(f"FAILED {flaky} - AssertionError")}, "xml": {21: junit_for((flaky, None))}}
    monkeypatch.setattr(detector, "runs_since", lambda gh, *a: gh.runs)
    monkeypatch.setattr(detector, "_add_retry_flips", lambda *a: None)

    gh = UnreadableGitHub(runs, jobs, broken={(2, 1): "gh: Not Found (HTTP 404)"}, **kwargs)
    text, found = detector.detect(gh, REPO, 1, "2026-11-07")
    assert list(found) == [flaky]  # run 1's flip is still found
    assert f"Skipped 1 run attempt(s)" in text and "runs/2/attempts/1" in text and "HTTP 404" in text
    assert "warning: skipped" in capsys.readouterr().err

    gh = UnreadableGitHub(runs, jobs, broken={(2, 1): "gh: Bad credentials (HTTP 401)"}, **kwargs)
    with pytest.raises(detector.GhError, match="HTTP 401"):
        detector.detect(gh, REPO, 1, "2026-11-07")

    every = {(run_id, attempt): "HTTP 502" for run_id in (1, 2) for attempt in (1, 2)}
    gh = UnreadableGitHub(runs, jobs, broken=every, **kwargs)
    with pytest.raises(detector.GhError, match="all 4 failed"):
        detector.detect(gh, REPO, 1, "2026-11-07")

    # The command line turns a systemic failure into exit 1 with gh's message, not a traceback.
    monkeypatch.setattr(detector, "GitHub", lambda repo, cache: UnreadableGitHub(runs, jobs, broken=every, **kwargs))
    assert detector.main(["detect", "--today", "2026-11-07"]) == 1
    assert "error: gh api" in capsys.readouterr().err


def test_gh_api_raises_gh_error_with_ghs_message(monkeypatch):
    failed = subprocess.CompletedProcess([], 1, stdout="", stderr="gh: Not Found (HTTP 404)\n")
    monkeypatch.setattr(detector.subprocess, "run", lambda *a, **k: failed)
    with pytest.raises(detector.GhError, match="HTTP 404") as error:
        detector.GitHub(REPO).api("repos/x/actions/runs/1/attempts/1/jobs")
    assert not error.value.systemic
    assert detector.GhError("p", "API rate limit exceeded (HTTP 403)").systemic


def test_a_passing_testcase_in_a_leg_that_failed_on_another_test_still_proves_it():
    """A leg fails on test B; its report still shows test A passed, and A's own testcase is the evidence."""
    flaky = "fixture_tests/test_flaky_fixture_c.py::test_flaky"
    other = "fixture_tests/test_flaky_fixture_c.py::test_still_failing"
    gh = FakeGitHub(
        runs=[run(1, attempts=2)],
        jobs={
            (1, 1): [job(10, "plan", "success"), job(11, "python-1", "failure")],
            (1, 2): [job(20, "plan", "success"), job(21, "python-1", "failure")],
        },
        logs={11: log(f"FAILED {flaky} - AssertionError"), 21: log(f"FAILED {other} - AssertionError")},
        xml={21: junit_for((flaky, None), (other, "failed"))},
    )
    found = flips_of(gh)
    assert list(found) == [flaky]
    assert found[flaky]["passed"] == [f"https://github.com/{REPO}/actions/runs/1/attempts/2"]


def test_a_rerun_passes_only_the_tests_its_report_shows_as_passed():
    """A failed test passed only when a leg's own JUnit report shows that exact test as passed."""
    flaky = "fixture_tests/test_flaky_fixture_d.py::test_wire"
    other = "fixture_tests/test_other.py::test_other"
    single = FakeGitHub(
        runs=[run(1, attempts=2)],
        jobs={
            (1, 1): [job(10, "plan", "success"), job(11, "python-1", "failure"), job(12, "python-2", "failure")],
            # Only python-2 was re-run; python-1 carries over, still failed.
            (1, 2): [job(10, "plan", "success"), job(11, "python-1", "failure"), job(22, "python-2", "success")],
        },
        logs={
            11: log(f"FAILED {flaky} - AssertionError"),
            12: log(f"FAILED {other} - AssertionError"),
        },
        xml={22: junit_for((other, None))},
    )
    assert list(flips_of(single)) == [other]
    rerun_all = {
        (1, 1): [job(10, "plan", "success"), job(11, "python-1", "failure"), job(12, "python-2", "success")],
        (1, 2): [job(20, "plan", "success"), job(21, "python-1", "success"), job(22, "python-2", "success")],
    }
    failing = log(f"FAILED {flaky} - AssertionError")
    no_reports = FakeGitHub(runs=[run(1, attempts=2)], jobs=rerun_all, logs={11: failing})
    assert flips_of(no_reports) == {}  # no report from any leg: no test is proven passing
    sibling = FakeGitHub(
        runs=[run(1, attempts=2)],
        jobs=rerun_all,
        logs={11: failing},
        xml={21: junit_for(("fixture_tests/test_a.py::test_a", None))},
    )
    assert flips_of(sibling) == {}  # a passing file's other test is not this one
    listed = FakeGitHub(
        runs=[run(1, attempts=2)],
        jobs=rerun_all,
        logs={11: failing},
        xml={21: junit_for(("fixture_tests/test_a.py::test_a", None)), 22: junit_for((flaky, None))},
    )
    assert list(flips_of(listed)) == [flaky]
    assert flips_of(listed)[flaky]["passed"] == [f"https://github.com/{REPO}/actions/runs/1/attempts/2"]


def test_a_skipped_testcase_is_no_evidence():
    flaky = "fixture_tests/test_flaky_fixture_c.py::test_skip"
    gh = FakeGitHub(
        runs=[run(1, attempts=2)],
        jobs={
            (1, 1): [job(10, "plan", "success"), job(11, "python-1", "failure")],
            (1, 2): [job(20, "plan", "success"), job(21, "python-1", "success")],
        },
        logs={11: log(f"FAILED {flaky} - AssertionError")},
        xml={21: junit_for((flaky, "skipped"))},
    )
    assert flips_of(gh) == {}


def test_a_passing_sibling_in_the_same_file_is_no_evidence_and_the_passing_testcase_is():
    flaky = "fixture_tests/test_flaky_fixture_c.py::test_flaky"
    sibling = "fixture_tests/test_flaky_fixture_c.py::test_sibling"

    def attempt(report):
        return FakeGitHub(
            runs=[run(1, attempts=2)],
            jobs={
                (1, 1): [job(10, "plan", "success"), job(11, "python-1", "failure")],
                (1, 2): [job(20, "plan", "success"), job(21, "python-1", "success")],
            },
            logs={11: log(f"FAILED {flaky} - AssertionError")},
            xml={21: report},
        )

    assert flips_of(attempt(junit_for((sibling, None)))) == {}
    assert list(flips_of(attempt(junit_for((sibling, None), (flaky, None))))) == [flaky]


def test_a_pull_request_run_is_keyed_by_the_merge_commit_it_tested_not_the_branch_head():
    """A pull_request run tests the branch merged into main as main was then: two runs of one branch head test two
    commits once main moved, so a failure then a pass across them is not a flip unless the merge commit matches."""
    flaky = "fixture_tests/test_flaky_fixture_b.py::test_every_remote_tool_answers"
    head = "d" * 40
    gh = FakeGitHub(
        runs=[
            run(1, event="pull_request", pr_head=head),
            run(2, event="pull_request", pr_head=head, conclusion="success"),
            run(3, event="pull_request", pr_head=head, conclusion="success"),
        ],
        jobs={
            (1, 1): [job(11, "contracts", "failure")],
            (2, 1): [job(21, "contracts", "success")],
            (3, 1): [job(31, "contracts", "success")],
        },
        logs={
            11: log(f"FAILED {flaky} - TimeoutError", commit="1" * 40),
            21: log(commit="2" * 40),  # main moved: another commit
            31: log(commit="1" * 40),  # the same merge commit
        },
        xml={21: junit_for((flaky, None)), 31: junit_for((flaky, None))},
    )
    found = flips_of(gh)
    assert list(found) == [flaky] and found[flaky]["commit"] == "1" * 40
    assert found[flaky]["passed"] == [f"https://github.com/{REPO}/actions/runs/3/attempts/1"]


def test_a_node_id_comes_from_its_junit_classname_and_name():
    assert detector.nodeid_of("fixture_tests.test_x", "test_a[1-2]") == "fixture_tests/test_x.py::test_a[1-2]"
    assert (
        detector.nodeid_of("fixture_tests.test_x.TestKind", "test_four")
        == "fixture_tests/test_x.py::TestKind::test_four"
    )


def test_no_workflow_or_conftest_quarantines_a_test():
    """A flaky test is fixed or deleted, never run as an xfail: no --quarantine flag and no ci/quarantine/ list."""
    assert not (ROOT / "ci/quarantine").exists()
    for path in [*sorted((ROOT / ".github/workflows").glob("*.yml")), ROOT / "conftest.py", ROOT / "pytest.ini"]:
        text = path.read_text()
        assert "--quarantine" not in text and "ci/quarantine" not in text, path.name
    assert detector.TEST_JOB.fullmatch("caddy-linux-amd64") and detector.TEST_JOB.fullmatch("caddy-linux-arm64")


def test_only_runs_that_can_flip_are_read():
    """A run is read when a pass could share its commit: a rerun, or another run with the same head sha."""
    runs = [
        run(1, attempts=2),  # a rerun
        run(2, sha="e" * 40),
        run(3, sha="e" * 40, conclusion="success"),  # a second queue group on the same commit
        run(4, sha="f" * 40),  # alone: a failure with no pass partner
        run(5, event="pull_request", pr_head="9" * 40, conclusion="success"),
        run(6, event="pull_request", pr_head="9" * 40, conclusion="success"),  # two passes and no failure
    ]
    assert [r["id"] for r in detector.candidates(runs)] == [1, 2, 3]


def test_the_flaky_tests_workflow_detects_daily_reads_only_and_goes_red_on_a_flake():
    doc = yaml.safe_load((ROOT / ".github/workflows/flaky-tests.yml").read_text())
    assert [c["cron"] for c in doc[True]["schedule"]] == ["23 6 * * *"] and "workflow_dispatch" in doc[True]
    assert doc["permissions"] == {"contents": "read", "actions": "read"}
    assert list(doc["jobs"]) == ["detect"]
    steps = json.dumps(doc["jobs"]["detect"]["steps"])
    assert "flake_detect.py detect" in steps and "GITHUB_STEP_SUMMARY" in steps and "pipefail" in steps
    assert "upload-artifact" not in steps  # a plain list in the summary, nothing to download


def test_the_detector_exits_1_when_it_finds_a_flake_and_0_when_it_finds_none(monkeypatch, capsys):
    flaky = "fixture_tests/test_flaky_fixture_c.py::test_flaky"
    gh = FakeGitHub(
        runs=[run(1, attempts=2)],
        jobs={
            (1, 1): [job(10, "plan", "success"), job(11, "python-1", "failure")],
            (1, 2): [job(10, "plan", "success"), job(21, "python-1", "success")],
        },
        logs={11: log(f"FAILED {flaky} - AssertionError")},
        xml={21: junit_for((flaky, None))},
    )
    monkeypatch.setattr(detector, "runs_since", lambda gh, *a: gh.runs)
    monkeypatch.setattr(detector, "_add_retry_flips", lambda *a: None)
    monkeypatch.setattr(detector, "GitHub", lambda repo, cache: gh)
    assert detector.main(["detect", "--today", "2026-11-07"]) == 1
    out = capsys.readouterr().out
    assert f"- `{flaky}` on {'c' * 10}: failed in https://github.com/job/11; passed in " in out
    assert f"https://github.com/{REPO}/actions/runs/1/attempts/2" in out
    gh.runs = []
    assert detector.main(["detect", "--today", "2026-11-07"]) == 0
    assert "No test failed and passed on the same commit." in capsys.readouterr().out


def test_a_slow_test_that_fails_every_time_is_not_proven_passing_by_a_python_leg():
    """pytest deselects the slow tests in python legs, so a python pass says nothing about a slow test's contracts failure."""
    slow = "fixture_tests/test_slow_fixture.py::test_every_call"
    gh = FakeGitHub(
        runs=[run(1, attempts=2)],
        jobs={
            (1, 1): [job(10, "plan", "success"), job(11, "contracts", "failure"), job(12, "python-1", "success")],
            (1, 2): [job(10, "plan", "success"), job(21, "contracts", "failure"), job(12, "python-1", "success")],
        },
        logs={
            11: log(f"FAILED {slow} - AssertionError", "Test files: fixture_tests/test_slow_fixture.py"),
            21: log(f"FAILED {slow} - AssertionError", "Test files: fixture_tests/test_slow_fixture.py"),
            12: log("Test files: fixture_tests/test_slow_fixture.py", "1 passed, 1 deselected"),
        },
    )
    assert flips_of(gh) == {}
