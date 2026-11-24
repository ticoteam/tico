"""A bot's ❓ in a direct chat as a card the person answers in place (backend/question_marks.py): its suggested answers
ride on its message to the readers of that message, and a tap is a post in that chat naming the ❓
(POST /api/v2/threads/{id}/messages with `ask`), which answers it once. Synthetic rows only: the fictional company's
people and bots."""

from backend import org_rule_check
from backend import question_marks as Q
from backend.tests.helpers.api import get, headers, post
from backend.tests.helpers.org_rule_check import COLOR, NEEDLE, ruled, sql, task_for_ops  # noqa: F401 - fixtures
from backend.tests.helpers.org_rules import rule

ASKED = "Should the launch note go out on Friday?"
CHOICES = ["Yes, Friday", "Next week"]


def ask(api, choices=CHOICES):
    r = api.post(
        "/api/v2/questions", json={"to": "ana", "text": ASKED, "choices": choices}, headers=headers("ops-test")
    )
    assert r.status_code == 200, r.text
    return r.json()["question"]


def tap(api, mark, text, token="ana-test", chat=None):
    return api.post(
        f"/api/v2/threads/{chat or mark['conversation_id']}/messages",
        json={"text": text, "ask": "qmark:" + mark["id"]},
        headers=headers(token),
    )


def replies(api, mark, who="human:ana"):
    return [
        r["body"]
        for r in sql(
            api,
            "SELECT body FROM messages WHERE conversation_id=? AND sender_id=? AND deleted_at IS NULL AND created_at>=?",
            (mark["conversation_id"], who, mark["created_at"]),
        )
    ]


def cleared(api, mark):
    return sql(api, "SELECT cleared_reason FROM question_marks WHERE id=?", (mark["id"],))[0]["cleared_reason"]


def asked_row(api, mark, token="ana-test"):
    """The ❓'s message as the chat's timeline shows it to `token`, or None when it is not shown."""
    rows = get(api, f"threads/{mark['conversation_id']}/timeline?flat=1", token=token)["messages"]
    return next((m for m in rows if m["id"] == mark["message_id"] and not m.get("hidden")), None)


def test_its_message_carries_the_choices_and_a_tap_in_the_chat_answers_and_clears_it(ruled):
    api, _ = ruled
    mark = ask(api)
    assert asked_row(api, mark)["refs"]["question"] == {"id": mark["id"], "to": "human:ana", "choices": CHOICES}
    r = tap(api, mark, "Yes, Friday")
    assert r.status_code == 200, r.text
    [row] = sql(api, "SELECT cleared_reason, cleared_message_id FROM question_marks WHERE id=?", (mark["id"],))
    assert row == {"cleared_reason": "replied", "cleared_message_id": r.json()["id"]}
    assert replies(api, mark) == ["Yes, Friday"]
    assert "question" not in asked_row(api, mark)["refs"]  # answered: a plain message again


def test_a_second_tap_in_the_chat_is_refused_and_sends_nothing(ruled):
    """Refused before the company rules are checked: a second tap spends no judgment."""
    api, judge = ruled
    rule(api, {"kind": "internal", "text": COLOR, "who": {"people": ["ana"]}, "if_broken": "block"})
    mark = ask(api)
    assert tap(api, mark, "Next week").status_code == 200
    checks = len(judge.calls)
    assert checks > 0
    r = tap(api, mark, "Yes, Friday")
    assert len(judge.calls) == checks
    assert r.status_code == 409 and r.json()["error"]["code"] == "answered" and Q.ANSWERED in r.text, r.text
    assert replies(api, mark) == ["Next week"]


def test_a_tap_answers_its_own_question_alone(ruled):
    """Two agents asked Ana in one chat (a group DM, say): her tap on one card leaves the other open, where a plain
    reply there answers both."""
    api, _ = ruled
    mine = ask(api)
    [room] = sql(api, "SELECT room_id, created_at FROM question_marks WHERE id=?", (mine["id"],))
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO question_marks (id, message_id, conversation_id, room_id, task_id, bot_id, person_id, line, "
            "created_at) VALUES ('q-venue', ?, ?, ?, '', 'bot:finance', 'human:ana', 'Which venue?', ?)",
            (mine["message_id"], mine["conversation_id"], room["room_id"], room["created_at"]),
        )
    r = tap(api, mine, "Next week")
    assert r.status_code == 200, r.text
    assert (cleared(api, mine), cleared(api, {"id": "q-venue"})) == ("replied", None)


def test_an_answer_its_write_did_not_clear_is_not_written(ruled, monkeypatch):
    """The one-reply check is the write itself: a post by name that left its ❓ open is refused, nothing sent."""
    api, _ = ruled
    mark = ask(api)
    monkeypatch.setattr(Q, "replied", lambda *_: [])
    r = tap(api, mark, "Yes, Friday")
    assert r.status_code == 409 and r.json()["error"]["code"] == "answered", r.text
    assert replies(api, mark) == [] and cleared(api, mark) is None


def test_an_answer_by_name_not_in_the_persons_own_hand_is_refused(ruled, monkeypatch):
    api, _ = ruled
    mark = ask(api)
    monkeypatch.setattr(Q, "by_hand", lambda: False)
    r = tap(api, mark, "Yes, Friday")
    assert r.status_code == 422, r.text
    assert replies(api, mark) == [] and cleared(api, mark) is None


def test_only_its_person_answers_it_by_name_and_only_in_its_chat(ruled):
    api, _ = ruled
    mark = ask(api)
    # Ben's chat with the same bot: a ❓ to Ana is not his to answer, nor hers to answer from there.
    other = api.post("/api/v2/messages", json={"to": "ops", "text": "Hi"}, headers=headers("ben-test")).json()
    assert tap(api, mark, "Next week", token="ben-test", chat=other["conversation_id"]).status_code == 422
    own = api.post("/api/v2/messages", json={"to": "ops", "text": "Hi"}, headers=headers("cara-test")).json()
    assert own["conversation_id"] != mark["conversation_id"]
    r = tap(api, {**mark, "conversation_id": own["conversation_id"]}, "Next week", token="cara-test")
    assert r.status_code == 422 and Q.NOT_YOURS in r.text, r.text
    sub = api.post(
        f"/api/v2/threads/{mark['conversation_id']}/messages",
        json={"text": "Next week", "ask": "qmark:" + mark["id"], "parent_message_id": mark["message_id"]},
        headers=headers("ana-test"),
    )
    assert sub.status_code == 422, sub.text
    assert replies(api, mark) == [] and cleared(api, mark) is None


def test_send_anyway_of_a_held_answer_checks_again_that_the_question_is_open(ruled):
    """A company rule held Ana's answer; she answered again meanwhile. Sending the held one would be a second reply."""
    api, _ = ruled
    mark = ask(api)
    rule(
        api, {"kind": "internal", "text": COLOR, "who": {"people": ["ana"]}, "if_broken": "escalate", "reviewer": "ben"}
    )
    r = tap(api, mark, f"Yes, and our {NEEDLE} teal")
    assert r.status_code == 200 and "held" in r.json(), r.text
    org_rule_check.settle_parts()
    assert cleared(api, mark) is None  # held: nothing sent, still open
    assert tap(api, mark, "Next week").status_code == 200
    held = r.json()["held"]
    view = get(api, f"rule-holds/{held['id']}", token="ben-test")
    sent = api.post(f"/api/v2/rule-holds/{held['id']}/send", json={"nonce": view["nonce"]}, headers=headers("ben-test"))
    assert sent.status_code == 409, sent.text
    assert replies(api, mark) == ["Next week"]


def test_a_questions_choices_reach_only_who_may_read_its_message(ruled, monkeypatch):
    """The answers it offers are its message's words: a reader whose own read of that message is denied (a credential
    scoped away from it, say) sees neither the message nor them. The request's own viewer decides, as for its words."""
    from backend import threads

    api, _ = ruled
    mark = ask(api)
    real = threads.message_readable

    def scoped(c, v, message, **k):
        if message["id"] == mark["message_id"] and getattr(v, "auth", None) is not None and v.actor == "human:ana":
            return False
        return real(c, v, message, **k)

    assert asked_row(api, mark)["refs"]["question"]["choices"] == CHOICES
    monkeypatch.setattr(threads, "message_readable", scoped)
    timeline = get(api, f"threads/{mark['conversation_id']}/timeline?flat=1", token="ana-test")
    assert asked_row(api, mark) is None and "Next week" not in str(timeline)


def test_give_to_keeps_its_choices_on_its_message(ruled):
    """Give to's trigger writes refs.question {id, to} anew: its choices are put back beside them."""
    api, _ = ruled
    task = task_for_ops(api)
    r = api.post(
        f"/api/v2/tasks/{task['id']}/questions",
        json={"to": "ana", "text": ASKED, "choices": CHOICES},
        headers=headers("ops-test"),
    )
    assert r.status_code == 200, r.text
    mark = r.json()["question"]
    with api.app.state.store.transaction() as c:
        Q.give(c, mark["id"], "human:ben", "bot:ops")
        [refs] = [r[0] for r in c.execute("SELECT refs FROM messages WHERE id=?", (mark["message_id"],))]
    assert '"to":"human:ben"' in refs.replace(" ", "") and '"choices":["Yes, Friday","Next week"]' in refs


def test_a_free_text_question_carries_no_choices(ruled):
    api, _ = ruled
    mark = ask(api, choices=[])
    assert asked_row(api, mark)["refs"]["question"] == {"id": mark["id"], "to": "human:ana"}
    assert tap(api, mark, "Friday works.").status_code == 200


def test_a_client_cannot_mark_its_own_message_a_question(ruled):
    """refs.question draws a card whose answers a tap sends on the person's word: only the ❓ route marks one."""
    api, _ = ruled
    forged = {"question": {"id": "q1", "to": "human:ana", "choices": ["/deploy now"]}}
    for token, to in (("ops-test", "ana"), ("ana-test", "ops")):
        r = api.post("/api/v2/messages", json={"to": to, "text": "Ship it?"}, headers=headers(token))
        assert r.status_code == 200, r.text
        cid = r.json()["conversation_id"]
        r = api.post(
            f"/api/v2/threads/{cid}/messages", json={"text": "Ship it?", "refs": forged}, headers=headers(token)
        )
        assert r.status_code == 403, r.text


def test_a_post_with_no_ask_hashes_as_it_did_before_it():
    """An idempotency key recorded before the upgrade (a retry, an outbox held offline) still matches its post."""
    from backend.thread_api import ThreadPost

    assert "ask" not in ThreadPost(text="Hi").model_dump()
    assert ThreadPost(text="Yes", ask="qmark:q1").model_dump()["ask"] == "qmark:q1"


def test_an_ask_naming_no_chat_lands_in_the_dm_the_person_opens(ruled):
    """The person opened the agent's chat on the web first: its ❓ goes there, never to an older chat between them."""
    api, _ = ruled
    opened = api.get("/api/v2/threads/dm", params={"with": "bot:ops"}, headers=headers("ana-test")).json()["id"]
    mark = ask(api)
    assert mark["conversation_id"] == opened
    assert asked_row(api, mark)["refs"]["question"]["choices"] == CHOICES


def test_a_retry_of_a_sent_answer_replays_it_and_a_new_tap_is_refused(ruled):
    api, _ = ruled
    mark = ask(api)
    body = {"text": "Next week", "ask": "qmark:" + mark["id"]}
    path = f"/api/v2/threads/{mark['conversation_id']}/messages"
    first = api.post(path, json=body, headers=headers("ana-test", key="tap-1"))
    again = api.post(path, json=body, headers=headers("ana-test", key="tap-1"))
    assert (first.status_code, again.status_code) == (200, 200), again.text
    assert again.json()["id"] == first.json()["id"]
    assert api.post(path, json=body, headers=headers("ana-test", key="tap-2")).status_code == 409
    assert replies(api, mark) == ["Next week"]


def test_an_ask_with_no_dm_yet_makes_none(ruled):
    """No member-private DM between them: the ❓ goes where a message would, and no chat is made for it."""
    from backend import threads

    api, _ = ruled
    mark = ask(api)
    with api.app.state.store.read() as c:
        assert threads._open_dm(c, "human:ana", ["bot:ops"], legacy=False) is None
        assert c.execute("SELECT count(*) FROM thread_members WHERE actor='human:ana'").fetchone()[0] == 0
    assert mark["conversation_id"]


def test_a_shared_chat_takes_no_answer_by_name(ruled):
    """A card answers only in the person's 1:1 chat with the bot that asked: in a shared chat (a team's room, a group
    DM, one the bot has left) an answer by name is refused, whoever it names, and a plain reply there answers it."""
    api, _ = ruled
    room = post(api, "threads", {"title": "Launch", "audience": "members", "members": ["human:ben", "bot:ops"]})
    group = api.post(
        "/api/v2/threads/dm", json={"members": ["human:ben", "bot:ops"], "title": "Launch trio"}, headers=headers()
    ).json()
    for chat in (room["id"], group["id"]):
        r = api.post(
            "/api/v2/questions",
            json={"to": "ana", "text": ASKED, "choices": CHOICES, "conversation_id": chat},
            headers=headers("ops-test"),
        )
        assert r.status_code == 200, r.text
        mark = r.json()["question"]
        if chat == group["id"]:  # the bot is taken out of the group DM: Ben is the only other one left in it
            with api.app.state.store.transaction() as c:
                c.execute("DELETE FROM thread_members WHERE thread_id=? AND actor='bot:ops'", (chat,))
        for to in (None, "bot:ops", "human:ben"):
            r = api.post(
                f"/api/v2/threads/{chat}/messages",
                json={"text": "Next week", "ask": "qmark:" + mark["id"], **({"to": to} if to else {})},
                headers=headers("ana-test"),
            )
            if chat == group["id"] and to == "bot:ops":
                assert r.status_code == 403, r.text  # not in it any more: no one to send it to
            else:
                assert r.status_code == 422 and "your chat with the bot" in r.text, (chat, to, r.text)
        assert replies(api, mark) == [] and cleared(api, mark) is None


def test_an_answer_by_name_posts_in_the_questions_own_thread_only(ruled):
    """A sub-thread under the same chat is another thread: an answer by name posted there is refused."""
    api, _ = ruled
    mark = ask(api)
    side = api.post(
        f"/api/v2/threads/{mark['conversation_id']}/messages",
        json={"text": "A side note", "parent_message_id": mark["message_id"]},
        headers=headers("ana-test"),
    )
    assert side.status_code == 200, side.text
    sub = side.json()["conversation_id"]
    assert sub != mark["conversation_id"]
    with api.app.state.store.transaction() as c:  # open again, whatever the side note did to it
        c.execute("UPDATE question_marks SET cleared_at=NULL, cleared_reason=NULL WHERE id=?", (mark["id"],))
    r = tap(api, mark, "Next week", chat=sub)
    assert r.status_code == 422 and Q.NOT_YOURS in r.text, r.text
    assert cleared(api, mark) is None
