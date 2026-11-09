"""Helpers that other tests share, moved out of test_urgent_news.py so they import no test file."""

from backend import hubdb as H


def sent_job(c, task, target, text, refs=None):
    msg = H._wake(c, H.task(c, task['id']), target, text, refs=refs)
    job = c.execute('SELECT * FROM jobs WHERE message_id=? AND bot_id=?', (msg['id'], H.actor_id(target))).fetchone()
    return msg, dict(job) if job else None
