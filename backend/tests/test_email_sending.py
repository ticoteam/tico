"""Policy, approval, revocation and idempotency boundaries shared by all email send paths."""

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

from backend import email_sending as S
from backend import task_asks
from backend.store import H
from backend.tests.helpers.api import headers, post as api_post, runner, setup_attempt  # noqa: F401
from backend.tests.helpers.email_sending import (
    BOX,
    CONTACT,
    autonomous,
    claim,
    inbox,
    incoming,
    mime,
    policy,
    post,
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
    machine = runner(inbox)
    policy(inbox, mode="never")
    assert claim(inbox, machine["token"]) is None
    with inbox.app.state.store.read() as c:
        row = c.execute("SELECT * FROM email_sends WHERE id=?", (queued["id"],)).fetchone()
        assert row["state"] == "blocked" and "never" in json.loads(row["decision"])["reasons"]


def test_an_ok_from_the_preview_releases_the_frozen_send_and_is_used(inbox):
    policy(inbox, undo_seconds=0, pacing_seconds=0)
    queued = queue(inbox, actor="bot:ops")
    assert queued["state"] == "waiting_approval"
    approved(inbox, queued)
    machine = runner(inbox)
    assert claim(inbox, machine["token"]) is None
    assert claim(inbox, machine["token"])["id"] == queued["id"]
    permitted = post(
        inbox, "connectors/email/sends/" + queued["id"] + "/dispatch", {"mailbox": BOX}, token=machine["token"]
    )
    assert permitted["send"]["id"] == queued["id"]
    with inbox.app.state.store.read() as c:
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
    machine = runner(inbox)
    assert claim(inbox, machine["token"]) is None
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
    assert claim(inbox, machine["token"]) is None
    with inbox.app.state.store.transaction() as c:
        c.execute("UPDATE email_sends SET state='sent' WHERE id=?", (queued["id"],))
    response = inbox.post("/api/v2/email/sends/" + queued["id"] + "/cancel", json={}, headers=headers())
    assert response.status_code == 409


def test_due_send_is_dispatched_once_with_a_receipt_and_audit(inbox):
    autonomous(inbox)
    queued = queue(inbox, raw=mime(files=True))
    machine = runner(inbox)
    assert claim(inbox, machine["token"])["id"] == queued["id"]
    path = "connectors/email/sends/" + queued["id"]
    permitted = post(inbox, path + "/dispatch", {"mailbox": BOX}, token=machine["token"], key="one-permit")
    assert S.snapshot(permitted["send"]["raw"], BOX)["content_sha256"] == queued["content_sha256"]
    assert post(inbox, path + "/dispatch", {"mailbox": BOX}, token=machine["token"], key="one-permit")["send"] is None
    receipt = post(inbox, path + "/result", {"status": "sent", "message": "provider-123"}, token=machine["token"])
    assert receipt["send"]["provider_message_id"] == "provider-123"
    assert [{k: f[k] for k in ("name", "sha256")} for f in receipt["send"]["attachments"]] == [
        {"name": "proof.pdf", "sha256": S.digest(b"proof")}
    ]
    assert claim(inbox, machine["token"]) is None


def test_permission_revocation_between_claim_and_dispatch(inbox):
    autonomous(inbox)
    queued = queue(inbox, actor="bot:ops")
    machine = runner(inbox)
    assert claim(inbox, machine["token"])
    with inbox.app.state.store.transaction() as c:
        c.execute("DELETE FROM mailbox_accesses WHERE grantee_type='bot' AND grantee_id='ops'")
    assert (
        post(inbox, "connectors/email/sends/" + queued["id"] + "/dispatch", {"mailbox": BOX}, token=machine["token"])[
            "send"
        ]
        is None
    )


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
    machine = runner(inbox)
    assert claim(inbox, machine["token"]) is None
    with inbox.app.state.store.read() as c:
        result = json.loads(c.execute("SELECT decision FROM email_sends WHERE id=?", (queued["id"],)).fetchone()[0])
    assert "agent_revoked" in result["reasons"]


@pytest.mark.parametrize("cap", ["daily_cap", "mailbox_daily_cap", "company_daily_cap"])
def test_caps_reserve_concurrent_dispatches_including_unknown_results(inbox, cap):
    autonomous(inbox, **{cap: 1})
    first = queue(inbox, raw=mime(body="First"))
    queue(inbox, raw=mime(body="Second"))
    machine = runner(inbox)
    assert claim(inbox, machine["token"])["id"] == first["id"]
    assert claim(inbox, machine["token"]) is None
    post(inbox, "connectors/email/sends/" + first["id"] + "/dispatch", {"mailbox": BOX}, token=machine["token"])
    post(inbox, "connectors/email/sends/" + first["id"] + "/result", {"status": "unknown"}, token=machine["token"])
    assert claim(inbox, machine["token"]) is None


def test_pacing_duplicate_hash_and_reply_loop_protection(inbox):
    autonomous(inbox, pacing_seconds=60)
    first = queue(inbox, raw=mime(Message_ID="<one@acme.example>"))
    assert queue(inbox, raw=mime(Message_ID="<two@acme.example>"))["id"] == first["id"]
    queue(inbox, raw=mime(body="Second"))
    machine = runner(inbox)
    assert claim(inbox, machine["token"])
    assert claim(inbox, machine["token"]) is None
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


def test_new_automation_uses_the_same_approval_queue_and_never_bypasses_never(inbox):
    machine = runner(inbox)
    with inbox.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO automations(id,name,owner_id,trigger_type,enabled,flow,version,created_at,created_by_id,updated_at) "
            "VALUES('auto','Auto','person:ana','manual',1,?,1,?,'person:ana',?)",
            (json.dumps({"owner": "person:ana", "steps": []}), H.now(), H.now()),
        )
        c.execute(
            "INSERT INTO automation_mail_actions(id,automation_id,run_id,mailbox,msg_id,action,args,state,created_at,claimed_by) "
            "VALUES('action','auto',NULL,?,'inbound','send','{}','claimed',?,?)",
            (BOX, H.now(), machine["runner_id"]),
        )
    result = post(
        inbox,
        "email/sends",
        {
            "mailbox": BOX,
            "raw": mime(In_Reply_To="<inbound@vendor.example>"),
            "thread_id": "thread",
            "reply_to_id": "inbound",
            "automation_action_id": "action",
        },
        token=machine["token"],
    )
    queued = result["send"]
    assert queued["state"] == "waiting_approval" and queued["actor"] == "human:ana"
    post(
        inbox,
        "automations/mail-actions/action/result",
        {"ok": True, "send_queue_id": queued["id"]},
        token=machine["token"],
    )
    approved(inbox, queued)
    policy(inbox, mode="never")
    assert claim(inbox, machine["token"]) is None
    with inbox.app.state.store.read() as c:
        assert c.execute("SELECT state FROM automation_mail_actions WHERE id='action'").fetchone()[0] == "failed"


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


def test_migration_snapshots_existing_authorizations_without_granting_new_ones():
    path = next((Path(__file__).parents[1] / "migrations").glob("*_email_sending_queue.py"))
    spec = importlib.util.spec_from_file_location("email_policy_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE bot_mail_settings(bot,outbound_send,updated_at)")
    c.execute("INSERT INTO bot_mail_settings VALUES('counsel',0,'2026-01-01')")
    c.execute("INSERT INTO bot_mail_settings VALUES('support',1,'2026-01-01')")
    c.execute("CREATE TABLE automations(id,owner_id,version,deleted_at)")
    c.execute("INSERT INTO automations VALUES('existing','person:ana',2,NULL)")
    module.up(c)
    assert S.saved(c, "bot:counsel")["mode"] == "ask_first"
    assert S.saved(c, "bot:support")["mode"] == "on_their_own"
    assert S.saved(c, "bot:new") == {}
    assert c.execute("SELECT automation_id FROM email_legacy_automations").fetchone()[0] == "existing"
    assert S.effective(c)["mode"] == "ask_first"
    c.execute(
        "UPDATE email_sending_policies SET settings=? WHERE scope='bot:support'", (json.dumps({"mode": "never"}),)
    )
    c.execute("INSERT INTO automations VALUES('new','person:ana',1,NULL)")
    module.up(c)
    assert S.saved(c, "bot:support")["mode"] == "never"
    assert not c.execute("SELECT 1 FROM email_legacy_automations WHERE automation_id='new'").fetchone()


@pytest.mark.parametrize("existing", [False, True])
def test_reply_history_migration_initializes_fresh_tables_and_preserves_existing_mail(existing):
    path = next((Path(__file__).parents[1] / "migrations").glob("*_initialize_mail_reply_history.py"))
    spec = importlib.util.spec_from_file_location("reply_history_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with sqlite3.connect(":memory:") as c:
        if existing:
            c.execute(module.TABLE)
            c.execute(
                "INSERT INTO mail_messages(mailbox,msg_id,epoch,date,from_addr,to_addresses,cc,subject,snippet,"
                "labels,body,attachments,updated_at) VALUES(?,'kept',1,'now',?,'[]','[]','Update','Hello',"
                "'[]','Hello','[]','now')",
                (BOX, CONTACT),
            )
        module.up(c)
        assert "send_facts" in {row[1] for row in c.execute("PRAGMA table_info(mail_messages)")}
        assert c.execute("SELECT name FROM sqlite_master WHERE name='mail_messages_rfc_id'").fetchone()
        if existing:
            assert c.execute("SELECT msg_id,body,send_facts FROM mail_messages").fetchall() == [("kept", "Hello", "{}")]
        module.up(c)
        assert c.execute("SELECT count(*) FROM mail_messages").fetchone()[0] == int(existing)


def test_a_bot_at_ask_first_cannot_send_without_approval(inbox):
    policy(inbox, undo_seconds=0, pacing_seconds=0)
    queued = queue(inbox, actor="bot:ops")
    assert queued["state"] == "waiting_approval" and "ask_first" in queued["decision"]["reasons"]
    machine = runner(inbox)
    assert claim(inbox, machine["token"]) is None and claim(inbox, machine["token"]) is None
    with inbox.app.state.store.read() as c:
        assert (
            c.execute("SELECT state FROM email_sends WHERE id=?", (queued["id"],)).fetchone()[0] == "waiting_approval"
        )


def test_a_bot_managers_message_approves_nothing(inbox):
    """Only an OK on the frozen email's preview lets it go: "Send it" in a chat is not one."""
    policy(inbox, undo_seconds=0, pacing_seconds=0, ask_lint=False, second_review=False)
    queued = queue(inbox, actor="bot:ops")
    assert inbox.get("/api/v2/email/sends/" + queued["id"], headers=headers()).json()["preview_complete"]
    note = api_post(inbox, "chat/ops", {"text": "Send it " + queued["id"]})
    with inbox.app.state.store.transaction() as c:
        c.execute("UPDATE email_sends SET approval_id=? WHERE id=?", (note["id"], queued["id"]))
        row = c.execute("SELECT * FROM email_sends WHERE id=?", (queued["id"],)).fetchone()
        assert not S.approved(c, inbox.app.state.auth, row, S.effective(c, BOX, "ops"))
    machine = runner(inbox)
    assert claim(inbox, machine["token"]) is None and claim(inbox, machine["token"]) is None


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


def workspace(monkeypatch, answer):
    """The inbox as a Workspace key covers it, and a fake Gmail that answers `answer` to every send."""
    from backend import gmail_live, google_workspace as GW

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
    # No computer leases a Workspace inbox's mail (the computer that syncs it gets None, any other is refused).
    post(inbox, "connectors/email/sends/claim", {"mailbox": BOX}, token=machine["token"], expected=403)
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

    monkeypatch.setattr(GW, "mint", refused)
    queued = queue(inbox, reply_to_id="inbound", thread_id="thread")
    assert S.drain(inbox.app.state.store, inbox.app.state.auth) == 0 and calls == []
    with inbox.app.state.store.read() as c:
        assert c.execute("SELECT state FROM email_sends WHERE id=?", (queued["id"],)).fetchone()[0] == "pending"


def test_a_mailbox_the_server_cannot_mint_for_is_still_dispatched_by_its_computer(inbox, monkeypatch):
    from backend import google_workspace as GW

    autonomous(inbox)
    calls = workspace(monkeypatch, (200, {"id": "gmail-1"}))
    # No Workspace key covers it: the grant its computer holds sends (a personal grant's own computer is the same path).
    monkeypatch.setattr(GW, "authority", lambda c, mailbox: "legacy")
    queued = queue(inbox, reply_to_id="inbound", thread_id="thread")
    assert S.drain(inbox.app.state.store, inbox.app.state.auth) == 0 and calls == []
    machine = runner(inbox)
    assert claim(inbox, machine["token"])["id"] == queued["id"]
    path = "connectors/email/sends/" + queued["id"]
    assert post(inbox, path + "/dispatch", {"mailbox": BOX}, token=machine["token"])["send"]["id"] == queued["id"]
    receipt = post(inbox, path + "/result", {"status": "sent", "message": "provider-9"}, token=machine["token"])
    assert receipt["send"]["provider_message_id"] == "provider-9" and calls == []


def one_send_policy():
    path = next((Path(__file__).parents[1] / "migrations").glob("*_one_send_policy.py"))
    spec = importlib.util.spec_from_file_location("one_send_policy_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_one_policy_migration_never_widens_what_a_bot_may_send():
    module = one_send_policy()
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute(
        "CREATE TABLE email_sending_policies(scope TEXT PRIMARY KEY, settings TEXT NOT NULL, "
        "updated_at TEXT NOT NULL, updated_by TEXT NOT NULL)"
    )
    c.execute(
        "CREATE TABLE bot_mail_settings(bot TEXT PRIMARY KEY, outbound_send INTEGER NOT NULL DEFAULT 0, "
        "forward_to TEXT NOT NULL DEFAULT '[]', updated_at TEXT NOT NULL, updated_by TEXT NOT NULL)"
    )
    c.execute("CREATE TABLE org_rule_misses(author TEXT NOT NULL, id TEXT NOT NULL, created_at TEXT NOT NULL)")
    layers = {
        "company": {"mode": "on_their_own"},
        "bot:turned-off": {
            "mode": "on_their_own",
            "legacy": True,
            "daily_cap": 20,
            "legacy_allowances": {BOX: [CONTACT]},
        },
        "bot:on": {"mode": "on_their_own", "legacy": True, "daily_cap": 20},
        "bot:never": {"mode": "never"},
        "bot:asks": {"mode": "ask_first"},
    }
    for scope, settings in layers.items():
        c.execute(
            "INSERT INTO email_sending_policies VALUES(?,?,'2026-01-01','migration')", (scope, json.dumps(settings))
        )
    switches = {"turned-off": 0, "on": 1, "never": 0, "asks": 1, "quiet": 0, "loud": 1}
    for bot, on in switches.items():
        c.execute(
            "INSERT INTO bot_mail_settings VALUES(?,?,?,'2026-01-01','human:ana')",
            (bot, on, json.dumps(["me@personal.example"])),
        )
    rank = {"never": 0, "ask_first": 1, "on_their_own": 2}

    def before(bot):
        # Before: the bot sent on its own only with its switch on and an On their own policy.
        mode = S.effective(c, bot=bot)["mode"]
        return mode if switches[bot] or mode == "never" else "ask_first"

    was = {bot: before(bot) for bot in switches}
    module.up(c)
    now = {bot: S.effective(c, bot=bot)["mode"] for bot in switches}
    assert all(rank[now[bot]] <= rank[was[bot]] for bot in switches), (was, now)
    assert now == {
        "turned-off": "ask_first",
        "on": "on_their_own",
        "never": "never",
        "asks": "ask_first",
        "quiet": "ask_first",
        "loud": "on_their_own",
    }
    saved = {r["scope"]: json.loads(r["settings"]) for r in c.execute("SELECT * FROM email_sending_policies")}
    assert saved["bot:turned-off"] == {"mode": "ask_first", "daily_cap": 20}
    assert saved["bot:on"] == {"mode": "on_their_own", "daily_cap": 20} and "bot:loud" not in saved
    assert saved["bot:quiet"] == {"mode": "ask_first"} and saved["company"] == {"mode": "on_their_own"}
    tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "bot_mail_settings" not in tables and "org_rule_misses" not in tables
    module.up(c)  # again, as on a restart: nothing changes
    assert {bot: S.effective(c, bot=bot)["mode"] for bot in switches} == now
    fresh = sqlite3.connect(":memory:")
    module.up(fresh)  # a new install has neither table


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
    import time

    from backend import drains, gmail_live

    autonomous(inbox)
    calls = workspace(monkeypatch, (200, {"id": "gmail-1"}))
    answer = gmail_live.send_call

    def slow(*args, **kwargs):
        time.sleep(0.5)
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
