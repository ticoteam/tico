"""Upload receipts avoid durable writes; parser work stays off the event loop."""
import asyncio
import threading

import pytest

from backend.tests.test_api import api, headers, post  # noqa: F401


@pytest.mark.parametrize("kind", ["task_multipart"])
def test_upload_replays_and_conflicts_do_not_write_blobs(api, monkeypatch, kind):
    tid = post(api, "tasks", {"title": "Review upload", "owner": "ops", "body": "Read the report."})["id"]
    path = "/api/v2/uploads/chat/ops" if kind == "chat_multipart" else f"/api/v2/tasks/{tid}/files"
    h = headers("ana-test", "upload-replay")
    def upload(content):
        if kind == "task_json":
            return api.post(path, json={"name": "report.txt", "text": content}, headers=h)
        field = "files" if kind == "chat_multipart" else "file"
        data = {"text": "Review attached report"} if kind == "chat_multipart" else {}
        return api.post(path, files={field: ("report.txt", content.encode(), "text/plain")}, data=data, headers=h)
    first = upload("original")
    assert first.status_code == 200, first.text
    def unexpected(*args, **kwargs):
        pytest.fail("replay or conflict attempted a durable blob write")
    blobs = api.app.state.blobs
    monkeypatch.setattr(blobs, "put", unexpected)
    monkeypatch.setattr(blobs, "put_staged", unexpected)
    again = upload("original")
    assert again.status_code == 200 and again.json() == first.json()
    changed = upload("changed")
    assert changed.status_code == 409 and changed.json()["error"]["code"] == "idempotency_conflict"


def test_multipart_parser_hashes_and_writes_in_worker_thread(tmp_path, monkeypatch):
    from backend import file_upload
    original = file_upload.MultipartParser
    threads = []
    class Parser(original):
        def write(self, data):
            threads.append(threading.get_ident())
            return super().write(data)
    monkeypatch.setattr(file_upload, "MultipartParser", Parser)
    raw = (b'--boundary\r\nContent-Disposition: form-data; name="file"; filename="report.txt"\r\n'
           b'Content-Type: text/plain\r\n\r\ncontent\r\n--boundary--\r\n')
    class Request:
        headers = {"content-type": "multipart/form-data; boundary=boundary"}
        async def stream(self):
            for offset in range(0, len(raw), 17):
                yield raw[offset:offset + 17]
    async def run():
        fields, uploads, stack = await file_upload.parse(Request(), 1000, tmp_path)
        try:
            assert uploads["file"]["stream"].read() == b"content"
            assert threads and all(thread != threading.get_ident() for thread in threads)
        finally:
            stack.close()
    asyncio.run(run())
