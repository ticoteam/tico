"""Privacy sweep using the same writes that produce review cards and runner inputs."""
import base64
import json

import pytest

from backend import scoped_routes, turn_scope
from backend.store import H
from backend.tests.test_api import api, assign, claim, get, headers, post, ready, runner  # noqa: F401
from backend.tests.test_assistant import assistant_turn
from backend.tests.test_file_reviews import ask, answer
from backend.tests.test_files import turn
from backend.tests.test_member_bots import botops  # noqa: F401
from backend.tests.test_meeting_items import record
from backend.tests.test_meeting_task_links import link, task
from backend.tests.test_pages import HTML
from backend.tests.test_postal_mail import (deliver, drain, hq, items, letter, mail, service)  # noqa: F401
from backend.tests.test_turn_scope import blob, make_scope, published, task_turn


def no_sources(value, secrets):
    wire = json.dumps(value, ensure_ascii=False)
    for secret in secrets:
        assert secret not in wire, (secret, wire[:4000])


def sources(api, title="Restricted source"):
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    t = post(api, "tasks", {"owner": "ops", "title": "Review source context", "body": "Review the sources.", "private": False})
    publisher = claim(api, machine, "ops", key="source-publish")
    hidden = published(api, publisher, task=t["id"], name="restricted.html", title=title,
                       data=HTML + b"<p>Restricted unique bytes</p>")
    allowed = published(api, publisher, task=t["id"], name="allowed.html", title="Allowed source")
    made = ask(api, publisher["token"], [{"file": hidden}, {"file": allowed}], pick=True,
               parent=t["id"], private=False)
    label = made["question"]["refs"]["questions"][0]["options"][0]["label"]
    answered = answer(api, made, [label])
    meeting = record(api, {"private": False, "title": "Restricted meeting"})
    link(api, meeting, [t["id"]])
    good_meeting = record(api, {"private": False, "title": "Allowed meeting"})
    link(api, good_meeting, [t["id"]])
    bid = blob(api, hidden)
    with api.app.state.store.read() as c:
        version = dict(c.execute("SELECT * FROM bot_file_versions WHERE file_id=?", (hidden,)).fetchone())
    return dict(task=t, source_title=title, source_label=label, machine=machine, publisher=publisher, hidden=hidden, allowed=allowed, made=made, answered=answered, meeting=meeting,
                good_meeting=good_meeting, bid=bid, version=version)


def narrow(api, data):
    from backend import meetings
    private = post(api, "tasks", {"owner": "human:ana", "title": "Private source provenance",
                                  "body": "Keep this source private", "private": True})
    moved = api.patch("/api/v2/files/" + data["hidden"], json={"task": private["id"]}, headers=headers())
    assert moved.status_code == 200, moved.text
    with api.app.state.store.transaction() as c:
        row = meetings.get(data["meeting"], c)
        meetings.snapshot({**row["metadata"], "private": True, "audience": "participants"}, conn=c)


def secrets(data):
    v = data["version"]
    return tuple(x for x in (data["hidden"], data["bid"], v["digest"], v["source_digest"],
                            data["source_title"], data["source_label"], "restricted.html", data["meeting"], "Restricted meeting") if x)


def scoped(api, data, make):
    if make:
        machine, attempt = assistant_turn(api, "ana-test")
        make_scope(api, attempt, data["task"]["id"])
    else:
        machine, publisher = data["machine"], data["publisher"]
        post(api, f'attempts/{publisher["id"]}/started', {"thread_id": "source-publish"}, machine["token"])
        post(api, f'attempts/{publisher["id"]}/complete', {"outcome": "completed", "last_seq": 0}, machine["token"])
        post(api, f'tasks/{data["task"]["id"]}/comments', {"text": "Continue reviewing this task"})
        attempt = claim(api, machine, "ops", key="real-write-claim")
        assert attempt and attempt["task"]["id"] == data["task"]["id"]
    return machine, attempt


@pytest.mark.parametrize("make", [False, True], ids=["task-bot", "make-page"])
def test_real_write_leak_sweep(api, make):
    data = sources(api)
    machine, attempt = scoped(api, data, make)
    token, tid = attempt["token"], data["task"]["id"]
    # Verify the production writes actually stored title provenance, including transformed pick labels.
    option = data["made"]["question"]["refs"]["questions"][0]["options"][0]
    assert option["source"]["file"] == data["hidden"]
    assert data["answered"]["answer"]["label_sources"]["decision"]["Restricted source"]["file"] == data["hidden"]
    if not make:
        assert data["hidden"] in json.dumps(attempt["task_context"])
        post(api, f'attempts/{attempt["id"]}/started', {"thread_id": "scope-inputs"}, token=machine["token"])
        steering = post(api, f"tasks/{tid}/comments", {"text": "Read these source versions", "delivery": "steer",
            "attachments": [data["hidden"] + "@1", data["allowed"] + "@1"]})["comment"]
        assert data["hidden"] in json.dumps(steering["refs"])
    narrow(api, data)
    paths = [f"tasks/{tid}/context", "pages/" + data["allowed"], "meetings/transcript?id=" + data["good_meeting"]]
    if not make:
        paths += ["tasks", f"tasks/{tid}", f"tasks/{tid}/comments", f"tasks/{tid}/answers",
                  "tasks/" + data["made"]["task"]["id"],
                  "tasks/" + data["made"]["task"]["id"] + "/comments",
                  "tasks/" + data["made"]["task"]["id"] + "/answers",
                  "messages/" + data["made"]["question"]["id"],
                  "messages/" + data["answered"]["comment"]["id"],
                  "origins/" + data["answered"]["comment"]["id"],
                  "origins/" + data["made"]["question"]["id"],
                  "conversations/" + data["made"]["task"]["conversation_id"] + "/messages",
                  "conversations/" + data["made"]["task"]["conversation_id"] + "/snapshot",
                  "threads/" + data["made"]["task"]["conversation_id"],
                  "files/" + data["allowed"] + "/meta", "bots/ops/files", "pages", "files/search?q=source"]
    for path in paths:
        no_sources(get(api, path, token), secrets(data))
    for path in ("files/" + data["hidden"], "files/" + data["bid"], "files/" + data["hidden"] + "/versions/1"):
        get(api, path, token, expected=403)
    for path in ("pages/" + data["hidden"], "meetings/transcript?id=" + data["meeting"]):
        get(api, path, token, expected=404)
    if not make:
        assert_file_metadata_hidden(api, data["hidden"], token)
    method = "hub_task_context" if make else "hub_task_show"
    result = post(api, "mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                               "params": {"name": method, "arguments": {"id": tid}}}, token)
    assert not result.get("error") and not result["result"].get("isError"), result
    no_sources(result, secrets(data))
    if not make:
        replay = claim(api, machine, "ops", key="real-write-claim")
        no_sources(replay, secrets(data))
        for key in ("real-input", "real-input", "real-input-fresh"):
            delivered = post(api, f'attempts/{attempt["id"]}/inputs', {}, machine["token"], key=key)
            assert steering["id"] in [m["id"] for m in delivered["messages"]]
            assert data["allowed"] in json.dumps(delivered)
            no_sources(delivered, secrets(data))


@pytest.mark.parametrize("make", [False, True], ids=["task-bot", "make-page"])
def test_scoped_routes_deny_sql_csv_history_downloads_and_new_surfaces(api, make):
    data = sources(api)
    _, attempt = scoped(api, data, make)
    token = attempt["token"]
    for path in ("history.csv?kind=decision", "history?kind=decision", "events", "admin",
                 "live-meetings/missing/events", "runners/me/events", "not-yet-reviewed", "threads/search", "threads/rail"):
        get(api, path, token, expected=403)
    post(api, "sql", {"sql": "SELECT hex(detail) FROM events"}, token, expected=403)
    for path in ("/healthz", "/download/missing", "/index.html"):
        response = api.get(path, headers=headers(token))
        assert response.status_code == 403, (path, response.text)
    # The file tool's download is allowed only after checking the individual source id.
    narrow(api, data)
    for path in ("files/" + data["hidden"], "files/" + data["bid"], "files/nonexistent"):
        get(api, path, token, expected=403)
    assert api.get("/api/v2/files/" + data["allowed"], headers=headers(token)).status_code == 200


def test_prose_unknown_references_and_task_origins_keep_their_bytes(api, monkeypatch):
    text = '{"digest":"example"}\n  file-0123456789abcdef01234567\n{"spaces":  [1, 2]}'
    t, attempt = task_turn(api, body=text)
    token = attempt["token"]
    got = get(api, "tasks/" + t["id"], token)
    assert got["task"]["body"].encode() == text.encode()
    origin = api.get("/api/v2/origins/" + t["id"], headers=headers(token))
    assert origin.status_code == 200 and origin.json() is not None, origin.text
    original = turn_scope.project
    monkeypatch.setattr(turn_scope, "project", lambda c, scoped, payload, **kw: payload)
    baseline = api.get("/api/v2/origins/" + t["id"], headers=headers(token))
    assert origin.content == baseline.content
    monkeypatch.setattr(turn_scope, "project", original)
    # Ordinary readers take the same unprojected response path as main, byte for byte.
    _, unscoped = turn(api, "finance")
    for ordinary in ("ana-test", unscoped["token"]):
        for path in ("tasks/" + t["id"], "origins/" + t["id"]):
            before = api.get("/api/v2/" + path, headers=headers(ordinary))
            assert before.status_code == 200
            monkeypatch.setattr(turn_scope, "project", lambda c, scoped, payload, **kw: payload)
            after = api.get("/api/v2/" + path, headers=headers(ordinary))
            assert before.content == after.content
            monkeypatch.setattr(turn_scope, "project", original)


def test_legacy_exact_title_labels_and_generated_answers_are_projected(api):
    data = sources(api)
    # Simulate legacy rows by removing only newly introduced provenance from real production writes.
    with api.app.state.store.transaction() as c:
        for mid in (data["made"]["question"]["id"], data["answered"]["comment"]["id"]):
            msg = H.message(c, mid)
            refs = msg["refs"]
            refs.pop("body_sources", None)
            for q in refs.get("questions", []):
                for option in q["options"]:
                    option.pop("source", None)
            refs.get("answer", {}).pop("label_sources", None)
            c.execute("UPDATE messages SET refs=? WHERE id=?", (H._dump(refs), mid))
    _, attempt = scoped(api, data, False)
    narrow(api, data)
    for path in ("messages/" + data["made"]["question"]["id"], "messages/" + data["answered"]["comment"]["id"]):
        no_sources(get(api, path, attempt["token"]), secrets(data))


def test_sweep_detects_disabling_either_boundary(api, monkeypatch):
    data = sources(api)
    _, attempt = scoped(api, data, False)
    narrow(api, data)
    path = "messages/" + data["answered"]["comment"]["id"]
    no_sources(get(api, path, attempt["token"]), secrets(data))
    with monkeypatch.context() as patch:
        patch.setattr(turn_scope, "project", lambda c, scoped, payload, **kw: payload)
        with pytest.raises(AssertionError):
            no_sources(get(api, path, attempt["token"]), secrets(data))
    _, make = scoped(api, data, True)
    with monkeypatch.context() as patch:
        patch.setattr(scoped_routes, "require_route", lambda *a: None)
        # Make's owner identity otherwise reaches CSV bytes outside the JSON projector.
        with pytest.raises(AssertionError):
            get(api, "history.csv?kind=decision", make["token"], expected=403)


def assert_file_metadata_hidden(api, fid, token):
    for suffix in ("/meta?v=1", "/versions", "/activity"):
        get(api, "files/" + fid + suffix, token, expected=404)


def test_origins_projects_the_message_before_copying_its_title(api):
    data = sources(api)
    _, attempt = scoped(api, data, False)
    mid, token = data["answered"]["comment"]["id"], attempt["token"]
    assert data["source_label"] in get(api, "origins/" + mid, token)["title"]
    narrow(api, data)
    assert get(api, "messages/" + mid, token)["body"] == ""
    assert get(api, "origins/" + mid, token)["title"] == ""
    assert data["source_label"] in get(api, "origins/" + mid)["title"]


def postal_sources(api, hq):
    from backend.tests.test_api import put
    _, attempt = task_turn(api)
    put(api, "postal/addresses/all/grants/bot/ops", {})
    letter(api, "scope-letter", "legal", summary="Restricted postal source")
    deliver(hq, "scope-letter")
    service(api).tick()
    drain(api)
    deliver(hq, "scope-arrival", checks=True)
    deliver(hq, "scope-failed")
    service(api).tick()
    api.app.state.postal_mail.process(items(api)["scope-failed"]["id"])
    return attempt["token"], items(api)


def test_postal_file_ids_and_unprocessed_arrivals_share_the_audience_ceiling(api, hq, mail):
    mail.down.add("scope-failed")
    token, rows = postal_sources(api, hq)
    filed = rows["scope-letter"]
    assert rows["scope-arrival"]["status"] == "new" and not rows["scope-arrival"]["file_id"]
    assert rows["scope-failed"]["status"] == "failed" and not rows["scope-failed"]["blob_id"]
    assert get(api, "postal/items", token)["items"] == []
    for row in rows.values():
        get(api, "postal/items/" + row["id"], token, expected=404)
    assert_file_metadata_hidden(api, filed["file_id"], token)
    for fid in (filed["file_id"], filed["blob_id"]):
        get(api, "files/" + fid + "/meta?v=1", token, expected=404)
        for suffix in ("", "/thumb", "/poster"):
            for method in ("GET", "HEAD"):
                response = api.request(method, "/api/v2/files/" + fid + suffix, headers=headers(token))
                assert response.status_code == 403, (method, suffix, response.text)
    get(api, "files/" + filed["file_id"] + "/versions/1", token, expected=403)
    # Ordinary mailbox grantees retain their normal reads, including scan metadata.
    from backend.tests.test_org_rules import as_bot
    as_bot(api, "ops", "ops-mail-reader")
    assert len(get(api, "postal/items", "ops-mail-reader")["items"]) == 3
    assert get(api, "files/" + filed["file_id"] + "/meta?v=1", "ops-mail-reader")["id"] == filed["file_id"]


def test_postal_cursors_scan_past_rejected_arrivals_without_naming_them(api, hq, mail, monkeypatch):
    from backend import postal_mail
    from backend.tests.test_api import put
    t, attempt = task_turn(api, private=True, roles={"participant": ["ben"]})
    token = attempt["token"]
    put(api, "postal/addresses/all/grants/bot/ops", {})
    with api.app.state.store.read() as c:
        audience = turn_scope.readers(c, api.app.state.files.auth, H.task(c, t["id"]))
    for reader in audience:
        kind = "bot" if H.is_bot(reader.actor) else "person"
        put(api, f"postal/addresses/shared/grants/{kind}/{H.actor_id(reader.actor)}", {})
    for ident in ("cursor-hidden-1", "cursor-hidden-2", "cursor-shared"):
        deliver(hq, ident, assignment_id="shared" if ident == "cursor-shared" else "restricted")
    service(api).tick()
    rows = items(api)
    with api.app.state.store.transaction() as c:
        for ident, stamp in (("cursor-hidden-1", "2099-01-01"), ("cursor-hidden-2", "2098-01-01"),
                             ("cursor-shared", "2097-01-01")):
            c.execute("UPDATE mail_items SET received_at=? WHERE id=?", (stamp, rows[ident]["id"]))
    shown = get(api, "postal/items?limit=1", token)
    assert [i["external_id"] for i in shown["items"]] == ["cursor-shared"] and shown["next"] is None
    with monkeypatch.context() as patch:
        patch.setattr(postal_mail, "SCAN_MAX", 1)
        first = get(api, "postal/items?limit=1", token)
        assert first["items"] == []
        hidden = (rows["cursor-hidden-1"]["id"], rows["cursor-hidden-2"]["id"], "2099-01-01", "2098-01-01")
        assert_cursor_hides_rejected_rows(first["next"], hidden)
        second = get(api, "postal/items?limit=1&after=" + first["next"], token)
        assert second["items"] == []
        assert_cursor_hides_rejected_rows(second["next"], hidden)
        third = get(api, "postal/items?limit=1&after=" + second["next"], token)
        assert [i["external_id"] for i in third["items"]] == ["cursor-shared"]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE mail_items SET received_at='2100-01-01' WHERE id=?", (rows["cursor-shared"]["id"],))
    with monkeypatch.context() as patch:
        patch.setattr(postal_mail, "SCAN_MAX", 2)
        mixed = get(api, "postal/items?limit=2", token)
        assert [i["external_id"] for i in mixed["items"]] == ["cursor-shared"]
        assert mixed["next"] == "2100-01-01|" + rows["cursor-shared"]["id"]


def cursor_sources(api, count=5000):
    machine, publisher = turn(api)
    seed = published(api, publisher, name="bot-wide.html", botwide=True)
    # The large rejected prefix is the reviewer's seed; the page and its version start with a production write.
    with api.app.state.store.transaction() as c:
        source = dict(c.execute("SELECT * FROM bot_files WHERE id=?", (seed,)).fetchone())
        columns = list(source)
        for n in range(count - 1):
            copy = {**source, "id": f"file-rejected-{n:05d}", "identity": f"rejected-{n:05d}"}
            c.execute("INSERT INTO bot_files (" + ",".join(columns) + ") VALUES (" + ",".join("?" * len(columns)) + ")",
                      [copy[k] for k in columns])
            c.execute("INSERT INTO pages(file_id,cadence,created_at,updated_at) VALUES(?,'none',?,?)",
                      (copy["id"], source["first_activity_at"], source["last_activity_at"]))
    post(api, f'attempts/{publisher["id"]}/started', {"thread_id": "cursor-source"}, machine["token"])
    post(api, f'attempts/{publisher["id"]}/complete', {"outcome": "completed", "last_seq": 0}, machine["token"])
    t = post(api, "tasks", {"owner": "ops", "title": "Read the cursor context", "body": "Read the permitted pages.",
                            "private": False})
    attempt = claim(api, machine, "ops")
    assert attempt and attempt["task"]["id"] == t["id"]
    allowed = published(api, attempt, name="last-allowed.html", task=t["id"])
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_files SET last_activity_at='2000-01-01T00:00:00Z' WHERE id=?", (allowed,))
    return attempt["token"], seed, source["last_activity_at"], allowed


def assert_cursor_hides_rejected_rows(cursor, secrets):
    assert cursor
    # Inspect every base64 layer available without the server's encryption key.
    decoded = base64.urlsafe_b64decode(cursor.removeprefix("opaque:")).decode(errors="replace")
    no_sources([cursor, decoded], secrets)


def test_empty_page_cursor_does_not_name_the_last_rejected_source(api):
    token, seed, stamp, allowed = cursor_sources(api)
    page = get(api, "pages?bot=ops&limit=1", token)
    assert page["pages"] == []
    assert_cursor_hides_rejected_rows(page["next_cursor"], (seed, stamp, "file-rejected-"))
    following = api.get("/api/v2/pages", params={"bot": "ops", "limit": 1, "cursor": page["next_cursor"]},
                        headers=headers(token))
    assert following.status_code == 200, following.text
    assert [p["file_id"] for p in following.json()["pages"]] == [allowed]
    get(api, "pages?cursor=" + page["next_cursor"][:-3] + "AAAA", token, expected=422)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_files SET last_activity_at='2099-01-01' WHERE id=?", (allowed,))
    mixed = get(api, "pages?bot=ops&limit=2", token)
    assert [p["file_id"] for p in mixed["pages"]] == [allowed]
    from backend.file_library import decode_cursor
    assert decode_cursor(mixed["next_cursor"]) == ("2099-01-01", allowed)


def test_new_leak_matrix_detects_each_disabled_guard(api, hq, mail, monkeypatch):
    from backend import files, list_cursors, postal_mail
    data = sources(api)
    _, attempt = scoped(api, data, False)
    narrow(api, data)
    # Origins must use the same projector even before middleware sees the copied title.
    with monkeypatch.context() as patch:
        original = turn_scope.project
        patch.setattr(turn_scope, "project", lambda c, scoped, value, **kw:
                      value if isinstance(value, dict) and "body" in value and
                      value.get("id") == data["answered"]["comment"]["id"] else original(c, scoped, value, **kw))
        with pytest.raises(AssertionError):
            no_sources(get(api, "origins/" + data["answered"]["comment"]["id"], attempt["token"]), secrets(data))
    mail.down.add("scope-failed")
    # Reuse the live scoped attempt rather than claiming a second queued task on this runner.
    from backend.tests.test_api import put
    put(api, "postal/addresses/all/grants/bot/ops", {})
    letter(api, "mutation-letter", "legal")
    deliver(hq, "mutation-letter")
    service(api).tick()
    drain(api)
    filed = items(api)["mutation-letter"]
    with monkeypatch.context() as patch:
        patch.setattr(files, "file_visible", lambda *a, **kw: True)
        with pytest.raises(AssertionError):
            assert_file_metadata_hidden(api, filed["file_id"], attempt["token"])
    deliver(hq, "mutation-arrival")
    service(api).tick()
    arrival = items(api)["mutation-arrival"]
    with monkeypatch.context() as patch:
        patch.setattr(postal_mail, "audience_allowed", lambda *a: True)
        with pytest.raises(AssertionError):
            no_sources(get(api, "postal/items", attempt["token"]), (arrival["external_id"],))
    # A smaller scan bound exercises the same production list path without another 5,000-row seed.
    from backend import file_library
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_files SET last_activity_at='2099-01-01' WHERE id=?", (data["hidden"],))
    with monkeypatch.context() as patch:
        patch.setattr(file_library, "SCAN_MAX", 1)
        patch.setattr(list_cursors, "seal", lambda c, ns, pos: file_library.encode_cursor(*pos))
        with pytest.raises(AssertionError):
            page = get(api, "pages?bot=ops&limit=1", attempt["token"])
            assert_cursor_hides_rejected_rows(page["next_cursor"], (data["hidden"], "2099-01-01"))


def test_scoped_hub_doc_read_manual_pages(api):
    from clients import hubcli, remotecli
    from backend.tests.test_mcp import call
    _, attempt = task_turn(api)
    args = hubcli.parser().parse_args(["doc", "read", "manual:pages"])
    class ScopedApi:
        def get(self, path, **params):
            response = api.get("/api/v2/" + path, params={k: v for k, v in params.items() if v is not None},
                               headers=headers(attempt["token"]))
            assert response.status_code == 200, response.text
            return response.json()
    result = remotecli.via_tool(ScopedApi(), args)
    assert result["doc"]["body"] and result["doc"]["path"] == "docs/pages.md"
    error, result = call(api, "hub_doc_read", {"ref": "manual:pages"}, token=attempt["token"])
    assert not error, result
    assert result["doc"]["body"] and result["doc"]["path"] == "docs/pages.md"


def test_transformed_review_labels_keep_provenance(api):
    data = sources(api, title="Restricted-source.html")
    assert data["source_label"] == "Restricted source"
    _, attempt = scoped(api, data, False)
    narrow(api, data)
    for mid in (data["made"]["question"]["id"], data["answered"]["comment"]["id"]):
        no_sources(get(api, "messages/" + mid, attempt["token"]), secrets(data))


def test_legacy_fallback_keeps_unrelated_labels_and_opaque_text(api):
    data = sources(api)
    _, attempt = scoped(api, data, False)
    other = post(api, "tasks", {"owner": "ops", "title": "Choose a label", "body": "Choose a label", "private": False})
    post(api, f'tasks/{other["id"]}/comments', {"text": "Choose a label", "ask": {
        "who": "ben", "questions": [{"id": "label", "header": "Label", "question": "Which label?", "options": [
            {"label": "Restricted source", "then": "Use this label"}, {"label": "Keep", "then": "Keep this label"}]}]}})
    parent_question = post(api, f'tasks/{data["task"]["id"]}/comments', {"text": "Choose a source", "ask": {
        "who": "ben", "questions": [{"id": "label", "header": "Source", "question": "Which source?", "options": [
            {"label": "Restricted source", "then": "Use this source"}, {"label": "Keep", "then": "Keep this source"}]}]}})["comment"]
    place = get(api, "places/task:" + data["task"]["id"])
    assert "page:" + data["hidden"] in [p["item"] for p in place["pins"]]
    text = '{"digest":"example"} ' + data["hidden"]
    comment = post(api, f'tasks/{data["task"]["id"]}/comments', {"text": text})["comment"]
    narrow(api, data)
    shown = get(api, "tasks", attempt["token"])
    row = next(t for t in shown["tasks"] if t["id"] == other["id"])
    assert row["ask"]["refs"]["questions"][0]["options"][0]["label"] == "Restricted source"
    parent_shown = get(api, "messages/" + parent_question["id"], attempt["token"])
    assert [o["label"] for o in parent_shown["refs"]["questions"][0]["options"]] == ["Keep"]
    assert get(api, "messages/" + comment["id"], attempt["token"])["body"] == text


def test_scoped_task_tools_bootstrap_update_and_read_context(api):
    t, attempt = task_turn(api)
    token = attempt["token"]
    assert "credentials" in get(api, "credential-runtime", token)
    result = post(api, "mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "hub_task_update", "arguments": {"id": t["id"], "status": "doing", "note": "Reviewing attached context"}}}, token)
    assert not result.get("error") and not result["result"].get("isError"), result
    assert get(api, "tasks/" + t["id"], token)["task"]["status"] == "doing"
    assert get(api, "tasks/" + t["id"] + "/context", token) == {"pages": [], "meetings": []}


def test_unknown_saved_file_ref_survives_the_real_message_writer(api):
    t, attempt = task_turn(api)
    unknown = "file-0123456789abcdef01234567@1"
    with api.app.state.store.transaction() as c:
        comment = H.task_comment(c, "human:ana", t["id"], "Keep this saved reference", wake=False,
                                 extra_refs={"files": [unknown]})
    shown = get(api, "messages/" + comment["id"], attempt["token"])
    assert shown["refs"]["files"] == [unknown]
    get(api, "files/" + unknown.split("@")[0], attempt["token"], expected=403)


def test_scoped_botops_cannot_use_delegation_to_cross_the_admin_perimeter(api, botops):
    task = post(api, "tasks", {"owner": "botops", "title": "Review the deployment task", "body": "Review its files."})
    attempt = claim(api, botops, "botops")
    assert attempt["task"]["id"] == task["id"]
    token = attempt["token"]
    for path in ("bots/ops/repositories", "automations", "inbox", "tico/fleet", "bots/ops/turns",
                 "turns/" + attempt["id"] + "/steps"):
        get(api, path, token, expected=403)
    for method, path in (("PUT", "bots/ops/repositories"), ("POST", "bots/ops/model"),
                         ("POST", "automations"), ("PUT", "providers"), ("POST", "runners/auto")):
        response = api.request(method, "/api/v2/" + path, json={}, headers={
            **headers(token), "X-Tico-On-Behalf-Of": "turn"})
        assert response.status_code == 403, (path, response.text)


@pytest.mark.parametrize("legacy", [False, True])
def test_passed_labels_also_remove_the_generated_answer_body(api, legacy):
    data = sources(api)
    question = post(api, "tasks/" + data["task"]["id"] + "/comments", {
        "text": "Which source should we use?", "ask": {"who": "ana", "questions": [{
            "id": "decision", "header": "Sources", "question": "Which source should we use?", "multi": True,
            "options": [{"label": data["source_label"], "file": data["hidden"] + "@1", "then": "Use this source"},
                        {"label": "Allowed source", "file": data["allowed"] + "@1",
                         "then": "Use this source", "recommended": True}]}]}},
        data["publisher"]["token"])["comment"]
    answered = answer(api, {"task": data["task"], "question": question}, ["Allowed source"])
    assert data["source_label"] in answered["comment"]["body"]
    assert answered["answer"]["not"]["decision"] == [data["source_label"]]
    if legacy:
        with api.app.state.store.transaction() as c:
            refs = H.message(c, answered["comment"]["id"])["refs"]
            refs.pop("body_sources")
            refs["answer"].pop("label_sources")
            c.execute("UPDATE messages SET refs=? WHERE id=?", (H._dump(refs), answered["comment"]["id"]))
    _, attempt = scoped(api, data, False)
    archived = api.patch("/api/v2/files/" + data["hidden"], json={"archived": True}, headers=headers())
    assert archived.status_code == 200, archived.text
    shown = get(api, "messages/" + answered["comment"]["id"], attempt["token"])
    no_sources(shown, secrets(data))
    assert shown["refs"]["answer"]["answers"]["decision"] == ["Allowed source"]
