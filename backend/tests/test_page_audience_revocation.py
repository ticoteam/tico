"""Revocation covers copied context, signed delegates, raw transports and roster reuse."""
import json

import pytest

from backend import counsel, events, files, sql as S, threads, visuals
from backend.auth import Identity
from backend.store import H
from backend.tests.slug_ids import bot_ids_are_their_slugs  # noqa: F401
from backend.tests.test_api import api, get, headers, post  # noqa: F401
from backend.tests.test_counsel import counsel_on, counsel_run, source, store_file
from backend.tests.test_groups import send
from backend.tests.test_org_rules import as_bot, org
from backend.tests.test_files import turn
from backend.tests.test_pages import page
from backend.tests.test_page_audience import edit, promoted, read


@pytest.mark.parametrize("running", [False, True], ids=["queued", "running"])
@pytest.mark.parametrize("removed", ["asker", "counsel"])
def test_counsel_copy_revoked_after_ask(api, running, removed):
    org(api)
    counsel_on(api)
    fid, _ = promoted(api)
    edit(api, fid, {"people": ["cara"], "bots": [H.COUNSEL]})
    task = post(api, "rules/read", {"file_id": fid, "source_id": source(api)}, token="cara-test")["task_id"]
    before = get(api, "tasks/" + task, token="cara-test")
    assert "42 deals" in before["task"]["body"]
    attempt = counsel_run(api)[1] if running else None
    edit(api, fid, {"people": [] if removed == "asker" else ["cara"],
                    "bots": [] if removed == "counsel" else [H.COUNSEL]})
    with api.app.state.store.read() as c:
        assert c.execute("SELECT body FROM tasks WHERE id=?", (task,)).fetchone()[0] == counsel.REMOVED_PAGE
    if attempt is None:
        attempt = counsel_run(api)[1]
    response = api.get("/api/v2/tasks/" + task, headers=headers(attempt["token"]))
    assert response.status_code == 200, response.text
    assert counsel.REMOVED_PAGE in response.text and "42 deals" not in response.text
    assert api.get("/api/v2/files/" + fid, headers=headers(attempt["token"])).status_code in (403, 404)


def test_counsel_copy_is_rechecked_when_group_membership_changes(api):
    org(api)
    counsel_on(api)
    legal = send(api, "POST", "groups", {"name": "Legal", "add": {"people": ["cara"]}})["id"]
    fid, _ = promoted(api)
    edit(api, fid, {"teams": [legal], "bots": [H.COUNSEL]})
    task = post(api, "rules/read", {"file_id": fid, "source_id": source(api)}, token="cara-test")["task_id"]
    # Simulate an independent roster writer: the read gate must not rely on set_audience scrubbing alone.
    with api.app.state.store.transaction() as c:
        from backend import views
        roster = views.roster(c)
        roster["people"] = [dict(p, team="") if p["id"] == "cara" else p for p in roster["people"]]
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (json.dumps(roster),))
    with api.app.state.store.read() as c:
        assert not H.task_private_readable(c, "human:cara", H.task(c, task))
    assert api.get("/api/v2/tasks/" + task, headers=headers("cara-test")).status_code == 404
    assert post(api, "sql", {"sql": f"SELECT body FROM tasks WHERE id='{task}'"}, token="cara-test")["rows"] == []
    send(api, "PATCH", "groups/" + legal, {"add": {"people": ["cara"]}})
    send(api, "PATCH", "groups/" + legal, {"remove": {"people": ["cara"]}})
    with api.app.state.store.read() as c:
        assert c.execute("SELECT body FROM tasks WHERE id=?", (task,)).fetchone()[0] == counsel.REMOVED_PAGE


def test_native_copy_gate_preserves_autocommit_and_later_writes(api):
    org(api)
    counsel_on(api)
    fid, _ = promoted(api)
    edit(api, fid, {"people": ["cara"], "bots": [H.COUNSEL]})
    tid = post(api, "rules/read", {"file_id": fid, "source_id": source(api)}, token="cara-test")["task_id"]
    c = H.connect(api.app.state.store.settings.db_path)
    try:
        for actor, allowed in (("human:cara", True), ("bot:finance", False)):
            assert not c.in_transaction
            assert H.task_private_readable(c, actor, H.task(c, tid)) is allowed
            assert not c.in_transaction
        c.execute("UPDATE tasks SET note='Native write survives close' WHERE id=?", (tid,))
    finally:
        c.close()
    with api.app.state.store.read() as c:
        assert c.execute("SELECT note FROM tasks WHERE id=?", (tid,)).fetchone()[0] == 'Native write survives close'


def test_visual_token_keeps_both_principals_and_rejects_legacy_tokens(api):
    fid, _ = promoted(api)
    api.app.state.store.settings.visual_origin = "https://visuals.acme.example"
    delegated = Identity("human:cara", "human", "cara@acme.example", task_actor="bot:finance")
    edit(api, fid, {"people": ["cara"], "bots": ["finance"]})
    with api.app.state.store.read() as c:
        src = visuals.src_for(api.app, api.app.state.store.settings, c, delegated, fid, 1)
    assert src and api.get(src).status_code == 200
    token = src.rsplit("/", 1)[1]
    assert visuals.read(api.app.state.visual_key, token, principals=True)[-1] == "bot:finance"
    edit(api, fid, {"people": ["cara"]})
    with api.app.state.store.read() as c:
        row = c.execute("SELECT * FROM bot_files WHERE id=?", (fid,)).fetchone()
        assert not files.file_visible(c, api.app.state.auth, delegated, row, {})
    assert api.get(src).status_code == 404
    edit(api, fid, {"people": ["cara"], "bots": ["finance"]})
    assert api.get(src).status_code == 200
    edit(api, fid, {"bots": ["finance"]})
    assert api.get(src).status_code == 404
    edit(api, fid, {"people": ["cara"]})
    # Five-field URLs cannot prove whether a bot was acting for this person.
    import hashlib
    import hmac
    import time
    key = api.app.state.visual_key
    body = visuals._b64(json.dumps(["human:cara", fid, 1, "", int(time.time()) + 60]).encode())
    legacy = body + "." + visuals._b64(hmac.new(key, body.encode(), hashlib.sha256).digest())
    assert visuals.read(key, legacy, principals=True) is None
    assert api.get("https://visuals.acme.example/visuals/" + legacy).status_code == 404
    fresh = read(api, fid, "cara-test").json()["page"]["src"]
    assert fresh and api.get(fresh).status_code == 200
    assert len(json.loads(visuals._unb64(fresh.rsplit("/", 1)[-1].split(".")[0]))) == 6


def test_deleted_group_cannot_regain_grants_when_legal_is_recreated(api):
    legal = send(api, "POST", "groups", {"name": "Legal", "add": {"people": ["cara"]}})["id"]
    fid, _ = promoted(api)
    edit(api, fid, {"teams": [legal]})
    assert read(api, fid, "cara-test").status_code == 200
    send(api, "DELETE", "groups/" + legal)
    audience = read(api, fid, "ben-test").json()["page"]["audience"]
    assert audience["teams"] == [] and not audience["everyone"]
    recreated = send(api, "POST", "groups", {"name": "Legal", "add": {"people": ["cara"]}})
    assert recreated["id"] == legal
    assert read(api, fid, "cara-test").status_code == 404
    assert read(api, fid, "ben-test").status_code == 200


def test_removed_person_and_archived_bot_lose_explicit_page_grants(api):
    fid, _ = promoted(api)
    pid = post(api, "access/people", {"name": "New member", "email": "new-member@acme.example"})["person"]
    edit(api, fid, {"people": [pid], "bots": ["finance"]})
    post(api, "people/" + pid, {"left": True})
    removed = api.delete("/api/v2/people/" + pid, headers=headers("ana-test"))
    assert removed.status_code == 200, removed.text
    assert post(api, "access/people", {"name": "Replacement", "email": "new-member@acme.example"})["person"] == pid
    with api.app.state.store.transaction() as c:
        from backend.settings_admin import archive_bot
        archive_bot(c, "human:ana", "finance")
        audience = json.loads(c.execute("SELECT audience FROM pages WHERE file_id=?", (fid,)).fetchone()[0])
        assert audience["people"] == audience["bots"] == [] and not audience["everyone"]


def test_review_refs_are_denied_on_sql_timeline_and_undecorated_events(api):
    task = post(api, "tasks", {"owner": "ops", "title": "Read the attached plan", "body": "Review", "private": False})
    tid = task["id"]
    _, attempt = turn(api)
    fid = page(api, attempt, task=tid, title="Audience secret").json()["page"]["file_id"]
    comment = post(api, f"tasks/{tid}/comments", {"text": "Review this", "attachments": [f"{fid}@1"]}, token="ben-test")["comment"]
    mid, cid = comment["id"], comment["conversation_id"]
    # A page ref outside attachments must use the same gate as the attachment object.
    with api.app.state.store.transaction() as c:
        refs = json.loads(c.execute("SELECT refs FROM messages WHERE id=?", (mid,)).fetchone()[0])
        refs["review"] = {"files": [{"file_id": fid, "title": "Audience secret", "name": "weekly.html", "size": 123}]}
        c.execute("UPDATE messages SET refs=? WHERE id=?", (json.dumps(refs), mid))
    edit(api, fid, {"people": ["ben"]})
    with api.app.state.store.transaction() as c:
        before = events.latest(c)
        c.execute("UPDATE messages SET body='Review this now',revision=revision+1 WHERE id=?", (mid,))
    as_bot(api, "finance", "finance-test")
    for who, token in ((Identity("human:cara", "human", "cara@acme.example"), "cara-test"),
                       (Identity("bot:finance", "bot", agent="test"), "finance-test")):
        with api.app.state.store.read() as c:
            timeline = threads.timeline(c, threads.Viewer(api.app.state.auth, who), cid)
        with api.app.state.store.read() as c:
            delivered, _, reset, _ = events.read(c, api.app.state.auth, who, before, topics=("messages",), visuals=None)
        assert not reset
        raw = post(api, "sql", {"sql": f"SELECT refs FROM messages WHERE id='{mid}'"}, token=token)["rows"]
        assert raw == []
        assert api.get("/api/v2/messages/" + mid, headers=headers(token)).status_code == 404
        for value in (timeline, delivered):
            text = json.dumps(value)
            for secret in (fid, "Audience secret", "weekly.html", '"size"'):
                assert secret not in text, (secret, text)
    assert fid in json.dumps(get(api, "messages/" + mid, token="ben-test"))


def test_events_recheck_page_references_in_detail(api):
    fid, _ = promoted(api)
    tid = post(api, "tasks", {"owner": "ops", "title": "Review the event", "body": "Review", "private": False})["id"]
    edit(api, fid, {"people": ["ben"]})
    who = Identity("human:cara", "human", "cara@acme.example")
    with api.app.state.store.transaction() as c:
        H.event(c, "human:ben", "message.visual", tid, {"file": fid, "version": 1, "kind": "page"})
        for detail in ({"file": fid}, {"files": [{"file_id": fid}]}, {"page_id": fid}, {"item": "page:" + fid}):
            assert not files.page_event_readable(c, api.app.state.auth, who, tid, detail)
        event = dict(c.execute("SELECT * FROM events WHERE action='message.visual' AND target=? ORDER BY rowid DESC", (tid,)).fetchone())
        from backend import task_privacy
        assert not task_privacy.event_readable(c, who.actor, event)
    assert post(api, "sql", {"sql": f"SELECT detail FROM events WHERE target='{tid}' AND action='message.visual'"}, token="cara-test")["rows"] == []


def test_sql_ordinary_files_do_not_add_per_file_audience_queries(api):
    store, auth = api.app.state.store, api.app.state.auth
    counts = {}
    for start, size in ((0, 3), (3, 30)):
        for n in range(start, size):
            store_file(api, "Acme report", title=f"Report {n}")
        who = Identity("human:ben", "human", "ben@acme.example")
        # Warm shared roster/schema inputs equally; the measured connection still has its own fresh snapshot.
        with store.read() as c:
            warm = S.connect(store.settings.db_path, c, auth, who)
        try:
            S.run(warm, "SELECT id FROM bot_files", [], 100, 5)
        finally:
            warm.close()
        statements = []
        with store.read() as c:
            conn = S.connect(store.settings.db_path, c, auth, who, trace=statements.append)
        try:
            rows = S.run(conn, "SELECT id FROM bot_files", [], 100, 5)["rows"]
            assert len(rows) == size
            counts[size] = len(statements)
            assert not any("SELECT * FROM main.\"bot_files\" WHERE id=" in q for q in statements)
        finally:
            conn.close()
    print(f"SQL ordinary files 3 -> 30: {counts[3]} -> {counts[30]} statements")
    assert counts[30] == counts[3], counts
