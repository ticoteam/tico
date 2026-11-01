"""Spreadsheet history as raw totals (spec §4.1): each month posts balanced against Opening balance equity and the
reads show it, the same file posts nothing twice, an unmapped line is refused and named, a closed month waits for the
owner, a bot's import waits for the owner's approval, and reversed months post the file again once all are reversed."""
from pathlib import Path

import pytest

from backend import hubdb as H
from backend.finance import actions as FA
from backend.finance import reads as FR
from backend.finance.core import raw_totals as RT
from backend.finance.core.errors import CONFLICT, DENIED, INVALID, PERIOD_CLOSED, FinanceError
from backend.finance.raw_totals import OUTSIDE
from backend.tests.test_finance_fixtures import AT, OWNER, account, act, books, finance_template, spend, store  # noqa: F401

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
