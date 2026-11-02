"""Watch checks (spec §11) as pure functions in finance core: runway, unusual spend, duplicate charges, price increases
and unused subscriptions, each silent when there is nothing to say. Plain values only, except the one test that reads a
card's bot facts from the hub store."""
from datetime import date

from backend.finance import reads as FR
from backend.finance.core import watch as W
from backend.finance.store import ledger as SL
from backend.finance.store import tico as ST
from backend.tests.test_finance_fixtures import AT, OWNER, account, act, books, finance_template, store  # noqa: F401


def charge(i: str, payee: str, amount: int, day: str) -> W.Charge:
    return W.Charge(id=i, payee=payee, amount=amount, day=date.fromisoformat(day))


def test_runway_is_news_under_six_months_of_burn_and_silent_without_burn():
    assert W.runway(4.2, 1_000_000)[0]["label"] == "Cash covers 4.2 months of average burn (under 6)"
    assert W.runway(6.0, 1_000_000) == []
    assert W.runway(None, 1_000_000) == []
    assert W.runway(3.0, None) == []


def test_unusual_spend_is_twice_the_usual_and_a_hundred_dollars_more_for_a_payee_already_spending():
    series = {"Acme": [10_000, 12_000, 11_000, 40_000], "Newco": [0, 0, 0, 90_000],
              "Steady": [10_000, 10_000, 10_000, 15_000]}
    found = W.unusual_spend(series, "2026-10")
    assert [f["key"] for f in found] == ["unusual_spend:Acme:2026-10"]
    assert found[0]["label"] == "Acme: 400.00 this month, usually 110.00"


def test_a_repeat_charge_within_three_days_is_a_possible_duplicate_and_a_week_apart_is_not():
    found = W.duplicate_charges([charge("a", "acme", 1_200, "2026-10-01"), charge("b", "acme", 1_200, "2026-10-03"),
                                 charge("c", "acme", 1_200, "2026-10-20"), charge("d", "acme", 900, "2026-10-02")])
    assert [f["key"] for f in found] == ["duplicate_charge:b"]


def test_a_recurring_charge_that_rises_ten_percent_and_ten_dollars_is_a_price_increase():
    rows = [charge("1", "cloudco", 5_000, "2026-08-01"), charge("2", "cloudco", 5_000, "2026-09-01"),
            charge("3", "cloudco", 6_000, "2026-10-01")]
    assert [f["key"] for f in W.price_increases(rows)] == ["price_increase:3"]
    assert W.price_increases(rows[:2]) == []
    # The two charges before the latest must be the same amount: a charge that already moved is not a recurring one.
    assert W.price_increases([charge("1", "x", 5_000, "2026-08-01"), charge("2", "x", 5_900, "2026-09-01"),
                              charge("3", "x", 6_000, "2026-10-01")]) == []


def test_new_posting_rules_lists_the_rules_made_since_a_time(books):  # noqa: F811
    with books.transaction() as c:
        made = act(c, OWNER, "rule_create", {"kind": "posting", "name": "Acme Cafe asks",
                                             "when": {"counterparty": "Acme Cafe"}, "outcome": {"outcome": "ask"},
                                             "examples": [{"input": {"counterparty": "Acme Cafe"}, "expect": "ask"}]})
        assert made.status == "committed", made
        assert [r["name"] for r in FR.new_posting_rules(c, "2026-10-08T00:00:00.000000Z")] == ["Acme Cafe asks"]
        assert FR.new_posting_rules(c, "2026-10-16T00:00:00.000000Z") == []


def test_a_rise_of_exactly_ten_percent_is_a_price_increase_and_a_cent_less_is_not():
    """Integer math: 110,000 is exactly ten percent above 100,000, which a float compare against 1.1 misses."""
    exact = [charge("1", "cloudco", 100_000, "2026-08-01"), charge("2", "cloudco", 100_000, "2026-09-01"),
             charge("3", "cloudco", 110_000, "2026-10-01")]
    assert [f["key"] for f in W.price_increases(exact)] == ["price_increase:3"]
    short = [charge("1", "cloudco", 100_000, "2026-08-01"), charge("2", "cloudco", 100_000, "2026-09-01"),
             charge("3", "cloudco", 109_999, "2026-10-01")]
    assert W.price_increases(short) == []


def test_a_monthly_charge_on_an_idle_bots_card_for_three_months_is_an_unused_subscription():
    def on(i: str, day: str) -> W.Charge:
        return W.Charge(id=i, payee="acme cloud", amount=4_900, day=date.fromisoformat(day), shown="ACME CLOUD",
                        card="card_a")
    months = [on("8", "2026-08-05"), on("9", "2026-09-05"), on("10", "2026-10-05")]
    idle = {"card_a": "archived-bot"}
    found = W.unused_subscriptions(months, idle, date(2026, 11, 1))
    assert [f["key"] for f in found] == ["unused_subscription:card_a:acme cloud"]
    assert found[0]["label"].startswith("ACME CLOUD: 49.00 a month on the card of archived-bot")
    assert W.unused_subscriptions(months[:2], idle, date(2026, 11, 1)) == []           # two months is not three
    assert W.unused_subscriptions([months[0], months[2]], idle, date(2026, 11, 1)) == []  # a gap month
    assert W.unused_subscriptions(months, {}, date(2026, 11, 1)) == []                 # the bot is not idle
    assert W.unused_subscriptions(months, idle, date(2026, 10, 15)) == []              # October is still open


def test_a_card_held_by_an_archived_bot_or_one_with_no_turn_in_thirty_days_is_idle(books):  # noqa: F811
    at = "2026-11-01T00:00:00.000000Z"
    with books.transaction() as c:
        c.execute("INSERT INTO bots(slug, state, last_turn_at) VALUES('idle', 'active', '2026-09-20T09:00:00.000000Z')")
        c.execute("INSERT INTO bots(slug, state) VALUES('never', 'active')")
        c.execute("UPDATE bots SET state='archived' WHERE slug='coder'")
        c.execute("UPDATE bots SET last_turn_at='2026-10-30T09:00:00.000000Z' WHERE slug='seller'")
        for card, bot in (("card_coder", "coder"), ("card_seller", "seller"), ("card_idle", "idle"),
                          ("card_never", "never")):
            c.execute("INSERT INTO fin_cards(id, provider, holder, kind, currency, created_at, created_by) "
                      "VALUES(?, 'mercury', ?, 'standing', 'USD', ?, ?)", (card, "bot:" + bot, at, OWNER))
        # A card someone already cancelled is not a problem to raise, whoever held it.
        c.execute("INSERT INTO fin_cards(id, provider, holder, kind, currency, created_at, created_by, status) "
                  "VALUES('card_gone', 'mercury', 'bot:coder', 'standing', 'USD', ?, ?, 'cancelled')", (at, OWNER))
        assert ST.idle_bot_cards(c, at, 30) == {"card_coder": "coder", "card_idle": "idle", "card_never": "never"}


PRIOR_MONTHS = ("2026-08-05", "2026-09-05", "2026-10-05")
WATCH_AT = "2026-11-01T00:00:00.000000Z"


def _bot_card_charges(c, slug: str, card: str, days: tuple[str, ...], amount: int = 4_900) -> None:
    """A card held by bot `slug`, on a checking account, with one posted card charge on each of `days`: the rows the
    bank feed brings in for a card (fin_feed_txns with card_id)."""
    money = SL.new_id()
    book = SL.book(c)
    c.execute("INSERT INTO fin_money_accounts(id, book_id, kind, name, provider, account_id, created_at, created_by) "
              "VALUES(?,?,?,?,?,?,?,?)", (money, book.id, "bank", "Checking", "mercury", account(c, "1000"), AT, OWNER))  # type: ignore[union-attr]
    c.execute("INSERT INTO fin_cards(id, provider, holder, kind, currency, created_at, created_by, money_account_id) "
              "VALUES(?, 'mercury', ?, 'standing', ?, ?, ?, ?)",
              (card, "bot:" + slug, book.base_currency, AT, OWNER, money))  # type: ignore[union-attr]
    for day in days:
        c.execute("INSERT INTO fin_feed_txns(id, money_account_id, provider_id, txn_date, amount, currency, counterparty, "
                  "status, created_at, created_by, card_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                  (SL.new_id(), money, f"txn-{SL.new_id()[:8]}", day, -amount, book.base_currency,  # type: ignore[union-attr]
                   "ACME CLOUD", "new", AT, OWNER, card))


def test_a_monthly_card_charge_on_an_archived_bots_card_is_an_unused_subscription_from_the_books(books):  # noqa: F811
    with books.transaction() as c:
        c.execute("INSERT INTO bots(slug, state) VALUES('quiet', 'archived')")
        _bot_card_charges(c, "quiet", "card_quiet", PRIOR_MONTHS)
        found = [f for f in FR.watch_findings(c, OWNER, WATCH_AT) if f["kind"] == "unused_subscription"]
        assert [f["key"] for f in found] == ["unused_subscription:card_quiet:acme cloud"]


def test_the_same_monthly_card_charge_on_an_active_bots_card_is_not_an_unused_subscription(books):  # noqa: F811
    with books.transaction() as c:
        c.execute("INSERT INTO bots(slug, state, last_turn_at) VALUES('busy', 'active', '2026-10-30T09:00:00.000000Z')")
        _bot_card_charges(c, "busy", "card_busy", PRIOR_MONTHS)
        assert [f for f in FR.watch_findings(c, OWNER, WATCH_AT) if f["kind"] == "unused_subscription"] == []
