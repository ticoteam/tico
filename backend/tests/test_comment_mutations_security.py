"""Withdrawn comment words and author rights stay enforced through retries and bot handoffs."""

import pytest

from backend.store import H, message_page
from backend.tests.test_api import as_member, headers, restrict
from backend.tests.test_tasks_board import api, get, post  # noqa: F401


def test_old_and_deleted_words_leave_retries_sql_events_and_bot_context(api):
    task = post(api, "tasks", {"owner": "ops", "title": "Review the report", "body": "Please review."})
    path = f"tasks/{task['id']}/comments"
    original_headers = headers()
    original = api.post("/api/v2/" + path, json={"text": "Withdrawn original words"}, headers=original_headers).json()
    mid = original["comment"]["id"]
    edited_headers = headers()
    edited = api.post(f"/api/v2/{path}/{mid}", json={"text": "Replacement words"}, headers=edited_headers)
    assert edited.status_code == 200, edited.text
    retry = api.post("/api/v2/" + path, json={"text": "Withdrawn original words"}, headers=original_headers)
    assert retry.json()["comment"]["body"] == "Replacement words"
    deleted = post(api, f"{path}/{mid}/delete", {})
    assert deleted["comment"]["body"] == "" and deleted["comments"] == []
    for route, body, request_headers in ((path, {"text": "Withdrawn original words"}, original_headers),
                                         (f"{path}/{mid}", {"text": "Replacement words"}, edited_headers)):
        retry = api.post("/api/v2/" + route, json=body, headers=request_headers)
        assert retry.status_code == 200, retry.text
        assert "Withdrawn original words" not in retry.text and "Replacement words" not in retry.text
    for token in ("ana-test", "ben-test"):
        query = {"sql": "SELECT body,refs_json FROM messages WHERE id=?", "params": [mid]}
        result = api.post("/api/v2/sql", json=query, headers=headers(token))
        assert result.status_code == 200, result.text
        assert result.json()["rows"] == []
    with api.app.state.store.read() as c:
        assert H.message(c, mid) is None
        assert mid not in {m["id"] for m in H.messages(c, task["conversation_id"])}
        assert mid not in {m["id"] for m in H.inbox(c, "bot:ops")["messages"]}
        assert mid not in {m["id"] for m in H.undelivered(c)}
        assert mid not in {m["id"] for m in message_page(c, task["conversation_id"])["messages"]}
        for table, field in (("messages", "body"), ("events", "detail_json"), ("idempotency", "response_json")):
            saved = " ".join(str(r[0]) for r in c.execute(f"SELECT {field} FROM {table}"))
            assert "Withdrawn original words" not in saved and "Replacement words" not in saved


def test_current_task_read_is_required_even_when_replaying_an_authorized_edit(api):
    as_member(api, "ben@acme.example")
    task = post(api, "tasks", {"owner": "ops", "title": "Review the report", "body": "Please review."})
    said = post(api, f"tasks/{task['id']}/comments", {"text": "My original words"}, token="ben-test")["comment"]
    path = f"tasks/{task['id']}/comments/{said['id']}"
    same_headers = headers("ben-test")
    edited = api.post("/api/v2/" + path, json={"text": "My replacement words"}, headers=same_headers)
    assert edited.status_code == 200, edited.text
    with api.app.state.store.transaction() as c:
        restrict(c, "ops", people=["ana"])
    for route, body, request_headers in ((path, {"text": "My replacement words"}, same_headers),
                                         (path, {"text": "Another edit"}, headers("ben-test")),
                                         (path + "/delete", {}, headers("ben-test"))):
        refused = api.post("/api/v2/" + route, json=body, headers=request_headers)
        assert refused.status_code in (403, 404), refused.text
        assert "My replacement words" not in refused.text
    with api.app.state.store.read() as c:
        assert H.message(c, said["id"])["body"] == "My replacement words"


@pytest.mark.parametrize("handoff", ["slack"])
def test_already_handed_comments_change_current_record_without_recalling_copies(api, handoff):
    task = post(api, "tasks", {"owner": "ops", "title": "Review the report", "body": "Please review."})
    said = post(api, f"tasks/{task['id']}/comments", {"text": "Retained elsewhere"})["comment"]
    with api.app.state.store.transaction() as c:
        if handoff == "delivered":
            c.execute("UPDATE messages SET delivered_at=? WHERE id=?", (H.now(), said["id"]))
        elif handoff == "job":
            c.execute("UPDATE jobs SET state='done' WHERE message_id=?", (said["id"],))
        elif handoff == "slack":
            c.execute("INSERT INTO slack_posts(message_id,channel,thread_ts,bot,text,state,created,updated) "
                      "VALUES(?,'Dexample','','ops',?,'ready',?,?)", (said["id"], said["body"], H.now(), H.now()))
        elif handoff == "context":
            H.turn_start(c, "bot:ops", "ops")
        else:
            c.execute("UPDATE bot_config SET config_json=json_set(config_json,'$.harness','hermes') WHERE bot='ops'")
    edited = post(api, f"tasks/{task['id']}/comments/{said['id']}", {"text": "Replacement words"})
    assert edited['comment']['body'] == 'Replacement words'
    detail = get(api, 'tasks/' + task['id'])
    assert 'Retained elsewhere' not in str(detail)
    post(api, f"tasks/{task['id']}/comments/{said['id']}/delete", {})
    with api.app.state.store.read() as c:
        assert H.message(c, said['id']) is None
        if handoff == 'slack':
            row = c.execute('SELECT text,state FROM slack_posts WHERE message_id=?', (said['id'],)).fetchone()
            assert row['text'] == '' and row['state'] == 'cancelled'


def test_bot_authorship_is_checked_in_the_domain(api):
    task = post(api, "tasks", {"owner": "ben", "title": "Review the report", "body": "Please review."})
    with api.app.state.store.transaction() as c:
        said = H.task_comment(c, "bot:ops", task["id"], "Draft ready", wake=False)
        with pytest.raises(H.Refused):
            H.task_comment_edit(c, "human:ana", task["id"], said["id"], "Replace")
        assert H.task_comment_edit(c, "bot:ops", task["id"], said["id"], "Draft revised")["edited_at"]
        assert H.task_comment_delete(c, "bot:ops", task["id"], said["id"])["body"] == ""
