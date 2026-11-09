#!/usr/bin/env python3
"""Keep the API guide's operation groups aligned with the committed OpenAPI tags."""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
START = '<!-- api-tags:start -->'
END = '<!-- api-tags:end -->'


def table():
    tags = json.loads((ROOT / 'docs/openapi/v2.json').read_text())['tags']
    rows = ['| Tag | What it covers |', '|---|---|']
    for tag in tags:
        description = re.sub(r'\((docs/[^)]+\.md)\)', lambda m: '([guide](' + m[1][5:] + '))', tag['description'])
        rows.append('| ' + tag['name'] + ' | ' + description.replace('|', '\\|') + ' |')
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
