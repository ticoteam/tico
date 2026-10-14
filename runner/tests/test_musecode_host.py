"""The Muse Code host (runner/hosts/musecode.py) against `muse exec --json` output recorded from Muse Code
1.4.7 with its offline `--provider echo`, plus a smoke run of the real CLI when `muse` is on PATH."""
import json
import os
import shutil
import stat
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import pytest

from runner.hosts.musecode import MuseCodeHost
from runner.tests.test_profiles import service

SID = "01a13b0d-c339-75b1-b8db-1d660fc7a499"
RUN = "3ba8458d-1acf-4e21-9a8a-5d217c6b1966"


def line(seq, kind, payload):
    return {"schema_version": 1, "stream": {"kind": "session", "id": SID}, "sequence": seq, "payload_type": kind,
            "payload_schema_version": 1, "payload": {"command_id": RUN, "run_stream": {"kind": "run", "id": RUN}, **payload}}


# Recorded (trimmed to the events the host reads, plus two it must skip).
ECHO = [
    line(1, "runtime.command.accepted", {"kind": "command_accepted", "client_id": None, "command_kind": "turn.submit"}),
    line(5, "run.lifecycle.started", {"kind": "run_started", "prompt": "Say hi"}),
    line(18, "run.output.delta", {"kind": "run_output_delta", "text": "echo: Say hi"}),
    line(35, "run.terminal.completed", {"kind": "run_terminal", "terminal": "completed", "text": "echo: Say hi", "reason": None}),
]
# The terminal a required MCP server that does not start gives (seen with a dead server), with token counts
# in the shape Muse's own protocol uses; the running `cumulative` totals must not be counted.
FAILED = ECHO[:2] + [
    line(3, "run.model.completed", {"usage": {"inputTokens": 100, "outputTokens": 7, "cacheReadTokens": 40,
                                              "cumulative": {"inputTokens": 900, "outputTokens": 70}}}),
    line(4, "run.terminal.failed", {"kind": "run_terminal", "terminal": "failed", "text": None,
                                    "reason": "invalid run configuration: Required MCP server `hub` failed during startup"}),
]


class FakeProc:
    def __init__(self, lines, rc):
        self.rc, self.pid = rc, 1
        self.stdout = iter([json.dumps(row) + "\n" for row in lines] + ["not json\n"])

    def wait(self, timeout=None):
        return self.rc

    def terminate(self):
        pass

    kill = terminate


def events(h, timeout=3):
    got, end = [], time.monotonic() + timeout
    while time.monotonic() < end:
        got += h.drain()
        if any(e["kind"] == "status" and e.get("state") == "idle" for e in got):
            return got
        time.sleep(0.01)
    raise AssertionError(got)


class MuseCode(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.settings = {"cwd": "/repo", "model": "muse-spark-1.3",
                         "env": {"HOME": "/home/x", "HUB_API_URL": "https://hub.test", "HUB_TOKEN": "tok-secret",
                                 "HUB_BOT": "coo", "LINEAR_API_KEY": "lin-secret"},
                         "mcp_servers": [{"name": "linear", "service": "linear", "url": "https://mcp.linear.app/mcp",
                                          "transport": "http", "env": "LINEAR_API_KEY",
                                          "headers": {"Authorization": "Bearer ${LINEAR_API_KEY}"}}]}

    def host(self, lines, rc=0):
        procs = []

        def spawn(argv, **kwargs):
            prompt = Path(argv[argv.index("--prompt-file") + 1])
            procs.append((argv, kwargs, prompt.read_text()))
            return FakeProc(lines, rc)
        self.config_home = Path(self.tmp.name) / "config"
        h = MuseCodeHost(bot="coo", spawn=spawn, state_dir=Path(self.tmp.name) / "state", config_home=self.config_home)
        h.start()
        return h, procs

    def test_a_turn_resumes_its_session_streams_text_and_ends_with_the_final_answer(self):
        h, procs = self.host(ECHO)
        tid = h.start_thread("coo", self.settings)
        turn = h.start_turn(tid, "Say hi")
        got = events(h)
        argv, kwargs, prompt = procs[0]
        self.assertEqual(argv[:3], ["muse", "exec", "--json"])
        self.assertEqual(argv[argv.index("--session-id") + 1], tid)              # every turn names the session
        self.assertEqual(argv[argv.index("--model") + 1], "muse-spark-1.3")
        self.assertEqual(argv[argv.index("--reasoning-effort") + 1], "medium")
        self.assertEqual(prompt, "Say hi")                                       # in a file, not argv
        self.assertNotIn("Say hi", argv)
        self.assertEqual(list((Path(self.tmp.name) / "state").iterdir()), [])     # and the file is gone after
        # The hub and the remote server are per-run settings; secrets stay `${VAR}` in the environment.
        overrides = [argv[i + 1] for i, a in enumerate(argv) if a == "-c"]
        hub = json.loads(next(o for o in overrides if o.startswith("mcp_servers.hub=")).split("=", 1)[1])
        self.assertEqual(hub["env"]["HUB_TOKEN"], "${HUB_TOKEN}")
        linear = json.loads(next(o for o in overrides if o.startswith("mcp_servers.linear=")).split("=", 1)[1])
        self.assertEqual(linear["headers"]["Authorization"], "${TICO_MCP_1}")
        self.assertEqual(kwargs["env"]["TICO_MCP_1"], "Bearer lin-secret")
        self.assertEqual(kwargs["env"]["MUSE_NO_AUTO_UPDATE"], "1")
        self.assertFalse(any("secret" in a for a in argv))
        self.assertEqual([e["text"] for e in got if e["kind"] == "delta"], ["echo: Say hi"])
        self.assertEqual([e["text"] for e in got if e["kind"] == "message" and e["final"]], ["echo: Say hi"])
        self.assertEqual([(e["turn_id"], e["status"]) for e in got if e["kind"] == "turn_completed"], [(turn, "completed")])
        # A restarted runner resumes the same session by its id.
        self.assertEqual(h.resume_thread("coo", tid, self.settings), tid)

    def test_a_real_recorded_tool_call_is_reported(self):
        """Muse 1.4.7 announces a tool as task.lifecycle.proposed with task_kind "tool.<name>"."""
        fixtures = Path(__file__).parent / "fixtures" / "musecode"
        lines = [json.loads(row) for row in (fixtures / "real-tool.jsonl").read_text().splitlines() if row.strip()]
        h, _ = self.host(lines)
        h.start_turn(h.start_thread("coo", self.settings), "x")
        got = [{k: v for k, v in e.items() if k in ("kind", "name", "tool", "phase", "text", "delta_kind", "final",
                                                    "status", "state")} for e in events(h) if e["kind"] != "tokens"]
        self.assertEqual(got, json.loads((fixtures / "real-tool.expected.json").read_text()))

    def test_the_operators_own_muse_servers_never_load(self):
        """Muse loads every server in $XDG_CONFIG_HOME/muse/settings.json and refuses `-c` on those names, so
        a turn runs on Tico's own Muse folder, and any servers in it are taken out first."""
        operator = Path(self.tmp.name) / "home" / ".config" / "muse"
        operator.mkdir(parents=True)
        (operator / "settings.json").write_text('{"schema_version": 1, "mcpServers": {"personal": {"command": "x"}}}')
        h, procs = self.host(ECHO)
        (self.config_home / "muse").mkdir(parents=True)
        (self.config_home / "muse" / "settings.json").write_text(
            '{"schema_version": 1, "tui": {"x": true}, "mcpServers": {"stray": {"command": "y"}}}')
        settings = {**self.settings, "env": {**self.settings["env"], "HOME": str(operator.parents[1]),
                                             "XDG_CONFIG_HOME": str(operator.parent)}}
        h.start_turn(h.start_thread("coo", settings), "x")
        events(h)
        argv, kwargs, _ = procs[0]
        self.assertEqual(kwargs["env"]["XDG_CONFIG_HOME"], str(self.config_home))
        self.assertEqual(json.loads((self.config_home / "muse" / "settings.json").read_text()),
                         {"schema_version": 1, "tui": {"x": True}})
        self.assertIn("mcpServers", (operator / "settings.json").read_text())      # the operator's file is untouched
        servers = sorted(a.split("=", 1)[0] for a in argv if a.startswith("mcp_servers."))
        self.assertEqual(servers, ["mcp_servers.hub", "mcp_servers.linear"])

    def test_a_failed_terminal_fails_the_turn_with_its_reason_and_counts_its_tokens(self):
        h, _ = self.host(FAILED, rc=1)
        h.start_turn(h.start_thread("coo", self.settings), "x")
        got = events(h)
        failed = next(e for e in got if e["kind"] == "turn_failed")
        self.assertIn("Required MCP server `hub` failed", failed["error"])
        self.assertFalse(failed["limit"])
        self.assertFalse(any(e["kind"] == "turn_completed" for e in got))
        tokens = next(e for e in got if e["kind"] == "tokens")
        self.assertEqual(tokens["usage"], {"input": 100, "cached": 40, "output": 7})


class Readiness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / "bin").mkdir()
        muse = root / "bin" / "muse"
        muse.write_text("#!/bin/sh\necho 'muse 1.4.7-R6101.1'\n")
        muse.chmod(muse.stat().st_mode | stat.S_IXUSR)
        self.home = root / "home"
        keep = {k: v for k, v in os.environ.items() if k not in ("META_API_KEY", "MUSE_AUTH_PATH", "XDG_CONFIG_HOME")}
        patcher = mock.patch.dict(os.environ, {**keep, "PATH": f"{root / 'bin'}:/usr/bin:/bin", "HOME": str(self.home)},
                                  clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.runner = service(root)

    def test_signed_out_is_missing_and_a_login_file_or_key_is_ready(self):
        row = self.runner.runtime_readiness("musecode", [])
        self.assertEqual((row["authenticated"], row["detail"]), ("missing", "Muse sign-in required (sign in from Tico) or META_API_KEY"))
        self.assertEqual(row["models"], ["muse-spark-1.3"])
        operator = self.home / ".config" / "muse" / "auth.json"      # the operator's own login is not the bots'
        operator.parent.mkdir(parents=True)
        operator.write_text("{}")
        self.assertEqual(self.runner.runtime_readiness("musecode", [])["authenticated"], "missing")
        auth = self.runner.muse_config_home() / "muse" / "auth.json"
        auth.parent.mkdir(parents=True)
        auth.write_text("{}")
        self.assertEqual(self.runner.runtime_readiness("musecode", [])["detail"], "Signed in with a Meta account")
        auth.unlink()
        with mock.patch.dict(os.environ, {"META_API_KEY": "k"}):
            self.assertEqual(self.runner.runtime_readiness("musecode", [])["detail"], "Signed in with META_API_KEY")

    def test_a_computer_wide_key_that_reports_ready_reaches_the_turn(self):
        attempt = {"bot": "coo", "token": "t", "config": {"runtime": "musecode"}}
        with mock.patch.dict(os.environ, {"META_API_KEY": "computer-key"}):
            self.assertEqual(self.runner.runtime_readiness("musecode", [])["authenticated"], "ready")
            self.assertEqual(self.runner.environment(attempt)["META_API_KEY"], "computer-key")
        self.assertNotIn("META_API_KEY", self.runner.environment(attempt))
        other = {**attempt, "config": {"runtime": "claude"}}
        with mock.patch.dict(os.environ, {"META_API_KEY": "computer-key"}):
            self.assertNotIn("META_API_KEY", self.runner.environment(other))


@pytest.mark.slow
@unittest.skipUnless(shutil.which("muse"), "muse is not on PATH")
class RealCli(unittest.TestCase):
    """Two turns of the installed CLI on its offline echo provider: no account, no network model."""

    def test_two_turns_share_one_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            h = MuseCodeHost(bot="coo", cmd=[shutil.which("muse")], provider="echo", state_dir=Path(tmp) / "state")
            h.start()
            settings = {"cwd": str(repo), "env": dict(os.environ)}
            tid = h.start_thread("coo", settings)
            for text in ("first", "second"):
                h.start_turn(tid, text)
                got = events(h, timeout=120)
                self.assertEqual([e["text"] for e in got if e["kind"] == "message"], ["echo: " + text])


class Hardening(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_a_terminal_without_text_still_ends_with_the_streamed_answer(self):
        lines = [line(10, "run.output.delta", {"text": "Looking"}),
                 line(11, "tool.call.started", {"tool_name": "hub"}),
                 line(12, "run.output.delta", {"text": "The answer"}),
                 line(13, "run.terminal.completed", {"terminal": "completed", "reason": None})]
        h = MuseCodeHost(bot="coo", spawn=lambda argv, **kw: FakeProc(lines, 0), state_dir=self.tmp.name)
        h.start()
        h.start_turn(h.start_thread("coo", {"cwd": "/repo", "env": {"HOME": "/home/x"}}), "x")
        got = events(h)
        self.assertEqual([e["text"] for e in got if e["kind"] == "message" and e["final"]], ["The answer"])

    def test_an_unreadable_config_folder_is_a_failed_check_not_a_raised_heartbeat(self):
        folder = Path(self.tmp.name) / "musecode" / "config" / "muse"
        folder.mkdir(parents=True)
        (folder / "auth.json").write_text("{}")
        os.chmod(folder, 0)
        self.addCleanup(os.chmod, folder, 0o700)
        if os.access(folder / "auth.json", os.F_OK):
            self.skipTest("running as root")
        from runner import service as S
        runner = S.Runner.__new__(S.Runner)
        runner.host_state = Path(self.tmp.name)
        runner.team_model_keys = lambda *a: None
        runner.headless_login = lambda *a: ""
        with mock.patch.dict(os.environ, {"HOME": self.tmp.name}), \
                mock.patch.object(S.shutil, "which", return_value="/nonexistent/muse"):
            os.environ.pop("XDG_CONFIG_HOME", None)
            os.environ.pop("MUSE_AUTH_PATH", None)
            row = runner.runtime_readiness("musecode", [])
        self.assertEqual(row["authenticated"], "failed")
