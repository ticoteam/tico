"""Finance is the owner's alone by default (backend/finance/core/view.py Defaults): a new book shows nothing to a
manager, a member or a bot until the owner grants it or opens the defaults up. A teammate always sees their own budgets
(handlers.reads_budget: Finance's My budget, the bot reads, the warnings) and nothing more."""

import pytest

from backend.finance import reads, tab
from backend.finance.core.errors import NOT_FOUND, FinanceError
from backend.finance.context import load
from backend.store import Store
from backend.tests.helpers.finance_ad_budgets import WARN_AT, platform_spend, rows_by_name, warn, warning_notes, world
from backend.tests.helpers.finance_fixtures import (
    AT,
    OWNER,
    act,
    books,
    finance_template,
    open_views,
    owner_only_books,
    store,
)


def test_a_new_book_shows_nothing_to_anyone_but_the_owner(owner_only_books: Store):
    with owner_only_books.read() as c:
        ctx = load(c, AT)
        assert ctx.view(OWNER).everything
        # ben leads Engineering, cara leads Web, eve is a member, coder a bot.
        for who in ("human:ben", "human:cara", "human:eve", "bot:coder"):
            assert not ctx.view(who).sees_anything(), who
        rows = {r["teammate"]: r["default"] for r in tab.access(c, OWNER, AT)["teammates"]}
        assert rows[OWNER] == "everything"
        assert {rows[w] for w in ("human:ben", "human:cara", "human:eve", "bot:coder")} == {"none"}


def test_the_owner_opens_it_by_grant_or_by_default(owner_only_books: Store):
    with owner_only_books.transaction() as c:
        assert act(c, OWNER, "view_grant", {"subject": "human:eve", "what": "expenses", "whose": "own"}).status == (
            "committed"
        )
        ctx = load(c, AT)
        assert ctx.view("human:eve").sees_anything()
        assert not ctx.view("human:fay").sees_anything()
        # A manager grants nothing while managers see only what everyone else does.
        assert act(
            c, "human:cara", "view_grant", {"subject": "human:fay", "what": "expenses", "whose": "own"}
        ).code == ("denied")
        assert act(c, OWNER, "view_defaults_set", {"manager": "team_expenses"}).status == "committed"
        ctx = load(c, AT)
        assert ctx.view("human:cara").teams() == frozenset({"web"})
        assert not ctx.view("human:fay").sees_anything()
        assert act(c, OWNER, "view_defaults_set", {"manager": "none", "member": "own_expenses"}).status == "committed"
        ctx = load(c, AT)
        assert not ctx.view("human:cara").teams()
        assert ctx.view("human:fay").sees_anything()


def test_a_bot_always_sees_its_own_budget_and_nothing_else(owner_only_books: Store):
    w = world(owner_only_books)
    platform_spend(owner_only_books, w["google_ads"], [("2026-10-03", 50_000)], "2026-10-01", "2026-10-15")
    with owner_only_books.read() as c:
        assert not load(c, AT).view("bot:paid-rental-ads").sees_anything()
        own = rows_by_name(c, "bot:paid-rental-ads")
        assert set(own) == {"Rental Google Ad Owner"}
        assert own["Rental Google Ad Owner"]["state"]["actual"] == 50_000  # type: ignore[index]
        assert reads.metric(c, "bot:paid-rental-ads", "budget_remaining", budget_id=w["google"], at=AT)["value"] == (
            250_000
        )
        listed = reads.budget_vs_actual(c, "bot:paid-rental-ads", "2026-10-01", "2026-10-31", at=AT)["budgets"]
        assert [b["name"] for b in listed] == ["Rental Google Ad Owner"]
        # Not another bot's, and not its reports' for the manager bot, which has no budget of its own here.
        assert set(rows_by_name(c, "bot:rental-social-ad-owner")) == {"Rental Social Ads Owner"}
        assert rows_by_name(c, "bot:cmo") == {}
        with pytest.raises(FinanceError) as refused:
            reads.metric(c, "bot:rental-social-ad-owner", "budget_remaining", budget_id=w["google"], at=AT)
        assert refused.value.code == NOT_FOUND
        # Nothing else of Finance: no P&L, and Access counts the budgets My budget lists.
        with pytest.raises(FinanceError):
            reads.records(c, "bot:paid-rental-ads", kind="item")
        rows = {r["teammate"]: r for r in tab.access(c, OWNER, AT)["teammates"]}
        assert rows["bot:paid-rental-ads"]["budgets"] == 1 and rows["bot:cmo"]["budgets"] == 0
        assert rows["bot:paid-rental-ads"]["default"] == "none"


def test_a_bot_needs_no_grant_for_its_own_budget_warnings(owner_only_books: Store):
    w = world(owner_only_books, WARN_AT)
    platform_spend(
        owner_only_books, w["google_ads"], [("2026-10-02", 40_000), ("2026-10-05", 36_000)], "2026-10-01", "2026-10-06"
    )
    assert warn(owner_only_books) == [("bot:paid-rental-ads", ("pace",))]
    with owner_only_books.read() as c:
        assert [to for to, _ in warning_notes(c)] == ["bot:paid-rental-ads"]


def test_a_bot_with_no_finance_view_is_briefed_on_its_own_budget_only(owner_only_books: Store):
    from backend.finance import brief

    w = world(owner_only_books)
    platform_spend(owner_only_books, w["google_ads"], [("2026-10-03", 50_000)], "2026-10-01", "2026-10-15")
    with owner_only_books.read() as c:
        lines = brief.compute(c, "bot:paid-rental-ads", AT)
        assert len(lines) == 1, lines
        assert lines[0].startswith("Budget: Rental Google Ad Owner, $500 of $3,000/month (17%), id ")
        assert lines[0].endswith(f"id {w['google']}") and len(lines[0]) <= brief.LINE_CHARS
        assert brief.compute(c, "bot:cmo", AT) == []  # no budget of its own, no view: nothing


def test_a_bot_is_told_once_when_a_budget_is_made_or_changed_for_it(owner_only_books: Store):
    w = world(owner_only_books)

    def told(c, bot):
        return [
            str(r[0])
            for r in c.execute(
                "SELECT body FROM notes WHERE to_actor=? AND body LIKE 'You have a budget:%' ORDER BY rowid", (bot,)
            )
        ]

    with owner_only_books.transaction() as c:
        assert told(c, "bot:paid-rental-ads") == [
            "You have a budget: Rental Google Ad Owner 3,000.00 USD/month; read it with tico_finance_report "
            "kind=budget_vs_actual"
        ]
        assert len(told(c, "bot:rental-social-ad-owner")) == 1 and told(c, "bot:cmo") == []
        done = act(c, OWNER, "budget_update", {"budget_id": w["google"], "amount": 250_000})
        assert done.status == "committed", done
        assert told(c, "bot:paid-rental-ads")[-1].startswith("You have a budget: Rental Google Ad Owner 2,500.00 USD")
        assert len(told(c, "bot:paid-rental-ads")) == 2


def test_the_expenses_view_shows_a_bot_its_own_budget_outside_its_lines(owner_only_books: Store):
    from backend.tests.helpers.finance_fixtures import account

    w = world(owner_only_books)
    with owner_only_books.transaction() as c:
        grant = {
            "subject": "bot:paid-rental-ads",
            "what": "pnl_lines",
            "whose": "own",
            "account_ids": [account(c, "6100")],
        }
        assert act(c, OWNER, "view_grant", grant).status == "committed"
    with owner_only_books.read() as c:
        panel = reads.pnl(c, "bot:paid-rental-ads", "2026-10-01", "2026-10-31", view="expenses", at=AT)["teammates"]
        mine = [r for r in panel if r["teammate"] == "bot:paid-rental-ads"]
        assert mine and mine[0]["budget"] == 300_000, panel
        assert w["google"]


def test_a_bot_whose_slug_is_not_its_id_is_found_by_its_slug(owner_only_books: Store):
    """A custom (or renamed) bot's slug differs from its id: `bot:<slug>` names it wherever Finance takes a teammate,
    and what is kept is its id."""
    from backend import hubdb as H

    w = world(owner_only_books)
    with owner_only_books.transaction() as c:
        H.rename_bot(c, "cmo", "consumer-example-ad-owner")
        assert reads.view_as(c, OWNER, "bot:consumer-example-ad-owner", AT) == "bot:cmo"
        assert reads.view_as(c, OWNER, "bot:cmo", AT) == "bot:cmo"
        with pytest.raises(FinanceError) as missing:
            reads.view_as(c, OWNER, "bot:no-such-bot", AT)
        assert missing.value.code == NOT_FOUND
        body = {
            "name": "Example tests",
            "teammate": "bot:consumer-example-ad-owner",
            "account_id": w["adv"],
            "parent_id": w["marketing"],
        }
        done = act(c, OWNER, "budget_create", body)
        assert done.status == "committed", done
        kept = c.execute("SELECT teammate FROM fin_budgets WHERE id=?", (done.result["budget_id"],)).fetchone()[0]
        assert kept == "bot:cmo"
        grant = {"subject": "bot:consumer-example-ad-owner", "what": "expenses", "whose": "own"}
        assert act(c, OWNER, "view_grant", grant).status == "committed"
        assert load(c, AT).view("bot:cmo").sees_anything()


def test_a_grant_asked_for_a_bot_by_slug_goes_to_that_bot_even_if_the_slug_changes_hands(books: Store):
    """A bot's grant waits for a person's OK. The subject is fixed to the bot's id when it is asked: a slug renamed and
    handed to another bot before the OK still grants the bot that was asked about."""
    from backend import hubdb as H
    from backend.finance import actions as FA
    from backend.tests.helpers.finance_oks import give

    with books.transaction() as c:
        c.execute("UPDATE bot_config SET reports_to='bot:finance-owner' WHERE bot='coder'")
        H.rename_bot(c, "coder", "example-web-helper")
        grant = {"subject": "bot:example-web-helper", "what": "expenses", "whose": "own"}
        asked = act(c, "bot:finance-owner", "view_grant", grant)
        assert asked.status == "needs_approval", (asked.code, asked.reason)
        stored = c.execute("SELECT payload FROM fin_actions WHERE id=?", (asked.action_id,)).fetchone()[0]
        assert '"subject": "bot:coder"' in stored
        H.rename_bot(c, "coder", "example-coder-two")
        H.rename_bot(c, "seller", "example-web-helper")
        give(c, asked.approver, asked.approval_id)
        done = FA.approval_decided(c, asked.approval_id, AT)
        assert done.status == "committed", (done.code, done.reason)
        ctx = load(c, AT)
        assert ctx.view("bot:coder").grant_ids == (done.result["grant_id"],) and not ctx.view("bot:seller").grant_ids


def test_every_teammate_a_request_names_is_kept_by_bot_id(owner_only_books: Store):
    """What is stored for an approval names bots by id: a teammate anywhere (attribution shares, a budget, a right), a
    card's holder, a teammate allocation's target and a teammate grant's subject; a team or a card is left alone."""
    from backend import hubdb as H
    from backend.finance import actions as FA

    with owner_only_books.transaction() as c:
        H.rename_bot(c, "coder", "example-ads")
        bot = "bot:example-ads"
        teammates = FA._teammates(c, "attribution_rule_set", {"splits": [{"teammate": bot, "share_bps": 10_000}]})
        assert teammates == {"splits": [{"teammate": "bot:coder", "share_bps": 10_000}]}
        assert FA._teammates(c, "card_create", {"holder": bot})["holder"] == "bot:coder"
        allocation = {"target_kind": "teammate", "target_id": bot}
        assert FA._teammates(c, "allocation_set", allocation)["target_id"] == "bot:coder"
        assert FA._teammates(c, "allocation_set", {**allocation, "target_kind": "card"})["target_id"] == bot
        assert FA._teammates(c, "view_grant", {"subject": bot})["subject"] == "bot:coder"
        assert FA._teammates(c, "view_grant", {"subject_kind": "team", "subject": bot})["subject"] == bot
        assert FA._teammates(c, "budget_create", {"teammate": bot, "holder": bot}) == {
            "teammate": "bot:coder",
            "holder": bot,
        }
