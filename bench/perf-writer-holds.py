"""Measures how long background and runner writes hold the single SQLite writer, and how many statements they run
inside it, on a fictional company at the hosted TIDY company's size (Nov 19: 18,700 tasks, 19,200 rooms, 40,000
messages, 4,700 runs, 1,150 open tasks on the engine).

    nice -n 10 .venv/bin/python bench/perf-writer-holds.py [scenario ...]

Scenarios (all by default):
  history   the history-read sweep (backend/read_deps.py): a backlog of queued rows settled in short writes

Env: TASKS (18700), ROOMS (19200), MESSAGES (40000), ROOM_DEPS (1000, messages in one busy room), RUNS (20).

Each hold is BEGIN IMMEDIATE to COMMIT, timed by wrapping backend.flight.begin; a statement counts as "in lock" when
it runs between the two on the holding thread. Only fictional data, in a temporary folder removed afterwards. Never
point it at a real hub.
"""

import contextlib
import dataclasses
import os
import shutil
import statistics
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import conftest  # noqa: F401  (the fictional registry and isolated benchmark settings)

from backend import flight
from backend.store import H, encode

TASKS = int(os.environ.get("TASKS", 18700))
ROOMS = int(os.environ.get("ROOMS", 19200))
MESSAGES = int(os.environ.get("MESSAGES", 40000))
ROOM_DEPS = int(os.environ.get("ROOM_DEPS", 1000))
RUNS = int(os.environ.get("RUNS", 20))


class Meter:
    """Write-lock holds (ms) and the statements run inside each, while `on`."""

    def __init__(self, recorder):
        self.on, self.local, self.holds = False, threading.local(), []
        query, begin = recorder.query, flight.begin

        def counted(sql, ms, locked=False):
            if self.on and getattr(self.local, "held", None) is not None:
                self.local.held[1] += 1
            return query(sql, ms, locked)

        def timed(conn, rec=None, **kwargs):
            done = begin(conn, rec, **kwargs)
            self.local.held = [time.perf_counter(), 0]

            def finished():
                started, statements = self.local.held
                self.local.held = None
                if self.on:
                    self.holds.append(((time.perf_counter() - started) * 1000, statements))
                return done()

            return finished

        recorder.query, flight.begin = counted, timed

    @contextlib.contextmanager
    def call(self):
        self.holds, self.on = [], True
        try:
            yield self
        finally:
            self.on = False


def q(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * p))] if xs else 0


def line(name, holds):
    ms = [h[0] for h in holds]
    st = [h[1] for h in holds]
    print(
        f"  {name:44s} holds {len(holds):4d} | hold p50 {statistics.median(ms) if ms else 0:8.1f} ms "
        f"p90 {q(ms, 0.9):8.1f} ms max {max(ms, default=0):8.1f} ms | statements in lock p50 "
        f"{statistics.median(st) if st else 0:6.0f} max {max(st, default=0):6d}"
    )


# ----------------------------------------------------------------------------- history (backend/read_deps.py)
def seed_history(store):
    """TASKS tasks, ROOMS rooms (nine in ten a task's room), MESSAGES messages: plain ones, task refs, replies and run
    messages; then every row walked once, as a server that has run a while has them."""
    from backend import read_deps

    now = H.now()
    tasks = [str(uuid.UUID(int=n + 1)) for n in range(TASKS)]
    rooms = [f"room-{n}" for n in range(ROOMS)]
    runs = [f"run-{n}" for n in range(max(1, MESSAGES // 10))]
    with store.transaction() as c:
        c.executemany(
            "INSERT INTO tasks(id,title,requester_id,owner_id,status,created_at,updated_at,private) "
            "VALUES(?,?,'human:ana','bot:ops','open',?,?,0)",
            [(tid, f"Task {n}", now, now) for n, tid in enumerate(tasks)],
        )
        c.executemany(
            "INSERT INTO conversations(id,kind,participants,created_at,task_id) VALUES(?,'chat',?,?,?)",
            [
                (rid, encode(["human:ana", "bot:ops"]), now, tasks[n % TASKS] if n % 10 else None)
                for n, rid in enumerate(rooms)
            ],
        )
        rows = []
        for n in range(MESSAGES):
            # The busy room is room-0: ROOM_DEPS messages, the rest spread over every room.
            room = rooms[0] if n < ROOM_DEPS else rooms[n % ROOMS]
            kind = n % 4
            refs = (
                "{}"
                if kind == 0
                else encode({"task": tasks[n % TASKS]})
                if kind == 1
                else encode({"turn_id": runs[n % len(runs)]})
                if kind == 2
                else encode({"goal": {"id": "g1"}})
            )
            reply = f"msg-{n - 1}" if n % 7 == 3 and n else None
            rows.append((f"msg-{n}", room, "human:ana", "bot:ops", "say", "Synthetic history", now, refs, reply))
        c.executemany(
            "INSERT INTO messages(id,conversation_id,sender_id,recipient_id,kind,body,created_at,refs,in_reply_to) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            rows,
        )
    with store.transaction() as c:
        keys = [r[0] for r in c.execute("SELECT key FROM read_dirty")]
        read_deps.settle(c, keys)  # one Walk for the whole backlog: the seed, not a measured write
    return rooms


def bench_history(app, meter):
    from backend import read_deps

    store = app.state.store
    t = time.perf_counter()
    rooms = seed_history(store)
    with store.read() as c:
        deps = c.execute("SELECT count(*) FROM read_deps WHERE dep=?", ("c:" + rooms[0],)).fetchone()[0]
        settled = c.execute("SELECT count(*) FROM read_index").fetchone()[0]
    print(
        f"history: seeded {TASKS} tasks, {ROOMS} rooms, {MESSAGES} messages ({settled} settled; {deps} read the busy "
        f"room) in {time.perf_counter() - t:.1f}s"
    )

    # A backlog of RUNS batches settled by one sweep, as the server's loop does: one read checkout, one short write
    # per batch (or less, by the hold budget). Each write is one hold.
    with store.transaction() as c:
        keys = [f"m:msg-{k % MESSAGES}" for k in range(RUNS * read_deps.BATCH)]
        c.executemany("INSERT OR IGNORE INTO read_dirty VALUES(?)", [(k,) for k in keys])
    with store.read() as c, meter.call():
        read_deps.sweep(c, gate=store.writer)
        holds = list(meter.holds)
    line(f"sweep of {RUNS * read_deps.BATCH} queued rows, per write", holds)

    explain_history(store)


def explain_history(store):
    """The walk's room and task reads: the whole-table reads a write made before, and the keyed reads it makes now."""
    from backend import row_clock

    with store.read() as c:
        rid = c.execute("SELECT id FROM conversations LIMIT 1 OFFSET 500").fetchone()[0]
        tid = c.execute("SELECT id FROM tasks LIMIT 1 OFFSET 500").fetchone()[0]
        reads = (
            ("old: every task id", row_clock.TASK_IDS, ()),
            ("old: every room's task", row_clock.ROOM_TASKS, ()),
            ("new: one room's task", "SELECT task_id FROM conversations WHERE id=?", (rid,)),
            ("new: one task id", "SELECT 1 FROM tasks WHERE id=?", (tid,)),
        )
        for label, sql, args in reads:
            plan = " | ".join(r[3] for r in c.execute("EXPLAIN QUERY PLAN " + sql, args))
            t = time.perf_counter()
            for _ in range(20):
                c.execute(sql, args).fetchall()
            print(f"  {label:24s} {(time.perf_counter() - t) * 1000 / 20:7.3f} ms  {plan}")


SCENARIOS = {"history": bench_history}


def main():
    from fastapi.testclient import TestClient

    from backend.app import create_app
    from backend.tests.helpers.api import api_settings
    from backend.tests.helpers.company import seed as company

    chosen = sys.argv[1:] or list(SCENARIOS)
    for name in chosen:
        with tempfile.TemporaryDirectory(prefix=".tmp-writer-holds-", dir=Path(__file__).resolve().parents[1]) as d:
            folder = Path(d)
            settings = dataclasses.replace(api_settings(folder), flight_recorder=True)
            app = create_app(settings)
            try:
                with TestClient(app):
                    with app.state.store.transaction() as c:
                        company(c)
                    SCENARIOS[name](app, Meter(app.state.store.recorder))
            finally:
                shutil.rmtree(folder, ignore_errors=True)


if __name__ == "__main__":
    main()
