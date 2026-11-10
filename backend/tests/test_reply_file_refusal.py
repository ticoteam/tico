"""A reply inherits the file refusal of the message it replies to, at any depth, on every read path.

Four read paths must agree on each message of a reply chain: the SQL list (POST /api/v2/sql), the conversation route,
the thread timeline and the per-message check (GET /api/v2/messages/{id}). Each runs once before and once after the
settled-row index is swept, because a settled row skips the Python walk.
"""

import pytest

from backend import read_deps
from backend.store import H
from backend.tests.helpers.api import headers
from backend.tests.helpers.page_audience import edit
from backend.tests.helpers.pages import page
from backend.tests.helpers.sql import query, world  # noqa: F401  (fixture)


def chain(api, room, refs_by_depth):
    """A reply chain in `room`: the root carries `refs_by_depth[0]`, each reply the next entry. Returns the ids root first."""
    store, ids = api.app.state.store, []
    with store.transaction() as c:
        for depth, refs in enumerate(refs_by_depth):
            ids.append(
                H.say(
                    c,
                    'human:ben',
                    'human:ana',
                    f'Synthetic chain message {depth}',
                    conversation_id=room,
                    refs=refs,
                    in_reply_to=ids[-1] if ids else None,
                )['id']
            )
    return ids


def visible_sql(api, ids, token):
    marks = ','.join('?' for _ in ids)
    rows = query(api, f'SELECT id FROM messages WHERE id IN ({marks})', token, params=ids)['rows']
    return {row[0] for row in rows}


def visible_route(api, room, ids, token):
    r = api.get(f'/api/v2/conversations/{room}/messages', headers=headers(token))
    assert r.status_code == 200, r.text
    return {m['id'] for m in r.json()['messages']} & set(ids)


def visible_timeline(api, room, ids, token):
    r = api.get(f'/api/v2/threads/{room}/timeline?limit=200', headers=headers(token))
    assert r.status_code == 200, r.text
    shown = set()
    for m in r.json()['messages']:
        if not m.get('hidden'):
            shown.add(m['id'])
    return shown & set(ids)


def visible_message(api, ids, token):
    return {mid for mid in ids if api.get(f'/api/v2/messages/{mid}', headers=headers(token)).status_code == 200}


def visible_everywhere(api, room, ids, token):
    """The four read paths' answers, which must agree."""
    answers = {
        'sql': visible_sql(api, ids, token),
        'route': visible_route(api, room, ids, token),
        'timeline': visible_timeline(api, room, ids, token),
        'message': visible_message(api, ids, token),
    }
    assert answers['sql'] == answers['route'] == answers['timeline'] == answers['message'], answers
    return answers['sql']


@pytest.mark.parametrize('settled', [False, True])
def test_reply_inherits_its_parents_file_refusal_at_any_depth(api, world, settled):
    made = page(api, world['attempt'])
    assert made.status_code == 200, made.text
    fid = made.json()['page']['file_id']
    edit(api, fid, {'people': ['ana']}, token='ana-test')
    assert api.get('/api/v2/pages/' + fid, headers=headers('cara-test')).status_code == 404
    room = world['coo_task']['conversation_id']
    with api.app.state.store.transaction() as c:
        hidden = H.task_create(c, 'human:ben', 'Synthetic private task', '', 'bot:finance', private=True, lint=False)
    file_refused = chain(api, room, [{'target': fid}, None, None, None])
    # A chain five deep: beyond the SQL levels, so the settled index declines it and Python reads it.
    deep = chain(api, room, [{'target': fid}, None, None, None, None])
    # The reply chain of a message that names no file: every reader keeps it.
    plain = chain(api, room, [None, None, None])
    # The reply chain of a message that names a private task: the task rule is unchanged.
    task_refused = chain(api, room, [{'task': hidden['id']}, None, None])
    if settled:
        with api.app.state.store.read() as c:
            read_deps.sweep(c)

    # Cara may not open the file; Ana may. A reply hides at depth 1, 2 and 3 alike, and with the file hidden the
    # chain's root hides too. The plain chain and Ana's view of the file chain are unchanged.
    assert visible_everywhere(api, room, file_refused, 'cara-test') == set()
    assert visible_everywhere(api, room, deep, 'cara-test') == set()
    assert visible_everywhere(api, room, plain, 'cara-test') == set(plain)
    assert visible_everywhere(api, room, file_refused, 'ana-test') == set(file_refused)
    assert visible_everywhere(api, room, deep, 'ana-test') == set(deep)
    assert visible_everywhere(api, room, plain, 'ana-test') == set(plain)
    # The task rule for a reply is unchanged: a reply to a refused task is hidden from a reader it is refused to.
    assert visible_everywhere(api, room, task_refused, 'cara-test') == set()


@pytest.mark.parametrize('settled', [False, True])
@pytest.mark.parametrize('ancestors', [99, 100])
def test_a_reply_with_too_many_ancestors_is_hidden_on_every_path(api, world, settled, ancestors):
    """A reply with 100 ancestors is refused as ReadIndex.sources refuses it, and one with 99 is read: the four paths
    agree on both sides of the limit, with no file refused at all."""
    room = world['coo_task']['conversation_id']
    ids = chain(api, room, [None] * (ancestors + 1))
    if settled:
        with api.app.state.store.read() as c:
            read_deps.sweep(c)
    expected = set(ids) if ancestors < 100 else set(ids[:-1])
    assert visible_everywhere(api, room, ids, 'cara-test') == expected
    assert visible_everywhere(api, room, ids, 'ana-test') == expected


def test_rows_an_older_server_settled_below_a_file_are_read_again_after_the_upgrade(api, world):
    """An older server settled a reply below a refused file in read_index; the migration requeues it, so the reply
    hides again instead of being read from the stored index."""
    import importlib

    made = page(api, world['attempt'])
    assert made.status_code == 200, made.text
    fid = made.json()['page']['file_id']
    edit(api, fid, {'people': ['ana']}, token='ana-test')
    room = world['coo_task']['conversation_id']
    ids = chain(api, room, [{'target': fid}, None, None])
    store = api.app.state.store
    with store.read() as c:
        read_deps.sweep(c)
    with store.transaction() as c:
        assert not c.execute('SELECT 1 FROM read_index WHERE key IN (?,?)', ('m:' + ids[1], 'm:' + ids[2])).fetchall()
        # As the older server left them: settled.
        c.executemany('INSERT OR IGNORE INTO read_index VALUES(?)', [('m:' + mid,) for mid in ids])
    assert visible_sql(api, ids[1:], 'cara-test') == set(ids[1:])
    migration = importlib.import_module('backend.migrations.20261110230000_requeue_rows_below_files')
    with store.transaction() as c:
        migration.up(c)
        queued = {r[0] for r in c.execute('SELECT key FROM read_dirty')}
    assert {'m:' + ids[1], 'm:' + ids[2]} <= queued
    assert visible_everywhere(api, room, ids, 'cara-test') == set()
    with store.read() as c:
        read_deps.sweep(c)
    assert visible_everywhere(api, room, ids, 'cara-test') == set()
    assert visible_everywhere(api, room, ids, 'ana-test') == set(ids)
