"""Exercise the network contract with separate humans and runners over real SQLite."""

import uuid
import sqlite3
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor

import anyio
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.store import H
from backend.tests.helpers.api import (
    api_settings,
    assign,
    claim,
    expire,
    get,
    headers,
    post,
    ready,
    runner,
    setup_attempt,
)


def test_chat_refusal_receipt_distinguishes_access_before_replay(api, monkeypatch):
    """Only a rolled-back, recorded refusal can resolve an uncertain Send's identity."""
    from backend import task_privacy
    from backend.store import Problem

    path, body, key = "/api/v2/chat/ops", {"text": "A synthetic draft"}, str(uuid.uuid4())

    def refuse(*args, **kwargs):
        raise H.Refused("escape", "A rule refused this send")

    with monkeypatch.context() as patch:
        patch.setattr(H, "say", refuse)
        first = api.post(path, json=body, headers=headers(key=key))
    assert first.status_code == 403, first.text
    assert first.json()["error"].get("idempotency_refused") is True, first.text
    replay = api.post(path, json=body, headers=headers(key=key))
    assert replay.status_code == 403, replay.text
    assert replay.json()["error"]["idempotency_refused"] is True
    fresh_key = str(uuid.uuid4())
    sent = api.post(path, json=body, headers=headers(key=fresh_key))
    assert sent.status_code == 200, sent.text

    def deny(*args, **kwargs):
        raise Problem("privacy", "Chat access refused", 403)

    with monkeypatch.context() as patch:
        patch.setattr(task_privacy, "guard_write", deny)
        denied = api.post(path, json=body, headers=headers(key=fresh_key))
    assert denied.status_code == 403, denied.text
    assert "idempotency_refused" not in denied.json()["error"]
    restored = api.post(path, json=body, headers=headers(key=fresh_key))
    assert restored.status_code == 200, restored.text
    assert restored.json()["message"]["id"] == sent.json()["message"]["id"]


def test_the_installers_enrollment_request_needs_an_idempotency_key(api):
    # scripts/install.sh asks for the server computer's code with an empty body and the owner token; without the key
    # the server answers 422 and the install finishes with no computer.
    bare = api.post("/api/v2/enrollments", json={}, headers={"Authorization": "Bearer ana-test"})
    assert bare.status_code == 422 and bare.json()["error"]["code"] == "idempotency_key", bare.text
    keyed = api.post("/api/v2/enrollments", json={}, headers=headers("ana-test", "install-" + uuid.uuid4().hex))
    assert keyed.status_code == 200 and keyed.json()["code"], keyed.text


def test_no_local_owner_or_header_spoofing(api):
    assert api.get("/healthz").status_code == 200
    assert api.get("/api/v2/tasks").status_code == 401
    assert api.get("/", headers={"Cf-Access-Authenticated-User-Email": "ana@acme.example"}).status_code == 401
    assert api.get("/runtime/hub.db", headers=headers()).status_code == 404
    assert api.get("/../registry/employees.yaml", headers=headers()).status_code == 404
    cross_site = api.post(
        "/api/v2/chat/ops",
        json={"text": "Cross-site request"},
        headers={**headers(), "Origin": "https://untrusted.example"},
    )
    assert cross_site.status_code == 403


def test_schema_rejects_forged_actor_and_missing_fields(api):
    post(
        api,
        "tasks",
        {"owner": "assistant", "title": "Create something", "body": "Do it", "requester": "human:ben"},
        expected=422,
    )
    post(api, "tasks", {"owner": "assistant", "title": "Missing body"}, expected=422)
    r = api.post(
        "/api/v2/tasks",
        json={"owner": "assistant", "title": "Create report", "body": "A report"},
        headers={"Authorization": "Bearer ana-test"},
    )
    assert r.status_code == 422


def test_task_and_queue_commit_once_with_concurrent_retries(api):
    body = {"owner": "assistant", "title": "Review the proposal", "body": "Give a recommendation."}
    with ThreadPoolExecutor(max_workers=4) as pool:
        replies = list(pool.map(lambda _: post(api, "tasks", body, key="same-operation"), range(4)))
    assert len({r["id"] for r in replies}) == 1
    assert replies[0]["requester_id"] == "human:ana"
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM tasks").fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1
    post(api, "tasks", {**body, "body": "Changed"}, key="same-operation", expected=409)


def test_task_version_conflict_preserves_first_write(api):
    task = post(api, "tasks", {"owner": "assistant", "title": "Review design", "body": "Recommend improvements."})
    updated = post(api, "tasks/" + task["id"], {"version": 1, "note": "First revision"})
    assert updated["version"] == 2
    post(api, "tasks/" + task["id"], {"version": 1, "note": "Stale revision"}, expected=409)
    assert get(api, "tasks/" + task["id"])["task"]["note"] == "First revision"


def test_the_assistant_chats_only_in_its_own_room_and_people_still_hand_it_work(api):
    """The assistant takes chat in one place, each person's own Assistant room (backend/assistant.py,
    tested in test_assistant.py). Every other route still refuses it, and a person's task for it
    still lands in that person's own room."""
    post(api, "chat/assistant", {"text": "Hello"}, expected=403)
    # `assistant` names the caller's own Assistant (backend/recipients.py), so a message to it lands in their room.
    sent = post(api, "messages", {"to": "assistant", "text": "Hello"}, token="ben-test")
    assert sent["refs"]["assistant"] is True and sent["recipient_id"] == "bot:assistant"
    ana = post(api, "tasks", {"owner": "assistant", "title": "Ana's review", "body": "Private context."})
    ben = post(
        api, "tasks", {"owner": "assistant", "title": "Ben's review", "body": "Private context."}, token="ben-test"
    )
    assert ana["conversation_id"] != ben["conversation_id"]
    get(
        api, "conversations/" + ana["conversation_id"] + "/messages", "ben-test", expected=404
    )  # unreadable looks missing
    post(
        api,
        "messages",
        {"to": "assistant", "text": "Injected", "conversation_id": ana["conversation_id"]},
        token="ben-test",
        expected=403,
    )


def test_shared_room_membership_updates_and_revokes_history_access(api):
    message = post(api, "chat/cpo", {"text": "The group can read this."})
    changed = post(api, "bots/cpo/owners", {"owners": ["cara"], "expected_revision": 1})
    assert [owner["id"] for owner in changed["owners"]] == ["cara"]
    get(api, "conversations/" + message["conversation_id"] + "/messages", "ben-test", expected=404)
    visible = get(api, "conversations/" + message["conversation_id"] + "/messages", "cara-test")
    assert visible[0]["body"] == "The group can read this."


def test_private_task_reference_is_denied(api):
    task = post(api, "tasks", {"owner": "inbox", "title": "Review inbox", "body": "Review private messages."})
    get(api, "tasks/" + task["id"], "cara-test", expected=404)
    post(api, "chat/cpo", {"text": "Read this", "refs": {"task": task["id"]}}, token="cara-test", expected=404)


def test_two_runners_claim_only_their_own_bots(api):
    ana, ben = runner(api), runner(api, "ben", "Ben Mac")
    assign(api, ana, "assistant")
    assign(api, ben, "cpo")
    ready(api, ana, ["assistant", "cpo"])
    ready(api, ben, ["assistant", "cpo"])
    post(api, "chat/cpo", {"text": "Talk to product"})
    assert claim(api, ana) is None
    attempt = claim(api, ben, key="claim-once")
    assert attempt["bot"] == "cpo"
    assert claim(api, ben, key="claim-once")["id"] == attempt["id"]
    assert claim(api, ben) is None


def test_turn_output_and_completion_are_durable_and_idempotent(api):
    r, msg, attempt = setup_attempt(api)
    aid = attempt["id"]
    post(api, f"attempts/{aid}/started", {"thread_id": "local-thread"}, token=r["token"])
    event = {"events": [{"seq": 1, "kind": "message", "payload": {"text": "Answer", "final": True}}]}
    assert post(api, f"attempts/{aid}/events", event, token=r["token"])["ack_seq"] == 1
    assert post(api, f"attempts/{aid}/events", event, token=r["token"])["ack_seq"] == 1
    final = {"outcome": "completed", "text": "A complete answer", "last_seq": 1}
    done = post(api, f"attempts/{aid}/complete", final, token=r["token"], key="completion-once")
    replay = post(api, f"attempts/{aid}/complete", final, token=r["token"], key="completion-once")
    assert done == replay
    messages = get(api, f"conversations/{msg['conversation_id']}/messages")
    assert [m["body"] for m in messages] == [msg["body"], "A complete answer"]
    post(api, "chat/cpo", {"text": "Stale bot token"}, token=attempt["token"], expected=409)


def slept(api, r, seconds=3600, awake_since=None):
    """Age a machine's last contact the way an hour of sleep does."""
    with api.app.state.store.transaction() as c:
        c.execute(
            "UPDATE runner_states SET last_seen_at=?,awake_since=? WHERE runner_id=?",
            (H.shift(H.now(), seconds=-seconds), awake_since, r["runner_id"]),
        )


def awake_since(api, r):
    with api.app.state.store.read() as c:
        return c.execute("SELECT awake_since FROM runners_with_state WHERE id=?", (r["runner_id"],)).fetchone()[
            "awake_since"
        ]


def test_a_files_metadata_follows_the_same_access_as_the_file(api):
    """Name, size and type without the bytes, for the viewer's thumbnails."""
    store = api.app.state.store
    with store.transaction() as c:
        c.execute(
            "INSERT INTO blobs(id,owner,digest,size,name,content_type,created_at) VALUES(?,?,?,?,?,?,?)",
            ("blob-meta-0001", "human:ana", "d" * 64, 2048, "storyboard.png", "image/png", H.now()),
        )
    assert get(api, "files/blob-meta-0001/meta") == {
        "id": "blob-meta-0001",
        "name": "storyboard.png",
        "size": 2048,
        "content_type": "image/png",
    }
    get(api, "files/blob-meta-0001/meta", token="ben-test", expected=403)
    get(api, "files/nope-nope-nope/meta", expected=404)


def test_run_windows_include_failed_runs_and_hide_private_rooms(api):
    machine, message, attempt = setup_attempt(api)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE attempts SET state='expired',finished_at=? WHERE id=?", (H.now(), attempt["id"]))
        # No run record: the lease ended before one was written.
        c.execute(
            "UPDATE attempts SET trigger=NULL,trigger_kind=NULL,trigger_source=NULL,purpose=NULL,settled_at=NULL,"
            "exit=NULL,summary=NULL WHERE id=?",
            (attempt["id"],),
        )
        H.status_set(c, "bot:ops", "ops", "running")
        H.status_set(c, "bot:ops", "ops", "idle")
    rows = get(api, "bots/ops/turns?since=24h")
    failed = next(row for row in rows if row["id"] == attempt["id"])
    assert failed["state"] == failed["exit"] == "expired"
    assert failed["failure_reason"] and "token_hash" not in failed
    assert get(api, "bots/ops/history?since=24h")
    assert get(api, "bots/ops/turns?since=not-a-window", expected=422)["error"]["code"] == "date"
    assert get(api, "bots/ops/history?since=not-a-window", expected=422)["error"]["code"] == "date"
    assert get(api, "bots/ops/turns?since=24h", token="ben-test") == []
    run = next(r for r in api.get("/api/runs", headers=headers()).json() if r["id"] == attempt["id"])
    assert run["state"] == "expired" and "attempt_id" not in run
    assert api.get("/api/runs/" + attempt["id"] + "/log", headers=headers()).status_code == 200
    assert api.get("/api/runs/" + attempt["id"] + "/log", headers=headers("ben-test")).status_code == 404
    issue = next(i for i in get(api, "fleet/check")["issues"] if i["kind"] == "failing_runs")
    assert "expired attempt" in issue["text"] and "last 24 hours" in issue["text"]


def test_runs_list_shows_a_live_run_as_unfinished(api):
    # A run still going has no result yet: GET /api/runs gives it no exit code and no finished time, so the Runs lists
    # do not draw it as a failure that ended when it started.
    r, _, attempt = setup_attempt(api)
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "live-turn"}, token=r["token"])
    for state in ("running", "leased", "input"):
        with api.app.state.store.transaction() as c:
            c.execute("UPDATE attempts SET state=? WHERE id=?", (state, attempt["id"]))
        run = next(r for r in api.get("/api/runs", headers=headers()).json() if r["id"] == attempt["id"])
        assert (run["state"], run["exit"], run["finished_at"]) == (state, None, None)
    # Once it ends, it reads as before: a failure is exit 1 with its finished time.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE attempts SET state='failed',finished_at=? WHERE id=?", (H.now(), attempt["id"]))
    run = next(r for r in api.get("/api/runs", headers=headers()).json() if r["id"] == attempt["id"])
    assert run["state"] == "failed" and run["exit"] == 1 and run["finished_at"]


def test_busy_bot_stays_out_of_claims_after_lease_expiry(api):
    r, _, first = setup_attempt(api)
    post(api, f"attempts/{first['id']}/started", {"thread_id": "live-turn"}, token=r["token"])
    post(api, "chat/ops", {"text": "A separate request"})
    expire(api, first["id"])
    for _ in range(2):
        assert post(api, "jobs/claim", {"busy_bots": ["ops"]}, token=r["token"]) == {"attempt": None}
    # No durable fence: once the supervisor reports the process gone, work resumes.
    replacement = post(api, "jobs/claim", {"busy_bots": []}, token=r["token"])["attempt"]
    assert replacement and replacement["id"] != first["id"]


def test_healthz_answers_while_slow_handlers_hold_every_shared_worker(api):
    # A one-token shared pool, held: every ordinary sync handler would queue behind it.
    held, size = object(), {}

    async def hold_all():
        shared = anyio.to_thread.current_default_thread_limiter()
        size["tokens"], shared.total_tokens = shared.total_tokens, 1
        await shared.acquire_on_behalf_of(held)

    async def release_all():
        shared = anyio.to_thread.current_default_thread_limiter()
        shared.release_on_behalf_of(held)
        shared.total_tokens = size["tokens"]

    api.portal.call(hold_all)
    try:
        assert api.get("/healthz").json()["ok"] is True
    finally:
        api.portal.call(release_all)


def test_a_second_start_over_a_store_still_owing_the_redesign_cutover_finishes(tmp_path):
    """The cutover runs in initialize, one write per object, after the store is ready. A store a restart finds part way
    through it (its record says running) must finish under the next start and not wait on writes the start holds.
    The second start runs on its own thread, so a stall fails here after a minute instead of hanging the run."""
    import json
    import threading
    from backend import redesign_migration

    def record_of(path):
        with closing(sqlite3.connect(path)) as c:
            raw = c.execute("SELECT value FROM registry_metadata WHERE key=?", (redesign_migration.CUTOVER,)).fetchone()
        return json.loads(raw[0])

    settings = api_settings(tmp_path)
    with TestClient(create_app(settings)):
        pass
    assert record_of(settings.db_path)["state"] == "done"
    with closing(sqlite3.connect(settings.db_path)) as c:
        record = record_of(settings.db_path)
        record["state"] = "running"
        c.execute(
            "UPDATE registry_metadata SET value=? WHERE key=?",
            (json.dumps(record, sort_keys=True), redesign_migration.CUTOVER),
        )
        c.commit()

    outcome = []

    def start_again():
        try:
            with TestClient(create_app(settings)):
                pass
            outcome.append("started")
        except BaseException as exc:  # reported to the test thread below
            outcome.append(exc)

    worker = threading.Thread(target=start_again, daemon=True)
    worker.start()
    worker.join(60)
    assert outcome == ["started"], f"the second start did not finish: {outcome!r}"
    assert record_of(settings.db_path)["state"] == "done"
