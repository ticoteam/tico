"""Helpers that other tests share, moved out of test_lifecycle.py so they import no test file."""

from backend.tests.helpers.api import post


def debrief(api):
    """An automation that gives ops a turn on each shared meeting."""
    return post(
        api,
        "automations",
        {
            "name": "Meeting debrief",
            "owner": "person:ana",
            "trigger": {"type": "event", "name": "meeting.ready"},
            "steps": [
                {
                    "id": "turn",
                    "type": "bot_turn",
                    "bot": "bot:ops",
                    "prompt": "Pull the product asks out of this meeting.",
                }
            ],
        },
    )["id"]
