"""Long-lived background threads (backend/background.py): a Loop's stop wakes and ends its threads, and a later start
runs again on a fresh stop event, as the scheduler's tick (`ensure`) and a test's own start expect."""

import threading

from backend import background


def test_a_stopped_loop_ends_its_threads_and_starts_again_on_a_new_event():
    rung = []

    def work(halt):
        halt.wait()

    loop = background.Loop("test-loop", lambda: rung.append(1))
    try:
        loop.start(work, count=2)
        threads = list(loop.threads)
        assert [t.name for t in threads] == ["test-loop-0", "test-loop-1"]
        assert all(t.daemon for t in threads) and {"test-loop-0", "test-loop-1"} <= set(background.running())
        loop.start(work, count=2)
        assert loop.threads == threads, "a start while they run starts none"

        loop.stop()
        assert not any(t.is_alive() for t in threads) and rung == [1] and loop.stopped()

        loop.start(work)
        assert not loop.stopped() and loop.threads[0].is_alive(), "a start after the stop runs on a new event"
    finally:
        loop.stop()
        background._loops.remove(loop)


class _WatchedLock:
    """A Loop's lock that says when a thread named `who` has come to take it, so a test can hold it and know the
    other thread is waiting on it rather than guess with a sleep."""

    def __init__(self, who: str) -> None:
        self.who, self.inner, self.reached = who, threading.Lock(), threading.Event()

    def __enter__(self) -> "_WatchedLock":
        if threading.current_thread().name == self.who:
            self.reached.set()
        self.inner.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.inner.release()


def test_a_stop_during_a_start_waits_for_it_and_covers_the_threads_it_started():
    stops = []
    loop = background.Loop("test-loop-race", lambda: None)
    loop.lock = _WatchedLock("test-stop")  # pyright: ignore[reportAttributeAccessIssue]
    stopper = threading.Thread(target=lambda: stops.append(loop.signal()), name="test-stop")

    def before():  # runs under the loop's lock, just before the start's threads
        stopper.start()
        assert loop.lock.reached.wait(5), "the stop did not take the loop's lock, so it ran in the middle of a start"
        assert not loop.halt.is_set(), "the stop set the event while a start held the lock"

    try:
        loop.start(lambda halt: halt.wait(), count=2, before=before)
        stopper.join()
        assert len(stops) == 1 and [t.name for t in stops[0]] == ["test-loop-race-0", "test-loop-race-1"], (
            "the stop returns both threads the start made"
        )
        background.join(stops[0], 5)
        assert loop.stopped() and not any(t.is_alive() for t in stops[0])
    finally:
        loop.stop()
        background._loops.remove(loop)


def test_a_start_while_a_timed_out_stop_drains_runs_once_the_old_thread_ends(monkeypatch):
    release, joining = threading.Event(), threading.Event()
    real_join = background.join

    def watched_join(threads, timeout):  # the real join, telling the test when the restart thread waits in it
        if threading.current_thread().name == "test-loop-stubborn-restart":
            joining.set()
        real_join(threads, timeout)

    monkeypatch.setattr(background, "join", watched_join)
    loop = background.Loop("test-loop-stubborn", lambda: None)
    try:
        loop.start(lambda halt: release.wait())  # ignores its stop event, like a worker stuck in a slow call
        first = loop.threads[0]
        loop.stop(timeout=0)  # the join gives up at once
        assert first.is_alive() and loop.threads == [first], "the stop forgot a thread that is still running"

        old_alive_at_start = []
        started = threading.Event()

        def replacement(halt):
            old_alive_at_start.append(first.is_alive())
            started.set()
            halt.wait()

        loop.start(replacement)  # the next lifespan's start, the only one it makes
        waiter = getattr(loop, "waiter", None)  # None on a Loop that has no restart thread
        assert loop.threads == [first] and background.running().count("test-loop-stubborn") == 1, (
            "a start ran a second copy beside the one the stop gave up on"
        )
        assert not loop.stopped(), "a start is asked for, so ensure() must not read the Loop as stopped"

        assert joining.wait(5), "the waiting start does not wait for the old thread to exit"
        release.set()
        assert started.wait(5), "the start that waited did not run once the old thread ended"
        assert old_alive_at_start == [False], "the replacement started before the old thread had exited"
        if waiter is not None:
            waiter.join()  # it has finished adding the replacement to the list
        assert len(loop.threads) == 1 and loop.threads[0] is not first
        assert loop.threads[0].is_alive() and not loop.halt.is_set() and not loop.stopped()
    finally:
        release.set()
        loop.stop()
        background._loops.remove(loop)


def test_a_stop_after_that_start_cancels_it():
    release = threading.Event()
    loop = background.Loop("test-loop-cancelled", lambda: None)
    try:
        loop.start(lambda halt: release.wait())
        first = loop.threads[0]
        loop.stop(timeout=0)
        loop.start(lambda halt: halt.wait())
        loop.stop(timeout=0)  # the next shutdown, before the old thread ends

        waiter = getattr(loop, "waiter", None)  # None on a Loop that has no restart thread
        release.set()
        first.join()
        if waiter is not None:
            waiter.join()  # whatever watched the old thread has decided
        assert not any(t.is_alive() for t in loop.threads) and loop.stopped(), "the cancelled start ran after all"
        assert "test-loop-cancelled" not in background.running()
    finally:
        release.set()
        loop.stop()
        background._loops.remove(loop)
