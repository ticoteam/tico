"""Helpers that other tests share, moved out of test_bot_tools.py so they import no test file."""

from backend.tests.helpers.api import headers
from backend import bot_settings as BS


RUNTIMES = {"codex": {"installed": True, "authenticated": "ready"}}


def configure(api, bot="ops"):
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE bot_config SET repo=? WHERE bot=?', ('acme-co/emp-' + bot, bot))
        BS.write(c, bot, {'name': bot, 'runtime': 'codex', 'model': 'gpt-6-luna', 'reasoning_effort': 'high'})


def report(api, machine, bot, tools):
    row = {
        "ready": True,
        "runtime": "codex",
        "model": "gpt-6-luna",
        "repository_present": True,
        "configuration_valid": True,
        "problems": [],
        **({"tools": tools} if tools is not None else {}),
    }
    return api.post(
        "/api/v2/runners/heartbeat",
        headers=headers(machine["token"]),
        json={
            "version": "test",
            "platform": "test",
            "readiness": {"schema_version": 1, "runtimes": RUNTIMES, "bots": {bot: row}},
        },
    )
