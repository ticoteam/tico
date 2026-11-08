"""The flight recorder drains its samples while a database write waits on the write lock."""
import sqlite3
import threading
import time
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from backend import flight

MINUTE = 1_700_000_040


def test_samples_keep_draining_while_a_flush_waits_on_the_write_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(flight, "BUFFER", 2000)
    rec = flight.Recorder()
    rec.start_drain(0.002)
    entered, release = threading.Event(), threading.Event()

    def blocked_write(self, work, minute=None):
        entered.set()
        release.wait(10)                    # a table write behind a long writer hold
        return True

    monkeypatch.setattr(flight.Flight, "write_db", blocked_write)
    sampler = flight.Flight(rec, None, SimpleNamespace(db_path=tmp_path / "hub.db"), folder=tmp_path)
    for _ in range(100):
        rec.request("GET /api/v2/tasks", "human", 200, 5.0)
    flusher = threading.Thread(target=sampler.flush, args=(MINUTE,), daemon=True)
    try:
        flusher.start()
        assert entered.wait(5)
        # Five times the buffer arrives while the flush is blocked; only the drain thread can keep up with it.
        for _ in range(50):
            for _ in range(100):
                rec.request("GET /api/v2/tasks", "bot", 200, 5.0, 10, "bot:x")
            time.sleep(0.002)
    finally:
        release.set()
        flusher.join(timeout=5)
        rec.stop_drain()
    taken = rec.take(MINUTE + 60)
    assert taken["process"][16] == 0                                # nothing dropped
    assert sum(row[4] for row in taken["requests"]) == 5000         # every sample is in the minute's counts


class CountingStore:
    """A store stand-in: every read() is its own connection, and the reads are counted."""

    def __init__(self, path):
        self.settings = SimpleNamespace(db_path=path)
        self.reads = 0
        self.statements = []

    @contextmanager
    def read(self):
        self.reads += 1
        c = sqlite3.connect(str(self.settings.db_path))
        c.set_trace_callback(self.statements.append)
        try:
            yield c
        finally:
            c.close()


def test_the_size_pass_reads_in_short_snapshots_and_keeps_its_shape(tmp_path):
    path = tmp_path / "hub.db"
    c = sqlite3.connect(path, isolation_level=None)
    c.execute("CREATE TABLE messages(id INTEGER PRIMARY KEY, created_at INTEGER, body TEXT)")
    c.execute("CREATE INDEX messages_created ON messages(created_at)")
    c.executemany("INSERT INTO messages(created_at, body) VALUES(?, ?)", [(i, "x" * 200) for i in range(500)])
    for i in range(60):
        c.execute(f"CREATE TABLE extra{i}(x)")
    c.close()
    store = CountingStore(path)
    try:
        size, wal, free, detail = flight.db_stats(store)
    except sqlite3.OperationalError:
        pytest.skip("this SQLite has no dbstat")
    assert size == path.stat().st_size and wal == 0 and free >= 0
    assert set(detail) == {"objects", "rows", "oldest"}
    assert len(detail["objects"]) == 40 and all(isinstance(pages, int) and pages > 0 for pages in detail["objects"].values())
    assert max(detail["objects"], key=detail["objects"].get) == "messages"
    assert detail["rows"] == {"messages": 500} and detail["oldest"] == {"messages": 0}
    # One read for the file's table list, then one per table with its indexes (61 tables and the schema), two for messages.
    assert store.reads >= 60
    assert not any("dbstat" in sql for sql in store.statements)     # estimated from sampled rows, no page walked

    before = store.reads
    _, _, _, light = flight.db_stats(store, sizes=False)
    assert light["objects"] is None and light["rows"] == {"messages": 500}
    assert store.reads - before < 6


def test_the_size_estimate_follows_rows_and_record_size(tmp_path):
    path = tmp_path / "sized.db"
    c = sqlite3.connect(path, isolation_level=None)
    c.execute("CREATE TABLE log(id INTEGER PRIMARY KEY, body TEXT)")
    c.execute("CREATE INDEX log_body ON log(body)")
    c.executemany("INSERT INTO log(body) VALUES(?)", [("x" * 400,)] * 3000)
    c.execute("CREATE TABLE kept(k TEXT PRIMARY KEY, v TEXT) WITHOUT ROWID")
    c.executemany("INSERT INTO kept VALUES(?, ?)", [(f"k{i:04d}", "y" * 100) for i in range(500)])
    c.execute("CREATE TABLE empty_one(a)")
    c.close()
    _, _, _, detail = flight.db_stats(CountingStore(path))
    logical = 3000 * 400                      # the body alone; the rowid and record headers add a little more
    assert 0.9 * logical < detail["objects"]["log"] < 1.5 * logical
    assert 0.9 * logical < detail["objects"]["log_body"] < 1.5 * logical
    assert detail["objects"]["kept"] > 500 * 100
    assert detail["objects"]["empty_one"] == 4096          # an empty table is one page
