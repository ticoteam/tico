"""Helpers that other tests share, moved out of test_gmail_live.py so they import no test file."""

import base64
import copy
from urllib.parse import parse_qs, unquote, urlsplit
import pytest
from backend import gmail_live as G


BOX = "ana@acme.example"


NEXT = "Gmail+next/=?|opaque"


def raw_message(mid, epoch=100):
    return {
        "id": mid,
        "threadId": "t1",
        "internalDate": str(epoch * 1000),
        "labelIds": ["INBOX", "UNREAD"],
        "snippet": "A &amp; B",
        "payload": {
            "mimeType": "multipart/mixed",
            "headers": [
                {"name": "From", "value": "Sender <sender@acme.example>"},
                {"name": "To", "value": BOX},
                {"name": "Subject", "value": "A contract"},
                {"name": "List-Id", "value": "contracts.acme.example"},
                {"name": "List-Unsubscribe", "value": "<https://acme.example/unsubscribe>"},
            ],
            "parts": [
                {"mimeType": "text/plain", "body": {"data": base64.urlsafe_b64encode(b"Live contract body.").decode()}},
                {
                    "filename": "contract.pdf",
                    "mimeType": "application/pdf",
                    "body": {"attachmentId": "a1", "size": 123},
                },
            ],
        },
    }


@pytest.fixture
def gmail_http(monkeypatch):
    state = {
        "calls": [],
        "status": 200,
        "metadata_status": 200,
        "writes": [],
        "write_status": 200,
        "labels": [
            {"id": name, "name": name, "type": "system"}
            for name in (
                "INBOX",
                "UNREAD",
                "STARRED",
                "IMPORTANT",
                "TRASH",
                "SPAM",
                "SENT",
                "DRAFT",
                "CATEGORY_PERSONAL",
            )
        ]
        + [{"id": "Label_1", "name": "Receipts", "type": "user"}],
        "messages": {"m1": raw_message("m1", 100), "m2": raw_message("m2", 200)},
    }

    def call(url, *, token, data=None):
        parsed = urlsplit(url)
        path, query = unquote(parsed.path), parse_qs(parsed.query)
        state["calls"].append((path, query, token))
        if state["status"] != 200:
            return state["status"], {"error": {"message": "PRIVATE GOOGLE TEXT"}}
        if data is not None:
            state["writes"].append((path, copy.deepcopy(data), token))
            if state["write_status"] != 200:
                return state["write_status"], {"error": {"message": "PRIVATE GOOGLE TEXT"}}
            if path.endswith("/labels"):
                label = {"id": "Label_" + str(len(state["labels"])), "name": data["name"], "type": "user"}
                state["labels"].append(label)
                return 200, copy.deepcopy(label)
            assert path.endswith("/messages/batchModify")
            return 200, {}
        if path.endswith("/labels"):
            return 200, {"labels": copy.deepcopy(state["labels"])}
        if path.endswith("/messages"):
            if "pages" in state:
                return 200, copy.deepcopy(state["pages"][query.get("pageToken", [""])[0]])
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
