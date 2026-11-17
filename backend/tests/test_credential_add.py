"""`tico credential add` (clients/commands/tools_admin.py): a key a caller already holds is stored from a variable or a
file on its own computer, through the Tools routes and their one rule, and its value is never in the tool's arguments,
its answer, an event, the database outside the vault, or a log."""

import json
import logging

import pytest

from backend.tests.helpers.api import assign, claim, get, headers, post, put, ready, runner
from backend.tests.helpers.api_tools import tool
from backend.tests.helpers.botops_parity import everything
from backend.tests.helpers.credentials import setup as vault
from backend.tests.helpers.mcp import call
from clients import hubtools
from clients.commands.tools_admin import secret_from
from clients.tico import APIError

SECRET = "acme-close-synthetic-0123456789abcdef"


class As:
    """A tool's `api`, answered by this server with one caller's token (an attempt's, for a bot's turn)."""

    def __init__(self, api, token):
        self.api, self.token = api, token

    def call(self, method, path, body=None, key=None, query=None, delegate=False):
        extra = {"X-Tico-On-Behalf-Of": delegate if isinstance(delegate, str) else "turn"} if delegate else {}
        json_body = None if method.upper() == "GET" else (body if body is not None else {})
        r = self.api.request(
            method, "/api/v2/" + path, json=json_body, params=query, headers={**headers(self.token, key), **extra}
        )
        if r.status_code >= 400:
            error = r.json()["error"]
            raise APIError(error["code"], error["detail"], status=r.status_code)
        return r.json()

    def get(self, path, /, **query):
        return self.call("GET", path, query=query)

    def post(self, path, body=None, key=None):
        return self.call("POST", path, body, key)

    def patch(self, path, body=None, key=None):
        return self.call("PATCH", path, body, key)


def run(api, token, name, args):
    return hubtools.BY_NAME[name]["fn"](As(api, token), args)


def turn(api, bot="ops"):
    """A turn of `bot` on a task Ana (a credential administrator) asked it for: (the computer, the attempt)."""
    machine = runner(api)
    assign(api, machine, bot)
    ready(api, machine, [bot])
    post(api, "tasks", {"owner": bot, "title": "Connect the Acme CRM for cpo", "body": "Store the new key."})
    return machine, claim(api, machine)


def test_a_key_held_in_a_variable_becomes_a_tools_key_at_a_level_and_never_shows(api, monkeypatch, caplog):
    vault(api)
    tid = tool(api)["id"]
    monkeypatch.setenv("NEW_CLOSE_KEY", SECRET)
    args = {"name": "Close (read)", "from_env": "NEW_CLOSE_KEY", "tool": "api:" + tid, "level": "read"}
    with caplog.at_level(logging.DEBUG):
        out = run(api, "ana-test", "tico_credential_add", {**args, "for_bot": "cpo"})
    assert SECRET not in json.dumps(args) and SECRET[:3] not in json.dumps(out) and "preview" not in json.dumps(out)
    page = get(api, f"api-tools/{tid}")
    assert [(k["name"], k["access_level"]) for k in page["keys"]] == [("Close (read)", "read")]
    assert {(a["subject"], a["level"]) for a in page["access"]} == {("bot:cpo", "read")}
    assert SECRET not in everything(api) and SECRET not in caplog.text
    # The detail a bot reads never carries a preview either.
    assert "preview" not in json.dumps(run(api, "ana-test", "tico_tool_detail", {"id": tid}))


def test_the_tools_owner_stores_a_key_from_a_file_for_a_bot_as_the_person_and_the_run_keeps_no_trace(api, tmp_path):
    vault(api)
    machine, attempt = turn(api)
    put(api, "page-owners/tools", {"bot": "ops"})
    # Whatever the run printed before it stored the key is scrubbed with it.
    events = {"events": [{"seq": 1, "kind": "message", "payload": {"text": f"made key {SECRET}"}}]}
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "t"}, machine["token"])
    post(api, f"attempts/{attempt['id']}/events", events, machine["token"])
    held = tmp_path / "key"
    held.write_text(SECRET + "\n")
    args = {"name": "Acme CRM", "env": "ACME_CRM_TOKEN", "from_file": str(held), "for_bot": "cpo"}
    out = run(api, attempt["token"], "tico_credential_add", args)
    assert out["granted"] is True and out["bot"] == "cpo" and SECRET not in json.dumps(out)
    with api.app.state.store.read() as c:
        holders = {
            r["subject"]
            for r in c.execute(
                "SELECT g.subject FROM credential_grants g JOIN credentials k ON k.id=g.credential_id "
                "WHERE k.env='ACME_CRM_TOKEN' AND g.revoked_at IS NULL"
            )
        }
        stored_by = c.execute("SELECT updated_by FROM credentials WHERE env='ACME_CRM_TOKEN'").fetchone()[0]
    assert holders == {"bot:cpo"} and stored_by == "human:ana"  # the person's act, via the Tools Owner
    assert SECRET not in everything(api)


def test_a_bot_without_the_right_gets_the_routes_refusal_naming_who_can_and_no_value_is_echoed(api, monkeypatch):
    vault(api)
    tid = tool(api)["id"]
    _, attempt = turn(api)  # ops is an ordinary bot here: not BotOps, not the Tools Owner
    monkeypatch.setenv("NEW_CLOSE_KEY", SECRET)
    for args in (
        {"name": "Close", "from_env": "NEW_CLOSE_KEY", "tool": tid},
        {"name": "Close", "env": "CLOSE_API_KEY", "from_env": "NEW_CLOSE_KEY", "for_bot": "cpo"},
    ):
        with pytest.raises(APIError) as refused:
            run(api, attempt["token"], "tico_credential_add", args)
        assert refused.value.status == 403 and SECRET not in str(refused.value)
        assert "BotOps" in str(refused.value) or "Ask" in str(refused.value), str(refused.value)
    assert get(api, f"api-tools/{tid}")["keys"] == [] and SECRET not in everything(api)


def test_a_value_is_read_on_the_computer_that_runs_the_command_and_errors_never_quote_it(monkeypatch, tmp_path):
    monkeypatch.setenv("HELD", SECRET)
    monkeypatch.delenv("UNSET_KEY", raising=False)
    crlf = tmp_path / "crlf"
    crlf.write_bytes((SECRET + "\r\n").encode())
    assert secret_from({"from_env": "HELD"}) == SECRET and secret_from({"from_file": str(crlf)}) == SECRET
    assert secret_from({}) is None
    (tmp_path / "empty").write_text("\n")
    for args in (
        {"from_env": "HELD", "from_file": str(crlf)},
        {"from_env": "UNSET_KEY"},
        {"from_file": str(tmp_path / "empty")},
        {"from_file": str(tmp_path / "missing")},
    ):
        with pytest.raises(ValueError) as bad:
            secret_from(args)
        assert SECRET not in str(bad.value)
    with pytest.raises(ValueError, match="never the value"):
        hubtools.BY_NAME["tico_credential_add"]["fn"](None, {"name": "Close"})


def test_the_servers_own_mcp_never_reads_its_environment_or_files_for_a_caller(api):
    for name in ("tico_credential_add", "tico_credential_update", "tico_tool_mcp_add", "tico_credential_login_add"):
        is_error, payload = call(api, name, {"name": "x", "credential": "x", "url": "x", "auth": "oauth", "tool": "x"})
        assert is_error and payload["error"] == "local_only", (name, payload)
