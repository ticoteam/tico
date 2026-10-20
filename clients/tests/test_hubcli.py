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
SUBCOMMANDS = ["whoami", "inbound", "meeting", "message", "conversation", "question", "note", "file", "doc", "chat", "assistant", "tag", "task", "replay", "goal", "kpi",
               "proposal", "market", "listening", "tool", "routine", "trigger", "approval", "brief", "mcp", "needs-you", "run", "team", "org", "health",
               "changelog", "update", "grokbot", "external", "calendar", "sql", "db", "classify", "decision", "template", "bot", "repo", "skill", "agent", "human", "group", "api", "computer",
               "credential", "slack", "support", "health-monitor", "roadmap", "service-key"]


def run_hub(*args, env=None):
    base = {k: v for k, v in os.environ.items() if not k.startswith("HUB_")}
    done = subprocess.run([sys.executable, str(HUB), *args], env={**base, **(env or {})},
                          capture_output=True, text=True, timeout=60)
    try:
        return done.returncode, json.loads(done.stdout)
    except ValueError:
        return done.returncode, {"_stdout": done.stdout, "_stderr": done.stderr}


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
        if any(part in self.path for part in ("/requesters", "/publication-preview", "/moderate", "/roadmaps/settings")):
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
            return {"routine": {"id": path.split("/")[1] + ":" + body["key"]}}

        def get(self, path, **query):
            self.seen.append(path)
            if path == "config":
                return dict(NAMES)
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
