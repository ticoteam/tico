#!/usr/bin/env python3
"""Reject PR-added legacy migration names and duplicate versions, and edits to a migration the base already has
(stdlib and git only).

A merged migration may already be in a release, and a server refuses to start when an applied migration's file
changed (backend/migrations `_verify`, checksums): put the change in a new migration instead.

Fetch the base branch tip with full history before running this check; --head is the PR head, not its merge commit.
"""

import argparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = "backend/migrations"
# Match the loader's numbered SQL and Python files without importing the server.
NAME = re.compile(r"backend/migrations/(\d{4}|\d{14})_[a-z0-9_]+\.(sql|py)")


def migrations(paths):
    return {path: match.group(1) for path in paths if (match := NAME.fullmatch(path))}


def errors(added, head, base, base_name, *, now=None, edited=()):
    """Only additions need timestamps; consider both trees for collisions and free rename suggestions. `edited` are
    the migrations the PR changed: any `base` already has is refused."""
    frozen = [
        f"{path}: {base_name} already has this migration, and a server that applied it refuses a changed file. "
        "Restore it (git show <base>:<path> > <path>) and put the change in a new migration"
        for path in sorted(migrations(edited))
        if path in migrations(base)
    ]
    added, head, base = migrations(added), migrations(head), migrations(base)
    by_number = defaultdict(set)
    for path, number in (base | head).items():
        by_number[number].add(path)
    used = set(by_number)
    next_free = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)
    found = []
    for path, number in sorted(added.items()):
        reasons = []
        if len(number) == 4:
            reasons.append("new migrations use YYYYMMDDHHMMSS UTC timestamps")
        if path in base:
            reasons.append(f"{base_name} already contains {path}")
        others = sorted(by_number[number] - {path})
        if others:
            reasons.append("version also used by " + ", ".join(Path(other).name for other in others))
        if not reasons:
            continue
        while next_free.strftime("%Y%m%d%H%M%S") in used:
            next_free += timedelta(seconds=1)
        timestamp = next_free.strftime("%Y%m%d%H%M%S")
        suffix = Path(path).name.split("_", 1)[1]
        target = f"{DIRECTORY}/{timestamp}_{suffix}"
        found.append(f"{path}: " + "; ".join(reasons) + f". Rename with: git mv {path} {target}")
        used.add(timestamp)
    return found + frozen


def _as_released(git, head, path):
    """True when `path` at `head` is byte for byte the file the newest release tag before `head` holds."""
    try:
        tag = git("describe", "--tags", "--abbrev=0", "--match", "v[0-9]*", head).strip()
        return git("rev-parse", f"{tag}:{path}").strip() == git("rev-parse", f"{head}:{path}").strip()
    except subprocess.CalledProcessError:
        return False  # no release tag here, or the release does not have the file


def check(base, head="HEAD", base_name="main", root=ROOT):
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=root, text=True)

    ancestor = git("merge-base", base, head).strip()
    # Disable rename detection: a new numbered path still needs checking when its contents match an old file.
    added = git("diff", "--name-only", "--diff-filter=A", "--no-renames", "-z", ancestor, head, "--", DIRECTORY).split(
        "\0"
    )
    edited = git("diff", "--name-only", "--diff-filter=M", "--no-renames", "-z", ancestor, head, "--", DIRECTORY).split(
        "\0"
    )
    # Putting a file back exactly as the newest release shipped it is the fix, not an edit.
    edited = [path for path in edited if path and not _as_released(git, head, path)]
    head_files = git("ls-tree", "-r", "--name-only", "-z", head, "--", DIRECTORY).split("\0")
    base_files = git("ls-tree", "-r", "--name-only", "-z", base, "--", DIRECTORY).split("\0")
    return errors(added, head_files, base_files, base_name, edited=edited)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--base", required=True, help="freshly fetched base branch ref, with full history")
    parser.add_argument("--head", default="HEAD", help="PR head commit")
    parser.add_argument("--base-name", default="main", help="base branch name for diagnostics")
    args = parser.parse_args(argv)
    try:
        found = check(args.base, args.head, args.base_name)
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        print(f"::error::Cannot check migration names: {exc}")
        return 2
    for message in found:
        print(f"::error::{message}")
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
