"""Meeting links preserve both read boundaries, even on retries and after visibility changes."""
from urllib.parse import urlencode

from backend import hubdb as H, task_references
from backend.auth import Identity
from backend.tests.test_api import api, as_member, get, headers, post, setup_attempt  # noqa: F401
from backend.tests.test_meeting_items import add, change, items, record


def task(api, title='Review the plan', requester='human:ana', owner='human:ben', **kw):
    with api.app.state.store.transaction() as c:
        return H.task_create(c, requester, title, 'Review the plan and report back.', owner, lint=False, **kw)


def link(api, rid, ids, token='ana-test', kind='linked', expected=200, key=None):
    return post(api, f'meetings/{rid}/tasks', {'task_ids': ids, 'kind': kind}, token, key, expected)


def remove(api, rid, tid, token='ana-test', expected=200):
    response = api.delete(f'/api/v2/meetings/{rid}/tasks/{tid}', headers=headers(token))
    assert response.status_code == expected, response.text
    return response.json()


def test_links_are_bidirectional_idempotent_and_emit_task_changes(api):
    rid, t = record(api), task(api)
    for _ in range(2):
        result = link(api, rid, [t['id'], t['id']])
        assert [x['task_id'] for x in result['tasks']] == [t['id']]
    meetings = get(api, f"tasks/{t['id']}/meetings")
    assert meetings == {'meetings': [{'meeting_id': rid, 'title': 'Pricing call',
        'started_at': meetings['meetings'][0]['started_at'], 'url': '#/meetings?meeting=' + rid,
        'kind': 'linked'}], 'next_cursor': None}
    remove(api, rid, t['id'])
    assert get(api, f'meetings/{rid}/tasks') == {'tasks': [], 'next_cursor': None}
    assert get(api, f"tasks/{t['id']}/meetings") == {'meetings': [], 'next_cursor': None}
    with api.app.state.store.read() as c:
        changes = list(c.execute("SELECT kind FROM changes WHERE topic='tasks' AND subject_id=? AND kind='meetings'", (t['id'],)))
        assert len(changes) == 2  # one insert and one delete; duplicate links write no change


def test_sent_links_require_the_requester_and_batches_are_atomic(api):
    rid = record(api)
    mine, other = task(api), task(api, requester='human:ben')
    link(api, rid, [mine['id'], other['id']], kind='sent', expected=403)
    assert not get(api, f'meetings/{rid}/tasks')['tasks']
    assert link(api, rid, [mine['id']], kind='sent')['tasks'][0]['kind'] == 'sent'
    # A pair's first kind and author survive a retry or a later ordinary link.
    assert link(api, rid, [mine['id']])['tasks'][0]['kind'] == 'sent'
    link(api, rid, [other['id'], 'missing-task'], expected=404)
    assert len(get(api, f'meetings/{rid}/tasks')['tasks']) == 1
    link(api, rid, [mine['id']] * 21, expected=422)
    link(api, rid, [mine['id']], kind='proposal', expected=422)


def test_an_assistant_links_as_its_person(api):
    from backend.tests.test_assistant import assistant_turn
    rid = record(api, token='ben-test')
    mine = task(api, requester='human:cara', private=False)
    _, attempt = assistant_turn(api, 'cara-test')
    result = link(api, rid, [mine['id']], attempt['token'], kind='sent')
    assert result['tasks'][0]['created_by'] == 'human:cara'
    assert get(api, f"tasks/{mine['id']}/meetings", attempt['token'])['meetings']
    remove(api, rid, mine['id'], attempt['token'])


def test_a_bot_is_refused_the_person_only_link_routes(api):
    rid, t = record(api), task(api, private=False)
    _, _, claimed = setup_attempt(api)
    token = claimed['token']
    get(api, f'meetings/{rid}/tasks', token, expected=403)
    link(api, rid, [t['id']], token, expected=403)
    remove(api, rid, t['id'], token, expected=403)
    get(api, f"tasks/{t['id']}/meetings", token, expected=403)


def test_each_answer_checks_full_task_access_including_bot_read(api):
    as_member(api, 'cara@acme.example')
    rid = record(api, token='ben-test')
    public = task(api, private=False)
    gated = task(api, owner='bot:inbox', private=False)
    link(api, rid, [public['id'], gated['id']])
    assert [t['task_id'] for t in get(api, f'meetings/{rid}/tasks', 'cara-test')['tasks']] == [public['id']]
    assert get(api, f'meetings/{rid}/tasks?limit=1', 'cara-test')['next_cursor'] is None
    link(api, rid, [gated['id']], 'cara-test', expected=404)
    get(api, f"tasks/{gated['id']}/meetings", 'cara-test', expected=404)
    remove(api, rid, gated['id'], 'cara-test', expected=404)
    # Link receipts contain no stored task summaries; a retry filters against current rights.
    link(api, rid, [public['id']], 'cara-test', key='link-retry')
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE tasks SET private=1 WHERE id=?', (public['id'],))
    assert link(api, rid, [public['id']], 'cara-test', key='link-retry') == {'tasks': [], 'next_cursor': None}
    assert get(api, f'meetings/{rid}/tasks', 'cara-test') == {'tasks': [], 'next_cursor': None}


def test_private_deleted_and_pending_meetings_do_not_escape_through_links(api):
    t = task(api, private=False)
    rid = record(api, {'private': True, 'participants': ['cara@acme.example']}, 'ben-test')
    link(api, rid, [t['id']], 'ben-test')
    assert get(api, f"tasks/{t['id']}/meetings", 'cara-test')['meetings'][0]['meeting_id'] == rid
    assert get(api, f"tasks/{t['id']}/meetings", 'ana-test')['meetings']  # org owner
    # A non-attendee who reads the team task sees no meeting or hidden count.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE meetings SET metadata=json_set(metadata,'$.participants',json('[]')) WHERE id=?", (rid,))
    assert get(api, f"tasks/{t['id']}/meetings", 'cara-test') == {'meetings': [], 'next_cursor': None}
    get(api, f'meetings/{rid}/tasks', 'cara-test', expected=403)
    link(api, rid, [t['id']], 'cara-test', expected=403)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE meetings SET metadata=json_set(metadata,'$.private',json('false')) WHERE id=?", (rid,))
    assert get(api, f"tasks/{t['id']}/meetings", 'cara-test')['meetings']
    post(api, f'meetings/{rid}/delete', {}, 'ben-test')
    assert not get(api, f"tasks/{t['id']}/meetings", 'cara-test')['meetings']
    link(api, rid, [t['id']], 'ben-test', expected=404)
    with api.app.state.store.transaction() as c:
        assert c.execute('SELECT 1 FROM meeting_task_links WHERE meeting_id=?', (rid,)).fetchone()
        c.execute('UPDATE media_control SET deleted_at=NULL WHERE meeting_id=?', (rid,))
    assert get(api, f"tasks/{t['id']}/meetings", 'cara-test')['meetings']
    pending = record(api, {'review': 'pending'}, 'ben-test')
    link(api, pending, [t['id']], 'ben-test', expected=409)
    get(api, f'meetings/{pending}/tasks', 'cara-test', expected=404)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE meetings SET review_state='pending' WHERE id=?", (rid,))
    assert not get(api, f"tasks/{t['id']}/meetings", 'ben-test')['meetings']
    get(api, f'meetings/{rid}/tasks', 'ben-test', expected=409)


def test_only_the_linker_meeting_owner_task_parties_or_org_owner_remove(api):
    rid, t = record(api, token='ben-test'), task(api, requester='human:ben', owner='human:ben', private=False)
    link(api, rid, [t['id']], 'ana-test')
    remove(api, rid, t['id'], 'cara-test', expected=403)
    remove(api, rid, t['id'], 'ben-test')  # meeting owner
    link(api, rid, [t['id']], 'cara-test')
    remove(api, rid, t['id'], 'cara-test')  # linker
    link(api, rid, [t['id']], 'cara-test')
    remove(api, rid, t['id'])  # org owner
    other = record(api, token='ana-test')
    link(api, other, [t['id']], 'cara-test')
    remove(api, other, t['id'], 'ben-test')  # task owner/requester, not meeting owner


def collect(api, path, key, limit=50):
    rows, cursor = [], None
    while True:
        response = api.get('/api/v2/' + path, params={'limit': limit, **({'cursor': cursor} if cursor else {})}, headers=headers())
        assert response.status_code == 200, response.text
        assert len(response.content) <= 64 * 1024
        result = response.json()
        assert set(result) == {key, 'next_cursor'}
        assert len(result[key]) <= limit
        assert all(len(row['title']) <= 200 for row in result[key])
        rows += result[key]
        cursor = result['next_cursor']
        if not cursor:
            return rows
        assert len(rows) < 200  # catch an opaque cursor that repeats a page


def test_both_lists_page_past_fifty_with_ties_and_truncated_titles(api):
    rid = record(api)
    ids = [task(api, title=f'Review plan {n} ' + '計' * 280)['id'] for n in range(55)]
    for offset in range(0, len(ids), 20):
        link(api, rid, ids[offset:offset + 20])
    first = get(api, f'meetings/{rid}/tasks')
    assert len(first['tasks']) == 20 and first['next_cursor']
    assert {row['task_id'] for row in collect(api, f'meetings/{rid}/tasks', 'tasks')} == set(ids)
    tid = ids[0]
    with api.app.state.store.transaction() as c:
        original = dict(c.execute('SELECT * FROM meetings WHERE id=?', (rid,)).fetchone())
        for n in range(54):
            copied = {**original, 'id': f'linked-meeting-{n}', 'title': 'Meeting ' + '界' * 300}
            c.execute(f"INSERT INTO meetings({','.join(copied)}) VALUES({','.join('?' * len(copied))})", tuple(copied.values()))
            c.execute("INSERT INTO meeting_task_links VALUES(?,?,?,'linked','human:ana',?)", (H.new_id(), copied['id'], tid, H.now()))
    meetings = collect(api, f'tasks/{tid}/meetings', 'meetings')
    assert len(meetings) == len({m['meeting_id'] for m in meetings}) == 55
    assert [m['started_at'] for m in meetings] == sorted((m['started_at'] for m in meetings), reverse=True)
    get(api, f'meetings/{rid}/tasks?limit=51', expected=422)
    get(api, f'meetings/{rid}/tasks?cursor=broken', expected=422)
    other = record(api)
    get(api, f"meetings/{other}/tasks?" + urlencode({'cursor': first['next_cursor']}), expected=422)


def test_byte_cap_keeps_the_remaining_links_on_the_next_page(api):
    rid = record(api)
    ids = [task(api, title=f'Review budget {n}')['id'] for n in range(20)]
    link(api, rid, ids)
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE meeting_task_links SET created_by=? WHERE meeting_id=?', ('人' * 2000, rid))
    first = get(api, f'meetings/{rid}/tasks?limit=50')
    assert len(first['tasks']) < 20 and first['next_cursor']
    assert {t['task_id'] for t in collect(api, f'meetings/{rid}/tasks', 'tasks')} == set(ids)


def test_task_meeting_cursors_order_offsets_and_fractional_seconds_in_utc(api):
    t = task(api)
    starts = ['2026-11-04T17:00:00Z', '2026-11-04T10:00:00-08:00',
              '2026-11-04T18:00:00.000001Z', '2026-11-04T09:30:00-08:00',
              '2026-11-04T18:00:00Z']
    ids = []
    for i, started in enumerate(starts):
        rid = record(api)
        ids.append(rid)
        with api.app.state.store.transaction() as c:
            c.execute("UPDATE meetings SET metadata=json_set(metadata,'$.started',?) WHERE id=?", (started, rid))
        link(api, rid, [t['id']])
        with api.app.state.store.transaction() as c:
            c.execute('UPDATE meeting_task_links SET id=? WHERE meeting_id=?', (f'ordered-link-{i}', rid))
    path = f"tasks/{t['id']}/meetings"
    ordered = collect(api, path, 'meetings', limit=2)
    assert [m['meeting_id'] for m in ordered] == [ids[i] for i in (2, 4, 1, 3, 0)]
    assert all(m['started_at'].endswith('Z') for m in ordered)
    assert [m['started_at'] for m in ordered] == sorted((m['started_at'] for m in ordered), reverse=True)
    first = get(api, path + '?limit=2')
    continuation = path + '?' + urlencode({'cursor': first['next_cursor'], 'limit': 2})
    assert get(api, continuation) == get(api, continuation)
    assert [m['meeting_id'] for m in collect(api, path, 'meetings', limit=1)] == [m['meeting_id'] for m in ordered]


def test_task_search_matches_title_words_and_numbers_with_existing_privacy(api):
    as_member(api, 'cara@acme.example')
    public = task(api, 'Review launch pricing', private=False)
    task(api, 'Review launch timeline', private=False)
    secret = task(api, 'Review launch pricing secret', owner='bot:inbox', private=False)
    found = get(api, 'tasks?' + urlencode({'q': 'pricing REVIEW'}), 'cara-test')['tasks']
    assert [t['id'] for t in found] == [public['id']]
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE tasks SET number=98431 WHERE id=?', (public['id'],))
        c.execute('UPDATE tasks SET number=98432 WHERE id=?', (secret['id'],))
    assert [t['id'] for t in get(api, 'tasks?q=%2398431', 'cara-test')['tasks']] == [public['id']]
    assert not get(api, 'tasks?q=%2398432', 'cara-test')['tasks']
    assert not get(api, 'tasks?q=%25', 'cara-test')['tasks']  # a literal, not a wildcard


def test_item_references_are_redacted_in_lists_mutations_replays_and_outbox(api):
    rid, hidden = record(api), task(api, owner='bot:inbox', private=False)
    item = add(api, rid, {'section': 'task', 'text': 'Review pricing'})['item']
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE meeting_items SET result_ref=? WHERE id=?', (hidden['id'], item['id']))
        value = task_references.redact(c, api.app.state.auth, Identity('human:cara', 'human', email='cara@acme.example'),
                                      {'proposed_task_id': hidden['id']})
        assert value['proposed_task_id'] is None
    changed = change(api, rid, item['id'], {'version': item['version'], 'text': 'Review pricing again'}, 'cara-test', key='edit-retry')
    assert changed['item']['result_ref'] is None
    assert change(api, rid, item['id'], {'version': item['version'], 'text': 'Review pricing again'}, 'cara-test', key='edit-retry')['item']['result_ref'] is None
    assert items(api, rid, 'cara-test')['sections']['task'][0]['result_ref'] is None
    response = api.get('/api/meetings/' + rid, headers=headers('cara-test'))
    assert response.status_code == 200, response.text
    assert response.json()['outbox']['task'][0]['result_ref'] is None
    assert items(api, rid)['sections']['task'][0]['result_ref'] == hidden['id']
    listed = get(api, 'meetings')['meetings']
    assert next(m for m in listed if m['id'] == rid)['outbox']['task'][0]['result_ref'] == hidden['id']
    assert get(api, f'meetings/{rid}')['outbox']['task'][0]['result_ref'] == hidden['id']
    assert get(api, f'meetings/{rid}', 'cara-test')['outbox']['task'][0]['result_ref'] is None


def test_hard_deletion_cascades_links_without_deleting_the_other_resource(api):
    rid, t = record(api), task(api)
    link(api, rid, [t['id']])
    with api.app.state.store.transaction() as c:
        c.execute('DELETE FROM media_control WHERE meeting_id=?', (rid,))
        c.execute('DELETE FROM meetings WHERE id=?', (rid,))
        assert not c.execute('SELECT 1 FROM meeting_task_links WHERE task_id=?', (t['id'],)).fetchone()
        assert H.task(c, t['id'])
    rid = record(api)
    link(api, rid, [t['id']])
    with api.app.state.store.transaction() as c:
        c.execute('DELETE FROM tasks WHERE id=?', (t['id'],))
        assert not c.execute('SELECT 1 FROM meeting_task_links WHERE meeting_id=?', (rid,)).fetchone()
        assert c.execute('SELECT 1 FROM meetings WHERE id=?', (rid,)).fetchone()


def test_task_trash_restores_links_and_skips_a_meeting_deleted_while_trashed(api):
    rid = record(api, {'private': True, 'audience': 'participants'}, 'ben-test')
    t = task(api, requester='human:ben', owner='human:ana', private=False)
    link(api, rid, [t['id']], 'ben-test', kind='sent')
    assert not get(api, f"tasks/{t['id']}/meetings")['meetings']
    post(api, f"tasks/{t['id']}/delete", {})
    assert not get(api, f'meetings/{rid}/tasks', 'ben-test')['tasks']
    restored = post(api, f"tasks/{t['id']}/restore", {})
    assert restored['restored'] == t['id'] and not restored['skipped']
    assert get(api, f'meetings/{rid}/tasks', 'ben-test')['tasks'][0]['kind'] == 'sent'
    post(api, f"tasks/{t['id']}/delete", {})
    with api.app.state.store.transaction() as c:
        c.execute('DELETE FROM media_control WHERE meeting_id=?', (rid,))
        c.execute('DELETE FROM meetings WHERE id=?', (rid,))
    restored = post(api, f"tasks/{t['id']}/restore", {})
    assert restored['restored'] == t['id'] and not restored['skipped']
    assert not get(api, f"tasks/{t['id']}/meetings")['meetings']


def test_recorder_participant_audiences_keep_the_current_media_read_boundary(api):
    from backend.tests.test_recorder_audience import live_meeting
    mid = live_meeting(api, visibility='team', joiner='cara')
    post(api, f'live-meetings/{mid}/control', {'action': 'end'}, 'ben-test')
    rid = post(api, f'live-meetings/{mid}/finalize', {}, 'ben-test')['meeting_id']
    post(api, f'meetings/{rid}/review', {'action': 'approve', 'private': True, 'audience': 'participants'}, 'ben-test')
    t = task(api, private=False)
    link(api, rid, [t['id']], 'ben-test')
    assert get(api, f"tasks/{t['id']}/meetings", 'cara-test')['meetings'][0]['meeting_id'] == rid
    assert get(api, f"tasks/{t['id']}/meetings") == {'meetings': [], 'next_cursor': None}
    get(api, f'meetings/{rid}/tasks', expected=403)
    link(api, rid, [t['id']], expected=403)
