"""The Growth line (backend/growth_line.py): which input KPI a channel moves, and how often people use the line.

- `growth_channels.input_kpi_id`: the input KPI the channel moves, at most one; NULL is "No KPI linked".
- `growth_line_uses`: one row per person or bot, day and use (`trace`: opened where a number comes from; `source`:
  selected a source on the line), so the weekly share of Growth viewers who use it is a count, never a log of clicks.
"""


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def up(conn):
    channels = _columns(conn, "growth_channels")
    if channels and "input_kpi_id" not in channels:
        conn.execute("ALTER TABLE growth_channels ADD COLUMN input_kpi_id TEXT")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS growth_line_uses ("
        "day TEXT NOT NULL, actor TEXT NOT NULL, use TEXT NOT NULL CHECK(use IN ('trace','source')), "
        "PRIMARY KEY (day, actor, use))"
    )
