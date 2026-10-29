"""Shared neutral reads (backend/row_clock.py): the private parties and the access registry rows are read once per
change, never once per request, and a write that is not yet committed shares nothing."""
import pytest

from backend import row_clock
from backend.config import Settings
from backend.store import H, Store


@pytest.fixture
def store(tmp_path):
    s = Store(Settings(db_path=tmp_path / "hub.sqlite"))
    s.initialize(seed_market=False)
    with s.transaction() as c:
        c.execute("INSERT INTO tasks(id,title,requester,owner,status,private,created,updated) "
                  "VALUES('secret','S','human:ana','bot:ops','open',1,?,?)", (H.now(), H.now()))
        c.execute("INSERT INTO tasks(id,title,requester,owner,status,private,created,updated) "
                  "VALUES('plain','P','human:ana','bot:ops','open',0,?,?)", (H.now(), H.now()))
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


def test_parties_are_read_once_until_a_private_party_changes(store, builds):
    assert set(request(store, parties)) == {"secret"}
    assert builds["n"] == 1
    request(store, parties)
    assert builds["n"] == 1, "a second request reads nothing"

    with store.transaction() as c:
        c.execute("UPDATE tasks SET status='done' WHERE id='secret'")
        c.execute("INSERT INTO tasks(id,title,requester,owner,status,private,created,updated) "
                  "VALUES('open-plain','O','human:ana','bot:ops','open',0,?,?)", (H.now(), H.now()))
    request(store, parties)
    assert builds["n"] == 1, "status changes and public tasks do not move the clock"

    with store.transaction() as c:
        c.execute("UPDATE tasks SET owner='bot:other' WHERE id='secret'")
    assert parties_owner(store, "secret") == "bot:other"
    assert builds["n"] == 2

    with store.transaction() as c:
        c.execute("INSERT INTO tasks(id,title,requester,owner,status,private,created,updated) "
                  "VALUES('newly','N','human:ana','bot:ops','open',1,?,?)", (H.now(), H.now()))
    assert set(request(store, parties)) == {"secret", "newly"}
    with store.transaction() as c:
        c.execute("DELETE FROM tasks WHERE id='newly'")
    assert set(request(store, parties)) == {"secret"}


def parties_owner(s, tid):
    return request(s, parties)[tid]["owner"]


def test_an_uncommitted_write_shares_nothing(store, builds):
    request(store, parties)
    with pytest.raises(RuntimeError):
        with store.transaction() as writer:
            writer.execute("UPDATE tasks SET owner='bot:ghost' WHERE id='secret'")
            # The writer sees its own change, and the change reaches no other reader.
            assert parties(writer)["secret"]["owner"] == "bot:ghost"
            raise RuntimeError("roll back")
    assert parties_owner(store, "secret") == "bot:ops"


def test_access_rows_follow_only_the_access_keys(store, builds):
    with store.transaction() as c:
        c.execute("INSERT INTO registry_metadata VALUES('owner','{\"email\":\"ana@acme.example\"}')")
    first = request(store, row_clock.access_rows)
    assert ("owner", '{"email":"ana@acme.example"}') in first
    with store.transaction() as c:
        c.execute("INSERT INTO registry_metadata VALUES('unrelated','{}')")
    assert request(store, row_clock.access_rows) == first
    with store.transaction() as c:
        c.execute("UPDATE registry_metadata SET value_json='{\"email\":\"ben@acme.example\"}' WHERE key='owner'")
    assert ("owner", '{"email":"ben@acme.example"}') in request(store, row_clock.access_rows)

