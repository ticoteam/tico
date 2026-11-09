"""Helpers that other tests share, moved out of test_finance_ap_review.py so they import no test file."""

from backend.tests.helpers.finance_ap import _bill
from backend.tests.helpers.finance_fixtures import OWNER, account, act
from backend.tests.helpers.finance_payments import _checking


def _approved(c, vendor, amount, code="6100", **more) -> str:
    bid = _bill(c, vendor, [{"account_id": account(c, code), "amount": amount, **more.pop("line", {})}], **more)
    assert act(c, OWNER, "bill_approve", {"bill_id": bid}).status == "committed"
    return bid


def _mark(c, bid, **more):
    done = act(c, OWNER, "bill_mark_paid", {"bill_id": bid, "money_account_id": _checking(c), **more})
    assert done.status == "committed", done
    return done
