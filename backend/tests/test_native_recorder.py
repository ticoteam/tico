"""Opt-in native privacy replay against an actual isolated Tico, with no runners/providers.

The loopback server pattern comes from recorder-actions-e2e's native Recorder harness.
Only synthetic word/audio fixtures enter the CLI; tasks go to a synthetic person.
"""
import json
import os
import socket
import subprocess
import threading
import time

import pytest
import uvicorn

from backend.tests.test_api import api  # noqa: F401


@pytest.mark.slow
@pytest.mark.skipif(not os.environ.get("TICO_NATIVE_CLI"), reason="Build CLI and set TICO_NATIVE_CLI")
@pytest.mark.parametrize("scenario", ["local", "remote", "discard", "lost-ack", "sequence-conflict", "pause-ack", "discard-ack"])
def test_native_pause_resume_discard_files_only_public_text(api, tmp_path, scenario):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    faults = []
    controls = []

    async def transport(scope, receive, send):
        # Let the real backend commit the first chunk, then lose only its acknowledgement.
        path = scope.get("path", "")
        if scope.get("method") == "POST" and path.endswith("/control"):
            controls.append(path)
        lose = ((scenario == "lost-ack" and path.endswith("/chunks"))
                or (scenario == "pause-ack" and path.endswith("/control") and len(controls) == 1)
                or (scenario == "discard-ack" and path.endswith("/control") and len(controls) == 2))
        if scope.get("method") == "POST" and lose and not faults:
            messages = []
            async def collect(message):
                messages.append(message)
            await api.app(scope, receive, collect)
            assert messages[0]["status"] == 200
            faults.append("committed_ack_lost")
            await send({"type": "http.response.start", "status": 503, "headers": []})
            await send({"type": "http.response.body", "body": b"fixture lost acknowledgement"})
        else:
            await api.app(scope, receive, send)

    server = uvicorn.Server(uvicorn.Config(transport, host="127.0.0.1", port=port,
                                         lifespan="off", log_level="error"))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        threading.Event().wait(.02)
    assert server.started
    try:
        result = subprocess.run(
            [os.environ["TICO_NATIVE_CLI"], "privacy-replay-test", "--tico-url",
             f"http://127.0.0.1:{port}", "--scenario", scenario],
            env={**os.environ, "TICO_RECORDER_DATA_DIR": str(tmp_path / "library"),
                 "TICO_REPLAY_TEST_TOKEN": "ana-test"},
            text=True, capture_output=True, timeout=60)
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        assert out["synthetic"] is True
        if scenario in ("lost-ack", "pause-ack", "discard-ack"):
            assert faults == ["committed_ack_lost"]
        mid = out["live_id"]
        with api.app.state.store.read() as c:
            live = c.execute("SELECT state,imported_meeting_id FROM live_meetings WHERE id=?", (mid,)).fetchone()
            assert live["state"] == "ended"
            # Final text alone could hide a temporary leak later removed by a correction.
            versions = c.execute("SELECT text FROM live_meeting_chunk_versions WHERE meeting_id=?",
                                 (mid,)).fetchall()
            assert versions
            assert all("PRIVATE_ALPHA" not in row["text"] and "PRIVATE_BETA" not in row["text"]
                       for row in versions)
            if scenario in ("discard", "discard-ack"):
                assert not live["imported_meeting_id"]
                assert c.execute("SELECT count(*) FROM meetings").fetchone()[0] == 0
                assert c.execute("SELECT count(*) FROM tasks").fetchone()[0] == 0
            else:
                filed = c.execute("SELECT transcript_original FROM meetings WHERE id=?",
                                  (live["imported_meeting_id"],)).fetchone()[0]
                assert "Public closing" in filed
                assert "Public opening" in filed
                assert "PRIVATE_ALPHA" not in filed and "PRIVATE_BETA" not in filed
                if scenario == "remote":
                    assert "Uncertain pre-pause turn" not in filed
                assert c.execute("SELECT count(*) FROM meetings").fetchone()[0] == 1
                tasks = c.execute("SELECT title,body,owner FROM tasks").fetchall()
                assert len(tasks) == 2
                assert {t["owner"] for t in tasks} == {"human:ben"}
                quote = next(t for t in tasks if t["title"] == "Review selected quote")
                # Explicitly reviewed quote is allowed; neighboring paused words never are.
                assert "PRIVATE_ALPHA" in quote["body"]
                assert "PRIVATE_BETA" not in quote["body"]
                title = next(t for t in tasks if t["title"] == "PRIVATE_BETA edited")
                assert "PRIVATE_ALPHA" not in title["body"]
            # No runner gets attached or queued by these controls or human-owned tasks.
            assert c.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0
        local = json.dumps(out["local"])
        assert "PRIVATE_ALPHA" in local and "PRIVATE_BETA" in local
        assert len(out["local"]["chunks"]) == 2
        assert out["context"]["paused_windows"]
    finally:
        server.should_exit = True
        thread.join(5)
        sock.close()
