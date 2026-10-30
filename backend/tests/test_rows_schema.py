"""backend/rows.py mirrors the hub database: each TypedDict has exactly its table's columns, and a column is optional
there only when SQLite may leave it NULL. A migration that adds or changes a column fails here until rows.py follows."""

import typing
from contextlib import closing

import pytest

from backend import rows
from backend.config import Settings
from backend.store import Store

TABLES = {
    rows.TaskRow: "tasks",
    rows.AttemptRow: "attempts",
    rows.RunnerRow: "runners",
    rows.ApprovalRow: "approvals",
    rows.AttemptEventRow: "attempt_events",
}


@pytest.fixture(scope="module")
def columns(tmp_path_factory):
    """Each table's columns from a freshly initialized hub database: name, nullable, and primary key."""
    tmp = tmp_path_factory.mktemp("rows-schema")
    store = Store(Settings(db_path=tmp / "hub.db", registry_dir=tmp))
    store.initialize(seed_market=False)
    with closing(store.connect()) as c:
        return {table: {name: (notnull == 0 and pk == 0) for _, name, _, notnull, _, pk in
                        c.execute(f"PRAGMA table_info({table})")}
                for table in TABLES.values()}


@pytest.mark.parametrize("shape,table", TABLES.items(), ids=TABLES.values())
def test_row_keys_match_table_columns(shape, table, columns):
    assert set(shape.__annotations__) == set(columns[table])


@pytest.mark.parametrize("shape,table", TABLES.items(), ids=TABLES.values())
def test_optional_fields_are_the_nullable_columns(shape, table, columns):
    hints = typing.get_type_hints(shape)
    optional = {name for name, hint in hints.items() if type(None) in typing.get_args(hint)}
    assert optional == {name for name, nullable in columns[table].items() if nullable}
