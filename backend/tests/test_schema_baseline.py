"""A new database and the oldest one this code opens, upgraded, end up with the same schema.

A new file loads the baseline (backend/migrations/baseline.sql) and runs only the migration files written after it; a
database at the compatibility floor (a v0.6.0-rc.1 install, fixtures/schema_floor.sql) runs every file the floor did
not ship. Both then take the same start. If the baseline lacks something the files make, or a file was folded into
the baseline that a floor database never ran, the two differ here: table by table, column by column, and every index,
view and trigger by its SQL. Tables made on first use count too: the fixture holds the floor's, and both sides get
this source's.
"""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from backend import migrations
from backend.config import Settings
from backend.store import Store
from backend.tests import schema_source as S

ROOT = Path(__file__).resolve().parents[2]
FLOOR = Path(__file__).parent / "fixtures" / "schema_floor.sql"


def start(path):
    Store(Settings(db_path=path)).initialize()


def read(path, creates, columns):
    with closing(sqlite3.connect(path)) as conn:
        S.add_lazy_tables(conn, creates, columns)
        conn.commit()
        return S.shape(conn), sorted(r[0] for r in conn.execute("SELECT version FROM schema_migrations"))


@pytest.fixture(scope="module")
def shapes(tmp_path_factory):
    """(new, upgraded, new after a second start): each a (shape, recorded migrations) pair. Built once: three starts."""
    creates, columns = S.statements(ROOT)
    tmp = tmp_path_factory.mktemp("baseline")
    new = tmp / "new.sqlite"
    start(new)
    first = read(new, creates, columns)
    start(new)
    again = read(new, creates, columns)
    old = tmp / "floor.sqlite"
    with closing(sqlite3.connect(old)) as conn:
        conn.executescript(FLOOR.read_text())
    start(old)
    return first, read(old, creates, columns), again


def differences(a, b):
    out = []
    for name in sorted(set(a) | set(b)):
        if a.get(name) != b.get(name):
            out.append(f"{name}: new={a.get(name)!r} upgraded={b.get(name)!r}"[:600])
    return out


def test_a_new_database_has_the_schema_of_the_floor_upgraded(shapes):
    (new, _), (upgraded, _), _ = shapes
    assert not differences(new, upgraded)


def test_both_record_every_migration_file_and_a_second_start_changes_nothing(shapes):
    (new, recorded), (_, upgraded), (again, recorded_again) = shapes
    files = {m.version for m in migrations.files()}
    assert files <= set(recorded) and files <= set(upgraded)
    assert again == new and recorded_again == recorded


def test_a_database_below_the_floor_is_refused_before_anything_is_written(tmp_path):
    """A file with tables but without the floor's migration: an older Tico's, or not Tico's."""
    path = tmp_path / "hub.sqlite"
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(FLOOR.read_text())
        conn.execute("DELETE FROM schema_migrations WHERE version=?", (max(migrations.retired()),))
        conn.commit()
    before = path.read_bytes()
    with pytest.raises(migrations.MigrationError, match="Tico 0.6 does not carry tasks over"):
        start(path)
    assert path.read_bytes() == before


def test_a_new_file_that_fails_its_baseline_is_left_empty(tmp_path, monkeypatch):
    """The baseline loads in one transaction: a failure part way leaves no table, so the next start is a new file."""
    broken = tmp_path / "migrations"
    broken.mkdir()
    text = (migrations.DIRECTORY / migrations.BASELINE).read_text()
    (broken / migrations.BASELINE).write_text(text + "\nCREATE TABLE bots(id TEXT);\n")
    conn = sqlite3.connect(":memory:", isolation_level=None)
    with pytest.raises(sqlite3.OperationalError, match="already exists"):
        migrations.apply(conn, broken)
    assert not conn.execute("SELECT 1 FROM sqlite_master").fetchone()
