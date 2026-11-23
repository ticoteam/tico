"""Full Claude logins (backend/claude_login.py): a Claude plan connected with `claude auth login --claudeai`, one
plan per account; turns get only an access token in their own file; one computer at a time refreshes
the login and the others get the new token; usage is read on a schedule from Claude's usage endpoint."""

import json
from datetime import datetime, timedelta, timezone
from importlib import import_module

import pytest

from backend import claude_login as CL
from backend import model_subscriptions as MS
from backend.store import H
from backend.tests.helpers.api import assign, claim, get, post, put, runner
from backend.tests.helpers.claude_login import access, full, refresh
from backend.tests.helpers.credentials import setup
from backend.tests.helpers.model_subscriptions import TOKEN, add, company, give, on, pick

FULL = ["model_subscriptions_v1", "model_sign_out_v1", "model_connect_v1", "claude_login_v1", "attempt_credentials_v1"]
OLD = ["model_subscriptions_v1", "model_sign_out_v1", "model_connect_v1"]


def computer(api, caps=FULL, label="Linux box", bot=None, operator="ana"):
    m = runner(api, operator=operator, label=label)
    if bot:
        assign(api, m, bot)
    post(
        api,
        "runners/heartbeat",
        {
            "version": "test",
            "platform": "linux",
            "capabilities": caps,
            "readiness": {
                "schema_version": 1,
                "runtimes": {
                    "claude": {"installed": True, "authenticated": "missing"},
                    "codex": {"installed": True, "authenticated": "missing"},
                },
                "bots": {bot: {"runtime": "claude", "ready": True}} if bot else {},
            },
        },
        token=m["token"],
    )
    return m


def asked(api, m):
    return get(api, "runner-logins", m["token"])["logins"]


def send(api, m, login, value):
    return post(api, f"runner-logins/{login['id']}/subscription", {"value": value}, m["token"])


def listed(api):
    return get(api, "model-subscriptions")["subscriptions"]


def poll(api, m, live=()):
    body = {"live": [{"subscription": sid, "revision": rev} for sid, rev in live]}
    return post(api, "runner-claude-logins", body, m["token"])


def sub_row(api, sid):
    with api.app.state.store.read() as c:
        return MS.row(c, sid)


def anywhere(api, value):
    found = []
    with api.app.state.store.read() as c:
        for (table,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE '%_fts%'"):
            for row in c.execute(f'SELECT * FROM "{table}"').fetchall():
                if value in " ".join(str(v) for v in tuple(row)):
                    found.append(table)
    return found


def at(**delta):
    return (datetime.now(timezone.utc) + timedelta(**delta)).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_a_full_login_is_named_for_its_owner_and_connecting_the_same_account_again_reconnects_it(api):
    setup(api)
    company(api)
    # Only a computer on an older runner: Connect stays the setup-token sign-in there.
    old = computer(api, caps=OLD, label="Old")
    first = post(api, "model-subscriptions/connect", {"kind": "claude_token", "name": "Max"})
    assert first["runner_id"] == old["runner_id"] and "subscription_kind" not in asked(api, old)[0]
    post(api, f"computers/{old['runner_id']}/logins/{first['id']}/cancel", {})
    m = computer(api)
    assert get(api, "model-subscriptions")["connect"] == ["claude_token", "codex_login"]  # never listed on its own
    login = post(api, "model-subscriptions/connect", {"kind": "claude_token"})
    assert login["runner_id"] == m["runner_id"] and login["name"] == "Ana's Claude plan"
    assert [w["subscription_kind"] for w in asked(api, m)] == ["claude_login"]
    assert send(api, m, login, full())["state"] == "done"
    [sub] = listed(api)
    assert (sub["kind"], sub["name"], sub["preview"]) == ("claude_login", login["name"], "ana@acme.example")
    assert sub["reconnect_by"] is None
    assert anywhere(api, access(1)) == [] and anywhere(api, refresh(1)) == []  # sealed
    # The same account connected again is that plan's Reconnect: one plan, the next revision.
    again = post(api, "model-subscriptions/connect", {"kind": "claude_token"})
    assert send(api, m, again, full(n=2))["state"] == "done"
    [same] = listed(api)
    assert same["id"] == sub["id"] and sub_row(api, sub["id"])["revision"] == 2
    foreign = post(api, "model-subscriptions/connect", {"kind": "claude_token"}, "cara-test")
    assert send(api, m, foreign, full(n=3))["state"] == "failed"
    assert sub_row(api, sub["id"])["revision"] == 2
    # Another account is a plan of its own; reconnecting one plan with the other's account is refused.
    other = post(api, "model-subscriptions/connect", {"kind": "claude_token"})
    send(api, m, other, full("ben@acme.example", n=3))
    assert sorted(s["name"] for s in listed(api)) == [login["name"], login["name"] + " 2"]
    refused = post(api, f"model-subscriptions/{sub['id']}", {"secret": full("ben@acme.example", n=4)}, expected=409)
    assert refused["error"]["code"] == "account"


def test_a_setup_token_plan_reconnected_with_a_full_login_keeps_its_owner_computers_and_history(api):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    m = computer(api, bot="ops")
    sid = add(api)["id"]
    pick(api, "ops", "claude", sid)
    before = [s for s in listed(api) if s["id"] == sid][0]
    login = post(api, "model-subscriptions/connect", {"subscription": sid, "confirm": True})
    assert asked(api, m)[0]["subscription_kind"] == "claude_login"
    assert send(api, m, login, full())["state"] == "done"
    [after] = listed(api)
    assert (after["id"], after["kind"], after["name"], after["owner_id"]) == (
        sid,
        "claude_login",
        "Company Max",
        before["owner_id"],
    )
    assert after["computers"] == before["computers"] and after["computers"]
    with api.app.state.store.read() as c:
        assert (
            c.execute(
                "SELECT count(*) FROM events WHERE action='subscription.converted' AND target=?", (sid,)
            ).fetchone()[0]
            == 1
        )
        assert c.execute("SELECT count(*) FROM agent_subscriptions WHERE subscription_id=?", (sid,)).fetchone()[0] == 1
    # The emergency path back: a pasted setup-token makes it a setup-token plan again, still the same plan.
    post(api, f"model-subscriptions/{sid}", {"secret": TOKEN, "confirm": True})
    assert [(s["id"], s["kind"]) for s in listed(api)] == [(sid, "claude_token")]


def test_the_name_migration_hides_the_account_email_of_a_login_converted_to_a_setup_token(api):
    setup(api)
    company(api)
    email = "account@acme.example"
    plan = add(api, "claude_login", full(email), name=f"{email} 2")
    reconnected = post(api, f"model-subscriptions/{plan['id']}", {"secret": TOKEN, "confirm": True})
    assert reconnected["kind"] == "claude_token" and reconnected["name"] == f"{email} 2"

    migration = import_module("backend.migrations.20261122215020_plan_display_names")
    with api.app.state.store.transaction() as c:
        migration.up(c)
    [member] = get(api, "model-subscriptions", "cara-test")["subscriptions"]
    assert (member["id"], member["kind"], member["name"], member["preview"]) == (
        plan["id"],
        "claude_token",
        "Ana's Claude plan",
        "",
    )


def test_a_kept_key_reconnected_as_a_full_login_is_retired_and_never_hands_out_the_login(api):
    setup(api)
    company(api)
    m = computer(api)
    sid = add(api)["id"]
    give(api, m, "claude", sid)
    # A setup token kept in the vault as the computer's key (before D11-4): older runners fetch it as their key.
    cid = post(api, "credentials", {"name": "Claude key", "env": "CLAUDE_CODE_OAUTH_TOKEN", "secret": TOKEN})["id"]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE model_subscriptions SET credential_id=? WHERE id=?", (cid, sid))
    with api.app.state.store.read() as c:
        before, _ = MS.keys_for(c, api.app.state.vault, m["runner_id"], "claude")
    assert before["credentials"] == []
    post(api, f"model-subscriptions/{sid}", {"kind": "claude_login", "secret": full(), "confirm": True})
    row = sub_row(api, sid)
    assert (row["kind"], row["credential_id"]) == ("claude_login", MS.CREDENTIAL + sid)
    with api.app.state.store.read() as c:
        after, sent = MS.keys_for(c, api.app.state.vault, m["runner_id"], "claude")
        assert c.execute("SELECT 1 FROM credentials WHERE id=?", (cid,)).fetchone() is None
    # The computer is told to clear its copy of the old key; nothing it fetches holds the login.
    assert after.get("clear_key") is True and after["credentials"] == [] and sent == []
    assert refresh(1) not in json.dumps(after)
    with api.app.state.store.read() as c:
        assert json.loads(MS.value_of(c, api.app.state.vault.cipher, row))["claudeAiOauth"]["refreshToken"] == refresh(
            1
        )


def test_upgrading_a_plan_preserves_its_pick_and_old_computers_wait_for_an_update(api):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    old = computer(api, caps=OLD, label="Old box", bot="ops")
    sid = add(api)["id"]
    pick(api, "ops", "claude", sid)
    updated = post(api, f"model-subscriptions/{sid}", {"kind": "claude_login", "secret": full(), "confirm": True})
    assert updated["kind"] == "claude_login"
    assert get(api, "bots/ops/subscriptions")["subscriptions"]["claude"]["subscription_id"] == sid
    post(api, "chat/ops", {"text": "Check the account."})
    assert claim(api, old) is None
    wait = next(check for check in get(api, "health")["checks"] if check["id"] == "paid_by_wait")
    assert "Update Old box" in str(wait["items"])


def test_a_turn_gets_only_an_access_token_in_its_own_file_and_an_older_runner_none(api):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    sid = add(api, "claude_login", full(), name=None)["id"]
    m = computer(api, bot="ops")
    pick(api, "ops", "claude", sid)
    post(api, "chat/ops", {"text": "Summarize the Acme account."})
    turn = claim(api, m)
    handed = turn["attempt_credential"]
    assert handed["id"] == sid and handed["file"] == ".credentials.json" and handed["home_env"] == "CLAUDE_CONFIG_DIR"
    got = handed
    assert not get(api, "credential-runtime", turn["token"]).get("subscriptions")
    assert (got["file"], got["home_env"]) == (".credentials.json", "CLAUDE_CONFIG_DIR")
    value = json.loads(got["value"])
    assert value["claudeAiOauth"]["accessToken"] == access(1)
    assert "refreshToken" not in value["claudeAiOauth"] and refresh(1) not in got["value"]
    assert "oauthAccount" not in value
    old = computer(api, caps=OLD, label="Old", bot="cpo")
    on(api, "cpo", "claude", "claude-sonnet-5-5")
    pick(api, "cpo", "claude", sid)
    post(api, "chat/cpo", {"text": "Check the account."})
    assert claim(api, old) is None


@pytest.mark.parametrize("legacy", [False, True])
def test_one_computer_refreshes_the_login_and_the_others_turns_get_the_new_token(api, legacy):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    sid = add(api, "claude_login", full(minutes=30), name=None)["id"]
    caps = [*OLD, "claude_login_v1"] if legacy else FULL
    m = computer(api, caps, bot="ops")
    pick(api, "ops", "claude", sid)
    spare = computer(api, caps, label="Spare", bot="cpo")
    pick(api, "cpo", "claude", sid)
    if legacy:
        give(api, m, "claude", sid)
    post(api, "chat/ops", {"text": "Summarize the Acme account."})
    turn = claim(api, m)
    post(api, f"attempts/{turn['id']}/started", {"thread_id": "t-1"}, m["token"])
    # Less than an hour left: the computer with a turn on the plan takes the lease, and only it.
    [job] = poll(api, m, [(sid, 1)])["refreshes"]
    assert job["subscription"] == sid and job["revision"] == 1
    assert json.loads(job["value"])["claudeAiOauth"]["refreshToken"] == refresh(1)
    assert poll(api, spare)["refreshes"] == []
    assert poll(api, m, [(sid, 1)])["refreshes"][0]["lease"] == job["lease"]  # asked again: the same lease
    done = {"lease": job["lease"], "revision": 1, "state": "succeeded", "value": full(n=2)}
    assert post(api, f"runner-claude-logins/{sid}/refreshed", done, m["token"]) == {"stored": True, "revision": 2}
    # The answer was lost: the same result again is acknowledged the same way, and nothing moves.
    assert post(api, f"runner-claude-logins/{sid}/refreshed", done, m["token"]) == {"stored": True, "revision": 2}
    row = sub_row(api, sid)
    assert row["revision"] == 2 and row["refreshed_at"] and row["access_expires_at"] > at(hours=7)
    assert row["credential_at"] == sub_row(api, sid)["credential_at"]
    # A computer with a turn on revision 1 gets the new access token, never the refresh token.
    post(api, f"model-subscriptions/{sid}/usage-refresh", {})
    answer = poll(api, m, [(sid, 1)])
    [usage] = answer["usage"]
    assert usage["subscription"] == sid and usage["token"] == access(2)
    [token] = answer["tokens"]
    assert token["revision"] == 2 and json.loads(token["value"])["claudeAiOauth"]["accessToken"] == access(2)
    assert refresh(2) not in token["value"]
    assert poll(api, m, [(sid, 2)])["tokens"] == []
    assert poll(api, spare, [(sid, 1)])["tokens"] == []  # no turn of its own on the plan
    with api.app.state.store.read() as c:
        assert (
            c.execute(
                "SELECT count(*) FROM changes WHERE kind='subscription_refresh' AND subject_id=?", (m["runner_id"],)
            ).fetchone()[0]
            >= 1
        )
    # Fresh for eight hours: nobody refreshes it now.
    assert poll(api, m, [(sid, 2)])["refreshes"] == [] and poll(api, spare)["refreshes"] == []


def test_legacy_pool_refreshes_the_rotated_login_after_it_expires_without_a_live_turn(api):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    m = computer(api, [*OLD, "claude_login_v1"], bot="ops")
    original = add(api, "claude_token", TOKEN)["id"]
    rotated = add(api, "claude_login", full(n=2), name=None)["id"]
    unused = add(api, "claude_login", full("other@acme.example", n=3), name=None)["id"]
    pick(api, "ops", "claude", person="ana")
    store = api.app.state.store
    with store.transaction() as c:
        MS._set(c, m["runner_id"], "claude", original, "human:ana", "human:ana")
        c.execute("UPDATE model_subscriptions SET status='needs_reconnect' WHERE id=?", (original,))
        c.execute("UPDATE claude_logins SET access_expires_at=? WHERE subscription_id=?", (at(minutes=-1), unused))
    post(api, "chat/ops", {"text": "Run after rotating to the next plan."})
    turn = claim(api, m)
    assert get(api, "credential-runtime", turn["token"])["subscriptions"][0]["id"] == rotated
    post(api, f"attempts/{turn['id']}/started", {"thread_id": "rotated"}, m["token"])
    post(api, f"attempts/{turn['id']}/complete", {"outcome": "completed", "last_seq": 0}, m["token"])
    with store.transaction() as c:
        c.execute("UPDATE claude_logins SET access_expires_at=? WHERE subscription_id=?", (at(minutes=-1), rotated))
    [lease] = poll(api, m)["refreshes"]
    assert lease["subscription"] == rotated
    pick(api, "ops", "claude")
    assert poll(api, m)["refreshes"] == []


@pytest.mark.parametrize("change", ["move", "clear"])
def test_maintenance_credentials_stop_when_the_computer_loses_the_pick(api, change):
    setup(api)
    company(api)
    m = computer(api, bot="ops")
    other = computer(api, label="Other box")
    refreshing = add(api, "claude_login", full(minutes=30))["id"]
    usage = add(api, "claude_login", full("other@acme.example"))["id"]
    pick(api, "ops", "claude", person="ana")
    answer = poll(api, m)
    [lease] = answer["refreshes"]
    [request] = answer["usage"]
    assert lease["subscription"] == refreshing and request["subscription"] == usage
    if change == "move":
        assign(api, other, "ops", generation=1)
    else:
        put(api, "bots/ops/subscriptions", {"subscriptions": {"claude": None}})
    rejection = {"lease": lease["lease"], "revision": lease["revision"], "state": "rejected"}
    assert post(api, f"runner-claude-logins/{refreshing}/refreshed", rejection, m["token"]) == {"stored": False}
    assert sub_row(api, refreshing)["status"] != "needs_reconnect"
    old_result = {"id": request["id"], "state": "limited", "retry_after": 3600}
    post(api, f"runner-claude-logins/{usage}/usage", old_result, m["token"], expected=404)
    with api.app.state.store.transaction() as c:
        CL._put_lease(c, refreshing, lease_until=at(minutes=-1))
    answer = poll(api, m)
    assert answer["refreshes"] == answer["usage"] == []
    with api.app.state.store.read() as c:
        assert CL._lease(c, refreshing)["lease_runner"] == ""
        assert CL._lease(c, refreshing)["lease_until"] < H.now()
        assert not CL._usage_meta(c, usage).get("request")


def test_a_lease_whose_computer_went_quiet_is_not_handed_over_while_the_old_token_works(api):
    setup(api)
    company(api)
    sid = add(api, "claude_login", full(minutes=30), name=None)["id"]
    first = computer(api, label="A", bot="ops")
    pick(api, "ops", "claude", sid)
    second = computer(api, label="B", bot="cpo")
    pick(api, "cpo", "claude", sid)
    # Each computer's turn in rotation: whichever is chosen takes the lease.
    jobs = {"A": poll(api, first)["refreshes"], "B": poll(api, second)["refreshes"]}
    [holder] = [k for k, v in jobs.items() if v]
    held, other = (first, second) if holder == "A" else (second, first)
    lease = jobs[holder][0]["lease"]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE claude_logins SET lease_until=? WHERE subscription_id=?", (at(minutes=-5), sid))
    # Uncertain: the quiet computer may have spent the refresh token. Nobody else gets it while the token works.
    assert poll(api, other)["refreshes"] == []
    assert poll(api, held)["refreshes"][0]["lease"] == lease  # its own lease comes back to it
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE claude_logins SET lease_until=? WHERE subscription_id=?", (at(minutes=-5), sid))
        c.execute("UPDATE claude_logins SET access_expires_at=? WHERE subscription_id=?", (at(minutes=-1), sid))
    # The old token is gone anyway: the other computer may try now.
    [taken] = poll(api, other)["refreshes"]
    assert taken["lease"] != lease
    # The quiet one's late result still counts (its refresh token was spent; this is the newest login there is)...
    late = {"lease": lease, "revision": 1, "state": "succeeded", "value": full(n=2)}
    assert post(api, f"runner-claude-logins/{sid}/refreshed", late, held["token"])["stored"] is True
    # ...and the second computer's stale refresh, begun from revision 1, is not kept over it.
    stale = {"lease": taken["lease"], "revision": 1, "state": "succeeded", "value": full(n=3)}
    assert post(api, f"runner-claude-logins/{sid}/refreshed", stale, other["token"]) == {"stored": False}
    with api.app.state.store.read() as c:
        value = json.loads(MS.value_of(c, api.app.state.vault.cipher, MS.row(c, sid)))
    assert value["claudeAiOauth"]["accessToken"] == access(2)
    # A login refused outright needs reconnecting; a plain failure only waits.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE claude_logins SET access_expires_at=? WHERE subscription_id=?", (at(minutes=10), sid))
        CL.forget(c, sid)
    jobs = [j for m in (first, second) for j in poll(api, m)["refreshes"]]
    [job] = jobs
    who = first if poll(api, first)["refreshes"] else second
    failed = {"lease": job["lease"], "revision": 2, "state": "failed"}
    post(api, f"runner-claude-logins/{sid}/refreshed", failed, who["token"])
    assert sub_row(api, sid)["status"] != "needs_reconnect"
    with api.app.state.store.read() as c:
        assert CL._lease(c, sid)["failures"] == 1 and CL._lease(c, sid)["retry_at"]


def test_a_refresh_result_counts_only_from_the_computer_its_lease_went_to(api):
    setup(api)
    company(api)
    sid = add(api, "claude_login", full(minutes=30), name=None)["id"]
    m = computer(api, bot="ops")
    pick(api, "ops", "claude", sid)
    other = computer(api, label="Other", bot="cpo")
    pick(api, "cpo", "claude", sid)
    [job] = poll(api, m)["refreshes"] + poll(api, other)["refreshes"]
    if not poll(api, m)["refreshes"]:
        m, other = other, m  # the lease went to the other one: name them by it
    done = {"lease": job["lease"], "revision": 1, "state": "succeeded", "value": full(n=2)}
    # Another computer with the lease's id: not kept, and the plan stays where it was.
    assert post(api, f"runner-claude-logins/{sid}/refreshed", done, other["token"]) == {"stored": False}
    assert post(api, f"runner-claude-logins/{sid}/refreshed", {**done, "lease": "x" * 32}, m["token"]) == {
        "stored": False
    }
    assert sub_row(api, sid)["revision"] == 1
    # Its own computer's result is kept, late or not; a repeat is acknowledged to it alone.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE claude_logins SET lease_until=? WHERE subscription_id=?", (at(minutes=-5), sid))
    assert post(api, f"runner-claude-logins/{sid}/refreshed", done, m["token"]) == {"stored": True, "revision": 2}
    assert post(api, f"runner-claude-logins/{sid}/refreshed", done, other["token"]) == {"stored": False}
    assert post(api, f"runner-claude-logins/{sid}/refreshed", done, m["token"]) == {"stored": True, "revision": 2}
    assert sub_row(api, sid)["revision"] == 2


def refused_turn(api, m, turn, thread="t-park"):
    post(api, f"attempts/{turn['id']}/started", {"thread_id": thread}, m["token"])
    post(
        api,
        f"attempts/{turn['id']}/complete",
        {"outcome": "failed", "last_seq": 0, "auth_rejected": {"runtime": "claude", "reason": "OAuth token revoked"}},
        m["token"],
    )
    with api.app.state.store.read() as c:
        state, result = c.execute("SELECT state,result FROM attempts WHERE id=?", (turn["id"],)).fetchone()
    return state, json.loads(result or "{}")


def test_a_turn_refused_during_a_refresh_waits_for_the_new_token_and_is_handed_it(api):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    sid = add(api, "claude_login", full(minutes=30), name=None)["id"]
    m = computer(api, bot="ops")
    pick(api, "ops", "claude", sid)
    post(api, "chat/ops", {"text": "Summarize the Acme account."})
    turn = claim(api, m)
    [job] = poll(api, m, [(sid, 1)])["refreshes"]
    # The refresh revoked the turn's token before its upload landed: the turn waits instead of taking it again.
    state, result = refused_turn(api, m, turn)
    assert state == "interrupted" and result["token_gap"] is True
    assert claim(api, m) is None
    done = {"lease": job["lease"], "revision": 1, "state": "succeeded", "value": full(n=2)}
    post(api, f"runner-claude-logins/{sid}/refreshed", done, m["token"])
    again = claim(api, m)
    assert again and again["attempt_credential"]["revision"] == 2
    with api.app.state.store.read() as c:
        assert c.execute("SELECT 1 FROM changes WHERE kind='work' AND subject_id=?", (m["runner_id"],)).fetchone()


def test_a_refused_turn_waits_five_minutes_at_most_and_a_second_refusal_is_a_failure(api):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    sid = add(api, "claude_login", full(minutes=30), name=None)["id"]
    m = computer(api, bot="ops")
    pick(api, "ops", "claude", sid)
    post(api, "chat/ops", {"text": "Summarize the Acme account."})
    turn = claim(api, m)
    poll(api, m, [(sid, 1)])  # a lease goes out, and its upload stalls
    assert refused_turn(api, m, turn)[0] == "interrupted"
    assert claim(api, m) is None
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE claude_login_parks SET until=? WHERE subscription_id=?", (at(minutes=-1), sid))
    again = claim(api, m)
    assert again and again["attempt_credential"]["revision"] == 1
    state, result = refused_turn(api, m, again, "t-park-2")
    assert state == "failed" and not result.get("token_gap")
    assert sub_row(api, sid)["status"] == "needs_reconnect"


def test_a_turn_refused_while_its_login_rotated_resumes_instead_of_failing(api):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    sid = add(api, "claude_login", full(minutes=30), name=None)["id"]
    m = computer(api, bot="ops")
    pick(api, "ops", "claude", sid)
    post(api, "chat/ops", {"text": "Summarize the Acme account."})
    turn = claim(api, m)
    post(api, f"attempts/{turn['id']}/started", {"thread_id": "t-gap"}, m["token"])
    [job] = poll(api, m, [(sid, 1)])["refreshes"]
    done = {"lease": job["lease"], "revision": 1, "state": "succeeded", "value": full(n=2)}
    post(api, f"runner-claude-logins/{sid}/refreshed", done, m["token"])
    refused = {"runtime": "claude", "reason": "OAuth token revoked"}
    post(
        api,
        f"attempts/{turn['id']}/complete",
        {"outcome": "failed", "last_seq": 0, "auth_rejected": refused},
        m["token"],
    )
    with api.app.state.store.read() as c:
        state, result = c.execute("SELECT state,result FROM attempts WHERE id=?", (turn["id"],)).fetchone()
    result = json.loads(result)
    assert state == "interrupted" and result["lease_continue"] is True and result["token_gap"] is True
    assert sub_row(api, sid)["status"] != "needs_reconnect"
    # The same refusal with no rotation behind it is the login's own: Reconnect.
    post(api, "chat/ops", {"text": "And the Globex account?"})
    second = claim(api, m)
    while second and second["id"] == turn["id"]:
        second = claim(api, m)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE claude_logins SET refreshed_at=? WHERE subscription_id=?", (at(minutes=-10), sid))
    post(api, f"attempts/{second['id']}/started", {"thread_id": "t-gap-2"}, m["token"])
    post(
        api,
        f"attempts/{second['id']}/complete",
        {"outcome": "failed", "last_seq": 0, "auth_rejected": refused},
        m["token"],
    )
    assert sub_row(api, sid)["status"] == "needs_reconnect"


def test_usage_is_read_on_a_schedule_waits_out_a_429_and_reports_all_windows(api):
    setup(api)
    company(api)
    sid = add(api, "claude_login", full(), name=None)["id"]
    m = computer(api, bot="ops")
    pick(api, "ops", "claude", sid)
    [job] = poll(api, m)["usage"]
    assert job["subscription"] == sid and job["token"] == access(1)
    # Refused (429): it waits as long as it was told, and the plan never goes to Reconnect.
    limited = {"id": job["id"], "state": "limited", "retry_after": 3600}
    post(api, f"runner-claude-logins/{sid}/usage", limited, m["token"])
    assert poll(api, m)["usage"] == []
    with api.app.state.store.read() as c:
        assert CL._usage_meta(c, sid)["schedule"]["next_at"] > at(minutes=55)
    assert sub_row(api, sid)["status"] != "needs_reconnect"
    # ↻ is held to one read in five minutes too.
    post(api, f"model-subscriptions/{sid}/usage-refresh", {})
    assert poll(api, m)["usage"] == []
    with api.app.state.store.transaction() as c:
        meta = CL._usage_meta(c, sid)
        meta["request"]["requested_at"] = at(minutes=-10)
        CL._save_usage_meta(c, sid, meta)
    post(api, f"model-subscriptions/{sid}/usage-refresh", {})
    [job] = poll(api, m)["usage"]
    read = {
        "id": job["id"],
        "state": "succeeded",
        "seven_day": {"used_percent": 40, "resets_at": at(days=3)},
        "five_hour": {"used_percent": 96, "resets_at": at(hours=2)},
        "models": [{"model": "Opus", "used_percent": 70, "resets_at": at(days=3)}],
    }
    post(api, f"runner-claude-logins/{sid}/usage", read, m["token"])
    [sub] = listed(api)
    usage = sub["usage"]
    assert (usage["window"], usage["used_pct"], usage["weekly_used_pct"]) == ("5h", 96, 40)
    assert usage["near"] is True and usage["models"][0]["model"] == "Opus"
    # A read asked before the plan was reconnected with another account is not kept.
    with api.app.state.store.transaction() as c:
        meta = CL._usage_meta(c, sid)
        meta["schedule"]["next_at"] = at(minutes=-1)
        CL._save_usage_meta(c, sid, meta)
    [job] = poll(api, m)["usage"]
    post(api, f"model-subscriptions/{sid}", {"secret": full("bo@acme.example", n=5), "confirm": True})
    stale = {**read, "id": job["id"], "five_hour": {"used_percent": 10, "resets_at": at(hours=2)}}
    post(api, f"runner-claude-logins/{sid}/usage", stale, m["token"])
    with api.app.state.store.read() as c:
        assert CL._usage_meta(c, sid)["five_hour"]["used_percent"] == 96
    assert sub_row(api, sid)["account"].startswith("bo@")


def test_a_new_login_tells_its_computers_to_read_its_usage_now(api):
    """A computer polls its full logins every five minutes while its live stream is up, so a new login's first reading
    waited that long and the plan looked broken. Signing in tells its computers to poll now (a `subscription_refresh`
    change, runner-rs events.rs), as ↻ already does."""
    setup(api)
    company(api)
    sid = add(api, "claude_login", full(), name=None)["id"]
    m = computer(api, bot="ops")
    pick(api, "ops", "claude", sid)

    def told():
        with api.app.state.store.read() as c:
            return c.execute(
                "SELECT count(*) FROM changes WHERE topic='runner' AND kind='subscription_refresh' AND subject_id=?",
                (m["runner_id"],),
            ).fetchone()[0]

    before = told()
    post(api, f"model-subscriptions/{sid}", {"secret": full("bo@acme.example", n=5), "confirm": True})
    assert told() > before
    [job] = poll(api, m)["usage"]
    assert job["subscription"] == sid and job["token"] == access(5)


def test_a_plan_near_a_limit_comes_last_only_until_that_window_resets():
    week = {"used_percent": 40, "resets_at": at(days=3)}
    meta = {"near": True, "reading": week, "five_hour": {"used_percent": 96, "resets_at": at(hours=2)}}
    assert CL.near(meta)
    # The 5-hour window has reset since the read: the plan is not ordered last for it, read again or not.
    assert not CL.near({**meta, "five_hour": {"used_percent": 96, "resets_at": at(hours=-1)}})
    # Near until back under 90% of a window still running; never near without the read saying so.
    assert CL.near(
        {**meta, "five_hour": None, "models": [{"model": "Opus", "used_percent": 92, "resets_at": at(days=3)}]}
    )
    assert not CL.near({**meta, "near": False})


def test_a_login_near_its_end_says_by_when_to_reconnect(api):
    setup(api)
    company(api)
    sid = add(api, "claude_login", full(days=3), name=None)["id"]
    [sub] = listed(api)
    assert sub["id"] == sid and sub["reconnect_by"] and sub["reconnect_by"] < at(days=4)
    assert sub["expired"] is False
    with api.app.state.store.transaction() as c:
        assert H.parse_ts(MS.row(c, sid)["login_expires_at"])
        c.execute("UPDATE claude_logins SET access_expires_at=? WHERE subscription_id=?", (at(minutes=-1), sid))
    [expired] = listed(api)
    assert expired["expired"] is True and expired["status"] == sub["status"]


def test_a_poll_with_nothing_to_write_is_answered_off_the_write_lock_as_the_write_lock_would(api, monkeypatch):
    """Every computer polls its full logins every few minutes, and most polls change nothing: such a poll is answered
    from a read snapshot. One that comes to a write (a usage read to ask, a lease to take, a turn's revision to move)
    is decided again whole under the write lock, so its answer is the one it always was."""
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    sid = add(api, "claude_login", full(minutes=30), name=None)["id"]
    m = computer(api, bot="ops")
    pick(api, "ops", "claude", sid)
    post(api, "chat/ops", {"text": "Summarize the Acme account."})
    turn = claim(api, m)
    post(api, f"attempts/{turn['id']}/started", {"thread_id": "t-1"}, m["token"])
    modes = []
    real = CL.poll

    def recorded(c, vault, runner_id, body, write=True):
        modes.append(write)
        return real(c, vault, runner_id, body, write)

    monkeypatch.setattr(CL, "poll", recorded)
    # A lease to take: the read finds it, the write lock decides it.
    first = poll(api, m, [(sid, 1)])
    assert modes == [False, True] and first["refreshes"]
    # Asked again: the same lease, read only.
    modes.clear()
    again = poll(api, m, [(sid, 1)])
    assert modes == [False] and again == first
    # The same answer as a poll under the write lock gives, and the read writes nothing.
    store, vault = api.app.state.store, api.app.state.vault
    body = CL.Poll.model_validate({"live": [{"subscription": sid, "revision": 1}]})
    with store.read_transaction() as c:
        c.execute("PRAGMA query_only=ON")
        try:
            read = real(c, vault, m["runner_id"], body, write=False)
        finally:
            c.execute("PRAGMA query_only=OFF")
    with store.transaction() as c:
        written = real(c, vault, m["runner_id"], body)
    assert read == written == first
    # A turn that holds a newer revision than its record moves the record: under the write lock.
    with store.transaction() as c:
        c.execute("UPDATE attempt_subscriptions SET revision=0 WHERE attempt_id=?", (turn["id"],))
    modes.clear()
    poll(api, m, [(sid, 1)])
    assert modes == [False, True]
    with store.read() as c:
        assert (
            c.execute("SELECT revision FROM attempt_subscriptions WHERE attempt_id=?", (turn["id"],)).fetchone()[0] == 1
        )
    # A computer ahead of its hub (a restored database): the record is at the plan's own revision, so nothing moves and
    # the poll stays off the write lock.
    modes.clear()
    poll(api, m, [(sid, 5)])
    assert modes == [False]
    # Before the vault's key is in memory (a restart), a poll that hands a value out loads it outside the write lock
    # and is read again.
    vault.cipher.cached = None
    modes.clear()
    assert poll(api, m, [(sid, 1)]) == first
    assert modes == [False, False] and vault.cipher.cached is not None


def test_every_due_plan_in_a_selected_pool_can_refresh_its_usage(api):
    setup(api)
    company(api)
    plans = [add(api, "claude_login", full(f"p{n}@acme.example", n=n), name=None)["id"] for n in range(4)]
    unrelated = add(api, "claude_login", full("other@acme.example"), token="ben-test")["id"]
    m = computer(api, bot="ops")
    pick(api, "ops", "claude", person="ana")
    with api.app.state.store.transaction() as c:
        for sid in [*plans, unrelated]:
            CL._save_usage_meta(c, sid, {"schedule": {"read_at": at(minutes=-20), "next_at": at(minutes=-1)}})
    jobs = poll(api, m)["usage"]
    assert {job["subscription"] for job in jobs} == set(plans)
    other = computer(api, label="Second box", bot="cpo")
    pick(api, "cpo", "claude", person="ana")
    assert poll(api, other)["usage"] == []  # the first computer already owns the outstanding reads


def test_a_usage_read_that_works_tries_a_plan_past_due_at_once(api):
    """A plan the provider refused for its billing answers a usage read again once it is paid: its next turn tries it
    now (login_holds.try_now) rather than at the end of its wait."""
    from backend import login_holds
    from backend.tests.helpers.login_holds import PAST_DUE

    setup(api)
    company(api)
    sid = add(api, "claude_login", full(), name=None)["id"]
    m = computer(api, bot="ops")
    pick(api, "ops", "claude", sid)
    login = login_holds.subscription_login(sid)
    with api.app.state.store.transaction() as c:
        login_holds.record(c, "", login, "claude", H.now(), error=PAST_DUE)
    [job] = poll(api, m)["usage"]
    read = {"id": job["id"], "state": "succeeded", "seven_day": {"used_percent": 40, "resets_at": at(days=3)}}
    post(api, f"runner-claude-logins/{sid}/usage", read, m["token"])
    with api.app.state.store.read() as c:
        hold = login_holds.get(c, "", login, "claude")
        assert hold and hold["until"] <= H.now() and hold["cut"] > H.now()
