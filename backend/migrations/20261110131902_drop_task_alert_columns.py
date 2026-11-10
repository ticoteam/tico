"""Personal alerts no longer page anyone about a task (docs/pipelines-v3.md): an overdue or stuck task reaches its
person in Needs you. The preferences for those pages (urgent_needs, bot_stuck and bot_stuck's stall_after) were kept
at 0 since the cut-over; they go."""

DROPPED = ("urgent_needs", "bot_stuck", "stall_after")


def up(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='alert_preferences'").fetchone():
        return
    have = {row[1] for row in conn.execute("PRAGMA table_info(alert_preferences)")}
    gone = [column for column in DROPPED if column in have]
    if not gone:
        return
    # A drop re-checks every trigger, and on a new file a trigger can name a table the module code makes after the
    # migrations (presences_meeting_delete names task_roles). So the triggers step aside and go back as they were.
    triggers = conn.execute("SELECT name, sql FROM sqlite_master WHERE type='trigger' AND sql IS NOT NULL").fetchall()
    for name, _sql in triggers:
        conn.execute(f'DROP TRIGGER IF EXISTS "{name}"')
    for column in gone:
        conn.execute(f"ALTER TABLE alert_preferences DROP COLUMN {column}")
    for _name, sql in triggers:
        conn.execute(sql)
