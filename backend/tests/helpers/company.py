"""The fictional company every API test starts with, and a store that has it without the app.

`seed` writes the company into a new database: seven bots, three people, Ben an admin, the inbox bot Ana's alone,
and onboarding done. `company_store` is a Store on such a database with no FastAPI app around it, for tests of code
below the routes (the task engine, the task calls): they need a transaction, not a client."""

import sqlite3
from contextlib import closing

from backend import bot_settings as BS
from backend.store import H, Store, encode
from backend.tests.helpers.api import api_settings, restrict


def seed(c):
    bots = {
        slug: {"name": slug, "runtime": "fake", "status": "active"}
        for slug in ("coo", "ops", "cpo", "product-design", "finance", "inbox", "doc-updater")
    }
    H.sync_registry(c, bots, {"people": [{"id": p, "email": p + "@acme.example"} for p in ("ana", "ben", "cara")]})
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
    c.execute("INSERT INTO registry_metadata VALUES('onboarding',?)", (encode({"completed": "2026-01-01T00:00:00Z"}),))


def company_store(tmp_path, api_database=None):
    """A Store on a new database in `tmp_path` (a copy of `api_database`, the worker's schema snapshot, when given)
    with the company in it."""
    settings = api_settings(tmp_path)
    if api_database is not None:
        with closing(sqlite3.connect(api_database.as_uri() + "?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(settings.db_path)) as target:
                source.backup(target)
    store = Store(settings)
    store.initialize()
    with store.transaction() as c:
        seed(c)
    return store


def bot(api, slug):
    """The bot itself, as an agent credential (backend/tests/test_strategy_bot.py as_bot)."""
    from backend.auth import Identity

    token = slug + "-test"
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET state='active' WHERE slug=?", (slug,))
        c.execute(
            "INSERT OR IGNORE INTO agents(bot,harness,token_hash,created_at,created_by) "
            "VALUES(?,'hermes',?,?,'human:ana')",
            (slug, "x-" + slug, H.now()),
        )
    api.app.state.store.settings.test_identities[token] = Identity("bot:" + slug, "bot", agent="hermes")
    return token


def with_strategy_bot(api, level=1):
    """The Strategy Bot at `level` (the owner's choice; Passive unless asked otherwise; None leaves it as built, at the
    default level), as its own agent credential."""
    from backend.tests.helpers.goal_asks import goal_manager

    goal_manager(api, level)
    return bot(api, "goal-manager")


def propose_why(api, goal_id, why, token, reason="The vision is about rental owners."):
    from backend.tests.helpers.api import post

    return post(
        api,
        "goal-proposals",
        {"kind": "goal_why", "goal_id": goal_id, "payload": {"why": why}, "reason": reason},
        token=token,
    )["proposal"]
