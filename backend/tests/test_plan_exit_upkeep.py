"""Plan upkeep uses the owner's exit even when it has no assigned agents."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from backend import model_subscriptions as MS, plan_exits, runner_states, subscription_refresh as SR
from backend.repositories import metadata
from backend.store import H
from backend.tests.helpers.api import assign, claim, get, post, put
from backend.tests.helpers.claude_login import access, full, refresh
from backend.tests.helpers.credentials import setup
from backend.tests.helpers.model_subscriptions import OTHER, TOKEN, add, company, on, pick

EMPTY = {"refreshes": [], "tokens": [], "usage": []}
CONNECT = "model-subscriptions/connect"


def computer(api, label, platform="linux"):
    code = post(api, "enrollments", {"operator": "ana"})["code"]
    machine = post(api, "runners/enroll", {"code": code, "label": label, "platform": platform})
    machine["platform"] = platform
    heartbeat(api, machine)
    return machine


def heartbeat(api, machine, bot=None, **runtime):
    post(
        api,
        "runners/heartbeat",
        {
            "version": "test",
            "platform": machine["platform"],
            "capabilities": [
                "model_subscriptions_v1",
                "model_connect_v1",
                "claude_login_v1",
                "plan_usage_v1",
                "attempt_credentials_v1",
                "model_exit_v1",
            ],
            "readiness": {
                "schema_version": 1,
                "runtimes": {"claude": {"installed": True, **runtime}},
                "bots": {bot: {"runtime": "claude", "ready": True}} if bot else {},
            },
        },
        machine["token"],
    )


@pytest.fixture
def computers(api, monkeypatch):
    setup(api)
    company(api)
    non_exit = computer(api, "Non-exit")
    exit_runner = computer(api, "Exit")
    monkeypatch.setattr(plan_exits, "UPKEEP_ROUTING", True)
    return exit_runner, non_exit


def select(api, machine):
    return put(api, "model-subscriptions/plan-exits/ana", {"runner_id": machine["runner_id"]})["generation"]


def agent_on(api, machine, bot, sid):
    on(api, bot, "claude", "")
    assign(api, machine, bot)
    heartbeat(api, machine, bot)
    pick(api, bot, "claude", sid)


def turn(api, machine, bot):
    post(api, f"chat/{bot}", {"text": "Summarize the account."})
    return claim(api, machine)["id"]


def poll(api, machine, sid, revision=1):
    return post(api, "runner-claude-logins", {"live": [{"subscription": sid, "revision": revision}]}, machine["token"])


def request_usage(api, sid):
    return post(api, f"model-subscriptions/{sid}/usage-refresh", {})


def window(percent=42):
    return {"used_percent": percent, "resets_at": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()}


def test_full_login_upkeep_and_token_acknowledgments_only_use_the_exit(api, computers):
    exit_runner, non_exit = computers
    sid = add(api, "claude_login", full(minutes=30))["id"]
    select(api, exit_runner)
    agent_on(api, non_exit, "ops", sid)
    assert poll(api, non_exit, sid) == EMPTY
    [job] = poll(api, exit_runner, sid)["refreshes"]
    assert json.loads(job["value"])["claudeAiOauth"]["refreshToken"] == refresh(1)

    agent_on(api, exit_runner, "cpo", sid)
    outside = turn(api, non_exit, "ops")
    local = turn(api, exit_runner, "cpo")
    result = {"lease": job["lease"], "revision": 1, "state": "succeeded", "value": full(n=2)}
    refreshed = f"runner-claude-logins/{sid}/refreshed"
    assert post(api, refreshed, result, non_exit["token"]) == {"stored": False}
    assert post(api, refreshed, result, exit_runner["token"]) == {"stored": True, "revision": 2}

    request_usage(api, sid)
    assert poll(api, non_exit, sid) == EMPTY
    answer = poll(api, exit_runner, sid)
    [token] = answer["tokens"]
    assert json.loads(token["value"])["claudeAiOauth"]["accessToken"] == access(2)
    [usage] = answer["usage"]
    assert usage["token"] == access(2)
    usage_body = {"id": usage["id"], "state": "succeeded", "seven_day": window()}
    post(api, f"runner-claude-logins/{sid}/usage", usage_body, non_exit["token"], expected=404)
    assert post(api, f"runner-claude-logins/{sid}/usage", usage_body, exit_runner["token"])["state"] == "succeeded"
    assert poll(api, non_exit, sid, 2) == EMPTY
    assert poll(api, exit_runner, sid, 2)["tokens"] == []
    store = api.app.state.store
    with store.transaction() as c:
        assert MS.handed(c, outside, "claude")["revision"] == 1
        assert MS.handed(c, local, "claude")["revision"] == 2
        c.execute("UPDATE attempt_subscriptions SET revision=1 WHERE attempt_id=?", (local,))

    select(api, non_exit)
    select(api, exit_runner)
    assert post(api, refreshed, result, exit_runner["token"]) == {"stored": False}
    post(api, f"runner-claude-logins/{sid}/usage", usage_body, exit_runner["token"], expected=404)
    assert poll(api, exit_runner, sid)["tokens"] == []
    poll(api, exit_runner, sid, 2)
    with store.read() as c:
        assert MS.handed(c, local, "claude")["revision"] == 1


def test_an_outstanding_refresh_stays_uncertain_after_the_exit_changes(api, computers):
    exit_runner, non_exit = computers
    sid = add(api, "claude_login", full(minutes=30))["id"]
    select(api, exit_runner)
    [job] = poll(api, exit_runner, sid)["refreshes"]
    select(api, non_exit)
    select(api, exit_runner)
    result = {"lease": job["lease"], "revision": job["revision"], "state": "succeeded", "value": full(n=2)}
    assert post(api, f"runner-claude-logins/{sid}/refreshed", result, exit_runner["token"]) == {"stored": False}
    assert poll(api, exit_runner, sid)["refreshes"] == []
    with api.app.state.store.read() as c:
        assert MS.row(c, sid)["revision"] == job["revision"]


def test_scheduled_usage_uses_the_exit_even_without_attempts(api, computers):
    exit_runner, non_exit = computers
    sid = add(api, "claude_login", full())["id"]
    select(api, exit_runner)
    agent_on(api, non_exit, "ops", sid)
    with api.app.state.store.transaction() as c:
        runner_states.save(c, exit_runner["runner_id"], last_seen_at=H.shift(H.now(), hours=-1))
    assert poll(api, non_exit, sid) == EMPTY
    post(api, f"model-subscriptions/{sid}/usage-refresh", {}, expected=409)

    heartbeat(api, exit_runner)
    [job] = poll(api, exit_runner, sid)["usage"]
    assert job["subscription"] == sid and job["token"] == access(1)


def test_rotation_advances_the_tunnel_lease_atomically(api, computers):
    exit_runner, non_exit = computers
    sid = add(api, "claude_login", full(minutes=30))["id"]
    select(api, exit_runner)
    agent_on(api, non_exit, "ops", sid)
    attempt = turn(api, non_exit, "ops")
    store = api.app.state.store
    with store.transaction() as c:
        # Turn routing is dark. Stand in for a claim that chose tunnel delivery.
        c.execute("UPDATE attempt_subscriptions SET delivery='model_exit' WHERE attempt_id=?", (attempt,))
    [job] = poll(api, exit_runner, sid)["refreshes"]
    result = {"lease": job["lease"], "revision": job["revision"], "state": "succeeded", "value": full(n=2)}
    post(api, f"runner-claude-logins/{sid}/refreshed", result, exit_runner["token"])
    with store.read() as c:
        assert MS.handed(c, attempt, "claude")["revision"] == 2
        sub = MS.model_exit_payer(c, non_exit["runner_id"], attempt)
        assert sub["id"] == sid
        assert MS.model_exit_credential(c, api.app.state.vault, sub) == {"access_token": access(2)}


def test_setup_token_poll_and_report_fence_legacy_work_and_same_runner_reselection(api, computers, monkeypatch):
    exit_runner, non_exit = computers
    sid = add(api)["id"]
    select(api, exit_runner)
    agent_on(api, non_exit, "ops", sid)
    path = "runner-subscription-refreshes"
    report = {"subscription": sid, "runtime": "claude", "state": "succeeded", "plan": window()}

    with monkeypatch.context() as legacy:
        legacy.setattr(plan_exits, "UPKEEP_ROUTING", False)
        request_usage(api, sid)
        [old] = get(api, path, non_exit["token"])["refreshes"]
        assert old["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == TOKEN
    assert get(api, path, non_exit["token"])["refreshes"] == []
    post(api, f"{path}/{old['id']}/report", report, non_exit["token"], expected=404)

    request_usage(api, sid)
    [job] = get(api, path, exit_runner["token"])["refreshes"]
    assert job["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == TOKEN
    post(api, f"{path}/{job['id']}/report", report, non_exit["token"], expected=404)
    select(api, non_exit)
    select(api, exit_runner)
    assert get(api, path, exit_runner["token"])["refreshes"] == []
    post(api, f"{path}/{job['id']}/report", report, exit_runner["token"], expected=404)
    pending = {key: value for key, value in job.items() if key != "env"}
    with api.app.state.store.read() as c:
        assert SR.plan_with_value(c, api.app.state.vault, exit_runner["runner_id"], pending) == pending
    request_usage(api, sid)
    [current] = get(api, path, exit_runner["token"])["refreshes"]
    assert current["id"] != job["id"]
    assert post(api, f"{path}/{current['id']}/report", report, exit_runner["token"])["state"] == "succeeded"


def test_turn_windows_from_non_exit_computers_are_ignored(api, computers):
    exit_runner, non_exit = computers
    sid = add(api)["id"]
    select(api, exit_runner)
    agent_on(api, non_exit, "ops", sid)
    turn(api, non_exit, "ops")
    agent_on(api, exit_runner, "cpo", sid)
    turn(api, exit_runner, "cpo")
    origin = {"origin": "subscription", "subscription_id": sid}
    heartbeat(api, exit_runner, "cpo", weekly={**window(42), "reported_at": H.now(), **origin})
    heartbeat(api, non_exit, "ops", weekly={**window(95), "reported_at": H.now(), **origin})
    with api.app.state.store.read() as c:
        assert metadata(c, SR.plan_key(sid))["turn_weekly"]["used_percent"] == 42
    [plan] = get(api, "model-subscriptions")["subscriptions"]
    assert plan["usage"]["used_pct"] == 42


def test_reconnect_is_pinned_and_a_mac_exit_only_routes_setup_tokens(api, computers):
    _, linux = computers
    mac = computer(api, "Mac exit", "darwin")
    sid = add(api)["id"]
    full_id = add(api, "claude_login", full(), name="Full login")["id"]
    select(api, mac)
    with api.app.state.store.read() as c:
        assert plan_exits.routed(c, MS.row(c, full_id)) is None
    request_usage(api, sid)
    assert get(api, "runner-subscription-refreshes", mac["token"])["refreshes"][0]["subscription"] == sid

    request = {"subscription": sid, "confirm": True}
    login = post(api, CONNECT, request)
    assert login["runner_id"] == mac["runner_id"]
    assert post(api, CONNECT, request)["id"] == login["id"]
    path = f"runner-logins/{login['id']}/subscription"
    post(api, path, {"value": OTHER}, linux["token"], expected=404)
    select(api, linux)
    select(api, mac)
    assert post(api, path, {"value": OTHER}, mac["token"])["state"] == "failed"
    current = post(api, CONNECT, request)
    assert current["id"] != login["id"] and current["runner_id"] == mac["runner_id"]
    assert post(api, f"runner-logins/{current['id']}/subscription", {"value": OTHER}, mac["token"])["state"] == "done"
    with api.app.state.store.read() as c:
        assert MS.row(c, sid)["kind"] == "claude_token"
