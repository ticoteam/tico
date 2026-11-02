"""The `hub` CLI as a bot runs it: `scripts/hub` in a subprocess, remote-only.

The command surface (docs/history/hub-v2.md §5) and the exit codes are the contract every bot's AGENT.md
depends on: 0 fine, 2 refused or a non-retryable API error, 1 anything else. The real HTTP round
trip is `runner/tests/test_conformance.py`; here the API is a stub or absent.
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread
from unittest import mock

import pytest

from clients import hubcli

HUB = Path(__file__).resolve().parents[2] / "scripts" / "hub"
SUBCOMMANDS = ["whoami", "inbound", "connections", "mail", "meeting", "message", "conversation", "thread", "question", "note", "file", "page", "card", "pin", "doc", "chat", "assistant", "tag", "task", "replay", "goal", "kpi",
               "proposal", "rules", "strategy", "brand", "gtm", "growth", "lens", "market", "listening", "tool", "automation", "audit", "event-types", "peer", "trigger", "relay", "call", "sleep", "notice", "settings", "release", "approval", "webhook", "pipeline", "brief", "mcp", "needs-you", "run", "team", "seat", "org", "health",
               "changelog", "update", "grokbot", "external", "calendar", "sql", "db", "classify", "gateway", "decision", "template", "bot", "repo", "skill", "agent", "human", "group", "api", "computer",
               "credential", "slack", "support", "health-monitor", "finance", "task-manager", "stall-triage", "roadmap", "service-key"]


def run_hub(*args, env=None):
    base = {k: v for k, v in os.environ.items() if not k.startswith("HUB_")}
    done = subprocess.run([sys.executable, str(HUB), *args], env={**base, **(env or {})},
                          capture_output=True, text=True, timeout=60)
    try:
        return done.returncode, json.loads(done.stdout)
    except ValueError:
        return done.returncode, {"_stdout": done.stdout, "_stderr": done.stderr}


def test_wake_url_commands_reach_the_tools_that_put_the_owners_address(monkeypatch):
    from clients import hubtools, remotecli
    calls = []
    class Api:
        def __init__(self, *args, **kw):
            self.__dict__["_tico_is_botops"] = False
        def get(self, path, **query):
            calls.append(("GET", path))
            return {"url": None, "set_at": None, "set_by": None}
        def call(self, method, path, body=None, key=None, query=None, delegate=False):
            calls.append((method, path, body))
            return {"url": body["url"], "secret": None}
    monkeypatch.setattr(remotecli, "Client", Api)
    monkeypatch.setenv("HUB_API_URL", "https://tico.acme.example/api/v2")
    for words in (["show"], ["set", "https://wake.acme.example/start"], ["off"]):
        remotecli.run(hubcli.parser().parse_args(["relay", "wake-url", *words]))
    assert calls == [("GET", "hooks/wake-url"), ("PUT", "hooks/wake-url", {"url": "https://wake.acme.example/start"}),
                     ("PUT", "hooks/wake-url", {"url": None})]
    assert {"hub_relay_wake_url_show", "hub_relay_wake_url_set", "hub_relay_wake_url_off"} <= set(hubtools.BY_NAME)


def test_thread_commands_reach_their_routes_with_their_flags(monkeypatch):
    from clients import hubtools, remotecli
    from clients.tico import APIError
    calls = []
    class Api:
        def __init__(self, *args, **kw):
            self.__dict__["_tico_is_botops"] = False
        def get(self, path, **query):
            if path == "me":
                return {"actor": "human:ana"}
            calls.append(("GET", path, {k: v for k, v in query.items() if v is not None}))
            return {}
        def post(self, path, body=None, key=None):
            calls.append(("POST", path, body))
            return {}
        def patch(self, path, body=None, key=None):
            calls.append(("PATCH", path, body))
            return {}
        def call(self, method, path, body=None, key=None, query=None, delegate=False):
            calls.append((method, path, body))
            return {}
    monkeypatch.setattr(remotecli, "Client", Api)
    monkeypatch.setenv("HUB_API_URL", "https://tico.acme.example/api/v2")
    runs = [
        (["thread", "list", "--limit", "10"], ("GET", "threads", {"limit": 10})),
        (["thread", "list", "--with", "bot:ops", "--cursor", "c2", "--limit", "5"],
         ("GET", "threads", {"with": "bot:ops", "cursor": "c2", "limit": 5})),
        (["thread", "show", "th1", "--cursor", "c2"], ("GET", "threads/th1/timeline", {"cursor": "c2"})),
        (["thread", "home", "bot:ops"], ("GET", "threads/home/bot:ops", {})),
        (["thread", "home", "group:x"], ("GET", "threads/home/group:x", {})),
        (["thread", "dm", "human:ben"], ("GET", "threads/dm", {"with": "human:ben"})),
        (["thread", "dm-group", "human:ana", "bot:ops", "--body", "Hi all"],
         ("POST", "threads/dm", {"members": ["human:ana", "bot:ops"], "body": "Hi all"})),
        (["thread", "dm-group", "human:ben"], ("POST", "threads/dm", {"members": ["human:ben"], "body": None})),
        (["thread", "create", "Refund policy", "--audience", "team", "--group", "grp1", "--member", "human:ana",
          "--member", "bot:legal", "--body", "Hi"],
         ("POST", "threads", {"title": "Refund policy", "audience": "team", "audience_group_id": "grp1",
                              "members": ["human:ana", "bot:legal"], "body": "Hi"})),
        (["thread", "post", "th1", "Hello", "--kind", "ask", "--to", "human:ana", "--ref", "task:t9", "--ref", "task:t10"],
         ("POST", "threads/th1/messages", {"text": "Hello", "kind": "ask", "to": "human:ana",
                                           "refs": {"task": ["t9", "t10"]}})),
        (["thread", "reply", "th1", "m7", "Side note"],
         ("POST", "threads/th1/messages", {"text": "Side note", "kind": "say", "parent_message_id": "m7"})),
        (["thread", "edit", "m7", "Fixed"], ("PATCH", "messages/m7", {"text": "Fixed"})),
        (["thread", "sub", "m7"], ("POST", "threads", {"parent_message_id": "m7"})),
        (["thread", "follow", "th1", "--notify", "mentions"], ("PUT", "threads/th1/follow", {"notify": "mentions"})),
        (["thread", "follow", "th1"], ("PUT", "threads/th1/follow", {"notify": "live"})),
        (["thread", "unfollow", "th1"], ("DELETE", "threads/th1/follow", None)),
        (["thread", "read", "th1", "--seq", "42"], ("POST", "threads/th1/read", {"seq": 42})),
        (["thread", "read", "th1", "--all"], ("POST", "threads/th1/read-all", {})),
        (["thread", "search", "refund terms"], ("GET", "threads/search", {"q": "refund terms"})),
        (["thread", "members", "list", "th1"], ("GET", "threads/th1/members", {})),
        (["thread", "members", "add", "th1", "human:ana", "--owner"],
         ("POST", "threads/th1/members", {"actor": "human:ana", "role": "owner"})),
        (["thread", "members", "remove", "th1", "human:ana"], ("DELETE", "threads/th1/members/human:ana", None)),
        (["thread", "update", "th1", "--title", "Refunds", "--archive"],
         ("PATCH", "threads/th1", {"title": "Refunds", "archived": True})),
        (["thread", "update", "th1", "--unarchive"], ("PATCH", "threads/th1", {"archived": False})),
        (["thread", "react", "m7", ":+1:"], ("PUT", "messages/m7/reactions/:+1:", None)),
        (["thread", "unreact", "m7", ":+1:"], ("DELETE", "messages/m7/reactions/:+1:", None)),
        (["thread", "open-earlier", "th1"], ("POST", "threads/th1/open-earlier", {})),
        (["thread", "task", "m7", "--title", "Ship it", "--owner", "bot:ops"],
         ("POST", "threads/messages/m7/task", {"title": "Ship it", "owner": "bot:ops"})),
    ]
    for words, expected in runs:
        calls.clear()
        remotecli.run(hubcli.parser().parse_args(words))
        assert calls == [expected], words
    assert {"hub_thread_list", "hub_thread_show", "hub_thread_create", "hub_thread_post", "hub_thread_reply",
            "hub_thread_sub", "hub_thread_edit", "hub_thread_follow", "hub_thread_unfollow", "hub_thread_read", "hub_thread_search",
            "hub_thread_members_list", "hub_thread_members_add", "hub_thread_members_remove", "hub_thread_update",
            "hub_thread_link_slack", "hub_thread_dm_group", "hub_thread_react", "hub_thread_unreact",
            "hub_thread_open_earlier"} <= set(hubtools.BY_NAME)


def test_dm_group_needs_at_least_one_member_on_the_command_line():
    with pytest.raises(SystemExit):
        hubcli.parser().parse_args(["thread", "dm-group"])


def test_review_command_and_mcp_preserve_head_body_and_retry_identity(tmp_path, monkeypatch):
    from clients import remotecli, hubtools
    calls = []
    class Api:
        def __init__(self, *args, **kw):
            pass
        def post(self, path, body, key=None):
            calls.append((path, body, key))
            return {"event": "APPROVE"}
    monkeypatch.setattr(remotecli, "Client", Api)
    monkeypatch.setenv("HUB_API_URL", "https://tico.acme.example/api/v2")
    text = tmp_path / "review.txt"
    text.write_text("Checked the credential boundary.\nSecond paragraph.\n")
    args = hubcli.parser().parse_args(["repo", "review", "Acme/product", "7", "--head", "a" * 40,
                                      "--verdict", "approve", "--body-file", str(text)])
    assert remotecli.run(args) == {"event": "APPROVE"}
    expected = {"repository": "Acme/product", "number": 7, "head": "a" * 40,
                "verdict": "approve", "body": text.read_text()}
    assert calls[0][:2] == ("github/reviews", expected)
    hubtools.repo_review(Api(), expected)
    assert calls[1] == calls[0]  # CLI and MCP retries identify the same exact review.


def test_gateway_fetch_command_asks_the_route_with_the_url_and_size(monkeypatch):
    from clients import remotecli
    calls = []
    class Api:
        def __init__(self, *args, **kw):
            pass
        def post(self, path, body, key=None):
            calls.append((path, body))
            return {"content": "C"}
    monkeypatch.setattr(remotecli, "Client", Api)
    monkeypatch.setenv("HUB_API_URL", "https://tico.acme.example/api/v2")
    assert remotecli.run(hubcli.parser().parse_args(["gateway", "fetch", "https://example.test/a"])) == {"content": "C"}
    remotecli.run(hubcli.parser().parse_args(["gateway", "fetch", "https://example.test/b", "--max-tokens", "2000"]))
    assert calls == [("gateway/fetch", {"url": "https://example.test/a", "max_tokens": 10000}),
                     ("gateway/fetch", {"url": "https://example.test/b", "max_tokens": 2000})]


def test_product_repository_cli_previews_and_requires_the_exact_typed_confirmation(monkeypatch):
    from clients import remotecli
    from io import StringIO
    sent = []

    class Api:
        def get(self, path, **query):
            sent.append(("GET", path, query))
            return {"org": "Acme", "name": "tico-recorder", "repository": "Acme/tico-recorder",
                    "visibility": "private", "auto_init": False, "capability": "available",
                    "capability_detail": "Administration: write verified"}
        def post(self, path, body, key=None):
            sent.append(("POST", path, body, key))
            return {"repository": "Acme/tico-recorder"}

    args = hubcli.parser().parse_args(["repo", "product-create", "tico-recorder"])
    assert args.fn == "repo product-create"
    out = StringIO("Acme/tico-recorder\n")
    out.isatty = lambda: True
    remotecli.product_repo_create(Api(), args.name, stdin=out, stderr=StringIO())
    assert sent[0] == ("GET", "github/product-repos/preview", {"name": "tico-recorder"})
    assert sent[1][:3] == ("POST", "github/product-repos", {"org": "Acme", "name": "tico-recorder",
                                                               "visibility": "private", "auto_init": False,
                                                               "confirmed": True})
    assert sent[1][3] and len(sent[1][3]) <= 200


def test_product_repository_cli_never_writes_without_matching_confirmation(monkeypatch):
    from clients import remotecli
    from io import StringIO
    calls = []

    class Api:
        def get(self, path, **query):
            return {"org": "Acme", "name": "tico-recorder", "repository": "Acme/tico-recorder",
                    "visibility": "private", "auto_init": False, "capability": "available",
                    "capability_detail": "Administration: write verified"}
        def post(self, *args, **kwargs):
            calls.append((args, kwargs))

    out = StringIO("Acme/other\n")
    out.isatty = lambda: True
    with pytest.raises(remotecli.APIError, match="Confirmation did not match"):
        remotecli.product_repo_create(Api(), "tico-recorder", stdin=out, stderr=StringIO())
    assert calls == []


def test_product_repository_cli_refuses_missing_capability_and_noninteractive_confirmation():
    from clients import remotecli
    from io import StringIO

    class Api:
        writes = 0
        def __init__(self, capability):
            self.capability = capability
        def get(self, path, **query):
            return {"org": "Acme", "name": "tico-recorder", "repository": "Acme/tico-recorder",
                    "visibility": "private", "auto_init": False, "capability": self.capability,
                    "capability_detail": "Live Administration permission is not available"}
        def post(self, *args, **kwargs):
            self.writes += 1

    with pytest.raises(remotecli.APIError, match="not available"):
        remotecli.product_repo_create(Api("missing"), "tico-recorder", stdin=StringIO(""), stderr=StringIO())
    available = Api("available")
    with pytest.raises(remotecli.APIError, match="--confirm Acme/tico-recorder, or type it at an interactive terminal"):
        remotecli.product_repo_create(available, "tico-recorder", stdin=StringIO("Acme/tico-recorder\n"), stderr=StringIO())
    assert available.writes == 0


def test_product_repository_cli_confirms_without_a_terminal_only_with_the_exact_target():
    """A bot's turn has no terminal: `--confirm org/name` stands in for typing it, and must match the preview."""
    from clients import remotecli
    from io import StringIO
    sent = []

    class Api:
        def get(self, path, **query):
            return {"org": "Acme", "name": "tico-services", "repository": "Acme/tico-services",
                    "visibility": "private", "auto_init": False, "capability": "available",
                    "capability_detail": "Administration: write verified"}
        def post(self, path, body, key=None):
            sent.append((path, body))
            return {"repository": "Acme/tico-services"}

    args = hubcli.parser().parse_args(["repo", "product-create", "tico-services", "--confirm", "Acme/tico-services"])
    assert args.confirm == "Acme/tico-services"
    for wrong in ("tico-services", "Acme/other", "acme/tico-services"):
        with pytest.raises(remotecli.APIError, match="exact preview target Acme/tico-services"):
            remotecli.product_repo_create(Api(), "tico-services", confirm=wrong, stdin=StringIO(""), stderr=StringIO())
    assert sent == []
    remotecli.product_repo_create(Api(), "tico-services", confirm=args.confirm, stdin=StringIO(""), stderr=StringIO())
    assert sent == [("github/product-repos", {"org": "Acme", "name": "tico-services", "visibility": "private",
                                              "auto_init": False, "confirmed": True})]


class Parser(unittest.TestCase):
    def test_every_subcommand_is_still_there(self):
        text = hubcli.parser().format_help()
        self.assertIn("{" + ",".join(SUBCOMMANDS) + "}", text)

    def test_task_update_help_and_mcp_offer_only_writable_statuses(self):
        from clients import hubtools

        code, out = run_hub("task", "update", "--help")
        self.assertEqual(code, 0, out)
        self.assertNotIn("closed", out["_stdout"])
        statuses = ["open", "doing", "waiting", "review", "ready", "done", "declined"]
        schema = next(t["inputSchema"] for t in hubtools.TOOLS if t["name"] == "hub_task_update")
        self.assertEqual(schema["properties"]["status"]["enum"], [status for status in statuses if status != "ready"])
        for status in statuses:
            args = hubcli.parser().parse_args(["task", "update", "T1", "--status", status])
            self.assertEqual(args.status, status)


class Remote(unittest.TestCase):
    def test_human_override_is_refused_remotely_with_exit_two(self):
        code, out = run_hub("--human", "ana", "whoami",
                            env={"HUB_API_URL": "http://127.0.0.1:9", "HUB_TOKEN": "x"})
        self.assertEqual(code, 2)
        self.assertEqual(out["error"], "identity")


class Stub(BaseHTTPRequestHandler):
    """Enough of the Tico API to see what the CLI sends: /me, one task, one refusal."""
    seen = []

    def log_message(self, *a):
        pass

    def reply(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.seen.append(("GET", self.path, self.headers.get("Authorization"), None))
        if self.path == "/api/v2/me":
            return self.reply(200, {"actor": "bot:coo", "kind": "bot", "id": "coo"})
        if self.path.startswith("/api/v2/roadmap"):
            return self.reply(200, {"ok": True})
        if self.path.startswith("/api/v2/decisions/readout"):
            return self.reply(200, {"sets": [], "text": "Decide readout"})
        if self.path.startswith("/api/v2/tasks/T1"):
            return self.reply(200, {"task": {"id": "T1", "version": 3}})
        if self.path == "/api/v2/tasks?status=closed":
            return self.reply(200, {"tasks": [{"id": "T1", "status": "closed"}]})
        self.reply(404, {"error": {"code": "not_found", "detail": "no"}})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        self.seen.append(("POST", self.path, self.headers.get("Authorization"), body))
        if self.path == "/api/v2/tasks/T1":
            return self.reply(200, {"task": {"id": "T1", "status": body.get("status"), "version": 4}})
        if self.path == "/api/v2/tasks" and body.get("check_model"):
            return self.reply(200, {"task": {"id": "T2", "owner": body["owner"], "title": body["title"]}})
        if self.path == "/api/v2/bots/writer/tools":
            return self.reply(200, {"ok": True, "dry_run": bool(body.get("dry_run"))})
        if self.path == "/api/v2/approvals":
            return self.reply(200, {"id": "ap-1", "kind": body.get("kind"), "decision": None})
        if self.path == "/api/v2/support/tickets/from-approval":
            return self.reply(200, {"ticket": {"id": "sup_1"}, "approval": body.get("approval"), "again": False})
        if any(part in self.path for part in ("/requesters", "/publication-preview", "/moderate", "/roadmaps/settings",
                                              "/waits", "/snooze")):
            return self.reply(200, {"ok": True})
        self.reply(422, {"error": {"code": "refused", "detail": "rule 2: not on the roster"}})


class AgainstAStub(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), Stub)
        cls.thread = Thread(target=cls.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        cls.thread.start()
        cls.env = {"HUB_API_URL": f"http://127.0.0.1:{cls.server.server_port}",
                   "HUB_TOKEN": "turn-token", "HUB_BOT": "coo"}

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        Stub.seen.clear()

    def test_whoami_is_the_api_identity_under_the_turn_token(self):
        code, out = run_hub("whoami", env=self.env)
        self.assertEqual(code, 0)
        self.assertEqual(out["actor"], "bot:coo")
        self.assertEqual(Stub.seen[0][2], "Bearer turn-token")

    def test_task_update_reads_the_version_first_so_a_stale_write_cannot_clobber(self):
        code, out = run_hub("task", "update", "T1", "--status", "done", "--note", "shipped", env=self.env)
        self.assertEqual(code, 0)
        self.assertEqual(out["status"], "done")
        posted = next(b for m, p, _, b in Stub.seen if m == "POST")
        self.assertEqual((posted["version"], posted["status"], posted["note"]), (3, "done", "shipped"))

    def test_task_update_closed_gives_close_hint_without_an_api_call(self):
        for status_arg in (("--status", "closed"), ("--status=closed",)):
            code, out = run_hub("task", "update", "T1", *status_arg, env=self.env)
            self.assertEqual(code, 1, out)
            self.assertIn("use `hub task close <id>`", out["_stderr"])
            self.assertEqual(Stub.seen, [])

    def test_task_list_still_filters_closed_tasks(self):
        code, out = run_hub("task", "list", "--status", "closed", env=self.env)
        self.assertEqual(code, 0, out)
        self.assertEqual(out, [{"id": "T1", "status": "closed"}])
        self.assertEqual([(m, p) for m, p, _, _ in Stub.seen],
                         [("GET", "/api/v2/me"), ("GET", "/api/v2/tasks?status=closed")])

    def test_task_child_on_a_check_model_is_the_bots_own_subtask(self):
        code, out = run_hub("task", "child", "T1", "--check-model", "grok", "--role", "review",
                            "--title", "Review PR 700", "--body", "https://github.com/ticoteam/tico/pull/700", env=self.env)
        self.assertEqual(code, 0, out)
        posted = next(b for m, p, _, b in Stub.seen if m == "POST" and p == "/api/v2/tasks")
        self.assertEqual((posted["owner"], posted["check_model"], posted["check_role"], posted["relations"]),
                         ("bot:coo", "grok", "review", [{"task": "T1", "kind": "parent"}]))
        # With no value, the server picks the company's check model.
        code, out = run_hub("task", "child", "T1", "--check-model", "--role", "review", "--title", "Review PR 701",
                            env=self.env)
        self.assertEqual(code, 0, out)
        posted = [b for m, p, _, b in Stub.seen if m == "POST" and p == "/api/v2/tasks"][-1]
        self.assertEqual((posted["owner"], posted["check_model"]), ("bot:coo", "auto"))

    def test_decision_readout_reads_the_days_asked(self):
        code, out = run_hub("decision", "readout", "--days", "14", env=self.env)
        self.assertEqual((code, out["text"]), (0, "Decide readout"), out)
        self.assertIn(("GET", "/api/v2/decisions/readout?days=14"), [(m, p) for m, p, _, _ in Stub.seen])
        code, out = run_hub("decision", "readout", env=self.env)
        self.assertEqual(code, 0, out)
        self.assertEqual(Stub.seen[-1][1], "/api/v2/decisions/readout", "the server's default, 7 days")

    def test_tool_add_dry_run_is_checked_by_the_server_not_the_task_dry_run(self):
        code, out = run_hub("tool", "add", "writer", "notion", "--can", "read",
                            "--mcp-url", "https://mcp.notion.example/mcp", "--dry-run", env=self.env)
        self.assertEqual(code, 0, out)
        posted = next(b for m, p, _, b in Stub.seen if m == "POST" and p == "/api/v2/bots/writer/tools")
        self.assertTrue(posted["dry_run"])
        self.assertEqual(posted["mcp"]["url"], "https://mcp.notion.example/mcp")

    def test_support_report_drafts_an_approval_with_the_exact_message_and_send_names_it(self):
        import hashlib
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "report.md"
            report.write_bytes(b"hub task update fails.\r\nError: refused (version)\r\n\r\n")
            code, out = run_hub("support", "report", "--file", str(report), "--no-ids", env=self.env)
        self.assertEqual(code, 0, out)
        self.assertEqual(out["approval"], "ap-1")
        self.assertIn("hub support send --approval ap-1", out["next"])
        posted = next(b for m, p, _, b in Stub.seen if m == "POST" and p == "/api/v2/approvals")
        message = "hub task update fails.\nError: refused (version)"
        self.assertEqual(posted, {"kind": "support", "payload": {
            "message": message, "content_sha256": hashlib.sha256(message.encode()).hexdigest(), "include_ids": False}})
        code, out = run_hub("support", "send", "--approval", "ap-1", env=self.env)
        self.assertEqual((code, out["ticket"]["id"]), (0, "sup_1"))
        posted = next(b for m, p, _, b in Stub.seen if m == "POST" and p == "/api/v2/support/tickets/from-approval")
        self.assertEqual(posted, {"approval": "ap-1"})

    def test_task_wait_and_snooze_send_their_note(self):
        def last(method, path):
            return [b for m, p, _, b in Stub.seen if m == method and p.split("?")[0] == path][-1]
        pr = "pr:https://github.com/acme/app/pull/9"
        self.assertEqual(run_hub("task", "wait", "T1", "--on", pr, "--note", "Check the migration", env=self.env)[0], 0)
        self.assertEqual(last("POST", "/api/v2/tasks/T1/waits"),
                         {"on": pr, "remove": False, "note": "Check the migration"})
        self.assertEqual(run_hub("task", "wait", "T1", "--on", "release:v0.3.64", env=self.env)[0], 0)
        self.assertNotIn("note", last("POST", "/api/v2/tasks/T1/waits"), "no note is sent when none is given")
        self.assertEqual(run_hub("task", "snooze", "T1", "--until", "2026-11-02", "--note", "Ask the landlord",
                                 env=self.env)[0], 0)
        self.assertEqual(last("POST", "/api/v2/tasks/T1/snooze"), {"until": "2026-11-02", "note": "Ask the landlord"})

    def test_roadmap_commands_send_what_their_tools_send(self):
        def last(method, path):
            return [(p, b) for m, p, _, b in Stub.seen if m == method and p.split("?")[0] == path][-1]
        for argv in (["task", "requesters", "T1", "--limit", "5"],
                     ["task", "requester-save", "T1", "--email", "jane@acme.example", "--organization", "Acme", "--important"],
                     ["task", "requester-save", "T1", "--requester-id", "R1", "--no-important"],
                     ["task", "requester-remove", "T1", "R1"],
                     ["task", "publication-preview", "T1", "--visibility", "public", "--public-title", "Exports", "--label", "roadmap"],
                     ["task", "comment-moderate", "T1", "C1", "hide"],
                     ["roadmap", "tasks", "--sort", "size", "--direction", "asc"],
                     ["roadmap", "settings"],
                     ["roadmap", "settings", "--label", "roadmap", "--suggestion-limit", "5", "--no-bot-public-approval"],
                     ["roadmap", "settings", "--label", ""]):
            code, out = run_hub(*argv, env=self.env)
            self.assertEqual(code, 0, (argv, out))
        self.assertIn("limit=5", last("GET", "/api/v2/tasks/T1/requesters")[0])
        self.assertEqual(last("POST", "/api/v2/tasks/T1/requesters")[1],
                         {"email": "jane@acme.example", "organization": "Acme", "important": True})
        self.assertEqual(last("POST", "/api/v2/tasks/T1/requesters/R1")[1], {"important": False})
        self.assertEqual(last("POST", "/api/v2/tasks/T1/requesters/R1/delete")[1], {})
        self.assertEqual(last("POST", "/api/v2/tasks/T1/publication-preview")[1],
                         {"visibility": "public", "public_title": "Exports", "labels": ["roadmap"]})
        self.assertEqual(last("POST", "/api/v2/tasks/T1/comments/C1/moderate")[1], {"action": "hide"})
        self.assertTrue({"sort=size", "direction=asc"} <= set(last("GET", "/api/v2/roadmap-tasks")[0].split("?")[1].split("&")))
        self.assertTrue(any(m == "GET" and p == "/api/v2/roadmaps/settings" for m, p, _, _ in Stub.seen))
        settings = [b for m, p, _, b in Stub.seen if m == "POST" and p == "/api/v2/roadmaps/settings"]
        self.assertEqual(settings, [{"labels": ["roadmap"], "suggestion_limit": 5, "bot_public_approval": False},
                                    {"labels": []}])

    def test_a_refusal_from_the_api_exits_two(self):
        code, out = run_hub("message", "send", "nobody", "hello", env=self.env)
        self.assertEqual(code, 2)
        self.assertEqual(out["error"], "refused")
        self.assertIn("roster", out["detail"])


CARD = """template: specialist
slug: seo
name: The Specialist
bootstrap: false
required: false
summary: Writes the blog.
owns: []
never: []
runtime: codex
"""
AGENT = "# {{bot_name}}\n\nYou are {{bot_name}} at {{company_name}} ({{app_name}}).\n\n## Owns\n- the blog\n"
NAMES = {"company_name": "Acme Ltd", "app_name": "Acme OS", "assistant_name": "Ada", "assistant_bot": "coo"}
ONBOARDING = {"names": NAMES,
              "answers": {"what_we_do": "we clean holiday homes", "customers": "owners",
                          "team_size": "nine people", "work_arrives": ["email"],
                          "never_without_person": ["refunds"]},
              "selected": {"seo": {"template": "specialist", "display_name": "Sam",
                                   "instructions": "# Sam\n\n## Owns\n- the blog\n"}},
              "completed": True}


class BotSetup(unittest.TestCase):
    """`hub template list` and `hub bot create|check`: the three commands BotOps needs in a turn.

    The parsing is the real parser and the work is the real `clients/catalog.py`; only the API is
    a fake, so what these assert is which endpoints a command reads and what it writes to disk.
    """

    class FakeClient:
        def __init__(self, cards=None, onboarding=ONBOARDING):
            self.cards, self.record, self.seen, self.posted = cards, onboarding, [], []
            self.github = None          # the APIError `github/repos` answers with; None creates the repository

        def post(self, path, body, key=None):
            self.posted.append((path, body))
            if path == "bots/register":
                return {"created": True, "status": "planned", "bot_owners": ["cara"]}
            if path == "github/repos":
                if self.github:
                    raise self.github
                return {"repository": "Acme/bot-" + body["slug"], "empty": True}
            return {"id": "auto_" + body["name"]}

        def get(self, path, **query):
            self.seen.append(path)
            if path == "config":
                return dict(NAMES)
            if path == "me":            # BotOps; a person's turn seeds the template's automations as that person
                return {"actor": "bot:botops", "kind": "botops"}
            if path == "setup":
                return json.loads(json.dumps(self.record))
            if path == "templates" and self.cards is not None:
                return {"cards": self.cards}
            from clients.tico import APIError
            raise APIError("not_found", "No such endpoint: " + path, 404, False)

    def setUp(self):
        from clients import remotecli
        self.remotecli = remotecli
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        self.workspace, catalog = root / "work", root / "catalog"
        (catalog / "specialist").mkdir(parents=True)
        (catalog / "specialist" / "card.yaml").write_text(CARD)
        (catalog / "specialist" / "AGENT.md").write_text(AGENT)
        (catalog / "specialist" / "bot.yaml").write_text('name: CHANGE-ME\ndisplay_name: "Change Me"\nroutines: []\n')
        (catalog / "specialist" / "state.md").write_text("# State\n")
        for key, value in {"HUB_WORKSPACE": str(self.workspace), "TICO_CATALOG_DIR": str(catalog)}.items():
            os.environ[key] = value
            self.addCleanup(os.environ.pop, key, None)
        self.client = self.FakeClient()

    def run_command(self, *argv):
        return self.remotecli.bots(self.client, hubcli.parser().parse_args(list(argv)))

    def test_creating_a_bot_twice_refuses_rather_than_overwriting_its_memory(self):
        self.run_command("bot", "create", "seo", "--template", "specialist")
        with self.assertRaises(ValueError) as refused:
            self.run_command("bot", "create", "seo", "--template", "specialist")
        self.assertIn("already exists", str(refused.exception))

    def test_creating_a_bot_in_a_persons_turn_registers_it_with_the_server_first(self):
        made = self.run_command("bot", "create", "seo", "--template", "specialist")
        self.assertEqual(self.client.posted[0][0], "bots/register")
        self.assertEqual(self.client.posted[0][1]["on_behalf_of"], "turn")
        self.assertEqual(made["registered"]["bot_owners"], ["cara"])
        self.assertEqual(made["github"]["repository"], "Acme/bot-seo")
        self.assertIn(("github/repos", {"slug": "seo", "empty": True}), self.client.posted)

    def test_creating_a_bot_without_repo_permission_fails_and_says_a_person_must_act(self):
        from clients.tico import APIError
        self.client.github = APIError("github_permission_missing", "A person must act: the GitHub App was set up "
                                      "without permission to create repositories.", 409, False)
        args = hubcli.parser().parse_args(["bot", "create", "seo", "--template", "specialist"])
        out = io.StringIO()
        with mock.patch.object(self.remotecli, "Client", lambda *a, **k: self.client), \
                mock.patch.dict(os.environ, {"HUB_API_URL": "http://hub.invalid"}), contextlib.redirect_stdout(out):
            code = self.remotecli.main(args)
        self.assertNotEqual(code, 0)
        said = json.loads(out.getvalue())
        self.assertEqual(said["error"], "github_permission_missing")
        self.assertTrue(said["detail"].startswith("Not finished: A person must act"))
        self.assertIn("stays planned", said["detail"])
        self.assertTrue(any(self.workspace.iterdir()), "the local build is kept")
        self.client.github = APIError("github_not_connected", "GitHub is not connected.", 409, False)
        self.assertIn("skipped", self.remotecli.github_repository(self.client, "seo", self.workspace))

    def test_creating_a_bot_the_server_has_not_registered_keeps_it_local(self):
        from clients.tico import APIError
        self.client.github = APIError("forbidden", "seo is not a bot being set up or running", 403, False)
        made = self.run_command("bot", "create", "seo", "--template", "specialist")
        self.assertIn("skipped", made["github"])
        self.assertIn("hub bot repo-create seo --empty", made["github"]["note"])

    def test_a_bot_created_without_a_slug_takes_the_one_picked_from_its_name(self):
        # The server picks it in a person's turn; the build and its repository use that slug.
        self.client.post = lambda path, body, key=None: (
            self.client.posted.append((path, body)) or {"slug": "head-of-marketing-2", "created": True}
            if path == "bots/register" else {"repository": "Acme/bot-" + body["slug"]})
        made = self.run_command("bot", "create", "--template", "specialist", "--name", "Head of Marketing")
        self.assertNotIn("slug", self.client.posted[0][1])
        self.assertEqual(made["slug"], "head-of-marketing-2")
        self.assertTrue((self.workspace / "bot-head-of-marketing-2").is_dir())
        # With no person's turn the command picks it, from the template's name, past a repository already here.
        from clients.tico import APIError
        def outside_a_turn(path, body, key=None):
            if path == "bots/register":
                raise APIError("on_behalf_of", "No person started this turn", 422, False)
            return {"repository": "Acme/bot-" + body["slug"]}
        self.client.post = outside_a_turn
        self.assertEqual(self.run_command("bot", "create", "--template", "specialist")["slug"], "the-specialist")
        self.assertEqual(self.run_command("bot", "create", "--template", "specialist")["slug"], "the-specialist-2")




if __name__ == "__main__":
    unittest.main(verbosity=2)


@pytest.mark.parametrize('choice', ['yes', 'no'])
def test_creation_qa_override_and_paths_survive_cli_and_mcp(monkeypatch, choice):
    from clients import remotecli, hubtools
    sent = []
    class Api:
        def get(self, path, **kw):
            return {'actor': 'human:ana'}
        def post(self, path, body, **kw):
            sent.append(body)
            return {'task': {'id': 'synthetic'}}
    api = Api()
    monkeypatch.setenv('HUB_API_URL', 'https://api.example.invalid')
    monkeypatch.setattr(remotecli, 'Client', lambda *a, **kw: api)
    args = hubcli.parser().parse_args(['task', 'create', '--owner', 'cpo', '--title', 'UI change',
        '--body', 'x', '--qa', choice, '--change-path', 'ui/page.js', '--qa-not-checkable-live'])
    remotecli.run(args)
    hubtools.BY_NAME['hub_task_create']['fn'](api, {'owner': 'cpo', 'title': 'UI change', 'body': 'x',
        'qa': choice == 'yes', 'change_paths': ['ui/page.js'], 'qa_not_checkable_live': True})
    assert len(sent) == 2
    assert all(p['qa'] is (choice == 'yes') and p['change_paths'] == ['ui/page.js']
               and p['qa_not_checkable_live'] is True for p in sent)


def test_keep_tasks_going_cli_preserves_explicit_unlimited(monkeypatch):
    from clients import remotecli
    from clients.hubtools import BY_NAME
    sent = []
    monkeypatch.setitem(BY_NAME['hub_bot_keep_tasks_going'], 'fn', lambda api, args: sent.append(args))
    for argv in (['bot', 'keep-tasks-going', 'ops'], ['bot', 'keep-tasks-going', 'ops', 'unlimited'],
                 ['bot', 'keep-tasks-going', 'ops', 'off'], ['bot', 'keep-tasks-going', 'ops', '3']):
        remotecli.via_tool(None, hubcli.parser().parse_args(argv))
    assert 'limit' not in sent[0]
    assert [row['limit'] for row in sent[1:]] == [None, 0, 3]


def test_turns_at_once_cli_preserves_explicit_unlimited(monkeypatch):
    from clients import remotecli
    from clients.hubtools import BY_NAME
    sent = []
    monkeypatch.setitem(BY_NAME['hub_bot_turns_at_once'], 'fn', lambda api, args: sent.append(args))
    for argv in (['bot', 'turns-at-once', 'ops'], ['bot', 'turns-at-once', 'ops', 'unlimited'],
                 ['bot', 'turns-at-once', 'ops', '3']):
        remotecli.via_tool(None, hubcli.parser().parse_args(argv))
    assert 'limit' not in sent[0]
    assert [row['limit'] for row in sent[1:]] == [None, 3]


def test_computer_turns_at_once_cli_sends_automatic_as_null(monkeypatch):
    from clients import remotecli
    from clients.hubtools import BY_NAME
    sent = []
    monkeypatch.setitem(BY_NAME['hub_computer_turns_at_once'], 'fn', lambda api, args: sent.append(args))
    for argv in (['computer', 'turns-at-once', 'rig-1', 'automatic'], ['computer', 'turns-at-once', 'rig-1', '3']):
        remotecli.via_tool(None, hubcli.parser().parse_args(argv))
    assert [row['limit'] for row in sent] == [None, 3]
    assert all(row['computer'] == 'rig-1' for row in sent)


def test_browser_exit_cli_sends_none_as_null_and_on_off_as_booleans(monkeypatch):
    from clients import remotecli
    from clients.hubtools import BY_NAME
    sent = []
    for name in ('hub_bot_browser_exit', 'hub_computer_browser_exit'):
        monkeypatch.setitem(BY_NAME[name], 'fn', lambda api, args: sent.append(args))
    for argv in (['bot', 'browser-exit', 'ops'], ['bot', 'browser-exit', 'ops', 'none'],
                 ['bot', 'browser-exit', 'ops', 'rig-1'], ['computer', 'browser-exit', 'rig-1', 'on', '--bots', 'ops,cpo'],
                 ['computer', 'browser-exit', 'rig-1']):
        remotecli.via_tool(None, hubcli.parser().parse_args(argv))
    assert ['computer' in row for row in sent[:3]] == [False, True, True]
    assert [row.get('computer') for row in sent[1:3]] == [None, 'rig-1']
    assert (sent[3]['sharing'], sent[3]['bots']) == (True, ['ops', 'cpo']) and 'sharing' not in sent[4]


def test_market_watch_and_briefing_commands_reach_their_routes(monkeypatch, tmp_path):
    from clients import remotecli
    calls = []

    class Api:
        def get(self, path, **kw):
            if path == 'me':
                return {'actor': 'bot:librarian'}
            calls.append(('GET', path, kw))
            return {}

        def post(self, path, body, **kw):
            calls.append(('POST', path, body))
            return {}
    monkeypatch.setenv('HUB_API_URL', 'https://api.example.invalid')
    monkeypatch.setattr(remotecli, 'Client', lambda *a, **kw: Api())
    (tmp_path / 'b.md').write_text('# Week 44\n')
    (tmp_path / 'items.json').write_text('[{"text": "Example Co raised prices.", "kind": "change", "entity_ids": ["company/example-co"]}]')
    for argv in (['market', 'watch', 'add', 'company/example-co', 'https://example.test/pricing', '--kind', 'pricing'],
                 ['market', 'watch', 'stop', 'w1'], ['market', 'watch', 'list'], ['market', 'watch', 'check'],
                 ['market', 'briefing', 'latest'],
                 ['market', 'briefing', 'post', '--week', '2026-W44', '--title', 'Week 44',
                  '--body-file', str(tmp_path / 'b.md'), '--items-file', str(tmp_path / 'items.json')]):
        remotecli.run(hubcli.parser().parse_args(argv))
    assert [(c[0], c[1]) for c in calls] == [
        ('POST', 'market/watches'), ('POST', 'market/watches/w1/stop'), ('GET', 'market/watches'),
        ('POST', 'market/watches/check'), ('GET', 'market/briefings/latest'), ('POST', 'market/briefings')]
    assert calls[0][2] == {'entity_id': 'company/example-co', 'url': 'https://example.test/pricing', 'kind': 'pricing'}
    assert calls[-1][2] == {'week': '2026-W44', 'title': 'Week 44', 'body': '# Week 44\n',
                            'items': [{'text': 'Example Co raised prices.', 'kind': 'change', 'entity_ids': ['company/example-co']}]}


def test_thread_link_slack_runs_the_mcp_tool_for_each_of_its_three_behaviours(monkeypatch):
    """`hub thread link-slack` runs `hub_thread_link_slack` (clients/hubtools.py): a channel asks for the link, the thread
    alone lists its links, and --off lists them and deletes each one."""
    from clients import remotecli
    calls = []

    class Api:
        def get(self, path, /, **query):
            if path == "me":
                return {"actor": "human:ana", "role": "owner", "kind": "owner"}
            calls.append(("GET", path, query))
            return {"links": [{"id": "L1", "state": "active"}]}

        def post(self, path, body=None, key=None):
            calls.append(("POST", path, body))
            return {"id": "L2", "state": "requested"}

        def call(self, method, path, body=None, key=None, query=None, delegate=False):
            calls.append((method, path))
            return {"id": "L1", "state": "unlinked"}

    monkeypatch.setenv("HUB_API_URL", "https://api.example.invalid")
    monkeypatch.setattr(remotecli, "Client", lambda *a, **kw: Api())
    for argv in (["thread", "link-slack", "T1", "#marketing", "--history"], ["thread", "link-slack", "T1"],
                 ["thread", "link-slack", "T1", "--off"]):
        remotecli.run(hubcli.parser().parse_args(argv))
    assert calls == [
        ("POST", "slack/links", {"thread_id": "T1", "channel": "#marketing", "history": True}),
        ("GET", "slack/links", {"thread": "T1", "limit": 100}),
        ("GET", "slack/links", {"thread": "T1", "limit": 100}),
        ("DELETE", "slack/links/L1"),
    ]


WEBHOOK_LEAVES = [  # every leaf of the command table in docs/webhooks.md, with the arguments it needs
    ["event-types", "--group", "webhook"],
    ["trigger", "list"], ["trigger", "create", "Orders"], ["trigger", "show", "orders"],
    ["trigger", "update", "orders", "--preset", "github", "--type-from", "X-GitHub-Event"],
    ["trigger", "rotate", "orders"], ["trigger", "sign", "orders", "--off"], ["trigger", "delete", "orders"],
    ["trigger", "deliveries", "orders"], ["trigger", "delivery", "orders", "d1", "--part", "2"],
    ["trigger", "replay", "orders", "d1"], ["trigger", "recover", "orders", "--since", "6"],
    ["trigger", "relay", "create", "orders"], ["trigger", "relay", "rotate", "orders"], ["trigger", "relay", "delete", "orders"],
    ["relay", "status"], ["relay", "set", "orders", "--wake", "off"],
    ["webhook", "list"], ["webhook", "create", "--name", "A", "--url", "https://hooks.example.com/x"],
    ["webhook", "show", "a"], ["webhook", "update", "a", "--name", "B"], ["webhook", "rotate", "a"],
    ["webhook", "pause", "a"], ["webhook", "resume", "a"], ["webhook", "delete", "a"],
    ["webhook", "send", "a", "--type", "custom.x", "--data", "{}"], ["webhook", "sends", "a"],
    ["webhook", "retry", "a", "s1"], ["webhook", "example", "a"], ["webhook", "enable", "a"],
    ["webhook", "recover", "a", "--since", "2026-10-20"], ["webhook", "replay", "a", "--since", "2026-10-20"],
    ["sleep", "status"], ["sleep", "set", "--hours", "4"], ["notice", "list"],
]


@pytest.mark.parametrize("argv", WEBHOOK_LEAVES, ids=lambda argv: " ".join(argv[:3]))
def test_every_webhook_command_runs_against_a_remote_server(argv, monkeypatch):
    """`hub` against HUB_API_URL + HUB_TOKEN reaches the same /api/v2 route as the server's own shell: no leaf of
    docs/webhooks.md answers "unsupported"."""
    from clients import remotecli
    seen = []

    class Anything(dict):
        def __missing__(self, key):
            return Anything()

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        def get(self, path, **params):
            seen.append(("GET", path))
            return Anything(actor="human:ana", kind="owner")

        def call(self, method, path, body=None, key=None, **kwargs):
            seen.append((method, path))
            return Anything()

        def post(self, path, body, key=None, **kwargs):
            seen.append(("POST", path))
            return Anything()
    monkeypatch.setattr(remotecli, "Client", Client)
    monkeypatch.setenv("HUB_API_URL", "https://hub.example.test")
    args = hubcli.parser().parse_args(argv)
    assert remotecli.tool_name(args.fn) in __import__("clients.hubtools", fromlist=["BY_NAME"]).BY_NAME or args.cmd == "sleep"
    remotecli.run(args)
    assert [p for m, p in seen if p != "me"], "no request was made"


def test_hub_notice_list_is_not_the_renamed_notice_send():
    """Only the old `hub notice <text>` becomes `message send --fyi`; the new `notice list` and its flags stay."""
    assert hubcli.rename_argv(["notice", "list"]) == (["notice", "list"], None)
    assert hubcli.rename_argv(["notice", "--help"]) == (["notice", "--help"], None)
    argv, said = hubcli.rename_argv(["notice", "Deploy done"])
    assert argv == ["message", "send", "Deploy done", "--fyi"] and "is now" in said


class _Recorder:
    """A client for `remotecli.run` that records each write and whether it asked to act as the requester."""
    calls = []

    def __init__(self, *args, **kw):
        self.__dict__["_tico_is_botops"] = True

    def get(self, path, **query):
        return {"actor": "bot:botops", "kind": "botops", "jobs": [{"key": "stall_triage", "on": True}]}

    def call(self, method, path, body=None, key=None, query=None, delegate=False):
        if method != "GET":
            _Recorder.calls.append((method, path, body, bool(delegate)))
        return {k: {} for k in ("trigger", "recover", "relay", "replay", "job", "type")}

    def post(self, path, body=None, key=None):
        return self.call("POST", path, body, key)

    def patch(self, path, body=None, key=None):
        return self.call("PATCH", path, body, key)


def test_botops_settings_commands_run_as_the_person_who_asked(monkeypatch):
    """Each of these changes a setting the route checks against a person (an owner, an admin, a mover, whoever manages
    the trigger): BotOps sends them as its requester, never with its own rights."""
    from clients import remotecli
    monkeypatch.setattr(remotecli, "Client", _Recorder)
    monkeypatch.setenv("HUB_API_URL", "https://tico.acme.example/api/v2")
    commands = [
        ["task", "type", "create", "--name", "Ops", "--default-visibility", "private"],
        ["task", "type", "update", "ops", "--default-visibility", "public"],
        ["task", "type", "delete", "ops"],
        ["task", "types", "--name", "Legacy"],
        ["release", "schedule", "--off"], ["release", "now", "--note", "hotfix"],
        ["task-manager", "on"], ["task-manager", "schedule", "daily", "--at", "09:00"], ["task-manager", "run"],
        ["task-manager", "bot", "off"], ["stall-triage", "--off"],
        ["team", "budget", "--hour", "5"],
        ["trigger", "create", "Orders"], ["trigger", "update", "orders", "--name", "Shop"], ["trigger", "rotate", "orders"],
        ["trigger", "sign", "orders", "--off"], ["trigger", "recover", "orders"], ["trigger", "replay", "orders", "d1"],
        ["trigger", "delete", "orders"],
        ["trigger", "relay", "create", "orders"], ["trigger", "relay", "rotate", "orders"],
        ["trigger", "relay", "delete", "orders"], ["relay", "set", "orders", "--wake", "on"],
        ["tag", "create", "launch", "--label", "Launch"],
    ]
    for words in commands:
        _Recorder.calls = []
        remotecli.run(hubcli.parser().parse_args(words))
        assert _Recorder.calls and all(delegated for *_, delegated in _Recorder.calls), (words, _Recorder.calls)
        if "--default-visibility" in words:
            assert _Recorder.calls[0][2]["default_visibility"] == words[-1]


def test_settings_cli_passes_an_id_addressed_routine_as_the_requester(monkeypatch):
    from clients import remotecli

    calls = []

    class Api:
        def __init__(self, *args, **kwargs):
            self.__dict__["_tico_is_botops"] = True

        def call(self, method, path, body=None, key=None, query=None, delegate=False):
            calls.append((method, path, body, delegate))
            if method == "POST" and path == "settings/catalog/rules.routine/plan":
                return {"run": {"method": "POST", "path": "routines/ops:qa-review",
                                 "body": {"enabled": False}},
                        "setting": {"name": "A bot's routine", "where": "Settings > Rules"},
                        "value": {"enabled": False}}
            if method == "GET" and path == "settings/catalog/rules.routine":
                return {"setting": {}, "you_may": True}
            return {}

    monkeypatch.setattr(remotecli, "Client", Api)
    monkeypatch.setenv("HUB_API_URL", "https://tico.acme.example/api/v2")
    args = hubcli.parser().parse_args(["settings", "set", "rules.routine", '{"enabled":false}',
                                      "--id", "ops:qa-review"])
    result = remotecli.run(args)
    assert result["value"] == {"enabled": False}
    assert calls[0][0:2] == ("POST", "settings/catalog/rules.routine/plan")
    assert calls[0][2] == {"value": '{"enabled":false}', "id": "ops:qa-review"}
    assert calls[0][3] is True
    assert calls[1] == ("POST", "routines/ops:qa-review", {"enabled": False}, True)


def test_a_plain_webhook_header_sends_no_secret_key():
    """BotOps' requests refuse any key named like a secret (backend/botops_act.py), so a plain header leaves it out."""
    from clients import hubtools
    fields = hubtools.webhook_options({"header": ["X-Team: acme"], "secret_header": ["X-Key: k"]})
    assert fields["headers"] == [{"name": "X-Team", "value": "acme"}, {"name": "X-Key", "secret": True, "value": "k"}]


def test_file_approved_by_path_asks_with_the_path_and_digest_as_query_fields(tmp_path, monkeypatch):
    """`hub file approved <path>` sends the path and digest as query fields, not as the client's own path argument."""
    import hashlib
    from urllib.parse import parse_qs, urlsplit
    from clients import hubtools, remotecli, tico
    sent = []
    monkeypatch.setattr(tico.Client, "request", lambda self, method, path, *a, **kw: sent.append((method, path)) or {"approved": True})
    monkeypatch.setenv("HUB_API_URL", "https://tico.acme.example")
    monkeypatch.setenv("HUB_TOKEN", "bot-token")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "brief.md").write_bytes(b"# Brief\n")
    assert remotecli.main(hubcli.parser().parse_args(["file", "approved", "brief.md", "--by", "human"])) == 0
    (method, url), = sent
    parts = urlsplit(url)
    assert (method, parts.path) == ("GET", "/api/v2/file-approvals")
    assert parse_qs(parts.query) == {"path": ["brief.md"], "digest": [hashlib.sha256(b"# Brief\n").hexdigest()], "by": ["human"]}
    assert hubtools.files_approved(tico.Client("https://tico.acme.example", "bot-token"), {"target": "brief.md"}) == {"approved": True}


def test_growth_cli_flags_become_the_api_bodies_the_playbooks_rely_on(monkeypatch):
    """The playbooks call these exact flags; each must land in the API field it names."""
    from clients import remotecli
    sent = []

    class Client:
        def __init__(self, *args, **kwargs):
            self.__dict__["_tico_is_botops"] = False

        def get(self, path, **query):
            return {"actor": "bot:growth", "kind": "bot"}

        def call(self, method, path, body=None, key=None, query=None, delegate=False):
            sent.append((method, path, body))
            return {"idea": {}, "kpi": {}, "segment": {}}

        def post(self, path, body=None, key=None):
            return self.call("POST", path, body)

        def patch(self, path, body=None, key=None):
            return self.call("PATCH", path, body)
    monkeypatch.setattr(remotecli, "Client", Client)
    monkeypatch.setenv("HUB_API_URL", "https://hub.example.test")

    def run(*argv):
        remotecli.run(hubcli.parser().parse_args(list(argv)))
        return sent[-1]
    assert run("growth", "idea", "create", "--title", "Referral card", "--bet", "b1", "--kpi", "k1", "--channel",
               "referral", "--channel", "door", "--i", "7", "--c", "5", "--e", "8", "--source-kind", "lost_test",
               "--follow-up-of", "t9", "--assignee", "me") == ("POST", "growth/ideas", {
        "title": "Referral card", "bet_id": "b1", "kpi_id": "k1", "channels": ["referral", "door"], "source_kind": "lost_test",
        "ice_i": 7, "ice_c": 5, "ice_e": 8, "follow_up_of": "t9", "assignee": "bot:growth"})
    assert run("growth", "idea", "start", "t1", "--from", "2026-10-01", "--to", "2026-10-15", "--change", "New card",
               "--stop-if", "CAC over 80", "--judged-kpi", "k2", "--min-events", "30", "--audience", "Austin")[2] == {
        "test_from": "2026-10-01", "test_to": "2026-10-15", "change": "New card", "stop_if": "CAC over 80",
        "judged_kpi_id": "k2", "min_events": 30, "audience": "Austin"}
    assert run("growth", "idea", "verdict", "t1", "--status", "lost", "--bet-effect", "weakens",
               "--confidence", "high")[1:] == ("growth/ideas/t1/verdict", {"status": "lost", "bet_effect": "weakens",
                                                                           "confidence": "high"})
    assert run("growth", "idea", "score", "t1", "--i", "3", "--c", "4", "--e", "5")[2] == {"i": 3, "c": 4, "e": 5}
    assert run("growth", "cohort", "set", "--channel", "unknown", "--month", "2026-10", "--new", "18",
               "--source", "new_customers=Stripe") == ("PUT", "growth/cohorts", {
        "channel": "unknown", "month": "2026-10", "new_customers": 18, "sources": {"new_customers": "Stripe"}})
    assert run("growth", "segment", "create", "--name", "Property managers", "--why", "Big portfolios") == (
        "POST", "growth/segments", {"name": "Property managers", "why": "Big portfolios", "status": "testing"})
    assert run("growth", "segment", "update", "segment/property-managers", "--status", "serving", "--motion",
               "sales_led", "--who-buys", "Ops lead", "--owner", "me")[1:] == (
        "growth/segments/segment/property-managers", {"status": "serving", "motion": "sales_led", "who_buys": "Ops lead",
                                                      "owner_ref": "bot:growth"})
    assert run("growth", "idea", "update", "t1", "--segment", "s1", "--segment", "s2")[2] == {"segments": ["s1", "s2"]}
    assert run("growth", "channel", "update", "door", "--segment", "")[2] == {"segments": []}       # "" clears
    assert run("growth", "cohort", "set", "--channel", "door", "--month", "2026-10", "--segment", "s1",
               "--new", "4")[2] == {"channel": "door", "month": "2026-10", "segment_id": "s1", "new_customers": 4}
    kpi = run("kpi", "update", "k1", "--parent", "k0", "--role", "north_star", "--loop", "referral:2")[2]
    assert (kpi["parent_kpi_id"], kpi["growth_role"], kpi["loop_name"], kpi["loop_step"]) == ("k0", "north_star", "referral", 2)
    assert "stage" not in kpi
    assert run("brand", "draft", "--section", "positioning", "--body", '"Acme cleans homes"')[1:] == (
        "brand/draft", {"sections": {"positioning": "Acme cleans homes"}})


def test_a_json_flag_no_command_takes_is_ignored(monkeypatch, capsys):
    p = hubcli.parser()
    assert hubcli.parse(p, ["task", "show", "t_1", "--json"]).id == ["t_1"]
    assert hubcli.parse(p, ["tool", "list", "--json"]).json is True                  # its own flag
    assert hubcli.parse(p, ["--json", "tool", "list", "--json"]).json is True
    assert hubcli.parse(p, ["meeting", "search", "--", "--json"]).q == "--json"       # a value stays a value
    for wrong in (["task", "get", "t_1", "--json"], ["task", "show", "t_1", "--json", "--bogus"]):
        with pytest.raises(SystemExit) as e:
            hubcli.parse(p, wrong)
        assert e.value.code == 2
    assert "--bogus" in capsys.readouterr().err
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"vendor": "v_1"}'))                # stdin is read once
    args = hubcli.parse(p, ["finance", "act", "bill_create", "--payload", "-", "--json"])
    assert args.payload == {"vendor": "v_1"}


def test_call_commands_run_the_call_tools_on_their_routes(monkeypatch):
    from clients import remotecli
    sent = []

    class Api:
        def __init__(self, *args, **kw):
            self.__dict__["_tico_is_botops"] = False

        def get(self, path, **query):
            sent.append(("GET", path, query))
            return {"kind": "owner"} if path == "me" else {"calls": [], "next_before": None}

        def post(self, path, body=None, key=None):
            sent.append(("POST", path, body, key))
            return {"id": "call-1", "state": "live"}

        def call(self, method, path, body=None, key=None, query=None, delegate=False):
            sent.append((method, path, body, key))
            return {"id": "call-1"}

    monkeypatch.setattr(remotecli, "Client", Api)
    monkeypatch.setenv("HUB_API_URL", "https://tico.acme.example/api/v2")
    for words in (["call", "start", "thread-1", "--title", "Acme standup"], ["call", "bot", "add", "call-1", "ops", "finance"],
                  ["call", "bot", "remove", "call-1", "ops"], ["call", "list", "thread-1", "--limit", "5"]):
        remotecli.run(hubcli.parser().parse_args(words))
    assert sent == [("POST", "threads/thread-1/calls", {"title": "Acme standup"}, None),
                    ("POST", "calls/call-1/bots", {"bots": ["ops", "finance"]}, None),
                    ("DELETE", "calls/call-1/bots/ops", {}, None),
                    ("GET", "threads/thread-1/calls", {"limit": 5})]


def test_thread_delete_runs_the_message_delete_route(monkeypatch):
    """`hub thread delete` runs `hub_thread_delete`: DELETE on the message, with no body."""
    from clients import remotecli
    from unittest.mock import Mock
    client = Mock()
    client.get.return_value = {"actor": "human:ana"}
    monkeypatch.setenv("HUB_API_URL", "https://api.example.invalid")
    monkeypatch.setattr(remotecli, "Client", lambda *a, **kw: client)
    remotecli.run(hubcli.parser().parse_args(["thread", "delete", "m7"]))
    client.call.assert_called_once_with("DELETE", "messages/m7", None, key=None)


def test_thread_upload_streams_the_file_to_the_thread_message_route(monkeypatch, tmp_path):
    """`hub thread upload` runs `hub_thread_upload`: the file is streamed (never read whole) as multipart to the thread's
    upload route, with the text, kind and reply target as form fields."""
    from pathlib import Path
    from unittest.mock import Mock
    from clients import remotecli
    path = tmp_path / "report.pdf"
    path.write_bytes(b"%PDF-1.4 test")
    client = Mock()
    client.get.return_value = {"actor": "human:ana"}
    monkeypatch.setenv("HUB_API_URL", "https://api.example.invalid")
    monkeypatch.setattr(remotecli, "Client", lambda *a, **kw: client)
    from clients import bot_files
    monkeypatch.setattr(bot_files, "checkout_root", lambda: tmp_path)
    monkeypatch.setattr(Path, "read_bytes", lambda _: pytest.fail("whole file read"))
    remotecli.run(hubcli.parser().parse_args(["thread", "upload", "T1", str(path), "--text", "Q3 numbers",
                                              "--kind", "ask", "--to", "human:ben", "--under", "m7"]))
    client.post_multipart.assert_called_once_with(
        "uploads/threads/T1/messages", {"file": path},
        {"text": "Q3 numbers", "kind": "ask", "to": "human:ben", "parent_message_id": "m7"}, key=None)


def test_thread_slack_resume_posts_to_the_link_resume_route(monkeypatch):
    """`hub thread slack-resume` runs `hub_thread_slack_resume`: POST on the link's resume route, as the person asking."""
    from clients import remotecli
    from unittest.mock import Mock
    client = Mock()
    client.get.return_value = {"actor": "human:ana", "role": "owner", "kind": "owner"}
    monkeypatch.setenv("HUB_API_URL", "https://api.example.invalid")
    monkeypatch.setattr(remotecli, "Client", lambda *a, **kw: client)
    remotecli.run(hubcli.parser().parse_args(["thread", "slack-resume", "L1"]))
    client.post.assert_called_once_with("slack/links/L1/resume", {}, key=None)


def test_rules_withdraw_posts_an_empty_body_to_the_hold_withdraw_route(monkeypatch):
    """`hub rules withdraw <hold-id>` runs `hub_rules_withdraw`: POST rule-holds/{id}/withdraw with an empty body."""
    from clients import remotecli
    from unittest.mock import Mock
    client = Mock()
    client.get.return_value = {"actor": "bot:ops"}
    monkeypatch.setenv("HUB_API_URL", "https://api.example.invalid")
    monkeypatch.setattr(remotecli, "Client", lambda *a, **kw: client)
    remotecli.run(hubcli.parser().parse_args(["rules", "withdraw", "h7"]))
    client.post.assert_called_once_with("rule-holds/h7/withdraw", {}, key=None)


def test_connection_list_reaches_the_connections_route_with_its_paging(monkeypatch):
    """`hub connections list --limit 5 --cursor c2` runs `hub_connections_list`: GET connections with the page."""
    from clients import remotecli
    from unittest.mock import Mock
    client = Mock()
    client.get.side_effect = lambda path, **kw: {"actor": "bot:ops"} if path == "me" else {"items": [], "next": None}
    monkeypatch.setenv("HUB_API_URL", "https://api.example.invalid")
    monkeypatch.setattr(remotecli, "Client", lambda *a, **kw: client)
    remotecli.run(hubcli.parser().parse_args(["connections", "list", "--limit", "5", "--cursor", "c2"]))
    from unittest.mock import call
    assert call("connections", limit=5, cursor="c2") in client.get.call_args_list
