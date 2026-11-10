"""Platform share (backend/finance/platform_share.py): an ad platform's charge on the platform's own vendor contact counts for
each bot in proportion to the platform's spend for that bot over the charge's window. The split is exact to the cent,
the spend no current bot owns stays unattributed, a charge's window runs from the previous charge (so two charges never
share spend), a person's or bot's override and a vendor rule both beat it, a descriptor-only charge gets none, an import
that restates spend re-splits the charges it touches (one history row per change, none for an identical run), budgets
read the platform's figure in covered months and the books' share in the rest (never both), and a mirrored book follows
it with its outbox untouched."""

import sqlite3

from backend.credentials import Vault
from backend.finance import ad_import as AI
from backend.finance import attribution, budgeting
from backend.finance import platform_share as PS
from backend.finance.context import load
from backend.finance.core.org import SYSTEM
from backend.finance.store import ads as SA
from backend.finance.store import ap as SAP
from backend.finance.store import attribution as ATT
from backend.store import Store
from backend.tests.helpers.finance_ad_spend import http, meta
from backend.tests.helpers.finance_fixtures import AT, OWNER, account, act, books, finance_template, spend, store  # noqa: F401
from backend.tests.helpers.finance_platform_share import A, B, SOCIAL, _bots, by_mate, google


def put(store: Store, account_id: str, rows: list[tuple[str, int]], start: str, end: str) -> None:
    """The platform's word for [start, end] on one ad account: one campaign's spend per (day, cents)."""
    with store.transaction() as c:
        SA.replace_spend(
            c, account_id, start, end, [SA.SpendRow(day, "c1", "Spring", cents, "USD") for day, cents in rows], AT
        )


def by_team(c: sqlite3.Connection, code: str) -> dict[str, int]:
    rows = c.execute(
        "SELECT team, sum(amount) FROM fin_rollups WHERE account_id=? GROUP BY team", (account(c, code),)
    ).fetchall()
    return {str(t): int(a) for t, a in rows if a}


def _line(c: sqlite3.Connection, entry_id: str, code: str) -> str:
    return str(
        c.execute(
            "SELECT id FROM fin_lines WHERE entry_id=? AND account_id=?", (entry_id, account(c, code))
        ).fetchone()[0]
    )


# ------------------------------------------------------------------------------------------------ the split
def test_a_charge_splits_by_each_bots_platform_spend_and_the_team_follows(books: Store):
    g = google(books)
    put(books, g["a"], [("2026-10-05", 60_000)], "2026-10-01", "2026-10-15")
    put(books, g["b"], [("2026-10-06", 40_000)], "2026-10-01", "2026-10-15")
    with books.transaction() as c:
        posted = spend(c, "6300", 100_000, day="2026-10-12", vendor=g["vendor"])
        line = _line(c, str(posted["entry_id"]), "6300")
        assert by_mate(c, "6300") == {A: 60_000, B: 40_000}  # 600 and 400 of 1000: 6000 and 4000 basis points
        assert by_team(c, "6300") == {"sales": 100_000}
        row = ATT.in_force(c, line)
        assert row is not None and str(row["source"]) == "platform" and row["rule_id"] is None
        assert [(s.teammate, s.share_bps) for s in ATT.parse_splits(str(row["splits"]))] == [(A, 6000), (B, 4000)]


def test_spend_no_bot_owns_stays_unattributed_and_lowers_the_shares(books: Store):
    g = google(books)
    put(books, g["a"], [("2026-10-05", 60_000)], "2026-10-01", "2026-10-15")
    put(books, g["b"], [("2026-10-06", 40_000)], "2026-10-01", "2026-10-15")
    put(books, g["u"], [("2026-10-07", 25_000)], "2026-10-01", "2026-10-15")  # 250 of 1250
    with books.transaction() as c:
        posted = spend(c, "6300", 100_000, day="2026-10-12", vendor=g["vendor"])
        assert by_mate(c, "6300") == {A: 48_000, B: 32_000, "": 20_000}  # 4800 and 3200, the rest 2000
        line = _line(c, str(posted["entry_id"]), "6300")
        row = ATT.in_force(c, line)
        assert row is not None and sum(s.share_bps for s in ATT.parse_splits(str(row["splits"]))) == 8000


def test_two_charges_in_a_month_each_count_only_the_spend_after_the_one_before(books: Store):
    g = google(books)
    put(books, g["a"], [("2026-09-05", 60_000)], "2026-08-01", "2026-09-30")
    put(books, g["b"], [("2026-09-20", 40_000)], "2026-08-01", "2026-09-30")
    with books.transaction() as c:
        spend(c, "6300", 100_000, day="2026-09-15", vendor=g["vendor"])  # window Aug 16 to Sep 15: only A's spend
        spend(c, "6300", 100_000, day="2026-09-30", vendor=g["vendor"])  # window Sep 16 to Sep 30: only B's spend
        assert by_mate(c, "6300") == {A: 100_000, B: 100_000}


def test_a_descriptor_only_charge_gets_no_platform_share(books: Store):
    g = google(books)
    put(books, g["a"], [("2026-10-05", 60_000)], "2026-10-01", "2026-10-15")
    with books.transaction() as c:
        posted = spend(c, "6300", 100_000, day="2026-10-12", vendor="GOOGLE *ADS 4421")
        assert ATT.in_force(c, _line(c, str(posted["entry_id"]), "6300")) is None
        assert by_mate(c, "6300") == {"": 100_000}


def test_a_book_keeps_at_most_nine_bots_and_the_rest_joins_the_unattributed(books: Store):
    g = google(books)
    with books.transaction() as c:
        for n in range(10):  # ten-N spends (N+1) x 10.00 on its own account; ten-0 is the smallest
            aid = SA.upsert_account(c, "gads", "google_ads", f"9{n}", f"Ten {n}", "USD", AT)
            SA.set_bot(c, aid, f"ten-{n}", AT)
            SA.replace_spend(
                c, aid, "2026-10-01", "2026-10-15", [SA.SpendRow("2026-10-05", "c", "x", (n + 1) * 1_000, "USD")], AT
            )
        posted = spend(c, "6300", 100_000, day="2026-10-12", vendor=g["vendor"])
        row = ATT.in_force(c, _line(c, str(posted["entry_id"]), "6300"))
        assert row is not None
        mates = [s.teammate for s in ATT.parse_splits(str(row["splits"]))]
        assert len(mates) == 9 and "bot:ten-0" not in mates and "bot:ten-9" in mates


# ------------------------------------------------------------------------------------------------ precedence
def test_an_override_survives_a_restated_import_and_clearing_it_falls_back_to_the_platform(books: Store):
    g = google(books)
    put(books, g["a"], [("2026-10-05", 60_000)], "2026-10-01", "2026-10-15")
    put(books, g["b"], [("2026-10-06", 40_000)], "2026-10-01", "2026-10-15")
    with books.transaction() as c:
        posted = spend(c, "6300", 100_000, day="2026-10-12", vendor=g["vendor"])
        line = _line(c, str(posted["entry_id"]), "6300")
        assert (
            act(c, OWNER, "attribute", {"line_id": line, "splits": [{"teammate": SOCIAL, "share_bps": 10000}]}).status
            == "committed"
        )
        assert by_mate(c, "6300") == {SOCIAL: 100_000}
    put(books, g["a"], [("2026-10-05", 90_000)], "2026-10-01", "2026-10-15")
    with books.transaction() as c:
        attribution.refresh_platform(load(c, AT), SYSTEM, "google_ads", "2026-09-12", "2026-10-12")
        assert by_mate(c, "6300") == {SOCIAL: 100_000}  # a person's override is never touched
        history = ATT.history(c, line)
        assert str(history[0]["source"]) == "person" and len(history) == 2
        assert act(c, OWNER, "attribute", {"line_id": line, "splits": []}).status == "committed"
        assert by_mate(c, "6300") == {A: 69_230, B: 30_770}  # cleared: the platform share again, from the new spend


def test_a_vendor_rule_beats_the_platform_share(books: Store):
    g = google(books)
    put(books, g["a"], [("2026-10-05", 60_000)], "2026-10-01", "2026-10-15")
    with books.transaction() as c:
        rule = act(
            c,
            OWNER,
            "attribution_rule_set",
            {
                "name": "Google to social",
                "match": {"kind": "vendor", "id": g["vendor"]},
                "splits": [{"teammate": SOCIAL, "share_bps": 10000}],
            },
        )
        assert rule.status == "committed", rule.reason
        posted = spend(c, "6300", 100_000, day="2026-10-12", vendor=g["vendor"])
        assert by_mate(c, "6300") == {SOCIAL: 100_000}
        row = ATT.in_force(c, _line(c, str(posted["entry_id"]), "6300"))
        assert row is not None and str(row["source"]) == "rule"


# ------------------------------------------------------------------------------------------------ restatements
def test_a_restated_import_resplits_the_charge_with_one_history_row_and_an_identical_one_adds_none(books: Store):
    with books.transaction() as c:
        _bots(c)
    iid, net, insights = meta(books)
    insights["111"] = [{"campaign_id": "c1", "campaign_name": "Spring", "spend": "600", "date_start": "2026-10-05"}]
    insights["222"] = [{"campaign_id": "c2", "campaign_name": "Fall", "spend": "400", "date_start": "2026-10-06"}]
    assert AI.run(books, Vault(books), iid, now=AT, http_=http(net)).status == "ok"
    with books.transaction() as c:
        for ext, slug in (("111", "paid-rental-ads"), ("222", "rental-social-ad-owner")):
            aid = str(c.execute("SELECT id FROM fin_ad_accounts WHERE external_id=?", (ext,)).fetchone()[0])
            SA.set_bot(c, aid, slug, AT)
        vendor = SA.platform_contact(c, str(c.execute("SELECT id FROM fin_books").fetchone()[0]), "meta_ads")
        posted = spend(c, "6100", 100_000, day="2026-10-12", vendor=vendor)
        line = _line(c, str(posted["entry_id"]), "6100")
        assert by_mate(c, "6100") == {A: 60_000, SOCIAL: 40_000}
    insights["111"] = [{"campaign_id": "c1", "campaign_name": "Spring", "spend": "700", "date_start": "2026-10-05"}]
    insights["222"] = [{"campaign_id": "c2", "campaign_name": "Fall", "spend": "300", "date_start": "2026-10-06"}]
    assert AI.run(books, Vault(books), iid, now=AT, http_=http(net)).status == "ok"
    with books.read() as c:
        assert by_mate(c, "6100") == {A: 70_000, SOCIAL: 30_000}
        assert len(ATT.history(c, line)) == 2
    assert AI.run(books, Vault(books), iid, now=AT, http_=http(net)).status == "ok"
    with books.read() as c:
        assert len(ATT.history(c, line)) == 2  # the same spend again: no new row


def test_a_mirrored_book_follows_the_split_and_its_outbox_stays_untouched(books: Store):
    g = google(books)
    put(books, g["a"], [("2026-10-05", 60_000)], "2026-10-01", "2026-10-15")
    put(books, g["b"], [("2026-10-06", 40_000)], "2026-10-01", "2026-10-15")
    with books.transaction() as c:
        spend(c, "6300", 100_000, day="2026-10-12", vendor=g["vendor"])
        SAP.update(c, "fin_contacts", g["vendor"], {"name": "Google"})
        c.execute("UPDATE fin_books SET mode='mirror'")
    put(books, g["a"], [("2026-10-05", 30_000)], "2026-10-01", "2026-10-15")
    put(books, g["b"], [("2026-10-06", 70_000)], "2026-10-01", "2026-10-15")
    with books.transaction() as c:
        outbox = c.execute("SELECT count(*) FROM fin_outbox").fetchone()[0]
        attribution.refresh_platform(load(c, AT), SYSTEM, "google_ads", "2026-09-12", "2026-10-12")
        assert by_mate(c, "6300") == {A: 30_000, B: 70_000}
        assert c.execute("SELECT count(*) FROM fin_outbox").fetchone()[0] == outbox


# ------------------------------------------------------------------------------------------------ budgets
def _budgets(store: Store, g: dict[str, str]) -> str:
    with store.transaction() as c:
        mk = act(
            c,
            OWNER,
            "budget_create",
            {"name": "Marketing", "amount": 1_000_000, "account_id": g["adv"], "at_limit": "warn"},
            at=AT,
        )
        assert mk.status == "committed", mk
        bot = act(
            c,
            OWNER,
            "budget_create",
            {
                "name": "Rental Google Ad Owner",
                "teammate": A,
                "vendor": g["vendor"],
                "amount": 300_000,
                "account_id": g["adv"],
                "parent_id": str(mk.result["budget_id"]),
                "at_limit": "warn",
            },
            at=AT,
        )
        assert bot.status == "committed", bot
        return str(bot.result["budget_id"])


def test_a_covered_month_shows_the_platforms_figure_for_the_bot_and_an_uncovered_one_the_books_share(books: Store):
    g = google(books, history_from="2026-09-01")  # the import covers September on, not August
    put(books, g["a"], [("2026-08-05", 60_000)], "2026-08-01", "2026-08-31")
    put(books, g["b"], [("2026-08-06", 40_000)], "2026-08-01", "2026-08-31")
    put(books, g["a"], [("2026-09-05", 70_000)], "2026-09-01", "2026-09-30")
    put(books, g["b"], [("2026-09-12", 30_000)], "2026-09-01", "2026-09-30")
    bot_budget = _budgets(books, g)
    with books.transaction() as c:
        spend(c, "6300", 100_000, day="2026-08-25", vendor=g["vendor"])  # books: A 60,000, B 40,000
        spend(c, "6300", 120_000, day="2026-09-20", vendor=g["vendor"])  # books: A 84,000 (70%), B 36,000
        ctx = load(c, AT)
        budget = ctx.budgets()[bot_budget]
        assert budgeting.actual(ctx, budget, "2026-08-01", "2026-08-31", live=True) == 60_000  # uncovered: the books
        assert budgeting.actual(ctx, budget, "2026-09-01", "2026-09-30", live=False) == 84_000  # the books' share
        # Covered: the platform's own figure for the bot, never the books' share on top of it.
        assert budgeting.actual(ctx, budget, "2026-09-01", "2026-09-30", live=True) == 70_000
        assert by_mate(c, "6300") == {A: 144_000, B: 76_000}


def test_a_restated_import_moves_the_bots_budget_with_it(books: Store):
    g = google(books, history_from="2026-09-01")
    put(books, g["a"], [("2026-09-05", 70_000)], "2026-09-01", "2026-09-30")
    put(books, g["b"], [("2026-09-12", 30_000)], "2026-09-01", "2026-09-30")
    bot_budget = _budgets(books, g)
    with books.transaction() as c:
        spend(c, "6300", 120_000, day="2026-09-20", vendor=g["vendor"])
    put(books, g["a"], [("2026-09-05", 30_000)], "2026-09-01", "2026-09-30")
    put(books, g["b"], [("2026-09-12", 70_000)], "2026-09-01", "2026-09-30")
    with books.transaction() as c:
        attribution.refresh_platform(load(c, AT), SYSTEM, "google_ads", "2026-08-21", "2026-09-30")
        assert by_mate(c, "6300") == {A: 36_000, B: 84_000}
        ctx = load(c, AT)
        assert budgeting.actual(ctx, ctx.budgets()[bot_budget], "2026-09-01", "2026-09-30", live=True) == 30_000


def test_the_platform_share_module_names_the_window_and_the_contact(books: Store):
    g = google(books)
    with books.transaction() as c:
        keys = PS.contact_keys(load(c, AT), "google_ads")
        assert g["vendor"] in keys
        assert PS.window(load(c, AT), keys, "2026-10-15") == ("2026-09-15", "2026-10-15")


def test_an_unpaid_platform_bill_follows_the_split_in_the_cash_view(books: Store):
    """A bill to the platform's own contact is an accrual on the P&L and an unpaid cash correction. The correction takes
    the platform share too, so the cash view by teammate follows the split of the spend."""
    g = google(books)
    put(books, g["a"], [("2026-10-05", 60_000)], "2026-10-01", "2026-10-15")
    put(books, g["b"], [("2026-10-06", 40_000)], "2026-10-01", "2026-10-15")
    with books.transaction() as c:
        made = act(
            c,
            OWNER,
            "bill_create",
            {
                "vendor_id": g["vendor"],
                "bill_date": "2026-10-12",
                "lines": [{"account_id": account(c, "6300"), "amount": 100_000}],
            },
        )
        assert made.status == "committed", made.reason
        assert act(c, OWNER, "bill_approve", {"bill_id": str(made.result["bill_id"])}).status == "committed"
        assert by_mate(c, "6300") == {A: 60_000, B: 40_000}
        assert by_mate(c, "6300", "fin_cash_rollups") == {A: -60_000, B: -40_000}
