"""Personal archive, logical-day supersession, and mixed-version schema coverage."""

import sqlite3
from datetime import date, timedelta

from backend import openapi_v2, updates
from backend.store import H
from backend.tests.test_api import api, as_member, get, headers, post, restrict, setup_attempt  # noqa: F401


SLIDES = {"goal": "Grow signups to 400 a month; at 310 and on pace.", "kpis": [],
          "done": ["Shipped the signup page"], "focus": ["Check conversion"], "blockers": []}


def day_offset(offset):
    return (date.fromisoformat(updates.today()) + timedelta(days=offset)).isoformat()


def allow_redo(api, bot, kind, day):
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO update_queue(id,bot,kind,day,rank,state,redo) VALUES(?,?,?,?,0,'queued',1)",
                  (H.new_id(), bot, kind, day))


def publish(api, bot_token, kind, day, key, *, body="- Finished the work", slides=None):
    if day != updates.today():
        allow_redo(api, "ops", kind, day)
    payload = {"kind": kind, "day": day, "body": body}
    if slides is not None:
        payload["slides"] = slides
    return api.post("/api/v2/updates", json=payload, headers=headers(bot_token, key))


def ids(result):
    return [item["id"] for item in result["updates"]]


def test_logical_days_restore_and_archive_are_personal_and_kind_scoped(api):
    _, _, attempt = setup_attempt(api, "ops")
    old_day, current_day, future_day, backdated_day = (day_offset(-1), day_offset(0), day_offset(1), day_offset(-2))
    old = publish(api, attempt["token"], "daily", old_day, "old-daily").json()["update"]
    old_week = publish(api, attempt["token"], "weekly", old_day, "old-week", slides=SLIDES).json()["update"]
    current_response = publish(api, attempt["token"], "daily", current_day, "current-daily")
    assert current_response.status_code == 200, current_response.text
    current = current_response.json()["update"]
    assert ids(get(api, f"updates?bot=ops&kind=daily")) == [current["id"]]
    archive = get(api, "updates?bot=ops&kind=daily&archive=true")
    assert ids(archive) == [old["id"]] and archive["updates"][0]["archived"] is True
    assert archive["unread"] == get(api, "updates?bot=ops&kind=daily")["unread"], \
        "archive rows do not inflate the inbox count for the active filter"
    assert ids(get(api, "updates?bot=ops&kind=weekly")) == [old_week["id"]], "daily posts do not supersede weekly posts"
    history = get(api, "updates?bot=ops&kind=daily&include_archive=true")
    assert {u["id"] for u in history["updates"]} == {old["id"], current["id"]}
    assert next(u for u in history["updates"] if u["id"] == old["id"])["archived"] is True

    # The archive is personal. Ben still sees the latest update and the automatic archive state
    # for the old one, regardless of Ana's later choices.
    ben_inbox_before = get(api, "updates?bot=ops&kind=daily", token="ben-test")
    assert ids(ben_inbox_before) == [current["id"]]
    assert ids(get(api, "updates?bot=ops&kind=daily&archive=true", token="ben-test")) == [old["id"]]

    unread_before = get(api, "updates/unread")["unread"]
    post(api, "updates/archive", {"ids": [current["id"]], "archived": True}, key="archive-current")
    assert ids(get(api, "updates?bot=ops&kind=daily")) == []
    assert get(api, "updates/unread")["unread"] == unread_before - 1
    assert ids(get(api, "updates?bot=ops&kind=daily", token="ben-test")) == [current["id"]]
    assert set(ids(get(api, "updates?bot=ops&kind=daily&archive=true"))) == {old["id"], current["id"]}

    # Restore is a per-reader exception to automatic supersession. A same-day retry/replacement
    # must not undo it or repeat side effects.
    post(api, "updates/archive", {"ids": [old["id"]], "archived": False}, key="restore-old")
    assert ids(get(api, "updates?bot=ops&kind=daily")) == [old["id"]]
    assert ids(get(api, "updates?bot=ops&kind=daily", token="ben-test")) == [current["id"]]
    replacement = publish(api, attempt["token"], "daily", current_day, "replace-current",
                          body="- Finished the work with a correction")
    assert replacement.status_code == 200 and replacement.json()["update"]["id"] == current["id"]
    retry = publish(api, attempt["token"], "daily", current_day, "replace-current",
                    body="- Finished the work with a correction")
    assert retry.status_code == 200 and retry.json()["update"]["id"] == current["id"]
    assert ids(get(api, "updates?bot=ops&kind=daily")) == [old["id"]], "same-ID retry did not rearchive restore"

    # A later logical day expires the restore exception. A newly posted backdated redo may hide
    # still-older updates but cannot displace that newer day.
    future = publish(api, attempt["token"], "daily", future_day, "future-daily")
    assert future.status_code == 200, future.text
    future_id = future.json()["update"]["id"]
    assert ids(get(api, "updates?bot=ops&kind=daily")) == [future_id]
    # A backdated post is not a new latest day and does not undo a reader's restore decision.
    restored_backdate = publish(api, attempt["token"], "daily", day_offset(-3), "restore-backdated")
    assert restored_backdate.status_code == 200, restored_backdate.text
    restored_backdate_id = restored_backdate.json()["update"]["id"]
    post(api, "updates/archive", {"ids": [restored_backdate_id], "archived": False}, key="restore-backdated")
    backdated = publish(api, attempt["token"], "daily", backdated_day, "backdated-daily")
    assert backdated.status_code == 200, backdated.text
    assert set(ids(get(api, "updates?bot=ops&kind=daily"))) == {future_id, restored_backdate_id}, \
        "a backdated redo cannot hide a newer day or cancel an unrelated restore"
    archive_ids = set(ids(get(api, "updates?bot=ops&kind=daily&archive=true")))
    assert {old["id"], current["id"], backdated.json()["update"]["id"]} <= archive_ids

    # Other bots keep independent daily histories.
    with api.app.state.store.transaction() as c:
        other_bot = updates.post(c, "cpo", "- CPO's daily update", kind="daily", day=current_day)
    assert ids(get(api, "updates?bot=cpo&kind=daily")) == [other_bot["id"]]

    # A failed publication has no supersession side effects: the already restored older item stays
    # visible even though the test preauthorizes a later logical day.
    restore_day = day_offset(-4)
    restore_old = publish(api, attempt["token"], "daily", restore_day, "restore-day").json()["update"]
    post(api, "updates/archive", {"ids": [restore_old["id"]], "archived": False}, key="restore-before-fail")
    failed_day = day_offset(2)
    allow_redo(api, "ops", "daily", failed_day)
    failed = api.post("/api/v2/updates", json={"kind": "daily", "day": failed_day, "body": ""},
                      headers=headers(attempt["token"], "invalid-future"))
    assert failed.status_code == 422
    assert restore_old["id"] in ids(get(api, "updates?bot=ops&kind=daily"))


def test_archive_batches_are_human_only_permission_checked_and_atomic(api):
    as_member(api, "ben@acme.example")
    _, _, attempt = setup_attempt(api, "ops")
    ops_update = publish(api, attempt["token"], "daily", updates.today(), "ops-visible").json()["update"]
    with api.app.state.store.transaction() as c:
        private_update = updates.post(c, "inbox", "- Ana's private update", kind="daily", day=updates.today())
        restrict(c, "inbox", people=["ana"])

    # Ben can read ops but cannot see Ana's inbox-manager update. A mixed request must reject
    # before archiving the valid first ID or creating an audit event.
    rejected = api.post("/api/v2/updates/archive", json={"ids": [ops_update["id"], private_update["id"]],
                                                        "archived": True},
                        headers=headers("ben-test", "mixed-archive"))
    assert rejected.status_code == 404
    with api.app.state.store.read() as c:
        assert c.execute("SELECT 1 FROM update_archive_overrides WHERE update_id=? AND actor='human:ben'",
                         (ops_update["id"],)).fetchone() is None
        assert c.execute("SELECT 1 FROM events WHERE target=? AND action='update.archive'",
                         (ops_update["id"],)).fetchone() is None

    rejected_read = api.post("/api/v2/updates/read", json={"ids": [ops_update["id"], private_update["id"]]},
                             headers=headers("ben-test", "mixed-read"))
    assert rejected_read.status_code == 404
    with api.app.state.store.read() as c:
        assert c.execute("SELECT 1 FROM update_reads WHERE update_id=? AND actor='human:ben'",
                         (ops_update["id"],)).fetchone() is None

    bot_attempt = api.post("/api/v2/updates/archive", json={"ids": [ops_update["id"]], "archived": True},
                           headers=headers(attempt["token"], "bot-archive"))
    assert bot_attempt.status_code == 403
    with api.app.state.store.read() as c:
        assert c.execute("SELECT 1 FROM update_archive_overrides WHERE update_id=?",
                         (ops_update["id"],)).fetchone() is None

    # Ana can archive her private update; it remains absent from Ben's inbox and archive.
    post(api, "updates/archive", {"ids": [private_update["id"]], "archived": True}, key="ana-private-archive")
    assert private_update["id"] in ids(get(api, "updates?archive=true"))
    assert private_update["id"] not in ids(get(api, "updates?archive=true", token="ben-test"))
    assert private_update["id"] not in ids(get(api, "updates", token="ben-test"))


def test_archive_schema_is_created_for_fresh_and_existing_databases():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    # The prior schema has update content/read state but no slides or archive tables.
    c.executescript("""
      CREATE TABLE updates(id TEXT PRIMARY KEY, bot TEXT NOT NULL, kind TEXT NOT NULL, day TEXT NOT NULL,
        headline TEXT NOT NULL, body TEXT NOT NULL DEFAULT '', created TEXT NOT NULL, updated TEXT NOT NULL,
        UNIQUE(bot,kind,day));
      CREATE TABLE update_reads(update_id TEXT NOT NULL, actor TEXT NOT NULL, read_at TEXT NOT NULL,
        PRIMARY KEY(update_id,actor));
      CREATE TABLE update_queue(id TEXT PRIMARY KEY, bot TEXT NOT NULL, kind TEXT NOT NULL, day TEXT NOT NULL,
        rank INTEGER NOT NULL, state TEXT NOT NULL, message_id TEXT, sent_at TEXT, done_at TEXT, reason TEXT,
        UNIQUE(bot,kind,day));
      CREATE TABLE update_settings(bot TEXT PRIMARY KEY, daily INTEGER NOT NULL DEFAULT 1,
        weekly INTEGER NOT NULL DEFAULT 1);
      INSERT INTO updates VALUES('existing','ops','daily','2026-10-06','Still here','- Old content',
        '2026-10-06T12:00:00Z','2026-10-06T12:00:00Z');
    """)
    c.executescript(updates.SCHEMA)
    updates.ensure_schema(c)
    assert c.execute("SELECT id,body FROM updates").fetchone()[:] == ("existing", "- Old content")
    assert {r["name"] for r in c.execute("PRAGMA table_info(updates)")} >= {"slides_json"}
    assert {r["name"] for r in c.execute("PRAGMA table_info(update_queue)")} >= {"tries", "redo"}
    assert c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='update_archive_overrides'").fetchone()
    c.close()


def test_archive_and_explicit_history_filters_are_in_the_stable_contract(api):
    document = openapi_v2.spec(api.app)
    listing = document["paths"]["/api/v2/updates"]["get"]
    params = {p["name"]: p for p in listing["parameters"]}
    assert params["archive"]["schema"]["type"] == "boolean"
    assert params["include_archive"]["schema"]["type"] == "boolean"
    operation = document["paths"]["/api/v2/updates/archive"]["post"]
    assert operation["operationId"] == "archiveUpdates"
    result_schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert result_schema["$ref"].endswith("/UpdateArchiveResult")
    with api.app.state.store.transaction() as c:
        item = updates.post(c, "ops", "- Retain this history", kind="daily")
    response = api.post("/api/v2/updates/archive", json={"ids": [item["id"]], "archived": True}, headers=headers())
    assert response.status_code == 200, response.text
    assert set(response.json()) == {"updated", "archived", "unread"}
