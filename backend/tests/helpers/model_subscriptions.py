"""Helpers that other tests share, moved out of test_model_subscriptions.py so they import no test file."""

import base64
import json
from backend import model_subscriptions as MS
from backend.store import encode
from backend.tests.helpers.api import assign, post, put, runner
from backend import bot_settings as BS


TOKEN = "sk-ant-oat01-" + "a" * 24


OTHER = "sk-ant-oat01-" + "b" * 24


def codex_file(account="acct-1", refresh="r-1", email="ana@acme.example"):
    claims = base64.urlsafe_b64encode(json.dumps({"email": email}).encode()).decode().rstrip("=")
    return json.dumps(
        {
            "tokens": {
                "id_token": f"h.{claims}.s",
                "access_token": "a-" + refresh,
                "refresh_token": refresh,
                "account_id": account,
            }
        }
    )


def company(api, enabled=("anthropic", "openai")):
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM registry_metadata WHERE key='providers'")
        c.execute(
            "INSERT INTO registry_metadata VALUES('providers',?)",
            (encode({"revision": 1, "enabled": list(enabled), "runtime": "claude", "model": "claude-sonnet-5-5"}),),
        )


def on(api, bot, runtime, model):
    with api.app.state.store.transaction() as c:
        config = BS.read(c, bot)
        config.update(runtime=runtime, harness=runtime, model=model)
        BS.write(c, bot, config)


def add(api, kind="claude_token", secret=TOKEN, name="Company Max", token="ana-test", expected=200):
    return post(
        api,
        "model-subscriptions",
        {"kind": kind, "secret": secret, **({"name": name} if name else {})},
        token,
        expected=expected,
    )


def give(api, m, runtime, sid, confirm=False, token="ana-test", expected=200):
    return put(
        api,
        "computer-subscriptions",
        {"runner_id": m["runner_id"], "runtime": runtime, "subscription": sid, "confirm": confirm},
        token,
        expected=expected,
    )


def machine(api, signed_in=False, takes=True, bot="ops", claude=None, label="Test Mac"):
    m = runner(api, label=label)
    assign(api, m, bot)
    post(
        api,
        "runners/heartbeat",
        {
            "version": "test",
            "platform": "test",
            "capabilities": [MS.CAPABILITY] if takes else [],
            "readiness": {
                "schema_version": 1,
                "runtimes": {
                    "claude": {"installed": True, "authenticated": claude or ("ready" if signed_in else "missing")},
                    "codex": {"installed": True, "authenticated": "missing"},
                },
                "bots": {bot: {"runtime": "claude", "ready": True}},
            },
        },
        token=m["token"],
    )
    return m
