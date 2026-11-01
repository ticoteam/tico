"""Software review handoffs refuse stale evidence before a reviewer can be queued."""
import json
from types import SimpleNamespace

import pytest

from backend import hubdb as H, review_gate, qa_defaults, test_receipts
from backend.tests.test_github import api, get, headers, post, hook, pr_event, PR
from backend.tests.test_api import hold

HEAD, OLD, BASE = 'a' * 40, 'b' * 40, 'c' * 40


def ticket(api, receipt=None, **fields):
    with api.app.state.store.transaction() as c:
        typ = H.type_create(c, H.KEEPER, 'Dev tickets', [
            {'name': 'In Progress', 'status': 'doing'}, {'name': 'Review 1', 'status': 'review'},
            {'name': 'Review 1 Fixes', 'status': 'doing'}, {'name': 'Review 2', 'status': 'review'},
            {'name': 'Done', 'status': 'done'}])
    task = post(api, 'tasks', {'owner_id': 'cpo', 'title': 'Improve messages', 'body': 'Build messages.',
                              'type': typ['id'], 'step': 'In Progress', 'links': [PR], **fields})
    hook(api, 'pull_request', pr_event('opened', head={'sha': HEAD}))
    if receipt:
        with api.app.state.store.transaction() as c:
            c.execute('UPDATE tasks SET note=? WHERE id=?', (receipt, task['id']))
    return get(api, 'tasks/' + task['id'])['task']


def move(api, task, step='Review 1'):
    return api.post('/api/v2/tasks/' + task['id'], headers=headers(),
                    json={'version': get(api, 'tasks/' + task['id'])['task']['version'], 'step': step})


def test_qa_pending_marker_is_scoped_to_each_links_task(api):
    task = ticket(api)
    plain = post(api, 'tasks', {'owner_id': 'cpo', 'title': 'Planning notes', 'body': 'x', 'links': [PR]})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE task_links SET detail=? WHERE task_id=? AND kind='pr'",
                  (json.dumps({'qa_default_pending': True, 'kept': 'metadata'}), plain['id']))
    hook(api, 'pull_request', pr_event('opened', head={'sha': HEAD}))
    hook(api, 'pull_request', pr_event('synchronize', head={'sha': OLD}))
    with api.app.state.store.read() as c:
        engineering = H.task_links(c, task['id'])[0]
        general = H.task_links(c, plain['id'])[0]
        assert json.loads(engineering['detail'])['qa_default_pending'] is True
        detail = json.loads(general['detail'])
        assert not detail.get('qa_default_pending')
        assert detail['kept'] == 'metadata'


def notices(api, task):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute("SELECT * FROM messages WHERE conversation_id=? AND json_extract(refs,'$.review_gate') IS NOT NULL", (task['conversation_id'],))]


@pytest.mark.parametrize('step', ['Review 1', 'Review 2'])
@pytest.mark.parametrize('receipt,allowed', [(None, False), (f'Tests: head {OLD}: 9 passed, 0 failed', False),
    (f'Tests: base {BASE} head {HEAD}: 9 passed, 0 failed', True)])
def test_receipt_at_current_head_allows_review_child_and_other_receipts_do_not_wake(api, receipt, allowed, step):
    task = ticket(api, receipt)
    response = move(api, task, step)
    assert response.status_code == (200 if allowed else 422), response.text
    child = api.post('/api/v2/tasks', headers=headers(), json={'owner': 'cmo', 'title': step + ': PR #412',
        'body': f'Check PR #412 base {BASE} head {HEAD}', 'relations': [{'task': task['id'], 'kind': 'parent'}]})
    assert child.status_code == (200 if allowed else 422), child.text
    with api.app.state.store.read() as c:
        assert bool(c.execute("SELECT 1 FROM jobs WHERE bot_id='cmo'").fetchone()) is allowed
    if not allowed:
        current = get(api, 'tasks/' + task['id'])['task']
        assert current['step']['name'] == 'In Progress' and current['version'] > task['version']
        assert HEAD in response.text and (OLD if receipt else 'no receipt') in response.text
        assert len(notices(api, task)) == 1  # child request and step retries share the fixed notice


@pytest.mark.parametrize('checks,allowed,names', [({'python': 'failing'}, False, ['python']),
    ({'python': 'passing'}, True, []), ({'python': 'pending'}, True, []),
    ({'python': 'failing', 'browser': 'pending'}, False, ['python'])])
def test_ci_is_first_names_failures_and_allows_pending(api, checks, allowed, names):
    task = ticket(api, f'Tests: head {HEAD}: 9 passed, 0 failed' if allowed else None)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE task_links SET detail=json_set(detail,'$.checks',json(?)) WHERE task_id=? AND kind='pr'", (json.dumps(checks), task['id']))
    r = move(api, task)
    assert r.status_code == (200 if allowed else 422), r.text
    if not allowed:
        assert 'CI failing' in r.text and 'test receipt' not in r.text
        assert all(n in r.text for n in names)
        assert 'browser' not in r.text
        assert move(api, task).status_code == 422
        assert len(notices(api, task)) == 1


def test_owner_refusal_does_not_post_a_comment(api):
    task = ticket(api)
    with api.app.state.store.transaction() as c:
        try:
            review_gate.check(c, task['owner_id'], H.task(c, task['id']))
        except H.Refused as e:
            review_gate.notify(c, task['owner_id'], e.review_gate)
        else:
            pytest.fail('missing receipt accepted')
    assert notices(api, task) == []


@pytest.mark.parametrize('text,state', [('356 pass / 0 fail', 'passed'), ('40 passed, 2 failing/errored', 'failed'),
    ('9 passed, 0 failed', 'passed'), ('results attached', 'unrecognised'), ('will pass', 'unrecognised'),
    ('0 passed, 5 skipped', 'unrecognised')])
def test_shared_recognizer_result_spellings(text, state):
    assert test_receipts.recognise(f'Tests: base {BASE} head {HEAD}: {text}')['status'] == state
    assert not test_receipts.at_head({'id': 't', 'note': f'Tests: base {HEAD} head {OLD}: 9 passed'}, HEAD)[0] == 'passed'


@pytest.mark.parametrize('historical', ['', ' Two original existing-fixture failures were corrected before final validation.'])
def test_mixed_note_keeps_current_result_separate_from_history_and_unrun_checks(api, historical):
    text = (f'Review 1: receipt gate implemented. Tests: base {BASE} head {HEAD}: '
            '185 passed, 0 failed, 0 skipped; exit 0, 78.10s.' + historical +
            ' Independent Review 1 and hosted checks pending. Full suite, browsers/native builds '
            'and installed behavior not run.')
    task = ticket(api, text)
    assert move(api, task).status_code == 200
    child = api.post('/api/v2/tasks', headers=headers(), json={'owner_id': 'cmo', 'title': 'Review 1: PR #412',
        'body': 'Review the change.', 'relations': [{'task': task['id'], 'kind': 'parent'}]})
    assert child.status_code == 200, child.text
    with api.app.state.store.transaction() as c:
        assert c.execute("SELECT 1 FROM jobs WHERE bot_id='cmo'").fetchone()
        c.execute('UPDATE tasks SET note=? WHERE id=?',
                  (text.replace('185 passed, 0 failed', '183 passed, 2 failing/errored'), task['id']))
    failed = move(api, get(api, 'tasks/' + task['id'])['task'], 'Review 2')
    assert failed.status_code == 422 and 'failed test receipt' in failed.text


@pytest.mark.parametrize('target', ['Review 1', 'Review 2', 'status', 'type-only'])
def test_destination_board_review_preflight_uses_fresh_github_and_refuses_atomically(api, monkeypatch, target):
    task = ticket(api, f'Tests: head {HEAD}: 9 passed, 0 failed')
    with api.app.state.store.transaction() as c:
        H.task_update(c, 'human:ana', task['id'], type=H.GENERAL_TYPE,
                      status='review' if target == 'type-only' else 'doing')
    task = get(api, 'tasks/' + task['id'])['task']
    service = api.app.state.github_app
    monkeypatch.setattr(service, 'row', lambda c: {'org': 'acme'})
    monkeypatch.setattr(service, 'mint', lambda *a: ('synthetic', 'later'))
    calls, fresh = [], {'head': OLD, 'conclusion': 'failure'}
    def fetch(method, path, **kw):
        calls.append(path)
        data = ({'head': {'sha': fresh['head']}} if '/pulls/' in path else
                {'check_runs': [{'name': 'python', 'conclusion': fresh['conclusion']}]} if path.endswith('check-runs') else [])
        return SimpleNamespace(status_code=200, json=lambda: data)
    monkeypatch.setattr(service, '_call', fetch)
    # Type names and ids, step names and ids, and implicit review steps use the same destination.
    body = {'version': task['version'], 'type': 'Dev tickets'}
    if target == 'status':
        body['status'] = 'review'
    elif target != 'type-only':
        with api.app.state.store.read() as c:
            typ = H.type_get(c, 'Dev tickets')
        body['type'] = typ['id']
        body['step'] = next(s['id'] for s in typ['steps'] if s['name'] == target) if target == 'Review 2' else target
    refused = api.post('/api/v2/tasks/' + task['id'], headers=headers(), json=body)
    assert refused.status_code == 422 and OLD in refused.text and 'python' in refused.text, refused.text
    current = get(api, 'tasks/' + task['id'])['task']
    assert current['type_id'] == task['type_id'] and current['status'] == 'waiting'
    assert hold(current)['since'] and 'CI failing' in hold(current)['reason']
    assert current['version'] > task['version']
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM jobs WHERE bot_id='cmo'").fetchone()
    assert any('/commits/' + OLD + '/check-runs' in p for p in calls)
    body['version'] = current['version']
    fresh.update(head=HEAD, conclusion='success')
    allowed = api.post('/api/v2/tasks/' + task['id'], headers=headers(), json=body)
    assert allowed.status_code == 200, allowed.text
    resumed = allowed.json()['task']
    assert resumed['step']['name'] in ('Review 1', 'Review 2')
    assert not hold(resumed).get('since') and not hold(resumed).get('reason')


def test_fresh_github_snapshot_head_and_checks_are_used_and_races_refuse(api, monkeypatch):
    task = ticket(api, f'Tests: head {HEAD}: 9 passed')
    service = api.app.state.github_app
    monkeypatch.setattr(service, 'row', lambda c: {'org': 'ticoteam'})
    monkeypatch.setattr(service, 'mint', lambda *a: ('synthetic', 'later'))
    calls, new_head = [], [OLD]
    def fetch(method, path, **kw):
        calls.append(path)
        data = {'head': {'sha': new_head[0]}} if '/pulls/' in path else {'check_runs': []} if path.endswith('check-runs') else []
        return SimpleNamespace(status_code=200, json=lambda: data)
    monkeypatch.setattr(service, '_call', fetch)
    r = move(api, task)
    assert r.status_code == 422 and OLD in r.text  # stored webhook head A does not bless fresh head B
    new_head[0] = HEAD
    assert move(api, task).status_code == 200
    assert any('/commits/' + HEAD + '/check-runs' in p for p in calls)


@pytest.mark.parametrize('paths,qa,note', [(['ui/page.js'], True, True), (['desktop/main.rs'], True, True),
    (['app/src/main.rs'], True, True), (['backend/chat.py'], True, False), (['backend/wake_rules.py'], True, False),
    (['docs/help.md'], False, False), (['.github/workflows/ci.yml'], False, False),
    (['ui/tests/tasks.cjs', 'backend/tests/test_tasks.py'], False, False), (['backend/store.py'], False, False),
    (['docs/help.md', 'ui/page.js'], True, True)])
def test_qa_path_table(paths, qa, note):
    result = qa_defaults.classify(paths)
    assert result['qa'] is qa and bool(result['note']) is note


def test_task_and_pr_creation_qa_default_and_author_override_are_once(api):
    task = ticket(api, change_paths=['docs/help.md'])
    from backend.github import apply_creation_qa
    pr = {'html_url': PR, 'labels': []}
    with api.app.state.store.transaction() as c:
        apply_creation_qa(c, pr, ['ui/page.js'])
        apply_creation_qa(c, pr, ['docs/help.md'])
        assert 'qa' in H.task_labels(H.task(c, task['id']))
    task2 = post(api, 'tasks', {'owner_id': 'cpo', 'title': 'Author override', 'body': 'x', 'type': task['type_id'],
        'qa': False, 'change_paths': ['ui/page.js'], 'links': ['https://github.com/ticoteam/tico/pull/413']})
    with api.app.state.store.transaction() as c:
        apply_creation_qa(c, {'html_url': 'https://github.com/ticoteam/tico/pull/413'}, ['ui/page.js'])
        assert 'qa' not in H.task_labels(H.task(c, task2['id']))
    task3 = post(api, 'tasks', {'owner_id': 'cpo', 'title': 'UI creation', 'body': 'x', 'type': task['type_id'],
        'change_paths': ['ui/page.js']})
    assert 'qa' in task3['labels'] and 'not checkable live' in task3['note']


def test_nonowner_notices_are_per_head_and_owner_refusals_are_silent(api):
    task = ticket(api)
    assert move(api, task).status_code == 422
    hook(api, 'pull_request', pr_event('synchronize', head={'sha': OLD}))
    assert move(api, task).status_code == 422
    assert len(notices(api, task)) == 2
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET owner_id='human:ana' WHERE id=?", (task['id'],))
    assert move(api, task).status_code == 422
    assert len(notices(api, task)) == 2


def test_review_child_without_a_pr_cannot_queue_the_reviewer(api):
    task = ticket(api)
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM task_links WHERE task_id=? AND kind='pr'", (task['id'],))
    r = api.post('/api/v2/tasks', headers=headers(), json={'owner': 'cmo', 'title': ' Review 1: PR #412',
        'body': 'Review the change.', 'relations': [{'task': task['id'], 'kind': 'parent'}]})
    assert r.status_code == 422 and 'no linked PR' in r.text
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM jobs WHERE bot_id='cmo'").fetchone()


@pytest.mark.parametrize('where', ['note', 'owner', 'keeper', 'child'])
def test_gate_consumes_each_agreed_receipt_source(api, where):
    task = ticket(api)
    text = f'Tests: base {BASE} head {HEAD}: 356 pass / 0 fail'
    with api.app.state.store.transaction() as c:
        if where == 'note':
            c.execute('UPDATE tasks SET note=? WHERE id=?', (text, task['id']))
        elif where == 'child':
            # Existing child from a previous review; its Builder-authored receipt belongs to this ticket.
            child = H.task_create(c, 'human:ana', 'Evidence', 'x', 'cmo', parent_id=task['id'])
            c.execute("UPDATE tasks SET title='Review 1: previous' WHERE id=?", (child['id'],))
            H.task_comment(c, task['owner_id'], child['id'], text, wake=False)
        else:
            conv = H.conversation(c, task['conversation_id'])
            actor = 'keeper' if where == 'keeper' else task['owner_id']
            H._write_message(c, actor, task['owner_id'], 'Ready for review. ' + text, conv, 'say',
                             {'task': task['id'], 'quiet': True}, None, None)
    assert move(api, task).status_code == 200


def test_fresh_checks_failure_pending_and_status_failure_history(api, monkeypatch):
    task = ticket(api, f'Tests: head {HEAD}: 9 passed')
    service = api.app.state.github_app
    monkeypatch.setattr(service, 'row', lambda c: {'org': 'ticoteam'})
    monkeypatch.setattr(service, 'mint', lambda *a: ('synthetic', 'later'))
    state, old_failure = ['failure'], [True]
    def fetch(method, path, **kw):
        if '/pulls/' in path:
            data = {'head': {'sha': HEAD}}
        elif path.endswith('check-runs'):
            data = {'check_runs': [{'id': 1, 'name': 'python', 'conclusion': state[0]},
                                  {'id': 2, 'name': 'browser', 'conclusion': None}]}
        else:
            data = [{'context': 'deploy', 'state': 'success'}]
            if old_failure[0]:
                data.append({'context': 'deploy', 'state': 'failure'})
        return SimpleNamespace(status_code=200, json=lambda: data)
    monkeypatch.setattr(service, '_call', fetch)
    r = move(api, task)
    assert r.status_code == 422 and 'python' in r.text and 'browser' not in r.text
    state[0] = None
    r = move(api, task)
    assert r.status_code == 422 and 'deploy (earlier failure)' in r.text
    old_failure[0] = False
    assert move(api, task).status_code == 200


def test_creation_rule_runs_from_github_files_and_preserves_author_choice(api, monkeypatch):
    from backend import github as G
    from backend.tests.test_github import _app, _reconciler
    _app(api)
    task = ticket(api)
    seen = []
    def fetch(method, path, **kw):
        seen.append(path)
        if path.endswith('/files'):
            data = [{'filename': 'ui/page.js'}]
        else:
            data = {**pr_event('refresh', head={'sha': HEAD})['pull_request'], 'number': 412, 'labels': []}
        return SimpleNamespace(status_code=200, json=lambda: data)
    _, run = _reconciler(api, monkeypatch, fetch)
    run()
    with api.app.state.store.read() as c:
        assert 'qa' in H.task_labels(H.task(c, task['id']))
    assert any(p.endswith('/files') for p in seen)
    seen.clear()
    run(fresh=True)
    assert not any(p.endswith('/files') for p in seen)  # never reclassify a later push


def test_removed_unrelated_and_wrong_actor_evidence_cannot_open_review(api):
    task = ticket(api)
    with api.app.state.store.transaction() as c:
        conv = H.conversation(c, task['conversation_id'])
        text = f'Tests: head {HEAD}: 9 passed, 0 failed'
        for actor, refs, removal in [(task['owner_id'], {'task': task['id']}, 'deleted_at'),
                (task['owner_id'], {'task': task['id']}, 'hidden_at'),
                ('bot:cmo', {'task': task['id']}, None), (task['owner_id'], {'task': 'unrelated'}, None)]:
            msg = H._write_message(c, actor, task['owner_id'], text, conv, 'say', {**refs, 'quiet': True}, None, None)
            if removal:
                c.execute(f'UPDATE messages SET {removal}=? WHERE id=?', (H.now(), msg['id']))
    assert move(api, task).status_code == 422


@pytest.mark.parametrize('reader', ['human:ana', 'bot:cpo'])
def test_private_child_receipt_must_be_readable_by_both_mover_and_builder(api, reader):
    task = ticket(api, private=False)
    from backend.tests.test_api import unrelated_task_managers
    with api.app.state.store.transaction() as c:
        unrelated_task_managers(c)
    with api.app.state.store.transaction() as c:
        child = H.task_create(c, 'human:ana', 'Evidence', 'x', 'cmo', parent_id=task['id'], next_run=True)
        c.execute("UPDATE tasks SET title='Review 1: previous', private=1, requester_id=?, note=? WHERE id=?",
                  (reader, f'Tests: head {HEAD}: 9 passed, 0 failed', child['id']))
        assert H.task_private_readable(c, reader, H.task(c, child['id']))
        other = task['owner_id'] if reader == 'human:ana' else 'human:ana'
        assert not H.task_private_readable(c, other, H.task(c, child['id']))
    refused = move(api, task)
    assert refused.status_code == 422 and 'missing test receipt' in refused.text
    with api.app.state.store.transaction() as c:
        assert not c.execute("SELECT 1 FROM jobs WHERE bot_id='cmo'").fetchone()
        c.execute('UPDATE tasks SET private=0 WHERE id=?', (child['id'],))
    assert move(api, task).status_code == 200


def test_github_errors_and_head_changes_during_collection_refuse(api, monkeypatch):
    task = ticket(api, f'Tests: head {HEAD}: 9 passed')
    service = api.app.state.github_app
    monkeypatch.setattr(service, 'row', lambda c: {'org': 'ticoteam'})
    monkeypatch.setattr(service, 'mint', lambda *a: ('synthetic', 'later'))
    monkeypatch.setattr(service, '_call', lambda *a, **kw: SimpleNamespace(status_code=503))
    refused = move(api, task)
    assert refused.status_code == 422 and 'GitHub answered 503' in refused.text, 'the refusal says why it could not verify'
    calls = [0]
    def fetch(method, path, **kw):
        if '/pulls/' in path:
            calls[0] += 1
            data = {'head': {'sha': HEAD if calls[0] == 1 else OLD}}
        elif path.endswith('check-runs'):
            data = {'check_runs': []}
        else:
            data = []
        return SimpleNamespace(status_code=200, json=lambda: data)
    monkeypatch.setattr(service, '_call', fetch)
    assert move(api, task).status_code == 422
    current = get(api, 'tasks/' + task['id'])['task']
    assert current['step_id'] == task['step_id'] and not hold(current).get('since')


def test_author_labels_override_pr_path_default(api):
    task = ticket(api, change_paths=['ui/page.js'])
    from backend.github import apply_creation_qa
    with api.app.state.store.transaction() as c:
        apply_creation_qa(c, {'html_url': PR, 'labels': [{'name': 'no-qa'}]}, ['ui/page.js'])
        assert 'qa' not in H.task_labels(H.task(c, task['id']))
    # Explicit qa also wins for a docs-only PR.
    with api.app.state.store.transaction() as c:
        other = H.task_create(c, 'human:ana', 'Docs QA by author', 'x', 'cpo', type=task['type_id'])
        H.task_link(c, 'human:ana', other['id'], PR.replace('/412', '/414'))
        apply_creation_qa(c, {'html_url': PR.replace('/412', '/414'), 'labels': [{'name': 'qa'}]}, ['docs/help.md'])
        assert 'qa' in H.task_labels(H.task(c, other['id']))


def test_incomplete_pr_paths_are_retried_without_losing_pending_default(api, monkeypatch):
    from backend import github as G
    task = ticket(api)
    service = api.app.state.github_app
    monkeypatch.setattr(service, '_call', lambda *a, **kw: SimpleNamespace(status_code=200, json=lambda: [{'filename': 'docs/help.md'}]))
    with pytest.raises(ValueError, match='incomplete'):
        G.creation_paths(service, {'html_url': PR, 'changed_files': 2}, {})
    with api.app.state.store.read() as c:
        detail = json.loads(c.execute("SELECT detail FROM task_links WHERE task_id=? AND kind='pr'", (task['id'],)).fetchone()[0])
        assert detail['qa_default_pending'] is True
        assert 'qa' not in H.task_labels(H.task(c, task['id']))
