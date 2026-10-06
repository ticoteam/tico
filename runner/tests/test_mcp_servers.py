"""A bot's remote MCP servers (docs/connect-tools.md): the entry is validated, the credential placeholder is filled
only from what the bot was granted, every harness that can take the server gets it next to the hub's own and the
operator's global servers stay out, and one that cannot is named in the bot's readiness warnings.

No network beyond a loopback socket, no runtime started."""
import http.server
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from clients import access_entry, mcp_servers
from runner.hosts import base
from runner.hosts.claude import ClaudeHost
from runner.hosts.codex import CodexHost, mcp_disable_config
from runner.hosts.gemini import GeminiHost
from runner.hosts.grok import GrokHost
from runner.service import Runner

JIRA = {"service": "jira", "can": ["read", "write"], "env": "JIRA_API_TOKEN",
        "mcp": {"url": "https://mcp.atlassian.com/v2/mcp", "transport": "http",
                "headers": {"Authorization": "Bearer ${JIRA_API_TOKEN}"}}}
LINEAR_SSE = {"service": "linear", "can": ["read"], "env": "LINEAR_API_KEY",
              "mcp": {"url": "https://mcp.linear.app/sse", "transport": "sse", "headers": {"Authorization": "Bearer ${LINEAR_API_KEY}"}}}
BASIC = {"service": "wiki", "can": ["read"], "env": "WIKI_AUTH",
         "mcp": {"url": "https://wiki.acme.example/mcp", "headers": {"Authorization": "Basic ${WIKI_AUTH}", "X-Key": "${WIKI_AUTH}"}}}
RUN_ENV = {"JIRA_API_TOKEN": "tok-jira-123", "LINEAR_API_KEY": "tok-linear-456", "WIKI_AUTH": "dXNlcjpwdw==",
           "HUB_API_URL": "https://hub.acme.example", "HUB_TOKEN": "t0k", "HUB_BOT": "atlas", "PATH": "/bin"}


class Validation(unittest.TestCase):
    def clean(self, mcp, env="JIRA_API_TOKEN"):
        return access_entry.clean({"service": "jira", "can": ["read"], "env": env, "mcp": mcp})

    def test_https_only_except_loopback_and_a_known_transport(self):
        for bad in ({"url": "http://mcp.acme.example/mcp"}, {"url": "https://u:pw@x.example/mcp"}):
            with self.assertRaises(access_entry.EntryError, msg=bad):
                self.clean(bad)
        self.assertEqual(self.clean({"url": "http://localhost:8080/mcp"})["mcp"], {"url": "http://localhost:8080/mcp", "transport": "http"})

    def test_a_header_holds_no_value_and_no_variable_but_the_entrys_own(self):
        url = "https://x.example/mcp"
        for headers in ({"Authorization": "${HOME}"}, {"Host": "evil.example"}):
            with self.assertRaises(access_entry.EntryError, msg=headers):
                self.clean({"url": url, "headers": headers})
        with self.assertRaises(access_entry.EntryError) as caught:          # a secret says so, for the register route's code
            self.clean({"url": url, "headers": {"Authorization": "Bearer abcdefghijklmnopqrstuvwx"}})
        self.assertEqual(caught.exception.code, "secret")
        with self.assertRaises(access_entry.EntryError):                    # a placeholder needs `env` to name
            self.clean({"url": url, "headers": {"Authorization": "Bearer ${JIRA_API_TOKEN}"}}, env="")
        with self.assertRaises(access_entry.EntryError):                    # and none in the address
            self.clean({"url": "https://x.example/mcp?key=${JIRA_API_TOKEN}"})


class Substitution(unittest.TestCase):
    def test_a_server_needs_its_variable_in_the_run_and_is_named_when_it_is_not(self):
        servers, problems = mcp_servers.servers_for_run([JIRA, LINEAR_SSE], {"JIRA_API_TOKEN": "tok"})
        self.assertEqual([s["name"] for s in servers], ["jira"])
        self.assertEqual(servers[0]["headers"], {"Authorization": "Bearer ${JIRA_API_TOKEN}"})      # still the placeholder
        self.assertEqual(len(problems), 1)
        self.assertIn("linear", problems[0])
        self.assertIn("LINEAR_API_KEY is not granted", problems[0])
        self.assertNotIn("tok", json.dumps(problems))

    def test_the_runners_own_environment_is_not_the_bots_to_use(self):
        # OPENAI_API_KEY-style variables live in the runner's process; only explicit vault grants count.
        with tempfile.TemporaryDirectory() as tmp:
            secrets = Path(tmp) / "secrets"
            secrets.mkdir()
            (secrets / "atlas.env").write_text("JIRA_API_TOKEN=tok-own\n")
            runner = Runner.__new__(Runner)
            runner.config = {"projects_dir": tmp}
            runner.vault_names = {"a1": {"JIRA_API_TOKEN", "LINEAR_API_KEY"}}
            attempt = {"id": "a1", "bot": "atlas", "config": {}}
            env = {"JIRA_API_TOKEN": "tok-own", "LINEAR_API_KEY": "tok-vault", "WIKI_AUTH": "from-the-runners-process"}
            self.assertEqual(runner.granted_environment(attempt, env), {"JIRA_API_TOKEN": "tok-own", "LINEAR_API_KEY": "tok-vault"})
            servers, problems = mcp_servers.servers_for_run([JIRA, LINEAR_SSE, BASIC], runner.granted_environment(attempt, env))
            self.assertEqual([s["name"] for s in servers], ["jira", "linear"])
            self.assertIn("WIKI_AUTH is not granted", problems[0])

    def test_upgrade_preserves_used_credentials_and_runs_never_read_legacy_fallbacks(self):
        from runner.hosts.pi import openrouter_key
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            secrets = root / "secrets"
            secrets.mkdir()
            (secrets / "_shared.env").write_text("JIRA_API_TOKEN=op://fixture/jira/token\nUNUSED_KEY=unused-fixture\nOPENAI_API_KEY=login-only-fixture\nOPENROUTER_API_KEY=model-fixture\n")
            (secrets / "atlas.env").write_text("OWN_KEY=own-fixture\nFILE_KEY=" + str(secrets / "fixture.json") + "\n")
            (secrets / "fixture.json").write_text('{"fixture":true}')
            service = Runner.__new__(Runner)
            service.config = {"projects_dir": tmp}
            service.client = mock.Mock()
            service.client.get.return_value = {"bots": ["atlas"]}
            service.local_path = lambda bot: root / ("bot-" + bot)
            entry = {"bot": "atlas", "config": {"runtime": "pi", "tools": [JIRA]}}
            with mock.patch.dict(os.environ, {"UNRELATED_KEY": "ambient-fixture"}), mock.patch("runner.service.isolation.identity", return_value=None):
                service.migrate_credentials([entry, {"bot": "new-bot", "config": entry["config"]}])
                body = service.client.post.call_args.args[1]
                values = {item["env"]: item for item in body["credentials"]}
                self.assertEqual(set(values), {"OWN_KEY", "FILE_KEY", "JIRA_API_TOKEN", "OPENROUTER_API_KEY", "UNUSED_KEY"})
                self.assertEqual(values["FILE_KEY"]["kind"], "file")
                self.assertEqual(values["FILE_KEY"]["value"], '{"fixture":true}')
                self.assertEqual(values["JIRA_API_TOKEN"]["value"], "op://fixture/jira/token")
                self.assertEqual(service.client.post.call_count, 1)
                ambient = service.credential_environment("atlas", entry["config"])
                self.assertFalse(set(values) & set(ambient))
                self.assertNotIn("UNRELATED_KEY", ambient)
            service.vault_names = {"attempt": set()}
            self.assertEqual(service.granted_environment({"id": "attempt", "bot": "atlas"}, {"OWN_KEY": "own-fixture"}), {})
            with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "ambient-fixture"}):
                self.assertEqual(openrouter_key({"HUB_WORKSPACE": tmp}, local=False), "")
                self.assertEqual(openrouter_key({"OPENROUTER_API_KEY": "granted-fixture"}, local=False), "granted-fixture")
            with mock.patch("runner.service.isolation.identity", return_value=(10003, 10002)), mock.patch("runner.service.os.chown") as owner, mock.patch("runner.service.os.chmod") as modes:
                service.protect_credential_files()
                owner.assert_any_call(secrets, os.geteuid(), os.getegid(), follow_symlinks=False)
                modes.assert_any_call(secrets, 0o700)
                modes.assert_any_call(secrets / "atlas.env", 0o600)


class HostConfigs(unittest.TestCase):
    def settings(self, tools, runtime, harness=""):
        servers, _ = mcp_servers.servers_for_run(tools, RUN_ENV)
        return base.settings("/tmp/emp-atlas", env=RUN_ENV, mcp_servers=mcp_servers.supported(servers, runtime, harness))

    def test_codex_reads_the_variable_itself_and_the_operators_servers_stay_disabled(self):
        home = tempfile.TemporaryDirectory()
        self.addCleanup(home.cleanup)
        (Path(home.name) / "config.toml").write_text('[mcp_servers.jira]\ncommand = "npx"\n[mcp_servers.posthog]\nurl = "https://mcp.posthog.com"\n')
        host = CodexHost(env=RUN_ENV, env_mode="config", config=mcp_disable_config(home.name))
        params = host._thread_params(self.settings([JIRA, BASIC, LINEAR_SSE], "codex"))
        servers = params["config"]["mcp_servers"]
        self.assertEqual(servers["posthog"], {"startup_timeout_ms": 1})             # the operator's own: still switched off
        self.assertEqual(servers["jira"], {"command": "/usr/bin/true", "args": []})    # (a clash is not merged into)
        self.assertEqual(servers["tico_jira"], {"url": "https://mcp.atlassian.com/v2/mcp", "bearer_token_env_var": "JIRA_API_TOKEN"})
        self.assertEqual(servers["wiki"]["env_http_headers"], {"X-Key": "WIKI_AUTH"})   # exactly ${VAR}: read from the environment
        self.assertEqual(servers["wiki"]["http_headers"], {"Authorization": "Basic dXNlcjpwdw=="})   # any other shape is filled in
        self.assertNotIn("linear", servers)                                          # Codex takes streamable HTTP only
        self.assertNotIn("tok-jira-123", json.dumps(servers["tico_jira"]))
        self.assertEqual(servers["hub"]["env"]["HUB_TOKEN"], "t0k")

    def test_claude_gets_them_beside_the_hub_with_placeholders_not_values(self):
        host = ClaudeHost(bot="atlas", spawn=lambda *a, **k: None)
        settings = self.settings([JIRA, LINEAR_SSE], "claude")
        tid = host.start_thread("atlas", settings)
        argv = host._argv(tid, host._threads[tid], None)
        config = json.loads(argv[argv.index("--mcp-config") + 1])["mcpServers"]
        self.assertEqual(sorted(config), ["hub", "jira", "linear"])
        self.assertEqual(config["jira"], {"type": "http", "url": "https://mcp.atlassian.com/v2/mcp",
                                          "headers": {"Authorization": "Bearer ${JIRA_API_TOKEN}"}})
        self.assertEqual(config["linear"]["type"], "sse")
        self.assertNotIn("tok-jira-123", " ".join(argv))
        self.assertNotIn("--strict-mcp-config", argv)                               # the bot repo's own .mcp.json stays

    def test_gemini_and_grok_take_both_transports(self):
        settings = self.settings([JIRA, LINEAR_SSE], "gemini")
        servers = GeminiHost(bot="atlas", home="/tmp/nowhere")._settings("gemini-3.8-flash", "high", settings["mcp_servers"])["mcpServers"]
        self.assertEqual(servers["jira"]["httpUrl"], "https://mcp.atlassian.com/v2/mcp")
        self.assertEqual(servers["linear"]["url"], "https://mcp.linear.app/sse")
        self.assertEqual(servers["jira"]["headers"], {"Authorization": "Bearer ${JIRA_API_TOKEN}"})   # the CLI expands it: no value on disk
        acp = GrokHost._mcp(self.settings([JIRA, LINEAR_SSE], "grok"))
        self.assertEqual([(s["type"], s["name"]) for s in acp], [("http", "jira"), ("sse", "linear")])
        self.assertEqual(acp[0]["headers"], [{"name": "Authorization", "value": "Bearer tok-jira-123"}])
        self.assertEqual(GrokHost._mcp(base.settings("/tmp/x", env=RUN_ENV)), [])



class Readiness(unittest.TestCase):
    MANIFEST = ("name: atlas\ntools:\n  - service: jira\n    can: [read]\n    env: JIRA_API_TOKEN\n"
                "    mcp: {url: 'https://mcp.atlassian.com/v2/mcp', transport: http, headers: {Authorization: 'Bearer ${JIRA_API_TOKEN}'}}\n"
                "  - service: linear\n    can: [read]\n    env: LINEAR_API_KEY\n"
                "    mcp: {url: 'https://mcp.linear.app/sse', transport: sse}\n"
                "  - service: broken\n    can: [read]\n    mcp: {url: 'http://plain.example/mcp'}\n")

    def report(self, runtime, harness=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        bot = Path(tmp.name) / "bot-atlas"
        bot.mkdir()
        (bot / "AGENT.md").write_text("# Atlas\n")
        (bot / "bot.yaml").write_text(self.MANIFEST)
        (Path(tmp.name) / "secrets").mkdir()
        (Path(tmp.name) / "secrets" / "atlas.env").write_text("JIRA_API_TOKEN=tok\n")
        runner = Runner.__new__(Runner)
        runner.config = {"url": "https://acme.test", "token": "t", "runner_id": "r1", "projects_dir": tmp.name}
        runner._names = None
        runner.bot_credential_names = {"atlas": ["JIRA_API_TOKEN"]}
        config = {"runtime": runtime, "model": "m", **({"harness": harness} if harness else {})}
        entries = [{"bot": "atlas", "runner_id": "r1", "state": "active", "config": config}]
        runtimes = {runtime: {"installed": True, "authenticated": "ready", "detail": "", "models": [], "version": "1", "controls": []}}
        with mock.patch.object(Runner, "mcp_reach", return_value="reachable"):
            return runner.readiness(entries, runner.preflight(entries, runtimes), runtimes)["bots"]["atlas"]

    def test_the_row_carries_the_block_and_the_check_but_not_a_value(self):
        row = self.report("claude")
        jira = next(t for t in row["tools"] if t["service"] == "jira")
        self.assertEqual(jira["mcp"], {"url": "https://mcp.atlassian.com/v2/mcp", "transport": "http",
                                       "headers": {"Authorization": "Bearer ${JIRA_API_TOKEN}"}, "status": "reachable"})
        self.assertNotIn("tok", json.dumps(row).replace("tokens", ""))
        broken = next(t for t in row["tools"] if t["service"] == "broken")
        self.assertIn("https", broken["problem"])
        self.assertNotIn("mcp", broken)
        self.assertEqual(row.get("warnings", []), [])

    def test_a_harness_that_cannot_take_a_server_is_a_warning_naming_the_tool(self):
        warnings = self.report("cursor")["warnings"]
        self.assertEqual(len([w for w in warnings if "MCP server is not passed" in w]), 2)
        self.assertTrue(any(w.startswith("jira:") and "Cursor cannot use it" in w for w in warnings))
        codex = self.report("codex")["warnings"]                   # takes jira (http); not linear (sse)
        self.assertEqual([w.split(":")[0] for w in codex if "MCP" in w], ["linear"])
        self.assertTrue(any("takes http servers, not sse" in w for w in codex))


class Reachability(unittest.TestCase):
    def serve(self, status):
        seen = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                seen.append((self.headers.get("Authorization"), self.rfile.read(int(self.headers.get("content-length") or 0))))
                self.send_response(status)
                self.end_headers()

            def do_GET(self):
                seen.append((self.headers.get("Authorization"), b""))
                self.send_response(status)
                self.end_headers()

            def log_message(self, *args):
                pass
        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return "http://127.0.0.1:%d/mcp" % server.server_port, seen

    def test_reachable_auth_failed_and_unreachable(self):
        for status, expected in ((200, "reachable"), (401, "auth_failed"), (503, "unreachable")):
            url, seen = self.serve(status)
            self.assertEqual(mcp_servers.reachability(url, "http", {"Authorization": "Bearer x"}, timeout=3), expected, status)
            self.assertEqual(seen[0][0], "Bearer x")
            self.assertIn(b'"initialize"', seen[0][1])
        self.assertEqual(mcp_servers.reachability("http://127.0.0.1:9/mcp", "http", {}, timeout=1), "unreachable")


if __name__ == "__main__":
    unittest.main()
