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
