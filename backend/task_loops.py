"""A task whose status changes an unusual number of times is flagged as a likely loop, never blocked.

Moving a task back and forth between stages is normal. Twenty status changes in a day is not: it is usually two
automations (or a bot and a rule) undoing each other. Health names the task and who is moving it, and its owner and
requester get one notice a day while it lasts.
"""
from . import hubdb as H

LOOP_CHANGES = 20       # status changes ...
LOOP_HOURS = 24         # ... within this many hours flag a task
FLAG_KEY = "task-loop:"  # registry_metadata: when a task's people were last told


def looping(c, at=None, limit=20):
    """[{task_id, title, private, changes, movers: [(actor, count)]}], busiest first (at most `limit`, 20 unless a reader
    names fewer). Uses history_events_kind."""
    since = H.shift(at or H.now(), hours=-LOOP_HOURS)
    out = []
    for row in c.execute("SELECT subject_id AS task_id, count(*) AS n FROM history_events WHERE subject_type='task' "
                         "AND kind='status' AND created_at>=? GROUP BY subject_id HAVING n>=? ORDER BY n DESC LIMIT ?",
                         (since, LOOP_CHANGES, limit)).fetchall():
        task = H.task(c, row["task_id"])
        if not task:
            continue
        movers = [(r[0], r[1]) for r in c.execute(
            "SELECT actor_id, count(*) FROM history_events WHERE subject_type='task' AND subject_id=? "
            "AND kind='status' AND created_at>=? GROUP BY actor_id ORDER BY 2 DESC LIMIT 5", (row["task_id"], since))]
        out.append({"task_id": task["id"], "title": task["title"], "private": bool(H.task_private(c, task)),
                    "owner_id": task["owner_id"], "requester_id": task["requester_id"], "changes": row["n"], "movers": movers})
    return out


def who(actor):
    return "Tico's automation" if actor == H.KEEPER else H.actor_id(actor)


def describe(item):
    return (f"changed status {item['changes']} times in {LOOP_HOURS} h, moved by "
            + ", ".join(f"{who(a)} ({n})" for a, n in item["movers"]))


def flag(c, at=None):
    """Tell each looping task's owner and requester once per LOOP_HOURS. Quiet for bots: no run is started."""
    from .repositories import metadata, save_metadata
    now = at or H.now()
    told = []
    for item in looping(c, now):
        key = FLAG_KEY + item["task_id"]
        last = metadata(c, key).get("at", "")
        if last and last > H.shift(now, hours=-LOOP_HOURS):
            continue
        task = H.task(c, item["task_id"])
        body = (f"This task {describe(item)}. That is unusual and may be a bug: two automations or rules undoing "
                "each other. Nothing is blocked; check what keeps moving it.")
        for person in dict.fromkeys([task["owner_id"], task["requester_id"]]):
            if person and person != H.KEEPER:
                H._wake(c, task, person, body, quiet_bots=True, quiet=True)
        save_metadata(c, key, {"at": now})
        told.append(item["task_id"])
    return told
