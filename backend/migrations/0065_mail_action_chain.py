"""A mail action's place in its step's chain (backend/automation_actions.py `_queue_mail`, backend/automations.py `claim`).

`step` is the step that queued the action, and `after` the id of the action that step queued before it on the same
message. A chain (Unsubscribe, then Archive) is claimed in order: an action waits until its `after` is done. Existing
rows keep NULL in both, which is no chain at all. On a new file the table does not exist yet: automations.SCHEMA makes it
with both columns.
"""


def _columns(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def up(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='automation_mail_actions'").fetchone():
        return
    have = _columns(conn, "automation_mail_actions")
    if "step" not in have:
        conn.execute("ALTER TABLE automation_mail_actions ADD COLUMN step TEXT")
    if "after" not in have:
        conn.execute('ALTER TABLE automation_mail_actions ADD COLUMN "after" TEXT')
