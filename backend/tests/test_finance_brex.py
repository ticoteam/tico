"""Brex in the books (spec §9): the card and cash accounts, a card charge, refund, chargeback and reward as card rows, a
card purchase on its card's line and expense line, a card payment paired with the bank's debit whichever arrives first
(or the bank side alone), a cash row's sign checked against the balance (agrees, inverted, fits neither way), a book
transfer between two of the company's accounts as one transfer (or noted while it waits for its other side), a repeated
read that changes nothing, a changed row restated, the backfill across passes with its bracketed opening balance (a row
posted mid-backfill, a bracket read again, a bracket that gives up), the same books whatever order Brex lists rows in,
history_from (the catch-up never reads before it), an unknown card type posted with Brex's sign and noted, a mapped card
that stops being live skipped and noted, a row with no amount skipped and noted, the refusals (a token Brex refuses, a
token that is not an admin, a second live card account, a row in another currency), a Brex card read-only in Finance, the
probe's budget, a 429 waited out, a redirect never followed, and the connections route. Runs on an in-memory fake of
Brex's account and transaction reads; nothing is sent anywhere."""

import io
import json
import re
import urllib.error
from collections.abc import Callable
from typing import Any

import pytest

from backend.finance import actions as FA
from backend.finance import brex, brex_sync, cards, sync, tab
from backend.finance.core import feeds as F
from backend.finance.core.errors import FinanceError
from backend.finance.context import load
from backend.finance.mercury import Answer
from backend.finance.store import banking as store_banking
from backend.tests.helpers.api import get, headers  # noqa: F401
from backend.tests.helpers.finance_banking import _txn
from backend.tests.helpers.finance_fixtures import AT, OWNER, FakeMercury, account, act, books, finance_template, store
from backend.tests.helpers.finance_fixtures import connect as connect_mercury
from backend.tests.helpers.finance_fixtures import store_keys

CARD = "card_acc_1"
CASH = "cash_acc_1"
CASH_TWO = "cash_acc_2"
LATER = ("2026-10-15T13:00:00.000000Z", "2026-10-15T14:00:00.000000Z", "2026-10-15T15:00:00.000000Z")


class _Reply:
    """What Brex's opener hands back: a context manager with a body."""

    status = 200

    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> "_Reply":
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False

    def read(self) -> bytes:
        return self._body


def money(amount: int, currency: str = "USD") -> dict[str, Any]:
    return {"amount": amount, "currency": currency}


def card_account(aid: str = CARD, status: str = "ACTIVE") -> dict[str, Any]:
    return {
        "id": aid,
        "status": status,
        "current_balance": money(0),
        "available_balance": money(0),
        "current_statement_period": {},
    }


def cash_account(
    aid: str = CASH,
    number: str = "123456789012",
    status: str = "ACTIVE",
    balance: int = 0,
    currency: str = "USD",
    name: str = "Operating",
) -> dict[str, Any]:
    return {
        "id": aid,
        "name": name,
        "status": status,
        "account_number": number,
        "routing_number": "021000021",
        "primary": aid == CASH,
        "current_balance": money(balance, currency),
        "available_balance": money(balance, currency),
    }


def card_txn(
    tid: str, kind: str, amount: int, day: str = "2026-10-10", merchant: str = "BLUE BOTTLE"
) -> dict[str, Any]:
    return {
        "id": tid,
        "type": kind,
        "amount": money(amount),
        "description": f"{kind} at {merchant}",
        "posted_at_date": day,
        "initiated_at_date": day,
        "card_id": "card_1",
        "merchant": {"raw_descriptor": merchant},
    }


def cash_txn(tid: str, kind: str, amount: int, day: str = "2026-10-10", currency: str = "USD") -> dict[str, Any]:
    return {
        "id": tid,
        "type": kind,
        "amount": money(amount, currency),
        "description": kind,
        "posted_at_date": day,
        "initiated_at_date": day,
    }


def _card_amount(raw: dict[str, Any]) -> int:
    """A card row as the card line carries it; 0 for one Finance does not post (no amount, another currency)."""
    try:
        return F.brex_card_txn(raw, CARD).amount if raw["amount"]["currency"] == "USD" else 0
    except (FinanceError, KeyError, TypeError):
        return 0


# The posted_at_start Brex takes: a date-time with no zone. It answers 400 to a `Z`, an offset or a bare date.
POSTED_AT_START = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
BAD_REQUEST = Answer(400, {"type": "BAD_REQUEST", "message": "Bad request"})


class FakeBrex:
    """Brex's account and transaction reads in memory, in the shapes the real API answers: the card accounts as one bare
    array (no page), the cash accounts and transactions as pages. Transactions are added oldest first and listed newest
    first, a `page` at a time, the cursor the index of the next row; posted_at_start keeps the rows posted on or after
    its day, as Brex's filter does, and a zoned one is a 400. `status`, when set, answers every call with that HTTP
    status and no data."""

    def __init__(self, page: int = 100) -> None:
        self.cards: list[dict[str, Any]] = [card_account()]
        self.cash: list[dict[str, Any]] = [cash_account()]
        self.card_txns: list[dict[str, Any]] = []
        self.cash_txns: dict[str, list[dict[str, Any]]] = {CASH: []}
        self.page = page
        self.card_owed = 0  # owed on the card before its rows here (history before history_from)
        self.status: int | None = None
        self.order = "newest_first"  # the order Brex lists rows in (ORDERS)
        self.calls = 0  # cash account-list reads so far
        self.hook_at: dict[
            int, Callable[[], None]
        ] = {}  # run before the nth cash account-list read (a row posted then)

    def card_accounts(self, cursor: str | None = None) -> Answer:
        """Each card account owing `card_owed` from before its rows here plus what its rows charged, as the positive
        current balance Brex reports."""
        if self.status is not None:
            return Answer(self.status, {"message": "nope"})
        owed = self.card_owed - sum(_card_amount(r) for r in self.card_txns)
        cards = [{**a, "current_balance": money(owed)} for a in self.cards]
        return Answer(200, brex._parse(json.dumps(cards).encode()))

    def cash_accounts(self, cursor: str | None = None) -> Answer:
        self.calls += 1
        if (hook := self.hook_at.get(self.calls)) is not None:
            hook()
        return self._answer(self.cash, cursor)

    def card_transactions(self, posted_at_start: str, cursor: str | None = None) -> Answer:
        if not POSTED_AT_START.fullmatch(posted_at_start):
            return BAD_REQUEST
        return self._answer(_since(self.card_txns, posted_at_start, self.order), cursor)

    def cash_transactions(self, account_id: str, posted_at_start: str, cursor: str | None = None) -> Answer:
        if not POSTED_AT_START.fullmatch(posted_at_start):
            return BAD_REQUEST
        return self._answer(_since(self.cash_txns.get(account_id, []), posted_at_start, self.order), cursor)

    def me(self) -> Answer:
        if self.status is not None:
            return Answer(self.status, {"message": "nope"})
        return Answer(200, {"id": "user_1"})

    def _answer(self, rows: list[dict[str, Any]], cursor: str | None) -> Answer:
        if self.status is not None:
            return Answer(self.status, {"message": "nope"})
        at = int(cursor or 0)
        page = rows[at : at + self.page]
        more = at + len(page) < len(rows)
        return Answer(200, {"items": page, "next_cursor": str(at + len(page)) if more else None})


ORDERS: dict[str, Callable[[list[dict[str, Any]]], list[dict[str, Any]]]] = {
    "newest_first": lambda rows: list(reversed(rows)),  # Brex's usual listing
    "oldest_first": lambda rows: list(rows),
    "rotated": lambda rows: rows[len(rows) // 2 :] + rows[: len(rows) // 2],  # neither end first
}


def _since(rows: list[dict[str, Any]], start: str, order: str = "newest_first") -> list[dict[str, Any]]:
    """The rows posted on or after the day `start` names, listed in the order `order` names."""
    return [r for r in ORDERS[order](rows) if str(r["posted_at_date"]) >= start[:10]]


def connect_brex(
    store: Any, fake: FakeBrex, credential: str = "cred-brex", history_from: str | None = None, verified: bool = True
) -> str:
    """Connect the fake's company. `verified` (the default) marks its cash sign as checked, so the cash rows post as the
    tests read them; the sign tests pass False and let the balance check the sign."""
    found, why = brex_sync.probe(fake)  # type: ignore[arg-type]
    assert not why, why
    payload: dict[str, object] = {"provider": "brex", "credential_id": credential, "probe": found}
    if history_from:
        payload["history_from"] = history_from
    with store.transaction() as c:
        done = act(c, OWNER, "connection_create", payload)
    assert done.status == "committed", done
    cid = str(done.result["connection_id"])
    if verified:
        with store.transaction() as c:
            settings = json.loads(c.execute("SELECT settings FROM fin_connections WHERE id=?", (cid,)).fetchone()[0])
            c.execute(
                "UPDATE fin_connections SET settings=? WHERE id=?", (json.dumps({**settings, "sign": "verified"}), cid)
            )
    return cid


def brex_pass(store: Any, fake: FakeBrex, at: str = AT) -> str:
    """One pass of the bank worker over the Brex connections, with `fake` as Brex."""
    with store.read() as c:
        ids = [str(r[0]) for r in c.execute("SELECT id FROM fin_connections WHERE provider='brex'")]
    out = ""
    for cid in ids:
        out = sync.connection(store, None, cid, make=lambda _t: fake, at=at)  # type: ignore[arg-type]
    return out


def _add(fake: FakeBrex, side: tuple[str, dict[str, Any]]) -> None:
    kind, row = side
    (fake.card_txns if kind == "card" else fake.cash_txns[CASH]).append(row)


def _feed(c: Any, provider_id: str) -> Any:
    return c.execute("SELECT * FROM fin_feed_txns WHERE provider_id=?", (provider_id,)).fetchone()


def _count(c: Any, sql: str) -> int:
    return int(c.execute(sql).fetchone()[0])


def _sum(c: Any, code: str) -> int:
    return int(
        c.execute(
            "SELECT COALESCE(SUM(l.amount),0) FROM fin_lines l JOIN fin_accounts a ON a.id=l.account_id WHERE a.code=?",
            (code,),
        ).fetchone()[0]
    )


def _every_entry_balances(c: Any) -> bool:
    return int(c.execute("SELECT COALESCE(SUM(amount),0) FROM fin_lines").fetchone()[0]) == 0


def _code(c: Any, kind: str) -> str:
    return str(
        c.execute(
            "SELECT a.code FROM fin_money_accounts m JOIN fin_accounts a ON a.id=m.account_id "
            "WHERE m.provider='brex' AND m.kind=? ORDER BY m.created_at LIMIT 1",
            (kind,),
        ).fetchone()[0]
    )


def _card_line_is_its_posted_rows(c: Any) -> bool:
    """A money account's GL line carries each posted feed row's amount: the card's line is the sum of its posted rows."""
    money = c.execute("SELECT id FROM fin_money_accounts WHERE provider='brex' AND kind='card_program'").fetchone()
    posted = int(
        c.execute(
            "SELECT COALESCE(SUM(amount),0) FROM fin_feed_txns WHERE money_account_id=? AND entry_id IS NOT NULL",
            (str(money[0]),),
        ).fetchone()[0]
    )
    return _sum(c, _code(c, "card_program")) == posted


def _bank_ties(c: Any) -> bool:
    """The Brex bank line is the balance Brex reports less the rows not yet posted: the opening carries the rest."""
    money = c.execute(
        "SELECT id, balance FROM fin_money_accounts WHERE provider='brex' AND kind='bank' ORDER BY created_at LIMIT 1"
    ).fetchone()
    unposted = int(
        c.execute(
            "SELECT COALESCE(SUM(amount),0) FROM fin_feed_txns WHERE money_account_id=? AND entry_id IS NULL",
            (str(money[0]),),
        ).fetchone()[0]
    )
    return _sum(c, _code(c, "bank")) == int(money[1]) - unposted


def _settings(c: Any) -> dict[str, Any]:
    return json.loads(c.execute("SELECT settings FROM fin_connections WHERE provider='brex'").fetchone()[0])


def _last_error(c: Any) -> tuple[str, str]:
    row = c.execute("SELECT status, last_error FROM fin_connections WHERE provider='brex'").fetchone()
    return str(row[0]), str(row[1] or "")


def test_the_probe_lists_the_live_accounts_and_keeps_only_the_last_four_digits():
    fake = FakeBrex()
    fake.cash.append(cash_account(CASH_TWO, number="987654321098", status="CLOSED"))
    found, why = brex_sync.probe(fake)  # type: ignore[arg-type]
    assert not why, why
    assert [r["id"] for r in found["card"]] == [CARD]
    assert [r["id"] for r in found["cash"]] == [CASH]
    assert found["cash"][0]["last4"] == "9012"
    assert "123456789012" not in json.dumps(found) and "987654321098" not in json.dumps(found)


def test_a_second_live_card_account_is_refused_at_the_probe():
    fake = FakeBrex()
    fake.cards.append(card_account("card_acc_2"))
    _found, why = brex_sync.probe(fake)  # type: ignore[arg-type]
    assert "card accounts" in why


def test_a_token_brex_refuses_is_refused_at_the_probe_and_fails_the_pass(books):
    fake = FakeBrex()
    fake.status = 401
    _found, why = brex_sync.probe(fake)  # type: ignore[arg-type]
    assert "refused the token" in why
    fake.status = None
    connect_brex(books, fake)
    fake.status = 401
    assert brex_pass(books, fake) == "failed"
    with books.read() as c:
        status, error = _last_error(c)
    assert status == "error" and "refused the token" in error


@pytest.mark.parametrize("sign", [-1, 1])
def test_a_card_purchase_is_a_charge_on_the_card_whichever_sign_brex_sends(books, sign):
    fake = FakeBrex()
    connect_brex(books, fake)
    fake.card_txns.append(card_txn("ctx_1", "PURCHASE", sign * 1_200))
    assert brex_pass(books, fake) == "ok"
    with books.read() as c:
        row = _feed(c, "ctx_1")
        assert (int(row["amount"]), row["kind"], row["counterparty"]) == (-1_200, "PURCHASE", "BLUE BOTTLE")
        assert _card_line_is_its_posted_rows(c)
        assert _every_entry_balances(c)


def test_a_refund_a_chargeback_and_a_reward_are_money_back_on_the_card(books):
    fake = FakeBrex()
    connect_brex(books, fake)
    fake.card_txns += [
        card_txn("ctx_refund", "REFUND", -300),
        card_txn("ctx_cb", "CHARGEBACK", 200),
        card_txn("ctx_rew", "REWARDS_CREDIT", 50),
    ]
    assert brex_pass(books, fake) == "ok"
    with books.read() as c:
        assert [int(_feed(c, p)["amount"]) for p in ("ctx_refund", "ctx_cb", "ctx_rew")] == [300, 200, 50]
        assert _card_line_is_its_posted_rows(c)
        assert _every_entry_balances(c)


def test_an_unknown_card_type_is_posted_with_brex_sign_and_noted_and_the_sync_goes_on(books):
    fake = FakeBrex()
    connect_brex(books, fake)
    fake.card_txns += [
        card_txn("ctx_odd", "MYSTERY_FEE", -99),
        card_txn("ctx_odd_in", "MYSTERY_FEE", 500, day="2026-10-11"),
    ]
    fake.cash_txns[CASH].append(cash_txn("cash_after", "PAYMENT", -300))
    assert brex_pass(books, fake) == "ok"
    with books.read() as c:
        odd, odd_in = _feed(c, "ctx_odd"), _feed(c, "ctx_odd_in")
        assert (int(odd["amount"]), odd["kind"]) == (-99, F.BREX_OTHER)
        assert (int(odd_in["amount"]), odd_in["kind"]) == (500, F.BREX_OTHER)
        assert _feed(c, "cash_after")["entry_id"] is not None
        assert _every_entry_balances(c)
        status, error = _last_error(c)
    assert status == "active" and "MYSTERY_FEE" in error and "ctx_odd" in error
    assert brex_pass(books, fake, LATER[0]) == "ok"  # the note is kept on the next pass too
    with books.read() as c:
        assert _last_error(c) == ("active", error)


def test_a_mapped_card_that_stops_being_live_is_skipped_and_noted_while_the_sync_goes_on(books):
    fake = FakeBrex()
    connect_brex(books, fake)
    assert brex_pass(books, fake) == "ok"
    fake.cards[0]["status"] = "CLOSED"
    fake.card_txns.append(card_txn("ctx_late", "PURCHASE", -800, day="2026-10-14"))
    fake.cash_txns[CASH].append(cash_txn("cash_kept", "PAYMENT", -300, day="2026-10-14"))
    assert brex_pass(books, fake, LATER[0]) == "ok"
    with books.read() as c:
        status, error = _last_error(c)
        assert _feed(c, "ctx_late") is None
        assert _feed(c, "cash_kept")["entry_id"] is not None
        assert _count(c, "SELECT COUNT(*) FROM fin_money_accounts WHERE provider='brex' AND kind='card_program'") == 1
        assert _every_entry_balances(c)
    assert status == "active" and CARD in error and "no longer live" in error


@pytest.mark.parametrize("bank_first", [True, False])
def test_a_card_payment_is_one_transfer_from_the_bank_to_the_card_whichever_row_arrives_first(books, bank_first):
    fake = FakeBrex()
    connect_brex(books, fake)
    assert brex_pass(books, fake, AT) == "ok"
    bank = ("cash", cash_txn("cash_pay", "CARD_COLLECTION", -50_000))
    card = ("card", card_txn("ctx_pay", "COLLECTION", 50_000))
    first, second = (bank, card) if bank_first else (card, bank)
    _add(fake, first)
    assert brex_pass(books, fake, LATER[0]) == "ok"
    _add(fake, second)
    assert brex_pass(books, fake, LATER[1]) == "ok"
    with books.read() as c:
        assert _feed(c, "cash_pay")["entry_id"] is not None and _feed(c, "ctx_pay")["entry_id"] is not None
        assert _sum(c, _code(c, "card_program")) == 50_000
        assert _sum(c, _code(c, "bank")) == -50_000
        assert _sum(c, "6900") == 0  # no expense for the payment: it moved money between the two lines
        assert _every_entry_balances(c)


def test_a_cash_row_keeps_the_sign_brex_sends(books):
    fake = FakeBrex()
    connect_brex(books, fake)
    fake.cash_txns[CASH] += [cash_txn("cash_out", "PAYMENT", -2_500), cash_txn("cash_in", "PAYMENT", 7_000)]
    assert brex_pass(books, fake) == "ok"
    with books.read() as c:
        assert int(_feed(c, "cash_out")["amount"]) == -2_500
        assert int(_feed(c, "cash_in")["amount"]) == 7_000
        assert _every_entry_balances(c)


def test_a_repeated_read_changes_nothing_and_a_changed_transaction_restates_its_row(books):
    fake = FakeBrex()
    connect_brex(books, fake)
    fake.card_txns.append(card_txn("ctx_1", "PURCHASE", -1_200))
    fake.cash_txns[CASH].append(cash_txn("cash_1", "PAYMENT", -4_000))
    assert brex_pass(books, fake) == "ok"
    with books.read() as c:
        before = (
            _count(c, "SELECT COUNT(*) FROM fin_feed_txns"),
            _count(c, "SELECT COUNT(*) FROM fin_entries"),
            _sum(c, _code(c, "card_program")),
        )
    for at in LATER[:2]:
        assert brex_pass(books, fake, at) == "ok"
    with books.read() as c:
        after = (
            _count(c, "SELECT COUNT(*) FROM fin_feed_txns"),
            _count(c, "SELECT COUNT(*) FROM fin_entries"),
            _sum(c, _code(c, "card_program")),
        )
    assert after == before
    fake.card_txns[0]["amount"] = money(-1_500)
    assert brex_pass(books, fake, LATER[2]) == "ok"
    with books.read() as c:
        row = _feed(c, "ctx_1")
        assert (int(row["amount"]), int(row["version"])) == (-1_500, 1)
        assert _card_line_is_its_posted_rows(c)
        assert _every_entry_balances(c)


def test_the_backfill_walks_pages_across_passes_and_the_opening_carries_every_row(books, monkeypatch):
    monkeypatch.setattr(brex_sync, "BACKFILL_PAGES", 1)
    fake = FakeBrex(page=2)
    amounts = [-1_000, 2_000, -3_000, 4_000, -5_000]
    waiting = sum(a for a in amounts if a > 0)  # the credits wait for a category, so the bank line is short by them
    fake.cash_txns[CASH] = [cash_txn(f"cash_{i}", "PAYMENT", amt) for i, amt in enumerate(amounts)]
    fake.cash[0]["current_balance"] = money(100_000 + sum(amounts))  # the balance now: the opening plus every row
    fake.cash[0]["available_balance"] = money(100_000 + sum(amounts))
    connect_brex(books, fake)
    assert brex_pass(books, fake) == "ok"
    with books.read() as c:
        assert _settings(c)["backfilled"] is False  # two of its three pages are still to read
    for at in LATER:
        brex_pass(books, fake, at)
        with books.read() as c:
            if _settings(c)["backfilled"]:
                break
    with books.read() as c:
        settings = _settings(c)
        assert settings["backfilled"] is True and "cursors" not in settings and "walked" not in settings
        assert _count(c, "SELECT COUNT(*) FROM fin_feed_txns") == len(amounts)
        # The opening is the balance less every row the balance holds; the debits post, the credits wait for a category.
        assert _count(c, "SELECT COUNT(*) FROM fin_feed_txns WHERE amount > 0 AND entry_id IS NULL") == 2
        assert _sum(c, _code(c, "bank")) == 100_000 + sum(amounts) - waiting
        money_id = str(
            c.execute("SELECT id FROM fin_money_accounts WHERE provider='brex' AND kind='bank'").fetchone()[0]
        )
        assert store_banking.opening_entry(c, load(c, AT).book.id, money_id) is not None
        assert _every_entry_balances(c)
    assert brex_pass(books, fake, "2026-10-15T16:00:00.000000Z") == "ok"
    with books.read() as c:
        assert _sum(c, _code(c, "bank")) == 100_000 + sum(amounts) - waiting
        assert _count(c, "SELECT COUNT(*) FROM fin_feed_txns") == len(amounts)


def test_a_row_posted_mid_backfill_is_left_out_of_the_opening_and_comes_in_with_the_catch_up(books, monkeypatch):
    monkeypatch.setattr(brex_sync, "BACKFILL_PAGES", 1)
    fake = FakeBrex(page=2)
    amounts = [-1_000, -2_000, -3_000, -4_000, -5_000]
    fake.cash_txns[CASH] = [cash_txn(f"cash_{i}", "PAYMENT", amt) for i, amt in enumerate(amounts)]
    fake.cash[0]["current_balance"] = money(100_000 + sum(amounts))
    fake.cash[0]["available_balance"] = money(100_000 + sum(amounts))
    connect_brex(books, fake)
    assert brex_pass(books, fake) == "ok"  # the bracket: the balance and the newest page, read together
    fake.cash_txns[CASH].append(cash_txn("cash_late", "PAYMENT", -7_000, day="2026-10-14"))  # posted mid-backfill
    fake.cash[0]["current_balance"] = money(100_000 + sum(amounts) - 7_000)
    fake.cash[0]["available_balance"] = money(100_000 + sum(amounts) - 7_000)
    for at in LATER[:2]:
        assert brex_pass(books, fake, at) == "ok"
    with books.read() as c:
        assert _settings(c)["backfilled"] is True
        assert _feed(c, "cash_late") is None  # newer than the bracket: the backfill's pages never reach it
        assert _sum(c, _code(c, "bank")) == 100_000 + sum(
            amounts
        )  # the opening is the bracketed balance, not a later one
    assert brex_pass(books, fake, LATER[2]) == "ok"  # the catch-up takes it in
    with books.read() as c:
        assert _feed(c, "cash_late")["entry_id"] is not None
        assert _sum(c, _code(c, "bank")) == 100_000 + sum(amounts) - 7_000
        assert _every_entry_balances(c)


def test_the_bracket_reads_again_when_a_row_comes_in_between_its_two_reads(books):
    fake = FakeBrex()
    fake.cash_txns[CASH] = [cash_txn("cash_0", "PAYMENT", -1_000)]
    fake.cash[0]["current_balance"] = money(10_000)
    fake.cash[0]["available_balance"] = money(10_000)
    connect_brex(books, fake)

    def move() -> None:
        """A row posted, and the balance moved with it, after the page was read and before the balance was."""
        fake.cash_txns[CASH].append(cash_txn("cash_late", "PAYMENT", -2_000))
        fake.cash[0]["current_balance"] = money(7_000)
        fake.cash[0]["available_balance"] = money(7_000)

    fake.calls = 0
    fake.hook_at = {2: move}  # this pass reads the account list in balances (1), then the bracket's balance (2)
    assert brex_pass(books, fake) == "ok"
    with books.read() as c:
        assert _settings(c)["backfilled"] is True
        assert _feed(c, "cash_late") is not None and _feed(c, "cash_0") is not None
        assert (
            _sum(c, _code(c, "bank")) == 7_000
        )  # the opening is the second read's balance: 10,000 less the 3,000 rows
        assert _every_entry_balances(c)


def test_a_bracket_whose_newest_row_keeps_moving_takes_nothing_and_the_next_step_reads_again(books):
    fake = FakeBrex()
    fake.cash_txns[CASH] = [cash_txn("cash_0", "PAYMENT", -1_000)]
    connect_brex(books, fake)
    posted = [0]

    def post() -> None:
        posted[0] += 1
        fake.cash_txns[CASH].append(cash_txn(f"cash_post{posted[0]}", "PAYMENT", -10))

    fake.calls = 0
    fake.hook_at = {2: post, 3: post, 4: post}  # a row posted after each try's page read, before its check
    assert brex_pass(books, fake) == "failed"
    with books.read() as c:
        status, error = _last_error(c)
        assert _count(c, "SELECT COUNT(*) FROM fin_feed_txns") == 0
        assert _settings(c)["backfilled"] is False
    assert status == "error" and "balance changed while it was read" in error


def test_a_book_transfer_between_two_brex_cash_accounts_is_one_transfer_not_an_expense(books):
    fake = FakeBrex()
    fake.cash = [cash_account(CASH), cash_account(CASH_TWO, number="210987654321", name="Savings")]
    fake.cash_txns[CASH_TWO] = []
    connect_brex(books, fake)
    assert brex_pass(books, fake) == "ok"
    fake.cash_txns[CASH].append(cash_txn("book_out", "BOOK_TRANSFER", -5_000, day="2026-10-14"))
    fake.cash_txns[CASH_TWO].append(cash_txn("book_in", "BOOK_TRANSFER", 5_000, day="2026-10-14"))
    assert brex_pass(books, fake, LATER[0]) == "ok"
    with books.read() as c:
        assert _feed(c, "book_out")["entry_id"] is not None and _feed(c, "book_in")["entry_id"] is not None
        assert _sum(c, "6900") == 0  # no expense: the transfer moved money between the two accounts
        assert _every_entry_balances(c)


def test_history_from_is_the_first_day_read(books):
    fake = FakeBrex()
    fake.card_txns += [
        card_txn("ctx_old", "PURCHASE", -100, day="2026-10-01"),
        card_txn("ctx_new", "PURCHASE", -200, day="2026-10-06"),
    ]
    connect_brex(books, fake, history_from="2026-10-05")
    assert brex_pass(books, fake) == "ok"
    with books.read() as c:
        assert _feed(c, "ctx_old") is None and _feed(c, "ctx_new") is not None
        assert _settings(c)["history_from"] == "2026-10-05"
    assert brex_pass(books, fake, LATER[0]) == "ok"  # the catch-up's 30 days reach back past this day: not read
    with books.read() as c:
        assert _feed(c, "ctx_old") is None


def test_history_starts_on_the_first_of_this_year_when_none_is_given(books):
    connect_brex(books, FakeBrex())
    with books.read() as c:
        assert _settings(c)["history_from"] == "2026-01-01"


def test_a_row_in_another_currency_is_not_posted_and_its_balance_is_not_kept(books):
    fake = FakeBrex()
    fake.cash = [cash_account(CASH, balance=5_000, currency="EUR")]
    fake.cash_txns[CASH] = [cash_txn("cash_eur", "PAYMENT", -700, currency="EUR")]
    connect_brex(books, fake)
    assert brex_pass(books, fake) == "ok"
    with books.read() as c:
        assert _feed(c, "cash_eur") is None
        balance = c.execute("SELECT balance FROM fin_money_accounts WHERE provider='brex' AND kind='bank'").fetchone()[
            0
        ]
        assert balance is None
        status, error = _last_error(c)
    assert status == "active" and "EUR" in error


def test_a_card_account_that_is_not_the_mapped_one_stops_the_sync(books):
    fake = FakeBrex()
    connect_brex(books, fake)
    fake.cards[0]["id"] = "card_acc_9"
    assert brex_pass(books, fake) == "failed"
    with books.read() as c:
        status, error = _last_error(c)
    assert status == "error" and "not the one Finance maps" in error


def test_the_same_credential_is_not_connected_twice(books):
    fake = FakeBrex()
    connect_brex(books, fake)
    found, why = brex_sync.probe(fake)  # type: ignore[arg-type]
    assert not why, why
    with books.transaction() as c:
        done = act(c, OWNER, "connection_create", {"provider": "brex", "credential_id": "cred-brex", "probe": found})
    assert done.status != "committed"


def test_a_429_is_waited_out_and_retried_and_a_redirect_is_never_followed(monkeypatch):
    waits: list[float] = []
    calls: list[str] = []

    def throttled(request: Any, timeout: float) -> _Reply:
        calls.append(request.full_url)
        if len(calls) == 1:
            raise urllib.error.HTTPError(request.full_url, 429, "slow down", {"Retry-After": "3"}, io.BytesIO(b""))
        return _Reply(b'{"items": [{"id": "card_acc_1"}], "next_cursor": null}')

    client = brex.Brex(
        "tok",
        base="https://api.brex.com",
        opener=throttled,  # type: ignore[arg-type]
        sleep=waits.append,
    )
    got = client.card_accounts()
    assert got.status == 200 and brex.listed(got)[0]["id"] == "card_acc_1"
    assert waits == [3.0] and len(calls) == 2

    redirects: list[str] = []

    def redirected(request: Any, timeout: float) -> _Reply:
        redirects.append(request.full_url)
        raise urllib.error.HTTPError(
            request.full_url, 302, "found", {"Location": "https://evil.example/x"}, io.BytesIO(b"")
        )

    client = brex.Brex(
        "tok",
        base="https://api.brex.com",
        opener=redirected,  # type: ignore[arg-type]
        sleep=waits.append,
    )
    got = client.cash_accounts()
    assert got.status == 302 and redirects == ["https://api.brex.com/v2/accounts/cash?limit=100"]
    assert "redirect" in brex.error_text(got)
    assert brex._NoRedirect().redirect_request(None, None, 302, "Found", {}, "https://evil.example/x") is None  # type: ignore[arg-type]


def test_the_base_url_is_brex_s_own_unless_a_local_host_is_named(monkeypatch):
    monkeypatch.delenv(brex.BASE_ENV, raising=False)
    assert brex.base_url() == brex.BASE
    monkeypatch.setenv(brex.BASE_ENV, "https://evil.example")
    assert brex.base_url() == brex.BASE
    monkeypatch.setenv(brex.BASE_ENV, "http://127.0.0.1:9999")
    assert brex.base_url() == "http://127.0.0.1:9999"


def test_the_connections_route_takes_a_brex_token_through_the_brex_probe(api, monkeypatch):  # noqa: F811
    store = api.app.state.store
    with store.transaction() as c:
        assert FA.ensure_setup(c, store.settings.owner_email)
        store_keys(c, "cred-brex")
    probed: list[str] = []

    def brex_probe(_store: Any, _vault: Any, credential_id: str) -> tuple[dict[str, object], str]:
        probed.append(credential_id)
        return {
            "card": [{"id": CARD, "name": "Brex card", "last4": ""}],
            "cash": [
                {"id": CASH, "name": "Operating", "last4": "9012", "current": 0, "available": 0, "currency": "USD"}
            ],
        }, ""

    def no_mercury(*_args: Any) -> tuple[dict[str, object], str]:
        pytest.fail("a Brex token was tested with Mercury's probe")

    monkeypatch.setattr(FA, "brex_probe", brex_probe)
    monkeypatch.setattr(FA, "mercury_probe", no_mercury)
    res = api.post(
        "/api/v2/finance/connections",
        json={"provider": "brex", "credential_id": "cred-brex", "history_from": "2026-03-01"},
        headers=headers(),
    )
    assert res.status_code == 200, res.text
    assert probed == ["cred-brex"]
    assert res.json()["status"] == "committed", res.json()
    assert sorted(m["kind"] for m in res.json()["result"]["money_accounts"]) == ["bank", "card_program"]
    with store.read() as c:
        assert (
            json.loads(c.execute("SELECT settings FROM fin_connections WHERE provider='brex'").fetchone()[0])[
                "history_from"
            ]
            == "2026-03-01"
        )


def test_the_connect_probe_has_a_budget_and_does_not_wait_out_a_long_retry_after():
    waits: list[float] = []

    def throttled(request: Any, timeout: float) -> _Reply:
        raise urllib.error.HTTPError(request.full_url, 429, "slow down", {"Retry-After": "25"}, io.BytesIO(b""))

    client = brex.Brex(
        "tok",
        base="https://api.brex.com",
        opener=throttled,  # type: ignore[arg-type]
        sleep=waits.append,
        budget=brex_sync.PROBE_SECONDS,
    )
    found, why = brex_sync.probe(client)
    assert not found and "Brex said 429" in why
    assert waits == []


def test_a_card_purchase_posts_to_the_card_liability_and_its_expense_line(books):
    fake = FakeBrex()
    connect_brex(books, fake)
    fake.card_txns.append(card_txn("ctx_buy", "PURCHASE", -1_200))
    assert brex_pass(books, fake) == "ok"
    with books.read() as c:
        assert tuple(c.execute("SELECT provider, holder, provider_card_id, status FROM fin_cards").fetchone()) == (
            "brex",
            "",
            "card_1",
            "active",
        )
        assert _feed(c, "ctx_buy")["entry_id"] is not None
        assert _sum(c, _code(c, "card_program")) == -1_200
        assert _sum(c, "6900") == 1_200
        assert _every_entry_balances(c)


def test_a_brex_card_is_read_only_in_finance(books):
    fake = FakeBrex()
    connect_brex(books, fake)
    fake.card_txns.append(card_txn("ctx_buy", "PURCHASE", -1_200))
    assert brex_pass(books, fake) == "ok"
    with books.read() as c:
        card_id = str(c.execute("SELECT id FROM fin_cards WHERE provider='brex'").fetchone()[0])
        with pytest.raises(FinanceError, match="read-only"):
            cards._card(load(c, AT), OWNER, card_id)


def test_a_card_payment_seen_only_on_the_bank_side_goes_to_the_card_and_the_card_side_joins_it(books):
    fake = FakeBrex()
    connect_brex(books, fake)
    assert brex_pass(books, fake, AT) == "ok"
    fake.cash_txns[CASH].append(cash_txn("cash_pay", "CARD_COLLECTION", -50_000))
    fake.cash[0]["current_balance"] = money(-50_000)  # the balance Brex now reports
    fake.cash[0]["available_balance"] = money(-50_000)
    assert brex_pass(books, fake, LATER[0]) == "ok"
    with books.read() as c:
        bank_entry = _feed(c, "cash_pay")["entry_id"]
        assert bank_entry is not None
        assert _sum(c, _code(c, "card_program")) == 50_000
        assert _sum(c, _code(c, "bank")) == -50_000
        assert _sum(c, "6900") == 0  # a payment is never an expense, even with its card side unseen
    fake.card_txns.append(card_txn("ctx_pay", "COLLECTION", 50_000))
    assert brex_pass(books, fake, LATER[1]) == "ok"
    with books.read() as c:
        assert _feed(c, "ctx_pay")["entry_id"] == bank_entry  # the card side joins that entry: no second transfer
        assert _sum(c, _code(c, "card_program")) == 50_000
        assert _sum(c, "6900") == 0
        assert _every_entry_balances(c)


def test_a_token_that_cannot_see_card_payments_posts_them_alone_and_is_noted_not_refused(books):
    fake = FakeBrex()
    connect_brex(books, fake)  # the probe checks the token only: nothing refuses it here
    assert brex_pass(books, fake, AT) == "ok"
    fake.cash_txns[CASH].append(cash_txn("cash_pay", "CARD_COLLECTION", -50_000))
    fake.cash[0]["current_balance"] = money(-50_000)
    fake.cash[0]["available_balance"] = money(-50_000)
    assert brex_pass(books, fake, LATER[0]) == "ok"  # the card side lists no COLLECTION: still a pass that ran
    with books.read() as c:
        assert _feed(c, "cash_pay")["entry_id"] is not None
        assert _sum(c, _code(c, "card_program")) == 50_000
        assert _sum(c, "6900") == 0
        status, error = _last_error(c)
        assert status == "active" and "Card payments aren't visible to this token" in error
        assert _settings(c)["card_payments_hidden"] is True
    fake.card_txns.append(card_txn("ctx_pay", "COLLECTION", 50_000))  # the card side shows it after all
    assert brex_pass(books, fake, LATER[1]) == "ok"
    with books.read() as c:
        assert _feed(c, "ctx_pay")["entry_id"] == _feed(c, "cash_pay")["entry_id"]
        assert "Card payments aren't visible" not in _last_error(c)[1]
        assert _settings(c)["card_payments_hidden"] is False
        assert _sum(c, "6900") == 0
        assert _every_entry_balances(c)


@pytest.mark.parametrize("order", sorted(ORDERS))
def test_the_books_are_the_same_whatever_order_brex_lists_the_rows_in(books, monkeypatch, order):
    monkeypatch.setattr(brex_sync, "BACKFILL_PAGES", 1)
    fake = FakeBrex(page=2)
    fake.order = order
    amounts = [-1_000, 2_000, -3_000, 4_000, -5_000]
    fake.cash_txns[CASH] = [cash_txn(f"cash_{i}", "PAYMENT", amt) for i, amt in enumerate(amounts)]
    fake.cash[0]["current_balance"] = money(100_000 + sum(amounts))
    fake.cash[0]["available_balance"] = money(100_000 + sum(amounts))
    connect_brex(books, fake)
    assert brex_pass(books, fake) == "ok"
    for at in LATER:
        brex_pass(books, fake, at)
        with books.read() as c:
            if _settings(c)["backfilled"]:
                break
    waiting = sum(a for a in amounts if a > 0)  # the credits wait for a category, as in the backfill test
    with books.read() as c:
        assert _settings(c)["backfilled"] is True
        assert _count(c, "SELECT COUNT(*) FROM fin_feed_txns") == len(amounts)
        assert _sum(c, _code(c, "bank")) == 100_000 + sum(amounts) - waiting
        assert _every_entry_balances(c)


def test_a_row_with_no_amount_is_skipped_and_noted_while_the_page_and_the_other_account_go_on(books):
    fake = FakeBrex()
    fake.cash = [cash_account(CASH), cash_account(CASH_TWO, number="210987654321", name="Savings")]
    fake.cash_txns[CASH] = [
        cash_txn("cash_a", "PAYMENT", -300),
        {
            "id": "cash_null",
            "type": "PAYMENT",
            "amount": None,
            "description": "PAYMENT",
            "posted_at_date": "2026-10-10",
        },
        cash_txn("cash_b", "PAYMENT", -200, day="2026-10-11"),
    ]
    fake.cash_txns[CASH_TWO] = [cash_txn("two_1", "PAYMENT", -400)]
    connect_brex(books, fake)
    assert brex_pass(books, fake) == "ok"
    with books.read() as c:
        assert _feed(c, "cash_null") is None
        assert all(_feed(c, p)["entry_id"] is not None for p in ("cash_a", "cash_b", "two_1"))
        status, error = _last_error(c)
        assert _every_entry_balances(c)
    assert status == "active" and "no amount" in error and "cash_null" in error


def test_a_cash_sign_that_agrees_with_the_balance_is_verified_and_the_rows_post(books):
    fake = FakeBrex()
    connect_brex(books, fake, verified=False)
    assert brex_pass(books, fake, AT) == "ok"  # the backfill: no rows, a balance of 0
    assert brex_pass(books, fake, LATER[0]) == "ok"  # a stable read: the balance is the anchor
    fake.cash_txns[CASH] += [cash_txn("c_out", "PAYMENT", -2_500), cash_txn("c_in", "PAYMENT", 7_000)]
    fake.cash[0]["current_balance"] = money(4_500)  # the rows as Brex signs them: 0 - 2,500 + 7,000
    fake.cash[0]["available_balance"] = money(4_500)
    assert brex_pass(books, fake, LATER[1]) == "ok"
    with books.read() as c:
        settings = _settings(c)
        assert (settings["sign"], settings["flipped"]) == ("verified", False)
        assert "sign_anchor" not in settings and "sign_pending" not in settings
        assert int(_feed(c, "c_out")["amount"]) == -2_500 and _feed(c, "c_out")["entry_id"] is not None
        assert int(_feed(c, "c_in")["amount"]) == 7_000  # a credit waits for its category, as any credit does
        assert _bank_ties(c)
        assert _every_entry_balances(c)


def test_a_cash_sign_that_comes_in_inverted_is_flipped_and_noted_and_the_next_row_follows(books):
    fake = FakeBrex()
    connect_brex(books, fake, verified=False)
    assert brex_pass(books, fake, AT) == "ok"
    assert brex_pass(books, fake, LATER[0]) == "ok"
    fake.cash_txns[CASH] += [cash_txn("c_out", "PAYMENT", -2_500), cash_txn("c_in", "PAYMENT", 7_000)]
    fake.cash[0]["current_balance"] = money(-4_500)  # the balance fell by 4,500: the opposite of the rows' signs
    fake.cash[0]["available_balance"] = money(-4_500)
    assert brex_pass(books, fake, LATER[1]) == "ok"
    with books.read() as c:
        settings = _settings(c)
        assert (settings["sign"], settings["flipped"]) == ("verified", True)
        assert int(_feed(c, "c_out")["amount"]) == 2_500 and int(_feed(c, "c_in")["amount"]) == -7_000
        assert _feed(c, "c_in")["entry_id"] is not None and _feed(c, "c_out")["entry_id"] is None
        assert _bank_ties(c)
        status, error = _last_error(c)
    assert status == "active" and "opposite sign" in error
    fake.cash_txns[CASH].append(cash_txn("c_next", "PAYMENT", -1_000))  # read with the same flip: money in
    fake.cash[0]["current_balance"] = money(-3_500)
    fake.cash[0]["available_balance"] = money(-3_500)
    assert brex_pass(books, fake, LATER[2]) == "ok"
    with books.read() as c:
        assert int(_feed(c, "c_next")["amount"]) == 1_000
        assert _bank_ties(c)
        assert _every_entry_balances(c)


def test_a_cash_sign_that_fits_neither_way_stays_unchecked_and_its_rows_wait(books):
    fake = FakeBrex()
    connect_brex(books, fake, verified=False)
    assert brex_pass(books, fake, AT) == "ok"
    assert brex_pass(books, fake, LATER[0]) == "ok"
    fake.cash_txns[CASH].append(cash_txn("c_out", "PAYMENT", -2_500))
    fake.cash[0]["current_balance"] = money(-1_000)  # moved by neither the row (-2,500) nor its opposite (+2,500)
    fake.cash[0]["available_balance"] = money(-1_000)
    assert brex_pass(books, fake, LATER[1]) == "ok"
    with books.read() as c:
        settings = _settings(c)
        assert settings["sign"] == "unverified" and settings["sign_anchor"] == -1_000
        assert settings["sign_pending"] == 0
        assert _feed(c, "c_out")["entry_id"] is None  # stored, not posted
        assert tab._history("brex", settings) == "checking"


def test_a_book_transfer_with_no_other_side_yet_is_noted_and_waits(books):
    fake = FakeBrex()
    connect_brex(books, fake)
    assert brex_pass(books, fake) == "ok"
    fake.cash_txns[CASH].append(cash_txn("bt_1", "BOOK_TRANSFER", -5_000, day="2026-10-14"))
    assert brex_pass(books, fake, LATER[0]) == "ok"
    with books.read() as c:
        assert _feed(c, "bt_1")["entry_id"] is None
        assert _sum(c, "6900") == 0
        status, error = _last_error(c)
    assert status == "active" and "book transfer" in error


def test_the_card_accounts_brex_answers_as_a_bare_array_read_as_one_page():
    def opener(request: Any, timeout: float) -> _Reply:
        return _Reply(json.dumps([card_account("card_acc_9")]).encode())

    got = brex.Brex("tok", base="https://api.brex.com", opener=opener).card_accounts()  # type: ignore[arg-type]
    assert got.status == 200 and [a["id"] for a in brex.listed(got)] == ["card_acc_9"]


def test_posted_at_start_is_sent_with_no_zone():
    seen: list[str] = []

    def opener(request: Any, timeout: float) -> _Reply:
        seen.append(request.full_url)
        return _Reply(b'{"items": [], "next_cursor": null}')

    client = brex.Brex("tok", base="https://api.brex.com", opener=opener)  # type: ignore[arg-type]
    client.card_transactions(brex_sync._start("2026-01-01"))
    assert "posted_at_start=2026-01-01T00%3A00%3A00" in seen[0] and "Z" not in seen[0].split("posted_at_start=")[1]


@pytest.mark.parametrize("side", ["card", "cash"])
def test_a_company_with_only_a_card_or_only_a_cash_account_connects_and_posts(books, side):
    fake = FakeBrex()
    if side == "card":
        fake.cash, fake.cash_txns = [], {}
        fake.card_txns.append(card_txn("ct_1", "PURCHASE", 1_200))
    else:
        fake.cards = []
        fake.cash_txns[CASH].append(cash_txn("cx_1", "DEPOSIT", 5_000))
    connect_brex(books, fake)
    brex_pass(books, fake)
    with books.read() as c:
        assert _feed(c, "ct_1" if side == "card" else "cx_1") is not None
        assert _every_entry_balances(c)


def test_a_company_with_no_card_and_no_cash_account_is_refused(books):
    fake = FakeBrex()
    fake.cards, fake.cash = [], []
    found, _ = brex_sync.probe(fake)  # type: ignore[arg-type]
    with books.transaction() as c:
        done = act(c, OWNER, "connection_create", {"provider": "brex", "credential_id": "cred-brex", "probe": found})
    assert done.status != "committed"


def _openings(c: Any) -> list[tuple[str, str, int]]:
    """Each opening line on a Brex money account's GL line: its day, its line's code and its amount."""
    return [
        (str(r[0]), str(r[1]), int(r[2]))
        for r in c.execute(
            "SELECT e.entry_date, a.code, l.amount FROM fin_entries e JOIN fin_lines l ON l.entry_id=e.id "
            "JOIN fin_accounts a ON a.id=l.account_id WHERE e.source_id LIKE 'opening:%' AND e.status='posted' AND "
            "a.id IN (SELECT account_id FROM fin_money_accounts WHERE provider='brex') ORDER BY a.code"
        )
    ]


def test_a_card_only_company_owing_with_no_rows_opens_the_card_liability(books):
    """A card owing $500 and no card row from history_from on: the card line opens at what is owed (a liability),
    dated the day before history_from."""
    fake = FakeBrex()
    fake.cash, fake.cash_txns = [], {}
    fake.card_owed = 50_000
    connect_brex(books, fake)
    brex_pass(books, fake)
    with books.read() as c:
        card = _code(c, "card_program")
        assert _openings(c) == [("2025-12-31", card, -50_000)]
        assert _sum(c, card) == -50_000
        assert _every_entry_balances(c)


def test_a_cut_history_with_no_cash_rows_opens_the_day_before_history_from(books):
    fake = FakeBrex()
    fake.cards = []
    fake.cash = [cash_account(balance=80_000)]
    connect_brex(books, fake, history_from="2026-09-10")
    brex_pass(books, fake)
    with books.read() as c:
        assert _openings(c) == [("2026-09-09", _code(c, "bank"), 80_000)]


# ------------------------------------------------------------------------------------------------ paid from Mercury
def _mercury_pass(store: Any, fake: Any, at: str = AT) -> None:
    """One pass of the bank worker over the Mercury connection alone, with `fake` as Mercury."""
    with store.read() as c:
        [cid] = [str(r[0]) for r in c.execute("SELECT id FROM fin_connections WHERE provider='mercury'")]
    sync.connection(store, None, cid, make=lambda _t: fake, at=at)  # type: ignore[arg-type]


def _paid_from_mercury(books: Any, *days: str, rule: bool = False) -> tuple[Any, FakeBrex]:
    """Brex and Mercury connected to one book; a Mercury debit of $500 to Brex on each of `days`, taken in first.
    `rule`: a posting rule sends those debits to Software."""
    mercury, fake = FakeMercury(), FakeBrex()
    connect_mercury(books, mercury)
    connect_brex(books, fake)
    if rule:
        with books.transaction() as c:
            done = act(
                c,
                OWNER,
                "rule_create",
                {
                    "kind": "posting",
                    "name": "Card bill",
                    "when": {"counterparty": "Example Card Co"},
                    "outcome": {"outcome": "post", "account_id": account(c, "6100")},
                    "examples": [{"input": {"counterparty": "Example Card Co"}, "expect": "post"}],
                },
            )
            assert done.status == "committed", done
    for n, day in enumerate(days):
        mercury.txns[f"mp-{n}"] = _txn(
            f"mp-{n}", "acct-chk", -500.0, "other", day=day, counterpartyName="Example Card Co"
        )
    _mercury_pass(books, mercury)
    return mercury, fake


def _paired(c: Any, card_id: str, bank_id: str) -> bool:
    card, bank = _feed(c, card_id), _feed(c, bank_id)
    return card["entry_id"] is not None and card["entry_id"] == bank["entry_id"]


def _waiting(c: Any, provider_id: str) -> int:
    fid = str(_feed(c, provider_id)["id"])
    return _count(
        c,
        f"SELECT COUNT(*) FROM fin_events WHERE name='finance.feed.needs_category' AND subject='{fid}' "
        "AND drained_at IS NULL",
    )


@pytest.mark.parametrize("mercury_first", [True, False])
def test_a_brex_card_paid_from_a_mercury_bank_pairs_across_connections_within_a_week(books, mercury_first):
    """The Mercury debit posts six days before the Brex COLLECTION: one transfer from the bank to the card, whichever
    side came in first. A Mercury side on the Other expenses default is taken back; no ask stays open."""
    if mercury_first:
        _mercury, fake = _paid_from_mercury(books, "2026-10-04")
        with books.read() as c:
            assert _sum(c, "6900") == 50_000  # the Mercury side sits on Other expenses for now
        fake.card_txns.append(card_txn("ctx_pay", "COLLECTION", 50_000, day="2026-10-10"))
        assert brex_pass(books, fake, LATER[0]) == "ok"  # the card side waits, then pairs at the end of the pass
    else:
        fake = FakeBrex()
        connect_brex(books, fake)
        fake.card_txns.append(card_txn("ctx_pay", "COLLECTION", 50_000, day="2026-10-10"))
        assert brex_pass(books, fake, LATER[0]) == "ok"
        with books.read() as c:
            assert _feed(c, "ctx_pay")["entry_id"] is None  # no bank side anywhere yet
        mercury = FakeMercury()
        connect_mercury(books, mercury)
        mercury.txns["mp-0"] = _txn("mp-0", "acct-chk", -500.0, "other", day="2026-10-04")
        _mercury_pass(books, mercury)  # the bank side comes second: it posts to Other expenses for now
        assert brex_pass(books, fake, LATER[1]) == "ok"  # the waiting card side pairs on its next pass
    with books.read() as c:
        assert _paired(c, "ctx_pay", "mp-0")
        assert _sum(c, "6900") == 0 and _sum(c, _code(c, "card_program")) == 50_000
        assert _waiting(c, "ctx_pay") == 0 and _waiting(c, "mp-0") == 0
        assert _every_entry_balances(c)


def test_a_mercury_debit_eight_days_from_the_brex_payment_does_not_pair(books):
    _mercury, fake = _paid_from_mercury(books, "2026-10-02")
    fake.card_txns.append(card_txn("ctx_pay", "COLLECTION", 50_000, day="2026-10-10"))
    assert brex_pass(books, fake, LATER[0]) == "ok"
    with books.read() as c:
        assert _feed(c, "ctx_pay")["entry_id"] is None and _sum(c, "6900") == 50_000


def test_two_mercury_debits_that_could_pay_the_brex_card_pair_neither(books):
    _mercury, fake = _paid_from_mercury(books, "2026-10-06", "2026-10-07")
    fake.card_txns.append(card_txn("ctx_pay", "COLLECTION", 50_000, day="2026-10-10"))
    assert brex_pass(books, fake, LATER[0]) == "ok"
    with books.read() as c:
        assert _feed(c, "ctx_pay")["entry_id"] is None and _sum(c, "6900") == 100_000


def test_a_brex_bank_side_on_the_same_connection_wins_over_a_mercury_debit(books):
    _mercury, fake = _paid_from_mercury(books, "2026-10-08")
    fake.card_txns.append(card_txn("ctx_pay", "COLLECTION", 50_000, day="2026-10-10"))
    fake.cash_txns[CASH].append(cash_txn("cash_pay", "CARD_COLLECTION", -50_000, day="2026-10-10"))
    assert brex_pass(books, fake, LATER[0]) == "ok"
    with books.read() as c:
        assert _paired(c, "ctx_pay", "cash_pay")
        assert _sum(c, "6900") == 50_000  # the Mercury debit stays where it was
        assert _every_entry_balances(c)


def test_a_mercury_debit_a_rule_posted_is_not_taken_for_the_brex_payment(books):
    _mercury, fake = _paid_from_mercury(books, "2026-10-08", rule=True)
    with books.read() as c:
        entry = _feed(c, "mp-0")["entry_id"]
        assert _sum(c, "6100") == 50_000
    fake.card_txns.append(card_txn("ctx_pay", "COLLECTION", 50_000, day="2026-10-10"))
    assert brex_pass(books, fake, LATER[0]) == "ok"
    with books.read() as c:
        assert _feed(c, "ctx_pay")["entry_id"] is None and _feed(c, "mp-0")["entry_id"] == entry


@pytest.mark.parametrize("debit_first", [True, False])
def test_a_mercury_io_payment_pairs_on_its_own_connection_before_a_waiting_brex_payment(books, debit_first):
    """A Mercury debit that could pay Mercury IO (same connection) or a waiting Brex payment (another) pays IO, whichever
    of the Mercury rows comes in first; the Brex payment keeps waiting."""
    fake = FakeBrex()
    connect_brex(books, fake)
    fake.card_txns.append(card_txn("ctx_pay", "COLLECTION", 50_000, day="2026-10-08"))
    assert brex_pass(books, fake, LATER[0]) == "ok"
    mercury = FakeMercury()
    connect_mercury(books, mercury)
    debit = ("mp-0", _txn("mp-0", "acct-chk", -500.0, "other", day="2026-10-08"))
    credit = ("io-0", _txn("io-0", "credit-io", 500.0, "other", day="2026-10-08"))
    mercury.txns = dict([debit, credit] if debit_first else [credit, debit])
    _mercury_pass(books, mercury)
    assert brex_pass(books, fake, LATER[1]) == "ok"
    with books.read() as c:
        assert _paired(c, "io-0", "mp-0") and _feed(c, "ctx_pay")["entry_id"] is None
        assert _every_entry_balances(c)
