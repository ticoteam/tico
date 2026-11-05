"""Workspace reads work with no synced messages; stored personal and legacy reads stay intact."""

import json

import jwt
import pytest

from backend import google_workspace, mailbox_access
from backend.tests.test_api import api, get, headers  # noqa: F401
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
