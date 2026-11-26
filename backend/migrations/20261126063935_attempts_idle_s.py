"""attempts.idle_s: how long a resumed run's session sat idle, in seconds from its bot's latest settled run on the same
task to the run's start (backend/run_triggers.py IDLE_SQL). Usage counts a resumed run idle over an hour as a cold
start. Resumed runs from the last 35 days get it from the runs already recorded; older ones keep NULL. Running it
again changes nothing."""

# run_triggers.IDLE_SQL word for word: a migration stands alone, so it does not import the module that may change.
IDLE_SQL = (
    "(SELECT CAST(round((julianday(attempts.created_at)-julianday(max(p.settled_at)))*86400) AS INTEGER) "
    "FROM attempts p WHERE p.task_id=attempts.task_id AND p.bot_id=attempts.bot_id AND p.id<>attempts.id "
    "AND p.settled_at<=attempts.created_at)"
)
BACKFILL_DAYS = 35


def up(conn):
    if "idle_s" not in {row[1] for row in conn.execute("PRAGMA table_info(attempts)")}:
        conn.execute("ALTER TABLE attempts ADD COLUMN idle_s INTEGER")
    # Timestamps are stored as text (YYYY-MM-DDTHH:MM:SS.ffffffZ) and compare as text; the bound takes the same form.
    conn.execute(
        f"UPDATE attempts SET idle_s={IDLE_SQL} WHERE session_kind='resumed' AND idle_s IS NULL "
        "AND task_id IS NOT NULL AND created_at>=strftime('%Y-%m-%dT%H:%M:%S', 'now', ?)",
        (f"-{BACKFILL_DAYS} days",),
    )
