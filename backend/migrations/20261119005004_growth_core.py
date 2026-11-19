"""Growth core (backend/growth_core.py): cohort rows, costs, and a segment's value kind.

- `growth_cohort_rows`: the people in one cohort cell (source, anchor signup|customer, month, segment, channel,
  campaign) and how many of them reached each step or were kept, each with its eligible count; NULL is not supplied.
- `growth_costs`: money, promotions, bot cost and hours by source, month, channel, campaign, kind and purpose.
- `growth_segments.value_kind`: revenue (the default) or retention.
"""

# backend/migrations/20261118221359_growth_segments.py's table, word for word: whichever of the two pending migrations
# runs first makes it, so the schema is the same in either order.
SEGMENTS = (
    "CREATE TABLE IF NOT EXISTS growth_segments ("
    "id TEXT PRIMARY KEY, slug TEXT NOT NULL UNIQUE, name TEXT NOT NULL, customer_rule TEXT NOT NULL DEFAULT '', "
    "churn_days INTEGER NOT NULL DEFAULT 0, revenue_month REAL, margin_pct REAL, min_cohort INTEGER, "
    "sort INTEGER NOT NULL DEFAULT 0, owner_ref TEXT, retired_at TEXT, created_at TEXT NOT NULL, "
    "updated_at TEXT NOT NULL)"
)
COUNTS = (
    "activated",
    "activated_eligible",
    "paid",
    "paid_eligible",
    "kept_d30",
    "kept_d30_eligible",
    "kept_d90",
    "kept_d90_eligible",
    "kept_d180",
    "kept_d180_eligible",
)
NETS = ("net_d90", "net_d180", "net_d365")


def up(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS growth_cohort_rows ("
        "source TEXT NOT NULL, anchor TEXT NOT NULL, period TEXT NOT NULL, segment TEXT NOT NULL DEFAULT '', "
        "channel TEXT NOT NULL DEFAULT '', campaign TEXT NOT NULL DEFAULT '', as_of TEXT NOT NULL, "
        "entered INTEGER NOT NULL, "
        + "".join(f"{c} INTEGER, " for c in COUNTS)
        + "".join(f"{n}_sum REAL, {n}_eligible INTEGER, " for n in NETS)
        + "pushed_by TEXT NOT NULL, pushed_at TEXT NOT NULL, "
        "PRIMARY KEY (source, anchor, period, segment, channel, campaign))"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS growth_costs ("
        "source TEXT NOT NULL, period TEXT NOT NULL, channel TEXT NOT NULL DEFAULT '', "
        "campaign TEXT NOT NULL DEFAULT '', kind TEXT NOT NULL, purpose TEXT NOT NULL DEFAULT 'acquisition', "
        "amount REAL NOT NULL, quality TEXT NOT NULL DEFAULT 'actual', pushed_by TEXT NOT NULL, "
        "pushed_at TEXT NOT NULL, PRIMARY KEY (source, period, channel, campaign, kind, purpose))"
    )
    conn.execute(SEGMENTS)
    if "value_kind" not in {row[1] for row in conn.execute("PRAGMA table_info(growth_segments)")}:
        conn.execute("ALTER TABLE growth_segments ADD COLUMN value_kind TEXT NOT NULL DEFAULT 'revenue'")
