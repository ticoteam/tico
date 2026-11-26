"""The server's own background workers (backend/lifespan_workers.py, started by backend/app.py's lifespan): a worker that
dies is never silent, a start that fails part way gives back what it started, and the way out always closes the store,
whatever a worker did."""

import asyncio
import contextlib
import logging
import threading
from datetime import datetime, timezone

from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from backend import invariants, lifespan_workers, observability
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


class Telemetry:
    """Telemetry's capture, keeping the event it would send (built as backend/observability.py builds it)."""

    def __init__(self):
        self.sent = []

    def capture(self, source, exc=None, *args, **kwargs):
        self.sent.append(observability.sanitize_event({"tags": {"source": source}}))  # None: dropped


def test_a_worker_that_dies_is_logged_filed_for_agent_ops_and_shows_on_health(tmp_path, monkeypatch, caplog):
    app, _ = built(tmp_path, monkeypatch)
    store = app.state.store
    with store.transaction() as c:  # Agent Ops, who takes the alert
        c.execute(
            "INSERT OR IGNORE INTO bots(id,slug,display_name,state) VALUES('botops','botops','Agent Ops','active')"
        )
    telemetry = Telemetry()
    monkeypatch.setattr(observability.Observability, "capture", lambda self, *a, **k: telemetry.capture(*a, **k))

    async def dies(stop):
        raise RuntimeError("inbound broke")

    monkeypatch.setattr(app.state.inbound, "loop", dies)
    recorded = threading.Event()
    record = lifespan_workers.record

    def record_then_tell(*args):
        record(*args)
        recorded.set()

    monkeypatch.setattr(lifespan_workers, "record", record_then_tell)
    filed = threading.Event()
    file_check_now = check_health.file_check_now

    def file_then_tell(*args):
        try:
            return file_check_now(*args)
        finally:
            filed.set()

    monkeypatch.setattr(check_health, "file_check_now", file_then_tell)
    with caplog.at_level(logging.ERROR, logger="tico.lifespan_workers"):
        with served(app):
            assert recorded.wait(WAY_OUT_SECONDS), "the dead worker was recorded"
            logged = [r for r in caplog.records if r.levelno >= logging.ERROR and "inbound" in r.getMessage()]
            assert logged and logged[0].exc_info, "the death is logged as an error, with its traceback"
            kept = [event["tags"]["source"] for event in telemetry.sent if event]
            assert "worker_inbound" in kept, "telemetry's sanitizer keeps the death's event"
            (hit,) = recorded_deaths(store)
            assert hit.startswith("inbound: RuntimeError")
            assert "inbound broke" not in hit, "the record names the error's type, never its message"

            # Filed for Agent Ops at once, without the Maintenance run (on the scheduler, which may be what died).
            assert filed.wait(WAY_OUT_SECONDS), "the death was filed"
            assert not [r for r in caplog.records if "could not be" in r.getMessage()]
            with store.read() as c:
                row = c.execute("SELECT present,pending FROM health_findings WHERE key='inv:worker_died'").fetchone()
                titles = [r[0] for r in c.execute("SELECT title FROM tasks WHERE owner_id='bot:botops'")]
                # Health reads it live, with no sweep of the production checks since the death.
                assert [row["id"] for row in invariants.health_checks(c)] == ["invariant:worker_died"]
            assert tuple(row) == (1, 0), "filed now, not pending a second run"
            assert any("Background worker stopped" in t for t in titles), titles

            # A production check like the others: the sweep counts it, and Check health's own run reads it as
            # `inv:worker_died`, filed on its first run.
            at = datetime.now(timezone.utc)
            assert invariants.sweep(store, None, store.settings, at)["worker_died"] == 1
            with store.read() as c:
                _, found = check_health._invariant_findings(c, H.now(), at, {})
            assert [f["key"] for f in found] == ["inv:worker_died"]
            assert "inbound" in found[0]["detail"]
            assert "❓" in found[0]["fix"] and "nothing to do by hand" not in found[0]["fix"], (
                "Agent Ops asks a person to restart: no run clears it"
            )
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


class Gone:
    """A store nothing should touch: the unit tests below record deaths through a patched `record`."""

    def transaction(self):
        raise AssertionError("the store was used")


def test_a_worker_name_is_one_telemetry_keeps():
    longest = "a" * 25
    assert observability.sanitize_event({"tags": {"source": f"worker_{longest}"}}) is not None

    async def main():
        workers = lifespan_workers.Workers(Gone(), Telemetry())

        async def work():
            pass

        with pytest.raises(ValueError, match="lowercase letters and underscores"):
            workers.task("screen-relay", work())
        with pytest.raises(ValueError):
            workers.task(longest + "a", work())

    asyncio.run(main())


def died(workers, on_death=None):
    """A worker that dies at once; returns when its death has been seen (logged, and its record begun)."""

    async def main():
        async def dies():
            raise RuntimeError("inbound broke")

        task = workers.task("inbound", dies())
        await asyncio.wait([task])  # its done-callback, registered first, has run
        await workers.shutdown()  # waits for the death's record

    asyncio.run(main())


def test_a_death_whose_record_fails_for_a_moment_is_tried_again_then_filed(monkeypatch):
    monkeypatch.setattr(lifespan_workers, "RECORD_TRIES", (0, 0))
    tries = []

    def busy_twice(store, name, error):
        tries.append((name, error))
        if len(tries) < 3:
            raise RuntimeError("database is locked")

    monkeypatch.setattr(lifespan_workers, "record", busy_twice)
    filed = threading.Event()
    died(lifespan_workers.Workers(Gone(), Telemetry(), on_death=filed.set))
    assert tries == [("inbound", "RuntimeError")] * 3
    assert filed.is_set(), "filed once the record was written"


def test_a_death_that_cannot_be_recorded_is_logged_as_an_error(monkeypatch, caplog):
    monkeypatch.setattr(lifespan_workers, "RECORD_TRIES", (0, 0))

    def always_busy(store, name, error):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(lifespan_workers, "record", always_busy)
    filed = threading.Event()
    with caplog.at_level(logging.ERROR, logger="tico.lifespan_workers"):
        died(lifespan_workers.Workers(Gone(), Telemetry(), on_death=filed.set))
    assert any("could not be recorded" in r.getMessage() and r.exc_info for r in caplog.records)
    assert not filed.is_set(), "nothing recorded, so nothing for Health to file"


def test_a_cancelled_way_out_still_waits_for_the_workers_before_the_cleanups():
    order = []

    async def main():
        stop, let_go = asyncio.Event(), asyncio.Event()
        workers = lifespan_workers.Workers(Gone(), Telemetry())

        async def slow():
            await stop.wait()
            await let_go.wait()
            order.append("worker ended")

        workers.task("slow", slow())
        workers.on_stop("slow", stop.set)
        workers.on_exit("store", lambda: order.append("cleanups"))
        way_out = asyncio.create_task(workers.shutdown())
        await stop.wait()
        way_out.cancel()
        for _ in range(10):
            await asyncio.sleep(0)  # the cancel reaches the way out
        order.append("cancelled")
        let_go.set()
        with pytest.raises(asyncio.CancelledError):
            await way_out

    asyncio.run(main())
    assert order == ["cancelled", "worker ended", "cleanups"], "the cancel waited for the worker, then was passed on"


def test_the_way_out_waits_for_a_thread_without_the_default_pool_it_fills(monkeypatch, caplog):
    monkeypatch.setattr(lifespan_workers, "SHUTDOWN_SECONDS", 0.1)
    monkeypatch.setattr(lifespan_workers, "THREAD_GRACE_SECONDS", 0.1)
    began, release = threading.Event(), threading.Event()

    def stuck():
        began.set()
        release.wait(WAY_OUT_SECONDS * 4)

    async def main():
        loop = asyncio.get_running_loop()
        loop.set_default_executor(ThreadPoolExecutor(max_workers=1))  # the stuck thread fills it
        workers = lifespan_workers.Workers(Gone(), Telemetry())
        workers.thread("stuck", stuck)
        with ThreadPoolExecutor(max_workers=1) as side:
            assert await loop.run_in_executor(side, began.wait, WAY_OUT_SECONDS)
        try:
            await workers.shutdown()
        finally:
            release.set()  # so asyncio.run's own close of the pool ends

    try:
        with caplog.at_level(logging.ERROR, logger="tico.lifespan_workers"):
            bounded(lambda: asyncio.run(main()), "way out")
        said = " ".join(r.getMessage() for r in caplog.records)
        assert "still running after the way out" in said and "['stuck']" in said
    finally:
        release.set()
