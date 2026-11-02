"""Write a 0001_baseline.sql candidate: the schema a new hub database has once every hubdb step, migration file and
module schema call has run, as one SQL file.

    python3 scripts/cleanup/make_baseline.py /path/to/0001_baseline.sql     (or no argument: print it)

Nothing reads the file. It is the starting point for squashing the migrations one day, not a squash: backend/migrations
stays as it is. backend/tests/test_make_baseline.py checks that a database built from the output has the tables,
columns, indexes, triggers and views of one built by Store.initialize.

The candidate is schema only. It leaves out what SQLite makes by itself (sqlite_sequence, the automatic indexes, a
full-text table's shadow tables) and every row, including the schema_migrations history and the singleton rows some
modules insert; a squash would have to write those. It starts its code only against a new empty database in a
temporary folder.
"""

import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "backend/tests/fixtures/registry"
# The tables FTS5 makes beside a virtual table named X: X_data, X_idx, X_content, X_docsize, X_config.
SHADOW = ("_data", "_idx", "_content", "_docsize", "_config")
ORDER = {"table": 0, "view": 1, "index": 2, "trigger": 3}


def build(path):
    """A new hub database at `path`, made the way a server's first start makes it."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from backend.config import Settings
    from backend.store import Store
    Store(Settings(db_path=Path(path), registry_dir=REGISTRY)).initialize(seed_market=False)


def baseline(conn):
    """The CREATE statements of `conn`'s schema, tables first, then views, indexes and triggers, each kind in the order
    SQLite recorded them."""
    rows = conn.execute("SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY rowid").fetchall()
    virtual = {name for kind, name, sql in rows if kind == "table" and sql.upper().startswith("CREATE VIRTUAL TABLE")}
    shadow = {name + suffix for name in virtual for suffix in SHADOW}
    kept = [(kind, sql) for kind, name, sql in rows if name not in shadow and not name.startswith("sqlite_")]
    kept.sort(key=lambda row: ORDER[row[0]])
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    head = ["-- 0001_baseline.sql candidate, written by scripts/cleanup/make_baseline.py from a new hub database.",
            "-- Unused: the migrations are not squashed. Schema only (see the script).",
            f"PRAGMA user_version={version};"]
    return "\n".join(head + [sql.strip() + ";" for _, sql in kept]) + "\n"


def main():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "hub.sqlite"
        build(path)
        with sqlite3.connect(path) as conn:
            text = baseline(conn)
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(text)
        print(f"wrote {sys.argv[1]}")
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
