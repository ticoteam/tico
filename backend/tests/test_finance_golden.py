"""Golden reports on fixed fictional books (spec 14): Acme's September is closed, then its accounts move and October
is booked. Every expected number below is worked out by hand from these entries, not read off a report:

  Aug 31  owner's capital                          bank +100,000
  Sep 5   software 800, seats 300 (under software), AI services 2,000   all paid from the bank
  Oct 3   a bill for software 7,000, approved, unpaid until Oct 14
  Oct 10  AI services 3,100, paid from the bank
  Oct 12  other expenses 500, paid from the bank
"""
from backend.finance import closing as CL
from backend.finance import reads as FR
from backend.tests.test_finance_fixtures import AT, OWNER, account, act, books, finance_template, spend, store  # noqa: F401

SEP = ("2026-09-01", "2026-09-30")
OCT = ("2026-10-01", "2026-10-31")
NOV = "2026-11-05T12:00:00.000000Z"


def _acme(c):
    """September's books, closed, then the Seats account moved from under software to other expenses."""
    act(c, OWNER, "journal", {"date": "2026-08-31", "lines": [
        {"account_id": account(c, "1000"), "amount": 100_000}, {"account_id": account(c, "3000"), "amount": -100_000}]})
    assert act(c, OWNER, "account_create", {"code": "6110", "name": "Seats", "type": "expense",
                                            "parent_id": account(c, "6100")}).status == "committed"
    spend(c, "6100", 800, day="2026-09-05")
    spend(c, "6110", 300, day="2026-09-12")
    spend(c, "5100", 2_000, day="2026-09-20")
    assert act(c, OWNER, "budget_create", {"name": "Software", "account_id": account(c, "6100"), "amount": 1_000,
                                           "effective_from": "2026-09-01"}, at="2026-08-15T12:00:00.000000Z").status == "committed"
    assert act(c, OWNER, "close_period", {"month": "2026-09"}).status == "committed"


def _by_code(rows):
    return {str(r["code"]): r for r in rows}


def _book(c) -> str:
    return str(c.execute("SELECT id FROM fin_books").fetchone()[0])


def test_a_closed_month_reads_its_own_tree_after_accounts_move(books):  # noqa: F811
    with books.transaction() as c:
        _acme(c)
        assert act(c, OWNER, "account_update", {"account_id": account(c, "6110"),
                                                "parent_id": account(c, "6900")}).status == "committed"
        sep = FR.pnl(c, OWNER, *SEP, "month", "accrual", AT)
        rows = _by_code(sep["rows"])
        assert (rows["6100"]["total"], rows["6110"]["total"], rows["5100"]["total"]) == (800, 300, 2_000)
        assert rows["6110"]["parent_id"] == account(c, "6100")     # the tree it closed with, not where it sits now
        assert sep["expense"] == [3_100] and sep["snapshot"] == {"month": "2026-09", "restatements": 0}
        assert FR.pnl(c, OWNER, *SEP, "month", "cash", AT)["expense"] == [3_100]
        software = next(r for r in FR.budget_vs_actual(c, OWNER, *SEP, "month", at=AT)["budgets"]
                        if r["name"] == "Software")
        assert (software["budget"], software["actual"]) == ([1_000], [1_100])   # live, seats would be 800
        sheet = FR.balance_sheet(c, OWNER, "2026-09-30", AT)
        assert sheet["totals"] == {"asset": 96_900, "liability": 0, "equity": 96_900}
        assert sheet["snapshot"] == {"month": "2026-09", "restatements": 0}
        assert [r["balance"] for r in sheet["equity"] if r["name"] == "Net income to date"] == [-3_100]
        # October is open: it reads the live tree and its own entries.
        bill = act(c, OWNER, "bill_create", {"vendor_id": str(act(c, OWNER, "contact_create", {
            "name": "Acme Tools", "kind": "vendor"}).result["contact_id"]), "bill_date": "2026-10-03",
            "lines": [{"account_id": account(c, "6100"), "amount": 7_000}]})
        assert bill.status == "committed", bill
        assert act(c, OWNER, "bill_approve", {"bill_id": str(bill.result["bill_id"])}).status == "committed"
        spend(c, "5100", 3_100, day="2026-10-10")
        spend(c, "6900", 500, day="2026-10-12")
        octo = FR.pnl(c, OWNER, *OCT, "month", "accrual", AT)
        assert octo["expense"] == [10_600] and octo["net"] == [-10_600] and octo["snapshot"] is None
        assert FR.balance_sheet(c, OWNER, "2026-10-31", AT)["totals"] == {
            "asset": 93_300, "liability": 7_000, "equity": 86_300}


def test_cash_and_accrual_part_ways_until_the_bill_is_paid(books):  # noqa: F811
    with books.transaction() as c:
        _acme(c)
        vendor = str(act(c, OWNER, "contact_create", {"name": "Acme Tools", "kind": "vendor"}).result["contact_id"])
        bill = str(act(c, OWNER, "bill_create", {"vendor_id": vendor, "bill_date": "2026-10-03",
                                                 "lines": [{"account_id": account(c, "6100"), "amount": 7_000}]}
                       ).result["bill_id"])
        assert act(c, OWNER, "bill_approve", {"bill_id": bill}).status == "committed"
        spend(c, "5100", 3_100, day="2026-10-10")
        spend(c, "6900", 500, day="2026-10-12")
        assert FR.pnl(c, OWNER, *OCT, "month", "accrual", AT)["expense"] == [10_600]
        assert FR.pnl(c, OWNER, *OCT, "month", "cash", AT)["expense"] == [3_600]    # the bill is not paid yet
        done = act(c, OWNER, "bill_mark_paid", {"bill_id": bill, "account_id": account(c, "1000"),
                                                "date": "2026-10-14"})
        assert done.status == "committed", done
        assert FR.pnl(c, OWNER, *OCT, "month", "cash", AT)["expense"] == [10_600]
        assert FR.balance_sheet(c, OWNER, "2026-10-31", AT)["totals"] == {
            "asset": 86_300, "liability": 0, "equity": 86_300}


def test_a_restatement_of_a_closed_month_is_flagged_on_its_snapshot(books):  # noqa: F811
    with books.transaction() as c:
        _acme(c)
        assert CL.restate(c, _book(c), "2026-09", OWNER, AT) == 0          # a month that still matches takes no flag
        c.execute("UPDATE fin_rollups SET amount = amount + 50 WHERE account_id=? AND day=?",
                  (account(c, "6100"), "2026-09-05"))
        assert CL.restate(c, _book(c), "2026-09", OWNER, AT) == 1
        sep = FR.pnl(c, OWNER, *SEP, "month", "accrual", AT)
        assert _by_code(sep["rows"])["6100"]["total"] == 850
        assert sep["snapshot"] == {"month": "2026-09", "restatements": 1}


def test_a_report_over_several_months_reads_each_closed_month_as_it_closed(books):  # noqa: F811
    with books.transaction() as c:
        _acme(c)
        assert act(c, OWNER, "account_update", {"account_id": account(c, "6110"),
                                                "parent_id": account(c, "6900")}).status == "committed"
        both = ("2026-09-01", "2026-10-31")
        software = next(r for r in FR.budget_vs_actual(c, OWNER, *both, "month", at=AT)["budgets"]
                        if r["name"] == "Software")
        assert software["actual"] == [1_100, 0]         # September as it closed: seats were under software then
        assert FR.line_detail(c, OWNER, account(c, "6100"), *SEP, at=AT)["total"] == 1_100
        drill = FR.line_detail(c, OWNER, account(c, "6100"), *both, at=AT)
        assert drill["total"] == 1_100 and sorted(e["amount"] for e in drill["entries"]) == [300, 800]


def test_a_window_across_a_closed_month_hides_private_spend_as_that_month_does(books):  # noqa: F811
    # Payroll sits under Other expenses when September closes and moves out of it after. Dan sees the budget but not
    # the payroll detail, so September's payroll stays hidden in the single-month report and in a window over it.
    with books.transaction() as c:
        made = act(c, OWNER, "account_create", {"code": "6200", "name": "Payroll", "type": "expense",
                                                "parent_id": account(c, "6900")})
        assert made.status == "committed", made
        assert act(c, OWNER, "private_lines_set", {"account_id": account(c, "6200"),
                                                   "others_see": "totals"}).status == "committed"
        spend(c, "6200", 3_000, day="2026-09-15")
        assert act(c, OWNER, "budget_create", {"name": "Other", "account_id": account(c, "6900"), "amount": 5_000,
                                               "effective_from": "2026-09-01"}, at="2026-08-15T12:00:00.000000Z").status == "committed"
        assert act(c, OWNER, "close_period", {"month": "2026-09"}).status == "committed"
        assert act(c, OWNER, "view_grant", {"subject": "human:dan", "what": "everything"}).status == "committed"
        assert act(c, OWNER, "account_update", {"account_id": account(c, "6200"),
                                                "parent_id": account(c, "6000")}).status == "committed"

        def other(a, z):
            return next(r for r in FR.budget_vs_actual(c, "human:dan", a, z, "month", at=AT)["budgets"]
                        if r["name"] == "Other")["actual"]
        assert other(*SEP) is None
        assert other("2026-09-01", "2026-10-31") is None


def test_a_drill_down_over_a_closed_month_lists_the_entries_its_total_counts(books):  # noqa: F811
    with books.transaction() as c:
        _acme(c)
        assert act(c, OWNER, "account_update", {"account_id": account(c, "6110"),
                                                "parent_id": account(c, "6900")}).status == "committed"
        for n in range(1, 6):       # newer October lines on seats, which no longer sits under software
            spend(c, "6110", 100, day=f"2026-10-0{n}")
        drill = FR.line_detail(c, OWNER, account(c, "6100"), "2026-09-01", "2026-10-31", limit=1, at=AT)
        assert drill["total"] == 1_100
        assert [(e["date"], e["amount"]) for e in drill["entries"]] == [("2026-09-12", 300)]
        assert drill["more"] is True


def test_a_drill_down_lists_no_entry_its_month_hides_from_the_viewer(books):  # noqa: F811
    # A $123.45 sale sat on seats under other expenses when September closed. Seats then moved under software, where
    # Cara's company-wide grant reaches it. September hides the sale from her, so the drill-down lists nothing.
    both = ("2026-09-01", "2026-10-31")
    with books.transaction() as c:
        assert act(c, OWNER, "account_create", {"code": "6110", "name": "Seats", "type": "expense",
                                                "parent_id": account(c, "6900")}).status == "committed"
        spend(c, "6110", 12_345, day="2026-09-12")
        assert act(c, OWNER, "close_period", {"month": "2026-09"}).status == "committed"
        assert act(c, OWNER, "account_update", {"account_id": account(c, "6110"),
                                                "parent_id": account(c, "6100")}).status == "committed"
        assert act(c, OWNER, "view_grant", {"subject_kind": "teammate", "subject": "human:cara", "what": "pnl_lines",
                                            "account_ids": [account(c, "6100")], "whose": "company"}
                   ).status == "committed"
        drill = FR.line_detail(c, "human:cara", account(c, "6000"), *both, at=AT)
        assert drill["total"] == 0
        assert drill["entries"] == []


def test_a_drill_down_entry_follows_the_view_of_the_month_it_is_in(books):  # noqa: F811
    # Seats' $3.00 was unattributed under software in September, then moved under other expenses, where Dan's grant
    # reaches it. September's view never showed Dan that line, so the entries must not show it to him either.
    both = ("2026-09-01", "2026-10-31")
    with books.transaction() as c:
        assert act(c, OWNER, "account_create", {"code": "6110", "name": "Seats", "type": "expense",
                                                "parent_id": account(c, "6100")}).status == "committed"
        spend(c, "6110", 300, day="2026-09-05")
        assert act(c, OWNER, "close_period", {"month": "2026-09"}).status == "committed"
        assert act(c, OWNER, "account_update", {"account_id": account(c, "6110"),
                                                "parent_id": account(c, "6900")}).status == "committed"
        assert act(c, OWNER, "view_grant", {"subject_kind": "teammate", "subject": "human:dan", "what": "pnl_lines",
                                            "account_ids": [account(c, "6900")], "whose": "company"}
                   ).status == "committed"
        drill = FR.line_detail(c, "human:dan", account(c, "6110"), *both, at=AT)
        assert drill["total"] == 0
        assert drill["entries"] == []


def test_a_drill_down_limit_applies_the_view_before_it_cuts(books):  # noqa: F811
    # Eve is granted web's expenses. September's one web line is her only entry; October's five sales lines on the same
    # account are newer and not hers. A limit of one must still list the web line, which is the whole of her total.
    both = ("2026-09-01", "2026-10-31")
    with books.transaction() as c:
        spend(c, "6100", 300, day="2026-09-05", team="web")
        assert act(c, OWNER, "view_grant", {"subject_kind": "teammate", "subject": "human:eve", "what": "expenses",
                                            "whose": "team", "whose_team": "web"}).status == "committed"
        for n in range(1, 6):
            spend(c, "6100", 100, day=f"2026-10-0{n}", team="sales")
        drill = FR.line_detail(c, "human:eve", account(c, "6100"), *both, limit=1, at=AT)
        assert drill["total"] == 300
        assert [(e["date"], e["amount"]) for e in drill["entries"]] == [("2026-09-05", 300)]
        assert drill["more"] is False


def test_a_multi_month_budget_read_omits_a_budget_its_viewer_cannot_see_in_every_month(books):  # noqa: F811
    # Seats' budget sat under other expenses when September closed, and its $123.45 sale with it. Seats then moved under
    # software, where Cara's company-wide grant reaches it. September alone omits the budget, so September to October
    # omits it too, and its actuals never show the closed month's sale.
    with books.transaction() as c:
        assert act(c, OWNER, "account_create", {"code": "6110", "name": "Seats", "type": "expense",
                                                "parent_id": account(c, "6900")}).status == "committed"
        spend(c, "6110", 12_345, day="2026-09-12")
        assert act(c, OWNER, "budget_create", {"name": "Seats", "account_id": account(c, "6110"), "amount": 20_000,
                                               "effective_from": "2026-09-01"}, at="2026-08-15T12:00:00.000000Z").status == "committed"
        assert act(c, OWNER, "close_period", {"month": "2026-09"}).status == "committed"
        assert act(c, OWNER, "account_update", {"account_id": account(c, "6110"),
                                                "parent_id": account(c, "6100")}).status == "committed"
        assert act(c, OWNER, "view_grant", {"subject_kind": "teammate", "subject": "human:cara", "what": "pnl_lines",
                                            "account_ids": [account(c, "6100")], "whose": "company"}
                   ).status == "committed"

        def seats(a, z):
            return [r for r in FR.budget_vs_actual(c, "human:cara", a, z, "month", at=AT)["budgets"]
                    if r["name"] == "Seats"]
        assert seats(*SEP) == []
        assert seats("2026-09-01", "2026-10-31") == []
        assert [r["actual"] for r in seats(*OCT)] == [[0]]      # October alone: visible, and nothing in it


def test_a_multi_month_budget_read_lists_a_budget_each_month_shows_after_it_moves_out_of_view(books):  # noqa: F811
    # Seats sat under software when September and October both closed, where Cara's company-wide grant reaches it. Seats
    # then moved under other expenses, out of her live view. Each month alone lists it with its own actual, so September
    # to October lists it with both months' actuals.
    with books.transaction() as c:
        assert act(c, OWNER, "account_create", {"code": "6110", "name": "Seats", "type": "expense",
                                                "parent_id": account(c, "6100")}).status == "committed"
        spend(c, "6110", 12_345, day="2026-09-12")
        spend(c, "6110", 6_789, day="2026-10-05")
        assert act(c, OWNER, "budget_create", {"name": "Seats", "account_id": account(c, "6110"), "amount": 20_000,
                                               "effective_from": "2026-09-01"}, at="2026-08-15T12:00:00.000000Z").status == "committed"
        assert act(c, OWNER, "view_grant", {"subject_kind": "teammate", "subject": "human:cara", "what": "pnl_lines",
                                            "account_ids": [account(c, "6100")], "whose": "company"}
                   ).status == "committed"
        assert act(c, OWNER, "close_period", {"month": "2026-09"}).status == "committed"
        assert act(c, OWNER, "close_period", {"month": "2026-10"}, at=NOV).status == "committed"
        assert act(c, OWNER, "account_update", {"account_id": account(c, "6110"), "parent_id": account(c, "6900")},
                   at=NOV).status == "committed"

        def seats(a, z):
            return [r for r in FR.budget_vs_actual(c, "human:cara", a, z, "month", at=NOV)["budgets"]
                    if r["name"] == "Seats"]
        assert [r["actual"] for r in seats(*SEP)] == [[12_345]]
        assert [r["actual"] for r in seats(*OCT)] == [[6_789]]
        assert [r["actual"] for r in seats("2026-09-01", "2026-10-31")] == [[12_345, 6_789]]
