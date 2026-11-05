"""Retry busy queues without starving a free pool, scanning the backlog, or rewriting idle waits."""
import importlib
import json
import sqlite3

import pytest

from backend import check_pools as P, hubdb as H
from backend.tests.test_check_pools import busy, checks, team
from backend.tests.test_github import api  # noqa: F401
from backend.tests.test_review_gate import HEAD


def waiting_ticket(api, number, reviewer):
    with api.app.state.store.transaction() as c:
        typ = H.type_create(c, H.KEEPER, f'Retry queue {number}', [
            {'name': 'Build', 'status': 'doing'},
            {'name': 'Check', 'status': 'review',
             'flow': {'check': 'review', 'on_pass': 'Build', 'on_fail': 'Build'}},
        ])
        task = H.task_create(c, 'human:ana', f'Change {number}', '', 'bot:cpo', type=typ['id'], step='Check',
                             private=False, mover=True)
        link = H.task_link(c, 'human:ana', task['id'], f'https://github.com/acme/atlas/pull/{number}')
        c.execute("UPDATE task_links SET state='open',checks='passing',mergeable='clean',detail=? WHERE id=?",
                  (json.dumps({'head_sha': HEAD}), link['id']))
        c.execute('UPDATE task_steps SET check_pool=?,check_limit=1 WHERE id=?',
                  (json.dumps([reviewer]), task['step_id']))
        step = dict(c.execute('SELECT * FROM task_steps WHERE id=?', (task['step_id'],)).fetchone())
        wait = P._wait(c, task, step, f'Check: PR #{number}', [HEAD], 'no reviewer in the pool is online')
    return task, wait


def backlog(c, wait, count, prefix='older'):
    """Queue rows suffice for selection: blocked pools must never reach ticket ranking or filing."""
    c.executemany('INSERT INTO check_waits(id,ticket_id,step_id,title,heads,reason,state,created_at,updated_at) '
                  "VALUES(?,?,?,?,?,?,'waiting',?,?)",
                  [(f'{prefix}-{n:06}', wait['ticket_id'], wait['step_id'], wait['title'], wait['heads'],
                    wait['reason'], '2020-01-01T00:00:00Z', '2020-01-01T00:00:00Z') for n in range(count)])


def self_owned_waits(c, task, wait, count):
    step = dict(c.execute('SELECT * FROM task_steps WHERE id=?', (task['step_id'],)).fetchone())
    _, reason = P.ranked(c, {**task, 'owner_id': 'bot:rev-a'}, step, ['rev-a'], 1)
    for n in range(count):
        tid, wid = f'self-{n:03}', f'wait-self-{n:03}'
        c.execute('INSERT INTO tasks(id,title,requester_id,owner_id,status,type_id,step_id,created_at,updated_at,private) '
                  "VALUES(?,'Own change','human:ana','bot:rev-a','review',?,?,?,?,0)",
                  (tid, task['type_id'], task['step_id'], H.now(), H.now()))
        c.execute('INSERT INTO check_waits(id,ticket_id,step_id,title,heads,reason,state,created_at,updated_at) '
                  "VALUES(?,?,?,?,?,?,'waiting',?,?)",
                  (wid, tid, task['step_id'], wait['title'], HEAD, reason,
                   '2020-01-01T00:00:00Z', '2020-01-01T00:00:00Z'))
    return reason


def test_a_thousand_busy_waits_do_not_consume_a_free_pools_retry_batch(api):
    team(api)
    busy(api, 'rev-a')
    _, blocked = waiting_ticket(api, 501, 'rev-a')
    free, _ = waiting_ticket(api, 502, 'rev-b')
    store = api.app.state.store
    with store.transaction() as c:
        backlog(c, blocked, 1000)
        before = [tuple(r) for r in c.execute('SELECT * FROM check_waits WHERE step_id=? ORDER BY id',
                                              (blocked['step_id'],))]
    [cid] = P.retry(store, limit=1)
    assert [t['id'] for t in checks(api, free)] == [cid]
    with store.read_transaction() as c:
        assert H.task(c, cid)['owner_id'] == 'bot:rev-b'
        assert [tuple(r) for r in c.execute('SELECT * FROM check_waits WHERE step_id=? ORDER BY id',
                                           (blocked['step_id'],))] == before


def test_unchanged_busy_waits_never_open_a_writer_or_run_ticket_ranking(api, monkeypatch):
    team(api)
    busy(api, 'rev-a')
    _, wait = waiting_ticket(api, 503, 'rev-a')
    store = api.app.state.store
    with store.transaction() as c:
        backlog(c, wait, 1000)

    def unexpected(*args, **kwargs):
        raise AssertionError('a saturated pool needs neither ranking nor a writer')

    monkeypatch.setattr(store, 'transaction', unexpected)
    monkeypatch.setattr(P, 'ranked', unexpected)
    assert P.retry(store) == []
    assert P.retry(store) == []


def test_ineligible_waits_advance_without_idle_writes_and_do_not_starve_the_same_pool(api, monkeypatch):
    team(api)
    task, wait = waiting_ticket(api, 504, 'rev-a')
    store = api.app.state.store
    with store.transaction() as c:
        self_owned_waits(c, task, wait, P.RETRY_LIMIT + 1)

    def unexpected(*args, **kwargs):
        raise AssertionError('unchanged ineligible waits need no writer')

    with monkeypatch.context() as patched:
        patched.setattr(store, 'transaction', unexpected)
        assert P.retry(store) == []
    [cid] = P.retry(store)
    assert [t['id'] for t in checks(api, task)] == [cid]
    with store.read_transaction() as c:
        assert c.execute("SELECT count(*) FROM check_waits WHERE id LIKE 'wait-self-%' AND state='waiting' "
                         "AND updated_at='2020-01-01T00:00:00Z'").fetchone()[0] == P.RETRY_LIMIT + 1


@pytest.mark.parametrize('limit', [1, P.RETRY_LIMIT])
def test_ineligible_waits_do_not_starve_other_pools_across_retry_passes(api, monkeypatch, limit):
    team(api)
    blocked, wait = waiting_ticket(api, 506, 'rev-a')
    eligible = [waiting_ticket(api, number, reviewer)[0] for number, reviewer in ((507, 'rev-b'), (508, 'rev-c'))]
    store = api.app.state.store
    with store.transaction() as c:
        reason = self_owned_waits(c, blocked, wait, P.RETRY_LIMIT - 1)
        c.execute("UPDATE tasks SET owner_id='bot:rev-a' WHERE id=?", (blocked['id'],))
        c.execute("UPDATE check_waits SET reason=?,created_at='2019-01-01T00:00:00Z' WHERE id=?", (reason, wait['id']))
        before = [tuple(r) for r in c.execute('SELECT * FROM check_waits WHERE step_id=? ORDER BY id', (blocked['step_id'],))]

    # Start at the ineligible pool so a one-wait batch must advance to another pool on its next pass.
    step_ids = sorted([blocked['step_id'], *(task['step_id'] for task in eligible)])
    store._check_retry_step = step_ids[step_ids.index(blocked['step_id']) - 1]
    filed = []
    for turn in range(3):
        batch = P.retry(store, limit=limit)
        assert len(batch) <= limit
        if turn == 0 and limit == 1:
            assert batch == []
        filed.extend(batch)
    assert len(filed) == len(set(filed)) == 2
    for task, reviewer in zip(eligible, ('rev-b', 'rev-c')):
        [check] = checks(api, task)
        assert check['id'] in filed and check['owner_id'] == 'bot:' + reviewer

    def unexpected(*args, **kwargs):
        raise AssertionError('unchanged ineligible waits need no writer after other pools make progress')

    with monkeypatch.context() as patched:
        patched.setattr(store, 'transaction', unexpected)
        for _ in range(3):
            assert P.retry(store, limit=limit) == []
    with store.read_transaction() as c:
        assert [tuple(r) for r in c.execute('SELECT * FROM check_waits WHERE step_id=? ORDER BY id',
                                           (blocked['step_id'],))] == before


def test_retry_read_work_and_query_plan_stay_bounded_as_the_queue_grows(api):
    team(api)
    _, wait = waiting_ticket(api, 505, 'rev-a')
    store = api.app.state.store

    def measured(after):
        ticks = 0

        def progress():
            nonlocal ticks
            ticks += 1

        with store.read_transaction() as c:
            c.set_progress_handler(progress, 1)
            try:
                rows = P._retry_waits(c, P.RETRY_LIMIT, after)
            finally:
                c.set_progress_handler(None, 0)
        assert len(rows) == P.RETRY_LIMIT
        return ticks

    with store.transaction() as c:
        backlog(c, wait, 25)
    small = measured({})
    with store.transaction() as c:
        backlog(c, wait, 5000, prefix='later')
    large = measured({})
    deep = measured({wait['step_id']: ('2020-01-01T00:00:00Z', 'later-004000')})
    assert large <= small + 200, (small, large)
    assert deep <= small + 200, (small, deep)
    with store.read_transaction() as c:
        plan = [r[3] for r in c.execute('EXPLAIN QUERY PLAN ' + P.RETRY_WAITS_SQL,
                                        (wait['step_id'], '', '', P.RETRY_LIMIT))]
    assert any('SEARCH check_waits USING' in line and 'check_waits_retry' in line for line in plan), plan
    assert not any('TEMP B-TREE' in line or 'SCAN check_waits' in line for line in plan), plan


def test_retry_index_migration_preserves_existing_waits_and_is_idempotent():
    migration = importlib.import_module('backend.migrations.20261105050420_index_check_waits_retry')
    with sqlite3.connect(':memory:') as c:
        c.executescript(P.SCHEMA)
        c.execute("INSERT INTO check_waits(id,ticket_id,step_id,title,created_at,updated_at) "
                  "VALUES('wait','ticket','step','Check','2020','2020')")
        migration.up(c)
        migration.up(c)
        assert c.execute('SELECT id,state FROM check_waits').fetchall() == [('wait', 'waiting')]
        plan = [r[3] for r in c.execute('EXPLAIN QUERY PLAN ' + P.RETRY_WAITS_SQL, ('step', '', '', 20))]
        assert any('check_waits_retry' in line for line in plan), plan
        assert not any('TEMP B-TREE' in line for line in plan), plan
