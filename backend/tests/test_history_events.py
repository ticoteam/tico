"""One history table, `history_events`: rows other than a task's are append-only, and a subject kind the table does
not know is refused."""

import sqlite3
from pathlib import Path

import pytest

from backend.config import Settings
from backend.store import Store

FIXTURES = Path(__file__).parent / "fixtures"


def boot(path):
    Store(Settings(db_path=path, registry_dir=FIXTURES / "registry")).initialize(seed_market=False)


def test_only_a_tasks_rows_can_be_changed_or_removed(tmp_path):
    path = tmp_path / "hub.sqlite"
    boot(path)
    with sqlite3.connect(path) as c:
        c.executemany(
            "INSERT INTO history_events(id, subject_type, subject_id, actor_id, kind, created_at) VALUES(?,?,?,?,?,?)",
            [
                ("h1", "task", "task-1", "bot:ops", "note", "t"),
                ("h2", "goal", "goal-1", "human:ana", "status", "t"),
                ("h3", "strategy", "strategy", "bot:x", "thesis.set", "t"),
            ],
        )
        c.execute("UPDATE history_events SET note='closed' WHERE id='h1'")
        c.execute("DELETE FROM history_events WHERE id='h1'")
        for statement in (
            "UPDATE history_events SET note='x' WHERE id='h2'",
            "DELETE FROM history_events WHERE id='h3'",
        ):
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                c.execute(statement)
        with pytest.raises(sqlite3.IntegrityError):
            c.execute(
                "INSERT INTO history_events(id, subject_type, subject_id, kind, created_at) "
                "VALUES('h4','planet','p','x','t')"
            )
