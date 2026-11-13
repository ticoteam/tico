"""Live Gmail reads use read-only tokens, bounded pages and the connector's normalizer."""

import io
import json

import pytest

from backend import gmail_live as G
from backend import google_workspace as W
from backend.mail import gmail_query
from backend.store import Problem
from backend.tests.helpers.gmail_live import BOX, NEXT, gmail_http


@pytest.fixture
def minted(monkeypatch):
    calls = []

    def mint(c, mailbox, service, row=None, scopes=None):
        calls.append((mailbox, service, scopes))
        return {"access_token": "readonly-token"}

    monkeypatch.setattr(W, "mint", mint)
    return calls


def test_list_search_and_opaque_paging_use_metadata_and_readonly_scopes(gmail_http, minted):
    first = G.list(None, BOX, q="from:sender newer_than:7d", limit=999)
    assert first["next_cursor"] == NEXT and first["messages"][0]["id"] == "m2"
    assert first["messages"][0]["body"] == "" and first["messages"][0]["snippet"] == "A & B"
    second = G.list(None, BOX, q=first["q"], page_token=first["next_cursor"], limit=1)
    assert second["next_cursor"] is None and second["messages"][0]["id"] == "m1"
    calls = gmail_http["calls"]
    assert calls[0][0] == "/gmail/v1/users/" + BOX + "/messages"
    assert calls[0][1]["q"] == ["from:sender newer_than:7d"] and calls[0][1]["maxResults"] == ["50"]
    assert calls[2][1]["pageToken"] == [NEXT] and calls[2][1]["maxResults"] == ["1"]
    assert calls[1][1]["format"] == ["metadata"] and "Subject" in calls[1][1]["metadataHeaders"]
    assert all(token == "readonly-token" for _, _, token in calls)
    assert minted == [(BOX, "gmail", W.READ_ONLY_SCOPES["gmail"])] * 2


def test_thread_and_message_fetch_full_and_normalize_bodies_and_attachments(gmail_http, minted):
    thread = G.thread(None, BOX, "t1")
    assert [item["id"] for item in thread] == ["m1", "m2"]
    message = G.message(None, BOX, "m1")
    assert message["body"] == "Live contract body." and message["from"] == "sender@acme.example"
    assert message["attachments"] == [{"name": "contract.pdf", "type": "application/pdf", "size": 123}]
    assert all(query["format"] == ["full"] for _, query, _ in gmail_http["calls"])
    assert minted == [(BOX, "gmail", W.READ_ONLY_SCOPES["gmail"])] * 2


@pytest.mark.parametrize("status, expected", [(400, 422), (403, 403), (404, 404), (429, 503), (503, 502)])
def test_gmail_errors_are_clear_refusals_never_empty_results(gmail_http, minted, status, expected):
    gmail_http["status"] = status
    with pytest.raises(Problem) as caught:
        G.list(None, BOX)
    assert caught.value.status == expected and "Gmail" in caught.value.detail
    assert "PRIVATE GOOGLE TEXT" not in caught.value.detail


def test_a_metadata_failure_refuses_the_page_instead_of_losing_a_message(gmail_http, minted):
    gmail_http["metadata_status"] = 500
    with pytest.raises(Problem, match="Gmail did not answer"):
        G.list(None, BOX)


def test_query_filters_and_a_callers_or_query_are_anded():
    assert gmail_query("from:a OR from:b", "Needs review", 100, 200, "true") == (
        '(from:a OR from:b) label:"Needs review" after:100 before:200 has:attachment'
    )
    assert gmail_query("", "", None, None, "false") == ""


def test_oversized_pages_refuse_without_skipping_results(gmail_http, minted, monkeypatch):
    monkeypatch.setattr(G, "PAGE_MAX_BYTES", 10)
    with pytest.raises(Problem, match="too large"):
        G.list(None, BOX)


def test_malformed_success_does_not_turn_into_an_empty_inbox(monkeypatch, minted):
    monkeypatch.setattr(G, "gmail_call", lambda *args, **kwargs: (200, {"messages": None}))
    with pytest.raises(Problem, match="incomplete page"):
        G.list(None, BOX)


def test_http_timeout_and_invalid_json_are_refusals_with_short_timeouts(monkeypatch, minted):
    def timeout(request, *, timeout):
        assert timeout == 10
        assert request.get_header("Authorization") == "Bearer readonly-token"
        raise TimeoutError()

    monkeypatch.setattr(G.urllib.request, "urlopen", timeout)
    with pytest.raises(Problem, match="Gmail did not answer"):
        G.message(None, BOX, "m1")

    class Response(io.BytesIO):
        status = 200

    monkeypatch.setattr(G.urllib.request, "urlopen", lambda *args, **kwargs: Response(b"<html>unavailable</html>"))
    with pytest.raises(Problem, match="Gmail did not answer"):
        G.list(None, BOX)


def test_an_empty_gmail_page_is_a_success(monkeypatch, minted):
    monkeypatch.setattr(G, "gmail_call", lambda *args, **kwargs: (200, {}))
    assert G.list(None, BOX) == {"messages": [], "next_cursor": None, "q": ""}


def test_batch_modify_chunks_and_uses_only_the_write_scope(gmail_http, minted):
    ids = ["m" + str(n) for n in range(2001)]
    G.organize(None, BOX, ids, add=("STARRED",), remove=("UNREAD",))
    writes = gmail_http["writes"]
    assert [len(body["ids"]) for _, body, _ in writes] == [1000, 1000, 1]
    assert [mid for _, body, _ in writes for mid in body["ids"]] == ids
    assert all(
        path.endswith("/messages/batchModify")
        and body["addLabelIds"] == ["STARRED"]
        and body["removeLabelIds"] == ["UNREAD"]
        for path, body, _ in writes
    )
    assert minted == [(BOX, "gmail", W.WORKSPACE_SCOPES["gmail"])]


def test_only_safe_system_labels_or_existing_user_labels_can_change(gmail_http, minted):
    for label in ("TRASH", "SPAM", "SENT", "DRAFT", "CATEGORY_PERSONAL", "unknown-label"):
        with pytest.raises(Problem) as caught:
            G.organize(None, BOX, ["m1"], remove=(label,))
        assert caught.value.status == 422
    assert gmail_http["writes"] == []
    G.organize(None, BOX, ["m1"], add=("Label_1",))
    assert gmail_http["writes"][0][1]["addLabelIds"] == ["Label_1"]


def test_label_resolution_creates_only_on_first_label_use(gmail_http, minted):
    assert G.resolve_label(None, BOX, "Receipts", create=True) == "Label_1"
    with pytest.raises(Problem) as caught:
        G.resolve_label(None, BOX, "New label")
    assert caught.value.status == 404 and not gmail_http["writes"]
    created = G.resolve_label(None, BOX, "New label", create=True)
    assert G.resolve_label(None, BOX, "New label", create=True) == created
    assert [body for _, body, _ in gmail_http["writes"]] == [{"name": "New label"}]
    for name in ("trash", "SPAM", "SENT", "DRAFT", "CATEGORY_PERSONAL"):
        with pytest.raises(Problem) as caught:
            G.resolve_label(None, BOX, name, create=True)
        assert caught.value.status == 422
    assert len(gmail_http["writes"]) == 1


def test_ids_resolve_all_pages_before_a_bounded_change_without_reading_content(gmail_http, minted):
    assert G.message_ids(None, BOX, "in:inbox", limit=1) == ["m2"]
    assert G.message_ids(None, BOX, "in:inbox", limit=100) == ["m2", "m1"]
    assert len(gmail_http["calls"]) == 3
    assert all(
        path.endswith("/messages") and query["fields"] == ["messages(id),nextPageToken"]
        for path, query, _ in gmail_http["calls"]
    )
    assert gmail_http["calls"][-1][1]["maxResults"] == ["99"]


@pytest.mark.parametrize("status, expected", [(403, 403)])
def test_modify_failures_are_refusals(gmail_http, minted, status, expected):
    gmail_http["write_status"] = status
    with pytest.raises(Problem) as caught:
        G.organize(None, BOX, ["m1"], remove=("INBOX",))
    assert caught.value.status == expected and "PRIVATE GOOGLE TEXT" not in caught.value.detail


def test_batch_modify_http_sends_json_and_accepts_an_empty_success(monkeypatch, minted):
    class Response(io.BytesIO):
        status = 204

    def answer(request, *, timeout):
        assert request.get_method() == "POST" and request.get_header("Content-type") == "application/json"
        assert json.loads(request.data) == {"ids": ["m1"], "addLabelIds": [], "removeLabelIds": ["INBOX"]}
        return Response(b"")

    monkeypatch.setattr(G.urllib.request, "urlopen", answer)
    G.organize(None, BOX, ["m1"], remove=("INBOX",))


def test_repeated_page_tokens_refuse_before_any_modify(gmail_http, minted):
    gmail_http["pages"] = {
        "": {"messages": [{"id": "m1"}], "nextPageToken": "loop"},
        "loop": {"messages": [], "nextPageToken": "loop"},
    }
    with pytest.raises(Problem, match="incomplete page"):
        G.message_ids(None, BOX, "in:inbox")
    assert not gmail_http["writes"]
