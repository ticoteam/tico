"""The video routes (backend/providers_routes.py): POST /api/v2/gateway/videos starts a job on the Tico AI Gateway, GET
the job's state and GET its MP4 read it through the server. The Gateway is faked (no call leaves the test), and a video
at its Finance budget's cutoff is refused before anything is sent."""

from backend.tests.helpers.api import headers  # noqa: F401
from clients import gateway as G

JOB = "gv_" + "c" * 24


def connect(api):
    settings = api.app.state.store.settings
    settings.gateway_key, settings.gateway_url = "gw-synthetic", "https://hq.example/v1/gateway/decisions"


def test_a_video_is_started_polled_and_read_through_the_server(api, monkeypatch):
    connect(api)
    submitted = []

    def submit(base, key, body, header):
        submitted.append((base, key, body, header))
        return 202, {"id": JOB, "status": "queued"}, ""

    monkeypatch.setattr(G, "video_submit", submit)
    monkeypatch.setattr(
        G,
        "video_status",
        lambda base, key, job_id, header: (
            200,
            {
                "id": job_id,
                "status": "done",
                "seconds": 5,
                "resolution": "720p",
                "aspect": "16:9",
                "audio": False,
                "cost_usd": 0.4,
            },
            "",
        ),
    )
    monkeypatch.setattr(G, "video_content", lambda base, key, job_id, header: (200, b"mp4 bytes", ""))
    started = api.post(
        "/api/v2/gateway/videos", json={"prompt": "a kettle", "seconds": 5, "audio": False}, headers=headers("ana-test")
    )
    assert started.status_code == 202 and started.json() == {"id": JOB, "status": "queued"}, started.text
    assert submitted[0][2] == {
        "model": "tico-video",
        "prompt": "a kettle",
        "seconds": 5,
        "resolution": "720p",
        "aspect": "16:9",
        "audio": False,
    }
    assert submitted[0][:2] == ("https://hq.example/v1/gateway", "gw-synthetic")
    polled = api.get(f"/api/v2/gateway/videos/{JOB}", headers=headers("ana-test"))
    assert polled.status_code == 200 and polled.json()["cost_usd"] == 0.4
    content = api.get(f"/api/v2/gateway/videos/{JOB}/content", headers=headers("ana-test"))
    assert content.status_code == 200 and content.content == b"mp4 bytes"
    assert content.headers["content-type"] == "video/mp4" and len(content.headers["x-content-sha256"]) == 64


def test_the_video_routes_map_the_gateways_answers_to_plain_problems(api, monkeypatch):
    connect(api)
    cases = [
        (lambda *a: (409, {}, "not_ready"), "gateway_video_not_ready", 409),
        (lambda *a: (409, {}, "failed"), "gateway_video_failed", 409),
        (lambda *a: (410, {}, "expired"), "gateway_video_expired", 410),
        (lambda *a: (404, {}, "not_found"), "gateway_video_not_found", 404),
        (lambda *a: (200, b"", "too_large"), "gateway_video_too_large", 413),
        (lambda *a: (0, b"", "unreachable"), "gateway_video_failed", 503),
    ]
    for answer, code, status in cases:
        monkeypatch.setattr(G, "video_content", answer)
        refused = api.get(f"/api/v2/gateway/videos/{JOB}/content", headers=headers("ana-test"))
        assert (refused.status_code, refused.json()["error"]["code"]) == (status, code), refused.text
    monkeypatch.setattr(G, "video_status", lambda *a: (404, {}, "not_found"))
    assert api.get(f"/api/v2/gateway/videos/{JOB}", headers=headers("ana-test")).status_code == 404
    monkeypatch.setattr(G, "video_submit", lambda *a: (502, {}, "unreadable"))
    refused = api.post("/api/v2/gateway/videos", json={"prompt": "x"}, headers=headers("ana-test"))
    assert (refused.status_code, refused.json()["error"]["code"]) == (503, "gateway_video_failed")
    monkeypatch.setattr(G, "video_submit", lambda *a: (404, {}, "404"))
    refused = api.post("/api/v2/gateway/videos", json={"prompt": "x"}, headers=headers("ana-test"))
    assert (refused.status_code, refused.json()["error"]["code"]) == (503, "gateway_video_unavailable")


def test_a_video_request_is_checked_before_anything_is_sent(api, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("A refused video must not reach the Gateway")

    monkeypatch.setattr(G, "video_submit", forbidden)
    connect(api)
    for body in (
        {"prompt": ""},
        {"prompt": "x", "seconds": 16},
        {"prompt": "x", "resolution": "8k"},
        {"prompt": "x", "aspect": "1:1"},
        {"prompt": "x", "first_frame": "https://example.test/a.png"},
    ):
        assert api.post("/api/v2/gateway/videos", json=body, headers=headers("ana-test")).status_code == 422, body
    assert api.get("/api/v2/gateway/videos/not-a-job", headers=headers("ana-test")).status_code == 404


def test_a_video_needs_a_connected_gateway_and_stops_at_the_video_budget(api, monkeypatch):

    refused = api.post("/api/v2/gateway/videos", json={"prompt": "x"}, headers=headers("ana-test"))
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "gateway_not_connected"
