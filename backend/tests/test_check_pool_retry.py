"""Retry busy queues without starving a free pool, scanning the backlog, or rewriting idle waits."""
import importlib
import json
import sqlite3
from contextlib import contextmanager

import pytest

from backend import check_pools as P, hubdb as H, review_gate
from backend.tests.test_check_pools import busy, checks, set_pool, team
from backend.tests.test_github import api  # noqa: F401
from backend.tests.test_review_gate import HEAD, OLD, move, ticket
from backend.tests.test_review_gate_fresh import github


def waiting_ticket(api, number, reviewer, kind='review'):
    with api.app.state.store.transaction() as c:
        typ = H.type_create(c, H.KEEPER, f'Retry queue {number}', [
            {'name': 'Build', 'status': 'doing'},
            {'name': 'Check', 'status': 'review',
             'flow': {'check': kind, 'on_pass': 'Build', 'on_fail': 'Build'}},
        ])
        task = H.task_create(c, 'human:ana', f'Change {number}', '', 'bot:cpo', type=typ['id'], step='Check',
                             private=False, mover=True)
        link = H.task_link(c, 'human:ana', task['id'], f'https://github.com/acme/atlas/pull/{number}')
        c.execute("UPDATE task_links SET state='open',checks='passing',mergeable='clean',detail=? WHERE id=?",
                  (json.dumps({'head_sha': HEAD}), link['id']))
        c.execute('UPDATE task_steps SET check_pool=?,check_limit=1 WHERE id=?',
                  (json.dumps([reviewer]), task['step_id']))
        step = dict(c.execute('SELECT * FROM task_steps WHERE id=?', (task['step_id'],)).fetchone())
        wait = P._wait(c, task, step, f'{P.kind(step)}: PR #{number}', [HEAD], 'no reviewer in the pool is online')
    return task, wait


@pytest.mark.parametrize('kind,ci', [('review', 'success'), ('qa', 'failure'), ('release_qa', 'success')])
def test_retry_requests_the_fresh_head_after_the_cached_pr_moves_on(api, monkeypatch, kind, ci):
    from backend import verdict_moves as V
    team(api, seen=H.shift(H.now(), minutes=-10))
    task, wait = waiting_ticket(api, 509, 'rev-a', kind=kind)
    store, service = api.app.state.store, api.app.state.github_app
    with store.transaction() as c:
        # The wait has H, but a PR refresh records J before a checker comes online.
        c.execute("UPDATE task_links SET detail=?,state=? WHERE task_id=? AND kind='pr'",
                  (json.dumps({'head_sha': OLD}), 'merged' if kind == 'release_qa' else 'open', task['id']))
        c.execute("UPDATE runner_states SET last_seen_at=? WHERE runner_id='r1'", (H.now(),))
    reads = github(api, monkeypatch, {'python': ci}, head=OLD)
    [cid] = P.retry(store, service=service)
    assert any('/pulls/509' in path for path in reads)
    with store.transaction() as c:
        check = H.task(c, cid)
        assert f'at head {OLD}.' in check['body'] and HEAD not in check['body']
        assert c.execute('SELECT state,child_id,heads FROM check_waits WHERE id=?',
                         (wait['id'],)).fetchone()[:] == ('filed', cid, OLD)
        assert c.execute("SELECT new FROM history_events WHERE subject_id=? AND kind='review_head'",
                         (cid,)).fetchone()[0] == OLD
        attempt = H.new_id()
        c.execute('INSERT INTO attempts(id,bot_id,task_id,created_at,settled_at) VALUES(?,?,?,?,?)',
                  (attempt, 'rev-a', cid, H.now(), H.now()))
        H.task_update(c, 'bot:rev-a', cid, status='done', note='Checked.',
                      verdict='changes' if kind == 'review' else 'fail', verdict_head=OLD)
        outcome = V.after_capture(c, cid, OLD, attempt)
        assert outcome and outcome['moved'] == 'Build'


def test_a_qa_retry_waits_without_writes_when_the_fresh_head_is_unavailable(api, monkeypatch):
    team(api)
    task, wait = waiting_ticket(api, 510, 'rev-a', kind='qa')
    store, service = api.app.state.store, api.app.state.github_app
    github(api, monkeypatch, {'python': 'success'}, head=OLD)
    with store.transaction() as c:
        c.execute("UPDATE task_links SET detail=? WHERE task_id=? AND kind='pr'",
                  (json.dumps({'head_sha': OLD}), task['id']))
        before = dict(c.execute('SELECT * FROM check_waits WHERE id=?', (wait['id'],)).fetchone())

    def unavailable(*args, **kwargs):
        raise ConnectionError('GitHub unavailable')

    def unexpected(*args, **kwargs):
        raise AssertionError('an unread head must leave the QA wait alone')

    with monkeypatch.context() as patched:
        patched.setattr(service, 'mint', unavailable)
        patched.setattr(store, 'transaction', unexpected)
        assert P.retry(store, service=service) == []
    with store.read_transaction() as c:
        assert P.waiting(c, task['id']) == [before]
    [cid] = P.retry(store, service=service)
    assert [check['id'] for check in checks(api, task)] == [cid]


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


def ci_only_wait(api, monkeypatch):
    team(api, seen=H.shift(H.now(), minutes=-10))
    task = ticket(api)
    set_pool(api, task, 'Review 1', ['rev-a'], limit=1)
    reads = github(api, monkeypatch, {'python': 'success'})
    response = move(api, task)
    assert response.status_code == 200, response.text
    assert checks(api, task) == []
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runner_states SET last_seen_at=? WHERE runner_id='r1'", (H.now(),))
        wait = dict(c.execute("SELECT * FROM check_waits WHERE ticket_id=? AND state='waiting'", (task['id'],)).fetchone())
        assert not H.task(c, task['id'])['note']
    reads.clear()
    return task, wait, reads


def test_scheduler_files_a_review_admitted_by_fresh_ci_without_a_receipt(api, monkeypatch):
    from backend.scheduler import Scheduler
    task, wait, reads = ci_only_wait(api, monkeypatch)
    store, service = api.app.state.store, api.app.state.github_app
    transaction, writers = store.transaction, 0

    @contextmanager
    def writing(*args, **kwargs):
        nonlocal writers
        with transaction(*args, **kwargs) as c:
            writers += 1
            try:
                yield c
            finally:
                writers -= 1

    snapshot = review_gate.snapshot

    def fresh(*args):
        assert writers == 0, 'GitHub reads must not hold the writer'
        return snapshot(*args)

    monkeypatch.setattr(store, 'transaction', writing)
    monkeypatch.setattr(review_gate, 'snapshot', fresh)
    scheduler = Scheduler(store, api.app.state.execution, github_app=service)
    result = scheduler.tick(H.parse_ts(H.now()))
    assert not any('check_pool_retry' in failure for failure in result['failures'])
    [check] = checks(api, task)
    assert check['owner_id'] == 'bot:rev-a'
    assert any('/commits/' + HEAD + '/check-runs' in path for path in reads)
    assert P.retry(store, service=service) == []
    with store.read_transaction() as c:
        assert c.execute('SELECT state,child_id FROM check_waits WHERE id=?', (wait['id'],)).fetchone()[:] == ('filed', check['id'])
        assert c.execute("SELECT new FROM history_events WHERE subject_id=? AND kind='review_head'",
                         (check['id'],)).fetchone()[0] == HEAD
        assert not c.execute("SELECT 1 FROM history_events WHERE subject_id=? AND kind='check_not_filed'",
                             (task['id'],)).fetchone()


@pytest.mark.parametrize('ci', ['success', 'failure', 'unavailable'])
def test_retry_keeps_a_new_heads_wait_while_the_cached_heads_check_is_open(api, monkeypatch, ci):
    team(api)
    task = ticket(api)
    set_pool(api, task, 'Review 1', ['rev-a', 'rev-b'], limit=1)
    github(api, monkeypatch, {'python': 'success'})
    response = move(api, task)
    assert response.status_code == 200, response.text
    [old_check] = checks(api, task)
    assert old_check['owner_id'] == 'bot:rev-a'
    response = move(api, task, 'In Progress')
    assert response.status_code == 200, response.text
    store, service = api.app.state.store, api.app.state.github_app
    with store.transaction() as c:
        c.execute("UPDATE runner_states SET last_seen_at=? WHERE runner_id='r1'", (H.shift(H.now(), minutes=-10),))

    # GitHub has J, but its webhook has not replaced cached H. H's review stays open across re-entry.
    github(api, monkeypatch, {'python': 'success'}, head=OLD)
    response = move(api, task)
    assert response.status_code == 200, response.text
    assert [check['id'] for check in checks(api, task)] == [old_check['id']]
    with store.transaction() as c:
        [wait] = P.waiting(c, task['id'])
        assert wait['heads'] == OLD
        [pr] = review_gate.links(c, H.task(c, task['id']))
        assert json.loads(pr['detail'])['head_sha'] == HEAD
        assert c.execute("SELECT new FROM history_events WHERE subject_id=? AND kind='review_head'",
                         (old_check['id'],)).fetchone()[0] == HEAD
        c.execute("UPDATE runner_states SET last_seen_at=? WHERE runner_id='r1'", (H.now(),))

    # Without fresh evidence the retry must keep J's wait, even though cached H already has a check.
    assert P.retry(store) == []
    with store.read_transaction() as c:
        assert P.waiting(c, task['id']) == [wait]

    with monkeypatch.context() as patched:
        reads = github(api, patched, {'python': 'failure' if ci == 'failure' else 'success'}, head=OLD)
        if ci == 'unavailable':
            def unavailable(*args, **kwargs):
                raise ConnectionError('GitHub unavailable')
            patched.setattr(service, 'mint', unavailable)
        filed = P.retry(store, service=service)
    if ci != 'success':
        assert filed == []
        with store.read_transaction() as c:
            assert P.waiting(c, task['id']) == [wait]
        reads = github(api, monkeypatch, {'python': 'success'}, head=OLD)
        filed = P.retry(store, service=service)

    [cid] = filed
    assert cid != old_check['id']
    assert {check['id'] for check in checks(api, task)} == {old_check['id'], cid}
    assert any('/commits/' + OLD + '/check-runs' in path for path in reads)
    with store.read_transaction() as c:
        assert H.task(c, cid)['owner_id'] == 'bot:rev-b'
        assert c.execute('SELECT state,child_id,heads FROM check_waits WHERE id=?',
                         (wait['id'],)).fetchone()[:] == ('filed', cid, OLD)
        assert c.execute("SELECT new FROM history_events WHERE subject_id=? AND kind='review_head'",
                         (cid,)).fetchone()[0] == OLD
    assert P.retry(store, service=service) == []


@pytest.mark.parametrize('conclusion', ['failure', None])
def test_refused_ci_only_waits_need_no_writer_until_gate_inputs_change(api, monkeypatch, conclusion):
    task, wait, _ = ci_only_wait(api, monkeypatch)
    store, service = api.app.state.store, api.app.state.github_app
    github(api, monkeypatch, {'python': conclusion})
    with store.read_transaction() as c:
        before = tuple(c.execute('SELECT * FROM check_waits WHERE id=?', (wait['id'],)).fetchone())

    def unexpected(*args, **kwargs):
        raise AssertionError('a refused wait needs no writer or filing attempt')

    with monkeypatch.context() as patched:
        patched.setattr(store, 'transaction', unexpected)
        patched.setattr(P, '_file', unexpected)
        for _ in range(3):
            assert P.retry(store, service=service) == []
    assert checks(api, task) == []
    with store.read_transaction() as c:
        assert tuple(c.execute('SELECT * FROM check_waits WHERE id=?', (wait['id'],)).fetchone()) == before
        assert not c.execute("SELECT 1 FROM history_events WHERE subject_id=? AND kind='check_not_filed'",
                             (task['id'],)).fetchone()
    current_head = OLD if conclusion == 'failure' else HEAD
    github(api, monkeypatch, {'python': 'success'}, head=current_head)
    [cid] = P.retry(store, service=service)
    assert [t['id'] for t in checks(api, task)] == [cid]
    with store.read_transaction() as c:
        assert c.execute('SELECT heads FROM check_waits WHERE id=?', (wait['id'],)).fetchone()[0] == current_head
        assert c.execute("SELECT new FROM history_events WHERE subject_id=? AND kind='review_head'", (cid,)).fetchone()[0] == current_head
