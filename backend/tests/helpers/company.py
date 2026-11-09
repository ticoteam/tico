"""Helpers that other tests share, moved out of test_company.py so they import no test file."""

from backend.auth import Identity
from backend.store import H
from backend.tests.helpers.api import post
from backend.tests.helpers.goal_asks import goal_manager


def bot(api, slug):
    """The bot itself, as an agent credential (backend/tests/test_strategy_bot.py as_bot)."""
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
    goal_manager(api, level)
    return bot(api, "goal-manager")


def propose_why(api, goal_id, why, token, reason="The vision is about rental owners."):
    return post(
        api,
        "goal-proposals",
        {"kind": "goal_why", "goal_id": goal_id, "payload": {"why": why}, "reason": reason},
        token=token,
    )["proposal"]
