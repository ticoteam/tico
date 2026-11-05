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
    service.users.return_value.drafts.return_value.send.return_value = request
    gmail = gm.Gmail(service, "support@acme.example", sleep=sleep)
    with pytest.raises(Failure):
        gmail.send_draft("draft")
    request.execute.assert_called_once_with()
    sleep.assert_not_called()


@pytest.mark.parametrize("uncertain", [False, True])
def test_one_permitted_provider_attempt_even_after_an_uncertain_delivery(tmp_path, uncertain):
    conn = db.connect(tmp_path / "mail.db")
    raw = compose.build(["customer@vendor.example"], "Update", "The order is ready.", from_addr="support@acme.example")
    gmail = mock.Mock()
    gmail.create_draft.return_value = {"id": "provider-draft"}
    gmail.send_draft.side_effect = Failure("Response lost") if uncertain else None
    gmail.send_draft.return_value = {"id": "provider-message"}
    ctx = SimpleNamespace(conn=conn, mailbox="support@acme.example", gmail=gmail)
    broker = mock.Mock()
    broker.post.return_value = {"send": {"raw": raw, "thread_id": "", "actor": "bot:support",
                                       "recipients": ["customer@vendor.example"], "subject": "Update"}}
    action = {"id": "queue-id", "mailbox": ctx.mailbox, "action": "dispatch_send", "raw": "untrusted"}
    with mock.patch("connectors.mail.oauth.broker", return_value=broker):
        first = actions.perform(ctx, action)
        repeated = actions.perform(ctx, action)
    gmail.send_draft.assert_called_once_with("provider-draft")
    gmail.create_draft.assert_called_once_with(raw, "")
    broker.post.assert_called_once()
    assert first["status"] == repeated["status"] == ("unknown" if uncertain else "sent")
    assert repeated["retry"] is False
    conn.close()
