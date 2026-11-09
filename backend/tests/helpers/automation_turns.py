"""Helpers that other tests share, moved out of test_automation_turns.py so they import no test file."""

from backend.tests.helpers.api import claim, post


USAGE = {
    "input_tokens": 400_000,
    "cached_tokens": 0,
    "output_tokens": 10_000,
    "model": "claude-opus-5-5",
    "runtime": "claude",
    "billing": "api",
}


def finish(api, r, text="A draft"):
    attempt = claim(api, r)
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "t"}, token=r["token"])
    post(
        api,
        f"attempts/{attempt['id']}/complete",
        {"outcome": "completed", "text": text, "last_seq": 0, "usage": USAGE},
        token=r["token"],
    )
    return attempt


def draft_then_tell(**extra):
    return {
        "name": "Draft",
        "owner": "bot:ops",
        "trigger": {"type": "event", "name": "x.y"},
        **extra,
        "steps": [
            {"id": "draft", "type": "llm", "as": "bot:ops", "prompt": "Draft a reply", "out": "reply"},
            {"id": "tell", "type": "action", "do": "wake_bot", "args": {"bot": "coo", "text": "{{reply.text}}"}},
        ],
    }
