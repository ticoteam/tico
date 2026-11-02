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
              created=lambda i: H.shift(first["created"], seconds=-i - 1), origin_id=lambda i: uuid.uuid4().hex, seq=lambda i: None)
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


def test_a_thread_timeline_reads_its_branches_in_a_query_count_that_ignores_how_many_there_are(api):
    """The whole tree comes in queries per level, so 40 branches read in as many as 5, and a page stays under its bytes."""
    from backend import threads

    def tree(branches):
        with api.app.state.store.transaction() as c:
            owner = threads.actor_viewer(c, "human:ana")
            tid = threads.create(c, owner, title="Budget", audience="company")["id"]
            for i in range(branches):
                parent = threads.post(c, owner, tid, f"Own {i}")
                for j in range(3):
                    threads.post(c, owner, tid, f"Reply {j}", parent_message_id=parent["id"])
        return tid

    def statements(tid):
        with api.app.state.store.read_transaction() as c:
            threads.timeline(c, threads.actor_viewer(c, "human:ana"), tid, limit=100)     # warms the per-snapshot caches
            seen = []
            c.set_trace_callback(seen.append)
            page = threads.timeline(c, threads.actor_viewer(c, "human:ana"), tid, limit=100)
            c.set_trace_callback(None)
        return len(seen), page

    few, _ = statements(tree(5))
    many, page = statements(tree(40))
    assert len(page["messages"]) == 40 * 4
    assert many <= few + 2, (few, many)


def test_a_thread_timeline_stays_within_its_bytes_and_a_branch_too_big_for_a_page_is_one_more_row(api):
    from backend import threads
    with api.app.state.store.transaction() as c:
        owner = threads.actor_viewer(c, "human:ana")
        tid = threads.create(c, owner, title="Budget", audience="company")["id"]
        older = threads.post(c, owner, tid, "Older")
        threads.post(c, owner, tid, BODY, parent_message_id=older["id"])
        parent = threads.post(c, owner, tid, BODY[:3000])
        for _ in range(10):
            threads.post(c, owner, tid, BODY, parent_message_id=parent["id"])
    cap = 9 * KB
    with api.app.state.store.read_transaction() as c:
        v = threads.actor_viewer(c, "human:ana")
        page = threads.timeline(c, v, tid, max_bytes=cap)
        rest = threads.timeline(c, v, tid, cursor=page["next_cursor"], max_bytes=cap)
    assert size(page["messages"]) <= cap and page["next_cursor"]
    assert [r.get("body") for r in page["messages"]] == [BODY[:3000].strip(), None]
    assert page["messages"][1]["collapsed"] is True and page["messages"][1]["count"] == 10
    assert page["messages"][1]["thread_id"] == page["messages"][0]["sub_thread"]["id"]
    assert [r.get("body") for r in rest["messages"]] == ["Older", BODY] and rest["next_cursor"] is None


def test_a_settings_history_page_is_byte_capped(api):
    # 200 changes each carrying a 38 KB description: the page stops at its byte budget and names the next page.
    text = {"display_name": "Ops", "description": "A long description. " * 2000}
    with api.app.state.store.transaction() as c:
        c.executemany("INSERT INTO settings_changes(id,bot,field,before_json,after_json,actor,transition_id,created,"
                      "via) VALUES(?,?,?,?,?,?,?,?,?)",
                      [(H.new_id(), "ops", "definition", encode(text), encode(text), "human:ana", None,
                        H.shift(H.now(), seconds=i), None) for i in range(200)])
    opened = api.get("/api/v2/settings/history?limit=200", headers=headers())
    page = opened.json()
    assert 0 < len(page["changes"]) < 200 and page["next_before"]
    assert len(opened.content) <= 300 * KB


def test_a_settings_history_page_counts_utf8_bytes_not_characters(api):
    # 200 changes whose descriptions are the 2,000 characters a description may hold, all emoji (four bytes each):
    # a page counted in characters would send four times its budget.
    text = {"display_name": "Ops", "description": "🙂" * 2000}
    with api.app.state.store.transaction() as c:
        c.executemany("INSERT INTO settings_changes(id,bot,field,before_json,after_json,actor,transition_id,created,"
                      "via) VALUES(?,?,?,?,?,?,?,?,?)",
                      [(H.new_id(), "ops", "definition", encode(text), encode(text), "human:ana", None,
                        H.shift(H.now(), seconds=i), None) for i in range(200)])
    opened = api.get("/api/v2/settings/history?limit=200", headers=headers())
    page = opened.json()
    assert 0 < len(page["changes"]) < 200 and page["next_before"]
    assert size(page["changes"]) <= 256 * KB and len(opened.content) <= 257 * KB


def test_a_settings_change_bigger_than_a_page_comes_without_its_values_and_paging_passes_it(api):
    # The owner's list of 40,000 people (about 900 KB of JSON) between two small changes.
    ids = [H.new_id() for _ in range(3)]
    people = [f"human:person-{i:05d}" for i in range(40000)]
    values = [(["human:ana"], ["human:ana", "human:ben"]), ([], people), ([], ["human:ana"])]
    with api.app.state.store.transaction() as c:
        c.executemany("INSERT INTO settings_changes(id,bot,field,before_json,after_json,actor,transition_id,created,"
                      "via) VALUES(?,?,?,?,?,?,?,?,?)",
                      [(ids[i], "ops", "owners", encode(before), encode(after), "human:ana", None,
                        H.shift(H.now(), seconds=-i), None) for i, (before, after) in enumerate(values)])
    first = api.get("/api/v2/settings/history?limit=2", headers=headers()).json()
    assert [row["id"] for row in first["changes"]] == ids[:2] and first["next_before"] == ids[1]
    big = first["changes"][1]
    assert big["truncated"] is True and big["before"] is None and big["after"] is None
    assert size(first["changes"]) <= 256 * KB
    rest = api.get(f"/api/v2/settings/history?limit=2&before={ids[1]}", headers=headers()).json()
    assert [row["id"] for row in rest["changes"]] == [ids[2]] and rest["next_before"] is None
    # A page whose first change is the big one sends it the same way, and the next page starts past it.
    alone = api.get(f"/api/v2/settings/history?limit=1&before={ids[0]}", headers=headers()).json()
    assert [row["id"] for row in alone["changes"]] == [ids[1]] and alone["next_before"] == ids[1]
    assert alone["changes"][0]["truncated"] is True
    past = api.get(f"/api/v2/settings/history?limit=1&before={ids[1]}", headers=headers()).json()
    assert [row["id"] for row in past["changes"]] == [ids[2]] and past["changes"][0]["truncated"] is False


def prepared_transitions(api, cid, count, text):
    """`count` prepared transitions of ops, newest first, each with a checkpoint of `text` in the chat `cid` (the table
    gives every checkpoint a message of its own, so each one is a copy of the chat's first message)."""
    stamp = H.now()
    with api.app.state.store.transaction() as c:
        first = dict(c.execute(
            "SELECT * FROM messages WHERE conversation_id=? ORDER BY rowid LIMIT 1", (cid,)).fetchone())
        clone(c, "messages", {**first, "from_actor": "bot:ops", "to_actor": "human:ana"}, count,
              created=lambda i: H.shift(first["created"], seconds=-i - 1), origin_id=lambda i: uuid.uuid4().hex, seq=lambda i: None)
        messages = [row[0] for row in c.execute(
            "SELECT id FROM messages WHERE conversation_id=? ORDER BY rowid DESC LIMIT ?", (cid, count)).fetchall()]
        for i, message in enumerate(messages):
            tid = H.new_id()
            c.execute("INSERT INTO bot_transitions(id,bot,kind,target_json,requested_by,expected_revision,state,"
                      "created,updated) VALUES(?,?,?,?,?,?,?,?,?)",
                      (tid, "ops", "model", encode({"model": "fake-model"}), "human:ana", 1, "prepared",
                       H.shift(stamp, seconds=-i), stamp))
            c.execute("INSERT INTO bot_transition_checkpoints(transition_id,conversation_id,message_id,state,"
                      "checkpoint_json) VALUES(?,?,?,?,?)",
                      (tid, cid, message, "prepared",
                       encode({"current_objective": text,
                               "next_recommended_action": "Ask Ana", "open_decisions": []})))


def test_a_settings_history_first_page_with_transitions_stays_within_its_bytes(api):
    # Three prepared changes with a checkpoint of about 20 KB each, and a page of changes that fills what they leave.
    # The 120 people who made the changes have long names: the page sends each name beside its id.
    _, msg, _ = setup_attempt(api)
    prepared_transitions(api, msg["conversation_id"], 3, "A checkpoint of the chat's objective. " * 550)
    text = {"display_name": "Ops", "description": "A long description. " * 80}
    with api.app.state.store.transaction() as c:
        c.executemany("INSERT INTO humans(id,name,email,slack_id,teams_json) VALUES(?,?,?,?,?)",
                      [(f"person-{i:03d}", f"Person {i:03d} " + "with a long name " * 6, None, None, None)
                       for i in range(120)])
        c.executemany("INSERT INTO settings_changes(id,bot,field,before_json,after_json,actor,transition_id,created,"
                      "via) VALUES(?,?,?,?,?,?,?,?,?)",
                      [(H.new_id(), "ops", "definition", encode(text), encode(text), f"human:person-{i:03d}", None,
                        H.shift(H.now(), seconds=-i), None) for i in range(120)])
    opened = api.get("/api/v2/settings/history?limit=100", headers=headers())
    page = opened.json()
    assert len(opened.content) <= 256 * KB
    assert len(page["transitions"]) == 3 and page["transitions_truncated"] is False
    assert len(page["changes"]) >= 40 and page["next_before"]


def test_a_settings_history_first_page_leaves_out_the_oldest_transitions_its_bytes_cannot_hold(api):
    # Twelve prepared changes of about 45 KB each are more than a page. The newest that fit are sent and flagged, and
    # the page still takes its changes.
    _, msg, _ = setup_attempt(api)
    prepared_transitions(api, msg["conversation_id"], 12, "A checkpoint of the chat's objective. " * 1200)
    text = {"display_name": "Ops", "description": "A long description. " * 80}
    with api.app.state.store.transaction() as c:
        c.executemany("INSERT INTO settings_changes(id,bot,field,before_json,after_json,actor,transition_id,created,"
                      "via) VALUES(?,?,?,?,?,?,?,?,?)",
                      [(H.new_id(), "ops", "definition", encode(text), encode(text), "human:ana", None,
                        H.shift(H.now(), seconds=-i), None) for i in range(5)])
    opened = api.get("/api/v2/settings/history?limit=100", headers=headers())
    page = opened.json()
    assert len(opened.content) <= 256 * KB
    with api.app.state.store.read() as c:
        newest = [row[0] for row in c.execute("SELECT id FROM bot_transitions ORDER BY created DESC")]
    kept = [row["id"] for row in page["transitions"]]
    assert page["transitions_truncated"] is True and 0 < len(kept) < 12 and kept == newest[:len(kept)]
    assert len(page["changes"]) >= 1


def test_a_settings_history_first_page_flags_the_transitions_past_its_limit(api):
    # Four small prepared changes on a page of three: the bytes hold them all, but the oldest is left out and flagged.
    _, msg, _ = setup_attempt(api)
    prepared_transitions(api, msg["conversation_id"], 4, "A short objective.")
    page = api.get("/api/v2/settings/history?limit=3", headers=headers()).json()
    with api.app.state.store.read() as c:
        newest = [row[0] for row in c.execute("SELECT id FROM bot_transitions ORDER BY created DESC")]
    assert [row["id"] for row in page["transitions"]] == newest[:3] and page["transitions_truncated"] is True
    page = api.get("/api/v2/settings/history?limit=4", headers=headers()).json()
    assert len(page["transitions"]) == 4 and page["transitions_truncated"] is False


def test_snoozed_notes_stay_out_of_the_board_and_the_task_keeps_its_note(api):
    store = api.app.state.store
    task = post(api, "tasks", {"owner": "human:ana", "title": "Snoozed 0", "body": "x"})
    note = "🔔" * 500                                   # 500 characters, 2,000 bytes each
    with store.transaction() as c:
        row = dict(c.execute("SELECT * FROM tasks WHERE id=?", (task["id"],)).fetchone())
        clone(c, "tasks", row, 499, title=lambda i: f"Snoozed {i}")
        c.execute("UPDATE tasks SET snoozed_until=?, snoozed_note=? WHERE title LIKE 'Snoozed %'",
                  (H.shift(H.now(), days=5), note))
    board = api.get("/api/v2/tasks?brief=1&limit=500&snoozed=only", headers=headers())
    rows = board.json()["tasks"]
    assert len(rows) == 500
    assert all("snoozed_note" not in r for r in rows), "a list row carries no snooze note"
    whole = get(api, "tasks/" + task["id"])["task"]
    assert whole["snoozed_note"] == note, "the task's own view keeps the note whole"


def test_the_federation_status_pages_its_peerings_and_shared_threads(api):
    """GET /api/v2/federation: one page of peerings and one of shared threads at a time, each with its cursor."""
    other = "gi_" + "b" * 24
    with api.app.state.store.transaction() as c:
        for i in range(130):
            install = f"gi_{i:024x}"
            c.execute("INSERT INTO federation_peerings(id,hq_peering_id,peer_install,direction,status,peer_company,created_at,"
                      "updated_at) VALUES(?,?,?,?,?,?,?,?)",
                      (f"fp_{i:04d}", f"hq_{i:04d}", install, "outgoing", "active", f"Acme {i} " + "x" * 100, H.now(), H.now()))
            c.execute("INSERT INTO federation_threads(id,thread_id,home_install,origin_id,applied_through,high_seq,"
                      "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                      (uuid.uuid4().hex, uuid.uuid4().hex, other, f"origin-{i:04d}", 1, 1, H.now(), H.now()))
    first = get(api, "federation?limit=100")
    assert len(first["peerings"]) == 100 and first["next_cursor"] and len(first["threads"]) == 100
    assert first["threads_next_cursor"]
    assert size(first) < 64 * KB
    rest = get(api, f"federation?limit=100&cursor={first['next_cursor']}&threads_cursor={first['threads_next_cursor']}")
    assert len(rest["peerings"]) == 30 and rest["next_cursor"] is None
    assert len(rest["threads"]) == 30 and rest["threads_next_cursor"] is None
    seen = [p["id"] for p in first["peerings"] + rest["peerings"]]
    assert len(set(seen)) == 130
    threads = [t["thread_id"] for t in first["threads"] + rest["threads"]]
    assert len(set(threads)) == 130
    assert first["peerings"][0]["company"].startswith("Acme 129 ")         # newest first
    assert api.get("/api/v2/federation?limit=101", headers=headers()).status_code == 422
    assert api.get("/api/v2/federation?cursor=nope", headers=headers()).status_code == 422
