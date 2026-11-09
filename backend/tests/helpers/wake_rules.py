"""Helpers that other tests share, moved out of test_wake_rules.py so they import no test file."""

import json
from backend import hubdb as H
from backend import wake_rules as W
from backend.tests.helpers.api import assign, post, ready, runner


def setup(api):
    r = runner(api)
    assign(api, r, "ops")
    assign(api, r, "cpo")
    ready(api, r, ["ops", "cpo"])
    return r


def claim(api, r, bot):
    return post(api, "jobs/claim", {"next_run": True, "bot": bot}, token=r["token"])["attempt"]


def finish(api, r, attempt):
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "t-" + attempt["id"]}, token=r["token"])
    post(
        api,
        f"attempts/{attempt['id']}/complete",
        {"outcome": "completed", "text": "ok", "last_seq": 0},
        token=r["token"],
    )


def jobs(c, bot):
    return c.execute("SELECT count(*) FROM jobs WHERE bot_id=?", (bot,)).fetchone()[0]


def skips(c):
    return [
        {**dict(row), "detail": json.loads(row["detail"])}
        for row in c.execute("SELECT * FROM events WHERE action=? ORDER BY created_at, rowid", (W.SKIPPED,))
    ]


def parked_task(c, title="Build 2/9", blocker_title="Build 1/9"):
    """cpo's task, filed by ops, blocked by an open task of ops's."""
    blocker = H.task_create(c, "bot:ops", blocker_title, "First", "bot:ops")
    task = H.task_create(c, "bot:ops", title, "Second", "bot:cpo")
    c.execute(
        "INSERT INTO task_relations(from_task,to_task,kind,created_at) VALUES(?,?,'blocks',?)",
        (blocker["id"], task["id"], H.now()),
    )
    c.execute("DELETE FROM jobs")
    return H.task(c, task["id"]), blocker


def as_cause(actor):
    """Run the next writes as `actor`'s request, as backend/app.py request_guard does."""

    class Cause:
        def __enter__(self):
            self.token = W.CAUSE.set(actor)

        def __exit__(self, *exc):
            W.CAUSE.reset(self.token)

    return Cause()


def news(c, task, body="Step moved: Build -> Review 1", refs=None):
    """A keeper notice about `task` to its owner, the kind a step move or relation change writes."""
    return H._wake(c, task, task["owner_id"], body, refs=refs)
