"""Helpers that other tests share, moved out of test_migrations.py so they import no test file."""

import sqlite3
from pathlib import Path
from backend import bot_settings as BS
from backend.config import Settings
from backend.store import Store


FIXTURES = Path(__file__).parents[1] / "fixtures"


def boot(path, **kw):
    Store(Settings(db_path=path, registry_dir=FIXTURES / "registry")).initialize(seed_market=False, **kw)


def fresh(tmp_path):
    path = tmp_path / "hub.sqlite"
    boot(path)
    return path


def snapshot(path):
    with sqlite3.connect(path) as c:
        return (
            c.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name").fetchall(),
            c.execute("SELECT version,checksum,applied_at FROM schema_migrations ORDER BY version").fetchall(),
        )


def restore_config(c):
    """Before migration 0070, the migrations 0043 and 0061 read bot_config.config, which 0070 drops: a rerun of them from
    before 0070 needs the column back, each declaration copied into it from bot_settings. A no-op while it is there."""
    if "config" in {row[1] for row in c.execute("PRAGMA table_info(bot_config)")}:
        return
    c.execute("ALTER TABLE bot_config ADD COLUMN config TEXT NOT NULL DEFAULT '{}'")
    c.execute(
        f"UPDATE bot_config SET config=coalesce((SELECT {BS.CONFIG} FROM bot_settings s "
        "WHERE s.bot_id=bot_config.bot), '{}')"
    )


# What a file from before the Pipelines v3 cut-over had and the cut-over takes away, that a migration from before it
# reads or indexes unguarded: the approvals table (0057, 0073, 0091) and tasks.type_id/step_id (20261107163623).
LEGACY_APPROVALS = (
    "CREATE TABLE IF NOT EXISTS approvals (id TEXT PRIMARY KEY, message_id TEXT, requested_by_id TEXT, "
    "decision TEXT, decided_at TEXT, created_at TEXT)"
)


def restore_before_cutover(c):
    """Put back, empty, what the cut-over took away, so migrations from before it run again on a file that has had it
    (the cut-over runs again after them and takes it away again). A no-op where it is there."""
    c.execute(LEGACY_APPROVALS)
    columns = {row[1] for row in c.execute("PRAGMA table_info(tasks)")}
    for column in ("type_id", "step_id"):
        if column not in columns:
            c.execute(f"ALTER TABLE tasks ADD COLUMN {column} TEXT")


# The first file of Pipelines v3. The files from it on, but the cut-over, were made with the new engine: a replay of an
# older file never needs them again, and some (an ALTER TABLE ADD COLUMN) cannot run twice.
PIPELINES_V3 = "20261110010000_pipelines_v3"
CUTOVER = "20261110054000_pipelines_v3_cutover"
# The files after the cut-over that add a column to `tasks`, which the cut-over makes again without it: each runs again
# after it (each is safe to run twice), as on an older install upgrading.
AFTER_CUTOVER_ON_TASKS = ("20261113224214_task_human",)


def forget_from(c, version):
    """The file as an install from before migration `version` left it: the records from that one on are forgotten, so
    they run again on the next start, and what the cut-over took away is back for them. The files of Pipelines v3 stay
    recorded, but the cut-over, which runs again after the replayed ones and takes that away again, and the ones after
    it that put a column back on the `tasks` it makes again (AFTER_CUTOVER_ON_TASKS)."""
    c.execute(
        "DELETE FROM schema_migrations WHERE version>=? AND version NOT LIKE 'legacy%' AND (version<? OR version=?)",
        (version, PIPELINES_V3, CUTOVER),
    )
    if version <= CUTOVER:
        marks = ",".join("?" * len(AFTER_CUTOVER_ON_TASKS))
        c.execute(f"DELETE FROM schema_migrations WHERE version IN ({marks})", AFTER_CUTOVER_ON_TASKS)
    restore_before_cutover(c)
