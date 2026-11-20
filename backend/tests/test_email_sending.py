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


def test_a_send_for_a_task_asks_its_ok_under_that_task_titled_with_the_email(inbox, monkeypatch):
    """Each held send filed a task of its own titled "Send an email", apart from the case it was for: three sends could
    not be told apart, and none was linked to the case. A bot's send from its turn on a task, or naming one, asks its
    OK on a sub-task of it titled with the email, one per send (on the case itself the next send's ask would replace
    the first's and stop that email). The send names both tasks; a task its sender cannot read is refused, and a
    sent email leaves the case open."""
    from backend import task_relations

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

    def send(subject, expected=200, **more):
        body = {"mailbox": BOX, "raw": mime(subject=subject), **more}
        return post(inbox, "email/sends", body, token=token, expected=expected)

    from_turn = send("Reply to your letter")["send"]
    named = send("Records enclosed", task_id=case[:8])["send"]
    with inbox.app.state.store.read() as c:
        for queued, subject in ((from_turn, "Reply to your letter"), (named, "Records enclosed")):
            assert queued["state"] == "waiting_approval" and queued["task_id"] == case
            sub = H.task(c, queued["approval_task_id"])
            assert sub["title"] == f"Send: {subject} → {CONTACT}" and sub["private"]
            assert task_relations.parent_of(c, sub["id"]) == case
            assert task_asks.task_of(c, queued["approval_id"]) == sub["id"]
            assert task_asks.standing(c, queued["approval_id"]) == task_asks.WAITING
    assert from_turn["approval_task_id"] != named["approval_task_id"]
    send("Other", expected=404, task_id=hidden)

    alone = queue(inbox, raw=mime(subject="Records request"), actor="bot:ops")
    with inbox.app.state.store.read() as c:
        assert alone["task_id"] is None and task_relations.parent_of(c, alone["approval_task_id"]) is None

    approved(inbox, from_turn)
    assert dispatch(inbox) == [] and len(dispatch(inbox)) == 1
    with inbox.app.state.store.read() as c:
        assert task_asks.standing(c, from_turn["approval_id"]) == task_asks.USED
        assert H.task(c, from_turn["approval_task_id"])["status"] == "done"
        assert H.task(c, case)["status"] == "open"
        assert task_asks.standing(c, named["approval_id"]) == task_asks.WAITING


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
    assert set(moved[case]) == {"id", "state", "send_after", "finished_at", "detail"}  # never the email itself
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
    # No computer leases mail: the route a computer once claimed sends from is gone.
    gone = inbox.post("/api/v2/connectors/email/sends/claim", json={"mailbox": BOX}, headers=headers(machine["token"]))
    assert gone.status_code in (404, 405), gone.text
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
