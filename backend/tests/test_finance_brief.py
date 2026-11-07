"""What changed (backend/finance/brief.py): the lines a reader is handed are cut to what they see (a bot its own budget,
the owner the company's cash, burn and unattributed spend, a person with no view none), the brief is cached per reader
(same numbers keep `as_of`; a change in the reader's slice moves it and sets `changed_since`; a revoked grant changes
it), and a turn start is handed the lines only when there are some."""

from backend.finance import brief as BR
from backend.tests.test_finance_fixtures import OWNER, AT, account, act, books, spend  # noqa: F401

LATER = "2026-10-15T14:00:00.000000Z"
LATEST = "2026-10-15T15:00:00.000000Z"


def _software(c, amount=1_000_000):
    """The company's Software budget on 6100 (owner's), returned by id."""
    soft = account(c, "6100")
    made = act(
        c, OWNER, "budget_create", {"name": "Software", "account_id": soft, "amount": amount, "at_limit": "decline"}
    )
    assert made.status == "committed", made
    return str(made.result["budget_id"]), soft


def test_owner_brief_has_budget_cash_burn_and_unattributed(books):
    with books.transaction() as c:
        _software(c)
        spend(c, "6100", 25_000, day="2026-10-10")  # untagged: no team, no teammate
        got = BR.brief(c, OWNER, at=AT)
    lines = got["lines"]
    assert any(line.startswith("Budget: Software · $10,000. Spent $250") for line in lines), lines
    assert any(line.startswith("Cash $") for line in lines), lines
    assert any(line.startswith("Burn $") and "this month" in line for line in lines), lines
    assert "Unattributed: $250 this month." in lines, lines
    assert len(lines) <= BR.MAX_LINES and all(len(line) <= BR.LINE_CHARS for line in lines)
    assert got["as_of"] == AT and got["changed_since"] is None


def test_a_bot_sees_only_its_own_budget_and_no_company_lines(books):
    with books.transaction() as c:
        parent, soft = _software(c)
        mine = act(
            c,
            OWNER,
            "budget_create",
            {
                "name": "Coder ads",
                "account_id": soft,
                "teammate": "bot:coder",
                "parent_id": parent,
                "amount": 50_000,
                "at_limit": "warn",
            },
        )
        assert mine.status == "committed", mine
        spend(c, "6100", 25_000, day="2026-10-10", teammate="bot:coder")
        got = BR.brief(c, "bot:coder", at=AT)
        assert got["lines"] and got["lines"][0].startswith("Budget: Coder ads · $500. Spent $250"), got
        assert not any(line.startswith(("Cash", "Burn", "Unattributed", "Watch")) for line in got["lines"]), got
        assert BR.for_turn(c, "bot:coder")["lines"] == got["lines"]
        # A bot with no budget and no slice gets no lines, so its turn is handed no Finance block.
        assert BR.brief(c, "bot:bookkeeper", at=AT)["lines"] == []
        assert BR.for_turn(c, "bot:bookkeeper") is None


def test_a_person_with_no_finance_view_gets_no_lines(books):
    with books.transaction() as c:
        _software(c)
        spend(c, "6100", 25_000, day="2026-10-10")
        assert BR.brief(c, "human:dan", at=AT)["lines"] == []
        assert BR.for_turn(c, "human:dan") is None


def test_same_numbers_keep_as_of_and_a_change_in_the_slice_moves_it(books):
    with books.transaction() as c:
        _software(c)
        spend(c, "6100", 25_000, day="2026-10-10")
        first = BR.brief(c, OWNER, at=AT)
    with books.transaction() as c:
        again = BR.brief(c, OWNER, at=LATER)
        assert again["lines"] == first["lines"]
        assert again["as_of"] == first["as_of"], "same numbers keep the time they were worked out"
        assert again["changed_since"] is None
    with books.transaction() as c:
        spend(c, "6100", 12_000, day="2026-10-12")  # a new posted line in the owner's slice
        moved = BR.brief(c, OWNER, at=LATEST)
    assert moved["lines"] != first["lines"]
    assert moved["as_of"] == LATEST
    assert moved["changed_since"] == first["as_of"]


def test_a_revoked_grant_changes_the_brief(books):
    with books.transaction() as c:
        _software(c)
        spend(c, "6100", 5_000, day="2026-10-11")
        granted = act(c, OWNER, "view_grant", {"subject": "human:dan", "what": "expenses", "whose": "company"})
        assert granted.status == "committed", granted
        grant_id = granted.result["grant_id"]
        seen = BR.brief(c, "human:dan", at=AT)
        assert any(line.startswith("Unattributed:") for line in seen["lines"]), seen
    with books.transaction() as c:
        assert act(c, OWNER, "view_revoke", {"grant_id": grant_id}).status == "committed"
        gone = BR.brief(c, "human:dan", at=LATER)
        assert gone["lines"] == []
        assert gone["changed_since"] == seen["as_of"]
