"""Current live-meeting membership also fences retained, derived work."""
import asyncio

import pytest
from starlette.requests import Request

from backend import live_meetings as meetings
from backend import task_privacy
from backend.auth import Identity
from backend.store import H, Problem, encode
from backend.tests.test_api import assign, get, headers, post, ready, runner
from backend.tests.test_live_meetings import api, attach, chunk, live  # noqa: F401
from backend.tests.test_botops_parity import act
from backend.tests.test_member_bots import botops, turn as botops_turn  # noqa: F401
from backend.tests.test_sql import query


def private(api, token="ana-test"):
    return post(api, "live-meetings", {"title": "Synthetic restricted planning", "visibility": "private"}, token=token)["id"]


def test_removed_human_cannot_replay_join_view(live):
    mid = private(live)
    chunk(live, mid, 1, 0, 1000, "Synthetic restricted transcript")
    post(live, f"live-meetings/{mid}/access", {"add": ["ben"]})
    receipt = post(live, f"live-meetings/{mid}/join", {}, token="ben-test", key="joined-once")
    assert receipt["chunks"][0]["text"] == "Synthetic restricted transcript"
    post(live, f"live-meetings/{mid}/access", {"remove": ["ben"]})
    post(live, f"live-meetings/{mid}/join", {}, token="ben-test", key="joined-once", expected=404)


def test_removed_bot_cannot_replay_turn_receipt(live):
    mid = private(live)
    attach(live, mid, "ops")
    chunk(live, mid, 1, 0, 1000, "Ops, review this synthetic plan")
    turn = get(live, f"live-meetings/{mid}/turns", token="ops-live")["turns"][0]
    path = f"live-meetings/{mid}/turns/{turn['id']}/claim"
    post(live, path, {}, token="ops-live", key="claimed-once")
    removed = live.request("DELETE", f"/api/v2/live-meetings/{mid}/bots/ops", json={}, headers=headers())
    assert removed.status_code == 200, removed.text
    post(live, path, {}, token="ops-live", key="claimed-once", expected=404)


def test_only_connector_expands_private_audience_and_team_members_keep_bot_attach(live):
    mid = private(live)
    post(live, f"live-meetings/{mid}/access", {"add": ["ben"]})
    post(live, f"live-meetings/{mid}/join", {}, token="ben-test")
    for body in ({"add": ["cara"]}, {"visibility": "team"}):
        post(live, f"live-meetings/{mid}/access", body, token="ben-test", expected=403)
    post(live, f"live-meetings/{mid}/bots", {"bots": ["ops"]}, token="ben-test", expected=403)
    assert attach(live, mid, "ops")["added"] == ["ops"]
    post(live, f"live-meetings/{mid}/access", {"add": ["cara"]}, token="ops-live", expected=403)
    team = post(live, "live-meetings", {"title": "Synthetic team planning"})["id"]
    post(live, f"live-meetings/{team}/join", {}, token="ben-test")
    assert attach(live, team, "ops", token="ben-test")["added"] == ["ops"]
    chunk(live, team, 1, 0, 1000, "Ops, review this synthetic team plan")
    # Tightening the audience also revokes grants made by implicit team participants.
    narrowed = post(live, f"live-meetings/{team}/access", {"visibility": "private"})
    assert narrowed["router"]["turns"][0]["status"] == "skipped"
    get(live, f"live-meetings/{team}", token="ops-live", expected=404)
    # A retained pre-correction attachment made by an added human is removed on revocation.
    with live.app.state.store.transaction() as c:
        c.execute("INSERT INTO live_meeting_bots(meeting_id,bot,joined_by,joined_at) VALUES(?,?,?,?)",
                  (mid, "finance", "human:ben", H.now()))
    post(live, f"live-meetings/{mid}/access", {"remove": ["ben"]})
    with live.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM live_meeting_bots WHERE meeting_id=? AND bot='finance'", (mid,)).fetchone()


def test_team_to_private_retains_explicit_people_not_joiners_or_open_stream(live, monkeypatch):
    mid = post(live, "live-meetings", {"title": "Synthetic audience transition"})["id"]
    post(live, f"live-meetings/{mid}/access", {"add": ["cara"]})
    for token in ("ben-test", "cara-test"):
        post(live, f"live-meetings/{mid}/join", {}, token=token)
    route = next(r for r in live.app.routes if getattr(r, "path", "") == "/api/v2/live-meetings/{rid}/events")
    request = Request({"type": "http", "method": "GET", "path": f"/api/v2/live-meetings/{mid}/events",
                       "headers": [(b"authorization", b"Bearer ben-test")], "query_string": b""})
    request.state.identity = Identity("human:ben", "human", "ben@acme.example")

    async def connected(_request):
        return False

    async def no_sleep(_seconds):
        pass

    monkeypatch.setattr(Request, "is_disconnected", connected)
    monkeypatch.setattr(meetings.asyncio, "sleep", no_sleep)

    async def transition():
        response = await route.endpoint(request, mid)
        iterator = response.body_iterator
        while not (await iterator.__anext__()).startswith(": keep-alive"):
            pass
        changed = post(live, f"live-meetings/{mid}/access", {"visibility": "private"})
        after = await iterator.__anext__()
        await iterator.aclose()
        return changed, after

    changed, after = asyncio.run(transition())
    assert after == "event: expired\ndata: {}\n\n"
    assert {p["actor"] for p in changed["humans"]} == {"human:ana", "human:cara"}
    assert mid not in {r["id"] for r in get(live, "live-meetings", token="ben-test")["meetings"]}
    for suffix in ("", "/events"):
        get(live, f"live-meetings/{mid}{suffix}", token="ben-test", expected=404)
    # Pulling turns is bot-only regardless of whether a meeting exists.
    get(live, f"live-meetings/{mid}/turns", token="ben-test", expected=403)
    post(live, f"live-meetings/{mid}/join", {}, token="ben-test", expected=404)
    assert get(live, f"live-meetings/{mid}", token="cara-test")["id"] == mid


def test_unattached_bot_private_meeting_is_indistinguishable_from_unknown(live):
    mid = private(live)
    for suffix in ("", "/events", "/turns"):
        known = get(live, f"live-meetings/{mid}{suffix}", token="finance-live", expected=404)
        unknown = get(live, f"live-meetings/not-a-meeting{suffix}", token="finance-live", expected=404)
        assert known == unknown


@pytest.fixture
def derived(live):
    # The company owner is deliberately not a member of the connector's private meeting.
    mid = private(live, token="ben-test")
    attach(live, mid, "ops", token="ben-test")
    chunk(live, mid, 1, 0, 1000, "Ops, review this synthetic restricted plan", token="ben-test")
    machine = runner(live)
    assign(live, machine, "ops")
    ready(live, machine, ["ops"])
    attempt = post(live, "jobs/claim", {"bot": "ops"}, token=machine["token"])["attempt"]
    with live.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET team='engineering' WHERE bot='ops'")
        c.execute("INSERT INTO attempt_captures(attempt_id,bot,provider,frames,truncated,dropped_bytes,bytes,created) "
                  "VALUES(?,?,?,?,0,0,2,?)", (attempt["id"], "ops", "synthetic", "[]", H.now()))
    return mid, attempt


def test_private_derived_conversation_message_sql_and_run_hide_from_nonmember_owner(live, derived):
    mid, attempt = derived
    msg, aid = attempt["message"], attempt["id"]
    cid = msg["conversation_id"]
    assert cid not in {row["id"] for row in get(live, "conversations")}
    get(live, f"conversations/{cid}/messages", expected=404)
    get(live, f"messages/{msg['id']}", expected=404)
    for table, identifier in (("conversations", cid), ("messages", msg["id"]), ("attempts", aid)):
        assert query(live, f"SELECT * FROM {table} WHERE id=?", params=[identifier])["rows"] == []
    get(live, f"attempts/{aid}/capture", expected=404)
    get(live, f"turns/{aid}/steps", expected=404)
    assert get(live, f"messages/{msg['id']}", token="ben-test")["id"] == msg["id"]
    assert get(live, f"messages/{msg['id']}", token="ops-live")["id"] == msg["id"]
    assert query(live, "SELECT id FROM conversations WHERE id=?", token="ben-test", params=[cid])["rows"] == [[cid]]
    # Retained rooms created before the explicit binding still restrict owner inspection.
    with live.app.state.store.transaction() as c:
        c.execute("UPDATE conversations SET room_key=NULL WHERE id=?", (cid,))
    assert cid not in {row["id"] for row in get(live, "conversations")}
    assert query(live, "SELECT id FROM conversations WHERE id=?", params=[cid])["rows"] == []
    assert get(live, f"messages/{msg['id']}", token="ops-live")["id"] == msg["id"]


def test_removed_bot_loses_retained_derived_history_and_run(live, derived):
    mid, attempt = derived
    msg, aid = attempt["message"], attempt["id"]
    assert get(live, f"messages/{msg['id']}", token="ops-live")["id"] == msg["id"]
    assert get(live, f"attempts/{aid}/capture", token="ops-live")["attempt"]["id"] == aid
    removed = live.request("DELETE", f"/api/v2/live-meetings/{mid}/bots/ops", json={}, headers=headers("ben-test"))
    assert removed.status_code == 200, removed.text
    get(live, f"conversations/{msg['conversation_id']}/messages", token="ops-live", expected=404)
    get(live, f"messages/{msg['id']}", token="ops-live", expected=404)
    get(live, f"attempts/{aid}/capture", token="ops-live", expected=404)
    assert query(live, "SELECT id FROM messages WHERE id=?", token="ops-live", params=[msg["id"]])["rows"] == []
    assert get(live, f"messages/{msg['id']}", token="ben-test")["id"] == msg["id"]


def test_message_references_cannot_grant_membership_of_an_unrelated_room(live):
    mid = post(live, "live-meetings", {"title": "Synthetic team discussion"})["id"]
    post(live, "chat/ops", {"text": "Synthetic personal chat",
                            "refs": {"live_meeting": {"meeting_id": mid}}}, expected=422)
    # Retained synthetic data bypasses input validation to exercise the read boundary itself.
    with live.app.state.store.transaction() as c:
        conv = H.open_conversation(c, "human:ana", ["bot:ops"], kind="chat", subject="Synthetic personal chat")
        message = H.say(c, "human:ana", "bot:ops", "Synthetic personal chat", conversation_id=conv["id"])
        c.execute("UPDATE messages SET refs_json=? WHERE id=?", (encode({"live_meeting": {"meeting_id": mid}}), message["id"]))
    cid = message["conversation_id"]
    assert get(live, f"conversations/{cid}/messages", token="cara-test", expected=403)
    assert query(live, "SELECT id FROM conversations WHERE id=?", token="cara-test", params=[cid])["rows"] == []


def test_room_provenance_is_reused_only_within_its_read_snapshot(live, derived):
    mid, attempt = derived
    cid = attempt["message"]["conversation_id"]
    with live.app.state.store.transaction() as c:
        c.execute("UPDATE conversations SET room_key=NULL WHERE id=?", (cid,))
        for n in range(30):
            c.execute("INSERT INTO messages(id,conversation_id,from_actor,to_actor,kind,body,refs_json,created) "
                      "VALUES(?,?,'bot:ops','keeper','say','Synthetic retained response','{}',?)",
                      (f"synthetic-room-response-{n}", cid, H.now()))
    statements = []
    with live.app.state.store.read() as c:
        c.set_trace_callback(statements.append)
        rows = list(c.execute("SELECT * FROM messages WHERE conversation_id=?", (cid,)))
        assert all(task_privacy.message_readable(c, "bot:ops", row, current=True) for row in rows)
        c.set_trace_callback(None)
    scans = [s for s in statements if "JOIN live_meeting_turns" in s]
    assert len(scans) == 1, f"room provenance was scanned {len(scans)} times for one page"
    removed = live.request("DELETE", f"/api/v2/live-meetings/{mid}/bots/ops", json={}, headers=headers("ben-test"))
    assert removed.status_code == 200, removed.text
    with live.app.state.store.read() as c:
        assert not any(task_privacy.message_readable(c, "bot:ops", row) for row in rows)


def publication_responses(api, token):
    team_task = post(api, "tasks", {"owner": "ops", "title": "Review synthetic team plan",
                                   "body": "Check the synthetic plan.", "private": False})
    writes = {
        "human": ("messages", {"to": "ana", "kind": "ask", "text": "Synthetic meeting summary?"}),
        "bot": ("messages", {"to": "finance", "text": "Review this synthetic meeting summary."}),
        "task": ("tasks", {"owner": "finance", "title": "Review synthetic meeting plan",
                           "body": "Review this synthetic summary.", "private": False}),
        "comment": (f"tasks/{team_task['id']}/comments", {"text": "Synthetic meeting summary."}),
        "status": ("bots/ops/status", {"focus": "Synthetic meeting summary."}),
    }
    return {name: api.post("/api/v2/" + path, json=body, headers=headers(token))
            for name, (path, body) in writes.items()}


def file_metadata_responses(api, token):
    task = post(api, "tasks", {"owner": "ops", "title": "Review synthetic shared file",
                              "body": "Check this synthetic reference.", "private": False})
    existing = post(api, "files/uploads", {"name": "synthetic-public.md", "text": "Synthetic public file",
                                           "title": "Public synthetic reference", "task": task["id"]}, token="ops-live")["file"]
    responses = {
        "link": api.post("/api/v2/files/links", json={"url": "https://acme.example/synthetic-meeting",
                         "title": "Synthetic meeting context", "task": task["id"]}, headers=headers(token)),
        "edit": api.patch(f"/api/v2/files/{existing['id']}", json={"title": "Synthetic meeting context"},
                          headers=headers(token)),
        "upload": api.post("/api/v2/files/uploads", json={"name": "synthetic-public.md", "text": "Synthetic public file",
                           "title": "Synthetic meeting context", "task": task["id"]}, headers=headers(token)),
    }
    return responses, existing["id"]


def test_private_meeting_execution_cannot_publish_unversioned_file_metadata(live, derived):
    _mid, attempt = derived
    responses, fid = file_metadata_responses(live, attempt["token"])
    assert {name: r.status_code for name, r in responses.items()} == {"link": 403, "edit": 403, "upload": 403}, {
        name: (r.status_code, r.text) for name, r in responses.items()}
    public = get(live, "bots/ops/files")["files"]
    assert next(row for row in public if row["id"] == fid)["title"] == "Public synthetic reference"
    assert not any(row["title"] == "Synthetic meeting context" for row in public)


def test_runner_control_write_guard_does_not_scan_execution_provenance(live, derived):
    _mid, attempt = derived
    statements = []
    with live.app.state.store.read() as c:
        c.execute("BEGIN")
        c.set_trace_callback(statements.append)
        who = Identity("bot:ops", "bot", attempt_id=attempt["id"])
        task_privacy.guard_write(c, who, f"/api/v2/attempts/{attempt['id']}/events", live.app.state.auth)
        task_privacy.guard_write(c, who, "/api/v2/jobs/claim", live.app.state.auth)
        c.set_trace_callback(None)
    assert not statements, f"runner control guard performed {len(statements)} provenance queries"


def test_private_meeting_execution_cannot_publish_to_nonmembers_and_can_reply(live, derived):
    mid, attempt = derived
    responses = publication_responses(live, attempt["token"])
    assert {name: r.status_code for name, r in responses.items()} == dict.fromkeys(responses, 403), {
        name: (r.status_code, r.text) for name, r in responses.items()}
    with live.app.state.store.read() as c:
        who = Identity("bot:ops", "bot", attempt_id=attempt["id"])
        assert task_privacy.private_execution(c, who)
        for recipient in ("human:ana", "bot:finance"):
            with pytest.raises(Problem) as refused:
                task_privacy.require_destination(c, who, recipient, None, {})
            assert refused.value.code == "privacy"
    turn = attempt["message"]["refs"]["live_meeting"]["turn_id"]
    reply = post(live, f"live-meetings/{mid}/chat", {"text": "Synthetic scoped response", "turn_id": turn},
                 token=attempt["token"])
    assert reply["message"]["text"] == "Synthetic scoped response"
    assert get(live, f"live-meetings/{mid}", token="ben-test")["chat"][0]["text"] == "Synthetic scoped response"
    made = post(live, "files/uploads", {"name": "synthetic-meeting.md", "text": "Synthetic scoped file"},
                token=attempt["token"])
    fid = made["file"]["id"]
    assert live.get(f"/api/v2/files/{fid}", headers=headers("ben-test")).content == b"Synthetic scoped file"
    get(live, f"files/{fid}", expected=404)


def test_team_meeting_execution_keeps_ordinary_publication(live):
    mid = post(live, "live-meetings", {"title": "Synthetic team plan"}, token="ben-test")["id"]
    attach(live, mid, "ops", token="ben-test")
    chunk(live, mid, 1, 0, 1000, "Ops, review this synthetic team plan", token="ben-test")
    machine = runner(live)
    assign(live, machine, "ops")
    ready(live, machine, ["ops"])
    attempt = post(live, "jobs/claim", {"bot": "ops"}, token=machine["token"])["attempt"]
    assert attempt["message"]["refs"]["live_meeting"]["meeting_id"] == mid
    responses = publication_responses(live, attempt["token"])
    assert {name: r.status_code for name, r in responses.items()} == dict.fromkeys(responses, 200), {
        name: (r.status_code, r.text) for name, r in responses.items()}
    file_responses, fid = file_metadata_responses(live, attempt["token"])
    assert {name: r.status_code for name, r in file_responses.items()} == {"link": 200, "edit": 200, "upload": 200}, {
        name: (r.status_code, r.text) for name, r in file_responses.items()}
    assert next(row for row in get(live, "bots/ops/files")["files"] if row["id"] == fid)["title"] == "Synthetic meeting context"


@pytest.mark.parametrize("visibility", ["private", "team"])
def test_meeting_context_read_does_not_allow_unscoped_room_writes(live, visibility):
    mid = post(live, "live-meetings", {"title": "Synthetic scoped discussion", "visibility": visibility})["id"]
    if visibility == "private":
        post(live, f"live-meetings/{mid}/access", {"add": ["ben"]})
    attach(live, mid, "ops")
    chunk(live, mid, 1, 0, 1000, "Ops, review this synthetic plan")
    with live.app.state.store.read() as c:
        source = c.execute("SELECT * FROM messages WHERE json_extract(refs_json,'$.live_meeting.meeting_id')=?",
                           (mid,)).fetchone()
        cid, source_id = source["conversation_id"], source["id"]
        before = c.execute("SELECT count(*) FROM messages").fetchone()[0]
    assert get(live, f"conversations/{cid}/messages", token="ben-test")[0]["id"] == source_id

    requests = [
        ("messages", {"to": "ops", "text": "Synthetic ordinary write", "conversation_id": cid}),
        (f"conversations/{cid}/messages", {"text": "Synthetic ordinary reply"}),
        ("messages", {"to": "ops", "text": "Synthetic reply reference", "in_reply_to": source_id}),
    ]
    responses = [live.post("/api/v2/" + path, json=body, headers=headers("ben-test")) for path, body in requests]
    assert [r.status_code for r in responses] == [403] * len(requests), [(r.status_code, r.text) for r in responses]
    with live.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM messages").fetchone()[0] == before
    assert get(live, f"conversations/{cid}/messages", token="ben-test")[0]["id"] == source_id


@pytest.mark.parametrize("surface", ["comment", "file_link"])
def test_meeting_visibility_is_rechecked_under_the_write_lock(live, monkeypatch, surface):
    mid = post(live, "live-meetings", {"title": "Synthetic transition during request"}, token="ben-test")["id"]
    attach(live, mid, "ops", token="ben-test")
    chunk(live, mid, 1, 0, 1000, "Ops, review this synthetic plan", token="ben-test")
    machine = runner(live)
    assign(live, machine, "ops")
    ready(live, machine, ["ops"])
    attempt = post(live, "jobs/claim", {"bot": "ops"}, token=machine["token"])["attempt"]
    task = post(live, "tasks", {"owner": "ops", "title": "Review synthetic team plan",
                              "body": "Check the synthetic plan.", "private": False})
    store = live.app.state.store
    write = store.mutate
    path = f"/api/v2/tasks/{task['id']}/comments" if surface == "comment" else "/api/v2/files/links"
    body = ({"text": "Synthetic scoped context"} if surface == "comment" else
            {"url": "https://acme.example/synthetic-context", "title": "Synthetic scoped context", "task": task["id"]})

    def narrowed_before_write(identity, operation, key, body, fn, check=None):
        if operation == path:
            with store.transaction() as c:
                c.execute("UPDATE live_meetings SET visibility='private' WHERE id=?", (mid,))
        return write(identity, operation, key, body, fn, check=check)

    monkeypatch.setattr(store, "mutate", narrowed_before_write)
    response = live.post(path, json=body, headers=headers(attempt["token"]))
    assert response.status_code == 403, response.text


def delegated_connect(api, attempt, body, key):
    return api.post("/api/v2/live-meetings", json=body,
                    headers={**headers(attempt["token"], key), "X-Tico-On-Behalf-Of": "turn"})


def test_delegated_meeting_access_requires_human_and_source_bot_membership(live, botops, monkeypatch):
    mid = private(live)
    chunk(live, mid, 1, 0, 1000, "Synthetic private audience context")
    post(live, f"live-meetings/{mid}/access", {"add": ["ben"]})
    attempt = botops_turn(live, botops, person="ben-test", text="Inspect this synthetic meeting")

    async def disconnected(_request):
        return True

    monkeypatch.setattr(Request, "is_disconnected", disconnected)

    def denied():
        assert act(live, attempt, "GET", f"live-meetings/{mid}").status_code == 404
        assert mid not in {m["id"] for m in act(live, attempt, "GET", "live-meetings").json()["meetings"]}
        assert act(live, attempt, "GET", f"live-meetings/{mid}/events").status_code == 404
        response = live.post(f"/api/v2/live-meetings/{mid}/join", json={}, headers={
            **headers(attempt["token"], "delegated-join-retry"), "X-Tico-On-Behalf-Of": "turn"})
        assert response.status_code == 404, response.text

    denied()
    attach(live, mid, "botops")
    assert act(live, attempt, "GET", f"live-meetings/{mid}").status_code == 200
    assert act(live, attempt, "GET", f"live-meetings/{mid}/events").status_code == 200
    response = live.post(f"/api/v2/live-meetings/{mid}/join", json={}, headers={
        **headers(attempt["token"], "delegated-join-retry"), "X-Tico-On-Behalf-Of": "turn"})
    assert response.status_code == 200 and response.json()["chunks"][0]["text"] == "Synthetic private audience context"
    polls, removed = 0, []

    async def revoke_after_first_poll(_request):
        nonlocal polls
        polls += 1
        if polls == 2:
            removed.append(await asyncio.to_thread(live.request, "DELETE",
                f"/api/v2/live-meetings/{mid}/bots/botops", json={}, headers=headers()))
        return False

    async def no_sleep(_seconds):
        pass

    with monkeypatch.context() as stream_patch:
        stream_patch.setattr(Request, "is_disconnected", revoke_after_first_poll)
        stream_patch.setattr(meetings.asyncio, "sleep", no_sleep)
        stream = act(live, attempt, "GET", f"live-meetings/{mid}/events")
    assert removed[0].status_code == 200
    assert "event: expired\ndata: {}" in stream.text
    denied()
    attach(live, mid, "botops")
    post(live, f"live-meetings/{mid}/access", {"remove": ["ben"]})
    denied()


@pytest.mark.parametrize("client_id", ["synthetic-connect-replay", None])
def test_delegated_connect_replays_recheck_current_private_audience(live, botops, client_id):
    body = {"title": "Synthetic private connection", "visibility": "private" if client_id else "team"}
    if client_id:
        body["client_id"] = client_id
    mid = post(live, "live-meetings", body, token="ben-test", key="direct-connect-key")["id"]
    if not client_id:
        post(live, f"live-meetings/{mid}/access", {"visibility": "private"}, token="ben-test")
    attempt = botops_turn(live, botops, person="ben-test", text="Inspect this synthetic connection")
    assert delegated_connect(live, attempt, body, "direct-connect-key").status_code == 404
    if client_id:
        assert act(live, attempt, "POST", "live-meetings", body).status_code == 404
    attach(live, mid, "botops", token="ben-test")
    replay = delegated_connect(live, attempt, body, "direct-connect-key")
    assert replay.status_code == 200 and replay.json()["id"] == mid
    if client_id:
        assert act(live, attempt, "POST", "live-meetings", body).json()["id"] == mid
    removed = live.request("DELETE", f"/api/v2/live-meetings/{mid}/bots/botops", json={}, headers=headers("ben-test"))
    assert removed.status_code == 200
    assert delegated_connect(live, attempt, body, "direct-connect-key").status_code == 404
    assert post(live, "live-meetings", body, token="ben-test", key="direct-connect-key")["id"] == mid


def test_delegated_private_creation_requires_direct_connection_and_team_stays_open(live, botops):
    attempt = botops_turn(live, botops, person="ben-test", text="Connect this synthetic meeting")
    created = act(live, attempt, "POST", "live-meetings", {"title": "Synthetic private connect", "visibility": "private"})
    assert created.status_code == 403, created.text
    with live.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM live_meetings").fetchone()[0] == 0
    team = act(live, attempt, "POST", "live-meetings", {"title": "Synthetic team connect"})
    assert team.status_code == 200, team.text
    mid = team.json()["id"]
    assert act(live, attempt, "GET", f"live-meetings/{mid}").status_code == 200
    assert mid in {m["id"] for m in act(live, attempt, "GET", "live-meetings").json()["meetings"]}
    assert act(live, attempt, "POST", f"live-meetings/{mid}/join", {}).status_code == 200


def test_shared_message_provenance_stays_bounded_and_permissions_stay_current(live, derived, monkeypatch):
    from backend import live_privacy
    mid, attempt = derived
    cid = attempt["message"]["conversation_id"]
    with live.app.state.store.transaction() as c:
        c.execute("INSERT INTO messages(id,conversation_id,from_actor,to_actor,kind,body,refs_json,created) "
                  "VALUES('shared-common',?,'bot:ops','keeper','say','Synthetic shared parent','{}',?)", (cid, H.now()))
        for n in range(100):
            c.execute("INSERT INTO messages(id,conversation_id,from_actor,to_actor,kind,body,refs_json,created,in_reply_to) "
                      "VALUES(?,?,'bot:ops','keeper','say','Synthetic shared chain',?,?,?)",
                      (f"shared-chain-{n}", cid, encode({"answers": ["shared-common"]}),
                       H.now(), f"shared-chain-{n-1}" if n else None))
    calls = []
    original = live_privacy.Sources.references

    def counted(self, value):
        if isinstance(value, dict):
            calls.append(1)
        return original(self, value)

    monkeypatch.setattr(live_privacy.Sources, "references", counted)
    task_calls = []
    original_tasks = task_privacy.references

    def task_counted(c, value):
        task_calls.append(1)
        return original_tasks(c, value)

    monkeypatch.setattr(task_privacy, "references", task_counted)
    with live.app.state.store.transaction() as c:
        index = live_privacy.sources(c)
        for n in range(99):
            ids = index.message(H.message(c, f"shared-chain-{n}"), include_run=False)
            assert ids == {mid} and index.readable("bot:ops", ids)
            assert task_privacy.message_tasks(c, H.message(c, f"shared-chain-{n}")) == task_privacy.message_tasks(c, H.message(c, "shared-common"))
        # Shared reply chains must cost their distinct messages, not every prefix again.
        assert len(calls) <= 110, len(calls)
        assert len(task_calls) <= 110, len(task_calls)
        with pytest.raises(Problem, match="too deep"):
            index.message(H.message(c, "shared-chain-99"), include_run=False)
        # Permission results are never memoized, even when provenance is reused.
        c.execute("DELETE FROM live_meeting_bots WHERE meeting_id=? AND bot='ops'", (mid,))
        assert not index.readable("bot:ops", ids)
        # A changed source within the same write transaction discards neutral provenance.
        c.execute("UPDATE messages SET refs_json=? WHERE id='shared-chain-0'",
                  (encode({"live_meetings": ["missing-meeting"]}),))
        fresh = live_privacy.sources(c)
        assert fresh.message(H.message(c, "shared-chain-98"), include_run=False) == {mid, "missing-meeting"}
        assert not fresh.readable("human:ben", {"missing-meeting"})
        # A diamond and a cycle through a reusable closure keep the same reachable sources.
        c.execute("UPDATE messages SET refs_json=? WHERE id='shared-chain-2'",
                  (encode({"answers": ["shared-common", "shared-chain-0"]}),))
        c.execute("UPDATE messages SET in_reply_to='shared-chain-3' WHERE id='shared-chain-0'")
        cyclic = live_privacy.sources(c)
        expected = {mid, "missing-meeting"}
        assert cyclic.message(H.message(c, "shared-chain-3"), include_run=False) == expected
        assert cyclic.message(H.message(c, "shared-chain-4"), include_run=False) == expected



def test_shared_attempt_provenance_rechecks_membership_and_source_commits(live, derived):
    from backend import live_privacy
    mid, attempt = derived
    store, aid = live.app.state.store, attempt["id"]
    with store.read_transaction() as c:
        assert live_privacy.sources(c).attempt_readable("bot:ops", aid)
        assert live_privacy.sources(c).conversation(attempt["message"]["conversation_id"]) == {mid}
    # An unrelated committed write preserves neutral provenance without caching policy.
    with store.transaction() as c:
        c.execute("UPDATE live_meetings SET title=title||' updated' WHERE id=?", (mid,))
    statements = []
    with store.read_transaction() as c:
        c.set_trace_callback(statements.append)
        assert live_privacy.sources(c).attempt(aid) == {mid}
        assert live_privacy.sources(c).conversation(attempt["message"]["conversation_id"]) == {mid}
        c.set_trace_callback(None)
    assert not any("JOIN jobs j ON j.id=a.job_id" in sql for sql in statements), statements
    with store.transaction() as c:
        c.execute("DELETE FROM live_meeting_bots WHERE meeting_id=? AND bot='ops'", (mid,))
    with store.read_transaction() as c:
        assert not live_privacy.sources(c).attempt_readable("bot:ops", aid)
    # A rolled-back source must never enter the Store-wide neutral cache.
    with pytest.raises(RuntimeError, match="synthetic rollback"):
        with store.transaction() as c:
            c.execute("UPDATE messages SET refs_json=? WHERE id=?",
                      (encode({"live_meetings": ["missing-meeting"]}), attempt["message"]["id"]))
            assert "missing-meeting" in live_privacy.sources(c).attempt(aid)
            raise RuntimeError("synthetic rollback")
    with store.read_transaction() as c:
        assert live_privacy.sources(c).attempt(aid) == {mid}
    with store.transaction() as c:
        # Room binding is a neutral source too; access still checks the current meeting row.
        c.execute("UPDATE conversations SET room_key='live-meeting:missing-meeting' WHERE id=?",
                  (attempt["message"]["conversation_id"],))
    with store.read_transaction() as c:
        assert "missing-meeting" in live_privacy.sources(c).attempt(aid)
        assert not live_privacy.sources(c).attempt_readable("human:ben", aid)


def test_existing_provenance_clock_upgrades_meeting_sources(live, derived):
    from backend import live_privacy, provenance_clock
    mid, attempt = derived
    store, cid = live.app.state.store, attempt["message"]["conversation_id"]
    with store.transaction() as c:
        # Simulate the installed pre-meeting UPDATE definitions before startup upgrades them.
        for table, cols in (("messages", "id,conversation_id,refs_json,in_reply_to"),
                            ("conversations", "id,task_id")):
            c.execute(f"DROP TRIGGER provenance_{table}_update_v2")
            c.execute(f"CREATE TRIGGER provenance_{table}_update AFTER UPDATE OF {cols} ON {table} "
                      "BEGIN UPDATE provenance_clock SET value=value+1 WHERE id=1; END")
        provenance_clock.ensure(c)
        c.execute("UPDATE conversations SET room_key=NULL WHERE id=?", (cid,))
    def source():
        with store.read_transaction() as c:
            return live_privacy.sources(c).conversation(cid)
    assert source() == {mid}
    with store.transaction() as c:
        c.execute("UPDATE messages SET from_actor='human:ana' WHERE id=?", (attempt["message"]["id"],))
    assert source() == set()
    with store.transaction() as c:
        c.execute("UPDATE messages SET from_actor=?,to_actor='bot:finance' WHERE id=?",
                  (H.KEEPER, attempt["message"]["id"]))
    assert source() == set()
    with store.transaction() as c:
        c.execute("UPDATE live_meeting_turns SET bot='finance' WHERE meeting_id=?", (mid,))
    assert source() == {mid}
    with store.transaction() as c:
        c.execute("UPDATE conversations SET room_key='live-meeting:missing-meeting' WHERE id=?", (cid,))
    assert source() == {"missing-meeting"}
