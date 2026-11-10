"""Helpers that other tests share, moved out of test_automation_mail_actions.py so they import no test file."""

import contextlib
import io
import json
import time
from datetime import datetime, timedelta, timezone
from unittest import mock
import pytest
import time_machine
from backend.store import H
from backend.tests.helpers.api import get, headers, post, runner
from clients.tico import APIError
from connectors.mail import access, audit as audit_log, db, gmail as gm, policy as pl
from connectors.mail import __main__ as cli
from connectors.mail import oauth
from connectors.mail.tests import fake
from runner_support.connectors import CalendarRefused, CalendarInvitationPending, ConnectorPublisher


BOX = "ana@acme.example"


INVOICE = {
    "From": "Billing <billing@vendor.example>",
    "To": BOX,
    "Subject": "Invoice 4471",
    "Message-ID": "<inv-4471@vendor.example>",
    "Date": "Tue, 27 Oct 2026 09:00:00 -0700",
}


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
        self.gmail = fake.FakeGmailService(
            [
                fake.message(
                    "m1", "t1", INVOICE, body="Please pay invoice 4471.", epoch_ms=int(time.time() * 1000) - 3_600_000
                )
            ]
        )
        self.worker = ConnectorPublisher(
            {
                "url": "http://localhost:8765",
                "token": "unused",
                "owner_handle": access.OWNER,
                "projects_dir": str(tmp_path),
            },
            client=Hub(api, token),
            mail=self.mail,
            action=self.action,
            event=self.event,
        )
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
    monkeypatch.setattr(
        access,
        "RUNNER_ACCESS",
        lambda: {
            **box.worker.client.get("connectors/mail/targets"),
            "mailboxes": [{"address": a} for a in box.worker.mail_boxes],
        },
    )
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
    return post(
        api,
        "automations",
        {
            "name": "Invoices",
            "owner": "person:ana",
            "trigger": {"type": "message", "inbox": BOX},
            "steps": list(steps),
            **extra,
        },
    )["id"]


def owned_by_ana(c, *ids):
    """Automation rows for actions a test queues by hand: the claim checks each action's owner holds the mailbox."""
    now = H.now()
    for aid in ids:
        c.execute(
            "INSERT OR IGNORE INTO automations(id,name,owner_id,trigger_type,enabled,flow,version,created_at,"
            "created_by_id,updated_at) VALUES(?,?,?,'manual',0,?,1,?,?,?)",
            (aid, aid, "person:ana", json.dumps({"owner": "person:ana", "steps": []}), now, "person:ana", now),
        )


def last_run(api, aid):
    return get(api, f"automations/{aid}/runs")["runs"][0]


def claim(api, token, expected=200):
    return post(api, "automations/mail-actions/claim", {"mailbox": BOX}, token, expected=expected)


def later(minutes):
    return time_machine.travel(datetime.now(timezone.utc) + timedelta(minutes=minutes), tick=False)
