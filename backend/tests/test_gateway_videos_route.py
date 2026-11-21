"""The video routes (backend/providers_routes.py): POST /api/v2/gateway/videos starts a job on the Tico AI Gateway, GET
the job's state and GET its MP4 read it through the server. The Gateway is faked (no call leaves the test), and a video
at its Finance budget's cutoff is refused before anything is sent."""

import io
import json
import urllib.error

from backend import paid_renders
from backend import task_relations as TR
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
        "/api/v2/gateway/videos",
        json={"prompt": "a kettle", "seconds": 5, "audio": False},
        headers=headers("ana-test"),
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
    monkeypatch.setattr(G, "video_submit", lambda *a: (502, {}, "http"))
    refused = api.post("/api/v2/gateway/videos", json={"prompt": "a kettle"}, headers=headers("ana-test"))
    assert refused.json()["error"]["detail"] == "Video failed: http", refused.text


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


def turn(api, with_botops=True):
    """ops at work on its card: a runner claims ops's task, and the attempt's token is ops for that turn. BotOps, who
    asks the reconcile question, is here unless `with_botops` is False."""
    if with_botops:
        botops(api)
    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    task = post(api, "tasks", {"owner": "ops", "title": "Make the shots", "body": "From the storyboard."})
    return task, claim(api, r)["token"]


def reconcile_tasks(api, card):
    """The card's open "Reconcile shots" tasks: (owner, the shot names its text lists, one per line)."""
    with api.app.state.store.read() as c:
        children = c.execute(
            "SELECT DISTINCT json_extract(e.detail, '$.child') FROM events e JOIN tasks t "
            "ON t.id=json_extract(e.detail, '$.child') WHERE e.action=? AND e.target=? AND t.status='open'",
            (paid_renders.RECONCILE, card["id"]),
        ).fetchall()
        found = []
        for (child,) in children:
            task = c.execute("SELECT owner_id, title FROM tasks WHERE id=?", (child,)).fetchone()
            body = c.execute(
                "SELECT m.body FROM messages m JOIN tasks t ON t.conversation_id=m.conversation_id WHERE t.id=? "
                "ORDER BY m.rowid LIMIT 1",
                (child,),
            ).fetchone()[0]
            assert task["title"] == "Reconcile shots" and TR.parent_of(c, child) == card["id"]
            names = [line[2:].split(": shot ")[0] for line in body.splitlines() if line.startswith("- ")]
            found.append((task["owner_id"], names))
    return found


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
    assert reconcile_tasks(api, task) == [("human:ana", ["a kettle on the stove, steam"])]


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
    assert len(sent) == 2 and reconcile_tasks(api, task) == [("human:ana", ["Kettle wide"])]
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
    assert len(sent) == 1 and reconcile_tasks(api, task) == [("human:ana", ["Kettle pour"])]


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
    assert len(sent) == 1 and reconcile_tasks(api, task) == [("human:ana", ["Kettle wide"])]


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
    assert reconcile_tasks(api, task) == [("human:ben", ["Kettle wide"])]


def test_an_unnamed_video_outside_a_card_is_keyed_to_its_request(api, monkeypatch):
    connect(api)
    sent = []
    monkeypatch.setattr(G, "video_submit", counting(sent, (202, {"id": JOB, "status": "queued"}, "")))
    body = {"prompt": "a kettle"}
    assert video(api, "ana-test", body, "first").status_code == 202
    assert video(api, "ana-test", body, "first").status_code == 202  # a retry of the same request
    assert video(api, "ana-test", body, "second").status_code == 202  # a new request is a new take
    assert len(sent) == 2
    # Nothing names the shot: refused before anything is sent.
    keyless = api.post("/api/v2/gateway/videos", json=body, headers={"Authorization": "Bearer ana-test"})
    assert keyless.status_code == 422 and "Idempotency-Key" in keyless.json()["error"]["detail"], keyless.text
    conflict = video(api, "ana-test", {"prompt": "a teapot"}, "first")
    assert (conflict.status_code, conflict.json()["error"]["code"]) == (409, "idempotency_conflict"), conflict.text
    assert len(sent) == 2


def test_a_retry_after_the_card_moved_on_is_the_same_shot(api, monkeypatch):
    connect(api)
    task, token = turn(api)
    sent = []
    monkeypatch.setattr(G, "video_submit", counting(sent, (202, {"id": JOB, "status": "queued"}, "")))
    body = {"prompt": "a kettle", "shot": "Kettle wide"}
    assert video(api, token, body, "the-request").status_code == 202
    with api.app.state.store.transaction() as c:  # the card moves to a new visit before the retry
        c.execute("UPDATE visits SET id='visit-moved' WHERE task_id=? AND left_at IS NULL", (task["id"],))
    again = video(api, token, body, "the-request")
    assert again.status_code == 202 and again.json() == {"id": JOB, "status": "queued"}, again.text
    assert len(sent) == 1


def unknown_now(api, token, shot, monkeypatch):
    monkeypatch.setattr(G, "video_submit", lambda *a: (0, {}, "unreachable"))
    assert video(api, token, {"prompt": "a kettle", "shot": shot}).json()["error"]["code"] == "reconcile_shot"


def test_only_certain_refusals_let_a_shot_be_sent_again(api, monkeypatch):
    connect(api)
    _, token = turn(api)
    cases = [  # by status and code only: a reason that comes with it changes nothing
        ((503, {}, "unconfigured"), 503, 2),  # refused before anything was sent
        ((502, {}, "auth"), 503, 2),
        ((502, {"detail": "No audio-free mode"}, "http"), 503, 2),
        ((504, {"detail": "The provider took too long"}, "http"), 409, 1),
        ((503, {"detail": "The provider timed out"}, "provider_timeout"), 409, 1),
        ((503, {}, "unavailable"), 409, 1),
        ((502, {"detail": "An answer that could not be read"}, "unreadable"), 409, 1),
    ]
    for n, (answer, status, sends) in enumerate(cases):
        sent = []
        monkeypatch.setattr(G, "video_submit", counting(sent, answer))
        body = {"prompt": "a kettle", "shot": f"Take {n}"}
        for _ in range(2):
            assert video(api, token, body).status_code == status, answer
        assert len(sent) == sends, answer


def test_a_second_request_for_a_shot_in_flight_keeps_its_key_as_an_alias(api, monkeypatch):
    connect(api)
    task, token = turn(api)
    sent = []
    monkeypatch.setattr(G, "video_submit", counting(sent, (202, {"id": JOB, "status": "queued"}, "")))
    body = {"prompt": "a kettle", "shot": "Kettle wide"}
    with monkeypatch.context() as stopped:  # request A is still waiting on the Gateway
        stopped.setattr(paid_renders, "end", lambda *a: None)
        assert video(api, token, body, "request-a").status_code == 202
    busy = video(api, token, body, "request-b")
    assert (busy.status_code, busy.json()["error"]["code"]) == (429, "paid_render_busy"), busy.text
    with api.app.state.store.transaction() as c:  # the card moves to a new visit before B retries
        c.execute("UPDATE visits SET id='visit-moved' WHERE task_id=? AND left_at IS NULL", (task["id"],))
    assert video(api, token, body, "request-b").status_code == 429  # still the same shot, never a second send
    paid_renders._IN_FLIGHT.clear()  # a restart: A's outcome is unknown
    settings = api.app.state.store.settings
    settings.gateway_key = ""  # an unknown shot is answered before the Gateway check
    refused = video(api, token, body, "request-b")
    assert (refused.status_code, refused.json()["error"]["code"]) == (409, "reconcile_shot"), refused.text
    assert len(sent) == 1


def test_a_shot_sent_again_after_a_refusal_keeps_its_first_visit_and_name(api, monkeypatch):
    connect(api)
    task, token = turn(api)
    with api.app.state.store.read() as c:
        first = c.execute("SELECT id FROM visits WHERE task_id=? AND left_at IS NULL", (task["id"],)).fetchone()[0]
    monkeypatch.setattr(G, "video_submit", lambda *a: (422, {}, "invalid"))
    body = {"prompt": "a kettle", "shot": "Kettle wide"}
    assert video(api, token, body, "the-request").status_code == 422
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE visits SET id='visit-moved' WHERE task_id=? AND left_at IS NULL", (task["id"],))
    monkeypatch.setattr(G, "video_submit", lambda *a: (0, {}, "unreachable"))
    named = video(api, token, {**body, "shot": "Another name"}, "the-request")  # the key with another shot
    assert (named.status_code, named.json()["error"]["code"]) == (409, "idempotency_conflict"), named.text
    # The retry is still the shot its key was first sent under, on its first visit.
    assert video(api, token, body, "the-request").status_code == 409
    with api.app.state.store.read() as c:
        sends = [
            json.loads(row[0]) for row in c.execute("SELECT detail FROM events WHERE action=?", (paid_renders.SENT,))
        ]
    assert [(d["visit"], d["shot"]) for d in sends] == [(first, "Kettle wide"), (first, "Kettle wide")]


def test_unknown_shots_share_one_task_one_line_each_even_with_one_name(api, monkeypatch):
    connect(api)
    task, token = turn(api)
    monkeypatch.setattr(G, "video_submit", lambda *a: (0, {}, "unreachable"))
    for prompt in ("a kettle", "a teapot"):  # two shots, both named Intro: two keys, two lines
        assert video(api, token, {"prompt": prompt, "shot": "Intro"}).status_code == 409
    assert reconcile_tasks(api, task) == [("human:ana", ["Intro", "Intro"])]
    with api.app.state.store.read() as c:
        keys = {row[0] for row in c.execute("SELECT target FROM events WHERE action=?", (paid_renders.UNKNOWN,))}
        assert len(keys) == 2 and len(TR.child_ids(c, task["id"])) == 1
    with api.app.state.store.transaction() as c:
        assert paid_renders.sweep(c) == 0  # nothing new: the task stays as it is


def test_closing_the_task_reconciles_its_shots_and_a_later_one_opens_a_new_task(api, monkeypatch):
    connect(api)
    task, token = turn(api)
    unknown_now(api, token, "Kettle wide", monkeypatch)
    store = api.app.state.store
    with store.transaction() as c:
        (child,) = TR.child_ids(c, task["id"])
        H.task_done(c, "human:ana", child, "Checked Gateway usage.")  # the person closes it
    with store.transaction() as c:
        assert paid_renders.sweep(c) == 0  # reconciled: nothing opens again
    assert reconcile_tasks(api, task) == []
    unknown_now(api, token, "Kettle top", monkeypatch)
    assert reconcile_tasks(api, task) == [("human:ana", ["Kettle top"])]


def test_an_approval_on_the_card_leaves_the_reconcile_task_alone(api, monkeypatch):
    from backend import question_marks as Q

    connect(api)
    task, token = turn(api)
    unknown_now(api, token, "Kettle wide", monkeypatch)
    with api.app.state.store.transaction() as c:  # a person's approval on the card clears what it answers there
        Q.approval_answered(c, task["id"], "human:ana", "answer-1")
    with api.app.state.store.transaction() as c:
        assert paid_renders.sweep(c) == 0
    assert reconcile_tasks(api, task) == [("human:ana", ["Kettle wide"])]


def test_a_person_who_cannot_read_the_card_is_not_given_the_task(api, monkeypatch):
    connect(api)
    task, token = turn(api)
    with api.app.state.store.transaction() as c:  # the pipeline's person cannot read this private card
        c.execute(
            "UPDATE pipelines SET owner_id='human:ben' WHERE id=(SELECT pipeline_id FROM tasks WHERE id=?)",
            (task["id"],),
        )
        c.execute("UPDATE tasks SET human_id=NULL, private=1 WHERE id=?", (task["id"],))
        assert not H.task_private_readable(c, "human:ben", H.task(c, task["id"]))
    unknown_now(api, token, "Kettle wide", monkeypatch)
    assert reconcile_tasks(api, task) == [("human:ana", ["Kettle wide"])]


def test_an_alias_is_bookkeeping_not_the_runs_work():
    from backend import run_triggers as RT

    assert not RT.counts(paid_renders.ALIAS) and RT.counts(paid_renders.SENT)


def test_the_sweep_finds_a_shot_sent_again_long_ago_whatever_came_before(api, monkeypatch):
    connect(api)
    task, token = turn(api)
    body = {"prompt": "a kettle", "shot": "Kettle wide"}
    monkeypatch.setattr(G, "video_submit", lambda *a: (422, {}, "invalid"))
    assert video(api, token, body).status_code == 422  # refused once, then sent again and cut off by a restart
    with monkeypatch.context() as stopped:
        stopped.setattr(paid_renders, "end", lambda *a: None)
        monkeypatch.setattr(G, "video_submit", lambda *a: (202, {"id": JOB, "status": "queued"}, ""))
        assert video(api, token, body).status_code == 202
    paid_renders._IN_FLIGHT.clear()
    store = api.app.state.store
    with store.transaction() as c:
        c.execute("UPDATE events SET created_at=? WHERE action LIKE 'paid_render.%'", (H.shift(H.now(), days=-3),))
    with store.transaction() as c:
        assert paid_renders.sweep(c) == 1
    assert reconcile_tasks(api, task) == [("human:ana", ["Kettle wide"])]
