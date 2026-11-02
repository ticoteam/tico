"""The rename map the e2e kinds read (e2e/renames.py): its shape is checked, and a rename applied to a golden or a
before-state makes the comparison pass only when the new names are the ones the code now uses."""
from pathlib import Path

import pytest

from e2e import renames
from e2e.preserve import _combine, _digest, compare


def test_a_map_takes_the_three_sections_and_nothing_else(tmp_path):
    path = tmp_path / "map.yaml"
    path.write_text("fields: {task_id: work_id}\ncolumns: {\"tasks.title\": heading}\ntables: {bots: bot_rows}\n")
    assert renames.load(path) == {"fields": {"task_id": "work_id", "title": "heading"}, "columns": {"tasks.title": "heading"},
                                  "tables": {"bots": "bot_rows"}, "golden_fields": {}, "golden_added": {}}
    empty = tmp_path / "empty.yaml"
    empty.write_text("")
    assert renames.load(empty) == renames.EMPTY


def test_a_column_renames_its_api_field_unless_its_names_went_apart_or_the_api_kept_it(tmp_path):
    path = tmp_path / "map.yaml"
    path.write_text("columns: {a.owner: owner_id, a.ts: created_at, b.ts: measured_at, a.bot: bot_id}\n"
                    "api_kept: [bot]\napi_fields: {ts: created_at}\ngolden_fields: {readings: {ts: measured_at}}\n"
                    "base: abc\nwaves: {}\n")
    loaded = renames.load(path)
    assert loaded["fields"] == {"owner": "owner_id", "ts": "created_at"}
    assert renames.golden_fields(loaded, "readings") == {"owner": "owner_id", "ts": "measured_at"}
    assert renames.golden_fields(loaded, "tasks") == loaded["fields"]


def test_a_field_can_be_named_under_its_parent():
    body = {"bot": 1, "files": [{"bot": 2}], "execution": {"bot": 3}}
    assert renames.apply_fields(body, {".bot": "bot_id", "execution.bot": "bot_id"}) == {
        "bot_id": 1, "files": [{"bot": 2}], "execution": {"bot_id": 3}}


def test_a_field_can_be_left_alone_where_the_object_has_another_key():
    body = [{"bot": 1}, {"bot": 2, "status": {}}]
    assert renames.apply_fields(body, {"bot!status": "bot_id"}) == [{"bot_id": 1}, {"bot": 2, "status": {}}]


def test_a_key_the_answer_gained_is_left_out_only_under_its_parent():
    body = {"owner_name": 1, "rows": [{"owner_name": 2, "id": 3}]}
    assert renames.drop_added(body, ["rows.owner_name"]) == {"owner_name": 1, "rows": [{"id": 3}]}


def test_the_codemod_map_can_be_read():
    assert renames.load(Path(__file__).resolve().parents[2] / "scripts/cleanup/renames.yaml")["fields"]


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
