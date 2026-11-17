"""Runs inside the runner image as the supervisor (ticorun with ambient capabilities, via the entrypoint), for test_isolation_docker.py.

It plays the runner: a credential socket for one attempt (as runner-rs/src/gitcred.rs serves it), a fake GitHub that only accepts that bot's
token, and a "turn" process dropped to the bot user exactly as runner_support/isolation.py does it. It prints
one JSON object of what the turn could and could not do.

The turn's git credential helper is this file too, run as the bot: `python /turn_side.py git-credential SOCKET` (git_credential).
It is a small client of the socket rather than tico-runner git-credential, because the Rust helper refuses http:// remotes
(runner-rs/src/gitcred.rs) and the fake GitHub is plain http.
"""

import base64
import http.server
import json
import os
import shlex
import socket
import socketserver
import subprocess
import sys
import threading
from pathlib import Path

sys.path.insert(0, "/opt/tico")
from runner_support import isolation  # noqa: E402

HOME = "/home/runner"
BOT_TOKEN = "ghs_fake_alpha_token"


class Hub:
    def __init__(self):
        self.bodies = []

    def post(self, path, body):
        assert path == "github/token"
        assert body in ({"bot": "alpha"}, {"bot": "alpha", "purpose": "git"})
        self.bodies.append(dict(body))
        return {"configured": True, "token": BOT_TOKEN, "repository": "acme/alpha"}


class FakeGitHub(http.server.BaseHTTPRequestHandler):
    seen = []

    def do_GET(self):
        self.serve()

    do_POST = do_GET

    def log_message(self, *args):
        pass

    def serve(self):
        auth = self.headers.get("Authorization", "")
        self.seen.append(auth)
        if auth != "Basic " + base64.b64encode(f"x-access-token:{BOT_TOKEN}".encode()).decode():
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="fake"')
            self.end_headers()
            return
        path, _, query = self.path.partition("?")
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        env = {
            "GIT_PROJECT_ROOT": "/tmp/remote",
            "GIT_HTTP_EXPORT_ALL": "1",
            "REQUEST_METHOD": self.command,
            "PATH_INFO": path,
            "QUERY_STRING": query,
            "CONTENT_TYPE": self.headers.get("Content-Type", ""),
            "CONTENT_LENGTH": str(len(body)),
            "REMOTE_USER": "x-access-token",
            "PATH": os.environ["PATH"],
        }
        out = subprocess.run(["git", "http-backend"], input=body, env=env, capture_output=True).stdout
        head, _, payload = out.partition(b"\r\n\r\n")
        self.send_response(200)
        for line in head.decode().splitlines():
            name, _, value = line.partition(": ")
            if name.lower() == "status":
                continue
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def git_credential(socket_path):
    """git's credential helper for the turn. On `get`, git sends key=value lines on stdin; this asks the credential
    socket for the bot's git token (the attempt token in HUB_TOKEN, purpose git) and prints it for git."""
    wanted = dict(line.split("=", 1) for line in sys.stdin.read().splitlines() if "=" in line)
    if wanted.get("host") != os.environ["TICO_GITHUB_HOST"]:
        return
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as asked:
        asked.connect(socket_path)
        asked.sendall(json.dumps({"token": os.environ["HUB_TOKEN"], "purpose": "git"}).encode() + b"\n")
        reply = json.loads(asked.makefile().readline())
    if "token" not in reply:
        print(reply.get("error", "refused"), file=sys.stderr)
        print("quit=true")
        return
    print(f"username=x-access-token\npassword={reply['token']}")


def credential_socket(hub):
    """The supervisor's socket for attempt-1 of bot alpha: a git token minted with the hub for that bot alone,
    readable by the bot user only."""

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            asked = json.loads(self.rfile.readline(4096))
            if asked.get("token") != "attempt-1":
                reply = {"error": "unknown attempt"}
            else:
                body = {"bot": "alpha"} | ({"purpose": "git"} if asked.get("purpose") == "git" else {})
                reply = {"token": hub.post("github/token", body)["token"]}
            self.wfile.write(json.dumps(reply).encode() + b"\n")

    path = os.environ.get("TICO_RUNNER_CRED_SOCKET") or "/run/tico-runner/git-credential.sock"
    Path(path).parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    Path(path).unlink(missing_ok=True)
    server = socketserver.UnixStreamServer(path, Handler)
    os.chmod(path, 0o600)
    isolation.chown(path)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return path


def main():
    subprocess.run(["git", "init", "-q", "--bare", "/tmp/remote/alpha.git"], check=True)
    subprocess.run(["git", "-C", "/tmp/remote/alpha.git", "config", "http.receivepack", "true"], check=True)
    github = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeGitHub)
    threading.Thread(target=github.serve_forever, daemon=True).start()
    host = f"127.0.0.1:{github.server_address[1]}"

    git_hub = Hub()
    assert isolation.enabled(), "isolation is not on: the entrypoint should have exported TICO_RUNNER_BOT_UID"
    socket_path = credential_socket(git_hub)
    env = {
        "HOME": HOME,
        "PATH": os.environ["PATH"] + ":/home/runner/tools/bin",
        "HUB_TOKEN": "attempt-1",
        "TICO_GITHUB_HOST": host,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.com",
    }
    # git matches the helper by the remote's host; the fake GitHub is not github.com. The helper is this file, run as
    # the bot (git_credential); the first entry clears any helper inherited for that host, as gitcred.rs does.
    key = f"credential.http://{host}.helper"
    helper = (
        f'!f() {{ [ "$1" = get ] || exit 0; exec {shlex.quote(sys.executable)} {shlex.quote(__file__)} '
        f"git-credential {shlex.quote(socket_path)}; }}; f"
    )
    env.update(
        GIT_CONFIG_COUNT="2",
        GIT_CONFIG_KEY_0=key,
        GIT_CONFIG_VALUE_0="",
        GIT_CONFIG_KEY_1=key,
        GIT_CONFIG_VALUE_1=helper,
    )
    turn = isolation.run(
        ["sh", "/turn.sh", f"http://{host}/alpha.git"], env=env, cwd=HOME + "/workspace", capture_output=True, text=True
    )
    print(
        json.dumps(
            {
                "turn": turn.stdout.splitlines(),
                "stderr": turn.stderr[-800:],
                "token_bodies": {"git_helper": git_hub.bodies},
                "registration": subprocess.run(
                    ["stat", "-c", "%U %a", HOME + "/runner.json"], capture_output=True, text=True
                ).stdout.strip(),
                "pushed": subprocess.run(
                    ["git", "-C", "/tmp/remote/alpha.git", "log", "--format=%s", "main"], capture_output=True, text=True
                ).stdout.strip(),
                "auth_seen": sorted(set(FakeGitHub.seen)),
            }
        )
    )


if __name__ == "__main__":
    if sys.argv[1:2] == ["git-credential"]:
        git_credential(sys.argv[2])
    else:
        main()
