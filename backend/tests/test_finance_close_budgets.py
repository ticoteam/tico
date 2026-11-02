"""The close and budget gaps from the Finance spec audit (spec v1.2): close snapshots and restatements, budget
versions in the past and the current period, the Expenses view, cash by account, burn and runway flags, the Gateway
meter fields, and the run hold average. One small test per gap; each fails before its fix."""
from datetime import date

import pytest

from backend.finance import reads as FR
from backend.finance import actions as FA
from clients import hubtools
from backend.finance.core.errors import NOT_FOUND, FinanceError
from backend.tests.test_finance_fixtures import (  # noqa: F401
    AT, MERCURY, OWNER, FakeMercury, account, act, books, budget, connect, finance_template, spend, store, sync_once)

OCT = ("2026-10-01", "2026-10-31")


def test_expenses_view_hides_a_budget_only_line_and_the_company_pnl_keeps_it(books):
    """§12: the Expenses view keeps lines with spend in the filter. A budget with no spend in the filter does not keep
    its line there; the company P&L shows the same line with its budget."""
    with books.transaction() as c:
        spend(c, "5100", 1_000, team="web", day="2026-10-02")
        made = act(c, OWNER, "budget_create", {"name": "Software", "account_id": account(c, "6100"), "amount": 5_000})
        assert made.status == "committed", made.reason
        expenses = FR.pnl(c, OWNER, *OCT, view="expenses", at=AT)
        assert "6100" not in [r["code"] for r in expenses["rows"]]
        assert "5100" in [r["code"] for r in expenses["rows"]]
        company = FR.pnl(c, OWNER, *OCT, at=AT)
        assert [r["budget"] for r in company["rows"] if r["code"] == "6100"] == [[5_000]]


def test_cash_for_one_money_account_counts_only_that_account(books):
    """The cash metric's account_id names one money account (the hub tool says so): its balance and only its row come
    back. An account that does not exist, or that the viewer cannot see, is not found."""
    fake = FakeMercury()
    connect(books, fake)
    sync_once(books, fake)
    with books.read() as c:
        everything = FR.metric(c, OWNER, "cash", at=AT)
        one = everything["accounts"][0]
        cut = FR.metric(c, OWNER, "cash", account_id=str(one["money_account_id"]), at=AT)
        assert cut["accounts"] == [one]
        assert cut["value"] == one["balance"]
        with pytest.raises(FinanceError) as missing:
            FR.metric(c, OWNER, "cash", account_id="ma_none", at=AT)
        assert missing.value.code == NOT_FOUND


def test_runway_is_withheld_for_a_slice_and_burn_says_it_is_scoped(books):
    """§7.5: company cash over one team's burn is no company runway, so a slice gets no runway and its burn says it is
    scoped; the owner's company runway carries no scoped flag."""
    with books.transaction() as c:
        spend(c, "5100", 1_000, team="sales", day="2026-08-10")
        for grant in ({"subject": "human:ben", "what": "expenses", "whose": "team", "whose_team": "sales"},
                      {"subject": "human:ben", "what": "bs_lines", "account_ids": [account(c, "1000")]}):
            assert act(c, OWNER, "view_grant", grant).status == "committed"
        ben = FR.metric(c, "human:ben", "runway_months", at=AT)
        owner = FR.metric(c, OWNER, "runway_months", at=AT)
        assert ben["scoped"] is True and ben["value"] is None
        assert owner["scoped"] is False and owner["value"] is not None
        assert FR.metric(c, "human:ben", "burn", start="2026-08-01", end="2026-08-31", at=AT)["scoped"] is True


def test_close_checklist_lists_the_money_accounts_behind_up_to_top_n_and_folds_the_rest(books):
    """§7.1: every read takes top_n and folds the rest into "Other · n"; the count stays the whole number."""
    fake = FakeMercury()
    connect(books, fake)
    for acct in MERCURY["accounts"]["accounts"]:            # one settled bank line on each account, so each has a feed
        fake.txns[f"txn-{acct['id']}"] = {**MERCURY["txn_bank_fee"], "id": f"txn-{acct['id']}", "accountId": acct["id"],
                                          "createdAt": "2026-10-14T10:00:00Z", "postedAt": "2026-10-14T10:00:00Z"}
    sync_once(books, fake)
    with books.read() as c:
        whole = next(k for k in FR.close_checklist(c, OWNER, "2026-10", at=AT)["checks"] if k["key"] == "reconciled")
        assert whole["count"] >= 2
        cut = next(k for k in FR.close_checklist(c, OWNER, "2026-10", at=AT, top_n=1)["checks"]
                   if k["key"] == "reconciled")
        assert cut["count"] == whole["count"] and len(cut["accounts"]) == 2
        assert cut["accounts"][-1]["key"] == FR.OTHER and cut["accounts"][-1]["count"] == whole["count"] - 1


def test_the_hub_close_passes_top_n_and_spend_by_offers_every_group_the_server_has():
    calls: list[tuple[str, dict[str, object]]] = []

    class Api:
        def get(self, path, **query):
            calls.append((path, query))
            return {"checks": []}
    hubtools.finance_report(Api(), {"kind": "close", "month": "2026-10", "top_n": 5})
    assert calls == [("finance/close/2026-10", {"top_n": 5})]
    report = next(t for t in hubtools.TOOLS if t["name"] == "hub_finance_report")
    assert set(report["inputSchema"]["properties"]["by"]["enum"]) == set(FR.GROUPS)


def test_the_current_month_shows_its_budget_to_date_beside_the_full_month(books):
    """§4.4: the current month's budget shows up to today next to the whole month; a past or future month has no
    budget to date, only its budget."""
    with books.transaction() as c:
        made = act(c, OWNER, "budget_create", {"name": "Software", "account_id": account(c, "6100"), "amount": 5_000})
        assert made.status == "committed", made.reason
        software = next(r for r in FR.pnl(c, OWNER, "2026-09-01", "2026-10-31", at=AT)["rows"] if r["code"] == "6100")
        assert software["budget"] == [None, 5_000]
        assert software["budget_to_date"][0] is None
        assert 0 < software["budget_to_date"][1] < 5_000


def test_a_past_period_budget_amount_is_refused_on_create_and_update(books):
    """§12: only future periods are editable, so an amount dated into a closed past period is refused."""
    with books.transaction() as c:
        past = act(c, OWNER, "budget_create", {"name": "Software", "account_id": account(c, "6100"), "amount": 5_000,
                                               "effective_from": "2026-09-01"})
        assert past.code == "invalid" and "past period" in past.reason
        made = act(c, OWNER, "budget_create", {"name": "Software", "account_id": account(c, "6100"), "amount": 5_000})
        assert made.status == "committed", made.reason
        again = act(c, OWNER, "budget_update", {"budget_id": budget(c, "Software"), "amount": 6_000,
                                                "effective_from": "2026-09-01"})
        assert again.code == "invalid" and "past period" in again.reason


def test_a_raise_in_the_current_period_asks_the_budget_owner_and_a_future_one_does_not(books):
    """§4.4 and §12: a current-period raise goes through the budget owner's approval; a raise for a future period is
    the ordinary change."""
    with books.transaction() as c:
        made = act(c, OWNER, "budget_create", {"name": "Software", "account_id": account(c, "6100"), "amount": 5_000})
        assert made.status == "committed", made.reason
        bid = budget(c, "Software")
        assert act(c, OWNER, "budget_update", {"budget_id": bid, "owner": "human:ben"}).status == "committed"
        raise_now = act(c, OWNER, "budget_update", {"budget_id": bid, "amount": 6_000})
        assert (raise_now.status, raise_now.approver) == ("needs_approval", "human:ben")
        later = act(c, OWNER, "budget_update", {"budget_id": bid, "amount": 7_000, "effective_from": "2026-11-01"})
        assert later.status == "committed", later.reason


def _turn(c, bot, finished, cost, model="tico-medium", billing="api"):
    tid = f"t{bot}{finished}{model}"
    c.execute("INSERT INTO turns(id, bot, started, finished, model, est_cost_usd, billing) VALUES(?,?,?,?,?,?,?)",
              (tid, bot, finished, finished, model, cost, billing))
    return tid


def test_an_own_key_bot_holds_its_recent_average_and_a_subscription_run_never_counts(books):
    """§9: a run hold is sized from the bot's recent average per run, whichever key paid for the runs. A flat
    subscription run never counts toward that average."""
    with books.transaction() as c:
        ai = budget(c, "AI services")
        act(c, OWNER, "budget_update", {"budget_id": ai, "amount": 1_000})
        _turn(c, "coder", "2026-10-14T10:00:00.000000Z", 0.40, model="claude-sonnet-4-5")
        _turn(c, "coder", "2026-10-13T10:00:00.000000Z", 9.00, model="claude-sonnet-4-5", billing="subscription")
        held = FA.run_hold(c, "coder", "job-own", at=AT)
        assert held.hold_id and not held.refused
        assert FA.budget_state(c, ai, AT).held == 40
