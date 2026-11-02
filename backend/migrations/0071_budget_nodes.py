"""Money limits become budget nodes (backend/usage_limits.py): one row per amount, period and scope.

A bot's own limits (usage_limits) become `bot` nodes, the whole team's combined limit (usage_pools `company`) a
`company` node, a group's (`group:<id>`) a `team` node, and the company default for bots with no limit of their own
(registry_metadata `usage_limits`) `company` nodes with `per_bot` set. Amounts move from float USD to whole nanodollars;
an amount the readers already ignored (not above zero, or above the largest limit) is left behind. The default's
`count_subscription` switch stays in registry_metadata. Then usage_limits and usage_pools go.

Hand-off caps (task_caps), run budgets (bot_budget) and loop guards stay where they are: they are not money limits."""
import json

from backend import hubdb as H

TABLE = """
CREATE TABLE IF NOT EXISTS budget_nodes (
  id TEXT PRIMARY KEY,
  scope TEXT NOT NULL CHECK (scope IN ('company', 'team', 'bot')),
  team_id TEXT,
  bot_id TEXT,
  per_bot INTEGER NOT NULL DEFAULT 0 CHECK (per_bot IN (0, 1)),
  period TEXT NOT NULL CHECK (period IN ('per_30m', 'hourly', 'daily', 'weekly', 'monthly')),
  amount_nano INTEGER NOT NULL CHECK (amount_nano > 0),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  updated_by_id TEXT,
  CHECK ((scope = 'company' AND team_id IS NULL AND bot_id IS NULL)
      OR (scope = 'team' AND team_id IS NOT NULL AND bot_id IS NULL AND per_bot = 0)
      OR (scope = 'bot' AND bot_id IS NOT NULL AND team_id IS NULL AND per_bot = 0)))
"""
INDEX = ("CREATE UNIQUE INDEX IF NOT EXISTS budget_nodes_one "
         "ON budget_nodes(scope, coalesce(team_id, bot_id, ''), per_bot, period)")
PERIODS = ("per_30m", "hourly", "daily", "weekly", "monthly")
MAX_USD = 10_000_000
KEY = "usage_limits"


def _nano(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(round(round(number, 6) * 10**9)) if 0 < number <= MAX_USD else None


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _insert(conn, scope, team_id, bot_id, per_bot, row, found, stamp):
    for period in PERIODS:
        amount = _nano(row[period + "_usd"]) if period + "_usd" in found else None
        if amount is None:
            continue
        at = (row["updated_at"] if "updated_at" in found else None) or stamp
        by = row["updated_by"] if "updated_by" in found else None
        conn.execute("INSERT OR IGNORE INTO budget_nodes(id, scope, team_id, bot_id, per_bot, period, amount_nano, "
                     "created_at, updated_at, updated_by_id) VALUES(?,?,?,?,?,?,?,?,?,?)",
                     (H.new_id(), scope, team_id, bot_id, per_bot, period, amount, at, at, by))


def up(conn):
    conn.execute(TABLE)
    conn.execute(INDEX)
    stamp = H.now()
    found = _columns(conn, "usage_limits")
    if "bot" in found:
        for row in conn.execute("SELECT * FROM usage_limits ORDER BY bot").fetchall():
            _insert(conn, "bot", None, row["bot"], 0, row, found, stamp)
    found = _columns(conn, "usage_pools")
    if "scope" in found:
        for row in conn.execute("SELECT * FROM usage_pools ORDER BY scope").fetchall():
            scope = str(row["scope"] or "")
            if scope == "company":
                _insert(conn, "company", None, None, 0, row, found, stamp)
            elif scope.startswith("group:") and scope[6:]:
                _insert(conn, "team", scope[6:], None, 0, row, found, stamp)
    stored = conn.execute("SELECT value FROM registry_metadata WHERE key=?", (KEY,)).fetchone() \
        if "key" in _columns(conn, "registry_metadata") else None
    if stored:
        try:
            value = json.loads(stored[0] or "{}")
        except ValueError:
            value = {}
        value = value if isinstance(value, dict) else {}
        defaults = {name + "_usd": value.get(name + "_usd") for name in PERIODS}
        _insert(conn, "company", None, None, 1, defaults, set(defaults), stamp)
        conn.execute("UPDATE registry_metadata SET value=? WHERE key=?",
                     (json.dumps({"count_subscription": value.get("count_subscription") is not False}), KEY))
    conn.execute("DROP TABLE IF EXISTS usage_limits;")
    conn.execute("DROP TABLE IF EXISTS usage_pools;")
