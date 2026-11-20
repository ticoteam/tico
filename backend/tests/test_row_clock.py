"""Shared neutral reads (backend/row_clock.py): the private parties and the access registry rows are read once per
change, never once per request, and a write that is not yet committed shares nothing."""

import sqlite3
import threading

import pytest

from backend import row_clock
from backend.config import Settings
from backend.store import H, Store


@pytest.fixture
def store(tmp_path):
    s = Store(Settings(db_path=tmp_path / "hub.sqlite"))
    s.initialize(seed_market=False)
    with s.transaction() as c:
        c.execute(
            "INSERT INTO tasks(id,title,requester_id,owner_id,status,private,created_at,updated_at) "
            "VALUES('secret','S','human:ana','bot:ops','open',1,?,?)",
            (H.now(), H.now()),
        )
        c.execute(
            "INSERT INTO tasks(id,title,requester_id,owner_id,status,private,created_at,updated_at) "
            "VALUES('plain','P','human:ana','bot:ops','open',0,?,?)",
            (H.now(), H.now()),
        )
    return s


@pytest.fixture
def builds(monkeypatch):
    """Count how often the parties statement actually runs (a cache miss)."""
    count = {"n": 0}
    real = row_clock._rows

    def counting(c, statement):
        count["n"] += 1
        return real(c, statement)

    monkeypatch.setattr(row_clock, "_rows", counting)
    return count


def request(s, read):
    with s.read() as c:
        c.execute("BEGIN")
        try:
            return read(c)
        finally:
            c.commit()


def parties(c):
    return {row["id"]: row for row in row_clock.parties(c)}


def _parties_are_read_once_until_a_private_party_changes(store, builds):
    assert set(request(store, parties)) == {"secret"}
    assert builds["n"] == 1
    request(store, parties)
    assert builds["n"] == 1, "a second request reads nothing"

    with store.transaction() as c:
        c.execute("UPDATE tasks SET title='Renamed' WHERE id='secret'")
        c.execute(
            "INSERT INTO tasks(id,title,requester_id,owner_id,status,private,created_at,updated_at) "
            "VALUES('open-plain','O','human:ana','bot:ops','open',0,?,?)",
            (H.now(), H.now()),
        )
    request(store, parties)
    assert builds["n"] == 1, "a title change and a public task do not move the clock"

    with store.transaction() as c:
        c.execute("UPDATE tasks SET requester_id='bot:other' WHERE id='secret'")
    assert parties_requester(store, "secret") == "bot:other"
    assert builds["n"] == 2

    with store.transaction() as c:
        c.execute(
            "INSERT INTO tasks(id,title,requester_id,owner_id,status,private,created_at,updated_at) "
            "VALUES('newly','N','human:ana','bot:ops','open',1,?,?)",
            (H.now(), H.now()),
        )
    assert set(request(store, parties)) == {"secret", "newly"}
    with store.transaction() as c:
        c.execute("DELETE FROM tasks WHERE id='newly'")
    assert set(request(store, parties)) == {"secret"}


def parties_requester(s, tid):
    return request(s, parties)[tid]["requester_id"]


def _an_uncommitted_write_shares_nothing(store, builds):
    request(store, parties)
    with pytest.raises(RuntimeError):
        with store.transaction() as writer:
            writer.execute("UPDATE tasks SET requester_id='bot:ghost' WHERE id='secret'")
            # The writer sees its own change, and the change reaches no other reader.
            assert parties(writer)["secret"]["requester_id"] == "bot:ghost"
            raise RuntimeError("roll back")
    assert parties_requester(store, "secret") == "bot:other"  # the rolled-back requester never reaches a reader


def _access_rows_follow_only_the_access_keys(store, builds):
    with store.transaction() as c:
        c.execute("INSERT INTO registry_metadata VALUES('owner','{\"email\":\"ana@acme.example\"}')")
    first = request(store, row_clock.access_rows)
    assert ("owner", '{"email":"ana@acme.example"}') in first
    with store.transaction() as c:
        c.execute("INSERT INTO registry_metadata VALUES('unrelated','{}')")
    assert request(store, row_clock.access_rows) == first
    with store.transaction() as c:
        c.execute("UPDATE registry_metadata SET value='{\"email\":\"ben@acme.example\"}' WHERE key='owner'")
    assert ("owner", '{"email":"ben@acme.example"}') in request(store, row_clock.access_rows)


def test_shared_party_and_access_reads_follow_only_their_keys_and_never_share_uncommitted_writes(store, builds):
    _parties_are_read_once_until_a_private_party_changes(store, builds)
    _an_uncommitted_write_shares_nothing(store, builds)
    _access_rows_follow_only_the_access_keys(store, builds)


def test_task_ids_and_room_tasks_are_shared_until_a_task_or_room_moves_and_never_uncommitted(store):
    _task_ids_and_room_tasks_are_read_once_until_a_task_or_room_moves(store)
    _an_uncommitted_task_is_never_shared(store)


def index_request(s):
    """ReadIndex's task ids and room tasks in one snapshot, and the whole-table statements SQLite ran for them."""
    from backend import row_clock as rc
    from backend.auth import Identity
    from backend.privacy_index import ReadIndex

    ran = []
    with s.read() as c:
        c.set_trace_callback(lambda sql: ran.append(sql.strip()))
        c.execute("BEGIN")
        try:
            index = ReadIndex(c, Identity("human:ana", "human"))
            found = (set(index.task_ids), dict(index.conversation_tasks))
        finally:
            c.set_trace_callback(None)
            c.commit()
    return found, [sql for sql in ran if sql in (rc.TASK_IDS, rc.ROOM_TASKS)]


def _task_ids_and_room_tasks_are_read_once_until_a_task_or_room_moves(store):
    from backend import row_clock as rc

    (ids, rooms), ran = index_request(store)
    assert ran == [rc.TASK_IDS, rc.ROOM_TASKS] and {"secret", "plain"} <= ids
    assert index_request(store) == ((ids, rooms), []), "a second request reads neither table"

    with store.transaction() as c:
        c.execute("UPDATE tasks SET title='Renamed' WHERE id='secret'")
    assert index_request(store) == ((ids, rooms), []), "a title change moves nothing"

    with store.transaction() as c:
        c.execute(
            "INSERT INTO tasks(id,title,requester_id,owner_id,status,private,created_at,updated_at) "
            "VALUES('newly','N','human:ana','bot:ops','open',0,?,?)",
            (H.now(), H.now()),
        )
        c.execute(
            "INSERT INTO conversations(id,kind,task_id,participants,scope,room_key,created_at) "
            "VALUES('room-new','task','newly','[]','direct',NULL,?)",
            (H.now(),),
        )
    (ids, rooms), ran = index_request(store)
    assert "newly" in ids and rooms["room-new"] == "newly" and ran

    with store.transaction() as c:
        c.execute("UPDATE conversations SET task_id='plain' WHERE id='room-new'")
    (ids, rooms), _ = index_request(store)
    assert rooms["room-new"] == "plain"

    with store.transaction() as c:
        c.execute("DELETE FROM conversations WHERE id='room-new'")
        c.execute("DELETE FROM tasks WHERE id='newly'")
    (ids, rooms), _ = index_request(store)
    assert "newly" not in ids and "room-new" not in rooms


def _an_uncommitted_task_is_never_shared(store):
    from backend.auth import Identity
    from backend.privacy_index import ReadIndex

    index_request(store)
    with pytest.raises(RuntimeError):
        with store.transaction() as writer:
            writer.execute(
                "INSERT INTO tasks(id,title,requester_id,owner_id,status,private,created_at,updated_at) "
                "VALUES('ghost','G','human:ana','bot:ops','open',0,?,?)",
                (H.now(), H.now()),
            )
            assert "ghost" in ReadIndex(writer, Identity("human:ana", "human")).task_ids
            raise RuntimeError("roll back")
    assert "ghost" not in index_request(store)[0][0]
    with store.transaction() as c:
        c.execute(
            "INSERT INTO tasks(id,title,requester_id,owner_id,status,private,created_at,updated_at) "
            "VALUES('real','R','human:ana','bot:ops','open',0,?,?)",
            (H.now(), H.now()),
        )
    ids = index_request(store)[0][0]
    assert "real" in ids and "ghost" not in ids


class _Waits(threading.Event):
    """A flight's done event that counts the readers waiting on it."""

    seen = threading.Condition()
    waiting = 0

    def wait(self, timeout=None):
        with _Waits.seen:
            _Waits.waiting += 1
            _Waits.seen.notify_all()
        return super().wait(timeout)


def _cold_misses(store, monkeypatch, readers, fail_first=False):
    """`readers` requests that miss the parties cache together, while the first one's read is held open until the
    rest are waiting: (each request's rows or exception, the number of reads that ran)."""
    real, release, reads = row_clock._rows, threading.Event(), []

    def held(c, statement):
        reads.append(statement)
        if len(reads) == 1:
            assert release.wait(30), "the waiters never arrived"
            if fail_first:
                raise sqlite3.OperationalError("interrupted")
        return real(c, statement)

    class Flight(row_clock._Flight):
        def __init__(self):
            super().__init__()
            self.done = _Waits()

    monkeypatch.setattr(row_clock, "_rows", held)
    monkeypatch.setattr(row_clock, "_Flight", Flight)
    _Waits.waiting = 0
    out = [None] * readers

    def one(n):
        try:
            out[n] = request(store, row_clock.parties)
        except sqlite3.OperationalError as e:
            out[n] = e

    threads = [threading.Thread(target=one, args=(n,)) for n in range(readers)]
    for t in threads:
        t.start()
    with _Waits.seen:
        assert _Waits.seen.wait_for(lambda: _Waits.waiting >= readers - 1, 30)
    release.set()
    for t in threads:
        t.join(30)
    return out, len(reads)


def test_cold_readers_at_one_clock_read_once_and_a_failed_read_leaves_its_waiters_to_read(store, monkeypatch):
    # Readers that miss together at one clock share one read of the rows, the same rows object each.
    store.row_cache = row_clock.Cache()
    out, reads = _cold_misses(store, monkeypatch, 6)
    assert reads == 1 and all(rows is out[0] for rows in out) and {r["id"] for r in out[0]} == {"secret"}
    assert not store.row_cache.building

    # The first read fails: that request alone sees the error, and one waiter reads in its place for the rest.
    store.row_cache = row_clock.Cache()
    out, reads = _cold_misses(store, monkeypatch, 6, fail_first=True)
    failed = [r for r in out if isinstance(r, Exception)]
    assert len(failed) == 1 and reads == 2
    assert all({r["id"] for r in rows} == {"secret"} for rows in out if rows not in failed)
    assert not store.row_cache.building


def test_a_read_at_an_older_clock_never_replaces_a_newer_entry(store):
    store.row_cache = row_clock.Cache()
    with store.read() as c:
        clock = c.execute("SELECT value FROM row_clock WHERE name='parties'").fetchone()[0]
    newer = ({"id": "from-a-later-snapshot"},)
    store.row_cache.values["parties"] = (clock + 1, newer)
    # This snapshot's clock is older than the published entry: it reads its own rows and leaves the entry alone.
    assert {r["id"] for r in request(store, row_clock.parties)} == {"secret"}
    assert store.row_cache.values["parties"] == (clock + 1, newer)
