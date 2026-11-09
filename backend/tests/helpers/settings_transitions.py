"""Helpers that other tests share, moved out of test_settings_transitions.py so they import no test file."""

from backend.store import encode
from backend import bot_settings as BS


def _company_default(api, bot, model="gpt-6-sol"):
    """Set a company Codex default while `bot` names neither setting."""
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO registry_metadata VALUES('providers',?)",
            (encode({"enabled": ["openai"], "runtime": "codex", "model": model, "revision": 1}),),
        )
        BS.write(c, bot, {'name': bot})
