"""Ad spend in budgets (budgeting.py's live read, reads.py): in an open month a budget reads the platforms' daily spend
in place of their card charges, never both; a closed month reads the books; the month-end pace; and each bot sees its
own ad budget while its manager bot sees its reports'.

Slugs echo TIDY's ad bots (paid-rental-ads, rental-social-ad-owner, their manager cmo) for realism only; nothing in the
code knows them."""
import sqlite3
from datetime import date

import pytest

from backend import bot_settings as BS
from backend import hubdb as H
from backend.finance import ad_import as AI
from backend.finance import budgeting
from backend.finance import reads
from backend.finance.context import load
from backend.finance.core import budgets as B
from backend.finance.core.errors import NOT_FOUND, FinanceError
from backend.finance.store import ads as SA
from backend.store import Store
from backend.tests.test_finance_fixtures import (AT, OWNER, account, act, books, close_month,  # noqa: F401  (fixtures)
                                                 finance_template, spend, store)

ADS = {"cmo": ("sales", "human:dan"), "paid-rental-ads": ("sales", "bot:cmo"),
       "rental-social-ad-owner": ("sales", "bot:cmo")}


def _bots(c: sqlite3.Connection) -> None:
    H.sync_registry(c, {slug: {"name": slug, "runtime": "fake", "status": "active"} for slug in ADS}, None)
    for slug, (team, boss) in ADS.items():
        c.execute("INSERT OR REPLACE INTO bot_config(bot,team,owner_id,reports_to) VALUES(?,?,?,?)",
                  (slug, team, OWNER, boss))
        BS.write(c, slug, {"team": team})


def _tool(c: sqlite3.Connection, tid: str, service: str) -> None:
    c.execute("INSERT INTO tools(id,name,service,base_url,auth_scheme,check_path,delivery,created_by,created_at,"
              "updated_at) VALUES(?,?,?,'','bearer','','computer',?,?,?)", (tid, tid, service, OWNER, AT, AT))


def world(store: Store, at: str = AT) -> dict[str, str]:
    """An Advertising line, Google Ads and Meta imports booking to it, one ad account per bot, the platforms' vendor
    contacts, and budgets: each bot's (warn only) and a Marketing one over the whole line. The ids by name."""
    with store.transaction() as c:
        _bots(c)
        made = act(c, OWNER, "account_create", {"code": "6300", "name": "Advertising", "type": "expense",
                                                "parent_id": account(c, "6000")}, at=at)
        adv = str(made.result["account_id"])
        out = {"adv": adv}
        for tid, service, platform, external, name, bot in (
                ("gads", "google-ads", "google_ads", "123", "Paid rental", "paid-rental-ads"),
                ("meta", "meta-ads", "meta_ads", "act_1", "Rental social", "rental-social-ad-owner")):
            _tool(c, tid, service)
            SA.save_import(c, SA.AdImport(id="", tool_id=tid, platform=platform, enabled=True,
                                          history_from="2026-01-01", account_id=adv, created_by=OWNER), at)
            iid = str(c.execute("SELECT id FROM fin_ad_imports WHERE tool_id=?", (tid,)).fetchone()[0])
            SA.finish(c, iid, "ok", "", at, imported=True)
            out[platform] = SA.upsert_account(c, tid, platform, external, name, "USD", at)
            SA.set_bot(c, out[platform], bot, at)
            out[platform + "_vendor"] = AI.ensure_vendor(c, platform, at)
        for key, body in (
                ("google", {"name": "Rental Google Ad Owner", "teammate": "bot:paid-rental-ads",
                            "vendor": out["google_ads_vendor"], "amount": 300_000}),
                ("meta", {"name": "Rental Social Ad Owner", "teammate": "bot:rental-social-ad-owner",
                          "vendor": out["meta_ads_vendor"], "amount": 300_000}),
                ("marketing", {"name": "Marketing", "amount": 1_000_000})):
            done = act(c, OWNER, "budget_create", {**body, "account_id": adv, "at_limit": "warn"}, at=at)
            assert done.status == "committed", done
            out[key] = str(done.result["budget_id"])
    return out


def platform_spend(store: Store, ad_account: str, rows: list[tuple[str, int]], start: str, end: str) -> None:
    with store.transaction() as c:
        SA.replace_spend(c, ad_account, start, end, [SA.SpendRow(d, "c1", "Spring", cents, "USD") for d, cents in rows],
                         AT)


def rows_by_name(c: sqlite3.Connection, viewer: str, at: str = AT, on: str | None = None) -> dict[str, dict[str, object]]:
    return {str(r["name"]): r for r in reads.budgets(c, viewer, at=at, on=on)["budgets"]}   # type: ignore[union-attr]


def test_platform_spend_stands_in_for_the_card_charge_never_both(books: Store):
    w = world(books)
    platform_spend(books, w["google_ads"], [("2026-10-03", 50_000), ("2026-10-14", 26_000)], "2026-10-01", "2026-10-15")
    platform_spend(books, w["meta_ads"], [("2026-10-05", 12_000)], "2026-10-01", "2026-10-15")
    with books.transaction() as c:
        # The card charges for that spend: the bot's own, filed under the Google Ads contact; a company card's, under
        # the names the card feeds give; and an ad bought elsewhere, which stays in the books' figure.
        spend(c, "6300", 40_000, day="2026-10-12", teammate="bot:paid-rental-ads", vendor=w["google_ads_vendor"])
        spend(c, "6300", 30_000, day="2026-10-12", vendor="GOOGLE *ADS 4421")
        spend(c, "6300", 9_000, day="2026-10-13", vendor="FACEBK *8FJ2KL")
        spend(c, "6300", 7_000, day="2026-10-13", vendor="META PLATFORMS INC")
        spend(c, "6300", 5_000, day="2026-10-13", vendor="Billboard Co")
    with books.read() as c:
        rows = rows_by_name(c, OWNER)
        google, meta, marketing = rows["Rental Google Ad Owner"], rows["Rental Social Ad Owner"], rows["Marketing"]
        gs, ms, mk = google["state"], meta["state"], marketing["state"]
        assert isinstance(gs, dict) and isinstance(ms, dict) and isinstance(mk, dict)
        assert google["platform"] == "google_ads" and meta["platform"] == "meta_ads" and marketing["platform"] == ""
        # The bot's budget: Google's 76,000 and not the 40,000 charge on top.
        assert gs["actual"] == 76_000 and gs["spent_from"] == "platform" and gs["as_of"] == AT
        assert ms["actual"] == 12_000 and ms["spent_from"] == "platform"
        # The whole line: both platforms' spend plus the billboard; no card charge from either platform.
        assert mk["actual"] == 76_000 + 12_000 + 5_000 and mk["spent_from"] == "mixed" and mk["as_of"] == AT
        # Pace: 76,000 over 15 days of October's 31.
        assert gs["pace_end"] == 157_067
        # Enforcement still reads the books alone, so platform figures never block anything.
        ctx = load(c, AT)
        assert budgeting.state(ctx, w["google"], date(2026, 10, 15)).actual == 40_000
        assert budgeting.state(ctx, w["marketing"], date(2026, 10, 15)).actual == 91_000
        # The metrics read the same, with the pace.
        m = reads.metric(c, OWNER, "budget_remaining", budget_id=w["google"], at=AT)
        assert m["value"] == 300_000 - 76_000 and m["pace_month_end"] == 157_067
        assert m["spent_from"] == "platform" and m["platform"] == "google_ads" and m["platform_as_of"] == AT
        pct = reads.metric(c, OWNER, "budget_pct_used", budget_id=w["marketing"], at=AT)
        assert pct["spent_from"] == "mixed" and pct["platform"] == ""


def test_spend_with_no_import_reads_the_books(books: Store):
    w = world(books)
    platform_spend(books, w["google_ads"], [("2026-10-03", 50_000)], "2026-10-01", "2026-10-15")
    with books.transaction() as c:
        spend(c, "6300", 40_000, day="2026-10-12", teammate="bot:paid-rental-ads", vendor=w["google_ads_vendor"])
        c.execute("UPDATE fin_ad_imports SET enabled=0 WHERE tool_id='gads'")
    with books.read() as c:
        g = rows_by_name(c, OWNER)["Rental Google Ad Owner"]
        assert g["platform"] == ""
        assert g["state"]["actual"] == 40_000 and g["state"]["spent_from"] == "books"     # type: ignore[index]
        assert g["state"]["as_of"] is None                                                  # type: ignore[index]


def test_a_closed_month_reads_the_books(books: Store):
    w = world(books)
    platform_spend(books, w["google_ads"], [("2026-09-05", 30_000)], "2026-09-01", "2026-09-30")
    platform_spend(books, w["google_ads"], [("2026-10-03", 50_000)], "2026-10-01", "2026-10-15")
    with books.transaction() as c:
        spend(c, "6300", 25_000, day="2026-09-20", teammate="bot:paid-rental-ads", vendor=w["google_ads_vendor"])
    with books.read() as c:
        sept = rows_by_name(c, OWNER, on="2026-09-15")["Rental Google Ad Owner"]["state"]
        assert sept["actual"] == 30_000 and sept["spent_from"] == "platform"      # type: ignore[index]
    with books.transaction() as c:
        assert close_month(c, "2026-09").status == "committed"
    with books.read() as c:
        sept = rows_by_name(c, OWNER, on="2026-09-15")["Rental Google Ad Owner"]["state"]
        assert sept["actual"] == 25_000 and sept["spent_from"] == "books"         # type: ignore[index]
        assert sept["as_of"] is None and sept["pace_end"] == 50_000               # type: ignore[index]
        report = reads.budget_vs_actual(c, OWNER, "2026-09-01", "2026-10-31", "month", at=AT)
        row = next(r for r in report["budgets"] if r["id"] == w["google"])
        assert row["actual"] == [25_000, 50_000] and row["platform"] == "google_ads"
        assert report["platform_as_of"] == AT


@pytest.mark.parametrize("period,on,spent,expected", [
    ("month", date(2026, 10, 1), 5_000, None),                  # one day is no rate
    ("month", date(2026, 10, 2), 1_000, 15_500),                # 1,000 / 2 days x 31
    ("month", date(2026, 10, 6), 76_000, 392_667),              # half up
    ("month", date(2026, 10, 31), 90_000, 90_000),              # the last day: what was spent
    ("quarter", date(2026, 11, 15), 46_000, 92_000),            # 46 days of Q4's 92
    ("year", date(2026, 10, 15), 1_000, None),
    ("once", date(2026, 10, 15), 1_000, None),
])
def test_pace_end(period: str, on: date, spent: int, expected: int | None):
    b = B.Budget(id="b", book_id="k", name="Ads", period=period)
    bounds = {"month": (date(2026, 10, 1), date(2026, 10, 31)), "quarter": (date(2026, 10, 1), date(2026, 12, 31)),
              "year": (date(2026, 1, 1), date(2026, 12, 31)), "once": (date(2026, 1, 1), date(9999, 12, 31))}[period]
    assert budgeting.pace_end(b, bounds, on, spent) == expected


@pytest.mark.parametrize("text,platform", [
    ("GOOGLE *ADS 4421", "google_ads"), ("google*ads1234567", "google_ads"), ("Google Ads", "google_ads"),
    ("AdWords", "google_ads"), ("FACEBK *8FJ2KL", "meta_ads"), ("META PLATFORMS INC", "meta_ads"), ("Meta", "meta_ads"),
    ("Meta Ads", "meta_ads"), ("FB *ADS", "meta_ads"), ("Google Workspace", ""), ("GOOGLE *CLOUD", ""),
    ("Metabase", ""), ("Billboard Co", ""),
])
def test_platform_vendors_are_found_by_name(text: str, platform: str):
    assert next((p for p, rx in budgeting.AD_VENDORS.items() if rx.search(text.casefold())), "") == platform


def test_each_bot_sees_its_own_ad_budget_and_the_manager_bot_its_reports(books: Store):
    w = world(books)
    platform_spend(books, w["google_ads"], [("2026-10-03", 50_000)], "2026-10-01", "2026-10-15")
    with books.read() as c:
        assert set(rows_by_name(c, "bot:paid-rental-ads")) == {"Rental Google Ad Owner"}
        assert set(rows_by_name(c, "bot:rental-social-ad-owner")) == {"Rental Social Ad Owner"}
        manager = rows_by_name(c, "bot:cmo")
        assert {"Rental Google Ad Owner", "Rental Social Ad Owner"} <= set(manager)
        assert manager["Rental Google Ad Owner"]["state"]["actual"] == 50_000      # type: ignore[index]
        own = reads.metric(c, "bot:paid-rental-ads", "budget_pct_used", budget_id=w["google"], at=AT)
        assert own["spent_from"] == "platform" and own["pace_month_end"] is not None
        with pytest.raises(FinanceError) as refused:
            reads.metric(c, "bot:rental-social-ad-owner", "budget_remaining", budget_id=w["google"], at=AT)
        assert refused.value.code == NOT_FOUND and str(refused.value.message) == "Not in your view."
