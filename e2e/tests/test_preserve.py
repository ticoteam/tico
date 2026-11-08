"""The checks that prove an upgrade kept a database (e2e/preserve.py), tested on small databases where the answer is
known: a changed value, a lost row, a value moved to another row, a broken foreign key, a counter that went back."""
import sqlite3

from e2e.preserve import compare, integrity, table_state

NO_RENAMES = {"tables": {}, "columns": {}, "fields": {}}


def make(path, rows, extra=()):
    with sqlite3.connect(path) as c:
        c.execute("CREATE TABLE tasks(id INTEGER PRIMARY KEY, title TEXT)")
        c.executemany("INSERT INTO tasks VALUES(?,?)", rows)
        for statement in extra:
            c.execute(statement)
    return path


def test_an_identical_copy_compares_equal(tmp_path):
    a = make(tmp_path / "a.sqlite", [(1, "Plan"), (2, "Ship")])
    b = make(tmp_path / "b.sqlite", [(1, "Plan"), (2, "Ship")])
    assert compare(table_state(a), table_state(b), NO_RENAMES) == []
    assert integrity(b) == []


def test_a_changed_value_a_lost_row_and_a_moved_value_are_each_reported(tmp_path):
    before = table_state(make(tmp_path / "a.sqlite", [(1, "Plan"), (2, "Ship")]))
    changed = table_state(make(tmp_path / "b.sqlite", [(1, "Plan"), (2, "Launch")]))
    assert compare(before, changed, NO_RENAMES) == ["tasks.title: values changed"]
    lost = table_state(make(tmp_path / "c.sqlite", [(1, "Plan")]))
    assert compare(before, lost, NO_RENAMES) == ["tasks: 2 rows before, 1 after"]
    # The same two titles on the same two ids, but swapped: each column's values are the same, the rows are not.
    moved = table_state(make(tmp_path / "d.sqlite", [(1, "Ship"), (2, "Plan")]))
    assert compare(before, moved, NO_RENAMES) == ["tasks: row contents changed"]


def test_a_new_column_is_the_new_schema_and_is_not_compared(tmp_path):
    before = table_state(make(tmp_path / "a.sqlite", [(1, "Plan")]))
    after = table_state(make(tmp_path / "b.sqlite", [(1, "Plan")], ["ALTER TABLE tasks ADD COLUMN owner_id TEXT"]))
    assert compare(before, after, NO_RENAMES) == []


def test_a_broken_foreign_key_is_reported(tmp_path):
    path = make(tmp_path / "fk.sqlite", [(1, "Plan")], [
        "CREATE TABLE notes(id INTEGER PRIMARY KEY, task_id INTEGER REFERENCES tasks(id))",
        "INSERT INTO notes VALUES(1, 99)"])
    problems = integrity(path)
    assert [kind for kind, _ in problems] == ["foreign_key"]


def test_a_change_counter_may_rise_but_not_go_back(tmp_path):
    def with_counter(name, value):
        path = make(tmp_path / name, [(1, "Plan")], ["CREATE TABLE roster_clock(id INTEGER PRIMARY KEY, value INTEGER)",
                                                       f"INSERT INTO roster_clock VALUES(1,{value})"])
        return table_state(path)
    assert compare(with_counter("a.sqlite", 5), with_counter("b.sqlite", 9), NO_RENAMES) == []
    assert compare(with_counter("c.sqlite", 5), with_counter("d.sqlite", 4), NO_RENAMES) == [
        "roster_clock: change counter went back (5 before, 4 after)"]
