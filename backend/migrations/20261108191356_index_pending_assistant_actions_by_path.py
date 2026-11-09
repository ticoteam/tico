"""Find the Confirm cards still waiting on a task by their path, with one seek.

A pull request's finished checks wake a bot whose card waits on them, but not one whose card waits on a person: a
Confirm card on the task (`/api/v2/tasks/<id>...`, or a bulk card) is such a wait (rules._held_by_person). The table's
only index is (owner, status), so that read scanned every card ever proposed. Pending cards are few, so the index is
partial.

assistant_actions normally starts in Store.initialize, after migrations (assistant.ensure_schema), so a new database
gets it here with the shape that code makes; ensure_schema then finds it and its columns in place.
"""


def up(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS assistant_actions ("
        "id TEXT PRIMARY KEY, owner TEXT NOT NULL, conversation_id TEXT, summary TEXT NOT NULL, "
        "method TEXT NOT NULL, path TEXT NOT NULL, body TEXT NOT NULL DEFAULT '{}', "
        "status TEXT NOT NULL, proposed_via TEXT, created_at TEXT NOT NULL, decided_at TEXT, result TEXT, "
        "confirm_hash TEXT, running_since TEXT, description TEXT, diff TEXT, proposer TEXT, ask TEXT)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS assistant_actions_pending_path ON assistant_actions(path) WHERE status='pending'"
    )
