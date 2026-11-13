"""Tico no longer sends "has room" notices (never_stuck, C10; spec "Spec has room: move it, don't send a notice"). This
takes the ones still waiting in bots' queues out, once: a queued notice keyed `room:...` is canceled. A notice that
took in an earlier wake about its task (`refs.parts`, task_wakes.add) stays queued with the earlier wake's words alone,
so that wake is still delivered. Delivered notices, and every other message and job, are left as they are. Running it
again changes nothing.
"""

import json

_ROOM = "room:"


def _is_room(key):
    return str(key or "").startswith(_ROOM)


def up(conn):
    have = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {"messages", "jobs"} <= have:
        return
    rows = conn.execute(
        "SELECT j.id, m.id, m.wake_key, m.refs FROM jobs j JOIN messages m ON m.id = j.message_id "
        "WHERE j.state='queued' AND ((m.wake_key >= 'room:' AND m.wake_key < 'room;') OR m.refs LIKE '%room:%')"
    ).fetchall()
    for job_id, message_id, wake_key, raw in rows:
        try:
            refs = json.loads(raw) if raw else {}
        except ValueError:
            refs = {}
        if not isinstance(refs, dict):
            refs = {}
        parts = refs.get("parts") if isinstance(refs.get("parts"), list) else None
        if not parts:
            if _is_room(wake_key):
                conn.execute("UPDATE jobs SET state='canceled' WHERE id=? AND state='queued'", (job_id,))
            continue
        rest = [part for part in parts if not (isinstance(part, dict) and _is_room(part.get("key")))]
        if len(rest) == len(parts):
            continue
        if not rest:
            conn.execute("UPDATE jobs SET state='canceled' WHERE id=? AND state='queued'", (job_id,))
            continue
        refs["parts"] = rest
        body = "\n\n".join(str(part.get("text") or "") for part in rest)
        conn.execute("UPDATE messages SET body=?, refs=? WHERE id=?", (body, json.dumps(refs), message_id))
