"""A stand-in for the supervisor's credential socket (runner-rs/src/gitcred.rs) for the client calls in
runner_support/credential_socket.py: one Unix socket that answers each JSON line with `answer(asked)` and records
what it was asked."""

import json
import shutil
import socketserver
import tempfile
import threading
from pathlib import Path


class Supervisor:
    def __init__(self, answer):
        self.answer, self.asked = answer, []
        self.directory = tempfile.mkdtemp(dir="/tmp")  # short: a Unix socket path has a length limit
        self.path = str(Path(self.directory) / "cred.sock")
        outer = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                asked = json.loads(self.rfile.readline(4096))
                outer.asked.append(asked)
                self.wfile.write(json.dumps(outer.answer(asked)).encode() + b"\n")

        class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
            daemon_threads = True

        self.server = Server(self.path, Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.directory, ignore_errors=True)
