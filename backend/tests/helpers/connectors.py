"""Helpers that other tests share, moved out of test_connectors.py so they import no test file."""


def mail_message(**overrides):
    row = {
        "msg_id": "m-plain",
        "thread_id": "t-plain",
        "epoch": 1_783_368_400,
        "date": "2026-09-02T09:30:00-07:00",
        "from_addr": "person@customer.example",
        "from_header": "Real Person <person@customer.example>",
        "to": ["ana@acme.example"],
        "cc": [],
        "subject": "Pricing question",
        "snippet": "How does",
        "labels": ["INBOX", "UNREAD"],
        "body": "How does pricing work?",
        "body_truncated": False,
        "attachments": [],
        "list_id": "",
        "is_internal": False,
        "has_unsubscribe": False,
    }
    row.update(overrides)
    return row
