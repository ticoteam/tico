"""A database from before pipelines that has tasks, in every legacy status, is refused as it is: Tico 0.6 carries no
task over (docs/dev/pipelines-v3-cutover.md), and the file keeps every task, status, body and note it had."""

import sqlite3

import pytest

from backend import hubdb as H
from backend.migrations import MigrationError

STATUSES = ("open", "doing", "review", "waiting", "closed", "declined")  # the statuses of the old engine


def test_a_database_from_before_pipelines_with_tasks_in_every_status_is_refused_and_keeps_them(tmp_path):
    path = tmp_path / "legacy.db"
    c = sqlite3.connect(path, isolation_level=None)
    c.row_factory = sqlite3.Row
    # The old engine's task table, as a Tico 0.5 file has it: no schema_migrations record of the floor.
    c.execute("CREATE TABLE tasks(id TEXT PRIMARY KEY, title TEXT, status TEXT, body TEXT, note TEXT)")
    c.execute("PRAGMA user_version=13")
    for status in STATUSES:
        c.execute(
            "INSERT INTO tasks(id,title,status,body,note) VALUES(?,?,?,?,?)",
            (status, "Keep this task", status, "Keep these details", "Keep this note"),
        )
    before = [dict(row) for row in c.execute("SELECT * FROM tasks ORDER BY id")]
    schema = c.execute("SELECT name, sql FROM sqlite_master ORDER BY name").fetchall()
    with pytest.raises(MigrationError, match="does not carry tasks over"):
        H.migrate(c)
    assert c.execute("PRAGMA user_version").fetchone()[0] == 13
    assert [dict(row) for row in c.execute("SELECT * FROM tasks ORDER BY id")] == before
    assert c.execute("SELECT name, sql FROM sqlite_master ORDER BY name").fetchall() == schema
    c.close()
