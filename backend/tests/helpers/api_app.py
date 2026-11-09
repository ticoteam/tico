"""The body of the `api` fixture (conftest.py): an app on a new database with the fictional company in it.

Benchmarks, the rollback check and query tests that need more than one company call api_client directly."""

import sqlite3
from contextlib import closing

from fastapi.testclient import TestClient

from backend import bot_settings as BS
from backend.app import create_app
from backend.store import H, encode
from backend.tests.helpers.api import api_settings, restrict


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
            bots = {
                slug: {"name": slug, "runtime": "fake", "status": "active"}
                for slug in ("coo", "ops", "cpo", "product-design", "finance", "inbox", "doc-updater")
            }
            H.sync_registry(
                c, bots, {"people": [{"id": p, "email": p + "@acme.example"} for p in ("ana", "ben", "cara")]}
            )
            c.execute(
                "INSERT INTO registry_metadata VALUES('people',?)",
                (
                    encode(
                        {
                            "people": [
                                {"id": "ana", "email": "ana@acme.example", "primary_for": ["*"]},
                                {
                                    "id": "ben",
                                    "email": "ben@acme.example",
                                    "primary_for": ["cpo", "product-design", "ops"],
                                },
                                {"id": "cara", "email": "cara@acme.example"},
                            ]
                        }
                    ),
                ),
            )
            for slug, config in bots.items():
                product = slug in ("cpo", "product-design")
                c.execute(
                    'INSERT INTO bot_config(bot,team,owner_id) VALUES(?,?,?)',
                    (slug, 'product' if product else None, 'ben' if product else 'ana'),
                )
                BS.write(c, slug, config)
            # The company's mail bot is Ana's alone: nobody else sees it, reads it or writes to it.
            restrict(c, "inbox", people=["ana"])
            # A company with a fleet this size is long past first run; the wizard belongs to
            # backend/tests/test_onboarding.py, not to every other test's front page.
            c.execute(
                "INSERT INTO registry_metadata VALUES('onboarding',?)", (encode({"completed": "2026-01-01T00:00:00Z"}),)
            )
        yield client
