"""The history-read sweep (backend/read_deps.py) beside a busy server: its writes queue at the server's writer gate and
give the lock back after a short while, so a claim or heartbeat waiting there goes before the next one (TK-INSRN53V).
Synthetic rows only."""
from backend import read_deps
from backend.store import H, encode
from backend.tests.test_api import api  # noqa: F401  (fixture)


def backlog(store, n):
    now = H.now()
    with store.transaction() as c:
        c.execute("INSERT INTO conversations(id,kind,participants,created_at) VALUES('history','chat',?,?)",
                  (encode(["human:ana", "bot:ops"]), now))
        for i in range(n):
            c.execute("INSERT INTO messages(id,conversation_id,sender_id,recipient_id,kind,body,created_at,refs) "
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
    monkeypatch.setattr(read_deps, "settle",
                        lambda conn, keys, walk=None: settled.append(len(keys)) or original(conn, keys, walk))
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


def test_no_upsert_writes_a_table_whose_triggers_mark_history_reads(api):
    """SQLite runs the triggers of an upsert's update under the upsert's own conflict rule, not their INSERT OR IGNORE:
    a row already marked fails the whole write. One such upsert in the claim fails every claim that moves an input, and
    no bot starts, so a table these triggers watch is written with an update and an INSERT OR IGNORE instead."""
    import re
    from pathlib import Path
    with api.app.state.store.read() as c:
        watched = {r[0] for r in c.execute("SELECT tbl_name FROM sqlite_master WHERE type='trigger' AND sql LIKE "
                                           "'%read_dirty%'")}
    assert {"attempt_inputs", "messages", "attempts", "jobs"} <= watched
    upsert = re.compile(r"INTO\s+(\w+)\b[^;]{0,600}?ON CONFLICT\s*(?:\([^)]*\))?\s*DO UPDATE", re.S)
    found = []
    for path in sorted((Path(read_deps.__file__).parent).glob("*.py")):
        source = re.sub(r'"\s*\n\s*(?:f|rf|fr)?"', "", path.read_text())   # join SQL split across lines
        found += [f"{path.name}: {m.group(1)}" for m in upsert.finditer(source) if m.group(1) in watched]
    assert found == []


def test_a_later_pass_searches_only_past_the_rows_already_searched_and_still_settles_new_rows(api):
    """The loop's pass every few seconds: history already walked is not searched again, and a row written since is
    settled through the queue its insert trigger fills."""
    store = api.app.state.store
    backlog(store, 5)
    searched, statements = {}, []
    with store.read() as c:
        read_deps.sweep(c, searched=searched)
        top = c.execute("SELECT max(rowid) FROM messages").fetchone()[0]
    assert searched["messages"] == top
    with store.transaction() as c:
        c.execute("INSERT INTO messages(id,conversation_id,sender_id,recipient_id,kind,body,created_at,refs) "
                  "VALUES('new-0','history','human:ana','bot:ops','say','Synthetic history',?,'{}')", (H.now(),))
    with store.read() as c:
        c.set_trace_callback(statements.append)
        read_deps.sweep(c, searched=searched)
        c.set_trace_callback(None)
        assert c.execute("SELECT 1 FROM read_index WHERE key='m:new-0'").fetchone()
        assert c.execute("SELECT count(*) FROM read_dirty").fetchone()[0] == 0
    searches = [s for s in statements if "FROM read_deps d WHERE d.key='m:'" in s]
    assert searches and all(f"rowid>{top} " in s for s in searches)


def test_a_run_is_walked_once_per_write_and_every_message_naming_it_still_depends_on_it(api, monkeypatch):
    """Every message of a turn names its run, and the run's state changes re-queue them all: the run's events are read
    once for the write, not once per message, and each message still records the run's nodes."""
    from backend.privacy_index import ReadIndex
    store = api.app.state.store
    now = H.now()
    with store.transaction() as c:
        c.execute("INSERT INTO conversations(id,kind,participants,created_at) VALUES('turn-room','chat',?,?)",
                  (encode(["human:ana", "bot:ops"]), now))
        for i in range(12):
            c.execute("INSERT INTO messages(id,conversation_id,sender_id,recipient_id,kind,body,created_at,refs) "
                      "VALUES(?,'turn-room','bot:ops','human:ana','say','Synthetic reply',?,?)",
                      (f"reply-{i}", now, encode({"turn_id": "run-1"})))
    walked, original = [], ReadIndex._attempt_sources
    monkeypatch.setattr(ReadIndex, "_attempt_sources", lambda self, aid: walked.append(aid) or original(self, aid))
    monkeypatch.setattr(read_deps, "HOLD_MS", 60_000)
    with store.read() as c:
        read_deps.sweep(c)
        deps = {key for key, in c.execute("SELECT key FROM read_deps WHERE dep='a:run-1'")}
    assert walked == ["run-1"]
    assert {f"m:reply-{i}" for i in range(12)} <= deps
