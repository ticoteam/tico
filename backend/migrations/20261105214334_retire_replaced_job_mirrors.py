"""The read-only Automations mirrors of the system jobs the Maintenance run replaced (auto_dream, cleanup and
stall_triage) are soft-deleted, and their unfinished runs skipped, as 0096 retires health_scan's mirror. Until now the
Automations list hid them on the client (ui/app/automations-maintenance.js). Their runs stay as history, and running it
again changes nothing.

Plain SQL: a migration never calls app code that may change later."""

from datetime import datetime, timezone

NAMES = tuple("tico.builtin.job." + key for key in ("auto_dream", "cleanup", "stall_triage"))
MIRRORS = ("SELECT id FROM automations WHERE builtin=1 AND json_extract(flow,'$.readonly')=1 "
           "AND json_extract(flow,'$.trigger.name') IN (?,?,?)")


def up(conn):
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "automations" not in tables:
        return
    when = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    if "automation_runs" in tables:
        # Its unfinished runs are skipped, as a delete skips them (automations.remove); a mirror holds nothing else.
        conn.execute("UPDATE automation_runs SET status='skipped', finished_at=? "
                     f"WHERE status IN ('queued','waiting','running') AND automation_id IN ({MIRRORS})", (when, *NAMES))
    conn.execute(f"UPDATE automations SET enabled=0, deleted_at=?, updated_at=? WHERE deleted_at IS NULL AND id IN ({MIRRORS})",
                 (when, when, *NAMES))
