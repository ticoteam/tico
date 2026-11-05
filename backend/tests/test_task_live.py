"""Normal task collaboration uses the task read boundary for both places and invalidations."""
from backend import events
from backend.tests.test_api import api, headers, post, restrict  # noqa: F401


def test_task_presence_sessions_expire_and_require_current_task_access(api):
    task = post(api, 'tasks', {'owner': 'human:ben', 'title': 'Review the launch', 'body': 'Review the plan.', 'private': True})
    place = 'task:' + task['id']
    store, auth = api.app.state.store, api.app.state.auth
    with store.read() as c:
        cursor = events.latest(c)
        outsider = auth.identity_for_actor(c, 'human:cara')
        reader = auth.identity_for_actor(c, 'human:ben')
    for session in ('one', 'two'):
        response = api.post('/api/v2/presence', json={'place': place, 'session_id': session}, headers=headers('ben-test'))
        assert response.status_code == 200, response.text
    response = api.post('/api/v2/presence', json={'place': place, 'session_id': 'one', 'gone': True}, headers=headers('ben-test'))
    assert [a['actor'] for a in response.json()['actors']] == ['human:ben']
    assert api.get('/api/v2/presence', params={'place': place}, headers=headers('cara-test')).status_code == 404
    assert api.post('/api/v2/presence', json={'place': place, 'session_id': 'three'}, headers=headers('cara-test')).status_code == 404
    with store.read() as c:
        assert events.read(c, auth, outsider, cursor, topics=('presence',), places=(place,))[0] == []
        sent = events.read(c, auth, reader, cursor, topics=('presence',), places=(place,))[0]
        assert sent and all(data == {'place': place} for _, _, data in sent)
    with store.transaction() as c:
        c.execute("UPDATE presences SET seen_at='2000-01-01T00:00:00Z' WHERE place=?", (place,))
    assert api.get('/api/v2/presence', params={'place': place}, headers=headers('ben-test')).json() == {'actors': []}


def test_task_place_stops_disclosing_presence_after_bot_access_is_revoked(api):
    task = post(api, 'tasks', {'owner': 'bot:ops', 'title': 'Review the plan', 'body': 'Review the launch plan.', 'private': False})
    place = 'task:' + task['id']
    store, auth = api.app.state.store, api.app.state.auth
    with store.read() as c:
        viewer = auth.identity_for_actor(c, 'human:cara')
        cursor = events.latest(c)
    assert api.post('/api/v2/presence', json={'place': place, 'session_id': 'reader'}, headers=headers('cara-test')).status_code == 200
    clock = [0]
    watch = events.Watch(store, auth, viewer, tasks=[task['id']], clock=lambda: clock[0])
    watch()
    with store.transaction() as c:
        restrict(c, 'ops', people=['ana'])
    # No task write is needed: the subscribed task is reauthorized on a keepalive.
    clock[0] = 60
    watch()
    assert watch.drain() == [('tasks', {'task_id': task['id'], 'gone': True})]
    clock[0] = 120
    watch()
    assert watch.drain() == []
    assert api.get('/api/v2/presence', params={'place': place}, headers=headers('cara-test')).status_code == 404
    assert api.post('/api/v2/presence', json={'place': place, 'session_id': 'reader'}, headers=headers('cara-test')).status_code == 404
    with store.read() as c:
        assert events.read(c, auth, viewer, cursor, topics=('presence',), places=(place,))[0] == []


def test_concurrent_task_field_changes_refuse_stale_version_and_keep_other_fields(api):
    task = post(api, 'tasks', {'owner': 'human:ben', 'title': 'Review the launch', 'body': 'Review the plan.'})
    path = '/api/v2/tasks/' + task['id']
    saved = api.post(path, json={'version': task['version'], 'body': 'Review the updated launch plan.'}, headers=headers('ben-test'))
    assert saved.status_code == 200, saved.text
    stale = api.post(path, json={'version': task['version'], 'title': 'Review the rollout'}, headers=headers())
    assert stale.status_code == 409
    current = api.get(path, headers=headers()).json()
    retry = api.post(path, json={'version': current['task']['version'], 'title': 'Review the rollout'}, headers=headers())
    assert retry.status_code == 200, retry.text
    assert retry.json()['task']['body'] == 'Review the updated launch plan.'
    detail = api.get(path, headers=headers()).json()
    assert [(e['field'], e['actor']) for e in detail['events'] if e['field'] in ('title', 'body')][-2:] == [
        ('body', 'human:ben'), ('title', 'human:ana')]
