"""Money limits as budget nodes (backend/usage_limits.py, backend/migrations/0054_budget_nodes.py): the migration moves a
bot's limits, the team and group pools and the company default into nodes and keeps the subscription switch; a save
writes and clears nodes; the readers answer as before."""
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

from backend import usage_limits
from backend.tests.test_api import api  # noqa: F401  (the api fixture)

MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "0054_budget_nodes.py"


def _migration():
    spec = importlib.util.spec_from_file_location("migration_0054", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_migration_moves_every_limit_into_budget_nodes():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
CREATE TABLE registry_metadata (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE usage_limits (bot TEXT PRIMARY KEY, daily_usd REAL, monthly_usd REAL, updated_at TEXT, updated_by TEXT,
  hourly_usd REAL, weekly_usd REAL, per_30m_usd REAL);
CREATE TABLE usage_pools (scope TEXT PRIMARY KEY, hourly_usd REAL, daily_usd REAL, weekly_usd REAL, monthly_usd REAL,
  updated_at TEXT, updated_by TEXT, per_30m_usd REAL);
INSERT INTO usage_limits VALUES ('ops', 5, NULL, '2026-10-01T00:00:00Z', 'human:ana', 1.25, NULL, 0.7);
INSERT INTO usage_limits VALUES ('cmo', 0, -3, NULL, NULL, NULL, NULL, NULL);
INSERT INTO usage_pools VALUES ('company', NULL, 80, NULL, NULL, NULL, NULL, NULL);
INSERT INTO usage_pools VALUES ('group:eng', NULL, NULL, NULL, 400, NULL, NULL, 3);
INSERT INTO registry_metadata VALUES ('usage_limits', '{"daily_usd": 20, "monthly_usd": 1e12, "count_subscription": false}');
""")
    module = _migration()
    module.up(conn)
    module.up(conn)                                      # the tables are gone; nothing doubles
    rows = {(r["scope"], r["team_id"], r["bot_id"], r["per_bot"], r["period"]): (r["amount_nano"], r["updated_by_id"])
            for r in conn.execute("SELECT * FROM budget_nodes")}
    assert rows == {
        ("bot", None, "ops", 0, "daily"): (5_000_000_000, "human:ana"),
        ("bot", None, "ops", 0, "hourly"): (1_250_000_000, "human:ana"),
        ("bot", None, "ops", 0, "per_30m"): (700_000_000, "human:ana"),
        ("bot", None, "cmo", 0, "daily"): (0, None),                   # its own 0; -3 is left behind
        ("company", None, None, 0, "daily"): (80_000_000_000, None),
        ("team", "eng", None, 0, "monthly"): (400_000_000_000, None),
        ("team", "eng", None, 0, "per_30m"): (3_000_000_000, None),
        ("company", None, None, 1, "daily"): (20_000_000_000, None),   # the default; an amount over the cap is left
    }
    assert json.loads(conn.execute("SELECT value FROM registry_metadata").fetchone()[0]) == {"count_subscription": False}
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert not {"usage_limits", "usage_pools"} & tables
    with pytest.raises(sqlite3.IntegrityError):         # a node names one scope, consistently
        conn.execute("INSERT INTO budget_nodes VALUES ('x','team',NULL,'ops',0,'daily',1,'','',NULL)")


def test_saves_write_and_clear_nodes_and_the_readers_answer_as_before(api):
    with api.app.state.store.transaction() as c:
        usage_limits.save_own(c, "ops", {"daily_usd": 2.5, "monthly_usd": 40}, "human:ana")
        usage_limits.save_pool(c, "group:eng", {"weekly_usd": 9}, "human:ana")
        usage_limits.save_default(c, {"daily_usd": 10}, False, "human:ana")
        assert usage_limits.own(c, "ops") == {"per_30m_usd": None, "hourly_usd": None, "daily_usd": 2.5,
                                              "weekly_usd": None, "monthly_usd": 40}
        default = usage_limits.company(c)
        assert default["daily_usd"] == 10 and default["count_subscription"] is False
        assert usage_limits.pools(c) == {"group:eng": {"per_30m_usd": None, "hourly_usd": None, "daily_usd": None,
                                                       "weekly_usd": 9, "monthly_usd": None}}
        limits, source = usage_limits.effective(c, "cmo")
        assert limits["daily_usd"] == 10 and source["daily"] == "company"
        usage_limits.save_own(c, "ops", {"daily_usd": None, "monthly_usd": 40}, "human:ana")
        usage_limits.save_pool(c, "group:eng", {}, "human:ana")
        assert usage_limits.own(c, "ops")["daily_usd"] is None and usage_limits.pools(c) == {}
        assert c.execute("SELECT count(*) FROM budget_nodes WHERE bot_id='ops'").fetchone()[0] == 1


def test_limits_set_through_the_api_read_back_identically_after_the_move(api):
    """Amounts the API stored as REAL USD (rounded to six decimals) become nanodollars and read back as the same floats:
    the API's own answer before and after the migration is identical."""
    from backend.tests.test_api import get
    from backend.tests.test_usage_limits import put
    awkward = {"per_30m_usd": 0.35, "daily_usd": 1234.567891, "weekly_usd": 9999999.999999, "monthly_usd": 0.000001}
    put(api, "usage/limits/ops", awkward)
    put(api, "usage/limits", {"daily_usd": 12.345678, "monthly_usd": 0.1 + 0.2, "count_subscription": False})
    put(api, "usage/pools/company", {"per_30m_usd": 2.675, "weekly_usd": 80.1})

    def answer():
        found = get(api, "usage/limits")
        company = found["pools"]["company"]
        return (found["default"], {k: v for k, v in found["bots"]["ops"].items() if k.startswith("own_")},
                {k: company[k] for k in usage_limits.KEYS})
    before = answer()
    assert before[1] == {"own_" + k: awkward.get(k) for k in usage_limits.KEYS}
    default, own, pool = before
    with api.app.state.store.transaction() as c:
        # The same amounts as the release before budget nodes kept them: REAL USD columns and the default's registry entry.
        c.execute("CREATE TABLE usage_limits (bot TEXT PRIMARY KEY, daily_usd REAL, monthly_usd REAL, updated_at TEXT, "
                  "updated_by TEXT, hourly_usd REAL, weekly_usd REAL, per_30m_usd REAL)")
        c.execute("CREATE TABLE usage_pools (scope TEXT PRIMARY KEY, hourly_usd REAL, daily_usd REAL, weekly_usd REAL, "
                  "monthly_usd REAL, updated_at TEXT, updated_by TEXT, per_30m_usd REAL)")
        columns = ", ".join(usage_limits.KEYS)
        c.execute(f"INSERT INTO usage_limits(bot, {columns}) VALUES(?,?,?,?,?,?)",
                  ("ops", *(own["own_" + k] for k in usage_limits.KEYS)))
        c.execute(f"INSERT INTO usage_pools(scope, {columns}) VALUES(?,?,?,?,?,?)",
                  ("company", *(pool[k] for k in usage_limits.KEYS)))
        c.execute("UPDATE registry_metadata SET value=? WHERE key=?", (json.dumps(default), usage_limits.KEY))
        c.execute("DELETE FROM budget_nodes")
        _migration().up(c)
    assert answer() == before


def _rewind(c):
    """Put every budget node back where the release before budget nodes kept it (REAL USD columns and the default's
    registry entry), then empty budget_nodes, so the migration can move them again."""
    found = usage_limits.nodes(c)
    counted = usage_limits.company(c)["count_subscription"]
    columns = ", ".join(usage_limits.KEYS)
    c.execute("CREATE TABLE usage_limits (bot TEXT PRIMARY KEY, daily_usd REAL, monthly_usd REAL, updated_at TEXT, "
              "updated_by TEXT, hourly_usd REAL, weekly_usd REAL, per_30m_usd REAL)")
    c.execute("CREATE TABLE usage_pools (scope TEXT PRIMARY KEY, hourly_usd REAL, daily_usd REAL, weekly_usd REAL, "
              "monthly_usd REAL, updated_at TEXT, updated_by TEXT, per_30m_usd REAL)")
    default = {"count_subscription": counted}
    for key, caps in found.items():
        values = [caps.get(k) for k in usage_limits.KEYS]
        if key[0] == "bot":
            c.execute(f"INSERT INTO usage_limits(bot, {columns}) VALUES(?,?,?,?,?,?)", (key[1], *values))
        elif key[0] == "pool":
            c.execute(f"INSERT INTO usage_pools(scope, {columns}) VALUES(?,?,?,?,?,?)", (key[1], *values))
        else:
            default.update({k: caps.get(k) for k in usage_limits.KEYS})
    c.execute("UPDATE registry_metadata SET value=? WHERE key=?", (json.dumps(default), usage_limits.KEY))
    c.execute("DELETE FROM budget_nodes")


def test_a_limit_the_api_stored_as_zero_still_overrides_the_default_and_caps_nothing_after_the_move(api):
    """The API keeps six decimals, so $0.0000001 is stored as 0.0. A bot's own 0 is its own value: the company default
    does not apply to that period, and a cap of 0 holds nothing back. A pool's 0 beside a real cap is kept the same way;
    a pool with nothing above zero is no pool."""
    from backend.tests.test_api import get
    from backend.tests.test_usage_limits import put
    put(api, "usage/limits", {"daily_usd": 5, "weekly_usd": 0.0000001})
    put(api, "usage/limits/ops", {"daily_usd": 0.0000001, "monthly_usd": 7})
    put(api, "usage/pools/company", {"per_30m_usd": 0.0000001, "weekly_usd": 80})
    put(api, "usage/pools/company", {"daily_usd": 9})

    def state():
        with api.app.state.store.read() as c:
            caps, source = usage_limits.effective(c, "ops")
            return (get(api, "usage/limits"), caps, source, usage_limits.pools(c), usage_limits.company(c),
                    usage_limits.state(c, "ops"))
    before = state()
    _, caps, source, pools, default, _ = before
    assert (caps["daily_usd"], source["daily"]) == (0.0, "bot")            # its own 0, not the default's $5
    assert (caps["monthly_usd"], source["monthly"]) == (7, "bot")
    assert default["weekly_usd"] is None                                   # the default never kept a 0
    assert pools["company"] == {"per_30m_usd": 0.0, "hourly_usd": None, "daily_usd": 9, "weekly_usd": 80,
                                "monthly_usd": None}
    assert not before[5]["blocked"]
    with api.app.state.store.transaction() as c:
        _rewind(c)
        _migration().up(c)
    assert state() == before

    put(api, "usage/pools/company", {"daily_usd": None, "weekly_usd": None})   # only the 0 is left: no pool
    with api.app.state.store.read() as c:
        assert usage_limits.pools(c) == {}
        assert not c.execute("SELECT 1 FROM budget_nodes WHERE scope='company' AND per_bot=0").fetchone()


def test_the_migration_keeps_a_bots_and_a_pools_zero_and_drops_an_empty_pool():
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
CREATE TABLE registry_metadata (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE usage_limits (bot TEXT PRIMARY KEY, daily_usd REAL, monthly_usd REAL);
CREATE TABLE usage_pools (scope TEXT PRIMARY KEY, daily_usd REAL, monthly_usd REAL);
INSERT INTO usage_limits VALUES ('ops', 0.0, NULL);
INSERT INTO usage_pools VALUES ('company', 0.0, 50);
INSERT INTO usage_pools VALUES ('group:eng', 0.0, NULL);
INSERT INTO registry_metadata VALUES ('usage_limits', '{"daily_usd": 0, "monthly_usd": 3}');
""")
    _migration().up(conn)
    rows = {(r[0], r[1], r[2], r[3], r[4]): r[5] for r in conn.execute(
        "SELECT scope, team_id, bot_id, per_bot, period, amount_nano FROM budget_nodes")}
    assert rows == {("bot", None, "ops", 0, "daily"): 0, ("company", None, None, 0, "daily"): 0,
                    ("company", None, None, 0, "monthly"): 50_000_000_000,
                    ("company", None, None, 1, "monthly"): 3_000_000_000}
    with pytest.raises(sqlite3.IntegrityError):         # the default has no 0
        conn.execute("INSERT INTO budget_nodes VALUES ('x','company',NULL,NULL,1,'daily',0,'','',NULL)")
