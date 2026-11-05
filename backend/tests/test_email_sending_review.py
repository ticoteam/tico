"""Regressions for the twelve send-policy review findings, through real authorization and frozen MIME."""

import base64
import json
from email.message import EmailMessage
from unittest import mock

import pytest
import yaml

from backend import judge
from backend.store import Problem
from backend.tests.test_api import api, headers, post as api_post, runner, setup_attempt
from backend.tests.test_email_sending import (BOX, CONTACT, H, S, approved, autonomous, claim, inbox, incoming,
                                             mime, policy, post, queue)


def raw(message):
    return base64.urlsafe_b64encode(message.as_bytes()).decode()


def test_negative_chat_cannot_approve_a_different_large_send_through_the_api(inbox):
    _, _, attempt = setup_attempt(inbox, "ops")
    note = api_post(inbox, "chat/ops", {"text": "Do not send any email to outsiders."})
    with mock.patch.object(S, "classify", return_value={"legal_or_money": True, "status": "classified"}):
        result = api_post(inbox, "email/sends", {"mailbox": BOX,
            "raw": mime(to=[f"person{i}@vendor.example" for i in range(11)], files=True, body="We agree to pay $100000."),
            "approval_id": note["id"]}, token=attempt["token"])["send"]
    assert result["state"] == "waiting_approval" and not result["decision"]["approved"]
    assert result["approval_id"] != note["id"]


@pytest.mark.parametrize("text,named,viewed,allowed", [
    ("Send it", True, True, True), ("Do not send it", True, True, False),
    ("Send it unless they object", True, True, False), ("We discussed the email", True, True, False),
    ("Send it", False, True, False), ("Send it", True, False, False),
])
def test_chat_instruction_requires_a_prior_exact_preview_and_unambiguous_yes(inbox, text, named, viewed, allowed):
    policy(inbox, undo_seconds=0)
    queued = queue(inbox, actor="bot:ops")
    if viewed:
        detail = inbox.get("/api/v2/email/sends/" + queued["id"], headers=headers())
        assert detail.json()["preview_complete"]
    note = api_post(inbox, "chat/ops", {"text": text + (" " + queued["id"] if named else "")})
    with inbox.app.state.store.transaction() as c:
        c.execute("UPDATE messages SET created_at=? WHERE id=?", (H.shift(H.now(), seconds=1), note["id"]))
        c.execute("UPDATE email_sends SET approval_id=? WHERE id=?", (note["id"], queued["id"]))
        row = c.execute("SELECT * FROM email_sends WHERE id=?", (queued["id"],)).fetchone()
        assert S.approved(c, inbox.app.state.auth, row, S.effective(c, BOX, "ops")) is allowed


def test_company_recipient_message_type_and_approver_cannot_be_loosened(inbox):
    autonomous(inbox, to="internal", what="replies_only", who_can_ok="inbox_admins")
    with inbox.app.state.store.transaction() as c:
        who = inbox.app.state.auth.identity_for_actor(c, "human:ana")
        for scope in ("inbox:" + BOX, "bot:ops"):
            S.update_policy(c, who, scope, S.PolicyUpdate(to="anyone", what="replies_and_new", who_can_ok="bot_managers"))
        found = S.effective(c, BOX, "ops")
        assert (found["to"], found["what"], found["who_can_ok"]) == ("internal", "replies_only", "inbox_admins")
        assert not S.can_ok(c, inbox.app.state.auth, "human:cara", BOX, "ops", found)
    queued = queue(inbox, actor="bot:ops")
    assert queued["state"] == "blocked" and "internal_only" in queued["decision"]["reasons"]


def registry(inbox, **values):
    base = {"global": {"send_enabled": True}, "internal_domains": ["acme.example"],
            "defaults": {"max_sends_per_day": 20, "per_recipient_cooldown_days": 0}}
    (inbox.app.state.store.settings.registry_dir / "mail-policy.yaml").write_text(yaml.safe_dump({**base, **values}))


@pytest.mark.parametrize("restriction,reason", [
    ({"global": {"send_enabled": False}}, "registry_send_disabled"),
    ({"mailboxes": {BOX: {"paused": True}}}, "registry_mailbox_paused"),
    ({"blocklist": {"addresses": ["ben@acme.example"]}}, "registry_blocklist"),
    ({"owner_handles_personally": {"domains": ["acme.example"]}}, "registry_owner_handles_personally"),
])
def test_migrated_bot_cannot_skip_registry_checks_at_enqueue_or_dispatch(inbox, restriction, reason):
    with inbox.app.state.store.transaction() as c:
        c.execute("INSERT INTO bot_mail_settings VALUES('ops',1,'[]',?,'human:ana')", (H.now(),))
        c.execute("INSERT INTO email_sending_policies VALUES('bot:ops',?,?,'migration')",
                  ('{"mode":"on_their_own","legacy":true}', H.now()))
    registry(inbox, **restriction)
    blocked = queue(inbox, actor="bot:ops", raw=mime(to=["ben@acme.example"]))
    assert blocked["state"] == "blocked" and reason in blocked["decision"]["reasons"]
    registry(inbox)
    autonomous(inbox)
    queued = queue(inbox, actor="bot:ops", raw=mime(to=["ben@acme.example"], body="A second intent"))
    machine = runner(inbox)
    assert claim(inbox, machine["token"])["id"] == queued["id"]
    registry(inbox, **restriction)
    assert post(inbox, "connectors/email/sends/" + queued["id"] + "/dispatch", {"mailbox": BOX}, token=machine["token"])["send"] is None


def test_registry_daily_cap_cooldown_and_recipient_caps_apply_to_direct_api(inbox):
    autonomous(inbox)
    registry(inbox, defaults={"max_sends_per_day": 1, "per_recipient_cooldown_days": 3, "max_external_recipients": 1})
    assert "registry_recipient_cap" in queue(inbox, raw=mime(to=[CONTACT, "two@vendor.example"]))["decision"]["reasons"]
    first = queue(inbox)
    machine = runner(inbox)
    assert claim(inbox, machine["token"])
    post(inbox, "connectors/email/sends/" + first["id"] + "/dispatch", {"mailbox": BOX}, token=machine["token"])
    post(inbox, "connectors/email/sends/" + first["id"] + "/result", {"status": "sent", "message": "receipt"}, token=machine["token"])
    second = queue(inbox, raw=mime(body="Different message"))
    assert claim(inbox, machine["token"]) is None
    with inbox.app.state.store.read() as c:
        row = c.execute("SELECT * FROM email_sends WHERE id=?", (second["id"],)).fetchone()
        reasons = S.decision(c, inbox.app.state.auth, row, dispatch=True)["reasons"]
    assert "registry_daily_cap" in reasons and "registry_cooldown" in reasons


def test_a_retained_allowance_is_revoked_when_the_current_registry_removes_its_recipient(inbox):
    with inbox.app.state.store.transaction() as c:
        c.execute("INSERT INTO bot_mail_settings VALUES('ops',1,'[]',?,'human:ana')", (H.now(),))
        c.execute("INSERT INTO email_sending_policies VALUES('bot:ops',?,?,'migration')", (json.dumps({
            "mode": "on_their_own", "legacy": True, "legacy_allowances": {BOX: [CONTACT]}}), H.now()))
    allowance = {"employee": "ops", "mailbox": BOX, "recipients": {"source": "list", "list": [CONTACT]}}
    registry(inbox, allowances=[allowance])
    queued = queue(inbox, actor="bot:ops")
    assert queued["state"] == "pending"
    with inbox.app.state.store.transaction() as c:
        c.execute("UPDATE email_sends SET created_at=?,send_after=? WHERE id=?",
                  (H.shift(H.now(), minutes=-3), H.shift(H.now(), minutes=-1), queued["id"]))
    machine = runner(inbox)
    assert claim(inbox, machine["token"])["id"] == queued["id"]
    allowance["recipients"]["list"] = []
    registry(inbox, allowances=[allowance])
    permit = post(inbox, "connectors/email/sends/" + queued["id"] + "/dispatch", {"mailbox": BOX}, token=machine["token"])
    assert permit["send"] is None


def test_unnamed_inline_files_require_approval_and_bind_the_actual_bytes(inbox):
    autonomous(inbox, ask_attachments=True)
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = BOX, CONTACT, "Inline file"
    msg.set_content("Thanks.")
    msg.add_attachment(b"secret", maintype="application", subtype="pdf", disposition="inline", cid="<file>")
    first = queue(inbox, raw=raw(msg))
    assert first["state"] == "waiting_approval"
    assert first["attachments"][0]["sha256"] == S.digest(b"secret")
    approved(inbox, first)
    msg.get_payload()[1].set_payload(base64.b64encode(b"changed").decode())
    changed = queue(inbox, raw=raw(msg), approval_id=first["approval_id"])
    assert changed["state"] == "waiting_approval" and not changed["decision"]["approved"]


def test_retained_daily_cap_counts_verified_sent_history_before_the_new_queue(inbox):
    autonomous(inbox)
    registry(inbox, defaults={"max_sends_per_day": 1, "per_recipient_cooldown_days": 0})
    with inbox.app.state.store.transaction() as c:
        incoming(c, sender=BOX, mid="old-sent")
        c.execute("UPDATE mail_messages SET labels='[\"SENT\"]',epoch=? WHERE msg_id='old-sent'",
                  (H.parse_ts(H.now()).timestamp(),))
    queued = queue(inbox)
    machine = runner(inbox)
    assert claim(inbox, machine["token"]) is None
    with inbox.app.state.store.read() as c:
        row = c.execute("SELECT decision FROM email_sends WHERE id=?", (queued["id"],)).fetchone()
        assert "registry_daily_cap" in json.loads(row["decision"])["reasons"]


def test_classifier_sees_html_as_well_as_plain_text_and_never_truncates_to_a_no(inbox):
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = BOX, CONTACT, "Update"
    msg.set_content("Thanks for the update.")
    msg.add_alternative("<p>We accept the contract and agree to pay $100000.</p><script>bad()</script>", subtype="html")
    seen = []
    with mock.patch.object(judge, "server_engine", return_value=object()), mock.patch.object(judge, "audit_detail", return_value={}), mock.patch.object(judge, "ask", side_effect=lambda engine, context, *args: seen.append(context) or {"answers": {"legal_or_money": {"value": "yes"}}}):
        result = S.classify(inbox.app, inbox.app.state.store, S.snapshot(raw(msg), BOX))
    assert result["legal_or_money"] is True and "$100000" in seen[0]["body"] and "Thanks" in seen[0]["body"]
    assert "<p>" not in seen[0]["body"] and "bad()" not in seen[0]["body"]
    inline = EmailMessage()
    inline.set_content("<p>Also agree to pay $500000.</p>", subtype="html")
    inline["Content-Disposition"] = "inline"
    msg.make_mixed()
    msg.attach(inline)
    with mock.patch.object(judge, "server_engine", return_value=object()), mock.patch.object(judge, "audit_detail", return_value={}), mock.patch.object(judge, "ask", side_effect=lambda engine, context, *args: seen.append(context) or {"answers": {"legal_or_money": {"value": "yes"}}}):
        S.classify(inbox.app, inbox.app.state.store, S.snapshot(raw(msg), BOX))
    assert "$500000" in seen[-1]["body"]
    with mock.patch.object(judge, "server_engine", return_value=object()), mock.patch.object(judge, "ask") as ask:
        result = S.classify(inbox.app, inbox.app.state.store, S.snapshot(mime(body="a" * 16001 + "Pay $100000"), BOX))
        assert result["legal_or_money"] is None
        ask.assert_not_called()


def test_nested_email_attachment_headers_and_mime_charset_are_bound_to_the_proof():
    inner = EmailMessage()
    inner["Subject"] = "Approved attachment"
    inner.set_content("The same body")
    outer = EmailMessage()
    outer["From"], outer["To"], outer["Subject"] = BOX, CONTACT, "Attached message"
    outer.set_content("Please read.")
    outer.add_attachment(inner)
    first = S.snapshot(raw(outer), BOX)
    inner.replace_header("Subject", "Changed attachment")
    changed = S.snapshot(raw(outer), BOX)
    assert first["attachments"][0]["sha256"] != changed["attachments"][0]["sha256"]
    assert first["content_sha256"] != changed["content_sha256"]
    assert b"Approved attachment" in S.attachment(first["raw"], first["attachments"][0]["part"])
    html = EmailMessage()
    html["From"], html["To"], html["Subject"] = BOX, CONTACT, "HTML"
    html.set_content("<p>caf\u00e9</p>", subtype="html", charset="utf-8")
    before = S.snapshot(raw(html), BOX)
    html.set_param("charset", "iso-8859-1")
    assert before["content_sha256"] != S.snapshot(raw(html), BOX)["content_sha256"]


def test_mime_reply_headers_enforce_loop_and_duplicate_guards_without_caller_metadata(inbox):
    autonomous(inbox, ask_new_address=True)
    with inbox.app.state.store.transaction() as c:
        incoming(c, facts={"auto-submitted": "auto-replied"}, mid="automatic", sender="robot@vendor.example")
    blocked = queue(inbox, raw=mime(to=["robot@vendor.example"], In_Reply_To="<automatic@vendor.example>"))
    assert blocked["state"] == "blocked" and "reply_loop" in blocked["decision"]["reasons"]
    first = queue(inbox, raw=mime(In_Reply_To="<inbound@vendor.example>"))
    assert first["state"] == "pending"
    repeated = queue(inbox, raw=mime(body="Second answer", In_Reply_To="<inbound@vendor.example>"))
    assert "already_replied" in repeated["decision"]["reasons"]
    with pytest.raises(Problem, match="Reply identifiers"):
        queue(inbox, raw=mime(In_Reply_To="<inbound@vendor.example>"), reply_to_id="automatic")


def test_expired_unpermitted_claim_is_recoverable_and_old_lease_is_fenced(inbox):
    autonomous(inbox)
    queued = queue(inbox)
    machine = runner(inbox)
    old = claim(inbox, machine["token"])
    with inbox.app.state.store.transaction() as c:
        c.execute("UPDATE email_sends SET claimed_at=? WHERE id=?", (H.shift(H.now(), minutes=-6), queued["id"]))
    renewed = claim(inbox, machine["token"])
    assert renewed["id"] == queued["id"] and renewed["lease_id"] != old["lease_id"]
    path = "connectors/email/sends/" + queued["id"] + "/dispatch"
    api_post(inbox, path, {"mailbox": BOX, "lease_id": old["lease_id"]}, token=machine["token"], expected=403)
    permit = api_post(inbox, path, {"mailbox": BOX, "lease_id": renewed["lease_id"]}, token=machine["token"])
    assert permit["send"]["raw"]
    with inbox.app.state.store.transaction() as c:
        c.execute("UPDATE email_sends SET claimed_at=? WHERE id=?", (H.shift(H.now(), minutes=-6), queued["id"]))
    assert claim(inbox, machine["token"]) is None
    with inbox.app.state.store.read() as c:
        row = c.execute("SELECT * FROM email_sends WHERE id=?", (queued["id"],)).fetchone()
        assert row["state"] == "unknown" and not row["raw"]


def test_one_hundred_waiting_approvals_do_not_starve_a_due_send(inbox):
    autonomous(inbox)
    with inbox.app.state.store.transaction() as c:
        S.update_policy(c, inbox.app.state.auth.identity_for_actor(c, "human:ana"), "bot:ops", S.PolicyUpdate(mode="ask_first"))
    for index in range(100):
        queue(inbox, actor="bot:ops", raw=mime(body="Unapproved " + str(index)))
    due = queue(inbox, raw=mime(body="Authorized human send"))
    machine = runner(inbox)
    assert claim(inbox, machine["token"])["id"] == due["id"]


def test_approver_must_load_authorized_frozen_preview_and_download_exact_attachment(inbox):
    policy(inbox, who_can_ok="named_people", approvers=["cara"])
    queued = queue(inbox, actor="bot:ops", raw=mime(body="<script>unsafe</script>", files=True))
    api_post(inbox, "approvals/" + queued["approval_id"], {"decision": "approved"}, token="cara-test", expected=409)
    assert inbox.get("/api/v2/email/sends/" + queued["id"], headers=headers("ben-test")).status_code == 403
    preview = inbox.get("/api/v2/email/sends/" + queued["id"], headers=headers("cara-test")).json()
    assert preview["preview_complete"] and preview["body"] == "<script>unsafe</script>\n"
    file = preview["send"]["attachments"][0]
    path = f"/api/v2/email/sends/{queued['id']}/attachments/{file['part']}"
    assert inbox.get(path, headers=headers("ben-test")).status_code == 403
    response = inbox.get(path, headers=headers("cara-test"))
    assert response.content == b"proof" and S.digest(response.content) == file["sha256"]
    api_post(inbox, "approvals/" + queued["approval_id"], {"decision": "approved"}, token="cara-test")


@pytest.mark.parametrize("state", ["canceled", "blocked", "failed", "declined"])
def test_new_intent_can_retry_a_definite_non_send_but_same_operation_cannot(inbox, state):
    autonomous(inbox)
    first = queue(inbox, operation_id="first-operation")
    with inbox.app.state.store.transaction() as c:
        c.execute("UPDATE email_sends SET state=?,raw='' WHERE id=?", (state, first["id"]))
    assert queue(inbox, operation_id="first-operation")["id"] == first["id"]
    new = queue(inbox, operation_id="new-operation")
    assert new["id"] != first["id"] and new["state"] == "pending"
    assert queue(inbox, operation_id="retry-of-active")["id"] == new["id"]


def test_sent_or_unknown_delivery_remains_deduplicated_for_a_new_operation(inbox):
    autonomous(inbox)
    first = queue(inbox)
    with inbox.app.state.store.transaction() as c:
        c.execute("UPDATE email_sends SET state='unknown',raw='' WHERE id=?", (first["id"],))
    assert queue(inbox, operation_id="new-intent")["id"] == first["id"]
