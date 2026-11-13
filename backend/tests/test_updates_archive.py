"""Personal archive, logical-day supersession, and mixed-version schema coverage."""

import sqlite3
from datetime import date, timedelta

from backend import openapi_v2, updates
from backend.store import H
from backend.tests.helpers.api import as_member, get, headers, post, restrict, setup_attempt  # noqa: F401


SLIDES = {
    "goal": "Grow signups to 400 a month; at 310 and on pace.",
    "kpis": [],
    "done": ["Shipped the signup page"],
    "focus": ["Check conversion"],
    "blockers": [],
}


def day_offset(offset):
    return (date.fromisoformat(updates.today()) + timedelta(days=offset)).isoformat()


def bot_id(c, slug):
    """A bot's id by its slug: updates are written under the bot's id."""
    return H.resolve_bot(c, slug)["id"]


def allow_redo(api, bot, kind, day):
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO update_queue(id,bot,kind,day,rank,state,redo) VALUES(?,?,?,?,0,'queued',1)",
            (H.new_id(), bot_id(c, bot), kind, day),
        )


def publish(api, bot_token, kind, day, key, *, body="- Finished the work", slides=None):
    if day != updates.today():
        allow_redo(api, "ops", kind, day)
    payload = {"kind": kind, "day": day, "body": body}
    if slides is not None:
        payload["slides"] = slides
    return api.post("/api/v2/updates", json=payload, headers=headers(bot_token, key))


def ids(result):
    return [item["id"] for item in result["updates"]]


def test_the_tree_leaves_out_what_this_reader_archived_and_keeps_the_reports_below(api, monkeypatch):
    from backend.tests.helpers.bot_contact import bot

    monkeypatch.setattr(updates, "today", lambda at=None: "2026-09-29")
    bot(api, "boss", "Manager", reports_to="human:ana")
    bot(api, "analyst", "Analyst", reports_to="boss")
    with api.app.state.store.transaction() as c:
        boss_id = bot_id(c, "boss")
        mine = updates.post(c, boss_id, "- Planned the launch", day="2026-09-29")
        updates.post(c, bot_id(c, "analyst"), "- Sized the market", day="2026-09-29")
    find = lambda nodes, key: (
        next((n for n in nodes if n["key"] == key), None)
        or next((hit for n in nodes if (hit := find(n["children"], key))), None)
    )
    boss = lambda token="ana-test": find(get(api, "updates/tree?scope=all", token=token)["roots"], "b:" + boss_id)

    post(api, "updates/archive", {"ids": [mine["id"]], "archived": True}, key="tree-archive")
    archived = boss()
    assert archived["update"] is None, "an archived card stays out of the tree on the next load"
    assert [k["update"]["body"] for k in archived["children"]] == ["- Sized the market"], (
        "the manager stays in the tree with its reports below it"
    )
    assert boss("ben-test")["update"]["id"] == mine["id"], "the archive is this reader's only"
    assert mine["id"] not in ids(get(api, "updates")) and mine["id"] in ids(get(api, "updates?archive=true"))

    post(api, "updates/archive", {"ids": [mine["id"]], "archived": False}, key="tree-restore")
    assert boss()["update"]["id"] == mine["id"], "restore brings it back into the tree"


def test_archive_batches_are_human_only_permission_checked_and_atomic(api):
    as_member(api, "ben@acme.example")
    _, _, attempt = setup_attempt(api, "ops")
    ops_update = publish(api, attempt["token"], "daily", updates.today(), "ops-visible").json()["update"]
    with api.app.state.store.transaction() as c:
        private_update = updates.post(
            c, bot_id(c, "inbox"), "- Ana's private update", kind="daily", day=updates.today()
        )
        restrict(c, "inbox", people=["ana"])

    # Ben can read ops but cannot see Ana's inbox-manager update. A mixed request must reject
    # before archiving the valid first ID or creating an audit event.
    rejected = api.post(
        "/api/v2/updates/archive",
        json={"ids": [ops_update["id"], private_update["id"]], "archived": True},
        headers=headers("ben-test", "mixed-archive"),
    )
    assert rejected.status_code == 404
    with api.app.state.store.read() as c:
        assert (
            c.execute(
                "SELECT 1 FROM update_archive_overrides WHERE update_id=? AND actor='human:ben'", (ops_update["id"],)
            ).fetchone()
            is None
        )
        assert (
            c.execute("SELECT 1 FROM events WHERE target=? AND action='update.archive'", (ops_update["id"],)).fetchone()
            is None
        )

    rejected_read = api.post(
        "/api/v2/updates/read",
        json={"ids": [ops_update["id"], private_update["id"]]},
        headers=headers("ben-test", "mixed-read"),
    )
    assert rejected_read.status_code == 404
    with api.app.state.store.read() as c:
        assert (
            c.execute(
                "SELECT 1 FROM update_reads WHERE update_id=? AND actor='human:ben'", (ops_update["id"],)
            ).fetchone()
            is None
        )

    bot_attempt = api.post(
        "/api/v2/updates/archive",
        json={"ids": [ops_update["id"]], "archived": True},
        headers=headers(attempt["token"], "bot-archive"),
    )
    assert bot_attempt.status_code == 403
    with api.app.state.store.read() as c:
        assert (
            c.execute("SELECT 1 FROM update_archive_overrides WHERE update_id=?", (ops_update["id"],)).fetchone()
            is None
        )

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
        headline TEXT NOT NULL, body TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
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
    assert {r["name"] for r in c.execute("PRAGMA table_info(updates)")} >= {"slides"}
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
        item = updates.post(c, bot_id(c, "ops"), "- Retain this history", kind="daily")
    response = api.post("/api/v2/updates/archive", json={"ids": [item["id"]], "archived": True}, headers=headers())
    assert response.status_code == 200, response.text
    assert set(response.json()) == {"updated", "archived", "unread"}
