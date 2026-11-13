"""Measures the share of a read spent finding page links (P08): the inbox (GET /api/v2/messages?unread=1) at 10, 100
and 500 unread messages in distinct task rooms, and opening a task (GET /api/v2/tasks/{tid}) with a short and a long
history, each read with the page-link checks on and off.

    python3 -m pytest bench/perf-page-scan.py -n 0 -s -q
    RUNS=5 python3 -m pytest bench/perf-page-scan.py -n 0 -s -q   # quicker

"Off" patches files.prime_page_references to do nothing and files.readable_page_refs to return the answer unchanged.
Share in the link check = 1 - off / on, from the medians of RUNS warm calls each, on and off calls taken in turn. The long task is then read again
naming 1, 10 and 100 pages, and once naming a page the reader may not open (its label is checked). Synthetic fixtures
only, on a shared machine: run it twice and give both runs.
"""

import contextlib
import os
import statistics
import time
import uuid

from backend import files
from backend.store import H
from backend.tests.helpers.api import headers, post, setup_attempt
from backend.tests.helpers.files import turn
from backend.tests.helpers.page_audience import edit
from backend.tests.helpers.pages import page
from backend.tests.helpers.payload_budgets import clone

RUNS = int(os.environ.get("RUNS", 9))
INBOX = (10, 100, 500)
SHORT, LONG = 7, 220  # comments: with the task's own events, the spec's 8 and 221 items
WORDS = (
    "The weekly numbers are in and the totals match the ledger, except for two refunds that landed after the cut. "
    "I moved them to next week and left a note on the reconciliation so the close is not held up by them. "
)


@contextlib.contextmanager
def checks_off():
    prime, readable = files.prime_page_references, files.readable_page_refs
    files.prime_page_references = lambda c, values: None
    files.readable_page_refs = lambda c, auth, who, refs, **_: refs
    try:
        yield
    finally:
        files.prime_page_references, files.readable_page_refs = prime, readable


def share(label, call, size=None):
    """Medians of RUNS warm calls with the checks on and off, taken in turn so a load swing on a shared machine falls
    on both."""
    call()
    with checks_off():
        call()
    on, off = [], []
    for _ in range(RUNS):
        for walls, switch in ((on, contextlib.nullcontext), (off, checks_off)):
            with switch():
                start = time.perf_counter()
                call()
                walls.append(time.perf_counter() - start)
    on, off = statistics.median(on) * 1000, statistics.median(off) * 1000
    print(
        f"{label:44s} {'' if size is None else f'{size:7d} B'} on={on:7.1f} ms off={off:7.1f} ms "
        f"share={max(0.0, 1 - off / on):5.1%}",
        flush=True,
    )
    return on, off


# ------------------------------------------------------------------------------------------------------ the inbox
def seed_rooms(store, task_row, conv, message, n, prefix):
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
            body=lambda i: f"{prefix} message {i}: {WORDS}",
            wake_key=lambda i: None,
        )


def test_inbox_share_by_unread_rooms(api):
    store = api.app.state.store
    _, _, attempt = setup_attempt(api)
    token = attempt["token"]
    task = post(api, "tasks", {"owner": "ops", "title": "Seed", "body": "The room every bench room copies."})
    task_id = task["id"] if "id" in task else task["task"]["id"]
    with store.read() as c:
        task_row = dict(c.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())
        conv = dict(c.execute("SELECT * FROM conversations WHERE task_id=? LIMIT 1", (task_id,)).fetchone())
        message = dict(
            c.execute(
                "SELECT * FROM messages WHERE conversation_id=? AND recipient_id='bot:ops' "
                "ORDER BY created_at DESC LIMIT 1",
                (conv["id"],),
            ).fetchone()
        )

    def call():
        r = api.get("/api/v2/messages?unread=1", headers=headers(token))
        assert r.status_code == 200, r.text
        return r

    print()
    seeded = 1
    for size in INBOX:
        seed_rooms(store, task_row, conv, message, size - seeded, f"s{size}")
        seeded = size
        share(f"inbox, {size} unread messages, no page links", call, len(call().content))


# ------------------------------------------------------------------------------------------------------- a task
def comment(api, tid, n, text=None):
    for i in range(n):
        post(api, f"tasks/{tid}/comments", {"text": text(i) if text else f"Update {i}. {WORDS}"})


def test_task_share_by_history_and_links(api):
    _, attempt = turn(api)

    def task(title):
        return post(api, "tasks", {"owner": "ops", "title": title, "body": WORDS, "private": False})["id"]

    def reader(tid):
        def call():
            r = api.get("/api/v2/tasks/" + tid, headers=headers("cara-test"))
            assert r.status_code == 200, r.text
            return r

        return call

    def items(tid):
        body = reader(tid)().json()
        return len(body.get("comments") or []) + len(body.get("events") or [])

    print()
    short = task("Short task")
    comment(api, short, SHORT)
    share(f"task, {items(short)} comments and events, no page links", reader(short), len(reader(short)().content))

    long_ = task("Long task")
    comment(api, long_, LONG)
    share(f"task, {items(long_)} comments and events, no page links", reader(long_), len(reader(long_)().content))

    held = task("Pages")
    made = [
        page(api, attempt, name=f"p{n}.html", task=held, title=f"Page {n}").json()["page"]["file_id"]
        for n in range(100)
    ]
    for count in (1, 10, 100):
        named = task(f"Long task naming {count} pages")
        comment(api, named, LONG - count)
        comment(api, named, count, lambda i: f"page:{made[i]}")
        share(f"task, {items(named)} items, {count} page links", reader(named), len(reader(named)().content))

    secret = made[0]
    edit(api, secret, {"people": ["ben"]})
    denied = task("Long task naming a page Cara may not open")
    comment(api, denied, LONG - 1)
    comment(api, denied, 1, lambda i: f"page:{secret}")
    share(f"task, {items(denied)} items, 1 denied page", reader(denied), len(reader(denied)().content))
    assert "A page you can't open" in reader(denied)().text
