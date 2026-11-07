"""Attribution (shares of a posted P&L line): the basis-point split rolls into the accrual rollups to the cent, a person's
override beats a rule and a rule beats the line's tags, undo puts back the row a change superseded (history newest
first), a rule applies at posting and to lines from its apply_from day, deleting a rule gives its lines back their tags,
the right is needed (a bot with it commits without approval), and a reversal nets its original's shares to zero."""

import sqlite3

from backend.finance.attribution import PLANNERS
from backend.finance.core import attribution as CA
from backend.finance.core.errors import CONFLICT, DENIED, INVALID
from backend.finance.core.kinds import ALL, LIVE, NEEDS
from backend.finance.store import attribution as ATT
from backend.tests.test_finance_fixtures import (  # noqa: F401
    AT,
    OWNER,
    account,
    act,
    books,
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
        assert act(c, OWNER, "attribute", {"line_id": line, "splits": []}).status == "committed"
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
        assert [ATT.parse_splits(str(h["splits_json"]))[0].teammate for h in history] == [CARA, DAN, CARA]


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
        posted = spend(c, "6100", 4_000, day="2026-10-10", team="web")
        line = _line(c, str(posted["entry_id"]), "6100")
        denied = act(c, EVE, "attribute", {"line_id": line, "splits": _shares((CARA, 10000))})
        assert denied.code == DENIED
        assert _by_teammate(c, "6100") == {"": 4_000}
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


def test_every_attribution_kind_is_registered_live_and_needs_the_right():
    kinds = ("attribute", "attribute_undo", "attribution_rule_set", "attribution_rule_delete")
    for kind in kinds:
        assert kind in PLANNERS and kind in LIVE and kind in ALL
        assert NEEDS[kind] == "finance.attribution"
