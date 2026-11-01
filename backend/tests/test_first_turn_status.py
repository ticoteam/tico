"""A real newly created bot can wait before it has ever posted a stored status."""

from backend import events as E
from backend.auth import Identity
from backend.store import H
from backend.tests.test_api import api, as_member, assign, claim, get, post, ready, restrict, runner  # noqa: F401
from backend.tests.test_run_view import events, task_attempt
from backend.tests.test_shared_logins import queued_login  # noqa: F401


def scribe_id(api):
    with api.app.state.store.read() as c:
        return H.resolve_bot(c, "scribe")["id"]     # a new bot's id is a UUID; status rows and claims key by it


def first_wait(api):
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    holder_task, holder = task_attempt(api, machine, "Count the private ledger")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET private=1 WHERE id=?", (holder_task,))
    post(api, "bots", {"slug": "scribe", "display_name": "Scribe", "description": "Writes summaries",
                       "status": "active", "model": "gpt-6.1-sol", "harness": "codex",
                       "runner_id": machine["runner_id"]})
    scribe = scribe_id(api)
    ready(api, machine, ["ops", scribe])
    task = post(api, "tasks", {"owner_id": "scribe", "title": "Count the public words", "body": "Count them."})
    attempt = claim(api, machine)
    assert attempt["bot_id"] == scribe and attempt["task"]["id"] == task["id"]
    with api.app.state.store.read() as c:
        assert H.status(c, scribe) is None  # Never delete a row to manufacture the regression.
        assert c.execute("SELECT state FROM attempts WHERE id=?", (attempt["id"],)).fetchone()[0] == "leased"
    events(api, machine, attempt["id"], [("status", {
        "waiting": "Waiting for this computer's ChatGPT login to be free", "login": "this computer's ChatGPT login",
        "holder": "ops", "holder_attempt": holder["id"], "since": 1_760_900_760, "position": 2})])
    return machine, task, attempt


def test_a_new_bots_first_login_wait_has_a_status_and_is_listed_without_storing_one(api, queued_login):
    machine, task, attempt = first_wait(api)
    scribe = scribe_id(api)
    status = get(api, "status?bot=scribe")
    assert status["status"] is not None
    row = status["status"]
    assert row["state"] == "waiting_bot"
    assert row["waiting"] == row["focus"]
    assert "held by ops on" in row["focus"] and "Count the private ledger" in row["focus"]
    assert "since 19:06Z; position 2" in row["focus"]
    assert row["open_tasks"] == 1 and status["history"] == []
    assert [s for s in get(api, "status")["bots"] if s and s["bot"] == scribe] == [row]
    assert get(api, f"tasks/{task['id']}")["execution"]["waiting"] == row["waiting"]
    assert not [s for s in get(api, "health")["stuck"] if s["bot"] == scribe]
    with api.app.state.store.read() as c:
        assert H.status(c, scribe) is None
    # Clearing the wait removes only the projection; /started still creates the real status normally.
    events(api, machine, attempt["id"], [("status", {"waiting": ""})], first=2)
    assert get(api, "status?bot=scribe")["status"] is None
    assert not [s for s in get(api, "status")["bots"] if s and s["bot"] == scribe]
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "first-scribe-turn"}, machine["token"])
    row = get(api, "status?bot=scribe")["status"]
    assert row["state"] == "running" and not row.get("waiting")


def test_first_wait_status_preserves_holder_privacy_and_bot_read_access(api, queued_login):
    first_wait(api)
    scribe = scribe_id(api)
    as_member(api, "ben@acme.example")
    with api.app.state.store.transaction() as c:
        restrict(c, "ops", people=["ana"])
    row = get(api, "status?bot=scribe", "ben-test")["status"]
    assert row is not None
    assert row["waiting"] == "Waiting for this computer's ChatGPT login (held by another bot since 19:06Z; position 2)"
    assert row["task_id"] is None
    assert [s for s in get(api, "status", "ben-test")["bots"] if s and s["bot"] == scribe] == [row]
    with api.app.state.store.transaction() as c:
        restrict(c, "scribe", see={"people": ["ana", "ben"]}, read={"people": ["ana"]},
                 write={"people": ["ana"]})
    get(api, "status?bot=scribe", "ben-test", expected=403)
    assert not [s for s in get(api, "status", "ben-test")["bots"] if s and s["bot"] == scribe]


def test_a_first_turn_old_wait_does_not_create_a_status_when_codex_locking_is_off(api):
    first_wait(api)
    scribe = scribe_id(api)
    assert get(api, "status?bot=scribe")["status"] is None
    assert not [s for s in get(api, "status")["bots"] if s and s["bot"] == scribe]


def test_wait_projection_streams_start_clear_and_termination_outside_its_conversation(api, queued_login):
    store, auth = api.app.state.store, api.app.state.auth
    ana = Identity("human:ana", "owner", "ana@acme.example")
    with store.read() as c:
        before = E.latest(c)
    machine, task, attempt = first_wait(api)

    def streamed(after, who=ana):
        with store.read() as c:
            sent, cursor, *_ = E.read(c, auth, who, after, topics=("runs", "bots"),
                                     conversations=("unrelated-chat",), bots=("scribe",))
        assert not [d for _, topic, d in sent if topic == "runs"]
        return [d["status"] for _, topic, d in sent if topic == "bots"], cursor

    rows, cursor = streamed(before)
    assert len(rows) == 1 and rows[0]["waiting"] == get(api, "status?bot=scribe")["status"]["waiting"]
    # Hydration uses the viewer's holder/task permissions, even on a global bot notification.
    as_member(api, "ben@acme.example")
    with store.transaction() as c:
        restrict(c, "ops", people=["ana"])
    rows, _ = streamed(before, Identity("human:ben", "human", "ben@acme.example"))
    assert "another bot" in rows[0]["waiting"] and "private ledger" not in rows[0]["waiting"]
    events(api, machine, attempt["id"], [("status", {"waiting": ""})], first=2)
    rows, cursor = streamed(cursor)
    assert rows == [None]
    events(api, machine, attempt["id"], [("status", {"waiting": "waiting", "login": "ChatGPT login"})], first=3)
    rows, cursor = streamed(cursor)
    assert len(rows) == 1 and rows[0]["waiting"]
    with store.transaction() as c:
        c.execute("UPDATE attempts SET state='interrupted' WHERE id=?", (attempt["id"],))
    assert streamed(cursor)[0] == [None]
