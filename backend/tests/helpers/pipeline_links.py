"""Pipeline link helpers that other tests share, moved out of test_pipeline_links.py so they import no test file."""

import sqlite3
from contextlib import closing

from backend import hubdb as H
from backend.tests.helpers.pipeline_foundation import ANA, boot, hub


def two(tmp_path, booted=None):
    """Two pipelines on a fresh hub: a copy of the module's boot, or (from other modules) a boot of its own."""
    path = tmp_path / "hub.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    if booted is None:
        boot(path)
    else:
        with closing(sqlite3.connect(booted)) as source, closing(sqlite3.connect(path)) as target:
            source.backup(target)
    c = hub(path)
    sales = H.type_create(
        c,
        ANA,
        "Sales",
        [
            {"name": "Lead", "status": "open"},
            {"name": "Won", "status": "done"},
            {"name": "Lost", "status": "closed"},
        ],
        mover=True,
    )
    success = H.type_create(
        c,
        ANA,
        "Success",
        [{"name": "Onboarding", "status": "doing"}, {"name": "Active", "status": "doing"}],
        mover=True,
    )
    other = H.type_create(c, ANA, "Support", [{"name": "Inbox", "status": "open"}], mover=True)
    for t in (sales, success, other):
        c.execute("UPDATE task_types SET pipeline_owner_id=? WHERE id=?", (ANA, t["id"]))
    return c, sales, success, other
