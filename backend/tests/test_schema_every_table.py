"""An upgraded database has every column under the name the code uses, on every table the code can make.

schema_old_main.sql holds every table an install of the last commit before the renames can hold, including tables made
only on first use, which a dump of a fresh hub misses. One boot upgrades it; every table the source makes (read from the
source, the same way) then has to match a new install, column for column. A table the rename map misses keeps its old
column names on an upgraded install, and the code then fails on it only when that feature is first used.
"""

import re
import sqlite3
from pathlib import Path

import pytest

from backend.config import Settings
from backend.migrations.renames_data import COLUMNS
from backend.store import Store
from backend.tests import schema_source as S

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).parent / "fixtures" / "schema_old_main.sql"
# Old tables a current install no longer makes: nothing reads them, an upgrade leaves them in place.
LEFT_BEHIND = set()
OLD_LOOKING = re.compile(r"^(created|updated|started|finished|expires|last_seen|first_seen|\w+_json)$")


@pytest.fixture(scope="module")
def shapes(tmp_path_factory):
    creates, columns = S.statements(ROOT)
    tmp = tmp_path_factory.mktemp("every-table")
    new_path = tmp / "new.sqlite"
    Store(Settings(db_path=new_path)).initialize()
    conn = sqlite3.connect(new_path)
    S.add_lazy_tables(conn, creates, columns)
    new = S.table_columns(conn)
    conn.close()
    old_path = tmp / "old.sqlite"
    conn = sqlite3.connect(old_path)
    conn.executescript(FIXTURE.read_text())
    before = S.table_columns(conn)
    conn.close()
    Store(Settings(db_path=old_path)).initialize()
    conn = sqlite3.connect(old_path)
    S.add_lazy_tables(conn, creates, columns)
    upgraded = S.table_columns(conn)
    conn.close()
    return before, upgraded, new


def test_the_fixture_has_the_tables_made_on_first_use(shapes):
    before, _, _ = shapes
    for table in ("slack_credentials", "github_app_states", "directory_credentials", "check_head_provenance"):
        assert table in before


def test_every_table_of_an_upgraded_database_has_the_columns_of_a_new_one(shapes):
    _, upgraded, new = shapes
    differ = {}
    for table in sorted(set(upgraded) | set(new)):
        if table in LEFT_BEHIND:
            continue
        have, want = upgraded.get(table), new.get(table)
        if have != want:
            differ[table] = {"only_upgraded": sorted((have or set()) - (want or set())),
                             "only_new": sorted((want or set()) - (have or set())),
                             "table_missing": "upgraded" if have is None else "new" if want is None else ""}
    assert not differ


def test_no_old_column_name_survives_an_upgrade(shapes):
    _, upgraded, _ = shapes
    renamed = {(table, old) for table, old, _new, _wave in COLUMNS}
    left = sorted(f"{t}.{c}" for t, cols in upgraded.items() if t not in LEFT_BEHIND for c in cols
                  if (t, c) in renamed or OLD_LOOKING.match(c))
    assert not left
