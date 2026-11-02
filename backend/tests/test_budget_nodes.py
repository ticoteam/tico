"""Money limits as budget nodes (backend/usage_limits.py, backend/migrations/0071_budget_nodes.py): the migration moves a
bot's limits, the team and group pools and the company default into nodes and keeps the subscription switch; a save
writes and clears nodes; the readers answer as before."""
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

from backend import usage_limits
from backend.tests.test_api import api  # noqa: F401  (the api fixture)

MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "0071_budget_nodes.py"


def _migration():
    spec = importlib.util.spec_from_file_location("migration_0071", MIGRATION)
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
