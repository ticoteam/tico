"""A computer never leases more turns than its limit: the server refuses the turn past it, and each ended lease frees
exactly one place for the next claim. A parked turn (a login or other pre-start wait) keeps its place."""
from backend import start_places
from backend.store import H
from backend.tests.test_api import api, claim, post  # noqa: F401  (api is a fixture)
from backend.tests.test_task_lanes import new_task, task_bot
from backend.tests.test_turns_at_once import set_computer


def live_turns(api, machine):
    with api.app.state.store.read() as c:
        return sum(start_places.live(c, machine['runner_id']).values())


def end_lease(api, attempt):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE attempts SET lease_until=? WHERE id=?", (H.shift(H.now(), seconds=-1), attempt['id']))


def park(api, machine, attempt, reason='shared login'):
    post(api, f"attempts/{attempt['id']}/start-place", {'waiting': reason}, machine['token'])


def test_five_times_the_capacity_leases_only_capacity_and_each_freed_place_leases_one_more(api):
    capacity = 3
    machine = task_bot(api, lanes=True, capacity=capacity)
    for i in range(5 * capacity):
        new_task(api, f"job {i}")
    held = [claim(api, machine) for _ in range(capacity)]
    assert all(held)
    assert claim(api, machine) is None
    assert live_turns(api, machine) == capacity
    for _ in range(4):
        end_lease(api, held.pop(0))
        held.append(claim(api, machine))
        assert held[-1] is not None
        assert claim(api, machine) is None
        assert live_turns(api, machine) == capacity


def test_a_parked_turn_keeps_its_place_until_it_ends(api):
    machine = task_bot(api, lanes=True, capacity=2)
    for i in range(3):
        new_task(api, f"job {i}")
    first, second = claim(api, machine), claim(api, machine)
    assert first and second
    park(api, machine, first)
    # The parked turn still holds its place, so no third turn is leased beside the two.
    assert claim(api, machine) is None
    assert live_turns(api, machine) == 2
    end_lease(api, first)
    assert claim(api, machine) is not None
    assert claim(api, machine) is None


def test_turns_at_once_below_capacity_caps_the_leases_at_that_limit(api):
    machine = task_bot(api, lanes=True, capacity=4)
    for i in range(10):
        new_task(api, f"job {i}")
    assert set_computer(api, machine, 2)["turn_limit"] == 2
    assert claim(api, machine) and claim(api, machine)
    assert claim(api, machine) is None
    assert live_turns(api, machine) == 2
