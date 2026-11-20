"""Long-lived background threads (backend/background.py): a Loop's stop wakes and ends its threads, and a later start
runs again on a fresh stop event, as the scheduler's tick (`ensure`) and a test's own start expect."""

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
