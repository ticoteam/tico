"""A reply inherits the file refusal of the message it replies to, at any depth, on every read path.

Four read paths must agree on each message of a reply chain: the SQL list (POST /api/v2/sql), the conversation route,
the thread timeline and the per-message check (GET /api/v2/messages/{id}). Each runs once before and once after the
settled-row index is swept, because a settled row skips the Python walk.
"""

import pytest

from backend import read_deps
from backend.store import H
from backend.tests.test_api import api, headers, post  # noqa: F401  (fixtures and helpers)
from backend.tests.test_page_audience import edit
from backend.tests.test_pages import page
from backend.tests.test_sql import query, world  # noqa: F401  (fixture)


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
    r = api.get(f'/api/v2/threads/{room}/timeline', headers=headers(token))
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
