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
from backend.tests.test_letters import hq as outgoing_hq  # noqa: F401


def no_sources(value, secrets):
    wire = json.dumps(value, ensure_ascii=False)
    for secret in secrets:
        assert secret not in wire, (secret, wire[:4000])


def sources(api, title="Restricted source", maintainer="ops"):
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    t = post(api, "tasks", {"owner": "ops", "title": "Review source context", "body": "Review the sources.", "private": False})
    publisher = claim(api, machine, "ops", key="source-publish")
    source_publisher = publisher if maintainer == "ops" else turn(api, maintainer)[1]
    hidden = published(api, source_publisher, task=t["id"], name="restricted.html", title=title,
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


@pytest.mark.parametrize("excluded", ["bot", "task"], ids=["page-denies-bot", "page-denies-task-readers"])
def test_page_audience_and_task_ceiling_intersect_on_real_paths(api, monkeypatch, excluded):
    from backend import files
    from backend.auth import Identity
    from backend.tests.test_page_audience import edit
    data = sources(api, maintainer="finance")
    _, attempt = scoped(api, data, False)
    token, fid, tid = attempt["token"], data["hidden"], data["task"]["id"]
    api.app.state.store.settings.visual_origin = "https://visuals.acme.example"
    page = get(api, "pages/" + fid, token)["page"]
    assert page["file_id"] == fid and api.get(page["src"]).status_code == 200
    # The page remains filed on the public task. Only its audience changes after the real review/answer writes.
    edit(api, fid, {"people": ["cara"]} if excluded == "bot" else {"bots": ["ops"]})
    with api.app.state.store.read() as c:
        row = c.execute("SELECT * FROM bot_files WHERE id=?", (fid,)).fetchone()
        auth = api.app.state.auth
        assert row["scope"] == "task:" + tid
        assert files.page_audience_visible(c, auth, Identity("bot:ops", "bot"), row, {}) is (excluded == "task")
        assert files.page_audience_visible(c, auth, Identity("human:cara", "human"), row, {}) is (excluded == "bot")
    # A plain bot can use its own page grant; a scoped turn also needs the carried task's readers to have access.
    from backend.tests.test_org_rules import as_bot
    as_bot(api, "ops", "page-audience-reader")
    get(api, "pages/" + fid, "page-audience-reader", expected=200 if excluded == "task" else 404)
    paths = ["tasks", "tasks/" + tid, "tasks/" + tid + "/context", "tasks/" + tid + "/files",
             "tasks/" + data["made"]["task"]["id"] + "/comments",
             "tasks/" + data["made"]["task"]["id"] + "/answers",
             "pages", "pages?task=" + tid, "files/search?q=source", "bots/finance/files"]
    page_secrets = tuple(x for x in secrets(data) if x not in (data["meeting"], "Restricted meeting"))
    for path in paths:
        no_sources(get(api, path, token), page_secrets)
    # Main denies the entire generated review message when the principal loses its page audience.
    for mid in (data["made"]["question"]["id"], data["answered"]["comment"]["id"]):
        for route in ("messages/", "origins/"):
            no_sources(get(api, route + mid, token, expected=404 if excluded == "bot" else 200), page_secrets)
    get(api, "pages/" + fid, token, expected=404)
    assert api.get(page["src"]).status_code == 404
    assert_file_metadata_hidden(api, fid, token)
    for ident in (fid, data["bid"]):
        for suffix in ("", "/thumb", "/poster"):
            for method in ("GET", "HEAD"):
                response = api.request(method, "/api/v2/files/" + ident + suffix, headers=headers(token))
                assert response.status_code == 403, (method, ident, suffix, response.text)
    get(api, "files/" + fid + "/versions/1", token, expected=403)
    # Each boundary independently matters: removing its guard must make the denial assertion fail.
    with monkeypatch.context() as patch:
        if excluded == "bot":
            patch.setattr(files, "page_audience_visible", lambda *a, **kw: True)
        else:
            patch.setattr(turn_scope, "allowed", lambda *a, **kw: True)
        with pytest.raises(AssertionError):
            get(api, "pages/" + fid, token, expected=404)
    edit(api, fid, None)
    assert get(api, "pages/" + fid, token)["page"]["file_id"] == fid
    assert api.get(page["src"]).status_code == 200


def test_origins_projects_the_message_before_copying_its_title(api):
    data = sources(api)
    _, attempt = scoped(api, data, False)
    mid, token = data["answered"]["comment"]["id"], attempt["token"]
    assert data["source_label"] in get(api, "origins/" + mid, token)["title"]
    narrow(api, data)
    assert get(api, "messages/" + mid, token)["body"] == ""
    assert get(api, "origins/" + mid, token)["title"] == ""
    assert data["source_label"] in get(api, "origins/" + mid)["title"]


def test_scoped_history_uses_retained_canonical_page_ids(api, monkeypatch):
    from backend import scope_projection
    data = sources(api)
    tid, fid = data["task"]["id"], data["hidden"]
    old_name = "Earlier archived page name"
    uploaded = post(api, f"tasks/{tid}/files", {"name": "restricted.html",
                                              "text": HTML.decode() + "<p>Retained older alias</p>"})
    assert uploaded["file_id"] == fid
    with api.app.state.store.transaction() as c:
        event = c.execute("SELECT id FROM history_events WHERE subject_id=? AND kind='file' "
                          "AND EXISTS(SELECT 1 FROM json_each(json_extract(data,'$.page_files')) WHERE value=?) "
                          "ORDER BY rowid DESC LIMIT 1", (tid, fid)).fetchone()
        assert event
        # Main's history migration retains page_files after an old display/blob identity is unavailable.
        c.execute("UPDATE history_events SET old=?,new=NULL,data=? WHERE id=?",
                  (old_name, H._dump({"page_files": [fid]}), event["id"]))
    _, attempt = scoped(api, data, False)
    assert old_name in json.dumps(get(api, "tasks/" + tid, attempt["token"]))
    narrow(api, data)
    no_sources(get(api, "tasks/" + tid, attempt["token"]), (*secrets(data), old_name))
    with monkeypatch.context() as patch:
        patch.setattr(scope_projection, "SOURCE_LISTS", scope_projection.SOURCE_LISTS - {"page_files"})
        with pytest.raises(AssertionError):
            no_sources(get(api, "tasks/" + tid, attempt["token"]), (old_name,))


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
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    t = post(api, "tasks", {"owner": "ops", "title": "Read the mailbox context", "body": "Read the shared arrivals.",
                            "private": True}, "ben-test")
    attempt = claim(api, machine, "ops")
    assert attempt and attempt["task"]["id"] == t["id"]
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
    from backend.visuals import key_for
    assert decode_cursor(mixed["next_cursor"], key_for(api.app, api.app.state.store.settings)) == ("2099-01-01", allowed)
    assert_cursor_hides_rejected_rows(mixed["next_cursor"], (seed, stamp, "file-rejected-"))


def test_archived_scoped_files_are_rejected_before_metadata_and_cursor_creation(api, monkeypatch):
    from backend import file_library
    t, attempt = task_turn(api)
    fid = published(api, attempt, name="archived.html", task=t["id"])
    response = api.patch("/api/v2/files/" + fid, json={"archived": True}, headers=headers())
    assert response.status_code == 200, response.text
    token = attempt["token"]
    assert_file_metadata_hidden(api, fid, token)
    get(api, "pages/" + fid, token, expected=404)
    monkeypatch.setattr(file_library, "SCAN_MAX", 1)
    shown = get(api, "pages?archived=1&limit=1", token)
    assert shown["pages"] == []
    assert_cursor_hides_rejected_rows(shown["next_cursor"], (fid,))
    assert get(api, "files/" + fid + "/meta")["id"] == fid


def test_new_leak_matrix_detects_each_disabled_guard(api, hq, mail, monkeypatch):
    from backend import files, postal_mail
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
        patch.setattr(file_library, "encode_cursor", lambda stamp, ident, key:
                      base64.urlsafe_b64encode(f"{stamp}|{ident}".encode()).decode())
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


def test_derived_message_text_uses_one_scoped_view(api, monkeypatch):
    from backend import threads
    data = sources(api)
    _, attempt = scoped(api, data, False)
    token, mid = attempt['token'], data['answered']['comment']['id']
    before = post(api, 'threads', {'parent_message_id': mid}, token)
    assert data['source_label'] in before['title']
    narrow(api, data)

    def check():
        made = post(api, 'threads', {'parent_message_id': mid}, token)
        assert made['title'] == ''
        no_sources(get(api, 'threads/' + made['id'], token), secrets(data))
        assert get(api, 'origins/' + mid, token)['title'] == ''
        # The quote service feeds thread replies; use the same reader as the real task token.
        with api.app.state.store.read() as c:
            who = api.app.state.auth.authenticate({'authorization': 'Bearer ' + token})
            message = H.message(c, mid)
            quote = threads.reply_quotes(c, threads.viewer(api.app.state.auth, who), [
                {'id': 'reply', 'conversation_id': message['conversation_id'], 'in_reply_to': mid}])
            no_sources(quote, secrets(data))
    check()
    with monkeypatch.context() as patch:
        patch.setattr(turn_scope, 'message_view', lambda c, message, **kw: message)
        with pytest.raises(AssertionError):
            check()
    assert data['source_label'] in get(api, 'threads/' + before['id'])['title']


@pytest.mark.parametrize("digest_only", [False, True])
def test_approval_notice_keeps_payload_provenance_and_projects_before_serializing(api, monkeypatch, digest_only):
    from backend import scope_projection
    data = sources(api)
    _, attempt = scoped(api, data, False)
    token, tid = attempt['token'], data['task']['id']
    payload = {'url': 'https://acme.example/release' if digest_only else '/api/v2/files/' + data['hidden'],
               'content_sha256': data['version']['digest']}
    made = post(api, 'approvals', {'kind': 'publish', 'payload': payload, 'task_id': tid}, token)
    mid = made['message_id']
    assert data['version']['digest'] in get(api, 'messages/' + mid, token)['body']
    with api.app.state.store.read() as c:
        assert H.message(c, mid)['refs']['body_sources'] == [payload]
    with monkeypatch.context() as patch:
        patch.setattr(scope_projection, 'body_sources', lambda *a: {})
        bare = post(api, 'approvals', {'kind': 'publish', 'payload': {**payload, 'reason': 'Missing refs'},
                                      'task_id': tid}, token)
    narrow(api, data)
    with pytest.raises(AssertionError):
        no_sources(get(api, 'messages/' + bare['message_id'], token), secrets(data))
    no_sources(get(api, 'messages/' + mid, token), secrets(data))
    get(api, 'origins/' + mid, token, expected=404)
    with monkeypatch.context() as patch:
        original = turn_scope.project
        patch.setattr(turn_scope, 'project', lambda c, scoped, value, **kw:
                      value if isinstance(value, dict) and value.get('id') == mid else original(c, scoped, value, **kw))
        with pytest.raises(AssertionError):
            no_sources(get(api, 'messages/' + mid, token), secrets(data))
    # A new notice cannot store a rejected reference as opaque prose, even if later read unscoped.
    payload = {**payload, 'reason': 'A fresh request'}
    projected = post(api, 'approvals', {'kind': 'publish', 'payload': payload, 'task_id': tid}, token)
    no_sources(get(api, 'messages/' + projected['message_id']), secrets(data))
    with monkeypatch.context() as patch:
        payload = {**payload, 'reason': 'Serialization guard mutation'}
        original = turn_scope.project
        patch.setattr(turn_scope, 'project', lambda c, scoped, value, **kw:
                      value if value == payload else original(c, scoped, value, **kw))
        unguarded = post(api, 'approvals', {'kind': 'publish', 'payload': payload, 'task_id': tid}, token)
        with pytest.raises(AssertionError):
            no_sources(get(api, 'messages/' + unguarded['message_id']), secrets(data))


@pytest.mark.parametrize('guard_order', ['page-first', 'scope-first'])
def test_approval_replay_clears_copied_text_in_both_guard_orders_on_real_paths(api, monkeypatch, guard_order):
    from backend import files
    from backend.tests.test_page_audience import edit
    t, attempt = task_turn(api)
    _, publisher = turn(api, 'finance')
    text = 'Confidential draft'
    fid = published(api, publisher, task=t['id'], title=text)
    source = {'file': fid}
    copied = {key: text for key in H.SOURCED_TEXT_FIELDS}
    shapes = []
    for key in H.SOURCED_TEXT_SOURCES:
        shapes.extend([{**copied, key: [source]},
                       {**copied, 'refs': {key: [source], 'answer': copied}},
                       {'answer': copied, key: [source]}])
    payload = {'url': 'https://acme.example/release', 'content_sha256': 'a' * 64,
               'other': text, 'sources': [source], 'copies': shapes}
    request = {'kind': 'publish', 'payload': payload, 'task_id': t['id']}
    token, key = attempt['token'], 'approval-source-replay'
    made = post(api, 'approvals', request, token, key=key)
    assert made['payload'] == payload
    edit(api, fid, {'bots': ['ops']})
    get(api, 'files/' + fid, token, expected=403)
    get(api, 'pages/' + fid, token, expected=404)
    scrub = files.readable_page_refs
    with api.app.state.store.read() as c:
        auth = api.app.state.auth
        who = auth.authenticate({'authorization': 'Bearer ' + token})
        cached = H._json(c.execute("SELECT response FROM idempotency WHERE operation='/api/v2/approvals' AND key=?",
                                   (key,)).fetchone()[0], {})
        assert cached['payload'] == payload
        guards = (lambda value: scrub(c, auth, who, value),
                  lambda value: turn_scope.project(c, turn_scope.scope(c, who), value, auth=auth))
        shown = cached
        for guard in guards if guard_order == 'page-first' else reversed(guards):
            shown = guard(shown)
            # Each guard clears the text in the same step that removes its source identity.
            no_sources(shown, (text,))
    if guard_order == 'scope-first':
        monkeypatch.setattr(files, 'readable_page_refs', lambda c, auth, who, value, **kw:
                            scrub(c, auth, who, turn_scope.project(c, turn_scope.scope(c, who), value, auth=auth), **kw))

    def check():
        replay = post(api, 'approvals', request, token, key=key)
        assert replay['id'] == made['id']
        no_sources(replay, (text,))
    check()
    with monkeypatch.context() as patch:
        patch.setattr(H, 'SOURCED_TEXT_FIELDS', tuple(field for field in H.SOURCED_TEXT_FIELDS if field != 'other'))
        with pytest.raises(AssertionError):
            check()


def test_dashboard_page_public_pin_only_widens_scoped_turns_on_real_paths(api, monkeypatch):
    _, publisher = turn(api, 'cpo')
    fid = published(api, publisher, name='dashboard.html', botwide=True)
    post(api, 'pins', {'place': 'dashboard', 'item': 'page:' + fid})
    machine, unscoped = turn(api, 'ops')
    get(api, 'pages/' + fid, unscoped['token'], expected=404)
    get(api, 'files/' + fid, unscoped['token'], expected=404)
    post(api, f'attempts/{unscoped["id"]}/started', {'thread_id': 'unscoped-page-reader'}, machine['token'])
    post(api, f'attempts/{unscoped["id"]}/complete', {'outcome': 'completed', 'last_seq': 0}, machine['token'])
    t = post(api, 'tasks', {'owner': 'ops', 'title': 'Read the Dashboard page',
                            'body': 'Review the pinned page.', 'private': False})
    attempt = claim(api, machine, 'ops', key='scoped-pin-reader')
    assert attempt and attempt['task']['id'] == t['id']
    post(api, 'pins', {'place': 'task:' + t['id'], 'item': 'page:' + fid})

    def check():
        assert get(api, 'pages/' + fid, attempt['token'])['page']['file_id'] == fid
        assert api.get('/api/v2/files/' + fid, headers=headers(attempt['token'])).status_code == 200
        assert fid in json.dumps(get(api, 'tasks/' + t['id'] + '/context', attempt['token']))
    check()
    with monkeypatch.context() as patch:
        patch.setattr(turn_scope, 'public_pin', lambda *a: False)
        with pytest.raises(AssertionError):
            check()


def test_embedded_members_filter_before_paging_and_cursor(api, monkeypatch):
    from backend import teammate_access
    from backend.tests.test_bot_access import bot
    from backend.tests.test_teammate_access import set_access
    _, attempt = task_turn(api)
    from backend.tests.test_bot_access import bot_id
    for i in range(51):
        bot(api, f'cursor-member-{i:02}')
    actors = ['bot:' + bot_id(api, f'cursor-member-{i:02}') for i in range(51)]
    thread = post(api, 'threads', {'title': 'Shared membership', 'audience': 'company', 'members': actors})
    # Public threads keep their bot participants on the root, rather than granting membership.
    hidden = sorted(actors)[49]
    assert hidden.startswith('bot:')
    set_access(api, 'bots/' + H.actor_id(hidden), see={'people': ['ana']}, read={'people': ['ana']},
               write={'people': ['ana']})

    def check():
        shown = get(api, 'threads/' + thread['id'], attempt['token'])['members']
        assert len(shown['members']) == 50
        no_sources(shown, (hidden,))
        assert shown['next_cursor'] is None
    check()
    # Disabling Known's visibility also disables its SQL prefilter and must expose the rejected actor.
    with monkeypatch.context() as patch:
        patch.setattr(teammate_access.Known, 'strangers', property(lambda self: set()))
        with pytest.raises(AssertionError):
            check()


def test_origins_counts_only_readable_siblings(api, monkeypatch):
    from backend import why_here
    t, attempt = task_turn(api)
    private = post(api, 'tasks', {'owner': 'human:ben', 'title': t['title'], 'body': 'Private sibling', 'private': True})
    # Legacy task notices carry no delivery-rule ref: exercise the requester's same-title sibling query.
    with api.app.state.store.transaction() as c:
        for row in c.execute('SELECT id,refs FROM messages WHERE conversation_id=?', (t['conversation_id'],)).fetchall():
            refs = H._json(row['refs'], {})
            refs.pop('rule', None)
            c.execute('UPDATE messages SET refs=? WHERE id=?', (H._dump(refs), row['id']))
    get(api, 'origins/' + private['id'], attempt['token'], expected=404)
    path = 'origins/' + t['id']
    assert get(api, path, attempt['token'])['cause']['type'] == 'person'
    baseline = get(api, path, attempt['token'])['siblings']
    assert baseline['made'] == baseline['open'] == 1
    post(api, 'tasks/' + private['id'], {'version': private['version'], 'close': True})
    assert get(api, path, attempt['token'])['siblings'] == baseline
    with monkeypatch.context() as patch:
        original = why_here.siblings_and_stops
        patch.setattr(why_here, 'siblings_and_stops', lambda c, task, cause, **kw: original(c, task, cause))
        with pytest.raises(AssertionError):
            assert get(api, path, attempt['token'])['siblings'] == baseline


def test_unreadable_approval_is_not_a_duplicate(api, monkeypatch):
    from backend import why_here
    t, attempt = task_turn(api)
    private = post(api, 'tasks', {'owner': 'human:ben', 'title': 'Private spend decision', 'body': 'Private', 'private': True})
    payload = {'amount': '10', 'account': 'default', 'what': 'Render'}
    hidden = post(api, 'approvals', {'kind': 'spend', 'payload': payload, 'task_id': private['id']})
    get(api, 'origins/' + hidden['id'], attempt['token'], expected=404)
    request = {'kind': 'spend', 'payload': payload, 'task_id': t['id']}
    with monkeypatch.context() as patch:
        patch.setattr(why_here, 'approval_readable', lambda *a: True)
        with pytest.raises(AssertionError):
            no_sources(post(api, 'approvals', request, attempt['token'], expected=422), (hidden['id'],))
    made = post(api, 'approvals', request, attempt['token'])
    assert made['id'] != hidden['id']
    duplicate = post(api, 'approvals', request, attempt['token'], expected=422)
    no_sources(duplicate, (hidden['id'],))
    assert made['id'] in json.dumps(duplicate)


def test_scoped_hub_task_attach_streams_twelve_mb(api, tmp_path, monkeypatch):
    import io
    from urllib.parse import urlsplit
    from urllib.error import HTTPError
    from clients import hubcli, hubtools, remotecli
    from clients.tico import Client
    from clients import bot_files
    t, attempt = task_turn(api)
    token = attempt['token']
    config = get(api, 'config', token)
    assert set(config) == {'features', 'upload_max_bytes'}
    assert config['features']['task_files_multipart'] and config['upload_max_bytes'] >= 12 * 1024 * 1024
    path = tmp_path / 'report.pdf'
    with path.open('wb') as stream:
        stream.write(b'%PDF-1.4\n')
        stream.truncate(12 * 1024 * 1024)
    calls = []
    class Opener:
        def open(self, request, timeout=None):
            parts = urlsplit(request.full_url)
            data = request.data
            if data is not None and not isinstance(data, bytes):
                data = b''.join(data)
            response = api.request(request.method, parts.path, content=data,
                                   headers=dict(request.header_items()))
            calls.append((request.method, parts.path, request.get_header('Content-type')))
            if response.status_code >= 400:
                raise HTTPError(request.full_url, response.status_code, 'Rejected', response.headers,
                                io.BytesIO(response.content))
            out = io.BytesIO(response.content)
            out.headers = response.headers
            return out
    client = Client('http://testserver', token, retries=0)
    client.opener = Opener()
    monkeypatch.setattr(remotecli, 'Client', lambda *a, **kw: client)
    monkeypatch.setenv('HUB_API_URL', 'http://testserver')
    args = hubcli.parser().parse_args(['task', 'attach', t['id'], str(path)])
    uploaded = remotecli.run(args)
    assert uploaded['file']['size'] == path.stat().st_size
    assert any(method == 'POST' and content_type.startswith('multipart/form-data')
               for method, _, content_type in calls)
    # The local MCP tool shares feature negotiation and streaming, through the same authenticated API.
    monkeypatch.setattr(bot_files, 'checkout_root', lambda: tmp_path)
    second = hubtools.task_attach(client, {'id': t['id'], 'name': 'mcp-report.pdf', 'path': str(path)})
    assert second['file']['size'] == path.stat().st_size
    assert get(api, 'config')['company_name']


def test_folded_questions_do_not_copy_scrubbed_message_text(api, monkeypatch):
    data = sources(api)
    _, attempt = scoped(api, data, False)
    token, tid = attempt['token'], data['task']['id']
    question = 'Review the source?'
    request = {'on': 'ana', 'question': question, 'parent': tid, 'topic': 'source-followup',
               'options': [{'label': 'Continue', 'then': 'Continue'}, {'label': 'Wait', 'then': 'Wait'}]}
    made = post(api, 'task-questions', request, token)
    # This is a generated question: retain the same structured provenance as real review answers.
    with api.app.state.store.transaction() as c:
        refs = H.message(c, made['question']['id'])['refs']
        refs['body_sources'] = [{'file': data['hidden']}]
        wording = 'Review ' + data['source_label'] + '?'
        refs['questions'][0]['question'] = wording
        c.execute('UPDATE messages SET body=?,refs=? WHERE id=?', (wording, H._dump(refs), made['question']['id']))
    narrow(api, data)
    request['question'] = 'Continue with the remaining source?'
    folded = post(api, 'task-questions', request, token)
    assert folded['folded']
    no_sources(folded, secrets(data))
    no_sources(get(api, 'tasks/' + made['task']['id'], token), secrets(data))


def test_origins_task_prefix_ignores_unreadable_matches(api):
    t, attempt = task_turn(api)
    private = post(api, 'tasks', {'owner': 'human:ben', 'title': 'Private prefix match', 'body': 'Private', 'private': True})
    # Reproduce a short-id collision without depending on random UUIDs colliding.
    with api.app.state.store.transaction() as c:
        row = dict(c.execute('SELECT * FROM tasks WHERE id=?', (private['id'],)).fetchone())
        row['id'] = t['id'][:8] + '-0000-0000-0000-000000000000'
        c.execute('INSERT INTO tasks (' + ','.join(row) + ') VALUES (' + ','.join('?' * len(row)) + ')', tuple(row.values()))
    get(api, 'origins/' + row['id'], attempt['token'], expected=404)
    shown = get(api, 'origins/' + t['id'][:8], attempt['token'])
    assert shown['id'] == t['id']


def test_plain_answer_previews_keep_the_message_body_provenance(api):
    data = sources(api)
    _, attempt = scoped(api, data, False)
    # Legacy answers may have body provenance without a structured answer map.
    with api.app.state.store.transaction() as c:
        mid = data['answered']['comment']['id']
        refs = H.message(c, mid)['refs']
        refs.pop('answer')
        c.execute('UPDATE messages SET refs=? WHERE id=?', (H._dump(refs), mid))
    narrow(api, data)
    token = attempt['token']
    for path in ('messages/' + data['made']['question']['id'],
                 'tasks/' + data['made']['task']['id'],
                 'tasks/' + data['made']['task']['id'] + '/comments'):
        no_sources(get(api, path, token), secrets(data))


def publication_collision(api, kind):
    machine, publisher = turn(api)
    body = ({'url': 'https://acme.example/roadmap'} if kind == 'links' else
            {'name': 'roadmap.md', 'text': '# Caller supplied bytes'}) | {'scope': 'bot'}
    # A chat turn publishes locally; its manager promotes the source without pinning it to a task.
    hidden = post(api, 'files/' + kind, {**body, 'scope': '', 'title': 'Restricted roadmap name'}, publisher['token'])['file']
    promoted = api.patch('/api/v2/files/' + hidden['id'], json={'promote': True}, headers=headers('ben-test'))
    assert promoted.status_code == 200, promoted.text
    post(api, f'attempts/{publisher["id"]}/started', {'thread_id': 'publication-source'}, machine['token'])
    post(api, f'attempts/{publisher["id"]}/complete', {'outcome': 'completed', 'last_seq': 0}, machine['token'])
    task = post(api, 'tasks', {'owner': 'ops', 'title': 'Prepare a public plan', 'body': 'Write the plan.', 'private': False})
    attempt = claim(api, machine, 'ops', key='publication-task')
    assert attempt['task']['id'] == task['id']
    no_sources(get(api, 'bots/ops/files', attempt['token']), (hidden['id'], hidden['title']))
    return body, hidden, task, attempt


@pytest.mark.parametrize('kind', ['links', 'uploads'])
def test_publication_matches_only_readable_files_and_keeps_hidden_series(api, monkeypatch, kind):
    from backend.files import Files
    body, hidden, task, attempt = publication_collision(api, kind)
    token, path = attempt['token'], 'files/' + kind
    shown = post(api, path, body, token, key='publication-collision')
    assert shown['created'] and 'file' not in shown
    no_sources(shown, (hidden['id'], hidden['title']))
    assert post(api, path, body, token, key='publication-collision') == shown
    with api.app.state.store.read() as c:
        original = c.execute('SELECT * FROM bot_files WHERE id=?', (hidden['id'],)).fetchone()
        assert original['title'] == hidden['title'] and original['task_id'] is None
        assert original['current_version'] == (1 if kind == 'uploads' else 0)
        created = c.execute('SELECT * FROM bot_files WHERE task_id=? AND scope=?', (task['id'], 'bot')).fetchall()
        assert len(created) == 1 and created[0]['id'] != hidden['id'] and created[0]['title'] != hidden['title']
    # A readable match still continues its existing series.
    readable = post(api, path, {**body, 'scope': 'task'}, token)
    again = post(api, path, {**body, 'scope': 'task'}, token)
    assert readable['created'] and not again['created'] and again['file']['id'] == readable['file']['id']
    # The matcher must protect the write itself, independently of response projection.
    with monkeypatch.context() as patch:
        find = Files.find
        patch.setattr(Files, 'find', lambda self, c, bot, scope, identity, **kw:
                      find(self, c, bot, scope, identity))
        with pytest.raises(AssertionError):
            result = post(api, path, body, token)
            assert result['created']


def test_reference_objects_are_projected_under_any_response_key(api, monkeypatch):
    from backend import scope_projection
    _, hidden, task, attempt = publication_collision(api, 'links')
    private = post(api, 'tasks', {'owner': 'human:ben', 'title': 'Restricted task title',
                                  'body': 'Private task body', 'private': True}, 'ben-test')
    private_message = post(api, 'tasks/' + private['id'] + '/comments', {'text': 'Private message body'},
                           'ben-test')['comment']
    comment = post(api, 'tasks/' + task['id'] + '/comments', {'text': 'Ordinary task comment'})['comment']
    fields = {key: 'Restricted copied text' for key in H.SOURCED_TEXT_FIELDS}
    opaque = {'id': 'unknown-source', 'text': 'Keep literal ' + hidden['id'],
              'other': json.dumps({'file': hidden['id']})}
    with api.app.state.store.transaction() as c:
        refs = H.message(c, comment['id'])['refs']
        refs.update({'file': {'id': hidden['id'], **fields}, 'page': {'id': hidden['id'], **fields},
                     'arbitrary': [{'nested': {'id': hidden['id'], **fields}},
                                   {'source_identity': hidden['id'], **fields}],
                     'unrelated': opaque})
        c.execute('UPDATE messages SET refs=? WHERE id=?', (H._dump(refs), comment['id']))
    def check():
        shown = get(api, 'messages/' + comment['id'], attempt['token'])
        assert shown['refs']['unrelated'] == opaque
        no_sources({k: v for k, v in shown['refs'].items() if k != 'unrelated'},
                   ('Restricted copied text', private['title'], private_message['body']))
    check()
    with monkeypatch.context() as patch:
        patch.setattr(scope_projection, 'object_refs', lambda value:
                      ((key, item) for key, item in value.items() if key in scope_projection.REF_FIELDS))
        with pytest.raises(AssertionError):
            check()
    # Message routes refuse private task ancestry before serialization. Other response shapes still use the
    # same source projector, so resolve task/message identities against these real stored private sources too.
    with api.app.state.store.read() as c:
        auth = api.app.state.auth
        who = auth.authenticate({'authorization': 'Bearer ' + attempt['token']})
        metadata = {'task_copy': {'id': private['id'], 'title': private['title']},
                    'message_copy': {'handle': private_message['id'], 'text': private_message['body']},
                    'opaque': {**opaque, 'refs': {'answer': 'Opaque answer'}, 'answers': {'draft': 'Keep this string'}}}
        shown = turn_scope.project(c, turn_scope.scope(c, who), metadata, auth=auth)
        assert shown['opaque'] == metadata['opaque']
        no_sources(shown, (private['title'], private_message['body']))


def test_sourced_text_provenance_covers_sibling_and_nested_fields(api, monkeypatch):
    _, hidden, task, attempt = publication_collision(api, 'links')
    comment = post(api, 'tasks/' + task['id'] + '/comments', {'text': 'Ordinary task comment'})['comment']
    text, provenance = H.sourced_text('Restricted generated prose', [{'file': hidden['id']}])
    copied = {key: text for key in H.SOURCED_TEXT_FIELDS}
    with api.app.state.store.transaction() as c:
        refs = H.message(c, comment['id'])['refs']
        refs.update({'beside': {**copied, **provenance},
                     'carried': {**copied, 'refs': provenance},
                     'nested': {'refs': {**provenance, 'answer': copied}},
                     'sources_beside_other': {'other': text, 'sources': [{'file': hidden['id']}]}})
        c.execute('UPDATE messages SET refs=? WHERE id=?', (H._dump(refs), comment['id']))
    def check():
        no_sources(get(api, 'messages/' + comment['id'], attempt['token']), (text,))
    check()
    with monkeypatch.context() as patch:
        patch.setattr(H, 'SOURCED_TEXT_FIELDS', tuple(key for key in H.SOURCED_TEXT_FIELDS if key != 'other'))
        with pytest.raises(AssertionError):
            check()


def test_quoted_answers_clear_other_on_answer_and_message_paths(api, monkeypatch):
    t, attempt = task_turn(api)
    hidden = published(api, attempt, task=t['id'], name='draft.html', title='Confidential draft')
    allowed = published(api, attempt, task=t['id'], name='public.html', title='Public draft')
    made = ask(api, attempt['token'], [{'file': hidden}, {'file': allowed}], pick=True, parent=t['id'], private=False)
    tid, qid = made['task']['id'], made['question']['id']
    resolved = post(api, f'tasks/{tid}/comments/{qid}/resolve', {'quote': 'Confidential draft'})
    mid = resolved['comment']['id']
    assert get(api, 'messages/' + mid)['refs']['answer']['other'] == 'Confidential draft'
    archived = api.patch('/api/v2/files/' + hidden, json={'archived': True}, headers=headers())
    assert archived.status_code == 200, archived.text
    get(api, 'files/' + hidden, attempt['token'], expected=403)
    for path in ('tasks/' + tid + '/answers', 'messages/' + mid, 'tasks/' + tid + '/comments'):
        no_sources(get(api, path, attempt['token']), ('Confidential draft',))
        # Each response path must independently detect a missing covered field.
        with monkeypatch.context() as patch:
            patch.setattr(H, 'SOURCED_TEXT_FIELDS', tuple(key for key in H.SOURCED_TEXT_FIELDS if key != 'other'))
            with pytest.raises(AssertionError):
                no_sources(get(api, path, attempt['token']), ('Confidential draft',))


def test_letter_blob_metadata_checks_scope_before_letter_access(api, outgoing_hq, monkeypatch):
    from backend.tests.test_letters import grant, quote, send
    grant(api, 'ops')
    row = send(api, quote(api))
    with api.app.state.store.read() as c:
        bid = c.execute('SELECT source_blob_id FROM letters WHERE id=?', (row['id'],)).fetchone()[0]
    _, attempt = task_turn(api)
    path = 'files/' + bid + '/meta'
    assert get(api, path)['size'] > 0
    get(api, path, attempt['token'], expected=404)
    with monkeypatch.context() as patch:
        patch.setattr(turn_scope, 'blob_allowed', lambda *a: True)
        with pytest.raises(AssertionError):
            get(api, path, attempt['token'], expected=404)


@pytest.mark.parametrize('revocation', ['archive', 'audience'])
@pytest.mark.parametrize('legacy', [False, True], ids=['stored-sources', 'legacy-card'])
def test_assistant_action_review_filename_clears_on_every_return(api, monkeypatch, revocation, legacy):
    from backend import assistant, files
    from backend.tests.test_page_audience import edit
    from backend.tests.test_task_review import ask as version_ask
    person = 'ana' if revocation == 'archive' else 'cara'
    human = person + '-test'
    _, finance = turn(api, 'finance')
    machine = runner(api)
    assign(api, machine, 'coo')
    ready(api, machine, ['coo'])
    t = post(api, 'tasks', {'owner': 'coo', 'title': 'Review the project plan',
                            'body': 'Review the linked project context.', 'private': False}, finance['token'])
    attempt = claim(api, machine, 'coo')
    assert attempt and attempt['task']['id'] == t['id']
    filename = 'confidential-plan.html'
    fid = published(api, finance, task=t['id'], name=filename)
    response = api.patch(f'/api/v2/files/{fid}/versions/1', json={'ask': version_ask(person)},
                         headers=headers(finance['token']))
    assert response.status_code == 200, response.text
    with api.app.state.store.read() as c:
        qid = c.execute('SELECT ask_message_id FROM task_file_reviews WHERE file_id=? AND version=1', (fid,)).fetchone()[0]
        assert filename in H.message(c, qid)['body']
    request = {'summary': 'Answer about ' + filename, 'path': f'/api/v2/messages/{qid}/answer', 'body': {'text': 'Yes'}}
    made = post(api, 'assistant/actions', request, human)
    aid = made['action']['id']
    assert filename in made['action']['description']
    assert filename in get(api, 'assistant/actions/' + aid, human)['action']['description']
    assert filename in json.dumps(get(api, 'assistant/proposals', human))
    batch = None
    if not legacy and revocation == 'archive':
        batch = {'summary': request['summary'], 'path': assistant.BATCH_PATH,
                 'body': {'actions': [{'path': request['path'], 'body': request['body']}]}}
        card = post(api, 'assistant/actions', batch, human)['action']
        assert filename in json.dumps(card['diff'])
    with api.app.state.store.transaction() as c:
        row = dict(c.execute('SELECT * FROM assistant_actions WHERE id=?', (aid,)).fetchone())
        assert fid in row['refs']
        assert fid in json.dumps(H.message(c, made['message_id'])['refs']['body_sources'])
        event = c.execute("SELECT detail FROM events WHERE action='assistant.action.proposed' AND target=?", (aid,)).fetchone()[0]
        assert fid in event
        if legacy:
            c.execute('UPDATE assistant_actions SET refs=NULL WHERE id=?', (aid,))
    if revocation == 'archive':
        response = api.patch('/api/v2/files/' + fid, json={'archived': True}, headers=headers())
        assert response.status_code == 200, response.text
    else:
        edit(api, fid, {'people': ['ben']})
        get(api, 'pages/' + fid, human, expected=404)
        # Ordinary get/list/chat returns also apply the shared clearer when the person loses page access.
        for path in ('assistant/actions/' + aid, 'assistant/proposals', 'assistant'):
            no_sources(get(api, path, human), (filename,))
    token = attempt['token']
    get(api, 'files/' + fid, token, expected=403)
    no_sources(get(api, 'messages/' + qid, token, expected=200 if revocation == 'archive' else 404), (filename,))
    proposed = {**request, 'for': 'human:' + person}

    def check(key):
        repeated = post(api, 'assistant/actions', proposed, token, key=key)
        assert repeated['repeated'] and repeated['action']['id'] == aid
        no_sources(repeated, (filename,))
    check('assistant-review-duplicate')
    check('assistant-review-duplicate')
    check('assistant-review-fresh-key')
    # A distinct request exercises create after revocation, including the caller-supplied summary.
    fresh = post(api, 'assistant/actions', {**proposed, 'body': {'text': 'Yes, proceed'}}, token)
    assert fresh['action']['id'] != aid
    no_sources(fresh, (filename,))
    for field in ('description', 'summary'):
        with monkeypatch.context() as patch:
            patch.setattr(H, 'SOURCED_TEXT_FIELDS', tuple(key for key in H.SOURCED_TEXT_FIELDS if key != field))
            with pytest.raises(AssertionError):
                check('assistant-review-without-' + field)
    if batch:
        def check_batch(key):
            repeated = post(api, 'assistant/actions', {**batch, 'for': 'human:' + person}, token, key=key)
            assert repeated['repeated'] and repeated['action']['id'] == card['id']
            no_sources(repeated, (filename,))
        check_batch('assistant-batch-source')
        with monkeypatch.context() as patch:
            patch.setattr(H, 'SOURCED_TEXT_FIELDS', tuple(key for key in H.SOURCED_TEXT_FIELDS if key != 'field'))
            with pytest.raises(AssertionError):
                check_batch('assistant-batch-without-field')
    # The legacy recovery is independently necessary; it cannot infer provenance from display text.
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE assistant_actions SET refs=NULL WHERE id=?', (aid,))
    if revocation == 'archive':
        # Audience revocation also rejects the message identity in the request path, independently of text sources.
        with monkeypatch.context() as patch:
            patch.setattr(assistant, 'action_sources', lambda *a: [])
            with pytest.raises(AssertionError):
                check('assistant-review-without-sources')
    # Test each action-view guard directly, before middleware can repair an unprojected return.
    with api.app.state.store.read() as c:
        auth = api.app.state.auth
        who = auth.authenticate({'authorization': 'Bearer ' + token})
        row = dict(c.execute('SELECT * FROM assistant_actions WHERE id=?', (aid,)).fetchone())
        principal, granting = turn_scope.WHO.set(who), turn_scope.AUTH.set(auth)
        try:
            no_sources(assistant.action_view(row, c), (filename,))
            with monkeypatch.context() as patch:
                if revocation == 'archive':
                    patch.setattr(turn_scope, 'project', lambda c, scoped, value, **kw: value)
                else:
                    # The page guard must clear immediately even when it is the first guard to run.
                    patch.setattr(turn_scope, 'project', lambda c, scoped, value, **kw: value)
                    no_sources(assistant.action_view(row, c), (filename,))
                    patch.setattr(files, 'readable_page_refs', lambda c, auth, who, value, **kw: value)
                with pytest.raises(AssertionError):
                    no_sources(assistant.action_view(row, c), (filename,))
        finally:
            turn_scope.AUTH.reset(granting)
            turn_scope.WHO.reset(principal)


def test_stored_task_copies_and_deferred_notes_keep_the_message_sources(api, monkeypatch):
    from backend import scope_projection, threads
    data = sources(api)
    mid, secret = data['answered']['comment']['id'], data['source_label']
    machine = runner(api)
    assign(api, machine, 'finance')
    ready(api, machine, ['finance'])
    path = f'threads/messages/{mid}/task'
    made = post(api, path, {'title': 'Forward the review', 'owner': 'finance'})
    assert secret in made['body']
    with monkeypatch.context() as patch:
        patch.setattr(H, 'remember_sourced_text', lambda c, kind, rid, field, text, sources: text)
        bare = post(api, path, {'title': 'Forward another review', 'owner': 'finance'})
    with api.app.state.store.transaction() as c:
        stored = c.execute("SELECT refs FROM sourced_text_records WHERE kind='task' AND record_id=? AND field='body'",
                           (made['id'],)).fetchone()
        assert stored and data['hidden'] in stored[0]
        threads._defer_note(c, H.message(c, mid), 'bot:finance')
        note = dict(c.execute('SELECT * FROM notes WHERE message_id=? AND to_actor=?', (mid, 'bot:finance')).fetchone())
        assert secret in note['body']
    response = api.patch('/api/v2/files/' + data['hidden'], json={'archived': True}, headers=headers())
    assert response.status_code == 200, response.text
    attempt = claim(api, machine, 'finance')
    assert attempt and attempt['task']['id'] == made['id']
    no_sources(attempt, (secret,))
    no_sources(get(api, 'tasks/' + made['id'], attempt['token']), (secret,))
    exposed = get(api, 'tasks/' + bare['id'], attempt['token'])
    with pytest.raises(AssertionError):
        no_sources(exposed, (secret,))
    with api.app.state.store.read() as c:
        auth = api.app.state.auth
        who = auth.authenticate({'authorization': 'Bearer ' + attempt['token']})
        scoped = turn_scope.scope(c, who)
        no_sources(turn_scope.project(c, scoped, note, auth=auth), (secret,))
        with monkeypatch.context() as patch:
            patch.setattr(scope_projection, 'project_notes', lambda c, scoped, payload, auth: payload)
            with pytest.raises(AssertionError):
                no_sources(turn_scope.project(c, scoped, note, auth=auth), (secret,))


def test_legacy_dependency_reasons_rebuild_from_the_scoped_question(api, monkeypatch):
    from backend import models, task_review
    from backend.tests.test_task_review import ask as version_ask
    t, attempt = task_turn(api)
    filename = 'confidential-hold.html'
    fid = published(api, attempt, task=t['id'], name=filename)
    with monkeypatch.context() as patch:
        patch.setattr(H, 'remember_sourced_text', lambda c, kind, rid, field, text, sources: text)
        # Seed a legacy version question through its writer: scoped HTTP deliberately excludes version edits.
        with api.app.state.store.transaction() as c:
            auth = api.app.state.auth
            who = auth.authenticate({'authorization': 'Bearer ' + attempt['token']})
            task_review.edit_version(c, auth, who, t['id'], fid, 1,
                                     models.FileVersionEdit(ask=version_ask('ana')))
        post(api, 'tasks/' + t['id'], {'version': get(api, 'tasks/' + t['id'])['task']['version'],
                                     'status': 'waiting', 'waiting_on': 'human:ana'}, attempt['token'])
    with api.app.state.store.read() as c:
        row = H.task(c, t['id'])
        assert filename in row['blocked_reason']
        assert row['blocked_kind'] == 'dependency'
    response = api.patch('/api/v2/files/' + fid, json={'archived': True}, headers=headers())
    assert response.status_code == 200, response.text
    no_sources(get(api, 'tasks/' + t['id'], attempt['token']), (filename,))
    with monkeypatch.context() as patch:
        patch.setattr(turn_scope, 'message_view', lambda c, message, **kw: message)
        with pytest.raises(AssertionError):
            no_sources(get(api, 'tasks/' + t['id'], attempt['token']), (filename,))
