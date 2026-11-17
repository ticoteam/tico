"""Standalone pages keep their identity and their original read boundary across writers and links."""

import sqlite3
from pathlib import Path

import pytest

from backend import page_links as L
from backend.store import Problem

MIGRATION = Path(__file__).parents[1] / 'migrations/20261117195614_page_links.sql'


def test_backfill_is_idempotent_and_adopts_documents_and_people():
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.executescript('''
        CREATE TABLE requirement_pages(visit_id TEXT, requirement_id TEXT, file_id TEXT, replaced_at TEXT, created_at TEXT);
        CREATE TABLE bot_files(id TEXT PRIMARY KEY,bot_id TEXT,task_id TEXT);
        CREATE TABLE pages(file_id TEXT PRIMARY KEY,cadence TEXT,created_at TEXT,updated_at TEXT,spec TEXT);
        CREATE TABLE pins(place TEXT,item TEXT,made_by TEXT,created_at TEXT);
        CREATE TABLE task_documents(file_id TEXT,task_id TEXT,name TEXT,created_at TEXT,updated_at TEXT);
        CREATE TABLE bot_file_versions(file_id TEXT,actor TEXT,created_at TEXT);
        INSERT INTO bot_files VALUES('p','ops','private'),('q','finance',NULL),('doc','ops','t');
        INSERT INTO pages VALUES('p','weekly','2026-01-01','2026-01-02','yes'),
                                ('q','none','2026-01-01','2026-01-01',NULL);
        INSERT INTO pins VALUES('task:public','page:p','human:ana','2026-01-02');
        INSERT INTO task_documents VALUES('doc','t','QA report','2026-01-01','2026-01-02');
        INSERT INTO bot_file_versions VALUES('p','human:ben','2026-01-02'),('doc','human:ana','2026-01-02');
    ''')
    for _ in range(2):
        c.executescript(MIGRATION.read_text())
    assert {tuple(r) for r in c.execute('SELECT page_id,target,role FROM page_links')} == {
        ('p', 'bot:ops', 'writer'),
        ('q', 'bot:finance', 'writer'),
        ('p', 'task:private', 'Spec'),
        ('p', 'task:public', 'related'),
        ('doc', 'task:t', 'QA report'),
        ('doc', 'task:t', 'related'),
        ('doc', 'bot:ops', 'writer'),
        ('p', 'human:ben', 'writer'),
        ('doc', 'human:ana', 'writer'),
    }
    assert c.execute("SELECT cadence FROM pages WHERE file_id='doc'").fetchone()[0] == 'none'
    c.execute(
        "INSERT INTO requirement_pages(visit_id,requirement_id,file_id,replaced_at) VALUES('v','r','p',NULL),('v','r','q',NULL)"
    )
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("INSERT INTO requirement_pages(visit_id,requirement_id,file_id,replaced_at) VALUES('v','r','p',NULL)")
    assert L.link(c, 'p', 'task:extra', 'qa-report', 'human:ana')
    assert not L.link(c, 'p', 'task:extra', 'QA report', 'human:ana')
    assert L.pages_for(c, 'task:extra', 'qa-report') == ['p']
    L.unlink(c, 'q', 'human:nobody', 'writer', 'human:ana')
    with pytest.raises(Problem, match='at least one writer'):
        L.unlink(c, 'q', 'bot:finance', 'writer', 'human:ana')
    for target, role in [('goal:g', 'Spec'), ('task:t', 'writer'), ('wrong:id', 'related')]:
        with pytest.raises(Problem):
            L.link(c, 'p', target, role, 'human:ana')


from backend.auth import Identity
from backend.tests.helpers.api import headers, post
from backend.tests.helpers.company import bot


def publish_page(api, token, text='One', **fields):
    return api.post(
        '/api/v2/pages', json={'name': 'spec.html', 'text': '<p>' + text + '</p>', **fields}, headers=headers(token)
    )


def task(api, private=False, token='ana-test', title='Standalone pages'):
    return post(api, 'tasks', {'owner': 'ops', 'title': title, 'body': 'Work', 'private': private}, token)


def add_link(api, fid, target, role='related', token='ana-test'):
    return api.post('/api/v2/pages/' + fid + '/links', json={'target': target, 'role': role}, headers=headers(token))


def test_three_writers_keep_one_page_and_version_history(api):
    ops, finance, outsider = (bot(api, b) for b in ('ops', 'finance', 'cpo'))
    t = task(api)
    first = publish_page(api, ops, task=t['id'], **{'as': 'Spec'})
    assert first.status_code == 200, first.text
    fid = first.json()['page']['file_id']
    refused = publish_page(api, outsider, 'Unlinked', page=fid)
    assert refused.status_code == 403, refused.text
    linked = add_link(api, fid, 'bot:finance', 'writer')
    assert linked.status_code == 200, linked.text
    second = publish_page(api, finance, 'Two', page=fid)
    assert second.status_code == 200, second.text
    third = publish_page(api, 'cara-test', 'Three', page=fid)
    assert third.status_code == 200, third.text
    assert [(r.json()['page']['file_id'], r.json()['page']['version']) for r in (first, second, third)] == [
        (fid, 1),
        (fid, 2),
        (fid, 3),
    ]
    p = third.json()['page']
    assert p['may_write']
    assert {w['actor'] for w in p['writers']} == {'bot:ops', 'bot:finance', 'human:cara'}
    assert all(w['versions'] == 1 and w['last_at'] for w in p['writers'])
    assert any(l['target'] == 'human:cara' and l['role'] == 'writer' for l in p['links'])
    with api.app.state.store.read() as c:
        assert [
            r[0] for r in c.execute('SELECT actor FROM bot_file_versions WHERE file_id=? ORDER BY version', (fid,))
        ] == ['bot:ops', 'bot:finance', 'human:cara']
        assert c.execute('SELECT count(*) FROM bot_files WHERE id=?', (fid,)).fetchone()[0] == 1
    archived = api.patch('/api/v2/pages/' + fid, json={'archived': True}, headers=headers(finance))
    assert archived.status_code == 200, archived.text
    assert (
        api.patch('/api/v2/pages/' + fid, json={'audience': {'everyone': True}}, headers=headers(finance)).status_code
        == 403
    )


def test_role_publish_resolves_creates_and_reports_ambiguity(api):
    ops = bot(api, 'ops')
    t = task(api)
    first = publish_page(api, ops, task=t['id'], **{'as': 'qa-report'})
    assert first.status_code == 200, first.text
    fid = first.json()['page']['file_id']
    second = publish_page(api, 'ben-test', 'Two', task=t['id'], **{'as': 'QA report'})
    assert second.status_code == 200, second.text
    assert (second.json()['page']['file_id'], second.json()['page']['version']) == (fid, 2)
    other = publish_page(api, ops, 'Other', task=t['id'], name='other.html').json()['page']['file_id']
    assert add_link(api, other, 'task:' + t['id'], 'QA report').status_code == 200
    ambiguous = publish_page(api, ops, 'Three', task=t['id'], **{'as': 'QA report'})
    assert ambiguous.status_code == 409, ambiguous.text
    assert ambiguous.json()['error']['code'] == 'ambiguous'
    assert set(ambiguous.json()['error']['pages']) == {fid, other}
    assert publish_page(api, ops, 'Three', page=fid, task=t['id'], **{'as': 'QA report'}).status_code == 200
    assert publish_page(api, ops, **{'as': 'Spec'}).status_code == 422


def test_import_provenance_is_owner_only_and_audited(api):
    ops = bot(api, 'ops')
    t = task(api)
    for actor in (ops, 'ben-test', 'cara-test'):
        for field in ({'written_by': 'human:ben'}, {'written_at': '2024-01-01T00:00:00Z'}):
            assert publish_page(api, actor, task=t['id'], **field).status_code == 403
    made = publish_page(api, 'ana-test', task=t['id'], written_by='human:ben', written_at='2024-01-01T00:00:00Z')
    assert made.status_code == 200, made.text
    p = made.json()['page']
    assert p['updated_by'] == 'human:ben' and p['updated_at'] == '2024-01-01T00:00:00Z'
    with api.app.state.store.read() as c:
        audit = c.execute(
            'SELECT actor,after FROM audit_log WHERE object=?', ('page:' + p['file_id'] + ':import',)
        ).fetchone()
        assert audit and audit['actor'] == 'human:ana'
    invalid = publish_page(api, 'ana-test', task=t['id'], written_at='yesterday')
    assert invalid.status_code == 422


def test_link_validation_last_writer_filters_and_preview_limit(api):
    ops = bot(api, 'ops')
    t = task(api)
    made = publish_page(api, ops, task=t['id'])
    assert made.status_code == 200, made.text
    fid = made.json()['page']['file_id']
    base = '/api/v2/pages/' + fid + '/links'
    assert api.delete(base, params={'target': 'bot:ops', 'role': 'writer'}, headers=headers()).status_code == 409
    assert add_link(api, fid, 'bot:finance', 'Spec').status_code == 422
    assert add_link(api, fid, 'missing:x').status_code == 422
    assert add_link(api, fid, 'human:ben', 'writer').status_code == 200
    assert api.delete(base, params={'target': 'bot:ops', 'role': 'writer'}, headers=headers()).status_code == 200
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET number=18463,prefix='D' WHERE id=?", (t['id'],))
    assert add_link(api, fid, 'task:D-18463', 'qa-report').status_code == 200
    for who in ('ops', 'finance', 'cpo', 'product-design', 'assistant', 'doc-updater'):
        assert add_link(api, fid, 'bot:' + who).status_code == 200
    for params in (
        {'linked': 'task:' + t['id'], 'role': 'qa-report'},
        {'writer': 'human:ben'},
        {'bot': 'finance'},
        {'task': t['id']},
    ):
        found = api.get('/api/v2/pages', params=params, headers=headers()).json()['pages']
        assert [p['file_id'] for p in found] == [fid]
    assert api.get('/api/v2/pages', params={'writer': 'bot:finance'}, headers=headers()).json()['pages'] == []
    for params in ({'linked': 'task:' + t['id'], 'role': 'QA report'}, {'writer': 'human:ben'}):
        found = api.get('/api/v2/files', params=params, headers=headers()).json()['files']
        assert [p['id'] for p in found] == [fid]
        assert len(found[0]['links']) == 5 and found[0]['links_more'] >= 3


def test_links_never_widen_private_page_or_reveal_private_targets(api):
    ops = bot(api, 'ops')
    secret, public = task(api, private=True, token='ben-test'), task(api)
    made = publish_page(api, ops, task=secret['id'])
    assert made.status_code == 200, made.text
    fid = made.json()['page']['file_id']
    linked = add_link(api, fid, 'task:' + public['id'], 'Spec', token='ben-test')
    assert linked.status_code == 200, linked.text
    public_page = publish_page(api, ops, 'Public', task=public['id'], name='public.html').json()['page']['file_id']
    assert add_link(api, public_page, 'task:' + public['id'], 'Spec').status_code == 200
    for target in ('bot:finance', 'bot:cpo', 'human:ana', 'human:cara'):
        assert add_link(api, public_page, target).status_code == 200
    assert add_link(api, public_page, 'task:' + secret['id'], 'Spec', token='ben-test').status_code == 200
    seen = api.get('/api/v2/pages/' + public_page, headers=headers('cara-test')).json()['page']
    assert not any(l['target'] == 'task:' + secret['id'] for l in seen['links'])
    assert api.get('/api/v2/pages/' + fid, headers=headers('cara-test')).status_code == 404
    for route in (
        '/api/v2/pages?task=' + public['id'],
        '/api/v2/pages?linked=task:' + public['id'],
        '/api/v2/files?linked=task:' + public['id'],
        '/api/v2/files',
        '/api/v2/files/search?q=One',
        '/api/v2/tasks/' + public['id'] + '/files?all=1',
        '/api/v2/tasks/' + public['id'] + '/context',
    ):
        r = api.get(route, headers=headers('cara-test'))
        assert r.status_code == 200, r.text
        assert fid not in r.text, route
    files = api.get('/api/v2/tasks/' + public['id'] + '/files?all=1', headers=headers('ben-test')).json()['files']
    assert any(f['id'] == fid and f['links'] for f in files)
    files = api.get('/api/v2/tasks/' + public['id'] + '/files?all=1', headers=headers('cara-test')).json()['files']
    assert [f['id'] for f in files] == [public_page]
    library = api.get('/api/v2/files', params={'linked': 'task:' + public['id']}, headers=headers('cara-test'))
    [preview] = library.json()['files']
    assert len(preview['links']) == 5 and preview['links_more'] == 2
    assert not any(l.get('approval') for l in preview['links'])
    context = api.get('/api/v2/tasks/' + public['id'] + '/context', headers=headers('ben-test')).json()['pages']
    assert any(p['id'] == fid and p['links'] for p in context)
    other_page = publish_page(api, ops, 'Other public', task=public['id'], name='other.html').json()['page']['file_id']
    assert add_link(api, other_page, 'task:' + public['id'], 'Spec').status_code == 200
    refusals = [
        publish_page(api, 'cara-test', task=public['id'], **{'as': 'Spec'}),
        api.post(
            '/api/v2/tasks/' + public['id'] + '/files',
            json={'document': 'Spec', 'text': '<p>Spec</p>'},
            headers=headers('cara-test'),
        ),
    ]
    for refused in refusals:
        assert refused.status_code == 409, refused.text
        assert set(refused.json()['error']['pages']) == {public_page, other_page}


def test_page_writers_can_continue_after_the_storage_bot_is_archived(api):
    from backend.settings_admin import archive_bot

    ops, finance = bot(api, 'ops'), bot(api, 'finance')
    t = task(api)
    made = publish_page(api, ops, task=t['id'])
    assert made.status_code == 200, made.text
    fid = made.json()['page']['file_id']
    assert add_link(api, fid, 'human:ben', 'writer').status_code == 200
    assert add_link(api, fid, 'bot:finance', 'writer').status_code == 200
    with api.app.state.store.transaction() as c:
        archive_bot(c, 'human:ana', 'ops', successor='finance')
    for version, actor in enumerate(('ben-test', finance), 2):
        read = api.get('/api/v2/pages/' + fid, headers=headers(actor))
        assert read.status_code == 200 and read.json()['page']['may_write']
        written = publish_page(api, actor, f'Content {version}', page=fid)
        assert written.status_code == 200, written.text
        page = written.json()['page']
        assert (page['file_id'], page['bot_id'], page['version']) == (fid, 'ops', version)


def test_plain_upload_does_not_overwrite_linked_document(api):
    ops = bot(api, 'ops')
    t = task(api)
    made = publish_page(api, ops, task=t['id'], **{'as': 'Spec'})
    assert made.status_code == 200, made.text
    fid = made.json()['page']['file_id']
    # The old document registry is deliberately empty: only the page role protects this series.
    with api.app.state.store.transaction() as c:
        c.execute('DELETE FROM task_documents WHERE file_id=?', (fid,))
    uploaded = api.post(
        '/api/v2/tasks/' + t['id'] + '/files',
        files={'file': ('spec.html', b'<p>Attachment</p>', 'text/html')},
        headers=headers(),
    )
    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json()['file_id'] != fid
    with api.app.state.store.read() as c:
        assert c.execute('SELECT current_version FROM bot_files WHERE id=?', (fid,)).fetchone()[0] == 1


def test_page_list_uses_fixed_number_of_queries(api):
    from backend.tests.helpers.cards import page as seed_page

    t = task(api)
    ids = [seed_page(api, 'ops', title='Page ' + str(i), task=t['id']) for i in range(50)]
    store, pages = api.app.state.store, api.app.state.pages
    who = Identity('human:ana', 'owner', 'ana@acme.example')
    with store.transaction() as c:
        for fid in ids:
            L.link(c, fid, 'bot:ops', 'writer', who.actor)
            L.link(c, fid, 'human:ben', 'writer', who.actor)
            L.link(c, fid, 'task:' + t['id'], 'related', who.actor)

    def count(wanted, reader):
        with store.read_transaction() as c:
            statements = []
            c.set_trace_callback(statements.append)
            # Use one snapshot so this measures the list's actual neutral batching.
            rows = list(pages.rows(c, wanted).values())
            from backend.files import prime_visible
            from backend.pages import prime_views

            prime_visible(c, rows)
            prime_views(c, rows)
            known = pages._authors(c, rows)
            assert known == set()
            cache = {}
            for row in rows:
                value = pages.view(c, reader, row, pages.page_of(row), cache, {'bot:ops': 'Ops'})
                assert len(value['links']) == 3
                assert value['may_write'] == (reader.role == 'owner')
            c.set_trace_callback(None)
        return [q for q in statements if q.lstrip().upper().startswith(('SELECT', 'WITH'))]

    for reader in (who, Identity('bot:finance', 'bot')):
        small, large = count(ids[:1], reader), count(ids, reader)
        assert len(large) <= len(small) + 2, (reader.actor, len(small), len(large), large)


def test_publish_submits_each_linked_document_requirement(api):
    from backend.tests.helpers import engine as X
    from backend import task_engine as E

    with api.app.state.store.transaction() as c:
        X.pipeline(
            c,
            "page-publish",
            "Publish",
            (
                {
                    "name": "Spec",
                    "owner": "bot:ops",
                    "requirements": [
                        {"kind": "page", "line": "Spec", "settings": {"document": "Spec"}, "who_oks": "none"}
                    ],
                },
            ),
            manager="human:ana",
        )
        t = X.new_task(c, X.ANA, "Publish the spec", "page-publish", assignee="bot:ops")
    made = publish_page(api, 'ana-test', task=t['id'], **{'as': 'Spec'})
    assert made.status_code == 200, made.text
    fid = made.json()['page']['file_id']
    with api.app.state.store.read() as c:
        current = E.task(c, t['id'])
        submitted = c.execute(
            "SELECT file_id,version FROM requirement_pages WHERE visit_id=? AND replaced_at IS NULL",
            (current['visit']['id'],),
        ).fetchall()
        assert [(r['file_id'], r['version']) for r in submitted] == [(fid, 1)]
    revised = publish_page(api, 'ana-test', 'Revised', page=fid)
    assert revised.status_code == 200, revised.text
    with api.app.state.store.read() as c:
        submitted = c.execute(
            "SELECT file_id,version FROM requirement_pages WHERE visit_id=? AND replaced_at IS NULL",
            (current['visit']['id'],),
        ).fetchall()
        assert [(r['file_id'], r['version']) for r in submitted] == [(fid, 2)]


def test_search_filters_and_page_relationship_previews(api):
    ops = bot(api, 'ops')
    one, two = task(api), task(api, title='Other pages')
    first = publish_page(api, ops, 'orbitalcraft', task=one['id'], **{'as': 'Spec'})
    assert first.status_code == 200, first.text
    fid = first.json()['page']['file_id']
    other = publish_page(api, ops, 'orbitalcraft', task=two['id'])
    assert other.status_code == 200, other.text
    assert add_link(api, fid, 'human:ben', 'writer').status_code == 200
    for name in ('finance', 'cpo', 'assistant', 'doc-updater'):
        assert add_link(api, fid, 'bot:' + name).status_code == 200
    for filters in (
        {'linked': 'task:' + one['id']},
        {'role': 'Spec'},
        {'writer': 'human:ben'},
        {'linked': 'task:' + one['id'], 'role': 'Spec', 'writer': 'human:ben'},
    ):
        shown = api.get('/api/v2/files/search', params={'q': 'orbitalcraft', **filters}, headers=headers())
        assert shown.status_code == 200, shown.text
        [hit] = shown.json()['results']
        assert hit['id'] == fid and len(hit['links']) == 5 and hit['links_more'] == 2
        assert [w['actor'] for w in hit['writers']] == ['bot:ops']
    assert (
        api.get(
            '/api/v2/files/search', params={'q': 'orbitalcraft', 'writer': 'bot:finance'}, headers=headers()
        ).json()['results']
        == []
    )


def test_existing_page_writers_do_not_need_an_active_assistant(api):
    ops = bot(api, 'ops')
    t = task(api)
    made = publish_page(api, ops, task=t['id'], **{'as': 'Spec'})
    assert made.status_code == 200, made.text
    fid = made.json()['page']['file_id']
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET state='paused' WHERE id='assistant'")
    second = publish_page(api, 'cara-test', 'Second', page=fid)
    assert second.status_code == 200, second.text
    third = publish_page(api, 'cara-test', 'Third', task=t['id'], **{'as': 'Spec'})
    assert third.status_code == 200, third.text
    assert (third.json()['page']['file_id'], third.json()['page']['version']) == (fid, 3)
    assert publish_page(api, 'cara-test', task=t['id'], name='new.html').status_code == 409


def test_runner_updates_page_as_the_bot_of_its_own_turn(api):
    from backend.tests.helpers.files import turn

    ops = bot(api, 'ops')
    t = task(api)
    made = publish_page(api, ops, task=t['id'])
    assert made.status_code == 200, made.text
    fid = made.json()['page']['file_id']
    assert add_link(api, fid, 'bot:finance', 'writer').status_code == 200
    machine, attempt = turn(api, bot='finance')
    fields = {'name': 'revised.html', 'text': '<p>Revised</p>', 'page': fid, 'bot': 'finance', 'attempt': attempt['id']}
    revised = api.post('/api/v2/pages', json=fields, headers=headers(machine['token']))
    assert revised.status_code == 200, revised.text
    p = revised.json()['page']
    assert p['file_id'] == fid and p['version'] == 2 and p['updated_by'] == 'bot:finance' and p['may_write']
    fields.pop('attempt')
    assert api.post('/api/v2/pages', json=fields, headers=headers(machine['token'])).status_code == 403
