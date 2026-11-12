"""A company that connects only QuickBooks or Xero (no bank, no card feed): its books read without a bank, its provider
classes, departments and projects map to teams by attribution rules (attribution_map), and Watch reads its posted
expense entries as charges. Tico never writes any of this to the provider: no outbox row is made."""

import sqlite3
from typing import Any

from backend.finance import reads as FR
from backend.finance.core import mirror as M
from backend.finance.store import ledger as SL
from backend.finance.core.errors import DENIED, INVALID
from backend.finance.store import attribution as ATT
from backend.tests.helpers.finance_fixtures import AT, OWNER, account, act, books, finance_template, store  # noqa: F401
from backend.tests.helpers.finance_mirror import FakeBooks, _bank_row, _connection, _drain, _local, _mirror, acct

EVE = "human:eve"  # a web teammate who has no attribution right
COLLEAGUE_BOT = "bot:coder"
OCTOBER = ("2026-10-01", "2026-10-31")
# October's posted expense: Marketing 14,700 (Acme 4,000 and 700; Metro Ads 5,000 twice), Ops 12,000 (Cloud Co).
OCTOBER_TOTAL = 26_700


def _purchase(n: int, day: str, amount: int, *, vendor: str, klass: str = "", dept: str = "") -> M.Journal:
    """A purchase from a vendor: debit Software (Account:2), credit the bank (Account:1), tagged with the class and
    department its provider names."""
    tags = tuple((kind, key) for kind, key in (("class", klass), ("location", dept)) if key)
    return M.Journal(
        M.Ext("JournalEntry", str(n), "1"),
        day,
        "purchase",
        "sync",
        (M.EntryLine("Account:2", amount, party=vendor, tags=tags), M.EntryLine("Account:1", -amount)),
    )


def _quickbooks(store: Any) -> tuple[FakeBooks, str]:
    """A QuickBooks book (not yet mirrored): a bank, a software account, three vendors, two classes and a department.
    Metro Ads is paid twice on one day; Cloud Co's monthly charge rises 20% in October."""
    fake = FakeBooks()
    fake.records["accounts"] = [acct(1, "1000", "Bank", "asset"), acct(2, "6100", "Software")]
    fake.records["parties"] = [
        M.Party(M.Ext("Vendor", "9", "1"), "Acme Software", "vendor"),
        M.Party(M.Ext("Vendor", "10", "1"), "Metro Ads", "vendor"),
        M.Party(M.Ext("Vendor", "11", "1"), "Cloud Co", "vendor"),
    ]
    fake.records["records"] = [
        M.Dimension(M.Ext("Class", "7", "1"), "Marketing"),
        M.Dimension(M.Ext("Class", "8", "1"), "Ops"),
        M.Location(M.Ext("Department", "3", "1"), "Austin"),
    ]
    fake.records["entries"] = [
        _purchase(1, "2026-10-03", 4_000, vendor="Vendor:9", klass="Class:7"),
        _purchase(2, "2026-10-02", 5_000, vendor="Vendor:10", klass="Class:7"),
        _purchase(3, "2026-10-02", 5_000, vendor="Vendor:10", klass="Class:7"),
        _purchase(4, "2026-08-01", 10_000, vendor="Vendor:11", klass="Class:8"),
        _purchase(5, "2026-09-01", 10_000, vendor="Vendor:11", klass="Class:8"),
        _purchase(6, "2026-10-01", 12_000, vendor="Vendor:11", klass="Class:8"),
        _purchase(7, "2026-10-05", 700, vendor="Vendor:9", klass="Class:7", dept="Department:3"),
    ]
    return fake, _connection(store)


def _mirror_book(store: Any) -> tuple[FakeBooks, str]:
    fake, cid = _quickbooks(store)
    _mirror(store, cid)
    assert _drain(store, fake, cid) == []
    return fake, cid


def _outbox(c: sqlite3.Connection) -> int:
    return int(c.execute("SELECT COUNT(*) FROM fin_outbox").fetchone()[0])


def _ids(c: sqlite3.Connection) -> dict[str, str]:
    return {
        "marketing": _local(c, "Class:7", "fin_dimensions"),
        "ops": _local(c, "Class:8", "fin_dimensions"),
        "austin": _local(c, "Department:3", "locations"),
        "acme": _local(c, "Vendor:9", "fin_contacts"),
        "metro": _local(c, "Vendor:10", "fin_contacts"),
    }


def _spend_by(c: sqlite3.Connection, by: str) -> dict[str, int]:
    """October's spend by `by` (team or vendor), keyed by each row's key (FR.NONE is the unattributed row)."""
    rows = FR.spend_by(c, OWNER, by, *OCTOBER, top_n=50, at=AT)["rows"]
    return {str(r["key"]): int(r["amount"]) for r in rows}


def _map(c: sqlite3.Connection, items: list[dict[str, object]]) -> None:
    r = act(c, OWNER, "attribution_map", {"items": items})
    assert r.status == "committed", r.reason


# ------------------------------------------------------------------------------------------------ no bank
def test_a_mirrored_book_with_no_bank_feed_reads_without_error(books):
    _mirror_book(books)
    with books.read() as c:
        assert c.execute("SELECT COUNT(*) FROM fin_feed_txns").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM fin_money_accounts").fetchone()[0] == 0
        assert sum(_spend_by(c, "vendor").values()) == OCTOBER_TOTAL
        assert FR.pnl(c, OWNER, *OCTOBER, at=AT)
        assert FR.budget_vs_actual(c, OWNER, *OCTOBER, "month", at=AT)["budgets"] is not None
        assert FR.transactions(c, OWNER, at=AT) is not None
        assert FR.attribution_rules(c, OWNER, at=AT) is not None
        assert FR.attribution_dimensions(c, OWNER, at=AT)["dimensions"]
        assert FR.watch_findings(c, OWNER, AT) is not None


# ------------------------------------------------------------------------------------------------ class map
def test_a_class_maps_its_lines_to_a_team_and_remapping_and_clearing_move_them(books):
    _mirror_book(books)
    with books.transaction() as c:
        ids = _ids(c)
        before = _outbox(c)
        _map(c, [{"kind": "class", "id": ids["marketing"], "team": "sales"}])
        assert _spend_by(c, "team") == {"sales": 14_700, FR.NONE: 12_000}
        _map(c, [{"kind": "class", "id": ids["marketing"], "team": "web"}])
        assert _spend_by(c, "team") == {"web": 14_700, FR.NONE: 12_000}
        _map(c, [{"kind": "class", "id": ids["marketing"]}])  # neither team nor teammate clears the rule
        assert _spend_by(c, "team") == {FR.NONE: OCTOBER_TOTAL}
        assert _outbox(c) == before


def test_a_vendor_rule_beats_a_class_rule_and_a_person_override_beats_both(books):
    _mirror_book(books)
    with books.transaction() as c:
        ids = _ids(c)
        before = _outbox(c)
        _map(c, [{"kind": "class", "id": ids["marketing"], "team": "sales"}])
        r = act(
            c,
            OWNER,
            "attribution_rule_set",
            {
                "name": "Metro Ads",
                "match": {"kind": "vendor", "id": ids["metro"]},
                "splits": [{"team": "web", "share_bps": 10_000}],
                "apply_from": "2026-10-01",
            },
        )
        assert r.status == "committed", r.reason
        assert _spend_by(c, "team") == {"web": 10_000, "sales": 4_700, FR.NONE: 12_000}
        acme_line = c.execute(
            "SELECT l.id FROM fin_lines l JOIN fin_line_tags t ON t.line_id=l.id WHERE t.kind='class' AND t.value=? "
            "AND l.contact_id=? AND l.amount=4000",
            (ids["marketing"], ids["acme"]),
        ).fetchone()
        assert acme_line is not None
        override = act(
            c, OWNER, "attribute", {"line_id": str(acme_line[0]), "splits": [{"team": "eng", "share_bps": 10_000}]}
        )
        assert override.status == "committed", override.reason
        assert _spend_by(c, "team") == {"web": 10_000, "eng": 4_000, "sales": 700, FR.NONE: 12_000}
        assert _outbox(c) == before


def test_a_class_rule_is_judged_before_a_department_rule(books):
    _mirror_book(books)
    with books.transaction() as c:
        ids = _ids(c)
        _map(c, [{"kind": "location", "id": ids["austin"], "team": "eng"}])
        assert _spend_by(c, "team") == {"eng": 700, FR.NONE: 26_000}  # the one Austin line, which has no class rule yet
        _map(c, [{"kind": "class", "id": ids["marketing"], "team": "sales"}])
        assert _spend_by(c, "team") == {"sales": 14_700, FR.NONE: 12_000}  # the class beats the department


def test_the_dimensions_read_lists_each_class_and_department_with_its_rule(books):
    _mirror_book(books)
    with books.transaction() as c:
        ids = _ids(c)
        _map(c, [{"kind": "class", "id": ids["marketing"], "team": "sales"}])
        rows = FR.attribution_dimensions(c, OWNER, at=AT)["dimensions"]
        by_id = {r["id"]: r for r in rows}
        assert (by_id[ids["ops"]]["name"], by_id[ids["ops"]]["lines"], by_id[ids["ops"]]["amount"]) == (
            "Ops",
            3,
            32_000,
        )
        assert (by_id[ids["marketing"]]["lines"], by_id[ids["marketing"]]["amount"]) == (4, 14_700)
        assert by_id[ids["marketing"]]["rule_id"] is not None
        assert by_id[ids["marketing"]]["splits"][0]["team"] == "sales"
        assert by_id[ids["ops"]]["rule_id"] is None
        amounts = [int(r["amount"]) for r in rows]
        assert amounts == sorted(amounts, reverse=True)


def test_the_dimensions_read_and_the_map_need_the_attribution_right(books):
    _mirror_book(books)
    with books.transaction() as c:
        ids = _ids(c)
        item = [{"kind": "class", "id": ids["marketing"], "team": "sales"}]
        refused = act(c, EVE, "attribution_map", {"items": item})
        assert refused.status == "denied" and refused.code == DENIED
        assert _spend_by(c, "team") == {FR.NONE: OCTOBER_TOTAL}
        assert act(c, OWNER, "right_grant", {"teammate": COLLEAGUE_BOT, "right": "finance.attribution"}).status == (
            "committed"
        )
        bot = act(c, COLLEAGUE_BOT, "attribution_map", {"items": item})
        assert bot.status == "committed", bot.reason  # a bot with the right commits with no approval
        assert _spend_by(c, "team")["sales"] == 14_700


def test_a_map_names_a_real_record_and_one_item_per_dimension(books):
    _mirror_book(books)
    with books.transaction() as c:
        ids = _ids(c)
        assert act(c, OWNER, "attribution_map", {"items": [{"kind": "class", "id": "nope", "team": "sales"}]}).code == (
            INVALID
        )
        twice = [{"kind": "class", "id": ids["ops"], "team": "sales"}] * 2
        assert act(c, OWNER, "attribution_map", {"items": twice}).code == INVALID
        unknown_team = [{"kind": "class", "id": ids["ops"], "team": "nope"}]
        assert act(c, OWNER, "attribution_map", {"items": unknown_team}).code == INVALID


# ------------------------------------------------------------------------------------------------ budgets
def test_a_budget_on_a_team_counts_the_lines_its_class_maps_to(books):
    _mirror_book(books)
    with books.transaction() as c:
        ids = _ids(c)
        parent = act(
            c, OWNER, "budget_create", {"name": "Software", "account_id": account(c, "6100"), "amount": 900_000}
        )
        assert parent.status == "committed", parent.reason
        child = act(
            c,
            OWNER,
            "budget_create",
            {
                "name": "Sales software",
                "account_id": account(c, "6100"),
                "parent_id": str(parent.result["budget_id"]),
                "team": "sales",
                "amount": 500_000,
            },
        )
        assert child.status == "committed", child.reason
        child_id = str(child.result["budget_id"])
        _map(c, [{"kind": "class", "id": ids["marketing"], "team": "sales"}])
        report = FR.budget_vs_actual(c, OWNER, *OCTOBER, "month", at=AT)
        row = next(r for r in report["budgets"] if r["id"] == child_id)
        assert sum(row["actual"]) == 14_700


# ------------------------------------------------------------------------------------------------ watch
def _findings(c: sqlite3.Connection, kind: str) -> list[dict[str, Any]]:
    return [f for f in FR.watch_findings(c, OWNER, AT) if f["kind"] == kind]


def test_watch_reads_mirrored_purchases_for_a_duplicate_and_a_price_increase(books):
    _mirror_book(books)
    with books.read() as c:
        assert len(_findings(c, "duplicate_charge")) == 1
        assert len(_findings(c, "price_increase")) == 1


def test_watch_leaves_out_the_entries_of_a_money_account_that_has_feed_rows(books):
    _mirror_book(books)
    with books.transaction() as c:
        _bank_row(c, -700, "2026-10-09")
    with books.read() as c:
        # Every purchase was paid from the bank, and the bank now has feed rows: the feed is the money, not the entries.
        assert _findings(c, "duplicate_charge") == []
        assert _findings(c, "price_increase") == []


# ------------------------------------------------------------------------------------------------ units
def test_rule_precedence_is_vendor_then_money_account_then_account_then_class_location_project():
    def rule(rid: str, kind: str, mid: str) -> dict[str, Any]:
        return {"id": rid, "match_kind": kind, "match_id": mid, "enabled": 1}

    tags = {"class": "cls", "location": "loc", "project": "prj"}
    facts = ATT.facts_of_tags("acct-6100", ("vendor-1",), "bank-1", tags)
    rows = [rule("p", "project", "prj"), rule("l", "location", "loc"), rule("c", "class", "cls")]
    assert ATT.match_rule(rows, facts)["id"] == "c"
    rows.append(rule("a", "account", "acct-6100"))
    assert ATT.match_rule(rows, facts)["id"] == "a"
    rows.append(rule("m", "money_account", "bank-1"))
    assert ATT.match_rule(rows, facts)["id"] == "m"
    rows.append(rule("v", "vendor", "vendor-1"))
    assert ATT.match_rule(rows, facts)["id"] == "v"
    assert ATT.match_rule([rule("c", "class", "other")], facts) is None
    assert ATT.match_rule([rule("c", "class", "cls")], ATT.facts_of_tags("acct-6100", (), "", {})) is None


def test_a_tico_kept_book_counts_a_bill_once_when_its_feed_payment_clears_it(books):
    """A Tico-kept book posts a bill to payables and the bank's payment is the charge: Watch reads the feed row only.
    The bill's expense entry must not count as a second charge."""
    with books.transaction() as c:
        assert SL.book(c).mode != "mirror"  # type: ignore[union-attr]
        made = act(c, OWNER, "contact_create", {"name": "Acme Software", "kind": "vendor"})
        assert made.status == "committed", made
        vendor = str(made.result["contact_id"])
        bill = act(
            c,
            OWNER,
            "bill_create",
            {
                "vendor_id": vendor,
                "bill_date": "2026-10-10",
                "lines": [{"account_id": account(c, "6100"), "amount": 4_000}],
            },
        )
        assert bill.status == "committed", bill
        assert act(c, OWNER, "bill_approve", {"bill_id": str(bill.result["bill_id"])}).status == "committed"
        _money, feed_id = _bank_row(c, -4_000, "2026-10-12")
    with books.read() as c:
        charges = FR._charges(c, OWNER, AT)
        assert [ch.id for ch in charges] == [feed_id]
