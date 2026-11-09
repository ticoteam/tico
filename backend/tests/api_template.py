"""A booted hub database built once per worker, which each test copies into its own database.

A file's own api fixture used to boot a new database (about 1.5 s of schema and migration work) for every test. Now the
first test of a file on a worker boots one template with that file's settings, and each test copies the template
(SQLite backup, as root conftest's api_database does for test_api.api) before its app boots on the copy.

What the template holds is what a boot writes from the file's settings: schema, migration markers, built-in types and
rules, and the registry_metadata rows those settings produce (the owner and access files), which are the same for
every test of the file. Identities, credentials, bots, the roster and any per-test settings are written after the copy,
by each test. The one random value a boot creates (the public roadmaps' IP key) is dropped from each copy so it is
regenerated per test. Two boot-time rows differ from a fresh boot: an empty company_profile row and a seeded-event row;
neither changes what the API returns. --fresh-api-databases builds every database from empty, as before.
"""

import re
import sqlite3
from contextlib import closing
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app import create_app

# The root conftest's rule: nothing a test signs in as, and no path of this worker, may live in the template.
IDENTITY = re.compile(
    r"human|person|people|bot|cred|secret|session|token|key|auth|member|user|account|"
    r"identit|device|machine|computer|grant|oauth|oidc"
)


_BUILT = {}


def template(request, tmp_path_factory, name, settings_for):
    """The booted template for `settings_for(directory)`, built on this worker's first use and shared by its tests.

    `settings_for` returns the module's Settings with db_path under `directory`: the settings its api fixture uses,
    without per-test parts. None with --fresh-api-databases, which builds every database from empty.
    """
    if request.config.getoption("--fresh-api-databases"):
        return None
    key = (str(tmp_path_factory.getbasetemp()), name)
    if key not in _BUILT:
        directory = tmp_path_factory.mktemp(name.replace("-", "_") + "-template")
        settings = settings_for(directory)
        # Boot exactly as a test's app does (the lifespan), so the template holds what a fresh database holds.
        with TestClient(create_app(settings)):
            pass
        check(settings.db_path, directory)
        _BUILT[key] = settings.db_path
    return _BUILT[key]


def check(path, directory):
    """Fail the template build if it holds an identity, a credential or this worker's path."""
    with closing(sqlite3.connect(path)) as source:
        tables = [
            row[0]
            for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")
            if "_fts" not in row[0]
        ]
        for table in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            if IDENTITY.search(table):
                assert not source.execute(f"SELECT 1 FROM {quoted} LIMIT 1").fetchone(), table
            else:
                assert str(directory) not in repr(source.execute(f"SELECT * FROM {quoted}").fetchall()), table
        assert {row[0] for row in source.execute("SELECT kind FROM principals")} <= {"system"}


def copy_into(template, db_path):
    """Give a test its own database: a copy of the template at `db_path` (nothing when there is no template).

    A random secret that boot creates once per database (public roadmaps' IP key) is dropped from the copy, so the
    next boot makes this test's own, as a fresh database would.
    """
    if template is None:
        return
    with closing(sqlite3.connect(f"{Path(template).as_uri()}?mode=ro", uri=True)) as source:
        with closing(sqlite3.connect(db_path)) as target:
            source.backup(target)
    with closing(sqlite3.connect(db_path)) as target:
        target.execute("DELETE FROM registry_metadata WHERE key LIKE '%secret%'")
        target.commit()
