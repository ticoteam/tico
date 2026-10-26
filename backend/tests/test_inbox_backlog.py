"""A bot's unread backlog (GET /api/v2/messages): it is paged newest first through its own index, each message once,
and a backlog in rooms the turn was never handed is not walked message by message."""
import uuid

from backend import task_privacy
from backend.store import H
from backend.tests.test_api import api, headers, post, setup_attempt  # noqa: F401
from backend.tests.test_payload_budgets import clone


def test_the_unread_page_reads_its_index_without_a_sort_or_a_table_scan(api):
    with api.app.state.store.read() as c:
        plan = " ".join(row[3] for row in c.execute("EXPLAIN QUERY PLAN " + H.UNREAD_PAGE_SQL,
                                                     ("bot:ops", H.now(), "￿", "￿", 1 << 62, 100)))
    assert "USING INDEX messages_unread_to" in plan and "TEMP B-TREE" not in plan and "SCAN" not in plan, plan


def test_pages_return_every_waiting_message_once_through_ties(api):
    """Messages sent in the same instant straddle the page edges; read, deleted, expired and other actors' messages
    are never paged."""
    store = api.app.state.store
    msg = post(api, "chat/ops", {"text": "Hello"})
    with store.transaction() as c:
        first = dict(c.execute("SELECT * FROM messages WHERE id=?", (msg["id"],)).fetchone())
        base = {**first, "from_actor": "human:ana", "to_actor": "bot:ops", "read_at": None, "delivered_at": None}
        c.execute("UPDATE messages SET read_at=?, delivered_at=? WHERE to_actor='bot:ops'", (H.now(), H.now()))
        # 40 waiting messages in 8 instants of 5 each, then the ones that are not waiting.
        clone(c, "messages", base, 40, created=lambda i: H.shift(first["created"], seconds=-(i // 5)),
              delivered_at=lambda i: H.now() if i % 2 else None, body=lambda i: f"waiting {i}")
        clone(c, "messages", base, 3, read_at=lambda i: H.now(), delivered_at=lambda i: H.now(), body=lambda i: "read")
        clone(c, "messages", base, 3, deleted_at=lambda i: H.now(), body=lambda i: "deleted")
        clone(c, "messages", base, 3, expires_at=lambda i: H.shift(H.now(), seconds=-60), body=lambda i: "expired")
        clone(c, "messages", base, 3, to_actor=lambda i: "bot:coo", body=lambda i: "someone else's")
    with store.read() as c:
        expected = [r["id"] for r in c.execute(
            "SELECT id FROM messages WHERE to_actor='bot:ops' AND body LIKE 'waiting %' ORDER BY created DESC, rowid DESC")]
        for size in (1, 3, 5, 7, 40, 41, 500):
            pages = list(H.inbox_pages(c, "bot:ops", size=size))
            assert all(0 < len(p) <= size for p in pages)
            assert [m["id"] for p in pages for m in p] == expected, size
            assert all("_seq" not in m and isinstance(m["refs"], dict) for p in pages for m in p)
        assert [m["id"] for m in H.inbox(c, "bot:ops")["messages"]] == expected[::-1]


def test_a_backlog_in_rooms_the_turn_was_not_handed_is_not_walked(api, monkeypatch):
    """A runner turn reads its inbox: 300 notices wait in 60 rooms it was never handed, older than the turn's own
    message. Each hidden room is refused once; no hidden message gets the privacy walk, and the turn's message is
    found under them."""
    store = api.app.state.store
    _, msg, attempt = setup_attempt(api)
    with store.transaction() as c:
        conv = dict(c.execute("SELECT * FROM conversations WHERE id=?", (msg["conversation_id"],)).fetchone())
        rooms = [uuid.uuid4().hex for _ in range(60)]
        clone(c, "conversations", conv, 60, id=lambda i: rooms[i], room_key=lambda i: "room-" + rooms[i])
        first = dict(c.execute("SELECT * FROM messages WHERE id=?", (msg["id"],)).fetchone())
        clone(c, "messages", {**first, "from_actor": "keeper", "to_actor": "bot:ops", "kind": "notice",
                              "delivered_at": H.now(), "read_at": None}, 300,
              created=lambda i: H.shift(first["created"], seconds=i + 1), conversation_id=lambda i: rooms[i % 60],
              body=lambda i: f"notice {i}")
        c.execute("UPDATE messages SET read_at=NULL WHERE id=?", (msg["id"],))
    walked = []
    readable = task_privacy.message_readable

    def counting(c, actor, message, current=False):
        walked.append(message["id"])
        return readable(c, actor, message, current)
    monkeypatch.setattr(task_privacy, "message_readable", counting)
    inbox = api.get("/api/v2/messages?unread=1", headers=headers(attempt["token"])).json()
    assert [m["id"] for m in inbox["messages"]] == [msg["id"]] and inbox["more_messages"] is False
    assert walked == [msg["id"]], len(walked)        # before: every one of the 301 messages


def test_the_newest_hundred_readable_arrive_oldest_first_with_their_answers(api):
    """What the inbox sends is unchanged: the newest INBOX_LIMIT readable messages, oldest first, `more_messages` when
    one more readable message waits, and an answered ask says when it was answered."""
    from backend.app import INBOX_LIMIT
    store = api.app.state.store
    msg = post(api, "chat/ops", {"text": "Hello"})
    with store.transaction() as c:
        first = dict(c.execute("SELECT * FROM messages WHERE id=?", (msg["id"],)).fetchone())
        base = {**first, "from_actor": "bot:ops", "to_actor": "human:ana", "read_at": None}
        clone(c, "messages", base, INBOX_LIMIT + 50, created=lambda i: H.shift(first["created"], seconds=-i - 1),
              kind=lambda i: "ask" if i == 0 else "say", body=lambda i: f"reply {i}")
        ask = c.execute("SELECT id FROM messages WHERE kind='ask' AND to_actor='human:ana'").fetchone()["id"]
        clone(c, "messages", {**first, "from_actor": "human:ana", "to_actor": "bot:ops", "kind": "answer",
                              "in_reply_to": ask}, 1, body=lambda i: "yes")
        newest = [r["id"] for r in c.execute("SELECT id FROM messages WHERE to_actor='human:ana' AND read_at IS NULL "
                                             "AND deleted_at IS NULL ORDER BY created DESC, rowid DESC")][:INBOX_LIMIT]
    inbox = api.get("/api/v2/messages?unread=1", headers=headers()).json()
    assert [m["id"] for m in inbox["messages"]] == newest[::-1] and inbox["more_messages"] is True
    answered = {m["id"]: m["answered_at"] for m in inbox["messages"]}
    assert answered[ask] and all(at is None for mid, at in answered.items() if mid != ask)
