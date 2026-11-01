"""A stand-in Tico server and GitHub for test_isolation_docker.py, run in a container beside the runner's.

The hub answers the few reads a runner needs to take a turn and accepts everything else with `{}`, as the
runner's own tests' recording server does. Work is files: the test puts attempts in /work/queue (taken in name
order, one per claim) and the bots' assignments in /work/assignments.json; a finished turn's completion lands in
/work/done/<attempt>.json. GitHub is `git http-backend` over /srv/git, with no sign-in.
"""
import http.server
import json
import os
import subprocess
import threading
from pathlib import Path

WORK = Path("/work")
GIT_ROOT = "/srv/git"


def read_json(path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def write_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value))
    temporary.replace(path)


class Hub(http.server.BaseHTTPRequestHandler):
    lock = threading.Lock()

    def log_message(self, *args):
        pass

    def answer(self, status, value):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.route(None)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"null")
        except ValueError:
            body = None
        self.route(body)

    do_PATCH = do_PUT = do_POST

    def route(self, body):
        path = self.path.split("?", 1)[0]
        name = path.removeprefix("/api/v2/").strip("/")
        if name == "runners/me/events":
            return self.answer(404, {"error": {"code": "not_found", "detail": "no event stream here"}})
        if name == "runners/assignments":
            return self.answer(200, read_json(WORK / "assignments.json", []))
        if name == "credential-runtime":
            return self.answer(200, {"credentials": read_json(WORK / "credentials.json", [])})
        if name == "jobs/claim":
            with self.lock:
                queue = sorted((WORK / "queue").glob("*.json"))
                if not queue:
                    return self.answer(200, {})
                attempt = read_json(queue[0], {})
                queue[0].replace(WORK / "claimed" / queue[0].name)
            return self.answer(200, {"attempt": attempt})
        parts = name.split("/")
        if len(parts) == 3 and parts[0] == "attempts":
            aid, verb = parts[1], parts[2]
            if verb == "renew":
                return self.answer(200, {"lease_seconds": 600})
            if verb == "events":
                events = (body or {}).get("events") or []
                return self.answer(200, {"ack_seq": events[-1]["seq"]} if events else {})
            if verb == "complete":
                write_json(WORK / "done" / f"{aid}.json", body)
                return self.answer(200, {})
        return self.answer(200, {})


class GitHub(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        path, _, query = self.path.partition("?")
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        env = {"GIT_PROJECT_ROOT": GIT_ROOT, "GIT_HTTP_EXPORT_ALL": "1", "REQUEST_METHOD": self.command,
               "PATH_INFO": path, "QUERY_STRING": query, "CONTENT_TYPE": self.headers.get("Content-Type", ""),
               "CONTENT_LENGTH": str(len(body)), "REMOTE_USER": "acme", "PATH": os.environ["PATH"]}
        out = subprocess.run(["git", "-c", "safe.directory=*", "http-backend"], input=body, env=env,
                             capture_output=True).stdout
        head, _, payload = out.partition(b"\r\n\r\n")
        status = 200
        headers = []
        for line in head.decode().splitlines():
            key, _, value = line.partition(": ")
            if key.lower() == "status":
                status = int(value.split()[0])
            else:
                headers.append((key, value))
        self.send_response(status)
        for key, value in headers:
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_POST = do_GET


def main():
    for folder in ("queue", "claimed", "done"):
        (WORK / folder).mkdir(parents=True, exist_ok=True)
    github = http.server.ThreadingHTTPServer(("0.0.0.0", 8766), GitHub)
    threading.Thread(target=github.serve_forever, daemon=True).start()
    http.server.ThreadingHTTPServer(("0.0.0.0", 8765), Hub).serve_forever()


main()
