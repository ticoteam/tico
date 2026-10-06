"""Task provenance and explicit lifecycle decisions survive secondary read/write paths."""
import json

import pytest

from backend import routines
from backend.store import H, encode
from backend.tests.test_tasks_board import api, bot_token, get, headers, post  # noqa: F401


def make_task(api, title, **fields):
    return post(api, 'tasks', {'owner': 'ops', 'title': title, 'body': 'Review the packet.', **fields})


def routine(api):
    return post(api, 'bots/ops/routines', {'title': 'Review the scheduled packet',
                'cron': '0 9 * * *', 'timezone': 'UTC', 'text': 'Review the packet.'})['routine']['id']


def test_occurrences_filter_before_limit_and_revoke_after_reassignment(api):
    bot_token(api)
    sid = routine(api)
    public = make_task(api, 'Review public packet')
    hidden = make_task(api, 'Review private packet', private=True)
    with api.app.state.store.transaction() as c:
        for when, tid in [('1', public['id']), ('2', hidden['id'])]:
            c.execute('INSERT INTO schedule_occurrences VALUES(?,?,?,?)', (sid, when, tid, 'created'))
        config = c.execute("SELECT config_json FROM bot_config WHERE bot='ops'").fetchone()[0]
        c.execute("UPDATE bot_config SET config_json=? WHERE bot='ops'", (encode({'private_tasks_default': True}),))
        private_default = routines.open_task(c, api.app.state.auth, routines.row(c, sid), 'Private default run', 'Review it.', H.now())
        c.execute('INSERT INTO schedule_occurrences VALUES(?,?,?,?)', (sid, '3', private_default['id'], 'created'))
        c.execute("UPDATE bot_config SET config_json=? WHERE bot='ops'", (config,))
        assert private_default['private']
        # A visible occurrence cannot disclose execution metadata derived from a second private task.
        mid = c.execute('SELECT j.message_id FROM jobs j JOIN attempts a ON a.job_id=j.id LIMIT 1').fetchone()[0]
        c.execute('UPDATE messages SET refs_json=? WHERE id=?', (encode({'task': public['id'], 'source': hidden['id']}), mid))
        c.execute("UPDATE attempts SET started='2026-10-02T12:00:00Z',finished='2026-10-02T12:01:00Z',state='completed'")

    assert [r['task_id'] for r in get(api, f'routines/{sid}/occurrences?limit=1', 'ben-test')['occurrences']] == [public['id']]
    assert get(api, f'routines/{sid}/occurrences?limit=1')['occurrences'][0]['task_id'] == hidden['id']
    outsider = get(api, f'routines/{sid}/occurrences', 'ben-test')['occurrences'][0]
    assert all(outsider[k] is None for k in ('started', 'finished', 'duration_s', 'exit'))
    own = get(api, f'routines/{sid}/occurrences')['occurrences']
    assert next(r for r in own if r['task_id'] == public['id'])['duration_s'] == 60
    public = post(api, 'tasks/' + public['id'], {'version': public['version'], 'private': True, 'owner': 'ben'})
    assert [r['task_id'] for r in get(api, f'routines/{sid}/occurrences', 'ben-test')['occurrences']] == [public['id']]
    post(api, 'tasks/' + public['id'], {'version': public['version'], 'owner': 'priya'})
    assert get(api, f'routines/{sid}/occurrences', 'ben-test')['occurrences'] == []


def test_cross_task_provenance_filters_comments_asks_answers_and_mutation_receipts(api):
    source = make_task(api, 'Review source packet')
    target = make_task(api, 'Review related packet')
    first = post(api, f"tasks/{source['id']}/comments", {'text': 'Source material'})['comment']
    derived = []
    for kind in ('say', 'ask'):
        derived.append(post(api, 'messages', {'to': 'ops', 'kind': kind, 'text': 'Restricted derived message',
            'conversation_id': target['conversation_id'], 'refs': {'task': target['id']}, 'in_reply_to': first['id']})['id'])
    with api.app.state.store.transaction() as c:
        answer = H.task_comment(c, 'human:ana', target['id'], 'Restricted answer',
                                extra_refs={'answer': {'text': 'Restricted answer'}})
        c.execute('UPDATE messages SET in_reply_to=? WHERE id=?', (first['id'], answer['id']))
        derived.append(answer['id'])
    post(api, 'tasks/' + source['id'], {'version': source['version'], 'private': True})
    for mid in derived:
        get(api, 'messages/' + mid, 'ben-test', expected=404)
    for path in ('', '/comments', '/answers'):
        value = get(api, 'tasks/' + target['id'] + path, 'ben-test')
        assert all(mid not in json.dumps(value) for mid in derived)
        assert 'Restricted' not in json.dumps(value)
    listed = next(t for t in get(api, 'tasks', 'ben-test')['tasks'] if t['id'] == target['id'])
    assert listed['ask'] is None and listed['open_asks'] == 0
    assert get(api, 'tasks/' + target['id'])['task']['open_asks'] == 1
    assert set(derived) <= {m['id'] for m in get(api, 'tasks/' + target['id'] + '/comments')['comments']}
    added = post(api, f"tasks/{target['id']}/comments", {'text': 'Ordinary follow-up'}, 'ben-test')
    mid = added['comment']['id']
    responses = [added, post(api, f"tasks/{target['id']}/comments/{mid}", {'text': 'Revised follow-up'}, 'ben-test'),
                 post(api, f"tasks/{target['id']}/comments/{mid}/delete", {}, 'ben-test'),
                 post(api, f"tasks/{target['id']}", {'version': target['version'], 'note': 'Still reviewing'}, 'ben-test')]
    assert all('Restricted' not in json.dumps(value) for value in responses)


def test_nested_answers_and_file_reviews_filter_each_source_message(api):
    source = make_task(api, 'Review sensitive source')
    target = make_task(api, 'Review shared draft')
    ask = {'questions': [{'id': 'verdict', 'header': 'Decision', 'question': 'Ready?',
                          'options': [{'label': 'Approve'}], 'other': True}]}
    question = post(api, f"tasks/{target['id']}/comments", {'text': 'Please review', 'ask': ask})['comment']
    answer = post(api, f"tasks/{target['id']}/answers", {'target': {'comment': question['id']},
                   'answers': {'verdict': ['Approve']}, 'other': 'Restricted review'}, 'ben-test')['comment']
    # An answer can carry another task as provenance (for example through its turn inputs).
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE conversations SET scope='direct',participants_json=? WHERE id=?",
                  (encode(['human:ana', 'human:priya', 'bot:ops']), target['conversation_id']))
        refs = H.message(c, answer['id'])['refs']
        c.execute('UPDATE messages SET refs_json=? WHERE id=?', (encode({**refs, 'source': source['id']}), answer['id']))
    file = post(api, f"tasks/{target['id']}/files", {'name': 'draft.md', 'text': 'Public draft'}, 'priya-test')
    with api.app.state.store.transaction() as c:
        c.execute('INSERT OR REPLACE INTO task_file_reviews(file_id,version,note,comment_id,ask_message_id) VALUES(?,1,?,?,?)',
                  (file['file_id'], 'Restricted note', answer['id'], question['id']))
    version_path = f"/api/v2/files/{file['file_id']}/versions/1"
    retry_headers = headers('priya-test')
    before = api.patch(version_path, json={}, headers=retry_headers)
    assert before.status_code == 200 and 'Restricted review' in before.text
    post(api, 'tasks/' + source['id'], {'version': source['version'], 'private': True})
    for path in (f"tasks/{target['id']}", f"tasks/{target['id']}/comments", f"tasks/{target['id']}/answers",
                 f"tasks/{target['id']}/files", f"messages/{question['id']}"):
        assert 'Restricted' not in json.dumps(get(api, path, 'priya-test'))
    replay = api.patch(version_path, json={}, headers=retry_headers)
    assert replay.status_code == 200 and 'Restricted' not in replay.text
    assert 'Restricted review' in json.dumps(get(api, f"tasks/{target['id']}/comments"))
    assert 'Restricted note' in json.dumps(get(api, f"tasks/{target['id']}/files"))


@pytest.mark.parametrize('prior_attempt,legacy', [(True, True)])
def test_routine_removal_preserves_unrelated_work(api, prior_attempt, legacy):
    if prior_attempt:
        bot_token(api)
    sid = routine(api)
    tid = post(api, f'routines/{sid}/run', {})['task_id']
    other = make_task(api, 'Review unrelated packet')
    chat = post(api, 'messages', {'to': 'ops', 'text': 'Unrelated chat', 'conversation_id': other['conversation_id']})
    with api.app.state.store.transaction() as c:
        # Coalesced firings must not close/bump the same task twice.
        c.execute('INSERT INTO schedule_occurrences VALUES(?,?,?,?)', (sid, 'extra', tid, 'coalesced_into_existing_task'))
        before = H.task(c, tid)
        if legacy:
            conv = H.open_conversation(c, H.KEEPER, ['bot:ops'], kind='task', subject='Legacy task')
            c.execute('UPDATE conversations SET task_id=? WHERE id=?', (tid, conv['id']))
            c.execute('UPDATE messages SET conversation_id=?,refs_json=? WHERE json_extract(refs_json,\'$.task\')=?',
                      (conv['id'], '{}', tid))
            c.execute('UPDATE tasks SET conversation_id=? WHERE id=?', (conv['id'], tid))
        unrelated = [dict(r) for r in c.execute('SELECT j.id,j.state,m.delivered_at FROM jobs j JOIN messages m ON m.id=j.message_id '
                     'WHERE json_extract(m.refs_json,\'$.task\')=?', (other['id'],))]
        assert unrelated and all(r['state'] == 'queued' and r['delivered_at'] is None for r in unrelated)
    post(api, f'routines/{sid}/delete', {})
    with api.app.state.store.read() as c:
        after = H.task(c, tid)
        assert after['status'] == 'closed' and after['version'] == before['version'] + 1
        assert H.task(c, other['id'])['status'] == 'open'
        assert H.message(c, chat['id'])['delivered_at'] is None
        assert c.execute('SELECT state FROM jobs WHERE message_id=?', (chat['id'],)).fetchone()[0] == 'queued'
        assert [dict(r) for r in c.execute('SELECT j.id,j.state,m.delivered_at FROM jobs j JOIN messages m ON m.id=j.message_id '
                    'WHERE json_extract(m.refs_json,\'$.task\')=?', (other['id'],))] == unrelated
        assert not c.execute('SELECT 1 FROM messages m JOIN conversations cv ON cv.id=m.conversation_id '
                             f'WHERE {H.MESSAGE_TASK_SQL}=? AND m.to_actor LIKE \'bot:%\' AND m.delivered_at IS NULL', (tid,)).fetchone()
        assert not c.execute('SELECT 1 FROM jobs j JOIN messages m ON m.id=j.message_id JOIN conversations cv ON cv.id=m.conversation_id '
                             f"WHERE {H.MESSAGE_TASK_SQL}=? AND j.state='queued'", (tid,)).fetchone()


@pytest.mark.parametrize('claimed_by', ['input'])
def test_routine_removal_preserves_work_carried_by_an_unrelated_attempt(api, claimed_by):
    bot_token(api)
    sid = routine(api)
    tid = post(api, f'routines/{sid}/run', {})['task_id']
    with api.app.state.store.transaction() as c:
        aid = c.execute('SELECT id FROM attempts LIMIT 1').fetchone()[0]
        if claimed_by == 'carried':
            c.execute('UPDATE tasks SET carried_by=? WHERE id=?', (aid, tid))
        else:
            mid = c.execute('SELECT id FROM messages WHERE json_extract(refs_json,\'$.task\')=? LIMIT 1', (tid,)).fetchone()[0]
            c.execute('INSERT INTO attempt_inputs(attempt_id,message_id) VALUES(?,?)', (aid, mid))
    post(api, f'routines/{sid}/delete', {})
    assert get(api, 'tasks/' + tid)['task']['status'] == 'open'


def test_pipeline_cannot_remap_hidden_done_task_but_names_and_order_are_editable(api):
    typ = post(api, 'task-types', {'name': 'Reviews', 'steps': [{'name': 'Start', 'status': 'open'},
                {'name': 'Finished', 'status': 'done'}]})['type']
    row = post(api, 'tasks', {'owner': 'priya', 'title': 'Review private packet', 'body': 'Review it.',
                'private': True, 'type': typ['id']}, 'ben-test')
    done = post(api, 'tasks/' + row['id'], {'version': row['version'], 'step': 'Finished'}, 'priya-test')
    get(api, 'tasks/' + row['id'], expected=404)
    steps = [{k: s[k] for k in ('id', 'name', 'status', 'position')} for s in typ['steps']]
    steps[1]['status'] = 'closed'
    refused = post(api, 'task-types/' + typ['id'], {'steps': steps}, expected=422)
    assert 'Move tasks off this step' in json.dumps(refused)
    assert get(api, 'tasks/' + row['id'], 'ben-test')['task'] == get(api, 'tasks/' + row['id'], 'priya-test')['task']
    steps[1].update(status='done', name='Completed', position=0)
    steps[0].update(status='doing', position=1)  # Empty steps may change meaning.
    post(api, 'task-types/' + typ['id'], {'steps': steps})
    after = get(api, 'tasks/' + row['id'], 'ben-test')['task']
    for key in ('status', 'version', 'done_at', 'closed_at', 'closed_by'):
        assert after[key] == done[key]
    assert after['step']['name'] == 'Completed'


def test_comment_delegation_never_authorizes_bot_acceptance(api):
    token = bot_token(api)
    row = make_task(api, 'Review the requested packet')
    done = post(api, 'tasks/' + row['id'], {'version': row['version'], 'status': 'done'}, token)
    for comment in (None, 'Accept and close this.'):
        if comment:
            post(api, f"tasks/{row['id']}/comments", {'text': comment})
        for close in ({'close': True}, {'step': 'general-closed'}):
            post(api, 'tasks/' + row['id'], {'version': done['version'], **close}, token, expected=403)
    accepted = post(api, 'tasks/' + row['id'], {'version': done['version'], 'close': True})
    assert accepted['status'] == 'closed' and accepted['closed_by'] == 'human:ana'
    assert accepted['done_at'] == done['done_at']
