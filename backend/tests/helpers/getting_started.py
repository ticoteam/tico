"""Helpers that other tests share, moved out of test_getting_started.py so they import no test file."""

import json
from backend.store import H
from backend.tests.helpers.onboarding import machine


def heartbeat(api, runner_id, *, seconds_ago=0, runtimes=None):
    """A runner's last word, as the heartbeat stores it."""
    with api.app.state.store.transaction() as c:
        c.execute(
            "UPDATE runner_states SET last_seen_at=?, readiness=? WHERE runner_id=?",
            (
                H.shift(H.now(), seconds=-seconds_ago),
                json.dumps({"schema_version": 1, "runtimes": runtimes or {}, "bots": {}}),
                runner_id,
            ),
        )


def enrolled(api):
    machine(api)
    with api.app.state.store.read() as c:
        return c.execute("SELECT id FROM runners").fetchone()["id"]


def add_bot(api, slug, state="active"):
    with api.app.state.store.transaction() as c:
        H.sync_registry(c, {slug: {"name": slug, "status": state}}, None)


SIGNED_IN = {"codex": {"installed": True, "authenticated": "ready"}}
