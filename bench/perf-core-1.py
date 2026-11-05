"""Measures the assignment recovery pass and Health's refused-queue check on a fictional company.

    python3 -m pytest bench/perf-core-1.py -n 0 -s -q

Per case it prints the statements one call takes, the median wall time of RUNS calls, and a digest of the rows it
yields (equal digests mean equal rows in equal order). "cold" clears every process-level memo a pass could reuse
(assigned_start's ATTEMPTED where the tree still has it, and the store's provenance cache) before each run; "warm"
keeps them and times the calls after one priming call.
"""
import hashlib
import itertools
import random
import sqlite3
import statistics
import time
import uuid

from backend import assigned_start as A, auto_tasks, bot_settings as BS, provenance_clock, queue_refusals as Q
from backend.store import H, encode
from backend.tests import test_api

api = test_api.api                                   # the fixture, bound so that tests can take it
BOTS = [f"bot{n:02d}" for n in range(20)]
TASKS = 200
RUNS = 50


def seed_overdue(store, monkeypatch):
    """200 open tasks over 20 bots, each assigned long ago and woken once (the ledger row a sweep leaves). Half of them
    have been attempted since. Returns the time to read them at: two hours after the wake, three after assignment.
    Ids are counted, so the rows come back in the same order on every tree."""
    counter = itertools.count(1)
    monkeypatch.setattr(H, "new_id", lambda: f"{next(counter):012d}")
    with store.transaction() as c:
        H.sync_registry(c, {slug: {"name": slug, "runtime": "fake", "status": "active"} for slug in BOTS},
                        {"people": [{"id": p, "email": p + "@acme.example"} for p in ("ana", "ben", "cara")]})
        for slug in BOTS:
            c.execute("INSERT INTO bot_config(bot,owner_id) VALUES(?,?)", (slug, "ana"))
            BS.write(c, slug, {"name": slug, "runtime": "fake"})
        for n in range(TASKS):
            H.task_create(c, "human:ana", f"Overdue {n:03d}", "Please do it.", "bot:" + BOTS[n % len(BOTS)], lint=False)
    with store.transaction() as c:
        now = H.now()
        rows = c.execute("SELECT id,owner_id,created_at FROM tasks WHERE owner_id LIKE 'bot:bot%' AND status='open' "
                         "ORDER BY id").fetchall()
        for tid, _owner, created in rows:
            key = c.execute("SELECT coalesce((SELECT id FROM history_events WHERE subject_type='task' AND subject_id=t.id AND kind='owner' "
                            "ORDER BY created_at DESC,rowid DESC LIMIT 1),t.owner_id||':'||t.created_at) "
                            "FROM tasks t WHERE t.id=?", (tid,)).fetchone()[0]
            H.event(c, H.KEEPER, A.EVENT, tid, {"assignment": key, "assigned_at": created,
                                                "rewoken_at": H.shift(created, hours=1), "refusal": "no_queued_wake"})
    # Runs of the assigned bots that carried the attempted tasks (task_privacy.attempt_tasks reads `carried_by`). Foreign
    # keys are off for this seed only: a run needs a job and a computer that this fixture does not build.
    raw = sqlite3.connect(str(store.settings.db_path), isolation_level=None)
    raw.execute("PRAGMA foreign_keys=OFF")
    raw.execute("BEGIN")
    # Every other task of each bot: its k-th task (k = n // bots) is attempted when k is even.
    attempted = [row for n, row in enumerate(rows) if (n // len(BOTS)) % 2 == 0]
    for tid, owner, _created in attempted:
        aid = H.new_id()
        raw.execute("INSERT INTO attempts(id,job_id,bot_id,runner_id,generation,token_hash,state,lease_until,created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (aid, H.new_id(), H.actor_id(owner), "bench", 0, H.new_id(), "completed", now, now))
        raw.execute("UPDATE tasks SET carried_by=? WHERE id=?", (aid, tid))
    raw.execute("COMMIT")
    raw.close()
    return H.shift(now, hours=3)


def go_cold(store):
    getattr(A, "ATTEMPTED", {}).clear()
    store.provenance_cache = provenance_clock.Cache()


def timed(store, call):
    with store.read() as c:
        statements = []
        c.set_trace_callback(statements.append)
        start = time.perf_counter()
        result = call(c)
        wall = time.perf_counter() - start
        c.set_trace_callback(None)
    return result, len(statements), wall


def digest(rows):
    return hashlib.sha1("\n".join(map(str, rows)).encode()).hexdigest()[:12]


def measure(api, label, call):
    store = api.app.state.store
    for mode in ("cold", "warm"):
        if mode == "warm":
            timed(store, call)                      # the first pass fills whatever memo the tree keeps
        walls, statements, rows = [], None, None
        for _ in range(RUNS):
            if mode == "cold":
                go_cold(store)
            rows, statements, wall = timed(store, call)
            walls.append(wall)
        print(f"{label:<34} {mode:<4} statements={statements:<5} rows={len(rows):<4} "
              f"median_ms={statistics.median(walls) * 1000:8.2f} digest={digest(rows)}")


def test_candidates_and_assigned_check(api, monkeypatch):
    at = seed_overdue(api.app.state.store, monkeypatch)
    settings = api.app.state.store.settings
    for recorded in (None, True):
        measure(api, f"candidates recorded={recorded}",
                lambda c, recorded=recorded: [row["title"] for row, *_ in A.candidates(c, at, recorded=recorded)])

    def health_check(c):
        overdue = list(A.overdue(c, at))
        suspect, shared = auto_tasks.maybe_waiting(c, overdue), {}
        return [A.description(c, row, settings, A.reason(c, row, settings, shared, suspect)) for row in overdue]

    measure(api, "health assigned_not_started", health_check)


def refusable(api, slugs):
    """The bots whose claim check reports a refusal now, with the job and reason it reports (queue_refusals.current)."""
    store = api.app.state.store
    with store.read() as c:
        values = {slug: Q.current(c, store.settings, slug) for slug in slugs}
    return {slug: value for slug, value in values.items() if value and value["reason"] != "lane_held"}


def refuse(api, values):
    """Each of these bots' queues refused for over 12 hours, as the sweep leaves them."""
    with api.app.state.store.transaction() as c:
        for slug, value in values.items():
            c.execute("INSERT OR REPLACE INTO registry_metadata VALUES(?,?)", (Q.KEY + slug, encode(
                {**value, "since": "2026-10-23T00:00:00.000000Z", "observed_at": H.now()})))


def test_health_statements_by_refused_queues(api, monkeypatch):
    from backend.tests.test_api import headers
    from backend.tests.test_health_queries import counted
    from backend.tests.test_run_backoff import at, setup
    from backend.tests.test_status_reads import busy
    rng = random.Random(7)

    def seeded_id():
        """Ids from one seeded generator, so which bots are refused is the same on every run and tree (the ids order
        the queued jobs, and random ones change that each run). Uniform bits, unlike a counter: short prefixes stay unique."""
        return str(uuid.UUID(int=rng.getrandbits(128), version=4))
    monkeypatch.setattr(H, "new_id", seeded_id)
    monkeypatch.setattr(H, "uuid7", seeded_id)
    machine = setup(api, monkeypatch, tasks=2)
    at(monkeypatch, api, "2026-10-24T12:00:00.000000Z")
    busy(api, machine, 0, 120)
    at(monkeypatch, api, "2026-10-24T12:40:00.000000Z")
    values = refusable(api, [f"busy-{k}" for k in range(120)])
    assert len(values) >= 50, len(values)
    slugs = sorted(values)
    done = 0
    for count in (0, 5, 50):
        refuse(api, {slug: values[slug] for slug in slugs[done:count]})
        done = count
        body, statements = counted(api, monkeypatch, "/api/v2/health")
        shown = next((row for row in body["checks"] if row["id"] == "queue_refused"), {}).get("items", [])
        walls = []
        for _ in range(RUNS):
            start = time.perf_counter()
            assert api.get("/api/v2/health", headers=headers("ana-test")).status_code == 200
            walls.append(time.perf_counter() - start)
        print(f"GET /api/v2/health refused={count:<3} statements={statements:<5} rows={len(shown):<4} "
              f"median_ms={statistics.median(walls) * 1000:8.2f} digest={digest([row['bot'] + '|' + row['text'] for row in shown])}")
