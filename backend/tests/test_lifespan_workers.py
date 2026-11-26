"""The server's own background workers (backend/lifespan_workers.py, started by backend/app.py's lifespan): a worker that
dies is never silent, a start that fails part way gives back what it started, and the way out always closes the store,
whatever a worker did."""

import asyncio
import contextlib
import logging
import threading
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend import invariants, lifespan_workers
from backend.app import create_app
from backend.maintenance_steps import check_health
from backend.store import H
from backend.tests.helpers.api import api_settings


def built(tmp_path, monkeypatch):
    """The app, with each run of its store's close recorded (it still closes)."""
    app = create_app(api_settings(tmp_path))
    store = app.state.store
    closed = []
    close = store.close

    def recorded_close():
        closed.append(True)
        close()

    monkeypatch.setattr(store, "close", recorded_close)
    return app, closed


WAY_OUT_SECONDS = 30  # a way out that takes longer is hung: some worker was never told to stop


def bounded(step, what):
    """`step()` on a thread of its own, failing the test rather than hanging it when it never ends."""
    errors = []

    def run():
        try:
            step()
        except BaseException as exc:  # noqa: BLE001 - handed back to the test below
            errors.append(exc)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(WAY_OUT_SECONDS)
    assert not thread.is_alive(), f"the server's {what} hung: a worker was never told to stop"
    if errors:
        raise errors[0]


@contextlib.contextmanager
def served(app):
    """The app served, its lifespan run in and out, each bounded (`bounded`)."""
    client = TestClient(app)
    bounded(client.__enter__, "start")
    try:
        yield client
    finally:
        bounded(lambda: client.__exit__(None, None, None), "way out")


def recorded_deaths(store):
    with store.read() as c:
        return lifespan_workers.dead(c)


def test_a_worker_that_dies_is_logged_and_shows_on_health_for_check_health_to_file(tmp_path, monkeypatch, caplog):
    app, _ = built(tmp_path, monkeypatch)
    store = app.state.store

    async def dies(stop):
        raise RuntimeError("inbound broke")

    monkeypatch.setattr(app.state.inbound, "loop", dies)
    recorded = threading.Event()
    record = lifespan_workers.record

    def record_then_tell(*args):
        record(*args)
        recorded.set()

    monkeypatch.setattr(lifespan_workers, "record", record_then_tell)
    with caplog.at_level(logging.ERROR, logger="tico.lifespan_workers"):
        with served(app):
            assert recorded.wait(WAY_OUT_SECONDS), "the dead worker was recorded"
            logged = [r for r in caplog.records if r.levelno >= logging.ERROR and "inbound" in r.getMessage()]
            assert logged and logged[0].exc_info, "the death is logged as an error, with its traceback"
            (hit,) = recorded_deaths(store)
            assert hit.startswith("inbound: RuntimeError")
            assert "inbound broke" not in hit, "the record names the error's type, never its message"

            # A production check like the others: Health lists it, and Check health reads it as `inv:worker_died`,
            # filed on its first run.
            at = datetime.now(timezone.utc)
            assert invariants.sweep(store, None, store.settings, at)["worker_died"] == 1
            with store.read() as c:
                assert [row["id"] for row in invariants.health_checks(c)] == ["invariant:worker_died"]
                _, found = check_health._invariant_findings(c, H.now(), at, {})
            assert [f["key"] for f in found] == ["inv:worker_died"]
            assert "inbound" in found[0]["detail"]
            assert check_health.tag_of("inv:worker_died") == "urgent"

    # The next start runs every worker again, so the record is cleared and the check passes.
    monkeypatch.delattr(app.state.inbound, "loop")
    with served(app):
        assert recorded_deaths(store) == []


def test_a_worker_that_raises_on_its_way_out_still_closes_the_store(tmp_path, monkeypatch):
    app, closed = built(tmp_path, monkeypatch)

    async def raises_at_stop(stop):
        await stop.wait()
        raise RuntimeError("inbound broke on the way out")

    monkeypatch.setattr(app.state.inbound, "loop", raises_at_stop)
    with served(app):
        pass  # leaving the block shuts the app down; the worker's error must not escape it
    assert len(closed) == 1, "store.close() ran"
    assert app.state.auth_pool is None


def test_a_worker_that_ignores_its_stop_is_cancelled_at_the_deadline_and_the_store_still_closes(
    tmp_path, monkeypatch, caplog
):
    app, closed = built(tmp_path, monkeypatch)
    monkeypatch.setattr(lifespan_workers, "SHUTDOWN_SECONDS", 0.3)
    monkeypatch.setattr(lifespan_workers, "THREAD_GRACE_SECONDS", 0.2)
    release = threading.Event()
    thread_ended = threading.Event()

    async def never_stops(stop):
        await asyncio.Event().wait()

    def thread_never_stops():
        try:
            release.wait(10)
        finally:
            thread_ended.set()

    monkeypatch.setattr(app.state.inbound, "loop", never_stops)
    monkeypatch.setattr(app.state.file_metadata, "loop", thread_never_stops)
    thread_at_close = []
    close = app.state.store.close

    def close_then_release():
        thread_at_close.append("ended" if thread_ended.is_set() else "running")
        close()
        release.set()  # the left-behind thread may end now: the test need not wait out its ten seconds

    monkeypatch.setattr(app.state.store, "close", close_then_release)
    try:
        with caplog.at_level(logging.ERROR, logger="tico.lifespan_workers"):
            with served(app):
                pass
        assert len(closed) == 1, "store.close() ran though two workers never stopped"
        # The thread is held until the store closes: had the way out waited for it, it would never have ended.
        assert thread_at_close == ["running"], "the cancelled await left its thread running when the store closed"
        said = " ".join(r.getMessage() for r in caplog.records)
        assert "cancelled: ['inbound', 'media']" in said, "both late workers are named in the log"
        assert "still running after the way out" in said and "['media']" in said, "and so is the thread left behind"
    finally:
        release.set()
        thread_ended.wait(5)


def test_a_start_that_fails_part_way_stops_what_it_started_and_closes_the_store(tmp_path, monkeypatch):
    app, closed = built(tmp_path, monkeypatch)
    seen_stop = []

    async def inbound(stop):
        await stop.wait()
        seen_stop.append(True)

    monkeypatch.setattr(app.state.inbound, "loop", inbound)
    from backend import needs_lane

    def lane_fails(app):
        raise RuntimeError("the last step of the start failed")

    monkeypatch.setattr(needs_lane, "start", lane_fails)
    with pytest.raises(RuntimeError, match="last step of the start"):
        with served(app):
            pass
    assert seen_stop == [True], "a worker started before the failure was stopped, not left running"
    assert len(closed) == 1, "store.close() ran"
    assert app.state.auth_pool is None
