"""Company subscriptions: saved once in Settings > Models, given to computers one per runtime, and signing in every turn
on that computer (backend/model_subscriptions.py)."""

import base64
import json

import pytest

from backend import login_holds
from backend import model_subscriptions as MS
from backend.auth import Identity
from backend.store import H, Problem, encode
from backend.tests.test_api import api, assign, claim, get, headers, post, put, runner  # noqa: F401
from backend.tests.test_credentials import setup

TOKEN = "sk-ant-oat01-" + "a" * 24
OTHER = "sk-ant-oat01-" + "b" * 24
CLAUDE_LIMIT = "You've hit your limit · resets 2:40am (UTC)"


def codex_file(account="acct-1", refresh="r-1", email="ana@acme.example"):
    claims = base64.urlsafe_b64encode(json.dumps({"email": email}).encode()).decode().rstrip("=")
    return json.dumps({"tokens": {"id_token": f"h.{claims}.s", "access_token": "a-" + refresh,
                                  "refresh_token": refresh, "account_id": account}})


def company(api, enabled=("anthropic", "openai")):
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM registry_metadata WHERE key='providers'")
        c.execute("INSERT INTO registry_metadata VALUES('providers',?)",
                  (encode({"revision": 1, "enabled": list(enabled), "runtime": "claude",
                           "model": "claude-sonnet-5-5"}),))


def on(api, bot, runtime, model):
    with api.app.state.store.transaction() as c:
        config = json.loads(c.execute("SELECT config_json FROM bot_config WHERE bot=?", (bot,)).fetchone()[0])
        config.update(runtime=runtime, harness=runtime, model=model)
        c.execute("UPDATE bot_config SET config_json=? WHERE bot=?", (json.dumps(config), bot))


def add(api, kind="claude_token", secret=TOKEN, name="Company Max", token="ana-test", expected=200):
    return post(api, "model-subscriptions", {"kind": kind, "secret": secret, **({"name": name} if name else {})},
                token, expected=expected)


def give(api, m, runtime, sid, confirm=False, token="ana-test", expected=200):
    return put(api, "computer-subscriptions", {"runner_id": m["runner_id"], "runtime": runtime, "subscription": sid,
                                               "confirm": confirm}, token, expected=expected)


def machine(api, signed_in=False, takes=True, bot="ops", claude=None, label="Test Mac"):
    m = runner(api, label=label)
    assign(api, m, bot)
    post(api, "runners/heartbeat", {
        "version": "test", "platform": "test", "capabilities": [MS.CAPABILITY] if takes else [],
        "readiness": {"schema_version": 1,
                      "runtimes": {"claude": {"installed": True,
                                              "authenticated": claude or ("ready" if signed_in else "missing")},
                                   "codex": {"installed": True, "authenticated": "missing"}},
                      "bots": {bot: {"runtime": "claude", "ready": True}}}}, token=m["token"])
    return m


def effect(api, m, runtime="claude"):
    with api.app.state.store.read() as c:
        found = MS.in_effect(c, m["runner_id"], runtime)
    return found and found["id"]


def listed(api, token="ana-test"):
    return get(api, "model-subscriptions", token)["subscriptions"]


def test_owners_and_admins_save_the_company_subscriptions_and_nobody_reads_a_value(api):
    setup(api)
    company(api, enabled=["openai"])
    saved = add(api)
    assert saved["kind"] == "claude_token" and saved["status"] == "new" and saved["runtime"] == "claude"
    assert saved["added_by"] == "human:ana" and saved["computers"] == [] and saved["usage"] is None
    assert TOKEN not in json.dumps(saved) and saved["preview"].endswith(TOKEN[-4:])
    # Saving it turns its provider on, so its models are offered.
    with api.app.state.store.read() as c:
        from backend import providers
        assert "anthropic" in providers.load(c, api.app.state.store.settings)["enabled"]
    assert add(api, "codex_login", codex_file(), name="ChatGPT", token="ben-test")["preview"] == "ana@acme.example"
    body = get(api, "model-subscriptions", "cara-test")                       # a member reads, no values
    assert [s["name"] for s in body["subscriptions"]] == ["ChatGPT", "Company Max"]
    assert [k["kind"] for k in body["kinds"]] == ["claude_token", "codex_login", "anthropic_key", "openai_key"]
    assert TOKEN not in json.dumps(body) and "r-1" not in json.dumps(body)
    with api.app.state.store.read() as c:
        assert TOKEN not in c.execute("SELECT sealed FROM model_subscriptions WHERE id=?", (saved["id"],)).fetchone()[0]
        assert not c.execute("SELECT 1 FROM idempotency WHERE response_json LIKE ? OR request_hash LIKE ?",
                             (f"%{TOKEN}%", f"%{TOKEN}%")).fetchone()
        assert not c.execute("SELECT 1 FROM events WHERE detail_json LIKE ?", (f"%{TOKEN}%",)).fetchone()
    add(api, token="cara-test", expected=403)                                 # a member saves nothing
    add(api, name="  ", expected=422)
    add(api, "claude_token", "not-a-token", expected=422)
    add(api, "codex_login", "{}", expected=422)
    api.app.state.store.settings.test_identities["assistant"] = Identity("human:ana", "owner",
                                                                         email="ana@acme.example", via="assistant")
    add(api, token="assistant", expected=403)                                 # a secret is pasted by the person
    support = Identity("human:support", "owner", email="ana@acme.example", support="full")
    with pytest.raises(Problem):                                              # a support session changes nothing
        MS._admin(api.app.state.auth, support)
    # Rename, and reconnect: a new value counts again from `new`.
    post(api, f"model-subscriptions/{saved['id']}", {"name": "Max"}, "cara-test", expected=403)
    assert post(api, f"model-subscriptions/{saved['id']}", {"name": "Max"})["name"] == "Max"
    with api.app.state.store.transaction() as c:
        MS._status(c, saved["id"], "needs_reconnect", "expired")
    again = post(api, f"model-subscriptions/{saved['id']}", {"secret": OTHER})
    assert again["status"] == "new" and again["revision"] == 2 and again["preview"].endswith(OTHER[-4:])


def test_a_computer_holds_one_subscription_per_runtime_of_the_right_kind(api):
    setup(api)
    company(api)
    m = machine(api)
    old = machine(api, takes=False, bot="cpo", label="Old Mac")
    first, second = add(api, name="One")["id"], add(api, name="Two")["id"]
    chatgpt = add(api, "codex_login", codex_file(), name="ChatGPT")["id"]
    give(api, m, "codex", first, expected=422)                                # a Claude login for Codex
    given = give(api, m, "claude", first)
    assert (given["subscription_id"], given["stopped"]) == (first, 0)
    give(api, m, "claude", second)                                            # a switch replaces it
    give(api, m, "codex", chatgpt)
    give(api, old, "claude", second)
    give(api, m, "claude", first, token="cara-test", expected=403)
    state = get(api, "computer-subscriptions", "cara-test")
    assert sorted((a["runner_id"], a["runtime"], a["subscription_id"]) for a in state["assignments"]) == sorted(
        [(m["runner_id"], "claude", second), (m["runner_id"], "codex", chatgpt), (old["runner_id"], "claude", second)])
    assert state["capable"] == [m["runner_id"]]                               # the old one needs an update
    computers = {s["id"]: s["computers"] for s in listed(api)}
    assert computers[first] == [] and sorted(computers[second]) == sorted([m["runner_id"], old["runner_id"]])
    assert effect(api, m) == second and effect(api, old) is None
    give(api, m, "claude", None)
    assert effect(api, m) is None and len(get(api, "computer-subscriptions")["assignments"]) == 2


def test_a_turn_signs_in_with_its_computers_subscription_and_a_refused_one_is_passed_over(api):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    sid = add(api)["id"]
    m = machine(api, claude="rejected")               # its own Claude login refused: the subscription still runs
    give(api, m, "claude", sid)
    [entry] = [e for e in get(api, "runners/assignments", m["token"]) if e["bot"] == "ops"]
    assert entry["subscriptions"] == {"claude": "claude_token"}       # its readiness needs no Claude sign-in here
    post(api, "chat/ops", {"text": "Summarize the Acme account."})
    turn = claim(api, m)
    assert turn and turn["config"]["runtime"] == "claude" and turn["credential_vault"] is True
    [handed] = turn["subscriptions"]
    assert handed["id"] == sid and handed["env"] == "CLAUDE_CODE_OAUTH_TOKEN" and handed["source"] == "computer"
    assert "value" not in handed and TOKEN not in json.dumps(turn)
    got = get(api, "credential-runtime", turn["token"])["subscriptions"]
    assert [(s["env"], s["value"], s["billing"]) for s in got] == [("CLAUDE_CODE_OAUTH_TOKEN", TOKEN, "subscription")]
    get(api, "credential-runtime", "ana-test", expected=403)
    post(api, f"attempts/{turn['id']}/started", {"thread_id": "t-sub"}, m["token"])
    post(api, f"attempts/{turn['id']}/complete", {
        "outcome": "failed", "last_seq": 0,
        "auth_rejected": {"runtime": "claude", "reason": "OAuth token has expired"}}, m["token"])
    [mine] = listed(api)
    assert mine["status"] == "needs_reconnect" and mine["status_detail"] == "OAuth token has expired"
    with api.app.state.store.read() as c:
        # Nothing waits on a computer sign-in card: the refusal was the subscription's.
        assert not c.execute("SELECT 1 FROM task_reconnects WHERE key LIKE 'login:%'").fetchone()
    # Passed over: the computer's own login (refused here) decides again, so nothing is handed.
    assert effect(api, m) is None
    assert "subscriptions" not in [e for e in get(api, "runners/assignments", m["token"]) if e["bot"] == "ops"][0]
    assert claim(api, m) is None                         # no sign-in here and no Gateway in this preset: it waits
    # Reconnecting with a new value makes it count again, and a completed turn on that revision marks it working.
    post(api, f"model-subscriptions/{sid}", {"secret": OTHER})
    assert effect(api, m) == sid
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE attempt_subscriptions SET revision=2 WHERE attempt_id=?", (turn["id"],))
        MS.turn_worked(c, turn["id"], "claude")
    assert listed(api)[0]["status"] == "working"


def test_an_older_computer_runs_on_its_own_login_as_before(api):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    m = machine(api, signed_in=True, takes=False)
    give(api, m, "claude", add(api)["id"])
    post(api, "chat/ops", {"text": "Summarize the Acme account."})
    turn = claim(api, m)
    assert turn and turn["config"]["runtime"] == "claude" and "subscriptions" not in turn
    assert "subscriptions" not in get(api, "credential-runtime", turn["token"])


def test_handing_a_turn_its_subscription_is_not_a_write(api):
    """The hub sending the run its sign-in is what the computer's own login gets too: the run has written nothing."""
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    sid = add(api)["id"]
    m = machine(api, signed_in=True)
    give(api, m, "claude", sid)
    post(api, "chat/ops", {"text": "Summarize the Acme account."})
    turn = claim(api, m)
    post(api, f"attempts/{turn['id']}/started", {"thread_id": "t-sub"}, m["token"])
    assert get(api, "credential-runtime", turn["token"])["subscriptions"][0]["id"] == sid
    with api.app.state.store.read() as c:
        assert c.execute("SELECT coalesce(writes,0) FROM turns WHERE id=?", (turn["id"],)).fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM events WHERE action='subscription.sent_to_computer' AND target=?",
                         (sid,)).fetchone()[0] == 1


def test_a_subscription_turn_that_only_reports_a_busy_model_is_retried_not_held(api):
    """Its sign-in was handed, but the run did nothing but say the model was busy: that is no work to hold for review."""
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    sid = add(api)["id"]
    m = machine(api, signed_in=True)
    give(api, m, "claude", sid)
    post(api, "chat/ops", {"text": "Summarize the Acme account."})
    turn = claim(api, m)
    post(api, f"attempts/{turn['id']}/started", {"thread_id": "t-busy"}, m["token"])
    get(api, "credential-runtime", turn["token"])
    notice = "Selected model is at capacity. Please try a different model."
    post(api, f"attempts/{turn['id']}/events", {"events": [
        {"seq": 1, "kind": "status", "payload": {"state": "active", "tool_events": True}},
        {"seq": 2, "kind": "message", "payload": {"text": notice, "final": False}},
        {"seq": 3, "kind": "error", "payload": {"error": notice, "limit": True}}]}, m["token"])
    post(api, f"attempts/{turn['id']}/complete", {"outcome": "failed", "last_seq": 3, "text": notice}, m["token"])
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM jobs WHERE id=?", (turn["job_id"],)).fetchone()[0] == "queued"


def test_a_limit_met_on_a_subscription_holds_every_computer_given_it(api):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    on(api, "cpo", "claude", "claude-sonnet-5-5")
    sid = add(api)["id"]
    m, n = machine(api, signed_in=True), machine(api, signed_in=True, bot="cpo", label="Other Mac")
    give(api, m, "claude", sid)
    give(api, n, "claude", sid)
    post(api, "chat/ops", {"text": "Summarize the Acme account."})
    turn = claim(api, m)
    post(api, f"attempts/{turn['id']}/started", {"thread_id": "limit"}, m["token"])
    # No reset named: a turn paused at a named reset keeps its saved session for that reset instead (source pause).
    post(api, f"attempts/{turn['id']}/events", {"events": [
        {"seq": 1, "kind": "error", "payload": {"error": "You've hit your usage limit.", "limit": True}}]}, m["token"])
    post(api, f"attempts/{turn['id']}/complete", {"outcome": "failed", "last_seq": 1, "limited": True, "text": ""},
         m["token"])
    sub_login = login_holds.subscription_login(sid)
    with api.app.state.store.read() as c:
        hold = login_holds.get(c, "", sub_login, "claude")
        assert hold and hold["until"] > H.now()
        # Not the computer's own login: that one is not held.
        assert not login_holds.get(c, m["runner_id"], "", "claude")
        # Every computer given it passes it over until the reset: the other one signs that runtime in with its own login.
        assert MS.in_effect(c, n["runner_id"], "claude") is None
        assert login_holds.canonical(c, n["runner_id"], "", "claude") == ""
    assert listed(api)[0]["usage"]["used_pct"] == 100
    post(api, "chat/cpo", {"text": "Price the Acme plan."})
    other = claim(api, n)                                                     # it never waits on the held subscription
    assert other and other["bot"] == "cpo" and other["config"]["runtime"] == "claude" and "subscriptions" not in other
    assert not other["config"].get("primary_held")
    # The bot that met the limit is not kept waiting by it either: its work back in the queue signs in with the
    # computer's own login, which no limit holds.
    from backend.execution import cooling_off
    with api.app.state.store.read() as c:
        assert H.status(c, "ops")["state"] == "limited" and cooling_off(c, "ops")
    mine = claim(api, m)
    assert mine and mine["job_id"] == turn["job_id"] and "subscriptions" not in mine
    assert not mine["config"].get("primary_held")


def test_a_held_subscription_falls_to_the_gateway_under_the_gateway_choice_and_waits_without_it(api, monkeypatch):
    from backend import models_preset
    from backend.tests.test_gateway_connection import KEY, fake_gateway, save
    from backend.tests.test_gateway_connection import setup as gateway_setup
    from backend.tests.test_models_preset import choose
    gateway_setup(api, monkeypatch)
    api.app.state.store.settings.credential_admins = ("ana@acme.example", "ben@acme.example")
    with fake_gateway() as fake:
        fake.active(KEY, 0.1)
        save(api, fake)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    sid = add(api)["id"]
    m = machine(api)                                  # not signed in to Claude itself: the subscription signs it in
    post(api, "runners/heartbeat", {"version": "test", "platform": "test", "capabilities": [MS.CAPABILITY],
                                    "readiness": {"schema_version": 1, "isolated": True,
                                                  "runtimes": {"claude": {"installed": True, "authenticated": "missing"},
                                                               "pi": {"installed": True, "authenticated": "missing"}},
                                                  # the runner's own answer once runners/assignments stops
                                                  # naming the held subscription
                                                  "bots": {"ops": {"runtime": "claude", "ready": False,
                                                                   "model": "claude-sonnet-5-5",
                                                                   "repository_present": True,
                                                                   "configuration_valid": True,
                                                                   "problems": ["claude is not signed in"]}}}},
         token=m["token"])
    give(api, m, "claude", sid)
    with api.app.state.store.transaction() as c:      # a limit met on it until the reset, on another computer
        login_holds._save(c, {"runner_id": "", "profile": login_holds.subscription_login(sid), "runtime": "claude",
                              "until": "2999-01-01T00:00:00.000000Z", "since": H.now(), "error": CLAUDE_LIMIT})
    with api.app.state.store.read() as c:
        assert login_holds.holding(c, "", login_holds.subscription_login(sid), "claude") == "held"
    choose(api, "subscriptions")
    post(api, "chat/ops", {"text": "Summarize the Acme account."})
    assert claim(api, m) is None                              # no subscription, no sign-in here, no Gateway: it waits
    choose(api, models_preset.RECOMMENDED)
    turn = claim(api, m)
    assert turn and turn["config"]["runtime"] == "pi" and turn["config"]["waiting_for_sign_in"] == "claude"
    assert "subscriptions" not in turn and not turn["config"].get("primary_held")


def test_a_change_stops_the_computers_runs_on_that_runtime_after_a_confirm_and_starts_sessions_fresh(api):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    first, second = add(api, name="One")["id"], add(api, name="Two")["id"]
    m = machine(api, claude="rejected")
    give(api, m, "claude", first)
    message = post(api, "chat/ops", {"text": "Summarize the Acme account."})
    turn = claim(api, m)
    post(api, f"attempts/{turn['id']}/started", {"thread_id": "t-one"}, m["token"])

    def epoch():
        with api.app.state.store.read() as c:
            found = c.execute("SELECT epoch FROM session_epochs WHERE conversation_id=?",
                              (message["conversation_id"],)).fetchone()
        return found[0] if found else 0
    before = epoch()
    refused = api.put("/api/v2/computer-subscriptions", headers=headers(),
                      json={"runner_id": m["runner_id"], "runtime": "claude", "subscription": second})
    assert refused.status_code == 409
    error = refused.json()["error"]
    assert error["code"] == "confirm_restart" and error["running"] == 1 and error["sessions"] >= 1
    assert effect(api, m) == first and epoch() == before                      # nothing changed without the confirm
    assert give(api, m, "codex", None)["stopped"] == 0                        # another runtime: nothing to stop
    done = give(api, m, "claude", second, confirm=True)
    assert done["subscription_id"] == second and done["stopped"] == 1 and done["sessions_reset"] >= 1
    assert epoch() > before                                                   # every session there starts fresh
    renewed = post(api, f"attempts/{turn['id']}/renew", {}, m["token"])
    assert renewed["stop"] is True and "sign-in changed" in renewed["stop_reason"]
    post(api, f"attempts/{turn['id']}/complete", {"outcome": "interrupted", "last_seq": 0}, m["token"])
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM jobs WHERE id=?", (turn["job_id"],)).fetchone()[0] == "queued"
    again = claim(api, m)                                                     # back in the queue, on the new login
    assert again and again["job_id"] == turn["job_id"] and again["subscriptions"][0]["id"] == second


def test_removing_a_subscription_in_use_confirms_then_takes_it_off_every_computer(api):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    sid = add(api)["id"]
    m = machine(api, claude="rejected")
    idle = machine(api, bot="cpo", label="Idle Mac")
    give(api, m, "claude", sid)
    give(api, idle, "claude", sid)
    post(api, "chat/ops", {"text": "Summarize the Acme account."})
    turn = claim(api, m)
    post(api, f"attempts/{turn['id']}/started", {"thread_id": "t-one"}, m["token"])
    gone = api.delete(f"/api/v2/model-subscriptions/{sid}", headers=headers())
    assert gone.status_code == 409
    error = gone.json()["error"]
    assert error["code"] == "confirm_restart" and error["running"] == 1 and error["computers"] == 2
    assert api.delete(f"/api/v2/model-subscriptions/{sid}?confirm=1",
                      headers=headers("cara-test")).status_code == 403
    r = api.delete(f"/api/v2/model-subscriptions/{sid}?confirm=1", headers=headers())
    assert r.status_code == 200 and r.json()["stopped"] == 1 and r.json()["computers"] == 2
    assert listed(api) == [] and get(api, "computer-subscriptions")["assignments"] == []
    assert post(api, f"attempts/{turn['id']}/renew", {}, m["token"])["stop"] is True


def test_an_older_computer_on_its_own_login_is_left_alone_by_give_remove_and_a_refusal(api):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    on(api, "cpo", "claude", "claude-sonnet-5-5")
    old = machine(api, signed_in=True, takes=False, label="Old Mac")    # runs Claude on its own login
    m = machine(api, bot="cpo", claude="rejected")
    message = post(api, "chat/ops", {"text": "Summarize the Acme account."})
    turn = claim(api, old)
    assert turn and "subscriptions" not in turn
    post(api, f"attempts/{turn['id']}/started", {"thread_id": "t-own"}, old["token"])

    def untouched():
        with api.app.state.store.read() as c:
            epoch = c.execute("SELECT epoch FROM session_epochs WHERE conversation_id=?",
                              (message["conversation_id"],)).fetchone()
            assert c.execute("SELECT state,stop_requested FROM attempts WHERE id=?", (turn["id"],)).fetchone()[:] == (
                "running", None)
        assert not epoch
        assert not post(api, f"attempts/{turn['id']}/renew", {}, old["token"]).get("stop")
    sid = add(api)["id"]
    # Giving it: recorded (it signs in with it once updated), no confirm, nothing stopped, no session reset.
    done = give(api, old, "claude", sid)
    assert (done["subscription_id"], done["stopped"], done["sessions_reset"]) == (sid, 0, 0)
    untouched()
    # A refusal on a capable computer given the same subscription resets only that computer's sessions.
    give(api, m, "claude", sid)
    post(api, "chat/cpo", {"text": "Price the Acme plan."})
    other = claim(api, m)
    assert other["subscriptions"][0]["id"] == sid
    post(api, f"attempts/{other['id']}/started", {"thread_id": "t-sub"}, m["token"])
    post(api, f"attempts/{other['id']}/complete", {
        "outcome": "failed", "last_seq": 0,
        "auth_rejected": {"runtime": "claude", "reason": "OAuth token has expired"}}, m["token"])
    assert listed(api)[0]["status"] == "needs_reconnect"
    untouched()
    # Removing it: no confirm (nothing on the older computer stops), both computers counted as taken off.
    r = api.delete(f"/api/v2/model-subscriptions/{sid}", headers=headers())
    assert r.status_code == 200 and r.json()["stopped"] == 0 and r.json()["computers"] == 2
    assert get(api, "computer-subscriptions")["assignments"] == []
    untouched()
    # Taking one off an older computer is the same: recorded only.
    again = add(api, name="Again")["id"]
    give(api, old, "claude", again)
    assert give(api, old, "claude", None)["stopped"] == 0
    untouched()


def test_a_refreshed_chatgpt_login_comes_back_only_from_the_run_that_had_it(api):
    setup(api)
    company(api)
    on(api, "ops", "codex", "gpt-6.1-sol")
    sid = add(api, "codex_login", codex_file(), name="ChatGPT")["id"]
    m = machine(api)
    give(api, m, "codex", sid)
    post(api, "chat/ops", {"text": "Summarize the Acme account."})
    turn = claim(api, m)
    [handed] = turn["subscriptions"]
    assert handed["file"] == "auth.json" and handed["revision"] == 1
    [value] = get(api, "credential-runtime", turn["token"])["subscriptions"]
    assert json.loads(value["value"])["tokens"]["refresh_token"] == "r-1"
    body = {"attempt": turn["id"], "subscription": sid, "revision": 1}
    other = runner(api, label="Other Mac")
    post(api, "runner-model-subscription-refresh", {**body, "value": codex_file(refresh="r-2")}, other["token"],
         expected=403)
    post(api, "runner-model-subscription-refresh", {**body, "value": codex_file(refresh="r-2")}, "ana-test",
         expected=403)
    assert post(api, "runner-model-subscription-refresh", {**body, "value": codex_file("acct-2", "r-2")},
                m["token"])["stored"] is False                  # another account's file is never kept
    assert post(api, "runner-model-subscription-refresh", {**body, "value": codex_file(refresh="r-2")},
                m["token"]) == {"stored": True, "revision": 2}
    assert post(api, "runner-model-subscription-refresh", {**body, "value": codex_file(refresh="r-3")},
                m["token"])["stored"] is False                  # stale: the stored one moved on
    with api.app.state.store.read() as c:
        sub = MS.row(c, sid)
        assert json.loads(api.app.state.vault.cipher.unseal(c, MS._context(sid), sub["sealed"]))["tokens"][
            "refresh_token"] == "r-2"
        assert MS.handed(c, turn["id"], "codex")["revision"] == 2       # the run's record moved with its file
    post(api, f"attempts/{turn['id']}/started", {"thread_id": "t-codex"}, m["token"])
    post(api, f"attempts/{turn['id']}/complete", {"outcome": "completed", "last_seq": 0, "text": "Done."}, m["token"])
    assert listed(api)[0]["status"] == "working"                         # it worked on the revision it refreshed to
    # A finished run writes nothing back, though its revision is still the current one.
    late = {"attempt": turn["id"], "subscription": sid, "revision": 2, "value": codex_file(refresh="r-9")}
    post(api, "runner-model-subscription-refresh", late, m["token"], expected=409)
    # Nor does a live run on a computer the subscription was taken off.
    post(api, "chat/ops", {"text": "And the Globex account?"})
    second = claim(api, m)
    assert second["subscriptions"][0]["revision"] == 2
    post(api, f"attempts/{second['id']}/started", {"thread_id": "t-codex-2"}, m["token"])
    assert give(api, m, "codex", None, confirm=True)["stopped"] == 1
    post(api, "runner-model-subscription-refresh", {**late, "attempt": second["id"]}, m["token"], expected=409)
    assert "subscriptions" not in get(api, "credential-runtime", second["token"])
    with api.app.state.store.read() as c:
        assert json.loads(api.app.state.vault.cipher.unseal(c, MS._context(sid), MS.row(c, sid)["sealed"]))[
            "tokens"]["refresh_token"] == "r-2"


def test_a_reconnect_confirms_then_restarts_runs_on_the_old_value_and_their_late_refusal_leaves_the_new_one(api):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    sid = add(api)["id"]
    m = machine(api)                                  # no Claude login of its own: the subscription is its only one
    idle = machine(api, bot="cpo", label="Idle Mac")
    give(api, m, "claude", sid)
    give(api, idle, "claude", sid)
    post(api, "chat/ops", {"text": "Summarize the Acme account."})
    turn = claim(api, m)
    post(api, f"attempts/{turn['id']}/started", {"thread_id": "t-old"}, m["token"])
    # A run would stop: the reconnect asks first, and nothing is saved without the confirm (not the new name either).
    error = post(api, f"model-subscriptions/{sid}", {"secret": OTHER, "name": "Renamed"}, expected=409)["error"]
    assert error["code"] == "confirm_restart" and error["running"] == 1 and error["computers"] == 2
    assert error["sessions"] >= 1
    [kept] = listed(api)
    assert kept["revision"] == 1 and kept["name"] == "Company Max" and kept["preview"].endswith(TOKEN[-4:])
    assert not post(api, f"attempts/{turn['id']}/renew", {}, m["token"]).get("stop")
    assert post(api, f"model-subscriptions/{sid}", {"name": "Max"})["name"] == "Max"      # a rename never asks
    done = post(api, f"model-subscriptions/{sid}", {"secret": OTHER, "confirm": True})
    assert done["revision"] == 2 and done["preview"].endswith(OTHER[-4:])
    with api.app.state.store.read() as c:
        assert json.loads(c.execute("SELECT detail_json FROM events WHERE action='subscription.updated' "
                                    "ORDER BY rowid DESC LIMIT 1").fetchone()[0])["stopped"] == 1
    # The run handed revision 1 is stopped and put back in the queue, and never reads the new value.
    renewed = post(api, f"attempts/{turn['id']}/renew", {}, m["token"])
    assert renewed["stop"] is True and "sign-in changed" in renewed["stop_reason"]
    assert "subscriptions" not in get(api, "credential-runtime", turn["token"])
    # Its refusal of the old value arrives late: the new value stays as it is, and no computer card waits on it.
    post(api, f"attempts/{turn['id']}/complete", {
        "outcome": "failed", "last_seq": 0,
        "auth_rejected": {"runtime": "claude", "reason": "OAuth token has expired"}}, m["token"])
    assert listed(api)[0]["status"] == "new" and effect(api, m) == sid
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM task_reconnects WHERE key LIKE 'login:%'").fetchone()


def test_a_run_reads_only_the_revision_it_was_handed_or_the_same_accounts_refresh(api):
    setup(api)
    company(api)
    on(api, "ops", "codex", "gpt-6.1-sol")
    on(api, "cpo", "codex", "gpt-6.1-sol")
    sid = add(api, "codex_login", codex_file(), name="ChatGPT")["id"]
    m, n = machine(api), machine(api, bot="cpo", label="Other Mac")
    give(api, m, "codex", sid)
    give(api, n, "codex", sid)
    post(api, "chat/ops", {"text": "Summarize the Acme account."})
    post(api, "chat/cpo", {"text": "Price the Acme plan."})
    first, second = claim(api, m), claim(api, n)
    assert first["subscriptions"][0]["revision"] == second["subscriptions"][0]["revision"] == 1
    get(api, "credential-runtime", first["token"])
    assert post(api, "runner-model-subscription-refresh", {"attempt": first["id"], "subscription": sid, "revision": 1,
                                                           "value": codex_file(refresh="r-2")}, m["token"])["stored"]
    # The other run reads after the refresh: the same account's newer file, and its record moves to it, so its own
    # write-back is not stale.
    [value] = get(api, "credential-runtime", second["token"])["subscriptions"]
    assert value["revision"] == 2 and json.loads(value["value"])["tokens"]["refresh_token"] == "r-2"
    assert post(api, "runner-model-subscription-refresh", {"attempt": second["id"], "subscription": sid, "revision": 2,
                                                           "value": codex_file(refresh="r-3")}, n["token"]) == {
        "stored": True, "revision": 3}
    # The first run's copy is now behind (its refresh token was used up by the second): its refusal says nothing of
    # the stored login, which stays in use on both computers.
    post(api, f"attempts/{first['id']}/started", {"thread_id": "t-first"}, m["token"])
    post(api, f"attempts/{first['id']}/complete", {
        "outcome": "failed", "last_seq": 0,
        "auth_rejected": {"runtime": "codex", "reason": "refresh_token_reused"}}, m["token"])
    assert listed(api)[0]["status"] != "needs_reconnect" and effect(api, n, "codex") == sid


def test_a_computer_with_only_a_codex_subscription_runs_codex_checks_and_their_limit_holds_it(api):
    from backend.readiness import can_run
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    sid = add(api, "codex_login", codex_file(), name="ChatGPT")["id"]
    m = machine(api, signed_in=True)                  # its own Claude login; no Codex login of its own
    post(api, "runners/heartbeat", {
        "version": "test", "platform": "test", "capabilities": [MS.CAPABILITY, "task_lanes_v2", "session_roots_v1"],
        "readiness": {"schema_version": 1, "runtimes": {"claude": {"installed": True, "authenticated": "ready"},
                                                        "codex": {"installed": True, "authenticated": "missing"}},
                      "bots": {"ops": {"runtime": "claude", "ready": True}}}}, token=m["token"])
    old = machine(api, takes=False, bot="cpo", label="Old Mac")
    with api.app.state.store.read() as c:
        assert can_run(c, "ops", "codex")["can_run"] is False
    give(api, m, "codex", sid)
    give(api, old, "codex", sid)
    with api.app.state.store.read() as c:
        # The subscription is its Codex sign-in for every check (a move, a task model, a check model, Health) ...
        assert can_run(c, "ops", "codex")["can_run"] is True
        # ... but not on a runner too old to take one.
        assert can_run(c, "cpo", "codex")["can_run"] is False
    parent = post(api, "tasks", {"owner": "ops", "title": "Ship the import fix", "body": "Open the PR."})
    turn = claim(api, m)
    assert turn["task"]["id"] == parent["id"]
    sub = post(api, "tasks", {"owner": "ops", "title": "Review it on Codex", "body": "Read it.", "check_model": "codex",
                              "check_role": "review", "relations": [{"task": parent["id"], "kind": "parent"}]},
               turn["token"])
    post(api, f"attempts/{turn['id']}/started", {"thread_id": "t-parent"}, m["token"])
    post(api, f"attempts/{turn['id']}/complete", {"outcome": "completed", "last_seq": 0, "text": "Done."}, m["token"])
    check = claim(api, m)                             # handed out though Codex is not signed in on the computer
    assert check and check["task"]["id"] == sub["id"] and check["config"]["runtime"] == "codex"
    assert [s["id"] for s in check["subscriptions"]] == [sid]
    post(api, f"attempts/{check['id']}/started", {"thread_id": "t-check"}, m["token"])
    post(api, f"attempts/{check['id']}/events", {"events": [
        {"seq": 1, "kind": "error", "payload": {"error": "You've hit your usage limit.", "limit": True}}]}, m["token"])
    post(api, f"attempts/{check['id']}/complete", {"outcome": "failed", "last_seq": 1, "limited": True, "text": ""},
         m["token"])
    with api.app.state.store.read() as c:
        # The check's limit holds the shared subscription on every computer given it ...
        assert login_holds.get(c, "", login_holds.subscription_login(sid), "codex")["until"] > H.now()
        assert not login_holds.get(c, m["runner_id"], "", "codex")
        # ... and the check keeps its own schedule: it alone is held, the bot is not limited.
        assert H.status(c, "ops")["state"] != "limited" and "on a check" in H.status(c, "ops")["focus"]


def limited_on(api, m, sid, bot="ops"):
    """A turn of `bot` on computer `m`, signed in with subscription `sid`, meets a usage limit that names its reset: the
    bot is limited and its queued work is held until the reset (`quota-reset`)."""
    give(api, m, "claude", sid)
    post(api, f"chat/{bot}", {"text": "Summarize the Acme account."})
    turn = claim(api, m)
    post(api, f"attempts/{turn['id']}/started", {"thread_id": "limit"}, m["token"])
    post(api, f"attempts/{turn['id']}/events", {"events": [
        {"seq": 1, "kind": "error", "payload": {"error": CLAUDE_LIMIT, "limit": True}}]}, m["token"])
    post(api, f"attempts/{turn['id']}/complete", {"outcome": "failed", "last_seq": 1, "limited": True, "text": ""},
         m["token"])
    return turn


def status(api, bot="ops"):
    with api.app.state.store.read() as c:
        return H.status(c, bot) or {}


def queued_holds(api, bot="ops"):
    with api.app.state.store.read() as c:
        return [tuple(r) for r in c.execute("SELECT held,wake_class FROM jobs WHERE bot=? AND state='queued' "
                                            "ORDER BY id", (bot,))]


def signed_with(api, turn):
    """The subscription a turn was handed (its attempt's record), or None for the computer's own login."""
    with api.app.state.store.read() as c:
        found = c.execute("SELECT subscription_id FROM attempt_subscriptions WHERE attempt_id=?",
                          (turn["id"],)).fetchone()
    return found[0] if found else None


def hold_plan(api, sid, until="2999-01-01T00:00:00.000000Z"):
    """A plan's own usage-limit hold, as a limit met on it elsewhere leaves it."""
    with api.app.state.store.transaction() as c:
        login_holds._save(c, {"runner_id": "", "profile": login_holds.subscription_login(sid), "runtime": "claude",
                              "until": until, "since": H.now(), "error": CLAUDE_LIMIT})


def hold_own_login(api, m, until="2999-01-01T00:00:00.000000Z", runtime="claude"):
    with api.app.state.store.transaction() as c:
        login_holds._save(c, {"runner_id": m["runner_id"], "profile": "", "runtime": runtime, "until": until,
                              "since": H.now(), "error": CLAUDE_LIMIT})


def hold_queued(api, bot="ops", until="2999-01-01T00:00:00.000000Z"):
    """The bot is limited and its queued work waits until `until`, as a limit with a named reset leaves it."""
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET held=1,hold_until=?,wake_class='quota-reset' WHERE bot=? AND state='queued'",
                  (until, bot))
        H.status_set(c, H.KEEPER, bot, state="limited", focus="Usage limit", reason="test")


def queued_until(api, bot="ops"):
    with api.app.state.store.read() as c:
        return [tuple(r) for r in c.execute("SELECT held,hold_until FROM jobs WHERE bot=? AND state='queued'", (bot,))]


def test_switching_a_computer_to_another_plan_frees_a_bot_limited_on_the_old_one(api):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    plan_a, plan_b = add(api, name="Plan A")["id"], add(api, secret=OTHER, name="Plan B")["id"]
    m = machine(api)                                     # no login of its own: the held work waits for the reset
    limited_on(api, m, plan_a)
    assert status(api)["state"] == "limited" and queued_holds(api) == [(1, "quota-reset")]
    give(api, m, "claude", plan_b)
    assert status(api)["state"] != "limited" and queued_holds(api) == [(0, "quota-reset")]
    turn = claim(api, m)
    assert turn and turn["bot"] == "ops" and signed_with(api, turn) == plan_b


def test_a_new_plan_held_at_its_limit_is_passed_over_so_the_bot_runs_on_the_computers_own_login(api):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    plan_a, plan_b = add(api, name="Plan A")["id"], add(api, secret=OTHER, name="Plan B")["id"]
    m = machine(api, signed_in=True)
    limited_on(api, m, plan_a)
    hold_plan(api, plan_b)
    give(api, m, "claude", plan_b)
    # The held plan is passed over (model_subscriptions.in_effect), so the turn runs on the computer's own login.
    assert status(api)["state"] != "limited" and queued_holds(api) == [(0, "quota-reset")]
    turn = claim(api, m)
    assert turn and turn["bot"] == "ops" and signed_with(api, turn) is None


def test_a_new_plan_held_at_its_limit_keeps_the_bot_limited_when_the_computers_own_login_is_held_too(api):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    plan_a, plan_b = add(api, name="Plan A")["id"], add(api, secret=OTHER, name="Plan B")["id"]
    m = machine(api, signed_in=True)
    limited_on(api, m, plan_a)
    hold_plan(api, plan_b)
    hold_own_login(api, m)
    give(api, m, "claude", plan_b)
    assert status(api)["state"] == "limited" and queued_holds(api) == [(1, "quota-reset")]


def test_a_plan_switch_does_not_end_the_computers_own_login_hold(api):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    plan_a, plan_b = add(api, name="Plan A")["id"], add(api, secret=OTHER, name="Plan B")["id"]
    m = machine(api)
    limited_on(api, m, plan_a)
    own = "2999-01-01T00:00:00.000000Z"
    hold_own_login(api, m, own)
    give(api, m, "claude", plan_b)
    # The bot is freed onto plan B (a plan switch never touches the computer's own login), and that login's hold stands.
    assert status(api)["state"] != "limited"
    with api.app.state.store.read() as c:
        assert login_holds.get(c, m["runner_id"], "", "claude")["until"] == own
    turn = claim(api, m)
    assert turn and signed_with(api, turn) == plan_b


def test_a_switch_leaves_a_bot_of_another_runtime_on_the_same_computer_alone(api):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    on(api, "cpo", "codex", "gpt-6.1-sol")
    plan_a, plan_b = add(api, name="Plan A")["id"], add(api, secret=OTHER, name="Plan B")["id"]
    m = machine(api)
    assign(api, m, "cpo")
    limited_on(api, m, plan_a)
    with api.app.state.store.transaction() as c:
        H.status_set(c, H.KEEPER, "cpo", state="limited", focus="Usage limit on ChatGPT", reason="test")
    give(api, m, "claude", plan_b)
    # Nothing re-judged cpo: its runtime is codex, and it has no hold on its own login to lift it.
    assert status(api, "cpo")["state"] == "limited" and status(api, "cpo")["focus"] == "Usage limit on ChatGPT"
    assert status(api)["state"] != "limited"


def test_a_new_secret_on_a_held_plan_ends_its_hold_as_try_now_does_and_a_rename_does_not(api):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    plan_a = add(api, name="Plan A")["id"]
    m = machine(api)
    limited_on(api, m, plan_a)
    assert status(api)["state"] == "limited"
    post(api, f"model-subscriptions/{plan_a}", {"name": "Plan A renamed"})
    with api.app.state.store.read() as c:
        assert login_holds.get(c, "", login_holds.subscription_login(plan_a), "claude")["until"] > H.now()
    assert status(api)["state"] == "limited"
    post(api, f"model-subscriptions/{plan_a}", {"secret": OTHER})      # a renewed plan, or another account
    with api.app.state.store.read() as c:
        hold = login_holds.get(c, "", login_holds.subscription_login(plan_a), "claude")
    assert hold["until"] <= H.now() and hold["cut"]
    assert status(api)["state"] != "limited"
    turn = claim(api, m)
    assert turn and signed_with(api, turn) == plan_a
    with api.app.state.store.read() as c:                 # the one probe turn tests the new value
        assert login_holds.get(c, "", login_holds.subscription_login(plan_a), "claude")["probe_job"] == turn["job_id"]


def test_removing_the_plan_a_bot_is_limited_on_frees_it_to_the_computers_own_login(api):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    plan_a = add(api, name="Plan A")["id"]
    m = machine(api, signed_in=True)
    limited_on(api, m, plan_a)
    assert status(api)["state"] == "limited"
    gone = api.delete(f"/api/v2/model-subscriptions/{plan_a}?confirm=1", headers=headers())
    assert gone.status_code == 200
    # The computer's row is deleted before the re-judge, so the bot's login is the computer's own, which is free.
    assert status(api)["state"] != "limited" and queued_holds(api) == [(0, "quota-reset")]
    turn = claim(api, m)
    assert turn and turn["bot"] == "ops" and signed_with(api, turn) is None


def test_a_switch_re_judges_a_bot_whose_named_fallback_runs_the_runtime_and_keeps_it_held_until_the_fallback_can_run(api):
    # The bot's primary is Codex, held on its own login; its named fallback is Claude. Switching Claude's plan re-judges
    # the bot (it runs Claude as its fallback), and it is freed only when that fallback can take the turn.
    setup(api)
    company(api)
    on(api, "ops", "codex", "gpt-6.1-sol")
    with api.app.state.store.transaction() as c:
        config = json.loads(c.execute("SELECT config_json FROM bot_config WHERE bot='ops'").fetchone()[0])
        config["fallback"] = {"harness": "claude", "model": "claude-sonnet-5-5"}
        c.execute("UPDATE bot_config SET config_json=? WHERE bot='ops'", (json.dumps(config),))
    plan_a, plan_b = add(api, name="Plan A")["id"], add(api, secret=OTHER, name="Plan B")["id"]
    m = machine(api, signed_in=True)                  # Claude is ready on its own login
    give(api, m, "claude", plan_a)
    post(api, "chat/ops", {"text": "Summarize the Acme account."})
    hold_own_login(api, m, runtime="codex")           # the primary is held
    hold_queued(api)
    # Plan B is held and the computer's own Claude login is held too: the fallback cannot take the turn, so it stays.
    hold_plan(api, plan_b)
    hold_own_login(api, m)
    give(api, m, "claude", plan_b)
    assert status(api)["state"] == "limited" and queued_holds(api) == [(1, "quota-reset")]
    # Plan B taken off and the Claude login free: the fallback runs the turn on the computer's own login, so it is freed.
    with api.app.state.store.transaction() as c:
        login_holds.clear(c, m["runner_id"], "", "claude")
    give(api, m, "claude", None)
    assert status(api)["state"] != "limited" and queued_holds(api) == [(0, "quota-reset")]


def test_a_held_own_login_keeps_the_bot_limited_and_its_job_waits_for_that_login_not_the_replaced_plan(api):
    from backend import wake_presets
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    plan_a = add(api, name="Plan A")["id"]
    m = machine(api)
    limited_on(api, m, plan_a)
    plan_end, own_end = "2999-01-01T20:00:00.000000Z", "2999-01-01T19:00:00.000000Z"
    hold_plan(api, plan_a, until=plan_end)
    hold_queued(api, until=plan_end)
    hold_own_login(api, m, until=own_end)
    give(api, m, "claude", None)                      # plan A taken off; the computer's own login is held until 19:00
    assert status(api)["state"] == "limited" and queued_holds(api) == [(1, "quota-reset")]
    assert queued_until(api) == [(1, own_end)]
    with api.app.state.store.transaction() as c:
        wake_presets.release_quota_resets(c, at="2999-01-01T18:59:59.000000Z")
    assert queued_until(api) == [(1, own_end)]
    with api.app.state.store.transaction() as c:
        wake_presets.release_quota_resets(c, at=own_end)
    assert queued_until(api) == [(0, own_end)]


def test_a_branch_runs_its_originals_runtime_now_not_the_runtime_it_stored(api):
    # The branch's stored config still says Claude, but it follows its original, which runs Codex now: a Claude plan
    # switch leaves it alone.
    setup(api)
    company(api)
    on(api, "cpo", "codex", "gpt-6.1-sol")
    on(api, "ops", "claude", "claude-sonnet-5-5")
    plan_a, plan_b = add(api, name="Plan A")["id"], add(api, secret=OTHER, name="Plan B")["id"]
    m = machine(api, signed_in=True)
    assign(api, m, "cpo")
    with api.app.state.store.transaction() as c:      # made a branch of cpo once it is on this computer
        config = json.loads(c.execute("SELECT config_json FROM bot_config WHERE bot='ops'").fetchone()[0])
        c.execute("UPDATE bot_config SET config_json=? WHERE bot='ops'",
                  (json.dumps({**config, "shared_from": "cpo"}),))
    give(api, m, "claude", plan_a)
    post(api, "chat/ops", {"text": "Summarize the Acme account."})
    hold_queued(api)
    give(api, m, "claude", plan_b)
    assert status(api)["state"] == "limited" and queued_holds(api) == [(1, "quota-reset")]


def test_try_now_on_an_idle_branch_ends_the_hold_on_the_login_its_original_runs_on(api):
    # The branch's stored config says Claude, but it follows its original, which runs Codex: Try now ends the hold on
    # the Codex login the branch signs in with, not one on Claude.
    from backend.execution import clear_limits
    setup(api)
    company(api)
    on(api, "cpo", "codex", "gpt-6.1-sol")
    on(api, "ops", "claude", "claude-sonnet-5-5")
    m = machine(api, signed_in=True)
    assign(api, m, "cpo")
    with api.app.state.store.transaction() as c:      # made a branch of cpo once it is on this computer
        config = json.loads(c.execute("SELECT config_json FROM bot_config WHERE bot='ops'").fetchone()[0])
        c.execute("UPDATE bot_config SET config_json=? WHERE bot='ops'",
                  (json.dumps({**config, "shared_from": "cpo"}),))
        login_holds._save(c, {"runner_id": m["runner_id"], "profile": "", "runtime": "codex",
                              "until": "2999-01-01T00:00:00.000000Z", "since": H.now(), "error": CLAUDE_LIMIT})
    with api.app.state.store.transaction() as c:
        assert clear_limits(c, api.app.state.store.settings, H.KEEPER, "ops") == 1
    with api.app.state.store.read() as c:
        assert login_holds.get(c, m["runner_id"], "", "codex")["until"] <= H.now()
