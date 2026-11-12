"""One Assistant for everyone (backend/assistant.py, docs/assistant.md): the owner's chat is their Assistant like
everyone else's, and the Strategy Owner, the Librarian and BotOps are helpers it hands cited work to. The owner's earlier
chat with the Strategy Owner (room_key "ceo") is read-only history under "Earlier chats", and the cards waiting in it
move to the owner's Assistant room once."""

import uuid

from backend import assistant, rooms, updates
from clients import sessions
from backend.auth import Identity
from backend.store import H
from backend.tests.helpers.api import as_member, assign, get, headers, post, ready, runner  # noqa: F401
from backend.tests.helpers.assistant import asked, say, turn_message
from backend import bot_settings as BS
from backend.tests.helpers.strategy_bot_chat import personal, strategy_bot, turn


def helper(api, slug, name):
    """Another built-in helper bot (the Librarian), active."""
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO bots(id,slug,display_name,effort,state,created_at) VALUES(?,?,?,'','active',?)",
            (slug, slug, name, H.now()),
        )
        c.execute("INSERT INTO bot_config(bot,team,owner_id) VALUES(?,NULL,'ana')", (slug,))
        BS.write(c, slug, {"runtime": "fake", "host": "keeper"})


def earlier_chat(api, owner="human:ana", said=("Grow revenue 20% this year", "Here is a plan for that.")):
    """The owner's chat with the Strategy Owner from before it became a helper: room_key "ceo", the owner's words and its
    reply."""
    with api.app.state.store.transaction() as c:
        room = H.open_conversation(
            c,
            owner,
            [owner, "bot:strategy-owner"],
            kind="chat",
            subject="Strategy Owner",
            scope=rooms.PERSONAL,
            owner_actor=owner,
            room_key=rooms.STRATEGY_ROOM,
        )
        H._write_message(c, owner, "bot:strategy-owner", said[0], room, "say", {"assistant": True}, None, None)
        H._write_message(
            c, "bot:strategy-owner", owner, said[1], room, "say", {"assistant": True}, None, None, delivered_at=H.now()
        )
        return room["id"]


def test_the_owners_chat_is_the_assistant_like_everyone_elses(api):
    strategy_bot(api)
    name = api.app.state.store.settings.assistant_name
    for token in ("ana-test", "ben-test", "cara-test"):
        chat = get(api, "assistant", token)
        assert (chat["bot"], chat["name"], chat["available"], chat["earlier"]) == ("assistant", name, True, None)
        assert "strategy_bot" not in chat
    said = say(api, "What should the company focus on this week?")["message"]
    mine = get(api, "assistant")
    assert said["recipient_id"] == "bot:assistant" and said["conversation_id"] == mine["room_id"]
    with api.app.state.store.read() as c:
        room = H.conversation(c, mine["room_id"])
    assert (room["owner_id"], room["room_key"], room["participants"]) == (
        "human:ana",
        rooms.ASSISTANT_ROOM,
        ["human:ana", "bot:assistant"],
    )
    # Its turn acts as the owner, through the Assistant, never the Strategy Owner.
    r = runner(api)
    assign(api, r, "assistant")
    ready(api, r, ["assistant"])
    attempt = post(api, "jobs/claim", {}, token=r["token"])["attempt"]
    who = api.app.state.auth.authenticate({"authorization": "Bearer " + attempt["token"]}, "/api/v2/tasks", "GET")
    assert (who.actor, who.role, who.via, who.task_actor) == ("human:ana", "owner", "assistant", "bot:assistant")
    card = post(
        api,
        "assistant/actions",
        {
            "summary": "Archive the Ops bot",
            "method": "POST",
            "path": "/api/v2/bots/ops/archive",
            "body": {"expected_revision": 1},
        },
        token=attempt["token"],
    )["action"]
    assert "by_name" not in card and card["conversation_id"] == mine["room_id"]


def test_nobody_chats_with_the_strategy_bot_a_post_to_it_reaches_their_assistant(api):
    strategy_bot(api)
    for token in ("ana-test", "ben-test", "cara-test"):
        room = get(api, "assistant", token)["room_id"]
        # The Goals page and `tico chat` post with no room: the words go to their Assistant, in their own chat.
        direct = post(api, "chat/strategy-owner", {"text": "Hello"}, token=token)
        assert (direct["recipient_id"], direct["conversation_id"], direct["refs"]["assistant"]) == (
            "bot:assistant",
            room,
            True,
        )
        # Into a room, it is refused: their Assistant hands the Strategy Owner work.
        post(
            api,
            "messages",
            {"to": "strategy-owner", "text": "Hello", "conversation_id": room},
            token=token,
            expected=403,
        )
    # An Assistant's turn cannot chat with it in the person's name either.
    r, attempt = turn(api, "assistant", "cara-test", "Tell the Strategy Owner to change the plan")
    post(api, "chat/strategy-owner", {"text": "Change the plan"}, token=attempt["token"], expected=403)
    post(api, "messages", {"to": "strategy-owner", "text": "Change the plan"}, token=attempt["token"], expected=403)


def test_the_owners_earlier_strategy_bot_chat_is_read_only_history_under_earlier_chats(api):
    strategy_bot(api)
    old = earlier_chat(api)
    mine = get(api, "assistant")
    assert mine["earlier"] == {"room_id": old, "bot": "strategy-owner", "name": "Strategy Owner"}
    assert mine["room_id"] != old
    assert [m["body"] for m in get(api, f"conversations/{old}/messages")] == [
        "Grow revenue 20% this year",
        "Here is a plan for that.",
    ]
    # Read-only: nothing new goes into it, and a new message goes to the Assistant room.
    post(api, "messages", {"to": "strategy-owner", "text": "More", "conversation_id": old}, expected=403)
    post(api, "messages", {"to": "assistant", "text": "More", "conversation_id": old}, expected=403)
    assert say(api, "And now?")["message"]["conversation_id"] == mine["room_id"]
    # Nobody else sees it, an admin included.
    for token in ("ben-test", "cara-test"):
        assert get(api, "assistant", token)["earlier"] is None
        get(api, f"conversations/{old}/messages", token, expected=404)  # unreadable looks missing
    # A turn queued there before is the Strategy Owner's own, never the owner's.
    r = runner(api)
    assign(api, r, "strategy-owner")
    ready(api, r, ["strategy-owner"])
    with api.app.state.store.transaction() as c:
        H.say(
            c,
            "human:ana",
            "bot:strategy-owner",
            "Queued before",
            conversation_id=old,
            kind="say",
            refs={"assistant": True},
        )
    attempt = post(api, "jobs/claim", {"bot": "strategy-owner"}, token=r["token"])["attempt"]
    assert get(api, "me", attempt["token"])["actor"] == "bot:strategy-owner"


def test_cards_waiting_in_the_earlier_chat_move_to_the_assistant_room_once(api):
    strategy_bot(api)
    old = earlier_chat(api)
    settings = api.app.state.store.settings
    with api.app.state.store.transaction() as c:
        owner = Identity("human:ana", "owner", "ana@acme.example")
        waiting = assistant.create_proposal(
            c,
            settings,
            owner,
            H.conversation(c, old),
            "assistant",
            "Archive the Ops bot",
            "POST",
            "/api/v2/bots/ops/archive",
            {"expected_revision": 1},
            None,
        )["action"]
        done = assistant.create_proposal(
            c,
            settings,
            owner,
            H.conversation(c, old),
            "assistant",
            "Archive the Docs bot",
            "POST",
            "/api/v2/bots/docs/archive",
            {"expected_revision": 1},
            None,
        )["action"]
        c.execute("UPDATE assistant_actions SET status='done' WHERE id=?", (done["id"],))
        assert assistant.migrate(c, settings) == 1
        assert assistant.migrate(c, settings) == 0  # every start: once moved, nothing is left to move
    mine = get(api, "assistant")
    with api.app.state.store.read() as c:
        rows = {r["id"]: r["conversation_id"] for r in c.execute("SELECT id, conversation_id FROM assistant_actions")}
        cards = c.execute(
            "SELECT sender_id FROM messages WHERE conversation_id=? AND json_extract(refs,'$.action')=?",
            (mine["room_id"], waiting["id"]),
        ).fetchall()
    assert rows == {waiting["id"]: mine["room_id"], done["id"]: old}
    assert [r["sender_id"] for r in cards] == ["bot:assistant"]
    assert [a["id"] for a in mine["pending"]] == [waiting["id"]]
    assert mine["actions"][waiting["id"]]["status"] == "pending"


def _assistant_asks(api, r, helper_slug, token="ana-test", said="What should we focus on?", cite="turn", expected=200):
    """The person asks their Assistant; its turn files the helper a task citing their words."""
    assert say(api, said, token)
    coo = post(api, "jobs/claim", {"bot": "assistant"}, token=r["token"])["attempt"]
    made = api.post(
        "/api/v2/tasks",
        json={
            "owner": helper_slug,
            "title": "From the Assistant",
            "body": "They asked.",
            **({"request_id": cite} if cite else {}),
        },
        headers=headers(coo["token"]),
    )
    assert made.status_code == expected, made.text
    return made.json(), coo


def test_the_assistant_hands_the_strategy_bot_and_the_librarian_cited_work(api):
    strategy_bot(api)
    helper(api, "librarian", "Librarian")
    r = runner(api)
    for slug in ("assistant", "strategy-owner", "librarian"):
        assign(api, r, slug)
    ready(api, r, ["assistant", "strategy-owner", "librarian"])
    for slug, said in (
        ("strategy-owner", "Is the launch goal on track? Tell me why"),
        ("librarian", "Research our three closest competitors"),
    ):
        task, coo = _assistant_asks(api, r, slug, said=said)
        task = task.get("task", task)
        assert (task["owner_id"], task["requester_id"]) == ("bot:" + slug, "human:ana")
        with api.app.state.store.read() as c:
            asked = H._json(
                c.execute(
                    "SELECT detail FROM events WHERE action='assistant.asked_helper' AND target=?", (task["id"],)
                ).fetchone()["detail"],
                {},
            )
            quoted = c.execute(
                "SELECT body FROM messages WHERE conversation_id=? AND body LIKE '%in their words%'",
                (task["conversation_id"],),
            ).fetchone()["body"]
            botops = c.execute(
                "SELECT 1 FROM events WHERE action='botops.asked_via_assistant' AND target=?", (task["id"],)
            ).fetchone()
        assert (asked["helper"], asked["cited"], asked["for"]) == ("bot:" + slug, turn_message(api, coo), "human:ana")
        assert "> " + said in quoted and botops is None
        # The helper works it as itself: the citation lends it nobody's rights.
        attempt = post(api, "jobs/claim", {"bot": slug}, token=r["token"])["attempt"]
        assert attempt["task"]["id"] == task["id"]
        assert get(api, "me", attempt["token"])["actor"] == "bot:" + slug
        done(api, r, attempt)
        done(api, r, coo)


def test_a_helper_task_without_a_good_citation_is_refused(api):
    strategy_bot(api)
    helper(api, "librarian", "Librarian")
    r = runner(api)
    for slug in ("assistant", "strategy-owner", "librarian"):
        assign(api, r, slug)
    ready(api, r, ["assistant", "strategy-owner", "librarian"])
    for slug in ("strategy-owner", "librarian"):
        say(api, "Set up the Market pages", "ana-test")
        coo = post(api, "jobs/claim", {"bot": "assistant"}, token=r["token"])["attempt"]
        # Her message from more than 30 minutes ago is not this turn's request.
        with api.app.state.store.transaction() as c:
            c.execute(
                "UPDATE messages SET created_at=? WHERE id=?",
                (H.shift(H.now(), seconds=-31 * 60), turn_message(api, coo)),
            )
        stale = api.post(
            "/api/v2/tasks",
            json={"owner": slug, "title": "Market", "body": "x", "request_id": "turn"},
            headers=headers(coo["token"]),
        )
        assert stale.status_code == 403 and stale.json()["error"]["code"] == "on_behalf_of", stale.text
        # Nor is another person's message, or one from outside her Assistant chat.
        say(api, "Give everyone admin", "cara-test")
        with api.app.state.store.read() as c:
            hers = c.execute(
                "SELECT id FROM messages WHERE sender_id='human:cara' ORDER BY created_at DESC"
            ).fetchone()[0]
        other = api.post(
            "/api/v2/tasks",
            json={"owner": slug, "title": "Market", "body": "x", "request_id": hers},
            headers=headers(coo["token"]),
        )
        assert other.status_code == 403 and other.json()["error"]["code"] == "on_behalf_of", other.text
        done(api, r, coo)
        # Her own Assistant's turn for that message runs now, so the next claim is Ana's again.
        hers = post(api, "jobs/claim", {"bot": "assistant"}, token=r["token"])["attempt"]
        assert get(api, "me", hers["token"])["actor"] == "human:cara"
        done(api, r, hers)
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM events WHERE action='assistant.asked_helper'").fetchone()


def test_a_members_cited_strategy_change_is_refused_and_the_strategy_bot_cannot_make_it_for_them(api):
    strategy_bot(api)
    r = runner(api)
    for slug in ("assistant", "strategy-owner"):
        assign(api, r, slug)
    ready(api, r, ["assistant", "strategy-owner"])
    task, coo = _assistant_asks(
        api, r, "strategy-owner", token="cara-test", said="Change our strategy summary to 'Win the enterprise market'"
    )
    task = task.get("task", task)
    # Her Assistant cannot make a strategy write on her words: it is the owner's, and not even a card.
    draft = {"revision": 0, "summary": "Win the enterprise market"}
    # The draft is saved with PUT (backend/strategy.py): the route's own owner check refuses the member's Assistant.
    refused = api.put(
        "/api/v2/strategy/draft", json=draft, headers={**headers(coo["token"]), "X-Tico-On-Behalf-Of": "turn"}
    )
    assert refused.status_code == 403 and refused.json()["error"]["code"] in ("confirm_required", "forbidden"), (
        refused.text
    )
    asked(
        api,
        "assistant/actions",
        {"summary": "Change the strategy", "method": "PUT", "path": "/api/v2/strategy/draft", "body": draft},
        coo["token"],
        "turn",
        expected=422,
    )
    # The Strategy Owner works her task as itself: the owner's strategy stays the owner's to change.
    done(api, r, coo)
    attempt = post(api, "jobs/claim", {"bot": "strategy-owner"}, token=r["token"])["attempt"]
    assert attempt["task"]["id"] == task["id"] and attempt["task"]["requester"] == "human:cara"
    api_put = api.put("/api/v2/strategy/draft", json=draft, headers=headers(attempt["token"]))
    assert api_put.status_code == 403, api_put.text


def test_the_strategy_bots_other_work_stays_its_own_in_the_goals_session(api):
    strategy_bot(api)
    r = runner(api)
    for bot in ("ops", "strategy-owner"):
        assign(api, r, bot)
    ready(api, r, ["ops", "strategy-owner"], capabilities=["threads_v1"])
    post(api, "chat/ops", {"text": "Check the launch goal"})
    ops = post(api, "jobs/claim", {"bot": "ops"}, token=r["token"])["attempt"]
    # A bot still messages the Strategy Owner: that is its goals session, never the owner's chat.
    post(
        api,
        "messages",
        {"to": "strategy-owner", "text": "The launch goal slipped a week", "needs_action": True},
        token=ops["token"],
    )
    attempt = post(api, "jobs/claim", {"bot": "strategy-owner"}, token=r["token"])["attempt"]
    assert (attempt.get("config") or {}).get("session") != "task"
    assert get(api, "me", attempt["token"])["actor"] == "bot:strategy-owner"
    with api.app.state.store.read() as c:
        assert assistant.find_strategy_room(c) is None


def key(attempt):
    """The provider thread a runner continues for this turn (clients/sessions.py; runner-rs session_key mirrors it)."""
    return sessions.session_key(
        attempt["config"], attempt["bot"], (attempt["task"] or {}).get("id"), attempt["conversation"]["id"]
    )


def done(api, r, attempt):
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "thread-" + key(attempt)}, token=r["token"])
    post(
        api,
        f"attempts/{attempt['id']}/complete",
        {"outcome": "completed", "text": "Done", "last_seq": 0},
        token=r["token"],
    )


def test_an_assistant_never_changes_another_persons_task_even_when_asked(api):
    """Spec test 31: a member asks their Assistant to change another person's task; it acts only as them."""
    as_member(api, "ben@acme.example")
    with api.app.state.store.transaction() as c:
        caras = H.task_create(c, "human:cara", "Cara's launch list", "Hers alone.", "human:cara")
    r, attempt = turn(api, "assistant", "ben-test", "Close Cara's launch list and add a note to it")
    token = attempt["token"]
    version = caras["version"]
    for path, body in (
        ("move", {"version": version, "to": "closed", "why": "Ben asked"}),
        ("done", {"version": version}),
        ("", {"version": version, "note": "Done by Ben"}),
        ("owner", {"version": version, "owner": "human:ben"}),
    ):
        path = f"tasks/{caras['id']}" + (f"/{path}" if path else "")
        post(api, path, body, token=token, expected=403)
        asked(api, path, body, token, "turn", expected=403)
    with api.app.state.store.read() as c:
        assert H.task(c, caras["id"])["status"] == "open"


def test_the_earlier_strategy_bot_chat_stays_with_the_owner_seat_and_each_keeps_their_assistant(api):
    ana, ben = personal(api, "ana"), personal(api, "ben")
    strategy_bot(api)
    say(api, "My week", ana)
    hers = get(api, "assistant", ana)["room_id"]
    old = earlier_chat(api)
    with api.app.state.store.transaction() as c:  # a card waiting on her click in it, as her
        owner = Identity("human:ana", "owner", "ana@acme.example")
        pending = assistant.create_proposal(
            c,
            api.app.state.store.settings,
            owner,
            H.conversation(c, old),
            "assistant",
            "Archive the Ops bot",
            "POST",
            "/api/v2/bots/ops/archive",
            {"expected_revision": 1},
            None,
        )
    owner = get(api, "access", ana)["owner"]
    post(api, "access/owner", {"person": "ben", "expected_revision": owner["revision"], "confirm": True}, token=ana)
    # The new owner reads the earlier chat under Earlier chats; his own chat is his Assistant.
    his = get(api, "assistant", ben)
    assert (his["bot"], his["earlier"]["room_id"]) == ("assistant", old) and his["room_id"] not in (old, hers)
    assert "Grow revenue 20% this year" in [m["body"] for m in get(api, f"conversations/{old}/messages", ben)]
    # Ana keeps her own Assistant chat and no longer reads the earlier one.
    mine = get(api, "assistant", ana)
    assert (mine["bot"], mine["room_id"], mine["earlier"]) == ("assistant", hers, None)
    get(api, f"conversations/{old}/messages", ana, expected=404)
    with api.app.state.store.read() as c:
        assert (
            c.execute("SELECT status FROM assistant_actions WHERE id=?", (pending["action"]["id"],)).fetchone()[0]
            == "canceled"
        )
        assert c.execute("SELECT count(*) FROM events WHERE action='strategy_bot.chat_moved'").fetchone()[0] == 1


def png():
    import io
    from PIL import Image

    image = io.BytesIO()
    Image.new("RGB", (1, 1)).save(image, format="PNG")
    return image.getvalue()


def test_a_file_in_an_assistant_chat_lands_on_the_persons_own_message_only(api):
    import json

    strategy_bot(api)
    rooms = {token: get(api, "assistant", token)["room_id"] for token in ("ana-test", "ben-test")}
    sent = {}
    for token in ("ana-test", "ben-test"):
        r = api.post(
            "/api/v2/uploads/assistant/messages",
            data={"text": "Look", "page": json.dumps({"path": "#/tasks", "title": "Tasks"})},
            files={"files": ("image.png", png(), "image/png")},
            headers=headers(token),
        )
        assert r.status_code == 200, r.text
        sent[token] = r.json()
    ana = sent["ana-test"]["message"]
    # In her own room, with the page she asked from; a picture is a turn like any message.
    assert ana["conversation_id"] == rooms["ana-test"] != rooms["ben-test"]
    assert "reply" not in sent["ana-test"]
    with api.app.state.store.read() as c:
        refs = json.loads(c.execute("SELECT refs FROM messages WHERE id=?", (ana["id"],)).fetchone()[0])
    assert refs["page"] == {"path": "#/tasks", "title": "Tasks"} and refs["assistant"] is True
    [picture] = refs["attachments"]
    assert picture["kind"] == "image" and picture["name"] == "image.png"
    # Each reads their own and not the other's, by the file or by its blob, the owner included.
    with api.app.state.store.read() as c:
        bens = json.loads(
            c.execute("SELECT refs FROM messages WHERE id=?", (sent["ben-test"]["message"]["id"],)).fetchone()[0]
        )["attachments"][0]
    for mine, theirs, token in ((picture, bens, "ana-test"), (bens, picture, "ben-test")):
        for url in (f"/api/v2/files/{mine['file_id']}", mine["url"]):
            assert api.get(url, headers=headers(token)).status_code == 200, (token, url)
        for url in (f"/api/v2/files/{theirs['file_id']}", theirs["url"]):
            assert api.get(url, headers=headers(token)).status_code in (403, 404), (token, url)


def test_an_assistant_upload_retried_with_its_key_is_stored_once(api):
    import json

    strategy_bot(api)
    get(api, "assistant")

    def send(text, picture, page="#/tasks"):
        return api.post(
            "/api/v2/uploads/assistant/messages",
            data={"text": text, "page": json.dumps({"path": page, "title": "Tasks"})},
            files={"files": ("image.png", picture, "image/png")},
            headers=headers("ana-test", "paste-1"),
        )

    def stored():
        with api.app.state.store.read() as c:
            return [
                c.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                for table in ("messages", "message_assets", "bot_files", "blobs")
            ]

    picture = png() + uuid.uuid4().bytes
    first = send("Look", picture)
    assert first.status_code == 200, first.text
    after = stored()
    again = send("Look", picture)  # the response was lost: the same request again
    assert again.status_code == 200 and again.json()["message"]["id"] == first.json()["message"]["id"]
    assert stored() == after
    # The same key with other words, another picture or another page is not that request.
    for changed in (
        send("Look again", picture),
        send("Look", png() + uuid.uuid4().bytes),
        send("Look", picture, "#/docs"),
    ):
        assert changed.status_code in (409, 422), changed.text
    assert stored() == after
