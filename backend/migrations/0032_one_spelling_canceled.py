"""One spelling: `canceled`. Every stored value and audit event Tico writes itself moves from `cancelled`, and the seat's
in-flight `cancelling` to `canceling`; notes.cancelled_at and cancelled_by take the new spelling (wave 4 of
scripts/cleanup/renames.yaml). Values an outside system wrote stay as written (fin_feed_txns.provider_status is
Mercury's), and so does audit_log, which is append-only.

fin_cards.status has a CHECK naming the old spelling, so it maps while status_values rebuilds the table, which is why
foreign keys are off for this file.
"""
from backend import status_values
from backend.migrations import renames as schema_renames

FOREIGN_KEYS_OFF = True

COLUMNS = (
    ("assistant_actions", "status"),
    ("automation_mail_actions", "state"),
    ("bot_tool_requests", "state"),
    ("bot_transition_checkpoints", "state"),
    ("bot_transitions", "state"),
    ("credential_requests", "status"),
    ("fin_cards", "provider_status"),   # Tico's reading of Mercury's status (cards._mercury_status), compared with status
    ("model_logins", "state"),
    ("seats", "request_state"),
    ("service_jobs", "state"),
    ("slack_posts", "state"),
    ("task_reconnects", "ended"),
    ("worktree_waits", "state"),
)
EVENTS = {
    "assistant.action.cancelled": "assistant.action.canceled",
    "bot.tool_request_cancelled": "bot.tool_request_canceled",
    "bot.transition_cancelled": "bot.transition_canceled",
    "seat.request_cancelled": "seat.request_canceled",
}


def _has(conn, table, column):
    return any(row[1] == column for row in conn.execute(f'PRAGMA table_info("{table}")'))


def up(conn):
    schema_renames.apply(conn, waves=("4",))
    for table, column in COLUMNS:
        if _has(conn, table, column):
            conn.execute(f"UPDATE {table} SET {column}='canceled' WHERE {column}='cancelled'")
    if _has(conn, "seats", "request_state"):
        conn.execute("UPDATE seats SET request_state='canceling' WHERE request_state='cancelling'")
    if _has(conn, "events", "action"):
        for old, new in EVENTS.items():
            conn.execute("UPDATE events SET action=? WHERE action=?", (new, old))
    status_values.enforce(conn)
