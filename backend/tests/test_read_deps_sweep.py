"""The history-read sweep (backend/read_deps.py) beside a busy server: its writes queue at the server's writer gate and
give the lock back after a short while, so a claim or heartbeat waiting there goes before the next one (TK-INSRN53V).
Synthetic rows only."""
import inspect
import threading
import time
import types

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
    backlog(store, 5)
    settled = []
    original = read_deps.settle
    monkeypatch.setattr(read_deps, "settle",
                        lambda conn, keys, walk=None: settled.append(len(keys)) or original(conn, keys, walk))
    monkeypatch.setattr(read_deps, "HOLD_MS", 0)          # every write gives the lock back after one row
    gate = Gate(store.writer)
    with store.read() as c:
        walked = read_deps.sweep(c, gate=gate)
        assert c.execute("SELECT count(*) FROM read_dirty").fetchone()[0] == 0
        assert c.execute("SELECT 1 FROM read_index WHERE key='m:old-0'").fetchone()
        assert c.execute("SELECT 1 FROM read_index WHERE key='m:old-4'").fetchone()
    assert walked >= 5
    assert max(settled) == 1
    assert gate.admitted == len(settled) >= 5    # one gate admission per short write


def test_the_hold_budget_still_settles_a_whole_batch_in_one_write_when_it_is_quick(api, monkeypatch):
    store = api.app.state.store
    backlog(store, 40)
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


class _Stop:
    """`loop`'s stop with no clock: each wait is recorded and returns at once; the test's pass sets it."""
    def __init__(self):
        self.events, self._set = [], False

    def is_set(self):
        return self._set

    def set(self):
        self._set = True

    def wait(self, timeout):
        self.events.append(("wait", timeout))
        return self._set


def test_the_loop_waits_before_its_first_pass_and_the_first_wait_is_sixty_seconds(api, monkeypatch):
    """A server that has just started has every runner replaying its queued writes at once, so the first pass waits."""
    assert inspect.signature(read_deps.loop).parameters["first_wait"].default == 60
    stop = _Stop()

    def one_pass(conn, stop, gate=None, searched=None):
        stop.events.append(("sweep",))
        stop.set()
        return 0
    monkeypatch.setattr(read_deps, "sweep", one_pass)
    read_deps.loop(api.app.state.store, stop, seconds=10, first_wait=7)
    assert stop.events == [("wait", 7), ("sweep",), ("wait", 10)]


def test_a_stop_during_the_first_wait_ends_the_loop_at_once_without_a_pass(api, monkeypatch):
    passes = []
    monkeypatch.setattr(read_deps, "sweep", lambda *a, **k: passes.append(1) or 0)
    stop = threading.Event()
    timer = threading.Timer(0.05, stop.set)
    timer.start()
    started = time.monotonic()
    read_deps.loop(api.app.state.store, stop, first_wait=60)
    timer.join()
    assert time.monotonic() - started < 10     # the wait is 60 s: a stop that waited it out would fail here
    assert passes == []


def test_a_slow_row_ends_the_write_after_the_row_that_passes_the_budget(api, monkeypatch):
    """Under CPU starvation one row can take far longer than normal. The clock is read after every row, so a write
    stops after the first row past HOLD_MS, not after a slice of rows (each 150 ms here, a 200 ms budget: two rows)."""
    store = api.app.state.store
    backlog(store, 10)
    clock = [0.0]
    monkeypatch.setattr(read_deps, "time", types.SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(read_deps, "HOLD_MS", 200)
    original, calls = read_deps.settle, []

    def slow_settle(conn, keys, walk=None):
        calls.append(list(keys))
        clock[0] += 0.15
        return original(conn, keys, walk)
    monkeypatch.setattr(read_deps, "settle", slow_settle)
    with store.read() as c:
        keys = [r[0] for r in c.execute("SELECT key FROM read_dirty ORDER BY key LIMIT ?", (read_deps.BATCH,))]
        assert len(keys) == 10
        settled = read_deps._settle_some(c, keys)
        left = [r[0] for r in c.execute("SELECT key FROM read_dirty ORDER BY key")]
        index = {r[0] for r in c.execute("SELECT key FROM read_index")}
    assert settled == 2 and calls == [keys[:1], keys[1:2]]
    assert left == sorted(keys[2:])
    assert set(keys[:2]) <= index and not index & set(keys[2:])
