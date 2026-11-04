"""Calendar data stays provider-local and reaches only its signed-in person."""

import os

import pytest

from backend.tests.test_api import api, post, runner  # noqa: F401
from clients.tico import APIError
from runner_support.connectors import KNOWN_SENDER_RECHECK, ConnectorPublisher
from runner_support.outage import Outage


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


class MailHub:
    """The hub's mail batch endpoint. A hub from before `known_sender` has a message model that forbids fields it does
    not know, so a batch carrying the field is refused with 422 and nothing is stored."""

    def __init__(self, takes_known_sender=True, refuse_all=False):
        self.takes_known_sender, self.refuse_all = takes_known_sender, refuse_all
        self.accepted = []
        self.refused = 0

    def post(self, path, body):
        assert path == "connectors/mail/messages"
        has_field = any("known_sender" in m for m in body["messages"])
        if self.refuse_all or (has_field and not self.takes_known_sender):
            self.refused += 1
            detail = "body.messages.0.known_sender: Extra inputs are not permitted" if has_field else "bad batch"
            raise APIError("invalid", detail, 422)
        self.accepted.append(body)
        return {"ok": True}


class MailCLI:
    """The local mail CLI: each export hands out the next batch of rows, and the acks are recorded."""

    def __init__(self, *batches):
        self.batches, self.acked = list(batches), []

    def __call__(self, args):
        if args[:2] == ["sync", "export"]:
            return {"messages": self.batches.pop(0) if self.batches else []}
        if args[:2] == ["sync", "ack"]:
            self.acked += args[2:args.index("--as")]
            return {}
        return {"history_id": "h1"}


def sent_row(msg_id, known):
    return {"msg_id": msg_id, "epoch": 1_783_368_400, "from_addr": "pat@customer.example",
            "subject": "Hello", "known_sender": known}


def mail_publisher(tmp_path, hub, mail):
    return ConnectorPublisher({"url": "http://localhost:8765", "token": "test", "projects_dir": str(tmp_path)},
                              client=hub, mail=mail)


def test_mail_batches_carry_known_sender_to_a_hub_that_takes_it(tmp_path, capsys):
    hub = MailHub()
    publisher = mail_publisher(tmp_path, hub, MailCLI([sent_row("m1", True)]))
    assert publisher._mail_mailbox("ana@acme.example", {"message_count": 3}, 100) == 1
    assert hub.accepted[0]["messages"][0]["known_sender"] is True
    assert publisher.refused_known_sender_at is None and hub.refused == 0
    assert "known_sender" not in capsys.readouterr().out


def test_mail_to_a_hub_from_before_known_sender_drops_the_field_once(tmp_path, capsys):
    hub = MailHub(takes_known_sender=False)
    mail = MailCLI([sent_row("m1", True)], [sent_row("m2", False)])
    publisher = mail_publisher(tmp_path, hub, mail)
    # The first batch is refused with the field, posted again without it and acked; the next batch goes without it.
    assert publisher._mail_mailbox("ana@acme.example", {"message_count": 3}, 100) == 2
    assert hub.refused == 1
    assert [m["msg_id"] for m in hub.accepted[0]["messages"]] == ["m1"]
    assert all("known_sender" not in m for body in hub.accepted for m in body["messages"])
    assert mail.acked == ["m1", "m2"] and publisher.refused_known_sender_at is not None
    assert capsys.readouterr().out.count("known_sender mail field") == 1
    # The next cycle sends no field either, and the hub is not asked with it again.
    mail.batches.append([sent_row("m3", True)])
    assert publisher._mail_mailbox("ana@acme.example", {"message_count": 3}, 100) == 1
    assert hub.refused == 1 and "known_sender" not in hub.accepted[-1]["messages"][0]


def test_a_refusal_that_is_not_about_the_field_raises_and_keeps_the_field(tmp_path):
    hub = MailHub(refuse_all=True)
    mail = MailCLI([sent_row("m1", True)])
    publisher = mail_publisher(tmp_path, hub, mail)
    with pytest.raises(APIError):
        publisher._mail_mailbox("ana@acme.example", {"message_count": 3}, 100)
    assert publisher.refused_known_sender_at is None and mail.acked == [] and hub.accepted == []


def test_a_hub_upgraded_while_the_publisher_runs_takes_the_field_again(tmp_path, capsys):
    hub = MailHub(takes_known_sender=False)
    mail = MailCLI()
    publisher = mail_publisher(tmp_path, hub, mail)
    now = [1000.0]
    publisher.clock = lambda: now[0]
    mail.batches.append([sent_row("m1", True)])
    assert publisher._mail_mailbox("ana@acme.example", {"message_count": 3}, 100) == 1     # refused, sent without
    assert hub.refused == 1 and publisher.refused_known_sender_at == 1000.0

    now[0] += KNOWN_SENDER_RECHECK - 1          # still held: sent without the field, the hub not asked with it
    mail.batches.append([sent_row("m2", True)])
    publisher._mail_mailbox("ana@acme.example", {"message_count": 3}, 100)
    assert hub.refused == 1 and "known_sender" not in hub.accepted[-1]["messages"][0]

    now[0] += 1                                 # due: asked with the field again, still an old hub, held again
    mail.batches.append([sent_row("m3", True)])
    publisher._mail_mailbox("ana@acme.example", {"message_count": 3}, 100)
    assert hub.refused == 2 and publisher.refused_known_sender_at == 1000.0 + KNOWN_SENDER_RECHECK
    assert "known_sender" not in hub.accepted[-1]["messages"][0]

    hub.takes_known_sender = True               # the hub is updated while this publisher keeps running
    now[0] += KNOWN_SENDER_RECHECK
    mail.batches.append([sent_row("m4", True)])
    publisher._mail_mailbox("ana@acme.example", {"message_count": 3}, 100)
    assert hub.accepted[-1]["messages"][0]["known_sender"] is True
    assert publisher.refused_known_sender_at is None and hub.refused == 2
    assert capsys.readouterr().out.count("known_sender mail field") == 1     # logged at the first refusal only
