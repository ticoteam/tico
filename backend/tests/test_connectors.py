"""Calendar data stays provider-local and reaches only its signed-in person."""

import os

import pytest

from backend.tests.test_api import api, get, headers, post, runner  # noqa: F401
from runner.connectors import ConnectorPublisher
from runner.outage import Outage


def event(title="Product sync"):
    return {"occurrence_id": "event_123", "event_id": "provider-event",
            "title": title, "start": "2026-09-11T09:00:00-07:00",
            "end": "2026-09-11T09:30:00-07:00",
            "attendees": ["ana@acme.example", "ben@acme.example"],
            "meeting_url": "https://meet.google.com/abc-defg-hij"}


class FakeClient:
    def __init__(self):
        self.published = []
        self.results = []

    def get(self, path):
        assert path == "connectors/calendar/targets"
        return {"hours": 24, "people": [
            {"id": "ana", "email": "ana@acme.example"},
            {"id": "ben", "email": "ben@acme.example"},
        ]}

    def post(self, path, body):
        if path == "connectors/calendar/actions/claim":
            return {"action": None}
        if path.startswith("connectors/calendar/actions/"):
            self.results.append((path, body))
            return {"ok": True}
        assert path == "connectors/calendar/snapshots"
        self.published.append(body)
        return {"ok": True}


def test_connector_worker_publishes_successes_without_cloud_provider_credentials(tmp_path):
    script = tmp_path / "hub/scripts/mail.sh"
    script.parent.mkdir(parents=True)
    script.write_text("#!/bin/sh\nexit 1\n")
    script.chmod(os.stat(script).st_mode | 0o100)
    client = FakeClient()
    publisher = ConnectorPublisher({"url": "http://localhost:8765", "token": "test",
                                    "projects_dir": str(tmp_path)}, client=client,
                                   fetch=lambda email, hours: [event(email)] if email.startswith("ana") else [])
    assert publisher.tick() == 2
    assert len(client.published) == 1
    snapshots = client.published[0]["snapshots"]
    assert snapshots[0]["email"] == "ana@acme.example" and snapshots[0]["events"][0]["title"] == "ana@acme.example"
    assert snapshots[1] == {"email": "ben@acme.example", "events": []}

    def partial(email, hours):
        if email.startswith("ben"):
            raise RuntimeError("provider detail must remain local")
        return [event()]
    publisher.fetch = partial
    # A failed account is logged on this Mac (account and error class only) and keeps its prior
    # cloud snapshot; the tick still publishes the others and never raises the provider detail.
    lines = []
    publisher.accounts["ben@acme.example"] = Outage("Tico connectors", "calendar refresh failed for ben@acme.example",
                                                  "still failing", "working again", out=lines.append)
    assert publisher.tick() == 1
    assert len(client.published) == 2 and len(client.published[-1]["snapshots"]) == 1
    assert lines == ["Tico connectors: calendar refresh failed for ben@acme.example (RuntimeError); retrying"]
    assert not any("provider detail" in str(call) for call in client.published)


def mail_message(**overrides):
    row = {"msg_id": "m-plain", "thread_id": "t-plain", "epoch": 1_783_368_400,
           "date": "2026-09-02T09:30:00-07:00", "from_addr": "person@customer.example",
           "from_header": "Real Person <person@customer.example>", "to": ["ana@acme.example"],
           "cc": [], "subject": "Pricing question", "snippet": "How does",
           "labels": ["INBOX", "UNREAD"], "body": "How does pricing work?",
           "body_truncated": False, "attachments": [], "list_id": "",
           "is_internal": False, "has_unsubscribe": False}
    row.update(overrides)
    return row


def mail_publish(mailbox="ana@acme.example", messages=None, deleted=None, **extra):
    body = {"mailbox": mailbox, "messages": messages if messages is not None else [mail_message()],
            "deleted": deleted or [], "synced_at": "2026-09-16T20:00:00+00:00"}
    body.update(extra)
    return body


def test_mail_tables_are_owner_only_in_sql(api):
    from backend.tests.test_sql import column, query

    machine = runner(api)
    post(api, "connectors/mail/messages", mail_publish(), machine["token"])
    assert column(api, "SELECT msg_id FROM mail_messages") == ["m-plain"]
    assert column(api, "SELECT address FROM mail_mailboxes") == ["ana@acme.example"]
    assert column(api, "SELECT msg_id FROM mail_fts") == ["m-plain"]
    assert query(api, "SELECT msg_id FROM mail_messages", "ben-test")["rows"] == []
    assert query(api, "SELECT address FROM mail_mailboxes", "ben-test")["rows"] == []
    assert query(api, "SELECT msg_id FROM mail_fts", "ben-test")["rows"] == []



def test_an_overlong_mail_field_is_cut_not_refused_so_the_mailbox_keeps_syncing(api):
    # Bruno 2026-09-27 and Chris 2026-10-01: one 32,809-character body refused every batch for hours.
    from backend.connectors import MAIL_BODY_MAX
    from backend.tests.test_sql import column

    machine = runner(api)
    long = mail_message(msg_id="m-long", body="x" * (MAIL_BODY_MAX + 41), subject="s" * 1200,
                        cc=[f"p{i}@example.com" for i in range(150)])
    post(api, "connectors/mail/messages", mail_publish(messages=[mail_message(), long]), machine["token"])
    assert sorted(column(api, "SELECT msg_id FROM mail_messages")) == ["m-long", "m-plain"]
    assert column(api, "SELECT length(body) FROM mail_messages WHERE msg_id='m-long'") == [MAIL_BODY_MAX]
    assert column(api, "SELECT body_truncated FROM mail_messages WHERE msg_id='m-long'") == [1]
    assert column(api, "SELECT length(subject) FROM mail_messages WHERE msg_id='m-long'") == [998]
    assert column(api, "SELECT json_array_length(cc_json) FROM mail_messages WHERE msg_id='m-long'") == [100]


def test_the_mail_tools_cut_body_fits_the_hub_with_its_note():
    from backend.connectors import MAIL_BODY_MAX
    from connectors.mail import MAX_BODY
    from connectors.mail.gmail import truncate

    text, cut = truncate("y" * (MAX_BODY * 2))
    assert cut and len(text) == MAX_BODY <= MAIL_BODY_MAX
    assert truncate("short") == ("short", False)
