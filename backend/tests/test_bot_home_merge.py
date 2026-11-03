"""Everyone's old personal chat with a bot moves into that bot's Home (backend/bot_home_merge.py): two people's rooms
and the bot's empty Home become one Home with both histories in seq order, read by anyone who reads the bot; a bot whose
Home is its migrated shared room keeps that history's old readers; the Assistant's rooms stay; a second run changes
nothing; `threads.home` is the merged Home, `threads._open_dm` no longer finds an old room, and a write that names an
old room lands in the Home, whichever path writes it: the API, the central writer, a person's new chat, a task's history.
Fictional people only: ana (owner), ben and cara; the bots come from the api fixture.
"""

import pytest

from backend.tests.slug_ids import bot_ids_are_their_slugs  # noqa: F401  (each bot's id is its slug)
from backend import bot_home_merge, rooms, threads
from backend.store import H
from backend.tests.test_api import api, headers  # noqa: F401  (the fixture)
from backend.tests.test_threads_private_execution import (  # noqa: F401  (the helper and the fixture it needs)
    private_run, private_tasks_have_an_unrelated_manager)

PEOPLE = ("human:ana", "human:ben", "human:cara")


def store(api):
    return api.app.state.store


def room(c, person, bot):
    """A person's personal room with a bot, as rooms.personal_room opened them before Homes (a legacy chat)."""
    return H.open_conversation(c, person, [person, "bot:" + bot], kind="chat", subject="Chat with " + bot,
                               scope=rooms.PERSONAL, owner_actor=person, room_key=bot, allow_held=True)["id"]


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


class Recorded:
    """A connection that keeps the statements it runs, so a test can count them."""

    def __init__(self, c):
        self._c, self.statements = c, []

    def execute(self, sql, *args):
        self.statements.append(sql)
        return self._c.execute(sql, *args)

    def __getattr__(self, name):
        return getattr(self._c, name)


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
        # The moved history is read history for everyone who reads the Home, including ben's unread reply (owner
        # decision, Nov 3: old unread state is discarded, not carried over).
        assert {p: unread(c, "human:" + p, home) for p in ("ana", "ben", "cara")} == {"ana": 0, "ben": 0, "cara": 0}
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


def test_the_api_puts_a_reply_to_an_old_room_in_the_home_and_refuses_a_private_run_there(api, ops_rooms):
    """Finding 1: the API's checks read the room a message names. A reply to a moved room names the Home from the start,
    so a private run's reply is checked against the Home (no audience for private text) and refused, and a person's
    reply lands in the Home."""
    merge(api)
    home = ops_rooms["home"]
    sent = api.post("/api/v2/messages", json={"to": "bot:ops", "text": "Ana, late", "conversation_id": ops_rooms["ana"]},
                    headers=headers("ana-test"))
    assert sent.status_code == 200, sent.text
    with store(api).read() as c:
        assert c.execute("SELECT conversation_id FROM messages WHERE id=?", (sent.json()["id"],)).fetchone()[0] == home
    row, attempt = private_run(api, "ops")
    refused = api.post("/api/v2/messages", json={"to": "human:ana", "text": "Confidential reply",
                                                 "conversation_id": ops_rooms["ana"]},
                       headers=headers(attempt["token"]))
    assert refused.status_code == 403, refused.text
    refused = api.post("/api/v2/messages", json={"to": "human:ana", "text": "Confidential again", "conversation_id": home},
                       headers=headers(attempt["token"]))
    assert refused.status_code == 403, refused.text
    with store(api).read() as c:
        assert not c.execute("SELECT 1 FROM messages WHERE body LIKE 'Confidential%'").fetchone()
        assert not seqs(c, ops_rooms["ana"])


def test_the_central_writer_puts_a_notice_for_an_old_room_in_the_home(api, ops_rooms):
    """Finding 2: writers that do not go through H.say (a task's notice, a reconnect card, a run's reply) name the old room
    too. The central writer sends them to the Home, and a task is never filed in one."""
    merge(api)
    home = ops_rooms["home"]
    with store(api).transaction() as c:
        conv = H.conversation(c, ops_rooms["ana"])
        notice = H._write_message(c, "bot:ops", "human:ana", "A notice for Ana", conv, "say", {}, None, None)
        assert notice["conversation_id"] == home
        auth = api.app.state.auth
        assert rooms.task_conversation_id(c, auth, "bot:ops", "human:ana") is None
    with store(api).read() as c:
        assert seqs(c, home)[-1] == notice["id"]
        assert not seqs(c, ops_rooms["ana"])


def test_a_persons_chat_with_a_moved_bot_is_its_home_on_every_route(api, ops_rooms):
    """Finding 3: personal_room, work_room and the chat route resolve to the Home for an ordinary bot. The Assistant's
    rooms stay as they are."""
    merge(api)
    home = ops_rooms["home"]
    auth = api.app.state.auth
    with store(api).transaction() as c:
        assert rooms.personal_room(c, "human:ana", "ops")["id"] == home
        assert rooms.work_room(c, auth, "ops", "human:ana")["id"] == home
        assert threads.home(c, "bot:coo", create=False) is None
        assert rooms.personal_room(c, "human:ana", "coo")["id"] != home
    sent = api.post("/api/v2/chat/ops", json={"text": "Hello from Ana"}, headers=headers("ana-test"))
    assert sent.status_code == 200, sent.text
    assert sent.json()["conversation"]["id"] == home
    with store(api).read() as c:
        assert seqs(c, home)[-1] == sent.json()["message"]["id"]


def test_a_moved_rooms_read_marks_are_discarded_for_root_and_sub_threads(api):
    """Finding 4: after the move, every reader of the Home has read the moved history, root and sub-threads alike."""
    with store(api).transaction() as c:
        home = threads.home(c, "bot:ops", create=True)["id"]
        ana = room(c, "human:ana", "ops")
        asked = say(c, "human:ana", "bot:ops", "A question with a thread", ana)["id"]
        sub = threads.sub_thread(c, "human:ana", asked)["id"]
        threads.post(c, "bot:ops", sub, "An answer in the thread")
    merge(api)
    with store(api).read() as c:
        assert threads.thread_row(c, sub)["root_id"] == home
        for person in PEOPLE:
            assert unread(c, person, home) == 0, person
            assert unread(c, person, sub) == 0, person


def test_a_tasks_history_is_read_from_its_room_and_the_homes_it_is_in(api, ops_rooms):
    """Finding 5: a task whose messages were written in a room reads them from the room, the Home they moved into, and the
    Home it is in now, even after that Home is archived and a new one takes the writes."""
    from backend.tests.test_api import post
    task = post(api, "tasks", {"owner": "ops", "title": "Summarize", "body": "Please summarize the current work."})
    task_id = task["id"] if "id" in task else task["task"]["id"]
    with store(api).transaction() as c:
        c.execute("UPDATE tasks SET conversation_id=? WHERE id=?", (ops_rooms["ana"], task_id))
        note = say(c, "human:ana", "bot:ops", "A note on it", ops_rooms["ana"],
                   refs={"task": task_id, "comment": True})["id"]
    merge(api)
    with store(api).transaction() as c:
        c.execute("UPDATE conversations SET archived_at=? WHERE id=?", (H.now(), ops_rooms["home"]))
        assert threads.home(c, "bot:ops", create=False) is None
    with store(api).transaction() as c:
        assert note in [m["id"] for m in H.task_comments(c, task_id)]
        new_home = threads.home(c, "bot:ops", create=True)["id"]
        assert new_home != ops_rooms["home"]
        later = say(c, "bot:ops", "human:ana", "Later, in the new Home", new_home, refs={"task": task_id})["id"]
    with store(api).read() as c:
        ids = [m["id"] for m in H.task_comments(c, task_id)]
        assert note in ids and later in ids and ids.index(note) < ids.index(later), ids


def test_a_bot_that_waits_on_a_person_keeps_the_install_marker_unset(api, ops_rooms, monkeypatch):
    """Finding 6: a bot that waits on a person (a Skip: two open Homes with messages, say) is not finished. The install is
    not marked done while that skip stands, its rooms stay where they are, and the next start moves them. The Home pick
    is stubbed to wait: two open Homes of one owner cannot exist (threads_one_home)."""
    real_home = bot_home_merge._home

    def waiting(c, bot, summary):
        raise bot_home_merge.Skip("two open Homes have messages: waits for a person")

    monkeypatch.setattr(bot_home_merge, "_home", waiting)
    with store(api).transaction() as c:
        c.execute("DELETE FROM registry_metadata WHERE key=?", (bot_home_merge.KEY,))
        summary = bot_home_merge.run_once(c)
        assert summary["pending"] == ["ops"], summary
        assert summary["rooms"] == 0 and not summary["errors"], summary
        assert not bot_home_merge.done(c)
        assert thread_is_open(c, ops_rooms["ana"]) and thread_is_open(c, ops_rooms["ben"])
    monkeypatch.setattr(bot_home_merge, "_home", real_home)
    with store(api).transaction() as c:
        summary = bot_home_merge.run_once(c)
        assert not summary["pending"] and not summary["errors"] and summary["rooms"] == 2, summary
        assert bot_home_merge.done(c)


def thread_is_open(c, thread_id):
    return not threads.thread_row(c, thread_id)["closed_at"]


def test_each_keyed_table_moves_in_one_statement_per_bot(api):
    """Finding 7: a bot's rooms move in bulk. Each table keyed by a room is updated once for the bot, not once per room."""
    with store(api).transaction() as c:
        threads.home(c, "bot:ops", create=True)
        for person in PEOPLE:
            say(c, person, "bot:ops", "Hello", room(c, person, "ops"))
    with store(api).transaction() as c:
        recorded = Recorded(c)
        summary = bot_home_merge.merge(recorded)
    assert summary["rooms"] == 3 and not summary["errors"], summary
    for table, _column in bot_home_merge.KEYED:
        updates = [s for s in recorded.statements if s.startswith(f"UPDATE {table} ")]
        assert len(updates) <= 1, (table, updates)
    assert sum(s.startswith("UPDATE thread_followups ") for s in recorded.statements) == 1
