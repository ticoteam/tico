"""Opt-in Tico Recorder replay against a local Tico: capture → STT → private live meeting →
drafted action item sent to a bot as a task → finalize → send, plus a local-only import.

Uses synthetic tone buffers and a local STT/model fixture, never a microphone or live provider.
Build the recorder CLI and run with TICO_NATIVE_CLI=/absolute/path/to/tico-recorder-cli.
"""
import base64
import io
import json
import os
import socket
import struct
import subprocess
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import uvicorn

from backend.tests.test_api import api, get  # noqa: F401  (api is a fixture)

COMMITMENT = "I will update the launch plan"


@pytest.mark.skipif(not os.environ.get("TICO_NATIVE_CLI"), reason="Build the native CLI and set TICO_NATIVE_CLI")
def test_recorder_sends_one_action_item_to_a_bot_and_files_the_meeting(api, tmp_path):
    provider_calls = []

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            provider_calls.append(self.path)
            if self.path == "/audio/transcriptions":
                data = base64.b64decode(body["input_audio"]["data"])
                with wave.open(io.BytesIO(data), "rb") as audio:
                    assert audio.getframerate() == 16000 and audio.getnchannels() == 1
                    frames = audio.readframes(audio.getnframes())
                samples = struct.unpack("<" + "h" * (len(frames) // 2), frames)
                crossings = sum(a <= 0 < b for a, b in zip(samples, samples[1:]))
                value = {"text": COMMITMENT if crossings < 550 else "The launch is scheduled for Friday"}
            else:
                assert self.path == "/chat/completions"
                if "action items" in body["messages"][0]["content"]:
                    # One real item and one whose quote was never said: the recorder drops it.
                    content = json.dumps({"items": [{"title": "Update the launch plan", "quote": COMMITMENT},
                                                    {"title": "Hire a DJ", "quote": "We need a DJ"}]})
                else:
                    content = "You said you will update the launch plan (00:00)."
                value = {"choices": [{"message": {"content": content}}]}
            response = json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

    provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    threading.Thread(target=provider.serve_forever, daemon=True).start()
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(api.app, host="127.0.0.1", port=port, lifespan="off", log_level="error"))
    server_thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    server_thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        threading.Event().wait(.02)
    assert server.started
    try:
        environment = {**os.environ, "TICO_RECORDER_DATA_DIR": str(tmp_path / "library"),
                       "TICO_REPLAY_TEST_TOKEN": "ana-test"}
        result = subprocess.run(
            [os.environ["TICO_NATIVE_CLI"], "replay-test", "--tico-url", f"http://127.0.0.1:{port}",
             "--stt-url", f"http://127.0.0.1:{provider.server_port}", "--person", "ben", "--bot", "coo"],
            env=environment, capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stderr
        output = json.loads(result.stdout)
        assert output["synthetic"] is True and output["chunks"] == 2
        assert output["drafts"] == 1
        assert output["assist"] == "You said you will update the launch plan (00:00)."
        # Two transcriptions, one draft review, one private question: nothing else left the computer.
        assert sorted(provider_calls) == ["/audio/transcriptions"] * 3 + ["/chat/completions"] * 2

        # The live meeting opened private, with Ben and the bot let in.
        live = get(api, f"live-meetings/{output['live_id']}")
        assert live["visibility"] == "private"
        assert "coo" in json.dumps(live["bots"])

        # The drafted item is one task for the bot, private, carrying the words it came from.
        sent, typed = output["items"]
        assert sent["title"] == "Update the launch plan" and sent["quote"] == COMMITMENT
        assert sent["task_id"] and sent["task_url"].endswith(f"/#/task/{sent['task_id']}")
        with api.app.state.store.read() as c:
            tasks = c.execute("SELECT id,owner,body FROM tasks WHERE title='Update the launch plan'").fetchall()
        assert [(row["id"], row["owner"]) for row in tasks] == [(sent["task_id"], "bot:coo")]
        body = tasks[0]["body"]
        assert f"“{COMMITMENT}”" in body and "Transcript around it:" in body
        assert "sent by" in body and "from Tico Recorder" in body
        assert f"#/meetings?live={output['live_id']}" in body
        hidden = api.get(f"/api/v2/tasks/{sent['task_id']}", headers={"Authorization": "Bearer cara-test"})
        assert hidden.status_code in (403, 404)

        # Finalized once; the notes reviewed at the end are the meeting's log; the unsent item went
        # along as a proposal, and the sent one did not.
        assert not typed["task_id"]
        meeting = get(api, f"meetings/{output['meeting_id']}")
        assert json.dumps(meeting).count("Reviewed replay notes") >= 1
        with api.app.state.store.read() as c:
            assert c.execute("SELECT imported_meeting_id FROM live_meetings WHERE id=?",
                             (output["live_id"],)).fetchone()[0] == output["meeting_id"]
            items = c.execute("SELECT text,status FROM meeting_items WHERE meeting_id=?",
                              (output["meeting_id"],)).fetchall()
        assert [(row["text"], row["status"]) for row in items] == [("Book the launch room", "proposed")]

        # A recording never opened live is imported once, private, with the calendar attendee.
        local = get(api, f"meetings/{output['local_meeting_id']}")
        assert "Selected local meeting notes" in json.dumps(local)
        assert "ana@acme.example" in json.dumps(local)
        with api.app.state.store.read() as c:
            assert c.execute("SELECT count(*) FROM meetings WHERE title='Selected local recording · synthetic'"
                             ).fetchone()[0] == 1
    finally:
        server.should_exit = True
        server_thread.join(5)
        provider.shutdown()
        provider.server_close()
