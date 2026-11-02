"""A comment edit or delete holds the write lock for a time that does not grow with the stored retry answers (Dev 194).

On a 665k-row `idempotency` table, rewriting the answers that quote an edited comment read every answer inside the write
transaction: 35-37 s, and other writers timed out at BEGIN. The answers that quote a message are now found by index
(backend/idempotency_messages.py), and the edit and delete statements read no table whole.
"""
import json
import sqlite3
import threading
import time
import uuid

import pytest

from backend import idempotency_messages as IM
from backend.store import H
from backend.tests.test_tasks_board import api, post  # noqa: F401

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
    c.executemany("INSERT INTO idempotency VALUES(?,?,?,?,?,?)",
                  [(f"bot:b{i % 40}", "/api/v2/x", str(uuid.uuid4()), "h", answer(i), H.now()) for i in range(count)])


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
            work = (lambda: H.task_comment_edit(c, "human:ana", task["id"], said["id"], "New words")) \
                if change == "edit" else (lambda: H.task_comment_delete(c, "human:ana", task["id"], said["id"]))
            steps.append(_steps(c, work))
    small, big = steps
    # Reading every answer costs several steps per answer: 20,000 answers would add hundreds of these hundreds.
    assert big <= small * 1.2 + 20, steps


def test_a_writer_waiting_on_an_edit_does_not_time_out(api, tmp_path):
    """The live file reads slowly from disk; here each 100 SQLite steps under the lock take 1 ms. Reading every one
    of 20,000 answers then held the lock for seconds, past the other writer's 1 s wait."""
    task, said = _comment(api)
    with api.app.state.store.transaction() as c:
        _answers(c, BIG, quoting=said)
    held, outcome, timing = threading.Event(), {}, {}

    def edit():
        with api.app.state.store.transaction() as c:
            started = time.monotonic()
            held.set()
            c.set_progress_handler(lambda: time.sleep(0.001) or 0, 100)
            try:
                H.task_comment_edit(c, "human:ana", task["id"], said["id"], "New words")
            finally:
                c.set_progress_handler(None, 0)
            timing["held"] = time.monotonic() - started

    def write():
        held.wait(10)
        other = sqlite3.connect(str(api.app.state.store.settings.db_path), timeout=1.0, isolation_level=None)
        try:
            other.execute("BEGIN IMMEDIATE")
            other.execute("INSERT INTO registry_metadata VALUES('dev194.writer','true')")
            other.execute("COMMIT")
            outcome["ok"] = True
        except sqlite3.OperationalError as exc:
            outcome["error"] = str(exc)
        finally:
            other.close()

    threads = [threading.Thread(target=edit), threading.Thread(target=write)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(60)
    assert outcome == {"ok": True}, (outcome, timing)
    assert timing["held"] < 1.0, timing
    with api.app.state.store.read() as c:
        stored = " ".join(r[0] for r in c.execute("SELECT response FROM idempotency"))
        assert "old words" not in stored and stored.count("New words") >= 2 * (BIG // 500)


SHAPES = (
    lambda m: {"comment": m},                                           # the answer to the comment itself
    lambda m: {"comments": [m, dict(m, id="other", body="kept")]},       # a list of comments
    lambda m: {"task": {"thread": [{"items": [m]}]}},                    # deep inside
    lambda m: [m, {"id": m["id"]}],                                       # an answer that is a list
    lambda m: {"comment": dict(m, from_actor=None)},                     # from_actor present but null
    lambda m: {"ref": {"id": m["id"], "title": "not a message"}},         # the id without from_actor: kept
    lambda m: {"id": m["id"], "from_actor": "bot:ops", "body": "old"},   # the answer is the message
    lambda m: {"note": json.dumps(m)},                                    # quoted inside a string: kept
    lambda m: {"comment": dict(m, id=[m["id"]])},                         # an id that is not a string: kept
    lambda m: {"comment": dict(m, id="someone-else")},                    # another message
)


def _read(text):
    try:
        return json.loads(text, parse_constant=str)        # NaN as text, so it equals itself
    except ValueError:
        return text


@pytest.mark.parametrize("change", ["edit", "delete"])
def test_retry_answers_are_rewritten_as_reading_every_answer_did(api, change, monkeypatch):
    task, said = _comment(api)
    monkeypatch.setattr(H, "now", lambda: "2026-10-18T14:19:00.000000Z")     # both runs edit at the same moment
    with api.app.state.store.transaction() as c:
        rows = [json.dumps(shape(dict(said, body="old"))) for shape in SHAPES]
        # Answers SQLite does not read as JSON: not JSON at all, and Python's NaN.
        rows += ["not json " + said["id"], '{"n": NaN, "comment": {"id": "%s", "from_actor": "x"}}' % said["id"]]
        c.executemany("INSERT INTO idempotency VALUES(?,?,?,?,?,?)",
                      [("human:ana", "/api/v2/shapes", f"k{i}", "h", row, H.now()) for i, row in enumerate(rows)])

    def outcome(indexed):
        class Undo(Exception):
            pass
        found = {}
        with pytest.raises(Undo), api.app.state.store.transaction() as c:
            if not indexed:
                c.execute("DELETE FROM registry_metadata WHERE key=?", (IM.READY,))
            assert (IM.quoting(c, said["id"]) is None) is not indexed
            if change == "edit":
                H.task_comment_edit(c, "human:ana", task["id"], said["id"], "New words")
            else:
                H.task_comment_delete(c, "human:ana", task["id"], said["id"])
            found.update((r[0], r[1]) for r in c.execute("SELECT key,response FROM idempotency "
                                                         "WHERE operation='/api/v2/shapes'"))
            raise Undo
        return found

    indexed, whole = outcome(True), outcome(False)
    # The same stored answers, read as a retry reads them; one that is not JSON compares as text.
    assert {k: _read(v) for k, v in indexed.items()} == {k: _read(v) for k, v in whole.items()}
    assert indexed[f"k{len(SHAPES)}"] == "{}"                                   # as reading every answer left it
    assert "old" not in json.loads(indexed[f"k{len(SHAPES) + 1}"], parse_constant=str)["comment"]["body"]
    assert "old" not in json.loads(indexed["k0"])["comment"]["body"]       # the rewrite did happen
    assert json.loads(indexed["k5"])["ref"] == {"id": said["id"], "title": "not a message"}


def test_answers_written_before_the_index_are_found_after_the_backfill(api):
    task, said = _comment(api)
    with api.app.state.store.transaction() as c:
        for statement in ("DROP TRIGGER idempotency_messages_insert", "DROP TRIGGER idempotency_messages_update",
                          "DROP TRIGGER idempotency_messages_delete", "DROP TABLE idempotency_messages"):
            c.execute(statement)
        c.execute("DELETE FROM registry_metadata WHERE key=?", (IM.READY,))
        _answers(c, 3000, quoting=said)        # an older server's answers, 6 of them quoting the comment
        quoted = {r[0] for r in c.execute("SELECT rowid FROM idempotency WHERE instr(response, ?)>0",
                                          (said["id"],))}
    assert len(quoted) == 7                     # and the answer to the request that wrote the comment
    with api.app.state.store.read() as c:
        IM.ensure(c)
        assert IM.quoting(c, said["id"]) is None                 # not ready: an edit reads every answer
        stopped = threading.Event()
        stopped.set()
        assert IM.backfill(c, span=400, stop=stopped) == 0       # the server stopping: the next start goes again
        assert IM.quoting(c, said["id"]) is None
        writes, real_write = [], IM._write

        class StopAfterFirstWrite:
            def is_set(self):
                return bool(writes)

        def counted(conn, work):
            writes.append(1)
            real_write(conn, work)
        IM._write = counted
        try:
            assert IM.backfill(c, batch=50, span=3000, stop=StopAfterFirstWrite()) == 50   # stops mid-span
        finally:
            IM._write = real_write
        assert writes == [1] and IM.quoting(c, said["id"]) is None   # one write, not ready
        writes.clear()
        IM._write = counted
        try:
            assert IM.backfill(c, batch=1000, span=10**6, stop=StopAfterFirstWrite()) == 601   # every batch, then stop
        finally:
            IM._write = real_write
        assert writes == [1] and IM.quoting(c, said["id"]) is None   # the ready mark is not written either
        assert IM.backfill(c, batch=50, span=400) == 601         # the answers that may quote a message
        assert {r[0] for r in IM.quoting(c, said["id"])} == quoted
        assert IM.backfill(c) == 0                               # once
    with api.app.state.store.transaction() as c:
        H.task_comment_edit(c, "human:ana", task["id"], said["id"], "New words")
        assert {r[0] for r in IM.quoting(c, said["id"])} == quoted      # rewritten, still quoting it
        H.task_comment_delete(c, "human:ana", task["id"], said["id"])
        # The deleted comment leaves the lists; each answer keeps its record of the comment, now empty.
        assert {r[0] for r in IM.quoting(c, said["id"])} == quoted
        assert all(json.loads(r[1]).get("comments") == [] for r in IM.quoting(c, said["id"])
                   if "comments" in json.loads(r[1]))
        some = sorted(quoted)[0]
        c.execute("UPDATE idempotency SET response='{}' WHERE rowid=?", (some,))   # a rewrite that drops the quote
        assert {r[0] for r in IM.quoting(c, said["id"])} == quoted - {some}
        c.execute("DELETE FROM idempotency")                    # the retention sweep
        assert c.execute("SELECT count(*) FROM idempotency_messages").fetchone()[0] == 0


def test_the_server_indexes_an_older_servers_answers_after_it_starts(tmp_path):
    from fastapi.testclient import TestClient
    from backend.app import create_app
    from backend.config import Settings
    from backend.store import Store
    settings = Settings(db_path=tmp_path / "hub.db")
    store = Store(settings)
    store.initialize(seed_market=False)
    message = {"id": "m-older", "from_actor": "bot:ops", "body": "old words"}
    with store.transaction() as c:
        for statement in ("DROP TRIGGER idempotency_messages_insert", "DROP TRIGGER idempotency_messages_update",
                          "DROP TRIGGER idempotency_messages_delete", "DROP TABLE idempotency_messages"):
            c.execute(statement)
        c.execute("DELETE FROM registry_metadata WHERE key=?", (IM.READY,))
        _answers(c, 1000, quoting=message)
    with TestClient(create_app(settings)) as client:
        deadline = time.monotonic() + 30
        with client.app.state.store.read() as c:
            while IM.quoting(c, "m-older") is None and time.monotonic() < deadline:
                time.sleep(0.05)
            assert len(IM.quoting(c, "m-older")) == 2


def test_a_new_file_is_ready_without_a_backfill(tmp_path):
    from backend.config import Settings
    from backend.store import Store
    store = Store(Settings(db_path=tmp_path / "hub.db"))
    store.initialize(seed_market=False)
    with store.read() as c:
        assert IM._get(c, IM.READY) is True
