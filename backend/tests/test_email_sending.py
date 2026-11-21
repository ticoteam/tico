"""Policy, approval, revocation and idempotency boundaries shared by all email send paths."""

import json
import sqlite3
from pathlib import Path

import pytest

from backend import email_sending as S
from backend import task_asks
from backend.store import H, Problem
from backend.tests.helpers.api import assign, claim, headers, post, ready, runner, setup_attempt  # noqa: F401
from backend.tests.helpers.fake_gmail import fake_gmail  # noqa: F401
from backend.tests.helpers.email_sending import (
    BOX,
    CONTACT,
    autonomous,
    dispatch,
    inbox,
    incoming,
    mime,
    policy,
    queue,
)


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


def test_resolution_default_opt_in_and_unbreakable_restrictions(inbox):
    with inbox.app.state.store.transaction() as c:
        assert S.effective(c)["mode"] == "ask_first"
        assert S.effective(c)["what"] == "replies_only"
        who = inbox.app.state.auth.identity_for_actor(c, "human:ana")
        S.update_policy(c, who, "bot:ops", S.PolicyUpdate(mode="on_their_own", daily_cap=200, ask_new_address=False))
        found = S.effective(c, BOX, "ops")
        assert found["mode"] == "on_their_own" and found["sources"]["mode"] == "bot"
        assert found["daily_cap"] == 50 and found["ask_new_address"] is True
        S.update_policy(c, who, "inbox:" + BOX, S.PolicyUpdate(mode="never"))
        assert S.effective(c, BOX, "ops")["mode"] == "never"
        assert S.effective(c, BOX, "ops")["sources"]["mode"] == "inbox"
        S.update_policy(c, who, "company", S.PolicyUpdate(mode="never"))
        assert S.effective(c, BOX, "ops")["sources"]["mode"] == "company"


@pytest.mark.parametrize(
    "rule,raw,reply,reason",
    [
        ("ask_new_address", mime(to=["new@vendor.example"]), False, "new_address"),
        ("ask_attachments", mime(files=True), False, "attachments"),
        (
            "ask_many_recipients",
            mime(to=[f"person{i}@vendor.example" for i in range(11)]),
            False,
            "more_than_10_recipients",
        ),
        ("ask_legal_money", mime(), False, "legal_or_money_or_unclassified"),
    ],
    ids=["new_address", "attachments", "many_recipients", "legal_money"],
)
def test_each_always_ask_rule_cannot_be_bypassed_by_autonomy(inbox, rule, raw, reply, reason):
    autonomous(inbox, **{rule: True})
    if rule == "ask_legal_money":
        # An unknown Decide result asks a person too.
        with inbox.app.state.store.transaction() as c:
            queued = S.enqueue(
                c, inbox.app.state.auth, "human:ana", S.Enqueue(mailbox=BOX, raw=raw), {"legal_or_money": None}
            )
    else:
        queued = queue(inbox, raw=raw)
    assert queued["state"] == "waiting_approval"
    assert reason in queued["decision"]["reasons"]
    assert queued["approval_id"]


def test_known_correspondent_and_verified_reply_can_send(inbox):
    autonomous(inbox, ask_new_address=True, what="replies_only")
    queued = queue(inbox, reply_to_id="inbound", thread_id="thread")
    assert queued["state"] == "pending"
    assert queued["decision"]["outcome"] == "allow"


def test_a_send_reads_back_the_email_it_answers(inbox):
    """The approval shows the email being answered from send.reply_to_id; a new email reads null, not ""."""
    reply = queue(inbox, reply_to_id="inbound", thread_id="thread")
    fresh = queue(inbox, raw=mime(body="A new email"))
    got = inbox.get("/api/v2/email/sends/" + reply["id"], headers=headers("ana-test")).json()["send"]
    assert (got["reply_to_id"], got["thread_id"]) == ("inbound", "thread")
    got = inbox.get("/api/v2/email/sends/" + fresh["id"], headers=headers("ana-test")).json()["send"]
    assert (got["reply_to_id"], got["thread_id"]) == (None, None)


def test_reply_and_address_checks_ask_gmail_and_wait_while_it_does_not_answer(inbox, fake_gmail):
    from backend import mail_facts
    from backend.store import Problem

    autonomous(inbox, ask_new_address=True)
    known = queue(inbox, raw=mime(body="To a known sender"))
    assert "new_address" not in known["decision"]["reasons"]  # the contact wrote to the inbox (Gmail from:)
    stranger = queue(inbox, raw=mime(body="To a stranger", to=["new@vendor.example"]))
    assert "new_address" in stranger["decision"]["reasons"]
    searches = [q.get("q", [""])[0] for path, q, _ in fake_gmail.calls if path.endswith("/messages")]
    assert "from:" + CONTACT in searches and any(s.startswith("in:sent") for s in searches)
    mail_facts.forget()
    fake_gmail.down.add(BOX)
    with inbox.app.state.store.read() as c:
        before = c.execute("SELECT count(*) FROM email_sends").fetchone()[0]
    with pytest.raises(Problem) as waited:
        queue(inbox, raw=mime(body="A reply", In_Reply_To="<inbound@vendor.example>"), reply_to_id="inbound")
    assert waited.value.retryable
    with inbox.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM email_sends").fetchone()[0] == before


def test_a_gmail_refusal_is_not_remembered_as_a_fact_and_the_send_waits(inbox, fake_gmail, monkeypatch):
    """Gmail answers 403 for a rate limit too, and a token outage refuses: neither is an answer about the address. The
    send waits (retryable), nothing is blocked or cached, and the next try asks Gmail again."""
    from backend import gmail_watch
    from backend.store import Problem

    monkeypatch.setattr(gmail_watch, "watched", lambda c, mailbox: True)
    autonomous(inbox, ask_new_address=True)
    fake_gmail.refused.add(BOX)
    with pytest.raises(Problem) as waited:
        queue(inbox, raw=mime(body="To a known sender"))
    assert waited.value.retryable
    with inbox.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM email_sends WHERE state='blocked'").fetchone()[0] == 0
    fake_gmail.refused.clear()
    assert "new_address" not in queue(inbox, raw=mime(body="To a known sender"))["decision"]["reasons"]


def test_mail_tico_composes_is_queued_while_gmail_is_down_and_decided_when_it_answers(inbox, fake_gmail, monkeypatch):
    """A calendar invitation (or any email Tico composes inside another write) never fails because Gmail cannot say
    who is known: it is queued pending, unchecked, and the drain decides it with Gmail's answer before it can go."""
    from backend import email_sending as S

    autonomous(inbox, ask_new_address=True)
    fake_gmail.down.add(BOX)
    with inbox.app.state.store.transaction() as c:
        queued = S.enqueue(
            c,
            inbox.app.state.auth,
            "human:ana",
            S.Enqueue(mailbox=BOX, raw=mime(body="An invitation", to=["new@vendor.example"]), calendar_action_id=""),
            {"legal_or_money": False, "status": "classified"},
            wait_for_gmail=True,
        )
    assert queued["state"] == "pending" and queued["decision"]["outcome"] == "wait"
    assert "gmail_unchecked" in queued["decision"]["reasons"]
    store, auth = inbox.app.state.store, inbox.app.state.auth
    assert S.drain(store, auth) == 0  # Gmail still down: it waits, nothing is sent or asked
    with store.read() as c:
        assert c.execute("SELECT state FROM email_sends WHERE id=?", (queued["id"],)).fetchone()[0] == "pending"
    fake_gmail.down.clear()
    S.drain(store, auth)
    with store.read() as c:
        row = c.execute("SELECT state,decision FROM email_sends WHERE id=?", (queued["id"],)).fetchone()
    assert row["state"] == "waiting_approval" and "new_address" in json.loads(row["decision"])["reasons"]


def test_cli_sending_controls_share_the_email_command_and_api(inbox, monkeypatch, capsys):
    from backend.tests.helpers.automation_cli import Client
    from clients import hubcli, remotecli

    monkeypatch.setenv("HUB_API_URL", "https://tico.acme.example")
    monkeypatch.setenv("HUB_TOKEN", "ana-test")
    monkeypatch.setattr(remotecli, "Client", lambda url, token, **kwargs: Client(inbox, token))
    assert hubcli.main(["email", "policy", "--settings", '{"mode":"ask_first"}']) == 0
    assert json.loads(capsys.readouterr().out)["effective"]["mode"] == "ask_first"
    queued = queue(inbox)
    assert hubcli.main(["email", "sends", "--mailbox", BOX, "--pending"]) == 0
    assert json.loads(capsys.readouterr().out)["sends"][0]["id"] == queued["id"]
    assert hubcli.main(["email", "cancel", queued["id"]]) == 0
    assert json.loads(capsys.readouterr().out)["send"]["state"] == "canceled"


def test_never_blocks_an_approved_send_at_dispatch(inbox):
    queued = queue(inbox)
    approved(inbox, queued)
    policy(inbox, mode="never")
    assert dispatch(inbox) == []
    with inbox.app.state.store.read() as c:
        row = c.execute("SELECT * FROM email_sends WHERE id=?", (queued["id"],)).fetchone()
        assert row["state"] == "blocked" and "never" in json.loads(row["decision"])["reasons"]


def test_an_ok_from_the_preview_releases_the_frozen_send_and_is_used(inbox):
    policy(inbox, undo_seconds=0, pacing_seconds=0)
    queued = queue(inbox, actor="bot:ops")
    assert queued["state"] == "waiting_approval"
    approved(inbox, queued)
    assert len(dispatch(inbox) + dispatch(inbox)) == 1  # the first pass lets it go, a later one sends it
    with inbox.app.state.store.read() as c:
        assert c.execute("SELECT state FROM email_sends WHERE id=?", (queued["id"],)).fetchone()[0] == "sent"
        assert task_asks.standing(c, queued["approval_id"]) == task_asks.USED


def test_an_approval_id_tico_does_not_know_neither_approves_nor_blocks(inbox):
    # A GitHub issue number, say: under Ask first the email waits for a Tico approval of its own.
    policy(inbox, undo_seconds=0, pacing_seconds=0)
    queued = queue(inbox, actor="bot:ops", approval_id="77")
    assert queued["state"] == "waiting_approval" and not queued["decision"]["approved"]
    assert queued["approval_id"] and queued["approval_id"] != "77"


def test_undo_pending_restart_cancel_and_no_recall(inbox, tmp_path):
    autonomous(inbox, undo_seconds=120)
    queued = queue(inbox)
    assert dispatch(inbox) == []  # inside its undo window
    # A fresh database connection sees the durable queue, without any worker memory.
    with sqlite3.connect(inbox.app.state.store.settings.db_path) as c:
        assert c.execute("SELECT state FROM email_sends WHERE id=?", (queued["id"],)).fetchone()[0] == "pending"
        with sqlite3.connect(tmp_path / "restarted.db") as target:
            c.backup(target)
    from dataclasses import replace
    from fastapi.testclient import TestClient
    from backend.app import create_app

    restarted = create_app(replace(inbox.app.state.store.settings, db_path=tmp_path / "restarted.db"))
    with TestClient(restarted) as client:
        recovered = client.get("/api/v2/email/sends", params={"mailbox": BOX, "pending": True}, headers=headers())
        assert recovered.status_code == 200
        assert recovered.json()["sends"][0]["id"] == queued["id"]
    response = post(inbox, "email/sends/" + queued["id"] + "/cancel", {})
    assert response["send"]["state"] == "canceled"
    assert dispatch(inbox) == []
    with inbox.app.state.store.transaction() as c:
        c.execute("UPDATE email_sends SET state='sent' WHERE id=?", (queued["id"],))
    response = inbox.post("/api/v2/email/sends/" + queued["id"] + "/cancel", json={}, headers=headers())
    assert response.status_code == 409


def test_due_send_is_dispatched_once_with_a_receipt_and_audit(inbox):
    import base64

    autonomous(inbox)
    queued = queue(inbox, raw=mime(files=True))
    (sent,) = dispatch(inbox)
    raw = base64.urlsafe_b64encode(sent).decode()
    assert S.snapshot(raw, BOX)["content_sha256"] == queued["content_sha256"]
    assert dispatch(inbox) == []
    receipt = inbox.get("/api/v2/email/sends/" + queued["id"], headers=headers()).json()["send"]
    assert receipt["state"] == "sent" and receipt["provider_message_id"] == "sent-1"
    assert [{k: f[k] for k in ("name", "sha256")} for f in receipt["attachments"]] == [
        {"name": "proof.pdf", "sha256": S.digest(b"proof")}
    ]


def test_permission_revoked_before_dispatch_sends_nothing(inbox):
    autonomous(inbox)
    queued = queue(inbox, actor="bot:ops")
    with inbox.app.state.store.transaction() as c:
        c.execute("DELETE FROM mailbox_accesses WHERE grantee_type='bot' AND grantee_id='ops'")
    assert dispatch(inbox) == []
    with inbox.app.state.store.read() as c:
        assert c.execute("SELECT state FROM email_sends WHERE id=?", (queued["id"],)).fetchone()[0] != "sent"


def test_external_agent_revocation_blocks_its_pending_send(inbox):
    autonomous(inbox)
    with inbox.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO agents(bot,harness,token_hash,created_at,created_by) VALUES('ops','external','fixture',?,'human:ana')",
            (H.now(),),
        )
        queued = S.enqueue(
            c,
            inbox.app.state.auth,
            "bot:ops",
            S.Enqueue(mailbox=BOX, raw=mime()),
            {"legal_or_money": False},
            via="agent:external",
        )
        c.execute("UPDATE agents SET revoked_at=? WHERE bot='ops'", (H.now(),))
    assert dispatch(inbox) == []
    with inbox.app.state.store.read() as c:
        result = json.loads(c.execute("SELECT decision FROM email_sends WHERE id=?", (queued["id"],)).fetchone()[0])
    assert "agent_revoked" in result["reasons"]


@pytest.mark.parametrize("cap", ["daily_cap", "mailbox_daily_cap", "company_daily_cap"])
def test_caps_reserve_concurrent_dispatches_including_unknown_results(inbox, cap):
    autonomous(inbox, **{cap: 1})
    from backend.tests.helpers.fake_gmail import CURRENT

    first = queue(inbox, raw=mime(body="First"))
    queue(inbox, raw=mime(body="Second"))
    CURRENT["fake"].send_answer = (503, {})  # Gmail never confirms the first: its result is unknown
    assert len(dispatch(inbox)) == 1
    with inbox.app.state.store.read() as c:
        assert c.execute("SELECT state FROM email_sends WHERE id=?", (first["id"],)).fetchone()[0] == "unknown"
    assert dispatch(inbox) == []  # the unknown one still counts against the cap


def test_pacing_duplicate_hash_and_reply_loop_protection(inbox):
    autonomous(inbox, pacing_seconds=60)
    first = queue(inbox, raw=mime(Message_ID="<one@acme.example>"))
    assert queue(inbox, raw=mime(Message_ID="<two@acme.example>"))["id"] == first["id"]
    queue(inbox, raw=mime(body="Second"))
    assert len(dispatch(inbox)) == 1 and dispatch(inbox) == []  # paced: the second waits a minute
    with inbox.app.state.store.transaction() as c:
        incoming(c, facts={"auto-submitted": "auto-replied"}, mid="automatic")
    blocked = queue(
        inbox,
        raw=mime(body="No loop", In_Reply_To="<automatic@vendor.example>"),
        reply_to_id="automatic",
        thread_id="thread",
    )
    assert blocked["state"] == "blocked" and "reply_loop" in blocked["decision"]["reasons"]


def test_bounces_suppress_only_attempted_recipients_and_admin_can_restore(inbox):
    autonomous(inbox)
    queued = queue(inbox)
    with inbox.app.state.store.transaction() as c:
        c.execute("UPDATE email_sends SET state='sent' WHERE id=?", (queued["id"],))
        S.record_bounces(c, BOX, "mailer-daemon@vendor.example", "Final-Recipient: rfc822; " + CONTACT)
    blocked = queue(inbox, raw=mime(body="Another email"))
    assert "bounce_suppression" in blocked["decision"]["reasons"]
    response = inbox.delete(f"/api/v2/email/inboxes/{BOX}/suppressed-recipients/{CONTACT}", headers=headers())
    assert response.status_code == 200
    assert queue(inbox, raw=mime(body="Restored"))["state"] == "pending"


def test_named_approvers_and_private_inbox_boundaries(inbox):
    policy(inbox, who_can_ok="named_people", approvers=["cara"])
    queued = queue(inbox)
    approved(inbox, queued, expected=409)  # asked of Cara, not Ana
    approved(inbox, queued, token="cara-test")
    response = inbox.get("/api/v2/email/sends", params={"mailbox": BOX}, headers=headers("cara-test"))
    assert response.status_code == 403
    response = inbox.put("/api/v2/email/sending-policy", json={"mode": "never"}, headers=headers("cara-test"))
    assert response.status_code == 403


def test_new_automation_uses_the_same_approval_queue_and_never_bypasses_never(inbox, monkeypatch):
    from backend import automation_actions as A, automations as M

    monkeypatch.setattr(S, "classify", lambda app, store, snap, review=None: {"legal_or_money": False})
    with inbox.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO automations(id,name,owner_id,trigger_type,enabled,flow,version,created_at,created_by_id,updated_at) "
            "VALUES('auto','Auto','person:ana','manual',1,?,1,?,'person:ana',?)",
            (json.dumps({"owner": "person:ana", "steps": []}), H.now(), H.now()),
        )
        action = A._queue_mail(
            c,
            {"id": "auto", "flow": {"owner": "person:ana"}},
            None,
            "send",
            {"mailbox": BOX, "id": "inbound"},
            {"body": "Thanks for the update."},
        )["mail_action"]
    M.drain_workspace_mail(inbox.app.state.store)
    with inbox.app.state.store.read() as c:
        queued = S.public(c.execute("SELECT * FROM email_sends WHERE automation_action_id=?", (action,)).fetchone())
        assert c.execute("SELECT state FROM automation_mail_actions WHERE id=?", (action,)).fetchone()[0] == (
            "waiting_send"
        )
    assert queued["state"] == "waiting_approval" and queued["actor"] == "human:ana"
    approved(inbox, queued)
    policy(inbox, mode="never")
    assert dispatch(inbox) == []
    with inbox.app.state.store.read() as c:
        assert c.execute("SELECT state FROM automation_mail_actions WHERE id=?", (action,)).fetchone()[0] == "failed"


def test_assistant_acting_for_a_person_queues_as_that_person_and_cannot_change_policy(inbox):
    from backend.auth import Identity

    machine, _, _ = setup_attempt(inbox, "assistant")
    with inbox.app.state.store.read() as c:
        attempt_id = c.execute(
            "SELECT id FROM attempts WHERE runner_id=? AND bot_id='assistant' ORDER BY rowid DESC LIMIT 1",
            (machine["runner_id"],),
        ).fetchone()[0]
    inbox.app.state.store.settings.test_identities["assistant-test"] = Identity(
        "human:ana",
        "owner",
        BOX,
        via="assistant",
        task_actor="bot:assistant",
        runner_id=machine["runner_id"],
        attempt_id=attempt_id,
    )
    queued = post(inbox, "email/sends", {"mailbox": BOX, "raw": mime()}, token="assistant-test")["send"]
    assert queued["actor"] == "human:ana" and queued["via"] == "assistant"
    assert queued["state"] == "waiting_approval"
    response = inbox.put(
        "/api/v2/email/sending-policy", json={"mode": "on_their_own"}, headers=headers("assistant-test")
    )
    assert response.status_code == 403


def test_a_bot_at_ask_first_cannot_send_without_approval(inbox):
    policy(inbox, undo_seconds=0, pacing_seconds=0)
    queued = queue(inbox, actor="bot:ops")
    assert queued["state"] == "waiting_approval" and "ask_first" in queued["decision"]["reasons"]
    assert dispatch(inbox) == [] and dispatch(inbox) == []
    with inbox.app.state.store.read() as c:
        assert (
            c.execute("SELECT state FROM email_sends WHERE id=?", (queued["id"],)).fetchone()[0] == "waiting_approval"
        )


def test_a_bot_managers_message_approves_nothing(inbox):
    """Only an OK on the frozen email's preview lets it go: "Send it" in a chat is not one."""
    policy(inbox, undo_seconds=0, pacing_seconds=0, ask_lint=False, second_review=False)
    queued = queue(inbox, actor="bot:ops")
    assert inbox.get("/api/v2/email/sends/" + queued["id"], headers=headers()).json()["preview_complete"]
    note = post(inbox, "chat/ops", {"text": "Send it " + queued["id"]})
    with inbox.app.state.store.transaction() as c:
        c.execute("UPDATE email_sends SET approval_id=? WHERE id=?", (note["id"], queued["id"]))
        row = c.execute("SELECT * FROM email_sends WHERE id=?", (queued["id"],)).fetchone()
        assert not S.approved(c, inbox.app.state.auth, row, S.effective(c, BOX, "ops"))
    assert dispatch(inbox) == [] and dispatch(inbox) == []


def test_a_send_for_a_task_asks_its_ok_on_that_task(inbox, monkeypatch):
    """Approving an email meant switching to a "Send:" task of its own under the case, because the bot's next ask on
    the case would have replaced the send's OK and so stopped the email. A send from a bot's turn on a task, or naming
    one, now asks its OK on that task: one OK per send, side by side, and the bot's later OK of a page or ❓ question
    there replaces neither; the approved one still goes. No Send task is filed for it. A send with no task still files
    a task of its own, under nothing; a task the sender cannot read is refused; a sent email leaves the case open."""
    from backend import task_relations
    from backend.tests.helpers import engine as X

    policy(inbox, undo_seconds=0, pacing_seconds=0)
    monkeypatch.setattr(S, "classify", lambda *a, **k: {"legal_or_money": False, "status": "classified"})
    r = runner(inbox, "ana")
    assign(inbox, r, "ops")
    ready(inbox, r, ["ops"])
    case = post(inbox, "tasks", {"owner": "ops", "title": "Winberg: reply letter", "body": "Answer the letter."})
    case = case.get("task", case)["id"]
    token = claim(inbox, r)["token"]
    with inbox.app.state.store.transaction() as c:
        hidden = H.task_create(c, "human:ben", "Ben's own", "x", "human:ben", private=True, lint=False)["id"]
        tasks_before = c.execute("SELECT count(*) FROM tasks").fetchone()[0]

    def send(subject, expected=200, **more):
        body = {"mailbox": BOX, "raw": mime(subject=subject), **more}
        return post(inbox, "email/sends", body, token=token, expected=expected)

    from_turn = send("Reply to your letter")["send"]
    named = send("Records enclosed", task_id=case[:8])["send"]
    with inbox.app.state.store.transaction() as c:
        assert c.execute("SELECT count(*) FROM tasks").fetchone()[0] == tasks_before  # no Send task
        for queued in (from_turn, named):
            assert queued["state"] == "waiting_approval" and queued["task_id"] == case
            assert queued["approval_task_id"] == case == task_asks.task_of(c, queued["approval_id"])
            assert task_asks.standing(c, queued["approval_id"]) == task_asks.WAITING
        page = X.shown(c, bot="ops", task_id=case)
        H.task_ask(
            c, "bot:ops", case, "human:ana", "OK the letter?", shows=page, seen_version=H.task(c, case)["version"]
        )
        assert {task_asks.standing(c, q["approval_id"]) for q in (from_turn, named)} == {task_asks.WAITING}
    post(inbox, f"tasks/{case}/questions", {"to": "ana", "text": "Which date suits the court?"}, token=token)
    with inbox.app.state.store.read() as c:
        assert {task_asks.standing(c, q["approval_id"]) for q in (from_turn, named)} == {task_asks.WAITING}
    assert from_turn["approval_id"] != named["approval_id"]
    send("Other", expected=404, task_id=hidden)

    alone = queue(inbox, raw=mime(subject="Records request"), actor="bot:ops")
    with inbox.app.state.store.read() as c:
        assert alone["task_id"] is None and alone["approval_task_id"] not in (None, case)
        assert task_relations.parent_of(c, alone["approval_task_id"]) is None

    approved(inbox, from_turn)
    assert dispatch(inbox) == [] and len(dispatch(inbox)) == 1
    with inbox.app.state.store.read() as c:
        assert task_asks.standing(c, from_turn["approval_id"]) == task_asks.USED
        assert H.task(c, case)["status"] == "open"
        assert task_asks.standing(c, named["approval_id"]) == task_asks.WAITING


def _case(inbox, title="Winberg: records request"):
    with inbox.app.state.store.transaction() as c:
        return H.task_create(c, "bot:ops", title, "Answer the request.", "bot:ops", lint=False)["id"]


def _not_before(inbox, task, day):
    with inbox.app.state.store.transaction() as c:
        c.execute("DELETE FROM matter_dates WHERE task_id=?", (task,))
        c.execute(
            "INSERT INTO matter_dates(id,task_id,label,starts_at,created_by_id,created_at,updated_by_id,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (H.new_id(), task, "Records produced", day, "human:ana", H.now(), "human:ana", H.now()),
        )


def test_an_approved_send_waits_for_its_matters_not_before_as_it_stands_when_it_goes(inbox, monkeypatch):
    """Nothing held an approved email until its matter's not before ("records may be produced no earlier than"): the
    OK set its time to the end of the undo window. A send goes at the later of its undo deadline and the date, read as
    it stands just before it goes: moved later after the OK, it waits for the new day; moved earlier, or cleared, it
    goes at its undo deadline. The preview names the day only while it is past the undo deadline."""
    policy(inbox, undo_seconds=90, pacing_seconds=0)
    case, other = _case(inbox), _case(inbox, "Winberg: subpoena")
    day, later = H.shift(H.now(), days=3)[:10], H.shift(H.now(), days=5)[:10]
    _not_before(inbox, case, day)
    _not_before(inbox, other, day)

    def preview(send):
        return inbox.get("/api/v2/email/sends/" + send["id"], headers=headers()).json()["not_before"]

    held = queue(inbox, raw=mime(body="The records."), actor="bot:ops", task_id=case)
    early = queue(inbox, raw=mime(body="The subpoena."), actor="bot:ops", task_id=other)
    assert preview(held)[:10] == day
    approved(inbox, held)
    approved(inbox, early)
    assert dispatch(inbox) == []
    with inbox.app.state.store.transaction() as c:
        row = c.execute("SELECT state,send_after FROM email_sends WHERE id=?", (held["id"],)).fetchone()
        assert row["state"] == "pending" and row["send_after"] < S.not_before(c, case)  # the undo deadline, apart
        c.execute("DELETE FROM matter_dates WHERE task_id=?", (other,))  # cleared after the OK
    _not_before(inbox, case, later)  # moved later after the OK
    assert preview(early) is None
    monkeypatch.setattr(H, "now", lambda: H.shift(row["send_after"], seconds=1))  # the undo deadline passed
    assert len(dispatch(inbox)) == 1  # the cleared one goes at its undo deadline
    monkeypatch.setattr(H, "now", lambda: day + "T23:00:00.000000Z")  # the first day came
    assert dispatch(inbox) == []
    monkeypatch.setattr(H, "now", lambda: later + "T23:00:00.000000Z")  # the new day came
    assert len(dispatch(inbox)) == 1


def test_a_send_held_until_a_time_is_left_out_of_the_queue_until_then(inbox, monkeypatch):
    """The queue reads a send's matter date in its own query (a held send's MIME is never loaded): a not before given as
    a time holds it until that time, and not after."""
    autonomous(inbox, undo_seconds=0)
    case = _case(inbox)
    at = H.shift(H.now(), hours=2)[:19] + "Z"
    _not_before(inbox, case, at)
    queue(inbox, raw=mime(body="At two."), actor="bot:ops", task_id=case)
    assert dispatch(inbox) == []
    monkeypatch.setattr(H, "now", lambda: H.shift(at, seconds=1))
    assert len(dispatch(inbox)) == 1


def test_a_held_send_is_on_its_cases_row_with_its_day_until_it_is_sent_and_after(inbox, monkeypatch):
    """A send held for its matter's not before read "Sending" on its case's card with no day, and once sent weeks after
    its undo window, the card dropped it (ended sends are read by `send_after`). The case's row carries the day it waits
    for, and a send that goes is read as going when it went."""
    autonomous(inbox, undo_seconds=0)
    case = _case(inbox)
    day = H.shift(H.now(), days=20)[:10]
    _not_before(inbox, case, day)
    held = queue(inbox, raw=mime(body="Later."), actor="bot:ops", task_id=case)
    with inbox.app.state.store.read() as c:
        row = S.for_tasks(c, [case])[case]
        assert (row["id"], row["state"]) == (held["id"], "pending")
        assert row["not_before"] == S.in_company_zone(S.not_before(c, case))  # the company's day, as the preview says
    monkeypatch.setattr(H, "now", lambda: day + "T23:00:00.000000Z")
    assert len(dispatch(inbox)) == 1
    with inbox.app.state.store.read() as c:
        row = S.for_tasks(c, [case])[case]
        assert (row["id"], row["state"], row["not_before"]) == (held["id"], "sent", None)


def test_a_cases_row_names_a_hold_only_past_the_undo_deadline_and_a_date_change_sends_it_again(inbox):
    """The case's row named a matter date that ends inside the undo window (the send goes at its undo deadline, not
    then), and a date edited later left open pages on the old day. It names the day only past the undo deadline an OK
    would set, as the preview does, and a change to the matter's dates sends the case's row again."""
    policy(inbox, undo_seconds=600, pacing_seconds=0)
    case = _case(inbox)
    risk = post(inbox, "risks", {"label": "Records requests"})
    key = risk.get("risk", risk).get("key") or "risk:records-requests"
    post(inbox, f"tasks/{case}", {"labels": [key if key.startswith("risk:") else "risk:" + key]})
    post(inbox, f"tasks/{case}/matter", {"kind": "request"})
    soon = H.shift(H.now(), minutes=5)[:19] + "Z"
    post(inbox, f"tasks/{case}/matter/dates", {"label": "Records produced", "starts_at": soon})
    queued = queue(inbox, raw=mime(body="The records."), actor="bot:ops", task_id=case)
    assert queued["state"] == "waiting_approval"
    with inbox.app.state.store.read() as c:
        assert S.for_tasks(c, [case])[case]["not_before"] is None  # within the 10 minutes an OK would start
        before = c.execute("SELECT max(rowid) FROM changes").fetchone()[0]
    later = H.shift(H.now(), days=3)[:10]
    post(inbox, f"tasks/{case}/matter/dates", {"label": "Hearing", "starts_at": later})
    with inbox.app.state.store.read() as c:
        assert S.for_tasks(c, [case])[case]["not_before"][:10] == later
        assert c.execute(
            "SELECT 1 FROM changes WHERE rowid>? AND topic='tasks' AND kind='email_send' AND subject_id=?",
            (before, case),
        ).fetchone()


def test_the_drain_writes_nothing_for_an_inbox_that_only_holds_sends_for_later(inbox, monkeypatch):
    """Every drain pass opened a durable write for each inbox with queued mail, even one whose only sends wait for
    their matter's not before. It writes only when it may lease, or has an OK or a hand-over to look at."""
    autonomous(inbox, undo_seconds=0)
    case = _case(inbox)
    _not_before(inbox, case, H.shift(H.now(), days=3)[:10])
    queue(inbox, raw=mime(body="Later."), actor="bot:ops", task_id=case)
    store, writes = inbox.app.state.store, []
    real = store.transaction

    def counted(*args, **kwargs):
        writes.append(kwargs.get("durable"))
        return real(*args, **kwargs)

    monkeypatch.setattr(store, "transaction", counted)
    assert dispatch(inbox) == [] and writes == []
    policy(inbox, mode="ask_first")
    assert queue(inbox, raw=mime(body="Asked first."), actor="bot:ops")["state"] == "waiting_approval"
    writes.clear()
    assert dispatch(inbox) == [] and writes  # a send waiting on an OK is looked at


def test_a_send_still_to_go_is_canceled_when_its_case_or_its_ok_task_closes(inbox):
    """A send still to go for a case that was closed, OK'd or with no OK at all, went anyway when its time came. Closing
    the case cancels it in the same write; closing the task its OK waits on, when that is a task of its own, does too.
    A send whose write reads its case closed (a close that won the race with a slow send) is refused."""
    autonomous(inbox, undo_seconds=90)
    case = _case(inbox)
    allowed = queue(inbox, raw=mime(body="No OK needed."), actor="bot:ops", task_id=case)
    assert allowed["state"] == "pending" and not allowed["approval_id"]
    policy(inbox, mode="ask_first")
    alone = queue(inbox, raw=mime(body="Asked apart."), actor="bot:ops")
    with inbox.app.state.store.transaction() as c:
        H.task_close(c, "human:ana", case, "Settled.")
        H.task_close(c, "human:ana", alone["approval_task_id"], "Not this one.")
        for queued in (allowed, alone):
            gone = c.execute("SELECT state,detail,raw FROM email_sends WHERE id=?", (queued["id"],)).fetchone()
            assert tuple(gone) == ("canceled", S.TASK_CLOSED, "")
    with pytest.raises(Problem, match="was closed"):
        queue(inbox, raw=mime(body="Too late."), actor="bot:ops", task_id=case)
    with inbox.app.state.store.read() as c:
        assert (
            c.execute("SELECT count(*) FROM email_sends WHERE state IN ('pending','waiting_approval')").fetchone()[0]
            == 0
        )
    assert dispatch(inbox) == []


def test_a_send_whose_case_was_closed_before_the_close_canceled_sends_never_goes(inbox, monkeypatch):
    """A case closed before closing a task canceled its sends left them queued. The write that would hand one to Gmail
    reads its case and cancels it instead."""
    autonomous(inbox, undo_seconds=0)
    case = _case(inbox)
    queued = queue(inbox, raw=mime(body="Old news."), actor="bot:ops", task_id=case)
    with monkeypatch.context() as before_deploy:
        before_deploy.setattr(S, "task_closed", lambda c, task_id: None)  # closed by a server that canceled nothing
        with inbox.app.state.store.transaction() as c:
            H.task_close(c, "human:ana", case, "Settled.")
    assert dispatch(inbox) == []
    with inbox.app.state.store.read() as c:
        assert c.execute("SELECT state FROM email_sends WHERE id=?", (queued["id"],)).fetchone()[0] == "canceled"


def test_a_send_back_on_the_case_holds_it_until_the_next_version_asks_again(inbox):
    """A send back on the case was withdrawn by the next drain pass: when it was the last thing the case waited on,
    the case moved on before the agent's next version could ask. The declined version never goes, its ask stays until
    the next version's ask is written, and the case stays where it is."""
    from backend import task_engine as E
    from backend.tests.helpers import engine as X

    policy(inbox, undo_seconds=0, pacing_seconds=0)
    case = _case(inbox)
    first = queue(inbox, raw=mime(body="Version one."), actor="bot:ops", draft_id="r-1", task_id=case)
    with inbox.app.state.store.transaction() as c:
        row = E.task(c, case)
        X.say_done(c, row, "bot:ops")
        H.task_answer(c, "human:ana", case, first["approval_id"], "no", "Say the date.")
    assert dispatch(inbox) == [] and dispatch(inbox) == []
    with inbox.app.state.store.read() as c:
        assert c.execute("SELECT state FROM email_sends WHERE id=?", (first["id"],)).fetchone()[0] == "waiting_approval"
        assert (H.task(c, case)["status"], H.task(c, case)["stage_id"]) == ("open", row["stage_id"])
    second = queue(inbox, raw=mime(body="Version two, with the date."), actor="bot:ops", draft_id="r-1", task_id=case)
    with inbox.app.state.store.read() as c:
        assert task_asks.standing(c, second["approval_id"]) == task_asks.WAITING
        assert c.execute("SELECT state FROM email_sends WHERE id=?", (first["id"],)).fetchone()[0] == "canceled"
        assert (H.task(c, case)["status"], H.task(c, case)["stage_id"]) == ("open", row["stage_id"])


def test_the_same_email_on_another_case_is_its_own_send(inbox):
    """The same email queued for a second case found the first case's send and stopped there: the second case had no
    send of its own. A case's send is never the one queued for another."""
    policy(inbox, undo_seconds=90, pacing_seconds=0)
    one, two = _case(inbox), _case(inbox, "Winberg: second request")
    first = queue(inbox, raw=mime(body="The records."), actor="bot:ops", task_id=one)
    second = queue(inbox, raw=mime(body="The records."), actor="bot:ops", task_id=two)
    assert first["id"] != second["id"] and (first["task_id"], second["task_id"]) == (one, two)
    assert queue(inbox, raw=mime(body="The records."), actor="bot:ops", task_id=one)["id"] == first["id"]


def test_a_draft_id_beside_a_callers_own_mime_replaces_nothing(inbox, monkeypatch):
    """A draft id sent beside the caller's own MIME, never checked against Gmail, replaced whatever send that draft had
    queued. Only a draft Gmail read (or Tico's own server code named) replaces its earlier sends."""
    monkeypatch.setattr(S, "classify", lambda *a, **k: {"legal_or_money": False, "status": "classified"})
    policy(inbox, undo_seconds=90, pacing_seconds=0)
    case = _case(inbox)
    first = queue(inbox, raw=mime(body="Version one."), actor="human:ana", draft_id="r-1", task_id=case)
    body = {"mailbox": BOX, "raw": mime(body="Not from Gmail."), "draft_id": "r-1", "task_id": case}
    other = post(inbox, "email/sends", body)["send"]
    assert other["id"] != first["id"] and other["draft_id"] == ""  # never stored where revisions are found
    with inbox.app.state.store.read() as c:
        assert c.execute("SELECT state FROM email_sends WHERE id=?", (first["id"],)).fetchone()[0] != "canceled"


def test_an_ok_asked_again_under_a_changed_approval_rule_never_lets_the_case_move_on(inbox):
    """When the approval rule changed, the drain withdrew the send's ask before asking the new approver: when it was
    the last thing the case waited on, the case moved on first. The new approver's ask is written first."""
    from backend import task_engine as E
    from backend.tests.helpers import engine as X

    policy(inbox, undo_seconds=0, pacing_seconds=0, who_can_ok="named_people", approvers=["ana"])
    case = _case(inbox)
    first = queue(inbox, raw=mime(body="The records."), actor="bot:ops", task_id=case)
    with inbox.app.state.store.transaction() as c:
        row = E.task(c, case)
        X.say_done(c, row, "bot:ops")
    policy(inbox, who_can_ok="named_people", approvers=["ben"])
    assert dispatch(inbox) == []
    with inbox.app.state.store.read() as c:
        again = c.execute("SELECT approval_id FROM email_sends WHERE id=?", (first["id"],)).fetchone()[0]
        assert again != first["approval_id"] and task_asks.grant(c, again)["asked_of_id"] == "human:ben"
        assert task_asks.standing(c, first["approval_id"]) == task_asks.WITHDRAWN
        assert (H.task(c, case)["status"], H.task(c, case)["stage_id"]) == ("open", row["stage_id"])


def test_a_send_queued_before_keys_named_the_case_is_found_only_for_its_case(inbox):
    """A send queued before its key named its case was missed by the same email again for that case (a second send),
    and found for another. It is found again only for its own case."""
    policy(inbox, undo_seconds=90, pacing_seconds=0)
    one, two = _case(inbox), _case(inbox, "Winberg: second request")
    first = queue(inbox, raw=mime(body="The records."), actor="bot:ops", task_id=one)
    with inbox.app.state.store.transaction() as c:  # as an older server keyed it
        legacy = S.digest(S.dump([BOX, first["content_sha256"], [CONTACT]]))
        c.execute("UPDATE email_sends SET idempotency_key=? WHERE id=?", (legacy, first["id"]))
    assert queue(inbox, raw=mime(body="The records."), actor="bot:ops", task_id=one)["id"] == first["id"]
    assert queue(inbox, raw=mime(body="The records."), actor="bot:ops", task_id=two)["id"] != first["id"]
    taskless = queue(inbox, raw=mime(body="The records."), actor="bot:ops")
    assert taskless["id"] != first["id"] and taskless["task_id"] is None  # a send for no case is not a case's
    assert queue(inbox, raw=mime(body="The records."), actor="bot:ops")["id"] == taskless["id"]


def test_requeuing_a_verified_draft_is_a_new_send_that_retires_every_older_one(inbox):
    """A revision updated its draft's send in place and merged with other sends by content, which let an OK'd version's
    approval reach other content. Requeued, a Gmail-verified draft is a new send of its own: every older send of that
    inbox and draft still to go is canceled with its ask, once the new ask is written, by anyone who may send from the
    inbox (anyone else is refused). It never merges with another draft's send; the same content again changes
    nothing."""
    policy(inbox, undo_seconds=90, pacing_seconds=0)
    case = _case(inbox)

    def send(body, draft="r-1", actor="bot:ops"):
        return queue(inbox, raw=mime(body=body), actor=actor, draft_id=draft, task_id=case)

    first = send("Version one.")
    approved(inbox, first)
    assert send("Version one.", actor="human:ana")["id"] == first["id"]  # the same content: nothing changes
    other = send("Version two.", draft="r-b")
    second = send("Version two.", actor="human:ana")  # says what r-b says: still its own send
    assert len({first["id"], other["id"], second["id"]}) == 3 and second["actor"] == "human:ana"
    with pytest.raises(Problem, match="cannot send"):
        send("Version three.", actor="human:cara")
    with inbox.app.state.store.transaction() as c:
        states = dict(c.execute("SELECT id,state FROM email_sends").fetchall())
        assert (states[first["id"]], states[other["id"]], states[second["id"]]) == (
            "canceled",
            "waiting_approval",
            "waiting_approval",
        )
        assert task_asks.standing(c, first["approval_id"]) == task_asks.WITHDRAWN
        assert task_asks.task_of(c, second["approval_id"]) == case
        c.execute("UPDATE email_sends SET draft_id='r-1' WHERE id=?", (other["id"],))  # older data: two of one draft
    third = send("Version three.")
    with inbox.app.state.store.read() as c:
        states = dict(c.execute("SELECT id,state FROM email_sends").fetchall())
        assert (states[other["id"]], states[second["id"]], states[third["id"]]) == (
            "canceled",
            "canceled",
            "waiting_approval",
        )


def test_a_draft_requeued_by_a_sender_who_cannot_read_its_case_names_no_case(inbox):
    """A draft requeued with no task by another sender of the inbox took its earlier send's case, private or not, and
    a refusal about that case could tell them it existed. The case is kept only for a sender who reads it."""
    from backend import mailbox_access as MA
    from backend.tests.helpers import engine as X

    policy(inbox, undo_seconds=90, pacing_seconds=0)
    with inbox.app.state.store.transaction() as c:
        X.bot(c, "other")
        MA.grant(c, "human:ana", MA._row(c, BOX)["id"], "bot", "other", "read_write")
        case = H.task_create(c, "bot:ops", "Ops' private matter", "x", "bot:ops", private=True, lint=False)["id"]
    first = queue(inbox, raw=mime(body="Version one."), actor="bot:ops", draft_id="r-1", task_id=case)
    assert first["task_id"] == case
    second = queue(inbox, raw=mime(body="Version two."), actor="bot:other", draft_id="r-1")
    assert second["task_id"] is None and second["approval_task_id"] != case
    mine = queue(inbox, raw=mime(body="Version three."), actor="bot:ops", draft_id="r-1")
    assert mine["task_id"] is None  # the newest send names no case now


def test_a_new_version_of_a_draft_an_ok_was_asked_for_needs_its_own_ok(inbox):
    """A new version that took out what made the first one ask (its files) went out with no OK. A version replacing
    one an OK was asked for always asks again, unless it is blocked."""
    autonomous(inbox, undo_seconds=90, ask_attachments=True)
    case = _case(inbox)
    first = queue(inbox, raw=mime(body="Attached.", files=True), actor="bot:ops", draft_id="r-1", task_id=case)
    assert first["state"] == "waiting_approval" and "attachments" in first["decision"]["reasons"]
    second = queue(inbox, raw=mime(body="Here it is, inline."), actor="bot:ops", draft_id="r-1", task_id=case)
    assert second["state"] == "waiting_approval" and second["decision"]["reasons"] == ["revised_after_ok"]
    assert dispatch(inbox) == []


def test_an_operation_is_bound_to_the_request_that_queued_it(inbox):
    """Each operation id is bound to the hash of the request as its caller sent it: the same request finds its send,
    whatever the draft froze since; another request under the same id is refused."""
    policy(inbox, undo_seconds=90, pacing_seconds=0)
    case = _case(inbox)

    def send(body, op, raw=None):
        request = S.Enqueue(mailbox=BOX, draft_id="r-1", task_id=case, operation_id=op)
        frozen = request.model_copy(update={"raw": raw or mime(body=body)})
        with inbox.app.state.store.transaction() as c:
            return S.enqueue(c, inbox.app.state.auth, "bot:ops", frozen, {"legal_or_money": False}, request=request)

    first = send("Version one.", "op-1")
    assert send("Version two.", "op-2")["id"] != first["id"]
    assert send("The draft changed since.", "op-1")["id"] == first["id"]  # the same request, another frozen MIME
    with pytest.raises(Problem, match="different email content"):
        queue(inbox, raw=mime(body="Version two."), actor="bot:ops", draft_id="r-1", task_id=case, operation_id="op-1")


def test_a_retried_operation_returns_its_send_only_to_who_still_reads_its_task(inbox):
    """A retry of an operation returned its send whatever the caller could still read. The replay runs through the
    normal write and checks the task of the send it returns."""
    autonomous(inbox, undo_seconds=90)
    _, _, attempt = setup_attempt(inbox, "ops")
    with inbox.app.state.store.transaction() as c:
        case = H.task_create(c, "human:ana", "Private matter", "x", "human:ana", private=True, lint=False)["id"]
        H.set_task_readers(c, "human:ana", case, add=["bot:ops"])
    body = {"mailbox": BOX, "raw": mime(body="The records."), "task_id": case, "operation_id": "op-r"}
    first = inbox.post("/api/v2/email/sends", json=body, headers=headers(attempt["token"]))
    assert first.status_code == 200, first.text
    with inbox.app.state.store.transaction() as c:
        H.set_task_readers(c, "human:ana", case, remove=["bot:ops"])
    again = inbox.post("/api/v2/email/sends", json=body, headers=headers(attempt["token"]))
    assert again.status_code in (403, 404), again.text


def test_a_held_send_whose_lookup_fails_never_holds_up_due_mail(inbox, monkeypatch):
    """A send held by its matter's not before was read (and its Gmail facts asked) with the due mail of its inbox: a
    lookup that failed for it held up the rest. Held sends are left out in the query, so due mail goes."""
    autonomous(inbox, undo_seconds=0)
    case = _case(inbox)
    _not_before(inbox, case, H.shift(H.now(), days=3)[:10])
    held = queue(inbox, raw=mime(body="Later."), actor="bot:ops", task_id=case)
    due = queue(inbox, raw=mime(body="Now."), actor="bot:ops")
    real = S._original

    def lookup(c, row):
        if row["id"] == held["id"]:
            raise Problem("gmail_unavailable", "Gmail did not answer; try again", 502, retryable=True)
        return real(c, row)

    monkeypatch.setattr(S, "_original", lookup)
    assert len(dispatch(inbox)) == 1
    with inbox.app.state.store.read() as c:
        assert c.execute("SELECT state FROM email_sends WHERE id=?", (due["id"],)).fetchone()[0] == "sent"
        assert c.execute("SELECT state FROM email_sends WHERE id=?", (held["id"],)).fetchone()[0] == "pending"


def test_an_approver_who_cannot_read_the_case_is_asked_apart_and_sees_none_of_its_files(inbox):
    """An approver the case is not private from, but who cannot read it (no Read on the agent it involves), was asked
    on it anyway. The OK is checked as the approval page checks a reader (auth.task) and asked on a task of its own;
    the preview flags no file of the case to them, read only from files they may see."""
    import hashlib

    policy(inbox, undo_seconds=0, pacing_seconds=0, who_can_ok="named_people", approvers=["cara"])
    current = inbox.get("/api/v2/bots/ops/access", headers=headers()).json()
    people = {"people": ["ana"]}
    body = {"see": {"everyone": True}, "read": people, "write": people, "revision": current["revision"]}
    assert inbox.put("/api/v2/bots/ops/access", json=body, headers=headers()).status_code == 200
    case = _case(inbox)
    queued = queue(inbox, raw=mime(files=True), actor="bot:ops", task_id=case)
    assert queued["state"] == "waiting_approval" and queued["approval_task_id"] not in (None, case)
    with inbox.app.state.store.transaction() as c:
        assert task_asks.grant(c, queued["approval_id"])["asked_of_id"] == "human:cara"
        now, digest = H.now(), hashlib.sha256(b"proof").hexdigest()
        c.execute(
            "INSERT INTO blobs (id, owner, digest, size, name, content_type, created_at) VALUES "
            "('b-old','bot:ops',?,5,'proof.pdf','application/pdf',?)",
            (digest, now),
        )
        c.execute(
            "INSERT INTO bot_files (id, bot_id, scope, identity, title, kind, mime, locator, first_activity_at, "
            "last_activity_at, current_version, task_id, archived) VALUES ('f-old','ops',?,'f-old','Letter v1','file',"
            "'application/pdf','tico_blob',?,?,1,?,1)",
            ("task:" + case, now, now, case),
        )
        c.execute(
            "INSERT INTO bot_file_versions (file_id, version, blob_id, digest, size, name, mime, actor, created_at) "
            "VALUES ('f-old',1,'b-old',?,5,'proof.pdf','application/pdf','bot:ops',?)",
            (digest, now),
        )

    def flags(token):
        detail = inbox.get("/api/v2/email/sends/" + queued["id"], headers=headers(token)).json()
        return [f.get("archived", False) for f in detail["send"]["attachments"]]

    assert flags("ana-test") == [True] and flags("cara-test") == [False]


def test_the_preview_marks_a_file_that_is_an_archived_file_of_the_case(inbox):
    """A send OK'd on its case listed its files with nothing to say one was an outdated, archived draft of the case's
    letter. The preview marks a frozen file `archived` when it is a version of an archived file or page of the send's
    task (its own, or linked to it) and of no file still in use there."""
    import hashlib

    policy(inbox, undo_seconds=0, pacing_seconds=0)
    case = _case(inbox)
    queued = queue(inbox, raw=mime(files=True), actor="bot:ops", task_id=case)

    def marked():
        detail = inbox.get("/api/v2/email/sends/" + queued["id"], headers=headers()).json()
        return [f.get("archived", False) for f in detail["send"]["attachments"]]

    with inbox.app.state.store.transaction() as c:
        now, blob, old = H.now(), H.new_id(), "file-letter-v1"
        c.execute(
            "INSERT INTO blobs (id, owner, digest, size, name, content_type, created_at) VALUES (?,?,?,?,?,?,?)",
            (blob, "bot:ops", hashlib.sha256(b"proof").hexdigest(), 5, "proof.pdf", "application/pdf", now),
        )
        c.execute(
            "INSERT INTO bot_files (id, bot_id, scope, identity, title, kind, mime, locator, first_activity_at, "
            "last_activity_at, current_version, task_id, archived) VALUES (?,'ops',?,?,'Letter v1','file',"
            "'application/pdf','tico_blob',?,?,1,?,1)",
            (old, "task:" + case, old, now, now, case),
        )
        c.execute(
            "INSERT INTO bot_file_versions (file_id, version, blob_id, digest, size, name, mime, actor, created_at) "
            "VALUES (?,1,?,?,5,'proof.pdf','application/pdf','bot:ops',?)",
            (old, blob, hashlib.sha256(b"proof").hexdigest(), now),
        )
    assert marked() == [True]
    with inbox.app.state.store.transaction() as c:
        c.execute("UPDATE bot_files SET archived=0 WHERE id=?", (old,))
    assert marked() == [False]
    with inbox.app.state.store.transaction() as c:  # a page linked to the case, archived as a page
        c.execute("UPDATE bot_files SET task_id=NULL, scope='bot' WHERE id=?", (old,))
        c.execute("INSERT INTO pages (file_id, archived, created_at, updated_at) VALUES (?,1,?,?)", (old, now, now))
        c.execute(
            "INSERT INTO page_links (id, page_id, target, role, created_by, created_at) VALUES "
            "('pl-1',?,?,'related','human:ana',?)",
            (old, "task:" + case, now),
        )
    assert marked() == [True]


def test_each_move_of_a_send_reaches_its_tasks_on_the_stream_and_their_rows(inbox, monkeypatch):
    """After Approve the case's card and the email's toast say "Sending · 1:40", then "Sent 5:23 AM", on every device
    and for everyone who reads the case, without reading the send again and again: each move of the send logs the case
    and its OK's task (a `tasks` change, kind email_send, on GET /api/v2/events), and each task's row, in a list and in
    its own read, carries the send's newest state (`email_send`)."""
    from backend import events as E
    from backend import task_list_routes
    from backend.auth import Identity

    policy(inbox, undo_seconds=90, pacing_seconds=0)
    monkeypatch.setattr(S, "classify", lambda *a, **k: {"legal_or_money": False, "status": "classified"})
    r = runner(inbox, "ana")
    assign(inbox, r, "ops")
    ready(inbox, r, ["ops"])
    case = post(inbox, "tasks", {"owner": "ops", "title": "Winberg: reply letter", "body": "Answer the letter."})
    case = case.get("task", case)["id"]
    token = claim(inbox, r)["token"]
    store, auth = inbox.app.state.store, inbox.app.state.auth
    task_views = task_list_routes.readers(auth)[2]
    ana = Identity("human:ana", "owner", "ana@acme.example")

    def latest():
        with store.read() as c:
            return E.latest(c)

    def streamed(after):
        """The send's state on each task the stream sends after `after` for a send's move."""
        with store.read() as c:
            events = E.read(c, auth, ana, after, topics=("tasks",), task_views=task_views)[0]
        return {d["id"]: d["task"].get("email_send") for _, _, d in events if "email_send" in d["kinds"]}

    def listed():
        rows = inbox.get("/api/v2/tasks?status=all", headers=headers()).json()["tasks"]
        return {row["id"]: row.get("email_send") for row in rows}

    before = latest()
    queued = post(inbox, "email/sends", {"mailbox": BOX, "raw": mime(subject="Reply")}, token=token)["send"]
    sub = queued["approval_task_id"]
    moved = streamed(before)
    assert set(moved) == {case, sub}
    assert moved[case] == moved[sub] and moved[case]["id"] == queued["id"]
    assert moved[case]["state"] == "waiting_approval"

    approved(inbox, queued)
    before = latest()
    assert dispatch(inbox) == []  # the pass that takes the OK: in its undo window
    moved = streamed(before)
    assert moved[case]["state"] == "pending" and moved[case]["send_after"] > H.now()
    assert listed()[case] == moved[case]

    with store.transaction() as c:  # the undo window is over
        c.execute(
            "UPDATE email_sends SET created_at=?,send_after=? WHERE id=?",
            (H.shift(H.now(), minutes=-5), H.shift(H.now(), minutes=-3), queued["id"]),
        )
    before = latest()
    assert len(dispatch(inbox)) == 1
    moved = streamed(before)
    assert moved[case]["state"] == "sent" and moved[case]["finished_at"]
    assert moved[sub] == moved[case]
    assert listed()[case] == moved[case]
    detail = inbox.get(f"/api/v2/tasks/{case}", headers=headers()).json()
    assert detail.get("task", detail)["email_send"] == moved[case]
    assert set(moved[case]) == {"id", "state", "send_after", "finished_at", "detail", "not_before"}  # never the email
    with store.transaction() as c:  # a day after it ended, the case no longer shows it
        c.execute("UPDATE email_sends SET finished_at=? WHERE id=?", (H.shift(H.now(), hours=-25), queued["id"]))
    assert listed()[case] is None


def test_never_refuses_and_names_the_setting(inbox):
    autonomous(inbox)
    with inbox.app.state.store.transaction() as c:
        S.update_policy(
            c, inbox.app.state.auth.identity_for_actor(c, "human:ana"), "bot:ops", S.PolicyUpdate(mode="never")
        )
    queued = queue(inbox, actor="bot:ops", reply_to_id="inbound", thread_id="thread")
    assert queued["state"] == "blocked" and "never" in queued["decision"]["reasons"]
    with inbox.app.state.store.read() as c:
        detail = c.execute("SELECT detail FROM email_sends WHERE id=?", (queued["id"],)).fetchone()[0]
    assert "never (bot setting)" in detail


def test_a_gmail_hiccup_reading_the_thread_stops_a_bots_send_as_retryable(inbox, monkeypatch):
    """The second reviewer reads the thread the email answers; if Gmail cannot answer now, nothing is queued."""
    from backend import gmail_live

    autonomous(inbox, second_review=True)
    _, _, attempt = setup_attempt(inbox, "ops")

    def down(c, mailbox, thread_id):
        raise Problem("gmail_unavailable", "Gmail did not answer; try again", 502, retryable=True)

    monkeypatch.setattr(gmail_live, "thread", down)
    monkeypatch.setattr(S, "classify", lambda *a, **k: pytest.fail("reviewed without its thread"))
    message = {"mailbox": BOX, "raw": mime(In_Reply_To="<inbound@vendor.example>"), "thread_id": "thread"}
    response = inbox.post("/api/v2/email/sends", json=message, headers=headers(attempt["token"]))
    assert response.status_code == 502 and response.json()["error"]["retryable"], response.text
    with inbox.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM email_sends").fetchone()[0] == 0


def workspace(monkeypatch, answer):
    """The inbox as a Workspace key covers it, and a fake Gmail that answers `answer` to every send."""
    from backend import gmail_live, google_tokens, google_workspace as GW
    from backend.tests.helpers.fake_gmail import REAL_TOKEN

    monkeypatch.setattr(google_tokens, "token", REAL_TOKEN)  # through the real door, to the faked key
    monkeypatch.setattr(GW, "authority", lambda c, mailbox: "workspace")
    monkeypatch.setattr(
        GW, "mint", lambda c, mailbox, service, row=None, scopes=None: {"access_token": "fixture-token"}
    )
    calls = []

    def send_call(url, *, token, body, content_type):
        calls.append({"url": url, "token": token, "body": json.loads(body), "type": content_type})
        return answer

    monkeypatch.setattr(gmail_live, "send_call", send_call)
    return calls


@pytest.mark.parametrize(
    "answer,state",
    [((200, {"id": "gmail-1"}), "sent"), ((400, {}), "failed"), ((503, {}), "unknown"), ((200, {}), "unknown")],
)
def test_the_server_sends_a_workspace_inboxs_mail_itself_once(inbox, monkeypatch, answer, state):
    autonomous(inbox)
    calls = workspace(monkeypatch, answer)
    queued = queue(inbox, reply_to_id="inbound", thread_id="thread")
    machine = runner(inbox)
    # No computer leases mail: a 0.6 computer that still asks is handed nothing (backend/connectors_floor.py).
    assert post(inbox, "connectors/email/sends/claim", {"mailbox": BOX}, token=machine["token"]) == {"send": None}
    store, auth = inbox.app.state.store, inbox.app.state.auth
    assert S.drain(store, auth) == int(state == "sent")
    [call] = calls
    assert call["token"] == "fixture-token" and call["url"].endswith(BOX.replace("@", "%40") + "/messages/send")
    assert call["body"] == {"raw": mime(In_Reply_To="<inbound@vendor.example>"), "threadId": "thread"}
    with store.read() as c:
        row = c.execute("SELECT * FROM email_sends WHERE id=?", (queued["id"],)).fetchone()
    assert (row["state"], row["runner_id"], row["raw"]) == (state, S.SERVER, "")
    assert row["provider_message_id"] == ("gmail-1" if state == "sent" else "")
    assert S.drain(store, auth) == 0 and len(calls) == 1  # never a second copy


def test_a_failing_workspace_key_leaves_the_email_pending(inbox, monkeypatch):
    from backend import google_workspace as GW
    from backend.store import Problem

    autonomous(inbox)
    calls = workspace(monkeypatch, (200, {"id": "gmail-1"}))

    def refused(*args, **kwargs):
        raise Problem("not_connected", "The key is failing", 403)

    queued = queue(inbox, reply_to_id="inbound", thread_id="thread")
    monkeypatch.setattr(GW, "mint", refused)  # the key fails after the email was queued
    assert S.drain(inbox.app.state.store, inbox.app.state.auth) == 0 and calls == []
    with inbox.app.state.store.read() as c:
        assert c.execute("SELECT state FROM email_sends WHERE id=?", (queued["id"],)).fetchone()[0] == "pending"


def test_a_mailbox_no_server_credential_covers_fails_its_email_with_why(inbox, monkeypatch):
    from backend import google_workspace as GW

    autonomous(inbox)
    calls = workspace(monkeypatch, (200, {"id": "gmail-1"}))
    queued = queue(inbox, reply_to_id="inbound", thread_id="thread")
    monkeypatch.setattr(GW, "authority", lambda c, mailbox: "legacy")  # the key and the grant are gone
    assert S.drain(inbox.app.state.store, inbox.app.state.auth) == 0 and calls == []
    with inbox.app.state.store.read() as c:
        row = c.execute("SELECT state,detail FROM email_sends WHERE id=?", (queued["id"],)).fetchone()
    assert row["state"] == "failed" and "covers" in row["detail"]


def own_grant(inbox, monkeypatch, scopes):
    """BOX is connected with its person's own Connect Google grant, held on the server, with `scopes`; Google refreshes
    it to `ya29.own`. Returns the fake Google and the fake Gmail's send calls."""
    from backend import gmail_live, google_connect, google_tokens
    from backend.tests.helpers.fake_gmail import REAL_TOKEN
    from backend.tests.helpers.google_connect import CLIENT, FakeGoogle
    from backend.tests.helpers.google_workspace import personal

    monkeypatch.setattr(google_tokens, "token", REAL_TOKEN)  # the grant's own token, through the real door
    settings = inbox.app.state.store.settings
    monkeypatch.setattr(settings, "google_oauth_client_id", CLIENT)
    monkeypatch.setattr(settings, "google_oauth_client_secret", "tico-secret")
    personal(inbox, "ana", BOX, client=CLIENT, scopes=json.dumps(scopes))
    google = FakeGoogle({"access_token": "ya29.own", "expires_in": 3599})
    monkeypatch.setattr(google_connect, "post_token", google)
    calls = []

    def send_call(url, *, token, body, content_type):
        calls.append({"url": url, "token": token})
        return 200, {"id": "gmail-7"}

    monkeypatch.setattr(gmail_live, "send_call", send_call)
    return google, calls


def test_the_server_sends_a_personal_grant_s_mail_from_its_own_grant(inbox, monkeypatch):
    from backend import google_connect as GC

    autonomous(inbox)
    google, calls = own_grant(inbox, monkeypatch, GC.READ_SCOPES + [GC.G + "gmail.modify"])
    queued = queue(inbox, reply_to_id="inbound", thread_id="thread")
    machine = runner(inbox)
    assert post(inbox, "connectors/email/sends/claim", {"mailbox": BOX}, token=machine["token"]) == {"send": None}
    store, auth = inbox.app.state.store, inbox.app.state.auth
    assert S.drain(store, auth) == 1
    assert [call["token"] for call in calls] == ["ya29.own"]
    assert google.calls[-1]["grant_type"] == "refresh_token" and google.calls[-1]["scope"] == GC.G + "gmail.modify"
    with store.read() as c:
        row = c.execute("SELECT state,runner_id FROM email_sends WHERE id=?", (queued["id"],)).fetchone()
    assert tuple(row) == ("sent", S.SERVER)


def test_a_read_only_personal_grant_fails_its_due_email_without_asking_google(inbox, monkeypatch):
    from backend import google_connect as GC

    autonomous(inbox)
    google, calls = own_grant(inbox, monkeypatch, GC.READ_SCOPES)
    queued = queue(inbox, reply_to_id="inbound", thread_id="thread")  # its reply checks read Gmail with the grant
    asked = len(google.calls)
    store, auth = inbox.app.state.store, inbox.app.state.auth
    assert S.drain(store, auth) == 0 and calls == [] and len(google.calls) == asked
    with store.read() as c:
        row = c.execute("SELECT state,detail FROM email_sends WHERE id=?", (queued["id"],)).fetchone()
    assert row["state"] == "failed" and "read only" in row["detail"]


def test_a_retired_registry_mail_policy_carries_its_safeguards_over_once(inbox):
    import yaml

    store = inbox.app.state.store
    path = store.settings.registry_dir / "mail-policy.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "global": {"send_enabled": True},
                "blocklist": {"addresses": ["Spam@Vendor.example"], "domains": ["@rival.example"]},
                "owner_handles_personally": {"domains": ["bank.example"]},
                "mailboxes": {BOX: {"paused": True}},
                "defaults": {"max_sends_per_day": 5, "max_external_recipients": 3, "per_recipient_cooldown_days": 2},
            }
        )
    )
    with store.transaction() as c:
        c.execute("DELETE FROM registry_metadata WHERE key=?", (S.IMPORTED,))
    S.import_registry(store)
    with store.read() as c:
        company, found = S.saved(c, "company"), S.effective(c, BOX, "ops")
    assert company["blocked_recipients"] == ["rival.example", "spam@vendor.example"]
    assert company["owner_handles"] == ["bank.example"] and company["daily_cap"] == 5
    assert "max_outside_recipients" not in company  # 3 is looser than the default 1: not carried
    assert found["mode"] == "never" and found["sources"]["mode"] == "inbox"
    # Read once: a person's later change stands, and the file is not read again.
    with store.transaction() as c:
        S.update_policy(
            c, inbox.app.state.auth.identity_for_actor(c, "human:ana"), "inbox:" + BOX, S.PolicyUpdate(inherit=True)
        )
    S.import_registry(store)
    with store.read() as c:
        assert S.effective(c, BOX, "ops")["mode"] == "ask_first"
    blocked = queue(inbox, actor="bot:ops", raw=mime(to=["spam@vendor.example"]))
    assert blocked["state"] == "blocked" and "blocked_recipient" in blocked["decision"]["reasons"]


def test_a_drain_leases_no_new_email_once_its_budget_is_spent(inbox, monkeypatch):
    """The drain worker's budget (backend/drains.py): a slow Gmail ends the pass early; the rest go next pass, once."""
    from types import SimpleNamespace

    from backend import drains, gmail_live

    autonomous(inbox)
    calls = workspace(monkeypatch, (200, {"id": "gmail-1"}))
    answer = gmail_live.send_call
    # The budget's clock moves only when Gmail answers, so a loaded machine cannot change what one pass leases.
    clock = [0.0]
    monkeypatch.setattr(drains, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    def slow(*args, **kwargs):
        clock[0] += 0.5
        return answer(*args, **kwargs)

    monkeypatch.setattr(gmail_live, "send_call", slow)
    ids = [queue(inbox, raw=mime(body=f"Update {n}.", subject=f"Update {n}"))["id"] for n in range(3)]
    assert len(set(ids)) == 3
    store, auth = inbox.app.state.store, inbox.app.state.auth
    budget = drains.Budget(0.75)
    assert S.drain(store, auth, budget=budget) == 2 and budget.spent  # leased at 0 s and 0.5 s, none at 1 s
    with store.read() as c:
        states = [c.execute("SELECT state FROM email_sends WHERE id=?", (i,)).fetchone()[0] for i in ids]
    assert states == ["sent", "sent", "pending"]
    assert S.drain(store, auth, budget=drains.Budget(5)) == 1
    assert S.drain(store, auth, budget=drains.Budget(5)) == 0 and len(calls) == 3  # each once, never twice
