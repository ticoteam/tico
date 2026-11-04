"""Check health replaces the Health Monitor bot: an existing one is archived, and its open tasks go to BotOps.

The Health Monitor was a built-in bot that Tico woke for each new finding. Check health, the Maintenance run's plain-code
step (backend/maintenance_steps/check_health.py), files the same findings itself, one BotOps task per problem, so the bot
has no job left. This archives it the way Settings > Bots archives a bot (backend/settings_admin.py `archive_bot`), with
nothing deleted: its state becomes archived, its computer assignment and its queued turns end, and the open tasks it owns
move to BotOps. With no active BotOps bot those tasks are closed as superseded, each with an event that says why.

A bot that is already archived is skipped, so running this again changes nothing. Its `health-scan` watcher belonged to its
template, which is gone; its watch rows stay as history.
"""

import uuid
from datetime import datetime, timezone

SLUG = "health-monitor"
ACTIVE = ("open", "doing", "waiting", "review", "ready")     # H.ACTIVE_STATUSES: still on the board
NOTE = "Health Monitor retired: Check health (Maintenance) files findings itself."


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _event(conn, task_id, when, field, old, new):
    conn.execute("INSERT INTO task_events(id,task_id,created_at,actor,field,old,new,note) VALUES(?,?,?,?,?,?,?,?)",
                 (uuid.uuid4().hex, task_id, when, "keeper", field, old, new, NOTE))


def up(conn):
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "bots" not in tables:
        return
    row = conn.execute("SELECT state FROM bots WHERE id=?", (SLUG,)).fetchone()
    if row is None or row[0] == "archived":
        return                                              # no such bot here, or already archived: nothing to do
    when = _now()
    botops = conn.execute("SELECT 1 FROM bots WHERE id='botops' AND state='active'").fetchone() is not None
    conn.execute("UPDATE bots SET state='archived' WHERE id=?", (SLUG,))
    if "assignments" in tables:
        conn.execute("DELETE FROM assignments WHERE bot=?", (SLUG,))
    if "jobs" in tables:
        conn.execute("UPDATE jobs SET state='canceled' WHERE bot_id=? AND state='queued'", (SLUG,))
    if "tasks" not in tables:
        return
    # A task's owner is stored as the bot's actor (bot:<id>); the bare slug is read too, for rows written before that.
    owners = (f"bot:{SLUG}", SLUG)
    marks = ",".join("?" * len(ACTIVE))
    open_tasks = conn.execute(f"SELECT id,status FROM tasks WHERE owner_id IN (?,?) AND status IN ({marks}) ORDER BY id",
                              (*owners, *ACTIVE)).fetchall()
    for task_id, status in open_tasks:
        if botops:
            conn.execute("UPDATE tasks SET owner_id=?, updated_at=? WHERE id=?", ("bot:botops", when, task_id))
            _event(conn, task_id, when, "owner_id", f"bot:{SLUG}", "bot:botops")
        else:
            conn.execute("UPDATE tasks SET status='closed', closed_at=?, closed_by_id='keeper', updated_at=?, note=? "
                         "WHERE id=?", (when, when, "Superseded: " + NOTE, task_id))
            _event(conn, task_id, when, "status", status, "closed")
