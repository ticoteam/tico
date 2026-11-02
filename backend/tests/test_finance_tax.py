"""Tax tracking (spec §11): the sales tax and 1099 workpapers and their CSV exports, the due dates the Bookkeeper owns, and
who may read the workpapers. Card payments stay out of the 1099 cash total, and a TIN never reaches a fin_* table."""
import pytest

from backend import hubdb as H
from backend.finance import actions as FA
from backend.finance import reads as FR
from backend.finance import tax as FT
from backend.finance.actions import FinanceError
from backend.finance.context import load
from backend.finance.core import tax as T
from backend.finance.store import banking as SK
from backend.finance.store import schema
from backend.tests.test_finance_fixtures import (  # noqa: F401
    AT, OWNER, PEOPLE, account, act, books, close_month, finance_template, store)

PLAIN = "human:eve"
PAYABLES = "human:dan"                          # a teammate whose view covers their own expenses and payables
TIN = "00-0000001"
BOOKKEEPER = {"bookkeeper": {"name": "bookkeeper", "runtime": "fake", "status": "active"}}


@pytest.fixture
def keeper(books):
    """Finance is set up and the Bookkeeper is on the roster, as bookkeeper.ensure leaves an install."""
    with books.transaction() as c:
        H.sync_registry(c, BOOKKEEPER, {"people": PEOPLE})
    return books


def _contact(c, name, kind="vendor", **more):
    made = act(c, OWNER, "contact_create", {"name": name, "kind": kind, **more})
    assert made.status == "committed", made
    return str(made.result["contact_id"])


def _book(c):
    return str(c.execute("SELECT id FROM fin_books").fetchone()[0])


def _card_gl(c):
    made = act(c, OWNER, "account_create", {"code": "2200", "name": "Company card", "type": "liability",
                                            "subtype": "credit_card"})
    assert made.status == "committed", made


def _private_salaries(c):
    """A private expense account for payroll, hidden from everyone but the owner."""
    made = act(c, OWNER, "account_create", {"code": "6500", "name": "Salaries", "type": "expense"})
    assert made.status == "committed", made
    assert act(c, OWNER, "private_lines_set", {"account_id": account(c, "6500"), "private": True,
                                               "others_see": "hidden"}).status == "committed"


def _bank(c):
    return SK.insert(c, "fin_money_accounts", {
        "book_id": _book(c), "provider": "mercury", "created_at": AT, "created_by": "system:finance", "kind": "bank",
        "subtype": "checking", "name": "Acme checking", "account_id": account(c, "1000"), "last4": "0001",
        "balance": 90_000, "balance_currency": "USD", "balance_as_of": AT})


def _program(c):
    return SK.insert(c, "fin_money_accounts", {
        "book_id": _book(c), "provider": "mercury", "created_at": AT, "created_by": "system:finance",
        "kind": "card_program", "name": "Acme IO", "holds_cash": 0, "account_id": account(c, "2200"), "last4": "0002"})


def _card_charge(c, program, amount, provider_id):
    """A card charge on the card program's feed, not yet in the books: it pays a bill through its own row."""
    return SK.insert(c, "fin_feed_txns", {
        "money_account_id": program, "provider_id": provider_id, "txn_date": "2026-06-01", "amount": -amount,
        "counterparty": "Acme Consulting", "currency": "USD", "created_at": AT, "created_by": "system:finance",
        "status": "posted", "raw_json": "{}"})


def _feed(c, money, amount, day="2026-06-01", kind=""):
    """A bank or card row as the provider sent it, not yet in the books, paid to Acme Consulting."""
    return SK.insert(c, "fin_feed_txns", {
        "money_account_id": money, "provider_id": "txn", "txn_date": day, "amount": amount, "currency": "USD",
        "counterparty": "Acme Consulting", "kind": kind, "created_at": AT, "created_by": "system:finance",
        "raw_json": "{}"})


def _pay(c, vendor, amount, source, bill_day="2026-05-01", paid_on="2026-06-01", code="6100"):
    """A bill for one expense on account `code`, approved and paid from `source` (a money or GL account). Returns the bill."""
    made = act(c, OWNER, "bill_create", {"vendor_id": vendor, "bill_date": bill_day,
                                         "lines": [{"account_id": account(c, code), "amount": amount}]})
    assert made.status == "committed", made
    bill = str(made.result["bill_id"])
    assert act(c, OWNER, "bill_approve", {"bill_id": bill}).status == "committed"
    paid = act(c, OWNER, "bill_mark_paid", {"bill_id": bill, "date": paid_on, **source})
    assert paid.status == "committed", paid
    return bill


def _expense(c, vendor, amount, source, source_id, credit_code):
    """A bank or card row posted straight to an expense with the vendor on it."""
    FA.post_entry(c, {"date": "2026-06-02", "source": source, "source_id": source_id, "lines": [
        {"account_id": account(c, "6100"), "amount": amount, "contact_id": vendor},
        {"account_id": account(c, credit_code), "amount": -amount}]}, at=AT)


def _grant(c, teammate, what, **more):
    made = act(c, OWNER, "view_grant", {"subject": teammate, "what": what, **more})
    assert made.status == "committed", made


def _vendor_row(report, name):
    return next(r for r in report["rows"] if r["vendor"] == name)


def _quarter(rows, period):
    row = next(r for r in rows if r["period"] == period)
    return {k: row[k] for k in ("taxable_sales", "tax_collected", "tax_remitted", "due")}


def _invoice(c, customer, day, income, tax=0, tax_account=""):
    lines = [{"account_id": account(c, "4000"), "amount": income}]
    if tax:
        lines.append({"account_id": tax_account, "amount": tax})
    made = act(c, OWNER, "invoice_create", {"customer_id": customer, "invoice_date": day, "lines": lines})
    assert made.status == "committed", made
    assert act(c, OWNER, "invoice_send", {"invoice_id": made.result["invoice_id"]}).status == "committed"


def _remit(c, period):
    made = act(c, OWNER, "tax_remit", {"jurisdiction": "TL", "period": period})
    assert made.status == "committed", made
    bill = str(made.result["bill_id"])
    assert act(c, OWNER, "bill_approve", {"bill_id": bill}).status == "committed"
    return bill


def test_the_1099_totals_count_cash_and_leave_card_payments_out(keeper):
    with keeper.transaction() as c:
        acme = _contact(c, "Acme Consulting", is_1099=True, tax_id=TIN)
        _card_gl(c)
        bank, program = _bank(c), _program(c)
        _pay(c, acme, 50_000, {"money_account_id": bank})
        _pay(c, acme, 30_000, {"feed_id": _card_charge(c, program, 30_000, "t1")})
        _pay(c, acme, 25_000, {"account_id": account(c, "2200")})
        _pay(c, acme, 10_000, {"account_id": account(c, "1000")})
        _expense(c, acme, 20_000, "bank", "b1", "1000")
        _expense(c, acme, 15_000, "card", "c1", "2200")
    with keeper.read() as c:
        report = FR.form_1099_report(c, OWNER, 2026, AT)
        csv_text = FR.form_1099_csv(c, OWNER, 2026, AT)
    row = _vendor_row(report, "Acme Consulting")
    assert (row["cash_paid"], row["card_paid_excluded"], row["tin_on_file"]) == (80_000, 70_000, True)
    assert csv_text.splitlines() == [",".join(T.FORM_COLUMNS), "2026,Acme Consulting,yes,800.00,700.00,2000.00,no"]


def test_the_1099_threshold_is_2000_from_2026_and_600_before(keeper):
    with keeper.transaction() as c:
        small = _contact(c, "Small Co", is_1099=True)
        old = _contact(c, "Old Co", is_1099=True)
        bank = _bank(c)
        _pay(c, small, 199_999, {"money_account_id": bank})
    with keeper.read() as c:
        assert _vendor_row(FR.form_1099_report(c, OWNER, 2026, AT), "Small Co")["reportable"] is False
    with keeper.transaction() as c:
        _pay(c, small, 1, {"money_account_id": bank})
        _pay(c, old, 60_000, {"money_account_id": bank}, bill_day="2025-05-01", paid_on="2025-06-01")
    with keeper.read() as c:
        report = FR.form_1099_report(c, OWNER, 2026, AT)
        old_report = FR.form_1099_report(c, OWNER, 2025, AT)
    small_row = _vendor_row(report, "Small Co")
    assert (small_row["cash_paid"], small_row["threshold"], small_row["reportable"]) == (200_000, 200_000, True)
    assert old_report["threshold"] == 60_000
    assert _vendor_row(old_report, "Old Co")["reportable"] is True
    assert (T.threshold(2026), T.threshold(2025)) == (200_000, 60_000)


def test_a_tin_is_never_written_to_the_finance_tables(keeper):
    with keeper.transaction() as c:
        _contact(c, "Acme Consulting", is_1099=True, tax_id=TIN)
        for table in schema.table_names():
            for row in c.execute(f"SELECT * FROM {table}").fetchall():
                assert "0000001" not in " ".join(str(v) for v in row), table


def test_sales_tax_is_collected_and_remitted_by_jurisdiction_and_quarter(keeper):
    with keeper.transaction() as c:
        authority = _contact(c, "Testland Revenue")
        made = act(c, OWNER, "tax_jurisdiction_add", {"code": "TL", "name": "Testland", "authority_id": authority,
                                                      "due_day": 20})
        assert made.status == "committed", made
        tax_account = str(made.result["account_id"])
        customer = _contact(c, "Globex", kind="customer")
        _invoice(c, customer, "2026-02-10", 20_000, 1_600, tax_account)
        _invoice(c, customer, "2026-03-05", 5_000)
        _invoice(c, customer, "2026-08-10", 100_000, 8_000, tax_account)
        _invoice(c, customer, "2026-09-15", 50_000)
        assert act(c, OWNER, "tax_remit", {"jurisdiction": "TL", "period": "2026-Q2"}).code == "invalid"
        q1 = _remit(c, "2026-Q1")
        assert act(c, OWNER, "bill_mark_paid", {"bill_id": q1, "account_id": account(c, "1000")}).status == "committed"
        q3 = _remit(c, "2026-Q3")
    with keeper.read() as c:
        rows = FR.sales_tax_report(c, OWNER, 2026, AT)["rows"]
    assert len(rows) == 4
    assert _quarter(rows, "2026-Q1") == {"taxable_sales": 20_000, "tax_collected": 1_600, "tax_remitted": 1_600, "due": 0}
    assert _quarter(rows, "2026-Q3") == {"taxable_sales": 100_000, "tax_collected": 8_000, "tax_remitted": 0,
                                         "due": 8_000}
    with keeper.transaction() as c:
        assert act(c, OWNER, "bill_mark_paid", {"bill_id": q3, "account_id": account(c, "1000")}).status == "committed"
    with keeper.read() as c:
        rows = FR.sales_tax_report(c, OWNER, 2026, AT)["rows"]
        csv_text = FR.sales_tax_csv(c, OWNER, 2026, AT)
    assert _quarter(rows, "2026-Q3") == {"taxable_sales": 100_000, "tax_collected": 8_000, "tax_remitted": 8_000,
                                         "due": 0}
    assert [r["due_on"] for r in rows] == [None, None, "2026-10-20", "2027-01-20"]
    assert csv_text.splitlines() == [
        ",".join(T.SALES_COLUMNS),
        "2026-Q1,Testland,200.00,0.00,16.00,16.00,0.00",
        "2026-Q2,Testland,0.00,0.00,0.00,0.00,0.00",
        "2026-Q3,Testland,1000.00,0.00,80.00,80.00,0.00",
        "2026-Q4,Testland,0.00,0.00,0.00,0.00,0.00"]


def test_a_jurisdiction_needs_a_vendor_authority_and_a_remittance_needs_one(keeper):
    with keeper.transaction() as c:
        customer = _contact(c, "Globex", kind="customer")
        assert act(c, OWNER, "tax_jurisdiction_add", {"code": "TL", "name": "Testland",
                                                      "authority_id": customer}).code == "invalid"
        assert act(c, OWNER, "tax_jurisdiction_add", {"code": "TL", "name": "Testland"}).status == "committed"
        assert act(c, OWNER, "tax_remit", {"jurisdiction": "TL", "period": "2026-Q1"}).code == "invalid"


def test_due_dates_wait_for_finance_and_the_bookkeeper_then_seed_once(store):
    with store.transaction() as c:
        assert FT.seed_dues(c, AT) == []
        FA.setup(c, OWNER, AT)
        assert FT.seed_dues(c, AT) == []
        H.sync_registry(c, BOOKKEEPER, {"people": PEOPLE})
        assert FT.seed_dues(c, AT) == ["1099-nec"]
        assert FT.seed_dues(c, AT) == []
        authority = _contact(c, "Testland Revenue")
        assert act(c, OWNER, "tax_jurisdiction_add", {"code": "TL", "name": "Testland", "authority_id": authority,
                                                      "due_day": 20}).status == "committed"
        assert FT.seed_dues(c, AT) == []
        assert act(c, OWNER, "tax_jurisdiction_add", {"code": "TM", "name": "Testmont",
                                                      "due_day": 5}).status == "committed"
        assert FT.seed_dues(c, AT) == []
        first = {"1099-nec": ("yearly", "2027-01-31"), "sales-tax:TL": ("quarterly", "2026-10-20"),
                 "sales-tax:TM": ("quarterly", "2027-01-05")}          # the first day on or after AT for each
        seeds = [(str(r[0]), str(r[1])) for r in c.execute("SELECT key, rule_id FROM fin_tax_seeds").fetchall()]
        assert sorted(key for key, _ in seeds) == sorted(first)
        for key, rule_id in seeds:
            rules = c.execute("SELECT COUNT(*) FROM org_rules WHERE id=? AND kind='external' AND status='active'",
                              (rule_id,)).fetchone()[0]
            dues = [(str(r[0]), str(r[1]), str(r[2]), r[3]) for r in c.execute(
                "SELECT owner, repeats, first_due, routine_id FROM rule_due_dates WHERE rule_id=?", (rule_id,))]
            assert rules == 1, key
            assert len(dues) == 1, key
            owner, repeats, first_due, routine_id = dues[0]
            assert (owner, routine_id is not None) == ("bot:bookkeeper", True), key
            assert (repeats, first_due) == first[key], key


def test_a_teammate_who_does_not_see_everything_is_refused_the_workpapers(keeper):
    with keeper.transaction() as c:
        authority = _contact(c, "Testland Revenue")
        made = act(c, OWNER, "tax_jurisdiction_add", {"code": "TL", "name": "Testland", "authority_id": authority})
        assert made.status == "committed", made
        _grant(c, PAYABLES, "payables")
        _grant(c, PAYABLES, "receivables")
        _grant(c, PAYABLES, "expenses", whose="own")
    reads = [FR.sales_tax_report, FR.sales_tax_csv, FR.form_1099_report, FR.form_1099_csv]
    with keeper.read() as c:
        for who in (PLAIN, PAYABLES):
            for read in reads:
                with pytest.raises(FinanceError) as refused:
                    read(c, who, 2026, AT)
                assert refused.value.code == "denied", (who, read.__name__)
    with keeper.transaction() as c:
        # Without the amount owed there is no bill to make: the owed figure is read off the workpaper.
        assert act(c, PAYABLES, "tax_remit", {"jurisdiction": "TL", "period": "2026-Q1"}).code == "denied"


def test_an_everything_grant_reads_the_workpapers_whole(keeper):
    with keeper.transaction() as c:
        acme = _contact(c, "Acme Consulting", is_1099=True, tax_id=TIN)
        bank = _bank(c)
        _grant(c, PAYABLES, "everything", whose="company")
        _expense(c, acme, 20_000, "bank", "b1", "1000")
        _pay(c, acme, 40_000, {"money_account_id": bank})
    with keeper.read() as c:
        mine = FR.form_1099_report(c, PAYABLES, 2026, AT)
        mine_csv = FR.form_1099_csv(c, PAYABLES, 2026, AT)
        everyone = FR.form_1099_report(c, OWNER, 2026, AT)
    assert _vendor_row(mine, "Acme Consulting")["cash_paid"] == 60_000
    assert mine_csv.splitlines()[1] == "2026,Acme Consulting,yes,600.00,0.00,2000.00,no"
    assert mine == everyone


def test_a_private_line_the_viewer_lacks_full_detail_on_refuses_the_workpapers(keeper):
    with keeper.transaction() as c:
        authority = _contact(c, "Testland Revenue")
        made = act(c, OWNER, "tax_jurisdiction_add", {"code": "TL", "name": "Testland", "authority_id": authority,
                                                      "due_day": 20})
        tax_account = str(made.result["account_id"])
        customer = _contact(c, "Globex", kind="customer")
        _invoice(c, customer, "2026-02-10", 20_000, 1_600, tax_account)
        # Private lines are expense lines, so a remittance can only reach one through a second line on its bill.
        remit = act(c, OWNER, "bill_create", {"vendor_id": authority, "bill_date": "2026-04-01", "lines": [
            {"account_id": tax_account, "amount": 1_600, "tags": {"custom": "tax:2026-Q1"}},
            {"account_id": account(c, "6100"), "amount": 100}]})
        assert remit.status == "committed", remit
        bill = str(remit.result["bill_id"])
        assert act(c, OWNER, "bill_approve", {"bill_id": bill}).status == "committed"
        assert act(c, OWNER, "bill_mark_paid", {"bill_id": bill, "account_id": account(c, "1000")}).status == "committed"
        assert act(c, OWNER, "private_lines_set", {"account_id": account(c, "6100"), "private": True,
                                                   "others_see": "hidden"}).status == "committed"
        _grant(c, PAYABLES, "everything", whose="company")
    with keeper.read() as c:
        with pytest.raises(FinanceError) as refused:
            FR.sales_tax_report(c, PAYABLES, 2026, AT)
    assert refused.value.code == "denied"
    with keeper.transaction() as c:
        assert act(c, OWNER, "private_lines_set", {"account_id": account(c, "6100"), "private": True,
                                                   "others_see": "hidden",
                                                   "full_detail": [PAYABLES]}).status == "committed"
    with keeper.read() as c:
        mine = FR.sales_tax_report(c, PAYABLES, 2026, AT)["rows"]
        everyone = FR.sales_tax_report(c, OWNER, 2026, AT)["rows"]
    assert mine == everyone
    assert _quarter(mine, "2026-Q1") == {"taxable_sales": 20_000, "tax_collected": 1_600, "tax_remitted": 1_600,
                                         "due": 0}


def test_an_everything_grant_reads_both_workpapers_when_no_line_behind_them_is_private(keeper):
    with keeper.transaction() as c:
        _private_salaries(c)
        # Payroll with no vendor and no tax on it: nothing the two workpapers read sits on the private account.
        FA.post_entry(c, {"date": "2026-06-02", "source": "bank", "source_id": "payroll", "lines": [
            {"account_id": account(c, "6500"), "amount": 90_000}, {"account_id": account(c, "1000"), "amount": -90_000}]},
            at=AT)
        authority = _contact(c, "Testland Revenue")
        made = act(c, OWNER, "tax_jurisdiction_add", {"code": "TL", "name": "Testland", "authority_id": authority,
                                                      "due_day": 20})
        tax_account = str(made.result["account_id"])
        customer = _contact(c, "Globex", kind="customer")
        _invoice(c, customer, "2026-02-10", 20_000, 1_600, tax_account)
        remit = _remit(c, "2026-Q1")
        assert act(c, OWNER, "bill_mark_paid", {"bill_id": remit, "account_id": account(c, "1000")}).status == "committed"
        acme = _contact(c, "Acme Consulting", is_1099=True, tax_id=TIN)
        _pay(c, acme, 40_000, {"money_account_id": _bank(c)})
        _grant(c, PAYABLES, "everything", whose="company")
    with keeper.read() as c:
        assert FR.sales_tax_report(c, PAYABLES, 2026, AT) == FR.sales_tax_report(c, OWNER, 2026, AT)
        assert FR.sales_tax_csv(c, PAYABLES, 2026, AT) == FR.sales_tax_csv(c, OWNER, 2026, AT)
        assert FR.form_1099_report(c, PAYABLES, 2026, AT) == FR.form_1099_report(c, OWNER, 2026, AT)
        assert FR.form_1099_csv(c, PAYABLES, 2026, AT) == FR.form_1099_csv(c, OWNER, 2026, AT)
        assert _quarter(FR.sales_tax_report(c, PAYABLES, 2026, AT)["rows"], "2026-Q1") == {
            "taxable_sales": 20_000, "tax_collected": 1_600, "tax_remitted": 1_600, "due": 0}
        assert _vendor_row(FR.form_1099_report(c, PAYABLES, 2026, AT), "Acme Consulting")["cash_paid"] == 40_000


def test_an_everything_grant_is_refused_a_1099_with_a_private_line_behind_it(keeper):
    with keeper.transaction() as c:
        acme = _contact(c, "Acme Consulting", is_1099=True, tax_id=TIN)
        _private_salaries(c)
        # The vendor's bill has a line on the private account, so its settlement is part of the 1099 total.
        _pay(c, acme, 40_000, {"money_account_id": _bank(c)}, code="6500")
        _grant(c, PAYABLES, "everything", whose="company")
    with keeper.read() as c:
        for read in (FR.form_1099_report, FR.form_1099_csv):
            with pytest.raises(FinanceError) as refused:
                read(c, PAYABLES, 2026, AT)
            assert refused.value.code == "denied"
        # No sales tax line sits on that account, so the sales tax workpaper still reads.
        assert FR.sales_tax_report(c, PAYABLES, 2026, AT)["year"] == 2026
        assert _vendor_row(FR.form_1099_report(c, OWNER, 2026, AT), "Acme Consulting")["cash_paid"] == 40_000
    with keeper.transaction() as c:
        assert act(c, OWNER, "private_lines_set", {"account_id": account(c, "6500"), "private": True,
                                                   "others_see": "hidden",
                                                   "full_detail": [PAYABLES]}).status == "committed"
    with keeper.read() as c:
        assert FR.form_1099_report(c, PAYABLES, 2026, AT) == FR.form_1099_report(c, OWNER, 2026, AT)
        assert FR.form_1099_csv(c, PAYABLES, 2026, AT) == FR.form_1099_csv(c, OWNER, 2026, AT)


def test_1099_payments_count_in_the_year_they_were_paid(keeper):
    with keeper.transaction() as c:
        acme = _contact(c, "Acme Consulting", is_1099=True, tax_id=TIN)
        bank = _bank(c)
        _pay(c, acme, 20_000, {"money_account_id": bank}, bill_day="2025-12-01", paid_on="2026-01-05")
        _pay(c, acme, 30_000, {"money_account_id": bank}, bill_day="2025-11-01", paid_on="2025-12-28")
    with keeper.read() as c:
        y2025 = _vendor_row(FR.form_1099_report(c, OWNER, 2025, AT), "Acme Consulting")
        y2026 = _vendor_row(FR.form_1099_report(c, OWNER, 2026, AT), "Acme Consulting")
    assert (y2025["cash_paid"], y2026["cash_paid"]) == (30_000, 20_000)


@pytest.mark.parametrize("kind", ["debitCardTransaction", "creditCardTransaction"])
def test_a_card_row_on_a_bank_account_is_a_card_payment(keeper, kind):
    with keeper.transaction() as c:
        acme = _contact(c, "Acme Consulting", is_1099=True, tax_id=TIN)
        bank = _bank(c)
        card = SK.insert(c, "fin_feed_txns", {
            "money_account_id": bank, "provider_id": "t3", "txn_date": "2026-06-01", "amount": -40_000,
            "counterparty": "Acme Consulting", "currency": "USD", "created_at": AT, "created_by": "system:finance",
            "status": "posted", "raw_json": "{}", "kind": kind})
        _pay(c, acme, 40_000, {"feed_id": card})
        _pay(c, acme, 10_000, {"money_account_id": bank})
    with keeper.read() as c:
        row = _vendor_row(FR.form_1099_report(c, OWNER, 2026, AT), "Acme Consulting")
    assert (row["cash_paid"], row["card_paid_excluded"]) == (10_000, 40_000)


def test_a_payment_posted_after_its_month_closed_counts_in_the_year_it_was_paid(keeper):
    """A December row posts on the first open day after the close, and so does its re-categorization. Both count in 2025
    with the payment, so a Dec 30 payment is not moved into 2026."""
    jan = "2026-01-05T12:00:00.000000Z"
    with keeper.transaction() as c:
        acme = _contact(c, "Acme Consulting", is_1099=True, tax_id=TIN)
        bank = _bank(c)
        assert close_month(c, "2025-12", at=jan).status == "committed"      # no December row is in the books yet
        fid = _feed(c, bank, -60_000, day="2025-12-30")                      # the payment is found after the close
        assert act(c, OWNER, "categorize", {"feed_id": fid, "account_id": account(c, "6100")},
                   at=jan).status == "committed"
        assert c.execute("SELECT entry_date FROM fin_lines WHERE contact_id=?", (acme,)).fetchone()[0] == "2026-01-05"
        assert act(c, OWNER, "categorize", {"feed_id": fid, "account_id": account(c, "6900")},
                   at=jan).status == "committed"
    with keeper.read() as c:
        assert _vendor_row(FR.form_1099_report(c, OWNER, 2025, AT), "Acme Consulting")["cash_paid"] == 60_000
        assert _vendor_row(FR.form_1099_report(c, OWNER, 2026, AT), "Acme Consulting")["cash_paid"] == 0


def test_a_payment_matched_to_a_bill_after_its_month_closed_counts_once_in_the_year_paid(keeper):
    """A November row is posted to the vendor's expense, its month closes, and then the row is matched to the vendor's bill.
    The match's reclassification posts on a later open day, but it counts in 2025 with the payment: once in 2025, nothing
    in 2026."""
    with keeper.transaction() as c:
        acme = _contact(c, "Acme Consulting", is_1099=True, tax_id=TIN)
        fid = _feed(c, _bank(c), -60_000, day="2025-11-20")
        assert act(c, OWNER, "categorize", {"feed_id": fid, "account_id": account(c, "6100")},
                   at="2025-11-25T12:00:00.000000Z").status == "committed"
        assert close_month(c, "2025-11",
                       at="2025-12-05T12:00:00.000000Z").status == "committed"
        made = act(c, OWNER, "bill_create", {"vendor_id": acme, "bill_date": "2025-11-01",
                                             "lines": [{"account_id": account(c, "6100"), "amount": 60_000}]})
        assert made.status == "committed", made
        bill = str(made.result["bill_id"])
        assert act(c, OWNER, "bill_approve", {"bill_id": bill}).status == "committed"
        assert act(c, OWNER, "bill_mark_paid", {"bill_id": bill, "feed_id": fid},
                   at="2026-01-05T12:00:00.000000Z").status == "committed"
        dates = [r[0] for r in c.execute("SELECT entry_date FROM fin_entries WHERE source='bank' ORDER BY entry_date")]
        assert dates == ["2025-11-20", "2026-01-05"]
    with keeper.read() as c:
        assert _vendor_row(FR.form_1099_report(c, OWNER, 2025, AT), "Acme Consulting")["cash_paid"] == 60_000
        assert _vendor_row(FR.form_1099_report(c, OWNER, 2026, AT), "Acme Consulting")["cash_paid"] == 0


@pytest.mark.parametrize("kind, amount, card", [
    ("debitCardTransaction", -15_000, 15_000), ("creditCardTransaction", -15_000, 15_000),
    ("debitCardCredit", 15_000, -15_000), ("creditCardCredit", 15_000, -15_000),
    ("cardInternationalTransactionFee", -15_000, 15_000)])
def test_every_card_row_on_a_bank_account_is_card_money_and_a_refund_never_lowers_cash(keeper, kind, amount, card):
    with keeper.transaction() as c:
        acme = _contact(c, "Acme Consulting", is_1099=True, tax_id=TIN)
        bank = _bank(c)
        _pay(c, acme, 50_000, {"money_account_id": bank})
        fid = _feed(c, bank, amount, kind=kind)
        assert act(c, OWNER, "categorize", {"feed_id": fid, "account_id": account(c, "6100")},
                   at=AT).status == "committed"
    with keeper.read() as c:
        row = _vendor_row(FR.form_1099_report(c, OWNER, 2026, AT), "Acme Consulting")
    assert (row["cash_paid"], row["card_paid_excluded"]) == (50_000, card)


def test_only_invoices_that_carry_sales_tax_feed_the_sales_tax_workpaper(keeper):
    """An invoice with no sales tax line feeds no row, so its income account is not behind the workpaper."""
    with keeper.transaction() as c:
        authority = _contact(c, "Testland Revenue")
        made = act(c, OWNER, "tax_jurisdiction_add", {"code": "TL", "name": "Testland", "authority_id": authority,
                                                      "due_day": 20})
        tax_account = str(made.result["account_id"])
        assert act(c, OWNER, "account_create", {"code": "4100", "name": "Consulting",
                                                "type": "income"}).status == "committed"
        customer = _contact(c, "Globex", kind="customer")
        _invoice(c, customer, "2026-02-10", 20_000, 1_600, tax_account)
        untaxed = act(c, OWNER, "invoice_create", {"customer_id": customer, "invoice_date": "2026-03-05",
                                                   "lines": [{"account_id": account(c, "4100"), "amount": 5_000}]})
        assert untaxed.status == "committed", untaxed
        assert act(c, OWNER, "invoice_send", {"invoice_id": untaxed.result["invoice_id"]}).status == "committed"
        feeding = FT._sales_feeds(load(c, AT), 2026)
    assert feeding == {account(c, "4000"), tax_account}


def test_a_card_expense_matched_to_a_bill_nets_on_the_card(keeper):
    with keeper.transaction() as c:
        acme = _contact(c, "Acme Consulting", is_1099=True, tax_id=TIN)
        _card_gl(c)
        bank, program = _bank(c), _program(c)
        charge = _card_charge(c, program, 50_000, "t5")
        assert act(c, OWNER, "categorize", {"feed_id": charge, "account_id": account(c, "6100")}).status == "committed"
        _pay(c, acme, 50_000, {"feed_id": charge})
        _pay(c, acme, 240_000, {"money_account_id": bank})
    with keeper.read() as c:
        row = _vendor_row(FR.form_1099_report(c, OWNER, 2026, AT), "Acme Consulting")
    assert (row["card_paid_excluded"], row["cash_paid"]) == (50_000, 240_000)


def test_a_vendor_paid_under_a_name_before_its_contact_existed_counts(keeper):
    with keeper.transaction() as c:
        FA.post_entry(c, {"date": "2026-06-02", "source": "bank", "source_id": "early", "lines": [
            {"account_id": account(c, "6100"), "amount": 20_000, "contact_id": "Acme Consulting"},
            {"account_id": account(c, "1000"), "amount": -20_000}]}, at=AT)
        _contact(c, "Acme Consulting", is_1099=True, tax_id=TIN)
    with keeper.read() as c:
        row = _vendor_row(FR.form_1099_report(c, OWNER, 2026, AT), "Acme Consulting")
    assert row["cash_paid"] == 20_000


def test_a_partial_remittance_counts_what_was_paid(keeper):
    with keeper.transaction() as c:
        authority = _contact(c, "Testland Revenue")
        made = act(c, OWNER, "tax_jurisdiction_add", {"code": "TL", "name": "Testland", "authority_id": authority,
                                                      "due_day": 20})
        tax_account = str(made.result["account_id"])
        customer = _contact(c, "Globex", kind="customer")
        _invoice(c, customer, "2026-02-10", 20_000, 1_600, tax_account)
        bill = _remit(c, "2026-Q1")
        paid = act(c, OWNER, "bill_mark_paid", {"bill_id": bill, "amount": 1_000, "account_id": account(c, "1000")})
        assert paid.status == "committed", paid
    with keeper.read() as c:
        rows = FR.sales_tax_report(c, OWNER, 2026, AT)["rows"]
    assert _quarter(rows, "2026-Q1") == {"taxable_sales": 20_000, "tax_collected": 1_600, "tax_remitted": 1_000,
                                         "due": 600}
    with keeper.transaction() as c:
        again = act(c, OWNER, "tax_remit", {"jurisdiction": "TL", "period": "2026-Q1"})
        assert again.status == "committed", again
        assert c.execute("SELECT amount FROM fin_bill_lines WHERE bill_id=?",
                         (again.result["bill_id"],)).fetchone()[0] == 600


def test_an_exempt_income_line_is_listed_as_exempt_sales(keeper):
    with keeper.transaction() as c:
        authority = _contact(c, "Testland Revenue")
        made = act(c, OWNER, "tax_jurisdiction_add", {"code": "TL", "name": "Testland", "authority_id": authority,
                                                      "due_day": 20})
        tax_account = str(made.result["account_id"])
        customer = _contact(c, "Globex", kind="customer")
        lines = [{"account_id": account(c, "4000"), "amount": 20_000},
                 {"account_id": account(c, "4000"), "amount": 5_000, "taxable": False},
                 {"account_id": tax_account, "amount": 1_600}]
        invoice = act(c, OWNER, "invoice_create", {"customer_id": customer, "invoice_date": "2026-02-10",
                                                   "lines": lines})
        assert invoice.status == "committed", invoice
        assert act(c, OWNER, "invoice_send", {"invoice_id": invoice.result["invoice_id"]}).status == "committed"
        # Income on an invoice with no sales tax line is not taxed, so it is not an exempt sale either.
        _invoice(c, customer, "2026-03-05", 9_000)
    with keeper.read() as c:
        rows = FR.sales_tax_report(c, OWNER, 2026, AT)["rows"]
        csv_text = FR.sales_tax_csv(c, OWNER, 2026, AT)
    first = next(r for r in rows if r["period"] == "2026-Q1")
    assert (first["taxable_sales"], first["exempt_sales"], first["tax_collected"]) == (20_000, 5_000, 1_600)
    assert csv_text.splitlines()[1] == "2026-Q1,Testland,200.00,50.00,16.00,0.00,16.00"
    # The invoice read says which line is taxable (the income line given none defaults to taxable).
    with keeper.read() as c:
        read = FR.invoice(c, OWNER, str(invoice.result["invoice_id"]), AT)
    assert [x["taxable"] for x in read["lines"]][:2] == [True, False]
