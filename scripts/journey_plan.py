#!/usr/bin/env python3
"""Which upgrade journey scripts/journey-test.sh runs from the previous release to the candidate.

    python3 scripts/journey_plan.py v0.6.0 v0.6.1          # upgrade
    python3 scripts/journey_plan.py v0.5.216 v0.6.0-rc.1   # fresh-start

upgrade      the previous release is installed and seeded, then updated in place to the candidate.
fresh-start  the pair crosses 0.6, which starts fresh (docs/updates.md, "From 0.5 to 0.6"; release_train.FRESH_START).
             The candidate must refuse the seeded previous database and change none of its files, the previous release
             must start on it again with its tasks, and an update to the candidate must roll back to it. Then a new
             previous install with no tasks updates in place, and the rest of the journey runs on that install.
"""

from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from release_train import crosses_fresh_start  # one line for the train, the journey and the gate

TAG = re.compile(r"^v\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")


def journey(previous, candidate):
    """'upgrade' or 'fresh-start' for this pair of release tags (vX.Y.Z, the candidate may have a prerelease)."""
    for tag in (previous, candidate):
        if not TAG.match(str(tag or "")):
            raise ValueError(f"not a release tag: {tag!r}")
    return "fresh-start" if crosses_fresh_start(previous, candidate) else "upgrade"


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        print("usage: journey_plan.py PREVIOUS CANDIDATE", file=sys.stderr)
        return 2
    try:
        print(journey(*argv))
    except ValueError as exc:
        print(f"journey_plan: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
