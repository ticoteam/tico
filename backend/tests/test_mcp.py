"""The hub's MCP door: same tools as the `hub` CLI, same rules, no privileged path."""

import base64
import hashlib
import json

import pytest

from backend.tests.test_api import api, assign, claim, get, headers, post, ready, restrict, runner, setup_attempt  # noqa: F401
from clients import hubcli, hubtools


def rpc(api, method, params=None, token="ana-test", rid=1):
    r = api.post("/api/v2/mcp", json={"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}},
                 headers=headers(token))
    assert r.status_code == 200, r.text
    return r.json()


def call(api, name, arguments=None, token="ana-test"):
    reply = rpc(api, "tools/call", {"name": name, "arguments": arguments or {}}, token=token)
    result = reply["result"]
    payload = result.get("structuredContent")
    return result["isError"], payload if payload is not None else json.loads(result["content"][0]["text"])


def test_initialize_lists_every_tool_and_ignores_notifications(api):
    init = rpc(api, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                   "clientInfo": {"name": "test", "version": "0"}})
    assert init["result"]["protocolVersion"] == "2025-06-18"
    assert init["result"]["capabilities"] == {"tools": {}}
    info = init["result"]["serverInfo"]
    assert info["name"] == "tico" and info["title"] == "Tico" and info["version"] == hubtools.release_version()
    r = api.post("/api/v2/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=headers())
    assert r.status_code == 202 and r.content == b""
    tools = rpc(api, "tools/list")["result"]["tools"]
    # A tool that reaches out to the internet (hub_doc_fetch) runs on the bot's computer, never here; the owner is offered
    # what a human may use (backend/tests/test_mcp_callers.py: the other callers).
    assert {t["name"] for t in tools} == {t["name"] for t in hubtools.listing(kind="owner")}
    assert "hub_doc_fetch" not in {t["name"] for t in tools} and len(tools) > 100
    assert all(t["inputSchema"]["type"] == "object" for t in tools)
    assert api.get("/api/v2/mcp", headers=headers()).status_code == 405
    assert api.post("/api/v2/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "resources/list"},
                    headers=headers()).json()["error"]["code"] == -32601


def test_mcp_needs_the_same_credential_as_http(api):
    r = api.post("/api/v2/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401


def test_every_cli_command_has_a_tool_of_the_same_name():
    """The schema is the standard; `hub <words>` is `hub_<words>` with underscores.

    A command that only works on the Mac (it writes the workspace) is listed in
    `hubtools.SHELL_ONLY`, so adding one is a decision, not an omission."""
    def leaves(parser, prefix):
        groups = [a for a in parser._actions if getattr(a, "choices", None) and not isinstance(a.choices, (list, tuple))]
        if not groups:
            return {prefix}
        return {leaf for word, sub in groups[0].choices.items() for leaf in leaves(sub, prefix + "_" + word.replace("-", "_"))}
    names = set()
    for action in hubcli.parser()._subparsers._group_actions:
        for word, sub in action.choices.items():
            names |= leaves(sub, "hub_" + word.replace("-", "_"))
    # Repository convenience commands select one of the explicit read/write MCP tools.
    for command, tools in hubtools.CLI_TOOL_ALIASES.items():
        if command in names:
            names.remove(command)
            names.update(tools)
    assert names - hubtools.SHELL_ONLY == set(hubtools.BY_NAME), names ^ set(hubtools.BY_NAME)
    assert hubtools.SHELL_ONLY <= names


def test_tools_write_through_the_same_rules_as_http(api):
    r, msg, attempt = setup_attempt(api)
    token = attempt["token"]
    err, me = call(api, "hub_whoami", token=token)
    assert not err and me["actor"] == "bot:ops"

    err, out = call(api, "hub_message_send", {"to": "ops", "text": "Talking to myself"}, token=token)
    assert err and out["error"] == "self"

    # Writing problems on an ask for a person are warnings: the task is made and the bot told how to improve.
    wordy = " ".join(["word"] * 150)
    err, out = call(api, "hub_task_create", {"owner": "ana", "title": "The thing", "body": wordy, "dry_run": True}, token=token)
    assert not err and out["ok"] is True and any("verb" in w for w in out["warnings"])
    err, out = call(api, "hub_task_create", {"owner": "ana", "title": "The thing", "body": wordy}, token=token)
    assert not err and out["task"]["owner"] == "human:ana" and any("150 words" in w for w in out["warnings"])
    wordy_task = out["task"]["id"]
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM tasks WHERE requester='bot:ops'").fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM events WHERE action LIKE 'refus%' AND actor='bot:ops'").fetchone()[0] >= 1

    err, task = call(api, "hub_task_create", {"owner": "cpo", "title": "Review the runtime", "body": "Please.",
                                               "operation_id": "op-1"}, token=token)
    assert not err and task["task"]["owner"] == "bot:cpo"
    err, again = call(api, "hub_task_create", {"owner": "cpo", "title": "Review the runtime", "body": "Please.",
                                                "operation_id": "op-1"}, token=token)
    assert not err and again["task"]["id"] == task["task"]["id"]

    err, shown = call(api, "hub_task_show", {"id": task["task"]["id"]}, token=token)
    assert not err and shown["task"]["title"] == "Review the runtime"
    err, many = call(api, "hub_task_show_many", {"ids": [task["task"]["id"], "no-such-task"]}, token=token)
    assert not err and [t["title"] for t in many["tasks"]] == ["Review the runtime"] and many["missing"] == ["no-such-task"]

    # A bot reads its conversation back itself: the hub rebuilds nothing into a session.
    err, page = call(api, "hub_conversation_show", {"conversation": msg["conversation_id"]}, token=token)
    assert not err and page["conversation"]["id"] == msg["conversation_id"]
    assert [m["body"] for m in page["messages"]][0] == msg["body"]
    assert (page["has_more"], page["next_before"]) == (False, None)

    err, listed = call(api, "hub_task_list", {"requester": "me"}, token=token)
    assert not err and sorted(t["id"] for t in listed["result"]) == sorted([task["task"]["id"], wordy_task])

    err, out = call(api, "hub_task_close", {"id": task["task"]["id"], "note": "Not needed"}, token=token)
    assert not err and out["task"]["status"] == "closed"
    err, out = call(api, "hub_task_show", {"id": "no-such-task"}, token=token)
    assert err and out["error"] == "not_found" and out["retryable"] is False


def test_tools_refuse_a_runner_credential_like_http_does(api):
    r = runner(api)
    err, out = call(api, "hub_message_list", token=r["token"])
    assert err and out["error"] == "identity"


def test_validation_identifies_fields_and_never_echoes_invalid_values(api):
    response = api.post("/api/v2/tasks", json={"owner": "me", "title": 123}, headers=headers())
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation" and "body.title" in error["detail"] and error["retryable"] is False
    for name, arguments, field in [
        ("hub_task_list", {"typo": "private-example-value"}, "typo"),
    ]:
        failed, payload = call(api, name, arguments)
        assert failed and payload["error"] == "validation" and field in payload["detail"]
        assert "private-example-value" not in payload["detail"]


def test_personal_token_api_bot_settings_and_archive_use_the_humans_rights(api):
    token = post(api, "me/tokens", {"label": "QA agent"})["token"]
    err, out = call(api, "hub_api", {"method": "GET", "path": "me"}, token=token)
    assert not err and out["actor"] == "human:ana"
    err, out = call(api, "hub_bot_update", {"slug": "ops", "description": "QA requested change"}, token=token)
    assert not err and "needs_confirm" not in out
    member = post(api, "me/tokens", {"label": "QA member"}, "cara-test")["token"]
    err, out = call(api, "hub_api", {"method": "PUT", "path": "access/rules", "body": {"member_tokens": False}}, token=member)
    assert err and out["error"] == "forbidden"
    err, out = call(api, "hub_bot_archive", {"bot": "ops"}, token=token)
    assert not err and out["status"] == "archived" and "needs_confirm" not in out


def test_assistant_tools_and_alias_always_use_the_token_humans_private_room(api):
    token = post(api, "me/tokens", {"label": "QA Assistant"})["token"]
    err, own = call(api, "hub_assistant_read", token=token)
    assert not err and own["room_id"]
    other = get(api, "assistant", "ben-test")
    err, sent = call(api, "hub_assistant_send", {"text": "Help"}, token=token)
    assert not err and sent["message"]["conversation_id"] == own["room_id"]
    err, sent = call(api, "hub_message_send", {"to": "assistant", "text": "Help"}, token=token)
    assert not err and sent["message"]["conversation_id"] == own["room_id"]
    post(api, "messages", {"to": "assistant", "text": "Help", "conversation_id": other["room_id"]}, token, expected=403)
    assert get(api, "assistant", "ben-test")["messages"] == other["messages"]


@pytest.mark.parametrize("method,params", [("initialize", "bad"), ("tools/call", {"name": []})])
def test_malformed_mcp_params_are_json_rpc_errors(api, method, params):
    response = api.post("/api/v2/mcp", json={"jsonrpc": "2.0", "id": 7, "method": method, "params": params},
                        headers=headers())
    assert response.status_code == 200 and response.json()["error"]["code"] == -32602


def test_a_bot_puts_one_decision_to_a_person_in_one_call(api):
    _, _, attempt = setup_attempt(api)
    ask = {"question": "Ship the September brief today?", "on": "ana", "operation_id": "q-1"}
    err, out = call(api, "hub_task_question", ask, token=attempt["token"])
    assert not err, out
    err, again = call(api, "hub_task_question", ask, token=attempt["token"])
    assert not err and again["task"]["id"] == out["task"]["id"], "a retry is the same question"
    # A question the person could not read is refused whole: no task is left behind.
    secret = post(api, "tasks", {"owner": "ops", "title": "Draft the settlement terms", "body": "Only us.", "private": True})
    err, refused = call(api, "hub_task_question", {"question": "Sign the settlement today?", "on": "ben", "private": True,
                                                   "parent": secret["id"]}, token=attempt["token"])
    assert err and refused["error"] == "privacy"
    # Nor to a person who may not read this bot's work: the question would reach them, the task would not.
    with api.app.state.store.transaction() as c:
        restrict(c, "ops", people=["ana"])
    err, hidden = call(api, "hub_task_question", {"question": "Renew the vendor contract today?", "on": "cara"},
                       token=attempt["token"])
    assert err and hidden["error"] == "privacy"
    # With a file, the same refusal comes before any bytes are stored.
    png = base64.b64encode(b"\x89PNG never stored").decode()
    picture = {"question": "Which banner?", "on": "cara", "options": [
        {"label": "A", "file": {"name": "a.png", "content_base64": png}}, {"label": "B"}]}
    r = api.post("/api/v2/task-questions", json=picture, headers=headers(attempt["token"]))
    assert r.status_code == 403 and not any(api.app.state.blobs.directory.rglob(hashlib.sha256(b"\x89PNG never stored").hexdigest()))
    r = api.post("/api/v2/task-questions", json={**picture, "on": "ana"}, headers=headers(attempt["token"], "pic-1"))
    assert r.status_code == 200 and r.json()["question"]["ask"]["questions"][0]["options"][0]["file"]
    again = api.post("/api/v2/task-questions", json={**picture, "on": "ana"}, headers=headers(attempt["token"], "pic-1"))
    assert again.status_code == 200 and again.json()["task"]["id"] == r.json()["task"]["id"]
    # A private question refused in the write's checks stores no bytes either.
    secret_png = base64.b64encode(b"\x89PNG private, never stored").decode()
    hidden_pick = {"question": "Which settlement banner?", "on": "cara", "private": True, "parent": secret["id"],
                   "options": [{"label": "A", "file": {"name": "a.png", "content_base64": secret_png}}, {"label": "B"}]}
    r = api.post("/api/v2/task-questions", json=hidden_pick, headers=headers(attempt["token"]))
    assert r.status_code == 403 and r.json()["error"]["code"] == "privacy", r.text
    assert not any(api.app.state.blobs.directory.rglob(hashlib.sha256(b"\x89PNG private, never stored").hexdigest()))
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM tasks WHERE title LIKE 'Which settlement banner%' "
                         "OR title LIKE 'Sign the settlement%' OR title LIKE 'Renew the vendor%'").fetchone()[0] == 0
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM tasks WHERE title LIKE 'Sign the settlement%' "
                         "OR title LIKE 'Renew the vendor%'").fetchone()[0] == 0
    made = out["task"]
    assert (made["owner"], made["requester"], made["status"], made["waiting_on"]) == ("bot:ops", "bot:ops", "waiting", "human:ana")
    assert [o["label"] for o in out["question"]["ask"]["questions"][0]["options"]] == ["Yes", "No"]
    item = next(i for i in get(api, "needs-you")["items"] if i["id"] == made["id"])
    assert item["kind"] == "question" and item["ask"]["from_actor"] == "bot:ops"


def test_a_private_question_goes_to_any_person_and_only_they_and_the_bot_read_it(api):
    """`private: true` is a private task whose two parties are the bot and the person asked, whoever started the run."""
    _, _, attempt = setup_attempt(api)          # Ana started this run; the question goes to Ben
    png = base64.b64encode(b"\x89PNG for ben").decode()
    ask = {"question": "Approve the Q3 bonus pool?", "on": "ben", "private": True,
           "options": [{"label": "Yes", "file": {"name": "pool.png", "content_base64": png}}, {"label": "No"}]}
    r = api.post("/api/v2/task-questions", json=ask, headers=headers(attempt["token"], "private-ben"))
    assert r.status_code == 200, r.text
    made = r.json()["task"]
    assert made["private"] and (made["owner"], made["requester"], made["waiting_on"]) == ("bot:ops", "human:ben", "human:ben")
    assert get(api, f"tasks/{made['id']}", token="ben-test")["task"]["id"] == made["id"]
    get(api, f"tasks/{made['id']}", token="cara-test", expected=404)
    get(api, f"tasks/{made['id']}", token="ana-test", expected=404)    # who started the run is not a party
    item = next(i for i in get(api, "needs-you", "ben-test")["items"] if i["id"] == made["id"])
    assert item["kind"] == "question"


def test_a_private_run_cannot_put_its_work_to_someone_outside_it(api):
    """A run on Ana's private task cannot put a question to Ben: it would carry what only Ana may read."""
    r = runner(api, "ana")
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    post(api, "tasks", {"owner": "ops", "title": "Plan the reorg", "body": "Only us.", "private": True})
    attempt = claim(api, r)
    err, refused = call(api, "hub_task_question", {"question": "Move the design team under product?", "on": "ben",
                                                   "private": True}, token=attempt["token"])
    assert err and refused["error"] == "privacy", refused
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM tasks WHERE title LIKE 'Move the design team%'").fetchone()[0] == 0
