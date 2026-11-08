"""Helpers that other tests share, moved out of test_goal_read_back.py so they import no test file."""

from backend.tests.helpers.api import post


def goal(api, title, owner="ops", **extra):
    return post(api, "goals", {"title": title, "owner": owner, **extra})["goal"]
