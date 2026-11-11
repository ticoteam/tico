"""What each system job does to tasks, shown beside it in Automations › System jobs. It never decides anything."""

JOBS = {
    "heartbeat": (
        "open",
        "Every beat: move a task on when everything it needs is met, wake whoever has its turn, end snoozes that are "
        "due, and hand a task that is Late or not moving to its pipeline's manager, then to a person.",
    ),
    "task_manager_report": (
        "open",
        "Task Manager: per department, list tasks that say Paused, held bots, runs waiting for their owner's review, "
        "and duplicate tasks.",
    ),
    "bookkeeper_check": (
        "open",
        "Finance Owner: read its Finance work queue; open one Books check task only when something is new.",
    ),
}
WAKES = {"heartbeat"}


def describe_job(row):
    if row.get("kind") == "task_rule":
        hours = " or ".join(str(t["hours"]) for t in row.get("triggers", []))
        return {
            **row,
            "task_trigger": "after",
            "task_effect": "Quiet",
            "task_action": f"Close a done task after {hours} hours untouched; skips tasks waiting on a person, snoozed, "
            "pinned, or with an open ask; at most 50 a pass.",
        }
    metadata = JOBS.get(row["key"])
    return {
        **row,
        "task_trigger": metadata[0] if metadata else None,
        "task_effect": "Wakes" if row["key"] in WAKES else "Quiet",
        "task_action": metadata[1] if metadata else None,
    }
