"""Pooled reads (backend/store.py Store.read): reused, rolled back, and never carrying a caller's state."""
import os
import sqlite3

from backend.config import Settings
from backend.store import Store
from backend import read_cache
from backend.sql import schema_columns


def store(tmp_path):
    s = Store(Settings(db_path=tmp_path / "hub.sqlite"))
    with s.transaction() as c:
        c.execute("CREATE TABLE IF NOT EXISTS t(x)")
        c.execute("INSERT INTO t VALUES(1)")
    return s


def test_reads_reuse_a_connection_left_clean(tmp_path):
    s = store(tmp_path)
    with s.read() as first:
        first.execute("BEGIN")
        assert read_cache.rows(first, "SELECT x FROM t")[0][0] == 1
        enriched = read_cache.value(first, ("synthetic",), lambda: {"allowed": ["ana"]})
        enriched["allowed"].append("ben")
        assert read_cache.value(first, ("synthetic",), lambda: {}) == {"allowed": ["ana"]}
        first.execute("UPDATE t SET x=2")
        assert read_cache.rows(first, "SELECT x FROM t")[0][0] == 2
        first.rollback()
        first.execute("BEGIN")
        assert read_cache.rows(first, "SELECT x FROM t")[0][0] == 1
        first.commit()
        with s.transaction() as writer:
            writer.execute("UPDATE t SET x=3")
        first.execute("BEGIN")
        assert read_cache.rows(first, "SELECT x FROM t")[0][0] == 3
    with s.read() as second:
        assert second is first and not second.in_transaction
        assert second.execute("SELECT x FROM t").fetchone()["x"] == 3
        assert second.read_cache == {}


def test_a_connection_a_caller_changed_is_not_reused(tmp_path):
    s = store(tmp_path)
    with s.read() as first:
        first.create_function("who", 0, lambda: "ana")
    with s.read() as second:
        assert second is not first
        assert second.execute("SELECT count(*) FROM pragma_function_list WHERE name='who'").fetchone()[0] == 0


def test_a_replaced_database_file_drops_the_pool(tmp_path):
    s = store(tmp_path)
    with s.read() as first:
        assert schema_columns(first, s.settings.db_path).get("t") == ("x",)
    copy = tmp_path / "copy.sqlite"
    with sqlite3.connect(copy) as replacement:
        replacement.execute("CREATE TABLE t(y)")   # same schema version, different file/columns
    os.replace(copy, s.settings.db_path)
    with s.read() as second:
        assert second is not first
        assert schema_columns(second, s.settings.db_path).get("t") == ("y",)



def test_failed_writer_releases_admission_and_rolls_back(tmp_path, monkeypatch):
    import pytest
    s = store(tmp_path)
    take = s._take
    def unavailable():
        raise sqlite3.OperationalError("synthetic connection failure")
    monkeypatch.setattr(s, "_take", unavailable)
    with pytest.raises(sqlite3.OperationalError):
        with s.transaction():
            raise AssertionError("unreachable")
    monkeypatch.setattr(s, "_take", take)
    with pytest.raises(KeyboardInterrupt):
        with s.transaction() as c:
            c.execute("UPDATE t SET x=2")
            raise KeyboardInterrupt
    # Failed BEGIN must release admission too, including when SQLite's writer belongs to another process.
    holder = sqlite3.connect(s.settings.db_path, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    borrowed = []
    def no_wait():
        c = take()
        borrowed.append((c, c.execute("PRAGMA busy_timeout").fetchone()[0]))
        c.execute("PRAGMA busy_timeout=0")
        return c
    monkeypatch.setattr(s, "_take", no_wait)
    try:
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            with s.transaction():
                raise AssertionError("unreachable")
    finally:
        holder.rollback()
        holder.close()
        for c, timeout in borrowed:
            c.execute(f"PRAGMA busy_timeout={timeout}")
        monkeypatch.setattr(s, "_take", take)
    # Neither an acquisition failure nor an interrupted transaction can strand the next writer.
    with s.transaction() as c:
        assert c.execute("SELECT x FROM t").fetchone()[0] == 1
        assert c.execute("PRAGMA busy_timeout").fetchone()[0] == 30000
        c.execute("UPDATE t SET x=3")


def test_writer_fifo_timeout_and_cancelled_waiter_keep_the_next_writer_live(monkeypatch):
    from collections import deque
    from queue import Queue
    import threading
    import pytest
    from backend.store import WriterGate
    from backend import store as store_module

    arrivals, acquired = Queue(), Queue()
    class ObservedQueue(deque):
        def append(self, event):
            super().append(event)
            arrivals.put(event)
    gate = WriterGate()
    gate.queue = ObservedQueue()
    gate.acquire()
    arrivals.get(timeout=5)
    release = threading.Event()
    failures = []
    def write(name):
        try:
            gate.acquire()
            try:
                acquired.put(name)
                if name == "request":
                    assert release.wait(5)
            finally:
                gate.release()
        except BaseException as exc:
            failures.append(exc)
    request = threading.Thread(target=write, args=("request",), daemon=True)
    scheduler = threading.Thread(target=write, args=("scheduler",), daemon=True)
    try:
        request.start()
        arrivals.get(timeout=5)
        scheduler.start()
        arrivals.get(timeout=5)
    finally:
        gate.release()
    try:
        assert acquired.get(timeout=5) == "request"
        # The next scheduler tick must remain asleep until the already queued request finishes.
        assert acquired.empty()
    finally:
        release.set()
        request.join(5)
        scheduler.join(5)
    assert not request.is_alive() and not scheduler.is_alive() and not failures
    assert acquired.get(timeout=5) == "scheduler" and not gate.queue

    # Timeout and cancellation remove only their own waiter; the live holder retains admission.
    gate.acquire()
    holder = gate.queue[0]
    gate.timeout = 0
    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        gate.acquire()
    assert list(gate.queue) == [holder]
    class Cancelled:
        def wait(self, timeout):
            raise KeyboardInterrupt
    with monkeypatch.context() as patch:
        patch.setattr(store_module.threading, "Event", Cancelled)
        with pytest.raises(KeyboardInterrupt):
            gate.acquire()
    assert list(gate.queue) == [holder]
    gate.release()
    gate.acquire()
    gate.release()
    assert not gate.queue


def test_test_databases_skip_the_disk_sync_and_servers_keep_theirs(tmp_path, monkeypatch):
    from backend import hubdb as H
    sync = lambda c: c.execute("PRAGMA synchronous").fetchone()[0]  # 0 OFF, 1 NORMAL, 2 FULL
    s = store(tmp_path)
    assert os.environ["TICO_SQLITE_SYNC"] == "off"
    with s.transaction() as c:
        assert sync(c) == 0
    hub = H.connect(tmp_path / "hub.db")
    assert sync(hub) == 0
    hub.close()
    monkeypatch.delenv("TICO_SQLITE_SYNC")
    with Store(Settings(db_path=tmp_path / "hub.sqlite")).transaction() as c:
        assert sync(c) == 1
    hub = H.connect(tmp_path / "hub.db")
    assert sync(hub) == 1
    hub.close()
