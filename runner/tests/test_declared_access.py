"""The runner reports what a bot's employee.yaml declares under `access:`, and never a value.

A real checkout and a real secrets folder in a temporary directory, no network: the report has the
service, identity, verbs, scope and the variable's name, with whether it is set, and none of the
secrets (backend/bot_tools.py turns it into the Tools row on the bot's page).
"""
import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from runner import declared_access
from runner.service import Runner

RUNTIMES = {"codex": {"installed": True, "authenticated": "ready", "detail": "", "models": [],
                      "version": "codex 1.0", "controls": []}}
MANIFEST = """name: atlas
access:
  - service: posthog
    identity: "PostHog project 12345 (US), personal key"
    can: [read]
    env: POSTHOG_KEY
    project: 12345
    note: "funnels only"
  - service: postgres
    identity: read-only role on the replica
    database: warehouse
    can: [read]
    max_rows: 200
    token: must-not-be-reported
  - service: slack
    identity: "Acme workspace via https://bot:hunter2@slack.example/hooks"
    can: [read, post]
    channels: ["#ops", "#launch"]
    env: SLACK_TOKEN
    vault: hub
  - service: gmail
    identity: "{{mailbox}}"
    can: [read]
    env: GOOGLE_SA_KEY
"""
SECRETS = "POSTHOG_KEY=phx_super_secret_value\nDB_WAREHOUSE_URL=postgresql://reader:hunter2@db.internal/app\n"


class DeclaredAccess(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.projects = Path(tmp.name)
        bot = self.projects / "emp-atlas"
        bot.mkdir()
        (bot / "AGENT.md").write_text("# Atlas\n")
        (bot / "employee.yaml").write_text(MANIFEST)
        (self.projects / "secrets").mkdir()
        (self.projects / "secrets" / "_shared.env").write_text(SECRETS)
        self.runner = Runner.__new__(Runner)
        self.runner.config = {"url": "https://acme.test", "token": "t", "runner_id": "r1", "projects_dir": str(self.projects)}
        self.runner._names = None
        self.entries = [{"bot": "atlas", "runner_id": "r1", "state": "active", "config": {"runtime": "codex", "model": "gpt-6-sol"}}]

    def report(self):
        return self.runner.readiness(self.entries, self.runner.preflight(self.entries, RUNTIMES), RUNTIMES)

    def test_names_verbs_scope_and_whether_each_credential_is_set(self):
        tools = {row["service"]: row for row in self.report()["bots"]["atlas"]["tools"]}
        self.assertEqual(list(tools), ["posthog", "postgres", "slack", "gmail"])
        self.assertEqual((tools["posthog"]["credential"], tools["posthog"]["env"], tools["posthog"]["scope"]),
                         ("present", "POSTHOG_KEY", {"project": "12345"}))
        self.assertEqual((tools["postgres"]["credential"], tools["postgres"]["env"], tools["postgres"]["scope"]),
                         ("present", "DB_WAREHOUSE_URL", {"database": "warehouse", "max_rows": "200"}))   # the default variable
        self.assertEqual((tools["slack"]["credential"], tools["slack"]["scope"]), ("hub-vault", {"channels": ["#ops", "#launch"]}))
        self.assertEqual(tools["gmail"]["credential"], "missing")
        self.assertIn("template placeholder", tools["gmail"]["problem"])

    def test_a_key_the_computer_holds_is_present_and_marked_held(self):
        (self.projects / "emp-atlas" / "employee.yaml").write_text(
            "name: atlas\naccess:\n  - service: gmail\n    identity: ana@acme.example\n    can: [read]\n    env: GOOGLE_SA_KEY\n"
            "  - service: posthog\n    can: [read]\n    env: POSTHOG_KEY_2\n")
        for held, expected in ((("GOOGLE_SA_KEY",), ("present", True)), ((), ("missing", None))):
            with mock.patch("runner.service.mail_key.held_by_computer", return_value=bool(held)):
                tools = {row["service"]: row for row in self.report()["bots"]["atlas"]["tools"]}
            self.assertEqual((tools["gmail"]["credential"], tools["gmail"].get("held")), expected)
            self.assertEqual(tools["posthog"]["credential"], "missing")         # only the Google key is held

    def test_no_value_leaves_the_computer(self):
        text = json.dumps(self.report())
        for secret in ("phx_super_secret_value", "hunter2", "must-not-be-reported", "reader:"):
            self.assertNotIn(secret, text)

    def test_a_bot_that_declares_nothing_reports_no_list(self):
        (self.projects / "emp-atlas" / "employee.yaml").write_text("name: atlas\naccess: []\n")
        self.assertNotIn("tools", self.report()["bots"]["atlas"])
        self.assertEqual(declared_access.declared_tools("not a list", {}), [])
        self.assertEqual(declared_access.declared_tools([{"identity": "no service"}, "x"], {}), [])

    def test_a_server_that_predates_the_report_is_not_told_again_for_a_while(self):
        self.runner._tools_after = float("inf")
        self.assertNotIn("tools", self.report()["bots"]["atlas"])


    def test_a_copy_of_a_shared_bot_may_carry_the_originals_name(self):
        # backend-reviewer-arthur runs from emp-backend-reviewer, whose employee.yaml says the original's name.
        copy = self.projects / "bot-atlas-arthur"
        copy.mkdir()
        (copy / "AGENT.md").write_text("# Atlas\n")
        (copy / "employee.yaml").write_text(MANIFEST)            # name: atlas
        config = {"runtime": "codex", "model": "gpt-6-sol", "model_managed_by": "cloud"}
        for shared_from, ok in (("atlas", True), (None, False)):
            self.entries = [{"bot": "atlas-arthur", "runner_id": "r1", "state": "active",
                             "config": {**config, **({"shared_from": shared_from} if shared_from else {})}}]
            problems = self.report()["bots"]["atlas-arthur"]["problems"]
            self.assertEqual("Configuration differs from server" not in problems, ok, problems)


if __name__ == "__main__":
    unittest.main()
