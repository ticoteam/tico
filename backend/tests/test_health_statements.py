"""The Health page reads in set queries: its statements do not grow with the failed runs, events or bots it summarises
(Nov 3: 384 statements and 1.2 s a call on the Tico Team hub, rising with the data)."""
import collections
import contextlib
import re
import time
import uuid

from backend.store import H
from backend.tests.test_claim_statements import seed, traced  # noqa: F401
from backend.tests.test_api import api  # noqa: F401  (the fixture)
from backend.tests.test_api import headers


def history(api, per_bot):
    """`per_bot` failed and completed runs for every bot, spread over the last days."""
    store = api.app.state.store
    with store.transaction() as c:
        jobs = {r["bot_id"]: r["id"] for r in c.execute("SELECT id,bot_id FROM jobs")}
        runner_id = c.execute("SELECT id FROM runners LIMIT 1").fetchone()["id"]
        n = 0
        for bot, job in jobs.items():
            for i in range(per_bot):
                n += 1
                at = H.shift(H.now(), minutes=-(7 * n % 4000))
                state = ("failed", "completed", "expired", "interrupted")[i % 4]
                c.execute("INSERT INTO attempts(id,job_id,bot_id,runner_id,generation,token_hash,state,lease_until,"
                          "created_at,started_at,finished_at,result) VALUES(?,?,?,?,0,?,?,?,?,?,?,?)",
                          (uuid.uuid4().hex, job, bot, runner_id, uuid.uuid4().hex, state, at, at, at, at,
                           '{"fallback": "codex"}' if i % 3 == 0 else "{}"))


def bulk(api, tasks=30000, events=100000):
    """Days of history: finished tasks, their events, the audit log."""
    with api.app.state.store.transaction() as c:
        bots = [r[0] for r in c.execute("SELECT id FROM bots")]
        c.execute("CREATE TEMP TABLE nums AS WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i+1 FROM n WHERE i<?) "
                  "SELECT i FROM n", (max(tasks, events),))
        for i, bot in enumerate(bots):
            c.execute("INSERT INTO tasks(id,title,requester_id,owner_id,status,created_at,updated_at,private) "
                      "SELECT 'bt'||?||'-'||i,'t','human:ana','bot:'||?, CASE i%5 WHEN 0 THEN 'open' WHEN 1 THEN 'doing' "
                      "WHEN 2 THEN 'declined' ELSE 'done' END, ?, ?, 0 FROM nums WHERE i<=?",
                      (i, bot, H.shift(H.now(), minutes=-3000), H.shift(H.now(), minutes=-30), tasks // len(bots)))
        c.execute("INSERT INTO task_events(id,task_id,created_at,actor,field,old,new) "
                  "SELECT 'te'||i,'bt0-'||(i%100+1),datetime('now','-'||(i%4000)||' minutes'),"
                  "'human:ana',CASE i%3 WHEN 0 THEN 'status' WHEN 1 THEN 'owner' ELSE 'note' END,'a','b' FROM nums WHERE i<=?", (events,))
        c.execute("INSERT INTO events(id,created_at,actor,action,target,detail) "
                  "SELECT 'ev'||i,datetime('now','-'||(i%4000)||' minutes'),'human:ana',CASE i%4 WHEN 0 THEN 'task.update' "
                  "WHEN 1 THEN 'worktree.released' WHEN 2 THEN 'chat.send' ELSE 'login' END,'t'||(i%500),'{}' FROM nums WHERE i<=?",
                  (events,))
        c.execute("DROP TABLE nums")


def health(api):
    with traced(api) as seen:
        response = api.get("/api/v2/health", headers=headers())
    assert response.status_code == 200, response.text
    return response.json(), seen


def timed(api):
    """(statement, seconds until the next statement began) for one call."""
    stamps = []
    store, original = api.app.state.store, api.app.state.store.read

    @contextlib.contextmanager
    def borrowed():
        with original() as c:
            c.set_trace_callback(lambda sql: stamps.append((sql, time.perf_counter())))
            try:
                yield c
            finally:
                stamps.append(("END", time.perf_counter()))
                c.set_trace_callback(None)
    store.read = borrowed
    try:
        assert api.get("/api/v2/health", headers=headers()).status_code == 200
    finally:
        del store.read
    return [(a[0], b[1] - a[1]) for a, b in zip(stamps, stamps[1:])]


def shape(sql):
    return re.sub(r"\s+", " ", re.sub(r"'[^']*'|\b\d+\b|\?(,\?)*", "?", sql))[:150]


def test_health_statements_do_not_grow_with_history(api):
    seed(api, "few", 6)
    history(api, 3)
    bulk(api)
    health(api)                                   # first-time reads
    _, small = health(api)
    seed(api, "many", 34)
    history(api, 40)
    _, large = health(api)
    for sql, n in collections.Counter(shape(s) for s in large).most_common(25):
        print(n, sql)
    print("statements", len(small), len(large))
    cost = collections.defaultdict(float)
    for sql, secs in timed(api):
        cost[shape(sql)] += secs
    for sql, secs in sorted(cost.items(), key=lambda kv: -kv[1])[:12]:
        print("TIME %.1f ms" % (secs * 1000), sql)
    with api.app.state.store.read() as c:
        done = set()
        for sql in large:
            if not sql.lstrip().upper().startswith(("SELECT", "WITH")) or shape(sql) in done:
                continue
            done.add(shape(sql))
            try:
                plan = [r[3] for r in c.execute("EXPLAIN QUERY PLAN " + sql)]
            except Exception as e:
                plan = [repr(e)]
            if any(p.startswith(("SCAN", "USE TEMP")) and "sqlite_master" not in p for p in plan):
                print("PLAN", shape(sql), plan)
    assert len(large) <= len(small) + 12, (len(small), len(large))
