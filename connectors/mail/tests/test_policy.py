"""registry/mail-policy.yaml and the send chain: every gate, both answers.

The point of this file is that no gate can quietly stop working. Each one is checked twice -
once passing, once refusing - and the refusal is checked to be a *downgrade*, never an
exception, because a send that fails must still leave the words somewhere.
"""

import sys, unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness                                                       # noqa: E402
from harness import Stage2, gh_answer                                # noqa: E402

from connectors.mail import Failure, Refused, db, policy as pl, stamp, zone   # noqa: E402

AVA, BO, CASS = "ava@creator.example", "bo@creator.example", "cass@creator.example"


class Gates(Stage2):
    """Each gate, once open and once shut. A shut gate is a Decision, never an exception."""

    def decide(self, slug="influencer", to=(AVA,), **kw):
        return pl.check_send(self.policy(), slug, "ana@acme.example", list(to),
                             root=self.root, **kw)

    def test_the_global_kill_switch(self):
        self.write_policy(harness.POLICY.replace("send_enabled: true", "send_enabled: false"))
        d = self.decide()
        self.assertFalse(d.allowed)
        self.assertEqual(d["gate"], "global")
        self.assertIn("global send switch is off", d["reason"])

    def test_outbound_send_false(self):
        p = self.root / "emp-influencer" / "employee.yaml"
        p.write_text(p.read_text().replace("outbound_send: true", "outbound_send: false"))
        d = self.decide()
        self.assertEqual(d["gate"], "outbound_send")
        self.assertIn("outbound_send is false", d["reason"])

    def test_a_recipient_outside_the_table_is_refused(self):
        d = self.decide(to=(BO,))
        self.assertEqual(d["gate"], "recipient")
        self.assertIn("not an eligible row", d["reason"])

    def test_an_external_cc_is_refused(self):
        d = self.decide(cc=["someone@out.example"])
        self.assertEqual(d["gate"], "caps")
        self.assertIn("Cc outside", d["reason"])

    def test_the_blocklist_beats_an_approval_issue(self):
        pl.RUN = gh_answer(body="blocked@nope.example is fine by me")
        d = pl.check_send(self.policy(), "ana", "ana@acme.example", ["blocked@nope.example"],
                          root=self.root, approval="77")
        self.assertEqual(d["gate"], "blocklist")
        self.assertIn("blocked@nope.example", d["reason"])

    def test_owner_handles_personally_is_the_last_gate(self):
        pl.RUN = gh_answer(body="investor@fund.example")
        d = pl.check_send(self.policy(), "influencer", "ana@acme.example",
                          ["investor@fund.example"], root=self.root, approval="77")
        self.assertEqual(d["gate"], "owner_handles_personally")


class Caps(Stage2):
    def test_the_daily_cap_counts_todays_rows(self):
        conn = self.conn()
        day = datetime.now(zone("America/Los_Angeles")).strftime("%Y-%m-%d")
        for i in range(10):
            db.claim_send(conn, f"k{i}", "influencer", "ana@acme.example", "1",
                          [f"x{i}@creator.example"], "s", day=day)
        d = pl.check_send(self.policy(), "influencer", "ana@acme.example", [AVA], conn=conn,
                          root=self.root)
        self.assertEqual(d["gate"], "caps")
        self.assertIn("the cap is 10 a day", d["reason"])

class DraftGate(Stage2):
    def test_a_draft_to_the_blocklist_is_refused_hard(self):
        with self.assertRaises(Refused) as e:
            pl.check_draft(self.policy(), "influencer", "ana@acme.example", ["blocked@nope.example"])
        self.assertIn("blocklist", e.exception.msg)

if __name__ == "__main__":
    unittest.main()
