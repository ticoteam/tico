"""When a pipeline's manager was assigned: the clock the pipeline manager's watchdog judges missed passes from (F11).

- task_types.manager_since: the moment the current manager was set. Two triggers keep it, so every writer is covered: an
  insert with a manager, or an update that changes the manager, sets it to now; clearing the manager clears it.
- Existing managed pipelines are backfilled to the migration's time, so an upgrade does not judge them from older runs.
- first_start_seeds: one row per seed the hub still owes (today only `roadmap_types`), written when this migration adds
  `manager` before the hub's schema code does.
"""

from datetime import datetime, timezone

TRIGGERS = (
    "CREATE TRIGGER IF NOT EXISTS task_types_manager_since_insert AFTER INSERT ON task_types "
    "WHEN NEW.manager IS NOT NULL AND NEW.manager <> '' "
    "BEGIN UPDATE task_types SET manager_since = strftime('%Y-%m-%dT%H:%M:%f', 'now') || '000Z' WHERE id = NEW.id; END",
    "CREATE TRIGGER IF NOT EXISTS task_types_manager_since_update AFTER UPDATE OF manager ON task_types "
    "WHEN NEW.manager IS NOT OLD.manager "
    "BEGIN UPDATE task_types SET manager_since = CASE WHEN NEW.manager IS NULL OR NEW.manager = '' THEN NULL "
    "ELSE strftime('%Y-%m-%dT%H:%M:%f', 'now') || '000Z' END WHERE id = NEW.id; END",
)


def up(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(task_types)")}
    if not columns:
        return
    # `manager` is added by the hub's schema code, which can run after this migration on a new database: add it here
    # first (the same definition, so that code then finds it and skips), or the triggers below name a missing column.
    # The hub seeds its Product and Engineering pipelines when it adds `manager` itself (a first start, or a hub older
    # than managers). Adding the column here takes that signal away, so this leaves a note instead, kept until the hub
    # has seeded (hubdb.migrate reads and clears it): a start that stops in between seeds on the next one.
    conn.execute("CREATE TABLE IF NOT EXISTS first_start_seeds (name TEXT PRIMARY KEY)")
    if "manager" not in columns:
        conn.execute("ALTER TABLE task_types ADD COLUMN manager TEXT")
        conn.execute("INSERT OR IGNORE INTO first_start_seeds (name) VALUES ('roadmap_types')")
    if "manager_since" not in columns:
        conn.execute("ALTER TABLE task_types ADD COLUMN manager_since TEXT")
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    conn.execute(
        "UPDATE task_types SET manager_since=? WHERE manager IS NOT NULL AND manager<>'' AND manager_since IS NULL",
        (stamp,),
    )
    for statement in TRIGGERS:
        conn.execute(statement)
