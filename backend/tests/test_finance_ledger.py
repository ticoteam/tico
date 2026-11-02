"""The books: migrations 64 and 65 on an existing hub, setup, the posting invariants (seeded property test: balanced posts,
unbalanced never do, rollups always equal the lines), immutability and closed periods in the database itself."""
import random
import sqlite3
from contextlib import closing

import pytest

from backend.finance import actions as FA
from backend.finance.store import schema
from backend.tests.test_finance_fixtures import AT, OWNER, account, act, books, budget, finance_template, spend, store  # noqa: F401


def _rollups_match_lines(c: sqlite3.Connection) -> None:
    from_lines: dict[tuple[str, str, str, str, str], int] = {}
    tags: dict[str, dict[str, str]] = {}
    for line_id, kind, value in c.execute("SELECT line_id, kind, value FROM fin_line_tags"):
        tags.setdefault(line_id, {})[kind] = value
    for lid, day, acct, vendor, amount in c.execute("SELECT id, entry_date, account_id, contact_id, amount FROM fin_lines"):
        t = tags.get(lid, {})
        key = (day, acct, t.get("team", ""), t.get("teammate", ""), vendor)
        from_lines[key] = from_lines.get(key, 0) + amount
    rolled: dict[tuple[str, str, str, str, str], int] = {}
    for day, acct, team, mate, vendor, amount in c.execute(
            "SELECT day, account_id, team, teammate, vendor, amount FROM fin_rollups"):
        key = (day, acct, team, mate, vendor)
        rolled[key] = rolled.get(key, 0) + amount
    assert {k: v for k, v in rolled.items() if v} == {k: v for k, v in from_lines.items() if v}


def test_migration_64_on_an_existing_hub(store):
    with store.transaction() as c:
        assert c.execute("SELECT 1 FROM cloud_migrations WHERE version=64").fetchone()
        c.execute("DELETE FROM cloud_migrations WHERE version=64")
        for (name,) in c.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'fin_%'").fetchall():
            c.execute(f"DROP TRIGGER {name}")
        for (name,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'fin_%'").fetchall():
            c.execute(f"DROP TABLE {name}")
        c.execute("INSERT INTO registry_metadata(key, value) VALUES('kept', '1')")
    store.initialize(seed_market=False)
    store.initialize(seed_market=False)
    with store.read() as c:
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'fin_%'")}
        assert tables == {t for t in schema.table_names()}
        assert c.execute("SELECT value FROM registry_metadata WHERE key='kept'").fetchone()[0] == "1"
        assert c.execute("SELECT count(*) FROM cloud_migrations WHERE version=64").fetchone()[0] == 1


def test_migration_65_adds_phase_1_to_an_install_from_64(store):
    with store.transaction() as c:
        c.execute("DELETE FROM cloud_migrations WHERE version=65")
        c.execute("DROP TABLE fin_payments")
        # Migration 69 adds the Stripe indexes on raw_json, which an install from 64 does not have; SQLite will not
        # drop a column an index names.
        c.execute("DROP INDEX IF EXISTS fin_feed_txns_stripe_link")
        c.execute("DROP INDEX IF EXISTS fin_feed_txns_stripe_source")
        c.execute("ALTER TABLE fin_feed_txns DROP COLUMN raw")
    store.initialize(seed_market=False)
    with store.read() as c:
        assert c.execute("SELECT 1 FROM sqlite_master WHERE name='fin_payments'").fetchone()
        assert "raw" in {r[1] for r in c.execute("PRAGMA table_info(fin_feed_txns)")}
        assert c.execute("SELECT count(*) FROM cloud_migrations WHERE version=65").fetchone()[0] == 1


def test_setup_is_idempotent_and_leaves_ai_services_without_an_amount(store):
    from backend import usage_limits
    from backend.finance.store import budgets as SB
    with store.transaction() as c:
        usage_limits.save_pool(c, usage_limits.COMPANY, {k: (12.3 if k == "monthly_usd" else None)
                                                         for k in usage_limits.KEYS}, OWNER)
        first = FA.setup(c, OWNER, AT)
        again = FA.setup(c, OWNER, AT)
        assert first.created and not again.created and first.accounts == again.accounts
        assert first.budgets == again.budgets and set(first.budgets) == {"AI services", "Video production",
                                                                         "Other expenses"}
        assert c.execute("SELECT count(*) FROM fin_periods").fetchone()[0] == 12
        ai = first.budgets["AI services"]
        assert FA.budget_state(c, ai, AT).amount is None
        # An earlier setup's copy of the usage cap (its only version, written with the budget) goes, with its events.
        SB.insert_version(c, ai, "2026-10-01", 1230, "USD", OWNER, AT)
        SB.event(c, "finance.budget.cutoff", ai, "2026-10-01/v1", {}, OWNER, AT)
        FA.setup(c, OWNER, "2026-10-16T09:00:00.000000Z")
        FA.setup(c, OWNER, "2026-10-16T09:00:00.000000Z")
        assert FA.budget_state(c, ai, AT).amount is None
        assert c.execute("SELECT count(*) FROM fin_events WHERE subject=?", (ai,)).fetchone()[0] == 0
        # An amount a person set stays.
        later = "2026-10-16T10:00:00.000000Z"
        assert act(c, OWNER, "budget_update", {"budget_id": ai, "amount": 5_000}, at=later).status == "committed"
        FA.setup(c, OWNER, "2026-10-16T11:00:00.000000Z")
        assert FA.budget_state(c, ai, later).amount == 5_000


def test_setup_keeps_the_amount_of_a_replacement_ai_budget_a_person_made(books):
    with books.transaction() as c:
        ai, later = budget(c, "AI services"), "2026-10-16T10:00:00.000000Z"
        assert act(c, OWNER, "budget_update", {"budget_id": ai, "active": False}, at=later).status == "committed"
        made = act(c, OWNER, "budget_create", {"name": "AI spend", "account_id": account(c, "5100"), "amount": 7_000,
                                               "at_limit": "decline"}, at=later)
        assert made.status == "committed", made
        FA.setup(c, OWNER, "2026-10-16T11:00:00.000000Z")
        assert FA.budget_state(c, str(made.result["budget_id"]), later).amount == 7_000  # type: ignore[index]


def test_setup_drops_a_seeded_amount_on_an_ai_budget_switched_off_and_on_again(books):
    from backend.finance.store import budgets as SB
    with books.transaction() as c:
        b1, later = budget(c, "AI services"), "2026-10-16T10:00:00.000000Z"
        SB.insert_version(c, b1, "2026-10-01", 1230, "USD", OWNER, AT)              # an earlier setup's copy of the cap
        assert act(c, OWNER, "budget_update", {"budget_id": b1, "active": False}, at=later).status == "committed"
        made = act(c, OWNER, "budget_create", {"name": "AI spend", "account_id": account(c, "5100"), "amount": 7_000,
                                               "at_limit": "decline"}, at=later)
        b2 = str(made.result["budget_id"])  # type: ignore[index]
        assert act(c, OWNER, "budget_update", {"budget_id": b2, "active": False}, at=later).status == "committed"
        assert act(c, OWNER, "budget_update", {"budget_id": b1, "active": True}, at=later).status == "committed"
        FA.setup(c, OWNER, "2026-10-16T11:00:00.000000Z")
        assert FA.budget_state(c, b1, later).amount is None
        assert FA.budget_state(c, b2, later).amount == 7_000


def test_random_entries_post_only_when_balanced_and_rollups_follow(books):
    rng = random.Random(64)
    with books.transaction() as c:
        codes = ["1000", "1100", "2000", "5100", "5200", "6100", "6900", "4000"]
        ids = [account(c, x) for x in codes]
        teams = ["", "web", "eng", "sales"]
        posted = refused = 0
        for n in range(120):
            amounts = [rng.randint(-50_000, 50_000) or 1 for _ in range(rng.randint(1, 5))]
            balanced = rng.random() < 0.7
            amounts.append(-sum(amounts) + (0 if balanced else rng.choice([-1, 1, 7])))
            lines = [{"account_id": rng.choice(ids), "amount": a, "tags": {"team": rng.choice(teams)}}
                     for a in amounts if a]
            r = act(c, OWNER, "journal", {"date": f"2026-10-{rng.randint(1, 28):02d}", "memo": f"n{n}", "lines": lines})
            ok = balanced and len(lines) >= 2
            assert (r.status == "committed") == ok, (r, lines)
            posted += ok
            refused += not ok
            if rng.random() < 0.2 and r.status == "committed":
                line_id = str(r.result["line_ids"][0])  # type: ignore[index]
                act(c, OWNER, "categorize", {"lines": [{"line_id": line_id, "tags": {"team": rng.choice(teams[1:])}}],
                                             "mode": "replace"})
        assert posted > 50 and refused > 10
        total = c.execute("SELECT COALESCE(SUM(amount), 0) FROM fin_lines").fetchone()[0]
        assert total == 0
        _rollups_match_lines(c)


def test_posted_lines_are_immutable_and_closed_periods_refuse_posts(books):
    with books.transaction() as c:
        r = act(c, OWNER, "journal", {"date": "2026-09-05", "memo": "rent", "lines": [
            {"account_id": account(c, "6900"), "amount": 1000}, {"account_id": account(c, "1000"), "amount": -1000}]})
        assert r.status == "committed"
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("UPDATE fin_lines SET amount=5")
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("DELETE FROM fin_entries")
        assert act(c, OWNER, "close_period", {"month": "2026-09"}).status == "committed"
        late = act(c, OWNER, "journal", {"date": "2026-09-30", "lines": [
            {"account_id": account(c, "6900"), "amount": 5}, {"account_id": account(c, "1000"), "amount": -5}]})
        assert (late.status, late.code) == ("failed", "period_closed")
        tag = act(c, OWNER, "categorize", {"lines": [{"line_id": r.result["line_ids"][0], "tags": {"team": "web"}}]})
        assert (tag.status, tag.code) == ("failed", "period_closed")
        with pytest.raises(sqlite3.IntegrityError, match="period closed"):
            entry = c.execute("SELECT * FROM fin_entries LIMIT 1").fetchone()
            c.execute("INSERT INTO fin_entries(id, book_id, entry_date, source, created_at, created_by) "
                      "VALUES('x', ?, '2026-09-12', 'manual', ?, 'test')", (entry["book_id"], AT))
        refused = act(c, OWNER, "journal", {"reverses": r.result["entry_id"]})
        assert refused.code == "period_closed"
        assert act(c, OWNER, "reopen_period", {"month": "2026-09"}).status == "committed"


def test_reversal_works_after_the_account_is_deactivated(books):
    with books.transaction() as c:
        soft = account(c, "6100")
        r = act(c, OWNER, "journal", {"date": "2026-10-02", "lines": [
            {"account_id": soft, "amount": 2500}, {"account_id": account(c, "1000"), "amount": -2500}]})
        assert act(c, OWNER, "account_update", {"account_id": soft, "active": False}).status == "committed"
        again = act(c, OWNER, "journal", {"date": "2026-10-03", "lines": [
            {"account_id": soft, "amount": 1}, {"account_id": account(c, "1000"), "amount": -1}]})
        assert again.code == "invalid"
        back = act(c, OWNER, "journal", {"reverses": r.result["entry_id"]})
        assert back.status == "committed", back
        assert act(c, OWNER, "journal", {"reverses": r.result["entry_id"]}).code == "conflict"
        assert c.execute("SELECT COALESCE(SUM(amount), 0) FROM fin_lines WHERE account_id=?", (soft,)).fetchone()[0] == 0
        _rollups_match_lines(c)


def test_other_currencies_are_refused_until_fx_is_specified(books):
    with books.transaction() as c:
        r = act(c, OWNER, "journal", {"date": "2026-10-02", "lines": [
            {"account_id": account(c, "6100"), "currency": "EUR", "original_amount": 100, "fx_rate": "1.1"},
            {"account_id": account(c, "1000"), "amount": -110}]})
        assert (r.status, r.code) == ("failed", "invalid") and "EUR" in r.reason


def test_usage_postings_are_posted_once_per_source(books):
    with books.transaction() as c:
        first = spend(c, "5100", 700, source_id="gw-2026-10-10")
        second = spend(c, "5100", 700, source_id="gw-2026-10-10")
        assert first["posted"] and not second["posted"] and first["entry_id"] == second["entry_id"]
        assert c.execute("SELECT count(*) FROM fin_entries").fetchone()[0] == 1


def test_a_fresh_database_gets_the_finance_tables(tmp_path):
    from backend.store import Store
    from backend.tests.test_api import api_settings
    s = Store(api_settings(tmp_path))
    s.initialize(seed_market=False)
    with closing(sqlite3.connect(s.settings.db_path)) as c:
        assert c.execute("SELECT count(*) FROM sqlite_master WHERE name LIKE 'fin_%' AND type='trigger'").fetchone()[0] >= 5


def test_a_lines_role_is_recorded_where_tico_puts_it_kept_by_its_reversal_and_never_taken_from_a_request(books):
    """A feed row's money line is marked on the line (any position), its reversal keeps the mark, and a journal
    request cannot set one (card use is read from it)."""
    from backend.finance import posting
    from backend.finance.core.ledger import MONEY_ROLE
    with books.transaction() as c:
        lines = [{"account_id": account(c, "1000"), "amount": -700}, {"account_id": account(c, "6100"), "amount": 700}]
        done = posting.post_entry(c, {"date": "2026-10-03", "source": "card", "source_id": "row-1", "lines": lines},
                                  at=AT, roles={0: MONEY_ROLE})
        back = act(c, OWNER, "journal", {"reverses": done["entry_id"]})
        assert back.status == "committed", back

        def roles(entry_id):
            return [tuple(r) for r in c.execute("SELECT line_no, role FROM fin_lines WHERE entry_id=? ORDER BY line_no",
                                                (entry_id,))]
        assert roles(done["entry_id"]) == roles(back.result["entry_id"]) == [(0, "money"), (1, "")]
        forged = act(c, OWNER, "journal", {"date": "2026-10-04", "lines": [{**lines[0], "role": "money"}, lines[1]]})
        assert forged.status != "committed" and forged.code == "invalid"


def test_migration_74_adds_the_mirror_columns_and_credit_refunds_to_an_install_from_68(store):
    with store.transaction() as c:
        c.execute("DELETE FROM cloud_migrations WHERE version=74")
        c.execute("DROP TABLE fin_credit_refunds")
        c.execute("ALTER TABLE fin_outbox DROP COLUMN unknown")
        c.execute("ALTER TABLE fin_lines DROP COLUMN external_line")
        c.execute("ALTER TABLE fin_bill_lines DROP COLUMN external_line")
        c.execute("ALTER TABLE fin_invoice_lines DROP COLUMN external_line")
    store.initialize(seed_market=False)
    with store.read() as c:
        assert c.execute("SELECT 1 FROM sqlite_master WHERE name='fin_credit_refunds'").fetchone()
        for table, column in (("fin_outbox", "unknown"), ("fin_lines", "external_line"),
                              ("fin_bill_lines", "external_line"), ("fin_invoice_lines", "external_line")):
            assert column in {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
        assert c.execute("SELECT count(*) FROM cloud_migrations WHERE version=74").fetchone()[0] == 1
