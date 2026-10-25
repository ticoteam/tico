"""`{{path}}` substitution for rule text and webhook data, and nothing else.

A placeholder is a dotted path into the values: `{{data.repository.full_name}}`, `{{type}}`, `{{items.0.name}}` (a
number indexes a list). A path that is not there gives an empty string. There are no expressions, filters, loops or
code, and a substituted value is never read again as a template, so a payload cannot inject one. A value that is a
number or text is written as it is, true/false/null as JSON, and an object or a list as compact JSON. The output is
cut at `cap` characters with " … (cut)".
"""
import json
import re

CUT = " … (cut)"
PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z0-9_][A-Za-z0-9_.-]{0,199}?)\s*\}\}")
MISSING = object()


def lookup(values, path):
    """The value at the dotted `path`, or MISSING."""
    here = values
    for part in path.split("."):
        if isinstance(here, dict):
            if part not in here:
                return MISSING
            here = here[part]
        elif isinstance(here, (list, tuple)):
            if not part.isdigit() or int(part) >= len(here):
                return MISSING
            here = here[int(part)]
        else:
            return MISSING
    return here


def text_of(value):
    if value is MISSING or value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        return ""


def clip(text, cap):
    if len(text) <= cap:
        return text
    return text[:max(0, cap - len(CUT))] + CUT


def render(template, values, *, cap=4096):
    """`template` with each `{{path}}` replaced by its value from `values`."""
    if not isinstance(template, str):
        return ""
    values = values if isinstance(values, dict) else {}
    # The template is scanned once; what a value contains is not scanned again.
    return clip(PLACEHOLDER.sub(lambda found: text_of(lookup(values, found.group(1))), template), cap)


def render_json(template, values, *, cap=65536):
    """`template` (an object, list or text) with every string rendered. Keys stay as written. When the result is
    over `cap` characters as JSON, the longest strings are cut (with " … (cut)") until it fits, so it stays valid."""
    values = values if isinstance(values, dict) else {}
    leaves = []         # (container, key) of every rendered string, to cut the longest first

    def walk(node, depth=0):
        if depth > 12:
            return None
        if isinstance(node, str):
            return render(node, values, cap=cap)
        if isinstance(node, dict):
            out = {}
            for key, value in node.items():
                out[str(key)] = walk(value, depth + 1)
                if isinstance(out[str(key)], str):
                    leaves.append((out, str(key)))
            return out
        if isinstance(node, (list, tuple)):
            out = []
            for index, value in enumerate(node):
                out.append(walk(value, depth + 1))
                if isinstance(out[-1], str):
                    leaves.append((out, index))
            return out
        return node

    def size(node):
        return len(json.dumps(node, ensure_ascii=False, separators=(",", ":"), default=str))

    out = walk(template)
    if isinstance(out, str):
        return out
    over = size(out) - cap
    while over > 0 and leaves:
        leaves.sort(key=lambda leaf: len(leaf[0][leaf[1]]))
        holder, key = leaves[-1]
        text = holder[key]
        if len(text) <= len(CUT):
            break
        holder[key] = clip(text, max(len(CUT), len(text) - over))
        over = size(out) - cap
    return out
