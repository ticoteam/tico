#!/usr/bin/env python3
"""The advisory gate's hand-kept inputs match the tree: every vendored library has its package, version and hash in
scripts/advisories/vendored.json, and scripts/advisories/tools.json lists every uv a script pins (scripts/osv_gate.py
drift). Offline and read-only, so it runs on every pull request; the advisory scan itself runs when an input changes."""

import argparse
from pathlib import Path
import sys

# Direct invocation has scripts/gates, rather than the repository, on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts import osv_gate


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", help="unused: the check reads the whole tree")
    parser.parse_args(argv)
    problems = osv_gate.drift()
    for problem in problems:
        print(problem)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
