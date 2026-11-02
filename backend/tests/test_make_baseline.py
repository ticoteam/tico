"""scripts/cleanup/make_baseline.py: a database built from its 0001_baseline.sql candidate has the schema of one built by
every migration and module schema call (tables, columns, indexes, triggers, views)."""

import importlib.util
import sqlite3
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "cleanup" / "make_baseline.py"


def _script():
    spec = importlib.util.spec_from_file_location("make_baseline", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _schema(conn):
    objects = set(conn.execute("SELECT type, name, tbl_name, sql FROM sqlite_master").fetchall())
    columns = {name: conn.execute(f'PRAGMA table_info("{name}")').fetchall()
               for kind, name, _, _ in objects if kind == "table"}
    return objects, columns, conn.execute("PRAGMA user_version").fetchone()[0]


def test_a_database_built_from_the_baseline_matches_one_built_by_the_migrations(tmp_path):
    make = _script()
    migrated = tmp_path / "migrated.sqlite"
    make.build(migrated)
    with sqlite3.connect(migrated) as conn:
        sql = make.baseline(conn)
        expected = _schema(conn)
    built = sqlite3.connect(tmp_path / "baseline.sqlite")
    built.executescript(sql)
    objects, columns, version = _schema(built)
    built.close()
    assert version == expected[2]
    assert objects == expected[0]
    assert columns == expected[1]
    kinds = {kind for kind, *_ in objects}
    assert {"table", "index", "trigger"} <= kinds
