"""Helpers that other tests share, moved out of test_strategy_bot.py so they import no test file."""

import uuid
from backend.auth import Identity
from backend.store import H


def as_bot(api, slug):
    """The bot itself, as an agent credential (the way backend/tests/test_health.py signs one in)."""
    token = slug + "-test"
    with api.app.state.store.transaction() as c:
        bot_id = (H.resolve_bot(c, slug) or {}).get("id", slug)
        c.execute("UPDATE bots SET state='active' WHERE id=?", (bot_id,))
        c.execute(
            "INSERT OR IGNORE INTO agents(bot,harness,token_hash,created_at,created_by) "
            "VALUES(?,'hermes',?,?,'human:morgan')",
            (bot_id, "x-" + bot_id, H.now()),
        )
    api.app.state.store.settings.test_identities[token] = Identity(H.bot_actor(bot_id), "bot", agent="hermes")
    return {"Authorization": "Bearer " + token, "Idempotency-Key": str(uuid.uuid4())}
