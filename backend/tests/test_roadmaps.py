"""Roadmap privacy boundaries and the existing parent/child flow."""

from backend import hubdb as H
from backend.tests.test_tasks_board import api, get, post


def test_engineering_children_drive_approved_roadmap_and_reopen(api):
    roadmap = post(api, 'tasks', {'owner': 'cpo', 'title': 'Improve exports', 'body': 'Research exports.',
                                 'type': 'Product roadmap', 'step': 'Approved'})
    child = post(api, 'tasks', {'owner': 'ops', 'title': 'Build exports', 'body': 'Implement exports.',
                               'type': 'Engineering', 'relations': [{'task': roadmap['id'], 'kind': 'parent'}]})
    assert get(api, 'tasks/' + roadmap['id'])['task']['step']['name'] == 'Building'
    second = post(api, 'tasks', {'owner': 'ops', 'title': 'Check exports', 'body': 'Check the changes.',
                                'type': 'Engineering', 'relations': [{'task': roadmap['id'], 'kind': 'parent'}]})
    child = post(api, 'tasks/' + child['id'], {'version': child['version'], 'status': 'done'})
    assert get(api, 'tasks/' + roadmap['id'])['task']['step']['name'] == 'Building'
    post(api, 'tasks/' + second['id'], {'version': second['version'], 'status': 'done'})
    assert get(api, 'tasks/' + roadmap['id'])['task']['step']['name'] == 'Shipped'
    post(api, 'tasks/' + child['id'], {'version': child['version'], 'status': 'open'})
    assert get(api, 'tasks/' + roadmap['id'])['task']['step']['name'] == 'Building'


def test_visibility_defaults_do_not_rewrite_tasks_and_private_never_publishes(api):
    draft = post(api, 'tasks', {'owner': 'cpo', 'title': 'Research search', 'body': 'Internal draft',
                               'type': 'Product roadmap'})
    assert draft['visibility'] == 'private' and draft['public_title'] == '' and draft['public_summary'] == ''
    refused = post(api, 'tasks', {'owner': 'cpo', 'title': 'Research filters', 'body': 'Internal draft',
                                 'type': 'Product roadmap', 'visibility': 'public'}, expected=422)
    assert 'public title' in refused['error']['detail']
    post(api, 'tasks/' + draft['id'], {'version': draft['version'], 'visibility': 'public'}, expected=422)
    post(api, 'task-types/product-roadmap', {'default_visibility': 'public'})
    unwritten = post(api, 'tasks', {'owner': 'cpo', 'title': 'Research sorting', 'body': 'Internal draft',
                                   'type': 'Product roadmap'})
    assert unwritten['visibility'] == 'private' and unwritten['public_summary'] == ''
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Research search again', 'body': 'Internal draft',
                              'type': 'Product roadmap', 'public_title': 'Better search', 'public_summary': 'Find more'})
    assert (task['visibility'], task['public_title'], task['public_summary']) == ('public', 'Better search', 'Find more')
    post(api, 'task-types/product-roadmap', {'default_visibility': 'private'})
    task = get(api, 'tasks/' + task['id'])['task']
    assert task['visibility'] == 'public'
    changed = post(api, 'tasks/' + task['id'], {'version': task['version'], 'body': 'Internal revision'})
    assert changed['public_summary'] == 'Find more'
    private = post(api, 'tasks', {'owner': 'cpo', 'title': 'Sensitive research', 'body': 'Secret',
                                 'type': 'Product roadmap', 'private': True})
    post(api, 'tasks/' + private['id'], {'version': private['version'], 'visibility': 'public'}, expected=422)
    child = post(api, 'tasks', {'owner': 'cpo', 'title': 'Review a part', 'body': 'Part', 'visibility': 'public',
                               'public_title': 'A part', 'relations': [{'task': task['id'], 'kind': 'parent'}]})
    post(api, 'tasks/' + child['id'] + '/relations', {'task': private['id'], 'kind': 'parent'})
    assert get(api, 'tasks/' + task['id'])['task']['visibility'] == 'public'
    assert not get(api, 'tasks/' + task['id'])['task']['private']
    assert get(api, 'tasks/' + child['id'])['task']['visibility'] == 'private'


def test_bot_public_text_and_comment_need_exact_single_use_approval(api):
    from backend.tests.test_tasks_board import bot_token
    token = bot_token(api, 'cpo')
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Improve search', 'body': 'Draft', 'type': 'Product roadmap'})
    changes = {'visibility': 'public', 'public_title': 'Search', 'public_summary': 'Search improvements'}
    post(api, 'tasks/' + task['id'], {'version': task['version'], **changes}, token=token, expected=422)
    preview = post(api, 'tasks/' + task['id'] + '/publication-preview', changes, token=token)
    approval = post(api, 'approvals', {'kind': 'publish', 'task_id': task['id'], 'payload': preview['payload']}, token=token)
    post(api, 'approvals/' + approval['id'], {'decision': 'approved'})
    task = post(api, 'tasks/' + task['id'], {'version': task['version'], **changes, 'approval_id': approval['id']}, token=token)
    assert task['public_summary'] == changes['public_summary']
    post(api, 'tasks/' + task['id'] + '/comments', {'text': 'Now available', 'public': True}, token=token, expected=422)
    preview = post(api, 'tasks/' + task['id'] + '/publication-preview', {'comment': 'Now available'}, token=token)
    approval = post(api, 'approvals', {'kind': 'publish', 'task_id': task['id'], 'payload': preview['payload']}, token=token)
    post(api, 'approvals/' + approval['id'], {'decision': 'approved'})
    result = post(api, 'tasks/' + task['id'] + '/comments', {'text': 'Now available', 'public': True,
                                                        'approval_id': approval['id']}, token=token)
    assert result['comment']['public'] == 1
    post(api, 'tasks/' + task['id'] + '/comments', {'text': 'Now available', 'public': True,
                                                'approval_id': approval['id']}, token=token, expected=422)


def public_setup(api, limit=10):
    post(api, 'tags', {'key': 'roadmap'})
    post(api, 'tags', {'key': 'internal'})
    post(api, 'roadmaps/settings', {'labels': ['roadmap'], 'cors_origins': ['https://acme.example'],
                                  'action_limit': limit})
    return post(api, 'tasks', {'owner': 'cpo', 'title': 'Internal title', 'body': 'Internal summary',
                               'type': 'Product roadmap', 'labels': ['roadmap'], 'visibility': 'public',
                               'public_title': 'Roadmap item', 'public_summary': 'What it does'})


ORIGIN = {'Origin': 'https://acme.example'}


def test_public_projection_moderation_and_no_label_enumeration(api):
    task = public_setup(api)
    task = post(api, 'tasks/' + task['id'], {'version': task['version'], 'public_title': 'Search',
                                          'public_summary': 'Find things faster'})
    post(api, 'tasks/' + task['id'] + '/comments', {'text': 'Internal discussion'})
    public = post(api, 'tasks/' + task['id'] + '/comments', {'text': 'Team update', 'public': True})['comment']
    post(api, 'tasks/' + task['id'] + '/requesters', {'email': 'private@acme.example', 'name': 'Private Name'})
    child = post(api, 'tasks', {'owner': 'cpo', 'title': 'Secret child', 'body': 'Internal work',
                              'visibility': 'public', 'public_title': 'Child part', 'labels': ['roadmap'],
                              'relations': [{'task': task['id'], 'kind': 'parent'}]})
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE task_steps SET public_name=? WHERE id=?', ('Under consideration', task['step_id']))
    path = '/public/roadmaps/roadmap/tasks/' + task['id']
    response = api.get(path, headers=ORIGIN)
    assert response.status_code == 200, response.text
    result = response.json()
    assert set(result['task']) == {'id', 'title', 'summary', 'step', 'requester_count', 'parts'}
    assert result['task']['title'] == 'Search' and result['task']['requester_count'] == 1
    assert result['task']['step'] == 'Under consideration'
    assert result['comments'][0]['author'] == 'Team'
    assert all(secret not in response.text for secret in ('private@', 'Private Name', 'Internal', 'Secret child', child['id']))
    assert len(api.get('/public/roadmaps/roadmap', headers=ORIGIN).json()['tasks']) == 1
    assert api.get('/public/roadmaps/internal', headers=ORIGIN).status_code == 404
    post(api, 'tasks/' + task['id'] + '/comments/' + public['id'] + '/moderate', {'action': 'hide'})
    assert api.get(path, headers=ORIGIN).json()['comments'] == []
    post(api, 'tasks/' + task['id'] + '/comments/' + public['id'] + '/moderate', {'action': 'show'})
    assert len(api.get(path, headers=ORIGIN).json()['comments']) == 1
    post(api, 'tasks/' + task['id'], {'version': task['version'], 'visibility': 'private'})
    assert api.get(path, headers=ORIGIN).status_code == 404


def test_cors_and_ip_limit_shared_across_actions_and_tokens_are_task_bound(api):
    task = public_setup(api, limit=4)
    base = '/public/roadmaps/roadmap/tasks/' + task['id']
    read = api.get(base)
    assert read.status_code == 200 and read.headers['vary'] == 'Origin'
    assert 'access-control-allow-origin' not in read.headers
    assert api.head(base).status_code == 200
    read = api.get(base, headers={'Origin': 'https://evil.example'})
    assert read.status_code == 200 and 'access-control-allow-origin' not in read.headers
    assert api.get(base, headers=ORIGIN).headers['access-control-allow-origin'] == ORIGIN['Origin']
    assert api.post(base + '/requesters', json={}).status_code == 403
    assert api.post(base + '/requesters', json={}, headers={'Origin': 'https://evil.example'}).status_code == 403
    preflight = api.options(base + '/requesters', headers={**ORIGIN, 'Access-Control-Request-Method': 'POST',
                                                         'Access-Control-Request-Headers': 'Content-Type'})
    assert preflight.status_code == 204 and preflight.headers['access-control-allow-origin'] == ORIGIN['Origin']
    assert 'access-control-allow-credentials' not in preflight.headers
    token = api.post(base + '/requesters', json={'email': 'customer@acme.example'}, headers=ORIGIN).json()['requester_token']
    other = post(api, 'tasks', {'owner': 'cpo', 'title': 'Other', 'body': 'Research it', 'type': 'Product roadmap', 'labels': ['roadmap']})
    assert api.delete('/public/roadmaps/roadmap/tasks/' + other['id'] + '/requesters/' + token, headers=ORIGIN).status_code == 404
    with api.app.state.store.read() as c:
        assert c.execute('SELECT token_hash FROM task_requesters').fetchone()[0] != token
    assert api.post(base + '/comments', json={'text': 'Please add this'}, headers=ORIGIN).status_code == 200
    assert api.delete(base + '/requesters/' + token, headers=ORIGIN).status_code == 200
    assert api.delete(base + '/requesters/' + token, headers=ORIGIN).status_code == 404
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET config_json=json_set(config_json,'$.template','product-manager') WHERE bot='cpo'")
    suggested = api.post('/public/roadmaps/roadmap/suggest', json={'title': 'New idea', 'summary': 'Details'}, headers=ORIGIN)
    assert suggested.status_code == 200, suggested.text
    with api.app.state.store.read() as c:
        row = H.task(c, suggested.json()['id'])
        assert row['visibility'] == 'private'
    assert api.post(base + '/requesters', json={}, headers=ORIGIN).status_code == 429
    post(api, 'roadmaps/settings', {'labels': ['roadmap'], 'cors_origins': ['*']}, expected=422)


def test_requester_contacts_follow_task_privacy_and_teammates_can_remove_themselves(api):
    private = post(api, 'tasks', {'owner': 'cpo', 'title': 'Review private feedback', 'body': 'Sensitive', 'private': True})
    post(api, 'tasks/' + private['id'] + '/requesters', {'email': 'sensitive@acme.example'})
    get(api, 'tasks/' + private['id'] + '/requesters', token='ben-test', expected=404)
    result = post(api, 'sql', {'sql': 'SELECT * FROM task_requesters'}, token='ben-test')
    assert 'sensitive@acme.example' not in str(result)
    result = post(api, 'sql', {'sql': 'SELECT * FROM task_requesters'})
    assert 'token_hash' not in str(result)
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Improve editing', 'body': 'Feedback', 'type': 'Product roadmap'})
    external = post(api, 'tasks/' + task['id'] + '/requesters',
                    {'email': 'customer@acme.example', 'name': 'Customer', 'source': 'meeting'})
    from backend.tests.test_mcp import call
    failed, result = call(api, 'hub_task_requester_save', {'id': task['id'], 'requester_id': external['id'], 'important': True})
    assert not failed, result
    changed = get(api, 'tasks/' + task['id'] + '/requesters')['requesters'][0]
    assert changed['important'] and changed['email'] == 'customer@acme.example'
    assert changed['name'] == 'Customer' and changed['source'] == 'meeting'
    post(api, 'tasks/' + task['id'] + '/requesters/' + external['id'], {}, expected=422)
    post(api, 'tasks/' + task['id'] + '/requesters/' + external['id'] + '/delete', {})
    sized = post(api, 'tasks/' + task['id'], {'version': task['version'], 'size': 7.5})
    assert get(api, 'tasks/' + task['id'])['task']['size'] == 7.5
    assert get(api, 'roadmap-tasks?sort=size')['tasks'][0]['size'] == 7.5
    post(api, 'tasks/' + task['id'], {'version': sized['version'], 'size': None})
    assert get(api, 'tasks/' + task['id'])['task']['size'] is None
    own = post(api, 'tasks/' + task['id'] + '/requesters', {'actor': 'human:priya'}, token='priya-test')
    with api.app.state.store.read() as c:
        assert not c.execute('SELECT 1 FROM idempotency WHERE instr(response_json,?)>0',
                             (own['requester_token'],)).fetchone()
    post(api, 'tasks/' + task['id'] + '/requesters/' + own['id'] + '/delete', {}, token='priya-test')
    assert get(api, 'tasks/' + task['id'] + '/requesters')['requester_count'] == 0


def test_bot_creation_and_label_publication_require_review(api):
    from backend.tests.test_tasks_board import bot_token
    task = public_setup(api)
    token = bot_token(api, 'cpo')
    for identity in (token, 'ben-via-botops'):
        created = post(api, 'tasks', {'owner': 'cpo', 'title': 'Research requested exports',
                                    'body': 'customer@acme.example', 'type': 'Product roadmap',
                                    'visibility': 'public', 'labels': ['roadmap']}, token=identity)
        assert created['visibility'] == 'private'
        assert api.get('/public/roadmaps/roadmap/tasks/' + created['id']).status_code == 404
    task = post(api, 'tasks/' + task['id'], {'version': task['version'], 'labels': []})
    token = 'ben-via-botops'
    post(api, 'tasks/' + task['id'], {'version': task['version'], 'labels': ['roadmap']}, token=token, expected=422)
    preview = post(api, 'tasks/' + task['id'] + '/publication-preview', {'labels': ['roadmap']}, token=token)
    approval = post(api, 'approvals', {'kind': 'publish', 'task_id': task['id'], 'payload': preview['payload']}, token=token)
    post(api, 'approvals/' + approval['id'], {'decision': 'approved'})
    post(api, 'tasks/' + task['id'], {'version': task['version'], 'labels': ['roadmap'],
                                     'approval_id': approval['id']}, token=token)
    assert api.get('/public/roadmaps/roadmap/tasks/' + task['id']).status_code == 200
    post(api, 'roadmaps/settings', {'action_limit': 20})
    settings = get(api, 'roadmaps/settings')
    assert settings['labels'] == ['roadmap'] and settings['cors_origins'] == [ORIGIN['Origin']]
    post(api, 'roadmaps/settings', {'action_limit': 1})
    assert api.post('/public/roadmaps/roadmap/tasks/' + task['id'] + '/comments', json={'text': 'Please add this'}, headers=ORIGIN).status_code == 200
    private = post(api, 'tasks/' + task['id'], {'version': get(api, 'tasks/' + task['id'])['task']['version'],
                                               'visibility': 'private'})
    responses = [api.delete('/public/roadmaps/roadmap/tasks/' + tid + '/requesters/invalid', headers=ORIGIN)
                 for tid in (private['id'], 'not-a-task')]
    assert all(r.status_code == 404 for r in responses) and responses[0].json() == responses[1].json()


def test_manual_graduation_all_declined_and_existing_dev_type(api):
    from backend import roadmap_flow as F
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Ship a small change', 'body': 'Ready to ship',
                               'type': 'Product roadmap', 'step': 'Building'})
    assert task['step']['name'] == 'Building'
    task = post(api, 'tasks/' + task['id'], {'version': task['version'], 'step': 'Shipped', 'status': 'done'})
    assert task['step']['name'] == 'Shipped'
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM task_steps WHERE type_id='engineering'")
        c.execute("DELETE FROM task_types WHERE id='engineering'")
        dev = H.type_create(c, 'human:ana', 'Dev tickets', steps=[{'name': 'Todo', 'status': 'open'},
                                                        {'name': 'Done', 'status': 'closed'},
                                                        {'name': 'Declined', 'status': 'declined'}])
        F.ensure(c)
        assert not c.execute("SELECT 1 FROM task_types WHERE id='engineering'").fetchone()
        assert H.type_get(c, dev['id'])['manager'] == 'engineering-lead'
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Explore another change', 'body': 'Review first',
                               'type': 'Product roadmap', 'step': 'Approved'})
    child = post(api, 'tasks', {'owner': 'ops', 'title': 'Build the change', 'body': 'Implementation',
                                'type': dev['id'], 'relations': [{'task': task['id'], 'kind': 'parent'}]})
    assert get(api, 'tasks/' + task['id'])['task']['step']['name'] == 'Building'
    post(api, 'tasks/' + child['id'], {'version': child['version'], 'status': 'declined'})
    assert get(api, 'tasks/' + task['id'])['task']['step']['name'] == 'Approved'


def test_internal_text_never_becomes_public_text(api):
    post(api, 'tags', {'key': 'roadmap'})
    post(api, 'roadmaps/settings', {'labels': ['roadmap'], 'cors_origins': ['https://acme.example']})
    secret = 'Acme (jane@acme.example) threatens to churn; contract $40k'
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Keep Acme happy', 'body': secret,
                              'type': 'Product roadmap', 'labels': ['roadmap']})
    assert task['visibility'] == 'private' and task['public_summary'] == ''
    listed = api.get('/public/roadmaps/roadmap')
    assert listed.status_code == 200 and listed.json()['tasks'] == []
    assert 'jane@' not in listed.text and '$40k' not in listed.text
    refused = post(api, 'tasks/' + task['id'], {'version': task['version'], 'visibility': 'public'}, expected=422)
    assert 'public title' in refused['error']['detail']
    task = post(api, 'tasks/' + task['id'], {'version': task['version'], 'visibility': 'public',
                                          'public_title': 'Faster exports'})
    listed = api.get('/public/roadmaps/roadmap')
    assert [t['title'] for t in listed.json()['tasks']] == ['Faster exports']
    assert listed.json()['tasks'][0]['summary'] == ''
    shown = api.get('/public/roadmaps/roadmap/tasks/' + task['id']).text
    assert all(text not in listed.text + shown for text in ('jane@', '$40k', 'Keep Acme happy'))


def test_first_version_copies_leave_private_tasks_and_the_product_type_turns_private(api):
    from backend import roadmaps as R
    hidden = post(api, 'tasks', {'owner': 'cpo', 'title': 'Old idea', 'body': 'Internal notes', 'type': 'Product roadmap'})
    shown = post(api, 'tasks', {'owner': 'cpo', 'title': 'Shown idea', 'body': 'Shown notes', 'type': 'Product roadmap',
                               'visibility': 'public', 'public_title': 'Shown idea', 'public_summary': 'Shown notes'})
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM registry_metadata WHERE key='roadmaps_explicit_public_text'")
        c.execute("UPDATE task_types SET default_visibility='public' WHERE id='product-roadmap'")
        c.execute("UPDATE tasks SET public_title=title,public_summary=body WHERE id=?", (hidden['id'],))
        R.migrate(c)
        assert H.type_get(c, 'product-roadmap')['default_visibility'] == 'private'
        assert tuple(c.execute('SELECT public_title,public_summary FROM tasks WHERE id=?', (hidden['id'],)).fetchone()) == ('', '')
        assert tuple(c.execute('SELECT visibility,public_title FROM tasks WHERE id=?', (shown['id'],)).fetchone()) == ('public', 'Shown idea')
        c.execute("UPDATE task_types SET default_visibility='public' WHERE id='product-roadmap'")
        R.migrate(c)                                                  # once only: a later choice stays
        assert H.type_get(c, 'product-roadmap')['default_visibility'] == 'public'


def test_a_requester_token_withdraws_after_unlabel_disable_and_delete(api):
    task = public_setup(api, limit=50)
    base = '/public/roadmaps/roadmap/tasks/' + task['id']

    def follow(path=base):
        response = api.post(path + '/requesters', json={'email': 'customer@acme.example', 'phone': '555-0100'}, headers=ORIGIN)
        assert response.status_code == 200, response.text
        return response.json()['requester_token']

    def count(tid):
        with api.app.state.store.read() as c:
            return c.execute('SELECT count(*) FROM task_requesters WHERE task_id=?', (tid,)).fetchone()[0]

    unlabelled, disabled = follow(), follow()
    task = post(api, 'tasks/' + task['id'], {'version': task['version'], 'labels': []})
    assert api.delete(base + '/requesters/' + unlabelled, headers=ORIGIN).status_code == 200
    assert count(task['id']) == 1
    post(api, 'tasks/' + task['id'], {'version': task['version'], 'labels': ['roadmap']})
    post(api, 'roadmaps/settings', {'labels': []})
    assert api.delete(base + '/requesters/' + disabled, headers=ORIGIN).status_code == 200
    assert count(task['id']) == 0
    unknown = api.delete(base + '/requesters/' + disabled, headers=ORIGIN)
    missing = api.delete('/public/roadmaps/roadmap/tasks/not-a-task/requesters/' + disabled, headers=ORIGIN)
    assert unknown.status_code == missing.status_code == 404 and unknown.json() == missing.json()

    post(api, 'roadmaps/settings', {'labels': ['roadmap']})
    trashed = post(api, 'tasks', {'owner': 'ben', 'title': 'Bulk exports', 'body': 'Internal', 'type': 'Product roadmap',
                                 'labels': ['roadmap'], 'visibility': 'public', 'public_title': 'Bulk exports'})
    path = '/public/roadmaps/roadmap/tasks/' + trashed['id']
    leaving, staying = follow(path), follow(path)
    assert post(api, 'tasks/' + trashed['id'] + '/delete', {}) == {'deleted': trashed['id']}
    assert api.delete(path + '/requesters/' + leaving, headers=ORIGIN).status_code == 200
    assert api.delete(path + '/requesters/' + leaving, headers=ORIGIN).status_code == 404
    import hashlib
    with api.app.state.store.read() as c:
        snapshot = c.execute('SELECT rows_json FROM task_trash WHERE task_id=?', (trashed['id'],)).fetchone()[0]
    assert hashlib.sha256(leaving.encode()).hexdigest() not in snapshot
    assert hashlib.sha256(staying.encode()).hexdigest() in snapshot
    post(api, 'tasks/' + trashed['id'] + '/restore', {})
    assert count(trashed['id']) == 1


def test_suggestions_have_an_overall_cap_and_spam_checks(api):
    public_setup(api, limit=100)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET config_json=json_set(config_json,'$.template','product-manager') WHERE bot='cpo'")
    post(api, 'roadmaps/settings', {'suggestion_limit': 2})
    assert get(api, 'roadmaps/settings')['suggestion_limit'] == 2
    suggest = lambda body: api.post('/public/roadmaps/roadmap/suggest', json=body, headers=ORIGIN)
    assert suggest({'title': 'See <script>alert(1)</script>'}).status_code == 422
    links = 'https://a.example https://b.example https://c.example'
    assert suggest({'title': 'Exports', 'summary': links}).status_code == 422
    first = suggest({'title': 'Exports', 'summary': 'CSV please'})
    assert first.status_code == 200, first.text
    assert suggest({'title': 'Imports', 'summary': 'From CSV'}).status_code == 200
    capped = suggest({'title': 'Webhooks'})
    assert capped.status_code == 429 and 'waiting' in capped.json()['error']['detail']
    with api.app.state.store.read() as c:
        assert c.execute('SELECT count(*) FROM tasks WHERE requester=?', (H.KEEPER,)).fetchone()[0] == 2
    waiting = get(api, 'tasks/' + first.json()['id'])['task']
    post(api, 'tasks/' + waiting['id'], {'version': waiting['version'], 'step': 'Exploring'})
    assert suggest({'title': 'Webhooks'}).status_code == 200
    post(api, 'roadmaps/settings', {'suggestion_limit': 0}, expected=422)


def test_guard_refusals_carry_cors_headers_for_allowed_origins(api):
    task = public_setup(api)
    base = '/public/roadmaps/roadmap/tasks/' + task['id']
    big = api.post(base + '/comments', content=b'{"text":"' + b'x' * 70000 + b'"}',
                   headers={**ORIGIN, 'Content-Type': 'application/json'})
    assert big.status_code == 413 and big.json()['error']['code'] == 'too_large'
    assert big.headers['access-control-allow-origin'] == ORIGIN['Origin'] and big.headers['vary'] == 'Origin'
    preflight = api.options(base + '/requesters', headers={**ORIGIN, 'Access-Control-Request-Method': 'PUT'})
    assert preflight.status_code == 400 and preflight.headers['access-control-allow-origin'] == ORIGIN['Origin']
    other = api.options(base + '/requesters', headers={'Origin': 'https://evil.example', 'Access-Control-Request-Method': 'POST'})
    assert other.status_code == 403 and 'access-control-allow-origin' not in other.headers
