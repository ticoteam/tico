#!/usr/bin/env python3
"""Ratchet on the size of the hub tool modules, so growth stops at the source.

scripts/module_size_limits.json holds, per file (or per function, as `path::name`), the most lines it may have. A
limit only goes down. A change that adds lines fails; a change that removes lines must lower the limit with --write,
so the room it freed cannot be filled again. New tools go in the area modules (clients/hubtools_<area>.py,
clients/hubcli_<area>.py), not in the files listed here. With --base REF, a limit above the one at REF fails too.

    python3 scripts/check_module_size.py                  check the limits against the working tree
    python3 scripts/check_module_size.py --base origin/main   also refuse a limit raised past the base
    python3 scripts/check_module_size.py --write          lower each limit to the current size (never raises one)
"""

import argparse
import ast
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIMITS_NAME = "scripts/module_size_limits.json"


def current(key):
    rel, _, func = key.partition("::")
    text = (ROOT / rel).read_text()
    if not func:
        return len(text.splitlines())
    for node in ast.parse(text).body:
        if isinstance(node, ast.FunctionDef) and node.name == func:
            return node.end_lineno - node.lineno + 1
    raise SystemExit(f"module size: {rel} has no top-level def {func}")


def load(text):
    return json.loads(text) if text else {}


def base_limits(ref):
    out = subprocess.run(["git", "show", f"{ref}:{LIMITS_NAME}"], cwd=ROOT, capture_output=True, text=True, check=False)
    return load(out.stdout) if out.returncode == 0 else {}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--base", metavar="REF", help="git ref whose limits this may not raise (e.g. origin/main)")
    parser.add_argument("--write", action="store_true", help="lower each limit to the current size")
    args = parser.parse_args(argv)
    path = ROOT / LIMITS_NAME
    limits = load(path.read_text())
    sizes = {key: current(key) for key in limits}
    if args.write:
        for key, size in sizes.items():
            if size > limits[key]["max_lines"]:
                print(f"module size: {key} is {size} lines, above its limit; shrink it, do not raise the limit")
                return 1
            limits[key]["max_lines"] = size
        path.write_text(json.dumps(limits, indent=2) + "\n")
        print(f"module size: wrote {len(limits)} limit(s)")
        return 0
    problems = []
    before = base_limits(args.base) if args.base else {}
    for key, entry in limits.items():
        size, limit = sizes[key], entry["max_lines"]
        if size > limit:
            problems.append(
                f"{key} grew to {size} lines, over its limit of {limit}: move the new code to an area module"
            )
        elif size < limit:
            problems.append(
                f"{key} is {size} lines, under its limit of {limit}: run python3 scripts/check_module_size.py --write"
            )
        if args.base and key in before and limit > before[key]["max_lines"]:
            problems.append(f"{key}: limit raised from {before[key]['max_lines']} to {limit} (limits only go down)")
    if problems:
        print("module size: FAIL")
        for problem in problems:
            print("  -", problem)
        return 1
    summary = ", ".join(f"{key} {sizes[key]}/{limits[key]['max_lines']}" for key in limits)
    print(f"module size: ok ({summary})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
