"""Everyone's old personal chat with a bot moves into that bot's Home (backend/bot_home_merge.py): two people's rooms
and the bot's empty Home become one Home with both histories in seq order, read by anyone who reads the bot; a bot whose
Home is its migrated shared room keeps that history's old readers; the Assistant's rooms stay; a second run changes
nothing; `threads.home` is the merged Home, `threads._open_dm` no longer finds an old room, and a write that names an
old room lands in the Home. Fictional people only: ana (owner), ben and cara; the bots come from the api fixture.
"""

import pytest

from backend.tests.slug_ids import bot_ids_are_their_slugs  # noqa: F401  (each bot's id is its slug)
from backend import bot_home_merge, rooms, threads
from backend.store import H
from backend.tests.test_api import api  # noqa: F401  (the fixture)

PEOPLE = ("human:ana", "human:ben", "human:cara")


def store(api):
    return api.app.state.store


def room(c, person, bot):
    """A person's personal room with a bot, as rooms.personal_room opened them before Homes."""
    return rooms.personal_room(c, person, bot, subject="Chat with " + bot)["id"]


def say(c, sender, to, body, conversation_id, **kwargs):
    return H.say(c, sender, to, body, conversation_id=conversation_id, **kwargs)


def merge(api):
    with store(api).transaction() as c:
        return bot_home_merge.merge(c)


def seqs(c, thread_id):
    return [r[0] for r in c.execute("SELECT id FROM messages WHERE conversation_id=? ORDER BY seq", (thread_id,))]


def readers(c, message_id):
    message = H.message(c, message_id)
    return {p for p in PEOPLE if threads.message_readable(c, threads.actor_viewer(c, p), message)}


def unread(c, person, thread_id):
    return threads.unread(c, threads.actor_viewer(c, person), thread_id)["unread"]


@pytest.fixture
def ops_rooms(api):
    """ana and ben each talked to ops in their own room, interleaved; ops has an empty Home."""
    with store(api).transaction() as c:
        home = threads.home(c, "bot:ops", create=True)["id"]
        ana, ben = room(c, "human:ana", "ops"), room(c, "human:ben", "ops")
        written = [say(c, "human:ana", "bot:ops", "Ana one", ana)["id"],
                   say(c, "human:ben", "bot:ops", "Ben one", ben)["id"]]
        written.append(say(c, "bot:ops", "human:ana", "Reply to Ana", ana, in_reply_to=written[0])["id"])
        written.append(say(c, "human:ana", "bot:ops", "Ana two", ana)["id"])
        written.append(say(c, "bot:ops", "human:ben", "Reply to Ben", ben, in_reply_to=written[1])["id"])
    return {"home": home, "ana": ana, "ben": ben, "messages": written}


def test_two_peoples_rooms_and_an_empty_home_become_one_home_in_order(api, ops_rooms):
    with store(api).read() as c:
        before = {m: c.execute("SELECT seq FROM messages WHERE id=?", (m,)).fetchone()[0] for m in ops_rooms["messages"]}
        assert not threads.readable(c, threads.actor_viewer(c, "human:cara"), ops_rooms["ana"])
        had = {p: unread(c, "human:" + p, ops_rooms[p]) for p in ("ana", "ben")}
        assert had["ben"] >= 1        # ops's reply to ben, not read yet
    summary = merge(api)
    assert summary["rooms"] == 2 and summary["messages"] == 5 and not summary["errors"], summary
    assert summary["homes_made"] == 0
    home = ops_rooms["home"]
    with store(api).read() as c:
        # One Home, both histories, in the order they were written, each message keeping its id and seq.
        assert seqs(c, home) == ops_rooms["messages"]
        assert {m: c.execute("SELECT seq FROM messages WHERE id=?", (m,)).fetchone()[0]
                for m in ops_rooms["messages"]} == before
        row = threads.thread_row(c, home)
        assert row["last_seq"] == max(before.values())
        # Read by everyone who reads the bot's Home: cara reads ana's and ben's chats now.
        cara = threads.actor_viewer(c, "human:cara")
        assert cara.can_read_home(c, threads.home_owner(row))
        assert threads.readable(c, cara, home)
        for m in ops_rooms["messages"]:
            assert readers(c, m) == set(PEOPLE), m
            assert c.execute("SELECT legacy_audience FROM messages WHERE id=?", (m,)).fetchone()[0] is None
        # The old rooms are closed, recorded, and found by nothing that opens a chat.
        for person in ("ana", "ben"):
            old = threads.thread_row(c, ops_rooms[person])
            assert old["closed_at"] and old["room_key"] == f"ops:home:{home}"
            assert bot_home_merge.home_for(c, ops_rooms[person]) == home
            assert threads._open_dm(c, "human:" + person, ["bot:ops"]) is None
        merged = {r["conversation_id"]: dict(r) for r in c.execute("SELECT * FROM home_merges")}
        assert set(merged) == {ops_rooms["ana"], ops_rooms["ben"]}
        assert merged[ops_rooms["ana"]]["messages_count"] == 3 and merged[ops_rooms["ana"]]["owner_id"] == "human:ana"
        assert {r[0] for r in c.execute("SELECT message_id FROM home_merge_messages")} == set(ops_rooms["messages"])
        assert c.execute("SELECT COUNT(*) FROM events WHERE action=?", (bot_home_merge.EVENT,)).fetchone()[0] == 2
        # threads.home is the merged Home; the former owners follow it, and nobody wakes to a flood of unread.
        assert threads.home(c, "bot:ops", create=False)["id"] == home
        for person in ("human:ana", "human:ben"):
            assert c.execute("SELECT following FROM thread_followers WHERE thread_id=? AND actor=?",
                             (home, person)).fetchone()[0] == 1
        # What a person had unread in their own room stays unread, and only that: cara has nothing new to read.
        assert {p: unread(c, "human:" + p, home) for p in ("ana", "ben")} == had
        assert unread(c, "human:cara", home) == 0
        assert not c.execute("SELECT 1 FROM message_deliveries WHERE thread_id IN (?,?)",
                             (ops_rooms["ana"], ops_rooms["ben"])).fetchone()
    with store(api).read() as c:
        assert personal_rooms_left(c, "ops") == []


def personal_rooms_left(c, bot):
    return [r[0] for r in c.execute("SELECT id FROM conversations WHERE scope='personal' AND room_key=? "
                                    "AND closed_at IS NULL", (bot,))]


def test_a_bot_without_a_home_gets_one_made(api):
    with store(api).transaction() as c:
        assert threads.home(c, "bot:finance", create=False) is None
        cid = room(c, "human:ben", "finance")
        first = say(c, "human:ben", "bot:finance", "Budget?", cid)["id"]
    summary = merge(api)
    assert summary["homes_made"] == 1 and summary["rooms"] == 1, summary
    with store(api).read() as c:
        home = threads.home(c, "bot:finance", create=False)
        assert home and seqs(c, home["id"]) == [first]


def test_a_shared_room_home_keeps_its_legacy_readers(api):
    """cpo's Home is its shared room, whose history only its old members read. Personal rooms merged in are read by the
    Home's readers; the legacy history keeps exactly its old readers."""
    auth = api.app.state.auth
    with store(api).transaction() as c:
        shared = rooms.sync_shared_room(c, auth, "cpo", create=True)["id"]
        legacy = say(c, "human:ben", "bot:cpo", "Old shared plan", shared)["id"]
        c.execute("UPDATE messages SET legacy_audience='shared:cpo', restricted=1 WHERE id=?", (legacy,))
        assert threads.home(c, "bot:cpo", create=False)["id"] == shared
        ana = room(c, "human:ana", "cpo")
        mine = say(c, "human:ana", "bot:cpo", "Ana's own question", ana)["id"]
    with store(api).read() as c:
        legacy_before = readers(c, legacy)
    summary = merge(api)
    assert summary["rooms"] == 1 and not summary["errors"] and summary["homes_made"] == 0, summary
    with store(api).read() as c:
        assert seqs(c, shared) == [legacy, mine]
        assert readers(c, legacy) == legacy_before
        assert c.execute("SELECT legacy_audience FROM messages WHERE id=?", (legacy,)).fetchone()[0] == "shared:cpo"
        assert c.execute("SELECT legacy_audience FROM messages WHERE id=?", (mine,)).fetchone()[0] is None
        # Read by the Home's readers, by the Home's own rule (a shared room that stores no audience: its members).
        home_readers = {p for p in PEOPLE if threads.readable(c, threads.actor_viewer(c, p), shared)}
        assert "human:ana" in home_readers
        assert readers(c, mine) == home_readers
        assert threads.home(c, "bot:cpo", create=False)["id"] == shared


def test_assistant_rooms_and_other_rows_stay(api):
    with store(api).transaction() as c:
        coo = room(c, "human:ana", "coo")
        assistant = H.open_conversation(c, "human:ana", ["human:ana", "bot:coo"], kind="chat", subject="Assistant",
                                        scope=rooms.PERSONAL, owner_actor="human:ana",
                                        room_key=rooms.ASSISTANT_ROOM)["id"]
        dm = H.open_conversation(c, "human:ana", ["human:ana", "bot:ops"], kind="chat", subject="Direct")["id"]
        closed = room(c, "human:cara", "ops")
        say(c, "human:cara", "bot:ops", "Old", closed)
        c.execute("UPDATE conversations SET closed_at=? WHERE id=?", (H.now(), closed))
    summary = merge(api)
    assert summary["rooms"] == 0, summary
    with store(api).read() as c:
        for cid in (coo, assistant, dm):
            row = threads.thread_row(c, cid)
            assert not row["closed_at"], cid
            assert bot_home_merge.home_for(c, cid) is None
        assert seqs(c, closed)
        assert threads.home(c, "bot:coo", create=False) is None


def test_a_second_run_changes_nothing(api, ops_rooms):
    merge(api)
    with store(api).read() as c:
        first = {t: [tuple(r) for r in c.execute(f"SELECT * FROM {t} ORDER BY 1")]
                 for t in ("messages", "home_merges", "thread_followers", "message_deliveries")}
    again = merge(api)
    assert again["rooms"] == 0 and again["messages"] == 0 and not again["errors"], again
    with store(api).transaction() as c:
        # The start-up step: done once, then a no-op.
        c.execute("DELETE FROM registry_metadata WHERE key=?", (bot_home_merge.KEY,))
        assert bot_home_merge.run_once(c)["rooms"] == 0
        assert bot_home_merge.done(c)
        assert bot_home_merge.run_once(c) is None
    with store(api).read() as c:
        assert first == {t: [tuple(r) for r in c.execute(f"SELECT * FROM {t} ORDER BY 1")]
                         for t in ("messages", "home_merges", "thread_followers", "message_deliveries")}


def test_a_write_that_names_an_old_room_lands_in_the_home(api, ops_rooms):
    merge(api)
    home = ops_rooms["home"]
    with store(api).transaction() as c:
        reply = say(c, "bot:ops", "human:ana", "A late answer", ops_rooms["ana"], in_reply_to=ops_rooms["messages"][3])
        assert reply["conversation_id"] == home
        posted = threads.post(c, "bot:ops", ops_rooms["ben"], "Posted to Ben's old room")
        assert posted["conversation_id"] == home
        # A person writing to the bot with no room named starts no personal room in the old one's place.
        assert threads._open_dm(c, "human:ana", ["bot:ops"]) is None
    with store(api).read() as c:
        assert seqs(c, home)[-2:] == [reply["id"], posted["id"]]
        assert not seqs(c, ops_rooms["ana"]) and not seqs(c, ops_rooms["ben"])


def test_an_old_tasks_comments_and_quiet_bot_posts_follow_the_room(api):
    """A task from before Oct 13 names the person's room. Its comments read from the Home; a bot's post there that did
    not reach the person (a task's thread, H.task_post_sql) does not start counting in the Home."""
    from backend.tests.test_api import post
    task = post(api, "tasks", {"owner": "ops", "title": "Summarize", "body": "Please summarize the current work."})
    task_id = task["id"] if "id" in task else task["task"]["id"]
    with store(api).transaction() as c:
        home = threads.home(c, "bot:ops", create=True)["id"]
        cid = room(c, "human:ana", "ops")
        comment = say(c, "human:ana", "bot:ops", "A note on it", cid, refs={"task": task_id, "comment": True})["id"]
        quiet = say(c, "bot:ops", "human:ana", "Working on it", cid, refs={"task": task_id}, in_reply_to=comment)["id"]
        waiting = [r[0] for r in c.execute("SELECT id FROM message_deliveries WHERE message_id=? AND recipient=? "
                                           "AND state IN ('pending','delivered','read')", (quiet, "human:ana"))]
        assert waiting
        # The room becomes the task's room, as tasks filed before Oct 13 named it: the bot's post stops counting.
        c.execute("UPDATE tasks SET conversation_id=? WHERE id=?", (cid, task_id))
    summary = merge(api)
    assert summary["rooms"] == 1 and summary["moot"] == len(waiting), summary
    with store(api).read() as c:
        assert comment in [m["id"] for m in H.task_comments(c, task_id)]
        assert not c.execute("SELECT 1 FROM message_deliveries WHERE message_id=? AND recipient=? "
                             "AND state IN ('pending','delivered','read')", (quiet, "human:ana")).fetchone()
        assert c.execute("SELECT conversation_id FROM tasks WHERE id=?", (task_id,)).fetchone()[0] == cid
        assert seqs(c, home)[-2:] == [comment, quiet]


def test_a_room_whose_owner_cannot_read_the_home_stays(api):
    """inbox is Ana's alone: ben's old room with it would put his history where he cannot read it."""
    with store(api).transaction() as c:
        cid = room(c, "human:ben", "inbox")
    summary = merge(api)
    assert summary["rooms"] == 0
    assert any(s.get("room") == cid for s in summary["skipped"]), summary
    with store(api).read() as c:
        assert not threads.thread_row(c, cid)["closed_at"]
