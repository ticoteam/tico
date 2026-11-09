"""Helpers that other tests share, moved out of test_meeting_task_links.py so they import no test file."""

from backend import hubdb as H
from backend.tests.helpers.api import get, headers, post
from backend.tests.helpers.meeting_items import record


def task(api, title='Review the plan', requester='human:ana', owner='human:ben', **kw):
    with api.app.state.store.transaction() as c:
        return H.task_create(c, requester, title, 'Review the plan and report back.', owner, lint=False, **kw)


def link(api, rid, ids, token='ana-test', kind='linked', expected=200, key=None):
    return post(api, f'meetings/{rid}/tasks', {'task_ids': ids, 'kind': kind}, token, key, expected)


def remove(api, rid, tid, token='ana-test', expected=200):
    response = api.delete(f'/api/v2/meetings/{rid}/tasks/{tid}', headers=headers(token))
    assert response.status_code == expected, response.text
    return response.json()
