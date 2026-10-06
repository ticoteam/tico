"""Every stable GET keeps the names supplied by the response annotation contract."""
import pytest
from fastapi.routing import APIRoute

from backend import meetings
from backend.store import H
from backend.tests.test_api import api, headers, post  # noqa: F401


@pytest.fixture
def named_data(api):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE humans SET name='Ana' WHERE id='ana'")
        c.execute("UPDATE bots SET display_name='Operations' WHERE slug='ops'")
        task = H.task_create(c, 'human:ana', 'Review the plan', 'Please review the plan.', 'bot:ops')
        ask = H.say(c, 'bot:ops', 'human:ana', 'Please choose a review date.',
                    conversation_id=task['conversation_id'], kind='ask', refs={'task': task['id']})
        notice = H.notice(c, 'bot:ops', 'human:ana', 'New task from bot:ops: Review the plan')
        meetings.snapshot({'id': 'review-meeting', 'title': 'Review the plan', 'kind': 'meeting',
                           'private': False, 'owner': 'ana@example.com', 'status': 'done'},
                          transcript='Please review the plan.', conn=c)
    doc = post(api, 'docs', {'title': 'Review plan', 'body': 'Please review the plan.'})['doc']
    return {'task': task, 'ask': ask, 'notice': notice, 'doc': doc}


def test_all_api_routes_keep_the_app_route_class(api):
    # Also guard aliases and convenience routes outside the stable display-name pattern.
    for route in api.app.routes:
        if isinstance(route, APIRoute):
            assert isinstance(route, api.app.router.route_class), route.path


def test_messages_and_task_asks_have_display_names(api, named_data):
    result = api.get('/api/v2/messages', headers=headers())
    assert result.status_code == 200
    inbox = result.json()
    assert inbox['actors']['human:ana'] == inbox['actor_name'] == 'Ana'
    assert inbox['actors']['bot:ops'] == 'Operations'
    message = next(m for m in inbox['messages'] if m['id'] == named_data['ask']['id'])
    assert message['from_actor_name'] == 'Operations'
    assert message['to_actor_name'] == 'Ana'
    notice = next(m for m in inbox['notices'] if m['id'] == named_data['notice']['id'])
    assert notice['body'] == 'New task from Operations: Review the plan'
    assert notice['body_raw'] == 'New task from bot:ops: Review the plan'

    result = api.get('/api/v2/tasks', headers=headers())
    assert result.status_code == 200
    tasks = result.json()
    task = next(t for t in tasks['tasks'] if t['id'] == named_data['task']['id'])
    assert task['owner_name'] == 'Operations' and task['requester_name'] == 'Ana'
    assert task['ask']['from_actor_name'] == 'Operations'
    assert task['ask']['to_actor_name'] == 'Ana'
    assert tasks['actors']['bot:ops'] == 'Operations' and tasks['actors']['human:ana'] == 'Ana'
