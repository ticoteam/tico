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
- a direct message between two people, a thread whose member is a group, and a private task held by a pipeline stage.

Each state is read twice by SQL's history gate: before the read_deps sweep (every row unsettled, so `tico sql` walks
each row's provenance) and after it (settled rows read from read_deps, a denied task still worked out per read).
Every door must give the same answer in every state, and PUBLIC and PRIVATE pin that answer, so agreement cannot pass
by every door refusing everything.

The fictional company (backend/tests/helpers/company.py): Ana owns it, Ben is an admin, Cara a plain person; ops and
finance are bots."""

import pytest

from backend.tests.slug_ids import bot_ids_are_their_slugs  # noqa: F401  (each bot's id is its slug)
from backend import events as E
from backend import read_deps, threads
from backend.auth import Identity
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.api import assign, claim, ready, runner, unrelated_task_managers
from backend.tests.helpers.automation_api import as_bot
from backend.tests.helpers.page_audience import edit
from backend.tests.helpers.pages import page
from backend.tests.helpers.sql import query
from backend.tests.threads_e2e import call, create_thread, make_group, ok, say

PEOPLE = {"ana": "ana-test", "ben": "ben-test", "cara": "cara-test"}
READERS = ("ana", "ben", "cara", "ops", "finance")
# Each message's first word, unique in the world, is what search looks it up by.
WORDS = ("zqplain", "zqnames", "zqtask", "zqsub", "zqstaged", "zqgroup", "zqdm", "zqcommented", "zqreview", "zqreply")
# What each reader reads, threads and messages, while the task is public and once it is private.
EVERYONE = {"company", "plain"}
# The task's thread, a sub-thread under it, and their messages (a comment with the page attached among them).
TASK = {"task", "sub", "task_said", "sub_said", "commented"}
NAMES_TASK = {"names_task"}  # a company message that names the task
STAGED = {"staged", "staged_said"}
GROUP = {"group", "group_said"}
DM = {"dm", "dm_said"}
REVIEW = {"review", "reply"}  # the page's: Cara is not in its audience
PUBLIC = {
    "ana": EVERYONE | TASK | NAMES_TASK | GROUP | REVIEW,
    "ben": EVERYONE | TASK | NAMES_TASK | STAGED | DM | REVIEW,
    "cara": EVERYONE | TASK | NAMES_TASK | STAGED | GROUP | DM,
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
    t = {"company": company, "task": late["conversation_id"], "staged": staged["conversation_id"], "group": group}
    m = {
        "plain": say(api, "ana-test", company, "zqplain the agenda"),
        "task_said": say(api, "ben-test", t["task"], "zqtask launch notes"),
        "staged_said": say(api, "cara-test", t["staged"], "zqstaged export columns"),
        "group_said": say(api, "ana-test", group, "zqgroup contract terms"),
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
    # After it was attached, the page goes to Ben alone (and whoever reads every page).
    edit(api, fid, {"people": ["ben"]})
    return {"threads": t, "messages": {k: v["id"] for k, v in m.items()}, "task": late["id"], "attempt": attempt}


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
        sse = threads.readable_ids(c, threads.viewer(auth, who), tids)
    sent, after, more = [], w["since"], True
    while more:  # the stream sends a batch at a time; read it all
        with store.read() as c:
            batch, after, reset, more = E.read(
                c, auth, who, after, topics=("messages",), task_views=lambda *_, **__: [], task_view=None
            )
        assert not reset
        sent += batch
    ids["sse"] = sse | {d.get("id") for _, topic, d in sent if topic == "messages" and not d.get("deleted")}
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
    for reader in READERS:
        found = _reads(api, w, reader)
        assert found["python"] == expected[reader], (state, reader, sorted(found["python"] ^ expected[reader]))
        for door, labels in found.items():
            assert labels == found["python"], (state, reader, door, sorted(labels ^ found["python"]))
        if reader != "finance":  # an external agent has no sidebar of its own here
            listed = _sidebar(api, w, reader)
            assert listed <= found["python"], (state, reader, "sidebar", sorted(listed - found["python"]))


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
    """What a message's attachments show its reader: the page's file id, or the placeholder of one they may not open."""
    refs = H._json(refs, {}) if isinstance(refs, str) else refs
    return [a.get("label") if a.get("removed") else a.get("file_id") for a in (refs or {}).get("attachments") or []]


def _attachment_doors(api, w, reader, label="commented"):
    """{door: the attachments it shows `reader` on that message}."""
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
        "sql": [_attachments(r[0]) for r in query(api, f"SELECT refs FROM messages WHERE id='{mid}'", token)["rows"]][
            0
        ],
    }


def test_a_page_attached_in_a_thread_is_removed_by_every_door_for_exactly_the_readers_it_was_narrowed_away_from(
    api, world
):
    w = world
    with api.app.state.store.read() as c:
        (fid,) = c.execute("SELECT file_id FROM pages").fetchone()
    removed = ["A page you can't open"]
    # Ben is the page's audience and ops made it; Cara and the external agent read the task's thread but not the page.
    expected = {"ben": [fid], "ops": [fid], "cara": removed, "finance": removed}
    for reader, shown in expected.items():
        assert _attachment_doors(api, w, reader) == dict.fromkeys(("timeline", "message", "sse", "sql"), shown), reader


@pytest.mark.xfail(
    strict=True,
    reason="tico sql's page check reads meeting_participant_readers on the guarded connection and fails the whole "
    "query for a reader in the page's audience who is no party to its task (here the owner); the follow-up task fixes it",
)
def test_the_owner_reads_the_page_comment_through_tico_sql_as_through_every_other_door(api, world):
    shown = _attachment_doors(api, world, "ana")
    assert shown == dict.fromkeys(("timeline", "message", "sse", "sql"), ["A page you can't open"])


# ----------------------------------------------------------------------------- unread counts
# (reader, thread) -> the messages its unread count counts that the reader may not open (the timeline shows each as a
# hidden row). The count reads only who wrote a message and whom it was delivered to (threads.unread_message_sql).
UNREAD_HIDDEN = {
    "public": {("cara", "company"): {"review", "reply"}},
    "private": {("cara", "company"): {"names_task", "review", "reply"}},
}


def _unread_hidden(api, w):
    """{(reader, thread label): the messages counted unread that the reader may not open}, for every thread each reader
    reads, after checking the sidebar's subtree count says the same as the thread's own."""
    auth, store = api.app.state.auth, api.app.state.store
    labels = {v: k for k, v in w["messages"].items()}
    found = {}
    with store.read() as c:
        for reader in READERS:
            v = threads.viewer(auth, _identity(api, w, reader))
            for name, tid in w["threads"].items():
                if not threads.readable(c, v, tid):
                    continue
                row = threads.thread_row(c, tid)
                follower = c.execute(
                    "SELECT last_read_seq FROM thread_followers WHERE thread_id=? AND actor=?", (tid, v.actor)
                ).fetchone()
                mark = max(follower[0] if follower else 0, threads._backfill_floor(c, v))
                counted = [
                    r[0]
                    for r in c.execute(
                        f"SELECT m.id FROM messages m WHERE m.conversation_id=? AND m.seq>? AND "
                        f"{threads.unread_message_sql()}",
                        (tid, mark, v.actor, v.actor),
                    )
                ]
                assert threads.unread(c, v, tid)["unread"] == len(counted), (reader, name)
                if row["root_id"] == tid and name not in ("task",):  # a root with no sub-thread: its row is its count
                    summary = threads.subtree_summaries(c, v, [tid]).get(tid, {"unread": 0})
                    assert summary["unread"] == len(counted), (reader, name, summary)
                hidden = {labels[m] for m in counted if m in labels and not threads.readable(c, v, H.message(c, m))}
                if hidden:
                    found[(reader, name)] = hidden
    return found


def test_the_unread_count_counts_only_what_its_reader_opens_but_for_the_hidden_rows_it_is_known_to_count(api, world):
    w = world
    assert _unread_hidden(api, w) == UNREAD_HIDDEN["public"]
    with api.app.state.store.transaction() as c:
        H.task_update(c, "human:ben", w["task"], private=True)
    assert _unread_hidden(api, w) == UNREAD_HIDDEN["private"]
