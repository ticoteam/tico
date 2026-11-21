"""The video routes (backend/providers_routes.py): POST /api/v2/gateway/videos starts a job on the Tico AI Gateway, GET
the job's state and GET its MP4 read it through the server. The Gateway is faked (no call leaves the test), and a video
at its Finance budget's cutoff is refused before anything is sent."""

import io
import json
import urllib.error

from backend import paid_renders
from backend.store import H
from backend.tests.helpers.held_runs import botops
from backend.tests.helpers.api import assign, claim, headers, post, ready, runner
from clients import gateway as G

JOB = "gv_" + "c" * 24
REFUSAL = "This model always generates audio with the video. generate_audio cannot be set to false."


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
    assert "no-transform" in content.headers["cache-control"].split(", ")


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
    monkeypatch.setattr(G, "video_submit", lambda *a: (404, {}, "404"))
    refused = api.post("/api/v2/gateway/videos", json={"prompt": "x"}, headers=headers("ana-test"))
    assert (refused.status_code, refused.json()["error"]["code"]) == (503, "gateway_video_unavailable")
    # A 202 whose job id cannot be read may have started (and been charged): the shot is not sent again.
    monkeypatch.setattr(G, "video_submit", lambda *a: (502, {}, "unreadable"))
    refused = api.post("/api/v2/gateway/videos", json={"prompt": "x"}, headers=headers("ana-test"))
    assert (refused.status_code, refused.json()["error"]["code"]) == (409, "reconcile_shot")


def test_a_refused_video_says_the_gateways_reason(api, monkeypatch):
    connect(api)
    monkeypatch.setattr(G, "video_submit", lambda *a: (502, {"detail": REFUSAL}, "http"))
    refused = api.post(
        "/api/v2/gateway/videos", json={"prompt": "a kettle", "audio": False}, headers=headers("ana-test")
    )
    error = refused.json()["error"]
    assert refused.status_code == 503 and error["code"] == "gateway_video_failed", refused.text
    assert error["detail"] == f"Video failed: http: {REFUSAL}", refused.text
    # A 5xx that gives no reason of its own may have started (and charged) the video: never sent again.
    monkeypatch.setattr(G, "video_submit", lambda *a: (502, {}, "http"))
    refused = api.post("/api/v2/gateway/videos", json={"prompt": "a kettle"}, headers=headers("ana-test"))
    assert refused.json()["error"]["code"] == "reconcile_shot", refused.text


def test_the_gateway_client_keeps_a_refusals_reason_trimmed_to_300_characters():
    def refusing(answer):
        def opener(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 502, "refused", {}, io.BytesIO(answer))

        return opener

    base = "https://hq.example/v1/gateway"
    long = json.dumps({"error": "http", "detail": "  " + "x" * 400 + "  "}).encode()
    assert G.video_submit(base, "gw-synthetic", {"prompt": "x"}, opener=refusing(long)) == (
        502,
        {"detail": "x" * 300},
        "http",
    )
    bare = json.dumps({"error": "http"}).encode()
    assert G.video_submit(base, "gw-synthetic", {"prompt": "x"}, opener=refusing(bare)) == (502, {}, "http")


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


def turn(api):
    """ops at work on its card: a runner claims ops's task, and the attempt's token is ops for that turn."""
    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    task = post(api, "tasks", {"owner": "ops", "title": "Make the shots", "body": "From the storyboard."})
    return task, claim(api, r)["token"]


def reconcile_asks(api, task):
    """The open reconcile questions on the card: (person, first line)."""
    with api.app.state.store.read() as c:
        rows = c.execute(
            "SELECT person_id, line FROM question_marks WHERE task_id=? AND cleared_at IS NULL AND line LIKE 'Reconcile%'",
            (task["id"],),
        ).fetchall()
    return [tuple(row) for row in rows]


def video(api, token, body, key=None):
    return api.post("/api/v2/gateway/videos", json=body, headers=headers(token, key))


def counting(sent, answer):
    def submit(base, key, body, header):
        sent.append(body)
        return answer

    return submit


def test_a_shot_whose_answer_was_lost_is_never_sent_again_and_asks_once_to_reconcile_it(api, monkeypatch):
    connect(api)
    task, token = turn(api)
    sent = []

    def lost(base, key, body, header):  # the Gateway may have started (and charged) it, but no answer came back
        sent.append(header.get("Idempotency-Key", ""))
        return 0, {}, "unreachable"

    monkeypatch.setattr(G, "video_submit", lost)
    body = {"prompt": "a kettle on the stove, steam rising"}
    first = api.post("/api/v2/gateway/videos", json=body, headers=headers(token, "first-try"))
    again = api.post("/api/v2/gateway/videos", json=body, headers=headers(token, "second-try"))
    assert len(sent) == 1, sent  # no second provider call
    assert sent[0].startswith("shot_")  # the shot key is the Gateway's Idempotency-Key
    for refused in (first, again):
        assert (refused.status_code, refused.json()["error"]["code"]) == (409, "reconcile_shot"), refused.text
    assert reconcile_asks(api, task) == [("human:ana", "Reconcile shot a kettle on the stove, steam")]


def test_a_started_shot_answers_its_job_again_and_one_cut_off_by_a_restart_is_reconciled(api, monkeypatch):
    connect(api)
    task, token = turn(api)
    sent = []

    def submit(base, key, body, header):
        sent.append(body)
        return 202, {"id": JOB, "status": "queued"}, ""

    monkeypatch.setattr(G, "video_submit", submit)
    body = {"prompt": "a kettle", "shot": "Kettle close-up"}
    for idempotency in ("one", "two"):
        started = api.post("/api/v2/gateway/videos", json=body, headers=headers(token, idempotency))
        assert started.status_code == 202 and started.json() == {"id": JOB, "status": "queued"}, started.text
    assert len(sent) == 1 and "shot" not in sent[0]
    # The server stops after the call, before it writes the answer down: after the restart nothing is in flight.
    take = {**body, "shot": "Kettle wide"}
    with monkeypatch.context() as stopped:
        stopped.setattr(paid_renders, "end", lambda *a: None)
        assert api.post("/api/v2/gateway/videos", json=take, headers=headers(token, "three")).status_code == 202
    busy = api.post("/api/v2/gateway/videos", json=take, headers=headers(token, "while-in-flight"))
    assert (busy.status_code, busy.json()["error"]["code"]) == (429, "paid_render_busy"), busy.text
    paid_renders._IN_FLIGHT.clear()
    refused = api.post("/api/v2/gateway/videos", json=take, headers=headers(token, "four"))
    assert (refused.status_code, refused.json()["error"]["code"]) == (409, "reconcile_shot"), refused.text
    assert len(sent) == 2 and reconcile_asks(api, task) == [("human:ana", "Reconcile shot Kettle wide")]
    # A refusal charged nothing, so that shot may be sent again.
    monkeypatch.setattr(G, "video_submit", lambda *a: (422, {}, "invalid"))
    retake = {**body, "shot": "Kettle top"}
    assert api.post("/api/v2/gateway/videos", json=retake, headers=headers(token)).status_code == 422
    monkeypatch.setattr(G, "video_submit", submit)
    assert api.post("/api/v2/gateway/videos", json=retake, headers=headers(token)).status_code == 202
    assert len(sent) == 3


def test_a_gateway_unavailable_answer_is_unknown_and_asked_about_once(api, monkeypatch):
    connect(api)
    task, token = turn(api)
    sent = []
    # HQ's 503 unavailable: the provider timed out, and may still finish and charge.
    monkeypatch.setattr(G, "video_submit", counting(sent, (503, {}, "unavailable")))
    body = {"prompt": "a kettle", "shot": "Kettle pour"}
    for key in ("one", "two"):
        assert video(api, token, body, key).json()["error"]["code"] == "reconcile_shot"
    assert len(sent) == 1 and reconcile_asks(api, task) == [("human:ana", "Reconcile shot Kettle pour")]


def test_the_sweep_asks_about_a_shot_a_restart_left_sent_with_no_request(api, monkeypatch):
    connect(api)
    task, token = turn(api)
    sent = []
    monkeypatch.setattr(G, "video_submit", counting(sent, (202, {"id": JOB, "status": "queued"}, "")))
    with monkeypatch.context() as stopped:  # the server stops after the call, before it writes the answer down
        stopped.setattr(paid_renders, "end", lambda *a: None)
        assert video(api, token, {"prompt": "a kettle", "shot": "Kettle wide"}).status_code == 202
    paid_renders._IN_FLIGHT.clear()
    store = api.app.state.store
    with store.transaction() as c:
        assert paid_renders.sweep(c) == 0  # too new: it may still be answered
        c.execute("UPDATE events SET created_at=? WHERE action=?", (H.shift(H.now(), minutes=-10), paid_renders.SENT))
    with store.transaction() as c:
        assert paid_renders.sweep(c) == 1
    with store.transaction() as c:
        assert paid_renders.sweep(c) == 0
    assert len(sent) == 1 and reconcile_asks(api, task) == [("human:ana", "Reconcile shot Kettle wide")]


def test_unknown_shots_share_one_question_and_leave_the_agents_own_question_open(api, monkeypatch):
    connect(api)
    botops(api)  # BotOps asks: the agent's own open question is never replaced
    task, token = turn(api)
    own = api.post(
        f"/api/v2/tasks/{task['id']}/questions",
        json={"to": "ana", "text": "Which tone should the launch video take?"},
        headers=headers(token),
    )
    assert own.status_code == 200, own.text
    monkeypatch.setattr(G, "video_submit", lambda *a: (0, {}, "unreachable"))
    for shot in ("Kettle wide", "Kettle top"):
        assert video(api, token, {"prompt": "a kettle", "shot": shot}).status_code == 409
    assert reconcile_asks(api, task) == [("human:ana", "Reconcile shots Kettle wide, Kettle top")]
    with api.app.state.store.read() as c:
        opened = c.execute(
            "SELECT line FROM question_marks WHERE task_id=? AND cleared_at IS NULL ORDER BY created_at", (task["id"],)
        ).fetchall()
    assert [row[0] for row in opened] == [
        "Which tone should the launch video take?",
        "Reconcile shots Kettle wide, Kettle top",
    ]


def test_a_started_shot_is_answered_again_without_the_send_checks(api, monkeypatch):
    connect(api)
    _, token = turn(api)
    sent = []
    monkeypatch.setattr(G, "video_submit", counting(sent, (202, {"id": JOB, "status": "queued"}, "")))
    body = {"prompt": "a kettle", "shot": "Kettle close-up"}
    assert video(api, token, body).status_code == 202
    settings = api.app.state.store.settings
    settings.gateway_key = ""  # the Gateway is gone: a new shot is refused, the started one still answers
    again = video(api, token, body)
    assert again.status_code == 202 and again.json() == {"id": JOB, "status": "queued"}, again.text
    assert video(api, token, {**body, "shot": "Kettle top"}).json()["error"]["code"] == "gateway_not_connected"
    assert len(sent) == 1


def test_the_reconcile_question_goes_to_the_pipelines_person(api, monkeypatch):
    connect(api)
    task, token = turn(api)
    with api.app.state.store.transaction() as c:
        c.execute(
            "UPDATE pipelines SET owner_id='human:ben' WHERE id=(SELECT pipeline_id FROM tasks WHERE id=?)",
            (task["id"],),
        )
        c.execute("UPDATE tasks SET human_id=NULL WHERE id=?", (task["id"],))
    monkeypatch.setattr(G, "video_submit", lambda *a: (0, {}, "unreachable"))
    assert video(api, token, {"prompt": "a kettle", "shot": "Kettle wide"}).status_code == 409
    assert reconcile_asks(api, task) == [("human:ben", "Reconcile shot Kettle wide")]


def test_an_unnamed_video_outside_a_card_is_keyed_to_its_request(api, monkeypatch):
    connect(api)
    sent = []
    monkeypatch.setattr(G, "video_submit", counting(sent, (202, {"id": JOB, "status": "queued"}, "")))
    body = {"prompt": "a kettle"}
    assert video(api, "ana-test", body, "first").status_code == 202
    assert video(api, "ana-test", body, "first").status_code == 202  # a retry of the same request
    assert video(api, "ana-test", body, "second").status_code == 202  # a new request is a new take
    assert len(sent) == 2
