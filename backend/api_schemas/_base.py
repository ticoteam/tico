# pyright: strict
"""What the schema modules share: the registry they add to, and the shorthand they write schemas in."""

from collections.abc import Iterable
from typing import Any


# Every answer schema of the v2 contract by name; each module of this package adds its own.
SCHEMAS: dict[str, Any] = {}


def scalar(code: str) -> dict[str, Any]:
    """The schema of one shorthand field type: s(tring), i(nteger), b(oolean), o(bject), n (string or null), a(rray),
    f (number)."""
    return {
        "s": {"type": "string"},
        "i": {"type": "integer"},
        "b": {"type": "boolean"},
        "o": {"type": "object"},
        "n": {"type": ["string", "null"]},
        "a": {"type": "array"},
        "f": {"type": "number"},
    }[code]


def obj(fields: dict[str, Any], required: Iterable[str] | None = None, **arrays: Any) -> dict[str, Any]:
    """An object schema from {"name": "s|i|b|o|n|a|f"} or {"name": {schema}}. Other fields are allowed."""
    props: dict[str, Any] = {k: (scalar(v) if isinstance(v, str) else v) for k, v in fields.items()}
    props.update(arrays)
    return {
        "type": "object",
        "properties": props,
        "required": sorted(required if required is not None else props),
        "additionalProperties": True,
    }


NUM_N: dict[str, Any] = {"type": ["number", "null"]}


def items(schema: str | dict[str, Any]) -> dict[str, Any]:
    """An array schema of one item schema, written like an obj field: "s|i|b|o|n|a|f" or {schema}."""
    return {"type": "array", "items": scalar(schema) if isinstance(schema, str) else schema}


ACTORS: dict[str, Any] = {
    "type": "object",
    "additionalProperties": {"type": "string"},
    "description": "On reads: display names for every actor id in the answer, {\"human:ana\": \"Ana Alvarez\"}",
}


def merge(*schemas: dict[str, Any], **more: Any) -> dict[str, Any]:
    """One object schema from several: the properties of each, and what each requires."""
    props: dict[str, Any] = {}
    required: set[str] = set()
    for schema in schemas:
        props.update(schema["properties"])
        required.update(schema.get("required", []))
    props.update(more)
    return {"type": "object", "properties": props, "required": sorted(required), "additionalProperties": True}
