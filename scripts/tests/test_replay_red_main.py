"""Historical selection coverage and replay bookkeeping, without running the selected checks."""

from collections import Counter, defaultdict
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import replay_red_main as replay

ROOT = Path(__file__).resolve().parents[2]
# Failing tests deleted with the old web app (#4660): a case that names only these can no longer replay.
RETIRED_WITH_UI = (
    "ui/tests/",
    "scripts/tests/test_release_shots.py",
    "backend/tests/test_growth_page_fixtures.py",
)
# Case numbers in red_main_cases.json from the recorded PR replay.
PINNED_CAUGHT = {
    1,
    2,
    3,
    4,
    5,
    7,
    10,
    11,
    13,
    14,
    16,
    17,
    19,
    20,
    21,
    22,
    23,
    25,
    26,
    27,
    29,
    30,
    31,
    32,
    33,
    34,
    35,
    36,
    37,
    38,
    39,
    40,
    41,
    42,
    43,
    44,
    45,
    46,
    47,
    49,
    52,
    53,
    55,
    56,
    57,
    58,
    59,
    60,
}
# Case numbers whose failing checks must run in the light PR plan.
PINNED_CAUGHT_AT_PR = {
    1,
    2,
    3,
    4,
    5,
    7,
    10,
    11,
    13,
    14,
    16,
    17,
    19,
    20,
    21,
    22,
    25,
    26,
    27,
    29,
    30,
    31,
    32,
    33,
    34,
    35,
    36,
    37,
    38,
    39,
    40,
    41,
    42,
    43,
    44,
    45,
    46,
    47,
    49,
    52,
    53,
    55,
    56,
    57,
    58,
    59,
    60,
}


def small_replay(tmp_path):
    runner = replay.Replay.__new__(replay.Replay)
    runner.root = tmp_path
    runner.graph = SimpleNamespace(by_base=defaultdict(set))
    runner.definitions = defaultdict(set)
    return runner


def test_replay_follows_moved_files_and_catches_any_failing_check(tmp_path):
    runner = small_replay(tmp_path)
    path = tmp_path / "backend/tests/test_check.py"
    path.parent.mkdir(parents=True)
    path.touch()
    runner.graph.by_base["test_check.py"].add("backend/tests/test_check.py")
    runner.plan = lambda changed, event: {"python": ["backend/tests/test_check.py"], "contracts": False}
    case = dict(
        fix_pr=1,
        cause="dynamic_ref",
        changed_files=["backend/old/test_check.py"],
        failing_tests=["backend/old/test_check.py"],
    )
    result = runner.case(case, 1)
    assert result["caught"] and not result["stale"]
    assert result["moved"] == {"backend/old/test_check.py": "backend/tests/test_check.py"}
    case["failing_tests"].append("backend/tests/test_gone.py")
    result = runner.case(case, 1)
    assert result["stale"] and result["caught"]
    assert result["results"][0]["caught"]
    case["failing_tests"] = ["backend/tests/test_gone.py"]
    assert not runner.case(case, 1)["caught"]
    case["changed_files"] = []
    result = runner.case(case, 1)
    assert result["stale"] and not result["caught"]


def test_replay_counts_a_browser_script_outside_the_pr_checks(tmp_path):
    runner = small_replay(tmp_path)
    path = tmp_path / "ui/tests/check.cts"
    path.parent.mkdir(parents=True)
    path.touch()
    runner.plan = lambda changed, event: {"python": [], "contracts": False}
    case = dict(fix_pr=1, cause="other", changed_files=["ui/app/source.ts"], failing_tests=["ui/tests/check.cts"])
    result = runner.case(case, 1)
    assert not result["caught"] and not result["stale"]
    assert result["results"][0]["stages"]["pull_request"]["reason"] == "outside the PR/queue test jobs"


def test_replay_counts_static_jobs_but_not_unrunnable_eval_sources(tmp_path):
    runner = small_replay(tmp_path)
    workflow = tmp_path / replay.checks.PR_WORKFLOW
    workflow.parent.mkdir(parents=True)
    workflow.write_text("run: bash scripts/check_types.sh\n")
    seed = tmp_path / "evals/seeds.py"
    seed.parent.mkdir()
    seed.touch()
    runner.plan = lambda changed, event: {"python": [], "contracts": False}
    case = dict(
        fix_pr=1,
        cause="other",
        changed_files=["backend/module.py"],
        failing_tests=["basedpyright: backend/module.py:10"],
    )
    assert runner.case(case, 1)["caught"]
    case["failing_tests"] = ["evals/seeds.py (not a pytest file)"]
    result = runner.case(case, 1)
    assert not result["caught"] and not result["stale"]
    assert result["results"][0]["stages"]["merge_group"]["reason"] == "outside the PR/queue test jobs"


@pytest.mark.parametrize("event", ["pull_request", "merge_group"])
def test_replay_applies_each_stages_time_cap(monkeypatch, tmp_path, event):
    runner = small_replay(tmp_path)
    runner.counts = Counter({"test_fast.py": 1, "test_over_budget.py": 1})
    runner.timings = {"test_fast.py": 1, "test_over_budget.py": 15}
    picked = {
        "python": list(runner.counts),
        "narrow": {
            "tier": dict.fromkeys(runner.counts, 2),
            "budget_seconds": replay.checks.NARROW[event]["shards"],
            "smoke_unless_capped": [],
        },
    }
    calls = []
    monkeypatch.setattr(replay.checks, "selection", lambda changed, **kw: calls.append(kw["event"]) or picked)
    monkeypatch.setattr(replay.checks, "narrow", lambda plan, changed, event, **kw: calls.append(event) or plan)
    result = runner.plan(["module.py"], event)
    assert calls == [event, event]
    assert result["python"] == (["test_fast.py"] if event == "pull_request" else list(runner.counts))
    assert result["narrow"]["left_over_budget"] == (["test_over_budget.py"] if event == "pull_request" else [])
    assert result["narrow"]["selected_seconds"] == (1 if event == "pull_request" else 16)


@pytest.mark.parametrize("stage", ["pull_request", "merge_group", None])
def test_replay_catches_one_of_multiple_resolved_failures_at_either_stage(tmp_path, stage):
    runner = small_replay(tmp_path)
    for name in ("test_first.py", "test_second.py"):
        path = tmp_path / "backend/tests" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    runner.plan = lambda changed, event: {
        "python": ["backend/tests/test_second.py"] if event == stage else [],
        "contracts": False,
    }
    case = dict(
        fix_pr=1,
        cause="dynamic_ref",
        changed_files=["backend/source.py"],
        failing_tests=["backend/tests/test_first.py", "backend/tests/test_second.py"],
    )
    row = runner.case(case, 1)
    assert row["caught"] == (stage is not None)
    assert row["caught_pr"] == (stage == "pull_request")
    assert row["caught_queue"] == (stage == "merge_group")
    assert not row["stale"]


def test_replay_combines_named_breakers_before_resolving_paths(monkeypatch, tmp_path):
    runner = small_replay(tmp_path)
    second = "backend/tests/test_second.py"
    path = tmp_path / second
    path.parent.mkdir(parents=True)
    path.touch()
    calls, plans = [], []

    def diff(command, **kwargs):
        calls.append(command)
        assert kwargs["cwd"] == tmp_path
        return "backend/first.py\n" if command[-1] == "abcdef123" else second + "\n"

    monkeypatch.setattr(replay.subprocess, "check_output", diff)
    runner.plan = lambda changed, event: (
        plans.append((changed, event)) or {"python": [second] if second in changed else [], "contracts": False}
    )
    case = dict(
        fix_pr=1,
        cause="batched",
        changed_files=["backend/first.py"],
        breaking_prs=["pr 1 (merge abcdef123; topic)", "commit 123abcdef (topic)"],
        failing_tests=[second],
    )
    row = runner.case(case, 1)
    assert row["caught"]
    assert [c[-2:] for c in calls] == [["abcdef123^1", "abcdef123"], ["123abcdef^1", "123abcdef"]]
    assert plans == [(["backend/first.py", second], event) for event in replay.checks.NARROW]
    assert row["batch"] == "combined diff of all named breaking commits"


def test_replay_discloses_incomplete_or_unavailable_batched_diffs(monkeypatch, tmp_path):
    runner = small_replay(tmp_path)
    case = dict(cause="batched", changed_files=["first.py"], breaking_prs=["commit abcdef123 (topic)"])
    changed, note = runner.changed_files(case)
    assert changed == ["first.py"] and "does not identify both" in note
    case["breaking_prs"].append("stage_pick migration (PR not identified)")
    assert runner.changed_files(case) == (changed, note)

    def unavailable(*args, **kwargs):
        raise replay.subprocess.CalledProcessError(128, args[0])

    monkeypatch.setattr(replay.subprocess, "check_output", unavailable)
    case["breaking_prs"][1] = "commit 123abcdef (topic)"
    changed, note = runner.changed_files(case)
    assert changed == ["first.py"] and "unavailable locally" in note


def test_report_counts_pr_queue_only_and_missed(tmp_path, capsys):
    runner = small_replay(tmp_path)
    runner.target = lambda description: ("file", "backend/tests/test_check.py")
    runner.plan = lambda changed, event: {
        "python": ["backend/tests/test_check.py"] if changed == [event] or changed == ["both"] else [],
        "contracts": False,
    }
    rows = runner.run(
        [
            dict(fix_pr=1, cause="other", changed_files=[name], failing_tests=["check"])
            for name in ("both", "merge_group", "neither")
        ]
    )
    replay.report(rows)
    output = capsys.readouterr().out
    total = next(line for line in output.splitlines() if line.startswith("TOTAL"))
    assert total.split() == ["TOTAL", "1", "1", "1"]
    assert "Caught case IDs: [1, 2]" in output
    assert "Missed at both: [3]" in output


@pytest.mark.slow
def test_red_main_replay_pinned_cases():
    runner = replay.Replay(ROOT)
    cases = json.loads((ROOT / "scripts/red_main_cases.json").read_text())
    results = runner.run(cases)
    caught = {row["case"] for row in results if row["caught"]}
    caught_at_pr = {row["case"] for row in results if row["caught_pr"]}
    # A case whose failing test was deleted (a retired migration's, say) can no longer be replayed. It counts as
    # stale, under the bound below, not as a lost catch.
    stale = {row["case"] for row in results if row["stale"]}
    replay.report(results)
    print("Newly caught case IDs:", sorted(caught - PINNED_CAUGHT))
    lost = PINNED_CAUGHT - caught - stale
    assert not lost, f"Lost caught cases: {sorted(lost)}"
    missing_at_pr = PINNED_CAUGHT_AT_PR - caught_at_pr - stale
    assert not missing_at_pr, f"Lost PR-stage catches: {sorted(missing_at_pr)}"
    # The old web app's removal (#4660) deleted every ui/ browser script and the tests of its screenshot harness and
    # growth fixtures, so their cases are stale for good; the bound is on the rest.
    retired = {n for n, c in enumerate(cases, 1) if all(t.startswith(RETIRED_WITH_UI) for t in c["failing_tests"])}
    assert len(stale - retired) <= 10  # 10th: case 31, whose test and migration the v0.6.0 floor retired (#4169)
