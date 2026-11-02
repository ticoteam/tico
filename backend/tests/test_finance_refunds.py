"""Refunds bank-side (backend/finance/refunds.py): money in from a merchant the books paid, from the same account, reverses
that money-out row's own lines in proportion and with their tags, and links to it. A refund that cannot be told apart
from others stays new and is asked, with the candidates named. A refund of a charge in a closed month posts in the open
period and keeps the charge's date in its memo."""
from backend.finance import reads as FR
from backend.finance.store import banking as SK
from backend.tests.test_finance_fixtures import (  # noqa: F401
    AT, MERCURY, OWNER, FakeMercury, account, act, books, connect, finance_template, store, sync_once)
from backend.tests.test_finance_payments import _checking

LATER = "2026-10-15T12:30:00.000000Z"
NEXT_DAY = "2026-10-16T12:30:00.000000Z"


def _txn(raw_id: str, amount: float, day: str, counterparty: str):
    return {**MERCURY["txn_bank_fee"], "id": raw_id, "accountId": "acct-chk", "amount": amount,
            "kind": "externalTransfer", "createdAt": f"{day}T10:00:00Z", "postedAt": f"{day}T10:00:00Z",
            "counterpartyName": counterparty}


def _arrives(fake, raw_id: str, amount: float, day: str, counterparty: str = "Acme", version: int = 1) -> None:
    """Mercury has this transaction at this version, and an event says so."""
    fake.txns[raw_id] = _txn(raw_id, amount, day, counterparty)
    fake.events_list.append({"id": f"evt-{raw_id}-{version}", "resourceType": "transaction", "resourceId": raw_id,
                             "operationType": "create" if version == 1 else "update", "resourceVersion": version})


def _fid(c, provider_id: str) -> str:
    return str(c.execute("SELECT id FROM fin_feed_txns WHERE provider_id=?", (provider_id,)).fetchone()[0])


def _row(c, provider_id: str) -> tuple:
    """The row's status, whether it has an entry, and the money-out row it refunds (by feed id)."""
    status, entry, refund_of = c.execute("SELECT status, entry_id, refund_of FROM fin_feed_txns WHERE provider_id=?",
                                         (provider_id,)).fetchone()
    return (status, entry is not None, refund_of)


def _balance(c, code: str) -> int:
    return int(c.execute("SELECT COALESCE(SUM(l.amount), 0) FROM fin_lines l JOIN fin_accounts a ON a.id=l.account_id "
                         "WHERE a.code=?", (code,)).fetchone()[0])


def _bank_code(c, money_id: str) -> str:
    return str(c.execute("SELECT a.code FROM fin_money_accounts m JOIN fin_accounts a ON a.id=m.account_id "
                         "WHERE m.id=?", (money_id,)).fetchone()[0])


def _asked(c, fid: str) -> list[dict]:
    return [SK.loads(raw) for (raw,) in c.execute(
        "SELECT payload_json FROM fin_events WHERE name='finance.feed.needs_category' AND subject=?", (fid,))]


def test_a_refund_reverses_its_outflow_in_full_with_the_outflows_tags(books):
    fake = FakeMercury()
    connect(books, fake)
    sync_once(books, fake)                                       # the account's opening
    with books.read() as c:
        bank = _bank_code(c, _checking(c))
        before = _balance(c, bank)
    _arrives(fake, "txn-buy", -100.0, "2026-10-16")
    sync_once(books, fake, LATER)                                # no rule: Other expenses
    with books.transaction() as c:
        buy = _fid(c, "txn-buy")
        tagged = act(c, OWNER, "categorize", {"feed_id": buy, "account_id": account(c, "6900"),
                                              "tags": {"teammate": "human:ana"}})
        assert tagged.status == "committed", tagged
    _arrives(fake, "txn-back", 100.0, "2026-10-17")
    sync_once(books, fake, NEXT_DAY)
    with books.read() as c:
        assert _row(c, "txn-back") == ("posted", True, buy)
        assert _balance(c, "6900") == 0                          # the outflow's expense is reversed
        assert _balance(c, bank) == before                       # and the money is back on the account
        ana = {r["key"]: r["amount"] for r in FR.expenses(c, OWNER, "2026-10-01", "2026-10-31", "teammate",
                                                          at=AT)["rows"]}.get("human:ana", 0)
        assert ana == 0                                          # the reversal carries Ana's tag


def test_partial_refunds_reverse_their_share_and_the_last_takes_the_rest_after_a_provider_update(books):
    fake = FakeMercury()
    connect(books, fake)
    sync_once(books, fake)
    _arrives(fake, "txn-buy", -100.0, "2026-10-16")
    sync_once(books, fake, LATER)
    with books.read() as c:
        buy = _fid(c, "txn-buy")
    _arrives(fake, "txn-part-1", 30.0, "2026-10-17")
    sync_once(books, fake, NEXT_DAY)
    with books.read() as c:
        assert _row(c, "txn-part-1") == ("posted", True, buy)
        assert _balance(c, "6900") == 7_000                     # 30.00 of the 100.00 reversed
    # Mercury sends the first refund again at a new version: the link to its outflow must survive the rewrite.
    _arrives(fake, "txn-part-1", 30.0, "2026-10-17", version=2)
    sync_once(books, fake, "2026-10-17T12:30:00.000000Z")
    _arrives(fake, "txn-part-2", 70.0, "2026-10-18")
    sync_once(books, fake, "2026-10-18T12:30:00.000000Z")
    with books.read() as c:
        assert _row(c, "txn-part-2") == ("posted", True, buy)
        assert _balance(c, "6900") == 0                         # the rest, and no more
        assert _row(c, "txn-part-1") == ("posted", True, buy)


def test_a_refund_no_outflow_fits_stays_new_and_is_asked_with_the_candidates(books):
    fake = FakeMercury()
    connect(books, fake)
    sync_once(books, fake)
    _arrives(fake, "txn-buy", -100.0, "2026-10-16")
    sync_once(books, fake, LATER)
    _arrives(fake, "txn-big", 150.0, "2026-10-17")               # more than the one outflow to Acme
    _arrives(fake, "txn-other", 40.0, "2026-10-17", counterparty="Globex")   # no outflow to Globex at all
    sync_once(books, fake, NEXT_DAY)
    with books.read() as c:
        buy = _fid(c, "txn-buy")
        big = _fid(c, "txn-big")
        assert _row(c, "txn-big") == ("new", False, None)
        assert _asked(c, big) and _asked(c, big)[0]["candidates"] == [buy]
        assert _row(c, "txn-other") == ("new", False, None)       # the ordinary path: no rule, so it is asked
        assert _balance(c, "6900") == 10_000                     # nothing reversed


def test_several_outflows_that_could_be_it_are_asked_and_an_exact_amount_takes_the_newest(books):
    fake = FakeMercury()
    connect(books, fake)
    sync_once(books, fake)
    _arrives(fake, "txn-a", -100.0, "2026-10-16")
    _arrives(fake, "txn-b", -100.0, "2026-10-17")
    sync_once(books, fake, LATER)
    _arrives(fake, "txn-part", 60.0, "2026-10-18")               # both outflows could be it: not an exact amount
    sync_once(books, fake, NEXT_DAY)
    with books.read() as c:
        a, b = _fid(c, "txn-a"), _fid(c, "txn-b")
        part = _fid(c, "txn-part")
        assert _row(c, "txn-part") == ("new", False, None)
        assert sorted(_asked(c, part)[0]["candidates"]) == sorted([a, b])
    _arrives(fake, "txn-exact", 100.0, "2026-10-19")             # exact for both: the newer outflow takes it
    sync_once(books, fake, "2026-10-19T12:30:00.000000Z")
    with books.read() as c:
        assert _row(c, "txn-exact") == ("posted", True, b)
        assert _balance(c, "6900") == 10_000                     # one outflow's 100.00 reversed, the other's kept


def test_a_refund_of_a_charge_in_a_closed_month_keeps_the_charge_date_in_its_memo(books):
    fake = FakeMercury()
    connect(books, fake)
    _arrives(fake, "txn-sep", -100.0, "2026-09-20")
    sync_once(books, fake)
    with books.transaction() as c:
        assert act(c, OWNER, "close_period", {"month": "2026-09"}).status == "committed"
    _arrives(fake, "txn-sep-back", 100.0, "2026-10-05")
    sync_once(books, fake, LATER)
    with books.read() as c:
        assert _row(c, "txn-sep-back") == ("posted", True, _fid(c, "txn-sep"))
        day, memo = c.execute("SELECT e.entry_date, e.memo FROM fin_entries e JOIN fin_feed_txns f ON f.entry_id=e.id "
                              "WHERE f.provider_id='txn-sep-back'").fetchone()
        assert day == "2026-10-05"                               # an open period
        assert "2026-09-20" in memo                              # the charge's own date
        assert _balance(c, "6900") == 0
