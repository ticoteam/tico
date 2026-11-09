"""The stuck rule (backend/stuck_cards.py): a card on a pipeline that stops moving reaches its manager bot, then a person.

- task_types.stuck_after_hours: hours with no progress before a card is stuck (NULL: stuck_cards.STUCK_AFTER_HOURS, 4).
- task_types.ask_person_after_hours: hours with no progress before the pipeline's owner gets one question about the card
  (NULL: stuck_cards.ASK_PERSON_AFTER_HOURS, 24).
- task_types.question_expiry_days: days a question to a person about the pipeline's work stays open before it expires
  (NULL: ask_deadlines.EXPIRE_DAYS, 7).
- task_holds.reason_changed_at: when a hold's reason last changed to a new one (task_holds.change). NULL on existing
  rows: their `created_at` stands for it.
- stuck_spells: one row per stuck spell of a card, open while `ended_at` is NULL. It records the manager's one wake and the
  person's one question, so a pass never repeats either.

Every step checks first, so a second run changes nothing. Nothing is backfilled: unset settings read as the defaults.
"""


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def up(conn):
    types = _columns(conn, "task_types")
    for column in ("stuck_after_hours", "ask_person_after_hours", "question_expiry_days"):
        if types and column not in types:
            conn.execute(f"ALTER TABLE task_types ADD COLUMN {column} INTEGER")
    holds = _columns(conn, "task_holds")
    if holds and "reason_changed_at" not in holds:
        conn.execute("ALTER TABLE task_holds ADD COLUMN reason_changed_at TEXT")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS stuck_spells ("
        "id TEXT PRIMARY KEY, task_id TEXT NOT NULL, pipeline_id TEXT NOT NULL, "
        "stuck_since TEXT NOT NULL, started_at TEXT NOT NULL, hold_reason TEXT, "
        "manager_id TEXT, manager_woken_at TEXT, "
        "person_id TEXT, person_asked_at TEXT, question_task_id TEXT, answer_relayed_at TEXT, "
        "ended_at TEXT, end_reason TEXT)"
    )
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS stuck_spells_open ON stuck_spells(task_id) WHERE ended_at IS NULL")
    conn.execute("CREATE INDEX IF NOT EXISTS stuck_spells_task ON stuck_spells(task_id, started_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS stuck_spells_question ON stuck_spells(question_task_id)")
