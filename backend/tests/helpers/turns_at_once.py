"""Helpers that other tests share, moved out of test_turns_at_once.py so they import no test file."""

from backend.tests.helpers.api import post


def set_computer(api, machine, limit, expected=200):
    return post(api, f"computers/{machine['runner_id']}/turns-at-once", {"turns_at_once": limit}, expected=expected)
