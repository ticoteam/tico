"""The Finance tab's reads cut to the viewer: the balance sheet and sources are the owner's unless granted, transactions
and cards follow the lines a manager, a plain bot or a granted bot sees, the transactions cursor pages without gaps or
repeats, the P&L serves day columns and the expenses view by team or teammate, and only the owner views as others."""
import json

import pytest

from backend.finance.actions import FinanceError
from backend.finance import actions as FA
from backend.finance import reads as FR
from backend.finance import tab as TB
from backend.finance.core.org import Org, Team
from backend.finance.store import banking as SK
from backend.tests.test_api import api, get  # noqa: F401
from backend.tests.test_finance_fixtures import (  # noqa: F401
    AT, GROUPS, OWNER, PEOPLE, _seed, account, act, books, finance_template, spend, store)


def _bank(c, at=AT):
    """Acme's bank account and card program, two cards and a few feed rows (eve's card spend posted, gus's posted, a
    bank fee new)."""
    book = str(c.execute("SELECT id FROM fin_books").fetchone()[0])
    cards_gl = str(act(c, OWNER, "account_create", {"code": "2100", "name": "Credit cards", "type": "liability"})
                   .result["account_id"])
    base = {"book_id": book, "provider": "mercury", "created_at": at, "created_by": "system:finance"}
    bank = SK.insert(c, "fin_money_accounts", {**base, "kind": "bank", "subtype": "checking", "name": "Acme checking",
                                               "account_id": account(c, "1000"), "last4": "0001", "balance": 90_000,
                                               "balance_currency": "USD", "balance_as_of": at})
    program = SK.insert(c, "fin_money_accounts", {**base, "kind": "card_program", "name": "Acme IO", "holds_cash": 0,
                                                  "account_id": cards_gl, "last4": "0002"})
    card = {"provider": "mercury", "money_account_id": program, "kind": "person", "currency": "USD", "created_at": at,
            "created_by": "system:finance"}
    eve = SK.insert(c, "fin_cards", {**card, "holder": "human:eve", "last4": "1111", "limit_amount": 50_000})
    gus = SK.insert(c, "fin_cards", {**card, "holder": "human:gus", "last4": "2222"})
    feed = {"currency": "USD", "created_at": at, "created_by": "system:finance", "status": "posted"}
    for pid, holder, cid, amount, day in (("t1", "human:eve", eve, 1_200, "2026-10-08"),
                                          ("t2", "human:gus", gus, 3_400, "2026-10-09")):
        team = "web" if holder == "human:eve" else "sales"
        entry = FA.post_entry(c, {"date": day, "source": "card", "source_id": pid, "lines": [
            {"account_id": account(c, "6900"), "amount": amount, "tags": {"teammate": holder, "team": team}},
            {"account_id": cards_gl, "amount": -amount}]}, at=at)
        SK.insert(c, "fin_feed_txns", {**feed, "money_account_id": program, "provider_id": pid, "txn_date": day,
                                       "amount": -amount, "counterparty": "Acme Supplies", "card_id": cid,
                                       "entry_id": entry["entry_id"], "raw_json": '{"secret": "raw"}'})
    SK.insert(c, "fin_feed_txns", {**feed, "status": "new", "money_account_id": bank, "provider_id": "t3",
                                   "txn_date": "2026-10-11", "amount": -500, "counterparty": "Bank fee"})
    return {"bank": bank, "program": program, "eve": eve, "gus": gus, "cards_gl": cards_gl}


def _all(c, viewer, **kw):
    """Every transaction row through the cursor, a page of `limit` at a time."""
    rows, cursor = [], None
    for _ in range(50):
        page = FR.transactions(c, viewer, cursor=cursor, at=AT, **kw)
        rows += page["rows"]
        cursor = page["next"]
        if not cursor:
            return rows
    raise AssertionError("the cursor never ended")


def test_balance_sheet_and_sources_are_the_owners_unless_granted(books):
    with books.transaction() as c:
        ids = _bank(c)
        spend(c, "6100", 2_000)
        sheet = FR.balance_sheet(c, OWNER, at=AT)
        bank = next(r for r in sheet["assets"] if r["code"] == "1000")
        assert bank["balance"] == -2_000 and bank["sources"][0]["provider_balance"] == 90_000
        cards = next(r for r in sheet["liabilities"] if r["code"] == "2100")
        assert cards["balance"] == 4_600 and cards["entry_sources"] == ["card"]
        t = sheet["totals"]
        assert t["asset"] == t["liability"] + t["equity"] and not sheet["scoped"]
        for who in ("human:ben", "human:cara", "bot:coder"):
            with pytest.raises(FinanceError):
                FR.balance_sheet(c, who, at=AT)
            with pytest.raises(FinanceError):
                FR.sources(c, who, at=AT)
        act(c, OWNER, "view_grant", {"subject": "human:cara", "what": "bs_lines", "account_ids": [ids["cards_gl"]]})
        part = FR.balance_sheet(c, "human:cara", at=AT)
        assert [r["code"] for r in part["liabilities"]] == ["2100"] and part["assets"] == [] and part["equity"] == []
        assert part["scoped"]
        groups = {g["group"]: g for g in FR.sources(c, OWNER, at=AT)["groups"]}
        assert groups["banks"]["count"] == 1 and groups["cards"]["count"] == 1
        assert groups["cards"]["rows"][0]["cards"] == {"active": 2}
        assert groups["banks"]["rows"][0]["feed"] == {"new": 1}
        assert any(a["who"] == "human:cara" for a in groups["cards"]["rows"][0]["access"])
        act(c, OWNER, "view_grant", {"subject": "bot:coder", "what": "sources"})
        only = FR.sources(c, "bot:coder", group="banks", at=AT)["groups"]
        assert [g["count"] for g in only][:2] == [1, 1] and only[1]["rows"] == []


def test_transactions_and_cards_follow_the_lines_each_viewer_sees(books):
    with books.transaction() as c:
        ids = _bank(c)
        spend(c, "6100", 700, team="web", teammate="bot:coder", day="2026-10-05")
        spend(c, "6100", 800, team="sales", day="2026-10-06")
        owner = _all(c, OWNER, limit=50)
        assert [(r["kind"], r["amount"]) for r in owner] == [
            ("feed", -500), ("feed", -3_400), ("feed", -1_200), ("entry", 800), ("entry", 700)]
        assert all("raw" not in str(r) for r in owner)
        assert owner[2]["lines"][0]["tags"] == {"teammate": "human:eve", "team": "web"}
        cara = _all(c, "human:cara")                  # leads Web: eve's card, the coder's line; no bank, no sales
        assert sorted((r["kind"], r["amount"]) for r in cara) == [("entry", 700), ("feed", -1_200)]
        assert _all(c, "human:ben") == cara           # Eng covers Web
        dan = _all(c, "human:dan")
        assert [(r["kind"], r["amount"]) for r in dan] == [("feed", -3_400), ("entry", 800)]
        coder = _all(c, "bot:coder")                  # a plain bot: its own line only
        assert [(r["kind"], r["amount"]) for r in coder] == [("entry", 700)]
        act(c, OWNER, "view_grant", {"subject": "bot:coder", "what": "expenses", "whose": "team", "whose_team": "sales"})
        granted = _all(c, "bot:coder")
        assert sorted((r["kind"], r["amount"]) for r in granted) == [("entry", 700), ("entry", 800),
                                                                       ("feed", -3_400)]
        assert sorted(x["last4"] for x in FR.cards(c, OWNER, at=AT)["cards"]) == ["1111", "2222"]
        assert [x["id"] for x in FR.cards(c, "human:cara", at=AT)["cards"]] == [ids["eve"]]
        assert [x["id"] for x in FR.cards(c, "human:eve", at=AT)["cards"]] == [ids["eve"]]
        assert FR.cards(c, "human:fay", at=AT)["cards"] == []
        card = FR.cards(c, "human:eve", at=AT)["cards"][0]
        assert card["limit"] == 50_000 and not {"number", "pan", "cvc"} & set(card)
        assert _all(c, OWNER, money_account_id=ids["bank"]) == owner[:1]
        assert [r["amount"] for r in _all(c, OWNER, q="supplies")] == [-3_400, -1_200]
        assert [r["amount"] for r in _all(c, OWNER, status="new")] == [-500]


def test_an_entry_with_only_balance_sheet_lines_shows_in_transactions(books):
    with books.transaction() as c:
        _bank(c)
        loan = str(act(c, OWNER, "account_create", {"code": "2500", "name": "Loan", "type": "liability"})
                   .result["account_id"])
        act(c, OWNER, "journal", {"date": "2026-10-12", "memo": "Loan drawn", "lines": [
            {"account_id": account(c, "1000"), "amount": 90_000}, {"account_id": loan, "amount": -90_000}]})
        rows = [r for r in _all(c, OWNER) if r.get("memo") == "Loan drawn"]
        assert {r["amount"] for r in rows} == {90_000, -90_000}
        # A viewer with no balance sheet doesn't see it, and asked for by line it shows too.
        assert not [r for r in _all(c, "human:eve") if r.get("memo") == "Loan drawn"]
        assert [r["amount"] for r in _all(c, OWNER, account_id=loan)] == [-90_000]
        # Entries with an income or expense line still show by that line alone.
        spent = [r for r in _all(c, OWNER) if r["kind"] == "entry" and r["account"] == "Credit cards"]
        assert not spent


def test_a_budget_a_card_draws_on_says_so_until_the_card_is_cancelled(books):
    with books.transaction() as c:
        ids = _bank(c)
        bid = str(act(c, OWNER, "budget_create", {"name": "Tools", "period": "month", "account_id": account(c, "6900"),
                                                  "amount": 10_000}).result["budget_id"])
        flag = lambda: next(b["card_limits"] for b in FR.budgets(c, OWNER, at=AT)["budgets"] if b["id"] == bid)
        assert flag() is False
        c.execute("UPDATE fin_cards SET budget_id=? WHERE id=?", (bid, ids["eve"]))
        assert flag() is True
        c.execute("UPDATE fin_cards SET status='cancelled' WHERE id=?", (ids["eve"],))
        assert flag() is False


def test_the_transactions_cursor_pages_without_gaps_or_repeats(books):
    with books.transaction() as c:
        _bank(c)
        for i in range(23):
            spend(c, "6100" if i % 2 else "5100", 100 + i, team="web" if i % 3 else "sales",
                  day=f"2026-10-{1 + i % 9:02d}")
        for viewer in (OWNER, "human:cara", "human:dan"):
            whole = FR.transactions(c, viewer, limit=200, at=AT)
            assert whole["next"] is None
            for size in (1, 3, 7):
                paged = _all(c, viewer, limit=size)
                assert [r["id"] for r in paged] == [r["id"] for r in whole["rows"]]
        with pytest.raises(FinanceError):
            FR.transactions(c, OWNER, cursor="not-a-cursor", at=AT)


def test_pnl_days_weeks_and_the_expenses_view_by_team_and_teammate(books):
    with books.transaction() as c:
        spend(c, "6100", 1_000, team="web", teammate="human:eve", day="2026-10-02")
        spend(c, "6100", 2_000, team="eng", day="2026-10-03")
        spend(c, "6900", 4_000, team="sales", teammate="human:gus", day="2026-10-03")
        days = FR.pnl(c, OWNER, "2026-10-01", "2026-10-07", "day", at=AT)
        assert len(days["columns"]) == 7 and days["expense"][1:3] == [1_000, 6_000]
        assert FR.pnl(c, OWNER, "2026-10-01", "2026-10-31", "week", at=AT)["columns"][0]["state"] == "past"
        eng = FR.pnl(c, OWNER, "2026-10-01", "2026-10-31", view="expenses", team="eng", at=AT)
        assert eng["expense"] == [3_000] and all(r["type"] != "income" for r in eng["rows"])
        assert FR.pnl(c, OWNER, "2026-10-01", "2026-10-31", view="expenses", team="web", at=AT)["expense"] == [1_000]
        eve = FR.pnl(c, OWNER, "2026-10-01", "2026-10-31", view="expenses", teammate="human:eve", at=AT)
        assert eve["expense"] == [1_000] and [r["code"] for r in eve["rows"] if r["total"]] == ["6100"]
        assert {m["teammate"]: m["spent"] for m in eng["teammates"]}.get("human:eve") == 1_000
        assert FR.pnl(c, "human:cara", "2026-10-01", "2026-10-31", view="expenses", team="sales", at=AT)["expense"] \
            == [0]
        with pytest.raises(FinanceError):
            FR.pnl(c, OWNER, "2026-10-01", "2026-10-31", team="web", at=AT)   # filters are for the expenses view
        accrual = FR.pnl(c, OWNER, "2026-10-01", "2026-10-31", basis="accrual", at=AT)
        assert accrual["expense"] == FR.pnl(c, OWNER, "2026-10-01", "2026-10-31", at=AT)["expense"]


def test_access_shows_each_viewer_their_part_and_only_the_owner_views_as(books):
    with books.transaction() as c:
        act(c, "human:cara", "view_grant", {"subject": "human:eve", "what": "expenses", "whose": "team",
                                            "whose_team": "web"})
        owner = FR.access(c, OWNER, at=AT)
        assert owner["can_view_as"] and {"human:ana", "human:gus", "bot:coder"} <= {m["teammate"]
                                                                                     for m in owner["teammates"]}
        mates = {m["teammate"]: m for m in owner["teammates"]}
        assert mates["human:ana"]["default"] == "everything" and mates["human:cara"]["default"] == "team_expenses"
        assert len(mates["human:eve"]["grants"]) == 1
        # A manager (Ben leads Engineering, so Web under it too) sees his own row and his teams', never Sales.
        ben = FR.access(c, "human:ben", at=AT)
        assert {m["teammate"] for m in ben["teammates"]} == {"human:ben", "human:cara", "human:eve", "human:fay", "bot:coder"}
        assert not ben["can_view_as"]
        mine = next(m for m in ben["teammates"] if m["teammate"] == "human:ben")
        assert mine["default"] == "team_expenses" and mine["rights"] == []
        assert next(m for m in ben["teammates"] if m["teammate"] == "human:cara")["rights"] is None
        eve = FR.access(c, "human:eve", at=AT)
        assert [m["teammate"] for m in eve["teammates"]] == ["human:eve"] and not eve["can_view_as"]
        assert [g["subject"] for g in eve["teammates"][0]["grants"]] == ["human:eve"]
        assert act(c, OWNER, "right_grant", {"teammate": "human:fay", "right": "finance.admin"}).status == "committed"
        fay = {m["teammate"] for m in FR.access(c, "human:fay", at=AT)["teammates"]}
        owner = {m["teammate"] for m in FR.access(c, OWNER, at=AT)["teammates"]}
        assert fay == owner and {"human:ana", "human:gus", "bot:coder"} <= fay   # a finance admin sees the whole roster
        assert FR.view_as(c, OWNER, "human:cara", at=AT) == "human:cara"
        with pytest.raises(FinanceError):
            FR.view_as(c, "human:ben", "human:cara", at=AT)
        with pytest.raises(FinanceError):
            FR.view_as(c, OWNER, "human:nobody", at=AT)


def test_a_managers_default_names_only_a_team_they_lead(books):
    """Access names a manager's default by its team ("Engineering expenses") only when that team is one they lead: their
    team slice then covers all of it. A team their reports sit in is not named, even when they are everyone on it.
    Several teams, a report on no team, or a sub-team lead with a report in the parent team stay generic."""
    with books.transaction() as c:
        rows = {m["teammate"]: m for m in FR.access(c, OWNER, at=AT)["teammates"]}
    assert rows["human:ben"]["default_team"] == "eng"          # leads Engineering, Web under it, Cara reports to him
    assert rows["human:cara"]["default_team"] == "web" and rows["human:dan"]["default_team"] == "sales"
    assert rows["human:eve"]["default_team"] is None and rows["human:ana"]["default_team"] is None
    teams = {"eng": Team("eng", "Engineering"), "web": Team("web", "Web", parent="eng", lead="human:sub"),
             "ops": Team("ops", "Ops"), "qa": Team("qa", "QA"), "sales": Team("sales", "Sales", lead="human:lea")}
    team_of = {"human:a": "ops", "human:b": "ops", "human:c": "qa", "human:q": "qa", "human:d": "", "human:e": "eng",
               "human:f": "eng", "human:w": "web", "human:sub": "web"}
    reports_to = {"human:a": "human:solo", "human:b": "human:solo", "human:c": "human:part", "human:w": "human:sub",
                  "human:e": "human:sub", "human:q": "human:wide", "human:f": "human:wide", "human:d": "human:loose",
                  "human:x": "human:lea"}
    org = Org(owner=OWNER, teams=teams, team_of=team_of, reports_to=reports_to,
              humans=frozenset([*team_of, "human:solo", "human:part", "human:wide", "human:loose", "human:lea"]))
    assert TB.default_team(org, "human:solo") is None         # reports only, and everyone in Ops, but leads nothing
    assert TB.default_team(org, "human:part") is None         # reports only in QA, and leads nothing
    assert TB.default_team(org, "human:sub") is None          # leads Web; one report in Engineering, above it
    assert TB.default_team(org, "human:wide") is None         # reports in Engineering and QA
    assert TB.default_team(org, "human:loose") is None        # a reporting line on no team
    assert TB.default_team(org, "human:lea") is None          # leads Sales, but a report sits on no team


def test_a_manager_with_reports_only_in_a_team_names_no_team_for_anyone(books):
    """A manager names a team only when they lead it. Eve sits in Web under Engineering, leads nothing, and manages all of
    Sales (Dan, and through him Gus and the seller bot, report up to her); she reports to no one. Her default names no
    team, for Ben or the owner, and Sales stays out of Ben's teams. A team-only Sales expense is not in her view: her
    team slices are for teams she leads, and her report slice follows teammates."""
    reports = {"dan": "eve", "eve": ""}             # both lines in one roster write, as test_finance_view does
    people = [{**p, "reports_to": reports.get(p["id"], p.get("reports_to", ""))} for p in PEOPLE]
    with books.transaction() as c:
        c.execute("UPDATE registry_metadata SET value_json=? WHERE key='people'",
                  (json.dumps({"people": people, "org_groups": GROUPS, "default_user": "ana"}),))
        ben = FR.access(c, "human:ben", at=AT)
        owner = FR.access(c, OWNER, at=AT)
        before = FR.expenses(c, "human:eve", by="team", at=AT)["total"]
        spend(c, "6100", 900, team="sales")
        assert FR.expenses(c, "human:eve", by="team", at=AT)["total"] == before
    assert "sales" not in {t["id"] for t in ben["teams"]}
    mates = {m["teammate"]: m for m in ben["teammates"]}
    assert mates["human:eve"]["default"] == "team_expenses" and mates["human:eve"]["default_team"] is None
    assert mates["human:ben"]["default_team"] == "eng"          # Engineering is his, so the id shows
    assert {m["teammate"]: m for m in owner["teammates"]}["human:eve"]["default_team"] is None


def test_a_lead_sees_the_team_only_expenses_of_the_team_their_default_names(books):
    """A team's expenses tagged with the team alone (no teammate) show to its lead, so the team a default names is one
    the manager sees whole."""
    with books.transaction() as c:
        rows = {m["teammate"]: m for m in FR.access(c, OWNER, at=AT)["teammates"]}
        for lead in ("human:ben", "human:cara", "human:dan"):
            team = rows[lead]["default_team"]
            assert team
            before = FR.expenses(c, lead, by="team", at=AT)["total"]
            spend(c, "6100", 700, team=team)
            assert FR.expenses(c, lead, by="team", at=AT)["total"] == before + 700


def test_tab_routes_answer_in_the_callers_view(api):
    store = api.app.state.store
    with store.transaction() as c:
        assert FA.ensure_setup(c, store.settings.owner_email)
    assert {g["group"] for g in get(api, "finance/sources")["groups"]} >= {"banks", "cards", "tico"}
    assert "totals" in get(api, "finance/balance-sheet") and "rows" in get(api, "finance/transactions")
    assert "cards" in get(api, "finance/cards") and get(api, "finance/access")["can_view_as"]
    assert len(get(api, "finance/pnl?period=week&start=2026-10-01&end=2026-10-31")["columns"]) >= 4
    get(api, "finance/balance-sheet", token="ben-test", expected=403)
    get(api, "finance/sources", token="ben-test", expected=403)
    assert get(api, "finance/access", token="ben-test")["can_view_as"] is False
    get(api, "finance/pnl?view_as=human:nobody", expected=404)
    get(api, "finance/pnl?view_as=human:ana", token="ben-test", expected=403)


def test_access_over_http_is_the_roster_for_admins_and_each_managers_own_teams_otherwise(api):
    """The access read over HTTP: owner and finance admins see everyone (with rights and private lines); a manager sees
    their own row and the teammates in the teams they manage, nested, but no rights and no private lines; anyone else
    sees only their own row."""
    store = api.app.state.store
    with store.transaction() as c:
        _seed(c)                                    # the Acme org chart, as the finance fixtures have it
        assert FA.ensure_setup(c, store.settings.owner_email)
        assert act(c, OWNER, "private_lines_set", {"account_id": account(c, "6900"), "private": True}).status == "committed"
    owner = get(api, "finance/access")
    everyone = {m["teammate"] for m in owner["teammates"]}
    acme = {"human:ana", "human:ben", "human:cara", "human:dan", "human:eve", "human:fay", "human:gus", "bot:coder",
            "bot:seller"}
    assert acme <= everyone and owner["private_lines"] and all(m["rights"] is not None for m in owner["teammates"])
    # Cara leads Web (under Engineering): her own row and Web's people, the bot included, never Sales or Ana.
    cara = get(api, "finance/access", token="cara-test")
    assert {m["teammate"] for m in cara["teammates"]} == {"human:cara", "human:eve", "human:fay", "bot:coder"}
    assert cara["private_lines"] == [] and [t["id"] for t in cara["teams"]] == ["web"]
    rows = {m["teammate"]: m for m in cara["teammates"]}
    assert rows["human:cara"]["rights"] is not None and rows["human:eve"]["rights"] is None
    # Ben leads Engineering, so Web is under him too.
    ben = get(api, "finance/access", token="ben-test")
    assert {m["teammate"] for m in ben["teammates"]} == {"human:ben", "human:cara", "human:eve", "human:fay", "bot:coder"}
    # Eve is a plain teammate: her own row only.
    eve = get(api, "finance/access", token="eve-test")
    assert [m["teammate"] for m in eve["teammates"]] == ["human:eve"] and eve["private_lines"] == []
    # A finance admin sees the whole roster, rights included.
    with store.transaction() as c:
        assert act(c, OWNER, "right_grant", {"teammate": "human:fay", "right": "finance.admin"}).status == "committed"
    fay = get(api, "finance/access", token="fay-test")
    assert {m["teammate"] for m in fay["teammates"]} == everyone and fay["private_lines"]
    assert all(m["rights"] is not None for m in fay["teammates"])
