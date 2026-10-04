"""The `hub` CLI as a bot runs it: `scripts/hub` in a subprocess, remote-only.

The command surface (docs/history/hub-v2.md §5) and the exit codes are the contract every bot's AGENT.md
depends on: 0 fine, 2 refused or a non-retryable API error, 1 anything else. The real HTTP round
trip is `backend/tests/test_runner.py`; here the API is a stub or absent.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread

from clients import hubcli

HUB = Path(__file__).resolve().parents[2] / "scripts" / "hub"
SUBCOMMANDS = ["whoami", "meeting", "message", "conversation", "question", "note", "file", "doc", "chat", "assistant", "tag", "task", "goal", "kpi",
               "proposal", "market", "listening", "tool", "routine", "approval", "brief", "mcp", "needs-you", "run", "team", "health",
               "update", "grokbot", "calendar", "sql", "db", "classify", "decision", "template", "bot", "repo", "skill", "agent", "human", "group", "api", "computer",
               "credential", "slack", "support", "service-key"]


def run_hub(*args, env=None):
    base = {k: v for k, v in os.environ.items() if not k.startswith("HUB_")}
    done = subprocess.run([sys.executable, str(HUB), *args], env={**base, **(env or {})},
                          capture_output=True, text=True, timeout=60)
    try:
        return done.returncode, json.loads(done.stdout)
    except ValueError:
        return done.returncode, {"_stdout": done.stdout, "_stderr": done.stderr}


def test_meeting_review_commands_use_the_same_personal_routes_as_mcp(monkeypatch):
    from clients import remotecli
    sent = []
    class Api:
        def __init__(self, *args, **kwargs):
            pass
        def get(self, path, **query):
            if path == 'me':
                return {'kind': 'member'}
            sent.append(('GET', path, query))
            return {}
        def post(self, path, body, key=None):
            sent.append(('POST', path, body))
            return {}
    monkeypatch.setattr(remotecli, 'Client', Api)
    monkeypatch.setenv('HUB_API_URL', 'http://example.test')
    monkeypatch.setenv('HUB_TOKEN', 'test-token')
    for argv in (['pending'], ['approve', 'm1'], ['approve', '--all'], ['dismiss', 'm1'], ['restore', 'm1']):
        remotecli.run(hubcli.parser().parse_args(['meeting', *argv]))
    assert sent == [('GET', 'meetings', {'review': 'pending'}),
                    ('POST', 'meetings/m1/review', {'action': 'approve'}),
                    ('POST', 'meetings/review', {'action': 'approve_all'}),
                    ('POST', 'meetings/m1/review', {'action': 'dismiss'}),
                    ('POST', 'meetings/m1/review', {'action': 'restore'})]


class Parser(unittest.TestCase):
    def test_kpi_archive_and_historical_list_commands_parse(self):
        historical = hubcli.parser().parse_args(["kpi", "list", "--archived", "--owner", "ana"])
        archived = hubcli.parser().parse_args(["kpi", "archive", "K1"])
        restored = hubcli.parser().parse_args(["kpi", "restore", "K1"])
        self.assertEqual((historical.fn, historical.include_archived, historical.owner), ("kpi list", True, "ana"))
        self.assertEqual((archived.fn, archived.id), ("kpi archive", "K1"))
        self.assertEqual((restored.fn, restored.id), ("kpi restore", "K1"))

    def test_every_subcommand_is_still_there(self):
        text = hubcli.parser().format_help()
        self.assertIn("{" + ",".join(SUBCOMMANDS) + "}", text)

    def test_the_last_releases_spellings_are_hidden_aliases_that_run_the_new_command(self):
        """Every old spelling maps onto a command the real parser accepts, says which one on the way, and is not in help."""
        help_text = hubcli.parser().format_help()
        samples = {("say",): ["say", "ana", "hi"], ("notice",): ["notice", "ana", "hi"], ("inbox",): ["inbox"],
                   ("ack",): ["ack", "m1"], ("history",): ["history", "c1"], ("ask",): ["ask", "ana", "why"],
                   ("answer",): ["answer", "m1", "yes"], ("notes",): ["notes"], ("unnote",): ["unnote", "n1"],
                   ("board",): ["board"], ("task", "stuck"): ["task", "stuck", "--hours", "5"], ("goals",): ["goals", "--all"],
                   ("goal", "auto"): ["goal", "auto", "G1"], ("goal", "checkins"): ["goal", "checkins", "G1"],
                   ("kpi", "add"): ["kpi", "add", "Activation"], ("kpi", "readings"): ["kpi", "readings", "K1"],
                   ("context", "search"): ["context", "search", "x", "--source", "market"], ("context", "show"): ["context", "show", "d1"],
                   ("docs", "links"): ["docs", "links"], ("docs",): ["docs", "list"], ("files", "add-link"): ["files", "add-link", "https://x.example"],
                   ("files",): ["files", "list"], ("meetings", "transcript"): ["meetings", "transcript", "m1"], ("meetings",): ["meetings", "search"],
                   ("listen", "judge"): ["listen", "judge"], ("listen",): ["listen", "stats"], ("intake",): ["intake", "list"],
                   ("tools",): ["tools", "list"], ("integrations",): ["integrations"], ("integration",): ["integration", "wh"],
                   ("queries",): ["queries", "wh", "jobs"], ("learn",): ["learn", "wh", "text"],
                   ("routine", "on"): ["routine", "on", "audit"], ("routine", "off"): ["routine", "off", "audit", "--bot", "cpo"],
                   ("status",): ["status", "list"], ("recent",): ["recent"], ("turns",): ["turns", "ops"], ("org",): ["org"],
                   ("fleet",): ["fleet"], ("fleet-check",): ["fleet-check"], ("computers",): ["computers"], ("catalog",): ["catalog"],
                   ("bot", "register"): ["bot", "register", "seo"], ("bot", "set"): ["bot", "set", "seo", "--status", "active"],
                   ("bot", "onboarded"): ["bot", "onboarded"], ("github", "create-bot-repo"): ["github", "create-bot-repo", "seo"],
                   ("people",): ["people", "list"], ("person",): ["person", "list"], ("update", "post"): ["update", "post", "- x"],
                   ("update", "read"): ["update", "read", "--all"], ("updates",): ["updates", "--unread"],
                   ("batch",): ["batch", "start"], ("live", "brief"): ["live", "brief"], ("live", "stats"): ["live", "stats"],
                   ("calendar", "upcoming"): ["calendar", "upcoming"], ("decisions",): ["decisions", "--list"], ("judge",): ["judge", "--list"]}
        self.assertEqual({old for old, *_ in hubcli.RENAMED}, set(samples))
        for old, argv in samples.items():
            with self.subTest(old=" ".join(old)):
                new_argv, notice = hubcli.rename_argv(argv)
                self.assertIn("is now", notice)
                args = hubcli.parser().parse_args(new_argv)      # what it turns into is a real command
                self.assertTrue(args.fn)
        self.assertEqual(hubcli.rename_argv(["notice", "ana", "hi"])[0], ["message", "send", "ana", "hi", "--fyi"])
        self.assertEqual(hubcli.rename_argv(["goal", "auto", "G1"])[0], ["goal", "status", "G1", "auto"])
        self.assertEqual(hubcli.rename_argv(["routine", "off", "audit", "--bot", "cpo"])[0],
                         ["routine", "update", "audit", "--bot", "cpo", "--disable"])
        self.assertEqual(hubcli.rename_argv(["note", "ana", "hi"])[0], ["note", "create", "ana", "hi"])
        self.assertEqual(hubcli.rename_argv(["note", "list"]), (["note", "list"], None))
        self.assertEqual(hubcli.rename_argv(["message", "send", "ana", "hi"]), (["message", "send", "ana", "hi"], None))
        for old in ("hub say", "hub notice", "hub board", "hub fleet-check", "hub people", "hub integrations", "hub org"):
            self.assertNotIn(old + " ", help_text)

    def test_an_old_command_still_runs_and_says_on_stderr_what_it_is_now(self):
        done = subprocess.run([sys.executable, str(HUB), "fleet-check"], capture_output=True, text=True, timeout=60,
                              env={**{k: v for k, v in os.environ.items() if not k.startswith("HUB_")},
                                   "HUB_API_URL": "http://127.0.0.1:9", "HUB_TOKEN": "x"})
        self.assertIn('"hub fleet-check" is now "hub health check"', done.stderr)
        self.assertEqual(done.stderr.count("\n"), 1)
        self.assertNotIn("is now", done.stdout)

class ToolUpdateAndRequester(unittest.TestCase):
    def test_tool_update_and_the_requester_filter_parse(self):
        args = hubcli.parser().parse_args(["tool", "update", "gmail", "--bot", "inbox", "--can", "read,draft,send",
                                           "--scope", "mailbox=a@acme.example", "--scope", "sites=", "--note", ""])
        self.assertEqual((args.fn, args.id, args.bot, args.can, args.scope, args.note),
                         ("tool update", "gmail", "inbox", "read,draft,send", ["mailbox=a@acme.example", "sites="], ""))
        with self.assertRaises(SystemExit):
            hubcli.parser().parse_args(["tool", "update", "gmail"])               # --bot is required
        mcp = hubcli.parser().parse_args(["tool", "add", "ops", "linear", "--can", "read", "--env", "LINEAR_API_KEY",
                                          "--mcp-url", "https://mcp.linear.app/mcp", "--transport", "sse",
                                          "--header", "Authorization: Bearer ${LINEAR_API_KEY}"])
        self.assertEqual((mcp.fn, mcp.mcp_url, mcp.transport, mcp.headers),
                         ("tool add", "https://mcp.linear.app/mcp", "sse", ["Authorization: Bearer ${LINEAR_API_KEY}"]))
        more = hubcli.parser().parse_args(["tool", "update", "linear", "--bot", "ops", "--mcp-url", "https://mcp.linear.app/mcp"])
        self.assertEqual((more.mcp_url, more.transport, more.headers), ("https://mcp.linear.app/mcp", None, None))
        listed = hubcli.parser().parse_args(["task", "list", "--requester", "me", "--status", "open"])
        self.assertEqual((listed.fn, listed.requester, listed.status), ("task list", "me", ["open"]))


class Remote(unittest.TestCase):
    def test_human_override_is_refused_remotely_with_exit_two(self):
        code, out = run_hub("--human", "ana", "whoami",
                            env={"HUB_API_URL": "http://127.0.0.1:9", "HUB_TOKEN": "x"})
        self.assertEqual(code, 2)
        self.assertEqual(out["error"], "identity")


PAGE = {
    "service": "warehouse", "title": "Warehouse", "kind": "sql", "summary": "The company warehouse.",
    "writes": "never", "owner": "ana", "aliases": ["wh"], "access": "hub sql in a turn",
    "credentials": ["none"], "declared_as": "nothing to declare\n",
    "body": "## What it is\n\nThe hub database.\n\n## Rules\n\n- One SELECT.\n",
    "queries": [
        {"id": "queued-work", "title": "Queued work", "description": "Jobs waiting per bot.", "category": "work",
         "tags": ["jobs", "queue"], "database": "hub.sqlite",
         "sql": "SELECT bot, count(*) AS queued FROM jobs WHERE state='queued' GROUP BY bot\n", "params": []},
        {"id": "task-history", "title": "A task history", "description": "Every change to one task.", "category": "tasks",
         "tags": ["tasks"], "database": "hub.sqlite",
         "sql": "SELECT ts, actor FROM task_events WHERE task_id=:task ORDER BY ts\n",
         "params": [{"name": "task", "type": "text", "label": "Task id", "required": True}]}],
    "learnings": [{"id": "L1", "integration": "warehouse", "actor": "bot:seo", "text": "Compare timestamps as text.",
                   "created": "2026-09-15T10:00:00Z"}],
}


class Stub(BaseHTTPRequestHandler):
    """Enough of the Tico API to see what the CLI sends: /me, one task, one refusal, one integration."""
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
        if self.path.startswith("/api/v2/tasks/T1"):
            return self.reply(200, {"task": {"id": "T1", "version": 3}})
        if self.path.startswith("/api/v2/goals?"):
            return self.reply(200, {"owner": "bot:coo", "goals": [{"id": "G1", "title": "Keep every bot able to do its job"}],
                                    "chain": [{"id": "G0", "title": "Grow revenue"}], "reports": [], "company": []})
        if self.path == "/api/v2/goals":
            return self.reply(200, {"owner": "bot:coo", "goals": [{"id": "G1"}], "chain": [], "reports": [], "company": []})
        if self.path == "/api/v2/goals/G1":
            return self.reply(200, {"goal": {"id": "G1", "status": "green", "kpis": []}})
        if self.path == "/api/v2/conversations/C1/messages":
            return self.reply(200, {"conversation": {"id": "C1"}, "messages": [{"id": "M2"}, {"id": "M3"}],
                                    "has_more": True, "next_before": "M2"})
        if self.path.startswith("/api/v2/kpis/K1/readings"):
            return self.reply(200, {"kpi": {"id": "K1"}, "readings": [{"value": 17.0}], "query": self.path.partition("?")[2]})
        if self.path.startswith("/api/v2/kpis?"):
            return self.reply(200, {"kpis": [], "query": self.path.partition("?")[2]})
        if self.path == "/api/v2/goals/G1/checkins":
            return self.reply(200, {"goal_id": "G1", "checkins": []})
        if self.path == "/api/v2/kpis/K1":
            return self.reply(200, {"kpi": {"id": "K1"}, "goals": []})
        if self.path == "/api/v2/tools":
            return self.reply(200, {"integrations": [
                {"service": "warehouse", "title": "Warehouse", "kind": "sql", "summary": "The company warehouse.",
                 "access": "hub sql in a turn", "credentials": ["none"], "declared_as": "nothing to declare\n",
                 "writes": "never", "owner": "ana", "aliases": [], "query_count": 2, "learning_count": 1},
                {"service": "slack", "title": "Slack", "kind": "api", "summary": "Channels and DMs.",
                 "access": "connectors/slack.py", "credentials": ["SLACK_BOT_TOKEN — shared"],
                 "declared_as": "env: SLACK_BOT_TOKEN\n",
                 "writes": "allowed", "owner": "ana", "aliases": [], "query_count": 0, "learning_count": 0}]})
        if self.path in ("/api/v2/tools/warehouse", "/api/v2/tools/wh"):
            return self.reply(200, PAGE)
        if self.path.startswith("/api/v2/tools/"):
            return self.reply(404, {"error": {"code": "not_found", "detail": "No integration named " + self.path.rsplit("/", 1)[1]}})
        self.reply(404, {"error": {"code": "not_found", "detail": "no"}})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        self.seen.append(("POST", self.path, self.headers.get("Authorization"), body))
        if self.path == "/api/v2/tasks":
            return self.reply(200, {"task": {"id": "T2", "title": body["title"], "owner": body["owner"]}})
        if self.path == "/api/v2/tasks/T1":
            return self.reply(200, {"task": {"id": "T1", "status": body.get("status"), "version": 4,
                                             "goal_id": body.get("goal_id")}})
        if self.path == "/api/v2/goals":
            return self.reply(200, {"goal": {"id": "G2", "title": body["title"], "owner": body["owner"],
                                             "parent_id": body.get("parent_id"), "status": None}})
        if self.path == "/api/v2/goals/G1/status":
            return self.reply(200, {"goal": {"id": "G1", "status": body["status"], "status_note": body["note"]}})
        if self.path == "/api/v2/goals/G1":
            return self.reply(200, {"goal": {"id": "G1", **{k: v for k, v in body.items() if v is not None}}})
        if self.path == "/api/v2/goals/G1/status/auto":
            return self.reply(200, {"goal": {"id": "G1", "status_source": "auto"}})
        if self.path == "/api/v2/goals/G1/checkins":
            return self.reply(200, {"checkin": {"id": "C1", **body}})
        if self.path in ("/api/v2/goals/G1/kpis", "/api/v2/goals/G1/kpis/K1/unlink", "/api/v2/kpis", "/api/v2/kpis/K1",
                         "/api/v2/proposals", "/api/v2/proposals/P1/decide", "/api/v2/kpis/K1/readings"):
            # Echo what was sent, under the key the real route answers with.
            key = {"/api/v2/proposals": "proposal", "/api/v2/proposals/P1/decide": "proposal",
                   "/api/v2/kpis/K1/readings": "reading", "/api/v2/goals/G1/kpis/K1/unlink": "goal"}.get(self.path, "kpi")
            return self.reply(200, {key: {"id": "X1", "path": self.path, **body}})
        if self.path == "/api/v2/bots/coo/routines":
            return self.reply(200, {"routine": {"id": "coo:" + body["key"], "title": body["title"], "cron": body["cron"]}})
        if self.path == "/api/v2/routines/coo:audit":
            return self.reply(200, {"routine": {"id": "coo:audit", "enabled": body["enabled"]}})
        if self.path == "/api/v2/tasks/T1/files":
            return self.reply(200, {"file": {"id": "F1", "name": body["name"], "url": "/api/v2/files/F1"},
                                    "link": "https://tico.test/api/v2/files/F1"})
        if self.path == "/api/v2/tools/warehouse/learnings":
            if not body.get("text"):
                return self.reply(422, {"error": {"code": "validation", "detail": "text: too short"}})
            return self.reply(200, {"id": "L2", "integration": "warehouse", "actor": "bot:coo", "text": body["text"],
                                    "created": "2026-09-15T11:00:00Z"})
        if self.path == "/api/v2/decisions":
            options = list(body["questions"]["covered"]["criteria"]) if "covered" in body["questions"] else []
            answers = {qid: ({"type": "choice", "choice": options[0], "confidence": 0.77,
                              "probabilities": {o: 0.1 for o in options}} if q["type"] == "choice"
                             else {"type": "noul", "noul": 0.2}) for qid, q in body["questions"].items()}
            return self.reply(200, {"model": "judge-1.13.0", "answers": answers, "usage": {"input_tokens": 50},
                                    "ms": 190, "label": body.get("label")})
        if self.path == "/api/v2/sql":
            if "credentials" in body["sql"]:
                return self.reply(422, {"error": {"code": "sql", "detail": "access to credentials.id is prohibited"}})
            return self.reply(200, {"columns": ["bot", "state", "focus"], "ms": 3, "truncated": body.get("max_rows") == 2,
                                    "rows": [["coo", "idle", None], ["seo", "running", "Writing the brief\nfor the launch"]][:body.get("max_rows") or 2],
                                    "row_count": min(2, body.get("max_rows") or 2)})
        self.reply(422, {"error": {"code": "refused", "detail": "rule 2: not on the roster"}})


class AgainstAStub(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), Stub)
        cls.thread = Thread(target=cls.server.serve_forever, daemon=True)
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

    def test_conversation_show_says_when_there_is_an_older_page_and_where_it_starts(self):
        # Without these a bot reading back a long conversation takes the newest 200 for all of it.
        code, out = run_hub("conversation", "show", "C1", env=self.env)
        self.assertEqual(code, 0, out)
        self.assertEqual((out["has_more"], out["next_before"]), (True, "M2"))

    def test_goal_and_kpi_commands_send_what_the_routes_take(self):
        def sent(*args):
            Stub.seen.clear()
            code, out = run_hub(*args, env=self.env)
            self.assertEqual(code, 0, out)
            return out, [b for m, p, _, b in Stub.seen if m == "POST"][-1]
        _, body = sent("kpi", "create", "Activation", "--goal", "G1", "--unit", "%", "--cadence", "daily", "--baseline", "40",
                       "--target", "65", "--deadline", "2026-12-31")
        self.assertEqual((body["name"], body["goal_id"], body["kind"], body["target"], body["deadline"], body["cadence"]),
                         ("Activation", "G1", "improve", 65, "2026-12-31", "daily"))
        _, body = sent("kpi", "link", "G1", "K1", "--min", "40", "--max", "60")
        self.assertEqual((body["kpi_id"], body["kind"], body["min"], body["max"]), ("K1", "maintain", 40, 60))
        out, body = sent("kpi", "log", "K1", "52", "pulled from the warehouse", "--period-end", "2026-09-27",
                         "--evidence", "https://bi.example/q/9", "--estimate", "--supersedes", "R0")
        self.assertEqual((body["value"], body["quality"], body["period_end"], body["evidence"], body["supersedes"]),
                         (52, "estimate", "2026-09-27", "https://bi.example/q/9", "R0"))
        out, body = sent("kpi", "unlink", "G1", "K1")
        self.assertEqual(out["path"], "/api/v2/goals/G1/kpis/K1/unlink")
        out, body = sent("goal", "checkin", "G1", "Waiting on legal", "--signal", "at_risk", "--from", "ben")
        self.assertEqual((body["body"], body["signal"], body["from_actor"]), ("Waiting on legal", "at_risk", "ben"))
        out, body = sent("goal", "status", "G1", "auto")
        self.assertEqual(out["status_source"], "auto")
        out, body = sent("proposal", "create", "--kind", "kpi_target", "--goal", "G1", "--kpi", "K1",
                         "--payload", '{"kind": "maintain", "min": 30}', "--reason", "too hard")
        self.assertEqual((body["kind"], body["payload"], body["reason"]), ("kpi_target", {"kind": "maintain", "min": 30}, "too hard"))
        out, body = sent("proposal", "decide", "P1", "confirm", "--note", "agreed")
        self.assertEqual((body["decision"], body["note"]), ("confirm", "agreed"))
        code, out = run_hub("kpi", "list", "--unlinked", "--bot", "ops", env=self.env)
        self.assertEqual(code, 0)
        self.assertIn("unlinked=1", out["query"])
        self.assertIn("auto_for=ops", out["query"])
        code, out = run_hub("kpi", "show", "K1", "--effective", env=self.env)
        self.assertEqual(code, 0, out)
        self.assertEqual(out["readings"], [{"value": 17.0}])
        self.assertIn("/api/v2/kpis/K1/readings?effective=1", [p for m, p, _, b in Stub.seen])

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

        def post(self, path, body, key=None):
            self.posted.append((path, body))
            if path == "bots/register":
                return {"created": True, "status": "planned", "bot_owners": ["cara"]}
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

    def test_the_new_commands_parse_and_carry_what_the_tools_need(self):
        args = hubcli.parser().parse_args(["bot", "access", "seo", "--read", "team:legal,ben", "--write", "everyone"])
        self.assertEqual((args.fn, args.slug, args.read, args.write, args.see), ("bot access", "seo", "team:legal,ben", "everyone", None))
        args = hubcli.parser().parse_args(["bot", "owners", "seo", "--add", "ben", "cara"])
        self.assertEqual((args.fn, args.add, args.remove), ("bot owners", ["ben", "cara"], []))
        args = hubcli.parser().parse_args(["human", "add", "sean@acme.example", "--name", "Sean"])
        self.assertEqual((args.fn, args.email, args.name), ("human add", "sean@acme.example", "Sean"))
        args = hubcli.parser().parse_args(["bot", "create", "seo", "--record-only", "--reports-to", "human:cara"])
        self.assertEqual((args.fn, args.record_only, args.template, args.reports_to), ("bot create", True, None, "human:cara"))
        args = hubcli.parser().parse_args(["group", "update", "--name", "SEO", "--parent", "marketing", "--add-bot", "seo", "--add-bot", "links"])
        self.assertEqual((args.fn, args.group, args.name, args.parent, args.add_bots),
                         ("group update", None, "SEO", "marketing", ["seo", "links"]))
        args = hubcli.parser().parse_args(["group", "update", "seo", "--parent", "", "--remove-human", "cara"])
        self.assertEqual((args.fn, args.group, args.parent, args.remove_humans), ("group update", "seo", "", ["cara"]))
        self.assertEqual(hubcli.parser().parse_args(["group", "list"]).fn, "group list")
        from clients import hubtools
        self.assertEqual(hubtools.audience("everyone"), {"everyone": True})
        self.assertEqual(hubtools.audience("group:legal"), {"people": [], "teams": ["legal"], "bots": []})
        self.assertEqual(hubtools.audience("ben,team:legal,bot:analyst,human:dee"),
                         {"people": ["ben", "dee"], "teams": ["legal"], "bots": ["analyst"]})

    def test_market_apply_carries_a_tier_and_an_explicit_id_for_a_new_entity(self):
        """The Librarian's market setup writes the company as company/self and each competitor with a tier."""
        argv = ["market", "apply", "i1", "--source", "https://acme.example", "--entity-type", "company",
                "--entity-name", "Acme", "--tier", "core", "--new-id", "company/self",
                "--edge-src", "company/acme", "--edge-rel", "competes_with", "--edge-dst", "company/self"]
        args = hubcli.parser().parse_args(argv)
        self.assertEqual((args.fn, args.tier, args.new_id), ("market apply", "core", "company/self"))
        posted = []

        class Client:
            def __init__(self, *a, **k): pass
            def get(self, path, **query): return {"actor": "bot:librarian"}
            def post(self, path, body, key=None): posted.append((path, body)); return {}

        from clients import remotecli
        real, remotecli.Client = remotecli.Client, Client
        os.environ["HUB_API_URL"] = "http://hub.test"
        self.addCleanup(os.environ.pop, "HUB_API_URL", None)
        self.addCleanup(setattr, remotecli, "Client", real)
        remotecli.run(args)
        path, body = posted[0]
        self.assertEqual(path, "market/insights/i1/apply")
        self.assertEqual((body["entity"]["tier"], body["entity"]["id"]), ("core", "company/self"))
        self.assertEqual(body["edge"], {"src": "company/acme", "rel": "competes_with", "dst": "company/self"})
        # Without them the body is what it was.
        args = hubcli.parser().parse_args(["market", "apply", "i2", "--entity-type", "segment", "--entity-name", "Owners"])
        remotecli.run(args)
        self.assertEqual(posted[1][1]["entity"], {"type": "segment", "name": "Owners", "summary": ""})


if __name__ == "__main__":
    unittest.main(verbosity=2)


class Tags(unittest.TestCase):
    def test_tag_commands_use_the_mcp_contract_and_keep_task_label_keys(self):
        from unittest.mock import patch
        from clients import hubtools, remotecli

        class API:
            def __init__(self):
                self.calls = []

            def get(self, path, **query):
                self.calls.append(("GET", path, query))
                return {"tags": []}

            def post(self, path, body, **kwargs):
                self.calls.append(("POST", path, body))
                return {"tag": body}

        api = API()
        with tempfile.TemporaryDirectory() as directory:
            markdown = Path(directory) / "checklist.md"
            markdown.write_text("- [ ] Smoke checks\n")
            with patch.dict(os.environ, {"HUB_API_URL": "https://acme.example", "HUB_TOKEN": "test"}), patch.object(remotecli, "Client", return_value=api):
                create = hubcli.parser().parse_args(["tag", "create", "release-2026-10-02", "--from-template", "release-checklist",
                    "--metadata", '{"date":"2026-10-02"}', "--markdown-file", str(markdown)])
                remotecli.run(create)
                update = hubcli.parser().parse_args(["tag", "update", "release-2026-10-02", "--version", "2",
                    "--markdown", "- [x] Smoke checks", "--metadata", "{}"])
                remotecli.run(update)
                remotecli.run(hubcli.parser().parse_args(["tag", "list", "--templates"]))
        self.assertEqual(api.calls, [
            ("POST", "tags/release-checklist/instances", {"key": "release-2026-10-02", "metadata": {"date": "2026-10-02"}, "markdown": "- [ ] Smoke checks\n"}),
            ("POST", "tags/release-2026-10-02", {"version": 2, "metadata": {}, "markdown": "- [x] Smoke checks"}),
            ("GET", "tags", {"is_template": "true"}),
        ])
        task = hubcli.parser().parse_args(["task", "label", "12345678", "--add", "release-2026-10-02"])
        self.assertEqual(task.add, ["release-2026-10-02"])
        for name in ("hub_tag_list", "hub_tag_show", "hub_tag_create", "hub_tag_update"):
            self.assertIn(name, hubtools.BY_NAME)


def test_team_icon_cli_streams_the_owner_logo(tmp_path, monkeypatch):
    from clients import remotecli
    logo = tmp_path / "logo.png"
    logo.write_bytes(b"logo")
    calls = []
    class IconClient:
        def __init__(self, *args, **kwargs):
            pass
        def post_bytes(self, path, source):
            calls.append((path, source.read()))
            return {"url": "/api/v2/team/icon"}
    monkeypatch.setenv("HUB_API_URL", "https://tico.example.com")
    monkeypatch.setattr(remotecli, "Client", IconClient)
    args = hubcli.parser().parse_args(["team", "icon", str(logo)])
    assert remotecli.run(args) == {"url": "/api/v2/team/icon"}
    assert calls == [("team/icon", b"logo")]


def test_task_board_filters_survive_the_all_form():
    from clients.hubtools import task_list
    args = hubcli.parser().parse_args(['task', 'list', '--all', '--type', 'Dev ticket', '--step', 'To do',
                                      '--sort', 'step', '--number', '42', '--updated-since', '2026-01-01T00:00:00Z', '--brief'])
    class Api:
        def __init__(self):
            self.query = None
        def get(self, path, **query):
            if path == 'bots':
                return []
            self.query = query
            return {'tasks': [{'id': 'task'}]}
    api = Api()
    result = task_list(api, vars(args))
    assert result == {'tasks': [{'id': 'task'}], 'bots': []}
    assert {key: api.query[key] for key in ('type', 'step', 'sort', 'number', 'updated_since', 'brief')} == {
        'type': 'Dev ticket', 'step': 'To do', 'sort': 'step', 'number': 42,
        'updated_since': '2026-01-01T00:00:00Z', 'brief': 'true'}


def test_task_rename_cli_keeps_number_and_step_fields(monkeypatch):
    from clients import remotecli
    sent = []
    class Api:
        def __init__(self, *args, **kwargs):
            pass
        def get(self, path, **query):
            if path == 'me':
                return {'actor': 'human:ana', 'kind': 'member'}
            return {'task': {'id': 'task', 'version': 3}}
        def post(self, path, body, key=None):
            sent.append((path, body))
            return {}
    monkeypatch.setattr(remotecli, 'Client', Api)
    monkeypatch.setenv('HUB_API_URL', 'http://example.test')
    monkeypatch.setenv('HUB_TOKEN', 'test-token')
    remotecli.run(hubcli.parser().parse_args(['task', 'update', '#42', '--title', 'Ticket copy',
                                           '--step', 'To do', '--step-rank', '2', '--number', '42']))
    assert sent[0][0] == 'tasks/#42'
    assert {key: sent[0][1][key] for key in ('title', 'step', 'step_rank', 'number', 'version')} == {
        'title': 'Ticket copy', 'step': 'To do', 'step_rank': 2.0, 'number': 42, 'version': 3}
