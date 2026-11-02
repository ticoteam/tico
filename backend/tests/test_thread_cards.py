"""Update cards: a new update leaves one card in its author's Home, linking back to the update and never repeating its
body. The card is written as the author, so a bot never wakes on its own card, and the Home's audience decides who
reads it. An edit the same day keeps the one card."""

from backend import hubdb as H
from backend import threads, updates
from backend.auth import Identity
from backend.tests.test_api import api  # noqa: F401
from backend.tests.test_bot_contact import bot

DAY = "2026-10-30"
ANA = Identity("human:ana", "owner", "ana@acme.example")
CARA = Identity("human:cara", "human", "cara@acme.example")


def home_cards(c, actor):
    home = threads.home(c, actor, create=True)
    rows = c.execute("SELECT id, body, refs_json FROM messages WHERE conversation_id=? AND kind='notice' "
                     "ORDER BY seq", (home["id"],)).fetchall()
    cards = []
    for row in rows:
        refs = H._json(row["refs_json"], {}) or {}
        if refs.get("card") == "update":
            cards.append({"id": row["id"], "body": row["body"], "update": refs["update"]})
    return cards


def test_a_new_update_leaves_one_card_in_its_authors_home_that_links_back(api):
    store = api.app.state.store
    with store.transaction() as c:
        posted = updates.post(c, "human:ana", "- Signed the office lease.", kind="daily", day=DAY)
    with store.transaction() as c:
        cards = home_cards(c, "human:ana")
    assert [card["update"]["id"] for card in cards] == [posted["id"]]
    assert cards[0]["update"]["day"] == DAY and cards[0]["update"]["author"] == "human:ana"
    assert "Signed the office lease" not in cards[0]["body"]


def test_an_edit_the_same_day_keeps_the_one_card(api):
    store = api.app.state.store
    with store.transaction() as c:
        updates.post(c, "human:ana", "- Signed the office lease.", kind="daily", day=DAY)
        updates.post(c, "human:ana", "- Signed the office lease and the offer.", kind="daily", day=DAY)
        assert len(home_cards(c, "human:ana")) == 1


def test_the_card_is_readable_exactly_where_its_home_is(api):
    """The card has no audience of its own: whoever reads the author's Home reads the card, and nobody else does."""
    store = api.app.state.store
    with store.transaction() as c:
        updates.post(c, "human:ana", "- Signed the office lease.", kind="daily", day=DAY)
        home = threads.home(c, "human:ana")["id"]
        card = home_cards(c, "human:ana")[0]["id"]
        for who in (ANA, CARA):
            viewer = threads.viewer(api.app.state.auth, who)
            assert threads.readable(c, viewer, card) == threads.readable(c, viewer, home)
        assert threads.readable(c, threads.viewer(api.app.state.auth, ANA), card)


def test_a_bots_card_never_wakes_the_bot(api):
    bot(api, "boss", "Manager", reports_to="human:ana")
    store = api.app.state.store
    with store.transaction() as c:
        updates.post(c, "boss", "- Planned the launch.", kind="daily", day=DAY)
        cards = home_cards(c, "bot:boss")
        assert len(cards) == 1
        woke = c.execute("SELECT COUNT(*) FROM message_deliveries WHERE message_id=? AND recipient='bot:boss'",
                         (cards[0]["id"],)).fetchone()[0]
    assert woke == 0
