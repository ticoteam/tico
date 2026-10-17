"""Roadmap privacy boundaries and the existing parent/child flow."""

import pytest

from backend import hubdb as H
from backend.tests.test_tasks_board import api, get, headers, post


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


def _dev_tickets(api):
    """A Dev tickets type shaped like the software factory's: Done ships, Closed and Won't do do not."""
    from backend import roadmap_flow as F
    with api.app.state.store.transaction() as c:
        dev = H.type_create(c, 'human:ana', 'Dev tickets', steps=[
            {'name': 'On Deck', 'status': 'open'}, {'name': 'In Progress', 'status': 'doing'},
            {'name': 'Done', 'status': 'done'}, {'name': 'Closed', 'status': 'closed'},
            {'name': "Won't do", 'status': 'declined'}])
        F.ensure(c)     # hands the type to engineering-lead, as on a server start
    return dev


def _building(api, dev, title, tickets=2):
    item = post(api, 'tasks', {'owner': 'cpo', 'title': title, 'body': 'Spec.', 'type': 'Product roadmap',
                               'step': 'Approved'})
    children = [post(api, 'tasks', {'owner': 'ops', 'title': f'{title} part {n}', 'body': 'Build it.',
                                    'type': dev['id'], 'relations': [{'task': item['id'], 'kind': 'parent'}]})
                for n in range(tickets)]
    assert get(api, 'tasks/' + item['id'])['task']['step']['name'] == 'Building'
    return item, children


def _step(api, task, step):
    return post(api, 'tasks/' + task['id'], {'version': get(api, 'tasks/' + task['id'])['task']['version'],
                                             'step': step})


def _roadmap_step(api, item):
    return get(api, 'tasks/' + item['id'])['task']['step']['name']


def test_a_dev_ticket_closed_without_shipping_does_not_ship_the_roadmap_item(api):
    dev = _dev_tickets(api)
    item, (done, dropped) = _building(api, dev, 'Exports')
    _step(api, done, 'Done')
    assert _roadmap_step(api, item) == 'Building'
    _step(api, dropped, 'Closed')
    assert _roadmap_step(api, item) == 'Shipped'

    item, children = _building(api, dev, 'Imports')
    for child in children:
        _step(api, child, 'Closed')
    assert _roadmap_step(api, item) == 'Approved'
    assert get(api, 'tasks/' + item['id'])['task']['done_at'] is None

    item, (closed, declined) = _building(api, dev, 'Sharing')
    _step(api, closed, 'Closed')
    _step(api, declined, "Won't do")
    assert _roadmap_step(api, item) == 'Approved'


def test_a_shipped_dev_ticket_its_requester_closes_still_counts_as_shipped(api):
    dev = _dev_tickets(api)
    item, children = _building(api, dev, 'Reports')
    for child in children:
        _step(api, child, 'Done')
    assert _roadmap_step(api, item) == 'Shipped'
    for child in children:
        # Accepting finished work closes it, which lands a Dev ticket on its Closed step.
        closed = post(api, 'tasks/' + child['id'], {'version': get(api, 'tasks/' + child['id'])['task']['version'],
                                                    'close': True})
        assert closed['step']['name'] == 'Closed' and closed['status'] == 'closed'
    assert _roadmap_step(api, item) == 'Shipped'

    item, (child,) = _building(api, dev, 'Reopened', tickets=1)
    _step(api, child, 'Done')
    _step(api, child, 'In Progress')
    assert _roadmap_step(api, item) == 'Building'
    _step(api, child, 'Closed')
    assert _roadmap_step(api, item) == 'Approved'

    item, (child,) = _building(api, dev, 'Dropped after Done', tickets=1)
    _step(api, child, 'Done')
    _step(api, child, "Won't do")
    assert _roadmap_step(api, item) == 'Approved'
    closed = post(api, 'tasks/' + child['id'], {'version': get(api, 'tasks/' + child['id'])['task']['version'],
                                                'close': True})
    assert closed['step']['name'] == 'Closed'
    assert _roadmap_step(api, item) == 'Approved'


def test_a_default_engineering_ticket_at_done_closed_ships_the_roadmap_item(api):
    item = post(api, 'tasks', {'owner': 'cpo', 'title': 'Search', 'body': 'Spec.', 'type': 'Product roadmap',
                               'step': 'Approved'})
    child = post(api, 'tasks', {'owner': 'ops', 'title': 'Build search', 'body': 'Build it.',
                                'type': 'Engineering', 'relations': [{'task': item['id'], 'kind': 'parent'}]})
    assert _roadmap_step(api, item) == 'Building'
    child = _step(api, child, 'Done')
    assert child['status'] == 'closed'
    assert _roadmap_step(api, item) == 'Shipped'


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
        task_body = {'owner': 'cpo', 'title': 'Research requested exports', 'body': 'customer@acme.example',
                     'type': 'Product roadmap', 'labels': ['roadmap']}
        # Public text from a bot is refused, not silently dropped: it goes through an approved publication.
        for extra in ({'visibility': 'public'}, {'public_title': 'Exports'}, {'public_summary': 'Soon'}):
            refused = api.post('/api/v2/tasks', json={**task_body, **extra}, headers=headers(identity))
            assert refused.status_code == 422 and refused.json()['error']['code'] == 'approval', refused.text
        created = post(api, 'tasks', task_body, token=identity)
        assert created['visibility'] == 'private' and created['public_title'] == ''
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


def test_roadmap_types_go_by_role_after_their_managers_change(api):
    from backend import roadmap_flow as F
    public_setup(api)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE task_types SET manager='ops' WHERE id IN ('product-roadmap','engineering')")
        c.execute("UPDATE bot_config SET config_json=json_set(config_json,'$.template','product-manager') WHERE bot='cpo'")
        F.ensure(c)     # as at a restart: a manager someone chose stays
        managers = "SELECT DISTINCT manager FROM task_types WHERE id IN ('product-roadmap','engineering')"
        assert [r[0] for r in c.execute(managers)] == ['ops']
    # A Dev tickets type made after the upgrade, beside Engineering, is a development flow too.
    dev = post(api, 'task-types', {'name': 'Dev tickets', 'steps': [{'name': 'On Deck', 'status': 'open'},
                                                                    {'name': 'Done', 'status': 'done'}]})['type']
    assert (dev['role'], dev['manager']) == ('engineering', None)
    roadmap = post(api, 'tasks', {'owner': 'cpo', 'title': 'Improve imports', 'body': 'Research imports.',
                                 'type': 'Product roadmap', 'step': 'Approved'})
    child = post(api, 'tasks', {'owner': 'ops', 'title': 'Build imports', 'body': 'Implement imports.',
                               'type': dev['id'], 'relations': [{'task': roadmap['id'], 'kind': 'parent'}]})
    assert get(api, 'tasks/' + roadmap['id'])['task']['step']['name'] == 'Building'
    post(api, 'tasks/' + child['id'], {'version': child['version'], 'status': 'done'})
    assert get(api, 'tasks/' + roadmap['id'])['task']['step']['name'] == 'Shipped'
    assert roadmap['id'] in [t['id'] for t in get(api, 'roadmap-tasks')['tasks']]
    suggested = api.post('/public/roadmaps/roadmap/suggest', json={'title': 'New idea', 'summary': 'Details'}, headers=ORIGIN)
    assert suggested.status_code == 200, suggested.text
    task = get(api, 'tasks/' + suggested.json()['id'])['task']
    assert (task['type']['id'], task['step']['name']) == ('product-roadmap', 'Suggested')


def test_role_migration_survives_a_stopped_start_and_backfills_renamed_types(tmp_path):
    from backend import roadmap_flow as F
    path = tmp_path / 'hub.db'
    c = H.connect(path)
    H.sync_registry(c, {'cmo': {'name': 'cmo', 'runtime': 'fake', 'status': 'active'}},
                    {'people': [{'id': 'ana', 'email': 'ana@acme.example'}]})
    # Today's shape after renames: generated ids and names no fallback recognises; only the legacy managers tell.
    c.execute("DELETE FROM task_steps WHERE type_id IN ('product-roadmap','engineering')")
    c.execute("DELETE FROM task_types WHERE id IN ('product-roadmap','engineering')")
    made = {name: H.type_create(c, 'human:ana', name, steps=[{'name': 'Todo', 'status': 'open'}], mover=True)['id']
            for name in ('Ideas', 'Builds', 'Platform work', 'Errands')}
    for type_id in made.values():
        H.task_create(c, 'human:ana', 'Plan the launch on ' + type_id, 'Details', 'bot:cmo', type=type_id, lint=False)
    c.execute('DROP INDEX task_types_product_role')
    c.execute('ALTER TABLE task_types DROP COLUMN role')
    for name, manager in (('Ideas', 'product-manager'), ('Builds', 'engineering-lead'),
                          ('Platform work', 'engineering-lead')):
        c.execute('UPDATE task_types SET manager=? WHERE id=?', (manager, made[name]))
    tasks = c.execute('SELECT * FROM tasks ORDER BY id').fetchall()
    managers = dict(c.execute('SELECT id,manager FROM task_types').fetchall())

    class StopsMidBackfill:
        def __init__(self, conn):
            self.conn = conn

        def execute(self, sql, *args):
            if sql.startswith('UPDATE task_types SET role'):
                raise KeyboardInterrupt('the start is stopped here')
            return self.conn.execute(sql, *args)

    with pytest.raises(KeyboardInterrupt):
        F.migrate(StopsMidBackfill(c))
    assert 'role' not in {r[1] for r in c.execute('PRAGMA table_info(task_types)')}
    c.close()
    c = H.connect(path)
    roles = {made[name]: role for name, role in (('Ideas', 'product-roadmap'), ('Builds', 'engineering'),
                                                 ('Platform work', 'engineering'), ('Errands', None))}
    assert {k: v for k, v in c.execute('SELECT id,role FROM task_types') if k in roles} == roles
    assert dict(c.execute('SELECT id,manager FROM task_types').fetchall()) == managers
    assert [tuple(r) for r in c.execute('SELECT * FROM tasks ORDER BY id')] == [tuple(r) for r in tasks]
    H.migrate(c)
    assert dict(c.execute('SELECT id,role FROM task_types WHERE role IS NOT NULL').fetchall()) == {
        k: v for k, v in roles.items() if v}
    c.close()


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


def test_first_version_copies_edited_since_or_published_at_creation_are_cleared(api):
    from backend import roadmaps as R
    edited = post(api, 'tasks', {'owner': 'cpo', 'title': 'Old title', 'body': 'Old notes', 'type': 'Product roadmap'})
    copied = post(api, 'tasks', {'owner': 'cpo', 'title': 'Acme churn', 'body': 'jane@acme.example', 'type': 'Product roadmap'})
    draft = post(api, 'tasks', {'owner': 'cpo', 'title': 'Draft', 'body': 'Internal', 'type': 'Product roadmap'})
    draft = post(api, 'tasks/' + draft['id'], {'version': draft['version'], 'public_title': 'Written draft'})
    shown = post(api, 'tasks', {'owner': 'cpo', 'title': 'Shown idea', 'body': 'Shown notes', 'type': 'Product roadmap',
                               'visibility': 'public', 'public_title': 'Shown idea', 'public_summary': 'Shown notes'})
    kept = post(api, 'tasks', {'owner': 'cpo', 'title': 'Kept internal', 'body': 'Kept internal notes',
                               'type': 'Product roadmap'})
    with api.app.state.store.transaction() as c:
        # A fresh database is marked on its first start, so its second start clears nothing.
        assert c.execute("SELECT 1 FROM registry_metadata WHERE key='roadmaps_copies_cleared_by_field'").fetchone()
        # What the first version left: a copy made before the title changed, and a task public at creation.
        c.execute("UPDATE tasks SET public_title='Old title',public_summary='Old notes',title='New title' WHERE id=?",
                  (edited['id'],))
        c.execute("UPDATE tasks SET visibility='public',public_title=title,public_summary=body WHERE id=?", (copied['id'],))
        # Public text with no event that differs from the internal text is not a copy, so a public task keeps it.
        c.execute("UPDATE tasks SET visibility='public',public_title='Kept public',public_summary='Kept summary' WHERE id=?",
                  (kept['id'],))
        c.execute("DELETE FROM registry_metadata WHERE key='roadmaps_copies_cleared_by_field'")
        R.migrate(c)
        state = lambda tid: tuple(c.execute('SELECT visibility,public_title,public_summary FROM tasks WHERE id=?',
                                            (tid,)).fetchone())
        assert state(edited['id']) == ('private', '', '')
        assert state(copied['id']) == ('private', '', '')
        assert state(draft['id']) == ('private', 'Written draft', '')              # written public text stays
        assert state(shown['id']) == ('public', 'Shown idea', 'Shown notes')       # written at creation, so it stays
        assert state(kept['id']) == ('public', 'Kept public', 'Kept summary')      # only an exact copy goes
        note = c.execute("SELECT note FROM task_events WHERE task_id=? AND field='visibility'", (copied['id'],)).fetchone()
        assert 'copy of the internal title' in note[0]
        cleared = c.execute("SELECT field,old,new,note FROM task_events WHERE task_id=? AND note LIKE 'Cleared:%' "
                            "ORDER BY field", (edited['id'],)).fetchall()
        assert [tuple(r[:3]) for r in cleared] == [('public_summary', 'Old notes', ''), ('public_title', 'Old title', '')]
        c.execute("UPDATE tasks SET public_title='Old title' WHERE id=?", (edited['id'],))
        R.migrate(c)                                                                # once only
        assert state(edited['id']) == ('private', 'Old title', '')
    assert api.get('/public/roadmaps/roadmap/tasks/' + copied['id']).status_code == 404


def test_first_version_copies_are_cleared_per_field_when_only_the_public_title_was_rewritten(api):
    from backend import roadmaps as R
    shown = post(api, 'tasks', {'owner': 'cpo', 'title': 'D', 'body': 'secret internal body', 'type': 'Product roadmap'})
    hidden = post(api, 'tasks', {'owner': 'cpo', 'title': 'E', 'body': 'other internal body', 'type': 'Product roadmap'})
    retitled = post(api, 'tasks', {'owner': 'cpo', 'title': 'F', 'body': 'Written summary', 'type': 'Product roadmap'})
    with api.app.state.store.transaction() as c:
        # The first version made each a copy at creation; then the owner rewrote only one field through publication().
        c.execute("UPDATE tasks SET public_title=title,public_summary=body WHERE id IN (?,?,?)",
                  (shown['id'], hidden['id'], retitled['id']))
        c.execute("UPDATE tasks SET visibility='public' WHERE id IN (?,?)", (shown['id'], retitled['id']))
    shown = get(api, 'tasks/' + shown['id'])['task']
    post(api, 'tasks/' + shown['id'], {'version': shown['version'], 'public_title': 'D written'})
    hidden = get(api, 'tasks/' + hidden['id'])['task']
    post(api, 'tasks/' + hidden['id'], {'version': hidden['version'], 'public_title': 'E written'})
    retitled = get(api, 'tasks/' + retitled['id'])['task']
    post(api, 'tasks/' + retitled['id'], {'version': retitled['version'], 'public_summary': 'Written summary, public'})
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM registry_metadata WHERE key='roadmaps_copies_cleared_by_field'")
        R.migrate(c)
        state = lambda tid: tuple(c.execute('SELECT visibility,public_title,public_summary FROM tasks WHERE id=?',
                                            (tid,)).fetchone())
        assert state(shown['id']) == ('public', 'D written', '')              # the written title keeps it public
        assert state(hidden['id']) == ('private', 'E written', '')
        assert state(retitled['id']) == ('private', '', 'Written summary, public')
        # Each clearing leaves its event, so it can be put back.
        assert tuple(c.execute("SELECT old,new FROM task_events WHERE task_id=? AND field='public_summary' "
                               "AND note LIKE 'Cleared:%'", (shown['id'],)).fetchone()) == ('secret internal body', '')
        assert c.execute("SELECT 1 FROM task_events WHERE task_id=? AND field='visibility' AND note LIKE 'Unpublished:%'",
                         (retitled['id'],)).fetchone()


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
    # A purged trash row is past withdrawing from: it is neither read nor written.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE task_trash SET purged_at='2026-01-01T00:00:00Z' WHERE task_id=?", (trashed['id'],))
    assert api.delete(path + '/requesters/' + staying, headers=ORIGIN).status_code == 404
    with api.app.state.store.transaction() as c:
        assert c.execute('SELECT rows_json FROM task_trash WHERE task_id=?', (trashed['id'],)).fetchone()[0] == snapshot
        c.execute('UPDATE task_trash SET purged_at=NULL WHERE task_id=?', (trashed['id'],))
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
    assert suggest({'title': 'Webhooks'}).status_code == 429
    with api.app.state.store.read() as c:
        assert c.execute('SELECT count(*) FROM tasks WHERE requester=?', (H.KEEPER,)).fetchone()[0] == 2
        # The Product Manager hears once a day that the form is refusing, not once per refusal.
        told = c.execute("SELECT body FROM messages WHERE to_actor='bot:cpo' AND kind='notice' "
                         "AND body LIKE '%refusing new suggestions%'").fetchall()
    assert len(told) == 1 and '2 are waiting' in told[0][0] and 'limit is 2' in told[0][0]
    waiting = get(api, 'tasks/' + first.json()['id'])['task']
    post(api, 'tasks/' + waiting['id'], {'version': waiting['version'], 'step': 'Exploring'})
    third = suggest({'title': 'Webhooks'})
    assert third.status_code == 200
    assert suggest({'title': 'Audit log'}).status_code == 429
    with api.app.state.store.transaction() as c:   # a declined suggestion still at Suggested no longer waits
        c.execute("UPDATE tasks SET status='declined' WHERE id=?", (third.json()['id'],))
    assert suggest({'title': 'Audit log'}).status_code == 200
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
