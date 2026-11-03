"""kpis.goal_id goes: a KPI reaches its goals through goal_kpis, which holds each goal's target (backend/kpis.py).

hubdb's KPIS_SCHEMA made the goal_kpis link for each old row when it ran, and since then no build writes goal_id (a new
KPI stores ''). A KPI whose goal_id has no link now is one a person took off that goal (kpis.unlink deletes the link
and leaves goal_id), so nothing is copied back: that would undo their change. kpis.target stays, read by nothing.
"""


def up(conn):
    if "goal_id" not in {row[1] for row in conn.execute("PRAGMA table_info(kpis)")}:
        return
    conn.execute("DROP INDEX IF EXISTS kpis_goal")
    conn.execute("ALTER TABLE kpis DROP COLUMN goal_id")
