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


def test_claims_stop_at_twice_the_capacity_and_each_ended_lease_frees_one_more(api):
    capacity = 3
    machine = task_bot(api, lanes=True, capacity=capacity)
    for i in range(5 * capacity):
        new_task(api, f"job {i}")
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


def test_a_parked_turn_does_not_block_a_claim_below_twice_the_capacity(api):
    machine = task_bot(api, lanes=True, capacity=2)
    for i in range(6):
        new_task(api, f"job {i}")
    first, second = claim(api, machine), claim(api, machine)
    assert first and second
    park(api, machine, first)
    # The parked turn counts toward the bound of 4 but takes no start place, so claims below 4 still go through.
    assert claim(api, machine) is not None
    assert claim(api, machine) is not None
    assert claim(api, machine) is None
    assert live_turns(api, machine) == 4


def test_a_claim_at_twice_the_capacity_is_refused_while_its_turns_are_parked(api):
    machine = task_bot(api, lanes=True, capacity=2)
    for i in range(6):
        new_task(api, f"job {i}")
    held = [claim(api, machine) for _ in range(4)]
    assert all(held)
    park(api, machine, held[0])
    park(api, machine, held[1])
    assert claim(api, machine) is None
    assert live_turns(api, machine) == 4
    end_lease(api, held[0])      # a parked turn ends: its claim frees one place
    assert claim(api, machine) is not None
    assert claim(api, machine) is None


def test_turns_at_once_below_capacity_caps_the_leases_at_twice_that_limit(api):
    machine = task_bot(api, lanes=True, capacity=4)
    for i in range(10):
        new_task(api, f"job {i}")
    assert set_computer(api, machine, 2)["turn_limit"] == 2
    assert all(claim(api, machine) for _ in range(4))
    assert claim(api, machine) is None
    assert live_turns(api, machine) == 4


def test_free_turn_says_no_place_when_its_starts_or_its_claims_are_full(api):
    machine = task_bot(api, lanes=True, capacity=2)
    for i in range(6):
        new_task(api, f"job {i}")
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
