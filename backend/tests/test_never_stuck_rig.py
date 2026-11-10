# pyright: strict
"""The fault rig (evals/never_stuck) against the real engine: the 52 scenarios, the known bugs and a few seeds.

The rig is seeded and replays exactly, so each case here runs the same way every time. A scenario or seed that fails
because of an engine bug stays here as a strict expected failure named for the bug (evals/never_stuck/README.md): it
turns red the day the bug is fixed, and the mark comes off. The engine modules are imported here so a change to any
of them selects this file."""

import os

import pytest

from backend import answers, never_stuck, pipeline_settings, task_engine, task_requirements, task_wakes, waiting_on
from evals.never_stuck import perf
from evals.never_stuck import run as rig_run
from evals.never_stuck import scenarios as SC
from evals.never_stuck import sim, world

# Imported here, not only through the rig, so a change to any of them selects this file directly (scripts/pr_checks.py).
SELECTED_BY = (answers, never_stuck, pipeline_settings, task_engine, task_requirements, task_wakes, waiting_on, world)

# Seeds that pass, and seeds that fail on a known bug (the bug each one hits).
PASSING_SEEDS = (1, 2, 4, 8)
FAILING_SEEDS = {27: "runs-outside-ladder"}


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


@pytest.mark.parametrize("name", sorted(SC.BUGS))
@pytest.mark.xfail(strict=True, raises=SC.ScenarioFailed, reason="a known engine bug (evals/never_stuck/README.md)")
def test_known_bug(name: str) -> None:
    SC.run(SC.BUGS[name])


@pytest.mark.parametrize("name", sorted(SC.CHECKS))
def test_extra_check(name: str) -> None:
    SC.run(SC.CHECKS[name])


def test_every_scenario_is_built_or_tabled() -> None:
    built, later = set(SC.SCENARIOS), set(SC.LATER)
    assert not built & later
    assert SC.numbers_covered() == list(range(1, 53))
    assert set(SC.FAILING) <= built
    assert all(phase and why for phase, why in SC.LATER.values())


@pytest.mark.parametrize("seed", PASSING_SEEDS)
def test_seed(seed: int) -> None:
    report = rig_run.one(seed)
    assert report["ok"], f"seed {seed} step {report.get('step')}: {report.get('what')}"
    assert report["runs"] > 0 and report["most_unchanged_runs"] <= 7


@pytest.mark.parametrize("seed", sorted(FAILING_SEEDS))
@pytest.mark.xfail(strict=True, raises=sim.RigFailure, reason="a known engine bug (evals/never_stuck/README.md)")
def test_seed_with_known_bug(seed: int) -> None:
    try:
        sim.run_seed(seed)
    except sim.RigFailure as failure:
        assert failure.bug == FAILING_SEEDS[seed], f"seed {seed} found another failure: {failure.what}"
        raise


def test_a_seed_replays_exactly() -> None:
    first, second = rig_run.one(2), rig_run.one(2)
    first.pop("seconds")
    second.pop("seconds")
    assert first == second


def test_shards_split_a_range() -> None:
    shards = [rig_run.seeds_of("1-10", f"{i}/3") for i in (1, 2, 3)]
    assert sorted(s for shard in shards for s in shard) == list(range(1, 11))
    assert shards[0] == [1, 4, 7, 10]


def test_perf_measures_one_pass() -> None:
    out = perf.measure(30, profile=False)
    assert out["tasks"] == 30 and out["statements"] > 0 and out["seconds"] >= 0


def test_a_world_puts_the_environment_back() -> None:
    before = {name: os.environ.get(name) for name in world.OFFLINE}
    with world.World(1):
        assert all(os.environ.get(name) == value for name, value in world.OFFLINE.items())
    assert {name: os.environ.get(name) for name in world.OFFLINE} == before
