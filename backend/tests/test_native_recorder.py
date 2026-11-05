"""Opt-in native privacy replay against an actual isolated Tico, with no runners/providers.

The loopback server pattern comes from recorder-actions-e2e's native Recorder harness.
Only synthetic word/audio fixtures enter the CLI; no real capture or providers are used.
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

from backend.tests.test_api import api, get  # noqa: F401


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
                tasks = c.execute("SELECT title,body,owner_id FROM tasks").fetchall()
                assert len(tasks) == 2
                assert {t["owner_id"] for t in tasks} == {"human:ben"}
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


COMMITMENT = "I will update the launch plan"


@pytest.mark.slow
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
        # Three transcriptions, one draft review, one private question: nothing else left the computer.
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
            tasks = c.execute("SELECT id,owner_id,body FROM tasks WHERE title='Update the launch plan'").fetchall()
        assert [(row["id"], row["owner_id"]) for row in tasks] == [(sent["task_id"], "bot:coo")]
        body = tasks[0]["body"]
        assert f"“{COMMITMENT}”" in body and "Transcript around it:" in body
        assert "sent by" in body and "from Tico Recorder" in body
        assert f"#/meetings/live/{output['live_id']}" in body
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
        sock.close()
        provider.shutdown()
        provider.server_close()


@pytest.mark.slow
@pytest.mark.skipif(not os.environ.get('TICO_NATIVE_CLI'), reason='Build CLI and set TICO_NATIVE_CLI')
def test_recorder_rows_link_both_ways_resume_batches_and_reconcile_save(api, tmp_path):
    from backend.tests.test_api import post
    targets = [post(api, 'tasks', {'title': f'Review pricing {n}', 'body': 'Review pricing and report back.', 'owner': 'human:ana', **({'number': 42} if n == 0 else {})}, 'ana-test')['id']
               for n in range(45)]
    batches, uploads = [], []
    lost_item = False
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))

    async def transport(scope, receive, send):
        nonlocal lost_item
        path = scope.get('path', '')
        if scope.get('method') != 'POST':
            return await api.app(scope, receive, send)
        body = b''
        while True:
            message = await receive()
            body += message.get('body', b'')
            if not message.get('more_body'):
                break
        payload = json.loads(body or '{}')
        delivered = False
        async def replay_body():
            nonlocal delivered
            if delivered:
                return await receive()
            delivered = True
            return {'type': 'http.request', 'body': body, 'more_body': False}
        fail = False
        if path.endswith('/tasks') and '/meetings/' in path:
            batches.append((payload['task_ids'], dict(scope['headers']).get(b'idempotency-key')))
            fail = len(batches) == 2
        lose = path.endswith('/items') and payload.get('text') == 'Original retry title' and not lost_item
        if path.endswith('/items'):
            uploads.append((payload, dict(scope['headers']).get(b'idempotency-key')))
        if lose:
            messages = []
            async def collect(message):
                messages.append(message)
            await api.app(scope, replay_body, collect)
            assert messages[0]['status'] == 200
            lost_item = True
        if fail or lose:
            await send({'type': 'http.response.start', 'status': 503, 'headers': []})
            return await send({'type': 'http.response.body', 'body': b'Interrupted synthetic delivery'})
        await api.app(scope, replay_body, send)

    server = uvicorn.Server(uvicorn.Config(transport, lifespan='off', log_level='error'))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        threading.Event().wait(.02)
    assert server.started
    try:
        result = subprocess.run([os.environ['TICO_NATIVE_CLI'], 'task-rows-test', '--tico-url',
                                 f'http://127.0.0.1:{sock.getsockname()[1]}'],
                                env={**os.environ, 'TICO_RECORDER_DATA_DIR': str(tmp_path / 'rows-library'),
                                     'TICO_REPLAY_TEST_TOKEN': 'ana-test', 'TICO_LINK_TEST_TASKS': json.dumps(targets)},
                                text=True, capture_output=True, timeout=60)
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        assert out['synthetic'] and out['local']['kept_on_mac'] and len(out['local']['items']) == 1
        assert [len(ids) for ids, _ in batches] == [20, 20, 20, 5]
        assert batches[1] == batches[2]
        linked = get(api, f"meetings/{out['meeting']}/tasks?limit=50")['tasks']
        assert {row['task_id'] for row in linked} == {*targets, out['created']}
        for target in (targets[0], targets[-1], out['created']):
            assert out['meeting'] in {row['meeting_id'] for row in get(api, f'tasks/{target}/meetings')['meetings']}
        assert get(api, f"meetings/{out['pending']}")['review_state'] == 'dismissed'
        retry_uploads = [row for row in uploads if row[0]['text'] == 'Original retry title']
        assert len(retry_uploads) == 2 and retry_uploads[0] == retry_uploads[1]
        retry = get(api, f"meetings/{out['retry_meeting']}/items")['sections']['task'][0]
        assert retry['text'] == 'Current retry title' and retry['version'] == 2
        assert {key: retry['detail'][key] for key in ('owner', 'body', 'due')} == {
            'owner': 'human:ben', 'body': 'Current retry details', 'due': '2026-12-01T12:00:00Z'}
        with api.app.state.store.read() as c:
            assert c.execute('SELECT count(*) FROM tasks').fetchone()[0] == 46
            assert c.execute('SELECT count(*) FROM meetings m LEFT JOIN media_control mc ON mc.meeting_id=m.id '
                             'WHERE mc.deleted_at IS NULL').fetchone()[0] == 3
            assert c.execute('SELECT count(*) FROM media_control WHERE deleted_at IS NOT NULL').fetchone()[0] == 0
            assert c.execute('SELECT review_state FROM meetings WHERE id=?', (out['pending'],)).fetchone()[0] == 'dismissed'
            for table in ('live_meeting_chunks', 'live_meeting_chunk_versions', 'live_meeting_chat', 'live_meeting_events'):
                assert c.execute(f'SELECT count(*) FROM {table} WHERE meeting_id=?', (out['live'],)).fetchone()[0] > 0
            assert c.execute('SELECT task_id FROM meeting_task_links WHERE meeting_id=?', (out['pending'],)).fetchone()[0] == out['created']
            proposal = c.execute("SELECT * FROM meeting_items WHERE proposed_task_id IS NOT NULL").fetchone()
            assert proposal['status'] == 'pushed' and proposal['result_ref'] == targets[0]
            assert c.execute('SELECT count(*) FROM jobs').fetchone()[0] == 0
    finally:
        server.should_exit = True
        thread.join(5)
        sock.close()
