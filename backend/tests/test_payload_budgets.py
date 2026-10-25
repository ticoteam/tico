"""What the web app downloads, in bytes, on a busy company's generated data: a Needs-you change on the live stream,
opening a chat, the task board's brief list and the status line stay under their budgets, and a run's tool output
never reaches a list. The limits start loose; tighten them as real installs' per-route sizes come in."""

import json
import uuid

from backend import events as E
from backend import hubdb as H
from backend.store import encode
from backend.tests.test_api import api, get, headers, post, setup_attempt  # noqa: F401

KB = 1024
MARKER = "TOOL-OUTPUT-MARKER-7f3a"
BODY = "A long reply with a table and notes. " * 160            # about 6 KB a message


def clone(c, table, row, n, **changes):
    """`n` copies of `row` in `table`, each with a fresh id and `changes(i)` applied."""
    cols = list(row)
    rows = []
    for i in range(n):
        copy = dict(row, id=uuid.uuid4().hex)
        copy.update({k: f(i) for k, f in changes.items()})
        rows.append([copy[k] for k in cols])
    c.executemany(f"INSERT INTO {table}({','.join(cols)}) VALUES({','.join('?' * len(cols))})", rows)


def size(value):
    return len(encode(value).encode())


def test_payloads_stay_under_their_budgets(api):
    store, auth = api.app.state.store, api.app.state.auth
    _, msg, attempt = setup_attempt(api)               # ana's chat with ops, its run under way
    cid = msg["conversation_id"]
    task = post(api, "tasks", {"owner": "human:ana", "title": "Review the plan", "body": BODY})["task"]
    with store.transaction() as c:
        row = dict(c.execute("SELECT * FROM tasks WHERE id=?", (task["id"],)).fetchone())
        # 2,000 tasks: 500 on ana (her Needs you), the rest on bots.
        clone(c, "tasks", row, 499, title=lambda i: f"Review plan {i}")
        clone(c, "tasks", row, 1500, owner=lambda i: "bot:ops", title=lambda i: f"Bot work {i}")
        first = dict(c.execute("SELECT * FROM messages WHERE conversation_id=? ORDER BY rowid LIMIT 1", (cid,)).fetchone())
        # 1,000 long replies before it; a person's message would queue a run of its own.
        clone(c, "messages", {**first, "body": BODY, "from_actor": "bot:ops", "to_actor": "human:ana"}, 1000,
              created=lambda i: H.shift(first["created"], seconds=-i - 1))
        # 60 bots at work, each with a status line as a busy team's are (focus, task, last result, counts).
        bot = dict(c.execute("SELECT * FROM bots WHERE slug='ops'").fetchone())
        c.executemany(f"INSERT INTO bots({','.join(bot)}) VALUES({','.join('?' * len(bot))})",
                      [[f"bot-{i}" if k == "slug" else f"Bot {i}" if k == "display_name" else v for k, v in bot.items()]
                       for i in range(53)])
        c.executemany("INSERT OR REPLACE INTO bot_status(bot,state,focus,task_id,since,last_turn_at,last_result,next_due,"
                      "open_tasks,needs_human,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                      [(slug, "running", "Drafting the weekly report for the launch review and checking the numbers",
                        task["id"], H.now(), H.now(), "Filed the summary and asked finance to confirm the totals. " * 3,
                        H.now(), 12, 1, H.now())
                       for slug in [f"bot-{i}" for i in range(53)] + ["coo", "ops", "cpo", "product-design", "finance",
                                                                       "inbox", "doc-updater"]])
        for seq in range(200):
            c.execute("INSERT INTO attempt_events(attempt_id,seq,kind,payload_json,created) VALUES(?,?,?,?,?)",
                      (attempt["id"], 1000 + seq, "tool" if seq % 2 else "message",
                       json.dumps({"name": "shell", "output": MARKER * 50, "text": f"Step {seq} done. " * 40}), H.now()))

    # Needs you, on the live stream: a change to one item sends ids, not the 500-item list.
    who = auth.authenticate({"authorization": "Bearer ana-test"})
    with store.read() as c:
        held = E.needs_baseline(c, auth, who, api.app.state.task_view)
        after, base = E.latest(c), held["version"]
    with store.transaction() as c:
        c.execute("UPDATE tasks SET title='Review the plan today' WHERE id=?", (task["id"],))
    with store.read() as c:
        whole = E.read(c, auth, who, after, topics=("needs",), task_view=api.app.state.task_view)[0]
        changes = E.read(c, auth, who, after, topics=("needs",), task_view=api.app.state.task_view, needs_held=held)[0]
    assert whole and size(whole[-1][2]) > 1024 * KB                     # what an older page still gets: the whole list
    event = changes[-1][2]
    assert size(event) <= 8 * KB and event["changed"] == [task["id"]] and event["base"] == base
    mended = get(api, f"needs-you?ids={task['id']}")
    assert mended["version"] == event["version"] and [x["id"] for x in mended["items"]] == list(mended["positions"]) == [task["id"]]

    # Opening a chat as the web app does: 50 messages at most, under 160 KB of them, the run's output capped.
    opened = api.get(f"/api/v2/conversations/{cid}/snapshot?limit=50&max_bytes=163840", headers=headers())
    snap = opened.json()
    assert len(opened.content) <= 256 * KB and snap["has_more"] and snap["execution"]["parts"]
    assert len(snap["execution"]["parts"]) <= 40

    # The inbox (`hub inbox`): ana's 1,000 unread replies and 500 tasks arrive as the newest 100 of each, flagged.
    # A 6 KB reply is sent with its body and raw body, so 100 measure about 1.3 MB; held there.
    inbox = api.get("/api/v2/messages?unread=1", headers=headers())
    unread = inbox.json()
    assert len(unread["messages"]) == len(unread["tasks"]) == 100 and unread["more_messages"] and unread["more_tasks"]
    assert unread["messages"][-1]["created"] >= unread["messages"][0]["created"] and len(inbox.content) <= 1400 * KB

    # The task board's brief list and the status line.
    board = api.get("/api/v2/tasks?brief=1&limit=500", headers=headers())
    # The aim is 200 KB. A brief task is still about 1.2 KB (its type, step, rollups and empty fields, every one sent);
    # this holds today's 580 KB from growing until the brief row is cut down.
    assert len(board.json()["tasks"]) == 500 and len(board.content) <= 600 * KB
    status = api.get("/api/v2/status", headers=headers())
    # 60 bots' status lines measure about 35 KB (under 600 B a bot); held just above that, inside the 64 KB budget.
    assert len(status.json()["bots"]) == 60 and len(status.content) <= 40 * KB

    # Tool output is in no list (the whole Needs-you list above included).
    lists = [opened, board, status, api.get("/api/v2/conversations", headers=headers())]
    assert not [r.request.url for r in lists if MARKER in r.text] and MARKER not in encode([whole, changes])
