#!/usr/bin/env python3
"""Keep the API guide's operation groups aligned with the OpenAPI tags (backend/openapi_v2.py)."""

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

START = '<!-- api-tags:start -->'
END = '<!-- api-tags:end -->'


def tags():
    """TAGS as the module builds it: its dict literal, then each top-level `TAGS[name] = text` in order. Read from the
    source, not imported, because importing the backend would reach every backend test in the PR selection."""
    found = {}
    for node in ast.parse((ROOT / 'backend/openapi_v2.py').read_text()).body:
        if not isinstance(node, ast.Assign):
            continue
        if any(getattr(t, 'id', None) == 'TAGS' for t in node.targets):
            found.update(ast.literal_eval(node.value))
        for t in node.targets:
            if isinstance(t, ast.Subscript) and getattr(t.value, 'id', None) == 'TAGS':
                found[ast.literal_eval(t.slice)] = ast.literal_eval(node.value)
    if not found:
        raise SystemExit('backend/openapi_v2.py has no TAGS')
    return found


def table():
    rows = ['| Tag | What it covers |', '|---|---|']
    for name, text in tags().items():
        description = re.sub(r'\((docs/[^)]+\.md)\)', lambda m: '([guide](' + m[1][5:] + '))', text)
        rows.append('| ' + name + ' | ' + description.replace('|', '\\|') + ' |')
    return '\n'.join(rows)


def main():
    page = ROOT / 'docs/api.md'
    text = page.read_text()
    before, rest = text.split(START, 1)
    _, after = rest.split(END, 1)
    current = before + START + '\n' + table() + '\n' + END + after
    if '--check' in sys.argv:
        return 0 if text == current else 'docs/api.md tags are stale: run python scripts/build_api_docs.py'
    page.write_text(current)
    return 0


if __name__ == '__main__':
    sys.exit(main())
