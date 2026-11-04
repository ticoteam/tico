"""A page audience narrows every file surface for humans and bots, including wider pins and byte routes."""
import json

import pytest

from backend import events, live_blocks, task_privacy, visuals
from backend.auth import Identity
from backend.tests.slug_ids import bot_ids_are_their_slugs  # noqa: F401
from backend.tests.test_api import api, get, headers, post  # noqa: F401
from backend.tests.test_files import turn
from backend.tests.test_groups import send
from backend.tests.test_pages import HTML, page, read


def edit(api, fid, audience, token="ben-test", expected=200):
    r = api.patch('/api/v2/pages/' + fid, json={'audience': audience}, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def promoted(api):
    _, attempt = turn(api)
    made = page(api, attempt, title="Audience secret")
    assert made.status_code == 200, made.text
    fid = made.json()['page']['file_id']
    r = api.patch('/api/v2/files/' + fid, json={'promote': True}, headers=headers('ben-test'))
    assert r.status_code == 200, r.text
    return fid, attempt


def finance_token(api):
    _, attempt = turn(api, bot='finance')
    return attempt['token']


def sql(api, query, token):
    r = api.post('/api/v2/sql', json={'sql': query}, headers=headers(token))
    assert r.status_code == 200, r.text
    return r.json()['rows']


def test_null_preserves_visibility_and_only_managers_see_the_audience(api):
    fid, _ = promoted(api)
    assert read(api, fid, 'ben-test').json()['page']['audience'] is None
    assert read(api, fid, 'cara-test').status_code == 200
    assert 'audience' not in read(api, fid, 'cara-test').json()['page']
    edit(api, fid, {'people': ['cara']}, token='cara-test', expected=403)
    assert read(api, fid, 'cara-test').status_code == 200
    edit(api, fid, {'people': ['cara']})
    assert 'audience' not in read(api, fid, 'cara-test').json()['page']
    assert 'audience' not in api.get('/api/v2/pages?ids=' + fid, headers=headers('cara-test')).json()['pages'][0]
    summary = api.get('/api/v2/pages?ids=' + fid, headers=headers('ben-test')).json()['pages'][0]
    assert 'audience' not in summary and summary['restricted']



def test_restriction_hides_lists_search_cards_pins_and_bytes_and_clearing_restores(api, monkeypatch):
    fid, attempt = promoted(api)
    api.app.state.store.settings.visual_origin = 'https://visuals.acme.example'
    src = read(api, fid, 'cara-test').json()['page']['src']
    assert api.get(src).status_code == 200
    post(api, 'pins', {'place': 'dashboard', 'item': 'page:' + fid})
    assert fid in api.get('/api/v2/places/dashboard', headers=headers('cara-test')).text
    edit(api, fid, {'people': ['ben']})
    for path in ('pages/' + fid, 'files/' + fid, 'files/' + fid + '/versions', 'files/' + fid + '/meta'):
        assert api.get('/api/v2/' + path, headers=headers('cara-test')).status_code == 404, path
    for path in ('pages', 'pages?ids=' + fid, 'files', 'files/search?q=deals', 'bots/ops/files',
                 'places/dashboard'):
        r = api.get('/api/v2/' + path, headers=headers('cara-test'))
        assert r.status_code == 200, r.text
        assert fid not in r.text, path
        assert 'Audience secret' not in r.text, path
    assert api.get(src).status_code == 404
    with api.app.state.store.read() as c:
        bid = c.execute('SELECT blob_id FROM bot_file_versions WHERE file_id=?', (fid,)).fetchone()[0]
        from backend.media import blob_for
        from backend.store import Problem
        with pytest.raises(Problem):
            blob_for(c, api.app.state.auth, Identity('human:cara', 'human'), bid)
    # Even a ready shared hosted copy must never be used for a restricted page.
    from backend import tico_storage
    monkeypatch.setattr(tico_storage, 'visual_link', lambda *a: pytest.fail('restricted page used shared hosting'))
    manager_src = read(api, fid, 'ben-test').json()['page']['src']
    assert api.get(manager_src).status_code == 200
    assert read(api, fid, attempt['token']).status_code == 200
    assert read(api, fid, 'ana-test').status_code == 200
    monkeypatch.undo()
    edit(api, fid, None)
    assert read(api, fid, 'cara-test').status_code == 200
    assert api.get(src).status_code == 200


def test_task_pages_live_blocks_and_bot_context_do_not_bypass_audience(api):
    _, attempt = turn(api)
    tid = post(api, 'tasks', {'owner': 'ops', 'title': 'Shared work', 'body': 'Plan', 'private': False}, token='ben-test')['id']
    fid = page(api, attempt, task=tid, title='Audience secret').json()['page']['file_id']
    outside = finance_token(api)
    assert read(api, fid, outside).status_code == 200
    assert read(api, fid, 'cara-test').status_code == 200
    edit(api, fid, {'people': ['ben']})
    for token in (outside, 'cara-test'):
        assert api.get('/api/v2/tasks/' + tid, headers=headers(token)).status_code == 200
        assert read(api, fid, token).status_code == 404
        for path in ('pages?task=' + tid, 'places/task:' + tid, 'tasks/' + tid + '/files', 'files/search?q=deals'):
            r = api.get('/api/v2/' + path, headers=headers(token))
            assert r.status_code == 200, r.text
            assert fid not in r.text and 'Audience secret' not in r.text
        assert sql(api, f"SELECT id FROM bot_files WHERE id='{fid}'", token) == []
    with api.app.state.store.read() as c:
        from backend.task_review import comment_file
        from backend.store import Problem
        with pytest.raises(Problem):
            comment_file(c, api.app.state.auth, api.app.state.files, Identity('bot:finance', 'bot'), tid, fid, 1)

    with api.app.state.store.read() as c:
        for viewer in (Identity('human:cara', 'human'), Identity('bot:finance', 'bot')):
            filled = live_blocks.fill(f'<tico-files task="{tid}"></tico-files>', c, viewer,
                                      api.app.state.auth, api.app.state.files)
            assert fid not in filled and 'Audience secret' not in filled
        messages = [{'refs': {'attachments': [{'file_id': fid, 'version': 1, 'kind': 'page', 'title': 'Audience secret'}]}}]
        shown = visuals.for_reader(api.app, api.app.state.store.settings, c, Identity('bot:finance', 'bot'), messages)
        assert 'Audience secret' not in str(shown) and shown[0]['refs']['attachments'][0]['removed']
    edit(api, fid, {'bots': ['finance']})
    assert read(api, fid, outside).status_code == 200
    assert read(api, fid, 'cara-test').status_code == 404


def test_nested_group_members_human_and_bot_match_the_shared_audience(api):
    send(api, 'POST', 'groups', {'name': 'Legal', 'add': {'people': ['cara']}})
    legal = next(g['id'] for g in get(api, 'groups') if g['name'] == 'Legal')
    send(api, 'POST', 'groups', {'name': 'Contracts', 'parent': legal, 'add': {'people': ['ben'], 'bots': ['finance']}})
    _, attempt = turn(api)
    tid = post(api, 'tasks', {'owner': 'ops', 'title': 'Shared work', 'body': 'Plan', 'private': False})['id']
    fid = page(api, attempt, task=tid).json()['page']['file_id']
    edit(api, fid, {'teams': [legal]})
    token = finance_token(api)
    for viewer in ('cara-test', 'ben-test', token):
        assert read(api, fid, viewer).status_code == 200
    send(api, 'PATCH', 'groups/' + legal, {'remove': {'people': ['cara']}})
    assert read(api, fid, 'cara-test').status_code == 404


def test_new_versions_keep_audience_and_publish_can_replace_or_clear_it(api):
    _, attempt = turn(api)
    tid = post(api, 'tasks', {'owner': 'ops', 'title': 'Shared work', 'body': 'Plan', 'private': False})['id']
    fid = page(api, attempt, task=tid).json()['page']['file_id']
    selected = edit(api, fid, {'people': ['ben']})['page']['audience']
    changed = page(api, attempt, task=tid, data=HTML + b'<p>new</p>')
    assert changed.status_code == 200, changed.text
    assert changed.json()['page']['audience'] == selected
    assert read(api, fid, 'cara-test').status_code == 404
    r = api.post('/api/v2/pages', json={'name': 'weekly.html', 'task': tid, 'text': HTML.decode(), 'audience': {'people': ['cara']}},
                 headers=headers(attempt['token']))
    assert r.status_code == 200, r.text
    assert r.json()['page']['audience']['people'] == ['cara']
    r = api.post('/api/v2/pages', json={'name': 'weekly.html', 'task': tid, 'text': HTML.decode(), 'audience': None},
                 headers=headers(attempt['token']))
    assert r.status_code == 200, r.text
    assert r.json()['page']['audience'] is None
    assert read(api, fid, 'cara-test').status_code == 200


def test_access_changes_record_old_and_new_but_readers_never_get_the_names(api):
    fid, _ = promoted(api)
    with api.app.state.store.read() as c:
        before = c.execute('SELECT coalesce(max(seq),0) FROM changes').fetchone()[0]
    edit(api, fid, {'people': ['cara']})
    with api.app.state.store.read() as c:
        activity = c.execute("SELECT detail FROM bot_file_activity WHERE file_id=? AND action='page.access_changed'", (fid,)).fetchone()
        event = dict(c.execute("SELECT * FROM events WHERE target=? AND action='page.access_changed'", (fid,)).fetchone())
        assert json.loads(activity[0]) == json.loads(event['detail'])
        assert json.loads(event['detail'])['before'] is None
        assert json.loads(event['detail'])['after']['people'] == ['cara']
        assert not task_privacy.event_readable(c, 'human:cara', event)
        sent, cursor, reset, more = events.read(c, api.app.state.auth, Identity('human:cara', 'human'), before, topics=('bots',))
        assert reset and not sent
    assert sql(api, f"SELECT detail FROM events WHERE target='{fid}' AND action='page.access_changed'", 'cara-test') == []
    assert len(sql(api, f"SELECT detail FROM events WHERE target='{fid}' AND action='page.access_changed'", 'ben-test')) == 1
    assert sql(api, f"SELECT detail FROM bot_file_activity WHERE file_id='{fid}' AND action='page.access_changed'", 'cara-test') == []
    assert len(sql(api, f"SELECT detail FROM bot_file_activity WHERE file_id='{fid}' AND action='page.access_changed'", 'ben-test')) == 1
    removed = api.delete('/api/v2/files/' + fid, headers=headers('ana-test'))
    assert removed.status_code == 200, removed.text
    assert sql(api, f"SELECT detail FROM events WHERE target='{fid}' AND action='page.access_changed'", 'cara-test') == []



def test_audience_does_not_grant_inherited_access_or_constrain_managers(api):
    _, attempt = turn(api)
    fid = page(api, attempt).json()['page']['file_id']
    edit(api, fid, {'people': ['cara']})
    assert read(api, fid, 'cara-test').status_code == 404  # the chat is still private
    assert read(api, fid, 'ben-test').status_code == 200
    assert read(api, fid, attempt['token']).status_code == 200
    assert sql(api, f"SELECT id FROM bot_files WHERE id='{fid}'", 'ben-test') == [[fid]]
    assert len(sql(api, f"SELECT detail FROM events WHERE target='{fid}' AND action='page.access_changed'", 'ben-test')) == 1
    edit(api, fid, {'everyone': False})
    assert read(api, fid, 'ben-test').status_code == 200
    assert read(api, fid, attempt['token']).status_code == 200


def test_a_person_above_the_maintainer_can_see_and_change_the_audience(api):
    fid, _ = promoted(api)
    edit(api, fid, {'everyone': False})
    assert read(api, fid, 'cara-test').status_code == 404
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET reports_to='human:cara' WHERE bot='ops'")
    shown = read(api, fid, 'cara-test')
    assert shown.status_code == 200 and shown.json()['can_manage']
    edit(api, fid, None, token='cara-test')


def test_counsel_cannot_copy_a_restricted_page_into_its_task_context(api, monkeypatch):
    from backend import counsel
    from backend.store import Problem
    fid, _ = promoted(api)
    edit(api, fid, {'people': ['ben']})
    monkeypatch.setattr(counsel, 'available', lambda c: True)
    with pytest.raises(Problem) as refused:
        counsel.prepare(api.app.state.store, api.app.state.auth, Identity('human:ben', 'human', 'ben@acme.example'),
                        api.app.state.blobs, counsel.RuleRead(file_id=fid, source_id='acme'))
    assert refused.value.status == 403


def test_page_audience_migration_keeps_existing_pages_unrestricted():
    import importlib
    import sqlite3
    migration = importlib.import_module('backend.migrations.0091_page_audience')
    with sqlite3.connect(':memory:') as c:
        c.execute('CREATE TABLE pages(file_id TEXT PRIMARY KEY, cadence TEXT)')
        c.execute("INSERT INTO pages VALUES('existing','weekly')")
        migration.up(c)
        migration.up(c)
        assert c.execute('SELECT file_id,cadence,audience FROM pages').fetchall() == [('existing', 'weekly', None)]


def test_linking_a_restricted_page_to_a_readable_task_never_widens_it(api):
    fid, _ = promoted(api)
    tid = post(api, 'tasks', {'owner': 'ops', 'title': 'Linked work', 'body': 'Plan', 'private': False})['id']
    post(api, 'pins', {'place': 'task:' + tid, 'item': 'page:' + fid})
    assert fid in api.get('/api/v2/pages?task=' + tid, headers=headers('cara-test')).text
    edit(api, fid, {'people': ['ben']})
    for path in ('pages?task=' + tid, 'places/task:' + tid, 'files?task=' + tid):
        response = api.get('/api/v2/' + path, headers=headers('cara-test'))
        assert response.status_code == 200, response.text
        assert fid not in response.text
    assert read(api, fid, 'cara-test').status_code == 404
    post(api, 'pins', {'place': 'task:' + tid, 'item': 'page:' + fid}, token='cara-test', expected=404)
    edit(api, fid, None)
    assert fid in api.get('/api/v2/pages?task=' + tid, headers=headers('cara-test')).text
