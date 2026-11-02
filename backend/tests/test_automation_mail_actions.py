"""Mail actions an automation queued, carried out by the computer that reads the mailbox: the real mail sync publishes
a message, the automation queues its actions, the connector worker (runner/connectors.py) claims them and runs the
mail CLI (connectors/mail/actions.py) against a fake Gmail, and the run's steps wait until the results come back.
Also: the lease and its retries never send twice, only that mailbox's computer may claim, and the send switch is read
again when a send leaves (mail and the Slack outbox)."""
import contextlib
import io
import json
import time
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest
import time_machine

from backend.tests.test_api import api, get, headers, post, runner  # noqa: F401
from clients.tico import APIError
from connectors.mail import access, audit as audit_log, db, gmail as gm
from connectors.mail import __main__ as cli
from connectors.mail.tests import fake
from runner.connectors import ConnectorPublisher

BOX = "ana@acme.example"
INVOICE = {"From": "Billing <billing@vendor.example>", "To": BOX, "Subject": "Invoice 4471",
           "Message-ID": "<inv-4471@vendor.example>", "Date": "Tue, 27 Oct 2026 09:00:00 -0700"}
DRAFT = {"id": "draft", "type": "action", "do": "draft_reply", "args": {"body": "Draft: checking with finance."}}
SEND = {"id": "reply", "type": "action", "do": "send_reply", "args": {"body": "Thanks, we will pay this week."}}


class Hub:
    """runner/connectors.py's client, over the test app with this computer's token."""

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
                                         client=Hub(api, token), mail=self.mail, action=self.action)
        self.worker.mail_boxes = [BOX]

    def mail(self, args):
        rc, answer = run_cli(args)
        assert rc == 0, answer
        return answer

    def action(self, action):
        return run_cli(["mail-action", "--as", access.OWNER, "--json"], json.dumps(action))[1]

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
    box.token = machine["token"]
    return box


def automate(api, *steps):
    return post(api, "automations", {"name": "Invoices", "owner": "person:ana",
                                     "trigger": {"type": "message", "inbox": BOX}, "steps": list(steps)})["id"]


def last_run(api, aid):
    return get(api, f"automations/{aid}/runs")["runs"][0]


def header(msg, name):
    return fake.FakeGmailService.header(msg, name)


def claim(api, token, expected=200):
    return post(api, "automations/mail-actions/claim", {"mailbox": BOX}, token, expected=expected)


def later(minutes):
    return time_machine.travel(datetime.now(timezone.utc) + timedelta(minutes=minutes), tick=False)


# ---------------------------------------------------------------------------------------------- the whole loop
def test_mail_in_runs_the_automation_and_its_computer_drafts_and_replies_in_the_thread(api, computer):
    aid = automate(api, DRAFT, SEND)
    assert computer.sync() == 1
    run = last_run(api, aid)
    # Queued, not done: the steps and the run wait for the mail computer.
    assert run["status"] == "waiting" and run["finished_at"] is None
    assert [(s["status"], s["outputs"]["queued"]) for s in run["steps"]] == [("waiting", True), ("waiting", True)]
    assert computer.worker.mail_action_tick() == 2
    draft, = computer.drafts()
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
    sent, = computer.sent()
    text = fake.base64.urlsafe_b64decode(sent["payload"]["body"]["data"] + "==").decode()
    assert header(sent, "To") == "ap@acme.example" and header(sent, "Subject") == "Fwd: Invoice 4471"
    assert text.startswith("For payment.") and "Please pay invoice 4471." in text
    assert last_run(api, aid)["status"] == "ok"


# ---------------------------------------------------------------------------------------------- lease and retries
def test_a_lost_claim_and_a_lost_result_never_send_twice(api, computer):
    aid = automate(api, SEND)
    computer.sync()
    lost = claim(api, computer.token)["actions"]             # the answer never reached the computer
    assert len(lost) == 1 and claim(api, computer.token)["actions"] == []    # leased: nobody else gets it
    with later(6):
        again, = claim(api, computer.token)["actions"]       # the lease ran out: back in the queue
        assert again["id"] == lost[0]["id"] and again["attempt"] == 2
        done = computer.action(again)                        # sent, but the report is lost
        assert done["detail"] == "sent" and len(computer.gmail.sent) == 1
    with later(12):
        third, = claim(api, computer.token)["actions"]
        assert computer.action(third)["detail"].startswith("already sent")
        assert len(computer.gmail.sent) == 1
        ok = {"ok": True, "detail": "sent", "message": done["message"]}
        url = f"automations/mail-actions/{third['id']}/result"
        assert post(api, url, ok, computer.token) == {"id": third["id"], "state": "done"}
        # A repeated report (its answer lost too) changes nothing.
        assert post(api, url, {"ok": False, "detail": "late"}, computer.token) == {"id": third["id"], "state": "done"}
    step = last_run(api, aid)["steps"][0]
    assert step["status"] == "ok" and step["outputs"]["message"] == done["message"]


def test_a_send_whose_answer_was_lost_inside_the_cli_is_not_sent_again(api, computer):
    """The send went out but the CLI died before it recorded so: the row is pending and the draft is gone."""
    automate(api, SEND)
    computer.sync()
    action, = claim(api, computer.token)["actions"]
    real = gm.Gmail.send_draft

    def sent_then_crash(self, draft_id):
        real(self, draft_id)
        raise gm.Failure("connection reset after the send", status=503)
    with mock.patch.object(gm.Gmail, "send_draft", sent_then_crash):
        answer = computer.action(action)
    assert answer["ok"] is False and answer["retry"] is True and len(computer.gmail.sent) == 1
    assert computer.action(action)["detail"].startswith("already sent")
    assert len(computer.gmail.sent) == 1


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
        c.execute("INSERT INTO automation_runs(id,automation_id,version,started_at,status,state) "
                  "VALUES('run_s',?,1,?,'ok',?)", (auto["id"], "2026-10-28T09:00:00Z", json.dumps({"status": "ok", "steps": []})))
        done = A.run(c, auto, "run_s", "send_reply", {"body": "On it", "bot": "ops"}, slack_msg)
        c.execute("UPDATE automation_runs SET state=? WHERE id='run_s'", (json.dumps(
            {"status": "ok", "threads": [], "steps": [{"id": "reply", "type": "action", "status": "ok",
                                                         "outputs": {"do": "send_reply", **done}, "cost": 0}]}),))
        c.execute("INSERT INTO automation_inboxes VALUES('slack:#general',0,?,'human:ana')", ("2026-10-28T09:01:00Z",))
    slack = FakeSlack()
    G.Gateway(store, slack).deliver()
    assert slack.posts == []
    with store.read() as c:
        held = dict(c.execute("SELECT state, error FROM slack_posts").fetchone())
        task = c.execute("SELECT owner, title FROM tasks WHERE title LIKE 'Draft reply:%'").fetchone()
        state = json.loads(c.execute("SELECT state FROM automation_runs WHERE id='run_s'").fetchone()[0])
    assert held == {"state": "cancelled", "error": "send_off"} and dict(task) == {"owner": "human:ana",
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
    assert run["status"] == "failed" and run["steps"][0]["outputs"]["detail"] == "cancelled: the run failed"
    assert claim(api, computer.token)["actions"] == []           # back online: nothing to send
    assert computer.worker.mail_action_tick() == 0 and computer.gmail.sent == []
    late = post(api, f"automations/mail-actions/{mid}/result", {"ok": True, "detail": "sent"}, computer.token)
    assert late["state"] == "cancelled"


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


def test_a_lease_that_runs_out_after_its_run_failed_is_cancelled_not_queued(api, computer):
    automate(api, SEND)
    computer.sync()
    action, = claim(api, computer.token)["actions"]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE automation_runs SET status='failed'")
    with later(6):
        assert claim(api, computer.token)["actions"] == []
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM automation_mail_actions WHERE id=?", (action["id"],)).fetchone()[0] == \
            "cancelled"


def test_no_computer_claims_or_reports_for_a_mailbox_not_published_yet(api, computer):
    claim(api, computer.token, expected=403)
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO automation_mail_actions(id,automation_id,mailbox,msg_id,action,args,state,created_at) "
                  "VALUES('ma_1','auto_1',?,'m1','send','{}','queued',?)", (BOX, "2026-10-28T09:00:00Z"))
    post(api, "automations/mail-actions/ma_1/result", {"ok": True, "detail": "sent"}, computer.token, expected=403)
    computer.sync()                                          # published: now it is this computer's
    assert len(claim(api, computer.token)["actions"]) == 1
