"""The one view rule for records (backend/finance/visible.py), on a vendor: what each reader sees of it. Three vendors
share one month. Acme Cloud has a spending bot's line, a person's line and an untagged line. Mixed Co has a visible
line and a line on payroll (a private account). Payroll Co has only payroll. Readers: the owner; Cara, who leads Web (her
default view covers Web and the people under her); Ben, who leads Engineering (Web is under it); a spending bot with its
own expenses granted (coder); and a bot with nothing (seller).

A private line is left out of a total, which is marked excl; a vendor with only private lines shows nowhere to a reader
without full detail. Unattributed is the reader's slice of the lines with no team, no teammate and no attribution."""

import pytest

from backend.finance import reads as FR
from backend.finance import tab_ap
from backend.finance.core.errors import FinanceError
from backend.tests.test_finance_fixtures import (  # noqa: F401
    AT,
    OWNER,
    account,
    act,
    books,
    finance_template,
    spend,
    store,
)

CODER, SELLER = "bot:coder", "bot:seller"
CARA, BEN = "human:cara", "human:ben"
MONTH = ("2026-10-01", "2026-10-31")


def _post(c) -> dict[str, str]:
    ids = {
        name: str(act(c, OWNER, "contact_create", {"name": name, "kind": "vendor"}).result["contact_id"])
        for name in ("Acme Cloud", "Mixed Co", "Payroll Co")
    }
    made = act(
        c,
        OWNER,
        "account_create",
        {"code": "6200", "name": "Payroll", "type": "expense", "parent_id": account(c, "6000")},
    )
    assert made.status == "committed", made.reason
    private = {"account_id": str(made.result["account_id"]), "others_see": "totals"}
    assert act(c, OWNER, "private_lines_set", private).status == "committed"
    spend(c, "6100", 10_000, day="2026-10-05", team="web", teammate=CODER, vendor=ids["Acme Cloud"])
    spend(c, "6100", 5_000, day="2026-10-06", team="web", teammate="human:eve", vendor=ids["Acme Cloud"])
    spend(c, "6100", 2_000, day="2026-10-07", team="web", teammate="human:eve", vendor=ids["Mixed Co"])
    spend(c, "6200", 3_000, day="2026-10-08", team="web", teammate="human:eve", vendor=ids["Mixed Co"])
    spend(c, "6200", 9_000, day="2026-10-09", team="web", teammate="human:eve", vendor=ids["Payroll Co"])
    spend(c, "6100", 4_000, day="2026-10-10", vendor=ids["Acme Cloud"])  # no team, no teammate: Unattributed
    return ids


def _by_vendor(c, who: str) -> dict[str, tuple[int, bool]]:
    """The vendor rows a reader sees, by name: (total, excl). A vendor whose name is hidden has no row."""
    rows = FR.spend_by(c, who, "vendor", *MONTH, top_n=50, at=AT)["rows"]
    return {str(r["label"]): (int(r["amount"]), bool(r.get("excl"))) for r in rows if r["label"]}


def _search(c, who: str, q: str = "") -> dict[str, dict[str, object]]:
    """The vendors a reader's search lists. A reader with no finance view at all is refused (as every kind is), which
    lists nothing."""
    try:
        found = FR.search(c, who, kind="vendor", q=q, at=AT)["results"]
    except FinanceError:
        return {}
    return {str(r["name"]): r for r in found}


def test_the_owner_sees_every_name_and_every_total(books):
    with books.transaction() as c:
        _post(c)
        assert _by_vendor(c, OWNER) == {
            "Acme Cloud": (19_000, False),
            "Mixed Co": (5_000, False),
            "Payroll Co": (9_000, False),
        }
        found = _search(c, OWNER)
        assert set(found) == {"Acme Cloud", "Mixed Co", "Payroll Co"}
        assert found["Mixed Co"]["month"] == {"spend": 5_000, "excl": False, "as_of": AT}


@pytest.mark.parametrize("who", [CARA, BEN])
def test_a_manager_sees_the_names_of_their_lines_and_a_private_line_is_left_out_with_excl(books, who):
    with books.transaction() as c:
        _post(c)
        rows = _by_vendor(c, who)
        # Acme's untagged line is not theirs; Mixed Co's payroll line is private to them: the total says excl.
        assert rows == {"Acme Cloud": (15_000, False), "Mixed Co": (2_000, True)}
        assert "Payroll Co" not in rows, "a vendor with only private lines shows nowhere"
        found = _search(c, who)
        assert set(found) == {"Acme Cloud", "Mixed Co"}
        assert found["Mixed Co"]["month"] == {"spend": 2_000, "excl": True, "as_of": AT}


def test_a_vendor_with_only_private_lines_is_hidden_from_search_and_spend(books):
    with books.transaction() as c:
        _post(c)
        for who in (CARA, BEN, CODER, SELLER):
            assert "Payroll Co" not in _search(c, who, "Payroll")
            assert "Payroll Co" not in _by_vendor(c, who)


def test_a_spending_bot_sees_its_vendor_with_only_its_own_spend(books):
    with books.transaction() as c:
        _post(c)
        assert act(c, OWNER, "view_grant", {"subject": CODER, "what": "expenses", "whose": "own"}).status == "committed"
        assert _by_vendor(c, CODER) == {"Acme Cloud": (10_000, False)}
        found = _search(c, CODER)
        assert set(found) == {"Acme Cloud"}
        assert found["Acme Cloud"]["month"]["spend"] == 10_000


def test_another_bot_sees_nothing_of_the_vendor(books):
    with books.transaction() as c:
        _post(c)
        assert _by_vendor(c, SELLER) == {}
        assert _search(c, SELLER) == {}


def test_a_revoked_grant_changes_the_next_read(books):
    """Members already see their own expenses by default, so the grant here is the web team's expenses: lines SELLER
    cannot see without it (Acme's 15,000 on Web, plus SELLER's own 1,500 it sees anyway)."""
    with books.transaction() as c:
        ids = _post(c)
        spend(c, "6100", 1_500, day="2026-10-12", teammate=SELLER, vendor=ids["Acme Cloud"])
        assert _by_vendor(c, SELLER) == {"Acme Cloud": (1_500, False)}
        grant = act(
            c,
            OWNER,
            "view_grant",
            {"subject": SELLER, "what": "expenses", "whose": "team", "whose_team": "web"},
        )
        assert grant.status == "committed", grant.reason
        assert _by_vendor(c, SELLER) == {"Acme Cloud": (16_500, False)}
        before = FR.view_version(c)
        assert act(c, OWNER, "view_revoke", {"grant_id": grant.result["grant_id"]}).status == "committed"
        assert FR.view_version(c) == before + 1
        assert _by_vendor(c, SELLER) == {"Acme Cloud": (1_500, False)}


def test_a_name_in_search_matches_the_spend_its_row_reports(books):
    with books.transaction() as c:
        _post(c)
        for who in (OWNER, CARA):
            rows = _by_vendor(c, who)
            for name, row in _search(c, who).items():
                assert rows[name][0] == row["month"]["spend"], (who, name)


def test_unattributed_is_the_readers_slice_of_untagged_lines(books):
    with books.transaction() as c:
        _post(c)
        owner = FR.metric(c, OWNER, "unattributed", start=MONTH[0], end=MONTH[1], at=AT)
        assert (owner["amount"], owner["lines"], owner["excl"]) == (4_000, 1, False)
        assert owner["value"] == 4_000
        # a line with no team or teammate counts only in company-wide views: a manager does not see it
        manager = FR.metric(c, CARA, "unattributed", start=MONTH[0], end=MONTH[1], at=AT)
        assert (manager["amount"], manager["lines"]) == (0, 0)
        queue = FR.unattributed(c, OWNER, start=MONTH[0], end=MONTH[1], at=AT)
        assert [(r["amount"], r["vendor"]["name"], r["account"]["code"]) for r in queue["lines"]] == [
            (4_000, "Acme Cloud", "6100")
        ]
        assert FR.unattributed(c, CARA, start=MONTH[0], end=MONTH[1], at=AT)["lines"] == []


def test_unattributed_pages_newest_first(books):
    with books.transaction() as c:
        _post(c)
        spend(c, "6100", 500, day="2026-10-11")
        spend(c, "6100", 700, day="2026-10-12")
        first = FR.unattributed(c, OWNER, start=MONTH[0], end=MONTH[1], limit=2, at=AT)
        assert [r["amount"] for r in first["lines"]] == [700, 500]
        assert first["next_cursor"]
        second = FR.unattributed(c, OWNER, start=MONTH[0], end=MONTH[1], limit=2, cursor=first["next_cursor"], at=AT)
        assert [r["amount"] for r in second["lines"]] == [4_000]
        assert second["next_cursor"] is None


def test_a_full_detail_reader_sees_the_same_names_the_contact_list_gives(books):
    """The payables view and the owner see every vendor name (as before this rule): the contact list agrees."""
    with books.transaction() as c:
        _post(c)
        listed = {str(r["name"]) for r in _contacts(c, OWNER)}
        assert {"Acme Cloud", "Mixed Co", "Payroll Co"} <= listed


def _contacts(c, who: str) -> list[dict[str, object]]:
    try:
        return tab_ap.contacts(c, who, kind="vendor", limit=50, at=AT)["contacts"]
    except FinanceError:
        return []  # no finance view at all: refused, as the contact list always was


def test_the_contact_list_shows_a_manager_only_the_vendors_they_see(books):
    with books.transaction() as c:
        _post(c)
        assert {str(r["name"]) for r in _contacts(c, CARA)} == {"Acme Cloud", "Mixed Co"}
        assert _contacts(c, SELLER) == []


def test_the_view_rule_takes_the_window_so_a_quarter_outside_it_is_not_counted(books):
    with books.transaction() as c:
        _post(c)
        october = _by_vendor(c, CARA)
        assert october["Mixed Co"] == (2_000, True)
        other = FR.spend_by(c, CARA, "vendor", "2026-11-01", "2026-11-30", top_n=50, at=AT)["rows"]
        assert other == [], "no November lines: nothing for the manager, and no name for the private vendor"
