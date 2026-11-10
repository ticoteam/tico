#!/usr/bin/env python3
"""Rename the MCP tools `hub_*` to `tico_*`, and the bots' MCP server `hub` to `tico`, in every file that names them.

Rerunnable and idempotent: it only ever turns `hub_` into `tico_` in front of a real tool name (and `mcp__hub__` into
`mcp__tico__`), so a second run finds nothing, and after a merge brings new text a rerun changes only that text.

A tool name is matched exactly, against the table in clients/hubtools.py (its tools and the CLI's alias names), so
Python helpers such as `hub_words` or `hub_mcp_server` stay. Also renamed:
  - a family of tools by its prefix (`hub_finance_*`, `"hub_email_" + action`), when some tool starts with it,
  - every tool at once (`hub_*`), and a family named in prose ("the hub_finance tools"),
  - the runner's prefix for the bots' own server (`mcp__hub__hub_task_show` is `mcp__tico__tico_task_show`, and a
    person's agent's `mcp__tico__hub_task_show` is `mcp__tico__tico_task_show`).

Never touched: docs/history/, reports/, scripts/cleanup/report-*.md, applied migrations, and KEEP below.

    python3 scripts/cleanup/rename_hub_tools_to_tico.py            # rewrite, and print counts
    python3 scripts/cleanup/rename_hub_tools_to_tico.py --check    # change nothing; exit 1 if anything would change
    python3 scripts/cleanup/rename_hub_tools_to_tico.py PATH...    # only these files or folders
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# An applied migration is never edited (backend/migrations checks each file's checksum), comments included.
SKIP_PREFIXES = ("docs/history/", "reports/", ".git/", "backend/migrations/")
SKIP_GLOBS = ("scripts/cleanup/report-*.md",)
BINARY = {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".woff", ".woff2", ".zip", ".gz", ".sqlite", ".db", ".ico", ".icns"}
BINARY |= {".mp4", ".mp3", ".wav", ".webp", ".ttf", ".otf", ".lock", ".svg", ".car", ".a", ".so", ".dylib"}

KEEP = {
    "scripts/cleanup/rename_hub_tools_to_tico.py",
    "backend/tests/test_no_hub_tool_names.py",  # its examples are the old names
    "clients/hermes_agent.py",  # finds the old names in a person's Hermes profile and says the new ones
    "clients/tests/test_hermes_agent.py",
}


def tracked(only=()):
    names = subprocess.run(
        ["git", "ls-files", "-z", "--", *only], cwd=ROOT, capture_output=True, check=True
    ).stdout.split(b"\0")
    for raw in names:
        name = raw.decode()
        if not name or name.startswith(SKIP_PREFIXES) or name in KEEP:
            continue
        if any(Path(name).match(glob) for glob in SKIP_GLOBS) or Path(name).suffix.lower() in BINARY:
            continue
        yield name


def stems():
    """Every tool's name without its `hub_` or `tico_` prefix: the registered tools, the CLI's alias names and the
    commands that have no tool (SHELL_ONLY), which the CLI names the same way."""
    from clients import hubtools

    names = {t["name"] for t in hubtools.TOOLS} | set(hubtools.SHELL_ONLY)
    names |= set(hubtools.CLI_TOOL_ALIASES) | {n for ns in hubtools.CLI_TOOL_ALIASES.values() for n in ns}
    return {re.sub(r"^(?:hub|tico)_", "", n) for n in names}


def alternation(words):
    return "|".join(re.escape(w) for w in sorted(words, key=lambda w: (-len(w), w)))


def patterns():
    names = stems()
    families = {stem[: i + 1] for stem in names for i, ch in enumerate(stem) if ch == "_"}
    # Not after a letter, digit or one underscore (`my_hub_task_list`); after two (`mcp__tico__hub_task_list`) it is.
    before = r"(?:(?<![A-Za-z0-9_])|(?<=__))"
    return {
        "server": re.compile(r"\bmcp__hub__"),
        "name": re.compile(rf"{before}hub_(?=(?:{alternation(names)})(?![A-Za-z0-9_]))"),
        "family": re.compile(rf"{before}hub_(?=(?:{alternation(families)})(?![A-Za-z0-9]))"),
        "every": re.compile(rf"{before}hub_(?=\*)"),
        # A family named in prose: "the hub_finance tools".
        "prose": re.compile(rf"{before}hub_(?=(?:{alternation(f[:-1] for f in families)}) tools\b)"),
    }


def rewrite(text, pats):
    """The text with its tool names renamed, and how many."""
    text, n = pats["server"].subn("mcp__tico__", text)
    for key in ("name", "family", "every", "prose"):
        text, k = pats[key].subn("tico_", text)
        n += k
    return text, n


def run(write=True, only=()):
    pats = patterns()
    files = changes = 0
    for name in tracked(only):
        path = ROOT / name
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if "hub_" not in text and "mcp__hub__" not in text:
            continue
        new, n = rewrite(text, pats)
        if n:
            files += 1
            changes += n
            if write:
                path.write_text(new, encoding="utf-8")
    verb = "rewrote" if write else "would rewrite"
    print(f"{verb} {changes} `hub_` tool names in {files} files")
    return changes


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="change nothing; exit 1 when anything would change")
    ap.add_argument("paths", nargs="*", help="only these files or folders (default: the whole checkout)")
    args = ap.parse_args(argv)
    changes = run(write=not args.check, only=args.paths)
    return 1 if args.check and changes else 0


if __name__ == "__main__":
    sys.exit(main())
