"""The runner's use of its harness installer: what it learns from the server, that an update
waits for the running turn, owner actions from the server, and the heartbeat's report.
`npm` is a stub (runner/tests/test_harness_tools.py), so nothing touches the network."""
import os
import tempfile
import time
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest import mock

import pytest

from clients.tico import APIError
from runner.service import Runner
from runner.tests.test_harness_tools import FAKE_NPM, script


class Client:
    def __init__(self, providers=("openai",), actions=(), refuse_harnesses=False):
        self.posts, self.providers, self.actions = [], list(providers), list(actions)
        self.refuse = refuse_harnesses

    def get(self, path, **query):
        if path == "config":
            return {"enabled_providers": self.providers}
        if path == "runner-harness-actions":
            return {"actions": self.actions}
        if path == "runner-credential-migration":
            return {"bots": []}
        if path == "runner-credential-grants":
            return {"bots": {}}
        return {}

    def post(self, path, body=None, key=None):
        self.posts.append((path, body))
        if path == "runners/heartbeat" and self.refuse and "harnesses" in body["readiness"]:
            raise APIError("invalid", "extra fields not permitted", 422, False)
        return {"attempt": None} if path == "jobs/claim" else {}


class RunnerHarnesses(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "stubs").mkdir()
        (self.root / "latest").write_text("1.0.0")
        script(self.root / "stubs" / "npm", FAKE_NPM.split("\n", 1)[1])
        patcher = mock.patch.dict(os.environ, {"PATH": f"{self.root / 'stubs'}:/usr/bin:/bin",
                                               "FAKE_LATEST": str(self.root / "latest"),
                                               "FAKE_FAIL": str(self.root / "nofail"), "HOME": str(self.root)})
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("TICO_TOOLS_DIR", None)
        self.config = {"url": "https://x.example", "token": "t", "projects_dir": str(self.root), "capacity": 1}

    def runner(self, client):
        runner = Runner(self.config, self.root / "state", client=client, config_path=self.root / "runner.json")
        self.addCleanup(runner.tools.stop)
        return runner

    def maintain(self, runner):
        quiet = dict(readiness_candidates=lambda *a: [], runtime_report=lambda *a: {}, preflight=lambda *a: [],
                     changed_agent_instructions=lambda *a: ({}, {}), mail_agent_instructions=lambda *a: {},
                     recover_output=lambda: None)
        runner._checkout_at = time.monotonic()
        with mock.patch.multiple(runner, **quiet), mock.patch("runner.service.log"):
            runner.maintain()

    def drain(self, runner, rounds=200):
        for _ in range(rounds):
            runner.step_harnesses()
            if runner.tools.job is None:
                break
            time.sleep(0.02)
        runner.step_harnesses()

    def test_a_rejected_tool_report_preserves_other_bots_and_other_tools(self):
        class RejectOne(Client):
            def post(self, path, body=None, key=None):
                if body["readiness"]["bots"]["alpha"]["tools"][0]["service"] == "invalid":
                    raise APIError("validation", "body.readiness.StructuredReadiness.bots.alpha.tools.0.mcp.status: Input should be reachable", 422, False)
                self.report = body
                return {}
        client = RejectOne()
        runner = self.runner(client)
        body = {"readiness": {"schema_version": 1, "bots": {
            "alpha": {"tools": [{"service": "invalid"}, {"service": "valid"}]},
            "beta": {"tools": [{"service": "other"}]}}}}
        with mock.patch("runner.service.log") as logged:
            runner.report_heartbeat(body)
        self.assertTrue(logged.called)
        self.assertEqual(client.report["readiness"]["bots"]["beta"]["tools"], [{"service": "other"}])
        self.assertEqual(client.report["readiness"]["bots"]["alpha"]["tools"], [{"service": "valid"}])
        self.assertIn("Tool report rejected", client.report["readiness"]["bots"]["alpha"]["warnings"][0])
        self.assertEqual(runner._tools_after, 0)

    def test_the_runner_installs_what_the_servers_enabled_providers_need_into_its_own_dir(self):
        client = Client(providers=["openai", "deepseek"])
        runner = self.runner(client)
        self.assertEqual(runner.tools.tools, self.root / "tools")
        self.maintain(runner)
        self.assertEqual(runner.tools.wanted, {"codex", "pi"})
        for _ in range(3):
            self.drain(runner)
        found = {p.name for p in (self.root / "tools" / "bin").iterdir()}
        self.assertEqual(found, {"codex", "pi"})
        self.maintain(runner)
        beat = [body for path, body in client.posts if path == "runners/heartbeat"][-1]
        harnesses = beat["readiness"]["harnesses"]
        self.assertEqual((harnesses["codex"]["installed"], harnesses["codex"]["version"], harnesses["codex"]["managed"]),
                         (True, "1.0.0", True))
        self.assertFalse(harnesses["claude-code"]["installed"])

    @pytest.mark.slow
    def test_an_update_waits_for_the_running_turn_then_goes_in_between_turns(self):
        runner = self.runner(Client(providers=["openai"]))
        self.maintain(runner)
        for _ in range(3):
            self.drain(runner)
        (self.root / "latest").write_text("1.1.0")
        runner.tools.state["checked"]["codex"] = 0
        turn = Future()
        runner.active["a1"] = turn
        runner.attempt_runtimes["a1"] = {"codex"}
        runner.last_heartbeat = time.monotonic()
        with mock.patch("runner.service.log"):
            for _ in range(15):
                runner.tick()
                time.sleep(0.02)
        path, _ = runner.tools.locate(runner.tools.manifests["codex"])
        self.assertEqual(runner.tools.detect(runner.tools.manifests["codex"], path), "1.0.0")
        turn.set_result(None)                      # the turn ends; the next tick is between turns
        with mock.patch("runner.service.log"):
            deadline = time.monotonic() + 10       # a busy CI box can take far longer than 40 ticks
            while time.monotonic() < deadline:
                runner.tick()
                if runner.tools.report()["codex"]["version"] == "1.1.0":
                    break
                time.sleep(0.02)
        self.assertEqual(runner.tools.report()["codex"]["version"], "1.1.0")

    def test_a_claimed_turn_holds_its_harness_and_its_fallbacks(self):
        client = Client(providers=[])
        attempt = {"id": "a2", "bot": "coo", "token": "t", "lease_seconds": 90,
                   "config": {"runtime": "codex", "fallback": {"harness": "gemini", "model": "m"}},
                   "conversation": {"id": "c"}, "message": {"id": "m", "body": "hi", "from_actor": "human:a"}}
        client.post = lambda path, body=None, key=None: {"attempt": attempt} if path == "jobs/claim" else {}
        runner = self.runner(client)
        runner.last_heartbeat = time.monotonic()
        with mock.patch.object(runner.pool, "submit", return_value=Future()), \
                mock.patch.object(runner.state, "record"):
            runner.tick()
        self.assertEqual(runner.attempt_runtimes["a2"], {"codex", "gemini"})

if __name__ == "__main__":
    unittest.main()
