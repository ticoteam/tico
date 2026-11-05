"""Disposable HTTP bridge to the production transcript validator and append function.

Used by the opt-in Rust end-to-end regression on RunsOn. No models, live data or background jobs.
"""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pydantic import ValidationError
from backend.live_meetings import ChunkBatch, append_chunks
from backend.meetings_schema import LIVE_MEETINGS_SCHEMA
from backend.store import Problem


db = sqlite3.connect(":memory:")
db.row_factory = sqlite3.Row
db.executescript(LIVE_MEETINGS_SCHEMA)
db.execute("INSERT INTO live_meetings(id,owner_actor,title,state,window_ms,created_at,updated_at,started_at) "
           "VALUES('live-fixture','human:test','Synthetic meeting','live',30000,'now','now','now')")
db.commit()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, status, value):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        assert self.path == "/api/v2/live-meetings/live-fixture"
        self.reply(200, {**dict(db.execute("SELECT * FROM live_meetings").fetchone()),
                         "chunks": [dict(row) for row in db.execute(
                             "SELECT * FROM live_meeting_chunks ORDER BY seq")]})

    def do_POST(self):
        assert self.path == "/api/v2/live-meetings/live-fixture/chunks"
        try:
            body = ChunkBatch.model_validate_json(self.rfile.read(int(self.headers["Content-Length"])))
            with db:
                row = dict(db.execute("SELECT * FROM live_meetings").fetchone())
                accepted, _ = append_chunks(db, "live-fixture", row, "human:test",
                                            [chunk.model_dump() for chunk in body.chunks])
            self.reply(200, {"accepted": accepted})
        except Problem as error:
            self.reply(error.status, {"error": {"code": error.code, "detail": error.detail}})
        except ValidationError:
            self.reply(422, {"error": {"code": "validation"}})


if __name__ == "__main__":
    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        print(f"http://127.0.0.1:{server.server_port}", flush=True)
        server.serve_forever()
