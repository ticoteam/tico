"""Growth computes retention and LTV from customers (backend/growth_customers.py, backend/growth_computed.py).

- `growth_customers`: one row per customer spell a team's source gave: the team's own customer key (never a name or an
  email: the load refuses those), the dates as dates in the team's time zone (signed up, became customer, churned), the
  channel slug, a segment and a plan label, and revenue per month. A customer who churned and came back has a second
  spell, keyed by its own "became customer" date. A load replaces every row of its source.
- `growth_customer_loads`: each load, its source, who loaded it, when and how many customers. Counts only, no keys.
- `growth_settings.min_cohort`: the fewest customers a cohort needs for a rate (default 30, never below 5);
  `time_zone`: the IANA zone dates are read in (default UTC); `key_readers`: the people and bots, besides the owner and
  admins, who may see customer keys and churn dates in a figure's trace (JSON list of actors).
"""


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def up(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS growth_customers ("
        "source TEXT NOT NULL, customer_key TEXT NOT NULL, became TEXT NOT NULL, signed_up TEXT, channel TEXT, "
        "segment TEXT, plan TEXT, churned TEXT, revenue_month REAL, loaded_at TEXT NOT NULL, "
        "PRIMARY KEY (source, customer_key, became))"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS growth_customers_became ON growth_customers(became)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS growth_customer_loads ("
        "id TEXT PRIMARY KEY, source TEXT NOT NULL, customers INTEGER NOT NULL, removed INTEGER NOT NULL DEFAULT 0, "
        "loaded_by TEXT, loaded_at TEXT NOT NULL)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS growth_customer_loads_at ON growth_customer_loads(loaded_at)")
    settings = _columns(conn, "growth_settings")
    if settings:
        if "min_cohort" not in settings:
            conn.execute("ALTER TABLE growth_settings ADD COLUMN min_cohort INTEGER")
        if "time_zone" not in settings:
            conn.execute("ALTER TABLE growth_settings ADD COLUMN time_zone TEXT")
        if "key_readers" not in settings:
            conn.execute("ALTER TABLE growth_settings ADD COLUMN key_readers TEXT")
