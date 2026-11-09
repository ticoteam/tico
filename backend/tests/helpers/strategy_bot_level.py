"""Helpers that other tests share, moved out of test_strategy_bot_level.py so they import no test file."""

from backend.tests.helpers.api import post


def level(api, n, token="ana-test", expected=200):
    return post(api, "strategy-bot/level", {"level": n}, token=token, expected=expected)
