"""Helpers that other tests share, moved out of test_pipeline_foundation.py so they import no test file."""

from pathlib import Path
from backend import hubdb as H
from backend.config import Settings
from backend.store import Store


ANA = "human:ana"


T = "2026-11-07T09:00:00.000000Z"


FIXTURES = Path(__file__).parents[1] / "fixtures"


def boot(path):
    Store(Settings(db_path=path, registry_dir=FIXTURES / "registry")).initialize(seed_market=False)


def hub(path):
    c = H.connect(path)
    H.sync_registry(
        c,
        {},
        {
            "people": [
                {"id": "ana", "email": "ana@acme.example"},
                {"id": "ben", "email": "ben@acme.example"},
                {"id": "chris", "email": "chris@acme.example"},
            ]
        },
    )
    return c


def rows(c, sql, *args):
    return [dict(r) for r in c.execute(sql, args).fetchall()]


def step_id(c, type_id, name):
    return c.execute("SELECT id FROM task_steps WHERE type_id=? AND name=?", (type_id, name)).fetchone()[0]


def insert(c, table, **values):
    columns = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    c.execute(f"INSERT INTO {table} ({columns}) VALUES ({marks})", tuple(values.values()))
