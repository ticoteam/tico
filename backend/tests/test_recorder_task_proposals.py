"""Recorder proposals keep the meeting's words and recheck the target's task access."""
import pytest

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
    assert add(api, rid, body, 'cara-test')['item']['proposed_task_id'] is None
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


def test_this_mac_deletes_pending_discards_live_content_and_keeps_sent_tasks(api):
    mid = live_meeting(api, visibility='team')
    post(api, f'live-meetings/{mid}/control', {'action': 'end'}, 'ben-test')
    rid = post(api, f'live-meetings/{mid}/finalize', {}, 'ben-test')['meeting_id']
    target = task(api, requester='human:ben', owner='human:ben', private=False)
    add(api, rid, {'section': 'task', 'text': 'Already sent', 'status': 'pushed',
                   'result_ref': target['id']}, 'ben-test')
    with api.app.state.store.read() as c:
        before = dict(c.execute('SELECT * FROM tasks WHERE id=?', (target['id'],)).fetchone())
        assert c.execute('SELECT 1 FROM meeting_task_links WHERE meeting_id=?', (rid,)).fetchone()
    post(api, f'meetings/{rid}/delete', {}, 'ben-test')
    post(api, f'live-meetings/{mid}/discard', {}, 'ben-test')
    get(api, f'meetings/{rid}', 'ben-test', expected=404)
    with api.app.state.store.read() as c:
        assert c.execute('SELECT deleted_at FROM media_control WHERE meeting_id=?', (rid,)).fetchone()[0]
        assert c.execute('SELECT discarded_at FROM live_meetings WHERE id=?', (mid,)).fetchone()[0]
        for table in ('live_meeting_chunks', 'live_meeting_chunk_versions', 'live_meeting_chat'):
            assert not c.execute(f'SELECT 1 FROM {table} WHERE meeting_id=?', (mid,)).fetchone()
        assert not c.execute('SELECT 1 FROM meeting_task_links WHERE meeting_id=?', (rid,)).fetchone()
        assert dict(c.execute('SELECT * FROM tasks WHERE id=?', (target['id'],)).fetchone()) == before


def test_link_proposals_keep_person_and_assistant_permissions_and_refuse_bot_tokens(api):
    from backend.tests.test_assistant import assistant_turn
    from backend.tests.test_api import setup_attempt
    rid, target = record(api), task(api, private=False)
    iid = add(api, rid, {'section': 'task', 'text': 'Follow up', 'proposed_task_id': target['id']})['item']['id']
    _, _, claimed = setup_attempt(api)
    accept(api, rid, iid, claimed['token'], expected=403)
    _, attempt = assistant_turn(api, 'cara-test')
    assert accept(api, rid, iid, attempt['token'])['item']['result_ref'] == target['id']


def test_task_trash_keeps_proposals_with_the_meeting_and_restores_only_the_pointer(api):
    rid = record(api, {'private': True, 'audience': 'participants'}, 'ben-test')
    target = task(api, private=False)
    iid = add(api, rid, {'section': 'task', 'text': 'Follow up', 'proposed_task_id': target['id']}, 'ben-test')['item']['id']
    get(api, f'meetings/{rid}', expected=403)
    post(api, f"tasks/{target['id']}/delete", {})
    proposal = items(api, rid, 'ben-test')['sections']['task'][0]
    assert proposal['id'] == iid and proposal['proposed_task_id'] is None
    restored = post(api, f"tasks/{target['id']}/restore", {})
    assert not restored['skipped']
    assert items(api, rid, 'ben-test')['sections']['task'][0]['proposed_task_id'] == target['id']
    post(api, f"tasks/{target['id']}/delete", {})
    with api.app.state.store.transaction() as c:
        c.execute('DELETE FROM meeting_items WHERE id=?', (iid,))
    restored = post(api, f"tasks/{target['id']}/restore", {})
    assert not restored['skipped'] and not restored.get('unlinked')


@pytest.mark.parametrize('audience,requested_private', [
    ({'private': True}, False), ({'private': True, 'audience': 'participants'}, False), ({}, True),
])
def test_push_keeps_private_and_participants_meeting_text_private_for_human_owners(api, audience, requested_private):
    from backend.tests.test_meeting_items import push
    if audience.get('audience') == 'participants':
        mid = live_meeting(api, visibility='private', added='ana')
        post(api, f'live-meetings/{mid}/control', {'action': 'end'}, 'ben-test')
        rid = post(api, f'live-meetings/{mid}/finalize', {}, 'ben-test')['meeting_id']
        post(api, f'meetings/{rid}/review', {'action': 'approve', 'private': True, 'audience': 'participants'}, 'ben-test')
    else:
        rid = record(api, audience, 'ben-test')
    assert readable(api, rid, 'ana-test')
    iid = add(api, rid, {'section': 'task', 'text': 'Send the pricing plan',
                        'quote': 'A confidential quote', 'detail': {'owner': 'human:ana'}}, 'ben-test')['item']['id']
    result = push(api, rid, iid, {'private': requested_private}, 'ben-test')
    created = get(api, 'tasks/' + result['item']['result_ref'], 'ben-test')['task']
    assert created['private'] and created['owner_id'] == 'human:ana'
    get(api, 'tasks/' + created['id'], 'cara-test', expected=404)


@pytest.mark.parametrize('original', [{}, {'owner': 'human:ben'}])
def test_post_save_create_applies_current_edits_under_the_saved_version_and_replays_once(api, original):
    from backend.tests.test_meeting_items import push
    rid = record(api)
    saved = add(api, rid, {'section': 'task', 'text': 'Send the original plan', 'detail': original})['item']
    edited = {'version': saved['version'], 'text': 'Send the revised plan',
              'detail': {'owner': 'human:cara', 'body': 'The current details', 'due': '2026-12-01T17:00:00-08:00'}}
    key = 'create-edited-' + saved['id']
    result = push(api, rid, saved['id'], edited, key=key)
    replay = push(api, rid, saved['id'], edited, key=key)
    assert result['item']['result_ref'] == replay['item']['result_ref']
    created = get(api, 'tasks/' + result['item']['result_ref'])['task']
    assert created['owner_id'] == 'human:cara' and created['title'] == edited['text']
    assert 'The current details' in created['body'] and created['due_at']
    with api.app.state.store.read() as c:
        assert c.execute('SELECT count(*) FROM tasks WHERE id=?', (created['id'],)).fetchone()[0] == 1
    saved = add(api, rid, {'section': 'task', 'text': 'Send the plan'})['item']
    changed = change(api, rid, saved['id'], {'text': 'Send the newer plan', 'version': saved['version']})['item']
    assert changed['version'] > saved['version']
    push(api, rid, saved['id'], {**edited, 'version': saved['version']}, expected=409)
    change(api, rid, saved['id'], {'text': 'Stale edit', 'version': saved['version']}, expected=409)
    assert next(row for row in items(api, rid)['sections']['task'] if row['id'] == saved['id'])['text'] == 'Send the newer plan'


def test_missing_trashed_and_unreadable_targets_do_not_block_links_or_item_creation(api):
    from backend.tests.test_meeting_task_links import link
    rid = record(api)
    available = task(api, private=False)
    hidden = task(api, title='Secret target', private=True)
    trashed = task(api, title='Archive the obsolete onboarding package', private=False)
    post(api, f"tasks/{trashed['id']}/delete", {})
    deleted = task(api, title='Validate the new search endpoint', private=False)
    with api.app.state.store.transaction() as c:
        c.execute('DELETE FROM tasks WHERE id=?', (deleted['id'],))
    ids = [available['id'], hidden['id'], trashed['id'], deleted['id'], 'missing']
    response = link(api, rid, ids, 'cara-test', key='skip-unavailable')
    assert response['results'] == [{'status': 200}, *[{'status': 404}] * 4]
    assert [row['task_id'] for row in response['tasks']] == [available['id']]
    assert 'Secret target' not in str(response) and hidden['id'] not in str(response)
    for target in ids[1:]:
        value = add(api, rid, {'section': 'task', 'text': 'Send the plan', 'proposed_task_id': target}, 'cara-test')['item']
        assert value['proposed_task_id'] is None
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE tasks SET private=1 WHERE id=?', (available['id'],))
    replay = link(api, rid, ids, 'cara-test', key='skip-unavailable')
    assert not replay['tasks'] and replay['results'] == [{'status': 404}] * 5
