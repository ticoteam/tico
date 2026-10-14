"""Model sign-in started from the browser (backend/model_login.py is the server half).

The runner starts the model's own login command as the runner's user, in the same home the
runtime's readiness check and turns use, and relays what that command prints: a link and a
one-time code, or a prompt for a code the owner pastes back. The credential the command produces
stays on this disk. Nothing here reads a credential file, and every line that leaves this machine
is scrubbed of anything token-shaped first. Success is not the command's word: readiness is
checked again, the same way the heartbeat checks it.
"""

import fcntl
import os
import pty
import re
import shutil
import signal
import struct
import subprocess
import termios
import threading
import time

from . import harness_tools, isolation, profiles
from .outage import log

# argv after the executable, and whether the command insists on a terminal. Claude Code's
# `auth login` (not `setup-token`) signs the CLI itself in and never prints the token; both wait
# for a pasted code after the link, and Claude Code prints nothing without a terminal.
# Read from the harness manifests (runner/harnesses/*.toml), keyed by host name.
COMMANDS = {m.host: (m.login["command"], m.login["terminal"])
            for m in harness_tools.load_all().values() if m.login}
LOGIN_ENV = {m.host: dict(m.env) for m in harness_tools.load_all().values() if m.login and m.env}
LIFETIME_S = 15 * 60
POLL_S = 2
KEEP_RAW = 60_000
MAX_LINES, MAX_LINE = 12, 200

OSC = re.compile(r"\x1b\](.*?)(?:\x07|\x1b\\)", re.S)
CURSOR_COLUMN = re.compile(r"\x1b\[\d*[GC]")     # a TUI spaces words with cursor moves
CSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
ESC = re.compile(r"\x1b[()#][0-9A-Za-z]|\x1b[@-Z\\-_=>78]")
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
URL = re.compile(r"https://[^\s\x1b\"'<>]+(?=\s)")
USER_CODE = re.compile(r"[A-Z0-9]{3,8}-[A-Z0-9]{3,8}")
PROMPT = re.compile(r"paste (?:the )?code|enter (?:the )?code|authorization code", re.I)
SECRETS = (
    re.compile(r"\beyJ[\w-]{6,}\.[\w-]{6,}(?:\.[\w-]*)?"),
    re.compile(r"\b(?:sk|pk|rk|ghp|gho|ghs|github_pat|xox[abprs]|AIza)[-_][A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)\b(?:access|refresh|id)?[_-]?token\b\s*[:=]\s*\S+"),
    re.compile(r"(?i)\b(?:secret|password|api[_-]?key)\b\s*[:=]\s*\S+"),
    re.compile(r"[A-Za-z0-9+/_\-=]{24,}"),
)


def redact(text):
    for pattern in SECRETS:
        text = pattern.sub("[redacted]", text)
    return text


def parse(raw, hide=""):
    """What the login command has shown so far: {url, code, lines, prompt}.

    Tolerant on purpose: ANSI styling, cursor-positioned words and OSC 8 hyperlinks are all
    reduced to text, and `lines` always carries the (scrubbed) output so a person can read
    it when a pattern here stops matching. `hide` is a pasted code that a terminal may echo.
    """
    links = []
    for match in OSC.finditer(raw):
        fields = match.group(1).split(";", 2)
        if fields[0] == "8" and len(fields) == 3 and fields[2].startswith("https://"):
            links.append(fields[2])
    text = OSC.sub("", raw)
    text = CURSOR_COLUMN.sub(" ", text)
    text = CSI.sub("", text)
    text = ESC.sub("", text)
    text = CONTROL.sub("", text)
    pieces = re.split(r"\r\n|\n|\r", text)
    complete = pieces[:-1]                   # the last piece may still be arriving
    tail = pieces[-1]
    lines = [" ".join(piece.split()) for piece in complete + [tail]]
    url = links[0] if links else ""
    if not url:
        found = URL.search("\n".join(complete) + "\n")
        url = found.group(0).rstrip(".,);") if found else ""
    code = ""
    for line in (" ".join(piece.split()) for piece in complete):
        if USER_CODE.fullmatch(line) or ("code" in line.lower() and USER_CODE.search(line)):
            code = (USER_CODE.fullmatch(line) or USER_CODE.search(line)).group(0)
            break
    shown = []
    for line in lines:
        if hide and hide[:12] in line:
            continue
        line = re.sub(r"https?://\S*", "[link]", line)
        if len(re.findall(r"[A-Za-z0-9]", line)) < 3 or (" " not in line and len(line) > 40):
            continue                          # spinners, and pieces of a wrapped link
        line = redact(line)[:MAX_LINE]
        if not shown or shown[-1] != line:
            shown.append(line)
    return {"url": url, "code": code, "lines": shown[-MAX_LINES:],
            "prompt": any(PROMPT.search(line) for line in lines)}


class Session:
    """One login command, from start to a verdict."""

    def __init__(self, manager, work):
        self.manager = manager
        self.id, self.runtime, self.profile = work["id"], work["runtime"], work.get("profile") or ""
        self.lock = threading.Lock()
        self.cancelled = threading.Event()
        self.state, self.message = "starting", ""
        self.view = {"url": "", "code": "", "lines": []}
        self.raw = ""
        self.pending = None               # a pasted code waiting to be typed
        self.typed = None                 # the code once typed, so the terminal's echo can be hidden
        self.taken = False                # the server should drop its copy of the code
        self.dirty = True
        self.finished = False
        self.proc = None
        self.thread = threading.Thread(target=self.run, daemon=True)

    @property
    def live(self):
        return not self.finished

    def key(self):
        return (self.runtime, self.profile)

    def submit(self, code):
        with self.lock:
            if self.pending is None and self.typed is None:
                self.pending = code

    def stop(self):
        self.cancelled.set()

    def change(self, **fields):
        with self.lock:
            for name, value in fields.items():
                if getattr(self, name) != value:
                    setattr(self, name, value)
                    self.dirty = True

    def report(self):
        """The body to send when something changed since the last report, else None."""
        with self.lock:
            if not self.dirty:
                return None
            self.dirty = False
            body = {"state": self.state, "message": self.message, **self.view}
            if self.taken:
                body["code_taken"] = True
            return body

    def again(self):
        with self.lock:
            self.dirty = True

    # ------------------------------------------------------------------ the command
    def environment(self):
        profile = self.manager.profile(self.profile)
        env = profile.environment(self.runtime) if profile else dict(os.environ)
        if self.runtime == "musecode":
            # Bot turns read Muse's login from Tico's own Muse folder, never the operator's (runner/service.py).
            env["XDG_CONFIG_HOME"] = str(self.manager.runner.muse_config_home(profile))
        # The person opens the link on their own screen, not on this machine.
        env.update({"BROWSER": "true", "NO_COLOR": "1", **LOGIN_ENV.get(self.runtime, {})})
        return env

    def spawn(self, executable):
        argv, terminal = COMMANDS[self.runtime]
        env = self.environment()
        if not terminal:
            self.proc = isolation.popen([executable, *argv], env=env, stdin=subprocess.DEVNULL,
                                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                         start_new_session=True)
            return self.proc.stdout.fileno(), None
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 500, 0, 0))
        attributes = termios.tcgetattr(slave)
        attributes[3] &= ~termios.ECHO       # a pasted code is not echoed back into the output
        termios.tcsetattr(slave, termios.TCSANOW, attributes)
        try:
            self.proc = isolation.popen([executable, *argv], env=env, stdin=slave, stdout=slave,
                                         stderr=slave, start_new_session=True, close_fds=True)
        finally:
            os.close(slave)
        return master, master

    def read(self, fd):
        while True:
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                return
            if not chunk:
                return
            with self.lock:
                self.raw = (self.raw + chunk.decode("utf-8", "replace"))[-KEEP_RAW:]
                raw, hide = self.raw, self.typed or ""
            shown = parse(raw, hide)
            self.change(view={"url": shown["url"], "code": shown["code"], "lines": shown["lines"]})
            if self.state == "starting" and (shown["url"] or shown["code"]):
                self.change(state="waiting")

    def kill(self):
        if not self.proc or self.proc.poll() is not None:
            return
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(self.proc.pid, sig)
            except (OSError, ProcessLookupError):
                return
            try:
                self.proc.wait(timeout=2)
                return
            except subprocess.TimeoutExpired:
                continue

    def enter(self, master):
        with self.lock:
            code, self.pending = self.pending, None
        if code is None:
            return
        try:
            os.write(master, (code + "\r").encode())
        except OSError:
            return
        with self.lock:
            self.typed, self.taken, self.dirty = code, True, True

    def run(self):
        master = None
        try:
            executable = shutil.which(harness_tools.executable_for(self.runtime))
            if not executable:
                return self.finish("failed", "The runtime is not installed on this computer")
            try:
                fd, master = self.spawn(executable)
            except OSError as exc:
                return self.finish("failed", "The sign-in command could not start (" + type(exc).__name__ + ")")
            reader = threading.Thread(target=self.read, args=(fd,), daemon=True)
            reader.start()
            deadline = time.monotonic() + self.manager.lifetime
            while self.proc.poll() is None:
                if self.cancelled.is_set():
                    self.kill()
                    return self.finish("cancelled", "Cancelled")
                if time.monotonic() > deadline:
                    self.kill()
                    return self.finish("failed", "The sign-in was not finished in time")
                if master is not None and self.pending is not None:
                    self.enter(master)
                self.cancelled.wait(0.1)
            reader.join(timeout=3)
            if self.cancelled.is_set():
                return self.finish("cancelled", "Cancelled")
            if self.proc.returncode != 0:
                return self.finish("failed", "The sign-in command stopped without signing in")
            ready = self.manager.readiness(self.runtime, self.profile)
            if ready.get("authenticated") == "ready":
                return self.finish("signed_in", ready.get("detail") or "Signed in")
            self.finish("failed", "The command finished but " + self.runtime + " is still not signed in")
        except Exception as exc:                     # a broken login must not take the runner with it
            self.kill()
            self.finish("failed", "The sign-in stopped unexpectedly (" + type(exc).__name__ + ")")
        finally:
            if master is not None:
                try:
                    os.close(master)
                except OSError:
                    pass
            with self.lock:
                self.typed = self.pending = None
                self.raw = ""

    def finish(self, state, message):
        with self.lock:
            self.state, self.message, self.finished, self.dirty = state, message[:300], True, True
            self.view = {"url": "", "code": "", "lines": self.view["lines"]}
        log(f"Tico runner: {self.runtime} sign-in {state}")
        if state == "signed_in":
            self.manager.runner.clear_rejection(self.runtime, self.profile or
                (self.manager.profile("").name if self.manager.profile("") else ""))


class Logins:
    """The browser sign-ins this runner is running, kept in step with the server's list."""

    def __init__(self, runner, lifetime=None):
        self.runner = runner
        self.lifetime = LIFETIME_S if lifetime is None else lifetime
        self.sessions = {}
        self.polled = 0.0

    def profile(self, name):
        """The named profile, else the registration's default, else None (the operator's own logins)."""
        config = self.runner.config
        if name:
            entry = (config.get("profiles") or {}).get(name)
            return profiles.Profile(name, entry["dir"], entry.get("share_operator")) \
                if isinstance(entry, dict) and entry.get("dir") else None
        return profiles.select(config)

    def readiness(self, runtime, name):
        return self.runner.runtime_readiness(runtime, [], self.profile(name))

    def begin(self, work):
        session = Session(self, work)
        if session.runtime not in COMMANDS:
            session.finish("failed", "Browser sign-in is not available for " + session.runtime)
        elif any(other.live and other.key() == session.key() for other in self.sessions.values()):
            session.finish("failed", "Another sign-in is already running for " + session.runtime)
        elif (self.profile(session.profile) and self.profile(session.profile).share_operator
              and session.runtime != "musecode"):         # Muse bots never use the operator's Muse login
            session.finish("failed", f"Profile {session.profile} shares the operator's own logins; sign in normally")
        else:
            try:
                if session.profile and not self.profile(session.profile):
                    self.runner.add_profile(session.profile)
                session.thread.start()
            except (OSError, ValueError) as exc:
                session.finish("failed", "Could not create subscription profile: " + str(exc))
        self.sessions[session.id] = session

    def poll(self, force=False):
        """Ask the server what to run, then say what happened. Never raises for a bad answer."""
        if not force and time.monotonic() - self.polled < POLL_S:
            return
        self.polled = time.monotonic()
        reply = self.runner.client.get("runner-logins")
        wanted = {row["id"]: row for row in (reply or {}).get("logins", []) if isinstance(row, dict) and row.get("id")}
        for lid, session in list(self.sessions.items()):
            if session.live and lid not in wanted:
                session.stop()                # cancelled or expired on the server
        for lid, row in wanted.items():
            session = self.sessions.get(lid)
            if session is None:
                self.begin(row)
            elif row.get("code"):
                session.submit(row["code"])
        self.flush()

    def flush(self):
        for lid, session in list(self.sessions.items()):
            body = session.report()
            if body is not None and body["state"] == "cancelled":
                body = None                   # the server ended it; there is nothing to tell it
            if body is not None:
                try:
                    self.runner.client.post(f"runner-logins/{lid}/report", body)
                except Exception:
                    session.again()           # said again on the next poll
                    continue
                with session.lock:
                    session.taken = session.taken and not body.get("code_taken")
                if body["state"] == "signed_in":
                    self.runner._profile_report_cache = None
                    self.runner.last_heartbeat = 0     # let readiness show it now
            if session.finished and not session.dirty:
                del self.sessions[lid]

    def stop(self):
        for session in self.sessions.values():
            session.stop()
