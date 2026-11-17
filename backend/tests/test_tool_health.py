"""A tool's health from what really happens (backend/api_tools.py `status`, `call_health`, `due`): only a 401 that does not
say a permission is missing says a key is rejected, a 403 or such a 401 is a key that works with a limited scope, a
proxied tool's real calls outrank its check, computer tools are checked too, a known service lends its check path; ids
stored as keys (POSTHOG_PROJECT_ID, TWILIO_ACCOUNT_SID) are never checked as keys and never sit behind the proxy
(backend/tool_settings.py `is_setting`, `repair_ids_once`), and an old check that would not run today says nothing."""

import pytest

from backend import api_tools, tool_settings
from backend.store import H
from backend.tests.helpers.api import assign, claim, get, headers, post, ready, runner
from backend.tests.helpers.api_tools import key, level, tool
from backend.tests.helpers.credentials import setup

SECRET = "acme-synthetic-health-key-0011223344"
# A synthetic Twilio Account SID (an id, never a secret), built so no scanner reads the source as a real one.
SID = "AC" + "0123456789abcdef" * 2


def answer(monkeypatch, code, missing_scope=False):
    """The vendor's answer to every check: `code`, and for a 401 whether its reply says the key lacks a permission."""
    sent = []

    def send(url, hdrs, params):
        sent.append(url)
        return api_tools.Reply(send.code, send.code == 401 and missing_scope)

    send.code = code
    monkeypatch.setattr(api_tools, "send_check", send)
    return send, sent


def calls(api, tid, codes, hours_ago=1):
    """Real calls through the proxy, oldest first, `hours_ago` back."""
    with api.app.state.store.transaction() as c:
        start = H.shift(H.now(), hours=-hours_ago)
        for i, code in enumerate(codes):
            c.execute(
                "INSERT INTO tool_calls(id,tool_id,credential_id,actor,bot,access_level,method,path,status_code,result,"
                "detail,duration_ms,created_at) VALUES(?,?,NULL,'bot:ops','ops','read_write','GET','/x',?,'sent','',1,?)",
                (H.new_id(), tid, code, H.shift(start, seconds=i)),
            )


def shown(api, tid):
    row = {r["id"]: r for r in get(api, "tool-index")["rows"]}["api:" + tid]
    page = get(api, f"api-tools/{tid}")
    assert page["status"] == row["status"]
    return row["status"]["text"], row["status"]["tone"], row["needs_you"]


def made(api, name, service, env, value, delivery="proxy"):
    """A tool with one key under `env`, as an import or a person left it."""
    tid = tool(api, name, service=service)["id"]
    post(api, f"api-tools/{tid}/keys", {"name": name, "value": value, "env": env})
    if delivery != "proxy":
        api.patch(f"/api/v2/api-tools/{tid}", json={"delivery": delivery}, headers=headers())
    return tid


def test_calendly_shaped_history_with_403s_on_the_check_and_real_calls_is_working(api, monkeypatch):
    setup(api)
    tid = tool(api, "Calendly")["id"]
    key(api, tid, SECRET)
    send, sent = answer(monkeypatch, 403)
    got = post(api, f"api-tools/{tid}/check", {})
    assert sent == ["https://api.calendly.com/users/me"]
    # The vendor knows the key; /users/me needs a scope it lacks: it works, limited, and needs no one.
    assert (got["ok"], got["status_code"], got["detail"]) == (True, 403, "Works (limited)")
    assert shown(api, tid) == ("Works (limited)", "ok", False)
    calls(api, tid, [200] * 10 + [403, 400] * 3 + [200] * 10)
    assert shown(api, tid) == ("Working", "ok", False)


def test_only_a_401_is_rejected_and_real_2xx_calls_outrank_the_check(api, monkeypatch):
    setup(api)
    tid = tool(api, "ElevenLabs")["id"]  # its check path /v1/user needs user_read, which a restricted key lacks
    key(api, tid, SECRET)
    answer(monkeypatch, 401)
    assert post(api, f"api-tools/{tid}/check", {})["detail"] == "Key rejected"
    assert shown(api, tid) == ("Key rejected", "fail", True)  # no calls: the 401 stands
    calls(api, tid, [200, 200])
    assert shown(api, tid) == ("Working", "ok", False)
    calls(api, tid, [401, 401], hours_ago=0)  # two 401s since: not yet
    assert shown(api, tid) == ("Working", "ok", False)
    calls(api, tid, [401], hours_ago=0)
    assert shown(api, tid) == ("Key rejected", "fail", True)
    with api.app.state.store.transaction() as c:  # a day later the old calls say nothing
        c.execute("DELETE FROM tool_calls WHERE status_code=401")
        c.execute("UPDATE tool_calls SET created_at=?", (H.shift(H.now(), hours=-25),))
    assert shown(api, tid) == ("Key rejected", "fail", True)  # back to the check's 401


def test_a_vendor_that_answers_a_bad_key_with_403_reads_rejected_and_an_mcp_403_is_no_scope(api, monkeypatch):
    setup(api)
    tid = tool(api, "Vercel")["id"]
    key(api, tid, SECRET)
    answer(monkeypatch, 403)
    got = post(api, f"api-tools/{tid}/check", {})
    assert (got["ok"], got["detail"]) == (False, "Key rejected")
    assert shown(api, tid) == ("Key rejected", "fail", True)
    other = tool(api, "Acme API", base_url="https://api.acme.example", check_path="/me")["id"]
    key(api, other, SECRET)
    answer(monkeypatch, 500)
    assert post(api, f"api-tools/{other}/check", {})["detail"] == "Check failed: 500"
    assert shown(api, other) == ("Check failed", "fail", True)
    calls(api, other, [200])
    assert shown(api, other) == ("Working", "ok", False)


def test_the_scheduler_checks_computer_tools_with_a_known_services_path_but_never_an_id(api, monkeypatch):
    setup(api)
    close = tool(api)["id"]
    key(api, close, SECRET)
    api.patch(f"/api/v2/api-tools/{close}", json={"delivery": "computer"}, headers=headers())
    # A Calendly tool made before Calendly had a check path: it checks with the service's.
    calendly = tool(api, "Calendly")["id"]
    key(api, calendly, SECRET + "-cal")
    other = tool(api, "Calendly EU", service="calendly", base_url="https://eu.calendly.example")["id"]
    key(api, other, SECRET + "-eu")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tools SET check_path='' WHERE id IN (?,?)", (calendly, other))
    sid = made(api, "Twilio SID", "twilio", "TWILIO_ACCOUNT_SID", SID, delivery="computer")
    with api.app.state.store.read() as c:
        due = api_tools.due(c)
    # Another address gets no borrowed path; a tool holding only an id has nothing to check.
    assert sorted(due) == sorted([close, calendly])
    send, sent = answer(monkeypatch, 200)
    assert post(api, f"api-tools/{calendly}/check", {})["detail"] == "Working"
    assert sent == ["https://api.calendly.com/users/me"]
    r = api.post(f"/api/v2/api-tools/{sid}/check", json={}, headers=headers())
    assert r.status_code == 409 and "only ids" in r.json()["error"]["detail"], r.text
    assert sent == ["https://api.calendly.com/users/me"]  # the SID was never sent as a key
    thread = api_tools.check_soon(api.app.state.store, vault=api.app.state.vault)
    thread.join(10)
    with api.app.state.store.read() as c:
        checked = {r[0] for r in c.execute("SELECT DISTINCT tool_id FROM tool_checks")}
    assert checked == {close, calendly}


@pytest.mark.parametrize(
    "env, value",
    [
        ("POSTHOG_PROJECT_ID", "4242"),
        ("TWILIO_ACCOUNT_SID", SID),
        ("GOOGLE_ADS_LOGIN_CUSTOMER_ID", "123-456-7890"),
        ("ACME_WORKSPACE_ID", "acme-ops"),
        ("POSTHOG_HOST", "https://eu.posthog.example"),
        ("AWS_PROFILE", "acme-ops"),
    ],
)
def test_an_id_or_setting_is_a_setting_only_when_its_name_and_value_both_say_so(env, value):
    assert tool_settings.is_setting(env, value)


@pytest.mark.parametrize(
    "env, value",
    [
        ("AWS_ACCESS_KEY_ID", "AKIASYNTHETIC0EXAMPLE"),
        ("AWS_ACCESS_KEY_ID", "plain-value"),
        ("GOOGLE_ADS_CLIENT_ID", "1234-synthetic.apps.googleusercontent.example"),
        ("STRIPE_KEY_ID", "key-7"),
        ("VAULT_SECRET_ID", "acme-7"),
        ("ACME_API_KEY_ID", "acme-7"),
        ("POSTHOG_PROJECT_ID", "phx_Synthetic0Value1234"),  # the value is a key's shape
        ("POSTHOG_PROJECT_ID", "Zq8vR2mXk4Lp9Wn3Ts7Yb1Cd6Fg"),  # long and random-looking
        ("POSTHOG_PROJECT_ID", "two words"),
        ("ACME_ACCOUNT_ID", "a/b"),  # an id is one word
        ("ACME_ID", "4242"),  # a bare _ID says nothing
    ],
)
def test_a_key_id_secret_id_client_id_or_a_secret_shaped_value_stays_a_key(env, value):
    assert not tool_settings.is_setting(env, value)


def turn(api, bot="ops"):
    machine = runner(api)
    assign(api, machine, bot)
    ready(api, machine, [bot])
    post(api, "chat/" + bot, {"text": "Use only the synthetic fixture"})
    return machine, claim(api, machine)


def delivered(api, attempt):
    return {i["env"]: i for i in get(api, "credential-runtime", attempt["token"])["credentials"]}


def test_an_id_stored_as_a_key_never_goes_behind_the_proxy(api):
    setup(api)
    stored = post(api, "credentials", {"name": "PostHog project", "env": "POSTHOG_PROJECT_ID", "secret": "4242"})
    key_id = post(api, "credentials", {"name": "Stripe key id", "env": "STRIPE_KEY_ID", "secret": "key-7"})
    with api.app.state.store.read() as c:
        sql = (
            "SELECT t.delivery, t.base_url, t.check_path FROM credentials k JOIN tools t ON t.id=k.tool_id WHERE k.id=?"
        )
        assert tuple(c.execute(sql, (stored["id"],)).fetchone()) == ("computer", "", "")
        assert c.execute(sql, (key_id["id"],)).fetchone()["delivery"] == "proxy"  # a key id is a key


def test_the_one_time_repair_moves_ids_off_the_proxy_so_bots_get_their_values(api):
    setup(api)
    store, vault = api.app.state.store, api.app.state.vault
    posthog = made(api, "PostHog", "posthog", "POSTHOG_API_KEY", "acme-synthetic-posthog-key-5150")
    project = made(api, "PostHog project", "posthog", "POSTHOG_PROJECT_ID", "4242")
    twilio = made(api, "Twilio", "twilio", "TWILIO_AUTH_TOKEN", "acme-synthetic-twilio-token-0099")
    sid = made(api, "Twilio SID", "twilio", "TWILIO_ACCOUNT_SID", SID)
    aws = made(api, "Acme AWS", "", "AWS_ACCESS_KEY_ID", "acme-synthetic-aws-id")
    shaped = made(api, "Acme project", "", "ACME_PROJECT_ID", "phx_Synthetic0Value1234")
    for tid in (posthog, project, twilio, sid, aws, shaped):
        level(api, tid, "bot:ops", "read_write")
    level(api, sid, "bot:finance", "read_write")  # finance holds no other Twilio key: the SID stays its key
    with store.transaction() as c:
        # On the proxy, as tools of unknown services can be.
        c.execute(
            "UPDATE tools SET delivery='proxy', base_url='https://api.acme.example' WHERE id IN (?,?)", (aws, shaped)
        )
        c.execute("DELETE FROM registry_metadata WHERE key=?", (tool_settings.IDS_REPAIRED,))
    machine, attempt = turn(api)
    before = delivered(api, attempt)
    # Even before the repair, a proxied id reaches runs as its value; a real key stays a proxy token.
    assert (before["POSTHOG_PROJECT_ID"]["value"], before["TWILIO_ACCOUNT_SID"]["value"]) == ("4242", SID)
    assert before["AWS_ACCESS_KEY_ID"]["value"].startswith("tpx1.")
    assert before["ACME_PROJECT_ID"]["value"].startswith("tpx1.")
    assert tool_settings.repair_ids_once(store, vault) == 2
    assert tool_settings.repair_ids_once(store, vault) == 0  # once
    with store.read() as c:
        settings = {(r["tool_id"], r["name"]): r["value"] for r in c.execute("SELECT * FROM tool_settings")}
        tools = {r["id"]: dict(r) for r in c.execute("SELECT id, delivery, base_url, check_path FROM tools")}
        keys = {r["env"]: r["tool_id"] for r in c.execute("SELECT env, tool_id FROM credentials")}
    assert settings == {(posthog, "POSTHOG_PROJECT_ID"): "4242"}
    assert project not in tools  # the tool made for the id alone went with it
    assert keys["TWILIO_ACCOUNT_SID"] == sid and (tools[sid]["delivery"], tools[sid]["base_url"]) == ("computer", "")
    assert tools[twilio]["delivery"] == "proxy"  # the auth token stays where it was
    assert tools[aws]["delivery"] == tools[shaped]["delivery"] == "proxy"
    after = delivered(api, attempt)
    assert after["POSTHOG_PROJECT_ID"]["value"] == "4242" and after["POSTHOG_PROJECT_ID"]["kind"] == "setting"
    assert after["TWILIO_ACCOUNT_SID"]["value"] == SID
    assert after["TWILIO_AUTH_TOKEN"]["value"].startswith("tpx1.")
    assert after["AWS_ACCESS_KEY_ID"]["value"].startswith("tpx1.")
    names = get(api, "runner-credential-grants", machine["token"])["bots"]["ops"]
    assert {"POSTHOG_PROJECT_ID", "TWILIO_ACCOUNT_SID"} <= set(names)


def legacy_id_tool(api, value):
    """A PostHog tool holding its project id as its key, as before the id rule: proxied, at PostHog's address."""
    tid = made(api, "PostHog project", "posthog", "POSTHOG_PROJECT_ID", value)
    with api.app.state.store.transaction() as c:
        c.execute(
            "UPDATE tools SET delivery='proxy', base_url='https://us.posthog.com', "
            "check_path='/api/users/@me/' WHERE id=?",
            (tid,),
        )
    return tid


@pytest.mark.parametrize("value", ["4242", "sk-acme-synthetic-0011223344"])
def test_an_id_held_as_a_key_is_never_checked_and_an_old_401_for_it_says_nothing(api, monkeypatch, value):
    setup(api)
    store = api.app.state.store
    tid = legacy_id_tool(api, value)
    _, sent = answer(monkeypatch, 401)
    with store.transaction() as c:  # the 401 an older check recorded when it sent the id as the bearer
        kid = c.execute("SELECT id FROM credentials WHERE tool_id=?", (tid,)).fetchone()[0]
        c.execute(
            "INSERT INTO tool_checks(id,tool_id,credential_id,ok,status_code,detail,checked_at) "
            "VALUES('stale-check',?,?,0,401,'Key rejected',?)",
            (tid, kid, H.now()),
        )
    assert shown(api, tid) == ("Not checked", "idle", False)
    r = api.post(f"/api/v2/api-tools/{tid}/check", json={}, headers=headers())
    assert r.status_code == 409 and "holds only ids" in r.text
    assert sent == []
    with store.transaction() as c:  # the repair left it as a computer tool with no address: still nothing to say
        c.execute("UPDATE tools SET delivery='computer', base_url='', check_path='' WHERE id=?", (tid,))
    assert shown(api, tid) == ("Not checked", "idle", False)
    assert sent == []


def test_a_401_whose_reply_says_a_permission_is_missing_is_limited_for_any_service(api, monkeypatch):
    setup(api)
    eleven = tool(api, "ElevenLabs")["id"]  # its check path /v1/user needs user_read, which a restricted key lacks
    key(api, eleven, SECRET)
    answer(monkeypatch, 401, missing_scope=True)
    got = post(api, f"api-tools/{eleven}/check", {})
    assert (got["ok"], got["status_code"], got["detail"]) == (True, 401, "Works (limited)")
    assert shown(api, eleven) == ("Works (limited)", "ok", False)
    assert get(api, f"api-tools/{eleven}")["checks"][0]["detail"] == "Works (limited)"
    other = tool(api, "Acme API", base_url="https://api.acme.example", check_path="/me")["id"]
    key(api, other, SECRET)
    assert post(api, f"api-tools/{other}/check", {})["detail"] == "Works (limited)"
    assert shown(api, other) == ("Works (limited)", "ok", False)


@pytest.mark.parametrize(
    "reply, headers_, scoped",
    [
        (
            b'{"detail":{"status":"missing_permissions","message":"missing the permission user_read"}}',
            {},
            True,
        ),
        (b'{"error":"insufficient_scope"}', {}, True),
        (b"", {"WWW-Authenticate": 'Bearer error="insufficient_scope"'}, True),
        (b'{"detail":"Invalid API key"}', {}, False),
        (b"", {"WWW-Authenticate": 'Bearer error="invalid_token"'}, False),
    ],
)
def test_only_a_401_that_names_a_missing_permission_is_a_scope(reply, headers_, scoped):
    import httpx

    assert api_tools._missing_scope(httpx.Response(401, content=reply, headers=headers_)) is scoped


def test_a_401_reply_is_read_only_up_to_its_first_bytes():
    import httpx

    late = b"x" * (api_tools.SCOPE_READ + 10) + b"missing_permissions"
    assert api_tools._missing_scope(httpx.Response(401, content=late)) is False
