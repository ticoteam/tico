"""Helpers that other tests share, moved out of test_watchers.py so they import no test file."""

from backend.tests.helpers.api import assign, post, ready, runner


def report(api, r, *events, bot="ops", name="hq-tickets", code=0, timed_out=False, output="", expected=200):
    return post(
        api,
        "runners/watchers",
        {
            "bot": bot,
            "name": name,
            "started": "2026-10-01T10:00:00Z",
            "finished": "2026-10-01T10:00:01Z",
            "exit": code,
            "timed_out": timed_out,
            "every": 300,
            "output": output,
            "events": list(events),
        },
        token=r["token"],
        expected=expected,
    )


def hosted(api, bot="ops"):
    r = runner(api)
    assign(api, r, bot)
    ready(api, r, [bot])
    return r


def tasks(api, bot="ops"):
    with api.app.state.store.read() as c:
        return [
            dict(t)
            for t in c.execute(
                "SELECT id,title,body,status,conversation_id FROM tasks WHERE owner_id=? ORDER BY created_at",
                ("bot:" + bot,),
            )
        ]


def wakes(api, bot="ops"):
    with api.app.state.store.read() as c:
        return c.execute("SELECT count(*) FROM jobs WHERE bot_id=? AND state='queued'", (bot,)).fetchone()[0]


def task(key, title="Support: the board is empty", body="Ticket text, quoted as untrusted data."):
    return {"op": "task", "key": key, "title": title, "body": body}
