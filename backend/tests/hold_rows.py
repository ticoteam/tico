"""Tests set a task's holds as they once wrote its columns: `set_holds(c, tid, snoozed_until=..., blocked_kind=...)`.

A task's move to Blocked and its snooze are task_holds rows (backend/migrations/0074_task_holds.py), one open row of
each kind. A field of a kind opens that kind's hold (from `blocked_since`, else now) or changes the one open;
`snoozed_until=None` or `blocked_since=None` releases it.
"""
from backend import hubdb as H, task_holds


def set_holds(c, task_id, **fields):
    unknown = set(fields) - {f for names in task_holds.FIELDS.values() for f in names}
    if unknown:
        raise ValueError(f"no hold field {sorted(unknown)}")
    for kind, names in task_holds.FIELDS.items():
        given = {f: v for f, v in fields.items() if f in names}
        if not given:
            continue
        opener = next(iter(names)) if kind == "blocked" else "snoozed_until"
        if opener in given and given[opener] is None:
            task_holds.release(c, task_id, kind, H.now())
            continue
        columns = {names[f]: v for f, v in given.items() if names[f] != "created_at"}
        hold = task_holds.open_hold(c, task_id, kind)
        if hold is None or "blocked_since" in given:
            task_holds.put(c, task_id, kind, given.get("blocked_since") or (hold or {}).get("created_at") or H.now(),
                           **columns)
        else:
            task_holds.change(c, task_id, kind, **columns)
