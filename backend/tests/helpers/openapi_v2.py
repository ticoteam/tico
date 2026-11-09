"""Helpers that other tests share, moved out of test_openapi_v2.py so they import no test file."""

from backend.tests.helpers.api import headers


def conforms(value, schema, root, where="$"):
    """The subset of JSON Schema the contract uses: type, required, properties, items, oneOf, enum, $ref."""
    if "$ref" in schema:
        return conforms(value, root["components"]["schemas"][schema["$ref"].rsplit("/", 1)[1]], root, where)
    if "oneOf" in schema:
        errors = [conforms(value, option, root, where) for option in schema["oneOf"]]
        return None if any(e is None for e in errors) else "; ".join(errors)
    if "enum" in schema:
        return None if value in schema["enum"] else "%s: %r not in %r" % (where, value, schema["enum"])
    kinds = schema.get("type")
    kinds = [kinds] if isinstance(kinds, str) else kinds
    names = {
        dict: "object",
        list: "array",
        str: "string",
        bool: "boolean",
        int: "integer",
        float: "number",
        type(None): "null",
    }
    actual = names[type(value)]
    if kinds and actual not in kinds and not (actual == "integer" and "number" in kinds):
        return "%s: %s is not %s" % (where, actual, kinds)
    if isinstance(value, dict):
        if schema.get("additionalProperties") is False and set(value) - set(schema.get("properties", {})):
            return where + ": unexpected fields " + str(set(value) - set(schema.get("properties", {})))
        for key in schema.get("required", []):
            if key not in value:
                return "%s: missing %s" % (where, key)
        for key, sub in schema.get("properties", {}).items():
            if key in value and (error := conforms(value[key], sub, root, where + "." + key)):
                return error
    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            if error := conforms(item, schema["items"], root, "%s[%d]" % (where, i)):
                return error
    return None
