"""Finance's ad campaigns (backend/finance/campaigns.py, store/ads.py, ad_import.py): the import stores each campaign once
with its clicks and conversions (Meta's scalar `conversions` wins over `actions`; both offsite and onsite conversion types
count), a restated day overwrites, a campaign's owner (a teammate or a team) decides the platform share of the charges
its spend is in and re-judges them, undo puts the owner back, the cost per conversion is null with no conversions, and
the reads (campaigns report, ad_spend and per_conversion metrics, the campaign search kind) cut the same rows.

Slugs echo TIDY's (paid-rental-ads, consumer-google-ad-owner) for realism only; nothing depends on them. Visibility for a
teammate who sees only their own campaigns is not covered here; the full-detail reader is."""

from backend.credentials import Vault
from backend.finance import ad_import as AI
from backend.finance import campaigns as CP
from backend.finance import reads as RD
from backend.finance.store import ads as SA
from backend.finance.store import attribution as ATT
from backend.store import Store
from backend.tests.test_api import api  # noqa: F401  (fixture)
from backend.tests.test_finance_ad_spend import http, meta
from backend.tests.test_finance_fixtures import AT, OWNER, account, act, books, finance_template, spend, store  # noqa: F401
from backend.tests.test_finance_platform_share import A, B, by_mate, google

ORIGIN = "2026-10-01"
END = "2026-10-31"


def feed(
    store: Store, account_id: str, campaign: str, name: str, day: str, cents: int, clicks: int = 0, conv: float = 0.0
) -> None:
    """The platform's word for one campaign on one ad account for one day (a whole replace of the month)."""
    with store.transaction() as c:
        SA.upsert_campaign(c, account_id, campaign, name, ORIGIN, END, AT)
        SA.replace_spend(c, account_id, ORIGIN, END, [SA.SpendRow(day, campaign, name, cents, "USD", clicks, conv)], AT)


def campaign_id(store: Store, account_id: str) -> str:
    with store.read() as c:
        return SA.campaigns(c, account_id)[0].id


def line_of(c, entry_id: str) -> str:  # type: ignore[no-untyped-def]
    return str(
        c.execute(
            "SELECT id FROM fin_lines WHERE entry_id=? AND account_id=?", (entry_id, account(c, "6300"))
        ).fetchone()[0]
    )


def campaign_rows(store: Store, month: str = "2026-10") -> list[dict]:  # type: ignore[type-arg]
    with store.read() as c:
        return CP.read(c, OWNER, month)["campaigns"]


# ------------------------------------------------------------------------------------------------ the import
def test_the_import_stores_each_campaign_once_with_its_clicks_and_conversions(books: Store):
    iid, net, insights = meta(books)
    insights["111"] = [
        {
            "campaign_id": "c1",
            "campaign_name": "Spring",
            "spend": "12.345",
            "clicks": "30",
            "actions": [
                {"action_type": "offsite_conversion.fb_pixel_purchase", "value": "2"},
                {"action_type": "onsite_conversion.messaging_conversation_started_7d", "value": "1"},
                {"action_type": "link_click", "value": "30"},
            ],
            "date_start": "2026-10-02",
        },
        {"campaign_id": "c1", "campaign_name": "Spring", "spend": "5", "clicks": "7", "date_start": "2026-10-03"},
    ]
    assert AI.run(books, Vault(books), iid, now=AT, http_=http(net)).status == "ok"
    # A later run reads the same days again: the campaign is not stored twice and the figures do not add up.
    assert AI.run(books, Vault(books), iid, now="2026-10-20T12:00:00.000000Z", http_=http(net)).status == "ok"
    with books.read() as c:
        assert [(x.external_id, x.name) for x in SA.campaigns(c)] == [("c1", "Spring")]
    [row] = campaign_rows(books)
    assert row["name"] == "Spring" and row["ad_account"]["name"] == "Paid rental"
    assert row["month"] == {"spend": 1735, "clicks": 37, "conversions": 3.0, "per_conversion": 578}


def test_meta_scalar_conversions_win_over_the_action_list(books: Store):
    iid, net, insights = meta(books)
    insights["111"] = [
        {
            "campaign_id": "c1",
            "campaign_name": "Spring",
            "spend": "10",
            "clicks": "5",
            "conversions": "4",
            "actions": [{"action_type": "offsite_conversion.fb_pixel_purchase", "value": "2"}],
            "date_start": "2026-10-02",
        }
    ]
    assert AI.run(books, Vault(books), iid, now=AT, http_=http(net)).status == "ok"
    [row] = campaign_rows(books)
    assert row["month"]["conversions"] == 4.0 and row["month"]["per_conversion"] == 250


def test_a_restated_day_overwrites_the_clicks_and_conversions_it_had(books: Store):
    g = google(books)
    feed(books, g["a"], "c1", "Spring", "2026-10-05", 6000, clicks=10, conv=1.0)
    feed(books, g["a"], "c1", "Spring", "2026-10-05", 9000, clicks=14, conv=2.0)  # restated: replaces, does not add
    [row] = campaign_rows(books)
    assert row["month"] == {"spend": 9000, "clicks": 14, "conversions": 2.0, "per_conversion": 4500}


def test_no_conversions_means_no_cost_per_conversion():
    assert CP.per_conversion(5000, 0.0) is None
    assert CP.per_conversion(1000, 3.0) == 333
    assert CP.per_conversion(0, 2.0) == 0


# ------------------------------------------------------------------------------------------------ owners and the split
def test_an_owner_change_resplits_the_platform_charge_and_undo_puts_it_back(books: Store):
    g = google(books)
    feed(books, g["a"], "c1", "Spring", "2026-10-05", 60_000)  # account a's bot: A
    feed(books, g["b"], "c2", "Fall", "2026-10-06", 40_000)  # account b's bot: B
    with books.transaction() as c:
        posted = spend(c, "6300", 100_000, day="2026-10-12", vendor=g["vendor"])
        line = line_of(c, str(posted["entry_id"]))
        assert ATT.in_force(c, line) is not None
        rows_before = c.execute("SELECT count(*) FROM fin_attributions WHERE line_id=?", (line,)).fetchone()[0]
        assert by_mate(c, "6300") == {A: 60_000, B: 40_000}  # 600 and 400 of 1000 is 6000 and 4000 basis points
    c2 = campaign_id(books, g["b"])
    with books.transaction() as c:
        assert act(c, OWNER, "campaign_owner_set", {"campaign_id": c2, "teammate": A}).status == "committed"
        in_force = ATT.in_force(c, line)
        assert in_force is not None and str(in_force["source"]) == "platform"
        assert [(s.teammate, s.share_bps) for s in ATT.parse_splits(str(in_force["splits"]))] == [(A, 10000)]
        # The re-split is one more row in the charge's history, not a copy of the one before.
        assert (
            c.execute("SELECT count(*) FROM fin_attributions WHERE line_id=?", (line,)).fetchone()[0] == rows_before + 1
        )
        assert by_mate(c, "6300") == {A: 100_000}
    with books.transaction() as c:
        assert act(c, OWNER, "campaign_owner_undo", {"campaign_id": c2}).status == "committed"
        in_force = ATT.in_force(c, line)
        assert in_force is not None
        assert [(s.teammate, s.share_bps) for s in ATT.parse_splits(str(in_force["splits"]))] == [(A, 6000), (B, 4000)]


def test_a_team_owner_reads_back_as_a_team_and_blank_returns_the_account_owner(books: Store):
    g = google(books)
    feed(books, g["a"], "c1", "Spring", "2026-10-05", 5000, clicks=10, conv=1.0)
    c1 = campaign_id(books, g["a"])
    with books.transaction() as c:
        assert act(c, OWNER, "campaign_owner_set", {"campaign_id": c1, "team": "sales"}).status == "committed"
    [row] = campaign_rows(books)
    assert row["owner"] == {"team": "sales", "inherited": False}
    with books.transaction() as c:
        assert act(c, OWNER, "campaign_owner_set", {"campaign_id": c1}).status == "committed"
    [row] = campaign_rows(books)
    assert row["owner"]["inherited"] is True


def test_a_reader_without_attribution_cannot_change_an_owner(books: Store):
    g = google(books)
    feed(books, g["a"], "c1", "Spring", "2026-10-05", 5000)
    c1 = campaign_id(books, g["a"])
    with books.transaction() as c:
        refused = act(c, "human:nobody", "campaign_owner_set", {"campaign_id": c1, "team": "sales"})
    assert refused.status != "committed"
    with books.read() as c:
        assert SA.campaign(c, c1) is not None and SA.campaign(c, c1).team_id == ""  # type: ignore[union-attr]


# ------------------------------------------------------------------------------------------------ the reads
def test_the_metrics_report_and_search_read_the_same_campaigns(books: Store):
    g = google(books)
    feed(books, g["a"], "c1", "Spring sale", "2026-10-05", 6000, clicks=10, conv=3.0)
    with books.read() as c:
        spend_metric = RD.metric(c, OWNER, "ad_spend", start="2026-10-01", end=END)
        per_conv = RD.metric(c, OWNER, "per_conversion", start="2026-10-01", end=END)
        report = RD.report(c, OWNER, "campaigns", start="2026-10-01", end=END)
        found = RD.search(c, OWNER, kind="campaign", q="spring", filters={"month": "2026-10"})
        missed = RD.search(c, OWNER, kind="campaign", q="winter", filters={"month": "2026-10"})
    assert spend_metric["amount"] == 6000 and spend_metric["unit"] == "minor"
    assert per_conv["amount"] == 2000 and per_conv["conversions"] == 3.0
    assert report["totals"]["spend"] == 6000 and report["count"] == 1 and report["rows"][0]["name"] == "Spring sale"
    assert [r["name"] for r in found["results"]] == ["Spring sale"]
    assert missed["results"] == []


def test_the_summary_for_a_briefing_names_the_cheapest_priced_campaign(books: Store):
    g = google(books)
    feed(books, g["a"], "c1", "Costly", "2026-10-05", 9000, clicks=5, conv=3.0)  # 3000 each
    with books.read() as c:
        summary = CP.campaign_summary(c, OWNER, "2026-10")
    assert summary == {"live": 1, "best": {"name": "Costly", "per_conversion": 3000}}


def test_the_campaign_month_is_the_one_asked_for(books: Store):
    g = google(books)
    feed(books, g["a"], "c1", "Spring", "2026-10-05", 6000)
    with books.read() as c:
        assert CP.read(c, OWNER, "2026-11")["campaigns"] == []
    assert campaign_rows(books)[0]["id"] == campaign_id(books, g["a"])
