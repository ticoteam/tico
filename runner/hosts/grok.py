"""The Grok Build host: one `grok agent stdio` process per bot, speaking ACP.

Verified against grok 1.0.25 (docs/history/hub-v2.md §12). The process owns one session at a time, so
the runner runs one per bot. Model and reasoning effort are command-line options on `grok
agent` (they are not accepted after the `stdio` subcommand):

    grok agent -m grok-4.6 --reasoning-effort high --always-approve --no-leader stdio

`session/prompt` is a long-running request: its response *is* the end of the turn, so it is
sent without blocking and its `stopReason` becomes `turn_completed` (or `turn_failed`).
Turn ids are the runner's own uuid4 — ACP does not have them.
"""

import json
import os
import subprocess
import threading
import uuid

from clients import mcp_servers
from .. import isolation
from .base import Host, HostError, is_limit

PROTOCOL_VERSION = 1
CLIENT_CAPS = {"fs": {"readTextFile": False, "writeTextFile": False}}
REQUEST_TIMEOUT_S = 120

# Grok 4.6 accepts four explicit reasoning levels. Unknown values fall back to the
# provider default so a malformed local manifest cannot prevent the host from starting.
EFFORTS = {"low": "low", "medium": "medium", "high": "high", "xhigh": "xhigh"}
DEFAULT_EFFORT = "high"
DEFAULT_MODEL = "grok-4.6"
# The operator's own MCP servers that Grok would pick up from other tools' files (~/.claude.json,
# ~/.cursor/mcp.json) and from the xAI account: off, so a bot gets the hub and its declared servers. Grok
# has no switch for servers in its own ~/.grok/config.toml or in plugins; those still load.
NO_OPERATOR_MCP = {"GROK_CLAUDE_MCPS_ENABLED": "false", "GROK_CURSOR_MCPS_ENABLED": "false",
                   "GROK_MANAGED_MCPS_ENABLED": "false"}


def effort_for(effort):
    return EFFORTS.get(str(effort or "").strip().lower(), DEFAULT_EFFORT)


class GrokHost(Host):
    name = "grok"

    def __init__(self, bot=None, model=None, effort=None, env=None, cmd=None, log=None,
                 stderr_path=None, spawn=None):
        super().__init__(log=log)
        self.bot = bot
        self.model = model or DEFAULT_MODEL
        self.effort = effort_for(effort)
        self.env = dict(env) if env is not None else None
        self.cmd = list(cmd) if cmd else ["grok", "agent", "-m", self.model,
                                          "--reasoning-effort", self.effort,
                                          "--always-approve", "--no-leader", "stdio"]
        self.stderr_path = stderr_path
        self._spawn = spawn or isolation.popen
        self.proc = None
        self._reader = None
        self._next_id = 0
        self._pending = {}                 # request id -> slot (sync) or callback (async)
        self._turn = {}                    # session id -> turn id
        self._reply = {}                   # turn id -> latest contiguous assistant response
        self._reply_boundary = set()       # tool call separates progress from the next response
        self._done_turns = set()
        self._caps = {}

    # ------------------------------------------------------------------ process
    def start(self):
        with self._lock:
            if self.alive():
                return
            stderr = subprocess.DEVNULL
            if self.stderr_path:
                stderr = open(self.stderr_path, "ab", buffering=0)
            env = {**(self.env if self.env is not None else os.environ), **NO_OPERATOR_MCP}
            self.proc = self._spawn(self.cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=stderr, env=env, text=True, bufsize=1)
            self._pending = {}
            self._reader = threading.Thread(target=self._read_loop, args=(self.proc,), daemon=True)
            self._reader.start()
            self._log(f"grok[{self.bot}]: agent started pid {getattr(self.proc, 'pid', '?')}")
        res = self.request("initialize", {"protocolVersion": PROTOCOL_VERSION,
                                          "clientCapabilities": CLIENT_CAPS})
        self._caps = (res or {}).get("agentCapabilities") or {}
        return res

    def stop(self):
        with self._lock:
            proc, self.proc = self.proc, None
        if not proc:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except OSError:
            pass
        try:
            proc.terminate(); proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def alive(self):
        return bool(self.proc) and self.proc.poll() is None

    def supervise(self):
        if self.alive():
            return False
        self._log(f"grok[{self.bot}]: agent is gone, restarting")
        self.proc = None
        try:
            self.start()
        except Exception as e:
            self.emit("error", error=f"grok agent would not restart: {e}", host_restart=True)
            return True
        self._turn = {}
        self.emit("error", error="grok agent restarted", host_restart=True)
        return True

    # ------------------------------------------------------------------ wire
    def _write(self, msg):
        if not self.alive():
            raise HostError("grok agent is not running")
        try:
            self.proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, ValueError, OSError) as e:
            raise HostError(f"grok agent stdin closed: {e}")

    def notify(self, method, params=None):
        self._write({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def _send(self, method, params, callback=None):
        with self._lock:
            self._next_id += 1
            rid = self._next_id
            slot = {"event": threading.Event(), "callback": callback}
            self._pending[rid] = slot
        self._write({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        return rid, slot

    def request(self, method, params=None, timeout=REQUEST_TIMEOUT_S):
        rid, slot = self._send(method, params)
        if not slot["event"].wait(timeout):
            self._pending.pop(rid, None)
            raise HostError(f"{method} timed out after {timeout}s")
        self._pending.pop(rid, None)
        if "error" in slot:
            err = slot["error"] or {}
            raise HostError(f"{method}: {err.get('message') or err}")
        return slot.get("result")

    def _read_loop(self, proc):
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                try:
                    self._on_message(msg)
                except Exception as e:
                    self._log(f"grok[{self.bot}]: event error {e}")
        except (ValueError, OSError):
            pass
        finally:
            for slot in list(self._pending.values()):
                slot.setdefault("error", {"message": "grok agent exited"})
                slot["event"].set()
                if slot.get("callback"):
                    try:
                        slot["callback"](None, slot["error"])
                    except Exception:
                        pass

    def _on_message(self, msg):
        rid = msg.get("id")
        if rid is not None and "method" not in msg:
            slot = self._pending.get(rid)
            if not slot:
                return
            if "error" in msg:
                slot["error"] = msg["error"]
            else:
                slot["result"] = msg.get("result")
            slot["event"].set()
            if slot.get("callback"):
                slot["callback"](slot.get("result"), slot.get("error"))
            return
        if rid is not None and "method" in msg:      # the agent asks the ACP client something
            if msg["method"] == "session/request_permission":
                # The runner already starts Grok with --always-approve and no sandbox. Grok still
                # delegates a few permission decisions to its ACP client, so honor that explicit
                # policy by selecting an allow option from the request. Unknown option shapes fail
                # closed instead of guessing an id.
                options = (msg.get("params") or {}).get("options") or []
                chosen = next((o for o in options if o.get("kind") == "allow_always"), None)
                chosen = chosen or next((o for o in options if o.get("kind") == "allow_once"), None)
                outcome = ({"outcome": "selected", "optionId": chosen["optionId"]}
                           if chosen and chosen.get("optionId") else {"outcome": "cancelled"})
                self._write({"jsonrpc": "2.0", "id": rid, "result": {"outcome": outcome}})
            else:
                self._write({"jsonrpc": "2.0", "id": rid,
                             "error": {"code": -32601, "message": "unsupported client request"}})
            return
        self._on_notification(msg.get("method") or "", msg.get("params") or {})

    # ------------------------------------------------------------------ notifications
    def _on_notification(self, method, p):
        if method != "session/update":
            return                                   # _x.ai/* announcements are informational
        sid = p.get("sessionId")
        turn = self._turn.get(sid)
        if turn is None:
            return
        u = p.get("update") or {}
        what = u.get("sessionUpdate")
        text = ((u.get("content") or {}).get("text")) if isinstance(u.get("content"), dict) else None
        if what == "agent_message_chunk" and text:
            if turn in self._reply_boundary:
                self._reply[turn] = ""
                self._reply_boundary.discard(turn)
            self._reply[turn] = (self._reply.get(turn) or "") + text
            self.emit("delta", sid, turn, text=text, delta_kind="text")
        elif what == "tool_call":
            # ACP streams progress and the eventual answer as message chunks. A new tool
            # invocation separates responses; tool_call_update may arrive late and must
            # not split a final answer. Keep progress in deltas, not concatenated into it.
            self._reply_boundary.add(turn)
            # The hub cannot tell where one message ends from the text alone, so the call is
            # reported too, by its kind (read, execute, ...) and never its title or input.
            self.emit("tool", sid, turn, tool=str(u.get("kind") or "tool"), status="started",
                      item_id=u.get("toolCallId"))
        elif what == "agent_thought_chunk" and text:
            self.emit("delta", sid, turn, text=text, delta_kind="thought")

    # ------------------------------------------------------------------ threads
    def _configure_environment(self, settings):
        """ACP sessions cannot set tool env; rotate the per-bot process before load/new."""
        env = settings.get("env")
        if not env or env == self.env:
            return
        if self._turn:
            raise HostError("cannot change the Grok tool environment during a turn")
        reader = self._reader
        self.stop()
        if reader and reader is not threading.current_thread():
            reader.join(timeout=5)
            if reader.is_alive():
                raise HostError("previous Grok reader did not stop before environment rotation")
        self.env = dict(env)
        self.start()

    @staticmethod
    def _mcp(settings):
        """The bot's declared remote MCP servers in ACP's shape (headers resolved: ACP has no placeholders;
        they go over the agent's stdin and nowhere else)."""
        return mcp_servers.grok_config(settings.get("mcp_servers") or [], settings.get("env") or {})

    def start_thread(self, bot, settings):
        self._configure_environment(settings)
        res = self.request("session/new", {"cwd": settings["cwd"], "mcpServers": self._mcp(settings)})
        sid = (res or {}).get("sessionId")
        if not sid:
            raise HostError(f"session/new for {bot} returned no session id")
        return sid

    def resume_thread(self, bot, thread_id, settings):
        self._configure_environment(settings)
        self.request("session/load", {"sessionId": thread_id, "cwd": settings["cwd"],
                                      "mcpServers": self._mcp(settings)})
        return thread_id

    def fork_thread(self, bot, thread_id, settings):
        """ACP has no fork: a fresh session in the same cwd is the closest thing."""
        return self.start_thread(bot, settings)

    # ------------------------------------------------------------------ turns
    def start_turn(self, thread_id, text, effort=None):
        turn = str(uuid.uuid4())
        self._turn[thread_id] = turn
        self._reply[turn] = ""

        def done(result, error):
            self._finish(thread_id, turn, result, error)

        self._send("session/prompt",
                   {"sessionId": thread_id, "prompt": [{"type": "text", "text": text}]}, done)
        return turn

    def _finish(self, sid, turn, result, error):
        if turn in self._done_turns:
            return
        self._done_turns.add(turn)
        self._turn.pop(sid, None)
        reply = self._reply.pop(turn, "")
        self._reply_boundary.discard(turn)
        if error:
            msg = (error or {}).get("message") or str(error)
            self.emit("turn_failed", sid, turn, error=msg, limit=is_limit(msg))
            return
        stop = (result or {}).get("stopReason") or "end_turn"
        if reply and stop != "cancelled":
            self.emit("message", sid, turn, text=reply, final=True)
        if stop in ("refusal", "error"):
            self.emit("turn_failed", sid, turn, error=f"stopReason {stop}", limit=False)
        else:
            self.emit("turn_completed", sid, turn,
                      status="interrupted" if stop == "cancelled" else "completed",
                      stop_reason=stop)
        self.emit("status", sid, None, state="idle")

    def steer(self, thread_id, turn_id, text):
        """ACP has no mid-turn input: Grok Build takes the next prompt after the current one."""
        raise HostError("grok agent cannot steer a running turn")

    def interrupt(self, thread_id, turn_id):
        self.notify("session/cancel", {"sessionId": thread_id})
