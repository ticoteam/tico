"""Helpers that other tests share, moved out of test_task_news_delivery.py so they import no test file."""

from backend import threads


def rows(api, sql, *args):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def delivery(api, message_id, recipient):
    found = rows(api, "SELECT * FROM message_deliveries WHERE message_id=? AND recipient=?", message_id, recipient)
    return found[0] if found else None


def budget_thread(api):
    """A members thread Ana started with Ben in it."""
    with api.app.state.store.transaction() as c:
        return threads.create(c, "human:ana", title="Budget", audience="members", members=["human:ben"])["id"]


def unread_for(api, actor, thread_id):
    with api.app.state.store.read() as c:
        return threads.unread(c, threads.actor_viewer(c, actor), thread_id)["unread"]
