"""A bot receives each kind of credential in a real run and using it works against the service: the Rust `tico-runner
run` against a real listening API (as test_conformance.py), its fake harness using what its turn was given
(runner_support/tests/conformance/credential_use.py markers), and a stub vendor that checks the credential on every
request (runner_support/tests/helpers/vendor.py). Each scenario sets the credential up through the API as a person
does, runs a bot turn, and checks that the vendor saw a correctly authenticated request, that the raw value is in no
transcript, event, runner log or table, and, for proxied delivery, that the run held only a `tpx1.` token and the
proxy logged the call. e2e/bots/README.md lists the scenarios.

Slow: it builds the release binary once per session (cargo) and starts runner processes."""

import hashlib
import json
import threading
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest

from backend import egress, mcp_connect
from backend.tests.helpers.api import assign, get, headers, post, put, runner
from backend.tests.helpers.credentials import setup as vault_setup
from backend.tests.live import live  # noqa: F401
from backend import bot_settings as BS
from runner_support.tests.helpers.conformance import (  # noqa: F401
    MODELS,
    computer,
    harness,
    rust_binary,
    setup,
    until,
)
from runner_support.tests.helpers.credential_turns import nowhere, one, turn
from runner_support.tests.helpers.vendor import HOST, MCP_TOOL, PUBLIC, Vendor

pytestmark = pytest.mark.slow

SECRET = "acme-synthetic-vendor-key-6194"
PAIR = "acme-synthetic-vendor-pair-6195"  # a header-pair vendor's second secret
USERNAME = "acme-api-user"
ENV = "ACME_VENDOR_KEY"
TOOL = "acme-vendor"  # the id Tools gives "Acme Vendor"
URL_ENV = "TICO_TOOL_ACME_VENDOR_URL"
FILE_ENV = "ACME_CLIENT_CERT"
FILE_BODY = "-----BEGIN ACME TEST CERT-----\nacme-synthetic-file-fixture-3390\n-----END ACME TEST CERT-----\n"
SITE = "portal.acme.example"
PORTAL_USER = "ops@acme.example"
PASSWORD = "acme-synthetic-portal-pass-5527"
TOTP_KEY = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"
BOT_AGENT = "acme-bot-fake"  # the fakes' user agent: the runner's own MCP reachability check is not the bot's call
MCP_SERVER = "acme_vendor"  # the server name the runner gives bot.yaml's `acme-vendor` entry


@pytest.fixture
def vendor(api, monkeypatch):
    """The stub vendor, the vault set up, and the Tools proxy's last hop turned to the stub: the proxy resolves the
    tool's public name to a public address as it would in production, and its call lands on the stub."""
    stub = Vendor(
        SECRET,
        pair=PAIR,
        username=USERNAME,
        file_body=FILE_BODY.encode(),
        login={"username": PORTAL_USER, "password": PASSWORD, "totp": TOTP_KEY},
    )
    real = egress.resolve
    monkeypatch.setattr(egress, "resolve", lambda host: [PUBLIC] if host == HOST else real(host))
    api.app.state.tool_proxy_transport = stub.proxy_transport()
    vault_setup(api)
    yield stub
    stub.close()


# ---- setting a credential up as a person does


def api_tool(api, scheme="bearer", delivery="proxy", username=""):
    """Tools > Add > API: Acme Vendor with its address and auth, its Read and write key, and how the key reaches runs."""
    tool = post(api, "api-tools", {"name": "Acme Vendor", "base_url": f"https://{HOST}", "auth_scheme": scheme})
    assert tool["id"] == TOOL and tool["delivery"] == "proxy", tool
    if delivery != "proxy":
        r = api.patch(f"/api/v2/api-tools/{TOOL}", json={"delivery": delivery}, headers=headers())
        assert r.status_code == 200 and r.json()["delivery"] == delivery, r.text
    key = post(
        api, f"api-tools/{TOOL}/keys", {"name": "Acme vendor key", "value": SECRET, "env": ENV, "username": username}
    )
    return key


def level(api, bot, value):
    """Tools > Acme Vendor > Who can use it: the bot's level (read, read_write or none)."""
    return put(api, f"api-tools/{TOOL}/access/bot:{bot}", {"level": value})


def declare_mcp(computer, url, bot="ops"):
    """bot.yaml's `tools:` entry for the vendor's remote MCP server, its key as a `${VAR}` header placeholder."""
    repo = computer.config.parent.parent / "projects" / f"bot-{bot}"
    manifest = repo / "bot.yaml"
    manifest.write_text(
        manifest.read_text()
        + "tools:\n"
        + "  - service: acme-vendor\n"
        + f"    env: {ENV}\n"
        + "    mcp:\n"
        + f"      url: {url}\n"
        + "      transport: http\n"
        + "      headers:\n"
        + f'        Authorization: "Bearer ${{{ENV}}}"\n'
    )


def add_bot(api, computer, slug):
    """Another bot on this computer, on its harness (call before start)."""
    assign(api, computer.registration, slug)
    config = {"name": slug, "runtime": computer.runtime, "harness": computer.harness, "model": MODELS[computer.harness]}
    with api.app.state.store.transaction() as c:
        BS.write(c, slug, {**config, "status": "active"})
    repo = computer.config.parent.parent / "projects" / f"bot-{slug}"
    repo.mkdir(parents=True)
    (repo / "AGENTS.md").write_text(f"# {slug.title()}\n\nAnswer the Acme team.\n")
    (repo / "bot.yaml").write_text(f"name: {slug}\nruntime: {config['runtime']}\nmodel: {config['model']}\n")


# ---- reading what happened


def proxied_calls(api):
    with api.app.state.store.read() as c:
        return [
            dict(r)
            for r in c.execute(
                "SELECT bot, path, status_code, result FROM tool_calls WHERE tool_id=? ORDER BY created_at", (TOOL,)
            )
        ]


# ---- static keys delivered on computers


@pytest.mark.parametrize("harness", ["claude", "codex"])
def test_a_key_on_computers_reaches_the_turn_and_the_vendor_accepts_it_as_a_bearer(api, vendor, computer):
    api_tool(api, delivery="computer")
    level(api, "ops", "read_write")
    computer.start()
    answer, uses, conversation = turn(api, computer, f"CALL bearer {ENV} {vendor.url}/bearer", f"ECHO {ENV}")
    call = one(uses, "call")
    assert call["environ"][ENV] == SECRET and URL_ENV not in call["environ"]
    assert call["status"] == 200
    [seen] = vendor.calls("/bearer", BOT_AGENT)
    assert seen["status"] == 200
    # The bot said the value: the transcript has the mask in its place.
    assert "••••" in answer["body"]
    nowhere(api, computer, conversation, SECRET)


@pytest.mark.parametrize("harness", ["claude"])
def test_a_key_on_computers_sent_as_an_api_key_header_is_accepted(api, vendor, computer):
    api_tool(api, scheme="header:X-Api-Key", delivery="computer")
    level(api, "ops", "read_write")
    computer.start()
    _, uses, conversation = turn(api, computer, f"CALL header:X-Api-Key {ENV} {vendor.url}/header")
    assert one(uses, "call")["status"] == 200
    assert [s["status"] for s in vendor.calls("/header", BOT_AGENT)] == [200]
    nowhere(api, computer, conversation, SECRET)


# ---- static keys through the Tools proxy


@pytest.mark.parametrize("harness", ["claude"])
@pytest.mark.parametrize(
    "scheme, route, logged",
    [
        ("bearer", "/bearer", "/bearer"),
        ("header:X-Api-Key", "/header", "/header"),
        ("query:api_key", "/query", "/query?api_key="),  # Activity keeps a query's keys, never its values
    ],
)
def test_a_proxied_key_never_reaches_the_turn_and_the_proxy_signs_the_vendor_call(
    api, vendor, computer, scheme, route, logged
):
    api_tool(api, scheme=scheme)
    level(api, "ops", "read_write")
    computer.start()
    _, uses, conversation = turn(api, computer, f"CALL {scheme} {ENV} ${URL_ENV}{route}", f"ECHO {ENV}")
    call = one(uses, "call")
    token = call["environ"][ENV]
    assert token.startswith("tpx1.") and call["environ"][URL_ENV].endswith(f"/api/v2/tool-proxy/{TOOL}")
    assert call["status"] == 200
    # The vendor got the real key under its own name; the run never had it, not even in the vendor's echo.
    [seen] = vendor.calls(route)
    assert (seen["status"], seen["host"], seen["shown"]) == (200, HOST, SECRET)
    assert SECRET not in json.dumps(computer.calls())
    assert proxied_calls(api) == [{"bot": "ops", "path": logged, "status_code": 200, "result": "sent"}]
    assert f"${URL_ENV}/<path>" in computer.prompts()[-1]["prompt"]
    nowhere(api, computer, conversation, SECRET, token)


@pytest.mark.parametrize("harness", ["claude"])
def test_a_proxied_username_and_password_key_reaches_the_vendor_as_basic(api, vendor, computer):
    api_tool(api, scheme="basic_password", username=USERNAME)
    level(api, "ops", "read_write")
    computer.start()
    _, uses, conversation = turn(api, computer, f"CALL basic:{USERNAME} {ENV} ${URL_ENV}/basic")
    call = one(uses, "call")
    assert call["environ"][ENV].startswith("tpx1.") and call["status"] == 200
    [seen] = vendor.calls("/basic")
    assert (seen["status"], seen["shown"]) == (200, f"{USERNAME}:{SECRET}")
    assert SECRET not in json.dumps(computer.calls())
    assert proxied_calls(api) == [{"bot": "ops", "path": "/basic", "status_code": 200, "result": "sent"}]
    nowhere(api, computer, conversation, SECRET)


# ---- several secrets on one tool: a multi-secret template (backend/tool_templates.py)


def template_tool(api, fields, ways, keys, settings=()):
    """Tools > Add > API with its address, then its template (each field a variable), its keys and settings."""
    tool = post(api, "api-tools", {"name": "Acme Vendor", "base_url": f"https://{HOST}"})
    assert tool["id"] == TOOL, tool
    put(api, f"api-tools/{TOOL}/template", {"template": {"fields": fields, "ways": ways}})
    for env, value in keys:
        post(api, f"api-tools/{TOOL}/keys", {"name": env, "value": value, "env": env})
    for name, value in settings:
        post(api, f"api-tools/{TOOL}/settings", {"name": name, "value": value})


@pytest.mark.parametrize("harness", ["claude"])
def test_a_twilio_like_account_sid_is_both_a_plain_variable_and_the_basic_username_through_the_proxy(
    api, vendor, computer
):
    """Basic with two fields, as Twilio: the account's id is a setting the run holds as it is (it also goes in paths),
    and the proxy puts it as the user beside the secret it holds."""
    fields = [{"id": "sid", "env": "ACME_ACCOUNT_SID", "role": "id"}, {"id": "key", "env": ENV, "role": "secret"}]
    template_tool(api, fields, [{"basic": ["sid", "key"]}], [(ENV, SECRET)], [("ACME_ACCOUNT_SID", USERNAME)])
    level(api, "ops", "read_write")
    computer.start()
    _, uses, conversation = turn(api, computer, f"CALL basic:${{ACME_ACCOUNT_SID}} {ENV} ${URL_ENV}/basic")
    call = one(uses, "call")
    assert call["environ"]["ACME_ACCOUNT_SID"] == USERNAME and call["environ"][ENV].startswith("tpx1.")
    assert call["status"] == 200
    [seen] = vendor.calls("/basic")
    assert (seen["status"], seen["shown"]) == (200, f"{USERNAME}:{SECRET}")
    assert SECRET not in json.dumps(computer.calls())
    assert proxied_calls(api) == [{"bot": "ops", "path": "/basic", "status_code": 200, "result": "sent"}]
    nowhere(api, computer, conversation, SECRET)


@pytest.mark.parametrize("harness", ["claude"])
def test_a_header_pair_reaches_the_vendor_with_both_secrets_while_the_run_holds_two_tokens(api, vendor, computer):
    fields = [
        {"id": "key", "env": ENV, "role": "secret"},
        {"id": "pair", "env": "ACME_VENDOR_SECRET", "role": "secret"},
    ]
    ways = [{"headers": {"X-Api-Key": "{key}", "X-Api-Secret": "{pair}"}}]
    template_tool(api, fields, ways, [(ENV, SECRET), ("ACME_VENDOR_SECRET", PAIR)])
    level(api, "ops", "read_write")
    computer.start()
    _, uses, conversation = turn(api, computer, f"CALL header:X-Api-Key {ENV} ${URL_ENV}/pair")
    call = one(uses, "call")
    assert call["environ"][ENV].startswith("tpx1.") and call["environ"]["ACME_VENDOR_SECRET"].startswith("tpx1.")
    assert call["status"] == 200
    [seen] = vendor.calls("/pair")
    assert (seen["status"], seen["shown"]) == (200, f"{SECRET}|{PAIR}")
    assert SECRET not in json.dumps(computer.calls()) and PAIR not in json.dumps(computer.calls())
    nowhere(api, computer, conversation, SECRET, PAIR)


# ---- a tool's settings


@pytest.mark.parametrize("harness", ["claude"])
@pytest.mark.parametrize("delivery", ["computer", "proxy"])
def test_a_tools_settings_reach_the_turn_beside_its_key_and_using_them_works(api, vendor, computer, delivery):
    """Tools > Acme Vendor > Settings: its address and region, plain values. A bot with a level gets both beside the key,
    on computers and through the proxy alike; the address it got reaches the vendor, and saying the region is not masked
    the way a key is. Read and write, so a key on computers is the key itself (a Read bot calls through the proxy)."""
    api_tool(api, delivery=delivery)
    post(api, f"api-tools/{TOOL}/settings", {"name": "ACME_VENDOR_URL", "value": vendor.url})
    post(api, f"api-tools/{TOOL}/settings", {"name": "ACME_VENDOR_REGION", "value": "eu-west-1"})
    level(api, "ops", "read_write")
    computer.start()
    base = "$ACME_VENDOR_URL" if delivery == "computer" else "$" + URL_ENV
    answer, uses, conversation = turn(api, computer, f"CALL bearer {ENV} {base}/bearer", "ECHO ACME_VENDOR_REGION")
    call = one(uses, "call")
    assert (call["environ"]["ACME_VENDOR_URL"], call["environ"]["ACME_VENDOR_REGION"]) == (vendor.url, "eu-west-1")
    assert call["status"] == 200 and [s["status"] for s in vendor.calls("/bearer")] == [200]
    assert "The value of ACME_VENDOR_REGION is eu-west-1." in answer["body"]
    nowhere(api, computer, conversation, SECRET)


# ---- a file credential


@pytest.mark.parametrize("harness", ["claude"])
def test_a_file_credential_is_an_owner_only_file_for_the_turn_and_gone_after_it(api, vendor, computer):
    cert = post(
        api, "credentials", {"name": "Acme client certificate", "kind": "file", "env": FILE_ENV, "secret": FILE_BODY}
    )
    post(api, f"credentials/{cert['id']}/grants", {"subject": "bot:ops"})
    computer.start()
    _, uses, conversation = turn(api, computer, f"FILE {FILE_ENV} {vendor.url}/file")
    used = one(uses, "file")
    assert used["present"] and used["mode"] == "0o600", used
    assert used["sha256"] == hashlib.sha256(FILE_BODY.encode()).hexdigest()
    assert used["status"] == 200 and [s["status"] for s in vendor.calls("/file", BOT_AGENT)] == [200]
    assert not Path(used["path"]).exists(), "the credential file outlived its turn"
    nowhere(api, computer, conversation, "acme-synthetic-file-fixture-3390")


# ---- a remote MCP server declared in bot.yaml


@pytest.mark.parametrize("harness", ["claude", "codex"])
def test_a_declared_mcp_server_gets_the_key_on_computers_and_lists_its_tools(api, vendor, computer):
    api_tool(api, delivery="computer")
    level(api, "ops", "read_write")
    declare_mcp(computer, f"{vendor.url}/mcp")
    computer.start()
    _, uses, conversation = turn(api, computer, f"MCPCALL {MCP_SERVER}")
    used = one(uses, "mcp")
    assert used["present"], used
    assert used["tools"] == [MCP_TOOL] and used["text"] == "Order 42 shipped"
    assert all(200 <= s < 300 for s in used["statuses"]), used["statuses"]
    seen = vendor.calls("/mcp", BOT_AGENT)
    assert [s["rpc"] for s in seen] == ["initialize", "notifications/initialized", "tools/list", "tools/call"]
    assert all(s["shown"] == SECRET for s in seen)
    if computer.harness == "claude":
        # The header goes to Claude Code as its placeholder: the value is never on a command line.
        argv = computer.prompts()[-1]["argv"]
        server = json.loads(argv[argv.index("--mcp-config") + 1])["mcpServers"][MCP_SERVER]
        assert server["headers"] == {"Authorization": f"Bearer ${{{ENV}}}"} and SECRET not in json.dumps(argv)
    nowhere(api, computer, conversation, SECRET)


@pytest.mark.parametrize("harness", ["claude"])
@pytest.mark.parametrize("delivery, given", [("proxy", "read_write"), ("computer", "read")])
def test_a_declared_mcp_server_whose_key_tico_holds_is_reached_through_the_proxy(
    api, vendor, computer, delivery, given
):
    """A proxied tool, and a computer tool held at Read only with just a Read and write key: the run holds a proxy
    token, its MCP server is reached through the proxy, and the vendor gets the real key. At Read the proxy lets the
    session's set-up, the listing and the read-only tool through."""
    api_tool(api, delivery=delivery)
    level(api, "ops", given)
    declare_mcp(computer, f"https://{HOST}/mcp")
    computer.start()
    _, uses, conversation = turn(api, computer, f"MCPCALL {MCP_SERVER}")
    used = one(uses, "mcp")
    assert used["present"], used
    token = used["environ"][ENV]
    assert token.startswith("tpx1."), "the run held the key itself, not a proxy token"
    assert used["tools"] == [MCP_TOOL] and used["text"] == "Order 42 shipped", json.dumps(used)
    seen = vendor.calls("/mcp", BOT_AGENT)
    assert [s["rpc"] for s in seen] == ["initialize", "notifications/initialized", "tools/list", "tools/call"]
    assert all(s["shown"] == SECRET and s["status"] < 300 for s in seen), [(s["status"], s["shown"][:5]) for s in seen]
    calls = proxied_calls(api)
    assert {c["result"] for c in calls} == {"sent"} and {c["path"] for c in calls} == {"/mcp"}, calls
    nowhere(api, computer, conversation, SECRET, token)


# ---- a remote MCP server added in Tools, granted with no bot.yaml entry


MCP_ENV = "ACME_VENDOR_MCP_TOKEN"  # the variable Tools gives the Acme Vendor MCP tool's key


def mcp_tool(api, vendor, monkeypatch, auth="token"):
    """Tools > Add > MCP server: Acme Vendor's server, probed and checked from the server through the stub."""
    monkeypatch.setattr(mcp_connect, "TRANSPORT", vendor.proxy_transport())
    url = f"https://{HOST}/mcp"
    found = post(api, "mcp-tools/probe", {"url": url})
    assert found["token"] and found["oauth"] == (auth == "oauth"), found
    body = {"name": "Acme Vendor", "url": url, "auth": auth} | ({"token": SECRET} if auth == "token" else {})
    tool = post(api, "mcp-tools", body)
    assert tool["id"] == TOOL and tool["delivery"] == "proxy", tool
    return tool


def mcp_reached(api, vendor, computer, accepted):
    """One turn's MCPCALL to the granted server: the run held a proxy token and no bot.yaml entry, and every call the
    bot made reached the vendor through the proxy with `accepted` in its place."""
    _, uses, conversation = turn(api, computer, f"MCPCALL {MCP_SERVER}")
    used = one(uses, "mcp")
    assert used["present"], used
    assert used["tools"] == [MCP_TOOL] and used["text"] == "Order 42 shipped", used
    assert used["environ"][MCP_ENV].startswith("tpx1."), "the run held the key itself, not a proxy token"
    seen = vendor.calls("/mcp", BOT_AGENT)
    assert [s["rpc"] for s in seen] == ["initialize", "notifications/initialized", "tools/list", "tools/call"]
    assert all(s["shown"] == accepted and s["host"] == HOST for s in seen), [s["status"] for s in seen]
    assert "tools:" not in (computer.config.parent.parent / "projects" / "bot-ops" / "bot.yaml").read_text()
    argv = computer.prompts()[-1]["argv"]
    server = json.loads(argv[argv.index("--mcp-config") + 1])["mcpServers"][MCP_SERVER]
    assert server["headers"] == {"Authorization": f"Bearer ${{{MCP_ENV}}}"}
    assert server["url"].endswith(f"/api/v2/tool-proxy/{TOOL}/mcp")
    calls = proxied_calls(api)
    assert len(calls) == 4 and all(c["path"] == "/mcp" and c["result"] == "sent" for c in calls), calls
    return conversation


@pytest.mark.parametrize("harness", ["claude"])
def test_a_granted_mcp_tool_with_a_token_reaches_the_run_with_no_bot_yaml_entry(api, vendor, computer, monkeypatch):
    mcp_tool(api, vendor, monkeypatch)
    detail = get(api, f"api-tools/{TOOL}")
    assert detail["status"]["text"] == "Working" and detail["mcp"]["tools"] == 1, detail["status"]
    level(api, "ops", "read_write")
    computer.start()
    conversation = mcp_reached(api, vendor, computer, SECRET)
    nowhere(api, computer, conversation, SECRET)


@pytest.mark.parametrize("harness", ["claude"])
def test_a_granted_mcp_tool_signed_in_over_oauth_reaches_the_run_and_its_tokens_stay_on_the_server(
    api, vendor, computer, monkeypatch
):
    vendor.oauth = True
    mcp_tool(api, vendor, monkeypatch, auth="oauth")
    authorize = post(api, f"api-tools/{TOOL}/sign-in", {})["authorize_url"]
    # The person's browser: the sign-in page approves and sends it back to Tico with a code.
    page = urlsplit(authorize)
    approved = httpx.get(f"{vendor.url}{page.path}?{page.query}", follow_redirects=False, timeout=10)
    back = urlsplit(approved.headers["location"])
    done = api.get(back.path + "?" + back.query, follow_redirects=False)
    assert done.status_code == 302 and done.headers["location"] == f"/#/tools/api/{TOOL}", done.text
    until(lambda: (get(api, f"api-tools/{TOOL}")["mcp"] or {}).get("tools") == 1, "the first check after sign-in")
    level(api, "ops", "read_write")
    computer.start()
    access = sorted(vendor.access)[-1]
    conversation = mcp_reached(api, vendor, computer, access)
    nowhere(api, computer, conversation, access, "acme-synthetic-oauth-refresh-1")


# ---- a website login with a password and a 2FA code


class BrowserHost:
    """The company's browser host, standing in for Chrome: a registered computer with `browser_v1` that long-polls
    its browser commands over HTTP as runner-rs/src/browser/host.rs does. It types nothing: it keeps each fill's value
    for its box and, on the click that submits, posts the form to the stub site. pytest launches no Chrome; the real
    fill into Chrome is runner-rs/src/browser/live.rs (`cargo test -- --ignored`)."""

    def __init__(self, api, live, vendor):
        self.live, self.vendor = live, vendor
        self.registration = runner(api, label="Acme browser host")
        self.token = self.registration["token"]
        post(
            api,
            "runners/heartbeat",
            {"version": "test", "platform": "test", "capabilities": ["browser_v1"]},
            token=self.token,
        )
        post(api, f"runners/{self.registration['runner_id']}/browser-host", {"on": True})
        self.boxes, self.verbs, self.errors, self.polls = {}, [], [], 0
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()
        until(lambda: self.polls, "the browser host's first poll")

    def serve(self):
        with httpx.Client(base_url=self.live + "/api/v2/", timeout=15) as http:
            while not self.stopping.is_set():
                try:
                    answer = http.get("runner-browser-commands", params={"wait": 1}, headers=headers(self.token))
                    self.polls += 1
                    for cmd in answer.json().get("commands", []):
                        http.post(
                            f"runner-browser-commands/{cmd['id']}/report",
                            json=self.run(cmd),
                            headers=headers(self.token),
                        )
                except Exception as exc:  # kept for the test to report, never a dead thread
                    self.errors.append(f"{type(exc).__name__}: {exc}")
                    self.stopping.wait(0.2)

    def run(self, cmd):
        verb, args = cmd["verb"], cmd.get("args") or {}
        self.verbs.append(verb)
        page = f"https://{SITE}/login"
        if verb == "fill":
            self.boxes[args["field"]] = cmd.get("value") or ""
            return {"ok": True, "url": page, "title": "Sign in"}
        if verb == "click":
            form = {k: self.boxes.get(k, "") for k in ("username", "password", "code")}
            status = httpx.post(self.vendor.url + "/login", data=form, timeout=10).status_code
            return {
                "ok": True,
                "url": f"https://{SITE}/home",
                "title": "Signed in" if status == 200 else "Sign-in refused",
            }
        return {"ok": True, "url": page, "title": "Sign in"}

    def stop(self):
        self.stopping.set()
        self.thread.join(timeout=10)


@pytest.mark.parametrize("harness", ["claude"])
def test_a_website_login_with_a_password_and_2fa_signs_in_and_the_bot_never_sees_either(api, live, vendor, computer):
    tool = post(api, "website-tools", {"name": "Acme Portal", "sites": [SITE], "central_only": True})
    post(api, f"website-tools/{tool['id']}/accounts", {"username": PORTAL_USER, "password": PASSWORD, "totp": TOTP_KEY})
    host = BrowserHost(api, live, vendor)
    try:
        computer.start()
        _, uses, conversation = turn(api, computer, f"LOGIN {SITE}")
    finally:
        host.stop()
    used = one(uses, "login")
    assert [s["code"] for s in used["steps"]] == [0] * 5, used["steps"]
    assert "Signed in" in used["steps"][-1]["stdout"], used["steps"][-1]
    assert host.verbs == ["open", "fill", "fill", "fill", "click"] and not host.errors, (host.verbs, host.errors)
    assert [s["status"] for s in vendor.calls("/login")] == [200]
    # The values went only to the browser host: never the turn's environment, its commands' output or Tico's records.
    assert PASSWORD not in json.dumps(computer.calls()) and TOTP_KEY not in json.dumps(computer.calls())
    nowhere(api, computer, conversation, PASSWORD, TOTP_KEY)


# ---- one credential shared by grant, and revoking it


@pytest.mark.parametrize("harness", ["claude"])
def test_a_key_granted_to_two_bots_reaches_both_and_revoking_one_leaves_the_other(api, vendor, computer):
    api_tool(api, delivery="computer")
    add_bot(api, computer, "finance")
    level(api, "ops", "read_write")
    level(api, "finance", "read_write")
    computer.start()
    until(lambda: computer.readiness().get("finance", {}).get("ready"), "the second bot's readiness")
    line = f"CALL bearer {ENV} {vendor.url}/bearer"
    for bot in ("ops", "finance"):
        _, uses, _ = turn(api, computer, line, bot=bot)
        call = one(uses, "call")
        assert call["environ"]["HUB_BOT"] == bot and call["environ"][ENV] == SECRET and call["status"] == 200
    level(api, "finance", "none")
    _, uses, _ = turn(api, computer, line, bot="finance")
    call = one(uses, "call")
    assert not call["present"] and ENV not in call["environ"]
    _, uses, conversation = turn(api, computer, line, bot="ops")
    assert one(uses, "call")["status"] == 200
    assert [s["status"] for s in vendor.calls("/bearer", BOT_AGENT)] == [200, 200, 200]
    nowhere(api, computer, conversation, SECRET)


@pytest.mark.parametrize("harness", ["claude"])
def test_after_revoke_the_next_turn_has_neither_the_variable_nor_the_mcp_server(api, vendor, computer):
    api_tool(api, delivery="computer")
    level(api, "ops", "read_write")
    declare_mcp(computer, f"{vendor.url}/mcp")
    computer.start()
    _, uses, _ = turn(api, computer, f"CALL bearer {ENV} {vendor.url}/bearer", f"MCPCALL {MCP_SERVER}")
    assert one(uses, "call")["status"] == 200 and one(uses, "mcp")["tools"] == [MCP_TOOL]
    level(api, "ops", "none")
    _, uses, conversation = turn(api, computer, f"CALL bearer {ENV} {vendor.url}/bearer", f"MCPCALL {MCP_SERVER}")
    call, mcp = one(uses, "call"), one(uses, "mcp")
    assert not call["present"] and ENV not in call["environ"]
    assert not mcp["present"] and MCP_SERVER not in mcp["servers"]
    argv = computer.prompts()[-1]["argv"]
    servers = json.loads(argv[argv.index("--mcp-config") + 1])["mcpServers"] if "--mcp-config" in argv else {}
    assert MCP_SERVER not in servers
    assert len(vendor.calls("/bearer", BOT_AGENT)) == 1 and len(vendor.calls("/mcp", BOT_AGENT)) == 4
    nowhere(api, computer, conversation, SECRET)
