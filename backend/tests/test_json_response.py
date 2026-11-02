"""Wire encoding preserves the authenticated API payload and downloaded bytes."""
from fastapi.responses import JSONResponse, Response, StreamingResponse

from backend.tests.test_api import api, headers, post


def test_task_json_compression_preserves_privacy_and_display_names(api):
    task = post(api, "tasks", {"owner": "ops", "title": "Review private sample work",
                              "body": "Private sample details. " * 200, "private": True})
    path = "/api/v2/tasks?limit=100"
    plain = api.get(path, headers={**headers(), "Accept-Encoding": "identity"})
    zipped = api.get(path, headers={**headers(), "Accept-Encoding": "gzip"})
    assert zipped.json() == plain.json()
    assert zipped.headers["content-encoding"] == "gzip"
    assert int(zipped.headers["content-length"]) < len(zipped.content) // 2
    assert "Accept-Encoding" in zipped.headers["vary"]
    assert zipped.headers["cache-control"].startswith("no-store")
    assert zipped.json()["tasks"][0]["owner_name"] == "ops"
    denied = api.get(path, headers={**headers("ben-test"), "Accept-Encoding": "gzip"})
    assert task["id"] not in {row["id"] for row in denied.json()["tasks"]}
    refused = api.get(path, headers={**headers(), "Accept-Encoding": "gzip;q=0, *;q=1"})
    assert "content-encoding" not in refused.headers
    assert refused.json() == plain.json()


def test_compression_leaves_streams_and_downloaded_json_bytes_alone(api):
    payload = b'{"text":"' + b"sample " * 1000 + b'"}'

    @api.app.get("/api/test-download")
    def download():
        return Response(payload, media_type="application/json",
                        headers={"Content-Disposition": "attachment; filename=sample.json", "X-Content-Sha256": "sample"})

    @api.app.get("/api/test-stream")
    def stream():
        return StreamingResponse(iter([b"event: ready\ndata: {}\n\n"]), media_type="text/event-stream")

    @api.app.get("/api/test-json-download")
    def json_download():
        return JSONResponse({"text": "sample " * 1000}, headers={"Content-Disposition": "attachment"})

    # The app's final static mount otherwise catches a test route appended after startup.
    for _ in range(3):
        api.app.router.routes.insert(0, api.app.router.routes.pop())
    for path in ("test-download", "test-stream", "test-json-download"):
        response = api.get("/api/" + path, headers={**headers(), "Accept-Encoding": "gzip"})
        assert response.status_code == 200
        assert "content-encoding" not in response.headers
    assert api.get("/api/test-download", headers=headers()).content == payload
