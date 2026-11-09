"""Helpers that other tests share, moved out of test_screen.py so they import no test file."""

from backend.tests.helpers.api import post


def offer(api, r):
    post(
        api,
        "runners/heartbeat",
        {
            "version": "test",
            "platform": "test",
            "capabilities": ["screen_v1"],
            "readiness": {"schema_version": 1, "screen": {"available": True, "width": 1280, "height": 800}},
        },
        token=r["token"],
    )
