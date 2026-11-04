"""§6.4 defaults 4 and 6 are visible. Rule 6 (closing or reopening a period asks the owner) is a seeded gate row that the
code also enforces, and nobody can change or turn it off. Rule 4 (a budget at its limit with decline denies holds and
freezes its cards) is listed read-only beside the rules; the cutoff code paths enforce it. Synthetic fixtures (Acme)."""
from datetime import date

from backend.finance import tab_money
from backend.finance.core import gates as G
from backend.finance.store import banking as SK
from backend.tests.test_finance_fixtures import OWNER, act, books, finance_template, store  # noqa: F401

DAY = date(2026, 10, 31)


def test_every_seeded_default_gives_its_own_examples():
    for rid, name, _words, when, outcome, examples in G.DEFAULTS:
        assert G.failing_examples(G.rule_of(rid, name, when, outcome, True), examples, DAY) == [], rid


def test_rule_six_is_a_fixed_gate_row_and_rule_four_is_listed_read_only(books):  # noqa: F811
    with books.transaction() as c:
        row = SK.get(c, "fin_rules", "rule:close-reopen-owner")
        assert row is not None and bool(row["active"]) and str(row["kind"]) == "gate"
        for kind, payload in (("rule_disable", {"rule_id": "rule:close-reopen-owner"}),
                              ("rule_update", {"rule_id": "rule:close-reopen-owner", "name": "Owner never asked"})):
            refused = act(c, OWNER, kind, payload)
            assert refused.code == "denied", (kind, refused)
        assert bool(SK.get(c, "fin_rules", "rule:close-reopen-owner")["active"])
    with books.read() as c:
        listed = {r["id"]: r for r in tab_money.rules(c, OWNER)["rules"]}
    assert listed["rule:close-reopen-owner"]["fixed"] is True
    assert listed["floor:budget-cutoff-freeze"]["kind"] == "floor"
    assert listed["floor:budget-cutoff-freeze"]["fixed"] is True
