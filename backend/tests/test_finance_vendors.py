"""Vendor identity (backend/finance/vendors.py): a bank row's vendor key is the merchant key of its text
(core/merchant.py); a row reads under the vendor it resolves to, so an alias added later applies to rows already
synced. vendor_merge folds names and contacts into one vendor: spend by vendor and budgets count the merged vendor as one,
a native book archives the merged contacts, and a mirrored book changes only aliases. vendor_unmerge takes the merge back
exactly. Watch groups charges by vendor, not by their text."""

import pytest

from backend.finance import actions as FA
from backend.finance import reads as FR
from backend.finance import vendors as VN
from backend.finance.core import kinds as KD
from backend.finance.core import rights as R
from backend.finance.core import watch as W
from backend.finance.core.errors import CONFLICT, DENIED, INVALID, NOT_FOUND
from backend.finance.core.merchant import merchant_key
from backend.finance.store import ap as SAP
from backend.tests.test_finance_fixtures import (  # noqa: F401
    AT,
    MERCURY,
    OWNER,
    FakeMercury,
    account,
    act,
    books,
    connect,
    finance_template,
    spend,
    store,
    sync_once,
)

EVE = "human:eve"  # a person with no finance rights


@pytest.mark.parametrize(
    "raw, key",
    [
        ("GOOGLE *ADS8812", "google ads"),
        ("GOOGLE ADS", "google ads"),
        ("SQ *BLUE BOTTLE 123", "blue bottle"),
        ("TST* JOE'S", "joe's"),
        ("AMZN Mktp US*2K4", "amazon mktp us"),
        ("PAYPAL *FIGMA", "figma"),
        ("META *ADS 4421", "meta ads"),
        ("STARBUCKS STORE #1234", "starbucks store"),
        ("Netflix.com", "netflix.com"),
        ("CHECKCARD 0412 NETFLIX", "netflix"),
        ("POS DEBIT WHOLE FOODS", "whole foods"),
        ("DEBIT CARD PURCHASE SHELL OIL", "shell oil"),
        ("ACH DEBIT ADOBE", "adobe"),
        ("PP*ZOOM.US", "zoom.us"),
        ("UBER *TRIP", "uber trip"),
        ("UBER   *EATS", "uber eats"),
        ("NOTION LABS, INC.", "notion labs inc"),
        ("Figma Inc 12345", "figma inc"),
        ("12345", "12345"),
        ("APPLE.COM/BILL", "apple.com bill"),
        ("sq *joes pizza new york", "joes pizza new york"),
        ("Google LLC", "google llc"),
        ("TST* SWEETGREEN 1234", "sweetgreen"),
        ("ÉCOLE 2 DANSE", "école danse"),
        ("GOOG *YOUTUBE", "google youtube"),
        ("zoom.us 888-799-9666", "zoom.us"),
        ("Joe's Diner", "joe's diner"),
        ("  AWS  ", "aws"),
        ("CARD SERVICES PMT", "services pmt"),
        ("WWW.GITHUB.COM", "www.github.com"),
    ],
)
def test_the_merchant_key_drops_the_processor_and_the_references(raw, key):
    assert merchant_key(raw) == key


def _txn(raw_id: str, amount: float, name: str, day: str) -> dict[str, object]:
    return {
        **MERCURY["txn_bank_fee"],
        "id": raw_id,
        "accountId": "acct-chk",
        "amount": amount,
        "kind": "debitCardTransaction",
        "createdAt": f"{day}T10:00:00Z",
        "postedAt": f"{day}T10:00:00Z",
        "counterpartyName": name,
        "cardId": None,
    }


def _books_id(c) -> str:
    return str(c.execute("SELECT id FROM fin_books").fetchone()[0])


def _feed_row(c, name: str) -> dict[str, object]:
    rows = FR.transactions(c, OWNER, source="feed", start="2026-01-01", end=AT[:10], limit=200)["rows"]
    return next(r for r in rows if r["counterparty"] == name)


def test_a_synced_row_reads_under_its_vendor_and_a_merge_made_later_applies_to_it(books):
    fake = FakeMercury()
    connect(books, fake)
    fake.txns["txn-v1"] = _txn("txn-v1", -120.0, "GOOGLE *ADS8812", "2026-10-02")
    sync_once(books, fake)
    with books.transaction() as c:
        key = c.execute("SELECT vendor_key FROM fin_feed_txns WHERE provider_id='txn-v1'").fetchone()[0]
        assert key == "google ads"
        assert _feed_row(c, "GOOGLE *ADS8812")["vendor"] == {"id": "google ads", "name": "GOOGLE *ADS8812"}
        ads = str(act(c, OWNER, "contact_create", {"name": "Google Ads", "kind": "vendor"}).result["contact_id"])
        merged = act(c, OWNER, "vendor_merge", {"into": ads, "merge": ["GOOGLE *ADS8812"]})
        assert merged.status == "committed", merged.reason
        assert _feed_row(c, "GOOGLE *ADS8812")["vendor"] == {"id": ads, "name": "Google Ads"}


def _merge_setup(books):
    """Google Ads (the vendor to keep) and Google LLC (a duplicate contact), with spend under each."""
    with books.transaction() as c:
        ads = str(act(c, OWNER, "contact_create", {"name": "Google Ads", "kind": "vendor"}).result["contact_id"])
        llc = str(act(c, OWNER, "contact_create", {"name": "Google LLC", "kind": "vendor"}).result["contact_id"])
        spend(c, "6100", 30_000, day="2026-10-10", vendor=llc)
        spend(c, "6100", 20_000, day="2026-10-11", vendor=ads)
    return ads, llc


def _by_vendor(c) -> dict[str, int]:
    rows = FR.spend_by(c, OWNER, "vendor", "2026-10-01", "2026-10-31", top_n=50, at=AT)["rows"]
    return {str(r["key"]): int(r["amount"]) for r in rows if int(r["amount"])}


def test_watch_groups_by_vendor_so_one_vendor_under_two_texts_is_one_duplicate(books):
    fake = FakeMercury()
    connect(books, fake)
    fake.txns["w1"] = _txn("w1", -120.0, "GOOGLE *ADS8812", "2026-10-02")
    fake.txns["w2"] = _txn("w2", -120.0, "GOOGLE ADS", "2026-10-03")
    fake.txns["w3"] = _txn("w3", -120.0, "GOOGLE ADS SERVICES", "2026-10-03")
    sync_once(books, fake)
    with books.transaction() as c:
        found = W.duplicate_charges(FR._charges(c, OWNER, AT))
    assert len(found) == 1
    assert found[0]["stamp"]["amount"] == 12_000


def test_a_price_increase_is_found_across_a_renamed_descriptor(books):
    fake = FakeMercury()
    connect(books, fake)
    fake.txns["n1"] = _txn("n1", -15.0, "NETFLIX *8812", "2026-08-05")
    fake.txns["n2"] = _txn("n2", -15.0, "NETFLIX", "2026-09-05")
    fake.txns["n3"] = _txn("n3", -25.0, "NETFLIX 4410", "2026-10-05")
    sync_once(books, fake)
    with books.transaction() as c:
        found = W.price_increases(FR._charges(c, OWNER, AT))
    assert len(found) == 1


def test_a_merge_makes_spend_and_budgets_one_vendor_and_unmerge_restores_exactly(books):
    ads, llc = _merge_setup(books)
    with books.transaction() as c:
        before = _by_vendor(c)
        assert before == {llc: 30_000, ads: 20_000}
        root = act(
            c,
            OWNER,
            "budget_create",
            {"name": "Software", "account_id": account(c, "6100"), "amount": 200_000, "at_limit": "warn"},
        )
        assert root.status == "committed", root.reason
        child = act(
            c,
            OWNER,
            "budget_create",
            {
                "name": "Google",
                "account_id": account(c, "6100"),
                "amount": 100_000,
                "at_limit": "warn",
                "parent_id": str(root.result["budget_id"]),
                "vendor": ads,
            },
        )
        assert child.status == "committed", child.reason
        bid = str(child.result["budget_id"])
        assert FA.budget_state(c, bid, AT).actual == 20_000
    with books.transaction() as c:
        merged = act(c, OWNER, "vendor_merge", {"into": ads, "merge": [llc]})
        assert merged.status == "committed", merged.reason
        keys = list(merged.result["keys"])
        assert llc in keys and merged.result["archived"] == [llc]
        assert _by_vendor(c) == {ads: 50_000}
        assert FA.budget_state(c, bid, AT).actual == 50_000
        assert c.execute("SELECT active FROM fin_contacts WHERE id=?", (llc,)).fetchone()[0] == 0
    with books.transaction() as c:
        undone = act(c, OWNER, "vendor_unmerge", {"into": ads, "keys": keys})
        assert undone.status == "committed", undone.reason
        assert _by_vendor(c) == before
        assert FA.budget_state(c, bid, AT).actual == 20_000
        assert c.execute("SELECT active FROM fin_contacts WHERE id=?", (llc,)).fetchone()[0] == 1
        for key in keys:
            assert SAP.alias_of(c, _books_id(c), key) == ""


def test_a_merge_refuses_itself_an_unknown_contact_and_a_name_another_contact_goes_by(books):
    with books.transaction() as c:
        ads = str(act(c, OWNER, "contact_create", {"name": "Google Ads", "kind": "vendor"}).result["contact_id"])
        meta = str(act(c, OWNER, "contact_create", {"name": "Meta Ads", "kind": "vendor"}).result["contact_id"])
        self_merge = act(c, OWNER, "vendor_merge", {"into": ads, "merge": [ads]})
        assert self_merge.code == INVALID
        unknown = act(c, OWNER, "vendor_merge", {"into": ads, "merge": ["00000000-0000-4000-8000-000000000000"]})
        assert unknown.code == NOT_FOUND
        assert act(c, OWNER, "vendor_merge", {"into": meta, "merge": ["META ADS"]}).status == "committed"
        taken = act(c, OWNER, "vendor_merge", {"into": ads, "merge": ["META ADS"]})
        assert taken.code == CONFLICT and "already goes by Meta Ads" in taken.reason


def test_a_mirrored_book_gets_only_the_aliases(books):
    ads, llc = _merge_setup(books)
    with books.transaction() as c:
        c.execute("UPDATE fin_books SET mode='mirror'")
        outbox = c.execute("SELECT count(*) FROM fin_outbox").fetchone()[0]
        merged = act(c, OWNER, "vendor_merge", {"into": ads, "merge": [llc]})
        assert merged.status == "committed", merged.reason
        assert merged.result["archived"] == []
        assert c.execute("SELECT count(*) FROM fin_outbox").fetchone()[0] == outbox
        assert c.execute("SELECT active FROM fin_contacts WHERE id=?", (llc,)).fetchone()[0] == 1
        assert SAP.alias_of(c, _books_id(c), llc) == ads


def test_only_a_holder_of_finance_book_may_merge(books):
    ads, llc = _merge_setup(books)
    with books.transaction() as c:
        denied = act(c, EVE, "vendor_merge", {"into": ads, "merge": [llc]})
        assert denied.code == DENIED
        assert c.execute("SELECT active FROM fin_contacts WHERE id=?", (llc,)).fetchone()[0] == 1


def test_vendor_merge_and_unmerge_are_live_kinds_that_need_the_book_right():
    for kind in ("vendor_merge", "vendor_unmerge"):
        assert kind in KD.ALL and kind in KD.LIVE and kind in KD.SHAPES and kind in KD.WORDS
        assert KD.NEEDS[kind] == R.BOOK
    assert set(VN.PLANNERS) == {"vendor_merge", "vendor_unmerge"}
