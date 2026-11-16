"""Money limits as budget nodes (backend/usage_limits.py): a save writes and clears nodes, and the readers answer as
before."""

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

from backend import usage_limits


def test_saves_write_and_clear_nodes_and_the_readers_answer_as_before(api):
    with api.app.state.store.transaction() as c:
        usage_limits.save_own(c, "ops", {"daily_usd": 2.5, "monthly_usd": 40}, "human:ana")
        usage_limits.save_pool(c, "group:eng", {"weekly_usd": 9}, "human:ana")
        usage_limits.save_default(c, {"daily_usd": 10}, False, "human:ana")
        assert usage_limits.own(c, "ops") == {
            "per_30m_usd": None,
            "hourly_usd": None,
            "daily_usd": 2.5,
            "weekly_usd": None,
            "monthly_usd": 40,
        }
        default = usage_limits.company(c)
        assert default["daily_usd"] == 10 and default["count_subscription"] is False
        assert usage_limits.pools(c) == {
            "group:eng": {
                "per_30m_usd": None,
                "hourly_usd": None,
                "daily_usd": None,
                "weekly_usd": 9,
                "monthly_usd": None,
            }
        }
        limits, source = usage_limits.effective(c, "cmo")
        assert limits["daily_usd"] == 10 and source["daily"] == "company"
        usage_limits.save_own(c, "ops", {"daily_usd": None, "monthly_usd": 40}, "human:ana")
        usage_limits.save_pool(c, "group:eng", {}, "human:ana")
        assert usage_limits.own(c, "ops")["daily_usd"] is None and usage_limits.pools(c) == {}
        assert c.execute("SELECT count(*) FROM budget_nodes WHERE bot_id='ops'").fetchone()[0] == 1
