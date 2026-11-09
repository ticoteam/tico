"""Helpers that other tests share, moved out of test_strategy_bot_level.py so they import no test file."""

from backend.auth import Identity
from backend.tests.helpers.api import post

OWNER = "human:ana"


def level(api, n, token="ana-test", expected=200):
    return post(api, "strategy-bot/level", {"level": n}, token=token, expected=expected)


def via_assistant(api, confirmed=False, via="assistant"):
    """The owner as the Assistant (or BotOps) acting for them; `confirmed`: their own tap on a Confirm card it made."""
    token = f"ana-via-{via}" + ("-tap" if confirmed else "")
    api.app.state.store.settings.test_identities[token] = Identity(
        OWNER, "owner", "ana@acme.example", via=via, confirmed=confirmed
    )
    return token
