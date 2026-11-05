"""Open "Still wanted?" questions to people are withdrawn: each is superseded, so no person is asked about an idle task and
the task is not held waiting on one.

The nudge that put them is gone (backend/still_wanted.py puts no new question, and the task roundup lists no idle task to a
bot), but a question put before that is still open on its person's Needs you and holds its task waiting. It is superseded
here, the way an answered or withdrawn ask is: it leaves the Needs you list, and the hold that reads open asks ends with it
(backend/task_holds.py reads only asks not superseded). Only keeper messages marked still_wanted are touched; a person's own
questions and a bot's asks are not. It writes no DDL, and running it again changes nothing.

Find stuck also takes the latest Task roundup switch at cutover: people could still change it after 0090 seeded the
step. The legacy setting is consumed so a replay cannot overwrite a later Maintenance choice. Its read-only Automations
mirrors are retired; the legacy Run now endpoint runs Maintenance's Find stuck step.

messages.superseded_at is added by the schema code after the migration files run (hubdb.migrate), so a new database has no
such column yet, and it has no question to withdraw either: the step is skipped there.
"""

import json

from backend import hubdb as H


def _cutover_roundup(conn):
    if H._has_table(conn, "registry_metadata"):
        row = conn.execute("SELECT value FROM registry_metadata WHERE key='system_routines'").fetchone()
        try:
            stored = json.loads(row[0]) if row else {}
        except (TypeError, ValueError):
            stored = {}
        if isinstance(stored, dict) and "task_roundup" in stored:
            saved = stored.pop("task_roundup")
            on = saved.get("on") if isinstance(saved, dict) else None
            if isinstance(on, bool):
                conn.execute("UPDATE maintenance_steps SET enabled=?, updated_at=?, updated_by_id='migration' "
                             "WHERE key='find_stuck'", (int(on), H.now()))
            conn.execute("UPDATE registry_metadata SET value=? WHERE key='system_routines'",
                         (json.dumps(stored, sort_keys=True),))
    if H._has_table(conn, "automations"):
        # Plain SQL, as 0091 retires health_scan's mirror: a migration never calls app code that may change later.
        when = H.now()
        conn.execute("UPDATE automations SET enabled=0, deleted_at=?, updated_at=? WHERE deleted_at IS NULL AND builtin=1 "
                     "AND json_extract(flow,'$.readonly')=1 "
                     "AND json_extract(flow,'$.trigger.name')='tico.builtin.job.task_roundup'", (when, when))
        if H._has_table(conn, "automation_runs"):
            # Its unfinished runs are skipped, as a delete skips them (automations.remove); a mirror holds nothing else.
            conn.execute("UPDATE automation_runs SET status='skipped', finished_at=? "
                         "WHERE status IN ('queued','waiting','running') AND automation_id IN (SELECT id FROM automations "
                         "WHERE builtin=1 AND json_extract(flow,'$.readonly')=1 "
                         "AND json_extract(flow,'$.trigger.name')='tico.builtin.job.task_roundup')", (when,))


def up(conn):
    _cutover_roundup(conn)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
    if "superseded_at" not in columns:
        return
    stamp = H.now()
    conn.execute("UPDATE messages SET superseded_at=? WHERE sender_id='keeper' AND superseded_at IS NULL "
                 "AND answered_by_id IS NULL AND json_extract(refs,'$.still_wanted')=1", (stamp,))
