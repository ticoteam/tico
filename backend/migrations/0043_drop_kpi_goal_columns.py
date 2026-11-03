"""kpis.goal_id goes: a KPI reaches its goals through goal_kpis, which holds each goal's target (backend/kpis.py).

hubdb's GOALS and KPIS schema made the goal_kpis link for each old row when it first ran, for a goal that existed then.
This copies any link it did not make (a goal made or restored after that, or a row that still had only goal_id), with
the KPI's target as an improvement. A goal_id naming no goal links nothing: that KPI stands on its own, like one that
never had a goal.

kpis.target stays where it is. A KPI with a goal has its target on the link too; a standalone KPI has nowhere else to
keep one, so nothing is lost and nothing refuses to start. No build writes or reads goal_id any more.
"""


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def up(conn):
    columns = _columns(conn, "kpis")
    if "goal_id" not in columns:
        return
    target = "k.target" if "target" in columns else "NULL"
    linked = ("k.goal_id <> '' AND k.goal_id IN (SELECT id FROM goals) AND NOT EXISTS ("
              "SELECT 1 FROM goal_kpis g WHERE g.goal_id=k.goal_id AND g.kpi_id=k.id)")
    conn.execute("INSERT OR IGNORE INTO goal_kpis (id, goal_id, kpi_id, kind, target, created_at, created_by_id, "
                 "updated_at, updated_by_id) "
                 f"SELECT 'goal_kpi:' || k.goal_id || ':' || k.id, k.goal_id, k.id, "
                 f"CASE WHEN {target} IS NULL THEN 'none' ELSE 'improve' END, {target}, k.created_at, k.created_by_id, "
                 f"k.created_at, k.created_by_id FROM kpis k WHERE {linked}")
    missing = conn.execute(f"SELECT count(*) FROM kpis k WHERE {linked}").fetchone()[0]
    if missing:
        raise RuntimeError(f"{missing} KPI(s) with a goal have no goal_kpis link after the copy: not dropping the column.")
    conn.execute("DROP INDEX IF EXISTS kpis_goal")
    conn.execute("ALTER TABLE kpis DROP COLUMN goal_id")
