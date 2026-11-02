"""The close gate (spec §11, handlers.close_period): a month closes only when its checklist's blocking items pass, for
everyone, in the plan and again when an approved close commits. Reconciliation through month end and settled rows still
waiting for a category block; lines on Other expenses are shown and do not. A book with no connected money account
closes, and so does a month holding raw totals."""
from pathlib import Path

from backend import hubdb as H
from backend.finance import actions as FA
from backend.finance.core.errors import CONFLICT
from backend.tests.test_finance_fixtures import (  # noqa: F401
    AT, MERCURY, OWNER, FakeMercury, account, act, books, connect, finance_template, spend, store, sync_once)

LATER = "2026-10-15T12:30:00.000000Z"
CSV = (Path(__file__).parent / "fixtures" / "finance" / "raw_totals.csv").read_text()
MAPPING = {"Software subscriptions": "6100", "Office rent": "6900", "Sales revenue": "4000", "Cash at bank": "1000"}


def _txn(raw_id: str, amount: float, day: str, counterparty: str):
    return {**MERCURY["txn_bank_fee"], "id": raw_id, "accountId": "acct-chk", "amount": amount,
            "kind": "externalTransfer", "createdAt": f"{day}T10:00:00Z", "postedAt": f"{day}T10:00:00Z",
            "counterpartyName": counterparty}


def _arrives(fake, raw_id: str, amount: float, day: str, counterparty: str = "Globex") -> None:
    fake.txns[raw_id] = _txn(raw_id, amount, day, counterparty)
    fake.events_list.append({"id": f"evt-{raw_id}", "resourceType": "transaction", "resourceId": raw_id,
                             "operationType": "create", "resourceVersion": 1})


def _reconciled_through_september(c) -> None:
    """The checking account reconciled through 2026-09-30 at its book balance (no feed rows cleared)."""
    bank = str(c.execute("SELECT m.id FROM fin_money_accounts m JOIN fin_accounts a ON a.id=m.account_id "
                         "WHERE a.code='1000'").fetchone()[0])
    balance = int(c.execute("SELECT COALESCE(SUM(l.amount), 0) FROM fin_lines l JOIN fin_accounts a ON a.id=l.account_id "
                            "JOIN fin_entries e ON e.id=l.entry_id WHERE a.code='1000' AND e.status='posted' AND "
                            "e.entry_date<='2026-09-30'").fetchone()[0])
    done = act(c, OWNER, "reconcile", {"money_account_id": bank, "statement_end": "2026-09-30",
                                       "statement_balance": balance})
    assert done.status == "committed" and done.result["status"] == "done", (balance, done.result)


def test_a_month_holding_raw_totals_closes_with_nothing_of_its_own_to_review(books):
    with books.transaction() as c:
        imported = act(c, OWNER, "import_raw_totals", {"csv": CSV, "basis": "cash", "mapping": MAPPING,
                                                       "source_name": "Acme history"})
        assert imported.status == "committed", imported
        assert act(c, OWNER, "close_period", {"month": "2026-01"}).status == "committed"


def test_a_book_with_no_connected_money_account_closes_and_lines_on_other_expenses_do_not_block(books):
    with books.transaction() as c:
        spend(c, "6900", 5_000, day="2026-09-10")              # on Other expenses: shown by the checklist, not blocking
        made = act(c, OWNER, "close_period", {"month": "2026-09"})
        assert made.status == "committed", made


def test_a_settled_row_waiting_for_a_category_blocks_the_close_until_it_is_categorized(books):
    fake = FakeMercury()
    connect(books, fake)
    sync_once(books, fake)
    with books.transaction() as c:
        _reconciled_through_september(c)
    _arrives(fake, "txn-in", 40.0, "2026-09-10")               # money in from a name with no rule: stays new
    sync_once(books, fake, LATER)
    with books.transaction() as c:
        fid = str(c.execute("SELECT id FROM fin_feed_txns WHERE provider_id='txn-in'").fetchone()[0])
        refused = act(c, OWNER, "close_period", {"month": "2026-09"})
        assert refused.status != "committed" and refused.code == CONFLICT, refused
        assert "waiting for a category" in refused.reason, refused.reason
        assert act(c, OWNER, "categorize", {"feed_id": fid, "account_id": account(c, "4000")}).status == "committed"
        assert act(c, OWNER, "close_period", {"month": "2026-09"}).status == "committed"


def test_an_approved_close_is_checked_again_when_it_commits(books):
    fake = FakeMercury()
    connect(books, fake)
    sync_once(books, fake)
    with books.transaction() as c:
        _reconciled_through_september(c)
        act(c, OWNER, "right_grant", {"teammate": "human:ben", "right": "finance.book"})
        asked = act(c, "human:ben", "close_period", {"month": "2026-09"})
        assert (asked.status, asked.approver) == ("needs_approval", OWNER), asked
    _arrives(fake, "txn-late-in", 40.0, "2026-09-12")          # a row arrives while the close waits for approval
    sync_once(books, fake, LATER)
    with books.transaction() as c:
        H.approval_decide(c, OWNER, asked.approval_id, "approved")
        decided = FA.approval_decided(c, asked.approval_id, AT)
        assert decided.code == CONFLICT and "waiting for a category" in decided.reason, decided
        assert c.execute("SELECT COUNT(*) FROM fin_periods WHERE start_date='2026-09-01' AND status='closed'"
                         ).fetchone()[0] == 0                   # the month stays open
