"""Task provenance and explicit lifecycle decisions survive secondary read/write paths."""

import json

from backend import pipeline_settings
from backend.store import H, encode
from backend.task_calls import SYSTEM
from backend.tests.helpers.tasks_board import api, bot_token, get, headers, post  # noqa: F401


def make_task(api, title, **fields):
    return post(api, 'tasks', {'owner': 'ops', 'title': title, 'body': 'Review the packet.', **fields})


def test_cross_task_provenance_filters_comments_asks_answers_and_mutation_receipts(api):
    source = make_task(api, 'Review source packet')
    target = make_task(api, 'Review related packet')
    first = post(api, f"tasks/{source['id']}/comments", {'text': 'Source material'})['comment']
    derived = []
    for kind in ('say', 'notice'):
        derived.append(
            post(
                api,
                'messages',
                {
                    'to': 'ops',
                    'kind': kind,
                    'text': 'Restricted derived message',
                    'conversation_id': target['conversation_id'],
                    'refs': {'task': target['id']},
                },
            )['id']
        )
    # The bot's open ask on the target is the target's own: whoever reads the target reads it.
    asked = post(
        api,
        f"tasks/{target['id']}/asks",
        {
            'to': 'ana',
            'line': 'Which packet is final?',
            'why': 'It decides what ships next.',
            'version': get(api, 'tasks/' + target['id'])['task']['version'],
        },
        token=bot_token(api, 'ops'),
    )
    with api.app.state.store.transaction() as c:  # each task has its own room, so the cross-room reply is set here
        for mid in derived:
            c.execute('UPDATE messages SET in_reply_to=? WHERE id=?', (first['id'], mid))
        answer = H.task_comment(
            c, 'human:ana', target['id'], 'Restricted answer', extra_refs={'answer': {'text': 'Restricted answer'}}
        )
        c.execute('UPDATE messages SET in_reply_to=? WHERE id=?', (first['id'], answer['id']))
        derived.append(answer['id'])
    # The comment above bumped the source's version, so the update names the version it reads now.
    source_version = get(api, 'tasks/' + source['id'])['task']['version']
    post(api, 'tasks/' + source['id'], {'version': source_version, 'private': True})
    for mid in derived:
        get(api, 'messages/' + mid, 'ben-test', expected=404)
    for path in ('', '/comments'):
        value = get(api, 'tasks/' + target['id'] + path, 'ben-test')
        assert all(mid not in json.dumps(value) for mid in derived)
        assert 'Restricted' not in json.dumps(value)
    shown = get(api, 'tasks/' + target['id'], 'ben-test')['task']
    assert asked['task']['id'] == target['id'] and shown['status'] == 'open'
    # Task comments are say, ask and answer messages; the notice in the loop is not one of them.
    say_id, notice_id, answer_id = derived
    comment_ids = {m['id'] for m in get(api, 'tasks/' + target['id'] + '/comments')['comments']}
    assert {say_id, answer_id} <= comment_ids and notice_id not in comment_ids
    added = post(api, f"tasks/{target['id']}/comments", {'text': 'Ordinary follow-up'}, 'ben-test')
    mid = added['comment']['id']
    responses = [
        added,
        post(api, f"tasks/{target['id']}/comments/{mid}", {'text': 'Revised follow-up'}, 'ben-test'),
        post(api, f"tasks/{target['id']}/comments/{mid}/delete", {}, 'ben-test'),
    ]
    # Filing the question, then the edit and delete, bumped the target's version: name the one Ben reads now.
    version = get(api, 'tasks/' + target['id'], 'ben-test')['task']['version']
    responses.append(post(api, f"tasks/{target['id']}", {'version': version, 'note': 'Still reviewing'}, 'ben-test'))
    assert all('Restricted' not in json.dumps(value) for value in responses)


def test_a_pipeline_edit_leaves_a_hidden_done_task_as_it_was_and_renames_its_stages(api):
    record = get(api, 'pipelines/general')['pipeline']
    [todo] = record['stages']
    stage = {k: v for k, v in todo.items() if k not in ('id', 'requirements')}
    # Ana owns the new pipeline, so she may change it.
    with api.app.state.store.transaction() as c:
        made = {'id': 'reviews', 'name': 'Reviews', 'goal': 'Review packets.', 'owner_id': 'human:ana'}
        pipeline_settings.save(c, SYSTEM, {**made, 'stages': [{**stage, 'name': 'Start'}]})
    pipe = get(api, 'pipelines/reviews')['pipeline']
    row = post(
        api,
        'tasks',
        {
            'owner': 'priya',
            'title': 'Review private packet',
            'body': 'Review it.',
            'private': True,
            'pipeline': 'reviews',
        },
        'ben-test',
    )
    done = post(api, f"tasks/{row['id']}/done", {'version': row['version'], 'note': 'Reviewed.'}, 'priya-test')
    assert done['status'] == 'done'
    get(api, 'tasks/' + row['id'], expected=404)
    record = {k: v for k, v in pipe.items() if k != 'may_change'}
    record['stages'] = [{**record['stages'][0], 'name': 'Begin'}]
    record['done'] = {**record['done'], 'name': 'Completed'}
    post(api, 'pipelines/reviews', record)
    assert get(api, 'tasks/' + row['id'], 'ben-test')['task'] == get(api, 'tasks/' + row['id'], 'priya-test')['task']
    after = get(api, 'tasks/' + row['id'], 'ben-test')['task']
    for key in ('status', 'version', 'done_at', 'closed_at', 'closed_by_id'):
        assert after[key] == done[key]
    assert [s['name'] for s in get(api, 'pipelines/reviews')['pipeline']['stages']] == ['Begin']


def test_comment_delegation_never_authorizes_a_bot_close(api):
    token = bot_token(api)
    row = make_task(api, 'Review the requested packet')
    for comment in (None, 'Close this.'):
        if comment:
            post(api, f"tasks/{row['id']}/comments", {'text': comment})
        version = get(api, 'tasks/' + row['id'])['task']['version']
        body = {'to': 'closed', 'why': 'Ana said to close it.', 'version': version}
        refused = api.post(f"/api/v2/tasks/{row['id']}/move", json=body, headers=headers(token))
        assert refused.status_code >= 400 and refused.json()['error'] == 'task_needs_person', refused.text
    closed = post(api, f"tasks/{row['id']}/move", {'to': 'closed'})
    assert closed['status'] == 'closed' and closed['closed_by_id'] == 'human:ana'
