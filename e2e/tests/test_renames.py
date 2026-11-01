"""The rename map the e2e kinds read (e2e/renames.py): its shape is checked, and a rename applied to a golden or a
before-state makes the comparison pass only when the new names are the ones the code now uses."""
import pytest

from e2e import renames
from e2e.preserve import _combine, _digest, compare


def test_a_map_takes_the_three_sections_and_nothing_else(tmp_path):
    path = tmp_path / "map.yaml"
    path.write_text("fields: {task_id: work_id}\ncolumns: {\"tasks.owner\": owner_actor}\ntables: {bots: bot_rows}\n")
    assert renames.load(path) == {"fields": {"task_id": "work_id"}, "columns": {"tasks.owner_id": "owner_actor"},
                                  "tables": {"bots": "bot_rows"}}
    empty = tmp_path / "empty.yaml"
    empty.write_text("")
    assert renames.load(empty) == renames.EMPTY


@pytest.mark.parametrize("text", ["colums: {a: b}\n", "fields: {a: a}\n", "fields: {a: ''}\n", "tables: {a: 3}\n"])
def test_a_malformed_map_is_refused(tmp_path, text):
    path = tmp_path / "bad.yaml"
    path.write_text(text)
    with pytest.raises(ValueError):
        renames.load(path)


def test_fields_are_renamed_at_every_depth_and_values_are_left_alone():
    body = {"task_id": "t1", "rows": [{"task_id": "t2", "note": "task_id"}], "nested": {"task_id": {"task_id": 1}}}
    assert renames.apply_fields(body, {"task_id": "work_id"}) == {
        "work_id": "t1", "rows": [{"work_id": "t2", "note": "task_id"}],
        "nested": {"work_id": {"work_id": 1}}}
    assert renames.apply_fields(body, {}) is body


def test_the_environment_map_is_read_when_a_test_runs_alone(tmp_path, monkeypatch):
    path = tmp_path / "map.yaml"
    path.write_text("tables: {old_name: new_name}\n")
    monkeypatch.setenv(renames.ENV, str(path))
    assert renames.active()["tables"] == {"old_name": "new_name"}
    monkeypatch.delenv(renames.ENV)
    assert renames.active() == renames.EMPTY


def _state(rows, columns=("id", "title")):
    """A before/after table state as e2e.preserve.table_state makes it, for one table with the given rows."""
    cells = [{c: _digest(row[i]) for i, c in enumerate(columns)} for row in rows]
    return {"rows": len(rows), "cells": cells,
            "columns": {c: _combine([cell[c] for cell in cells]) for c in columns}}


def test_a_renamed_column_passes_with_the_column_map_and_fails_without_it():
    before = {"tasks": _state([(1, "Plan"), (2, "Ship")])}
    after = {"tasks": _state([(1, "Plan"), (2, "Ship")], columns=("id", "heading"))}
    assert compare(before, after, {"columns": {"tasks.title": "heading"}, "fields": {}, "tables": {}}) == []
    assert compare(before, after, renames.EMPTY) != []


def test_a_renamed_table_passes_with_the_table_map_and_fails_without_it():
    before = {"bots": _state([(1, "Ops")])}
    after = {"bot_rows": _state([(1, "Ops")])}
    assert compare(before, after, {"columns": {}, "fields": {}, "tables": {"bots": "bot_rows"}}) == []
    assert compare(before, after, renames.EMPTY) == ["bots: table is gone (expected bots)"]
