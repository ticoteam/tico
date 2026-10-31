"""The Finance tab's later asks, cut to the viewer: the owner edits the view defaults; the expenses view filters by
several teams and teammates, bots or people, and Unattributed (company-wide viewers only); a line opens per vendor with
how it was paid and next month's projection; budgets read at a date; Sources' sync / disconnect / map are a finance
admin's; Mercury's users are for teammates who may issue cards, emails masked and only for a finance admin; view_as
reaches lines, expenses and budgets."""
import pytest

from backend.finance import actions as FA
from backend.finance import reads as FR
from backend.finance.actions import FinanceError
from backend.finance.store import banking as SK
from backend.tests.test_api import api, get  # noqa: F401
from backend.tests.test_finance_fixtures import AT, OWNER, account, act, books, finance_template, spend, store  # noqa: F401
from backend.tests.test_finance_tab import _bank

OCT = ("2026-10-01", "2026-10-31")


def test_a_line_without_a_projection_projects_from_its_posted_history(books):
    with books.transaction() as c:
        spend(c, "6100", 800, day="2026-09-05")          # software recurs: September's total is next month's guess
        spend(c, "6100", 400, day="2026-09-20")
        spend(c, "5100", 3_100, day="2026-10-10")        # AI services runs at its last 30 days
        act(c, OWNER, "projection_set", {"account_id": account(c, "5100"), "month": "2026-11", "amount": 9_000})
        soft = FR.line_detail(c, OWNER, account(c, "6100"), *OCT, at=AT)["next_month"]
        assert soft == {"month": "2026-11", "projection": 1_200, "projection_method": "recurring"}
        ai = FR.line_detail(c, OWNER, account(c, "5100"), *OCT, at=AT)["next_month"]
        assert ai == {"month": "2026-11", "projection": 9_000, "projection_method": "manual"}
        none = FR.line_detail(c, OWNER, account(c, "6900"), *OCT, at=AT)["next_month"]
        assert none == {"month": "2026-11", "projection": None, "projection_method": None}
        rows = {r["code"]: r for r in FR.pnl(c, OWNER, *OCT, at=AT)["rows"]}
        assert rows["6100"]["projection"] == [1_200] and rows["6100"]["projection_method"] == "recurring"
        assert rows["5100"]["projection"] == [3_203]            # October's run rate: 3100 x 31 / 30, a stored November
        assert rows["5100"]["projection_method"] == "run_rate"  # does not change October
        assert rows["6900"]["projection"] is None and rows["6900"]["projection_method"] is None


def test_only_the_owner_edits_view_defaults_and_unbacked_grants_are_suspended(books):
    with books.transaction() as c:
        spend(c, "6100", 1_000, team="web", teammate="human:eve")
        grant = act(c, "human:cara", "view_grant", {"subject": "human:eve", "what": "expenses", "whose": "team",
                                                    "whose_team": "web"})
        assert grant.status == "committed"
        assert act(c, "human:cara", "view_defaults_set", {"member": "none"}).code == "denied"
        assert act(c, OWNER, "view_defaults_set", {}).code == "invalid"
        assert act(c, OWNER, "view_defaults_set", {"member": "everything"}).code == "invalid"
        done = act(c, OWNER, "view_defaults_set", {"member": "none", "manager": "own_expenses"})
        assert done.status == "committed"
        assert done.result["defaults"] == {"manager": "own_expenses", "member": "none", "manager_payroll": "totals"}
        assert done.result["suspended_grants"] == [grant.result["grant_id"]]
        assert FR.access(c, OWNER, at=AT)["defaults"]["member"] == "none"
        assert FR.pnl(c, "human:eve", *OCT, view="expenses", at=AT)["expense"] == [0]   # her own spend is gone


def test_the_expenses_filter_picks_several_and_cuts_by_who_and_unattributed(books):
    with books.transaction() as c:
        spend(c, "6100", 1_000, team="web", teammate="human:eve")
        spend(c, "6100", 2_000, team="eng")
        spend(c, "6900", 4_000, team="sales", teammate="human:gus")
        spend(c, "5100", 500, team="web", teammate="bot:coder")
        spend(c, "6900", 300)                                                     # unattributed

        def total(viewer=OWNER, **kw):
            return FR.pnl(c, viewer, *OCT, view="expenses", at=AT, **kw)["expense"][0]
        assert total(teams=["web", "sales"]) == 5_500
        assert total(teams=["web"], teammates=["human:gus"]) == 5_500
        assert total(who="bots") == 500 and total(who="people") == 5_000
        assert total(teams=["web"], who="people") == 1_000
        assert total(unattributed=True) == 300 and total(teams=["sales"], unattributed=True) == 4_300
        picked = FR.pnl(c, OWNER, *OCT, view="expenses", teams=["web"], teammates=["human:gus"], who="people", at=AT)
        assert picked["filter"] == {"teams": ["web"], "teammates": ["human:gus"], "who": "people",
                                    "unattributed": False}
        assert {m["teammate"] for m in picked["teammates"]} == {"human:cara", "human:eve", "human:fay", "human:gus"}
        sections = {r["code"]: r["section"] for r in FR.pnl(c, OWNER, *OCT, at=AT)["rows"]}
        assert (sections["4000"], sections["5100"], sections["6100"]) == ("revenue", "cost_of_revenue", "operating")
        assert total("human:cara", teams=["web", "sales"]) == 1_500                   # her slice only
        with pytest.raises(FinanceError, match="company-wide"):
            total("human:cara", unattributed=True)
        with pytest.raises(FinanceError):
            FR.pnl(c, OWNER, *OCT, who="bots", at=AT)                              # filters are for expenses


def test_a_line_opens_per_vendor_with_how_it_was_paid_and_next_month(books):
    with books.transaction() as c:
        _bank(c)                                     # eve's and gus's card spend on 6900, no vendor
        spend(c, "6900", 700, team="web", teammate="human:eve", vendor="acme-tools")
        act(c, OWNER, "projection_set", {"account_id": account(c, "6900"), "month": "2026-11", "amount": 9_000})
        line = account(c, "6900")
        whole = FR.line_detail(c, OWNER, line, *OCT, at=AT)
        assert whole["next_month"] == {"month": "2026-11", "projection": 9_000, "projection_method": "manual"}
        assert {(p["kind"], p["name"], p["amount"]) for p in whole["paid_by"]} == {
            ("card", "Card ••1111", 1_200), ("card", "Card ••2222", 3_400), ("entry", "usage", 700)}
        tools = FR.line_detail(c, OWNER, line, *OCT, vendor="acme-tools", at=AT)
        assert tools["vendor"] == "acme-tools" and tools["total"] == 700
        assert [e["amount"] for e in tools["entries"]] == [700]
        assert [(r["label"], r["amount"]) for r in tools["by_team"]] == [("Web", 700)]
        mine = FR.line_detail(c, "human:gus", line, *OCT, at=AT)
        assert [(p["kind"], p["name"]) for p in mine["paid_by"]] == [("card", "Card ••2222")]
        web = FR.line_detail(c, OWNER, line, *OCT, teams=["web"], at=AT)          # the Expenses rail's filter
        assert web["filter"]["teams"] == ["web"] and web["total"] == 1_200 + 700
        assert {e["amount"] for e in web["entries"]} == {1_200, 700}
        assert FR.line_detail(c, OWNER, line, *OCT, teammates=["human:gus"], at=AT)["total"] == 3_400
        assert FR.line_detail(c, OWNER, line, *OCT, who="bots", at=AT)["total"] == 0

        def tx(**kw):
            return sorted(abs(int(r["amount"])) for r in FR.transactions(c, OWNER, account_id=line, at=AT, **kw)["rows"])
        assert tx() == [700, 1_200, 3_400]
        assert tx(teams=["web"]) == [700, 1_200]                # "All transactions" from a filtered line, cut the same
        assert tx(teammates=["human:gus"]) == [3_400] and tx(who="bots") == []
        with pytest.raises(FinanceError, match="company-wide"):
            FR.transactions(c, "human:cara", unattributed=True, at=AT)


def test_transactions_by_line_and_team_match_on_the_same_split_line(books):
    """Round 4 #3: a split feed row ($10 Engineering on Software, $90 Sales on Other expenses) is not a Software +
    Sales match; it is for Software + Engineering and for Other expenses + Sales."""
    with books.transaction() as c:
        money = _bank(c)
        software, other = account(c, "6100"), account(c, "6900")
        entry = FA.post_entry(c, {"date": "2026-10-12", "source": "card", "source_id": "t-split", "lines": [
            {"account_id": software, "amount": 1_000, "tags": {"team": "eng"}},
            {"account_id": other, "amount": 9_000, "tags": {"team": "sales"}},
            {"account_id": money["cards_gl"], "amount": -10_000}]}, at=AT)
        SK.insert(c, "fin_feed_txns", {"currency": "USD", "created_at": AT, "created_by": "system:finance",
                                       "status": "posted", "money_account_id": money["program"],
                                       "provider_id": "t-split", "txn_date": "2026-10-12", "amount": -10_000,
                                       "counterparty": "Acme Supplies", "card_id": money["eve"],
                                       "entry_id": entry["entry_id"]})

        def split(line, team):
            rows = FR.transactions(c, OWNER, account_id=line, teams=[team], at=AT)["rows"]
            return [r["kind"] for r in rows if abs(int(r["amount"])) == 10_000]
        assert (split(software, "sales"), split(other, "eng")) == ([], [])
        assert (split(software, "eng"), split(other, "sales")) == (["feed"], ["feed"])


def test_sources_actions_are_a_finance_admins_and_mercury_users_are_cut_to_the_viewer(books):
    with books.transaction() as c:
        ids = _bank(c)
        cid = SK.insert(c, "fin_connections", {"provider": "mercury", "purpose": "bank", "status": "active",
                                               "name": "Mercury", "created_at": AT, "created_by": OWNER})
        SK.update(c, "fin_money_accounts", ids["bank"], {"connection_id": cid})
        SK.set_provider_users(c, cid, [{"id": "user-ana", "name": "Ana Acme", "email": "ana@acme.example"},
                                       {"id": "user-eve", "name": "Eve Acme", "email": "eve@acme.example"}], AT)
        users = FR.provider_users(c, OWNER, at=AT)["users"]
        assert [(u["id"], u["name"], u["email"]) for u in users] == [("user-ana", "Ana Acme", "a•••@acme.example"),
                                                                     ("user-eve", "Eve Acme", "e•••@acme.example")]
        with pytest.raises(FinanceError):
            FR.provider_users(c, "human:ben", at=AT)
        act(c, OWNER, "right_grant", {"teammate": "human:ben", "right": "finance.cards"})
        assert {u["email"] for u in FR.provider_users(c, "human:ben", at=AT)["users"]} == {None}

        assert act(c, "human:ben", "connection_sync", {"connection_id": cid}).code == "denied"
        assert act(c, OWNER, "connection_sync", {"connection_id": cid}).result["next_sync_at"] == AT
        asset = str(act(c, OWNER, "account_create", {"code": "1010", "name": "Acme savings", "type": "asset"})
                    .result["account_id"])
        assert act(c, OWNER, "connection_map", {"money_account_id": ids["bank"],
                                                "account_id": ids["cards_gl"]}).code == "invalid"
        assert act(c, OWNER, "connection_map", {"money_account_id": ids["bank"],
                                                "account_id": account(c, "1000")}).status == "committed"
        # N7: a finance admin acts only on sources they see (finance.admin grants no view).
        act(c, OWNER, "right_grant", {"teammate": "human:dan", "right": "finance.admin"})
        to_savings = {"money_account_id": ids["bank"], "account_id": asset}
        assert act(c, "human:dan", "connection_map", to_savings).code == "denied"           # no sources view
        assert act(c, "human:dan", "connection_sync", {"connection_id": cid}).code == "denied"
        act(c, OWNER, "view_grant", {"subject": "human:dan", "what": "sources"})
        assert act(c, "human:dan", "connection_map", to_savings).code == "not_found"        # nor the bank line
        assert act(c, "human:dan", "connection_disconnect", {"connection_id": cid}).code == "not_found"
        act(c, OWNER, "view_grant", {"subject": "human:dan", "what": "bs_lines", "account_ids": [account(c, "1000")]})
        assert act(c, "human:dan", "connection_map", to_savings).code == "not_found"        # nor the savings line
        assert act(c, "human:dan", "connection_sync", {"connection_id": cid}).status == "committed"
        moved = act(c, OWNER, "connection_map", to_savings)
        assert moved.status == "committed" and moved.result["account_id"] == asset
        SK.insert(c, "fin_outbox", {"connection_id": cid, "op": "card_sync", "idempotency_key": "o1",
                                    "created_at": AT, "created_by": OWNER})
        assert act(c, OWNER, "connection_disconnect", {"connection_id": cid}).code == "conflict"
        c.execute("UPDATE fin_outbox SET status='sent'")
        assert act(c, OWNER, "connection_disconnect", {"connection_id": cid}).result["status"] == "disconnected"
        assert FR.provider_users(c, OWNER, at=AT)["users"] == []
        assert act(c, OWNER, "connection_sync", {"connection_id": cid}).code == "conflict"
        sheet = FR.balance_sheet(c, OWNER, at=AT)
        assert sheet["closed_through"] is None
        act(c, OWNER, "close_period", {"month": "2026-09"})
        assert FR.balance_sheet(c, OWNER, at=AT)["closed_through"] == "2026-09-30"


def test_budgets_read_at_a_date_show_the_version_in_effect_then(books):
    with books.transaction() as c:
        soft = act(c, OWNER, "budget_create", {"name": "Software", "account_id": account(c, "6100"), "amount": 1_000})
        bid = str(soft.result["budget_id"])
        act(c, OWNER, "budget_update", {"budget_id": bid, "amount": 3_000, "effective_from": "2026-12-01"})

        def amount(on=None):
            rows = FR.budgets(c, OWNER, at=AT, on=on)["budgets"]
            return next(r["state"]["amount"] for r in rows if r["id"] == bid)
        assert (amount(), amount("2026-12-15"), amount("2026-11-15")) == (1_000, 3_000, 1_000)


def test_view_as_reaches_lines_expenses_and_budgets_and_only_for_the_owner(api):
    store = api.app.state.store
    with store.transaction() as c:
        from backend.finance import actions as FA
        assert FA.ensure_setup(c, store.settings.owner_email)
        line = account(c, "6100")
    for path in (f"finance/lines/{line}", "finance/expenses", "finance/budgets"):
        get(api, f"{path}?view_as=human:ben")
        get(api, f"{path}?view_as=human:ana", token="ben-test", expected=403)
    assert get(api, "finance/budgets?at=2026-12-15")["on"] == "2026-12-15"
    picked = get(api, "finance/pnl?view=expenses&teammates=human:ben&teammates=human:ana&who=people")["filter"]
    assert picked["teammates"] == ["human:ben", "human:ana"] and picked["who"] == "people"
    lined = get(api, f"finance/lines/{line}?teammates=human:ben&teammates=human:ana&who=people")["filter"]
    assert lined == {"teams": [], "teammates": ["human:ben", "human:ana"], "who": "people", "unattributed": False}
    assert "rows" in get(api, "finance/transactions?teammates=human:ben&who=people")
    get(api, "finance/transactions?teams=nope", expected=404)
    get(api, "finance/transactions?who=nobody", expected=422)
    get(api, "finance/mercury-users", token="ben-test", expected=403)
    assert get(api, "finance/mercury-users")["users"] == []


def test_chart_lists_unused_lines_and_tells_a_manager_whom_they_may_tag(books):
    with books.transaction() as c:
        act(c, OWNER, "right_grant", {"teammate": "human:cara", "right": "finance.book"})
        with pytest.raises(FinanceError):
            FR.chart(c, "human:eve")                                     # no finance.book, ap or ar
        owner = FR.chart(c, OWNER)["accounts"]
        assert "6100" in {a["number"] for a in owner}                    # no activity, still listed
        assert all(a["company_wide"] and "attributable" not in a for a in owner)
        line = next(a for a in FR.chart(c, "human:cara")["accounts"] if a["number"] == "6100")
        assert not line["company_wide"] and line["attributable"][0] == {"teammate": "human:cara"}
        assert {"team": "web"} in line["attributable"] and {"teammate": "human:eve"} in line["attributable"]


def test_a_team_override_leaves_the_other_teams_defaults_standing(books):
    with books.transaction() as c:
        soft = account(c, "6100")
        spend(c, "6100", 1_000, day="2026-09-10", team="web")
        spend(c, "6100", 2_000, day="2026-09-10", team="sales")
        assert FR.line_detail(c, OWNER, soft, at=AT)["next_month"] == {
            "month": "2026-11", "projection": 3_000, "projection_method": "recurring"}
        assert act(c, OWNER, "projection_set", {"account_id": soft, "month": "2026-11", "amount": 1_500,
                                                "team": "web"}).status == "committed"
        # Web's override replaces Web's default; Sales keeps its own.
        assert FR.line_detail(c, OWNER, soft, at=AT)["next_month"]["projection"] == 3_500


def test_a_trend_default_is_fitted_to_the_line_and_split_only_under_a_stored_projection(books):
    with books.transaction() as c:
        revenue = account(c, "4000")
        for n in range(6):      # April to September: web's revenue rises, sales' falls to nothing, eng holds flat
            day = f"2026-{4 + n:02d}-10"
            spend(c, "4000", -100 * (n + 1), day=day, team="web")
            spend(c, "4000", -100 * (6 - n), day=day, team="sales")
            spend(c, "4000", -300, day=day, team="eng")
        # The line is flat at 1,000 a month. Clamping sales' falling trend on its own would give 1,100.
        assert FR.line_detail(c, OWNER, revenue, at=AT)["next_month"] == {
            "month": "2026-11", "projection": 1_000, "projection_method": "trend"}
        assert act(c, OWNER, "projection_set", {"account_id": revenue, "month": "2026-11", "amount": 1_000,
                                                "team": "web"}).status == "committed"
        # Web's stored projection stands; sales and eng keep their own defaults, 0 and 300.
        assert FR.line_detail(c, OWNER, revenue, at=AT)["next_month"]["projection"] == 1_300
