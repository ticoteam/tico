"""Helpers that other tests share, moved out of test_email_sending.py so they import no test file."""

import base64
import json
from email.message import EmailMessage
import pytest
from backend import email_sending as S, mailbox_access as MA
from backend.store import H
from backend.tests.helpers.api import headers, post as api_post


BOX = "ana@acme.example"


CONTACT = "contact@vendor.example"


def post(api, path, body, **kwargs):
    if (
        path.startswith("connectors/email/sends/")
        and not path.endswith("/claim")
        and isinstance(body, dict)
        and "lease_id" not in body
    ):
        with api.app.state.store.read() as c:
            row = c.execute("SELECT lease_id FROM email_sends WHERE id=?", (path.split("/")[-2],)).fetchone()
        if row:
            body = {**body, "lease_id": row["lease_id"]}
    return api_post(api, path, body, **kwargs)


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
def inbox(api):
    with api.app.state.store.transaction() as c:
        row = MA.ensure_mailbox(c, BOX, person_id="ana", created_by="human:ana")
        MA.grant(c, "human:ana", row["id"], "bot", "ops", "read_write")
        incoming(c)
    return api


def incoming(c, *, facts=None, sender=CONTACT, mid="inbound"):
    c.execute(
        "INSERT INTO mail_messages(mailbox,msg_id,thread_id,epoch,date,from_addr,to_addresses,cc,subject,"
        "snippet,labels,body,attachments,updated_at,send_facts) VALUES(?,?,?,0,?,?,?,?,?,?,?,?,?,?,?)",
        (
            BOX,
            mid,
            "thread",
            H.now(),
            sender,
            json.dumps([BOX]),
            "[]",
            "Update",
            "Hello",
            "[]",
            "Hello",
            "[]",
            H.now(),
            json.dumps({"message-id": "<" + mid + "@vendor.example>", **(facts or {})}),
        ),
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


def claim(api, token):
    return post(api, "connectors/email/sends/claim", {"mailbox": BOX}, token=token)["send"]


def approved(api, queued, token="ana-test"):
    assert api.get("/api/v2/email/sends/" + queued["id"], headers=headers(token)).status_code == 200
    post(api, "approvals/" + queued["approval_id"], {"decision": "approved"}, token=token)
