"""Pipeline fix Q: factories.output_stage_id, the stage whose entries count as a factory's done work (D56).

NULL means "not set": the overview then reads the factory's last pipeline's last done-kind stage. This migration fills
each factory that has none with that same stage, taking the factory's most recently created pipeline as its last one.
Every statement is guarded, so a second run changes nothing."""


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def up(conn):
    if not _columns(conn, "factories"):
        return
    if "output_stage_id" not in _columns(conn, "factories"):
        conn.execute("ALTER TABLE factories ADD COLUMN output_stage_id TEXT")
    if "factory_id" not in _columns(conn, "task_types") or "status" not in _columns(conn, "task_steps"):
        return
    conn.execute(
        "UPDATE factories SET output_stage_id = ("
        " SELECT s.id FROM task_steps s WHERE s.type_id = ("
        "  SELECT p.id FROM task_types p WHERE p.factory_id = factories.id ORDER BY p.created_at DESC, p.id DESC LIMIT 1)"
        "  AND s.status = 'done' ORDER BY s.position DESC, s.id DESC LIMIT 1)"
        " WHERE output_stage_id IS NULL"
    )
