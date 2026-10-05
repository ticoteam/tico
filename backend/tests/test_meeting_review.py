"""Personal import review protects drafts and publishes exactly once."""
import json
import sqlite3

import pytest

from backend import hubdb as H, meetings
from backend.tests.test_api import api, as_member, get, headers, post, restrict, runner, setup_attempt  # noqa: F401
from backend.tests.test_granola_mcp import Provider
from backend.tests.test_mcp import call
from backend.tests.test_member_bots import botops, turn  # noqa: F401
from backend.tests.test_routines import DEBRIEF, create, setup


def imported(api, token='ben-test', **fields):
    return post(api, 'meetings/import', {'source': 'zoom', 'external_id': 'queue-1',
                'title': 'Planning', 'transcript': 'Sam: Review this transcript.',
                'participants': ['cara@acme.example'], **fields}, token)


def review(api, rid, action='approve', token='ben-test', **fields):
    return post(api, f'meetings/{rid}/review', {'action': action, **fields}, token)


def queue(api, token='ben-test', state='pending'):
    return get(api, 'meetings?review=' + state, token)


def test_pending_hidden_from_owner_participants_bots_search_sql_and_send(api):
    made = imported(api)
    rid = made['id']
    assert made['review_state'] == 'pending'
    assert queue(api)['count'] == queue(api)['pending_count'] == 1
    assert get(api, 'updates/unread', 'ben-test')['meetings_pending'] == 1
    assert api.get('/api/meetings', headers=headers('ben-test')).json() == []
    _, _, attempt = setup_attempt(api)
    for token in ('ana-test', 'cara-test', attempt['token']):
        assert api.get('/api/meetings/' + rid, headers=headers(token)).status_code == 404
        assert api.get('/api/meetings/' + rid + '/versions', headers=headers(token)).status_code == 404
        assert get(api, 'meetings/search', token)['results'] == []
        assert api.get('/api/v2/meetings/transcript?id=' + rid, headers=headers(token)).status_code == 404
        assert post(api, 'sql', {'sql': 'SELECT id FROM meetings'}, token)['rows'] == []
        assert post(api, f'meetings/{rid}/review', {'action': 'approve'}, token,
                    expected=403 if token == attempt['token'] else 404)
    # Even the filed-for person's search/export waits until sharing; the queue is their read path.
    assert get(api, 'meetings/search', 'ben-test')['results'] == []
    assert queue(api, 'ana-test')['pending_count'] == 0
    response = api.post('/api/meetings/' + rid + '/send', json={'slug': 'ops'}, headers=headers('ben-test'))
    assert response.status_code == 409
    assert post(api, 'sql', {'sql': "SELECT target FROM events WHERE action IN ('note.created','meeting.imported')"})['rows'] == []


def test_auto_share_precedence_and_changing_preference_keeps_existing_queue(api):
    first = imported(api)
    assert get(api, 'meetings/settings', 'ben-test')['effective_auto_share'] is False
    post(api, 'meetings/settings', {'review_default': 'auto'})
    assert imported(api, external_id='auto-team')['review_state'] == 'live'
    post(api, 'preferences/meetings.auto_share', {'value': False}, 'ben-test')
    assert imported(api, external_id='personal-review')['review_state'] == 'pending'
    post(api, 'meetings/settings', {'auto_share': True}, 'ben-test')
    assert imported(api, external_id='personal-auto')['review_state'] == 'live'
    assert queue(api)['count'] == 2 and first['id'] in [r['id'] for r in queue(api)['meetings']]
    assert post(api, 'meetings/settings', {'review_default': 'review'}, 'ben-test', expected=403)
    post(api, 'meetings/settings', {'auto_share': None}, 'ben-test')
    assert get(api, 'meetings/settings', 'ben-test')['auto_share'] is None
    assert post(api, 'preferences/meetings.auto_share', {'value': 'false'}, 'ben-test', expected=422)


def test_manual_and_explicit_live_human_imports_but_machine_cannot_bypass(api):
    assert imported(api, source='manual')['review_state'] == 'live'
    assert imported(api, source='api', review='live')['review_state'] == 'live'
    machine = runner(api)
    body = {'source': 'zoom', 'owner_email': 'ben@acme.example', 'notes': 'Machine notes', 'review': 'live'}
    assert post(api, 'meetings/import', body, machine['token'], expected=403)
    body.pop('review')
    assert post(api, 'meetings/import', body, machine['token'])['review_state'] == 'pending'


def test_approve_emits_once_and_reimports_never_undo_review_privacy(api):
    setup(api)
    create(api, 'ops', DEBRIEF)
    made = imported(api, token='ana-test', send_to='coo')
    assert not get(api, 'tasks')['tasks']
    approved = review(api, made['id'], token='ana-test', private=False)
    assert approved['review_state'] == 'live' and approved['delivery']['task_id']
    assert len([t for t in get(api, 'tasks')['tasks'] if t['owner'] == 'bot:ops']) == 1
    review(api, made['id'], token='ana-test')
    imported(api, token='ana-test', transcript='Sam: Corrected transcript.', private=True)
    assert get(api, 'meetings/' + made['id'])['private'] is False
    post(api, f"meetings/{made['id']}/review", {'action': 'dismiss'}, expected=409)
    assert get(api, 'meetings/' + made['id'])['review_state'] == 'live'
    review(api, made['id'], token='ana-test')
    assert len([t for t in get(api, 'tasks')['tasks'] if t['owner'] == 'bot:ops']) == 1


def test_private_approval_does_not_emit_and_uses_granola_source_default(api):
    setup(api)
    create(api, 'ops', DEBRIEF)
    made = imported(api, token='ana-test', source='granola')
    assert review(api, made['id'], token='ana-test')['private'] is True
    assert not get(api, 'tasks')['tasks']


def test_dismiss_survives_sync_restore_and_batch_selection_is_atomic(api):
    first = imported(api)
    assert imported(api)['id'] == first['id']
    review(api, first['id'], 'dismiss')
    changed = imported(api, notes='New notes')
    assert changed['id'] == first['id'] and changed['review_state'] == 'dismissed'
    assert imported(api, notes='New notes')['review_state'] == 'dismissed'
    assert queue(api)['count'] == 0 and queue(api, state='dismissed')['count'] == 1
    assert review(api, first['id'], 'restore')['review_state'] == 'pending'
    second = imported(api, external_id='queue-2')
    other = imported(api, token='cara-test')
    assert post(api, 'meetings/review', {'action': 'approve_all', 'ids': [first['id'], other['id']]}, 'ben-test', expected=404)
    assert queue(api)['count'] == 2
    shared = post(api, 'meetings/review', {'action': 'approve_all'}, 'ben-test')
    assert shared['count'] == 2 and shared['pending_count'] == 0
    assert {r['id'] for r in shared['meetings']} == {first['id'], second['id']}
    assert queue(api, 'cara-test')['count'] == 1
    assert post(api, 'meetings/review', {'action': 'dismiss_all', 'ids': []}, 'cara-test')['count'] == 0
    assert post(api, 'meetings/review', {'action': 'dismiss_all'}, 'cara-test')['count'] == 1
    assert post(api, f"meetings/{other['id']}/review", {'action': 'approve'}, 'cara-test', expected=409)


def test_legacy_hub_migration_backfills_live_and_cloud_restart_keeps_queue(api, tmp_path):
    db = sqlite3.connect(tmp_path / 'old.db', isolation_level=None)
    db.row_factory = sqlite3.Row
    for version, script in enumerate(H.MIGRATIONS[:19], 1):
        H._apply(db, script)
        db.execute(f'PRAGMA user_version={version}')
    db.execute("INSERT INTO meetings(id,title,owner,metadata_json,created,updated) VALUES(?,?,?,?,?,?)",
               ('legacy', 'Meeting', 'ben@acme.example', json.dumps({'id': 'legacy', 'kind': 'meeting'}), H.now(), H.now()))
    H.migrate(db)
    assert db.execute('PRAGMA user_version').fetchone()[0] == len(H.MIGRATIONS)
    assert meetings.get('legacy', db)['review_state'] == 'live'
    assert db.execute("SELECT 1 FROM sqlite_master WHERE name='task_file_reviews'").fetchone()
    assert 'media_state' in {r[1] for r in db.execute('PRAGMA table_info(bot_file_versions)')}
    db.close()
    made = imported(api)
    api.app.state.store.initialize(seed_market=False)
    assert queue(api)['meetings'][0]['id'] == made['id']
    with api.app.state.store.read() as c:
        assert {53, 54, 55, 56, 57} <= {r[0] for r in c.execute('SELECT version FROM cloud_migrations WHERE version>=53')}


def test_granola_account_sync_lands_in_the_persons_pending_queue(api):
    provider = Provider(api)
    provider.connect('ben-test')
    provider.sync('human:ben')
    pending = queue(api)
    assert pending['count'] == 1 and pending['meetings'][0]['source'] == 'granola'
    assert pending['meetings'][0]['review_state'] == 'pending'
    assert queue(api, 'ana-test')['count'] == 0
    assert not get(api, 'meetings/search')['results']
    err, found = call(api, 'hub_meeting_pending', {}, token='ben-test')
    assert not err and found['count'] == 1
    rid = found['meetings'][0]['id']
    assert not call(api, 'hub_meeting_dismiss', {'id': rid}, token='ben-test')[0]
    assert not call(api, 'hub_meeting_restore', {'id': rid}, token='ben-test')[0]
    assert not call(api, 'hub_meeting_approve', {'all': True}, token='ben-test')[0]


def test_botops_can_review_only_the_requesting_persons_queue(api, botops):
    mine = imported(api, token='cara-test')
    other = imported(api)
    attempt = turn(api, botops, 'cara-test', 'Share my pending meetings')
    err, pending = call(api, 'hub_meeting_pending', {}, token=attempt['token'])
    assert not err and [r['id'] for r in pending['meetings']] == [mine['id']]
    assert call(api, 'hub_meeting_approve', {'id': other['id']}, token=attempt['token'])[0]
    err, shared = call(api, 'hub_meeting_approve', {'id': mine['id']}, token=attempt['token'])
    assert not err and shared['review_state'] == 'live'
    assert queue(api)['count'] == 1


@pytest.mark.parametrize('filed_for,state', [('ben@acme.example', 'pending'), ('', 'live')])
def test_close_personal_queue_and_team_wide_exception(api, filed_for, state):
    machine = runner(api)
    body = {'source': 'close', 'resource_type': 'call', 'external_id': 'call-queue',
            'owner_email': filed_for, 'started': '2026-10-01T10:00:00Z', 'ended': '2026-10-01T10:01:00Z',
            'duration_ms': 60000, 'source_updated_at': '2026-10-01T10:01:00Z',
            'turns': [{'text': 'Review this call.', 'speaker': 'Sam', 'start_ms': 0, 'end_ms': 1000}],
            'summary_text': 'Call notes'}
    made = post(api, 'imports/transcripts', body, machine['token'])
    assert made['review_state'] == state
    assert post(api, 'imports/transcripts', body, machine['token'])['review_state'] == state
    if state == 'pending':
        assert queue(api)['meetings'][0]['id'] == made['id']
        review(api, made['id'], 'dismiss')
        body['turns'][0]['text'] = 'Corrected call.'
        body['source_updated_at'] = '2026-10-01T10:02:00Z'
        again = post(api, 'imports/transcripts', body, machine['token'])
        assert again['id'] == made['id'] and again['review_state'] == 'dismissed'
        review(api, made['id'], 'restore')
        setup(api)
        create(api, 'ops', DEBRIEF)
        assert review(api, made['id'])['review_state'] == 'live'
        assert len([t for t in get(api, 'tasks')['tasks'] if t['owner'] == 'bot:ops']) == 1


@pytest.mark.parametrize('version', [20, 21])
def test_released_migrations_remain_in_place(tmp_path, version):
    db = sqlite3.connect(tmp_path / 'released.db', isolation_level=None)
    db.row_factory = sqlite3.Row
    for i, script in enumerate(H.MIGRATIONS[:version], 1):
        H._apply(db, script)
        db.execute(f'PRAGMA user_version={i}')
    db.execute("INSERT INTO blob_locations VALUES('digest','example-bucket','2026-10-01')")
    db.execute("INSERT INTO meetings(id,title,owner,metadata_json,created,updated) VALUES(?,?,?,?,?,?)",
               ('released', 'Existing notes', 'ben@acme.example', '{}', H.now(), H.now()))
    H.migrate(db)
    assert db.execute('SELECT bucket FROM blob_locations').fetchone()[0] == 'example-bucket'
    assert db.execute('SELECT count(*) FROM task_file_reviews').fetchone()[0] == 0
    assert meetings.get('released', db)['review_state'] == 'live'
    assert meetings.get('released', db)['metadata']['ready_announced'] is True
    H.migrate(db)
    assert db.execute('PRAGMA user_version').fetchone()[0] == len(H.MIGRATIONS)
    db.close()


def test_delayed_send_rechecks_rights_and_rolls_back_approval(api):
    as_member(api, 'ben@acme.example')
    made = imported(api, send_to='ops')
    with api.app.state.store.transaction() as c:
        restrict(c, 'ops', people=['ana'])
    post(api, f"meetings/{made['id']}/review", {'action': 'approve'}, 'ben-test', expected=404)
    assert queue(api)['pending_count'] == 1
    with api.app.state.store.read() as c:
        record = meetings.get(made['id'], c)
        assert record['review_state'] == 'pending' and not record['delivery']
        assert not record['metadata'].get('ready_announced')
    with api.app.state.store.transaction() as c:
        restrict(c, 'ops', people=['ben'])
    approved = review(api, made['id'])
    delivery = approved['delivery']['task_id']
    assert delivery
    assert review(api, made['id'])['delivery']['task_id'] == delivery
    assert len(get(api, 'tasks', 'ben-test')['tasks']) == 1


def test_mixed_batch_transitions_and_repeated_actions_are_atomic(api):
    pending = imported(api)
    live = imported(api, external_id='already-live', review='live')
    dismissed = imported(api, external_id='already-dismissed')
    review(api, dismissed['id'], 'dismiss')
    post(api, 'meetings/review', {'action': 'dismiss_all', 'ids': [pending['id'], live['id']]}, 'ben-test', expected=409)
    post(api, 'meetings/review', {'action': 'approve_all', 'ids': [pending['id'], dismissed['id']]}, 'ben-test', expected=409)
    assert queue(api)['pending_count'] == 1
    result = post(api, 'meetings/review', {'action': 'approve_all', 'ids': [pending['id'], live['id']]}, 'ben-test')
    assert result['pending_count'] == 0 and result['count'] == 2
    assert review(api, live['id'])['review_state'] == 'live'
    assert review(api, dismissed['id'], 'dismiss')['review_state'] == 'dismissed'
    assert review(api, dismissed['id'], 'restore')['review_state'] == 'pending'
    assert review(api, dismissed['id'], 'restore')['review_state'] == 'pending'
    result = post(api, 'meetings/review', {'action': 'dismiss_all', 'ids': [dismissed['id']]}, 'ben-test')
    assert result['pending_count'] == 0
    assert post(api, 'meetings/review', {'action': 'dismiss_all', 'ids': [dismissed['id']]}, 'ben-test')['count'] == 1


def test_dismissed_private_content_stays_out_of_all_shared_reads(api):
    setup(api)
    create(api, 'ops', DEBRIEF)
    made = imported(api)
    review(api, made['id'], 'dismiss')
    _, _, attempt = setup_attempt(api, bot='finance')
    for token in ('ana-test', 'cara-test', attempt['token']):
        assert api.get('/api/v2/meetings/' + made['id'], headers=headers(token)).status_code == 404
        assert api.get('/api/meetings/' + made['id'] + '/versions', headers=headers(token)).status_code == 404
        assert get(api, 'meetings/search', token)['results'] == []
        assert api.get('/api/v2/meetings/transcript?id=' + made['id'], headers=headers(token)).status_code == 404
        assert post(api, 'sql', {'sql': 'SELECT id FROM meetings'}, token)['rows'] == []
    assert queue(api, 'ana-test', 'dismissed')['count'] == 0
    assert queue(api, 'cara-test', 'dismissed')['count'] == 0
    assert not get(api, 'tasks')['tasks']
