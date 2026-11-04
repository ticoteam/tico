"""Open "Still wanted?" questions to people are withdrawn: each is superseded, so no person is asked about an idle task.

Maintenance messages no person (backend/maintenance_steps/find_stuck.py), and backend/still_wanted.py puts no new question.
A question put before that is still open on its person's Needs you and holds its task waiting, so it is superseded here,
the way an answered or withdrawn ask is: it leaves the Needs you list, and the hold that reads open asks ends with it
(backend/task_holds.py reads only asks not superseded). Only keeper messages marked still_wanted are touched; a person's own
questions and a bot's asks are not. It writes no DDL, and running it again changes nothing.
"""

from datetime import datetime, timezone


def up(conn):
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    conn.execute("UPDATE messages SET superseded_at=? WHERE sender_id='keeper' AND superseded_at IS NULL "
                 "AND answered_by_id IS NULL AND json_extract(refs,'$.still_wanted')=1", (stamp,))
