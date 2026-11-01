"""The factory's board judge (evals/factory/scenarios.py board_keeps_moving): what it counts on the claim rounds it
is given, and a real run whose server passes over ready work, which the judge must fail."""
from types import SimpleNamespace

from evals.factory.cases.freezes import spend_under
from evals.factory.scenarios import board_keeps_moving
from evals.factory.sim import Run, Scenario, at, login_ready, tickets
from evals.factory.world import START
from backend import execution


def round_at(hour, free, ready, started, fallback=0, why=None):
    """One claim round as sim.Run.runner_round marks it: `ready` are the bots the records say can run, `started_at` the
    turns the round's claims started."""
    mark = {"at": START + at(hour), "free": free, "held": 0, "started_at": started, "fallback": fallback,
            "queued": len(ready), "slugs": list(ready), "ready": list(ready), "blocked": {}}
    if why:
        mark["why"] = why
    return mark


def judge(marks, since=at(0), waits=(), fallback=False):
    return board_keeps_moving(since, waits, fallback)(SimpleNamespace(marks=marks))


def test_a_round_that_starts_what_it_can_passes():
    passed, detail = judge([round_at(1, 1, ["fleet-01"], 1), round_at(2, 2, ["fleet-01", "fleet-02"], 2)])
    assert passed, detail


def test_a_free_slot_and_a_ready_bot_that_start_nothing_fail_with_the_round_named():
    passed, detail = judge([round_at(1, 1, ["fleet-01"], 0, why="fleet-01 oldest job abc123 hands out")])
    assert not passed
    assert "free 1, ready fleet-01, started 0" in detail and "fleet-01 oldest job abc123" in detail


def test_a_round_starts_the_smaller_of_its_free_slots_and_its_ready_bots():
    assert judge([round_at(1, 3, ["a", "b"], 2)])[0]          # two ready bots fill two of three free slots
    assert not judge([round_at(1, 3, ["a", "b"], 1)])[0]      # a second ready bot went unstarted
    assert not judge([round_at(1, 1, ["a", "b", "c"], 0)])[0]


def test_a_round_with_no_free_slot_or_no_ready_bot_is_not_judged():
    passed, detail = judge([round_at(1, 0, ["a"], 0), round_at(2, 2, [], 0)])
    assert not passed and "nothing shows the board moved" in detail
    assert judge([round_at(1, 0, ["a"], 0), round_at(2, 2, [], 0), round_at(3, 1, ["a"], 1)])[0]


def test_rounds_before_the_since_mark_are_not_judged():
    assert judge([round_at(0, 1, ["a"], 0), round_at(2, 1, ["a"], 1)], since=at(1))[0]


def test_a_declared_wait_waives_only_the_rounds_inside_it():
    stalled = round_at(1, 1, ["a"], 0)
    assert not judge([stalled, round_at(2, 1, ["a"], 1)])[0]
    passed, detail = judge([stalled, round_at(2, 1, ["a"], 1)], waits=[(at(0, 30), at(1, 15))])
    assert passed and "1 rounds inside declared waits were not judged" in detail
    assert not judge([stalled, round_at(2, 1, ["a"], 0)], waits=[(at(0, 30), at(1, 15))])[0]


def test_a_blocked_bot_does_not_waive_the_round_for_the_bots_that_are_ready():
    # One bot cooling off says nothing about another that can run: the round is still judged on the ready one, beside
    # a round that started what it could, so a judge that waived the round would pass.
    stalled = round_at(1, 2, ["b"], 0)
    stalled["blocked"] = {"a": "cooling off, no fallback free"}
    passed, detail = judge([stalled, round_at(2, 1, ["b"], 1)])
    assert not passed and "1 of 2 claim rounds" in detail and "ready b, started 0" in detail


def test_a_fallback_scenario_needs_a_judged_round_that_started_on_a_fallback():
    assert not judge([round_at(1, 1, ["a"], 1)], fallback=True)[0]
    assert judge([round_at(1, 1, ["a"], 1), round_at(2, 1, ["a"], 1, fallback=1)], fallback=True)[0]


def test_a_run_with_no_claim_rounds_fails():
    assert not judge([])[0]


def test_a_bot_is_ready_by_its_logins_as_the_claim_decides():
    # A hold on its own login leaves the turn to a free fallback; with its own login free, its cooldown decides, and a
    # free fallback does not lift it (execution._claim_refusal).
    assert login_ready(None, False, False) and not login_ready(None, True, True)
    assert login_ready("held", True, True) and not login_ready("held", False, False)


def stall_run(seed=1):
    """A two-hour run with two tickets, claimed the way a runner claims: small enough for the default suite."""
    scenario = Scenario("judge_stall", "Two tickets on a two-slot computer.",
                        setup=lambda run, w, team: tickets(run, w, 2), claim="runner", capacity=2, hours=2,
                        drain_hours=0.5, checks=[("the board keeps moving", None, board_keeps_moving(at(0)))])
    return Run(scenario, seed).go(), scenario


def test_the_judge_passes_a_server_that_starts_its_ready_work():
    run, scenario = stall_run()
    passed, detail = scenario.checks[0][2](run)
    assert passed, detail


def test_the_judge_fails_a_server_that_passes_over_every_claim(monkeypatch):
    # The injected stall: the claim route finds no candidate, so each claim answers with no work while the queue holds
    # bots the records say can run. Every round with a free slot is then short, and the judge must say so.
    monkeypatch.setattr(execution.Execution, "candidate", lambda self, c, who, body, runner, **kw: None)
    run, scenario = stall_run()
    passed, detail = scenario.checks[0][2](run)
    assert not passed, "a stalled board passed the judge"
    assert "started 0" in detail, detail
    assert run.claims[200] > 0 and sum(m["started_at"] for m in run.marks) == 0


def turn(started, finished, cost, bot="fleet-01"):
    """A turn row as the sim reads it: start and finish clock offsets, and its estimated cost."""
    text = lambda offset: (START + offset).strftime("%Y-%m-%dT%H:%M:%S.000000Z")
    return {"bot": bot, "started_at": text(started), "finished_at": text(finished) if finished is not None else None,
            "est_cost_usd": cost}


def turns_run(turns):
    """A stand-in run whose records are these turns, for the company pool (no store needed)."""
    return SimpleNamespace(w=SimpleNamespace(sql=lambda statement, args=(): turns))


def test_a_company_cap_holds_a_burst_only_once_the_half_hour_has_spent_it():
    # The cap of $3 an hour is held as the half hour at $1.5. A burst that starts in a fresh half hour is admitted, and
    # the half hour reached $1.5 by 10:42 (the turns that finished by then count).
    burst = [turn(at(1, 30), at(1, 40), 1.0), turn(at(1, 31), at(1, 39), 0.6)]
    assert spend_under(3, at(1, 30), at(1, 42))(turns_run(burst))[0]


def test_a_turn_that_starts_after_the_half_hour_reached_its_cap_fails():
    late = [turn(at(1, 30), at(1, 33), 1.6), turn(at(1, 35), at(1, 40), 0.4)]
    passed, detail = spend_under(3, at(1, 30), at(1, 42))(turns_run(late))
    assert not passed and "10:35" in detail, detail


def test_a_window_that_never_reached_the_cap_fails_so_the_check_cannot_pass_on_nothing():
    passed, detail = spend_under(3, at(1, 30), at(1, 42))(turns_run([turn(at(1, 30), at(1, 35), 0.5)]))
    assert not passed and "short of the $1.5 window cap" in detail, detail

