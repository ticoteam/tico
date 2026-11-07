"""Pipeline F0 columns, and the backfills that fill them for the types and steps a database already has.

task_types.prefix: the Dev tickets type gets D and the Product roadmap type P, each to the first such type with no prefix
yet and only while no type holds that prefix. task_types.pipeline_owner_id (not owner_id, which tasks has: a join would be ambiguous): NULL for every existing type (a type made from now on
records its human). task_types.manager_every_minutes: 10 for every type.
task_steps.limit_minutes: the step's stage limit in minutes (the "Warn after" time). It is the flow's late_hours x 60
when the flow sets one, else 240 (4 hours) on a working ("doing") step of an engineering type, which is what
backend/step_age.py late_limit gives it today, else NULL.
tasks.work_revision: 0 for every task.

task_types.role, task_types.manager and task_steps.flow are not added here. Module code adds them at boot, and their
absence is read: roadmap_flow.migrate backfills the roles only while task_types.role is missing, and step_flow reads
task_steps.flow to know whether flows exist. Adding them early would change that.

Every backfill fills only the rows it has not filled yet, so a second run changes nothing. Applied migrations are recorded
in schema_migrations and not run again. The Dev tickets and Product roadmap types are made by module code after the
migrations, so on a fresh database these backfills find no such type and fill nothing."""

import json

ENGINEERING = "engineering"
WORKING = "doing"
LATE_HOURS = 4


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _add(conn, table, column, definition):
    columns = _columns(conn, table)
    if columns and column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _late_minutes(raw):
    """The flow's late_hours in whole minutes, or None when the flow sets none (or it is not a positive number)."""
    try:
        flow = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return None
    value = flow.get("late_hours") if isinstance(flow, dict) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    minutes = round(value * 60)
    return minutes if minutes > 0 else None


def _prefixes(types):
    """(prefix, the type it goes to) in order: the Dev tickets type by its name, the Product roadmap by its role."""
    found = [("D", "name = 'Dev tickets' COLLATE NOCASE")]
    if "role" in types:
        found.append(("P", "role = 'product-roadmap'"))
    return found


def up(conn):
    types = _columns(conn, "task_types")
    if types:
        _add(conn, "task_types", "prefix", "TEXT")
        _add(conn, "task_types", "pipeline_owner_id", "TEXT")
        _add(conn, "task_types", "manager_every_minutes", "INTEGER NOT NULL DEFAULT 10")
        for prefix, where in _prefixes(types):
            conn.execute(
                f"UPDATE task_types SET prefix = ? WHERE id = (SELECT id FROM task_types WHERE {where} "
                "AND prefix IS NULL ORDER BY created_at, id LIMIT 1) "
                "AND NOT EXISTS (SELECT 1 FROM task_types WHERE prefix = ?)",
                (prefix, prefix),
            )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS task_types_prefix ON task_types(prefix) WHERE prefix IS NOT NULL"
        )
    if _columns(conn, "task_steps"):
        _add(conn, "task_steps", "limit_minutes", "INTEGER")
        flow = "s.flow" if "flow" in _columns(conn, "task_steps") else "NULL"
        role = "t.role" if "role" in types else "NULL"
        steps = conn.execute(
            f"SELECT s.id, s.status, {role}, {flow} FROM task_steps s JOIN task_types t ON t.id = s.type_id "
            "WHERE s.limit_minutes IS NULL"
        ).fetchall()
        for step_id, status, step_role, raw in steps:
            minutes = _late_minutes(raw)
            if minutes is None and step_role == ENGINEERING and status == WORKING:
                minutes = LATE_HOURS * 60
            if minutes is not None:
                conn.execute("UPDATE task_steps SET limit_minutes = ? WHERE id = ?", (minutes, step_id))
    _add(conn, "tasks", "work_revision", "INTEGER NOT NULL DEFAULT 0")
