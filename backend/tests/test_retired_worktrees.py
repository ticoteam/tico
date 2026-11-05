"""Retired computers hold history, never a ready checkout or a place at the worktree limit."""
import json
import uuid

import pytest

from backend import hubdb as H, worktrees as W, worktree_waits as waits
from backend.tests.test_github_app import api, gh, auth  # noqa: F401
from backend.tests.test_worktrees import prepared, post  # noqa: F401
from backend.tests.test_repositories import put


def leave_on_old_computer(api, tid, *, revoked=True):
    link = post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}, 'bot-test').json()
    with api.app_state.store.transaction() as c:
        if revoked:
            # Copy the registered computer, keeping the current bot and its test tokens on r1.
            cols = [r[1] for r in c.execute('PRAGMA table_info(runners)')]
            old = dict(c.execute("SELECT * FROM runners WHERE id='r1'").fetchone())
            old.update(id='old', token_hash='old-token', revoked_at=H.now())
            c.execute(f"INSERT INTO runners({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                      tuple(old[k] for k in cols))
        detail = {'owner': 'bot:cmo', 'checkout_state': 'ready', 'setup_pending': False,
                  'expected_head': 'a' * 40, 'checkout_target': 'refs/heads/' + link['branch'],
                  'expected_base': 'b' * 64, 'last_commit': 'c' * 40, 'pushed': 'a' * 40,
                  'ahead': 2, 'dirty_files': 1, 'cleanup_gave_up': True, 'cleanup_requested': True}
        c.execute("UPDATE task_links SET computer_id='old',state='present',detail=? WHERE id=?",
                  (json.dumps(detail), link['link_id']))
        c.execute("UPDATE runner_states SET readiness=? WHERE runner_id='r1'",
                  (json.dumps({'schema_version': 1, 'worktrees': True, 'worktree_waits': True, 'worktree_recovery': True}),))
    return link


@pytest.mark.parametrize('revoked', [True, False], ids=['revoked', 'deleted'])
def test_retired_status_health_recovery_and_old_reports(prepared, revoked):
    from backend import maintenance_health
    api, tid, _ = prepared
    old = leave_on_old_computer(api, tid, revoked=revoked)
    view = api.get(f'/api/v2/tasks/{tid}', headers=auth('bot-test'))
    assert view.status_code == 200, view.text
    link = next(l for l in view.json()['task']['links'] if l['id'] == old['link_id'])
    assert link['state'] == 'retired'
    detail = json.loads(link['detail']) if isinstance(link['detail'], str) else link['detail']
    assert detail['checkout_state'] == 'retired' and detail['ahead'] == 2
    assert detail['last_commit'] == 'c' * 40 and detail['pushed'] == 'a' * 40
    assert 'retired computer' in detail['error']
    with api.app_state.store.read() as c:
        assert W.inventory(c, 'old') == []
        assert W.holdings(c, 'bot:cmo') == []
        notes = W.recovery_prompt(c, H.task(c, tid), 'cmo')
        assert f'hub task worktree add Acme/product --task {tid}' in notes[0]
        assert W.recovery_prompt(c, H.task(c, tid), 'cpo') == []
    # Health uses the same retirement projection, including the last report's facts.
    maintenance_health.check(api.app_state.store, api.app_state.auth, api.app_state.store.settings)
    with api.app_state.store.read() as c:
        findings = maintenance_health.findings(c, api.app_state.store.settings)
        assert any('retired computer' in f['detail'] and '2 unpushed' in f['detail'] for f in findings)
    response = post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}, 'bot-test')
    assert response.status_code == 200, response.text
    moved = response.json()
    assert moved['link_id'] == old['link_id'] and moved['branch'] == old['branch']
    assert moved['checkout_state'] == 'queued' and moved['expected_head'] is None
    assert moved['recovery']['published_head'] == 'a' * 40
    assert moved['retired_worktree']['ahead'] == 2 and moved['retired_worktree']['last_commit'] == 'c' * 40
    with api.app_state.store.transaction() as c:
        row = dict(c.execute('SELECT * FROM task_links WHERE id=?', (old['link_id'],)).fetchone())
        detail = json.loads(row['detail'])
        assert row['computer_id'] == 'r1' and row['state'] == 'pending'
        assert not any(detail.get(k) for k in (*W.IDENTITY, *W.REMOVAL, 'cleanup_gave_up'))
        assert W.preparable(c, row, detail)
        from backend.auth import Identity
        W.heartbeat(c, Identity('runner:old', 'runner', runner_id='old'),
                    [], True)
        assert c.execute('SELECT computer_id FROM task_links WHERE id=?', (old['link_id'],)).fetchone()[0] == 'r1'


def test_live_other_computer_and_unauthorized_bot_still_refused(prepared):
    api, tid, other = prepared
    old = leave_on_old_computer(api, tid)
    assert post(api, f'tasks/{other}/worktrees', {'repo': 'Acme/product'}, 'bot-test').status_code == 403
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE runners SET revoked_at=NULL WHERE id='old'")
        assert W.status(c, c.execute('SELECT * FROM task_links WHERE id=?', (old['link_id'],)).fetchone())['state'] == 'present'
    refused = post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}, 'bot-test')
    assert refused.status_code == 403 and 'live computer' in refused.text
    # The current computer cannot confirm or redirect a live computer's link either.
    response = api.patch(f'/api/v2/tasks/{tid}/links/{old["link_id"]}',
                         json={'recovery_branch_missing': True},
                         headers={**auth('bot-test'), 'Idempotency-Key': uuid.uuid4().hex})
    assert response.status_code == 403


def test_retired_links_do_not_fill_the_limit_and_cleanup_needs_no_dead_computer(prepared, monkeypatch):
    api, tid, _ = prepared
    old = leave_on_old_computer(api, tid)
    monkeypatch.setattr(W, 'LIMIT', 1)
    put(api, 'repositories/Acme/docs', {'enabled': True})
    response = post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/docs'}, 'bot-test')
    assert response.status_code == 200, response.text
    with api.app_state.store.transaction() as c:
        H.task_unlink(c, 'bot:cmo', tid, old['link_id'])
        assert not c.execute('SELECT 1 FROM task_links WHERE id=?', (old['link_id'],)).fetchone()


def test_missing_origin_branch_changes_only_a_queued_recovery(prepared):
    api, tid, _ = prepared
    old = leave_on_old_computer(api, tid)
    moved = post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}, 'bot-test').json()
    route = f'/api/v2/tasks/{tid}/links/{old["link_id"]}'
    def patch(fields):
        return api.patch(route, json=fields, headers={**auth('bot-test'), 'Idempotency-Key': uuid.uuid4().hex})
    fallback = patch({'recovery_branch_missing': True})
    assert fallback.status_code == 200, fallback.text
    assert fallback.json()['branch'] == moved['recovery']['fallback_branch'] != old['branch']
    detail = json.loads(fallback.json()['detail_json'])
    assert 'not published' in detail['recovery_note'] and detail['retired_worktree']['ahead'] == 2
    proof = {'state': 'pending', 'checkout_state': 'initializing', 'expected_head': 'd' * 40,
             'checkout_target': 'refs/remotes/origin/main', 'expected_base': 'e' * 64}
    assert patch(proof).status_code == 200
    assert patch({'recovery_branch_missing': True}).status_code == 409


def test_an_old_runner_cannot_silently_recover_from_main(prepared):
    api, tid, _ = prepared
    leave_on_old_computer(api, tid)
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE runner_states SET readiness=? WHERE runner_id='r1'", (json.dumps({'worktrees': True}),))
    response = post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}, 'bot-test')
    assert response.status_code == 409 and response.json()['error']['code'] == 'computer_update'


def test_a_reserved_wait_on_a_retired_computer_is_prepared_again_instead_of_woken(prepared):
    api, tid, _ = prepared
    old = leave_on_old_computer(api, tid)
    with api.app_state.store.transaction() as c:
        c.execute("INSERT INTO worktree_waits(id,task_id,owner,repo,generation,attempt_id,state,link_id,created_at,updated_at) "
                  "VALUES('wait',?,'bot:cmo','Acme/product',1,'a1','reserved',?,?,?)",
                  (tid, old['link_id'], H.now(), H.now()))
        assert waits.serve(c, ['bot:cmo'], 'Acme') == []
        wait = dict(c.execute("SELECT * FROM worktree_waits WHERE id='wait'").fetchone())
        row = dict(c.execute('SELECT * FROM task_links WHERE id=?', (old['link_id'],)).fetchone())
        assert wait['state'] == 'reserved' and wait['link_id'] == old['link_id']
        assert row['computer_id'] == 'r1' and row['state'] == 'pending'
        assert json.loads(row['detail'])['wait_id'] == 'wait'
