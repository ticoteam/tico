"""Bot history reads on a disposable, production-sized synthetic database.

Run explicitly with a development venv:
python -m pytest -q -s -o addopts='' -m slow scripts/benchmark_sql_history.py
This manual timing receipt is excluded from the ordinary and release test suites.
"""
import json
import time
import uuid

import pytest

from backend.store import H
from backend.tests.test_api import api, assign, claim, headers, post, ready, runner  # noqa: F401


@pytest.mark.slow
@pytest.mark.parametrize("provenance", ["plain", "mixed"])
def test_bot_history_queries_finish_within_the_existing_timeout(api, provenance):
    machine = runner(api)
    assign(api, machine, 'ops')
    ready(api, machine, ['ops'])
    post(api, 'chat/ops', {'text': 'Synthetic history benchmark'})
    attempt = claim(api, machine)
    count, rooms, recent = 30_000, 5_000, 1_000
    now = H.now()
    public_tasks = [str(uuid.uuid5(uuid.NAMESPACE_URL, f'synthetic-public-task-{i}')) for i in range(rooms)]
    with api.app.state.store.transaction() as c:
        # The synthetic credential must stay valid through all timed reads.
        c.execute('UPDATE attempts SET lease_until=? WHERE id=?',
                  (H.shift(now, hours=1), attempt['id']))
        # Bulk fixture loading must not queue actual runs or construct message links.
        for trigger in ('message_links_insert', 'queue_bot_message', 'wake_preset_hold', 'message_links_update'):
            c.execute('DROP TRIGGER IF EXISTS ' + trigger)
        c.executemany('INSERT INTO tasks(id,title,owner,requester,private,status,created_at,updated_at) '
                      "VALUES(?,'Synthetic task','bot:ops','human:ana',0,'done',?,?)",
                      [(public_tasks[i], now, now) for i in range(rooms)])
        # Authorization must also work with a nonempty denied-private set;
        # no message references these unrelated synthetic private rows.
        c.executemany('INSERT INTO tasks(id,title,owner,requester,private,status,created_at,updated_at) '
                      "VALUES(?,'Hidden synthetic task','bot:finance','human:ben',1,'done',?,?)",
                      [(str(uuid.uuid5(uuid.NAMESPACE_URL, f'synthetic-hidden-task-{i}')), now, now) for i in range(500)])
        c.executemany('INSERT INTO conversations(id,kind,task_id,participants,created_at) '
                      "VALUES(?,'task',?,'[]',?)",
                      [(f'fixture-room-{i}', public_tasks[i], now) for i in range(rooms)])

        def copies(table, original, size, patch):
            template = dict(c.execute(f'SELECT * FROM {table} WHERE id=?', (original,)).fetchone())
            columns = list(template)
            rows = []
            for i in range(size):
                row = {**template, 'id': f'fixture-{table}-{i}', **patch(i)}
                assert not set(row) - set(columns), (table, set(row) - set(columns))
                rows.append([row[key] for key in columns])
            c.executemany(f'INSERT INTO {table} ({",".join(columns)}) '
                          f'VALUES ({",".join("?" for _ in columns)})', rows)

        mid = c.execute('SELECT message_id FROM jobs WHERE id=?', (attempt['job_id'],)).fetchone()[0]
        copies('messages', mid, count * 5, lambda i: {
            'conversation_id': f'fixture-room-{i % rooms}',
            'refs': '{}' if provenance == 'plain' else json.dumps({
                'task': public_tasks[i % rooms], 'progress': {'sequence': i, 'note': 'Synthetic progress receipt'},
                **({'turn_id': f'fixture-attempts-{i % count}'} if i % 10 == 0 else {})}),
            'in_reply_to': f'fixture-messages-{i % rooms}' if provenance == 'mixed' and i >= rooms and i % 10 == 1 else None})
        event = c.execute('SELECT id FROM events LIMIT 1').fetchone()[0]
        copies('events', event, count * 15, lambda i: {})
        copies('jobs', attempt['job_id'], count, lambda i: {
            'message_id': f'fixture-messages-{i}', 'state': 'done', 'attempt_id': None})
        copies('attempts', attempt['id'], count, lambda i: {
            'job_id': f'fixture-jobs-{i}', 'token_hash': f'fixture-token-hash-{i}',
            'state': 'done', 'lane': f'fixture-lane-{i}',
            'created_at': now if i < recent else H.shift(now, hours=-72)})
        copies('turns', attempt['id'], count, lambda i: {
            'id': f'fixture-attempts-{i}', 'message_id': f'fixture-messages-{i}',
            'task_id': public_tasks[i % rooms],
            'started_at': now if i < recent else H.shift(now, hours=-72)})
        print('synthetic sizes:', {table: c.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
                                   for table in ('tasks', 'conversations', 'messages', 'jobs', 'attempts', 'turns', 'events')},
              flush=True)

    queries = [
        'SELECT id FROM turns LIMIT 1',
        'SELECT id FROM attempts LIMIT 1',
        "SELECT count(*) FROM turns WHERE started_at>=:day AND started_at<:tomorrow",
        "SELECT count(*) FROM attempts WHERE created_at>=:day AND created_at<:tomorrow",
        "SELECT count(*) FROM turns WHERE started_at >= datetime('now','-1 day')",
        "SELECT count(*) FROM attempts WHERE created_at >= datetime('now','-1 day')",
        'SELECT count(*) FROM messages',
    ]
    params = {'day': now[:10], 'tomorrow': H.shift(now, hours=24)[:10]}
    for sql in queries:
        plan = api.post('/api/v2/sql', json={'sql': 'EXPLAIN QUERY PLAN ' + sql, 'params': params},
                        headers=headers(attempt['token']))
        assert plan.status_code == 200, plan.text
        print(sql, 'plan:', plan.json()['rows'], flush=True)
        measured = []
        for round_number in range(1, 11):
            start = time.monotonic()
            response = api.post('/api/v2/sql', json={'sql': sql, 'params': params},
                                headers=headers(attempt['token']))
            elapsed = time.monotonic() - start
            print('round:', round_number, 'sql:', sql, 'http_ms:', elapsed * 1000,
                  'status:', response.status_code, 'operation:', response.headers.get('x-operation-id'),
                  'result:', response.text, flush=True)
            assert response.status_code == 200, response.text
            result = response.json()
            assert result['rows'] and not result['truncated'], result
            if 'count(*)' in sql:
                assert result['rows'] == [[count * 5 + 1 if 'messages' in sql else recent + 1]], result
            measured.append((result['ms'], round(elapsed * 1000)))
            assert elapsed < 5, (sql, measured)
        print(sql, 'query_ms/http_ms:', measured, flush=True)

    # Exercise the shared provenance index on a real task list with open asks.
    with api.app.state.store.transaction() as c:
        for i in range(100):
            c.execute('UPDATE tasks SET conversation_id=? WHERE id=?',
                      (f'fixture-room-{i}', public_tasks[i]))
            c.execute("UPDATE messages SET kind='ask' WHERE id=?", (f'fixture-messages-{i}',))
    task_ids = ','.join(public_tasks[i] for i in range(100))
    measured = []
    for _ in range(10):
        start = time.monotonic()
        response = api.get('/api/v2/tasks', params={'ids': task_ids, 'brief': 'true'},
                           headers=headers(attempt['token']))
        assert response.status_code == 200, response.text
        assert len(response.json()['tasks']) == 100
        assert all(row.get('ask') for row in response.json()['tasks'])
        measured.append(round((time.monotonic() - start) * 1000))
    print('task-list 100 brief rows http_ms:', measured, flush=True)
