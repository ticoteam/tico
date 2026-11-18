"""Growth segments (backend/growth_segments.py).

- `growth_segments`: who the team sells to, each with its own rule for becoming a customer and churning (plain words),
  the inactivity window churn waits on (`churn_days`, 0 when churn is a cancel date), its own revenue per customer
  month, margin and minimum cohort, which fall back to the settings when blank.
- `growth_channels.segment_id`: the one segment a channel's spend is for; NULL is company-wide spend.
- `growth_customers`: `net_d90`, `net_d180`, `net_d365`, a customer's observed cumulative net revenue at that age, and
  `attribution`, which rung named its channel (utm, code, asked, unknown, or another short label).
"""


def up(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS growth_segments ("
        "id TEXT PRIMARY KEY, slug TEXT NOT NULL UNIQUE, name TEXT NOT NULL, customer_rule TEXT NOT NULL DEFAULT '', "
        "churn_days INTEGER NOT NULL DEFAULT 0, revenue_month REAL, margin_pct REAL, min_cohort INTEGER, "
        "sort INTEGER NOT NULL DEFAULT 0, owner_ref TEXT, retired_at TEXT, created_at TEXT NOT NULL, "
        "updated_at TEXT NOT NULL)"
    )
    channels = {row[1] for row in conn.execute("PRAGMA table_info(growth_channels)")}
    # The Growth line's input_kpi_id first, whichever of these pending migrations runs first: one column order.
    if "input_kpi_id" not in channels:
        conn.execute("ALTER TABLE growth_channels ADD COLUMN input_kpi_id TEXT")
    if "segment_id" not in channels:
        conn.execute("ALTER TABLE growth_channels ADD COLUMN segment_id TEXT")
    have = {row[1] for row in conn.execute("PRAGMA table_info(growth_customers)")}
    for column, kind in (("net_d90", "REAL"), ("net_d180", "REAL"), ("net_d365", "REAL"), ("attribution", "TEXT")):
        if column not in have:
            conn.execute(f"ALTER TABLE growth_customers ADD COLUMN {column} {kind}")
