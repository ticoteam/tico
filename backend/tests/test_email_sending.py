"""Policy, approval, revocation and idempotency boundaries shared by all email send paths."""

import base64
import importlib.util
import json
import sqlite3
from email.message import EmailMessage
from pathlib import Path

import pytest

from backend import email_sending as S, mailbox_access as MA
from backend.store import H
from backend.tests.test_api import api, headers, post as api_post, runner, setup_attempt  # noqa: F401

BOX = "ana@acme.example"
CONTACT = "contact@vendor.example"


def post(api, path, body, **kwargs):
    if path.startswith("connectors/email/sends/") and not path.endswith("/claim") and isinstance(body, dict) and "lease_id" not in body:
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
    c.execute("INSERT INTO mail_messages(mailbox,msg_id,thread_id,epoch,date,from_addr,to_addresses,cc,subject,"
              "snippet,labels,body,attachments,updated_at,send_facts) VALUES(?,?,?,0,?,?,?,?,?,?,?,?,?,?,?)",
              (BOX, mid, "thread", H.now(), sender, json.dumps([BOX]), "[]", "Update", "Hello", "[]", "Hello", "[]",
               H.now(), json.dumps({"message-id": "<" + mid + "@vendor.example>", **(facts or {})})))


def policy(api, **values):
    response = api.put("/api/v2/email/sending-policy", json=values, headers=headers())
    assert response.status_code == 200, response.text
    return response.json()


def autonomous(api, **values):
    return policy(api, **{"mode": "on_their_own", "what": "replies_and_new", "ask_new_address": False,
                          "ask_attachments": False, "ask_many_recipients": False, "ask_legal_money": False,
                          "undo_seconds": 0, "pacing_seconds": 0, **values})


def queue(api, *, raw=None, actor="human:ana", **values):
    with api.app.state.store.transaction() as c:
        return S.enqueue(c, api.app.state.auth, actor, S.Enqueue(mailbox=BOX, raw=raw or mime(**({"In_Reply_To": "<" + values["reply_to_id"] + "@vendor.example>"} if values.get("reply_to_id") else {})), **values),
                         {"legal_or_money": False, "status": "classified"})


def claim(api, token):
    return post(api, "connectors/email/sends/claim", {"mailbox": BOX}, token=token)["send"]


def approved(api, queued, token="ana-test"):
    assert api.get("/api/v2/email/sends/" + queued["id"], headers=headers(token)).status_code == 200
    post(api, "approvals/" + queued["approval_id"], {"decision": "approved"}, token=token)


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


@pytest.mark.parametrize("rule,raw,reply,reason", [
    ("ask_new_address", mime(to=["new@vendor.example"]), False, "new_address"),
    ("ask_attachments", mime(files=True), False, "attachments"),
    ("ask_many_recipients", mime(to=[f"person{i}@vendor.example" for i in range(11)]), False, "more_than_10_recipients"),
    ("ask_legal_money", mime(), False, "legal_or_money_or_unclassified"),
], ids=["new_address", "attachments", "many_recipients", "legal_money"])
def test_each_always_ask_rule_cannot_be_bypassed_by_autonomy(inbox, rule, raw, reply, reason):
    autonomous(inbox, **{rule: True})
    if rule == "ask_legal_money":
        # An unknown Decide result asks a person too.
        with inbox.app.state.store.transaction() as c:
            queued = S.enqueue(c, inbox.app.state.auth, "human:ana", S.Enqueue(mailbox=BOX, raw=raw),
                               {"legal_or_money": None})
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
    from backend.tests.test_automation_cli import Client
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


def test_a_tico_approval_without_bcc_releases_the_frozen_send_and_is_consumed(inbox):
    policy(inbox, undo_seconds=0, pacing_seconds=0)
    queued = queue(inbox, actor="bot:ops")
    assert queued["state"] == "waiting_approval"
    approved(inbox, queued)
    machine = runner(inbox)
    assert claim(inbox, machine["token"]) is None
    assert claim(inbox, machine["token"])["id"] == queued["id"]
    permitted = post(inbox, "connectors/email/sends/" + queued["id"] + "/dispatch", {"mailbox": BOX}, token=machine["token"])
    assert permitted["send"]["id"] == queued["id"]
    with inbox.app.state.store.read() as c:
        assert H.approval(c, queued["approval_id"])["consumed_at"]


def test_a_github_issue_number_asks_for_a_normal_tico_approval(inbox):
    autonomous(inbox)
    queued = queue(inbox, approval_id="77")
    assert queued["state"] == "waiting_approval"
    assert queued["approval_id"] and queued["approval_id"] != "77"
    assert "github_approval_needs_tico" in queued["decision"]["reasons"]


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
    assert [{k: f[k] for k in ("name", "sha256")} for f in receipt["send"]["attachments"]] == [{"name": "proof.pdf", "sha256": S.digest(b"proof")}]
    assert claim(inbox, machine["token"]) is None


def test_permission_revocation_between_claim_and_dispatch(inbox):
    autonomous(inbox)
    queued = queue(inbox, actor="bot:ops")
    machine = runner(inbox)
    assert claim(inbox, machine["token"])
    with inbox.app.state.store.transaction() as c:
        c.execute("DELETE FROM mailbox_accesses WHERE grantee_type='bot' AND grantee_id='ops'")
    assert post(inbox, "connectors/email/sends/" + queued["id"] + "/dispatch", {"mailbox": BOX},
                token=machine["token"])["send"] is None


def test_external_agent_revocation_blocks_its_pending_send(inbox):
    autonomous(inbox)
    with inbox.app.state.store.transaction() as c:
        c.execute("INSERT INTO agents(bot,harness,token_hash,created_at,created_by) VALUES('ops','external','fixture',?,'human:ana')", (H.now(),))
        queued = S.enqueue(c, inbox.app.state.auth, "bot:ops", S.Enqueue(mailbox=BOX, raw=mime()),
                           {"legal_or_money": False}, via="agent:external")
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
    blocked = queue(inbox, raw=mime(body="No loop", In_Reply_To="<automatic@vendor.example>"), reply_to_id="automatic", thread_id="thread")
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
    response = inbox.post("/api/v2/approvals/" + queued["approval_id"], json={"decision": "approved"}, headers=headers())
    assert response.status_code == 403

    approved(inbox, queued, token="cara-test")
    response = inbox.get("/api/v2/email/sends", params={"mailbox": BOX}, headers=headers("cara-test"))
    assert response.status_code == 403
    response = inbox.put("/api/v2/email/sending-policy", json={"mode": "never"}, headers=headers("cara-test"))
    assert response.status_code == 403


def test_new_automation_uses_the_same_approval_queue_and_never_bypasses_never(inbox):
    machine = runner(inbox)
    with inbox.app.state.store.transaction() as c:
        c.execute("INSERT INTO automations(id,name,owner_id,trigger_type,enabled,flow,version,created_at,created_by_id,updated_at) "
                  "VALUES('auto','Auto','person:ana','manual',1,?,1,?,'person:ana',?)",
                  (json.dumps({"owner": "person:ana", "steps": []}), H.now(), H.now()))
        c.execute("INSERT INTO automation_mail_actions(id,automation_id,run_id,mailbox,msg_id,action,args,state,created_at,claimed_by) "
                  "VALUES('action','auto',NULL,?,'inbound','send','{}','claimed',?,?)", (BOX, H.now(), machine["runner_id"]))
    result = post(inbox, "email/sends", {"mailbox": BOX, "raw": mime(In_Reply_To="<inbound@vendor.example>"), "thread_id": "thread",
                                       "reply_to_id": "inbound", "automation_action_id": "action"}, token=machine["token"])
    queued = result["send"]
    assert queued["state"] == "waiting_approval" and queued["actor"] == "human:ana"
    post(inbox, "automations/mail-actions/action/result", {"ok": True, "send_queue_id": queued["id"]}, token=machine["token"])
    approved(inbox, queued)
    policy(inbox, mode="never")
    assert claim(inbox, machine["token"]) is None
    with inbox.app.state.store.read() as c:
        assert c.execute("SELECT state FROM automation_mail_actions WHERE id='action'").fetchone()[0] == "failed"


def test_assistant_acting_for_a_person_queues_as_that_person_and_cannot_change_policy(inbox):
    from backend.auth import Identity
    machine, _, _ = setup_attempt(inbox, "coo")
    with inbox.app.state.store.read() as c:
        attempt_id = c.execute("SELECT id FROM attempts WHERE runner_id=? AND bot_id='coo' ORDER BY rowid DESC LIMIT 1", (machine["runner_id"],)).fetchone()[0]
    inbox.app.state.store.settings.test_identities["assistant-test"] = Identity(
        "human:ana", "owner", BOX, via="assistant", task_actor="bot:coo", runner_id=machine["runner_id"], attempt_id=attempt_id)
    queued = post(inbox, "email/sends", {"mailbox": BOX, "raw": mime()}, token="assistant-test")["send"]
    assert queued["actor"] == "human:ana" and queued["via"] == "assistant"
    assert queued["state"] == "waiting_approval"
    response = inbox.put("/api/v2/email/sending-policy", json={"mode": "on_their_own"}, headers=headers("assistant-test"))
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
    c.execute("UPDATE email_sending_policies SET settings=? WHERE scope='bot:support'", (json.dumps({"mode": "never"}),))
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
            c.execute("INSERT INTO mail_messages(mailbox,msg_id,epoch,date,from_addr,to_addresses,cc,subject,snippet,"
                      "labels,body,attachments,updated_at) VALUES(?,'kept',1,'now',?,'[]','[]','Update','Hello',"
                      "'[]','Hello','[]','now')", (BOX, CONTACT))
        module.up(c)
        assert "send_facts" in {row[1] for row in c.execute("PRAGMA table_info(mail_messages)")}
        assert c.execute("SELECT name FROM sqlite_master WHERE name='mail_messages_rfc_id'").fetchone()
        if existing:
            assert c.execute("SELECT msg_id,body,send_facts FROM mail_messages").fetchall() == [("kept", "Hello", "{}")]
        module.up(c)
        assert c.execute("SELECT count(*) FROM mail_messages").fetchone()[0] == int(existing)
