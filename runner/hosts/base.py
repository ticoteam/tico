"""The runtime host interface the runner talks to.

A *host* owns one runtime (Codex, Claude, Gemini, Grok Build, or the fake used by tests) and hides its wire
protocol behind seven calls and one event queue. The runner never speaks JSON-RPC; it starts
threads, starts turns, and drains events.

    host.start()                                   # bring the runtime up
    tid = host.start_thread("coo", settings)       # a persistent thread for one bot
    tid = host.resume_thread("coo", tid, settings) # after a restart
    turn = host.start_turn(tid, "text")            # one turn on that thread
    host.steer(tid, turn, "text")                  # mid-turn input
    host.interrupt(tid, turn)
    for ev in host.drain(): ...                    # events, oldest first

`settings` is a plain dict so a host can ignore what it does not understand:

    cwd     str   the bot's repo (required)
    model   str   runtime model id, None for the runtime's default
    effort  str   reasoning effort, None for the default
    env     dict  the environment the turn's shell commands get (`Runner.environment`: the
                  bot's secrets plus HUB_API_URL / HUB_TOKEN / HUB_BOT / HUB_EMPLOYEE / HUB_DIR /
                  HUB_WORKSPACE)
    slug    str   the bot, for logging

Events are dicts. Every event carries `kind`, `thread_id` and `turn_id` (either may be None):

    delta          text          a streamed fragment of the agent's reply
    message        text, final   a complete agent message; `final` is the turn's answer
    turn_completed status        the turn ended (status "completed" | "interrupted")
    turn_failed    error, limit  the turn failed; `limit` is True for a usage-limit error
    status         state         "idle" | "active" for the thread
    tokens         input, output, total; `usage` {input, cached, output} is this event's increment
                   (input counts cached tokens too), emitted once per token (runner/usage.py sums it)
    rate_limits    used_percent, window_minutes, resets_at (ISO-8601 UTC)
    error          error         host-level trouble; `host_restart` True when the process was
                                 restarted and the runner must re-resume its threads
"""

import queue
import re
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HUB_MCP_SCRIPT = ROOT / "clients" / "hubmcp.py"
HUB_MCP_ENV = ("HUB_API_URL", "HUB_TOKEN", "HUB_BOT", "HUB_EMPLOYEE", "HUB_DIR")


# One rule, kept here so every host classifies a usage limit the same way. A provider with no
# capacity right now (Gemini "UNAVAILABLE (code 503): No capacity available", Codex "Selected model is at
# capacity", "overloaded",
# RESOURCE_EXHAUSTED) is the same thing for the cloud: nothing ran, try again later, not a
# person's review (a turn could sit "uncertain" on one of these).
LIMIT_RE = re.compile(r"hit your usage limit|usage limit|usage_limit_reached|rate limit(?:ed)?"
                      r"|no capacity available|model is at capacity|\boverloaded\b|resource_exhausted|\b(?:code|status|http) (?:429|503)\b", re.I)
# A sign-in that could not renew itself. The turn was refused before it began, so it is
# retryable rather than reviewable (one lost renewal could leave a bot
# sitting "uncertain" and claiming nothing for hours).
# Codex's "unexpected status 401 Unauthorized" after its reconnect loop is the same lost sign-in
# (a mention check parked "uncertain" with nothing run).
AUTH_RETRY_RE = re.compile(r"failed to refresh oauth token|another claude code process is refreshing"
                           r"|exited mid-refresh"
                           r"|oauth_refresh_lock|lock_busy|lock_timeout"
                           r"|unexpected status 401 unauthorized", re.I)

KINDS = ("delta", "message", "turn_completed", "turn_failed", "status", "tokens",
         "rate_limits", "error", "tool", "diagnostic", "goal")


class HostError(RuntimeError):
    """The runtime refused a call, or died while answering one."""


class SteerRefused(HostError):
    """`steer` wrote nothing because the turn is over or already ending.

    Unlike a bare HostError, the input certainly did not reach the runtime, so the runner may
    hand it to a new turn instead of treating its delivery as uncertain.
    """


class Host:
    """One runtime. Subclasses implement everything below `start`."""

    name = "base"
    supports_steer = False

    def __init__(self, log=None):
        self._events = queue.Queue()
        self._lock = threading.RLock()
        self._log = log or (lambda msg: None)

    # ------------------------------------------------------------------ events
    def emit(self, kind, thread_id=None, turn_id=None, **fields):
        """Put one event on the queue. Hosts call this; the runner drains it."""
        if kind not in KINDS:
            raise ValueError(f"unknown event kind {kind!r}")
        ev = {"kind": kind, "thread_id": thread_id, "turn_id": turn_id}
        ev.update(fields)
        self._events.put(ev)
        return ev

    def drain(self, limit=500):
        """Every event waiting, oldest first, at most `limit` of them."""
        out = []
        while len(out) < limit:
            try:
                out.append(self._events.get_nowait())
            except queue.Empty:
                break
        return out

    # ------------------------------------------------------------------ process
    def start(self):
        raise NotImplementedError

    def stop(self):
        raise NotImplementedError

    def alive(self):
        raise NotImplementedError

    def supervise(self):
        """Called every runner tick. True when the runtime was restarted and the runner must
        re-resume its threads; the host also emits an `error` event with `host_restart` True."""
        return False

    # ------------------------------------------------------------------ threads
    def start_thread(self, bot, settings):
        """A new persistent thread for `bot`. Returns the thread id."""
        raise NotImplementedError

    def resume_thread(self, bot, thread_id, settings):
        """Reattach to `thread_id`. Returns the thread id (usually the same one)."""
        raise NotImplementedError

    def fork_thread(self, bot, thread_id, settings):
        """A copy of `thread_id` up to its last finished turn. Returns the new thread id."""
        raise NotImplementedError

    # ------------------------------------------------------------------ turns
    def start_turn(self, thread_id, text, effort=None):
        """Begin a turn with `text` as the user input. Returns the turn id."""
        raise NotImplementedError

    def start_goal(self, thread_id, action, objective, effort=None):
        raise HostError("This harness does not support native goals")

    def start_command(self, thread_id, text, effort=None):
        return self.start_turn(thread_id, text, effort=effort)

    def poll_goal(self, thread_id):
        pass

    def session_id(self, thread_id):
        return thread_id

    def steer(self, thread_id, turn_id, text):
        """Add `text` to the turn that is already running."""
        raise NotImplementedError

    def interrupt(self, thread_id, turn_id):
        """Stop the running turn."""
        raise NotImplementedError


def is_limit(text):
    """True when this error text is a subscription usage limit rather than a failure."""
    return bool(LIMIT_RE.search(text or ""))


# A credential the provider refused outright: retrying cannot help until the key or sign-in changes.
# It wins over AUTH_RETRY_RE, whose 401 pattern is a lost token renewal, not a wrong key.
AUTH_REJECTED_RE = re.compile(r"incorrect api key|invalid[ _-]?(?:x-)?api[ _-]?key|api key (?:is )?(?:invalid|revoked|expired)"
                              r"|not logged in|please run /login|invalid authentication credentials|authentication_error"
                              r"|oauth session expired and could not be refreshed", re.I)
_SECRETISH = re.compile(r"\b(?:sk|pk|xai|gsk|key|ghp|github_pat)[-_][A-Za-z0-9_-]{6,}|Bearer\s+\S+|[A-Za-z0-9_-]{32,}")


def is_auth_rejected(text):
    return bool(AUTH_REJECTED_RE.search(text or ""))


def rejection_reason(text):
    """One line of the provider's refusal for people to read; keys and tokens are never kept."""
    line = next((row.strip() for row in (text or "").splitlines() if is_auth_rejected(row)), "") or (text or "").strip()
    return _SECRETISH.sub("[redacted]", line)[:200]


def is_auth_retryable(text):
    """True when the turn never ran because the runtime could not renew its own sign-in.

    Claude Code's access token lasts about eight hours and is renewed lazily, by whichever
    process first finds it expired, serialised through a lock file. Four bots waking in the
    same second all race that renewal and the losers are refused outright. Nothing has run at
    that point -- no model call, no tools, no side effects -- so this is the same situation as
    a usage limit: try again in a minute, do not park it for a person to review.
    """
    return bool(AUTH_RETRY_RE.search(text or "")) and not is_auth_rejected(text)


def hub_mcp_server(env):
    """The hub as a stdio MCP server for one turn, or None outside a turn.

    `python clients/hubmcp.py` with the turn's own credential (`clients/hubtools.py` is the
    schema; `hub <name>` in a shell is the same tool). The runner's interpreter runs it, for
    the same reason `Runner.environment` puts that interpreter first on PATH. A warm harness's
    `tico-file:` token is read fresh on every call, so the env can be fixed at thread start.
    """
    env = env or {}
    if not env.get("HUB_API_URL") or not env.get("HUB_TOKEN"):
        return None
    return {"command": sys.executable, "args": [str(HUB_MCP_SCRIPT)],
            "env": {k: env[k] for k in HUB_MCP_ENV if env.get(k)}}


def settings(cwd, model=None, effort=None, env=None, slug=None, **extra):
    """A settings dict, so callers do not have to remember the key names."""
    s = {"cwd": str(cwd), "model": model, "effort": effort, "env": dict(env or {}), "slug": slug}
    s.update(extra)
    return s
