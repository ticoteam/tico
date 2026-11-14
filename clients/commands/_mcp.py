"""MCP over the table: BY_NAME, the JSON-RPC `Protocol` and the error shapes both clients print.

Moved from clients/hubtools.py; clients/commands/__init__.py says how the table is put together."""

import json
import re
from clients.tico import APIError

from clients.commands._registry import INSTRUCTIONS, PROTOCOL_VERSIONS, SERVER_INFO, TOOLS, release_version
from clients.commands._requester import kind_of
from clients.commands._access import listing, offered_to


BY_NAME = {t["name"]: t for t in TOOLS}


# What a task engine refusal carries beside its rule and detail (docs/dev/pipelines-v3-cutover.md, HTTP): what is
# missing and the commands allowed next, or the version the task has now. A bot reads them to act instead of retrying.
REFUSAL_FIELDS = ("missing", "next", "version")
# Who saved the version a refused doc write did not read (clients/commands/content.py docs_write).
CONFLICT_FIELDS = ("updated_by_name", "updated_at")


def error_payload(exc):
    """The shape the `tico` CLI prints for an API refusal or failure, so both clients read alike."""
    extra = getattr(exc, "extra", None) or {}
    return {
        "error": getattr(exc, "code", type(exc).__name__),
        "detail": getattr(exc, "detail", str(exc)),
        "retryable": bool(getattr(exc, "retryable", False)),
        "operation_id": getattr(exc, "operation_id", None),
        **{k: extra[k] for k in REFUSAL_FIELDS + CONFLICT_FIELDS if extra.get(k) not in (None, [])},
    }


def refusal_text(payload, task_id=None):
    """A refusal as a bot reads it in a terminal: the one line, what is missing, and the commands allowed next."""
    lines = [str(payload.get("detail") or payload.get("error") or "Refused")]
    if payload.get("missing"):
        lines += ["Missing:", *(f"  {m}" for m in payload["missing"])]
    if payload.get("next"):
        lines += ["You can:", *(f"  {n}" for n in payload["next"])]
    if payload.get("error") == "task_changed" and payload.get("version") is not None:
        task = task_id or "<id>"
        lines.append(f"It is version {payload['version']} now: run `tico task show {task}` and decide again.")
    return "\n".join(lines)


def _kinds(schema):
    kind = schema.get("type")
    return [kind] if isinstance(kind, str) else kind or []


def as_json_integers(value, schema):
    """`value` with each integral float that `schema` types as an integer sent as an int: JSON Schema counts 1.0 as an
    integer, and the server's strict integer fields refuse a float."""
    if (
        isinstance(value, float)
        and value.is_integer()
        and "integer" in (kinds := _kinds(schema))
        and "number" not in kinds
    ):
        return int(value)
    if isinstance(value, dict):
        props, extra = schema.get("properties", {}), schema.get("additionalProperties", {})
        return {
            k: as_json_integers(v, props.get(k, extra if isinstance(extra, dict) else {})) for k, v in value.items()
        }
    if isinstance(value, list):
        return [as_json_integers(item, schema.get("items", {})) for item in value]
    return value


def argument_error(value, schema, path="arguments"):
    types = {
        "object": dict,
        "array": list,
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "null": type(None),
    }
    kinds = _kinds(schema)

    def is_kind(k):  # JSON Schema's numbers: a bool is neither, and 1.0 is an integer
        if isinstance(value, bool):
            return k == "boolean"
        return isinstance(value, types[k]) or k == "integer" and isinstance(value, float) and value.is_integer()

    if kinds and not any(is_kind(k) for k in kinds):
        return f"{path}: expected {' or '.join(kinds)}"
    if "enum" in schema and value not in schema["enum"]:
        return f"{path}: use one of {', '.join(str(v) for v in schema['enum'])}"
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                return f"{path}.{key}: required"
        for key, item in value.items():
            if key not in props and schema.get("additionalProperties") is False:
                return f"{path}.{key}: unknown argument"
            sub = props.get(key, schema.get("additionalProperties", {}))
            if isinstance(sub, dict) and (error := argument_error(item, sub, f"{path}.{key}")):
                return error
    if isinstance(value, list):
        for key, cmp in (("minItems", lambda n: len(value) < n), ("maxItems", lambda n: len(value) > n)):
            if key in schema and cmp(schema[key]):
                return f"{path}: {key} is {schema[key]}"
        for index, item in enumerate(value):
            if error := argument_error(item, schema.get("items", {}), f"{path}[{index}]"):
                return error
    if isinstance(value, str):
        for key, cmp in (("minLength", lambda n: len(value) < n), ("maxLength", lambda n: len(value) > n)):
            if key in schema and cmp(schema[key]):
                return f"{path}: {key} is {schema[key]}"
        if schema.get("pattern") and not re.search(schema["pattern"], value):
            return f"{path}: expected pattern {schema['pattern']}"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        for key, cmp in (("minimum", lambda n: value < n), ("maximum", lambda n: value > n)):
            if key in schema and cmp(schema[key]):
                return f"{path}: {key} is {schema[key]}"
    return None


class Protocol:
    """The MCP JSON-RPC surface over the tool table, transport-agnostic.

    `handle(message) -> response | None` for one JSON-RPC message (None for a notification).
    `api_error` is the exception type the `api` raises for an HTTP-level refusal; anything else
    is a bug and surfaces as a JSON-RPC error.
    """

    def __init__(self, api, api_error=Exception, local=False, kind=None, profile=None):
        self.api, self.api_error, self.local = api, api_error, local
        self._kind, self._profile, self._me = kind, profile, None

    def me(self):
        """`GET /me`, read once; {} when it cannot be read."""
        if self._me is None:
            try:
                self._me = self.api.get("me") or {}
            except Exception:
                self._me = {}
        return self._me

    def kind(self):
        """Who is calling: given by the server that binds this protocol, else read once from `GET /me`. None (offer
        everything) when it cannot be told; the server still decides every call."""
        if self._kind is None:
            self._kind = kind_of(self.me()) or ""
        return self._kind or None

    def profile(self):
        """The tool list a bot's own turn is offered (`GET /me` `tool_profile`: `developer` for a code bot, `feature` for a Feature Owner), or None for
        the whole list. Only a bot has one."""
        if self._profile is None:
            self._profile = str(self.me().get("tool_profile") or "") if self.kind() == "bot" else ""
        return self._profile or None

    def handle(self, message):
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return self._error(None, -32600, "Invalid Request")
        rid, method, params = message.get("id"), message.get("method"), message.get("params", {})
        if method is None:
            return None  # a response to us; we send no requests
        if rid is None:  # a notification
            return None
        if not isinstance(params, dict):
            return self._error(rid, -32602, "params must be an object")
        if method == "initialize":
            asked = params.get("protocolVersion")
            version = asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
            return self._result(
                rid,
                {
                    "protocolVersion": version,
                    "capabilities": {"tools": {}},
                    "serverInfo": {**SERVER_INFO, "version": release_version()},
                    "instructions": INSTRUCTIONS,
                },
            )
        if method == "ping":
            return self._result(rid, {})
        if method == "tools/list":
            return self._result(rid, {"tools": listing(self.local, self.kind(), self.profile())})
        if method == "tools/call":
            return self._call(rid, params)
        return self._error(rid, -32601, f"Method not found: {method}")

    def _call(self, rid, params):
        name, args = params.get("name"), params.get("arguments", {})
        if not isinstance(name, str):
            return self._error(rid, -32602, "name must be a string")
        entry = BY_NAME.get(name)
        if entry and entry.get("local") and not self.local:
            return self._tool_error(
                rid,
                {
                    "error": "local_only",
                    "detail": f"{name} runs on the Computer holding the bot repository. Run it there, or ask BotOps.",
                    "retryable": False,
                },
            )
        if not entry:
            return self._error(rid, -32602, f"Unknown tool: {name}")
        if not isinstance(args, dict):
            return self._error(rid, -32602, "arguments must be an object")
        kind = self.kind()
        if kind and kind not in offered_to(entry):
            return self._tool_error(
                rid, {"error": "forbidden", "detail": f"{name} is not available to you", "retryable": False}
            )
        properties = entry["inputSchema"].get("properties", {})
        missing = [
            k
            for k in entry["inputSchema"].get("required", [])
            if k not in args
            or (args[k] is None and "null" not in _kinds(properties.get(k, {})))
            or (args[k] == "" and properties.get(k, {}).get("minLength") != 0)
        ]
        if missing:
            return self._tool_error(
                rid, {"error": "usage", "detail": f"{name} needs {', '.join(missing)}", "retryable": False}
            )
        if error := argument_error(args, entry["inputSchema"]):
            return self._tool_error(rid, {"error": "validation", "detail": error, "retryable": False})
        args = as_json_integers(args, entry["inputSchema"])
        try:
            result = entry["fn"](self.api, args)
        except ValueError as exc:
            return self._tool_error(rid, {"error": "usage", "detail": str(exc), "retryable": False})
        except (self.api_error, APIError) as exc:  # a refusal the tool itself raises reads like the API's
            return self._tool_error(rid, error_payload(exc))
        return self._result(
            rid,
            {
                "content": [{"type": "text", "text": json.dumps(result, default=str)}],
                "structuredContent": result if isinstance(result, dict) else {"result": result},
                "isError": False,
            },
        )

    @staticmethod
    def _result(rid, result):
        return {"jsonrpc": "2.0", "id": rid, "result": result}

    @staticmethod
    def _error(rid, code, message):
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}

    @staticmethod
    def _tool_error(rid, payload):
        return {
            "jsonrpc": "2.0",
            "id": rid,
            "result": {
                "content": [{"type": "text", "text": json.dumps(payload)}],
                "structuredContent": payload,
                "isError": True,
            },
        }
