"""Every task-to-task relationship lives in task_relations: the migration drops the old parent_id and blocked_by
columns (a database that kept them has no tasks), the table keeps one parent a task and `related` stored once, the
write path takes only related, duplicate_of and follow_up (a parent is set when a sub-task is filed, and no task
waits on a relation), and a bot's run sees the relations it may read and nothing of the ones it may not."""

import gzip
import shutil
import sqlite3

import pytest

from backend import hubdb as H
from backend import task_relations as TR
from backend.tests.helpers.api import assign, claim, headers, post, ready, runner  # noqa: F401  (fixtures)
from backend.tests.helpers.upgrade import FIXTURES, boot


def hub(path):
    """A hub as a start leaves it (every module's tables: a task's entry reads them), with one bot and one person."""
    boot(path)
    c = H.connect(path)
    H.sync_registry(
        c,
        {"cmo": {"name": "cmo", "runtime": "fake", "status": "active"}},
        {"people": [{"id": "ana", "email": "ana@acme.example"}]},
    )
    return c


def columns(c, table="tasks"):
    return {row[1] for row in c.execute(f"PRAGMA table_info({table})")}


def old_columns(c):
    """Put a database back the way the previous release kept a task's parent and blocker."""
    c.execute("ALTER TABLE tasks ADD COLUMN parent_id TEXT")
    c.execute("ALTER TABLE tasks ADD COLUMN blocked_by TEXT")
    c.execute("CREATE INDEX tasks_parent ON tasks(parent_id)")
    c.execute("CREATE INDEX tasks_blocked_by ON tasks(blocked_by)")


def test_the_migration_drops_both_old_columns_and_runs_twice(tmp_path):
    """A database that still has `tasks.parent_id` and `tasks.blocked_by` has no tasks (one with tasks from before the
    cut-over is refused), so the step only drops them, with their indexes and Draft #110's related-only table."""
    path = tmp_path / "hub.db"
    c = hub(path)
    old_columns(c)
    c.execute("ALTER TABLE task_relations RENAME TO kept")
    c.execute("CREATE TABLE task_relations(task_id TEXT, related_id TEXT, added_by TEXT, created_at TEXT)")
    c.execute("CREATE INDEX task_relations_related ON task_relations(related_id)")
    c.execute("DROP TABLE kept")
    c.execute("PRAGMA user_version=30")
    c.close()
    c = H.connect(path)
    assert c.execute("PRAGMA user_version").fetchone()[0] == len(H.MIGRATIONS)
    assert not {"parent_id", "blocked_by"} & columns(c)
    assert {"from_task", "to_task", "kind"} <= columns(c, "task_relations")
    indexes = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert not {"tasks_parent", "tasks_blocked_by", "task_relations_related"} & indexes
    assert not columns(c, "task_relations_v110")
    H.migrate(c)
    TR.migrate(c)
    # The table works as a fresh one does.
    parent = H.task_create(c, "human:ana", "Plan the launch", "", "bot:cmo", lint=False)
    child = H.task_create(c, "human:ana", "Write the launch post", "", "bot:cmo", parent_id=parent["id"], lint=False)
    assert TR.parent_of(c, child["id"]) == parent["id"] and TR.child_ids(c, parent["id"]) == [child["id"]]
    assert c.execute("PRAGMA foreign_key_check").fetchall() == []
    c.close()


def test_a_previous_release_database_with_tasks_is_refused_and_one_without_upgrades_through_the_cloud_store(tmp_path):
    """Tico 0.6 carries no task over (docs/dev/pipelines-v3-cutover.md): a v0.2.1 database with tasks is refused as
    it is; the same database without them upgrades, its task columns moved and dropped."""
    from backend.migrations import MigrationError

    path = tmp_path / "hub.sqlite"
    with gzip.open(FIXTURES / "hub-v0.2.1.sqlite.gz") as source, open(path, "wb") as target:
        shutil.copyfileobj(source, target)
    with sqlite3.connect(path) as c:
        ids = [r[0] for r in c.execute("SELECT id FROM tasks ORDER BY created, id")]  # v0.2.1, before the renames
        c.execute("UPDATE tasks SET parent_id=? WHERE id=?", (ids[0], ids[1]))
    before = path.read_bytes()
    # Below the bridge (test_migrations), so this is the legacy import path: adopt_legacy, as manage.migrate_legacy boots.
    with pytest.raises(MigrationError):
        boot(path, adopt_legacy=True)
    assert path.read_bytes() == before
    with sqlite3.connect(path) as c:
        c.execute("PRAGMA foreign_keys=OFF")
        for table in [
            r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND sql LIKE '%task%'")
        ]:
            if "task_id" in columns(c, table) or table == "tasks":
                c.execute(f"DELETE FROM {table}")
    boot(path, adopt_legacy=True)
    boot(path, adopt_legacy=True)
    with sqlite3.connect(path) as c:
        assert not {"parent_id", "blocked_by"} & columns(c)
        assert c.execute("SELECT from_task,to_task,kind FROM task_relations").fetchall() == []
        assert c.execute("SELECT 1 FROM cloud_migrations WHERE version=60").fetchone()


def test_the_table_keeps_one_parent_and_related_stored_once(tmp_path):
    c = hub(tmp_path / "hub.db")
    a, b, d = (H.task_create(c, "human:ana", f"Write part {n}", "", "bot:cmo", lint=False)["id"] for n in "abc")
    insert = "INSERT INTO task_relations(from_task,to_task,kind,created_at) VALUES(?,?,?,'now')"
    c.execute(insert, (a, b, "parent"))
    with pytest.raises(sqlite3.IntegrityError):
        c.execute(insert, (a, d, "parent"))
    with pytest.raises(sqlite3.IntegrityError):
        c.execute(insert, (max(b, d), min(b, d), "related"))
    with pytest.raises(sqlite3.IntegrityError):
        c.execute(insert, (a, a, "blocks"))
    with pytest.raises(sqlite3.IntegrityError):
        c.execute(insert, (a, b, "sibling"))
    # Through the write path: a parent is the task that filed it and never changes; nothing blocks; a relation to
    # itself is refused; `related` is stored once whichever way it is written; a duplicate is one way only.
    with pytest.raises(H.Refused):
        TR.relate(c, "human:ana", a, d, "parent", mover=True)
    assert TR.parent_of(c, a) == b
    with pytest.raises(H.Refused):
        TR.relate(c, "human:ana", b, a, "blocks", mover=True)
    with pytest.raises(H.Refused):
        TR.relate(c, "human:ana", a, a, "related", mover=True)
    assert TR.relate(c, "human:ana", b, d, "related", mover=True)
    assert not TR.relate(c, "human:ana", d, b, "related", mover=True)
    assert c.execute("SELECT count(*) FROM task_relations WHERE kind='related'").fetchone()[0] == 1
    TR.relate(c, "human:ana", a, d, "duplicate_of", mover=True)
    with pytest.raises(H.Refused):
        TR.relate(c, "human:ana", d, a, "duplicate_of", mover=True)  # d is already duplicated by a


def test_a_bot_run_sees_the_relations_it_may_read_and_nothing_of_the_rest(api):
    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    secret = post(
        api, "tasks", {"owner": "cara", "title": "Secret salary review", "body": "x", "private": True}, token="ben-test"
    )
    copy = post(api, "tasks", {"owner": "cpo", "title": "Write the refund copy", "body": "x"})
    task = post(
        api,
        "tasks",
        {
            "owner": "ops",
            "title": "Ship the refund page",
            "body": "x",
            "relations": [{"task": copy["id"], "kind": "related"}],
        },
    )
    post(api, "tasks/" + secret["id"] + "/relations", {"task": task["id"]}, token="ben-test")
    attempt = claim(api, r, "ops")
    assert attempt["task"]["id"] == task["id"], "a relation holds nothing back: no task waits on one"
    assert [(x["kind"], x["id"], x["title"]) for x in attempt["task"]["relations"]] == [
        ("related", copy["id"], "Write the refund copy")
    ]
    assert secret["id"] not in str(attempt) and "Secret salary" not in str(attempt)


def test_sparse_relations_on_a_large_page_do_not_probe_all_task_pairs():
    with sqlite3.connect(':memory:') as c:
        c.row_factory = sqlite3.Row
        c.execute('CREATE TABLE tasks(id TEXT PRIMARY KEY,title TEXT,status TEXT,owner_id TEXT,private INTEGER)')
        ids = [f'task-{i:04}' for i in range(1000)]
        c.executemany(
            'INSERT INTO tasks VALUES(?,?,?,?,?)',
            [(tid, tid, 'open', 'bot:cmo', int(i == 999)) for i, tid in enumerate(ids)],
        )
        c.executescript(TR.SCHEMA)
        c.executemany(
            "INSERT INTO task_relations(from_task,to_task,kind,created_at) VALUES(?,?,'follow_up','now')",
            [(ids[0], ids[600]), (ids[700], ids[1]), (ids[0], ids[999])],
        )
        operations = [0]

        def budget():
            operations[0] += 1000
            return int(operations[0] > 100_000)

        c.set_progress_handler(budget, 1000)
        result = TR.grouped(c, ids[:400], 'private=0')
        assert [r['id'] for r in result[ids[0]]['follow_up']] == [ids[600]]
        assert [r['id'] for r in result[ids[1]]['follow_up']] == [ids[700]]
        assert result[ids[2]] == {}


def test_the_api_takes_a_dependency_until_released(api):
    """`until: released` on a depends_on (task_depends): stored, read back from both ends, and refused on anything else."""
    work = post(api, "tasks", {"owner": "cpo", "title": "Ship the refund page", "body": "x"})
    check = post(api, "tasks", {"owner": "cpo", "title": "Check the refund page in production", "body": "x"})
    path = "tasks/" + check["id"] + "/relations"
    r = api.post(
        "/api/v2/" + path, json={"task": work["id"], "kind": "depends_on", "until": "released"}, headers=headers()
    )
    assert r.status_code == 200, r.text
    assert all(line.endswith("until it is released") for line in r.json()["readback"])
    assert [(x["id"], x["until"]) for x in r.json()["task"]["relations"]["depends_on"]] == [(work["id"], "released")]
    post(api, path, {"task": work["id"], "kind": "depends_on", "until": "merged"}, expected=422)
    post(api, path, {"task": work["id"], "kind": "follow_up", "until": "released"}, expected=422)
