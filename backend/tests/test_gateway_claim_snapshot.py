"""Synthetic checks that a run's Gateway key follows what its claim handed it out on (fallback and check cases)."""
import json

import pytest

from backend.store import H
from backend.tests.test_api import api, assign, claim, get, post, ready, runner  # noqa: F401
from backend.tests.test_gateway_connection import KEY, fake_gateway, finish, handed_to, put_bot_on, save, setup


def set_fallback(api, bot, fallback):
    with api.app.state.store.transaction() as c:
        config = json.loads(c.execute("SELECT config FROM bot_config WHERE bot=?", (bot,)).fetchone()[0])
        config = {k: v for k, v in config.items() if k != "fallback"}
        c.execute("UPDATE bot_config SET config=? WHERE bot=?",
                  (json.dumps({**config, **({"fallback": fallback} if fallback else {})}), bot))


TICO_CHECK = {"runtime": "pi", "model": "tico-mid-1.0", "harness": "pi", "effort": "", "role": "review"}


@pytest.mark.parametrize("change", ["fallback_added", "fallback_removed", "check_reassigned"])
def test_the_key_follows_the_claims_fallback_and_check(api, monkeypatch, change):
    from backend import check_models
    setup(api, monkeypatch)
    with fake_gateway() as fake:
        fake.active(KEY, 0.1)
        save(api, fake)
    put_bot_on(api, "ops", "kimi-k3")
    if change == "fallback_removed":
        set_fallback(api, "ops", {"harness": "pi", "model": "tico-mid-1.0"})
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    if change == "check_reassigned":
        task = post(api, "tasks", {"owner": "ops", "title": "Synthetic check", "body": "Use only the synthetic fixture"})
        task_id = (task.get("task") or task)["id"]
        with api.app.state.store.transaction() as c:
            check_models.record(c, "bot:ops", task_id, TICO_CHECK)
    else:
        post(api, "chat/ops", {"text": "Use only the synthetic fixture"})
    attempt = claim(api, machine, "ops")
    on_tico = change != "fallback_added"
    assert attempt["config"]["model"] == ("tico-mid-1.0" if change == "check_reassigned" else "kimi-k3")
    # While the run is under way, the setting moves.
    if change == "fallback_added":
        set_fallback(api, "ops", {"harness": "pi", "model": "tico-mid-1.0"})
    elif change == "fallback_removed":
        set_fallback(api, "ops", None)
    else:
        with api.app.state.store.transaction() as c:                         # the check is handed to another bot
            c.execute("UPDATE tasks SET owner='bot:finance' WHERE id=?", (task_id,))
            assert check_models.of(c, task_id, "ops") is None
    handed = handed_to(api, machine, attempt)
    assert ("TICOAI_GATEWAY_KEY" in handed) is on_tico, (change, list(handed))
    finish(api, machine, attempt, "Tico AI Gateway: no Tico route for this request (no_route)")
    # A finished run's computer gets nothing, and nothing is left behind.
    handed_to(api, machine, attempt, expected=409)
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM registry_metadata WHERE key LIKE 'gateway-attempt:%'").fetchone()[0] == 0
