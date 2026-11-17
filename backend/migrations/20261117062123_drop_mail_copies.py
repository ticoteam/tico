"""The copies of mail and calendars that computers synced go: the server reads Gmail and Calendar live, and no computer
syncs, books or holds a key any more (mail spec v1.1, PR 5).

Dropped: `mail_fts`, `mail_messages` with its indexes, `mail_mailboxes`, `connector_snapshots`, and
`calendar_actions.runner_id`; and `connector_reports`, the per-computer health the deleted `connectors/health` route wrote
and only `google_rights.py` (deleted) read. An appointment a computer was asked to book and never claimed (`pending`) fails, so its
person books it again; one a computer claimed and never reported (`running` with a `runner_id`) is `unknown`, never
retried, since Google may have made the event. The health rows the computer side reported (`connector:mail`,
`connector:calendar`) go with it. Each step checks first, so a second run changes nothing.
"""

from datetime import datetime, timezone


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def up(conn):
    conn.execute("DROP TABLE IF EXISTS mail_fts;")
    conn.execute("DROP TABLE IF EXISTS mail_messages;")
    conn.execute("DROP TABLE IF EXISTS mail_mailboxes;")
    conn.execute("DROP TABLE IF EXISTS connector_snapshots;")
    conn.execute("DROP TABLE IF EXISTS connector_reports;")
    if "runner_id" in _columns(conn, "calendar_actions"):
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
        conn.execute(
            "UPDATE calendar_actions SET status='failed',error=?,updated_at=?,finished_at=? WHERE status='pending'",
            ("No computer books calendars any more; book it again", now, now),
        )
        conn.execute(
            "UPDATE calendar_actions SET status='unknown',error=?,updated_at=?,finished_at=? "
            "WHERE status='running' AND runner_id IS NOT NULL",
            ("A computer did not report back; check the calendar before booking again", now, now),
        )
        conn.execute("ALTER TABLE calendar_actions DROP COLUMN runner_id")
    if _columns(conn, "service_health"):
        conn.execute("DELETE FROM service_health WHERE service IN ('connector:mail','connector:calendar')")
