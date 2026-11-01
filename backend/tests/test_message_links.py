"""The message-to-task index (backend/message_links.py): a task's page reads only the messages that could be
about it, and shows exactly what reading the whole room showed."""
import json
import random
import sqlite3
from types import SimpleNamespace

import pytest

from backend import message_links, task_privacy
from backend.config import Settings
from backend.store import H, Store

READERS = ("human:ana", "human:ben")
KINDS = ("refs", "reply", "answer", "input", "carried", "moved", "job", "read", "conversation")


def store(tmp_path):
    s = Store(Settings(db_path=tmp_path / "hub.sqlite"))
    s.initialize(seed_market=False)
    with s.transaction() as c:
        c.execute("INSERT OR IGNORE INTO humans(id,name) VALUES('ana','Ana'),('ben','Ben')")
        c.execute("INSERT OR IGNORE INTO bots(slug,state) VALUES('ops','active')")
        c.execute("INSERT INTO runners(id,label,operator,token_hash,created_at) VALUES('r1','r','ana','h','t')")
    return s


class World:
    """A synthetic hub: tasks, rooms and messages linked to tasks in every way `message_tasks` follows."""

    def __init__(self, s, seed, kinds=KINDS):
        self.s, self.rng, self.kinds, self.n = s, random.Random(seed), kinds, 0
        self.made = {kind: [] for kind in KINDS}
        with s.transaction() as c:
            self.tasks = []
            for i in range(5):
                tid = f"task-{seed}-{i}"
                requester, owner = self.rng.choice(READERS), self.rng.choice(READERS + ("bot:ops",))
                c.execute("INSERT INTO tasks(id,title,requester,owner,status,private,created_at,updated_at) "
                          "VALUES(?,?,?,?,'doing',?,'t','t')", (tid, tid, requester, owner, self.rng.random() < 0.5))
                self.tasks.append(tid)
            self.rooms = [self.room(c) for _ in range(3)]
            self.task_room = self.room(c, self.tasks[0])
            for tid in self.tasks:
                c.execute("UPDATE tasks SET conversation_id=? WHERE id=?",
                          (self.task_room if tid == self.tasks[0] else self.rng.choice(self.rooms), tid))
            self.messages, self.attempts = [], []

    def id(self, prefix):
        self.n += 1
        return f"{prefix}-{self.n}"

    def room(self, c, task_id=None):
        cid = self.id("conv")
        c.execute("INSERT INTO conversations(id,kind,task_id,participants,created_at,scope) VALUES(?,?,?,?,?,?)",
                  (cid, "chat", task_id, json.dumps(["human:ana", "human:ben", "bot:ops"]), "t", "shared"))
        return cid

    def say(self, c, refs=None, reply=None, cid=None):
        mid = self.id("msg")
        c.execute("INSERT INTO messages(id,conversation_id,from_actor,to_actor,kind,body,refs,in_reply_to,created_at) "
                  "VALUES(?,?,?,?,?,?,?,?,?)", (mid, cid or self.rng.choice(self.rooms), "human:ana", "human:ben",
                                                self.rng.choice(("chat", "say", "ask", "answer")),
                                                "body " + mid, json.dumps(refs or {}), reply, f"2026-01-01T{self.n:08d}"))
        self.messages.append(mid)
        return mid

    def run(self, c, message):
        jid, aid = self.id("job"), self.id("attempt")
        c.execute("INSERT INTO jobs(id,message_id,bot,state,created_at) VALUES(?,?,'ops','done','t')", (jid, message))
        c.execute("INSERT INTO attempts(id,job_id,bot,runner_id,generation,token_hash,state,lease_until,created_at) "
                  "VALUES(?,?,'ops','r1',1,?,'done','t','t')", (aid, jid, aid))
        self.attempts.append(aid)
        return aid

    def reference(self, tid):
        return self.rng.choice([{"task": tid}, {"task_id": [tid]}, {"ref": "task:" + tid},
                                {"url": f"https://hub/#/task/{tid}"}, {"nested": [{"deep": tid}]},
                                {"blob": json.dumps({"a": tid})}, {"task": " " + tid + " "}])

    def step(self, c):
        kind, tid, earlier = self.rng.choice(self.kinds), self.rng.choice(self.tasks), self.messages[-12:]
        report = lambda aid: self.rng.choice([{"turn_id": aid}, {"run": {"attempt_id": aid}}])
        if kind == "refs" or not earlier:
            mid = self.say(c, self.reference(tid))
        elif kind == "reply":
            mid = self.say(c, reply=self.rng.choice(earlier))
        elif kind in ("answer", "input"):
            mid = self.say(c, {kind + "s": self.rng.sample(earlier, min(2, len(earlier)))})
        elif kind == "carried":
            aid = self.run(c, self.say(c))
            mid = self.say(c, report(aid))
            c.execute("UPDATE tasks SET carried_by=? WHERE id=?", (aid, tid))
        elif kind == "moved":
            aid = self.run(c, self.say(c))
            mid = self.say(c, report(aid))
            H.event(c, H.KEEPER, task_privacy.INPUT_MOVED, aid, {"message_id": "gone", "tasks": [tid]})
        elif kind == "job":
            free = [m for m in earlier if not c.execute("SELECT 1 FROM jobs WHERE message_id=?", (m,)).fetchone()]
            aid = self.run(c, self.rng.choice(free) if free else self.say(c, self.reference(tid)))
            mid = self.say(c, report(aid))
        elif kind == "read":
            aid = self.run(c, self.say(c))
            c.execute("INSERT OR IGNORE INTO attempt_inputs(attempt_id,message_id) VALUES(?,?)",
                      (aid, self.rng.choice(earlier)))
            mid = self.say(c, report(aid))
        else:
            mid = self.say(c, cid=self.task_room)
        self.made[kind].append(mid)

    def change(self, c):
        """Edits, deletes, moves and re-links after the fact: every write path a link has to follow."""
        mid = self.rng.choice(self.messages)
        what = self.rng.choice(("edit", "unlink", "delete", "comment-delete", "reparent", "move", "relink-room",
                                "late-carry", "late-move", "purge"))
        if what == "edit":
            c.execute("UPDATE messages SET refs=?, edited_at='t' WHERE id=?",
                      (json.dumps(self.reference(self.rng.choice(self.tasks))), mid))
        elif what == "unlink":
            c.execute("UPDATE messages SET refs='{}' WHERE id=?", (mid,))
        elif what == "delete":
            c.execute("UPDATE messages SET deleted_at='t' WHERE id=?", (mid,))
        elif what == "comment-delete":
            c.execute("UPDATE messages SET body='',refs=?,deleted_at='t' WHERE id=?",
                      (json.dumps({"task": self.rng.choice(self.tasks), "comment": True}), mid))
        elif what == "reparent":
            c.execute("UPDATE messages SET in_reply_to=? WHERE id=?", (self.rng.choice(self.messages), mid))
        elif what == "move":
            c.execute("UPDATE messages SET conversation_id=? WHERE id=?", (self.rng.choice(self.rooms), mid))
        elif what == "relink-room":
            c.execute("UPDATE conversations SET task_id=? WHERE id=?",
                      (self.rng.choice(self.tasks + [None]), self.rng.choice(self.rooms[1:] + [self.task_room])))
        elif what == "late-carry" and self.attempts:
            c.execute("UPDATE tasks SET carried_by=? WHERE id=?", (self.rng.choice(self.attempts), self.rng.choice(self.tasks)))
        elif what == "late-move" and self.attempts:
            H.event(c, H.KEEPER, task_privacy.INPUT_MOVED, self.rng.choice(self.attempts),
                    {"tasks": [self.rng.choice(self.tasks)]})
        elif what == "purge" and not c.execute(
                "SELECT 1 FROM jobs WHERE message_id=? UNION SELECT 1 FROM attempt_inputs WHERE message_id=?",
                (mid, mid)).fetchone():
            c.execute("DELETE FROM messages WHERE id=?", (mid,))
            self.messages.remove(mid)
            # A reply to a purged message keeps its id; message_tasks then finds nothing there.


def pages(s, cid, tid, reader, limit):
    """Every page of a task's messages in one room, walking `before` cursors."""
    who, out, before = SimpleNamespace(actor=reader, task_actor=""), [], None
    with s.read_transaction() as c:
        while True:
            page = task_privacy.page(c, who, cid, task_id=tid, before=before, limit=limit)
            out.append([(m["id"], m["refs"]) for m in page["messages"]] + [page["has_more"]])
            if not page["has_more"]:
                return out
            before = page["next_before"]


def compare(s, world, monkeypatch):
    """The indexed pages equal the whole-room pages for every task, room, reader and page size. Returns the
    messages shown on some task's page that do not name that task themselves."""
    real, used, inherited = message_links.candidates, [], set()
    real_named = message_links.named

    def counted(c, tid, cid):
        found = real(c, tid, cid)
        used.append(found is not None)
        return found

    for cid in world.rooms + [world.task_room]:
        for tid in world.tasks + ["task-none"]:
            for reader in READERS:
                for limit in (200, 3):
                    monkeypatch.setattr(message_links, "candidates", lambda c, t, i: None)
                    whole = pages(s, cid, tid, reader, limit)
                    monkeypatch.setattr(message_links, "candidates", counted)
                    assert pages(s, cid, tid, reader, limit) == whole, (cid, tid, reader, limit)
                    if limit == 200:
                        inherited.update(mid for mid, refs in whole[0][:-1] if tid not in refs)
    assert any(used)
    # A task's comments: the messages naming it, from its room.
    for tid in world.tasks:
        for reader in READERS:
            with s.read_transaction() as c:
                monkeypatch.setattr(message_links, "named", lambda c, t, i: None)
                whole = H.task_comments(c, tid, actor=reader)
                monkeypatch.setattr(message_links, "named", real_named)
                assert H.task_comments(c, tid, actor=reader) == whole, (tid, reader)
    return inherited


def build(s, seed, kinds=KINDS, steps=60, changes=25, unrefreshed=False):
    world = World(s, seed, kinds)
    with s.transaction() as c:
        for _ in range(steps):
            world.step(c)
    for _ in range(changes):
        # `unrefreshed`: writes that skip Store.transaction's refresh. The triggers mark them, and reads take
        # their links from their rows.
        with (s.read_transaction() if unrefreshed else s.transaction()) as c:
            world.change(c)
    if unrefreshed:
        with s.read() as c:
            assert c.execute("SELECT 1 FROM message_links_dirty").fetchone()
    return world


@pytest.mark.slow
@pytest.mark.parametrize("seed", range(40))
def test_indexed_task_pages_equal_whole_room_pages(tmp_path, monkeypatch, seed):
    """The acceptance gate: random rooms with every link kind, then edits, deletes, moves and re-links; every
    fourth seed's changes skip Store.transaction's refresh."""
    s = store(tmp_path)
    world = build(s, seed, unrefreshed=seed % 4 == 3)
    compare(s, world, monkeypatch)


@pytest.mark.slow
def test_every_link_kind_puts_messages_on_a_task_page_across_the_seeds(tmp_path, monkeypatch):
    """Across seeds, each kind shows some message on a task's page that does not name the task itself."""
    shown = set()
    for seed in range(12):
        s = store(tmp_path / str(seed))
        world = build(s, 1000 + seed, changes=0)
        inherited = compare(s, world, monkeypatch)
        shown.update(kind for kind, mids in world.made.items() if inherited & set(mids))
    assert set(KINDS) - {"refs"} <= shown, shown


def test_each_link_kind_with_a_fixed_seed(tmp_path, monkeypatch):
    """One fixed case per kind: a message that does not name the task reaches its page only through that kind."""
    for kind in KINDS:
        s = store(tmp_path / kind)
        world = build(s, 7, kinds=("refs", kind), steps=30, changes=0)
        inherited = compare(s, world, monkeypatch)
        if kind == "refs":
            with s.read() as c:
                named = {r[0] for r in c.execute("SELECT target FROM message_links WHERE kind='task'")}
            assert set(world.tasks) <= named
        else:
            assert inherited & set(world.made[kind]), kind


def test_an_edit_and_a_delete_move_a_message_on_and_off_a_task_page(tmp_path, monkeypatch):
    s = store(tmp_path)
    world = World(s, 3, kinds=("refs",))
    first, second = world.tasks[:2]
    with s.transaction() as c:
        c.execute("UPDATE tasks SET private=0")
        cid = world.rooms[0]
        mid = world.say(c, {"task": first}, cid=cid)
        reply = world.say(c, reply=mid, cid=cid)
    ids = lambda tid: [m for m, _ in pages(s, cid, tid, "human:ana", 200)[0][:-1]]
    assert ids(first) == [mid, reply] and ids(second) == []
    with s.transaction() as c:
        c.execute("UPDATE messages SET refs=? WHERE id=?", (json.dumps({"task": second}), mid))
    assert ids(first) == [] and ids(second) == [mid, reply]
    with s.transaction() as c:
        c.execute("UPDATE messages SET body='',deleted_at='t' WHERE id=?", (mid,))
    assert ids(second) == [reply]
    with s.transaction() as c:
        c.execute("DELETE FROM messages WHERE id=?", (reply,))
        assert not c.execute("SELECT 1 FROM message_links WHERE message_id=?", (reply,)).fetchone()
    assert ids(second) == []
    with s.read() as c:
        assert not c.execute("SELECT 1 FROM message_links_dirty").fetchone()
        # Every task page asks for the run events naming the task: by their own index, covering their detail, so the
        # search reads no event rows.
        plan = " ".join(r[3] for r in c.execute(
            "EXPLAIN QUERY PLAN SELECT target FROM events WHERE action IN (" + task_privacy.RUN_TASK_EVENTS_SQL
            + ") AND instr(detail, ?)>0", (first,)))
        assert "COVERING INDEX events_run_detail" in plan, plan
        # The task's own room, and an id the links never keep, are read whole.
        assert message_links.candidates(c, world.tasks[0], world.task_room) is None
        assert message_links.candidates(c, "not an id", cid) is None
    compare(s, world, monkeypatch)


def test_a_write_outside_store_transaction_is_read_from_its_row_until_the_next_tick(tmp_path, monkeypatch):
    s = store(tmp_path)
    world = World(s, 4, kinds=("refs",))
    first, second = world.tasks[:2]
    with s.transaction() as c:
        c.execute("UPDATE tasks SET private=0")
        cid = world.rooms[0]
        mid = world.say(c, {"task": first}, cid=cid)
        reply = world.say(c, reply=mid, cid=cid)
    ids = lambda tid: [m for m, _ in pages(s, cid, tid, "human:ana", 200)[0][:-1]]
    with s.read_transaction() as c:
        c.execute("UPDATE messages SET refs=? WHERE id=?", (json.dumps({"task": second}), mid))
        orphan = world.say(c, reply=mid, cid=cid)
    with s.read() as c:
        assert {r[0] for r in c.execute("SELECT message_id FROM message_links_dirty")} == {mid, orphan}
        assert c.execute("SELECT 1 FROM message_links WHERE message_id=? AND target=?", (mid, first)).fetchone()
    assert ids(first) == [] and ids(second) == [mid, reply, orphan]
    # The scheduler's tick, every few seconds, is a Store.transaction: it refreshes them.
    from backend.auth import Auth
    from backend.execution import Execution
    from backend.scheduler import Scheduler
    assert not Scheduler(s, Execution(s, Auth(s))).tick()["failures"]
    with s.read() as c:
        assert not c.execute("SELECT 1 FROM message_links_dirty").fetchone()
    assert ids(first) == [] and ids(second) == [mid, reply, orphan]
    compare(s, world, monkeypatch)


def test_backfill_is_batched_idempotent_and_catches_rows_an_older_server_wrote(tmp_path, monkeypatch):
    s = store(tmp_path)
    world = build(s, 11, steps=50, changes=0)
    with s.transaction() as c:
        tail = [world.say(c) for _ in range(10)]
    with s.read() as c:
        before = sorted(map(tuple, c.execute("SELECT * FROM message_links")))
        top = c.execute("SELECT max(rowid) FROM messages").fetchone()[0]
    # An older server, after a rollback: no triggers, so its rows have no links, are not seen and not dirty.
    raw = sqlite3.connect(tmp_path / "hub.sqlite", isolation_level=None)
    raw.row_factory = sqlite3.Row
    for name in ("message_links_insert", "message_links_update", "message_links_delete"):
        raw.execute("DROP TRIGGER " + name)
    unlinked = [r[0] for r in raw.execute("SELECT id FROM messages WHERE id NOT IN (%s) ORDER BY rowid DESC LIMIT 5"
                                          % ",".join("?" * len(tail)), tail)]
    for table in ("message_links", "message_links_seen"):
        raw.execute(f"DELETE FROM {table} WHERE message_id IN (%s)" % ",".join("?" * 5), unlinked)
    # It deletes the newest messages and writes one, which takes a rowid an earlier message had.
    raw.execute("DELETE FROM messages WHERE id IN (%s)" % ",".join("?" * len(tail)), tail)
    tid, old = world.tasks[1], "msg-older"
    raw.execute("UPDATE tasks SET private=0 WHERE id=?", (tid,))
    raw.execute("INSERT INTO messages(id,conversation_id,from_actor,to_actor,kind,body,refs,created_at) "
                "VALUES(?,?,'human:ana','human:ben','chat','b',?,'z')", (old, world.rooms[0], json.dumps({"task": tid})))
    assert raw.execute("SELECT rowid FROM messages WHERE id=?", (old,)).fetchone()[0] < top
    raw.close()
    # The upgrade: migrations put the triggers back and the start backfills every message not seen.
    again = Store(Settings(db_path=tmp_path / "hub.sqlite"))
    again.initialize(seed_market=False)
    with again.read() as c:
        assert c.execute("SELECT 1 FROM message_links WHERE message_id=? AND kind='task' AND target=?",
                         (old, tid)).fetchone()
        after = sorted(map(tuple, c.execute("SELECT * FROM message_links WHERE message_id<>?", (old,))))
    assert after == before
    assert old in [m for m, _ in pages(again, world.rooms[0], tid, "human:ana", 200)[0][:-1]]
    compare(again, world, monkeypatch)
    # From scratch in batches of 7: the same links, every batch commits on its own, and a second pass reads nothing.
    with again.transaction() as c:
        c.execute("DELETE FROM message_links")
        c.execute("DELETE FROM message_links_seen")
        c.execute("DELETE FROM registry_metadata WHERE key=?", (message_links.READY,))
    commits = []
    with again.read() as c:
        assert message_links.candidates(c, tid, world.rooms[0]) is None
        c.set_trace_callback(lambda sql: commits.append(sql) if sql == "COMMIT" else None)
        count = message_links.backfill(c, batch=7, span=25)
        c.set_trace_callback(None)
        assert count == c.execute("SELECT count(*) FROM messages").fetchone()[0]
        assert message_links.backfill(c, batch=7) == 0
        assert len(commits) >= -(-count // 7) + 1
        assert sorted(map(tuple, c.execute("SELECT * FROM message_links WHERE message_id<>?", (old,)))) == before
