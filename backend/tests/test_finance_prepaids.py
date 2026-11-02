"""Prepaid expenses (backend/finance/prepaids.py, spec §4.2, §4.4): a prepaid bill line's asset is posted untagged when
the bill is entered, and each due month comes out as expense with the line's tags; a due month that will not post is
asked for once per line and month, and tried again on each look instead of being dropped silently."""
import json

from backend.finance import actions as FA
from backend.finance import jobs
from backend.finance.store import tico as T
from backend.tests.test_finance_ap import _bill, _vendor
from backend.tests.test_finance_fixtures import OWNER, account, act, books, finance_template, store  # noqa: F401

DEC_15 = "2026-12-15T12:00:00.000000Z"      # November and December have come for a line starting in November


def _posted(c, bill_id: str) -> list[tuple[str, int, str]]:
    """The bill's posted lines besides AP, as (account code, amount, teammate tag as JSON) across its own entry and its
    months. Only the teammate tag is compared: the company's default team tag is on every expense line as well."""
    ids = {str(r[0]) for r in c.execute("SELECT id FROM fin_bill_lines WHERE bill_id=?", (bill_id,))}
    out: list[tuple[str, int, str]] = []
    for line_id, source_id, code, amount in c.execute(
            "SELECT l.id, e.source_id, a.code, l.amount FROM fin_lines l JOIN fin_entries e ON e.id=l.entry_id "
            "JOIN fin_accounts a ON a.id=l.account_id WHERE e.source='bill' AND e.status='posted' AND a.code!='2000'"):
        if source_id == bill_id or str(source_id).split(":")[0] in ids:
            tags = {str(k): str(v) for k, v in c.execute("SELECT kind, value FROM fin_line_tags WHERE line_id=? "
                                                         "AND kind='teammate'", (line_id,))}
            out.append((str(code), int(amount), json.dumps(tags, sort_keys=True)))
    return sorted(out)


def test_a_prepaid_line_leaves_its_asset_untagged_and_each_due_month_carries_the_lines_tags(books):
    with books.transaction() as c:
        bid = _bill(c, _vendor(c), [
            {"account_id": account(c, "6100"), "amount": 1_800, "prepaid": {"start": "2026-11", "months": 3},
             "tags": {"teammate": "human:ana"}},
            {"account_id": account(c, "6900"), "amount": 500, "tags": {"teammate": "human:ben"}}])
        assert act(c, OWNER, "bill_approve", {"bill_id": bid}).status == "committed"
        # Entering the bill: the prepaid line is an asset (untagged), the other line is expense as it is tagged.
        assert _posted(c, bid) == [("1150", 1_800, "{}"), ("6900", 500, '{"teammate": "human:ben"}')]
        assert FA.amortize_prepaids(c, DEC_15) == 2
        # Each month that came expenses a third of the line on its own tags, out of the asset.
        assert _posted(c, bid) == sorted([("1150", 1_800, "{}"), ("1150", -600, "{}"), ("1150", -600, "{}"),
                                          ("6900", 500, '{"teammate": "human:ben"}'),
                                          ("6100", 600, '{"teammate": "human:ana"}'),
                                          ("6100", 600, '{"teammate": "human:ana"}')])
        assert _balance_of(c, "1150") == 600


def test_a_due_month_that_will_not_post_is_asked_once_per_line_and_month_and_tried_again_on_the_next_look(books, monkeypatch):
    asks: list[tuple[str, str]] = []
    monkeypatch.setattr(T, "needs_you", lambda c, person, title, body: asks.append((person, title)))
    with books.transaction() as c:
        bid = _bill(c, _vendor(c), [{"account_id": account(c, "6100"), "amount": 1_800,
                                     "prepaid": {"start": "2026-11", "months": 3}}])
        assert act(c, OWNER, "bill_approve", {"bill_id": bid}).status == "committed"
        line_id = str(c.execute("SELECT id FROM fin_bill_lines WHERE bill_id=?", (bid,)).fetchone()[0])
        c.execute("UPDATE fin_accounts SET active=0 WHERE code='6100'")       # its expense account is switched off
        assert FA.amortize_prepaids(c, DEC_15) == 0
        assert FA.amortize_prepaids(c, DEC_15) == 0                           # tried again on the next look
        assert len(asks) == 4                                                 # two due months, two looks
        assert {person for person, _ in asks} == {OWNER}                      # the finance admin (the owner here)
        assert sorted({title for _, title in asks}) == sorted([
            f"Prepaid 2026-11 not expensed: line {line_id}", f"Prepaid 2026-12 not expensed: line {line_id}"])
        c.execute("UPDATE fin_accounts SET active=1 WHERE code='6100'")       # fixed: the months post, and ask no more
        assert FA.amortize_prepaids(c, DEC_15) == 2
        assert len(asks) == 4
        assert _balance_of(c, "1150") == 600


def test_a_book_with_a_providers_books_amortizes_nothing(books):
    """Mirror books are a provider's: the job posts no prepaid month (jobs.amortize returns before any write)."""
    with books.transaction() as c:
        bid = _bill(c, _vendor(c), [{"account_id": account(c, "6100"), "amount": 1_800,
                                     "prepaid": {"start": "2026-11", "months": 3}}])
        assert act(c, OWNER, "bill_approve", {"bill_id": bid}).status == "committed"
        c.execute("UPDATE fin_books SET mode='mirror'")
        assert jobs.amortize(c, DEC_15) == 0
        c.execute("UPDATE fin_books SET mode='native'")
        assert _balance_of(c, "1150") == 1_800


def _balance_of(c, code: str) -> int:
    return int(c.execute("SELECT COALESCE(SUM(l.amount), 0) FROM fin_lines l JOIN fin_accounts a ON a.id=l.account_id "
                         "JOIN fin_entries e ON e.id=l.entry_id WHERE a.code=? AND e.status='posted'",
                         (code,)).fetchone()[0])
