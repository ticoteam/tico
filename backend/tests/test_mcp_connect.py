"""Remote MCP servers as Tools (backend/mcp_connect.py): what a probe finds a server offers (a token only; OAuth with
dynamic client registration; OAuth with a client a person gives), the OAuth sign-in kept sealed on the server and
refreshed there, and the proxy putting the current access token on a run's MCP calls. The servers are local fakes
(httpx MockTransport); names resolve to a public address without DNS."""

import base64
import hashlib
import ipaddress
import json
import threading
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from backend import egress, mcp_connect, oauth_refresh
from backend.store import Store
from backend.tests.helpers.api import assign, claim, get, headers, post, put, ready, runner
from backend.tests.helpers.credentials import setup

MCP = "https://mcp.acme.example/mcp"
AUTH = "https://auth.acme.example"
TOKEN = "acme-synthetic-mcp-token-5150"
CLIENT_SECRET = "acme-synthetic-client-secret-8086"


@pytest.fixture(autouse=True)
def lookups(monkeypatch):
    monkeypatch.setattr(egress, "resolve", lambda host: [ipaddress.ip_address("93.184.215.14")])


class Server:
    """A fake MCP server (streamable HTTP answering in server-sent events, as Raygun's does) and, when `oauth`, its
    authorization server: protected-resource metadata, authorization-server metadata, registration when `dcr`, and a
    token endpoint that checks PKCE and rotates refresh tokens. `issued` are the access tokens it accepts."""

    def __init__(self, oauth=False, dcr=True, token=TOKEN):
        self.oauth, self.dcr = oauth, dcr
        self.issued = {token} if token else set()
        self.seen, self.codes, self.refreshes, self.n = [], {}, set(), 0
        self.client_auth = []
        self.extra = []  # more tools on the listing's second page
        self.hidden = set()  # names the vendor has dropped from its listing

    def mint(self):
        self.n += 1
        access, refresh = f"acme-synthetic-access-{self.n}", f"acme-synthetic-refresh-{self.n}"
        self.issued, self.refreshes = {access}, {refresh}
        return {"access_token": access, "refresh_token": refresh, "expires_in": 3600, "token_type": "Bearer"}

    def __call__(self, request):
        host, path = request.headers["host"], request.url.path
        self.seen.append((host, request.method, path, request.headers.get("authorization", "")))
        if host == "mcp.acme.example" and path == "/mcp":
            return self.mcp(request)
        if self.oauth and host == "mcp.acme.example" and path == "/.well-known/oauth-protected-resource/mcp":
            return httpx.Response(200, json={"resource": MCP, "authorization_servers": [AUTH]})
        if self.oauth and host == "auth.acme.example":
            return self.auth(request, path)
        return httpx.Response(404, json={"error": "not found"})

    def mcp(self, request):
        auth = request.headers.get("authorization", "")
        if auth[7:] not in self.issued:
            challenge = 'Bearer realm="acme"'
            if self.oauth:
                challenge += ', resource_metadata="https://mcp.acme.example/.well-known/oauth-protected-resource/mcp"'
            return httpx.Response(401, headers={"www-authenticate": challenge}, json={"error": "unauthorized"})
        message = json.loads(request.content)
        if "id" not in message:
            return httpx.Response(202)
        method = message["method"]
        if method == "initialize":
            result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}, "serverInfo": {"name": "acme"}}
        elif method == "tools/list" and not (message.get("params") or {}).get("cursor"):
            result = {
                "tools": [
                    {"name": "list_errors", "annotations": {"readOnlyHint": True}},
                    {"name": "resolve_error", "annotations": {"readOnlyHint": False}},
                ],
                "nextCursor": "page-2",
            }
        elif method == "tools/list":
            # count_errors only reads, but this vendor leaves its hint off (an administrator may mark it Only reads).
            listed = [{"name": "get_error", "annotations": {"readOnlyHint": True}}, {"name": "count_errors"}]
            result = {"tools": [t for t in listed + self.extra if t["name"] not in self.hidden]}
        else:
            result = {"content": [{"type": "text", "text": "3 errors"}]}
        data = json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result})
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream", "mcp-session-id": "acme-session"},
            content=f"event: message\ndata: {data}\n\n".encode(),
        )

    def auth(self, request, path):
        if path == "/.well-known/oauth-authorization-server":
            meta = {
                "issuer": AUTH,
                "authorization_endpoint": AUTH + "/authorize",
                "token_endpoint": AUTH + "/token",
                "code_challenge_methods_supported": ["S256"],
            }
            if self.dcr:
                meta["registration_endpoint"] = AUTH + "/register"
            return httpx.Response(200, json=meta)
        if path == "/register" and self.dcr:
            body = json.loads(request.content)
            assert body["redirect_uris"] == ["http://127.0.0.1:8765" + mcp_connect.CALLBACK_PATH]
            assert body["token_endpoint_auth_method"] == "none"
            return httpx.Response(201, json={"client_id": "acme-registered-client"})
        if path == "/token":
            self.client_auth.append(request.headers.get("authorization", ""))
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            if form["grant_type"] == "authorization_code":
                challenge = self.codes.pop(form.get("code"), None)
                digest = hashlib.sha256(form.get("code_verifier", "").encode()).digest()
                if challenge != base64.urlsafe_b64encode(digest).rstrip(b"=").decode():
                    return httpx.Response(400, json={"error": "invalid_grant"})
                assert form["resource"] == MCP
                return httpx.Response(200, json=self.mint())
            if form["grant_type"] == "refresh_token" and form.get("refresh_token") in self.refreshes:
                return httpx.Response(200, json=self.mint())
            return httpx.Response(400, json={"error": "invalid_grant"})
        return httpx.Response(404)

    def authorize(self, url):
        """The person's browser at the sign-in page: approve, and the code the server sends back with the state."""
        query = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        assert url.startswith(AUTH + "/authorize?") and query["code_challenge_method"] == "S256"
        code = f"acme-code-{len(self.codes) + 1}"
        self.codes[code] = query["code_challenge"]
        return query, code


@pytest.fixture
def server(api, monkeypatch):
    def make(**kwargs):
        fake = Server(**kwargs)
        transport = httpx.MockTransport(fake)
        monkeypatch.setattr(mcp_connect, "TRANSPORT", transport)
        api.app.state.tool_proxy_transport = transport
        setup(api)
        return fake

    return make


def run(api, bot="ops"):
    machine = runner(api)
    assign(api, machine, bot)
    ready(api, machine, [bot])
    post(api, "chat/" + bot, {"text": "Use only the synthetic fixture"})
    return claim(api, machine)


def mcp_call(api, server_item, token, method, params=None, session=None):
    """One MCP POST the way a harness sends it: to the server's run address, with the header its template names."""
    url = urlsplit(server_item["url"])
    hdrs = {k: v.replace("${" + server_item["env"] + "}", token) for k, v in server_item["headers"].items()}
    hdrs |= {"Accept": "application/json, text/event-stream", **({"Mcp-Session-Id": session} if session else {})}
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    return api.post(url.path, json=body, headers=hdrs)


def runtime(api, attempt):
    answer = get(api, "credential-runtime", attempt["token"])
    return answer["credentials"], answer.get("mcp_servers", [])


def test_a_token_only_server_is_offered_a_token_and_reaches_a_run_through_the_proxy_with_no_bot_yaml(api, server):
    fake = server(oauth=False)
    found = post(api, "mcp-tools/probe", {"url": MCP})
    assert (found["token"], found["oauth"], found["transport"]) == (True, False, "http")
    # No OAuth: a create asking for a sign-in is refused, a token one is checked at once.
    post(api, "mcp-tools", {"name": "Acme Errors", "url": MCP, "auth": "oauth"}, expected=422)
    made = post(api, "mcp-tools", {"name": "Acme Errors", "url": MCP, "auth": "token", "token": TOKEN})
    tool = get(api, "api-tools/" + made["id"])
    assert tool["tool"]["delivery"] == "proxy" and tool["status"]["text"] == "Working"
    assert tool["mcp"]["tools"] == 4 and tool["mcp"]["read_only"] == 2 and tool["mcp"]["auth"] == "token"
    put(api, f"api-tools/{made['id']}/access/bot:ops", {"level": "read_write"})
    attempt = run(api)
    items, servers = runtime(api, attempt)
    [item] = [i for i in items if i["env"] == "ACME_ERRORS_MCP_TOKEN"]
    [srv] = servers
    assert item["value"].startswith("tpx1.") and TOKEN not in json.dumps([items, servers])
    assert srv["proxied"] and srv["url"].endswith("/api/v2/tool-proxy/acme-errors/mcp")
    assert srv["headers"] == {"Authorization": "Bearer ${ACME_ERRORS_MCP_TOKEN}"}
    r = mcp_call(api, srv, item["value"], "tools/list")
    assert r.status_code == 200 and "list_errors" in r.text
    assert fake.seen[-1][3] == "Bearer " + TOKEN  # the proxy sent the stored token, never the run's
    # A computer delivery hands the server's own address with the key itself in the run's variable.
    r = api.patch(f"/api/v2/api-tools/{made['id']}", json={"delivery": "computer"}, headers=headers())
    assert r.status_code == 200, r.text
    items, servers = runtime(api, attempt)
    assert servers[0]["url"] == MCP and not servers[0]["proxied"]
    assert [i["value"] for i in items if i["env"] == "ACME_ERRORS_MCP_TOKEN"] == [TOKEN]


def test_an_oauth_server_with_registration_signs_in_and_its_tokens_never_leave_the_server(api, server):
    fake = server(oauth=True, dcr=True, token=None)
    found = post(api, "mcp-tools/probe", {"url": MCP})
    assert (found["token"], found["oauth"], found["dcr"]) == (True, True, True)
    made = post(api, "mcp-tools", {"name": "Acme Errors", "url": MCP, "auth": "oauth"})
    assert get(api, "api-tools/acme-errors")["status"]["text"] == "Not signed in"
    url = post(api, f"api-tools/{made['id']}/sign-in", {})["authorize_url"]
    query, code = fake.authorize(url)
    assert query["client_id"] == "acme-registered-client" and query["resource"] == MCP
    # The browser comes back with no Tico sign-in of its own: the state is the only right to finish.
    back = api.get(mcp_connect.CALLBACK_PATH, params={"code": code, "state": query["state"]}, follow_redirects=False)
    assert back.status_code == 302 and back.headers["location"] == "/#/tools/api/acme-errors"
    # The state works once.
    again = api.get(mcp_connect.CALLBACK_PATH, params={"code": code, "state": query["state"]}, follow_redirects=False)
    assert again.headers["location"] == "/#/tools"
    with api.app.state.store.read() as c:
        dump = json.dumps(
            [[str(v) for v in r] for t in ("credentials", "tools", "events") for r in c.execute(f"SELECT * FROM {t}")]
        )
        row = c.execute("SELECT * FROM credentials WHERE tool_id='acme-errors'").fetchone()
    assert "acme-synthetic-access-1" not in dump and "acme-synthetic-refresh-1" not in dump
    assert row["kind"] == "oauth" and row["updated_by"] == "human:ana"
    detail = get(api, "api-tools/acme-errors")
    assert detail["mcp"]["signed_in_by"] == "human:ana" and detail["mcp"]["client"] == "registered"
    # A sign-in is never revealed, never moved to computers.
    assert api.post(f"/api/v2/credentials/{row['id']}/reveal", headers=headers()).status_code == 403
    r = api.patch("/api/v2/api-tools/acme-errors", json={"delivery": "computer"}, headers=headers())
    assert r.status_code == 422
    put(api, "api-tools/acme-errors/access/bot:ops", {"level": "read_write"})
    items, servers = runtime(api, run(api))
    [item] = [i for i in items if i["env"] == "ACME_ERRORS_MCP_TOKEN"]
    assert item["value"].startswith("tpx1.") and "acme-synthetic-access" not in json.dumps([items, servers])
    r = mcp_call(api, servers[0], item["value"], "initialize")
    assert r.status_code == 200 and fake.seen[-1][3] == "Bearer acme-synthetic-access-1"
    assert "acme-synthetic-access-1" not in r.text


def test_an_ending_access_token_is_refreshed_on_the_server_and_a_refused_refresh_asks_to_sign_in_again(api, server):
    fake = server(oauth=True, dcr=True, token=None)
    post(api, "mcp-tools", {"name": "Acme Errors", "url": MCP, "auth": "oauth"})
    query, code = fake.authorize(post(api, "api-tools/acme-errors/sign-in", {})["authorize_url"])
    api.get(mcp_connect.CALLBACK_PATH, params={"code": code, "state": query["state"]}, follow_redirects=False)
    put(api, "api-tools/acme-errors/access/bot:ops", {"level": "read_write"})
    items, servers = runtime(api, run(api))
    token = next(i["value"] for i in items if i["env"] == "ACME_ERRORS_MCP_TOKEN")
    vault, store = api.app.state.vault, api.app.state.store

    def age(seconds):
        with store.transaction() as c:
            row = c.execute("SELECT * FROM credentials WHERE tool_id='acme-errors'").fetchone()
            held = json.loads(vault.cipher.decrypt(c, row))
            held["expires_at"] = seconds
            sealed, nonce = vault.cipher.encrypt(c, row["id"], json.dumps(held))
            c.execute("UPDATE credentials SET ciphertext=?, nonce=? WHERE id=?", (sealed, nonce, row["id"]))

    age(1)  # long ended
    r = mcp_call(api, servers[0], token, "tools/list")
    assert r.status_code == 200 and fake.seen[-1][3] == "Bearer acme-synthetic-access-2"
    # The server ends the token early: the proxy refreshes once and sends the call again.
    fake.issued = set()
    r = mcp_call(api, servers[0], token, "tools/list")
    assert r.status_code == 200 and fake.seen[-1][3] == "Bearer acme-synthetic-access-3"
    # The server will not refresh any more: the run is told to sign in again, and so is the tool's page.
    fake.refreshes = set()
    age(1)
    r = mcp_call(api, servers[0], token, "tools/list")
    assert r.status_code == 401 and r.json()["error"]["code"] == "tool_sign_in"
    assert get(api, "api-tools/acme-errors")["status"]["text"] == "Sign in again"
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM oauth_refresh_claims").fetchone()


def test_an_oauth_server_without_registration_takes_the_client_a_person_gives(api, server):
    fake = server(oauth=True, dcr=False, token=None)
    found = post(api, "mcp-tools/probe", {"url": MCP})
    assert (found["oauth"], found["dcr"]) == (True, False)
    r = api.post("/api/v2/mcp-tools", json={"name": "Acme Errors", "url": MCP, "auth": "oauth"}, headers=headers())
    assert r.status_code == 422 and mcp_connect.CALLBACK_PATH in r.json()["error"]["detail"]
    post(
        api,
        "mcp-tools",
        {"name": "Acme Errors", "url": MCP, "auth": "oauth", "client_id": "acme-own", "client_secret": CLIENT_SECRET},
    )
    query, code = fake.authorize(post(api, "api-tools/acme-errors/sign-in", {})["authorize_url"])
    assert query["client_id"] == "acme-own"
    api.get(mcp_connect.CALLBACK_PATH, params={"code": code, "state": query["state"]}, follow_redirects=False)
    assert not [s for s in fake.seen if s[2] == "/register"]
    # The secret went as HTTP Basic, and is kept sealed.
    assert fake.client_auth[-1] == "Basic " + base64.b64encode(f"acme-own:{CLIENT_SECRET}".encode()).decode()
    with api.app.state.store.read() as c:
        assert CLIENT_SECRET not in json.dumps([[str(v) for v in r] for r in c.execute("SELECT * FROM tools")])
    assert get(api, "api-tools/acme-errors")["mcp"]["client"] == "given"
    rows = post(api, "api-tools/acme-errors/check", {})["rows"]
    assert rows[0]["ok"] and get(api, "api-tools/acme-errors")["mcp"]["tools"] == 4


def test_a_read_caller_on_a_signed_in_tool_is_held_to_mcp_reads(api, server):
    fake = server(oauth=True, dcr=True, token=None)
    post(api, "mcp-tools", {"name": "Acme Errors", "url": MCP, "auth": "oauth"})
    query, code = fake.authorize(post(api, "api-tools/acme-errors/sign-in", {})["authorize_url"])
    api.get(mcp_connect.CALLBACK_PATH, params={"code": code, "state": query["state"]}, follow_redirects=False)
    put(api, "api-tools/acme-errors/access/bot:ops", {"level": "read"})
    items, servers = runtime(api, run(api))
    token = next(i["value"] for i in items if i["env"] == "ACME_ERRORS_MCP_TOKEN")
    # The sign-in is the only key, held at Read: listings pass (and teach the proxy which tools only read) ...
    assert mcp_call(api, servers[0], token, "tools/list").status_code == 200
    calls = len(fake.seen)
    # ... a read-only tool's call passes with the access token, and one that may write is refused before the server.
    r = mcp_call(api, servers[0], token, "tools/call", {"name": "list_errors", "arguments": {}})
    assert r.status_code == 200 and fake.seen[-1][3] == "Bearer acme-synthetic-access-1"
    r = mcp_call(api, servers[0], token, "tools/call", {"name": "resolve_error", "arguments": {}})
    assert r.status_code == 403 and "resolve_error" in r.json()["error"]["detail"]
    assert len(fake.seen) == calls + 1


def tico(api, *argv, token="ana-test"):
    from backend.tests.helpers.automation_cli import Client
    from clients import cli_spec, hubcli

    args = hubcli.parser().parse_args(list(argv))
    return cli_spec.of(args).call(Client(api, token), args)


def test_an_administrator_marks_an_unhinted_read_only_reads_and_a_read_bot_calls_it_after_a_restart(api, server):
    """A vendor that leaves readOnlyHint off a read: only an administrator marks it Only reads, only a name the last
    check listed, and a vendor-hinted name is already a read (not stored). The proxy reads the mark from the tool, so a
    bot at Read calls it with no listing seen since the server started; an unmarked tool is refused with who can mark
    it, and taking the mark off refuses the call again."""
    from backend import tool_proxy

    fake = server(oauth=False)
    post(api, "mcp-tools", {"name": "Acme Errors", "url": MCP, "auth": "token", "token": TOKEN})
    path = "/api/v2/api-tools/acme-errors"
    mark = lambda names, token="ana-test": api.patch(path, json={"only_reads": names}, headers=headers(token))  # noqa: E731
    r = mark(["count_errors"], "cara-test")
    assert r.status_code == 403 and r.json()["error"]["detail"] == "Only an administrator marks MCP tools Only reads"
    r = mark(["export_everything"])
    assert (
        r.status_code == 422
        and r.json()["error"]["detail"] == "Acme Errors's last check did not list export_everything"
    )
    assert get(api, "api-tools/acme-errors")["mcp"]["only_reads"] == []
    # Marked from the command line; the vendor-hinted name is already a read and is not stored.
    tico(api, "tool", "edit", "acme-errors", "--only-reads", "count_errors, list_errors")
    mcp = get(api, "api-tools/acme-errors")["mcp"]
    assert [(m["name"], m["by"], m["listed"]) for m in mcp["only_reads"]] == [("count_errors", "human:ana", True)]
    assert mcp["read_only_names"] == ["list_errors", "get_error"]
    with api.app.state.store.read() as c:
        rows = c.execute("SELECT actor, detail FROM events WHERE action='tool.only_reads_changed'").fetchall()
    assert [(r[0], json.loads(r[1])) for r in rows] == [("human:ana", {"added": ["count_errors"], "removed": []})]
    put(api, "api-tools/acme-errors/access/bot:ops", {"level": "read"})
    tool_proxy.READ_ONLY_TOOLS.clear()  # a restarted server: no tools/list seen, no vendor hint known
    items, servers = runtime(api, run(api))
    token = next(i["value"] for i in items if i["env"] == "ACME_ERRORS_MCP_TOKEN")
    assert mcp_call(api, servers[0], token, "tools/call", {"name": "count_errors", "arguments": {}}).status_code == 200
    # A mark counts only at the tool's exact MCP address: the same call sent elsewhere on its host is refused at Read.
    elsewhere = servers[0] | {"url": servers[0]["url"].rstrip("/") + "/other"}
    assert mcp_call(api, elsewhere, token, "tools/call", {"name": "count_errors", "arguments": {}}).status_code == 403
    r = mcp_call(api, servers[0], token, "tools/call", {"name": "resolve_error", "arguments": {}})
    assert r.status_code == 403 and r.json()["error"]["detail"].endswith(
        "is Read, and the MCP tool resolve_error is not marked read-only by its server. If it only reads, an "
        "administrator can mark it Only reads in Tools > Acme Errors; otherwise set Read and write."
    )
    count = lambda: mcp_call(api, servers[0], token, "tools/call", {"name": "count_errors", "arguments": {}})  # noqa: E731
    shown = lambda: [  # noqa: E731
        (m["by"], m["listed"], m["active"]) for m in get(api, "api-tools/acme-errors")["mcp"]["only_reads"]
    ]
    # The vendor drops the name: the next check suspends the mark. It stays with who made it, shown no longer listed and
    # unused, and the Read bot is refused; a whole-list save that keeps it is no new mark and leaves it unused.
    fake.hidden = {"count_errors"}
    assert post(api, "api-tools/acme-errors/check", {})["rows"][0]["ok"]
    assert shown() == [("human:ana", False, False)]
    assert count().status_code == 403
    assert mark(["count_errors"]).status_code == 200 and shown() == [("human:ana", False, False)]
    # The vendor lists it again: the old mark stays suspended, so the call is still refused until it is marked afresh.
    fake.hidden = set()
    post(api, "api-tools/acme-errors/check", {})
    assert shown() == [("human:ana", True, False)]
    assert count().status_code == 403
    # Changing a different mark, or a whole-list save that carries it forward now it is listed, never restores it.
    assert api.patch(path, json={"only_reads_on": ["resolve_error"]}, headers=headers("ana-test")).status_code == 200
    assert mark(["count_errors", "resolve_error"]).status_code == 200
    assert mark(["count_errors"]).status_code == 200
    assert shown() == [("human:ana", True, False)]
    assert count().status_code == 403
    with api.app.state.store.read() as c:
        rows = c.execute("SELECT actor, detail FROM events WHERE action='tool.only_reads_changed'").fetchall()
    assert ("system", {"added": [], "removed": [], "suspended": ["count_errors"]}) in [
        (r[0], json.loads(r[1])) for r in rows
    ]
    assert api.patch(path, json={"only_reads_on": ["count_errors"]}, headers=headers("ana-test")).status_code == 200
    assert shown() == [("human:ana", True, True)]
    assert count().status_code == 200
    # Taken off: the Read bot is refused again.
    assert mark([]).status_code == 200
    assert count().status_code == 403


def test_a_mark_shows_in_the_tools_activity_and_tool_show_and_a_name_over_200_characters_is_never_markable(api, server):
    """Who marked a tool Only reads is a row in the tool's activity (its page's recent list too); `tico tool show` lists
    the marks with who made them; and a vendor name longer than the listing keeps is left out of it, so neither it nor
    its first 200 characters can be marked."""
    from clients.commands.tools import integration_text

    fake = server(oauth=False)
    long = "count_" + "x" * 250
    fake.extra = [{"name": long}]
    post(api, "mcp-tools", {"name": "Acme Errors", "url": MCP, "auth": "token", "token": TOKEN})
    path = "/api/v2/api-tools/acme-errors"
    names = get(api, "api-tools/acme-errors")["mcp"]["names"]
    assert "count_errors" in names and not [n for n in names if n.startswith("count_x")]
    # The whole name is too long to send; its first 200 characters were never listed.
    assert api.patch(path, json={"only_reads": [long]}, headers=headers("ana-test")).status_code == 422
    r = api.patch(path, json={"only_reads": [long[:200]]}, headers=headers("ana-test"))
    assert r.status_code == 422 and r.json()["error"]["detail"] == f"Acme Errors's last check did not list {long[:200]}"
    tico(api, "tool", "edit", "acme-errors", "--only-reads", "count_errors")
    rows = get(api, "api-tools/acme-errors/activity")["rows"]
    assert [(r["kind"], r["actor"], r["result"], r["detail"]) for r in rows] == [
        ("mark", "human:ana", "marked", "Only reads on: count_errors")
    ]
    assert [r["detail"] for r in get(api, "api-tools/acme-errors")["recent"]] == ["Only reads on: count_errors"]
    # A filter by a call's result leaves marks out; taking the mark off is a row too, newest first.
    assert get(api, "api-tools/acme-errors/activity?result=refused")["rows"] == []
    tico(api, "tool", "edit", "acme-errors", "--only-reads", "")
    assert [r["detail"] for r in get(api, "api-tools/acme-errors/activity")["rows"]] == [
        "Only reads off: count_errors",
        "Only reads on: count_errors",
    ]
    tico(api, "tool", "edit", "acme-errors", "--only-reads", "count_errors")
    shown = tico(api, "tool", "show", "acme-errors")
    assert [(m["tool"], m["name"], m["by"]) for m in shown["only_reads"]] == [
        ("Acme Errors", "count_errors", "human:ana")
    ]
    text = integration_text(shown)
    assert (
        "## Only reads (1; marked by an administrator, `tico tool edit <tool> --only-reads-on` or `--only-reads-off` changes one)"
        in text
    )
    assert "  Acme Errors  count_errors  marked by human:ana" in text


def signed_in(api, server):
    """An OAuth tool signed in on the fake server: (the fake, the sign-in's credential id)."""
    fake = server(oauth=True, dcr=True, token=None)
    post(api, "mcp-tools", {"name": "Acme Errors", "url": MCP, "auth": "oauth"})
    query, code = fake.authorize(post(api, "api-tools/acme-errors/sign-in", {})["authorize_url"])
    api.get(mcp_connect.CALLBACK_PATH, params={"code": code, "state": query["state"]}, follow_redirects=False)
    with api.app.state.store.read() as c:
        return fake, c.execute("SELECT id FROM credentials WHERE tool_id='acme-errors'").fetchone()[0]


def read_tokens(api, cid):
    with api.app.state.store.transaction() as c:
        row = c.execute("SELECT * FROM credentials WHERE id=?", (cid,)).fetchone()
        return json.loads(api.app.state.vault.cipher.decrypt(c, row))


def write_tokens(api, cid, held):
    vault = api.app.state.vault
    with api.app.state.store.transaction() as c:
        sealed, nonce = vault.cipher.encrypt(c, cid, json.dumps(held))
        c.execute("UPDATE credentials SET ciphertext=?, nonce=? WHERE id=?", (sealed, nonce, cid))


def another_writer(store):
    with store.transaction() as c:
        c.execute("SELECT 1")


def test_two_refreshes_of_one_sign_in_at_once_send_its_refresh_token_once(api, server, monkeypatch):
    """Two callers (two stores on one database, as two processes have) find an ending access token at once. The vendor
    rotates refresh tokens and refuses a used one, so exactly one refresh may reach it, and both callers get the access
    token that refresh stored. While the vendor answers, another writer gets the database: no write lock is held."""
    fake, cid = signed_in(api, server)
    write_tokens(api, cid, {**read_tokens(api, cid), "expires_at": 1})  # long ended
    store, vault = api.app.state.store, api.app.state.vault
    other = Store(store.settings)
    original = fake.auth
    # (the vendor's status, whether another writer got the database during its answer, whether a second caller
    # found this refresh in flight before the vendor answered)
    seen = []
    found_in_flight = threading.Event()
    real_take = oauth_refresh.take

    def take_or_note(c, sign_in):
        holder = real_take(c, sign_in)
        if holder is None:  # another holder's refresh is live: this caller waits on it
            found_in_flight.set()
        return holder

    monkeypatch.setattr(oauth_refresh, "take", take_or_note)

    def slow_vendor(request, path):
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()} if path == "/token" else {}
        if form.get("grant_type") != "refresh_token":
            return original(request, path)
        writer = threading.Thread(target=another_writer, args=(other,))
        writer.start()
        writer.join(10)
        in_flight = found_in_flight.wait(10)  # the second caller arrives while this refresh is in flight
        response = original(request, path)
        seen.append((response.status_code, not writer.is_alive(), in_flight))
        return response

    fake.auth = slow_vendor
    results, errors = [], []

    def caller(s):
        try:
            results.append(mcp_connect.access_token(s, vault, cid))
        except Exception as exc:  # noqa: BLE001 - reported by the assertion below
            errors.append(exc)

    callers = [threading.Thread(target=caller, args=(s,)) for s in (store, other)]
    for thread in callers:
        thread.start()
    for thread in callers:
        thread.join(60)
    other.close()
    assert errors == [] and len(results) == 2
    assert seen == [(200, True, True)], seen
    stored = read_tokens(api, cid)
    assert results == [stored["access_token"], stored["access_token"]]
    assert stored["access_token"] in fake.issued and stored["refresh_token"] in fake.refreshes


def test_a_refresh_whose_claim_lapsed_keeps_the_tokens_another_refresh_stored(api, server):
    """A refresh whose claim lapsed while the vendor answered: another refresh has stored a newer refresh token
    meanwhile. Its answer is dropped, the stored tokens are used, and the newer refresh token is kept."""
    fake, cid = signed_in(api, server)
    write_tokens(api, cid, {**read_tokens(api, cid), "expires_at": 1})
    store, vault = api.app.state.store, api.app.state.vault
    original = fake.auth

    def lapse_then_newer_refresh(request, path):
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()} if path == "/token" else {}
        if form.get("grant_type") == "refresh_token":
            with store.transaction() as c:
                c.execute("UPDATE oauth_refresh_claims SET claimed_until='2000-01-01T00:00:00.000000Z'")
            write_tokens(
                api,
                cid,
                {
                    "access_token": "acme-newer-access",
                    "refresh_token": "acme-newer-refresh",
                    "expires_at": time.time() + 3600,
                },
            )
        return original(request, path)

    fake.auth = lapse_then_newer_refresh
    assert mcp_connect.access_token(store, vault, cid) == "acme-newer-access"
    assert read_tokens(api, cid)["refresh_token"] == "acme-newer-refresh"


def test_a_live_claim_makes_a_refresh_wait_and_a_lapsed_claim_is_taken_over(api, server, monkeypatch):
    """A refresh another holder has claimed is not sent again: the caller waits, then fails, and the vendor is not asked.
    A claim whose holder died lapses, and the next caller takes the sign-in over."""
    fake, cid = signed_in(api, server)
    write_tokens(api, cid, {**read_tokens(api, cid), "expires_at": 1})
    store, vault = api.app.state.store, api.app.state.vault
    monkeypatch.setattr(oauth_refresh, "WAIT_SECONDS", 0.5)
    monkeypatch.setattr(oauth_refresh, "POLL_SECONDS", 0.05)
    with store.transaction() as c:
        assert oauth_refresh.take(c, "credential:" + cid)
    asked = len([s for s in fake.seen if s[2] == "/token"])
    with pytest.raises(mcp_connect.Failed):
        mcp_connect.access_token(store, vault, cid)
    assert len([s for s in fake.seen if s[2] == "/token"]) == asked
    with store.transaction() as c:
        c.execute("UPDATE oauth_refresh_claims SET claimed_until='2000-01-01T00:00:00.000000Z'")
    assert mcp_connect.access_token(store, vault, cid).startswith("acme-synthetic-access-")


def test_a_switch_changes_only_its_own_name_so_a_stale_page_never_restores_a_removed_mark(api, server):
    """The Tools switch sends one name on or off: a page loaded before another administrator removed a mark, turning a
    different switch, leaves that removal in force. Only an administrator may send either, and not with a whole list."""
    server(oauth=False)
    post(api, "mcp-tools", {"name": "Acme Errors", "url": MCP, "auth": "token", "token": TOKEN})
    path = "/api/v2/api-tools/acme-errors"
    patch = lambda body, token="ana-test": api.patch(path, json=body, headers=headers(token))  # noqa: E731
    names = lambda: [m["name"] for m in get(api, "api-tools/acme-errors")["mcp"]["only_reads"]]  # noqa: E731
    assert patch({"only_reads": ["count_errors", "resolve_error"]}).status_code == 200
    # Another administrator removes count_errors; the stale page (still showing it on) turns resolve_error off.
    assert patch({"only_reads_off": ["count_errors"]}).status_code == 200
    assert patch({"only_reads_off": ["resolve_error"]}).status_code == 200
    assert names() == []
    assert patch({"only_reads_on": ["resolve_error"]}).status_code == 200
    assert names() == ["resolve_error"]
    r = patch({"only_reads_on": ["count_errors"]}, "cara-test")
    assert r.status_code == 403 and r.json()["error"]["detail"] == "Only an administrator marks MCP tools Only reads"
    assert patch({"only_reads_on": ["export_everything"]}).status_code == 422
    assert patch({"only_reads": [], "only_reads_off": ["resolve_error"]}).status_code == 422
    assert names() == ["resolve_error"]


def test_a_long_name_from_a_listing_made_before_names_were_kept_whole_needs_a_fresh_check(api, server):
    """A listing stored before names were kept whole cut long names to 200 characters: such a name may be a cut one the
    vendor never listed, so marking it waits for a fresh check (which keeps names whole and says so)."""
    fake = server(oauth=False)
    exact = "count_" + "y" * 194  # 200 characters: what an old listing would have cut a longer name to
    fake.extra = [{"name": exact}]
    post(api, "mcp-tools", {"name": "Acme Errors", "url": MCP, "auth": "token", "token": TOKEN})
    path = "/api/v2/api-tools/acme-errors"
    with api.app.state.store.transaction() as c:
        old = json.loads(c.execute("SELECT mcp_tools FROM tools WHERE id='acme-errors'").fetchone()[0])
        assert old.pop("exact") is True and exact in old["names"]
        c.execute("UPDATE tools SET mcp_tools=? WHERE id='acme-errors'", (json.dumps(old),))
    r = api.patch(path, json={"only_reads_on": [exact]}, headers=headers("ana-test"))
    assert r.status_code == 422 and r.json()["error"]["detail"].startswith("Check Acme Errors again before marking")
    # A short name in the same old listing was never cut, and is marked as before.
    assert api.patch(path, json={"only_reads_on": ["count_errors"]}, headers=headers("ana-test")).status_code == 200
    post(api, "api-tools/acme-errors/check", {})
    assert api.patch(path, json={"only_reads_on": [exact]}, headers=headers("ana-test")).status_code == 200
