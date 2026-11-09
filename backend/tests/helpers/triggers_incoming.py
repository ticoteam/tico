"""Helpers that other tests share, moved out of test_triggers_incoming.py so they import no test file."""

from backend.auth import Identity


def as_bot(api, slug):
    """The bot itself, as an agent credential (backend/tests/test_company.py bot)."""
    from backend.store import H

    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET state='active' WHERE slug=?", (slug,))
        c.execute(
            "INSERT OR IGNORE INTO agents(bot,harness,token_hash,created_at,created_by) "
            "VALUES(?,'hermes',?,?,'human:ana')",
            (slug, "x-" + slug, H.now()),
        )
    api.app.state.store.settings.test_identities[slug + "-test"] = Identity("bot:" + slug, "bot", agent="hermes")


def stored_rows(api, sql, *args):
    with api.app.state.store.read() as c:
        return c.execute(sql, args).fetchall()
