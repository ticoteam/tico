"""The flight recorder drains its samples while a database write waits on the write lock."""

import sqlite3
from contextlib import contextmanager
from types import SimpleNamespace


from backend import flight

MINUTE = 1_700_000_040


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
    logical = 3000 * 400  # the body alone; the rowid and record headers add a little more
    assert 0.9 * logical < detail["objects"]["log"] < 1.5 * logical
    assert 0.9 * logical < detail["objects"]["log_body"] < 1.5 * logical
    assert detail["objects"]["kept"] > 500 * 100
    assert detail["objects"]["empty_one"] == 4096  # an empty table is one page
