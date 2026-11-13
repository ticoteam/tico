"""The hub's MCP door: same tools as the `tico` CLI, same rules, no privileged path."""

import asyncio
import base64
import hashlib
import json
import threading
from urllib.parse import urlencode

import pytest

from backend.mcp import ApiProblem, InProcessApi
from backend.tests.helpers.api import assign, claim, get, headers, post, ready, restrict, runner, setup_attempt  # noqa: F401
from clients import bot_files as BF, hubcli, hubtools
from clients.tico import MultipartBody
from backend import bot_settings as BS
from backend.tests.helpers.mcp import call, rpc


def test_initialize_lists_every_tool_and_ignores_notifications(api):
    init = rpc(
        api,
        "initialize",
        {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}},
    )
    assert init["result"]["protocolVersion"] == "2025-06-18"
    assert init["result"]["capabilities"] == {"tools": {}}
    info = init["result"]["serverInfo"]
    assert info["name"] == "tico" and info["title"] == "Tico" and info["version"] == hubtools.release_version()
    r = api.post("/api/v2/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=headers())
    assert r.status_code == 202 and r.content == b""
    tools = rpc(api, "tools/list")["result"]["tools"]
    # A tool that reaches out to the internet (tico_doc_fetch) runs on the bot's computer, never here; the owner is offered
    # what a human may use (backend/tests/test_mcp_callers.py: the other callers).
    assert {t["name"] for t in tools} == {t["name"] for t in hubtools.listing(kind="owner")}
    assert "tico_doc_fetch" not in {t["name"] for t in tools} and len(tools) > 100
    assert all(t["inputSchema"]["type"] == "object" for t in tools)
    assert api.get("/api/v2/mcp", headers=headers()).status_code == 405
    assert (
        api.post("/api/v2/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "resources/list"}, headers=headers()).json()[
            "error"
        ]["code"]
        == -32601
    )


THREAD_READS = {
    "tico_thread_list",
    "tico_thread_show",
    "tico_thread_search",
    "tico_thread_members_list",
    "tico_thread_home",
    "tico_thread_dm",
    "tico_thread_catch_up",
    "tico_thread_bot_status",
}
THREAD_WRITES = {
    "tico_thread_create",
    "tico_thread_post",
    "tico_thread_reply",
    "tico_thread_edit",
    "tico_thread_sub",
    "tico_thread_follow",
    "tico_thread_unfollow",
    "tico_thread_read",
    "tico_thread_members_add",
    "tico_thread_members_remove",
    "tico_thread_update",
    "tico_thread_link_slack",
    "tico_thread_dm_group",
    "tico_thread_react",
    "tico_thread_unreact",
    "tico_thread_open_earlier",
    "tico_thread_task",
    "tico_thread_delete",
    "tico_thread_upload",
    "tico_thread_slack_resume",
    "tico_thread_pin",
    "tico_thread_unpin",
}


def test_every_thread_tool_is_listed_with_a_schema_a_caller_can_check(api):
    tools = {t["name"]: t for t in rpc(api, "tools/list")["result"]["tools"]}
    assert THREAD_READS | THREAD_WRITES <= set(tools)
    for name in THREAD_READS | THREAD_WRITES:
        schema = tools[name]["inputSchema"]
        assert schema["additionalProperties"] is False, name
        assert set(schema.get("required", [])) <= set(schema["properties"]), name
        assert ("operation_id" in schema["properties"]) == (name in THREAD_WRITES), name
    assert "kind" not in tools["tico_thread_post"]["inputSchema"]["properties"]  # asks are gone: a post is a say
    assert tools["tico_thread_update"]["inputSchema"]["properties"]["audience"]["enum"] == [
        "members",
        "team",
        "company",
    ]
    assert "with" in tools["tico_thread_list"]["inputSchema"]["properties"]


class RecordingApi:
    """The API a thread tool calls, with each request recorded as (method, route, body) and no server behind it."""

    def __init__(self):
        self.calls = []

    def get(self, path, **query):
        self.calls.append(("GET", path, {k: v for k, v in query.items() if v is not None}))
        return {}

    def post(self, path, body=None, key=None):
        self.calls.append(("POST", path, body))
        return {}

    def call(self, method, path, body=None, key=None, query=None, delegate=False):
        self.calls.append((method, path, body))
        return {}


def test_thread_tools_reach_the_routes_of_the_contract():
    recorder = RecordingApi()
    run = lambda name, args: hubtools.BY_NAME[name]["fn"](recorder, args)
    run("tico_thread_home", {"actor": "bot:ops"})
    run("tico_thread_list", {"with": "human:ben", "limit": 5})
    run("tico_thread_dm_group", {"members": ["human:ana", "bot:assistant"], "body": "Hi"})
    run("tico_thread_dm_group", {"members": ["human:cara"]})
    run("tico_thread_react", {"message_id": "m7", "emoji": ":+1:"})
    run("tico_thread_unreact", {"message_id": "m7", "emoji": ":+1:"})
    run("tico_thread_open_earlier", {"thread_id": "th1"})
    run("tico_thread_task", {"message_id": "m7", "title": "Ship it", "owner": "bot:ops", "operation_id": "op1"})
    run("tico_thread_pin", {"thread": "th1"})
    run("tico_thread_unpin", {"thread": "th1"})
    run("tico_thread_catch_up", {"limit": 5})
    run("tico_thread_dm_group", {"members": ["human:ana", "bot:assistant"], "title": "Launch"})
    run("tico_thread_post", {"thread": "th1", "text": "Yes", "reply_to": "m7", "operation_id": "op2"})
    run("tico_thread_show", {"thread": "th1", "flat": True})
    run("tico_thread_bot_status", {"thread": "th1"})
    assert recorder.calls == [
        ("GET", "threads/home/bot:ops", {}),
        ("GET", "threads", {"with": "human:ben", "limit": 5}),
        ("POST", "threads/dm", {"members": ["human:ana", "bot:assistant"], "body": "Hi"}),
        ("POST", "threads/dm", {"members": ["human:cara"], "body": None}),
        ("PUT", "messages/m7/reactions/:+1:", None),
        ("DELETE", "messages/m7/reactions/:+1:", None),
        ("POST", "threads/th1/open-earlier", {}),
        ("POST", "threads/messages/m7/task", {"title": "Ship it", "owner": "bot:ops"}),
        ("PUT", "threads/th1/pin", None),
        ("DELETE", "threads/th1/pin", None),
        ("GET", "threads/catch-up", {"limit": 5}),
        ("POST", "threads/dm", {"members": ["human:ana", "bot:assistant"], "body": None, "title": "Launch"}),
        ("POST", "threads/th1/messages", {"text": "Yes", "kind": "say", "in_reply_to": "m7"}),
        ("GET", "threads/th1/timeline", {"flat": 1}),
        ("GET", "threads/th1/bot-status", {}),
    ]


def test_a_group_dm_needs_at_least_one_member(api):
    tools = {t["name"]: t for t in rpc(api, "tools/list")["result"]["tools"]}
    schema = tools["tico_thread_dm_group"]["inputSchema"]
    assert schema["required"] == ["members"] and schema["properties"]["members"]["minItems"] == 1
    isError, payload = call(api, "tico_thread_dm_group", {"members": []})
    assert isError is True
    assert payload["error"] == "validation" and "members" in payload["detail"]


def test_a_task_refusal_reads_the_same_from_the_in_process_api_as_from_http():
    """The in-process door keeps what a task refusal carries beside its rule: what is missing, the commands allowed
    next and the version the task has now, from either shape of error body."""
    from backend.mcp import problem

    refusal = {"error": "task_not_met", "detail": "Not moved.", "missing": ["Owner says done"], "next": ["x"]}
    flat = problem(refusal, 409, "k1")
    nested = problem(
        {"error": {"code": "task_changed", "detail": "Changed since you looked.", "version": 13}}, 409, "k2"
    )
    assert (flat.code, flat.detail, flat.status, flat.extra) == (
        "task_not_met",
        "Not moved.",
        409,
        {"missing": ["Owner says done"], "next": ["x"]},
    )
    assert (nested.code, nested.extra) == ("task_changed", {"version": 13})
    assert (
        hubtools.error_payload(flat)["missing"] == ["Owner says done"]
        and hubtools.error_payload(nested)["version"] == 13
    )


def test_tools_write_through_the_same_rules_as_http(api):
    r, msg, attempt = setup_attempt(api)
    token = attempt["token"]
    err, me = call(api, "tico_whoami", token=token)
    assert not err and me["actor"] == "bot:ops"

    err, out = call(api, "tico_message_send", {"to": "ops", "text": "Talking to myself"}, token=token)
    assert err and out["error"] == "self"

    # A person's task opens with a plain ask, or it is refused with the rule and an example the bot can act on.
    wordy = " ".join(["word"] * 150)
    err, out = call(api, "tico_task_create", {"owner": "ana", "title": "The thing", "body": wordy}, token=token)
    assert err and "Your ask to ana is 150 words" in json.dumps(out) and "Example:" in json.dumps(out)
    # Other writing problems on it are warnings: the task is made and the bot told how to improve.
    wordy = "Approve the thing? If yes, I'll ship it.\n\n" + wordy
    err, out = call(
        api, "tico_task_create", {"owner": "ana", "title": "The thing", "body": wordy, "dry_run": True}, token=token
    )
    assert not err and out["ok"] is True and any("verb" in w for w in out["warnings"])
    err, out = call(api, "tico_task_create", {"owner": "ana", "title": "The thing", "body": wordy}, token=token)
    assert not err and out["task"]["owner_id"] == "human:ana" and any("words outside" in w for w in out["warnings"])
    wordy_task = out["task"]["id"]
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM tasks WHERE requester_id='bot:ops'").fetchone()[0] == 1
        assert (
            c.execute("SELECT count(*) FROM events WHERE action LIKE 'refus%' AND actor='bot:ops'").fetchone()[0] >= 1
        )

    err, task = call(
        api,
        "tico_task_create",
        {"owner": "cpo", "title": "Review the runtime", "body": "Please.", "operation_id": "op-1"},
        token=token,
    )
    assert not err and task["task"]["owner_id"] == "bot:cpo"
    err, again = call(
        api,
        "tico_task_create",
        {"owner": "cpo", "title": "Review the runtime", "body": "Please.", "operation_id": "op-1"},
        token=token,
    )
    assert not err and again["task"]["id"] == task["task"]["id"]

    err, shown = call(api, "tico_task_show", {"id": task["task"]["id"]}, token=token)
    assert not err and shown["task"]["title"] == "Review the runtime"
    err, many = call(api, "tico_task_show_many", {"ids": [task["task"]["id"], "no-such-task"]}, token=token)
    assert (
        not err
        and [t["title"] for t in many["tasks"]] == ["Review the runtime"]
        and many["missing"] == ["no-such-task"]
    )

    # A bot reads its conversation back itself: the hub rebuilds nothing into a session.
    err, page = call(api, "tico_conversation_show", {"conversation": msg["conversation_id"]}, token=token)
    assert not err and page["conversation"]["id"] == msg["conversation_id"]
    assert [m["body"] for m in page["messages"]][0] == msg["body"]
    assert (page["has_more"], page["next_before"]) == (False, None)

    err, listed = call(api, "tico_task_list", {"requester": "me"}, token=token)
    assert not err and sorted(t["id"] for t in listed["result"]) == sorted([task["task"]["id"], wordy_task])

    err, out = call(api, "tico_task_close", {"id": task["task"]["id"], "why": "Not needed"}, token=token)
    assert err and out["detail"] == hubtools.version_needed(task["task"]["id"]), "a bot sends the version it saw"
    version = shown["task"]["version"]
    err, out = call(
        api, "tico_task_close", {"id": task["task"]["id"], "why": "Not needed", "version": version}, token=token
    )
    assert not err and out["task"]["status"] == "closed"
    err, out = call(api, "tico_task_show", {"id": "no-such-task"}, token=token)
    assert err and out["error"] == "not_found" and out["retryable"] is False


def test_tools_refuse_a_runner_credential_like_http_does(api):
    r = runner(api)
    err, out = call(api, "tico_message_list", token=r["token"])
    assert err and out["error"] == "identity"


def test_validation_identifies_fields_and_never_echoes_invalid_values(api):
    response = api.post("/api/v2/tasks", json={"owner": "me", "title": 123}, headers=headers())
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation" and "body.title" in error["detail"] and error["retryable"] is False
    for name, arguments, field in [
        ("tico_task_list", {"typo": "private-example-value"}, "typo"),
    ]:
        failed, payload = call(api, name, arguments)
        assert failed and payload["error"] == "validation" and field in payload["detail"]
        assert "private-example-value" not in payload["detail"]


def test_personal_token_api_bot_settings_and_archive_use_the_humans_rights(api):
    token = post(api, "me/tokens", {"label": "QA agent"})["token"]
    err, out = call(api, "tico_api", {"method": "GET", "path": "me"}, token=token)
    assert not err and out["actor"] == "human:ana"
    err, out = call(api, "tico_bot_update", {"slug": "ops", "description": "QA requested change"}, token=token)
    assert not err and "needs_confirm" not in out
    member = post(api, "me/tokens", {"label": "QA member"}, "cara-test")["token"]
    err, out = call(
        api, "tico_api", {"method": "PUT", "path": "access/rules", "body": {"member_tokens": False}}, token=member
    )
    assert err and out["error"] == "forbidden"
    err, out = call(api, "tico_bot_archive", {"bot": "ops"}, token=token)
    assert not err and out["status"] == "archived" and "needs_confirm" not in out


def test_assistant_tools_and_alias_always_use_the_token_humans_private_room(api):
    token = post(api, "me/tokens", {"label": "QA Assistant"})["token"]
    err, own = call(api, "tico_assistant_read", token=token)
    assert not err and own["room_id"]
    other = get(api, "assistant", "ben-test")
    err, sent = call(api, "tico_assistant_send", {"text": "Help"}, token=token)
    assert not err and sent["message"]["conversation_id"] == own["room_id"]
    err, sent = call(api, "tico_message_send", {"to": "assistant", "text": "Help"}, token=token)
    assert not err and sent["message"]["conversation_id"] == own["room_id"]
    post(api, "messages", {"to": "assistant", "text": "Help", "conversation_id": other["room_id"]}, token, expected=403)
    assert get(api, "assistant", "ben-test")["messages"] == other["messages"]


@pytest.mark.parametrize("method,params", [("initialize", "bad"), ("tools/call", {"name": []})])
def test_malformed_mcp_params_are_json_rpc_errors(api, method, params):
    response = api.post(
        "/api/v2/mcp", json={"jsonrpc": "2.0", "id": 7, "method": method, "params": params}, headers=headers()
    )
    assert response.status_code == 200 and response.json()["error"]["code"] == -32602


def test_a_bot_asks_a_person_on_its_own_task_and_the_ask_is_in_their_needs_you(api):
    """One ask, on the task: no second task just to ask. It carries the version the bot was shown; a stale one is
    refused with the version the task has now; the ask is one line in the person's Needs you, and the bot takes it
    back with --withdraw."""
    r = runner(api, "ana")
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    work = post(api, "tasks", {"owner": "ops", "title": "Assess the vendor agent", "body": "Is it worth a test?"})
    attempt = claim(api, r)
    err, shown = call(api, "tico_task_show", {"id": work["id"]}, token=attempt["token"])
    assert not err and "version" in shown["text"], shown
    version = shown["task"]["version"]
    ask = {
        "id": work["id"],
        "to": "ana",
        "line": "Run the vendor trial once?",
        "choice": ["Yes|The trial runs once.", "No|Nothing runs."],
        "recommend": "Yes",
        "why": "It decides what ships next.",
    }
    err, refused = call(api, "tico_task_ask", {**ask, "version": version - 1}, token=attempt["token"])
    assert err and refused["error"] == "task_changed" and refused["version"] == version, refused
    err, asked = call(api, "tico_task_ask", {**ask, "version": version}, token=attempt["token"])
    assert not err, asked
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM tasks").fetchone()[0] == 1, "an ask is not a task"
    item = next(i for i in get(api, "needs-you")["items"] if i["kind"] == "ask")
    assert item["line"] == "Run the vendor trial once?"
    err, out = call(api, "tico_task_ask", {"id": work["id"], "withdraw": True}, token=attempt["token"])
    assert not err, out
    assert not [i for i in get(api, "needs-you")["items"] if i["kind"] == "ask"]


# ------------------------------------------------------------------ the computer list
SUMMARY_FIELDS = {
    "id",
    "label",
    "online",
    "last_seen_at",
    "owner_id",
    "release",
    "version",
    "wanted_release",
    "update",
    "readiness",
    "warnings",
    "errors",
}


def heavy_heartbeat(api, r, bots, n, release="0.4.56", update=None, huge=""):
    """What a busy computer reports: runtime catalogs, every bot's readiness with instruction warnings, a long error
    history and its repositories (synthetic, at the sizes the models allow)."""
    models = ["model-" + str(m) for m in range(30)]
    runtime = lambda name: {
        "installed": True,
        "authenticated": "ready" if name != "gemini" else "missing",  # noqa: E731
        "version": "2.1." + str(n),
        "models": models,
        "detail": "signed in " * 20,
        "profiles": {
            p: {"runtime": name, "installed": True, "authenticated": "ready", "version": "2.1", "models": models}
            for p in ("work", "personal")
        },
    }
    report = {
        "schema_version": 1,
        "disk": {"total_bytes": 10**12, "free_bytes": 10**11},
        "engine": {"name": "rust", "build": "abc123"},
        "runtimes": {name: runtime(name) for name in ("claude", "codex", "gemini")},
        "harnesses": {
            h: {"name": h, "runtime": h, "installed": True, "version": "1.0", "detail": "ok " * 50}
            for h in ("claude", "codex", "gemini", "grok")
        },
        "bots": {
            bot: {
                "ready": bool(i or n % 3),
                "runtime": "claude",
                "model": "model-1",
                "repository": f"/Users/acme/tico/{bot}",
                "repository_present": True,
                "repository_revision": "f" * 40,
                "configuration_valid": True,
                "problems": [] if i or n % 3 else ["The repository is not on this computer yet"],
                "warnings": ["AGENTS.md is 41,000 characters; a run reads it every turn " + "x" * 150] * 3
                if bot != huge
                else ["z" * 50_000],
            }
            for i, bot in enumerate(bots)
        },
        "recent_errors": [f"2026-10-18T07:{m:02d}:00Z ERROR runner: turn {m} failed " + "y" * 230 for m in range(50)],
    }
    repositories = [
        {
            "full_name": f"acme/repo-{k}",
            "state": "cloned",
            "last_fetch": "2026-10-18T07:00:00Z",
            "size_mb": 120.5,
            "error": None,
        }
        for k in range(30)
    ]
    return post(
        api,
        "runners/heartbeat",
        {
            "version": "0.5.9",
            "platform": "darwin",
            "kind": "mac",
            "release": release,
            "readiness": report,
            "repositories": repositories,
            **({"update": update} if update else {}),
        },
        token=r["token"],
    )


def add_bots(api, slugs, runner_id=None):
    """Bots in the registry, and with `runner_id` already on that computer (as many as a big company puts there)."""
    from backend import hubdb as H
    from backend.store import encode

    bots = {slug: {"name": slug, "runtime": "fake", "status": "active"} for slug in slugs}
    with api.app.state.store.transaction() as c:
        H.sync_registry(c, bots, [])
        for slug, config in bots.items():
            c.execute("INSERT INTO bot_config(bot,owner_id) VALUES(?,'ana')", (slug,))
            BS.write(c, slug, config)
            if runner_id:
                c.execute("INSERT INTO assignments VALUES(?,?,1,?,'human:ana')", (slug, runner_id, H.now()))


def eleven_computers(api):
    slugs = ["bot-" + str(k) for k in range(33)]
    add_bots(api, slugs)
    computers = []
    for n in range(11):
        r = runner(api, label=f"Mac {n:02d}")
        mine = slugs[n * 3 : n * 3 + 3]
        for bot in mine:
            assign(api, r, bot)
        heavy_heartbeat(
            api,
            r,
            mine,
            n,
            release="0.4.50" if n == 1 else "0.4.56",
            update={"state": "failed", "error": "download refused"}
            if n == 2
            else {"state": "updating", "target": "0.4.56"}
            if n == 3
            else None,
            huge="bot-13",
        )
        computers.append(r)
    return computers


def test_the_computer_list_is_a_short_summary_and_one_computer_reads_in_capped_pages(api, monkeypatch):
    monkeypatch.setenv("TICO_VERSION", "0.4.56")
    computers = eleven_computers(api)
    add_bots(api, [f"helper-with-a-long-name-{k:03d}" for k in range(300)], computers[5]["runner_id"])
    assert len(json.dumps(get(api, "computers"))) > 200_000  # the Computers page keeps everything
    err, listing = call(api, "tico_computer_list")
    assert not err and len(listing["computers"]) == 11
    assert len(json.dumps(listing)) < 8_000  # the budget for 11 computers
    rows = {row["label"]: row for row in listing["computers"]}
    assert all(SUMMARY_FIELDS <= set(row) for row in rows.values())
    assert [rows[f"Mac {n:02d}"]["update"] for n in range(4)] == ["current", "update_available", "failed", "updating"]
    assert rows["Mac 00"]["readiness"] == "not_ready" and rows["Mac 00"]["errors"] == 1
    assert (
        rows["Mac 01"]["readiness"] == "warnings"
        and rows["Mac 01"]["warnings"] == 9
        and rows["Mac 04"]["warnings"] == 7
    )
    assert rows["Mac 01"]["signed_in"] == ["claude", "codex"] and rows["Mac 01"]["recent_errors"] == 50
    assert rows["Mac 05"]["bots"] == 303 and rows["Mac 01"]["bots"] == 3  # a count, not the names
    assert "repositories" not in rows["Mac 01"] and "AGENTS.md" not in json.dumps(listing)

    err, detail = call(api, "tico_computer_list", {"computer": "mac 04"})
    assert not err and detail["label"] == "Mac 04" and len(json.dumps(detail)) <= hubtools.COMPUTER_DETAIL_CHARS
    assert len(detail["repositories"]) == hubtools.COMPUTER_PAGE and len(detail["readiness"]["bots"]) == 3
    assert detail["readiness"]["recent_errors"][0].startswith("2026-10-18T07:49")  # newest first
    assert detail["readiness"]["bots"]["bot-12"]["warnings"][0].startswith("AGENTS.md is 41,000")
    assert detail["readiness"]["bots"]["bot-13"]["warnings"][0].endswith("… (cut)")  # one 50,000-character warning
    assert detail["readiness"]["runtimes"]["claude"]["models"][-1] == "… 10 more"  # catalogs inside a record
    err, bots = call(api, "tico_computer_list", {"computer": detail["id"], "section": "bots"})
    assert (
        not err
        and len(json.dumps(bots)) <= hubtools.COMPUTER_DETAIL_CHARS
        and set(bots["items"]) == {"bot-12", "bot-13", "bot-14"}
        and bots["items"]["bot-13"]["warnings"][0].endswith("… (cut)")
    )
    cut = detail["truncated"]
    assert set(cut) == {"repositories", "recent_errors"} and cut["repositories"]["total"] == 30
    err, rest = call(api, "tico_computer_list", {"computer": detail["id"], "section": "repositories", "offset": 20})
    assert not err and [r["full_name"] for r in rest["items"]] == [f"acme/repo-{k}" for k in range(20, 30)]
    assert "more" not in rest and rest["total"] == 30
    assert "tico_computer_list" in cut["recent_errors"]["more"]
    err, busy = call(api, "tico_computer_list", {"computer": "Mac 05"})
    assert not err and len(busy["bots"]) == 20 and busy["truncated"]["assigned"]["total"] == 303
    err, rest = call(api, "tico_computer_list", {"computer": "Mac 05", "section": "assigned", "offset": 300})
    assert not err and len(rest["items"]) == 3 and "more" not in rest
    # A record no page size or cut can fit names how to read it instead of going over the ceiling.
    row = {"id": "c1", "label": "Odd", "readiness": {"bots": {"odd": {f"k{k}": k for k in range(20_000)}}}}
    for read in (hubtools.computer_detail(row), hubtools.computer_detail(row, "bots")):
        assert read["too_large"] and len(json.dumps(read)) <= hubtools.COMPUTER_DETAIL_CHARS
    assert hubtools.computer_detail(row)["truncated"]["bots"]["more"].endswith("section=bots offset=0")


def test_a_member_reads_neither_a_computer_nor_a_bot_they_cannot_see_in_the_summary_or_the_detail(api):
    from backend.tests.helpers.api import as_member

    as_member(api, "ben@acme.example")
    anas = runner(api, label="Ana Mac")  # Ana's own, closed to members' bots
    shared = runner(api, label="Shared Mac")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runners SET accepts_member_bots=0 WHERE id=?", (anas["runner_id"],))
        c.execute("UPDATE runners SET accepts_member_bots=1 WHERE id=?", (shared["runner_id"],))
        restrict(c, "finance", people=["ana"])
    assign(api, anas, "assistant")
    for bot in ("ops", "finance"):
        assign(api, shared, bot)
    heavy_heartbeat(api, anas, ["assistant"], 1)
    heavy_heartbeat(api, shared, ["ops", "finance"], 1)
    raw = get(api, "computers", "ben-test")
    err, listing = call(api, "tico_computer_list", token="ben-test")
    assert not err and [row["label"] for row in listing["computers"]] == [row["label"] for row in raw["computers"]]
    assert [row["label"] for row in listing["computers"]] == ["Shared Mac"]
    assert listing["computers"][0]["bots"] == 1 and listing["computers"][0]["warnings"] == 3
    err, detail = call(api, "tico_computer_list", {"computer": "Shared Mac"}, token="ben-test")
    assert (
        not err
        and detail["bots"] == list(detail["readiness"]["bots"]) == ["ops"]
        and "finance" not in json.dumps(detail)
    )
    assert call(api, "tico_computer_list", {"computer": "Ana Mac"}, token="ben-test")[0]
    err, everything = call(api, "tico_computer_list", {"computer": "Shared Mac"})
    assert not err and set(everything["readiness"]["bots"]) == {"ops", "finance"}  # the owner still reads it


@pytest.fixture
def door(api):
    """The server's own MCP door for a caller: an InProcessApi on a loop of its own, the app answering through it."""
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    yield lambda token: InProcessApi(api.app, loop, "Bearer " + token, "http://testserver")
    loop.call_soon_threadsafe(loop.stop)
    thread.join()
    loop.close()


def test_a_bot_sends_raw_bodies_through_the_door_and_they_arrive_as_sent(api, door, monkeypatch, tmp_path):
    """The tools that send a file as its own body (publish a file or a page, attach it to a task, import it) run on the
    server's door as they do from the CLI: the bytes and name arrive unchanged, under the bot's own rights."""
    _, _, attempt = setup_attempt(api)
    bot, token = door(attempt["token"]), attempt["token"]
    monkeypatch.setattr(BF, "checkout_root", lambda cwd=None: tmp_path)
    text = "# Pipeline\n\nAcme grew 12%.\n".encode()
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "pipeline.md").write_bytes(text)

    made = hubtools.files_publish_path(bot, {"path": "reports/pipeline.md", "operation_id": "op-read-1"})
    assert api.get("/api/v2/files/" + made["file"]["id"], headers=headers(token)).content == text
    assert "pipeline.md" in api.get("/api/v2/bots/ops/files", headers=headers("ana-test")).text

    # A streamed file (a file object, read a block at a time) is the same file.
    with (tmp_path / "reports" / "pipeline.md").open("rb") as stream:
        copy = bot.request(
            "POST", "/api/v2/files/uploads?" + urlencode({"name": "pipeline-copy.md"}), raw=stream, key="op-stream-1"
        )
    assert api.get("/api/v2/files/" + copy["file"]["id"], headers=headers(token)).content == text

    (tmp_path / "pages").mkdir()
    # A screenshot saved beside the page goes in with it: the server would drop a reference to a file it cannot reach.
    (tmp_path / "pages" / "shot.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    (tmp_path / "pages" / "weekly.html").write_text(
        '<h1>Weekly</h1><p>Pipeline grew to 42 deals.</p><img src="shot.png">'
    )
    shown = hubtools.pages_publish_path(bot, {"path": "pages/weekly.html", "title": "Weekly", "cadence": "weekly"})
    assert shown["page"]["title"] == "Weekly"
    stored = api.get("/api/v2/files/" + shown["page"]["file_id"], headers=headers(token)).text
    assert "42 deals" in stored and 'src="data:image/png;base64,' in stored

    # An import is copied from S3 by the bot's computer; the server stores the bytes it is sent with their source.
    source = "s3://acme-exports/q3/pipeline.md"
    imported = bot.request(
        "POST",
        "/api/v2/files/imports?" + urlencode({"source": source, "etag": "etag-1", "name": "pipeline.md"}),
        raw=b"# From S3\n",
        key="op-import-1",
    )
    assert api.get("/api/v2/files/" + imported["file"]["id"], headers=headers(token)).content == b"# From S3\n"

    # Several files in one multipart body, as a task attachment sends them.
    task = post(api, "tasks", {"owner": "ops", "title": "Plan", "body": "Write the plan."})
    (tmp_path / "notes.txt").write_bytes(b"Notes for the plan.\n")
    attached = bot.request(
        "POST",
        f"/api/v2/tasks/{task['id']}/files",
        raw=MultipartBody({"file": tmp_path / "notes.txt"}, {"name": "notes.txt"}),
        key="op-multi-1",
    )
    assert api.get("/api/v2/files/" + attached["file_id"], headers=headers(token)).content == b"Notes for the plan.\n"


def test_a_raw_body_gets_the_callers_own_rights_as_over_http(api, door):
    http = api.post(
        "/api/v2/files/uploads?name=plan.md",
        content=b"# Plan",
        headers={**headers("ana-test"), "Content-Type": "application/octet-stream"},
    )
    assert http.status_code == 403
    with pytest.raises(ApiProblem) as refused:
        door("ana-test").request("POST", "/api/v2/files/uploads?name=plan.md", raw=b"# Plan", key="op-person-1")
    assert refused.value.status == 403


def test_a_raw_file_over_the_servers_limit_is_refused_before_it_is_sent(api, door, tmp_path):
    _, _, attempt = setup_attempt(api)
    big = tmp_path / "big.md"
    with big.open("wb") as stream:
        stream.truncate(27_000_001)  # a bot's raw upload stops at 27 MB (backend/app.py: published)
    with big.open("rb") as stream, pytest.raises(ApiProblem) as refused:
        door(attempt["token"]).request("POST", "/api/v2/files/uploads?name=big.md", raw=stream, key="op-big-1")
    assert refused.value.status == 413 and refused.value.code == "too_large"  # the Content-Length check, up front


def test_raw_file_position(api, door, tmp_path):
    """A file passed after its header was read is sent whole: its Content-Length is the file's size, so its bytes are
    too (as Client.request rewinds a file before sending it)."""
    _, _, attempt = setup_attempt(api)
    path = tmp_path / "plan.md"
    expected = b"# Plan\nComplete document.\n"
    path.write_bytes(expected)
    with path.open("rb") as source:
        source.read(7)
        made = door(attempt["token"]).request(
            "POST",
            "/api/v2/files/uploads?name=plan.md",
            raw=source,
            key="repro-file-position",
        )
    response = api.get(
        "/api/v2/files/" + made["file"]["id"],
        headers=headers(attempt["token"]),
    )
    assert response.status_code == 200
    assert response.content == expected


def test_a_raw_body_and_a_json_body_are_one_or_the_other():
    with pytest.raises(ValueError):
        InProcessApi(None, None, "Bearer unused", "http://testserver").request(
            "POST", "/api/v2/files/uploads", body={"name": "plan.md"}, raw=b"# Plan"
        )


def test_a_query_field_named_path_reaches_the_query_string(api, door):
    """`get(path, **query)` takes the URL path positionally, so a query field may be called `path` (`tico file approved
    <path>` asks file-approvals that way). The server answers from the query: no file has these bytes, so not approved,
    with the digest echoed back. A path it did not receive would be a 422 instead."""
    _, _, attempt = setup_attempt(api)
    answer = door(attempt["token"]).get("file-approvals", path="reports/pipeline.md", digest="0" * 64)
    assert answer["approved"] is False and answer["digest"] == "0" * 64


def test_a_generated_operation_id_survives_a_lost_answer_and_an_error(door, monkeypatch):
    """A write sent without an operation id gets one before it leaves; when the answer is lost or is an error, the
    caller is told that same id, so a retry can reuse the server's key instead of repeating the write."""
    import backend.mcp as M

    sent = []

    async def committed_then_lost(scope, receive, send):
        sent.append(dict(scope["headers"]).get(b"idempotency-key", b"").decode())
        if scope["path"].endswith("/slow"):
            await asyncio.sleep(5)  # committed, but the answer never arrives in time
        await send({"type": "http.response.start", "status": 500, "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": b'{"error": {"code": "boom", "detail": "Failed"}}'})

    monkeypatch.setattr(M, "INTERNAL_TIMEOUT_S", 0.2)
    monkeypatch.setattr(M, "FUTURE_MARGIN_S", 0.1)
    bot = door("unused")
    bot.app = committed_then_lost

    with pytest.raises(ApiProblem) as lost:
        bot.post("things/slow", {"name": "one"})
    assert sent[-1] and lost.value.operation_id == sent[-1]

    with pytest.raises(ApiProblem) as failed:
        bot.post("things/fail", {"name": "two"})
    assert sent[-1] and failed.value.operation_id == sent[-1]

    with pytest.raises(ApiProblem) as given:  # the caller's own id is kept
        bot.post("things/slow", {"name": "three"}, key="op-mine")
    assert sent[-1] == "op-mine" and given.value.operation_id == "op-mine"
