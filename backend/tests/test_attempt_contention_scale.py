"""Busy-install regression, using only synthetic task/tag/provenance rows."""
import json
import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from backend import execution, flight, worktrees
from backend.scheduler import Scheduler
from backend.store import H, encode, _pooled
from backend.tests.test_api import api, headers  # noqa: F401
from backend.tests.test_attempt_contention import fleet


@pytest.mark.slow
def test_busy_install_routes(api, monkeypatch):
    started = time.monotonic()
    computer = fleet(api, attempts=100, trees=100)
    store = api.app.state.store
    now = H.now()
    with store.transaction() as c:
        for j in range(8):
            c.execute("INSERT INTO tags(id,key,label,created_at,updated_at) VALUES(?,?,?,?,?)",
                      (f'tag-{j}', f'label-{j}', f'Label {j}', now, now))
        for i in range(200):
            c.executemany('INSERT INTO task_tags(task_id,tag_id) VALUES(?,?)',
                          [(f'task-{i}', f'tag-{j}') for j in range(8)])
        # Shared reply chains are common in long-lived task conversations. Each attempt also
        # carries twenty inputs; the same parents must be resolved in one coherent snapshot.
        for i in range(100):
            for j in range(20):
                mid = f'input-{i}-{j}'
                c.execute("INSERT INTO messages(id,conversation_id,sender_id,recipient_id,kind,body,created_at,refs,in_reply_to) "
                          "VALUES(?,?,'human:ana','bot:ops','say','Synthetic input',?,?,?)",
                          (mid, f'room-{i}', now, encode({'task': f'task-{i}', 'answers': [f'message-{k}' for k in range(10)]}),
                           f'input-{i}-{j-1}' if j else None))
                c.execute('INSERT INTO attempt_inputs(attempt_id,message_id) VALUES(?,?)',
                          (f'attempt-{i}', mid))
                c.execute("UPDATE jobs SET state='input',attempt_id=? WHERE id=?", (f'attempt-{i}', mid))
        # Distinct task lanes leave real work for concurrent claims. The heartbeat
        # reports every checkout, as a busy runner does, rather than an empty inventory.
        c.execute('UPDATE runners SET capacity=200,capabilities=? WHERE id=?',
                  (encode([execution.LANES, execution.ROOTS]), computer['runner_id']))
        for i in range(197, 200):
            c.execute("INSERT INTO messages(id,conversation_id,sender_id,recipient_id,kind,body,created_at,refs) "
                      "VALUES(?,?,'human:ana','bot:ops','say','Synthetic queued work',?,?)",
                      (f'queued-{i}', f'room-{i}', now, encode({'task': f'task-{i}'})))
        c.execute("UPDATE tasks SET updated_at=?,owner_id='bot:finance',private=0 WHERE id IN (" + ','.join('?' * 10) + ")",
                  (H.shift(now, hours=-2), *(f'task-{i}' for i in range(100, 110))))
        # Fixture construction is not part of the lease workload. Start the real lease
        # clock only after all synthetic inputs have been inserted.
        original_lease = H.shift(H.now(), seconds=45)
        c.execute("UPDATE attempts SET created_at=?,lease_until=?", (H.now(), original_lease))
    print("SCALE_SEED_SECONDS", round(time.monotonic() - started, 2), flush=True)
    rec = flight.Recorder()
    store.recorder, store.factory = rec, flight.connection_class(rec)
    store.pooled = _pooled(store.factory)
    with store.pool_lock:
        for c in store.pool:
            c.close()
        store.pool.clear()
    monkeypatch.setattr(flight, 'LONG_HOLD_MS', 10)
    holds = []
    original_hold = rec.long_hold
    def hold(route, site, hold_ms, wait_ms):
        holds.append({'route': route, 'site': site, 'hold_s': hold_ms/1000, 'wait_s': wait_ms/1000})
        original_hold(route, site, hold_ms, wait_ms)
    monkeypatch.setattr(rec, 'long_hold', hold)
    prepared = threading.Event()
    original = worktrees.live_turn_cache

    def prepare(c, runner):
        result = original(c, runner)
        # An unrelated event commit invalidates main's data_version optimization. This is
        # deterministic traffic overlap, without sleeping or changing the live database.
        with store.transaction() as write:
            write.execute("UPDATE tasks SET updated_at=? WHERE id='task-199'", (H.now(),))
        print("SCALE_PREPARE_SECONDS", round(time.monotonic() - started, 2), flush=True)
        prepared.set()
        return result
    monkeypatch.setattr(worktrees, 'live_turn_cache', prepare)
    samples = {n: [] for n in ('renew', 'inputs', 'events', 'task')}
    failures = []

    def background(route, call):
        method, template = route.split(' ', 1)
        _, token = flight.request_started(method, {'route': SimpleNamespace(path=template)})
        try:
            return call()
        finally:
            flight.request_finished(token)

    def request(name, i):
        route = 'GET /api/v2/tasks/{tid}' if name == 'task' else f'POST /api/v2/attempts/{{aid}}/{name}'
        method, template = route.split(' ', 1)
        cost, token = flight.request_started(method, {'route': SimpleNamespace(path=template)})
        start = time.monotonic()
        try:
            response = (api.get(f'/api/v2/tasks/task-{i}', headers=headers()) if name == 'task' else
                        api.post(f'/api/v2/attempts/attempt-{i}/{name}', headers=headers(computer['token']),
                                 json={'events': [{'seq': 1, 'kind': 'delta', 'payload': {'text': 'Synthetic'}}]} if name == 'events' else {}))
            samples[name].append(((time.monotonic()-start)*1000, cost[1], cost[0]))
            if response.status_code != 200:
                failures.append((name, response.status_code, response.text[:200]))
        finally:
            flight.request_finished(token)

    def beat():
        return background('POST /api/v2/runners/heartbeat', lambda: api.post(
            '/api/v2/runners/heartbeat', headers=headers(computer['token']), json={
                'version': 'synthetic', 'platform': 'test', 'capacity': 200,
                'capabilities': [execution.LANES, execution.ROOTS],
                'worktrees': [{'link_id': f'tree-{i}', 'state': 'present', 'checkout_state': 'ready',
                              'last_commit': 'synthetic', 'last_activity': 1} for i in range(100, 200)],
                'readiness': {'schema_version': 1, 'bots': {'ops': {'ready': True}}, 'worktrees': True}}))

    with ThreadPoolExecutor(max_workers=12) as pool:
        heartbeat = pool.submit(beat)
        assert prepared.wait(60)
        tick = pool.submit(background, 'TICK scheduler', Scheduler(store, api.app.state.execution).tick)
        claims = [pool.submit(background, 'POST /api/v2/jobs/claim', lambda: api.post(
            '/api/v2/jobs/claim', headers=headers(computer['token']), json={})) for _ in range(3)]
        requests = [pool.submit(request, name, i) for i in range(100) for name in samples]
        for pending in requests:
            pending.result(timeout=120)
        assert heartbeat.result().status_code == 200
        assert not tick.result()['failures']
        claimed = [p.result() for p in claims]
        assert all(p.status_code == 200 for p in claimed)
        assert any(p.json().get('attempt') for p in claimed), [p.json() for p in claimed]
    def p95(values):
        return round(sorted(values)[math.ceil(len(values)*.95)-1], 2)
    receipt = {name: {'n': len(rows), 'p95_ms': p95([r[0] for r in rows]),
                      'wait_p95_ms': p95([r[1] for r in rows]), 'sql_p95_ms': p95([r[2] for r in rows]),
                      'max_ms': round(max(r[0] for r in rows), 2)} for name, rows in samples.items()}
    rec.drain()
    print('SCALE_RECEIPT', json.dumps(receipt, sort_keys=True))
    print('SCALE_HOLDERS', json.dumps(sorted(holds, key=lambda h:-h['hold_s'])[:20]))
    # sched_getaffinity is Linux only; elsewhere the receipt records the machine's CPU count.
    print('SCALE_CPU', sorted(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity') else os.cpu_count())
    assert not failures, failures
    with store.read() as c:
        assert c.execute("SELECT count(*) FROM attempts WHERE id LIKE 'attempt-%' AND state='running' AND lease_until>?", (H.now(),)).fetchone()[0] == 100
        assert c.execute("SELECT count(*) FROM attempts WHERE id LIKE 'attempt-%' AND lease_until>?", (original_lease,)).fetchone()[0] == 100
        assert c.execute("SELECT count(*) FROM messages WHERE recipient_id='bot:finance'").fetchone()[0] >= 10
    # Wall-clock p95 under twelve threads on a shared VM varies run to run; the receipt above records it, and the lock
    # holds below are what the contention fix bounds. A hold is wall time, so it includes GIL waits behind the other
    # eleven threads: the heartbeat's cache-miss write runs about 0.2 s and has reached 0.87 s on a busy release VM with
    # nothing on its path changed. Before the fix, holds ran for seconds; this bound, on every write hold here, still
    # fails a hold of 1.5 s or more, and lets a slower but sub-second write pass rather than fail on the scheduler's
    # spread. It is the test's only timing guard: the lease counts above show each renew wrote a lease, not timing.
    assert all(h['hold_s'] <= 1.5 for h in holds), sorted(holds, key=lambda h:-h['hold_s'])[:10]
