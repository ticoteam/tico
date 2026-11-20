"""Brex test helpers: an in-memory fake of Brex's account and transaction reads in the shapes the real API answers,
its rows, and connecting it and running one sync pass. Shared by the Brex tests and other finance tests."""

import json
import re
from collections.abc import Callable
from typing import Any

from backend.finance import brex, brex_sync, sync
from backend.finance.core import feeds as F
from backend.finance.core.errors import FinanceError
from backend.finance.mercury import Answer
from backend.tests.helpers.finance_fixtures import AT, OWNER, act

CARD = "card_acc_1"
CASH = "cash_acc_1"
CASH_TWO = "cash_acc_2"
LATER = ("2026-10-15T13:00:00.000000Z", "2026-10-15T14:00:00.000000Z", "2026-10-15T15:00:00.000000Z")


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
