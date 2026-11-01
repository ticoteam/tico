"""The one migration authority for the hub database.

A schema change that persists is a file in this directory, numbered: `NNNN_name.sql` (its statements) or `NNNN_name.py`
(a module whose `up(conn)` makes the change, without committing). `apply` runs each file the database has not recorded,
in order. Each one runs in its own transaction together with its `schema_migrations` row, so a failure leaves neither
behind and the next start tries it again. An applied file is never edited: its checksum is recorded, and a start refuses
a database whose recorded checksum no longer matches its file.

`hubdb.migrate` (every hubdb.connect) calls `apply` right after hubdb's numbered steps and before its own startup schema
code and the module schema code in `Store.initialize` (the SCHEMA scripts, ensure_schema and add_column calls), so a
rename or move lands before code that uses the new names. On a new file a
module's tables do not exist yet when the migrations run: a migration that touches one skips it when it is absent, and
the module's CREATE then makes the new shape.

A `.py` migration that rebuilds a table may set `FOREIGN_KEYS_OFF = True`. Foreign keys can only be switched off
outside a transaction, so the runner turns them off before BEGIN, runs `PRAGMA foreign_key_check` before COMMIT and
rolls back when the migration left a broken reference the database did not already have, then restores the setting.

The histories a database already has are recorded as applied, not replayed, once, when the file first gets
`schema_migrations`: `legacy_user_version_<N>` for hubdb's `PRAGMA user_version` steps and `legacy_cloud_<N>` for the
cloud steps in `Store.initialize`. Those steps still run where they always did, guarded by their own markers, and
nothing here runs them again. After hubdb's last step (user_version 71, `MIN_BRIDGE_USER_VERSION`) and the last cloud
step (`CLOUD_VERSION`) neither list grows: every persistent change is a file here.

`preflight` runs before anything writes to a database. It refuses a file newer than this code, a recorded migration this
code does not know or whose file has changed, and a file below `MIN_BRIDGE_USER_VERSION`, which a release that writes that
version must open first. `Store.initialize(adopt_legacy=True)`, the legacy import (manage.migrate_legacy), skips only that
last refusal.
"""
import hashlib
import importlib.util
import re
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path

from .. import hubdb as H

DIRECTORY = Path(__file__).parent
# The oldest user_version this server opens. Below it the schema is one this code's migrations do not bring up to date.
MIN_BRIDGE_USER_VERSION = 71
# The newest user_version a release wrote before hubdb's list ended at 71: step 72 is now 0030_growth_segments, guarded
# for a file that already ran it. This server opens such a file as it is; the number never grows.
NEWEST_USER_VERSION = 72
# The newest cloud_migrations version Store.initialize writes; tests/test_migrations.py keeps it in step with store.py.
CLOUD_VERSION = 75
# A legacy row has no file to hash, so its checksum says so.
LEGACY = "legacy"
TABLE = ("CREATE TABLE IF NOT EXISTS schema_migrations(version TEXT PRIMARY KEY, checksum TEXT NOT NULL, "
         "applied_at TEXT NOT NULL)")
NAME = re.compile(r"(\d{4})_[a-z0-9_]+\.(sql|py)")
LEGACY_USER = re.compile(r"legacy_user_version_(\d+)")
LEGACY_CLOUD = re.compile(r"legacy_cloud_(\d+)")


class MigrationError(RuntimeError):
    """This code cannot open the database. The server does not start on it."""


class Migration:
    """One file: its version is its name without the extension, its checksum is the SHA-256 of the file's bytes."""

    def __init__(self, path):
        self.path = path
        self.version = path.stem
        self.kind = NAME.fullmatch(path.name).group(2)
        self.checksum = hashlib.sha256(path.read_bytes()).hexdigest()


def files(directory=None):
    """The migration files in version order. A name that starts with a digit must match NNNN_name.sql or .py: a file
    named like one but not matching is an error, not skipped. Only digit-led names are listed, so this module does not
    read every tracked file (a bare "*" listing makes the dependency map treat any change as its input)."""
    found = []
    for path in Path(directory or DIRECTORY).glob("[0-9]*"):
        if not NAME.fullmatch(path.name):
            raise MigrationError(f"{path.name} is not a migration name: use NNNN_name.sql or NNNN_name.py")
        found.append(Migration(path))
    found.sort(key=lambda migration: migration.version)
    numbers = [migration.version[:4] for migration in found]
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


def _legacy_known(version):
    """A legacy row is known when it names a version this code reaches, not a newer one."""
    user, cloud = LEGACY_USER.fullmatch(version), LEGACY_CLOUD.fullmatch(version)
    if user:
        return int(user.group(1)) <= NEWEST_USER_VERSION
    if cloud:
        return int(cloud.group(1)) <= CLOUD_VERSION
    return False


def _verify(recorded, known):
    """Refuse a recorded version this code does not know, and a file whose checksum changed after it was applied."""
    by_version = {migration.version: migration for migration in known}
    for version, checksum in recorded:
        if version in by_version:
            current = by_version[version].checksum
            if checksum != current:
                raise MigrationError(
                    f"Migration {version} was applied to this database with checksum {checksum[:12]}, but its file now "
                    f"has {current[:12]}. An applied migration is never edited: make the change in a new file.")
        elif not (checksum == LEGACY and _legacy_known(version)):
            raise MigrationError(
                f"This database has schema migration {version}, which this code does not know. It was written by a "
                "newer Tico: run that release, or restore a backup taken before it.")


def preflight(path, *, adopt_legacy=False, directory=None):
    """Refuse a database this code must not open, before anything writes to it. A missing or empty file is new."""
    path = Path(path)
    if not path.is_file() or path.stat().st_size == 0:
        return
    with closing(sqlite3.connect(str(path))) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not tables:
            return
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if "schema_migrations" in tables:
            _verify(conn.execute("SELECT version, checksum FROM schema_migrations").fetchall(), files(directory))
        if version > NEWEST_USER_VERSION:
            raise MigrationError(f"This database is at schema version {version}, newer than this code "
                                 f"({NEWEST_USER_VERSION}). Run a newer Tico.")
        if version < MIN_BRIDGE_USER_VERSION and not adopt_legacy:
            if version == 0 and not {"cloud_migrations", "schema_migrations"} & tables:
                # No Tico history: a new file that some module touched first (hubdb.connect builds it), or one this
                # Tico did not make, which hubdb refuses by name.
                H._refuse_foreign(conn)
                return
            raise MigrationError(
                f"This database is at schema version {version}, below {MIN_BRIDGE_USER_VERSION}, the oldest this server "
                f"upgrades. Start it once with Tico v0.5.x (the release that writes version {MIN_BRIDGE_USER_VERSION}), "
                "then upgrade to this release.")


def _record_legacy(conn):
    """Each history the database already has, recorded as applied when it first gets schema_migrations: its
    user_version and its newest cloud step. A new file has no cloud steps yet at that point (they run after the
    migrations), so it records only its user_version."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    names = [f"legacy_user_version_{version}"] if version else []
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cloud_migrations'").fetchone():
        newest = conn.execute("SELECT max(version) FROM cloud_migrations").fetchone()[0]
        if newest:
            names.append(f"legacy_cloud_{newest}")
    for name in names:
        conn.execute("INSERT OR IGNORE INTO schema_migrations(version,checksum,applied_at) VALUES(?,?,?)",
                     (name, LEGACY, H.now()))


def _broken_references(conn):
    """How many rows of each (table, parent) refer to a missing row."""
    return Counter((row[0], row[2]) for row in conn.execute("PRAGMA foreign_key_check"))


def _run(conn, migration):
    """One migration in one transaction with its row, so a failure rolls back both. `conn` must be in autocommit."""
    module = _module(migration) if migration.kind == "py" else None
    keys_off = bool(module and getattr(module, "FOREIGN_KEYS_OFF", False))
    prior = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    if keys_off:
        # A no-op inside a transaction, so it goes off before BEGIN.
        conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
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
                    raise MigrationError(f"Migration {migration.version} left rows referring to missing rows "
                                         f"(table, parent: rows): {broken}. Nothing was changed.")
            conn.execute("INSERT INTO schema_migrations(version,checksum,applied_at) VALUES(?,?,?)",
                         (migration.version, migration.checksum, H.now()))
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    finally:
        if keys_off:
            conn.execute(f"PRAGMA foreign_keys={'ON' if prior else 'OFF'}")


def apply(conn, directory=None):
    """Record the histories the database already has, then run each migration file it has not, in version order."""
    adopting = not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'").fetchone()
    conn.execute(TABLE)
    if adopting:
        _record_legacy(conn)
    known = files(directory)
    recorded = conn.execute("SELECT version, checksum FROM schema_migrations").fetchall()
    _verify(recorded, known)
    applied = {version for version, _ in recorded}
    newest = max((m.version for m in known if m.version in applied), default="")
    for migration in known:
        if migration.version in applied:
            continue
        if migration.version < newest:
            raise MigrationError(f"Migration {migration.version} sorts before {newest}, which this database has already "
                                 "applied. Renumber it.")
        _run(conn, migration)
