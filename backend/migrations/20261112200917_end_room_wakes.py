"""End the "next stage has room" wakes written before their key named their task (never_stuck.room_key). Their key began
`room:`, so the run each one started was taken to be about a task called "room": every start was refused, and the job
was leased again until it failed, jamming its bot's queue. Each such job still queued or leased is canceled, the way a
folded wake is (task_wakes.add), and a lease out on one ends now. The never-stuck check writes a correct wake on its next
pass while the stage still has room. Running it again finds nothing left to end."""

from datetime import datetime, timezone

# The old keys, read as a range on the wake-key index: every key that starts `room:`.
OLD = "SELECT id FROM messages WHERE wake_key >= 'room:' AND wake_key < 'room;'"


def up(conn):
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {"messages", "jobs", "attempts"} <= tables:
        return
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    conn.execute(
        "UPDATE attempts SET state='expired', finished_at=?, lease_until=? WHERE state='leased' "
        f"AND job_id IN (SELECT id FROM jobs WHERE state='leased' AND message_id IN ({OLD}))",
        (now, now),
    )
    conn.execute(f"UPDATE jobs SET state='canceled' WHERE state IN ('queued', 'leased') AND message_id IN ({OLD})")
