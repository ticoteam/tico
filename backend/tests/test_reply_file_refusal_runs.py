"""A run and an event or attachment on a reply inherit the file refusal of the messages the reply replies to.

test_reply_file_refusal covers the message read paths. A run reads its job message and its inputs through the same
reply chains (ReadIndex.attempt_pages, and the settled run gate in backend/sql.py), and an event or attachment reads
its message through ReadIndex.event and ReadIndex.blob. Each is checked here at depth 2 below a file-refused message,
with a plain control first, and with the settled index swept and unswept.

The reader is Cara: she requested the run's task, so she reads the run, and she is not a page manager, so the file's
audience decides for her. Ben published nothing and manages nothing in these fixtures.
"""

import pytest

from backend import read_deps, task_privacy
from backend.auth import Identity
from backend.privacy_index import ReadIndex
from backend.enqueue import enqueue_stored
from backend.store import H, encode
from backend.tests.helpers.api import assign, claim, headers, post, ready, runner, setup_attempt
from backend.tests.helpers.page_audience import edit
from backend.tests.helpers.pages import page
from backend.tests.helpers.task_privacy_reads import sql

READER, READER_TOKEN = "human:cara", "cara-test"


def stored(c, mid, cid, refs, in_reply_to=None, sender="human:ben"):
    """A message to bot:ops as stored, without queueing a job for it."""
    c.execute(
        "INSERT INTO messages(id,conversation_id,sender_id,recipient_id,kind,body,created_at,refs,in_reply_to) "
        "VALUES(?,?,?,'bot:ops','say','Synthetic',?,?,?)",
        (mid, cid, sender, H.now(), encode(refs), in_reply_to),
    )


def queue(c, mid, cid, refs, *, sender="human:ana", in_reply_to=None):
    """A message to bot:ops as stored, with the job it queues."""
    stored(c, mid, cid, refs, in_reply_to, sender=sender)
    enqueue_stored(c, mid)


def running_turn(api):
    """A run of the ops bot on a task Cara requested, operated by Ana."""
    r = runner(api, "ana")
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    work = post(api, "tasks", {"owner": "ops", "title": "Release", "body": "Ship it."}, token=READER_TOKEN)
    attempt = claim(api, r)
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "t"}, r["token"])
    with api.app.state.store.read() as c:
        cid = c.execute("SELECT conversation_id FROM tasks WHERE id=?", (work["id"],)).fetchone()[0]
    return r, work, attempt, cid


def refused_file(api):
    """A file Cara may not open: a page the finance bot publishes on Ana's computer, audience Ben alone."""
    _, _, finance = setup_attempt(api, bot="finance")
    made = page(api, finance)
    assert made.status_code == 200, made.text
    fid = made.json()["page"]["file_id"]
    edit(api, fid, {"people": ["ben"]}, token="ana-test")
    assert api.get("/api/v2/pages/" + fid, headers=headers(READER_TOKEN)).status_code == 404
    return fid


def run_visible(api, aid, settled):
    """What the run's reader sees by each path: the Python run check, the run index and the SQL run list."""
    store = api.app.state.store
    with store.read() as c:
        if settled:
            read_deps.sweep(c)
        answers = {
            "python": task_privacy.attempt_readable(c, READER, aid),
            "index": ReadIndex(c, Identity(READER, "human", email="cara@acme.example")).attempt(aid),
        }
    answers["sql"] = bool(sql(api, f"SELECT id FROM attempts WHERE id='{aid}'", READER_TOKEN))
    assert len(set(answers.values())) == 1, answers
    return answers["sql"]


@pytest.mark.parametrize("settled", [False, True])
@pytest.mark.parametrize("shape", ["input", "job"])
def test_run_whose_input_or_job_replies_to_a_refused_file_is_hidden(api, settled, shape):
    r, work, attempt, cid = running_turn(api)
    aid = attempt["id"]
    fid = refused_file(api)
    store = api.app.state.store
    # Control: the run's message names no file, so Cara reads the run.
    with store.transaction() as c:
        stored(c, "plain-root", cid, {})
        if shape == "job":
            c.execute(
                "UPDATE jobs SET message_id='plain-root' WHERE id=(SELECT job_id FROM attempts WHERE id=?)", (aid,)
            )
        else:
            queue(c, "plain-input", cid, {}, sender="human:cara")
            c.execute("UPDATE jobs SET state='input',attempt_id=? WHERE message_id='plain-input'", (aid,))
            c.execute(
                "INSERT INTO attempt_inputs(attempt_id,message_id,acked_at) VALUES(?,?,?)",
                (aid, "plain-input", H.now()),
            )
    assert run_visible(api, aid, settled) is True

    # Depth 2: a file refusal on the root, a reply to it, and the run's message replying to that reply.
    with store.transaction() as c:
        stored(c, "refused-root", cid, {"target": fid})
        stored(c, "refused-mid", cid, {}, in_reply_to="refused-root")
        if shape == "job":
            stored(c, "refused-leaf", cid, {}, in_reply_to="refused-mid")
            c.execute(
                "UPDATE jobs SET message_id='refused-leaf' WHERE id=(SELECT job_id FROM attempts WHERE id=?)", (aid,)
            )
        else:
            queue(c, "refused-leaf", cid, {}, sender="human:cara", in_reply_to="refused-mid")
            c.execute("UPDATE jobs SET state='input',attempt_id=? WHERE message_id='refused-leaf'", (aid,))
            c.execute(
                "INSERT INTO attempt_inputs(attempt_id,message_id,acked_at) VALUES(?,?,?)",
                (aid, "refused-leaf", H.now()),
            )
    assert run_visible(api, aid, settled) is False


def test_event_and_attachment_on_a_reply_to_a_refused_file_are_hidden(api):
    r, work, attempt, cid = running_turn(api)
    fid = refused_file(api)
    store = api.app.state.store
    with store.transaction() as c:
        stored(c, "plain-root", cid, {})
        stored(c, "plain-reply", cid, {}, in_reply_to="plain-root")
        stored(c, "refused-root", cid, {"target": fid})
        stored(c, "refused-reply", cid, {}, in_reply_to="refused-root")
        stored(c, "refused-leaf", cid, {}, in_reply_to="refused-reply")
        for blob, digest in (("b-plain", "d"), ("b-leaf", "e")):
            c.execute(
                "INSERT INTO blobs(id,owner,digest,size,name,content_type,created_at) "
                "VALUES(?,'human:ben',?,1,'a.txt','text/plain',?)",
                (blob, digest, H.now()),
            )
        c.execute("INSERT INTO message_assets(message_id,blob_id) VALUES('plain-reply','b-plain')")
        c.execute("INSERT INTO message_assets(message_id,blob_id) VALUES('refused-leaf','b-leaf')")
    who = Identity(READER, "human", email="cara@acme.example")
    with store.read() as c:
        index = ReadIndex(c, who)
        # Control: a reply to a plain message keeps its event and attachment.
        assert index.event("plain-reply", READER, H.now(), encode({})) is True
        assert index.blob("b-plain") is True
        # Depth 2 below a refused file: the event and the attachment are hidden.
        assert index.event("refused-leaf", READER, H.now(), encode({})) is False
        assert index.blob("b-leaf") is False


def chain(c, prefix, cid, length):
    """A reply chain of `length` plain messages, root first; each replies to the one before it."""
    ids = []
    for i in range(length):
        mid = f"{prefix}-{i}"
        stored(c, mid, cid, {}, in_reply_to=ids[-1] if ids else None)
        ids.append(mid)
    return ids


@pytest.mark.parametrize("settled", [False, True])
@pytest.mark.parametrize("refused_chain", ["job", "input"])
def test_two_long_reply_chains_on_one_run_each_keep_their_file_refusal(api, settled, refused_chain):
    """A run whose job and input each sit on a 60-deep reply chain. The file refusal sits on an ancestor 55 replies up
    from the leaf, past where a batch read of both chains would stop if it counted rows, not levels."""
    r, work, attempt, cid = running_turn(api)
    aid = attempt["id"]
    fid = refused_file(api)
    store = api.app.state.store
    with store.transaction() as c:
        job_chain = chain(c, "job", cid, 60)
        input_chain = chain(c, "input", cid, 60)
        c.execute(
            "UPDATE jobs SET message_id=? WHERE id=(SELECT job_id FROM attempts WHERE id=?)", (job_chain[-1], aid)
        )
        queue(c, "input-leaf", cid, {}, sender="human:cara", in_reply_to=input_chain[-1])
        c.execute("UPDATE jobs SET state='input',attempt_id=? WHERE message_id='input-leaf'", (aid,))
        c.execute(
            "INSERT INTO attempt_inputs(attempt_id,message_id,acked_at) VALUES(?,?,?)", (aid, "input-leaf", H.now())
        )
    assert run_visible(api, aid, settled) is True
    refused = job_chain if refused_chain == "job" else input_chain
    with store.transaction() as c:
        # Index 4 of a 60-long chain is 55 replies above its leaf.
        c.execute("UPDATE messages SET refs=? WHERE id=?", (encode({"target": fid}), refused[4]))
    assert run_visible(api, aid, settled) is False


@pytest.mark.parametrize("settled", [False, True])
@pytest.mark.parametrize("ancestors", [99, 100])
def test_a_run_event_and_attachment_past_the_reply_limit_follow_the_message(api, settled, ancestors):
    """A run whose job is a reply with `ancestors` ancestors, and its event and attachment: all of them agree on both
    sides of the limit, which ReadIndex.sources sets at 100 ancestors."""
    r, work, attempt, cid = running_turn(api)
    aid = attempt["id"]
    store = api.app.state.store
    with store.transaction() as c:
        ids = chain(c, "deep", cid, ancestors + 1)
        c.execute("UPDATE jobs SET message_id=? WHERE id=(SELECT job_id FROM attempts WHERE id=?)", (ids[-1], aid))
        c.execute(
            "INSERT INTO blobs(id,owner,digest,size,name,content_type,created_at) "
            "VALUES('b-deep','human:ben','f',1,'c.txt','text/plain',?)",
            (H.now(),),
        )
        c.execute("INSERT INTO message_assets(message_id,blob_id) VALUES(?,'b-deep')", (ids[-1],))
    expected = ancestors < 100
    assert run_visible(api, aid, settled) is expected
    who = Identity(READER, "human", email="cara@acme.example")
    with store.read() as c:
        if settled:
            read_deps.sweep(c)
        index = ReadIndex(c, who)
        assert index.event(ids[-1], READER, H.now(), encode({})) is expected
        assert index.blob("b-deep") is expected
        assert task_privacy.message_readable(c, READER, H.message(c, ids[-1], include_deleted=True)) is expected
