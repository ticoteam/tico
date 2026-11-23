"""The one migration authority for the hub database.

A database starts from the baseline, `baseline.sql` here: every table, index, view and trigger a new database had when
it was written, the rows that schema holds, and a schema_migrations row for each migration file it already contains.
A new file (no tables) loads it in one transaction; then `apply` runs each migration file the database has not
recorded, in version order. scripts/schema_baseline.py writes the baseline, and only when the compatibility floor moves.

A schema change that persists is a file in this directory: `YYYYMMDDHHMMSS_name.sql` (its statements, UTC) or `.py`
(a module whose `up(conn)` makes the change, without committing). Each one runs in its own transaction together with
its `schema_migrations` row, so a failure leaves neither behind and the next start tries it again. An applied file is
never edited: its checksum is recorded, and a start refuses a database whose recorded checksum no longer matches its
file. Create one with `python3 scripts/new_migration.py name` (add `--py` for `up(conn)`). Every pending file runs, even
if a newer version has already run. A migration must not depend on another unmerged migration's schema: each stands
alone, so parallel PRs may merge in either order. CI requires new files to use unique UTC timestamps.

The compatibility floor is v0.6.0: the oldest database this code opens is one that release made or upgraded,
which has the newest migration file that release shipped recorded. Those files are gone (`retired.txt` lists them):
such a database recorded them, a new one has their changes in the baseline, and neither runs them again. A database below
the floor (Tico 0.5 and older, or a file this Tico did not make) is refused before anything is written to it.

A `.py` migration that rebuilds a table may set `FOREIGN_KEYS_OFF = True`. Foreign keys can only be switched off
outside a transaction, so the runner turns them off before BEGIN, runs `PRAGMA foreign_key_check` before COMMIT and
rolls back when the migration left a broken reference the database did not already have, then restores the setting.

`preflight` runs before anything writes to a database. It refuses a file below the floor, a recorded migration this
code does not know (a newer Tico wrote it) and one whose file has changed. `apply` refuses the same on every
connection, before its first write.
"""

import functools
import hashlib
import importlib.util
import re
import shutil
import sqlite3
import tempfile
from collections import Counter
from contextlib import closing, contextmanager
from pathlib import Path

from .. import hubdb as H

DIRECTORY = Path(__file__).parent
BASELINE = "baseline.sql"
CUTOVER_PREREQUISITES = {
    "20261122144410_slack_outbound": ("20261122001930_outbound_actions.sql",),
    "20261122194833_email_outbound": ("20261118234339_email_send_tasks.sql", "20261122001930_outbound_actions.sql"),
    "20261122215020_plan_display_names": (
        "20261117061243_computer_keys_to_subscriptions.py",
        "20261117085930_drop_computer_keys.py",
    ),
    "20261122220441_agent_subscriptions": (
        "20261118012154_credential_adders.py",
        "20261117085930_drop_computer_keys.py",
        "20261122215020_plan_display_names.py",
    ),
}
RETIRED_LIST = "retired.txt"
# Older releases recorded their pre-file history as `legacy_user_version_<N>` and `legacy_cloud_<N>` rows with this
# checksum. They name steps every database at the floor ran; nothing reads them.
LEGACY = "legacy"
TABLE = (
    "CREATE TABLE IF NOT EXISTS schema_migrations(version TEXT PRIMARY KEY, checksum TEXT NOT NULL, "
    "applied_at TEXT NOT NULL)"
)
NAME = re.compile(r"(\d{14})_[a-z0-9_]+\.(sql|py)")
# Tables a database may hold before Tico first writes to it: SQLite's own and Litestream's.
NOT_TICO = re.compile(r"sqlite_|_litestream_")
BELOW_FLOOR = (
    "This database was made by a Tico older than 0.6.0, or not by Tico. Tico 0.6 does not carry tasks over: every "
    "company starts on a new database. Nothing was changed. Run the release that made it on this database, or set up "
    "a new company on 0.6."
)


class MigrationError(RuntimeError):
    """This code cannot open the database. The server does not start on it."""


class Migration:
    """One file: its version is its name without the extension, its checksum is the SHA-256 of the file's bytes."""

    def __init__(self, path):
        self.path = path
        self.version = path.stem
        self.kind = NAME.fullmatch(path.name).group(2)
        self.checksum = hashlib.sha256(path.read_bytes()).hexdigest()


_READ = {}  # {path: ((mtime_ns, size), Migration)}: a test process opens thousands of databases


def _migration(path):
    """The Migration for a file, read and hashed again only when the file's mtime or size changed."""
    stat = path.stat()
    stamp = (stat.st_mtime_ns, stat.st_size)
    known = _READ.get(path)
    if known is None or known[0] != stamp:
        known = _READ[path] = (stamp, Migration(path))
    return known[1]


def files(directory=None):
    """The migration files in full string version order. Digit-led names use YYYYMMDDHHMMSS_ and .sql/.py.
    A name that does not match is an error, not skipped. Only digit-led names are listed, so this module does not
    read every tracked file (a bare "*" listing makes the dependency map treat any change as its input)."""
    found = []
    for path in Path(directory or DIRECTORY).glob("[0-9]*"):
        if not NAME.fullmatch(path.name):
            raise MigrationError(f"{path.name} is not a migration name: use YYYYMMDDHHMMSS_name.sql or .py")
        found.append(_migration(path))
    found.sort(key=lambda migration: migration.version)
    numbers = [migration.version.split("_", 1)[0] for migration in found]
    if len(set(numbers)) != len(numbers):
        raise MigrationError("Two migration files share a number: " + ", ".join(m.version for m in found))
    return found


def statements(sql):
    """The complete statements of a file. Comment lines go; a trigger body keeps its own semicolons."""
    buffer = ""
    for line in sql.splitlines(keepends=True):
        if line.lstrip().startswith("--"):
            continue
        buffer += line
        if sqlite3.complete_statement(buffer):
            yield buffer.strip()
            buffer = ""
    if buffer.strip():
        yield buffer.strip()


def _module(migration):
    spec = importlib.util.spec_from_file_location(f"migration_{migration.version}", migration.path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not callable(getattr(module, "up", None)):
        raise MigrationError(f"{migration.path.name} must define up(conn)")
    return module


@functools.cache
def _retired(path):
    if not path.is_file():
        return frozenset()
    lines = path.read_text(encoding="utf-8").splitlines()
    return frozenset(line.strip() for line in lines if line.strip() and not line.startswith("#"))


def retired(directory=None):
    """The versions of the files the floor retired: recorded on a database at the floor, never run again. The newest
    of them is the floor itself."""
    return _retired(Path(directory or DIRECTORY) / RETIRED_LIST)


def _verify(recorded, known, gone):
    """Refuse a recorded version this code does not know, and a file whose checksum changed after it was applied."""
    by_version = {migration.version: migration for migration in known}
    for version, checksum in recorded:
        if version in by_version:
            current = by_version[version].checksum
            if checksum != current:
                raise MigrationError(
                    f"Migration {version} was applied to this database with checksum {checksum[:12]}, but its file now "
                    f"has {current[:12]}. An applied migration is never edited: make the change in a new file."
                )
        elif version not in gone and not (checksum == LEGACY and version.startswith("legacy_")):
            raise MigrationError(
                f"This database has schema migration {version}, which this code does not know. It was written by a "
                "newer Tico: run that release, or restore a backup taken before it."
            )


def _tables(conn):
    return {
        row[0]
        for row in conn.execute("SELECT name FROM main.sqlite_master WHERE type='table'")
        if not NOT_TICO.match(row[0])
    }


def _at_floor(conn, tables, directory=None):
    """Refuse a database below the floor: it has tables, but not the floor's migration. A directory that retired no
    files has no floor."""
    gone = retired(directory)
    if not gone:
        return
    if (
        "schema_migrations" not in tables
        or not conn.execute("SELECT 1 FROM schema_migrations WHERE version=?", (max(gone),)).fetchone()
    ):
        raise MigrationError(BELOW_FLOOR)


@contextmanager
def read_only(path):
    """A connection that reads the database as it is, with what its write-ahead log holds, and changes no byte of it,
    of its -wal or of its -shm, and makes no file next to it. A read-write connection would merge the log into the
    file when it closes; a plain read-only one still writes the -shm index and makes a missing -wal and -shm.
    - No log (a clean stop): the file alone is the database, read as immutable (no locks, no -shm, no -wal).
    - A log and its index (a running server, or one that stopped without closing): the index is opened read-only, and
      SQLite reads the log itself where the index cannot be trusted.
    - A log without its index (copied without it): the file and its log are read from a copy, since SQLite reads a log
      only through an index it can write."""
    wal, shm = Path(f"{path}-wal"), Path(f"{path}-shm")
    uri = path.resolve().as_uri()
    if not wal.is_file() or wal.stat().st_size == 0:
        with closing(sqlite3.connect(f"{uri}?mode=ro&immutable=1", uri=True)) as conn:
            yield conn
    elif shm.is_file():
        with closing(sqlite3.connect(f"{uri}?mode=ro&readonly_shm=1", uri=True)) as conn:
            yield conn
    else:
        with tempfile.TemporaryDirectory() as scratch:
            copy = Path(scratch) / path.name
            shutil.copyfile(path, copy)
            shutil.copyfile(wal, f"{copy}-wal")
            with closing(sqlite3.connect(copy)) as conn:
                yield conn


def preflight(path, *, directory=None):
    """Refuse a database this code must not open, before anything writes to it. A missing or empty file is new."""
    path = Path(path)
    if not path.is_file() or path.stat().st_size == 0:
        return
    with read_only(path) as conn:
        tables = _tables(conn)
        if not tables:
            return
        _at_floor(conn, tables, directory)
        recorded = conn.execute("SELECT version, checksum FROM schema_migrations").fetchall()
        _verify(recorded, files(directory), retired(directory))


@functools.cache
def _baseline(path):
    """The baseline's statements, one by one: they run inside a transaction the loader holds, which executescript
    would commit first."""
    if not path.is_file():
        return None
    out, buffer = [], ""
    for line in path.read_text(encoding="utf-8").splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            out.append(buffer)
            buffer = ""
    if buffer.strip():
        raise MigrationError(f"{path.name} ends inside a statement")
    return tuple(out)


def _load_baseline(conn, directory=None):
    """A new file takes the baseline in one transaction: a failure leaves it empty. The write lock comes first and the
    file is looked at again under it, so of two starts that both found it empty (two processes, or a request beside the
    start) one loads the baseline and the other finds it loaded. Rows go in before the triggers, so none fires; foreign
    keys are checked at COMMIT."""
    script = _baseline(Path(directory or DIRECTORY) / BASELINE)
    if script is None:
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        if not _tables(conn):
            conn.execute("PRAGMA defer_foreign_keys=ON")
            for statement in script:
                conn.execute(statement)
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def _broken_references(conn):
    """How many rows of each (table, parent) refer to a missing row."""
    return Counter((row[0], row[2]) for row in conn.execute("PRAGMA foreign_key_check"))


def _run(conn, migration):
    """One migration in one transaction with its row, so a failure rolls back both. `conn` must be in autocommit."""
    module = _module(migration) if migration.kind == "py" else None
    # A cutover can need both the tables and the data made by an earlier migration on an install at the floor.
    for filename in CUTOVER_PREREQUISITES.get(migration.version, ()):
        path = migration.path.with_name(filename)
        if not conn.execute("SELECT 1 FROM schema_migrations WHERE version=?", (path.stem,)).fetchone():
            _run(conn, _migration(path))
    keys_off = bool(module and getattr(module, "FOREIGN_KEYS_OFF", False))
    prior = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    if keys_off:
        # A no-op inside a transaction, so it goes off before BEGIN.
        conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            # Another start may have run it since this one read the records: under the write lock, look again.
            if conn.execute("SELECT 1 FROM schema_migrations WHERE version=?", (migration.version,)).fetchone():
                conn.execute("ROLLBACK")
                return
            before = _broken_references(conn) if keys_off else None
            if module:
                module.up(conn)
            else:
                for statement in statements(migration.path.read_text(encoding="utf-8")):
                    conn.execute(statement)
            if keys_off:
                # Only references this migration broke count: a file may already hold some from before.
                after = _broken_references(conn)
                broken = {key: n - before.get(key, 0) for key, n in after.items() if n > before.get(key, 0)}
                if broken:
                    raise MigrationError(
                        f"Migration {migration.version} left rows referring to missing rows "
                        f"(table, parent: rows): {broken}. Nothing was changed."
                    )
            conn.execute(
                "INSERT INTO schema_migrations(version,checksum,applied_at) VALUES(?,?,?)",
                (migration.version, migration.checksum, H.now()),
            )
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    finally:
        if keys_off:
            conn.execute(f"PRAGMA foreign_keys={'ON' if prior else 'OFF'}")


def apply(conn, directory=None):
    """Load the baseline into a new file, or refuse one below the floor; then run each migration file the database has
    not recorded, in version order. `conn` must be in autocommit."""
    tables = _tables(conn)
    if tables:
        _at_floor(conn, tables, directory)
    else:
        _load_baseline(conn, directory)
    conn.execute(TABLE)
    known = files(directory)
    recorded = conn.execute("SELECT version, checksum FROM schema_migrations").fetchall()
    _verify(recorded, known, retired(directory))
    applied = {version for version, _ in recorded}
    for migration in known:
        if migration.version in applied:
            continue
        _run(conn, migration)
