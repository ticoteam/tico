"""clients/tool_call.py, the bot side of a proxied tool call: what it sends."""

import base64
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from clients import tool_call


def token_for(tool):
    claims = base64.urlsafe_b64encode(json.dumps({"t": tool}).encode()).decode().rstrip("=")
    return tool_call.TOKEN_PREFIX + claims + ".sig"


def test_call_names_itself_so_cloudflare_lets_it_through():
    # Cloudflare in front of Tico answers Python's default User-Agent with 403 error 1010 before the proxy runs.
    seen = {}

    class Proxy(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.update(path=self.path, agent=self.headers.get("User-Agent"), auth=self.headers.get("Authorization"))
            body = b'{"ok": true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Proxy)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        token = token_for("sentry")
        environ = {
            "TICO_TOOL_SENTRY_URL": f"http://127.0.0.1:{server.server_port}/api/v2/tool-proxy/sentry",
            "SENTRY_READ_TOKEN": token,
        }
        out = tool_call.call("sentry", "GET", "/api/0/projects/", query=["query=is:unresolved"], environ=environ)
    finally:
        server.shutdown()
    assert out == {"status": 200, "content_type": "application/json", "body": {"ok": True}}
    assert seen["path"] == "/api/v2/tool-proxy/sentry/api/0/projects/?query=is%3Aunresolved"
    assert seen["auth"] == "Bearer " + token
    assert seen["agent"] == tool_call.USER_AGENT
    assert not seen["agent"].startswith("Python-urllib")


def test_a_write_carries_one_idempotency_key_and_a_read_none():
    """The proxy checks a write against the org rules only with an Idempotency-Key (backend/rule_links.py)."""
    seen = []

    class Proxy(BaseHTTPRequestHandler):
        def answer(self):
            seen.append(self.headers.get("Idempotency-Key"))
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_GET = do_POST = answer

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Proxy)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        environ = {
            "TICO_TOOL_CLOSE_URL": f"http://127.0.0.1:{server.server_port}/api/v2/tool-proxy/close",
            "CLOSE_KEY": token_for("close"),
        }
        tool_call.call("close", "GET", "/leads", environ=environ)
        tool_call.call("close", "POST", "/leads", {"name": "Acme"}, environ=environ)
        tool_call.call("close", "POST", "/leads", {"name": "Acme"}, environ=environ, idempotency_key="op-7")
    finally:
        server.shutdown()
    assert seen[0] is None and seen[1].startswith("tico-tool-call-") and seen[2] == "op-7"


def test_a_tool_whose_key_is_on_the_computer_is_told_to_use_its_variable_never_its_value():
    """AC-1: an agent read "jira is not a proxied tool in this run" as a refusal with $JIRA_API_TOKEN set."""
    environ = {"JIRA_API_TOKEN": "s3cret-token", "JIRA_BASE_URL": "https://acme.example", "TICO_JIRA_X": "no"}
    with pytest.raises(tool_call.CallError) as got:
        tool_call.call("jira", "GET", "/rest/api/3/myself", environ=environ)
    words = str(got.value)
    assert words == (
        "jira's access is on this computer, not behind Tico's proxy, so tico tool call cannot use it. Call the Jira "
        "API yourself with $JIRA_API_TOKEN (this run also has $JIRA_BASE_URL)."
    )
    assert "s3cret" not in words and "acme.example" not in words and "TICO_JIRA" not in words


def test_the_mcp_tool_names_the_variables_it_sees_and_never_says_none_are_set(monkeypatch):
    """AC-2. A harness starts the MCP server with Tico's own variables only (Codex among them), so a key in the agent's
    shell is not there: the answer then says how to look and gives both cases, never "no JIRA_ variable is set"."""
    from clients.commands import tools

    ask = {"tool": "jira", "method": "GET", "path": "/rest/api/3/myself"}
    for name in [k for k in os.environ if k.startswith(("JIRA_", "TICO_TOOL_"))]:
        monkeypatch.delenv(name)
    unseen = tools.tool_call(None, ask)["error"]
    assert unseen.startswith("jira is not proxied for this MCP server, which cannot see your shell's variables.")
    assert "env | grep ^JIRA_" in unseen and "call the Jira API yourself" in unseen and "tico tool show jira" in unseen
    assert "no JIRA_ variable is set" not in unseen
    monkeypatch.setenv("JIRA_API_TOKEN", "s3cret-token")
    monkeypatch.setenv("JIRA_BASE_URL", "https://acme.example")
    seen = tools.tool_call(None, ask)["error"]
    assert seen == tool_call.not_proxied("jira", os.environ)  # the CLI's words when the server does see them
    assert seen.startswith("jira's access is on this computer") and "s3cret" not in seen


def test_a_tool_id_with_two_words_falls_back_to_its_first_word():
    assert tool_call.own_variables("close-crm", {"CLOSE_CRM_KEY": "x", "CLOSE_OTHER": "y"}) == ["CLOSE_CRM_KEY"]
    assert tool_call.own_variables("close-crm", {"CLOSE_API_KEY": "x"}) == ["CLOSE_API_KEY"]
    assert "with $CLOSE_API_KEY." in tool_call.not_proxied("close-crm", {"CLOSE_API_KEY": "x"})


def test_no_proxy_and_no_variable_says_to_check_the_grant_and_lists_the_proxied_tools():
    """AC-3."""
    environ = {"TICO_TOOL_SENTRY_URL": "https://x/api/v2/tool-proxy/sentry", "JIRAFFE": "x"}
    words = tool_call.not_proxied("jira", environ)
    assert words == (
        "jira is not proxied in this run and no JIRA_ variable is set: check that this bot is granted jira "
        "(tico tool show jira). Proxied tools in this run: TICO_TOOL_SENTRY_URL."
    )


def test_a_proxied_tool_is_found_as_before():
    """AC-4: a tool with its proxy address goes through the proxy even when a variable is named after it."""
    token = token_for("jira")
    environ = {"TICO_TOOL_JIRA_URL": "https://x/api/v2/tool-proxy/jira/", "JIRA_API_TOKEN": token}
    assert tool_call.find("jira", environ) == ("https://x/api/v2/tool-proxy/jira", token)
