"""Helpers that other tests share, moved out of test_thread_tasks.py so they import no test file."""

from backend.tests.helpers.api import headers


def make_task(api, body, token="ana-test"):
    response = api.post("/api/v2/tasks", json=body, headers=headers(token))
    assert response.status_code == 200, response.text
    return response.json().get("task", response.json())
