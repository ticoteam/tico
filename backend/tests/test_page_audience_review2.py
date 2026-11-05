"""Copied text, runner context and metadata stay revoked across edits, deletion and retries."""
import json

import pytest

from backend import counsel, events, files, task_privacy, teammate_access, threads, visuals
from backend.auth import Identity
from backend.store import H
from backend.tests.slug_ids import bot_ids_are_their_slugs  # noqa: F401
from backend.tests.test_api import api, assign, claim, get, headers, post, ready, runner  # noqa: F401
from backend.tests.test_assistant import room
from backend.tests.test_counsel import counsel_on, counsel_run, source
from backend.tests.test_files import turn
from backend.tests.test_groups import send
from backend.tests.test_org_rules import org
from backend.tests.test_page_audience import edit, promoted, read
from backend.tests.test_pages import page


def assert_placeholder(value, label, fid):
    text = json.dumps(value)
    assert label in text, text
    for secret in (fid, "Audience secret", "weekly.html", '"size"'):
        assert secret not in text, (secret, text)


def test_counsel_edit_then_narrow_scrubs_history_comments_results_and_sse(api):
    org(api)
    counsel_on(api)
    fid, _ = promoted(api)
    edit(api, fid, {"people": ["cara"], "bots": [H.COUNSEL]})
    tid = post(api, "rules/read", {"file_id": fid, "source_id": source(api)}, token="cara-test")["task_id"]
    original = get(api, "tasks/" + tid, token="cara-test")["task"]["body"]
    assert "42 deals" in original
    changed = {"body": original + "\nReview this document.", "note": "The document says 42 deals."}
    post(api, "tasks/" + tid, changed, token="cara-test", key="edit-page-copy")
    machine, attempt = counsel_run(api)
    aid = attempt["id"]
    post(api, "tasks/" + tid + "/comments", {"text": "The document reports 42 deals."}, token=attempt["token"])
    post(api, f"attempts/{aid}/events", {"events": [{"seq": 1, "kind": "message", "payload": {"text": original}}]}, token=machine["token"])
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET checkpoint=?,resume=? WHERE id=?", (original, json.dumps({"text": original}), tid))
        c.execute("UPDATE attempts SET result=?,final_text=?,summary=? WHERE id=?",
                  (json.dumps({"text": original, "outcome": "completed"}), original, original, aid))
        before = events.latest(c)
    edit(api, fid, {"people": ["cara"]})
    result = get(api, "tasks/" + tid, token="cara-test")
    assert result["task"]["body"] == counsel.REMOVED_PAGE
    assert result["events"] and "42 deals" not in json.dumps(result)
    replay = post(api, "tasks/" + tid, changed, token="cara-test", key="edit-page-copy")
    assert "42 deals" not in json.dumps(replay)
    with api.app.state.store.read() as c:
        delivered, cursor, reset, _ = events.read(c, api.app.state.auth,
            Identity("human:cara", "human", "cara@acme.example"), before, topics=("tasks", "messages", "runs"))
        assert reset and not delivered  # SSE tells the client to discard its old snapshot.
        for table, where, args in (("history_events", "subject_type='task' AND subject_id=?", (tid,)),
                                   ("messages", "conversation_id=?", (result["task"]["conversation_id"],)),
                                   ("attempts", "id=?", (aid,)), ("attempt_events", "attempt_id=?", (aid,))):
            saved = [dict(r) for r in c.execute(f"SELECT * FROM {table} WHERE {where}", args)]
            assert "42 deals" not in json.dumps(saved), (table, saved)
    # Output already in flight must not restore the copied text after the transaction scrubbed it.
    post(api, f"attempts/{aid}/events", {"events": [{"seq": 2, "kind": "message", "payload": {"text": original}}]}, token=machine["token"])
    post(api, f"attempts/{aid}/complete", {"outcome": "completed", "last_seq": 2, "text": original}, token=machine["token"])
    assert "42 deals" not in json.dumps(get(api, "tasks/" + tid, token="cara-test"))
    with api.app.state.store.read() as c:
        delivered, _, reset, _ = events.read(c, api.app.state.auth,
            Identity("human:cara", "human", "cara@acme.example"), cursor, topics=("messages", "runs"))
        assert not reset and "42 deals" not in json.dumps(delivered)


def test_archived_bot_matches_no_explicit_or_nested_group_audience_and_no_signed_url(api):
    legal = send(api, "POST", "groups", {"name": "Legal"})["id"]
    contracts = send(api, "POST", "groups", {"name": "Contracts", "parent": legal, "add": {"bots": ["finance"]}})["id"]
    fid, _ = promoted(api)
    edit(api, fid, {"people": ["cara"], "teams": [legal]})
    api.app.state.store.settings.visual_origin = "https://visuals.acme.example"
    who = Identity("human:cara", "human", "cara@acme.example", task_actor="bot:finance")
    with api.app.state.store.read() as c:
        src = visuals.src_for(api.app, api.app.state.store.settings, c, who, fid, 1)
    assert src and api.get(src).status_code == 200
    with api.app.state.store.transaction() as c:
        from backend.settings_admin import archive_bot
        archive_bot(c, "human:ana", "finance")
    with api.app.state.store.read() as c:
        bot = Identity("bot:finance", "bot")
        for audience in ({"everyone": True}, {"bots": ["finance"]}, {"teams": [contracts]}, {"teams": [legal]}):
            assert not teammate_access.in_audience(c, bot, audience, api.app.state.auth)
        assert visuals.token_reader(c, api.app.state.auth, "bot:finance") is None
        row = c.execute("SELECT * FROM bot_files WHERE id=?", (fid,)).fetchone()
        assert not files.file_visible(c, api.app.state.auth, who, row, {})
    assert api.get(src).status_code == 404


@pytest.mark.parametrize("retry", [False, True], ids=["claim", "cached-claim"])
def test_runner_claim_scrubs_message_history_and_stored_refs(api, retry):
    machine, attempt = turn(api)
    tid = post(api, "tasks", {"owner": "finance", "title": "Review the plan", "body": "Review", "private": False})["id"]
    fid = page(api, attempt, task=tid, title="Audience secret").json()["page"]["file_id"]
    with api.app.state.store.transaction() as c:
        cid = H.task(c, tid)["conversation_id"]
        row = c.execute("SELECT id,refs FROM messages WHERE conversation_id=? ORDER BY rowid LIMIT 1", (cid,)).fetchone()
        refs = H._json(row["refs"], {})
        refs["attachments"] = [{"file_id": fid, "ref": fid + "@1", "version": 1, "kind": "page",
                                 "title": "Audience secret", "name": "weekly.html", "size": 123}]
        refs["page"] = {"path": "#/page/" + fid, "title": "Audience secret"}
        c.execute("UPDATE messages SET refs=? WHERE id=?", (json.dumps(refs), row["id"]))
    assign(api, machine, "finance")
    ready(api, machine, ["ops", "finance"])
    if retry:
        initial = claim(api, machine, "finance", key="page-ref-claim")
        assert fid in json.dumps(initial)
    edit(api, fid, {"people": ["ben"]})
    attempt = claim(api, machine, "finance", key="page-ref-claim")
    for value in (attempt["message"]["refs"], json.loads(attempt["message"]["refs_json"]), attempt["history"]):
        assert_placeholder(value, "A page you can't open", fid)
    assert json.loads(attempt["message"]["refs_json"]) == attempt["message"]["refs"]


def test_deleted_page_refs_stay_scrubbed_for_everyone_and_events_stay_denied(api):
    tid = post(api, "tasks", {"owner": "ops", "title": "Review attachment", "body": "Review", "private": False})["id"]
    _, attempt = turn(api, who="ana-test")
    fid = page(api, attempt, task=tid, title="Audience secret").json()["page"]["file_id"]
    comment = post(api, f"tasks/{tid}/comments", {"text": "Review this", "attachments": [fid + "@1"]}, token="ben-test")["comment"]
    edit(api, fid, {"people": ["ben"]})
    who = Identity("human:cara", "human", "cara@acme.example")
    with api.app.state.store.transaction() as c:
        H.event(c, "human:ben", "message.visual", tid, {"file": fid, "version": 1, "kind": "page"})
        event = dict(c.execute("SELECT * FROM events WHERE action='message.visual' AND target=? ORDER BY rowid DESC", (tid,)).fetchone())
        assert not files.page_event_readable(c, api.app.state.auth, who, tid, event["detail"])
    api.app.state.blobs._fresh.clear()
    deleted = api.delete("/api/v2/files/" + fid, headers=headers("ana-test"))
    assert deleted.status_code == 200, deleted.text
    for person in ("ana", "ben", "cara"):
        who = Identity("human:" + person, "owner" if person == "ana" else "human", person + "@acme.example")
        with api.app.state.store.read() as c:
            assert c.execute("SELECT 1 FROM pages WHERE file_id=?", (fid,)).fetchone() is None
            refs = H.message(c, comment["id"])["refs"]
            assert_placeholder(files.readable_page_refs(c, api.app.state.auth, who, refs), "A deleted page", fid)
            shown = visuals.decorate(c, who, api.app.state.files, b"test", "", [{"refs": refs}])
            assert_placeholder(shown, "A deleted page", fid)
            assert not files.page_event_readable(c, api.app.state.auth, who, tid, event["detail"])
            assert not task_privacy.event_readable(c, who.actor, event)
        assert_placeholder(get(api, "messages/" + comment["id"], token=person + "-test"), "A deleted page", fid)


def test_assistant_page_links_use_the_shared_recognizer_on_all_transports(api):
    fid, _ = promoted(api)
    assistant_room = room(api, "cara-test")["room_id"]
    message = post(api, "assistant/messages", {"text": "Review this page", "page": {"path": "#/page/" + fid,
                   "title": "Audience secret"}}, token="cara-test")["message"]
    edit(api, fid, {"people": ["ben"]})
    who = Identity("human:cara", "human", "cara@acme.example")
    forms = [fid, fid + "@1", "page:" + fid, "#/page/" + fid, "https://acme.example/app/#/page/" + fid,
             "https://acme.example/#/page/" + fid + "?version=1", "/api/v2/pages/" + fid]
    with api.app.state.store.read() as c:
        for value in forms:
            assert files.page_references(value) == {fid}
            assert_placeholder(files.readable_page_refs(c, api.app.state.auth, who,
                {"page": {"path": value, "title": "Audience secret"}}), "A page you can't open", fid)
        timeline = threads.timeline(c, threads.Viewer(api.app.state.auth, who), assistant_room)
        assert_placeholder(timeline, "A page you can't open", fid)
    with api.app.state.store.transaction() as c:
        before = events.latest(c)
        c.execute("UPDATE messages SET revision=revision+1 WHERE id=?", (message["id"],))
    shared = {}
    for actor in ("cara", "ben", "cara"):
        viewer = Identity("human:" + actor, "human", actor + "@acme.example")
        with api.app.state.store.read() as c:
            delivered, _, reset, _ = events.read(c, api.app.state.auth, viewer, before, topics=("messages",), shared=shared)
        if actor == "cara":
            assert not reset
            assert_placeholder(delivered, "A page you can't open", fid)
    assert_placeholder(get(api, "messages/" + message["id"], token="cara-test"), "A page you can't open", fid)


def test_pin_positions_are_dense_and_headers_do_not_name_hidden_items(api):
    _, attempt = turn(api)
    ids = []
    for i in range(3):
        fid = page(api, attempt, name=f"pinned-{i}.html", title=f"Pinned page {i}").json()["page"]["file_id"]
        response = api.patch("/api/v2/files/" + fid, json={"promote": True}, headers=headers("ben-test"))
        assert response.status_code == 200, response.text
        ids.append(fid)
    for fid in ids:
        post(api, "pins", {"place": "dashboard", "item": "page:" + fid})
    edit(api, ids[1], {"people": ["ben"]})
    response = api.get("/api/v2/places/dashboard", headers=headers("cara-test"))
    assert response.status_code == 200, response.text
    pins = response.json()["pins"]
    assert [p["position"] for p in pins] == [0, 1]
    assert [p["item"] for p in pins] == ["page:" + ids[0], "page:" + ids[2]]
    assert ids[1] not in response.text
    assert "hidden" not in str(response.headers).lower()
    names = [part.strip().split(";", 1)[0] for part in response.headers["server-timing"].split(",")]
    edit(api, ids[1], None)
    restored = api.get("/api/v2/places/dashboard", headers=headers("cara-test"))
    assert [p["position"] for p in restored.json()["pins"]] == [0, 1, 2]
    assert names == [part.strip().split(";", 1)[0] for part in restored.headers["server-timing"].split(",")]
