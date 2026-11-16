"""Keep-warm is gone: its setting, its table, and what its turns left that would still count.

A keep-warm turn was a one-line turn Tico sent an idle task session to hold its prompt cache. It went: few were
followed by real work within the hour. What it left:

- A keep-warm job still queued (its keeper notice's `refs.wake` is `keep_warm`) is canceled: nothing would start it on
  purpose any more, and run as an ordinary turn it would answer "ok" in the task's room. One already leased or running
  finishes, and execution.complete posts nothing for it.
- Its finished runs (attempts with purpose "keep warm") stay as history, but no longer on their task: their task_id,
  message_id and job_id are cleared. They were never runs on the task, and the readers that left them out (the
  never-stuck ladder's run count, the last run on a task, the checks a run already saw, a result's run) now count every
  run linked to it. Their replies already left every timeline (20261116124618_keep_warm_replies_hidden, which ran
  first and found them through job_id).
- `stage_keep_warm` is dropped, and so is `pipelines.keep_warm_hours`.

Keep-warm runs are read from the attempts_created index (none is older than the setting, migration
20261113235427_keep_warm_hours). Running it again changes nothing.
"""

KEEP_WARM = "keep warm"  # the purpose its runs carried
SHIPPED = "2026-11-13"  # migration 20261113235427_keep_warm_hours: no keep-warm run is older


def _table(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def up(conn):
    # A new file has no runs: its attempts table's triggers name tables the module code makes after the migrations.
    if all(_table(conn, name) for name in ("attempts", "jobs", "messages")):
        conn.execute(
            "UPDATE jobs SET state = 'canceled' WHERE state = 'queued' AND message_id IN (SELECT m.id FROM jobs j "
            "JOIN messages m ON m.id = j.message_id WHERE j.state = 'queued' AND m.sender_id = 'keeper' "
            "AND json_valid(m.refs) AND json_extract(m.refs, '$.wake') = 'keep_warm')"
        )
        conn.execute(
            "UPDATE attempts SET task_id = NULL, message_id = NULL, job_id = NULL WHERE created_at >= ? "
            "AND purpose = ? AND coalesce(state, '') NOT IN ('leased', 'running') "
            "AND (task_id IS NOT NULL OR message_id IS NOT NULL OR job_id IS NOT NULL)",
            (SHIPPED, KEEP_WARM),
        )
    conn.execute("DROP TABLE IF EXISTS stage_keep_warm")
    if not _table(conn, "pipelines"):
        return
    if "keep_warm_hours" not in {row[1] for row in conn.execute("PRAGMA table_info(pipelines)")}:
        return
    # A drop re-checks every trigger, and on a new file a trigger can name a table the module code makes after the
    # migrations. So the triggers step aside and go back as they were (as 20261110131902_drop_task_alert_columns).
    triggers = conn.execute("SELECT name, sql FROM sqlite_master WHERE type='trigger' AND sql IS NOT NULL").fetchall()
    for name, _sql in triggers:
        conn.execute(f'DROP TRIGGER IF EXISTS "{name}"')
    conn.execute("ALTER TABLE pipelines DROP COLUMN keep_warm_hours")
    for _name, sql in triggers:
        conn.execute(sql)
