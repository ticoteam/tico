"""kpis.goal_id and kpis.target go: a KPI reaches its goals through goal_kpis, which holds the target (backend/kpis.py).
hubdb's KPIS_SCHEMA made the goal_kpis link for each old row when it first ran, for a goal that existed then. This copies
any link it did not make (a goal gone at that time, or a row that still had only goal_id), and refuses a standalone KPI
with a target: that target has no goal to belong to, and no build writes one.
"""


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def up(conn):
    columns = _columns(conn, "kpis")
    if "goal_id" not in columns:
        return
    orphan = conn.execute("SELECT count(*) FROM kpis WHERE (goal_id IS NULL OR goal_id='') AND target IS NOT NULL"
                          ).fetchone()[0]
    if orphan:
        raise RuntimeError(f"{orphan} KPI(s) have a target and no goal: the target has nowhere to go. Link each KPI "
                           "to its goal (goal_kpis), or clear its target, then start the server again.")
    conn.execute("INSERT OR IGNORE INTO goal_kpis (id, goal_id, kpi_id, kind, target, created_at, created_by, "
                 "updated_at, updated_by) "
                 "SELECT 'goal_kpi:' || k.goal_id || ':' || k.id, k.goal_id, k.id, "
                 "CASE WHEN k.target IS NULL THEN 'none' ELSE 'improve' END, k.target, k.created_at, k.created_by, "
                 "k.created_at, k.created_by FROM kpis k WHERE k.goal_id <> '' AND NOT EXISTS ("
                 "SELECT 1 FROM goal_kpis g WHERE g.goal_id=k.goal_id AND g.kpi_id=k.id)")
    missing = conn.execute("SELECT count(*) FROM kpis k WHERE k.goal_id <> '' AND NOT EXISTS ("
                           "SELECT 1 FROM goal_kpis g WHERE g.goal_id=k.goal_id AND g.kpi_id=k.id)").fetchone()[0]
    if missing:
        raise RuntimeError(f"{missing} KPI(s) with a goal have no goal_kpis link after the copy: not dropping the column.")
    conn.execute("DROP INDEX IF EXISTS kpis_goal")
    conn.execute("ALTER TABLE kpis DROP COLUMN goal_id")
    if "target" in columns:
        conn.execute("ALTER TABLE kpis DROP COLUMN target")
