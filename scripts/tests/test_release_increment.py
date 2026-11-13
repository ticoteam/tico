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
    "slow_only": {"backend/tests/test_slow.py"},
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
    assert len(full) == ri.FULL_PYTHON_SHARDS + 6
    assert full[0] == {"name": "product", "part": "product", "shard": "", "workers": "2", "runner": ri.RUNNER}
    assert full[5] == {
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
