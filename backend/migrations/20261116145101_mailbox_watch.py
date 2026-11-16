"""The server watches each mailbox's Gmail itself (backend/gmail_watch.py), so the watch state moves onto `mailboxes`,
and a finished automation run stops holding the email body it read.

- `mailboxes.history_id`: the Gmail history id the last check reached (NULL before the first check).
- `mailboxes.checked_at`: when Gmail was last read for it successfully.
- `mailboxes.check_error`: why the last check failed ('' text, NULL when it worked).
- `mailboxes.catch_up`: while a check lists the mail it has not heard yet (a first check, or one after Gmail no
  longer had the history id), JSON {history_id, after, id}: the history id to follow once it is done, and the last
  message heard (received time in epoch seconds, Gmail id). NULL otherwise.
- `mailboxes.watching_since_at`: where its message automations start hearing mail (backend/automations.py
  `mail_watch`), copied from `mail_mailboxes` where a computer's sync had set it, so a watched mailbox does not hear the
  last day of its mail a second time.
- `automation_runs_forget_body`: a run's saved state keeps the message's body, and the snippet cut from it, only while
  the run is unfinished. The run's facts (its steps, decisions, cost) stay; the existing finished runs lose them here
  too.
"""

COLUMNS = (
    ("history_id", "TEXT"),
    ("checked_at", "TEXT"),
    ("check_error", "TEXT"),
    ("catch_up", "TEXT"),
    ("watching_since_at", "TEXT"),
)
BODY = "'$.payload.message.body', '$.payload.message.snippet'"
# CASE, not AND: SQLite may evaluate both sides of an AND, and json_type raises on text that is not JSON.
HAS_BODY = (
    "CASE WHEN json_valid({state}) THEN coalesce(json_type({state}, '$.payload.message.body'), "
    "json_type({state}, '$.payload.message.snippet')) END IS NOT NULL"
)
TRIGGER = (
    "CREATE TRIGGER IF NOT EXISTS automation_runs_forget_body AFTER UPDATE ON automation_runs "
    f"WHEN NEW.finished_at IS NOT NULL AND {HAS_BODY.format(state='NEW.saved_state')} BEGIN "
    f"UPDATE automation_runs SET saved_state=json_remove(saved_state, {BODY}) WHERE id=NEW.id; END"
)


def _table(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def up(conn):
    if _table(conn, "mailboxes"):
        have = {row[1] for row in conn.execute("PRAGMA table_info(mailboxes)")}
        for name, kind in COLUMNS:
            if name not in have:
                conn.execute(f"ALTER TABLE mailboxes ADD COLUMN {name} {kind}")
        if _table(conn, "mail_mailboxes"):
            conn.execute(
                "UPDATE mailboxes SET watching_since_at=(SELECT m.watching_since_at FROM mail_mailboxes m "
                "WHERE m.address=mailboxes.address) WHERE watching_since_at IS NULL"
            )
    if _table(conn, "automation_runs"):
        conn.execute(TRIGGER)
        conn.execute(
            f"UPDATE automation_runs SET saved_state=json_remove(saved_state, {BODY}) "
            f"WHERE finished_at IS NOT NULL AND {HAS_BODY.format(state='saved_state')}"
        )
