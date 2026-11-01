"""Pooled reads (backend/store.py Store.read): reused, rolled back, and never carrying a caller's state."""
import os
import sqlite3

import pytest

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
        assert c.execute("PRAGMA busy_timeout").fetchone()[0] == 10000
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


@pytest.mark.parametrize("recording", [False, True])
@pytest.mark.parametrize("error", [sqlite3.OperationalError, KeyboardInterrupt])
def test_provenance_initialization_failure_releases_writer(tmp_path, monkeypatch, recording, error):
    from backend import flight, provenance_clock
    s = store(tmp_path)
    rec = flight.Recorder() if recording else None
    s.recorder = rec
    monkeypatch.setattr(flight, "ACTIVE", None)
    original = provenance_clock.current
    borrowed = []

    def fail_once(c):
        assert c.in_transaction and len(s.writer.queue) == 1
        if rec:
            assert len(rec.holding) == 1
        borrowed.append(c)
        c.execute("UPDATE t SET x=2")
        monkeypatch.setattr(provenance_clock, "current", original)
        raise error("synthetic provenance failure")

    monkeypatch.setattr(provenance_clock, "current", fail_once)
    with pytest.raises(error, match="synthetic provenance failure"):
        with s.transaction():
            pytest.fail("initialization must fail before yielding")
    assert len(borrowed) == 1 and not borrowed[0].in_transaction
    assert not s.writer.queue
    if rec:
        assert not rec.holding and not rec.waiting
    with s.transaction() as c:
        assert c.execute("SELECT x FROM t").fetchone()[0] == 1
        c.execute("UPDATE t SET x=3")
    assert not s.writer.queue


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


def test_a_durable_transaction_is_fully_synced_and_its_connection_goes_back_at_the_default(tmp_path, monkeypatch):
    """A money write's commit reaches the disk (FULL) and the pooled connection is not left at FULL, whether the
    transaction commits, its body fails, or BEGIN itself fails after the connection was taken."""
    from backend import flight
    from backend import hubdb as H
    sync = lambda c: c.execute("PRAGMA synchronous").fetchone()[0]  # 0 OFF, 1 NORMAL, 2 FULL
    s = store(tmp_path)
    default = {"OFF": 0, "NORMAL": 1}[H.synchronous("NORMAL")]
    with s.transaction(durable=True) as c:
        assert sync(c) == 2
        c.execute("INSERT INTO t VALUES(2)")
    with pytest.raises(RuntimeError):
        with s.transaction(durable=True) as c:
            assert sync(c) == 2
            c.execute("INSERT INTO t VALUES(3)")
            raise RuntimeError("the body failed")
    real_begin = flight.begin

    def begin_refused(conn, recorder=None, *, gate=None, checkout=None):
        checkout()                                       # the connection is taken, then BEGIN is refused
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(flight, "begin", begin_refused)
    with pytest.raises(sqlite3.OperationalError):
        with s.transaction(durable=True):
            pass
    monkeypatch.setattr(flight, "begin", real_begin)
    with s.transaction() as c:
        assert sync(c) == default                        # an ordinary transaction is never durable
    assert s.pool and all(sync(p) == default for p in s.pool)
    with s.read() as c:
        assert [r[0] for r in c.execute("SELECT x FROM t ORDER BY x")] == [1, 2]


def test_sweep_deltas_drops_a_finished_runs_text_pieces_after_a_day_only(tmp_path):
    from backend.store import H, sweep_deltas
    s = store(tmp_path)
    now = H.now()
    day, hours = (lambda n: H.shift(now, days=-n)), (lambda n: H.shift(now, hours=-n))
    # (created, finished, final_text, final message?, job state): what a run's pieces are kept beside
    runs = {
        "a-old": (day(4), day(3), "", True, None),              # finished 3 days ago, its final message holds the text
        "b-recent": (day(3), hours(12), "", True, None),        # a long run finished 12 hours ago: a day has not passed
        "c-open": (day(3), None, "", False, None),              # still running
        "d-no-final": (day(4), day(3), "", False, None),        # only a partial message: its text is not whole anywhere
        "e-review": (day(4), day(3), "", True, "uncertain"),    # held for review: the card and auto_reconcile read it
        "f-final-text": (day(4), day(3), "Hello", False, None), # no message, but attempts.final_text holds the reply
    }
    with s.transaction() as c:
        c.execute("CREATE TABLE attempts(id TEXT PRIMARY KEY, created_at TEXT, finished_at TEXT, final_text TEXT)")
        c.execute("CREATE TABLE jobs(id TEXT PRIMARY KEY, attempt_id TEXT, state TEXT)")
        c.execute("CREATE TABLE attempt_events(id INTEGER PRIMARY KEY, attempt_id TEXT, seq INTEGER, kind TEXT, "
                  "payload TEXT, created_at TEXT, UNIQUE(attempt_id,seq))")
        c.execute("CREATE TABLE registry_metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        for aid, (created, finished, final_text, final_msg, job) in runs.items():
            c.execute("INSERT INTO attempts VALUES(?,?,?,?)", (aid, created, finished, final_text))
            if job:
                c.execute("INSERT INTO jobs VALUES(?,?,?)", ("job-" + aid, aid, job))
            events = [(1, "delta", '{"text":"Hel","delta_kind":"text"}'), (2, "delta", '{"text":"lo"}'),
                      (3, "delta", '{"text":"hm","delta_kind":"thought"}'),
                      (4, "message", '{"text":"Hello","final":true}' if final_msg else '{"text":"Hel"}'),
                      (5, "tool", '{"tool":"x"}')]
            for seq, kind, payload in events:
                c.execute("INSERT INTO attempt_events(attempt_id,seq,kind,payload,created_at) VALUES(?,?,?,?,?)",
                          (aid, seq, kind, payload, created))
    assert sweep_deltas(s, now, batch=1) == 4          # batch=1: one row per transaction, the pass still drains
    with s.read() as c:
        left = {(r[0], r[1]) for r in c.execute("SELECT attempt_id,seq FROM attempt_events")}
    everything = {(a, n) for a in runs for n in range(1, 6)}
    # Only a-old and f-final-text lose their two text pieces; the thought, the message and the tool all stay.
    assert left == everything - {("a-old", 1), ("a-old", 2), ("f-final-text", 1), ("f-final-text", 2)}
    assert sweep_deltas(s, now) == 0, "the mark moved past what was swept"


def test_sweep_events_drops_noise_after_a_week_and_the_rest_after_thirty_in_bounded_passes(tmp_path):
    from backend.store import EVENT_NOISE_ACTIONS, EVENT_SWEEP_ACTIONS, H, sweep_events
    s = store(tmp_path)
    now = H.now()
    day = lambda n: H.shift(now, days=-n)
    kept = ("task.create", "attempt.limited", "attempt.input.moved", "credential.revealed", "credential.sent_to_computer",
            "mail.read", "calendar.read", "judge.spend", "judge.call", "mcp.call", "github.token", "inbound.read",
            "message.say", "turn.outcome")
    assert not {*EVENT_NOISE_ACTIONS, *EVENT_SWEEP_ACTIONS} & set(kept)
    rows = []                                                   # (action, age in days)
    for action in EVENT_NOISE_ACTIONS:                          # a week old goes; two days old stays
        rows += [(action, 8), (action, 2)]
    for action in EVENT_SWEEP_ACTIONS:                          # eight days old stays; thirty-one goes
        rows += [(action, 31), (action, 8), (action, 2)]
    for action in kept:                                         # never swept, however old
        rows += [(action, 40), (action, 2)]
    rows += [("sql.query", 40)] * 12                            # a backlog of noise that drains over passes
    with s.transaction() as c:
        c.execute("CREATE TABLE events(id TEXT PRIMARY KEY, created_at TEXT, actor TEXT, action TEXT, target TEXT, "
                  "detail TEXT)")
        c.execute("CREATE INDEX events_ts ON events(created_at)")
        c.execute("CREATE TABLE registry_metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        seen = {}
        for action, age in rows:
            n = seen[(action, age)] = seen.get((action, age), 0) + 1
            ident = f"{action}-{age}" if n == 1 else f"{action}-{age}-{n}"
            c.execute("INSERT INTO events VALUES(?,?,?,?,?,?)", (ident, day(age), "keeper", action, "t", None))
    expected = len(EVENT_NOISE_ACTIONS) + 12 + len(EVENT_SWEEP_ACTIONS)
    passes = []
    while True:
        passes.append(sweep_events(s, now, batch=5, most=10))
        if not passes[-1]:
            break
    assert max(passes) <= 10 and sum(passes) == expected, passes
    assert sweep_events(s, now) == 0, "the marks moved past what was swept"
    with s.read() as c:
        left = {r[0] for r in c.execute("SELECT id FROM events")}
    assert left == ({f"{action}-2" for action in (*EVENT_NOISE_ACTIONS, *EVENT_SWEEP_ACTIONS, *kept)}
                    | {f"{action}-8" for action in EVENT_SWEEP_ACTIONS}
                    | {f"{action}-40" for action in kept})


def test_sweep_tokens_drops_a_finished_runs_token_rows_after_a_week_only(tmp_path):
    from backend.store import H, sweep_tokens
    s = store(tmp_path)
    now = H.now()
    day = lambda n: H.shift(now, days=-n)
    # (created, finished, turn row as (finished, input_tokens, usage_reason), job state): what the run's totals are kept on
    runs = {
        "a-old": (day(10), day(9), (day(9), 100, None), None),          # finished nine days ago, totals written
        "b-recent": (day(9), day(3), (day(3), 100, None), None),        # finished three days ago: a week has not passed
        "c-open": (day(9), None, None, None),                           # still running
        "d-no-turn": (day(10), day(9), None, None),                     # no turn row: nothing records its totals
        "e-no-usage": (day(10), day(9), (day(9), None, "no_result"), None),   # a run with no usage still has a turn row
        "f-uncertain": (day(10), day(9), (day(9), 100, None), "uncertain"),   # held for review: auto_reconcile reads it
        "g-turn-open": (day(10), day(9), (None, None, None), None),     # the turn was never finished
    }
    with s.transaction() as c:
        c.execute("CREATE TABLE attempts(id TEXT PRIMARY KEY, created_at TEXT, finished_at TEXT)")
        c.execute("CREATE TABLE jobs(id TEXT PRIMARY KEY, attempt_id TEXT, state TEXT)")
        c.execute("CREATE TABLE turns(id TEXT PRIMARY KEY, finished_at TEXT, input_tokens INTEGER, usage_reason TEXT)")
        c.execute("CREATE TABLE attempt_events(id INTEGER PRIMARY KEY, attempt_id TEXT, seq INTEGER, kind TEXT, "
                  "payload TEXT, created_at TEXT, UNIQUE(attempt_id,seq))")
        c.execute("CREATE TABLE registry_metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        for aid, (created, finished, turn, job) in runs.items():
            c.execute("INSERT INTO attempts VALUES(?,?,?)", (aid, created, finished))
            if turn:
                c.execute("INSERT INTO turns VALUES(?,?,?,?)", (aid, turn[0], turn[1], turn[2]))
            if job:
                c.execute("INSERT INTO jobs VALUES(?,?,?)", ("job-" + aid, aid, job))
            events = [(1, "delta", '{"text":"Hel"}'), (2, "tokens", '{"input":900,"output":12}'),
                      (3, "message", '{"text":"Hello","final":true}'), (4, "tokens", '{"input":40,"output":0}')]
            for seq, kind, payload in events:
                c.execute("INSERT INTO attempt_events(attempt_id,seq,kind,payload,created_at) VALUES(?,?,?,?,?)",
                          (aid, seq, kind, payload, created))
    with s.read() as c:
        totals = c.execute("SELECT id,finished_at,input_tokens,usage_reason FROM turns ORDER BY id").fetchall()
    assert sweep_tokens(s, now, batch=1) == 4          # a-old and e-no-usage lose their two token rows each
    with s.read() as c:
        left = {(r[0], r[1]) for r in c.execute("SELECT attempt_id,seq FROM attempt_events")}
        assert c.execute("SELECT id,finished_at,input_tokens,usage_reason FROM turns ORDER BY id").fetchall() == totals
    everything = {(a, n) for a in runs for n in range(1, 5)}
    assert left == everything - {("a-old", 2), ("a-old", 4), ("e-no-usage", 2), ("e-no-usage", 4)}
    assert sweep_tokens(s, now) == 0, "the mark moved past what was swept"


def test_hub_connections_cap_the_wal_file_after_a_checkpoint(tmp_path):
    from backend import hubdb
    from backend.store import H
    s = store(tmp_path)
    assert s.connect().execute("PRAGMA journal_size_limit").fetchone()[0] == 64 * 1024 * 1024
    conn = hubdb.connect(str(tmp_path / "hub-open.sqlite"))
    try:
        assert conn.execute("PRAGMA journal_size_limit").fetchone()[0] == H.WAL_SIZE_LIMIT == 64 * 1024 * 1024
    finally:
        conn.close()
