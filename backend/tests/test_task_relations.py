"""Every task-to-task relationship lives in task_relations: the table keeps one parent a task and `related` stored once,
the write path takes only related, duplicate_of and follow_up (a parent is set when a sub-task is filed, and no task
waits on a relation), and a bot's run sees the relations it may read and nothing of the ones it may not."""

import sqlite3

import pytest

from backend import hubdb as H
from backend import task_relations as TR
from backend.tests.helpers.api import assign, claim, headers, post, ready, runner  # noqa: F401  (fixtures)
from backend import migrations
from backend.tests.helpers.upgrade import boot


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


def relations_schema():
    """task_relations and its indexes as a new database has them (backend/migrations/baseline.sql)."""
    new = sqlite3.connect(":memory:", isolation_level=None)
    try:
        migrations.apply(new)
        return [
            row[0]
            for row in new.execute(
                "SELECT sql FROM sqlite_master WHERE tbl_name='task_relations' AND type IN ('table','index') "
                "AND sql IS NOT NULL ORDER BY type DESC"
            )
        ]
    finally:
        new.close()


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
        for statement in relations_schema():
            c.execute(statement)
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
