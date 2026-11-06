"""Step 6d: the ask indexes and triggers go. Asks are question tasks now (backend/question_tasks.py), and no message is
written as an ask or an answer (hubdb.OLD_MESSAGE_KINDS are refused). The old rows stay as history; their indexes and
the triggers that watched them for the Needs you list are dead weight on every message write.
"""

INDEXES = (
    "messages_open_asks",  # REPLY_ANSWERS_ASKS (hubdb's numbered step), made before this file runs
    "messages_open_asks_to",
    "messages_pending_conversation",
    "messages_open_ask_to",
    "messages_superseded_asks_to",
)
TRIGGERS = ("changes_task_asks_insert", "changes_task_asks_update")


def up(conn):
    for name in INDEXES:
        conn.execute(f"DROP INDEX IF EXISTS {name}")
    for name in TRIGGERS:
        conn.execute(f"DROP TRIGGER IF EXISTS {name}")
