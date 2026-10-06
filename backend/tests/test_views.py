

from backend.store import H
from backend.tests.test_api import api, as_member, headers, get, post, restrict, setup_attempt, runner, ready, assign, claim


def test_revocation_invalidates_running_bot_credential(api):
    r, _, attempt = setup_attempt(api)
    post(api, f"runners/{r['runner_id']}/revoke", {})
    get(api, "me", r["token"], expected=401)
    get(api, "me", attempt["token"], expected=401)


def test_bot_execution_cannot_read_another_humans_personal_chat(api):
    secret = post(api, "chat/ops", {"text": "Ana private context"})
    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    # Complete the older job so the next claim belongs to Ben's conversation.
    first = claim(api, r)
    post(api, f"attempts/{first['id']}/started", {"thread_id": "ana-context"}, r["token"])
    post(api, f"attempts/{first['id']}/complete", {"outcome": "completed", "last_seq": 0}, r["token"])
    own = post(api, "chat/ops", {"text": "Ben work"}, "ben-test")
    attempt = claim(api, r)
    assert attempt["message"]["id"] == own["id"]
    get(api, f"conversations/{secret['conversation_id']}/messages", attempt["token"], expected=403)
    assert secret["id"] not in {row["id"] for row in get(api, "inbox", attempt["token"])["messages"]}
    post(api, "messages", {"to": "human:ana", "text": "Use an old private chat"}, attempt["token"], expected=403)
    question = post(api, "messages", {"to": "finance", "text": "What is the budget?", "kind": "ask"}, attempt["token"])
    assert get(api, f"conversations/{question['conversation_id']}/messages", attempt["token"])


def test_tico_fleet_snapshot_is_bound_to_the_initiating_human(api):
    as_member(api, "ben@acme.example")
    with api.app.state.store.transaction() as c:      # finance takes requests from Ana alone
        restrict(c, "finance", people=["ana"])
    post(api, "tasks", {"title": "Review product direction", "body": "Choose the next slice", "owner": "cpo"},
         "ben-test")
    post(api, "tasks", {"title": "What is blocked?", "body": "List it", "owner": "coo"}, "ben-test")
    machine = runner(api)
    assign(api, machine, "coo")
    ready(api, machine, ["coo"])
    attempt = claim(api, machine)
    fleet = get(api, "tico/fleet", attempt["token"])
    assert fleet["actor"] == "human:ben"
    # The assistant is in the fleet (it still takes work) though nobody chats with it.
    assert {bot["slug"] for bot in fleet["bots"]} == {"coo", "ops", "cpo", "product-design", "doc-updater"}
    assert any(task["title"] == "Review product direction" for task in fleet["tasks"])
    assert "finance" not in {bot["slug"] for bot in fleet["bots"]}


def test_tico_task_execution_cannot_assume_a_private_fleet_identity(api):
    with api.app.state.store.transaction() as c:
        H.task_create(c, "human:ana", "Legacy isolated task", "Review the queue", "bot:coo")
    machine = runner(api)
    assign(api, machine, "coo")
    ready(api, machine, ["coo"])
    attempt = claim(api, machine)
    assert attempt["conversation"]["scope"] == "task"
    get(api, "tico/fleet", attempt["token"], expected=403)


def test_message_pagination_preserves_all_history_with_timestamp_ties(api):
    msg = post(api, "chat/ops", {"text": "First message"})
    with api.app.state.store.transaction() as c:
        for n in range(240):
            H.say(c, "human:ana", "bot:ops", f"Message {n}", conversation_id=msg["conversation_id"])
        c.execute("UPDATE messages SET created='2026-09-10T10:00:00Z'")
    path = f"/api/v2/conversations/{msg['conversation_id']}/messages"
    newest = api.get(path, headers=headers()).json()
    assert newest["messages"][-1]["body"] == "Message 239"
    assert len(newest["messages"]) == 200 and newest["has_more"]
    older = api.get(path, params={"before": newest["next_before"]}, headers=headers()).json()
    assert len(older["messages"]) == 41 and not older["has_more"]
    assert older["messages"][0]["id"] == msg["id"]
    assert len({m["id"] for m in older["messages"] + newest["messages"]}) == 241

