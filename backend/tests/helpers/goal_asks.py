"""Helpers that other tests share, moved out of test_goal_asks.py so they import no test file."""

from backend import strategy_bot
from backend import goal_asks as GA
from backend.store import H
from backend.tests.helpers.api import get
from backend import bot_settings as BS


MANAGER = "bot:strategy-owner"


def goal_manager(api, level=1):
    """The Strategy Owner at `level`, chosen by the owner (Passive unless asked otherwise); None leaves it as built, at
    the default level."""
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO bots(id,slug,display_name,effort,state,created_at) "
            "VALUES('strategy-owner','strategy-owner','Strategy Owner','','active',?)",
            (H.now(),),
        )
        c.execute("INSERT INTO bot_config(bot,team,owner_id) VALUES('strategy-owner',NULL,'ana')")
        BS.write(c, 'strategy-owner', {"runtime": "fake", "host": "keeper"})
    raise_level(api, level)


def raise_level(api, level):
    """The owner chooses `level` (`strategy_bot.set_level`); None chooses nothing."""
    if level is not None:
        with api.app.state.store.transaction() as c:
            strategy_bot.set_level(c, "human:ana", level)


def asks(api, owner=None):
    with api.app.state.store.read() as c:
        return [
            dict(r)
            for r in c.execute(
                "SELECT t.id, t.title, t.body, t.owner_id, t.status, a.kind, a.ref FROM goal_asks a JOIN tasks t ON t.id=a.task_id "
                "WHERE t.requester_id=?" + (" AND a.owner=?" if owner else "") + " ORDER BY t.created_at",
                (MANAGER, owner) if owner else (MANAGER,),
            )
        ]


def sync(api, at=None):
    with api.app.state.store.transaction() as c:
        return GA.sync(c, api.app.state.auth.owner_id(c), at)


def needs_tasks(api, token):
    """The tasks in a person's Needs you (one list: there is no "older" part any more)."""
    return {str(i.get("task_id") or i["id"]) for i in get(api, "needs-you", token)["items"]}
