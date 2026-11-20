"""The two standard-library schema checks read JSON Schema as jsonschema does, in 2020-12 (MCP's dialect when a schema
names none): the hub's company file actions (backend/company_file/actions.py, copied as is to Tico HQ and the install
bundle, so it takes no dependency) and the MCP tool table's (clients/commands/_mcp.py, run by `tico` on computers that
have only the standard library and PyYAML). jsonschema is the referee here, a test dependency only."""

import itertools
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from backend.company_file.actions import SPEC, schema_error
from clients.commands._mcp import argument_error
from clients.commands._registry import TOOLS

ROOT = Path(__file__).resolve().parents[2]
SCREEN = json.loads((ROOT / "contracts/screen-actions.json").read_text())["actions"]
HUB_KEYWORDS = {"type", "properties", "required", "additionalProperties", "enum", "items", "description"}


def hub_ok(schema, value):
    return schema_error(schema, value) is None


def client_ok(schema, value):
    return argument_error(value, schema) is None


@pytest.mark.parametrize(
    "schema,values",
    [
        ({"type": "integer"}, [1, 1.0, 1e20, 1.5, True, "1"]),
        ({"type": "number"}, [1, 1.0, True, None]),
        ({"enum": [1, 2]}, [1, 1.0, True, "1"]),
        ({"enum": [0, True]}, [False, 0.0, 1, True]),
        ({"enum": [[1], {"a": 1}]}, [[1.0], [True], {"a": 1.0}, {"a": True}]),
        ({"type": ["string", "null"], "enum": ["a"]}, [None, "a", "b"]),
        ({"type": "array", "items": {"type": "integer"}}, [[1.0, 2], [True]]),
        (
            {"type": "object", "properties": {"n": {"type": "integer"}}, "additionalProperties": False},
            [{"n": 2.0}, {"m": 1}],
        ),
    ],
)
def test_integers_enums_and_null_read_as_jsonschema_reads_them(schema, values):
    referee = Draft202012Validator(schema)
    for value in values:
        assert hub_ok(schema, value) == referee.is_valid(value) == client_ok(schema, value), value


def sample(schema):
    """One instance shaped like `schema`: its first option, enum value or required fields."""
    if "anyOf" in schema:
        return sample(schema["anyOf"][0])
    if "enum" in schema:
        return schema["enum"][0]
    kind = schema.get("type")
    kind = kind[0] if isinstance(kind, list) else kind
    if kind == "object":
        properties = schema.get("properties", {})
        return {key: sample(properties[key]) for key in schema.get("required", []) if key in properties}
    if kind == "array":
        return [sample(schema["items"])] * max(1, schema.get("minItems", 1)) if schema.get("items") else []
    if kind == "string":
        return "a" * max(1, schema.get("minLength", 1))
    return {"integer": max(1, schema.get("minimum", 1)), "number": 1.5, "boolean": True}.get(kind, None)


def variants(schema, value):
    """`value`, values of every JSON type, and `value` with each field broken in the same ways, one at a time."""
    yield value
    yield from (None, True, 1, 1.0, 2.5, "s", "", [], {}, [1.0], {"k": 1}, 10**12, -5, "x" * 5000)
    if isinstance(value, dict):
        yield {**value, "unknown_field": 1}
        for key in value:
            yield {k: v for k, v in value.items() if k != key}
        for key, sub in schema.get("properties", {}).items():
            for broken in itertools.islice(variants(sub, sample(sub)), 16):
                yield {**value, key: broken}
    if isinstance(value, list) and schema.get("items"):
        yield value * 3
        yield [None]


def shipped():
    yield from (("actions:" + name, action["input_schema"]) for name, action in SPEC.items())
    yield from (("mcp:" + tool["name"], tool["inputSchema"]) for tool in TOOLS)
    yield from (("screen:" + action["id"], action.get("input") or {}) for action in SCREEN)


def test_every_shipped_schema_is_checked_as_jsonschema_checks_it():
    wrong = []
    for name, schema in shipped():
        referee = Draft202012Validator(schema)
        for value in variants(schema, sample(schema)):
            expected = referee.is_valid(value)
            if (
                client_ok(schema, value) != expected
                or name.startswith("actions:")
                and hub_ok(schema, value) != expected
            ):
                wrong.append((name, json.dumps(value)[:80], expected))
    assert not wrong, wrong[:5]


def test_the_company_file_actions_use_only_the_keywords_its_check_reads():
    def keywords(schema):
        yield from schema
        if schema.get("additionalProperties") not in (None, False):
            yield "additionalProperties (a schema)"
        for sub in schema.get("properties", {}).values():
            yield from keywords(sub)
        if "items" in schema:
            yield from keywords(schema["items"])

    used = {key for action in SPEC.values() for key in keywords(action["input_schema"])}
    assert used <= HUB_KEYWORDS, used - HUB_KEYWORDS
