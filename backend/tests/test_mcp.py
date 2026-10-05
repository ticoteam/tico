"""The hub's MCP door: same tools as the `hub` CLI, same rules, no privileged path."""

import io
import json

import pytest

from backend.tests.test_api import api, assign, claim, get, headers, post, ready, runner, setup_attempt  # noqa: F401
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


def test_assignment_cli_tools_expose_the_existing_api_contract(api):
    names = {t["name"]: t for t in rpc(api, "tools/list")["result"]["tools"]}
    expected = {"hub_bot_assignment_list", "hub_bot_assignment_policy", "hub_bot_assignment_create",
                "hub_bot_assignment_update", "hub_bot_assignment_cleanup"}
    assert expected <= names.keys()
    create_schema = names["hub_bot_assignment_create"]["inputSchema"]
    assert set(create_schema["required"]) == {"source", "task", "name", "key"}
    assert create_schema["properties"]["generation"]["default"] == 1
    update_schema = names["hub_bot_assignment_update"]["inputSchema"]
    assert update_schema["properties"]["phase"]["enum"] == [
        "working", "waiting_review", "waiting_release", "paused", "interrupted", "verifying", "archived", "cancelled"
    ]
    assert "operation_id" in update_schema["properties"]

    def leaves(parser, prefix):
        groups = [a for a in parser._actions if getattr(a, "choices", None) and not isinstance(a.choices, (list, tuple))]
        if not groups:
            return {prefix}
        return {leaf for word, sub in groups[0].choices.items()
                for leaf in leaves(sub, prefix + "_" + word.replace("-", "_"))}
    cli_names = set()
    for action in hubcli.parser()._subparsers._group_actions:
        for word, sub in action.choices.items():
            cli_names |= leaves(sub, "hub_" + word.replace("-", "_"))
    assert expected <= cli_names
    # The HTTP contracts remain present alongside the MCP/CLI adapters.
    openapi = api.get("/api/v2/openapi.json", headers=headers()).json()
    assert "/api/v2/bots/{source}/assignment-branches" in openapi["paths"]
    assert "/api/v2/assignment-branches/{ident}" in openapi["paths"]
    assert "/api/v2/assignment-branches/{ident}/cleanup" in openapi["paths"]


def test_assignment_mcp_tools_reuse_api_authority_and_idempotency(api):
    from backend.tests.test_assignment_branches import delivery_task, prepare_source

    prepare_source(api)
    source = get(api, "bots/cpo")
    err, no_parent = call(api, "hub_bot_assignment_policy", {
        "source": "cpo", "enabled": True, "revision": source["revision"],
        "operation_id": "mcp-assignment-policy-1",
    })
    assert err and no_parent["error"] == "assignment_allocator"

    task = delivery_task(api, "MCP allocation remains task-scoped")
    arguments = {"source": "cpo", "task": task["id"], "name": "MCP Workflow Engineer",
                 "key": "mcp-workflow-1", "generation": 1,
                 "operation_id": "mcp-assignment-create-1"}

    # A member can see the same tool contract, but its ordinary API rights still decide the request.
    denied, refusal = call(api, "hub_bot_assignment_create", arguments, token="cara-test")
    assert denied and refusal["error"] in ("forbidden", "not_found")
    assert get(api, "bots/cpo/assignment-branches")["active"] == 0

    err, created = call(api, "hub_bot_assignment_create", arguments)
    assert not err and created["source_bot"] == "cpo" and created["task_id"] == task["id"]
    err, replay = call(api, "hub_bot_assignment_create", arguments)
    assert not err and replay["id"] == created["id"]

    err, listed = call(api, "hub_bot_assignment_list", {"source": "cpo"})
    assert not err and listed["active"] == 1
    assert [row["id"] for row in listed["assignments"]] == [created["id"]]

    err, paused = call(api, "hub_bot_assignment_update", {
        "assignment": created["id"], "revision": created["revision"], "phase": "paused",
        "note": "Record a safe synthetic checkpoint", "checkpoint": {"commit": "synthetic-head", "next": "resume"},
        "operation_id": "mcp-assignment-pause-1",
    })
    assert not err and paused["phase"] == "paused" and paused["checkpoint"]["commit"] == "synthetic-head"

    err, blocked = call(api, "hub_bot_assignment_cleanup", {
        "assignment": created["id"], "revision": paused["revision"],
        "operation_id": "mcp-assignment-cleanup-1",
    })
    assert err and blocked["error"] == "assignment_cleanup_phase"

    # The human-only policy remains protected both by the audience filter and by the API route.
    err, refused_human_policy = call(api, "hub_bot_assignment_policy", {
        "source": "cpo", "enabled": False, "revision": get(api, "bots/cpo")["revision"],
    }, token="cara-test")
    assert err and refused_human_policy["error"] == "forbidden"
    bot_token = setup_attempt(api, "ops")[2]["token"]
    err, refused_policy = call(api, "hub_bot_assignment_policy", {
        "source": "cpo", "enabled": True, "revision": get(api, "bots/cpo")["revision"],
    }, token=bot_token)
    assert err and refused_policy["error"] == "forbidden"


def test_tools_write_through_the_same_rules_as_http(api):
    r, msg, attempt = setup_attempt(api)
    token = attempt["token"]
    err, me = call(api, "hub_whoami", token=token)
    assert not err and me["actor"] == "bot:ops"

    err, out = call(api, "hub_message_send", {"to": "ops", "text": "Talking to myself"}, token=token)
    assert err and out["error"] == "self"

    err, out = call(api, "hub_task_create", {"owner": "ana", "title": "The thing", "body": "x"}, token=token)
    assert err and out["error"] == "lint"

    err, out = call(api, "hub_task_create", {"owner": "ana", "title": "The thing", "body": "x", "dry_run": True}, token=token)
    assert not err and out["ok"] is False and any("verb" in p for p in out["problems"])
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM tasks WHERE requester='bot:ops'").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM events WHERE action LIKE 'refus%' AND actor='bot:ops'").fetchone()[0] >= 1

    err, task = call(api, "hub_task_create", {"owner": "cpo", "title": "Review the runtime", "body": "Please.",
                                               "operation_id": "op-1"}, token=token)
    assert not err and task["task"]["owner"] == "bot:cpo"
    err, again = call(api, "hub_task_create", {"owner": "cpo", "title": "Review the runtime", "body": "Please.",
                                                "operation_id": "op-1"}, token=token)
    assert not err and again["task"]["id"] == task["task"]["id"]

    err, shown = call(api, "hub_task_show", {"id": task["task"]["id"]}, token=token)
    assert not err and shown["task"]["title"] == "Review the runtime"

    # A bot reads its conversation back itself: the hub rebuilds nothing into a session.
    err, page = call(api, "hub_conversation_show", {"conversation": msg["conversation_id"]}, token=token)
    assert not err and page["conversation"]["id"] == msg["conversation_id"]
    assert [m["body"] for m in page["messages"]][0] == msg["body"]
    assert (page["has_more"], page["next_before"]) == (False, None)

    # A bot sets up its own routine; the same key is the same routine.
    err, routine = call(api, "hub_routine_set", {"key": "audit", "title": "Daily audit", "cron": "0 7 * * 1-5",
                                                  "text": "Audit the rentals"}, token=token)
    assert not err and routine["id"] == "ops:audit" and routine["enabled"] == 1
    err, again = call(api, "hub_routine_set", {"key": "audit", "title": "Daily audit", "cron": "0 8 * * 1-5"}, token=token)
    assert not err and again["cron"] == "0 8 * * 1-5"
    err, listed = call(api, "hub_routine_list", {}, token=token)
    assert not err and [r["key"] for r in listed["result"]] == ["audit"]
    err, out = call(api, "hub_routine_set", {"key": "other", "title": "Not mine", "cron": "0 7 * * *", "bot": "cpo"}, token=token)
    assert err
    err, gone = call(api, "hub_routine_delete", {"id": "ops:audit"}, token=token)
    assert not err and gone["deleted_at"]
    err, listed = call(api, "hub_task_list", {"requester": "me"}, token=token)
    assert not err and [t["id"] for t in listed["result"]] == [task["task"]["id"]]

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
        ("hub_task_create", {"owner": "me", "title": 123, "body": "Details"}, "title"),
        ("hub_task_list", {"typo": "private-example-value"}, "typo"),
        ("hub_grokbot_sync", {"bots": [{"grok_id": "test", "name": "Example", "messages": [{"role": "bot", "text": 123}]}]}, "text"),
    ]:
        failed, payload = call(api, name, arguments)
        assert failed and payload["error"] == "validation" and field in payload["detail"]
        assert "private-example-value" not in payload["detail"]


def test_tool_usage_errors_and_local_tools_return_actionable_tool_errors(api):
    err, out = call(api, "hub_bot_model", {"bot": "ops", "model": "qa-unknown-model"})
    assert err and out["error"] == "usage" and "Models:" in out["detail"]
    err, out = call(api, "hub_bot_copy", {"bot": "ops"})
    assert err and out["error"] == "local_only" and "Computer" in out["detail"] and "BotOps" in out["detail"]
    err, out = call(api, "hub_whoami")
    assert not err and out["actor"] == "human:ana"


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


def test_personal_token_friendly_cleanup_archives_docs_files_and_deletes_meetings(api):
    from backend.tests.test_docs import make
    from backend.tests.test_files import publish
    from backend.tests.test_media import import_meeting
    token = post(api, "me/tokens", {"label": "QA cleanup"})["token"]
    doc = make(api, title="QA cleanup report")
    err, archived = call(api, "hub_doc_archive", {"ref": doc["path"]}, token=token)
    assert not err and archived["doc"]["archived"] is True
    _, _, attempt = setup_attempt(api)
    file = publish(api, attempt, name="qa-report.md").json()["file"]
    err, archived = call(api, "hub_file_archive", {"id": file["id"]}, token=token)
    assert not err
    with api.app.state.store.read() as c:
        assert c.execute("SELECT archived FROM bot_files WHERE id=?", (file["id"],)).fetchone()[0] == 1
    meeting = import_meeting(api, title="QA cleanup meeting")
    err, deleted = call(api, "hub_meeting_delete", {"id": meeting["id"]}, token=token)
    assert not err and deleted["ok"] and deleted["recoverable"]


@pytest.mark.parametrize("method,params", [("initialize", "bad"), ("initialize", []),
    ("tools/call", "bad"), ("tools/call", {"name": []}), ("tools/call", {"name": None})])
def test_malformed_mcp_params_are_json_rpc_errors(api, method, params):
    response = api.post("/api/v2/mcp", json={"jsonrpc": "2.0", "id": 7, "method": method, "params": params},
                        headers=headers())
    assert response.status_code == 200 and response.json()["error"]["code"] == -32602


def test_bodyless_delete_and_file_content_survive_the_mcp_adapter(api, monkeypatch):
    from backend.tests.test_files import publish
    from backend import mcp
    group = post(api, "groups", {"name": "QA cleanup"})
    err, deleted = call(api, "hub_api", {"method": "DELETE", "path": "groups/" + group["id"]})
    assert not err, deleted
    _, _, attempt = setup_attempt(api)
    file = publish(api, attempt, text="# QA content").json()["file"]
    err, read = call(api, "hub_api", {"method": "GET", "path": "files/" + file["id"]})
    assert not err and read["text"] == "# QA content" and not read["truncated"]
    monkeypatch.setattr(mcp, "MAX_CONTENT_BYTES", 4)
    err, read = call(api, "hub_api", {"method": "GET", "path": "files/" + file["id"]})
    assert not err and read["text"] == "# QA" and read["truncated"] and read["bytes"] == 4
    binary = api.post("/api/v2/files/uploads?name=sample.pdf", content=b"%PDF-1.7\n",
                      headers={**headers(attempt["token"]), "Content-Type": "application/pdf"}).json()["file"]
    err, read = call(api, "hub_api", {"method": "GET", "path": "files/" + binary["id"]})
    assert not err and read["base64"] == "JVBERg==" and read["truncated"]
    err, denied = call(api, "hub_api", {"method": "GET", "path": "files/" + file["id"]}, token="cara-test")
    assert err and denied["error"] == "not_found"


def test_starter_creation_uses_computer_build_and_preserves_requested_scope(api):
    err, made = call(api, "hub_bot_create", {"slug": "qa-meetings", "template": "meeting-notes",
                    "description": "Only test meetings; do not send outside the team.", "instructions": "# QA meetings"})
    assert not err and made["setup_task_id"] is None and made["reports_to"] == "human:ana", made
    with api.app.state.store.read() as c:
        config = json.loads(c.execute("SELECT config_json FROM bot_config WHERE bot='qa-meetings'").fetchone()[0])
        assert config["materialize"] is True and "# QA meetings" in config["instructions"]
        assert "Only test meetings; do not send outside the team." in config["instructions"]
    err, archived = call(api, "hub_bot_archive", {"bot": "qa-meetings"})
    assert not err
    err, refused = call(api, "hub_bot_create", {"slug": "qa-meetings"})
    assert err and refused["error"] == "duplicate" and "fresh slug" in refused["detail"]
    assert get(api, "bots/qa-meetings")["state"] == "archived"


def test_model_tool_forwards_harness_and_lists_current_settings(api):
    choices = get(api, "models")["models"]
    model = next(m for m in choices if m.get("harnesses") and not m.get("deprecated"))
    harness = model["harnesses"][0]
    if isinstance(harness, dict):
        harness = harness["id"]
    err, changed = call(api, "hub_bot_model", {"bot": "ops", "model": model["id"], "harness": harness})
    assert not err, changed
    err, listed = call(api, "hub_bot_model", {"bot": "ops"})
    assert not err and listed["harness"] == harness and "effort" in listed


def test_pipeline_tools_and_cli_use_type_and_step_contracts(api):
    err, types = call(api, 'hub_task_types')
    assert not err and types['result'][0]['name'] == 'General'
    err, created = call(api, 'hub_task_type_create', {'name': 'Marketing', 'steps': [
        {'name': 'Draft', 'status': 'open'}, {'name': 'Copy review', 'status': 'review'}]})
    assert not err
    typ = created['type']
    err, created = call(api, 'hub_task_create', {'owner': 'ops', 'title': 'Draft the launch copy',
        'body': 'Please.', 'type': typ['id']})
    assert not err
    err, moved = call(api, 'hub_task_update', {'id': created['task']['id'], 'step': 'Copy review'})
    assert not err and moved['task']['step']['name'] == 'Copy review' and moved['task']['status'] == 'review'
    args = hubcli.parser().parse_args(['task', 'update', created['task']['id'], '--step', 'Draft', '--type', 'Marketing'])
    assert args.step == 'Draft' and args.type == 'Marketing'
    assert hubcli.parser().parse_args(['task', 'types']).fn == 'task types'
