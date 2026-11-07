"""Pipeline fix Q: factories.output_stage_id, the stage whose entries count as a factory's done work (D56).

NULL means "not set": the overview then reads the factory's last pipeline's completion stage (pipeline_setup.completion_stage:
its last done stage, else its last closed one). This migration fills each factory that has none with that same stage. The
factory's last pipeline is in flow order: one that no live link leaves to another pipeline of the same factory (a sink).
Among sinks, or when every pipeline has a link out, the most recently created one is taken. Every statement is guarded, so
a second run changes nothing."""

from backend.pipeline_setup import completion_stage


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def up(conn):
    if not _columns(conn, "factories"):
        return
    if "output_stage_id" not in _columns(conn, "factories"):
        conn.execute("ALTER TABLE factories ADD COLUMN output_stage_id TEXT")
    if "factory_id" not in _columns(conn, "task_types") or "status" not in _columns(conn, "task_steps"):
        return
    # A sink sorts first (1 before 0): no live link leaves it for another pipeline of the factory.
    sink = (
        "(NOT EXISTS (SELECT 1 FROM pipeline_links l JOIN task_types q ON q.id = l.to_pipeline_id "
        "WHERE l.from_pipeline_id = p.id AND l.retired_at IS NULL AND q.factory_id = p.factory_id AND q.id <> p.id))"
    )
    last = (
        "(SELECT p.id FROM task_types p WHERE p.factory_id = factories.id "
        f"ORDER BY {sink} DESC, p.created_at DESC, p.id DESC LIMIT 1)"
    )
    unset = conn.execute(f"SELECT id, {last} FROM factories WHERE output_stage_id IS NULL").fetchall()
    for factory_id, pipeline_id in unset:
        stage = completion_stage(conn, pipeline_id) if pipeline_id else None
        if stage:
            conn.execute(
                "UPDATE factories SET output_stage_id = ? WHERE id = ? AND output_stage_id IS NULL", (stage, factory_id)
            )
