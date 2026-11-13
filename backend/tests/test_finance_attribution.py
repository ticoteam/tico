"""Attribution (shares of a posted P&L line): the basis-point split rolls into the accrual rollups to the cent, a person's
override beats a rule and a rule beats the line's tags, undo puts back the row a change superseded (history newest
first), a rule applies at posting and to lines from its apply_from day, deleting a rule gives its lines back their tags,
the right is needed (a bot with it commits without approval), a reversal nets its original's shares to zero, the cash view,
budgets and mirror books follow it, a private line refuses it, and the reads say who can change it and where it came from."""

import sqlite3

import pytest

from backend.finance import reads as FR
from backend.finance import tab
from backend.finance.core import attribution as CA
from backend.finance.core.errors import CONFLICT, DENIED, INVALID, NOT_FOUND, FinanceError
from backend.finance.store import attribution as ATT
from backend.finance.store import ledger as SL
from backend.tests.helpers.finance_fixtures import (
    AT,
    OWNER,
    account,
    act,
    books,
    budget,
    finance_template,
    spend,
    store,
)

DAN = "human:dan"
BEN = "human:ben"
CARA = "human:cara"
EVE = "human:eve"
COLLEAGUE_BOT = "bot:coder"


def _line(c: sqlite3.Connection, entry_id: str, code: str) -> str:
    return str(
        c.execute(
            "SELECT id FROM fin_lines WHERE entry_id=? AND account_id=?", (entry_id, account(c, code))
        ).fetchone()[0]
    )


def _by_teammate(c: sqlite3.Connection, code: str, table: str = "fin_rollups") -> dict[str, int]:
    rows = c.execute(
        f"SELECT teammate, sum(amount) FROM {table} WHERE account_id=? GROUP BY teammate", (account(c, code),)
    ).fetchall()
    return {str(t): int(a) for t, a in rows if a}


def _shares(*pairs: tuple[str, int]) -> list[dict[str, object]]:
    return [{"teammate": mate, "share_bps": bps} for mate, bps in pairs]


def test_the_split_is_exact_to_the_cent_and_the_rest_stays_unattributed(books):
    with books.transaction() as c:
        posted = spend(c, "6100", 300_001, day="2026-10-10")
        line = _line(c, str(posted["entry_id"]), "6100")
        r = act(c, OWNER, "attribute", {"line_id": line, "splits": _shares((CARA, 5000), (DAN, 3000))})
        assert r.status == "committed", r.reason
        assert [s["amount"] for s in r.result["splits"]] == [150_001, 90_000]
        got = _by_teammate(c, "6100")
        assert got == {CARA: 150_001, DAN: 90_000, "": 60_000}
        assert sum(got.values()) == 300_001


def test_rounding_is_largest_remainder_and_negative_amounts_mirror_it():
    assert CA.allocate(10, [1, 1, 1]) == [4, 3, 3]
    assert CA.allocate(-10, [1, 1, 1]) == [-4, -3, -3]
    assert sum(CA.allocate(300_001, [5000, 3000, 2000])) == 300_001


def test_a_person_override_beats_a_rule_and_clearing_it_falls_back(books):
    with books.transaction() as c:
        rule = act(
            c,
            OWNER,
            "attribution_rule_set",
            {
                "name": "Subscriptions",
                "match": {"kind": "account", "id": account(c, "6100")},
                "splits": _shares((DAN, 10000)),
            },
        )
        assert rule.status == "committed", rule.reason
        posted = spend(c, "6100", 4_000, day="2026-10-10", teammate=BEN)
        line = _line(c, str(posted["entry_id"]), "6100")
        assert _by_teammate(c, "6100") == {DAN: 4_000}  # the rule, at posting, over the line's tag
        assert act(c, OWNER, "attribute", {"line_id": line, "splits": _shares((CARA, 10000))}).status == "committed"
        assert _by_teammate(c, "6100") == {CARA: 4_000}  # the override beats the rule
        cleared = act(c, OWNER, "attribute", {"line_id": line, "splits": []})
        assert cleared.status == "committed", cleared.reason
        assert _by_teammate(c, "6100") == {DAN: 4_000}  # clearing the override falls back to the rule


def test_undo_restores_the_row_a_change_superseded_and_history_is_newest_first(books):
    with books.transaction() as c:
        posted = spend(c, "6100", 4_000, day="2026-10-10")
        line = _line(c, str(posted["entry_id"]), "6100")
        assert act(c, OWNER, "attribute", {"line_id": line, "splits": _shares((CARA, 10000))}).status == "committed"
        assert act(c, OWNER, "attribute", {"line_id": line, "splits": _shares((DAN, 10000))}).status == "committed"
        undone = act(c, OWNER, "attribute_undo", {"line_id": line})
        assert undone.status == "committed", undone.reason
        assert _by_teammate(c, "6100") == {CARA: 4_000}
        history = ATT.history(c, line)
        assert [ATT.parse_splits(str(h["splits"]))[0].teammate for h in history] == [CARA, DAN, CARA]


def test_a_rule_applies_to_lines_from_its_apply_from_day_and_deleting_it_gives_tags_back(books):
    with books.transaction() as c:
        spend(c, "6100", 4_000, day="2026-10-10", teammate=BEN)
        assert _by_teammate(c, "6100") == {BEN: 4_000}
        rule = act(
            c,
            OWNER,
            "attribution_rule_set",
            {
                "name": "Subscriptions",
                "match": {"kind": "account", "id": account(c, "6100")},
                "splits": _shares((DAN, 10000)),
                "apply_from": "2026-10-01",
            },
        )
        assert rule.status == "committed", rule.reason
        assert _by_teammate(c, "6100") == {DAN: 4_000}  # the line posted on or after apply_from is re-applied
        gone = act(c, OWNER, "attribution_rule_delete", {"id": rule.result["id"]})
        assert gone.status == "committed", gone.reason
        assert _by_teammate(c, "6100") == {BEN: 4_000}


def test_the_attribution_right_is_needed_and_a_bot_with_it_commits_without_approval(books):
    with books.transaction() as c:
        posted = spend(c, "6100", 4_000, day="2026-10-10", teammate=COLLEAGUE_BOT)
        line = _line(c, str(posted["entry_id"]), "6100")
        denied = act(c, EVE, "attribute", {"line_id": line, "splits": _shares((CARA, 10000))})
        assert denied.code == DENIED
        assert _by_teammate(c, "6100") == {COLLEAGUE_BOT: 4_000}
        assert act(c, OWNER, "right_grant", {"teammate": COLLEAGUE_BOT, "right": "finance.attribution"}).status == (
            "committed"
        )
        bot = act(c, COLLEAGUE_BOT, "attribute", {"line_id": line, "splits": _shares((CARA, 10000))})
        assert bot.status == "committed", bot.reason
        assert _by_teammate(c, "6100") == {CARA: 4_000}


def test_a_reversal_nets_the_original_shares_to_zero(books):
    with books.transaction() as c:
        posted = spend(c, "6100", 300_001, day="2026-10-10")
        line = _line(c, str(posted["entry_id"]), "6100")
        assert act(c, OWNER, "attribute", {"line_id": line, "splits": _shares((CARA, 5000), (DAN, 3000))}).status == (
            "committed"
        )
        reversed_ = act(c, OWNER, "journal", {"reverses": str(posted["entry_id"])})
        assert reversed_.status == "committed", reversed_.reason
        assert sum(_by_teammate(c, "6100").values()) == 0


def test_bad_splits_and_rules_are_refused(books):
    with books.transaction() as c:
        posted = spend(c, "6100", 4_000, day="2026-10-10")
        line = _line(c, str(posted["entry_id"]), "6100")
        over = act(c, OWNER, "attribute", {"line_id": line, "splits": _shares((CARA, 6000), (DAN, 5000))})
        assert over.code == INVALID
        nobody = act(c, OWNER, "attribute", {"line_id": line, "splits": _shares(("human:nobody", 10000))})
        assert nobody.code == INVALID
        empty_rule = act(
            c,
            OWNER,
            "attribution_rule_set",
            {"name": "Nothing", "match": {"kind": "account", "id": account(c, "6100")}, "splits": []},
        )
        assert empty_rule.code == INVALID
        assert (
            act(
                c,
                OWNER,
                "attribution_rule_set",
                {
                    "name": "Subs",
                    "match": {"kind": "account", "id": account(c, "6100")},
                    "splits": _shares((DAN, 10000)),
                },
            ).status
            == "committed"
        )
        taken = act(
            c,
            OWNER,
            "attribution_rule_set",
            {"name": "Again", "match": {"kind": "account", "id": account(c, "6100")}, "splits": _shares((CARA, 10000))},
        )
        assert taken.code == CONFLICT


def test_the_cash_view_follows_the_split_and_undo_moves_it_back(books):
    """An unpaid bill's expense is a cash correction (fin_cash_rollups) that holds the cash view out of its P&L until it
    is paid. The correction takes the line's shares, so the cash view follows the attribution, and undo moves it back."""
    with books.transaction() as c:
        vendor = str(act(c, OWNER, "contact_create", {"name": "Acme Supplies", "kind": "vendor"}).result["contact_id"])
        made = act(
            c,
            OWNER,
            "bill_create",
            {
                "vendor_id": vendor,
                "bill_date": "2026-10-10",
                "lines": [{"account_id": account(c, "6100"), "amount": 300_001}],
            },
        )
        assert made.status == "committed", made.reason
        assert act(c, OWNER, "bill_approve", {"bill_id": str(made.result["bill_id"])}).status == "committed"
        line = str(
            c.execute(
                "SELECT id FROM fin_lines WHERE account_id=? ORDER BY rowid DESC LIMIT 1", (account(c, "6100"),)
            ).fetchone()[0]
        )
        assert _by_teammate(c, "6100", "fin_cash_rollups") == {"": -300_001}
        before = _by_teammate(c, "6100", "fin_cash_rollups")
        assert act(c, OWNER, "attribute", {"line_id": line, "splits": _shares((CARA, 5000), (DAN, 3000))}).status == (
            "committed"
        )
        assert _by_teammate(c, "6100", "fin_cash_rollups") == {CARA: -150_001, DAN: -90_000, "": -60_000}
        assert act(c, OWNER, "attribute", {"line_id": line, "splits": _shares((CARA, 10000))}).status == "committed"
        assert _by_teammate(c, "6100", "fin_cash_rollups") == {CARA: -300_001}
        assert act(c, OWNER, "attribute_undo", {"line_id": line}).status == "committed"
        assert _by_teammate(c, "6100", "fin_cash_rollups") == {CARA: -150_001, DAN: -90_000, "": -60_000}
        assert act(c, OWNER, "attribute", {"line_id": line, "splits": []}).status == "committed"
        assert _by_teammate(c, "6100", "fin_cash_rollups") == before


def test_the_readers_and_a_teammate_budget_follow_the_split(books):
    with books.transaction() as c:
        posted = spend(c, "6100", 300_001, day="2026-10-10")
        line = _line(c, str(posted["entry_id"]), "6100")
        parent = act(
            c, OWNER, "budget_create", {"name": "Software", "account_id": account(c, "6100"), "amount": 500_000}
        )
        assert parent.status == "committed", parent.reason
        made = act(
            c,
            OWNER,
            "budget_create",
            {
                "name": "Cara software",
                "account_id": account(c, "6100"),
                "parent_id": budget(c, "Software"),
                "amount": 200_000,
                "teammate": CARA,
            },
        )
        assert made.status == "committed", made.reason
        assert act(c, OWNER, "attribute", {"line_id": line, "splits": _shares((CARA, 5000), (DAN, 3000))}).status == (
            "committed"
        )
        report = FR.budget_vs_actual(c, OWNER, "2026-10-01", "2026-10-31", "month", at=AT)
        row = next(r for r in report["budgets"] if r["name"] == "Cara software")
        assert row["actual"] == [150_001]
        amounts = {r["amount"] for r in FR.spend_by(c, OWNER, "teammate", "2026-10-01", "2026-10-31")["rows"]}
        assert {150_001, 90_000} <= amounts


def test_a_mirror_book_attributes_without_writing_to_the_provider(books):
    with books.transaction() as c:
        posted = spend(c, "6100", 300_001, day="2026-10-10")
        line = _line(c, str(posted["entry_id"]), "6100")
        c.execute(
            "INSERT INTO fin_connections(id, provider, purpose, created_at, created_by, name) VALUES(?,?,?,?,?,?)",
            ("conn-books", "quickbooks", "books", AT, OWNER, "Acme books"),
        )
        SL.set_book_mode(c, SL.book(c).id, "mirror", "conn-books")  # type: ignore[union-attr]
        outbox = c.execute("SELECT COUNT(*) FROM fin_outbox").fetchone()[0]
        done = act(c, OWNER, "attribute", {"line_id": line, "splits": _shares((CARA, 10000))})
        assert done.status == "committed", done.reason
        assert c.execute("SELECT COUNT(*) FROM fin_outbox").fetchone()[0] == outbox
        assert _by_teammate(c, "6100") == {CARA: 300_001}


def test_a_private_line_the_viewer_cannot_see_refuses_attribution(books):
    with books.transaction() as c:
        posted = spend(c, "6100", 4_000, day="2026-10-10")
        line = _line(c, str(posted["entry_id"]), "6100")
        assert act(c, OWNER, "right_grant", {"teammate": DAN, "right": "finance.attribution"}).status == "committed"
        assert act(c, OWNER, "view_grant", {"subject": DAN, "what": "everything"}).status == "committed"
        assert act(
            c, OWNER, "private_lines_set", {"account_id": account(c, "6100"), "others_see": "totals"}
        ).status == ("committed")
        refused = act(c, DAN, "attribute", {"line_id": line, "splits": _shares((CARA, 10000))})
        assert refused.code in (DENIED, NOT_FOUND)
        assert _by_teammate(c, "6100") == {"": 4_000}
        with pytest.raises(FinanceError) as seen:  # the read is not found for a viewer who does not see the line
            FR.line_attribution(c, DAN, line)
        assert seen.value.code == NOT_FOUND
        assert FR.line_attribution(c, OWNER, line)["amount"] == 4_000


def test_the_line_read_says_who_can_change_its_source_and_history(books):
    with books.transaction() as c:
        assert act(c, OWNER, "right_grant", {"teammate": COLLEAGUE_BOT, "right": "finance.attribution"}).status == (
            "committed"
        )
        assert act(c, OWNER, "view_grant", {"subject": DAN, "what": "everything"}).status == "committed"  # no right
        posted = spend(c, "6100", 4_000, day="2026-10-10", teammate=COLLEAGUE_BOT)
        line = _line(c, str(posted["entry_id"]), "6100")
        bot = act(c, COLLEAGUE_BOT, "attribute", {"line_id": line, "splits": _shares((CARA, 10000))})
        assert bot.status == "committed", bot.reason
        read = FR.line_attribution(c, OWNER, line)
        assert read["source"] == "bot" and read["rule_id"] is None and read["rule_name"] is None
        assert read["set_by"] == COLLEAGUE_BOT and read["set_at"] and read["can_change"] is True
        assert FR.line_attribution(c, DAN, line)["can_change"] is False  # sees the line, lacks the right

        rule = act(
            c,
            OWNER,
            "attribution_rule_set",
            {
                "name": "Subscriptions",
                "match": {"kind": "account", "id": account(c, "6100")},
                "splits": _shares((DAN, 10000)),
            },
        )
        assert rule.status == "committed", rule.reason
        ruled = spend(c, "6100", 2_000, day="2026-10-11", teammate=BEN)
        read = FR.line_attribution(c, OWNER, _line(c, str(ruled["entry_id"]), "6100"))
        assert (
            read["source"] == "rule" and read["rule_id"] == rule.result["id"] and read["rule_name"] == "Subscriptions"
        )

        for n in range(22):  # 22 more changes, alternating so each one moves the line; the last (n=21) names CARA
            mate = CARA if n % 2 else DAN
            assert act(c, OWNER, "attribute", {"line_id": line, "splits": _shares((mate, 10000))}).status == "committed"
        read = FR.line_attribution(c, OWNER, line)
        assert read["source"] == "person" and read["can_change"] is True
        assert len(read["history"]) == 20  # 23 rows exist; the read caps at 20
        assert read["history"][0]["splits"][0]["teammate"] == CARA and read["history"][0]["source"] == "person"
        assert read["history"][1]["splits"][0]["teammate"] == DAN  # newest first
        assert all(h["set_at"] for h in read["history"])


def test_the_rules_read_says_who_may_change_and_who_made_each_rule(books):
    with books.transaction() as c:
        assert act(c, OWNER, "right_grant", {"teammate": DAN, "right": "finance.attribution"}).status == "committed"
        assert (
            act(
                c,
                OWNER,
                "attribution_rule_set",
                {
                    "name": "Subscriptions",
                    "match": {"kind": "account", "id": account(c, "6100")},
                    "splits": _shares((DAN, 10000)),
                },
            ).status
            == "committed"
        )
        rules = FR.attribution_rules(c, DAN)
        assert rules["can_change"] is True
        assert [r["created_by"] for r in rules["rules"]] == [OWNER]
        assert FR.attribution_rules(c, OWNER)["can_change"] is True
        with pytest.raises(FinanceError) as refused:
            FR.attribution_rules(c, EVE)
        assert refused.value.code == DENIED


def test_the_caps_say_who_may_attribute(books):
    with books.transaction() as c:
        assert act(c, OWNER, "right_grant", {"teammate": DAN, "right": "finance.attribution"}).status == "committed"
        rows = {t["teammate"]: t for t in tab.access(c, OWNER)["teammates"]}
        assert rows[OWNER]["attribute"] is True
        assert rows[DAN]["attribute"] is True
        assert rows[EVE]["attribute"] is False
