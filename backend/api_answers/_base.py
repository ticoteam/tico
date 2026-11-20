# pyright: strict
"""What the answer modules share: the `@answer` decorator every answer TypedDict wears, the field types the contract
spells its own way, and references to answers still declared by hand in backend/api_schemas/.

An answer is a TypedDict, not a model: handlers keep returning plain dicts, encoded once by orjson
(backend/fast_json.py), so declaring an answer changes no byte on the wire and costs nothing per request. FastAPI
reads the TypedDict only to write the document (api_contract.api_v2 passes it as the route's 200 `model`).

    @answer
    class Thing(TypedDict):
        id: str                      # required, never null
        owner_id: str | None         # required, may be null
        note: NotRequired[str]       # may be absent, never null
        '''A field's docstring is its description in the document.'''
"""

import ast
import inspect
import textwrap
from typing import Annotated, Any, is_typeddict

from pydantic import ConfigDict, GetJsonSchemaHandler, WithJsonSchema, with_config


JsonSchema = dict[str, Any]


def _tidy(node: JsonSchema) -> None:
    """One property's schema in the contract's spelling (the one backend/api_schemas/ wrote by hand, which the UI's
    generated types and tests/helpers/openapi_v2.conforms read): no generated titles, a nullable scalar as a type list,
    a nullable object or reference as oneOf with null. Does not follow $ref: each answer tidies its own."""
    node.pop("title", None)
    if "const" in node:
        node["enum"] = [node.pop("const")]  # Literal["risk"]: one value, spelled as the contract spells several
    if node.get("type") == "object" and node.get("additionalProperties") is True and "properties" not in node:
        del node["additionalProperties"]  # dict[str, Any]: any object, as {"type": "object"} says already
    if node.get("type") == "array" and node.get("items") == {}:
        del node["items"]  # list[Any]
    for key in ("items", "additionalProperties"):
        sub = node.get(key)
        if isinstance(sub, dict):
            _tidy(sub)  # pyright: ignore[reportUnknownArgumentType]
    options: list[JsonSchema] | None = node.get("anyOf")
    if options is None:
        return
    for option in options:
        _tidy(option)
    kinds = [o.get("type") for o in options]
    formats = [o["format"] for o in options if "format" in o]
    if (
        all(isinstance(k, str) and set(o) <= {"type", "format"} for k, o in zip(kinds, options, strict=True))
        and len(set(kinds)) == len(kinds)  # DateTime | str would say "string" twice
        and len(formats) <= 1
    ):
        del node["anyOf"]
        node["type"] = kinds
        if formats:
            node["format"] = formats[0]
    elif len(options) == 2 and {"type": "null"} in options:
        node["oneOf"] = node.pop("anyOf")  # one value or null: they never overlap, so oneOf says the same


def _docstrings(cls: type) -> dict[str, str]:
    """The field docstrings of a TypedDict, its parents' included: pydantic reads only the class's own, so a field a
    TypedDict inherits would lose its description."""
    found: dict[str, str] = {}
    for base in getattr(cls, "__orig_bases__", ()):
        if is_typeddict(base):
            found.update(_docstrings(base))
    try:
        body = ast.parse(textwrap.dedent(inspect.getsource(cls))).body[0]
    except (OSError, TypeError):  # a TypedDict made by a call has no class body
        return found
    if isinstance(body, ast.ClassDef):
        for field, doc in zip(body.body, body.body[1:], strict=False):
            if (
                isinstance(field, ast.AnnAssign)
                and isinstance(field.target, ast.Name)
                and isinstance(doc, ast.Expr)
                and isinstance(doc.value, ast.Constant)
                and isinstance(doc.value.value, str)
            ):
                found[field.target.id] = inspect.cleandoc(doc.value.value)
    return found


def _contract_style(schema: JsonSchema, cls: type) -> None:
    schema.pop("title", None)
    inherited = _docstrings(cls) if any(is_typeddict(base) for base in getattr(cls, "__orig_bases__", ())) else {}
    for name, prop in schema.get("properties", {}).items():
        if "description" not in prop and name in inherited:
            prop["description"] = inherited[name]
        _tidy(prop)
    if "required" in schema:
        schema["required"] = sorted(schema["required"])


# Open objects (fields are added within v2, never removed), field docstrings as descriptions, the contract's spelling.
answer = with_config(ConfigDict(extra="allow", use_attribute_docstrings=True, json_schema_extra=_contract_style))


class OneOf:
    """`Annotated[A | B, ONE_OF]`: the union is published as oneOf, exactly one of them, where pydantic writes anyOf.
    Only for options that cannot both match (a person's review answer is one shape or the other)."""

    def __get_pydantic_json_schema__(self, core_schema: Any, handler: GetJsonSchemaHandler) -> JsonSchema:
        schema = handler(core_schema)
        if "anyOf" in schema:
            schema["oneOf"] = schema.pop("anyOf")
        return schema


ONE_OF = OneOf()

# A timestamp the contract marks as date-time; on the wire and in Python it is an ISO string.
DateTime = Annotated[str, WithJsonSchema({"type": "string", "format": "date-time"})]


# An answer that refers to one still declared by hand in backend/api_schemas/ names a stand-in: an empty open TypedDict
# of the same name, marked so openapi_v2.spec() puts the hand-written schema of that name in the document in its place.
# When the tag that owns one moves, its real TypedDict replaces the stand-in (the migration plan is in the pull request
# that started this). None is needed now: every answer moved so far refers only to moved ones.
HAND_WRITTEN = "x-tico-hand-written"
hand_written = with_config(ConfigDict(extra="allow", json_schema_extra={HAND_WRITTEN: True}))
