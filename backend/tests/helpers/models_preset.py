"""Helpers that other tests share, moved out of test_models_preset.py so they import no test file."""

from backend import providers
from backend.tests.helpers.api import post
from backend.tests.helpers.gateway_connection import KEY, fake_gateway, save, setup


CLAUDE = {"runtime": "claude", "harness": "claude-code", "model": "claude-opus-5-5"}


def choose(api, preset, enabled=("anthropic", "openai")):
    with api.app.state.store.transaction() as c:
        record = providers.load(c, api.app.state.store.settings)
        providers.save(
            c,
            "human:ana",
            {
                "enabled": list(enabled),
                "runtime": "claude",
                "model": CLAUDE["model"],
                "preset": preset,
                "expected_revision": record["revision"],
            },
            "2026-10-19T00:00:00Z",
        )


def says(readiness, isolated):
    """`readiness` saying it isolates bots (True/False), nothing (None), or "cloud": a cloud machine, not isolated
    but one company's trust group (runner-rs/src/cloud_machine.rs), which runs Tico models too."""
    if isolated == "cloud":
        readiness.update(isolated=False, trust="cloud_machine")
    elif isolated is not None:
        readiness["isolated"] = isolated


def connected(api, monkeypatch):
    setup(api, monkeypatch)
    with fake_gateway() as fake:
        fake.active(KEY, 0.1)
        save(api, fake)


def on_tico(api, machine, isolated):
    """ops on Tico Medium 1.0 itself, ready on `machine`, which says (or does not say) it runs bots as their own user."""
    from backend.tests.helpers.gateway_connection import put_bot_on

    put_bot_on(api, "ops", "tico-medium-1.0")
    readiness = {
        "schema_version": 1,
        "runtimes": {"pi": {"installed": True, "authenticated": "missing"}},
        "bots": {
            "ops": {
                "ready": True,
                "runtime": "pi",
                "model": "tico-medium-1.0",
                "repository_present": True,
                "configuration_valid": True,
            }
        },
    }
    says(readiness, isolated)
    post(
        api,
        "runners/heartbeat",
        {"version": "test", "platform": "test", "readiness": readiness},
        token=machine["token"],
    )
