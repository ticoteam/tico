"""A person's model subscriptions: saved once, picked per task, bot or team, and handed to whichever computer runs the
turn (backend/model_subscriptions.py)."""

import base64
import json

from backend import model_subscriptions as MS
from backend.store import encode
from backend.tests.test_api import api, as_member, assign, claim, get, post, put, runner  # noqa: F401
from backend.tests.test_credentials import setup

TOKEN = "sk-ant-oat01-" + "a" * 24
OTHER = "sk-ant-oat01-" + "b" * 24


def codex_file(account="acct-1", refresh="r-1", email="ana@acme.example"):
    claims = base64.urlsafe_b64encode(json.dumps({"email": email}).encode()).decode().rstrip("=")
    return json.dumps({"tokens": {"id_token": f"h.{claims}.s", "access_token": "a-" + refresh,
                                  "refresh_token": refresh, "account_id": account}})


def company(api):
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM registry_metadata WHERE key='providers'")
        c.execute("INSERT INTO registry_metadata VALUES('providers',?)",
                  (encode({"revision": 1, "enabled": ["anthropic", "openai"], "runtime": "claude",
                           "model": "claude-sonnet-5-5"}),))


def on(api, bot, runtime, model):
    with api.app.state.store.transaction() as c:
        config = json.loads(c.execute("SELECT config_json FROM bot_config WHERE bot=?", (bot,)).fetchone()[0])
        config.update(runtime=runtime, harness=runtime, model=model)
        c.execute("UPDATE bot_config SET config_json=? WHERE bot=?", (json.dumps(config), bot))


def teams(api):
    """product (led by Ben) inside company (led by Ana); cpo and product-design are on product."""
    with api.app.state.store.transaction() as c:
        roster = json.loads(c.execute("SELECT value_json FROM registry_metadata WHERE key='people'").fetchone()[0])
        roster["org_groups"] = {"company": {"name": "Company", "lead": "person:ana"},
                                "product": {"name": "Product", "parent": "company", "lead": "person:ben"}}
        for p in roster["people"]:
            p["team"] = {"ana": "company", "ben": "product"}.get(p["id"], p.get("team"))
        c.execute("UPDATE registry_metadata SET value_json=? WHERE key='people'", (encode(roster),))


def add(api, kind="claude_token", secret=TOKEN, name=None, token="ana-test", expected=200):
    return post(api, "me/subscriptions", {"kind": kind, "secret": secret, **({"name": name} if name else {})},
                token, expected=expected)


def pick(api, scope, id, runtime, sid, token="ana-test", expected=200):
    return put(api, "subscription-picks", {"scope": scope, "id": id, "runtime": runtime, "subscription": sid},
               token, expected=expected)


def machine(api, signed_in=False, takes=True, bot="ops", claude=None):
    m = runner(api)
    assign(api, m, bot)
    post(api, "runners/heartbeat", {
        "version": "test", "platform": "test", "capabilities": [MS.CAPABILITY] if takes else [],
        "readiness": {"schema_version": 1,
                      "runtimes": {"claude": {"installed": True,
                                              "authenticated": claude or ("ready" if signed_in else "missing")},
                                   "codex": {"installed": True, "authenticated": "missing"}},
                      "bots": {bot: {"runtime": "claude", "ready": True}}}}, token=m["token"])
    return m


def resolved(api, bot, runtime, task_id=None):
    with api.app.state.store.read() as c:
        found = MS.resolve(c, bot, runtime, task_id=task_id)
    return found and found["subscription"] and (found["scope"], found["subscription"]["id"])


def test_saved_once_listed_without_its_value_and_the_value_sealed(api):
    setup(api)
    saved = add(api, name="Ana's Max")
    assert saved["kind"] == "claude_token" and saved["status"] == "new" and saved["runtime"] == "claude"
    assert TOKEN not in json.dumps(saved) and saved["preview"].endswith(TOKEN[-4:])
    codex = add(api, "codex_login", codex_file())
    assert codex["preview"] == "ana@acme.example"
    listed = get(api, "me/subscriptions")
    assert [s["name"] for s in listed["subscriptions"]] == ["Ana's Max", "ChatGPT"]
    assert {k["kind"] for k in listed["kinds"]} == set(MS.KINDS)
    assert TOKEN not in json.dumps(listed) and "r-1" not in json.dumps(listed)
    with api.app.state.store.read() as c:
        stored = c.execute("SELECT sealed FROM model_subscriptions WHERE id=?", (saved["id"],)).fetchone()[0]
        assert TOKEN not in stored
        assert not c.execute("SELECT 1 FROM idempotency WHERE response_json LIKE ? OR request_hash LIKE ?",
                             (f"%{TOKEN}%", f"%{TOKEN}%")).fetchone()
        assert not c.execute("SELECT 1 FROM events WHERE detail_json LIKE ?", (f"%{TOKEN}%",)).fetchone()
    # Each person sees only their own.
    assert get(api, "me/subscriptions", "cara-test")["subscriptions"] == []
    add(api, "claude_token", "not-a-token", expected=422)
    add(api, "codex_login", "{}", expected=422)
    add(api, "openai_key", "sk-proj-" + "c" * 20)


def test_task_then_bot_then_team_then_the_team_above_and_a_broken_pick_falls_through(api):
    setup(api)
    teams(api)
    on(api, "cpo", "claude", "claude-sonnet-5-5")
    top, team, own, once = (add(api, name=n)["id"] for n in ("Top", "Team", "Bot", "Task"))
    assert resolved(api, "cpo", "claude") is None                     # nothing picked: the computer or the Gateway
    pick(api, "team", "company", "claude", top)
    assert resolved(api, "cpo", "claude") == ("team", top)
    pick(api, "team", "product", "claude", team)
    assert resolved(api, "cpo", "claude") == ("team", team)
    pick(api, "bot", "cpo", "claude", own)
    assert resolved(api, "cpo", "claude") == ("bot", own)
    t = post(api, "tasks", {"owner": "cpo", "title": "Price the Acme plan", "body": "Draft it."})
    pick(api, "task", t["id"], "claude", once)
    assert resolved(api, "cpo", "claude", t["id"]) == ("task", once)
    assert resolved(api, "cpo", "claude") == ("bot", own)             # the override is that task's only
    assert resolved(api, "cpo", "codex") is None                      # picks are per runtime
    with api.app.state.store.transaction() as c:
        MS._status(c, once, "needs_reconnect")
        found = MS.resolve(c, "cpo", "claude", task_id=t["id"])
    assert found["scope"] == "bot" and found["passed"][0]["why"] == "needs_reconnect"
    # The picker shows what is in effect and why.
    view = get(api, f"subscription-picks?scope=task&id={t['id']}")
    [row] = view["runtimes"]
    assert row["pick"]["id"] == once and row["effect"]["source"] == "bot" and row["effect"]["subscription"]["id"] == own
    # Removing a subscription takes its picks with it.
    post(api, f"me/subscriptions/{own}", {"name": "Renamed"})
    assert api.delete(f"/api/v2/me/subscriptions/{own}", headers={"Authorization": "Bearer ana-test",
                                                                   "Idempotency-Key": "rm-own"}).status_code == 200
    assert resolved(api, "cpo", "claude") == ("team", team)
    used = {s["id"]: s["used_by"] for s in get(api, "me/subscriptions")["subscriptions"]}
    assert [(u["scope"], u["id"]) for u in used[team]] == [("team", "product")]


def test_only_your_own_subscription_on_a_bot_task_or_team_you_manage(api):
    setup(api)
    teams(api)
    as_member(api, "ben@acme.example")
    on(api, "ops", "claude", "claude-sonnet-5-5")
    on(api, "cpo", "claude", "claude-sonnet-5-5")
    anas, bens, caras = add(api)["id"], add(api, token="ben-test")["id"], add(api, token="cara-test")["id"]
    pick(api, "bot", "ops", "claude", caras, "cara-test", expected=403)      # not her bot
    pick(api, "bot", "cpo", "claude", anas, "ben-test", expected=403)        # his bot, someone else's subscription
    pick(api, "bot", "cpo", "claude", bens, "ben-test")                      # his bot, his own
    pick(api, "team", "product", "claude", bens, "ben-test")                 # the team he leads
    pick(api, "team", "company", "claude", bens, "ben-test", expected=403)   # the team above him
    pick(api, "team", "company", "claude", anas)                             # the owner, any team
    pick(api, "bot", "cpo", "codex", bens, "ben-test", expected=422)         # a Claude login for Codex
    t = post(api, "tasks", {"owner": "cpo", "title": "Price the Acme plan", "body": "Draft it."})
    pick(api, "task", t["id"], "claude", caras, "cara-test", expected=403)
    pick(api, "task", t["id"], "claude", bens, "ben-test")
    assert get(api, f"subscription-picks?scope=bot&id=cpo", "ben-test")["can_pick"] is True
    # Ana manages every bot: she may take Ben's pick off; Cara may not; Ben may take his own off anywhere.
    pick(api, "bot", "cpo", "claude", None, "cara-test", expected=403)
    pick(api, "team", "company", "claude", None, "ben-test", expected=403)
    pick(api, "bot", "cpo", "claude", None)
    assert resolved(api, "cpo", "claude") == ("team", bens)
    # A subscription is the person's: nobody else renames, reconnects or removes it.
    post(api, f"me/subscriptions/{bens}", {"name": "Mine now"}, "cara-test", expected=404)
    # An owner who has left: their subscription is passed over.
    with api.app.state.store.transaction() as c:
        roster = json.loads(c.execute("SELECT value_json FROM registry_metadata WHERE key='people'").fetchone()[0])
        for p in roster["people"]:
            if p["id"] == "ben":
                p["hidden"] = True
        c.execute("UPDATE registry_metadata SET value_json=? WHERE key='people'", (encode(roster),))
    assert resolved(api, "cpo", "claude") == ("team", anas)


def test_a_turn_reads_its_subscription_on_any_computer_and_a_refusal_needs_a_reconnect(api):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    sid = add(api, name="Ana's Max")["id"]
    pick(api, "bot", "ops", "claude", sid)
    m = machine(api, claude="rejected")               # its own Claude login refused: the subscription still runs
    [entry] = [e for e in get(api, "runners/assignments", m["token"]) if e["bot"] == "ops"]
    assert entry["subscriptions"] == {"claude": "claude_token"}       # its readiness needs no Claude sign-in here
    post(api, "chat/ops", {"text": "Summarize the Acme account."})
    turn = claim(api, m)
    assert turn and turn["config"]["runtime"] == "claude" and turn["credential_vault"] is True
    [handed] = turn["subscriptions"]
    assert handed["id"] == sid and handed["env"] == "CLAUDE_CODE_OAUTH_TOKEN" and "value" not in handed
    assert TOKEN not in json.dumps(turn)
    got = get(api, "credential-runtime", turn["token"])["subscriptions"]
    assert [(s["env"], s["value"], s["billing"]) for s in got] == [("CLAUDE_CODE_OAUTH_TOKEN", TOKEN, "subscription")]
    get(api, "credential-runtime", "ana-test", expected=403)
    post(api, f"attempts/{turn['id']}/started", {"thread_id": "t-sub"}, m["token"])
    post(api, f"attempts/{turn['id']}/complete", {
        "outcome": "failed", "last_seq": 0,
        "auth_rejected": {"runtime": "claude", "reason": "OAuth token has expired"}}, m["token"])
    [mine] = get(api, "me/subscriptions")["subscriptions"]
    assert mine["status"] == "needs_reconnect"
    with api.app.state.store.read() as c:
        # The computer's own login is not marked refused, and nothing waits on a computer sign-in card.
        assert not c.execute("SELECT 1 FROM task_reconnects WHERE key LIKE 'login:%'").fetchone()
        assert (json.loads(c.execute("SELECT readiness_json FROM runners WHERE id=?", (m["runner_id"],)).fetchone()[0])
                ["runtimes"]["claude"]["authenticated"]) == "rejected"
    assert resolved(api, "ops", "claude") is None
    # Reconnecting with a new value makes it count again, and a completed turn marks it working.
    post(api, f"me/subscriptions/{sid}", {"secret": OTHER})
    assert resolved(api, "ops", "claude") == ("bot", sid)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE attempt_subscriptions SET revision=2 WHERE attempt_id=?", (turn["id"],))
        MS.turn_worked(c, turn["id"], "claude")
    assert get(api, "me/subscriptions")["subscriptions"][0]["status"] == "working"


def test_an_older_computer_runs_as_before(api):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    pick(api, "bot", "ops", "claude", add(api)["id"])
    m = machine(api, signed_in=True, takes=False)
    post(api, "chat/ops", {"text": "Summarize the Acme account."})
    turn = claim(api, m)
    assert turn and "subscriptions" not in turn
    assert "subscriptions" not in get(api, "credential-runtime", turn["token"])


def test_a_refreshed_chatgpt_login_comes_back_only_from_the_run_that_had_it(api):
    setup(api)
    company(api)
    on(api, "ops", "codex", "gpt-6.1-sol")
    sid = add(api, "codex_login", codex_file())["id"]
    pick(api, "bot", "ops", "codex", sid)
    m = machine(api)
    post(api, "chat/ops", {"text": "Summarize the Acme account."})
    turn = claim(api, m)
    [handed] = turn["subscriptions"]
    assert handed["file"] == "auth.json" and handed["revision"] == 1
    [value] = get(api, "credential-runtime", turn["token"])["subscriptions"]
    assert json.loads(value["value"])["tokens"]["refresh_token"] == "r-1"
    body = {"attempt": turn["id"], "subscription": sid, "revision": 1}
    other = runner(api, label="Other Mac")
    post(api, "runner-subscription-refresh", {**body, "value": codex_file(refresh="r-2")}, other["token"], expected=403)
    post(api, "runner-subscription-refresh", {**body, "value": codex_file(refresh="r-2")}, "ana-test", expected=403)
    assert post(api, "runner-subscription-refresh", {**body, "value": codex_file("acct-2", "r-2")},
                m["token"])["stored"] is False                  # another account's file is never kept
    assert post(api, "runner-subscription-refresh", {**body, "value": codex_file(refresh="r-2")},
                m["token"]) == {"stored": True, "revision": 2}
    assert post(api, "runner-subscription-refresh", {**body, "value": codex_file(refresh="r-3")},
                m["token"])["stored"] is False                  # stale: the stored one moved on
    with api.app.state.store.read() as c:
        sub = MS.row(c, sid)
        assert json.loads(api.app.state.vault.cipher.unseal(c, MS._context(sid), sub["sealed"]))["tokens"]["refresh_token"] == "r-2"
