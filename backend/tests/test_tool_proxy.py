"""The Tools proxy (backend/tool_proxy.py): a proxied tool's key never leaves the server, a token works only for its
tool and its live run, and the caller's level picks the key and the methods. The vendor is a local fake (MockTransport)."""

import base64
import ipaddress
import json
import uuid

import httpx
import pytest

from backend import api_tools, egress
from backend.store import H
from backend.tests.helpers.api import assign, claim, expire, get, post, ready, runner  # noqa: F401
from backend.tests.helpers.credentials import explicit_only, setup

FULL = "close-synthetic-full-key-4815162342"
READ = "close-synthetic-read-key-2718281828"
COMPUTER = "acme-synthetic-computer-key-1123581321"


ANSWERS = {}


@pytest.fixture(autouse=True)
def lookups(monkeypatch):
    """Name lookups answer from ANSWERS (a public address by default): no real DNS in tests."""
    ANSWERS.clear()
    monkeypatch.setattr(egress, "resolve", lambda host: [ipaddress.ip_address(ANSWERS.get(host, "93.184.215.14"))])


class Vendor:
    """A fake vendor API: records each request, and echoes the key it got back in a header and the body."""

    def __init__(self, status=200):
        self.seen, self.status = [], status

    def __call__(self, request):
        self.seen.append(request)
        auth = request.headers.get("authorization", "")
        got = base64.b64decode(auth[6:]).decode() if auth.startswith("Basic ") else auth
        return httpx.Response(
            self.status,
            headers={"x-echo": got, "set-cookie": "vendor=1"},
            json={"echo": got, "path": request.url.path, "query": str(request.url.query, "ascii")},
        )


def tool(
    api,
    tid="close-crm",
    name="Close CRM",
    scheme="basic_user",
    delivery="proxy",
    base="https://api.close.example",
    service="close",
):
    with api.app.state.store.transaction() as c:
        now = H.now()
        c.execute(
            "INSERT INTO tools(id,name,service,base_url,auth_scheme,check_path,delivery,created_by,created_at,"
            "updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (tid, name, service, base, scheme, "/api/v1/me/", delivery, "human:ana", now, now),
        )


def key(api, tid, secret, level="read_write", env="CLOSE_API_KEY", name=None):
    row = post(api, "credentials", {"name": name or f"{tid} {level}", "secret": secret, "env": env})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE credentials SET tool_id=?,access_level=? WHERE id=?", (tid, level, row["id"]))
    return row["id"]


def give(api, cid, subject, level="read_write"):
    grant = post(api, f"credentials/{cid}/grants", {"subject": subject})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE credential_grants SET access_level=? WHERE id=?", (level, grant["id"]))
    return grant


def run(api, bot="ops"):
    machine = runner(api)
    assign(api, machine, bot)
    ready(api, machine, [bot])
    post(api, "chat/" + bot, {"text": "Use only the synthetic fixture"})
    return machine, claim(api, machine)


def handed(api, attempt, env="CLOSE_API_KEY"):
    items = get(api, "credential-runtime", attempt["token"])["credentials"]
    return next(i for i in items if i["env"] == env)


def call(api, method, path, token, tid="close-crm", how="bearer", **kwargs):
    headers = (
        {"Authorization": "Bearer " + token}
        if how == "bearer"
        else {"Authorization": "Basic " + base64.b64encode((token + ":").encode()).decode()}
    )
    return api.request(method, f"/api/v2/tool-proxy/{tid}/{path}", headers=headers, **kwargs)


def dump(api, *tables):
    with api.app.state.store.read() as c:
        return json.dumps([[tuple(str(v) for v in r) for r in c.execute(f"SELECT * FROM {t}")] for t in tables])


def test_a_proxied_key_never_reaches_the_run_and_is_scrubbed_from_answers_and_records(api):
    setup(api)
    tool(api)
    tool(api, "acme-api", "Acme API", "bearer", delivery="computer", base="https://api.acme.example", service="acme")
    full = key(api, "close-crm", FULL)
    give(api, full, "bot:ops")
    give(api, key(api, "acme-api", COMPUTER, env="ACME_API_KEY"), "bot:ops")
    vendor = Vendor(status=401)
    api.app.state.tool_proxy_transport = httpx.MockTransport(vendor)
    machine, attempt = run(api)
    item = handed(api, attempt)
    # The run gets a token and the proxy's address, never the key; a computer tool still gets its value.
    assert item["value"].startswith("tpx1.") and FULL not in json.dumps(item)
    assert item["url_env"] == "TICO_TOOL_CLOSE_CRM_URL" and item["proxy_url"].endswith("/api/v2/tool-proxy/close-crm")
    assert handed(api, attempt, "ACME_API_KEY")["value"] == COMPUTER
    r = call(api, "GET", "api/v1/lead/?query=secret-search&_limit=5", item["value"], how="basic")
    assert r.status_code == 401
    sent = vendor.seen[-1]
    # basic_user: the key as the Basic user; the caller's token, cookies and forwarding headers never go on.
    assert sent.headers["authorization"] == "Basic " + base64.b64encode(f"{FULL}:".encode()).decode()
    # Sent to the address the name was checked at, with Host and SNI still the name.
    assert str(sent.url) == "https://93.184.215.14/api/v1/lead/?query=secret-search&_limit=5"
    assert sent.headers["host"] == "api.close.example" and sent.extensions["sni_hostname"] == "api.close.example"
    assert item["value"] not in str(sent.headers)
    # The vendor echoed the key in a header and its body: neither reaches the caller.
    assert FULL not in r.text and FULL not in str(r.headers) and "[key]" in r.text
    assert "set-cookie" not in r.headers
    assert FULL not in dump(api, "tool_calls", "events", "credential_uses", "idempotency")
    with api.app.state.store.read() as c:
        row = c.execute("SELECT * FROM tool_calls").fetchone()
    assert (row["result"], row["status_code"], row["method"], row["actor"]) == ("sent", 401, "GET", "bot:ops")
    assert row["path"] == "/api/v1/lead/?query=&_limit=" and row["credential_id"] == full
    with api.app.state.store.read() as c:
        assert c.execute("SELECT 1 FROM events WHERE action='credential.proxied' AND target=?", (full,)).fetchone()
        assert not c.execute("SELECT 1 FROM events WHERE action='credential.revealed' AND target=?", (full,)).fetchone()
    # A watcher on the bot's computer gets a token too, never the value.
    watched = get(api, "runner-watcher-credentials?bot=ops", machine["token"])["credentials"]
    close = next(i for i in watched if i["env"] == "CLOSE_API_KEY")
    assert close["value"].startswith("tpx1.") and FULL not in json.dumps(watched)
    assert call(api, "GET", "api/v1/me/", close["value"]).status_code == 401 and len(vendor.seen) == 2


def test_a_token_works_only_for_its_tool_and_its_live_run(api):
    setup(api)
    tool(api)
    tool(api, "stripe", "Stripe", "bearer", base="https://api.stripe.example")
    give(api, key(api, "close-crm", FULL), "bot:ops")
    give(api, key(api, "stripe", "stripe-synthetic-key-99", env="STRIPE_KEY"), "bot:ops")
    vendor = Vendor()
    api.app.state.tool_proxy_transport = httpx.MockTransport(vendor)
    _, attempt = run(api)
    token = handed(api, attempt)["value"]
    assert call(api, "GET", "api/v1/me/", token).status_code == 200
    # Another tool, a forged token, no token.
    assert call(api, "GET", "v1/balance", token, tid="stripe").status_code == 401
    body, mac = token[5:].split(".")
    claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    forged = base64.urlsafe_b64encode(json.dumps({**claims, "s": "bot:finance"}).encode()).rstrip(b"=").decode()
    assert call(api, "GET", "api/v1/me/", "tpx1." + forged + "." + mac).status_code == 401
    assert api.get("/api/v2/tool-proxy/close-crm/api/v1/me/").status_code == 401
    # A token signed for another bot's attempt does not carry this bot's grant.
    from backend import tool_proxy

    _, other = run(api, "finance")
    with api.app.state.store.transaction() as c:
        stolen = tool_proxy.mint(c, api.app.state.vault, "close-crm", "bot:ops", attempt=other["id"])
    assert call(api, "GET", "api/v1/me/", stolen).status_code == 401
    explicit_only(api)  # what explicit grants alone give
    # Taking the level away stops the next call, naming the setting.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE credential_grants SET revoked_at=? WHERE subject='bot:ops'", (H.now(),))
    r = call(api, "GET", "api/v1/me/", token)
    assert r.status_code == 403 and "Tools > Close CRM" in r.json()["error"]["detail"]
    # The run ended: refused, and nothing more reached the vendor.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE credential_grants SET revoked_at=NULL WHERE subject='bot:ops'")
    assert call(api, "GET", "api/v1/me/", token).status_code == 200
    expire(api, attempt["id"])
    r = call(api, "GET", "api/v1/me/", token)
    assert r.status_code == 401 and r.json()["error"]["code"] == "token_ended"
    assert len(vendor.seen) == 2


def test_read_level_picks_the_read_key_and_with_only_a_full_key_refuses_writes(api):
    setup(api)
    tool(api)
    full = key(api, "close-crm", FULL)
    give(api, full, "bot:ops", "read")
    give(api, full, "bot:finance", "read_write")
    vendor = Vendor()
    api.app.state.tool_proxy_transport = httpx.MockTransport(vendor)
    _, ops = run(api)
    token = handed(api, ops)["value"]
    assert call(api, "GET", "api/v1/me/", token).status_code == 200
    r = call(api, "POST", "api/v1/lead/", token, json={"name": "Acme"})
    assert r.status_code == 403
    assert r.json()["error"]["detail"].endswith("is Read: only GET calls. Add a read key or set Read and write.")
    assert len(vendor.seen) == 1
    with api.app.state.store.read() as c:
        refused = c.execute("SELECT * FROM tool_calls WHERE result='refused'").fetchone()
    assert (refused["method"], refused["access_level"], refused["status_code"]) == ("POST", "read", 403)
    # With a read key, a Read caller gets it (and the vendor decides what it may write); a full caller the full key.
    read = key(api, "close-crm", READ, level="read", env="CLOSE_READ_KEY")
    give(api, read, "bot:ops", "read")
    assert call(api, "POST", "api/v1/lead/", token, json={"name": "Acme"}).status_code == 200
    assert vendor.seen[-1].headers["authorization"] == "Basic " + base64.b64encode(f"{READ}:".encode()).decode()
    assert json.loads(vendor.seen[-1].content) == {"name": "Acme"}
    _, finance = run(api, "finance")
    assert call(api, "POST", "api/v1/lead/", handed(api, finance)["value"], json={}).status_code == 200
    assert vendor.seen[-1].headers["authorization"] == "Basic " + base64.b64encode(f"{FULL}:".encode()).decode()


def test_read_on_a_computer_tool_goes_through_the_proxy_unless_a_read_key_or_no_address(api):
    """A computer tool's keys reach runs as they are, except to a bot held at Read: the read key when there is one,
    else a proxy token so only reads get through; with no API address nothing can hold it to reads, and Who can use
    it says so."""
    setup(api)
    # no ads, payments or CRM tool: its write key goes as it is
    tool(api, name="Ledger", delivery="computer", base="https://api.ledger.example", service="ledger")
    full = key(api, "close-crm", FULL)
    give(api, full, "bot:ops", "read")
    give(api, full, "bot:finance", "read_write")
    vendor = Vendor()
    api.app.state.tool_proxy_transport = httpx.MockTransport(vendor)
    _, ops = run(api)
    item = handed(api, ops)
    assert item["value"].startswith("tpx1.") and item["url_env"] == "TICO_TOOL_CLOSE_CRM_URL"
    assert item["base_url"] == "https://api.ledger.example"
    assert call(api, "GET", "api/v1/me/", item["value"]).status_code == 200
    assert call(api, "POST", "api/v1/lead/", item["value"], json={"name": "Acme"}).status_code == 403
    _, finance = run(api, "finance")
    assert handed(api, finance)["value"] == FULL  # Read and write on a computer tool: the key itself
    access = lambda: {r["subject"]: r for r in get(api, "api-tools/close-crm")["access"]}  # noqa: E731 - read 3 times
    assert access()["bot:ops"].get("proxied_read") is True and "proxied_read" not in access()["bot:finance"]
    # A read key of the tool goes instead of the Read and write one, as it is: no longer through the proxy.
    read = key(api, "close-crm", READ, level="read", env="CLOSE_READ_KEY")
    give(api, read, "bot:ops", "read")
    items = get(api, "credential-runtime", ops["token"])["credentials"]
    assert [(i["env"], i["value"]) for i in items] == [("CLOSE_READ_KEY", READ)]
    assert "proxied_read" not in access()["bot:ops"]
    # No address to call through: the key itself, and the row is marked.
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM credential_grants WHERE credential_id=?", (read,))
        c.execute("UPDATE tools SET base_url='' WHERE id='close-crm'")
    assert handed(api, ops)["value"] == FULL
    rows = {r["subject"]: r for r in get(api, "api-tools/close-crm")["access"]}
    assert rows["bot:ops"].get("read_not_enforced") is True and "read_not_enforced" not in rows["bot:finance"]


class McpVendor:
    """A fake MCP server: answers JSON-RPC by method; its listing marks `search` read-only and `delete_issue` not."""

    def __init__(self, sse=False):
        self.seen, self.sse = [], sse

    def __call__(self, request):
        body = json.loads(request.content or b"{}")
        self.seen.append(body.get("method"))
        if body.get("method") == "tools/list":
            result = {
                "tools": [
                    {"name": "search", "annotations": {"readOnlyHint": True}},
                    {"name": "delete_issue", "annotations": {"destructiveHint": True}},
                ]
            }
        else:
            result = {}
        message = {"jsonrpc": "2.0", "id": body.get("id"), "result": result}
        if self.sse:
            return httpx.Response(
                200, headers={"content-type": "text/event-stream"}, text=f"data: {json.dumps(message)}\n\n"
            )
        return httpx.Response(200, json=message)


def test_a_read_callers_mcp_read_over_a_megabyte_still_passes(api):
    """The gate reads at most 1 MB; Read authorization still parses a larger MCP call to see that it only reads."""
    from backend import tool_proxy

    tool_proxy.READ_ONLY_TOOLS.clear()
    setup(api)
    tool(api, scheme="bearer", base="https://mcp.close.example")
    give(api, key(api, "close-crm", FULL), "bot:ops", "read")
    api.app.state.tool_proxy_transport = httpx.MockTransport(McpVendor())
    _, ops = run(api)
    token = handed(api, ops)["value"]
    assert call(api, "POST", "mcp", token, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).status_code == 200
    big = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "search", "q": "x" * (1024 * 1024)}}
    assert call(api, "POST", "mcp", token, json=big).status_code == 200


def test_an_mcp_read_over_a_megabyte_is_never_counted_as_a_write(api):
    """The gate reads Read authorization's parse whatever its size, so a Read caller's large MCP read at the tool's MCP
    address is no write; a Read and write caller's large POST is not parsed at all, so it is recorded unchecked,
    never judged or counted as a write."""
    from backend.tests.helpers.org_rules import org, rule

    setup(api)
    org(api)
    judge = api.app.state.judge = Judge()
    tool(api, scheme="bearer", base="https://mcp.close.example")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tools SET mcp_url='https://mcp.close.example/mcp' WHERE id='close-crm'")
    # A rule every write here would break: a judged write is refused, so a write never judged shows.
    who = {"bots": ["ops", "finance"]}
    rule(api, {"kind": "internal", "text": "Never write to Close CRM.", "who": who, "if_broken": "block"})
    full = key(api, "close-crm", FULL)
    give(api, full, "bot:ops", "read")
    give(api, full, "bot:finance", "read_write")
    api.app.state.tool_proxy_transport = httpx.MockTransport(McpVendor())
    _, ops = run(api)
    _, finance = run(api, "finance")
    big = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "search", "q": "x" * (1024 * 1024)}}

    def outcomes():
        with api.app.state.store.read() as c:
            rows = c.execute("SELECT action, detail FROM events WHERE action LIKE 'rules.%' ORDER BY rowid")
            return [(r[0], json.loads(r[1]).get("unchecked", "")) for r in rows]

    reader = handed(api, ops)["value"]
    assert call(api, "POST", "mcp", reader, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).status_code == 200
    assert call(api, "POST", "mcp", reader, json=big).status_code == 200
    assert outcomes() == [] and judge.read == []
    # The same large read sent anywhere else is a write the name-only check reads (its parse is not the gate's).
    assert call(api, "POST", "upload", reader, json=big).status_code == 200
    assert outcomes() == [("rules.refused", "")] and judge.read == ["POST to Close CRM"]
    writer = handed(api, finance)["value"]
    # At the MCP address, unparsed: unchecked, never judged. Anywhere else: the name-only check, which this rule refuses.
    assert call(api, "POST", "mcp", writer, json=big).status_code == 200
    assert outcomes()[1:] == [("rules.unchecked", "too_large")] and len(judge.read) == 1
    assert call(api, "POST", "upload", writer, json=big).status_code == 200
    assert outcomes()[1:] == [("rules.unchecked", "too_large"), ("rules.refused", "")]


@pytest.mark.parametrize("sse", [False, True])
def test_mcp_at_read_passes_set_up_listings_and_read_only_tools_and_refuses_the_rest(api, sse):
    from backend import tool_proxy

    tool_proxy.READ_ONLY_TOOLS.clear()
    setup(api)
    tool(api, scheme="bearer", base="https://mcp.close.example")
    give(api, key(api, "close-crm", FULL), "bot:ops", "read")
    vendor = McpVendor(sse)
    api.app.state.tool_proxy_transport = httpx.MockTransport(vendor)
    _, ops = run(api)
    token = handed(api, ops)["value"]

    def rpc(method, **params):
        return call(api, "POST", "mcp", token, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})

    assert rpc("initialize").status_code == 200
    assert (
        call(api, "POST", "mcp", token, json={"jsonrpc": "2.0", "method": "notifications/initialized"}).status_code
        == 200
    )
    # Before the listing nothing is known to be read-only.
    assert rpc("tools/call", name="search").status_code == 403
    assert rpc("tools/list").status_code == 200
    assert rpc("tools/call", name="search", arguments={"q": "x"}).status_code == 200
    r = rpc("tools/call", name="delete_issue")
    assert (
        r.status_code == 403
        and "the MCP tool delete_issue, which its server does not mark read-only" in r.json()["error"]["detail"]
    )
    batch = [
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "delete_issue"}},
    ]
    assert call(api, "POST", "mcp", token, json=batch).status_code == 403
    assert vendor.seen == ["initialize", "notifications/initialized", "tools/list", "tools/call"]
    # A Read and write caller is not filtered.
    give(api, key(api, "close-crm", FULL + "-w", env="CLOSE_W"), "bot:finance", "read_write")
    _, finance = run(api, "finance")
    w = call(
        api,
        "POST",
        "mcp",
        handed(api, finance, "CLOSE_W")["value"],
        json={"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "delete_issue"}},
    )
    assert w.status_code == 200


def test_header_query_and_twilio_style_schemes_rewrite_only_the_token(api):
    from backend import tool_proxy

    req = type("R", (), {})()
    req.headers = httpx.Headers({"x-api-key": "Token tpx1.abc.def", "cookie": "tico_session=s", "x-forwarded-for": "1"})
    out = dict(tool_proxy.outgoing_headers(req, {"auth_scheme": "header:X-Api-Key"}, {"username": ""}, "k-1", "header"))
    assert out["X-Api-Key"] == "Token k-1" and "cookie" not in out and "x-forwarded-for" not in out
    twilio = dict(
        tool_proxy.outgoing_headers(req, {"auth_scheme": "basic_password"}, {"username": "AC123"}, "k-2", "auth")
    )
    assert twilio["Authorization"] == "Basic " + base64.b64encode(b"AC123:k-2").decode()
    assert (
        tool_proxy.outgoing_query("a=1&api_key=tpx1.abc.def", {"auth_scheme": "query:api_key"}, "k 3")
        == "a=1&api_key=k%203"
    )
    assert tool_proxy.logged_path("v1/x", "api_key=tpx1.abc.def&b=2") == "/v1/x?api_key=&b="
    scrub = tool_proxy.Scrubber(["secret-key"])
    assert scrub.feed(b"a secr") + scrub.feed(b"et-key b") + scrub.end() == b"a [key] b"


def test_a_name_that_resolves_to_a_private_address_is_refused_and_nothing_is_sent(api):
    setup(api)
    tool(api)
    give(api, key(api, "close-crm", FULL), "bot:ops")
    vendor = Vendor()
    api.app.state.tool_proxy_transport = httpx.MockTransport(vendor)
    _, attempt = run(api)
    token = handed(api, attempt)["value"]
    for answer in ("127.0.0.1", "169.254.169.254", "10.0.0.5", "::1"):
        ANSWERS["api.close.example"] = answer
        r = call(api, "GET", "api/v1/me/", token)
        assert r.status_code == 403 and r.json()["error"]["code"] == "tool_address", answer
        assert "Change it on Tools > Close CRM" in r.json()["error"]["detail"]
    assert vendor.seen == []
    with api.app.state.store.read() as c:
        assert [r["result"] for r in c.execute("SELECT result FROM tool_calls")] == ["refused"] * 4
    # A local name is refused by its spelling, before any lookup.
    from backend import tool_proxy

    assert "Change it on Tools > Meta" in tool_proxy.address_problem("https://metadata.internal/", "Meta")


class Refusing:
    """A vendor that answers every call 401 with `reply`, and `challenge` as its WWW-Authenticate when it has one."""

    def __init__(self, reply=b"", challenge=""):
        self.reply, self.challenge, self.seen = reply, challenge, []

    def __call__(self, request):
        self.seen.append(request)
        headers = {"www-authenticate": self.challenge} if self.challenge else {}
        return httpx.Response(401, headers=headers, content=self.reply)


class Pieces(httpx.AsyncByteStream):
    """A vendor answer that arrives in pieces, as a stream does."""

    def __init__(self, parts):
        self.parts = parts

    async def __aiter__(self):
        for part in self.parts:
            yield part


# A restricted key's answer to a call its scope does not cover (ElevenLabs words it so); synthetic.
OUT_OF_SCOPE = b'{"detail":{"status":"missing_permissions"}}'


def health(api, tid="close-crm"):
    with api.app.state.store.read() as c:
        return api_tools.call_health(c, tid)


def test_three_out_of_scope_401s_are_no_rejected_key_and_three_plain_401s_are(api):
    setup(api)
    tool(api)
    give(api, key(api, "close-crm", FULL), "bot:ops")
    _, attempt = run(api)
    token = handed(api, attempt)["value"]
    api.app.state.tool_proxy_transport = httpx.MockTransport(Refusing(OUT_OF_SCOPE))
    for _ in range(3):
        assert call(api, "GET", "api/v1/lead/", token, how="basic").status_code == 401
    assert health(api) is None  # the vendor knows the key: three of these are no rejection
    with api.app.state.store.read() as c:
        assert [row[0] for row in c.execute("SELECT detail FROM tool_calls")] == [api_tools.MISSING_SCOPE] * 3
    assert "missing_permissions" not in dump(api, "tool_calls")  # the reply's text is never kept
    api.app.state.tool_proxy_transport = httpx.MockTransport(Refusing(b'{"detail":"Invalid API key"}'))
    for _ in range(3):
        assert call(api, "GET", "api/v1/lead/", token, how="basic").status_code == 401
    assert health(api) == "rejected"


def test_a_streamed_401_reaches_the_caller_unchanged_with_the_phrase_split_across_pieces(api):
    setup(api)
    tool(api)
    give(api, key(api, "close-crm", FULL), "bot:ops")
    _, attempt = run(api)
    token = handed(api, attempt)["value"]
    # The phrase is cut in two, and the body runs well past what the scope check reads.
    parts = [OUT_OF_SCOPE[:25], OUT_OF_SCOPE[25:], b"." * 1000, b"0123456789" * 500]
    api.app.state.tool_proxy_transport = httpx.MockTransport(lambda request: httpx.Response(401, stream=Pieces(parts)))
    r = call(api, "GET", "api/v1/lead/", token, how="basic")
    assert r.status_code == 401 and r.content == b"".join(parts)
    with api.app.state.store.read() as c:
        assert [row[0] for row in c.execute("SELECT detail FROM tool_calls")] == [api_tools.MISSING_SCOPE]


class Judge:
    """The org rule check's model (backend/org_rule_check.py): a rule is broken when what it reads says NEEDLE."""

    NEEDLE = "post to close crm"  # a write to Close CRM: what the check reads is names only

    def __init__(self):
        self.read = []

    def __call__(self, state, questions, label=None):
        self.read.append(state["output"])
        broken = self.NEEDLE in state["output"].lower()
        answers = {qid: {"type": "noul", "noul": 0.95 if broken else 0.05} for qid in questions}
        return {"model": "jev-1.14", "usage": {"input_tokens": 10, "output_tokens": 0}, "ms": 1, "answers": answers}


def test_an_agents_write_is_rule_checked_before_the_key_goes_in_logged_once_and_never_stopped(api):
    """Rules › Links, log-only (backend/rule_links.py): a write by a bot meets the org rules before the proxy puts the key
    in, and leaves one outcome event on the tool's link with the rule in it. A Block rule broken is a would-be refusal:
    the write still goes. What the check reads is names only, so no credential reaches it, and a read is never
    counted."""
    from backend.tests.helpers.org_rules import org, rule

    setup(api)
    org(api)
    judge = api.app.state.judge = Judge()
    tool(api, scheme="query:api_key")
    give(api, key(api, "close-crm", FULL), "bot:ops")
    made = rule(
        api, {"kind": "internal", "text": "Never write to Close CRM.", "who": {"bots": ["ops"]}, "if_broken": "block"}
    )
    vendor = Vendor()
    api.app.state.tool_proxy_transport = httpx.MockTransport(vendor)
    _, attempt = run(api)
    token = handed(api, attempt)["value"]
    basic = base64.b64encode((token + ":").encode()).decode()
    note = {"note": "Our favorite color is gold", "copy": token, "basic": basic}
    r = api.post(
        f"/api/v2/tool-proxy/close-crm/api/v1/lead/?api_key={token}&mode=x",
        headers={
            "Authorization": "Basic " + basic,
            "Cookie": "session=" + token,
            "X-Vendor-Auth": token,
            "Idempotency-Key": "lead-1",
        },
        json=note,
    )
    assert r.status_code == 200 and len(vendor.seen) == 1  # log-only: the would-be refusal still went
    assert "api_key=" + FULL in str(vendor.seen[0].url)  # the key went in after the check
    [read] = judge.read
    assert read == "POST to Close CRM\nFields: basic, copy, note"  # names only: no value, path, query or header
    with api.app.state.store.read() as c:
        rows = c.execute("SELECT actor, action, target, detail FROM events WHERE action LIKE 'rules.%'").fetchall()
    assert [(a, b, t) for a, b, t, _ in rows] == [("bot:ops", "rules.refused", "link:tool:close-crm")]
    detail = json.loads(rows[0]["detail"])
    assert (detail["rules"], detail["broken"], detail["would"]) == ([made["id"]], [made["id"]], True)
    assert token not in dump(api, "events", "tool_calls")

    def write(path, key=True, **kw):
        sent = {"Authorization": "Bearer " + token, **({"Idempotency-Key": uuid.uuid4().hex} if key else {})}
        return api.post("/api/v2/tool-proxy/close-crm/" + path, headers=sent, **kw)

    def logged():
        with api.app.state.store.read() as c:
            return [json.loads(r[0]) for r in c.execute("SELECT detail FROM events WHERE action LIKE 'rules.%'")]

    # A read is never counted.
    assert call(api, "GET", f"api/v1/lead/?api_key={token}", token).status_code == 200 and len(logged()) == 1
    # Its key goes in the query, so no call to it is ever exactly its MCP address: an MCP read there is checked too.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tools SET mcp_url='https://api.close.example/mcp' WHERE id='close-crm'")
    assert write("mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).status_code == 200
    assert len(logged()) == 2
    # With no Idempotency-Key (a stock MCP client, curl) a write is judged all the same; with one, a retry under it
    # is forwarded again but logs no second event.
    forwarded = len(vendor.seen)
    assert write("api/v1/lead/", key=False, json=note).status_code == 200
    with api.app.state.store.read() as c:
        last = c.execute("SELECT action FROM events WHERE action LIKE 'rules.%' ORDER BY rowid DESC").fetchone()
    assert last[0] == "rules.refused" and len(logged()) == 3  # judged (the same names: the cached verdict)
    again = {"Authorization": "Bearer " + token, "Idempotency-Key": "lead-2"}
    assert api.post("/api/v2/tool-proxy/close-crm/api/v1/lead/", headers=again, json=note).status_code == 200
    asked = len(judge.read)
    # The retry under that key reads a body the judge has not seen, and is still not judged: its event is logged.
    retried = {"name": "Acme", "owner": "ana"}
    assert api.post("/api/v2/tool-proxy/close-crm/api/v1/lead/", headers=again, json=retried).status_code == 200
    assert len(judge.read) == asked
    assert len(logged()) == 4 and len(vendor.seen) == forwarded + 3
    asked = len(judge.read)
    # A destination refused at its check is never judged: the address is checked and pinned first.
    ANSWERS["api.close.example"] = "10.0.0.5"
    assert write("api/v1/lead/", json=note).status_code == 403
    assert len(logged()) == 4 and len(judge.read) == asked


def test_only_an_mcp_read_sent_to_exactly_the_tools_mcp_address_is_not_a_write(api):
    setup(api)
    api.app.state.judge = Judge()
    tool(api, scheme="bearer", base="https://mcp.close.example")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tools SET mcp_url='https://mcp.close.example/mcp' WHERE id='close-crm'")
    give(api, key(api, "close-crm", FULL), "bot:ops")
    api.app.state.tool_proxy_transport = httpx.MockTransport(McpVendor())
    _, attempt = run(api)
    token = handed(api, attempt)["value"]
    listing = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    search = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "search"}}

    def send(path, body):
        sent = {"Authorization": "Bearer " + token, "Idempotency-Key": uuid.uuid4().hex}
        assert api.post("/api/v2/tool-proxy/close-crm/" + path, headers=sent, json=body).status_code == 200
        with api.app.state.store.read() as c:
            return c.execute("SELECT count(*) FROM events WHERE action LIKE 'rules.%'").fetchone()[0]

    for path in ("mcp", "mcp/", "mcp?session=1"):  # only the first is exactly the MCP address
        send(path, listing)
    assert send("mcp", search) == 2  # `search` is read-only by the listing the MCP address gave
    # The same id made again: the hints of the tool it replaced are not its own.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tools SET created_at='2099-01-01T00:00:00Z' WHERE id='close-crm'")
    assert send("mcp", search) == 3


def test_write_keys_of_ads_payments_and_crm_tools_reach_runs_only_through_the_proxy_and_meet_the_gate(api):
    """#4892: every Read and write grant on an ads, payments or CRM tool, old or new and an upgrade from Read alike,
    hands the run a proxy token even where the tool's keys go to computers: never the key, nor its reveal, and each
    write meets the Rules gate, shown on Rules › Links. A tool is one of these by its API's host too. Google Ads, signed
    on the computer, keeps its key and is Not covered; the report lists every such grant, changing nothing."""
    from dataclasses import replace

    from backend import proxy_keys
    from backend.auth import Identity
    from backend.store import Problem
    from backend.tests.helpers.api import headers
    from backend.tests.helpers.api_tools import level
    from backend.tests.helpers.org_rules import org, rule

    setup(api)
    org(api)
    judge = api.app.state.judge = Judge()
    tool(api, delivery="computer", base="https://api.close.com")  # Close CRM at its own API, keys on computers
    full = key(api, "close-crm", FULL)
    rule(
        api,
        {"kind": "internal", "text": "Never write to Close CRM.", "who": {"bots": ["finance"]}, "if_broken": "block"},
    )
    give(api, full, "bot:finance")
    give(api, full, "bot:ops", "read")

    def outcomes():
        with api.app.state.store.read() as c:
            return [r[0] for r in c.execute("SELECT target FROM events WHERE action LIKE 'rules.%' ORDER BY rowid")]

    vendor = Vendor()
    api.app.state.tool_proxy_transport = httpx.MockTransport(vendor)
    _, finance = run(api, "finance")
    item = handed(api, finance)
    assert item["value"].startswith("tpx1.") and FULL not in json.dumps(item)
    reveal = api.post(f"/api/v2/credentials/{full}/reveal", json={}, headers=headers(finance["token"]))
    assert reveal.status_code == 403 and reveal.json()["error"]["code"] == "proxied_key" and FULL not in reveal.text
    # Nor does an agent with a person's rights, even confirmed: only the person signed in themselves sees it.
    assistant = replace(Identity("human:ana", "owner"), via="assistant", confirmed=True)
    with api.app.state.store.transaction() as c, pytest.raises(Problem) as refused:
        api.app.state.vault.reveal(c, assistant, full)
    assert refused.value.code == "proxied_key"
    assert post(api, f"credentials/{full}/reveal", {})["value"] == FULL  # Ana herself
    assert call(api, "POST", "api/v1/lead/", item["value"], how="basic", json={"name": "Acme"}).status_code == 200
    assert vendor.seen[-1].headers["authorization"] == "Basic " + base64.b64encode(f"{FULL}:".encode()).decode()
    assert outcomes() == ["link:tool:close-crm"] and len(judge.read) == 1  # the write was judged (log-only)
    assert call(api, "POST", "api/v1/data/search/", item["value"], how="basic", json={"q": "x"}).status_code == 200
    assert outcomes() == ["link:tool:close-crm"]  # Close's search is a read
    # Read, then Read and write: still a proxy token, now one its writes go through on.
    _, ops = run(api)
    assert handed(api, ops)["value"].startswith("tpx1.")
    level(api, "close-crm", "bot:ops", "read_write")
    upgraded = handed(api, ops)["value"]
    assert upgraded.startswith("tpx1.") and call(api, "POST", "api/v1/lead/", upgraded, how="basic").status_code == 200
    # Rules › Links shows its outcomes, not Not covered.
    links = {r["id"]: r for r in get(api, "rule-links")["links"]}
    assert links["tool:close-crm"]["mode"] == "informed" and sum(links["tool:close-crm"]["counts"].values()) == 2
    # A "Billing" tool at Stripe's API host is Stripe's; Google Ads keeps its key (Not covered), never refused.
    tool(api, "billing", "Billing", "bearer", delivery="computer", base="https://api.stripe.com", service="")
    tool(
        api, "ads", "Ads", "bearer", delivery="computer", base="https://googleads.googleapis.com", service="google-ads"
    )
    give(api, key(api, "billing", "sk_synthetic_billing_0001", env="BILLING_KEY"), "bot:finance")
    give(api, key(api, "ads", "ads-synthetic-0002", env="GOOGLE_ADS_KEY"), "bot:finance")
    assert handed(api, finance, "BILLING_KEY")["value"].startswith("tpx1.")
    assert handed(api, finance, "GOOGLE_ADS_KEY")["value"] == "ads-synthetic-0002"
    links = {r["id"]: r for r in get(api, "rule-links")["links"]}
    assert links["tool:billing"]["mode"] == "informed" and links["tool:ads"]["mode"] == "not_covered"
    with api.app.state.store.read() as c:
        listed = proxy_keys.report(c)
    assert sorted((d["tool"], d["subject"]) for d in listed["proxied"]) == [
        ("billing", "bot:finance"),
        ("close-crm", "bot:finance"),
        ("close-crm", "bot:ops"),
    ]
    assert [(d["tool"], d["category"]) for d in listed["direct"]] == [("ads", "ads")]
    assert FULL not in json.dumps(listed) and [c["release"] for c in listed["computers"]] == ["", ""]


def test_hubspots_documented_reads_are_no_writes_and_a_neighbouring_write_still_meets_the_gate(api):
    from backend.tests.helpers.org_rules import org

    setup(api)
    org(api)
    api.app.state.judge = Judge()
    tool(api, "hubspot", "HubSpot", "bearer", base="https://api.hubapi.com", service="hubspot")
    tool(api, "elsewhere", "HubSpot copy", "bearer", base="https://api.hubapi.example", service="hubspot")
    give(api, key(api, "hubspot", "hubspot-synthetic-0003", env="HUBSPOT_KEY"), "bot:ops")
    give(api, key(api, "elsewhere", "hubspot-synthetic-0009", env="HUBSPOT_COPY_KEY"), "bot:ops")
    api.app.state.tool_proxy_transport = httpx.MockTransport(Vendor())
    _, ops = run(api)
    token = handed(api, ops, "HUBSPOT_KEY")["value"]

    def gated():
        with api.app.state.store.read() as c:
            return c.execute("SELECT count(*) FROM events WHERE action LIKE 'rules.%'").fetchone()[0]

    reads = (
        "crm/v3/objects/contacts/search",
        "crm/objects/2026-03/deals/search",
        "crm/v3/lists/search",
        "crm/lists/2026-03/search",
    )
    for path in reads:
        assert call(api, "POST", path, token, tid="hubspot", json={}).status_code == 200
    assert gated() == 0
    assert call(api, "POST", "crm/v3/objects/contacts/batch/create", token, tid="hubspot", json={}).status_code == 200
    assert call(api, "POST", "crm/lists/2026-03", token, tid="hubspot", json={}).status_code == 200  # makes a list
    assert gated() == 2
    # The same search anywhere but HubSpot's own API host is a write.
    copy = handed(api, ops, "HUBSPOT_COPY_KEY")["value"]
    assert call(api, "POST", "crm/v3/objects/contacts/search", copy, tid="elsewhere", json={}).status_code == 200
    assert gated() == 3


def test_the_write_key_policy_comes_first_by_service_or_known_host_and_its_exceptions_beat_the_stored_delivery(api):
    """One decision (credentials.held_at_read): a tool is an ads, payments or CRM tool by its service or its API's
    known host, never its name; that policy comes before the id heuristic and before the tool's own delivery, so a
    Meta tool named Campaigns gets a proxy token, a HubSpot token under an id-like variable is never handed as a
    setting, and Google Ads keeps its key even marked proxied: Not covered, and listed as direct."""
    from backend import proxy_keys

    setup(api)
    tool(api, "campaigns", "Campaigns", "bearer", delivery="computer", base="https://graph.facebook.com", service="")
    tool(api, "hubspot", "HubSpot", "bearer", delivery="computer", base="https://api.hubapi.com", service="hubspot")
    tool(api, "ads", "Ads", "bearer", delivery="proxy", base="https://googleads.googleapis.com", service="google-ads")
    give(api, key(api, "campaigns", "meta-synthetic-token-0004", env="CAMPAIGNS_TOKEN"), "bot:ops")
    hubspot = "1a2b3c4d-1a2b-3c4d-5e6f-1a2b3c4d5e6f"  # a private app token's id-like shape (made up)
    give(api, key(api, "hubspot", hubspot, env="HUBSPOT_ACCOUNT_ID"), "bot:ops")  # an id-like name on a write key
    give(api, key(api, "ads", "ads-synthetic-0005", env="GOOGLE_ADS_KEY"), "bot:ops")
    _, ops = run(api)
    assert handed(api, ops, "CAMPAIGNS_TOKEN")["value"].startswith("tpx1.")
    assert handed(api, ops, "HUBSPOT_ACCOUNT_ID")["value"].startswith("tpx1.")
    assert handed(api, ops, "GOOGLE_ADS_KEY")["value"] == "ads-synthetic-0005"
    links = {r["id"]: r for r in get(api, "rule-links")["links"]}
    assert links["tool:campaigns"]["mode"] == "informed" and links["tool:ads"]["mode"] == "not_covered"
    with api.app.state.store.read() as c:
        listed = proxy_keys.report(c)
    assert [d["tool"] for d in listed["direct"]] == ["ads"]
    assert sorted(d["tool"] for d in listed["proxied"]) == ["campaigns", "hubspot"]
    assert hubspot not in json.dumps(listed)


def test_the_write_key_policy_holds_for_default_reads_edits_and_one_time_moves_and_reads_exact_hosts(api):
    """Round three of #4892: a Google Ads tool with only a write key reads by default with nothing (its Read needs a
    real Read key, which then goes as it is and is listed); an agent may not take a protected tool's write keys out of
    the proxy, only a person signed in themselves; the one-time move of id-shaped keys never turns a protected write
    key into a setting; and a tool is classified only by a vendor's id, an explicit alias or its exact API host."""
    from backend import proxy_keys, tool_settings
    from backend import tool_services as TS
    from backend.tests.helpers.api import headers

    setup(api)
    tool(
        api, "ads", "Ads", "bearer", delivery="computer", base="https://googleads.googleapis.com", service="google-ads"
    )
    ads_write = key(api, "ads", "ads-synthetic-write-0006", env="GOOGLE_ADS_KEY")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tools SET everyone_reads=1 WHERE id='ads'")
    _, ops = run(api)
    items = get(api, "credential-runtime", ops["token"])["credentials"]
    assert "ads-synthetic-write-0006" not in json.dumps(items)  # Read never hands out a write key
    ads_read = key(api, "ads", "ads-synthetic-read-0007", level="read", env="GOOGLE_ADS_KEY")
    assert handed(api, ops, "GOOGLE_ADS_KEY")["value"] == "ads-synthetic-read-0007"
    give(api, ads_read, "bot:ops", "read")
    with api.app.state.store.read() as c:
        listed = proxy_keys.report(c)
    assert [(d["tool"], d["level"]) for d in listed["direct"]] == [("ads", "read")]
    assert ads_write not in json.dumps(listed)
    # A Stripe tool: an agent (the Assistant with Ana's rights) may not move its write keys out of the proxy.
    tool(api, "billing", "Billing", "bearer", delivery="computer", base="https://api.stripe.com", service="stripe")
    hubspot_id = "1a2b3c4d-1a2b-3c4d-5e6f-1a2b3c4d5e6f"  # an id-shaped value under an id-like name (made up)
    tool(api, "hubspot", "HubSpot", "bearer", delivery="proxy", base="https://api.hubapi.com", service="hubspot")
    protected = key(api, "hubspot", hubspot_id, env="HUBSPOT_ACCOUNT_ID")
    from dataclasses import replace

    from backend.auth import Identity
    from backend.store import Problem

    agent = replace(Identity("human:ana", "owner"), via="assistant", confirmed=True)
    with api.app.state.store.transaction() as c, pytest.raises(Problem) as refused:
        api_tools.update_tool(
            c, agent, "billing", api_tools.ToolUpdate(base_url=""), api.app.state.vault.admins, api.app.state.auth
        )
    assert refused.value.status == 403
    moved = api.patch("/api/v2/api-tools/billing", json={"base_url": ""}, headers=headers("ana-test"))
    assert moved.status_code == 200, moved.text  # Ana herself
    # The one-time move leaves the protected write key a key, never a setting.
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM registry_metadata WHERE key IN (?,?)", (tool_settings.MOVED, tool_settings.IDS_REPAIRED))
    tool_settings.move_once(api.app.state.store, api.app.state.vault)
    tool_settings.repair_ids_once(api.app.state.store, api.app.state.vault)
    with api.app.state.store.read() as c:
        assert c.execute("SELECT tool_id FROM credentials WHERE id=?", (protected,)).fetchone()[0] == "hubspot"
        assert not c.execute("SELECT 1 FROM tool_settings WHERE name='HUBSPOT_ACCOUNT_ID'").fetchone()
    # Exact names and hosts only.
    assert TS.gated_vendor("stripe-adapter", "") == "" and TS.gated_vendor("", "https://dashboard.stripe.com") == ""
    assert TS.gated_vendor("close", "") == "close-crm" and TS.gated_vendor("", "https://api.stripe.com/v1") == "stripe"


def test_a_bot_held_at_read_on_a_crm_tool_gets_its_read_key_alone(api):
    """A bot held at Read that holds both the Read key and the Read and write key of a CRM tool gets the Read key, as
    it is: the write key is skipped, never handed as a proxy token beside it."""
    setup(api)
    tool(api, delivery="computer")  # Close CRM
    full = key(api, "close-crm", FULL)
    read = key(api, "close-crm", READ, level="read", env="CLOSE_READ_KEY")
    give(api, full, "bot:ops", "read")
    give(api, read, "bot:ops", "read")
    _, ops = run(api)
    items = get(api, "credential-runtime", ops["token"])["credentials"]
    assert [(i["env"], i["value"]) for i in items] == [("CLOSE_READ_KEY", READ)]


def test_a_protected_tools_keys_and_reach_change_only_by_a_person_and_intake_keeps_its_write_keys(api):
    """Rule P: an agent, even confirmed with a person's rights, may not relabel a CRM or payments tool's write key as
    Read nor move it to another vendor; the person signed in may. A HubSpot write key under an id-like name stored in
    the vault stays a key on a tool with HubSpot's address, never an id's own addressless tool. A service given at
    create is kept as given, so "Stripe Adapter" at a dashboard host is not Stripe. The report says `unknown` where
    only a value would decide."""
    from dataclasses import replace

    from backend import proxy_keys
    from backend.auth import Identity
    from backend.store import Problem
    from backend.tests.helpers.api import headers

    setup(api)
    tool(api, "billing", "Billing", "bearer", delivery="computer", base="https://api.stripe.com", service="stripe")
    write = key(api, "billing", "sk_synthetic_billing_0010", env="BILLING_KEY")
    agent = replace(Identity("human:ana", "owner"), via="assistant", confirmed=True)
    vault = api.app.state.vault
    for change in (
        lambda c: api_tools.update_key(c, agent, vault, "billing", write, api_tools.KeyUpdate(access_level="read")),
        lambda c: api_tools.update_tool(
            c, agent, "billing", api_tools.ToolUpdate(service="brex"), vault.admins, api.app.state.auth
        ),
    ):
        with api.app.state.store.transaction() as c, pytest.raises(Problem) as refused:
            change(c)
        assert refused.value.status == 403
    relabel = api.patch(f"/api/v2/api-tools/billing/keys/{write}", json={"access_level": "read"}, headers=headers())
    assert relabel.status_code == 200, relabel.text  # Ana herself
    # Intake: a HubSpot write key under an id-like variable stays a key on a tool HubSpot's API reaches.
    stored = post(
        api,
        "credentials",
        {"name": "HubSpot", "env": "HUBSPOT_ACCOUNT_ID", "secret": "1a2b3c4d-1a2b-3c4d-5e6f-1a2b3c4d5e6f"},
    )
    with api.app.state.store.read() as c:
        home = c.execute(
            "SELECT t.* FROM credentials k JOIN tools t ON t.id=k.tool_id WHERE k.id=?", (stored["id"],)
        ).fetchone()
        assert home["service"] == "hubspot" and home["base_url"] == "https://api.hubapi.com"
        assert not c.execute("SELECT 1 FROM tool_settings WHERE name='HUBSPOT_ACCOUNT_ID'").fetchone()
    # A service given is kept: no Stripe for a "stripe-adapter" at a dashboard host.
    made = post(
        api,
        "api-tools",
        {"name": "Stripe Adapter", "service": "stripe-adapter", "base_url": "https://dashboard.stripe.com"},
    )
    assert made["service"] == "stripe-adapter"
    # A Read key under an id-like name: only its value says whether it goes as an id or a key.
    give(api, key(api, "billing", "acct_synthetic0012", level="read", env="STRIPE_ACCOUNT_ID"), "bot:ops", "read")
    with api.app.state.store.read() as c:
        adapter = c.execute("SELECT * FROM tools WHERE id=?", (made["id"],)).fetchone()
        assert api_tools.proxy_policy(adapter) is None  # its own delivery decides; an agent may change it
        listed = proxy_keys.report(c)
    assert [(d["tool"], d["env"], d["level"]) for d in listed["unknown"]] == [("billing", "STRIPE_ACCOUNT_ID", "read")]
