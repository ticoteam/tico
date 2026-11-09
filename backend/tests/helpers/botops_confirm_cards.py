"""Helpers that other tests share, moved out of test_botops_confirm_cards.py so they import no test file."""

from backend.store import H
from backend.tests.helpers.api import post


def card_in_chat(api, card, person):
    """The card's message is in the person's own chat with BotOps, and the action is theirs and still waiting."""
    with api.app.state.store.read() as c:
        msg = H.message(c, card["message_id"])
        room = H.conversation(c, msg["conversation_id"])
        row = c.execute("SELECT owner,status FROM assistant_actions WHERE id=?", (card["action"]["id"],)).fetchone()
    assert msg["sender_id"] == "bot:botops" and msg["recipient_id"] == "human:" + person
    assert set(room["participants"]) == {"human:" + person, "bot:botops"}
    assert (row["owner"], row["status"]) == ("human:" + person, "pending")


def confirm(api, card, token):
    return post(api, f"assistant/actions/{card['action']['id']}/confirm", {}, token=token)
