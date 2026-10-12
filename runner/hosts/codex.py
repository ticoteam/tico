"""The Codex host: one `codex app-server` process over stdio, many bot threads inside it.

Verified against codex-cli 0.153.3 (docs/history/hub-v2.md §12). The transport is newline-delimited
JSON-RPC 2.0 on the process's stdin/stdout; `--listen unix://...` is the control socket for
`codex app-server proxy` and closes plain clients, so it is not used.

Per-thread environment. `thread/start` takes a free-form `config` object which the server
deep-merges over `~/.codex/config.toml`, so the bot's environment is delivered as
`shell_environment_policy` (inherit `core`, `set` the employee's own variables). Verified: a turn's shell saw exactly the variables the runner passed. Construct the host with
`env_mode="process"` to fall back to one app-server process per bot whose own environment is
the bot's; `CODEX_ENV_MODE=process` does the same.

The user's global MCP servers. The merge is a merge, so neither `config: {"mcp_servers": {}}`
nor `-c mcp_servers={}` on the command line removes them (both probed: all six still started).
What does work is overriding each configured server: a stdio server's `command` becomes
`/usr/bin/true` and a remote server's `startup_timeout_ms` becomes 1, so it fails at startup
and contributes no tools (`mcp_disable_config`). Codex's own built-ins (`cua_repl`,
`codex_apps`) are not in `mcp_servers` and cannot be turned off through thread config in
0.153.3.

A bot's declared remote MCP servers (`mcp:` in its `tools:`, clients/mcp_servers.py) are added to `mcp_servers` beside
`hub`. Codex takes streamable HTTP only, and reads `bearer_token_env_var` / `env_http_headers` from the app-server
process's own environment, which is this bot's (`make_host` passes the run's env): the bot's credential is named, not copied.
"""

import json
import os
import subprocess
import threading
import time
from datetime import datetime, timezone

from clients import mcp_servers
from .. import isolation
from .base import Host, HostError, compact_at, hub_mcp_server, is_limit

CLIENT_INFO = {"name": "tico-keeper", "title": "Tico keeper", "version": "0.1"}
REQUEST_TIMEOUT_S = 120
START_TIMEOUT_S = 60

# Environment names that never travel into a bot thread: the shell policy inherits them from
# the app-server process anyway, and copying them wastes room in every request.
ENV_SKIP = {"PWD", "OLDPWD", "SHLVL", "_", "TMPDIR", "XPC_SERVICE_NAME", "XPC_FLAGS",
            "__CF_USER_TEXT_ENCODING", "TERM_SESSION_ID", "SSH_AUTH_SOCK"}
ENV_MAX_VALUE = 8000
NOOP_COMMAND = "/usr/bin/true"


def iso(epoch):
    """An epoch-seconds timestamp as ISO-8601 UTC, or None."""
    if epoch is None:
        return None
    try:
        return datetime.fromtimestamp(float(epoch), timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError):
        return None


def env_for_config(env):
    """The `set` table for `shell_environment_policy`: the employee's environment, trimmed."""
    out = {}
    for k, v in (env or {}).items():
        if k in ENV_SKIP or not isinstance(v, str) or len(v) > ENV_MAX_VALUE:
            continue
        if "\x00" in v:
            continue
        out[k] = v
    return out


def mcp_disable_config(codex_home=None):
    """A `mcp_servers` override that stops every server in the user's config.toml from starting.

    Returns {} when there is no config file or no servers in it.
    """
    home = codex_home or os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")
    path = os.path.join(home, "config.toml")
    try:
        import tomllib
        with open(path, "rb") as fh:
            doc = tomllib.load(fh)
    except Exception:
        return {}
    servers = doc.get("mcp_servers") or {}
    out = {}
    for name, s in servers.items():
        if not isinstance(s, dict):
            continue
        out[name] = ({"command": NOOP_COMMAND, "args": []} if s.get("command")
                     else {"startup_timeout_ms": 1})
    return {"mcp_servers": out} if out else {}


class CodexHost(Host):
    name = "codex"
    supports_steer = True

    def __init__(self, cmd=("codex", "app-server"), env=None, env_mode=None, log=None,
                 stderr_path=None, config=None, cli_overrides=(), spawn=None):
        super().__init__(log=log)
        self.cmd = list(cmd)
        self.env = dict(env) if env is not None else None      # the app-server's own environment
        self.env_mode = env_mode or os.environ.get("CODEX_ENV_MODE") or "config"
        self.stderr_path = stderr_path
        # `config` is merged over ~/.codex/config.toml for the thread only. The owner's global MCP
        # servers (mobbin, pencil, node_repl, cua_repl, posthog, codex_apps) all start otherwise,
        # and a bot has no use for them.
        self.base_config = dict(config) if config is not None else mcp_disable_config()
        self.cli_overrides = list(cli_overrides)
        self._spawn = spawn or isolation.popen
        self.proc = None
        self._reader = None
        self._stderr_reader = None
        self._next_id = 0
        self._pending = {}                 # request id -> {"event": Event, "result":, "error":}
        self._active_turn = {}             # thread id -> turn id
        self._done_turns = set()           # turn ids already reported completed or failed
        self._token_seen = {}              # thread id -> the running totals last reported for it
        self._restarts = 0
        self._server_info = {}

    # ------------------------------------------------------------------ process
    def start(self):
        with self._lock:
            if self.alive():
                return
            argv = list(self.cmd)
            for o in self.cli_overrides:
                argv += ["-c", o]
            stderr = subprocess.DEVNULL
            if self.stderr_path:
                stderr = open(self.stderr_path, "ab", buffering=0)
            self.proc = self._spawn(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=stderr, env=self.env, text=True, bufsize=1)
            self._pending = {}
            self._reader = threading.Thread(target=self._read_loop, args=(self.proc,), daemon=True)
            self._reader.start()
            self._log(f"codex: app-server started pid {getattr(self.proc, 'pid', '?')}")
        self._initialize()

    def _initialize(self):
        res = self.request("initialize", {"clientInfo": CLIENT_INFO, "capabilities": {"experimentalApi": True}}, timeout=START_TIMEOUT_S)
        self._server_info = res or {}
        self.notify("initialized", {})
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
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def alive(self):
        return bool(self.proc) and self.proc.poll() is None

    def supervise(self):
        """Restart and re-initialize a dead app-server. The keeper re-resumes the threads."""
        if self.alive():
            return False
        self._restarts += 1
        self._log("codex: app-server is gone, restarting")
        self.proc = None
        try:
            self.start()
        except Exception as e:
            self.emit("error", error=f"codex app-server would not restart: {e}", host_restart=True)
            return True
        self._active_turn = {}
        self.emit("error", error="codex app-server restarted", host_restart=True)
        return True

    # ------------------------------------------------------------------ wire
    def _write(self, msg):
        if not self.alive():
            raise HostError("codex app-server is not running")
        line = json.dumps(msg, ensure_ascii=False) + "\n"
        try:
            self.proc.stdin.write(line)
            self.proc.stdin.flush()
        except (BrokenPipeError, ValueError, OSError) as e:
            raise HostError(f"codex app-server stdin closed: {e}")

    def notify(self, method, params=None):
        self._write({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def request(self, method, params=None, timeout=REQUEST_TIMEOUT_S):
        with self._lock:
            self._next_id += 1
            rid = self._next_id
            slot = {"event": threading.Event()}
            self._pending[rid] = slot
        self._write({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
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
                except Exception as e:                      # a bad notification must not kill us
                    self._log(f"codex: event error {e}")
        except (ValueError, OSError):
            pass
        finally:
            for slot in list(self._pending.values()):
                slot.setdefault("error", {"message": "codex app-server exited"})
                slot["event"].set()

    def _on_message(self, msg):
        rid = msg.get("id")
        if rid is not None and "method" not in msg:          # a response to one of our requests
            slot = self._pending.get(rid)
            if slot:
                if "error" in msg:
                    slot["error"] = msg["error"]
                else:
                    slot["result"] = msg.get("result")
                slot["event"].set()
            return
        if rid is not None and "method" in msg:              # a server request; we approve nothing
            self._write({"jsonrpc": "2.0", "id": rid,
                         "error": {"code": -32601, "message": "the runner answers no requests"}})
            return
        self._on_notification(msg.get("method") or "", msg.get("params") or {})

    # ------------------------------------------------------------------ notifications
    def _on_notification(self, method, p):
        tid, turn = p.get("threadId"), p.get("turnId")
        if method == "thread/goal/updated":
            from ..goals import codex_status
            goal = p.get("goal") or {}
            status = codex_status(goal)
            if status in ("active", "paused", "met", "stopped"):
                self.emit("goal", tid, None, status=status, objective=goal.get("objective"),
                          note=goal.get("note") or (goal.get("status") if status == "stopped" else ""))
            return
        if method == "thread/goal/cleared":
            self.emit("goal", tid, None, status="cleared", note="")
            return
        if method == "turn/started":
            turn = (p.get("turn") or {}).get("id")
            if tid:
                self._active_turn[tid] = turn
            self.emit("status", tid, turn, state="active")
            return
        if method == "item/agentMessage/delta":
            self.emit("delta", tid, turn, text=p.get("delta") or "")
            return
        if method == "item/completed":
            item = p.get("item") or {}
            if item.get("type") == "agentMessage":
                self.emit("message", tid, turn, text=item.get("text") or "",
                          final=item.get("phase") == "final_answer", item_id=item.get("id"))
            return
        if method == "thread/tokenUsage/updated":
            u = (p.get("tokenUsage") or {}).get("total") or {}
            self.emit("tokens", tid, turn, input=u.get("inputTokens"), output=u.get("outputTokens"),
                      total=u.get("totalTokens"), usage=self._token_increment(tid, p.get("tokenUsage") or {}))
            return
        if method == "account/rateLimits/updated":
            rl = p.get("rateLimits") or {}
            for w in (rl.get("primary"), rl.get("secondary")):
                if not isinstance(w, dict):
                    continue
                self.emit("rate_limits", tid, turn, used_percent=w.get("usedPercent"),
                          window_minutes=w.get("windowDurationMins"), resets_at=iso(w.get("resetsAt")),
                          plan_type=rl.get("planType"))
            return
        if method == "thread/status/changed":
            state = (p.get("status") or {}).get("type")
            if state == "idle" and tid:
                self._active_turn.pop(tid, None)
            self.emit("status", tid, None, state=state)
            return
        if method == "turn/completed":
            t = p.get("turn") or {}
            turn = t.get("id") or self._active_turn.get(tid)
            if tid:
                self._active_turn.pop(tid, None)
            if turn in self._done_turns:
                return
            self._done_turns.add(turn)
            status = t.get("status")
            if status == "failed":
                msg = ((t.get("error") or {}).get("message") or "the turn failed")
                self.emit("turn_failed", tid, turn, error=msg, limit=is_limit(msg))
            else:
                self.emit("turn_completed", tid, turn, status=status or "completed")
            return
        if method == "error":
            err = p.get("error") or {}
            msg = err.get("message") or json.dumps(err)[:500]
            if turn and not p.get("willRetry") and turn not in self._done_turns:
                self._done_turns.add(turn)
                self._active_turn.pop(tid, None)
                self.emit("turn_failed", tid, turn, error=msg, limit=is_limit(msg))
            else:
                self.emit("error", tid, turn, error=msg, limit=is_limit(msg))
            return
        # thread/started, hook/*, mcpServer/*, item/started, turn/plan, warnings: nothing to do

    # ------------------------------------------------------------------ threads
    def _token_increment(self, thread_id, usage):
        """The tokens one `thread/tokenUsage/updated` adds, as {input, cached, output}.

        Codex reports the thread's running totals (across every turn it has had) and the last model
        call's own. The first report in this process has no earlier total to subtract, so it counts
        `last`; later ones count what the total grew by, and a repeat of the same total adds nothing.
        """
        def fields(row):
            row = row if isinstance(row, dict) else {}

            def count(key):
                try:
                    return max(0, int(row.get(key) or 0))
                except (TypeError, ValueError):
                    return 0
            return {"input": count("inputTokens"), "cached": count("cachedInputTokens"),
                    "output": count("outputTokens")}
        total, last = fields(usage.get("total")), fields(usage.get("last"))
        before = self._token_seen.get(thread_id)
        self._token_seen[thread_id] = total
        if before is None:
            return last
        return {key: max(0, total[key] - before[key]) for key in total}

    def _thread_params(self, settings):
        cfg = dict(self.base_config)
        if self.env_mode == "config":
            env = env_for_config(settings.get("env"))
            if env:
                cfg["shell_environment_policy"] = {"inherit": "core", "set": env}
        # The hub's own MCP server rides next to the neutered global ones (docstring above):
        # the bot sees the hub tools and nothing of the operator's.
        hub = hub_mcp_server(settings.get("env"))
        remote = mcp_servers.codex_config(settings.get("mcp_servers") or [], settings.get("env") or {}, self.env)
        if hub or remote:
            servers = dict(cfg.get("mcp_servers") or {})
            for name, table in remote.items():
                # A name the operator's own config.toml also has is already a disabled stub; a second table
                # under it would merge with that one (url next to command), so the bot's takes another name.
                servers["tico_" + name if name in servers or name == "hub" else name] = table
            if hub:
                servers["hub"] = hub
            cfg["mcp_servers"] = servers
        # The bot's "Compact at": Codex compacts once the conversation holds this many tokens.
        cfg["model_auto_compact_token_limit"] = compact_at(settings.get("compact_at"))
        params = {"cwd": settings["cwd"], "approvalPolicy": "never",
                  "sandbox": "danger-full-access"}
        if settings.get("model"):
            params["model"] = settings["model"]
        if cfg:
            params["config"] = cfg
        return params

    def start_thread(self, bot, settings):
        res = self.request("thread/start", self._thread_params(settings), timeout=START_TIMEOUT_S)
        tid = ((res or {}).get("thread") or {}).get("id")
        if not tid:
            raise HostError(f"thread/start for {bot} returned no thread id")
        return tid

    def resume_thread(self, bot, thread_id, settings):
        params = dict(self._thread_params(settings), threadId=thread_id, excludeTurns=True)
        res = self.request("thread/resume", params, timeout=START_TIMEOUT_S)
        return ((res or {}).get("thread") or {}).get("id") or thread_id

    def fork_thread(self, bot, thread_id, settings):
        params = dict(self._thread_params(settings), threadId=thread_id, excludeTurns=True)
        res = self.request("thread/fork", params, timeout=START_TIMEOUT_S)
        tid = ((res or {}).get("thread") or {}).get("id")
        if not tid:
            raise HostError(f"thread/fork for {bot} returned no thread id")
        return tid

    # ------------------------------------------------------------------ turns
    def start_turn(self, thread_id, text, effort=None):
        params = {"threadId": thread_id, "input": [{"type": "text", "text": text}]}
        if effort:
            params["effort"] = effort
        res = self.request("turn/start", params)
        turn = ((res or {}).get("turn") or {}).get("id")
        if not turn:
            raise HostError("turn/start returned no turn id")
        self._active_turn[thread_id] = turn
        return turn

    def start_goal(self, thread_id, action, objective, effort=None):
        if action == "clear":
            self.request("thread/goal/clear", {"threadId": thread_id})
            return self._control_completed(thread_id)
        if action == "pause":
            try:
                self.request("thread/goal/set", {"threadId": thread_id, "status": "paused"})
            except HostError:
                self.request("thread/goal/clear", {"threadId": thread_id})
            return self._control_completed(thread_id)
        self.request("thread/goal/set", {"threadId": thread_id, "objective": objective, "status": "active"})
        return self.start_turn(thread_id, objective, effort=effort)

    def _control_completed(self, thread_id):
        import uuid
        turn = str(uuid.uuid4())
        self.emit("turn_completed", thread_id, turn, status="completed")
        return turn

    def start_command(self, thread_id, text, effort=None):
        name, _, args = text.strip().partition(" ")
        if name == "/compact":
            self.request("thread/compact/start", {"threadId": thread_id})
            return self._control_completed(thread_id)
        if name == "/review":
            target = {"type": "custom", "instructions": args} if args else {"type": "uncommittedChanges"}
            result = self.request("review/start", {"threadId": thread_id, "target": target})
            turn = (result.get("turn") or {}).get("id")
            if not turn:
                raise HostError("review/start returned no turn id")
            self._active_turn[thread_id] = turn
            return turn
        return self.start_turn(thread_id, text, effort=effort)

    def steer(self, thread_id, turn_id, text):
        self.request("turn/steer", {"threadId": thread_id, "expectedTurnId": turn_id,
                                    "input": [{"type": "text", "text": text}]})

    def interrupt(self, thread_id, turn_id):
        self.request("turn/interrupt", {"threadId": thread_id, "turnId": turn_id}, timeout=30)

    # ------------------------------------------------------------------ helpers
    def active_turn(self, thread_id):
        return self._active_turn.get(thread_id)
