"""Synthetic runner traffic during the worktree-heavy heartbeat and scheduler pass."""
import json
import math
import threading
import time
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend import flight, worktrees
from backend.scheduler import Scheduler
from backend.store import H, encode
from backend.tests.test_api import api, assign, headers, ready, runner  # noqa: F401


def fleet(api, attempts=100, trees=50):
    computer = runner(api)
    assign(api, computer, "ops")
    ready(api, computer, ["ops"])
    store = api.app.state.store
    now = H.now()
    until = H.shift(now, seconds=store.settings.lease_seconds)
    with store.transaction() as c:
        generation = c.execute("SELECT generation FROM assignments WHERE bot='ops'").fetchone()[0]
        for i in range(attempts + trees):
            tid, cid, mid = f"task-{i}", f"room-{i}", f"message-{i}"
            c.execute("INSERT INTO conversations(id,kind,participants,created_at,task_id) VALUES(?,'task',?,?,?)",
                      (cid, encode(["human:ana", "bot:ops"]), now, tid))
            c.execute("INSERT INTO tasks(id,title,body,requester,owner,status,conversation_id,created_at,updated_at) "
                      "VALUES(?,?,'Synthetic workload','human:ana','bot:ops','doing',?,?,?)",
                      (tid, tid, cid, now, now))
            if i < attempts:
                c.execute("INSERT INTO messages(id,conversation_id,from_actor,to_actor,kind,body,created_at,refs) "
                          "VALUES(?,?,'human:ana','bot:ops','say','Synthetic work',?,?)",
                          (mid, cid, now, encode({"task": tid})))
                c.execute("INSERT INTO attempts(id,job_id,bot,runner_id,generation,token_hash,state,lease_until,created_at,lane) "
                          "VALUES(?,?,'ops',?,?,?,'running',?,?,?)",
                          (f"attempt-{i}", mid, computer["runner_id"], generation, f"synthetic-{i}", until, now, "task:" + tid))
                c.execute("UPDATE jobs SET state='running',attempt_id=? WHERE id=?", (f"attempt-{i}", mid))
            else:
                detail = {"owner": "bot:ops", "checkout_state": "ready", "setup_pending": False,
                          "expected_head": "a" * 40, "checkout_target": "refs/heads/main", "expected_base": "b" * 64}
                c.execute("INSERT INTO task_links(id,task_id,kind,url,state,added_by,created_at,computer_id,path,detail) "
                          "VALUES(?,?,'worktree',?,'present','bot:ops',?,?,?,?)",
                          (f"tree-{i}", tid, f"worktree:tree-{i}", now, computer["runner_id"], f"tasks/{tid}/Acme__product", encode(detail)))
                c.execute("INSERT INTO task_links(id,task_id,kind,url,state,added_by,created_at) "
                          "VALUES(?,?,'pr',?,'merged','bot:ops',?)",
                          (f"pr-{i}", tid, f"https://github.com/Acme/product/pull/{i}", now))
    return computer


def test_fresh_renew_reads_through_a_write_and_rechecks_stop_lease_and_assignment(api, monkeypatch):
    import sqlite3
    from backend.tests.test_api import post
    computer = fleet(api, attempts=1, trees=0)
    store = api.app.state.store
    now = H.now()
    monkeypatch.setattr(H, "now", lambda: now)
    with store.transaction() as c:
        until = H.shift(now, seconds=store.settings.lease_seconds - 17)
        c.execute("UPDATE attempts SET lease_until=? WHERE id='attempt-0'", (until,))
    post(api, "bots/ops/stop", {"attempt_id": "attempt-0"})
    with store.read() as c:
        until = c.execute("SELECT lease_until FROM attempts WHERE id='attempt-0'").fetchone()[0]
    holder = sqlite3.connect(store.settings.db_path, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = pool.submit(api.post, "/api/v2/attempts/attempt-0/renew", json={},
                                  headers=headers(computer["token"]))
            # Release even on failure: a broken fast path must not leave the test's executor stuck.
            try:
                renewed = pending.result(timeout=2)
            finally:
                holder.rollback()
        assert renewed.status_code == 200
        assert renewed.json()["stop"] is True and renewed.json()["lease_until"] == until
        assert renewed.json()["lease_seconds"] == store.settings.lease_seconds - 17
    finally:
        holder.close()
    with store.transaction() as c:
        c.execute("UPDATE attempts SET lease_until=? WHERE id='attempt-0'", (H.shift(H.now(), seconds=10),))
    renewed = post(api, "attempts/attempt-0/renew", {}, computer["token"])
    assert renewed["lease_until"] > until and renewed["lease_seconds"] == store.settings.lease_seconds
    # Even a legacy attempt with a long lease must persist life before the five-minute stall threshold.
    with store.transaction() as c:
        c.execute("DELETE FROM attempt_diagnostics WHERE attempt_id='attempt-0'")
        c.execute("UPDATE attempts SET created_at=?,lease_until=? WHERE id='attempt-0'",
                  (H.shift(now, seconds=-61), H.shift(now, hours=1)))
    renewed = post(api, "attempts/attempt-0/renew", {}, computer["token"])
    assert renewed["lease_seconds"] == store.settings.lease_seconds
    with store.read() as c:
        assert c.execute("SELECT heartbeat_at FROM attempt_diagnostics WHERE attempt_id='attempt-0'").fetchone()[0] == now
    with store.transaction() as c:
        c.execute("UPDATE assignments SET generation=generation+1 WHERE bot='ops'")
    for name in ("renew", "inputs"):
        post(api, "attempts/attempt-0/" + name, {}, computer["token"], expected=409)


def test_heartbeat_prepares_provenance_outside_write_and_discards_a_stale_snapshot(api, monkeypatch):
    from backend import task_privacy
    from backend.tests.test_api import post
    computer = fleet(api, attempts=4, trees=3)
    store = api.app.state.store
    observed = []
    original = task_privacy.attempt_tasks

    def counted(c, aid, memo=None):
        if memo is None:
            observed.append(aid)
        return original(c, aid, memo)
    monkeypatch.setattr(task_privacy, "attempt_tasks", counted)
    body = {"version": "synthetic", "platform": "test",
            "readiness": {"schema_version": 1, "bots": {}, "worktrees": True}}
    result = post(api, "runners/heartbeat", body, computer["token"])
    assert len(observed) == 4
    assert len(result["worktree_actions"]) == 3
    prepare = worktrees.live_turn_cache

    def changes_after_read(c, computer_id):
        selected = prepare(c, computer_id)
        with store.transaction() as writer:
            writer.execute("UPDATE tasks SET carried_by='attempt-0' WHERE id='task-4'")
        return selected
    monkeypatch.setattr(worktrees, "live_turn_cache", changes_after_read)
    result = post(api, "runners/heartbeat", body, computer["token"])
    assert "tree-4" not in {a["link_id"] for a in result["worktree_actions"]}
    monkeypatch.setattr(worktrees, "live_turn_cache", prepare)
    with store.transaction() as c:
        c.execute("UPDATE tasks SET owner='bot:finance' WHERE id='task-4'")
    result = post(api, "runners/heartbeat", body, computer["token"])
    assert "tree-4" in {a["link_id"] for a in result["worktree_actions"]}


def test_event_batch_replay_conflict_and_gap_are_atomic(api):
    from backend.tests.test_api import post
    computer = fleet(api, attempts=1, trees=0)
    path = "attempts/attempt-0/events"
    batch = [{"seq": i, "kind": "delta", "payload": {"text": str(i)}} for i in range(1, 4)]
    assert post(api, path, {"events": batch}, computer["token"])["ack_seq"] == 3
    assert post(api, path, {"events": batch + [batch[-1]]}, computer["token"])["ack_seq"] == 3
    post(api, path, {"events": [dict(batch[0], payload={"text": "different"})]}, computer["token"], expected=409)
    post(api, path, {"events": [{"seq": i, "kind": "delta", "payload": {}} for i in (4, 6)]},
         computer["token"], expected=409)
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM attempt_events WHERE attempt_id='attempt-0'").fetchone()[0] == 3
        assert c.execute("SELECT last_seq FROM attempts WHERE id='attempt-0'").fetchone()[0] == 3


def test_idle_inputs_and_task_read_do_not_wait_on_unrelated_writers(api):
    import sqlite3
    computer = fleet(api, attempts=1, trees=0)
    store = api.app.state.store
    github = api.app.state.github_app
    locked, release = threading.Event(), threading.Event()

    def inventory():
        with github.lock:
            locked.set()
            assert release.wait(5)

    holder = sqlite3.connect(store.settings.db_path, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    with ThreadPoolExecutor(max_workers=3) as pool:
        background = pool.submit(inventory)
        assert locked.wait(2)
        try:
            inputs = pool.submit(api.post, '/api/v2/attempts/attempt-0/inputs', json={},
                                 headers=headers(computer['token']))
            task = pool.submit(api.get, '/api/v2/tasks/task-0', headers=headers())
            assert inputs.result(timeout=2).status_code == 200
            assert task.result(timeout=2).status_code == 200
        finally:
            holder.rollback()
            holder.close()
            release.set()
        background.result()
    with store.transaction() as c:
        c.execute("UPDATE tasks SET private=1,owner='bot:finance',requester='human:ben' WHERE id='task-0'")
    assert api.post('/api/v2/attempts/attempt-0/inputs', json={},
                    headers=headers(computer['token'])).status_code == 409


def test_shared_provenance_rechecks_permissions_and_never_publishes_rolled_back_sources(api, monkeypatch):
    from backend import task_privacy
    fleet(api, attempts=1, trees=2)
    store = api.app.state.store
    builds = []
    original = task_privacy._attempt_tasks

    def build(c, aid):
        builds.append(aid)
        return original(c, aid)
    monkeypatch.setattr(task_privacy, '_attempt_tasks', build)
    with store.read_transaction() as c:
        assert task_privacy.attempt_readable(c, 'bot:ops', 'attempt-0')
    count = len(builds)
    with store.transaction() as c:
        c.execute("UPDATE tasks SET private=1,owner='bot:finance',requester='human:ben' WHERE id='task-0'")
    with store.read_transaction() as c:
        assert not task_privacy.attempt_readable(c, 'bot:ops', 'attempt-0')
    assert len(builds) == count       # Only neutral provenance survives; permission never does.
    # Even a manual transaction on a borrowed connection must not publish a clock
    # value that rollback can reuse for entirely different source rows.
    with store.read() as c:
        c.execute('BEGIN IMMEDIATE')
        c.execute("UPDATE messages SET refs=? WHERE id='message-0'", (encode({'task': 'task-1'}),))
        assert task_privacy.attempt_tasks(c, 'attempt-0') == {'task-1'}
        c.rollback()
    with store.transaction() as c:
        c.execute("UPDATE messages SET refs=? WHERE id='message-0'", (encode({'task': 'task-2'}),))
    with store.read_transaction() as c:
        assert task_privacy.attempt_tasks(c, 'attempt-0') == {'task-2'}


def test_claim_cleans_a_bounded_page_of_obsolete_notices(api):
    from backend import execution
    from backend.tests.test_api import post
    computer = fleet(api, attempts=0, trees=20)
    store = api.app.state.store
    with store.transaction() as c:
        c.execute("UPDATE tasks SET status='done'")
        for i in range(20):
            c.execute("INSERT INTO messages(id,conversation_id,from_actor,to_actor,kind,body,created_at,refs) "
                      "VALUES(?,?,?,'bot:ops','notice','Synthetic finished task',?,?)",
                      (f'obsolete-{i}', f'room-{i}', H.KEEPER, H.now(), encode({'task': f'task-{i}'})))
    assert post(api, 'jobs/claim', {}, computer['token'])['attempt'] is None
    with store.read() as c:
        assert c.execute("SELECT count(*) FROM jobs WHERE state='cancelled'").fetchone()[0] == execution.CLAIM_MAINTENANCE
        assert c.execute("SELECT count(*) FROM jobs WHERE state='queued'").fetchone()[0] == 20 - execution.CLAIM_MAINTENANCE


def test_health_flags_runner_p95_and_clears_after_the_window(api):
    import sqlite3
    from backend import health, health_monitor
    with sqlite3.connect(":memory:") as empty:
        assert health.runner_route_latency(empty) is None
    store = api.app.state.store
    now = int(time.time())
    rec = flight.Recorder()
    for _ in range(20):
        rec.request("POST /api/v2/attempts/{aid}/renew", "runner", 200, 1500)
        rec.request("POST /api/v2/attempts/{aid}/inputs", "runner", 200, 10)
    rec.drain(now)
    with store.transaction() as c:
        flight.write(c, rec.take(now // 60 * 60))
        assert "renew" in health.runner_route_latency(c, now)["summary"]
        assert any(row["key"] == "runner_route_latency" for row in health_monitor._server_checks(c))
        assert health.runner_route_latency(c, now + 16 * 60) is None


@pytest.mark.slow
def test_100_attempt_routes_during_heartbeat_and_scheduler(api, monkeypatch):
    computer = fleet(api)
    store = api.app.state.store
    # Time SQL and lock waits without starting any production service or background exporter.
    rec = flight.Recorder()
    store.recorder = rec
    store.factory = flight.connection_class(rec)
    store.pooled = __import__("backend.store", fromlist=["_pooled"])._pooled(store.factory)
    with store.pool_lock:
        for c in store.pool:
            c.close()
        store.pool.clear()
    monkeypatch.setattr(flight, "LONG_HOLD_MS", 20)
    entered = threading.Event()
    original = worktrees.live_turn

    def held(*args, **kwargs):
        entered.set()
        return original(*args, **kwargs)
    monkeypatch.setattr(worktrees, "live_turn", held)
    snapshot_counts = {"hit": 0, "fallback": 0}
    heartbeat = api.app.state.execution.heartbeat

    def counted_heartbeat(*args, **kwargs):
        snapshot_counts["hit" if kwargs.get("live_tasks") is not None else "fallback"] += 1
        return heartbeat(*args, **kwargs)
    monkeypatch.setattr(api.app.state.execution, "heartbeat", counted_heartbeat)
    samples = {name: [] for name in ("renew", "events", "inputs", "task")}
    failures = []

    def request(name, i):
        path = f"/api/v2/tasks/task-{i}" if name == "task" else f"/api/v2/attempts/attempt-{i}/{name}"
        body = {"events": [{"seq": 1, "kind": "delta", "payload": {"text": "synthetic"}}]} if name == "events" else {}
        route = "GET /api/v2/tasks/{tid}" if name == "task" else f"POST /api/v2/attempts/{{aid}}/{name}"
        method, template = route.split(" ", 1)
        cost, token = flight.request_started(method, {"route": SimpleNamespace(path=template)})
        started = time.monotonic()
        try:
            result = (api.get(path, headers=headers()) if name == "task" else
                      api.post(path, json=body, headers=headers(computer["token"])))
            elapsed = (time.monotonic() - started) * 1000
            samples[name].append((elapsed, cost[1], max(0, elapsed - cost[1])))
            if result.status_code != 200:
                failures.append((name, result.status_code, result.text[:200]))
        finally:
            flight.request_finished(token)

    with ThreadPoolExecutor(max_workers=18) as pool:
        beat = pool.submit(api.post, "/api/v2/runners/heartbeat", headers=headers(computer["token"]), json={
            "version": "synthetic", "platform": "test", "readiness": {"schema_version": 1, "bots": {}, "worktrees": True}})
        assert entered.wait(10)
        tick = pool.submit(Scheduler(store, api.app.state.execution).tick)
        jobs = [pool.submit(request, name, i) for i in range(100) for name in samples]
        for job in jobs:
            job.result(timeout=60)
        assert beat.result().status_code == 200
        assert not tick.result()["failures"]
    receipt = {name: {"n": len(values), "p95_ms": round(sorted(v[0] for v in values)[math.ceil(.95 * len(values)) - 1], 2),
                      "max_ms": round(max(v[0] for v in values), 2),
                      "wait_p95_ms": round(sorted(v[1] for v in values)[math.ceil(.95 * len(values)) - 1], 2),
                      "work_p95_ms": round(sorted(v[2] for v in values)[math.ceil(.95 * len(values)) - 1], 2)}
               for name, values in samples.items()}
    print("SNAPSHOT_RECEIPT " + json.dumps(snapshot_counts, sort_keys=True))
    print("CONTENTION_RECEIPT " + json.dumps(receipt, sort_keys=True))
    rec.drain()
    print("HOLDERS " + json.dumps(sorted(rec.long_holds, key=lambda h: -h["hold_s"])[:10]))
    assert not failures, failures
    with store.read() as c:
        assert c.execute("SELECT count(*) FROM attempts WHERE state='running' AND lease_until>?", (H.now(),)).fetchone()[0] == 100
    # Held to the health monitor's 1 s warning: the gate runs this beside other tests on the same VM, which roughly
    # doubles the 300 ms an idle 4-vCPU VM takes here.
    assert all(row["p95_ms"] < 1000 for row in receipt.values()), receipt
