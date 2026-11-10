"""Helpers that other tests share, moved out of test_task_moves_routes.py so they import no test file."""

from backend.auth import Identity
from backend.store import H


def as_bot(api, slug="ops"):
    """A token for a bot of the company, as its own command would carry: an agent's (backend/agents.py)."""
    token = slug + "-bot-test"
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT OR IGNORE INTO agents(bot,harness,token_hash,created_at,created_by) VALUES(?,?,?,?,?)",
            (slug, "hermes", "x-" + slug, H.now(), "human:ana"),
        )
    api.app.state.auth.settings.test_identities[token] = Identity("bot:" + slug, "bot", agent="hermes")
    return token
