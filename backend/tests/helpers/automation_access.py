"""Helpers that other tests share, moved out of test_automation_access.py so they import no test file."""

WAKE = {"id": "tell", "type": "action", "do": "wake_bot", "args": {"bot": "ops", "text": "{{message.subject}}"}}


def flow(owner="person:ana", trigger=None, steps=(WAKE,)):
    return {
        "name": "Test",
        "owner": owner,
        "trigger": trigger or {"type": "event", "name": "x.y"},
        "steps": list(steps),
    }
