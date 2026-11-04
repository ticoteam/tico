"""Open "Still wanted?" questions to people are withdrawn: each is superseded, so no person is asked about an idle task.

Maintenance messages no person (backend/maintenance_steps/find_stuck.py), and backend/still_wanted.py puts no new question.
A question put before that is still open on its person's Needs you and holds its task waiting, so it is superseded here,
the way an answered or withdrawn ask is: it leaves the Needs you list, and the hold that reads open asks ends with it
(backend/task_holds.py reads only asks not superseded). Only keeper messages marked still_wanted are touched; a person's own
questions and a bot's asks are not. It writes no DDL, and running it again changes nothing.

messages.superseded_at is added by the schema code after the migration files run (hubdb.migrate), so a new database has no
such column yet, and it has no question to withdraw either: the step is skipped there.
"""

from datetime import datetime, timezone


def up(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
    if "superseded_at" not in columns:
        return
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    conn.execute("UPDATE messages SET superseded_at=? WHERE sender_id='keeper' AND superseded_at IS NULL "
                 "AND answered_by_id IS NULL AND json_extract(refs,'$.still_wanted')=1", (stamp,))
