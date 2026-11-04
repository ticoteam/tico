"""Shared editing, current authorization and per-window presence."""
import json
from concurrent.futures import ThreadPoolExecutor

from backend import events, meeting_items
from backend.tests.test_api import api, headers, get, restrict, unrelated_task_managers  # noqa: F401
from backend.tests.test_meeting_items import record, add, change, push, items


def test_reader_merges_edits_and_send_applies_final_fields_once(api):
    rid = record(api)
    item = add(api, rid, {'section': 'task', 'text': 'Draft',
                         'detail': {'owner': 'human:ben', 'priority': 'p1'}})['item']['id']
    assert items(api, rid, 'cara-test')['can_send']
    change(api, rid, item, {'detail': {'body': 'The details'}}, 'ben-test')
    edited = change(api, rid, item, {'detail': {'due': '2026-12-01T00:00:00Z', 'priority': None}}, 'cara-test')['item']
    assert edited['detail'] == {'owner': 'human:ben', 'body': 'The details', 'due': '2026-12-01T00:00:00Z'}
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda token: push(api, rid, item, {'text': 'Final words', 'detail': {'body': 'Final details'}}, token),
                                ['ben-test', 'cara-test']))
    assert sorted(r['pushed'] for r in results) == [False, True]
    tid = results[0]['item']['result_ref']
    task = get(api, 'tasks/' + tid)['task']
    assert task['title'] == 'Final words' and 'Final details' in task['body']
    change(api, rid, item, {'text': 'Too late'}, expected=409)
    change(api, rid, item, {'detail': {'owner': 'human:cara'}}, expected=409)
    change(api, rid, item, {'status': 'dismissed'}, expected=409)


def test_private_audience_and_participant_read_access(api):
    rid = record(api, {'private': True, 'participants': ['ana@acme.example', 'ben@acme.example']})
    item = add(api, rid, {'section': 'task', 'text': 'Private plan', 'detail': {'owner': 'human:cara'}})['item']['id']
    denied = push(api, rid, item, expected=409)
    assert denied['error']['detail'] == "Someone who would see this isn't in the meeting"
    assert items(api, rid)['sections']['task'][0]['status'] == 'proposed'
    assert items(api, rid)['audience'] == ['human:ana', 'human:ben']
    sent = push(api, rid, item, {'detail': {'owner': 'human:ana'}})['item']
    assert get(api, 'tasks/' + sent['result_ref'], 'ben-test')['task']['private']
    with api.app.state.store.read() as c:
        assert c.execute("SELECT actor FROM task_roles WHERE task_id=? AND role='participant' ORDER BY actor",
                         (sent['result_ref'],)).fetchall()


def test_redacts_mutation_replay_listing_and_outbox(api):
    rid = record(api)
    iid = add(api, rid, {'section': 'task', 'text': 'Secret mail', 'detail': {'owner': 'bot:inbox'}})['item']['id']
    sent = push(api, rid, iid)['item']
    assert sent['result_ref']
    assert items(api, rid, 'cara-test')['sections']['task'][0]['result_ref'] is None
    replay = push(api, rid, iid, token='cara-test', key='replay')
    assert replay['item']['result_ref'] is None and replay['pushed'] is False
    assert push(api, rid, iid, token='cara-test', key='replay')['item']['result_ref'] is None
    outbox = api.get('/api/meetings/' + rid, headers=headers('cara-test')).json()['outbox']
    assert outbox['task'][0]['result_ref'] is None
    with api.app.state.store.read() as c:
        auth = api.app.state.auth
        who = auth.identity_for_actor(c, 'human:cara')
        assert meeting_items.feature_duplicate(c, 'Secret mail', who, auth) is None


def test_presence_sessions_expire_independently_and_places_are_authorized(api):
    rid = record(api, {'private': True})
    place = 'meeting:' + rid
    def beat(session, **extra):
        r = api.post('/api/v2/presence', json={'place': place, 'session_id': session, **extra}, headers=headers())
        assert r.status_code == 200, r.text
        return r.json()
    beat('one'); beat('two')
    assert len(beat('one', gone=True)['actors']) == 1
    assert len(api.get('/api/v2/presence', params={'place': place}, headers=headers()).json()['actors']) == 1
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE presences SET seen_at='2000-01-01T00:00:00Z'")
    assert api.get('/api/v2/presence', params={'place': place}, headers=headers()).json() == {'actors': []}
    assert api.get('/api/v2/presence', params={'place': place}, headers=headers('cara-test')).status_code == 404


def test_stream_filters_and_one_gone_on_access_change(api):
    store, auth = api.app.state.store, api.app.state.auth
    rid, other = record(api), record(api)
    with store.read() as c:
        who = auth.identity_for_actor(c, 'human:cara')
        start = events.latest(c)
    watch = events.Watch(store, auth, who, [rid])
    watch()
    add(api, rid, {'section': 'task', 'text': 'For this meeting'})
    add(api, other, {'section': 'task', 'text': 'Other meeting'})
    api.post('/api/v2/presence', json={'place': 'meeting:' + rid, 'session_id': 'one'}, headers=headers())
    with store.read() as c:
        result = events.read(c, auth, who, start, topics=('meeting_items', 'presence'), meetings=(rid,),
                             places=('meeting:' + rid,), watch=watch)
        assert {data.get('meeting_id') or data.get('place') for _, _, data in result[0]} == {rid, 'meeting:' + rid}
        # The cached tail and SQL path must select the same filters.
        snap = events._Snapshot(c)
        assert [r['seq'] for r in snap.changes(who, start, 100, ('meeting_items',), (), (), (), (), (), (rid,))] == [
            r['seq'] for r in snap._read_changes(who, start, 100, ('meeting_items',), (), (), (), (), (), (rid,))]
    with store.transaction() as c:
        c.execute("UPDATE meetings SET metadata=json_set(metadata,'$.private',json('true')) WHERE id=?", (rid,))
    with store.read() as c:
        events.read(c, auth, who, result[1], topics=('meeting_items',), meetings=(rid,), watch=watch)
    assert watch.drain() == [('meeting_items', {'meeting_id': rid, 'gone': True})]
    watch.checked_at = None
    watch()
    assert watch.drain() == []


def test_watch_notices_bot_access_revoked_without_task_change(api):
    from backend.tests.test_api import as_member
    as_member(api, 'ben@acme.example')
    task = api.post('/api/v2/tasks', json={'title': 'Ops followup', 'body': 'Follow up', 'owner': 'bot:ops', 'private': False},
                    headers=headers()).json()['task']['id']
    store, auth = api.app.state.store, api.app.state.auth
    with store.read() as c:
        who = auth.identity_for_actor(c, 'human:cara')
    clock = [0]
    watch = events.Watch(store, auth, who, tasks=[task], clock=lambda: clock[0])
    watch()
    assert watch.drain() == []
    with store.transaction() as c:
        restrict(c, 'ops', people=['ana'])
    clock[0] = 60
    watch()
    assert watch.drain() == [('tasks', {'task_id': task, 'gone': True})]
    clock[0] = 120
    watch()
    assert watch.drain() == []


def test_before_save_checks_private_live_audience_and_finalize_preserves_access(api):
    from backend.tests.test_api import post
    mid = post(api, 'live-meetings', {'title': 'Private live', 'visibility': 'private'})['id']
    post(api, f'live-meetings/{mid}/access', {'add': ['human:ben']})
    body = {'title': 'Private words', 'body': 'Transcript around it', 'owner': 'human:cara', 'live_meeting_id': mid}
    denied = api.post('/api/v2/tasks', json=body, headers=headers())
    assert denied.status_code == 409, denied.text
    task = post(api, 'tasks', {**body, 'owner': 'human:ana'})
    assert task['private']
    assert get(api, 'tasks/' + task['id'], 'ben-test')['task']['id'] == task['id']
    post(api, f'live-meetings/{mid}/chunks', {'chunks': [{'seq': 1, 'speaker': 'Ana', 'start_ms': 0, 'end_ms': 1000, 'text': 'Private words'}]})
    api.app.state.live_meeting_routing.settle(timeout=30)
    post(api, f'live-meetings/{mid}/control', {'action': 'end'})
    api.app.state.live_meeting_routing.settle(timeout=30)
    rid = post(api, f'live-meetings/{mid}/finalize', {})['meeting_id']
    post(api, f'meetings/{rid}/review', {'action': 'approve', 'private': True})
    assert api.get('/api/meetings/' + rid, headers=headers('ben-test')).status_code == 200
    assert api.get('/api/meetings/' + rid, headers=headers('cara-test')).status_code == 403
    team = post(api, 'live-meetings', {'title': 'Team live', 'visibility': 'team'})['id']
    public = post(api, 'tasks', {**body, 'title': 'Team words', 'live_meeting_id': team})
    assert get(api, 'tasks/' + public['id'], 'cara-test')['task']['id'] == public['id']
    post(api, f'live-meetings/{team}/access', {'visibility': 'private'})
    assert get(api, 'tasks/' + public['id'], 'cara-test')['task']['id'] == public['id']


def test_doc_feature_and_bot_manager_are_inside_the_audience(api):
    from backend.tests.test_api import post
    mid = post(api, 'live-meetings', {'title': 'Private live', 'visibility': 'private'})['id']
    post(api, f'live-meetings/{mid}/bots', {'bots': ['ops']})
    with api.app.state.store.transaction() as c:
        unrelated_task_managers(c, 'ops')
    denied = api.post('/api/v2/tasks', json={'title': 'Bot words', 'body': 'Private transcript', 'owner': 'bot:ops',
                                          'live_meeting_id': mid}, headers=headers())
    assert denied.status_code == 409, denied.text
    rid = record(api, {'private': True}, 'ben-test')
    for section in ('doc', 'feature'):
        iid = add(api, rid, {'section': section, 'text': 'Private outcome'}, 'ben-test')['item']['id']
        # The doc bot is not attached; the feature's default owner (Ana) is the org owner and can read this meeting.
        result = push(api, rid, iid, token='ben-test', expected=409 if section == 'doc' else 200)
        if section == 'feature':
            assert get(api, 'tasks/' + result['item']['result_ref'], 'ben-test')['task']['private']


def test_task_access_invalidates_items_and_deleted_meeting_emits_one_gone(api):
    rid = record(api)
    iid = add(api, rid, {'section': 'task', 'text': 'Team followup', 'detail': {'owner': 'human:ben'}})['item']['id']
    tid = push(api, rid, iid)['item']['result_ref']
    store, auth = api.app.state.store, api.app.state.auth
    with store.read() as c:
        who = auth.identity_for_actor(c, 'human:cara')
        after = events.latest(c)
    watch = events.Watch(store, auth, who, meetings=[rid])
    watch()
    with store.transaction() as c:
        c.execute('UPDATE tasks SET private=1 WHERE id=?', (tid,))
    with store.read() as c:
        changes = events.read(c, auth, who, after, topics=('meeting_items',), meetings=(rid,), watch=watch)
    assert any(data['kind'] == 'links' and data['meeting_id'] == rid for _, _, data in changes[0])
    assert items(api, rid, 'cara-test')['sections']['task'][0]['result_ref'] is None
    with store.transaction() as c:
        c.execute('UPDATE media_control SET deleted_at=? WHERE meeting_id=?', (events.H.now(), rid))
    with store.read() as c:
        events.read(c, auth, who, changes[1], topics=('meeting_items',), meetings=(rid,), watch=watch)
    assert watch.drain() == [('meeting_items', {'meeting_id': rid, 'gone': True})]
    with store.transaction() as c:
        c.execute('UPDATE media_control SET deleted_at=NULL WHERE meeting_id=?', (rid,))
    watch.checked_at = None
    watch()
    assert watch.drain() == []
    fresh = events.Watch(store, auth, who, meetings=[rid])
    fresh()
    assert fresh.drain() == []
    assert items(api, rid, 'cara-test')['id'] == rid


def test_future_link_proposal_column_is_redacted_at_every_boundary(api):
    rid = record(api)
    secret = add(api, rid, {'section': 'task', 'text': 'Mail task', 'detail': {'owner': 'bot:inbox'}})['item']['id']
    tid = push(api, rid, secret)['item']['result_ref']
    iid = add(api, rid, {'section': 'task', 'text': 'Follow up'})['item']['id']
    with api.app.state.store.transaction() as c:
        # The proposal migration is built separately; exercise its column when present.
        events.H.add_column(c, 'meeting_items', 'proposed_task_id', 'TEXT')
        c.execute('UPDATE meeting_items SET proposed_task_id=? WHERE id=?', (tid, iid))
    proposal = next(i for i in items(api, rid, 'cara-test')['sections']['task'] if i['id'] == iid)
    assert proposal['proposed_task_id'] is None
    for _ in range(2):
        result = change(api, rid, iid, {'text': 'Reviewed'}, 'cara-test', key='proposal-edit')
        assert result['item']['proposed_task_id'] is None
    outbox = api.get('/api/meetings/' + rid, headers=headers('cara-test')).json()['outbox']['task']
    assert all(i['proposed_task_id'] is None for i in outbox)
