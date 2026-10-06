"""Every task-to-task relationship lives in task_relations: the migration moves parent_id and blocked_by
into it and drops both columns, the table keeps one parent a task and `related` stored once, and a
bot's run sees the relations it may read and nothing of the ones it may not."""
import gzip
import shutil
import sqlite3

import pytest

from backend import hubdb as H
from backend import task_relations as TR
from backend.tests.test_api import api, assign, claim, post, ready, runner  # noqa: F401  (fixtures)
from backend.tests.test_upgrade import FIXTURES, boot


def hub(path):
    c = H.connect(path)
    H.sync_registry(c, {"cmo": {"name": "cmo", "runtime": "fake", "status": "active"}},
                    {"people": [{"id": "ana", "email": "ana@acme.example"}]})
    return c


def columns(c, table="tasks"):
    return {row[1] for row in c.execute(f"PRAGMA table_info({table})")}


def old_columns(c):
    """Put a database back the way the previous release kept a task's parent and blocker."""
    c.execute("ALTER TABLE tasks ADD COLUMN parent_id TEXT")
    c.execute("ALTER TABLE tasks ADD COLUMN blocked_by TEXT")
    c.execute("CREATE INDEX tasks_parent ON tasks(parent_id)")
    c.execute("CREATE INDEX tasks_blocked_by ON tasks(blocked_by)")


def test_the_migration_backfills_both_columns_drops_them_and_runs_twice(tmp_path):
    path = tmp_path / "hub.db"
    c = hub(path)
    parent = H.task_create(c, "human:ana", "Plan the launch", "", "bot:cmo", lint=False)
    child = H.task_create(c, "human:ana", "Write the launch post", "", "bot:cmo", lint=False)
    blocker = H.task_create(c, "human:ana", "Pick the launch date", "", "bot:cmo", lint=False)
    orphan = H.task_create(c, "human:ana", "Pick the photo", "", "bot:cmo", lint=False)
    c.execute("DELETE FROM task_relations")
    old_columns(c)
    c.execute("UPDATE tasks SET parent_id=?, blocked_by=? WHERE id=?", (parent["id"], blocker["id"], child["id"]))
    c.execute("UPDATE tasks SET parent_id='gone' WHERE id=?", (orphan["id"],))     # a parent purged long ago
    c.execute("PRAGMA user_version=30")
    c.close()
    c = H.connect(path)
    assert c.execute("PRAGMA user_version").fetchone()[0] == len(H.MIGRATIONS)
    assert not {"parent_id", "blocked_by"} & columns(c)
    rows = {tuple(r) for r in c.execute("SELECT from_task,to_task,kind FROM task_relations")}
    assert rows == {(child["id"], parent["id"], "parent"), (blocker["id"], child["id"], "blocks")}
    assert TR.parent_of(c, child["id"]) == parent["id"] and TR.blocker_ids(c, child["id"]) == [blocker["id"]]
    H.migrate(c)
    TR.migrate(c)
    assert {tuple(r) for r in c.execute("SELECT from_task,to_task,kind FROM task_relations")} == rows
    assert c.execute("PRAGMA foreign_key_check").fetchall() == []
    c.close()


def test_a_previous_release_database_upgrades_through_the_cloud_store(tmp_path):
    path = tmp_path / "hub.sqlite"
    with gzip.open(FIXTURES / "hub-v0.2.1.sqlite.gz") as source, open(path, "wb") as target:
        shutil.copyfileobj(source, target)
    with sqlite3.connect(path) as c:
        ids = [r[0] for r in c.execute("SELECT id FROM tasks ORDER BY created, id")]
        c.execute("UPDATE tasks SET parent_id=? WHERE id=?", (ids[0], ids[1]))
    boot(path)
    boot(path)
    with sqlite3.connect(path) as c:
        assert not {"parent_id", "blocked_by"} & columns(c)
        assert c.execute("SELECT from_task,to_task,kind FROM task_relations").fetchall() == [(ids[1], ids[0], "parent")]
        assert c.execute("SELECT 1 FROM cloud_migrations WHERE version=60").fetchone()


def test_the_table_keeps_one_parent_and_related_stored_once(tmp_path):
    c = hub(tmp_path / "hub.db")
    a, b, d = (H.task_create(c, "human:ana", f"Write part {n}", "", "bot:cmo", lint=False)["id"] for n in "abc")
    insert = "INSERT INTO task_relations(from_task,to_task,kind,created) VALUES(?,?,?,'now')"
    c.execute(insert, (a, b, "parent"))
    with pytest.raises(sqlite3.IntegrityError):
        c.execute(insert, (a, d, "parent"))
    with pytest.raises(sqlite3.IntegrityError):
        c.execute(insert, (max(b, d), min(b, d), "related"))
    with pytest.raises(sqlite3.IntegrityError):
        c.execute(insert, (a, a, "blocks"))
    with pytest.raises(sqlite3.IntegrityError):
        c.execute(insert, (a, b, "sibling"))
    # Through the write path a second parent moves the task; it never has two.
    TR.relate(c, "human:ana", a, d, "parent", mover=True)
    assert TR.parent_of(c, a) == d and TR.child_ids(c, b) == []
    with pytest.raises(H.Refused):
        TR.relate(c, "human:ana", d, a, "parent", mover=True)             # its own ancestor
    TR.relate(c, "human:ana", b, a, "blocks", mover=True)
    TR.relate(c, "human:ana", d, a, "blocks", mover=True)
    assert TR.blocker_ids(c, a) == [b, d]
    with pytest.raises(H.Refused):
        TR.relate(c, "human:ana", a, b, "blocks", mover=True)             # b already blocks a


def test_a_bot_run_sees_the_relations_it_may_read_and_nothing_of_the_rest(api):
    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    secret = post(api, "tasks", {"owner": "cara", "title": "Secret salary review", "body": "x", "private": True},
                  token="ben-test")
    blocker = post(api, "tasks", {"owner": "cpo", "title": "Write the refund copy", "body": "x"})
    task = post(api, "tasks", {"owner": "ops", "title": "Ship the refund page", "body": "x",
                               "relations": [{"task": blocker["id"], "kind": "blocked_by"}]})
    post(api, "tasks/" + secret["id"] + "/relations", {"task": task["id"]}, token="ben-test")
    attempt = claim(api, r, "ops")
    assert attempt["task"]["id"] == task["id"]
    assert attempt["task"]["relations"] == [{"kind": "blocks", "direction": "in", "id": blocker["id"],
                                             "title": "Write the refund copy", "status": "open"}]
    assert secret["id"] not in str(attempt) and "Secret salary" not in str(attempt)
