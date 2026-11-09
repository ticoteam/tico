"""Helpers that other tests share, moved out of test_task_caps.py so they import no test file."""

from backend.auth import Identity
from backend.store import H


def as_bot(api, slug, token):
    """The bot signs in with its own agent credential, as the hub's agents do: one agents row per bot."""
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT OR REPLACE INTO agents(bot,harness,token_hash,created_at,created_by) VALUES(?,?,?,?,?)",
            (slug, "test", "x-" + slug, H.now(), "human:ana"),
        )
    api.app.state.store.settings.test_identities[token] = Identity(f"bot:{slug}", "bot", agent="test")
