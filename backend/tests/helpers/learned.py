"""Helpers that other tests share, moved out of test_learned.py so they import no test file."""

from backend.tests.helpers.api import post


def learn(api, text, layer="personal", token="ana-test", expected=200):
    return post(api, "assistant/learned", {"layer": layer, "text": text}, token=token, expected=expected)
