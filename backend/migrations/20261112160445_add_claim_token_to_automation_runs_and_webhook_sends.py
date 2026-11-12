"""Which claim owns a running automation run or a webhook send in flight: a fresh token stamped on each claim and
cleared when the run or send leaves it (backend/automations.py Hub.claim, backend/webhook_delivery.py claim). A worker
that outlived its stale window holds the old token, so its late answer can no longer settle the claim that replaced it.

Both tables are made by module code after the migrations (the CREATEs there have the column), so a new database has
no table here yet and nothing to add. A row in flight when this runs has no token: it is reclaimed by its stale window
as before, and its old worker's answer is then refused like any other lapsed claim's.
"""


def up(conn):
    for table in ("automation_runs", "webhook_sends"):
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if columns and "claim_token" not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN claim_token TEXT")
