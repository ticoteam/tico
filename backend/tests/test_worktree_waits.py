"""A task waiting for room for a worktree: the turn ends, the task is woken once its worktree is ready (synthetic)."""
import json
import uuid

from backend import hubdb as H
from backend import worktree_waits as waits
from backend import worktrees as W
from backend.tests.test_github_app import api, gh, auth, runner_token  # noqa: F401
from backend.tests.test_repositories import put
from backend.tests.test_worktrees import prepared, post  # noqa: F401

READY = {'schema_version': 1, 'bots': {}, 'worktrees': True, 'worktree_waits': True}


def turn_on(c, task_id):
    """The synthetic turn (attempt a1) is this task's: its message is in the task's room and names it."""
    room = H.task(c, task_id)['conversation_id']
    c.execute("UPDATE messages SET refs=?,conversation_id=? WHERE id='m1'", (json.dumps({'task': task_id}), room))


def setup_wait(api, tid, readiness=READY):
    """The bot's turn works on `tid`, and the bot already holds LIMIT worktrees on another task."""
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE runners SET readiness=? WHERE id='r1'", (json.dumps(readiness),))
        turn_on(c, tid)
        filler = H.task_create(c, 'human:ana', 'Earlier work', 'Older worktrees', 'bot:cmo')
        c.execute("UPDATE tasks SET status='doing' WHERE status='open'")  # a started task gets a worktree
        links = []
        for i in range(W.LIMIT):
            link = uuid.uuid4().hex
            c.execute("INSERT INTO task_links(id,task_id,kind,url,title,state,added_by,created_at,repo,branch,computer_id,"
                      "path,detail,updated_at) VALUES(?,?,'worktree',?,'w','present','bot:cmo',?,'Acme/product',?,"
                      "'r1',?,?,?)", (link, filler['id'], 'worktree:' + link, H.now(), f'tico/f{i}',
                                      f'tasks/{filler["id"][:8]}/f{i}',
                                      json.dumps({'owner': 'bot:cmo', 'checkout_state': 'ready'}), H.now()))
            links.append(link)
    return filler['id'], links


def beat(api, reports=(), readiness=READY):
    body = {'version': '0.3.44', 'platform': 'test', 'readiness': readiness, 'worktrees': list(reports)}
    response = post(api, 'runners/heartbeat', body, 'runner-test')
    assert response.status_code == 200, response.text
    return response.json()['worktree_actions']


def runner_patch(api, tid, link_id, **fields):
    return api.patch(f'/api/v2/tasks/{tid}/links/{link_id}', json=fields,
                     headers={**auth('runner-test'), 'Idempotency-Key': uuid.uuid4().hex})


def finish_setup(api, tid, link_id):
    for fields in ({'state': 'pending', 'checkout_state': 'initializing', 'setup_pending': True,
                    'expected_head': 'a' * 40, 'checkout_target': 'refs/remotes/origin/main', 'expected_base': 'b' * 64},
                   {'state': 'pending', 'checkout_state': 'checkout_ready', 'setup_pending': True},
                   {'state': 'pending', 'checkout_state': 'setup_running', 'setup_pending': True}):
        assert runner_patch(api, tid, link_id, **fields).status_code == 200
    return runner_patch(api, tid, link_id, state='present', checkout_state='ready', setup_pending=False,
                        expected_head='a' * 40, checkout_target='refs/remotes/origin/main', expected_base='b' * 64)


def rows(api, sql, args=()):
    with api.app_state.store.read() as c:
        return [dict(r) for r in c.execute(sql, args)]


def wakes(api, tid):
    return rows(api, "SELECT m.* FROM messages m WHERE m.recipient_id='bot:cmo' "
                     "AND json_extract(m.refs,'$.worktree_wait') IS NOT NULL AND json_extract(m.refs,'$.task')=?",
                (tid,))


def free(api, filler, link):
    """A finished worktree on the other task is removed by its computer: the event a waiting task needs."""
    return beat(api, [{'link_id': link, 'state': 'removed', 'branch': None}])


def test_only_the_limit_for_the_turns_own_task_records_a_wait(prepared):
    api, tid, _ = prepared
    filler, _ = setup_wait(api, tid)
    route = f'tasks/{tid}/worktrees'
    key = {**auth('bot-test'), 'Idempotency-Key': 'same-key'}
    first = api.post('/api/v2/' + route, json={'repo': 'Acme/product'}, headers=key)
    error = first.json()['error']
    assert first.status_code == 409 and error['code'] == 'worktree_limit'
    assert 'End your turn now' in error['detail'] and 'wake this task' in error['detail']
    assert error['wait']['repo'] == 'Acme/product' and error['limit'] == W.LIMIT
    # The same request again, replayed or new, is the same wait.
    assert api.post('/api/v2/' + route, json={'repo': 'Acme/product'}, headers=key).json() == first.json()
    assert post(api, route, {'repo': 'acme/PRODUCT'}, 'bot-test').json()['error']['wait']['id'] == error['wait']['id']
    assert len(rows(api, 'SELECT * FROM worktree_waits')) == 1
    # Another task of the same bot, a person's request, an attach and another refusal never make one.
    with api.app_state.store.transaction() as c:
        third = H.task_create(c, 'human:ana', 'Third', 'More', 'bot:cmo')['id']
        c.execute("UPDATE tasks SET status='doing' WHERE status='open'")  # a started task gets a worktree
    assert 'wait' not in post(api, f'tasks/{third}/worktrees', {'repo': 'Acme/product'}, 'bot-test').json()['error']
    person = post(api, f'tasks/{third}/worktrees', {'repo': 'Acme/product'})
    assert person.status_code == 409 and 'wait' not in person.json()['error']
    attach = post(api, f'tasks/{tid}/worktrees/attach', {'path': f'tasks/{tid[:8]}/x', 'repo': 'Acme/product'}, 'bot-test')
    assert attach.status_code == 409 and 'wait' not in attach.json()['error']
    put(api, 'bots/cmo/repositories', {'mode': 'all', 'all_access': 'read'})
    with api.app_state.store.transaction() as c:
        fourth = H.task_create(c, 'human:ana', 'Fourth', 'More', 'bot:cmo')['id']
        c.execute("UPDATE tasks SET status='doing' WHERE status='open'")  # a started task gets a worktree
        turn_on(c, fourth)
    assert post(api, f'tasks/{fourth}/worktrees', {'repo': 'Acme/product'}, 'bot-test').status_code == 403
    assert len(rows(api, 'SELECT * FROM worktree_waits')) == 1


def test_a_freed_place_is_reserved_prepared_and_wakes_the_task_once_after_setup(prepared):
    api, tid, _ = prepared
    filler, links = setup_wait(api, tid)
    wait = post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}, 'bot-test').json()['error']['wait']
    jobs_before = rows(api, "SELECT id FROM jobs WHERE bot_id='cmo'")
    # Still full: heartbeats and sweeps change nothing and say nothing.
    for _ in range(3):
        assert beat(api) == []
        waits.tick(api.app_state.store, 'Acme', force=True)
    assert rows(api, 'SELECT state FROM worktree_waits')[0]['state'] == 'waiting' and not wakes(api, tid)
    free(api, filler, links[0])
    reserved = rows(api, 'SELECT * FROM worktree_waits')[0]
    assert reserved['state'] == 'reserved' and reserved['link_id']
    link = rows(api, 'SELECT * FROM task_links WHERE id=?', (reserved['link_id'],))[0]
    assert link['task_id'] == tid and link['state'] == 'pending' and json.loads(link['detail'])['wait_id'] == wait['id']
    # The computer is asked to prepare it; repeated beats and sweeps keep one reservation and wake nobody.
    for _ in range(3):
        actions = beat(api)
        assert [a['action'] for a in actions] == ['prepare'] and actions[0]['link_id'] == link['id']
        waits.tick(api.app_state.store, 'Acme', force=True)
    assert len(rows(api, "SELECT id FROM task_links WHERE task_id=? AND kind='worktree'", (tid,))) == 1
    assert not wakes(api, tid)
    # A report of a present tree without proof, or with setup still pending, is not ready.
    beat(api, [{'link_id': link['id'], 'state': 'present', 'branch': link['branch'], 'checkout_state': 'checkout_ready'}])
    assert not wakes(api, tid)
    assert finish_setup(api, tid, link['id']).status_code == 200
    woke = wakes(api, tid)
    assert len(woke) == 1 and link['path'] in woke[0]['body'] and 'ready' in woke[0]['body']
    assert rows(api, 'SELECT state FROM worktree_waits')[0]['state'] == 'woken'
    # The wake is a new message to the task, not the turn's message again; nothing is said twice.
    jobs = rows(api, "SELECT j.message_id FROM jobs j WHERE j.bot_id='cmo'")
    assert len(jobs) == len(jobs_before) + 1 and jobs[-1]['message_id'] == woke[0]['id']
    for _ in range(3):
        beat(api)
        waits.tick(api.app_state.store, 'Acme', force=True)
    assert len(wakes(api, tid)) == 1 and beat(api) == []
    # The bot's own add now finds the ready worktree.
    again = post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}, 'bot-test').json()
    assert again['link_id'] == link['id'] and again['state'] == 'present'


def test_waiters_are_served_oldest_first_without_double_booking(prepared):
    api, tid, _ = prepared
    filler, links = setup_wait(api, tid)
    with api.app_state.store.transaction() as c:
        younger = H.task_create(c, 'human:ana', 'Younger', 'Later work', 'bot:cmo')['id']
        c.execute("UPDATE tasks SET status='doing' WHERE status='open'")  # a started task gets a worktree
    post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}, 'bot-test')
    with api.app_state.store.transaction() as c:
        turn_on(c, younger)
    post(api, f'tasks/{younger}/worktrees', {'repo': 'Acme/product'}, 'bot-test')
    order = {r['task_id']: r for r in rows(api, 'SELECT * FROM worktree_waits')}
    assert set(order) == {tid, younger}
    free(api, filler, links[0])
    for _ in range(3):
        waits.tick(api.app_state.store, 'Acme', force=True)
    state = {r['task_id']: r['state'] for r in rows(api, 'SELECT * FROM worktree_waits')}
    assert state == {tid: 'reserved', younger: 'waiting'}
    live = rows(api, "SELECT count(*) n FROM task_links WHERE kind='worktree' AND state<>'removed'")[0]['n']
    assert live == W.LIMIT
    free(api, filler, links[1])
    assert {r['task_id']: r['state'] for r in rows(api, 'SELECT * FROM worktree_waits')} == {tid: 'reserved', younger: 'reserved'}


def test_one_broken_wait_ends_alone_and_the_heartbeat_still_frees_places(prepared, monkeypatch):
    api, tid, _ = prepared
    filler, links = setup_wait(api, tid)
    with api.app_state.store.transaction() as c:
        younger = H.task_create(c, 'human:ana', 'Younger', 'Later work', 'bot:cmo')['id']
        c.execute("UPDATE tasks SET status='doing' WHERE status='open'")  # a started task gets a worktree
    post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}, 'bot-test')
    with api.app_state.store.transaction() as c:
        turn_on(c, younger)
    post(api, f'tasks/{younger}/worktrees', {'repo': 'Acme/product'}, 'bot-test')
    # At the limit, serving asks on every heartbeat without listing the bot's worktrees.
    real_holdings = W.holdings
    monkeypatch.setattr(W, 'holdings', lambda *a: (_ for _ in ()).throw(AssertionError('holdings on a heartbeat')))
    for _ in range(3):
        assert beat(api) == []
    assert {r['state'] for r in rows(api, 'SELECT * FROM worktree_waits')} == {'waiting'}
    monkeypatch.setattr(W, 'holdings', real_holdings)
    real = W.provision

    def provision(c, who, task, *args, **kwargs):
        if task['id'] == tid:
            H.event(c, H.KEEPER, 'half.done', tid, {})
            raise RuntimeError('synthetic fault')
        return real(c, who, task, *args, **kwargs)
    monkeypatch.setattr(W, 'provision', provision)
    # The heartbeat that frees two places succeeds: both removals land, the broken wait ends, the other is served.
    beat(api, [{'link_id': link, 'state': 'removed', 'branch': None} for link in links[:2]])
    assert {r['state'] for r in rows(api, 'SELECT state FROM task_links WHERE id IN (?,?)', tuple(links[:2]))} == {'removed'}
    state = {r['task_id']: r for r in rows(api, 'SELECT * FROM worktree_waits')}
    assert state[tid]['state'] == 'cancelled' and 'RuntimeError' in state[tid]['reason']
    assert state[younger]['state'] == 'reserved'
    assert not rows(api, "SELECT * FROM events WHERE action='half.done'")
    told = wakes(api, tid)
    assert len(told) == 1 and json.loads(told[0]['refs'])['quiet'] and 'worktree add Acme/product' in told[0]['body']
    for _ in range(3):
        beat(api)
        waits.tick(api.app_state.store, 'Acme', force=True)
    assert len(wakes(api, tid)) == 1


def test_a_restarted_server_and_computer_still_wake_once(prepared, tmp_path):
    api, tid, _ = prepared
    filler, links = setup_wait(api, tid)
    post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}, 'bot-test')
    # The place frees while no event reaches the server (the computer's report is lost): the sweep finds it.
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE task_links SET state='removed' WHERE id=?", (links[0],))
    # A restarted server reads the wait from the database: nothing about it lives in memory.
    from backend.app import create_app
    from backend.config import Settings
    from fastapi.testclient import TestClient
    settings = api.app_state.store.settings
    again = create_app(Settings(db_path=settings.db_path, public_url=settings.public_url, company_name='Acme',
                                github_owner='Acme', test_identities=settings.test_identities))
    with TestClient(again, follow_redirects=False) as restarted:
        restarted.app_state = again.state
        assert rows(restarted, 'SELECT state FROM worktree_waits')[0]['state'] == 'waiting'
        waits.tick(again.state.store, 'Acme', force=True)
        link = rows(restarted, 'SELECT link_id FROM worktree_waits')[0]['link_id']
        # A restarted computer that lost its place mid-checkout is asked again and resumes it.
        assert runner_patch(restarted, tid, link, state='pending', checkout_state='initializing', setup_pending=True,
                            expected_head='a' * 40, checkout_target='refs/remotes/origin/main',
                            expected_base='b' * 64).status_code == 200
        assert [a['action'] for a in beat(restarted)] == ['prepare']
        assert finish_setup(restarted, tid, link).status_code == 200
        waits.tick(again.state.store, 'Acme', force=True)
        assert len(wakes(restarted, tid)) == 1


def test_a_failed_preparation_wakes_once_with_the_reason(prepared):
    api, tid, _ = prepared
    filler, links = setup_wait(api, tid)
    post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}, 'bot-test')
    free(api, filler, links[0])
    link = rows(api, 'SELECT link_id FROM worktree_waits')[0]['link_id']
    report = {'link_id': link, 'state': 'pending', 'branch': None,
              'error': 'Worktree action failed; history kept. Worktree created but setup failed (exit 7)'}
    for _ in range(4):
        assert beat(api, [report]) == []
        waits.tick(api.app_state.store, 'Acme', force=True)
    woke = wakes(api, tid)
    assert len(woke) == 1 and 'setup failed (exit 7)' in woke[0]['body'] and 'hub task worktree' in woke[0]['body']
    assert rows(api, 'SELECT state FROM worktree_waits')[0]['state'] == 'failed'


def test_close_reassign_revoke_and_stop_end_the_wait_without_touching_other_tasks(prepared):
    api, tid, _ = prepared
    filler, links = setup_wait(api, tid)
    tasks = [tid]
    with api.app_state.store.transaction() as c:
        for title in ('Second', 'Third', 'Fourth'):
            tasks.append(H.task_create(c, 'human:ana', title, 'Work', 'bot:cmo')['id'])
            c.execute("UPDATE tasks SET status='doing' WHERE status='open'")  # a started task gets a worktree
    for task in tasks:
        with api.app_state.store.transaction() as c:
            turn_on(c, task)
        assert 'wait' in post(api, f'tasks/{task}/worktrees', {'repo': 'Acme/product'}, 'bot-test').json()['error']
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE tasks SET status='closed' WHERE id=?", (tasks[0],))
        c.execute("UPDATE tasks SET owner_id='bot:cpo' WHERE id=?", (tasks[1],))
    waits.tick(api.app_state.store, 'Acme', force=True)
    state = {r['task_id']: r['state'] for r in rows(api, 'SELECT * FROM worktree_waits')}
    assert state == {tasks[0]: 'cancelled', tasks[1]: 'cancelled', tasks[2]: 'waiting', tasks[3]: 'waiting'}
    # Losing write access to the repository ends the wait when a place frees; the task hears it quietly.
    put(api, 'bots/cmo/repositories', {'mode': 'all', 'all_access': 'read'})
    free(api, filler, links[0])
    state = {r['task_id']: r['state'] for r in rows(api, 'SELECT * FROM worktree_waits')}
    assert state[tasks[2]] == 'cancelled' and state[tasks[3]] == 'cancelled'
    assert all(json.loads(m['refs']).get('quiet') for m in wakes(api, tasks[2]))
    assert not wakes(api, tasks[0]) and not wakes(api, tasks[1])
    # A person stopping the turn that asked ends its waits; another turn's wait stands.
    put(api, 'bots/cmo/repositories', {'mode': 'all', 'all_access': 'write'})
    with api.app_state.store.transaction() as c:
        fifth = H.task_create(c, 'human:ana', 'Fifth', 'Work', 'bot:cmo')['id']
        c.execute("UPDATE tasks SET status='doing' WHERE status='open'")  # a started task gets a worktree
        turn_on(c, fifth)
        c.execute("INSERT INTO worktree_waits VALUES('other',?,'bot:cmo','Acme/docs',1,'a9','waiting',NULL,'',?,?,NULL)",
                  (tasks[3], H.now(), H.now()))
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE task_links SET state='present' WHERE id=?", (links[0],))
    assert 'wait' in post(api, f'tasks/{fifth}/worktrees', {'repo': 'Acme/product'}, 'bot-test').json()['error']
    stop = api.post('/api/v2/bots/cmo/stop', json={'run_id': 'a1'}, headers={**auth(), 'Idempotency-Key': uuid.uuid4().hex})
    assert stop.status_code == 200, stop.text
    done = api.post('/api/v2/attempts/a1/complete', json={'outcome': 'interrupted', 'last_seq': 0},
                    headers={**auth('runner-test'), 'Idempotency-Key': uuid.uuid4().hex})
    assert done.status_code == 200, done.text
    state = {r['task_id']: r['state'] for r in rows(api, 'SELECT * FROM worktree_waits WHERE task_id IN (?,?)',
                                                     (fifth, tasks[3]))}
    assert state == {fifth: 'cancelled', tasks[3]: 'waiting'}


def test_old_computers_keep_the_old_answer_and_health_and_the_task_show_the_wait(prepared):
    api, tid, _ = prepared
    old = {'schema_version': 1, 'bots': {}, 'worktrees': True}
    setup_wait(api, tid, old)
    refused = post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}, 'bot-test').json()['error']
    assert 'wait' not in refused and 'Oldest idle: ' in refused['detail'] and 'End your turn' not in refused['detail']
    assert not rows(api, 'SELECT * FROM worktree_waits')
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE runners SET readiness=? WHERE id='r1'", (json.dumps(READY),))
    wait = post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}, 'bot-test').json()['error']['wait']
    shown = api.get(f'/api/v2/tasks/{tid}', headers=auth()).json()['task']['worktree_waits']
    assert shown == [{'id': wait['id'], 'repo': 'Acme/product', 'state': 'waiting', 'since': wait['since'],
                      'reason': f'This bot has {W.LIMIT} worktrees'}]
    check = next(c for c in api.get('/api/v2/health', headers=auth()).json()['checks'] if c['id'] == 'worktree_waits')
    assert check['status'] == 'info' and 'cmo: Acme/product, no room for 0 min' in check['summary']
    # The computer goes back to an older version while the task waits: the task is told once to ask again.
    beat(api, readiness=old)
    waits.tick(api.app_state.store, 'Acme', force=True)
    waits.tick(api.app_state.store, 'Acme', force=True)
    assert len(wakes(api, tid)) == 1 and rows(api, 'SELECT state FROM worktree_waits')[0]['state'] == 'cancelled'


def test_the_cli_exits_with_its_own_status_for_a_wait(monkeypatch, capsys):
    from types import SimpleNamespace
    from clients import remotecli
    from clients.tico import APIError
    args = SimpleNamespace(cmd='task', fn='task worktree add')
    monkeypatch.setattr(remotecli, 'run', lambda *_: (_ for _ in ()).throw(
        APIError('worktree_limit', 'No room. End your turn now.', 409, extra={'wait': {'id': 'w'}})))
    assert remotecli.main(args) == remotecli.WORKTREE_WAIT_EXIT == 3
    assert capsys.readouterr().out.strip() == 'No room. End your turn now.'
    monkeypatch.setattr(remotecli, 'run', lambda *_: (_ for _ in ()).throw(
        APIError('worktree_limit', 'Clean one up first.', 409)))
    assert remotecli.main(args) == 2



def test_a_wait_whose_task_got_blocked_is_cancelled_not_served(prepared):
    api, tid, _ = prepared
    filler, links = setup_wait(api, tid)
    assert 'wait' in post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}, 'bot-test').json()['error']
    with api.app_state.store.transaction() as c:
        blocker = H.task_create(c, 'human:ana', 'Blocker', 'First this', 'bot:cpo')['id']
        c.execute("UPDATE tasks SET status='doing' WHERE id=?", (blocker,))
        c.execute("INSERT INTO task_relations(from_task,to_task,kind,created_at) VALUES(?,?,'blocks',?)",
                  (blocker, tid, H.now()))
    free(api, filler, links[0])
    wait = rows(api, 'SELECT * FROM worktree_waits WHERE task_id=?', (tid,))[0]
    assert wait['state'] == 'cancelled' and 'blocked by' in wait['reason'] and wait['link_id'] is None
    assert not wakes(api, tid)


def test_two_freed_places_and_repeated_passes_give_one_reservation_and_one_wake(prepared):
    api, tid, _ = prepared
    filler, links = setup_wait(api, tid)
    assert 'wait' in post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}, 'bot-test').json()['error']
    free(api, filler, links[0])
    free(api, filler, links[1])
    waits.tick(api.app_state.store, 'Acme', force=True)
    reserved = rows(api, "SELECT * FROM task_links WHERE task_id=? AND kind='worktree'", (tid,))
    assert len(reserved) == 1
    assert finish_setup(api, tid, reserved[0]['id']).status_code == 200
    for _ in range(2):
        beat(api)
        waits.tick(api.app_state.store, 'Acme', force=True)
    assert len(wakes(api, tid)) == 1


def test_a_reserved_worktree_whose_task_closes_is_cancelled_once_and_never_prepared(prepared):
    api, tid, _ = prepared
    filler, links = setup_wait(api, tid)
    assert 'wait' in post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}, 'bot-test').json()['error']
    free(api, filler, links[0])
    link = rows(api, "SELECT * FROM task_links WHERE task_id=? AND kind='worktree'", (tid,))[0]
    assert link['state'] == 'pending'
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE tasks SET status='closed' WHERE id=?", (tid,))
    actions = beat(api) + beat(api)
    assert not [a for a in actions if a['link_id'] == link['id']]
    assert rows(api, 'SELECT state FROM task_links WHERE id=?', (link['id'],))[0]['state'] == 'removed'
    assert len(rows(api, "SELECT * FROM events WHERE action='worktree.released' AND target=?", (tid,))) == 1
    assert not wakes(api, tid)


def test_served_wait_reuses_removed_link_without_old_cleanup_intent(prepared):
    api, tid, _ = prepared
    filler, links = setup_wait(api, tid)
    old = H.shift(H.now(), days=-4)
    retired = uuid.uuid4().hex
    stale = dict(owner='bot:cmo', release_requested=True, cleanup_requested=True,
                 release_reason='pull request merged', removed_by='cleanup', restore_on_reopen=True,
                 held='held: live turn',
                 cleanup_attempts=W.CLEANUP_TRIES, cleanup_gave_up=True, cleanup_retry_at=H.now())
    with api.app_state.store.transaction() as c:
        c.execute("INSERT INTO task_links(id,task_id,kind,url,title,state,added_by,created_at,repo,branch,computer_id,"
                  "path,detail,updated_at) VALUES(?,?,'worktree',?,'w','removed','bot:cmo',?,'Acme/product',"
                  "'tico/retired','r1',?,?,?)",
                  (retired, tid, 'worktree:' + retired, old, f'tasks/{tid[:8]}/Acme__product', json.dumps(stale), old))
        pr = H.task_link(c, 'bot:cmo', tid, 'https://github.com/Acme/product/pull/7')
        c.execute("UPDATE task_links SET state='merged',created_at=?,branch='tico/retired',detail=? WHERE id=?",
                  (old, json.dumps({'head_sha': 'a' * 40}), pr['id']))
    assert post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}, 'bot-test').status_code == 409
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE attempts SET state='completed' WHERE id='a1'")  # the asking turn ended
    free(api, filler, links[0])
    served = rows(api, 'SELECT * FROM worktree_waits')[0]
    assert served['state'] == 'reserved' and served['link_id'] == retired
    detail = json.loads(rows(api, 'SELECT detail FROM task_links WHERE id=?', (retired,))[0]['detail'])
    assert not any(key in detail for key in stale if key not in ('owner', 'held'))
    assert detail.get('held') != stale['held']  # a current reservation hold is separate from the retired one
    assert finish_setup(api, tid, retired).status_code == 200
    assert rows(api, 'SELECT state FROM worktree_waits')[0]['state'] == 'woken'
    assert not any(a['link_id'] == retired and a['action'] == 'remove' for a in beat(api))
    row = rows(api, 'SELECT state,detail FROM task_links WHERE id=?', (retired,))[0]
    assert row['state'] == 'present'
    assert not any(json.loads(row['detail']).get(k) for k in W.REMOVAL)
    # A PR opened for the new allocation can still release it when that work finishes.
    with api.app_state.store.transaction() as c:
        pr = H.task_link(c, 'bot:cmo', tid, 'https://github.com/Acme/product/pull/8')
        c.execute("UPDATE task_links SET state='merged',branch='tico/retired' WHERE id=?", (pr['id'],))
    assert [(a['link_id'], a['action']) for a in beat(api)] == [(retired, 'remove')]
