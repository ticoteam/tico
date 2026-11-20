"""The incremental release validation's decisions (scripts/release_increment.py), from recorded inputs: the anchor, the
red run's wait, the failures a red run carries, and the plan's VMs. The selection and the timings are given, not read;
the replay and RunsOn read the real ones."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

from scripts import pr_checks
from scripts import release_increment as ri

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
A, B, C = ("a" * 40, "b" * 40, "c" * 40)
TABLES = {
    "seconds": {"backend/tests/test_a.py": 30.0, "backend/tests/test_slow.py": 900.0},
    "browser": {"threads": 10.0, "task-code": 5.0},
    "slow_only": {"backend/tests/test_slow.py"},
}
PYTEST_FAILURES = (
    "FAILED backend/tests/test_company_file.py::test_one - AssertionError\n"
    "FAILED backend/tests/test_company_file.py::test_two\n"
    "FAIL  threads.cjs (4.1s)\n"
    "FAIL  ui/tests/task-code.cjs (2s)\n"
)


def test_release_selection_skips_map_for_documentation_and_owned_baselines(monkeypatch):
    checkout = ri.Checkout()
    monkeypatch.setattr(checkout, "known", lambda sha: True)
    monkeypatch.setattr(ri.pr_checks, "script_changes", lambda *args, **kwargs: [])
    paths = ["README.md", "baselines/ruff/example.json"]
    monkeypatch.setattr(ri.pr_checks, "changed_files", lambda *args, **kwargs: paths)
    monkeypatch.setattr(ri.pr_checks, "DependencyMap", lambda root: pytest.fail("unnecessary dependency scan"))
    _, selected = checkout.selected(A, B)
    assert selected["python"] == sorted(pr_checks.OWNED_DATA["baselines/"])


def record(id, sha, *, conclusion="success", hours=1, incremental=False, status="completed"):
    return {
        "id": id,
        "sha": sha,
        "status": status,
        "conclusion": conclusion,
        "finished": NOW - timedelta(hours=hours),
        "url": "https://github.example/run/%d" % id,
        "incremental": incremental,
    }


def red(failed, sha=A, conclusion="failure"):
    return {"sha": sha, "url": "https://github.example/red", "conclusion": conclusion, "failed": failed}


def selected(python=(), browser=(), **flags):
    return {
        "python": list(python),
        "browser": list(browser),
        "rust": [],
        "conformance": False,
        "contracts": False,
        "caddy": False,
        "reason": "",
        "uncertain": [],
        **flags,
    }


def plan_of(
    sel, changed=("backend/tests/test_a.py",), carried=(), smoke=("sign-in-redirect",), gone=("gone",), **options
):
    return ri.plan_for(
        sel,
        list(changed),
        set(carried),
        lambda path: not any(word in path for word in gone),
        list(smoke),
        TABLES,
        **options,
    )


# ---- the anchor and the red run ----


def unnamed(record):
    return None  # a red validation whose failures no log names


def test_the_anchor_is_the_newest_green_validation_of_the_last_day_full_or_incremental():
    records = [record(3, C, incremental=True), record(2, B, hours=3), record(1, A, hours=5)]
    assert ri.anchor_of(records, NOW, lambda sha: True, unnamed)["id"] == 3
    assert ri.anchor_of(records, NOW, lambda sha: True, unnamed)["failed"] == set()
    assert ri.anchor_of(records, NOW, lambda sha: sha not in (B, C), unnamed)["id"] == 1


def test_an_incremental_anchor_chains_the_next_candidate_from_the_newest_green_incremental():
    # Full A (5 h ago), incremental B (3 h ago, green), incremental C (1 h ago, red, failures named): C anchors next.
    records = [
        record(3, C, conclusion="failure", incremental=True),
        record(2, B, hours=3, incremental=True),
        record(1, A, hours=5),
    ]
    anchor = ri.anchor_of(records, NOW, lambda sha: True, lambda item: {"backend/tests/test_a.py"})
    assert anchor["id"] == 3 and anchor["failed"] == {"backend/tests/test_a.py"}
    assert ri.full_within_day(records, NOW)["id"] == 1, "the day's full run is still the one that holds the day"


def test_a_red_that_fail_fast_cut_short_is_no_anchor_so_the_one_before_it_is():
    records = [record(3, C, conclusion="failure", incremental=True), record(2, B, hours=3, incremental=True)]
    named = lambda item: {"backend/tests/test_a.py"}  # noqa: E731
    assert ri.anchor_of(records, NOW, lambda sha: True, named, lambda item: item["id"] != 3)["id"] == 2
    assert ri.anchor_of(records, NOW, lambda sha: True, named)["id"] == 3


def test_the_anchor_is_the_newest_by_finish_time_not_by_list_order():
    records = [record(1, A, hours=3), record(2, B, hours=2)]  # oldest first
    assert ri.anchor_of(records, NOW, lambda sha: True, unnamed)["id"] == 2


def test_a_cancelled_or_unnamed_red_full_is_no_anchor_so_an_older_one_is():
    records = [
        record(3, C, conclusion="cancelled", hours=1),
        record(2, B, conclusion="failure", hours=2),
        record(1, A, hours=3),
    ]
    assert ri.anchor_of(records, NOW, lambda sha: True, unnamed)["id"] == 1
    assert ri.anchor_of(records, NOW, lambda sha: True, lambda item: {"x"})["id"] == 2


def test_full_within_day_is_the_newest_full_run_of_the_last_day_whatever_its_outcome():
    # The cap: a cancelled or unnamed-red full run counts too, so no second full run is dispatched on its account.
    assert ri.full_within_day([], NOW) is None
    records = [
        record(4, C, conclusion="cancelled", hours=1),
        record(3, B, conclusion="failure", hours=2),
        record(2, A, hours=3, incremental=True),
    ]
    assert ri.full_within_day(records, NOW)["id"] == 4
    assert ri.full_within_day([record(1, A, conclusion="failure", hours=23)], NOW)["id"] == 1


def test_full_within_day_ignores_stale_running_and_incremental_runs():
    assert ri.full_within_day([record(1, A, hours=25)], NOW) is None
    assert ri.full_within_day([record(1, A, conclusion="cancelled", status="in_progress", hours=0)], NOW) is None
    assert ri.full_within_day([record(1, A, hours=1, incremental=True)], NOW) is None


def test_a_stale_red_running_or_uncontained_validation_is_no_anchor():
    assert ri.anchor_of([record(1, A, hours=25)], NOW, lambda sha: True, unnamed) is None
    assert ri.anchor_of([record(1, A, conclusion="failure")], NOW, lambda sha: True, unnamed) is None
    assert ri.anchor_of([record(1, A, status="in_progress")], NOW, lambda sha: True, unnamed) is None
    assert ri.anchor_of([record(1, A)], NOW, lambda sha: False, unnamed) is None


def test_a_red_anchor_stays_open_until_a_green_validation_of_main_supersedes_it():
    anchor = record(2, B, conclusion="failure", hours=2)
    later_green = record(3, C, hours=1, incremental=True)
    assert ri.anchor_open([anchor], anchor, lambda sha: True) is True
    assert ri.anchor_open([later_green, anchor], anchor, lambda sha: True) is False
    assert ri.anchor_open([later_green, anchor], anchor, lambda sha: sha != C) is True
    assert ri.anchor_open([anchor], dict(anchor, conclusion="success"), lambda sha: True) is False
    assert ri.anchor_open([anchor], None, lambda sha: True) is False


def test_the_red_run_since_the_anchor_is_the_newest_red_newer_than_it():
    anchor = record(2, B, hours=3)
    records = [record(4, C, conclusion="failure"), record(3, B, conclusion="cancelled", hours=2), anchor]
    assert ri.red_since(records + [record(1, A, conclusion="failure", hours=9)], anchor, lambda sha: True)["id"] == 4
    assert ri.red_since([anchor, record(1, A, conclusion="failure")], anchor, lambda sha: True) is None
    assert ri.red_since(records, None, lambda sha: True)["id"] == 4


def test_a_green_validation_after_a_red_one_supersedes_it_whatever_its_release_or_kind():
    anchor = record(2, B, hours=3)
    # Red at C, then a green incremental run of C (an earlier release's): the red is superseded, nothing is carried.
    after_green = [record(4, C, incremental=True, hours=1), record(3, C, conclusion="failure", hours=2), anchor]
    assert ri.red_since(after_green, anchor, lambda sha: True) is None
    # The green run of a commit main no longer contains supersedes nothing: the red still stands.
    assert ri.red_since(after_green, anchor, lambda sha: sha != C)["id"] == 3
    # A red after that green one is the red that stands.
    again = [record(5, C, conclusion="failure", hours=0.5)] + after_green
    assert ri.red_since(again, anchor, lambda sha: True)["id"] == 5


def test_a_candidate_whose_previous_release_moved_from_the_anchors_runs_the_product_part():
    sel = selected(python=["backend/tests/test_a.py"])
    assert plan_of(sel)["product"] is False
    moved = plan_of(sel, previous_changed=True)
    assert moved["product"] is True
    assert [job["name"] for job in moved["jobs"] if job["part"] == "product"] == ["product"]


# ---- the failures a red run names ----


def test_failures_are_named_from_the_logs_and_by_their_job():
    assert ri.failure_tokens(PYTEST_FAILURES) == {
        "backend/tests/test_company_file.py",
        "ui/tests/threads.cjs",
        "ui/tests/task-code.cjs",
    }
    named = ri.failed_tokens([("python-3", PYTEST_FAILURES), ("release-check", "")])
    assert named == {
        "backend/tests/test_company_file.py",
        "ui/tests/threads.cjs",
        "ui/tests/task-code.cjs",
    }
    assert ri.failed_tokens([("product", ""), ("desktop", "")]) == {"product", "desktop"}


def test_a_failure_nobody_can_name_is_unknown_so_any_commit_may_fix_it():
    assert ri.failed_tokens([("python-4", "Traceback: boom")]) is None
    assert ri.failed_tokens([("images (tico, amd64)", "")]) is None
    assert ri.failed_tokens([("native-linux (linux-x86_64)", "")]) is None


# ---- a red that only its infrastructure failed ----

# The annotations of this morning's two reds, as GitHub recorded them (runs 44483934113 and 44490064278).
BUILDKIT_DENIED = (
    "Error response from daemon: pull access denied for moby/buildkit, repository does not exist or may require "
    "'docker login': denied: RBAC: access denied"
)
SPOT = "AWS interrupted the EC2 Spot instance running this job. RunsOn can retry it after the workflow run finishes."


# Receipts (hosted_release_check.py's run.json) as this morning's reports hold them: the Spot part's gate was still
# running when AWS took it; v0.6.87's product never started its gate (its report has no receipt); the v0.6.83 isolation
# race finished its gate with exit 1.
RUNNING = {"gate_started": True, "gate_state": "running", "success": False}
NOT_STARTED = {}
FAILED_GATE = {"gate_started": True, "gate_state": "finished", "gate_exit": 1, "success": False}
LOST = "The runner has received a shutdown signal. This can happen when the runner service is stopped."


def test_a_registry_pull_denial_and_the_product_part_waiting_on_its_images_are_infrastructure():
    # v0.6.87: one image build could not pull moby/buildkit from Docker Hub; product failed waiting for the images.
    parts = [
        ("images (tico-updater, arm64)", [BUILDKIT_DENIED], None),
        ("product", ["Process completed with exit code 1.", "An image build did not succeed."], NOT_STARTED),
        ("release-check", ["Process completed with exit code 1."], None),
    ]
    assert ri.infrastructure_red(parts) == [("images (tico-updater, arm64)", "registry pull denied", BUILDKIT_DENIED)]


def test_a_spot_interruption_of_a_gate_that_had_not_answered_is_infrastructure():
    # v0.6.89: AWS took the product part's Spot VM (exit 137) mid-gate; the train waited for a commit selecting "product".
    annotations = ["Process completed with exit code 1.", SPOT, "Process completed with exit code 137.", SPOT]
    assert ri.infrastructure_red([("product", annotations, RUNNING)]) == [("product", "Spot interrupted", SPOT)]
    # A gate that passed, or never started, before its machine went is infrastructure too.
    assert ri.infrastructure_red([("python-3", [SPOT], {"gate_state": "finished", "gate_exit": 0})])
    assert ri.infrastructure_red([("python-3", [SPOT], NOT_STARTED)])


@pytest.mark.parametrize(
    "message, cause",
    [
        ("toomanyrequests: You have reached your unauthenticated pull rate limit.", "registry rate limited"),
        ("Error: ... 429 toomanyrequests: retry-after: 60", "registry rate limited"),
        (
            "The self-hosted runner: runs-on-i-0abc lost communication with the server. Verify the machine is running.",
            "runner lost",
        ),
        ("The hosted runner: GitHub Actions 12 lost communication with the server.", "runner lost"),
        (LOST, "runner lost"),
    ],
)
def test_rate_limits_and_lost_runners_are_infrastructure(message, cause):
    assert ri.infrastructure_red([("python-3", [message], RUNNING)]) == [("python-3", cause, message)]


def test_a_test_that_failed_before_its_runner_was_lost_is_a_test_failure():
    # The gate finished with exit 1, then the runner went during the always-run log upload: both annotations show.
    assert ri.infrastructure_red([("python-3", ["Process completed with exit code 1.", LOST], FAILED_GATE)]) is None
    assert ri.infrastructure_red([("product", [SPOT], FAILED_GATE)]) is None
    # A gate that crashed (its receipt has an error) answered too.
    assert (
        ri.infrastructure_red([("product", [SPOT], {"gate_state": "running", "error": "source tree changed"})]) is None
    )
    # A report that cannot be read cannot show the gate had not answered: no retry.
    assert ri.infrastructure_red([("product", [SPOT], None)]) is None


def test_a_red_with_any_other_failure_is_not_infrastructure():
    # A test failure beside a lost VM is still a test failure: it is never run again until it passes (CLAUDE.md).
    other = ("python-2", ["Process completed with exit code 1."], FAILED_GATE)
    assert ri.infrastructure_red([("product", [SPOT], RUNNING), other]) is None
    assert ri.infrastructure_red([("python-2", ["Process completed with exit code 1."], RUNNING)]) is None
    # A part with nothing said is unknown, not infrastructure.
    assert ri.infrastructure_red([("contracts-1", [], RUNNING)]) is None
    assert ri.infrastructure_red([]) is None
    # "An image build did not succeed" excuses product only, and only when an image build failed on infrastructure.
    assert ri.infrastructure_red([("product", ["An image build did not succeed."], NOT_STARTED)]) is None
    assert (
        ri.infrastructure_red([("python-2", [SPOT], RUNNING), ("product", ["An image build did not succeed."], {})])
        is None
    )
    images = ("images (tico, amd64)", [BUILDKIT_DENIED], None)
    assert ri.infrastructure_red([images, ("python-2", ["An image build did not succeed."], NOT_STARTED)]) is None
    # A test that prints the words is not a registry: only annotations are read, and a test's own says it failed.
    assert ri.infrastructure_red([("python-2", ["FAILED test_registry.py::test_pull - pull access"], RUNNING)]) is None


# ---- when a red validation runs again ----


def test_a_red_at_mains_head_waits_for_a_push():
    go, why = ri.red_rule(red({"backend/tests/test_x.py"}, sha=C), C, set())
    assert go is False and "next push to main" in why


def test_a_red_waits_until_a_commit_since_it_selects_what_failed():
    go, why = ri.red_rule(red({"backend/tests/test_x.py"}), C, {"backend/tests/test_y.py"})
    assert go is False and "the train waits" in why and "test_x.py" in why
    go, why = ri.red_rule(red({"backend/tests/test_x.py"}), C, {"backend/tests/test_x.py", "ui/tests/threads.cjs"})
    assert go is True and "a commit since" in why and "test_x.py" in why


def test_a_red_naming_no_failure_or_with_an_unreadable_diff_runs_on_the_next_commit():
    assert ri.red_rule(red(None), C, set())[0] is True
    assert ri.red_rule(red(set()), C, set())[0] is True
    assert ri.red_rule(red({"backend/tests/test_x.py"}), C, None)[0] is True


# ---- the incremental plan ----


def test_a_plan_runs_its_selection_the_carried_failures_and_the_smoke_browser_scripts():
    sel = selected(
        python=["backend/tests/test_a.py", "backend/tests/test_gone.py", "backend/tests/test_slow.py"],
        browser=["threads"],
    )
    plan = plan_of(
        sel,
        carried={"ui/tests/task-code.cjs", "backend/tests/test_b.py", "backend/tests/test_gone2.py", ri.PRODUCT},
        gone=("gone",),
    )
    assert plan["python"] == ["backend/tests/test_a.py", "backend/tests/test_b.py"]
    assert plan["contracts"] == ["backend/tests/test_slow.py"]
    assert plan["browser"] == ["sign-in-redirect", "task-code", "threads"]
    assert plan["product"] is True and plan["desktop"] is False
    assert plan["dropped"] == ["backend/tests/test_gone2.py"]
    assert [job["name"] for job in plan["jobs"]] == ["python-1", "browser-1", "contracts", "product"]


def test_a_carried_file_with_slow_tests_runs_them_in_the_contracts_lane_too():
    plan = ri.plan_for(
        selected(python=["backend/tests/test_a.py"]),
        ["backend/tests/test_a.py"],
        {"backend/tests/test_mixed.py", "backend/tests/test_plain.py"},
        lambda path: True,
        [],
        TABLES,
        has_slow=lambda path: path == "backend/tests/test_mixed.py",
    )
    assert plan["python"] == ["backend/tests/test_a.py", "backend/tests/test_mixed.py", "backend/tests/test_plain.py"]
    assert plan["contracts"] == ["backend/tests/test_mixed.py"]
    assert plan["jobs"][-1]["name"] == "contracts" and plan["jobs"][-1]["files"] == ["backend/tests/test_mixed.py"]


def test_a_test_file_is_slow_only_when_it_marks_a_test_slow(tmp_path):
    slow_file = tmp_path / "test_mixed.py"
    slow_file.write_text("import pytest\n\n@pytest.mark.slow\ndef test_x():\n    pass\n")
    plain = tmp_path / "test_plain.py"
    plain.write_text("def test_y():\n    pass\n")
    assert ri.has_slow_tests(slow_file) is True
    assert ri.has_slow_tests(plain) is False
    assert ri.has_slow_tests(tmp_path / "missing.py") is False


def test_an_empty_selection_runs_the_smoke_browser_scripts_only():
    plan = plan_of(selected(), changed=["docs/readme.md"], gone=())
    assert plan["python"] == [] and plan["contracts"] == [] and plan["browser"] == ["sign-in-redirect"]
    assert [job["name"] for job in plan["jobs"]] == ["browser-1"]
    assert plan["product"] is False and plan["desktop"] is False


def test_an_app_change_runs_the_desktop_part_and_a_caddy_change_the_product_part():
    plan = plan_of(selected(caddy=True), changed=["app/src/main.rs"], smoke=(), gone=())
    assert plan["desktop"] is True and plan["product"] is True
    assert [job["name"] for job in plan["jobs"]] == ["product", "desktop"]


def test_the_product_and_desktop_vms_of_a_plan_place_the_release_binaries():
    # Both parts run native_binaries (hosted_release_check.py), so their VMs must wait for and place the binaries.
    plan = plan_of(selected(caddy=True), changed=["app/src/main.rs"], smoke=(), gone=())
    include = ri.matrix_of(json.dumps(plan))["include"]
    assert [entry["name"] for entry in include] == ["product", "desktop"]
    assert all("binaries" not in entry for entry in include)


def test_selection_tokens_name_what_the_selection_runs():
    sel = selected(python=["backend/tests/test_a.py"], browser=["threads"], contracts=True)
    tokens = ri.selection_tokens(sel, ["app/x.rs"])
    assert {"backend/tests/test_a.py", "ui/tests/threads.cjs", ri.DESKTOP} <= tokens
    assert set(pr_checks.CONTRACT_TESTS) <= tokens
    assert "ui/tests/sign-in-redirect.cjs" not in tokens


def test_a_fix_to_the_isolation_suite_selects_the_product_part():
    # v0.6.83: the isolation check went red and its fix (#4413) touched only runner_support/tests/isolation/fake_cli.py;
    # the train read no product token from it and waited for a commit that would never come.
    changed = ["runner_support/tests/isolation/fake_cli.py"]
    assert ri.PRODUCT in ri.selection_tokens(selected(), changed)
    assert ri.red_rule(
        {"sha": "a" * 40, "url": "u", "conclusion": "failure", "failed": {ri.PRODUCT}},
        "b" * 40,
        ri.selection_tokens(selected(), changed),
    )[0]
    assert plan_of(selected(), changed=changed, smoke=(), gone=())["product"] is True
    assert ri.PRODUCT not in ri.selection_tokens(selected(), ["runner_support/turns.py"])


def test_a_vm_of_an_incremental_plan_runs_its_own_files_through_the_queue_runner(tmp_path, monkeypatch):
    plan = plan_of(selected(python=["backend/tests/test_a.py"]), smoke=(), gone=())
    (tmp_path / "backend/tests").mkdir(parents=True)
    (tmp_path / "backend/tests/test_a.py").touch()
    (tmp_path / "ci-results").mkdir()
    (tmp_path / "ci-results/python-1.xml").write_text("<testsuite><testcase/></testsuite>")
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    calls = []
    monkeypatch.setattr(
        pr_checks.subprocess, "run", lambda command, **kw: calls.append(command) or SimpleNamespace(returncode=0)
    )
    assert pr_checks.run(path, "python-1", root=tmp_path) == 0
    assert calls[-1][-1] == "backend/tests/test_a.py"


# ---- the gate's VMs ----


def test_the_full_matrix_is_the_gate_and_an_incremental_plan_lists_only_its_vms():
    full = ri.matrix_of("")["include"]
    assert len(full) == ri.FULL_PYTHON_SHARDS + 2
    assert full[0] == {"name": "product", "part": "product", "shard": "", "workers": "2", "runner": ri.RUNNER}
    assert not [entry for entry in full if entry["part"] == "browser"]  # the old web app's browser scripts are gone
    assert full[1] == {
        "name": "python-1",
        "part": "python",
        "shard": f"1/{ri.FULL_PYTHON_SHARDS}",
        "workers": "4",
        "runner": ri.RUNNER,
    }
    assert full[-1]["name"] == "desktop"
    plan = plan_of(selected(python=["backend/tests/test_a.py"]), smoke=("sign-in-redirect",), gone=())
    include = ri.matrix_of(json.dumps(plan))["include"]
    assert [entry["name"] for entry in include] == ["python-1", "browser-1"]
    assert "files" not in include[0] and include[1]["binaries"] == "no"


@pytest.mark.parametrize(
    "text",
    [
        json.dumps({"jobs": []}),
        json.dumps({"jobs": [{"name": "../x", "part": "python"}]}),
        json.dumps({"jobs": [{"name": "python-1", "part": "shell"}]}),
    ],
)
def test_a_plan_naming_an_unknown_vm_is_refused(text):
    with pytest.raises(ValueError):
        ri.matrix_of(text)


def test_a_file_only_the_release_table_records_is_slow_only_when_every_test_is_slow(tmp_path):
    # The default table is recorded from older runs: a plain file can be missing from it, and a mixed file runs its slow
    # tests in the contracts lane and its plain tests in the python lane. Only an all-slow file is slow-only.
    tests = tmp_path / "backend" / "tests"
    tests.mkdir(parents=True)
    (tests / "test_plain.py").write_text("def test_a():\n    pass\n")
    (tests / "test_marked.py").write_text("import pytest\n\n@pytest.mark.slow\ndef test_b():\n    pass\n")
    (tests / "test_mixed.py").write_text(
        "import pytest\n\n@pytest.mark.slow\ndef test_b():\n    pass\n\ndef test_c():\n    pass\n"
    )
    table = tmp_path / "test-timings.json"
    table.write_text(
        json.dumps(
            {
                "default": {"backend/tests/test_known.py": 5.0},
                "release": {
                    "backend/tests/test_known.py": 5.0,
                    "backend/tests/test_plain.py": 40.0,
                    "backend/tests/test_marked.py": 300.0,
                    "backend/tests/test_mixed.py": 90.0,
                },
            }
        )
    )
    tables = ri.load_tables(table, tmp_path)
    assert tables["slow_only"] == {"backend/tests/test_marked.py"}
    assert tables["seconds"]["backend/tests/test_plain.py"] == 40.0


def test_a_string_or_a_parametrised_slow_case_is_not_a_slow_only_file(tmp_path):
    fixture = tmp_path / "test_fixture_strings.py"
    fixture.write_text('MARK = "@pytest.mark.slow"\n\ndef test_a():\n    assert MARK\n')
    param = tmp_path / "test_param.py"
    param.write_text(
        "import pytest\n\n@pytest.mark.parametrize('x', [1, pytest.param(2, marks=pytest.mark.slow)])\n"
        "def test_b(x):\n    pass\n"
    )
    assert ri.slow_marks(fixture) == (False, False)
    assert ri.slow_marks(param) == (True, False)


def test_a_large_contracts_lane_splits_into_timed_vms_and_a_small_one_keeps_its_name():
    files = [f"backend/tests/test_s{i}.py" for i in range(20)]
    tables = {"seconds": {name: 100.0 for name in files}, "browser": {}, "slow": set(files)}
    jobs = ri.build_jobs([], [], files, False, [], False, False, tables)
    names = [job["name"] for job in jobs]
    assert names == ["contracts-1", "contracts-2", "contracts-3"]  # 2000 one-worker seconds: three VMs of about 330 s
    ri.matrix_of(json.dumps({"jobs": jobs}))  # the gate accepts numbered contracts VMs
    assert sorted(sum((job["files"] for job in jobs), [])) == sorted(files)
    assert all(job["part"] == "python" for job in jobs)
    small = ri.build_jobs([], [], files[:2], False, [], False, False, tables)
    assert [job["name"] for job in small] == ["contracts"]


def test_only_the_python_vms_holding_native_test_files_wait_for_the_release_binaries():
    # ci.yml waits for native-linux on every VM without binaries "no"; as in the full matrix (shards 1-2), only the VMs
    # with a file that runs the runner or relay binaries wait, and those files are packed on their own VMs.
    files = [f"backend/tests/test_p{i}.py" for i in range(6)]
    native = {"runner_support/tests/test_engine.py"}
    tables = {"seconds": {name: 400.0 for name in [*files, *native]}, "browser": {}, "slow_only": set()}
    jobs = ri.build_jobs(
        sorted([*files, *native]), [], [], False, [], False, False, tables, native=lambda names: native & set(names)
    )
    waiting = [job for job in jobs if "binaries" not in job]
    assert [job["files"] for job in waiting] == [sorted(native)]
    assert all(job["binaries"] == "no" for job in jobs if job not in waiting)
    assert sorted(sum((job["files"] for job in jobs), [])) == sorted([*files, *native])  # the same tests run
    assert [job["name"] for job in jobs] == [f"python-{i}" for i in range(1, len(jobs) + 1)]
    include = ri.matrix_of(json.dumps({"jobs": jobs}))["include"]
    assert [entry.get("binaries", "") for entry in include] == [job.get("binaries", "") for job in jobs]


def test_a_contracts_vm_waits_for_the_binaries_only_when_it_holds_a_native_test_file():
    native = {"backend/tests/test_s0.py"}
    files = [f"backend/tests/test_s{i}.py" for i in range(20)]
    tables = {"seconds": {name: 100.0 for name in files}, "browser": {}, "slow_only": set()}
    jobs = ri.build_jobs([], [], files, False, [], False, False, tables, native=lambda names: native & set(names))
    assert [("binaries" not in job) for job in jobs] == [bool(native & set(job["files"])) for job in jobs]
    assert any(job.get("binaries") == "no" for job in jobs)


def test_a_plan_that_cannot_tell_native_files_keeps_every_python_vm_waiting_for_the_binaries():
    plan = plan_of(selected(python=["backend/tests/test_a.py"]), smoke=(), gone=())
    assert [job for job in plan["jobs"] if job["part"] == "python" and "binaries" in job] == []
    told = plan_of(selected(python=["backend/tests/test_a.py"]), smoke=(), gone=(), native=lambda names: set())
    assert [job["binaries"] for job in told["jobs"] if job["name"].startswith("python-")] == ["no"]


def test_the_planner_finds_native_test_files_in_the_candidates_checkout(tmp_path):
    tests = tmp_path / "backend/tests"
    tests.mkdir(parents=True)
    # Split so this file itself names no native binary (release_checks.NATIVE_TEST would make it a native test).
    (tests / "test_native.py").write_text('BIN = "runner-rs/target/' + 'release/tico-runner"\n')
    (tests / "test_plain.py").write_text("def test_a():\n    pass\n")
    names = ["backend/tests/test_native.py", "backend/tests/test_plain.py"]
    assert ri.native_test_files(names, tmp_path) == {"backend/tests/test_native.py"}


def test_the_planner_finds_native_files_with_only_the_files_ci_checks_out_beside_it(tmp_path):
    # ci.yml's candidate job checks the planner out sparsely (`validation-control`, read here from ci.yml), so
    # everything the planner imports must be in that list; a missing import fails every incremental validation at its
    # plan step while full-checkout tests stay green.
    root = Path(ri.__file__).resolve().parents[1]
    workflow = (root / ".github/workflows/ci.yml").read_text()
    listed = re.search(r"path: validation-control\n\s+sparse-checkout: \|\n((?:\s+scripts/\S+\n)+)", workflow)
    assert listed, "ci.yml's validation-control checkout no longer has a sparse list this test can read"
    control = tmp_path / "control"
    for name in listed.group(1).split():
        (control / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(root / name, control / name)
    control = control / "scripts"
    tests = tmp_path / "candidate/backend/tests"
    tests.mkdir(parents=True)
    (tests / "test_native.py").write_text('BIN = "runner-rs/target/' + 'release/tico-runner"\n')
    (tests / "test_plain.py").write_text("def test_a():\n    pass\n")
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]); import release_increment as ri; "
        "print(sorted(ri.native_test_files(['backend/tests/test_native.py', 'backend/tests/test_plain.py'], sys.argv[2])))"
    )
    done = subprocess.run(
        [sys.executable, "-I", "-c", code, str(control), str(tmp_path / "candidate")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "['backend/tests/test_native.py']"
