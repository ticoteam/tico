"""The per-test pooled connection check (backend/tests/connection_guard.py) fails the test that leaves a connection open."""

from pathlib import Path

import pytest

pytest_plugins = ["pytester"]

REPO = Path(__file__).resolve().parents[2]


def test_a_test_that_leaves_a_pooled_connection_open_fails_at_its_teardown_and_names_itself(pytester, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(REPO))
    pytester.makepyfile(
        test_leaks="""
        from backend.config import Settings
        from backend.store import Store

        HELD = []


        def test_the_one_that_leaks(tmp_path):
            store = Store(Settings(db_path=tmp_path / "hub.sqlite"))
            # The context manager is kept: were it freed, its generator would give the connection back to the pool.
            checkout = store.read()
            HELD.append((checkout, checkout.__enter__()))  # checked out and never returned


        def test_a_clean_one_after_it(tmp_path):
            store = Store(Settings(db_path=tmp_path / "hub.sqlite"))
            with store.read():
                pass
        """
    )
    result = pytester.runpytest_subprocess("-p", "backend.tests.connection_guard")
    # The leaking test passes its call and errors at its teardown; the clean one passes.
    result.assert_outcomes(passed=2, errors=1)
    result.stdout.fnmatch_lines(
        [
            "*ERROR at teardown of test_the_one_that_leaks*",
            "*left 1 pooled database connection*",
            "*test_the_one_that_leaks*",
        ]
    )


# A scheduler tick's pass thread (thread-rank) outlived its test and opened a connection during a later one. The later
# test's teardown closed it under the thread's statement, which segfaulted the xdist worker (merge queue run
# 42516409696, `worker 'gw3' crashed`), and blamed the later test for a leak that was not its own. Each case below starts
# such a thread in one test, lets it reach a point with the connection in hand during the next test, runs that test's
# teardown, and lets the thread go on in a third: the connection must still be open there, and no test fails.
LINGERING = """
import threading

import pytest

from backend.config import Settings
from backend.store import Store
from backend.tests import connection_guard as G

GO, PAUSED, RELEASE = threading.Event(), threading.Event(), threading.Event()
SEEN = {}
PLACE = {}
real_is_open = G._is_open


def probing(c):
    # The teardown must not read through SQLite a connection another live thread holds: that can deadlock.
    holder = getattr(c, "_held_by", None)
    if isinstance(holder, threading.Thread) and holder is not threading.current_thread() and holder.is_alive():
        SEEN["probed under another thread"] = True
    return real_is_open(c)


G._is_open = probing


def pause():
    PAUSED.set()
    RELEASE.wait(10)


def body(store):
    {body}


def pass_thread():
    GO.wait(10)
    try:
        body(Store(Settings(db_path=PLACE["db"])))
    except Exception as exc:
        SEEN["error"] = repr(exc)


def test_a_tick_starts_a_pass_that_outlives_it(tmp_path):
    PLACE["db"] = tmp_path / "hub.sqlite"
    threading.Thread(target=pass_thread, name="thread-rank", daemon=True).start()


def test_the_pass_has_a_connection_in_hand_during_a_later_test():
    GO.set()
    assert PAUSED.wait(10)


def test_the_pass_goes_on_with_an_open_connection():
    RELEASE.set()
    for thread in threading.enumerate():
        if thread.name == "thread-rank":
            thread.join(10)
    assert SEEN == {seen}
"""

CASES = {
    # Mid-read: the crash. The pass holds a checked-out connection across the later test's teardown.
    "holding": (
        """with store.read() as c:
        pause()
        SEEN["value"] = c.execute("SELECT 1").fetchone()[0]""",
        {"value": 1},
    ),
    # Giving it back: still the pass's while the store rolls it back, so the teardown leaves it alone.
    "giving back": (
        """real = G._store_give

    def paused_give(self, c):
        pause()
        SEEN["open"] = G._is_open(c)
        real(self, c)

    G._store_give = paused_give
    with store.read() as c:
        c.execute("BEGIN")""",
        {"open": True},
    ),
    # Taking it up again from the pool: no holder yet while the pass is inside Store._take.
    "taking up": (
        """with store.read():
        pass
    real = Store._take

    def paused_take(self):
        c = real(self)
        pause()
        return c

    Store._take = paused_take
    with store.read() as c:
        SEEN["value"] = c.execute("SELECT 1").fetchone()[0]""",
        {"value": 1},
    ),
    # Given back on this thread (the garbage collector freeing it here) for a read context another thread opened.
    "giving back another thread's": (
        """checkout = store.read()
    opener = threading.Thread(target=lambda: SEEN.setdefault("c", checkout.__enter__()), name="opener")
    opener.start()
    opener.join()
    del SEEN["c"]
    real = G._store_give

    def paused_give(self, c):
        pause()
        SEEN["open"] = G._is_open(c)
        real(self, c)

    G._store_give = paused_give
    checkout.__exit__(None, None, None)""",
        {"open": True},
    ),
}


@pytest.mark.parametrize("case", list(CASES))
def test_a_connection_an_earlier_tests_thread_has_in_hand_is_neither_closed_nor_blamed(pytester, monkeypatch, case):
    monkeypatch.setenv("PYTHONPATH", str(REPO))
    body, seen = CASES[case]
    pytester.makepyfile(test_lingering=LINGERING.replace("{body}", body).replace("{seen}", repr(seen)))
    result = pytester.runpytest_subprocess("-p", "backend.tests.connection_guard")
    result.assert_outcomes(passed=3)


def test_a_connection_an_earlier_tests_thread_never_gives_back_is_closed_once_it_ends(pytester, monkeypatch):
    """The thread kept the connection and ended: a later teardown closes it and names the test it was opened in."""
    monkeypatch.setenv("PYTHONPATH", str(REPO))
    body = """checkout = store.read()
    SEEN["kept"] = (checkout, checkout.__enter__())  # never returned
    pause()"""
    pytester.makepyfile(test_lingering=LINGERING.replace("{body}", body).replace("{seen}", "SEEN"))
    result = pytester.runpytest_subprocess("-p", "backend.tests.connection_guard")
    result.assert_outcomes(passed=3, errors=1)
    result.stdout.fnmatch_lines(
        [
            "*ERROR at teardown of test_the_pass_goes_on_with_an_open_connection*",
            "*test_the_pass_has_a_connection_in_hand_during_a_later_test opened a pooled database connection*",
        ]
    )


def test_a_read_given_back_while_the_teardown_holds_its_lock_completes(tmp_path):
    """The teardown holds HOLD while it checks and closes. A read context the garbage collector frees then (an abandoned
    one in a reference cycle) gives its connection back on that same thread: a plain lock deadlocked the worker."""
    from backend.config import Settings
    from backend.store import Store
    from backend.tests import connection_guard as G

    store = Store(Settings(db_path=tmp_path / "hub.sqlite"))
    checkout = store.read()
    c = checkout.__enter__()
    with G.HOLD:
        reentered = G.HOLD.acquire(blocking=False)  # a non-reentrant lock answers False here instead of hanging
        if reentered:
            G.HOLD.release()
        assert reentered
        checkout.__exit__(None, None, None)  # what the generator's finalizer does
    assert getattr(c, "_held_by", "unset") == G.POOLED  # back in the pool, idle
    # The same inside a pool operation (_Pool runs Python under the store's pool lock), and with no second lock for two
    # threads to take in opposite orders: the pool lock is HOLD.
    assert store.pool_lock is G.HOLD
    checkout = store.read()
    checkout.__enter__()
    with store.pool_lock:
        checkout.__exit__(None, None, None)
    store.close()
