"""Helpers that other tests share, moved out of test_task_review.py so they import no test file."""

from backend.tests.helpers.api import headers, post


def task(api, title="Review the draft"):
    return post(api, "tasks", {"owner": "ops", "title": title, "body": "Read the report."})["id"]


def get(api, path, who="ana-test"):
    response = api.get("/api/v2/" + path, headers=headers(who))
    assert response.status_code == 200, response.text
    return response.json()
