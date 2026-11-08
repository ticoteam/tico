"""Statement uploads (backend/finance/statement_upload.py, Finance step 8): the parsers on the fixture files, the
review (New or Already in Tico, vendor, attribution, tie-out), the duplicate rule, posting through the rules, the
warning a gap leaves and what clears it, undo, the preview token, and the refusals (a mirror book, no book right)."""

import base64
from pathlib import Path
from typing import Any

import pytest

from backend.finance import banking as BK
from backend.finance import context as FC
from backend.finance import statement_upload as SU
from backend.finance import tab
from backend.finance.core import statement_files as SF
from backend.finance.core.errors import CONFLICT, DENIED, INVALID, NOT_AVAILABLE, FinanceError
from backend.finance.store import banking as SK
from backend.finance.store import ledger as SL
from backend.finance.store import statement_uploads as SS
from backend.tests import test_finance_fixtures as FX
from backend.tests.test_finance_fixtures import AT, OWNER, act, books, finance_template, store  # noqa: F401

FIXTURES = Path(__file__).parent / "fixtures" / "statements"
LATER = "2026-10-15T12:31:00.000000Z"  # past a preview's 30 minutes
RECONCILE_OCT = {"statement_end": "2026-10-31", "opening_balance": 1248022, "statement_balance": 1360777}


def account(c: Any, code: str) -> str:
    """A GL line by code. Setup's chart has no card line: 2100 is made the way connecting a card makes it."""
    if code == BK.CARDS and c.execute("SELECT 1 FROM fin_accounts WHERE code=?", (code,)).fetchone() is None:
        BK.new_account(FC.load(c, AT), BK.CARDS, "Credit cards", "liability", "credit_card", OWNER)
    return FX.account(c, code)


def book_deposits(c: Any, money: str) -> None:
    """A deposit nothing matches waits for a person (banking._settle, as a synced one does): book each to Revenue."""
    waiting = c.execute(
        "SELECT id FROM fin_feed_txns WHERE money_account_id=? AND amount>0 AND entry_id IS NULL", (money,)
    ).fetchall()
    for (fid,) in waiting:
        done = act(c, OWNER, "categorize", {"feed_id": fid, "account_id": account(c, "4000")})
        assert done.status == "committed", done.reason


def file_b64(name: str) -> str:
    return base64.b64encode((FIXTURES / name).read_bytes()).decode()


def money_account(c: Any, kind: str, code: str, name: str) -> str:
    """A bank or card money account on its GL line, the way the books hold one (no provider)."""
    mid = SL.new_id()
    c.execute(
        "INSERT INTO fin_money_accounts(id, book_id, kind, name, account_id, created_at, created_by) "
        "VALUES(?,?,?,?,?,?,?)",
        (mid, SL.book(c).id, kind, name, account(c, code), AT, OWNER),
    )
    return mid


def opening(c: Any, code: str, amount: int) -> None:
    """The books hold `amount` on a GL line the day before a statement starts: a balanced manual entry."""
    lines = [{"account_id": account(c, code), "amount": amount}, {"account_id": account(c, "3900"), "amount": -amount}]
    done = act(c, OWNER, "journal", {"date": "2026-09-30", "memo": "Opening balance", "lines": lines})
    assert done.status == "committed", done.reason


def review(c: Any, money: str, name: str, *, who: str = OWNER, at: str = AT) -> dict[str, Any]:
    payload = {"money_account_id": money, "file": file_b64(name), "file_name": name}
    return SU.preview(FC.load(c, at), who, payload)


def post(c: Any, money: str, name: str, *, who: str = OWNER, at: str = AT, **extra: object) -> Any:
    payload = {"money_account_id": money, "file": file_b64(name), "file_name": name, **extra}
    return act(c, who, "statement_post", payload, at=at)


def synced_row(c: Any, money: str, provider_id: str, day: str, amount: int, text: str) -> None:
    """A feed row a bank sync put in (not an upload): a file row of the same amount, day and merchant is already in."""
    SK.insert(
        c,
        "fin_feed_txns",
        {
            "money_account_id": money,
            "provider_id": provider_id,
            "txn_date": day,
            "amount": amount,
            "currency": "USD",
            "counterparty": text,
            "description": text,
            "created_at": AT,
            "created_by": OWNER,
        },
    )


def feed_count(c: Any, money: str) -> int:
    return int(c.execute("SELECT COUNT(*) FROM fin_feed_txns WHERE money_account_id=?", (money,)).fetchone()[0])


def gl_sum(c: Any, code: str) -> int:
    """What the posted entries put on a GL line (a reversed pair nets to 0)."""
    return int(
        c.execute(
            "SELECT COALESCE(SUM(l.amount), 0) FROM fin_lines l JOIN fin_entries e ON e.id=l.entry_id "
            "WHERE l.account_id=?",
            (account(c, code),),
        ).fetchone()[0]
    )


def line_of(tree: object, money: str) -> dict[str, Any] | None:
    """A money account's line in a sources read, wherever the read nests it."""
    if isinstance(tree, dict):
        if tree.get("id") == money and "off_by" in tree:
            return tree
        found = [line_of(v, money) for v in tree.values()]
    elif isinstance(tree, list):
        found = [line_of(v, money) for v in tree]
    else:
        return None
    return next((f for f in found if f is not None), None)


@pytest.mark.parametrize(
    "name, fmt, start, end, opening_bal, closing, card, last4, amounts",
    [
        (
            "chase_checking.csv",
            "csv",
            "2026-10-02",
            "2026-10-27",
            1248022,
            2004508,
            False,
            None,
            [-675, 388720, -15000, -31840, 421280, -5999],
        ),
        (
            "bofa_checking.csv",
            "csv",
            "2026-10-05",
            "2026-10-22",
            910000,
            1045364,
            False,
            None,
            [-8437, -100000, -1199, 245000],
        ),
        (
            "chase_card.csv",
            "csv",
            "2026-10-03",
            "2026-10-25",
            None,
            None,
            False,
            None,
            [-4210, -1999, 50000, -31840, -2315],
        ),
        ("amex.csv", "csv", "2026-10-02", "2026-10-21", None, None, False, None, [-5999, -31840, 50000, -675]),
        (
            "money_formats.csv",
            "csv",
            "2026-10-02",
            "2026-10-30",
            None,
            None,
            False,
            None,
            [-5999, 421280, -31840, 1200, -150, -123456],
        ),
        (
            "debit_credit.csv",
            "csv",
            "2026-10-02",
            "2026-10-12",
            None,
            None,
            False,
            None,
            [-5999, 421280, -31840, -123450],
        ),
        ("semicolon.csv", "csv", "2026-10-02", "2026-10-03", None, None, False, None, [-5999, 421280]),
        (
            "ofx_1x.ofx",
            "ofx",
            "2026-10-01",
            "2026-10-31",
            None,
            1366777,
            False,
            "2210",
            [-5999, 421280, -31840, -240000, -350000, 388720, -69406],
        ),
        (
            "ofx_2x.ofx",
            "ofx",
            "2026-10-02",
            "2026-10-10",
            None,
            None,
            False,
            "2210",
            [-5999, 421280, -31840, -4512, -2000],
        ),
        ("statement.qfx", "ofx", "2026-10-01", "2026-10-31", None, 25000, False, "2210", [-7500, 42, -1250]),
        ("card_ofx.ofx", "ofx", "2026-10-01", "2026-10-31", None, 123456, True, "5555", [-4210, -1999, 50000]),
    ],
)
def test_each_fixture_parses_to_its_rows_and_balances(
    name, fmt, start, end, opening_bal, closing, card, last4, amounts
):
    parsed = SF.parse((FIXTURES / name).read_bytes())
    assert parsed.facts.format == fmt
    assert (parsed.facts.start, parsed.facts.end) == (start, end)
    assert (parsed.facts.opening, parsed.facts.closing) == (opening_bal, closing)
    assert parsed.facts.card == card
    assert parsed.facts.last4 == last4
    assert [r.amount for r in parsed.rows] == amounts


def test_a_file_without_an_amount_column_is_refused(books):
    with books.transaction() as c:
        bank = money_account(c, "bank", "1000", "Checking")
        with pytest.raises(FinanceError) as refused:
            review(c, bank, "no_amount.csv")
        assert refused.value.code == INVALID


def test_a_review_lists_each_row_and_ties_the_file_out_and_writes_only_a_token(books):
    with books.transaction() as c:
        bank = money_account(c, "bank", "1000", "Checking")
        r = review(c, bank, "chase_checking.csv")
        assert (r["new"], r["duplicate"], r["card"], r["last4"]) == (6, 0, False, None)
        assert [row["status"] for row in r["rows"]] == ["new"] * 6
        assert r["tie"] == {
            "opening": 1248022,
            "added": 810000,
            "removed": 53514,
            "ends": 2004508,
            "statement_says": 2004508,
            "off_by": 0,
        }
        assert feed_count(c, bank) == 0
        assert c.execute("SELECT COUNT(*) FROM fin_statement_uploads").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM fin_statement_previews").fetchone()[0] == 1


def test_a_card_statement_ties_out_on_the_owed_balance(books):
    with books.transaction() as c:
        opening(c, "2100", -167247)  # the card owes 1672.47 at the day before the statement starts
        card = money_account(c, "card_program", "2100", "Card")
        r = review(c, card, "card_ofx.ofx")
        assert (r["card"], r["last4"]) == (True, "5555")
        assert r["tie"] == {
            "opening": 167247,
            "added": 6209,
            "removed": 50000,
            "ends": 123456,
            "statement_says": 123456,
            "off_by": 0,
        }


def test_a_row_an_upload_put_in_is_already_in_tico_by_its_file_id(books):
    with books.transaction() as c:
        bank = money_account(c, "bank", "1000", "Checking")
        assert post(c, bank, "ofx_1x.ofx").status == "committed"
        second = review(c, bank, "ofx_2x.ofx")
        assert [(row["id"], row["status"]) for row in second["rows"]] == [
            ("20261002001", "duplicate"),
            ("20261003001", "duplicate"),
            ("20261009001", "duplicate"),
            ("20261004001", "new"),
            ("20261008001", "new"),
        ]


def test_a_feed_row_of_the_same_amount_day_and_merchant_is_already_in_tico(books):
    with books.transaction() as c:
        bank = money_account(c, "bank", "1000", "Checking")
        synced_row(c, bank, "bank-txn-1", "2026-10-02", -5999, "ADOBE *CREATIVE CLD")
        r = review(c, bank, "chase_checking.csv")
        adobe = next(row for row in r["rows"] if row["description"] == "ADOBE *CREATIVE CLD")
        assert adobe["status"] == "duplicate"
        assert (r["new"], r["duplicate"]) == (5, 1)


def test_a_posting_rule_attributes_a_new_row_and_the_post_books_it_there(books):
    with books.transaction() as c:
        bank = money_account(c, "bank", "1000", "Checking")
        rule = {
            "kind": "posting",
            "name": "Adobe software",
            "when": {"counterparty": "ADOBE *CREATIVE CLD"},
            "outcome": {"outcome": "post", "account_id": account(c, "6100")},
            "examples": [{"input": {"counterparty": "ADOBE *CREATIVE CLD"}, "expect": "post"}],
        }
        assert act(c, OWNER, "rule_create", rule).status == "committed"
        r = review(c, bank, "chase_checking.csv")
        adobe = next(row for row in r["rows"] if row["description"] == "ADOBE *CREATIVE CLD")
        assert adobe["attribution"]["rule"] == "Adobe software"
        done = post(c, bank, "chase_checking.csv")
        assert done.status == "committed", done.reason
        lines = c.execute(
            "SELECT l.account_id FROM fin_lines l JOIN fin_feed_txns f ON f.entry_id=l.entry_id "
            "WHERE f.upload_id=? AND f.counterparty=? AND l.account_id<>?",
            (done.result["upload_id"], "ADOBE *CREATIVE CLD", account(c, "1000")),
        ).fetchall()
        assert [row[0] for row in lines] == [account(c, "6100")]


def test_a_post_books_each_new_row_once_and_keeps_the_file_statement(books):
    with books.transaction() as c:
        bank = money_account(c, "bank", "1000", "Checking")
        done = post(c, bank, "chase_checking.csv")
        assert done.status == "committed", done.reason
        assert (done.result["new"], done.result["duplicate"], done.result["off_by"]) == (6, 0, 0)
        assert feed_count(c, bank) == 6
        statement = SK.statement(c, bank, "2026-10-27")
        assert statement is not None and int(statement["ending_balance"]) == 2004508
        again = post(c, bank, "chase_checking.csv")
        assert (again.status, again.code) == ("failed", CONFLICT)
        assert feed_count(c, bank) == 6


def test_a_second_file_posts_only_the_rows_it_does_not_share(books):
    with books.transaction() as c:
        bank = money_account(c, "bank", "1000", "Checking")
        assert post(c, bank, "ofx_1x.ofx").status == "committed"
        second = post(c, bank, "ofx_2x.ofx")
        assert second.status == "committed", second.reason
        assert (second.result["new"], second.result["duplicate"]) == (2, 3)
        assert feed_count(c, bank) == 9


def test_a_skipped_new_row_is_left_out_and_shows_in_the_tie_out(books):
    with books.transaction() as c:
        bank = money_account(c, "bank", "1000", "Checking")
        coffee = review(c, bank, "chase_checking.csv")["rows"][0]  # COFFEE BEAN 0042, -6.75
        done = post(c, bank, "chase_checking.csv", skip=[coffee["id"]])
        assert done.status == "committed", done.reason
        assert (done.result["new"], done.result["skipped"], done.result["off_by"]) == (5, 1, 675)


def test_a_gap_is_a_warning_a_post_can_go_past_and_a_reconciliation_clears(books):
    with books.transaction() as c:
        opening(c, "1000", 1248022)
        bank = money_account(c, "bank", "1000", "Checking")
        r = review(c, bank, "ofx_1x.ofx")
        assert r["tie"]["off_by"] == -6000  # 1248022 + 112755 is 1360777; the file closes at 1366777
        done = post(c, bank, "ofx_1x.ofx")
        assert done.status == "committed", done.reason
        assert done.result["off_by"] == -6000
        assert SS.off_by(c, bank) == -6000
        assert (line_of(tab.sources(c, OWNER), bank) or {}).get("off_by") == -6000
        book_deposits(c, bank)
        tied = act(c, OWNER, "reconcile", {"money_account_id": bank, **RECONCILE_OCT})
        assert (tied.status, tied.result["status"], tied.result["difference"]) == ("committed", "done", 0)
        assert SS.off_by(c, bank) is None


def test_undo_takes_an_upload_back_and_the_file_can_be_posted_again(books):
    with books.transaction() as c:
        bank = money_account(c, "bank", "1000", "Checking")
        done = post(c, bank, "bofa_checking.csv")
        assert done.status == "committed", done.reason
        upload = done.result["upload_id"]
        assert gl_sum(c, "1000") == -109636  # the three debits; the 2,450.00 deposit waits for a person
        undone = act(c, OWNER, "statement_undo", {"upload_id": upload})
        assert undone.status == "committed", undone.reason
        assert (feed_count(c, bank), gl_sum(c, "1000")) == (0, 0)
        assert SS.upload_get(c, upload)["undone_at"] is not None
        assert SK.statement(c, bank, "2026-10-22") is None
        assert post(c, bank, "bofa_checking.csv").status == "committed"


def test_undo_is_refused_once_the_account_is_reconciled_through_the_upload(books):
    with books.transaction() as c:
        opening(c, "1000", 1248022)
        bank = money_account(c, "bank", "1000", "Checking")
        upload = post(c, bank, "ofx_1x.ofx").result["upload_id"]
        book_deposits(c, bank)
        tied = act(c, OWNER, "reconcile", {"money_account_id": bank, **RECONCILE_OCT})
        assert tied.result["status"] == "done", tied.result
        refused = act(c, OWNER, "statement_undo", {"upload_id": upload})
        assert (refused.status, refused.code) == ("failed", CONFLICT)
        assert feed_count(c, bank) == 7


def test_a_preview_token_must_be_for_this_file_unexpired_and_used_once(books):
    with books.transaction() as c:
        bank = money_account(c, "bank", "1000", "Checking")
        chase = review(c, bank, "chase_checking.csv")
        wrong_file = post(c, bank, "bofa_checking.csv", preview_token=chase["token"])
        assert (wrong_file.status, wrong_file.code) == ("failed", CONFLICT)
        late = post(c, bank, "chase_checking.csv", preview_token=chase["token"], at=LATER)
        assert (late.status, late.code) == ("failed", CONFLICT)
        good = post(c, bank, "chase_checking.csv", preview_token=chase["token"])
        assert good.status == "committed", good.reason
        reused = post(c, bank, "bofa_checking.csv", preview_token=chase["token"])
        assert (reused.status, reused.code) == ("failed", CONFLICT)


def test_a_new_row_that_changes_after_the_preview_refuses_the_post(books):
    with books.transaction() as c:
        bank = money_account(c, "bank", "1000", "Checking")
        preview = review(c, bank, "chase_checking.csv")
        synced_row(c, bank, "bank-txn-1", "2026-10-02", -5999, "ADOBE *CREATIVE CLD")  # one of the New rows is now in
        changed = post(c, bank, "chase_checking.csv", preview_token=preview["token"])
        assert (changed.status, changed.code) == ("failed", CONFLICT)
        assert feed_count(c, bank) == 1


def test_a_mirror_book_takes_no_statement(books):
    with books.transaction() as c:
        bank = money_account(c, "bank", "1000", "Checking")
        c.execute("UPDATE fin_books SET mode='mirror'")
        outbox = c.execute("SELECT COUNT(*) FROM fin_outbox").fetchone()[0]
        refused = post(c, bank, "chase_checking.csv")
        assert (refused.status, refused.code) == ("denied", NOT_AVAILABLE)
        assert c.execute("SELECT COUNT(*) FROM fin_outbox").fetchone()[0] == outbox  # never written to the provider
        added = act(c, OWNER, "money_account_add", {"kind": "bank", "name": "Chase Checking"})
        assert (added.status, added.code) == ("denied", NOT_AVAILABLE)
        with pytest.raises(FinanceError) as previewed:
            review(c, bank, "chase_checking.csv")
        assert previewed.value.code == NOT_AVAILABLE
        assert feed_count(c, bank) == 0


def test_posting_or_reviewing_a_statement_needs_the_book_right(books):
    with books.transaction() as c:
        bank = money_account(c, "bank", "1000", "Checking")
        refused = post(c, bank, "chase_checking.csv", who="human:dan")
        assert refused.status == "denied", refused
        with pytest.raises(FinanceError) as previewed:
            review(c, bank, "chase_checking.csv", who="human:dan")
        assert previewed.value.code == DENIED
        assert feed_count(c, bank) == 0


@pytest.mark.parametrize(
    "name, signs, flipped, by, amounts",
    [
        ("chase_card.csv", "auto", False, "type column", [-4210, -1999, 50000, -31840, -2315]),
        ("card_type_positive.csv", "auto", True, "type column", [-4820, 30000, -9137]),
        ("amex.csv", "auto", True, "amex headers", [-5999, -31840, 50000, -675]),
        ("debit_credit.csv", "auto", False, "debit and credit columns", [-5999, 421280, -31840, -123450]),
        ("discover.csv", "auto", False, "as shown", [4820, -30000, 9137]),
        ("discover.csv", "flipped", True, "chosen", [-4820, 30000, -9137]),
        ("amex.csv", "as_shown", False, "chosen", [5999, 31840, -50000, 675]),
        ("card_ofx.ofx", "auto", False, "file", [-4210, -1999, 50000]),
    ],
)
def test_signs_come_from_the_choice_the_file_or_its_type_column_then_amex_headers(name, signs, flipped, by, amounts):
    parsed = SF.parse((FIXTURES / name).read_bytes(), signs)
    assert (parsed.facts.flipped, parsed.facts.signs_by) == (flipped, by)
    assert [r.amount for r in parsed.rows] == amounts


def test_a_card_file_read_flipped_posts_charges_as_money_out_and_the_post_keeps_the_previews_signs(books):
    with books.transaction() as c:
        card = money_account(c, "card_program", "2100", "Discover")
        shown = review(c, card, "discover.csv")
        assert (shown["card"], shown["signs"]) == (True, {"flipped": False, "by": "as shown"})
        payload = {"money_account_id": card, "file": file_b64("discover.csv"), "file_name": "discover.csv"}
        turned = SU.preview(FC.load(c, AT), OWNER, {**payload, "signs": "flipped"})
        assert turned["signs"] == {"flipped": True, "by": "chosen"}
        assert [row["amount"] for row in turned["rows"]] == [-4820, 30000, -9137]
        unflipped = post(c, card, "discover.csv", preview_token=turned["token"])
        assert (unflipped.status, unflipped.code) == ("failed", CONFLICT)  # not what the preview showed
        done = post(c, card, "discover.csv", preview_token=turned["token"], signs="flipped")
        assert done.status == "committed", done.reason
        amounts = c.execute(
            "SELECT amount FROM fin_feed_txns WHERE money_account_id=? ORDER BY txn_date", (card,)
        ).fetchall()
        assert [int(a[0]) for a in amounts] == [-4820, 30000, -9137]


def test_a_credit_card_ofx_is_refused_on_a_bank_account(books):
    with books.transaction() as c:
        bank = money_account(c, "bank", "1000", "Checking")
        with pytest.raises(FinanceError) as refused:
            review(c, bank, "card_ofx.ofx")
        assert refused.value.code == INVALID and "card account" in str(refused.value)


def test_a_file_over_the_size_cap_is_refused(books):
    with books.transaction() as c:
        bank = money_account(c, "bank", "1000", "Checking")
        big = b"Date,Description,Amount\n" + b"10/02/2026,X,1.00\n" * (SF.MAX_BYTES // 18 + 1)
        payload = {"money_account_id": bank, "file": base64.b64encode(big).decode()}
        with pytest.raises(FinanceError) as refused:
            SU.preview(FC.load(c, AT), OWNER, payload)
        assert refused.value.code == INVALID


def test_a_bank_tico_cannot_connect_is_added_then_takes_a_statement(books):
    with books.transaction() as c:
        added = act(c, OWNER, "money_account_add", {"kind": "bank", "name": "Chase Checking", "last4": "2210"})
        assert added.status == "committed", added.reason
        money = SK.get(c, "fin_money_accounts", added.result["money_account_id"])
        assert (money["kind"], money["subtype"], money["provider"], money["connection_id"]) == (
            "bank",
            "checking",
            "",
            None,
        )
        gl = c.execute("SELECT type, subtype, name FROM fin_accounts WHERE id=?", (money["account_id"],)).fetchone()
        assert tuple(gl) == ("asset", "bank", "Chase Checking ••2210")
        done = post(c, str(money["id"]), "chase_checking.csv")
        assert (done.status, done.result["new"]) == ("committed", 6)
        card = act(
            c,
            OWNER,
            "money_account_add",
            {"kind": "card_program", "name": "Amex Gold", "account_id": account(c, "2100")},
        )
        assert card.status == "committed", card.reason
        assert card.result["account_id"] == account(c, "2100")
        taken = act(
            c, OWNER, "money_account_add", {"kind": "card_program", "name": "Other", "account_id": account(c, "2100")}
        )
        assert (taken.status, taken.code) == ("failed", CONFLICT)
        wrong = act(c, OWNER, "money_account_add", {"kind": "bank", "name": "Other", "account_id": account(c, "2100")})
        assert (wrong.status, wrong.code) == ("failed", INVALID)
        outsider = act(c, "human:dan", "money_account_add", {"kind": "bank", "name": "Mine"})
        assert outsider.status == "denied"
