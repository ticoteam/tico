"""Helpers that other tests share, moved out of test_sql.py so they import no test file."""

import pytest
from backend.tests.helpers.api import assign, claim, headers, post, ready, runner


def query(api, sql, token="ana-test", expected=200, **body):
    r = api.post("/api/v2/sql", json={"sql": sql, **body}, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def column(api, sql, token="ana-test"):
    return sorted(row[0] for row in query(api, sql, token)["rows"])


@pytest.fixture
def world(api):
    """Two people's private Tico rooms, a private bot's task, and a ops turn granted Ana's room."""
    with api.app.state.store.transaction() as c:
        c.execute("CREATE TABLE IF NOT EXISTS _litestream_seq(id INTEGER PRIMARY KEY, seq INTEGER)")
        c.execute("INSERT INTO _litestream_seq VALUES(1, 1)")
    ana_room = post(api, "chat/ops", {"text": "Ana private context."})["conversation_id"]
    steven_room = post(api, "chat/ops", {"text": "Ben private context."}, token="ben-test")["conversation_id"]
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    attempt = claim(api, machine)
    with api.app.state.store.transaction() as c:  # a person's Mac: its credential reads as them
        c.execute("UPDATE runners SET platform='darwin' WHERE id=?", (machine["runner_id"],))
    private = post(api, "tasks", {"owner": "inbox", "title": "Sort the inbox", "body": "Private to Ana."})
    coo_task = post(api, "tasks", {"owner": "ops", "title": "Summarize the week", "body": "For ops."})
    finance_task = post(api, "tasks", {"owner": "finance", "title": "Close the books", "body": "For finance."})
    return {
        "ana_room": ana_room,
        "steven_room": steven_room,
        "machine": machine,
        "attempt": attempt,
        "private": private,
        "coo_task": coo_task,
        "finance_task": finance_task,
    }
