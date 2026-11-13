"""Frozen source indexes drive bounded, permission-filtered changes to open outboxes."""

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

from backend import blobs, events as E, letters as L, task_asks
from backend.tests.helpers.api import as_member, get, headers, post  # noqa: F401
from backend.tests.helpers.letters import hq, quote, send, grant, fetch  # noqa: F401
from backend.tests.helpers.agents import hermes_bot, bot_id, credential
from backend.tests.threads_e2e import create_thread


def approve(api, row, token="ana-test"):
    """The person opens the letter in Tico and clicks OK on it (backend/task_asks.py)."""
    shown = get(api, "letters/" + row["id"], token=token)["shown_hash"]
    with api.app.state.store.read() as c:
        task = task_asks.task_of(c, row["approval_id"])
    return post(api, f"tasks/{task}/asks/{row['approval_id']}/ok", {"shown_hash": shown}, token=token)


def read_letters(api, token, after):
    with api.app.state.store.read() as c:
        who = api.app.state.auth.settings.test_identities[token]
        return E.read(c, api.app.state.auth, who, after, topics=("letters",))[0]


def cursor(api):
    with api.app.state.store.read() as c:
        return E.latest(c)


def delivered(api, row, suffix='one'):
    api.app.state.letters.update(
        row['id'], {'letter_id': 'lt_' + suffix, 'status': 'delivered', 'billing': 'billed', 'charged_micros': 1500000}
    )


# Each axis once: an audience change on an explicit source, a removed member on an inferred child. The manager's loss of
# source access is pinned by test_a_manager_losing_source_access_gets_gone_and_an_unrelated_person_gets_no_id.
@pytest.mark.parametrize('mutation,source', [('audience', 'explicit'), ('remove', 'inferred_child')])
def test_real_thread_access_changes_redact_finished_letter_rows(api, hq, mutation, source):
    root = create_thread(
        api,
        'cara-test',
        audience='company' if mutation == 'audience' else 'members',
        members=['human:ana'] if mutation == 'remove' else [],
        body='Source context.',
    )
    if source == 'explicit':
        row = quote(api, thread_id=root['id'])
    else:
        child = create_thread(api, 'cara-test', parent_message_id=root['message']['id'])
        message = post(api, 'threads/' + child['id'] + '/messages', {'text': 'The source PDF.'}, token='cara-test')
        data = L.render('A complete source letter with enough searchable text for recipient triage.')
        digest = api.app.state.blobs.put(data, 'application/pdf')
        with api.app.state.store.transaction() as c:
            original = blobs.register(
                c,
                api.app.state.auth.settings.test_identities['ana-test'],
                digest,
                len(data),
                'source.pdf',
                'application/pdf',
            )
            c.execute('INSERT INTO message_assets(message_id,blob_id) VALUES(?,?)', (message['id'], original['id']))
        row = post(
            api,
            'letters/quotes',
            {
                'file_id': original['id'],
                'recipient': {
                    'name': 'Recipient',
                    'line1': '2 Main St',
                    'city': 'Dover',
                    'state': 'DE',
                    'postal_code': '19901',
                },
            },
        )
        assert row['thread_id'] is None
        with api.app.state.store.read() as c:
            indexed = {
                r[0]
                for r in c.execute(
                    "SELECT source_id FROM letter_sources WHERE letter_id=? AND source_type='thread'", (row['id'],)
                )
            }
        assert {root['id'], child['id']} <= indexed
    delivered(api, row)
    assert not fetch(api, row)['redacted']
    before = cursor(api)
    if mutation == 'audience':
        response = api.patch(
            '/api/v2/threads/' + root['id'], json={'audience': 'members'}, headers=headers('cara-test')
        )
    else:
        response = api.delete('/api/v2/threads/' + root['id'] + '/members/human:ana', headers=headers('cara-test'))
    assert response.status_code == 200, response.text
    changed = read_letters(api, 'ana-test', before)
    assert [event[2]['id'] for event in changed] == [row['id']]
    patch = changed[0][2]['letter']
    assert patch['redacted'] and not {'recipient', 'return_address', 'proof'}.intersection(patch)
    assert api.get(row['proof'], headers=headers()).status_code == 403


def test_a_manager_losing_source_access_gets_gone_and_an_unrelated_person_gets_no_id(api, hq):
    as_member(api, 'ben@acme.example')
    hermes_bot(api, slug='mail-writer', operator='cara', owners=['cara'], reports_to='human:cara')
    ident, token = bot_id(api, 'mail-writer'), credential(api, 'mail-writer')['token']
    grant(api, ident)
    task = post(
        api,
        'tasks',
        {'owner': 'human:ben', 'title': 'Review the source', 'body': 'Source packet.', 'private': False},
        token='ben-test',
    )
    row = send(api, quote(api, token=token, task_id=task['id']), token)
    approve(api, row, 'cara-test')  # the bot's OK is asked of its manager, Cara: hers to click
    delivered(api, row)
    assert not fetch(api, row, 'cara-test')['redacted']
    before = cursor(api)
    post(api, 'tasks/' + task['id'], {'version': task['version'], 'private': True}, token='ben-test')
    get(api, 'letters/' + row['id'], 'cara-test', expected=404)
    assert get(api, 'letters', 'cara-test')['items'] == []
    removed = read_letters(api, 'cara-test', before)
    assert len(removed) == 1 and removed[0][2]['id'] == row['id'] and removed[0][2]['gone'] is True
    assert 'letter' not in removed[0][2]
    assert read_letters(api, 'ben-test', before) == []
    assert api.get(row['proof'], headers=headers('cara-test')).status_code == 403


def clone_rows(c, table, first, count, prefix):
    row = dict(c.execute('SELECT * FROM ' + table + ' WHERE id=?', (first,)).fetchone())
    columns = list(row)
    sql = 'INSERT INTO ' + table + '(' + ','.join(columns) + ') VALUES(' + ','.join('?' * len(columns)) + ')'
    batch = []
    for i in range(count):
        item = dict(row, id=prefix + str(i))
        if table == 'conversations':
            item.update(root_id=item['id'], origin_id=prefix + 'origin-' + str(i), room_key=None)
        batch.append(tuple(item[key] for key in columns))
    c.executemany(sql, batch)


def test_large_source_fanout_coalesces_to_one_invalidation_without_letter_ids(api, hq):
    task = post(
        api,
        'tasks',
        {'owner': 'human:cara', 'title': 'Shared source', 'body': 'Source.', 'private': False},
        token='cara-test',
    )
    row = quote(api, task_id=task['id'])
    with api.app.state.store.transaction() as c:
        clone_rows(c, 'letters', row['id'], E.FAN_CAP + 5, 'many-')
        c.execute("INSERT INTO letter_sources SELECT id,'task',? FROM letters WHERE id LIKE 'many-%'", (task['id'],))
    before = cursor(api)
    post(api, 'tasks/' + task['id'], {'version': task['version'], 'private': True}, token='cara-test')
    with api.app.state.store.read() as c:
        changes = c.execute("SELECT kind,subject_id FROM changes WHERE topic='letters' AND seq>?", (before,)).fetchall()
    assert [tuple(r) for r in changes] == [('invalidate', None)]
    invalidated = read_letters(api, 'ana-test', before)
    assert len(invalidated) == 1 and invalidated[0][2]['invalidate'] is True and 'id' not in invalidated[0][2]
    assert read_letters(api, 'cara-test', before) == []


def test_source_map_migration_is_standalone_and_backfills_tasks_threads_and_roots():
    path = Path(__file__).resolve().parents[1] / 'migrations' / '20261105101525_letter_sources.py'
    spec = importlib.util.spec_from_file_location('letter_sources_migration', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with sqlite3.connect(':memory:') as c:
        migration.up(c)
        assert c.execute('SELECT count(*) FROM letter_sources').fetchone()[0] == 0
    with sqlite3.connect(':memory:') as c:
        c.executescript(
            'CREATE TABLE letters(id TEXT PRIMARY KEY,task_id TEXT,thread_id TEXT,source_provenance TEXT);'
            'CREATE TABLE conversations(id TEXT PRIMARY KEY,root_id TEXT,task_id TEXT);'
        )
        c.execute("INSERT INTO conversations VALUES('root','root','root-task')")
        c.execute("INSERT INTO conversations VALUES('child','root',NULL)")
        c.execute(
            'INSERT INTO letters VALUES(?,?,?,?)',
            ('one', 'direct-task', 'child', json.dumps({'tasks': ['frozen-task'], 'threads': ['child']})),
        )
        migration.up(c)
        migration.up(c)
        assert set(c.execute('SELECT source_type,source_id FROM letter_sources')) == {
            ('task', 'direct-task'),
            ('task', 'frozen-task'),
            ('task', 'root-task'),
            ('thread', 'child'),
            ('thread', 'root'),
        }
