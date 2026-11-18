import json

import pytest

from backend import rooms, task_privacy, threads
from backend.auth import Identity
from backend.store import H
from backend.tests.helpers.api import get, headers, unrelated_task_managers
from backend.tests.slug_ids import bot_ids_are_their_slugs  # noqa: F401  (the fixture uses bot slugs as ids)


BOT = "bot:ops"
READER = Identity("human:cara", "human", "cara@acme.example")


def dm(api, c):
    return threads.dm(c, threads.viewer(api.app.state.auth, READER), [BOT])["id"]


def task(c, title="Release", *, private=False):
    return H.task_create(c, "human:ana", title, "Check the release", BOT, private=private)


def message(c, cid, body, *, sender=BOT, kind="say", refs=None):
    mid = H.new_id()
    c.execute(
        "INSERT INTO messages(id,conversation_id,sender_id,kind,body,refs,created_at) VALUES(?,?,?,?,?,?,?)",
        (mid, cid, sender, kind, body, json.dumps(refs or {}), "2026-11-18T10:00:00.000000Z"),
    )
    return mid


def read(api, cid, surface="timeline", **params):
    path = f"threads/{cid}/timeline" if surface == "timeline" else f"conversations/{cid}/{surface}"
    response = api.get(f"/api/v2/{path}", params=params, headers=headers("cara-test"))
    assert response.status_code == 200, response.text
    return response.json()


def ordered(page):
    return [row["id"] for row in sorted(page["messages"] + page.get("task_activity", []), key=lambda row: row["seq"])]


def test_dm_reads_only_the_peers_task_say_as_excerpts_without_writing(api):
    store = api.app.state.store
    with store.transaction() as c:
        cid = dm(api, c)
        release = task(c)
        tid = release["conversation_id"]
        early = H.task_comment(c, BOT, release["id"], "Before chat", wake=False)["id"]
        chat = message(c, cid, "How did it go?", sender=READER.actor)
        message(c, tid, "Someone else's work", sender="bot:eng")
        message(c, tid, "Human comment", sender="human:ana")
        message(c, tid, "Bot question", kind="ask")
        message(c, tid, "Bot notice", kind="notice")
        message(c, tid, "System notice", sender=H.TICO_ACTOR, kind="notice")
        notice = message(
            c, cid, "Started task: Release", sender=H.TICO_ACTOR, kind="notice", refs={"task": release["id"]}
        )
        long = message(c, tid, "Checked " + "a" * 200 + "\nDetails below")
        unrelated = threads.create(c, READER, title="Group chat", audience="personal", members=[BOT, "human:ana"])["id"]
        other_chat = message(c, unrelated, "Keep this in its own chat")
        count = c.execute("SELECT COUNT(*) FROM messages").fetchone()[0]

    for surface in ("timeline", "messages", "snapshot"):
        page = read(api, cid, surface)
        assert ordered(page) == [early, chat, notice, long]
        preview = page["task_activity"][-1]
        assert preview["title"] == "Release" and preview["task_id"] == release["id"]
        assert preview["excerpt"].startswith("Checked ") and preview["excerpt"].endswith("…")
        assert len(preview["excerpt"]) == 160 and "Details below" not in json.dumps(page)
    assert ordered(read(api, unrelated)) == [other_chat]
    with store.read() as c:
        assert c.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == count
        bot = threads.viewer(api.app.state.auth, Identity(BOT, "bot", agent="ops"))
        assert "task_activity" not in threads.timeline(c, bot, cid)


def test_task_and_message_privacy_both_apply_to_dm_activity(api):
    with api.app.state.store.transaction() as c:
        unrelated_task_managers(c, "ops")
        cid = dm(api, c)
        public = task(c)
        private = task(c, "Private project", private=True)
        okay = message(c, public["conversation_id"], "Visible status")
        message(c, private["conversation_id"], "Private status")
        message(c, public["conversation_id"], "Private provenance", refs={"task": private["id"]})
    get(api, f"tasks/{private['id']}", token="cara-test", expected=404)
    for surface in ("timeline", "messages"):
        page = read(api, cid, surface)
        assert ordered(page) == [okay]
        assert "Private" not in json.dumps(page)


@pytest.mark.parametrize("surface", ["timeline", "messages"])
def test_chat_pages_cover_activity_before_between_and_after_chat_rows(api, surface):
    with api.app.state.store.transaction() as c:
        cid = dm(api, c) if surface == "timeline" else rooms.personal_room(c, READER.actor, "ops")["id"]
        tid = task(c)["conversation_id"]
        expected = [message(c, tid, "Before the chat")]
        expected.append(message(c, cid, "First chat line", sender=READER.actor))
        expected.extend(
            message(c, tid, f"Between chat lines {i}") for i in range(task_privacy.CHAT_ACTIVITY_PAGE_SIZE + 2)
        )
        expected.append(message(c, cid, "Second chat line", sender=READER.actor))
        expected.append(message(c, tid, "After the chat"))

    before, next_key = ("cursor", "next_cursor") if surface == "timeline" else ("before", "next_before")
    seen, params = [], {"limit": 1}
    for _ in range(4):
        page = read(api, cid, surface, **params)
        seen = ordered(page) + seen
        if page[next_key] is None:
            break
        params[before] = page[next_key]
    assert page[next_key] is None
    assert seen == expected
