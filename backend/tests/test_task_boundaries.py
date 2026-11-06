"""Task provenance and explicit lifecycle decisions survive secondary read/write paths."""
import json

from backend.store import H, encode
from backend.tests.test_tasks_board import api, bot_token, get, headers, post  # noqa: F401


def make_task(api, title, **fields):
    return post(api, 'tasks', {'owner': 'ops', 'title': title, 'body': 'Review the packet.', **fields})


def test_cross_task_provenance_filters_comments_asks_answers_and_mutation_receipts(api):
    source = make_task(api, 'Review source packet')
    target = make_task(api, 'Review related packet')
    first = post(api, f"tasks/{source['id']}/comments", {'text': 'Source material'})['comment']
    derived = []
    for kind in ('say', 'notice'):
        derived.append(post(api, 'messages', {'to': 'ops', 'kind': kind, 'text': 'Restricted derived message',
            'conversation_id': target['conversation_id'], 'refs': {'task': target['id']}})['id'])
    with api.app.state.store.transaction() as c:      # each task has its own room, so the cross-room reply is set here
        for mid in derived:
            c.execute('UPDATE messages SET in_reply_to=? WHERE id=?', (first['id'], mid))
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
    for key in ('status', 'version', 'done_at', 'closed_at', 'closed_by_id'):
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
    assert accepted['status'] == 'closed' and accepted['closed_by_id'] == 'human:ana'
    assert accepted['done_at'] == done['done_at']
