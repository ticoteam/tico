"""Live Gmail reads use read-only tokens, bounded pages and the connector's normalizer."""

import base64
import copy
import io
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from backend import gmail_live as G
from backend import google_workspace as W
from backend.mail import gmail_query
from backend.store import Problem

BOX = "ana@acme.example"
NEXT = "Gmail+next/=?|opaque"


def raw_message(mid, epoch=100):
    return {"id": mid, "threadId": "t1", "internalDate": str(epoch * 1000),
            "labelIds": ["INBOX", "UNREAD"], "snippet": "A &amp; B",
            "payload": {"mimeType": "multipart/mixed", "headers": [
                {"name": "From", "value": "Sender <sender@acme.example>"},
                {"name": "To", "value": BOX}, {"name": "Subject", "value": "A contract"},
                {"name": "List-Id", "value": "contracts.acme.example"},
                {"name": "List-Unsubscribe", "value": "<https://acme.example/unsubscribe>"}],
                "parts": [{"mimeType": "text/plain", "body": {
                    "data": base64.urlsafe_b64encode(b"Live contract body.").decode()}},
                    {"filename": "contract.pdf", "mimeType": "application/pdf",
                     "body": {"attachmentId": "a1", "size": 123}}]}}


@pytest.fixture
def gmail_http(monkeypatch):
    state = {"calls": [], "status": 200, "metadata_status": 200,
             "messages": {"m1": raw_message("m1", 100), "m2": raw_message("m2", 200)}}

    def call(url, *, token):
        parsed = urlsplit(url)
        path, query = unquote(parsed.path), parse_qs(parsed.query)
        state["calls"].append((path, query, token))
        if state["status"] != 200:
            return state["status"], {"error": {"message": "PRIVATE GOOGLE TEXT"}}
        if path.endswith("/messages"):
            if query.get("pageToken") == [NEXT]:
                return 200, {"messages": [{"id": "m1"}]}
            return 200, {"messages": [{"id": "m2"}], "nextPageToken": NEXT}
        if "/threads/" in path:
            return 200, {"id": "t1", "messages": [copy.deepcopy(state["messages"][mid]) for mid in ("m2", "m1")]}
        raw = copy.deepcopy(state["messages"][path.rsplit("/", 1)[-1]])
        if query.get("format") == ["metadata"]:
            if state["metadata_status"] != 200:
                return state["metadata_status"], {}
            raw["payload"].pop("parts")
        return 200, raw

    monkeypatch.setattr(G, "gmail_call", call)
    return state


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


@pytest.mark.parametrize("status, expected", [(0, 502), (400, 422), (403, 403), (404, 404), (429, 503), (503, 502)])
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
        '(from:a OR from:b) label:"Needs review" after:100 before:200 has:attachment')
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
