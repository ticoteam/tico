"""Plan upkeep uses the owner's exit even when it has no assigned agents."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from backend import claude_login, model_subscriptions as MS, plan_exits, runner_states, subscription_refresh as SR
from backend.repositories import metadata, save_metadata
from backend.store import H
from backend.tests.helpers.api import assign, claim, get, post, put
from backend.tests.helpers.claude_login import access, full, refresh
from backend.tests.helpers.credentials import setup
from backend.tests.helpers.model_subscriptions import OTHER, TOKEN, add, company, legacy_give, on, pick

EMPTY = {"refreshes": [], "tokens": [], "usage": []}
CONNECT = "model-subscriptions/connect"


def computer(api, label, platform="linux"):
    code = post(api, "enrollments", {"operator": "ana"})["code"]
    machine = post(api, "runners/enroll", {"code": code, "label": label, "platform": platform})
    machine["platform"] = platform
    heartbeat(api, machine)
    return machine


def heartbeat(api, machine, bot=None, *, legacy=False, profile=None, **runtime):
    claude = {"installed": True, **runtime}
    post(
        api,
        "runners/heartbeat",
        {
            "version": "test",
            "platform": machine["platform"],
            "capabilities": ["model_subscriptions_v1"]
            if legacy
            else [
                "model_subscriptions_v1",
                "model_connect_v1",
                "computer_logins_v1",
                "claude_login_v1",
                "plan_usage_v1",
                "attempt_credentials_v1",
                "model_exit_v1",
            ],
            "readiness": {
                "schema_version": 1,
                "runtimes": {"claude": claude},
                "bots": {bot: {"runtime": "claude", "ready": True}} if bot else {},
                **({"computer_profile": profile} if profile else {}),
            },
            **({"profiles": [{"name": profile, "runtimes": {"claude": runtime}}]} if profile else {}),
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
    assert post(api, refreshed, result, exit_runner["token"]) == {"stored": True, "revision": 2}
    post(api, f"runner-claude-logins/{sid}/usage", usage_body, exit_runner["token"], expected=404)
    assert poll(api, exit_runner, sid)["tokens"] == []
    poll(api, exit_runner, sid, 2)
    with store.read() as c:
        assert MS.handed(c, local, "claude")["revision"] == 1


def test_only_a_successful_result_from_the_issued_runner_survives_the_exit_change(api, computers):
    exit_runner, non_exit = computers
    sid = add(api, "claude_login", full(minutes=30))["id"]
    select(api, exit_runner)
    [job] = poll(api, exit_runner, sid)["refreshes"]
    select(api, non_exit)
    assert poll(api, non_exit, sid)["refreshes"] == []
    path = f"runner-claude-logins/{sid}/refreshed"
    result = {"lease": job["lease"], "revision": job["revision"], "state": "succeeded", "value": full(n=2)}
    rejected = {"lease": job["lease"], "revision": job["revision"], "state": "rejected"}
    assert post(api, path, rejected, exit_runner["token"]) == {"stored": False}
    with api.app.state.store.read() as c:
        assert MS.row(c, sid)["status"] != "needs_reconnect"
    assert post(api, path, result, non_exit["token"]) == {"stored": False}
    assert post(api, path, result, exit_runner["token"]) == {"stored": True, "revision": 2}


def test_scheduled_usage_uses_the_exit_even_without_attempts(api, computers):
    exit_runner, non_exit = computers
    sid = add(api, "claude_login", full())["id"]
    select(api, exit_runner)
    agent_on(api, non_exit, "ops", sid)
    with api.app.state.store.transaction() as c:
        runner_states.save(c, exit_runner["runner_id"], last_seen_at=H.shift(H.now(), hours=-1))
    assert poll(api, non_exit, sid) == EMPTY
    post(api, f"model-subscriptions/{sid}/usage-refresh", {}, expected=409)
    [plan] = get(api, "model-subscriptions")["subscriptions"]
    assert plan["usage"]["reason"] == "Waiting for the plan's exit computer"

    heartbeat(api, exit_runner)
    [job] = poll(api, exit_runner, sid)["usage"]
    assert job["subscription"] == sid and job["token"] == access(1)
    idle = add(api, "claude_login", full(n=2, email="idle@acme.example"), name="Idle")["id"]
    with api.app.state.store.transaction() as c:
        save_metadata(c, SR.plan_key(idle), {"schedule": {"read_at": H.shift(H.now(), hours=-1)}})
    assert all(job["subscription"] != idle for job in poll(api, exit_runner, idle)["usage"])


def test_claude_poll_reads_exit_selections_once_per_snapshot(api, computers, monkeypatch):
    exit_runner, other = computers
    select_ids = [
        add(api, "claude_login", full(minutes=30, email=email), name=email)["id"]
        for email in ("first@acme.example", "second@acme.example")
    ]
    select(api, exit_runner)
    body = claude_login.Poll.model_validate({"live": [{"subscription": sid, "revision": 1} for sid in select_ids]})
    app = api.app.state
    for enabled, expected in ((True, 1), (False, 0)):
        monkeypatch.setattr(plan_exits, "UPKEEP_ROUTING", enabled)
        with app.store.read_transaction() as c:
            statements = []
            c.set_trace_callback(statements.append)
            try:
                assert claude_login.poll(c, app.vault, other["runner_id"], body, write=False) == EMPTY
            finally:
                c.set_trace_callback(None)
        assert sum("FROM plan_exits" in statement for statement in statements) == expected


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


def test_raw_and_saved_turn_windows_require_a_current_local_receipt(api, computers):
    exit_runner, other = computers
    sid = add(api)["id"]
    old_generation = select(api, exit_runner)
    agent_on(api, exit_runner, "ops", sid)
    attempt = turn(api, exit_runner, "ops")
    store = api.app.state.store
    key = SR.plan_key(sid)
    taken = H.now()

    def reading(pct, seconds):
        return {
            **window(pct),
            "origin": "subscription",
            "subscription_id": sid,
            "reported_at": H.shift(taken, seconds=seconds),
        }

    def usage():
        [plan] = get(api, "model-subscriptions")["subscriptions"]
        return plan["usage"]["used_pct"]

    heartbeat(api, exit_runner, "ops", profile="own", weekly=reading(42, 1))
    assert usage() == 42
    with store.transaction() as c:
        assert metadata(c, key)["turn_weekly_exit"] == [exit_runner["runner_id"], old_generation]
        save_metadata(c, key, {})
    assert usage() == 42  # the named profile's raw window has the same provenance
    select(api, other)
    current = select(api, exit_runner)
    heartbeat(api, exit_runner, "ops", profile="own", weekly=reading(93, 2))
    assert usage() is None
    with store.transaction() as c:
        assert metadata(c, key) == {}
        delivery = MS.handed(c, attempt, "claude")["delivery"]
        c.execute(
            "UPDATE attempt_subscriptions SET exit_generation=?,delivery='model_exit' WHERE attempt_id=?",
            (current, attempt),
        )
    heartbeat(api, exit_runner, "ops", profile="own", weekly=reading(94, 3))
    assert usage() is None
    with store.transaction() as c:
        assert metadata(c, key) == {}
        c.execute("UPDATE attempt_subscriptions SET delivery=? WHERE attempt_id=?", (delivery, attempt))
        save_metadata(
            c,
            key,
            {
                "turn_weekly": reading(40, 5),
                "turn_weekly_exit": [exit_runner["runner_id"], old_generation],
            },
        )
    heartbeat(api, exit_runner, "ops", profile="own", weekly=reading(45, 4))
    with store.read() as c:
        assert metadata(c, key)["turn_weekly"]["used_percent"] == 40
    heartbeat(api, exit_runner, "ops", profile="own", weekly=reading(46, 6))
    with store.read() as c:
        assert metadata(c, key)["turn_weekly_exit"] == [exit_runner["runner_id"], current]
        assert metadata(c, key)["turn_weekly"]["used_percent"] == 46


@pytest.mark.parametrize("waiting", ["cleared", "revoked", "not_ready"])
def test_waiting_routes_never_send_upkeep_to_legacy_computers(api, computers, monkeypatch, waiting):
    exit_runner, other = computers
    token_sid = add(api)["id"]
    full_sid = add(api, "claude_login", full(minutes=30), name="Full login")["id"]
    select(api, exit_runner)
    agent_on(api, other, "ops", token_sid)
    agent_on(api, other, "cpo", full_sid)
    path = "runner-subscription-refreshes"
    with monkeypatch.context() as legacy:
        legacy.setattr(plan_exits, "UPKEEP_ROUTING", False)
        request_usage(api, token_sid)
        [old] = get(api, path, other["token"])["refreshes"]
    store = api.app.state.store
    if waiting == "not_ready":
        heartbeat(api, exit_runner, installed=False)
    else:
        with store.transaction() as c:
            if waiting == "cleared":
                plan_exits.clear(c, "ana")
            else:
                c.execute("UPDATE runners SET revoked_at=? WHERE id=?", (H.now(), exit_runner["runner_id"]))
    with store.read() as c:
        for sid in (full_sid, token_sid):
            assert plan_exits.routed(c, MS.row(c, sid)).runner_id is None
    assert poll(api, other, full_sid) == EMPTY
    assert get(api, path, other["token"])["refreshes"] == []
    report = {"subscription": token_sid, "runtime": "claude", "state": "succeeded", "plan": window()}
    post(api, f"{path}/{old['id']}/report", report, other["token"], expected=404)
    for sid in (full_sid, token_sid):
        post(api, CONNECT, {"subscription": sid, "confirm": True}, expected=409)
        post(api, f"model-subscriptions/{sid}/usage-refresh", {}, expected=409)


def test_legacy_runner_can_pick_from_a_personal_pool_with_routing_on(api, computers):
    exit_runner, legacy = computers
    sid = add(api)["id"]
    other_sid = add(api, secret=OTHER, name="Another plan")["id"]
    select(api, exit_runner)
    assign(api, legacy, "ops")
    heartbeat(api, legacy, "ops", legacy=True)
    binding = legacy_give(api, {"runner_id": legacy["runner_id"], "runtime": "claude", "person": "human:ana"})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE model_subscriptions SET status='needs_reconnect' WHERE id=?", (binding["subscription_id"],))
        payer = MS.legacy_current(c, legacy["runner_id"], "claude", ["model_subscriptions_v1"])
        assert {payer["id"], binding["subscription_id"]} == {sid, other_sid}


def test_a_legacy_connect_cannot_poll_or_receive_a_code_after_routing_activates(api, computers, monkeypatch):
    exit_runner, other = computers
    sid = add(api)["id"]
    select(api, exit_runner)
    heartbeat(api, other)
    request = {"subscription": sid, "confirm": True}
    with monkeypatch.context() as legacy:
        legacy.setattr(plan_exits, "UPKEEP_ROUTING", False)
        login = post(api, CONNECT, request)
        assert login["runner_id"] == other["runner_id"]
        post(api, f"runner-logins/{login['id']}/report", {"state": "waiting"}, other["token"])
    code_path = f"computers/{other['runner_id']}/logins/{login['id']}/code"
    post(api, code_path, {"code": "abcdef#ghijkl"}, expected=409)
    assert get(api, "runner-logins", other["token"])["logins"] == []
    assert post(api, CONNECT, request)["runner_id"] == exit_runner["runner_id"]


def test_reconnect_is_pinned_and_a_mac_exit_only_routes_setup_tokens(api, computers):
    _, linux = computers
    mac = computer(api, "Mac exit", "darwin")
    sid = add(api)["id"]
    full_id = add(api, "claude_login", full(), name="Full login")["id"]
    select(api, mac)
    with api.app.state.store.read() as c:
        assert plan_exits.routed(c, MS.row(c, full_id)) is None
    denied = post(
        api,
        f"model-subscriptions/{sid}",
        {"kind": "claude_login", "secret": full(n=2, email="other@acme.example")},
        expected=409,
    )
    assert denied["error"]["code"] == "exit_platform"
    request_usage(api, sid)
    assert get(api, "runner-subscription-refreshes", mac["token"])["refreshes"][0]["subscription"] == sid

    request = {"subscription": sid, "confirm": True}
    login = post(api, CONNECT, request)
    assert login["runner_id"] == mac["runner_id"]
    assert post(api, CONNECT, request)["id"] == login["id"]
    path = f"runner-logins/{login['id']}/subscription"
    post(api, path, {"value": OTHER}, linux["token"], expected=404)
    post(api, f"runner-logins/{login['id']}/report", {"state": "waiting"}, mac["token"])
    code_path = f"computers/{mac['runner_id']}/logins/{login['id']}/code"
    post(api, code_path, {"code": "abcdef#ghijkl"})
    select(api, linux)
    assert get(api, "runner-logins", mac["token"])["logins"] == []
    assert post(api, code_path, {"code": "abcdef#ghijkl"}, expected=409)["error"]["code"] == "state"
    with api.app.state.store.read() as c:
        assert c.execute("SELECT pending_code FROM model_logins WHERE id=?", (login["id"],)).fetchone()[0] is None
    select(api, mac)
    post(api, path, {"value": OTHER}, mac["token"], expected=409)
    current = post(api, CONNECT, request)
    assert current["id"] != login["id"] and current["runner_id"] == mac["runner_id"]
    assert post(api, f"runner-logins/{current['id']}/subscription", {"value": OTHER}, mac["token"])["state"] == "done"
    with api.app.state.store.read() as c:
        assert MS.row(c, sid)["kind"] == "claude_token"
