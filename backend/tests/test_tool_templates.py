"""Multi-secret templates on a tool (backend/tool_templates.py): a vendor account's several values on one tool reach
the vendor together through the proxy (Basic with two fields, a header pair, query + header), each still its own
variable in a run; the one-time grouping makes each vendor account one tool without changing any bot's run
environment, gives the union of grants with an event, asks for a sensitive vendor, and is undone exactly; login pairs
go to the Tools Owner; pastes are classified into a template's fields. The vendor is a local fake (MockTransport)."""

import base64
import ipaddress
import json

import httpx
import pytest

from backend import egress, page_owners, tool_templates
from backend.store import H
from backend.tests.helpers.api import assign, claim, get, headers, post, put, ready, runner
from backend.tests.helpers.credentials import setup

# Built here, never written whole: synthetic values in the shapes the templates read.
SID = "AC" + "0" * 30 + "aa"
SK = "SK" + "0" * 30 + "bb"
API_SECRET = "acmeSyntheticTwilioSecret0000001"
AUTH_TOKEN = "0" * 28 + "cafe"
ACME_KEY = "acme-synthetic-pair-key-2201"
ACME_SECRET = "acme-synthetic-pair-secret-2202"


@pytest.fixture(autouse=True)
def lookups(monkeypatch):
    """Every vendor name resolves to one public address: no real DNS in tests."""
    monkeypatch.setattr(egress, "resolve", lambda host: [ipaddress.ip_address("93.184.215.14")])


class Vendor:
    """A fake vendor API: records each request and echoes what it was shown."""

    def __init__(self):
        self.seen = []

    def __call__(self, request):
        self.seen.append(request)
        auth = request.headers.get("authorization", "")
        shown = base64.b64decode(auth[6:]).decode() if auth.startswith("Basic ") else auth
        return httpx.Response(200, json={"echo": shown, "query": str(request.url.query, "ascii")})


def vendor(api):
    fake = Vendor()
    api.app.state.tool_proxy_transport = httpx.MockTransport(fake)
    return fake


def run(api, bots=("ops",)):
    """A live run for each bot: {bot: attempt}."""
    machine = runner(api)
    out = {}
    for bot in bots:
        assign(api, machine, bot)
    ready(api, machine, list(bots))
    for bot in bots:
        post(api, "chat/" + bot, {"text": "Use only the synthetic fixture"})
        out[bot] = claim(api, machine, bot)
    return out


def env_of(api, attempt):
    """{variable: value} a run gets, each proxy token as `tpx1` (a token names its tool, and is minted per call)."""
    items = get(api, "credential-runtime", attempt["token"])["credentials"]
    out = {}
    for i in items:
        out[i["env"]] = "tpx1" if str(i["value"]).startswith("tpx1.") else i["value"]
        if i.get("url_env"):
            out[i["url_env"]] = i["proxy_url"]
    return out


def proxy(api, path, token, how="bearer", user="", **kwargs):
    auth = (
        {"Authorization": "Bearer " + token}
        if how == "bearer"
        else {"Authorization": "Basic " + base64.b64encode(f"{user}:{token}".encode()).decode()}
        if how == "basic"
        else {how: token}
    )
    return api.get(path, headers=auth, **kwargs)


def make_tool(api, tid, name, delivery="computer", base="", service="", scheme="bearer"):
    with api.app.state.store.transaction() as c:
        now = H.now()
        c.execute(
            "INSERT INTO tools(id,name,service,base_url,auth_scheme,check_path,delivery,created_by,created_at,"
            "updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (tid, name, service, base, scheme, "", delivery, "human:ana", now, now),
        )
    return tid


def add(api, tid, env, value, level="read_write"):
    return post(api, f"api-tools/{tid}/keys", {"name": env, "value": value, "env": env, "access_level": level})


def setting(api, tid, name, value):
    return post(api, f"api-tools/{tid}/settings", {"name": name, "value": value})


def give(api, tid, bot, level="read_write"):
    return put(api, f"api-tools/{tid}/access/bot:{bot}", {"level": level})


# ---- the proxy builds a template's auth from every field


def test_a_twilio_account_reaches_the_vendor_as_basic_and_the_run_holds_its_sid_and_tokens(api):
    setup(api)
    fake = vendor(api)
    tid = post(api, "api-tools", {"name": "Twilio"})["id"]
    pasted = post(
        api,
        f"api-tools/{tid}/paste",
        {"text": f"TWILIO_ACCOUNT_SID={SID}\nTWILIO_API_KEY={SK}\nTWILIO_API_SECRET={API_SECRET}"},
    )
    assert (pasted["keys"], pasted["settings"]) == (2, 1)
    assert pasted["tool"]["tool"]["template"]["id"] == "twilio"
    give(api, tid, "ops")
    attempt = run(api)["ops"]
    env = env_of(api, attempt)
    # The SID is a plain value (it is in every path); the key and secret are proxy tokens; one address.
    assert env["TWILIO_ACCOUNT_SID"] == SID
    assert env["TWILIO_API_KEY"] == env["TWILIO_API_SECRET"] == "tpx1"
    token = next(
        i["value"]
        for i in get(api, "credential-runtime", attempt["token"])["credentials"]
        if i["env"] == "TWILIO_API_SECRET"
    )
    url = env["TICO_TOOL_TWILIO_URL"]
    path = url[url.index("/api/v2/") :] + f"/2010-04-01/Accounts/{SID}/Messages.json"
    # The bot signs Basic as Twilio says (SID as user, its token as password); the vendor gets the key pair.
    r = proxy(api, path, token, "basic", SID)
    assert r.status_code == 200, r.text
    assert base64.b64decode(fake.seen[-1].headers["authorization"][6:]).decode() == f"{SK}:{API_SECRET}"
    assert API_SECRET not in r.text and r.json()["echo"] == "[key]"
    # Without the key pair, the Auth Token goes with the SID: the second way.
    page = get(api, f"api-tools/{tid}")
    for k in page["keys"]:
        assert api.delete(f"/api/v2/api-tools/{tid}/keys/{k['id']}", headers=headers()).status_code == 200
    add(api, tid, "TWILIO_AUTH_TOKEN", AUTH_TOKEN)
    give(api, tid, "ops")
    token = next(
        i["value"]
        for i in get(api, "credential-runtime", attempt["token"])["credentials"]
        if i["env"] == "TWILIO_AUTH_TOKEN"
    )
    assert proxy(api, path, token, "basic", SID).status_code == 200
    assert base64.b64decode(fake.seen[-1].headers["authorization"][6:]).decode() == f"{SID}:{AUTH_TOKEN}"


def test_a_header_pair_and_query_plus_header_template_send_every_field_and_read_holds_to_reads(api):
    setup(api)
    fake = vendor(api)
    tid = post(api, "api-tools", {"name": "Acme Pair", "base_url": "https://api.acme.example"})["id"]
    add(api, tid, "ACME_KEY", ACME_KEY)
    fields = [
        {"id": "key", "env": "ACME_KEY", "role": "secret"},
        {"id": "secret", "env": "ACME_SECRET", "role": "secret"},
    ]
    pair = {"fields": fields, "ways": [{"headers": {"X-Api-Key": "{key}", "X-Api-Secret": "{secret}"}}]}
    # One key a level, until a template says the tool holds one a variable.
    post(api, f"api-tools/{tid}/keys", {"name": "S", "value": ACME_SECRET, "env": "ACME_SECRET"}, expected=409)
    put(api, f"api-tools/{tid}/template", {"template": pair})
    add(api, tid, "ACME_SECRET", ACME_SECRET)
    give(api, tid, "ops")
    attempt = run(api)["ops"]
    items = {i["env"]: i for i in get(api, "credential-runtime", attempt["token"])["credentials"]}
    token = items["ACME_KEY"]["value"]
    assert token.startswith("tpx1.") and items["ACME_SECRET"]["value"].startswith("tpx1.")
    path = f"/api/v2/tool-proxy/{tid}/v1/orders"
    assert proxy(api, path, token, "X-Api-Key").status_code == 200
    seen = fake.seen[-1].headers
    assert (seen["x-api-key"], seen["x-api-secret"]) == (ACME_KEY, ACME_SECRET)
    # Query + header: the key in the query string, the secret in a header, wherever the caller put its token.
    pair["ways"] = [{"query": {"key": "{key}"}, "headers": {"X-Token": "{secret}"}}]
    put(api, f"api-tools/{tid}/template", {"template": pair})
    assert proxy(api, path + "?page=2&key=" + token, "", "X-Other").status_code == 200
    sent = fake.seen[-1]
    assert sent.url.params["key"] == ACME_KEY and sent.url.params["page"] == "2"
    assert sent.headers["x-token"] == ACME_SECRET
    rows = get(api, f"api-tools/{tid}/activity")["rows"]
    assert ACME_KEY not in json.dumps(rows) and ACME_SECRET not in json.dumps(rows)
    # A template that names a field it does not have is refused.
    bad = {"fields": fields, "ways": [{"headers": {"X-Api-Key": "{nope}"}}]}
    put(api, f"api-tools/{tid}/template", {"template": bad}, expected=422)
    # At Read with no read keys, the proxy lets reads through and nothing else.
    give(api, tid, "ops", "read")
    assert api.post(path + "?key=" + token, headers={"X-Token": token}).status_code == 403
    assert proxy(api, path + "?key=" + token, "", "X-Other").status_code == 200


def test_a_template_tools_check_sends_its_fields_together_on_one_row(api, monkeypatch):
    from backend import api_tools

    setup(api)
    sent = []
    monkeypatch.setattr(api_tools, "send_check", lambda url, h, p: sent.append((url, h, p)) or api_tools.Reply(200))
    tid = post(api, "api-tools", {"name": "Twilio"})["id"]
    post(api, f"api-tools/{tid}/paste", {"text": f"{SID}\n{AUTH_TOKEN}"})
    r = post(api, f"api-tools/{tid}/check", {})
    assert r["ok"] and len(r["rows"]) == 1
    [(url, sent_headers, _params)] = sent
    assert url == "https://api.twilio.com/2010-04-01/Accounts.json"
    assert base64.b64decode(sent_headers["Authorization"][6:]).decode() == f"{SID}:{AUTH_TOKEN}"
    assert get(api, f"api-tools/{tid}")["status"]["text"] == "Working"


# ---- paste to classify


@pytest.mark.parametrize(
    "text, template, expected",
    [
        # Bare values, by shape: the SID is an id; with no SK key, a 32-hex secret is the Auth Token.
        (f"{SID}\n{AUTH_TOKEN}", "twilio", [("TWILIO_ACCOUNT_SID", "id"), ("TWILIO_AUTH_TOKEN", "secret")]),
        (
            f"{SID} {SK} {API_SECRET}",
            "twilio",
            [("TWILIO_ACCOUNT_SID", "id"), ("TWILIO_API_KEY", "secret"), ("TWILIO_API_SECRET", "secret")],
        ),
        # Named lines keep their names, label included; ids and settings by tool_settings' rules.
        (
            "POSTHOG_CRO_API_KEY=phx_acmesynthetic00000000000000\nPOSTHOG_CRO_HOST=https://eu.posthog.example\n"
            "export POSTHOG_CRO_PROJECT_ID='4242'",
            "posthog",
            [("POSTHOG_CRO_API_KEY", "secret"), ("POSTHOG_CRO_HOST", "setting"), ("POSTHOG_CRO_PROJECT_ID", "id")],
        ),
        (
            "GOOGLE_ADS_CLIENT_ID: 1234-acme.apps.googleusercontent.com\nGOOGLE_ADS_LOGIN_CUSTOMER_ID: 123-456-7890",
            "google-ads",
            [("GOOGLE_ADS_CLIENT_ID", "secret"), ("GOOGLE_ADS_LOGIN_CUSTOMER_ID", "id")],
        ),
        (
            "AWS_ACCESS_KEY_ID=AKIA0000ACMESYNTH000\nAWS_REGION=eu-west-1",
            "aws",
            [("AWS_ACCESS_KEY_ID", "secret"), ("AWS_REGION", "setting")],
        ),
        # An unknown vendor: secret, id or setting by name and value; a bare URL a setting, anything else a secret.
        (
            "RAYGUN_PAT=acme-synthetic-raygun-0009\nRAYGUN_PROJECT_ID=acme-7\nhttps://api.raygun.example\nzzsynthetic",
            "",
            [("RAYGUN_PAT", "secret"), ("RAYGUN_PROJECT_ID", "id"), ("VALUE_3", "setting"), ("VALUE_4", "secret")],
        ),
    ],
)
def test_a_paste_is_split_into_a_templates_fields_and_each_value_marked(text, template, expected):
    found = tool_templates.classify(text)
    assert found["template"] == template
    assert [(i["name"], i["role"]) for i in found["items"]] == expected


def test_classify_answers_no_secret_value_and_only_a_manager_may_ask(api):
    setup(api)
    text = f"TWILIO_ACCOUNT_SID={SID}\nTWILIO_AUTH_TOKEN={AUTH_TOKEN}"
    found = post(api, "tool-templates/classify", {"text": text})
    assert AUTH_TOKEN not in json.dumps(found) and found["items"][0]["preview"] == SID
    post(api, "tool-templates/classify", {"text": text}, "cara-test", expected=403)
    assert {t["id"] for t in get(api, "tool-templates")["templates"]} == set(tool_templates.TS.TEMPLATES)


# ---- grouping a vendor's account into one tool


def tidy_shaped(api):
    """Tools as an import left them on a company like TIDY's (synthetic values): Twilio split into three (the SID on a
    computer tool of its own), Google Ads as six, the AWS pair and region on two, PostHog with its settings, a PostHog
    CRO account split in two, ElevenLabs, Calendly, a Slack tool named after its variable, a login pair and Close."""
    twilio_api = "https://api.twilio.com"
    make_tool(api, "twilio-account-sid", "TWILIO_ACCOUNT_SID")
    add(api, "twilio-account-sid", "TWILIO_ACCOUNT_SID", SID)
    make_tool(api, "twilio", "Twilio", "proxy", twilio_api, "twilio", "basic_password")
    add(api, "twilio", "TWILIO_API_KEY", SK)
    make_tool(api, "twilio-2", "TWILIO_API_SECRET", "proxy", twilio_api, "twilio", "basic_password")
    add(api, "twilio-2", "TWILIO_API_SECRET", API_SECRET)
    for tid in ("twilio-account-sid", "twilio", "twilio-2"):
        give(api, tid, "ops")
    give(api, "twilio-account-sid", "finance")
    give(api, "twilio", "finance")  # finance never held the secret explicitly: the union gives it
    ads = {
        "GOOGLE_ADS_DEVELOPER_TOKEN": "acmeSyntheticDevToken0",
        "GOOGLE_ADS_CLIENT_ID": "1234-acme.apps.googleusercontent.com",
        "GOOGLE_ADS_CLIENT_SECRET": "acme-synthetic-client-secret-77",
        "GOOGLE_ADS_REFRESH_TOKEN": "acme-synthetic-refresh-token-78",
        "GOOGLE_ADS_LOGIN_CUSTOMER_ID": "123-456-7890",
        "GOOGLE_ADS_CUSTOMER_ID": "987-654-3210",
    }
    for env, value in ads.items():
        tid = make_tool(api, env.lower().replace("_", "-"), env, service="google-ads")
        add(api, tid, env, value)
        give(api, tid, "cpo")
    make_tool(api, "aws", "AWS_ACCESS_KEY_ID")
    add(api, "aws", "AWS_ACCESS_KEY_ID", "AKIA0000ACMESYNTH000")
    setting(api, "aws", "AWS_REGION", "eu-west-1")
    make_tool(api, "aws-2", "AWS_SECRET_ACCESS_KEY")
    add(api, "aws-2", "AWS_SECRET_ACCESS_KEY", "acme/synthetic/secret/access/key/0000000")
    give(api, "aws", "ops")
    give(api, "aws-2", "ops")
    make_tool(api, "posthog", "PostHog", "proxy", "https://us.posthog.com", "posthog")
    add(api, "posthog", "POSTHOG_API_KEY", "phx_acmesynthetic00000000000001")
    setting(api, "posthog", "POSTHOG_HOST", "https://us.posthog.example")
    setting(api, "posthog", "POSTHOG_PROJECT_ID", "4242")
    give(api, "posthog", "product-design")
    make_tool(api, "posthog-cro", "POSTHOG_CRO_API_KEY", "proxy", "https://us.posthog.com", "posthog")
    add(api, "posthog-cro", "POSTHOG_CRO_API_KEY", "phx_acmesynthetic00000000000002")
    make_tool(api, "posthog-cro-project-id", "POSTHOG_CRO_PROJECT_ID")
    add(api, "posthog-cro-project-id", "POSTHOG_CRO_PROJECT_ID", "5151")
    give(api, "posthog-cro", "product-design")
    give(api, "posthog-cro-project-id", "product-design")
    make_tool(api, "elevenlabs", "ElevenLabs", "proxy", "https://api.elevenlabs.io", "elevenlabs", "header:xi-api-key")
    add(api, "elevenlabs", "ELEVENLABS_API_KEY", "acme-synthetic-eleven-0001")
    make_tool(api, "calendly", "Calendly", "proxy", "https://api.calendly.com", "calendly")
    add(api, "calendly", "CALENDLY_API_KEY", "acme-synthetic-calendly-0001")
    give(api, "calendly", "inbox")
    make_tool(api, "slack-sentry", "SLACK_SENTRY_BOT_TOKEN")
    add(api, "slack-sentry", "SLACK_SENTRY_BOT_TOKEN", "acme-synthetic-slack-sentry-01")
    give(api, "slack-sentry", "ops")
    make_tool(api, "vpm", "VPM_TIDY_USERNAME")
    add(api, "vpm", "VPM_TIDY_USERNAME", "ops@acme.example")
    make_tool(api, "vpm-2", "VPM_TIDY_PASSWORD")
    add(api, "vpm-2", "VPM_TIDY_PASSWORD", "acme-synthetic-portal-pass-01")
    give(api, "vpm", "ops")
    give(api, "vpm-2", "ops")
    make_tool(api, "close-crm", "Close CRM", "proxy", "https://api.close.com", "close-crm", "basic_user")
    add(api, "close-crm", "CLOSE_API_KEY", "acme-synthetic-close-0001")
    give(api, "close-crm", "finance")
    with api.app.state.store.transaction() as c:
        page_owners.save(c, "human:ana", "tools", "doc-updater")


BOTS = ("ops", "cpo", "product-design", "finance", "inbox")


def regroup(api):
    store, vault = api.app.state.store, api.app.state.vault
    with store.transaction() as c:
        c.execute("DELETE FROM registry_metadata WHERE key=?", (tool_templates.GROUPED,))
    return tool_templates.group_once(store, vault)


def names(api):
    with api.app.state.store.read() as c:
        return {r["id"]: r["name"] for r in c.execute("SELECT id, name FROM tools")}


def test_grouping_makes_each_vendor_account_one_tool_and_every_bots_run_environment_stays_as_it_was(api):
    setup(api)
    tidy_shaped(api)
    fake = vendor(api)
    attempts = run(api, BOTS)
    before = {bot: env_of(api, a) for bot, a in attempts.items()}
    summary = regroup(api)
    assert sorted(summary["grouped"]) == [
        "aws",
        "calendly",
        "elevenlabs",
        "google-ads",
        "posthog",
        "posthog:CRO",
        "twilio",
    ]
    assert regroup(api) is not None and tool_templates.group_once(api.app.state.store, api.app.state.vault) is None
    after = {bot: env_of(api, a) for bot, a in attempts.items()}
    with api.app.state.store.read() as c:
        events = [json.loads(r[0]) for r in c.execute("SELECT detail FROM events WHERE action='tool.grouped'")]
    gained = {}
    for record in events:
        for subject, envs in record["gained"].items():
            gained.setdefault(subject[4:], set()).update(envs)
    for bot in BOTS:
        # Every variable a bot had, with the same value or token shape and the same proxy address: nothing it reads
        # changed. Anything new is a gain its grouping's event names.
        assert {k: after[bot][k] for k in before[bot]} == before[bot], bot
        assert set(after[bot]) - set(before[bot]) == gained.get(bot, set()), bot
    # Who gained: the union gave finance the Twilio secret at its level; every bot reads the now-proxied ids.
    twilio = next(r for r in events if r["template"] == "twilio")
    assert {(g["subject"], g["env"], g["level"]) for g in twilio["grants"]} == {
        ("bot:finance", "TWILIO_API_SECRET", "read_write")
    }
    assert "TWILIO_ACCOUNT_SID" in gained["inbox"] and "POSTHOG_CRO_PROJECT_ID" in gained["finance"]
    tools = names(api)
    assert {
        tools[t]
        for t in ("twilio", "google-ads-developer-token", "aws", "posthog", "posthog-cro", "elevenlabs", "calendly")
    } == {"Twilio", "Google Ads", "AWS", "PostHog", "PostHog · CRO", "ElevenLabs", "Calendly"}
    assert tools["slack-sentry"] == "Slack · Sentry bot" and tools["close-crm"] == "Close CRM"
    assert "twilio-2" not in tools and "google-ads-customer-id" not in tools and "aws-2" not in tools
    # The old address still reaches the grouped tool, which signs Basic with the key pair.
    token = next(
        i["value"]
        for i in get(api, "credential-runtime", attempts["ops"]["token"])["credentials"]
        if i["env"] == "TWILIO_API_SECRET"
    )
    url = after["ops"]["TICO_TOOL_TWILIO_2_URL"]
    r = proxy(api, url[url.index("/api/v2/") :] + "/2010-04-01/Accounts.json", token, "basic", SID)
    assert r.status_code == 200, r.text
    assert base64.b64decode(fake.seen[-1].headers["authorization"][6:]).decode() == f"{SK}:{API_SECRET}"
    # Undo (each grouping's own): its parts come back with their names, only a manager may, and only once.
    post(api, "api-tools/twilio/ungroup", {}, "cara-test", expected=403)
    post(api, "api-tools/twilio/ungroup", {})
    post(api, "api-tools/twilio/ungroup", {}, expected=409)
    assert (names(api)["twilio-2"], names(api)["twilio"]) == ("TWILIO_API_SECRET", "Twilio")


def test_undo_restores_every_run_exactly(api):
    setup(api)
    tidy_shaped(api)
    attempts = run(api, BOTS)
    before = {bot: env_of(api, a) for bot, a in attempts.items()}
    regroup(api)
    with api.app.state.store.read() as c:
        grouped = [r[0] for r in c.execute("SELECT target FROM events WHERE action='tool.grouped'")]
    for tid in grouped:
        post(api, f"api-tools/{tid}/ungroup", {})
    assert {bot: env_of(api, a) for bot, a in attempts.items()} == before


def test_a_sensitive_vendor_whose_grouping_gives_someone_a_value_asks_and_a_vendor_with_two_accounts_waits(api):
    setup(api)
    make_tool(api, "aws", "AWS_ACCESS_KEY_ID")
    add(api, "aws", "AWS_ACCESS_KEY_ID", "AKIA0000ACMESYNTH000")
    make_tool(api, "aws-2", "AWS_SECRET_ACCESS_KEY")
    add(api, "aws-2", "AWS_SECRET_ACCESS_KEY", "acme/synthetic/secret/access/key/0000000")
    give(api, "aws", "ops")
    give(api, "aws-2", "ops")
    give(api, "aws", "finance")  # finance would get the secret key
    for tid, value in (("calendly", "acme-synthetic-calendly-0001"), ("calendly-2", "acme-synthetic-calendly-0002")):
        make_tool(api, tid, "Calendly", "proxy", "https://api.calendly.com", "calendly")
        add(api, tid, "CALENDLY_API_KEY", value)
    with api.app.state.store.transaction() as c:
        page_owners.save(c, "human:ana", "tools", "doc-updater")
    summary = regroup(api)
    assert summary["grouped"] == [] and summary["asked"] == ["aws"]
    assert summary["skipped"] == ["calendly: CALENDLY_API_KEY is on two tools"]
    assert {"aws", "aws-2", "calendly", "calendly-2"} <= set(names(api))
    with api.app.state.store.read() as c:
        task = c.execute("SELECT id, title, owner_id AS owner FROM tasks WHERE title='Group AWS?'").fetchone()
        said = H.task_words(c, task["id"])  # what Tico asked the tools' owner (tasks have no description)
    assert task["owner"] == "bot:doc-updater" and "bot:finance" in said
    regroup(api)  # asked once
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM tasks WHERE title='Group AWS?'").fetchone()[0] == 1


def test_a_login_pair_stored_as_two_keys_is_offered_to_the_tools_owner_once_and_keeps_reaching_runs(api):
    setup(api)
    make_tool(api, "click2mail", "CLICK2MAIL_USERNAME")
    add(api, "click2mail", "CLICK2MAIL_USERNAME", "ops@acme.example")
    make_tool(api, "click2mail-2", "CLICK2MAIL_PASSWORD")
    add(api, "click2mail-2", "CLICK2MAIL_PASSWORD", "acme-synthetic-portal-pass-02")
    give(api, "click2mail", "ops")
    give(api, "click2mail-2", "ops")
    with api.app.state.store.transaction() as c:
        page_owners.save(c, "human:ana", "tools", "doc-updater")
    assert regroup(api)["logins"] == 1
    assert regroup(api)["logins"] == 0
    with api.app.state.store.read() as c:
        task = c.execute(
            "SELECT id, owner_id AS owner FROM tasks WHERE title='Make CLICK2MAIL a website account?'"
        ).fetchone()
        said = H.task_words(c, task["id"])
    assert task["owner"] == "bot:doc-updater" and "CLICK2MAIL_PASSWORD" in said
    assert "acme-synthetic-portal-pass-02" not in said
    env = env_of(api, run(api)["ops"])
    assert env["CLICK2MAIL_PASSWORD"] == "acme-synthetic-portal-pass-02"


def test_a_key_stored_elsewhere_joins_its_vendor_accounts_tool_and_not_another_accounts(api):
    setup(api)
    tid = post(api, "api-tools", {"name": "Twilio"})["id"]
    post(api, f"api-tools/{tid}/paste", {"text": f"TWILIO_ACCOUNT_SID={SID}\nTWILIO_API_KEY={SK}"})
    secret = post(api, "credentials", {"name": "Twilio secret", "secret": API_SECRET, "env": "TWILIO_API_SECRET"})
    test = post(api, "credentials", {"name": "Test token", "secret": AUTH_TOKEN, "env": "TWILIO_TEST_AUTH_TOKEN"})
    with api.app.state.store.read() as c:
        homes = {r["id"]: r["tool_id"] for r in c.execute("SELECT id, tool_id FROM credentials")}
    assert homes[secret["id"]] == tid  # the account's other value
    assert homes[test["id"]] != tid  # another account's (its TEST label) is not this tool's
    assert {k["env"] for k in get(api, f"api-tools/{tid}")["keys"]} == {"TWILIO_API_KEY", "TWILIO_API_SECRET"}
