"""Helpers that other tests share, moved out of test_live_meetings.py so they import no test file."""

import pytest
from backend.auth import Identity
from backend.store import H, digest
from backend.tests.helpers.api import headers, post


@pytest.fixture
def live(api):
    settings = api.app.state.store.settings
    settings.test_identities.update(
        {
            "ops-live": Identity("bot:ops", "bot", agent="hermes"),
            "finance-live": Identity("bot:finance", "bot", agent="hermes"),
        }
    )
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT OR REPLACE INTO agents(bot,harness,token_hash,created_at,created_by) VALUES(?,?,?,?,?)",
            ("ops", "hermes", digest("synthetic-ops-agent"), H.now(), "human:ana"),
        )
        c.execute(
            "INSERT OR REPLACE INTO agents(bot,harness,token_hash,created_at,created_by) VALUES(?,?,?,?,?)",
            ("finance", "hermes", digest("synthetic-finance-agent"), H.now(), "human:ana"),
        )
    yield api
    assert settle(api)


def ready(api, r, bots, capabilities=("private_lanes_v1",)):
    """A current runner: it advertises private lanes, so it takes a private meeting's turns (execution.PRIVATE_LANES)."""
    return post(
        api,
        "runners/heartbeat",
        {
            "version": "test",
            "platform": "test",
            "capabilities": list(capabilities),
            "readiness": {bot: True for bot in bots},
        },
        token=r["token"],
    )


def settle(api):
    """Wait for the meeting routing a write started off its request (live_meetings.Routing)."""
    return api.app.state.live_meeting_routing.settle(timeout=60)


def attach(api, mid, *bots, token="ana-test"):
    return post(api, f"live-meetings/{mid}/bots", {"bots": list(bots)}, token=token)


def chunk(api, mid, seq, start, end, text="Transcript", *, token="ana-test", expected=200):
    response = api.post(
        "/api/v2/live-meetings/" + mid + "/chunks",
        json={"chunks": [{"seq": seq, "speaker": "Ana", "start_ms": start, "end_ms": end, "text": text}]},
        headers=headers(token),
    )
    assert response.status_code == expected, response.text
    settle(api)
    return response.json() if expected == 200 else response
