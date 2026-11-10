"""The stuck alarm (backend/stuck_alarm.py): one row per card an alarm has been raised for, open while `cleared_at` is NULL.

- stuck_alarms: `alarm_task_id` is the one task the alarm filed to the recipient (the factory's manager, else the
  Engineering Pipeline Manager); `cleared_at` is set when the card is no longer stuck, so a card that gets stuck again
  later raises a new alarm. The partial unique index keeps one open alarm per card, so a pass never files a second one
  while the condition holds.

Nothing is backfilled: the alarm ships switched off (TICO_STUCK_ALARM), and no row exists until it is turned on.
"""


def up(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS stuck_alarms ("
        "id TEXT PRIMARY KEY, task_id TEXT NOT NULL, factory_id TEXT, recipient TEXT NOT NULL, "
        "alarm_task_id TEXT, stuck_since TEXT NOT NULL, raised_at TEXT NOT NULL, cleared_at TEXT, clear_reason TEXT)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS stuck_alarms_open ON stuck_alarms(task_id) WHERE cleared_at IS NULL"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS stuck_alarms_task ON stuck_alarms(task_id, raised_at)")
