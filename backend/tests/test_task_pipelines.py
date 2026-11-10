"""A database from before pipelines that has tasks, in every legacy status, is refused as it is: Tico 0.6 carries no
task over (docs/dev/pipelines-v3-cutover.md), so the old General backfill, Dev tickets steps and Won't do retirement
are gone with the old engine, and the file keeps every task, status, body and note it had."""

import sqlite3

import pytest

from backend import hubdb as H
from backend.migrations import MigrationError

STATUSES = ("open", "doing", "review", "waiting", "closed", "declined")  # the statuses of the old engine


def test_a_database_from_before_pipelines_with_tasks_in_every_status_is_refused_and_keeps_them(tmp_path):
    path = tmp_path / "legacy.db"
    c = sqlite3.connect(path, isolation_level=None)
    c.row_factory = sqlite3.Row
    for script in H.MIGRATIONS[:13]:
        H._apply(c, script)
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


def test_the_same_database_without_tasks_migrates_to_today(tmp_path):
    c = sqlite3.connect(tmp_path / "legacy.db", isolation_level=None)
    c.row_factory = sqlite3.Row
    for script in H.MIGRATIONS[:13]:
        H._apply(c, script)
    c.execute("PRAGMA user_version=13")
    H.migrate(c)
    assert c.execute("PRAGMA user_version").fetchone()[0] == len(H.MIGRATIONS)
    assert c.execute("PRAGMA foreign_key_check").fetchall() == []
    c.close()
