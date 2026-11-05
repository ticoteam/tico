"""Task attempts share only contained sources; direct URLs never bypass that boundary."""
import json
import sqlite3

import pytest

from backend import meetings, turn_scope
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
        seed_page(api, "ops", title="P" * 400, task=t["id"])
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
