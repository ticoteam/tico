"""Follow-up times are set by a person or a bot, never by Tico.

A task's check_back_at (and check_back_note) is its follow-up time. A person or a bot sets one through the check-back route
or the bot's check-back tool, and each such set writes a check_back_explicit history row with the same time
(backend/task_check_backs.py set_time). Tico used to write one by itself: a default time on a bot's task, a deferral's due
date, a repair. Nobody asked for those, so this clears every follow-up time that no check_back_explicit row names. A time
that was set explicitly keeps its note.
"""


def up(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='tasks'").fetchone():
        return
    columns = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
    if "check_back_at" not in columns:
        return
    clear = "check_back_at=NULL" + (", check_back_note=NULL" if "check_back_note" in columns else "")
    explicit = "0"
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='history_events'").fetchone():
        explicit = ("EXISTS (SELECT 1 FROM history_events h WHERE h.subject_type='task' AND h.subject_id=tasks.id "
                    "AND h.kind='check_back_explicit' AND h.new=tasks.check_back_at)")
    conn.execute(f"UPDATE tasks SET {clear} WHERE check_back_at IS NOT NULL AND NOT {explicit}")
