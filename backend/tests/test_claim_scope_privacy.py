"""A bot's claim limit names one task (POST /api/v2/bots/{bot}/control scope). Whoever may read the bot learns that a
limit is on; only a reader of that task learns which task it is, from GET /api/v2/bots and from POST /api/v2/sql."""
from backend.store import H
from backend.tests.test_api import api, get, headers, post  # noqa: F401  (api is a fixture)


def private_task(api, requester, title):
    with api.app.state.store.transaction() as c:
        return H.task_create(c, requester, title, "Private.", "bot:ops", private=True, lint=False)


def control(api, token="ana-test", expected=200, **body):
    return post(api, "bots/ops/control", {**body, "expected_revision": get(api, "bots/ops")["revision"]},
                token=token, expected=expected)


def sql(api, statement, token):
    r = api.post("/api/v2/sql", json={"sql": statement}, headers=headers(token))
    assert r.status_code == 200, r.text
    return [list(row) for row in r.json()["rows"]]


def test_a_bot_reader_who_may_not_read_its_limit_task_learns_only_that_a_limit_is_on(api):
    task = private_task(api, "human:ana", "Ana's private check")
    control(api, action="scope", task_id=task["id"])
    ana, ben = get(api, "bots/ops"), get(api, "bots/ops", "ben-test")
    assert (ana["claim_scoped"], ana["claim_task"]) == (True, task["id"])
    assert (ben["claim_scoped"], ben["claim_task"]) == (True, None)       # Ben reads the bot, not the task
    get(api, "tasks/" + task["id"], "ben-test", expected=404)
    query = "SELECT bot, claim_task, draining FROM bot_control WHERE bot='ops'"
    assert sql(api, query, "ana-test") == [["ops", task["id"], 0]]
    assert sql(api, query, "ben-test") == [["ops", "", 0]]
    # Not around the projection either: a filter, a join or the audit of the scope.
    for statement in (f"SELECT bot FROM bot_control WHERE claim_task='{task['id']}'",
                      f"SELECT b.slug FROM bots b JOIN bot_control ctl ON ctl.bot=b.slug AND ctl.claim_task LIKE "
                      f"'{task['id'][:8]}%'",
                      f"SELECT action FROM events WHERE target='ops' AND detail LIKE '%{task['id']}%'"):
        assert sql(api, statement, "ben-test") == [], statement
    assert sql(api, f"SELECT bot FROM bot_control WHERE claim_task='{task['id']}'", "ana-test") == [["ops"]]
    control(api, action="unscope")
    assert sql(api, query, "ben-test") == [["ops", None, 0]]
    assert get(api, "bots/ops", "ben-test")["claim_scoped"] is False


def test_a_bot_manager_cannot_limit_it_to_a_task_they_may_not_read(api):
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, "human:ben", "Ben's private check", "Private.", "human:ben",
                             private=True, lint=False)  # Person-only privacy does not inherit through bot management.
    get(api, "tasks/" + task["id"], "ana-test", expected=404)
    before = get(api, "bots/ops")["revision"]
    refused = control(api, action="scope", task_id=task["id"], expected=404)
    assert refused["error"]["code"] == "not_found"
    control(api, action="scope", task_id="no-such-task", expected=404)
    after = get(api, "bots/ops")
    assert (after["revision"], after["claim_scoped"]) == (before, False)
    with api.app.state.store.read() as c:
        assert c.execute("SELECT claim_task FROM bot_control WHERE bot='ops'").fetchone() in (None, (None,))
        assert not c.execute("SELECT 1 FROM events WHERE target='ops' AND action='bot.scope'").fetchone()
