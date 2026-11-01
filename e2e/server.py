"""Disposable servers over copies of a hub database, the way an upgrade or a restore starts one."""
import dataclasses
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path


def snapshot(source, target):
    """A consistent copy of `source` made with SQLite's backup API (committed WAL pages included), never a file copy."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(str(source))) as src, closing(sqlite3.connect(str(target))) as dst:
        src.backup(dst)


def settings_for(directory, db_path):
    """The `api` fixture's settings (fictional company, test identities, its registry) over `db_path`."""
    from backend.tests.test_api import api_settings
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    return dataclasses.replace(api_settings(directory), db_path=Path(db_path))


@contextmanager
def serve(settings):
    """A TestClient on a fresh app over `settings`: its lifespan runs the same startup a server does."""
    from fastapi.testclient import TestClient
    from backend.app import create_app
    app = create_app(settings)
    with TestClient(app) as client:
        yield client
