"""The merge driver for docs/openapi/v2.json (scripts/json_merge.py), run on the committed document: edits from both
sides combine into what the generator writes, and one value changed two ways is a conflict."""

import copy
import importlib.util
import json
from pathlib import Path

from backend import openapi_v2

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("json_merge", ROOT / "scripts" / "json_merge.py")
json_merge = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(json_merge)


def merge(folder, base, ours, theirs):
    """Run the driver as git does (BASE, OURS, THEIRS); returns its exit code and OURS afterwards."""
    folder.mkdir(exist_ok=True)
    names = {}
    for name, text in (("base", base), ("ours", ours), ("theirs", theirs)):
        names[name] = folder / (name + ".json")
        names[name].write_text(text, encoding="utf-8")
    code = json_merge.main([str(names["base"]), str(names["ours"]), str(names["theirs"])])
    return code, names["ours"].read_text(encoding="utf-8")


def first_summary(doc):
    return next(
        (path, method)
        for path, ops in doc["paths"].items()
        for method, op in ops.items()
        if isinstance(op, dict) and "summary" in op
    )


def insert_after(mapping, after, key, value):
    items = []
    for name, item in mapping.items():
        items.append((name, item))
        if name == after:
            items.append((key, value))
    return dict(items)


def schema_with_properties(doc):
    return next(
        (name, schema)
        for name, schema in doc["components"]["schemas"].items()
        if len(schema.get("properties", {})) >= 2
    )


def test_edits_on_both_sides_combine_as_the_generator_writes_them(tmp_path):
    base_text = openapi_v2.COPY.read_text()
    base = json.loads(base_text)
    path, method = first_summary(base)
    ours = copy.deepcopy(base)
    ours["paths"][path][method]["summary"] = "Edited on our side"
    theirs = copy.deepcopy(base)
    theirs["components"]["schemas"]["MergeProbe"] = {"type": "object", "properties": {"x": {"type": "string"}}}
    expected = copy.deepcopy(ours)
    expected["components"]["schemas"]["MergeProbe"] = theirs["components"]["schemas"]["MergeProbe"]
    expected["components"]["schemas"] = dict(sorted(expected["components"]["schemas"].items()))
    rendered = openapi_v2.render(expected)

    ours_text, theirs_text = openapi_v2.render(ours), openapi_v2.render(theirs)
    assert merge(tmp_path / "forward", base_text, ours_text, theirs_text) == (0, rendered)
    assert merge(tmp_path / "swapped", base_text, theirs_text, ours_text) == (0, rendered)


def test_one_value_changed_two_ways_is_a_conflict_and_ours_is_left_alone(tmp_path):
    base_text = openapi_v2.COPY.read_text()
    base = json.loads(base_text)
    path, method = first_summary(base)
    ours = copy.deepcopy(base)
    ours["paths"][path][method]["summary"] = "Ours"
    theirs = copy.deepcopy(base)
    theirs["paths"][path][method]["summary"] = "Theirs"
    ours_text = openapi_v2.render(ours)

    assert merge(tmp_path, base_text, ours_text, openapi_v2.render(theirs)) == (1, ours_text)


def test_keys_added_after_the_same_key_conflict(tmp_path):
    base_text = openapi_v2.COPY.read_text()
    base = json.loads(base_text)
    name, schema = schema_with_properties(base)
    first = next(iter(schema["properties"]))
    ours = copy.deepcopy(base)
    ours["components"]["schemas"][name]["properties"] = insert_after(
        schema["properties"], first, "ours_new", {"type": "string"}
    )
    theirs = copy.deepcopy(base)
    theirs["components"]["schemas"][name]["properties"] = insert_after(
        schema["properties"], first, "theirs_new", {"type": "string"}
    )

    assert merge(tmp_path, base_text, openapi_v2.render(ours), openapi_v2.render(theirs))[0] == 1


def test_keys_added_in_different_places_keep_each_position(tmp_path):
    base_text = openapi_v2.COPY.read_text()
    base = json.loads(base_text)
    name, schema = schema_with_properties(base)
    keys = list(schema["properties"])
    first, last = keys[0], keys[-1]
    ours = copy.deepcopy(base)
    ours["components"]["schemas"][name]["properties"] = insert_after(
        schema["properties"], first, "ours_new", {"type": "string"}
    )
    theirs = copy.deepcopy(base)
    theirs["components"]["schemas"][name]["properties"] = insert_after(
        schema["properties"], last, "theirs_new", {"type": "string"}
    )
    expected = copy.deepcopy(base)
    expected["components"]["schemas"][name]["properties"] = insert_after(
        insert_after(schema["properties"], first, "ours_new", {"type": "string"}),
        last,
        "theirs_new",
        {"type": "string"},
    )

    assert merge(tmp_path, base_text, openapi_v2.render(ours), openapi_v2.render(theirs)) == (
        0,
        openapi_v2.render(expected),
    )
