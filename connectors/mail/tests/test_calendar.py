"""Slots, scheduling, and the two things that must never happen:

  - a confirmation goes out and the invite does not (the event is rolled back if the send fails)
  - `doctor --e2e` sends real mail from a real mailbox (it only ever touches the sandbox)
"""

import io, json, os, sys, unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake, harness                                                 # noqa: E402
from harness import GOOD_BODY, Stage2                                # noqa: E402

from connectors.mail import calendar as cal, db, zone                # noqa: E402

TZ = zone("America/Los_Angeles")
NOW = datetime(2026, 9, 2, 10, 0, tzinfo=TZ)                # Wednesday 2026-09-02, 10:00 PT
AVA = "ava@creator.example"
INCOMING = {"From": "Ava Reyes <ava@creator.example>", "To": "ana@acme.example",
            "Subject": "Re: your note", "Message-ID": "<ava-1@creator.example>",
            "Date": "Tue, 02 Sep 2026 09:00:00 -0700"}


def span(day, h1, m1, h2, m2):
    return (datetime(2026, 9, day, h1, m1, tzinfo=TZ), datetime(2026, 9, day, h2, m2, tzinfo=TZ))


class PrivateCalendarHold(Stage2):
    manifests = {
        **harness.MANIFESTS,
        "legal": """
name: legal
access:
  - service: gmail
    identity: "ana@acme.example"
    can: [read]
    read_only: true
  - service: google-calendar
    identity: "ana@acme.example"
    can: [read, draft, schedule]
"""
    }
    def setUp(self):
        super().setUp()
        fake.MAIL_LEVELS["ana"] = {"ana@acme.example": "read_write"}     # her own mailbox, as Tico answers it

    def hold_with_outside_guest(self):
        return self.run_json("hold", "--as", "ana", "--for", "ana@acme.example",
                             "--start", "2026-09-22T09:00:00-07:00",
                             "--minutes", "30", "--summary", "Murphy planning hold",
                             "--attendee", "outside@example.com",
                             "--json")

    def test_hold_may_invite_an_outside_guest_by_default(self):
        rc, out, err = self.hold_with_outside_guest()
        self.assertEqual(rc, 0, err)
        self.assertEqual(len(self.calendar.created_events), 1)

    def test_hold_refuses_an_outside_guest_when_external_invites_are_blocked(self):
        with patch.dict("os.environ", {"TICO_BLOCK_EXTERNAL_INVITES": "1"}):
            rc, out, err = self.hold_with_outside_guest()
        self.assertEqual(rc, 2)
        self.assertEqual(len(self.calendar.created_events), 0)

class Scheduling(Stage2):
    def corpus(self):
        return [fake.message("m-ava", "t-ava", INCOMING,
                             body="Can we talk?", epoch_ms=1788364800000)]

    def when(self):
        """The next weekday at 13:00 PT, at least two days out - always a valid slot."""
        d = datetime.now(TZ).replace(hour=13, minute=0, second=0, microsecond=0)
        d += timedelta(days=2)
        while d.weekday() >= 5:
            d += timedelta(days=1)
        return d

    def confirmation(self, when):
        return (f"{when.strftime('%a %b')} {when.day} at 1:00pm PT works. Invite is on its "
                "way.\n\nLooking forward to chatting.\n\nAna\n")

    def schedule(self, *extra, slug="influencer", body=None):
        when = self.when()
        f = self.body_file(body if body is not None else self.confirmation(when))
        return self.run_json("schedule", "--as", slug, "--thread", "t-ava", "--slot",
                             when.isoformat(), "--minutes", "20", "--attendee", AVA,
                             "--body-file", f, "--issue", "42", *extra)

    def test_a_bot_without_the_send_verb_puts_nothing_on_the_calendar_at_all(self):
        rc, p, err = self.schedule(slug="inbox")
        self.assertEqual(rc, 0, err)
        self.assertFalse(p["scheduled"])
        self.assertFalse(p["sent"])
        self.assertEqual(self.calendar.created_events, {})
        self.assertEqual(p["would_schedule"]["attendee"], AVA)
        self.assertIn(p["draft"], self.service.drafts_by_id)
        self.assertEqual(p["gate"], "verb")

    def test_inbox_never_is_checked_before_creating_the_event(self):
        from connectors.mail import policy
        with patch.object(policy, "HUB_GET", return_value={"effective": {"mode": "never"}}):
            rc, answer, err = self.schedule()
        self.assertEqual(rc, 2)
        self.assertEqual(self.calendar.created_events, {})
        self.assertEqual(self.queue_requests, [])

    def test_schedule_creates_a_silent_event_and_freezes_the_invitation_in_the_queue(self):
        rc, answer, err = self.schedule()
        self.assertEqual(rc, 0, err)
        self.assertTrue(answer["queued"], answer)
        self.assertFalse(answer["sent"])
        self.assertEqual(self.service.sent, [])
        self.assertTrue(all(event["sendUpdates"] == "none" for event in self.calendar.created_events.values()))
        from backend.email_content import snapshot
        frozen = snapshot(self.queue_requests[0]["raw"], "ana@acme.example")
        self.assertTrue(any(file["type"] == "text/calendar" for file in frozen["attachments"]))

class E2E(Stage2):
    def test_e2e_refuses_any_mailbox_that_is_not_the_sandbox(self):
        with patch.dict("os.environ", {"TICO_MAIL_SANDBOX": "hub-test@acme.example"}):
            rc, out, err = self.run_cli("doctor", "--e2e", "--mailbox", "ana@acme.example")
        self.assertEqual(rc, 2)
        self.assertIn("only ever runs against the sandbox mailbox hub-test@acme.example", err)

    def test_e2e_refuses_to_run_with_no_sandbox_named(self):
        os.environ.pop("TICO_MAIL_SANDBOX", None)       # the harness restores the environment
        rc, out, err = self.run_cli("doctor", "--e2e")
        self.assertEqual(rc, 2)
        self.assertIn("no sandbox mailbox", err)

if __name__ == "__main__":
    unittest.main()
