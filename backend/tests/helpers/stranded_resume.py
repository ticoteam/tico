"""Shared by the tests that import it, moved out of test_stranded_resume.py so they import no test file."""

import pytest
from backend import hubdb as H
from backend import profiles as P
from backend import stranded_resume as SR
from backend import task_roundup
from backend.tests.hold_rows import set_holds
from backend.tests.helpers.api import assign, claim, post, ready, runner  # noqa: F401  (api is a fixture)
from backend.tests.helpers.profiles import add_bot, put


def online(api, *bots):
    """The bots on one computer that was just seen."""
    machine = runner(api)
    for bot in bots:
        assign(api, machine, bot)
    ready(api, machine, list(bots))
    return machine


def new_task(api, title, owner="ops"):
    return post(api, "tasks", {"owner": owner, "title": title, "body": "Please do " + title + "."})["id"]


def strand(c, tid, hours=2):
    """The task in progress since `hours` ago, its runs over, and nothing said or changed on it since."""
    then = H.shift(H.now(), hours=-hours)
    row = H.task(c, tid)
    c.execute("UPDATE tasks SET status='doing', updated_at=? WHERE id=?", (then, tid))
    c.execute("UPDATE messages SET created_at=? WHERE conversation_id=?", (then, row["conversation_id"]))
    c.execute("UPDATE history_events SET created_at=? WHERE subject_type='task' AND subject_id=?", (then, tid))
    finish(c, H.actor_id(row["owner_id"]))


def finish(c, bot):
    """The bot's queued runs ran and ended."""
    c.execute("UPDATE jobs SET state='completed' WHERE bot_id=? AND state IN ('queued','leased','running')", (bot,))


def wakes(c, bot, tid):
    """Resume wakes queued for the bot on the task."""
    return c.execute(
        "SELECT count(*) FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.bot_id=? AND j.state='queued' "
        "AND json_extract(m.refs,'$.wake')='resume' AND json_extract(m.refs,'$.stranded')=1 "
        "AND json_extract(m.refs,'$.task')=?",
        (bot, tid),
    ).fetchone()[0]


def resumes(c, tid):
    return [
        r[0]
        for r in c.execute(
            "SELECT new FROM history_events WHERE subject_type='task' AND subject_id=? AND kind=? ORDER BY created_at",
            (tid, SR.RESUMED),
        )
    ]
