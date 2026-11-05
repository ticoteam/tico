"""Regressions for the twelve send-policy review findings, through real authorization and frozen MIME."""

import base64
import copy
import json
import sqlite3
from contextlib import closing
from email.message import EmailMessage
from unittest import mock
from types import SimpleNamespace

import pytest
import yaml

from backend import judge
from backend.store import Problem
from backend.tests.test_api import api, headers, post as api_post, runner, setup_attempt
from backend.tests.test_email_sending import (BOX, CONTACT, H, S, approved, autonomous, claim, inbox, incoming,
                                             mime, policy, post, queue)


def raw(message):
    return base64.urlsafe_b64encode(message.as_bytes()).decode()


@pytest.mark.parametrize("form", ["alternative", "inline_text", "primary_text", "primary_unknown", "primary_multipart"])
@pytest.mark.parametrize("legal_prompt", [True, False])
def test_unsupported_recipient_visible_bodies_always_ask(inbox, form, legal_prompt):
    autonomous(inbox, ask_attachments=False, ask_legal_money=legal_prompt)
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = BOX, CONTACT, "Alternative body"
    message.set_content("Thanks for the update.")
    commitment = "<bold>We accept the contract and agree to pay $100000.</bold>"
    if form == "alternative":
        message.add_alternative(commitment, subtype="enriched")
    elif form == "inline_text":
        message.make_mixed()
        part = EmailMessage()
        part.set_content(commitment, subtype="enriched")
        part["Content-Disposition"] = "inline"
        message.attach(part)
    elif form == "primary_text":
        message.set_content(commitment, subtype="enriched")
    elif form == "primary_multipart":
        message.make_mixed()
        message.replace_header("Content-Type", "multipart/x-body")
    else:
        message.set_content(commitment.encode(), maintype="application", subtype="x-body")
    snap = S.snapshot(raw(message), BOX)
    assert snap["classification_complete"] is False
    with mock.patch.object(judge, "server_engine", return_value=object()), mock.patch.object(judge, "ask") as ask:
        response = api_post(inbox, "email/sends", {"mailbox": BOX, "raw": raw(message)})["send"]
    ask.assert_not_called()
    assert response["classification"]["legal_or_money"] is None
    assert response["state"] == "waiting_approval"
    preview = inbox.get("/api/v2/email/sends/" + response["id"], headers=headers()).json()
    assert preview["preview_complete"] and preview["bodies"]


@pytest.mark.parametrize("form", ["inline_enriched", "no_disposition", "inline_html", "primary_plain", "named_container"])
def test_named_inline_bodies_cannot_hide_commitments_from_classification(inbox, form):
    autonomous(inbox, ask_attachments=False, ask_legal_money=True)
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = BOX, CONTACT, "Named body " + form
    commitment = "We accept the contract and agree to pay $100000."
    message.set_content(commitment if form == "primary_plain" else "Thanks for the update.")
    if form != "primary_plain":
        message.add_alternative("<p>" + commitment + "</p>", subtype="html" if form in ("inline_html", "named_container") else "enriched")
    part = message if form in ("primary_plain", "named_container") else message.get_payload()[-1]
    if form == "no_disposition":
        part.set_param("name", "answer.enriched", header="Content-Type")
    else:
        part["Content-Disposition"] = 'inline; filename="answer.txt"'
    snap = S.snapshot(raw(message), BOX)
    assert not snap["attachments"]
    assert commitment in "\n".join(body["text"] for body in snap["visible_bodies"])
    supported = form in ("inline_html", "primary_plain", "named_container")
    assert snap["classification_complete"] is supported
    def classify(engine, context, *args):
        assert commitment in context["body"]
        return {"answers": {"legal_or_money": {"value": "yes"}}}
    with mock.patch.object(judge, "server_engine", return_value=object()), \
            mock.patch.object(judge, "ask", side_effect=classify) as ask, mock.patch.object(judge, "audit_detail", return_value={}):
        result = api_post(inbox, "email/sends", {"mailbox": BOX, "raw": raw(message)})["send"]
    assert ask.call_count == int(supported)
    assert result["classification"]["legal_or_money"] is (True if supported else None)
    assert result["state"] == "waiting_approval" and claim(inbox, runner(inbox)["token"]) is None


@pytest.mark.parametrize("subtype", ["plain", "html", "enriched"])
def test_inline_text_files_are_classified_and_keep_attachment_approval_and_download(inbox, subtype):
    autonomous(inbox, ask_attachments=True)
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = BOX, CONTACT, "Inline text file " + subtype
    message.set_content("Please see the attached notes.")
    text = "Harmless notes" if subtype == "plain" else "<p>Harmless notes</p>"
    message.add_attachment(text, subtype=subtype, disposition="inline", filename="notes." + subtype)
    encoded = raw(message)
    snap = S.snapshot(encoded, BOX)
    assert len(snap["attachments"]) == 1
    file = snap["attachments"][0]
    expected = message.get_payload()[-1].get_payload(decode=True)
    assert file["name"] == "notes." + subtype and file["sha256"] == S.digest(expected)
    with mock.patch.object(judge, "server_engine", return_value=object()), \
            mock.patch.object(judge, "ask", return_value={"answers": {"legal_or_money": {"value": "no"}}}) as ask, \
            mock.patch.object(judge, "audit_detail", return_value={}):
        result = api_post(inbox, "email/sends", {"mailbox": BOX, "raw": encoded})["send"]
    if subtype != "enriched":
        assert "Harmless notes" in ask.call_args.args[1]["body"]
        assert "attachments" in result["decision"]["reasons"]
    else:
        ask.assert_not_called()
        assert not snap["classification_complete"]
    assert result["state"] == "waiting_approval" and claim(inbox, runner(inbox)["token"]) is None
    response = inbox.get(f'/api/v2/email/sends/{result["id"]}/attachments/{file["part"]}', headers=headers())
    assert response.status_code == 200 and response.content == expected
    message.get_payload()[-1].set_payload("Changed notes")
    changed = S.snapshot(raw(message), BOX)
    assert changed["attachments"][0]["sha256"] != file["sha256"] and changed["content_sha256"] != snap["content_sha256"]


def test_new_old_format_approval_cannot_authorize_an_added_internal_bcc(inbox):
    _, _, attempt = setup_attempt(inbox, "ops")
    original = mime(to=["ben@acme.example"], body="Sensitive legal reply")
    snap = S.snapshot(original, BOX)
    payload = {"mailbox": BOX, "to": ["ben@acme.example"], "cc": [], "subject": snap["subject"],
               "body_sha256": snap["body_sha256"], "attachments": []}
    refused = inbox.post("/api/v2/approvals", json={"kind": "send", "payload": payload}, headers=headers(attempt["token"]))
    assert refused.status_code == 422 and "/api/v2/email/sends" in refused.text
    with inbox.app.state.store.transaction() as c:
        approval = H.approval_request(c, "bot:ops", "send", payload, to="human:ana")
        H.approval_decide(c, "human:ana", approval["id"], "approved")
    from backend.email_content import parsed
    expanded = parsed(original)
    expanded["Bcc"] = "cara@acme.example"
    queued = queue(inbox, actor="bot:ops", raw=raw(expanded), approval_id=approval["id"])
    assert queued["state"] == "waiting_approval" and not queued["decision"]["approved"]
    card = api_post(inbox, "approvals/" + queued["approval_id"], {"decision": "approved"}, expected=409)
    assert "preview" in str(card)
    approved(inbox, queued)
    with inbox.app.state.store.read() as c:
        payload = H.approval(c, queued["approval_id"])["payload"]
    assert payload["to"] == ["ben@acme.example"] and payload["cc"] == [] and payload["bcc"] == ["cara@acme.example"]
    # A claimed queue id cannot make the public endpoint a second approval creator.
    assert inbox.post("/api/v2/approvals", json={"kind": "send", "payload": payload}, headers=headers(attempt["token"])).status_code == 422


def test_only_approvals_that_predate_queue_cutover_keep_the_old_recipient_scope(inbox):
    original = S.snapshot(mime(to=["ben@acme.example"]), BOX)
    with inbox.app.state.store.transaction() as c:
        approval = H.approval_request(c, "bot:ops", "send", {"mailbox": BOX, "to": ["ben@acme.example"],
            "cc": [], "subject": original["subject"], "body_sha256": original["body_sha256"]}, to="human:ana")
        H.approval_decide(c, "human:ana", approval["id"], "approved")
        cutover = c.execute("SELECT applied_at FROM schema_migrations WHERE version='20261105040656_email_sending_queue'").fetchone()[0]
        c.execute("UPDATE approvals SET created_at=? WHERE id=?", (H.shift(cutover, seconds=-1), approval["id"]))
    queued = queue(inbox, actor="bot:ops", raw=mime(to=["ben@acme.example"], Bcc="cara@acme.example"), approval_id=approval["id"])
    assert queued["decision"]["approved"] and queued["state"] == "pending"


@pytest.mark.parametrize("synced,cap,allowed", [(False, 2, False), (True, 2, False), (True, 3, True)])
def test_retained_daily_cap_counts_the_union_of_history_and_queue(inbox, synced, cap, allowed):
    autonomous(inbox)
    registry(inbox, defaults={"max_sends_per_day": cap, "per_recipient_cooldown_days": 0})
    with inbox.app.state.store.transaction() as c:
        incoming(c, sender=BOX, mid="pre-cutover")
        c.execute("UPDATE mail_messages SET labels='[\"SENT\"]',epoch=? WHERE msg_id='pre-cutover'", (H.parse_ts(H.now()).timestamp(),))
    first = queue(inbox, raw=mime(body="First new send"))
    machine = runner(inbox)
    assert claim(inbox, machine["token"])["id"] == first["id"]
    assert post(inbox, "connectors/email/sends/" + first["id"] + "/dispatch", {"mailbox": BOX}, token=machine["token"])["send"]
    post(inbox, "connectors/email/sends/" + first["id"] + "/result", {"status": "sent", "message": "new-provider"}, token=machine["token"])
    if synced:
        with inbox.app.state.store.transaction() as c:
            incoming(c, sender=BOX, mid="new-provider")
            c.execute("UPDATE mail_messages SET labels='[\"SENT\"]',epoch=? WHERE msg_id='new-provider'", (H.parse_ts(H.now()).timestamp(),))
    third = queue(inbox, raw=mime(body="Third overall send"))
    leased = claim(inbox, machine["token"])
    assert bool(leased) is allowed
    if leased:
        assert leased["id"] == third["id"]
        assert post(inbox, "connectors/email/sends/" + third["id"] + "/dispatch", {"mailbox": BOX}, token=machine["token"])["send"]


@pytest.mark.parametrize("scenario", ["cancel_twice", "conference_twice", "conference_failure_twice", "trusted_duplicates", "legacy_no_snapshot", "legacy_notes", "legacy_link", "changed_hold", "moved_hold", "unrelated", "edit_create_failure", "edit_create_pending", "edit_reuse_failure", "edit_reuse_ready"])
def test_standing_booking_retries_reuse_one_verified_silent_hold(inbox, scenario):
    from contextlib import nullcontext
    from connectors.mail import __main__ as cli, scheduling, calendar as cal
    from connectors.mail.tests.fake import FakeCalendarService
    message = {"id": "inbound", "from": CONTACT, "to": [BOX], "cc": [], "labels": [], "epoch": 1,
               "subject": "Meeting", "headers": {"message-id": "<inbound@vendor.example>"}}
    args = SimpleNamespace(thread="thread", category="influencer", bd_confirmation="", mode="book",
                           slot="2030-01-08T10:00:00-08:00", acceptance_message="inbound")
    service = FakeCalendarService(calendars=["primary"])
    calendar = cal.Calendar(service, BOX)
    original_query, original_insert, original_exec, original_patch = service.query, service.insert, calendar._exec, service.patch
    conference_requests, polls = [], []
    def execute(request, what):
        if scenario == "conference_twice" and what == "events.get conference":
            raise cal.Failure("Transient conference read failure", status=503)
        if scenario.startswith("edit_") and what == "events.get conference":
            polls.append(what)
            # The original insert snapshot is persisted before any poll, including failure paths.
            receipt = json.loads(connection.execute("SELECT result FROM scheduling_actions WHERE key NOT LIKE 'retired:%'").fetchone()[0])
            assert receipt["hold_version"] == 1 and receipt["hold"]["summary"] == "Meeting"
            if scenario.startswith("edit_reuse") and len(polls) == 1:
                raise cal.Failure("Transient conference read failure", status=503)
            event = next(iter(service.created_events.values()))
            if scenario.endswith("pending"):
                event["conferenceData"]["notes"] = "User added conference notes"
            else:
                event["summary"] = "User changed the meeting title"
                event["attendees"].append({"email": "other@vendor.example"})
            event["etag"] = '"user-edited"'
            event["conferenceData"]["createRequest"]["status"] = {"statusCode": "success" if scenario.endswith("ready") else "failure" if scenario.endswith("failure") else "pending"}
            if scenario.endswith("ready"):
                event["hangoutLink"] = "https://meet.google.com/abc-defg-hij"
            return copy.deepcopy(event)
        return copy.deepcopy(original_exec(request, what))
    def query(body=None):
        service.busy = [(e["start"]["dateTime"], e["end"]["dateTime"]) for e in service.events_by_calendar.get("primary", [])]
        return original_query(body=body)
    def insert(**kwargs):
        request = original_insert(**kwargs)
        execute = request.execute
        def pending():
            event = execute()
            event.pop("hangoutLink", None)
            event["conferenceData"]["createRequest"]["status"] = {"statusCode": "failure" if scenario == "conference_failure_twice" else "pending"}
            conference_requests.append(event["conferenceData"]["createRequest"]["requestId"])
            return event
        if scenario in ("conference_twice", "conference_failure_twice") or scenario.startswith("edit_"):
            request.execute = pending
        return request
    def retry_conference(**kwargs):
        request = original_patch(**kwargs)
        execute = request.execute
        def finish():
            event = execute()
            conference_requests.append(event["conferenceData"]["createRequest"]["requestId"])
            if len(conference_requests) < 3:
                event.pop("hangoutLink", None)
                event["conferenceData"]["createRequest"]["status"] = {"statusCode": "failure"}
            return event
        request.execute = finish
        return request
    with closing(sqlite3.connect(":memory:")) as connection:
        ctx = SimpleNamespace(conn=connection, mailbox=BOX, dry=False, calendar=calendar, gmail=mock.MagicMock(), audit=mock.MagicMock(),
                              policy={"defaults": {"max_sends_per_day": 20}})
        def submit(mailbox, raw, thread_id, reply_id):
            return queue(inbox, actor="bot:ops", raw=raw, thread_id=thread_id, reply_to_id=reply_id)
        def read(path):
            response = inbox.get("/api/v2/" + path, headers=headers())
            assert response.status_code == 200, response.text
            return response.json()
        with mock.patch.object(cli, "thread_context", return_value=([message], "", "", "", [])), \
                mock.patch.object(scheduling, "permission"), mock.patch.object(scheduling, "review_thread", return_value={}), \
                mock.patch.object(scheduling, "mailbox_lock", side_effect=lambda _: nullcontext()), \
                mock.patch.object(scheduling.pl, "queue_send", side_effect=submit) as enqueue, \
                mock.patch.object(scheduling.pl, "HUB_GET", side_effect=read), \
                mock.patch.object(service, "query", side_effect=query), mock.patch.object(service, "insert", side_effect=insert), \
                mock.patch.object(service, "patch", side_effect=retry_conference), \
                mock.patch.object(calendar, "_exec", side_effect=execute), mock.patch.object(cal.time, "sleep"):
            first = scheduling.run(ctx, args)
            if scenario.startswith("edit_"):
                result = scheduling.run(ctx, args) if scenario.startswith("edit_reuse") else first
                assert result["retry"] and not result["queued"] and "Review the calendar hold" in result["reason"]
                assert result["hold"] == first["hold"] and result["hold"]["summary"] == "Meeting"
                assert result["hold"]["attendees"] == [{"email": CONTACT}]
                assert "notes" not in result["hold"]["conferenceData"]
                with pytest.raises(scheduling.Refused, match="Review the calendar hold"):
                    scheduling.run(ctx, args)
                enqueue.assert_not_called()
                assert not service.patch.called and not service.deleted and service.insert.call_count == 1
                return
            if scenario in ("conference_twice", "conference_failure_twice"):
                assert first["retry"] and not first["queued"]
                second = scheduling.run(ctx, args)
                assert second["retry"] and not second["queued"]
                enqueue.assert_not_called()
                if scenario == "conference_twice":
                    event = next(iter(service.created_events.values()))
                    event["hangoutLink"] = "https://meet.google.com/abc-defg-hij"
                    event["conferenceData"]["createRequest"]["status"] = {"statusCode": "success"}
            else:
                post(inbox, "email/sends/" + first["send_id"] + "/cancel", {})
                if scenario.startswith("legacy_"):
                    receipt = json.loads(connection.execute("SELECT result FROM scheduling_actions").fetchone()[0])
                    if scenario == "legacy_no_snapshot":
                        receipt.pop("hold")
                    receipt.pop("hold_version")
                    connection.execute("UPDATE scheduling_actions SET result=?", (json.dumps(receipt),))
                    event = service.created_events[first["event"]]
                    if scenario == "legacy_notes":
                        event["conferenceData"]["notes"] = "User added dial-in instructions; preserve this meeting."
                    elif scenario == "legacy_link":
                        event["hangoutLink"] = "https://meet.google.com/user-link-url"
                    if scenario != "legacy_no_snapshot":
                        snapshots = []
                        duplicate = calendar.create_scheduling_event(scheduling.datetime.fromisoformat(args.slot),
                            scheduling.datetime.fromisoformat(args.slot)+scheduling.DURATION, CONTACT, "newer-hold", receipt["body"], on_insert=snapshots.append)
                        newer = {**receipt, "event": duplicate["id"], "expected_event_id": duplicate["id"],
                                 "hold": snapshots[0], "hold_version": 1, "state": "canceled"}
                        connection.execute("INSERT INTO scheduling_actions VALUES (?,?,?,?,?,?,?)",
                            ("retired:newer", BOX, "thread", "book", "2030-01-01", "canceled", json.dumps(newer)))
                    with pytest.raises(scheduling.Refused, match="Review the calendar hold"):
                        scheduling.run(ctx, args)
                    assert enqueue.call_count == 1 and not service.deleted and not service.patch.called
                    assert len(service.created_events) == (1 if scenario == "legacy_no_snapshot" else 2)
                    return
                if scenario in ("changed_hold", "moved_hold", "unrelated"):
                    event = service.created_events[first["event"]]
                    if scenario == "changed_hold":
                        event["summary"] = "A person changed this meeting"
                    elif scenario == "moved_hold":
                        event["start"]["dateTime"] = "2030-01-08T10:15:00-08:00"
                    else:
                        service.events_by_calendar["primary"].append({**event, "id": "unrelated"})
                    with pytest.raises(scheduling.Refused, match="calendar blocks" if scenario == "unrelated" else "Review the calendar hold"):
                        scheduling.run(ctx, args)
                    assert enqueue.call_count == 1 and not service.deleted
                    return
                if scenario == "trusted_duplicates":
                    # Every verified original hold is excluded; the newest is reused and none is deleted.
                    receipt = json.loads(connection.execute("SELECT result FROM scheduling_actions").fetchone()[0])
                    snapshots = []
                    duplicate = calendar.create_scheduling_event(scheduling.datetime.fromisoformat(args.slot),
                        scheduling.datetime.fromisoformat(args.slot)+scheduling.DURATION, CONTACT, "older-duplicate", receipt["body"], on_insert=snapshots.append)
                    receipt.update(event=duplicate["id"], expected_event_id=duplicate["id"], state="canceled", queued=False, hold=snapshots[0])
                    connection.execute("INSERT INTO scheduling_actions VALUES (?,?,?,?,?,?,?)",
                        ("retired:old", BOX, "thread", "book", "2030-01-01", "canceled", json.dumps(receipt)))
                    connection.commit()
                second = scheduling.run(ctx, args)
                assert second["queued"] and second["send_id"] != first["send_id"]
                post(inbox, "email/sends/" + second["send_id"] + "/cancel", {})
            third = scheduling.run(ctx, args)
            assert third["queued"] and third["state"] == "waiting_approval"
            assert first["expected_event_id"] == second["expected_event_id"] == third["expected_event_id"] or scenario == "trusted_duplicates"
            assert second["hold"] == third["hold"]
            assert len(service.created_events) == len(service.events_by_calendar["primary"]) == (2 if scenario == "trusted_duplicates" else 1)
            assert all(event["sendUpdates"] == "none" for event in service.created_events.values() if "sendUpdates" in event)
            assert not service.deleted
            assert service.insert.call_count == (2 if scenario == "trusted_duplicates" else 1)
            if scenario == "conference_failure_twice":
                assert service.patch.call_count == 2 and len(set(conference_requests)) == 3
            assert scheduling.run(ctx, args)["send_id"] == third["send_id"]


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


def test_rehearsal_keeps_restrictions_but_discards_send_permits_and_proofs(inbox, tmp_path):
    from backend import devcopy
    policy(inbox, mode="ask_first")
    pending = queue(inbox, operation_id="rehearsal-source")
    assert inbox.get("/api/v2/email/sends/" + pending["id"], headers=headers()).status_code == 200
    with inbox.app.state.store.transaction() as c:
        c.execute("INSERT INTO email_suppressed_recipients VALUES(?,?,?,?)", (BOX, CONTACT, "unsubscribe", H.now()))
        c.execute("INSERT INTO email_legacy_automations VALUES('source-authorization','ana',1)")
    dest = tmp_path / "rehearsal" / "hub.sqlite"
    _, unclassified = devcopy.build(inbox.app.state.store.settings.db_path, dest)
    assert not unclassified
    with closing(sqlite3.connect(dest)) as c:
        for table in ("email_sends", "email_send_operations", "email_send_previews", "email_legacy_automations"):
            assert c.execute("SELECT count(*) FROM " + table).fetchone()[0] == 0
        assert c.execute("SELECT scope FROM email_sending_policies").fetchall() == [("company",)]
        assert c.execute("SELECT mailbox,address FROM email_suppressed_recipients").fetchall() == [(BOX, CONTACT)]


@pytest.mark.parametrize("html", [
    '<style>p::before { content: "We agree to pay $100000"; }</style><p>Thanks.</p>',
    '<p style="content: \'We agree to pay $100000\'">Thanks.</p>',
    '<link rel="stylesheet" href="https://assets.acme.example/contract.css"><p>Thanks.</p>',
    '<!--[if mso]><p>We agree to pay $100000.</p><![endif]--><p>Thanks.</p>',
    '<input value="We agree to pay $100000"><p>Thanks.</p>',
])
@pytest.mark.parametrize("prompt_legal", [False, True])
def test_render_dependent_body_is_previewed_but_cannot_be_classified_as_harmless(inbox, html, prompt_legal):
    autonomous(inbox, ask_legal_money=prompt_legal)
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = BOX, CONTACT, "Update"
    msg.set_content(html, subtype="html")
    frozen = S.snapshot(raw(msg), BOX)
    assert not frozen["classification_complete"]
    assert frozen["visible_bodies"][0]["text"].strip() == html
    preview = "\n".join(body["text"] for body in frozen["visible_bodies"])
    assert "Thanks." in preview
    assert "$100000" in preview or "contract.css" in preview
    with mock.patch.object(judge, "ask") as ask:
        queued = api_post(inbox, "email/sends", {"mailbox": BOX, "raw": raw(msg)})["send"]
    ask.assert_not_called()
    assert queued["state"] == "waiting_approval"
    assert queued["classification"]["status"] == "incomplete_content"
    shown = inbox.get("/api/v2/email/sends/" + queued["id"], headers=headers()).json()
    assert shown["preview_complete"] and shown["bodies"] == frozen["visible_bodies"]


@pytest.mark.parametrize("body,status", [("Thanks.", "unavailable"), ("a" * 16001 + "Pay $100000", "incomplete_content")])
def test_unknown_classification_needs_approval_even_when_the_legal_prompt_is_off(inbox, body, status):
    autonomous(inbox, ask_legal_money=False)
    with mock.patch.object(judge, "server_engine", return_value=None):
        queued = api_post(inbox, "email/sends", {"mailbox": BOX, "raw": mime(body=body)})["send"]
    assert queued["state"] == "waiting_approval" and queued["classification"]["status"] == status
