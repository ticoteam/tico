"""Finance's ad campaigns (backend/finance/campaigns.py, store/ads.py, ad_import.py): the import stores each campaign once
with its clicks and conversions (Meta's scalar `conversions` wins over `actions`; both offsite and onsite conversion types
count), a restated day overwrites, a campaign's owner (a teammate or a team) decides the platform share of the charges
its spend is in and re-judges them, undo puts the owner back, the cost per conversion is null with no conversions, and
the reads (campaigns report, ad_spend and per_conversion metrics, the campaign search kind) cut the same rows.

Slugs echo TIDY's (paid-rental-ads, consumer-google-ad-owner) for realism only; nothing depends on them. Visibility for a
teammate who sees only their own campaigns is not covered here; the full-detail reader is."""

from backend.credentials import Vault
from backend.finance import ad_import as AI
from backend.finance import budgeting
from backend.finance import campaigns as CP
from backend.finance.context import load
from backend.finance import reads as RD
from backend.finance.store import ads as SA
from backend.finance.store import attribution as ATT
from backend.store import Store
from backend.tests.helpers.finance_ad_spend import http, meta
from backend.tests.helpers.finance_fixtures import AT, OWNER, account, act, books, finance_template, spend, store  # noqa: F401
from backend.tests.helpers.finance_platform_share import A, B, by_mate, google

ORIGIN = "2026-10-01"
END = "2026-10-31"


def feed(
    store: Store, account_id: str, campaign: str, name: str, day: str, cents: int, clicks: int = 0, conv: float = 0.0
) -> None:
    """The platform's word for one campaign on one ad account for one day (a whole replace of the month)."""
    with store.transaction() as c:
        SA.upsert_campaign(c, account_id, campaign, name, ORIGIN, END, AT)
        SA.replace_spend(c, account_id, ORIGIN, END, [SA.SpendRow(day, campaign, name, cents, "USD", clicks, conv)], AT)


def feed_both(store: Store, account_id: str) -> None:
    """One import for account a: campaign cx (Fall sale) spent 600 on Oct 6 and cy (Spring sale) 400 on Oct 5."""
    with store.transaction() as c:
        SA.upsert_campaign(c, account_id, "cx", "Fall sale", ORIGIN, END, AT)
        SA.upsert_campaign(c, account_id, "cy", "Spring sale", ORIGIN, END, AT)
        rows = [
            SA.SpendRow("2026-10-06", "cx", "Fall sale", 60_000, "USD"),
            SA.SpendRow("2026-10-05", "cy", "Spring sale", 40_000, "USD"),
        ]
        SA.replace_spend(c, account_id, ORIGIN, END, rows, AT)


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


def test_the_campaign_month_is_the_one_asked_for(books: Store):
    g = google(books)
    feed(books, g["a"], "c1", "Spring", "2026-10-05", 6000)
    with books.read() as c:
        assert CP.read(c, OWNER, "2026-11")["campaigns"] == []
    assert campaign_rows(books)[0]["id"] == campaign_id(books, g["a"])


# ------------------------------------------------------------------------------------------------ visibility
C = "bot:rental-social-ad-owner"  # a sibling bot under the same boss, owning nothing in these tests
MANAGER = "bot:cmo"  # the boss of A, B and C


def campaign_by_external(store: Store, account_id: str, external: str) -> str:
    with store.read() as c:
        return next(x.id for x in SA.campaigns(c, account_id) if x.external_id == external)


def visible_ids(store: Store, reader: str, month: str = "2026-10") -> dict[str, set[str]]:
    """The campaigns one reader sees, read by each surface that names them: the GET route and its reader, the campaigns
    report, the campaign search kind. The three must agree."""
    first, last = CP.month_window(month)
    with store.read() as c:
        route = {x["id"] for x in CP.read(c, reader, month)["campaigns"]}
        report = {x["id"] for x in RD.report(c, reader, "campaigns", start=first, end=last)["rows"]}
        search = {x["id"] for x in RD.search(c, reader, kind="campaign", q="sale", filters={"month": month})["results"]}
    assert route == report == search, (reader, route, report, search)
    return {"ids": route}


def test_the_campaign_visibility_matrix_across_owners_managers_teams_and_full_detail(books: Store):
    g = google(books)
    feed(books, g["a"], "cx", "Fall sale", "2026-10-06", 0)  # zero spend this month: still listed, follows its owner
    feed(books, g["a"], "cy", "Spring sale", "2026-10-05", 40_000, clicks=9, conv=1.0)  # inherited: account a's bot A
    x = campaign_by_external(books, g["a"], "cx")
    y = campaign_by_external(books, g["a"], "cy")
    with books.transaction() as c:
        assert act(c, OWNER, "campaign_owner_set", {"campaign_id": x, "teammate": B}).status == "committed"
    # A teammate owner sees its own campaign; a manager sees everyone under it; another bot sees neither.
    assert visible_ids(books, B)["ids"] == {x}
    assert visible_ids(books, MANAGER)["ids"] == {x, y}
    assert visible_ids(books, C)["ids"] == set()
    # Full detail sees both.
    assert visible_ids(books, OWNER)["ids"] == {x, y}
    # A campaign owned by a team: a member of the team sees it, a bot outside the team does not.
    with books.transaction() as c:
        assert act(c, OWNER, "campaign_owner_set", {"campaign_id": x, "team": "sales"}).status == "committed"
    assert x in visible_ids(books, "bot:ten-0")["ids"]
    assert x not in visible_ids(books, "bot:finance-owner")["ids"]


def test_a_list_read_checks_the_platform_lines_once_for_every_campaign(books: Store, monkeypatch):  # type: ignore[no-untyped-def]
    g = google(books)
    feed(books, g["a"], "cx", "Fall sale", "2026-10-06", 5000)
    feed(books, g["b"], "cz", "Winter sale", "2026-10-07", 7000)
    with books.transaction() as c:
        spend(c, "6300", 12_000, day="2026-10-12", vendor=g["vendor"])
    from backend.finance import visible as VIS

    calls = {"n": 0}
    real = VIS._platform_attributed

    def counted(*args, **kwargs):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(VIS, "_platform_attributed", counted)
    with books.read() as c:
        CP.read(c, C, "2026-10")
    assert calls["n"] <= 1


# ------------------------------------------------------------------------------------------------ budgets
def _bot_budget(store: Store, g: dict[str, str], teammate: str, parent: str) -> str:
    with store.transaction() as c:
        made = act(
            c,
            OWNER,
            "budget_create",
            {
                "name": f"Google {teammate}",
                "teammate": teammate,
                "vendor": g["vendor"],
                "amount": 300_000,
                "account_id": g["adv"],
                "parent_id": parent,
                "at_limit": "warn",
            },
            at=AT,
        )
        assert made.status == "committed", made
        return str(made.result["budget_id"])


def _company_budget(store: Store, g: dict[str, str]) -> str:
    with store.transaction() as c:
        made = act(
            c,
            OWNER,
            "budget_create",
            {"name": "Marketing", "amount": 1_000_000, "account_id": g["adv"], "at_limit": "warn"},
            at=AT,
        )
        assert made.status == "committed", made
        return str(made.result["budget_id"])


def test_a_campaign_owner_is_charged_its_own_spend_and_nothing_is_counted_twice(books: Store):
    g = google(books)
    feed_both(books, g["a"])  # cx owned by B below; cy inherited: A
    x = campaign_by_external(books, g["a"], "cx")
    with books.transaction() as c:
        assert act(c, OWNER, "campaign_owner_set", {"campaign_id": x, "teammate": B}).status == "committed"
        spend(c, "6300", 100_000, day="2026-10-12", vendor=g["vendor"])  # the books' charge for the month: 1000
    company = _company_budget(books, g)
    budget_a = _bot_budget(books, g, A, company)
    budget_b = _bot_budget(books, g, B, company)
    with books.read() as c:
        ctx = load(c, AT)
        budgets = ctx.budgets()
        live = {
            "b": budgeting.actual(ctx, budgets[budget_b], "2026-10-01", "2026-10-31", live=True),
            "a": budgeting.actual(ctx, budgets[budget_a], "2026-10-01", "2026-10-31", live=True),
            "company": budgeting.actual(ctx, budgets[company], "2026-10-01", "2026-10-31", live=True),
        }
    assert live == {"b": 60_000, "a": 40_000, "company": 100_000}


def test_with_no_campaign_owners_the_account_bot_carries_the_whole_month(books: Store):
    g = google(books)
    feed_both(books, g["a"])
    with books.transaction() as c:
        spend(c, "6300", 100_000, day="2026-10-12", vendor=g["vendor"])
    company = _company_budget(books, g)
    budget_a = _bot_budget(books, g, A, company)
    with books.read() as c:
        ctx = load(c, AT)
        assert budgeting.actual(ctx, ctx.budgets()[budget_a], "2026-10-01", "2026-10-31", live=True) == 100_000
