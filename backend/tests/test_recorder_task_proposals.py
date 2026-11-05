"""Recorder proposals keep the meeting's words and recheck the target's task access."""
from backend.tests.test_meeting_items import api, record, add, items, change, headers  # noqa: F401
from backend.tests.test_meeting_task_links import task, get
from backend.tests.test_recorder_audience import live_meeting, readable
from backend.tests.test_api import post


def accept(api, rid, iid, token='ana-test', key=None, expected=200):
    response = api.post(f'/api/v2/meetings/{rid}/items/{iid}/link', json={}, headers=headers(token, key))
    assert response.status_code == expected, response.text
    return response.json()


def test_accepting_a_closed_task_proposal_links_without_creating_a_task(api):
    rid, target = record(api), task(api, private=False)
    proposal = add(api, rid, {'section': 'task', 'text': 'Follow up on the pricing plan',
                             'proposed_task_id': target['id'], 'detail': {'body': 'The agreed details'}})['item']
    with api.app.state.store.transaction() as c:
        before = c.execute('SELECT count(*) FROM tasks').fetchone()[0]
        c.execute("UPDATE tasks SET status='closed' WHERE id=?", (target['id'],))
    result = accept(api, rid, proposal['id'], 'cara-test', 'accept-retry')
    assert result['pushed'] and result['item']['result_ref'] == target['id']
    assert not accept(api, rid, proposal['id'])['pushed']
    assert get(api, f'meetings/{rid}/tasks')['tasks'][0]['kind'] == 'proposal'
    assert get(api, f"tasks/{target['id']}/meetings")['meetings'][0]['meeting_id'] == rid
    with api.app.state.store.read() as c:
        assert c.execute('SELECT count(*) FROM tasks').fetchone()[0] == before
        assert c.execute('SELECT count(*) FROM meeting_task_links WHERE meeting_id=?', (rid,)).fetchone()[0] == 1
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE tasks SET private=1 WHERE id=?', (target['id'],))
    replay = accept(api, rid, proposal['id'], 'cara-test', 'accept-retry')['item']
    assert replay['proposed_task_id'] is None and replay['result_ref'] is None
    assert items(api, rid, 'cara-test')['sections']['task'][0]['proposed_task_id'] is None
    outbox = get(api, f'meetings/{rid}', 'cara-test')['outbox']['task'][0]
    assert outbox['proposed_task_id'] is None and outbox['result_ref'] is None


def test_hidden_missing_dismissed_and_non_task_proposals_cannot_link(api):
    rid, hidden = record(api), task(api, owner='bot:inbox', private=False)
    body = {'section': 'task', 'text': 'Follow up', 'proposed_task_id': hidden['id']}
    add(api, rid, body, 'cara-test', expected=404)
    iid = add(api, rid, body)['item']['id']
    accept(api, rid, iid, 'cara-test', expected=404)
    add(api, rid, {**body, 'section': 'doc'}, expected=422)
    change(api, rid, iid, {'status': 'dismissed'})
    accept(api, rid, iid, expected=409)
    iid = add(api, rid, body)['item']['id']
    with api.app.state.store.transaction() as c:
        c.execute('DELETE FROM tasks WHERE id=?', (hidden['id'],))
    assert items(api, rid)['sections']['task'][-1]['proposed_task_id'] is None
    accept(api, rid, iid, expected=409)
    pending = record(api, {'review': 'pending'})
    target = task(api, private=False)
    iid = add(api, pending, {**body, 'proposed_task_id': target['id']})['item']['id']
    accept(api, pending, iid, expected=409)


def test_private_live_participants_survive_finalize_and_pending_stays_owner_only(api):
    mid = live_meeting(api, visibility='private', added='cara', joiner='cara')
    post(api, f'live-meetings/{mid}/access', {'add': ['eve']}, 'ben-test')
    post(api, f'live-meetings/{mid}/control', {'action': 'end'}, 'ben-test')
    rid = post(api, f'live-meetings/{mid}/finalize', {}, 'ben-test')['meeting_id']
    assert not readable(api, rid, 'cara-test')
    post(api, f'meetings/{rid}/review', {'action': 'approve', 'private': True}, 'ben-test')
    assert readable(api, rid, 'cara-test') and readable(api, rid, 'eve-test')
    assert not readable(api, rid, 'fay-test')


def test_this_mac_dismisses_pending_without_links(api):
    rid = record(api, {'review': 'pending'}, 'ben-test')
    post(api, f'meetings/{rid}/review', {'action': 'dismiss'}, 'ben-test')
    assert get(api, f'meetings/{rid}', 'ben-test')['review_state'] == 'dismissed'
    with api.app.state.store.read() as c:
        assert not c.execute('SELECT 1 FROM meeting_task_links WHERE meeting_id=?', (rid,)).fetchone()


def test_link_proposals_keep_person_and_assistant_permissions_and_refuse_bot_tokens(api):
    from backend.tests.test_assistant import assistant_turn
    from backend.tests.test_api import setup_attempt
    rid, target = record(api), task(api, private=False)
    iid = add(api, rid, {'section': 'task', 'text': 'Follow up', 'proposed_task_id': target['id']})['item']['id']
    _, _, claimed = setup_attempt(api)
    accept(api, rid, iid, claimed['token'], expected=403)
    _, attempt = assistant_turn(api, 'cara-test')
    assert accept(api, rid, iid, attempt['token'])['item']['result_ref'] == target['id']
