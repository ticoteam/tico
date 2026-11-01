"""Embedded runner helper. Run as the ordinary bot, never as a leased turn user.

Only access credentials cross stdout. The supervisor serializes calls for each login,
and never forwards provider stderr. No model request or repository command runs here.
"""
import base64
import ctypes
import functools
import signal
import json
import os
from pathlib import Path
import stat
import selectors
import subprocess
import sys
import time

LIMIT = 1024 * 1024


def read_json(path):
    """Pin every ancestor; refuse links, devices, aliases and oversized credentials."""
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("invalid login path")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in path.parts[1:-1]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        leaf = os.open(path.name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=fd)
        with os.fdopen(leaf, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
                    info.st_uid != os.geteuid() or info.st_mode & 0o077):
                raise ValueError("login is not an owned plain file")
            raw = stream.read(LIMIT + 1)
            if len(raw) > LIMIT:
                raise ValueError("login is too large")
            return json.loads(raw)
    finally:
        os.close(fd)


def claims(token):
    body = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))


def die_with_parent(expected=None):
    # The helper is single threaded. Kill a vendor refresher if its broker helper dies;
    # it must never keep rotating credentials after the supervisor loses its lock.
    if sys.platform == "linux":
        parent = expected if expected is not None else os.getppid()
        if ctypes.CDLL(None, use_errno=True).prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
            raise OSError("cannot bind refresh lifetime")
        if os.getppid() != parent:
            os.kill(os.getpid(), signal.SIGKILL)


def codex_refresh():
    """Use Codex's managed refresh path, in the original login home only."""
    env = {k: v for k, v in os.environ.items() if k not in (
        "OPENAI_API_KEY", "CODEX_API_KEY", "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")}
    messages = [
        {"id": 1, "method": "initialize", "params": {"clientInfo": {"name": "tico-auth", "version": "1"}}},
        {"method": "initialized", "params": {}},
        {"id": 2, "method": "account/read", "params": {"refreshToken": True}},
    ]
    process = subprocess.Popen(["codex", "app-server"], env=env, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, preexec_fn=functools.partial(die_with_parent, os.getpid()))
    try:
        process.stdin.write("".join(json.dumps(m) + "\n" for m in messages))
        process.stdin.flush()
        until, buffer = time.monotonic() + 7, b""
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while time.monotonic() < until:
                if not selector.select(max(0, until - time.monotonic())):
                    break
                raw = os.read(process.stdout.fileno(), 65536)
                if not raw:
                    break
                buffer += raw
                if len(buffer) > LIMIT:
                    raise ValueError("oversized refresh reply")
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    reply = json.loads(line)
                    if reply.get("id") == 2:
                        if "result" not in reply:
                            raise ValueError("managed refresh failed")
                        return
        raise ValueError("managed refresh timed out")
    finally:
        process.kill()
        process.wait()


def grant(kind, minimum, previous=None, previous_access=None):
    if kind == "codex-external":
        path = Path(os.environ.get("CODEX_HOME") or str(Path.home() / ".codex")) / "auth.json"
        auth = read_json(path)
        if auth.get("OPENAI_API_KEY") and not previous:
            return {"apiKey": auth["OPENAI_API_KEY"]}
        tokens = auth.get("tokens") or {}
        account = tokens.get("account_id")
        if previous and previous != account:
            raise ValueError("login account changed")
        token = tokens.get("access_token") or ""
        if previous_access == token or claims(token).get("exp", 0) <= time.time() + minimum:
            codex_refresh()
            auth = read_json(path)
            tokens = auth.get("tokens") or {}
            if tokens.get("account_id") != account:
                raise ValueError("login account changed during refresh")
            token = tokens.get("access_token") or ""
        if not account or claims(token).get("exp", 0) <= time.time() + minimum:
            raise ValueError("access credential lifetime is too short")
        return {"accessToken": token, "chatgptAccountId": account}
    if kind == "claude-oauth":
        folder = Path(os.environ.get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude"))
        auth = read_json(folder / ".credentials.json")["claudeAiOauth"]
        if auth.get("expiresAt", 0) / 1000 <= time.time() + minimum:
            env = dict(os.environ)
            env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
            env.pop("ANTHROPIC_API_KEY", None)
            env["CLAUDE_CODE_OAUTH_REFRESH_TOKEN"] = auth["refreshToken"]
            env["CLAUDE_CODE_OAUTH_SCOPES"] = " ".join(auth["scopes"])
            subprocess.run(["claude", "auth", "login"], env=env, stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30, check=True, preexec_fn=functools.partial(die_with_parent, os.getpid()))
            auth = read_json(folder / ".credentials.json")["claudeAiOauth"]
        if auth.get("expiresAt", 0) / 1000 <= time.time() + minimum:
            raise ValueError("access credential lifetime is too short")
        return {"accessToken": auth["accessToken"], "expiresAt": auth["expiresAt"] / 1000}
    raise ValueError("unsupported broker")


def main():
    try:
        request = json.load(sys.stdin)
        die_with_parent(request.get("parent"))
        result = grant(request["kind"], request["minimum"], request.get("previous"), request.get("previousAccess"))
        print(json.dumps(result))
    except Exception:  # noqa: BLE001 - This process boundary must suppress credential-bearing tracebacks.
        # Neither token bytes, filesystem paths nor the vendor's error text may reach a task/capture.
        sys.exit(1)


if __name__ == "__main__":
    main()
