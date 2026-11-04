#!/usr/bin/env python3
"""Reject PR-added migrations that cannot follow the current base branch's migrations (stdlib and git only).

Fetch the base branch tip with full history before running this check; --head is the PR head, not its merge commit.
"""
import argparse
from collections import defaultdict
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = "backend/migrations"
# Match the loader's numbered SQL and Python files without importing the server.
NAME = re.compile(r"backend/migrations/(\d{4})_[a-z0-9_]+\.(sql|py)")


def migrations(paths):
    return {path: int(match.group(1)) for path in paths if (match := NAME.fullmatch(path))}


def errors(added, head, base, base_name):
    """Only additions need new numbers; consider both trees when finding collisions and suggesting free numbers."""
    added, head, base = migrations(added), migrations(head), migrations(base)
    newest = max(base.values(), default=-1)
    by_number = defaultdict(set)
    for path, number in (base | head).items():
        by_number[number].add(path)
    used = set(by_number)
    next_free = max(newest + 1, 1)
    found = []
    for path, number in sorted(added.items()):
        reasons = []
        if number <= newest:
            reasons.append(f"{base_name} already has {newest:04d}")
        others = sorted(by_number[number] - {path})
        if others:
            reasons.append("number also used by " + ", ".join(Path(other).name for other in others))
        if not reasons:
            continue
        while next_free in used:
            next_free += 1
        if next_free > 9999:
            raise ValueError("No free four-digit migration number remains")
        name = Path(path).name
        found.append(f"Renumber {name} to {next_free:04d}{name[4:]}: " + "; ".join(reasons))
        used.add(next_free)
    return found


def check(base, head="HEAD", base_name="main", root=ROOT):
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=root, text=True)

    ancestor = git("merge-base", base, head).strip()
    # Disable rename detection: a new numbered path still needs checking when its contents match an old file.
    added = git("diff", "--name-only", "--diff-filter=A", "--no-renames", "-z", ancestor, head,
                "--", DIRECTORY).split("\0")
    head_files = git("ls-tree", "-r", "--name-only", "-z", head, "--", DIRECTORY).split("\0")
    base_files = git("ls-tree", "-r", "--name-only", "-z", base, "--", DIRECTORY).split("\0")
    return errors(added, head_files, base_files, base_name)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--base", required=True, help="freshly fetched base branch ref, with full history")
    parser.add_argument("--head", default="HEAD", help="PR head commit")
    parser.add_argument("--base-name", default="main", help="base branch name for diagnostics")
    args = parser.parse_args(argv)
    try:
        found = check(args.base, args.head, args.base_name)
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        print(f"::error::Cannot check migration order: {exc}")
        return 2
    for message in found:
        print(f"::error::{message}")
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
