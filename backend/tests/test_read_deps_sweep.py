"""The history-read sweep (backend/read_deps.py) beside a busy server: its writes queue at the server's writer gate and
give the lock back after a short while, so a claim or heartbeat waiting there goes before the next one (TK-INSRN53V).
Synthetic rows only."""
from backend import read_deps
from backend.store import H, encode
from backend.tests.test_api import api  # noqa: F401  (fixture)


def backlog(store, n):
    now = H.now()
    with store.transaction() as c:
        c.execute("INSERT INTO conversations(id,kind,participants_json,created) VALUES('history','chat',?,?)",
                  (encode(["human:ana", "bot:ops"]), now))
        for i in range(n):
            c.execute("INSERT INTO messages(id,conversation_id,from_actor,to_actor,kind,body,created,refs_json) "
                      "VALUES(?,'history','human:ana','bot:ops','say','Synthetic history',?,'{}')", (f"old-{i}", now))


class Gate:
    """The server's writer gate, counting each admission."""

    def __init__(self, inner):
        self.inner, self.admitted = inner, 0

    def acquire(self):
        self.inner.acquire()
        self.admitted += 1

    def release(self):
        self.inner.release()


def test_a_long_backlog_is_settled_in_short_writes_each_through_the_writer_gate(api, monkeypatch):
    store = api.app.state.store
    backlog(store, 3 * read_deps.SLICE + 5)
    settled = []
    original = read_deps.settle
    monkeypatch.setattr(read_deps, "settle", lambda conn, keys: settled.append(len(keys)) or original(conn, keys))
    monkeypatch.setattr(read_deps, "HOLD_MS", 0)          # every write gives the lock back after one slice
    gate = Gate(store.writer)
    with store.read() as c:
        walked = read_deps.sweep(c, gate=gate)
        assert c.execute("SELECT count(*) FROM read_dirty").fetchone()[0] == 0
        assert c.execute("SELECT 1 FROM read_index WHERE key='m:old-0'").fetchone()
        assert c.execute("SELECT 1 FROM read_index WHERE key=?", (f"m:old-{3 * read_deps.SLICE + 4}",)).fetchone()
    assert walked >= 3 * read_deps.SLICE + 5
    assert max(settled) <= read_deps.SLICE
    assert gate.admitted == len(settled) and gate.admitted >= 4   # one gate admission per short write


def test_the_hold_budget_still_settles_a_whole_batch_in_one_write_when_it_is_quick(api, monkeypatch):
    store = api.app.state.store
    backlog(store, 2 * read_deps.SLICE)
    monkeypatch.setattr(read_deps, "HOLD_MS", 60_000)
    gate = Gate(store.writer)
    with store.read() as c:
        before = c.execute("SELECT count(*) FROM read_dirty").fetchone()[0]
        read_deps.sweep(c, gate=gate)
        assert c.execute("SELECT count(*) FROM read_dirty").fetchone()[0] == 0
    assert before <= read_deps.BATCH and gate.admitted == 1
