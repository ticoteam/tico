"""Every lint rule, twice: a body that trips it and a body that does not.

Pure - no CLI, no Gmail, no Tico. `ids()` collects the rule ids a body produces, so a
rule that stops firing fails a test rather than quietly letting a bad draft through.
"""

import sys, unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake                                                          # noqa: E402,F401

from connectors.mail import lint as ln, zone                         # noqa: E402

TZ = zone("America/Los_Angeles")
NOW = datetime(2026, 9, 2, 10, 0, tzinfo=TZ)                # Wednesday 2026-09-02, 10:00 PT
CTA = ("https://acme.example/rentals?utm_source=influencer&utm_medium=email"
       "&utm_campaign=ava-reyes")
CLEAN = """Hi Ava,

I'm Ana, the founder of Acme. We're the AI property manager: pricing, guest messaging and
turnovers for 3.9% instead of the 30% a manager charges.

I'd like to work with you. What is your rate for posts/collaborations?

Ana
Founder, Acme
"""


def lint(body, **kw):
    kw.setdefault("subject", "Ava, 6 months of Acme on us")
    kw.setdefault("now", NOW)
    kw.setdefault("signature_names", ["ana"])
    return ln.run(body, **kw)


def ids(res):
    return [x["id"] for x in res["findings"]]


class Clean(unittest.TestCase):
    def test_the_shipped_outreach_shape_passes(self):
        res = lint(CLEAN + f"\nHere's the product: {CTA}\n\nAna\n",
                   to=["ava@creator.example"], slug="influencer")
        self.assertTrue(res.ok, ids(res))
        self.assertEqual(res["findings"], [])

class Content(unittest.TestCase):
    def test_L030_secret_shapes(self):
        for bad in ("xoxb-123", "AKIAIOSFODNN7", "sk-abc123", "ghp_abc", "-----BEGIN KEY"):
            self.assertIn("L030", ids(lint(CLEAN + f"\n{bad}\n\nAna\n")), bad)
        self.assertNotIn("L030", ids(lint(CLEAN)))

    def test_L040_internal_leakage(self):
        for bad in ("s3://acme-tico-hub/x", "runtime/mail/mail.db", "emp-influencer",
                    "bot-influencer/knowledge/notes.md", "see AGENT.md"):
            self.assertIn("L040", ids(lint(CLEAN + f"\n{bad}\n\nAna\n")), bad)
        self.assertNotIn("L040", ids(lint(CLEAN)))
        self.assertNotIn("L040", ids(lint(CLEAN + "\nA bot-driven approach.\n\nAna\n")))

if __name__ == "__main__":
    unittest.main()
