"""The one migration authority for the hub database.

A schema change that persists is a file in this directory, numbered: `NNNN_name.sql` (its statements) or `NNNN_name.py`
(a module whose `up(conn)` makes the change, without committing). `apply` runs each file the database has not recorded,
in order. Each one runs in its own transaction together with its `schema_migrations` row, so a failure leaves neither
behind and the next start tries it again. An applied file is never edited: its checksum is recorded, and a start refuses
a database whose recorded checksum no longer matches its file.

The histories a database already has are recorded as applied, not replayed: `legacy_user_version_<N>` for hubdb's
`PRAGMA user_version` steps and `legacy_cloud_<N>` for the cloud steps in `Store.initialize`. Those steps still run where
they always did, guarded by their own markers, and nothing here runs them again.

`preflight` runs before anything writes to a database. It refuses a file newer than this code, a recorded migration this
code does not know or whose file has changed, and a file below `MIN_BRIDGE_USER_VERSION`, which a release that writes that
version must open first. `Store.initialize(adopt_legacy=True)`, the legacy import (manage.migrate_legacy), skips only that
last refusal.
"""
import hashlib
import importlib.util
import re
import sqlite3
from contextlib import closing
from pathlib import Path

from .. import hubdb as H

DIRECTORY = Path(__file__).parent
# The oldest user_version this server opens. Below it the schema is one this code's migrations do not bring up to date.
MIN_BRIDGE_USER_VERSION = 71
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
    """The migration files in version order. A file that is named like one but does not match is an error, not skipped."""
    found = []
    for path in Path(directory or DIRECTORY).glob("*"):
        if path.suffix not in (".sql", ".py") or path.stem == "__init__":
            continue
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


def _up(migration):
    spec = importlib.util.spec_from_file_location(f"migration_{migration.version}", migration.path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not callable(getattr(module, "up", None)):
        raise MigrationError(f"{migration.path.name} must define up(conn)")
    return module.up


def _legacy_known(version):
    """A legacy row is known when it names a version this code reaches, not a newer one."""
    user, cloud = LEGACY_USER.fullmatch(version), LEGACY_CLOUD.fullmatch(version)
    if user:
        return int(user.group(1)) <= len(H.MIGRATIONS)
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
        if version > len(H.MIGRATIONS):
            raise MigrationError(f"This database is at schema version {version}, newer than this code "
                                 f"({len(H.MIGRATIONS)}). Run a newer Tico.")
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
    """Each history the database already has, recorded once as applied: its user_version and its newest cloud step."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    names = [f"legacy_user_version_{version}"] if version else []
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cloud_migrations'").fetchone():
        newest = conn.execute("SELECT max(version) FROM cloud_migrations").fetchone()[0]
        if newest:
            names.append(f"legacy_cloud_{newest}")
    for name in names:
        conn.execute("INSERT OR IGNORE INTO schema_migrations(version,checksum,applied_at) VALUES(?,?,?)",
                     (name, LEGACY, H.now()))


def _run(conn, migration):
    """One migration in one transaction with its row, so a failure rolls back both. `conn` must be in autocommit."""
    up = _up(migration) if migration.kind == "py" else None
    conn.execute("BEGIN IMMEDIATE")
    try:
        if up:
            up(conn)
        else:
            for statement in statements(migration.path.read_text(encoding="utf-8")):
                conn.execute(statement)
        conn.execute("INSERT INTO schema_migrations(version,checksum,applied_at) VALUES(?,?,?)",
                     (migration.version, migration.checksum, H.now()))
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def apply(conn, directory=None):
    """Record the histories the database already has, then run each migration file it has not, in version order."""
    conn.execute(TABLE)
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
