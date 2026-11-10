"""The body of the `api` fixture (conftest.py): an app on a new database with the fictional company in it.

Benchmarks, the rollback check and query tests that need more than one company call api_client directly."""

import sqlite3
from contextlib import closing

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.tests.helpers.api import api_settings
from backend.tests.helpers.company import seed


def api_client(tmp_path, api_database=None):
    """Yield a test client on a new database in `tmp_path` (a copy of `api_database`, the worker's schema snapshot, when
    given): seven bots, three people, Ben an admin, the inbox bot Ana's alone, and onboarding done."""
    settings = api_settings(tmp_path)
    if api_database is not None:
        with closing(sqlite3.connect(api_database.as_uri() + "?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(settings.db_path)) as target:
                source.backup(target)
    app = create_app(settings)
    with TestClient(app) as client:
        with app.state.store.transaction() as c:
            seed(c)
        yield client
