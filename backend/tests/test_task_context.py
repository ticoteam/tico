"""Task context is bounded metadata, filtered by the caller's ordinary source permissions."""
import json

from backend import blobs, meetings
from backend.auth import Identity
from backend.store import H, encode
from backend.tests.slug_ids import bot_ids_are_their_slugs  # noqa: F401
from backend.tests.test_api import api, assign, claim, get, headers, post, ready, restrict, runner  # noqa: F401
from backend.tests.test_cards import page as seed_page
from backend.tests.test_files import turn
from backend.tests.test_meeting_items import record
from backend.tests.test_meeting_task_links import link, task
from backend.tests.test_mcp import call
from backend.tests.test_pages import page


def pin(api, tid, fid):
    post(api, 'pins', {'place': 'task:' + tid, 'item': 'page:' + fid})


def attach(api, mid):
    live = post(api, 'live-meetings', {'title': 'Project meeting', 'visibility': 'private'})['id']
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE live_meetings SET imported_meeting_id=? WHERE id=?', (mid, live))
        c.execute("INSERT INTO live_meeting_bots(meeting_id,bot,joined_by,joined_at) VALUES(?,'ops','human:ana',?)",
                  (live, H.now()))
    return live


def test_context_is_bounded_metadata_newest_first_and_claims_include_it(api):
    t = task(api, owner='bot:ops', private=False)
    with api.app.state.store.transaction() as c:
        for i in range(12):
            mid = f'meeting-{i:02}'
            meetings.snapshot({'id': mid, 'kind': 'meeting', 'private': False, 'title': 'M' * 400,
                               'started': f'2026-10-{i+1:02}', 'owner': 'ana@acme.example'},
                              transcript='Never include this transcript', notes='Never include these notes', conn=c)
            c.execute("INSERT INTO meeting_task_links VALUES(?,?,?,'linked','human:ana',?)",
                      (H.new_id(), mid, t['id'], H.now()))
    page_ids = []
    for i in range(12):
        fid = seed_page(api, 'ops', title='P' * 400, task=t['id'])
        page_ids.append(fid)
        content = f'<html><body>Never include page {i}</body></html>'.encode()
        digest = api.app.state.blobs.put(content, 'text/html')
        with api.app.state.store.transaction() as c:
            asset = blobs.register(c, Identity('bot:ops', 'bot'), digest, len(content), 'context.html', 'text/html')
            c.execute("INSERT INTO bot_file_versions(file_id,version,blob_id,digest,size,name,mime,actor,created_at) "
                      "VALUES(?,1,?,?,?,'context.html','text/html','bot:ops',?)",
                      (fid, asset['id'], digest, len(content), f'2026-10-{i+1:02}T12:00:00Z'))
            c.execute('UPDATE bot_files SET current_version=1 WHERE id=?', (fid,))
    result = get(api, 'tasks/' + t['id'] + '/context')
    assert len(result['pages']) == len(result['meetings']) == 10
    assert [r['id'] for r in result['pages']] == page_ids[:1:-1]
    assert [r['id'] for r in result['meetings']] == [f'meeting-{i:02}' for i in range(11, 1, -1)]
    assert all(len(r['title']) == 200 and not r['limited'] for rows in result.values() for r in rows)
    assert all(set(r) == {'id', 'title', 'version', 'updated_at', 'limited'} for r in result['pages'])
    assert all(set(r) == {'id', 'title', 'date', 'limited'} for r in result['meetings'])
    assert 'Never include' not in json.dumps(result)
    assert len(json.dumps(result).encode()) < 24000
    machine = runner(api)
    assign(api, machine, 'ops')
    ready(api, machine, ['ops'])
    attempt = claim(api, machine, 'ops')
    assert attempt['task_context'] == result
    assert get(api, 'tasks/' + t['id'][:8] + '/context', attempt['token']) == result
    error, mcp = call(api, 'hub_task_context', {'id': t['id']}, token=attempt['token'])
    assert not error and mcp == result
    with api.app.state.store.transaction() as c:
        restrict(c, 'ops', people=['ana'])
    get(api, 'tasks/' + t['id'] + '/context', 'cara-test', expected=404)


def test_bot_uses_own_page_rights_during_task_work_without_task_reader_intersection(api):
    _, attempt = turn(api)
    t = task(api, owner='bot:ops', private=False)
    token = attempt['token']
    filed = seed_page(api, 'cpo', task=t['id'])
    limited = seed_page(api, 'cpo', task=t['id'])
    hidden = seed_page(api, 'cpo', task=t['id'])
    personal = page(api, attempt).json()['page']['file_id']
    post(api, 'pins', {'place': 'task:' + t['id'], 'item': 'page:' + personal}, token='ben-test')
    pinned = seed_page(api, 'ops')
    private_task = task(api, title='Review the private budget', owner='bot:ops', private=True)
    private_page = seed_page(api, 'ops', task=private_task['id'])
    for fid in (pinned, private_page):
        pin(api, t['id'], fid)
    with api.app.state.store.transaction() as c:
        for fid, bots in ((limited, ['ops']), (hidden, ['finance'])):
            c.execute('UPDATE pages SET audience=? WHERE file_id=?', (encode({'bots': bots}), fid))
    before = get(api, 'tasks/' + t['id'] + '/context', token)
    shown = {p['id']: p for p in before['pages']}
    assert set(shown) == {filed, limited, personal, pinned, private_page}
    assert {fid for fid, p in shown.items() if p['limited']} == {limited, personal, private_page}
    pin(api, private_task['id'], pinned)
    private_context = get(api, 'tasks/' + private_task['id'] + '/context', token)
    assert not next(p for p in private_context['pages'] if p['id'] == pinned)['limited']
    # A task reader cannot read this source, but the bot can; the task does not change either grant.
    get(api, 'pages/' + limited, 'cara-test', expected=404)
    get(api, 'pages/' + limited, token)
    with api.app.state.store.transaction() as c:
        message = H.task_comment(c, 'human:ana', t['id'], 'Review this task', wake=False)
        c.execute('INSERT INTO attempt_inputs(attempt_id,message_id) VALUES(?,?)', (attempt['id'], message['id']))
    assert get(api, 'tasks/' + t['id'] + '/context', token) == before
    get(api, 'pages/' + limited, token)
    get(api, 'pages/' + private_page, token)
    # A readable page need not be a task reference to remain readable during task work.
    outside = seed_page(api, 'ops')
    get(api, 'pages/' + outside, token)
    assert outside not in json.dumps(before)
    assert hidden not in json.dumps(before)
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE pages SET audience=? WHERE file_id=?', (encode({'bots': ['finance']}), limited))
        c.execute('UPDATE pages SET archived=1 WHERE file_id=?', (pinned,))
    after = get(api, 'tasks/' + t['id'] + '/context', token)
    assert {p['id'] for p in after['pages']} == {filed, personal, private_page}
    get(api, 'pages/' + limited, token, expected=404)


def test_attached_bot_reads_saved_meeting_in_every_turn_and_loses_revoked_grant(api):
    mid = record(api, {'private': True, 'notes': 'Owner decisions'})
    live = attach(api, mid)
    other = record(api, {'private': True})
    _, attempt = turn(api)
    t = task(api, owner='bot:ops', private=False)
    for rid in (mid, other):
        link(api, rid, [t['id']])
    token = attempt['token']
    assert get(api, 'meetings/transcript?id=' + mid, token)['notes'] == 'Owner decisions'
    assert [m['id'] for m in get(api, 'meetings/search', token)['results']] == [mid]
    context = get(api, 'tasks/' + t['id'] + '/context', token)
    assert [m['id'] for m in context['meetings']] == [mid]
    assert context['meetings'][0]['limited']
    with api.app.state.store.transaction() as c:
        message = H.task_comment(c, 'human:ana', t['id'], 'Review this task', wake=False)
        c.execute('INSERT INTO attempt_inputs(attempt_id,message_id) VALUES(?,?)', (attempt['id'], message['id']))
    assert get(api, 'tasks/' + t['id'] + '/context', token) == context
    get(api, 'meetings/transcript?id=' + mid, token)
    get(api, 'meetings/transcript?id=' + other, token, expected=404)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE live_meeting_bots SET joined_by='human:ben' WHERE meeting_id=?", (live,))
    get(api, 'meetings/transcript?id=' + mid, token, expected=404)
    assert not get(api, 'tasks/' + t['id'] + '/context', token)['meetings']
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE live_meeting_bots SET joined_by='human:ana' WHERE meeting_id=?", (live,))
        original = meetings.get(mid, c)['metadata']
        meetings.snapshot({**original, 'owner': 'ben@acme.example', 'audience': 'participants',
                           'audience_emails': ['ben@acme.example']}, conn=c)
    get(api, 'meetings/transcript?id=' + mid, token, expected=404)
    with api.app.state.store.transaction() as c:
        meetings.snapshot({**original, 'audience': 'participants', 'audience_emails': ['ana@acme.example']}, conn=c)
    assert get(api, 'tasks/' + t['id'] + '/context', token)['meetings'][0]['limited']
    get(api, 'meetings/transcript?id=' + mid, token)
    with api.app.state.store.transaction() as c:
        c.execute('DELETE FROM live_meeting_bots WHERE meeting_id=?', (live,))
    get(api, 'meetings/transcript?id=' + mid, token, expected=404)


def test_claim_retry_rebuilds_metadata_with_current_own_access(api):
    t = task(api, owner='bot:ops', private=False)
    fid = seed_page(api, 'cpo', task=t['id'])
    mid = record(api, {'private': False})
    link(api, mid, [t['id']])
    machine = runner(api)
    assign(api, machine, 'ops')
    ready(api, machine, ['ops'])
    first = claim(api, machine, 'ops', key='context-claim')
    assert first['task_context']['pages'][0]['id'] == fid
    assert first['task_context']['meetings'][0]['id'] == mid
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE pages SET audience=? WHERE file_id=?', (encode({'bots': ['finance']}), fid))
        meetings.snapshot({**meetings.get(mid, c)['metadata'], 'private': True}, conn=c)
    replay = claim(api, machine, 'ops', key='context-claim')
    assert replay['id'] == first['id']
    assert replay['task_context'] == {'pages': [], 'meetings': []}
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE pages SET audience=NULL WHERE file_id=?', (fid,))
        meetings.snapshot({**meetings.get(mid, c)['metadata'], 'private': False}, conn=c)
    assert claim(api, machine, 'ops', key='context-claim')['task_context'] == first['task_context']


def test_notes_are_paged_without_repeating_them_at_transcript_offsets(api):
    mid = record(api, {'private': False, 'notes': 'First decision. Second decision.'})
    first = get(api, f'meetings/transcript?id={mid}&notes=true&limit=16')
    rest = get(api, f'meetings/transcript?id={mid}&notes=true&offset={first["next_offset"]}')
    assert first['text'] + rest['text'] == 'First decision. Second decision.'
    assert 'notes' not in rest
    transcript = get(api, f'meetings/transcript?id={mid}')
    assert transcript['notes'] == first['notes']
    transcript = get(api, f'meetings/transcript?id={mid}&offset=1')
    assert 'notes' not in transcript and 'decision' not in transcript['text']
    _, attempt = turn(api)
    error, notes = call(api, 'hub_meeting_read', {'id': mid, 'notes': True}, token=attempt['token'])
    assert not error and notes['text'] == first['notes']
