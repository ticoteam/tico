"""The Muse Code host: one `muse exec --json` process per turn (Meta's coding CLI).

Verified against Muse Code 1.4.7 (1.4.7-R6101.1) with its offline `--provider echo`. A thread is a
Muse session whose id Tico makes (a UUID): every turn runs with `--session-id <id>`, and an id the CLI
already has continues that session (both turns land in one session log), so a restart resumes it.

    muse exec --json --yolo --approval-mode never --approval-judge off --user-input-auto-resolve
              --model muse-spark-1.3 --reasoning-effort medium --workspace <repo>
              --session-id <uuid> -c mcp_servers.hub={...} --prompt-file <file>

`--yolo` turns off approval and the sandbox and trusts the workspace (the bot's own repository), as the
other hosts run. The prompt goes in a file the turn can read: the CLI takes no prompt on stdin and refuses
`/dev/stdin`. Events are one JSON object per line, `{"schema_version": 1, "stream": {...},
"payload_type": ..., "payload": {...}}`: `run.output.delta` streams text and `run.terminal.completed`,
`.failed` or `.cancelled` ends the run with the final `text` or a `reason`. Anything else is skipped.

The hub reaches the model as a stdio MCP server set for this run only (`-c mcp_servers.hub=...`). Muse
takes a secret only as a whole-value `${VAR}` it reads from its own environment, so the hub's credential
and each remote server's header are named, never put on the command line. The launcher updates itself
unless MUSE_NO_AUTO_UPDATE=1; the runner updates the harness itself (runner/harness_tools.py), so every
run sets it.

A bot turn loads only the hub, the servers its bot.yaml declares and the repository's own MCP config.
Muse also loads every server in `$XDG_CONFIG_HOME/muse/settings.json` and has no switch to skip them
(`-c mcp_servers.<name>.enabled=false` on a server that file names is refused: "the server name is
reserved"). So turns never run on the operator's `~/.config/muse`: the runner gives each a Muse folder
of Tico's own (`config_home`: a subscription profile's, else the runner's), and `prepare_config` takes
any MCP servers out of its settings before each run. The Meta-account login (`muse login`) lives in that
folder's `muse/auth.json`; META_API_KEY in the environment wins over it.
"""

import json
import os
import subprocess
import tempfile
import threading
import uuid
from pathlib import Path

from clients import mcp_servers
from .. import isolation
from .base import HUB_MCP_ENV, Host, HostError, hub_mcp_server, is_limit

EXECUTABLE = "muse"
# Catalog id -> the CLI's own model id. Anything else is passed through, so a model Meta adds works
# without a change here.
MODELS = {"muse-spark-1.3": "muse-spark-1.3"}
DEFAULT_MODEL = "muse-spark-1.3"
EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")
DEFAULT_EFFORT = "medium"
STOP_TIMEOUT_S = 10
STDERR_TAIL = 2000
QUIET = {"MUSE_NO_AUTO_UPDATE": "1", "NO_COLOR": "1"}


def model_for(model):
    value = str(model or "").strip()
    return DEFAULT_MODEL if not value or value == "default" else MODELS.get(value, value)


def effort_for(effort):
    value = str(effort or "").strip().lower()
    return value if value in EFFORTS else DEFAULT_EFFORT


def _count(row, *keys):
    for key in keys:
        try:
            if row.get(key) is not None:
                return int(row[key])
        except (TypeError, ValueError):
            continue
    return 0


def tool_started(kind, payload):
    """The tool a JSONL event starts, or None. Real Muse 1.4.7 runs announce a tool call as
    `task.lifecycle.proposed` with `event.task_kind` "tool.<name>"; `tool.*.started` is the older shape."""
    if kind == "task.lifecycle.proposed":
        event = payload.get("event")
        task = event.get("task_kind") if isinstance(event, dict) else None
        return task[5:] if isinstance(task, str) and task.startswith("tool.") and task[5:] else None
    if kind.startswith("tool.") and kind.endswith(".started"):
        return str(payload.get("tool_name") or payload.get("name") or payload.get("tool") or "tool")
    return None


def usage_of(payload):
    """(input, cached, output) from one event's own token counters, or None. Muse reports usage per
    model completion (`inputTokens`/`input_tokens`, ...); a session's `cumulative` running totals are
    skipped, because a resumed session's totals include earlier turns."""
    found = None
    stack = [payload]
    while stack:
        row = stack.pop()
        if not isinstance(row, dict):
            continue
        if any(k in row for k in ("inputTokens", "input_tokens")) and any(k in row for k in ("outputTokens", "output_tokens")):
            inp = _count(row, "inputTokens", "input_tokens")
            cached = _count(row, "cacheReadTokens", "cache_read_tokens", "cachedTokens", "cached_tokens")
            out = _count(row, "outputTokens", "output_tokens")
            found = (found[0] + inp, found[1] + cached, found[2] + out) if found else (inp, cached, out)
            continue
        stack.extend(v for k, v in row.items() if k != "cumulative" and isinstance(v, dict))
    return found


def prepare_config(home):
    """Make `home` (XDG_CONFIG_HOME) ready for a bot turn: `muse/` exists and its settings name no MCP
    servers, so none but this run's own load. The folder is Tico's own, never a person's."""
    directory = isolation.mkdir(Path(home) / "muse")
    path = directory / "settings.json"
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return directory
    except ValueError:
        stored = {"schema_version": 1}     # Muse refuses a broken file anyway; this one is ours to rewrite
    except OSError as exc:
        raise HostError(f"Muse settings are not readable: {exc}")
    if not isinstance(stored, dict):
        stored = {"schema_version": 1}
    elif not {"mcpServers", "mcp_servers"} & set(stored):
        return directory
    cleaned = {k: v for k, v in stored.items() if k not in ("mcpServers", "mcp_servers")}
    fd, temporary = tempfile.mkstemp(prefix=".settings-", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(cleaned, out, indent=2)
        isolation.chown(temporary)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return directory


def mcp_overrides(settings):
    """(["-c", "mcp_servers.<name>=<json>", ...], extra environment) for one run: the hub as a stdio server,
    and the bot's declared remote servers. Secrets stay in the environment as `${VAR}`; a header that is not
    exactly one placeholder is resolved into a variable of its own (`TICO_MCP_<n>`), which only this run has."""
    env = settings.get("env") or {}
    args, extra = [], {}
    hub = hub_mcp_server(env)
    if hub:
        server = {"transport": "stdio", "command": hub["command"], "args": hub["args"],
                  "env": {name: "${" + name + "}" for name in HUB_MCP_ENV if hub["env"].get(name)}}
        args += ["-c", "mcp_servers.hub=" + json.dumps(server, separators=(",", ":"))]
    config, extra = mcp_servers.muse_config(settings.get("mcp_servers") or [], env)
    for name, server in config.items():
        args += ["-c", f"mcp_servers.{name}=" + json.dumps(server, separators=(",", ":"))]
    return args, extra


class MuseCodeHost(Host):
    name = "musecode"
    supports_steer = False

    def __init__(self, bot=None, cmd=(EXECUTABLE,), provider=None, state_dir=None, log=None, stderr_path=None,
                 spawn=None, config_home=None):
        super().__init__(log=log)
        self.bot = bot
        self.cmd = list(cmd)
        # `echo` is Muse's offline provider (tests and smoke runs): it takes no model or effort.
        self.provider = provider
        self.state_dir = Path(state_dir) if state_dir else Path(tempfile.gettempdir()) / "tico-musecode"
        # Muse's XDG_CONFIG_HOME for every turn (see the module docstring): never the operator's.
        self.config_home = Path(config_home) if config_home else self.state_dir / "config"
        self.stderr_path = stderr_path
        self._spawn = spawn or isolation.popen
        self._up = False
        self.proc = None
        self._reader = None
        self._threads = {}            # thread id (the Muse session id) -> {"bot", "settings"}
        self._turn = None             # (thread id, turn id) while a process runs
        self._interrupted = set()
        self._done_turns = set()

    # ------------------------------------------------------------------ process
    def start(self):
        self._up = True

    def stop(self):
        with self._lock:
            self._up = False
            proc, self.proc = self.proc, None
            running = self._turn
        if proc:
            if running:
                self._interrupted.add(running[1])
            self._end(proc)
        reader = self._reader
        if reader and reader is not threading.current_thread():
            reader.join(timeout=STOP_TIMEOUT_S)

    def alive(self):
        return self._up

    @staticmethod
    def _end(proc):
        try:
            proc.terminate()
            proc.wait(timeout=STOP_TIMEOUT_S)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    # ------------------------------------------------------------------ threads
    def _record(self, thread_id, bot, settings):
        self._threads[thread_id] = {"bot": bot, "settings": dict(settings)}
        return thread_id

    def start_thread(self, bot, settings):
        # The session is created by its first turn; the id is ours so a restart can name it.
        return self._record(str(uuid.uuid4()), bot, settings)

    def resume_thread(self, bot, thread_id, settings):
        return self._record(thread_id, bot, settings)

    def fork_thread(self, bot, thread_id, settings):
        # `muse exec` cannot copy a session: a fork starts a fresh one.
        return self.start_thread(bot, settings)

    # ------------------------------------------------------------------ turns
    def argv(self, thread_id, settings, prompt_file, effort=None):
        argv = self.cmd + ["exec", "--json", "--yolo", "--approval-mode", "never", "--approval-judge", "off",
                           "--user-input-auto-resolve", "--workspace", str(settings["cwd"]),
                           "--session-id", thread_id]
        if self.provider:
            argv += ["--provider", self.provider]
        if self.provider != "echo":
            argv += ["--model", model_for(settings.get("model")),
                     "--reasoning-effort", effort_for(effort or settings.get("effort"))]
        argv += mcp_overrides(settings)[0]
        return argv + ["--prompt-file", str(prompt_file)]

    def env(self, settings):
        env = dict(settings.get("env") or os.environ)
        if not env.get("HOME") and os.environ.get("HOME"):
            env["HOME"] = os.environ["HOME"]
        env["XDG_CONFIG_HOME"] = str(self.config_home)
        env.update(QUIET)
        env.update(mcp_overrides(settings)[1])
        return env

    def _prompt_file(self, text):
        directory = isolation.mkdir(self.state_dir)
        fd, path = tempfile.mkstemp(prefix="prompt-", suffix=".md", dir=directory)
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(text)
        os.chmod(path, 0o600)
        isolation.chown(path)
        return Path(path)

    def start_turn(self, thread_id, text, effort=None):
        with self._lock:
            if not self._up:
                raise HostError("muse host is not running")
            if self._turn:
                raise HostError("muse host already has a turn running")
            t = self._threads.get(thread_id)
            if t is None:
                raise HostError(f"unknown muse thread {thread_id}")
            turn = str(uuid.uuid4())
            prepare_config(self.config_home)
            prompt = self._prompt_file(text)
            stderr = tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace")
            try:
                proc = self._spawn(self.argv(thread_id, t["settings"], prompt, effort), stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=stderr, cwd=t["settings"]["cwd"],
                                   env=self.env(t["settings"]), text=True, bufsize=1)
            except OSError as exc:
                stderr.close()
                prompt.unlink(missing_ok=True)
                raise HostError(f"could not start muse: {exc}")
            self.proc = proc
            self._turn = (thread_id, turn)
        self._log(f"muse[{self.bot}]: turn started pid {getattr(proc, 'pid', '?')}")
        self.emit("status", thread_id, None, state="active")
        self._reader = threading.Thread(target=self._read_loop, args=(proc, thread_id, turn, stderr, prompt),
                                        daemon=True)
        self._reader.start()
        return turn

    def _read_loop(self, proc, tid, turn, stderr, prompt):
        state = {"reply": "", "streamed": "", "terminal": "", "reason": "", "usage": None}
        try:
            for line in proc.stdout:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                try:
                    self.on_event(event, tid, turn, state)
                except Exception as exc:
                    self._log(f"muse[{self.bot}]: event error {exc}")
        except (ValueError, OSError):
            pass
        try:
            rc = proc.wait()
        except Exception:
            rc = -1
        prompt.unlink(missing_ok=True)
        self._finish(tid, turn, rc, self._stderr_tail(stderr), state)

    def _stderr_tail(self, stderr):
        try:
            stderr.seek(0)
            text = stderr.read()
        except (OSError, ValueError):
            text = ""
        finally:
            try:
                stderr.close()
            except OSError:
                pass
        if text and self.stderr_path:
            try:
                with open(self.stderr_path, "a", encoding="utf-8") as log:
                    log.write(text if text.endswith("\n") else text + "\n")
            except OSError:
                pass
        # The CLI's progress notes ("muse: workspace root: ...") are not the reason a run failed.
        lines = [row for row in text.splitlines() if row.strip() and not row.startswith("muse: ")]
        return "\n".join(lines).strip()[-STDERR_TAIL:]

    def on_event(self, event, tid, turn, state):
        """One JSONL event. Unknown payload types are skipped: the CLI adds them freely."""
        if not isinstance(event, dict):
            return
        kind = str(event.get("payload_type") or "")
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        found = usage_of(payload)
        if found:
            seen = state["usage"] or (0, 0, 0)
            state["usage"] = tuple(a + b for a, b in zip(seen, found))
        if kind == "run.output.delta":
            text = payload.get("text")
            if isinstance(text, str) and text:
                state["streamed"] += text
                self.emit("delta", tid, turn, text=text, delta_kind="text")
        elif tool_started(kind, payload):
            name = tool_started(kind, payload)
            state["streamed"] = ""         # text before a tool call is progress, not the answer
            self.emit("tool", tid, turn, name=name, tool=name, phase="tool_execution_start")
        elif kind.startswith("run.terminal."):
            state["terminal"] = str(payload.get("terminal") or kind.rsplit(".", 1)[-1])
            state["reason"] = str(payload.get("reason") or "")
            if isinstance(payload.get("text"), str):
                state["reply"] = payload["text"]

    def _finish(self, tid, turn, rc, stderr, state):
        with self._lock:
            if self._turn and self._turn[1] == turn:
                self._turn = None
                self.proc = None
            interrupted = turn in self._interrupted
        if turn in self._done_turns:
            return
        self._done_turns.add(turn)
        if state["usage"]:
            inp, cached, out = state["usage"]
            self.emit("tokens", tid, turn, input=inp, output=out, total=inp + out,
                      usage={"input": inp, "cached": cached, "output": out})
        if interrupted:
            self.emit("turn_completed", tid, turn, status="interrupted")
        elif state["terminal"] == "completed" and rc == 0:
            # A terminal without its own text still ends with what the model streamed after its last tool call.
            reply = state["reply"] or state["streamed"].strip()
            if reply:
                self.emit("message", tid, turn, text=reply, final=True)
            self.emit("turn_completed", tid, turn, status="completed")
        else:
            text = (state["reason"] or stderr
                    or (f"muse run ended {state['terminal']}" if state["terminal"] else f"muse exited {rc}"))
            self.emit("turn_failed", tid, turn, error=text, limit=is_limit(text))
        self.emit("status", tid, None, state="idle")

    def steer(self, thread_id, turn_id, text):
        raise HostError("Muse Code cannot steer a running turn")

    def interrupt(self, thread_id, turn_id):
        with self._lock:
            proc, running = self.proc, self._turn
            if not proc or not running or running[1] != turn_id:
                return
            self._interrupted.add(turn_id)
        self._end(proc)

    def active_turn(self, thread_id):
        running = self._turn
        return running[1] if running and running[0] == thread_id else None
