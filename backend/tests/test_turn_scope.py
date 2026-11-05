"""Task attempts share only contained sources; direct URLs never bypass that boundary."""
import json
import sqlite3
import base64

import pytest

from backend import blobs, meetings, turn_scope, visuals
from backend.auth import Identity
from backend.store import H, Problem, encode
from backend.tests.test_api import api, assign, claim, get, headers, post, ready, restrict, runner  # noqa: F401
from backend.tests.test_assistant import assistant_turn
from backend.tests.test_cards import page as seed_page
from backend.tests.test_files import turn
from backend.tests.test_meeting_items import record
from backend.tests.test_meeting_task_links import link, task
from backend.tests.test_pages import HTML, page


def task_turn(api, bot="ops", **fields):
    machine = runner(api)
    assign(api, machine, bot)
    ready(api, machine, [bot])
    made = post(api, "tasks", {"owner": bot, "title": "Prepare the project plan",
                               "body": "Review the linked project context.", "private": False, **fields})
    attempt = claim(api, machine, bot)
    assert attempt and attempt["task"]["id"] == made["id"]
    return made, attempt


def carry(api, attempt, tid):
    with api.app.state.store.transaction() as c:
        message = H.task_comment(c, "human:ana", tid, "Also review this task", wake=False)
        c.execute("INSERT INTO attempt_inputs(attempt_id,message_id) VALUES(?,?)", (attempt["id"], message["id"]))


def blob(api, fid):
    with api.app.state.store.read() as c:
        return c.execute("SELECT blob_id FROM bot_file_versions WHERE file_id=?", (fid,)).fetchone()[0]


def published(api, attempt, *, botwide=False, **fields):
    response = page(api, attempt, **fields)
    assert response.status_code == 200, response.text
    fid = response.json()["page"]["file_id"]
    if botwide:
        promoted = api.patch("/api/v2/files/" + fid, json={"promote": True}, headers=headers("ben-test"))
        assert promoted.status_code == 200, promoted.text
    return fid


def make_scope(api, attempt, tid):
    with api.app.state.store.transaction() as c:
        row = c.execute("SELECT m.id,m.refs FROM attempts a JOIN jobs j ON j.id=a.job_id "
                        "JOIN messages m ON m.id=j.message_id WHERE a.id=?", (attempt["id"],)).fetchone()
        c.execute("UPDATE messages SET refs=? WHERE id=?",
                  (encode({**json.loads(row["refs"] or "{}"), "make_page": {"task": tid}}), row["id"]))


def test_task_turn_meetings_and_pages_use_containment_on_every_read(api):
    shared = record(api, {"private": False, "notes": "Decisions only"})
    hidden = record(api, {"private": True, "participants": ["ana@acme.example"]})
    _, source = turn(api, "cpo")
    botwide = published(api, source, name="bot.html", botwide=True)
    dashboard = published(api, source, name="dashboard.html", botwide=True)
    team = published(api, source, name="team.html", botwide=True)
    post(api, "pins", {"place": "dashboard", "item": "page:" + dashboard})
    # A team pin may widen to its group, but never to every task reader.
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO pins(id,place,item,position,size,made_by,created_at) VALUES(?,?,?,?,?,?,?)",
                  (H.new_id(), "team:product", "page:" + team, 0, "medium", "human:ana", H.now()))
    t, attempt = task_turn(api)
    token = attempt["token"]
    for mid in (shared, hidden):
        link(api, mid, [t["id"]])
    for fid in (botwide, dashboard, team):
        post(api, "pins", {"place": "task:" + t["id"], "item": "page:" + fid})
    assert get(api, "meetings/transcript?id=" + shared, token)["notes"] == "Decisions only"
    get(api, "meetings/transcript?id=" + hidden, token, expected=404)
    for fid in (botwide, team):
        for suffix in ("", "/meta", "/versions", "/activity"):
            get(api, "files/" + fid + suffix, token, expected=404)
        get(api, "pages/" + fid, token, expected=404)
        get(api, "files/" + blob(api, fid), token, expected=404)
    assert api.get("/api/v2/files/" + dashboard, headers=headers(token)).status_code == 200
    assert api.get("/api/v2/files/" + blob(api, dashboard), headers=headers(token)).status_code == 200
    context = get(api, "tasks/" + t["id"] + "/context", token)
    assert [p["id"] for p in context["pages"]] == [dashboard]
    assert [m["id"] for m in context["meetings"]] == [shared]
    assert hidden not in json.dumps(context) and "transcript" not in json.dumps(context)
    # Changing a source's policy is effective on the next request, with no cached grant.
    with api.app.state.store.transaction() as c:
        row = meetings.get(shared, c)
        meetings.snapshot({**row["metadata"], "private": True}, conn=c)
    get(api, "meetings/transcript?id=" + shared, token, expected=404)
    assert get(api, "tasks/" + t["id"] + "/context", token)["meetings"] == []
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM attempt_reads WHERE attempt_id=?", (attempt["id"],)).fetchone()


def test_filed_bot_party_pages_are_local_to_that_task_and_multiple_tasks_intersect(api):
    t, attempt = task_turn(api)
    other = task(api, owner="bot:ops", private=False)
    own = page(api, attempt, name="local.html", task=t["id"])
    assert own.status_code == 200, own.text
    fid = own.json()["page"]["file_id"]
    get(api, "pages/" + fid, attempt["token"])
    _, publisher = turn(api, "cpo")
    public_task = task(api, private=False)
    public = page(api, publisher, name="public.html", task=public_task["id"]).json()["page"]["file_id"]
    get(api, "pages/" + public, attempt["token"])
    carry(api, attempt, other["id"])
    get(api, "pages/" + fid, attempt["token"], expected=404)
    get(api, "files/" + blob(api, fid), attempt["token"], expected=404)
    get(api, "pages/" + public, attempt["token"])


def test_private_task_context_checks_guests_participants_managers_and_all_versions(api):
    t, attempt = task_turn(api, private=True)
    # Ben can read a file in his own chat, while the task's other readers cannot.
    _, publisher = turn(api, "cpo")
    private = page(api, publisher, name="personal.html").json()["page"]["file_id"]
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO pins(id,place,item,position,size,made_by,created_at) VALUES(?,?,?,?,?,?,?)",
                  (H.new_id(), "task:" + t["id"], "page:" + private, 0, "medium", "human:ben", H.now()))
    get(api, "pages/" + private, attempt["token"], expected=404)
    own = page(api, attempt, name="own.html", task=t["id"])
    assert own.status_code == 200, own.text
    fid = own.json()["page"]["file_id"]
    get(api, "pages/" + fid, attempt["token"])
    # A later version with narrower task provenance hides the entire file.
    unrelated = task(api, owner="human:ben", requester="human:ben", private=True)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET guest='human:cara' WHERE id=?", (t["id"],))
        c.execute("INSERT INTO task_roles(task_id,role,actor,created_at) VALUES(?,'participant','human:ben',?)",
                  (t["id"], H.now()))
        c.execute("INSERT INTO task_assets(task_id,blob_id) VALUES(?,?)", (unrelated["id"], blob(api, fid)))
    get(api, "pages/" + fid, attempt["token"], expected=404)
    assert get(api, "tasks/" + t["id"] + "/context", attempt["token"])["pages"] == []


def test_attached_bot_reads_its_private_saved_meeting_only_outside_task_turns(api):
    mid = record(api, {"private": True})
    live = post(api, "live-meetings", {"title": "Project meeting", "visibility": "private"})["id"]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE live_meetings SET imported_meeting_id=? WHERE id=?", (mid, live))
        c.execute("INSERT INTO live_meeting_bots(meeting_id,bot,joined_by,joined_at) VALUES(?,'ops','human:ana',?)",
                  (live, H.now()))
    _, unscoped = turn(api)
    get(api, "meetings/transcript?id=" + mid, unscoped["token"])
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE live_meeting_bots SET joined_by='human:ben' WHERE meeting_id=?", (live,))
    get(api, "meetings/transcript?id=" + mid, unscoped["token"], expected=404)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE live_meeting_bots SET joined_by='human:ana' WHERE meeting_id=?", (live,))
    t = task(api, owner="bot:ops", private=False)
    carry(api, unscoped, t["id"])
    get(api, "meetings/transcript?id=" + mid, unscoped["token"], expected=404)


def test_make_scope_guards_legacy_routes_logs_reads_and_rechecks_revocation(api, monkeypatch):
    t = task(api, private=False)
    linked = record(api, {"private": False})
    outside = record(api, {"private": False})
    link(api, linked, [t["id"]])
    _, attempt = assistant_turn(api, "ana-test")
    # Ordinary Assistant reads are unscoped, even if a task is mentioned in the chat.
    get(api, "meetings/transcript?id=" + outside, attempt["token"])
    make_scope(api, attempt, t["id"])
    for suffix in ("", "/versions"):
        response = api.get("/api/meetings/" + outside + suffix, headers=headers(attempt["token"]))
        assert response.status_code == 404, response.text
    for path in ("/api/meetings/" + linked, "/api/meetings/" + linked + "/versions",
                 "/api/v2/meetings/transcript?id=" + linked):
        response = api.get(path, headers=headers(attempt["token"]))
        assert response.status_code == 200, response.text
    post(api, "sql", {"sql": "SELECT id FROM meetings ORDER BY id"}, attempt["token"], expected=403)
    store = api.app.state.store
    with store.read() as c:
        assert [tuple(r) for r in c.execute("SELECT kind,ref FROM attempt_reads WHERE attempt_id=?", (attempt["id"],))] == [
            ("meeting", linked)]
        who = api.app.state.auth.authenticate({k.lower(): v for k, v in headers(attempt["token"]).items()},
                                             "/api/v2/pages", "POST")
        turn_scope.recheck_reads(c, api.app.state.auth, who)
    with store.transaction() as c:
        row = meetings.get(linked, c)
        meetings.snapshot({**row["metadata"], "private": True}, conn=c)
    with store.read() as c, pytest.raises(Problem) as refused:
        turn_scope.recheck_reads(c, api.app.state.auth, who)
    assert refused.value.status == 403
    with store.transaction() as c:
        row = meetings.get(linked, c)
        meetings.snapshot({**row["metadata"], "private": False}, conn=c)
    original = store.mutate
    def unavailable(who, operation, *args, **kwargs):
        if operation == "attempt.reads":
            raise sqlite3.OperationalError("Synthetic write failure")
        return original(who, operation, *args, **kwargs)
    monkeypatch.setattr(store, "mutate", unavailable)
    get(api, "meetings/transcript?id=" + linked, attempt["token"], expected=503)


def test_context_is_bounded_metadata_and_claims_include_it(api):
    t = task(api, owner="bot:ops", private=False)
    with api.app.state.store.transaction() as c:
        for i in range(12):
            mid = f"meeting-{i:02}"
            meetings.snapshot({"id": mid, "kind": "meeting", "private": False, "title": "M" * 400,
                               "started": f"2026-10-{i+1:02}", "owner": "ana@acme.example"},
                              transcript="Never include this transcript", conn=c)
            c.execute("INSERT INTO meeting_task_links VALUES(?,?,?,'linked','human:ana',?)",
                      (H.new_id(), mid, t["id"], H.now()))
    for i in range(12):
        fid = seed_page(api, "ops", title="P" * 400, task=t["id"])
        content = f"<html><body>Context {i}</body></html>".encode()
        digest = api.app.state.blobs.put(content, "text/html")
        with api.app.state.store.transaction() as c:
            asset = blobs.register(c, Identity("bot:ops", "bot"), digest, len(content), "context.html", "text/html")
            c.execute("INSERT INTO bot_file_versions(file_id,version,blob_id,digest,size,name,mime,actor,created_at) "
                      "VALUES(?,1,?,?,?,'context.html','text/html','bot:ops',?)",
                      (fid, asset["id"], digest, len(content), H.now()))
            c.execute("UPDATE bot_files SET current_version=1 WHERE id=?", (fid,))
    result = get(api, "tasks/" + t["id"] + "/context")
    assert len(result["pages"]) == len(result["meetings"]) == 10
    assert [r["id"] for r in result["meetings"]] == [f"meeting-{i:02}" for i in range(11, 1, -1)]
    assert all(len(r["title"]) == 200 for rows in result.values() for r in rows)
    assert "Never include" not in json.dumps(result)
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    attempt = claim(api, machine, "ops")
    assert attempt["task_context"] == result
    # Measure the complete delivery projector on a typical full (10 + 10) context claim, on RunsOn.
    import time
    elapsed = []
    for _ in range(5):
        with api.app.state.store.read() as c:
            who = Identity("bot:ops", "bot", attempt_id=attempt["id"])
            started = time.perf_counter()
            projected = turn_scope.project(c, turn_scope.scope(c, who), {"attempt": attempt}, auth=api.app.state.auth)
            elapsed.append((time.perf_counter() - started) * 1000)
            assert projected == {"attempt": attempt}
    print(f"project full claim: {len(json.dumps(attempt))} bytes, 10 pages + 10 meetings, "
          f"median {sorted(elapsed)[2]:.2f} ms, range {min(elapsed):.2f}-{max(elapsed):.2f} ms")
    with api.app.state.store.transaction() as c:
        restrict(c, "ops", people=["ana"])
    get(api, "tasks/" + t["id"] + "/context", "cara-test", expected=404)


def test_only_a_filed_page_on_the_exact_single_task_accepts_cross_bot_versions(api):
    t, attempt = task_turn(api)
    _, publisher = turn(api, "cpo")
    fid = page(api, publisher, task=t["id"]).json()["page"]["file_id"]
    revised = page(api, attempt, task=t["id"], page=fid, base_version=1, data=HTML + b"<p>Revised</p>")
    assert revised.status_code == 200, revised.text
    current = revised.json()["page"]
    assert (current["file_id"], current["bot_id"], current["updated_by"], current["version"]) == (fid, "cpo", "bot:ops", 2)
    stale = page(api, attempt, task=t["id"], page=fid, base_version=1, data=HTML + b"<p>Lost update</p>")
    assert stale.status_code == 409, stale.text
    assert page(api, attempt, task=t["id"], page=fid, base_version="²").status_code == 422
    pinned = published(api, publisher, name="pinned.html", botwide=True)
    post(api, "pins", {"place": "dashboard", "item": "page:" + pinned})
    post(api, "pins", {"place": "task:" + t["id"], "item": "page:" + pinned})
    assert page(api, attempt, task=t["id"], page=pinned, base_version=1).status_code == 403
    other = task(api, private=False)
    carry(api, attempt, other["id"])
    assert page(api, attempt, task=t["id"], page=fid, base_version=2).status_code in (403, 404)
    with api.app.state.store.read() as c:
        versions = c.execute("SELECT version,actor,attempt_id FROM bot_file_versions WHERE file_id=? ORDER BY version", (fid,)).fetchall()
        assert [tuple(v) for v in versions] == [(1, "bot:cpo", publisher["id"]), (2, "bot:ops", attempt["id"])]


def test_make_page_file_reads_log_file_identity_and_obey_the_ten_item_list(api):
    t = task(api, private=False)
    seeded = {seed_page(api, "cpo", task=t["id"]) for _ in range(12)}
    _, publisher = turn(api, "cpo")
    fid = published(api, publisher, task=t["id"])
    shown = {r["id"] for r in get(api, "tasks/" + t["id"] + "/context")["pages"]}
    outside = next(iter(seeded - shown))
    _, attempt = assistant_turn(api, "ana-test")
    make_scope(api, attempt, t["id"])
    token = attempt["token"]
    get(api, "pages/" + outside, token, expected=404)
    get(api, "files/" + outside + "/versions", token, expected=404)
    for target in (fid, blob(api, fid)):
        response = api.get("/api/v2/files/" + target, headers=headers(token))
        assert response.status_code == 200, response.text
    with api.app.state.store.read() as c:
        assert [tuple(r) for r in c.execute("SELECT kind,ref FROM attempt_reads WHERE attempt_id=?", (attempt["id"],))] == [
            ("file", fid)]


def test_notes_read_pages_through_notes_without_repeating_them_at_transcript_offsets(api):
    mid = record(api, {"private": False, "notes": "First decision. Second decision."})
    first = get(api, f"meetings/transcript?id={mid}&notes=true&limit=16")
    rest = get(api, f"meetings/transcript?id={mid}&notes=true&offset={first['next_offset']}")
    assert first["text"] + rest["text"] == "First decision. Second decision."
    assert "notes" not in rest
    transcript = get(api, f"meetings/transcript?id={mid}&offset=1")
    assert "notes" not in transcript and "decision" not in transcript["text"]


def test_scoped_sql_cannot_name_or_count_a_page_the_file_hook_refuses(api):
    t, attempt = task_turn(api)
    # The same bot owns both pages, so SQL's ordinary bot ownership filter admits both candidates.
    fid = published(api, attempt, task=t["id"], name="project.html")
    hidden = seed_page(api, "ops", title="Private bot-wide title")
    answer = post(api, "sql", {"sql": "SELECT id,title FROM bot_files ORDER BY id"}, attempt["token"])
    assert answer["rows"] == [[fid, "project.html"]]
    counted = post(api, "sql", {"sql": f"SELECT count(*) FROM bot_files WHERE id='{hidden}'"}, attempt["token"])
    assert counted["rows"] == [[0]]


def test_botops_borrowing_a_persons_rights_keeps_its_task_scope(api):
    _, attempt = task_turn(api)
    mid = record(api, {"private": True})
    with api.app.state.store.read() as c:
        machine = c.execute("SELECT runner_id FROM attempts WHERE id=?", (attempt["id"],)).fetchone()[0]
    acted = Identity("human:ana", "owner", email="ana@acme.example", runner_id=machine,
                     attempt_id=attempt["id"], task_actor="bot:ops", via="botops")
    api.app.state.store.settings.test_identities["acted"] = acted
    get(api, "meetings/transcript?id=" + mid)
    get(api, "meetings/transcript?id=" + mid, "acted", expected=404)


def test_context_orders_meetings_by_utc_and_pages_by_version_time(api):
    t = task(api, private=False)
    for mid, started in (("earlier", "2026-11-05T01:00:00+02:00"), ("later", "2026-11-05T00:00:00Z")):
        with api.app.state.store.transaction() as c:
            meetings.snapshot({"id": mid, "kind": "meeting", "private": False, "title": mid,
                               "started": started, "owner": "ana@acme.example"}, conn=c)
            c.execute("INSERT INTO meeting_task_links VALUES(?,?,?,'linked','human:ana',?)",
                      (H.new_id(), mid, t["id"], H.now()))
    _, publisher = turn(api, "cpo")
    fid = published(api, publisher, task=t["id"])
    with api.app.state.store.transaction() as c:
        stamp = c.execute("SELECT created_at FROM bot_file_versions WHERE file_id=?", (fid,)).fetchone()[0]
        c.execute("UPDATE bot_files SET last_activity_at='2099-01-01' WHERE id=?", (fid,))
    result = get(api, "tasks/" + t["id"] + "/context")
    assert [r["id"] for r in result["meetings"]] == ["later", "earlier"]
    assert result["pages"][0]["updated_at"] == stamp


def test_scoped_visuals_filter_live_files_and_tasks_and_recheck_current_scope(api, monkeypatch):
    monkeypatch.setattr(api.app.state.store.settings, "visual_origin", "http://visuals.localhost:9001")
    t, attempt = task_turn(api)
    other = task(api, owner="bot:cpo", title="Outside task title", private=False)
    _, publisher = turn(api, "cpo")
    secret = published(api, publisher, task=other["id"], title="Outside page title")
    live = (f'<html><body><tico-files task="{other["id"]}"></tico-files>'
            f'<tico-task id="{other["id"]}"></tico-task><tico-tasks waiting-on="viewer"></tico-tasks>'
            f'<tico-files task="{t["id"]}"></tico-files></body></html>').encode()
    fid = published(api, attempt, task=t["id"], data=live, title="Allowed page title")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET status='waiting',waiting_on='bot:ops' WHERE id=?", (other["id"],))
    src = get(api, "pages/" + fid, attempt["token"])["page"]["src"]
    assert visuals.read(api.app.state.visual_key, src.rsplit("/", 1)[1])[-1] == attempt["id"]
    shown = api.get(src)
    assert shown.status_code == 200, shown.text
    assert "Allowed page title" in shown.text
    assert all(value not in shown.text for value in (secret, "Outside page title", other["id"], "Outside task title"))
    # A fresh request rebuilds containment; carrying a second task now excludes this bot-party page.
    carry(api, attempt, other["id"])
    assert api.get(src).status_code == 404


def test_make_page_visuals_log_secondary_reads_and_fail_closed_when_logging_fails(api, monkeypatch):
    monkeypatch.setattr(api.app.state.store.settings, "visual_origin", "http://visuals.localhost:9001")
    t = task(api, private=False)
    _, publisher = turn(api, "cpo")
    source = published(api, publisher, task=t["id"], title="Source page")
    viewer = published(api, publisher, task=t["id"], name="viewer.html",
                       data=f'<html><tico-files task="{t["id"]}"></tico-files></html>'.encode())
    _, attempt = assistant_turn(api, "ana-test")
    make_scope(api, attempt, t["id"])
    src = get(api, "pages/" + viewer, attempt["token"])["page"]["src"]
    assert visuals.read(api.app.state.visual_key, src.rsplit("/", 1)[1])[-1] == attempt["id"]
    store = api.app.state.store
    with store.transaction() as c:
        c.execute("DELETE FROM attempt_reads WHERE attempt_id=?", (attempt["id"],))
    shown = api.get(src)
    assert shown.status_code == 200 and "Source page" in shown.text, shown.text
    with store.read() as c:
        assert {tuple(r) for r in c.execute("SELECT kind,ref FROM attempt_reads WHERE attempt_id=?", (attempt["id"],))} == {
            ("file", viewer), ("file", source)}
    original = store.mutate

    def unavailable(who, operation, *args, **kwargs):
        if operation == "attempt.reads":
            raise sqlite3.OperationalError("Synthetic write failure")
        return original(who, operation, *args, **kwargs)

    monkeypatch.setattr(store, "mutate", unavailable)
    assert api.get(src).status_code == 503
    missing = visuals.mint(api.app.state.visual_key, "human:ana", viewer, 1, attempt_id="missing-attempt")
    assert api.get("http://visuals.localhost:9001/visuals/" + missing).status_code == 404


def test_task_attachment_projections_omit_rejected_files_blobs_and_covers(api):
    t, attempt = task_turn(api)
    other = task(api, owner="bot:cpo", private=False)
    image = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    attached = post(api, f'tasks/{other["id"]}/files', {
        "name": "hidden-cover.png", "content_base64": base64.b64encode(image).decode()})
    fid = attached["file_id"]
    digest = api.app.state.blobs.put(image + b"legacy", "image/png")
    with api.app.state.store.transaction() as c:
        legacy = blobs.register(c, Identity("human:ana", "owner"), digest, len(image) + 6, "hidden-legacy.png", "image/png")
        c.execute("INSERT INTO task_assets(task_id,blob_id) VALUES(?,?)", (other["id"], legacy["id"]))
        comment = H.task_comment(c, "human:ana", other["id"], "Review the attachments", wake=False,
                                 extra_refs={"files": [fid + "@1", legacy["id"] + "@1"],
                                             "attachments": [legacy, {"id": fid, "file_id": fid, "version": 1,
                                                                     "kind": "image", "title": "hidden-cover.png"}]})
        c.execute("INSERT INTO message_assets(message_id,blob_id) VALUES(?,?)", (comment["id"], legacy["id"]))
    # Ordinary readers retain their attachments and newest cover.
    ordinary = get(api, "tasks/" + other["id"])["task"]
    assert ordinary["attachments"] and ordinary["cover"]
    token = attempt["token"]
    get(api, "files/" + fid + "/meta", token, expected=404)
    get(api, "files/" + legacy["id"], token, expected=404)
    single = get(api, "tasks/" + other["id"], token)["task"]
    listed = next(row for row in get(api, "tasks", token)["tasks"] if row["id"] == other["id"])
    for projected in (single, listed):
        assert projected["attachments"] == [] and projected["cover"] is None
        assert "hidden-" not in json.dumps(projected)
    assert get(api, f'tasks/{other["id"]}/files', token)["files"] == []
    comments = get(api, f'tasks/{other["id"]}/comments', token)["comments"]
    projected_refs = next(row for row in comments if row["id"] == comment["id"])["refs"]
    assert projected_refs["attachments"] == projected_refs["files"] == []
    for query in ("SELECT task_id,blob_id FROM task_assets", "SELECT count(*) FROM task_assets",
                  "SELECT message_id,blob_id FROM message_assets", "SELECT count(*) FROM message_assets"):
        rows = post(api, "sql", {"sql": query}, token)["rows"]
        assert rows == ([[0]] if "count" in query else [])
    refs = post(api, "sql", {"sql": "SELECT refs FROM messages WHERE id=?", "params": [comment["id"]]}, token)["rows"]
    projected_refs = json.loads(refs[0][0])
    assert projected_refs["attachments"] == projected_refs["files"] == []
    # A legacy upload on the carried task stays visible, including its cover and SQL row.
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM message_assets WHERE blob_id=?", (legacy["id"],))
        c.execute("DELETE FROM task_assets WHERE blob_id=?", (legacy["id"],))
        c.execute("INSERT INTO task_assets(task_id,blob_id) VALUES(?,?)", (t["id"], legacy["id"]))
    own = get(api, "tasks/" + t["id"], token)["task"]
    assert [r["id"] for r in own["attachments"]] == [legacy["id"]]
    assert legacy["id"] in own["cover"]["url"]
    assert [r["id"] for r in get(api, f'tasks/{t["id"]}/files', token)["files"]] == [legacy["id"]]
    assert post(api, "sql", {"sql": "SELECT task_id,blob_id FROM task_assets"}, token)["rows"] == [[t["id"], legacy["id"]]]
    comments = get(api, f'tasks/{other["id"]}/comments', token)["comments"]
    assert next(row for row in comments if row["id"] == comment["id"])["refs"]["files"] == [legacy["id"] + "@1"]
    refs = post(api, "sql", {"sql": "SELECT refs FROM messages WHERE id=?", "params": [comment["id"]]}, token)["rows"]
    assert json.loads(refs[0][0])["files"] == [legacy["id"] + "@1"]


def test_claim_replay_rebuilds_context_after_meeting_and_page_access_narrow(api):
    t = task(api, owner="bot:ops", private=False)
    mid = record(api, {"private": False, "title": "Previously shared meeting"})
    link(api, mid, [t["id"]])
    _, publisher = turn(api, "cpo")
    fid = published(api, publisher, task=t["id"], title="Previously shared page")
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    attempt = claim(api, machine, "ops", key="context-claim")
    assert [r["id"] for r in attempt["task_context"]["pages"]] == [fid]
    assert [r["id"] for r in attempt["task_context"]["meetings"]] == [mid]
    private = task(api, owner="human:ben", requester="human:ben", private=True)
    with api.app.state.store.transaction() as c:
        meeting = meetings.get(mid, c)
        meetings.snapshot({**meeting["metadata"], "private": True, "audience": "participants"}, conn=c)
        c.execute("INSERT INTO task_assets(task_id,blob_id) VALUES(?,?)", (private["id"], blob(api, fid)))
    replay = claim(api, machine, "ops", key="context-claim")
    assert replay["id"] == attempt["id"] and replay["token"] == attempt["token"]
    assert replay["task_context"] == {"pages": [], "meetings": []}
    assert "Previously shared" not in json.dumps(replay["task_context"])
