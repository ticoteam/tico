# pyright: strict
"""The fault rig (evals/never_stuck) against the real engine: the 52 scenarios, the known bugs, a few seeds, and the
Product and Engineering cases and seeds (software_scenarios, software_sim).

The rig is seeded and replays exactly, so each case here runs the same way every time. A scenario or seed that fails
because of an engine bug stays here as a strict expected failure named for the bug (evals/never_stuck/README.md): it
turns red the day the bug is fixed, and the mark comes off. The engine modules are imported here so a change to any
of them selects this file."""

import os
import random

import pytest

from backend import answers, never_stuck, pipeline_settings, task_engine, task_requirements, task_wakes, waiting_on
from evals.never_stuck import perf
from evals.never_stuck import run as rig_run
from evals.never_stuck import scenarios as SC
from evals.never_stuck import sim, software, software_scenarios, software_sim, world
from evals.never_stuck.fake_github import DOWN, Faults, RigGitHub
from evals.never_stuck.fake_jev import FAIL, MET, NOT_MET, UNSURE, FakeJev

# Imported here, not only through the rig, so a change to any of them selects this file directly (scripts/pr_checks.py).
SELECTED_BY = (answers, never_stuck, pipeline_settings, task_engine, task_requirements, task_wakes, waiting_on, world)

# Seeds that pass, and seeds that fail on a known bug (the bug each one hits).
PASSING_SEEDS = (1, 2, 4, 8, 27, 8313)
FAILING_SEEDS: dict[int, str] = {}


def _scenario_cases() -> list[object]:
    cases: list[object] = []
    for number, case in sorted(SC.SCENARIOS.items()):
        marks: list[pytest.MarkDecorator] = []
        if number in SC.FAILING:
            marks.append(pytest.mark.xfail(strict=True, raises=SC.ScenarioFailed, reason=SC.FAILING[number]))
        cases.append(pytest.param(case, id=f"{number:02d}", marks=marks))
    return cases


@pytest.mark.parametrize("case", _scenario_cases())
def test_scenario(case: SC.Scenario) -> None:
    SC.run(case)


@pytest.mark.parametrize("name", sorted(SC.CHECKS))
def test_extra_check(name: str) -> None:
    SC.run(SC.CHECKS[name])


@pytest.mark.parametrize("seed", PASSING_SEEDS)
def test_seed(seed: int) -> None:
    report = rig_run.one(seed)
    assert report["ok"], f"seed {seed} step {report.get('step')}: {report.get('what')}"
    assert report["runs"] > 0 and report["most_unchanged_runs"] <= 7


@pytest.mark.parametrize("queued", [False, True], ids=["runs-ended", "wakes-queued"])
def test_one_pass_reads_as_many_statements_over_a_thousand_idle_tasks_as_over_a_hundred(queued: bool) -> None:
    """Cost: a pass reads what it needs for every open task together, in a fixed number of statements, not a few
    statements a task (it read 20 a task here when the runs had ended, 13 with the wakes queued; now 48 a
    pass). 25 tasks made through the engine, copied to 100 and to 1,000, none due
    for a try: each bot's run ended and its next try is later, or its wake is still queued."""
    small = perf.measure(25, profile=False, copies=3, queued=queued, due=0)
    big = perf.measure(25, profile=False, copies=39, queued=queued, due=0)
    assert (small["tasks"], big["tasks"]) == (100, 1000)
    assert small["pass"] == big["pass"] == {}
    assert big["reads"] == small["reads"], big["top_statements"]
    assert big["reads"] < 60, big["top_statements"]


def test_a_world_puts_the_environment_back() -> None:
    before = {name: os.environ.get(name) for name in world.OFFLINE}
    with world.World(1):
        assert all(os.environ.get(name) == value for name, value in world.OFFLINE.items())
    assert {name: os.environ.get(name) for name in world.OFFLINE} == before


# ----------------------------------------------------------------------------- Product and Engineering (G18)
SS = software_scenarios
SOFTWARE_SEEDS = (1, 2, 3)


def _software_cases() -> list[object]:
    cases: list[object] = []
    for name, case in SS.CASES.items():
        marks: list[pytest.MarkDecorator] = []
        if name in SS.PENDING:
            reason = f"not built yet: {SS.PENDING[name]}"
            marks.append(pytest.mark.xfail(strict=True, raises=software.NotBuiltYet, reason=reason))
        cases.append(pytest.param(case, id=name, marks=marks))
    return cases


@pytest.mark.parametrize("case", _software_cases())
def test_software_case(case: SS.Case) -> None:
    SS.run(case)


@pytest.mark.parametrize("seed", SOFTWARE_SEEDS)
def test_software_seed(seed: int) -> None:
    if "idea-to-done" in SS.PENDING:
        with pytest.raises(software.NotBuiltYet):
            software_sim.run_software_seed(seed)
        return
    report = software_sim.run_software_seed(seed)
    assert report["runs"] > 0 and report["most_unchanged_runs"] <= software_sim.MOST_UNCHANGED_RUNS
