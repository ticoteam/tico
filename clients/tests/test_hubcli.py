"""The `tico` CLI as a bot runs it: `scripts/hub` in a subprocess, remote-only.

The command surface (docs/history/hub-v2.md §5) and the exit codes are the contract every bot's AGENT.md
depends on: 0 fine, 2 refused or a non-retryable API error, 1 may retry; 4 needs sign-in, 5 not found and 64 usage
(docs/cli.md). The real HTTP round
trip is `runner_support/tests/test_conformance.py`; here the API is a stub or absent.
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

from clients import cli_spec, hubcli
from clients.tico import APIError, Client

HUB = Path(__file__).resolve().parents[2] / "scripts" / "hub"


def run_hub(*args, env=None):
    # The child is told who it is only by `env`: a person's own TICO_PROFILE in the shell that runs the
    # suite would otherwise be refused in a bot's turn before the request is ever sent.
    base = {k: v for k, v in os.environ.items() if not k.startswith(("HUB_", "TICO_"))}
    done = subprocess.run(
        [sys.executable, str(HUB), *args], env={**base, **(env or {})}, capture_output=True, text=True, timeout=60
    )
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
    assert calls == [
        ("GET", "hooks/wake-url"),
        ("PUT", "hooks/wake-url", {"url": "https://wake.acme.example/start"}),
        ("PUT", "hooks/wake-url", {"url": None}),
    ]
    assert {"tico_relay_wake_url_show", "tico_relay_wake_url_set", "tico_relay_wake_url_off"} <= set(hubtools.BY_NAME)


def test_page_link_queries_reach_the_api_through_cli_and_mcp(monkeypatch):
    from clients import hubtools, remotecli

    calls = []

    class Api:
        def __init__(self, *args, **kw):
            self.__dict__["_tico_is_botops"] = False

        def get(self, path, **query):
            calls.append((path, {k: v for k, v in query.items() if v is not None}))
            return {"pages": [], "results": []}

    monkeypatch.setattr(remotecli, "Client", Api)
    monkeypatch.setenv("HUB_API_URL", "https://tico.acme.example/api/v2")
    remotecli.run(hubcli.parser().parse_args(["page", "list", "--task", "task-1", "--limit", "2", "--cursor", "next"]))
    remotecli.run(hubcli.parser().parse_args(["file", "search", "plan", "--kind", "page"]))
    hubtools.pages_list(Api(), {"task": "task-1", "limit": 2, "cursor": "next"})
    hubtools.files_search(Api(), {"q": "plan", "kind": "page"})
    assert (
        calls
        == [
            ("pages", {"task": "task-1", "limit": 2, "cursor": "next"}),
            ("files/search", {"q": "plan", "kind": "page"}),
        ]
        * 2
    )


def test_health_findings_and_report_reach_their_routes(monkeypatch):
    from clients import hubtools, remotecli

    calls = []

    class Api:
        def __init__(self, *args, **kw):
            self.__dict__["_tico_is_botops"] = False

        def get(self, path, **query):
            if path == "me":
                return {"actor": "human:ana"}
            calls.append(("GET", path))
            return {"findings": [], "botops": True, "reports_without_asking": False}

        def post(self, path, body=None, key=None):
            calls.append(("POST", path))
            return {"ticket": "t1"}

    monkeypatch.setattr(remotecli, "Client", Api)
    monkeypatch.setenv("HUB_API_URL", "https://tico.acme.example/api/v2")
    remotecli.run(hubcli.parser().parse_args(["health", "findings"]))
    remotecli.run(hubcli.parser().parse_args(["health", "report", "hm-0123456789ab"]))
    assert calls == [("GET", "health/findings"), ("POST", "health/findings/hm-0123456789ab/report")]
    assert {"tico_health_findings", "tico_health_report"} <= set(hubtools.BY_NAME)


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
        (
            ["thread", "list", "--with", "bot:ops", "--cursor", "c2", "--limit", "5"],
            ("GET", "threads", {"with": "bot:ops", "cursor": "c2", "limit": 5}),
        ),
        (["thread", "show", "th1", "--cursor", "c2"], ("GET", "threads/th1/timeline", {"cursor": "c2"})),
        (["thread", "home", "bot:ops"], ("GET", "threads/home/bot:ops", {})),
        (["thread", "home", "group:x"], ("GET", "threads/home/group:x", {})),
        (["thread", "dm", "human:ben"], ("GET", "threads/dm", {"with": "human:ben"})),
        (
            ["thread", "dm-group", "human:ana", "bot:ops", "--body", "Hi all"],
            ("POST", "threads/dm", {"members": ["human:ana", "bot:ops"], "body": "Hi all"}),
        ),
        (["thread", "dm-group", "human:ben"], ("POST", "threads/dm", {"members": ["human:ben"], "body": None})),
        (
            [
                "thread",
                "create",
                "Refund policy",
                "--audience",
                "team",
                "--group",
                "grp1",
                "--member",
                "human:ana",
                "--member",
                "bot:legal",
                "--body",
                "Hi",
            ],
            (
                "POST",
                "threads",
                {
                    "title": "Refund policy",
                    "audience": "team",
                    "audience_group_id": "grp1",
                    "members": ["human:ana", "bot:legal"],
                    "body": "Hi",
                },
            ),
        ),
        (
            ["thread", "post", "th1", "Hello", "--to", "human:ana", "--ref", "task:t9", "--ref", "task:t10"],
            (
                "POST",
                "threads/th1/messages",
                {"text": "Hello", "kind": "say", "to": "human:ana", "refs": {"task": ["t9", "t10"]}},
            ),
        ),
        (
            ["thread", "post", "th1", "Yes", "--reply-to", "m7"],
            ("POST", "threads/th1/messages", {"text": "Yes", "kind": "say", "in_reply_to": "m7"}),
        ),
        (["thread", "show", "th1", "--flat"], ("GET", "threads/th1/timeline", {"flat": 1})),
        (["thread", "bot-status", "th1"], ("GET", "threads/th1/bot-status", {})),
        (
            ["thread", "reply", "th1", "m7", "Side note"],
            ("POST", "threads/th1/messages", {"text": "Side note", "kind": "say", "parent_message_id": "m7"}),
        ),
        (["thread", "edit", "m7", "Fixed"], ("PATCH", "messages/m7", {"text": "Fixed"})),
        (["thread", "sub", "m7"], ("POST", "threads", {"parent_message_id": "m7"})),
        (["thread", "follow", "th1", "--notify", "mentions"], ("PUT", "threads/th1/follow", {"notify": "mentions"})),
        (["thread", "follow", "th1"], ("PUT", "threads/th1/follow", {"notify": "live"})),
        (["thread", "unfollow", "th1"], ("DELETE", "threads/th1/follow", None)),
        (["thread", "pin", "th1"], ("PUT", "threads/th1/pin", None)),
        (["thread", "unpin", "th1"], ("DELETE", "threads/th1/pin", None)),
        (["thread", "catch-up", "--limit", "5"], ("GET", "threads/catch-up", {"limit": 5})),
        (["thread", "update", "th1", "--pin-for-everyone"], ("PATCH", "threads/th1", {"pinned_for_everyone": True})),
        (["thread", "update", "th1", "--unpin-for-everyone"], ("PATCH", "threads/th1", {"pinned_for_everyone": False})),
        (
            ["thread", "dm-group", "human:ana", "bot:ops", "--title", "Launch"],
            ("POST", "threads/dm", {"members": ["human:ana", "bot:ops"], "body": None, "title": "Launch"}),
        ),
        (
            ["thread", "create", "Launch", "--member", "human:ana", "--pin-for-members"],
            ("POST", "threads", {"title": "Launch", "members": ["human:ana"], "pin_for_members": True}),
        ),
        (["thread", "read", "th1", "--seq", "42"], ("POST", "threads/th1/read", {"seq": 42})),
        (["thread", "read", "th1", "--all"], ("POST", "threads/th1/read-all", {})),
        (["thread", "search", "refund terms"], ("GET", "threads/search", {"q": "refund terms"})),
        (["thread", "members", "list", "th1"], ("GET", "threads/th1/members", {})),
        (
            ["thread", "members", "add", "th1", "human:ana", "--owner"],
            ("POST", "threads/th1/members", {"actor": "human:ana", "role": "owner"}),
        ),
        (["thread", "members", "remove", "th1", "human:ana"], ("DELETE", "threads/th1/members/human:ana", None)),
        (
            ["thread", "update", "th1", "--title", "Refunds", "--archive"],
            ("PATCH", "threads/th1", {"title": "Refunds", "archived": True}),
        ),
        (["thread", "update", "th1", "--unarchive"], ("PATCH", "threads/th1", {"archived": False})),
        (["thread", "react", "m7", ":+1:"], ("PUT", "messages/m7/reactions/:+1:", None)),
        (["thread", "unreact", "m7", ":+1:"], ("DELETE", "messages/m7/reactions/:+1:", None)),
        (["thread", "open-earlier", "th1"], ("POST", "threads/th1/open-earlier", {})),
        (
            ["thread", "task", "m7", "--title", "Ship it", "--owner", "bot:ops"],
            ("POST", "threads/messages/m7/task", {"title": "Ship it", "owner": "bot:ops"}),
        ),
    ]
    for words, expected in runs:
        calls.clear()
        remotecli.run(hubcli.parser().parse_args(words))
        assert calls == [expected], words
    assert {
        "tico_thread_list",
        "tico_thread_show",
        "tico_thread_create",
        "tico_thread_post",
        "tico_thread_reply",
        "tico_thread_sub",
        "tico_thread_edit",
        "tico_thread_follow",
        "tico_thread_unfollow",
        "tico_thread_read",
        "tico_thread_search",
        "tico_thread_members_list",
        "tico_thread_members_add",
        "tico_thread_members_remove",
        "tico_thread_update",
        "tico_thread_link_slack",
        "tico_thread_dm_group",
        "tico_thread_react",
        "tico_thread_unreact",
        "tico_thread_open_earlier",
    } <= set(hubtools.BY_NAME)


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
    args = hubcli.parser().parse_args(
        ["repo", "review", "Acme/product", "7", "--head", "a" * 40, "--verdict", "approve", "--body-file", str(text)]
    )
    assert remotecli.run(args) == {"event": "APPROVE"}
    expected = {
        "repository": "Acme/product",
        "number": 7,
        "head": "a" * 40,
        "verdict": "approve",
        "body": text.read_text(),
    }
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
    assert calls == [
        ("gateway/fetch", {"url": "https://example.test/a", "max_tokens": 10000}),
        ("gateway/fetch", {"url": "https://example.test/b", "max_tokens": 2000}),
    ]


def test_product_repository_cli_previews_and_requires_the_exact_typed_confirmation(monkeypatch):
    from clients import remotecli
    from io import StringIO

    sent = []

    class Api:
        def get(self, path, **query):
            sent.append(("GET", path, query))
            return {
                "org": "Acme",
                "name": "tico-recorder",
                "repository": "Acme/tico-recorder",
                "visibility": "private",
                "auto_init": False,
                "capability": "available",
                "capability_detail": "Administration: write verified",
            }

        def post(self, path, body, key=None):
            sent.append(("POST", path, body, key))
            return {"repository": "Acme/tico-recorder"}

    args = hubcli.parser().parse_args(["repo", "product-create", "tico-recorder"])
    assert args.fn == "repo product-create"
    out = StringIO("Acme/tico-recorder\n")
    out.isatty = lambda: True
    remotecli.product_repo_create(Api(), args.name, stdin=out, stderr=StringIO())
    assert sent[0] == ("GET", "github/product-repos/preview", {"name": "tico-recorder"})
    assert sent[1][:3] == (
        "POST",
        "github/product-repos",
        {"org": "Acme", "name": "tico-recorder", "visibility": "private", "auto_init": False, "confirmed": True},
    )
    assert sent[1][3] and len(sent[1][3]) <= 200


def test_product_repository_cli_never_writes_without_matching_confirmation(monkeypatch):
    from clients import remotecli
    from io import StringIO

    calls = []

    class Api:
        def get(self, path, **query):
            return {
                "org": "Acme",
                "name": "tico-recorder",
                "repository": "Acme/tico-recorder",
                "visibility": "private",
                "auto_init": False,
                "capability": "available",
                "capability_detail": "Administration: write verified",
            }

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
            return {
                "org": "Acme",
                "name": "tico-recorder",
                "repository": "Acme/tico-recorder",
                "visibility": "private",
                "auto_init": False,
                "capability": self.capability,
                "capability_detail": "Live Administration permission is not available",
            }

        def post(self, *args, **kwargs):
            self.writes += 1

    with pytest.raises(remotecli.APIError, match="not available"):
        remotecli.product_repo_create(Api("missing"), "tico-recorder", stdin=StringIO(""), stderr=StringIO())
    available = Api("available")
    with pytest.raises(remotecli.APIError, match="--confirm Acme/tico-recorder, or type it at an interactive terminal"):
        remotecli.product_repo_create(
            available, "tico-recorder", stdin=StringIO("Acme/tico-recorder\n"), stderr=StringIO()
        )
    assert available.writes == 0


def test_product_repository_cli_confirms_without_a_terminal_only_with_the_exact_target():
    """A bot's turn has no terminal: `--confirm org/name` stands in for typing it, and must match the preview."""
    from clients import remotecli
    from io import StringIO

    sent = []

    class Api:
        def get(self, path, **query):
            return {
                "org": "Acme",
                "name": "tico-services",
                "repository": "Acme/tico-services",
                "visibility": "private",
                "auto_init": False,
                "capability": "available",
                "capability_detail": "Administration: write verified",
            }

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
    assert sent == [
        (
            "github/product-repos",
            {"org": "Acme", "name": "tico-services", "visibility": "private", "auto_init": False, "confirmed": True},
        )
    ]


class Parser(unittest.TestCase):
    def test_a_task_is_open_done_or_closed_and_update_edits_fields_only(self):
        from clients import hubtools

        for status in ("open", "done", "closed"):
            self.assertEqual(hubcli.parser().parse_args(["task", "list", "--status", status]).status, [status])
        schema = hubtools.BY_NAME["tico_task_list"]["inputSchema"]
        self.assertEqual(schema["properties"]["status"]["items"]["enum"], ["open", "done", "closed"])
        update = hubtools.BY_NAME["tico_task_update"]["inputSchema"]["properties"]
        self.assertFalse({"status", "step", "type", "owner", "waiting_on"} & set(update))
        for gone in (["--status", "doing"], ["--step", "Build"], ["--type", "Dev"], ["--owner", "ana"]):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                hubcli.parser().parse_args(["task", "update", "T1", *gone])


class Remote(unittest.TestCase):
    def test_human_override_is_refused_remotely_with_exit_two(self):
        code, out = run_hub("--human", "ana", "whoami", env={"HUB_API_URL": "http://127.0.0.1:9", "HUB_TOKEN": "x"})
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
            return self.reply(200, {"actor": "bot:assistant", "kind": "bot", "id": "assistant"})
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
            return self.reply(200, {"task": {"id": "T1", "title": body.get("title"), "version": 4}})
        if self.path == "/api/v2/tasks/T1/move":
            return self.reply(
                409,
                {
                    "error": "task_not_met",
                    "detail": "Not moved. Not met: Owner says done.",
                    "missing": ["Owner says done"],
                    "next": ['tico task move T1 next -m "why" --version 3', 'tico task close T1 -m "why" --version 3'],
                    "version": 3,
                },
            )
        if self.path == "/api/v2/tasks" and body.get("check_model"):
            return self.reply(200, {"task": {"id": "T2", "owner": body["owner"], "title": body["title"]}})
        if self.path == "/api/v2/bots/writer/tools":
            return self.reply(200, {"ok": True, "dry_run": bool(body.get("dry_run"))})
        if self.path == "/api/v2/support/reports":
            return self.reply(200, {"id": "r-1", "requirement_id": "r-1", "task_id": "T9", "status": "pending"})
        if self.path == "/api/v2/support/tickets/from-approval":
            return self.reply(200, {"ticket": {"id": "sup_1"}, "approval": body.get("approval"), "again": False})
        if any(
            part in self.path
            for part in (
                "/requesters",
                "/moderate",
                "/roadmaps/settings",
                "/waits",
                "/snooze",
                "/comments",
                "/api/v2/webhooks",
                "/api/v2/release/now",
                "/oks",
                "/thumbs",
            )
        ):
            return self.reply(200, {"ok": True})
        self.reply(422, {"error": {"code": "refused", "detail": "rule 2: not on the roster"}})


class AgainstAStub(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), Stub)
        cls.thread = Thread(target=cls.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        cls.thread.start()
        cls.env = {
            "HUB_API_URL": f"http://127.0.0.1:{cls.server.server_port}",
            "HUB_TOKEN": "turn-token",
            "HUB_BOT": "assistant",
        }

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        Stub.seen.clear()

    def test_whoami_is_the_api_identity_under_the_turn_token(self):
        code, out = run_hub("whoami", env=self.env)
        self.assertEqual(code, 0)
        self.assertEqual(out["actor"], "bot:assistant")
        self.assertEqual(Stub.seen[0][2], "Bearer turn-token")

    def test_task_update_sends_the_version_it_was_given_and_never_reads_one(self):
        code, out = run_hub(
            "task", "update", "T1", "--title", "Ship it", "-m", "renamed", "--version", "2", env=self.env
        )
        self.assertEqual(code, 0, out)
        self.assertEqual(out["title"], "Ship it")
        self.assertNotIn(("GET", "/api/v2/tasks/T1"), [(m, p) for m, p, _, _ in Stub.seen])
        posted = next(b for m, p, _, b in Stub.seen if m == "POST")
        self.assertEqual(posted, {"version": 2, "title": "Ship it", "note": "renamed"})

    def test_a_change_without_a_version_says_to_add_the_version_and_sends_nothing(self):
        from clients.hubtools import version_needed

        for argv in (["task", "update", "T1", "--title", "x"], ["task", "done", "T1", "-m", "x"]):
            Stub.seen.clear()
            code, out = run_hub(*argv, env=self.env)
            self.assertEqual(code, 64, out)  # usage (docs/cli.md)
            self.assertEqual((out["error"], out["detail"]), ("usage", version_needed("T1")))
            self.assertIn("--version N", out["detail"])
            self.assertFalse([s for s in Stub.seen if s[0] == "POST"])

    def test_a_refusal_prints_what_is_missing_and_the_commands_allowed_next(self):
        code, out = run_hub("task", "move", "T1", "next", "--version", "3", env=self.env)
        self.assertEqual(code, 2, out)
        self.assertEqual(
            out["_stdout"].strip().splitlines(),
            [
                "Not moved. Not met: Owner says done.",
                "Missing:",
                "  Owner says done",
                "You can:",
                '  tico task move T1 next -m "why" --version 3',
                '  tico task close T1 -m "why" --version 3',
            ],
        )

    def test_task_list_still_filters_closed_tasks(self):
        code, out = run_hub("task", "list", "--status", "closed", env=self.env)
        self.assertEqual(code, 0, out)
        self.assertEqual(out, [{"id": "T1", "status": "closed"}])
        self.assertEqual(
            [(m, p) for m, p, _, _ in Stub.seen], [("GET", "/api/v2/me"), ("GET", "/api/v2/tasks?status=closed")]
        )

    def test_task_child_on_a_check_model_is_the_bots_own_subtask(self):
        code, out = run_hub(
            "task",
            "child",
            "T1",
            "--check-model",
            "grok",
            "--role",
            "review",
            "--title",
            "Review PR 700",
            "--body",
            "https://github.com/ticoteam/tico/pull/700",
            env=self.env,
        )
        self.assertEqual(code, 0, out)
        posted = next(b for m, p, _, b in Stub.seen if m == "POST" and p == "/api/v2/tasks")
        self.assertEqual(
            (posted["owner"], posted["check_model"], posted["check_role"], posted["parent"]),
            ("bot:assistant", "grok", "review", "T1"),
        )
        # With no value, the server picks the company's check model.
        code, out = run_hub(
            "task", "child", "T1", "--check-model", "--role", "review", "--title", "Review PR 701", env=self.env
        )
        self.assertEqual(code, 0, out)
        posted = [b for m, p, _, b in Stub.seen if m == "POST" and p == "/api/v2/tasks"][-1]
        self.assertEqual((posted["owner"], posted["check_model"]), ("bot:assistant", "auto"))

    def test_tool_add_dry_run_is_checked_by_the_server_not_the_task_dry_run(self):
        code, out = run_hub(
            "tool",
            "add",
            "writer",
            "notion",
            "--can",
            "read",
            "--mcp-url",
            "https://mcp.notion.example/mcp",
            "--dry-run",
            env=self.env,
        )
        self.assertEqual(code, 0, out)
        posted = next(b for m, p, _, b in Stub.seen if m == "POST" and p == "/api/v2/bots/writer/tools")
        self.assertTrue(posted["dry_run"])
        self.assertEqual(posted["mcp"]["url"], "https://mcp.notion.example/mcp")

    def test_support_report_asks_an_ok_on_the_exact_message_and_send_names_it(self):
        import hashlib

        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "report.md"
            report.write_bytes(b"tico task update fails.\r\nError: refused (version)\r\n\r\n")
            argv = ("support", "report", "--file", str(report), "--no-ids", "--task", "T9")
            code, out = run_hub(*argv, env=self.env)
        self.assertEqual(code, 0, out)
        self.assertEqual((out["ok"], out["task_id"]), ("r-1", "T9"))
        self.assertIn("tico support send --ok r-1", out["next"])
        posted = next(b for m, p, _, b in Stub.seen if m == "POST" and p == "/api/v2/support/reports")
        message = "tico task update fails.\nError: refused (version)"
        self.assertEqual(
            posted,
            {
                "task_id": "T9",
                "payload": {
                    "message": message,
                    "content_sha256": hashlib.sha256(message.encode()).hexdigest(),
                    "include_ids": False,
                },
            },
        )
        code, out = run_hub("support", "send", "--ok", "r-1", env=self.env)
        self.assertEqual((code, out["ticket"]["id"]), (0, "sup_1"))
        posted = next(b for m, p, _, b in Stub.seen if m == "POST" and p == "/api/v2/support/tickets/from-approval")
        self.assertEqual(posted, {"approval": "r-1"})

    def test_a_public_reply_names_the_ok_a_person_gave_and_the_old_approval_flag_is_gone(self):
        def last(path):
            return [b for m, p, _, b in Stub.seen if m == "POST" and p == path][-1]

        code, out = run_hub("task", "comment", "T1", "Fixed in 1.2", "--public", "--ok", "r-7", env=self.env)
        self.assertEqual(code, 0, out)
        self.assertEqual(last("/api/v2/tasks/T1/comments")["approval_id"], "r-7")
        code, out = run_hub("task", "comment-edit", "T1", "m-1", "Fixed in 1.2.1", "--ok", "r-8", env=self.env)
        self.assertEqual(code, 0, out)
        self.assertEqual(last("/api/v2/tasks/T1/comments/m-1"), {"text": "Fixed in 1.2.1", "approval_id": "r-8"})
        for argv in (["comment", "T1", "x"], ["comment-edit", "T1", "m-1", "x"]):
            with self.assertRaises(SystemExit):
                hubcli.parser().parse_args(["task", *argv, "--approval-id", "a-1"])

    def test_an_ok_bound_to_a_subject_is_asked_on_the_task_and_used_once_by_its_id(self):
        content = '{"project":"launch","usd":"10.00"}'
        argv = ("task", "ask", "T1", "Raise it?", "--to", "human:ana", "--for", "budget_raise:launch")
        code, out = run_hub(*argv, "--content", content, "--version", "3", env=self.env)
        self.assertEqual(code, 0, out)
        posted = [b for m, p, _, b in Stub.seen if m == "POST" and p == "/api/v2/tasks/T1/oks"]
        self.assertEqual(
            posted,
            [
                {
                    "to": "human:ana",
                    "line": "Raise it?",
                    "version": 3,
                    "subject": "budget_raise:launch",
                    "content": {"project": "launch", "usd": "10.00"},
                }
            ],
        )
        code, out = run_hub("ok", "use", "r-1", "--for", "budget_raise:launch", "--content", content, env=self.env)
        self.assertEqual(code, 0, out)
        used = [b for m, p, _, b in Stub.seen if m == "POST" and p == "/api/v2/oks/r-1/use"]
        self.assertEqual(used, [{"subject": "budget_raise:launch", "content": {"project": "launch", "usd": "10.00"}}])
        code, _ = run_hub(*argv, "--choices", "a,b", "--content", content, "--version", "3", env=self.env)
        self.assertNotEqual(code, 0)  # a bound OK is never a question with choices

    def test_task_snooze_sends_its_date_why_and_version(self):
        def last(method, path):
            return [b for m, p, _, b in Stub.seen if m == method and p.split("?")[0] == path][-1]

        argv = ("task", "snooze", "T1", "--until", "2026-11-14", "-m", "Vendor replies by Nov 14", "--version", "3")
        self.assertEqual(run_hub(*argv, env=self.env)[0], 0)
        self.assertEqual(
            last("POST", "/api/v2/tasks/T1/snooze"),
            {"version": 3, "until": "2026-11-14", "why": "Vendor replies by Nov 14"},
        )
        self.assertEqual(run_hub("task", "snooze", "T1", "-m", "On ice", "--version", "3", env=self.env)[0], 0)
        self.assertEqual(last("POST", "/api/v2/tasks/T1/snooze"), {"version": 3, "why": "On ice"})

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
NAMES = {"company_name": "Acme Ltd", "app_name": "Acme OS", "assistant_name": "Ada", "assistant_bot": "assistant"}
ONBOARDING = {
    "names": NAMES,
    "answers": {
        "what_we_do": "we clean holiday homes",
        "customers": "owners",
        "team_size": "nine people",
        "work_arrives": ["email"],
        "never_without_person": ["refunds"],
    },
    "selected": {
        "seo": {"template": "specialist", "display_name": "Sam", "instructions": "# Sam\n\n## Owns\n- the blog\n"}
    },
    "completed": True,
}


class BotSetup(unittest.TestCase):
    """`tico template list` and `tico bot create|check`: the three commands BotOps needs in a turn.

    The parsing is the real parser and the work is the real `clients/catalog.py`; only the API is
    a fake, so what these assert is which endpoints a command reads and what it writes to disk.
    """

    class FakeClient:
        def __init__(self, cards=None, onboarding=ONBOARDING):
            self.cards, self.record, self.seen, self.posted = cards, onboarding, [], []
            self.github = None  # the APIError `github/repos` answers with; None creates the repository

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
            if path == "me":  # BotOps; a person's turn seeds the template's automations as that person
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

        self.client.github = APIError(
            "github_permission_missing",
            "A person must act: the GitHub App was set up without permission to create repositories.",
            409,
            False,
        )
        args = hubcli.parser().parse_args(["bot", "create", "seo", "--template", "specialist"])
        out = io.StringIO()
        with (
            mock.patch.object(self.remotecli, "Client", lambda *a, **k: self.client),
            mock.patch.dict(os.environ, {"HUB_API_URL": "http://hub.invalid"}),
            contextlib.redirect_stdout(out),
        ):
            code = self.remotecli.main(args)
        self.assertNotEqual(code, 0)
        said = json.loads(out.getvalue())
        self.assertEqual(said["error"], "github_permission_missing")
        self.assertTrue(said["detail"].startswith("Not finished: A person must act"))
        self.assertIn("stays planned", said["detail"])
        self.assertTrue(any(self.workspace.iterdir()), "the local build is kept")
        self.client.github = APIError("github_not_connected", "GitHub is not connected.", 409, False)
        self.assertIn("skipped", self.remotecli.github_repository(self.client, "seo", self.workspace))
        # Chosen repositories only, or a name someone else took: nothing is made or adopted; the bot stays here.
        for code in ("github_selected_repos", "github_repo_taken", "github_repo_exists"):
            self.client.github = APIError(code, "No repository was made for seo.", 409, False)
            answer = self.remotecli.github_repository(self.client, "seo", self.workspace)
            self.assertEqual(answer, {"skipped": "No repository was made for seo."})

    def test_creating_a_bot_the_server_has_not_registered_keeps_it_local(self):
        from clients.tico import APIError

        self.client.github = APIError("forbidden", "seo is not a bot being set up or running", 403, False)
        made = self.run_command("bot", "create", "seo", "--template", "specialist")
        self.assertIn("skipped", made["github"])
        self.assertIn("tico bot repo-create seo --empty", made["github"]["note"])

    def test_a_bot_created_without_a_slug_takes_the_one_picked_from_its_name(self):
        # The server picks it in a person's turn; the build and its repository use that slug.
        self.client.post = lambda path, body, key=None: (
            self.client.posted.append((path, body)) or {"slug": "head-of-marketing-2", "created": True}
            if path == "bots/register"
            else {"repository": "Acme/bot-" + body["slug"]}
        )
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


def test_create_sends_its_pipeline_and_parent_the_same_from_the_cli_and_mcp(monkeypatch):
    from clients import remotecli, hubtools

    sent = []

    class Api:
        def get(self, path, **kw):
            return {"actor": "human:ana"}

        def post(self, path, body, **kw):
            sent.append((path, body))
            return {"task": {"id": "synthetic"}}

    api = Api()
    monkeypatch.setenv("HUB_API_URL", "https://api.example.invalid")
    monkeypatch.setattr(remotecli, "Client", lambda *a, **kw: api)
    argv = ["task", "create", "--owner", "cpo", "--title", "UI change", "--body", "x", "--pipeline", "general"]
    remotecli.run(hubcli.parser().parse_args(argv + ["--parent", "T1"]))
    hubtools.BY_NAME["tico_task_create"]["fn"](
        api, {"owner": "cpo", "title": "UI change", "body": "x", "pipeline": "general", "parent": "T1"}
    )
    assert len(sent) == 2 and sent[0] == sent[1]
    path, body = sent[0]
    assert path == "tasks" and (body["pipeline"], body["parent"]) == ("general", "T1")
    assert not {"type", "step", "status", "relations", "next_run", "qa"} & set(body)
    for gone in (["--type", "Dev"], ["--step", "Build"], ["--status", "doing"], ["--next-run"], ["--qa", "yes"]):
        with pytest.raises(SystemExit):
            hubcli.parser().parse_args(argv + gone)


def test_turns_at_once_cli_preserves_explicit_unlimited(monkeypatch):
    from clients import remotecli
    from clients.hubtools import BY_NAME

    sent = []
    monkeypatch.setitem(BY_NAME['tico_bot_turns_at_once'], 'fn', lambda api, args: sent.append(args))
    for argv in (
        ['bot', 'turns-at-once', 'ops'],
        ['bot', 'turns-at-once', 'ops', 'unlimited'],
        ['bot', 'turns-at-once', 'ops', '3'],
    ):
        remotecli.via_tool(None, hubcli.parser().parse_args(argv))
    assert 'limit' not in sent[0]
    assert [row['limit'] for row in sent[1:]] == [None, 3]


def test_computer_turns_at_once_cli_sends_automatic_as_null(monkeypatch):
    from clients import remotecli
    from clients.hubtools import BY_NAME

    sent = []
    monkeypatch.setitem(BY_NAME['tico_computer_turns_at_once'], 'fn', lambda api, args: sent.append(args))
    for argv in (['computer', 'turns-at-once', 'rig-1', 'automatic'], ['computer', 'turns-at-once', 'rig-1', '3']):
        args = hubcli.parser().parse_args(argv)
        cli_spec.of(args).call(None, args)
    assert [row['limit'] for row in sent] == [None, 3]
    assert all(row['computer'] == 'rig-1' for row in sent)


def test_browser_exit_cli_sends_none_as_null_and_on_off_as_booleans(monkeypatch):
    from clients import remotecli
    from clients.hubtools import BY_NAME

    sent = []
    for name in ('tico_bot_browser_exit', 'tico_computer_browser_exit'):
        monkeypatch.setitem(BY_NAME[name], 'fn', lambda api, args: sent.append(args))
    for argv in (
        ['bot', 'browser-exit', 'ops'],
        ['bot', 'browser-exit', 'ops', 'none'],
        ['bot', 'browser-exit', 'ops', 'rig-1'],
        ['computer', 'browser-exit', 'rig-1', 'on', '--bots', 'ops,cpo'],
        ['computer', 'browser-exit', 'rig-1', 'off'],
        ['computer', 'browser-exit', 'rig-1'],
    ):
        args = hubcli.parser().parse_args(argv)
        cli_spec.of(args).call(None, args) if cli_spec.of(args) else remotecli.via_tool(None, args)
    assert ['computer' in row for row in sent[:3]] == [False, True, True]
    assert [row.get('computer') for row in sent[1:3]] == [None, 'rig-1']
    assert (sent[3]['sharing'], sent[3]['bots']) == (True, ['ops', 'cpo']) and sent[4]['sharing'] is False
    assert 'sharing' not in sent[5]


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
    (tmp_path / 'items.json').write_text(
        '[{"text": "Example Co raised prices.", "kind": "change", "entity_ids": ["company/example-co"]}]'
    )
    (tmp_path / 'answers.json').write_text('{"changed": "Example Co raised prices."}')
    for argv in (
        ['market', 'watch', 'add', 'company/example-co', 'https://example.test/pricing', '--kind', 'pricing'],
        ['market', 'watch', 'stop', 'w1'],
        ['market', 'watch', 'list'],
        ['market', 'watch', 'check'],
        ['market', 'briefing', 'latest'],
        [
            'market',
            'briefing',
            'post',
            '--week',
            '2026-W44',
            '--title',
            'Week 44',
            '--body-file',
            str(tmp_path / 'b.md'),
            '--items-file',
            str(tmp_path / 'items.json'),
            '--answers-file',
            str(tmp_path / 'answers.json'),
        ],
    ):
        remotecli.run(hubcli.parser().parse_args(argv))
    assert [(c[0], c[1]) for c in calls] == [
        ('POST', 'market/watches'),
        ('POST', 'market/watches/w1/stop'),
        ('GET', 'market/watches'),
        ('POST', 'market/watches/check'),
        ('GET', 'market/briefings/latest'),
        ('POST', 'market/briefings'),
    ]
    assert calls[0][2] == {'entity_id': 'company/example-co', 'url': 'https://example.test/pricing', 'kind': 'pricing'}
    assert calls[-1][2] == {
        'week': '2026-W44',
        'title': 'Week 44',
        'body': '# Week 44\n',
        'items': [{'text': 'Example Co raised prices.', 'kind': 'change', 'entity_ids': ['company/example-co']}],
        'answers': {'changed': 'Example Co raised prices.'},
    }


def test_market_apply_sends_the_verified_date_to_an_entity_or_a_new_one_and_only_when_given(monkeypatch):
    """`tico market apply --last-verified-at` puts the date on the existing entity, or on the new entity it creates;
    without the flag the body is what it was. The date is passed through: the server refuses a bad one."""
    import pytest
    from clients import remotecli
    from clients.tico import APIError

    calls = []

    class Api:
        def get(self, path, **kw):
            return {'actor': 'bot:librarian'} if path == 'me' else {}

        def post(self, path, body, **kw):
            calls.append((path, body))
            return {}

    monkeypatch.setenv('HUB_API_URL', 'https://api.example.invalid')
    monkeypatch.setattr(remotecli, 'Client', lambda *a, **kw: Api())
    base = [
        'market',
        'apply',
        'ins-1',
        '--source',
        'https://acme.example/pricing',
        '--quote',
        'Pro is $12',
        '--our-read',
        'A price rise.',
    ]
    remotecli.run(hubcli.parser().parse_args(base + ['--entity-id', 'company/x', '--last-verified-at', '2026-11-03']))
    remotecli.run(
        hubcli.parser().parse_args(
            base + ['--entity-type', 'company', '--entity-name', 'Acme', '--last-verified-at', '2026-11-03']
        )
    )
    remotecli.run(hubcli.parser().parse_args(base + ['--entity-id', 'company/x']))
    with pytest.raises(APIError):
        remotecli.run(hubcli.parser().parse_args(base + ['--last-verified-at', '2026-11-03']))
    evidence = {
        'source_url': 'https://acme.example/pricing',
        'source_kind': 'other',
        'quote': 'Pro is $12',
        'our_read': 'A price rise.',
    }
    assert calls == [
        (
            'market/insights/ins-1/apply',
            {'evidence': evidence, 'entity_id': 'company/x', 'last_verified_at': '2026-11-03'},
        ),
        (
            'market/insights/ins-1/apply',
            {
                'evidence': evidence,
                'entity': {'type': 'company', 'name': 'Acme', 'summary': '', 'last_verified_at': '2026-11-03'},
            },
        ),
        ('market/insights/ins-1/apply', {'evidence': evidence, 'entity_id': 'company/x'}),
    ]


def test_the_market_apply_tool_puts_the_verified_date_in_the_body_for_an_entity_or_a_new_one():
    from clients.hubtools import BY_NAME

    calls = []

    class Api:
        def post(self, path, body=None, key=None):
            calls.append((path, body))
            return {}

    fn = BY_NAME['tico_market_apply']['fn']
    fn(Api(), {'id': 'ins-1', 'entity_id': 'company/x', 'last_verified_at': '2026-11-03'})
    fn(Api(), {'id': 'ins-1', 'entity_type': 'company', 'entity_name': 'Acme', 'last_verified_at': '2026-11-03'})
    assert calls[0][1]['last_verified_at'] == '2026-11-03' and 'entity' not in calls[0][1]
    assert calls[1][1]['entity']['last_verified_at'] == '2026-11-03' and 'last_verified_at' not in calls[1][1]
    assert 'last_verified_at' in BY_NAME['tico_market_apply']['inputSchema']['properties']


def test_market_apply_refuses_a_timestamp_or_a_written_date_and_sends_nothing(monkeypatch):
    """--last-verified-at takes a plain YYYY-MM-DD: a timestamp or a written date is refused before any request, never cut down."""
    import pytest
    from clients import remotecli
    from clients.hubtools import BY_NAME
    from clients.tico import APIError

    sent = []

    class Api:
        def get(self, path, **kw):
            return {'actor': 'bot:librarian'} if path == 'me' else {}

        def post(self, path, body, **kw):
            sent.append((path, body))
            return {}

    monkeypatch.setenv('HUB_API_URL', 'https://api.example.invalid')
    monkeypatch.setattr(remotecli, 'Client', lambda *a, **kw: Api())
    base = ['market', 'apply', 'ins-1', '--entity-id', 'company/x', '--source', 'https://acme.example/pricing']
    for bad in ('2026-11-03T09:00:00Z', '3 Nov 2026', '2026-11-3', '2026-02-31', '2026-13-01'):
        with pytest.raises(APIError) as refused:
            remotecli.run(hubcli.parser().parse_args(base + ['--last-verified-at', bad]))
        assert refused.value.status == 422 and 'YYYY-MM-DD' in str(refused.value)
    assert sent == []

    api = BY_NAME['tico_market_apply']
    for bad in ('2026-11-03T09:00:00Z', '2026-02-31'):
        with pytest.raises(ValueError):
            api['fn'](Api(), {'id': 'ins-1', 'entity_id': 'company/x', 'last_verified_at': bad})
    assert sent == []


def test_thread_link_slack_runs_the_mcp_tool_for_each_of_its_three_behaviours(monkeypatch):
    """`tico thread link-slack` runs `tico_thread_link_slack` (clients/hubtools.py): a channel asks for the link, the thread
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
    for argv in (
        ["thread", "link-slack", "T1", "#marketing", "--history"],
        ["thread", "link-slack", "T1"],
        ["thread", "link-slack", "T1", "--off"],
    ):
        remotecli.run(hubcli.parser().parse_args(argv))
    assert calls == [
        ("POST", "slack/links", {"thread_id": "T1", "channel": "#marketing", "history": True}),
        ("GET", "slack/links", {"thread": "T1", "limit": 100}),
        ("GET", "slack/links", {"thread": "T1", "limit": 100}),
        ("DELETE", "slack/links/L1"),
    ]


def test_who_can_see_runs_from_the_cli_and_its_mcp_tools(monkeypatch):
    """`tico task create --private --reader X` sends the readers; `tico task readers list|add|remove` run the
    tico_task_readers_* tools against /tasks/{id}/readers."""
    from clients import remotecli

    calls = []

    class Api:
        def get(self, path, /, **query):
            if path == "me":
                return {"actor": "human:ana", "role": "owner", "kind": "owner"}
            calls.append(("GET", path, None))
            return {"private": True, "readers": []}

        def post(self, path, body=None, key=None, **kw):
            calls.append(("POST", path, body))
            return {"id": "T1", "private": True, "readers": ["human:ben"]}

        def call(self, method, path, body=None, key=None, query=None, delegate=False):
            calls.append((method, path, body))
            return {"id": "T1"}

    monkeypatch.setenv("HUB_API_URL", "https://api.example.invalid")
    monkeypatch.setattr(remotecli, "Client", lambda *a, **kw: Api())
    remotecli.run(
        hubcli.parser().parse_args(
            [
                "task",
                "create",
                "--owner",
                "ops",
                "--title",
                "Draft the offer letter",
                "--body",
                "For the new hire.",
                "--private",
                "--reader",
                "ben",
                "--reader",
                "group:desk",
            ]
        )
    )
    made = [body for method, path, body in calls if method == "POST" and path == "tasks"]
    assert made and made[-1]["private"] is True and made[-1]["readers"] == ["ben", "group:desk"], calls
    calls.clear()
    for argv in (
        ["task", "readers", "list", "T1"],
        ["task", "readers", "add", "T1", "ben", "bot:cmo"],
        ["task", "readers", "remove", "T1", "ben"],
    ):
        args = hubcli.parser().parse_args(argv)
        assert remotecli.tool_name(args.fn) == "tico_task_readers_" + argv[2]
        remotecli.run(args)
    assert calls == [
        ("GET", "tasks/T1/readers", None),
        ("POST", "tasks/T1/readers", {"add": ["ben", "bot:cmo"]}),
        ("POST", "tasks/T1/readers", {"remove": ["ben"]}),
    ], calls


def test_followers_run_from_the_cli_and_its_mcp_tools(monkeypatch):
    """`tico task create --follow` sends follow; `tico task followers|follow|unfollow` run the tico_task_* tools against
    /tasks/{id}/followers."""
    from clients import remotecli

    calls = []

    class Api:
        def get(self, path, /, **query):
            if path == "me":
                return {"actor": "human:ana", "role": "owner", "kind": "owner"}
            calls.append(("GET", path, None))
            return {"task_id": "T1", "following": False, "notify": None, "followers": []}

        def post(self, path, body=None, key=None, **kw):
            calls.append(("POST", path, body))
            return {"id": "T1"}

        def call(self, method, path, body=None, key=None, query=None, delegate=False):
            calls.append((method, path, body))
            return {"task_id": "T1", "following": True, "notify": "live", "followers": []}

    monkeypatch.setenv("HUB_API_URL", "https://api.example.invalid")
    monkeypatch.setattr(remotecli, "Client", lambda *a, **kw: Api())
    for follow in (True, False):
        remotecli.run(
            hubcli.parser().parse_args(
                ["task", "create", "--owner", "ops", "--title", "Ops weekly sweep", "--body", "Check the queue."]
                + (["--follow"] if follow else [])
            )
        )
    made = [body for method, path, body in calls if method == "POST" and path == "tasks"]
    assert len(made) == 2 and made[0]["follow"] is True and "follow" not in made[1], calls
    calls.clear()
    for argv, tool in (
        (["task", "followers", "T1"], "tico_task_followers"),
        (["task", "follow", "T1", "--level", "digest"], "tico_task_follow"),
        (["task", "follow", "T1", "--who", "bot:cpo"], "tico_task_follow"),
        (["task", "unfollow", "T1", "--who", "ben"], "tico_task_follow"),
        (["task", "unfollow", "T1"], "tico_task_follow"),
    ):
        args = hubcli.parser().parse_args(argv)
        assert remotecli.tool_name(args.fn) == tool
        remotecli.run(args)
    assert calls == [
        ("GET", "tasks/T1/followers", None),
        ("PUT", "tasks/T1/followers", {"following": True, "notify": "digest"}),
        ("PUT", "tasks/T1/followers", {"following": True, "actor": "bot:cpo"}),
        ("PUT", "tasks/T1/followers", {"following": False, "actor": "ben"}),
        ("PUT", "tasks/T1/followers", {"following": False}),
    ], calls
    with pytest.raises(SystemExit):
        hubcli.parser().parse_args(["task", "follow", "T1", "--level", "loud"])


WEBHOOK_LEAVES = [  # every leaf of the command table in docs/webhooks.md, with the arguments it needs
    ["event-types", "--group", "webhook"],
    ["trigger", "list"],
    ["trigger", "create", "Orders"],
    ["trigger", "show", "orders"],
    ["trigger", "update", "orders", "--preset", "github", "--type-from", "X-GitHub-Event"],
    ["trigger", "rotate", "orders"],
    ["trigger", "sign", "orders", "--off"],
    ["trigger", "delete", "orders"],
    ["trigger", "deliveries", "orders"],
    ["trigger", "delivery", "orders", "d1", "--part", "2"],
    ["trigger", "replay", "orders", "d1"],
    ["trigger", "recover", "orders", "--since", "6"],
    ["trigger", "relay", "create", "orders"],
    ["trigger", "relay", "rotate", "orders"],
    ["trigger", "relay", "delete", "orders"],
    ["relay", "status"],
    ["relay", "set", "orders", "--wake", "off"],
    ["webhook", "list"],
    ["webhook", "create", "--name", "A", "--url", "https://hooks.example.com/x"],
    ["webhook", "show", "a"],
    ["webhook", "update", "a", "--name", "B"],
    ["webhook", "rotate", "a"],
    ["webhook", "pause", "a"],
    ["webhook", "resume", "a"],
    ["webhook", "delete", "a"],
    ["webhook", "send", "a", "--type", "custom.x", "--data", "{}"],
    ["webhook", "sends", "a"],
    ["webhook", "retry", "a", "s1"],
    ["webhook", "example", "a"],
    ["webhook", "enable", "a"],
    ["webhook", "recover", "a", "--since", "2026-10-20"],
    ["webhook", "replay", "a", "--since", "2026-10-20"],
    ["sleep", "status"],
    ["sleep", "set", "--hours", "4"],
    ["notice", "list"],
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
    assert (
        remotecli.tool_name(args.fn) in __import__("clients.hubtools", fromlist=["BY_NAME"]).BY_NAME
        or args.cmd == "sleep"
    )
    remotecli.run(args)
    assert [p for m, p in seen if p != "me"], "no request was made"


def test_hub_notice_list_is_not_the_renamed_notice_send():
    """Only the old `tico notice <text>` becomes `message send --fyi`; the new `notice list` and its flags stay."""
    assert hubcli.rename_argv(["notice", "list"]) == (["notice", "list"], None)
    assert hubcli.rename_argv(["notice", "--help"]) == (["notice", "--help"], None)
    argv, said = hubcli.rename_argv(["notice", "Deploy done"])
    assert argv == ["message", "send", "Deploy done", "--fyi"] and "is now" in said


class _Me:
    def get(self, path, **kw):
        return {"actor": "bot:ops"}


def test_org_models_reaches_its_tool_and_is_not_renamed(monkeypatch, capsys):
    """`tico org models` is not renamed to `tico team show` (only bare `tico org` is) and reaches tico_org_models."""
    from clients import remotecli
    from clients.hubtools import BY_NAME

    sent = []
    monkeypatch.setitem(BY_NAME["tico_org_models"], "fn", lambda api, args: sent.append(args) or {"models": []})
    monkeypatch.setattr(remotecli, "Client", lambda *a, **kw: _Me())
    monkeypatch.setenv("HUB_API_URL", "https://synthetic.invalid/api/v2")
    for argv in (["org", "models"], ["org", "order", "ops"], ["org", "reports-to", "bot:ops", "human:ana"]):
        assert hubcli.rename_argv(argv) == (argv, None)
    assert hubcli.main(["org", "models"]) == 0
    assert len(sent) == 1
    assert "is now" not in capsys.readouterr().err


def test_check_names_leaves_every_org_subcommand_alone():
    """`tico bot check-names --fix` rewrites old spellings; `tico org <sub>` is live, so none of them is rewritten."""
    import json
    from pathlib import Path

    exempt = json.loads((Path(hubcli.__file__).with_name("renames.json")).read_text())["commands_exempt"]
    org = next(a for a in hubcli.parser()._actions if getattr(a, "choices", None) and "org" in a.choices)
    subs = next(a for a in org.choices["org"]._actions if getattr(a, "choices", None)).choices
    assert {("org", sub) for sub in subs} <= {tuple(e) for e in exempt}


def test_chat_send_runs_message_send_with_a_notice(monkeypatch):
    """`tico chat send` was the same as `tico message send`; the old line runs it, harness commands included."""
    argv, said = hubcli.rename_argv(["chat", "send", "ops", "/compact", "--conversation", "c1", "--command"])
    assert argv == ["message", "send", "ops", "/compact", "--conversation", "c1", "--command"] and "is now" in said
    args = hubcli.parser().parse_args(argv)
    assert (args.fn, args.conversation, args.command) == ("message send", "c1", True)

    from clients import remotecli
    from clients.hubtools import BY_NAME

    sent = []
    monkeypatch.setitem(BY_NAME["tico_message_send"], "fn", lambda api, args: sent.append(args) or {})
    monkeypatch.setattr(remotecli, "Client", lambda *a, **kw: _Me())
    monkeypatch.setenv("HUB_API_URL", "https://synthetic.invalid/api/v2")
    remotecli.run(args)
    assert (sent[0]["to"], sent[0]["text"], sent[0]["conversation_id"], sent[0]["command"]) == (
        "ops",
        "/compact",
        "c1",
        True,
    )


@pytest.mark.parametrize(
    "argv, instead",
    [
        (["question", "ask", "ops", "Which plan?"], "tico task ask"),
        (["question", "answer", "m1", "Yes"], "tico task answer"),
        (["ask", "ops", "Which plan?"], "tico task ask"),
        (["answer", "m1", "Yes"], "tico task answer"),
        (["task", "question", "Which plan?", "--on", "ana"], "tico task ask"),
        (["task", "ask-resolve", "t1", "c1"], "Needs you"),
        (["task", "answers", "t1"], "tico task show <id>, under To leave"),
        (["approval", "approve", "ap1"], "tico task answer"),
        (["approval", "request", "--kind", "send"], "tico task ask"),
        (["task", "check-back", "t1", "--in", "2h"], "tico task snooze"),
        (["task", "later", "t1", "--handled"], "tico task snooze"),
        (["task", "defer", "t1", "--handled"], "tico task snooze"),
        (["task", "bulk", "close"], "one task at a time"),
        (["task", "waive-qa", "t1"], "tico task move"),
        (["task", "types"], "tico task show"),
        (["task", "type", "create", "--name", "Ops"], "tico task show"),
        (["task", "stuck"], "tico task list"),
        (["growth", "idea", "create", "--title", "Referral"], "tico task create"),
    ],
)
def test_a_removed_command_prints_one_line_naming_its_replacement(argv, instead, capsys, monkeypatch):
    """Asks, approvals and the old task engine's commands are gone: each old command says in one line what to use
    instead, sends nothing and exits 1."""
    monkeypatch.setattr(hubcli, "run", lambda *a: pytest.fail("a removed command ran"))
    assert hubcli.main(argv) == 1
    out = capsys.readouterr()
    lines = out.err.strip().splitlines()
    assert out.out == "" and len(lines) == 1 and lines[0].startswith('tico: "') and "is gone" in lines[0]
    assert instead in lines[0]
    if instead == "tico task ask" and argv[0] in ("ask", "question"):
        assert "tico message send" in lines[0] and "--needs-action" in lines[0]


def test_goal_checkin_list_with_the_list_word_is_checkin_list():
    """`goal checkin list <id>` is the noun-sub-noun-verb form of `goal checkin-list <id>`: matched before the parse, so it
    never posts a check-in on a goal named "list"."""
    argv, said = hubcli.rename_argv(["goal", "checkin", "list", "g1"])
    assert argv == ["goal", "checkin-list", "g1"] and "is now" in said
    assert hubcli.parser().parse_args(argv).fn == "goal checkin-list"


class _Recorder:
    """A client for `remotecli.run` that records each write and whether it asked to act as the requester."""

    calls = []

    def __init__(self, *args, **kw):
        self.__dict__["_tico_is_botops"] = True

    def get(self, path, **query):
        return {"actor": "bot:botops", "kind": "botops", "jobs": []}

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
        ["release", "schedule", "--off"],
        ["release", "now", "--note", "hotfix"],
        ["general-manager", "on"],
        ["general-manager", "schedule", "daily", "--at", "09:00"],
        ["general-manager", "run"],
        ["general-manager", "bot", "off"],
        ["maintenance", "set", "--step", "learn", "--off"],
        ["team", "budget", "--hour", "5"],
        ["trigger", "create", "Orders"],
        ["trigger", "update", "orders", "--name", "Shop"],
        ["trigger", "rotate", "orders"],
        ["trigger", "sign", "orders", "--off"],
        ["trigger", "recover", "orders"],
        ["trigger", "replay", "orders", "d1"],
        ["trigger", "delete", "orders"],
        ["trigger", "relay", "create", "orders"],
        ["trigger", "relay", "rotate", "orders"],
        ["trigger", "relay", "delete", "orders"],
        ["relay", "set", "orders", "--wake", "on"],
        ["tag", "create", "launch", "--label", "Launch"],
    ]
    for words in commands:
        _Recorder.calls = []
        remotecli.run(hubcli.parser().parse_args(words))
        assert _Recorder.calls and all(delegated for *_, delegated in _Recorder.calls), (words, _Recorder.calls)


def test_settings_cli_passes_an_id_addressed_routine_as_the_requester(monkeypatch):
    from clients import remotecli

    calls = []

    class Api:
        def __init__(self, *args, **kwargs):
            self.__dict__["_tico_is_botops"] = True

        def call(self, method, path, body=None, key=None, query=None, delegate=False):
            calls.append((method, path, body, delegate))
            if method == "POST" and path == "settings/catalog/rules.routine/plan":
                return {
                    "run": {"method": "POST", "path": "routines/ops:qa-review", "body": {"enabled": False}},
                    "setting": {"name": "A bot's routine", "where": "Settings > Rules"},
                    "value": {"enabled": False},
                }
            if method == "GET" and path == "settings/catalog/rules.routine":
                return {"setting": {}, "you_may": True}
            return {}

    monkeypatch.setattr(remotecli, "Client", Api)
    monkeypatch.setenv("HUB_API_URL", "https://tico.acme.example/api/v2")
    args = hubcli.parser().parse_args(
        ["settings", "set", "rules.routine", '{"enabled":false}', "--id", "ops:qa-review"]
    )
    result = remotecli.run(args)
    assert result["value"] == {"enabled": False}
    assert calls[0][0:2] == ("POST", "settings/catalog/rules.routine/plan")
    assert calls[0][2] == {"value": '{"enabled":false}', "id": "ops:qa-review"}
    assert calls[0][3] is True
    assert calls[1] == ("POST", "routines/ops:qa-review", {"enabled": False}, True)


def test_a_plain_webhook_header_sends_no_secret_key():
    """BotOps' requests refuse any key named like a secret (backend/botops_act.py), so a plain header leaves it out."""
    args = hubcli.parser().parse_args(
        ["webhook", "update", "ep_1", "--header", "X-Team: acme", "--secret-header", "X-Key: k"]
    )
    assert cli_spec.of(args).prepare(args)[1]["headers"] == [
        {"name": "X-Team", "value": "acme"},
        {"name": "X-Key", "secret": True, "value": "k"},
    ]


def test_file_approved_by_path_asks_with_the_path_and_digest_as_query_fields(tmp_path, monkeypatch):
    """`tico file approved <path>` sends the path and digest as query fields, not as the client's own path argument."""
    import hashlib
    from urllib.parse import parse_qs, urlsplit
    from clients import hubtools, remotecli, tico

    sent = []
    monkeypatch.setattr(
        tico.Client, "request", lambda self, method, path, *a, **kw: sent.append((method, path)) or {"approved": True}
    )
    monkeypatch.setenv("HUB_API_URL", "https://tico.acme.example")
    monkeypatch.setenv("HUB_TOKEN", "bot-token")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "brief.md").write_bytes(b"# Brief\n")
    assert remotecli.main(hubcli.parser().parse_args(["file", "approved", "brief.md", "--by", "human"])) == 0
    ((method, url),) = sent
    parts = urlsplit(url)
    assert (method, parts.path) == ("GET", "/api/v2/file-approvals")
    assert parse_qs(parts.query) == {
        "path": ["brief.md"],
        "digest": [hashlib.sha256(b"# Brief\n").hexdigest()],
        "by": ["human"],
    }
    assert hubtools.files_approved(tico.Client("https://tico.acme.example", "bot-token"), {"target": "brief.md"}) == {
        "approved": True
    }


def test_growth_cli_flags_become_the_api_bodies_the_playbooks_rely_on(monkeypatch):
    """The playbooks call these exact flags; each must land in the API field it names."""
    from clients import remotecli

    sent = []

    class Client:
        def __init__(self, *args, **kwargs):
            self.__dict__["_tico_is_botops"] = False

        def get(self, path, **query):
            if path.startswith("growth/"):
                sent.append(("GET", path, query))
                return {}
            return {"actor": "bot:growth", "kind": "bot"}

        def call(self, method, path, body=None, key=None, query=None, delegate=False):
            sent.append((method, path, body))
            return {"kpi": {}}

        def post(self, path, body=None, key=None):
            return self.call("POST", path, body)

        def patch(self, path, body=None, key=None):
            return self.call("PATCH", path, body)

    monkeypatch.setattr(remotecli, "Client", Client)
    monkeypatch.setenv("HUB_API_URL", "https://hub.example.test")

    def run(*argv):
        remotecli.run(hubcli.parser().parse_args(list(argv)))
        return sent[-1]

    assert run(
        "growth",
        "channel",
        "add",
        "google-search",
        "--name",
        "Google search",
        "--track",
        "performance",
        "--stage",
        "testing",
        "--owner",
        "me",
        "--budget",
        "500",
        "--source",
        "src1",
        "--why",
        "Demand exists",
        "--guardrail-kpi",
        "k9",
        "--guardrail-max",
        "80",
        "--sort",
        "2",
    ) == (
        "POST",
        "growth/channels",
        {
            "slug": "google-search",
            "name": "Google search",
            "track": "performance",
            "stage": "testing",
            "owner_ref": "bot:growth",
            "budget_month": 500.0,
            "source_id": "src1",
            "why": "Demand exists",
            "guardrail_kpi_id": "k9",
            "guardrail_max": 80.0,
            "sort": 2,
        },
    )
    assert run("growth", "channel", "update", "door", "--stage", "paused", "--owner", "") == (
        "PATCH",
        "growth/channels/door",
        {"stage": "paused", "owner_ref": None},
    )  # "" clears
    assert run("growth", "channel", "retire", "door") == ("POST", "growth/channels/door/retire", {})
    assert run(
        "growth",
        "source",
        "add",
        "--truth-for",
        "bookings",
        "--system",
        "Stripe",
        "--reach-kind",
        "mcp",
        "--reach-ref",
        "stripe",
        "--may",
        "read only",
        "--owner",
        "me",
    ) == (
        "POST",
        "growth/sources",
        {
            "truth_for": "bookings",
            "system": "Stripe",
            "reach_kind": "mcp",
            "reach_ref": "stripe",
            "may": "read only",
            "owner_ref": "bot:growth",
        },
    )
    assert run("growth", "source", "update", "src1", "--may", "pause ads") == (
        "PATCH",
        "growth/sources/src1",
        {"may": "pause ads"},
    )
    assert run(
        "growth",
        "cohort",
        "set",
        "--channel",
        "unknown",
        "--month",
        "2026-10",
        "--new",
        "18",
        "--source",
        "new_customers=Stripe",
    ) == (
        "PUT",
        "growth/cohorts",
        {"channel": "unknown", "month": "2026-10", "new_customers": 18, "sources": {"new_customers": "Stripe"}},
    )
    assert run(
        "growth",
        "cohort",
        "set",
        "--channel",
        "door",
        "--month",
        "2026-10",
        "--spend",
        "900",
        "--d30",
        "42",
        "--margin",
        "70",
        "--quality",
        "estimate",
    )[2] == {
        "channel": "door",
        "month": "2026-10",
        "spend": 900.0,
        "kept_d30": 42.0,
        "margin_pct": 70.0,
        "quality": "estimate",
    }
    assert run("growth", "cohort", "list", "--channel", "door", "--month", "2026-10", "--limit", "5") == (
        "GET",
        "growth/cohorts",
        {"channel": "door", "month": "2026-10", "limit": 5},
    )
    assert run("growth", "economics", "--month", "2026-10") == ("GET", "growth/economics", {"month": "2026-10"})
    assert run("growth", "numbers", "--months", "6") == ("GET", "growth/numbers", {"months": 6})
    assert run("growth", "kpis") == ("GET", "growth/kpis", {})
    assert run("growth", "settings") == ("GET", "growth/settings", {})
    assert run("growth", "settings", "--set", "margin_pct=40", "--set", "unit=orders") == (
        "PUT",
        "growth/settings",
        {"margin_pct": 40.0, "unit": "orders"},
    )
    kpi = run("kpi", "update", "k1", "--parent", "k0", "--role", "north_star", "--loop", "referral:2")[2]
    assert (kpi["parent_kpi_id"], kpi["growth_role"], kpi["loop_name"], kpi["loop_step"]) == (
        "k0",
        "north_star",
        "referral",
        2,
    )
    assert "stage" not in kpi
    assert run("brand", "draft", "--section", "positioning", "--body", '"Acme cleans homes"')[1:] == (
        "brand/draft",
        {"sections": {"positioning": "Acme cleans homes"}},
    )


def test_finance_has_no_card_fill_command():
    """Card fill is not offered to bots (hubtools.UNLISTED), so the CLI has no command for it either."""
    with pytest.raises(SystemExit):
        hubcli.parse(hubcli.parser(), ["finance", "card-fill"])


def test_the_retired_gtm_lines_run_their_new_commands_with_a_notice(monkeypatch, capsys):
    ran = []
    monkeypatch.setattr(hubcli, "run", lambda argv, who: ran.append(argv) or 0)
    for old, new in (
        (["gtm", "show", "--json"], ["strategy", "show", "--section", "how", "--json"]),
        (["gtm", "summary"], ["strategy", "show", "--section", "how"]),
        (["gtm", "sources"], ["growth", "source", "list"]),
    ):
        assert hubcli.main(old) == 0
        assert ran[-1] == new
    assert '"tico gtm show" is now "tico strategy show --section how"' in capsys.readouterr().err


def test_strategy_show_section_how_prints_the_section_or_says_nothing_is_published(monkeypatch, capsys):
    from clients import remotecli

    published = {"current": {"sections": {"how": "- Motion: self serve", "serve": "SMBs"}}}

    class Client:
        def __init__(self, *args, **kwargs):
            self.__dict__["_tico_is_botops"] = False

        def get(self, path, **query):
            return {"actor": "bot:growth", "kind": "bot"} if path == "me" else published

    monkeypatch.setattr(remotecli, "Client", Client)
    monkeypatch.setenv("HUB_API_URL", "https://hub.example.test")

    remotecli.main(hubcli.parser().parse_args(["strategy", "show", "--section", "how"]))
    assert capsys.readouterr().out == "- Motion: self serve\n"
    remotecli.main(hubcli.parser().parse_args(["strategy", "show", "--section", "how", "--json"]))
    assert json.loads(capsys.readouterr().out) == {
        "section": "how",
        "text": "- Motion: self serve",
    }  # the tool's answer
    published["current"] = None
    remotecli.main(hubcli.parser().parse_args(["strategy", "show", "--section", "how"]))
    assert capsys.readouterr().out == "Nothing published yet.\n"


def test_a_json_flag_no_command_takes_is_ignored(monkeypatch, capsys):
    p = hubcli.parser()
    assert hubcli.parse(p, ["task", "show", "t_1", "--json"]).id == ["t_1"]
    assert hubcli.parse(p, ["tool", "list", "--json"]).json is True  # its own flag
    assert hubcli.parse(p, ["--json", "tool", "list", "--json"]).json is True
    assert hubcli.parse(p, ["meeting", "search", "--", "--json"]).q == "--json"  # a value stays a value
    for wrong in (["task", "get", "t_1", "--json"], ["task", "show", "t_1", "--json", "--bogus"]):
        with pytest.raises(SystemExit) as e:
            hubcli.parse(p, wrong)
        assert e.value.code == 2
    assert "--bogus" in capsys.readouterr().err
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"vendor": "v_1"}'))  # stdin is read once
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
    for words in (
        ["call", "start", "thread-1", "--title", "Acme standup"],
        ["call", "bot", "add", "call-1", "ops", "finance"],
        ["call", "bot", "remove", "call-1", "ops"],
        ["call", "list", "thread-1", "--limit", "5"],
    ):
        remotecli.run(hubcli.parser().parse_args(words))
    assert sent == [
        ("POST", "threads/thread-1/calls", {"title": "Acme standup"}, None),
        ("POST", "calls/call-1/bots", {"bots": ["ops", "finance"]}, None),
        ("DELETE", "calls/call-1/bots/ops", {}, None),
        ("GET", "threads/thread-1/calls", {"limit": 5}),
    ]


def test_thread_delete_runs_the_message_delete_route(monkeypatch):
    """`tico thread delete` runs `tico_thread_delete`: DELETE on the message, with no body."""
    from clients import remotecli
    from unittest.mock import Mock

    client = Mock()
    client.get.return_value = {"actor": "human:ana"}
    monkeypatch.setenv("HUB_API_URL", "https://api.example.invalid")
    monkeypatch.setattr(remotecli, "Client", lambda *a, **kw: client)
    remotecli.run(hubcli.parser().parse_args(["thread", "delete", "m7"]))
    client.call.assert_called_once_with("DELETE", "messages/m7", None, key=None)


def test_thread_upload_streams_the_file_to_the_thread_message_route(monkeypatch, tmp_path):
    """`tico thread upload` runs `tico_thread_upload`: the file is streamed (never read whole) as multipart to the thread's
    upload route, with the text and reply target as form fields."""
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
    remotecli.run(
        hubcli.parser().parse_args(
            ["thread", "upload", "T1", str(path), "--text", "Q3 numbers", "--to", "human:ben", "--under", "m7"]
        )
    )
    client.post_multipart.assert_called_once_with(
        "uploads/threads/T1/messages",
        {"file": path},
        {"text": "Q3 numbers", "to": "human:ben", "parent_message_id": "m7"},
        key=None,
    )


def test_thread_slack_resume_posts_to_the_link_resume_route(monkeypatch):
    """`tico thread slack-resume` runs `tico_thread_slack_resume`: POST on the link's resume route, as the person asking."""
    from clients import remotecli
    from unittest.mock import Mock

    client = Mock()
    client.get.return_value = {"actor": "human:ana", "role": "owner", "kind": "owner"}
    monkeypatch.setenv("HUB_API_URL", "https://api.example.invalid")
    monkeypatch.setattr(remotecli, "Client", lambda *a, **kw: client)
    remotecli.run(hubcli.parser().parse_args(["thread", "slack-resume", "L1"]))
    client.post.assert_called_once_with("slack/links/L1/resume", {}, key=None)


def test_rules_withdraw_posts_an_empty_body_to_the_hold_withdraw_route(monkeypatch):
    """`tico rules withdraw <hold-id>` runs `tico_rules_withdraw`: POST rule-holds/{id}/withdraw with an empty body."""
    from clients import remotecli
    from unittest.mock import Mock

    client = Mock()
    client.get.return_value = {"actor": "bot:ops"}
    monkeypatch.setenv("HUB_API_URL", "https://api.example.invalid")
    monkeypatch.setattr(remotecli, "Client", lambda *a, **kw: client)
    remotecli.run(hubcli.parser().parse_args(["rules", "withdraw", "h7"]))
    client.post.assert_called_once_with("rule-holds/h7/withdraw", {}, key=None)


def test_assistant_withdraw_posts_the_reason_to_the_card_route(monkeypatch):
    """`tico assistant withdraw <id> --reason ...` runs `tico_assistant_withdraw`: POST assistant/actions/{id}/withdraw."""
    from clients import remotecli
    from unittest.mock import Mock

    client = Mock()
    client.get.return_value = {"actor": "bot:botops"}
    monkeypatch.setenv("HUB_API_URL", "https://api.example.invalid")
    monkeypatch.setattr(remotecli, "Client", lambda *a, **kw: client)
    remotecli.run(hubcli.parser().parse_args(["assistant", "withdraw", "a7", "--reason", "Wrong person"]))
    client.post.assert_called_once_with("assistant/actions/a7/withdraw", {"reason": "Wrong person"}, key=None)


def test_assistant_proposals_lists_the_callers_waiting_cards(monkeypatch):
    """`tico assistant proposals` runs `tico_assistant_proposals`: GET assistant/proposals."""
    from clients import remotecli
    from unittest.mock import Mock

    client = Mock()
    client.get.return_value = {"actions": []}
    monkeypatch.setenv("HUB_API_URL", "https://api.example.invalid")
    monkeypatch.setattr(remotecli, "Client", lambda *a, **kw: client)
    remotecli.run(hubcli.parser().parse_args(["assistant", "proposals"]))
    client.get.assert_called_with("assistant/proposals")


def test_tool_index_reaches_the_tool_index_route_with_its_paging(monkeypatch):
    """`tico tool index --limit 5 --cursor c2` runs `tico_tool_index`: GET tool-index with the page."""
    from clients import remotecli
    from unittest.mock import Mock

    client = Mock()
    client.get.side_effect = lambda path, **kw: {"actor": "bot:ops"} if path == "me" else {"items": [], "next": None}
    monkeypatch.setenv("HUB_API_URL", "https://api.example.invalid")
    monkeypatch.setattr(remotecli, "Client", lambda *a, **kw: client)
    remotecli.run(hubcli.parser().parse_args(["tool", "index", "--limit", "5", "--cursor", "c2"]))
    from unittest.mock import call

    assert call("tool-index", limit=5, cursor="c2") in client.get.call_args_list


def test_the_background_card_names_its_person_on_the_wire(monkeypatch):
    """`tico assistant propose --for human:<id>` reaches the server as the body's `for`: remotecli.run used to drop it."""
    from clients import hubcli, remotecli

    client = mock.Mock()
    client.post.return_value = {"action": {"id": "a1"}}
    monkeypatch.setenv("HUB_API_URL", "https://api.example.invalid")
    monkeypatch.setattr(remotecli, "Client", lambda *a, **kw: client)
    remotecli.run(
        hubcli.parser().parse_args(
            [
                "assistant",
                "propose",
                "--summary",
                "Archive the Ops bot",
                "--path",
                "/api/v2/bots/ops/archive",
                "--body",
                '{"expected_revision": 1}',
                "--for",
                "human:ana",
            ]
        )
    )
    path, body = client.post.call_args.args[0], client.post.call_args.args[1]
    assert path == "assistant/actions"
    assert body["for"] == "human:ana" and body["path"] == "/api/v2/bots/ops/archive" and body["method"] == "POST"


def test_a_propose_without_for_sends_no_recipient(monkeypatch):
    from clients import hubcli, remotecli

    client = mock.Mock()
    client.post.return_value = {"action": {"id": "a1"}}
    monkeypatch.setenv("HUB_API_URL", "https://api.example.invalid")
    monkeypatch.setattr(remotecli, "Client", lambda *a, **kw: client)
    remotecli.run(
        hubcli.parser().parse_args(
            ["assistant", "propose", "--summary", "Archive", "--path", "/api/v2/bots/ops/archive"]
        )
    )
    assert "for" not in client.post.call_args.args[1]


def test_the_morning_message_posts_its_person_and_body_to_the_assistant(monkeypatch):
    """`tico assistant morning --for human:<id> --body <text>` reaches the server as assistant/morning with both fields."""
    from clients import hubcli, remotecli

    client = mock.Mock()
    client.post.return_value = {"message_id": "m1"}
    monkeypatch.setenv("HUB_API_URL", "https://api.example.invalid")
    monkeypatch.setattr(remotecli, "Client", lambda *a, **kw: client)
    args = hubcli.parser().parse_args(["assistant", "morning", "--for", "human:ana", "--body", "Hi"])
    assert args.fn == "assistant morning"
    remotecli.run(args)
    assert client.post.call_args.args[0] == "assistant/morning"
    assert client.post.call_args.args[1] == {"for": "human:ana", "body": "Hi"}


@pytest.mark.parametrize(
    "alias, canonical",
    [
        (["tasks", "list"], ["task", "list"]),
        (["automations", "list"], ["automation", "list"]),
        (["bots", "show", "ops"], ["bot", "show", "ops"]),
        (["rule", "list"], ["rules", "list"]),
    ],
)
def test_an_alias_runs_the_same_calls_as_the_command_it_names(alias, canonical, monkeypatch):
    """The parser keeps the spelling typed, so dispatch renames an alias first: `tico tasks list` makes the calls of
    `tico task list` and never reaches the "unsupported" fallback."""
    from clients import remotecli
    from unittest.mock import MagicMock

    monkeypatch.setenv("HUB_API_URL", "https://api.example.invalid")
    made = []
    for words in (alias, canonical):
        client = MagicMock()
        monkeypatch.setattr(remotecli, "Client", lambda *a, **kw: client)
        remotecli.run(hubcli.parser().parse_args(words))
        made.append(client.mock_calls)
    assert made[0] == made[1] and made[0]


def test_maintenance_status_reads_the_one_maintenance_tool(monkeypatch):
    """`tico maintenance status` has no tool of its own: its command name is tico_maintenance_status only in the parser's
    listing (CLI_TOOL_ALIASES), so the dispatch must name the tool that exists, tico_maintenance."""
    from clients import remotecli

    reads = []

    class Api:
        def __init__(self, *args, **kw):
            self.__dict__["_tico_is_botops"] = False

        def get(self, path, **query):
            reads.append((path, query))
            return {}

    monkeypatch.setattr(remotecli, "Client", Api)
    monkeypatch.setenv("HUB_API_URL", "https://tico.acme.example/api/v2")
    remotecli.run(hubcli.parser().parse_args(["maintenance", "status"]))
    assert reads == [("maintenance", {})]


def test_personal_alert_cli_keeps_paid_test_identity_and_explicit_consent(monkeypatch):
    from clients import remotecli, hubtools

    class API:
        def __init__(self, *args, **kwargs):
            self.calls = []

        def post(self, path, body, key=None):
            self.calls.append((path, body, key))
            return {"status": "accepted"}

    api = API()
    monkeypatch.setenv("HUB_API_URL", "https://tico.acme.example")
    monkeypatch.delenv("HUB_OPERATION_ID", raising=False)
    monkeypatch.setattr(remotecli, "Client", lambda *args, **kwargs: api)
    args = hubcli.parser().parse_args(["alerts", "test", "--accept-charge", "--operation-id", "paid-once"])
    assert remotecli.run(args) == {"status": "accepted"}
    assert api.calls == [("alerts/me/test", {"accept_charge": True}, "paid-once")]
    args = hubcli.parser().parse_args(["alerts", "verify", "+14155550123", "--sms-consent", "--call-consent"])
    remotecli.run(args)
    assert api.calls[-1][1] == {"phone": "+14155550123", "sms_consent": True, "call_consent": True}
    assert "tico_alerts_test" not in {t["name"] for t in hubtools.listing(kind="botops")}


@pytest.mark.parametrize("argv, word", [(["status"], "status"), (["update"], "update")])
@pytest.mark.parametrize("bot", ["", "ops"])
def test_a_word_that_was_the_computers_says_where_it_went_to_a_person_only(argv, word, bot, capsys, monkeypatch):
    monkeypatch.setenv("HUB_BOT", bot)
    monkeypatch.delenv("HUB_EMPLOYEE", raising=False)
    monkeypatch.setattr(hubcli, "run", lambda *a: 0)
    assert hubcli.main(argv) == 0
    note = f"this computer's own {word} is `tico-computer {word}`"
    assert (note in capsys.readouterr().err) == (not bot)


# The Calendar commands and tools. The wire is the one place faked (clients/tico.Client.request): the client builds the
# path and query as it does for real, and each request is recorded with its method, body and operation key.
class _Wire(Client):
    def __init__(self, answer=None, refusal=None):
        super().__init__("https://tico.acme.example", "token")
        self.answer, self.refusal, self.sent = answer, refusal, []

    def request(self, method, path, body=None, key=None, **kwargs):
        self.sent.append((method, path, body, key))
        if self.refusal is not None:
            raise self.refusal
        return self.answer


def _calendar_cli(argv, monkeypatch, answer=None, refusal=None, operation=None):
    """`tico <argv>` through remotecli.main: the requests it sent, and the exit code."""
    from clients import remotecli

    wire = _Wire(answer, refusal)
    monkeypatch.setattr(remotecli, "Client", lambda *a, **k: wire)
    monkeypatch.setenv("HUB_API_URL", "https://tico.acme.example/api/v2")
    if operation:
        monkeypatch.setenv("HUB_OPERATION_ID", operation)
    else:
        monkeypatch.delenv("HUB_OPERATION_ID", raising=False)
    return remotecli.main(hubcli.parser().parse_args(argv)), wire.sent


def _split(path):
    """A request's path and its query, each name with its values as lists."""
    from urllib.parse import parse_qs, urlsplit

    split = urlsplit(path)
    return split.path, parse_qs(split.query, keep_blank_values=True)


CALENDAR_ANA = {"ref": "human:ana", "type": "human", "id": "ana", "name": "Ana", "time_zone": "America/Los_Angeles"}
CALENDAR_EVENTS = {
    "calendars": [CALENDAR_ANA],
    "occurrences": [
        {
            "occurrence_id": "tico:evt_1:2026-11-11T17:30:00Z",
            "title": "Standup",
            "start": "2026-11-11T17:30:00Z",
            "end": "2026-11-11T18:00:00Z",
            "all_day": False,
            "calendar": {"ref": "human:ana", "type": "human", "id": "ana", "name": "Ana"},
        },
        {
            "occurrence_id": "due:task:t_9",
            "title": "Ship the thing",
            "start": "2026-11-11",
            "end": "2026-11-12",
            "all_day": True,
            "calendar": {"ref": "human:ana", "type": "human", "id": "ana", "name": "Ana"},
        },
    ],
    "errors": [{"ref": "human:ben", "reason": "gcal_refused"}],
}


def test_calendar_list_without_a_calendar_flag_still_reads_the_company_calendar(monkeypatch, capsys):
    code, sent = _calendar_cli(["calendar", "list"], monkeypatch, answer={"appointments": []})
    assert code == 0 and sent == [("GET", "/api/v2/calendar/appointments", None, None)]
    assert json.loads(capsys.readouterr().out) == {"appointments": []}
    code, sent = _calendar_cli(["calendar", "list", "--calendar", "legal@acme.example"], monkeypatch, answer={})
    assert sent == [("GET", "/api/v2/calendar/appointments?calendar=legal%40acme.example", None, None)]


def test_calendar_list_with_a_flag_reads_the_events_route_and_prints_one_line_per_occurrence(monkeypatch, capsys):
    code, sent = _calendar_cli(
        [
            "calendar",
            "list",
            "--who",
            "human:ana,bot:ops",
            "--from",
            "2026-11-11",
            "--to",
            "2026-11-14",
            "--kinds",
            "event,run",
        ],
        monkeypatch,
        answer=CALENDAR_EVENTS,
    )
    assert code == 0
    [(method, path, _body, _key)] = sent
    assert method == "GET" and _split(path) == (
        "/api/v2/calendar/events",
        {"who": ["human:ana,bot:ops"], "from": ["2026-11-11"], "to": ["2026-11-14"], "kinds": ["event,run"]},
    )
    captured = capsys.readouterr()
    # The first calendar's zone: 17:30Z is 09:30 in Los Angeles in November. An all-day line names no time.
    assert captured.out == (
        "Wed 11 Nov 09:30–10:00  Standup  (Ana)  [tico:evt_1:2026-11-11T17:30:00Z]\n"
        "Wed 11 Nov all day  Ship the thing  (Ana)  [due:task:t_9]\n"
    )
    assert captured.err == "tico: could not read human:ben: gcal_refused\n"


def test_calendar_list_json_prints_the_response_as_it_came(monkeypatch, capsys):
    code, _sent = _calendar_cli(["calendar", "list", "--json"], monkeypatch, answer=CALENDAR_EVENTS)
    assert code == 0 and json.loads(capsys.readouterr().out) == CALENDAR_EVENTS


def test_calendar_free_sends_its_people_length_and_window_and_prints_the_slots(monkeypatch, capsys):
    answer = {"slots": [{"start": "2026-11-11T17:30:00Z", "end": "2026-11-11T18:15:00Z"}]}
    code, sent = _calendar_cli(
        ["calendar", "free", "--who", "human:ana,human:ben", "--minutes", "45", "--within", "5d"],
        monkeypatch,
        answer=answer,
    )
    assert code == 0
    assert sent == [("GET", "/api/v2/calendar/free?who=human%3Aana%2Chuman%3Aben&minutes=45&within=5d", None, None)]
    assert capsys.readouterr().out == "Wed 11 Nov 17:30–18:15\n"  # no calendar gives a zone: the server's Z stays


def test_calendar_add_posts_the_event_with_its_repeat_words_calendar_and_operation_key(monkeypatch, capsys):
    answer = {
        "occurrence_id": "tico:evt_2",
        "title": "Standup",
        "start": "2026-11-11T09:30:00-08:00",
        "end": "2026-11-11T10:00:00-08:00",
        "time_zone": "America/Los_Angeles",
        "all_day": False,
        "calendar": {"name": "Legal"},
    }
    code, sent = _calendar_cli(
        [
            "calendar",
            "add",
            "--title",
            "Standup",
            "--start",
            "2026-11-11T09:30:00-08:00",
            "--end",
            "2026-11-11T10:00:00-08:00",
            "--who",
            "human:ben, bot:ops",
            "--repeat",
            "weekly on wed",
            "--calendar",
            "legal@acme.example",
            "--notes",
            "Bring numbers",
            "--no-meet",
        ],  # fmt: skip
        monkeypatch,
        answer=answer,
        operation="op-7",
    )
    assert code == 0
    assert sent == [
        (
            "POST",
            "/api/v2/calendar/events",
            {
                "calendar": "legal@acme.example",
                "title": "Standup",
                "start": "2026-11-11T09:30:00-08:00",
                "end": "2026-11-11T10:00:00-08:00",
                "attendees": ["human:ben", "bot:ops"],
                "repeat": "weekly on wed",
                "notes": "Bring numbers",
                "add_meet": False,
            },
            "op-7",
        )
    ]
    assert capsys.readouterr().out == "Wed 11 Nov 09:30–10:00  Standup  (Legal)  [tico:evt_2]\n"


def test_calendar_add_all_day_sends_dates_and_meet_on_by_default(monkeypatch):
    code, sent = _calendar_cli(
        ["calendar", "add", "--title", "Offsite", "--start", "2026-11-16", "--end", "2026-11-18", "--all-day"],
        monkeypatch,
        answer={},
    )
    assert code == 0
    assert sent == [
        ("POST", "/api/v2/calendar/events", {"title": "Offsite", "start": "2026-11-16", "end": "2026-11-18", "all_day": True, "add_meet": True}, None)
    ]  # fmt: skip


def test_calendar_move_patches_the_encoded_occurrence_with_its_scope(monkeypatch, capsys):
    code, sent = _calendar_cli(
        [
            "calendar",
            "move",
            "tico:evt_1:2026-11-11T17:30:00Z",
            "--start",
            "2026-11-11T10:00:00-08:00",
            "--end",
            "2026-11-11T10:30:00-08:00",
            "--scope",
            "this",
        ],  # fmt: skip
        monkeypatch,
        answer={},
        operation="op-8",
    )
    assert code == 0
    assert sent == [
        (
            "PATCH",
            "/api/v2/calendar/events/tico%3Aevt_1%3A2026-11-11T17%3A30%3A00Z",
            {"start": "2026-11-11T10:00:00-08:00", "end": "2026-11-11T10:30:00-08:00", "scope": "this"},
            "op-8",
        )
    ]


def test_calendar_cancel_deletes_the_encoded_occurrence_with_its_scope_and_says_so(monkeypatch, capsys):
    code, sent = _calendar_cli(
        ["calendar", "cancel", "google:chris@acme.example:evt_3", "--scope", "following"], monkeypatch, answer={}
    )
    assert code == 0
    assert [(method, path) for method, path, _body, _key in sent] == [
        ("DELETE", "/api/v2/calendar/events/google%3Achris%40acme.example%3Aevt_3?scope=following")
    ]
    assert capsys.readouterr().out == "cancelled google:chris@acme.example:evt_3 (following)\n"


def test_calendar_search_sends_the_words_and_says_when_results_are_partial(monkeypatch, capsys):
    answer = {"occurrences": [], "partial": True, "errors": []}
    code, sent = _calendar_cli(
        ["calendar", "search", "standup with:ben", "--who", "human:ana"], monkeypatch, answer=answer
    )
    assert code == 0
    assert sent == [("GET", "/api/v2/calendar/search?q=standup+with%3Aben&who=human%3Aana", None, None)]
    captured = capsys.readouterr()
    assert captured.out == "No matches.\n"
    assert captured.err == "tico: some results are missing: the search ran out of time\n"


def test_a_refused_booking_prints_the_servers_reason_as_a_line_and_exits_two(monkeypatch, capsys):
    refusal = APIError("book_rule", "Who bots may book: ops may book only people near it (rule: Nearby)", 403)
    code, _sent = _calendar_cli(
        ["calendar", "add", "--title", "x", "--start", "2026-11-11T09:00:00Z", "--end", "2026-11-11T09:30:00Z", "--who", "human:ben"],
        monkeypatch,
        refusal=refusal,
    )  # fmt: skip
    captured = capsys.readouterr()
    assert code == 2 and captured.out == ""
    assert captured.err == "tico: Who bots may book: ops may book only people near it (rule: Nearby)\n"


def test_the_calendar_tools_send_their_arguments_as_the_requests(monkeypatch):
    from clients import hubtools

    wire = _Wire({"occurrences": []})
    tools = hubtools.BY_NAME
    tools["tico_calendar_events"]["fn"](
        wire, {"who": "human:ana", "from": "2026-11-11", "to": "2026-11-14", "kinds": "event"}
    )
    tools["tico_calendar_free"]["fn"](wire, {"who": "human:ana,human:ben"})
    tools["tico_calendar_search"]["fn"](wire, {"q": "standup", "who": "human:ana"})
    tools["tico_calendar_add"]["fn"](
        wire,
        {
            "title": "Hold",
            "start": "2026-11-11",
            "end": "2026-11-12",
            "all_day": True,
            "attendees": ["bot:ops"],
            "operation_id": "op-1",
        },
    )
    tools["tico_calendar_move"]["fn"](
        wire,
        {"occurrence": "tico:evt_1:2026-11-11T17:30:00Z", "start": "2026-11-11T10:00:00-08:00", "operation_id": "op-2"},
    )
    tools["tico_calendar_cancel"]["fn"](wire, {"occurrence": "tico:evt_1", "scope": "all", "operation_id": "op-3"})
    assert wire.sent == [
        ("GET", "/api/v2/calendar/events?who=human%3Aana&from=2026-11-11&to=2026-11-14&kinds=event", None, None),
        ("GET", "/api/v2/calendar/free?who=human%3Aana%2Chuman%3Aben&minutes=30", None, None),
        ("GET", "/api/v2/calendar/search?q=standup&who=human%3Aana", None, None),
        (
            "POST",
            "/api/v2/calendar/events",
            {"title": "Hold", "start": "2026-11-11", "end": "2026-11-12", "all_day": True, "attendees": ["bot:ops"], "add_meet": True},
            "op-1",
        ),
        (
            "PATCH",
            "/api/v2/calendar/events/tico%3Aevt_1%3A2026-11-11T17%3A30%3A00Z",
            {"start": "2026-11-11T10:00:00-08:00"},
            "op-2",
        ),
        ("DELETE", "/api/v2/calendar/events/tico%3Aevt_1?scope=all", {}, "op-3"),
    ]  # fmt: skip


def test_bots_and_the_assistant_may_book_and_every_caller_may_read_the_calendar():
    from clients import hubtools

    for name in ("tico_calendar_add", "tico_calendar_move", "tico_calendar_cancel"):
        assert {"bot", "assistant"} <= set(hubtools.offered_to(hubtools.BY_NAME[name])), name
    for name in ("tico_calendar_events", "tico_calendar_free", "tico_calendar_search"):
        assert {"owner", "member", "bot", "assistant"} <= set(hubtools.offered_to(hubtools.BY_NAME[name])), name


def test_app_view_cli_sends_on_off_as_booleans_and_app_shot_keeps_the_token_off_the_command_line(monkeypatch, tmp_path):
    import subprocess

    from clients import remotecli
    from clients.hubtools import BY_NAME

    sent = []
    monkeypatch.setitem(BY_NAME['tico_bot_app_view'], 'fn', lambda api, args: sent.append(args))
    for argv in (['bot', 'app-view', 'cpo'], ['bot', 'app-view', 'cpo', 'on'], ['bot', 'app-view', 'cpo', 'off']):
        args = hubcli.parser().parse_args(argv)
        cli_spec.of(args).call(None, args) if cli_spec.of(args) else remotecli.via_tool(None, args)
    assert ['on' in row for row in sent] == [False, True, True] and [r.get('on') for r in sent[1:]] == [True, False]

    class Api:
        url = 'https://team.example'

        def post(self, path, body=None, key=None):
            assert path == 'app-view'
            return {'id': 'v1', 'token': 'tico_av_secret', 'expires_at': 'later', 'read_only': True}

    ran = []

    def run(command, env, **kw):
        ran.append((command, env))
        return subprocess.CompletedProcess(command, 0, stdout='{"file": "x.png", "width": 390, "height": 844}\n')

    monkeypatch.setattr(subprocess, 'run', run)
    out = BY_NAME['tico_bot_app_shot']['fn'](Api(), {'path': '/#/tasks', 'out': str(tmp_path / 'x.png'), 'width': 390})
    command, env = ran[0]
    assert command[2] == 'https://team.example/#/tasks' and command[4] == '390'
    assert 'tico_av_secret' not in ' '.join(command) and env['TICO_VIEW_TOKEN'] == 'tico_av_secret'
    assert out['view'] == 'v1' and out['read_only'] is True
