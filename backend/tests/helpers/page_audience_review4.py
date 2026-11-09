"""Helpers that other tests share, moved out of test_page_audience_review4.py so they import no test file."""

from backend.tests.helpers.api import post
from backend.tests.helpers.files import turn
from backend.tests.helpers.org_rules import org
from backend.tests.helpers.pages import page


def public_page(api):
    org(api)
    _, attempt = turn(api)
    tid = post(api, "tasks", {"owner": "ops", "title": "Review drafts", "body": "Review", "private": False})["id"]
    made = page(api, attempt, task=tid, title="Confidential draft")
    assert made.status_code == 200, made.text
    return tid, attempt, made.json()["page"]["file_id"]
