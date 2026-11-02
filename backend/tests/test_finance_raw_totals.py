"""Spreadsheet history as raw totals (spec §4.1): each month posts balanced against Opening balance equity and the
reads show it, the same file posts nothing twice, an unmapped line is refused and named, a closed month waits for the
owner, a bot's import waits for the owner's approval, and reversed months post the file again once all are reversed."""
import sqlite3
from pathlib import Path

import pytest

from backend import hubdb as H
from backend.finance import actions as FA
from backend.finance import reads as FR
from backend.finance.core import raw_totals as RT
from backend.finance.core.errors import CONFLICT, DENIED, INVALID, PERIOD_CLOSED, FinanceError
from backend.finance.core.ledger import name_key
from backend.finance.raw_totals import OUTSIDE
from backend.finance.store import schema as SCH
from backend.tests.test_finance_fixtures import (  # noqa: F401
    AT, MERCURY, OWNER, FakeMercury, account, act, books, connect, finance_template, spend, store, sync_once)

CSV = (Path(__file__).parent / "fixtures" / "finance" / "raw_totals.csv").read_text()
MAPPING = {"Software subscriptions": "6100", "Office rent": "6900", "Sales revenue": "4000", "Cash at bank": "1000"}


def _body(mapping: dict[str, str] | None = None, **extra: object) -> dict[str, object]:
    return {"csv": CSV, "basis": "cash", "mapping": mapping or MAPPING, "source_name": "Acme history", **extra}


def _count(c, table: str) -> int:
    return c.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_each_month_balances_and_the_reads_show_the_totals(books):
    with books.transaction() as c:
        r = act(c, OWNER, "import_raw_totals", _body())
        assert r.status == "committed", r.reason
        assert [m["month"] for m in r.result["months"]] == ["2026-01", "2026-02", "2026-03"]
        sums = c.execute("SELECT e.id, sum(l.amount) FROM fin_entries e JOIN fin_lines l ON l.entry_id=e.id "
                         "WHERE e.source='import' GROUP BY e.id").fetchall()
        assert len(sums) == 3 and all(total == 0 for _, total in sums)
        assert tuple(c.execute("SELECT basis, periods, row_count FROM fin_raw_imports").fetchone()) == ("cash", 3, 12)
        pnl = FR.pnl(c, OWNER, "2026-01-01", "2026-03-31", at=AT)
        revenue = next(row for row in pnl["rows"] if row["code"] == "4000")
        assert abs(revenue["total"]) == 410_000 + 395_000 + 430_000
        sheet = FR.balance_sheet(c, OWNER, as_of="2026-03-31", at=AT)
        assert next(row for row in sheet["assets"] if row["code"] == "1000")["balance"] == 340_000


def test_the_same_file_posts_nothing_again(books):
    with books.transaction() as c:
        first = act(c, OWNER, "import_raw_totals", _body())
        before = _count(c, "fin_entries")
        by_id = {"Software subscriptions": account(c, "6100"), "Office rent": account(c, "6900"),
                 "Sales revenue": account(c, "4000"), "Cash at bank": account(c, "1000")}
        again = act(c, OWNER, "import_raw_totals", _body(by_id, source_name="A copy"))
        assert again.status == "committed" and again.result["already_imported"]
        assert again.result["batch_id"] == first.result["batch_id"]
        dry = act(c, OWNER, "import_raw_totals", _body(), dry_run=True)
        assert dry.impact["already_imported"] == first.result["batch_id"]
        assert _count(c, "fin_entries") == before and _count(c, "fin_raw_imports") == 1


def test_an_unmapped_line_is_refused_and_named(books):
    with books.transaction() as c:
        before = _count(c, "fin_entries")
        short = {k: v for k, v in MAPPING.items() if k != "Sales revenue"}
        dry = act(c, OWNER, "import_raw_totals", _body(short), dry_run=True)
        assert dry.status == "failed" and dry.code == INVALID and "Sales revenue" in dry.reason
        real = act(c, OWNER, "import_raw_totals", _body(short))
        assert real.status == "failed" and "Sales revenue" in real.reason
        assert _count(c, "fin_entries") == before and _count(c, "fin_raw_imports") == 0


def test_a_closed_month_waits_for_the_owner_to_reopen_it(books):
    with books.transaction() as c:
        assert act(c, OWNER, "close_period", {"month": "2026-02"}).status == "committed"
        closed = act(c, OWNER, "import_raw_totals", _body())
        assert closed.status == "failed" and closed.code == PERIOD_CLOSED
        assert act(c, OWNER, "reopen_period", {"month": "2026-02"}).status == "committed"
        assert act(c, OWNER, "import_raw_totals", _body()).status == "committed"


def test_the_bookkeeper_asks_and_the_owner_approves(books):
    with books.transaction() as c:
        assert act(c, OWNER, "right_grant", {"teammate": "bot:coder", "right": "finance.book"}).status == "committed"
        # Its view covers the Web team only, so the untagged rent and bank lines are not its to write.
        assert act(c, "bot:coder", "import_raw_totals", _body(), "bot-narrow").code == DENIED
        assert act(c, OWNER, "view_grant", {"subject": "bot:coder", "what": "everything"}).status == "committed"
        asked = act(c, "bot:coder", "import_raw_totals", _body(), "bot-import")
        assert asked.status == "needs_approval" and asked.approver == OWNER
        assert _count(c, "fin_raw_imports") == 0
        H.approval_decide(c, OWNER, asked.approval_id, "approved")
        done = FA.approval_decided(c, asked.approval_id, AT)
        assert done.status == "committed" and len(done.result["months"]) == 3
        assert _count(c, "fin_raw_imports") == 1


def test_reversed_months_post_the_file_again_once_all_are_reversed(books):
    with books.transaction() as c:
        first = act(c, OWNER, "import_raw_totals", _body())
        entries = {m["month"]: m["entry_id"] for m in first.result["months"]}
        assert act(c, OWNER, "journal", {"reverses": entries["2026-02"]}).status == "committed"
        partial = act(c, OWNER, "import_raw_totals", _body())
        assert partial.status == "failed" and partial.code == CONFLICT
        for month in ("2026-01", "2026-03"):
            assert act(c, OWNER, "journal", {"reverses": entries[month]}).status == "committed"
        again = act(c, OWNER, "import_raw_totals", _body())
        assert again.status == "committed" and again.result["batch_id"] != first.result["batch_id"]
        imports = FR.raw_imports(c, OWNER, at=AT)["imports"]
        assert {i["batch_id"]: i["live"] for i in imports} == {first.result["batch_id"]: False,
                                                               again.result["batch_id"]: True}


def test_a_month_with_transactions_takes_no_raw_totals(books):
    with books.transaction() as c:
        spend(c, "6100", 5000, day="2026-02-10")
        before = _count(c, "fin_entries")
        dry = act(c, OWNER, "import_raw_totals", _body(), dry_run=True)
        assert dry.status == "failed" and dry.code == CONFLICT and "2026-02" in dry.reason
        real = act(c, OWNER, "import_raw_totals", _body())
        assert real.status == "failed" and real.code == CONFLICT
        assert _count(c, "fin_entries") == before and _count(c, "fin_raw_imports") == 0
        # Once the transaction is reversed the month is empty again, and the file posts.
        usage = c.execute("SELECT id FROM fin_entries WHERE source='usage'").fetchone()[0]
        assert act(c, OWNER, "journal", {"reverses": usage}).status == "committed"
        assert act(c, OWNER, "import_raw_totals", _body()).status == "committed"


def test_a_month_with_a_posted_bank_row_takes_no_raw_totals_even_when_its_entry_rolled_forward(books):
    """A bank row of a month that posted refuses that month's raw totals, though its entry is dated outside the month: a
    row posted while February was closed lands in the open period (open_day), so no itemized entry is found in February."""
    fake = FakeMercury()
    connect(books, fake)
    with books.transaction() as c:
        assert act(c, OWNER, "close_period", {"month": "2026-02"}).status == "committed"
    fake.txns["txn-feb"] = _feed("txn-feb", -1_500, "2026-02-10")
    sync_once(books, fake)
    with books.transaction() as c:
        fid = c.execute("SELECT id FROM fin_feed_txns WHERE provider_id='txn-feb'").fetchone()[0]
        assert act(c, OWNER, "categorize", {"feed_id": fid, "account_id": account(c, "6900")}).status == "committed"
        entry = c.execute("SELECT entry_id FROM fin_feed_txns WHERE id=?", (fid,)).fetchone()[0]
        assert act(c, OWNER, "reopen_period", {"month": "2026-02"}).status == "committed"
        refused = act(c, OWNER, "import_raw_totals", _body())
        assert refused.status == "failed" and refused.code == CONFLICT, refused
        assert "2026-02" in refused.reason and "bank or card" in refused.reason, refused.reason
        # Reversing the row's entry clears the month, and the file posts.
        assert act(c, OWNER, "journal", {"reverses": entry}).status == "committed"
        again = act(c, OWNER, "import_raw_totals", _body())
        assert again.status == "committed", again.reason


def test_a_sheet_past_the_64_bit_range_is_refused_by_the_dry_run(books):
    # Twelve 18-digit rows net past the ledger's 64-bit range, and twenty rows that net to zero still sum past it: each
    # is refused at the dry run, and the commit refuses it too with nothing posted.
    netting = [(f"Plus {k:02d}", "999999999999999999") for k in range(10)]
    netting += [(f"Minus {k:02d}", "-999999999999999999") for k in range(10)]
    for pairs in ([(f"Big {k:02d}", "999999999999999999") for k in range(12)], netting):
        text = "month,line,amount\n" + "".join(f"2026-01,{name},{amount}\n" for name, amount in pairs)
        mapping = {name: "6100" for name, _ in pairs}
        with books.transaction() as c:
            before = _count(c, "fin_entries")
            dry = act(c, OWNER, "import_raw_totals", _body(mapping, csv=text), dry_run=True)
            assert dry.status == "failed" and dry.code == INVALID and "out of range" in dry.reason
            real = act(c, OWNER, "import_raw_totals", _body(mapping, csv=text))
            assert real.status == "failed" and real.code == INVALID
            assert _count(c, "fin_entries") == before and _count(c, "fin_raw_imports") == 0


def test_a_bad_sheet_is_refused_and_the_same_rows_hash_the_same():
    bad = {"an unknown column": "month,line,amount,note\n2026-01,Rent,100,x\n",
           "a bad month": "month,line,amount\n2026-1,Rent,100\n",
           "a fraction": "month,line,amount\n2026-01,Rent,100.50\n",
           "a repeated line": "month,line,amount\n2026-01,Rent,100\n2026-01,rent,50\n",
           "only zeros": "month,line,amount\n2026-01,Rent,0\n"}
    for text in bad.values():
        with pytest.raises(FinanceError):
            RT.parse(text, "cash")
    one = RT.parse("month,line,amount\n2026-01,Rent,100\n2026-02,Rent,200\n", "cash")
    two = RT.parse("month,line,amount\n2026-02,Rent,200\n2026-01,Rent,100\n", "cash")
    assert one.digest == two.digest
    assert one.digest != RT.parse("month,line,amount\n2026-01,Rent,100\n2026-02,Rent,200\n", "accrual").digest


def test_amounts_and_months_are_ascii_digits_only():
    # Arabic-Indic and fullwidth digits are \d in Python's regex, so the sheet reader itself must refuse them.
    for digits in ("١٢٣٤", "１２３４"):
        with pytest.raises(FinanceError):
            RT.parse(f"month,line,amount\n2026-01,Rent,{digits}\n", "cash")
    with pytest.raises(FinanceError):
        RT.parse("month,line,amount\n٢٠٢٦-01,Rent,100\n", "cash")


def test_a_leading_byte_order_mark_is_stripped():
    sheet = RT.parse("﻿month,line,amount\n2026-01,Rent,100\n", "cash")
    assert [(r.month, r.line, r.amount) for r in sheet.rows] == [("2026-01", "Rent", 100)]
    assert sheet.digest == RT.parse("month,line,amount\n2026-01,Rent,100\n", "cash").digest


def test_internal_runs_of_spaces_collapse_in_line_names():
    sheet = RT.parse("month,line,amount\n2026-01,Office  Rent,100\n2026-02,  Office \t Rent ,200\n", "cash")
    assert {r.line for r in sheet.rows} == {"Office Rent"}
    assert RT.unmapped(sheet, {name_key("Office Rent")}) == []


def test_a_sheet_whose_amounts_all_end_in_00_gets_a_dry_run_warning_and_no_refusal():
    dollars = RT.parse("month,line,amount\n2026-01,Rent,290000\n2026-02,Rent,300000\n", "cash")
    assert RT.cents_warning(dollars) != ""
    assert RT.cents_warning(RT.parse("month,line,amount\n2026-01,Rent,290000\n2026-02,Rent,300050\n", "cash")) == ""
    assert RT.cents_warning(RT.parse("month,line,amount\n2026-01,Rent,300000\n", "cash")) == ""


def test_a_scoped_replay_is_denied_the_same_as_a_fresh_import(books):
    with books.transaction() as c:
        first = act(c, OWNER, "import_raw_totals", _body())
        assert act(c, OWNER, "right_grant", {"teammate": "bot:coder", "right": "finance.book"}).status == "committed"
        replay = act(c, "bot:coder", "import_raw_totals", _body(), "scoped-replay")
        fresh = act(c, "bot:coder", "import_raw_totals", {**_body(), "csv": "month,line,amount\n2026-05,Office rent,100\n"},
                    "scoped-fresh")
        assert replay.code == fresh.code == DENIED and replay.reason == fresh.reason
        dry = act(c, "bot:coder", "import_raw_totals", _body(), dry_run=True)
        assert dry.code == DENIED
        for refused in (replay, dry):
            assert first.result["batch_id"] not in str(refused.as_dict())


def test_a_scoped_caller_hitting_an_occupied_month_sees_no_entry(books):
    with books.transaction() as c:
        first = act(c, OWNER, "import_raw_totals", _body())
        jan_entry = str(first.result["months"][0]["entry_id"])
        assert act(c, OWNER, "right_grant", {"teammate": "bot:coder", "right": "finance.book"}).status == "committed"
        assert act(c, OWNER, "view_grant", {"subject": "bot:coder", "what": "expenses", "whose": "team",
                                            "whose_team": "web"}).status == "committed"
        # Web's software lines net to zero, so the month needs no untagged offset line: the view covers the whole file
        # and the refusal can only come from January being occupied.
        web_only = ("month,line,amount,team\n2026-{m},Software subscriptions,9000,web\n"
                    "2026-{m},Software refunds,-9000,web\n")
        mapping = {"Software subscriptions": "6100", "Software refunds": "6100"}
        april = {"csv": web_only.format(m="04"), "basis": "cash", "mapping": mapping}
        january = {"csv": web_only.format(m="01"), "basis": "cash", "mapping": mapping}
        assert act(c, "bot:coder", "import_raw_totals", april, "scoped-april").status == "needs_approval"
        held = act(c, "bot:coder", "import_raw_totals", january, "scoped-jan")
        assert held.code == DENIED and held.reason == OUTSIDE and jan_entry not in str(held.as_dict())
        owner = act(c, OWNER, "import_raw_totals", january, "owner-jan")
        assert owner.code == CONFLICT and jan_entry in owner.reason


def test_reports_mark_the_months_an_import_states_on_another_basis(books):
    months = [{"month": m, "basis": "cash"} for m in ("2026-01", "2026-02", "2026-03")]
    with books.transaction() as c:
        assert act(c, OWNER, "import_raw_totals", _body()).status == "committed"
        assert FR.pnl(c, OWNER, "2026-01-01", "2026-03-31", basis="accrual", at=AT)["imported"] == months
        assert FR.pnl(c, OWNER, "2026-02-01", "2026-02-28", basis="accrual", at=AT)["imported"] == [months[1]]
        assert FR.pnl(c, OWNER, "2026-01-01", "2026-03-31", basis="cash", at=AT)["imported"] == []
        assert FR.pnl(c, "human:cara", "2026-01-01", "2026-03-31", basis="accrual", at=AT)["imported"] == []
        act(c, OWNER, "view_grant", {"subject": "human:dan", "what": "everything"})
        assert FR.pnl(c, "human:dan", "2026-01-01", "2026-03-31", basis="accrual", at=AT)["imported"] == months
        # The sheet and the budgets have no basis of their own: they are stated in the book's.
        assert FR.balance_sheet(c, OWNER, as_of="2026-03-31", at=AT)["imported"] == []
        c.execute("UPDATE fin_books SET basis='accrual'")
        assert FR.balance_sheet(c, OWNER, as_of="2026-03-31", at=AT)["imported"] == months
        assert FR.budget_vs_actual(c, OWNER, "2026-02-01", "2026-02-28", at=AT)["imported"] == [months[1]]


def test_a_grantee_without_payroll_detail_learns_nothing_from_an_import(books):
    payroll = "month,line,amount\n2026-{m},Payroll salaries,{a}\n"
    mapping = {"Payroll salaries": "6200"}
    with books.transaction() as c:
        made = act(c, OWNER, "account_create", {"code": "6200", "name": "Payroll", "type": "expense",
                                                "parent_id": account(c, "6000")})
        pay = str(made.result["account_id"])
        assert act(c, OWNER, "private_lines_set", {"account_id": pay, "others_see": "totals"}).status == "committed"
        jan = {"csv": payroll.format(m="01", a=500_000), "basis": "cash", "mapping": mapping, "source_name": "Payroll"}
        first = act(c, OWNER, "import_raw_totals", jan)
        assert first.status == "committed", first.reason
        batch, jan_entry = str(first.result["batch_id"]), str(first.result["months"][0]["entry_id"])
        assert act(c, OWNER, "right_grant", {"teammate": "human:dan", "right": "finance.book"}).status == "committed"
        # Everything, but no payroll detail: each refusal is the one a fresh import of payroll gets.
        assert act(c, OWNER, "view_grant", {"subject": "human:dan", "what": "everything"}).status == "committed"
        fresh = act(c, "human:dan", "import_raw_totals", {**jan, "csv": payroll.format(m="02", a=500_000)}, "dan-feb")
        assert fresh.code == DENIED and fresh.reason == OUTSIDE
        replay = act(c, "human:dan", "import_raw_totals", jan, "dan-replay")
        assert replay.code == DENIED and replay.reason == OUTSIDE and batch not in str(replay.as_dict())
        guessed = act(c, "human:dan", "import_raw_totals", {**jan, "csv": payroll.format(m="01", a=500_001)},
                      "dan-guess")
        assert guessed.code == DENIED and guessed.reason == OUTSIDE and jan_entry not in str(guessed.as_dict())
        # Positive control: a line she can see, in an open month, passes the view check and asks for approval.
        software = {"csv": "month,line,amount\n2026-04,Software subscriptions,9000\n", "basis": "cash",
                    "mapping": {"Software subscriptions": "6100"}}
        assert act(c, "human:dan", "import_raw_totals", software, "dan-april").status == "needs_approval"
        # Full detail for her turns the same replay into already_imported, which names the batch.
        assert act(c, OWNER, "private_lines_set", {"account_id": pay, "others_see": "totals",
                                                   "full_detail": ["human:dan"]}).status == "committed"
        again = act(c, "human:dan", "import_raw_totals", jan, "dan-replay-detail")
        assert again.result["already_imported"] is True and again.result["batch_id"] == batch


def test_a_posting_dated_in_a_raw_totals_month_is_refused_until_its_raw_entry_is_reversed(books):
    """§4.1: a bill or journal dated in a month holding live raw totals is refused by the posting path itself. The raw
    entry's reversal, the raw import and every other reversal still post, and the refused bill posts once it is undone."""
    with books.transaction() as c:
        r = act(c, OWNER, "import_raw_totals", _body())
        assert r.status == "committed", r.reason
        feb = next(m["entry_id"] for m in r.result["months"] if m["month"] == "2026-02")
        vendor = str(act(c, OWNER, "contact_create", {"name": "Acme Supplies", "kind": "vendor"}).result["contact_id"])
        bill = str(act(c, OWNER, "bill_create", {"vendor_id": vendor, "bill_date": "2026-02-10", "lines": [
            {"account_id": account(c, "6100"), "amount": 5_000}]}).result["bill_id"])
        before = _count(c, "fin_entries")
        approved = act(c, OWNER, "bill_approve", {"bill_id": bill})
        stray = act(c, OWNER, "journal", {"date": "2026-02-20", "memo": "stray", "lines": [
            {"account_id": account(c, "6900"), "amount": 10_000}, {"account_id": account(c, "1000"), "amount": -10_000}]})
        for refused in (approved, stray):
            assert refused.status == "failed" and refused.code == CONFLICT and "raw totals" in refused.reason
        assert _count(c, "fin_entries") == before
        assert act(c, OWNER, "journal", {"reverses": feb}).status == "committed"
        assert act(c, OWNER, "bill_approve", {"bill_id": bill}).status == "committed"


def _feed(raw_id: str, amount: int, day: str) -> dict[str, object]:
    return {**MERCURY["txn_bank_fee"], "id": raw_id, "amount": amount, "createdAt": f"{day}T10:00:00Z",
            "postedAt": f"{day}T10:00:00Z"}


def _feed_state(c):
    return c.execute("SELECT status, entry_id FROM fin_feed_txns WHERE provider_id='txn-feb'").fetchone()


def test_a_feed_row_in_a_raw_totals_month_is_covered_history_and_posts_nowhere(books):
    """§4.1: a settled feed row dated on or before the latest raw-totals month stays unposted as covered history, out
    of the review queues, and still counts toward reconciling the bank. Categorize refuses it with the reason."""
    fake = FakeMercury()
    with books.transaction() as c:
        assert act(c, OWNER, "import_raw_totals", _body()).status == "committed"
    connect(books, fake)
    fake.txns["txn-feb"] = _feed("txn-feb", -1_500, "2026-02-10")
    sync_once(books, fake)
    with books.transaction() as c:
        status, entry = _feed_state(c)
        assert status == "covered" and entry is None, (status, entry)
        fid, money = c.execute("SELECT id, money_account_id FROM fin_feed_txns WHERE provider_id='txn-feb'").fetchone()
        refused = act(c, OWNER, "categorize", {"feed_id": fid, "account_id": account(c, "6900")})
        assert refused.status == "failed" and refused.code == CONFLICT and "covered" in refused.reason, refused
        made = act(c, OWNER, "reconcile", {"money_account_id": money, "statement_end": "2026-02-28",
                                           "statement_balance": -1_500})
        assert made.result["cleared"] == 1 and made.result["outstanding"] == 0, made.result


def test_reversing_the_months_a_covered_row_sits_behind_lets_it_post_once(books):
    """§4.1: a reversal of a later month leaves a covered row covered; reversing back past it lets it settle, once, as
    if it had just arrived."""
    fake = FakeMercury()
    with books.transaction() as c:
        r = act(c, OWNER, "import_raw_totals", _body())
        assert r.status == "committed", r.reason
        months = {m["month"]: m["entry_id"] for m in r.result["months"]}
    connect(books, fake)
    fake.txns["txn-feb"] = _feed("txn-feb", -1_500, "2026-02-10")
    sync_once(books, fake)
    with books.transaction() as c:
        assert act(c, OWNER, "journal", {"reverses": months["2026-03"]}).status == "committed"
        assert _feed_state(c)[0] == "covered"
        assert act(c, OWNER, "journal", {"reverses": months["2026-02"]}).status == "committed"
        status, entry = _feed_state(c)
        assert status == "posted" and entry is not None, (status, entry)
        assert c.execute("SELECT count(*) FROM fin_entries WHERE source='bank' AND status='posted'").fetchone()[0] == 1


# The same history as a sheet with no cash line: its months post against Opening balance equity alone.
PL_MAPPING = {"Software subscriptions": "6100", "Sales revenue": "4000"}
PL_CSV = ("month,line,amount,team\n2026-01,Sales revenue,250000,sales\n2026-03,Software subscriptions,30000,web\n"
          "2026-03,Sales revenue,40000,sales\n")
SHEETS = {"cash": (MAPPING, CSV), "pl": (PL_MAPPING, PL_CSV)}


def _bank_gl(c) -> tuple[int, int]:
    """The checking account's GL line through the day, and the provider's balance the books hold for it."""
    line = next(r["balance"] for r in FR.balance_sheet(c, OWNER, at=AT)["assets"] if r["code"] == "1000")
    held = c.execute("SELECT balance FROM fin_money_accounts WHERE provider_account_id='acct-chk'").fetchone()[0]
    return int(line), int(held)


@pytest.mark.parametrize("sheet", ["cash", "pl"])
def test_the_bank_line_is_the_providers_balance_across_covered_history(books, sheet):
    """§4.1 opening: a row before the first raw-totals month and one inside the sheet are covered history, an uncovered
    row after it posts, and the opening balance makes the bank's GL line the provider's balance, with or without a cash
    line in the sheet."""
    mapping, csv = SHEETS[sheet]
    fake = FakeMercury()
    fake.txns["txn-dec"] = _feed("txn-dec", -1_000.0, "2025-12-15")
    fake.txns["txn-feb"] = _feed("txn-feb", -1_500.0, "2026-02-10")
    fake.txns["txn-apr"] = _feed("txn-apr", -400.0, "2026-04-05")
    with books.transaction() as c:
        assert act(c, OWNER, "import_raw_totals", _body(mapping, csv=csv)).status == "committed"
    connect(books, fake)
    sync_once(books, fake)
    with books.transaction() as c:
        line, held = _bank_gl(c)
        assert line == held, (line, held)
        assert c.execute("SELECT count(*) FROM fin_feed_txns WHERE status='covered'").fetchone()[0] == 2


@pytest.mark.parametrize("sheet", ["cash", "pl"])
def test_a_backfill_that_starts_inside_the_sheet_still_ties_the_bank_line(books, sheet):
    """§4.1 opening: the feed begins in the middle of the sheet, so the opening sits before the first raw-totals month,
    not before the first row, and the bank line still equals the provider's balance."""
    mapping, csv = SHEETS[sheet]
    fake = FakeMercury()
    fake.txns["txn-feb"] = _feed("txn-feb", -1_500.0, "2026-02-10")
    fake.txns["txn-apr"] = _feed("txn-apr", -400.0, "2026-04-05")
    with books.transaction() as c:
        assert act(c, OWNER, "import_raw_totals", _body(mapping, csv=csv)).status == "committed"
    connect(books, fake)
    sync_once(books, fake)
    with books.transaction() as c:
        line, held = _bank_gl(c)
        assert line == held, (line, held)


def test_reversing_sheet_months_moves_the_bank_line_and_lets_the_covered_row_post(books):
    """§4.1 opening: reversing the latest month moves the raw movement out of the bank line and keeps it equal to the
    provider's balance; reversing the month behind a covered row lets that row settle, and the line still ties."""
    fake = FakeMercury()
    fake.txns["txn-feb"] = _feed("txn-feb", -1_500.0, "2026-02-10")
    fake.txns["txn-apr"] = _feed("txn-apr", -400.0, "2026-04-05")
    with books.transaction() as c:
        r = act(c, OWNER, "import_raw_totals", _body())
        assert r.status == "committed", r.reason
        months = {m["month"]: m["entry_id"] for m in r.result["months"]}
    connect(books, fake)
    sync_once(books, fake)
    with books.transaction() as c:
        assert act(c, OWNER, "journal", {"reverses": months["2026-03"]}).status == "committed"
        line, held = _bank_gl(c)
        assert line == held, (line, held)
        assert act(c, OWNER, "journal", {"reverses": months["2026-02"]}).status == "committed"
        assert _feed_state(c)[0] == "posted"
        line, held = _bank_gl(c)
        assert line == held, (line, held)


def test_the_first_reconcile_to_the_latest_raw_month_end_is_done_with_no_difference(books):
    """§4.1 opening: the first reconciliation starts from the bank's own balance before its first row, covered history
    included, so reconciling to the sheet's last month-end (before the uncovered row) is done with no difference, and
    the books agree with the statement."""
    fake = FakeMercury()
    fake.txns["txn-dec"] = _feed("txn-dec", -1_000.0, "2025-12-15")
    fake.txns["txn-feb"] = _feed("txn-feb", -1_500.0, "2026-02-10")
    fake.txns["txn-apr"] = _feed("txn-apr", -400.0, "2026-04-05")
    with books.transaction() as c:
        assert act(c, OWNER, "import_raw_totals", _body()).status == "committed"
    connect(books, fake)
    sync_once(books, fake)
    with books.transaction() as c:
        line, held = _bank_gl(c)
        bank = str(c.execute("SELECT id FROM fin_money_accounts WHERE provider_account_id='acct-chk'").fetchone()[0])
        # The bank's balance on 31 March: today's less the 5 April row (-400.00, 40,000 in cents).
        done = act(c, OWNER, "reconcile", {"money_account_id": bank, "statement_end": "2026-03-31",
                                           "statement_balance": held + 40_000})
        assert (done.result["status"], done.result["difference"], done.result["book_difference"]) == ("done", 0, 0)
        assert line == held, (line, held)


def test_migration_78_widens_the_feed_status_keeps_its_rows_and_runs_twice(books):
    """Migration 78 on an install whose fin_feed_txns predates the covered status (its CHECK has no 'covered'): the
    rebuild keeps every row and its entry_line_id, the table's triggers come back, 'covered' is accepted afterwards, and
    a second run changes nothing. Other statuses stay refused."""
    fake = FakeMercury()
    connect(books, fake)
    with books.transaction() as c:
        money = c.execute("SELECT id FROM fin_money_accounts ORDER BY id LIMIT 1").fetchone()[0]

        def triggers() -> list[str]:
            return sorted(r[0] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='fin_feed_txns'"))

        def sql() -> str:
            return str(c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='fin_feed_txns'").fetchone()[0])

        def state() -> list[tuple]:
            return c.execute("SELECT id, status, amount, entry_line_id FROM fin_feed_txns ORDER BY id").fetchall()

        before_triggers = triggers()
        # The table as migration 77 left it: the same columns, and a status CHECK without 'covered'.
        current = sql()
        c.execute("DROP TABLE fin_feed_txns")
        c.execute(current.replace("'ignored','covered'", "'ignored'"))
        assert "'covered'" not in sql() and "entry_line_id" in sql()
        c.executemany("INSERT INTO fin_feed_txns(id, money_account_id, provider_id, txn_date, amount, currency, status, "
                      "created_at, created_by, entry_line_id) VALUES(?,?,?,?,?,?,?,'2026-02-10T00:00:00Z','bot',?)",
                      [("f1", money, "p1", "2026-02-10", -1_500, "USD", "posted", "L1"),
                       ("f2", money, "p2", "2026-02-11", 900, "USD", "new", None)])
        rows = state()

        SCH.covered_history(c)
        assert state() == rows
        assert "entry_line_id" in [r[1] for r in c.execute("PRAGMA table_info(fin_feed_txns)")]
        assert triggers() == before_triggers
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("UPDATE fin_feed_txns SET status='bogus' WHERE id='f2'")
        c.execute("UPDATE fin_feed_txns SET status='covered' WHERE id='f2'")
        rows = state()
        table = sql()

        SCH.covered_history(c)
        assert state() == rows and sql() == table
        assert triggers() == before_triggers


def _opening_entry(c) -> tuple[str, str, int]:
    """The live opening balance entry on the checking line: (entry id, day, amount on that line)."""
    row = c.execute("SELECT e.id, e.entry_date, l.amount FROM fin_entries e JOIN fin_lines l ON l.entry_id=e.id "
                    "JOIN fin_accounts a ON a.id=l.account_id WHERE e.source='sync' AND e.source_id LIKE 'opening:%' "
                    "AND e.status='posted' AND e.reverses_id IS NULL AND a.code='1000'").fetchone()
    return str(row[0]), str(row[1]), int(row[2])


PASSES = [f"2026-10-15T{h:02d}:30:00.000000Z" for h in (13, 14, 15)]


def test_a_row_the_feed_never_sees_is_a_difference_the_opening_does_not_absorb(books):
    """Phase 2 #1 (§4.1 opening): once the backfill has finished the opening moves only by what changed in the books,
    never by a fresh balance read. The bank holds a row the feed never sends: later passes leave the opening where it
    was, the bank line stays short of the statement, and reconciliation reports that difference."""
    fake = FakeMercury()
    fake.txns["txn-feb"] = _feed("txn-feb", -1_500, "2026-02-10")
    fake.balances["acct-chk"] = -500
    connect(books, fake)
    sync_once(books, fake)
    with books.transaction() as c:
        opening = _opening_entry(c)
        assert opening[1:] == ("2026-02-09", 100_000), opening
        assert _bank_gl(c) == (-50_000, -50_000)
    fake.balances["acct-chk"] = -800                    # 300.00 the bank holds that the feed never sends
    for at in PASSES:
        sync_once(books, fake, at)
    with books.transaction() as c:
        assert _opening_entry(c) == opening
        assert _bank_gl(c) == (-50_000, -80_000)
        bank = str(c.execute("SELECT id FROM fin_money_accounts WHERE provider_account_id='acct-chk'").fetchone()[0])
        made = act(c, OWNER, "reconcile", {"money_account_id": bank, "statement_end": "2026-10-14",
                                           "statement_balance": -80_000})
        assert made.status == "committed", made.reason
        assert made.result["book_difference"] == -30_000, made.result     # 300.00 short of the statement
        assert made.result["status"] == "open"


def test_an_opening_does_not_block_a_sheet_that_reaches_back_past_it(books):
    """Phase 2 #3 (§4.1 opening): connect first, so the opening exists before any import. The sheet's first month is
    before the opening's day, so the import moves the opening back before that month instead of being blocked by it,
    and the bank line still equals the provider's balance."""
    fake = FakeMercury()
    fake.txns["txn-feb"] = _feed("txn-feb", -1_500, "2026-02-10")
    fake.balances["acct-chk"] = -500
    connect(books, fake)
    sync_once(books, fake)
    with books.transaction() as c:
        assert _opening_entry(c)[1] == "2026-02-09"
    with books.transaction() as c:
        r = act(c, OWNER, "import_raw_totals", _body(PL_MAPPING, csv=PL_CSV))
        assert r.status == "committed", r.reason
        assert _bank_gl(c) == (-50_000, -50_000)
        assert _opening_entry(c)[1] == "2025-12-31"
