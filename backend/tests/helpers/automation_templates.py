"""Helpers that other tests share, moved out of test_automation_templates.py so they import no test file."""

from urllib.parse import urlencode
from backend.tests.helpers.api import get


def fill(api, tid, owner, inbox, token="ana-test", expected=200):
    query = urlencode({"owner": owner, "inbox": inbox})
    return get(api, f"automations/templates/{tid}?{query}", token=token, expected=expected)
