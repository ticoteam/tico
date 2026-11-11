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
    assert not set(SC.ELSEWHERE) & (built | later)
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


def test_one_pass_over_three_thousand_idle_tasks_with_queued_wakes_is_quick() -> None:
    """Speed: every pass looks at every open task. 30 tasks made through the engine, copied to 3,000 (perf.clone), each
    with its wake queued and nothing due. About 0.5 s of CPU here; when wakes were read with LIKE, 2,000 tasks took
    3.1 s, and the time grew with the square of the tasks. Live runs are read once a pass, not once a task. Timed in
    this process's CPU, the best of two passes, so other load on the machine does not count; a read made for each task
    shows first in the count of reads, which no load moves."""
    runs = [perf.measure(30, profile=False, copies=99, queued=True) for _ in range(2)]
    for out in runs:
        assert out["tasks"] == 3000 and out["pass"] == {}
        assert out["live_reads"] == 1
        assert out["reads"] < 60, out["top_statements"]
    best = min(runs, key=lambda out: out["cpu_seconds"])
    assert best["cpu_seconds"] < 2.0, (best["cpu_seconds"], best["seconds"], best["top_statements"])


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


def test_one_pass_takes_about_twice_as_long_over_twice_the_tasks() -> None:
    """Speed: a pass's time grows with the open tasks, not with their square. Each task's runs are found from the task
    (task_wakes._runs_of), never by reading every run of its bot for each task: that took 4.6 s over 2,000 tasks and
    148 s over 10,000. Now about 0.7 s and 1.3 s here over 2,000 and 4,000 (4.2 s over 10,000), 2% due for a try.
    Timed in this process's CPU, not on the wall clock, so other load on the machine (a 16-way run) does not count:
    linear is about 2x, a square 4x; under 3x passes. The best of two passes each."""
    small = min((perf.measure(50, profile=False, copies=39) for _ in range(2)), key=lambda out: out["cpu_seconds"])
    big = min((perf.measure(50, profile=False, copies=79) for _ in range(2)), key=lambda out: out["cpu_seconds"])
    assert (small["tasks"], big["tasks"]) == (2000, 4000)
    assert big["pass"]["woken"] > small["pass"]["woken"] > 0
    assert big["cpu_seconds"] < 3 * small["cpu_seconds"], (
        small["cpu_seconds"],
        big["cpu_seconds"],
        big["top_statements"],
    )


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


def test_software_cases_say_what_they_cover() -> None:
    assert set(SS.PENDING) <= set(SS.CASES)
    for number, name in SC.ELSEWHERE.items():
        assert name not in SS.PENDING and number in SS.CASES[name].covers, (number, name)
    assert SS.covered() <= set(range(1, 53))


@pytest.mark.parametrize("seed", SOFTWARE_SEEDS)
def test_software_seed(seed: int) -> None:
    if "idea-to-done" in SS.PENDING:
        with pytest.raises(software.NotBuiltYet):
            software_sim.run_software_seed(seed)
        return
    report = software_sim.run_software_seed(seed)
    assert report["runs"] > 0 and report["most_unchanged_runs"] <= software_sim.MOST_UNCHANGED_RUNS


def test_a_software_world_has_the_role_bots_with_their_templates() -> None:
    from backend import bot_settings

    with world.World(1, software=True) as w:
        for bot, template in world.ROLE_TEMPLATES.items():
            slug = bot.split(":", 1)[1]
            assert w.read(lambda c, slug=slug: bot_settings.read(c, slug)).get("template") == template, bot


def test_a_stub_ends_the_case_as_not_built_yet() -> None:
    with pytest.raises(software.NotBuiltYet, match="track C"):
        with software.stubs_end_the_case():
            raise NotImplementedError("merge_queue.run: track C")
    with pytest.raises(SC.ScenarioFailed) as raised:
        with software.stubs_end_the_case():
            SC.expect(False, "a real failure")
    assert not isinstance(raised.value, software.NotBuiltYet)


def test_fake_jev_answers_what_the_page_asks() -> None:
    from clients.judge import JudgeError

    jev = FakeJev()
    jev.say("d1", NOT_MET)
    assert jev.engine("m", {"attachment": {"digest": "d1"}}, {}, "x")["p"] < 0.5
    assert jev.engine("m", {"attachment": {"digest": "d2", "text": "jev:unsure"}}, {}, "x")["p"] == 0.5
    assert jev.engine("m", {"attachment": {"digest": "d3", "text": "plain"}}, {}, "x")["p"] > 0.9
    jev.say("d4", FAIL)
    with pytest.raises(JudgeError):
        jev.engine("m", {"attachment": {"digest": "d4"}}, {}, "x")
    assert dict(jev.counts) == {NOT_MET: 1, UNSURE: 1, MET: 1, FAIL: 1}


def test_fake_jev_is_put_back() -> None:
    from backend import pipeline_judges

    before = pipeline_judges.ENGINE
    jev = FakeJev()
    with jev.installed():
        assert pipeline_judges.ENGINE == jev.engine
    assert pipeline_judges.ENGINE is before


def test_rig_github_runs_checks_lands_the_queue_and_goes_down() -> None:
    from backend import hubdb as H
    from backend.tests.helpers.fake_github import GitHubError

    with world.World(1) as w:
        gh = RigGitHub()
        head = gh.open_pr(1)
        assert gh.read_checks("acme/app", head)[0]["state"] == "pending"
        w.shift(gh.ci_s)
        gh.tick()
        assert gh.read_checks("acme/app", head)[0]["state"] == "success"
        assert not gh.enqueue("acme/app", 1, head)["ok"], "GitHub queues no draft"
        gh.ready("acme/app", 1)
        assert gh.enqueue("acme/app", 1, head)["ok"]
        gh._queued[("acme/app", 1)] = H.shift(H.now(), seconds=-gh.land_after_s)
        gh.land()
        assert gh.state(1)["state"] == "merged" and gh.unreleased()
        versions = [d.version for d in gh.outbox]
        assert versions == sorted(set(versions)), "webhook versions go up"
        gh.down_until = H.shift(H.now(), seconds=60)
        with pytest.raises(GitHubError, match=DOWN):
            gh.read_pr("acme/app", 1)
        assert gh.deliver(None) == 0


def test_rig_github_faults_lose_and_repeat_webhooks() -> None:
    with world.World(1):
        lossy = RigGitHub(rng=random.Random(1), faults=Faults(lose=1.0))
        lossy.open_pr(1)
        assert not lossy.outbox and lossy.stats["webhook lost"] == 1
        twice = RigGitHub(rng=random.Random(1), faults=Faults(repeat=1.0))
        twice.open_pr(1)
        assert len(twice.outbox) == 2 and twice.outbox[0].id == twice.outbox[1].id
