"""The Tools proxy (backend/tool_proxy.py): a proxied tool's key never leaves the server, a token works only for its
tool and its live run, and the caller's level picks the key and the methods. The vendor is a local fake (MockTransport)."""

import base64
import ipaddress
import json

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
    api, tid="close-crm", name="Close CRM", scheme="basic_user", delivery="proxy", base="https://api.close.example"
):
    with api.app.state.store.transaction() as c:
        now = H.now()
        c.execute(
            "INSERT INTO tools(id,name,service,base_url,auth_scheme,check_path,delivery,created_by,created_at,"
            "updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (tid, name, "close", base, scheme, "/api/v1/me/", delivery, "human:ana", now, now),
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
    tool(api, "acme-api", "Acme API", "bearer", delivery="computer")
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
    tool(api, delivery="computer")
    full = key(api, "close-crm", FULL)
    give(api, full, "bot:ops", "read")
    give(api, full, "bot:finance", "read_write")
    vendor = Vendor()
    api.app.state.tool_proxy_transport = httpx.MockTransport(vendor)
    _, ops = run(api)
    item = handed(api, ops)
    assert item["value"].startswith("tpx1.") and item["url_env"] == "TICO_TOOL_CLOSE_CRM_URL"
    assert item["base_url"] == "https://api.close.example"
    assert call(api, "GET", "api/v1/me/", item["value"]).status_code == 200
    assert call(api, "POST", "api/v1/lead/", item["value"], json={"name": "Acme"}).status_code == 403
    _, finance = run(api, "finance")
    assert handed(api, finance)["value"] == FULL  # Read and write on a computer tool: the key itself
    # A read key of the tool goes instead of the Read and write one.
    read = key(api, "close-crm", READ, level="read", env="CLOSE_READ_KEY")
    give(api, read, "bot:ops", "read")
    items = get(api, "credential-runtime", ops["token"])["credentials"]
    assert [(i["env"], i["value"]) for i in items] == [("CLOSE_READ_KEY", READ)]
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

    NEEDLE = "favorite color is"

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
    the write still goes. What the check reads holds no credential in any spelling, and a read is never counted."""
    from backend.tests.helpers.org_rules import org, rule

    setup(api)
    org(api)
    judge = api.app.state.judge = Judge()
    tool(api, scheme="query:api_key")
    give(api, key(api, "close-crm", FULL), "bot:ops")
    color = "Never tell anyone Acme's favorite color."
    made = rule(api, {"kind": "internal", "text": color, "who": {"bots": ["ops"]}, "if_broken": "block"})
    vendor = Vendor()
    api.app.state.tool_proxy_transport = httpx.MockTransport(vendor)
    _, attempt = run(api)
    token = handed(api, attempt)["value"]
    basic = base64.b64encode((token + ":").encode()).decode()
    note = {"note": "Our favorite color is gold", "copy": token, "basic": basic}
    r = api.post(
        f"/api/v2/tool-proxy/close-crm/api/v1/lead/?api_key={token}&mode=x",
        headers={"Authorization": "Basic " + basic, "Cookie": "session=" + token, "X-Vendor-Auth": token},
        json=note,
    )
    assert r.status_code == 200 and len(vendor.seen) == 1  # log-only: the would-be refusal still went
    assert "api_key=" + FULL in str(vendor.seen[0].url)  # the key went in after the check
    [read] = judge.read
    for secret in (token, basic, token.split(".")[1], FULL):
        assert secret not in read
    assert "favorite color is gold" in read and "[token]" in read and "api_key=[key]&mode=x" in read
    with api.app.state.store.read() as c:
        rows = c.execute("SELECT actor, action, target, detail FROM events WHERE action LIKE 'rules.%'").fetchall()
    assert [(a, b, t) for a, b, t, _ in rows] == [("bot:ops", "rules.refused", "link:tool:close-crm")]
    detail = json.loads(rows[0]["detail"])
    assert (detail["rules"], detail["broken"], detail["would"]) == ([made["id"]], [made["id"]], True)
    assert token not in dump(api, "events", "tool_calls")
    # A read, and an MCP call that only reads sent to the tool's own MCP server, are never counted; the same call to
    # any other address is a write like any other POST.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tools SET mcp_url='https://api.close.example/mcp' WHERE id='close-crm'")
    assert call(api, "GET", f"api/v1/lead/?api_key={token}", token).status_code == 200
    listing = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    assert call(api, "POST", "mcp", token, json=listing).status_code == 200
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM events WHERE action LIKE 'rules.%'").fetchone()[0] == 1
    assert call(api, "POST", "api/v1/lead/", token, json=listing).status_code == 200
    assert call(api, "POST", "mcp/", token, json=listing).status_code == 200  # not the MCP address exactly
    assert call(api, "POST", "mcp?session=1", token, json=listing).status_code == 200  # nor with a query
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM events WHERE action LIKE 'rules.%'").fetchone()[0] == 4
    # A body over what the check reads is never copied or judged: recorded unchecked.
    asked = len(judge.read)
    assert call(api, "POST", "api/v1/lead/", token, content=b"x" * (1024 * 1024 + 1)).status_code == 200
    with api.app.state.store.read() as c:
        last = c.execute("SELECT detail FROM events WHERE action LIKE 'rules.%' ORDER BY rowid DESC").fetchone()
    assert json.loads(last[0])["unchecked"] == "too_large" and len(judge.read) == asked
    # A destination refused at its check is never judged: the address is checked and pinned first.
    ANSWERS["api.close.example"] = "10.0.0.5"
    asked = len(judge.read)
    assert call(api, "POST", "api/v1/lead/", token, json=note).status_code == 403
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM events WHERE action LIKE 'rules.%'").fetchone()[0] == 5
    assert len(judge.read) == asked
