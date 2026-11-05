"""Privacy sweep using the same writes that produce review cards and runner inputs."""
import json

import pytest

from backend import scoped_routes, turn_scope
from backend.store import H
from backend.tests.test_api import api, assign, claim, get, headers, post, ready, runner  # noqa: F401
from backend.tests.test_assistant import assistant_turn
from backend.tests.test_file_reviews import ask, answer
from backend.tests.test_files import turn
from backend.tests.test_meeting_items import record
from backend.tests.test_meeting_task_links import link, task
from backend.tests.test_turn_scope import blob, make_scope, published, task_turn
from backend.tests.test_pages import HTML


def no_sources(value, secrets):
    wire = json.dumps(value, ensure_ascii=False)
    for secret in secrets:
        assert secret not in wire, (secret, wire[:4000])


def sources(api):
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    t = post(api, "tasks", {"owner": "ops", "title": "Review source context", "body": "Review the sources.", "private": False})
    publisher = claim(api, machine, "ops", key="source-publish")
    hidden = published(api, publisher, task=t["id"], name="restricted.html", title="Restricted source",
                       data=HTML + b"<p>Restricted source content</p>")
    allowed = published(api, publisher, task=t["id"], name="allowed.html", title="Allowed source")
    made = ask(api, publisher["token"], [{"file": hidden}, {"file": allowed}], pick=True,
               parent=t["id"], private=False)
    answered = answer(api, made, ["Restricted source"])
    meeting = record(api, {"private": False, "title": "Restricted meeting"})
    link(api, meeting, [t["id"]])
    good_meeting = record(api, {"private": False, "title": "Allowed meeting"})
    link(api, good_meeting, [t["id"]])
    bid = blob(api, hidden)
    with api.app.state.store.read() as c:
        version = dict(c.execute("SELECT * FROM bot_file_versions WHERE file_id=?", (hidden,)).fetchone())
    return dict(task=t, machine=machine, publisher=publisher, hidden=hidden, allowed=allowed, made=made, answered=answered, meeting=meeting,
                good_meeting=good_meeting, bid=bid, version=version)


def narrow(api, data):
    from backend import meetings
    private = task(api, owner="human:ben", requester="human:ben", private=True)
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO task_assets(task_id,blob_id) VALUES(?,?)", (private["id"], data["bid"]))
        row = meetings.get(data["meeting"], c)
        meetings.snapshot({**row["metadata"], "private": True, "audience": "participants"}, conn=c)


def secrets(data):
    v = data["version"]
    return tuple(x for x in (data["hidden"], data["bid"], v["digest"], v["source_digest"],
                            "Restricted source", "restricted.html", data["meeting"], "Restricted meeting") if x)


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
    narrow(api, data)
    paths = [f"tasks/{tid}/context", "pages/" + data["allowed"], "meetings/transcript?id=" + data["good_meeting"]]
    if not make:
        paths += ["tasks", f"tasks/{tid}", f"tasks/{tid}/comments", f"tasks/{tid}/answers",
                  "tasks/" + data["made"]["task"]["id"],
                  "tasks/" + data["made"]["task"]["id"] + "/comments",
                  "tasks/" + data["made"]["task"]["id"] + "/answers",
                  "messages/" + data["made"]["question"]["id"],
                  "messages/" + data["answered"]["comment"]["id"],
                  "conversations/" + data["made"]["task"]["conversation_id"] + "/messages",
                  "conversations/" + data["made"]["task"]["conversation_id"] + "/snapshot",
                  "files/" + data["allowed"] + "/meta", "bots/ops/files", "pages", "files/search?q=source"]
    for path in paths:
        no_sources(get(api, path, token), secrets(data))
    for path in ("files/" + data["hidden"], "files/" + data["bid"], "files/" + data["hidden"] + "/versions/1"):
        get(api, path, token, expected=403)
    for path in ("pages/" + data["hidden"], "meetings/transcript?id=" + data["meeting"]):
        get(api, path, token, expected=404)
    method = "hub_task_context" if make else "hub_task_show"
    result = post(api, "mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                               "params": {"name": method, "arguments": {"id": tid}}}, token)
    assert not result.get("error") and not result["result"].get("isError"), result
    no_sources(result, secrets(data))
    if not make:
        replay = claim(api, machine, "ops", key="real-write-claim")
        no_sources(replay, secrets(data))
        post(api, f'attempts/{attempt["id"]}/started', {"thread_id": "scope-inputs"}, token=machine["token"])
        # The normal task comment API queues a steering input for the active turn.
        post(api, f"tasks/{tid}/comments", {"text": "Read the review on the task"})
        for key in ("real-input", "real-input", "real-input-fresh"):
            no_sources(post(api, f'attempts/{attempt["id"]}/inputs', {}, machine["token"], key=key), secrets(data))


@pytest.mark.parametrize("make", [False, True], ids=["task-bot", "make-page"])
def test_scoped_routes_deny_sql_csv_history_downloads_and_new_surfaces(api, make):
    data = sources(api)
    _, attempt = scoped(api, data, make)
    token = attempt["token"]
    for path in ("history.csv?kind=decision", "history?kind=decision", "events", "admin",
                 "live-meetings/missing/events", "runners/me/events", "not-yet-reviewed"):
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
    with monkeypatch.context() as patch:
        patch.setattr(scoped_routes, "require_route", lambda *a: None)
        # The same sweep's deny assertion fails when the allowlist is bypassed.
        with pytest.raises(AssertionError):
            get(api, "history.csv?kind=decision", attempt["token"], expected=403)
