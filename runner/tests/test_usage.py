"""The runner counts a turn's tokens once and sends them with its result (runner/usage.py): the meter, the
hosts' increments, the subscription rule, and a server that predates the field."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from runner.hosts.codex import CodexHost
from runner.hosts.fake import FakeHost
from runner.service import Runner
from runner.tests.test_runner_resilience import FakeClient, attempt
from runner.usage import Meter, billing_for


class Counting(unittest.TestCase):
    def test_the_meter_sums_increments_and_splits_cached_from_uncached_input(self):
        meter = Meter()
        for event in ({"kind": "tokens", "usage": {"input": 1000, "cached": 800, "output": 50}},
                      {"kind": "tokens", "usage": {"input": 500, "cached": 0, "output": 25}},
                      {"kind": "tokens", "input": 99999, "output": 99999},         # a running total, not an increment
                      {"kind": "tokens", "usage": {"input": "x", "cached": None}}):
            meter.add(event)
        self.assertEqual(meter.report("gpt-6-sol", "codex", "api"),
                         {"input_tokens": 700, "cached_tokens": 800, "output_tokens": 75, "model": "gpt-6-sol",
                          "runtime": "codex", "billing": "api"})
        self.assertIsNone(Meter().report("m", "r"))                                 # nothing counted, nothing sent

    def test_codex_reports_running_totals_and_only_the_growth_is_counted(self):
        host = SimpleNamespace(_token_seen={})

        def update(total, last):
            row = lambda i, c, o: {"inputTokens": i, "cachedInputTokens": c, "outputTokens": o, "totalTokens": i + o}   # noqa: E731
            return CodexHost._token_increment(host, "th", {"total": row(*total), "last": row(*last)})
        # A resumed thread: 50k tokens from earlier turns are in the total, so the first report counts `last`.
        self.assertEqual(update((50_000, 40_000, 2_000), (1_000, 800, 100)), {"input": 1000, "cached": 800, "output": 100})
        self.assertEqual(update((52_000, 41_500, 2_150), (1_000, 700, 50)), {"input": 2000, "cached": 1500, "output": 150})
        self.assertEqual(update((52_000, 41_500, 2_150), (1_000, 700, 50)), {"input": 0, "cached": 0, "output": 0})     # a repeat

    def test_only_a_plan_sign_in_is_a_subscription(self):
        cases = [("codex", "Signed in with ChatGPT", "subscription"), ("codex", "Signed in with an API key", "api"),
                 ("claude", "Signed in with CLAUDE_CODE_OAUTH_TOKEN", "subscription"),
                 ("claude", "Signed in with ANTHROPIC_API_KEY", "api")]
        for runtime, detail, expected in cases:
            self.assertEqual(billing_for(runtime, detail), expected, (runtime, detail))


class Sending(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "emp-coo").mkdir()
        self.config = {"url": "https://runner.example", "token": "machine", "projects_dir": str(root), "capacity": 1}

    def tearDown(self):
        self.tmp.cleanup()

    def runner(self, client):
        runner = Runner(self.config, Path(self.tmp.name) / "state", host_factory=lambda a, env: FakeHost(replies=["done"]),
                        client=client, push=lambda path, env=None: (0, ""))
        runner.renew_interval = 0.05
        return runner

    def run_turn(self, client, detail=None):
        runner = self.runner(client)
        if detail:
            runner.runtime_rows = {"codex": {"detail": detail}}
        row = attempt()
        row["config"]["model"] = "gpt-6-sol"
        runner.execute(row)
        return client.completion()

    def test_the_result_carries_the_turns_tokens_the_model_and_how_it_is_billed(self):
        done = self.run_turn(FakeClient())
        part = {"input_tokens": 100, "cached_tokens": 0, "output_tokens": 4, "model": "gpt-6-sol",
                "runtime": "codex", "billing": "api", "harness": "codex", "effort": "", "profile_used": None}
        self.assertEqual(done["usage"], {**part, "segments": [part]})
        self.assertEqual(self.run_turn(FakeClient(), "Signed in with ChatGPT")["usage"]["billing"], "subscription")
