"""Who reads a thread and its messages, asked through every door at once (backend/threads.py, contract C1).

threads.py says its rule three times: in Python (`readable`, `message_readable`), as SQL (`readable_sql` and
`message_sql`, which `tico sql`, the sidebar, search and the unread counts build on) and for the live stream
(`readable_ids` and events._messages). test_threads_core checks the three agree on the demo world's threads. This file
asks the same question about the cases the demo world does not have, for the owner, an admin, a plain person, a bot in
a turn and an external agent:

- a task thread, a sub-thread under it and a company message that names the task, all written while the task was
  public, then the task made private;
- a page attached to a comment on that task, and a company message whose review names the page, after the page's
  audience is narrowed to one person (per-reader file refusals);
- a reply to that review message: a row whose walk reads a message naming a file, which read_deps never settles;
- a direct message between two people, a thread whose member is a group, a private task held by a pipeline stage, and a
  person's own private task that no other reader is a party to (the admin reads none of it).

The live stream is read as it is delivered: every thread renamed and every message edited in each state, read on the
`messages` and `threads` topics from just before. A stream that started before the page's audience changed resets.
The sidebar and the unread counts are pinned as rows and numbers, so a door refusing too much fails as well.

Each state is read twice by SQL's history gate: before the read_deps sweep (every row unsettled, so `tico sql` walks
each row's provenance) and after it (settled rows read from read_deps, a denied task still worked out per read).
Every door must give the same answer in every state, and PUBLIC and PRIVATE pin that answer, so agreement cannot pass
by every door refusing everything.

The fictional company (backend/tests/helpers/company.py): Ana owns it, Ben is an admin, Cara a plain person; ops and
finance are bots."""

import json

import pytest

from backend.tests.slug_ids import bot_ids_are_their_slugs  # noqa: F401  (each bot's id is its slug)
from backend import events as E
from backend import read_deps, threads
from backend import sql as S
from backend.auth import Identity
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.api import assign, claim, headers, ready, runner, unrelated_task_managers
from backend.tests.helpers.automation_api import as_bot
from backend.tests.helpers.page_audience import edit
from backend.tests.helpers.pages import page
from backend.tests.helpers.sql import query
from backend.tests.threads_e2e import call, create_thread, make_group, ok, say

PEOPLE = {"ana": "ana-test", "ben": "ben-test", "cara": "cara-test"}
READERS = ("ana", "ben", "cara", "ops", "finance")
# Each message's first word, unique in the world, is what search looks it up by.
WORDS = (
    "zqplain",
    "zqnames",
    "zqtask",
    "zqsub",
    "zqstaged",
    "zqgroup",
    "zqdm",
    "zqcommented",
    "zqreview",
    "zqreply",
    "zqsecret",
)
# What each reader reads, threads and messages, while the task is public and once it is private.
EVERYONE = {"company", "plain"}
# The task's thread, a sub-thread under it, and their messages (a comment with the page attached among them).
TASK = {"task", "sub", "task_said", "sub_said", "commented"}
NAMES_TASK = {"names_task"}  # a company message that names the task
STAGED = {"staged", "staged_said"}
GROUP = {"group", "group_said"}
DM = {"dm", "dm_said"}
REVIEW = {"review", "reply"}  # the page's: Cara is not in its audience
# Cara's own private task, outside every other reader's parties and Ben's management: an admin seat reads none of it.
SECRET = {"secret", "secret_said"}
PUBLIC = {
    "ana": EVERYONE | TASK | NAMES_TASK | GROUP | REVIEW,
    "ben": EVERYONE | TASK | NAMES_TASK | STAGED | DM | REVIEW,
    "cara": EVERYONE | TASK | NAMES_TASK | STAGED | GROUP | DM | SECRET,
    "ops": EVERYONE | TASK | NAMES_TASK | STAGED | REVIEW,
    # An external agent reads no company thread by standing (C1); a public task's thread it reads as any bot does.
    "finance": TASK,
}
PRIVATE = {
    # Ben asked for the task and ops owns it; the owner seat does not read another's private task.
    "ana": PUBLIC["ana"] - TASK - NAMES_TASK,
    "ben": PUBLIC["ben"],
    "cara": PUBLIC["cara"] - TASK - NAMES_TASK,
    "ops": PUBLIC["ops"],
    "finance": set(),
}
# The reply's walk reads the message it answers, which names a file (read_deps.names_file): it is never settled, so
# SQL walks it on every read. The review row's own file check is SQL's page gate, so it settles.
NEVER_SETTLED = {"reply"}
# The sidebar each person sees in every state: a few rows each, so a door that refuses too much fails too.
SIDEBAR = {
    "ana": {"company", "group"},
    "ben": {"company", "dm", "staged"},
    "cara": {"company", "dm", "group"},
    "ops": {"company", "task"},
}


@pytest.fixture
def world(api):
    """{"threads": {label: id}, "messages": {label: id}, "task": the task made private later, "attempt": ops's run}."""
    store = api.app.state.store
    with store.transaction() as c:
        unrelated_task_managers(c)
        late = H.task_create(c, "human:ben", "Late launch", "The plan.", "bot:ops", private=False, lint=False)
        # Ops's own private work, which its run carries (and gives the runner something to claim).
        own = H.task_create(c, "human:ben", "Payroll packet", "Pay.", "bot:ops", private=True, lint=False)
        # Given to Ben, held by ops at Build: ops reads it by the stage.
        X.pipeline(c, "eng", "Exports", ({"name": "Build", "owner": "bot:ops", "requirements": []},))
        staged = H.task_create(
            c, "human:cara", "Build the export", "Rows.", "human:ben", private=True, pipeline="eng", lint=False
        )
        secret = H.task_create(c, "human:cara", "Salary review", "Mine.", "human:cara", private=True, lint=False)
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    attempt = claim(api, machine, "ops")
    with store.transaction() as c:
        c.execute("UPDATE attempts SET task_id=? WHERE id=?", (own["id"], attempt["id"]))
    as_bot(api, "finance")
    made = page(api, attempt, title="Audience secret")
    assert made.status_code == 200, made.text
    fid = made.json()["page"]["file_id"]
    ok(call(api, "PATCH", "files/" + fid, "ben-test", {"promote": True}))

    company = create_thread(api, title="All hands", audience="company")["id"]
    legal = make_group(api, "Legal", people=["cara"])["id"]
    group = create_thread(api, title="Legal only", members=["group:" + legal])["id"]
    t = {
        "company": company,
        "task": late["conversation_id"],
        "staged": staged["conversation_id"],
        "group": group,
        "secret": secret["conversation_id"],
    }
    m = {
        "plain": say(api, "ana-test", company, "zqplain the agenda"),
        "task_said": say(api, "ben-test", t["task"], "zqtask launch notes"),
        "staged_said": say(api, "cara-test", t["staged"], "zqstaged export columns"),
        "group_said": say(api, "ana-test", group, "zqgroup contract terms"),
        "secret_said": say(api, "cara-test", t["secret"], "zqsecret my numbers"),
    }
    m["sub_said"] = say(api, "ben-test", t["task"], "zqsub the venue", parent_message_id=m["task_said"]["id"])
    t["sub"] = m["sub_said"]["conversation_id"]
    dm = ok(call(api, "POST", "threads", "ben-test", {"audience": "personal", "members": ["human:cara"], "body": "x"}))
    t["dm"] = dm["id"]
    m["dm_said"] = say(api, "cara-test", t["dm"], "zqdm lunch on friday")
    # A comment on the task with the page attached: a message in the task's thread whose file is checked per reader.
    m["commented"] = ok(
        call(
            api,
            "POST",
            f"tasks/{late['id']}/comments",
            "ben-test",
            {"text": "zqcommented deck", "attachments": [fid + "@1"]},
        )
    )["comment"]
    with store.transaction() as c:
        m["names_task"] = threads.post(c, "human:ana", company, "zqnames see the launch", refs={"task": late["id"]})
        m["review"] = threads.post(
            c, "human:ana", company, "zqreview notes", refs={"review": {"files": [{"file_id": fid, "title": "Deck"}]}}
        )
        m["reply"] = threads.post(c, "human:ben", company, "zqreply agreed", in_reply_to=m["review"]["id"])
    assert t["sub"] not in (t["task"], company) and m["commented"]["conversation_id"] == t["task"]
    with store.read() as c:
        before = E.latest(c)
    # After it was attached, the page goes to Ben alone (and whoever reads every page).
    edit(api, fid, {"people": ["ben"]})
    return {
        "threads": t,
        "messages": {k: v["id"] for k, v in m.items()},
        "task": late["id"],
        "attempt": attempt,
        "file": fid,
        "before_narrowing": before,
    }


def _identity(api, w, reader):
    if reader == "ops":
        return Identity("bot:ops", "bot", attempt_id=w["attempt"]["id"])
    return api.app.state.auth.settings.test_identities[_token(w, reader)]


def _token(w, reader):
    return {"ops": w["attempt"]["token"], "finance": "finance-test"}.get(reader) or PEOPLE[reader]


def _reads(api, w, reader):
    """{door: the labels (threads and messages) it lets `reader` read}."""
    auth, store = api.app.state.auth, api.app.state.store
    who, token = _identity(api, w, reader), _token(w, reader)
    tids, mids = list(w["threads"].values()), list(w["messages"].values())
    ids = {}
    with store.read() as c:
        rows = {mid: H.message(c, mid) for mid in mids}
        ids["python"] = {t for t in tids if threads.readable(c, threads.viewer(auth, who), t)} | {
            mid for mid in mids if threads.readable(c, threads.viewer(auth, who), rows[mid])
        }
        # a door for threads only: the messages it is compared on are the Python answer
        ids["readable_ids"] = threads.readable_ids(c, threads.viewer(auth, who), tids) | (ids["python"] - set(tids))
    sent, after, more = [], w["since"], True
    while more:  # the stream sends a batch at a time; read it all
        with store.read() as c:
            batch, after, reset, more = E.read(
                c, auth, who, after, topics=("messages", "threads"), task_views=lambda *_, **__: [], task_view=None
            )
        assert not reset
        sent += batch
    # Every thread was renamed and every message edited since `since` (_check): the stream names what it delivers.
    ids["sse"] = {d["thread_id"] for _, topic, d in sent if topic == "threads" and d.get("kind") == "renamed"} | {
        d.get("id") for _, topic, d in sent if topic == "messages" and not d.get("deleted")
    }
    ids["sql"] = {r[0] for r in query(api, "SELECT id FROM conversations", token)["rows"]} | {
        r[0] for r in query(api, "SELECT id FROM messages", token)["rows"]
    }
    # Search finds messages, each with its thread.
    hits = [h for word in WORDS for h in ok(call(api, "GET", "threads/search?q=" + word, token))["hits"]]
    ids["search"] = {h["message_id"] for h in hits}
    labels = {v: k for k, v in {**w["threads"], **w["messages"]}.items()}
    found = {door: {labels[i] for i in got if i in labels} for door, got in ids.items()}
    hit_threads = {labels[h["thread_id"]] for h in hits if h["thread_id"] in labels}
    assert hit_threads <= found["python"], (reader, sorted(hit_threads - found["python"]))
    found["search"] |= found["python"] & set(w["threads"])
    return found


def _sidebar(api, w, reader):
    listed = ok(call(api, "GET", "threads", _token(w, reader)))
    labels = {v: k for k, v in w["threads"].items()}
    return {
        labels[r["id"]] for key in ("attention", "threads", "dms") for r in listed.get(key, []) if r["id"] in labels
    }


def _check(api, w, expected, state):
    with api.app.state.store.transaction() as c:
        # Every message again on the stream, as an edit sends it: a stream that started before the page's audience
        # changed is reset (events.read_in), so each state reads from here.
        w["since"] = E.latest(c)
        marks = ",".join("?" * len(w["messages"]))
        c.execute(f"UPDATE messages SET edited_at=? WHERE id IN ({marks})", [state, *w["messages"].values()])
        # and every thread on the `threads` topic, as a rename sends it
        marks = ",".join("?" * len(w["threads"]))
        c.execute(f"UPDATE conversations SET subject=? WHERE id IN ({marks})", [state, *w["threads"].values()])
    for reader in READERS:
        found = _reads(api, w, reader)
        assert found["python"] == expected[reader], (state, reader, sorted(found["python"] ^ expected[reader]))
        for door, labels in found.items():
            assert labels == found["python"], (state, reader, door, sorted(labels ^ found["python"]))
        if reader != "finance":  # an external agent has no sidebar of its own here
            listed = _sidebar(api, w, reader)
            assert listed <= found["python"], (state, reader, "sidebar", sorted(listed - found["python"]))
            assert listed == SIDEBAR[reader], (state, reader, "sidebar", sorted(listed ^ SIDEBAR[reader]))


def _settled(api, w):
    with api.app.state.store.read() as c:
        keys = {r[0] for r in c.execute("SELECT key FROM read_index")}
    return {label for label, mid in w["messages"].items() if "m:" + mid in keys}


def _sweep(api):
    with api.app.state.store.read() as c:
        read_deps.sweep(c)


def test_every_door_reads_threads_and_messages_alike_as_a_task_goes_private_and_history_settles(api, world):
    w = world
    assert _settled(api, w) == set(), "the world starts with every row unsettled"
    _check(api, w, PUBLIC, "public, unsettled")
    _sweep(api)
    assert _settled(api, w) == set(w["messages"]) - NEVER_SETTLED
    _check(api, w, PUBLIC, "public, settled")
    with api.app.state.store.transaction() as c:
        H.task_update(c, "human:ben", w["task"], private=True)
    # A task turning private touches no node: the rows settled while it was public stay settled, and SQL works out
    # the denial per read.
    assert _settled(api, w) == set(w["messages"]) - NEVER_SETTLED
    _check(api, w, PRIVATE, "private, settled while public")
    with api.app.state.store.transaction() as c:  # every row unsettled again, as a writer's trigger leaves it
        c.execute("INSERT OR IGNORE INTO read_dirty(key) SELECT 'm:'||id FROM messages")
        c.execute("DELETE FROM read_index")
    _check(api, w, PRIVATE, "private, unsettled")
    _sweep(api)
    assert _settled(api, w) == set(w["messages"]) - NEVER_SETTLED
    _check(api, w, PRIVATE, "private, settled while private")


# ----------------------------------------------------------------------------- a page attached, per reader
def _attachments(refs):
    """What a message's attachments show its reader: the page's file id, or the whole placeholder of one they may not
    open (compared whole, so a placeholder that kept the file's id, title or blob fails)."""
    refs = H._json(refs, {}) if isinstance(refs, str) else refs
    return [a if a.get("removed") else a.get("file_id") for a in (refs or {}).get("attachments") or []]


REMOVED = [{"removed": True, "label": "A page you can't open"}]  # files.page_ref_denials' placeholder, nothing more
DOORS = ("timeline", "message", "sse")  # and `tico sql`, asked apart (_sql_attachments)


def _sql_attachments(api, w, reader, label="commented"):
    mid = w["messages"][label]
    (row,) = query(api, f"SELECT refs FROM messages WHERE id='{mid}'", _token(w, reader))["rows"]
    return _attachments(row[0])


def _attachment_doors(api, w, reader, label="commented"):
    """{door: the attachments it shows `reader` on that message}, but for `tico sql`."""
    auth, store = api.app.state.auth, api.app.state.store
    mid, token, who = w["messages"][label], _token(w, reader), _identity(api, w, reader)
    with store.transaction() as c:
        since = E.latest(c)
        c.execute("UPDATE messages SET edited_at=? WHERE id=?", (H.now(), mid))
    with store.read() as c:
        sent, _, reset, _ = E.read(
            c, auth, who, since, topics=("messages",), task_views=lambda *_, **__: [], task_view=None
        )
    assert not reset
    (live,) = [d for _, topic, d in sent if topic == "messages" and d.get("id") == mid]
    timeline = ok(call(api, "GET", f"threads/{w['threads']['task']}/timeline", token))["messages"]
    return {
        "timeline": [_attachments(r.get("refs")) for r in timeline if r.get("id") == mid][0],
        "message": _attachments(ok(call(api, "GET", "messages/" + mid, token))["refs"]),
        "sse": _attachments((live.get("message") or live).get("refs") if isinstance(live, dict) else live.refs),
    }


def test_a_page_attached_in_a_thread_is_removed_by_every_door_for_exactly_the_readers_it_was_narrowed_away_from(
    api, world
):
    w = world
    fid = w["file"]
    # Ben is the page's audience and ops made it; the owner, Cara and the external agent read the task's thread but not
    # the page.
    expected = {"ben": [fid], "ops": [fid], "ana": REMOVED, "cara": REMOVED, "finance": REMOVED}
    for reader, shown in expected.items():
        assert _attachment_doors(api, w, reader) == dict.fromkeys(DOORS, shown), reader
        assert _sql_attachments(api, w, reader) == shown, (reader, "sql")


def test_the_owner_reads_the_page_comment_through_tico_sql_as_through_every_other_door(api, world):
    mid = world["messages"]["commented"]
    r = api.post(
        "/api/v2/sql", json={"sql": f"SELECT * FROM messages WHERE id='{mid}'"}, headers=headers(PEOPLE["ana"])
    )
    assert r.status_code == 200, r.text
    body = r.json()
    (row,) = body["rows"]
    assert _attachments(row[body["columns"].index("refs")]) == REMOVED
    # Nothing of the page in any column: not its file, its title, its name or its words.
    whole = json.dumps(row)
    for secret in (world["file"], "Audience secret", "weekly.html", "Acme weekly review", "42 deals"):
        assert secret not in whole, secret


def _add_call_follow_up(api, world):
    """A call follow-up task in the company: from then on a private task's read also asks whether its reader reads
    the thread of some follow-up's call (call_follow_ups.thread_reader)."""
    store = api.app.state.store
    with store.transaction() as c:
        other = H.task_create(c, "human:cara", "Check the date", "From the call.", "bot:ops", private=True, lint=False)
        now = H.now()
        c.execute(
            "INSERT INTO live_meetings(id,owner_actor,title,state,window_ms,created_at,updated_at,started_at) "
            "VALUES('lm-parity','human:cara','Launch call','ended',30000,?,?,?)",
            (now, now, now),
        )
        c.execute(
            "INSERT INTO calls(id,thread_id,live_meeting_id,connector_actor,title,state,room_name,started_at,"
            "created_at,updated_at) VALUES('call-parity',?,'lm-parity','human:cara','Launch call','ended',"
            "'room-parity',?,?,?)",
            (world["threads"]["company"], now, now, now),
        )
        c.execute("INSERT INTO call_follow_ups(task_id,call_id,bot_id) VALUES(?,'call-parity','ops')", (other["id"],))


# The internal tables the trusted page check reads (backend/sql.py source_tables); a caller's own SQL never does.
CHECK_ONLY_TABLES = ("meeting_participant_readers", "meeting_task_links", "call_follow_ups", "calls")


def test_the_owner_reads_the_page_comment_through_tico_sql_when_the_company_has_a_call_follow_up(api, world):
    """The page check falls through to call follow-ups once any exist, and reads each one's call: still one answer."""
    _add_call_follow_up(api, world)
    assert _sql_attachments(api, world, "ana") == REMOVED
    assert _sql_attachments(api, world, "ben") == [world["file"]]


def test_the_tables_the_page_check_reads_stay_closed_to_the_caller_s_own_sql(api, world):
    """Listed for Tico's own check, not for the caller: named directly, or in the same query as a page the check runs
    for, each is refused for a person and for an agent, and none is a table tico sql offers."""
    _add_call_follow_up(api, world)
    mid = world["messages"]["commented"]
    for table in CHECK_ONLY_TABLES:
        assert table not in S._ALL_EXPOSED and table not in S.GRANTED_SOURCES, table
        for reader in ("ana", "ben", "ops"):
            token = _token(world, reader)
            for sql in (
                f"SELECT * FROM {table}",
                f"SELECT count(*) FROM {table}",
                f"SELECT refs, (SELECT count(*) FROM {table}) FROM messages WHERE id='{mid}'",
                f"SELECT * FROM main.{table}",
            ):
                detail = query(api, sql, token, expected=422)["error"]["detail"]
                assert "prohibited" in detail or detail == "not authorized", (table, reader, sql, detail)


def test_narrowing_a_page_resets_every_reader_s_stream_that_started_before_it(api, world):
    """A stream reading across the page's audience change sends nothing it would have to take back: it resets, and the
    reader reloads (events.read_in). Read from the change on, the same reader's stream goes on."""
    w = world
    auth, store = api.app.state.auth, api.app.state.store
    with store.read() as c:
        moved = E.latest(c)
    for reader in READERS:
        who = _identity(api, w, reader)
        with store.read() as c:
            sent, cursor, reset, more = E.read(
                c, auth, who, w["before_narrowing"], topics=E.AGENT_TOPICS, task_views=lambda *_, **__: []
            )
            assert (sent, cursor, reset, more) == ([], moved, True, False), reader
            assert E.read(c, auth, who, moved, topics=E.AGENT_TOPICS, task_views=lambda *_, **__: [])[2] is False


# ----------------------------------------------------------------------------- unread counts
# {(reader, thread): its unread count}, every thread a reader reads that has one; each count also holds the message a
# task or a direct message opens with. Pinned as numbers, so a count that drops a message its reader opens fails.
UNREAD = {
    "public": {
        ("ana", "company"): 1,
        ("ana", "task"): 3,
        ("ana", "sub"): 1,
        ("ben", "company"): 3,
        ("ben", "staged"): 2,
        ("ben", "dm"): 1,
        ("cara", "company"): 4,
        ("cara", "task"): 3,
        ("cara", "group"): 1,
        ("cara", "sub"): 1,
        ("cara", "dm"): 1,
        ("ops", "company"): 4,
        ("ops", "task"): 3,
        ("ops", "staged"): 2,
        ("ops", "sub"): 1,
        ("finance", "task"): 3,
        ("finance", "sub"): 1,
    },
    "private": {
        ("ana", "company"): 1,
        ("ben", "company"): 3,
        ("ben", "staged"): 2,
        ("ben", "dm"): 1,
        ("cara", "company"): 4,
        ("cara", "group"): 1,
        ("cara", "dm"): 1,
        ("ops", "company"): 4,
        ("ops", "task"): 3,
        ("ops", "staged"): 2,
        ("ops", "sub"): 1,
    },
}
# (reader, thread) -> the messages its count counts that the reader may not open (the timeline shows each as a hidden
# row). The count reads only who wrote a message and whom it was delivered to (threads.unread_message_sql).
UNREAD_HIDDEN = {
    "public": {("cara", "company"): {"review", "reply"}},
    "private": {("cara", "company"): {"names_task", "review", "reply"}},
}


def _unread(api, w):
    """({(reader, thread label): unread count}, {(reader, thread label): the counted messages the reader may not open})
    for every thread each reader reads, after checking the sidebar's subtree count says the same as the thread's own."""
    auth, store = api.app.state.auth, api.app.state.store
    labels = {v: k for k, v in w["messages"].items()}
    counts, hidden = {}, {}
    with store.read() as c:
        for reader in READERS:
            v = threads.viewer(auth, _identity(api, w, reader))
            for name, tid in w["threads"].items():
                if not threads.readable(c, v, tid):
                    continue
                count = threads.unread(c, v, tid)["unread"]
                if threads.thread_row(c, tid)["root_id"] == tid and name != "task":  # no sub-thread: its row's count
                    assert threads.subtree_summaries(c, v, [tid]).get(tid, {"unread": 0})["unread"] == count, (
                        reader,
                        name,
                    )
                if count:
                    counts[(reader, name)] = count
                follower = c.execute(
                    "SELECT last_read_seq FROM thread_followers WHERE thread_id=? AND actor=?", (tid, v.actor)
                ).fetchone()
                mark = max(follower[0] if follower else 0, threads._backfill_floor(c, v))
                counted = c.execute(
                    f"SELECT m.id FROM messages m WHERE m.conversation_id=? AND m.seq>? AND "
                    f"{threads.unread_message_sql()}",
                    (tid, mark, v.actor, v.actor),
                ).fetchall()
                assert len(counted) == count, (reader, name)
                unopened = {
                    labels[r[0]] for r in counted if r[0] in labels and not threads.readable(c, v, H.message(c, r[0]))
                }
                if unopened:
                    hidden[(reader, name)] = unopened
    return counts, hidden


def test_the_unread_count_counts_what_its_reader_opens_and_the_hidden_rows_it_is_known_to_count(api, world):
    w = world
    assert _unread(api, w) == (UNREAD["public"], UNREAD_HIDDEN["public"])
    with api.app.state.store.transaction() as c:
        H.task_update(c, "human:ben", w["task"], private=True)
    assert _unread(api, w) == (UNREAD["private"], UNREAD_HIDDEN["private"])
