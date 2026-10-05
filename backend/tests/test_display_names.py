"""Every stable GET keeps the names supplied by the response annotation contract."""
import json
from pathlib import Path

import pytest
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.routing import APIRoute, request_response

from backend import meetings, names
from backend.app import STABLE_PATH
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


def test_every_stable_get_matches_reference_annotation(api, named_data, monkeypatch):
    routes = [route for route in api.app.routes if isinstance(route, APIRoute)
              and 'GET' in route.methods and '{' not in route.path and STABLE_PATH.fullmatch(route.path)]
    assert len(routes) >= 40
    reference = {}
    base_handler = APIRoute.get_route_handler

    def observed_handler(route):
        handler = base_handler(route)

        async def observe(request):
            # Capture the serialized handler response before the app's route class touches it.
            # Both answers use this one invocation, so clocks and timing counters match exactly.
            response = await handler(request)
            if isinstance(response, JSONResponse):
                raw = response.body
                with api.app.state.store.read() as c:
                    annotated = names.annotate_json(c, raw, render_notices=True, actors=True)
                reference[route.path] = ('json', json.loads(annotated or raw))
            elif isinstance(response, FileResponse):
                reference[route.path] = ('bytes', Path(response.path).read_bytes())
            elif isinstance(response, StreamingResponse):
                chunks = []
                stream = response.body_iterator

                async def capture():
                    async for chunk in stream:
                        chunks.append(chunk.encode() if isinstance(chunk, str) else bytes(chunk))
                        yield chunk
                    reference[route.path] = ('bytes', b''.join(chunks))
                response.body_iterator = capture()
            else:
                reference[route.path] = ('bytes', response.body)
            return response
        return observe

    monkeypatch.setattr(APIRoute, 'get_route_handler', observed_handler)
    params = {'/api/v2/docs/search': {'q': 'review'}, '/api/v2/context/search': {'q': 'review'},
              '/api/v2/context/document': {'id': named_data['doc']['id']},
              '/api/v2/meetings/transcript': {'id': 'review-meeting'},
              '/api/v2/github/product-repos/preview': {'name': 'review-product'}}
    checked = set()
    for route in routes:
        monkeypatch.setattr(route, 'app', request_response(route.get_route_handler()))
        result = api.get(route.path, params=params.get(route.path), headers=headers(), follow_redirects=False)
        if route.path == '/auth/login':
            # This fixture uses bearer identities, with no browser sign-in provider configured.
            assert result.status_code == 404
        elif route.path == '/api/v2/github/product-repos/preview':
            # No GitHub App is connected in this fixture, so the preview refuses before naming anyone.
            assert result.status_code == 409 and result.json()['error']['code'] == 'github_not_connected'
        else:
            assert result.status_code == 200, (route.path, result.text)
            assert route.path in reference, route.path
            kind, expected = reference[route.path]
            assert (result.json() if kind == 'json' else result.content) == expected, route.path
            checked.add(route.path)
    assert checked == {route.path for route in routes} - {'/auth/login', '/api/v2/github/product-repos/preview'}


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
