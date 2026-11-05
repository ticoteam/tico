#!/usr/bin/env python3
"""Create an independent UTC timestamped migration: new_migration.py <name> [--py]."""
import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re

DIRECTORY = Path(__file__).resolve().parents[1] / "backend/migrations"
SQL = "-- Make the schema change here; the migration runner owns the transaction.\n"
PYTHON = '''"""An independent schema change; the migration runner owns the transaction."""

# For table rebuilds, set FOREIGN_KEYS_OFF = True here. The runner disables foreign keys
# before BEGIN, checks references before COMMIT, and restores the setting afterward.


def up(conn):
    pass
'''


def create(name, *, python=False):
    if not re.fullmatch(r"[a-z0-9_]+", name):
        raise ValueError("Use a name containing only lowercase letters, digits and underscores")
    stamp = datetime.now(timezone.utc).replace(microsecond=0)
    used = {path.name.split("_", 1)[0] for path in DIRECTORY.glob("[0-9]*")}
    while stamp.strftime("%Y%m%d%H%M%S") in used:
        stamp += timedelta(seconds=1)
    path = DIRECTORY / f"{stamp:%Y%m%d%H%M%S}_{name}.{'py' if python else 'sql'}"
    with path.open("x", encoding="utf-8") as target:
        target.write(PYTHON if python else SQL)
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name")
    parser.add_argument("--py", action="store_true", help="create a Python up(conn) migration")
    args = parser.parse_args(argv)
    try:
        path = create(args.name, python=args.py)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
