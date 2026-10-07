"""The runtime hosts: framing, parameters and notification mapping (docs/history/hub-v2.md §12).

No runtime is started. `FakeProcess` stands in for `codex app-server` and `grok agent stdio`:
it records the JSON-RPC frames the host writes and lets a test push notifications and
responses back, so the mapping from wire to runner events is checked exactly.
"""

import json
import queue
import tempfile
import unittest
from pathlib import Path

from runner.hosts import base
from runner.hosts.claude import ClaudeHost, model_for, usage_tokens
from runner.hosts.claude import effort_for as claude_effort_for
from runner.hosts.codex import CodexHost, env_for_config, iso
from runner.hosts.fake import FakeHost
from runner.hosts.grok import GrokHost, effort_for
from runner.hosts.gemini import GeminiHost, effort_for as gemini_effort_for
from runner.hosts.gemini import usage_tokens as gemini_usage_tokens
from runner.hosts.pi import PiHost
from runner.hosts.pi import effort_for as pi_effort_for
from runner.hosts.pi import model_for as pi_model_for
from runner.hosts.pi import usage_tokens as pi_usage_tokens


class _Stdout:
    """A blocking line iterator the host's reader thread consumes."""

    def __init__(self):
        self.q = queue.Queue()

    def __iter__(self):
        return self

    def __next__(self):
        line = self.q.get()
        if line is None:
            raise StopIteration
        return line


class _Stdin:
    def __init__(self, proc):
        self.proc = proc

    def write(self, line):
        self.proc.sent.append(json.loads(line))
        self.proc.answer(json.loads(line))

    def flush(self):
        pass

    def close(self):
        pass


class FakeProcess:
    """Popen's surface, as much of it as the hosts use."""

    instances = []

    def __init__(self, argv, **kw):
        self.argv = argv
        self.kwargs = kw
        self.sent = []
        self.stdout = _Stdout()
        self.stdin = _Stdin(self)
        self.pid = 4242
        self._rc = None
        self.responder = None
        FakeProcess.instances.append(self)

    # ---- the test's side
    def push(self, obj):
        self.stdout.q.put(json.dumps(obj) + "\n")

    def notify(self, method, params):
        self.push({"jsonrpc": "2.0", "method": method, "params": params})

    def reply(self, request_id, result=None, error=None):
        msg = {"jsonrpc": "2.0", "id": request_id}
        if error is not None:
            msg["error"] = error
        else:
            msg["result"] = result
        self.push(msg)

    def request_ids(self, method):
        return [m["id"] for m in self.sent if m.get("method") == method and "id" in m]

    def answer(self, msg):
        if self.responder:
            self.responder(self, msg)

    def die(self):
        self._rc = 1
        self.stdout.q.put(None)

    # ---- Popen's side
    def poll(self):
        return self._rc

    def terminate(self):
        self.die()

    def kill(self):
        self.die()

    def wait(self, timeout=None):
        return self._rc


class AuthRetryClassification(unittest.TestCase):
    """One rule for "the runtime could not sign itself in", kept beside the usage-limit rule."""

    def test_a_key_the_provider_refused_is_rejected_not_retried(self):
        text = "unexpected status 401 Unauthorized: Incorrect API key provided: sk-proj-abcdef123456"
        self.assertTrue(base.is_auth_rejected(text))
        self.assertFalse(base.is_auth_retryable(text))
        self.assertTrue(base.is_auth_rejected("Not logged in · Please run /login"))
        self.assertNotIn("sk-proj", base.rejection_reason(text))
        self.assertIn("Incorrect API key", base.rejection_reason(text))

    def test_a_model_at_capacity_is_a_limit_not_a_failure(self):
        # Codex, 2026-10-05 12:42-13:05 UTC: nine runs ended "failed" and two were dismissed as stopped.
        self.assertTrue(base.is_limit("Selected model is at capacity. Please try a different model."))
        self.assertFalse(base.is_limit("The capacity planning doc is at docs/capacity.md"))

# ----------------------------------------------------------------------------- Grok (ACP)
def grok_responder(proc, msg):
    method, rid = msg.get("method"), msg.get("id")
    if rid is None:
        return
    if method == "initialize":
        proc.reply(rid, {"protocolVersion": 1,
                         "agentCapabilities": {"loadSession": True}})
    elif method == "session/new":
        proc.reply(rid, {"sessionId": "sess-1", "models": {}})
    elif method == "session/load":
        proc.reply(rid, {})
    elif method == "session/prompt":
        proc.prompt_id = rid                      # answered by the test, at the end of the turn
    else:
        proc.reply(rid, {})


def make_grok(**kw):
    FakeProcess.instances = []

    def spawn(argv, **kwargs):
        proc = FakeProcess(argv, **kwargs)
        proc.responder = grok_responder
        return proc

    host = GrokHost(bot="seo", model="grok-4.6", effort="xhigh", spawn=spawn, **kw)
    host.start()
    return host, FakeProcess.instances[-1]


class GrokAcp(unittest.TestCase):
    def drain(self, host, kind=None):
        import time
        for _ in range(200):
            time.sleep(0.005)
            evs = host.drain()
            if evs:
                return [e for e in evs if kind is None or e["kind"] == kind]
        return []

    def test_unknown_permission_options_fail_closed(self):
        host, proc = make_grok()
        host._on_message({"jsonrpc": "2.0", "id": "permission-2",
                          "method": "session/request_permission", "params": {"options": [
                              {"optionId": "no", "name": "Reject", "kind": "reject_once"},
                          ]}})
        response = [m for m in proc.sent if m.get("id") == "permission-2" and "method" not in m][0]
        self.assertEqual(response["result"]["outcome"], {"outcome": "cancelled"})

    def test_a_tool_call_is_reported_by_its_kind_only(self):
        host, _ = make_grok()
        host._turn["sess-1"] = "turn-1"
        host._on_notification("session/update", {"sessionId": "sess-1", "update": {
            "sessionUpdate": "tool_call", "kind": "execute", "toolCallId": "c1",
            "title": "curl -H 'Authorization: hunter2' https://example.com", "rawInput": {"command": "hunter2"}}})
        tools = self.drain(host, "tool")
        self.assertEqual([(e["tool"], e["status"], e["item_id"]) for e in tools], [("execute", "started", "c1")])
        self.assertNotIn("hunter2", json.dumps(tools))

# ----------------------------------------------------------------------------- Claude (stream-json)
class ClaudeProcess:
    """Popen's surface for one `claude -p` run: the prompt arrives on stdin, stream-json leaves
    on stdout, and the test decides when and how the process exits."""

    instances = []

    def __init__(self, argv, **kw):
        self.argv = argv
        self.kwargs = kw
        self.prompt = ""
        self.stdout = _Stdout()
        self.stdin = self
        self.pid = 4243
        self.terminated = False
        self._rc = None
        ClaudeProcess.instances.append(self)

    # ---- stdin
    def write(self, text):
        self.prompt += text

    def flush(self):
        pass

    def close(self):
        pass

    # ---- the test's side
    def push(self, obj):
        self.stdout.q.put(json.dumps(obj) + "\n")

    def exit(self, rc=0):
        self._rc = rc
        self.stdout.q.put(None)

    def result(self, text="pong", is_error=False, rc=0, **extra):
        usage = {"input_tokens": 2, "cache_creation_input_tokens": 10521,
                 "cache_read_input_tokens": 15560, "output_tokens": 4}
        self.push({"type": "result", "subtype": "error_during_execution" if is_error else "success",
                   "is_error": is_error, "result": text, "stop_reason": "end_turn",
                   "session_id": self.session_id(), "total_cost_usd": 0.1131, "usage": usage, **extra})
        self.exit(rc)

    def session_id(self):
        for flag in ("--session-id", "--resume"):
            if flag in self.argv:
                return self.argv[self.argv.index(flag) + 1]
        return None

    # ---- Popen's side
    def poll(self):
        return self._rc

    def terminate(self):
        self.terminated = True
        self.exit(143)

    def kill(self):
        self.exit(137)

    def wait(self, timeout=None):
        return self._rc


def make_claude(**kw):
    ClaudeProcess.instances = []
    host = ClaudeHost(bot="cpo", spawn=lambda argv, **kwargs: ClaudeProcess(argv, **kwargs), **kw)
    host.start()
    return host


class ClaudeStreamJson(unittest.TestCase):
    SETTINGS = base.settings("/tmp/emp-cpo", model="claude-sonnet-5", effort="high",
                             env={"HUB_EMPLOYEE": "cpo", "HUB_TOKEN": "t0k", "PATH": "/bin"})

    def drain(self, host, kind=None):
        import time
        out = []
        for _ in range(400):
            time.sleep(0.005)
            out += host.drain()
            if out and out[-1]["kind"] == "status" and out[-1].get("state") == "idle":
                break
        return [e for e in out if kind is None or e["kind"] == kind]

    def finish(self, host, proc, **kw):
        """Let the fake process answer, then wait for the turn's events."""
        proc.push({"type": "system", "subtype": "init", "session_id": proc.session_id()})
        proc.result(**kw)
        return self.drain(host)

    def test_a_turn_with_a_hub_credential_gets_the_hub_mcp_server_and_only_that(self):
        host = make_claude()
        settings = base.settings("/tmp/emp-cpo", env={"HUB_EMPLOYEE": "cpo", "HUB_TOKEN": "t0k",
                                                      "HUB_API_URL": "https://hub.acme.example", "PATH": "/bin"})
        tid = host.start_thread("cpo", settings)
        host.start_turn(tid, "hello")
        proc = ClaudeProcess.instances[-1]
        config = json.loads(proc.argv[proc.argv.index("--mcp-config") + 1])
        self.assertEqual(list(config["mcpServers"]), ["hub"])
        self.assertTrue(config["mcpServers"]["hub"]["args"][0].endswith("clients/hubmcp.py"))
        self.assertEqual(config["mcpServers"]["hub"]["env"]["HUB_TOKEN"], "t0k")
        self.assertNotIn("--strict-mcp-config", proc.argv)     # the bot repo's own .mcp.json stays
        self.finish(host, proc)

    def test_a_missing_saved_session_restarts_once_fresh_under_the_same_id(self):
        host = make_claude()
        tid = host.resume_thread("cpo", "gone-session", self.SETTINGS)
        turn = host.start_turn(tid, "ping")
        dead = ClaudeProcess.instances[-1]
        self.assertEqual(dead.session_id(), "gone-session")
        self.assertIn("--resume", dead.argv)
        dead.kwargs["stderr"].write("No conversation found with session ID: gone-session\n")
        dead.result(text="", is_error=True, rc=1, errors=["No conversation found with session ID: gone-session"])
        import time
        for _ in range(400):
            if len(ClaudeProcess.instances) > 1:
                break
            time.sleep(0.005)
        fresh = ClaudeProcess.instances[-1]
        self.assertNotIn("--resume", fresh.argv)
        self.assertEqual(fresh.argv[fresh.argv.index("--session-id") + 1], "gone-session")
        self.assertEqual(fresh.prompt, "ping")
        events = self.finish(host, fresh)
        self.assertEqual([e["kind"] for e in events if e["kind"] in ("diagnostic", "turn_failed", "turn_completed")],
                         ["diagnostic", "turn_completed"])
        self.assertTrue(all(e["turn_id"] == turn for e in events if e["kind"] in ("diagnostic", "turn_completed")))
        self.assertEqual(host.session_id(tid), "gone-session")   # the runner saves this; next turn resumes it
        host.start_turn(tid, "again")
        again = ClaudeProcess.instances[-1]
        self.assertEqual(again.argv[again.argv.index("--resume") + 1], "gone-session")
        self.finish(host, again)
        host.stop()

    def test_interrupt_terminates_the_process_and_reports_interrupted(self):
        host = make_claude()
        tid = host.start_thread("cpo", self.SETTINGS)
        turn = host.start_turn(tid, "ping")
        proc = ClaudeProcess.instances[-1]
        proc.push({"type": "assistant", "message": {"id": "m", "content": [{"type": "text", "text": "I will"}]}})
        self.assertEqual(host.active_turn(tid), turn)
        host.interrupt(tid, turn)
        self.assertTrue(proc.terminated)
        events = self.drain(host)
        self.assertEqual([e["kind"] for e in events], ["status", "message", "turn_completed", "status"])
        self.assertEqual(events[2]["status"], "interrupted")
        self.assertIsNone(host.active_turn(tid))
        host.interrupt(tid, turn)                              # nothing running: a no-op
        self.assertEqual(host.drain(), [])
        host.stop()

# ----------------------------------------------------------------------------- Gemini (stream-json)
class GeminiProcess:
    instances = []

    def __init__(self, argv, **kw):
        self.argv = argv
        self.kwargs = kw
        self.prompt = ""
        self.stdout = _Stdout()
        self.stdin = self
        self.pid = 4244
        self.terminated = False
        self._rc = None
        GeminiProcess.instances.append(self)

    def write(self, text):
        self.prompt += text

    def close(self):
        pass

    def push(self, obj):
        self.stdout.q.put(json.dumps(obj) + "\n")

    def result(self, status="success", rc=0, **extra):
        self.push({"type": "result", "status": status,
                   "stats": {"input_tokens": 12, "output_tokens": 3, "total_tokens": 15,
                             "models": {"gemini-3.8-flash": {"total_tokens": 15}}},
                   **extra})
        self._rc = rc
        self.stdout.q.put(None)

    def exit(self, rc=0):
        self._rc = rc
        self.stdout.q.put(None)

    def poll(self):
        return self._rc

    def terminate(self):
        self.terminated = True
        self.exit(143)

    def kill(self):
        self.exit(137)

    def wait(self, timeout=None):
        return self._rc


class GeminiStreamJson(unittest.TestCase):
    def setUp(self):
        GeminiProcess.instances = []
        self.home = tempfile.TemporaryDirectory()
        self.host = GeminiHost(bot="botops", home=self.home.name,
                               spawn=lambda argv, **kwargs: GeminiProcess(argv, **kwargs))
        self.host.start()
        self.settings = base.settings("/tmp", model="gemini-3.8-flash", effort="high",
                                      env={"GEMINI_API_KEY": "test-key", "PATH": "/bin"})

    def tearDown(self):
        self.host.stop()
        self.home.cleanup()

    def drain(self, kind=None):
        import time
        events = []
        for _ in range(400):
            time.sleep(0.005)
            events += self.host.drain()
            if events and events[-1]["kind"] == "status" and events[-1].get("state") == "idle":
                break
        return [event for event in events if kind is None or event["kind"] == kind]

    def test_resume_failure_after_provider_event_is_not_replayed(self):
        thread = self.host.resume_thread("botops", "existing-session", self.settings)
        self.host.start_turn(thread, "Do work")
        proc = GeminiProcess.instances[-1]
        proc.push({"type": "tool_use", "tool_name": "run_shell_command"})
        proc.kwargs["stderr"].write('Error resuming session: Invalid session identifier "existing-session".\n')
        proc.exit(1)
        self.assertTrue(self.drain("turn_failed"))
        self.assertEqual(len(GeminiProcess.instances), 1)

    def test_silent_cli_model_fallback_fails_the_turn(self):
        thread = self.host.start_thread("botops", self.settings)
        self.host.start_turn(thread, "use the configured model")
        proc = GeminiProcess.instances[-1]
        proc.result(stats={"input_tokens": 12, "output_tokens": 3, "total_tokens": 15,
                           "models": {"gemini-3.5-flash": {"total_tokens": 15}}})
        failed = self.drain("turn_failed")[0]
        self.assertIn("used gemini-3.5-flash instead of gemini-3.8-flash", failed["error"])

if __name__ == "__main__":
    unittest.main()
