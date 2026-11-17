"""Helpers that other tests share, moved out of test_migrations.py so they import no test file."""

import sqlite3
from contextlib import closing
from pathlib import Path
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
    # Close before the next boot inspects WAL and SHM. The connection context alone only ends a transaction.
    with closing(sqlite3.connect(path)) as c:
        return (
            c.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name").fetchall(),
            c.execute("SELECT version,checksum,applied_at FROM schema_migrations ORDER BY version").fetchall(),
        )


def forget_from(c, version):
    """The file as an install from before migration `version` left it: the records of the migration files from that one
    on are forgotten, so they run again on the next start. The files the floor retired (backend/migrations/retired.txt)
    stay recorded: they are gone, and a file without the floor's record is refused."""
    from backend import migrations

    current = [m.version for m in migrations.files() if m.version >= version]
    c.executemany("DELETE FROM schema_migrations WHERE version=?", [(v,) for v in current])
