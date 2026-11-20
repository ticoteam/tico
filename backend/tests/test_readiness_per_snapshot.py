"""A computer's stored readiness text is parsed once a snapshot (read_cache.readiness) and shared by its readers there;
store.readiness_document is a new document on every call, for a caller that changes it. No memo outlives a snapshot."""

import sqlite3
from contextlib import closing

import orjson

from backend import read_cache
from backend.config import Settings
from backend.store import Store, readiness_document

TEXT = orjson.dumps({"schema_version": 1, "bots": {"ops": {"ready": True}}, "runtimes": {}}).decode()


def _store(tmp_path):
    path = tmp_path / "hub.sqlite"
    with closing(sqlite3.connect(path)) as c:
        c.execute("CREATE TABLE t(x)")
        c.commit()
    return Store(Settings(db_path=path))


def test_parsed_once_a_snapshot_and_again_after_a_write(tmp_path):
    s = _store(tmp_path)
    with s.read() as c:
        outside = read_cache.readiness(c, "r1", TEXT)
        assert outside == readiness_document(TEXT)
        assert read_cache.readiness(c, "r1", TEXT) is not outside  # no snapshot: parsed each time
        c.execute("BEGIN")
        first = read_cache.readiness(c, "r1", TEXT)
        assert read_cache.readiness(c, "r1", TEXT) is first
        assert read_cache.readiness(c, "r2", TEXT) is not first  # another computer
        c.execute("INSERT INTO t VALUES(1)")  # a write in the snapshot drops what was read
        assert read_cache.readiness(c, "r1", TEXT) is not first
        c.execute("ROLLBACK")


def test_every_call_of_readiness_document_is_the_callers_own():
    first = readiness_document(TEXT)
    first["bots"]["ops"]["ready"] = False
    del first["runtimes"]
    assert readiness_document(TEXT) == {"schema_version": 1, "bots": {"ops": {"ready": True}}, "runtimes": {}}
    legacy = orjson.dumps({"ops": True}).decode()
    assert readiness_document(legacy)["bots"]["ops"]["ready"] is True
    for value in ("", "not json", b"null", None):
        assert readiness_document(value) == {"schema_version": 0, "runtimes": {}, "bots": {}}
