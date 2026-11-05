"""Mail actions an automation queued, carried out by the computer that reads the mailbox: the real mail sync publishes
a message, the automation queues its actions, the connector worker (runner_support/connectors.py) claims them and runs the
mail CLI (connectors/mail/actions.py) against a fake Gmail, and the run's steps wait until the results come back.
Also: the lease and its retries never send twice, only that mailbox's computer may claim, and the send switch is read
again when a send leaves (mail and the Slack outbox)."""
import contextlib
import io
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest
import time_machine

from backend.store import H
from backend.tests.test_api import api, assign, get, headers, post, ready, runner  # noqa: F401
from backend.tests.test_automation_turns import finish
from clients.tico import APIError
from connectors.mail import access, audit as audit_log, db, gmail as gm, policy as pl
from connectors.mail import __main__ as cli
from connectors.mail import actions as mail_actions, unsubscribe as unsub, oauth
from connectors.mail.tests import fake
from runner_support.connectors import CalendarRefused, CalendarInvitationPending, ConnectorPublisher

BOX = "ana@acme.example"
INVOICE = {"From": "Billing <billing@vendor.example>", "To": BOX, "Subject": "Invoice 4471",
           "Message-ID": "<inv-4471@vendor.example>", "Date": "Tue, 27 Oct 2026 09:00:00 -0700"}
DRAFT = {"id": "draft", "type": "action", "do": "draft_reply", "args": {"body": "Draft: checking with finance."}}
SEND = {"id": "reply", "type": "action", "do": "send_reply", "args": {"body": "Thanks, we will pay this week."}}


class Hub:
    """runner_support/connectors.py's client, over the test app with this computer's token."""

    def __init__(self, api, token):
        self.api, self.token = api, token

    def _answer(self, r):
        if r.status_code != 200:
            error = r.json().get("error") or {}
            raise APIError(error.get("code"), error.get("detail"), r.status_code)
        return r.json()

    def get(self, path, **query):
        return self._answer(self.api.get("/api/v2/" + path, params=query, headers=headers(self.token)))

    def post(self, path, body=None, key=None):
        return self._answer(self.api.post("/api/v2/" + path, json=body or {}, headers=headers(self.token, key)))


def run_cli(argv, stdin=""):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), mock.patch("sys.stdin", io.StringIO(stdin)):
        rc = cli.main(list(argv))
    return rc, json.loads(out.getvalue() or "{}")


class Computer:
    """The mail computer: the mail CLI on a fake Gmail and its own mail.db, and the connector worker that talks to
    the hub with its runner token."""

    def __init__(self, api, tmp_path, token):
        self.gmail = fake.FakeGmailService([fake.message(
            "m1", "t1", INVOICE, body="Please pay invoice 4471.", epoch_ms=int(time.time() * 1000) - 3_600_000)])
        self.worker = ConnectorPublisher({"url": "http://localhost:8765", "token": "unused",
                                          "owner_handle": access.OWNER, "projects_dir": str(tmp_path)},
                                         client=Hub(api, token), mail=self.mail, action=self.action,
                                         event=self.event)
        self.worker.mail_boxes = [BOX]
        self.calendar = fake.FakeCalendarService()

    def mail(self, args):
        rc, answer = run_cli(args)
        assert rc == 0, answer
        return answer

    def action(self, action):
        return run_cli(["mail-action", "--as", access.CONNECTOR, "--json"], json.dumps(action))[1]

    def event(self, action):
        """ConnectorPublisher.calendar_event, in this process: exit 2 is a refusal before Google was asked."""
        rc, answer = run_cli(["connector-event", "--as", access.CONNECTOR, "--json"], json.dumps(action))
        if rc == 2:
            raise CalendarRefused("Tico refused the calendar event before calling Google")
        if rc == 75 and answer.get("kind") == "conference_pending":
            raise CalendarInvitationPending(answer["event_id"])
        assert rc == 0, answer
        return answer

    def sync(self):
        """The real sync: Gmail into mail.db, exported and published to the hub as this runner."""
        return self.worker._mail_mailbox(BOX, {"message_count": 0}, 100)

    def sent(self):
        return [self.gmail.store[s["id"]] for s in self.gmail.sent]

    def drafts(self):
        return [self.gmail.store[d["message"]["id"]] for d in self.gmail.drafts_by_id.values()]


@pytest.fixture
def computer(api, tmp_path, monkeypatch):
    machine = runner(api)
    box = Computer(api, tmp_path, machine["token"])
    monkeypatch.setattr(cli, "open_gmail", lambda mailbox: gm.Gmail(box.gmail, mailbox, sleep=lambda s: None))
    monkeypatch.setattr(cli, "open_db", lambda: db.connect(tmp_path / "mail.db"))
    monkeypatch.setattr(audit_log, "AUDIT_PATH", tmp_path / "audit.jsonl")
    monkeypatch.setattr(access, "PROJECTS", tmp_path)
    # The CLI runs as the connector and reads What Tico may do from the hub as this runner (policy.runner_company). The
    # registration lives where TICO_RUNNER_CONFIG says, with none in the home, as a custom install has it: nothing the
    # runner runs looks for one.
    monkeypatch.setattr(pl, "RUNNER_GET", lambda path: box.worker.client.get(path))
    # The CLI's connector principal reads the mailboxes this computer acts for as the worker does (its mail targets;
    # here the mailbox it was given).
    monkeypatch.setattr(access, "RUNNER_ACCESS", lambda: {**box.worker.client.get("connectors/mail/targets"),
                                                          "mailboxes": [{"address": a} for a in box.worker.mail_boxes]})
    monkeypatch.setattr(cli, "open_calendar", lambda mailbox: box.calendar)
    (tmp_path / "home").mkdir()
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "elsewhere/runner.json").write_text(json.dumps({"url": "http://localhost:8765", "token": "unused"}))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TICO_RUNNER_CONFIG", str(tmp_path / "elsewhere/runner.json"))
    box.token = machine["token"]
    monkeypatch.setattr(oauth, "broker", lambda **kw: box.worker.client)
    return box


def automate(api, *steps, **extra):
    return post(api, "automations", {"name": "Invoices", "owner": "person:ana",
                                     "trigger": {"type": "message", "inbox": BOX}, "steps": list(steps), **extra})["id"]


def owned_by_ana(c, *ids):
    """Automation rows for actions a test queues by hand: the claim checks each action's owner holds the mailbox."""
    now = H.now()
    for aid in ids:
        c.execute("INSERT OR IGNORE INTO automations(id,name,owner_id,trigger_type,enabled,flow,version,created_at,"
                  "created_by_id,updated_at) VALUES(?,?,?,'manual',0,?,1,?,?,?)",
                  (aid, aid, "person:ana", json.dumps({"owner": "person:ana", "steps": []}), now, "person:ana", now))


def last_run(api, aid):
    return get(api, f"automations/{aid}/runs")["runs"][0]


def header(msg, name):
    return fake.FakeGmailService.header(msg, name)


def claim(api, token, expected=200):
    return post(api, "automations/mail-actions/claim", {"mailbox": BOX}, token, expected=expected)


def later(minutes):
    return time_machine.travel(datetime.now(timezone.utc) + timedelta(minutes=minutes), tick=False)


def approve_and_dispatch(api, computer, send_id=None):
    with api.app.state.store.read() as c:
        row = c.execute("SELECT id,approval_id FROM email_sends WHERE " + ("id=?" if send_id else "state='waiting_approval'") + " ORDER BY rowid DESC LIMIT 1", (send_id,) if send_id else ()).fetchone()
    assert row
    assert api.get("/api/v2/email/sends/" + row["id"], headers=headers()).status_code == 200
    post(api, "approvals/" + row["approval_id"], {"decision": "approved"})
    assert computer.worker.email_send_tick() == 0  # approval starts the undo window
    with later(3):
        count = computer.worker.email_send_tick()
        with api.app.state.store.read() as c:
            state = c.execute("SELECT state,decision,detail,send_after FROM email_sends WHERE id=?", (row["id"],)).fetchone()
        assert count == 1, dict(state)
    return row["id"]


# ---------------------------------------------------------------------------------------------- the whole loop
def test_redesign_promo_sample_queues_danas_archive_without_a_mailbox_row():
    from backend import automations, mailbox_access
    from evals.redesign import seeds
    from evals.redesign.harness import DANA_REF, Org

    # The browser and this check use the same sample so it targets a message the connector can identify.
    sample = json.loads((Path(__file__).resolve().parents[2] / "ui/tests/fixtures/redesign-automation-promo.json")
                        .read_text())
    with Org() as world:
        seeds.automations(world)
        listed = world.api("get", "/api/v2/automations")
        assert listed.status == 200, listed.body
        triage = next(a for a in listed.body["automations"] if a["name"] == "Inbox triage")
        inbox = triage["trigger"]["inbox"]
        with world.store.read() as c:
            assert triage["owner_id"] == DANA_REF
            assert mailbox_access.principal(c, DANA_REF) == mailbox_access.Principal("person", "dana")
            assert c.execute("SELECT 1 FROM mailboxes WHERE address=?", (inbox,)).fetchone() is None
            assert mailbox_access.level(c, DANA_REF, inbox) == "read_write"
            assert automations.may_read_inbox(c, DANA_REF, inbox)
            assert mailbox_access.action_refusal(c, DANA_REF, inbox, "archive") is None
        reply = world.api("post", f"/api/v2/automations/{triage['id']}/run", {"input": sample})
        assert reply.status == 200, reply.body
        run = reply.body["run"]
        assert run["status"] == "waiting", run
        assert run["steps"][0]["status"] == "waiting", run
        with world.store.read() as c:
            action, = c.execute("SELECT mailbox, msg_id, action, state FROM automation_mail_actions WHERE run_id=?",
                                (run["id"],)).fetchall()
            assert dict(action) == {"mailbox": inbox, "msg_id": "promo-sample", "action": "archive", "state": "queued"}


def test_a_sample_naming_no_mailbox_is_mail_in_the_trigger_inbox():
    """The page's default sample has no mailbox; its archive targets the trigger's inbox, not none (refused)."""
    from evals.redesign import seeds
    from evals.redesign.harness import Org

    with Org() as world:
        seeds.automations(world)
        triage = next(a for a in world.api("get", "/api/v2/automations").body["automations"]
                      if a["name"] == "Inbox triage")
        sample = {"message": {"from": "promo@deals.example", "subject": "Sale", "body": "50% off"}}
        reply = world.api("post", f"/api/v2/automations/{triage['id']}/run", {"input": sample})
        assert reply.status == 200, reply.body
        assert reply.body["run"]["steps"][0]["status"] == "waiting", reply.body["run"]
        with world.store.read() as c:
            action, = c.execute("SELECT mailbox, action, state FROM automation_mail_actions WHERE run_id=?",
                                (reply.body["run"]["id"],)).fetchall()
        assert dict(action) == {"mailbox": triage["trigger"]["inbox"], "action": "archive", "state": "queued"}


def test_mail_in_runs_the_automation_and_its_computer_drafts_and_replies_in_the_thread(api, computer):
    aid = automate(api, DRAFT, SEND)
    assert computer.sync() == 1
    run = last_run(api, aid)
    # Queued, not done: the steps and the run wait for the mail computer.
    assert run["status"] == "waiting" and run["finished_at"] is None
    assert [(s["status"], s["outputs"]["queued"]) for s in run["steps"]] == [("waiting", True), ("waiting", True)]
    assert computer.worker.mail_action_tick() == 2
    assert computer.sent() == []
    assert len(computer.drafts()) == 2
    approve_and_dispatch(api, computer)
    # Raw MIME delivery preserves source drafts so a concurrent user edit is never deleted.
    assert len(computer.drafts()) == 2
    draft = next(d for d in computer.drafts() if "checking with finance" in fake.base64.urlsafe_b64decode(d["payload"]["body"]["data"] + "==").decode())
    sent, = computer.sent()
    for msg, text in ((draft, "checking with finance"), (sent, "pay this week")):
        assert msg["threadId"] == "t1" and header(msg, "In-Reply-To") == "<inv-4471@vendor.example>"
        assert header(msg, "To") == "billing@vendor.example" and header(msg, "Subject") == "Re: Invoice 4471"
        assert text in fake.base64.urlsafe_b64decode(msg["payload"]["body"]["data"] + "==").decode()
    run = last_run(api, aid)
    assert run["status"] == "ok" and run["finished_at"]
    assert [(s["status"], s["outputs"]["detail"], s["outputs"]["queued"]) for s in run["steps"]] == [
        ("ok", "drafted", False), ("ok", "sent", False)]
    assert run["steps"][1]["outputs"]["message"] == sent["id"]
    assert computer.worker.mail_action_tick() == 0          # nothing left, nothing done twice
    assert len(computer.gmail.sent) == 1


def test_archive_label_and_leave_change_the_message_and_a_filter_step_waits_for_them(api, computer):
    gate = {"id": "gate", "type": "filter", "rules": [{"when": [], "then": [
        {"do": "label", "args": {"tag": "billing"}}, {"do": "leave_for_you"}, {"do": "archive"}]}]}
    aid = automate(api, gate)
    computer.sync()
    assert last_run(api, aid)["steps"][0]["status"] == "waiting"
    computer.worker.mail_action_tick()
    labels = {computer.gmail.name_of(lid) for lid in computer.gmail.store["m1"]["labelIds"]}
    assert {"hub/billing", "hub/needs-owner"} <= labels and "INBOX" not in labels
    run = last_run(api, aid)
    assert run["status"] == "ok" and run["steps"][0]["status"] == "ok"
    assert [a["detail"] for a in run["steps"][0]["outputs"]["actions"]] == ["labelled hub/billing", "left for you",
                                                                            "archived"]


def test_forward_sends_the_message_on_with_the_note_first(api, computer):
    aid = automate(api, {"id": "fwd", "type": "action", "do": "forward",
                         "args": {"to": "ap@acme.example", "body": "For payment."}})
    computer.sync()
    computer.worker.mail_action_tick()
    assert computer.sent() == []
    approve_and_dispatch(api, computer)
    sent, = computer.sent()
    text = fake.base64.urlsafe_b64decode(sent["payload"]["body"]["data"] + "==").decode()
    assert header(sent, "To") == "ap@acme.example" and header(sent, "Subject") == "Fwd: Invoice 4471"
    assert text.startswith("For payment.") and "Please pay invoice 4471." in text
    assert last_run(api, aid)["status"] == "ok"


# ---------------------------------------------------------------------------------------------- lease and retries
def test_a_lost_claim_and_a_lost_result_never_send_twice(api, computer):
    aid = automate(api, SEND)
    computer.sync()
    lost = claim(api, computer.token)["actions"]
    assert len(lost) == 1 and claim(api, computer.token)["actions"] == []
    with later(6):
        again, = claim(api, computer.token)["actions"]
        assert again["id"] == lost[0]["id"] and again["attempt"] == 2
        first = computer.action(again)
        assert first["detail"] == "waiting_approval" and computer.gmail.sent == []
    with later(12):
        third, = claim(api, computer.token)["actions"]
        repeated = computer.action(third)
        assert repeated["send_queue_id"] == first["send_queue_id"]
        assert computer.gmail.sent == []
        report = {"ok": True, "send_queue_id": first["send_queue_id"]}
        url = f"automations/mail-actions/{third['id']}/result"
        assert post(api, url, report, computer.token)["state"] == "waiting_send"
    # The approval follows both lease retries in time; the undo window never moves backward.
    with later(15):
        approve_and_dispatch(api, computer, first["send_queue_id"])
    assert len(computer.gmail.sent) == 1
    assert last_run(api, aid)["status"] == "ok"


def test_a_send_whose_answer_was_lost_inside_the_cli_is_not_sent_again(api, computer):
    """An uncertain provider delivery stays unknown and a second CLI call cannot send it again."""
    automate(api, SEND)
    computer.sync()
    computer.worker.mail_action_tick()
    with api.app.state.store.read() as c:
        queued = c.execute("SELECT id,approval_id FROM email_sends ORDER BY rowid DESC LIMIT 1").fetchone()
    assert api.get("/api/v2/email/sends/" + queued["id"], headers=headers()).status_code == 200
    post(api, "approvals/" + queued["approval_id"], {"decision": "approved"})
    assert computer.worker.email_send_tick() == 0
    real = gm.Gmail.send_raw
    def sent_then_crash(self, raw, thread_id=""):
        real(self, raw, thread_id)
        raise gm.Failure("connection reset after the send", status=503)
    with later(3), mock.patch.object(gm.Gmail, "send_raw", sent_then_crash):
        count = computer.worker.email_send_tick()
        with api.app.state.store.read() as c:
            state = c.execute("SELECT state,decision,detail,send_after FROM email_sends WHERE id=?", (queued["id"],)).fetchone()
        assert count == 1, dict(state)
    assert len(computer.gmail.sent) == 1
    repeated = computer.action({"id": queued["id"], "mailbox": BOX, "action": "dispatch_send"})
    assert repeated["status"] == "unknown" and repeated["retry"] is False
    assert len(computer.gmail.sent) == 1
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM email_sends WHERE id=?", (queued["id"],)).fetchone()[0] == "unknown"


def test_three_failed_tries_fail_the_action_and_the_run_says_where(api, computer):
    aid = automate(api, SEND)
    computer.sync()
    for n in range(3):
        action, = claim(api, computer.token)["actions"]
        state = post(api, f"automations/mail-actions/{action['id']}/result",
                     {"ok": False, "detail": "Gmail drafts.create failed: 503"}, computer.token)["state"]
        assert state == ("failed" if n == 2 else "queued")
    assert claim(api, computer.token)["actions"] == []
    run = last_run(api, aid)
    assert run["status"] == "failed" and run["summary"] == "failed at reply"
    assert run["steps"][0]["status"] == "failed" and "503" in run["steps"][0]["outputs"]["detail"]


def test_a_lease_never_reported_three_times_fails(api, computer):
    aid = automate(api, SEND)
    computer.sync()
    for n in range(3):
        with later(6 * n):
            assert len(claim(api, computer.token)["actions"]) == 1
    with later(18):
        assert claim(api, computer.token)["actions"] == []
    step = last_run(api, aid)["steps"][0]
    assert step["status"] == "failed" and step["outputs"]["detail"] == "no result after 3 tries"


def test_a_message_that_is_gone_fails_at_once(api, computer):
    automate(api, SEND)
    computer.sync()
    del computer.gmail.store["m1"]
    computer.worker.mail_action_tick()
    with api.app.state.store.read() as c:
        row = c.execute("SELECT state, attempts FROM automation_mail_actions").fetchone()
    assert dict(row) == {"state": "failed", "attempts": 1}


# ---------------------------------------------------------------------------------------------- who may claim
def test_only_the_computer_that_reads_the_mailbox_claims_or_reports_its_actions(api, computer):
    automate(api, SEND)
    computer.sync()                                          # this runner published the mailbox
    other = runner(api, label="Second Mac")                  # the owner's too, but it does not read this mailbox
    stranger = runner(api, operator="ben", label="Ben's Mac")
    assert claim(api, other["token"], expected=403)
    assert claim(api, stranger["token"], expected=403)
    assert claim(api, "ana-test", expected=403)              # a person is not a computer
    action, = claim(api, computer.token)["actions"]
    url = f"automations/mail-actions/{action['id']}/result"
    for token in (other["token"], stranger["token"], "ben-test"):
        post(api, url, {"ok": True, "detail": "sent"}, token, expected=403)
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM automation_mail_actions").fetchone()[0] == "claimed"


def test_a_claim_stops_inside_the_byte_budget_and_the_rest_wait_for_the_next(api, computer):
    computer.sync()
    body = json.dumps({"body": "x" * 100_000})                   # 100 KB each: a claim takes two or three at most
    with api.app.state.store.transaction() as c:
        owned_by_ana(c, "auto_x")
        for i in range(5):
            c.execute("INSERT INTO automation_mail_actions(id,automation_id,run_id,mailbox,msg_id,action,args,"
                      "state,created_at,attempts) VALUES(?,?,?,?,?,?,?,?,?,0)",
                      (f"act{i}", "auto_x", "run_x", BOX, "m1", "draft_reply", body, "queued",
                       f"2026-10-28T00:00:0{i}Z"))
    first = [a["id"] for a in claim(api, computer.token)["actions"]]
    assert 0 < len(first) < 5
    rest = list(first)
    while len(rest) < 5 and (batch := claim(api, computer.token)["actions"]):
        rest += [a["id"] for a in batch]
    assert sorted(rest) == [f"act{i}" for i in range(5)]


# ---------------------------------------------------------------------------------------------- the send switch
def test_a_send_claimed_after_the_switch_went_off_is_a_draft(api, computer):
    aid = automate(api, SEND)
    computer.sync()                                          # queued while sending was on
    r = api.patch(f"/api/v2/inboxes/{BOX}", json={"send": False}, headers=headers())
    assert r.status_code == 200
    computer.worker.mail_action_tick()
    assert computer.gmail.sent == [] and len(computer.drafts()) == 1
    step = last_run(api, aid)["steps"][0]
    assert step["status"] == "ok" and step["outputs"]["detail"] == "send_off" and step["outputs"]["send_off"]
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM events WHERE action='automation.send_off'").fetchone()[0] == 1


def test_what_tico_may_do_holds_automations_a_send_is_claimed_as_a_draft_which_organize_off_refuses(api, computer):
    """Settings > Inboxes > Settings > What Tico may do covers automations: Send mail off claims a send as a draft, as the inbox's
    own switch does. With Send mail off only, the send is leased as a draft; with Organize mail off too the server's
    one check (backend/mailbox_access.py action_refusal) fails the draft and the archive at the claim, so no computer
    is asked, and the connector still refuses whatever an action on stdin says, from the hub's own setting."""
    send = automate(api, SEND)
    archive = automate(api, ARCHIVE)
    automate(api, DRAFT)
    computer.sync()
    r = api.put("/api/v2/google/permissions", json={"organize_mail": True, "send_mail": False, "change_calendar": True},
                headers=headers())
    assert r.status_code == 200, r.text
    leased = claim(api, computer.token)["actions"]
    assert sorted(a["action"] for a in leased) == ["archive", "draft", "draft"]      # the send became a draft
    assert all("company" not in a for a in leased)
    drafted = next(a for a in leased if a["action"] == "draft")
    answer = computer.action(dict(drafted, action="send", company={}))   # a send made up on stdin, past the draft
    assert answer["ok"] is False and "What Tico may do" in answer["detail"], answer
    assert computer.gmail.sent == []                      # at most a draft: Organize mail is still on
    drafts = len(computer.drafts())

    with api.app.state.store.transaction() as c:          # queue them again, then turn Organize mail off too
        c.execute("UPDATE automation_mail_actions SET state='queued', claimed_at=NULL, claimed_by=NULL, attempts=0")
    r = api.put("/api/v2/google/permissions", json={"organize_mail": False, "send_mail": False, "change_calendar": True},
                headers=headers())
    assert r.status_code == 200, r.text
    assert claim(api, computer.token)["actions"] == []
    for aid in (send, archive):
        step = last_run(api, aid)["steps"][0]
        assert step["status"] == "failed" and "What Tico may do" in json.dumps(step["outputs"]), step
    with api.app.state.store.transaction() as c:
        assert {r["state"] for r in c.execute("SELECT state FROM automation_mail_actions")} == {"failed"}
    assert "INBOX" in computer.gmail.store["m1"]["labelIds"] and len(computer.drafts()) == drafts
    assert computer.gmail.sent == []


def test_a_mail_action_that_cannot_read_what_tico_may_do_writes_nothing_and_is_retried(api, computer, monkeypatch):
    automate(api, ARCHIVE)
    computer.sync()
    action, = claim(api, computer.token)["actions"]

    def down(path):
        raise APIError("unavailable", "the hub is down", 503)
    monkeypatch.setattr(pl, "RUNNER_GET", down)
    answer = computer.action(action)
    assert (answer["ok"], answer["retry"]) == (False, True) and "couldn't check" in answer["detail"], answer
    assert "INBOX" in computer.gmail.store["m1"]["labelIds"]


def test_a_send_queued_with_the_switch_off_still_says_send_off_once_drafted(api, computer):
    """The switch was off when the run queued it: a draft from the start, and the computer's report keeps send_off."""
    aid = automate(api, SEND)
    assert api.patch(f"/api/v2/inboxes/{BOX}", json={"send": False}, headers=headers()).status_code == 200
    computer.sync()
    assert last_run(api, aid)["steps"][0]["outputs"]["send_off"] is True
    computer.worker.mail_action_tick()
    assert computer.gmail.sent == [] and len(computer.drafts()) == 1
    step = last_run(api, aid)["steps"][0]
    assert step["status"] == "ok" and step["outputs"]["detail"] == "send_off" and step["outputs"]["draft"]


def test_a_forward_claimed_after_the_switch_went_off_is_a_draft_to_its_target(api, computer):
    aid = automate(api, {"id": "fwd", "type": "action", "do": "forward",
                         "args": {"to": "ap@acme.example", "body": "For payment."}})
    computer.sync()
    assert api.patch(f"/api/v2/inboxes/{BOX}", json={"send": False}, headers=headers()).status_code == 200
    computer.worker.mail_action_tick()
    draft, = computer.drafts()
    assert computer.gmail.sent == [] and header(draft, "To") == "ap@acme.example"
    assert header(draft, "Subject") == "Fwd: Invoice 4471"
    step = last_run(api, aid)["steps"][0]
    assert step["status"] == "ok" and step["outputs"]["detail"] == "send_off"


def test_a_slack_reply_delivered_after_the_switch_went_off_is_held_as_a_draft_task(api):
    from backend import automation_actions as A
    from backend import slack_gateway as G
    from backend.tests.test_slack_gateway import FakeSlack
    store = api.app.state.store
    slack_msg = {"message": {"source": "slack", "inbox": "slack:#general", "channel": "C1", "ts": "1.1", "text": "Help?"}}
    auto = {"id": automate(api, NOTE), "flow": {"owner": "person:ana"}, "assistant_bot": "coo"}
    with store.transaction() as c:
        c.execute("INSERT INTO automation_runs(id,automation_id,version,started_at,status,saved_state) "
                  "VALUES('run_s',?,1,?,'ok',?)", (auto["id"], "2026-10-28T09:00:00Z", json.dumps({"status": "ok", "steps": []})))
        done = A.run(c, auto, "run_s", "send_reply", {"body": "On it", "bot": "ops"}, slack_msg)
        c.execute("UPDATE automation_runs SET saved_state=? WHERE id='run_s'", (json.dumps(
            {"status": "ok", "threads": [], "steps": [{"id": "reply", "type": "action", "status": "ok",
                                                         "outputs": {"do": "send_reply", **done}, "cost": 0}]}),))
        c.execute("INSERT INTO automation_inboxes VALUES('slack:#general',0,?,'human:ana')", ("2026-10-28T09:01:00Z",))
    slack = FakeSlack()
    G.Gateway(store, slack).deliver()
    assert slack.posts == []
    with store.read() as c:
        held = dict(c.execute("SELECT state, error FROM slack_posts").fetchone())
        task = c.execute("SELECT owner_id, title FROM tasks WHERE title LIKE 'Draft reply:%'").fetchone()
        state = json.loads(c.execute("SELECT saved_state FROM automation_runs WHERE id='run_s'").fetchone()[0])
    assert held == {"state": "canceled", "error": "send_off"} and dict(task) == {"owner_id": "human:ana",
                                                                                   "title": "Draft reply: On it"}
    assert state["steps"][0]["outputs"]["detail"] == "send_off"


NOTE = {"id": "tell", "type": "action", "do": "wake_bot", "args": {"bot": "ops", "text": "Look"}}


# ---------------------------------------------------------------------------------------------- review 2
def test_a_run_that_fails_cancels_its_unclaimed_mail_and_a_late_result_changes_nothing(api, computer):
    aid = automate(api, SEND)
    computer.sync()
    with api.app.state.store.read() as c:
        mid = c.execute("SELECT id FROM automation_mail_actions").fetchone()[0]
    from backend import automations
    # The mail computer is off for a day: the tick gives up on the run.
    automations.tick(api.app.state.store, datetime.now(timezone.utc) + timedelta(days=1, hours=1))
    run = last_run(api, aid)
    assert run["status"] == "failed" and run["steps"][0]["outputs"]["detail"] == "canceled: the run failed"
    assert claim(api, computer.token)["actions"] == []           # back online: nothing to send
    assert computer.worker.mail_action_tick() == 0 and computer.gmail.sent == []
    late = post(api, f"automations/mail-actions/{mid}/result", {"ok": True, "detail": "sent"}, computer.token)
    assert late["state"] == "canceled"


@pytest.mark.parametrize("ok", [True, False])
def test_a_late_mail_report_after_its_held_run_was_dropped_is_recorded_and_the_run_stays_skipped(api, computer, ok):
    from backend import automations
    aid = automate(api, SEND)
    computer.sync()
    action, = claim(api, computer.token)["actions"]   # out on its lease when the run is held and dropped
    with api.app.state.store.transaction() as c:
        automations._hold(c, dict(c.execute("SELECT * FROM automations WHERE id=?", (aid,)).fetchone()), "flood")
        c.execute("UPDATE automation_runs SET status='held' WHERE automation_id=?", (aid,))
    assert post(api, f"automations/{aid}/held", {"action": "drop"})["moved"] == 1
    late = post(api, f"automations/mail-actions/{action['id']}/result",
                {"ok": ok, "detail": "sent" if ok else "no such thread", "retry": False}, computer.token)
    assert late["state"] == ("done" if ok else "failed")
    run = last_run(api, aid)
    assert run["status"] == "skipped" and run["finished_at"]
    step = run["steps"][0]
    assert step["status"] == ("ok" if ok else "failed") and step["outputs"]["ok"] is ok
    assert step["outputs"]["detail"] == ("sent" if ok else "no such thread")


def test_a_lease_that_runs_out_after_its_run_failed_is_canceled_not_queued(api, computer):
    automate(api, SEND)
    computer.sync()
    action, = claim(api, computer.token)["actions"]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE automation_runs SET status='failed'")
    with later(6):
        assert claim(api, computer.token)["actions"] == []
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM automation_mail_actions WHERE id=?", (action["id"],)).fetchone()[0] == \
            "canceled"


ARCHIVE = {"id": "gate", "type": "filter", "rules": [{"when": [], "then": [{"do": "archive"}]}]}


def test_a_lease_another_computer_let_run_out_is_not_run_again_unless_the_action_is_repeatable(api, computer):
    send = automate(api, SEND)
    automate(api, ARCHIVE)
    computer.sync()
    claimed = {action["action"]: action for action in claim(api, computer.token)["actions"]}
    assert set(claimed) == {"send", "archive"}                 # both leased to the first computer, which never answers
    second = runner(api, label="Second Mac")
    post(api, "connectors/mail/messages", {"mailbox": BOX, "messages": [], "deleted": [], "synced_at": H.now()},
         second["token"])                                      # the second computer now reads the mailbox
    with later(6):
        again, = claim(api, second["token"])["actions"]
        assert (again["action"], again["attempt"]) == ("archive", 2)   # a label change is safe to run again
    step = last_run(api, send)["steps"][0]
    assert step["status"] == "failed" and "not run again" in step["outputs"]["detail"]
    assert computer.gmail.sent == []                           # the send was never carried out on either computer


def test_no_computer_claims_or_reports_for_a_mailbox_not_published_yet(api, computer):
    claim(api, computer.token, expected=403)
    with api.app.state.store.transaction() as c:
        owned_by_ana(c, "auto_1")
        c.execute("INSERT INTO automation_mail_actions(id,automation_id,mailbox,msg_id,action,args,state,created_at) "
                  "VALUES('ma_1','auto_1',?,'m1','send','{}','queued',?)", (BOX, "2026-10-28T09:00:00Z"))
    post(api, "automations/mail-actions/ma_1/result", {"ok": True, "detail": "sent"}, computer.token, expected=403)
    computer.sync()                                          # published: now it is this computer's
    assert len(claim(api, computer.token)["actions"]) == 1


# ---------------------------------------------------------------------------------------------- mail fields, end to end
def test_the_sync_carries_the_mail_fields_to_the_trigger(api, computer):
    """The real sync and publish: the owner's earlier send makes the sender known to a later message only; a category
    label, a calendar part and an .ics file come through the hub copy as the trigger reads them."""
    # The first sync acts on the last 24 hours of mail, so these times stay inside that window, in this order.
    now, hour, person = int(time.time() * 1000), 3_600_000, "person@customer.example"
    store = computer.gmail.store
    store["sent-1"] = fake.message("sent-1", "t-person", {"From": BOX, "To": person, "Subject": "Pricing"},
                                   body="Here it is.", labels=("SENT",), epoch_ms=now - 10 * hour)
    store["early"] = fake.message("early", "t-early", {"From": person, "To": BOX, "Subject": "Hello"},
                                  body="Hi.", epoch_ms=now - 20 * hour)
    store["reply"] = fake.message("reply", "t-person", {"From": person, "To": BOX, "Subject": "Re: Pricing"},
                                  body="Thanks.", epoch_ms=now - 2 * hour)
    store["promo"] = fake.message("promo", "t-promo", {"From": "Deals <deals@shop.example>", "To": BOX,
                                                       "Subject": "Sale"}, body="50% off.",
                                  labels=("INBOX", "CATEGORY_PROMOTIONS"), epoch_ms=now - 2 * 3_600_000)
    store["invite"] = fake.message("invite", "t-invite", {"From": "Ben <ben@partner.example>", "To": BOX,
                                                         "Subject": "Invite"},
                                   parts=[fake.part("text/plain", "Join us."),
                                          fake.part("text/calendar", "BEGIN:VCALENDAR\r\nEND:VCALENDAR")],
                                   labels=("INBOX", "CATEGORY_SOCIAL"), epoch_ms=now - 3_600_000)
    store["ics"] = fake.message("ics", "t-ics", {"From": "Cal <cal@vendor.example>", "To": BOX, "Subject": "Meeting"},
                                parts=[fake.part("text/plain", "See attached."),
                                       fake.part("application/octet-stream", filename="meeting.ics", size=120)],
                                epoch_ms=now - 1_800_000)
    aid = automate(api, {"id": "sort", "type": "filter", "rules": [{"when": [{"field": "message.subject",
                                                                             "op": "contains", "value": "zz-never"}],
                                                                  "then": [{"do": "archive"}]}]})
    assert computer.sync() == len(store)
    with api.app.state.store.read() as c:
        seen = {r["trigger_ref"].rsplit(":", 1)[-1]: json.loads(r["saved_state"])["payload"]["message"]
                for r in c.execute("SELECT trigger_ref, saved_state FROM automation_runs WHERE automation_id=?", (aid,))}
    assert set(seen) == set(store)
    assert seen["reply"]["known_sender"] is True and seen["reply"]["from_domain"] == "customer.example"
    assert seen["early"]["known_sender"] is False            # the owner wrote to this sender only after it arrived
    assert seen["sent-1"]["known_sender"] is False
    assert seen["promo"]["category"] == "promotions" and seen["promo"]["is_calendar_invite"] is False
    assert seen["invite"]["category"] == "social" and seen["invite"]["is_calendar_invite"] is True
    assert seen["ics"]["is_calendar_invite"] is True and seen["ics"]["attachments"] == ["meeting.ics"]


# ---------------------------------------------------------------------------------------------- unsubscribe
UNSUB_URL = "https://news.example.org/unsub?token=abc"
UNSUBSCRIBE = {"id": "unsub", "type": "action", "do": "unsubscribe", "args": {}}
UNSUB_THEN_ARCHIVE = {"id": "gate", "type": "filter", "rules": [{"when": [], "then": [
    {"do": "unsubscribe"}, {"do": "archive"}]}]}
# A new-shaped flow: the unsubscribe is the rule's own action, the archive its destination's.
UNSUB_TO_TIDY = {"id": "gate", "type": "filter", "rules": [
    {"when": [], "then": [{"do": "unsubscribe"}], "to": "tidy"}]}
TIDY = [{"id": "tidy", "name": "Tidy", "then": [{"do": "archive"}]}]


@pytest.fixture
def unsubscriber(monkeypatch):
    """The sender's one-click endpoint: every POST is recorded and answered 202. The message's own link is given, since
    the fake Gmail keeps no raw copy to find it in; nobody is on the people roster."""
    posts = []
    monkeypatch.setattr(mail_actions, "_one_click_url", lambda ctx, mid: UNSUB_URL)
    monkeypatch.setattr(unsub, "post", lambda url: posts.append(url) or 202)
    monkeypatch.setattr(access, "roster_mailboxes", lambda roster=None: [])
    return posts


def test_unsubscribe_runs_on_the_mailboxs_computer_and_keeps_the_mail_in_the_inbox(api, computer, unsubscriber):
    aid = automate(api, UNSUBSCRIBE)
    computer.sync()
    assert last_run(api, aid)["status"] == "waiting"
    assert computer.worker.mail_action_tick() == 1
    assert unsubscriber == [UNSUB_URL]
    assert "INBOX" in computer.gmail.store["m1"]["labelIds"]
    run = last_run(api, aid)
    assert run["status"] == "ok" and run["steps"][0]["outputs"]["detail"] == "unsubscribed"
    assert computer.worker.mail_action_tick() == 0 and len(unsubscriber) == 1


def test_the_computer_runs_an_unsubscribe_then_an_archive_in_one_pass(api, computer, unsubscriber):
    aid = automate(api, UNSUB_THEN_ARCHIVE)
    computer.sync()
    assert computer.worker.mail_action_tick() == 2
    assert unsubscriber == [UNSUB_URL]
    assert "INBOX" not in computer.gmail.store["m1"]["labelIds"]
    run = last_run(api, aid)
    assert run["status"] == "ok"
    assert [a["detail"] for a in run["steps"][0]["outputs"]["actions"]] == ["unsubscribed", "archived"]


def test_a_refused_unsubscribe_fails_the_run_and_the_archive_after_it_never_runs(api, computer, unsubscriber):
    aid = automate(api, UNSUB_THEN_ARCHIVE)
    computer.gmail.store["m1"]["labelIds"] = ["INBOX", "UNREAD", "STARRED"]
    computer.sync()
    assert computer.worker.mail_action_tick() == 1
    assert unsubscriber == [] and "INBOX" in computer.gmail.store["m1"]["labelIds"]
    assert claim(api, computer.token)["actions"] == []
    with api.app.state.store.read() as c:
        assert [r[0] for r in c.execute("SELECT state FROM automation_mail_actions ORDER BY created_at, rowid")] == [
            "failed", "skipped"]
    run = last_run(api, aid)
    assert run["status"] == "failed" and run["steps"][0]["status"] == "failed"
    assert [a["detail"] for a in run["steps"][0]["outputs"]["actions"]] == [
        "not unsubscribing: the message is starred", "skipped: an earlier action on this message failed"]


def test_a_chain_is_claimed_one_action_at_a_time_and_in_order(api, computer, unsubscriber):
    aid = automate(api, UNSUB_THEN_ARCHIVE)
    computer.sync()
    first, = claim(api, computer.token)["actions"]
    assert first["action"] == "unsubscribe"
    assert claim(api, computer.token)["actions"] == []        # the archive waits: the unsubscribe is out, not done
    post(api, f"automations/mail-actions/{first['id']}/result", {"ok": True, "detail": "unsubscribed"}, computer.token)
    second, = claim(api, computer.token)["actions"]
    assert second["action"] == "archive"
    post(api, f"automations/mail-actions/{second['id']}/result", {"ok": True, "detail": "archived"}, computer.token)
    run = last_run(api, aid)
    assert run["status"] == "ok"
    assert [a["detail"] for a in run["steps"][0]["outputs"]["actions"]] == ["unsubscribed", "archived"]


def test_a_chained_unsubscribe_whose_lease_runs_out_is_claimed_again(api, computer, unsubscriber):
    aid = automate(api, UNSUB_THEN_ARCHIVE)
    computer.sync()
    first, = claim(api, computer.token)["actions"]
    with later(6):
        again, = claim(api, computer.token)["actions"]
    assert (again["id"], again["action"]) == (first["id"], "unsubscribe")
    assert claim(api, computer.token)["actions"] == []        # the archive still waits for it
    assert last_run(api, aid)["status"] == "waiting"


def test_a_chained_unsubscribe_that_never_reports_fails_the_run_and_skips_the_archive(api, computer, unsubscriber):
    aid = automate(api, UNSUB_THEN_ARCHIVE)
    computer.sync()
    for n in range(3):
        with later(6 * n):
            assert [a["action"] for a in claim(api, computer.token)["actions"]] == ["unsubscribe"]
    with later(18):
        assert claim(api, computer.token)["actions"] == []
    run = last_run(api, aid)
    assert run["status"] == "failed"
    assert [a["detail"] for a in run["steps"][0]["outputs"]["actions"]] == [
        "no result after 3 tries", "skipped: an earlier action on this message failed"]
    assert unsubscriber == []


def test_a_new_shaped_rules_unsubscribe_chains_its_destinations_archive_after_it(api, computer, unsubscriber):
    """The rule's unsubscribe and its destination's archive are one step's actions on one message, so the archive is
    claimed only once the unsubscribe reports ok, and the run records both in order."""
    aid = automate(api, UNSUB_TO_TIDY, destinations=TIDY)
    computer.sync()
    first, = claim(api, computer.token)["actions"]
    assert first["action"] == "unsubscribe"
    assert claim(api, computer.token)["actions"] == []        # the archive waits: the unsubscribe is out, not done
    post(api, f"automations/mail-actions/{first['id']}/result", {"ok": True, "detail": "unsubscribed"}, computer.token)
    second, = claim(api, computer.token)["actions"]
    assert second["action"] == "archive"
    post(api, f"automations/mail-actions/{second['id']}/result", {"ok": True, "detail": "archived"}, computer.token)
    run = last_run(api, aid)
    assert run["status"] == "ok"
    assert [(a["detail"], a["from"]) for a in run["steps"][0]["outputs"]["actions"]] == [
        ("unsubscribed", "rule"), ("archived", "destination")]


def test_a_chain_longer_than_the_recursion_limit_settles_and_the_mailbox_still_claims(api, computer):
    """1,100 actions chained on one message: a failed head skips all of them (the walk is a work list, not recursion),
    and the mailbox's next claim is not held up by them."""
    computer.sync()
    with api.app.state.store.transaction() as c:
        owned_by_ana(c, "auto_x")
        for i in range(1100):
            c.execute('INSERT INTO automation_mail_actions(id,automation_id,run_id,mailbox,msg_id,action,args,state,'
                      'created_at,attempts,step,"after") VALUES(?,?,?,?,?,?,?,?,?,0,?,?)',
                      (f"chain_{i:04d}", "auto_x", "run_x", BOX, "m1", "archive", "{}", "queued",
                       f"2026-10-28T00:00:00.{i:06d}Z", "gate", f"chain_{i - 1:04d}" if i else None))
    head, = claim(api, computer.token)["actions"]
    assert head["id"] == "chain_0000"
    post(api, f"automations/mail-actions/{head['id']}/result", {"ok": False, "detail": "refused", "retry": False},
         computer.token)
    with api.app.state.store.read() as c:
        states = {r[0]: r[1] for r in c.execute(
            "SELECT state, COUNT(*) FROM automation_mail_actions WHERE automation_id='auto_x' GROUP BY state")}
    assert states == {"failed": 1, "skipped": 1099}
    with api.app.state.store.transaction() as c:
        owned_by_ana(c, "auto_y")
        c.execute("INSERT INTO automation_mail_actions(id,automation_id,run_id,mailbox,msg_id,action,args,state,"
                  "created_at,attempts) VALUES('free','auto_y',NULL,?,'m1','archive','{}','queued',"
                  "'2026-10-28T01:00:00.000000Z',0)", (BOX,))
    assert [a["id"] for a in claim(api, computer.token)["actions"]] == ["free"]

def test_two_rules_that_fired_and_a_destinations_mail_action_settle_into_one_record(api, computer):
    gate = {"id": "gate", "type": "filter", "rules": [
        {"when": [], "then": [{"do": "label", "args": {"tag": "billing"}}], "next": "rule"},
        {"when": [], "then": [], "to": "legal"}]}
    aid = automate(api, gate, destinations=[{"id": "legal", "name": "Legal", "then": [{"do": "leave_for_you"}]}])
    computer.sync()
    assert last_run(api, aid)["steps"][0]["status"] == "waiting"
    assert computer.worker.mail_action_tick() == 2          # both queued, claimed in order, one tick
    labels = {computer.gmail.name_of(lid) for lid in computer.gmail.store["m1"]["labelIds"]}
    assert {"hub/billing", "hub/needs-owner", "INBOX"} <= labels   # leave for you labels and keeps the message in INBOX
    run = last_run(api, aid)
    assert run["status"] == "ok" and run["steps"][0]["status"] == "ok"
    out = run["steps"][0]["outputs"]
    assert out["rules"] == [0, 1]
    assert [(a["detail"], a["from"]) for a in out["actions"]] == [
        ("labelled hub/billing", "rule"), ("left for you", "destination")]
    assert computer.worker.mail_action_tick() == 0          # nothing left, nothing done twice


# ---------------------------------------------------------------------------------------------- a choose turn's reply
CHOOSE = {"to": ["legal", "sales"], "default": "sales", "mode": "draft"}
ROUTES = [{"id": "legal", "name": "Legal", "then": [{"do": "wake_bot", "args": {"bot": "coo", "text": "Routed to legal."}}]},
          {"id": "sales", "name": "Sales", "then": []}]


def choosing_bot(choose):
    """A bot's flow on the inbox: the bot's turn answers `choose`, and the reply goes out through the mail computer."""
    return {"name": "Invoices", "owner": "bot:ops", "trigger": {"type": "message", "inbox": BOX}, "destinations": ROUTES,
            "steps": [{"id": "draft", "type": "llm", "as": "bot:ops", "prompt": "Reply to {{message.from}}.",
                       "out": "reply", "choose": choose}]}


def bot_ops(api):
    r = runner(api, label="Bot Mac")
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    # ana let ops use her mailbox (Settings > Inboxes): a bot's automation acts on a mailbox it was given.
    from backend import mailbox_access
    with api.app.state.store.transaction() as c:
        bot = H.resolve_bot(c, "ops")["id"]
        mailbox_access.give(c, mailbox_access.ensure_mailbox(c, BOX), "bot", bot, "read_write", "human:ana")
    return r


def test_a_choose_answer_drafts_the_reply_in_the_thread_and_runs_the_chain(api, computer):
    r = bot_ops(api)
    aid = post(api, "automations", choosing_bot(CHOOSE))["id"]
    computer.sync()
    assert last_run(api, aid)["status"] == "waiting"           # the bot's turn is in its own task
    body = "Sending the deck. {{message.body}}"                 # the body is the reply as written
    finish(api, r, json.dumps({"to": "legal", "body": body}))
    assert computer.worker.mail_action_tick() == 1
    draft, = computer.drafts()
    assert body in fake.base64.urlsafe_b64decode(draft["payload"]["body"]["data"] + "==").decode()
    run = last_run(api, aid)
    assert run["status"] == "ok"
    out = run["steps"][0]["outputs"]
    assert out["to"] == "legal"
    assert [(a["do"], a["ok"]) for a in out["actions"]] == [("draft_reply", True), ("wake_bot", True)]


def test_reply_mode_with_the_send_switch_off_saves_a_draft(api, computer):
    r = bot_ops(api)
    aid = post(api, "automations", choosing_bot({**CHOOSE, "mode": "reply"}))["id"]
    assert api.patch(f"/api/v2/inboxes/{BOX}", json={"send": False}, headers=headers()).status_code == 200
    computer.sync()
    finish(api, r, json.dumps({"to": "sales", "body": "Thanks, here is the deck."}))
    assert computer.worker.mail_action_tick() == 1
    assert computer.gmail.sent == [] and len(computer.drafts()) == 1
    assert last_run(api, aid)["steps"][0]["outputs"]["actions"][0]["detail"] == "send_off"


def test_change_calendar_off_refuses_a_booking_and_fails_what_was_queued_at_claim(api, computer):
    """What Tico may do holds the calendar on the server too: no booking is queued while Change calendar is off, and one
    queued before it went off fails at the claim instead of landing on the calendar later."""
    booking = {"calendar": BOX, "title": "Planning", "start": "2099-01-01T09:00:00+00:00",
               "end": "2099-01-01T09:30:00+00:00", "attendees": []}
    queued = post(api, "calendar/appointments", booking)["action"]
    r = api.put("/api/v2/google/permissions", json={"organize_mail": True, "send_mail": True, "change_calendar": False},
                headers=headers())
    assert r.status_code == 200, r.text
    refused = api.post("/api/v2/calendar/appointments", json=booking, headers=headers())
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "company_off", refused.text
    assert post(api, "connectors/calendar/actions/claim", {}, computer.token) == {"action": None}
    with api.app.state.store.read() as c:
        row = c.execute("SELECT status,error FROM calendar_actions WHERE id=?", (queued["id"],)).fetchone()
        assert row["status"] == "failed" and "What Tico may do" in row["error"]
        assert c.execute("SELECT count(*) FROM calendar_actions").fetchone()[0] == 1


def test_the_runner_s_own_commands_need_no_owner_proof_wherever_its_registration_lives(api, computer):
    """Sync, a mail action's lease and a calendar booking's claim run as the owner with the registration at
    TICO_RUNNER_CONFIG (the fixture): each reads What Tico may do from the hub and nothing else about who runs it."""
    automate(api, ARCHIVE)
    computer.sync()
    assert computer.worker.mail_action_tick() >= 1
    assert "INBOX" not in computer.gmail.store["m1"]["labelIds"]
    booking = {"calendar": BOX, "title": "Planning", "start": "2099-01-01T09:00:00+00:00",
               "end": "2099-01-01T09:30:00+00:00", "attendees": []}
    queued = post(api, "calendar/appointments", booking)["action"]
    assert computer.worker.calendar_action_tick() == 1
    assert len(computer.calendar.created_events) == 1
    with api.app.state.store.read() as c:
        assert c.execute("SELECT status FROM calendar_actions WHERE id=?", (queued["id"],)).fetchone()[0] == "succeeded"


def test_completed_silent_calendar_hold_does_not_revoke_its_approved_invitation(api, computer):
    booking = {"calendar": BOX, "title": "Planning", "start": "2099-01-01T09:00:00+00:00",
               "end": "2099-01-01T09:30:00+00:00", "attendees": ["ben@acme.example"]}
    queued = post(api, "calendar/appointments", booking)["action"]
    assert computer.worker.calendar_action_tick() == 1
    assert computer.gmail.sent == []
    with api.app.state.store.read() as c:
        assert c.execute("SELECT status FROM calendar_actions WHERE id=?", (queued["id"],)).fetchone()[0] == "succeeded"
        send = c.execute("SELECT * FROM email_sends WHERE calendar_action_id=?", (queued["id"],)).fetchone()
        assert send is not None and send["state"] == "waiting_approval"
    approve_and_dispatch(api, computer, send["id"])
    assert len(computer.gmail.sent) == 1
    assert "text/calendar" in str(computer.sent()[0])


def test_hub_conference_preparation_failure_returns_a_retryable_non_send(api, computer):
    from connectors.mail import calendar
    booking = {"calendar": BOX, "title": "Planning", "start": "2099-01-01T09:00:00+00:00",
               "end": "2099-01-01T09:30:00+00:00", "attendees": ["ben@acme.example"]}
    queued = post(api, "calendar/appointments", booking)["action"]
    with mock.patch.object(calendar.Calendar, "ready_conference", side_effect=calendar.ConferencePending("Meeting link pending", status=503)), \
            mock.patch.object(pl, "queue_send") as send:
        assert computer.worker.calendar_action_tick() == 1
    send.assert_not_called()
    result = get(api, "calendar/actions/" + queued["id"])["action"]
    assert result["status"] == "failed" and "Retry scheduling" in result["error"]
    assert result["result"]["event_id"] and all(event["sendUpdates"] == "none" for event in computer.calendar.created_events.values())
    retried = post(api, "calendar/appointments", booking)["action"]
    assert retried["id"] != queued["id"]
    assert computer.worker.calendar_action_tick() == 1
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM email_sends WHERE calendar_action_id=?", (retried["id"],)).fetchone()[0] == "waiting_approval"
