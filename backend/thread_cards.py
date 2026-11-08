"""The update card (spec 1, Updates): an update keeps its own structure and page, and also shows in its author's Home
as one card that links back to it. The card is one line and a reference, never the update's body, so the Home
reads as the thread it is and the update is read on its own page.

The card is written as the author, in the update's own transaction. A bot's card is its own post in its own Home, so
the bot never wakes on it (a message never wakes its author). A person's card is their post in their own Home. The
Home's audience decides who reads it (threads.post checks it on every read).
"""


def post_card(c, uid, author, kind, day):
    """The card for a new update `uid` by `author` (a bot slug or `human:<id>`), in the author's Home."""
    from . import threads, updates
    actor = updates.author_actor(author)
    home = threads.home(c, actor, create=True)
    label = ("Week in review" if kind == "weekly" else "Daily update") + ", " + day
    return threads.post(c, actor, home["id"], label, kind="notice",
                        refs={"card": "update", "update": {"id": uid, "kind": kind, "day": day, "author": actor}})
