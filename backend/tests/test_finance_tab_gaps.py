"""The Finance tab's later asks, cut to the viewer: the owner edits the view defaults; the expenses view filters by
several teams and teammates, bots or people, and Unattributed (company-wide viewers only); a line opens per vendor with
how it was paid and next month's projection; budgets read at a date; Sources' sync / disconnect / map are a finance
admin's; Mercury's users are for teammates who may issue cards, emails masked and only for whoever sees everything;
view_as reaches lines, expenses and budgets."""
import pytest

from backend.finance import actions as FA
from backend.finance import reads as FR
from backend.finance.actions import FinanceError
from backend.finance.store import banking as SK
from backend.tests.test_api import api, get, headers  # noqa: F401
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


def test_a_finance_admin_adds_no_view_of_its_own_so_emails_tax_ids_and_sync_errors_stay_with_the_owner(books):
    """finance.admin is rights (§7.4), not view (§7.5): Fay, an admin with payables, reads the vendor's tax id last 4,
    the Mercury emails and the failed-sync inbox only as the owner would, which is not at all here."""
    with books.transaction() as c:
        ids = _bank(c)
        cid = SK.insert(c, "fin_connections", {"provider": "mercury", "purpose": "bank", "status": "error",
                                               "name": "Mercury", "last_error": "token expired", "created_at": AT,
                                               "created_by": OWNER})
        SK.update(c, "fin_money_accounts", ids["bank"], {"connection_id": cid})
        SK.set_provider_users(c, cid, [{"id": "user-eve", "name": "Eve Acme", "email": "eve@acme.example"}], AT)
        made = act(c, OWNER, "contact_create", {"name": "Acme Supplies", "kind": "vendor", "tax_id": "98-7654321"})
        assert made.status == "committed", made
        assert act(c, OWNER, "right_grant", {"teammate": "human:fay", "right": "finance.admin"}).status == "committed"
        assert act(c, OWNER, "view_grant", {"subject": "human:fay", "what": "payables"}).status == "committed"
    with books.read() as c:
        assert FR.provider_users(c, OWNER, at=AT)["users"][0]["email"] == "e•••@acme.example"
        assert FR.provider_users(c, "human:fay", at=AT)["users"][0]["email"] is None
        assert [x["id"] for x in FR.inbox(c, OWNER, kind="sync_failed", at=AT)["items"]] == [cid]
        assert FR.inbox(c, "human:fay", kind="sync_failed", at=AT)["items"] == []
        for who, last4 in ((OWNER, "4321"), ("human:fay", None)):
            row = next(x for x in FR.contacts(c, who, at=AT)["contacts"] if x["name"] == "Acme Supplies")
            assert row["tax_id_last4"] == last4, who
        assert [m["teammate"] for m in FR.access(c, "human:fay", at=AT)["teammates"]] == ["human:fay"]



def test_the_bills_address_and_a_books_disconnect_are_actions_only_a_finance_admin_may_ask_for(books):
    """Spec §5: the bills address and a books connection's local part change only through actions. A teammate without
    finance.admin is refused; a bad address is refused with its reason."""
    with books.transaction() as c:
        assert act(c, "human:dan", "bills_address_set", {"address": "bills@acme.example"}).code == "denied"
        assert act(c, OWNER, "bills_address_set", {"address": "not-an-address"}).code == "invalid"
        assert act(c, OWNER, "bills_address_set", {"address": "Bills@Acme.example"}).status == "committed"
        assert act(c, "human:dan", "books_disconnect", {"connection_id": "nope"}).code == "denied"
        assert act(c, OWNER, "books_disconnect", {"connection_id": "nope"}).code == "not_found"
    with books.read() as c:
        assert FR.bills_email(c) == "bills@acme.example"

def test_the_finance_approver_is_the_owner_until_the_owner_names_a_person(books):
    """Spec §16: the finance approver is the owner by default, with a setting to name one person. Only the owner sets it,
    and a bot can't be named."""
    from backend.finance.context import load
    with books.transaction() as c:
        assert load(c, AT).approver_setting() == ""
        assert act(c, "human:dan", "finance_approver_set", {"approver": "human:dan"}).code == "denied"
        assert act(c, OWNER, "finance_approver_set", {"approver": "bot:coder"}).code == "invalid"
        assert act(c, OWNER, "finance_approver_set", {"approver": "human:dan"}).status == "committed"
        assert load(c, AT).approver_setting() == "human:dan"
        assert act(c, OWNER, "finance_approver_set", {"approver": ""}).status == "committed"
        assert load(c, AT).approver_setting() == ""


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


def test_tico_sources_show_with_no_entries_and_net_income_comes_from_the_pnl(books):
    with books.transaction() as c:
        groups = {g["group"]: g for g in FR.sources(c, OWNER, at=AT)["groups"]}
        assert [r["name"] for r in groups["bills_invoices"]["rows"]] == ["Tico invoices"] and groups["tico"]["rows"] == []
        FA.set_bills_email(c, "bills@acme.example", OWNER, AT)
        groups = {g["group"]: g for g in FR.sources(c, OWNER, gateway=True, at=AT)["groups"]}
        assert [(r["kind"], r.get("address")) for r in groups["bills_invoices"]["rows"]] == [
            ("inbox", "bills@acme.example"), ("documents", None)]
        assert [(r["kind"], r["entries"], r["connected"]) for r in groups["tico"]["rows"]] == [("usage", 0, True)]
        net = FR.balance_sheet(c, OWNER, at=AT)["equity"][-1]
        assert net["name"] == "Net income to date" and net["entry_sources"] == ["pnl"]


def test_a_line_projects_next_month_for_each_vendor_team_and_teammate(books):
    with books.transaction() as c:
        spend(c, "6100", 1_000, day="2026-09-05", team="web", teammate="human:eve", vendor="acme-tools")
        spend(c, "6100", 500, day="2026-09-07", team="sales", vendor="acme-cloud")
        spend(c, "6100", 300, day="2026-10-02", team="web", vendor="acme-tools")
        line = account(c, "6100")
        by = FR.line_detail(c, OWNER, line, *OCT, at=AT)["next_by"]      # Software recurs: September's
        assert by["vendor"] == {"acme-tools": 1_000, "acme-cloud": 500}
        assert by["team"] == {"web": 1_000, "sales": 500}
        assert by["teammate"] == {"human:eve": 1_000, FR.NONE: 500}
        assert FR.line_detail(c, "human:dan", line, *OCT, at=AT)["next_by"]["vendor"] == {"acme-cloud": 500}


def test_bills_show_their_line_team_and_when_paid_and_late_keeps_those_past_due(books):
    with books.transaction() as c:
        vendor = str(act(c, OWNER, "contact_create", {"name": "Acme Tools", "kind": "vendor"}).result["contact_id"])

        def bill(number, day, due, **tags):
            made = act(c, OWNER, "bill_create", {"vendor_id": vendor, "number": number, "bill_date": day,
                                                 "due_date": due, "lines": [{"account_id": account(c, "6100"),
                                                                             "amount": 2_000, "tags": tags}]})
            assert act(c, OWNER, "bill_approve", {"bill_id": made.result["bill_id"]}).status == "committed"
            return str(made.result["bill_id"])
        late = bill("B-1", "2026-09-01", "2026-09-15", team="web")
        bill("B-2", "2026-10-01", "2026-11-30")
        paid = bill("B-3", "2026-09-20", "2026-10-01", team="sales")
        assert act(c, OWNER, "bill_mark_paid", {"bill_id": paid, "date": "2026-10-05"}).status == "committed"
        rows = {r["number"]: r for r in FR.bills(c, OWNER, at=AT)["bills"]}
        assert rows["B-1"]["account"]["name"] == "Software" and rows["B-1"]["team"] == "web"
        assert rows["B-2"]["team"] is None and not rows["B-2"]["split"]
        assert (rows["B-3"]["paid_on"], rows["B-3"]["paid_by"]) == ("2026-10-05", OWNER)
        assert [r["id"] for r in FR.bills(c, OWNER, status="unpaid", late=True, at=AT)["bills"]] == [late]


def test_a_lines_breakdown_follows_a_saved_projection(books):
    with books.transaction() as c:
        spend(c, "6100", 1_000, day="2026-09-05", team="eng", teammate="human:ben", vendor="acme-tools")
        spend(c, "6100", 500, day="2026-09-07", team="sales", vendor="acme-cloud")
        line = account(c, "6100")
        assert act(c, OWNER, "projection_set", {"account_id": line, "month": "2026-11", "team": "eng",
                                                "amount": 9_000}).status == "committed"
        got = FR.line_detail(c, OWNER, line, *OCT, at=AT)
        assert got["next_month"]["projection"] == 9_500                 # Engineering's saved 9,000 and Sales' 500
        assert got["next_by"]["team"] == {"eng": 9_000, "sales": 500}
        assert got["next_by"]["teammate"] == {"human:ben": 9_000, FR.NONE: 500}
        assert got["next_by"]["vendor"] == {"acme-tools": 9_000, "acme-cloud": 500}


def test_a_parent_lines_breakdown_projects_each_line_with_its_own_method(books):
    with books.transaction() as c:
        spend(c, "5100", 3_100, day="2026-10-10", team="eng")            # AI services runs at its last 30 days
        got = FR.line_detail(c, OWNER, account(c, "5000"), *OCT, at=AT)
        assert got["next_month"]["projection"] == 3_100
        assert got["next_by"]["team"] == {"eng": 3_100}


def test_a_saved_projection_with_no_weighing_history_lands_on_that_dimensions_rows(books):
    with books.transaction() as c:
        spend(c, "6100", 1_000, day="2026-10-02", team="eng", teammate="human:ben", vendor="acme-tools")
        line = account(c, "6100")
        for team, amount in (("eng", 9_000), ("sales", 400)):               # Sales has no history at all
            assert act(c, OWNER, "projection_set", {"account_id": line, "month": "2026-11", "team": team,
                                                    "amount": amount}).status == "committed"
        got = FR.line_detail(c, OWNER, line, *OCT, at=AT)
        assert got["next_month"]["projection"] == 9_400
        assert got["next_by"]["vendor"] == {"acme-tools": 9_000, FR.NONE: 400}
        assert got["next_by"]["team"] == {"eng": 9_000, "sales": 400}
        assert got["next_by"]["teammate"] == {"human:ben": 9_000, FR.NONE: 400}


def test_each_column_of_a_lines_breakdown_adds_up_to_its_projection(books):
    import random
    rng = random.Random(7)
    mates = {"human:eve": "web", "human:fay": "web", "human:gus": "sales", "bot:coder": "web", "": "eng", "human:ben": ""}
    with books.transaction() as c:
        for _ in range(60):
            mate = rng.choice(sorted(mates))
            code = rng.choice(["4000", "5100", "6100", "6900"])
            amount = rng.randint(101, 9_999) * (-1 if code == "4000" else 1)
            spend(c, code, amount, day=f"2026-{rng.randint(4, 10):02d}-{rng.randint(1, 28):02d}",
                  team=mates[mate], teammate=mate, vendor=rng.choice(["acme-tools", "acme-cloud", ""]))
        assert act(c, OWNER, "projection_set", {"account_id": account(c, "4000"), "month": "2026-11",
                                                "teammate": "human:gus", "amount": 9_000}).status == "committed"
        assert act(c, OWNER, "projection_set", {"account_id": account(c, "6100"), "month": "2026-11",
                                                "team": "web", "amount": 7_777}).status == "committed"
        for viewer in (OWNER, "human:ben", "human:cara"):
            for code in ("4000", "5000", "5100", "6000", "6100", "6900"):
                if viewer != OWNER and code == "4000":
                    continue
                got = FR.line_detail(c, viewer, account(c, code), *OCT, at=AT)
                total = got["next_month"]["projection"] or 0
                for by, rows in got["next_by"].items():
                    assert sum(rows.values()) == total, (viewer, code, by, rows, total)


def test_a_projection_is_shared_out_by_largest_remainder():
    keys = [("l", v, "", "") for v in ("a", "b", "c")]
    assert list(FR._largest_remainder(10, dict.fromkeys(keys, 1)).values()) == [4, 3, 3]
    assert list(FR._largest_remainder(10, dict(zip(keys, (1, 2, 3)))).values()) == [2, 3, 5]
    assert list(FR._largest_remainder(2, dict.fromkeys(reversed(keys), 1)).values()) == [0, 1, 1]  # ties by key


def test_a_saved_projection_with_no_weights_splits_its_cents_evenly(books):
    with books.transaction() as c:
        for v in ("acme-a", "acme-b", "acme-c"):                          # October only: no recurring weight
            spend(c, "6100", 1_000, day="2026-10-02", team="eng", vendor=v)
        line = account(c, "6100")
        assert act(c, OWNER, "projection_set", {"account_id": line, "month": "2026-11", "team": "eng",
                                                "amount": 2}).status == "committed"
        got = FR.line_detail(c, OWNER, line, *OCT, at=AT)["next_by"]
        assert got["vendor"] == {"acme-a": 1, "acme-b": 1, "acme-c": 0}
        assert got["team"] == {"eng": 2}


def test_recently_paid_bills_page_by_when_they_were_paid(books):
    with books.transaction() as c:
        vendor = str(act(c, OWNER, "contact_create", {"name": "Acme Tools", "kind": "vendor"}).result["contact_id"])

        def paid(number, day, on):
            made = act(c, OWNER, "bill_create", {"vendor_id": vendor, "number": number, "bill_date": day,
                                                 "due_date": day, "lines": [{"account_id": account(c, "6100"),
                                                                             "amount": 1_000}]})
            bill = str(made.result["bill_id"])
            assert act(c, OWNER, "bill_approve", {"bill_id": bill}).status == "committed"
            assert act(c, OWNER, "bill_mark_paid", {"bill_id": bill, "date": on}).status == "committed"
        paid("AUG", "2026-08-03", "2026-10-15")                       # an old bill, paid last
        for n in range(20):
            paid(f"S-{n:02d}", f"2026-09-{n + 1:02d}", "2026-10-01")
        assert "AUG" not in [b["number"] for b in FR.bills(c, OWNER, status="paid", limit=20, at=AT)["bills"]]
        first = FR.bills(c, OWNER, status="paid", order="paid_on", limit=5, at=AT)
        assert [b["number"] for b in first["bills"]][0] == "AUG" and first["bills"][0]["paid_on"] == "2026-10-15"
        seen = [b["number"] for b in first["bills"]]
        cursor = first["next_cursor"]
        while cursor:
            page = FR.bills(c, OWNER, status="paid", order="paid_on", limit=5, cursor=cursor, at=AT)
            seen += [b["number"] for b in page["bills"]]
            cursor = page["next_cursor"]
        assert len(seen) == len(set(seen)) == 21
        with pytest.raises(FinanceError):
            FR.bills(c, "human:eve", status="paid", order="paid_on", at=AT)           # payables stay out of her view


def test_a_projection_with_no_spend_is_named_for_its_drill_down_row(books):
    with books.transaction() as c:
        spend(c, "6100", 1_000, day="2026-10-02", team="eng", teammate="human:ben")
        line = account(c, "6100")
        assert act(c, OWNER, "projection_set", {"account_id": line, "month": "2026-11", "team": "sales",
                                                "amount": 400}).status == "committed"
        got = FR.line_detail(c, OWNER, line, *OCT, at=AT)
        assert got["next_by"]["team"]["sales"] == 400 and "sales" not in {r["key"] for r in got["by_team"]}
        assert got["next_labels"]["team"]["sales"] == "Sales"
        assert got["next_labels"]["teammate"][FR.NONE] == "Unattributed"


def test_a_vendor_keyed_other_is_its_own_row_apart_from_the_folded_one(books):
    with books.transaction() as c:
        for day in ("2026-09-05", "2026-10-05"):                           # Software recurs: September's projects
            for vendor, amount in (("other", 10_000), ("acme-a", 500), ("acme-b", 400), ("acme-c", 300)):
                spend(c, "6100", amount, day=day, team="eng", vendor=vendor)
        got = FR.line_detail(c, OWNER, account(c, "6100"), *OCT, at=AT, top_n=2)
        keys = [r["key"] for r in got["by_vendor"]]
        assert len(keys) == len(set(keys)) and keys[-1] == FR.OTHER and "other" in keys
        assert next(r for r in got["by_vendor"] if r["key"] == "other")["amount"] == 10_000
        assert got["by_vendor"][-1]["amount"] == 700 and got["by_vendor"][-1]["label"] == "Other · 2"
        nxt = got["next_by"]["vendor"]
        assert nxt["other"] == 10_000 and sum(nxt.values()) == got["next_month"]["projection"] == 11_200
        assert FR.OTHER not in nxt                                         # the folded row's sum is the UI's to add


def test_a_vendor_named_unattributed_is_its_own_row_apart_from_no_vendor(books):
    with books.transaction() as c:
        for day in ("2026-09-05", "2026-10-05"):
            spend(c, "6100", 1_000, day=day, team="eng", vendor="unattributed")
            spend(c, "6100", 300, day=day)                                  # no vendor, team or teammate
        line = account(c, "6100")
        got = FR.line_detail(c, OWNER, line, *OCT, at=AT)
        rows = {r["key"]: r for r in got["by_vendor"]}
        assert rows["unattributed"]["amount"] == 1_000 and rows[FR.NONE]["amount"] == 300
        assert rows[FR.NONE]["label"] == "No vendor" and rows["unattributed"]["label"] != "No vendor"
        assert got["next_by"]["vendor"] == {"unattributed": 1_000, FR.NONE: 300}
        assert got["next_labels"]["vendor"][FR.NONE] == "No vendor"
        assert got["next_by"]["team"] == {"eng": 1_000, FR.NONE: 300}
        alone = FR.line_detail(c, OWNER, line, *OCT, at=AT, vendor="unattributed")
        assert alone["total"] == 1_000                                     # the vendor, not the lines with none


def test_a_full_read_of_a_private_line_by_someone_else_is_written_to_events(api):
    """Spec 7.5: a full read of a private line is written to events. Eve holds full detail of account 6900 (private), so
    her requests leave a row naming it; the owner's own requests leave none."""
    from backend.finance import actions as FA
    from backend.tests.test_finance_fixtures import _seed
    store = api.app.state.store
    with store.transaction() as c:
        _seed(c)
        assert FA.ensure_setup(c, store.settings.owner_email)
        private = account(c, "6900")
        assert act(c, OWNER, "private_lines_set", {"account_id": private, "private": True,
                                                   "full_detail": ["human:eve"]}).status == "committed"
    get(api, "finance/pnl", token="eve-test")
    get(api, "finance/pnl")
    with store.read() as c:
        rows = c.execute("SELECT actor, target FROM events WHERE action='finance.private_read'").fetchall()
    assert [tuple(r) for r in rows] == [("human:eve", private)]


def test_each_request_by_someone_else_with_private_detail_leaves_one_row_for_the_read(api):
    """Spec 7.5: one path serves the tools, search and exports, and each request leaves one row. Eve holds full detail of
    account 6900, so each of her requests (search, the tax CSV, the close, the webhook bodies, an action's status, and a
    dry run) adds exactly one 'finance.private_read' row, whatever the route answers. The owner's requests add none."""
    from backend.finance import actions as FA
    from backend.tests.test_finance_fixtures import _seed
    store = api.app.state.store
    with store.transaction() as c:
        _seed(c)
        assert FA.ensure_setup(c, store.settings.owner_email)
        private = account(c, "6900")
        payload = {"account_id": private, "private": True, "full_detail": ["human:eve"]}
        done = act(c, OWNER, "private_lines_set", payload)
        assert done.status == "committed"

    def rows(actor):
        with store.read() as c:
            return c.execute("SELECT COUNT(*) FROM events WHERE action='finance.private_read' AND actor=?",
                             (actor,)).fetchone()[0]

    reads = [("GET", "/api/v2/finance/search?kind=entry"), ("GET", "/api/v2/finance/tax/sales-tax.csv?year=2026"),
             ("GET", "/api/v2/finance/close/2026-10"), ("GET", "/api/v2/finance/webhook-deliveries"),
             ("GET", f"/api/v2/finance/actions/{done.action_id}"),
             ("POST", "/api/v2/finance/actions")]
    for method, path in reads:
        before = rows("human:eve")
        if method == "GET":
            r = api.get(path, headers=headers("eve-test"))
        else:
            r = api.post(path, json={"kind": "private_lines_set", "payload": payload, "dry_run": True},
                         headers=headers("eve-test"))
        assert r.status_code < 500, (path, r.text)
        assert rows("human:eve") == before + 1, path
    before = rows(OWNER)
    api.get("/api/v2/finance/close/2026-10", headers=headers("ana-test"))
    assert rows(OWNER) == before == 0


def test_a_csv_export_is_recorded_for_whoever_it_was_made_for(api):
    """Spec 7.5: exports record who they were made for. The sales tax CSV the owner downloads leaves a row naming the
    owner and the export; nothing is stored, so there is no copy to expire."""
    from backend.finance import actions as FA
    from backend.tests.test_finance_fixtures import _seed
    store = api.app.state.store
    with store.transaction() as c:
        _seed(c)
        assert FA.ensure_setup(c, store.settings.owner_email)
    r = api.get("/api/v2/finance/tax/sales-tax.csv?year=2026", headers=headers("ana-test"))
    assert r.status_code == 200, r.text
    with store.read() as c:
        rows = c.execute("SELECT actor, target FROM events WHERE action='finance.export'").fetchall()
    assert [tuple(x) for x in rows] == [("human:ana", "sales-tax")]


def test_a_person_who_leaves_on_the_people_route_leaves_their_team_grant_at_once(api):
    """Spec 7.5: grants follow the org chart and membership. The people route writes the roster itself, not through the
    roster save, so it runs Finance's revalidation too: the team grant forgets the leaver at once."""
    from backend.finance import actions as FA
    from backend.finance.store import access as SA
    from backend.tests.test_finance_fixtures import _seed
    store = api.app.state.store
    with store.transaction() as c:
        _seed(c)
        assert FA.ensure_setup(c, store.settings.owner_email)
        grant = act(c, OWNER, "view_grant", {"subject_kind": "team", "subject": "web", "what": "expenses",
                                             "whose": "company"})
        assert grant.status == "committed", grant
        gid = str(grant.result["grant_id"])
        assert "human:eve" in SA.grant(c, gid).members
    r = api.post("/api/v2/people/eve", json={"left": True}, headers=headers("ana-test"))
    assert r.status_code == 200, r.text
    with store.read() as c:
        assert "human:eve" not in SA.grant(c, gid).members


def test_a_fault_in_the_org_revalidation_leaves_the_person_update_and_no_partial_grant_change(api, monkeypatch):
    """A Finance fault must not stop Tico's own writes (spec: a Finance fault doesn't stop bots). The revalidation a
    roster save runs fails here after a partial change (eve leaves the grant's members and the grant is suspended). The
    person update still commits; the partial change is rolled back, so the grant is as it was until Finance's next
    action runs the revalidation again."""
    import json

    from backend.finance import actions as FA
    from backend.finance import handlers
    from backend.finance.store import access as SA
    from backend.tests.test_finance_fixtures import _seed
    store = api.app.state.store
    with store.transaction() as c:
        _seed(c)
        assert FA.ensure_setup(c, store.settings.owner_email)
        grant = act(c, OWNER, "view_grant", {"subject_kind": "team", "subject": "web", "what": "expenses",
                                             "whose": "company"})
        assert grant.status == "committed", grant
        gid = str(grant.result["grant_id"])
    calls = []

    def half_done(ctx):
        calls.append(gid)
        SA.set_members(ctx.c, gid, SA.grant(ctx.c, gid).members - {"human:eve"})
        SA.set_grant_status(ctx.c, gid, "suspended", FA.SYSTEM, ctx.at)
        raise RuntimeError("forced: the revalidation fails after a partial change")

    monkeypatch.setattr(handlers, "suspend_unstanding", half_done)
    r = api.post("/api/v2/people/eve", json={"left": True}, headers=headers("ana-test"))
    assert r.status_code == 200, r.text
    assert calls == [gid]
    with store.read() as c:
        g = SA.grant(c, gid)
        assert g.status == "active" and "human:eve" in g.members
        people = json.loads(c.execute("SELECT value_json FROM registry_metadata WHERE key='people'").fetchone()[0])
    assert next(p for p in people["people"] if p["id"] == "eve")["hidden"]
