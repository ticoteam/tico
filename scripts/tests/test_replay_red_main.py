"""Historical selection coverage and replay bookkeeping, without running the selected checks."""

from collections import Counter, defaultdict
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import replay_red_main as replay

ROOT = Path(__file__).resolve().parents[2]


def small_replay(tmp_path):
    runner = replay.Replay.__new__(replay.Replay)
    runner.root = tmp_path
    runner.graph = SimpleNamespace(by_base=defaultdict(set))
    runner.definitions = defaultdict(set)
    return runner


def test_replay_maps_script_conversions_and_excludes_unresolved_cases(tmp_path):
    runner = small_replay(tmp_path)
    path = tmp_path / "ui/tests/check.cts"
    path.parent.mkdir(parents=True)
    path.touch()
    runner.plan = lambda changed: {"python": [], "browser": ["check"], "contracts": False}
    case = dict(
        fix_pr=1, cause="dynamic_ref", changed_files=["ui/tests/check.cjs"], failing_tests=["ui/tests/check.cjs"]
    )
    result = runner.case(case, 1)
    assert result["caught"] and not result["stale"]
    assert result["moved"] == {"ui/tests/check.cjs": "ui/tests/check.cts"}
    case["failing_tests"].append("ui/tests/gone.cjs")
    result = runner.case(case, 1)
    assert result["stale"] and not result["caught"]
    assert result["results"][0]["caught"]
    case["changed_files"] = []
    assert runner.case(case, 1)["stale"]


def test_replay_counts_static_jobs_but_not_unrunnable_eval_sources(tmp_path):
    runner = small_replay(tmp_path)
    workflow = tmp_path / replay.checks.PR_WORKFLOW
    workflow.parent.mkdir(parents=True)
    workflow.write_text("run: bash scripts/check_types.sh\n")
    seed = tmp_path / "evals/seeds.py"
    seed.parent.mkdir()
    seed.touch()
    runner.plan = lambda changed: {"python": [], "browser": [], "contracts": False}
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
    assert result["results"][0]["reason"] == "outside the PR test jobs"


def test_replay_applies_the_light_plans_time_cap(monkeypatch, tmp_path):
    runner = small_replay(tmp_path)
    runner.core = []
    runner.counts = Counter({"test_fast.py": 1, "test_over_budget.py": 1})
    runner.timings = {"test_fast.py": 1, "test_over_budget.py": 20}
    picked = {
        "python": list(runner.counts),
        "narrow": {"tier": dict.fromkeys(runner.counts, 2), "budget_seconds": 10, "smoke_unless_capped": []},
    }
    calls = []
    monkeypatch.setattr(replay.checks, "selection", lambda changed, **kw: calls.append("selection") or picked)
    monkeypatch.setattr(replay.checks, "narrow", lambda plan, changed, event, **kw: calls.append(event) or plan)
    result = runner.plan(["module.py"])
    assert calls == ["selection", "pull_request"]
    assert result["python"] == ["test_fast.py"]
    assert result["narrow"]["left_over_budget"] == ["test_over_budget.py"]


@pytest.mark.slow
def test_red_main_replay_coverage_floor():
    runner = replay.Replay(ROOT)
    cases = json.loads((ROOT / "scripts/red_main_cases.json").read_text())
    results = runner.run(cases)
    # Count whole cases: every resolvable failing check must run. Stale cases never earn a catch.
    assert sum(row["caught"] for row in results) >= 35
    assert sum(row["stale"] for row in results) <= 9
