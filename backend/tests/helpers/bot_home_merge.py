"""Helpers that other tests share, moved out of test_bot_home_merge.py so they import no test file."""

from backend import bot_home_merge, rooms
from backend.store import H


def store(api):
    return api.app.state.store


def room(c, person, bot):
    """A person's personal room with a bot, as rooms.personal_room opened them before Homes (a legacy chat)."""
    return H.open_conversation(
        c,
        person,
        [person, "bot:" + bot],
        kind="chat",
        subject="Chat with " + bot,
        scope=rooms.PERSONAL,
        owner_actor=person,
        room_key=bot,
        allow_held=True,
    )["id"]


def merge(api):
    with store(api).transaction() as c:
        return bot_home_merge.merge(c)
