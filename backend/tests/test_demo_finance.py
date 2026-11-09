"""Demo finance: Tico's own books balance, show the previous full month and this month to date by default, and carry the
budgets, invoices, bills and rules the demo tells. The bookkeeper's books view is granted and its first rule proposal
approved before the build, so nothing about them is waiting in Ana's queue.

The demo is built early in a month. Finance reads its default month from its clock, so the test pins that clock to the
build's moment, as the demo is shown the day it is built. The checks live in helpers/demo_finance.py (CHECKS), and
test_demo_other_moments.py runs them again on a demo built mid-month in a later quarter."""

from datetime import datetime, timezone

import pytest
import time_machine

from backend import demo
from backend.tests.helpers.demo_finance import CHECKS

EARLY_MONTH = datetime(2026, 11, 2, 18, 30, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def book(tmp_path_factory):
    with time_machine.travel(EARLY_MONTH, tick=False):
        settings = demo.build(tmp_path_factory.mktemp("demo-finance"), now=EARLY_MONTH)
        yield settings, EARLY_MONTH


def test_the_demo_finance_tells_its_story(book, subtests):
    """One build (the book) for every check, in file order, each as its own subtest."""
    for check in CHECKS:
        with subtests.test(check.__name__):
            check(book)
