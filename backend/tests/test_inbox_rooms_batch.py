"""A reader's unread list checks the rooms of a page of unread messages together (P04): the rooms, their meeting
provenance and their tasks are read in a fixed number of statements per page, and each room is still decided as
before. The answer is the same as when every room is read one at a time, for every reader."""

import re
import uuid

from backend import task_privacy
from backend.store import H
from backend.tests.helpers.api import headers, post, setup_attempt
from backend.tests.helpers.meeting_search import traced
from backend.tests.helpers.payload_budgets import clone

URL = "/api/v2/messages?unread=1"


def _one_by_one(monkeypatch):
    """The path before P04: each room's task read on its own, with its tags, and no meeting provenance primed."""
    monkeypatch.setattr(task_privacy, "prime_rooms", lambda c, cids: H.prime_conversations(c, cids))
    monkeypatch.setattr(H, "task_untagged", H.task)


def _seed(api):
    """Unread messages for the bot's turn, Ana and Ben in: open task rooms the bot owns, a private task the bot is a
    party to, a private task between people only, a sub-thread of a task room, a live meeting's room, a DM, rooms the
    turn was never handed, and deleted and expired messages; 130 readable ones in one room, past the 100 sent."""
    store = api.app.state.store
    _, msg, attempt = setup_attempt(api)
    task = post(api, "tasks", {"owner": "ops", "title": "Open", "body": "Seed room."})
    task_id = task["id"] if "id" in task else task["task"]["id"]
    with store.transaction() as c:
        row = dict(c.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())
        room = dict(c.execute("SELECT * FROM conversations WHERE task_id=? LIMIT 1", (task_id,)).fetchone())
        chat = dict(c.execute("SELECT * FROM conversations WHERE id=?", (msg["conversation_id"],)).fetchone())
        first = dict(c.execute("SELECT * FROM messages WHERE id=?", (msg["id"],)).fetchone())
        new = {**row, "pipeline_id": None, "stage_id": None, "status": "open"}
        kinds = {
            "mine": dict(new, private=0),
            "mine-private": dict(new, private=1),
            "people-private": dict(new, private=1, owner_id="human:ben", requester_id="human:ben", assignee_id=None),
        }
        tasks = {}
        for kind, base in kinds.items():
            tasks[kind] = [uuid.uuid4().hex for _ in range(4)]
            clone(c, "tasks", base, 4, id=lambda i, k=kind: tasks[k][i], number=lambda i: None)
        rooms = {}

        def add(kind, base, n, **changes):
            ids = [uuid.uuid4().hex for _ in range(n)]
            rooms[kind] = ids
            clone(c, "conversations", base, n, id=lambda i: ids[i], room_key=lambda i: None, **changes)

        for kind in kinds:
            add(kind, room, 4, task_id=lambda i, k=kind: tasks[k][i], root_id=lambda i: None, path=lambda i: None)
        add("sub-thread", room, 2, root_id=lambda i: rooms["mine"][0], task_id=lambda i: None)
        add("meeting", chat, 2, audience=lambda i: "meeting", task_id=lambda i: None)
        c.execute(
            "UPDATE conversations SET room_key='live-meeting:' || id, root_id=NULL, path=NULL WHERE id IN (?,?)",
            rooms["meeting"],
        )
        add("never-handed", chat, 6, root_id=lambda i: None, path=lambda i: None, task_id=lambda i: None)
        from backend import threads

        dm = threads.post(c, "human:ana", None, "Hello there", start={"dm": ["bot:ops"], "title": ""})
        rooms["dm"] = [dm.get("thread_id") or dm.get("conversation_id")]
        waiting = [cid for kind in rooms for cid in rooms[kind]] + [msg["conversation_id"]]
        base = {**first, "sender_id": "keeper", "delivered_at": H.now(), "read_at": None, "wake_key": None}
        at = iter(range(1, 10_000))
        for recipient in ("bot:ops", "human:ana", "human:ben"):
            n = len(waiting) * 3
            clone(
                c,
                "messages",
                {**base, "recipient_id": recipient},
                n,
                conversation_id=lambda i: waiting[i % len(waiting)],
                task_id=lambda i: None,
                kind=lambda i: "notice" if i % 2 else first["kind"],
                created_at=lambda i: H.shift(first["created_at"], seconds=next(at)),
                body=lambda i: f"waiting {i}",
            )
            for gone in ("deleted_at", "expires_at"):
                clone(
                    c,
                    "messages",
                    {**base, "recipient_id": recipient},
                    3,
                    conversation_id=lambda i: rooms["mine"][i],
                    created_at=lambda i: H.shift(first["created_at"], seconds=next(at)),
                    body=lambda i, g=gone: g,
                    **{gone: lambda i: H.shift(H.now(), seconds=-60)},
                )
            clone(
                c,
                "messages",
                {**base, "recipient_id": recipient},
                130,
                conversation_id=lambda i: rooms["mine"][1],
                created_at=lambda i: H.shift(first["created_at"], seconds=-(i + 1)),
                body=lambda i: f"older {i}",
            )
        c.execute("UPDATE messages SET read_at=NULL WHERE id=?", (msg["id"],))
    return attempt["token"], rooms


def test_batched_room_checks_answer_as_one_by_one_for_every_reader(api, monkeypatch):
    token, rooms = _seed(api)
    readers = {"turn": token, "owner": "ana-test", "person": "ben-test"}
    before = {}
    with monkeypatch.context() as m:
        _one_by_one(m)
        for name, t in readers.items():
            r = api.get(URL, headers=headers(t))
            assert r.status_code == 200, r.text
            before[name] = r.json()
    for name, t in readers.items():
        r = api.get(URL, headers=headers(t))
        assert r.status_code == 200, r.text
        assert r.json() == before[name], name
    # Not vacuous: the turn sees some rooms and not others, and both "more" flags are exercised.
    turn = before["turn"]
    seen = {m["conversation_id"] for m in turn["messages"]}
    assert turn["more_messages"] is True and len(turn["messages"]) == 100
    assert seen & set(rooms["mine"]) and not seen & set(rooms["people-private"] + rooms["never-handed"])
    assert {"deleted_at", "expires_at"}.isdisjoint(m["body"] for m in turn["messages"])
    assert all(before[name]["messages"] for name in readers)


def test_unread_rooms_are_checked_in_a_fixed_number_of_statements(api):
    """One unread message in each of 10 and then 300 task rooms the bot owns: the list runs as many statements at 300
    as at 10, the first call after a write too, and no task or tag row is read one room at a time."""
    store = api.app.state.store
    _, msg, attempt = setup_attempt(api)
    task = post(api, "tasks", {"owner": "ops", "title": "Seed", "body": "Seed room."})
    task_id = task["id"] if "id" in task else task["task"]["id"]
    with store.read() as c:
        row = dict(c.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())
        room = dict(c.execute("SELECT * FROM conversations WHERE task_id=? LIMIT 1", (task_id,)).fetchone())
        first = dict(c.execute("SELECT * FROM messages WHERE id=?", (msg["id"],)).fetchone())
    counts, seeded = {}, 0
    for size in (10, 300):
        ids = [uuid.uuid4().hex for _ in range(size - seeded)]
        cids = [uuid.uuid4().hex for _ in ids]
        with store.transaction() as c:
            clone(
                c,
                "tasks",
                {**row, "pipeline_id": None, "stage_id": None},
                len(ids),
                id=lambda i: ids[i],
                number=lambda i: None,
            )
            clone(
                c,
                "conversations",
                room,
                len(ids),
                id=lambda i: cids[i],
                task_id=lambda i: ids[i],
                room_key=lambda i: None,
                root_id=lambda i: None,
                path=lambda i: None,
            )
            clone(
                c,
                "messages",
                {**first, "recipient_id": "bot:ops", "read_at": None, "delivered_at": None, "wake_key": None},
                len(ids),
                conversation_id=lambda i: cids[i],
                created_at=lambda i: H.shift(first["created_at"], seconds=seeded + i + 1),
            )
        seeded = size
        for state in ("cold", "warm"):
            with traced(store) as seen:
                r = api.get(URL, headers=headers(attempt["token"]))
            assert r.status_code == 200, r.text
            counts[size, state] = seen
    for state in ("cold", "warm"):
        assert len(counts[300, state]) <= len(counts[10, state]) + 10, (
            state,
            len(counts[10, state]),
            len(counts[300, state]),
        )
    one = re.compile(
        r"^SELECT \* FROM tasks tasks WHERE id='[^']*'$|^SELECT tags\..* IN \('[^']*'\)|^SELECT m\.refs FROM messages m INDEXED BY messages_live_meeting_input"
    )
    per_room = [s for s in counts[300, "cold"] if one.search(s)]
    assert per_room == [], per_room[:3]
