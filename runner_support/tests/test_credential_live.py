"""The live auth matrix: each kind of auth Tico can hand a bot today, against a real public test service. A person
sets the credential up through the API as Tools does (an API tool and its key, a vault credential, a file credential,
a grant), the real Rust runner runs one bot turn, and the fake harness uses what the turn was given against the
service, with the standard library only, as a bot would (runner_support/tests/conformance/credential_use.py). Each
scenario checks that the good credential works, that the service refuses a wrong one (so the pass means something),
and that the value is in no transcript, event, runner log or table. docs/auth-types.md lists every auth type, how a
person hands it over and which scenario here covers it.

Every value here is a vendor's published test credential (reqres.in, NASA's DEMO_KEY, MinIO's play server,
postman-echo, badssl.com's client certificate, GitHub's documented webhook example, Rebex's and DummyJSON's demo
users) or synthetic: never a company's own.

Opt-in: these call the internet, so they run only with TICO_LIVE_AUTH=1, and are slow (the release binary and a
runner process per scenario):
    TICO_LIVE_AUTH=1 .venv/bin/python -m pytest -q -m slow -n 2 runner_support/tests/test_credential_live.py
A service that is down, rate limited or answering 5xx skips its scenario with the reason; it never fails it."""

import base64
import os
import secrets
import socket

import httpx
import pytest

from backend.tests.helpers.api import headers, post, put
from backend.tests.helpers.credentials import setup as vault_setup
from backend.tests.live import live  # noqa: F401
from runner_support.tests.helpers.conformance import computer, rust_binary, setup  # noqa: F401
from runner_support.tests.helpers.credential_turns import nowhere, one, turn

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        not os.environ.get("TICO_LIVE_AUTH"), reason="TICO_LIVE_AUTH=1 runs the live auth matrix (public services)"
    ),
]

AGENT = "acme-bot-fake/1"  # the fakes' user agent: Cloudflare in front of postman-echo refuses python-urllib's


@pytest.fixture
def harness():
    return "claude"


@pytest.fixture(autouse=True)
def vault(api):
    vault_setup(api)


# ---- the service is there at all


def reachable(url):
    """Skip unless `url` answers (any status under 500 but 429): a scenario never fails for a service that is down."""
    try:
        status = httpx.get(url, timeout=15, headers={"User-Agent": AGENT}).status_code
    except httpx.HTTPError as exc:
        pytest.skip(f"{url} did not answer: {type(exc).__name__}")
    if status == 429 or status >= 500:
        pytest.skip(f"{url} answered {status}")


def answered(status, what):
    """Skip when a call met an outage or a rate limit (no answer, 429, 5xx) rather than an auth answer."""
    if status in (None, 0, 429) or status >= 500:
        pytest.skip(f"{what} answered {status or 'nothing'}")


# ---- setting credentials up as a person does


def api_tool(api, name, base_url="", scheme="bearer", check_path=""):
    """Tools > Add > API: with an address it is on the proxy, without one on computers (bots get the key)."""
    tool = post(api, "api-tools", {"name": name, "base_url": base_url, "auth_scheme": scheme, "check_path": check_path})
    assert tool["delivery"] == ("proxy" if base_url else "computer"), tool
    return tool["id"]


def tool_key(api, tool, env, value, username=""):
    """The tool's key (Read and write), and the ops bot given Read and write on the tool."""
    key = post(api, f"api-tools/{tool}/keys", {"name": f"{tool} key", "value": value, "env": env, "username": username})
    put(api, f"api-tools/{tool}/access/bot:ops", {"level": "read_write"})
    return key


def credential(api, name, env, value, kind="api_key"):
    """A vault credential (Tools > Add > Key, or a file) granted to the ops bot."""
    made = post(api, "credentials", {"name": name, "kind": kind, "env": env, "secret": value})
    post(api, f"credentials/{made['id']}/grants", {"subject": "bot:ops"})
    return made


def rejected_after_a_wrong_key(api, tool, key, wrong):
    """The person pastes a wrong key and runs Check now: the service's own refusal comes back on the check."""
    r = api.patch(f"/api/v2/api-tools/{tool}/keys/{key['id']}", json={"value": wrong}, headers=headers())
    assert r.status_code == 200, r.text
    checked = post(api, f"api-tools/{tool}/check", {})
    answered(checked["status_code"], f"{tool}'s check")
    return checked


def proxy_token(call):
    token = call["environ"][call["env"]]
    assert token.startswith("tpx1."), "the run held the key itself, not a proxy token"
    return token


# ---- bearer


def test_bearer_on_computers_httpbin(api, computer):
    reachable("https://httpbin.org/get")
    token = "acme-live-bearer-" + secrets.token_hex(6)
    tool = api_tool(api, "Httpbin")  # no address: on computers
    tool_key(api, tool, "HTTPBIN_TOKEN", token)
    computer.start()
    _, uses, conversation = turn(api, computer, "CALL bearer HTTPBIN_TOKEN https://httpbin.org/bearer +bare")
    call = one(uses, "call")
    answered(call["status"], "httpbin.org")
    # httpbin takes any bearer token, so its refusal of a call without one is the check that it reads the header.
    assert (call["status"], call["refused"]) == (200, 401), call
    assert call["environ"]["HTTPBIN_TOKEN"] == token and f'"token": "{token}"' in call["body"]
    nowhere(api, computer, conversation, token)


def test_bearer_through_the_proxy_httpbin(api, computer):
    reachable("https://httpbin.org/get")
    token = "acme-live-bearer-" + secrets.token_hex(6)
    tool = api_tool(api, "Httpbin", "https://httpbin.org")
    tool_key(api, tool, "HTTPBIN_TOKEN", token)
    computer.start()
    _, uses, conversation = turn(api, computer, "CALL bearer HTTPBIN_TOKEN $TICO_TOOL_HTTPBIN_URL/bearer")
    call = one(uses, "call")
    answered(call["status"], "httpbin.org through the proxy")
    assert call["status"] == 200, call
    # httpbin echoes the token it got: the real key, which the proxy takes out of the answer before the bot reads it.
    assert '"authenticated": true' in call["body"] and '"token": "[key]"' in call["body"], call["body"]
    nowhere(api, computer, conversation, token, proxy_token(call))


# ---- basic and digest


def test_basic_on_computers_httpbin(api, computer):
    reachable("https://httpbin.org/get")
    password = "acme-live-pass-" + secrets.token_hex(6)
    tool = api_tool(api, "Httpbin")
    tool_key(api, tool, "HTTPBIN_PASSWORD", password)
    computer.start()
    # httpbin's path names the pair it expects; the bot fills the password into it from its environment.
    url = "https://httpbin.org/basic-auth/acme-user/${HTTPBIN_PASSWORD}"
    _, uses, conversation = turn(api, computer, f"CALL basic:acme-user HTTPBIN_PASSWORD {url} +wrong")
    call = one(uses, "call")
    answered(call["status"], "httpbin.org")
    assert (call["status"], call["refused"]) == (200, 401), call
    nowhere(api, computer, conversation, password)


def test_basic_password_through_the_proxy_postman_echo(api, computer):
    reachable("https://postman-echo.com/get")
    tool = api_tool(api, "Postman Echo", "https://postman-echo.com", "basic_password", "/basic-auth")
    key = tool_key(api, tool, "POSTMAN_ECHO_PASSWORD", "password", username="postman")
    computer.start()
    marker = "CALL basic:postman POSTMAN_ECHO_PASSWORD $TICO_TOOL_POSTMAN_ECHO_URL/basic-auth"
    _, uses, conversation = turn(api, computer, marker)
    call = one(uses, "call")
    answered(call["status"], "postman-echo.com through the proxy")
    assert call["status"] == 200 and '"authenticated":true' in call["body"].replace(" ", ""), call
    nowhere(api, computer, conversation, base64.b64encode(b"postman:password").decode(), proxy_token(call))
    checked = rejected_after_a_wrong_key(api, tool, key, "passwordx")
    assert (checked["status_code"], checked["detail"]) == (401, "Key rejected"), checked


def test_digest_on_computers_httpbin(api, computer):
    reachable("https://httpbin.org/get")
    password = "acme-live-digest-" + secrets.token_hex(6)
    tool = api_tool(api, "Httpbin")
    tool_key(api, tool, "HTTPBIN_PASSWORD", password)
    computer.start()
    url = "https://httpbin.org/digest-auth/auth/acme-user/${HTTPBIN_PASSWORD}"
    _, uses, conversation = turn(api, computer, f"CALL digest:acme-user HTTPBIN_PASSWORD {url} +wrong")
    call = one(uses, "call")
    answered(call["status"], "httpbin.org")
    assert (call["status"], call["refused"]) == (200, 401), call
    nowhere(api, computer, conversation, password)


# ---- API keys in a header and in the query, through the proxy


def test_api_key_header_through_the_proxy_reqres(api, computer):
    reachable("https://reqres.in/")
    tool = api_tool(api, "Reqres", "https://reqres.in", "header:x-api-key", "/api/users/2")
    key = tool_key(api, tool, "REQRES_API_KEY", "reqres-free-v1")
    computer.start()
    marker = "CALL header:x-api-key REQRES_API_KEY $TICO_TOOL_REQRES_URL/api/users/2"
    _, uses, conversation = turn(api, computer, marker)
    call = one(uses, "call")
    answered(call["status"], "reqres.in through the proxy")
    assert call["status"] == 200 and "janet.weaver@reqres.in" in call["body"], call
    nowhere(api, computer, conversation, "reqres-free-v1", proxy_token(call))
    # reqres.in answers a call with no key at all; a wrong key is a 403, so this shows the proxy sends the key. Only a
    # 401 reads Key rejected: a 403 is a key without the path's scope to a check, so real calls say whether it works.
    checked = rejected_after_a_wrong_key(api, tool, key, "reqres-free-v1x")
    assert (checked["status_code"], checked["detail"]) == (403, "Works (limited)"), checked


def test_api_key_query_through_the_proxy_nasa(api, computer):
    reachable("https://api.nasa.gov/")
    tool = api_tool(api, "NASA", "https://api.nasa.gov", "query:api_key", "/neo/rest/v1/stats")
    key = tool_key(api, tool, "NASA_API_KEY", "DEMO_KEY")
    computer.start()
    feed = "/neo/rest/v1/feed?start_date=2026-11-01&end_date=2026-11-01"
    _, uses, conversation = turn(api, computer, f"CALL query:api_key NASA_API_KEY $TICO_TOOL_NASA_URL{feed}")
    call = one(uses, "call")
    answered(call["status"], "api.nasa.gov through the proxy")  # DEMO_KEY allows a few calls an hour per address
    assert call["status"] == 200 and "near_earth_objects" in call["body"], call
    with api.app.state.store.read() as c:
        logged = [r[0] for r in c.execute("SELECT path FROM tool_calls WHERE tool_id=?", (tool,))]
    assert logged == ["/neo/rest/v1/feed?start_date=&end_date=&api_key="], logged  # names, never values
    nowhere(api, computer, conversation, "DEMO_KEY", proxy_token(call))
    checked = rejected_after_a_wrong_key(api, tool, key, "DEMO_KEYX")
    assert (checked["status_code"], checked["detail"]) == (403, "Works (limited)"), checked  # 403: see reqres above


# ---- a client certificate as a file credential


def test_mtls_client_certificate_file_badssl(api, computer):
    reachable("https://client.badssl.com/")
    try:
        pem = httpx.get("https://badssl.com/certs/badssl.com-client.pem", timeout=15).raise_for_status().text
    except httpx.HTTPError as exc:
        pytest.skip(f"badssl.com's published client certificate did not download: {type(exc).__name__}")
    assert "BEGIN ENCRYPTED PRIVATE KEY" in pem
    credential(api, "BadSSL client certificate", "BADSSL_CLIENT_CERT", pem, kind="file")
    credential(api, "BadSSL certificate passphrase", "BADSSL_CERT_PASSPHRASE", "badssl.com", kind="password")
    computer.start()
    _, uses, conversation = turn(
        api, computer, "MTLS BADSSL_CLIENT_CERT BADSSL_CERT_PASSPHRASE https://client.badssl.com/"
    )
    used = one(uses, "mtls")
    assert used["present"], used
    answered(used["status"], "client.badssl.com")
    # Without the certificate badssl.com answers 400 "No required SSL certificate was sent".
    assert (used["status"], used["refused"]) == (200, 400), used
    key_line = pem.split("BEGIN ENCRYPTED PRIVATE KEY-----", 1)[1].split()[0]
    nowhere(api, computer, conversation, key_line)


# ---- signed requests: the bot signs with values it holds


def test_aws_sigv4_key_pair_on_computers_minio_play(api, computer):
    reachable("https://play.min.io/")
    secret = "zuf+tfteSlswRu7BJ86wekitnifILbZam1KYY3TG"  # MinIO's published play.min.io key pair
    tool = api_tool(api, "MinIO Play")
    tool_key(api, tool, "AWS_SECRET_ACCESS_KEY", secret)
    credential(api, "MinIO Play access key ID", "AWS_ACCESS_KEY_ID", "Q3AM3UQ867SPQQA43P2F")
    computer.start()
    marker = "SIGV4 AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY us-east-1/s3 https://play.min.io/"
    _, uses, conversation = turn(api, computer, marker)
    used = one(uses, "sigv4")
    assert used["present"], used
    answered(used["status"], "play.min.io")
    assert (used["status"], used["refused"]) == (200, 403) and "ListAllMyBucketsResult" in used["body"], used
    nowhere(api, computer, conversation, secret)


def test_oauth1_consumer_key_and_secret_on_computers_postman_echo(api, computer):
    reachable("https://postman-echo.com/get")
    secret = "D+EdQ-gs$-%@2Nu7"  # postman-echo's published consumer secret
    tool = api_tool(api, "Postman Echo OAuth")
    tool_key(api, tool, "OAUTH_CONSUMER_SECRET", secret)
    credential(api, "Postman Echo consumer key", "OAUTH_CONSUMER_KEY", "RKCGzna7bv9YD57c")
    computer.start()
    marker = "OAUTH1 OAUTH_CONSUMER_KEY OAUTH_CONSUMER_SECRET https://postman-echo.com/oauth1"
    _, uses, conversation = turn(api, computer, marker)
    used = one(uses, "oauth1")
    answered(used["status"], "postman-echo.com")
    assert (used["status"], used["refused"]) == (200, 401) and "verification was successful" in used["body"], used
    nowhere(api, computer, conversation, secret)


def test_hawk_on_computers_postman_echo(api, computer):
    reachable("https://postman-echo.com/get")
    secret = "werxhqb98rpaxn39848xrunpaw3489ruxnpa98w4rxn"  # postman-echo's published Hawk key
    tool = api_tool(api, "Postman Echo Hawk")
    tool_key(api, tool, "HAWK_KEY", secret)
    credential(api, "Postman Echo Hawk id", "HAWK_ID", "dh37fgj492je")
    computer.start()
    _, uses, conversation = turn(api, computer, "HAWK HAWK_ID HAWK_KEY https://postman-echo.com/auth/hawk")
    used = one(uses, "hawk")
    answered(used["status"], "postman-echo.com")
    assert (used["status"], used["refused"]) == (200, 401) and "Hawk Authentication Successful" in used["body"], used
    nowhere(api, computer, conversation, secret)


def test_webhook_secret_checks_githubs_documented_signature(api, computer):
    # docs.github.com "Validating webhook deliveries": this secret signs "Hello, World!" as this sha256.
    secret = "It's a Secret to Everybody"
    credential(api, "GitHub webhook secret", "GITHUB_WEBHOOK_SECRET", secret)
    computer.start()
    digest = "757107ea0eb2509fc211221cce984b8a37570b6d7586c22c46f4379c8b043e17"
    _, uses, conversation = turn(api, computer, f"HMAC GITHUB_WEBHOOK_SECRET {digest} Hello, World!")
    used = one(uses, "hmac")
    # The secret itself, never a proxy token: GitHub has a proxied tool, but a webhook secret is not its API key.
    assert used["environ"]["GITHUB_WEBHOOK_SECRET"] == secret
    assert (used["status"], used["refused"]) == ("valid", "invalid"), used
    nowhere(api, computer, conversation, secret)


# ---- logins the bot makes itself


def test_imap_login_on_computers_rebex(api, computer):
    try:
        socket.create_connection(("test.rebex.net", 993), timeout=10).close()
    except OSError as exc:
        pytest.skip(f"test.rebex.net:993 did not answer: {type(exc).__name__}")
    credential(api, "Rebex IMAP password", "IMAP_PASSWORD", "password", kind="password")
    computer.start()
    _, uses, _ = turn(api, computer, "IMAP test.rebex.net demo IMAP_PASSWORD")
    used = one(uses, "imap")
    if str(used["status"]).startswith("unreachable"):
        pytest.skip(
            f"the computer could not reach test.rebex.net from its own network ({used['status']}); login not tried"
        )
    # The demo password is the word "password", which shows in ordinary text: no `nowhere` check for it here.
    assert (used["status"], used["refused"]) == ("OK", "NO"), used


def test_session_cookie_login_on_computers_dummyjson(api, computer):
    reachable("https://dummyjson.com/test")
    credential(api, "DummyJSON password", "DUMMYJSON_PASSWORD", "emilyspass", kind="password")
    computer.start()
    marker = "SESSION DUMMYJSON_PASSWORD emilys https://dummyjson.com/auth/login https://dummyjson.com/auth/me"
    _, uses, conversation = turn(api, computer, marker)
    used = one(uses, "session")
    answered(used["status"], "dummyjson.com")
    assert (used["status"], used["me"], used["refused"]) == (200, 200, 400), used
    nowhere(api, computer, conversation, "emilyspass")


# ---- a remote MCP server


def test_mcp_server_with_no_auth_deepwiki(api, computer):
    reachable("https://mcp.deepwiki.com/")
    manifest = computer.config.parent.parent / "projects" / "bot-ops" / "bot.yaml"
    manifest.write_text(
        manifest.read_text()
        + "tools:\n  - service: deepwiki\n    mcp:\n      url: https://mcp.deepwiki.com/mcp\n      transport: http\n"
    )
    computer.start()
    marker = 'MCPCALL deepwiki read_wiki_structure {"repoName":"modelcontextprotocol/python-sdk"}'
    _, uses, _ = turn(api, computer, marker)
    used = one(uses, "mcp")
    assert used["present"], used
    for status in used["statuses"]:
        answered(status, "mcp.deepwiki.com")
    assert all(200 <= s < 300 for s in used["statuses"]) and "read_wiki_structure" in used["tools"], used
