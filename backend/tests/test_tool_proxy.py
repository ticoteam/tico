"""The Tools proxy (backend/tool_proxy.py): a proxied tool's key never leaves the server, a token works only for its
tool and its live run, and the caller's level picks the key and the methods. The vendor is a local fake (MockTransport)."""
import base64
import ipaddress
import json

import httpx
import pytest

from backend import egress
from backend.store import H
from backend.tests.test_api import api, assign, claim, expire, get, post, ready, runner  # noqa: F401
from backend.tests.test_credentials import setup

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
        return httpx.Response(self.status, headers={"x-echo": got, "set-cookie": "vendor=1"},
                              json={"echo": got, "path": request.url.path, "query": str(request.url.query, "ascii")})


def tool(api, tid="close-crm", name="Close CRM", scheme="basic_user", delivery="proxy", base="https://api.close.example"):
    with api.app.state.store.transaction() as c:
        now = H.now()
        c.execute("INSERT INTO tools(id,name,service,base_url,auth_scheme,check_path,delivery,created_by,created_at,"
                  "updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (tid, name, "close", base, scheme, "/api/v1/me/", delivery, "human:ana", now, now))


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
    headers = {"Authorization": "Bearer " + token} if how == "bearer" else \
        {"Authorization": "Basic " + base64.b64encode((token + ":").encode()).decode()}
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


def test_header_query_and_twilio_style_schemes_rewrite_only_the_token(api):
    from backend import tool_proxy
    req = type("R", (), {})()
    req.headers = httpx.Headers({"x-api-key": "Token tpx1.abc.def", "cookie": "tico_session=s", "x-forwarded-for": "1"})
    out = dict(tool_proxy.outgoing_headers(req, {"auth_scheme": "header:X-Api-Key"}, {"username": ""}, "k-1", "header"))
    assert out["X-Api-Key"] == "Token k-1" and "cookie" not in out and "x-forwarded-for" not in out
    twilio = dict(tool_proxy.outgoing_headers(req, {"auth_scheme": "basic_password"}, {"username": "AC123"}, "k-2", "auth"))
    assert twilio["Authorization"] == "Basic " + base64.b64encode(b"AC123:k-2").decode()
    assert tool_proxy.outgoing_query("a=1&api_key=tpx1.abc.def", {"auth_scheme": "query:api_key"}, "k 3") == \
        "a=1&api_key=k%203"
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
