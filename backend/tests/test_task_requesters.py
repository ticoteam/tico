"""Who asked for a task (backend/roadmaps.py): its requesters, who may read and change them, and the signed-in Roadmap
that ranks the Product pipeline's tasks by who asked for them."""

import json

from backend import roadmaps as R
from backend.tests.helpers.tasks_board import api, get, headers, post  # noqa: F401


def test_requester_contacts_follow_task_privacy_and_teammates_can_remove_themselves(api):
    private = post(
        api, 'tasks', {'owner': 'cpo', 'title': 'Review private feedback', 'body': 'Sensitive', 'private': True}
    )
    post(api, 'tasks/' + private['id'] + '/requesters', {'email': 'sensitive@acme.example'})
    get(api, 'tasks/' + private['id'] + '/requesters', token='ben-test', expected=404)
    result = post(api, 'sql', {'sql': 'SELECT * FROM task_requesters'}, token='ben-test')
    assert 'sensitive@acme.example' not in str(result)
    result = post(api, 'sql', {'sql': 'SELECT * FROM task_requesters'})
    assert 'token_hash' not in str(result)
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Improve editing', 'body': 'Feedback'})
    external = post(
        api,
        'tasks/' + task['id'] + '/requesters',
        {'email': 'customer@acme.example', 'name': 'Customer', 'source': 'meeting'},
    )
    from backend.tests.helpers.mcp import call

    failed, result = call(
        api, 'tico_task_requester_save', {'id': task['id'], 'requester_id': external['id'], 'important': True}
    )
    assert not failed, result
    changed = get(api, 'tasks/' + task['id'] + '/requesters')['requesters'][0]
    assert changed['important'] and changed['email'] == 'customer@acme.example'
    assert changed['name'] == 'Customer' and changed['source'] == 'meeting'
    post(api, 'tasks/' + task['id'] + '/requesters/' + external['id'], {}, expected=422)
    post(api, 'tasks/' + task['id'] + '/requesters/' + external['id'] + '/delete', {})
    sized = post(api, 'tasks/' + task['id'], {'version': task['version'], 'size': 7.5})
    assert get(api, 'tasks/' + task['id'])['task']['size'] == 7.5
    post(api, 'tasks/' + task['id'], {'version': sized['version'], 'size': None})
    assert get(api, 'tasks/' + task['id'])['task']['size'] is None
    own = post(api, 'tasks/' + task['id'] + '/requesters', {'actor': 'human:priya'}, token='priya-test')
    with api.app.state.store.read() as c:
        assert not c.execute(
            'SELECT 1 FROM idempotency WHERE instr(response,?)>0', (own['requester_token'],)
        ).fetchone()
    post(api, 'tasks/' + task['id'] + '/requesters/' + own['id'] + '/delete', {}, token='priya-test')
    assert get(api, 'tasks/' + task['id'] + '/requesters')['requester_count'] == 0


def _product(api):
    """The company's Product pipeline, as a company that builds software gets it."""
    from backend import pipeline_settings as PS

    with api.app.state.store.transaction() as c:
        if not PS._made_from(c, 'engineering'):
            PS.add_preset(c, PS.SYSTEM, 'engineering')
        return PS.add_preset(c, PS.SYSTEM, 'product')['id']


def _ranked(api, query='', token='ana-test'):
    return get(api, 'roadmap-tasks' + query, token=token)


def test_the_roadmap_ranks_the_product_pipelines_tasks_by_who_asked(api):
    missing = api.get('/api/v2/roadmap-tasks', headers=headers())
    assert missing.status_code == 404 and missing.json()['error']['detail'] == R.NO_PRODUCT
    product = _product(api)
    made = {
        title: post(api, 'tasks', {'owner': 'cpo', 'title': title, 'pipeline': product})
        for title in ('Add exports', 'Add imports', 'Add filters')
    }
    post(api, 'tasks', {'owner': 'cpo', 'title': 'Plan the offsite'})  # General: not on the roadmap
    exports, imports, filters = (made[t]['id'] for t in ('Add exports', 'Add imports', 'Add filters'))
    post(api, f'tasks/{exports}/requesters', {'email': 'a@acme.example', 'organization': 'Globex', 'important': True})
    post(api, f'tasks/{exports}/requesters', {'email': 'b@acme.example'})
    post(api, f'tasks/{imports}/requesters', {'actor': 'human:priya'}, token='priya-test')
    post(api, f'tasks/{filters}', {'version': made['Add filters']['version'], 'size': 3})

    rows = _ranked(api)['tasks']
    assert [r['id'] for r in rows] == [exports, imports, filters]
    first = rows[0]
    assert (first['pipeline_id'], first['stage_name'], first['status']) == (product, 'Ideas', 'open')
    assert {k: first[k] for k in R.EMPTY_STATS} == {
        'requester_count': 2,
        'external_requesters': 2,
        'internal_requesters': 0,
        'important_requesters': 1,
        'organization': 'Globex',
    }
    assert rows[1]['internal_requesters'] == 1 and rows[2]['requester_count'] == 0
    assert [r['id'] for r in _ranked(api, '?direction=asc')['tasks']] == [filters, imports, exports]
    # A task with no organization or size comes last whichever way it sorts.
    for direction in ('asc', 'desc'):
        assert _ranked(api, '?sort=organization&direction=' + direction)['tasks'][0]['id'] == exports
        assert _ranked(api, '?sort=size&direction=' + direction)['tasks'][0]['id'] == filters
    one = _ranked(api, '?limit=1')
    assert [r['id'] for r in one['tasks']] == [exports] and one['next_offset'] == 1
    last = _ranked(api, '?limit=2&offset=2')
    assert [r['id'] for r in last['tasks']] == [filters] and last['next_offset'] is None
    for bad in ('?sort=votes', '?direction=up'):
        r = api.get('/api/v2/roadmap-tasks' + bad, headers=headers())
        assert r.status_code == 422 and r.json()['error']['code'] == 'sort', r.text


def test_the_roadmap_shows_a_reader_only_what_they_may_read_and_only_editors_publish(api):
    product = _product(api)
    shared = post(api, 'tasks', {'owner': 'cpo', 'title': 'Add exports', 'pipeline': product})
    secret = post(api, 'tasks', {'owner': 'cpo', 'title': 'Add payroll', 'pipeline': product, 'private': True})
    secret = secret.get('task', secret)  # the task, or the task beside a warning when its first stage gives one
    post(api, f"tasks/{secret['id']}/requesters", {'email': 'cfo@acme.example', 'organization': 'Initech'})
    assert {r['id'] for r in _ranked(api)['tasks']} == {shared['id'], secret['id']}
    seen = _ranked(api, token='priya-test')
    assert [r['id'] for r in seen['tasks']] == [shared['id']]
    assert 'Initech' not in json.dumps(seen) and 'payroll' not in json.dumps(seen)

    # Public text and requesters are set by those who may change the task, never by a reader alone.
    public = {'version': shared['version'], 'visibility': 'public', 'public_title': 'Exports'}
    post(api, 'tasks/' + shared['id'], public, token='priya-test', expected=403)
    post(api, f"tasks/{shared['id']}/requesters", {'email': 'x@acme.example'}, token='priya-test', expected=403)
    assert _ranked(api, token='priya-test')['tasks'][0]['visibility'] == 'private'
    post(api, 'tasks/' + shared['id'], public)
    row = _ranked(api, token='priya-test')['tasks'][0]
    assert (row['visibility'], row['public_title']) == ('public', 'Exports')
