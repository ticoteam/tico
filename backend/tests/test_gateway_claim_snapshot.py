"""Synthetic checks that a run's Gateway key follows what its claim handed it out on (fallback and check cases)."""

import json

import pytest

from backend.store import H
from backend.tests.helpers.api import assign, claim, get, post, ready, runner  # noqa: F401
from backend.tests.helpers.gateway_connection import KEY, fake_gateway, handed_to, put_bot_on, save, setup
from backend import bot_settings as BS


def set_fallback(api, bot, fallback):
    with api.app.state.store.transaction() as c:
        config = BS.read(c, bot)
        config = {k: v for k, v in config.items() if k != "fallback"}
        BS.write(c, bot, {**config, **({'fallback': fallback} if fallback else {})})


TICO_CHECK = {"runtime": "pi", "model": "tico-medium-1.0", "harness": "pi", "effort": "", "role": "review"}


@pytest.mark.parametrize("change", ["fallback_added", "fallback_removed", "check_reassigned"])
def test_the_key_follows_the_claims_fallback_and_check(api, monkeypatch, change):
    from backend import check_models

    setup(api, monkeypatch)
    with fake_gateway() as fake:
        fake.active(KEY, 0.1)
        save(api, fake)
    put_bot_on(api, "ops", "kimi-k3")
    if change == "fallback_removed":
        set_fallback(api, "ops", {"harness": "pi", "model": "tico-medium-1.0"})
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    if change == "check_reassigned":
        task = post(
            api, "tasks", {"owner": "ops", "title": "Synthetic check", "body": "Use only the synthetic fixture"}
        )
        task_id = (task.get("task") or task)["id"]
        with api.app.state.store.transaction() as c:
            check_models.record(c, "bot:ops", task_id, TICO_CHECK)
    else:
        post(api, "chat/ops", {"text": "Use only the synthetic fixture"})
    attempt = claim(api, machine, "ops")
    on_tico = change != "fallback_added"
    assert attempt["config"]["model"] == ("tico-medium-1.0" if change == "check_reassigned" else "kimi-k3")
    # While the run is under way, the setting moves.
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "gateway"}, machine["token"])
    if change == "fallback_added":
        set_fallback(api, "ops", {"harness": "pi", "model": "tico-medium-1.0"})
    elif change == "fallback_removed":
        set_fallback(api, "ops", None)
    else:
        with api.app.state.store.transaction() as c:  # the check is handed to another bot
            H.task_reassign(c, "human:ana", task_id, "bot:finance", "Finance takes the check.")
            assert check_models.of(c, task_id, "ops") is None
    handed = handed_to(api, machine, attempt)
    assert ("TICOAI_GATEWAY_KEY" in handed) is on_tico, (change, list(handed))
    error = "Tico AI Gateway: no Tico route for this request (no_route)"
    post(
        api,
        f"attempts/{attempt['id']}/events",
        {"events": [{"seq": 1, "kind": "error", "payload": {"error": error, "limit": False}}]},
        machine["token"],
    )
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "failed", "last_seq": 1, "text": ""}, machine["token"])
    # A finished run's computer gets nothing, and nothing is left behind.
    handed_to(api, machine, attempt, expected=409)
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM registry_metadata WHERE key LIKE 'gateway-attempt:%'").fetchone()[0] == 0
