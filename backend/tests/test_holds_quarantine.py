"""Migration 20261117072054_holds_quarantine: every quarantine moves into the holds table (backend/holds.py) - a
quarantined bot's as an open hold on the bot, and each quarantine_holds row (one task or chat) as a hold on it."""

import json

from backend.migrations import DIRECTORY, statements
from backend.store import H
from backend.tests.helpers.api import get  # noqa: F401  (the api fixture)

MIGRATION = DIRECTORY / "20261117072054_holds_quarantine.sql"
HELD = "2026-10-24T12:00:00.000000Z"
COOLED = "2026-10-24T13:00:00.000000Z"
EARLY = "2026-10-24T12:30:00.000000Z"
OLD = (
    "CREATE TABLE IF NOT EXISTS quarantine_holds(id INTEGER PRIMARY KEY, bot_id TEXT NOT NULL, task_id TEXT, "
    "conversation_id TEXT, reason TEXT NOT NULL, refusal TEXT NOT NULL, created_at TEXT NOT NULL, released_at TEXT)"
)
REFUSAL = {"rule": "R8", "detail": "wrote outside its folder"}
REVIEW = {"what": "file write", "found": "a key"}
COLUMNS = (
    "id,kind,holdable_type,holdable_id,bot_id,reason,details,until_at,created_by_id,created_at,updated_at,"
    "released_at,released_by_id,released_how"
)


def _migrate(c):
    for statement in statements(MIGRATION.read_text()):
        c.execute(statement)


def _rows(c):
    return {r["id"]: dict(r) for r in c.execute(f"SELECT {COLUMNS} FROM holds WHERE kind='quarantine' ORDER BY id")}


def _event(c, id, bot, detail, at=HELD):
    c.execute(
        "INSERT INTO events(id,created_at,actor,action,target,detail) VALUES(?,?,'keeper','quarantine',?,?)",
        (id, at, f"bot:{bot}", json.dumps(detail)),
    )


def _shown(c, bot):
    return c.execute(f"SELECT {H.BOT_STATE_SQL} FROM bots b WHERE b.id=?", (bot,)).fetchone()[0]


def test_every_quarantine_moves_into_holds_and_running_it_again_changes_nothing(api):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET state='quarantined' WHERE id IN ('ops','cpo','finance')")
        # Two events at one instant: the later row is the bot's latest.
        _event(c, "e1", "ops", {"reason": "2 refusals today"})
        _event(c, "e2", "ops", {"reason": "3 refusals today", "refusal": REFUSAL})
        _event(c, "e3", "cpo", {"reason": "escaped its folder", "review": REVIEW})  # an escape: no cooldown
        c.execute(OLD)  # an older database's table, as a release before this one left it
        c.executemany(
            "INSERT INTO quarantine_holds(id,bot_id,task_id,conversation_id,reason,refusal,created_at,released_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            [
                (1, "ops", "task-1", None, "held", json.dumps(REFUSAL), HELD, None),  # held now
                (2, "ops", None, "chat-1", "held", json.dumps(REFUSAL), HELD, COOLED),  # its cooldown released it
                (3, "cpo", "task-2", None, "held", "not json", HELD, EARLY),  # a person released it early
            ],
        )
        _migrate(c)
        assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='quarantine_holds'").fetchone()
        moved = _rows(c)
        _migrate(c)  # again: the older table comes back empty and goes, and no bot is quarantined
        assert _rows(c) == moved
        assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='quarantine_holds'").fetchone()

        states = dict(c.execute("SELECT id,state FROM bots WHERE id IN ('ops','cpo','finance')").fetchall())
        assert states == {"ops": "active", "cpo": "active", "finance": "active"}
        assert [_shown(c, bot) for bot in ("ops", "cpo", "finance")] == ["quarantined"] * 3  # rc.1's status payload

        bot = {"kind": "quarantine", "holdable_type": "bot", "created_by_id": "keeper", "released_at": None}
        bot |= {"released_by_id": None, "released_how": None}
        assert moved["bots:quarantined:ops"] == {
            **bot,
            "id": "bots:quarantined:ops",
            "holdable_id": "ops",
            "bot_id": "ops",
            "reason": "3 refusals today",
            "details": json.dumps({"refusal": REFUSAL}, separators=(",", ":")),
            "until_at": COOLED,
            "created_at": HELD,
            "updated_at": HELD,
        }
        assert moved["bots:quarantined:cpo"]["reason"] == "escaped its folder"
        assert json.loads(moved["bots:quarantined:cpo"]["details"]) == {"review": REVIEW}
        assert moved["bots:quarantined:cpo"]["until_at"] is None  # waits for a person
        finance = moved["bots:quarantined:finance"]
        assert (finance["reason"], finance["details"], finance["until_at"]) == ("", "{}", None)

        task = {"kind": "quarantine", "reason": "held", "until_at": COOLED, "created_by_id": "keeper"}
        task |= {"created_at": HELD}
        assert moved["quarantine_holds:1"] == {
            **task,
            "id": "quarantine_holds:1",
            "holdable_type": "task",
            "holdable_id": "task-1",
            "bot_id": "ops",
            "details": json.dumps({"refusal": REFUSAL}, separators=(",", ":")),
            "updated_at": HELD,
            "released_at": None,
            "released_by_id": None,
            "released_how": None,
        }
        chat = moved["quarantine_holds:2"]
        assert (chat["holdable_type"], chat["holdable_id"], chat["updated_at"]) == ("conversation", "chat-1", COOLED)
        assert (chat["released_by_id"], chat["released_how"]) == ("keeper", "expired")
        early = moved["quarantine_holds:3"]
        assert json.loads(early["details"]) == {"refusal": "not json"}
        assert (early["released_at"], early["released_by_id"], early["released_how"]) == (EARLY, None, "person")
