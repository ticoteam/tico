"""Ad spend in budgets (budgeting.py's live read, reads.py): an ad budget reads its platform's daily spend in place of
every charge and hold at that platform (card, ACH, bill, order) in each month a healthy import covers, closed or open,
never both, and the books in any other month; nothing comes out without the platform's figure going in; the tight
descriptors; the month-end pace; an ad budget only warns (no decline, no freeze, no owner notice); each bot sees its
own ad budget while its manager bot sees its reports'; and the warnings (ad_warnings.py) go to the spending bot alone,
once per level per period, and pause nothing.

Slugs echo TIDY's ad bots (paid-rental-ads, rental-social-ad-owner, their manager cmo) for realism only; nothing in the
code knows them."""
import sqlite3
from datetime import date

import pytest

from backend import bot_settings as BS
from backend import hubdb as H
from backend.credentials import Vault
from backend.finance import actions as FA
from backend.finance import ad_import as AI
from backend.finance import ad_warnings
from backend.finance import budgeting
from backend.finance import reads
from backend.finance.context import load
from backend.finance.core import budgets as B
from backend.finance.core.errors import NOT_FOUND, FinanceError
from backend.finance.store import ads as SA
from backend.finance.store import budgets as SB
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
    contacts, and budgets: a Marketing one over the whole line and, under it, each bot's (warn only). The ids by name."""
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
                ("marketing", {"name": "Marketing", "amount": 1_000_000}),
                ("google", {"name": "Rental Google Ad Owner", "teammate": "bot:paid-rental-ads",
                            "vendor": out["google_ads_vendor"], "amount": 300_000}),
                ("meta", {"name": "Rental Social Ad Owner", "teammate": "bot:rental-social-ad-owner",
                          "vendor": out["meta_ads_vendor"], "amount": 300_000})):
            under = {"parent_id": out["marketing"]} if key != "marketing" else {}
            done = act(c, OWNER, "budget_create", {**body, **under, "account_id": adv, "at_limit": "warn"}, at=at)
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
        # The whole line names no platform, so it reads the books: the expense of record, every charge in it.
        assert mk["actual"] == 40_000 + 30_000 + 9_000 + 5_000 and mk["spent_from"] == "books" and mk["as_of"] is None
        # Pace: 76,000 over the 14.5 days from October 1 to the import's as_of (Oct 15 12:00), times 31.
        assert gs["pace_end"] == 162_483
        # Enforcement reads the books alone, and an ad budget only warns anyway.
        ctx = load(c, AT)
        assert budgeting.state(ctx, w["google"], date(2026, 10, 15)).actual == 40_000
        assert budgeting.state(ctx, w["marketing"], date(2026, 10, 15)).actual == 84_000
        # The metrics read the same, with the pace.
        m = reads.metric(c, OWNER, "budget_remaining", budget_id=w["google"], at=AT)
        assert m["value"] == 300_000 - 76_000 and m["pace_month_end"] == 162_483
        assert m["spent_from"] == "platform" and m["platform"] == "google_ads" and m["platform_as_of"] == AT
        pct = reads.metric(c, OWNER, "budget_pct_used", budget_id=w["marketing"], at=AT)
        assert pct["spent_from"] == "books" and pct["platform"] == ""


def google_state(store: Store, on: str | None = None) -> dict[str, object]:
    with store.read() as c:
        row = rows_by_name(c, OWNER, on=on)["Rental Google Ad Owner"]
        assert row["platform"] == "google_ads"
        found = row["state"]
        assert isinstance(found, dict)
        return found                                                                # type: ignore[return-value]


@pytest.mark.parametrize("why", ["off", "reconnect", "failed", "stale", "never", "later history", "no bot", "EUR"])
def test_never_cut_without_the_platforms_figure(books: Store, why: str):
    """An import that is off, needs a reconnect, failed, last succeeded over 12 hours ago, never ran or reads from after
    the month, or an ad account with no bot or in another currency: the charges stay and the budget reads the books."""
    w = world(books)
    platform_spend(books, w["google_ads"], [("2026-10-03", 50_000)], "2026-10-01", "2026-10-15")
    with books.transaction() as c:
        spend(c, "6300", 40_000, day="2026-10-12", teammate="bot:paid-rental-ads", vendor=w["google_ads_vendor"])
        change = {"off": "enabled=0", "reconnect": "status='reconnect'", "failed": "status='failed'",
                  "stale": "last_import_at='2026-10-14T23:59:00.000000Z'", "never": "last_import_at=NULL",
                  "later history": "history_from='2026-10-02'"}.get(why)
        if change:
            c.execute(f"UPDATE fin_ad_imports SET {change} WHERE tool_id='gads'")
        elif why == "no bot":
            SA.set_bot(c, w["google_ads"], None, AT)
        else:
            c.execute("UPDATE fin_ad_accounts SET currency='EUR' WHERE id=?", (w["google_ads"],))
            c.execute("UPDATE fin_ad_spends SET currency='EUR' WHERE ad_account_id=?", (w["google_ads"],))
    g = google_state(books)
    assert g["actual"] == 40_000 and g["spent_from"] == "books" and g["as_of"] is None


def test_an_import_fresh_within_12_hours_still_counts(books: Store):
    w = world(books)
    platform_spend(books, w["google_ads"], [("2026-10-03", 50_000)], "2026-10-01", "2026-10-15")
    with books.transaction() as c:
        c.execute("UPDATE fin_ad_imports SET last_import_at='2026-10-15T00:30:00.000000Z' WHERE tool_id='gads'")
    g = google_state(books)
    assert g["actual"] == 50_000 and g["spent_from"] == "platform" and g["as_of"] == "2026-10-15T00:30:00.000000Z"


def test_every_covered_month_reads_the_platform_closed_or_not(books: Store):
    w = world(books)
    platform_spend(books, w["google_ads"], [("2026-09-05", 30_000)], "2026-09-01", "2026-09-30")
    platform_spend(books, w["google_ads"], [("2026-10-03", 50_000)], "2026-10-01", "2026-10-15")
    with books.transaction() as c:
        spend(c, "6300", 25_000, day="2026-09-20", teammate="bot:paid-rental-ads", vendor=w["google_ads_vendor"])
        # September's last charge posts October 2: it comes out of October, which Google's October figure stands for.
        spend(c, "6300", 5_000, day="2026-10-02", teammate="bot:paid-rental-ads", vendor=w["google_ads_vendor"])
        assert close_month(c, "2026-09").status == "committed"
    sept = google_state(books, on="2026-09-15")
    assert sept["actual"] == 30_000 and sept["spent_from"] == "platform" and sept["as_of"] == AT
    assert sept["pace_end"] == 30_000                                   # the month is over: what was spent
    assert google_state(books)["actual"] == 50_000
    with books.read() as c:
        report = reads.budget_vs_actual(c, OWNER, "2026-09-01", "2026-10-31", "month", at=AT)
        row = next(r for r in report["budgets"] if r["id"] == w["google"])
        assert row["actual"] == [30_000, 50_000] and row["platform"] == "google_ads"
        assert report["platform_as_of"] == AT


def test_a_month_before_the_import_reads_the_books(books: Store):
    w = world(books)
    platform_spend(books, w["google_ads"], [("2026-09-05", 30_000)], "2026-09-01", "2026-09-30")
    platform_spend(books, w["google_ads"], [("2026-10-03", 50_000)], "2026-10-01", "2026-10-15")
    with books.transaction() as c:
        spend(c, "6300", 25_000, day="2026-09-20", teammate="bot:paid-rental-ads", vendor=w["google_ads_vendor"])
        c.execute("UPDATE fin_ad_imports SET history_from='2026-10-01' WHERE tool_id='gads'")
    sept = google_state(books, on="2026-09-15")
    assert sept["actual"] == 25_000 and sept["spent_from"] == "books" and sept["as_of"] is None
    with books.read() as c:
        report = reads.budget_vs_actual(c, OWNER, "2026-09-01", "2026-10-31", "month", at=AT)
        row = next(r for r in report["budgets"] if r["id"] == w["google"])
        assert row["actual"] == [25_000, 50_000]
    with books.read() as c:
        ctx = load(c, AT)
        quarter = budgeting.live_ads(ctx, ctx.budgets()[w["google"]], "2026-09-01", "2026-10-31")
        assert quarter.spent_from == "mixed" and quarter.live_months == 1 and quarter.books_months == 1


def _money_account(c: sqlite3.Connection) -> str:
    book = str(c.execute("SELECT id FROM fin_books").fetchone()[0])
    c.execute("INSERT INTO fin_money_accounts(id,book_id,kind,name,created_at,created_by) "
              "VALUES('ma-chk',?,'bank','Checking',?,?)", (book, AT, OWNER))
    return book


def test_every_hold_at_the_platform_comes_out_card_ach_bill_and_order(books: Store):
    """The $3,000 ACH to Google (and a card authorization, a bill and an order line) is in the platform's figure: a
    covered month takes its hold out. A hold for another vendor stays."""
    w = world(books)
    platform_spend(books, w["google_ads"], [("2026-10-03", 50_000)], "2026-10-01", "2026-10-15")
    gv = w["google_ads_vendor"]
    with books.transaction() as c:
        book = _money_account(c)
        other = str(c.execute("INSERT INTO fin_contacts(id,book_id,name,kind,created_at,created_by,updated_at) "
                              "VALUES('k-bill',?,'Billboard Co','vendor',?,?,?) RETURNING id",
                              (book, AT, OWNER, AT)).fetchone()[0])
        c.execute("INSERT INTO fin_feed_txns(id,money_account_id,provider_id,txn_date,amount,currency,counterparty,"
                  "created_at,created_by) VALUES('t-card','ma-chk','p1','2026-10-12',-7000,'USD','GOOGLE *ADS 4421',?,?)",
                  (AT, OWNER))
        c.execute("INSERT INTO fin_recipients(id,name,method,details_hash,contact_id,created_at,created_by) "
                  "VALUES('r-g','Google Ads','ach','h',?,?,?)", (gv, AT, OWNER))
        c.execute("INSERT INTO fin_payments(id,book_id,action_id,idempotency_key,recipient_id,recipient_version,"
                  "money_account_id,amount,currency,method,account_id,created_at,created_by) "
                  "VALUES('p-ach',?,'a1','k1','r-g',1,'ma-chk',300000,'USD','ach',?,?,?)", (book, w["adv"], AT, OWNER))
        for bid, vid in (("b-google", gv), ("b-other", other)):
            c.execute("INSERT INTO fin_bills(id,book_id,vendor_id,bill_date,currency,created_at,created_by) "
                      "VALUES(?,?,?,'2026-10-10','USD',?,?)", (bid, book, vid, AT, OWNER))
        c.execute("INSERT INTO fin_purchase_orders(id,book_id,vendor_id,po_date,currency,created_at,created_by) "
                  "VALUES('po-google',?,?,'2026-10-10','USD',?,?)", (book, gv, AT, OWNER))
        for source, sid, amount in (("card", "t-card", 7_000), ("payment", "p-ach", 300_000),
                                    ("bill", "b-google:1", 4_000), ("purchase_order", "po-google:2", 3_000),
                                    ("bill", "b-other:1", 2_000)):
            SB.insert_hold(c, budget_id=w["google"], amount=amount, currency="USD", source=source, source_id=sid,
                           target="", held_on="2026-10-12", expires_at=None, by=OWNER, at=AT)
    g = google_state(books)
    assert g["actual"] == 50_000 and g["held"] == 2_000 and g["spent_from"] == "platform"
    with books.read() as c:
        ctx = load(c, AT)
        assert budgeting.state(ctx, w["google"], date(2026, 10, 15)).held == 316_000      # enforcement: the books
    with books.transaction() as c:
        c.execute("UPDATE fin_ad_imports SET status='reconnect' WHERE tool_id='gads'")
    g = google_state(books)
    assert g["actual"] == 0 and g["held"] == 316_000 and g["spent_from"] == "books"         # nothing vanishes


@pytest.mark.parametrize("period,ref,spent,expected", [
    ("month", "2026-11-01T23:00:00Z", 5_000, None),             # under a whole day is no rate
    ("month", "2026-11-02T00:00:00Z", 1_000, 30_000),           # day 2, one day of spend: 30x in a 30-day month
    ("month", "2026-11-02T12:00:00Z", 1_000, 20_000),           # a day and a half: fractional days
    ("month", "2026-11-06T12:00:00Z", 76_000, 414_545),         # half up
    ("month", "2026-12-03T00:00:00Z", 90_000, 90_000),          # past the end: what was spent
    ("quarter", "2026-11-15T00:00:00Z", 45_000, 92_000),        # 45 days of Q4's 92
    ("year", "2026-11-15T00:00:00Z", 1_000, None),
    ("once", "2026-11-15T00:00:00Z", 1_000, None),
])
def test_pace_end_runs_to_the_imports_as_of(period: str, ref: str, spent: int, expected: int | None):
    b = B.Budget(id="b", book_id="k", name="Ads", period=period)
    bounds = {"month": (date(2026, 11, 1), date(2026, 11, 30)), "quarter": (date(2026, 10, 1), date(2026, 12, 31)),
              "year": (date(2026, 1, 1), date(2026, 12, 31)), "once": (date(2026, 1, 1), date(9999, 12, 31))}[period]
    assert budgeting.pace_end(b, bounds, ref, spent) == expected


@pytest.mark.parametrize("text,platform", [
    ("GOOGLE *ADS 4421", "google_ads"), ("google*ads1234567", "google_ads"), ("GOOGLE ADS", "google_ads"),
    ("Google Ads", "google_ads"), ("GOOGLE*ADS", "google_ads"), ("FACEBK *8FJ2KL", "meta_ads"),
    ("facebk*abc", "meta_ads"), ("FB.ME/ADS", "meta_ads"), ("fb.me/ads 650-543-4800", "meta_ads"),
    ("FACEBOOK ADS", "meta_ads"), ("Meta Ads", "meta_ads"), ("META ADS 123", "meta_ads"),
    # Not ad spend: AdSense pays out, and Meta's other charges are not its ads.
    ("GOOGLE *ADSENSE", ""), ("GOOGLE ADSENSE", ""), ("Google Workspace", ""), ("GOOGLE *CLOUD", ""), ("AdWords", ""),
    ("META PLATFORMS INC", ""), ("FACEBOOK", ""), ("Facebook Workplace", ""), ("META *QUEST", ""),
    ("Meta Verified", ""), ("Meta Blueprint", ""), ("FB.ME/ADSX", ""), ("FACEBOOK ADSMANAGER", ""), ("Meta", ""),
    ("Metabase", ""), ("Billboard Co", ""),
])
def test_descriptors_are_tight(text: str, platform: str):
    assert SA.descriptor_platform(text) == platform


def test_the_imports_own_contacts_are_the_platforms(books: Store):
    w = world(books)
    with books.read() as c:
        book = str(c.execute("SELECT id FROM fin_books").fetchone()[0])
        assert SA.vendor_platform(c, book, w["google_ads_vendor"]) == "google_ads"
        assert SA.vendor_platform(c, book, w["meta_ads_vendor"]) == "meta_ads"
        assert SA.vendor_platform(c, book, "google *ads 4421") == "google_ads"          # a posted name key
        assert SA.vendor_platform(c, book, "meta platforms inc") == ""


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


# ------------------------------------------------------------------------------------------------ warnings
WARN_AT = "2026-10-06T12:00:00.000000Z"
FOOT = "This is a warning only. Nothing has been paused."


def warning_notes(c: sqlite3.Connection) -> list[tuple[str, str]]:
    return [(str(r[0]), str(r[1])) for r in c.execute(
        "SELECT to_actor, body FROM notes WHERE from_actor=? AND body LIKE 'Finance · budget warning%' ORDER BY rowid",
        (H.KEEPER,))]


def warn(store: Store, at: str = WARN_AT) -> list[tuple[str, tuple[str, ...]]]:
    with store.transaction() as c:
        return [(s.bot, s.levels) for s in ad_warnings.run(c, at)]


def test_the_spending_bot_gets_one_line_per_level_and_nobody_else(books: Store):
    w = world(books, WARN_AT)
    google = w["google_ads"]
    platform_spend(books, google, [("2026-10-02", 40_000), ("2026-10-05", 36_000)], "2026-10-01", "2026-10-06")
    assert warn(books) == [("bot:paid-rental-ads", ("pace",))]
    with books.read() as c:
        assert warning_notes(c) == [("bot:paid-rental-ads", "Finance · budget warning\n"
                                     "Rental Google Ad Owner · Google Ads · October\n"
                                     "Spent $760 of $3,000 (25%), but at today's pace the month ends near $4,284, "
                                     "which is over budget.\n" + FOOT)]
    assert warn(books) == []                                            # once per level
    platform_spend(books, google, [("2026-10-02", 200_000), ("2026-10-05", 45_000)], "2026-10-01", "2026-10-06")
    assert warn(books) == [("bot:paid-rental-ads", ("warn",))]
    with books.read() as c:
        line = warning_notes(c)[-1][1].split("\n")[2]
        assert line == ("Spent $2,450 of $3,000 (82%), past the 80% warning level. At today's pace the month ends "
                        "near $13,809, which is over budget.")
    platform_spend(books, google, [("2026-10-02", 200_000), ("2026-10-05", 110_000)], "2026-10-01", "2026-10-06")
    assert warn(books) == [("bot:paid-rental-ads", ("limit",))]
    assert warn(books) == []
    with books.read() as c:
        sent = warning_notes(c)
        assert sent[-1][1].split("\n")[2] == "Spent $3,100 of $3,000 (103%): the month's budget is used up."
        # The bot alone: not its manager bot, not a person, not the Marketing budget with no bot.
        assert {to for to, _ in sent} == {"bot:paid-rental-ads"} and len(sent) == 3
        # Warn only: the budget's own action is untouched and the books-based state stays under its limit.
        assert c.execute("SELECT at_limit FROM fin_budgets WHERE id=?", (w["google"],)).fetchone()[0] == "warn"
        assert not budgeting.state(load(c, WARN_AT), w["google"], date(2026, 10, 6)).at_limit


def test_a_jump_past_two_levels_is_one_line(books: Store):
    w = world(books, WARN_AT)
    platform_spend(books, w["meta_ads"], [("2026-10-03", 260_000)], "2026-10-01", "2026-10-06")
    assert warn(books) == [("bot:rental-social-ad-owner", ("warn", "pace"))]
    with books.read() as c:
        (to, body), = warning_notes(c)
        assert to == "bot:rental-social-ad-owner"
        assert body.split("\n")[1] == "Rental Social Ad Owner · Meta Ads · October"
    assert warn(books) == []


def test_the_import_pass_gives_the_warnings(books: Store):
    w = world(books, WARN_AT)
    platform_spend(books, w["google_ads"], [("2026-10-05", 76_000)], "2026-10-01", "2026-10-06")
    assert AI.pass_once(books, Vault(books), now=WARN_AT) == {}          # nothing due: imported at WARN_AT
    with books.read() as c:
        assert [to for to, _ in warning_notes(c)] == ["bot:paid-rental-ads"]


def test_the_owner_gets_no_notice_for_an_ad_budget(books: Store):
    w = world(books, WARN_AT)

    def notices(c: sqlite3.Connection, name: str) -> int:
        return int(c.execute("SELECT count(*) FROM messages WHERE recipient_id=? AND kind='notice' AND body LIKE ?",
                             (OWNER, f"%{name}%")).fetchone()[0])

    with books.transaction() as c:
        soft = act(c, OWNER, "budget_create", {"name": "Tools", "account_id": account(c, "6100"), "amount": 1_000},
                   at=WARN_AT)
        assert soft.status == "committed", soft
        # Meta ads bought on the 6100 line by no bot: an ad budget with no bot to tell.
        nobody = act(c, OWNER, "budget_create", {"name": "Meta on tools", "account_id": account(c, "6100"),
                                                 "vendor": w["meta_ads_vendor"], "parent_id": soft.result["budget_id"],
                                                 "amount": 500}, at=WARN_AT)
        assert nobody.status == "committed", nobody
        spend(c, "6100", 600, day="2026-10-05", vendor=w["meta_ads_vendor"])
        # Books-based crossings: the bot's ad budget (its card charge) and a budget with no ad spend in it.
        spend(c, "6300", 260_000, day="2026-10-05", teammate="bot:paid-rental-ads", vendor=w["google_ads_vendor"])
        spend(c, "6100", 900, day="2026-10-05")
        assert FA.drain_events(c, WARN_AT) >= 2
        assert notices(c, "budget is") == 1 and notices(c, "The Tools budget") == 1
        assert notices(c, "Rental Google Ad Owner") == 0 and notices(c, "Meta on tools") == 0
        ctx = load(c, WARN_AT)
        assert ad_warnings.bot_of(ctx, ctx.budgets()[w["marketing"]]) == ""
        assert ad_warnings.bot_of(ctx, ctx.budgets()[w["google"]]) == "bot:paid-rental-ads"
        assert ad_warnings.bot_of(ctx, ctx.budgets()[str(nobody.result["budget_id"])]) == ""
    # And no bot warning for it either: only the ad budgets with a bot are warned.
    assert all(bot == "bot:paid-rental-ads" for bot, _ in warn(books))


# ------------------------------------------------------------------------------------------------ warn only
def test_an_ad_budget_only_warns(books: Store):
    w = world(books)
    with books.transaction() as c:
        for kind, body in (("budget_create", {"name": "Google again", "account_id": w["adv"], "parent_id": w["marketing"],
                                              "vendor": "GOOGLE *ADS", "at_limit": "decline"}),
                           ("budget_update", {"budget_id": w["google"], "at_limit": "hold_payment"}),
                           ("budget_update", {"budget_id": w["meta"], "at_limit": "ask"})):
            refused = act(c, OWNER, kind, body, at=AT)
            assert refused.code == "invalid" and "only warns" in refused.reason, refused
        # Left out, the at-limit is warn; a budget on another vendor keeps what it asks for.
        made = act(c, OWNER, "budget_create", {"name": "Meta descriptor", "account_id": w["adv"], "vendor": "FACEBK *",
                                               "parent_id": w["marketing"]}, at=AT)
        assert made.status == "committed", made
        other = act(c, OWNER, "budget_create", {"name": "Billboards", "account_id": w["adv"], "vendor": "Billboard Co",
                                                "parent_id": w["marketing"], "at_limit": "decline"}, at=AT)
        assert other.status == "committed", other
        limits = dict(c.execute("SELECT name, at_limit FROM fin_budgets").fetchall())
        assert limits["Meta descriptor"] == "warn" and limits["Billboards"] == "decline"
        assert act(c, OWNER, "budget_update", {"budget_id": w["google"], "amount": 200_000}, at=AT).status == "committed"


def test_a_google_card_charge_over_an_ad_budget_is_not_declined_and_freezes_no_card(books: Store):
    from backend.finance import cards
    from backend.tests.test_finance_fixtures import FakeMercury, connect
    connect(books, FakeMercury())
    w = world(books)
    with books.transaction() as c:
        made = act(c, OWNER, "card_create", {"holder": "bot:paid-rental-ads", "budget_id": w["google"],
                                             "amount": 20_000, "provider_user_id": "u-ads"}, at=AT)
        assert made.status == "committed", made
        card = str(made.result["card_id"])
        # As if made before warn only: the data fix sets it to warn, and enforcement reads it as warn regardless.
        c.execute("UPDATE fin_budgets SET at_limit='decline' WHERE id=?", (w["google"],))
        spend(c, "6300", 310_000, day="2026-10-12", teammate="bot:paid-rental-ads", vendor=w["google_ads_vendor"])
        ctx = load(c, AT)
        assert budgeting.state(ctx, w["google"], date(2026, 10, 15)).at_limit
        verdict = budgeting.decide(ctx, w["google"], 5_000)
        assert verdict.allowed and verdict.action != "decline"
        hold = budgeting.place(ctx, budget_scope=w["google"], amount=5_000, currency="USD", source="card",
                               source_id="auth-1", expires_at=None)
        assert hold.allowed
        FA.drain_events(c, AT)
        status = c.execute("SELECT status FROM fin_cards WHERE id=?", (card,)).fetchone()[0]
        assert status == "active"
        assert cards.blocking_budget(ctx, w["google"], date(2026, 10, 15)) == ""
        # Paused at 0: still nothing stops it.
        assert act(c, OWNER, "budget_update", {"budget_id": w["google"], "amount": 0}, at=AT).status == "committed"
        assert c.execute("SELECT status FROM fin_cards WHERE id=?", (card,)).fetchone()[0] == "active"
        assert cards.blocking_budget(load(c, AT), w["google"], date(2026, 10, 15)) == ""


def test_the_data_fix_sets_existing_ad_budgets_to_warn(books: Store):
    import importlib
    fix = importlib.import_module("backend.migrations.20261106085449_ad_budgets_warn_only")
    w = world(books)
    with books.transaction() as c:
        made = act(c, OWNER, "budget_create", {"name": "Billboards", "account_id": w["adv"], "vendor": "Billboard Co",
                                               "parent_id": w["marketing"], "at_limit": "decline"}, at=AT)
        assert made.status == "committed", made
        named = act(c, OWNER, "budget_create", {"name": "Descriptor", "account_id": w["adv"], "vendor": "GOOGLE *ADS",
                                                "parent_id": w["marketing"]}, at=AT)
        assert named.status == "committed", named
        c.execute("UPDATE fin_budgets SET at_limit='decline' WHERE vendor<>''")
        fix.up(c)
        fix.up(c)
        limits = dict(c.execute("SELECT name, at_limit FROM fin_budgets").fetchall())
    assert limits == {"Marketing": "warn", "Rental Google Ad Owner": "warn", "Rental Social Ad Owner": "warn",
                      "Descriptor": "warn", "Billboards": "decline"}
