"""Workspace reads work with no synced messages; stored personal and legacy reads stay intact."""

import json

import jwt
import pytest

from backend import google_workspace, mailbox_access
from backend.tests.test_api import api, get, headers, setup_attempt  # noqa: F401
from backend.tests.test_assistant import assistant_turn
from backend.tests.test_gmail_live import BOX, NEXT, gmail_http  # noqa: F401
from backend.tests.test_google_workspace import personal
from backend.tests.test_google_workspace_authz import workspace_key  # noqa: F401
from backend.tests.test_mail_api import put_mail

SHARED = "legal@acme.example"


@pytest.fixture(autouse=True)
def no_cached_tokens():
    google_workspace.forget_tokens()
    yield
    google_workspace.forget_tokens()


@pytest.fixture
def live(api, workspace_key, gmail_http):
    with api.app.state.store.transaction() as c:
        mailbox_access.ensure_mailbox(c, SHARED, kind="shared", created_by="human:ana")
    return gmail_http


@pytest.fixture
def assistant_token(api):
    _, attempt = assistant_turn(api, "ana-test", "Read my mail and the shared mailboxes I can access.")
    return attempt["token"]


def read(api, path, token="ana-test", **params):
    answer = api.get("/api/v2/mail/" + path, params=params, headers=headers(token))
    assert answer.status_code == 200, answer.text
    return answer.json()


def test_owner_and_assistant_read_personal_and_shared_workspace_mail_without_a_copy(
        api, live, workspace_key, assistant_token):
    for token, mailbox in (("ana-test", BOX), (assistant_token, BOX), (assistant_token, SHARED)):
        page = read(api, "messages", token, mailbox=mailbox, q="from:sender OR is:unread",
                    label="INBOX", since="100", until="200", has_attachment="true", limit=999)
        assert page["messages"][0]["mailbox"] == mailbox and "body" not in page["messages"][0]
        assert page["next_cursor"] == NEXT
        nxt = read(api, "messages", token, mailbox=mailbox, cursor=NEXT)
        assert nxt["messages"][0]["msg_id"] == "m1" and nxt["next_cursor"] is None
    query = live["calls"][0][1]
    assert query["q"] == ['(from:sender OR is:unread) label:"INBOX" after:100 before:200 has:attachment']
    assert query["maxResults"] == ["50"]
    assert workspace_key and all(jwt.decode(call["assertion"], options={"verify_signature": False})["scope"] ==
                                 google_workspace.READ_ONLY_SCOPES["gmail"][0] for call in workspace_key)
    with api.app.state.store.read() as c:
        assert c.execute("SELECT COUNT(*) FROM mail_messages").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM mail_fts").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM mail_mailboxes").fetchone()[0] == 0


@pytest.mark.parametrize("mailbox", [BOX, SHARED])
def test_opening_live_mail_keeps_the_public_shape_and_audits_every_open(api, live, assistant_token, mailbox):
    message = read(api, "messages/m1", assistant_token, mailbox=mailbox)
    thread = read(api, "threads/t1", assistant_token, mailbox=mailbox)
    read(api, "messages/m1", assistant_token, mailbox=mailbox)
    assert message["body"] == "Live contract body." and message["attachment_count"] == 1
    assert message["has_attachment"] and message["has_unsubscribe"] and message["is_internal"]
    assert message["rule_hits"] == [] and message["list_id"] == "contracts.acme.example"
    assert [item["msg_id"] for item in thread["messages"]] == ["m1", "m2"]
    with api.app.state.store.read() as c:
        rows = c.execute("SELECT actor,detail FROM events WHERE action='mail.read' ORDER BY rowid").fetchall()
        assert len(rows) == 3 and all(row["actor"] == "human:ana" for row in rows)
        details = [json.loads(row["detail"]) for row in rows]
        assert [item["kind"] for item in details] == ["message", "thread", "message"]
        assert all(item["mailbox"] == mailbox and item["via"] == "assistant" for item in details)
        assert "Live contract body" not in json.dumps(details)
        assert c.execute("SELECT COUNT(*) FROM mail_messages").fetchone()[0] == 0


def test_member_is_refused_before_gmail_and_a_grant_is_checked_each_time(api, live):
    _, attempt = assistant_turn(api, "cara-test", "Read the shared mailbox if I have access.")
    tokens = ("cara-test", attempt["token"])
    for token in tokens:
        for path in ("messages", "messages/m1", "threads/t1"):
            get(api, "mail/" + path + "?mailbox=" + SHARED, token, expected=403)
    assert live["calls"] == []
    with api.app.state.store.transaction() as c:
        row = mailbox_access.ensure_mailbox(c, SHARED, kind="shared", created_by="human:ana")
        mailbox_access.grant(c, "human:ana", row["id"], "person", "cara", "read")
    for token in tokens:
        read(api, "messages/m1", token, mailbox=SHARED)
    before = len(live["calls"])
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM mailbox_accesses WHERE mailbox_id=? AND grantee_id='cara'", (row["id"],))
    for token in tokens:
        get(api, "mail/messages/m1?mailbox=" + SHARED, token, expected=403)
    assert len(live["calls"]) == before


def test_workspace_lists_require_a_mailbox_and_gmail_failures_are_not_empty(api, live):
    get(api, "mail/messages", expected=422)
    live["status"] = 503
    for path in ("messages", "messages/m1", "threads/t1"):
        error = get(api, "mail/" + path + "?mailbox=" + SHARED, expected=502)
        assert error["error"]["code"] == "gmail_unavailable"
    with api.app.state.store.read() as c:
        assert c.execute("SELECT COUNT(*) FROM events WHERE action='mail.read'").fetchone()[0] == 0


def test_live_personal_grants_still_use_the_stored_path_even_with_workspace_connected(api, live):
    personal(api, "ana", BOX)
    put_mail(api, BOX, msg_id="stored", thread_id="stored-thread", subject="Stored invoice", body="Stored body")
    page = read(api, "messages", mailbox=BOX, q="invoice")
    assert page["messages"][0]["msg_id"] == "stored" and page["q"] == '"invoice"'
    assert read(api, "messages/stored", mailbox=BOX)["body"] == "Stored body"
    assert read(api, "threads/stored-thread", mailbox=BOX)["messages"][0]["body"] == "Stored body"
    assert live["calls"] == []


def test_legacy_reads_keep_fts_and_the_stored_cursor(api, gmail_http):
    put_mail(api, BOX, msg_id="old1", thread_id="old-thread", epoch=100, subject="Old invoice")
    put_mail(api, BOX, msg_id="old2", thread_id="old-thread", epoch=200, subject="Old invoice")
    first = read(api, "messages", q="invoice", limit=1)
    assert first["next_cursor"] == "200|" + BOX + "|old2"
    second = read(api, "messages", q="invoice", limit=1, cursor=first["next_cursor"])
    assert second["messages"][0]["msg_id"] == "old1" and second["next_cursor"] is None
    assert read(api, "messages/old1", mailbox=BOX)["subject"] == "Old invoice"
    assert len(read(api, "threads/old-thread", mailbox=BOX)["messages"]) == 2
    assert gmail_http["calls"] == []


def organize(api, token="ana-test", expected=200, **body):
    answer = api.post("/api/v2/mail/messages/organize", json=body, headers=headers(token))
    assert answer.status_code == expected, answer.text
    return answer.json()


@pytest.mark.parametrize("token_kind", ["person", "assistant"])
def test_organizing_explicit_ids_is_live_and_audits_only_action_facts(api, live, workspace_key, assistant_token, token_kind):
    token = "ana-test" if token_kind == "person" else assistant_token
    result = organize(api, token, mailbox=SHARED, action="archive", ids=["m1", "m2", "m1"])
    assert result == {"mailbox": SHARED, "action": "archive", "count": 2, "ids": ["m1", "m2"],
                      "undo": {"action": "unarchive", "ids": ["m1", "m2"]}}
    assert len(live["calls"]) == 1 and live["writes"][0][1] == {
        "ids": ["m1", "m2"], "addLabelIds": [], "removeLabelIds": ["INBOX"]}
    assert jwt.decode(workspace_key[-1]["assertion"], options={"verify_signature": False})["scope"] == (
        google_workspace.WORKSPACE_SCOPES["gmail"][0])
    with api.app.state.store.read() as c:
        rows = c.execute("SELECT actor,detail FROM events WHERE action='mail.organize'").fetchall()
        assert len(rows) == 1 and rows[0]["actor"] == "human:ana"
        detail = json.loads(rows[0]["detail"])
        assert {k: detail[k] for k in ("mailbox", "action", "label", "count", "ids")} == {
            "mailbox": SHARED, "action": "archive", "label": None, "count": 2, "ids": ["m1", "m2"]}
        if token_kind == "assistant":
            assert detail["via"] == "assistant"
        assert not {"subject", "body", "snippet", "query"} & detail.keys()
        assert "Live contract body" not in rows[0]["detail"]
        assert c.execute("SELECT COUNT(*) FROM mail_messages").fetchone()[0] == 0


def test_query_resolves_ids_with_default_and_explicit_caps_before_modifying(api, live):
    result = organize(api, mailbox=BOX, action="archive", query="in:inbox category:promotions", limit=1)
    assert result["ids"] == ["m2"] and live["calls"][0][1]["maxResults"] == ["1"]
    live["calls"].clear()
    result = organize(api, mailbox=BOX, action="mark_read", query="is:unread")
    assert result["ids"] == ["m2", "m1"]
    assert live["calls"][0][1]["maxResults"] == ["100"]
    assert live["calls"][1][1]["pageToken"] == [NEXT]
    assert [path.rsplit("/", 1)[-1] for path, _, _ in live["calls"]] == ["messages", "messages", "batchModify"]
    live["pages"] = {"": {"messages": [{"id": "m" + str(i)} for i in range(500)], "nextPageToken": "more"}}
    assert organize(api, mailbox=BOX, action="archive", query="in:inbox", limit=500)["count"] == 500
    assert len(live["writes"][-1][1]["ids"]) == 500


@pytest.mark.parametrize("selection", [{"ids": ["m1"], "query": "in:inbox"}, {}, {"ids": []},
                                      {"ids": ["m"] * 501}, {"query": " "}, {"query": "in:inbox", "limit": 501}])
def test_invalid_selections_never_reach_gmail(api, live, selection):
    organize(api, expected=422, mailbox=BOX, action="archive", **selection)
    assert not live["calls"]


def test_read_only_level_and_company_switch_are_rechecked_before_each_write(api, live, assistant_token):
    with api.app.state.store.transaction() as c:
        row = mailbox_access._row(c, SHARED)
        mailbox_access.grant(c, "human:ana", row["id"], "person", "cara", "read")
    result = organize(api, "cara-test", expected=403, mailbox=SHARED, action="archive", ids=["m1"])
    assert "Who can use it" in result["error"]["detail"] and not live["calls"]
    with api.app.state.store.transaction() as c:
        mailbox_access.grant(c, "human:ana", row["id"], "person", "cara", "read_write")
    organize(api, "cara-test", mailbox=SHARED, action="archive", ids=["m1"])
    changed = api.put("/api/v2/google/permissions", json={"organize_mail": False, "send_mail": True,
                                                       "change_calendar": True}, headers=headers())
    assert changed.status_code == 200, changed.text
    before = len(live["calls"])
    for token in ("cara-test", assistant_token):
        result = organize(api, token, expected=403, mailbox=SHARED, action="archive", ids=["m1"])
        assert "What Tico may do" in result["error"]["detail"]
    assert len(live["calls"]) == before


def test_a_bot_organizes_only_with_its_own_current_grant(api, live):
    _, _, attempt = setup_attempt(api)
    token = attempt["token"]
    organize(api, token, expected=403, mailbox=BOX, action="archive", ids=["m1"])
    with api.app.state.store.transaction() as c:
        row = mailbox_access.ensure_mailbox(c, BOX, person_id="ana", created_by="human:ana")
        bot = c.execute("SELECT bot_id FROM attempts WHERE id=?", (attempt["id"],)).fetchone()[0]
        mailbox_access.grant(c, "human:ana", row["id"], "bot", bot, "read_write")
    organize(api, token, mailbox=BOX, action="archive", ids=["m1"])
    with api.app.state.store.transaction() as c:
        mailbox_access.grant(c, "human:ana", row["id"], "bot", bot, "read")
    organize(api, token, expected=403, mailbox=BOX, action="archive", ids=["m1"])
    assert len(live["writes"]) == 1


def test_a_personal_grant_cannot_be_organized_live(api, live):
    personal(api, "ana", BOX)
    result = organize(api, expected=422, mailbox=BOX, action="archive", ids=["m1"])
    assert result["error"]["detail"] == "Live organizing needs Whole Workspace" and not live["calls"]


def test_label_unlabel_and_empty_queries_do_not_create_unwanted_labels(api, live):
    result = organize(api, mailbox=BOX, action="label", label="Review later", ids=["m1"])
    assert result["label"] == "Review later" and result["undo"]["label"] == "Review later"
    assert live["writes"][0][1] == {"name": "Review later"}
    result = organize(api, mailbox=BOX, action="unlabel", label="Review later", ids=["m1"])
    assert result["undo"]["action"] == "label"
    assert len([path for path, _, _ in live["writes"] if path.endswith("/labels")]) == 1
    organize(api, expected=404, mailbox=BOX, action="unlabel", label="Missing", ids=["m1"])
    organize(api, expected=422, mailbox=BOX, action="label", label="TRASH", ids=["m1"])
    live["pages"] = {"": {}}
    before = len(live["writes"])
    assert organize(api, mailbox=BOX, action="label", label="Unused", query="from:nobody")["count"] == 0
    assert len(live["writes"]) == before


def test_a_gmail_write_refusal_is_never_reported_or_audited_as_success(api, live):
    live["write_status"] = 403
    result = organize(api, expected=403, mailbox=BOX, action="archive", ids=["m1"])
    assert result["error"]["code"] == "gmail_refused"
    with api.app.state.store.read() as c:
        assert c.execute("SELECT COUNT(*) FROM events WHERE action='mail.organize'").fetchone()[0] == 0


@pytest.mark.parametrize("mode", ["rehearsal", "demo"])
def test_demo_and_rehearsal_cannot_organize_live_mail(api, live, mode):
    setattr(api.app.state.store.settings, mode, True)
    try:
        organize(api, expected=403, mailbox=BOX, action="archive", ids=["m1"])
        assert not live["calls"]
    finally:
        setattr(api.app.state.store.settings, mode, False)


def test_query_retries_keep_the_original_ids_and_recheck_access(api, live):
    auth_headers = headers(key="organize-retry")
    body = {"mailbox": BOX, "action": "archive", "query": "in:inbox", "limit": 1}
    first = api.post("/api/v2/mail/messages/organize", json=body, headers=auth_headers)
    assert first.status_code == 200, first.text
    live["pages"] = {"": {"messages": [{"id": "next-message"}]}}
    second = api.post("/api/v2/mail/messages/organize", json=body, headers=auth_headers)
    assert second.status_code == 200 and second.json()["ids"] == first.json()["ids"] == ["m2"]
    assert len([path for path, _, _ in live["calls"] if path.endswith("/messages")]) == 1
    changed = api.put("/api/v2/google/permissions", json={"organize_mail": False, "send_mail": True,
                                                       "change_calendar": True}, headers=headers())
    assert changed.status_code == 200, changed.text
    refused = api.post("/api/v2/mail/messages/organize", json=body, headers=auth_headers)
    assert refused.status_code == 403 and len(live["writes"]) == 1
    with api.app.state.store.read() as c:
        assert c.execute("SELECT COUNT(*) FROM events WHERE action='mail.organize'").fetchone()[0] == 1


def test_query_retry_after_a_gmail_failure_keeps_the_attempted_selection(api, live):
    auth_headers = headers(key="organize-failed-retry")
    body = {"mailbox": BOX, "action": "archive", "query": "in:inbox", "limit": 1}
    live["write_status"] = 503
    first = api.post("/api/v2/mail/messages/organize", json=body, headers=auth_headers)
    assert first.status_code == 502, first.text
    live["write_status"] = 200
    live["pages"] = {"": {"messages": [{"id": "next-message"}]}}
    second = api.post("/api/v2/mail/messages/organize", json=body, headers=auth_headers)
    assert second.status_code == 200 and second.json()["ids"] == ["m2"]
    assert [body["ids"] for _, body, _ in live["writes"]] == [["m2"], ["m2"]]
