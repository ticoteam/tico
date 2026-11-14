"""What the roadmap keeps while the public roadmap is off (backend/roadmaps.py, backend/public_roadmaps.py): a task's
public text and who may write it, its requesters, a visitor's token that takes their details back, the guard on the
public routes, and the settings. The public pages and API answer "not available yet"."""

import hashlib
import json

import pytest

from backend import hubdb as H
from backend import roadmaps as R
from backend.tests.helpers.tasks_board import api, get, headers, post  # noqa: F401
from backend import bot_settings as BS

ORIGIN = {'Origin': 'https://acme.example'}


def _settings(api, limit=10):
    post(api, 'tags', {'key': 'roadmap'})
    post(
        api,
        'roadmaps/settings',
        {'labels': ['roadmap'], 'cors_origins': [ORIGIN['Origin']], 'action_limit': limit},
    )


def _public_task(api, limit=10):
    """A task with public text and the roadmap's label."""
    _settings(api, limit)
    return post(
        api,
        'tasks',
        {
            'owner': 'cpo',
            'title': 'Internal title',
            'body': 'Internal summary',
            'labels': ['roadmap'],
            'visibility': 'public',
            'public_title': 'Roadmap item',
            'public_summary': 'What it does',
        },
    )


def test_the_public_roadmap_is_not_available_yet_and_its_settings_stay(api):
    task = _public_task(api)
    base = '/public/roadmaps/roadmap/tasks/' + task['id']
    answers = [
        api.get('/public/roadmaps/roadmap', headers=ORIGIN),
        api.get(base, headers=ORIGIN),
        api.get(base + '/comments', headers=ORIGIN),
        api.post('/public/roadmaps/roadmap/suggest', json={'title': 'Exports'}, headers=ORIGIN),
        api.post(base + '/requesters', json={'email': 'customer@acme.example'}, headers=ORIGIN),
        api.post(base + '/comments', json={'text': 'Please add this'}, headers=ORIGIN),
    ]
    for r in answers:
        assert r.status_code == 404 and r.json()['error']['detail'] == R.NOT_YET, r.text
        assert 'Internal' not in r.text and 'Roadmap item' not in r.text
    assert api.head('/public/roadmaps/roadmap').status_code == 404
    ranked = api.get('/api/v2/roadmap-tasks', headers=headers())
    assert ranked.status_code == 404 and ranked.json()['error']['detail'] == R.NOT_YET
    with api.app.state.store.read() as c:
        assert c.execute('SELECT count(*) FROM tasks').fetchone()[0] == 1  # a suggestion files nothing
        assert not c.execute('SELECT 1 FROM task_requesters').fetchone()
        assert not c.execute("SELECT 1 FROM messages WHERE json_extract(refs,'$.source')='public_roadmap'").fetchone()
    settings = get(api, 'roadmaps/settings')
    assert settings['labels'] == ['roadmap'] and settings['cors_origins'] == [ORIGIN['Origin']]
    post(api, 'roadmaps/settings', {'action_limit': 20})
    assert get(api, 'roadmaps/settings')['action_limit'] == 20
    post(api, 'roadmaps/settings', {'labels': ['roadmap'], 'cors_origins': ['*']}, expected=422)


def test_visibility_defaults_do_not_rewrite_tasks_and_private_never_publishes(api):
    draft = post(api, 'tasks', {'owner': 'cpo', 'title': 'Research search', 'body': 'Internal draft'})
    assert draft['visibility'] == 'private' and draft['public_title'] == '' and draft['public_summary'] == ''
    refused = post(
        api,
        'tasks',
        {'owner': 'cpo', 'title': 'Research filters', 'body': 'Internal draft', 'visibility': 'public'},
        expected=422,
    )
    assert 'public title' in refused['error']['detail']
    post(api, 'tasks/' + draft['id'], {'version': draft['version'], 'visibility': 'public'}, expected=422)
    # There are no task types to default to public any more: public text alone keeps the task private.
    unasked = post(
        api,
        'tasks',
        {'owner': 'cpo', 'title': 'Research sorting', 'body': 'Internal draft', 'public_title': 'Sorting'},
    )
    assert unasked['visibility'] == 'private'
    task = post(
        api,
        'tasks',
        {
            'owner': 'cpo',
            'title': 'Research search again',
            'body': 'Internal draft',
            'visibility': 'public',
            'public_title': 'Better search',
            'public_summary': 'Find more',
        },
    )
    assert (task['visibility'], task['public_title'], task['public_summary']) == (
        'public',
        'Better search',
        'Find more',
    )
    changed = post(api, 'tasks/' + task['id'], {'version': task['version'], 'body': 'Internal revision'})
    assert changed['public_summary'] == 'Find more'
    private = post(api, 'tasks', {'owner': 'cpo', 'title': 'Sensitive research', 'body': 'Secret', 'private': True})
    post(api, 'tasks/' + private['id'], {'version': private['version'], 'visibility': 'public'}, expected=422)
    child = post(
        api,
        'tasks',
        {
            'owner': 'cpo',
            'title': 'Review a part',
            'body': 'Part',
            'visibility': 'public',
            'public_title': 'A part',
            'parent': private['id'],
        },
    )
    assert get(api, 'tasks/' + task['id'])['task']['visibility'] == 'public'
    assert get(api, 'tasks/' + child['id'])['task']['visibility'] == 'private'


def test_a_bot_has_no_way_to_publish_yet_and_is_told_so(api):
    """Codex review 2 of the asks track, finding 9: the publication preview handed a bot a payload no route could ask
    an OK for. Until the roadmap ships, the preview answers as the public routes do, the generic OK route refuses a
    publish subject (its module asks it, and has no ask path yet), and public text is refused with the same words."""
    from backend.tests.helpers.tasks_board import bot_token

    token = bot_token(api, 'cpo')
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Improve search', 'body': 'Draft'})
    changes = {'visibility': 'public', 'public_title': 'Search', 'public_summary': 'Search improvements'}
    for body in (changes, {'comment': 'Now available'}, {'labels': ['roadmap']}):
        r = api.post('/api/v2/tasks/' + task['id'] + '/publication-preview', json=body, headers=headers(token))
        assert r.status_code == 404 and R.NOT_YET in r.text, r.text
    asked = api.post(
        '/api/v2/tasks/' + task['id'] + '/oks',
        json={
            'to': 'ana',
            'line': 'OK to publish?',
            'subject': R.publication_subject(task['id']),
            'content': R.publication_payload(task['id'], 'text', changes),
            'version': task['version'],
        },
        headers=headers(token),
    )
    assert asked.status_code in (403, 422) and 'publication-preview' not in asked.text, asked.text
    refused = post(api, 'tasks/' + task['id'], {'version': task['version'], **changes}, token=token, expected=422)
    assert 'publication-preview' not in json.dumps(refused)
    with api.app.state.store.read() as c:
        assert c.execute('SELECT count(*) FROM ok_grants').fetchone()[0] == 0


def test_requester_contacts_follow_task_privacy_and_teammates_can_remove_themselves(api):
    private = post(
        api, 'tasks', {'owner': 'cpo', 'title': 'Review private feedback', 'body': 'Sensitive', 'private': True}
    )
    post(api, 'tasks/' + private['id'] + '/requesters', {'email': 'sensitive@acme.example'})
    get(api, 'tasks/' + private['id'] + '/requesters', token='ben-test', expected=404)
    result = post(api, 'sql', {'sql': 'SELECT * FROM task_requesters'}, token='ben-test')
    assert 'sensitive@acme.example' not in str(result)
    result = post(api, 'sql', {'sql': 'SELECT * FROM task_requesters'})
    assert 'token_hash' not in str(result)
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Improve editing', 'body': 'Feedback'})
    external = post(
        api,
        'tasks/' + task['id'] + '/requesters',
        {'email': 'customer@acme.example', 'name': 'Customer', 'source': 'meeting'},
    )
    from backend.tests.helpers.mcp import call

    failed, result = call(
        api, 'tico_task_requester_save', {'id': task['id'], 'requester_id': external['id'], 'important': True}
    )
    assert not failed, result
    changed = get(api, 'tasks/' + task['id'] + '/requesters')['requesters'][0]
    assert changed['important'] and changed['email'] == 'customer@acme.example'
    assert changed['name'] == 'Customer' and changed['source'] == 'meeting'
    post(api, 'tasks/' + task['id'] + '/requesters/' + external['id'], {}, expected=422)
    post(api, 'tasks/' + task['id'] + '/requesters/' + external['id'] + '/delete', {})
    sized = post(api, 'tasks/' + task['id'], {'version': task['version'], 'size': 7.5})
    assert get(api, 'tasks/' + task['id'])['task']['size'] == 7.5
    post(api, 'tasks/' + task['id'], {'version': sized['version'], 'size': None})
    assert get(api, 'tasks/' + task['id'])['task']['size'] is None
    own = post(api, 'tasks/' + task['id'] + '/requesters', {'actor': 'human:priya'}, token='priya-test')
    with api.app.state.store.read() as c:
        assert not c.execute(
            'SELECT 1 FROM idempotency WHERE instr(response,?)>0', (own['requester_token'],)
        ).fetchone()
    post(api, 'tasks/' + task['id'] + '/requesters/' + own['id'] + '/delete', {}, token='priya-test')
    assert get(api, 'tasks/' + task['id'] + '/requesters')['requester_count'] == 0


def test_a_bot_files_no_public_text_without_an_approved_publication(api):
    from backend.tests.helpers.tasks_board import bot_token

    _settings(api)
    token = bot_token(api, 'cpo')
    for identity in (token, 'ben-via-botops'):
        task_body = {'owner': 'cpo', 'title': 'Research requested exports', 'body': 'customer@acme.example'}
        # Public text from a bot is refused, not silently dropped: it goes through an approved publication.
        for extra in ({'visibility': 'public'}, {'public_title': 'Exports'}, {'public_summary': 'Soon'}):
            refused = api.post('/api/v2/tasks', json={**task_body, **extra}, headers=headers(identity))
            assert refused.status_code == 422 and refused.json()['error']['code'] == 'approval', refused.text
        if identity == token:
            # A bot also creates no task carrying a public roadmap label (test_task_labels); it adds it on the task.
            refused = post(api, 'tasks', {**task_body, 'labels': ['roadmap']}, token=identity, expected=403)
            assert refused['error']['code'] == 'label'
        created = post(api, 'tasks', task_body, token=identity)
        assert created['visibility'] == 'private' and created['public_title'] == ''


def test_internal_text_never_becomes_public_text(api):
    _settings(api)
    secret = 'Acme (jane@acme.example) threatens to churn; contract $40k'
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Keep Acme happy', 'body': secret, 'labels': ['roadmap']})
    assert task['visibility'] == 'private' and task['public_summary'] == ''
    refused = post(api, 'tasks/' + task['id'], {'version': task['version'], 'visibility': 'public'}, expected=422)
    assert 'public title' in refused['error']['detail']
    task = post(
        api,
        'tasks/' + task['id'],
        {'version': task['version'], 'visibility': 'public', 'public_title': 'Faster exports'},
    )
    assert (task['public_title'], task['public_summary']) == ('Faster exports', '')
    shown = api.get('/public/roadmaps/roadmap').text + api.get('/public/roadmaps/roadmap/tasks/' + task['id']).text
    assert all(text not in shown for text in ('jane@', '$40k', 'Keep Acme happy', 'Faster exports'))


def _first_version_copies_edited_since_or_published_at_creation_are_cleared(api):
    from backend import roadmaps as R

    edited = post(api, 'tasks', {'owner': 'cpo', 'title': 'Old title', 'body': 'Old notes'})
    copied = post(api, 'tasks', {'owner': 'cpo', 'title': 'Acme churn', 'body': 'jane@acme.example'})
    draft = post(api, 'tasks', {'owner': 'cpo', 'title': 'Draft', 'body': 'Internal'})
    draft = post(api, 'tasks/' + draft['id'], {'version': draft['version'], 'public_title': 'Written draft'})
    shown = post(
        api,
        'tasks',
        {
            'owner': 'cpo',
            'title': 'Shown idea',
            'body': 'Shown notes',
            'visibility': 'public',
            'public_title': 'Shown idea',
            'public_summary': 'Shown notes',
        },
    )
    kept = post(
        api,
        'tasks',
        {'owner': 'cpo', 'title': 'Kept internal', 'body': 'Kept internal notes'},
    )
    with api.app.state.store.transaction() as c:
        # A fresh database is marked on its first start, so its second start clears nothing.
        assert c.execute("SELECT 1 FROM registry_metadata WHERE key='roadmaps_copies_cleared_by_field'").fetchone()
        # What the first version left: a copy made before the title changed, and a task public at creation.
        c.execute(
            "UPDATE tasks SET public_title='Old title',public_summary='Old notes',title='New title' WHERE id=?",
            (edited['id'],),
        )
        c.execute(
            "UPDATE tasks SET visibility='public',public_title=title,public_summary=body WHERE id=?", (copied['id'],)
        )
        # Public text with no event that differs from the internal text is not a copy, so a public task keeps it.
        c.execute(
            "UPDATE tasks SET visibility='public',public_title='Kept public',public_summary='Kept summary' WHERE id=?",
            (kept['id'],),
        )
        c.execute("DELETE FROM registry_metadata WHERE key='roadmaps_copies_cleared_by_field'")
        R.migrate(c)
        state = lambda tid: tuple(
            c.execute('SELECT visibility,public_title,public_summary FROM tasks WHERE id=?', (tid,)).fetchone()
        )
        assert state(edited['id']) == ('private', '', '')
        assert state(copied['id']) == ('private', '', '')
        assert state(draft['id']) == ('private', 'Written draft', '')  # written public text stays
        assert state(shown['id']) == ('public', 'Shown idea', 'Shown notes')  # written at creation, so it stays
        assert state(kept['id']) == ('public', 'Kept public', 'Kept summary')  # only an exact copy goes
        note = c.execute(
            "SELECT note FROM history_events WHERE subject_type='task' AND subject_id=? AND kind='visibility'",
            (copied['id'],),
        ).fetchone()
        assert 'copy of the internal title' in note[0]
        cleared = c.execute(
            "SELECT kind AS field,old,new,note FROM history_events WHERE subject_type='task' AND subject_id=? AND note LIKE 'Cleared:%' "
            "ORDER BY kind",
            (edited['id'],),
        ).fetchall()
        assert [tuple(r[:3]) for r in cleared] == [
            ('public_summary', 'Old notes', ''),
            ('public_title', 'Old title', ''),
        ]
        c.execute("UPDATE tasks SET public_title='Old title' WHERE id=?", (edited['id'],))
        R.migrate(c)  # once only
        assert state(edited['id']) == ('private', 'Old title', '')


def _first_version_copies_are_cleared_per_field_when_only_the_public_title_was_rewritten(api):
    from backend import roadmaps as R

    shown = post(api, 'tasks', {'owner': 'cpo', 'title': 'D', 'body': 'secret internal body'})
    hidden = post(api, 'tasks', {'owner': 'cpo', 'title': 'E', 'body': 'other internal body'})
    retitled = post(api, 'tasks', {'owner': 'cpo', 'title': 'F', 'body': 'Written summary'})
    with api.app.state.store.transaction() as c:
        # The first version made each a copy at creation; then the owner rewrote only one field through publication().
        c.execute(
            "UPDATE tasks SET public_title=title,public_summary=body WHERE id IN (?,?,?)",
            (shown['id'], hidden['id'], retitled['id']),
        )
        c.execute("UPDATE tasks SET visibility='public' WHERE id IN (?,?)", (shown['id'], retitled['id']))
    shown = get(api, 'tasks/' + shown['id'])['task']
    post(api, 'tasks/' + shown['id'], {'version': shown['version'], 'public_title': 'D written'})
    hidden = get(api, 'tasks/' + hidden['id'])['task']
    post(api, 'tasks/' + hidden['id'], {'version': hidden['version'], 'public_title': 'E written'})
    retitled = get(api, 'tasks/' + retitled['id'])['task']
    post(api, 'tasks/' + retitled['id'], {'version': retitled['version'], 'public_summary': 'Written summary, public'})
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM registry_metadata WHERE key='roadmaps_copies_cleared_by_field'")
        R.migrate(c)
        state = lambda tid: tuple(
            c.execute('SELECT visibility,public_title,public_summary FROM tasks WHERE id=?', (tid,)).fetchone()
        )
        assert state(shown['id']) == ('public', 'D written', '')  # the written title keeps it public
        assert state(hidden['id']) == ('private', 'E written', '')
        assert state(retitled['id']) == ('private', '', 'Written summary, public')
        # Each clearing leaves its event, so it can be put back.
        assert tuple(
            c.execute(
                "SELECT old,new FROM history_events WHERE subject_type='task' AND subject_id=? AND kind='public_summary' "
                "AND note LIKE 'Cleared:%'",
                (shown['id'],),
            ).fetchone()
        ) == ('secret internal body', '')
        assert c.execute(
            "SELECT 1 FROM history_events WHERE subject_type='task' AND subject_id=? AND kind='visibility' AND note LIKE 'Unpublished:%'",
            (retitled['id'],),
        ).fetchone()


def test_first_version_copies_are_cleared_once_and_only_what_was_a_copy(api):
    _first_version_copies_are_cleared_per_field_when_only_the_public_title_was_rewritten(api)
    _first_version_copies_edited_since_or_published_at_creation_are_cleared(api)


def test_a_requester_token_withdraws_after_unlabel_disable_and_delete(api):
    """The token handed back when a requester is recorded takes their details back whatever became of the task:
    unlabelled, off every roadmap, or in the trash. A purged trash row is past withdrawing from."""
    task = _public_task(api, limit=50)
    base = '/public/roadmaps/roadmap/tasks/' + task['id']

    def follow(tid=None):
        made = post(
            api, f"tasks/{tid or task['id']}/requesters", {'email': 'customer@acme.example', 'phone': '555-0100'}
        )
        return made['requester_token']

    def count(tid):
        with api.app.state.store.read() as c:
            return c.execute('SELECT count(*) FROM task_requesters WHERE task_id=?', (tid,)).fetchone()[0]

    unlabelled, disabled = follow(), follow()
    task = post(api, 'tasks/' + task['id'], {'version': task['version'], 'labels': []})
    assert api.delete(base + '/requesters/' + unlabelled, headers=ORIGIN).status_code == 200
    assert count(task['id']) == 1
    post(api, 'tasks/' + task['id'], {'version': task['version'], 'labels': ['roadmap']})
    post(api, 'roadmaps/settings', {'labels': []})
    assert api.delete(base + '/requesters/' + disabled, headers=ORIGIN).status_code == 200
    assert count(task['id']) == 0
    unknown = api.delete(base + '/requesters/' + disabled, headers=ORIGIN)
    missing = api.delete('/public/roadmaps/roadmap/tasks/not-a-task/requesters/' + disabled, headers=ORIGIN)
    assert unknown.status_code == missing.status_code == 404 and unknown.json() == missing.json()

    trashed = post(api, 'tasks', {'owner': 'ben', 'title': 'Bulk exports', 'body': 'Internal'})
    path = '/public/roadmaps/roadmap/tasks/' + trashed['id']
    leaving, staying = follow(trashed['id']), follow(trashed['id'])
    assert post(api, 'tasks/' + trashed['id'] + '/delete', {}) == {'deleted': trashed['id']}
    assert api.delete(path + '/requesters/' + leaving, headers=ORIGIN).status_code == 200
    assert api.delete(path + '/requesters/' + leaving, headers=ORIGIN).status_code == 404
    with api.app.state.store.read() as c:
        snapshot = c.execute('SELECT rows FROM task_trash WHERE task_id=?', (trashed['id'],)).fetchone()[0]
    assert hashlib.sha256(leaving.encode()).hexdigest() not in snapshot
    assert hashlib.sha256(staying.encode()).hexdigest() in snapshot
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE task_trash SET purged_at='2026-01-01T00:00:00Z' WHERE task_id=?", (trashed['id'],))
    assert api.delete(path + '/requesters/' + staying, headers=ORIGIN).status_code == 404
    with api.app.state.store.transaction() as c:
        assert c.execute('SELECT rows FROM task_trash WHERE task_id=?', (trashed['id'],)).fetchone()[0] == snapshot
        c.execute('UPDATE task_trash SET purged_at=NULL WHERE task_id=?', (trashed['id'],))
    post(api, 'tasks/' + trashed['id'] + '/restore', {})
    assert count(trashed['id']) == 1


def test_withdrawals_share_the_daily_action_limit(api):
    task = _public_task(api, limit=2)
    tokens = [
        post(api, f"tasks/{task['id']}/requesters", {'email': f'c{n}@acme.example'})['requester_token']
        for n in range(3)
    ]
    base = '/public/roadmaps/roadmap/tasks/' + task['id'] + '/requesters/'
    assert [api.delete(base + t, headers=ORIGIN).status_code for t in tokens] == [200, 200, 429]


def test_guard_refusals_carry_cors_headers_for_allowed_origins(api):
    task = _public_task(api)
    base = '/public/roadmaps/roadmap/tasks/' + task['id']
    big = api.post(
        base + '/comments',
        content=b'{"text":"' + b'x' * 70000 + b'"}',
        headers={**ORIGIN, 'Content-Type': 'application/json'},
    )
    assert big.status_code == 413 and big.json()['error']['code'] == 'too_large'
    assert big.headers['access-control-allow-origin'] == ORIGIN['Origin'] and big.headers['vary'] == 'Origin'
    preflight = api.options(base + '/requesters', headers={**ORIGIN, 'Access-Control-Request-Method': 'PUT'})
    assert preflight.status_code == 400 and preflight.headers['access-control-allow-origin'] == ORIGIN['Origin']
    other = api.options(
        base + '/requesters', headers={'Origin': 'https://evil.example', 'Access-Control-Request-Method': 'POST'}
    )
    assert other.status_code == 403 and 'access-control-allow-origin' not in other.headers
    assert api.post(base + '/requesters', json={}, headers={'Origin': 'https://evil.example'}).status_code == 403


def _suggestions_receiver_order_and_states(api):
    with api.app.state.store.transaction() as c:
        BS.update(c, 'cpo', {'template': 'feature-owner'})
        BS.update(c, 'cmo', {'template': 'product-lead'})

    def receiver(chosen, **states):
        with api.app.state.store.transaction() as c:
            c.execute("UPDATE bots SET state='active' WHERE slug IN ('cpo','cmo','ops')")
            for bot, value in states.items():
                c.execute('UPDATE bots SET state=? WHERE slug=?', (value, bot))
            c.execute(
                "INSERT INTO registry_metadata(key,value) VALUES('roadmaps',?) ON CONFLICT(key) "
                'DO UPDATE SET value=excluded.value',
                (json.dumps({'suggestions_bot': chosen}),),
            )
            return R.suggestions_receiver(c)

    assert receiver(None) == ('cpo', 'automatic')
    assert receiver(None, cpo='planned') == ('cpo', 'automatic')  # a planned Product Owner counts
    assert receiver(None, cpo='archived') == ('cmo', 'automatic')
    assert receiver(None, cpo='archived', cmo='planned') == (None, 'automatic')
    assert receiver(None, cpo='archived', cmo='archived') == (None, 'automatic')
    assert receiver('ops') == ('ops', 'chosen')
    assert receiver('ops', ops='planned') == ('cpo', 'chosen_inactive')
    assert receiver('ops', ops='paused', cpo='archived') == ('cmo', 'chosen_inactive')
    assert receiver('ops', ops='archived', cpo='archived', cmo='archived') == (None, 'chosen_archived')


def _only_owners_and_admins_choose_the_suggestions_bot(api):
    post(api, 'roadmaps/settings', {'suggestions_bot': 'cpo'}, token='ben-test', expected=403)
    assert get(api, 'roadmaps/settings', token='ben-test')['suggestions_bot'] is None
    assert (
        'No active bot'
        in post(api, 'roadmaps/settings', {'suggestions_bot': 'nobody'}, expected=422)['error']['detail']
    )
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET state='archived' WHERE slug='cmo'")
    post(api, 'roadmaps/settings', {'suggestions_bot': 'cmo'}, expected=422)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET state='planned' WHERE slug='ops'")
    post(api, 'roadmaps/settings', {'suggestions_bot': 'ops'}, expected=422)
    chosen = post(api, 'roadmaps/settings', {'suggestions_bot': 'bot:cpo'})
    assert chosen['suggestions_bot'] == 'cpo' and chosen['suggestions_receiver']['source'] == 'chosen'
    automatic = post(api, 'roadmaps/settings', {'suggestions_bot': None})
    assert automatic['suggestions_bot'] is None and automatic['suggestions_receiver']['source'] == 'automatic'


def test_who_may_choose_the_suggestions_bot_and_which_bot_receives_them(api):
    _only_owners_and_admins_choose_the_suggestions_bot(api)
    _suggestions_receiver_order_and_states(api)


def test_suggestions_receiver_is_a_new_org_product_bot_after_the_old_roles(api):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET state='active' WHERE slug IN ('cpo','cmo')")
        BS.update(c, 'cpo', {'template': 'product-manager'})
        assert R.suggestions_receiver(c) == ('cpo', 'automatic')
        BS.update(c, 'cpo', {'template': 'feature-owner'})
        BS.update(c, 'cmo', {'template': 'product-lead'})
        assert R.suggestions_receiver(c) == ('cpo', 'automatic')  # the Product Owner, who owns Product's Ideas
        BS.update(c, 'cpo', {'template': None})
        assert R.suggestions_receiver(c) == ('cmo', 'automatic')
