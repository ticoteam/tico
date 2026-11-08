"""Commitments (spec §3.3, §9): a bill, a card authorization, its settlement and the payment for it are linked by one
commitment id. A bill's holds carry the bill's id; a card charge carries one id on its feed row, its hold and its
authorization; and the bank or card row that pays a bill takes the bill's id, with the hold and authorization it came
with."""
from backend.finance import reads as FR
from backend.tests.test_finance_ap import _bill, _ingest, _txn, _vendor
from backend.tests.test_finance_ap_review import _approved, _mark
from backend.tests.test_finance_banking import _arrives, _card_for_eve
from backend.tests.test_finance_fixtures import (  # noqa: F401
    MERCURY, OWNER, FakeMercury, account, act, books, connect, finance_template, store, sync_once)
from backend.tests.test_finance_payments import HOURS


def _commitments(c, provider_id: str) -> tuple:
    """The feed row's, its hold's and its card authorization's commitment ids (None where a row has none)."""
    return tuple(c.execute("SELECT f.commitment_id, h.commitment_id, a.commitment_id FROM fin_feed_txns f "
                           "LEFT JOIN fin_holds h ON h.id=f.hold_id "
                           "LEFT JOIN fin_card_auths a ON a.hold_id=f.hold_id WHERE f.provider_id=?",
                           (provider_id,)).fetchone())


def test_a_bills_holds_carry_the_bills_commitment(books):
    with books.transaction() as c:
        made = act(c, OWNER, "budget_create", {"name": "Software", "account_id": account(c, "6100"),
                                               "amount": 100_000, "at_limit": "decline"})
        assert made.status == "committed", made
        bid = _bill(c, _vendor(c), [{"account_id": account(c, "6100"), "amount": 4_500}])
        held = [r[0] for r in c.execute("SELECT commitment_id FROM fin_holds WHERE source='bill' AND "
                                        "source_id LIKE ?", (f"{bid}:%",))]
        assert held == [bid]


def test_a_bank_row_that_pays_an_entered_bill_takes_the_bills_commitment(books):
    fake = FakeMercury()
    connect(books, fake)
    with books.transaction() as c:
        bid = _approved(c, _vendor(c), 12_345)
        fid = _ingest(c, _txn("txn-b3", -123.45))
        assert FR.bill(c, OWNER, bid)["status"] == "paid"
        assert c.execute("SELECT commitment_id FROM fin_feed_txns WHERE id=?", (fid,)).fetchone()[0] == bid


def test_a_bank_row_that_a_payment_marked_paid_waits_for_takes_the_bills_commitment(books):
    fake = FakeMercury()
    connect(books, fake)
    with books.transaction() as c:
        bid = _approved(c, _vendor(c), 4_500)
        _mark(c, bid)
        fid = _ingest(c, _txn("txn-marked", -45.00))
        assert FR.bill(c, OWNER, bid)["status"] == "paid"
        assert c.execute("SELECT commitment_id FROM fin_feed_txns WHERE id=?", (fid,)).fetchone()[0] == bid


def test_a_card_charge_carries_one_commitment_from_its_authorization_to_the_bill_it_pays(books):
    fake = FakeMercury()
    fake.balances["credit-io"] = 0
    connect(books, fake)
    _card_for_eve(books, fake)
    sync_once(books, fake)
    with books.transaction() as c:
        bid = _approved(c, _vendor(c, "Acme Cloud"), 4_500)
    _arrives(fake, MERCURY["txn_card_pending"], 1, "evt-0001")
    sync_once(books, fake, HOURS[1])
    with books.read() as c:
        held = _commitments(c, "txn-card-1")
        assert held[0] and held[0] == held[1] == held[2], held          # one id on the row, hold and authorization
    _arrives(fake, MERCURY["txn_card_posted"], 2, "evt-0002")
    sync_once(books, fake, HOURS[2])
    with books.read() as c:
        assert FR.bill(c, OWNER, bid)["status"] == "paid"
        assert _commitments(c, "txn-card-1") == (bid, bid, bid)         # the bill it pays takes the id
