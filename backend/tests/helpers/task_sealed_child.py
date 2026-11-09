"""Helpers that other tests share, moved out of test_task_sealed_child.py so they import no test file."""

from backend.tests.helpers.api import assign, claim, headers, post, ready, runner


ROUTE = "ask them with `hub task question`, or have them file the subtask"


def carry(api, attempt, row):
    """The run also carries `row`, as when its prompt brought that task in."""
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET carried_by=? WHERE id=?", (attempt["id"], row["id"]))


def run_for(api, bot):
    """A run of `bot` on its own computer, on the oldest work waiting for it (a chat when nothing is)."""
    machine = runner(api)
    assign(api, machine, bot)
    ready(api, machine, [bot])
    with api.app.state.store.read() as c:
        waiting = c.execute("SELECT 1 FROM jobs WHERE bot_id=? AND state='queued'", (bot,)).fetchone()
    if not waiting:
        post(api, "chat/" + bot, {"text": "Please look at your queue."})
    return claim(api, machine, bot)


def sees(api, token, words):
    r = api.post("/api/v2/sql", json={"sql": "SELECT id,title,body FROM tasks"}, headers=headers(token))
    assert r.status_code == 200, r.text
    return words in r.text
