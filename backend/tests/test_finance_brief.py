"""What changed (backend/finance/brief.py): the lines a reader is handed are cut to what they see (a spending bot its own
budget, a manager bot its team's budget, over-pace teammates and the team's unattributed spend, the owner the company's
cash, burn and unattributed spend, a person with no view none). The brief is cached per reader (same numbers keep
`as_of`; a change in the reader's slice moves it and sets `changed_since`; a revoked grant changes it), and a turn start
is handed the lines only when they differ from the ones that reader was last handed."""

from backend.finance import brief as BR
from backend.tests.helpers.finance_fixtures import AT, OWNER, account, act, books, finance_template, spend, store

LATER = "2026-10-15T14:00:00.000000Z"
LATEST = "2026-10-15T15:00:00.000000Z"
COLLEAGUE = "bot:seller"


def _software(c, amount=1_000_000):
    """The company's Software budget on 6100, returned with its account id."""
    soft = account(c, "6100")
    made = act(
        c, OWNER, "budget_create", {"name": "Software", "account_id": soft, "amount": amount, "at_limit": "decline"}
    )
    assert made.status == "committed", made
    return str(made.result["budget_id"]), soft


def _coder_budget(c, parent: str, soft: str, amount: int = 50_000, team: str = "") -> None:
    """The coding bot's ad budget ($500 by default) under `parent`, on `team` when it nests under a team's budget."""
    mine = act(
        c,
        OWNER,
        "budget_create",
        {
            "name": "Coder ads",
            "account_id": soft,
            "teammate": "bot:coder",
            "parent_id": parent,
            "amount": amount,
            "at_limit": "warn",
            **({"team": team} if team else {}),
        },
    )
    assert mine.status == "committed", mine


def test_owner_brief_has_budget_cash_burn_and_unattributed(books):
    with books.transaction() as c:
        _software(c)
        spend(c, "6100", 25_000, day="2026-10-10")  # untagged: no team, no teammate
        got = BR.brief(c, OWNER, at=AT)
    lines = got["lines"]
    assert any(line.startswith("Budget: Software · $10,000. Spent $250") for line in lines), lines
    assert any(line.startswith("Cash $") for line in lines), lines
    assert any(line.startswith("Burn $") and "this month" in line for line in lines), lines
    assert "Unattributed: $250 (1 line)." in lines, lines
    assert len(lines) <= BR.MAX_LINES and all(len(line) <= BR.LINE_CHARS for line in lines)
    assert got["as_of"] == AT and got["changed_since"] is None


def test_a_spending_bot_sees_only_its_own_budget_and_no_company_lines(books):
    with books.transaction() as c:
        parent, soft = _software(c)
        _coder_budget(c, parent, soft)
        spend(c, "6100", 25_000, day="2026-10-10", teammate="bot:coder")
        got = BR.brief(c, "bot:coder", at=AT)
        assert got["lines"][0].startswith("Budget: Coder ads · $500. Spent $250 (50%). Pace $"), got
        assert not any(line.startswith(("Cash", "Burn", "Unattributed", "Watch")) for line in got["lines"]), got
        # A bot with no budget and no slice gets no lines, so its turn is handed no Finance block.
        assert BR.brief(c, "bot:finance-owner", at=AT)["lines"] == []
        assert BR.for_turn(c, "bot:finance-owner") is None


def test_a_budget_pacing_past_its_amount_reads_over_budget(books):
    with books.transaction() as c:
        parent, soft = _software(c)
        _coder_budget(c, parent, soft, amount=50_000)
        spend(c, "6100", 25_000, day="2026-10-10", teammate="bot:coder")  # half a month spent: paces past $500
        over = BR.brief(c, "bot:coder", at=AT)["lines"][0]
        assert over.endswith(", over budget."), over
        made = act(
            c,
            OWNER,
            "budget_create",
            {
                "name": "Small",
                "account_id": soft,
                "teammate": "bot:seller",
                "parent_id": parent,
                "amount": 1_000_000,
                "at_limit": "warn",
            },
        )
        assert made.status == "committed", made
        spend(c, "6100", 1_000, day="2026-10-10", teammate="bot:seller")  # $10 spent: far under $10,000
        under = BR.brief(c, "bot:seller", at=AT)["lines"][0]
        assert under.startswith("Budget: Small · $10,000. Spent $10"), under
        assert "over budget" not in under, under


def test_a_manager_bot_sees_the_team_budget_its_over_pace_teammates_and_its_unattributed_spend(books):
    with books.transaction() as c:
        parent, soft = _software(c)
        web = act(
            c,
            OWNER,
            "budget_create",
            {
                "name": "Web",
                "account_id": soft,
                "team": "web",
                "parent_id": parent,
                "amount": 900_000,
                "at_limit": "warn",
            },
        )
        assert web.status == "committed", web
        _coder_budget(c, str(web.result["budget_id"]), soft, team="web")  # nests under Web: no sibling overlap
        granted = act(
            c, OWNER, "view_grant", {"subject": COLLEAGUE, "what": "expenses", "whose": "team", "whose_team": "web"}
        )
        assert granted.status == "committed", granted
        spend(c, "6100", 25_000, day="2026-10-10", team="web", teammate="bot:coder")
        spend(c, "6100", 14_000, day="2026-10-12", team="web")  # a team's spend with no teammate: $140
        got = BR.brief(c, COLLEAGUE, at=AT)
    lines = got["lines"]
    assert any(line.startswith("Team: Web · $9,000. Spent $") for line in lines), lines
    # The coder's budget paces past $500: a manager sees it by name, with its pace.
    assert any(line.startswith("Over pace: coder (Coder ads: pace $") and "vs $500)" in line for line in lines), lines
    assert "Unattributed in your team: $140 (1 line)." in lines, lines
    assert not any(line.startswith(("Cash", "Burn", "Unattributed:")) for line in lines), lines


def test_a_person_with_no_finance_view_gets_no_lines(books):
    with books.transaction() as c:
        _software(c)
        spend(c, "6100", 25_000, day="2026-10-10")
        assert BR.brief(c, "human:eve", at=AT)["lines"] == []
        assert BR.for_turn(c, "human:eve") is None


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


def test_a_line_outside_the_readers_slice_leaves_their_brief_alone(books):
    with books.transaction() as c:
        parent, soft = _software(c)
        _coder_budget(c, parent, soft)
        spend(c, "6100", 25_000, day="2026-10-10", teammate="bot:coder")
        first = BR.brief(c, "bot:coder", at=AT)
    with books.transaction() as c:
        spend(c, "6100", 12_000, day="2026-10-12", teammate=COLLEAGUE)  # the seller's spend, not the coder's
        same = BR.brief(c, "bot:coder", at=LATER)
    assert same["lines"] == first["lines"]
    assert same["as_of"] == first["as_of"] and same["changed_since"] is None


def test_a_revoked_grant_changes_the_brief(books):
    with books.transaction() as c:
        _software(c)
        spend(c, "6100", 5_000, day="2026-10-11")
        granted = act(c, OWNER, "view_grant", {"subject": "human:dan", "what": "expenses", "whose": "company"})
        assert granted.status == "committed", granted
        grant_id = granted.result["grant_id"]
        seen = BR.brief(c, "human:dan", at=AT)
        assert "Unattributed: $50 (1 line)." in seen["lines"], seen
    with books.transaction() as c:
        assert act(c, OWNER, "view_revoke", {"grant_id": grant_id}).status == "committed"
        gone = BR.brief(c, "human:dan", at=LATER)
        assert gone["lines"] == []
        assert gone["changed_since"] == seen["as_of"]


def test_turn_start_hands_lines_only_when_the_fingerprint_changed(books):
    with books.transaction() as c:
        _software(c)
        spend(c, "6100", 25_000, day="2026-10-10")
        first = BR.for_turn(c, OWNER, at=AT)
        assert first is not None and first["lines"], first
        before = c.total_changes
        assert BR.for_turn(c, OWNER, at=LATER) is None, "same numbers, already handed: nothing this turn"
        assert c.total_changes == before, "an unchanged turn start reads and compares, and writes nothing"
    with books.transaction() as c:
        spend(c, "6100", 12_000, day="2026-10-12")
        again = BR.for_turn(c, OWNER, at=LATEST)
        assert again is not None and again["lines"] != first["lines"]
        assert again["changed_since"] == first["as_of"]


def test_an_on_demand_brief_does_not_use_up_the_turn_start_hand_off(books):
    with books.transaction() as c:
        parent, soft = _software(c)
        _coder_budget(c, parent, soft)
        spend(c, "6100", 25_000, day="2026-10-10", teammate="bot:coder")
        asked = BR.brief(c, "bot:coder", at=AT)  # tico_finance_brief answers on demand
        assert asked["lines"]
        handed = BR.for_turn(c, "bot:coder", at=LATER)  # the turn start still gets them: nothing was handed yet
        assert handed is not None and handed["lines"] == asked["lines"]


def test_an_install_without_finance_hands_no_lines_and_never_fails_a_turn(store):
    with store.transaction() as c:
        assert BR.compute(c, OWNER, AT) == []
        assert BR.for_turn(c, "bot:coder", at=AT) is None


def test_a_failing_read_hands_no_lines_instead_of_failing_the_turn(books, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("a finance read broke")

    monkeypatch.setattr(BR, "compute", boom)
    with books.transaction() as c:
        assert BR.for_turn(c, OWNER, at=AT) is None
        c.execute("SELECT 1").fetchone()  # the claim's transaction is still usable
