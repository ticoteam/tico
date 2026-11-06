"""Brex in the books (spec §9): the card and cash accounts, a card charge, refund, chargeback and reward as card rows, a
card payment paired with the bank's debit whichever arrives first, a cash row's sign, a repeated read that changes
nothing, a changed row restated, the backfill across passes with the opening balance, history_from, and the refusals (a
token Brex refuses, an unknown card type, a second card account, a card account that is not the mapped one, a row in
another currency), a 429 waited out, a redirect never followed, and the connections route. Runs on an in-memory fake of
Brex's account and transaction reads; nothing is sent anywhere."""
import io
import json
import urllib.error
from typing import Any

import pytest

from backend.finance import actions as FA
from backend.finance import brex, brex_sync, sync
from backend.finance.context import load
from backend.finance.mercury import Answer
from backend.finance.store import banking as store_banking
from backend.tests.test_api import api, get, headers  # noqa: F401
from backend.tests.test_finance_fixtures import (  # noqa: F401
    AT, OWNER, act, books, finance_template, store)

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
    return {"id": aid, "status": status, "current_balance": money(0), "available_balance": money(0),
            "current_statement_period": {}}


def cash_account(aid: str = CASH, number: str = "123456789012", status: str = "ACTIVE", balance: int = 0,
                 currency: str = "USD", name: str = "Operating") -> dict[str, Any]:
    return {"id": aid, "name": name, "status": status, "account_number": number, "routing_number": "021000021",
            "primary": aid == CASH, "current_balance": money(balance, currency),
            "available_balance": money(balance, currency)}


def card_txn(tid: str, kind: str, amount: int, day: str = "2026-10-10", merchant: str = "BLUE BOTTLE") -> dict[str, Any]:
    return {"id": tid, "type": kind, "amount": money(amount), "description": f"{kind} at {merchant}",
            "posted_at_date": day, "initiated_at_date": day, "card_id": "card_1",
            "merchant": {"raw_descriptor": merchant}}


def cash_txn(tid: str, kind: str, amount: int, day: str = "2026-10-10", currency: str = "USD") -> dict[str, Any]:
    return {"id": tid, "type": kind, "amount": money(amount, currency), "description": kind, "posted_at_date": day,
            "initiated_at_date": day}


class FakeBrex:
    """Brex's account and transaction reads in memory. Transactions are added oldest first and listed newest first, a
    `page` at a time, the cursor the index of the next row; posted_at_start keeps the rows posted on or after its day, as
    Brex's filter does. `status`, when set, answers every call with that HTTP status and no data."""

    def __init__(self, page: int = 100) -> None:
        self.cards: list[dict[str, Any]] = [card_account()]
        self.cash: list[dict[str, Any]] = [cash_account()]
        self.card_txns: list[dict[str, Any]] = []
        self.cash_txns: dict[str, list[dict[str, Any]]] = {CASH: []}
        self.page = page
        self.status: int | None = None

    def card_accounts(self, cursor: str | None = None) -> Answer:
        return self._answer(self.cards, cursor)

    def cash_accounts(self, cursor: str | None = None) -> Answer:
        return self._answer(self.cash, cursor)

    def card_transactions(self, posted_at_start: str, cursor: str | None = None) -> Answer:
        return self._answer(_since(self.card_txns, posted_at_start), cursor)

    def cash_transactions(self, account_id: str, posted_at_start: str, cursor: str | None = None) -> Answer:
        return self._answer(_since(self.cash_txns.get(account_id, []), posted_at_start), cursor)

    def _answer(self, rows: list[dict[str, Any]], cursor: str | None) -> Answer:
        if self.status is not None:
            return Answer(self.status, {"message": "nope"})
        at = int(cursor or 0)
        page = rows[at:at + self.page]
        more = at + len(page) < len(rows)
        return Answer(200, {"items": page, "next_cursor": str(at + len(page)) if more else None})


def _since(rows: list[dict[str, Any]], start: str) -> list[dict[str, Any]]:
    """The rows posted on or after the day `start` names, newest first."""
    return [r for r in reversed(rows) if str(r["posted_at_date"]) >= start[:10]]


def connect_brex(store: Any, fake: FakeBrex, credential: str = "cred-brex", history_from: str | None = None) -> str:
    found, why = brex_sync.probe(fake)  # type: ignore[arg-type]
    assert not why, why
    payload: dict[str, object] = {"provider": "brex", "credential_id": credential, "probe": found}
    if history_from:
        payload["history_from"] = history_from
    with store.transaction() as c:
        done = act(c, OWNER, "connection_create", payload)
    assert done.status == "committed", done
    return str(done.result["connection_id"])


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
    return int(c.execute("SELECT COALESCE(SUM(l.amount),0) FROM fin_lines l JOIN fin_accounts a ON a.id=l.account_id "
                         "WHERE a.code=?", (code,)).fetchone()[0])


def _every_entry_balances(c: Any) -> bool:
    return int(c.execute("SELECT COALESCE(SUM(amount),0) FROM fin_lines").fetchone()[0]) == 0


def _code(c: Any, kind: str) -> str:
    return str(c.execute("SELECT a.code FROM fin_money_accounts m JOIN fin_accounts a ON a.id=m.account_id "
                         "WHERE m.provider='brex' AND m.kind=? ORDER BY m.created_at LIMIT 1", (kind,)).fetchone()[0])


def _card_line_is_its_posted_rows(c: Any) -> bool:
    """A money account's GL line carries each posted feed row's amount: the card's line is the sum of its posted rows."""
    money = c.execute("SELECT id FROM fin_money_accounts WHERE provider='brex' AND kind='card_program'").fetchone()
    posted = int(c.execute("SELECT COALESCE(SUM(amount),0) FROM fin_feed_txns WHERE money_account_id=? AND "
                           "entry_id IS NOT NULL", (str(money[0]),)).fetchone()[0])
    return _sum(c, _code(c, "card_program")) == posted


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
    fake.card_txns += [card_txn("ctx_refund", "REFUND", -300), card_txn("ctx_cb", "CHARGEBACK", 200),
                       card_txn("ctx_rew", "REWARDS_CREDIT", 50)]
    assert brex_pass(books, fake) == "ok"
    with books.read() as c:
        assert [int(_feed(c, p)["amount"]) for p in ("ctx_refund", "ctx_cb", "ctx_rew")] == [300, 200, 50]
        assert _card_line_is_its_posted_rows(c)
        assert _every_entry_balances(c)


def test_an_unknown_card_type_stops_the_pass_and_names_the_row(books):
    fake = FakeBrex()
    connect_brex(books, fake)
    fake.card_txns.append(card_txn("ctx_odd", "MYSTERY_FEE", -99))
    assert brex_pass(books, fake) == "failed"
    with books.read() as c:
        assert _feed(c, "ctx_odd") is None
        status, error = _last_error(c)
    assert status == "error" and "ctx_odd" in error and "MYSTERY_FEE" in error


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
        assert _sum(c, "6900") == 0           # no expense for the payment: it moved money between the two lines
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
        before = (_count(c, "SELECT COUNT(*) FROM fin_feed_txns"), _count(c, "SELECT COUNT(*) FROM fin_entries"),
                  _sum(c, _code(c, "card_program")))
    for at in LATER[:2]:
        assert brex_pass(books, fake, at) == "ok"
    with books.read() as c:
        after = (_count(c, "SELECT COUNT(*) FROM fin_feed_txns"), _count(c, "SELECT COUNT(*) FROM fin_entries"),
                 _sum(c, _code(c, "card_program")))
    assert after == before
    fake.card_txns[0]["amount"] = money(-1_500)
    assert brex_pass(books, fake, LATER[2]) == "ok"
    with books.read() as c:
        row = _feed(c, "ctx_1")
        assert (int(row["amount"]), int(row["version"])) == (-1_500, 1)
        assert _card_line_is_its_posted_rows(c)
        assert _every_entry_balances(c)


def test_the_backfill_walks_pages_across_passes_and_the_opening_makes_the_bank_line_the_balance(books, monkeypatch):
    monkeypatch.setattr(brex_sync, "BACKFILL_PAGES", 1)
    fake = FakeBrex(page=2)
    amounts = [-1_000, 2_000, -3_000, 4_000, -5_000]
    fake.cash_txns[CASH] = [cash_txn(f"cash_{i}", "PAYMENT", amt) for i, amt in enumerate(amounts)]
    fake.cash[0]["current_balance"] = money(100_000 + sum(amounts))    # the balance now: the opening plus every row
    fake.cash[0]["available_balance"] = money(100_000 + sum(amounts))
    connect_brex(books, fake)
    assert brex_pass(books, fake) == "ok"
    with books.read() as c:
        assert _settings(c)["backfilled"] is False     # two of its three pages are still to read
    for at in LATER:
        brex_pass(books, fake, at)
        with books.read() as c:
            if _settings(c)["backfilled"]:
                break
    with books.read() as c:
        settings = _settings(c)
        assert settings["backfilled"] is True and "cursors" not in settings and "walked" not in settings
        assert _count(c, "SELECT COUNT(*) FROM fin_feed_txns") == len(amounts)
        assert _sum(c, _code(c, "bank")) == 100_000 + sum(amounts)
        money_id = str(c.execute("SELECT id FROM fin_money_accounts WHERE provider='brex' AND kind='bank'").fetchone()[0])
        assert store_banking.opening_entry(c, load(c, AT).book.id, money_id) is not None
        assert _every_entry_balances(c)
    assert brex_pass(books, fake, "2026-10-15T16:00:00.000000Z") == "ok"
    with books.read() as c:
        assert _sum(c, _code(c, "bank")) == 100_000 + sum(amounts)
        assert _count(c, "SELECT COUNT(*) FROM fin_feed_txns") == len(amounts)


def test_history_from_is_the_first_day_read(books):
    fake = FakeBrex()
    fake.card_txns += [card_txn("ctx_old", "PURCHASE", -100, day="2026-10-01"),
                       card_txn("ctx_new", "PURCHASE", -200, day="2026-10-06")]
    connect_brex(books, fake, history_from="2026-10-05")
    assert brex_pass(books, fake) == "ok"
    with books.read() as c:
        assert _feed(c, "ctx_old") is None and _feed(c, "ctx_new") is not None
        assert _settings(c)["history_from"] == "2026-10-05"


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
        balance = c.execute("SELECT balance FROM fin_money_accounts WHERE provider='brex' AND kind='bank'").fetchone()[0]
        assert balance is None


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

    client = brex.Brex("tok", base="https://api.brex.com", opener=throttled,  # type: ignore[arg-type]
                       sleep=waits.append)
    got = client.card_accounts()
    assert got.status == 200 and brex.listed(got)[0]["id"] == "card_acc_1"
    assert waits == [3.0] and len(calls) == 2

    redirects: list[str] = []

    def redirected(request: Any, timeout: float) -> _Reply:
        redirects.append(request.full_url)
        raise urllib.error.HTTPError(request.full_url, 302, "found", {"Location": "https://evil.example/x"},
                                     io.BytesIO(b""))

    client = brex.Brex("tok", base="https://api.brex.com", opener=redirected,  # type: ignore[arg-type]
                       sleep=waits.append)
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
    probed: list[str] = []

    def brex_probe(_store: Any, _vault: Any, credential_id: str) -> tuple[dict[str, object], str]:
        probed.append(credential_id)
        return {"card": [{"id": CARD, "name": "Brex card", "last4": ""}],
                "cash": [{"id": CASH, "name": "Operating", "last4": "9012", "current": 0, "available": 0,
                          "currency": "USD"}]}, ""

    def no_mercury(*_args: Any) -> tuple[dict[str, object], str]:
        pytest.fail("a Brex token was tested with Mercury's probe")

    monkeypatch.setattr(FA, "brex_probe", brex_probe)
    monkeypatch.setattr(FA, "mercury_probe", no_mercury)
    res = api.post("/api/v2/finance/connections", json={"provider": "brex", "credential_id": "cred-brex",
                                                        "history_from": "2026-03-01"}, headers=headers())
    assert res.status_code == 200, res.text
    assert probed == ["cred-brex"]
    assert res.json()["status"] == "committed", res.json()
    assert sorted(m["kind"] for m in res.json()["result"]["money_accounts"]) == ["bank", "card_program"]
    with store.read() as c:
        assert json.loads(c.execute("SELECT settings FROM fin_connections WHERE provider='brex'").fetchone()[0]
                          )["history_from"] == "2026-03-01"
