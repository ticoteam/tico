"""The only provider send needs a one-use permit; uncertain attempts never send again."""

from types import SimpleNamespace
from unittest import mock

import pytest

from connectors.mail import Failure, actions, compose, db, gmail as gm, policy


def test_missing_server_queue_confirmation_fails_closed():
    with mock.patch.object(policy, "HUB_POST", return_value=None):
        with pytest.raises(Failure):
            policy.queue_send("support@acme.example", "frozen")


def test_gmail_does_not_retry_a_send_after_a_retryable_provider_error():
    class ProviderError(Exception):
        status_code = 503

    request, sleep = mock.Mock(), mock.Mock()
    request.execute.side_effect = ProviderError("Delivery response lost")
    service = mock.Mock(_tico_granted=None)
    service.users.return_value.messages.return_value.send.return_value = request
    gmail = gm.Gmail(service, "support@acme.example", sleep=sleep)
    with pytest.raises(Failure):
        gmail.send_raw("ZnJvemVu")
    request.execute.assert_called_once_with()
    sleep.assert_not_called()


@pytest.mark.parametrize("uncertain", [False, True])
def test_one_permitted_provider_attempt_even_after_an_uncertain_delivery(tmp_path, uncertain):
    conn = db.connect(tmp_path / "mail.db")
    raw = compose.build(["customer@vendor.example"], "Update", "The order is ready.", from_addr="support@acme.example")
    gmail = mock.Mock()
    gmail.create_draft.return_value = {"id": "provider-draft"}
    gmail.send_raw.side_effect = Failure("Response lost") if uncertain else None
    gmail.send_raw.return_value = {"id": "provider-message"}
    ctx = SimpleNamespace(conn=conn, mailbox="support@acme.example", gmail=gmail)
    broker = mock.Mock()
    broker.post.return_value = {"send": {"raw": raw, "thread_id": "", "actor": "bot:support",
                                       "recipients": ["customer@vendor.example"], "subject": "Update"}}
    action = {"id": "queue-id", "mailbox": ctx.mailbox, "action": "dispatch_send", "raw": "untrusted"}
    with mock.patch("connectors.mail.oauth.broker", return_value=broker):
        first = actions.perform(ctx, action)
        repeated = actions.perform(ctx, action)
    gmail.send_raw.assert_called_once_with(raw, "")
    gmail.create_draft.assert_not_called()
    gmail.update_draft.assert_not_called()
    broker.post.assert_called_once()
    assert first["status"] == repeated["status"] == ("unknown" if uncertain else "sent")
    assert repeated["retry"] is False
    conn.close()


def test_concurrent_draft_edit_cannot_change_the_frozen_provider_send(tmp_path):
    from connectors.mail.tests.fake import FakeGmailService
    conn = db.connect(tmp_path / "mail.db")
    service = FakeGmailService([])
    provider = gm.Gmail(service, "support@acme.example", sleep=lambda delay: None)
    frozen = compose.build(["customer@vendor.example"], "Approved", "Approved body.", from_addr=provider.mailbox)
    changed = compose.build(["outsider@vendor.example"], "Unapproved", "Changed body.", from_addr=provider.mailbox)
    draft = provider.create_draft(frozen)
    provider.update_draft(draft["id"], changed)
    broker = mock.Mock()
    broker.post.return_value = {"send": {"raw": frozen, "draft_id": draft["id"], "actor": "bot:ops",
        "recipients": ["customer@vendor.example"], "subject": "Approved"}}
    ctx = SimpleNamespace(conn=conn, mailbox=provider.mailbox, gmail=provider)
    with mock.patch("connectors.mail.oauth.broker", return_value=broker), mock.patch.object(provider, "update_draft") as update:
        answer = actions.perform(ctx, {"id": "race", "action": "dispatch_send", "lease_id": "lease"})
    assert answer["status"] == "sent"
    update.assert_not_called()
    delivered = service.store[service.sent[0]["id"]]
    assert service.header(delivered, "To") == "customer@vendor.example"
    assert service.header(delivered, "Subject") == "Approved"
    assert provider.get_draft_raw(draft["id"]) == changed
    conn.close()
