"""Helpers that other tests share, moved out of test_rules.py so they import no test file."""

from backend import hubdb as H


def plain_task(c, title="Plain work", clear=True):
    task = H.task_create(c, "bot:ops", title, "Do it", "bot:cpo")
    if clear:
        c.execute("DELETE FROM jobs")
    return H.task(c, task["id"])
