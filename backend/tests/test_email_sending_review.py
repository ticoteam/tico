"""Regressions for the twelve send-policy review findings, through real authorization and frozen MIME."""

import base64
import copy
import json
import sqlite3
from contextlib import closing
from email.message import EmailMessage
from types import SimpleNamespace

import pytest

from backend import task_asks
from backend.store import Problem
from backend.tests.helpers.api import headers, post, runner, setup_attempt
from backend.tests.helpers.fake_gmail import fake_gmail  # noqa: F401
from backend.tests.helpers.email_sending import (
    BOX,
    CONTACT,
    H,
    S,
    autonomous,
    dispatch,
    inbox,
    incoming,
    mime,
    policy,
    queue,
)
from backend.tests.helpers.judge import fake


def approved(api, queued, token="ana-test", expected=200):
    """The person opens the frozen email in Tico and clicks OK on its preview (backend/task_asks.py)."""
    preview = api.get("/api/v2/email/sends/" + queued["id"], headers=headers(token))
    assert preview.status_code == 200
    with api.app.state.store.read() as c:
        task = task_asks.task_of(c, queued["approval_id"])
    return post(
        api,
        f"tasks/{task}/asks/{queued['approval_id']}/ok",
        {"shown_hash": preview.json()["shown_hash"]},
        token=token,
        expected=expected,
    )


def raw(message):
    return base64.urlsafe_b64encode(message.as_bytes()).decode()


@pytest.mark.parametrize("form", ["alternative", "inline_text", "primary_text", "primary_unknown", "primary_multipart"])
def test_unsupported_recipient_visible_bodies_always_ask(inbox, form):
    autonomous(inbox, ask_attachments=False, ask_legal_money=False)
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
    engine = fake(inbox)
    response = post(inbox, "email/sends", {"mailbox": BOX, "raw": raw(message)})["send"]
    assert engine.calls == []
    assert response["classification"]["legal_or_money"] is None
    assert response["state"] == "waiting_approval"
    preview = inbox.get("/api/v2/email/sends/" + response["id"], headers=headers()).json()
    assert preview["preview_complete"] and preview["bodies"]


@pytest.mark.parametrize(
    "form", ["inline_enriched", "no_disposition", "inline_html", "primary_plain", "named_container"]
)
def test_named_inline_bodies_cannot_hide_commitments_from_classification(inbox, form):
    autonomous(inbox, ask_attachments=False, ask_legal_money=True)
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = BOX, CONTACT, "Named body " + form
    commitment = "We accept the contract and agree to pay $100000."
    message.set_content(commitment if form == "primary_plain" else "Thanks for the update.")
    if form != "primary_plain":
        message.add_alternative(
            "<p>" + commitment + "</p>", subtype="html" if form in ("inline_html", "named_container") else "enriched"
        )
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

    engine = fake(inbox, legal_or_money="yes")
    result = post(inbox, "email/sends", {"mailbox": BOX, "raw": raw(message)})["send"]
    assert len(engine.calls) == int(supported)
    if supported:
        assert commitment in engine.calls[0]["state"]["body"]
    assert result["classification"]["status"] == ("classified" if supported else "incomplete_content")
    assert result["state"] == "waiting_approval" and dispatch(inbox) == []


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
    engine = fake(inbox, legal_or_money="no")
    result = post(inbox, "email/sends", {"mailbox": BOX, "raw": encoded})["send"]
    if subtype != "enriched":
        assert "Harmless notes" in engine.calls[0]["state"]["body"]
        assert "attachments" in result["decision"]["reasons"]
    else:
        assert engine.calls == []
        assert not snap["classification_complete"]
    assert result["state"] == "waiting_approval" and dispatch(inbox) == []
    response = inbox.get(f'/api/v2/email/sends/{result["id"]}/attachments/{file["part"]}', headers=headers())
    assert response.status_code == 200 and response.content == expected
    message.get_payload()[-1].set_payload("Changed notes")
    changed = S.snapshot(raw(message), BOX)
    assert changed["attachments"][0]["sha256"] != file["sha256"] and changed["content_sha256"] != snap["content_sha256"]


def test_an_ok_of_one_copy_cannot_authorize_an_added_internal_bcc(inbox):
    original = mime(to=["ben@acme.example"], body="Sensitive legal reply")
    first = queue(inbox, actor="bot:ops", raw=original)
    approved(inbox, first)
    from backend.email_content import parsed

    expanded = parsed(original)
    expanded["Bcc"] = "cara@acme.example"
    queued = queue(inbox, actor="bot:ops", raw=raw(expanded), approval_id=first["approval_id"])
    assert queued["state"] == "waiting_approval" and not queued["decision"]["approved"]
    assert queued["approval_id"] != first["approval_id"]
    with inbox.app.state.store.read() as c:
        asked = task_asks.grant(c, queued["approval_id"])["content"]
    assert asked["recipients"] == ["ben@acme.example", "cara@acme.example"]


def test_negative_chat_cannot_approve_a_different_large_send_through_the_api(inbox):
    fake(inbox, legal_or_money="yes")
    _, _, attempt = setup_attempt(inbox, "ops")
    note = post(inbox, "chat/ops", {"text": "Do not send any email to outsiders."})
    result = post(
        inbox,
        "email/sends",
        {
            "mailbox": BOX,
            "raw": mime(
                to=[f"person{i}@vendor.example" for i in range(11)], files=True, body="We agree to pay $100000."
            ),
            "approval_id": note["id"],
        },
        token=attempt["token"],
    )["send"]
    assert result["state"] == "waiting_approval" and not result["decision"]["approved"]
    assert result["approval_id"] != note["id"]


def test_company_recipient_message_type_and_approver_cannot_be_loosened(inbox):
    autonomous(inbox, to="internal", what="replies_only", who_can_ok="inbox_admins")
    with inbox.app.state.store.transaction() as c:
        who = inbox.app.state.auth.identity_for_actor(c, "human:ana")
        for scope in ("inbox:" + BOX, "bot:ops"):
            S.update_policy(
                c, who, scope, S.PolicyUpdate(to="anyone", what="replies_and_new", who_can_ok="bot_managers")
            )
        found = S.effective(c, BOX, "ops")
        assert (found["to"], found["what"], found["who_can_ok"]) == ("internal", "replies_only", "inbox_admins")
        assert not S.can_ok(c, inbox.app.state.auth, "human:cara", BOX, "ops", found)
    queued = queue(inbox, actor="bot:ops")
    assert queued["state"] == "blocked" and "internal_only" in queued["decision"]["reasons"]


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


def test_classifier_sees_html_as_well_as_plain_text_and_never_truncates_to_a_no(inbox):
    engine = fake(inbox, legal_or_money="yes")
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = BOX, CONTACT, "Update"
    msg.set_content("Thanks for the update.")
    msg.add_alternative("<p>We accept the contract and agree to pay $100000.</p><script>bad()</script>", subtype="html")
    result = S.classify(inbox.app, inbox.app.state.store, S.snapshot(raw(msg), BOX))
    seen = engine.calls[-1]["state"]["body"]
    assert result["status"] == "classified" and "$100000" in seen and "Thanks" in seen
    assert "<p>" not in seen and "bad()" not in seen
    inline = EmailMessage()
    inline.set_content("<p>Also agree to pay $500000.</p>", subtype="html")
    inline["Content-Disposition"] = "inline"
    msg.make_mixed()
    msg.attach(inline)
    S.classify(inbox.app, inbox.app.state.store, S.snapshot(raw(msg), BOX))
    assert "$500000" in engine.calls[-1]["state"]["body"]
    asked = len(engine.calls)
    result = S.classify(inbox.app, inbox.app.state.store, S.snapshot(mime(body="a" * 16001 + "Pay $100000"), BOX))
    assert result["legal_or_money"] is None and len(engine.calls) == asked


def test_the_classifier_reads_the_choice_the_judge_answers(inbox):
    # The judge answers a choice question with `choice` (clients/judge.py `_project`); only the audit says `value`.
    fake(inbox, legal_or_money="yes")
    snap = S.snapshot(mime(body="We agree to pay $100000."), BOX)
    assert S.classify(inbox.app, inbox.app.state.store, snap)["legal_or_money"] is True
    fake(inbox, legal_or_money="no", review="send")
    second = {"thread": None, "approver_instruction": None}
    result = S.classify(inbox.app, inbox.app.state.store, snap, review=second)
    assert result["legal_or_money"] is False and result["review"] is True
    fake(inbox, legal_or_money="no", review="stop")
    assert S.classify(inbox.app, inbox.app.state.store, snap, review=second)["review"] is False


def test_a_stop_always_says_why_in_words(inbox):
    """The decision model writes no prose: the reason is a second choice in the same call, and a stop that names none
    of them, or a review that gave no verdict, says so and what to do. A send carries no reason."""
    from clients.judge import JudgeError

    snap = S.snapshot(mime(body="We will refund the full $400 by Friday."), BOX)
    second = {"thread": "", "approver_instruction": ""}

    def review(**choices):
        engine = fake(inbox, legal_or_money="yes", **choices)
        return S.classify(inbox.app, inbox.app.state.store, snap, review=second), engine

    found, engine = review(review="stop", review_reason="commitment")
    assert (found["review"], found["review_reason"]) == (False, S.STOPPED["commitment"])
    (call,) = engine.calls
    assert set(call["questions"]) == {"legal_or_money", "review", "review_reason"} and found["questions"] == 3
    assert set(call["questions"]["review_reason"]["criteria"]) == {*S.STOPPED, "none"}
    assert call["state"]["attachments"] == "(no files attached)"
    for named in ("none", None):  # the reviewer stopped it but named none of the reasons
        found, _ = review(review="stop", **({"review_reason": named} if named else {}))
        assert found["review"] is False and found["review_reason"] == S.NO_REASON
    found, _ = review(review="send", review_reason="commitment")
    assert (found["review"], found["review_reason"]) == (True, "")
    found, engine = review(review="maybe")  # an answer that is not one of the options is no verdict
    assert found["review"] is None and found["review_reason"] == S.UNANSWERED["unavailable"]
    engine.fail = JudgeError("unavailable", "down")
    found = S.classify(inbox.app, inbox.app.state.store, snap, review=second)
    assert (found["status"], found["review"], found["review_reason"]) == (
        "unavailable",
        None,
        S.UNANSWERED["unavailable"],
    )
    whole = S.snapshot(mime(body="a" * 16001), BOX)
    found = S.classify(inbox.app, inbox.app.state.store, whole, review=second)
    assert found["review"] is None and found["review_reason"] == S.UNANSWERED["incomplete_content"]
    # A person's own email has no second reviewer, so no reason either.
    assert "review_reason" not in S.classify(inbox.app, inbox.app.state.store, snap)


@pytest.mark.parametrize(
    "body,files,stopped",
    [
        ("Please find attached the records you asked for.", False, True),
        ("The signed declaration is enclosed.", False, True),
        ("Attachments: records.zip", False, True),
        ("Please find attached the records you asked for.", True, False),
        ("The files are not attached yet; they follow tomorrow.", False, False),
        ("Thanks, see you Monday.\n\n> Please send the attached form back.", False, False),
        (
            "Thanks, will do.\n\nOn Mon, 16 Nov 2026 at 09:00, Dana <dana@vendor.example> wrote:\nForm attached.",
            False,
            False,
        ),
    ],
)
def test_an_email_that_says_files_are_attached_and_has_none_is_stopped_for_that(inbox, body, files, stopped):
    """Whatever the model answers: a reviewed email whose own words say files are attached, with none attached, is
    stopped with exactly that reason. Quoted lines and a negation are not a claim."""
    fake(inbox, legal_or_money="no", review="send", review_reason="none")
    snap = S.snapshot(mime(body=body, files=files), BOX)
    assert S.says_attached_without_files(snap) is stopped
    found = S.classify(inbox.app, inbox.app.state.store, snap, review={"thread": "", "approver_instruction": ""})
    assert found["review"] is (not stopped)
    assert found["review_reason"] == (S.STOPPED["missing_file"] if stopped else "")
    if files:
        assert inbox.app.state.judge.calls[-1]["state"]["attachments"] == ["proof.pdf"]
    # With no decision model at all, the deterministic check still says it.
    inbox.app.state.judge.fail = __import__("clients.judge", fromlist=["JudgeError"]).JudgeError("unavailable", "x")
    found = S.classify(inbox.app, inbox.app.state.store, snap, review={"thread": "", "approver_instruction": ""})
    assert found["review_reason"] == (S.STOPPED["missing_file"] if stopped else S.UNANSWERED["unavailable"])


def test_a_queued_send_keeps_the_reviewers_reason_for_the_person_it_asks(inbox):
    autonomous(inbox, second_review=True)
    fake(inbox, legal_or_money="no", review="stop", review_reason="confirmed_time")
    _, _, attempt = setup_attempt(inbox, "ops")
    message = {"mailbox": BOX, "raw": mime(body="Confirmed: we meet Tuesday at 3pm.")}
    queued = post(inbox, "email/sends", message, token=attempt["token"])["send"]
    assert queued["state"] == "waiting_approval" and "second_review" in queued["decision"]["reasons"]
    assert queued["classification"]["review_reason"] == S.STOPPED["confirmed_time"]
    preview = inbox.get("/api/v2/email/sends/" + queued["id"], headers=headers("ana-test")).json()
    assert preview["send"]["classification"]["review_reason"] == S.STOPPED["confirmed_time"]


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


def test_an_older_computers_abandoned_lease_is_recovered_and_its_old_lease_fenced(inbox):
    """An unpermitted lease an older computer held is safe to send again (no permit, nothing reached Gmail); a permitted
    one that never reported is unknown, never sent again."""
    autonomous(inbox)
    first, second = queue(inbox), queue(inbox, raw=mime(body="Second answer"))
    machine = runner(inbox)
    stale = H.shift(H.now(), minutes=-6)
    with inbox.app.state.store.transaction() as c:
        c.execute(
            "UPDATE outbound_actions SET state='attempting',metadata=json_set(metadata,'$.runner_id',?),claim_token='old',claimed_at=? WHERE id=?",
            (machine["runner_id"], stale, first["id"]),
        )
        c.execute(
            "UPDATE outbound_actions SET state='attempting',metadata=json_set(metadata,'$.runner_id',?),claim_token='permitted',claimed_at=?,"
            "provider_attempted_at=? WHERE id=?",
            (machine["runner_id"], stale, stale, second["id"]),
        )
    assert len(dispatch(inbox) + dispatch(inbox)) == 1  # recovered on one pass, sent on the next
    path = "connectors/email/sends/" + first["id"] + "/dispatch"
    post(inbox, path, {"mailbox": BOX, "lease_id": "old"}, token=machine["token"], expected=405)  # no route
    with inbox.app.state.store.read() as c:
        rows = {r["id"]: r for r in c.execute("SELECT * FROM outbound_email").fetchall()}
    assert rows[first["id"]]["state"] == "sent"
    assert rows[second["id"]]["state"] == "unknown" and not rows[second["id"]]["raw"]


def test_cutover_reports_uncertain_delivery_once_and_withdraws_an_unused_ok(inbox):
    queued = queue(inbox)
    approved(inbox, queued)
    with inbox.app.state.store.transaction() as c:
        c.execute(
            "UPDATE outbound_actions SET state='unknown',payload='',last_error=?,"
            "metadata=json_set(metadata,'$.cutover_unconfirmed',1) WHERE id=?",
            (S.UNCONFIRMED, queued["id"]),
        )
    assert dispatch(inbox) == dispatch(inbox) == []
    with inbox.app.state.store.read() as c:
        assert task_asks.standing(c, queued["approval_id"]) == task_asks.WITHDRAWN
        assert (
            c.execute(
                "SELECT count(*) FROM events WHERE action='email.unknown' AND target=?", (queued["id"],)
            ).fetchone()[0]
            == 1
        )


def test_one_hundred_waiting_approvals_do_not_starve_a_due_send(inbox):
    autonomous(inbox)
    with inbox.app.state.store.transaction() as c:
        S.update_policy(
            c, inbox.app.state.auth.identity_for_actor(c, "human:ana"), "bot:ops", S.PolicyUpdate(mode="ask_first")
        )
    for index in range(100):
        queue(inbox, actor="bot:ops", raw=mime(body="Unapproved " + str(index)))
    due = queue(inbox, raw=mime(body="Authorized human send"))
    assert len(dispatch(inbox)) == 1
    with inbox.app.state.store.read() as c:
        assert c.execute("SELECT state FROM outbound_email WHERE id=?", (due["id"],)).fetchone()[0] == "sent"


def test_approver_must_load_authorized_frozen_preview_and_download_exact_attachment(inbox):
    policy(inbox, who_can_ok="named_people", approvers=["cara"])
    queued = queue(inbox, actor="bot:ops", raw=mime(body="<script>unsafe</script>", files=True))
    with inbox.app.state.store.read() as c:
        task = task_asks.task_of(c, queued["approval_id"])
        unseen = task_asks.content_hash(
            S.content(c.execute("SELECT * FROM outbound_email WHERE id=?", (queued["id"],)).fetchone())
        )
    # Knowing what it says is not having seen it: the OK needs the preview the server showed Cara.
    post(
        inbox, f"tasks/{task}/asks/{queued['approval_id']}/ok", {"shown_hash": unseen}, token="cara-test", expected=409
    )
    assert inbox.get("/api/v2/email/sends/" + queued["id"], headers=headers("ben-test")).status_code == 403
    preview = inbox.get("/api/v2/email/sends/" + queued["id"], headers=headers("cara-test")).json()
    assert preview["preview_complete"] and preview["body"] == "<script>unsafe</script>\n"
    file = preview["send"]["attachments"][0]
    path = f"/api/v2/email/sends/{queued['id']}/attachments/{file['part']}"
    assert inbox.get(path, headers=headers("ben-test")).status_code == 403
    response = inbox.get(path, headers=headers("cara-test"))
    assert response.content == b"proof" and S.digest(response.content) == file["sha256"]
    ok = {"shown_hash": preview["shown_hash"]}
    post(inbox, f"tasks/{task}/asks/{queued['approval_id']}/ok", ok, token="cara-test")
    with inbox.app.state.store.read() as c:
        assert task_asks.standing(c, queued["approval_id"]) == task_asks.OK


def test_the_preview_names_to_cc_and_bcc_apart_and_the_ok_carries_its_answer_to_undo(inbox):
    # The approver must see who is only copied or blind-copied, not one merged list, and the OK must be undoable.
    queued = queue(inbox, actor="bot:ops", raw=mime(Cc="ben@acme.example", Bcc="cara@acme.example"))
    preview = inbox.get("/api/v2/email/sends/" + queued["id"], headers=headers("ana-test")).json()
    assert (preview["to"], preview["cc"], preview["bcc"]) == ([CONTACT], ["ben@acme.example"], ["cara@acme.example"])
    assert preview["send"]["recipients"] == sorted([CONTACT, "ben@acme.example", "cara@acme.example"])
    given = approved(inbox, queued)
    with inbox.app.state.store.read() as c:
        stored = task_asks.grant(c, queued["approval_id"])["answer_id"]
    assert given["answer_id"] and given["answer_id"] == stored
    # Clicked again, the OK already given answers with the same id.
    assert approved(inbox, queued)["answer_id"] == stored


@pytest.mark.parametrize("state", ["canceled", "blocked", "failed"])
def test_new_intent_can_retry_a_definite_non_send_but_same_operation_cannot(inbox, state):
    autonomous(inbox)
    first = queue(inbox, operation_id="first-operation")
    with inbox.app.state.store.transaction() as c:
        c.execute("UPDATE outbound_actions SET state=?,payload='' WHERE id=?", (state, first["id"]))
    assert queue(inbox, operation_id="first-operation")["id"] == first["id"]
    new = queue(inbox, operation_id="new-operation")
    assert new["id"] != first["id"] and new["state"] == "pending"
    assert queue(inbox, operation_id="retry-of-active")["id"] == new["id"]


def test_sent_or_unknown_delivery_remains_deduplicated_for_a_new_operation(inbox):
    autonomous(inbox)
    first = queue(inbox)
    with inbox.app.state.store.transaction() as c:
        c.execute("UPDATE outbound_actions SET state='unknown',payload='' WHERE id=?", (first["id"],))
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
        for table in (
            "outbound_actions",
            "email_sends",
            "email_send_operations",
            "email_send_previews",
            "email_legacy_automations",
        ):
            assert c.execute("SELECT count(*) FROM " + table).fetchone()[0] == 0
        assert c.execute("SELECT scope FROM email_sending_policies").fetchall() == [("company",)]
        assert c.execute("SELECT mailbox,address FROM email_suppressed_recipients").fetchall() == [(BOX, CONTACT)]


@pytest.mark.parametrize(
    "html",
    [
        '<style>p::before { content: "We agree to pay $100000"; }</style><p>Thanks.</p>',
        '<p style="content: \'We agree to pay $100000\'">Thanks.</p>',
        '<link rel="stylesheet" href="https://assets.acme.example/contract.css"><p>Thanks.</p>',
        '<!--[if mso]><p>We agree to pay $100000.</p><![endif]--><p>Thanks.</p>',
        '<input value="We agree to pay $100000"><p>Thanks.</p>',
    ],
)
def test_render_dependent_body_is_previewed_but_cannot_be_classified_as_harmless(inbox, html):
    autonomous(inbox, ask_legal_money=False)
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = BOX, CONTACT, "Update"
    msg.set_content(html, subtype="html")
    frozen = S.snapshot(raw(msg), BOX)
    assert not frozen["classification_complete"]
    assert frozen["visible_bodies"][0]["text"].strip() == html
    preview = "\n".join(body["text"] for body in frozen["visible_bodies"])
    assert "Thanks." in preview
    assert "$100000" in preview or "contract.css" in preview
    engine = fake(inbox)
    queued = post(inbox, "email/sends", {"mailbox": BOX, "raw": raw(msg)})["send"]
    assert engine.calls == []
    assert queued["state"] == "waiting_approval"
    assert queued["classification"]["status"] == "incomplete_content"
    shown = inbox.get("/api/v2/email/sends/" + queued["id"], headers=headers()).json()
    assert shown["preview_complete"] and shown["bodies"] == frozen["visible_bodies"]


@pytest.mark.parametrize(
    "body,status", [("Thanks.", "unavailable"), ("a" * 16001 + "Pay $100000", "incomplete_content")]
)
def test_unknown_classification_needs_approval_even_when_the_legal_prompt_is_off(inbox, body, status):
    autonomous(inbox, ask_legal_money=False)
    inbox.app.state.judge = None  # no decision model is configured on this server
    queued = post(inbox, "email/sends", {"mailbox": BOX, "raw": mime(body=body)})["send"]
    assert queued["state"] == "waiting_approval" and queued["classification"]["status"] == status
