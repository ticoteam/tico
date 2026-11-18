"""A call's notes from its one model call (backend/call_items.py): the summary and the action items, each item's quote
found in the transcript and its owner one of the speakers. The Gateway is replaced; every name is synthetic."""

import json

import pytest

from backend import call_items
from clients import gateway

ROWS = [
    {"t": "0:42", "speaker": "human:ana", "text": "Numbers first, then the launch post."},
    {"t": "21:30", "speaker": "human:ben", "text": "Let's do another one on Thursday."},
    {"t": "21:38", "speaker": "bot:content-social", "text": "I'll draft it today."},
]


def answer(monkeypatch, content):
    sent = []

    def relay(method, url, body=None, headers=None, timeout=None):
        sent.append(json.loads(body))
        return 200, json.dumps({"choices": [{"message": {"content": content}}]}).encode()

    monkeypatch.setattr(gateway, "relay", relay)
    return sent


def test_one_call_answers_the_summary_and_the_items_with_owners_who_spoke(monkeypatch):
    sent = answer(
        monkeypatch,
        "Here you go:\n```json\n"
        + json.dumps(
            {
                "summary": ["Signups  are up 14%.", "", 7, "Post again on Thursday.", "a", "b", "c", "d"],
                "items": [
                    {"title": "Draft Thursday's post", "quote": "i'll draft it today", "owner": "bot:content-social"},
                    {"title": "Plan the launch", "quote": "the launch post", "owner": "human:dana"},
                    {"title": "Invented", "quote": "Nobody said this", "owner": "human:ana"},
                ],
            }
        )
        + "\n```",
    )
    notes = call_items.draft("https://gateway.acme.example/v1", "k", "m", ROWS)
    assert len(sent) == 1 and "summary" in sent[0]["messages"][0]["content"]
    assert notes["summary"] == ["Signups are up 14%.", "Post again on Thursday.", "a", "b", "c"]
    assert notes["items"] == [
        {"title": "Draft Thursday's post", "quote": "I'll draft it today", "owner": "bot:content-social"},
        {"title": "Plan the launch", "quote": "the launch post", "owner": ""},  # nobody named Dana spoke
    ]


def test_a_reply_without_a_summary_still_gives_the_items(monkeypatch):
    answer(monkeypatch, json.dumps({"items": [{"title": "Plan the launch", "quote": "launch post"}]}))
    notes = call_items.draft("https://gateway.acme.example/v1", "k", "m", ROWS)
    assert notes == {"summary": [], "items": [{"title": "Plan the launch", "quote": "launch post", "owner": ""}]}


def test_a_reply_that_is_not_the_items_json_is_unavailable(monkeypatch):
    answer(monkeypatch, json.dumps({"summary": ["Only a summary"]}))
    with pytest.raises(call_items.ItemsUnavailable):
        call_items.draft("https://gateway.acme.example/v1", "k", "m", ROWS)
