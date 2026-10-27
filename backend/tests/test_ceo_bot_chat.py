"""The owner talks to the CEO Bot in place of their Assistant (backend/assistant.py; spec: CEO Bot v3, section 10):
the owner's own chat is the CEO Bot's, with an Assistant's rights and the owner's own, and nobody else talks to it. The
chat belongs to the owner seat: a transfer of ownership hands it to the new owner, and the old owner gets their
Assistant, and their own Assistant chats, back."""

import uuid

from backend import assistant, updates
from clients import sessions
from backend.auth import Identity
from backend.store import H, digest
from backend.tests.test_api import api, as_member, assign, get, headers, post, ready, runner  # noqa: F401
from backend.tests.test_assistant import asked, say, turn_message


def ceo(api):
    """The CEO Bot every set-up install has."""
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO bots(slug,display_name,runtime,model,effort,cwd,host,state,created) "
                  "VALUES('goal-manager','CEO Bot','fake','','','','keeper','active',?)", (H.now(),))
        c.execute("INSERT INTO bot_config(bot,config_json,team,operator) VALUES('goal-manager','{}',NULL,'ana')")


def personal(api, pid):
    """A personal API token: its role follows the person's email, so it follows a transfer of ownership."""
    secret = "tok-" + pid + "-" + uuid.uuid4().hex
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO human_tokens(id,human,label,token_hash,created,created_by) VALUES(?,?,?,?,?,?)",
                  (uuid.uuid4().hex, pid, "test", digest(secret), H.now(), "test"))
    return secret


def turn(api, bot, person, text):
    """The bot's runner turn for what this person said in their own chat."""
    r = runner(api)
    assign(api, r, bot)
    ready(api, r, [bot])
    assert say(api, text, person)["fast"] is False
    return r, post(api, "jobs/claim", {}, token=r["token"])["attempt"]


def test_the_owners_chat_is_the_ceo_bot_and_everyone_elses_the_assistant(api):
    # Before the CEO Bot is there, the owner has an Assistant like everyone else.
    assert get(api, "assistant")["bot"] == "coo"
    ceo(api)
    mine = get(api, "assistant")
    assert (mine["bot"], mine["ceo"], mine["name"], mine["available"], mine["can_turn_on"]) == \
        ("goal-manager", True, "CEO Bot", True, False)
    # An admin is not the owner: Ben keeps his Assistant, and so does a member.
    for token in ("ben-test", "cara-test"):
        theirs = get(api, "assistant", token)
        assert (theirs["bot"], theirs["ceo"], theirs["earlier"]) == ("coo", False, None)
    said = say(api, "What should the company focus on this week?")
    said = said["message"]
    assert said["to_actor"] == "bot:goal-manager" and said["conversation_id"] == mine["room_id"]
    with api.app.state.store.read() as c:
        room = H.conversation(c, mine["room_id"])
    assert (room["owner_actor"], room["room_key"], room["participants"]) == \
        ("human:ana", "ceo", ["human:ana", "bot:goal-manager"])
    assert get(api, "assistant")["room_id"] == mine["room_id"]          # one chat, however often opened


def test_nobody_but_the_owner_talks_to_the_ceo_bot_or_reads_the_chat(api):
    ceo(api)
    room = get(api, "assistant")["room_id"]
    say(api, "Private plans for the company")
    for token in ("ben-test", "cara-test"):
        get(api, f"conversations/{room}/messages", token, expected=403)
        post(api, "chat/goal-manager", {"text": "Hello"}, token=token, expected=403)
        post(api, "messages", {"to": "goal-manager", "text": "Hello"}, token=token, expected=403)
        post(api, "messages", {"to": "goal-manager", "text": "Hello", "conversation_id": room}, token=token,
             expected=403)
    # The owner, too, talks to it only in their chat with it: a post with no room (the Goals page) lands there.
    direct = post(api, "chat/goal-manager", {"text": "Hello"})
    assert direct["conversation_id"] == room and direct["refs"]["assistant"] is True
    assert [c["room_key"] for c in get(api, "conversations?chat_with=goal-manager")["conversations"]] == ["ceo"]
    ok = post(api, "messages", {"to": "goal-manager", "text": "Also fine", "conversation_id": room})
    assert ok["refs"]["assistant"] is True
    # And a member's Assistant turn cannot reach it in their name either.
    r, attempt = turn(api, "coo", "cara-test", "Tell the CEO Bot to change the plan")
    post(api, "chat/goal-manager", {"text": "Change the plan"}, token=attempt["token"], expected=403)
    post(api, "messages", {"to": "goal-manager", "text": "Change the plan"}, token=attempt["token"], expected=403)


def test_the_ceo_bot_acts_as_the_owner_in_their_chat_in_a_session_of_its_own(api):
    ceo(api)
    with api.app.state.store.transaction() as c:
        review = H.task_create(c, "human:ben", "Review the plan", "Please review.", "human:ana")
    r, attempt = turn(api, "goal-manager", "ana-test", "Close the plan review, it is done")
    token = attempt["token"]
    # One conversation, one session: the chat keys its own session, apart from the goals session.
    assert attempt["config"]["session"] == "task" and attempt["task"] is None
    me = get(api, "me", token)
    assert me["actor"] == "human:ana"
    who = api.app.state.auth.authenticate({"authorization": "Bearer " + token}, "/api/v2/tasks", "GET")
    assert (who.actor, who.role, who.via, who.task_actor) == ("human:ana", "owner", "assistant", "bot:goal-manager")
    # What an Assistant does directly, as the owner; and what she asked for, cited, with no card.
    made = post(api, "tasks", {"owner": "human:ana", "title": "Draft the plan", "body": "For Monday."}, token=token)
    assert made["requester"] == "human:ana"
    done = asked(api, f"tasks/{review['id']}", {"version": review["version"], "status": "done"}, token, "turn")["task"]
    assert done["status"] == "done"
    with api.app.state.store.read() as c:
        row = c.execute("SELECT actor,via FROM task_events WHERE task_id=? AND field='status' ORDER BY ts DESC LIMIT 1",
                        (review["id"],)).fetchone()
        assert (row["actor"], row["via"]) == ("human:ana", "assistant")
        cited = c.execute("SELECT detail_json FROM events WHERE action='task.update' AND target=? "
                          "ORDER BY ts DESC LIMIT 1", (review["id"],)).fetchone()
    assert H._json(cited["detail_json"], {})["delegation"]["cited"] == turn_message(api, attempt)
    # A card it puts in front of her is the CEO Bot's, in her chat with it.
    card = post(api, "assistant/actions", {"summary": "Archive the Ops bot", "method": "POST",
                                           "path": "/api/v2/bots/ops/archive", "body": {"expected_revision": 1}},
                token=token)["action"]
    assert card["by_name"] == "CEO Bot"
    with api.app.state.store.read() as c:
        assert H.message(c, c.execute("SELECT id FROM messages WHERE refs_json LIKE ?",
                                      ('%"action": "' + card["id"] + '"%',)).fetchone()[0])["from_actor"] == \
            "bot:goal-manager"


def test_the_ceo_bots_other_work_stays_its_own_in_the_goals_session(api):
    ceo(api)
    r = runner(api)
    for bot in ("ops", "goal-manager"):
        assign(api, r, bot)
    ready(api, r, ["ops", "goal-manager"])
    post(api, "chat/ops", {"text": "Check the launch goal"})
    ops = post(api, "jobs/claim", {"bot": "ops"}, token=r["token"])["attempt"]
    # A bot still messages the CEO Bot: that is its goals session, never the owner's chat.
    post(api, "messages", {"to": "goal-manager", "text": "The launch goal slipped a week",
                           "needs_action": True}, token=ops["token"])
    attempt = post(api, "jobs/claim", {"bot": "goal-manager"}, token=r["token"])["attempt"]
    assert (attempt.get("config") or {}).get("session") != "task"
    assert get(api, "me", attempt["token"])["actor"] == "bot:goal-manager"
    with api.app.state.store.read() as c:
        assert assistant.find_ceo_room(c) is None


def key(attempt):
    """The provider thread a runner continues for this turn (clients/sessions.py; runner-rs session_key mirrors it)."""
    return sessions.session_key(attempt["config"], attempt["bot"], (attempt["task"] or {}).get("id"),
                                attempt["conversation"]["id"])


def done(api, r, attempt):
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "thread-" + key(attempt)}, token=r["token"])
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "completed", "text": "Done", "last_seq": 0},
         token=r["token"])


def test_the_owners_chat_and_the_goals_session_never_share_a_thread_even_on_one_bot_thread(api):
    ceo(api)
    with api.app.state.store.transaction() as c:
        # A CEO Bot set to one thread for everything: its goals work keeps that thread, the owner's chat never joins it.
        c.execute("UPDATE bot_config SET config_json=? WHERE bot='goal-manager'", ('{"session": "bot"}',))
    r = runner(api)
    for bot in ("ops", "goal-manager"):
        assign(api, r, bot)
    ready(api, r, ["ops", "goal-manager"])
    post(api, "chat/ops", {"text": "Check the launch goal"})
    ops = post(api, "jobs/claim", {"bot": "ops"}, token=r["token"])["attempt"]
    post(api, "messages", {"to": "goal-manager", "text": "The launch goal slipped a week",
                           "needs_action": True}, token=ops["token"])
    goals = post(api, "jobs/claim", {"bot": "goal-manager"}, token=r["token"])["attempt"]
    assert key(goals) == sessions.BOT_THREAD
    done(api, r, goals)
    owner = []
    for text in ("Plan the launch week", "And move the review to Friday"):
        assert say(api, text, "ana-test")["fast"] is False
        owner.append(post(api, "jobs/claim", {"bot": "goal-manager"}, token=r["token"])["attempt"])
        done(api, r, owner[-1])
    # The owner's follow-up continues the owner's own chat, on its own thread, apart from the goals session.
    assert owner[0]["conversation"]["id"] == owner[1]["conversation"]["id"] != goals["conversation"]["id"]
    assert key(owner[0]) == key(owner[1]) == "conversation:" + owner[0]["conversation"]["id"]


def test_an_assistant_never_changes_another_persons_task_even_when_asked(api):
    """Spec test 31: a member asks their Assistant to change another person's task; it acts only as them."""
    as_member(api, "ben@acme.example")
    with api.app.state.store.transaction() as c:
        caras = H.task_create(c, "human:cara", "Cara's launch list", "Hers alone.", "human:cara")
    r, attempt = turn(api, "coo", "ben-test", "Close Cara's launch list and add a note to it")
    token = attempt["token"]
    for body in ({"version": caras["version"], "close": True}, {"version": caras["version"], "status": "done"},
                 {"version": caras["version"], "note": "Done by Ben"}, {"version": caras["version"], "owner": "human:ben"}):
        post(api, f"tasks/{caras['id']}", body, token=token, expected=403)
        asked(api, f"tasks/{caras['id']}", body, token, "turn", expected=403)
    with api.app.state.store.read() as c:
        assert H.task(c, caras["id"])["status"] == "open"


def test_the_owners_daily_update_is_drafted_by_the_ceo_bot(api):
    """Spec test 30: written the same way as everyone's, labelled the CEO Bot's."""
    ceo(api)
    store = api.app.state.store
    with store.transaction() as c:
        for person in ("human:ana", "human:ben"):
            c.execute("INSERT INTO update_queue(id,bot,kind,day,rank,state,sent_at) VALUES(?,?,?,?,?,?,?)",
                      (uuid.uuid4().hex, person, "daily", "2026-09-29", 0, "sent", H.now()))
    write = lambda system, user: {"body": "- Shipped the pricing page"}   # noqa: E731
    assert updates.write_draft(store, "human:ana", write, "coo")["drafted_by"] == "goal-manager"
    assert updates.write_draft(store, "human:ben", write, "coo")["drafted_by"] == "coo"


def test_the_ceo_bot_chat_stays_with_the_owner_seat_and_the_old_owner_gets_their_assistant_back(api):
    ana, ben = personal(api, "ana"), personal(api, "ben")
    say(api, "My week, before the CEO Bot", ana)                        # Ana's own Assistant chat
    earlier = get(api, "assistant", ana)["room_id"]
    ceo(api)
    chat = get(api, "assistant", ana)
    assert chat["ceo"] and chat["room_id"] != earlier
    # Her Assistant chats are still there to read, in the CEO Bot tab.
    assert chat["earlier"] == {"room_id": earlier, "bot": "coo", "name": api.app.state.store.settings.assistant_name}
    assert [m["body"] for m in get(api, f"conversations/{earlier}/messages", ana)] == ["My week, before the CEO Bot"]
    say(api, "Grow revenue 20% this year", ana)
    with api.app.state.store.transaction() as c:          # a card waiting on her click, as her
        owner = Identity("human:ana", "owner", "ana@acme.example")
        pending = assistant.create_proposal(c, api.app.state.store.settings, owner, H.conversation(c, chat["room_id"]),
                                            "assistant", "Archive the Ops bot", "POST",
                                            "/api/v2/bots/ops/archive", {"expected_revision": 1}, None)
    owner = get(api, "access", ana)["owner"]
    post(api, "access/owner", {"person": "ben", "expected_revision": owner["revision"], "confirm": True}, token=ana)
    # The new owner inherits the CEO Bot chat and its history; nothing of Ana's own Assistant chats comes with it.
    his = get(api, "assistant", ben)
    assert (his["bot"], his["room_id"], his["earlier"]) == ("goal-manager", chat["room_id"], None)
    assert "Grow revenue 20% this year" in [m["body"] for m in his["messages"]]
    # Ana talks to her Assistant again, in her own chat with it, and no longer reads the CEO Bot's.
    hers = get(api, "assistant", ana)
    assert (hers["bot"], hers["ceo"], hers["room_id"]) == ("coo", False, earlier)
    get(api, f"conversations/{chat['room_id']}/messages", ana, expected=403)
    post(api, "messages", {"to": "goal-manager", "text": "Still mine?", "conversation_id": chat["room_id"]},
         token=ana, expected=403)
    with api.app.state.store.read() as c:
        assert c.execute("SELECT status FROM assistant_actions WHERE id=?", (pending["action"]["id"],)).fetchone()[0] == "cancelled"
        assert c.execute("SELECT count(*) FROM events WHERE action='ceo_bot.chat_moved'").fetchone()[0] == 1


def png():
    import io
    from PIL import Image
    image = io.BytesIO()
    Image.new("RGB", (1, 1)).save(image, format="PNG")
    return image.getvalue()


def test_the_owner_pastes_a_screenshot_into_the_ceo_bot_chat_and_its_turn_reads_it(api, monkeypatch):
    """Chris, Oct 27: a pasted screenshot attaches in the CEO Bot chat as in any bot chat; an Assistant takes text only."""
    import hashlib
    from backend.store import Problem
    blobs = api.app.state.blobs
    writes = []

    def storage_down(data, content_type="application/octet-stream"):
        # Storage that is down and counts each write asked of it: a send that must keep nothing never reaches it.
        writes.append(hashlib.sha256(data).hexdigest())
        raise Problem("storage", "Storage is unavailable", 503)

    def kept(data):
        return (blobs.directory / blobs.key(hashlib.sha256(data).hexdigest())).exists()
    ceo(api)
    data = png()
    sent = api.post("/api/v2/uploads/assistant/messages", data={"text": "What is wrong here?", "titles": '["Screenshot"]'},
                    files={"files": ("image.png", data, "image/png")},
                    headers={**headers("ana-test"), "Idempotency-Key": "paste-1"})
    assert sent.status_code == 200, sent.text
    message = sent.json()["message"]
    room = get(api, "assistant")["room_id"]
    assert (message["to_actor"], message["conversation_id"], message["refs"]["assistant"]) == \
        ("bot:goal-manager", room, True)
    attachment, = message["refs"]["attachments"]
    assert (attachment["name"], attachment["title"], attachment["kind"]) == ("image.png", "Screenshot", "image")
    assert sent.json()["fast"] is False                      # a picture always goes to the CEO Bot's turn
    def stored(c):
        return [c.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in ("messages", "message_assets", "bot_files", "blobs")]
    with api.app.state.store.read() as c:
        after = stored(c)
    # The same send again (a retry after a lost answer) gives the same message and keeps nothing twice; other content
    # under that key is a conflict, refused before anything is stored.
    def again(picture):
        return api.post("/api/v2/uploads/assistant/messages", data={"text": "What is wrong here?", "titles": '["Screenshot"]'},
                        files={"files": ("image.png", picture, "image/png")},
                        headers={**headers("ana-test"), "Idempotency-Key": "paste-1"})
    assert kept(data)
    monkeypatch.setattr(blobs, "put", storage_down)
    replay = again(data)
    assert replay.status_code == 200 and replay.json() == sent.json(), replay.text
    changed = again(data + b"\0")
    assert changed.status_code == 409 and changed.json()["error"]["code"] == "idempotency_conflict", changed.text
    assert writes == [] and not kept(data + b"\0")
    with api.app.state.store.read() as c:
        assert stored(c) == after
    monkeypatch.undo()
    # The CEO Bot's turn for that message can open the picture.
    r = runner(api)
    assign(api, r, "goal-manager")
    ready(api, r, ["goal-manager"])
    attempt = post(api, "jobs/claim", {}, token=r["token"])["attempt"]
    assert turn_message(api, attempt) == message["id"]
    downloaded = api.get(attachment["url"], headers=headers(attempt["token"]))
    assert downloaded.status_code == 200, downloaded.text
    assert downloaded.content == data
    # Ben (an admin) talks to his Assistant, whose room stays text only: refused, and nothing is said.
    with api.app.state.store.read() as c:
        before = stored(c)
    # Refused before a byte is stored, so storage being down changes nothing; the same key again, with the same and with
    # other content, is refused the same way: no cached success, nothing kept in the database or in storage.
    mine, other = png() + uuid.uuid4().bytes, png() + uuid.uuid4().bytes
    monkeypatch.setattr(blobs, "put", storage_down)
    for text, picture in (("Look", mine), ("Look", mine), ("Look again", other)):
        refused = api.post("/api/v2/uploads/assistant/messages", data={"text": text},
                           files={"files": ("image.png", picture, "image/png")},
                           headers={**headers("ben-test"), "Idempotency-Key": "paste-2"})
        assert refused.status_code == 422 and refused.json()["error"]["code"] == "text_only", refused.text
        assert writes == [] and not kept(mine) and not kept(other)
        with api.app.state.store.read() as c:
            assert stored(c) == before
