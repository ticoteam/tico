"""Helpers that other tests share, moved out of test_agents.py so they import no test file."""

from backend.store import H
from backend.tests.helpers.api import post


def hermes_bot(api, slug="scout", status="active", token="ana-test", **overrides):
    return post(
        api,
        "bots",
        {
            "slug": slug,
            "display_name": "Scout",
            "description": "A Hermes profile.",
            "reports_to": None,
            "status": status,
            "repo": "emp-" + slug,
            "thread_mode": "personal",
            "model": "hermes-profile",
            "effort": "as-configured",
            "harness": "hermes",
            "operator": "ana",
            "owners": ["ana"],
            "runner_id": None,
            **overrides,
        },
        token=token,
    )


def credential(api, slug="scout"):
    return post(api, "bots/" + slug + "/agent-credential", {})


def bot_id(api, slug="scout"):
    with api.app.state.store.read() as c:
        return H.resolve_bot(c, slug)["id"]  # jobs, agents, health issues and actors are keyed by the id


def beat(api, token, **fields):
    return post(
        api,
        "agents/heartbeat",
        {
            "version": "0.19.0",
            "platform": "darwin",
            "model": "gpt-5.6-luna",
            "provider": "openai-codex",
            "profile": "scout",
            **fields,
        },
        token=token,
    )
