"""Opening a chat: GET /api/v2/threads/{id}, a conversation's snapshot and the conversation lists each read in one
snapshot (store.read_transaction), so their per-row checks share read_cache's rows. That snapshot commits where a plain
read rolled back, so none of them may write. The lists check every conversation from rows read together for the page,
never a few statements per row. Fictional people only: ana (owner), ben and cara."""

import re

from backend import threads
from backend.tests.helpers.api import headers
from backend.tests.helpers.task_privacy_reads import traced

WRITE = re.compile(r"^\s*(INSERT|UPDATE|DELETE|REPLACE)\b", re.I)


def _statements(api, path, token="ana-test"):
    response, statements = traced(api, lambda: api.get(path, headers=headers(token)))
    assert response.status_code == 200, response.text
    return response.json(), statements


def _start(api, n, tag, *, starter, audience="company"):
    with api.app.state.store.transaction() as c:
        for i in range(n):
            threads.post(c, starter, None, f"{tag} {i}", start={"title": f"{tag} {i}", "audience": audience})


def test_conversation_lists_stay_flat_as_conversations_grow(api):
    """5 then 30 conversations: the owner's view of a bot's and a person's own list each add at most 5 statements, and
    every conversation is listed."""
    sizes, mine, bots = (5, 30), {}, {}
    done = 0
    for size in sizes:
        _start(api, size - done, f"ops{size}", starter="bot:ops")
        _start(api, size - done, f"ana{size}", starter="human:ana", audience="members")
        done = size
        listed, statements = _statements(api, "/api/v2/conversations?bot=ops")
        assert len([r for r in listed["conversations"] if r["subject"].startswith("ops")]) == size
        bots[size] = len(statements)
        listed, statements = _statements(api, "/api/v2/conversations")
        assert len([r for r in listed["conversations"] if r["subject"].startswith("ana")]) == size
        mine[size] = len(statements)
    assert bots[30] - bots[5] <= 5 and mine[30] - mine[5] <= 5, (bots, mine)


def test_opening_a_chat_writes_nothing(api):
    """The thread view, its snapshot and both conversation lists run no INSERT, UPDATE, DELETE or REPLACE."""
    with api.app.state.store.transaction() as c:
        out = threads.post(c, "human:ana", None, "Hi", start={"dm": ["bot:ops"], "title": ""})
        dm = out.get("thread_id") or out.get("conversation_id")
        for k in range(3):
            threads.post(c, "bot:ops", dm, f"Answer {k}")
    _start(api, 3, "ops", starter="bot:ops")
    for path in (
        f"/api/v2/threads/{dm}",
        f"/api/v2/conversations/{dm}/snapshot?limit=50",
        "/api/v2/conversations",
        "/api/v2/conversations?bot=ops",
    ):
        _, statements = _statements(api, path)
        assert not [s for s in statements if WRITE.match(s)], path
