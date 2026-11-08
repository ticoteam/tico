"""Subscriptions (subscriptions.py, step 6): the repeats the daily look finds (pure, then from the books), the Watch findings
on confirmed rows, confirm and undo by the owner and the Bookkeeper, the cancel after missed periods, and the reads (report,
metric, search, briefing) with who sees each subscription."""

import sqlite3
from datetime import date
from typing import Any

from backend.finance import actions as FA
from backend.finance import brief as BR
from backend.finance import reads as RD
from backend.finance import subscriptions as SUB
from backend.finance.core import watch as W
from backend.finance.store import ledger as SL
from backend.finance.store import subscriptions as SS
from backend.store import Store
from backend.tests.test_finance_fixtures import AT, OWNER, act, books, finance_template, store  # noqa: F401
from backend.tests.test_finance_watch import PRIOR_MONTHS, WATCH_AT, _bot_card_charges

TODAY = date(2026, 11, 1)
MONTHLY = ("2026-08-05", "2026-09-05", "2026-10-05")


def charge(i: str, payee: str, amount: int, day: str, card: str = "", shown: str = "") -> W.Charge:
    return W.Charge(id=i, payee=payee, amount=amount, day=date.fromisoformat(day), shown=shown, card=card)


def rows_of(c: sqlite3.Connection) -> list[dict[str, Any]]:
    book = SL.book(c)
    assert book is not None
    return [dict(r) for r in SS.book_rows(c, book.id)]


def only_row(c: sqlite3.Connection) -> dict[str, Any]:
    found = rows_of(c)
    assert len(found) == 1, found
    return found[0]


# ------------------------------------------------------------------------------------------------ detection (pure)
def test_three_monthly_charges_on_about_the_same_day_at_similar_amounts_are_one_monthly_repeat():
    found = SUB.find(
        [
            charge("1", "figma", 1_500, "2026-08-05", card="card_a"),
            charge("2", "figma", 1_500, "2026-09-06", card="card_a"),
            charge("3", "figma", 1_650, "2026-10-05", card="card_a"),
        ],
        TODAY,
    )
    assert [(f.vendor, f.cadence) for f in found] == [("figma", "monthly")]
    assert found[0].last.id == "3" and found[0].first_on == date(2026, 8, 5)


def test_two_monthly_charges_are_not_yet_a_repeat_and_amounts_a_quarter_apart_are_not_one_either():
    assert (
        SUB.find(
            [
                charge("1", "figma", 1_500, "2026-09-05", card="card_a"),
                charge("2", "figma", 1_500, "2026-10-05", card="card_a"),
            ],
            TODAY,
        )
        == []
    )
    assert (
        SUB.find(
            [
                charge("1", "figma", 1_500, "2026-08-05", card="card_a"),
                charge("2", "figma", 1_500, "2026-09-05", card="card_a"),
                charge("3", "figma", 2_000, "2026-10-05", card="card_a"),
            ],
            TODAY,
        )
        == []
    )


def test_a_monthly_charge_a_week_off_the_others_is_not_a_repeat_and_a_few_days_off_is():
    off_by_seven = [
        charge(str(i), "figma", 1_500, d, card="card_a")
        for i, d in enumerate(("2026-08-01", "2026-09-12", "2026-10-05"))
    ]
    within_five = [
        charge(str(i), "figma", 1_500, d, card="card_a")
        for i, d in enumerate(("2026-08-01", "2026-09-09", "2026-10-05"))
    ]
    assert SUB.find(off_by_seven, TODAY) == []
    assert [f.cadence for f in SUB.find(within_five, TODAY)] == ["monthly"]


def test_the_same_vendor_on_two_cards_is_two_repeats_and_similar_names_stay_apart():
    two_cards = [
        charge(f"{card}{i}", "figma", 1_500, day, card=card)
        for card in ("card_a", "card_b")
        for i, day in enumerate(MONTHLY)
    ]
    assert sorted(f.last.card for f in SUB.find(two_cards, TODAY)) == ["card_a", "card_b"]
    similar = [charge(f"a{i}", "figma", 1_500, day, card="card_a") for i, day in enumerate(MONTHLY)]
    similar += [charge(f"b{i}", "figma design", 1_500, day, card="card_a") for i, day in enumerate(MONTHLY[:2])]
    assert [(f.vendor, f.cadence) for f in SUB.find(similar, TODAY)] == [("figma", "monthly")]


def test_two_charges_a_year_apart_are_an_annual_repeat_and_fourteen_months_apart_are_not():
    year = [
        charge("1", "adobe", 12_000, "2024-10-03", card="card_a"),
        charge("2", "adobe", 12_000, "2025-10-04", card="card_a"),
    ]
    assert [f.cadence for f in SUB.find(year, date(2025, 11, 1))] == ["annual"]
    gap = [
        charge("1", "adobe", 12_000, "2024-08-03", card="card_a"),
        charge("2", "adobe", 12_000, "2025-10-04", card="card_a"),
    ]
    assert SUB.find(gap, date(2025, 11, 1)) == []


def test_the_next_charge_clamps_to_the_month_end_and_a_leap_day_to_february():
    assert SUB.next_on(date(2026, 1, 31), "monthly") == date(2026, 2, 28)
    assert SUB.next_on(date(2024, 2, 29), "annual") == date(2025, 2, 28)


# ------------------------------------------------------------------------------------------------ watch (pure)
ACTIVE_FIGMA = [{"vendor_key": "figma", "source_key": "card_a", "status": "active", "amount": 1_500}]
FIGMA_RISES = [
    charge("1", "figma", 1_500, "2026-08-05", card="card_a"),
    charge("2", "figma", 1_500, "2026-09-05", card="card_a"),
    charge("3", "figma", 2_000, "2026-10-05", card="card_a", shown="FIGMA"),
]


def test_a_charge_on_an_active_subscription_is_judged_against_its_expected_amount():
    found = SUB.price_findings(FIGMA_RISES, ACTIVE_FIGMA)
    assert [(f["key"], f["label"]) for f in found] == [("price_increase:3", "FIGMA: 15.00 → 20.00")]


def test_an_ignored_subscription_raises_no_price_finding_and_no_rows_is_todays_check():
    ignored = [{**ACTIVE_FIGMA[0], "status": "ignored"}]
    assert SUB.price_findings(FIGMA_RISES, ignored) == []
    assert SUB.price_findings(FIGMA_RISES, []) == W.price_increases(FIGMA_RISES)


def test_an_ignored_subscription_on_an_idle_card_raises_no_unused_finding():
    def on(i: str, day: str) -> W.Charge:
        return W.Charge(
            id=i, payee="acme cloud", amount=4_900, day=date.fromisoformat(day), shown="ACME CLOUD", card="card_a"
        )

    months = [on("8", "2026-08-05"), on("9", "2026-09-05"), on("10", "2026-10-05")]
    idle = {"card_a": "archived-bot"}
    ignored = [{"vendor_key": "acme cloud", "source_key": "card_a", "status": "ignored", "amount": 4_900}]
    assert SUB.unused_findings(months, ignored, idle, TODAY) == []
    assert [f["key"] for f in SUB.unused_findings(months, [], idle, TODAY)] == ["unused_subscription:card_a:acme cloud"]


# ------------------------------------------------------------------------------------------------ detection (books)
def test_the_daily_look_suggests_a_monthly_card_repeat_once_and_refreshes_it_without_a_second_row(books: Store):
    with books.transaction() as c:
        _bot_card_charges(c, "coder", "card_coder", PRIOR_MONTHS)
        first = SUB.detect(c, WATCH_AT)
        second = SUB.detect(c, WATCH_AT)
        row = only_row(c)
    assert first == {"found": 1, "new": 1, "cancelled": 0}
    assert second == {"found": 1, "new": 0, "cancelled": 0}
    assert (row["vendor_key"], row["source_key"], row["cadence"], row["status"]) == (
        "acme cloud",
        "card_coder",
        "monthly",
        "suggested",
    )
    assert (row["amount"], row["first_charge_on"], row["next_charge_on"]) == (4_900, "2026-08-05", "2026-11-05")


def test_an_annual_charge_a_year_back_is_found_by_the_daily_look_though_watch_reads_only_120_days(books: Store):
    with books.transaction() as c:
        _bot_card_charges(c, "coder", "card_coder", ("2025-10-04", "2026-10-05"))
        found = SUB.detect(c, WATCH_AT)
        row = only_row(c)
    assert found == {"found": 1, "new": 1, "cancelled": 0}
    assert (row["cadence"], row["next_charge_on"]) == ("annual", "2027-10-05")


def test_a_bot_without_the_book_right_cannot_confirm_a_suggestion(books: Store):
    with books.transaction() as c:
        _bot_card_charges(c, "coder", "card_coder", PRIOR_MONTHS)
        SUB.detect(c, WATCH_AT)
        sid = str(only_row(c)["id"])
        res = act(c, "bot:coder", "subscription_confirm", {"subscription_id": sid, "status": "active"}, at=WATCH_AT)
    assert res.status != "committed", res
    with books.read() as c:
        assert only_row(c)["status"] == "suggested"


def test_the_bookkeeper_confirms_with_its_book_right_and_the_owner_can_undo_it(books: Store):
    with books.transaction() as c:
        _bot_card_charges(c, "coder", "card_coder", PRIOR_MONTHS)
        SUB.detect(c, WATCH_AT)
        sid = str(only_row(c)["id"])
        FA.grant_defaults(c, "bot:bookkeeper", at=AT)
        done = act(
            c,
            "bot:bookkeeper",
            "subscription_confirm",
            {"subscription_id": sid, "status": "active", "amount": 5_200},
            at=WATCH_AT,
        )
        assert done.status == "committed", done
        active = only_row(c)
        assert (active["status"], active["amount"], active["confirmed_by"]) == ("active", 5_200, "bot:bookkeeper")
        undone = act(c, OWNER, "subscription_undo", {"subscription_id": sid}, at=WATCH_AT)
        assert undone.status == "committed", undone
        back = only_row(c)
        assert (back["status"], back["amount"]) == ("suggested", 4_900)
        again = act(c, OWNER, "subscription_undo", {"subscription_id": sid}, at=WATCH_AT)
        assert again.status != "committed", again


def test_a_repeat_with_no_charge_for_two_periods_is_cancelled_by_the_daily_look(books: Store):
    with books.transaction() as c:
        _bot_card_charges(c, "coder", "card_coder", PRIOR_MONTHS)
        SUB.detect(c, WATCH_AT)
        assert SUB.detect(c, "2027-01-04T00:00:00.000000Z")["cancelled"] == 0
        assert SUB.detect(c, "2027-01-06T00:00:00.000000Z") == {"found": 0, "new": 0, "cancelled": 1}
        assert only_row(c)["status"] == "cancelled"


# ------------------------------------------------------------------------------------------------ reads
def _two_subscriptions(books: Store) -> None:
    """Coder's card: 49.00 a month, confirmed active. Seller's card: 30.00 a month, cancelled on Oct 20."""
    with books.transaction() as c:
        _bot_card_charges(c, "coder", "card_coder", PRIOR_MONTHS)
        _bot_card_charges(c, "seller", "card_seller", PRIOR_MONTHS, amount=3_000)
        SUB.detect(c, WATCH_AT)
        by_card = {str(r["source_key"]): str(r["id"]) for r in rows_of(c)}
        kept = act(
            c,
            OWNER,
            "subscription_confirm",
            {"subscription_id": by_card["card_coder"], "status": "active"},
            at=WATCH_AT,
        )
        ended = act(
            c,
            OWNER,
            "subscription_confirm",
            {"subscription_id": by_card["card_seller"], "status": "cancelled"},
            at="2026-10-20T00:00:00.000000Z",
        )
        assert (kept.status, ended.status) == ("committed", "committed"), (kept, ended)


def test_the_subscriptions_report_counts_the_rows_and_lists_the_renewals_within_thirty_days(books: Store):
    _two_subscriptions(books)
    with books.read() as c:
        report = RD.report(c, OWNER, "subscriptions", at=WATCH_AT)
    assert (report["count"], report["monthly_total"]) == (2, 4_900)
    assert [(r["status"], r["next_charge_on"]) for r in report["renewals_30d"]] == [("active", "2026-11-05")]


def test_the_monthly_subscriptions_metric_is_the_active_total_the_owner_sees(books: Store):
    _two_subscriptions(books)
    with books.read() as c:
        owner = RD.metric(c, OWNER, "subscriptions_monthly", at=WATCH_AT)
    assert (owner["amount"], owner["count"], owner["unit"]) == (4_900, 1, "minor")


def test_the_subscription_search_finds_a_vendor_by_name_for_the_owner(books: Store):
    _two_subscriptions(books)
    with books.read() as c:
        found = RD.search(c, OWNER, kind="subscription", q="acme", at=WATCH_AT)
        missed = RD.search(c, OWNER, kind="subscription", q="figma", at=WATCH_AT)
    assert len(found["results"]) == 2
    assert missed["results"] == []


def test_the_briefing_line_says_what_is_active_and_how_it_moved_since_last_month(books: Store):
    _two_subscriptions(books)
    with books.read() as c:
        lines = BR.brief(c, OWNER, at=WATCH_AT)["lines"]
    assert any("Subscriptions: $49/mo across 1, down $30 since Oct" in line for line in lines), lines


def test_the_owner_sees_every_subscription_and_a_coder_bot_with_no_attribution_sees_none(books: Store):
    _two_subscriptions(books)
    with books.read() as c:
        assert len(SUB.read(c, OWNER, at=WATCH_AT)["rows"]) == 2
        assert SUB.read(c, "bot:coder", at=WATCH_AT)["rows"] == []
