"""Helpers that other tests share, moved out of test_question_tasks.py so they import no test file."""

from backend.tests.helpers.api import assign, claim, post, ready, runner


def _work(api, title="Pick a launch date", **extra):
    """A task of the bot's and a run on it: a question asked from that run is filed under it."""
    r = runner(api, "ana")
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    work = post(
        api, "tasks", {"owner": "ops", "title": title, "body": "Choose when the Acme launch goes out.", **extra}
    )
    return work, claim(api, r)


def _read(api, sql, args=()):
    with api.app.state.store.read() as c:
        return c.execute(sql, args).fetchall()


def _mark(api):
    return _read(api, "SELECT coalesce(max(rowid), 0) FROM messages")[0][0]


def _to_ops(api, since):
    return [
        r[0]
        for r in _read(
            api, "SELECT body FROM messages WHERE recipient_id='bot:ops' AND rowid>? ORDER BY rowid", (since,)
        )
    ]


def _answer(api, task_id, body, token="ana-test", expected=200):
    return post(api, f"tasks/{task_id}/answers", {"target": {"task": task_id}, **body}, token=token, expected=expected)


def _bot_only_private_parent(api):
    """A private task with no person on it, another bot's request of ops, and an ops run on it."""
    from backend.tests.helpers.api import unrelated_task_managers
    from backend.tests.helpers.task_readers import run_on, task

    with api.app.state.store.transaction() as c:
        unrelated_task_managers(c)
    parent = task(api, "bot:cpo", "bot:ops", "Zip triage")
    return parent, run_on(api)


def _to(api, who, since):
    return [
        r[0]
        for r in _read(api, "SELECT body FROM messages WHERE recipient_id=? AND rowid>? ORDER BY rowid", (who, since))
    ]


def keep_asking(api, on=False):
    """Set the team's "bots go ahead" rule. Off, a bot may ask with a reversible default (backend/ask_refusals.py);
    turned back on after, that ask goes ahead at its deadline, as one filed before the refusal does."""
    from backend import team_rules

    with api.app.state.store.transaction() as c:
        team_rules.save(c, "human:ana", {"bots_go_ahead": on})
