"""The Pi coding-agent host: one `pi --mode json -p` process per turn.

Pi (https://pi.dev) is Tico's catch-all harness (docs/harnesses.md): it serves every provider
that has no CLI of its own (DeepSeek, Kimi, Llama, Mistral, ...) through OpenRouter, so a bot
can move off a subscription window without a new host per vendor. Swap the provider or the
OpenRouter id in clients/openrouter.py; the catalog id stays.

    pi --mode json -p --provider openrouter --model deepseek/deepseek-v4.1-flash
       --thinking low --session <uuid> --session-dir <dir> --approve

Turn ids are the runner's uuid4. Pi has no MCP and no permission prompts; the bot uses the
hub CLI over bash. `steer` is refused. OPENROUTER_API_KEY is injected into the Pi process
from this bot's granted Credential; other runtimes still strip it.
"""

import json
import os
import subprocess
import tempfile
import threading
import uuid
from pathlib import Path

from clients.openrouter import MODELS as OPENROUTER_MODELS, model_id as openrouter_id

from .. import isolation
from .base import Host, HostError, is_limit

PROVIDER = "openrouter"
# Catalog id -> OpenRouter model id, shared with the server's decision model (clients/openrouter.py).
MODELS = OPENROUTER_MODELS
EFFORTS = {"low": "low", "medium": "low", "high": "high", "xhigh": "max", "max": "max"}
DEFAULT_EFFORT = "low"
STOP_TIMEOUT_S = 10
STDERR_TAIL = 2000
TOOLS = "read,bash,edit,write,grep,find,ls"


def effort_for(effort):
    return EFFORTS.get(str(effort or "").strip().lower(), DEFAULT_EFFORT)


def model_for(model):
    return openrouter_id(model)


def usage_tokens(usage):
    """(input, output, total) from a Pi/OpenRouter usage object."""
    u = usage if isinstance(usage, dict) else {}

    def count(*keys):
        for key in keys:
            try:
                value = int(u.get(key) or 0)
            except (TypeError, ValueError):
                value = 0
            if value:
                return value
        return 0

    inp = count("input", "input_tokens", "inputTokens", "prompt_tokens", "promptTokens")
    out = count("output", "output_tokens", "outputTokens", "completion_tokens", "completionTokens")
    total = count("total", "total_tokens", "totalTokens") or (inp + out)
    return inp, out, total


def usage_increment(usage):
    """{input, cached, output} for one assistant message's usage object. Pi keeps cache reads and writes
    apart from `input`; the meter counts them all as input, the reads also as cached."""
    u = usage if isinstance(usage, dict) else {}

    def count(*keys):
        for key in keys:
            try:
                value = int(u.get(key) or 0)
            except (TypeError, ValueError):
                value = 0
            if value:
                return value
        return 0
    read = count("cacheRead", "cache_read", "cachedTokens")
    inp = count("input", "input_tokens", "inputTokens", "prompt_tokens", "promptTokens") + read + count("cacheWrite", "cache_write")
    return {"input": inp, "cached": read,
            "output": count("output", "output_tokens", "outputTokens", "completion_tokens", "completionTokens")}


def openrouter_key(env, local=True):
    """Use the run's granted key; local diagnostics may also read the machine key."""
    env = env or {}
    for value in (env.get("OPENROUTER_API_KEY"), os.environ.get("OPENROUTER_API_KEY") if local else None):
        if str(value or "").strip():
            return str(value).strip()
    if not local:
        return ""
    root = Path(env.get("HUB_WORKSPACE") or "")
    path = root / "secrets" / "_shared.env"
    if not path.is_file():
        return ""
    try:
        for line in path.read_text().splitlines():
            if line.startswith("OPENROUTER_API_KEY="):
                return line.split("=", 1)[1].strip().strip("'\"")
    except OSError:
        return ""
    return ""


def session_dir(bot, settings):
    env = (settings or {}).get("env") or {}
    root = Path(env.get("HUB_WORKSPACE") or (settings or {}).get("cwd") or ".")
    path = root / "runtime" / "pi-sessions" / str(bot or "pi")
    isolation.mkdir(path, 0o777)     # the bot user's, when the supervisor makes it
    return path


class PiHost(Host):
    name = "pi"
    supports_steer = False

    def __init__(self, bot=None, cmd=("pi",), log=None, stderr_path=None, spawn=None):
        super().__init__(log=log)
        self.bot = bot
        self.cmd = list(cmd)
        self.stderr_path = stderr_path
        self._spawn = spawn or isolation.popen
        self._up = False
        self.proc = None
        self._reader = None
        self._threads = {}
        self._turn = None
        self._reply = {}
        self._interrupted = set()
        self._done_turns = set()
        self._last_error = ""

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

    def _record(self, thread_id, bot, settings, started, fork_from=None):
        self._threads[thread_id] = {"bot": bot, "settings": dict(settings), "started": started,
                                    "fork_from": fork_from}
        return thread_id

    def start_thread(self, bot, settings):
        return self._record(str(uuid.uuid4()), bot, settings, started=False)

    def resume_thread(self, bot, thread_id, settings):
        return self._record(thread_id, bot, settings, started=True)

    def fork_thread(self, bot, thread_id, settings):
        return self._record(str(uuid.uuid4()), bot, settings, started=False, fork_from=thread_id)

    def _argv(self, thread_id, t, effort):
        settings = t["settings"]
        argv = self.cmd + ["--mode", "json", "-p", "--provider", PROVIDER,
                           "--model", model_for(settings.get("model")),
                           "--thinking", effort_for(effort or settings.get("effort")),
                           "--tools", TOOLS, "--session-id", thread_id,
                           "--session-dir", str(session_dir(t.get("bot") or self.bot, settings)),
                           "--approve"]
        return argv

    @staticmethod
    def _env(settings):
        env = dict(settings.get("env") or os.environ)
        if not env.get("HOME") and os.environ.get("HOME"):
            env["HOME"] = os.environ["HOME"]
        key = openrouter_key(env, local="env" not in settings)
        if key:
            env["OPENROUTER_API_KEY"] = key
        return env

    def start_turn(self, thread_id, text, effort=None):
        with self._lock:
            if not self._up:
                raise HostError("pi host is not running")
            if self._turn:
                raise HostError("pi host already has a turn running")
            t = self._threads.get(thread_id)
            if t is None:
                raise HostError(f"unknown pi thread {thread_id}")
            turn = str(uuid.uuid4())
            stderr = tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace")
            try:
                proc = self._spawn(self._argv(thread_id, t, effort), stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=stderr, cwd=t["settings"]["cwd"],
                                   env=self._env(t["settings"]), text=True, bufsize=1)
            except OSError as e:
                stderr.close()
                raise HostError(f"could not start pi: {e}")
            self.proc = proc
            self._turn = (thread_id, turn)
            self._reply[turn] = ""
        self._log(f"pi[{self.bot}]: turn started pid {getattr(proc, 'pid', '?')}")
        self.emit("status", thread_id, None, state="active")
        threading.Thread(target=self._feed, args=(proc, text), daemon=True).start()
        self._reader = threading.Thread(target=self._read_loop, args=(proc, thread_id, turn, stderr),
                                        daemon=True)
        self._reader.start()
        return turn

    @staticmethod
    def _feed(proc, text):
        try:
            proc.stdin.write(text)
            proc.stdin.close()
        except (BrokenPipeError, ValueError, OSError):
            pass

    def _read_loop(self, proc, tid, turn, stderr):
        ended = False
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
                    if self._on_event(msg, tid, turn):
                        ended = True
                except Exception as e:
                    self._log(f"pi[{self.bot}]: event error {e}")
        except (ValueError, OSError):
            pass
        try:
            rc = proc.wait()
        except Exception:
            rc = -1
        self._finish(tid, turn, rc, self._stderr_tail(stderr), ended)

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
        return text.strip()[-STDERR_TAIL:]

    def _on_event(self, msg, tid, turn):
        if not isinstance(msg, dict):
            return False
        kind = msg.get("type")
        if kind == "session" and msg.get("id"):
            t = self._threads.get(tid)
            if t:
                t.update(started=True, fork_from=None)
        elif kind == "message_update":
            ev = msg.get("assistantMessageEvent") or {}
            if ev.get("type") == "text_delta" and ev.get("delta"):
                self.emit("delta", tid, turn, text=ev["delta"], delta_kind="text")
            usage = msg.get("usage")
            if usage:
                inp, out, total = usage_tokens(usage)
                if total:
                    self.emit("tokens", tid, turn, input=inp, output=out, total=total)
        elif kind == "message_end":
            done = msg.get("message")
            if isinstance(done, dict) and done.get("role") == "assistant" and isinstance(done.get("usage"), dict):
                # One assistant message ends once, so this is where its tokens are counted.
                inp, out, total = usage_tokens(done["usage"])
                self.emit("tokens", tid, turn, input=inp, output=out, total=total, usage=usage_increment(done["usage"]))
            text = self._assistant_text(msg.get("message"))
            if text:
                self._reply[turn] = text
                self.emit("message", tid, turn, text=text, final=False)
        elif kind == "turn_end":
            text = self._assistant_text(msg.get("message")) or self._reply.get(turn, "")
            if text:
                self._reply[turn] = text
        elif kind in ("agent_end", "error") and kind == "error":
            err = msg.get("error") or msg.get("message") or "pi error"
            if isinstance(err, dict):
                err = err.get("message") or str(err)
            self._reply[turn] = self._reply.get(turn, "")
            self._last_error = str(err)
        elif kind == "agent_end":
            return True
        elif kind in ("tool_execution_start", "tool_execution_end"):
            self.emit("tool", tid, turn, name=msg.get("toolName") or "",
                      tool=msg.get("toolName") or "", phase=kind)
        return False

    @staticmethod
    def _assistant_text(message):
        if isinstance(message, str) and message.strip():
            return message
        if not isinstance(message, dict):
            return ""
        if message.get("role") not in (None, "assistant"):
            return ""
        content = message.get("content")
        if isinstance(content, str):
            return content
        parts = []
        for block in content or []:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") in ("text", None) and block.get("text"):
                parts.append(block["text"])
        return "".join(parts)

    def _finish(self, tid, turn, rc, stderr, ended):
        with self._lock:
            if self._turn and self._turn[1] == turn:
                self._turn = None
                self.proc = None
            interrupted = turn in self._interrupted
        reply = self._reply.pop(turn, "")
        if turn in self._done_turns:
            return
        self._done_turns.add(turn)
        error = getattr(self, "_last_error", "")
        self._last_error = ""
        if interrupted:
            self.emit("turn_completed", tid, turn, status="interrupted")
        elif rc == 0 or (ended and not error):
            if reply:
                self.emit("message", tid, turn, text=reply, final=True)
            self.emit("turn_completed", tid, turn, status="completed")
        else:
            text = error or stderr or f"pi exited {rc}"
            self.emit("turn_failed", tid, turn, error=text, limit=is_limit(text))
        self.emit("status", tid, None, state="idle")

    def steer(self, thread_id, turn_id, text):
        raise HostError("Pi runtime cannot steer a running turn")

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
