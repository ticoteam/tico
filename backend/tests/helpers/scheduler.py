"""Helpers that other tests share, moved out of test_scheduler.py so they import no test file."""

from backend.store import H


def _wake_by_hand(c, message_id, bot):
    """H.say marks a keeper notice quiet, so it queues no job. This queues it as a notice that wakes the bot: the job
    enqueue writes for a notice that is not quiet."""
    c.execute(
        "INSERT INTO jobs(id,message_id,bot_id,created_at) VALUES(?,?,?,?)", (message_id, message_id, bot, H.now())
    )
    return message_id
