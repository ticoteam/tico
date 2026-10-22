"""Shared CLI question shorthand and attachment payloads (stdlib only)."""
import base64
import json
from pathlib import Path


def ask_from_args(args):
    if getattr(args, "ask", None):
        return json.loads(Path(args.ask).read_text())
    if getattr(args, "choices", None) is not None:
        # "LABEL -> WHAT HAPPENS, ...": a person's card shows each choice with what happens if it is picked.
        pairs = [[x.strip() for x in s.partition("->")[::2]] for s in args.choices.split(",")]
        labels = [label for label, _ in pairs]
        if not 1 <= len(labels) <= 6 or any(not s or len(s) > 60 for s in labels) or len(set(labels)) != len(labels):
            raise ValueError("Choices are one to six unique labels, each at most 60 characters")
        return {"questions": [{"id": "verdict", "header": "Review", "question": "What do you think?",
                               "options": [{"label": label, **({"then": then} if then else {})} for label, then in pairs],
                               "multi": False, "other": True}], "who": None}
    return None


def attachment(path, name=None):
    path = Path(path)
    data = path.read_bytes()
    body = {"name": name or path.name}
    try:
        body["text"] = data.decode("utf-8")
    except UnicodeDecodeError:
        body["content_base64"] = base64.b64encode(data).decode("ascii")
    return body


ASK_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["questions"], "properties": {
    "who": {"type": ["string", "null"], "description": "Person or bot slug highlighted in Needs you; defaults to the requester"},
    "questions": {"type": "array", "minItems": 1, "maxItems": 4, "items": {
        "type": "object", "additionalProperties": False, "required": ["id", "header", "question"], "properties": {
            "id": {"type": "string", "minLength": 1, "maxLength": 40},
            "header": {"type": "string", "maxLength": 30},
            "question": {"type": "string", "minLength": 1, "maxLength": 300},
            "why": {"type": "string", "maxLength": 200, "description": "One plain line on why it matters"},
            "multi": {"type": "boolean", "default": False}, "other": {"type": "boolean", "default": True},
            "options": {"type": "array", "maxItems": 6, "items": {
                "type": "object", "additionalProperties": False, "required": ["label"], "properties": {
                    "label": {"type": "string", "minLength": 1, "maxLength": 60},
                    "then": {"type": "string", "maxLength": 120,
                             "description": "What happens if it is picked; required when a bot asks a person"},
                    "recommended": {"type": "boolean", "description": "Pre-ticked among several picks"},
                    "description": {"type": "string", "maxLength": 200},
                    "file": {"type": "string", "pattern": "^[^@]+@[1-9][0-9]*$"}}}}}}}}}
