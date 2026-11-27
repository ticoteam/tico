"""A task's public text and who may write it (backend/roadmaps.py publication): a bot files none without an approved
publication, internal text never becomes public text, and the public roadmap's pages, settings and visitor routes are
gone (removed with backend/public_roadmaps.py)."""

import json

from backend import roadmaps as R
from backend.tests.helpers.tasks_board import api, get, headers, post  # noqa: F401


def test_visibility_defaults_do_not_rewrite_tasks_and_private_never_publishes(api):
    draft = post(api, 'tasks', {'owner': 'cpo', 'title': 'Research search', 'body': 'Internal draft'})
    assert draft['visibility'] == 'private' and draft['public_title'] == '' and draft['public_summary'] == ''
    refused = post(
        api,
        'tasks',
        {'owner': 'cpo', 'title': 'Research filters', 'body': 'Internal draft', 'visibility': 'public'},
        expected=422,
    )
    assert 'public title' in refused['error']['detail']
    post(api, 'tasks/' + draft['id'], {'version': draft['version'], 'visibility': 'public'}, expected=422)
    # There are no task types to default to public any more: public text alone keeps the task private.
    unasked = post(
        api,
        'tasks',
        {'owner': 'cpo', 'title': 'Research sorting', 'body': 'Internal draft', 'public_title': 'Sorting'},
    )
    assert unasked['visibility'] == 'private'
    task = post(
        api,
        'tasks',
        {
            'owner': 'cpo',
            'title': 'Research search again',
            'body': 'Internal draft',
            'visibility': 'public',
            'public_title': 'Better search',
            'public_summary': 'Find more',
        },
    )
    assert (task['visibility'], task['public_title'], task['public_summary']) == (
        'public',
        'Better search',
        'Find more',
    )
    changed = post(api, 'tasks/' + task['id'], {'version': task['version'], 'title': 'Research search again'})
    assert changed['public_summary'] == 'Find more'
    private = post(api, 'tasks', {'owner': 'cpo', 'title': 'Sensitive research', 'body': 'Secret', 'private': True})
    post(api, 'tasks/' + private['id'], {'version': private['version'], 'visibility': 'public'}, expected=422)
    child = post(
        api,
        'tasks',
        {
            'owner': 'cpo',
            'title': 'Review a part',
            'body': 'Part',
            'visibility': 'public',
            'public_title': 'A part',
            'parent': private['id'],
        },
    )
    assert get(api, 'tasks/' + task['id'])['task']['visibility'] == 'public'
    # Privacy does not spread: under a private parent the child is company-visible, with its own public title.
    assert get(api, 'tasks/' + child['id'])['task']['visibility'] == 'public'


def test_a_bot_has_no_way_to_publish_yet_and_is_told_so(api):
    """Codex review 2 of the asks track, finding 9: the publication preview handed a bot a payload no route could ask
    an OK for. Until the roadmap ships, the preview answers as the public routes do, the generic OK route refuses a
    publish subject (its module asks it, and has no ask path yet), and public text is refused with the same words."""
    from backend.tests.helpers.tasks_board import bot_token

    token = bot_token(api, 'cpo')
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Improve search', 'body': 'Draft'})
    changes = {'visibility': 'public', 'public_title': 'Search', 'public_summary': 'Search improvements'}
    for body in (changes, {'comment': 'Now available'}, {'labels': ['roadmap']}):
        r = api.post('/api/v2/tasks/' + task['id'] + '/publication-preview', json=body, headers=headers(token))
        assert r.status_code == 404 and R.NOT_YET in r.text, r.text
    asked = api.post(
        '/api/v2/tasks/' + task['id'] + '/oks',
        json={
            'to': 'ana',
            'line': 'OK to publish?',
            'subject': R.publication_subject(task['id']),
            'content': R.publication_payload(task['id'], 'text', changes),
            'version': task['version'],
        },
        headers=headers(token),
    )
    assert asked.status_code in (403, 422) and 'publication-preview' not in asked.text, asked.text
    refused = post(api, 'tasks/' + task['id'], {'version': task['version'], **changes}, token=token, expected=422)
    assert 'publication-preview' not in json.dumps(refused)
    with api.app.state.store.read() as c:
        assert c.execute('SELECT count(*) FROM ok_grants').fetchone()[0] == 0


def test_a_bot_files_no_public_text_without_an_approved_publication(api):
    from backend.tests.helpers.tasks_board import bot_token

    token = bot_token(api, 'cpo')
    for identity in (token, 'ben-via-agent-ops'):
        task_body = {'owner': 'cpo', 'title': 'Research requested exports', 'body': 'customer@acme.example'}
        # Public text from a bot is refused, not silently dropped: it goes through an approved publication.
        for extra in ({'visibility': 'public'}, {'public_title': 'Exports'}, {'public_summary': 'Soon'}):
            refused = api.post('/api/v2/tasks', json={**task_body, **extra}, headers=headers(identity))
            assert refused.status_code == 422 and refused.json()['error']['code'] == 'approval', refused.text
        created = post(api, 'tasks', task_body, token=identity)
        assert created['visibility'] == 'private' and created['public_title'] == ''


def test_internal_text_never_becomes_public_text(api):
    secret = 'Acme (jane@acme.example) threatens to churn; contract $40k'
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Keep Acme happy', 'body': secret, 'labels': ['roadmap']})
    assert task['visibility'] == 'private' and task['public_summary'] == ''
    refused = post(api, 'tasks/' + task['id'], {'version': task['version'], 'visibility': 'public'}, expected=422)
    assert 'public title' in refused['error']['detail']
    task = post(
        api,
        'tasks/' + task['id'],
        {'version': task['version'], 'visibility': 'public', 'public_title': 'Faster exports'},
    )
    assert (task['public_title'], task['public_summary']) == ('Faster exports', '')
    # No page shows it: the public roadmap's routes are gone, so its address asks for a sign-in as any other does.
    shown = api.get('/public/roadmaps/roadmap/tasks/' + task['id'])
    assert shown.status_code == 401 and R.NOT_YET not in shown.text
    assert all(text not in shown.text for text in ('jane@', '$40k', 'Keep Acme happy', 'Faster exports'))


def test_the_public_roadmap_its_settings_and_its_visitor_routes_are_gone(api):
    """Task #4095: the public roadmap is removed, not off. Its pages and visitor writes are no routes, its settings
    route is gone, its guard adds no CORS headers, and a fresh install keeps no visitor rate table or address secret."""
    origin = {'Origin': 'https://acme.example'}
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Add exports', 'body': 'Internal'})
    base = '/public/roadmaps/roadmap/tasks/' + task['id']
    # No anonymous route is left there: a visitor is asked to sign in, with no CORS header for their website.
    for r in (
        api.get('/public/roadmaps/roadmap', headers=origin),
        api.post('/public/roadmaps/roadmap/suggest', json={'title': 'Exports'}, headers=origin),
        api.delete(base + '/requesters/' + 'x' * 43, headers=origin),
    ):
        assert r.status_code == 401 and R.NOT_YET not in r.text, (r.request.url, r.status_code, r.text)
        assert 'access-control-allow-origin' not in r.headers
    # Signed in, the same addresses and the settings route are unknown.
    for r in (
        api.get('/public/roadmaps/roadmap', headers=headers()),
        api.get('/api/v2/roadmaps/settings', headers=headers()),
    ):
        assert r.status_code in (404, 405) and R.NOT_YET not in r.text, (r.request.url, r.status_code, r.text)
    refused = api.post('/api/v2/roadmaps/settings', json={'labels': []}, headers=headers())
    assert refused.status_code in (404, 405), refused.text
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='roadmap_actions'").fetchone()
        assert not c.execute("SELECT 1 FROM registry_metadata WHERE key IN ('roadmaps','roadmap_ip_secret')").fetchone()
