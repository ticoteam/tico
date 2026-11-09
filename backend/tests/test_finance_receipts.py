"""Receipt matching (backend/finance/receipts.py, store/receipts.py): a Library file with the amount, date and vendor read
from its indexed text (no model), suggested on the card or bank row it matches, attached by accept, undone, refused for
good, or read by a bot that looked at a file with no text. Covers the parser (a table of realistic receipts), the match
rules (one good candidate is a suggestion, two equal ones are not, a row that syncs later is matched, nothing attaches
while auto-attach is off, and a match at the setting attaches), the actions (accept on the row or another one, undo,
reject), the Watch findings they move, the rights (a bot accepts on its own card and another bot sees nothing), the reads,
and the briefing line. The browser side is ui/tests/finance-receipts.cjs."""

import json

import pytest

from backend import file_library as FL
from backend.finance import reads as FR
from backend.finance import receipts as RCP
from backend.finance.context import load
from backend.finance.core.errors import FinanceError
from backend.finance.store import ap as SAP
from backend.finance.store import ledger as SL
from backend.finance.store import receipts as RS
from backend.tests.test_finance_fixtures import AT, OWNER, account, act, books, finance_template, store  # noqa: F401

NOV = "2026-11-08T12:00:00.000000Z"  # the briefing is read on this day: Canva Nov 1 and Later Nov 3 are a week back
BOT = "bot:coder"  # holds the card in these tests; its Library files are the coder bot's
OTHER = "bot:seller"  # a bot with no card of its own
CANVA = "Canva\nReceipt #INV-1042\nDate: October 14, 2026\nTotal $12.00\nThank you for subscribing."

# (text, total in cents, date, vendor): the texts a Library file holds once indexed (the parser reads these; no model).
RECEIPTS = [
    (
        "Canva Pty Ltd\nReceipt #INV-1042\nDate: November 1, 2026\nTotal $12.00\nThank you for subscribing to Canva Pro.",
        1200,
        "2026-11-01",
        "Canva Pty Ltd",
    ),
    (
        "Figma Inc\nInvoice INV-2231\nDate of issue October 3, 2026\nAmount due $15.00\nPay online at figma.com",
        1500,
        "2026-10-03",
        "Figma Inc",
    ),
    (
        "Your receipt from Adobe\nAdobe Creative Cloud All Apps\nOrder date: 03/15/2026\nTotal: $54.99",
        5499,
        "2026-03-15",
        "Adobe",
    ),
    ("Uber\nThanks for riding, Ana.\nTrip fare $18.40\nTotal $21.85\nMon, Oct 12, 2026", 2185, "2026-10-12", "Uber"),
    (
        "Amazon.com\nOrder #112-3344556-7788901\nOrder Date: 2026-09-30\nItems: $42.10\nGrand Total: $46.52\n"
        "Visa ending 4411",
        4652,
        "2026-09-30",
        "Amazon.com",
    ),
    (
        "The Corner Bistro\n2 Main St\n03/14/2026  7:42 PM\nSubtotal 38.00\nTip\nTotal 45.00",
        4500,
        "2026-03-14",
        "The Corner Bistro",
    ),
    (
        "Receipt from Acme Lights\nReceipt number 1234-5678\nReceipt date Oct 14, 2026\nAmount paid $99.00\n"
        "Paid with Visa ending in 4242",
        9900,
        "2026-10-14",
        "Acme Lights",
    ),
    ("Canva Receipt Order 88 Total $12.00 Date 2026-11-01", 1200, "2026-11-01", "Canva"),
    ("Lyft\nTotal $9.75\n5 Nov 2026", 975, "2026-11-05", "Lyft"),
    ("Acme Corp\nInvoice #5\nTotal: $1,234.56\nDue 2026-11-30", 123456, "2026-11-30", "Acme Corp"),
]


@pytest.mark.parametrize("text, cents, day, vendor", RECEIPTS)
def test_the_parser_reads_the_total_the_date_and_the_vendor(text, cents, day, vendor):
    got = RCP.parse(text)
    assert (got.amount, got.date, got.vendor) == (cents, day, vendor)
    assert got.complete


def test_a_text_with_no_total_or_date_reads_as_nothing():
    got = RCP.parse("   \n  ")
    assert (got.amount, got.date, got.vendor, got.complete) == (None, None, "", False)


def test_without_a_total_label_the_largest_amount_is_the_total_and_no_date_is_not_complete():
    got = RCP.parse("Hotel Lumen\nRoom 4 x $120.00\nTax $10.80\nTax 1 $9.20")
    assert (got.amount, got.date, got.complete) == (12000, None, False)


# ------------------------------------------------------------------------------------------------ helpers
def _account(c, kind: str, name: str) -> str:
    mid = SL.new_id()
    c.execute(
        "INSERT INTO fin_money_accounts(id, book_id, kind, name, provider, account_id, created_at, created_by) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (mid, SL.book(c).id, kind, name, "mercury", account(c, "1000"), AT, OWNER),  # type: ignore[union-attr]
    )
    return mid


def _card(c, card: str, holder: str, program: str) -> None:
    c.execute(
        "INSERT INTO fin_cards(id, provider, holder, kind, currency, created_at, created_by, money_account_id) "
        "VALUES(?, 'mercury', ?, 'standing', ?, ?, ?, ?)",
        (card, holder, SL.book(c).base_currency, AT, OWNER, program),  # type: ignore[union-attr]
    )


def _charge(c, money: str, day: str, cents: int, name: str, card: str | None = None) -> str:
    """A posted debit on a money account (a card charge when `card` is given); a bank row otherwise."""
    fid = SL.new_id()
    c.execute(
        "INSERT INTO fin_feed_txns(id, money_account_id, provider_id, txn_date, amount, currency, counterparty, status, "
        "created_at, created_by, card_id) VALUES(?,?,?,?,?,?,?,'posted',?,?,?)",
        (fid, money, "txn-" + fid[:8], day, -cents, SL.book(c).base_currency, name, AT, OWNER, card),  # type: ignore
    )
    return fid


def _library_file(c, fid: str, text: str | None, bot: str = "coder") -> None:
    """A Library file of `bot`, its indexed text at version 1 (none for a PDF or an image: they are read by someone)."""
    # The bots_identity_insert trigger fires before OR IGNORE's key check, so insert a bot only when it is not there.
    if c.execute("SELECT 1 FROM bots WHERE id=?", (bot,)).fetchone() is None:
        c.execute("INSERT INTO bots(id,slug,display_name,state) VALUES(?,?,?,'active')", (bot, bot, bot.title()))
    c.execute(
        "INSERT INTO bot_files(id,bot_id,scope,identity,title,kind,locator,current_version,first_activity_at,"
        "last_activity_at) VALUES(?,?,'bot',?,'receipt.pdf','file','',1,?,?)",
        (fid, bot, fid, AT, AT),
    )
    if text is not None:
        FL.index(c, fid, 1, "receipt.pdf", text)


def _upload(c, fid: str) -> None:
    """The owner files the file as an unmatched receipt, the way documents.attach does."""
    filing = {"subject_kind": "unmatched", "subject_id": "", "file_ids": [fid]}
    assert act(c, OWNER, "attach", filing).status == "committed"


def _bot_upload(c, fid: str, bot: str) -> None:
    """A bot files its own file as an unmatched receipt: the attachment, then the receipt hook documents.attach calls."""
    SAP.attach(c, SL.book(c).id, "unmatched", "", [fid], bot, AT)  # type: ignore[union-attr]
    RCP.filed(load(c, AT), bot, "unmatched", "", [fid])


def _receipt(c, fid: str) -> dict:
    row = RS.by_file(c, SL.book(c).id, fid)  # type: ignore[union-attr]
    assert row is not None
    return dict(row)


def _attached(c, feed_id: str) -> list[str]:
    rows = c.execute("SELECT file_id FROM fin_attachments WHERE subject_kind='feed' AND subject_id=?", (feed_id,))
    return [r[0] for r in rows]


def _unmatched_files(c) -> list[str]:
    return [r[0] for r in c.execute("SELECT file_id FROM fin_attachments WHERE subject_kind='unmatched'")]


def _missing(c, viewer: str = OWNER, at: str = AT) -> list[str]:
    """The card charges the viewer sees with no receipt, as the Watch's missing_receipt item names them (feed ids)."""
    return [i["feed_id"] for i in FR.inbox(c, viewer, kind="missing_receipt", at=at)["items"]]


def _unmatched_items(c) -> list[str]:
    return [i["file_id"] for i in FR.inbox(c, OWNER, kind="unmatched_receipt", at=AT)["items"]]


def _suggested(c) -> tuple[str, str]:
    """A 12.00 Canva charge on the coder's card on Oct 14, and the Canva receipt filed for it: suggested on the charge.
    Returns (charge id, receipt id)."""
    program = _account(c, "card_program", "Card program")
    _card(c, "card_canva", BOT, program)
    charge = _charge(c, program, "2026-10-14", 1200, "Canva", "card_canva")
    _library_file(c, "file-canva", CANVA)
    _upload(c, "file-canva")
    return charge, _receipt(c, "file-canva")["id"]


# ------------------------------------------------------------------------------------------------ matching
def test_a_receipt_read_to_one_card_charge_is_suggested_at_confidence_of_at_least_point_nine(books):  # noqa: F811
    with books.transaction() as c:
        program = _account(c, "card_program", "Card program")
        _card(c, "card_canva", BOT, program)
        charge = _charge(c, program, "2026-10-14", 1200, "Canva", "card_canva")
        _library_file(c, "file-canva", CANVA)
        _upload(c, "file-canva")
        row = _receipt(c, "file-canva")
        assert row["status"] == "suggested"
        assert row["match_feed_id"] == charge
        assert row["confidence"] >= 0.9
        assert _attached(c, charge) == []  # a suggestion attaches nothing


def test_two_equally_good_charges_leave_the_receipt_unmatched(books):  # noqa: F811
    with books.transaction() as c:
        program = _account(c, "card_program", "Card program")
        _card(c, "card_canva", BOT, program)
        _charge(c, program, "2026-10-14", 1200, "Canva", "card_canva")
        _charge(c, program, "2026-10-14", 1200, "Canva", "card_canva")
        _library_file(c, "file-canva", CANVA)
        _upload(c, "file-canva")
        row = _receipt(c, "file-canva")
        assert (row["status"], row["match_feed_id"]) == ("unmatched", None)


def test_a_charge_that_syncs_later_turns_the_unmatched_receipt_into_a_suggestion(books):  # noqa: F811
    with books.transaction() as c:
        program = _account(c, "card_program", "Card program")
        _card(c, "card_canva", BOT, program)
        _library_file(c, "file-canva", CANVA)
        _upload(c, "file-canva")
        assert _receipt(c, "file-canva")["status"] == "unmatched"
        charge = _charge(c, program, "2026-10-14", 1200, "Canva", "card_canva")
        RCP.feed_changed(load(c, AT), charge)  # what banking.ingest calls for a settled row
        row = _receipt(c, "file-canva")
        assert (row["status"], row["match_feed_id"]) == ("suggested", charge)


def test_nothing_attaches_on_its_own_while_auto_attach_is_off(books):  # noqa: F811
    with books.transaction() as c:
        program = _account(c, "card_program", "Card program")
        _card(c, "card_canva", BOT, program)
        charge = _charge(c, program, "2026-10-16", 1200, "Canva", "card_canva")  # two days after the read: 0.96
        _library_file(c, "file-canva", CANVA)
        _upload(c, "file-canva")
        assert SL.setting(c, RCP.AUTO_KEY) is None
        row = _receipt(c, "file-canva")
        assert row["status"] == "suggested"
        assert row["confidence"] == pytest.approx(0.96)
        assert _attached(c, charge) == []


def test_with_the_setting_at_point_nine_a_match_of_point_nine_six_attaches(books):  # noqa: F811
    with books.transaction() as c:
        SL.set_setting(c, RCP.AUTO_KEY, 0.9, OWNER, AT)  # type: ignore[arg-type]
        program = _account(c, "card_program", "Card program")
        _card(c, "card_canva", BOT, program)
        charge = _charge(c, program, "2026-10-16", 1200, "Canva", "card_canva")
        _library_file(c, "file-canva", CANVA)
        _upload(c, "file-canva")
        row = _receipt(c, "file-canva")
        assert (row["status"], row["match_feed_id"]) == ("attached", charge)
        assert row["attachment_id"] is not None
        assert _attached(c, charge) == ["file-canva"]
        assert _unmatched_files(c) == []


# ------------------------------------------------------------------------------------------------ actions
def test_accept_files_the_receipt_to_the_row_and_clears_the_missing_and_unmatched_findings(books):  # noqa: F811
    with books.transaction() as c:
        program = _account(c, "card_program", "Card program")
        _card(c, "card_canva", BOT, program)
        charge = _charge(c, program, "2026-10-14", 1200, "Canva", "card_canva")
        _library_file(c, "file-canva", CANVA)
        _upload(c, "file-canva")
        rid = _receipt(c, "file-canva")["id"]
        assert _missing(c) == [charge]
        assert _unmatched_items(c) == ["file-canva"]
        assert act(c, OWNER, "receipt_match", {"receipt_id": rid, "decision": "accept"}).status == "committed"
        assert _attached(c, charge) == ["file-canva"]
        assert _missing(c) == []
        assert _unmatched_items(c) == []
        row = _receipt(c, "file-canva")
        assert (row["status"], row["match_feed_id"]) == ("attached", charge)
        assert c.execute("SELECT COUNT(*) FROM fin_outbox").fetchone()[0] == 0  # nothing goes to QuickBooks or Xero


def test_undo_puts_back_the_attachment_the_unmatched_receipt_and_the_suggestion(books):  # noqa: F811
    with books.transaction() as c:
        charge, rid = _suggested(c)
        assert act(c, OWNER, "receipt_match", {"receipt_id": rid, "decision": "accept"}).status == "committed"
        assert act(c, OWNER, "receipt_match_undo", {"receipt_id": rid}).status == "committed"
        assert _attached(c, charge) == []
        assert _missing(c) == [charge]
        assert _unmatched_items(c) == ["file-canva"]
        row = _receipt(c, "file-canva")
        assert (row["status"], row["match_feed_id"], row["attachment_id"]) == ("suggested", charge, None)


def test_a_refused_suggestion_is_never_suggested_to_that_row_again(books):  # noqa: F811
    with books.transaction() as c:
        charge, rid = _suggested(c)
        assert act(c, OWNER, "receipt_match", {"receipt_id": rid, "decision": "reject"}).status == "committed"
        row = _receipt(c, "file-canva")
        assert (row["status"], row["match_feed_id"]) == ("rejected", None)
        assert json.loads(row["rejected_feed_ids"]) == [charge]
        RCP.feed_changed(load(c, AT), charge)
        RCP.sweep(load(c, AT))
        row = _receipt(c, "file-canva")
        assert (row["status"], row["match_feed_id"]) == ("rejected", None)
        assert _attached(c, charge) == []


def test_accept_with_another_feed_id_files_the_receipt_to_that_row(books):  # noqa: F811
    with books.transaction() as c:
        charge, rid = _suggested(c)
        program = c.execute("SELECT money_account_id FROM fin_feed_txns WHERE id=?", (charge,)).fetchone()[0]
        other = _charge(c, program, "2026-10-15", 1250, "Later", "card_canva")  # too far off to be a candidate
        accept = {"receipt_id": rid, "decision": "accept", "feed_id": other}
        assert act(c, OWNER, "receipt_match", accept).status == "committed"
        assert _attached(c, other) == ["file-canva"]
        assert _attached(c, charge) == []
        assert _receipt(c, "file-canva")["match_feed_id"] == other


def test_a_bot_reads_a_receipt_it_looked_at_and_it_matches_again(books):  # noqa: F811
    with books.transaction() as c:
        program = _account(c, "card_program", "Card program")
        _card(c, "card_canva", BOT, program)
        charge = _charge(c, program, "2026-10-14", 1200, "Canva", "card_canva")
        _library_file(c, "file-pdf", None)  # a PDF: no indexed text, so no read yet
        _bot_upload(c, "file-pdf", BOT)
        rid = _receipt(c, "file-pdf")["id"]
        assert (_receipt(c, "file-pdf")["status"], _receipt(c, "file-pdf")["read_amount"]) == ("unread", None)
        read = {"receipt_id": rid, "amount": 1200, "date": "2026-10-14", "vendor": "Canva"}
        assert act(c, BOT, "receipt_read", read).status == "committed"
        row = _receipt(c, "file-pdf")
        assert (row["status"], row["match_feed_id"], row["read_by"]) == ("suggested", charge, BOT)
        assert (row["read_amount"], row["read_date"]) == (1200, "2026-10-14")


# ------------------------------------------------------------------------------------------------ rights and reads
def test_a_bot_accepts_a_match_on_its_own_card_and_no_one_else_sees_or_acts_on_it(books):  # noqa: F811
    with books.transaction() as c:
        charge, rid = _suggested(c)
        assert [r["id"] for r in RCP.read(c, BOT, at=AT)["rows"]] == [rid]
        assert [r["id"] for r in RCP.read(c, OWNER, at=AT)["rows"]] == [rid]
        assert RCP.read(c, OTHER, at=AT)["rows"] == []
        assert act(c, OTHER, "receipt_match", {"receipt_id": rid, "decision": "accept"}).status != "committed"
        assert _attached(c, charge) == []
        assert act(c, BOT, "receipt_match", {"receipt_id": rid, "decision": "accept"}).status == "committed"
        assert _attached(c, charge) == ["file-canva"]


def test_the_reads_and_the_search_show_the_read_and_the_match(books):  # noqa: F811
    with books.transaction() as c:
        charge, _ = _suggested(c)
        rows = RCP.read(c, OWNER, status="suggested", at=AT)["rows"]
        assert len(rows) == 1
        row = rows[0]
        assert row["file"] == {"id": "file-canva", "name": "receipt.pdf"}
        read = (row["read"]["amount"], row["read"]["date"], row["read"]["vendor"]["name"])
        assert read == (1200, "2026-10-14", "Canva")
        assert (row["match"]["feed"]["id"], row["match"]["feed"]["date"]) == (charge, "2026-10-14")
        assert row["match"]["confidence"] >= 0.9
        assert (row["status"], row["as_of"]) == ("suggested", AT)
        found = RCP.search(load(c, AT), OWNER, q="canva", status=None, most=10)
        assert [(r["kind"], r["label"]) for r in found] == [("receipt", "receipt.pdf")]
        assert RCP.search(load(c, AT), OTHER, q="canva", status=None, most=10) == []
        with pytest.raises(FinanceError):
            RCP.read(c, OWNER, status="paid", at=AT)


# ------------------------------------------------------------------------------------------------ briefing
def test_the_briefing_names_the_card_charges_with_no_receipt_and_counts_the_rest(books):  # noqa: F811
    with books.transaction() as c:
        program = _account(c, "card_program", "Card program")
        _card(c, "card_canva", BOT, program)
        _charge(c, program, "2026-11-01", 1200, "Canva", "card_canva")
        _charge(c, program, "2026-11-03", 900, "Later", "card_canva")
        assert RCP.brief_lines(c, BOT, NOV) == ["Needs you: 2 receipts missing (Canva Nov 1, Later Nov 3)."]
        _charge(c, program, "2026-11-05", 500, "Figma", "card_canva")
        assert RCP.brief_lines(c, BOT, NOV) == ["Needs you: 3 receipts missing (Canva Nov 1, Later Nov 3, +1)."]
        assert RCP.brief_lines(c, OTHER, NOV) == []  # a bot sees its own cards only


def test_the_briefing_counts_the_matches_to_check_and_is_silent_once_none_are_left(books):  # noqa: F811
    with books.transaction() as c:
        program = _account(c, "card_program", "Card program")
        _card(c, "card_canva", BOT, program)
        _charge(c, program, "2026-11-01", 1200, "Canva", "card_canva")
        _library_file(c, "file-canva", "Canva\nTotal $12.00\nNovember 1, 2026")
        _upload(c, "file-canva")
        rid = _receipt(c, "file-canva")["id"]
        assert RCP.brief_lines(c, BOT, NOV) == ["Needs you: 1 receipt missing (Canva Nov 1) · 1 match to check."]
        assert act(c, OWNER, "receipt_match", {"receipt_id": rid, "decision": "accept"}).status == "committed"
        assert RCP.brief_lines(c, BOT, NOV) == []


def test_receipts_do_not_move_money_into_the_books_outbox(books):  # noqa: F811
    with books.transaction() as c:
        before = c.execute("SELECT COUNT(*) FROM fin_outbox").fetchone()[0]
        charge, rid = _suggested(c)
        assert act(c, OWNER, "receipt_match", {"receipt_id": rid, "decision": "accept"}).status == "committed"
        assert act(c, OWNER, "receipt_match_undo", {"receipt_id": rid}).status == "committed"
        assert c.execute("SELECT COUNT(*) FROM fin_outbox").fetchone()[0] == before
