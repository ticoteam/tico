"""Everyone's old personal chat with a bot moves into that bot's Home (backend/bot_home_merge.py): two people's rooms
and the bot's empty Home become one Home with both histories in seq order, read by anyone who reads the bot; a bot whose
Home is its migrated shared room keeps that history's old readers; the Assistant's rooms stay; a second run changes
nothing; `threads.home` is the merged Home, `threads._open_dm` no longer finds an old room, and a write that names an
old room lands in the Home, whichever path writes it: the API, the central writer, a person's new chat, a task's history.
Fictional people only: ana (owner), ben and cara; the bots come from the api fixture.
"""

import pytest

from backend.tests.slug_ids import bot_ids_are_their_slugs  # noqa: F401  (each bot's id is its slug)
from backend import bot_home_merge, rooms, threads, visuals
from backend.auth import Identity
from backend.blobs import register
from backend.files import series
from backend.store import H, task_message_page
from backend.tests.test_api import api, headers, post, restrict  # noqa: F401  (the fixture; post is a helper)
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


def test_chat_with_lists_a_moved_bots_readable_home_and_keeps_unmoved_dms(api, ops_rooms):
    merge(api)
    home = ops_rooms["home"]
    listed = api.get("/api/v2/conversations", params={"chat_with": "ops"}, headers=headers("ana-test"))
    assert listed.status_code == 200, listed.text
    assert [row["id"] for row in listed.json()["conversations"]] == [home]

    # Cara may not read Ops's Home, so the lookup must not reveal it.
    with store(api).transaction() as c:
        restrict(c, "ops", see={"people": ["ana", "cara"]}, read={"people": ["ana"]},
                 write={"people": ["ana"]})
    hidden = api.get("/api/v2/conversations", params={"chat_with": "ops"}, headers=headers("cara-test"))
    assert hidden.status_code == 200 and hidden.json()["conversations"] == []

    # A new chat with a bot the migration never covered remains the original pair DM.
    with store(api).transaction() as c:
        finance = room(c, "human:ana", "finance")
    unchanged = api.get("/api/v2/conversations", params={"chat_with": "finance"}, headers=headers("ana-test"))
    assert unchanged.status_code == 200, unchanged.text
    assert [row["id"] for row in unchanged.json()["conversations"]] == [finance]


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


def test_a_private_runs_message_to_its_requester_with_no_room_named_is_refused(api, ops_rooms):
    """Finding 1 (P1): a bot's message to a person that names no room is that person's chat with the bot, which is the
    Home once the chats have moved. A private run's words cannot go there: the destination is named before the checks,
    and the named-room check refuses it, so nothing is written."""
    merge(api)
    _row, attempt = private_run(api, "ops")          # the run's requester is ben
    refused = api.post("/api/v2/messages", json={"to": "human:ben", "text": "Implicit confidential"},
                       headers=headers(attempt["token"]))
    assert refused.status_code == 403, refused.text
    with store(api).read() as c:
        assert not c.execute("SELECT 1 FROM messages WHERE body LIKE 'Implicit confidential%'").fetchone()


def test_an_unread_home_message_between_moved_messages_stays_unread(api):
    """Finding 2 (P2): moved history is read for everyone, but a message already in the Home and unread for a reader stays
    unread, even with moved messages after it; what is written after the move counts as usual."""
    with store(api).transaction() as c:
        home = threads.home(c, "bot:ops", create=True)["id"]
        ana = room(c, "human:ana", "ops")
        say(c, "human:ana", "bot:ops", "Legacy one", ana)
        say(c, "human:ben", "bot:ops", "In the Home, before the move", home)
        say(c, "human:ana", "bot:ops", "Legacy two", ana)
        say(c, "human:ben", "bot:ops", "In the Home, later", home)
    with store(api).read() as c:
        assert unread(c, "human:cara", home) == 2
    merge(api)
    with store(api).read() as c:
        assert unread(c, "human:cara", home) == 2
    with store(api).transaction() as c:
        say(c, "human:ben", "bot:ops", "Written after the move", home)
    with store(api).read() as c:
        assert unread(c, "human:cara", home) == 3


def test_a_tasks_history_keeps_every_home_through_two_archives(api, ops_rooms):
    """Finding 3 (P2): a task's history reads every Home its bot has had, archived ones included. Each comment written
    through the old room after its Home was archived lands in the next Home, and all three stay in the task's history."""
    from backend.tests.test_api import post
    task = post(api, "tasks", {"owner": "ops", "title": "Summarize", "body": "Please summarize the current work."})
    task_id = task["id"] if "id" in task else task["task"]["id"]
    ana = ops_rooms["ana"]
    with store(api).transaction() as c:
        c.execute("UPDATE tasks SET conversation_id=? WHERE id=?", (ana, task_id))
        notes = [say(c, "human:ana", "bot:ops", "Comment one", ana, refs={"task": task_id, "comment": True})["id"]]
    merge(api)
    for text in ("Comment two", "Comment three"):
        with store(api).transaction() as c:
            open_home = threads.home(c, "bot:ops", create=False)["id"]
            c.execute("UPDATE conversations SET archived_at=? WHERE id=?", (H.now(), open_home))
            notes.append(say(c, "human:ana", "bot:ops", text, ana, refs={"task": task_id, "comment": True})["id"])
    with store(api).read() as c:
        landed = {r[0] for r in c.execute(
            "SELECT conversation_id FROM messages WHERE id IN (%s)" % ",".join("?" * len(notes)), notes)}
        assert len(landed) == 3, landed                       # one Home each, the first merged one included
        ids = [m["id"] for m in H.task_comments(c, task_id)]
        assert [i for i in ids if i in notes] == notes, ids
        page = task_message_page(c, H.task(c, task_id), limit=50)
        assert [m["id"] for m in page["messages"] if m["id"] in notes] == notes, page


def test_a_write_to_an_open_room_asks_for_no_home_lookup(api, ops_rooms):
    """Finding 4: only a closed room can have moved, so a write to an open room looks up no Home."""
    merge(api)
    with store(api).transaction() as c:
        open_room = room(c, "human:cara", "ops")
        recorded = Recorded(c)
        say(recorded, "human:cara", "bot:ops", "An open room, no lookup", open_room)
    assert not [s for s in recorded.statements if "home_merges" in s], recorded.statements


def test_a_chat_with_a_bot_the_move_never_reached_costs_one_lookup(api):
    """Finding 4: a bot the move never reached costs one lookup to learn that, on every chat asked about it."""
    with store(api).transaction() as c:
        recorded = Recorded(c)
        assert bot_home_merge.home_for_chat(recorded, "human:ben", "finance") is None
    assert len(recorded.statements) == 1, recorded.statements


def test_an_explicit_reply_to_a_moved_room_with_an_archived_home_is_checked_in_the_new_home(api, ops_rooms):
    """A reply that names a moved room whose Home is archived makes the bot's Home before the checks (as the implicit
    path does), so a private run's reply is refused in that Home and a person's reply lands in the new one."""
    merge(api)
    with store(api).transaction() as c:
        c.execute("UPDATE conversations SET archived_at=? WHERE id=?", (H.now(), ops_rooms["home"]))
    _row, attempt = private_run(api, "ops")
    refused = api.post("/api/v2/messages", json={"to": "human:ana", "text": "Confidential after the archive",
                                                 "conversation_id": ops_rooms["ana"]},
                       headers=headers(attempt["token"]))
    assert refused.status_code == 403, refused.text
    sent = api.post("/api/v2/messages", json={"to": "bot:ops", "text": "Ana, after the archive",
                                              "conversation_id": ops_rooms["ana"]},
                    headers=headers("ana-test"))
    assert sent.status_code == 200, sent.text
    with store(api).read() as c:
        assert not c.execute("SELECT 1 FROM messages WHERE body LIKE 'Confidential after the archive%'").fetchone()
        landed = c.execute("SELECT conversation_id FROM messages WHERE id=?", (sent.json()["id"],)).fetchone()[0]
        new_home = threads.home(c, "bot:ops", create=False)
        assert new_home is not None and landed == new_home["id"] and landed != ops_rooms["home"]


def test_a_second_chat_resolution_in_a_transaction_costs_less_than_the_first(api, ops_rooms):
    """Finding 4: a person's chat with a covered bot resolves its Home with a bounded number of statements. The first
    resolution in a transaction reads the bot, its config, the Home and the person's rights; a second one in the same
    transaction reuses what the read cache holds (backend/read_cache.py), so it costs only what is not cached."""
    merge(api)
    with store(api).transaction() as c:
        first, second = Recorded(c), Recorded(c)
        assert bot_home_merge.home_for_chat(first, "human:ben", "ops") == ops_rooms["home"]
        assert bot_home_merge.home_for_chat(second, "human:ben", "ops") == ops_rooms["home"]
    # Measured on RunsOn at 18 for the first resolution: the bot and its config, the open Home, the person's viewer, the
    # federation check and the readable check. A second resolution in the same transaction reads the repeated rows from
    # the read cache (backend/read_cache.py), so it costs fewer statements.
    assert len(first.statements) <= 18, first.statements
    assert len(second.statements) < len(first.statements), second.statements


def body_id(api, text):
    with store(api).read() as c:
        return c.execute("SELECT id FROM messages WHERE body=? AND deleted_at IS NULL", (text,)).fetchone()[0]


def test_a_task_comment_on_a_moved_room_is_edited_and_deleted_by_its_author(api, ops_rooms):
    """A task keeps the room it was written in, and that room moved: the task's comments are found in every room and Home
    of its history (hubdb.comment_on), so the author edits or deletes one written before the move and one written after it,
    through the redirect. A comment carried by the move keeps its `moved_home` mark, which is_comment allows."""
    task_id = post(api, "tasks", {"owner": "ops", "title": "Summarize", "body": "Please summarize the work."})["id"]
    with store(api).transaction() as c:
        c.execute("UPDATE tasks SET conversation_id=? WHERE id=?", (ops_rooms["ana"], task_id))
        old = say(c, "human:ana", "bot:ops", "An old note", ops_rooms["ana"],
                  refs={"task": task_id, "comment": True})["id"]
    merge(api)
    post(api, f"tasks/{task_id}/comments", {"text": "A note after the move"})
    new = body_id(api, "A note after the move")
    with store(api).read() as c:
        assert H.is_comment(H.message(c, old)) and H.message(c, old)["refs"].get("moved_home")
        assert H.message(c, new)["conversation_id"] == ops_rooms["home"]
    post(api, f"tasks/{task_id}/comments/{old}", {"text": "An old note, edited"})
    post(api, f"tasks/{task_id}/comments/{new}", {"text": "A note after the move, edited"})
    with store(api).read() as c:
        texts = {m["id"]: m["body"] for m in H.task_comments(c, task_id)}
        assert texts[old] == "An old note, edited" and texts[new] == "A note after the move, edited", texts
    post(api, f"tasks/{task_id}/comments/{new}/delete", {})
    with store(api).read() as c:
        listed = [m["id"] for m in H.task_comments(c, task_id)]
        assert new not in listed and old in listed


def test_a_question_on_a_moved_task_is_read_across_the_move_and_replaced_by_a_newer_one(api, ops_rooms):
    """A task's open questions are read from its history too: one asked before its room moved is replaced by a newer one to
    the same person (the newer one goes through the redirect), and a question to another person stays open. Each is listed
    for its own task only, and the batch read agrees with the single one for every reader."""
    questions = [{"id": "q1", "header": "Pick", "question": "Which one?", "options": [], "multi": False, "other": True}]
    first = post(api, "tasks", {"owner": "ops", "title": "First", "body": "Pick for the first."})["id"]
    second = post(api, "tasks", {"owner": "ops", "title": "Second", "body": "Pick for the second."})["id"]
    with store(api).transaction() as c:
        c.execute("UPDATE tasks SET conversation_id=? WHERE id=?", (ops_rooms["ana"], first))
        c.execute("UPDATE tasks SET conversation_id=? WHERE id=?", (ops_rooms["ben"], second))
        before = say(c, "bot:ops", "human:ana", "Which one for the first?", ops_rooms["ana"], kind="ask",
                     refs={"task": first, "questions": questions})["id"]
        other = say(c, "bot:ops", "human:ben", "Which one for the second?", ops_rooms["ben"], kind="ask",
                    refs={"task": second, "questions": questions})["id"]
    merge(api)
    with store(api).transaction() as c:
        after = say(c, "bot:ops", "human:ana", "Which one for the first, again?", ops_rooms["ana"], kind="ask",
                    refs={"task": first, "questions": questions})["id"]
        assert H.message(c, after)["conversation_id"] == ops_rooms["home"]
    with store(api).read() as c:
        row_first, row_second = H.task(c, first), H.task(c, second)
        # The newer question to the same person replaces the one asked before the move (supersede_asks reads the history).
        assert H.message(c, before)["superseded_at"] and H.message(c, after)["superseded_at"] is None
        assert {a["id"] for a in H.open_task_asks(c, row_first)} == {after}
        assert [a["id"] for a in H.open_task_asks(c, row_second)] == [other]
        for actor in ("human:ana", "human:ben"):
            batch = H.open_tasks_asks(c, [row_first, row_second], actor=actor)
            for row in (row_first, row_second):
                assert [a["id"] for a in batch[row["id"]]] == \
                    [a["id"] for a in H.open_task_asks(c, row, actor=actor)], (actor, row["id"])
        assert {a["id"] for a in H.open_tasks_asks(c, [row_first], actor="human:ana")[first]} == {after}


QUESTIONS = [{"id": "q1", "header": "Pick", "question": "Which one?", "options": [], "multi": False, "other": True}]


def test_a_question_asked_before_its_task_room_moved_still_lists_the_task_in_needs_you(api, ops_rooms):
    """Needs you (hubdb.tasks_asked_of) reads a task's asks from its history as its queue does: a question asked before the
    task's room moved into a Home lists the task for the person it is addressed to, and so does one asked after the move."""
    first = post(api, "tasks", {"owner": "ops", "title": "First", "body": "Pick for the first."})["id"]
    second = post(api, "tasks", {"owner": "ops", "title": "Second", "body": "Pick for the second."})["id"]
    with store(api).transaction() as c:
        c.execute("UPDATE tasks SET conversation_id=? WHERE id=?", (ops_rooms["ana"], first))
        c.execute("UPDATE tasks SET conversation_id=? WHERE id=?", (ops_rooms["ben"], second))
        say(c, "bot:ops", "human:ana", "Which one for the first?", ops_rooms["ana"], kind="ask",
            refs={"task": first, "questions": QUESTIONS})
        say(c, "bot:ops", "human:ben", "Which one for the second?", ops_rooms["ben"], kind="ask",
            refs={"task": second, "questions": QUESTIONS})
    merge(api)
    with store(api).transaction() as c:
        say(c, "bot:ops", "human:ana", "Which one for the first, again?", ops_rooms["ana"], kind="ask",
            refs={"task": first, "questions": QUESTIONS})
    with store(api).read() as c:
        ana = [t["id"] for t in H.tasks_asked_of(c, "human:ana")]
        ben = [t["id"] for t in H.tasks_asked_of(c, "human:ben")]
    assert first in ana and second not in ana, ana
    assert second in ben and first not in ben, ben


def test_a_question_asked_before_the_move_is_superseded_by_a_replacement_and_by_the_task_end(api, ops_rooms):
    """supersede_asks reads a task's history as its queue does. A question asked before the task's room moved is replaced
    when a newer one goes to the same person on that task, and finishing the task (no filter) supersedes the question still
    open for it, moved or not. A question to another person is left alone."""
    first = post(api, "tasks", {"owner": "ops", "title": "First", "body": "Pick for the first."})["id"]
    second = post(api, "tasks", {"owner": "ops", "title": "Second", "body": "Pick for the second."})["id"]
    with store(api).transaction() as c:
        c.execute("UPDATE tasks SET conversation_id=? WHERE id=?", (ops_rooms["ana"], first))
        c.execute("UPDATE tasks SET conversation_id=? WHERE id=?", (ops_rooms["ben"], second))
        old = say(c, "bot:ops", "human:ana", "Which one for the first?", ops_rooms["ana"], kind="ask",
                  refs={"task": first, "questions": QUESTIONS})["id"]
        left = say(c, "bot:ops", "human:ben", "Which one for the second?", ops_rooms["ben"], kind="ask",
                   refs={"task": second, "questions": QUESTIONS})["id"]
    merge(api)
    with store(api).transaction() as c:
        newer = say(c, "bot:ops", "human:ana", "Which one for the first, again?", ops_rooms["ana"], kind="ask",
                    refs={"task": first, "questions": QUESTIONS})["id"]
        assert H.message(c, newer)["conversation_id"] == ops_rooms["home"]
    with store(api).transaction() as c:
        # The write's replacement found the question asked before the move (it is in the task's history) and superseded it.
        assert H.message(c, old)["superseded_at"] is not None
        assert H.supersede_asks(c, second, to_actor="human:ana") == []
        assert H.supersede_asks(c, second) == [left]
        assert H.supersede_asks(c, first) == [newer]
    with store(api).read() as c:
        assert H.message(c, left)["superseded_at"] and H.message(c, newer)["superseded_at"]


def run_once(api):
    """The start-up step, run on the migrated database: its marker is cleared first, as on an install the move has not reached."""
    with store(api).transaction() as c:
        c.execute("DELETE FROM registry_metadata WHERE key=?", (bot_home_merge.KEY,))
        return bot_home_merge.run_once(c)


def bot_file(api, conversation_id, name, text):
    """A file the bot published in a chat. Its identity is the bot and the path (files.series), so one name in two rooms
    is one identity: two scopes until the move puts both in the Home."""
    data = text.encode("utf-8")
    with store(api).transaction() as c:
        digest = api.app.state.blobs.put(data, "text/plain")
        item = register(c, Identity("bot:ops", "bot"), digest, len(data), name, "text/plain")
        row, _, _ = api.app.state.files.add_version(
            c, bot="ops", actor="bot:ops", scope="conversation:" + conversation_id, task=None,
            conversation=conversation_id, attempt="", identity=series("ops", name), title=name, name=name,
            mime="text/plain", blob_id=item["id"], digest=digest, size=len(data))
    return row["id"]


def readable(api, fid):
    """The file as cara, who reads the Home and no old room: its content, else the status."""
    r = api.get("/api/v2/files/" + fid, headers=headers("cara-test"))
    return r.content if r.status_code == 200 else r.status_code


def attachable(api, conversation_id, fid):
    """The file ids a message in this conversation may attach (visuals.attach, as a Home reader posts)."""
    with store(api).transaction() as c:
        out, _ = visuals.attach(c, Identity("human:cara", "human", "cara@acme.example"), api.app.state.files,
                                api.app.state.blobs, [fid + "@1"], conversation_id=conversation_id)
    return [item["id"] for item in out]


def test_a_file_from_an_old_room_reads_and_attaches_in_the_home_after_run_once(api, ops_rooms):
    fid = bot_file(api, ops_rooms["ana"], "plan.md", "# Plan")
    assert readable(api, fid) == 404                               # a file of a private room
    assert run_once(api)["rooms"] == 2
    with store(api).read() as c:
        assert c.execute("SELECT scope FROM bot_files WHERE id=?", (fid,)).fetchone()[0] == "conversation:" + ops_rooms["home"]
    assert readable(api, fid) == b"# Plan"
    listed = api.get("/api/v2/bots/ops/files?limit=100", headers=headers("cara-test")).json()["files"]
    assert fid in {f["id"] for f in listed}
    assert attachable(api, ops_rooms["home"], fid) == [fid]


def test_two_rooms_files_under_one_name_both_stay_readable_in_the_home(api, ops_rooms):
    ana = bot_file(api, ops_rooms["ana"], "notes.md", "# Ana")
    ben = bot_file(api, ops_rooms["ben"], "notes.md", "# Ben")     # one identity: only one can take the Home's scope
    assert run_once(api)["rooms"] == 2
    home = "conversation:" + ops_rooms["home"]
    with store(api).read() as c:
        scopes = dict(c.execute("SELECT id, scope FROM bot_files WHERE id IN (?,?)", (ana, ben)).fetchall())
    taken = [fid for fid, scope in scopes.items() if scope == home]
    assert len(taken) == 1, scopes
    kept = ben if taken == [ana] else ana
    assert scopes[kept] == "conversation:" + (ops_rooms["ben"] if kept == ben else ops_rooms["ana"]), scopes
    assert readable(api, ana) == b"# Ana" and readable(api, ben) == b"# Ben"
    assert attachable(api, ops_rooms["home"], ana) == [ana] and attachable(api, ops_rooms["home"], ben) == [ben]


def test_a_file_the_home_already_has_under_its_name_keeps_its_room_scope_and_stays_readable(api, ops_rooms):
    own = bot_file(api, ops_rooms["home"], "notes.md", "# Home")
    moved = bot_file(api, ops_rooms["ana"], "notes.md", "# Ana")
    assert run_once(api)["rooms"] == 2
    with store(api).read() as c:
        assert c.execute("SELECT scope FROM bot_files WHERE id=?", (moved,)).fetchone()[0] == "conversation:" + ops_rooms["ana"]
    assert readable(api, moved) == b"# Ana" and readable(api, own) == b"# Home"
    assert attachable(api, ops_rooms["home"], moved) == [moved]
