"""Exercise the network contract with separate humans and runners over real SQLite."""

import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
import yaml
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.auth import Identity
from backend.config import Settings
from backend.execution import AWAKE_SETTLE
from backend.store import H, encode


@pytest.fixture
def api(tmp_path):
    registry = tmp_path / "hub-registry"
    registry.mkdir()
    (registry / "hub-access.yaml").write_text(yaml.safe_dump({
        "owner": "ana@acme.example", "bot_admins": ["ben@acme.example"]}))
    app = create_app(Settings(db_path=tmp_path / "hub.db", registry_dir=registry, test_identities={
        "ana-test": Identity("human:ana", "owner", "ana@acme.example"),
        "ben-test": Identity("human:ben", "human", "ben@acme.example"),
        "cara-test": Identity("human:cara", "human", "cara@acme.example"),
    }))
    with TestClient(app) as client:
        with app.state.store.transaction() as c:
            bots = {slug: {"name": slug, "runtime": "fake", "status": "active"}
                    for slug in ("coo", "ops", "cpo", "product-design", "finance", "inbox", "doc-updater")}
            H.sync_registry(c, bots, {"people": [{"id": p, "email": p + "@acme.example"}
                                               for p in ("ana", "ben", "cara")]})
            c.execute("INSERT INTO registry_metadata VALUES('people',?)", (encode({"people": [
                {"id": "ana", "email": "ana@acme.example", "primary_for": ["*"]},
                {"id": "ben", "email": "ben@acme.example", "primary_for": ["cpo", "product-design", "ops"]},
                {"id": "cara", "email": "cara@acme.example"}]}),))
            for slug, config in bots.items():
                product = slug in ("cpo", "product-design")
                c.execute("INSERT INTO bot_config(bot,config_json,team,operator) VALUES(?,?,?,?)",
                          (slug, encode(config), "product" if product else None, "ben" if product else "ana"))
            # The company's mail bot is Ana's alone: nobody else sees it, reads it or writes to it.
            restrict(c, "inbox", people=["ana"])
            # A company with a fleet this size is long past first run; the wizard belongs to
            # backend/tests/test_onboarding.py, not to every other test's front page.
            c.execute("INSERT INTO registry_metadata VALUES('onboarding',?)",
                      (encode({"completed": "2026-01-01T00:00:00Z"}),))
        yield client


def restrict(c, bot, **audience):
    """Set a bot's access straight in the database: see, read and write all go to `audience`
    (people, teams, bots), or per level with see=, read=, write= each an audience dict."""
    from backend import bot_access as BA
    levels = {level: audience.pop(level) for level in BA.LEVELS if level in audience}
    every = {level: levels.get(level, audience) for level in BA.LEVELS}
    c.execute("UPDATE bot_config SET access_json=? WHERE bot=?",
              (BA.stored(BA.document({level: BA.audience(value) for level, value in every.items()})), bot))


def as_member(api, email):
    """Take an Admin back to a plain member (the fixture makes Ben an admin: an admin manages every bot, so
    a test about what a member may not see needs him to be one)."""
    from backend import access as Access
    with api.app.state.store.transaction() as c:
        stored = Access._load_json(c, Access.ACCESS) or {}
        Access._store(c, Access.ACCESS, {**stored, "admins": [e for e in Access.load_access(c, api.app.state.store.settings)["admins"] if e != email]})
    with api.app.state.store.read() as c:
        api.app.state.auth.sync_access(c)


def headers(token="ana-test", key=None):
    return {"Authorization": "Bearer " + token, "Idempotency-Key": key or str(uuid.uuid4())}


def post(api, path, body, token="ana-test", key=None, expected=200):
    r = api.post("/api/v2/" + path, json=body, headers=headers(token, key))
    assert r.status_code == expected, r.text
    data = r.json()
    if expected == 200 and isinstance(data, dict):
        if path.startswith("chat/"):
            return data["message"]
        if set(data) == {"task"}:
            return data["task"]
    return data


def get(api, path, token="ana-test", expected=200):
    r = api.get("/api/v2/" + path, headers=headers(token))
    assert r.status_code == expected, r.text
    data = r.json()
    if expected == 200 and isinstance(data, dict):
        if path == "conversations":
            return data["conversations"]
        if path.startswith("conversations/") and path.endswith("/messages"):
            return data["messages"]
    return data


def put(api, path, body, token="ana-test", expected=200):
    r = api.put("/api/v2/" + path, json=body, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def runner(api, operator="ana", label="Test Mac"):
    code = post(api, "enrollments", {"operator": operator})["code"]
    return post(api, "runners/enroll", {"code": code, "label": label, "platform": "test"})


def assign(api, r, bot, generation=0, operator="ana-test"):
    return post(api, "bots/" + bot + "/assignment", {"runner_id": r["runner_id"],
                "expected_generation": generation}, token=operator)


def ready(api, r, bots):
    return post(api, "runners/heartbeat", {"version": "test", "platform": "test",
                "readiness": {bot: True for bot in bots}}, token=r["token"])


def claim(api, r, bot=None, key=None):
    return post(api, "jobs/claim", {"bot": bot}, token=r["token"], key=key)["attempt"]


def setup_attempt(api, bot="ops", operator="ana"):
    r = runner(api, operator)
    assign(api, r, bot)
    ready(api, r, [bot])
    if bot == "coo":
        # Nobody chats with the assistant; a person hands it a task.
        msg = post(api, "tasks", {"owner": bot, "title": "Summarize", "body": "Please summarize the current work."})
    else:
        msg = post(api, "chat/" + bot, {"text": "Please summarize the current work."})
    return r, msg, claim(api, r)


def expire(api, aid):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE attempts SET lease_until=? WHERE id=?", (H.shift(H.now(), seconds=-1), aid))


def test_no_local_owner_or_header_spoofing(api):
    assert api.get("/healthz").status_code == 200
    assert api.get("/api/v2/tasks").status_code == 401
    assert api.get("/", headers={"Cf-Access-Authenticated-User-Email": "ana@acme.example"}).status_code == 401
    assert api.get("/runtime/hub.db", headers=headers()).status_code == 404
    assert api.get("/../registry/employees.yaml", headers=headers()).status_code == 404
    cross_site = api.post("/api/v2/chat/ops", json={"text": "Cross-site request"},
                          headers={**headers(), "Origin": "https://untrusted.example"})
    assert cross_site.status_code == 403


def test_schema_rejects_forged_actor_and_missing_fields(api):
    post(api, "tasks", {"owner": "coo", "title": "Create something", "body": "Do it", "requester": "human:ben"}, expected=422)
    post(api, "tasks", {"owner": "coo", "title": "Missing body"}, expected=422)
    r = api.post("/api/v2/tasks", json={"owner": "coo", "title": "Create report", "body": "A report"},
                 headers={"Authorization": "Bearer ana-test"})
    assert r.status_code == 422


def test_task_and_queue_commit_once_with_concurrent_retries(api):
    body = {"owner": "coo", "title": "Review the proposal", "body": "Give a recommendation."}
    with ThreadPoolExecutor(max_workers=4) as pool:
        replies = list(pool.map(lambda _: post(api, "tasks", body, key="same-operation"), range(4)))
    assert len({r["id"] for r in replies}) == 1
    assert replies[0]["requester"] == "human:ana"
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM tasks").fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1
    post(api, "tasks", {**body, "body": "Changed"}, key="same-operation", expected=409)


def test_task_version_conflict_preserves_first_write(api):
    task = post(api, "tasks", {"owner": "coo", "title": "Review design", "body": "Recommend improvements."})
    updated = post(api, "tasks/" + task["id"], {"version": 1, "note": "First revision"})
    assert updated["version"] == 2
    post(api, "tasks/" + task["id"], {"version": 1, "note": "Stale revision"}, expected=409)
    assert get(api, "tasks/" + task["id"])["task"]["note"] == "First revision"


def test_the_assistant_chats_only_in_its_own_room_and_people_still_hand_it_work(api):
    """The assistant takes chat in one place, each person's own Assistant room (backend/assistant.py,
    tested in test_assistant.py). Every other route still refuses it, and a person's task for it
    still lands in that person's own room."""
    post(api, "chat/coo", {"text": "Hello"}, expected=403)
    post(api, "messages", {"to": "coo", "text": "Hello"}, token="ben-test", expected=403)
    ana = post(api, "tasks", {"owner": "coo", "title": "Ana's review", "body": "Private context."})
    ben = post(api, "tasks", {"owner": "coo", "title": "Ben's review", "body": "Private context."},
                  token="ben-test")
    assert ana["conversation_id"] != ben["conversation_id"]
    get(api, "conversations/" + ana["conversation_id"] + "/messages", "ben-test", expected=403)
    post(api, "messages", {"to": "coo", "text": "Injected", "conversation_id": ana["conversation_id"]},
         token="ben-test", expected=403)


def test_shared_room_membership_updates_and_revokes_history_access(api):
    message = post(api, "chat/cpo", {"text": "The group can read this."})
    changed = post(api, "bots/cpo/owners", {"owners": ["cara"], "expected_revision": 1})
    assert [owner["id"] for owner in changed["owners"]] == ["cara"]
    get(api, "conversations/" + message["conversation_id"] + "/messages", "ben-test", expected=403)
    visible = get(api, "conversations/" + message["conversation_id"] + "/messages", "cara-test")
    assert visible[0]["body"] == "The group can read this."


def test_private_task_reference_is_denied(api):
    task = post(api, "tasks", {"owner": "inbox", "title": "Review inbox", "body": "Review private messages."})
    get(api, "tasks/" + task["id"], "cara-test", expected=404)
    post(api, "chat/cpo", {"text": "Read this", "refs": {"task": task["id"]}}, token="cara-test", expected=404)


def test_two_runners_claim_only_their_own_bots(api):
    ana, ben = runner(api), runner(api, "ben", "Ben Mac")
    assign(api, ana, "coo")
    assign(api, ben, "cpo")
    ready(api, ana, ["coo", "cpo"])
    ready(api, ben, ["coo", "cpo"])
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
        c.execute("UPDATE runners SET last_seen=?,awake_since=? WHERE id=?",
                  (H.shift(H.now(), seconds=-seconds), awake_since, r["runner_id"]))


def settled(api, r):
    """A machine that came back and has been reporting in ever since."""
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runners SET awake_since=? WHERE id=?",
                  (H.shift(H.now(), seconds=-AWAKE_SETTLE - 1), r["runner_id"]))


def awake_since(api, r):
    with api.app.state.store.read() as c:
        return c.execute("SELECT awake_since FROM runners WHERE id=?", (r["runner_id"],)).fetchone()["awake_since"]


def test_approval_decision_and_consumption_have_separate_authority(api):
    r, _, a = setup_attempt(api)
    payload = {"to": "colleague@acme.example", "cc": [], "subject": "Draft", "body_sha256": "a" * 64, "mailbox": "work"}
    approval = post(api, "approvals", {"kind": "send", "payload": payload}, token=a["token"])
    path = "approvals/" + approval["id"]
    post(api, path, {"decision": "approved"}, token="ben-test", expected=403)
    post(api, path, {"decision": "approved"})
    post(api, path + "/consume", {"payload_hash": "b" * 64}, token=a["token"], expected=403)
    result = post(api, path + "/consume", {"payload_hash": approval["payload_hash"]}, token=a["token"], key="send-op")
    assert result["consumed_at"]
    assert post(api, path + "/consume", {"payload_hash": approval["payload_hash"]}, token=a["token"], key="send-op") == result
    post(api, path + "/consume", {"payload_hash": approval["payload_hash"]}, token=a["token"], expected=422)


def test_a_files_metadata_follows_the_same_access_as_the_file(api):
    """Name, size and type without the bytes, for the viewer's thumbnails."""
    store = api.app.state.store
    with store.transaction() as c:
        c.execute("INSERT INTO blobs(id,owner,digest,size,name,content_type,created) VALUES(?,?,?,?,?,?,?)",
                  ("blob-meta-0001", "human:ana", "d" * 64, 2048, "storyboard.png", "image/png", H.now()))
    assert get(api, "files/blob-meta-0001/meta") == {"id": "blob-meta-0001", "name": "storyboard.png", "size": 2048,
                                                     "content_type": "image/png"}
    get(api, "files/blob-meta-0001/meta", token="ben-test", expected=403)
    get(api, "files/nope-nope-nope/meta", expected=404)


def test_run_windows_include_failed_attempts_without_turns_and_hide_private_rooms(api):
    machine, message, attempt = setup_attempt(api)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE attempts SET state='expired',finished=? WHERE id=?", (H.now(), attempt["id"]))
        c.execute("DELETE FROM turns WHERE id=?", (attempt["id"],))
        H.status_set(c, "bot:ops", "ops", "running")
        H.status_set(c, "bot:ops", "ops", "idle")
    rows = get(api, "bots/ops/turns?since=24h")
    failed = next(row for row in rows if row["id"] == attempt["id"])
    assert failed["record_kind"] == "attempt" and failed["exit"] == "expired"
    assert failed["failure_reason"] and "token_hash" not in failed
    assert get(api, "bots/ops/history?since=24h")
    assert get(api, "bots/ops/turns?since=not-a-window", expected=422)["error"]["code"] == "date"
    assert get(api, "bots/ops/history?since=not-a-window", expected=422)["error"]["code"] == "date"
    assert get(api, "bots/ops/turns?since=24h", token="ben-test") == []
    run = next(r for r in api.get("/api/runs", headers=headers()).json() if r["run"] == attempt["id"])
    assert run["record_kind"] == "attempt" and run["attempt_id"] == attempt["id"]
    assert api.get("/api/runs/" + attempt["id"] + "/log", headers=headers()).status_code == 200
    assert api.get("/api/runs/" + attempt["id"] + "/log", headers=headers("ben-test")).status_code == 404
    issue = next(i for i in get(api, "fleet/check")["issues"] if i["kind"] == "failing_runs")
    assert "expired attempt" in issue["text"] and "last 24 hours" in issue["text"]


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
