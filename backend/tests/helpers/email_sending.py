"""Helpers that other tests share, moved out of test_email_sending.py so they import no test file."""

import base64
from email.message import EmailMessage
import pytest
from backend import email_sending as S, mailbox_access as MA
from backend.store import H
from backend.tests.helpers.api import give_ok, headers


BOX = "ana@acme.example"


CONTACT = "contact@vendor.example"


def mime(body="Thanks for the update.", to=None, files=False, subject="Update", **headers):
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = BOX, ", ".join(to or [CONTACT]), subject
    for key, value in headers.items():
        msg[key.replace("_", "-")] = value
    msg.set_content(body)
    if files:
        msg.add_attachment(b"proof", maintype="application", subtype="pdf", filename="proof.pdf")
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


@pytest.fixture
def inbox(api, fake_gmail, monkeypatch):
    from backend import gmail_watch

    monkeypatch.setattr(gmail_watch, "watched", lambda c, mailbox: True)  # the server reads its Gmail (fake_gmail)
    with api.app.state.store.transaction() as c:
        row = MA.ensure_mailbox(c, BOX, person_id="ana", created_by="human:ana")
        MA.grant(c, "human:ana", row["id"], "bot", "ops", "read_write")
        incoming(c)
    return api


def incoming(c, *, facts=None, sender=CONTACT, mid="inbound"):
    """A message from `sender` in the inbox's Gmail (the fake_gmail fixture's), with these header facts."""
    from backend.tests.helpers.fake_gmail import CURRENT

    CURRENT["fake"].add(
        BOX,
        mid,
        thread="thread",
        sender=sender,
        to=[BOX],
        subject="Update",
        body="Hello",
        message_id="<" + mid + "@vendor.example>",
        headers={key.title(): value for key, value in (facts or {}).items()},
    )


def policy(api, **values):
    response = api.put("/api/v2/email/sending-policy", json=values, headers=headers())
    assert response.status_code == 200, response.text
    return response.json()


def autonomous(api, **values):
    return policy(
        api,
        **{
            "mode": "on_their_own",
            "what": "replies_and_new",
            "ask_new_address": False,
            "ask_attachments": False,
            "ask_many_recipients": False,
            "ask_legal_money": False,
            "ask_lint": False,
            "second_review": False,
            "undo_seconds": 0,
            "pacing_seconds": 0,
            **values,
        },
    )


def queue(api, *, raw=None, actor="human:ana", **values):
    with api.app.state.store.transaction() as c:
        return S.enqueue(
            c,
            api.app.state.auth,
            actor,
            S.Enqueue(
                mailbox=BOX,
                raw=raw
                or mime(
                    **(
                        {"In_Reply_To": "<" + values["reply_to_id"] + "@vendor.example>"}
                        if values.get("reply_to_id")
                        else {}
                    )
                ),
                **values,
            ),
            {"legal_or_money": False, "status": "classified"},
        )


def dispatch(api):
    """One pass of the server's own sends (email_sending.drain) to the fake Gmail: the MIME of each email it sent."""
    from backend.tests.helpers.fake_gmail import CURRENT

    before = len(CURRENT["fake"].sent)
    S.drain(api.app.state.store, api.app.state.auth)
    return [mime for _, mime, _ in CURRENT["fake"].sent[before:]]


def approved(api, queued, token="ana-test"):
    assert api.get("/api/v2/email/sends/" + queued["id"], headers=headers(token)).status_code == 200
    give_ok(api, queued["approval_id"], token=token)
