"""Needs you's builds take turns on their own lane (backend/needs_lane.py, I39 AC-3): fifty readers at once used to
run fifty builds at once, each waiting on the others for the interpreter, and the route served a third of the lists
a second it serves to one reader. A request waiting its turn holds none of the shared pool's threads."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

from backend import needs_lane
from backend import views as V
from backend.tests.helpers.api import headers


def _counting(monkeypatch, name):
    """Wrap views.<name> so it records how many builds ran at once (each takes 50 ms) and on which threads."""
    real = getattr(V, name)
    state = {"now": 0, "most": 0, "threads": set()}
    lock = threading.Lock()

    def counted(*args, **kwargs):
        with lock:
            state["now"] += 1
            state["most"] = max(state["most"], state["now"])
            state["threads"].add(threading.current_thread().name)
        try:
            time.sleep(0.05)
            return real(*args, **kwargs)
        finally:
            with lock:
                state["now"] -= 1

    monkeypatch.setattr(V, name, counted)
    return state


def _at_once(api, path, readers=6):
    with ThreadPoolExecutor(readers) as pool:
        tokens = ["ana-test", "ben-test"] * (readers // 2)
        return list(pool.map(lambda token: api.get(path, headers=headers(token)), tokens))


def test_needs_you_builds_take_turns_on_their_own_lane(api, monkeypatch):
    state = _counting(monkeypatch, "needs_items")
    answers = _at_once(api, "/api/v2/needs-you")
    assert [a.status_code for a in answers] == [200] * len(answers)
    assert {a.json()["actor"] for a in answers} == {"human:ana", "human:ben"}
    assert state["most"] == needs_lane.LANES
    assert all(name.startswith("tico-needs-you") for name in state["threads"]), state["threads"]


def test_the_count_and_the_summary_take_the_same_lane(api, monkeypatch):
    state = _counting(monkeypatch, "needs_items")
    answers = _at_once(api, "/api/v2/needs-you?count=1")
    assert [a.status_code for a in answers] == [200] * len(answers)
    summary = _counting(monkeypatch, "needs_summary")
    answers = _at_once(api, "/api/v2/needs-you/summary")
    assert [a.status_code for a in answers] == [200] * len(answers)
    for found in (state, summary):
        assert found["most"] == needs_lane.LANES
        assert all(name.startswith("tico-needs-you") for name in found["threads"]), found["threads"]


def test_other_routes_answer_while_needs_you_builds_wait_their_turn(api, monkeypatch):
    """More Needs you readers than the shared pool has threads, every build held: a request waiting its turn on the lane
    holds no thread of the shared pool, so GET /api/v2/me still answers."""
    release = threading.Event()
    real = V.needs_items

    def held(*args, **kwargs):
        release.wait(30)
        return real(*args, **kwargs)

    monkeypatch.setattr(V, "needs_items", held)
    readers = ThreadPoolExecutor(45)
    try:
        waiting = [readers.submit(api.get, "/api/v2/needs-you", headers=headers("ben-test")) for _ in range(45)]
        time.sleep(0.5)  # every Needs you request has arrived and waits on the held builds
        with ThreadPoolExecutor(1) as one:
            me = one.submit(api.get, "/api/v2/me", headers=headers("ana-test"))
            answer = me.result(timeout=10)
            assert not release.is_set()
            assert answer.status_code == 200
    finally:
        release.set()
        readers.shutdown(wait=True)
    assert [w.result().status_code for w in waiting] == [200] * 45
