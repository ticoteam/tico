"""Two-party privacy, transparency, defaults and conservative upgrade boundaries."""
import json

import pytest

from backend import hubdb as H
from backend import task_relations as TR
from backend.tests.test_tasks_board import api, bot_token, get, headers, post


@pytest.mark.parametrize('requester,owner', [('human:ben', 'bot:cpo')])
def test_private_two_party_matrix(api, requester, owner):
    tokens = {'human:ana': 'ana-test', 'human:ben': 'ben-test', 'human:priya': 'priya-test',
              **{'bot:' + slug: bot_token(api, slug) for slug in ('ops', 'cpo', 'cmo')}}
    with api.app.state.store.transaction() as c:
        typ = H.type_create(c, H.KEEPER, 'Private matrix work',
                            [{'name': 'Open', 'status': 'open'}], bots='work')
        task = H.task_create(c, requester, 'Review the sensitive packet', 'Sensitive packet.', owner,
                             private=True, type=typ['id'], lint=False)
        c.execute('INSERT INTO task_delegations(task_id,delegate,requested_by,message_id,expires) '
                  'VALUES(?,?,?,?,?)', (task['id'], 'bot:cmo', requester, None, H.shift(H.now(), hours=24)))
    for actor, token in tokens.items():
        response = api.get('/api/v2/tasks/' + task['id'], headers=headers(token))
        assert response.status_code == (200 if actor in (requester, owner) else 404), response.text
        listed = get(api, 'tasks', token=token)['tasks']
        assert (task['id'] in {row['id'] for row in listed}) == (actor in (requester, owner))
        response = api.post('/api/v2/sql', json={'sql': 'SELECT id,title FROM tasks'}, headers=headers(token))
        assert response.status_code == 200, response.text
        assert ('Sensitive packet' in response.text or task['id'] in response.text) == (actor in (requester, owner))


def test_company_read_does_not_grant_work_but_dev_type_does(api):
    token = bot_token(api, 'ops')
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Build the board adapter', 'body': 'Implement the adapter.'})
    assert not task['private']
    assert get(api, 'tasks/' + task['id'], token=token)['task']['id'] == task['id']
    post(api, 'tasks/' + task['id'], {'version': task['version'], 'owner': 'ops'}, token=token, expected=403)
    typ = post(api, 'task-types', {'name': 'Dev', 'bots': 'work', 'steps': [
        {'name': 'Todo', 'status': 'open'}, {'name': 'PR Review', 'status': 'review'}]})['type']
    task = post(api, 'tasks/' + task['id'], {'version': task['version'], 'type': typ['id']})
    found = get(api, 'tasks?type=' + typ['id'], token=token)['tasks']
    assert task['id'] in {row['id'] for row in found}
    post(api, 'tasks/' + task['id'] + '/links', {'url': 'https://github.com/acme/example/pull/7'}, token=token)
    task = get(api, 'tasks/' + task['id'], token=token)['task']
    task = post(api, 'tasks/' + task['id'], {'version': task['version'], 'step': 'PR Review'}, token=token)
    assert task['status'] == 'review'
    task = post(api, 'tasks/' + task['id'], {'version': task['version'], 'private': True})
    get(api, 'tasks/' + task['id'], token=token, expected=404)


def test_private_defaults_reassignment_and_human_publication(api):
    ops = bot_token(api, 'ops')
    cpo = bot_token(api, 'cpo')
    with api.app.state.store.transaction() as c:
        for slug in ('ops', 'cpo'):
            config = json.loads(c.execute("SELECT config_json FROM bot_config WHERE bot=?", (slug,)).fetchone()[0])
            config['private_tasks_default'] = True
            c.execute("UPDATE bot_config SET config_json=? WHERE bot=?", (json.dumps(config), slug))
    assigned = post(api, 'tasks', {'owner': 'ops', 'title': 'Review the request', 'body': 'Review it.'})
    created = post(api, 'tasks', {'owner': 'cmo', 'title': 'Draft the response', 'body': 'Draft it.', 'private': False}, token=cpo)
    assert assigned['private'] and created['private']
    explicit = post(api, 'tasks', {'owner': 'ops', 'title': 'Review the public request', 'body': 'Review it.', 'private': False})
    assert not explicit['private']
    post(api, 'tasks/' + assigned['id'], {'version': assigned['version'], 'private': False}, token=ops, expected=422)
    changed = post(api, 'tasks/' + assigned['id'], {'version': assigned['version'], 'owner': 'cpo'})
    assert changed['private']
    get(api, 'tasks/' + assigned['id'], token=ops, expected=404)
    assert get(api, 'tasks/' + assigned['id'], token=cpo)['task']['private']
    published = post(api, 'tasks/' + assigned['id'], {'version': changed['version'], 'private': False})
    assert not published['private']
    assert get(api, 'tasks/' + assigned['id'], token=ops)['task']['id'] == assigned['id']
    post(api, 'tasks/' + created['id'], {'version': created['version'], 'private': False}, token=cpo, expected=422)


def test_private_parent_has_no_ancestry_bypass_and_requires_detachment(api):
    parent = post(api, 'tasks', {'owner': 'ben', 'title': 'Review the packet', 'body': 'Review it.', 'private': True})
    child = post(api, 'tasks', {'owner': 'priya', 'title': 'Check the packet', 'body': 'Check it.',
                               'relations': [{'task': parent['id'], 'kind': 'parent'}]}, token='ben-test')
    assert child['private'] and child['requester'] == 'human:ben'
    get(api, 'tasks/' + child['id'], expected=404)
    detail = get(api, 'tasks/' + parent['id'])
    assert not detail['children'] and detail['task']['parts']['total'] == 0
    post(api, 'tasks/' + child['id'], {'version': child['version'], 'private': False}, token='ben-test', expected=422)
    detached = post(api, 'tasks/' + child['id'] + '/relations', {'task': parent['id'], 'kind': 'parent', 'remove': True},
                    token='ben-test')
    assert 'parent' not in detached['relations']
    published = post(api, 'tasks/' + child['id'], {'version': detached['version'], 'private': False}, token='ben-test')
    assert not published['private']


def test_cached_comment_response_cannot_bypass_reassignment(api):
    task = post(api, 'tasks', {'owner': 'ben', 'title': 'Review the private draft', 'body': 'Review it.', 'private': True})
    replay_headers = headers('ben-test')
    url = '/api/v2/tasks/' + task['id'] + '/comments'
    first = api.post(url, json={'text': 'Sensitive response.'}, headers=replay_headers)
    assert first.status_code == 200, first.text
    task = get(api, 'tasks/' + task['id'])['task']
    post(api, 'tasks/' + task['id'], {'version': task['version'], 'owner': 'priya'})
    response = api.post(url, json={'text': 'Sensitive response.'}, headers=replay_headers)
    assert response.status_code == 404 and 'Sensitive response.' not in response.text


def test_cloud_upgrade_classifies_legacy_identity_and_keeps_files_intact(api):
    ordinary = post(api, 'tasks', {'owner': 'cmo', 'title': 'Review ordinary work', 'body': 'Ordinary content.'})
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Review the older work', 'body': 'Older content.'})
    child = post(api, 'tasks', {'owner': 'ben', 'title': 'Review related work', 'body': 'Related content.', 'relations': [{'task': task['id'], 'kind': 'parent'}]})
    routine = post(api, 'tasks', {'owner': 'cmo', 'title': 'Review scheduled work', 'body': 'Scheduled content.'})
    orphan = post(api, 'tasks', {'owner': 'priya', 'title': 'Review orphaned work', 'body': 'Orphan content.'})
    post(api, 'tasks/' + task['id'] + '/files', {'name': 'legacy-brief.md', 'text': 'Legacy attachment bytes.'})
    with api.app.state.store.transaction() as c:
        counts = {name: c.execute('SELECT count(*) FROM ' + name).fetchone()[0]
                  for name in ('tasks', 'blobs', 'task_assets', 'bot_files')}
        c.execute("UPDATE bot_config SET config_json=? WHERE bot='cpo'", (json.dumps({'template': 'general-counsel'}),))
        c.execute("UPDATE tasks SET requester='human:missing' WHERE id=?", (orphan['id'],))
        c.execute("UPDATE tasks SET requester='keeper' WHERE id=?", (routine['id'],))
        # A database from before task privacy had no change log either (backend/events.py).
        for (name,) in c.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'changes_%'").fetchall():
            c.execute('DROP TRIGGER ' + name)
        c.execute('DROP INDEX IF EXISTS tasks_private')
        c.execute('ALTER TABLE tasks DROP COLUMN private')
        c.execute('DELETE FROM cloud_migrations WHERE version=57')
    api.app.state.store.initialize(seed_market=False)
    with api.app.state.store.read() as c:
        assert H.task(c, task['id'])['private'] == 1
        assert H.task(c, child['id'])['private'] == 1
        assert H.task(c, orphan['id'])['private'] == 1
        assert H.task(c, ordinary['id'])['private'] == 0
        assert H.task(c, routine['id'])['private'] == 0
        assert c.execute('SELECT 1 FROM cloud_migrations WHERE version=57').fetchone()
        assert all(c.execute('SELECT count(*) FROM ' + name).fetchone()[0] == count for name, count in counts.items())
        assert c.execute('SELECT 1 FROM cloud_migrations WHERE version=53').fetchone()
    api.app.state.store.initialize(seed_market=False)
    assert get(api, 'tasks/' + task['id'])['task']['private']


def test_private_dependency_and_refusal_audit_never_copy_sensitive_content(api):
    private = post(api, 'tasks', {'owner': 'cpo', 'title': 'Review sensitive evidence', 'body': 'Review it.', 'private': True})
    public = post(api, 'tasks', {'owner': 'cmo', 'title': 'Review release timing', 'body': 'Review it.'})
    post(api, 'tasks/' + public['id'] + '/relations', {'task': private['id'], 'kind': 'blocked_by'}, expected=422)
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO task_relations(from_task,to_task,kind,created) VALUES(?,?,?,'now')", (private['id'], public['id'], 'blocks'))
        H._unblock(c, H.task(c, private['id']))
        assert TR.blocker_ids(c, public['id']) == [private['id']]
        with pytest.raises(H.Refused) as exc:
            H.task_update(c, 'bot:cpo', private['id'], body='Read SECRET-PACKET', status='ready')
        assert exc.value.private
        audit = c.execute("SELECT detail_json FROM events WHERE action='refused'").fetchall()
        assert all('SECRET-PACKET' not in row[0] for row in audit)


def test_bot_acting_for_human_cannot_publish_in_domain(api):
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, 'human:ben', 'Review confidential work', 'Review it.', 'bot:cpo', private=True, lint=False)
        token = H.VIA.set('assistant')
        try:
            with pytest.raises(H.Refused):
                H.task_update(c, 'human:ben', task['id'], private=False)
        finally:
            H.VIA.reset(token)
        assert H.task_private(c, H.task(c, task['id']))


def test_acted_duplicate_diagnostics_keep_private_ids_out_of_responses_and_audit(api):
    with api.app.state.store.transaction() as c:
        private = H.task_create(c, 'human:ben', 'Review confidential work', 'Review it.',
                                'bot:cpo', private=True, lint=False)
        ordinary = H.task_create(c, 'human:ben', 'Review ordinary work', 'Review it.',
                                 'bot:cpo', lint=False)
        token = H.VIA.set('assistant')
        try:
            for write in (lambda: H.task_create(c, 'human:ben', private['title'], 'Review it.',
                                                'bot:cpo', lint=False),
                          lambda: H.task_update(c, 'human:ben', ordinary['id'], title=private['title'])):
                with pytest.raises(H.Refused) as refused:
                    write()
                assert private['id'] not in str(refused.value)
            with pytest.raises(H.Refused) as public_duplicate:
                H.task_create(c, 'human:ben', ordinary['title'], 'Review it.', 'bot:cpo', lint=False)
            assert ordinary['id'] in str(public_duplicate.value)
        finally:
            H.VIA.reset(token)
        diagnostics = c.execute("SELECT detail_json FROM events WHERE action='refused'").fetchall()
        assert private['id'] not in str([tuple(row) for row in diagnostics])


def test_native_hub_upgrade_from_23_is_atomic_and_preserves_tasks_messages(tmp_path):
    path = tmp_path / 'native-hub.db'
    c = H.connect(path)
    H.sync_registry(c, {}, {'people': [{'id': 'ana', 'email': 'ana@acme.example'},
                                      {'id': 'ben', 'email': 'ben@acme.example'}]})
    ordinary = H.task_create(c, 'human:ana', 'Review ordinary work', 'Review it.', 'human:ben', lint=False)
    orphan = H.task_create(c, 'human:ana', 'Review orphan work', 'Review it.', 'human:ben', lint=False)
    comment = H.task_comment(c, 'human:ana', orphan['id'], 'Preserved tracked comment.', wake=False)
    c.execute("UPDATE tasks SET requester='human:missing' WHERE id=?", (orphan['id'],))
    c.execute('DROP INDEX IF EXISTS tasks_private')
    c.execute('ALTER TABLE tasks DROP COLUMN private')
    c.execute('PRAGMA user_version=23')
    ids = {row[0] for row in c.execute('SELECT id FROM messages')}
    c.close()
    c = H.connect(path)
    try:
        assert c.execute('PRAGMA user_version').fetchone()[0] == len(H.MIGRATIONS)
        assert not H.task_private(c, H.task(c, ordinary['id']))
        assert H.task_private(c, H.task(c, orphan['id']))
        assert {row[0] for row in c.execute('SELECT id FROM messages')} == ids
        assert H.message(c, comment['id'])['body'] == 'Preserved tracked comment.'
        H.migrate(c)
        assert c.execute('PRAGMA foreign_key_check').fetchall() == []
    finally:
        c.close()
