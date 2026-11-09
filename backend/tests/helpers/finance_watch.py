"""Test helpers shared with other test files (moved out of backend/tests/test_finance_watch.py so they no longer import it)."""

from backend.finance.store import ledger as SL
from backend.tests.helpers.finance_fixtures import AT, OWNER, account


PRIOR_MONTHS = ("2026-08-05", "2026-09-05", "2026-10-05")


WATCH_AT = "2026-11-01T00:00:00.000000Z"


def _bot_card_charges(c, slug: str, card: str, days: tuple[str, ...], amount: int = 4_900) -> None:
    """A card held by bot `slug`, on a checking account, with one posted card charge on each of `days`: the rows the
    bank feed brings in for a card (fin_feed_txns with card_id)."""
    money = SL.new_id()
    book = SL.book(c)
    c.execute(
        "INSERT INTO fin_money_accounts(id, book_id, kind, name, provider, account_id, created_at, created_by) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (money, book.id, "bank", "Checking", "mercury", account(c, "1000"), AT, OWNER),
    )  # type: ignore[union-attr]
    c.execute(
        "INSERT INTO fin_cards(id, provider, holder, kind, currency, created_at, created_by, money_account_id) "
        "VALUES(?, 'mercury', ?, 'standing', ?, ?, ?, ?)",
        (card, "bot:" + slug, book.base_currency, AT, OWNER, money),
    )  # type: ignore[union-attr]
    for day in days:
        c.execute(
            "INSERT INTO fin_feed_txns(id, money_account_id, provider_id, txn_date, amount, currency, counterparty, "
            "status, created_at, created_by, card_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                SL.new_id(),
                money,
                f"txn-{SL.new_id()[:8]}",
                day,
                -amount,
                book.base_currency,  # type: ignore[union-attr]
                "ACME CLOUD",
                "new",
                AT,
                OWNER,
                card,
            ),
        )
