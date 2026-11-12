"""A test that fails and passes on the same commit is quarantined: PR checks and the merge queue run it without failing on
it, release validation still fails on it, and an entry left too long fails the weekly report, not the queue."""

import json
from pathlib import Path
import re
import subprocess
import sys
from types import SimpleNamespace

import pytest
import yaml

from scripts import flake_quarantine as quarantine

ROOT = Path(__file__).resolve().parents[2]
REPO = "ticoteam/tico"
TEST = "fixture_tests/test_quarantine_fixture_a.py::test_titles_draft_from_the_template"


def entry(test=TEST, first_seen="2026-11-01", **extra):
    return {
        "test": test,
        "first_seen": first_seen,
        "commit": "a" * 40,
        "runs": ["https://github.com/ticoteam/tico/actions/runs/1/job/2"],
        "ticket": "",
        "reason": "failed and passed on the same commit",
        **extra,
    }


def write(root, data, name=None):
    folder = root / quarantine.QUARANTINE
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (name or quarantine.entry_name(data["test"]))
    path.write_text(json.dumps(data, indent=2) + "\n")
    return path


def test_each_entry_is_its_own_file_named_from_its_test_id():
    name = quarantine.entry_name(TEST)
    assert name.endswith(".json") and "/" not in name and ":" not in name
    assert name.startswith("fixture_tests-test_quarantine_fixture_a.py-test_titles_draft_from_the_template-")
    # Two ids that sanitize alike still get two files, and a very long id stays a short name.
    assert quarantine.entry_name("a.py::t[x/y]") != quarantine.entry_name("a.py::t[x-y]")
    assert len(quarantine.entry_name("a.py::" + "t" * 500)) < 140
    assert quarantine.entry_name(TEST) == name


def test_the_repository_entries_are_valid():
    assert quarantine.problems(ROOT) == []


def test_a_malformed_misnamed_or_stale_entry_is_a_problem(tmp_path):
    (tmp_path / "fixture_tests").mkdir(parents=True)
    (tmp_path / "fixture_tests/test_quarantine_fixture_a.py").write_text(
        "def test_titles_draft_from_the_template(): pass\n"
    )
    assert quarantine.problems(tmp_path) == []  # no folder: nothing quarantined
    write(tmp_path, entry())
    assert quarantine.problems(tmp_path) == []
    assert set(quarantine.load(tmp_path)) == {TEST}
    write(tmp_path, entry(), name="renamed.json")
    write(tmp_path, entry(test="fixture_tests/test_gone.py::test_x"))
    write(tmp_path, {**entry(test="fixture_tests/test_quarantine_fixture_a.py::test_other"), "first_seen": "yesterday"})
    write(tmp_path, {**entry(test="fixture_tests/test_quarantine_fixture_a.py::test_more"), "runs": []})
    write(tmp_path, {**entry(test="fixture_tests/test_quarantine_fixture_a.py::test_extra"), "owner": "me"})
    write(tmp_path, {"test": "no-node-id"})
    (tmp_path / quarantine.QUARANTINE / "broken.json").write_text("{")
    found = "\n".join(quarantine.problems(tmp_path))
    assert "renamed.json" in found and "the name for" in found
    assert "test_gone.py" in found and "no such test file" in found
    assert "first_seen" in found and "runs" in found and "owner" in found
    assert "no-node-id" in found and "broken.json" in found
    # Only well-formed entries quarantine anything.
    assert set(quarantine.load(tmp_path)) == {TEST}


def test_an_entry_matches_its_test_and_every_parameter_of_it_and_nothing_else():
    tests = {TEST, "a.py::test_one[x]"}
    assert quarantine.matching(TEST, tests) == TEST
    assert quarantine.matching(TEST + "[case-1]", tests) == TEST
    assert quarantine.matching("a.py::test_one[x]", tests) == "a.py::test_one[x]"
    assert quarantine.matching("a.py::test_one[y]", tests) is None
    assert quarantine.matching(TEST + "_longer", tests) is None
    assert quarantine.matching("fixture_tests/test_quarantine_fixture_a.py::test_titles", tests) is None


def run_pytest(tmp_path, *args):
    """A tiny project whose conftest wires the quarantine the way the repository's root conftest does."""
    (tmp_path / "conftest.py").write_text(
        f"import sys\nsys.path.insert(0, {str(ROOT)!r})\n"
        "from scripts import flake_quarantine\n"
        "def pytest_addoption(parser):\n    flake_quarantine.addoption(parser)\n"
        "def pytest_collection_modifyitems(config, items):\n    flake_quarantine.modify(config, items)\n"
    )
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "test_flaky.py").write_text(
        "import pytest\n"
        "def test_flaky(): assert False\n"
        "@pytest.mark.parametrize('n', [1, 2])\n"
        "def test_param(n): assert n == 3\n"
        "def test_steady(): assert True\n"
    )
    write(tmp_path, entry(test="test_flaky.py::test_flaky"))
    write(tmp_path, entry(test="test_flaky.py::test_param"))
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:xdist", "-rxX", *args],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )


def test_pr_checks_run_a_quarantined_test_without_failing_on_it(tmp_path):
    result = run_pytest(tmp_path, "--quarantine", "-o", "junit_family=xunit1", "--junitxml=r.xml")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed, 3 xfailed" in result.stdout
    assert "quarantined: ci/quarantine/" in result.stdout
    # pr_checks.run reads the report: a quarantined test that ran is a skipped element of type pytest.xfail.
    report = (tmp_path / "r.xml").read_text()
    assert report.count('type="pytest.xfail"') == 3 and 'type="pytest.skip"' not in report


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
    assert quarantine.failed_tests(text) == [
        "fixture_tests/test_a.py::test_one",
        "fixture_tests/test_b.py::test_two[case a-1]",
        "fixture_tests/test_c.py::test_three",
        "fixture_tests/test_e.py::TestKind::test_four",
        "fixture_tests/test_f.py::test_five[foo - bar]",
        "fixture_tests/test_g.py::test_six[a]",
    ]
    assert quarantine.tested_commit(text) == "b" * 40
    assert quarantine.tested_commit("no checkout here\n") is None


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
    found = quarantine.flips(observed)
    # a.py::test_broken failed twice and never passed; a passing sibling in a.py proves nothing about it.
    assert set(found) == {"a.py::test_flaky", "e.py::test_new"}
    assert found["a.py::test_flaky"] == {
        "commit": "c1",
        "failed": ["u1/job"],
        "passed": ["u2"],
        "first_seen": "2026-11-07",
    }


def test_a_job_with_many_failures_is_a_breakage_not_flakes():
    many = [f"a.py::test_{n}" for n in range(quarantine.MAX_FAILURES_PER_JOB + 1)]
    observations = [observation("c1", failed=many), observation("c1", passed=[many[0]], url="u2")]
    assert quarantine.flips(observations)  # flips() trusts its input: the cap is applied when logs are read
    gh = FakeGitHub(
        runs=[run(1, attempts=2)],
        jobs={
            (1, 1): [job(11, "python-1", "failure")],
            (1, 2): [job(21, "python-1", "success")],
        },
        logs={11: log(*[f"FAILED {test} - boom" for test in many])},
    )
    found = quarantine.observations(gh, REPO, gh.runs)
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
    return quarantine.flips(quarantine.observations(gh, REPO, gh.runs))


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
        return None if text is None else quarantine.passed_tests(text)


def test_a_rerun_attempt_that_passes_flips_the_test_its_first_attempt_failed():
    flaky = "fixture_tests/test_quarantine_fixture_c.py::test_the_runner_lists_exactly_the_command_paths"
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


def test_a_passing_testcase_in_a_leg_that_failed_on_another_test_still_proves_it():
    """A leg fails on test B; its report still shows test A passed, and A's own testcase is the evidence."""
    flaky = "fixture_tests/test_quarantine_fixture_c.py::test_flaky"
    other = "fixture_tests/test_quarantine_fixture_c.py::test_still_failing"
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
    flaky = "fixture_tests/test_quarantine_fixture_d.py::test_wire"
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
    flaky = "fixture_tests/test_quarantine_fixture_c.py::test_skip"
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
    flaky = "fixture_tests/test_quarantine_fixture_c.py::test_flaky"
    sibling = "fixture_tests/test_quarantine_fixture_c.py::test_sibling"

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
    flaky = "fixture_tests/test_quarantine_fixture_b.py::test_every_remote_tool_answers"
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
    assert quarantine.nodeid_of("fixture_tests.test_x", "test_a[1-2]") == "fixture_tests/test_x.py::test_a[1-2]"
    assert (
        quarantine.nodeid_of("fixture_tests.test_x.TestKind", "test_four")
        == "fixture_tests/test_x.py::TestKind::test_four"
    )


def test_every_pytest_call_and_quarantine_leg_in_the_workflow_passes_quarantine():
    """A new pytest call or pr_checks leg in pr-checks.yml must pass --quarantine, or its failures block the queue."""
    workflow = yaml.safe_load((ROOT / ".github/workflows/pr-checks.yml").read_text())
    runs = [step.get("run", "") for job in workflow["jobs"].values() for step in job.get("steps", [])]
    calls = [text for text in runs if re.search(r"\bpytest\b", text)]
    assert calls  # the Caddy container test is one
    for text in calls:
        assert "--quarantine" in text, text
    legs = [text for text in runs if "pr_checks.py run" in text]
    assert legs and all("--quarantine" in text for text in legs)
    assert quarantine.TEST_JOB.fullmatch("caddy-linux-amd64") and quarantine.TEST_JOB.fullmatch("caddy-linux-arm64")


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
    assert [r["id"] for r in quarantine.candidates(runs)] == [1, 2, 3]


def test_a_proposal_skips_what_is_already_quarantined_and_writes_one_file_each(tmp_path):
    found = {
        TEST: {"commit": "c1", "failed": ["u1"], "passed": ["u2"], "first_seen": "2026-11-07"},
        TEST + "[x]": {"commit": "c1", "failed": ["u3"], "passed": ["u2"], "first_seen": "2026-11-07"},
        "fixture_tests/test_new.py::test_y": {
            "commit": "c2",
            "failed": ["u4"],
            "passed": ["u5"],
            "first_seen": "2026-11-06",
        },
    }
    # A flip in a test file that only a pull request branch has cannot be an entry on main: its file is not here.
    found["fixture_tests/test_branch_only.py::test_z"] = {**found[TEST], "commit": "c3"}
    (tmp_path / "fixture_tests").mkdir(parents=True)
    (tmp_path / "fixture_tests/test_new.py").write_text("def test_y(): pass\n")
    proposed = quarantine.propose(found, {TEST}, root=tmp_path)
    assert [p["test"] for p in proposed] == ["fixture_tests/test_new.py::test_y"]
    assert proposed[0] == {
        "test": "fixture_tests/test_new.py::test_y",
        "first_seen": "2026-11-06",
        "commit": "c2",
        "runs": ["u4", "u5"],
        "ticket": "",
        "reason": "failed and passed on the same commit",
    }
    written = quarantine.write_entries(proposed, tmp_path / "out")
    assert [p.name for p in written] == [quarantine.entry_name("fixture_tests/test_new.py::test_y")]
    assert json.loads(written[0].read_text()) == proposed[0]


def test_the_weekly_report_fails_on_an_old_entry_and_on_a_problem_never_on_a_fresh_one():
    entries = {
        TEST: entry(first_seen="2026-11-06", ticket="https://github.com/ticoteam/tico/issues/1"),
        "a.py::test_old": entry(test="a.py::test_old", first_seen="2026-10-20"),
    }
    text, failing = quarantine.report(entries, [], today="2026-11-08", max_age=14)
    assert failing and "a.py::test_old" in text and "19 days" in text and "no ticket" in text
    fresh = {TEST: entries[TEST]}
    text, failing = quarantine.report(fresh, [], today="2026-11-08", max_age=14)
    assert not failing and "2 days" in text
    assert quarantine.report({}, ["ci/quarantine/x.json: bad"], today="2026-11-08", max_age=14)[1]
    assert quarantine.report({}, [], today="2026-11-08", max_age=14) == ("No tests are quarantined.\n", False)


def test_the_flaky_tests_workflow_detects_daily_reports_weekly_and_reads_only():
    doc = yaml.safe_load((ROOT / ".github/workflows/flaky-tests.yml").read_text())
    crons = [c["cron"] for c in doc[True]["schedule"]]
    assert len(crons) == 2 and "workflow_dispatch" in doc[True]
    assert doc["permissions"] == {"contents": "read", "actions": "read"}
    detect, report = doc["jobs"]["detect"], doc["jobs"]["report"]
    assert crons[0] in detect["if"] and crons[1] in report["if"]
    assert "flake_quarantine.py detect" in json.dumps(detect["steps"])
    assert "flake_quarantine.py report" in json.dumps(report["steps"])
    # The report fails its own job: nothing in PR checks or the queue reads it.
    pr = (ROOT / ".github/workflows/pr-checks.yml").read_text()
    assert "flaky-tests" not in pr and "flake_quarantine.py report" not in pr


def test_without_the_flag_a_quarantined_test_still_fails(tmp_path):
    result = run_pytest(tmp_path)
    assert result.returncode == 1 and "3 failed, 1 passed" in result.stdout


def test_the_root_conftest_wires_the_quarantine(request, tmp_path):
    """The repository's conftest registers --quarantine and marks quarantined items only when it is passed."""
    assert request.config.getoption("--quarantine") in (True, False)  # registered; CI runs this file with it on
    plugin = request.config.pluginmanager.get_plugin(str(ROOT / "conftest.py"))
    assert plugin is not None and hasattr(plugin, "pytest_collection_modifyitems")
    (tmp_path / "x.py").write_text("def test_a(): pass\n")
    write(tmp_path, entry(test="x.py::test_a"))
    marked = []

    def item(nodeid):
        return SimpleNamespace(nodeid=nodeid, add_marker=lambda mark: marked.append((nodeid, mark)))

    def config(on):
        return SimpleNamespace(getoption=lambda name: on, rootpath=tmp_path)

    plugin.pytest_collection_modifyitems(config(False), [item("x.py::test_a")])
    assert marked == []
    plugin.pytest_collection_modifyitems(config(True), [item("x.py::test_a"), item("x.py::test_b")])
    assert [nodeid for nodeid, _ in marked] == ["x.py::test_a"]
    mark = marked[0][1]
    assert mark.name == "xfail" and mark.kwargs["strict"] is False and mark.kwargs.get("run", True)


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
