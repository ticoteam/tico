"""Check health replaces the Health Monitor bot: an existing one is archived, its open tasks go to BotOps, and the open
production-check alerts go to BotOps too. Nothing is deleted, and running it again changes nothing.

The saved health_scan switch is copied to check_health again: System routines remained the person's control after 0090
seeded the step. Its read-only Automations mirror is soft-deleted; its runs stay as history.

The Health Monitor was a built-in bot that Tico woke for each new finding. Check health, the Maintenance run's plain-code
step (backend/maintenance_steps/check_health.py), files the same findings itself, one BotOps task per problem, so the bot
has no job left. The bot is archived the way Settings > Bots archives one (backend/settings_admin.py `archive_bot`): its
state and declared status become archived, its computer assignment and queued turns end, its schedules are disabled and
deleted (soft), and its bot_config revision is bumped.

Its open tasks:
  - a task a finding was filed on (watcher_items, key `hm:<finding key>`, whose finding is in health_findings) moves to
    BotOps, and the step's registry entry for that finding's signature (`health-task:<signature>`) is seeded with it, so
    the step adopts the task on its first run rather than opening a second one for the same problem. One task per
    signature; any other task for it is closed as superseded;
  - any other open task is closed as superseded, with an event that says why.

The invariant sweep used to open one alert task per firing production check, for its owner or for BotOps. Those open tasks
move to BotOps and are registered the same way, as `inv:<check id>`, and `invariant_state.task_id` is cleared: the sweep
writes it no more. A check that no longer fires is registered too, so the step clears its task on its first run.

With no active BotOps bot the moved tasks are closed as superseded instead, each with an event that says why.

Each task change writes a history event (history_events, kind = the field changed) as the keeper. A bot that is already archived
is skipped, and the invariant pass only touches tasks that still have an id in invariant_state, so a second run finds
nothing to do. The Health Monitor's `health-scan` watcher belonged to its template, which is gone; its watch rows stay as
history.

health_findings gets `pending` (the confirmation run of a noisy check, backend/maintenance_steps/check_health.py), and
health_reports gets `reported_at` (the last report of a Tico bug, which its 24-hour throttle reads). Each is added to a file
that has the table without the column. A new file has both from maintenance_health.SCHEMA.

The signature below is backend/maintenance_health.py `signature`, copied as it is: the registry key depends on it forever.
"""

import hashlib
import json
import uuid
from datetime import datetime, timezone

SLUG = "health-monitor"
BOTOPS = "botops"
KEEPER = "keeper"
ACTIVE = ("open", "doing", "waiting", "review", "ready")     # H.ACTIVE_STATUSES: still on the board
NOTE = "Health Monitor retired: Check health (Maintenance) files findings itself."
REGISTRY = "health-task:"                                    # check_health.REGISTRY_PREFIX


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _signature(check_id, subject=""):
    return "hm-" + hashlib.sha256((check_id + "\n" + subject).encode()).hexdigest()[:12]


def up(conn):
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    _columns(conn, tables)
    when = _now()
    _health_switch(conn, tables, when)
    _retire_health_scan(conn, tables, when)
    if "bots" not in tables:
        return
    botops = conn.execute("SELECT 1 FROM bots WHERE id=? AND state='active'", (BOTOPS,)).fetchone() is not None
    _retire_health_monitor(conn, tables, botops, when)
    _invariant_alerts(conn, tables, botops, when)
    _recount(conn, tables, (SLUG, BOTOPS), when)


def _health_switch(conn, tables, when):
    if not {"registry_metadata", "maintenance_steps"} <= tables:
        return
    row = conn.execute("SELECT value FROM registry_metadata WHERE key='system_routines'").fetchone()
    try:
        saved = json.loads(row[0]) if row else {}
    except (TypeError, ValueError):
        return
    entry = saved.get("health_scan") if isinstance(saved, dict) else None
    on = entry.get("on") if isinstance(entry, dict) else None
    if isinstance(on, bool):
        conn.execute("UPDATE maintenance_steps SET enabled=?, updated_at=?, updated_by_id='migration' "
                     "WHERE key='check_health' AND enabled<>?", (int(on), when, int(on)))


def _retire_health_scan(conn, tables, when):
    if "automations" in tables:
        conn.execute("UPDATE automations SET enabled=0, deleted_at=?, updated_at=? WHERE deleted_at IS NULL AND builtin=1 "
                     "AND json_extract(flow,'$.readonly')=1 "
                     "AND json_extract(flow,'$.trigger.name')='tico.builtin.job.health_scan'", (when, when))
    if "automations" in tables and "automation_runs" in tables:
        # Its unfinished runs are skipped, as a delete skips them (automations.remove); a mirror holds nothing else.
        conn.execute("UPDATE automation_runs SET status='skipped', finished_at=? "
                     "WHERE status IN ('queued','waiting','running') AND automation_id IN (SELECT id FROM automations "
                     "WHERE builtin=1 AND json_extract(flow,'$.readonly')=1 "
                     "AND json_extract(flow,'$.trigger.name')='tico.builtin.job.health_scan')", (when,))


def _columns(conn, tables):
    """health_findings.pending: 1 on a noisy check's finding that waits for its confirmation run, 0 once it is filed or
    cleared. health_reports.reported_at: when a Tico bug was last reported (a ticket or a comment), '' before. A file with
    the table and no column gets it; a file with no table yet gets the table, with its columns, from maintenance_health.SCHEMA,
    so this skips it. Running it again finds the columns and changes nothing."""
    _add_column(conn, tables, "health_findings", "pending", "INTEGER NOT NULL DEFAULT 0")
    _add_column(conn, tables, "health_reports", "reported_at", "TEXT NOT NULL DEFAULT ''")


def _add_column(conn, tables, table, column, definition):
    if table not in tables:
        return
    if column not in {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _retire_health_monitor(conn, tables, botops, when):
    row = conn.execute("SELECT state FROM bots WHERE id=?", (SLUG,)).fetchone()
    if row is None or row[0] == "archived":
        return                                              # no such bot here, or already archived: nothing to do
    conn.execute("UPDATE bots SET state='archived' WHERE id=?", (SLUG,))
    if "bot_config" in tables:
        conn.execute("UPDATE bot_config SET revision=revision+1, definition_updated_at=?, definition_updated_by=? WHERE bot=?",
                     (when, KEEPER, SLUG))
    if "bot_settings" in tables:
        # The declared status is a key of the declaration; the settings columns do not hold it, so it lives in `extra`.
        conn.execute("UPDATE bot_settings SET extra=json_set(coalesce(extra,'{}'),'$.status','archived'), updated_at=? "
                     "WHERE bot_id=?", (when, SLUG))
    if "assignments" in tables:
        conn.execute("DELETE FROM assignments WHERE bot=?", (SLUG,))
    if "jobs" in tables:
        conn.execute("UPDATE jobs SET state='canceled' WHERE bot_id=? AND state='queued'", (SLUG,))
    if "schedules" in tables:
        if "schedule_config" in tables:
            for (schedule_id,) in conn.execute("SELECT id FROM schedules WHERE bot=? AND deleted_at IS NULL",
                                               (SLUG,)).fetchall():
                conn.execute("INSERT INTO schedule_config(schedule_id,enabled) VALUES(?,0) "
                             "ON CONFLICT(schedule_id) DO UPDATE SET enabled=0", (schedule_id,))
        conn.execute("UPDATE schedules SET deleted_at=COALESCE(deleted_at,?), updated_at=? WHERE bot=?", (when, when, SLUG))
    if "tasks" not in tables:
        return
    owners = (f"bot:{SLUG}", SLUG)     # a task's owner is stored as the bot's actor; the bare slug is read too, for old rows
    marks = ",".join("?" * len(ACTIVE))
    tasks = conn.execute(f"SELECT id,owner_id,status FROM tasks WHERE owner_id IN (?,?) AND status IN ({marks}) ORDER BY id",
                         (*owners, *ACTIVE)).fetchall()
    linked = _finding_tasks(conn, tables)
    registered = set()                 # signatures already given their task
    for task_id, owner, status in tasks:
        signature = linked.get(task_id)
        if botops and signature and signature not in registered and _has(tables, "registry_metadata"):
            registered.add(signature)
            _to_botops(conn, task_id, owner, when)
            keys, count = conn.execute("SELECT count(*), coalesce(sum(occurrences),0) FROM health_findings "
                                       "WHERE signature=? AND present=1", (signature,)).fetchone()
            _register(conn, signature, task_id, keys, count, when)
        else:
            _supersede(conn, task_id, status, when, "its bot is retired and the finding is Check health's now")


def _finding_tasks(conn, tables):
    """{task id: signature} for each Health Monitor task a finding was filed on: watcher_items key `hm:<finding key>`
    (the bot's watcher, `hm:` and the key), joined to the finding for its signature. The finding may be cleared already."""
    if not _has(tables, "watcher_items") or not _has(tables, "health_findings"):
        return {}
    out = {}
    rows = conn.execute("SELECT w.task_id, f.signature FROM watcher_items w "
                        "JOIN health_findings f ON f.key = substr(w.key, 4) "
                        "WHERE w.bot=? AND w.key LIKE 'hm:%' ORDER BY w.task_id, f.signature", (SLUG,)).fetchall()
    for task_id, signature in rows:
        out.setdefault(task_id, signature)
    return out


def _invariant_alerts(conn, tables, botops, when):
    if "invariant_state" not in tables or "tasks" not in tables:
        return
    rows = conn.execute("SELECT check_id, task_id, count FROM invariant_state WHERE task_id IS NOT NULL").fetchall()
    for check_id, task_id, count in rows:
        task = conn.execute("SELECT owner_id, status FROM tasks WHERE id=?", (task_id,)).fetchone()
        if task is None or task[1] not in ACTIVE:
            continue
        owner, status = task
        if botops and _has(tables, "registry_metadata"):
            _to_botops(conn, task_id, owner, when)
            _register(conn, _signature("inv:" + check_id), task_id, 1, count, when)
        else:
            _supersede(conn, task_id, status, when, "the production check's alert; Check health files it now")
    conn.execute("UPDATE invariant_state SET task_id=NULL")


def _to_botops(conn, task_id, owner, when):
    if owner == f"bot:{BOTOPS}":
        return
    conn.execute("UPDATE tasks SET owner_id=?, updated_at=?, version=version+1 WHERE id=?", (f"bot:{BOTOPS}", when, task_id))
    _event(conn, task_id, when, "owner", owner, f"bot:{BOTOPS}", NOTE)


def _supersede(conn, task_id, status, when, why):
    note = "Superseded: " + why
    conn.execute("UPDATE tasks SET status='closed', closed_at=?, closed_by_id=?, updated_at=?, note=?, version=version+1 "
                 "WHERE id=?", (when, KEEPER, when, note, task_id))
    _event(conn, task_id, when, "status", status, "closed", note)


def _register(conn, signature, task_id, keys, count, when):
    """The step's registry entry for a signature, as check_health writes it: the task it adopts, with its place count and
    count, and its last comment today, so the next comment comes a day on."""
    value = {"task": task_id, "keys": keys, "count": count, "last_comment": when, "cleared": False, "approvals": {}}
    conn.execute("INSERT INTO registry_metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (REGISTRY + signature, json.dumps(value)))


def _recount(conn, tables, slugs, when):
    """open_tasks on each bot's status row, after the moves (needs_human is left: the next change to the bot's tasks sets it)."""
    if "bot_status" not in tables:
        return
    marks = ",".join("?" * len(ACTIVE))
    for slug in slugs:
        open_tasks = conn.execute(f"SELECT count(*) FROM tasks WHERE owner_id=? AND status IN ({marks})",
                                  (f"bot:{slug}", *ACTIVE)).fetchone()[0]
        conn.execute("UPDATE bot_status SET open_tasks=?, updated_at=? WHERE bot=?", (open_tasks, when, slug))


def _event(conn, task_id, when, kind, old, new, note):
    conn.execute("INSERT INTO history_events(id,subject_type,subject_id,actor_id,kind,old,new,note,action_id,via,data,created_at) "
                 "VALUES(?,?,?,?,?,?,?,?,NULL,NULL,NULL,?)",
                 (str(uuid.uuid4()), "task", task_id, KEEPER, kind, old, new, note, when))


def _has(tables, name):
    return name in tables
