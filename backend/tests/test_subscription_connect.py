"""Connect in Settings > Models: a computer runs a plan's sign-in and the server seals the result as a company
subscription, which the browser never holds (backend/model_subscriptions.py `connect`, backend/model_login.py)."""

import json

from backend import model_login as ML
from backend import model_subscriptions as MS
from backend.auth import Identity
from backend.store import H
from backend.tests.test_api import assign, claim, get, headers, post, runner
from backend.tests.test_api import api  # noqa: F401
from backend.tests.test_credentials import setup
from backend.tests.test_model_subscriptions import OTHER, TOKEN, codex_file, company, give, on

CAPS = [MS.CAPABILITY, ML.SIGN_OUT_CAPABILITY, ML.CONNECT_CAPABILITY]


def computer(api, caps=CAPS, label="Test Mac", operator="ana", claude=True, bot=None):
    m = runner(api, operator=operator, label=label)
    if bot:
        assign(api, m, bot)
    post(api, "runners/heartbeat", {
        "version": "test", "platform": "test", "capabilities": caps,
        "readiness": {"schema_version": 1, "runtimes": {"claude": {"installed": claude, "authenticated": "missing"},
                                                        "codex": {"installed": True, "authenticated": "missing"}},
                      "bots": {bot: {"runtime": "claude", "ready": True}} if bot else {}}}, token=m["token"])
    return m


def send(api, m, login, value=TOKEN, expected=200):
    return post(api, f"runner-logins/{login['id']}/subscription", {"value": value}, m["token"], expected=expected)


def asked(api, m):
    return get(api, "runner-logins", m["token"])["logins"]


def anywhere(api, value):
    """Every table's rows that hold `value` in plain text."""
    found = []
    with api.app.state.store.read() as c:
        for (table,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE '%_fts%'").fetchall():
            for row in c.execute(f'SELECT * FROM "{table}"').fetchall():
                if value in " ".join(str(v) for v in tuple(row)):
                    found.append(table)
    return found


def test_connect_signs_a_plan_in_on_a_computer_and_seals_the_result_without_the_browser_holding_it(api):
    setup(api)
    company(api)
    m = computer(api)
    body = get(api, "model-subscriptions")
    assert body["connect"] == ["claude_token", "codex_login"]
    assert [k["connect"] for k in body["kinds"]] == [True, True, False, False]
    login = post(api, "model-subscriptions/connect", {"kind": "claude_token", "name": "Company Max"})
    assert login["kind"] == "connect" and login["state"] == "requested" and login["runner_id"] == m["runner_id"]
    assert login["runtime"] == "claude" and login["computer"] == "Test Mac"
    work = asked(api, m)
    assert [(w["id"], w["kind"], w["runtime"], w["profile"]) for w in work] == [(login["id"], "connect", "claude", "")]
    # The person gets the link and pastes the page's code back, as for any sign-in.
    post(api, f"runner-logins/{login['id']}/report", {"state": "waiting", "url": "https://claude.example/authorize",
                                                      "lines": ["Paste code here"]}, m["token"])
    seen = get(api, f"runners/{m['runner_id']}/logins/{login['id']}")
    assert seen["url"] == "https://claude.example/authorize" and seen["accepts_code"] and seen["lines"] == []
    post(api, f"runners/{m['runner_id']}/logins/{login['id']}/code", {"code": "abc123#state"})
    assert asked(api, m)[0]["code"] == "abc123#state"
    # The computer's word that it is done is not enough: only the value ends it.
    post(api, f"runner-logins/{login['id']}/report", {"state": "done"}, m["token"], expected=422)
    assert send(api, m, login) == {"state": "done", "message": ""}
    seen = get(api, f"runners/{m['runner_id']}/logins/{login['id']}")
    assert seen["state"] == "done" and not seen["url"] and TOKEN not in json.dumps(seen)
    assert asked(api, m) == []
    saved = get(api, "model-subscriptions")["subscriptions"]
    assert [(s["name"], s["kind"], s["added_by"], s["status"]) for s in saved] == [
        ("Company Max", "claude_token", "human:ana", "new")]
    assert saved[0]["preview"].endswith(TOKEN[-4:]) and TOKEN not in json.dumps(saved)
    assert anywhere(api, TOKEN) == []                                         # sealed; in no receipt, event or row
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM subscription_connects").fetchone()[0] == 0
    # It is one job: the same computer cannot send a second value for it.
    send(api, m, login, OTHER, expected=409)


def test_a_chatgpt_plan_connects_on_the_codex_sign_in(api):
    setup(api)
    company(api)
    m = computer(api)
    login = post(api, "model-subscriptions/connect", {"kind": "codex_login", "name": "ChatGPT"})
    assert login["runtime"] == "codex"
    assert send(api, m, login, codex_file())["state"] == "done"
    saved = get(api, "model-subscriptions")["subscriptions"]
    assert [(s["kind"], s["preview"]) for s in saved] == [("codex_login", "ana@acme.example")]


def test_reconnect_asks_before_the_sign_in_when_runs_would_restart_and_replaces_the_value(api):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    m = computer(api, bot="ops")
    sid = post(api, "model-subscriptions", {"kind": "claude_token", "name": "Max", "secret": TOKEN})["id"]
    give(api, m, "claude", sid)
    post(api, "chat/ops", {"text": "Summarize the account."})
    turn = claim(api, m)
    post(api, f"attempts/{turn['id']}/started", {"thread_id": "t-one"}, m["token"])
    refused = api.post("/api/v2/model-subscriptions/connect", headers=headers(), json={"subscription": sid})
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "confirm_restart"
    assert refused.json()["error"]["running"] == 1 and asked(api, m) == []    # nothing starts without the confirm
    login = post(api, "model-subscriptions/connect", {"subscription": sid, "confirm": True})
    assert send(api, m, login, OTHER)["state"] == "done"
    saved = get(api, "model-subscriptions")["subscriptions"]
    assert [(s["id"], s["name"], s["preview"][-4:]) for s in saved] == [(sid, "Max", OTHER[-4:])]
    with api.app.state.store.read() as c:
        assert c.execute("SELECT revision FROM model_subscriptions WHERE id=?", (sid,)).fetchone()[0] == 2
        assert c.execute("SELECT stop_requested FROM attempts WHERE id=?", (turn["id"],)).fetchone()[0]


def test_only_a_person_who_may_save_starts_one_and_only_for_a_plan(api):
    setup(api)
    company(api)
    computer(api)
    post(api, "model-subscriptions/connect", {"kind": "claude_token", "name": "Max"}, "cara-test", expected=403)
    api.app.state.store.settings.test_identities["assistant"] = Identity("human:ana", "owner", email="ana@acme.example",
                                                                         via="assistant")
    post(api, "model-subscriptions/connect", {"kind": "claude_token", "name": "Max"}, "assistant", expected=403)
    post(api, "model-subscriptions/connect", {"kind": "anthropic_key", "name": "Key"}, expected=422)
    post(api, "model-subscriptions/connect", {"kind": "claude_token", "name": "  "}, expected=422)
    post(api, "model-subscriptions/connect", {"subscription": "nope"}, expected=404)


def test_connect_needs_a_computer_that_is_online_up_to_date_and_has_the_tool(api):
    setup(api)
    company(api)
    assert get(api, "model-subscriptions")["connect"] == []
    start = {"kind": "claude_token", "name": "Max"}
    assert post(api, "model-subscriptions/connect", start, expected=409)["error"]["code"] == "offline"
    computer(api, caps=[MS.CAPABILITY, ML.SIGN_OUT_CAPABILITY], label="Old")   # an older runner
    computer(api, claude=False, label="No Claude")               # no Claude Code on it
    assert get(api, "model-subscriptions")["connect"] == ["codex_login"]
    post(api, "model-subscriptions/connect", start, expected=409)
    m = computer(api, label="Good")
    assert post(api, "model-subscriptions/connect", start)["runner_id"] == m["runner_id"]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runners SET last_seen=? WHERE id=?", (H.shift(H.now(), minutes=-10), m["runner_id"]))
    assert get(api, "model-subscriptions")["connect"] == ["codex_login"]


def test_the_persons_own_computer_goes_first_and_someone_elses_says_so(api):
    setup(api)
    company(api)
    other = computer(api, operator="ben", label="Ben's")
    start = {"kind": "claude_token", "name": "Max"}
    # Only Ben's can run it: the answer says the token would pass through a computer that is not theirs.
    first = post(api, "model-subscriptions/connect", start)
    assert first["runner_id"] == other["runner_id"] and first["not_yours"] is True
    post(api, f"computers/{other['runner_id']}/logins/{first['id']}/cancel", {})
    mine = computer(api, label="Mine")
    second = post(api, "model-subscriptions/connect", start)
    assert second["runner_id"] == mine["runner_id"] and second["not_yours"] is False


def test_a_second_connect_does_not_take_over_the_first_ones_result(api):
    setup(api)
    company(api)
    m = computer(api)
    send(api, m, post(api, "model-subscriptions/connect", {"kind": "claude_token", "name": "A"}))
    send(api, m, post(api, "model-subscriptions/connect", {"kind": "claude_token", "name": "B"}))
    subs = {s["name"]: s["id"] for s in get(api, "model-subscriptions")["subscriptions"]}
    reconnect_a = {"subscription": subs["A"], "confirm": True}
    first = post(api, "model-subscriptions/connect", reconnect_a)
    # Reconnecting B meanwhile is refused, and asking for A again picks the same sign-in back up.
    post(api, "model-subscriptions/connect", {"subscription": subs["B"], "confirm": True}, expected=409)
    post(api, "model-subscriptions/connect", {"kind": "claude_token", "name": "C"}, expected=409)
    assert post(api, "model-subscriptions/connect", reconnect_a)["id"] == first["id"]
    assert send(api, m, first)["state"] == "done"
    assert sorted(get(api, "model-subscriptions")["subscriptions"], key=lambda s: s["name"])[0]["id"] == subs["A"]
    assert len(get(api, "model-subscriptions")["subscriptions"]) == 2


def test_someone_elses_connect_is_not_taken_over(api):
    setup(api)
    company(api)
    m = computer(api)
    first = post(api, "model-subscriptions/connect", {"kind": "claude_token", "name": "Ana's"})
    post(api, "model-subscriptions/connect", {"kind": "claude_token", "name": "Ben's"}, "ben-test", expected=409)
    assert send(api, m, first)["state"] == "done"
    assert [s["name"] for s in get(api, "model-subscriptions")["subscriptions"]] == ["Ana's"]


def test_a_cancelled_or_expired_job_a_wrong_computer_or_a_bad_value_saves_nothing(api):
    setup(api)
    company(api)
    m = computer(api)
    other = computer(api, label="Other")
    login = post(api, "model-subscriptions/connect", {"kind": "claude_token", "name": "Max"})
    m, other = (m, other) if login["runner_id"] == m["runner_id"] else (other, m)
    send(api, other, login, expected=404)                                     # not the computer it was asked of
    post(api, f"runner-logins/{login['id']}/subscription", {"value": TOKEN}, "ana-test", expected=403)  # not a computer
    post(api, f"runners/{m['runner_id']}/logins/{login['id']}/cancel", {})
    send(api, m, login, expected=409)
    assert get(api, "model-subscriptions")["subscriptions"] == []

    login = post(api, "model-subscriptions/connect", {"kind": "claude_token", "name": "Max"})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE model_logins SET expires_at=? WHERE id=?", (H.shift(H.now(), minutes=-1), login["id"]))
    send(api, m, login, expected=409)
    assert get(api, "model-subscriptions")["subscriptions"] == []

    login = post(api, "model-subscriptions/connect", {"kind": "claude_token", "name": "Max"})
    reply = send(api, m, login, "not-a-token")
    assert reply["state"] == "failed" and "not-a-token" not in reply["message"] and "sk-ant-" in reply["message"]
    seen = get(api, f"runners/{m['runner_id']}/logins/{login['id']}")
    assert seen["state"] == "failed" and seen["message"]
    assert get(api, "model-subscriptions")["subscriptions"] == []
    # A failed sign-in the computer reports says why, and keeps no line of what the CLI printed.
    login = post(api, "model-subscriptions/connect", {"kind": "claude_token", "name": "Max"})
    post(api, f"runner-logins/{login['id']}/report", {"state": "failed", "message": "It stopped",
                                                      "lines": [f"Your token {TOKEN[:12]}"]}, m["token"])
    seen = get(api, f"runners/{m['runner_id']}/logins/{login['id']}")
    assert seen["state"] == "failed" and seen["message"] == "It stopped" and seen["lines"] == []
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM model_subscriptions").fetchone()[0] == 0
