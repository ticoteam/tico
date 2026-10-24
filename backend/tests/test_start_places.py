"""Busy-turns v3: synthetic workers, real transactional claims, no provider calls."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.store import H
from backend.tests.test_api import api, assign, claim, get, headers, post, ready  # noqa: F401
from backend.tests.test_attempt_contention import fleet
from backend.tests.test_runner_load import point


def waiters(api, n=37, running=3):
    computer = fleet(api, attempts=n + running, trees=0)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runners SET capacity=? WHERE id=?", (n + running, computer['runner_id']))
        for i in range(n):
            c.execute("UPDATE attempts SET state='leased' WHERE id=?", (f'attempt-{i}',))
            c.execute("UPDATE jobs SET state='leased' WHERE attempt_id=?", (f'attempt-{i}',))
    for i in range(n):
        post(api, f'attempts/attempt-{i}/events', {'events': [
            {'seq': 1, 'kind': 'status', 'payload': {'waiting': 'Waiting for the shared ChatGPT login', 'login': 'ChatGPT'}}
        ]}, computer['token'])
    return computer


def test_four_counts_separate_37_login_waiters(api):
    """Spec 1/2: the original 40-of-40 case leaves room for a different login."""
    computer = waiters(api)
    rid = computer['runner_id']
    machine = next(m for m in get(api, 'operations')['machines'] if m['id'] == rid)
    assert {k: machine.get('turn_counts', {}).get(k) for k in ('running', 'starting', 'waiting', 'queued')} == {
        'running': 3, 'starting': 0, 'waiting': 37, 'queued': 0}
    assert 'shared login' in machine['turn_summary'] and 'oldest' in machine['turn_summary']
    health = get(api, 'health')
    assert next(c for c in health['checks'] if c['id'] == 'turns:' + rid)['summary'].endswith(machine['turn_summary'])


def test_unrelated_work_and_free_turn_ignore_37_login_waiters(api):
    """Spec 2: a different login starts immediately on an otherwise idle computer."""
    computer = waiters(api)
    rid = computer['runner_id']
    assign(api, computer, 'finance')
    ready(api, computer, ['ops', 'finance'])
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE runners SET capacity=40 WHERE id=?', (rid,))
    post(api, 'chat/finance', {'text': 'Independent work on another login'})
    attempt = claim(api, computer, 'finance')
    assert attempt is not None and attempt['bot'] == 'finance'
    with api.app.state.store.read() as c:
        from backend.views import free_turn
        assert free_turn(c, 'ops', {'machine': {'runner_id': rid, 'label': 'Test Mac'}}) == ''


def test_two_ready_waiters_claim_last_start_place_atomically(api):
    """Spec 3: renewable delivery leases give neither waiter a start place."""
    computer = waiters(api, n=2, running=0)
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE runners SET capacity=1 WHERE id=?', (computer['runner_id'],))

    def reserve(i):
        return post(api, f'attempts/attempt-{i}/start-place', {}, computer['token'])

    with ThreadPoolExecutor(max_workers=2) as pool:
        answers = list(pool.map(reserve, range(2)))
    assert sum(a['reserved'] for a in answers) == 1
    winner = next(i for i, a in enumerate(answers) if a['reserved'])
    post(api, f'attempts/attempt-{winner}/started', {'thread_id': 'synthetic-thread'}, computer['token'])
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM attempts WHERE state='running'").fetchone()[0] == 1


def test_start_reservation_expires_despite_renewals_and_leaves_task_untouched(api, monkeypatch):
    """Spec 4: three minutes is a hard preparation deadline, not another renewable lease."""
    from backend.tests.test_task_lanes import task_bot, new_task
    computer = task_bot(api, lanes=True)
    task = new_task(api, "Work whose preparation times out")
    attempt = claim(api, computer)
    assert attempt['task']['id'] == task['id']
    store = api.app.state.store
    aid = attempt['id']
    now = H.now()
    with store.read() as c:
        before = [tuple(r) for r in c.execute('SELECT id,status,version,note,updated FROM tasks ORDER BY id')]
    for elapsed in (60, 120):
        monkeypatch.setattr(H, 'now', lambda elapsed=elapsed: H.shift(now, seconds=elapsed))
        post(api, f'attempts/{aid}/renew', {}, computer['token'])
    monkeypatch.setattr(H, 'now', lambda: H.shift(now, seconds=181))
    with store.transaction() as c:
        # Keep the delivery lease long: only the reservation has expired.
        c.execute('UPDATE attempts SET lease_until=? WHERE id=?', (H.shift(now, seconds=600), aid))
        from backend import start_places
        start_places.waiting(c, aid, 'shared login')
        assert aid in start_places.overdue(c)  # A late wait report cannot erase the deadline.
        api.app.state.execution.expire(c)
    with store.read() as c:
        assert c.execute('SELECT state FROM attempts WHERE id=?', (aid,)).fetchone()[0] == 'expired'
        assert c.execute('SELECT state FROM jobs WHERE id=?', (attempt['job_id'],)).fetchone()[0] == 'queued'
        assert [tuple(r) for r in c.execute('SELECT id,status,version,note,updated FROM tasks ORDER BY id')] == before
    denied = api.post(f'/api/v2/attempts/{aid}/started', json={'thread_id': 'too-late'}, headers=headers(computer['token']))
    assert denied.status_code == 409


@pytest.mark.parametrize('cpu,mem', [(95, .5), (20, .95)])
def test_overload_holds_only_new_starts_and_calm_load_resumes_them(api, cpu, mem):
    """Spec 14: the existing sampler's five-minute CPU/memory signal brakes this computer only."""
    import time
    computer = fleet(api, attempts=1, trees=0)
    assign(api, computer, 'finance')
    ready(api, computer, ['ops', 'finance'])
    post(api, 'chat/finance', {'text': 'New work'})
    now = int(time.time()) // 60 * 60
    post(api, 'runners/performance', {'samples': [point(now - 300 + i * 60, cpu=cpu, mem=mem) for i in range(6)]}, computer['token'])
    assert claim(api, computer, 'finance') is None
    machine = next(m for m in get(api, 'operations')['machines'] if m['id'] == computer['runner_id'])
    assert any(w['reason'] == 'computer busy' for w in machine['turn_counts']['waits'])
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state,stop_requested FROM attempts WHERE id='attempt-0'").fetchone()[:] == ('running', None)
    post(api, 'runners/performance', {'samples': [point(now + 1, cpu=20)]}, computer['token'])
    assert claim(api, computer, 'finance') is not None


def test_stop_on_waiter_is_final_even_after_capacity_returns(api):
    """Spec 12 on the candidate: Stop still wins when a waiter releases its computer place."""
    computer = waiters(api, n=1, running=0)
    post(api, 'bots/ops/stop', {'attempt_id': 'attempt-0'})
    answer = post(api, 'attempts/attempt-0/renew', {}, computer['token'])
    assert answer['stop'] is True
    denied = post(api, 'attempts/attempt-0/start-place', {}, computer['token'])
    assert denied['reserved'] is False and denied['stop'] is True
    post(api, 'attempts/attempt-0/complete', {'outcome': 'interrupted', 'text': '', 'last_seq': 1}, computer['token'])
    assert claim(api, computer, 'ops') is None
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM jobs WHERE attempt_id='attempt-0'").fetchone()[0] == 'cancelled'


def test_upgrade_preserves_legacy_waits_and_only_unstarted_places_expire(api):
    from backend import start_places
    computer = waiters(api, n=2, running=1)
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM start_places")
        old = H.shift(H.now(), seconds=-7200)
        c.execute("UPDATE attempts SET created=?", (old,))
        # One legacy worker cleared its wait and really is preparing; the other still waits.
        c.execute("INSERT INTO attempt_events(attempt_id,seq,kind,payload_json,created) "
                  "VALUES('attempt-1',2,'status',?,?)", ('{"waiting":""}', H.now()))
        start_places.restore_waits(c)
        assert start_places.overdue(c) == ['attempt-1']
        assert sum(start_places.held(c, computer['runner_id']).values()) == 1
        counts = start_places.summary(c, computer['runner_id'], api.app.state.store.settings)
        assert counts['running'] == 1 and counts['waiting'] == 1
        assert counts['waits'][0]['oldest'] == old
