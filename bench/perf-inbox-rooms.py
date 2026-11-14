"""Measures a bot's unread list (GET /api/v2/messages?unread=1, the handler GET /api/v2/inbox shares) with one unread
message in each of 10, 100, 200 and 500 task rooms, each room's task open and owned by the bot (P04).

    python3 -m pytest bench/perf-inbox-rooms.py -n 0 -s -q

Per size it prints the statements one call runs on the store's read connections, warm (the call repeated) and cold
(the first call after a write), the most statements any one SQL text ran, and the median wall time of RUNS warm
calls. Synthetic fixtures only: it shows the slope, not production timings.
"""

import collections
import re
import statistics
import time
import uuid

from backend.store import H
from backend.tests.helpers.api import headers, post, setup_attempt
from backend.tests.helpers.meeting_search import traced
from backend.tests.helpers.payload_budgets import clone

SIZES = (10, 100, 200, 500)
RUNS = 9
URL = "/api/v2/messages?unread=1"


def seed(store, task_row, conv, message, n, prefix):
    """`n` more open tasks owned by the bot, each with its own room holding one unread message from a person."""
    ids = [uuid.uuid4().hex for _ in range(n)]
    rooms = [uuid.uuid4().hex for _ in range(n)]
    with store.transaction() as c:
        clone(
            c,
            "tasks",
            {**task_row, "pipeline_id": None, "stage_id": None, "status": "open"},
            n,
            id=lambda i: ids[i],
            title=lambda i: f"{prefix} task {i}",
            number=lambda i: None,
        )
        clone(
            c,
            "conversations",
            conv,
            n,
            id=lambda i: rooms[i],
            task_id=lambda i: ids[i],
            room_key=lambda i: None,
            root_id=lambda i: rooms[i] if conv.get("root_id") else None,
            path=lambda i: f"/{rooms[i]}/" if conv.get("path") else conv.get("path"),
        )
        clone(
            c,
            "messages",
            {**message, "read_at": None, "delivered_at": None},
            n,
            conversation_id=lambda i: rooms[i],
            task_id=lambda i: ids[i],
            created_at=lambda i: H.shift(message["created_at"], seconds=i + 1),
            body=lambda i: f"{prefix} message {i}",
            wake_key=lambda i: None,
        )


def shape(sql):
    """A statement's text with its values blanked, so the same query on different rows counts as one."""
    return re.sub(r"'[^']*'|\\b\\d+\\b", "?", sql)


def call(api, token):
    r = api.get(URL, headers=headers(token))
    assert r.status_code == 200, r.text
    return r.json()


def test_inbox_statements_and_time_by_unread_rooms(api):
    store = api.app.state.store
    _, _, attempt = setup_attempt(api)
    token = attempt["token"]
    task = post(api, "tasks", {"owner": "ops", "title": "Seed", "body": "The room every bench room copies."})
    task_id = task["id"] if "id" in task else task["task"]["id"]
    with store.read() as c:
        task_row = dict(c.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())
        conv = dict(c.execute("SELECT * FROM conversations WHERE task_id=? LIMIT 1", (task_id,)).fetchone())
        message = c.execute(
            "SELECT * FROM messages WHERE conversation_id=? AND recipient_id='bot:ops' ORDER BY created_at DESC LIMIT 1",
            (conv["id"],),
        ).fetchone()
        message = dict(message) if message else None
    assert message, "the seed task sent the bot no message"
    seeded = 1
    for size in SIZES:
        seed(store, task_row, conv, message, size - seeded, f"s{size}")
        seeded = size
        post(api, "chat/ops", {"text": f"a write before {size}"})  # the next call is cold
        with traced(store) as cold:
            first = call(api, token)
        with traced(store) as warm:
            again = call(api, token)
        assert again == first
        walls = []
        for _ in range(RUNS):
            start = time.perf_counter()
            call(api, token)
            walls.append(time.perf_counter() - start)
        most = collections.Counter(map(shape, warm)).most_common(1)[0]
        cold_most = collections.Counter(map(shape, cold)).most_common(3)
        print(
            f"rooms={size:<4} messages={len(first['messages']):<4} more={first['more_messages']!s:<5} "
            f"warm={len(warm):<5} cold={len(cold):<5} median_ms={statistics.median(walls) * 1000:7.1f} "
            f"most_repeated={most[1]}x {most[0][:90]!r} cold_top={[(n, q[:70]) for q, n in cold_most]}",
            flush=True,
        )
