"""A single forbidden source is absent from every scoped delivery, regardless of stored shape."""
import json
from urllib.parse import quote

import pytest

from backend import turn_scope
from backend.store import H, encode
from backend.tests.test_api import api, assign, claim, get, headers, post, ready, runner  # noqa: F401
from backend.tests.test_assistant import say
from backend.tests.test_files import turn
from backend.tests.test_meeting_items import record
from backend.tests.test_meeting_task_links import link, task
from backend.tests.test_pages import HTML
from backend.tests.test_task_review import ask
from backend.tests.test_turn_scope import blob, published


def all_shapes(fid, bid, digest, title, mid):
    attachment = {"id": fid, "file_id": fid, "version": 1, "ref": fid + "@1", "title": title,
                  "name": title, "kind": "html", "digest": digest, "blob_id": bid}
    review = {"file": fid, "version": 1, "digest": digest, "title": title}
    return {"attachments": [attachment], "files": [fid + "@1", bid], "file": review,
            "review": {"files": [review]},
            "questions": [{"id": "choose", "options": [{"label": title, "file": review},
                                                          {"label": title, "file": fid}]}],
            "answer": {"target": {"file": fid, "version": 1}, "reviewed": [{**review, "verdict": "approve"}],
                       "review": [review], "files": [review]},
            "message": {"refs": {"attachments": [attachment]}},
            "page": {"path": "#/page/" + fid, "title": title},
            "pages": [{"id": fid, "title": title}, {"file_id": fid, "title": title},
                      {"page": fid, "title": title}, {"page_id": fid, "title": title},
                      {"ref": "page:" + fid, "title": title}, {"url": "/?page=" + fid, "title": title},
                      {"href": "#/page/" + fid, "title": title},
                      {"path": "#/page/" + fid, "title": title},
                      {"src": "/api/v2/pages/" + quote(fid, safe=""), "title": title}],
            "versions": [{"id": bid, "title": title}, {"digest": digest, "title": title}],
            "meetings": [{"id": mid, "title": "Forbidden meeting title"}],
            "page_links": [fid, "page:" + fid, "#/page/" + fid, "/api/v2/pages/" + fid,
                           "https://acme.example/?page=" + fid, fid + "@1"],
            "blob_links": [bid, digest]}


def append_allowed(refs, fid, bid, digest):
    refs["attachments"].append({"id": fid, "file_id": fid, "version": 1, "title": "Allowed page title"})
    refs["files"].append(fid + "@1")
    refs["review"]["files"].append({"file": fid, "version": 1, "digest": digest})
    refs["pages"].append({"page": fid, "title": "Allowed page title"})
    refs["pages"].append({"path": "#/page/" + fid, "title": "Allowed page title"})


def no_sources(value, secrets):
    wire = json.dumps(value, ensure_ascii=False)
    for secret in secrets:
        assert secret not in wire, (secret, wire[:4000])


@pytest.mark.parametrize("assistant", [False, True], ids=["task-bot", "make-page-assistant"])
def test_every_stored_shape_is_projected_at_every_delivery(api, assistant):
    t = task(api, owner="bot:ops", private=False)
    other = task(api, owner="bot:cpo", private=False)
    _, publisher = turn(api, "cpo")
    hidden = published(api, publisher, task=other["id"], name="forbidden-page.html", title="Forbidden page title",
                       data=HTML + b"<p>Forbidden unique bytes</p>")
    allowed = published(api, publisher, task=t["id"], name="allowed-page.html", title="Allowed page title")
    mid = record(api, {"private": True, "title": "Forbidden meeting title"})
    shared = record(api, {"private": False, "title": "Allowed meeting title"})
    link(api, shared, [t["id"]])
    store = api.app.state.store
    with store.read() as c:
        version = dict(c.execute("SELECT * FROM bot_file_versions WHERE file_id=?", (hidden,)).fetchone())
        good = dict(c.execute("SELECT * FROM bot_file_versions WHERE file_id=?", (allowed,)).fetchone())
    bid, digest = version["blob_id"], version["digest"]
    refs = all_shapes(hidden, bid, digest, "Forbidden page title", mid)
    missing = H.new_id()
    refs["attachments"].append({"id": missing, "title": "Missing source title"})
    refs["files"].append(missing + "@1")
    refs["review"]["files"].append({"file": missing, "version": 1, "title": "Missing source title"})
    append_allowed(refs, allowed, good["blob_id"], good["digest"])
    if version["source_digest"]:
        refs["versions"].append({"source_digest": version["source_digest"], "title": "Forbidden page title"})
    with store.transaction() as c:
        c.execute("INSERT OR IGNORE INTO task_assets(task_id,blob_id) VALUES(?,?)", (other["id"], bid))
        question = H.task_comment(c, "bot:ops", t["id"], "Review the source versions", wake=False,
                                  ask=ask("ana"), extra_refs=refs)
        answer = H.task_comment(c, "human:ana", t["id"], "Reviewed", wake=False, extra_refs=refs)
        # Keep the ask open too: task.ask and task listings must exercise the same nested references.
        c.execute("UPDATE messages SET refs=? WHERE id=?", (encode({**answer["refs"], "answer": {
            "target": {"comment": question["id"]}, "reviewed": refs["review"]["files"],
            "answers": {"verdict": ["Approve"]}}}), answer["id"]))
        comment = H.task_comment(c, "human:ana", t["id"], "Sources are attached", wake=False, extra_refs=refs)
    machine = runner(api)
    bot = "coo" if assistant else "ops"
    assign(api, machine, bot)
    ready(api, machine, [bot])
    if assistant:
        say(api, "Prepare a page from the task context")
    with store.transaction() as c:
        origin = c.execute("SELECT m.id,m.refs FROM jobs j JOIN messages m ON m.id=j.message_id "
                           "WHERE j.bot_id=? AND j.state='queued' ORDER BY m.rowid LIMIT 1", (bot,)).fetchone()
        original = H._json(origin["refs"], {})
        original.update(refs)
        if assistant:
            original["make_page"] = {"task": t["id"]}
        c.execute("UPDATE messages SET refs=? WHERE id=?", (encode(original), origin["id"]))
    secrets = (hidden, hidden + "@1", bid, digest, "Forbidden page title", "forbidden-page.html", mid, "Forbidden meeting title")
    secrets += (missing, "Missing source title")
    if version["source_digest"]:
        secrets += (version["source_digest"],)
    attempt = claim(api, machine, bot, key="all-shapes-claim")
    assert attempt
    no_sources(attempt, secrets)
    assert allowed in json.dumps(attempt) and "Allowed page title" in json.dumps(attempt)
    assert shared in json.dumps(attempt)
    assert [r["file_id"] for r in attempt["message"]["refs"]["attachments"]] == [allowed]
    assert json.loads(attempt["message"]["refs_json"])["attachments"] == attempt["message"]["refs"]["attachments"]
    # The initiating message and history really carry the seeded references on disk.
    with store.read() as c:
        assert hidden in H.message(c, origin["id"])["refs"]["files"][0]
        assert hidden in json.dumps(H.message(c, comment["id"])["refs"])
    replay = claim(api, machine, bot, key="all-shapes-claim")
    no_sources(replay, secrets)
    assert allowed in json.dumps(replay)
    token = attempt["token"]
    paths = ["tasks", "tasks?ids=" + t["id"] + "," + other["id"], "tasks/" + t["id"],
             "tasks/" + other["id"], "tasks/" + t["id"] + "/comments", "tasks/" + t["id"] + "/answers",
             "tasks/" + t["id"] + "/context", "tasks/" + other["id"] + "/files",
             "conversations/" + t["conversation_id"] + "/messages",
             "conversations/" + t["conversation_id"] + "/snapshot", "messages/" + comment["id"],
             "messages/" + question["id"], "inbox", "pages", "pages?ids=" + hidden + "," + allowed,
             "pages/" + allowed, "files/" + allowed + "/meta", "files/" + allowed + "/versions",
             "files/" + allowed + "/activity", "bots/cpo/files", "meetings/search", "recordings/search",
             "meetings/transcript?id=" + shared, "docs/manual"]
    for path in paths:
        response = api.get("/api/v2/" + path, headers=headers(token))
        if assistant and path not in {
                "tasks/" + t["id"] + "/context", "pages", "pages?ids=" + hidden + "," + allowed,
                "pages/" + allowed, "files/" + allowed + "/meta", "files/" + allowed + "/versions",
                "files/" + allowed + "/activity", "meetings/search", "meetings/transcript?id=" + shared}:
            assert response.status_code == 404, (path, response.text)
            no_sources(response.json(), secrets)
            continue
        assert response.status_code == 200, (path, response.status_code, response.text)
        no_sources(response.json(), secrets)
        assert int(response.headers["content-length"]) == len(response.content)
        if path == "docs/manual":
            assert response.json()["pages"] and all(p["id"].startswith("manual:") for p in response.json()["pages"])
    if assistant:
        response = api.get("/api/v2/assistant", headers=headers(token))
        assert response.status_code == 404
        no_sources(response.json(), secrets)
    for path in ("pages/" + hidden, "files/" + hidden + "/meta", "files/" + hidden + "/versions",
                 "files/" + hidden + "/activity", "files/" + hidden, "files/" + bid,
                 "meetings/transcript?id=" + mid):
        response = api.get("/api/v2/" + path, headers=headers(token))
        assert response.status_code == 404, (path, response.text)
        no_sources(response.json(), secrets)
    for path in ("events?topics=tasks,messages", "live-meetings/missing/events", "runners/me/events"):
        response = api.get("/api/v2/" + path, headers=headers(token))
        assert response.status_code == 403, (path, response.text)
        no_sources(response.json(), secrets)
    if not assistant:
        for sql in ("SELECT refs FROM messages", "SELECT json_extract(refs,'$.review') FROM messages",
                    "SELECT json_extract(refs,'$.questions') FROM messages",
                    "SELECT json_extract(refs,'$.answer') FROM messages",
                    "SELECT json_extract(refs,'$.answer.reviewed') FROM messages"):
            result = post(api, "sql", {"sql": sql}, token)
            no_sources(result, secrets)
    for name, arguments in (("hub_task_show", {"id": t["id"]}), ("hub_task_list", {}),
                            ("hub_task_answers", {"id": t["id"]}), ("hub_task_context", {"id": t["id"]}),
                            ("hub_conversation_show", {"conversation": t["conversation_id"]})):
        result = post(api, "mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                   "params": {"name": name, "arguments": arguments}}, token)
        assert not result.get("error"), result
        if assistant and name != "hub_task_context":
            assert result["result"].get("isError"), result
            no_sources(result, secrets)
            continue
        assert not result["result"].get("isError"), result
        no_sources(result, secrets)
        if name == "hub_task_show":
            assert allowed in json.dumps(result)
    # Ordinary human and unscoped bot projections preserve both sources and all nested metadata.
    _, unscoped = turn(api, "finance")
    for ordinary in ("ana-test", unscoped["token"]):
        result = get(api, "tasks/" + t["id"], ordinary)
        assert hidden in json.dumps(result) and "Forbidden page title" in json.dumps(result)
        with store.read() as c:
            who = api.app.state.auth.authenticate({k.lower(): v for k, v in headers(ordinary).items()}, "/api/v2/me", "GET")
            assert turn_scope.project(c, turn_scope.scope(c, who), refs, auth=api.app.state.auth) is refs
    if assistant:
        with store.read() as c:
            reads = {tuple(r) for r in c.execute("SELECT kind,ref FROM attempt_reads WHERE attempt_id=?", (attempt["id"],))}
        assert ("file", allowed) in reads and ("meeting", shared) in reads
        assert ("file", hidden) not in reads and ("meeting", mid) not in reads


def test_claim_and_runner_inputs_reproject_saved_wire_refs_after_scope_narrows(api):
    t = task(api, owner="bot:ops", private=False)
    _, publisher = turn(api, "cpo")
    fid = published(api, publisher, task=t["id"], title="Narrowed page title")
    bid = blob(api, fid)
    with api.app.state.store.read() as c:
        digest = c.execute("SELECT digest FROM bot_file_versions WHERE file_id=?", (fid,)).fetchone()[0]
    refs = all_shapes(fid, bid, digest, "Narrowed page title", "not-a-meeting")
    with api.app.state.store.transaction() as c:
        origin = c.execute("SELECT m.id,m.refs FROM jobs j JOIN messages m ON m.id=j.message_id "
                           "WHERE j.bot_id='ops' AND j.state='queued' ORDER BY m.rowid LIMIT 1").fetchone()
        c.execute("UPDATE messages SET refs=? WHERE id=?", (encode({**H._json(origin["refs"], {}), **refs}), origin["id"]))
        comment = H.task_comment(c, "human:ana", t["id"], "Source for the prompt", wake=False, extra_refs=refs)
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    attempt = claim(api, machine, "ops", key="narrowed-wire")
    assert fid in json.dumps(attempt["history"]) and fid in json.dumps(attempt["message"])
    private = task(api, owner="human:ben", requester="human:ben", private=True)
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO task_assets(task_id,blob_id) VALUES(?,?)", (private["id"], bid))
    replay = claim(api, machine, "ops", key="narrowed-wire")
    no_sources(replay, (fid, bid, digest, "Narrowed page title"))
    post(api, f'attempts/{attempt["id"]}/started', {"thread_id": "scope-inputs"}, token=machine["token"])
    with api.app.state.store.transaction() as c:
        c.execute("INSERT OR IGNORE INTO attempt_inputs(attempt_id,message_id) VALUES(?,?)", (attempt["id"], comment["id"]))
    for key in ("scoped-input", "scoped-input", "scoped-input-fresh"):
        inputs = post(api, f'attempts/{attempt["id"]}/inputs', {}, token=machine["token"], key=key)
        no_sources(inputs, (fid, bid, digest, "Narrowed page title"))
        assert inputs["messages"]


def test_boundary_alone_logs_sources_before_delivering_even_an_error(api, monkeypatch):
    import sqlite3
    from fastapi import Request
    from fastapi.responses import JSONResponse, PlainTextResponse
    from backend.tests.test_assistant import assistant_turn
    from backend.tests.test_turn_scope import make_scope

    t = task(api, private=False)
    _, publisher = turn(api, "cpo")
    fid = published(api, publisher, task=t["id"])
    live = post(api, "live-meetings", {"title": "Forbidden live title", "visibility": "private"})["id"]
    _, attempt = assistant_turn(api, "ana-test")
    make_scope(api, attempt, t["id"])

    @api.app.get("/api/v2/projection-fixture")
    def fixture(request: Request, plain: str = ""):
        if plain:
            return PlainTextResponse("Forbidden live title " + live if plain == "hidden" else "Allowed source " + fid)
        return JSONResponse({"attachments": [{"file_id": fid, "title": "Allowed source"}],
                             "meetings": [{"id": live, "title": "Forbidden live title"}]}, status_code=409)

    # The production static mount is last; put this test route before that catch-all.
    api.app.router.routes.insert(0, api.app.router.routes.pop())

    response = api.get("/api/v2/projection-fixture", headers={**headers(attempt["token"]), "Accept-Encoding": "gzip"})
    assert response.status_code == 409 and fid in response.text
    assert live not in response.text and "Forbidden live title" not in response.text
    store = api.app.state.store
    with store.read() as c:
        assert c.execute("SELECT 1 FROM attempt_reads WHERE attempt_id=? AND ref=?", (attempt["id"], fid)).fetchone()
    response = api.get("/api/v2/projection-fixture?plain=hidden", headers=headers(attempt["token"]))
    assert response.status_code == 200 and response.content == b""
    response = api.get("/api/v2/projection-fixture?plain=allowed", headers=headers(attempt["token"]))
    assert response.status_code == 200 and response.text == "Allowed source " + fid
    original = store.mutate

    def unavailable(who, operation, *args, **kwargs):
        if operation == "attempt.reads":
            raise sqlite3.OperationalError("Synthetic write failure")
        return original(who, operation, *args, **kwargs)

    monkeypatch.setattr(store, "mutate", unavailable)
    response = api.get("/api/v2/projection-fixture", headers=headers(attempt["token"]))
    assert response.status_code == 503
    assert fid not in response.text and "Allowed source" not in response.text
