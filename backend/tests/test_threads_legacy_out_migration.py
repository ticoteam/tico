"""Migration 20261116172132_threads_legacy_out on a database that still holds pre-threads history in a bot's Home: a
message kept for a shared room's old readers is deleted as a person's delete deletes one, never opened to the Home's
readers; everything else stays; the message indexes are there; the record of moved chats goes. Running it again changes
nothing."""

import importlib

from backend import threads
from backend.store import H
from backend.tests.helpers.api import headers  # noqa: F401  (the api fixture)

MIGRATION = importlib.import_module("backend.migrations.20261116172132_threads_legacy_out")


def names(c, kind):
    return {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type=?", (kind,))}


def test_old_readers_history_is_deleted_not_opened_and_the_rest_stays(api):
    store = api.app.state.store
    with store.transaction() as c:
        home = threads.home(c, "bot:ops")["id"]
        kept = H.say(c, "human:ana", "bot:ops", "Plan for the week", conversation_id=home)
        old = H.say(c, "human:ana", "bot:ops", "The old room's secret", conversation_id=home)
        threads.react(c, "human:ana", old["id"], ":+1:")
        c.execute("UPDATE messages SET legacy_audience='shared:ops', restricted=1 WHERE id=?", (old["id"],))
        c.execute("CREATE TABLE home_merges(conversation_id TEXT PRIMARY KEY, home_id TEXT, bot_id TEXT)")
        c.execute("CREATE TABLE home_merge_messages(message_id TEXT PRIMARY KEY, conversation_id TEXT)")
        c.execute("DROP INDEX messages_thread_human")
        MIGRATION.up(c)
        first = [dict(r) for r in c.execute("SELECT * FROM messages WHERE conversation_id=? ORDER BY seq", (home,))]
        MIGRATION.up(c)
        again = [dict(r) for r in c.execute("SELECT * FROM messages WHERE conversation_id=? ORDER BY seq", (home,))]
        assert again == first
        rows = {m["id"]: m for m in first}
        gone = rows[old["id"]]
        assert gone["body"] == "" and gone["deleted_at"] and gone["legacy_audience"] is None
        assert rows[kept["id"]]["body"] == "Plan for the week" and not rows[kept["id"]]["deleted_at"]
        assert c.execute("SELECT COUNT(*) FROM message_reactions WHERE message_id=?", (old["id"],)).fetchone()[0] == 0
        states = {r[0] for r in c.execute("SELECT state FROM message_deliveries WHERE message_id=?", (old["id"],))}
        assert states <= {"moot", "delivered", "read"}
        assert c.execute("SELECT COUNT(*) FROM messages WHERE legacy_audience IS NOT NULL").fetchone()[0] == 0
        assert {"messages_seq", "messages_origin", "messages_thread_seq", "messages_thread_human"} <= names(c, "index")
        assert "messages_legacy_audience" not in names(c, "index")
        assert not {"home_merges", "home_merge_messages"} & names(c, "table")
    # The Home's readers never get the old words: the owner, who reads the Home, sees the plan and no secret.
    page = api.get(f"/api/v2/threads/{home}/timeline", headers=headers("ana-test"))
    assert page.status_code == 200, page.text
    assert "Plan for the week" in page.text and "secret" not in page.text
