"""Finance is the owner's alone by default (backend/finance/core/view.py Defaults): a new book shows nothing to a
manager, a member or a bot until the owner grants it or opens the defaults up; existing books move there
(migrations/20261106210000_finance_owner_only_defaults.py); and an ad budget's warning reaches its bot only while the
bot's view shows that budget (ad_warnings.bot_of)."""

import importlib

from backend.finance import tab
from backend.finance.context import load
from backend.finance.store import access as SA
from backend.store import Store
from backend.tests.test_finance_ad_budgets import WARN_AT, platform_spend, warn, warning_notes, world
from backend.tests.test_finance_fixtures import (  # noqa: F401  (fixtures)
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


def test_an_ad_budget_warns_its_bot_only_once_the_bot_may_see_it(owner_only_books: Store):
    w = world(owner_only_books, WARN_AT)
    platform_spend(
        owner_only_books, w["google_ads"], [("2026-10-02", 40_000), ("2026-10-05", 36_000)], "2026-10-01", "2026-10-06"
    )
    assert warn(owner_only_books) == []
    with owner_only_books.transaction() as c:
        assert warning_notes(c) == []
        made = act(
            c, OWNER, "view_grant", {"subject": "bot:paid-rental-ads", "what": "expenses", "whose": "own"}, at=WARN_AT
        )
        assert made.status == "committed"
    assert warn(owner_only_books) == [("bot:paid-rental-ads", ("pace",))]
