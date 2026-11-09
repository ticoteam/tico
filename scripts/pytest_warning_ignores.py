"""Ratchet for the warning filters in pytest.ini: the third-party `ignore:` lines only shrink.

pytest.ini makes every warning a test error (`filterwarnings`' first line is `error`), then ignores the third-party
warnings we cannot fix yet, one `ignore:` line each with a comment above it. This check keeps that list from growing
and keeps the gate itself in place:

    python3 scripts/pytest_warning_ignores.py                  the gate must be in pytest.ini, and no:warnings must not
    python3 scripts/pytest_warning_ignores.py --base origin/main
                                                               also fail when the `ignore:` count grew against that ref

A base without the gate is the pull request that introduces it: there is no list to compare with yet, so the growth
check is skipped and says so (lint_baseline.py does the same for the ruff baseline).
"""

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INI = "pytest.ini"
GATE = "error"


def filter_values(text):
    """The entries of the [pytest] filterwarnings value, in order: the first lines after the key, without comments."""
    values = []
    inside = False
    for line in text.splitlines():
        if line.startswith("filterwarnings"):
            inside = True
            rest = line.split("=", 1)[1].strip() if "=" in line else ""
            if rest:
                values.append(rest)
        elif inside and line.strip() and not line[0].isspace():
            inside = False
        elif inside and line.strip() and not line.strip().startswith("#"):
            values.append(line.strip())
    return values


def ignore_lines(values):
    return [value for value in values if value.startswith("ignore:")]


def problems(head, base=None):
    """What is wrong with pytest.ini text `head`, and with its growth against `base` (None: no base to compare)."""
    found = []
    if "no:warnings" in head:
        found.append("pytest.ini has no:warnings again: that hides every warning (remove it)")
    values = filter_values(head)
    if not values or values[0] != GATE:
        found.append(f"pytest.ini filterwarnings must start with `{GATE}`: warnings are test errors")
    if base is None:
        return found
    base_values = filter_values(base)
    if not base_values or base_values[0] != GATE:
        return found  # the pull request that introduces the gate: its list is the first one
    new, old = ignore_lines(values), ignore_lines(base_values)
    if len(new) > len(old):
        added = [line for line in new if line not in old]
        found.append(
            f"pytest.ini has {len(new)} `ignore:` lines, {len(old)} on the base; the list may only shrink. "
            f"Added: {'; '.join(added)}. Fix the warning, or name the test or module in a narrow, commented line "
            "and get it reviewed."
        )
    return found


def git_show(ref, path):
    done = subprocess.run(["git", "show", f"{ref}:{path}"], cwd=ROOT, capture_output=True, text=True, check=False)
    return done.stdout if done.returncode == 0 else None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", metavar="REF", help="git ref to compare the ignore count against (e.g. origin/main)")
    args = parser.parse_args(argv)
    head = (ROOT / INI).read_text(encoding="utf-8")
    base = None
    if args.base:
        verified = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"{args.base}^{{commit}}"],
            cwd=ROOT,
            capture_output=True,
            check=False,
        )
        if verified.returncode:
            sys.exit(f"pytest_warning_ignores: base ref {args.base} is not available; fetch it or pass a reachable ref")
        base = git_show(args.base, INI) or ""
        if filter_values(base)[:1] != [GATE]:
            print(f"pytest_warning_ignores: {args.base} has no warning gate yet; growth check skipped", flush=True)
    found = problems(head, base)
    count = len(ignore_lines(filter_values(head)))
    print(f"pytest_warning_ignores: {count} `ignore:` lines in {INI}", flush=True)
    for line in found:
        print(f"pytest_warning_ignores: {line}", file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
