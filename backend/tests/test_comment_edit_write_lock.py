"""A comment edit or delete holds the write lock for a time that does not grow with the stored retry answers (Dev 194).

On a 665k-row `idempotency` table, rewriting the answers that quote an edited comment read every answer inside the write
transaction: 35-37 s, and other writers timed out at BEGIN. The answers that quote a message are now found by index
(backend/idempotency_messages.py), and the edit and delete statements read no table whole.
"""

import json
import uuid

import pytest

from backend import idempotency_messages as IM
from backend.store import H
from backend.tests.helpers.tasks_board import api, post  # noqa: F401

BIG = 20000


def _comment(api, text="Words to withdraw", title="Review the report"):
    task = post(api, "tasks", {"owner": "ops", "title": title, "body": "Please review."})
    return task, post(api, f"tasks/{task['id']}/comments", {"text": text})["comment"]


def _answers(c, count, quoting=None):
    """`count` stored answers, most quoting some other message, as a busy server keeps them for 48 hours; with
    `quoting`, every 500th quotes that message."""

    def answer(i):
        if quoting and i % 500 == 0:
            return json.dumps({"comment": dict(quoting, body="old words"), "comments": [dict(quoting, body="old")]})
        if i % 5 == 0:
            other = {"id": str(uuid.uuid4()), "from_actor": "bot:ops", "body": "x" * 200, "kind": "say"}
            return json.dumps({"comment": other, "task": {"id": str(uuid.uuid4())}})
        return json.dumps({"ok": True, "id": str(uuid.uuid4()), "detail": "y" * 300})

    c.executemany(
        "INSERT INTO idempotency(actor,operation,key,request_hash,response,created_at) VALUES(?,?,?,?,?,?)",
        [(f"bot:b{i % 40}", "/api/v2/x", str(uuid.uuid4()), "h", answer(i), H.now()) for i in range(count)],
    )


def _steps(c, work):
    """SQLite virtual-machine steps `work` takes, in hundreds: the work done while it holds the lock."""
    count = [0]

    def tick():
        count[0] += 1
        return 0

    c.set_progress_handler(tick, 100)
    try:
        work()
    finally:
        c.set_progress_handler(None, 0)
    return count[0]


def test_edit_and_delete_read_no_table_whole(api):
    task, said = _comment(api)
    seen = []
    with api.app.state.store.transaction() as c:
        _answers(c, 200, quoting=said)
        c.set_trace_callback(seen.append)
        H.task_comment_edit(c, "human:ana", task["id"], said["id"], "New words")
        H.task_comment_delete(c, "human:ana", task["id"], said["id"])
        c.set_trace_callback(None)
    scans = []
    with api.app.state.store.read() as c:
        for sql in seen:
            words = sql.split()
            if not words or words[0].upper() not in ("SELECT", "UPDATE", "DELETE", "INSERT", "WITH"):
                continue
            plan = [row[3] for row in c.execute("EXPLAIN QUERY PLAN " + sql)]
            scans += [(step, sql) for step in plan if step.startswith("SCAN") and step != "SCAN sqlite_master"]
    assert scans == []


@pytest.mark.parametrize("change", ["edit", "delete"])
def test_the_work_under_the_lock_does_not_grow_with_the_stored_answers(api, change):
    steps = []
    for extra, title in ((0, "Review the report"), (BIG, "Review the budget")):
        task, said = _comment(api, title=title)
        with api.app.state.store.transaction() as c:
            _answers(c, extra)
        with api.app.state.store.transaction() as c:
            work = (
                (lambda: H.task_comment_edit(c, "human:ana", task["id"], said["id"], "New words"))
                if change == "edit"
                else (lambda: H.task_comment_delete(c, "human:ana", task["id"], said["id"]))
            )
            steps.append(_steps(c, work))
    small, big = steps
    # Reading every answer costs several steps per answer: 20,000 answers would add hundreds of these hundreds.
    assert big <= small * 1.2 + 20, steps


SHAPES = (
    lambda m: {"comment": m},  # the answer to the comment itself
    lambda m: {"comments": [m, dict(m, id="other", body="kept")]},  # a list of comments
    lambda m: {"task": {"thread": [{"items": [m]}]}},  # deep inside
    lambda m: [m, {"id": m["id"]}],  # an answer that is a list
    lambda m: {"comment": dict(m, sender_id=None)},  # from_actor present but null
    lambda m: {"ref": {"id": m["id"], "title": "not a message"}},  # the id without from_actor: kept
    lambda m: {"id": m["id"], "from_actor": "bot:ops", "body": "old"},  # the answer is the message
    lambda m: {"note": json.dumps(m)},  # quoted inside a string: kept
    lambda m: {"comment": dict(m, id=[m["id"]])},  # an id that is not a string: kept
    lambda m: {"comment": dict(m, id="someone-else")},  # another message
)


@pytest.mark.parametrize("change", ["edit", "delete"])
def test_retry_answers_that_quote_the_comment_are_rewritten(api, change, monkeypatch):
    task, said = _comment(api)
    monkeypatch.setattr(H, "now", lambda: "2026-10-18T14:19:00.000000Z")  # both runs edit at the same moment
    with api.app.state.store.transaction() as c:
        rows = [json.dumps(shape(dict(said, body="old"))) for shape in SHAPES]
        # Answers SQLite does not read as JSON: not JSON at all, and Python's NaN.
        rows += ["not json " + said["id"], '{"n": NaN, "comment": {"id": "%s", "from_actor": "x"}}' % said["id"]]
        c.executemany(
            "INSERT INTO idempotency(actor,operation,key,request_hash,response,created_at) VALUES(?,?,?,?,?,?)",
            [("human:ana", "/api/v2/shapes", f"k{i}", "h", row, H.now()) for i, row in enumerate(rows)],
        )

    with api.app.state.store.transaction() as c:
        assert IM.quoting(c, said["id"])
        if change == "edit":
            H.task_comment_edit(c, "human:ana", task["id"], said["id"], "New words")
        else:
            H.task_comment_delete(c, "human:ana", task["id"], said["id"])
        indexed = dict(c.execute("SELECT key,response FROM idempotency WHERE operation='/api/v2/shapes'").fetchall())
    assert indexed[f"k{len(SHAPES)}"] == "{}"  # an answer that is not JSON
    assert "old" not in json.loads(indexed[f"k{len(SHAPES) + 1}"], parse_constant=str)["comment"]["body"]
    assert "old" not in json.loads(indexed["k0"])["comment"]["body"]  # the rewrite did happen
    assert json.loads(indexed["k5"])["ref"] == {"id": said["id"], "title": "not a message"}
