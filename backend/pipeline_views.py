# pyright: strict
"""The pipeline views, read only: `pipelines` (a task type as a pipeline) and `pipeline_stages` (a step as a stage).

They are created at startup (store.initialize), after the schema code that adds the columns they name: task_types.role
and task_types.manager (hubdb, roadmap_flow), task_steps.flow (step_flow) and task_types.factory_id (the factories
migration). They are not in a migration: migrations run before that code, and SQLite checks a view only when it runs an
ALTER TABLE, where a view naming a column that is not there yet fails the statement. So a view is created only when every
column it names exists, and a start that cannot yet name them makes none. CREATE VIEW IF NOT EXISTS makes a later start a
no-op.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import sqlite3

VIEWS = (
    (
        "task_steps",
        "CREATE VIEW IF NOT EXISTS pipeline_stages AS\n"
        "SELECT id, type_id AS pipeline_id, name, position, status, limit_minutes, flow\n"
        "FROM task_steps",
        ("id", "type_id", "name", "position", "status", "limit_minutes", "flow"),
    ),
    (
        "task_types",
        "CREATE VIEW IF NOT EXISTS pipelines AS\n"
        "SELECT id, name, prefix, pipeline_owner_id AS owner_id, manager AS manager_id, manager_every_minutes, factory_id, role, numbered,\n"
        "       created_at, updated_at\n"
        "FROM task_types",
        (
            "id",
            "name",
            "prefix",
            "pipeline_owner_id",
            "manager",
            "manager_every_minutes",
            "factory_id",
            "role",
            "numbered",
            "created_at",
            "updated_at",
        ),
    ),
)


def ensure(c: "sqlite3.Connection") -> None:
    for table, sql, columns in VIEWS:
        have = {row[1] for row in c.execute(f"PRAGMA table_info({table})")}
        if set(columns) <= have:
            c.execute(sql)
