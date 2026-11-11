"""Finance is the owner's alone by default (backend/finance/core/view.py Defaults): a new book shows nothing to a
manager, a member or a bot until the owner grants it or opens the defaults up; existing books move there
(migrations/20261106210000_finance_owner_only_defaults.py). A teammate always sees their own budgets
(handlers.reads_budget: Finance's My budget, the bot reads, the warnings) and nothing more."""

import importlib

import pytest

from backend.finance import reads, tab
from backend.finance.core.errors import NOT_FOUND, FinanceError
from backend.finance.context import load
from backend.finance.store import access as SA
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

MIGRATION = importlib.import_module("backend.migrations.20261106210000_finance_owner_only_defaults")


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


def test_existing_books_move_to_owner_only(books: Store):
    with books.transaction() as c:
        before = SA.view_version(c)
        assert SA.defaults(c).member == "own_expenses"
        MIGRATION.up(c)
        d = SA.defaults(c)
        assert (d.manager, d.member) == ("none", "none")
        assert SA.view_version(c) == before + 1
        MIGRATION.up(c)  # again: nothing to move, the version stays
        assert SA.view_version(c) == before + 1
        assert not load(c, AT).view("human:ben").sees_anything()


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
