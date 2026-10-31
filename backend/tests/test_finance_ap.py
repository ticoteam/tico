"""Finance phase 2: bills, invoices, contacts, settlements and the cash view, posting rules, the remaining defaults,
the new events, the close, and the reads that show them (search, report, metric, inbox) cut to each viewer."""
import pytest

from backend.finance import actions as FA
from backend.finance import reads as FR
from backend.tests.test_finance_fixtures import AT, OWNER, books, finance_template, store  # noqa: F401

PLAIN = "human:eve"          # a web teammate with no grants or rights


def test_every_new_read_answers_on_empty_books_and_refuses_what_the_viewer_cannot_see(books):  # noqa: F811
    with books.read() as c:
        assert FR.bills(c, OWNER)["bills"] == [] and FR.invoices(c, OWNER)["open"]["remaining"] == 0
        assert FR.contacts(c, OWNER)["contacts"] == []
        assert FR.search(c, OWNER, kind=None, q="acme")["results"] == []
        assert FR.inbox(c, OWNER)["items"] == []
        for kind in FR.REPORTS:
            assert FR.report(c, OWNER, kind, start="2026-10-01", end="2026-10-31", compare="prior")["kind"] == kind
        for name in FR.METRICS:
            if not name.startswith("budget_"):
                assert FR.metric(c, OWNER, name, at=AT)["as_of"] == AT
        assert FR.close_checklist(c, OWNER, "2026-09")["month"] == "2026-09"
        for read in (FR.bills, FR.invoices, FR.contacts):
            with pytest.raises(FA.FinanceError):
                read(c, PLAIN)
        with pytest.raises(FA.FinanceError):
            FR.metric(c, PLAIN, "cash")
        assert FR.search(c, PLAIN, kind=None)["results"] == []
        assert FA.help("bill_create")["fields"][0]["name"] == "vendor_id"
