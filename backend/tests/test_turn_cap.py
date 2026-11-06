"""A computer's claims stop at twice its turn limit (start_places.live_limit): the server refuses a claim past that
bound, and each ended lease frees one place for the next claim. A parked turn (a login or other pre-start wait) counts
toward the bound but takes no start place, so it does not stop other work from starting."""
from backend import start_places
from backend.store import H
from backend.tests.test_api import api, claim, post  # noqa: F401  (api is a fixture)
from backend.tests.test_task_lanes import new_task, task_bot
from backend.tests.test_turns_at_once import set_computer
from backend.views import free_turn


def live_turns(api, machine):
    with api.app.state.store.read() as c:
        return sum(start_places.live(c, machine['runner_id']).values())


def end_lease(api, attempt):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE attempts SET lease_until=? WHERE id=?", (H.shift(H.now(), seconds=-1), attempt['id']))


def park(api, machine, attempt, reason='shared login'):
    post(api, f"attempts/{attempt['id']}/start-place", {'waiting': reason}, machine['token'])


def free_here(api, machine):
    with api.app.state.store.read() as c:
        return free_turn(c, 'ops', {'machine': {'runner_id': machine['runner_id'], 'label': 'Test Mac'}})


def claims_stop_at_twice_the_capacity_and_each_ended_lease_frees_one_more(api, machine):
    capacity = 3
    held = [claim(api, machine) for _ in range(2 * capacity)]
    assert all(held)
    assert claim(api, machine) is None
    assert live_turns(api, machine) == 2 * capacity
    for _ in range(4):
        end_lease(api, held.pop(0))
        held.append(claim(api, machine))
        assert held[-1] is not None
        assert claim(api, machine) is None
        assert live_turns(api, machine) == 2 * capacity


def a_parked_turn_does_not_block_a_claim_below_twice_the_capacity(api, machine):
    first, second = claim(api, machine), claim(api, machine)
    assert first and second
    park(api, machine, first)
    # The parked turn counts toward the bound of 4 but takes no start place, so claims below 4 still go through.
    assert claim(api, machine) is not None
    assert claim(api, machine) is not None
    assert claim(api, machine) is None
    assert live_turns(api, machine) == 4


def a_claim_at_twice_the_capacity_is_refused_while_its_turns_are_parked(api, machine):
    held = [claim(api, machine) for _ in range(4)]
    assert all(held)
    park(api, machine, held[0])
    park(api, machine, held[1])
    assert claim(api, machine) is None
    assert live_turns(api, machine) == 4
    end_lease(api, held[0])      # a parked turn ends: its claim frees one place
    assert claim(api, machine) is not None
    assert claim(api, machine) is None


def turns_at_once_below_capacity_caps_the_leases_at_twice_that_limit(api, machine):
    assert set_computer(api, machine, 2)["turn_limit"] == 2
    assert all(claim(api, machine) for _ in range(4))
    assert claim(api, machine) is None
    assert live_turns(api, machine) == 4


def free_turn_says_no_place_when_its_starts_or_its_claims_are_full(api, machine):
    first, second = claim(api, machine), claim(api, machine)
    assert free_here(api, machine) == 'Test Mac'      # both start places are taken
    park(api, machine, first)
    park(api, machine, second)
    assert free_here(api, machine) == ''              # parked turns take no start place: a turn can start now
    third, fourth = claim(api, machine), claim(api, machine)
    assert third and fourth
    park(api, machine, third)
    park(api, machine, fourth)
    assert free_here(api, machine) == 'Test Mac'      # four claims are held: nothing more can be claimed
    assert claim(api, machine) is None


def running_turns(api, machine):
    with api.app.state.store.read() as c:
        return c.execute("SELECT count(*) FROM attempts WHERE runner_id=? AND state='running'",
                         (machine['runner_id'],)).fetchone()[0]


def a_claim_leased_after_the_limit_drops_starts_nothing_until_a_place_frees(api, machine):
    """"Turns at once" lowered from 4 to 1 before the runner hears of it. The next claim is leased (two turns are under
    twice the new limit) but reserves no place while the first turn runs: its start is refused and one turn runs. Once the
    first turn ends, the same claim takes the place and starts."""
    first = claim(api, machine)
    assert post(api, f"attempts/{first['id']}/start-place", {}, machine["token"])["reserved"] is True
    post(api, f"attempts/{first['id']}/started", {"thread_id": "first-run"}, machine["token"])
    assert set_computer(api, machine, 1)["turn_limit"] == 1
    second = claim(api, machine)
    assert second is not None
    post(api, f"attempts/{second['id']}/started", {"thread_id": "second-run"}, machine["token"], expected=409)
    assert running_turns(api, machine) == 1
    post(api, f"attempts/{first['id']}/complete", {"outcome": "interrupted", "text": "", "last_seq": 0}, machine["token"])
    assert post(api, f"attempts/{second['id']}/start-place", {}, machine["token"])["reserved"] is True
    post(api, f"attempts/{second['id']}/started", {"thread_id": "second-run"}, machine["token"])
    assert running_turns(api, machine) == 1


def test_the_live_count_searches_the_runner_state_index(api):
    """The live count's explicit state IN lets SQLite search attempts_runner_state,
    not scan every attempt on the computer."""
    with api.app.state.store.read() as c:
        plan = [r[3] for r in c.execute("EXPLAIN QUERY PLAN " + start_places.LIVE_SQL, ("runner", H.now()))]
    assert any("attempts_runner_state" in line for line in plan), plan


def free_every_place(api, machine):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE attempts SET lease_until=? WHERE runner_id=?", (H.shift(H.now(), seconds=-1), machine['runner_id']))


def test_claims_stop_at_twice_the_capacity_and_a_parked_turn_takes_no_start_place(api):
    machine = task_bot(api, lanes=True, capacity=2)
    for i in range(40):
        new_task(api, f"job {i}")
    for story in (a_parked_turn_does_not_block_a_claim_below_twice_the_capacity,
                  a_claim_at_twice_the_capacity_is_refused_while_its_turns_are_parked,
                  free_turn_says_no_place_when_its_starts_or_its_claims_are_full):
        story(api, machine)
        free_every_place(api, machine)
        assert live_turns(api, machine) == 0


def test_the_bound_is_twice_the_turn_limit_and_a_lowered_limit_starts_nothing_until_a_place_frees(api):
    machine = task_bot(api, lanes=True, capacity=3)
    for i in range(40):
        new_task(api, f"job {i}")
    claims_stop_at_twice_the_capacity_and_each_ended_lease_frees_one_more(api, machine)
    free_every_place(api, machine)
    turns_at_once_below_capacity_caps_the_leases_at_twice_that_limit(api, machine)
    free_every_place(api, machine)
    a_claim_leased_after_the_limit_drops_starts_nothing_until_a_place_frees(api, machine)
