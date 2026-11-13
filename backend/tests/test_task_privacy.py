"""Two-party privacy, transparency, defaults and conservative upgrade boundaries."""

import json

import pytest

from backend import hubdb as H
from backend import task_relations as TR
from backend.tests.helpers.tasks_board import api, bot_token, get, headers, post
from backend import bot_settings as BS


@pytest.mark.parametrize('requester,owner', [('human:ben', 'bot:cpo')])
def test_private_two_party_matrix(api, requester, owner):
    tokens = {
        'human:ana': 'ana-test',
        'human:ben': 'ben-test',
        'human:priya': 'priya-test',
        **{'bot:' + slug: bot_token(api, slug) for slug in ('ops', 'cpo', 'cmo')},
    }
    with api.app.state.store.transaction() as c:
        task = H.task_create(
            c,
            requester,
            'Review the sensitive packet',
            'Sensitive packet.',
            owner,
            private=True,
            lint=False,
        )
        c.execute(
            'INSERT INTO task_delegations(task_id,delegate,requested_by,message_id,expires_at) VALUES(?,?,?,?,?)',
            (task['id'], 'bot:cmo', requester, None, H.shift(H.now(), hours=24)),
        )

    def check(readers):
        for actor, token in tokens.items():
            response = api.get('/api/v2/tasks/' + task['id'], headers=headers(token))
            assert response.status_code == (200 if actor in readers else 404), (actor, response.text)
            listed = get(api, 'tasks', token=token)['tasks']
            assert (task['id'] in {row['id'] for row in listed}) == (actor in readers), actor
            response = api.post('/api/v2/sql', json={'sql': 'SELECT id,title FROM tasks'}, headers=headers(token))
            assert response.status_code == 200, response.text
            assert ('Sensitive packet' in response.text or task['id'] in response.text) == (actor in readers), actor

    # Ana is the chart's root above cpo even when she is not a task party.
    parties = {requester, owner, 'human:ana'}
    check(parties)
    # A person and a bot on the reader list read it; nobody else does.
    path = 'tasks/' + task['id'] + '/readers'
    post(api, path, {'add': ['priya', 'bot:ops']}, token='ben-test')
    check(parties | {'human:priya', 'bot:ops'})
    # A group reads it through whoever is in the group when they look.
    gid = post(api, 'groups', {'name': 'Packet desk', 'add': {'people': ['priya'], 'bots': ['cmo']}})['id']
    post(api, path, {'add': ['group:' + gid], 'remove': ['priya', 'bot:ops']}, token='ben-test')
    check(parties | {'human:priya', 'bot:cmo'})
    r = api.patch('/api/v2/groups/' + gid, json={'remove': {'people': ['priya']}}, headers=headers())
    assert r.status_code == 200, r.text
    check(parties | {'bot:cmo'})


def test_company_read_does_not_grant_work(api):
    """A bot reads a company task it is not on, and does not work it. (A pipeline has no setting that lets any bot work
    its tasks, as the old task type's `bots: work` did: who works a task is task_calls.can_work.)"""
    token = bot_token(api, 'ops')
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Build the board adapter', 'body': 'Implement the adapter.'})
    assert not task['private']
    assert get(api, 'tasks/' + task['id'], token=token)['task']['id'] == task['id']
    assert task['id'] in {row['id'] for row in get(api, 'tasks?pipeline=general', token=token)['tasks']}
    post(
        api,
        'tasks/' + task['id'] + '/owner',
        {'version': task['version'], 'owner': 'ops', 'why': 'Mine now'},
        token=token,
        expected=403,
    )
    post(
        api,
        'tasks/' + task['id'] + '/move',
        {'version': task['version'], 'to': 'done', 'why': 'Built'},
        token=token,
        expected=403,
    )
    task = post(api, 'tasks/' + task['id'], {'version': task['version'], 'private': True})
    get(api, 'tasks/' + task['id'], token=token, expected=404)
    assert task['id'] not in {row['id'] for row in get(api, 'tasks?pipeline=general', token=token)['tasks']}


def test_private_defaults_reassignment_and_human_publication(api):
    ops = bot_token(api, 'ops')
    cpo = bot_token(api, 'cpo')
    with api.app.state.store.transaction() as c:
        for slug in ('ops', 'cpo'):
            config = BS.read(c, slug)
            config['private_tasks_default'] = True
            BS.write(c, slug, config)
    assigned = post(api, 'tasks', {'owner': 'ops', 'title': 'Review the request', 'body': 'Review it.'})
    created = post(
        api, 'tasks', {'owner': 'cmo', 'title': 'Draft the response', 'body': 'Draft it.', 'private': False}, token=cpo
    )
    assert assigned['private'] and created['private']
    explicit = post(
        api, 'tasks', {'owner': 'ops', 'title': 'Review the public request', 'body': 'Review it.', 'private': False}
    )
    assert not explicit['private']
    post(api, 'tasks/' + assigned['id'], {'version': assigned['version'], 'private': False}, token=ops, expected=422)
    changed = post(
        api,
        'tasks/' + assigned['id'] + '/owner',
        {'version': assigned['version'], 'owner': 'cpo', 'why': 'Cpo reviews'},
    )
    assert changed['private']
    get(api, 'tasks/' + assigned['id'], token=ops, expected=404)
    assert get(api, 'tasks/' + assigned['id'], token=cpo)['task']['private']
    published = post(api, 'tasks/' + assigned['id'], {'version': changed['version'], 'private': False})
    assert not published['private']
    assert get(api, 'tasks/' + assigned['id'], token=ops)['task']['id'] == assigned['id']
    post(api, 'tasks/' + created['id'], {'version': created['version'], 'private': False}, token=cpo, expected=422)


def test_a_private_parent_gives_no_ancestry_bypass_and_its_sub_task_stays_private(api):
    parent = post(api, 'tasks', {'owner': 'ben', 'title': 'Review the packet', 'body': 'Review it.', 'private': True})
    child = post(
        api,
        'tasks',
        {'owner': 'priya', 'title': 'Check the packet', 'body': 'Check it.', 'parent': parent['id']},
        token='ben-test',
    )
    assert child['private'] and child['requester_id'] == 'human:ben'
    get(api, 'tasks/' + child['id'], expected=404)
    detail = get(api, 'tasks/' + parent['id'])
    assert not detail['children'] and detail['task']['parts']['total'] == 0
    # Its parent never changes, so a sub-task of a private task stays private.
    post(api, 'tasks/' + child['id'], {'version': child['version'], 'private': False}, token='ben-test', expected=422)


def test_cached_comment_response_cannot_bypass_reassignment(api):
    task = post(
        api, 'tasks', {'owner': 'ben', 'title': 'Review the private draft', 'body': 'Review it.', 'private': True}
    )
    replay_headers = headers('ben-test')
    url = '/api/v2/tasks/' + task['id'] + '/comments'
    first = api.post(url, json={'text': 'Sensitive response.'}, headers=replay_headers)
    assert first.status_code == 200, first.text
    task = get(api, 'tasks/' + task['id'])['task']
    post(api, 'tasks/' + task['id'] + '/owner', {'version': task['version'], 'owner': 'priya', 'why': 'Priya reviews'})
    response = api.post(url, json={'text': 'Sensitive response.'}, headers=replay_headers)
    assert response.status_code == 404 and 'Sensitive response.' not in response.text


def test_cloud_upgrade_classifies_legacy_identity_and_keeps_files_intact(api):
    ordinary = post(api, 'tasks', {'owner': 'cmo', 'title': 'Review ordinary work', 'body': 'Ordinary content.'})
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Review the older work', 'body': 'Older content.'})
    child = post(
        api,
        'tasks',
        {
            'owner': 'ben',
            'title': 'Review related work',
            'body': 'Related content.',
            'parent': task['id'],
        },
    )
    routine = post(api, 'tasks', {'owner': 'cmo', 'title': 'Review scheduled work', 'body': 'Scheduled content.'})
    orphan = post(api, 'tasks', {'owner': 'priya', 'title': 'Review orphaned work', 'body': 'Orphan content.'})
    post(api, 'tasks/' + task['id'] + '/files', {'name': 'legacy-brief.md', 'text': 'Legacy attachment bytes.'})
    with api.app.state.store.transaction() as c:
        counts = {
            name: c.execute('SELECT count(*) FROM ' + name).fetchone()[0]
            for name in ('tasks', 'blobs', 'task_assets', 'bot_files')
        }
        BS.write(c, 'cpo', {'template': 'general-counsel'})
        c.execute("UPDATE tasks SET requester_id='human:missing' WHERE id=?", (orphan['id'],))
        c.execute("UPDATE tasks SET requester_id='keeper' WHERE id=?", (routine['id'],))
        # A database from before task privacy had no change log (backend/events.py) or parties counter (backend/row_clock.py).
        for (name,) in c.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND "
            "(name LIKE 'changes_%' OR name LIKE 'row_clock_parties_%')"
        ).fetchall():
            c.execute('DROP TRIGGER ' + name)
        c.execute('DROP INDEX IF EXISTS tasks_private_parties')
        c.execute('DROP INDEX IF EXISTS tasks_private_status')
        c.execute('DROP INDEX IF EXISTS tasks_queue_cover')
        c.execute('DROP INDEX IF EXISTS tasks_owner_status_cover')
        c.execute('DROP INDEX IF EXISTS tasks_requester_status_cover')
        # Needs you's open-task index (I39) names private too, and came later: the upgrade makes it again.
        c.execute('DROP INDEX IF EXISTS tasks_open_pipeline')
        c.execute("DELETE FROM schema_migrations WHERE version='20261112080000_needs_you_per_person'")
        c.execute('ALTER TABLE tasks DROP COLUMN private')
        c.execute('DELETE FROM cloud_migrations WHERE version=57')
    api.app.state.store.initialize(seed_market=False)
    with api.app.state.store.read() as c:
        assert H.task(c, task['id'])['private'] == 1
        assert H.task(c, child['id'])['private'] == 1
        assert H.task(c, orphan['id'])['private'] == 1
        assert H.task(c, ordinary['id'])['private'] == 0
        assert H.task(c, routine['id'])['private'] == 0
        assert c.execute('SELECT 1 FROM cloud_migrations WHERE version=57').fetchone()
        assert c.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='tasks_open_pipeline'").fetchone()
        assert all(c.execute('SELECT count(*) FROM ' + name).fetchone()[0] == count for name, count in counts.items())
        assert c.execute('SELECT 1 FROM cloud_migrations WHERE version=53').fetchone()
    # Task reads name tasks_private_status: the upgrade's first start makes it, not only the next restart.
    get(api, 'tasks')
    api.app.state.store.initialize(seed_market=False)
    assert get(api, 'tasks/' + task['id'])['task']['private']


def test_private_dependency_and_refusal_audit_never_copy_sensitive_content(api):
    private = post(
        api, 'tasks', {'owner': 'cpo', 'title': 'Review sensitive evidence', 'body': 'Review it.', 'private': True}
    )
    public = post(api, 'tasks', {'owner': 'cmo', 'title': 'Review release timing', 'body': 'Review it.'})
    # A task waits on no relation: blocked_by is no kind of one.
    post(api, 'tasks/' + public['id'] + '/relations', {'task': private['id'], 'kind': 'blocked_by'}, expected=422)
    with api.app.state.store.transaction() as c:
        with pytest.raises(H.Refused) as exc:
            H.task_move(c, 'bot:cpo', private['id'], 'next', 'Read SECRET-PACKET', 0)
        assert exc.value.private
        audit = c.execute("SELECT detail FROM events WHERE action='refused'").fetchall()
        assert all('SECRET-PACKET' not in row[0] for row in audit)


def test_bot_acting_for_human_cannot_publish_in_domain(api):
    with api.app.state.store.transaction() as c:
        task = H.task_create(
            c, 'human:ben', 'Review confidential work', 'Review it.', 'bot:cpo', private=True, lint=False
        )
        token = H.VIA.set('assistant')
        try:
            with pytest.raises(H.Refused):
                H.task_update(c, 'human:ben', task['id'], private=False)
        finally:
            H.VIA.reset(token)
        assert H.task_private(c, H.task(c, task['id']))


def test_acted_duplicate_diagnostics_keep_private_ids_out_of_responses_and_audit(api):
    with api.app.state.store.transaction() as c:
        private = H.task_create(
            c, 'human:ben', 'Review confidential work', 'Review it.', 'bot:cpo', private=True, lint=False
        )
        ordinary = H.task_create(c, 'human:ben', 'Review ordinary work', 'Review it.', 'bot:cpo', lint=False)
        token = H.VIA.set('assistant')
        try:
            for write in (
                lambda: H.task_create(c, 'human:ben', private['title'], 'Review it.', 'bot:cpo', lint=False),
                lambda: H.task_update(c, 'human:ben', ordinary['id'], title=private['title']),
            ):
                with pytest.raises(H.Refused) as refused:
                    write()
                assert private['id'] not in str(refused.value)
            with pytest.raises(H.Refused) as public_duplicate:
                H.task_create(c, 'human:ben', ordinary['title'], 'Review it.', 'bot:cpo', lint=False)
            assert ordinary['id'] in str(public_duplicate.value)
        finally:
            H.VIA.reset(token)
        diagnostics = c.execute("SELECT detail FROM events WHERE action='refused'").fetchall()
        assert private['id'] not in str([tuple(row) for row in diagnostics])


def test_native_hub_upgrade_from_23_is_atomic_and_preserves_tasks_messages(tmp_path):
    path = tmp_path / 'native-hub.db'
    c = H.connect(path)
    H.sync_registry(
        c, {}, {'people': [{'id': 'ana', 'email': 'ana@acme.example'}, {'id': 'ben', 'email': 'ben@acme.example'}]}
    )
    ordinary = H.task_create(c, 'human:ana', 'Review ordinary work', 'Review it.', 'human:ben', lint=False)
    orphan = H.task_create(c, 'human:ana', 'Review orphan work', 'Review it.', 'human:ben', lint=False)
    comment = H.task_comment(c, 'human:ana', orphan['id'], 'Preserved tracked comment.', wake=False)
    c.execute("UPDATE tasks SET requester_id='human:missing' WHERE id=?", (orphan['id'],))
    c.execute('DROP INDEX IF EXISTS tasks_private_parties')
    c.execute('DROP INDEX IF EXISTS tasks_private_status')
    c.execute('DROP INDEX IF EXISTS tasks_queue_cover')
    c.execute('DROP INDEX IF EXISTS tasks_owner_status_cover')
    c.execute('DROP INDEX IF EXISTS tasks_requester_status_cover')
    # Needs you's open-task index (I39) names private too, and came later: the upgrade makes it again.
    c.execute('DROP INDEX IF EXISTS tasks_open_pipeline')
    c.execute("DELETE FROM schema_migrations WHERE version='20261112080000_needs_you_per_person'")
    c.execute('ALTER TABLE tasks DROP COLUMN private')
    c.execute('PRAGMA user_version=23')
    ids = {row[0] for row in c.execute('SELECT id FROM messages')}
    c.close()
    c = H.connect(path)
    try:
        assert c.execute('PRAGMA user_version').fetchone()[0] == len(H.MIGRATIONS)
        assert not H.task_private(c, H.task(c, ordinary['id']))
        assert H.task_private(c, H.task(c, orphan['id']))
        assert {row[0] for row in c.execute('SELECT id FROM messages')} == ids
        assert H.message(c, comment['id'])['body'] == 'Preserved tracked comment.'
        H.migrate(c)
        assert c.execute('PRAGMA foreign_key_check').fetchall() == []
    finally:
        c.close()


def test_a_visuals_line_link_to_a_private_task_message_opens_only_for_its_readers(api):
    """The "N visuals - view in Tico" link names a task and a message, nothing else (backend/visuals.py elsewhere).
    What it opens is the task as the reader may see it: its readers get the message, anyone else not-found."""
    from urllib.parse import parse_qs, urlsplit
    from backend import visuals

    task = post(api, 'tasks', {'owner': 'ben', 'title': 'Board pack', 'body': 'Chart the quarter.', 'private': True})
    with api.app.state.store.transaction() as c:
        cid = H.task(c, task['id'])['conversation_id']
        said = H.say(
            c,
            'human:ben',
            'human:ana',
            'Quarter attached, revenue up.',
            conversation_id=cid,
            refs={'task': task['id'], 'attachments': [{'file_id': 'f1', 'version': 1, 'kind': 'image', 'title': 'Q3'}]},
        )
        message = H.message(c, said['id'])
    line = visuals.elsewhere('https://tico.example.com', message, task['id'])
    assert line['label'] == '1 visual - view in Tico'
    query = parse_qs(urlsplit(line['url']).query)
    assert query == {'task': [task['id']], 'message': [said['id']]}
    for token, readable in (('ana-test', True), ('ben-test', True), ('priya-test', False)):
        response = api.get('/api/v2/tasks/' + query['task'][0], headers=headers(token))
        assert response.status_code == (200 if readable else 404), response.text
        assert ('revenue up' in response.text) == readable and (said['id'] in response.text) == readable


def test_a_pipeline_without_a_manager_cannot_be_made_private(api):
    """A private pipeline's tasks are read by its manager and the company's owner (backend/private_pipelines.py), so
    one with no manager would promise readers it does not have: refused, by the route and by the record's one writer,
    on a new pipeline and on General; nothing is saved. backend/tests/test_private_pipelines.py has the rest."""
    from backend import pipeline_settings as PS

    with api.app.state.store.transaction() as c:  # Ana owns General here, so she may change it
        c.execute("UPDATE pipelines SET owner_id='human:ana' WHERE id='general'")
    record = get(api, 'pipelines/general')['pipeline']
    assert record['may_change']
    legal = {
        'name': 'Legal',
        'goal': 'Contracts',
        'private': True,
        'stages': [{'name': 'Review', 'owner': 'assignee', 'requirements': [{'kind': 'owner_done'}]}],
    }
    # Without the switch the same new pipeline saves: the refusal is the switch's alone.
    with api.app.state.store.read() as c:
        assert PS.check(c, {**legal, 'private': False}) == []
    r = api.post('/api/v2/pipelines/general', json={**record, 'private': True}, headers=headers())
    assert r.status_code == 409 and "A private pipeline needs a manager" in r.text, r.text
    with api.app.state.store.transaction() as c:
        for body in ({**PS.read(c, 'general'), 'private': True}, {**legal, 'id': 'legal'}):
            with pytest.raises(H.Refused) as refused:
                PS.save(c, H.KEEPER, body)
            assert "A private pipeline needs a manager" in str(refused.value)
        assert not c.execute('SELECT 1 FROM pipelines WHERE private<>0').fetchone()
        assert not c.execute("SELECT 1 FROM pipelines WHERE id='legal'").fetchone()
    assert get(api, 'pipelines/general')['pipeline']['private'] is False
