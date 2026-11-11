"""Every stable GET keeps the names supplied by the response annotation contract."""

import pytest
from fastapi.routing import APIRoute

from backend import meetings
from backend.store import H
from backend.tests.old_asks import old_ask
from backend.tests.helpers.api import headers, post  # noqa: F401


@pytest.fixture
def named_data(api):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE humans SET name='Ana' WHERE id='ana'")
        c.execute("UPDATE bots SET display_name='Operations' WHERE slug='ops'")
        task = H.task_create(c, 'human:ana', 'Review the plan', 'Please review the plan.', 'bot:ops')
        # An old ask in a task's thread, read as history, and the bot's ask on the task to a person.
        ask = old_ask(
            c,
            'bot:ops',
            'human:ana',
            '@ana Please choose a review date.',
            conversation_id=task['conversation_id'],
            refs={'task': task['id']},
        )
        c.execute("UPDATE humans SET name='Ben' WHERE id='ben'")
        question = H.task_ask(
            c, 'bot:ops', task['id'], 'human:ben', 'Which review date?', seen_version=H.task(c, task['id'])['version']
        )
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


def test_messages_tasks_and_asks_have_display_names(api, named_data):
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
    assert tasks['actors']['bot:ops'] == 'Operations' and tasks['actors']['human:ana'] == 'Ana'
    assert task['turn']['who'] == 'human:ben'


def test_the_person_a_task_waits_on_is_named_though_they_are_not_on_the_task(api, named_data):
    """names.LISTED_KEYS: the `who` and `asked_by` of a task's waits and requirements go into `actors`, which
    `tico task show` prints names from."""
    task_id = named_data['task']['id']
    tasks = api.get('/api/v2/tasks', headers=headers()).json()
    assert tasks['actors']['human:ben'] == 'Ben'
    full = api.get(f'/api/v2/tasks/{task_id}', headers=headers()).json()
    ask = next(r for r in full['task']['to_leave'] if r['requirement_id'] == named_data['question']['requirement_id'])
    assert (ask['who'], ask['asked_by']) == ('human:ben', 'bot:ops')
    assert full['actors']['human:ben'] == 'Ben'


def test_a_pipelines_manager_is_named_though_it_holds_no_stage(api):
    """Acme rc.3 bug 8: `GET /api/v2/pipelines` named only the bots that own a stage, so a manager that holds none (the
    General Manager on General, a team's Manager) was left out of `actors`."""
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET display_name='Operations' WHERE slug='ops'")
        c.execute("UPDATE pipelines SET manager_id='bot:ops' WHERE id='general'")
        assert not c.execute("SELECT 1 FROM stages WHERE pipeline_id='general' AND owner='bot:ops'").fetchone()
    listed = api.get('/api/v2/pipelines', headers=headers()).json()
    general = next(p for p in listed['pipelines'] if p['id'] == 'general')
    assert general['manager_id'] == 'bot:ops' and listed['actors']['bot:ops'] == 'Operations'


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
