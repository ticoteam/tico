"""A person lets bots they choose read their Assistant chat, read only (backend/assistant_share.py, docs/assistant.md
"Private to you"): only the bots on their own list read it, removing one refuses its next read, a chosen bot still
cannot post, react or act on a card there, what it has learned stays out unless they include it, and each read is
audited."""

from backend.store import H
from backend.tests.helpers.api import get, headers, post, put, setup_attempt
from backend.tests.helpers.assistant import assistant_turn, room, say


def bot_token(api, slug):
    """A bot's own run, as it reads through tico."""
    return setup_attempt(api, slug)[2]["token"]


def delete(api, path, token):
    r = api.delete("/api/v2/" + path, headers=headers(token))
    assert r.status_code == 200, r.text
    return r.json()


def reply(api, room_id, text, refs=None):
    """The Assistant's answer in the person's room, as its turn writes it."""
    with api.app.state.store.transaction() as c:
        return H.say(c, "bot:assistant", "human:ben", text, conversation_id=room_id, kind="say", refs=refs or {})


def audited(api, action):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute("SELECT actor, target, detail FROM events WHERE action=?", (action,))]


def test_only_a_bot_the_person_chose_reads_their_chat_and_each_read_is_audited(api):
    finance, ops = bot_token(api, "finance"), bot_token(api, "ops")
    chat = room(api, "ben-test")
    say(api, "Which launch tasks are late?", "ben-test")
    reply(api, chat["room_id"], "Two are late: the press kit and the pricing page.")
    # Nobody is on the list at first: no bot reads it.
    assert get(api, "assistant/viewers", "ben-test") == {"bots": [], "include_learned": False}
    get(api, "assistant/shared/human:ben", finance, expected=403)

    shown = post(api, "assistant/viewers", {"bot": "finance"}, token="ben-test")
    assert [b["slug"] for b in shown["bots"]] == ["finance"] and shown["bots"][0]["last_read_at"] is None
    assert get(api, "assistant/shared", finance)["people"][0]["person"] == "human:ben"
    assert get(api, "assistant/shared", ops)["people"] == []

    read = get(api, "assistant/shared/ben", finance)
    assert [(m["from_assistant"], m["body"]) for m in read["messages"]] == [
        (False, "Which launch tasks are late?"),
        (True, "Two are late: the press kit and the pricing page."),
    ]
    assert read["learned"] is None
    assert get(api, "assistant/viewers", "ben-test")["bots"][0]["last_read_at"]
    events = audited(api, "assistant.shared_read")
    assert [(e["actor"], e["target"]) for e in events] == [("bot:finance", "human:ben")]

    # A bot not chosen, any person, the owner and an admin are all refused, and none of it is audited as a read.
    get(api, "assistant/shared/human:ben", ops, expected=403)
    for person in ("ana-test", "cara-test", "ben-test"):
        get(api, "assistant/shared/human:ben", person, expected=403)
    assert len(audited(api, "assistant.shared_read")) == 1

    # Taking it off the list refuses the very next read.
    delete(api, "assistant/viewers/finance", "ben-test")
    get(api, "assistant/shared/human:ben", finance, expected=403)
    assert get(api, "assistant/shared", finance)["people"] == []
    assert [e["target"] for e in audited(api, "assistant.share_removed")] == ["bot:finance"]


def test_only_the_person_on_their_own_chooses_who_views_their_chat(api):
    ops = bot_token(api, "ops")
    # Not the owner or an admin for someone else: each request only ever changes the caller's own list.
    post(api, "assistant/viewers", {"bot": "finance"}, token="ana-test")
    assert get(api, "assistant/viewers", "ben-test")["bots"] == []
    # Not a bot, and not the person's own Assistant turn acting for them.
    post(api, "assistant/viewers", {"bot": "ops"}, token=ops, expected=403)
    _, attempt = assistant_turn(api, "ben-test")
    post(api, "assistant/viewers", {"bot": "finance"}, token=attempt["token"], expected=403)
    put(api, "assistant/viewers/learned", {"include_learned": True}, token=attempt["token"], expected=403)
    assert get(api, "assistant/viewers", "ben-test") == {"bots": [], "include_learned": False}
    # The Assistant itself and a bot that is not there cannot be added.
    post(api, "assistant/viewers", {"bot": "assistant"}, token="ben-test", expected=422)
    post(api, "assistant/viewers", {"bot": "nobody"}, token="ben-test", expected=404)


def test_a_chosen_bot_cannot_post_react_or_act_on_a_card_in_the_chat(api):
    _, attempt = assistant_turn(api, "ana-test")
    assert api.put("/api/v2/access/rules", json={"assistant_direct": False}, headers=headers()).status_code == 200
    with api.app.state.store.read() as c:
        revision = c.execute("SELECT revision FROM bot_config WHERE bot='ops'").fetchone()[0]
    card = post(
        api,
        "assistant/actions",
        {
            "summary": "Archive the Ops bot",
            "method": "POST",
            "path": "/api/v2/bots/ops/archive",
            "body": {"expected_revision": revision},
        },
        token=attempt["token"],
    )["action"]
    finance = bot_token(api, "finance")
    post(api, "assistant/viewers", {"bot": "finance"}, token="ana-test")
    chat = room(api, "ana-test")
    read = get(api, "assistant/shared/human:ana", finance)
    carded = [m for m in read["messages"] if m["card"]]
    assert carded and read["cards"][card["id"]]["summary"] == "Archive the Ops bot"
    assert "body" not in read["cards"][card["id"]] and "result" not in read["cards"][card["id"]]

    post(
        api,
        "messages",
        {"to": "assistant", "text": "Injected", "conversation_id": chat["room_id"]},
        token=finance,
        expected=403,
    )
    target = carded[0]["id"]
    r = api.put(f"/api/v2/messages/{target}/reactions/👍", headers=headers(finance))
    assert r.status_code in (403, 404), r.text
    for verb in ("confirm", "cancel"):
        r = api.post(f"/api/v2/assistant/actions/{card['id']}/{verb}", json={}, headers=headers(finance))
        assert r.status_code in (403, 404), r.text
    get(api, f"conversations/{chat['room_id']}/messages", finance, expected=404)
    with api.app.state.store.read() as c:
        assert H.bot(c, "ops")["state"] != "archived"
        senders = {
            r[0] for r in c.execute("SELECT sender_id FROM messages WHERE conversation_id=?", (chat["room_id"],))
        }
    assert "bot:finance" not in senders


def test_what_it_has_learned_is_shared_only_when_the_person_includes_it(api):
    finance = bot_token(api, "finance")
    post(api, "assistant/learned", {"text": "Launches matter more than hiring news"}, token="ben-test")
    post(api, "assistant/viewers", {"bot": "finance"}, token="ben-test")
    assert get(api, "assistant/shared/human:ben", finance)["learned"] is None
    shown = put(api, "assistant/viewers/learned", {"include_learned": True}, token="ben-test")
    assert shown["include_learned"] is True
    learned = get(api, "assistant/shared/human:ben", finance)["learned"]
    assert [e["text"] for e in learned] == ["Launches matter more than hiring news"]
    put(api, "assistant/viewers/learned", {"include_learned": False}, token="ben-test")
    assert get(api, "assistant/shared/human:ben", finance)["learned"] is None


def test_a_personal_token_cannot_choose_who_views_the_chat(api):
    """A token may be in an outside assistant's hands: the choice is the person's own, in Tico."""
    token = post(api, "me/tokens", {"label": "outside assistant"}, "ben-test")["token"]
    post(api, "assistant/viewers", {"bot": "finance"}, token=token, expected=403)
    put(api, "assistant/viewers/learned", {"include_learned": True}, token=token, expected=403)
    assert get(api, "assistant/viewers", "ben-test") == {"bots": [], "include_learned": False}


def test_a_person_whose_sign_in_is_off_shares_nothing_more(api):
    finance = bot_token(api, "finance")
    post(api, "assistant/viewers", {"bot": "finance"}, token="cara-test")
    get(api, "assistant/shared/human:cara", finance)
    post(api, "access/people/cara", {"sign_in": False})
    get(api, "assistant/shared/human:cara", finance, expected=403)


def test_removing_the_last_bot_turns_learned_sharing_back_off(api):
    post(api, "assistant/viewers", {"bot": "finance"}, token="ben-test")
    put(api, "assistant/viewers/learned", {"include_learned": True}, token="ben-test")
    assert delete(api, "assistant/viewers/finance", "ben-test") == {"bots": [], "include_learned": False}
    finance = bot_token(api, "finance")
    post(api, "assistant/learned", {"text": "Launches first"}, token="ben-test")
    post(api, "assistant/viewers", {"bot": "finance"}, token="ben-test")
    assert get(api, "assistant/shared/human:ben", finance)["learned"] is None
