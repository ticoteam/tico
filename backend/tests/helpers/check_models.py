"""Helpers that other tests share, moved out of test_check_models.py so they import no test file."""

from backend.store import encode
from backend.tests.helpers.api import assign, claim, post, runner
from backend import bot_settings as BS


GROK = {"model": "grok-4.6", "runtime": "grok", "harness": "grok", "billing": "api"}


def company(api, *enabled):
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM registry_metadata WHERE key='providers'")
        c.execute(
            "INSERT INTO registry_metadata VALUES('providers',?)",
            (encode({"revision": 1, "enabled": list(enabled), "runtime": "claude", "model": "claude-sonnet-5-5"}),),
        )


def heartbeat(api, machine, grok="ready"):
    post(
        api,
        "runners/heartbeat",
        {
            "version": "test",
            "platform": "test",
            "capabilities": ["task_lanes_v2", "session_roots_v1"],
            "readiness": {
                "schema_version": 1,
                "runtimes": {
                    "claude": {"installed": True, "authenticated": "ready"},
                    "grok": {"installed": True, "authenticated": grok},
                },
                "bots": {"ops": {"runtime": "claude", "ready": True}},
            },
        },
        token=machine["token"],
    )


def parent_turn(api):
    """ops on Claude, working a person's task: its claimed turn."""
    company(api, "anthropic", "xai")
    with api.app.state.store.transaction() as c:
        config = BS.read(c, 'ops')
        config.update(
            runtime="claude",
            harness="claude",
            model="claude-sonnet-5-5",
            fallback={"harness": "codex", "model": "gpt-6.1-sol"},
        )
        BS.write(c, 'ops', config)
    machine = runner(api)
    assign(api, machine, "ops")
    heartbeat(api, machine)
    parent = post(
        api, "tasks", {"owner": "ops", "title": "Ship the import fix", "body": "Open the PR and get it reviewed."}
    )
    turn = claim(api, machine)
    assert turn["task"]["id"] == parent["id"] and turn["config"]["runtime"] == "claude"
    start(api, machine, turn)  # a run files its sub-tasks once it has started
    return machine, parent, turn


def start(api, machine, turn):
    post(api, f"attempts/{turn['id']}/started", {"thread_id": "t-" + turn["id"][:8]}, machine["token"])


def finish(api, machine, turn, segment=None, during=None):
    """Start the turn (unless it has started) and complete it; `during` runs while it is running."""
    with api.app.state.store.read() as c:
        state = c.execute("SELECT state FROM attempts WHERE id=?", (turn["id"],)).fetchone()
    if state and state[0] == "leased":
        start(api, machine, turn)
    if during:
        during()
    usage = (
        {
            **segment,
            "input_tokens": 30,
            "output_tokens": 30,
            "segments": [{**segment, "input_tokens": 30, "output_tokens": 30}],
        }
        if segment
        else None
    )
    post(
        api,
        f"attempts/{turn['id']}/complete",
        {"outcome": "completed", "last_seq": 0, "text": "Done.", **({"usage": usage} if usage else {})},
        machine["token"],
    )
