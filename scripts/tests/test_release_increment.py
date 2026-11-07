"""The incremental release validation's decisions (scripts/release_increment.py), from recorded inputs: the anchor, the
red run's wait, the failures a red run carries, and the plan's VMs. The selection and the timings are given, not read;
the replay and RunsOn read the real ones."""

from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest

from scripts import pr_checks
from scripts import release_increment as ri

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
A, B, C = ("a" * 40, "b" * 40, "c" * 40)
TABLES = {
    "seconds": {"backend/tests/test_a.py": 30.0, "backend/tests/test_slow.py": 900.0},
    "browser": {"threads": 10.0, "task-code": 5.0},
    "slow": {"backend/tests/test_slow.py"},
}
PYTEST_FAILURES = (
    "FAILED backend/tests/test_company_file.py::test_one - AssertionError\n"
    "FAILED backend/tests/test_company_file.py::test_two\n"
    "FAIL  threads.cjs (4.1s)\n"
    "FAIL  ui/tests/task-code.cjs (2s)\n"
)


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


def test_the_anchor_is_the_newest_green_full_validation_of_the_last_day():
    records = [record(3, C, incremental=True), record(2, B, hours=3), record(1, A, hours=5)]
    assert ri.anchor_of(records, NOW, lambda sha: True)["id"] == 2
    assert ri.anchor_of(records, NOW, lambda sha: sha != B)["id"] == 1


def test_a_stale_red_running_or_uncontained_validation_is_no_anchor():
    assert ri.anchor_of([record(1, A, hours=25)], NOW, lambda sha: True) is None
    assert ri.anchor_of([record(1, A, conclusion="failure")], NOW, lambda sha: True) is None
    assert ri.anchor_of([record(1, A, status="in_progress")], NOW, lambda sha: True) is None
    assert ri.anchor_of([record(1, A)], NOW, lambda sha: False) is None


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
    named = ri.failed_tokens([("python-3", PYTEST_FAILURES), ("factory-sim-2", ""), ("release-check", "")])
    assert named == {
        "backend/tests/test_company_file.py",
        "ui/tests/threads.cjs",
        "ui/tests/task-code.cjs",
        ri.FACTORY,
    }
    assert ri.failed_tokens([("product", ""), ("desktop", "")]) == {"product", "desktop"}


def test_a_failure_nobody_can_name_is_unknown_so_any_commit_may_fix_it():
    assert ri.failed_tokens([("python-4", "Traceback: boom")]) is None
    assert ri.failed_tokens([("images (tico, amd64)", "")]) is None
    assert ri.failed_tokens([("native-linux (linux-x86_64)", "")]) is None


# ---- when a red validation runs again ----


def test_a_red_at_mains_head_waits_for_a_push():
    go, why = ri.red_rule(red({"backend/tests/test_x.py"}, sha=C), C, set())
    assert go is False and "next push to main" in why


def test_a_red_waits_until_a_commit_since_it_selects_what_failed():
    go, why = ri.red_rule(red({"backend/tests/test_x.py"}), C, {"backend/tests/test_y.py"})
    assert go is False and "the window waits" in why and "test_x.py" in why
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


def test_a_carried_factory_failure_runs_the_factory_sim():
    assert plan_of(selected(), carried={ri.FACTORY}, smoke=(), gone=())["factory"] is True


def test_a_plan_too_big_for_a_dispatch_input_is_refused():
    assert ri.plan_json({"jobs": [{"name": "python-1", "files": ["x" * 70000]}]}) is None
    assert json.loads(ri.plan_json({"jobs": []})) == {"jobs": []}


def test_selection_tokens_name_what_the_selection_runs():
    sel = selected(python=["backend/tests/test_a.py"], browser=["threads"], contracts=True)
    tokens = ri.selection_tokens(sel, ["app/x.rs"])
    assert {"backend/tests/test_a.py", "ui/tests/threads.cjs", ri.DESKTOP} <= tokens
    assert set(pr_checks.CONTRACT_TESTS) <= tokens
    assert "ui/tests/sign-in-redirect.cjs" not in tokens


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
    assert len(full) == 34
    assert full[0] == {"name": "product", "part": "product", "shard": "", "workers": "2", "runner": ri.RUNNER}
    assert full[5] == {"name": "python-1", "part": "python", "shard": "1/28", "workers": "4", "runner": ri.RUNNER}
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
