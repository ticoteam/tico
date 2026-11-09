"""Every stable GET keeps the names supplied by the response annotation contract."""

import pytest
from fastapi.routing import APIRoute

from backend import meetings
from backend import question_tasks as QT
from backend import sources
from backend.store import H
from backend.tests.old_asks import old_ask
from backend.tests.helpers.api import get, headers, post  # noqa: F401


@pytest.fixture
def named_data(api):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE humans SET name='Ana' WHERE id='ana'")
        c.execute("UPDATE bots SET display_name='Operations' WHERE slug='ops'")
        task = H.task_create(c, 'human:ana', 'Review the plan', 'Please review the plan.', 'bot:ops')
        # An old ask in a task's thread, read as history, and a question task under the task for a person.
        ask = old_ask(
            c,
            'bot:ops',
            'human:ana',
            '@ana Please choose a review date.',
            conversation_id=task['conversation_id'],
            refs={'task': task['id']},
        )
        question = QT.ask_plain(c, 'bot:ops', 'human:ana', 'Which review date?', parent=task)
        notice = H.notice(c, 'bot:ops', 'human:ana', 'New task from bot:ops: Review the plan')
        meetings.snapshot(
            {
                'id': 'review-meeting',
                'title': 'Review the plan',
                'kind': 'meeting',
                'private': False,
                'owner': 'ana@example.com',
                'status': 'done',
            },
            transcript='Please review the plan.',
            conn=c,
        )
    doc = post(api, 'docs', {'title': 'Review plan', 'body': 'Please review the plan.'})['doc']
    return {'task': task, 'ask': ask, 'question': question, 'notice': notice, 'doc': doc}


def test_all_api_routes_keep_the_app_route_class(api):
    # Also guard aliases and convenience routes outside the stable display-name pattern.
    for route in api.app.routes:
        if isinstance(route, APIRoute):
            assert isinstance(route, api.app.router.route_class), route.path


def test_messages_and_question_tasks_have_display_names(api, named_data):
    result = api.get('/api/v2/messages', headers=headers())
    assert result.status_code == 200
    inbox = result.json()
    assert inbox['actors']['human:ana'] == inbox['actor_name'] == 'Ana'
    assert inbox['actors']['bot:ops'] == 'Operations'
    message = next(m for m in inbox['messages'] if m['id'] == named_data['ask']['id'])
    assert message['sender_name'] == 'Operations'
    assert message['recipient_name'] == 'Ana'
    notice = next(m for m in inbox['notices'] if m['id'] == named_data['notice']['id'])
    assert notice['body'] == 'New task from Operations: Review the plan'
    assert notice['body_raw'] == 'New task from bot:ops: Review the plan'

    result = api.get('/api/v2/tasks', headers=headers())
    assert result.status_code == 200
    tasks = result.json()
    task = next(t for t in tasks['tasks'] if t['id'] == named_data['task']['id'])
    assert task['owner_name'] == 'Operations' and task['requester_name'] == 'Ana'
    question = next(t for t in tasks['tasks'] if t['id'] == named_data['question']['id'])
    assert question['ask']['sender_name'] == 'Operations'
    assert question['ask']['recipient_name'] == 'Ana'
    assert tasks['actors']['bot:ops'] == 'Operations' and tasks['actors']['human:ana'] == 'Ana'


def test_a_task_the_server_filed_reads_new_task_without_from(api):
    from backend import names

    notice = {'kind': 'notice', 'sender_id': 'keeper', 'body': 'New task from keeper: Health: Watchers'}
    with api.app.state.store.read() as c:
        names.annotate(c, {'messages': [notice]}, render_notices=True)
        bot = {'kind': 'notice', 'sender_id': 'keeper', 'body': 'New task from keeper: Health: Watchers'}
        names.annotate(c, {'messages': [bot]})
    assert (
        notice['body'] == 'New task: Health: Watchers'
        and notice['body_raw'] == 'New task from keeper: Health: Watchers'
    )
    assert bot['body'] == 'New task from keeper: Health: Watchers'  # bots read it as written


def test_a_keeper_task_scoped_to_a_builtin_names_that_source_as_requester_and_notice(api):
    with api.app.state.store.transaction() as c:
        with sources.writing_as('check-health'):
            made = H.task_create(c, H.KEEPER, 'Health: Check', 'Fix the check.', 'bot:ops', lint=False)
        notice = c.execute(
            "SELECT body FROM messages WHERE conversation_id=? AND kind='notice' ORDER BY created_at DESC LIMIT 1",
            (made['conversation_id'],),
        ).fetchone()

    task = next(t for t in get(api, 'tasks')['tasks'] if t['id'] == made['id'])
    assert (
        task['requester_id'] == 'keeper' and task['requester_source'] == 'check-health' and task['origin_actor'] is None
    )
    assert notice['body'] == 'New task from Health check: Health: Check'
