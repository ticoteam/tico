"""Tax tracking (spec §11): the sales tax and 1099 workpapers and their CSV exports, the due dates the Bookkeeper owns, and
who may read the workpapers. Card payments stay out of the 1099 cash total, and a TIN never reaches a fin_* table."""
import pytest

from backend import hubdb as H
from backend.finance import actions as FA
from backend.finance import reads as FR
from backend.finance import tax as FT
from backend.finance.actions import FinanceError
from backend.finance.core import tax as T
from backend.finance.store import banking as SK
from backend.finance.store import schema
from backend.tests.test_finance_fixtures import (  # noqa: F401
    AT, OWNER, PEOPLE, account, act, books, finance_template, store)

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


def _pay(c, vendor, amount, source, bill_day="2026-05-01", paid_on="2026-06-01"):
    """A bill for one expense, approved and paid from `source` (a money account or a GL account). Returns the bill."""
    made = act(c, OWNER, "bill_create", {"vendor_id": vendor, "bill_date": bill_day,
                                         "lines": [{"account_id": account(c, "6100"), "amount": amount}]})
    assert made.status == "committed", made
    bill = str(made.result["bill_id"])
    assert act(c, OWNER, "bill_approve", {"bill_id": bill}).status == "committed"
    paid = act(c, OWNER, "bill_mark_paid", {"bill_id": bill, "date": paid_on, **source})
    assert paid.status == "committed", paid
    return bill


def _split_bill(c, vendor, parts, source):
    """A bill with one expense line per (amount, teammate) part, approved and paid from `source`."""
    lines = [{"account_id": account(c, "6100"), "amount": amount, "tags": {"teammate": teammate}}
             for amount, teammate in parts]
    made = act(c, OWNER, "bill_create", {"vendor_id": vendor, "bill_date": "2026-05-01", "lines": lines})
    assert made.status == "committed", made
    bill = str(made.result["bill_id"])
    assert act(c, OWNER, "bill_approve", {"bill_id": bill}).status == "committed"
    paid = act(c, OWNER, "bill_mark_paid", {"bill_id": bill, "date": "2026-06-01", **source})
    assert paid.status == "committed", paid


def _expense(c, vendor, amount, source, source_id, credit_code, teammate=""):
    """A bank or card row posted straight to an expense with the vendor on it, tagged with a teammate when one is given."""
    tags = {"teammate": teammate} if teammate else {}
    FA.post_entry(c, {"date": "2026-06-02", "source": source, "source_id": source_id, "lines": [
        {"account_id": account(c, "6100"), "amount": amount, "contact_id": vendor, "tags": tags},
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


def test_a_teammate_without_payables_and_receivables_cannot_read_the_workpapers(keeper):
    with keeper.read() as c:
        with pytest.raises(FinanceError) as sales:
            FR.sales_tax_report(c, PLAIN, 2026, AT)
        with pytest.raises(FinanceError) as form:
            FR.form_1099_csv(c, PLAIN, 2026, AT)
    assert (sales.value.code, form.value.code) == ("denied", "denied")


def test_a_payables_teammate_sees_only_the_1099_lines_their_view_covers(keeper):
    with keeper.transaction() as c:
        acme = _contact(c, "Acme Consulting", is_1099=True, tax_id=TIN)
        bank = _bank(c)
        _grant(c, PAYABLES, "payables")
        _grant(c, PAYABLES, "expenses", whose="own")
        _expense(c, acme, 20_000, "bank", "b1", "1000", teammate=PAYABLES)
        _expense(c, acme, 5_000, "bank", "b2", "1000", teammate="human:ben")
        _split_bill(c, acme, [(30_000, PAYABLES), (10_000, "human:ben")], {"money_account_id": bank})
    with keeper.read() as c:
        mine = FR.form_1099_report(c, PAYABLES, 2026, AT)
        mine_csv = FR.form_1099_csv(c, PAYABLES, 2026, AT)
        everyone = FR.form_1099_report(c, OWNER, 2026, AT)
    # The bill's 40,000 is settled in full; the teammate sees the 30,000 of its lines their view covers.
    assert _vendor_row(mine, "Acme Consulting")["cash_paid"] == 50_000
    assert mine_csv.splitlines()[1] == "2026,Acme Consulting,yes,500.00,0.00,2000.00,no"
    assert _vendor_row(everyone, "Acme Consulting")["cash_paid"] == 65_000


def test_a_remittance_bill_with_a_private_line_is_left_out_whole(keeper):
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
        _grant(c, PAYABLES, "receivables")
        _grant(c, PAYABLES, "payables")
    with keeper.read() as c:
        mine = FR.sales_tax_report(c, PAYABLES, 2026, AT)["rows"]
        everyone = FR.sales_tax_report(c, OWNER, 2026, AT)["rows"]
    # The invoice is visible to them, the paid remittance is not: its tax line goes out with the whole bill.
    assert _quarter(mine, "2026-Q1") == {"taxable_sales": 20_000, "tax_collected": 1_600, "tax_remitted": 0,
                                         "due": 1_600}
    assert _quarter(everyone, "2026-Q1") == {"taxable_sales": 20_000, "tax_collected": 1_600, "tax_remitted": 1_600,
                                             "due": 0}


def test_a_debit_card_row_on_a_bank_account_is_a_card_payment(keeper):
    with keeper.transaction() as c:
        acme = _contact(c, "Acme Consulting", is_1099=True, tax_id=TIN)
        bank = _bank(c)
        debit = SK.insert(c, "fin_feed_txns", {
            "money_account_id": bank, "provider_id": "t3", "txn_date": "2026-06-01", "amount": -40_000,
            "counterparty": "Acme Consulting", "currency": "USD", "created_at": AT, "created_by": "system:finance",
            "status": "posted", "raw_json": "{}", "kind": "debitCardTransaction"})
        _pay(c, acme, 40_000, {"feed_id": debit})
        _pay(c, acme, 10_000, {"money_account_id": bank})
    with keeper.read() as c:
        row = _vendor_row(FR.form_1099_report(c, OWNER, 2026, AT), "Acme Consulting")
    assert (row["cash_paid"], row["card_paid_excluded"]) == (10_000, 40_000)


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
