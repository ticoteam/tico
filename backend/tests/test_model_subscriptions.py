"""Provider plans, agent Paid by, and the credentials and usage of the resulting turns."""

import json
from datetime import datetime, timedelta, timezone
from importlib import import_module

import pytest

from backend import agent_subscriptions, login_holds
from backend import model_subscriptions as MS
from backend.auth import Identity
from backend.store import H, Problem, encode
from backend.tests.helpers.api import assign, claim, get, headers, post, restrict, runner, unrelated_task_managers
from backend.tests.helpers.credentials import setup
from backend import bot_settings as BS
from backend.tests.helpers.model_subscriptions import (
    OTHER,
    TOKEN,
    add,
    codex_file,
    company,
    give,
    machine,
    on,
    pick,
)

CLAUDE_LIMIT = "You've hit your limit · resets 2:40am (UTC)"
NO_COMPUTER = {"window": None, "used_pct": None, "cost_usd_month": None, "reason": "Not on a computer yet"}


def limit_notice_soon():
    # Relative to the clock: a date-less reset more than 20 hours away is not believed (login_holds.reset_from_text).
    soon = datetime.now(timezone.utc) + timedelta(hours=2)
    return f"You've hit your limit · resets {soon:%-I:%M}{soon:%p} (UTC)".replace("AM", "am").replace("PM", "pm")


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
    assert saved["added_by"] == "human:ana" and saved["computers"] == [] and saved["usage"] == NO_COMPUTER
    assert TOKEN not in json.dumps(saved) and saved["preview"].endswith(TOKEN[-4:])
    # Saving it turns its provider on, so its models are offered.
    with api.app.state.store.read() as c:
        from backend import providers

        assert "anthropic" in providers.load(c, api.app.state.store.settings)["enabled"]
    assert add(api, "codex_login", codex_file(), name="ChatGPT", token="ben-test")["preview"] == "ana@acme.example"
    body = get(api, "model-subscriptions", "cara-test")  # a member reads, no values
    assert [s["name"] for s in body["subscriptions"]] == ["ChatGPT", "Company Max"]
    assert [k["kind"] for k in body["kinds"]] == [
        "claude_token",
        "codex_login",
        "anthropic_key",
        "openai_key",
        "cursor_key",
        "meta_key",
    ]
    assert TOKEN not in json.dumps(body) and "r-1" not in json.dumps(body)
    with api.app.state.store.read() as c:
        sub = MS.row(c, saved["id"])  # the value is the vault's service row, never in the subscription's own row
        assert sub["sealed"] == "" and sub["credential_id"] == MS.CREDENTIAL + saved["id"]
        assert c.execute("SELECT kind FROM credentials WHERE id=?", (sub["credential_id"],)).fetchone()[0] == "service"
        assert not c.execute(
            "SELECT 1 FROM idempotency WHERE response LIKE ? OR request_hash LIKE ?", (f"%{TOKEN}%", f"%{TOKEN}%")
        ).fetchone()
        assert not c.execute("SELECT 1 FROM events WHERE detail LIKE ?", (f"%{TOKEN}%",)).fetchone()
    add(api, "claude_token", "not-a-token", expected=422)
    add(api, "codex_login", "{}", expected=422)
    api.app.state.store.settings.test_identities["assistant"] = Identity(
        "human:ana", "owner", email="ana@acme.example", via="assistant"
    )
    add(api, token="assistant", expected=403)  # a secret is pasted by the person
    support = Identity("human:support", "owner", email="ana@acme.example", support="full")
    with pytest.raises(Problem):  # a support session changes nothing
        MS._admin(api.app.state.auth, support)
    # Rename, and reconnect: a new value counts again from `new`.
    post(api, f"model-subscriptions/{saved['id']}", {"name": "Max"}, "cara-test", expected=403)
    assert post(api, f"model-subscriptions/{saved['id']}", {"name": "Max"})["name"] == "Max"
    with api.app.state.store.transaction() as c:
        MS._status(c, saved["id"], "needs_reconnect", "expired")
    again = post(api, f"model-subscriptions/{saved['id']}", {"secret": OTHER})
    assert again["status"] == "new" and again["revision"] == 2 and again["preview"].endswith(OTHER[-4:])


def test_a_demoted_adder_can_remove_a_model_key_but_other_members_cannot(api):
    setup(api)
    sid = add(api, "openai_key", "sk-acme-synthetic-key-123456789", token="ben-test")["id"]
    post(api, "access/humans/ben", {"role": "member"})
    assert listed(api, "ben-test")[0]["can_remove"]
    assert not listed(api, "cara-test")[0]["can_remove"]
    path = f"/api/v2/model-subscriptions/{sid}"
    assert api.delete(path, headers=headers("cara-test")).status_code == 403
    assert api.delete(path, headers=headers("ben-test")).json()["deleted"]
    assert listed(api) == []


def test_an_agent_picks_one_compatible_provider_per_runtime(api):
    setup(api)
    company(api)
    first, second = add(api, name="One")["id"], add(api, name="Two")["id"]
    chatgpt = add(api, "codex_login", codex_file(), name="ChatGPT")["id"]
    pick(api, "ops", "codex", first, expected=422)
    pick(api, "ops", "claude", first)
    pick(api, "ops", "claude", second)
    pick(api, "ops", "codex", chatgpt)
    pick(api, "cpo", "claude", first)
    pick(api, "ops", "claude", first, token="cara-test", expected=403)
    assert {tool: p["subscription_id"] for tool, p in get(api, "bots/ops/subscriptions")["subscriptions"].items()} == {
        "claude": second,
        "codex": chatgpt,
    }
    pick(api, "ops", "claude")
    assert "claude" not in get(api, "bots/ops/subscriptions")["subscriptions"]
    assert get(api, "bots/cpo/subscriptions")["subscriptions"]["claude"]["subscription_id"] == first


def test_a_turn_uses_its_exact_pick_and_waits_after_the_provider_refuses_it(api):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    sid = add(api)["id"]
    m = machine(api, claude="rejected")  # its own Claude login refused: the subscription still runs
    pick(api, "ops", "claude", sid)
    [entry] = [e for e in get(api, "runners/assignments", m["token"]) if e["bot"] == "ops"]
    assert entry["subscriptions"] == {"claude": "claude_token"}  # its readiness needs no Claude sign-in here
    post(api, "chat/ops", {"text": "Summarize the Acme account."})
    turn = claim(api, m)
    assert turn and turn["config"]["runtime"] == "claude" and turn["credential_vault"] is True
    handed = turn["attempt_credential"]
    assert handed["id"] == sid and handed["env"] == "CLAUDE_CODE_OAUTH_TOKEN"
    assert (handed["value"], handed["billing"]) == (TOKEN, "subscription")
    assert not get(api, "credential-runtime", turn["token"]).get("subscriptions")
    get(api, "credential-runtime", "ana-test", expected=403)
    post(api, f"attempts/{turn['id']}/started", {"thread_id": "t-sub"}, m["token"])
    post(
        api,
        f"attempts/{turn['id']}/complete",
        {
            "outcome": "failed",
            "last_seq": 0,
            "auth_rejected": {"runtime": "claude", "reason": "OAuth token has expired"},
        },
        m["token"],
    )
    [mine] = listed(api)
    assert mine["status"] == "needs_reconnect" and mine["status_detail"] == "OAuth token has expired"
    with api.app.state.store.read() as c:
        # Nothing waits on a computer sign-in card: the refusal was the subscription's.
        assert not c.execute("SELECT 1 FROM task_reconnects WHERE key LIKE 'login:%'").fetchone()
    assert get(api, "bots/ops/subscriptions")["subscriptions"]["claude"]["subscription_id"] == sid
    assert claim(api, m) is None  # no sign-in here and no Gateway in this preset: it waits
    # Reconnecting with a new value makes it count again, and a completed turn on that revision marks it working.
    post(api, f"model-subscriptions/{sid}", {"secret": OTHER})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE attempt_subscriptions SET revision=2 WHERE attempt_id=?", (turn["id"],))
        MS.turn_worked(c, turn["id"], "claude")
    assert listed(api)[0]["status"] == "working"


def given_events(api):
    with api.app.state.store.read() as c:
        return [
            (r["actor"], json.loads(r["detail"])["runner"])
            for r in c.execute("SELECT actor,detail FROM events WHERE action='subscription.given' ORDER BY rowid")
        ]


def test_adding_a_plan_does_not_bind_a_single_computer_or_its_unset_agent(api):
    setup(api)
    company(api)
    m = machine(api, signed_in=True)
    add(api)
    assert effect(api, m) is None and not given_events(api)
    assert get(api, "bots/ops/subscriptions")["subscriptions"] == {}
    assert "ops" not in {a["id"] for a in listed(api)[0]["agents"]}


def test_a_plan_added_on_a_company_with_several_computers_waits_for_the_person_to_choose(api):
    setup(api)
    company(api)
    m = machine(api)
    machine(api, bot="cpo", label="Office Mac")
    add(api)
    assert effect(api, m) is None and given_events(api) == []
    assert {"ops", "cpo"}.isdisjoint(a["id"] for a in listed(api)[0]["agents"])


def test_handing_a_turn_its_subscription_is_not_a_write(api):
    """The hub sending the run its sign-in is what the computer's own login gets too: the run has written nothing."""
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    sid = add(api)["id"]
    m = machine(api, signed_in=True)
    pick(api, "ops", "claude", sid)
    post(api, "chat/ops", {"text": "Summarize the Acme account."})
    turn = claim(api, m)
    post(api, f"attempts/{turn['id']}/started", {"thread_id": "t-sub"}, m["token"])
    assert turn["attempt_credential"]["id"] == sid
    with api.app.state.store.read() as c:
        assert c.execute("SELECT coalesce(writes,0) FROM attempts WHERE id=?", (turn["id"],)).fetchone()[0] == 0
        assert (
            c.execute(
                "SELECT delivery FROM attempt_subscriptions WHERE attempt_id=? AND subscription_id=?", (turn["id"], sid)
            ).fetchone()[0]
            == "claim"
        )


def test_a_subscription_turn_that_only_reports_a_busy_model_is_retried_not_held(api):
    """Its sign-in was handed, but the run did nothing but say the model was busy: that is no work to hold for review."""
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    sid = add(api)["id"]
    m = machine(api, signed_in=True)
    pick(api, "ops", "claude", sid)
    post(api, "chat/ops", {"text": "Summarize the Acme account."})
    turn = claim(api, m)
    post(api, f"attempts/{turn['id']}/started", {"thread_id": "t-busy"}, m["token"])
    get(api, "credential-runtime", turn["token"])
    notice = "Selected model is at capacity. Please try a different model."
    post(
        api,
        f"attempts/{turn['id']}/events",
        {
            "events": [
                {"seq": 1, "kind": "status", "payload": {"state": "active", "tool_events": True}},
                {"seq": 2, "kind": "message", "payload": {"text": notice, "final": False}},
                {"seq": 3, "kind": "error", "payload": {"error": notice, "limit": True}},
            ]
        },
        m["token"],
    )
    post(api, f"attempts/{turn['id']}/complete", {"outcome": "failed", "last_seq": 3, "text": notice}, m["token"])
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM jobs WHERE id=?", (turn["job_id"],)).fetchone()[0] == "queued"


def test_a_limit_holds_every_agent_selecting_the_plan_without_holding_the_local_login(api):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    on(api, "cpo", "claude", "claude-sonnet-5-5")
    sid = add(api)["id"]
    m, n = machine(api, signed_in=True), machine(api, signed_in=True, bot="cpo", label="Other Mac")
    pick(api, "ops", "claude", sid)
    pick(api, "cpo", "claude", sid)
    post(api, "chat/ops", {"text": "Summarize the Acme account."})
    turn = claim(api, m)
    post(api, f"attempts/{turn['id']}/started", {"thread_id": "limit"}, m["token"])
    # No reset named: a turn paused at a named reset keeps its saved session for that reset instead (source pause).
    post(
        api,
        f"attempts/{turn['id']}/events",
        {"events": [{"seq": 1, "kind": "error", "payload": {"error": "You've hit your usage limit.", "limit": True}}]},
        m["token"],
    )
    post(
        api,
        f"attempts/{turn['id']}/complete",
        {"outcome": "failed", "last_seq": 1, "limited": True, "text": ""},
        m["token"],
    )
    sub_login = login_holds.subscription_login(sid)
    with api.app.state.store.read() as c:
        hold = login_holds.get(c, "", sub_login, "claude")
        assert hold and hold["until"] > H.now()
        # Not the computer's own login: that one is not held.
        assert not login_holds.get(c, m["runner_id"], "", "claude")
        assert login_holds.canonical(c, n["runner_id"], "", "claude") == ""
    assert listed(api)[0]["usage"]["used_pct"] == 100
    post(api, "chat/cpo", {"text": "Price the Acme plan."})
    assert claim(api, n) is None
    assert claim(api, m) is None
    with api.app.state.store.transaction() as c:
        login_holds.try_now(c, "", sub_login, "claude")
    assert signed_with(api, claim(api, n)) == sid


def test_a_held_pick_waits_on_both_presets_and_clearing_it_allows_gateway(api, monkeypatch):
    from backend import models_preset
    from backend.tests.helpers.gateway_connection import KEY
    from backend.tests.helpers.models_preset import choose

    setup(api)
    monkeypatch.setenv("TICO_GATEWAY_KEY", KEY)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    sid = add(api)["id"]
    m = machine(api)  # not signed in to Claude itself: the subscription signs it in
    post(
        api,
        "runners/heartbeat",
        {
            "version": "test",
            "platform": "test",
            "capabilities": ["model_subscriptions_v1", agent_subscriptions.CAPABILITY],
            "readiness": {
                "schema_version": 1,
                "isolated": True,
                "runtimes": {
                    "claude": {"installed": True, "authenticated": "missing"},
                    "pi": {"installed": True, "authenticated": "missing"},
                },
                # the runner's own answer once runners/assignments stops
                # naming the held subscription
                "bots": {
                    "ops": {
                        "runtime": "claude",
                        "ready": False,
                        "model": "claude-sonnet-5-5",
                        "repository_present": True,
                        "configuration_valid": True,
                        "problems": ["claude is not signed in"],
                    }
                },
            },
        },
        token=m["token"],
    )
    pick(api, "ops", "claude", sid)
    with api.app.state.store.transaction() as c:  # a limit met on it until the reset, on another computer
        login_holds._save(
            c,
            {
                "runner_id": "",
                "profile": login_holds.subscription_login(sid),
                "runtime": "claude",
                "until": "2999-01-01T00:00:00.000000Z",
                "since": H.now(),
                "error": CLAUDE_LIMIT,
            },
        )
    with api.app.state.store.read() as c:
        assert login_holds.holding(c, "", login_holds.subscription_login(sid), "claude") == "held"
    choose(api, "subscriptions")
    post(api, "chat/ops", {"text": "Summarize the Acme account."})
    assert claim(api, m) is None  # no subscription, no sign-in here, no Gateway: it waits
    choose(api, models_preset.RECOMMENDED)
    # Under the Gateway choice it still waits: the computer does not fall back to the Gateway until an admin says so.
    assert claim(api, m) is None
    post(api, f"computers/{m['runner_id']}/gateway-fallback", {"on": True})
    assert claim(api, m) is None
    pick(api, "ops", "claude")
    turn = claim(api, m)
    assert turn and turn["config"]["runtime"] == "pi" and turn["config"]["waiting_for_sign_in"] == "claude"
    assert "subscriptions" not in turn and not turn["config"].get("primary_held")


def test_changing_an_exact_pick_affects_the_next_turn_and_keeps_the_current_turn(api):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    first, second = add(api, name="One")["id"], add(api, name="Two")["id"]
    m = machine(api, claude="rejected")
    pick(api, "ops", "claude", first)
    post(api, "chat/ops", {"text": "Summarize the Acme account."})
    turn = claim(api, m)
    post(api, f"attempts/{turn['id']}/started", {"thread_id": "t-one"}, m["token"])
    pick(api, "ops", "claude", second)
    assert not post(api, f"attempts/{turn['id']}/renew", {}, m["token"]).get("stop")
    assert signed_with(api, turn) == first
    post(api, f"attempts/{turn['id']}/complete", {"outcome": "completed", "last_seq": 0, "text": "Done."}, m["token"])
    post(api, "chat/ops", {"text": "Now summarize the Beta account."})
    assert signed_with(api, claim(api, m)) == second


def test_removing_a_pick_confirms_and_unsets_every_agent_selecting_it(api):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    sid = add(api)["id"]
    m = machine(api, claude="rejected")
    pick(api, "ops", "claude", sid)
    pick(api, "cpo", "claude", sid)
    post(api, "chat/ops", {"text": "Summarize the Acme account."})
    turn = claim(api, m)
    post(api, f"attempts/{turn['id']}/started", {"thread_id": "t-one"}, m["token"])
    gone = api.delete(f"/api/v2/model-subscriptions/{sid}", headers=headers())
    assert gone.status_code == 409
    error = gone.json()["error"]
    assert (
        error["code"] == "confirm_restart"
        and error["running"] == 1
        and {"ops", "cpo"} <= {a["bot"] for a in error["agents"]}
    )
    assert api.delete(f"/api/v2/model-subscriptions/{sid}?confirm=1", headers=headers("cara-test")).status_code == 403
    r = api.delete(f"/api/v2/model-subscriptions/{sid}?confirm=1", headers=headers())
    assert r.status_code == 200 and r.json()["stopped"] == 1
    assert listed(api) == []
    assert "claude" not in get(api, "bots/ops/subscriptions")["subscriptions"]
    assert "claude" not in get(api, "bots/cpo/subscriptions")["subscriptions"]
    assert post(api, f"attempts/{turn['id']}/renew", {}, m["token"])["stop"] is True


def test_a_refreshed_chatgpt_login_comes_back_only_from_the_run_that_had_it(api):
    setup(api)
    company(api)
    on(api, "ops", "codex", "gpt-6.2-sol")
    sid = add(api, "codex_login", codex_file(), name="ChatGPT")["id"]
    m = machine(api)
    pick(api, "ops", "codex", sid)
    post(api, "chat/ops", {"text": "Summarize the Acme account."})
    turn = claim(api, m)
    handed = turn["attempt_credential"]
    assert handed["file"] == "auth.json" and handed["revision"] == 1
    assert json.loads(handed["value"])["tokens"]["refresh_token"] == "r-1"
    body = {"attempt": turn["id"], "subscription": sid, "revision": 1}
    other = runner(api, label="Other Mac")
    post(
        api,
        "runner-model-subscription-refresh",
        {**body, "value": codex_file(refresh="r-2")},
        other["token"],
        expected=403,
    )
    post(
        api, "runner-model-subscription-refresh", {**body, "value": codex_file(refresh="r-2")}, "ana-test", expected=403
    )
    assert (
        post(api, "runner-model-subscription-refresh", {**body, "value": codex_file("acct-2", "r-2")}, m["token"])[
            "stored"
        ]
        is False
    )  # another account's file is never kept
    assert post(api, "runner-model-subscription-refresh", {**body, "value": codex_file(refresh="r-2")}, m["token"]) == {
        "stored": True,
        "revision": 2,
    }
    assert (
        post(api, "runner-model-subscription-refresh", {**body, "value": codex_file(refresh="r-3")}, m["token"])[
            "stored"
        ]
        is False
    )  # stale: the stored one moved on
    with api.app.state.store.read() as c:
        sub = MS.row(c, sid)
        assert json.loads(MS.value_of(c, api.app.state.vault.cipher, sub))["tokens"]["refresh_token"] == "r-2"
        assert MS.handed(c, turn["id"], "codex")["revision"] == 2  # the run's record moved with its file
    post(api, f"attempts/{turn['id']}/started", {"thread_id": "t-codex"}, m["token"])
    post(api, f"attempts/{turn['id']}/complete", {"outcome": "completed", "last_seq": 0, "text": "Done."}, m["token"])
    assert listed(api)[0]["status"] == "working"  # it worked on the revision it refreshed to
    # A finished run writes nothing back, though its revision is still the current one.
    late = {"attempt": turn["id"], "subscription": sid, "revision": 2, "value": codex_file(refresh="r-9")}
    post(api, "runner-model-subscription-refresh", late, m["token"], expected=409)
    # An unselected payer can still refresh from a live attempt bound to it.
    post(api, "chat/ops", {"text": "And the Globex account?"})
    second = claim(api, m)
    assert second["attempt_credential"]["revision"] == 2
    post(api, f"attempts/{second['id']}/started", {"thread_id": "t-codex-2"}, m["token"])
    pick(api, "ops", "codex")
    assert post(api, "runner-model-subscription-refresh", {**late, "attempt": second["id"]}, m["token"])["stored"]
    assert "subscriptions" not in get(api, "credential-runtime", second["token"])
    with api.app.state.store.read() as c:
        assert (
            json.loads(MS.value_of(c, api.app.state.vault.cipher, MS.row(c, sid)))["tokens"]["refresh_token"] == "r-9"
        )


def test_a_reconnect_confirms_then_restarts_runs_on_the_old_value_and_their_late_refusal_leaves_the_new_one(api):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    sid = add(api)["id"]
    m = machine(api)  # no Claude login of its own: the subscription is its only one
    pick(api, "ops", "claude", sid)
    pick(api, "cpo", "claude", sid)
    post(api, "chat/ops", {"text": "Summarize the Acme account."})
    turn = claim(api, m)
    post(api, f"attempts/{turn['id']}/started", {"thread_id": "t-old"}, m["token"])
    # A run would stop: the reconnect asks first, and nothing is saved without the confirm (not the new name either).
    error = post(api, f"model-subscriptions/{sid}", {"secret": OTHER, "name": "Renamed"}, expected=409)["error"]
    assert error["code"] == "confirm_restart" and error["running"] == 1 and error["computers"] == 1
    [kept] = listed(api)
    assert kept["revision"] == 1 and kept["name"] == "Company Max" and kept["preview"].endswith(TOKEN[-4:])
    assert not post(api, f"attempts/{turn['id']}/renew", {}, m["token"]).get("stop")
    assert post(api, f"model-subscriptions/{sid}", {"name": "Max"})["name"] == "Max"  # a rename never asks
    done = post(api, f"model-subscriptions/{sid}", {"secret": OTHER, "confirm": True})
    assert done["revision"] == 2 and done["preview"].endswith(OTHER[-4:])
    with api.app.state.store.read() as c:
        assert (
            json.loads(
                c.execute(
                    "SELECT detail FROM events WHERE action='subscription.updated' ORDER BY rowid DESC LIMIT 1"
                ).fetchone()[0]
            )["stopped"]
            == 1
        )
    # The run handed revision 1 is stopped and put back in the queue, and never reads the new value.
    renewed = post(api, f"attempts/{turn['id']}/renew", {}, m["token"])
    assert renewed["stop"] is True and "reconnected" in renewed["stop_reason"]
    assert "subscriptions" not in get(api, "credential-runtime", turn["token"])
    # Its refusal of the old value arrives late: the new value stays as it is, and no computer card waits on it.
    post(
        api,
        f"attempts/{turn['id']}/complete",
        {
            "outcome": "failed",
            "last_seq": 0,
            "auth_rejected": {"runtime": "claude", "reason": "OAuth token has expired"},
        },
        m["token"],
    )
    assert listed(api)[0]["status"] == "new"
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM task_reconnects WHERE key LIKE 'login:%'").fetchone()


def test_a_stale_codex_turn_cannot_overwrite_or_reject_the_newer_login(api):
    setup(api)
    company(api)
    on(api, "ops", "codex", "gpt-6.2-sol")
    on(api, "cpo", "codex", "gpt-6.2-sol")
    sid = add(api, "codex_login", codex_file(), name="ChatGPT")["id"]
    m, n = machine(api), machine(api, bot="cpo", label="Other Mac")
    pick(api, "ops", "codex", sid)
    pick(api, "cpo", "codex", sid)
    post(api, "chat/ops", {"text": "Summarize the Acme account."})
    post(api, "chat/cpo", {"text": "Price the Acme plan."})
    first, second = claim(api, m), claim(api, n)
    assert first["attempt_credential"]["revision"] == second["attempt_credential"]["revision"] == 1
    post(api, f"attempts/{second['id']}/started", {"thread_id": "t-second"}, n["token"])
    assert post(
        api,
        "runner-model-subscription-refresh",
        {"attempt": first["id"], "subscription": sid, "revision": 1, "value": codex_file(refresh="r-2")},
        m["token"],
    )["stored"]
    assert not post(
        api,
        "runner-model-subscription-refresh",
        {"attempt": second["id"], "subscription": sid, "revision": 1, "value": codex_file(refresh="r-3")},
        n["token"],
    )["stored"]
    # An already running attempt may report a refusal from before the refresh.

    post(
        api,
        f"attempts/{second['id']}/complete",
        {"outcome": "failed", "last_seq": 0, "auth_rejected": {"runtime": "codex", "reason": "refresh_token_reused"}},
        n["token"],
    )
    assert listed(api)[0]["status"] != "needs_reconnect"


def test_a_computer_with_only_a_codex_subscription_runs_codex_checks_and_their_limit_holds_it(api):
    from backend.readiness import can_run

    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    sid = add(api, "codex_login", codex_file(), name="ChatGPT")["id"]
    m = machine(api, signed_in=True)  # its own Claude login; no Codex login of its own
    post(
        api,
        "runners/heartbeat",
        {
            "version": "test",
            "platform": "test",
            "capabilities": [
                "model_subscriptions_v1",
                "task_lanes_v2",
                "session_roots_v1",
                agent_subscriptions.CAPABILITY,
            ],
            "readiness": {
                "schema_version": 1,
                "runtimes": {
                    "claude": {"installed": True, "authenticated": "ready"},
                    "codex": {"installed": True, "authenticated": "missing"},
                },
                "bots": {"ops": {"runtime": "claude", "ready": True}},
            },
        },
        token=m["token"],
    )
    machine(api, bot="cpo", label="Other Mac")
    with api.app.state.store.read() as c:
        before = can_run(c, "ops", "codex")
    # A saved ChatGPT plan can pay for the agent even when its computer is signed out of Codex.
    assert before["can_run"] is False and before["sign_in"] is True
    assert before["link"] == "#/settings/providers" and "Paid by" in before["fix"]
    pick(api, "ops", "codex", sid)
    pick(api, "cpo", "codex", sid)
    with api.app.state.store.read() as c:
        # The subscription is its Codex sign-in for every check (a move, a task model, a check model, Health) ...
        assert can_run(c, "ops", "codex")["can_run"] is True
    parent = post(api, "tasks", {"owner": "ops", "title": "Ship the import fix", "body": "Open the PR."})
    turn = claim(api, m)
    assert turn["task"]["id"] == parent["id"]
    post(api, f"attempts/{turn['id']}/started", {"thread_id": "t-parent"}, m["token"])
    sub = post(
        api,
        "tasks",
        {
            "owner": "ops",
            "title": "Review it on Codex",
            "body": "Read it.",
            "check_model": "codex",
            "check_role": "review",
            "parent": parent["id"],
        },
        turn["token"],
    )
    post(api, f"attempts/{turn['id']}/complete", {"outcome": "completed", "last_seq": 0, "text": "Done."}, m["token"])
    check = claim(api, m)  # handed out though Codex is not signed in on the computer
    assert check and check["task"]["id"] == sub["id"] and check["config"]["runtime"] == "codex"
    assert check["attempt_credential"]["id"] == sid
    post(api, f"attempts/{check['id']}/started", {"thread_id": "t-check"}, m["token"])
    post(
        api,
        f"attempts/{check['id']}/events",
        {"events": [{"seq": 1, "kind": "error", "payload": {"error": "You've hit your usage limit.", "limit": True}}]},
        m["token"],
    )
    post(
        api,
        f"attempts/{check['id']}/complete",
        {"outcome": "failed", "last_seq": 1, "limited": True, "text": ""},
        m["token"],
    )
    with api.app.state.store.read() as c:
        # The check's limit holds the shared subscription on every computer given it ...
        assert login_holds.get(c, "", login_holds.subscription_login(sid), "codex")["until"] > H.now()
        assert not login_holds.get(c, m["runner_id"], "", "codex")
        # ... and the check keeps its own schedule: it alone is held, the bot is not limited.
        assert H.status(c, "ops")["state"] != "limited" and "on a check" in H.status(c, "ops")["focus"]


def limited_on(api, m, sid, bot="ops"):
    """A turn of `bot` on computer `m`, signed in with subscription `sid`, meets a usage limit that names its reset: the
    bot is limited and its queued work waits on the login's hold until the reset."""
    pick(api, bot, "claude", sid)
    post(api, f"chat/{bot}", {"text": "Summarize the Acme account."})
    turn = claim(api, m)
    post(api, f"attempts/{turn['id']}/started", {"thread_id": "limit"}, m["token"])
    post(
        api,
        f"attempts/{turn['id']}/events",
        {"events": [{"seq": 1, "kind": "error", "payload": {"error": limit_notice_soon(), "limit": True}}]},
        m["token"],
    )
    post(
        api,
        f"attempts/{turn['id']}/complete",
        {"outcome": "failed", "last_seq": 1, "limited": True, "text": ""},
        m["token"],
    )
    return turn


def status(api, bot="ops"):
    with api.app.state.store.read() as c:
        return H.status(c, bot) or {}


def waits(api, bot="ops"):
    """Why the claim passes over each of the bot's queued jobs on its computer (None: it may start)."""
    from backend import execution

    with api.app.state.store.read() as c:
        place = c.execute("SELECT runner_id FROM assignments WHERE bot=?", (bot,)).fetchone()
        settings = api.app.state.store.settings
        return [why for _, why in execution.queued_reasons(c, settings, place["runner_id"], bot)]


def signed_with(api, turn):
    """The subscription a turn was handed (its attempt's record), or None for the computer's own login."""
    with api.app.state.store.read() as c:
        found = c.execute(
            "SELECT subscription_id FROM attempt_subscriptions WHERE attempt_id=?", (turn["id"],)
        ).fetchone()
    return found[0] if found else None


def hold_plan(api, sid, until="2999-01-01T00:00:00.000000Z"):
    """A plan's own usage-limit hold, as a limit met on it elsewhere leaves it."""
    with api.app.state.store.transaction() as c:
        login_holds._save(
            c,
            {
                "runner_id": "",
                "profile": login_holds.subscription_login(sid),
                "runtime": "claude",
                "until": until,
                "since": H.now(),
                "error": CLAUDE_LIMIT,
            },
        )


def hold_own_login(api, m, until="2999-01-01T00:00:00.000000Z", runtime="claude"):
    with api.app.state.store.transaction() as c:
        login_holds._save(
            c,
            {
                "runner_id": m["runner_id"],
                "profile": "",
                "runtime": runtime,
                "until": until,
                "since": H.now(),
                "error": CLAUDE_LIMIT,
            },
        )


def limit(api, bot="ops"):
    """The bot is limited, as a limit with a named reset leaves it."""
    with api.app.state.store.transaction() as c:
        H.status_set(c, H.TICO_ACTOR, bot, state="limited", focus="Usage limit", reason="test")


def test_switching_the_agents_pick_frees_queued_work_held_on_the_old_plan(api):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    plan_a, plan_b = add(api, name="Plan A")["id"], add(api, secret=OTHER, name="Plan B")["id"]
    m = machine(api)  # no login of its own: the held work waits for the reset
    limited_on(api, m, plan_a)
    assert status(api)["state"] == "limited" and claim(api, m) is None
    pick(api, "ops", "claude", plan_b)
    turn = claim(api, m)
    assert turn and turn["bot"] == "ops" and signed_with(api, turn) == plan_b


def test_a_held_plan_waits_on_its_hold_when_the_computers_named_home_is_not_signed_in(api):
    # A computer that names its default home ("own"): the login a turn signs in with is that home, which is not
    # signed in, so the held plan is still what the bot waits for, and Try now ends that plan's hold.
    from backend.execution import own_login_hold

    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    plan_a = add(api, name="Plan A")["id"]
    plan_b = add(api, secret=OTHER, name="Plan B")["id"]
    m = machine(api)
    assign(api, m, "cpo")
    on(api, "cpo", "claude", "claude-sonnet-5-5")
    pick(api, "cpo", "claude", plan_b)
    hold_plan(api, plan_b)
    limit(api, "cpo")
    home_less(api, m)
    limited_on(api, m, plan_a)
    assert claim(api, m) is None
    with api.app.state.store.read() as c:
        assert own_login_hold(c, api.app.state.store.settings, "ops")["profile"] == login_holds.subscription_login(
            plan_a
        )
    post(api, "bots/ops/limit/retry", {})
    with api.app.state.store.read() as c:
        assert login_holds.get(c, "", login_holds.subscription_login(plan_a), "claude")["until"] <= H.now()
        assert login_holds.get(c, "", login_holds.subscription_login(plan_b), "claude")["until"] > H.now()


def home_less(api, m):
    """A heartbeat naming the computer's default home ("own"), with Claude not signed in there."""
    post(
        api,
        "runners/heartbeat",
        {
            "version": "test",
            "platform": "test",
            "capabilities": ["model_subscriptions_v1", "computer_logins_v1", agent_subscriptions.CAPABILITY],
            "readiness": {
                "schema_version": 1,
                "computer_profile": "own",
                "runtimes": {"claude": {"installed": True, "authenticated": "missing"}},
                "bots": {"ops": {"runtime": "claude", "ready": True}},
            },
        },
        token=m["token"],
    )


def test_switching_to_another_held_plan_waits_even_with_a_ready_local_login(api):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    plan_a, plan_b = add(api, name="Plan A")["id"], add(api, secret=OTHER, name="Plan B")["id"]
    m = machine(api, signed_in=True)
    limited_on(api, m, plan_a)
    hold_plan(api, plan_b)
    pick(api, "ops", "claude", plan_b)
    assert claim(api, m) is None
    with api.app.state.store.transaction() as c:
        login_holds.try_now(c, "", login_holds.subscription_login(plan_b), "claude")
    assert signed_with(api, claim(api, m)) == plan_b


def test_a_new_plan_held_at_its_limit_keeps_the_bot_limited_when_the_computers_own_login_is_held_too(api):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    plan_a, plan_b = add(api, name="Plan A")["id"], add(api, secret=OTHER, name="Plan B")["id"]
    m = machine(api, signed_in=True)
    limited_on(api, m, plan_a)
    hold_plan(api, plan_b)
    hold_own_login(api, m)
    pick(api, "ops", "claude", plan_b)
    assert status(api)["state"] == "limited" and claim(api, m) is None


def test_a_plan_switch_does_not_end_the_computers_own_login_hold(api):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    plan_a, plan_b = add(api, name="Plan A")["id"], add(api, secret=OTHER, name="Plan B")["id"]
    m = machine(api)
    limited_on(api, m, plan_a)
    own = "2999-01-01T00:00:00.000000Z"
    hold_own_login(api, m, own)
    pick(api, "ops", "claude", plan_b)
    # The bot is freed onto plan B (a plan switch never touches the computer's own login), and that login's hold stands.
    with api.app.state.store.read() as c:
        assert login_holds.get(c, m["runner_id"], "", "claude")["until"] == own
    turn = claim(api, m)
    assert turn and signed_with(api, turn) == plan_b


def test_a_switch_leaves_a_bot_of_another_runtime_on_the_same_computer_alone(api):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    on(api, "cpo", "codex", "gpt-6.2-sol")
    plan_a, plan_b = add(api, name="Plan A")["id"], add(api, secret=OTHER, name="Plan B")["id"]
    m = machine(api)
    assign(api, m, "cpo")
    limited_on(api, m, plan_a)
    with api.app.state.store.transaction() as c:
        H.status_set(c, H.TICO_ACTOR, "cpo", state="limited", focus="Usage limit on ChatGPT", reason="test")
    pick(api, "ops", "claude", plan_b)
    # Nothing re-judged cpo: its runtime is codex, and it has no hold on its own login to lift it.
    assert status(api, "cpo")["state"] == "limited" and status(api, "cpo")["focus"] == "Usage limit on ChatGPT"


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
    post(api, f"model-subscriptions/{plan_a}", {"secret": OTHER})  # a renewed plan, or another account
    with api.app.state.store.read() as c:
        hold = login_holds.get(c, "", login_holds.subscription_login(plan_a), "claude")
    assert hold["until"] <= H.now() and hold["cut"]
    turn = claim(api, m)
    assert turn and signed_with(api, turn) == plan_a
    with api.app.state.store.read() as c:  # the one probe turn tests the new value
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
    assert "claude" not in get(api, "bots/ops/subscriptions")["subscriptions"]
    turn = claim(api, m)
    assert turn and turn["bot"] == "ops" and signed_with(api, turn) is None


def test_a_switch_re_judges_a_bot_whose_named_fallback_runs_the_runtime_and_keeps_it_held_until_the_fallback_can_run(
    api,
):
    # The bot's primary is Codex, held on its own login; its named fallback is Claude. Switching Claude's plan re-judges
    # the bot (it runs Claude as its fallback), and it is freed only when that fallback can take the turn.
    setup(api)
    company(api)
    on(api, "ops", "codex", "gpt-6.2-sol")
    with api.app.state.store.transaction() as c:
        config = BS.read(c, 'ops')
        config["fallback"] = {"harness": "claude", "model": "claude-sonnet-5-5"}
        BS.write(c, 'ops', config)
    plan_a, plan_b = add(api, name="Plan A")["id"], add(api, secret=OTHER, name="Plan B")["id"]
    m = machine(api, signed_in=True)  # Claude is ready on its own login
    post(
        api,
        "runners/heartbeat",
        {
            "version": "test",
            "platform": "test",
            "capabilities": ["model_subscriptions_v1", agent_subscriptions.CAPABILITY],
            "readiness": {
                "schema_version": 1,
                "runtimes": {
                    "claude": {"installed": True, "authenticated": "ready"},
                    "codex": {"installed": True, "authenticated": "ready"},
                },
                "bots": {"ops": {"runtime": "codex", "ready": True}},
            },
        },
        m["token"],
    )
    pick(api, "ops", "claude", plan_a)
    post(api, "chat/ops", {"text": "Summarize the Acme account."})
    hold_own_login(api, m, runtime="codex")  # the primary is held
    limit(api)
    # Plan B is held and the computer's own Claude login is held too: the fallback cannot take the turn, so it stays.
    hold_plan(api, plan_b)
    hold_own_login(api, m)
    pick(api, "ops", "claude", plan_b)
    assert status(api)["state"] == "limited" and waits(api) == ["cooling_off"]
    # Plan B taken off and the Claude login free: the fallback runs the turn on the computer's own login, so it is freed.
    with api.app.state.store.transaction() as c:
        login_holds.clear(c, m["runner_id"], "", "claude")
    pick(api, "ops", "claude", None)
    assert waits(api) == [None]


def test_a_held_own_login_keeps_the_bot_limited_and_its_job_waits_for_that_login_not_the_replaced_plan(api):
    from backend.execution import own_login_hold

    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    plan_a = add(api, name="Plan A")["id"]
    m = machine(api)
    limited_on(api, m, plan_a)
    plan_end, own_end = "2999-01-01T20:00:00.000000Z", "2999-01-01T19:00:00.000000Z"
    hold_plan(api, plan_a, until=plan_end)
    hold_own_login(api, m, until=own_end)
    pick(api, "ops", "claude", None)  # plan A taken off; the computer's own login is held until 19:00
    # The job waits on the login it runs on now, through that login's hold.
    assert status(api)["state"] == "limited" and waits(api) == ["cooling_off"]
    with api.app.state.store.read() as c:
        assert own_login_hold(c, api.app.state.store.settings, "ops")["until"] == own_end


def test_a_branch_runs_its_originals_runtime_now_not_the_runtime_it_stored(api):
    # The branch's stored config still says Claude, but it follows its original, which runs Codex now: a Claude plan
    # switch leaves it alone.
    setup(api)
    company(api)
    on(api, "cpo", "codex", "gpt-6.2-sol")
    on(api, "ops", "claude", "claude-sonnet-5-5")
    plan_a, plan_b = add(api, name="Plan A")["id"], add(api, secret=OTHER, name="Plan B")["id"]
    m = machine(api, signed_in=True)
    assign(api, m, "cpo")
    with api.app.state.store.transaction() as c:  # made a branch of cpo once it is on this computer
        config = BS.read(c, 'ops')
        BS.write(c, 'ops', {**config, 'shared_from': 'cpo'})
    pick(api, "ops", "claude", plan_a)
    post(api, "chat/ops", {"text": "Summarize the Acme account."})
    limit(api)
    pick(api, "ops", "claude", plan_b)
    assert status(api)["state"] == "limited"


def test_try_now_on_an_idle_branch_ends_the_hold_on_the_login_its_original_runs_on(api):
    # The branch's stored config says Claude, but it follows its original, which runs Codex: Try now ends the hold on
    # the Codex login the branch signs in with, not one on Claude.
    from backend.execution import clear_limits

    setup(api)
    company(api)
    on(api, "cpo", "codex", "gpt-6.2-sol")
    on(api, "ops", "claude", "claude-sonnet-5-5")
    m = machine(api, signed_in=True)
    assign(api, m, "cpo")
    with api.app.state.store.transaction() as c:  # made a branch of cpo once it is on this computer
        config = BS.read(c, 'ops')
        BS.write(c, 'ops', {**config, 'shared_from': 'cpo'})
        login_holds._save(
            c,
            {
                "runner_id": m["runner_id"],
                "profile": "",
                "runtime": "codex",
                "until": "2999-01-01T00:00:00.000000Z",
                "since": H.now(),
                "error": CLAUDE_LIMIT,
            },
        )
    with api.app.state.store.transaction() as c:
        assert clear_limits(c, api.app.state.store.settings, H.TICO_ACTOR, "ops") == 1
    with api.app.state.store.read() as c:
        assert login_holds.get(c, m["runner_id"], "", "codex")["until"] <= H.now()


@pytest.mark.parametrize(
    "mode",
    [
        "automatic",
        "disabled",
        "bot_off",
        "held",
        "rejected",
        "named",
        "same_runtime",
        "absent",
        "uninstalled",
        "incomplete_named",
    ],
)
def test_a_codex_pick_is_never_a_fallback_credential_for_a_claude_turn(api, mode):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    sid = add(api, "codex_login", codex_file(), name="ChatGPT")["id"]
    m = machine(api, signed_in=True)
    pick(api, "ops", "codex", sid)
    with api.app.state.store.transaction() as c:
        config = BS.read(c, "ops")
        if mode == "disabled":
            record = json.loads(c.execute("SELECT value FROM registry_metadata WHERE key='providers'").fetchone()[0])
            c.execute(
                "UPDATE registry_metadata SET value=? WHERE key='providers'",
                (encode({**record, "auto_fallback": False}),),
            )
        if mode == "bot_off":
            config["fallback"] = {"off": True}
        if mode in ("named", "same_runtime", "incomplete_named"):
            config["fallback"] = {"harness": "codex" if mode == "named" else "claude"}
            if mode != "incomplete_named":
                config["fallback"]["model"] = "gpt-6.2-sol" if mode == "named" else "claude-sonnet-5-5"
        BS.write(c, "ops", config)
        if mode in ("absent", "uninstalled"):
            ready = H._json(
                c.execute("SELECT readiness FROM runner_states WHERE runner_id=?", (m["runner_id"],)).fetchone()[0]
            )
            if mode == "absent":
                ready["runtimes"].pop("codex")
            else:
                ready["runtimes"]["codex"]["installed"] = False
            c.execute("UPDATE runner_states SET readiness=? WHERE runner_id=?", (encode(ready), m["runner_id"]))
        if mode == "held":
            login_holds.record(
                c, "", login_holds.subscription_login(sid), "codex", H.now(), reset="2099-01-01T00:00:00Z"
            )
        if mode == "rejected":
            MS._status(c, sid, "needs_reconnect", "Synthetic refusal")
    post(api, "chat/ops", {"text": "Summarize the Acme account."})
    turn = claim(api, m)
    handed = turn.get("attempt_credential") or {}
    assert "id" not in handed
    if mode in ("held", "rejected"):
        assert "shell" not in handed and not turn.get("subscriptions")
    else:
        assert handed["shell"]["id"] == sid
        assert [(sub["id"], sub.get("activate")) for sub in turn["subscriptions"]] == [(sid, None)]
    from backend.execution import fallback_choice

    with api.app.state.store.read() as c:
        assert fallback_choice(c, api.app.state.store.settings, m["runner_id"], "ops", turn["config"]) is None
    assert not get(api, "credential-runtime?subscription_runtime=codex", turn["token"]).get("subscriptions")


def test_an_unset_agent_uses_gateway_without_borrowing_a_saved_codex_plan(api, monkeypatch):
    from backend import models_preset
    from backend.tests.helpers.gateway_connection import KEY
    from backend.tests.helpers.models_preset import choose

    setup(api)
    monkeypatch.setenv("TICO_GATEWAY_KEY", KEY)
    choose(api, models_preset.RECOMMENDED)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    m = machine(api)
    add(api, "codex_login", codex_file(), name="ChatGPT")
    with api.app.state.store.transaction() as c:
        ready = H._json(
            c.execute("SELECT readiness FROM runner_states WHERE runner_id=?", (m["runner_id"],)).fetchone()[0]
        )
        ready["isolated"] = True
        ready["bots"]["ops"].update(repository_present=True, configuration_valid=True)
        ready["runtimes"]["pi"] = {"installed": True, "authenticated": "missing"}
        c.execute("UPDATE runner_states SET readiness=? WHERE runner_id=?", (encode(ready), m["runner_id"]))
    post(api, "chat/ops", {"text": "Summarize the Acme account."})
    turn = claim(api, m)
    assert turn["gateway"] and turn["config"]["waiting_for_sign_in"] == "claude"
    assert not turn.get("attempt_credential") and not turn.get("subscriptions")


def test_changing_a_codex_pick_preserves_claude_sessions(api):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    m = machine(api, signed_in=True)
    message = post(api, "chat/ops", {"text": "Summarize the Acme account."})
    turn = claim(api, m)
    post(api, f"attempts/{turn['id']}/started", {"thread_id": "t-claude"}, m["token"])
    post(api, f"attempts/{turn['id']}/complete", {"outcome": "completed", "last_seq": 0, "text": "Done."}, m["token"])

    def epoch():
        with api.app.state.store.read() as c:
            found = c.execute(
                "SELECT epoch FROM session_epochs WHERE conversation_id=?", (message["conversation_id"],)
            ).fetchone()
        return found[0] if found else 0

    before = epoch()
    sid = add(api, "codex_login", codex_file(), name="ChatGPT")["id"]
    pick(api, "ops", "codex", sid)
    assert post(api, f"model-subscriptions/{sid}", {"secret": codex_file(refresh="r-2")})["revision"] == 2
    pick(api, "ops", "codex")
    assert api.delete(f"/api/v2/model-subscriptions/{sid}?confirm=1", headers=headers()).status_code == 200
    assert epoch() == before


@pytest.mark.parametrize("change", ["clear_pick", "reconnect", "remove"])
@pytest.mark.parametrize("using_key", [False, True])
def test_changing_a_codex_key_only_stops_live_turns_using_the_removed_or_replaced_key(api, change, using_key):
    setup(api)
    company(api)
    runtime, model = ("codex", "gpt-6.2-sol") if using_key else ("claude", "claude-sonnet-5-5")
    on(api, "ops", runtime, model)
    m = machine(api, signed_in=True)
    sid = add(api, "openai_key", "sk-" + "c" * 24, name="OpenAI")["id"]
    pick(api, "ops", "codex", sid, kind="key")
    post(api, "chat/ops", {"text": "Summarize the account."})
    turn = claim(api, m)
    assert signed_with(api, turn) == (sid if using_key else None)
    post(api, f"attempts/{turn['id']}/started", {"thread_id": "t-run"}, m["token"])
    needs_confirm = using_key and change != "clear_pick"
    if change == "clear_pick":
        pick(api, "ops", "codex")
    else:

        def update(confirm):
            if change == "remove":
                return api.delete(f"/api/v2/model-subscriptions/{sid}", headers=headers(), params={"confirm": confirm})
            return api.post(
                f"/api/v2/model-subscriptions/{sid}",
                headers=headers(),
                json={"secret": "sk-" + "d" * 24, "confirm": confirm},
            )

        response = update(False)
        if needs_confirm or change == "remove":
            assert response.status_code == 409 and response.json()["error"]["running"] == int(needs_confirm)
            response = update(True)
        assert response.status_code == 200, response.text
    assert bool(post(api, f"attempts/{turn['id']}/renew", {}, m["token"]).get("stop")) is needs_confirm


@pytest.mark.parametrize("blocked", ["held", "rejected"])
def test_a_paid_by_provider_is_rechecked_before_the_turn_starts(api, blocked):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    m = machine(api, signed_in=True)
    sid = add(api)["id"]
    pick(api, "ops", "claude", sid)
    post(api, "chat/ops", {"text": "Summarize the account."})
    turn = claim(api, m)
    if blocked == "held":
        hold_plan(api, sid)
    else:
        with api.app.state.store.transaction() as c:
            MS._status(c, sid, "needs_reconnect", "Synthetic refusal")
    response = api.post(
        f"/api/v2/attempts/{turn['id']}/started", headers=headers(m["token"]), json={"thread_id": "t-acme"}
    )
    assert response.status_code == 409 and response.json()["error"]["code"] == "payer_wait"
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM jobs WHERE id=?", (turn["job_id"],)).fetchone()[0] == "queued"


def test_replacing_the_bound_provider_stops_the_leased_turn(api):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    m = machine(api)
    sid = add(api)["id"]
    pick(api, "ops", "claude", sid)
    post(api, "chat/ops", {"text": "Summarize the account."})
    turn = claim(api, m)
    assert signed_with(api, turn) == sid
    assert post(api, f"model-subscriptions/{sid}", {"secret": OTHER}, expected=409)["error"]["running"] == 1
    post(api, f"model-subscriptions/{sid}", {"secret": OTHER, "confirm": True})
    assert post(api, f"attempts/{turn['id']}/renew", {}, m["token"])["stop"]


def roster(api, pid, **fields):
    """Mark a person on the roster: hidden (left) or sign_in False (switched off)."""
    with api.app.state.store.transaction() as c:
        people = json.loads(c.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()[0])
        next(p for p in people["people"] if p["id"] == pid).update(fields)
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (encode(people),))


def test_members_manage_only_their_own_plans_and_ownership_cannot_transfer(api):
    setup(api)
    company(api)
    mine = add(api, token="cara-test")
    assert mine["owner_id"] == "human:cara" and mine["owner_name"] == mine["added_by_name"] != ""
    assert add(api, token="cara-test")["id"] != mine["id"]
    plan = f"model-subscriptions/{mine['id']}"
    for owner in ("ana", "human:eve", "bot:ops", "tico-support", "", "service:x"):
        refused = post(
            api,
            "model-subscriptions",
            {"kind": "claude_token", "secret": TOKEN, "owner_id": owner},
            "cara-test",
            expected=422,
        )
        assert refused["error"]["code"] == "owner"
    post(api, plan, {"owner_id": "ben"}, "ben-test", expected=422)
    assert post(api, plan, {"name": "Max"}, "cara-test")["name"] == "Max"
    assert post(api, plan, {"secret": OTHER}, "cara-test")["revision"] == 2
    assert post(api, plan, {"name": "Admin rename"}, "ben-test")["owner_id"] == "human:cara"

    owner_plan = add(api)
    foreign = f"model-subscriptions/{owner_plan['id']}"
    post(api, foreign, {"name": "Rename"}, "cara-test", expected=403)
    post(api, foreign, {"secret": OTHER}, "cara-test", expected=403)
    assert api.delete(f"/api/v2/{foreign}", headers=headers("cara-test")).status_code == 403
    assert api.delete(f"/api/v2/{plan}", headers=headers("cara-test")).json()["deleted"]
    assert api.delete(f"/api/v2/{foreign}", headers=headers("ben-test")).json()["deleted"]


def test_members_save_and_manage_their_own_api_keys(api):
    setup(api)
    company(api, enabled=["openai"])
    keys = [
        add(api, "anthropic_key", "sk-ant-api03-" + str(n) * 20, name=f"Key {n}", token="cara-test") for n in range(3)
    ]
    assert {k["owner_id"] for k in keys} == {"human:cara"}
    assert len({k["id"] for k in keys}) == 3
    with api.app.state.store.read() as c:
        from backend import providers

        assert "anthropic" not in providers.load(c, api.app.state.store.settings)["enabled"]
    refused = post(
        api,
        "model-subscriptions",
        {"kind": "openai_key", "secret": "sk-" + "c" * 24, "name": "OpenAI", "owner_id": "human:ana"},
        "cara-test",
        expected=422,
    )
    assert refused["error"]["code"] == "owner"
    shared = post(
        api,
        "model-subscriptions",
        {"kind": "openai_key", "secret": "sk-" + "d" * 24, "owner_id": "cara"},
        "ben-test",
    )
    assert shared["owner_id"] == "human:cara" and shared["added_by"] == "human:ben"
    key = f"model-subscriptions/{keys[0]['id']}"
    post(api, key, {"owner_id": "human:ana"}, expected=422)
    post(api, key, {"name": "Rotated", "secret": "sk-ant-replacement-0000000000"}, "cara-test")
    post(api, key, {"name": "Admin rename"}, "ben-test")
    api.app.state.store.settings.test_identities["delegated"] = Identity(
        "human:cara", "human", email="cara@acme.example", via="assistant"
    )
    post(api, key, {"secret": "sk-ant-again-0000000000"}, "delegated", expected=403)
    assert api.delete(f"/api/v2/{key}", headers=headers("cara-test")).json()["deleted"]


def usage_of(api, sid):
    return next(s for s in listed(api) if s["id"] == sid)["usage"]


def window_reading(used, resets_in, reported_ago=timedelta(0), origin=None, subscription_id=None):
    """A usage window as a computer reports it: `used` percent (None when the plan sends none), resetting `resets_in`
    after the reading, which was taken `reported_ago` ago, and where it came from: `origin` "subscription" for a turn that
    signed in with plan `subscription_id`, "login" for the computer's own login, None for a runner that sends neither."""
    reported = datetime.now(timezone.utc) - reported_ago
    return {
        "used_percent": used,
        "resets_at": (reported + resets_in).isoformat(),
        "reported_at": reported.isoformat(),
        "origin": origin,
        "subscription_id": subscription_id,
    }


def turn_window(used, resets_in, sid, reported_ago=timedelta(0)):
    """A usage window a turn reports after it signed in with plan `sid`."""
    return window_reading(used, resets_in, reported_ago, origin="subscription", subscription_id=sid)


def login_window(used, resets_in, reported_ago=timedelta(0)):
    """A usage window the computer's own login reports: a background read, or a turn that ran on that login."""
    return window_reading(used, resets_in, reported_ago, origin="login")


def beat(api, m, profiles, *, logins=True, signed_in=True, runtime="claude"):
    """A heartbeat: each login profile's runtimes as the computer reports them. `logins` names its default login (a
    computer that does not names none)."""
    readiness = {
        "schema_version": 1,
        "bots": {"ops": {"runtime": runtime, "ready": True}},
        "runtimes": {runtime: {"installed": True, "authenticated": "ready" if signed_in else "missing"}},
    }
    if logins:
        readiness["computer_profile"] = "own"
    capabilities = ["model_subscriptions_v1", agent_subscriptions.CAPABILITY] + (
        ["computer_logins_v1"] if logins else []
    )
    return post(
        api,
        "runners/heartbeat",
        {
            "version": "test",
            "platform": "test",
            "capabilities": capabilities,
            "readiness": readiness,
            "profiles": [{"name": name, "runtimes": runtimes} for name, runtimes in profiles.items()],
        },
        token=m["token"],
    )


NOT_SINCE = "No usage reported since this subscription was added"


def runtime_state(account=None, **windows):
    """A runtime as a computer's readiness reports it on its ordinary home: authenticated (as `account`'s email when
    given), with its windows (`weekly`, `five_hour`)."""
    state = {"installed": True, "authenticated": "ready", **windows}
    return {**state, "account": {"email": account}} if account else state


def home_beat(api, m, **runtimes):
    """A heartbeat from a computer on its ordinary home: readiness names no profile ("", so no profile row) and carries
    each runtime's windows itself."""
    return post(
        api,
        "runners/heartbeat",
        {
            "version": "test",
            "platform": "test",
            "capabilities": ["model_subscriptions_v1", "computer_logins_v1", agent_subscriptions.CAPABILITY],
            "readiness": {
                "schema_version": 1,
                "bots": {"ops": {"runtime": next(iter(runtimes), "claude"), "ready": True}},
                "computer_profile": "",
                "runtimes": runtimes,
            },
        },
        token=m["token"],
    )


def usage_machine(api, label="Plan Mac"):
    """A computer that names its own login, takes plans and has the bot ops on it, so a turn can run there."""
    m = runner(api, label=label)
    assign(api, m, "ops")
    beat(api, m, {})
    return m


def turn_on(api, m, sid, runtime="claude", bot="ops"):
    """A turn of `bot` on computer `m` that signs in with plan `sid` (given to `m` for `runtime` first), still running:
    the readings it reports while it runs are this plan's (their origin, model_subscriptions.provenance)."""
    on(api, bot, runtime, "gpt-6.2-sol" if runtime == "codex" else "claude-sonnet-5-5")
    pick(api, bot, runtime, sid)
    post(api, "chat/" + bot, {"text": "Summarize the Acme account."})
    turn = claim(api, m)
    assert turn and signed_with(api, turn) == sid
    return turn


def test_a_plan_says_why_it_shows_no_usage_yet(api):
    setup(api)
    company(api)
    sid = add(api)["id"]
    assert usage_of(api, sid) == NO_COMPUTER
    # A computer that does not report its own login's windows (no computer_logins_v1) is only given the plan.
    machine(api, label="Old Mac")
    pick(api, "ops", "claude", sid)
    assert usage_of(api, sid)["reason"] == "Update the computer to see usage"
    m = runner(api, label="Plan Mac")
    assign(api, m, "ops", generation=1)
    beat(api, m, {})
    assert usage_of(api, sid)["reason"] == "Waiting for a computer to report"
    with api.app.state.store.transaction() as c:
        c.execute(
            "UPDATE runner_states SET last_seen_at=? WHERE runner_id=?", ("2000-01-01T00:00:00.000000Z", m["runner_id"])
        )
    assert usage_of(api, sid)["reason"] == "Its computers are offline"
    beat(api, m, {})
    with api.app.state.store.transaction() as c:
        MS._status(c, sid, "needs_reconnect", "Token expired")
    assert usage_of(api, sid)["reason"] == "Reconnect to see usage"


def test_a_plan_shows_the_most_used_window_its_computer_reads_even_at_zero(api):
    setup(api)
    company(api)
    sid = add(api)["id"]
    m = usage_machine(api)
    turn_on(api, m, sid)
    beat(api, m, {"own": {"claude": {"signed_in": True, "weekly": turn_window(0, timedelta(days=3), sid)}}})
    usage = usage_of(api, sid)
    assert (usage["window"], usage["used_pct"]) == ("week", 0) and "reason" not in usage
    # Its 5-hour window is the most used now, then its week again.
    beat(api, m, {"own": {"claude": {"signed_in": True, "five_hour": turn_window(40, timedelta(hours=2), sid)}}})
    assert (usage_of(api, sid)["window"], usage_of(api, sid)["used_pct"]) == ("5h", 40)
    beat(api, m, {"own": {"claude": {"signed_in": True, "weekly": turn_window(70, timedelta(days=3), sid)}}})
    assert (usage_of(api, sid)["window"], usage_of(api, sid)["used_pct"]) == ("week", 70)
    # Another login on the computer is not this plan's, however used it is: its 99% is not read; the open 40% stands.
    beat(
        api,
        m,
        {
            "own": {"claude": {"signed_in": True, "weekly": turn_window(10, timedelta(days=3), sid)}},
            "work": {"claude": {"signed_in": True, "weekly": login_window(99, timedelta(days=3))}},
        },
    )
    assert (usage_of(api, sid)["window"], usage_of(api, sid)["used_pct"]) == ("5h", 40)
    # A limit met on the plan holds it at 100, whatever its computers last read.
    hold_plan(api, sid)
    assert usage_of(api, sid)["used_pct"] == 100


def test_a_window_that_has_reset_or_sends_no_percent_is_not_a_reading(api):
    setup(api)
    company(api)
    sid = add(api)["id"]
    m = usage_machine(api)
    beat(
        api,
        m,
        {
            "own": {
                "claude": {
                    "signed_in": True,
                    "weekly": login_window(55, timedelta(days=1), reported_ago=timedelta(days=2)),
                }
            }
        },
    )
    turn_on(api, m, sid)
    # Its week reset a day ago, so the 55% read before it is no longer this plan's usage.
    assert usage_of(api, sid)["reason"] == "Waiting for a computer to report"
    beat(api, m, {"own": {"claude": {"signed_in": True, "weekly": turn_window(None, timedelta(days=3), sid)}}})
    assert usage_of(api, sid)["reason"] == "No percentage from the plan"


def test_only_a_computer_that_names_its_default_login_and_takes_plans_reads_usage(api):
    setup(api)
    company(api)
    sid = add(api)["id"]
    m = runner(api, label="Plan Mac")
    assign(api, m, "ops")
    beat(api, m, {}, logins=False)
    pick(api, "ops", "claude", sid)
    weekly = {"own": {"claude": {"signed_in": True, "weekly": login_window(30, timedelta(days=3))}}}
    beat(api, m, weekly, logins=False)
    assert usage_of(api, sid)["reason"] == "Update the computer to see usage"
    beat(api, m, {})
    turn_on(api, m, sid)
    beat(api, m, {"own": {"claude": {"signed_in": True, "weekly": turn_window(31, timedelta(days=3), sid)}}})
    assert usage_of(api, sid)["used_pct"] == 31


def test_a_reading_from_before_a_plan_was_given_to_the_computer_is_not_its_usage(api):
    setup(api)
    company(api)
    sid = add(api)["id"]
    m = runner(api, label="Plan Mac")
    assign(api, m, "ops")
    # The computer's own login was at 88% before the plan reached it: that is not this plan's usage.
    home_beat(api, m, claude=runtime_state(weekly=login_window(88, timedelta(days=3))))
    pick(api, "ops", "claude", sid)
    assert usage_of(api, sid)["reason"] == NOT_SINCE and usage_of(api, sid)["used_pct"] is None
    turn_on(api, m, sid)
    home_beat(api, m, claude=runtime_state(weekly=turn_window(45, timedelta(days=3), sid)))
    assert usage_of(api, sid)["used_pct"] == 45


def test_a_rename_keeps_the_readings_taken_for_a_plan_and_a_new_secret_ends_them(api):
    setup(api)
    company(api)
    sid = add(api)["id"]
    m = usage_machine(api)
    turn_on(api, m, sid)
    beat(api, m, {"own": {"claude": {"signed_in": True, "weekly": turn_window(30, timedelta(days=3), sid)}}})
    assert usage_of(api, sid)["used_pct"] == 30
    post(api, f"model-subscriptions/{sid}", {"name": "Renamed plan"})  # the same credential: its readings stand
    assert usage_of(api, sid)["used_pct"] == 30
    post(api, f"model-subscriptions/{sid}", {"secret": OTHER, "confirm": True})  # another account: they are not its
    assert usage_of(api, sid)["reason"] == NOT_SINCE


def test_a_same_accounts_refresh_keeps_the_readings_taken_for_a_chatgpt_plan(api):
    setup(api)
    company(api)
    on(api, "ops", "codex", "gpt-6.2-sol")
    sid = add(api, "codex_login", codex_file(), name="ChatGPT")["id"]
    m = runner(api, label="Plan Mac")
    assign(api, m, "ops")
    home_beat(api, m, codex=runtime_state(account="ana@acme.example"))
    turn = turn_on(api, m, sid, runtime="codex")
    home_beat(api, m, codex=runtime_state(account="ana@acme.example", weekly=turn_window(64, timedelta(days=3), sid)))
    assert usage_of(api, sid)["used_pct"] == 64
    # The run refreshes its file for the same account: the stored credential moves on, its readings stand.
    body = {"attempt": turn["id"], "subscription": sid, "revision": 1}
    assert post(api, "runner-model-subscription-refresh", {**body, "value": codex_file(refresh="r-2")}, m["token"]) == {
        "stored": True,
        "revision": 2,
    }
    assert usage_of(api, sid)["used_pct"] == 64


def test_a_chatgpt_login_reading_on_an_ordinary_home_counts_only_for_the_account_the_plan_is_signed_in_as(api):
    setup(api)
    company(api)
    on(api, "ops", "codex", "gpt-6.2-sol")
    sid = add(api, "codex_login", codex_file(), name="ChatGPT")["id"]
    m = runner(api, label="Plan Mac")
    assign(api, m, "ops")
    home_beat(api, m, codex=runtime_state(account="ana@acme.example"))
    turn_on(api, m, sid, runtime="codex")
    # The computer's own login is the plan's account, so its background read is the plan's usage.
    home_beat(api, m, codex=runtime_state(account="ana@acme.example", weekly=login_window(64, timedelta(days=3))))
    assert usage_of(api, sid)["used_pct"] == 64
    # The same computer signed in as someone else: that account's usage is not this plan's.
    home_beat(api, m, codex=runtime_state(account="someone@else.example", weekly=login_window(99, timedelta(days=3))))
    assert usage_of(api, sid)["reason"] == NOT_SINCE
    # A reading that names no account cannot be attributed to the plan either.
    home_beat(api, m, codex=runtime_state(weekly=login_window(50, timedelta(days=3))))
    assert usage_of(api, sid)["reason"] == NOT_SINCE


def test_a_background_login_read_during_a_subscription_turn_is_not_that_plans_usage_without_an_account(api):
    # A ChatGPT login whose id_token names no email: nothing says whose login the plan is, so the computer's own background
    # read taken while the plan's turn runs is no reading of the plan. Only the turn's own reading (its origin and id) is.
    setup(api)
    company(api)
    on(api, "ops", "codex", "gpt-6.2-sol")
    sid = add(api, "codex_login", codex_file(email=""), name="ChatGPT")["id"]
    m = runner(api, label="Plan Mac")
    assign(api, m, "ops")
    home_beat(api, m, codex=runtime_state(account="ana@acme.example"))
    turn_on(api, m, sid, runtime="codex")
    home_beat(api, m, codex=runtime_state(account="ana@acme.example", weekly=login_window(30, timedelta(days=3))))
    assert usage_of(api, sid)["reason"] == NOT_SINCE and usage_of(api, sid)["used_pct"] is None
    home_beat(api, m, codex=runtime_state(account="ana@acme.example", weekly=turn_window(45, timedelta(days=3), sid)))
    assert usage_of(api, sid)["used_pct"] == 45
    # A reading that carries no origin (a runner that predates it) is no plan's either.
    home_beat(api, m, codex=runtime_state(weekly=window_reading(60, timedelta(days=3))))
    assert usage_of(api, sid)["used_pct"] == 45


def test_a_computer_on_its_ordinary_home_shows_the_plan_its_turn_ran_on_not_its_own_login(api):
    setup(api)
    company(api)
    sid = add(api)["id"]
    m = runner(api, label="Plan Mac")
    assign(api, m, "ops")
    home_beat(api, m, claude=runtime_state())
    turn_on(api, m, sid)
    home_beat(
        api,
        m,
        claude=runtime_state(
            five_hour=turn_window(40, timedelta(hours=2), sid), weekly=turn_window(70, timedelta(days=3), sid)
        ),
    )
    usage = usage_of(api, sid)
    assert (usage["window"], usage["used_pct"]) == ("week", 70)


def test_a_reading_taken_on_the_computers_own_login_is_not_the_plans_when_its_hold_ends(api):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    sid = add(api)["id"]
    m = usage_machine(api)
    pick(api, "ops", "claude", sid)
    hold_plan(api, sid)
    post(api, "chat/ops", {"text": "Summarize the Acme account."})
    assert claim(api, m) is None
    beat(api, m, {"own": {"claude": {"signed_in": True, "weekly": login_window(88, timedelta(days=3))}}})
    assert usage_of(api, sid)["used_pct"] == 100  # a limit met on the plan holds it at 100
    hold_plan(api, sid, until=H.shift(H.now(), seconds=-1))  # the hold ends: the 88% was not the plan's
    assert usage_of(api, sid)["reason"] == NOT_SINCE


def listing_statements(api, monkeypatch):
    """GET /api/v2/model-subscriptions after one warm-up read, and the SQL statements that request ran."""
    import contextlib
    from backend.tests.helpers.statement_scope import measuring, recorder

    store, seen = api.app.state.store, []
    get(api, "model-subscriptions")
    with monkeypatch.context() as patched:
        for name in ("read", "read_transaction"):

            def traced(opened=getattr(store, name)):
                @contextlib.contextmanager
                def borrowed():
                    with opened() as c:
                        c.set_trace_callback(recorder(seen))
                        try:
                            yield c
                        finally:
                            c.set_trace_callback(None)

                return borrowed()

            patched.setattr(store, name, traced)
        with measuring(seen):
            get(api, "model-subscriptions")
    return seen


def join_home(api, label, claude, codex, used):
    m = runner(api, label=label)
    home_beat(
        api,
        m,
        claude=runtime_state(weekly=window_reading(used, timedelta(days=3))),
        codex=runtime_state(account="ana@acme.example"),
    )
    bot = "usage-" + label.lower().replace(" ", "-")
    post(api, "bots", {"slug": bot, "display_name": label, "status": "active", "runner_id": m["runner_id"]})
    pick(api, bot, "claude", claude)
    pick(api, bot, "codex", codex)


def test_the_plan_list_runs_the_same_queries_however_many_computers_it_has(api, monkeypatch):
    setup(api)
    company(api)
    claude = add(api)["id"]
    codex = add(api, "codex_login", codex_file(), name="ChatGPT")["id"]
    for i in range(3):
        join_home(api, f"Mac {i}", claude, codex, 10 + i)
    few = listing_statements(api, monkeypatch)
    for i in range(3, 12):
        join_home(api, f"Mac {i}", claude, codex, 10 + i)
    many = listing_statements(api, monkeypatch)
    assert any("FROM agent_subscriptions" in sql for sql in many)
    assert len(many) == len(few)
    assert [len(row["computers"]) for row in listed(api)] == [12, 12]


def test_a_plan_list_runs_the_month_cost_query_only_when_an_api_key_is_saved(api, monkeypatch):
    setup(api)
    company(api)
    add(api)
    assert not any("est_cost_usd" in sql for sql in listing_statements(api, monkeypatch))
    add(api, "anthropic_key", secret="sk-ant-api-" + "c" * 24, name="Company API")
    assert any("est_cost_usd" in sql for sql in listing_statements(api, monkeypatch))


def test_a_persons_plans_list_in_use_first_across_tools(api):
    """Settings lists a person's plans together: the one a computer is using first, whichever tool it is, then the rest
    by their soonest weekly reset (`place`). Each tool's `order` stays its own pick order."""
    setup(api)
    company(api)
    on(api, "ops", "codex", "gpt-6.2-sol")
    claude = add(api)["id"]
    chatgpt = add(api, "codex_login", codex_file(), name="ChatGPT")["id"]
    m = machine(api)
    turn_on(api, m, chatgpt, runtime="codex")
    by = {s["id"]: s for s in listed(api)}
    assert by[chatgpt]["in_use_on"] and by[chatgpt]["place"] == 1
    assert not by[claude]["in_use_on"] and by[claude]["place"] == 2
    assert by[chatgpt]["order"] == by[claude]["order"] == 1


def test_the_list_distinguishes_readings_from_holds_and_pins_from_rotation(api):
    setup(api)
    company(api)
    current = add(api, name="Plan A")["id"]
    spare = add(api, secret=OTHER, name="Plan B")["id"]
    m = usage_machine(api)
    turn_on(api, m, current)
    beat(api, m, {"own": {"claude": {"signed_in": True, "weekly": turn_window(100, timedelta(days=3), current)}}})

    def rows():
        return {s["id"]: s for s in listed(api)}

    before = rows()
    assert before[current]["usage"]["used_pct"] == 100
    assert "quota_hold_until" not in before[current]["usage"]
    assert before[current]["available"] and before[current]["in_use_on"] == [m["runner_id"]]
    assert not before[spare]["next_in_rotation"]

    pick(api, "ops", "claude", person="ana")
    rotating = rows()
    assert rotating[current]["in_use_on"] == [m["runner_id"]]
    assert rotating[spare]["next_in_rotation"]

    hold_plan(api, spare)
    held = rows()
    assert held[spare]["usage"]["quota_hold_until"] == held[spare]["usage"]["resets_at"]
    assert not held[spare]["available"] and not held[spare]["in_use_on"] and not held[spare]["next_in_rotation"]


def test_capacity_holds_preserve_usage_and_report_when_the_provider_is_available_again(api, monkeypatch):
    setup(api)
    company(api)
    plan = add(api)["id"]
    key = add(api, "anthropic_key", secret="sk-ant-api-" + "c" * 24, name="Company API")["id"]
    before = {s["id"]: s["usage"] for s in listed(api)}
    with api.app.state.store.transaction() as c:
        hit = H.now()
        for sid in (plan, key):
            login_holds.record(c, "", login_holds.subscription_login(sid), "claude", hit, error="overloaded")

    until = H.shift(hit, seconds=login_holds.CAPACITY_WAIT)
    held = {s["id"]: s for s in listed(api)}
    for sid in (plan, key):
        assert not held[sid]["available"]
        assert held[sid]["usage"] == {**before[sid], "capacity_until": until}

    monkeypatch.setattr(H, "now", lambda: until)
    released = {s["id"]: s for s in listed(api)}
    for sid in (plan, key):
        assert released[sid]["available"]
        assert released[sid]["usage"] == before[sid]


def test_each_subscription_lists_only_visible_agents_that_pick_it_even_when_broken(api):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    on(api, "cpo", "codex", "gpt-6.2-sol")
    m = machine(api)
    assign(api, m, "cpo")
    plan = add(api, name="Plan A", token="cara-test")["id"]
    spare = add(api, secret=OTHER, name="Plan B", token="cara-test")["id"]
    chatgpt = add(api, "codex_login", codex_file(), name="ChatGPT")["id"]
    pick(api, "ops", "claude", plan)
    pick(api, "cpo", "codex", chatgpt)
    with api.app.state.store.read() as c:
        ops, cpo = H.resolve_bot(c, "ops")["id"], H.resolve_bot(c, "cpo")["id"]

    def agents():  # the company's other bots share the computer; these two stand for them
        return {s["id"]: {a["id"]: a["fallback"] for a in s["agents"] if a["id"] in (ops, cpo)} for s in listed(api)}

    assert agents() == {plan: {ops: False}, spare: {}, chatgpt: {cpo: False}}
    with api.app.state.store.transaction() as c:
        config = BS.read(c, "cpo")
        config["fallback"] = {"harness": "claude", "model": "claude-sonnet-5-5"}
        BS.write(c, "cpo", config)
        MS._status(c, plan, "needs_reconnect", "expired")
        unrelated_task_managers(c, cpo)
        restrict(c, cpo, see={"everyone": True}, read={"people": ["ana"]})
    assert agents()[plan] == {ops: False}
    pick(api, "cpo", "claude", plan)
    assert agents()[plan] == {ops: False, cpo: False}
    member_rows = {s["id"]: s for s in listed(api, "cara-test")}
    assert ops in {a["id"] for a in member_rows[plan]["agents"]}
    assert all(a["id"] != cpo for s in member_rows.values() for a in s["agents"])
    assert member_rows[plan]["removal_agents"] == 1
    pick(api, "ops", "claude", person="cara")
    member_rows = {s["id"]: s for s in listed(api, "cara-test")}
    assert member_rows[plan]["removal_agents"] == member_rows[spare]["removal_agents"] == 0
    assert api.delete(f"/api/v2/model-subscriptions/{spare}", headers=headers("cara-test")).status_code == 200
    assert next(s for s in listed(api, "cara-test") if s["id"] == plan)["removal_agents"] == 1


def test_only_provider_owners_and_admins_see_previews_and_refusal_details(api):
    setup(api)
    company(api)
    email = "cara@acme.example"
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE humans SET name='Casey' WHERE id='cara'")
    plan = post(api, "model-subscriptions", {"kind": "codex_login", "secret": codex_file(email=email)}, "cara-test")
    # Older rows may name someone other than the person who added the plan.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE model_subscriptions SET added_by='human:ben' WHERE id=?", (plan["id"],))
    assert plan["name"] == "Casey's ChatGPT plan" and plan["expired"] is False
    refusal = f"The provider refused the sign-in for {email}"
    with api.app.state.store.transaction() as c:
        MS._status(c, plan["id"], "needs_reconnect", refusal)
    [admin] = listed(api, "ben-test")
    assert admin["preview"] == email and admin["status_detail"] == refusal
    post(api, "access/humans/ben", {"role": "member"})
    [member] = listed(api, "ben-test")
    assert member["preview"] == "" and member["usage"] == plan["usage"] and not member["can_remove"]
    assert member["status"] == "needs_reconnect" and member["status_detail"] == "The provider refused it"
    for token in ("cara-test", "ana-test"):
        [owner] = listed(api, token)
        assert owner["preview"] == email and owner["status_detail"] == refusal
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE humans SET name='' WHERE id='cara'")
        c.execute("UPDATE humans SET name=email WHERE id='ben'")
        c.execute("UPDATE humans SET name=email WHERE id='ana'")
    assert add(api, name=None)["name"] == "Claude plan"
    key = add(api, "anthropic_key", secret="sk-ant-api-" + "c" * 24, name="Company API")
    personal_key = add(api, "anthropic_key", secret="sk-ant-api-" + "d" * 24, token="cara-test")
    with api.app.state.store.transaction() as c:
        MS._status(c, personal_key["id"], "needs_reconnect", refusal)
    for token in ("cara-test", "ana-test"):
        row = next(s for s in listed(api, token) if s["id"] == personal_key["id"])
        assert row["preview"] == personal_key["preview"] and row["status_detail"] == refusal
    for token in ("ben-test", "ana-test"):
        rows = {s["id"]: s for s in listed(api, token)}
        assert rows[plan["id"]]["owner_name"] == rows[plan["id"]]["added_by_name"] == "A teammate"
        assert rows[key["id"]]["preview"] == (key["preview"] if token == "ana-test" else "")
        if token == "ben-test":
            assert email not in json.dumps(rows[plan["id"]])
            assert rows[personal_key["id"]]["preview"] == ""
            assert rows[personal_key["id"]]["status_detail"] == "The provider refused it"


def test_the_migration_renames_legacy_login_emails_with_or_without_an_owner(api):
    migration = import_module("backend.migrations.20261122215020_plan_display_names")
    email = "account@acme.example"
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE humans SET name=NULL WHERE id='ben'")
        c.executemany(
            "INSERT INTO model_subscriptions(id,name,kind,preview,owner_id,added_by,sealed,created_at,updated_at) "
            "VALUES(?,?,?,?,?,'human:ana','', '2026-11-20','2026-11-21')",
            [
                ("a", email, "claude_login", email, "human:ana"),
                ("b", f" {email.upper()} 2 ", "claude_login", email, "human:ana"),
                ("c", email, "codex_login", email, "human:ben"),
                ("custom", email + " team", "codex_login", email, "human:ana"),
                ("taken", " ANA'S CLAUDE PLAN ", "claude_login", email, "human:ana"),
                ("ownerless", email, "codex_login", email, ""),
                ("token", email, "claude_token", email, ""),
            ],
        )
        before = H._rows(c.execute("SELECT * FROM model_subscriptions ORDER BY id"))
        migration.up(c)
        names = {
            "a": "Ana's Claude plan 2",
            "b": "Ana's Claude plan 3",
            "c": "ChatGPT plan",
            "ownerless": "ChatGPT plan 2",
        }
        assert H._rows(c.execute("SELECT * FROM model_subscriptions ORDER BY id")) == [
            row | {"name": names.get(row["id"], row["name"])} for row in before
        ]


def test_the_migration_hides_the_old_email_of_a_reconnected_plan(api):
    setup(api)
    company(api)
    email, new_email = "old@acme.example", "new@acme.example"
    plan = add(api, "codex_login", codex_file(email=email), name=f"{email} 2")
    reconnected = post(api, f"model-subscriptions/{plan['id']}", {"secret": codex_file(email=new_email)})
    assert reconnected["name"] == f"{email} 2" and reconnected["preview"] == new_email

    migration = import_module("backend.migrations.20261122215020_plan_display_names")
    with api.app.state.store.transaction() as c:
        migration.up(c)
    [member] = listed(api, "cara-test")
    assert member["name"] == "Ana's ChatGPT plan" and member["preview"] == ""
    assert listed(api)[0]["preview"] == new_email


def test_long_default_plan_names_fit_the_write_api_even_with_a_three_digit_suffix(api):
    setup(api)
    company(api)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE humans SET name=? WHERE id='ana'", ("🌿" * 120,))
    plan = add(api, "codex_login", codex_file(), name=None)
    base = plan["name"]
    assert base.endswith("'s ChatGPT plan") and len(base) <= 76
    with api.app.state.store.transaction() as c:
        c.executemany(
            "INSERT INTO model_subscriptions(id,name,kind,sealed,added_by,created_at,updated_at) "
            "VALUES(?,?,'openai_key','','tico','2026-11-20','2026-11-20')",
            [(f"key-{n}", f"{base.lower()} {n}") for n in range(2, 100)],
        )
    duplicate = add(api, "codex_login", codex_file(account="acct-2"), name=None)
    assert duplicate["name"] == f"{base} 100"
    assert len(duplicate["name"]) == 80
    edited = post(api, f"model-subscriptions/{duplicate['id']}", {"name": duplicate["name"]})
    assert edited["name"] == duplicate["name"]
    post(api, f"model-subscriptions/{duplicate['id']}", {"name": duplicate["name"] + "🌿"}, expected=422)


def test_the_migration_numbers_a_batch_of_long_names_across_existing_collisions(api):
    migration = import_module("backend.migrations.20261122215020_plan_display_names")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE humans SET name=? WHERE id='ana'", ("A" * 120,))
        base = MS.default_name(c, "codex_login", "human:ana")
        c.executemany(
            "INSERT INTO model_subscriptions(id,name,kind,sealed,owner_id,added_by,created_at,updated_at) "
            "VALUES(?,?,'codex_login','','human:ana','tico','2026-11-20','2026-11-20')",
            [(f"plan-{n:03}", f"account-{n}@acme.example") for n in range(101)] + [("taken", f" {base.upper()} 2 ")],
        )
        migration.up(c)
        names = [row[0] for row in c.execute("SELECT name FROM model_subscriptions WHERE id LIKE 'plan-%' ORDER BY id")]
    assert names == [base] + [f"{base} {n}" for n in range(3, 103)]
    assert max(map(len, names)) <= 80


@pytest.mark.parametrize("provider", ["codex_login", "openai_key"])
def test_only_the_agents_chatgpt_pick_can_supply_a_codex_shell_login(api, provider):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    m = machine(api, signed_in=True)
    plan = provider == "codex_login"
    sid = add(api, provider, codex_file() if plan else "sk-" + "c" * 24)["id"]
    pick(api, "ops", "codex", sid, kind="plan" if plan else "key")
    post(api, "chat/ops", {"text": "Summarize the account."})
    turn = claim(api, m)
    handed = turn.get("attempt_credential") or {}
    assert (handed.get("shell") or {}).get("id") == (sid if plan else None)
    assert not get(api, "credential-runtime?subscription_runtime=codex", turn["token"]).get("subscriptions")
